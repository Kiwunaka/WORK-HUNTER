from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Iterator

from work_hunter.storage import Storage, redact_for_storage

from .state_machine import assert_transition
from .types import AutopilotState, RetryStage


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
TERMINAL_RUN_STATUSES = frozenset(
    {"completed", "failed", "interrupted", "cancelled"}
)


class StaleWrite(RuntimeError):
    pass


class LostLease(RuntimeError):
    pass


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


def _required_lastrowid(cursor: sqlite3.Cursor) -> int:
    lastrowid = cursor.lastrowid
    if lastrowid is None:
        raise RuntimeError("SQLite did not return a lastrowid")
    return lastrowid


def _enum_value(value: Any, enum_type: type[AutopilotState] | type[RetryStage], *, field: str):
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
        with self.immediate():
            current = self._run_for_update(run_id)
            if fencing_token is not None:
                self._assert_fence(current.account_id, fencing_token)
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
            (
                account_id
                + "\0"
                + resume_id
                + "\0"
                + vacancy_id
                + "\0apply"
            ).encode("utf-8")
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
        with self.immediate():
            current = self._item_for_update(item_id)
            if current.version != expected_version:
                raise StaleWrite(f"item {item_id} version changed")
            if fencing_token is not None:
                self._assert_fence(current.account_id, fencing_token)
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
                    _utc_now(),
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
                metadata=_json_loads(metadata_json, field="metadata"),
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
        with self.immediate():
            current = self._item_for_update(item_id)
            if fencing_token is not None:
                self._assert_fence(current.account_id, fencing_token)
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

    def get_lease(self, account_id: str) -> LeaseRecord | None:
        account_id = _canonical_identifier(account_id, field="account_id")
        row = self.conn.execute(
            "SELECT * FROM hh_autopilot_leases WHERE account_profile_id = ?",
            (account_id,),
        ).fetchone()
        return self._lease_from_row(row) if row is not None else None

    def get_challenge(self, challenge_id: int) -> ChallengeRecord | None:
        challenge_id = _integer(challenge_id, field="challenge_id", minimum=1)
        row = self.conn.execute(
            "SELECT * FROM hh_autopilot_challenges WHERE id = ?", (challenge_id,)
        ).fetchone()
        return self._challenge_from_row(row) if row is not None else None

    def _assert_fence(self, account_id: str, fencing_token: int) -> None:
        row = self.conn.execute(
            """
            SELECT fencing_token, expires_at
            FROM hh_autopilot_leases
            WHERE account_profile_id = ?
            """,
            (account_id,),
        ).fetchone()
        if row is None or int(row["fencing_token"]) != fencing_token:
            raise LostLease(f"account {account_id} lease was lost")
        try:
            expires_at = datetime.fromisoformat(str(row["expires_at"]))
            if expires_at.tzinfo is None or expires_at.utcoffset() is None:
                raise ValueError("lease expiry is not timezone-aware")
        except ValueError as exc:
            raise LostLease(f"account {account_id} lease expiry is invalid") from exc
        if expires_at.astimezone(timezone.utc) <= datetime.now(timezone.utc):
            raise LostLease(f"account {account_id} lease expired")

    def _insert_event(
        self,
        *,
        item_id: int,
        run_id: int | None,
        previous: AutopilotState | None,
        target: AutopilotState,
        reason: str,
        metadata: dict[str, Any],
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
                _utc_now(),
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
            fencing_token=int(row["fencing_token"]),
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
            last_run_id=int(row["last_run_id"]),
            account_id=str(row["account_profile_id"]),
            vacancy_id=str(row["vacancy_id"]),
            resume_id=str(row["resume_id"]),
            state=AutopilotState(str(row["state"])),
            retry_stage=RetryStage(str(row["retry_stage"])),
            version=int(row["version"]),
            active_attempt_id=(
                None
                if row["active_attempt_id"] is None
                else int(row["active_attempt_id"])
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
    def _event_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "run_id": None if row["run_id"] is None else int(row["run_id"]),
            "item_id": int(row["item_id"]),
            "previous_state": str(row["previous_state"]),
            "next_state": str(row["next_state"]),
            "reason_code": str(row["reason_code"]),
            "metadata_json": _json_loads(
                row["metadata_json"], field="metadata_json"
            ),
            "created_at": str(row["created_at"]),
        }


__all__ = [
    "AutopilotRepository",
    "ChallengeRecord",
    "ItemRecord",
    "LeaseRecord",
    "LostLease",
    "RunRecord",
    "StaleWrite",
]
