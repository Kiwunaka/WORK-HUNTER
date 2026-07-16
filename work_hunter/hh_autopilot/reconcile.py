from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Protocol, Sequence

from work_hunter.hh_transport.errors import HHAuthError

from .config import AutopilotSettings
from .repository import AutopilotRepository, LeaseRecord
from .types import AutopilotState, RecoveryProvenance


@dataclass(frozen=True)
class NegotiationSnapshot:
    remote_id: str
    vacancy_id: str
    resume_id: str | None
    created_at: datetime | None
    status: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "remote_id", _required_text(self.remote_id, "remote_id"))
        object.__setattr__(
            self,
            "vacancy_id",
            _required_text(self.vacancy_id, "vacancy_id"),
        )
        if self.resume_id is not None:
            object.__setattr__(
                self,
                "resume_id",
                _required_text(self.resume_id, "resume_id").casefold(),
            )
        if self.created_at is not None:
            object.__setattr__(self, "created_at", _utc(self.created_at))
        object.__setattr__(self, "status", _required_text(self.status, "status"))

    @property
    def is_classifiable(self) -> bool:
        return self.resume_id is not None and self.created_at is not None

    @classmethod
    def from_payload(
        cls,
        payload: Mapping[str, Any],
        *,
        status: str,
    ) -> NegotiationSnapshot:
        if not isinstance(payload, Mapping):
            raise TypeError("negotiation payload must be a mapping")
        remote_id = _payload_id(payload.get("id"), "negotiation id")
        vacancy_id = _payload_id(
            payload.get("vacancy_id", payload.get("vacancy")),
            "vacancy id",
        )
        resume_id = _optional_payload_id(
            payload.get("resume_id", payload.get("resume"))
        )
        created_at = _optional_timestamp(
            payload.get("created_at", payload.get("created"))
        )
        return cls(
            remote_id=remote_id,
            vacancy_id=vacancy_id,
            resume_id=resume_id,
            created_at=created_at,
            status=status,
        )


class NegotiationReader(Protocol):
    def negotiation_snapshots(
        self,
        account_id: str,
    ) -> Sequence[NegotiationSnapshot]: ...


@dataclass(frozen=True)
class ReconcileResult:
    item_id: int
    outcome: str
    state: AutopilotState
    challenge_id: int | None = None


@dataclass
class RecoveryReport:
    applied: int = 0
    duplicate_external: int = 0
    ready: int = 0
    dead: int = 0
    ambiguous_application: int = 0
    manual_auth: int = 0
    reconciling: int = 0
    busy: int = 0
    failed: int = 0

    def add(self, result: ReconcileResult) -> None:
        if not isinstance(result, ReconcileResult):
            raise TypeError("result must be a ReconcileResult")
        if hasattr(self, result.outcome):
            setattr(self, result.outcome, getattr(self, result.outcome) + 1)
        else:
            self.failed += 1


class HHApplicationReconciler:
    def __init__(
        self,
        repository: AutopilotRepository,
        reader: NegotiationReader,
        *,
        settings_provider: Callable[[], AutopilotSettings],
        clock_tolerance_seconds: int = 2,
    ) -> None:
        if not isinstance(repository, AutopilotRepository):
            raise TypeError("repository must be an AutopilotRepository")
        if not callable(getattr(reader, "negotiation_snapshots", None)):
            raise TypeError("reader must expose negotiation_snapshots")
        if callable(getattr(reader, "apply_outcome", None)):
            raise TypeError("reconciliation reader must not expose application POST")
        if not callable(settings_provider):
            raise TypeError("settings_provider must be callable")
        if type(clock_tolerance_seconds) is not int or not 0 <= clock_tolerance_seconds <= 300:
            raise ValueError("clock_tolerance_seconds must be in 0..300")
        self.repository = repository
        self.reader = reader
        self.settings_provider = settings_provider
        self.clock_tolerance = timedelta(seconds=clock_tolerance_seconds)

    def reconcile(
        self,
        item_id: int,
        provenance: RecoveryProvenance,
        lease: LeaseRecord,
        now: datetime | str | None = None,
    ) -> ReconcileResult:
        instant = _utc_now(now)
        context = self.repository.load_reconciliation_context(
            item_id,
            provenance,
            lease.fencing_token,
            now=instant,
        )
        if context.challenge_type in {"ambiguous_application", "manual_auth"}:
            return ReconcileResult(
                item_id=context.prepared.item_id,
                outcome=context.challenge_type,
                state=AutopilotState.MANUAL_CHALLENGE,
                challenge_id=context.challenge_id,
            )

        settings = self.settings_provider()
        if type(settings) is not AutopilotSettings:
            raise TypeError("settings_provider returned an invalid snapshot")
        try:
            raw_snapshots = self.reader.negotiation_snapshots(context.prepared.account_id)
        except HHAuthError:
            challenge = self.repository.open_reconciliation_auth_challenge(
                context,
                lease.fencing_token,
                now=instant,
            )
            return ReconcileResult(
                item_id=context.prepared.item_id,
                outcome="manual_auth",
                state=AutopilotState.MANUAL_CHALLENGE,
                challenge_id=challenge.id,
            )

        if isinstance(raw_snapshots, (str, bytes)) or not isinstance(
            raw_snapshots, Sequence
        ):
            raise TypeError("negotiation_snapshots must return a sequence")
        snapshots = tuple(raw_snapshots)
        if any(type(row) is not NegotiationSnapshot for row in snapshots):
            raise TypeError("negotiation reader returned an invalid snapshot")

        relevant = [
            row for row in snapshots if row.vacancy_id == context.prepared.vacancy_id
        ]
        threshold = context.dispatched_at - self.clock_tolerance
        exact = [
            row
            for row in relevant
            if row.is_classifiable
            and row.resume_id == context.prepared.resume_id
            and row.created_at is not None
            and row.created_at >= threshold
        ]
        if exact:
            selected = min(exact, key=lambda row: row.created_at or context.dispatched_at)
            item = self.repository.finalize_reconciled_applied(
                context,
                remote_negotiation_id=selected.remote_id,
                fencing_token=lease.fencing_token,
                now=instant,
            )
            return ReconcileResult(item.id, "applied", item.state)

        external = [
            row
            for row in relevant
            if row.is_classifiable
            and (
                row.resume_id != context.prepared.resume_id
                or (
                    row.created_at is not None
                    and row.created_at < threshold
                )
            )
        ]
        if external:
            selected = max(
                external,
                key=lambda row: row.created_at or datetime.min.replace(tzinfo=timezone.utc),
            )
            assert selected.created_at is not None
            item = self.repository.finalize_external_application(
                context,
                remote_negotiation_id=selected.remote_id,
                occurred_at=selected.created_at,
                timezone_name=settings.timezone,
                fencing_token=lease.fencing_token,
                now=instant,
            )
            return ReconcileResult(item.id, "duplicate_external", item.state)

        unclassifiable_ids = tuple(
            row.remote_id for row in relevant if not row.is_classifiable
        )
        item, maybe_challenge = self.repository.record_reconciliation_check(
            context,
            fencing_token=lease.fencing_token,
            max_checks=int(settings.retry["reconciliation_checks"]),
            delay_seconds=int(settings.retry["reconciliation_delay_seconds"]),
            max_attempts=int(settings.retry["max_attempts"]),
            challenge_expiry_hours=int(
                settings.application["challenge_expiry_hours"]
            ),
            unclassifiable_remote_ids=unclassifiable_ids,
            now=instant,
        )
        outcome = item.last_outcome_code
        if outcome == "reconciliation_pending":
            outcome = "reconciling"
        elif outcome == "confirmed_absent":
            outcome = "ready"
        elif outcome == "retry_exhausted":
            outcome = "dead"
        return ReconcileResult(
            item_id=item.id,
            outcome=outcome,
            state=item.state,
            challenge_id=(
                None if maybe_challenge is None else maybe_challenge.id
            ),
        )


class HHRecoverySweep:
    def __init__(
        self,
        repository: AutopilotRepository,
        *,
        reconciler_factory: Callable[[str], HHApplicationReconciler],
        settings_provider: Callable[[], AutopilotSettings],
        owner_token_factory: Callable[[], str],
    ) -> None:
        if not isinstance(repository, AutopilotRepository):
            raise TypeError("repository must be an AutopilotRepository")
        for value, name in (
            (reconciler_factory, "reconciler_factory"),
            (settings_provider, "settings_provider"),
            (owner_token_factory, "owner_token_factory"),
        ):
            if not callable(value):
                raise TypeError(f"{name} must be callable")
        self.repository = repository
        self.reconciler_factory = reconciler_factory
        self.settings_provider = settings_provider
        self.owner_token_factory = owner_token_factory

    def run(
        self,
        account_id: str | None = None,
        now: datetime | str | None = None,
    ) -> RecoveryReport:
        instant = _utc_now(now)
        settings = self.settings_provider()
        if type(settings) is not AutopilotSettings:
            raise TypeError("settings_provider returned an invalid snapshot")
        report = RecoveryReport()
        for account in self.repository.accounts_needing_recovery(
            account_id,
            now=instant,
        ):
            lease = self.repository.acquire_lease(
                account,
                self.owner_token_factory(),
                ttl_seconds=settings.lease.ttl_seconds,
                now=instant,
            )
            if lease is None:
                report.busy += 1
                continue
            run = self.repository.create_run(
                account,
                trigger="recovery",
                policy_hash="recovery",
                fencing_token=lease.fencing_token,
            )
            try:
                self.repository.recover_stale_applying(
                    account,
                    lease.fencing_token,
                    run_id=run.id,
                    now=instant,
                )
                reconciler = self.reconciler_factory(account)
                if not isinstance(reconciler, HHApplicationReconciler):
                    raise TypeError("reconciler_factory returned an invalid reconciler")
                for item in self.repository.due_reconciliation_items(
                    account,
                    now=instant,
                ):
                    if item.active_attempt_id is None:
                        report.failed += 1
                        continue
                    provenance = self.repository.recovery_provenance(
                        item.active_attempt_id
                    )
                    try:
                        report.add(
                            reconciler.reconcile(
                                item.id,
                                provenance,
                                lease,
                                now=instant,
                            )
                        )
                    except Exception:
                        report.failed += 1
                self.repository.finish_run(
                    run.id,
                    counters={
                        "applied": report.applied,
                        "duplicate_external": report.duplicate_external,
                        "ready": report.ready,
                        "dead": report.dead,
                        "failed": report.failed,
                    },
                    fencing_token=lease.fencing_token,
                )
            finally:
                self.repository.release_lease(lease)
        return report


def _required_text(value: Any, field: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{field} must be text")
    normalized = value.strip()
    if not normalized or "\0" in normalized:
        raise ValueError(f"{field} must be nonempty text")
    return normalized


def _payload_id(value: Any, field: str) -> str:
    if isinstance(value, Mapping):
        value = value.get("id")
    return _required_text(value, field)


def _optional_payload_id(value: Any) -> str | None:
    try:
        return _payload_id(value, "resume id").casefold()
    except (TypeError, ValueError):
        return None


def _optional_timestamp(value: Any) -> datetime | None:
    if type(value) is not str:
        return None
    try:
        return _utc(datetime.fromisoformat(value.strip()))
    except (TypeError, ValueError):
        return None


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError("timestamp must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


def _utc_now(value: datetime | str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, str):
        return _utc(datetime.fromisoformat(value.strip()))
    return _utc(value)


__all__ = [
    "HHApplicationReconciler",
    "HHRecoverySweep",
    "NegotiationReader",
    "NegotiationSnapshot",
    "ReconcileResult",
    "RecoveryReport",
]
