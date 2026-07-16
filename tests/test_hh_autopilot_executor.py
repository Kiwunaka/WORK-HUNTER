from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any, Callable
from zoneinfo import ZoneInfo

import pytest

from work_hunter.hh_autopilot.config import (
    AutopilotSettings,
    default_autopilot_config,
    parse_autopilot_settings,
)
from work_hunter.hh_autopilot.executor import (
    HHApplicationExecutor,
    HHRetryPolicy,
)
from work_hunter.hh_autopilot.repository import (
    AutopilotRepository,
    LostLease,
    RepositoryAuthorizationDenied,
    StaleWrite,
)
from work_hunter.hh_autopilot.types import (
    AIDecision,
    AutopilotState,
    DeliveryCertainty,
    DispatchConfigSnapshot,
    DispatchOutcome,
    FilterDecision,
    LiteralConfirmation,
    LiveAuthorization,
    RankScore,
    RankingDecision,
)
from work_hunter.models import Job
from work_hunter.storage import Storage


UTC = timezone.utc
RANK_COMPONENTS = (
    "role",
    "skills",
    "experience",
    "salary",
    "work_format",
    "area",
    "industry",
)


def _settings(
    *,
    enabled: bool = True,
    paused: bool = False,
    generation: int | None = 1,
    daily_limit: int = 50,
    run_limit: int = 10,
    max_attempts: int = 4,
) -> AutopilotSettings:
    raw = default_autopilot_config()
    account = raw["accounts"][0]
    account["enabled"] = enabled
    account["paused"] = paused
    account["authorization_generation"] = generation
    raw["schedule"] = {
        "days": [1, 2, 3, 4, 5, 6, 7],
        "start": "00:00",
        "end": "23:59",
        "interval_minutes": 60,
    }
    raw["limits"]["daily_success"] = daily_limit
    raw["limits"]["per_run_success"] = min(run_limit, daily_limit)
    raw["retry"]["max_attempts"] = max_attempts
    return parse_autopilot_settings(
        {"sources": {"hh": {"autopilot": raw}}}
    )


def _filter_decision() -> FilterDecision:
    return FilterDecision(
        passed=True,
        reason="hard_filters_passed",
        evidence={
            "checks": (
                "vacancy_open",
                "history",
                "blacklists",
                "keywords_and_roles",
                "area_and_relocation",
                "work_format",
                "experience",
                "salary",
                "candidate_constraints",
                "application_capabilities",
            )
        },
    )


def _ranking_decision() -> RankingDecision:
    score = 90.0
    return RankingDecision(
        ready=True,
        retry=False,
        reason="ai_suitable",
        rank_score=RankScore(
            score=score,
            components={name: score for name in RANK_COMPONENTS},
            weights={name: 1 / len(RANK_COMPONENTS) for name in RANK_COMPONENTS},
        ),
        ai_decision=AIDecision(
            available=True,
            suitable=True,
            confidence=0.95,
            evidence=("python",),
            reasons=(),
            reason="available",
        ),
    )


class FakeTransport:
    def __init__(self, repo: AutopilotRepository) -> None:
        self.repo = repo
        self.calls: list[dict[str, Any]] = []
        self.outcome = DispatchOutcome(
            code="applied",
            certainty=DeliveryCertainty.DEFINITE_RESPONSE,
            status_code=201,
            payload={"id": "n-1"},
        )
        self.observed_prepared_state = ""
        self.on_call: Callable[[], None] | None = None
        self._lock = Lock()

    def apply_outcome(
        self,
        vacancy_id: str,
        resume_id: str,
        message: str,
        *,
        timeout_seconds: int,
    ) -> DispatchOutcome:
        with self._lock:
            item = self.repo.conn.execute(
                """
                SELECT state FROM hh_autopilot_items
                WHERE vacancy_id = ? AND resume_id = ?
                """,
                (vacancy_id, resume_id),
            ).fetchone()
            self.observed_prepared_state = "" if item is None else str(item["state"])
            self.calls.append(
                {
                    "vacancy_id": vacancy_id,
                    "resume_id": resume_id,
                    "message": message,
                    "timeout_seconds": timeout_seconds,
                }
            )
            if self.on_call is not None:
                self.on_call()
            return self.outcome


class FakeLetters:
    def __init__(self) -> None:
        self.error: BaseException | None = None
        self.calls: list[Any] = []

    def render(self, prepared) -> str:
        self.calls.append(prepared)
        if self.error is not None:
            raise self.error
        if prepared.cover_letter_mode == "none":
            return ""
        return "Grounded cover letter"


@dataclass
class ExecutorCase:
    storage: Storage
    repo: AutopilotRepository
    transport: FakeTransport
    letters: FakeLetters
    settings: AutopilotSettings
    settings_provider: Callable[[], AutopilotSettings]
    policy_hash_provider: Callable[[str], str]
    executor: HHApplicationExecutor
    item: Any
    run: Any
    lease: Any
    authorization: LiveAuthorization | LiteralConfirmation
    now: datetime
    job_id: int


def _ready_item(
    repo: AutopilotRepository,
    run,
    lease,
    *,
    vacancy_id: str = "v-1",
    resume_id: str = "r-1",
):
    item = repo.create_item(
        run.id,
        run.account_id,
        vacancy_id,
        resume_id,
        "preset:python",
    )
    item = repo.record_filter_decision(
        item.id,
        expected_version=item.version,
        decision=_filter_decision(),
        run_id=run.id,
        fencing_token=lease.fencing_token,
    )
    item = repo.record_ranking_decision(
        item.id,
        expected_version=item.version,
        decision=_ranking_decision(),
        run_id=run.id,
        fencing_token=lease.fencing_token,
    )
    return repo.finalize_ranked_candidates(
        {item.id: item.version},
        selected_item_ids=[item.id],
        resume_policy="best_resume_only",
        run_id=run.id,
        fencing_token=lease.fencing_token,
    )[0]


def _make_case(
    tmp_path,
    *,
    live: bool = True,
    settings: AutopilotSettings | None = None,
    vacancy_id: str = "v-1",
    resume_id: str = "r-1",
    reference_id: str = "manual-1",
    authorization_type: str = "manual",
    one_shot_targets: tuple[tuple[str, str], ...] | None = None,
    max_success: int = 1,
) -> ExecutorCase:
    storage = Storage(tmp_path / "work-hunter.db")
    repo = AutopilotRepository(storage)
    now = datetime.now(UTC).replace(microsecond=0)
    lease = repo.acquire_lease(
        "default",
        "owner-1",
        ttl_seconds=3600,
        now=now,
    )
    assert lease is not None
    selected_settings = settings or _settings(
        enabled=live,
        generation=1 if live else None,
    )
    if live:
        repo.create_grants([("default", "policy-1", "tester", "test")])
        grant = repo.active_grant("default")
        assert grant is not None
        run = repo.create_run(
            "default",
            trigger="schedule",
            policy_hash="policy-1",
            grant_id=grant.id,
            fencing_token=lease.fencing_token,
        )
        authorization: LiveAuthorization | LiteralConfirmation = LiveAuthorization(
            grant_id=grant.id,
            scope="applications",
            account_id="default",
            run_id=run.id,
            fencing_token=lease.fencing_token,
            policy_hash="policy-1",
        )
    else:
        run = repo.create_run(
            "default",
            trigger="canary" if authorization_type == "canary" else "manual",
            policy_hash="manual-policy",
            fencing_token=lease.fencing_token,
        )
        targets = one_shot_targets or ((resume_id, vacancy_id),)
        repo.create_one_shot_authorization(
            reference_id,
            authorization_type=authorization_type,
            account_id="default",
            targets=targets,
            max_success=max_success,
            expires_at=now + timedelta(hours=1),
            now=now,
        )
        authorization = LiteralConfirmation(
            account_id="default",
            reference_id=reference_id,
        )
    job_id = storage.upsert_job(
        Job(
            source="hh",
            source_id=vacancy_id,
            url=f"https://hh.ru/vacancy/{vacancy_id}",
            title="Python developer",
            company="ACME",
            description="Python",
        )
    )
    item = _ready_item(
        repo,
        run,
        lease,
        vacancy_id=vacancy_id,
        resume_id=resume_id,
    )
    transport = FakeTransport(repo)
    letters = FakeLetters()

    def settings_provider():
        return selected_settings

    def policy_hash_provider(account_id):
        return "policy-1"

    executor = HHApplicationExecutor(
        repo,
        transport,
        letters,
        settings_provider=settings_provider,
        policy_hash_provider=policy_hash_provider,
        random_source=lambda: 0.5,
    )
    return ExecutorCase(
        storage=storage,
        repo=repo,
        transport=transport,
        letters=letters,
        settings=selected_settings,
        settings_provider=settings_provider,
        policy_hash_provider=policy_hash_provider,
        executor=executor,
        item=item,
        run=run,
        lease=lease,
        authorization=authorization,
        now=now,
        job_id=job_id,
    )


@pytest.fixture
def executor_case(tmp_path):
    case = _make_case(tmp_path)
    try:
        yield case
    finally:
        case.storage.close()


def test_success_prepares_before_post_and_finalizes_once(executor_case) -> None:
    result = executor_case.executor.execute(
        executor_case.item.id,
        executor_case.authorization,
        executor_case.lease,
        now=executor_case.now,
    )

    assert result.state is AutopilotState.APPLIED
    assert executor_case.transport.observed_prepared_state == "applying"
    assert executor_case.transport.calls == [
        {
            "vacancy_id": "v-1",
            "resume_id": "r-1",
            "message": "Grounded cover letter",
            "timeout_seconds": 30,
        }
    ]
    reservation = executor_case.repo.active_reservation_for_attempt(
        result.attempt_id
    )
    assert reservation is not None
    assert reservation.state.value == "consumed"
    assert (
        executor_case.repo.count_applications("default", "v-1", "r-1") == 1
    )
    guard = executor_case.repo.get_guard("default", "hh", "v-1")
    assert guard is not None
    assert guard.status == "applied"
    assert guard.application_count == 1
    assert executor_case.repo.get_run(executor_case.run.id).counters["successful"] == 1


def test_possibly_sent_never_becomes_retry_ready(executor_case) -> None:
    executor_case.transport.outcome = DispatchOutcome(
        code="post_dispatch_network_error",
        certainty=DeliveryCertainty.POSSIBLY_SENT,
    )

    result = executor_case.executor.execute(
        executor_case.item.id,
        executor_case.authorization,
        executor_case.lease,
        now=executor_case.now,
    )

    assert result.state is AutopilotState.RECONCILING
    reservation = executor_case.repo.active_reservation_for_attempt(
        result.attempt_id
    )
    assert reservation is not None
    assert reservation.state.value == "held"
    item = executor_case.repo.get_item(result.item_id)
    assert item is not None
    assert item.next_attempt_at
    attempt = executor_case.repo.get_application_attempt(result.attempt_id)
    assert attempt is not None
    assert attempt.delivery_certainty is DeliveryCertainty.POSSIBLY_SENT


def test_duplicate_definite_response_is_held_for_reconciliation(
    executor_case,
) -> None:
    executor_case.transport.outcome = DispatchOutcome(
        code="duplicate",
        certainty=DeliveryCertainty.DEFINITE_RESPONSE,
        status_code=400,
    )

    result = executor_case.executor.execute(
        executor_case.item.id,
        executor_case.authorization,
        executor_case.lease,
        now=executor_case.now,
    )

    assert result.state is AutopilotState.RECONCILING
    reservation = executor_case.repo.active_reservation_for_attempt(
        result.attempt_id
    )
    assert reservation is not None
    assert reservation.state.value == "held"


def test_stale_fence_is_rejected_before_transport(executor_case) -> None:
    executor_case.repo.conn.execute(
        """
        UPDATE hh_autopilot_leases
        SET expires_at = ?
        WHERE account_profile_id = 'default'
        """,
        ((executor_case.now - timedelta(seconds=1)).isoformat(),),
    )
    executor_case.repo.conn.commit()
    replacement = executor_case.repo.acquire_lease(
        "default",
        "owner-2",
        ttl_seconds=3600,
        now=executor_case.now,
    )
    assert replacement is not None

    with pytest.raises(LostLease):
        executor_case.executor.execute(
            executor_case.item.id,
            executor_case.authorization,
            executor_case.lease,
            now=executor_case.now,
        )

    assert executor_case.transport.calls == []


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("pause_db", "autopilot_disabled_or_paused"),
        ("kill_db", "kill_switch_active"),
        ("disabled_config", "authorization_state_mismatch"),
        ("paused_config", "autopilot_disabled_or_paused"),
        ("generation_config", "authorization_state_mismatch"),
        ("policy_config", "policy_hash_mismatch"),
        ("window_config", "outside_scheduler_window"),
    ],
)
def test_live_runtime_guards_block_before_transport(
    tmp_path, mutation: str, code: str
) -> None:
    case = _make_case(tmp_path)
    try:
        if mutation == "pause_db":
            case.repo.set_pause("account", "default", True)
        elif mutation == "kill_db":
            case.repo.set_kill_switch("account", "default", actor="tester")
        elif mutation == "disabled_config":
            current = _settings(enabled=False, generation=None)
            case.executor.settings_provider = lambda: current
        elif mutation == "paused_config":
            current = _settings(paused=True)
            case.executor.settings_provider = lambda: current
        elif mutation == "generation_config":
            current = _settings(generation=2)
            case.executor.settings_provider = lambda: current
        elif mutation == "policy_config":
            case.executor.policy_hash_provider = lambda account_id: "changed"
        elif mutation == "window_config":
            schedule = dict(case.settings.schedule)
            schedule["days"] = tuple(
                day
                for day in range(1, 8)
                if day
                != case.now.astimezone(
                    ZoneInfo(case.settings.timezone)
                ).isoweekday()
            )
            current = replace(case.settings, schedule=schedule)
            case.executor.settings_provider = lambda: current

        with pytest.raises(RepositoryAuthorizationDenied) as raised:
            case.executor.execute(
                case.item.id,
                case.authorization,
                case.lease,
                now=case.now,
            )

        assert raised.value.code == code
        assert case.transport.calls == []
        assert case.repo.count_application_attempts() == 0
        assert case.repo.count_reservations() == 0
    finally:
        case.storage.close()


def test_wrong_account_authorization_never_reaches_transport(executor_case) -> None:
    wrong = replace(executor_case.authorization, account_id="other")

    with pytest.raises(PermissionError):
        executor_case.executor.execute(
            executor_case.item.id,
            wrong,
            executor_case.lease,
            now=executor_case.now,
        )

    assert executor_case.transport.calls == []


def test_account_cooldown_never_reaches_transport(executor_case) -> None:
    executor_case.repo.set_account_cooldown(
        "default",
        executor_case.now + timedelta(minutes=5),
        "rate_limited",
        executor_case.lease.fencing_token,
        now=executor_case.now,
    )

    with pytest.raises(Exception) as raised:
        executor_case.executor.execute(
            executor_case.item.id,
            executor_case.authorization,
            executor_case.lease,
            now=executor_case.now,
        )

    assert "cooldown" in str(raised.value)
    assert executor_case.transport.calls == []


def test_daily_quota_exhaustion_never_reaches_transport(tmp_path) -> None:
    case = _make_case(tmp_path, settings=_settings(daily_limit=1))
    try:
        local_date = case.now.astimezone(
            __import__("zoneinfo").ZoneInfo(case.settings.timezone)
        ).date().isoformat()
        case.repo.conn.execute(
            """
            INSERT INTO hh_autopilot_quota_reservations (
                attempt_id, run_id, source, remote_negotiation_id,
                account_profile_id, timezone, local_date, state,
                fencing_token, created_at, resolved_at
            ) VALUES (
                NULL, NULL, 'external_sync', 'existing-negotiation',
                'default', ?, ?, 'consumed', ?, ?, ?
            )
            """,
            (
                case.settings.timezone,
                local_date,
                case.lease.fencing_token,
                case.now.isoformat(),
                case.now.isoformat(),
            ),
        )
        case.repo.conn.commit()

        with pytest.raises(Exception) as raised:
            case.executor.execute(
                case.item.id,
                case.authorization,
                case.lease,
                now=case.now,
            )

        assert "quota" in str(raised.value)
        assert case.transport.calls == []
        assert case.repo.count_application_attempts() == 0
    finally:
        case.storage.close()


def test_account_vacancy_guard_conflict_never_reaches_transport(
    executor_case,
) -> None:
    executor_case.repo.conn.execute(
        """
        INSERT INTO hh_application_account_guards (
            account_profile_id, source, source_id, owner_attempt_id,
            first_resume_id, status, application_id, application_count,
            created_at, updated_at
        ) VALUES (
            'default', 'hh', 'v-1', NULL, 'other-resume',
            'applied', NULL, 1, ?, ?
        )
        """,
        (executor_case.now.isoformat(), executor_case.now.isoformat()),
    )
    executor_case.repo.conn.commit()

    with pytest.raises(StaleWrite):
        executor_case.executor.execute(
            executor_case.item.id,
            executor_case.authorization,
            executor_case.lease,
            now=executor_case.now,
        )

    assert executor_case.transport.calls == []
    assert executor_case.repo.count_application_attempts() == 0


def test_item_cas_change_during_current_config_read_blocks_post(
    executor_case,
) -> None:
    changed = False

    def mutate_item_then_read() -> AutopilotSettings:
        nonlocal changed
        if not changed:
            executor_case.repo.conn.execute(
                """
                UPDATE hh_autopilot_items
                SET version = version + 1
                WHERE id = ?
                """,
                (executor_case.item.id,),
            )
            executor_case.repo.conn.commit()
            changed = True
        return executor_case.settings

    executor_case.executor.settings_provider = mutate_item_then_read

    with pytest.raises(StaleWrite):
        executor_case.executor.execute(
            executor_case.item.id,
            executor_case.authorization,
            executor_case.lease,
            now=executor_case.now,
        )

    assert executor_case.transport.calls == []
    assert executor_case.repo.count_application_attempts() == 0


def test_prepare_dispatch_rolls_back_every_write_on_quota_insert_failure(
    executor_case,
) -> None:
    executor_case.repo.conn.execute(
        """
        CREATE TRIGGER abort_dispatch_reservation
        BEFORE INSERT ON hh_autopilot_quota_reservations
        BEGIN
            SELECT RAISE(ABORT, 'reservation blocked');
        END
        """
    )
    executor_case.repo.conn.commit()
    before_events = executor_case.repo.list_events(executor_case.item.id)

    with pytest.raises(sqlite3.IntegrityError, match="reservation blocked"):
        executor_case.executor.execute(
            executor_case.item.id,
            executor_case.authorization,
            executor_case.lease,
            now=executor_case.now,
        )

    assert executor_case.transport.calls == []
    assert executor_case.repo.get_item(executor_case.item.id) == executor_case.item
    assert executor_case.repo.list_events(executor_case.item.id) == before_events
    assert executor_case.repo.count_application_attempts() == 0
    assert executor_case.repo.count_reservations() == 0
    assert executor_case.repo.count_guards() == 0


def test_success_finalization_trigger_rolls_back_every_finalization_change(
    executor_case,
) -> None:
    executor_case.repo.conn.execute(
        """
        CREATE TRIGGER abort_application_upsert
        BEFORE INSERT ON applications
        BEGIN
            SELECT RAISE(ABORT, 'application blocked');
        END
        """
    )
    executor_case.repo.conn.commit()

    with pytest.raises(sqlite3.IntegrityError, match="application blocked"):
        executor_case.executor.execute(
            executor_case.item.id,
            executor_case.authorization,
            executor_case.lease,
            now=executor_case.now,
        )

    item = executor_case.repo.get_item(executor_case.item.id)
    assert item is not None
    assert item.state is AutopilotState.APPLYING
    assert executor_case.repo.count_applications("default", "v-1", "r-1") == 0
    assert executor_case.repo.count_application_attempts() == 1
    attempt = executor_case.repo.get_application_attempt(item.active_attempt_id)
    assert attempt is not None
    assert attempt.status == "applying"
    reservation = executor_case.repo.active_reservation_for_attempt(attempt.id)
    assert reservation is not None
    assert reservation.state.value == "reserved"
    guard = executor_case.repo.get_guard("default", "hh", "v-1")
    assert guard is not None
    assert guard.status == "active"
    assert guard.application_count == 0
    assert executor_case.repo.get_run(executor_case.run.id).counters.get(
        "successful", 0
    ) == 0


def test_cover_letter_failure_releases_reservation_without_post(
    executor_case,
) -> None:
    executor_case.letters.error = RuntimeError("AI secret must not leak")

    result = executor_case.executor.execute(
        executor_case.item.id,
        executor_case.authorization,
        executor_case.lease,
        now=executor_case.now,
    )

    assert executor_case.transport.calls == []
    assert result.state is AutopilotState.RETRY_WAIT
    assert result.reservation_id is None
    assert executor_case.repo.count_reservations() == 0
    assert executor_case.repo.count_guards() == 0
    item = executor_case.repo.get_item(result.item_id)
    assert item is not None
    assert item.application_attempt_count == 0
    attempt = executor_case.repo.get_application_attempt(result.attempt_id)
    assert attempt is not None
    assert (
        attempt.delivery_certainty
        is DeliveryCertainty.DEFINITELY_NOT_SENT
    )
    assert "secret" not in attempt.raw_result_json


@pytest.mark.parametrize(
    ("outcome", "expected_state", "reservation_state"),
    [
        (
            DispatchOutcome(
                code="vacancy_closed",
                certainty=DeliveryCertainty.DEFINITE_RESPONSE,
                status_code=400,
            ),
            AutopilotState.SKIPPED,
            "released",
        ),
        (
            DispatchOutcome(
                code="server_error",
                certainty=DeliveryCertainty.DEFINITE_RESPONSE,
                status_code=503,
            ),
            AutopilotState.RETRY_WAIT,
            "released",
        ),
        (
            DispatchOutcome(
                code="manual_captcha",
                certainty=DeliveryCertainty.DEFINITE_RESPONSE,
                status_code=403,
                location="https://hh.ru/captcha",
            ),
            AutopilotState.MANUAL_CHALLENGE,
            "released",
        ),
        (
            DispatchOutcome(
                code="manual_assessment",
                certainty=DeliveryCertainty.DEFINITE_RESPONSE,
                status_code=303,
            ),
            AutopilotState.MANUAL_CHALLENGE,
            "released",
        ),
        (
            DispatchOutcome(
                code="form_required",
                certainty=DeliveryCertainty.DEFINITE_RESPONSE,
                status_code=303,
                location="https://hh.ru/applicant/vacancy_response",
            ),
            AutopilotState.MANUAL_CHALLENGE,
            "released",
        ),
    ],
)
def test_definite_outcome_finalization(
    tmp_path, outcome, expected_state, reservation_state
) -> None:
    case = _make_case(tmp_path)
    try:
        case.transport.outcome = outcome
        result = case.executor.execute(
            case.item.id,
            case.authorization,
            case.lease,
            now=case.now,
        )

        assert result.state is expected_state
        reservation = case.repo.get_reservation(result.reservation_id)
        assert reservation is not None
        assert reservation.state.value == reservation_state
        if expected_state is AutopilotState.MANUAL_CHALLENGE:
            challenge = case.repo.get_challenge(
                case.repo.get_item(case.item.id).challenge_id
            )
            assert challenge is not None
            assert challenge.challenge_type == outcome.code
    finally:
        case.storage.close()


@pytest.mark.parametrize("code", ["rate_limited", "hh_daily_limit"])
def test_rate_and_daily_limits_set_account_cooldown_and_release(
    tmp_path, code
) -> None:
    case = _make_case(tmp_path)
    try:
        case.transport.outcome = DispatchOutcome(
            code=code,
            certainty=DeliveryCertainty.DEFINITE_RESPONSE,
            status_code=429 if code == "rate_limited" else 403,
            retry_after_seconds=600 if code == "rate_limited" else None,
        )

        result = case.executor.execute(
            case.item.id,
            case.authorization,
            case.lease,
            now=case.now,
        )

        assert result.state is AutopilotState.RETRY_WAIT
        assert case.repo.get_reservation(result.reservation_id).state.value == "released"
        cooldown = case.repo.get_account_state("default")
        assert cooldown is not None
        assert cooldown.block_reason == code
        assert datetime.fromisoformat(cooldown.blocked_until) > case.now
    finally:
        case.storage.close()


def test_local_finalization_does_not_recheck_revoked_grant_or_kill_switch(
    executor_case,
) -> None:
    executor_case.transport.on_call = lambda: executor_case.repo.set_kill_switch(
        "account",
        "default",
        actor="operator",
    )

    result = executor_case.executor.execute(
        executor_case.item.id,
        executor_case.authorization,
        executor_case.lease,
        now=executor_case.now,
    )

    assert result.state is AutopilotState.APPLIED
    assert executor_case.repo.active_grant("default") is None
    assert executor_case.repo.kill_switch_active("default")


def test_literal_confirmation_is_exact_target_and_ignores_pause_window(
    tmp_path,
) -> None:
    settings = _settings(enabled=False, paused=True, generation=None)
    schedule = dict(settings.schedule)
    schedule["days"] = ()
    settings = replace(settings, schedule=schedule)
    case = _make_case(tmp_path, live=False, settings=settings)
    try:
        case.repo.set_pause("account", "default", True)

        result = case.executor.execute(
            case.item.id,
            case.authorization,
            case.lease,
            now=case.now,
        )

        assert result.state is AutopilotState.APPLIED
        row = case.repo.conn.execute(
            """
            SELECT status FROM hh_autopilot_one_shot_targets
            WHERE authorization_ref = 'manual-1'
              AND resume_id = 'r-1' AND vacancy_id = 'v-1'
            """
        ).fetchone()
        assert row["status"] == "succeeded"
    finally:
        case.storage.close()


def test_literal_confirmation_rejects_target_not_in_immutable_set(
    tmp_path,
) -> None:
    case = _make_case(
        tmp_path,
        live=False,
        one_shot_targets=(("r-1", "other-vacancy"),),
    )
    try:
        with pytest.raises(RepositoryAuthorizationDenied) as raised:
            case.executor.execute(
                case.item.id,
                case.authorization,
                case.lease,
                now=case.now,
            )

        assert raised.value.code == "literal_target_mismatch"
        assert case.transport.calls == []
    finally:
        case.storage.close()


def test_literal_prepare_reserves_success_capacity_before_finalization(
    tmp_path,
) -> None:
    case = _make_case(
        tmp_path,
        live=False,
        one_shot_targets=(("r-1", "v-1"), ("r-1", "v-2")),
        max_success=1,
    )
    try:
        case.storage.upsert_job(
            Job(
                source="hh",
                source_id="v-2",
                url="https://hh.ru/vacancy/v-2",
                title="Python developer",
            )
        )
        second_item = _ready_item(
            case.repo,
            case.run,
            case.lease,
            vacancy_id="v-2",
            resume_id="r-1",
        )
        account = case.settings.accounts[0]
        snapshot = DispatchConfigSnapshot(
            account_id="default",
            enabled=account.enabled,
            paused=account.paused,
            authorization_generation=account.authorization_generation,
            policy_hash="manual-policy",
            within_scheduler_window=True,
            timezone_name=case.settings.timezone,
            daily_limit=case.settings.limits.daily_success,
            run_limit=case.settings.limits.per_run_success,
            resume_policy=str(case.settings.application["resume_policy"]),
            cover_letter_mode=str(
                case.settings.application["cover_letter_mode"]
            ),
            lease_ttl_seconds=case.settings.lease.ttl_seconds,
            request_timeout_seconds=case.settings.lease.request_timeout_seconds,
        )
        case.repo.prepare_dispatch(
            item_id=case.item.id,
            expected_version=case.item.version,
            authorization=case.authorization,
            fencing_token=case.lease.fencing_token,
            snapshot=snapshot,
            now=case.now,
        )
        before_events = case.repo.list_events(second_item.id)
        before_attempts = case.repo.count_application_attempts()
        before_reservations = case.repo.count_reservations()
        before_guards = case.repo.count_guards()

        with pytest.raises(RepositoryAuthorizationDenied) as raised:
            case.repo.prepare_dispatch(
                item_id=second_item.id,
                expected_version=second_item.version,
                authorization=case.authorization,
                fencing_token=case.lease.fencing_token,
                snapshot=snapshot,
                now=case.now,
            )

        assert raised.value.code == "literal_authorization_inactive"
        assert case.repo.get_item(second_item.id) == second_item
        assert case.repo.list_events(second_item.id) == before_events
        assert case.repo.count_application_attempts() == before_attempts
        assert case.repo.count_reservations() == before_reservations
        assert case.repo.count_guards() == before_guards
        assert case.repo.get_guard("default", "hh", "v-2") is None
        second_target = case.repo.conn.execute(
            """
            SELECT status, active_attempt_id
            FROM hh_autopilot_one_shot_targets
            WHERE authorization_ref = 'manual-1'
              AND resume_id = 'r-1' AND vacancy_id = 'v-2'
            """
        ).fetchone()
        assert second_target["status"] == "pending"
        assert second_target["active_attempt_id"] is None
    finally:
        case.storage.close()


def test_literal_campaign_success_cap_blocks_second_target(tmp_path) -> None:
    case = _make_case(
        tmp_path,
        live=False,
        one_shot_targets=(("r-1", "v-1"), ("r-1", "v-2")),
        max_success=1,
    )
    try:
        first = case.executor.execute(
            case.item.id,
            case.authorization,
            case.lease,
            now=case.now,
        )
        assert first.state is AutopilotState.APPLIED
        case.storage.upsert_job(
            Job(
                source="hh",
                source_id="v-2",
                url="https://hh.ru/vacancy/v-2",
                title="Python developer",
            )
        )
        second_item = _ready_item(
            case.repo,
            case.run,
            case.lease,
            vacancy_id="v-2",
            resume_id="r-1",
        )

        with pytest.raises(RepositoryAuthorizationDenied) as raised:
            case.executor.execute(
                second_item.id,
                case.authorization,
                case.lease,
                now=case.now,
            )

        assert raised.value.code == "literal_authorization_inactive"
        assert len(case.transport.calls) == 1
    finally:
        case.storage.close()


def test_concurrent_executors_issue_only_one_post(tmp_path) -> None:
    case = _make_case(tmp_path)
    database_path = case.storage.path

    class SharedTransport:
        def __init__(self):
            self.calls = []
            self.lock = Lock()

        def apply_outcome(
            self,
            vacancy_id,
            resume_id,
            message,
            *,
            timeout_seconds,
        ):
            with self.lock:
                self.calls.append((vacancy_id, resume_id))
            return DispatchOutcome(
                code="applied",
                certainty=DeliveryCertainty.DEFINITE_RESPONSE,
                status_code=201,
                payload={"id": "n-1"},
            )

    shared_transport = SharedTransport()

    def call(_worker_id):
        storage = Storage(database_path)
        repo = AutopilotRepository(storage)
        executor = HHApplicationExecutor(
            repo,
            shared_transport,
            FakeLetters(),
            settings_provider=lambda: case.settings,
            policy_hash_provider=lambda account_id: "policy-1",
            random_source=lambda: 0.5,
        )
        try:
            return executor.execute(
                case.item.id,
                case.authorization,
                case.lease,
                now=case.now,
            )
        except (StaleWrite, LostLease):
            return None
        finally:
            storage.close()

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(call, (1, 2)))

        assert len(shared_transport.calls) == 1
        assert sum(result is not None for result in results) == 1
        assert case.repo.get_item(case.item.id).state is AutopilotState.APPLIED
    finally:
        case.storage.close()


def test_retry_policy_is_bounded_persistable_and_honors_retry_after() -> None:
    settings = _settings()
    policy = HHRetryPolicy(settings.retry)
    now = datetime(2026, 7, 17, 9, 0, tzinfo=UTC)
    outcome = DispatchOutcome(
        code="server_error",
        certainty=DeliveryCertainty.DEFINITE_RESPONSE,
        retry_after_seconds=300,
    )

    decision = policy.classify(
        outcome,
        attempt_count=2,
        now=now,
        random_value=0.5,
    )

    assert decision.target is AutopilotState.RETRY_WAIT
    assert decision.reason == "server_error"
    assert datetime.fromisoformat(decision.next_attempt_at) == now + timedelta(
        seconds=300
    )


def test_retry_policy_never_retries_possibly_sent_or_permanent_outcome() -> None:
    policy = HHRetryPolicy(_settings().retry)
    now = datetime(2026, 7, 17, 9, 0, tzinfo=UTC)

    ambiguous = policy.classify(
        DispatchOutcome(
            code="post_dispatch_network_error",
            certainty=DeliveryCertainty.POSSIBLY_SENT,
        ),
        attempt_count=1,
        now=now,
        random_value=0.5,
    )
    permanent = policy.classify(
        DispatchOutcome(
            code="vacancy_closed",
            certainty=DeliveryCertainty.DEFINITE_RESPONSE,
        ),
        attempt_count=1,
        now=now,
        random_value=0.5,
    )

    assert ambiguous.target is AutopilotState.RECONCILING
    assert datetime.fromisoformat(ambiguous.next_attempt_at) > now
    assert permanent.target is AutopilotState.SKIPPED
    assert permanent.next_attempt_at == ""


def test_retry_policy_exhaustion_is_terminal() -> None:
    settings = _settings(max_attempts=2)
    policy = HHRetryPolicy(settings.retry)

    decision = policy.classify(
        DispatchOutcome(
            code="server_error",
            certainty=DeliveryCertainty.DEFINITE_RESPONSE,
        ),
        attempt_count=2,
        now=datetime(2026, 7, 17, 9, 0, tzinfo=UTC),
        random_value=0.5,
    )

    assert decision.target is AutopilotState.DEAD
    assert decision.reason == "retry_exhausted"
