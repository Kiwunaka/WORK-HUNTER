from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
import pytest

from tests.test_hh_autopilot_executor import _make_case, _settings
from work_hunter.hh_autopilot.reconcile import (
    HHApplicationReconciler,
    HHRecoverySweep,
    NegotiationSnapshot,
)
from work_hunter.hh_autopilot.types import (
    AutopilotState,
    DeliveryCertainty,
    DispatchOutcome,
    SearchPage,
)
from work_hunter.hh_transport.errors import HHAuthError
from work_hunter.sources.hh import HHApplyClient


class HistoryReader:
    def __init__(self, snapshots: list[NegotiationSnapshot] | None = None) -> None:
        self.snapshots = snapshots or []
        self.error: BaseException | None = None
        self.calls: list[str] = []

    def negotiation_snapshots(
        self, account_id: str
    ) -> tuple[NegotiationSnapshot, ...]:
        self.calls.append(account_id)
        if self.error is not None:
            raise self.error
        return tuple(self.snapshots)


def _reconcile_settings(*, checks: int = 1, max_attempts: int = 4):
    base = _settings(max_attempts=max_attempts)
    return replace(
        base,
        retry={
            **base.retry,
            "reconciliation_checks": checks,
            "reconciliation_delay_seconds": 30,
        },
    )


def _held_case(tmp_path, *, code: str = "post_dispatch_network_error", checks=1, max_attempts=4):
    settings = _reconcile_settings(checks=checks, max_attempts=max_attempts)
    case = _make_case(tmp_path, settings=settings)
    case.transport.outcome = DispatchOutcome(
        code=code,
        certainty=(
            DeliveryCertainty.DEFINITE_RESPONSE
            if code == "duplicate"
            else DeliveryCertainty.POSSIBLY_SENT
        ),
    )
    result = case.executor.execute(
        case.item.id,
        case.authorization,
        case.lease,
        now=case.now,
    )
    assert result.state is AutopilotState.RECONCILING
    provenance = case.repo.recovery_provenance(result.attempt_id)
    return case, result, provenance


def _snapshot(
    case,
    *,
    remote_id: str = "n-1",
    resume_id: str | None = "r-1",
    created_offset: int | None = 1,
) -> NegotiationSnapshot:
    return NegotiationSnapshot(
        remote_id=remote_id,
        vacancy_id="v-1",
        resume_id=resume_id,
        created_at=(
            None
            if created_offset is None
            else case.now + timedelta(seconds=created_offset)
        ),
        status="active",
    )


def _reconciler(case, reader: HistoryReader) -> HHApplicationReconciler:
    return HHApplicationReconciler(
        case.repo,
        reader,
        settings_provider=lambda: case.settings,
        clock_tolerance_seconds=2,
    )


def test_negotiation_snapshots_walk_pages_normalize_and_deduplicate() -> None:
    client = HHApplyClient(
        {
            "access_token": "token",
            "negotiation_statuses": ["active", "archived"],
        }
    )
    calls: list[tuple[str, int, int]] = []
    rows = {
        ("active", 0): SearchPage(
            items=[
                {
                    "id": "n-1",
                    "vacancy": {"id": "v-1"},
                    "resume": {"id": "r-1"},
                    "created_at": "2026-07-17T09:00:01+00:00",
                }
            ],
            page=0,
            pages=2,
            per_page=100,
            total=101,
        ),
        ("active", 1): SearchPage(
            items=[
                {
                    "id": "n-2",
                    "vacancy": {"id": "v-2"},
                    "resume": {"id": "r-2"},
                    "created_at": "2026-07-17T09:00:02+00:00",
                }
            ],
            page=1,
            pages=2,
            per_page=100,
            total=101,
        ),
        ("archived", 0): SearchPage(
            items=[
                {
                    "id": "n-1",
                    "vacancy": {"id": "v-1"},
                    "resume": {"id": "r-1"},
                    "created_at": "2026-07-17T09:00:01+00:00",
                }
            ],
            page=0,
            pages=1,
            per_page=100,
            total=1,
        ),
    }

    class Pages:
        def list_negotiations_page(self, *, status: str, page: int, per_page: int = 100):
            calls.append((status, page, per_page))
            return rows[(status, page)]

    client.session = Pages()  # type: ignore[assignment]

    snapshots = client.negotiation_snapshots("default")

    assert [row.remote_id for row in snapshots] == ["n-1", "n-2"]
    assert snapshots[0].resume_id == "r-1"
    assert snapshots[0].is_classifiable
    assert calls == [
        ("active", 0, 100),
        ("active", 1, 100),
        ("archived", 0, 100),
    ]


def test_accepted_then_disconnected_recovers_once_after_disable_and_kill(tmp_path) -> None:
    case, result, _provenance = _held_case(tmp_path)
    reader = HistoryReader([_snapshot(case)])
    reconciler = _reconciler(case, reader)
    disabled = _settings(enabled=False, generation=None)
    recovery = HHRecoverySweep(
        case.repo,
        reconciler_factory=lambda _account_id: reconciler,
        settings_provider=lambda: disabled,
        owner_token_factory=lambda: "recovery-owner",
    )
    try:
        assert len(case.transport.calls) == 1
        assert case.repo.release_lease(case.lease)
        case.repo.set_kill_switch("account", "default", actor="test")

        report = recovery.run(
            account_id="default",
            now=case.now + timedelta(seconds=60),
        )

        assert report.applied == 1
        assert len(case.transport.calls) == 1
        reservation = case.repo.active_reservation_for_attempt(result.attempt_id)
        assert reservation is not None and reservation.state.value == "consumed"
        assert case.repo.count_applications("default", "v-1", "r-1") == 1
        assert not hasattr(reader, "apply_outcome")
        assert not hasattr(recovery, "apply_outcome")
    finally:
        case.storage.close()


@pytest.mark.parametrize("code", ["duplicate", "post_dispatch_network_error"])
def test_same_resume_current_negotiation_finalizes_applied(tmp_path, code: str) -> None:
    case, result, provenance = _held_case(tmp_path, code=code)
    try:
        outcome = _reconciler(case, HistoryReader([_snapshot(case)])).reconcile(
            result.item_id,
            provenance,
            case.lease,
            now=case.now + timedelta(seconds=30),
        )

        assert outcome.outcome == "applied"
        assert case.repo.get_item(result.item_id).state is AutopilotState.APPLIED
        reservation = case.repo.active_reservation_for_attempt(result.attempt_id)
        assert reservation is not None and reservation.state.value == "consumed"
    finally:
        case.storage.close()


@pytest.mark.parametrize(
    ("resume_id", "created_offset"),
    [("r-2", 1), ("r-1", -60)],
)
def test_other_resume_or_predating_negotiation_is_external(
    tmp_path, resume_id: str, created_offset: int
) -> None:
    case, result, provenance = _held_case(tmp_path, code="duplicate")
    try:
        outcome = _reconciler(
            case,
            HistoryReader(
                [
                    _snapshot(
                        case,
                        resume_id=resume_id,
                        created_offset=created_offset,
                    )
                ]
            ),
        ).reconcile(
            result.item_id,
            provenance,
            case.lease,
            now=case.now + timedelta(seconds=30),
        )

        assert outcome.outcome == "duplicate_external"
        assert case.repo.get_item(result.item_id).state is AutopilotState.SKIPPED
        reservation = case.repo.active_reservation_for_attempt(result.attempt_id)
        assert reservation is None
        guard = case.repo.get_guard("default", "hh", "v-1")
        assert guard is not None and guard.status == "external_applied"
    finally:
        case.storage.close()


@pytest.mark.parametrize(
    ("checks", "max_attempts", "expected"),
    [(2, 4, AutopilotState.READY), (1, 1, AutopilotState.DEAD)],
)
def test_confirmed_absence_is_bounded_then_ready_or_dead(
    tmp_path, checks: int, max_attempts: int, expected: AutopilotState
) -> None:
    case, result, provenance = _held_case(
        tmp_path,
        checks=checks,
        max_attempts=max_attempts,
    )
    reconciler = _reconciler(case, HistoryReader())
    try:
        outcome = None
        for index in range(checks):
            outcome = reconciler.reconcile(
                result.item_id,
                provenance,
                case.lease,
                now=case.now + timedelta(seconds=30 * (index + 1)),
            )
        assert outcome is not None
        assert case.repo.get_item(result.item_id).state is expected
        reservation = case.repo.get_reservation(result.reservation_id)
        assert reservation is not None and reservation.state.value == "released"
        if expected is AutopilotState.READY:
            assert outcome.outcome == "ready"
        else:
            assert outcome.outcome == "dead"
    finally:
        case.storage.close()


def test_unclassifiable_history_opens_one_held_ambiguity(tmp_path) -> None:
    case, result, provenance = _held_case(tmp_path, code="duplicate")
    reader = HistoryReader(
        [_snapshot(case, remote_id="n-unknown", resume_id=None, created_offset=None)]
    )
    reconciler = _reconciler(case, reader)
    try:
        first = reconciler.reconcile(
            result.item_id,
            provenance,
            case.lease,
            now=case.now + timedelta(seconds=30),
        )
        second = reconciler.reconcile(
            result.item_id,
            provenance,
            case.lease,
            now=case.now + timedelta(seconds=31),
        )

        assert first.outcome == second.outcome == "ambiguous_application"
        assert first.challenge_id == second.challenge_id
        assert case.repo.count_challenges() == 1
        challenge = case.repo.get_challenge(first.challenge_id)
        assert challenge is not None and challenge.challenge_type == "ambiguous_application"
        reservation = case.repo.get_reservation(result.reservation_id)
        assert reservation is not None and reservation.state.value == "held"
    finally:
        case.storage.close()


def test_auth_read_failure_holds_and_opens_one_account_challenge(tmp_path) -> None:
    case, result, provenance = _held_case(tmp_path)
    reader = HistoryReader()
    reader.error = HHAuthError("expired", code="auth_expired")
    reconciler = _reconciler(case, reader)
    try:
        first = reconciler.reconcile(
            result.item_id,
            provenance,
            case.lease,
            now=case.now + timedelta(seconds=30),
        )
        second = reconciler.reconcile(
            result.item_id,
            provenance,
            case.lease,
            now=case.now + timedelta(seconds=31),
        )

        assert first.outcome == second.outcome == "manual_auth"
        assert first.challenge_id == second.challenge_id
        challenge = case.repo.get_challenge(first.challenge_id)
        assert challenge is not None
        assert challenge.scope == "account"
        assert challenge.expires_at == ""
        assert case.repo.count_challenges() == 1
        reservation = case.repo.get_reservation(result.reservation_id)
        assert reservation is not None and reservation.state.value == "held"
    finally:
        case.storage.close()


def test_sweep_recovers_stale_applying_without_a_grant(tmp_path) -> None:
    case = _make_case(tmp_path, settings=_reconcile_settings())
    reader = HistoryReader([_snapshot(case)])
    reconciler = _reconciler(case, reader)
    try:
        snapshot, _settings_value = case.executor._current_snapshot(
            case.item,
            case.authorization,
            now=case.now,
        )
        prepared = case.repo.prepare_dispatch(
            item_id=case.item.id,
            expected_version=case.item.version,
            authorization=case.authorization,
            fencing_token=case.lease.fencing_token,
            snapshot=snapshot,
            now=case.now,
        )
        assert case.repo.get_item(case.item.id).state is AutopilotState.APPLYING
        assert case.repo.release_lease(case.lease)
        case.repo.revoke_grants(["default"], actor="test", reason="test")
        recovery = HHRecoverySweep(
            case.repo,
            reconciler_factory=lambda _account_id: reconciler,
            settings_provider=lambda: _settings(enabled=False, generation=None),
            owner_token_factory=lambda: "recovery-owner",
        )

        report = recovery.run(
            account_id="default",
            now=case.now + timedelta(seconds=60),
        )

        assert report.applied == 1
        assert case.repo.get_item(case.item.id).state is AutopilotState.APPLIED
        reservation = case.repo.active_reservation_for_attempt(prepared.attempt_id)
        assert reservation is not None and reservation.state.value == "consumed"
        assert case.transport.calls == []
    finally:
        case.storage.close()


@pytest.mark.parametrize(
    ("action", "state", "reservation_state"),
    [
        ("confirmed_applied", AutopilotState.APPLIED, "consumed"),
        ("confirmed_not_applied_retry", AutopilotState.READY, "released"),
        ("confirmed_not_applied_skip", AutopilotState.SKIPPED, "released"),
        ("retry_reconciliation", AutopilotState.RECONCILING, "held"),
    ],
)
def test_expired_ambiguity_accepts_only_exact_late_actions(
    tmp_path,
    action: str,
    state: AutopilotState,
    reservation_state: str,
) -> None:
    case, result, provenance = _held_case(tmp_path, code="duplicate")
    reader = HistoryReader(
        [_snapshot(case, remote_id="n-late", resume_id=None, created_offset=None)]
    )
    try:
        ambiguous = _reconciler(case, reader).reconcile(
            result.item_id,
            provenance,
            case.lease,
            now=case.now + timedelta(seconds=30),
        )
        challenge = case.repo.get_challenge(ambiguous.challenge_id)
        assert challenge is not None
        assert case.repo.release_lease(case.lease)
        late = case.now + timedelta(hours=25)
        lease = case.repo.acquire_lease(
            "default",
            "late-operator",
            ttl_seconds=3600,
            now=late,
        )
        assert lease is not None
        case.repo.expire_challenge(
            challenge.id,
            fencing_token=lease.fencing_token,
            now=late,
        )
        with pytest.raises(RuntimeError, match="unresolved_ambiguity"):
            case.repo.requeue_dead(
                challenge.item_id,
                actor="cli",
                fencing_token=lease.fencing_token,
                now=late,
            )

        item = case.repo.resolve_challenge(
            challenge.id,
            action=action,
            actor="cli",
            fencing_token=lease.fencing_token,
            now=late,
        )

        assert item.state is state
        reservation = case.repo.get_reservation(challenge.reservation_id)
        assert reservation is not None and reservation.state.value == reservation_state
        resolved = case.repo.get_challenge(challenge.id)
        assert resolved is not None and resolved.status == "resolved"
        if action == "confirmed_applied":
            assert case.repo.count_applications("default", "v-1", "r-1") == 1
    finally:
        case.storage.close()


def test_nonambiguous_dead_item_can_be_audited_requeued(tmp_path) -> None:
    case, result, provenance = _held_case(tmp_path, checks=1, max_attempts=1)
    try:
        _reconciler(case, HistoryReader()).reconcile(
            result.item_id,
            provenance,
            case.lease,
            now=case.now + timedelta(seconds=30),
        )
        item = case.repo.requeue_dead(
            result.item_id,
            actor="cli",
            fencing_token=case.lease.fencing_token,
            now=case.now + timedelta(seconds=31),
        )

        assert item.state is AutopilotState.READY
        assert case.repo.list_events(item.id)[-1]["reason_code"] == "operator_requeue"
    finally:
        case.storage.close()
