from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Iterator, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from work_hunter.storage import Storage, redact_for_storage

from .state_machine import assert_transition
from .types import AutopilotState, QuotaReservationState, RetryStage


RUN_TRIGGERS = frozenset(
    {"schedule", "manual", "shadow", "retry", "recovery", "canary"}
)
RUN_STATUSES = frozenset(
    {
        "created",
        "running",
        "stop_requested",
        "completed",
        "failed",
        "interrupted",
        "cancelled",
    }
)
INITIAL_RUN_STATUSES = frozenset({"created", "running"})
TERMINAL_RUN_STATUSES = frozenset({"completed", "failed", "interrupted", "cancelled"})
APPLICATION_SCOPE = "applications"
CONTROL_SCOPE_TYPES = frozenset({"global", "account"})
GLOBAL_CONTROL_SCOPE_ID = "global"
LEASE_TOMBSTONE_OWNER = ""
LEASE_TOMBSTONE_EXPIRY = "0001-01-01T00:00:00+00:00"
ACTIVE_QUOTA_STATES = (
    QuotaReservationState.RESERVED.value,
    QuotaReservationState.HELD.value,
    QuotaReservationState.CONSUMED.value,
)
UNRESOLVED_QUOTA_STATES = (
    QuotaReservationState.RESERVED.value,
    QuotaReservationState.HELD.value,
)


class StaleWrite(RuntimeError):
    pass


class LostLease(RuntimeError):
    pass


class KillSwitchActive(RuntimeError):
    pass


class CooldownActive(RuntimeError):
    __slots__ = ("_account_id", "_reason", "_until")

    def __init__(self, account_id: str, reason: str, until: str):
        self._account_id = account_id
        self._reason = reason
        self._until = until
        super().__init__(f"account {account_id} is in cooldown until {until}: {reason}")

    @property
    def account_id(self) -> str:
        return self._account_id

    @property
    def reason(self) -> str:
        return self._reason

    @property
    def until(self) -> str:
        return self._until


class QuotaExceeded(RuntimeError):
    __slots__ = ("_account_id", "_dimension", "_limit")

    def __init__(self, account_id: str, dimension: str, limit: int):
        self._account_id = account_id
        self._dimension = dimension
        self._limit = limit
        super().__init__(
            f"{dimension} quota limit {limit} exceeded for account {account_id}"
        )

    @property
    def account_id(self) -> str:
        return self._account_id

    @property
    def dimension(self) -> str:
        return self._dimension

    @property
    def limit(self) -> int:
        return self._limit


class TimezoneChangeUnsafe(RuntimeError):
    __slots__ = ("_account_id", "_reason")

    def __init__(self, account_id: str, reason: str):
        self._account_id = account_id
        self._reason = reason
        super().__init__(
            f"timezone change is unsafe for account {account_id}: {reason}"
        )

    @property
    def account_id(self) -> str:
        return self._account_id

    @property
    def reason(self) -> str:
        return self._reason


class RepositoryAuthorizationDenied(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class RunRecord:
    id: int
    account_id: str
    trigger: str
    status: str
    grant_id: int | None
    policy_hash: str
    fencing_token: int
    counters: dict[str, Any]
    error: str
    started_at: str
    finished_at: str
    created_at: str

    @property
    def account_profile_id(self) -> str:
        return self.account_id

    @property
    def counters_json(self) -> dict[str, Any]:
        return _json_copy(self.counters)


@dataclass(frozen=True)
class ItemRecord:
    id: int
    origin_run_id: int
    last_run_id: int
    account_id: str
    vacancy_id: str
    resume_id: str
    state: AutopilotState
    retry_stage: RetryStage
    version: int
    active_attempt_id: int | None

    @property
    def account_profile_id(self) -> str:
        return self.account_id


@dataclass(frozen=True)
class LeaseRecord:
    account_id: str
    owner_token: str
    fencing_token: int
    expires_at: str
    updated_at: str

    @property
    def account_profile_id(self) -> str:
        return self.account_id


@dataclass(frozen=True)
class QuotaReservationRecord:
    id: int
    attempt_id: int | None
    run_id: int | None
    source: str
    remote_negotiation_id: str | None
    account_id: str
    timezone: str
    local_date: str
    state: QuotaReservationState
    fencing_token: int
    created_at: str
    resolved_at: str

    @property
    def account_profile_id(self) -> str:
        return self.account_id


@dataclass(frozen=True)
class ChallengeRecord:
    id: int
    scope: str
    challenge_type: str
    account_id: str
    item_id: int | None
    reservation_id: int | None
    sanitized_url: str
    screenshot_path: str
    status: str
    expires_at: str
    resolution_at: str
    resolution_actor: str
    resolution_action: str
    metadata: dict[str, Any]
    created_at: str

    @property
    def account_profile_id(self) -> str:
        return self.account_id

    @property
    def metadata_json(self) -> dict[str, Any]:
        return _json_copy(self.metadata)


@dataclass(frozen=True)
class GrantRecord:
    id: int
    account_id: str
    scope: str
    policy_hash: str
    generation: int
    active: bool
    actor: str
    source: str
    created_at: str
    revoked_at: str

    @property
    def account_profile_id(self) -> str:
        return self.account_id


@dataclass(frozen=True)
class ControlRecord:
    scope_type: str
    scope_id: str
    paused: bool
    kill_switch: bool
    version: int
    updated_at: str


@dataclass(frozen=True)
class AccountStateRecord:
    account_id: str
    blocked_until: str
    block_reason: str
    hh_reset: dict[str, Any]
    last_scheduled_at: str
    next_scheduled_at: str
    version: int
    updated_at: str

    @property
    def account_profile_id(self) -> str:
        return self.account_id

    @property
    def hh_reset_json(self) -> dict[str, Any]:
        return _json_copy(self.hh_reset)


@dataclass(frozen=True)
class AuthorizationReconciliationRecord:
    """Detached result; the mismatch mapping is a caller-mutable fresh snapshot."""

    mismatches: dict[str, str]
    revoked_accounts: tuple[str, ...]
    stopped_accounts: tuple[str, ...]


@dataclass(frozen=True)
class LiveAuthorizationSnapshot:
    grant: GrantRecord
    run: RunRecord


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_identifier(value: Any, *, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    normalized = value.strip().casefold()
    if not normalized:
        raise ValueError(f"{field} must not be empty")
    if "\0" in normalized:
        raise ValueError(f"{field} must not contain NUL")
    return normalized


def _required_text(value: Any, *, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} must not be empty")
    if "\0" in normalized:
        raise ValueError(f"{field} must not contain NUL")
    return normalized


def _optional_integer(value: Any, *, field: str) -> int | None:
    if value is None:
        return None
    return _integer(value, field=field)


def _integer(value: Any, *, field: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field} must be an integer")
    if value < minimum:
        raise ValueError(f"{field} must be at least {minimum}")
    return value


def _persisted_integer(
    value: Any,
    *,
    field: str,
    minimum: int = 0,
    error_type: type[RuntimeError] = StaleWrite,
) -> int:
    if type(value) is not int or value < minimum:
        raise error_type(f"{field} is not a valid persisted integer")
    return value


def _required_lastrowid(cursor: sqlite3.Cursor) -> int:
    lastrowid = cursor.lastrowid
    if lastrowid is None:
        raise RuntimeError("SQLite did not return a lastrowid")
    return lastrowid


def _enum_value(
    value: Any, enum_type: type[AutopilotState] | type[RetryStage], *, field: str
):
    if not isinstance(value, enum_type):
        raise TypeError(f"{field} must be {enum_type.__name__}")
    return value


def _json_dumps(value: dict[str, Any] | None, *, field: str) -> str:
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise TypeError(f"{field} must be a dictionary or None")
    return json.dumps(
        redact_for_storage(value),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _json_loads(value: Any, *, field: str) -> dict[str, Any]:
    try:
        loaded = json.loads(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid JSON in {field}") from exc
    if not isinstance(loaded, dict):
        raise ValueError(f"{field} must contain a JSON object")
    return loaded


def _json_copy(value: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _timestamp(value: datetime | str, *, field: str) -> str:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"{field} must not be empty")
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise ValueError(f"{field} must be a valid ISO timestamp") from exc
    else:
        raise TypeError(f"{field} must be a datetime or ISO timestamp string")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return parsed.astimezone(timezone.utc).isoformat()


def _instant(
    value: datetime | str | None,
    *,
    field: str,
) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    return datetime.fromisoformat(_timestamp(value, field=field))


def _timezone(value: Any) -> tuple[str, ZoneInfo]:
    name = _required_text(value, field="timezone_name")
    try:
        return name, ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown IANA timezone: {name}") from exc


def _lease_input(value: Any) -> tuple[str, str, int]:
    if not isinstance(value, LeaseRecord):
        raise TypeError("lease must be a LeaseRecord")
    _instant(value.expires_at, field="lease.expires_at")
    _instant(value.updated_at, field="lease.updated_at")
    return (
        _canonical_identifier(value.account_id, field="lease.account_id"),
        _required_text(value.owner_token, field="lease.owner_token"),
        _integer(value.fencing_token, field="lease.fencing_token", minimum=1),
    )


def _stored_lease_expiry(value: Any, *, account_id: str) -> datetime:
    try:
        return _instant(str(value), field="lease expiry")
    except (TypeError, ValueError) as exc:
        raise LostLease(f"account {account_id} lease expiry is invalid") from exc


def _stored_lease_owner(value: Any, *, account_id: str) -> str:
    if type(value) is not str:
        raise LostLease(f"account {account_id} lease owner is invalid")
    if value == LEASE_TOMBSTONE_OWNER:
        return value
    try:
        normalized = _required_text(value, field="lease owner")
    except (TypeError, ValueError) as exc:
        raise LostLease(f"account {account_id} lease owner is invalid") from exc
    if normalized != value:
        raise LostLease(f"account {account_id} lease owner is invalid")
    return value


def _stored_fencing_token(value: Any, *, account_id: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise LostLease(f"account {account_id} lease fencing token is invalid")
    return value


def _scope(value: Any) -> str:
    value = _required_text(value, field="scope")
    if value != APPLICATION_SCOPE:
        raise ValueError("scope must be applications")
    return value


def _control_identity(scope_type: Any, scope_id: Any) -> tuple[str, str]:
    scope_type = _required_text(scope_type, field="scope_type").casefold()
    if scope_type not in CONTROL_SCOPE_TYPES:
        raise ValueError("scope_type must be global or account")
    if scope_type == "global":
        supplied = _required_text(scope_id, field="scope_id").casefold()
        if supplied not in {GLOBAL_CONTROL_SCOPE_ID, "*"}:
            raise ValueError("global control scope_id must be global")
        return scope_type, GLOBAL_CONTROL_SCOPE_ID
    return scope_type, _canonical_identifier(scope_id, field="scope_id")


def _account_ids(values: Any, *, field: str = "account_ids") -> list[str]:
    if isinstance(values, (str, bytes)) or not isinstance(values, (list, tuple)):
        raise TypeError(f"{field} must be a list of account identifiers")
    canonical: list[str] = []
    seen: set[str] = set()
    for index, value in enumerate(values):
        account_id = _canonical_identifier(value, field=f"{field}[{index}]")
        if account_id in seen:
            raise ValueError(f"{field} contains a duplicate account identifier")
        seen.add(account_id)
        canonical.append(account_id)
    return canonical


def _authorization_projections(
    values: Any,
) -> dict[str, tuple[bool, int | None]]:
    if not isinstance(values, Mapping):
        raise TypeError("projections must be a mapping")
    projections: dict[str, tuple[bool, int | None]] = {}
    for raw_account_id, raw_projection in values.items():
        account_id = _canonical_identifier(
            raw_account_id, field="projection account_id"
        )
        if account_id in projections:
            raise ValueError("projections contains a duplicate account identifier")
        if not isinstance(raw_projection, tuple) or len(raw_projection) != 2:
            raise TypeError("projection must be an enabled/generation tuple")
        enabled, generation = raw_projection
        if type(enabled) is not bool:
            raise TypeError("projection enabled must be a boolean")
        if generation is not None:
            generation = _integer(generation, field="projection generation", minimum=1)
        projections[account_id] = (enabled, generation)
    return projections


def _authorization_annotations(
    values: Any,
    *,
    field: str,
    accounts: set[str],
) -> dict[str, str]:
    if values is None:
        return {}
    if not isinstance(values, Mapping):
        raise TypeError(f"{field} must be a mapping")
    annotations: dict[str, str] = {}
    for raw_account_id, raw_value in values.items():
        account_id = _canonical_identifier(raw_account_id, field=f"{field} account_id")
        if account_id in annotations:
            raise ValueError(f"{field} contains a duplicate account identifier")
        if account_id not in accounts:
            raise ValueError(f"{field} contains an account without a projection")
        annotations[account_id] = _required_text(raw_value, field=field)
    return annotations


def _exact_grant_expectations(
    values: Any,
) -> dict[str, tuple[int, str]]:
    if not isinstance(values, Mapping):
        raise TypeError("expectations must be a mapping")
    expectations: dict[str, tuple[int, str]] = {}
    for raw_account_id, raw_expectation in values.items():
        account_id = _canonical_identifier(
            raw_account_id, field="expectation account_id"
        )
        if account_id in expectations:
            raise ValueError("expectations contains a duplicate account identifier")
        if not isinstance(raw_expectation, tuple) or len(raw_expectation) != 2:
            raise TypeError("expectation must be a generation/policy_hash tuple")
        raw_generation, raw_policy_hash = raw_expectation
        generation = _integer(
            raw_generation,
            field="expectation generation",
            minimum=1,
        )
        policy_hash = _required_text(
            raw_policy_hash,
            field="expectation policy_hash",
        )
        expectations[account_id] = (generation, policy_hash)
    return expectations


class AutopilotRepository:
    def __init__(self, storage: Storage):
        if not isinstance(storage, Storage):
            raise TypeError("storage must be a Storage instance")
        self.storage = storage
        self.conn = storage.conn

    @contextmanager
    def immediate(self) -> Iterator[None]:
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise

    def create_run(
        self,
        account_id: str,
        *,
        trigger: str,
        policy_hash: str,
        grant_id: int | None = None,
        fencing_token: int = 0,
        counters: dict[str, Any] | None = None,
        status: str = "running",
    ) -> RunRecord:
        account_id = _canonical_identifier(account_id, field="account_id")
        if not isinstance(trigger, str):
            raise TypeError("trigger must be a string")
        if trigger not in RUN_TRIGGERS:
            raise ValueError(f"unsupported run trigger: {trigger}")
        if not isinstance(status, str):
            raise TypeError("status must be a string")
        if status not in INITIAL_RUN_STATUSES:
            raise ValueError(f"unsupported initial run status: {status}")
        policy_hash = _required_text(policy_hash, field="policy_hash")
        grant_id = _optional_integer(grant_id, field="grant_id")
        fencing_token = _integer(fencing_token, field="fencing_token")
        counters_json = _json_dumps(counters, field="counters")
        now = _utc_now()
        with self.immediate():
            cursor = self.conn.execute(
                """
                INSERT INTO hh_autopilot_runs (
                    account_profile_id, trigger, status, grant_id, policy_hash,
                    fencing_token, counters_json, started_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    trigger,
                    status,
                    grant_id,
                    policy_hash,
                    fencing_token,
                    counters_json,
                    now,
                    now,
                ),
            )
            return self._run_for_update(_required_lastrowid(cursor))

    def get_run(self, run_id: int) -> RunRecord | None:
        run_id = _integer(run_id, field="run_id", minimum=1)
        row = self.conn.execute(
            "SELECT * FROM hh_autopilot_runs WHERE id = ?", (run_id,)
        ).fetchone()
        return self._run_from_row(row) if row is not None else None

    def finish_run(
        self,
        run_id: int,
        *,
        status: str = "completed",
        counters: dict[str, Any] | None = None,
        error: str = "",
        fencing_token: int | None = None,
    ) -> RunRecord:
        run_id = _integer(run_id, field="run_id", minimum=1)
        if not isinstance(status, str):
            raise TypeError("status must be a string")
        if status not in TERMINAL_RUN_STATUSES:
            raise ValueError(f"unsupported terminal run status: {status}")
        if not isinstance(error, str):
            raise TypeError("error must be a string")
        if counters is not None:
            counters_json = _json_dumps(counters, field="counters")
        else:
            counters_json = None
        fencing_token = _optional_integer(fencing_token, field="fencing_token")
        fence_instant = (
            _instant(None, field="now") if fencing_token is not None else None
        )
        with self.immediate():
            current = self._run_for_update(run_id)
            if fencing_token is not None:
                assert fence_instant is not None
                self._assert_fence(current.account_id, fencing_token, fence_instant)
            self.conn.execute(
                """
                UPDATE hh_autopilot_runs
                SET status = ?, counters_json = COALESCE(?, counters_json),
                    error = ?, finished_at = ?
                WHERE id = ?
                """,
                (status, counters_json, error, _utc_now(), run_id),
            )
            return self._run_for_update(run_id)

    def create_item(
        self,
        origin_run_id: int,
        account_id: str,
        vacancy_id: str,
        resume_id: str,
        query_key: str,
    ) -> ItemRecord:
        origin_run_id = _integer(origin_run_id, field="origin_run_id", minimum=1)
        account_id = _canonical_identifier(account_id, field="account_id")
        vacancy_id = _required_text(vacancy_id, field="vacancy_id")
        resume_id = _canonical_identifier(resume_id, field="resume_id")
        query_key = _required_text(query_key, field="query_key")
        idempotency_key = hashlib.sha256(
            (account_id + "\0" + resume_id + "\0" + vacancy_id + "\0apply").encode(
                "utf-8"
            )
        ).hexdigest()
        now = _utc_now()
        with self.immediate():
            origin_run = self._run_for_update(origin_run_id)
            if origin_run.account_id != account_id:
                raise ValueError("item account_id must match its origin run")
            cursor = self.conn.execute(
                """
                INSERT INTO hh_autopilot_items (
                    origin_run_id, last_run_id, account_profile_id, vacancy_id,
                    resume_id, query_key, state, retry_stage, idempotency_key,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_profile_id, idempotency_key) DO NOTHING
                """,
                (
                    origin_run_id,
                    origin_run_id,
                    account_id,
                    vacancy_id,
                    resume_id,
                    query_key,
                    AutopilotState.DISCOVERED.value,
                    RetryStage.ELIGIBILITY.value,
                    idempotency_key,
                    now,
                    now,
                ),
            )
            if cursor.rowcount == 1:
                item_id = _required_lastrowid(cursor)
                self._insert_event(
                    item_id=item_id,
                    run_id=origin_run_id,
                    previous=None,
                    target=AutopilotState.DISCOVERED,
                    reason="discovered",
                    metadata={},
                )
                return self._item_for_update(item_id)
            row = self.conn.execute(
                """
                SELECT * FROM hh_autopilot_items
                WHERE account_profile_id = ? AND idempotency_key = ?
                """,
                (account_id, idempotency_key),
            ).fetchone()
            if row is None:
                raise StaleWrite("item idempotency collision could not be resolved")
            return self._item_from_row(row)

    def get_item(self, item_id: int) -> ItemRecord | None:
        item_id = _integer(item_id, field="item_id", minimum=1)
        row = self.conn.execute(
            "SELECT * FROM hh_autopilot_items WHERE id = ?", (item_id,)
        ).fetchone()
        return self._item_from_row(row) if row is not None else None

    def transition_item(
        self,
        item_id: int,
        expected_version: int,
        target: AutopilotState,
        reason: str,
        metadata: dict[str, Any] | None = None,
        *,
        run_id: int | None = None,
        fencing_token: int | None = None,
    ) -> ItemRecord:
        item_id = _integer(item_id, field="item_id", minimum=1)
        expected_version = _integer(expected_version, field="expected_version")
        target = _enum_value(target, AutopilotState, field="target")
        reason = _required_text(reason, field="reason")
        metadata_json = _json_dumps(metadata, field="metadata")
        run_id = _optional_integer(run_id, field="run_id")
        if run_id == 0:
            raise ValueError("run_id must be at least 1")
        fencing_token = _optional_integer(fencing_token, field="fencing_token")
        fence_instant = _instant(None, field="now")
        sanitized_metadata = _json_loads(metadata_json, field="metadata")
        with self.immediate():
            return self._transition_item_for_update(
                item_id,
                expected_version,
                target,
                reason,
                sanitized_metadata,
                run_id=run_id,
                fencing_token=fencing_token,
                fence_instant=fence_instant,
            )

    def _transition_item_for_update(
        self,
        item_id: int,
        expected_version: int,
        target: AutopilotState,
        reason: str,
        metadata: dict[str, Any],
        *,
        run_id: int | None,
        fencing_token: int | None,
        fence_instant: datetime,
    ) -> ItemRecord:
        """Apply a prevalidated item transition inside the caller's transaction."""
        current = self._item_for_update(item_id)
        if current.version != expected_version:
            raise StaleWrite(f"item {item_id} version changed")
        if fencing_token is not None:
            self._assert_fence(current.account_id, fencing_token, fence_instant)
        if run_id is not None:
            run = self._run_for_update(run_id)
            if run.account_id != current.account_id:
                raise ValueError("transition run and item accounts must match")
        assert_transition(current.state, target)
        cursor = self.conn.execute(
            """
            UPDATE hh_autopilot_items
            SET state = ?, version = version + 1,
                last_run_id = COALESCE(?, last_run_id),
                last_outcome_code = ?, updated_at = ?
            WHERE id = ? AND version = ?
            """,
            (
                target.value,
                run_id,
                reason,
                fence_instant.isoformat(),
                item_id,
                expected_version,
            ),
        )
        if cursor.rowcount != 1:
            raise StaleWrite(f"item {item_id} compare-and-swap failed")
        self._insert_event(
            item_id=item_id,
            run_id=run_id,
            previous=current.state,
            target=target,
            reason=reason,
            metadata=metadata,
            created_at=fence_instant,
        )
        return self._item_for_update(item_id)

    def append_event(
        self,
        item_id: int,
        reason: str,
        metadata: dict[str, Any] | None = None,
        *,
        run_id: int | None = None,
        fencing_token: int | None = None,
    ) -> dict[str, Any]:
        item_id = _integer(item_id, field="item_id", minimum=1)
        reason = _required_text(reason, field="reason")
        metadata_json = _json_dumps(metadata, field="metadata")
        run_id = _optional_integer(run_id, field="run_id")
        if run_id == 0:
            raise ValueError("run_id must be at least 1")
        fencing_token = _optional_integer(fencing_token, field="fencing_token")
        fence_instant = (
            _instant(None, field="now") if fencing_token is not None else None
        )
        with self.immediate():
            current = self._item_for_update(item_id)
            if fencing_token is not None:
                assert fence_instant is not None
                self._assert_fence(current.account_id, fencing_token, fence_instant)
            if run_id is not None:
                run = self._run_for_update(run_id)
                if run.account_id != current.account_id:
                    raise ValueError("event run and item accounts must match")
            event_id = self._insert_event(
                item_id=item_id,
                run_id=run_id,
                previous=current.state,
                target=current.state,
                reason=reason,
                metadata=_json_loads(metadata_json, field="metadata"),
            )
            row = self.conn.execute(
                "SELECT * FROM hh_autopilot_events WHERE id = ?", (event_id,)
            ).fetchone()
            if row is None:
                raise StaleWrite(f"event {event_id} disappeared")
            return self._event_from_row(row)

    def list_events(self, item_id: int) -> list[dict[str, Any]]:
        item_id = _integer(item_id, field="item_id", minimum=1)
        rows = self.conn.execute(
            "SELECT * FROM hh_autopilot_events WHERE item_id = ? ORDER BY id ASC",
            (item_id,),
        ).fetchall()
        return [self._event_from_row(row) for row in rows]

    def list_due_items(
        self,
        account_id: str,
        *,
        states: Iterable[AutopilotState],
        now: datetime | str,
        limit: int,
    ) -> list[ItemRecord]:
        account_id = _canonical_identifier(account_id, field="account_id")
        if isinstance(states, (str, bytes)):
            raise TypeError("states must be an iterable of AutopilotState")
        try:
            state_values = tuple(
                sorted(
                    {
                        _enum_value(state, AutopilotState, field="states").value
                        for state in states
                    }
                )
            )
        except TypeError:
            raise
        if not state_values:
            return []
        now_value = _timestamp(now, field="now")
        limit = _integer(limit, field="limit")
        placeholders = ",".join("?" for _ in state_values)
        rows = self.conn.execute(
            f"""
            SELECT * FROM hh_autopilot_items
            WHERE account_profile_id = ?
              AND state IN ({placeholders})
              AND next_attempt_at <= ?
            ORDER BY next_attempt_at ASC, id ASC
            LIMIT ?
            """,
            (account_id, *state_values, now_value, limit),
        ).fetchall()
        return [self._item_from_row(row) for row in rows]

    def recover_stale_applying(
        self,
        account_id: str,
        fencing_token: int,
        *,
        run_id: int | None = None,
        now: datetime | str | None = None,
    ) -> list[ItemRecord]:
        account_id = _canonical_identifier(account_id, field="account_id")
        fencing_token = _integer(fencing_token, field="fencing_token", minimum=1)
        run_id = _optional_integer(run_id, field="run_id")
        if run_id == 0:
            raise ValueError("run_id must be at least 1")
        instant = _instant(now, field="now")

        with self.immediate():
            self._assert_fence(account_id, fencing_token, instant)
            if run_id is not None:
                run = self._run_for_update(run_id)
                if run.account_id != account_id:
                    raise ValueError("recovery run account does not match")
                if run.fencing_token != fencing_token:
                    raise LostLease(f"recovery run {run_id} fencing token is stale")
            rows = self.conn.execute(
                """
                SELECT * FROM hh_autopilot_items
                WHERE account_profile_id = ? AND state = 'applying'
                ORDER BY id ASC
                """,
                (account_id,),
            ).fetchall()
            recovered_ids: list[int] = []
            for row in rows:
                current = self._item_from_row(row)
                assert_transition(
                    current.state,
                    AutopilotState.RECONCILING,
                )
                cursor = self.conn.execute(
                    """
                    UPDATE hh_autopilot_items
                    SET state = 'reconciling', version = version + 1,
                        last_run_id = COALESCE(?, last_run_id),
                        last_outcome_code = 'stale_applying_recovered',
                        updated_at = ?
                    WHERE id = ? AND version = ? AND state = 'applying'
                    """,
                    (
                        run_id,
                        instant.isoformat(),
                        current.id,
                        current.version,
                    ),
                )
                if cursor.rowcount != 1:
                    raise StaleWrite(
                        f"item {current.id} recovery compare-and-swap failed"
                    )
                self._insert_event(
                    item_id=current.id,
                    run_id=run_id,
                    previous=AutopilotState.APPLYING,
                    target=AutopilotState.RECONCILING,
                    reason="stale_applying_recovered",
                    metadata={"fence": fencing_token},
                    created_at=instant,
                )
                recovered_ids.append(current.id)
            return [self._item_for_update(item_id) for item_id in recovered_ids]

    def create_grants(
        self,
        requests: list[tuple[str, str, str, str]],
    ) -> dict[str, int]:
        """Create every requested application grant in one transaction."""
        if not isinstance(requests, list):
            raise TypeError("requests must be a list")
        validated: list[tuple[str, str, str, str]] = []
        seen: set[str] = set()
        for index, request in enumerate(requests):
            if not isinstance(request, tuple) or len(request) != 4:
                raise TypeError(
                    f"requests[{index}] must be an account/policy/actor/source tuple"
                )
            account_id = _canonical_identifier(
                request[0], field=f"requests[{index}].account_id"
            )
            if account_id in seen:
                raise ValueError("requests contains a duplicate account identifier")
            seen.add(account_id)
            validated.append(
                (
                    account_id,
                    _required_text(request[1], field=f"requests[{index}].policy_hash"),
                    _required_text(request[2], field=f"requests[{index}].actor"),
                    _required_text(request[3], field=f"requests[{index}].source"),
                )
            )
        if not validated:
            return {}

        with self.immediate():
            for account_id, _, _, _ in validated:
                if self._kill_switch_active_for_update(account_id):
                    raise KillSwitchActive(
                        f"kill switch is active for account {account_id}"
                    )
            generations: dict[str, int] = {}
            for account_id, policy_hash, actor, source in validated:
                row = self.conn.execute(
                    """
                    SELECT COALESCE(MAX(generation), 0) AS generation
                    FROM hh_autopilot_grants
                    WHERE account_profile_id = ? AND scope = ?
                    """,
                    (account_id, APPLICATION_SCOPE),
                ).fetchone()
                generation = int(row["generation"]) + 1
                now = _utc_now()
                self.conn.execute(
                    """
                    UPDATE hh_autopilot_grants
                    SET active = 0, revoked_at = ?
                    WHERE account_profile_id = ? AND scope = ? AND active = 1
                    """,
                    (now, account_id, APPLICATION_SCOPE),
                )
                self.conn.execute(
                    """
                    INSERT INTO hh_autopilot_grants (
                        account_profile_id, scope, policy_hash, generation,
                        active, actor, source, created_at
                    ) VALUES (?, ?, ?, ?, 1, ?, ?, ?)
                    """,
                    (
                        account_id,
                        APPLICATION_SCOPE,
                        policy_hash,
                        generation,
                        actor,
                        source,
                        now,
                    ),
                )
                generations[account_id] = generation
            return generations

    def active_grant(
        self, account_id: str, scope: str = APPLICATION_SCOPE
    ) -> GrantRecord | None:
        account_id = _canonical_identifier(account_id, field="account_id")
        scope = _scope(scope)
        return self._active_grant_for_update(account_id, scope=scope)

    def _active_grant_for_update(
        self,
        account_id: str,
        *,
        scope: str = APPLICATION_SCOPE,
    ) -> GrantRecord | None:
        row = self.conn.execute(
            """
            SELECT * FROM hh_autopilot_grants
            WHERE account_profile_id = ? AND scope = ? AND active = 1
            """,
            (account_id, scope),
        ).fetchone()
        return self._grant_from_row(row) if row is not None else None

    def list_active_grants(self, scope: str = APPLICATION_SCOPE) -> list[GrantRecord]:
        scope = _scope(scope)
        rows = self.conn.execute(
            """
            SELECT * FROM hh_autopilot_grants
            WHERE scope = ? AND active = 1
            ORDER BY account_profile_id ASC, id ASC
            """,
            (scope,),
        ).fetchall()
        return [self._grant_from_row(row) for row in rows]

    def validate_exact_active_grants(
        self,
        expectations: Mapping[str, tuple[int, str]],
    ) -> None:
        """Atomically validate every selected active generation and policy hash."""
        validated = _exact_grant_expectations(expectations)
        if not validated:
            return
        with self.immediate():
            for account_id, (generation, policy_hash) in validated.items():
                grant = self._active_grant_for_update(account_id)
                if grant is None or grant.generation != generation:
                    raise RepositoryAuthorizationDenied("authorization_state_mismatch")
                if grant.policy_hash != policy_hash:
                    raise RepositoryAuthorizationDenied("policy_hash_mismatch")

    def reconcile_authorization_projection(
        self,
        projections: Mapping[str, tuple[bool, int | None]],
        *,
        actor: str,
        reason: str,
        policy_hashes: Mapping[str, str] | None = None,
        policy_errors: Mapping[str, str] | None = None,
    ) -> AuthorizationReconciliationRecord:
        """Atomically reconcile current grants/runs to an authoritative projection.

        Policy annotations are optional so startup reconciliation never depends on
        an AI/model/material resolver. All active grants are re-read only after
        ``BEGIN IMMEDIATE`` and current generations are revoked in that transaction.
        """
        validated = _authorization_projections(projections)
        actor = _required_text(actor, field="actor")
        reason = _required_text(reason, field="reason")
        del actor, reason  # The exact Task 4 schema has no revocation audit columns.
        account_ids = set(validated)
        hashes = _authorization_annotations(
            policy_hashes,
            field="policy_hashes",
            accounts=account_ids,
        )
        errors = _authorization_annotations(
            policy_errors,
            field="policy_errors",
            accounts=account_ids,
        )

        with self.immediate():
            grant_rows = self.conn.execute(
                """
                SELECT * FROM hh_autopilot_grants
                WHERE scope = ? AND active = 1
                ORDER BY account_profile_id ASC, id ASC
                """,
                (APPLICATION_SCOPE,),
            ).fetchall()
            grants = {
                str(row["account_profile_id"]): self._grant_from_row(row)
                for row in grant_rows
            }
            running_rows = self.conn.execute(
                """
                SELECT DISTINCT account_profile_id
                FROM hh_autopilot_runs
                WHERE status = 'running'
                ORDER BY account_profile_id ASC
                """
            ).fetchall()
            running_accounts = {str(row["account_profile_id"]) for row in running_rows}

            mismatches: dict[str, str] = {}
            blocked: set[str] = set()
            for account_id, (enabled, generation) in validated.items():
                grant = grants.get(account_id)
                if not enabled:
                    blocked.add(account_id)
                    continue
                if grant is None or generation != grant.generation:
                    mismatches[account_id] = "authorization_state_mismatch"
                    blocked.add(account_id)
                    continue
                if account_id in errors:
                    mismatches[account_id] = errors[account_id]
                    blocked.add(account_id)
                    continue
                expected_hash = hashes.get(account_id)
                if expected_hash is not None and expected_hash != grant.policy_hash:
                    mismatches[account_id] = "policy_hash_mismatch"
                    blocked.add(account_id)

            known_runtime_accounts = set(grants) | running_accounts
            blocked.update(known_runtime_accounts.difference(validated))
            revoked_accounts = tuple(
                account_id for account_id in grants if account_id in blocked
            )
            stopped_accounts = tuple(
                sorted(
                    account_id
                    for account_id in running_accounts
                    if account_id in blocked
                )
            )
            if revoked_accounts:
                placeholders = ",".join("?" for _ in revoked_accounts)
                self.conn.execute(
                    f"""
                    UPDATE hh_autopilot_grants
                    SET active = 0, revoked_at = ?
                    WHERE scope = ? AND active = 1
                      AND account_profile_id IN ({placeholders})
                    """,
                    (_utc_now(), APPLICATION_SCOPE, *revoked_accounts),
                )
            if blocked:
                self._request_stop(sorted(blocked))
            return AuthorizationReconciliationRecord(
                mismatches=dict(mismatches),
                revoked_accounts=revoked_accounts,
                stopped_accounts=stopped_accounts,
            )

    def validate_live_authorization_snapshot(
        self,
        account_id: str,
        *,
        generation: int,
        policy_hash: str,
        run_id: int,
        fencing_token: int,
    ) -> LiveAuthorizationSnapshot:
        """Validate the mutable DB authorization state in one transaction.

        Task 9 must call this again at the final pre-dispatch boundary because a
        snapshot can be revoked immediately after this method returns.
        """
        account_id = _canonical_identifier(account_id, field="account_id")
        generation = _integer(generation, field="generation", minimum=1)
        policy_hash = _required_text(policy_hash, field="policy_hash")
        run_id = _integer(run_id, field="run_id", minimum=1)
        fencing_token = _integer(fencing_token, field="fencing_token")
        with self.immediate():
            grant = self._active_grant_for_update(account_id)
            if grant is None or grant.generation != generation:
                raise RepositoryAuthorizationDenied("authorization_state_mismatch")
            if grant.policy_hash != policy_hash:
                raise RepositoryAuthorizationDenied("policy_hash_mismatch")
            if self._pause_active_for_update(account_id):
                raise RepositoryAuthorizationDenied("autopilot_disabled_or_paused")
            if self._kill_switch_active_for_update(account_id):
                raise RepositoryAuthorizationDenied("kill_switch_active")
            row = self.conn.execute(
                "SELECT * FROM hh_autopilot_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise RepositoryAuthorizationDenied("run_not_found")
            run = self._run_from_row(row)
            if run.account_id != account_id:
                raise RepositoryAuthorizationDenied("run_account_mismatch")
            if run.status != "running":
                raise RepositoryAuthorizationDenied("run_not_active")
            if run.policy_hash != grant.policy_hash:
                raise RepositoryAuthorizationDenied("run_policy_mismatch")
            if run.grant_id is not None and run.grant_id != grant.id:
                raise RepositoryAuthorizationDenied("run_grant_mismatch")
            if run.fencing_token != fencing_token:
                raise RepositoryAuthorizationDenied("run_fencing_token_mismatch")
            return LiveAuthorizationSnapshot(grant=grant, run=run)

    def revoke_grants(
        self,
        account_ids: list[str],
        *,
        actor: str,
        reason: str,
    ) -> None:
        account_ids = _account_ids(account_ids)
        _required_text(actor, field="actor")
        _required_text(reason, field="reason")
        if not account_ids:
            return
        with self.immediate():
            self._revoke_accounts(account_ids, _utc_now())

    def revoke_exact_generations(
        self,
        generations: dict[str, int],
        *,
        actor: str,
        reason: str,
    ) -> None:
        if not isinstance(generations, dict):
            raise TypeError("generations must be a dictionary")
        _required_text(actor, field="actor")
        _required_text(reason, field="reason")
        validated: list[tuple[str, int]] = []
        seen: set[str] = set()
        for raw_account_id, raw_generation in generations.items():
            account_id = _canonical_identifier(raw_account_id, field="account_id")
            if account_id in seen:
                raise ValueError("generations contains a duplicate account identifier")
            seen.add(account_id)
            validated.append(
                (
                    account_id,
                    _integer(raw_generation, field="generation", minimum=1),
                )
            )
        if not validated:
            return
        with self.immediate():
            now = _utc_now()
            self.conn.executemany(
                """
                UPDATE hh_autopilot_grants
                SET active = 0, revoked_at = ?
                WHERE account_profile_id = ? AND scope = ?
                  AND generation = ? AND active = 1
                """,
                [
                    (now, account_id, APPLICATION_SCOPE, generation)
                    for account_id, generation in validated
                ],
            )

    def disable_accounts(
        self,
        account_ids: list[str],
        *,
        actor: str,
        reason: str = "disabled",
    ) -> None:
        account_ids = _account_ids(account_ids)
        _required_text(actor, field="actor")
        _required_text(reason, field="reason")
        if not account_ids:
            return
        with self.immediate():
            self._revoke_accounts(account_ids, _utc_now())
            self._request_stop(account_ids)

    def request_stop(self, account_ids: list[str]) -> None:
        account_ids = _account_ids(account_ids)
        if not account_ids:
            return
        with self.immediate():
            self._request_stop(account_ids)

    def run_stop_requested(self, run_id: int) -> bool:
        run_id = _integer(run_id, field="run_id", minimum=1)
        row = self.conn.execute(
            "SELECT status FROM hh_autopilot_runs WHERE id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"run {run_id} does not exist")
        return str(row["status"]) == "stop_requested"

    def get_account_state(self, account_id: str) -> AccountStateRecord | None:
        account_id = _canonical_identifier(account_id, field="account_id")
        row = self.conn.execute(
            """
            SELECT * FROM hh_autopilot_account_state
            WHERE account_profile_id = ?
            """,
            (account_id,),
        ).fetchone()
        return self._account_state_from_row(row) if row is not None else None

    def set_account_cooldown(
        self,
        account_id: str,
        blocked_until: datetime | str,
        reason: str,
        fencing_token: int,
        *,
        hh_reset: dict[str, Any] | None = None,
        now: datetime | str | None = None,
    ) -> AccountStateRecord:
        account_id = _canonical_identifier(account_id, field="account_id")
        blocked_until_instant = _instant(
            blocked_until,
            field="blocked_until",
        )
        reason = _required_text(reason, field="reason")
        fencing_token = _integer(fencing_token, field="fencing_token", minimum=1)
        hh_reset_json = _json_dumps(hh_reset, field="hh_reset")
        instant = _instant(now, field="now")

        with self.immediate():
            return self._set_account_cooldown_for_update(
                account_id=account_id,
                blocked_until=blocked_until_instant,
                reason=reason,
                fencing_token=fencing_token,
                hh_reset_json=hh_reset_json,
                instant=instant,
            )

    def _set_account_cooldown_for_update(
        self,
        *,
        account_id: str,
        blocked_until: datetime,
        reason: str,
        fencing_token: int,
        hh_reset_json: str,
        instant: datetime,
    ) -> AccountStateRecord:
        """Set a prevalidated cooldown in the caller's transaction."""
        self._assert_fence(account_id, fencing_token, instant)
        self.conn.execute(
            """
            INSERT INTO hh_autopilot_account_state (
                account_profile_id, blocked_until, block_reason,
                hh_reset_json, version, updated_at
            ) VALUES (?, ?, ?, ?, 0, ?)
            ON CONFLICT(account_profile_id) DO UPDATE SET
                blocked_until = excluded.blocked_until,
                block_reason = excluded.block_reason,
                hh_reset_json = excluded.hh_reset_json,
                version = hh_autopilot_account_state.version + 1,
                updated_at = excluded.updated_at
            """,
            (
                account_id,
                blocked_until.isoformat(),
                reason,
                hh_reset_json,
                instant.isoformat(),
            ),
        )
        row = self.conn.execute(
            """
            SELECT * FROM hh_autopilot_account_state
            WHERE account_profile_id = ?
            """,
            (account_id,),
        ).fetchone()
        if row is None:
            raise StaleWrite("account cooldown disappeared")
        return self._account_state_from_row(row)

    def assert_dispatch_available(
        self,
        account_id: str,
        fencing_token: int,
        *,
        now: datetime | str | None = None,
    ) -> None:
        account_id = _canonical_identifier(account_id, field="account_id")
        fencing_token = _integer(fencing_token, field="fencing_token", minimum=1)
        instant = _instant(now, field="now")
        with self.immediate():
            self._assert_fence(account_id, fencing_token, instant)
            self._assert_cooldown_for_update(account_id, instant)

    def assert_timezone_change_safe(self, account_id: str) -> None:
        account_id = _canonical_identifier(account_id, field="account_id")
        with self.immediate():
            grant = self.conn.execute(
                """
                SELECT 1 FROM hh_autopilot_grants
                WHERE account_profile_id = ? AND scope = ? AND active = 1
                LIMIT 1
                """,
                (account_id, APPLICATION_SCOPE),
            ).fetchone()
            if grant is not None:
                raise TimezoneChangeUnsafe(account_id, "active_grant")
            unresolved = self.conn.execute(
                """
                SELECT 1 FROM hh_autopilot_quota_reservations
                WHERE account_profile_id = ? AND state IN ('reserved', 'held')
                LIMIT 1
                """,
                (account_id,),
            ).fetchone()
            if unresolved is not None:
                raise TimezoneChangeUnsafe(
                    account_id,
                    "unresolved_reservation",
                )

    def get_control(self, scope_type: str, scope_id: str) -> ControlRecord | None:
        scope_type, scope_id = _control_identity(scope_type, scope_id)
        row = self.conn.execute(
            """
            SELECT * FROM hh_autopilot_controls
            WHERE scope_type = ? AND scope_id = ?
            """,
            (scope_type, scope_id),
        ).fetchone()
        return self._control_from_row(row) if row is not None else None

    def set_pause(self, scope_type: str, scope_id: str, paused: bool) -> ControlRecord:
        scope_type, scope_id = _control_identity(scope_type, scope_id)
        if type(paused) is not bool:
            raise TypeError("paused must be a boolean")
        with self.immediate():
            self._upsert_control(
                scope_type,
                scope_id,
                paused=paused,
                kill_switch=None,
            )
            return self._control_for_update(scope_type, scope_id)

    def set_kill_switch(
        self,
        scope_type: str,
        scope_id: str,
        *,
        actor: str,
    ) -> ControlRecord:
        scope_type, scope_id = _control_identity(scope_type, scope_id)
        _required_text(actor, field="actor")
        with self.immediate():
            self._upsert_control(
                scope_type,
                scope_id,
                paused=None,
                kill_switch=True,
            )
            if scope_type == "global":
                self.conn.execute(
                    """
                    UPDATE hh_autopilot_grants
                    SET active = 0, revoked_at = ?
                    WHERE scope = ? AND active = 1
                    """,
                    (_utc_now(), APPLICATION_SCOPE),
                )
                self.conn.execute(
                    """
                    UPDATE hh_autopilot_runs
                    SET status = 'stop_requested'
                    WHERE status = 'running'
                    """
                )
            else:
                self._revoke_accounts([scope_id], _utc_now())
                self._request_stop([scope_id])
            return self._control_for_update(scope_type, scope_id)

    def clear_kill_switch(
        self,
        scope_type: str,
        scope_id: str,
        *,
        actor: str,
    ) -> ControlRecord:
        scope_type, scope_id = _control_identity(scope_type, scope_id)
        _required_text(actor, field="actor")
        with self.immediate():
            self._upsert_control(
                scope_type,
                scope_id,
                paused=None,
                kill_switch=False,
            )
            return self._control_for_update(scope_type, scope_id)

    def pause_active(self, account_id: str) -> bool:
        account_id = _canonical_identifier(account_id, field="account_id")
        return self._pause_active_for_update(account_id)

    def _pause_active_for_update(self, account_id: str) -> bool:
        row = self.conn.execute(
            """
            SELECT 1 FROM hh_autopilot_controls
            WHERE paused = 1 AND (
                (scope_type = 'global' AND scope_id = ?)
                OR (scope_type = 'account' AND scope_id = ?)
            )
            LIMIT 1
            """,
            (GLOBAL_CONTROL_SCOPE_ID, account_id),
        ).fetchone()
        return row is not None

    def kill_switch_active(self, account_id: str) -> bool:
        account_id = _canonical_identifier(account_id, field="account_id")
        return self._kill_switch_active_for_update(account_id)

    def _kill_switch_active_for_update(self, account_id: str) -> bool:
        row = self.conn.execute(
            """
            SELECT 1 FROM hh_autopilot_controls
            WHERE kill_switch = 1 AND (
                (scope_type = 'global' AND scope_id = ?)
                OR (scope_type = 'account' AND scope_id = ?)
            )
            LIMIT 1
            """,
            (GLOBAL_CONTROL_SCOPE_ID, account_id),
        ).fetchone()
        return row is not None

    def _revoke_accounts(self, account_ids: list[str], revoked_at: str) -> None:
        placeholders = ",".join("?" for _ in account_ids)
        self.conn.execute(
            f"""
            UPDATE hh_autopilot_grants
            SET active = 0, revoked_at = ?
            WHERE account_profile_id IN ({placeholders})
              AND scope = ? AND active = 1
            """,
            (revoked_at, *account_ids, APPLICATION_SCOPE),
        )

    def _request_stop(self, account_ids: list[str]) -> None:
        placeholders = ",".join("?" for _ in account_ids)
        self.conn.execute(
            f"""
            UPDATE hh_autopilot_runs
            SET status = 'stop_requested'
            WHERE account_profile_id IN ({placeholders}) AND status = 'running'
            """,
            tuple(account_ids),
        )

    def _upsert_control(
        self,
        scope_type: str,
        scope_id: str,
        *,
        paused: bool | None,
        kill_switch: bool | None,
    ) -> None:
        current = self.conn.execute(
            """
            SELECT paused, kill_switch FROM hh_autopilot_controls
            WHERE scope_type = ? AND scope_id = ?
            """,
            (scope_type, scope_id),
        ).fetchone()
        current_paused = bool(current["paused"]) if current is not None else False
        current_kill = bool(current["kill_switch"]) if current is not None else False
        self.conn.execute(
            """
            INSERT INTO hh_autopilot_controls (
                scope_type, scope_id, paused, kill_switch, version, updated_at
            ) VALUES (?, ?, ?, ?, 0, ?)
            ON CONFLICT(scope_type, scope_id) DO UPDATE SET
                paused = excluded.paused,
                kill_switch = excluded.kill_switch,
                version = hh_autopilot_controls.version + 1,
                updated_at = excluded.updated_at
            """,
            (
                scope_type,
                scope_id,
                int(current_paused if paused is None else paused),
                int(current_kill if kill_switch is None else kill_switch),
                _utc_now(),
            ),
        )

    def _control_for_update(self, scope_type: str, scope_id: str) -> ControlRecord:
        row = self.conn.execute(
            """
            SELECT * FROM hh_autopilot_controls
            WHERE scope_type = ? AND scope_id = ?
            """,
            (scope_type, scope_id),
        ).fetchone()
        if row is None:
            raise StaleWrite("control row disappeared")
        return self._control_from_row(row)

    def acquire_lease(
        self,
        account_id: str,
        owner_token: str,
        *,
        ttl_seconds: int,
        now: datetime | str | None = None,
    ) -> LeaseRecord | None:
        account_id = _canonical_identifier(account_id, field="account_id")
        owner_token = _required_text(owner_token, field="owner_token")
        ttl_seconds = _integer(ttl_seconds, field="ttl_seconds", minimum=1)
        instant = _instant(now, field="now")
        requested_expiry = instant + timedelta(seconds=ttl_seconds)

        with self.immediate():
            row = self.conn.execute(
                """
                SELECT * FROM hh_autopilot_leases
                WHERE account_profile_id = ?
                """,
                (account_id,),
            ).fetchone()
            if row is None:
                fencing_token = 1
                expires_at = requested_expiry
            else:
                current_owner = _stored_lease_owner(
                    row["owner_token"], account_id=account_id
                )
                current_expiry = _stored_lease_expiry(
                    row["expires_at"], account_id=account_id
                )
                current_token = _stored_fencing_token(
                    row["fencing_token"], account_id=account_id
                )
                if current_owner == LEASE_TOMBSTONE_OWNER:
                    if row["expires_at"] != LEASE_TOMBSTONE_EXPIRY:
                        raise LostLease(
                            f"account {account_id} lease tombstone is invalid"
                        )
                    fencing_token = current_token + 1
                    expires_at = requested_expiry
                elif current_expiry > instant:
                    if current_owner != owner_token:
                        return None
                    fencing_token = current_token
                    expires_at = max(current_expiry, requested_expiry)
                else:
                    fencing_token = current_token + 1
                    expires_at = requested_expiry

            timestamp = instant.isoformat()
            self.conn.execute(
                """
                INSERT INTO hh_autopilot_leases (
                    account_profile_id, owner_token, fencing_token,
                    expires_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(account_profile_id) DO UPDATE SET
                    owner_token = excluded.owner_token,
                    fencing_token = excluded.fencing_token,
                    expires_at = excluded.expires_at,
                    updated_at = excluded.updated_at
                """,
                (
                    account_id,
                    owner_token,
                    fencing_token,
                    expires_at.isoformat(),
                    timestamp,
                ),
            )
            return self._lease_for_update(account_id)

    def renew_lease(
        self,
        lease: LeaseRecord,
        *,
        ttl_seconds: int,
        now: datetime | str | None = None,
    ) -> LeaseRecord:
        account_id, owner_token, fencing_token = _lease_input(lease)
        ttl_seconds = _integer(ttl_seconds, field="ttl_seconds", minimum=1)
        instant = _instant(now, field="now")
        requested_expiry = instant + timedelta(seconds=ttl_seconds)

        with self.immediate():
            row = self.conn.execute(
                """
                SELECT * FROM hh_autopilot_leases
                WHERE account_profile_id = ?
                """,
                (account_id,),
            ).fetchone()
            if row is None:
                raise LostLease(f"account {account_id} lease was lost")
            current_expiry = _stored_lease_expiry(
                row["expires_at"], account_id=account_id
            )
            current_owner = _stored_lease_owner(
                row["owner_token"], account_id=account_id
            )
            current_token = _stored_fencing_token(
                row["fencing_token"], account_id=account_id
            )
            if (
                current_owner == LEASE_TOMBSTONE_OWNER
                or current_owner != owner_token
                or current_token != fencing_token
                or current_expiry <= instant
            ):
                raise LostLease(f"account {account_id} lease was lost")
            expires_at = max(current_expiry, requested_expiry)
            cursor = self.conn.execute(
                """
                UPDATE hh_autopilot_leases
                SET expires_at = ?, updated_at = ?
                WHERE account_profile_id = ? AND owner_token = ?
                  AND fencing_token = ?
                """,
                (
                    expires_at.isoformat(),
                    instant.isoformat(),
                    account_id,
                    owner_token,
                    fencing_token,
                ),
            )
            if cursor.rowcount != 1:
                raise LostLease(f"account {account_id} lease was lost")
            return self._lease_for_update(account_id)

    def release_lease(self, lease: LeaseRecord) -> bool:
        account_id, owner_token, fencing_token = _lease_input(lease)
        with self.immediate():
            cursor = self.conn.execute(
                """
                UPDATE hh_autopilot_leases
                SET owner_token = ?, expires_at = ?, updated_at = ?
                WHERE account_profile_id = ? AND owner_token = ?
                  AND fencing_token = ?
                """,
                (
                    LEASE_TOMBSTONE_OWNER,
                    LEASE_TOMBSTONE_EXPIRY,
                    _utc_now(),
                    account_id,
                    owner_token,
                    fencing_token,
                ),
            )
            return cursor.rowcount == 1

    def assert_fence(
        self,
        account_id: str,
        fencing_token: int,
        *,
        now: datetime | str | None = None,
    ) -> None:
        account_id = _canonical_identifier(account_id, field="account_id")
        fencing_token = _integer(fencing_token, field="fencing_token", minimum=1)
        instant = _instant(now, field="now")
        with self.immediate():
            self._assert_fence(account_id, fencing_token, instant)

    def reserve_quota(
        self,
        account_id: str,
        run_id: int,
        attempt_id: int,
        timezone_name: str,
        daily_limit: int,
        run_limit: int,
        fencing_token: int,
        now: datetime | str | None = None,
    ) -> QuotaReservationRecord:
        account_id = _canonical_identifier(account_id, field="account_id")
        run_id = _integer(run_id, field="run_id", minimum=1)
        attempt_id = _integer(attempt_id, field="attempt_id", minimum=1)
        timezone_name, local_timezone = _timezone(timezone_name)
        daily_limit = _integer(daily_limit, field="daily_limit", minimum=1)
        run_limit = _integer(run_limit, field="run_limit", minimum=1)
        fencing_token = _integer(fencing_token, field="fencing_token", minimum=1)
        instant = _instant(now, field="now")
        local_date = instant.astimezone(local_timezone).date().isoformat()

        with self.immediate():
            return self._reserve_quota_for_update(
                account_id=account_id,
                run_id=run_id,
                attempt_id=attempt_id,
                timezone_name=timezone_name,
                local_date=local_date,
                daily_limit=daily_limit,
                run_limit=run_limit,
                fencing_token=fencing_token,
                instant=instant,
            )

    def _reserve_quota_for_update(
        self,
        *,
        account_id: str,
        run_id: int,
        attempt_id: int,
        timezone_name: str,
        local_date: str,
        daily_limit: int,
        run_limit: int,
        fencing_token: int,
        instant: datetime,
    ) -> QuotaReservationRecord:
        """Reserve quota from prevalidated values in the caller's transaction."""
        self._assert_fence(account_id, fencing_token, instant)
        self._assert_cooldown_for_update(account_id, instant)
        run = self._run_for_update(run_id)
        if run.account_id != account_id:
            raise ValueError("run account does not match reservation account")
        if run.fencing_token != fencing_token:
            raise LostLease(f"run {run_id} fencing token is stale")
        if run.status != "running":
            raise StaleWrite(f"run {run_id} is not running")

        attempt = self.conn.execute(
            """
            SELECT account_profile_id, autopilot_run_id, autopilot_item_id
            FROM hh_application_attempts
            WHERE id = ?
            """,
            (attempt_id,),
        ).fetchone()
        if attempt is None:
            raise StaleWrite(f"attempt {attempt_id} does not exist")
        if str(attempt["account_profile_id"]) != account_id:
            raise ValueError("attempt account does not match reservation account")
        if attempt["autopilot_run_id"] is None:
            raise ValueError("attempt run does not match reservation run")
        attempt_run_id = _persisted_integer(
            attempt["autopilot_run_id"],
            field="attempt autopilot_run_id",
            minimum=1,
        )
        if attempt_run_id != run_id:
            raise ValueError("attempt run does not match reservation run")
        if attempt["autopilot_item_id"] is None:
            raise ValueError("attempt item is required for dispatch reservation")
        attempt_item_id = _persisted_integer(
            attempt["autopilot_item_id"],
            field="attempt autopilot_item_id",
            minimum=1,
        )
        item_row = self.conn.execute(
            """
            SELECT account_profile_id, last_run_id
            FROM hh_autopilot_items
            WHERE id = ?
            """,
            (attempt_item_id,),
        ).fetchone()
        if item_row is None:
            raise StaleWrite("attempt item does not exist")
        if str(item_row["account_profile_id"]) != account_id:
            raise ValueError("attempt item account does not match reservation")
        item_last_run_id = _persisted_integer(
            item_row["last_run_id"],
            field="item last_run_id",
            minimum=1,
        )
        if item_last_run_id != run_id:
            raise ValueError("attempt item run does not match reservation")

        existing_row = self.conn.execute(
            """
            SELECT * FROM hh_autopilot_quota_reservations
            WHERE attempt_id = ?
            """,
            (attempt_id,),
        ).fetchone()
        if existing_row is not None:
            existing = self._reservation_from_row(existing_row)
            exact = (
                existing.source == "dispatch"
                and existing.account_id == account_id
                and existing.run_id == run_id
                and existing.timezone == timezone_name
                and existing.local_date == local_date
                and existing.fencing_token == fencing_token
                and existing.state.value in ACTIVE_QUOTA_STATES
            )
            if exact:
                return existing
            raise StaleWrite(f"attempt {attempt_id} has a contradictory reservation")

        daily_count = int(
            self.conn.execute(
                """
                SELECT COUNT(*)
                FROM hh_autopilot_quota_reservations
                WHERE account_profile_id = ? AND local_date = ?
                  AND state IN ('reserved', 'held', 'consumed')
                """,
                (account_id, local_date),
            ).fetchone()[0]
        )
        if daily_count >= daily_limit:
            raise QuotaExceeded(account_id, "daily", daily_limit)
        run_count = int(
            self.conn.execute(
                """
                SELECT COUNT(*)
                FROM hh_autopilot_quota_reservations
                WHERE run_id = ?
                  AND state IN ('reserved', 'held', 'consumed')
                """,
                (run_id,),
            ).fetchone()[0]
        )
        if run_count >= run_limit:
            raise QuotaExceeded(account_id, "run", run_limit)

        cursor = self.conn.execute(
            """
            INSERT INTO hh_autopilot_quota_reservations (
                attempt_id, run_id, source, account_profile_id, timezone,
                local_date, state, fencing_token, created_at
            ) VALUES (?, ?, 'dispatch', ?, ?, ?, 'reserved', ?, ?)
            """,
            (
                attempt_id,
                run_id,
                account_id,
                timezone_name,
                local_date,
                fencing_token,
                instant.isoformat(),
            ),
        )
        return self._reservation_for_update(_required_lastrowid(cursor))

    def hold_reservation(
        self,
        reservation_id: int,
        fencing_token: int,
        *,
        now: datetime | str | None = None,
    ) -> QuotaReservationRecord:
        return self._change_reservation_state(
            reservation_id,
            fencing_token,
            target=QuotaReservationState.HELD,
            now=now,
        )

    def consume_reservation(
        self,
        reservation_id: int,
        fencing_token: int,
        *,
        remote_negotiation_id: str | None = None,
        now: datetime | str | None = None,
    ) -> QuotaReservationRecord:
        if remote_negotiation_id is not None:
            remote_negotiation_id = _required_text(
                remote_negotiation_id,
                field="remote_negotiation_id",
            )
        return self._change_reservation_state(
            reservation_id,
            fencing_token,
            target=QuotaReservationState.CONSUMED,
            remote_negotiation_id=remote_negotiation_id,
            now=now,
        )

    def release_reservation(
        self,
        reservation_id: int,
        fencing_token: int,
        *,
        now: datetime | str | None = None,
    ) -> QuotaReservationRecord:
        return self._change_reservation_state(
            reservation_id,
            fencing_token,
            target=QuotaReservationState.RELEASED,
            now=now,
        )

    def adopt_reservation_for_recovery(
        self,
        reservation_id: int,
        recovery_run_id: int,
        fencing_token: int,
        *,
        expected_fencing_token: int,
        now: datetime | str | None = None,
    ) -> QuotaReservationRecord:
        reservation_id = _integer(reservation_id, field="reservation_id", minimum=1)
        recovery_run_id = _integer(
            recovery_run_id,
            field="recovery_run_id",
            minimum=1,
        )
        fencing_token = _integer(fencing_token, field="fencing_token", minimum=1)
        expected_fencing_token = _integer(
            expected_fencing_token,
            field="expected_fencing_token",
            minimum=1,
        )
        instant = _instant(now, field="now")

        with self.immediate():
            return self._adopt_reservation_for_recovery_for_update(
                reservation_id,
                recovery_run_id,
                fencing_token,
                expected_fencing_token=expected_fencing_token,
                instant=instant,
            )

    def _adopt_reservation_for_recovery_for_update(
        self,
        reservation_id: int,
        recovery_run_id: int,
        fencing_token: int,
        *,
        expected_fencing_token: int,
        instant: datetime,
    ) -> QuotaReservationRecord:
        """Adopt an unresolved reservation inside the caller's transaction."""
        current = self._reservation_for_update(reservation_id)
        self._assert_fence(current.account_id, fencing_token, instant)

        try:
            recovery_run = self._run_for_update(recovery_run_id)
        except KeyError as exc:
            raise StaleWrite(f"recovery run {recovery_run_id} does not exist") from exc
        if recovery_run.trigger != "recovery":
            raise StaleWrite(f"run {recovery_run_id} is not a recovery run")
        if recovery_run.status != "running":
            raise StaleWrite(f"recovery run {recovery_run_id} is not running")
        if recovery_run.account_id != current.account_id:
            raise StaleWrite("recovery run account does not match reservation")
        if recovery_run.fencing_token != fencing_token:
            raise LostLease(f"recovery run {recovery_run_id} fencing token is stale")

        if current.source != "dispatch":
            raise StaleWrite("only dispatch reservations can be adopted")
        if current.state not in {
            QuotaReservationState.RESERVED,
            QuotaReservationState.HELD,
        }:
            raise StaleWrite(f"reservation {reservation_id} is already resolved")
        if current.attempt_id is None or current.run_id is None:
            raise StaleWrite("reservation attempt/run provenance is incomplete")
        is_replay = current.fencing_token == fencing_token
        if expected_fencing_token >= fencing_token:
            raise StaleWrite("expected fencing token must be lower than current token")
        if not is_replay and current.fencing_token != expected_fencing_token:
            raise StaleWrite("reservation fencing token changed from expected value")

        try:
            original_run = self._run_for_update(current.run_id)
        except KeyError as exc:
            raise StaleWrite(
                f"reservation run {current.run_id} does not exist"
            ) from exc
        if original_run.account_id != current.account_id:
            raise StaleWrite("reservation run account does not match reservation")
        if original_run.fencing_token < 1:
            raise LostLease(
                f"reservation run {current.run_id} fencing token is invalid"
            )
        if original_run.fencing_token > expected_fencing_token:
            raise StaleWrite("reservation run is newer than expected fencing token")

        attempt = self.conn.execute(
            """
            SELECT account_profile_id, autopilot_run_id, autopilot_item_id
            FROM hh_application_attempts
            WHERE id = ?
            """,
            (current.attempt_id,),
        ).fetchone()
        if attempt is None:
            raise StaleWrite(f"attempt {current.attempt_id} does not exist")
        if str(attempt["account_profile_id"]) != current.account_id:
            raise StaleWrite("attempt account does not match reservation")
        attempt_run_id = _persisted_integer(
            attempt["autopilot_run_id"],
            field="attempt autopilot_run_id",
            minimum=1,
        )
        if attempt_run_id != current.run_id:
            raise StaleWrite("attempt run does not match reservation")
        attempt_item_id = _persisted_integer(
            attempt["autopilot_item_id"],
            field="attempt autopilot_item_id",
            minimum=1,
        )

        item = self.conn.execute(
            """
            SELECT account_profile_id, last_run_id, active_attempt_id, state
            FROM hh_autopilot_items
            WHERE id = ?
            """,
            (attempt_item_id,),
        ).fetchone()
        if item is None:
            raise StaleWrite(f"item {attempt_item_id} does not exist")
        if str(item["account_profile_id"]) != current.account_id:
            raise StaleWrite("item account does not match reservation")
        item_active_attempt_id = _persisted_integer(
            item["active_attempt_id"],
            field="item active_attempt_id",
            minimum=1,
        )
        if item_active_attempt_id != current.attempt_id:
            raise StaleWrite("item active attempt does not match reservation")
        item_last_run_id = _persisted_integer(
            item["last_run_id"],
            field="item last_run_id",
            minimum=1,
        )
        item_state = str(item["state"])
        if item_state == AutopilotState.APPLYING.value:
            if item_last_run_id != current.run_id:
                raise StaleWrite("applying item does not belong to reservation run")
        elif item_state == AutopilotState.RECONCILING.value:
            if item_last_run_id == current.run_id:
                pass
            elif item_last_run_id == recovery_run_id:
                pass
            else:
                try:
                    previous_recovery_run = self._run_for_update(item_last_run_id)
                except KeyError as exc:
                    raise StaleWrite(
                        f"item recovery run {item_last_run_id} does not exist"
                    ) from exc
                if (
                    previous_recovery_run.account_id != current.account_id
                    or previous_recovery_run.trigger != "recovery"
                    or previous_recovery_run.fencing_token < 1
                    or previous_recovery_run.fencing_token > expected_fencing_token
                ):
                    raise StaleWrite(
                        "item previous recovery run does not match expected fence"
                    )
        else:
            raise StaleWrite("item is not applying or reconciling")

        if is_replay:
            return current

        cursor = self.conn.execute(
            """
            UPDATE hh_autopilot_quota_reservations
            SET fencing_token = ?
            WHERE id = ? AND fencing_token = ? AND state = ?
            """,
            (
                fencing_token,
                reservation_id,
                expected_fencing_token,
                current.state.value,
            ),
        )
        if cursor.rowcount != 1:
            raise StaleWrite(f"reservation {reservation_id} adoption CAS failed")
        return self._reservation_for_update(reservation_id)

    def sync_external_quota(
        self,
        account_id: str,
        remote_negotiation_id: str,
        timezone_name: str,
        fencing_token: int,
        *,
        occurred_at: datetime | str | None = None,
        now: datetime | str | None = None,
    ) -> QuotaReservationRecord:
        account_id = _canonical_identifier(account_id, field="account_id")
        remote_negotiation_id = _required_text(
            remote_negotiation_id,
            field="remote_negotiation_id",
        )
        timezone_name, local_timezone = _timezone(timezone_name)
        fencing_token = _integer(fencing_token, field="fencing_token", minimum=1)
        instant = _instant(now, field="now")
        occurred = (
            instant
            if occurred_at is None
            else _instant(occurred_at, field="occurred_at")
        )
        local_date = occurred.astimezone(local_timezone).date().isoformat()

        with self.immediate():
            self._assert_fence(account_id, fencing_token, instant)
            row = self.conn.execute(
                """
                SELECT * FROM hh_autopilot_quota_reservations
                WHERE remote_negotiation_id = ?
                """,
                (remote_negotiation_id,),
            ).fetchone()
            if row is not None:
                existing = self._reservation_from_row(row)
                if (
                    existing.account_id != account_id
                    or existing.state is not QuotaReservationState.CONSUMED
                ):
                    raise StaleWrite("remote negotiation has a contradictory quota row")
                if existing.source == "external_sync":
                    if (
                        existing.timezone != timezone_name
                        or existing.local_date != local_date
                    ):
                        raise StaleWrite(
                            "remote negotiation replay changed timezone or local date"
                        )
                    try:
                        created_at = _instant(
                            existing.created_at,
                            field="external quota created_at",
                        )
                        resolved_at = _instant(
                            existing.resolved_at,
                            field="external quota resolved_at",
                        )
                    except (TypeError, ValueError) as exc:
                        raise StaleWrite(
                            "remote negotiation occurrence timestamp is invalid"
                        ) from exc
                    if created_at != occurred or resolved_at != occurred:
                        raise StaleWrite(
                            "remote negotiation replay changed occurrence instant"
                        )
                return existing

            cursor = self.conn.execute(
                """
                INSERT INTO hh_autopilot_quota_reservations (
                    attempt_id, run_id, source, remote_negotiation_id,
                    account_profile_id, timezone, local_date, state,
                    fencing_token, created_at, resolved_at
                ) VALUES (NULL, NULL, 'external_sync', ?, ?, ?, ?,
                          'consumed', ?, ?, ?)
                """,
                (
                    remote_negotiation_id,
                    account_id,
                    timezone_name,
                    local_date,
                    fencing_token,
                    occurred.isoformat(),
                    occurred.isoformat(),
                ),
            )
            return self._reservation_for_update(_required_lastrowid(cursor))

    def get_reservation(
        self,
        reservation_id: int,
    ) -> QuotaReservationRecord | None:
        reservation_id = _integer(reservation_id, field="reservation_id", minimum=1)
        row = self.conn.execute(
            """
            SELECT * FROM hh_autopilot_quota_reservations
            WHERE id = ?
            """,
            (reservation_id,),
        ).fetchone()
        return self._reservation_from_row(row) if row is not None else None

    def active_reservation_for_attempt(
        self,
        attempt_id: int,
    ) -> QuotaReservationRecord | None:
        attempt_id = _integer(attempt_id, field="attempt_id", minimum=1)
        row = self.conn.execute(
            """
            SELECT * FROM hh_autopilot_quota_reservations
            WHERE attempt_id = ?
              AND state IN ('reserved', 'held', 'consumed')
            """,
            (attempt_id,),
        ).fetchone()
        return self._reservation_from_row(row) if row is not None else None

    def list_active_reservations(
        self,
        account_id: str,
        *,
        local_date: str | None = None,
        run_id: int | None = None,
    ) -> list[QuotaReservationRecord]:
        account_id = _canonical_identifier(account_id, field="account_id")
        if local_date is not None:
            local_date = _required_text(local_date, field="local_date")
        run_id = _optional_integer(run_id, field="run_id")
        if run_id == 0:
            raise ValueError("run_id must be at least 1")
        clauses = [
            "account_profile_id = ?",
            "state IN ('reserved', 'held', 'consumed')",
        ]
        parameters: list[Any] = [account_id]
        if local_date is not None:
            clauses.append("local_date = ?")
            parameters.append(local_date)
        if run_id is not None:
            clauses.append("run_id = ?")
            parameters.append(run_id)
        rows = self.conn.execute(
            "SELECT * FROM hh_autopilot_quota_reservations WHERE "
            + " AND ".join(clauses)
            + " ORDER BY id ASC",
            tuple(parameters),
        ).fetchall()
        return [self._reservation_from_row(row) for row in rows]

    def count_active_reservations(
        self,
        account_id: str,
        *,
        local_date: str | None = None,
        run_id: int | None = None,
    ) -> int:
        return len(
            self.list_active_reservations(
                account_id,
                local_date=local_date,
                run_id=run_id,
            )
        )

    def get_lease(self, account_id: str) -> LeaseRecord | None:
        account_id = _canonical_identifier(account_id, field="account_id")
        row = self.conn.execute(
            "SELECT * FROM hh_autopilot_leases WHERE account_profile_id = ?",
            (account_id,),
        ).fetchone()
        if row is None:
            return None
        owner_token = _stored_lease_owner(row["owner_token"], account_id=account_id)
        if owner_token == LEASE_TOMBSTONE_OWNER:
            if row["expires_at"] != LEASE_TOMBSTONE_EXPIRY:
                raise LostLease(f"account {account_id} lease tombstone is invalid")
            return None
        return self._lease_from_row(row)

    def get_challenge(self, challenge_id: int) -> ChallengeRecord | None:
        challenge_id = _integer(challenge_id, field="challenge_id", minimum=1)
        row = self.conn.execute(
            "SELECT * FROM hh_autopilot_challenges WHERE id = ?", (challenge_id,)
        ).fetchone()
        return self._challenge_from_row(row) if row is not None else None

    def _change_reservation_state(
        self,
        reservation_id: int,
        fencing_token: int,
        *,
        target: QuotaReservationState,
        remote_negotiation_id: str | None = None,
        now: datetime | str | None,
    ) -> QuotaReservationRecord:
        reservation_id = _integer(reservation_id, field="reservation_id", minimum=1)
        fencing_token = _integer(fencing_token, field="fencing_token", minimum=1)
        if not isinstance(target, QuotaReservationState):
            raise TypeError("target must be QuotaReservationState")
        if remote_negotiation_id is not None:
            remote_negotiation_id = _required_text(
                remote_negotiation_id,
                field="remote_negotiation_id",
            )
        instant = _instant(now, field="now")

        with self.immediate():
            return self._change_reservation_state_for_update(
                reservation_id,
                fencing_token,
                target=target,
                remote_negotiation_id=remote_negotiation_id,
                instant=instant,
            )

    def _change_reservation_state_for_update(
        self,
        reservation_id: int,
        fencing_token: int,
        *,
        target: QuotaReservationState,
        remote_negotiation_id: str | None,
        instant: datetime,
    ) -> QuotaReservationRecord:
        """Apply a prevalidated reservation CAS in the caller's transaction."""
        current = self._reservation_for_update(reservation_id)
        self._assert_fence(
            current.account_id,
            fencing_token,
            instant,
        )
        if current.fencing_token != fencing_token:
            raise StaleWrite(f"reservation {reservation_id} fencing token changed")
        if current.state is target:
            if (
                target is QuotaReservationState.CONSUMED
                and remote_negotiation_id is not None
                and current.remote_negotiation_id != remote_negotiation_id
            ):
                raise StaleWrite("consumed reservation remote negotiation changed")
            return current

        allowed_sources: dict[
            QuotaReservationState, frozenset[QuotaReservationState]
        ] = {
            QuotaReservationState.HELD: frozenset({QuotaReservationState.RESERVED}),
            QuotaReservationState.CONSUMED: frozenset(
                {
                    QuotaReservationState.RESERVED,
                    QuotaReservationState.HELD,
                }
            ),
            QuotaReservationState.RELEASED: frozenset(
                {
                    QuotaReservationState.RESERVED,
                    QuotaReservationState.HELD,
                }
            ),
        }
        if current.state not in allowed_sources.get(target, frozenset()):
            raise StaleWrite(
                f"reservation {reservation_id} cannot transition "
                f"from {current.state.value} to {target.value}"
            )

        if (
            target is QuotaReservationState.CONSUMED
            and remote_negotiation_id is not None
        ):
            duplicate = self.conn.execute(
                """
                SELECT id FROM hh_autopilot_quota_reservations
                WHERE remote_negotiation_id = ?
                """,
                (remote_negotiation_id,),
            ).fetchone()
            if duplicate is not None and int(duplicate["id"]) != reservation_id:
                raise StaleWrite("remote negotiation belongs to another reservation")

        resolved_at = (
            "" if target is QuotaReservationState.HELD else instant.isoformat()
        )
        cursor = self.conn.execute(
            """
            UPDATE hh_autopilot_quota_reservations
            SET state = ?,
                remote_negotiation_id = CASE
                    WHEN ? = 'consumed' THEN ?
                    ELSE remote_negotiation_id
                END,
                resolved_at = ?
            WHERE id = ? AND state = ? AND fencing_token = ?
            """,
            (
                target.value,
                target.value,
                remote_negotiation_id,
                resolved_at,
                reservation_id,
                current.state.value,
                fencing_token,
            ),
        )
        if cursor.rowcount != 1:
            raise StaleWrite(f"reservation {reservation_id} compare-and-swap failed")
        return self._reservation_for_update(reservation_id)

    def _reservation_for_update(
        self,
        reservation_id: int,
    ) -> QuotaReservationRecord:
        row = self.conn.execute(
            """
            SELECT * FROM hh_autopilot_quota_reservations
            WHERE id = ?
            """,
            (reservation_id,),
        ).fetchone()
        if row is None:
            raise StaleWrite(f"reservation {reservation_id} does not exist")
        return self._reservation_from_row(row)

    def _assert_cooldown_for_update(
        self,
        account_id: str,
        instant: datetime,
    ) -> None:
        row = self.conn.execute(
            """
            SELECT blocked_until, block_reason
            FROM hh_autopilot_account_state
            WHERE account_profile_id = ?
            """,
            (account_id,),
        ).fetchone()
        if row is None or not str(row["blocked_until"]).strip():
            return
        blocked_until_raw = str(row["blocked_until"])
        reason = str(row["block_reason"]) or "cooldown"
        try:
            blocked_until = _instant(
                blocked_until_raw,
                field="blocked_until",
            )
        except (TypeError, ValueError):
            raise CooldownActive(
                account_id,
                reason or "invalid_cooldown",
                blocked_until_raw,
            ) from None
        if blocked_until > instant:
            raise CooldownActive(
                account_id,
                reason,
                blocked_until.isoformat(),
            )

    def _assert_fence(
        self,
        account_id: str,
        fencing_token: int,
        instant: datetime,
    ) -> None:
        row = self.conn.execute(
            """
            SELECT owner_token, fencing_token, expires_at
            FROM hh_autopilot_leases
            WHERE account_profile_id = ?
            """,
            (account_id,),
        ).fetchone()
        if row is None:
            raise LostLease(f"account {account_id} lease was lost")
        stored_token = _stored_fencing_token(
            row["fencing_token"], account_id=account_id
        )
        if stored_token != fencing_token:
            raise LostLease(f"account {account_id} lease was lost")
        stored_owner = _stored_lease_owner(row["owner_token"], account_id=account_id)
        if stored_owner == LEASE_TOMBSTONE_OWNER:
            raise LostLease(f"account {account_id} lease was released")
        expires_at = _stored_lease_expiry(row["expires_at"], account_id=account_id)
        if expires_at <= instant:
            raise LostLease(f"account {account_id} lease expired")

    def _lease_for_update(self, account_id: str) -> LeaseRecord:
        row = self.conn.execute(
            "SELECT * FROM hh_autopilot_leases WHERE account_profile_id = ?",
            (account_id,),
        ).fetchone()
        if row is None:
            raise LostLease(f"account {account_id} lease disappeared")
        return self._lease_from_row(row)

    def _insert_event(
        self,
        *,
        item_id: int,
        run_id: int | None,
        previous: AutopilotState | None,
        target: AutopilotState,
        reason: str,
        metadata: dict[str, Any],
        created_at: datetime | None = None,
    ) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO hh_autopilot_events (
                run_id, item_id, previous_state, next_state, reason_code,
                metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                item_id,
                "" if previous is None else previous.value,
                target.value,
                reason,
                _json_dumps(metadata, field="metadata"),
                _utc_now() if created_at is None else created_at.isoformat(),
            ),
        )
        return _required_lastrowid(cursor)

    def _run_for_update(self, run_id: int) -> RunRecord:
        row = self.conn.execute(
            "SELECT * FROM hh_autopilot_runs WHERE id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"run {run_id} does not exist")
        return self._run_from_row(row)

    def _item_for_update(self, item_id: int) -> ItemRecord:
        row = self.conn.execute(
            "SELECT * FROM hh_autopilot_items WHERE id = ?", (item_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"item {item_id} does not exist")
        return self._item_from_row(row)

    @staticmethod
    def _run_from_row(row: sqlite3.Row) -> RunRecord:
        trigger = str(row["trigger"])
        status = str(row["status"])
        if trigger not in RUN_TRIGGERS:
            raise ValueError(f"invalid run trigger in storage: {trigger}")
        if status not in RUN_STATUSES:
            raise ValueError(f"invalid run status in storage: {status}")
        return RunRecord(
            id=int(row["id"]),
            account_id=str(row["account_profile_id"]),
            trigger=trigger,
            status=status,
            grant_id=None if row["grant_id"] is None else int(row["grant_id"]),
            policy_hash=str(row["policy_hash"]),
            fencing_token=_persisted_integer(
                row["fencing_token"],
                field="run fencing_token",
                error_type=LostLease,
            ),
            counters=_json_loads(row["counters_json"], field="counters_json"),
            error=str(row["error"]),
            started_at=str(row["started_at"]),
            finished_at=str(row["finished_at"]),
            created_at=str(row["created_at"]),
        )

    @staticmethod
    def _item_from_row(row: sqlite3.Row) -> ItemRecord:
        return ItemRecord(
            id=int(row["id"]),
            origin_run_id=int(row["origin_run_id"]),
            last_run_id=_persisted_integer(
                row["last_run_id"],
                field="item last_run_id",
                minimum=1,
            ),
            account_id=str(row["account_profile_id"]),
            vacancy_id=str(row["vacancy_id"]),
            resume_id=str(row["resume_id"]),
            state=AutopilotState(str(row["state"])),
            retry_stage=RetryStage(str(row["retry_stage"])),
            version=int(row["version"]),
            active_attempt_id=(
                None
                if row["active_attempt_id"] is None
                else _persisted_integer(
                    row["active_attempt_id"],
                    field="item active_attempt_id",
                    minimum=1,
                )
            ),
        )

    @staticmethod
    def _lease_from_row(row: sqlite3.Row) -> LeaseRecord:
        return LeaseRecord(
            account_id=str(row["account_profile_id"]),
            owner_token=str(row["owner_token"]),
            fencing_token=int(row["fencing_token"]),
            expires_at=str(row["expires_at"]),
            updated_at=str(row["updated_at"]),
        )

    @staticmethod
    def _reservation_from_row(row: sqlite3.Row) -> QuotaReservationRecord:
        source = str(row["source"])
        if source not in {"dispatch", "external_sync"}:
            raise ValueError(f"invalid quota reservation source: {source}")
        return QuotaReservationRecord(
            id=int(row["id"]),
            attempt_id=(
                None
                if row["attempt_id"] is None
                else _persisted_integer(
                    row["attempt_id"],
                    field="reservation attempt_id",
                    minimum=1,
                )
            ),
            run_id=(
                None
                if row["run_id"] is None
                else _persisted_integer(
                    row["run_id"],
                    field="reservation run_id",
                    minimum=1,
                )
            ),
            source=source,
            remote_negotiation_id=(
                None
                if row["remote_negotiation_id"] is None
                else str(row["remote_negotiation_id"])
            ),
            account_id=str(row["account_profile_id"]),
            timezone=str(row["timezone"]),
            local_date=str(row["local_date"]),
            state=QuotaReservationState(str(row["state"])),
            fencing_token=_persisted_integer(
                row["fencing_token"],
                field="reservation fencing_token",
                minimum=1,
            ),
            created_at=str(row["created_at"]),
            resolved_at=str(row["resolved_at"]),
        )

    @staticmethod
    def _challenge_from_row(row: sqlite3.Row) -> ChallengeRecord:
        return ChallengeRecord(
            id=int(row["id"]),
            scope=str(row["scope"]),
            challenge_type=str(row["challenge_type"]),
            account_id=str(row["account_profile_id"]),
            item_id=None if row["item_id"] is None else int(row["item_id"]),
            reservation_id=(
                None if row["reservation_id"] is None else int(row["reservation_id"])
            ),
            sanitized_url=str(row["sanitized_url"]),
            screenshot_path=str(row["screenshot_path"]),
            status=str(row["status"]),
            expires_at=str(row["expires_at"]),
            resolution_at=str(row["resolution_at"]),
            resolution_actor=str(row["resolution_actor"]),
            resolution_action=str(row["resolution_action"]),
            metadata=_json_loads(row["metadata_json"], field="metadata_json"),
            created_at=str(row["created_at"]),
        )

    @staticmethod
    def _grant_from_row(row: sqlite3.Row) -> GrantRecord:
        scope = str(row["scope"])
        if scope != APPLICATION_SCOPE:
            raise ValueError(f"invalid grant scope in storage: {scope}")
        active = int(row["active"])
        if active not in {0, 1}:
            raise ValueError(f"invalid grant active flag in storage: {active}")
        return GrantRecord(
            id=int(row["id"]),
            account_id=str(row["account_profile_id"]),
            scope=scope,
            policy_hash=str(row["policy_hash"]),
            generation=int(row["generation"]),
            active=bool(active),
            actor=str(row["actor"]),
            source=str(row["source"]),
            created_at=str(row["created_at"]),
            revoked_at=str(row["revoked_at"]),
        )

    @staticmethod
    def _control_from_row(row: sqlite3.Row) -> ControlRecord:
        scope_type = str(row["scope_type"])
        if scope_type not in CONTROL_SCOPE_TYPES:
            raise ValueError(f"invalid control scope in storage: {scope_type}")
        paused = int(row["paused"])
        kill_switch = int(row["kill_switch"])
        if paused not in {0, 1} or kill_switch not in {0, 1}:
            raise ValueError("invalid control flag in storage")
        return ControlRecord(
            scope_type=scope_type,
            scope_id=str(row["scope_id"]),
            paused=bool(paused),
            kill_switch=bool(kill_switch),
            version=int(row["version"]),
            updated_at=str(row["updated_at"]),
        )

    @staticmethod
    def _account_state_from_row(row: sqlite3.Row) -> AccountStateRecord:
        return AccountStateRecord(
            account_id=str(row["account_profile_id"]),
            blocked_until=str(row["blocked_until"]),
            block_reason=str(row["block_reason"]),
            hh_reset=_json_loads(row["hh_reset_json"], field="hh_reset_json"),
            last_scheduled_at=str(row["last_scheduled_at"]),
            next_scheduled_at=str(row["next_scheduled_at"]),
            version=int(row["version"]),
            updated_at=str(row["updated_at"]),
        )

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "run_id": None if row["run_id"] is None else int(row["run_id"]),
            "item_id": int(row["item_id"]),
            "previous_state": str(row["previous_state"]),
            "next_state": str(row["next_state"]),
            "reason_code": str(row["reason_code"]),
            "metadata_json": _json_loads(row["metadata_json"], field="metadata_json"),
            "created_at": str(row["created_at"]),
        }


class LeaseKeeper:
    def __init__(
        self,
        repository: AutopilotRepository,
        lease: LeaseRecord,
        *,
        ttl_seconds: int,
        renewal_margin_seconds: int,
        clock: Callable[[], datetime | str] | None = None,
    ) -> None:
        if not isinstance(repository, AutopilotRepository):
            raise TypeError("repository must be an AutopilotRepository")
        _lease_input(lease)
        self._repository = repository
        self._lease = lease
        self._ttl_seconds = _integer(ttl_seconds, field="ttl_seconds", minimum=1)
        self._renewal_margin_seconds = _integer(
            renewal_margin_seconds,
            field="renewal_margin_seconds",
        )
        if clock is not None and not callable(clock):
            raise TypeError("clock must be callable")
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    @property
    def lease(self) -> LeaseRecord:
        return self._lease

    def ensure_current(
        self,
        now: datetime | str | None = None,
    ) -> LeaseRecord:
        instant = _instant(
            self._clock() if now is None else now,
            field="now",
        )
        expires_at = _stored_lease_expiry(
            self._lease.expires_at,
            account_id=self._lease.account_id,
        )
        remaining = expires_at - instant
        if remaining <= timedelta(seconds=self._renewal_margin_seconds):
            self._lease = self._repository.renew_lease(
                self._lease,
                ttl_seconds=self._ttl_seconds,
                now=instant,
            )
        else:
            self._repository.assert_fence(
                self._lease.account_id,
                self._lease.fencing_token,
                now=instant,
            )
        return self._lease


__all__ = [
    "AccountStateRecord",
    "AuthorizationReconciliationRecord",
    "AutopilotRepository",
    "ChallengeRecord",
    "CooldownActive",
    "ControlRecord",
    "GrantRecord",
    "ItemRecord",
    "LeaseKeeper",
    "LeaseRecord",
    "LiveAuthorizationSnapshot",
    "KillSwitchActive",
    "LostLease",
    "QuotaExceeded",
    "QuotaReservationRecord",
    "RunRecord",
    "RepositoryAuthorizationDenied",
    "StaleWrite",
    "TimezoneChangeUnsafe",
]
