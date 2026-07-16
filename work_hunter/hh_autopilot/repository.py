from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from work_hunter.storage import Storage, redact_for_storage

from .sanitization import sanitize_text
from .state_machine import assert_transition
from .types import (
    AIDecision,
    AuthorizationKind,
    AutopilotState,
    DeliveryCertainty,
    DispatchConfigSnapshot,
    DispatchOutcome,
    FilterDecision,
    LiteralConfirmation,
    LiveAuthorization,
    NormalizedVacancy,
    PreparedDispatch,
    QuotaReservationState,
    RankScore,
    RankingDecision,
    RecoveryProvenance,
    RetryDecision,
    RetryStage,
    SearchPage,
    SearchRequest,
)


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
class SearchCycleRecord:
    id: int
    account_id: str
    policy_hash: str
    origin_run_id: int
    owner_run_id: int
    claim_version: int
    fencing_token: int
    mode: str
    distinct_vacancy_cap: int | None
    status: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class SearchCheckpointRecord:
    id: int
    cycle_id: int
    resume_id: str
    query_key: str
    next_page: int
    reported_total: int | None
    unique_vacancy_count: int
    status: str
    updated_at: str


@dataclass(frozen=True)
class SearchPageCommitRecord:
    checkpoint: SearchCheckpointRecord
    accepted_vacancy_ids: tuple[str, ...]
    inserted_reference_count: int
    new_distinct_count: int
    cycle_distinct_count: int


@dataclass(frozen=True)
class SearchResultRecord:
    id: int
    cycle_id: int
    checkpoint_id: int
    account_id: str
    resume_id: str
    query_key: str
    vacancy_id: str
    page: int
    normalized: dict[str, Any]
    discovered_at: str


@dataclass(frozen=True)
class ShadowResultRecord:
    id: int
    run_id: int
    account_id: str
    vacancy_id: str
    resume_id: str
    filter_data: dict[str, Any]
    deterministic_score: float | None
    ai_data: dict[str, Any]
    would_apply: bool
    created_at: str


@dataclass(frozen=True)
class ItemRecord:
    id: int
    origin_run_id: int
    last_run_id: int
    account_id: str
    vacancy_id: str
    resume_id: str
    query_key: str
    state: AutopilotState
    retry_stage: RetryStage
    version: int
    filter_data: dict[str, Any]
    deterministic_score: float | None
    ai_data: dict[str, Any]
    published_at: str
    application_attempt_count: int
    reconciliation_count: int
    next_attempt_at: str
    last_outcome_code: str
    active_attempt_id: int | None
    challenge_id: int | None

    @property
    def account_profile_id(self) -> str:
        return self.account_id

    @property
    def filter_evidence(self) -> dict[str, Any]:
        return _json_copy(self.filter_data)

    @property
    def ai_evidence(self) -> dict[str, Any]:
        return _json_copy(self.ai_data)


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


@dataclass(frozen=True)
class ApplicationAttemptRecord:
    id: int
    account_id: str
    run_id: int
    item_id: int
    autopilot_attempt_id: int
    vacancy_id: str
    resume_id: str
    status: str
    reason: str
    authorization_kind: AuthorizationKind
    authorization_ref: str
    policy_hash: str
    delivery_certainty: DeliveryCertainty | None
    raw_result: dict[str, Any]
    created_at: str
    dispatched_at: str
    finished_at: str

    @property
    def raw_result_json(self) -> str:
        return json.dumps(
            self.raw_result,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )


@dataclass(frozen=True)
class ApplicationGuardRecord:
    account_id: str
    source: str
    source_id: str
    owner_attempt_id: int | None
    first_resume_id: str
    status: str
    application_id: int | None
    application_count: int
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ReconciliationContext:
    prepared: PreparedDispatch
    dispatched_at: datetime
    initial_outcome_code: str
    challenge_id: int | None = None
    challenge_type: str = ""


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


def _persisted_text(
    value: Any,
    *,
    field: str,
    canonical: bool = False,
) -> str:
    if type(value) is not str:
        raise StaleWrite(f"{field} is not valid persisted text")
    try:
        parsed = (
            _canonical_identifier(value, field=field)
            if canonical
            else _required_text(value, field=field)
        )
    except (TypeError, ValueError) as exc:
        raise StaleWrite(f"{field} is not valid persisted text") from exc
    if parsed != value:
        raise StaleWrite(f"{field} is not canonical persisted text")
    return value


def _optional_text(value: Any, *, field: str, maximum: int = 8_000) -> str:
    if type(value) is not str:
        raise TypeError(f"{field} must be a string")
    parsed = value.strip()
    if "\0" in parsed:
        raise ValueError(f"{field} must not contain NUL")
    if len(parsed) > maximum:
        raise ValueError(f"{field} is too long")
    return parsed


def _persisted_optional_text(
    value: Any,
    *,
    field: str,
    maximum: int = 8_000,
) -> str:
    if type(value) is not str:
        raise StaleWrite(f"{field} is not valid persisted text")
    try:
        parsed = _optional_text(value, field=field, maximum=maximum)
    except (TypeError, ValueError) as exc:
        raise StaleWrite(f"{field} is not valid persisted text") from exc
    if parsed != value:
        raise StaleWrite(f"{field} is not canonical persisted text")
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
        allow_nan=False,
    )


def _json_loads(value: Any, *, field: str) -> dict[str, Any]:
    if type(value) is not str:
        raise ValueError(f"invalid JSON in {field}")
    try:
        loaded = json.loads(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid JSON in {field}") from exc
    if not isinstance(loaded, dict):
        raise ValueError(f"{field} must contain a JSON object")
    return loaded


def _persisted_json_object(value: Any, *, field: str) -> dict[str, Any]:
    if type(value) is not str:
        raise StaleWrite(f"{field} is not persisted JSON text")
    try:
        loaded = json.loads(value, parse_constant=_reject_json_constant)
        if not isinstance(loaded, dict):
            raise TypeError(f"{field} must contain a JSON object")
        canonical = _json_dumps(loaded, field=field)
    except (TypeError, ValueError) as exc:
        raise StaleWrite(f"{field} is malformed") from exc
    if canonical != value:
        raise StaleWrite(f"{field} is not canonical persisted JSON")
    return _json_copy(loaded)


def _persisted_filter_data(value: Any) -> dict[str, Any]:
    data = _persisted_json_object(value, field="item filter_json")
    if not data:
        return {}
    if set(data) != {"passed", "reason", "evidence"}:
        raise StaleWrite("item filter_json has an invalid shape")
    try:
        decision = FilterDecision(
            passed=data["passed"],
            reason=data["reason"],
            evidence=data["evidence"],
        )
        canonical = _json_dumps(
            decision.to_dict(),
            field="item filter_json",
        )
    except (TypeError, ValueError) as exc:
        raise StaleWrite("item filter_json has an invalid decision") from exc
    if canonical != value:
        raise StaleWrite("item filter_json is not a canonical decision")
    return decision.to_dict()


def _ranking_decision_from_data(
    data: Mapping[str, Any],
    *,
    field: str,
) -> RankingDecision:
    if set(data) != {
        "ready",
        "retry",
        "reason",
        "rank_score",
        "ai_decision",
    }:
        raise StaleWrite(f"{field} has an invalid shape")
    rank_value = data["rank_score"]
    if not isinstance(rank_value, Mapping) or set(rank_value) != {
        "score",
        "components",
        "weights",
    }:
        raise StaleWrite(f"{field} rank score has an invalid shape")
    ai_value = data["ai_decision"]
    ai: AIDecision | None
    if ai_value is None:
        ai = None
    else:
        if not isinstance(ai_value, Mapping) or set(ai_value) != {
            "available",
            "suitable",
            "confidence",
            "evidence",
            "reasons",
            "reason",
        }:
            raise StaleWrite(f"{field} AI evidence has an invalid shape")
        try:
            ai = AIDecision(
                available=ai_value["available"],
                suitable=ai_value["suitable"],
                confidence=ai_value["confidence"],
                evidence=ai_value["evidence"],
                reasons=ai_value["reasons"],
                reason=ai_value["reason"],
            )
        except (TypeError, ValueError) as exc:
            raise StaleWrite(f"{field} AI evidence is malformed") from exc
    try:
        rank_score = RankScore(
            score=rank_value["score"],
            components=rank_value["components"],
            weights=rank_value["weights"],
        )
        return RankingDecision(
            ready=data["ready"],
            retry=data["retry"],
            reason=data["reason"],
            rank_score=rank_score,
            ai_decision=ai,
        )
    except (TypeError, ValueError) as exc:
        raise StaleWrite(f"{field} has an invalid ranking decision") from exc


def _persisted_ai_data(
    value: Any,
    *,
    deterministic_score: float | None,
) -> dict[str, Any]:
    data = _persisted_json_object(value, field="item ai_json")
    if not data:
        if deterministic_score is not None:
            raise StaleWrite("item score exists without a ranking decision")
        return {}
    decision = _ranking_decision_from_data(data, field="item ai_json")
    if deterministic_score is None or deterministic_score != decision.score:
        raise StaleWrite("item deterministic score disagrees with ranking decision")
    canonical_data = decision.to_dict()
    if _json_dumps(canonical_data, field="item ai_json") != value:
        raise StaleWrite("item ai_json is not a canonical decision")
    return canonical_data


def _persisted_score(value: Any, *, field: str) -> float | None:
    if value is None:
        return None
    if type(value) not in {int, float}:
        raise StaleWrite(f"{field} is not a persisted score")
    parsed = float(value)
    if not 0.0 <= parsed <= 100.0 or not math.isfinite(parsed):
        raise StaleWrite(f"{field} is not a finite score in 0..100")
    return parsed


def _ranking_publication_timestamp(value: str) -> float:
    if not value:
        return float("-inf")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return float("-inf")
        return parsed.astimezone(timezone.utc).timestamp()
    except (ValueError, OverflowError, OSError):
        return float("-inf")


def _ranking_ai_confidence(data: Mapping[str, Any]) -> float | None:
    ai = data.get("ai_decision")
    if not isinstance(ai, Mapping) or ai.get("available") is not True:
        return None
    value = ai.get("confidence")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StaleWrite("item AI confidence is malformed")
    parsed = float(value)
    if not math.isfinite(parsed) or not 0.0 <= parsed <= 1.0:
        raise StaleWrite("item AI confidence is malformed")
    return parsed


def _ranked_item_sort_key(
    candidate: ItemRecord,
) -> tuple[float, float, float, str, str]:
    score = candidate.deterministic_score
    if score is None:
        raise StaleWrite("ranked item is missing deterministic score")
    confidence = _ranking_ai_confidence(candidate.ai_data)
    return (
        -score,
        -(confidence if confidence is not None else -1.0),
        -_ranking_publication_timestamp(candidate.published_at),
        candidate.vacancy_id,
        candidate.resume_id,
    )


def _json_copy(value: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _sanitize_dispatch_value(
    value: Any,
    *,
    field: str,
    depth: int = 0,
) -> Any:
    if depth > 5:
        return "redacted"
    if value is None or type(value) in {bool, int}:
        return value
    if type(value) is float:
        return value if math.isfinite(value) else "redacted"
    if isinstance(value, str):
        try:
            return sanitize_text(
                value,
                field=field,
                maximum=1_000,
                allow_empty=True,
                markup="strip",
                sensitive="redact",
                overflow="truncate",
            )
        except (TypeError, ValueError):
            return "redacted"
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 100 or not isinstance(key, str):
                break
            safe_key = key.strip()[:100]
            if not safe_key or "\0" in safe_key:
                continue
            result[safe_key] = _sanitize_dispatch_value(
                item,
                field=f"{field}.{safe_key}",
                depth=depth + 1,
            )
        return redact_for_storage(result)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [
            _sanitize_dispatch_value(
                item,
                field=f"{field}[{index}]",
                depth=depth + 1,
            )
            for index, item in enumerate(value[:100])
        ]
    return "redacted"


def _dispatch_storage_payload(outcome: DispatchOutcome) -> dict[str, Any]:
    if type(outcome) is not DispatchOutcome:
        raise TypeError("outcome must be an exact DispatchOutcome")
    sanitized = _sanitize_dispatch_value(
        {
            "code": outcome.code,
            "certainty": outcome.certainty.value,
            "status_code": outcome.status_code,
            "retry_after_seconds": outcome.retry_after_seconds,
            "location": outcome.location,
            "payload": outcome.payload,
        },
        field="dispatch",
    )
    if not isinstance(sanitized, dict):
        raise ValueError("sanitized dispatch outcome is not an object")
    return sanitized


def _dispatch_event_metadata(outcome: DispatchOutcome) -> dict[str, Any]:
    return {
        "certainty": outcome.certainty.value,
        "status_code": outcome.status_code,
        "retry_after_seconds": outcome.retry_after_seconds,
    }


def _remote_negotiation_id(stored_outcome: dict[str, Any]) -> str | None:
    payload = stored_outcome.get("payload")
    if not isinstance(payload, dict):
        return None
    for key in ("negotiation_id", "id"):
        value = payload.get(key)
        if (
            isinstance(value, str)
            and value
            and value != "redacted"
            and len(value) <= 200
            and "\0" not in value
        ):
            return value
    return None


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant {value} is forbidden")


def _normalized_json_dumps(vacancy: NormalizedVacancy) -> str:
    try:
        return json.dumps(
            redact_for_storage(vacancy.to_dict()),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("normalized vacancy is not canonical JSON") from exc


def _normalized_json_loads(value: Any, *, vacancy_id: str) -> dict[str, Any]:
    if type(value) is not str:
        raise StaleWrite("search result normalized_json is not persisted text")
    try:
        loaded = json.loads(value, parse_constant=_reject_json_constant)
        if not isinstance(loaded, dict):
            raise TypeError("normalized vacancy must contain an object")
        vacancy = NormalizedVacancy.from_dict(loaded)
        canonical = _normalized_json_dumps(vacancy)
    except (TypeError, ValueError) as exc:
        raise StaleWrite("invalid persisted normalized vacancy") from exc
    if vacancy.id != vacancy_id:
        raise StaleWrite("normalized vacancy id does not match search result")
    if canonical != value:
        raise StaleWrite("normalized vacancy storage is not canonical")
    return vacancy.to_dict()


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
        with self.immediate():
            return self._create_item_for_update(
                origin_run_id,
                account_id,
                vacancy_id,
                resume_id,
                query_key,
                idempotency_key=idempotency_key,
            )

    def _create_item_for_update(
        self,
        origin_run_id: int,
        account_id: str,
        vacancy_id: str,
        resume_id: str,
        query_key: str,
        *,
        idempotency_key: str | None = None,
    ) -> ItemRecord:
        """Create an already-validated item inside the caller's transaction."""
        resume_id = _canonical_identifier(resume_id, field="resume_id")
        expected_idempotency_key = hashlib.sha256(
            (
                account_id
                + "\0"
                + resume_id
                + "\0"
                + vacancy_id
                + "\0apply"
            ).encode("utf-8")
        ).hexdigest()
        if (
            idempotency_key is not None
            and idempotency_key != expected_idempotency_key
        ):
            raise ValueError("idempotency_key does not match canonical item identity")
        idempotency_key = expected_idempotency_key
        origin_run = self._run_for_update(origin_run_id)
        if origin_run.account_id != account_id:
            raise ValueError("item account_id must match its origin run")
        now = _utc_now()
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

    def record_filter_decision(
        self,
        item_id: int,
        *,
        expected_version: int,
        decision: FilterDecision,
        run_id: int,
        published_at: str = "",
        fencing_token: int | None = None,
    ) -> ItemRecord:
        item_id = _integer(item_id, field="item_id", minimum=1)
        expected_version = _integer(
            expected_version,
            field="expected_version",
        )
        if not isinstance(decision, FilterDecision):
            raise TypeError("decision must be a FilterDecision")
        run_id = _integer(run_id, field="run_id", minimum=1)
        published_at = _optional_text(
            published_at,
            field="published_at",
            maximum=128,
        )
        fencing_token = _optional_integer(
            fencing_token,
            field="fencing_token",
        )
        if fencing_token == 0:
            raise ValueError("fencing_token must be at least 1")
        target = (
            AutopilotState.ELIGIBLE
            if decision.passed
            else AutopilotState.SKIPPED
        )
        payload = decision.to_dict()
        filter_json = _json_dumps(payload, field="filter decision")
        sanitized_payload = _json_loads(
            filter_json,
            field="filter decision",
        )
        with self.immediate():
            instant = _instant(None, field="now")
            current = self._item_for_update(item_id)
            if current.version != expected_version:
                raise StaleWrite(f"item {item_id} version changed")
            if current.state is not AutopilotState.DISCOVERED:
                raise StaleWrite("filter decision requires a discovered item")
            self._assert_active_owned_run_for_update(
                current,
                run_id=run_id,
                fencing_token=fencing_token,
                instant=instant,
                operation="filter decision",
            )
            self._assert_candidate_set_unsealed_for_update(
                account_id=current.account_id,
                vacancy_id=current.vacancy_id,
                run_id=run_id,
                operation="filter decision",
            )
            assert_transition(current.state, target)
            cursor = self.conn.execute(
                """
                UPDATE hh_autopilot_items
                SET state = ?, version = version + 1, last_run_id = ?,
                    filter_json = ?, published_at = ?,
                    last_outcome_code = ?, updated_at = ?
                WHERE id = ? AND version = ? AND state = 'discovered'
                """,
                (
                    target.value,
                    run_id,
                    filter_json,
                    published_at,
                    decision.reason,
                    instant.isoformat(),
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
                reason=decision.reason,
                metadata=sanitized_payload,
                created_at=instant,
            )
            return self._item_for_update(item_id)

    def record_ranking_decision(
        self,
        item_id: int,
        *,
        expected_version: int,
        decision: RankingDecision,
        run_id: int,
        fencing_token: int | None = None,
    ) -> ItemRecord:
        item_id = _integer(item_id, field="item_id", minimum=1)
        expected_version = _integer(
            expected_version,
            field="expected_version",
        )
        if not isinstance(decision, RankingDecision):
            raise TypeError("decision must be a RankingDecision")
        run_id = _integer(run_id, field="run_id", minimum=1)
        fencing_token = _optional_integer(
            fencing_token,
            field="fencing_token",
        )
        if fencing_token == 0:
            raise ValueError("fencing_token must be at least 1")
        ai_payload = decision.to_dict()
        ai_json = _json_dumps(ai_payload, field="ranking AI decision")
        sanitized_payload = _json_loads(
            ai_json,
            field="ranking AI decision",
        )
        score = decision.rank_score.score
        if not math.isfinite(score) or not 0.0 <= score <= 100.0:
            raise ValueError("deterministic score must be finite in 0..100")
        with self.immediate():
            instant = _instant(None, field="now")
            current = self._item_for_update(item_id)
            if current.version != expected_version:
                raise StaleWrite(f"item {item_id} version changed")
            if current.state is not AutopilotState.ELIGIBLE:
                raise StaleWrite("ranking decision requires an eligible item")
            self._assert_active_owned_run_for_update(
                current,
                run_id=run_id,
                fencing_token=fencing_token,
                instant=instant,
                operation="ranking decision",
            )
            self._assert_candidate_set_unsealed_for_update(
                account_id=current.account_id,
                vacancy_id=current.vacancy_id,
                run_id=run_id,
                operation="ranking decision",
            )
            assert_transition(current.state, AutopilotState.RANKED)
            cursor = self.conn.execute(
                """
                UPDATE hh_autopilot_items
                SET state = 'ranked', version = version + 1, last_run_id = ?,
                    deterministic_score = ?, ai_json = ?,
                    last_outcome_code = ?, updated_at = ?
                WHERE id = ? AND version = ? AND state = 'eligible'
                """,
                (
                    run_id,
                    score,
                    ai_json,
                    decision.reason,
                    instant.isoformat(),
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
                target=AutopilotState.RANKED,
                reason=decision.reason,
                metadata=sanitized_payload,
                created_at=instant,
            )
            return self._item_for_update(item_id)

    def finalize_ranked_candidates(
        self,
        expected_versions: Mapping[int, int],
        *,
        selected_item_ids: Sequence[int],
        resume_policy: str,
        run_id: int,
        fencing_token: int | None = None,
    ) -> tuple[ItemRecord, ...]:
        if not isinstance(expected_versions, Mapping):
            raise TypeError("expected_versions must be a mapping")
        if not expected_versions or len(expected_versions) > 500:
            raise ValueError("expected_versions must contain 1..500 items")
        parsed_versions: dict[int, int] = {}
        for raw_item_id, raw_version in expected_versions.items():
            item_id = _integer(raw_item_id, field="item_id", minimum=1)
            if item_id in parsed_versions:
                raise ValueError("expected_versions contains a duplicate item")
            parsed_versions[item_id] = _integer(
                raw_version,
                field=f"expected_versions[{item_id}]",
            )
        if isinstance(selected_item_ids, (str, bytes)) or not isinstance(
            selected_item_ids,
            Sequence,
        ):
            raise TypeError("selected_item_ids must be a sequence")
        selected: set[int] = set()
        for index, raw_item_id in enumerate(selected_item_ids):
            item_id = _integer(
                raw_item_id,
                field=f"selected_item_ids[{index}]",
                minimum=1,
            )
            if item_id in selected:
                raise ValueError("selected_item_ids contains a duplicate")
            selected.add(item_id)
        if not selected or not selected <= set(parsed_versions):
            raise ValueError("selected items must be a nonempty candidate subset")
        if resume_policy not in {"best_resume_only", "per_resume"}:
            raise ValueError("unsupported resume policy")
        if resume_policy == "best_resume_only" and len(selected) != 1:
            raise ValueError("best_resume_only requires exactly one selected item")
        if resume_policy == "per_resume" and selected != set(parsed_versions):
            raise ValueError("per_resume must select every candidate")
        run_id = _integer(run_id, field="run_id", minimum=1)
        fencing_token = _optional_integer(
            fencing_token,
            field="fencing_token",
        )
        if fencing_token == 0:
            raise ValueError("fencing_token must be at least 1")
        ordered_ids = tuple(sorted(parsed_versions))
        with self.immediate():
            instant = _instant(None, field="now")
            supplied = tuple(
                self._item_for_update(item_id) for item_id in ordered_ids
            )
            for candidate in supplied:
                if candidate.version != parsed_versions[candidate.id]:
                    raise StaleWrite(f"item {candidate.id} version changed")
            accounts = {candidate.account_id for candidate in supplied}
            if len(accounts) != 1:
                raise ValueError("candidate items must share one account")
            vacancies = {candidate.vacancy_id for candidate in supplied}
            if len(vacancies) != 1:
                raise ValueError("candidate items must share one vacancy")
            if any(candidate.last_run_id != run_id for candidate in supplied):
                raise ValueError("candidate items must belong to the same run")
            account_id = next(iter(accounts))
            vacancy_id = next(iter(vacancies))
            for candidate in supplied:
                self._assert_active_owned_run_for_update(
                    candidate,
                    run_id=run_id,
                    fencing_token=fencing_token,
                    instant=instant,
                    operation="candidate finalization",
                )
            self._assert_candidate_set_unsealed_for_update(
                account_id=account_id,
                vacancy_id=vacancy_id,
                run_id=run_id,
                operation="candidate finalization",
            )

            rows = self.conn.execute(
                """
                SELECT * FROM hh_autopilot_items
                WHERE account_profile_id = ?
                  AND vacancy_id = ?
                  AND last_run_id = ?
                ORDER BY id ASC
                """,
                (account_id, vacancy_id, run_id),
            ).fetchall()
            candidates = tuple(self._item_from_row(row) for row in rows)
            qualifying: list[ItemRecord] = []
            for candidate in candidates:
                self._assert_active_owned_run_for_update(
                    candidate,
                    run_id=run_id,
                    fencing_token=fencing_token,
                    instant=instant,
                    operation="candidate finalization",
                )
                if candidate.state in {
                    AutopilotState.DISCOVERED,
                    AutopilotState.ELIGIBLE,
                    AutopilotState.RETRY_WAIT,
                }:
                    raise StaleWrite(
                        "candidate set is incomplete and can still change"
                    )
                if candidate.state is AutopilotState.SKIPPED:
                    try:
                        filter_decision = FilterDecision(
                            passed=candidate.filter_data["passed"],
                            reason=candidate.filter_data["reason"],
                            evidence=candidate.filter_data["evidence"],
                        )
                    except (KeyError, TypeError, ValueError) as exc:
                        raise StaleWrite(
                            "skipped candidate filter decision is malformed"
                        ) from exc
                    if filter_decision.passed:
                        raise StaleWrite(
                            "candidate set contains a nonterminal skipped item"
                        )
                    continue
                if candidate.state is not AutopilotState.RANKED:
                    raise StaleWrite(
                        "candidate set contains an incomplete state"
                    )
                ranking_decision = _ranking_decision_from_data(
                    candidate.ai_data,
                    field="candidate ranking decision",
                )
                if ranking_decision.retry:
                    raise StaleWrite(
                        "candidate set contains a retryable ranking decision"
                    )
                if ranking_decision.ready and not ranking_decision.retry:
                    qualifying.append(candidate)
            complete_ids = {candidate.id for candidate in qualifying}
            if set(parsed_versions) != complete_ids:
                raise StaleWrite(
                    "expected_versions must match the complete qualifying candidate set"
                )
            for candidate in qualifying:
                if candidate.version != parsed_versions[candidate.id]:
                    raise StaleWrite(f"item {candidate.id} version changed")
            stable_candidates = sorted(qualifying, key=_ranked_item_sort_key)
            if resume_policy == "best_resume_only":
                if selected != {stable_candidates[0].id}:
                    raise ValueError(
                        "selected item is not the stable best candidate"
                    )
            for candidate in stable_candidates:
                is_selected = candidate.id in selected
                target = (
                    AutopilotState.READY
                    if is_selected
                    else AutopilotState.SKIPPED
                )
                reason = "ready" if is_selected else "not_best_resume"
                self._transition_item_for_update(
                    candidate.id,
                    candidate.version,
                    target,
                    reason,
                    {
                        "resume_policy": resume_policy,
                        "selected": is_selected,
                    },
                    run_id=run_id,
                    fencing_token=fencing_token,
                    fence_instant=instant,
                    allow_stage_owned=True,
                )
            return tuple(
                self._item_for_update(candidate.id)
                for candidate in stable_candidates
            )

    def create_search_cycle(
        self,
        account_id: str,
        run_id: int,
        policy_hash: str,
        fencing_token: int,
        *,
        mode: str = "live",
    ) -> SearchCycleRecord:
        account_id = _canonical_identifier(account_id, field="account_id")
        run_id = _integer(run_id, field="run_id", minimum=1)
        policy_hash = _required_text(policy_hash, field="policy_hash")
        fencing_token = _integer(fencing_token, field="fencing_token", minimum=1)
        mode = self._search_mode(mode)
        instant = _instant(None, field="now")
        with self.immediate():
            existing = self.conn.execute(
                """
                SELECT * FROM hh_autopilot_search_cycles
                WHERE owner_run_id = ? AND status = 'running'
                ORDER BY id ASC
                """,
                (run_id,),
            ).fetchall()
            if len(existing) > 1:
                raise StaleWrite("run owns multiple running search cycles")
            if existing:
                cycle = self._search_cycle_from_row(existing[0])
                self._assert_search_runtime_for_update(
                    cycle,
                    account_id=account_id,
                    run_id=run_id,
                    policy_hash=policy_hash,
                    fencing_token=fencing_token,
                    mode=mode,
                    instant=instant,
                )
                return cycle
            run = self._run_for_update(run_id)
            self._assert_search_run_for_update(
                run,
                account_id=account_id,
                policy_hash=policy_hash,
                fencing_token=fencing_token,
                mode=mode,
                instant=instant,
            )
            now = instant.isoformat()
            cursor = self.conn.execute(
                """
                INSERT INTO hh_autopilot_search_cycles (
                    account_profile_id, policy_hash, origin_run_id, owner_run_id,
                    claim_version, fencing_token, mode, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 0, ?, ?, 'running', ?, ?)
                """,
                (
                    account_id,
                    policy_hash,
                    run_id,
                    run_id,
                    fencing_token,
                    mode,
                    now,
                    now,
                ),
            )
            return self._search_cycle_for_update(_required_lastrowid(cursor))

    def get_or_create_owned_cycle(
        self,
        request: SearchRequest | None = None,
        *,
        account_id: str | None = None,
        run_id: int | None = None,
        policy_hash: str | None = None,
        fencing_token: int | None = None,
        mode: str | None = None,
    ) -> SearchCycleRecord:
        if request is not None:
            if not isinstance(request, SearchRequest):
                raise TypeError("request must be a SearchRequest")
            if any(
                value is not None
                for value in (account_id, run_id, policy_hash, fencing_token, mode)
            ):
                raise ValueError("request cannot be combined with explicit cycle fields")
            account_id = request.account_id
            run_id = request.run_id
            policy_hash = request.policy_hash
            fencing_token = request.fencing_token
            mode = request.mode
        if None in (account_id, run_id, policy_hash, fencing_token, mode):
            raise ValueError("all search cycle fields are required")
        assert isinstance(account_id, str)
        assert isinstance(run_id, int)
        assert isinstance(policy_hash, str)
        assert isinstance(fencing_token, int)
        assert isinstance(mode, str)
        return self.create_search_cycle(
            account_id,
            run_id,
            policy_hash,
            fencing_token,
            mode=mode,
        )

    def get_search_cycle(self, cycle_id: int) -> SearchCycleRecord | None:
        cycle_id = _integer(cycle_id, field="cycle_id", minimum=1)
        row = self.conn.execute(
            "SELECT * FROM hh_autopilot_search_cycles WHERE id = ?", (cycle_id,)
        ).fetchone()
        return self._search_cycle_from_row(row) if row is not None else None

    def get_owned_search_cycle(self, run_id: int) -> SearchCycleRecord | None:
        run_id = _integer(run_id, field="run_id", minimum=1)
        rows = self.conn.execute(
            """
            SELECT * FROM hh_autopilot_search_cycles
            WHERE owner_run_id = ? AND status = 'running'
            ORDER BY id ASC
            """,
            (run_id,),
        ).fetchall()
        if len(rows) > 1:
            raise StaleWrite("run owns multiple running search cycles")
        return self._search_cycle_from_row(rows[0]) if rows else None

    def list_search_cycles(self, account_id: str | None = None) -> list[SearchCycleRecord]:
        if account_id is None:
            rows = self.conn.execute(
                "SELECT * FROM hh_autopilot_search_cycles ORDER BY id ASC"
            ).fetchall()
        else:
            account_id = _canonical_identifier(account_id, field="account_id")
            rows = self.conn.execute(
                """
                SELECT * FROM hh_autopilot_search_cycles
                WHERE account_profile_id = ? ORDER BY id ASC
                """,
                (account_id,),
            ).fetchall()
        return [self._search_cycle_from_row(row) for row in rows]

    def count_search_cycles(self, account_id: str | None = None) -> int:
        return len(self.list_search_cycles(account_id))

    def ensure_search_checkpoint(
        self,
        cycle_id: int,
        resume_id: str,
        query_key: str,
    ) -> SearchCheckpointRecord:
        cycle_id = _integer(cycle_id, field="cycle_id", minimum=1)
        resume_id = _canonical_identifier(resume_id, field="resume_id")
        query_key = _required_text(query_key, field="query_key")
        with self.immediate():
            cycle = self._search_cycle_for_update(cycle_id)
            if cycle.status != "running":
                raise StaleWrite("search cycle is not running")
            mode = self._search_cycle_origin_mode_for_update(cycle)
            self._assert_search_runtime_for_update(
                cycle,
                account_id=cycle.account_id,
                run_id=cycle.owner_run_id,
                policy_hash=cycle.policy_hash,
                fencing_token=cycle.fencing_token,
                mode=mode,
                instant=_instant(None, field="now"),
            )
            now = _utc_now()
            self.conn.execute(
                """
                INSERT INTO hh_autopilot_search_checkpoints (
                    cycle_id, resume_id, query_key, next_page,
                    unique_vacancy_count, status, updated_at
                ) VALUES (?, ?, ?, 0, 0, 'pending', ?)
                ON CONFLICT(cycle_id, resume_id, query_key) DO NOTHING
                """,
                (cycle_id, resume_id, query_key, now),
            )
            row = self.conn.execute(
                """
                SELECT * FROM hh_autopilot_search_checkpoints
                WHERE cycle_id = ? AND resume_id = ? AND query_key = ?
                """,
                (cycle_id, resume_id, query_key),
            ).fetchone()
            if row is None:
                raise StaleWrite("search checkpoint disappeared")
            return self._search_checkpoint_from_row(row)

    def get_checkpoint(
        self, cycle_id: int, resume_id: str, query_key: str
    ) -> SearchCheckpointRecord | None:
        cycle_id = _integer(cycle_id, field="cycle_id", minimum=1)
        resume_id = _canonical_identifier(resume_id, field="resume_id")
        query_key = _required_text(query_key, field="query_key")
        row = self.conn.execute(
            """
            SELECT * FROM hh_autopilot_search_checkpoints
            WHERE cycle_id = ? AND resume_id = ? AND query_key = ?
            """,
            (cycle_id, resume_id, query_key),
        ).fetchone()
        return self._search_checkpoint_from_row(row) if row is not None else None

    def list_search_checkpoints(self, cycle_id: int) -> list[SearchCheckpointRecord]:
        cycle_id = _integer(cycle_id, field="cycle_id", minimum=1)
        rows = self.conn.execute(
            """
            SELECT * FROM hh_autopilot_search_checkpoints
            WHERE cycle_id = ? ORDER BY id ASC
            """,
            (cycle_id,),
        ).fetchall()
        return [self._search_checkpoint_from_row(row) for row in rows]

    def initialize_search_cycle_distinct_cap(
        self,
        cycle_id: int,
        remaining_budget: int,
        *,
        fencing_token: int,
        mode: str | None = None,
        owner_run_id: int | None = None,
        expected_claim_version: int | None = None,
        policy_hash: str | None = None,
    ) -> SearchCycleRecord:
        cycle_id = _integer(cycle_id, field="cycle_id", minimum=1)
        remaining_budget = _integer(
            remaining_budget, field="remaining_budget"
        )
        fencing_token = _integer(
            fencing_token, field="fencing_token", minimum=1
        )
        if mode is not None:
            mode = self._search_mode(mode)
        owner_run_id = _optional_integer(owner_run_id, field="owner_run_id")
        if owner_run_id == 0:
            raise ValueError("owner_run_id must be at least 1")
        expected_claim_version = _optional_integer(
            expected_claim_version, field="expected_claim_version"
        )
        if policy_hash is not None:
            policy_hash = _required_text(policy_hash, field="policy_hash")
        instant = _instant(None, field="now")
        with self.immediate():
            cycle = self._search_cycle_for_update(cycle_id)
            if mode is None:
                mode = self._search_cycle_origin_mode_for_update(cycle)
            self._assert_search_commit_provenance(
                cycle,
                owner_run_id=owner_run_id,
                expected_claim_version=expected_claim_version,
                policy_hash=policy_hash,
                fencing_token=fencing_token,
                mode=mode,
                instant=instant,
            )
            current_distinct = len(
                {
                    result.vacancy_id
                    for result in self.list_search_results(cycle_id=cycle.id)
                }
            )
            stored_cap = cycle.distinct_vacancy_cap
            if stored_cap is not None and current_distinct > stored_cap:
                raise StaleWrite(
                    "search cycle distinct count exceeds its persisted cap"
                )
            proposed_cap = current_distinct + remaining_budget
            effective_cap = (
                proposed_cap
                if stored_cap is None
                else min(stored_cap, max(current_distinct, proposed_cap))
            )
            if stored_cap != effective_cap:
                cursor = self.conn.execute(
                    """
                    UPDATE hh_autopilot_search_cycles
                    SET distinct_vacancy_cap = ?, updated_at = ?
                    WHERE id = ? AND distinct_vacancy_cap IS ?
                    """,
                    (
                        effective_cap,
                        instant.isoformat(),
                        cycle.id,
                        stored_cap,
                    ),
                )
                if cursor.rowcount != 1:
                    raise StaleWrite(
                        "search cycle distinct cap compare-and-swap failed"
                    )
            return self._search_cycle_for_update(cycle.id)

    def commit_search_page(
        self,
        checkpoint_id: int,
        *,
        expected_next_page: int,
        page: SearchPage,
        normalized: Iterable[NormalizedVacancy],
        fencing_token: int,
        mode: str | None = None,
        owner_run_id: int | None = None,
        expected_claim_version: int | None = None,
        policy_hash: str | None = None,
        terminal: bool = False,
        absolute_distinct_cap: int | None = None,
    ) -> SearchPageCommitRecord:
        checkpoint_id = _integer(checkpoint_id, field="checkpoint_id", minimum=1)
        expected_next_page = _integer(expected_next_page, field="expected_next_page")
        if not isinstance(page, SearchPage):
            raise TypeError("page must be a SearchPage")
        if page.page != expected_next_page:
            raise ValueError("page.page must equal expected_next_page")
        fencing_token = _integer(fencing_token, field="fencing_token", minimum=1)
        if mode is not None:
            mode = self._search_mode(mode)
        owner_run_id = _optional_integer(owner_run_id, field="owner_run_id")
        if owner_run_id == 0:
            raise ValueError("owner_run_id must be at least 1")
        expected_claim_version = _optional_integer(
            expected_claim_version, field="expected_claim_version"
        )
        if policy_hash is not None:
            policy_hash = _required_text(policy_hash, field="policy_hash")
        if type(terminal) is not bool:
            raise TypeError("terminal must be a boolean")
        absolute_distinct_cap = _optional_integer(
            absolute_distinct_cap, field="absolute_distinct_cap"
        )
        if isinstance(normalized, (str, bytes)):
            raise TypeError("normalized must be an iterable of NormalizedVacancy")
        detached: list[tuple[NormalizedVacancy, str]] = []
        seen: set[str] = set()
        try:
            values = list(normalized)
        except TypeError as exc:
            raise TypeError("normalized must be iterable") from exc
        for index, vacancy in enumerate(values):
            if not isinstance(vacancy, NormalizedVacancy):
                raise TypeError(f"normalized[{index}] must be a NormalizedVacancy")
            if vacancy.id in seen:
                continue
            seen.add(vacancy.id)
            detached.append((vacancy, _normalized_json_dumps(vacancy)))
        instant = _instant(None, field="now")
        with self.immediate():
            checkpoint = self._search_checkpoint_for_update(checkpoint_id)
            cycle = self._search_cycle_for_update(checkpoint.cycle_id)
            if mode is None:
                mode = self._search_cycle_origin_mode_for_update(cycle)
            self._assert_search_commit_provenance(
                cycle,
                owner_run_id=owner_run_id,
                expected_claim_version=expected_claim_version,
                policy_hash=policy_hash,
                fencing_token=fencing_token,
                mode=mode,
                instant=instant,
            )
            if checkpoint.next_page != expected_next_page:
                raise StaleWrite("search checkpoint next_page changed")
            if checkpoint.status == "complete":
                raise StaleWrite("search checkpoint is complete")
            distinct_ids = {
                result.vacancy_id
                for result in self.list_search_results(cycle_id=cycle.id)
            }
            distinct_before = len(distinct_ids)
            stored_cap = cycle.distinct_vacancy_cap
            if stored_cap is not None and distinct_before > stored_cap:
                raise StaleWrite(
                    "search cycle distinct count exceeds its persisted cap"
                )
            if stored_cap is None and absolute_distinct_cap is None:
                raise StaleWrite(
                    "search cycle distinct cap must be initialized before commit"
                )
            if absolute_distinct_cap is not None:
                proposed_cap = max(distinct_before, absolute_distinct_cap)
                effective_cap = (
                    proposed_cap
                    if stored_cap is None
                    else min(stored_cap, proposed_cap)
                )
                if effective_cap != stored_cap:
                    cursor = self.conn.execute(
                        """
                        UPDATE hh_autopilot_search_cycles
                        SET distinct_vacancy_cap = ?, updated_at = ?
                        WHERE id = ? AND distinct_vacancy_cap IS ?
                        """,
                        (
                            effective_cap,
                            instant.isoformat(),
                            cycle.id,
                            stored_cap,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise StaleWrite(
                            "search cycle distinct cap compare-and-swap failed"
                        )
                    stored_cap = effective_cap
            inserted = 0
            accepted: list[str] = []
            for vacancy, normalized_json in detached:
                is_new_distinct = vacancy.id not in distinct_ids
                if (
                    is_new_distinct
                    and stored_cap is not None
                    and len(distinct_ids) >= stored_cap
                ):
                    continue
                cursor = self.conn.execute(
                    """
                    INSERT INTO hh_autopilot_search_results (
                        cycle_id, checkpoint_id, account_profile_id, resume_id,
                        query_key, vacancy_id, page, normalized_json, discovered_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(cycle_id, resume_id, query_key, vacancy_id) DO NOTHING
                    """,
                    (
                        cycle.id,
                        checkpoint.id,
                        cycle.account_id,
                        checkpoint.resume_id,
                        checkpoint.query_key,
                        vacancy.id,
                        page.page,
                        normalized_json,
                        instant.isoformat(),
                    ),
                )
                if cursor.rowcount != 1:
                    existing_row = self.conn.execute(
                        """
                        SELECT * FROM hh_autopilot_search_results
                        WHERE cycle_id = ? AND resume_id = ?
                          AND query_key = ? AND vacancy_id = ?
                        """,
                        (
                            cycle.id,
                            checkpoint.resume_id,
                            checkpoint.query_key,
                            vacancy.id,
                        ),
                    ).fetchone()
                    if existing_row is None:
                        raise StaleWrite(
                            "search result conflict could not be resolved"
                        )
                    existing = self._search_result_from_row(existing_row)
                    if (
                        existing.cycle_id != cycle.id
                        or existing.checkpoint_id != checkpoint.id
                        or existing.account_id != cycle.account_id
                        or existing.resume_id != checkpoint.resume_id
                        or existing.query_key != checkpoint.query_key
                        or existing.vacancy_id != vacancy.id
                    ):
                        raise StaleWrite(
                            "search result conflict provenance changed"
                        )
                    continue
                accepted.append(vacancy.id)
                inserted += 1
                if is_new_distinct:
                    distinct_ids.add(vacancy.id)
                if mode == "live":
                    self._create_item_for_update(
                        cycle.origin_run_id,
                        cycle.account_id,
                        vacancy.id,
                        checkpoint.resume_id,
                        checkpoint.query_key,
                    )
            effective_terminal = terminal or (
                stored_cap is not None
                and len(distinct_ids) >= stored_cap
            )
            target_status = "complete" if effective_terminal else "running"
            cursor = self.conn.execute(
                """
                UPDATE hh_autopilot_search_checkpoints
                SET next_page = ?, reported_total = ?,
                    unique_vacancy_count = unique_vacancy_count + ?,
                    status = ?, updated_at = ?
                WHERE id = ? AND next_page = ? AND status != 'complete'
                """,
                (
                    page.page + 1,
                    page.total,
                    inserted,
                    target_status,
                    instant.isoformat(),
                    checkpoint.id,
                    expected_next_page,
                ),
            )
            if cursor.rowcount != 1:
                raise StaleWrite("search checkpoint compare-and-swap failed")
            self.conn.execute(
                "UPDATE hh_autopilot_search_cycles SET updated_at = ? WHERE id = ?",
                (instant.isoformat(), cycle.id),
            )
            return SearchPageCommitRecord(
                checkpoint=self._search_checkpoint_for_update(checkpoint.id),
                accepted_vacancy_ids=tuple(accepted),
                inserted_reference_count=inserted,
                new_distinct_count=len(distinct_ids) - distinct_before,
                cycle_distinct_count=len(distinct_ids),
            )

    def complete_checkpoint(
        self,
        checkpoint_id: int,
        fencing_token: int,
        *,
        owner_run_id: int | None = None,
        expected_claim_version: int | None = None,
        policy_hash: str | None = None,
    ) -> SearchCheckpointRecord:
        checkpoint_id = _integer(checkpoint_id, field="checkpoint_id", minimum=1)
        fencing_token = _integer(fencing_token, field="fencing_token", minimum=1)
        owner_run_id = _optional_integer(owner_run_id, field="owner_run_id")
        expected_claim_version = _optional_integer(
            expected_claim_version, field="expected_claim_version"
        )
        if policy_hash is not None:
            policy_hash = _required_text(policy_hash, field="policy_hash")
        instant = _instant(None, field="now")
        with self.immediate():
            checkpoint = self._search_checkpoint_for_update(checkpoint_id)
            cycle = self._search_cycle_for_update(checkpoint.cycle_id)
            mode = self._search_cycle_origin_mode_for_update(cycle)
            self._assert_search_commit_provenance(
                cycle,
                owner_run_id=owner_run_id,
                expected_claim_version=expected_claim_version,
                policy_hash=policy_hash,
                fencing_token=fencing_token,
                mode=mode,
                instant=instant,
            )
            if checkpoint.status != "complete":
                self.conn.execute(
                    """
                    UPDATE hh_autopilot_search_checkpoints
                    SET status = 'complete', updated_at = ? WHERE id = ?
                    """,
                    (instant.isoformat(), checkpoint.id),
                )
            return self._search_checkpoint_for_update(checkpoint.id)

    def interrupt_search_cycle(
        self, cycle_id: int, fencing_token: int
    ) -> SearchCycleRecord:
        return self._change_search_cycle_status(
            cycle_id, fencing_token, expected="running", target="interrupted"
        )

    def complete_search_cycle(
        self, cycle_id: int, fencing_token: int
    ) -> SearchCycleRecord:
        return self._change_search_cycle_status(
            cycle_id, fencing_token, expected="running", target="complete"
        )

    def supersede_search_cycle(
        self, cycle_id: int, fencing_token: int
    ) -> SearchCycleRecord:
        return self._change_search_cycle_status(
            cycle_id,
            fencing_token,
            expected=("running", "interrupted"),
            target="superseded",
        )

    def _change_search_cycle_status(
        self,
        cycle_id: int,
        fencing_token: int,
        *,
        expected: str | tuple[str, ...],
        target: str,
    ) -> SearchCycleRecord:
        cycle_id = _integer(cycle_id, field="cycle_id", minimum=1)
        fencing_token = _integer(fencing_token, field="fencing_token", minimum=1)
        instant = _instant(None, field="now")
        expected_values = (expected,) if isinstance(expected, str) else expected
        with self.immediate():
            cycle = self._search_cycle_for_update(cycle_id)
            self._search_cycle_origin_mode_for_update(cycle)
            self._assert_search_owner_provenance_for_update(cycle)
            if cycle.status == target:
                return cycle
            if cycle.status not in expected_values:
                raise StaleWrite(f"search cycle cannot become {target}")
            self._assert_fence(cycle.account_id, fencing_token, instant)
            if cycle.fencing_token != fencing_token:
                raise LostLease("search cycle fencing token changed")
            cursor = self.conn.execute(
                """
                UPDATE hh_autopilot_search_cycles
                SET status = ?, updated_at = ?
                WHERE id = ? AND status = ? AND fencing_token = ?
                """,
                (target, instant.isoformat(), cycle.id, cycle.status, fencing_token),
            )
            if cursor.rowcount != 1:
                raise StaleWrite("search cycle status compare-and-swap failed")
            return self._search_cycle_for_update(cycle.id)

    def claim_search_cycle(
        self,
        cycle_id: int,
        *,
        expected_claim_version: int,
        new_run_id: int,
        policy_hash: str,
        fencing_token: int,
    ) -> SearchCycleRecord | None:
        cycle_id = _integer(cycle_id, field="cycle_id", minimum=1)
        expected_claim_version = _integer(
            expected_claim_version, field="expected_claim_version"
        )
        new_run_id = _integer(new_run_id, field="new_run_id", minimum=1)
        policy_hash = _required_text(policy_hash, field="policy_hash")
        fencing_token = _integer(fencing_token, field="fencing_token", minimum=1)
        instant = _instant(None, field="now")
        with self.immediate():
            cycle = self._search_cycle_for_update(cycle_id)
            if cycle.status not in {"running", "interrupted"}:
                raise StaleWrite(
                    "only an interrupted or stale running search cycle can be claimed"
                )
            if cycle.claim_version != expected_claim_version:
                raise StaleWrite("search cycle claim version changed")
            origin_mode = self._search_cycle_origin_mode_for_update(cycle)
            if origin_mode != "live":
                raise RepositoryAuthorizationDenied("shadow_search_cycle_not_claimable")
            old_owner = self._assert_search_owner_provenance_for_update(cycle)
            if cycle.status == "running" and cycle.fencing_token == fencing_token:
                raise StaleWrite("search cycle replacement fence did not change")
            run = self._run_for_update(new_run_id)
            if run.trigger != "recovery":
                raise RepositoryAuthorizationDenied("search_recovery_run_required")
            self._assert_search_run_for_update(
                run,
                account_id=cycle.account_id,
                policy_hash=policy_hash,
                fencing_token=fencing_token,
                mode="live",
                instant=instant,
            )
            another = self.conn.execute(
                """
                SELECT id FROM hh_autopilot_search_cycles
                WHERE owner_run_id = ? AND status = 'running' AND id != ?
                ORDER BY id ASC LIMIT 1
                """,
                (new_run_id, cycle.id),
            ).fetchone()
            if another is not None:
                raise StaleWrite("recovery run already owns a running search cycle")
            self._assert_no_running_search_siblings_for_update(
                cycle, old_owner=old_owner
            )
            if cycle.policy_hash != policy_hash:
                self._supersede_and_reset_search_items_for_update(
                    cycle,
                    run_id=new_run_id,
                    instant=instant,
                    expected_status=cycle.status,
                )
                self._interrupt_abandoned_search_owner_for_update(
                    old_owner, replacement_run_id=new_run_id, instant=instant
                )
                return None
            cursor = self.conn.execute(
                """
                UPDATE hh_autopilot_search_cycles
                SET owner_run_id = ?, claim_version = claim_version + 1,
                    fencing_token = ?, status = 'running', updated_at = ?
                WHERE id = ? AND status = ? AND claim_version = ?
                """,
                (
                    new_run_id,
                    fencing_token,
                    instant.isoformat(),
                    cycle.id,
                    cycle.status,
                    expected_claim_version,
                ),
            )
            if cursor.rowcount != 1:
                raise StaleWrite("search cycle claim compare-and-swap failed")
            self._interrupt_abandoned_search_owner_for_update(
                old_owner, replacement_run_id=new_run_id, instant=instant
            )
            return self._search_cycle_for_update(cycle.id)

    def save_shadow_result(
        self,
        run_id: int,
        account_id: str,
        vacancy_id: str,
        resume_id: str,
        *,
        filter_data: dict[str, Any],
        deterministic_score: float | int | None,
        ai_data: dict[str, Any] | None = None,
        would_apply: bool,
        fencing_token: int,
    ) -> ShadowResultRecord:
        run_id = _integer(run_id, field="run_id", minimum=1)
        account_id = _canonical_identifier(account_id, field="account_id")
        vacancy_id = _required_text(vacancy_id, field="vacancy_id")
        resume_id = _canonical_identifier(resume_id, field="resume_id")
        if not isinstance(filter_data, dict):
            raise TypeError("filter_data must be a dictionary")
        filter_json = _json_dumps(filter_data, field="filter_data")
        if ai_data is not None and not isinstance(ai_data, dict):
            raise TypeError("ai_data must be a dictionary or None")
        ai_json = _json_dumps(ai_data, field="ai_data")
        if deterministic_score is not None:
            if isinstance(deterministic_score, bool) or not isinstance(
                deterministic_score, (int, float)
            ):
                raise TypeError("deterministic_score must be numeric or None")
            deterministic_score = float(deterministic_score)
            if deterministic_score != deterministic_score or abs(deterministic_score) == float("inf"):
                raise ValueError("deterministic_score must be finite")
        if type(would_apply) is not bool:
            raise TypeError("would_apply must be a boolean")
        fencing_token = _integer(fencing_token, field="fencing_token", minimum=1)
        instant = _instant(None, field="now")
        with self.immediate():
            run = self._run_for_update(run_id)
            if (
                run.account_id != account_id
                or run.trigger != "shadow"
                or run.grant_id is not None
                or run.status != "running"
                or run.fencing_token != fencing_token
            ):
                raise RepositoryAuthorizationDenied("shadow_run_mismatch")
            self._assert_fence(account_id, fencing_token, instant)
            self.conn.execute(
                """
                INSERT INTO hh_autopilot_shadow_results (
                    run_id, account_profile_id, vacancy_id, resume_id,
                    filter_json, deterministic_score, ai_json,
                    would_apply, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, vacancy_id, resume_id) DO NOTHING
                """,
                (
                    run_id,
                    account_id,
                    vacancy_id,
                    resume_id,
                    filter_json,
                    deterministic_score,
                    ai_json,
                    int(would_apply),
                    instant.isoformat(),
                ),
            )
            row = self.conn.execute(
                """
                SELECT * FROM hh_autopilot_shadow_results
                WHERE run_id = ? AND vacancy_id = ? AND resume_id = ?
                """,
                (run_id, vacancy_id, resume_id),
            ).fetchone()
            if row is None:
                raise StaleWrite("shadow result disappeared")
            record = self._shadow_result_from_row(row)
            if (
                record.account_id != account_id
                or _json_dumps(record.filter_data, field="filter_data") != filter_json
                or record.deterministic_score != deterministic_score
                or _json_dumps(record.ai_data, field="ai_data") != ai_json
                or record.would_apply != would_apply
            ):
                raise StaleWrite("contradictory shadow result replay")
            return record

    def list_search_results(self, *, cycle_id: int) -> list[SearchResultRecord]:
        cycle_id = _integer(cycle_id, field="cycle_id", minimum=1)
        rows = self.conn.execute(
            """
            SELECT * FROM hh_autopilot_search_results
            WHERE cycle_id = ? ORDER BY id ASC
            """,
            (cycle_id,),
        ).fetchall()
        return [self._search_result_from_row(row) for row in rows]

    def count_search_results(
        self,
        run_id: int | None = None,
        *,
        cycle_id: int | None = None,
    ) -> int:
        cycle_id = _optional_integer(cycle_id, field="cycle_id")
        run_id = _optional_integer(run_id, field="run_id")
        if cycle_id is not None and cycle_id < 1:
            raise ValueError("cycle_id must be at least 1")
        if run_id is not None and run_id < 1:
            raise ValueError("run_id must be at least 1")
        if cycle_id is not None and run_id is not None:
            raise ValueError("supply cycle_id or run_id, not both")
        if cycle_id is not None:
            rows = self.conn.execute(
                "SELECT * FROM hh_autopilot_search_results WHERE cycle_id = ?",
                (cycle_id,),
            ).fetchall()
        elif run_id is not None:
            rows = self.conn.execute(
                """
                SELECT result.* FROM hh_autopilot_search_results AS result
                JOIN hh_autopilot_search_cycles AS cycle ON cycle.id = result.cycle_id
                WHERE cycle.origin_run_id = ?
                """,
                (run_id,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM hh_autopilot_search_results"
            ).fetchall()
        return len([self._search_result_from_row(row) for row in rows])

    def list_search_cycle_vacancy_ids(self, cycle_id: int) -> tuple[str, ...]:
        cycle_id = _integer(cycle_id, field="cycle_id", minimum=1)
        ordered: dict[str, None] = {}
        for result in self.list_search_results(cycle_id=cycle_id):
            ordered.setdefault(result.vacancy_id, None)
        return tuple(ordered)

    def count_items(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS total FROM hh_autopilot_items").fetchone()
        return _persisted_integer(row["total"], field="item count")

    def count_guards(self) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS total FROM hh_application_account_guards"
        ).fetchone()
        return _persisted_integer(row["total"], field="guard count")

    def count_application_attempts(self) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS total FROM hh_application_attempts"
        ).fetchone()
        return _persisted_integer(row["total"], field="application attempt count")

    def count_reservations(self) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS total FROM hh_autopilot_quota_reservations"
        ).fetchone()
        return _persisted_integer(row["total"], field="reservation count")

    def count_challenges(self) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS total FROM hh_autopilot_challenges"
        ).fetchone()
        return _persisted_integer(row["total"], field="challenge count")

    def count_applications(
        self,
        account_id: str,
        vacancy_id: str,
        resume_id: str,
    ) -> int:
        account_id = _canonical_identifier(account_id, field="account_id")
        vacancy_id = _required_text(vacancy_id, field="vacancy_id")
        resume_id = _canonical_identifier(resume_id, field="resume_id")
        row = self.conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM applications AS application
            JOIN jobs AS job ON job.id = application.job_id
            WHERE application.account_profile_id = ?
              AND job.source = 'hh' AND job.source_id = ?
              AND application.resume_id = ?
            """,
            (account_id, vacancy_id, resume_id),
        ).fetchone()
        return _persisted_integer(row["total"], field="application count")

    def get_guard(
        self,
        account_id: str,
        source: str,
        source_id: str,
    ) -> ApplicationGuardRecord | None:
        account_id = _canonical_identifier(account_id, field="account_id")
        source = _canonical_identifier(source, field="source")
        source_id = _required_text(source_id, field="source_id")
        row = self.conn.execute(
            """
            SELECT * FROM hh_application_account_guards
            WHERE account_profile_id = ? AND source = ? AND source_id = ?
            """,
            (account_id, source, source_id),
        ).fetchone()
        return self._guard_from_row(row) if row is not None else None

    def get_application_attempt(
        self,
        attempt_id: int | None,
    ) -> ApplicationAttemptRecord | None:
        if attempt_id is None:
            return None
        attempt_id = _integer(attempt_id, field="attempt_id", minimum=1)
        row = self.conn.execute(
            "SELECT * FROM hh_application_attempts WHERE id = ?",
            (attempt_id,),
        ).fetchone()
        return self._attempt_from_row(row) if row is not None else None

    def list_shadow_results(self, run_id: int) -> list[ShadowResultRecord]:
        run_id = _integer(run_id, field="run_id", minimum=1)
        rows = self.conn.execute(
            "SELECT * FROM hh_autopilot_shadow_results WHERE run_id = ? ORDER BY id ASC",
            (run_id,),
        ).fetchall()
        return [self._shadow_result_from_row(row) for row in rows]

    def count_shadow_results(self, run_id: int) -> int:
        return len(self.list_shadow_results(run_id))

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
        sanitized_metadata = _json_loads(metadata_json, field="metadata")
        with self.immediate():
            fence_instant = _instant(None, field="now")
            return self._transition_item_for_update(
                item_id,
                expected_version,
                target,
                reason,
                sanitized_metadata,
                run_id=run_id,
                fencing_token=fencing_token,
                fence_instant=fence_instant,
                allow_stage_owned=False,
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
        allow_stage_owned: bool = False,
    ) -> ItemRecord:
        """Apply a prevalidated item transition inside the caller's transaction."""
        current = self._item_for_update(item_id)
        if current.version != expected_version:
            raise StaleWrite(f"item {item_id} version changed")
        stage_owned_edges = {
            (AutopilotState.DISCOVERED, AutopilotState.ELIGIBLE),
            (AutopilotState.ELIGIBLE, AutopilotState.RANKED),
            (AutopilotState.RANKED, AutopilotState.READY),
        }
        if not allow_stage_owned and (current.state, target) in stage_owned_edges:
            raise ValueError("transition is stage-owned by the ranking pipeline")
        effective_run_id = current.last_run_id if run_id is None else run_id
        self._assert_active_owned_run_for_update(
            current,
            run_id=effective_run_id,
            fencing_token=fencing_token,
            instant=fence_instant,
            operation="item transition",
        )
        assert_transition(current.state, target)
        cursor = self.conn.execute(
            """
            UPDATE hh_autopilot_items
            SET state = ?, version = version + 1,
                last_run_id = ?,
                last_outcome_code = ?, updated_at = ?
            WHERE id = ? AND version = ?
            """,
            (
                target.value,
                effective_run_id,
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
            run_id=effective_run_id,
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

    def recovery_provenance(self, attempt_id: int) -> RecoveryProvenance:
        attempt = self.get_application_attempt(attempt_id)
        if attempt is None:
            raise KeyError(f"attempt {attempt_id} does not exist")
        return RecoveryProvenance(
            attempt_id=attempt.id,
            account_id=attempt.account_id,
            authorization_kind=attempt.authorization_kind,
            authorization_ref=attempt.authorization_ref,
            policy_hash=attempt.policy_hash,
        )

    def accounts_needing_recovery(
        self,
        account_id: str | None = None,
        *,
        now: datetime | str | None = None,
    ) -> tuple[str, ...]:
        instant = _instant(now, field="now").isoformat()
        parameters: list[Any] = [instant]
        account_clause = ""
        if account_id is not None:
            account_clause = " AND account_profile_id = ?"
            parameters.append(_canonical_identifier(account_id, field="account_id"))
        rows = self.conn.execute(
            """
            SELECT DISTINCT account_profile_id
            FROM hh_autopilot_items
            WHERE (
                state = 'applying'
                OR (
                    state = 'reconciling'
                    AND (next_attempt_at = '' OR next_attempt_at <= ?)
                )
            )
            """
            + account_clause
            + " ORDER BY account_profile_id",
            tuple(parameters),
        ).fetchall()
        return tuple(
            _persisted_text(
                row["account_profile_id"],
                field="recovery account_id",
                canonical=True,
            )
            for row in rows
        )

    def due_reconciliation_items(
        self,
        account_id: str,
        *,
        now: datetime | str | None = None,
        limit: int = 1000,
    ) -> list[ItemRecord]:
        account_id = _canonical_identifier(account_id, field="account_id")
        instant = _instant(now, field="now").isoformat()
        limit = _integer(limit, field="limit", minimum=1)
        rows = self.conn.execute(
            """
            SELECT * FROM hh_autopilot_items
            WHERE account_profile_id = ? AND state = 'reconciling'
              AND (next_attempt_at = '' OR next_attempt_at <= ?)
            ORDER BY next_attempt_at ASC, id ASC
            LIMIT ?
            """,
            (account_id, instant, limit),
        ).fetchall()
        return [self._item_from_row(row) for row in rows]

    def load_reconciliation_context(
        self,
        item_id: int,
        provenance: RecoveryProvenance,
        fencing_token: int,
        *,
        now: datetime | str | None = None,
    ) -> ReconciliationContext:
        item_id = _integer(item_id, field="item_id", minimum=1)
        if type(provenance) is not RecoveryProvenance:
            raise TypeError("provenance must be an exact RecoveryProvenance")
        fencing_token = _integer(fencing_token, field="fencing_token", minimum=1)
        instant = _instant(now, field="now")
        with self.immediate():
            self._assert_fence(provenance.account_id, fencing_token, instant)
            item = self._item_for_update(item_id)
            if item.account_id != provenance.account_id:
                raise StaleWrite("recovery item account changed")
            if item.state not in {
                AutopilotState.RECONCILING,
                AutopilotState.MANUAL_CHALLENGE,
            }:
                raise StaleWrite("item is not awaiting reconciliation")
            if item.active_attempt_id != provenance.attempt_id:
                raise StaleWrite("recovery item attempt changed")
            attempt = self.get_application_attempt(provenance.attempt_id)
            if attempt is None or (
                attempt.item_id != item.id
                or attempt.account_id != provenance.account_id
                or attempt.authorization_kind is not provenance.authorization_kind
                or attempt.authorization_ref != provenance.authorization_ref
                or attempt.policy_hash != provenance.policy_hash
                or attempt.status not in {"applying", "reconciling"}
            ):
                raise StaleWrite("immutable attempt provenance changed")
            reservation = self.active_reservation_for_attempt(attempt.id)
            if reservation is None or reservation.source != "dispatch":
                raise StaleWrite("reconciliation reservation is unavailable")
            if reservation.fencing_token != fencing_token:
                recovery_run_row = self.conn.execute(
                    """
                    SELECT id FROM hh_autopilot_runs
                    WHERE account_profile_id = ? AND trigger = 'recovery'
                      AND status = 'running' AND fencing_token = ?
                    ORDER BY id DESC LIMIT 1
                    """,
                    (item.account_id, fencing_token),
                ).fetchone()
                if recovery_run_row is None:
                    raise LostLease("current fence has no recovery run")
                reservation = self._adopt_reservation_for_recovery_for_update(
                    reservation.id,
                    _persisted_integer(
                        recovery_run_row["id"],
                        field="recovery run id",
                        minimum=1,
                    ),
                    fencing_token,
                    expected_fencing_token=reservation.fencing_token,
                    instant=instant,
                )

            challenge_id: int | None = None
            challenge_type = ""
            if item.challenge_id is not None:
                challenge = self.get_challenge(item.challenge_id)
                if challenge is None or challenge.account_id != item.account_id:
                    raise StaleWrite("reconciliation challenge changed")
                if challenge.status in {"open", "in_progress"}:
                    challenge_id = challenge.id
                    challenge_type = challenge.challenge_type
            return self._reconciliation_context_for_update(
                item,
                attempt,
                reservation,
                fencing_token=fencing_token,
                challenge_id=challenge_id,
                challenge_type=challenge_type,
            )

    def finalize_reconciled_applied(
        self,
        context: ReconciliationContext,
        *,
        remote_negotiation_id: str,
        fencing_token: int,
        now: datetime | str | None = None,
    ) -> ItemRecord:
        if type(context) is not ReconciliationContext:
            raise TypeError("context must be an exact ReconciliationContext")
        remote_negotiation_id = _required_text(
            remote_negotiation_id,
            field="remote_negotiation_id",
        )
        fencing_token = _integer(fencing_token, field="fencing_token", minimum=1)
        if context.prepared.fencing_token != fencing_token:
            raise LostLease("reconciliation context fence changed")
        return self.finalize_applied(
            context.prepared,
            DispatchOutcome(
                code="applied",
                certainty=DeliveryCertainty.DEFINITE_RESPONSE,
                payload={"id": remote_negotiation_id},
            ),
            now=now,
        )

    def finalize_external_application(
        self,
        context: ReconciliationContext,
        *,
        remote_negotiation_id: str,
        occurred_at: datetime | str,
        timezone_name: str,
        fencing_token: int,
        now: datetime | str | None = None,
    ) -> ItemRecord:
        if type(context) is not ReconciliationContext:
            raise TypeError("context must be an exact ReconciliationContext")
        remote_negotiation_id = _required_text(
            remote_negotiation_id,
            field="remote_negotiation_id",
        )
        occurred = _instant(occurred_at, field="occurred_at")
        timezone_name, local_timezone = _timezone(timezone_name)
        fencing_token = _integer(fencing_token, field="fencing_token", minimum=1)
        instant = _instant(now, field="now")
        prepared = context.prepared
        if prepared.fencing_token != fencing_token:
            raise LostLease("reconciliation context fence changed")
        payload = {
            "code": "duplicate_external",
            "remote_negotiation_id": remote_negotiation_id,
        }
        with self.immediate():
            current = self._validate_prepared_for_update(prepared, instant=instant)
            cursor = self.conn.execute(
                """
                UPDATE hh_application_attempts
                SET status = 'skipped', reason = 'duplicate_external',
                    raw_result_json = ?, finished_at = ?
                WHERE id = ? AND status IN ('applying','reconciling')
                """,
                (
                    _json_dumps(payload, field="external result"),
                    instant.isoformat(),
                    prepared.attempt_id,
                ),
            )
            if cursor.rowcount != 1:
                raise StaleWrite("external attempt finalization failed")
            self._change_reservation_state_for_update(
                prepared.reservation_id,
                fencing_token,
                target=QuotaReservationState.RELEASED,
                remote_negotiation_id=None,
                instant=instant,
            )
            if occurred.astimezone(local_timezone).date() == instant.astimezone(
                local_timezone
            ).date():
                existing_row = self.conn.execute(
                    """
                    SELECT * FROM hh_autopilot_quota_reservations
                    WHERE remote_negotiation_id = ?
                    """,
                    (remote_negotiation_id,),
                ).fetchone()
                if existing_row is None:
                    self.conn.execute(
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
                            prepared.account_id,
                            timezone_name,
                            occurred.astimezone(local_timezone).date().isoformat(),
                            fencing_token,
                            occurred.isoformat(),
                            occurred.isoformat(),
                        ),
                    )
                else:
                    existing = self._reservation_from_row(existing_row)
                    if (
                        existing.source != "external_sync"
                        or existing.account_id != prepared.account_id
                        or existing.state is not QuotaReservationState.CONSUMED
                    ):
                        raise StaleWrite("external negotiation quota conflicts")
            assert_transition(current.state, AutopilotState.SKIPPED)
            cursor = self.conn.execute(
                """
                UPDATE hh_autopilot_items
                SET state = 'skipped', retry_stage = 'application',
                    version = version + 1, next_attempt_at = '',
                    challenge_id = NULL,
                    last_outcome_code = 'duplicate_external', updated_at = ?
                WHERE id = ? AND version = ? AND active_attempt_id = ?
                  AND state = 'reconciling'
                """,
                (
                    instant.isoformat(),
                    current.id,
                    current.version,
                    prepared.attempt_id,
                ),
            )
            if cursor.rowcount != 1:
                raise StaleWrite("external item finalization failed")
            cursor = self.conn.execute(
                """
                UPDATE hh_application_account_guards
                SET status = 'external_applied', owner_attempt_id = NULL,
                    updated_at = ?
                WHERE account_profile_id = ? AND source = 'hh'
                  AND source_id = ? AND status = 'active'
                  AND owner_attempt_id = ?
                """,
                (
                    instant.isoformat(),
                    prepared.account_id,
                    prepared.vacancy_id,
                    prepared.attempt_id,
                ),
            )
            if cursor.rowcount != 1:
                raise StaleWrite("external guard finalization failed")
            self._finish_literal_target_for_update(
                prepared,
                succeeded=False,
                terminal=True,
                instant=instant,
            )
            self._insert_event(
                item_id=current.id,
                run_id=prepared.run_id,
                previous=current.state,
                target=AutopilotState.SKIPPED,
                reason="duplicate_external",
                metadata={"remote_negotiation_id": remote_negotiation_id},
                created_at=instant,
            )
            return self._item_for_update(current.id)

    def record_reconciliation_check(
        self,
        context: ReconciliationContext,
        *,
        fencing_token: int,
        max_checks: int,
        delay_seconds: int,
        max_attempts: int,
        challenge_expiry_hours: int,
        unclassifiable_remote_ids: Sequence[str],
        now: datetime | str | None = None,
    ) -> tuple[ItemRecord, ChallengeRecord | None]:
        if type(context) is not ReconciliationContext:
            raise TypeError("context must be an exact ReconciliationContext")
        fencing_token = _integer(fencing_token, field="fencing_token", minimum=1)
        max_checks = _integer(max_checks, field="max_checks", minimum=1)
        delay_seconds = _integer(delay_seconds, field="delay_seconds", minimum=1)
        max_attempts = _integer(max_attempts, field="max_attempts", minimum=1)
        challenge_expiry_hours = _integer(
            challenge_expiry_hours,
            field="challenge_expiry_hours",
            minimum=1,
        )
        remote_ids = tuple(
            _required_text(value, field="unclassifiable_remote_id")
            for value in unclassifiable_remote_ids
        )
        instant = _instant(now, field="now")
        prepared = context.prepared
        if prepared.fencing_token != fencing_token:
            raise LostLease("reconciliation context fence changed")
        with self.immediate():
            current = self._validate_prepared_for_update(prepared, instant=instant)
            reservation = self._reservation_for_update(prepared.reservation_id)
            if reservation.state is QuotaReservationState.RESERVED:
                self._change_reservation_state_for_update(
                    reservation.id,
                    fencing_token,
                    target=QuotaReservationState.HELD,
                    remote_negotiation_id=None,
                    instant=instant,
                )
            count = current.reconciliation_count + 1
            must_ask = bool(remote_ids) or context.initial_outcome_code == "duplicate"
            if count < max_checks:
                next_check = instant + timedelta(seconds=delay_seconds)
                cursor = self.conn.execute(
                    """
                    UPDATE hh_autopilot_items
                    SET reconciliation_count = ?, version = version + 1,
                        next_attempt_at = ?,
                        last_outcome_code = 'reconciliation_pending',
                        updated_at = ?
                    WHERE id = ? AND version = ? AND state = 'reconciling'
                    """,
                    (
                        count,
                        next_check.isoformat(),
                        instant.isoformat(),
                        current.id,
                        current.version,
                    ),
                )
                if cursor.rowcount != 1:
                    raise StaleWrite("reconciliation check compare-and-swap failed")
                self._insert_event(
                    item_id=current.id,
                    run_id=prepared.run_id,
                    previous=current.state,
                    target=current.state,
                    reason="reconciliation_pending",
                    metadata={"check": count},
                    created_at=instant,
                )
                return self._item_for_update(current.id), None

            if must_ask:
                cursor = self.conn.execute(
                    """
                    INSERT INTO hh_autopilot_challenges (
                        scope, challenge_type, account_profile_id, item_id,
                        reservation_id, sanitized_url, screenshot_path,
                        status, expires_at, metadata_json, created_at
                    ) VALUES ('item', 'ambiguous_application', ?, ?, ?, '', '',
                              'open', ?, ?, ?)
                    """,
                    (
                        current.account_id,
                        current.id,
                        prepared.reservation_id,
                        (instant + timedelta(hours=challenge_expiry_hours)).isoformat(),
                        _json_dumps(
                            {"remote_negotiation_ids": remote_ids},
                            field="ambiguity metadata",
                        ),
                        instant.isoformat(),
                    ),
                )
                challenge_id = _required_lastrowid(cursor)
                assert_transition(current.state, AutopilotState.MANUAL_CHALLENGE)
                changed = self.conn.execute(
                    """
                    UPDATE hh_autopilot_items
                    SET state = 'manual_challenge', version = version + 1,
                        reconciliation_count = ?, next_attempt_at = '',
                        challenge_id = ?,
                        last_outcome_code = 'ambiguous_application',
                        updated_at = ?
                    WHERE id = ? AND version = ? AND state = 'reconciling'
                    """,
                    (
                        count,
                        challenge_id,
                        instant.isoformat(),
                        current.id,
                        current.version,
                    ),
                )
                if changed.rowcount != 1:
                    raise StaleWrite("ambiguity item compare-and-swap failed")
                self._insert_event(
                    item_id=current.id,
                    run_id=prepared.run_id,
                    previous=current.state,
                    target=AutopilotState.MANUAL_CHALLENGE,
                    reason="ambiguous_application",
                    metadata={"remote_negotiation_ids": remote_ids},
                    created_at=instant,
                )
                return (
                    self._item_for_update(current.id),
                    self._challenge_for_update(challenge_id),
                )

            target = (
                AutopilotState.READY
                if current.application_attempt_count < max_attempts
                else AutopilotState.DEAD
            )
            reason = "confirmed_absent" if target is AutopilotState.READY else "retry_exhausted"
            self._change_reservation_state_for_update(
                prepared.reservation_id,
                fencing_token,
                target=QuotaReservationState.RELEASED,
                remote_negotiation_id=None,
                instant=instant,
            )
            cursor = self.conn.execute(
                """
                DELETE FROM hh_application_account_guards
                WHERE account_profile_id = ? AND source = 'hh'
                  AND source_id = ? AND status = 'active'
                  AND owner_attempt_id = ?
                """,
                (prepared.account_id, prepared.vacancy_id, prepared.attempt_id),
            )
            if cursor.rowcount != 1:
                raise StaleWrite("absence guard release failed")
            cursor = self.conn.execute(
                """
                UPDATE hh_application_attempts
                SET status = ?, reason = ?, finished_at = ?
                WHERE id = ? AND status IN ('applying','reconciling')
                """,
                (target.value, reason, instant.isoformat(), prepared.attempt_id),
            )
            if cursor.rowcount != 1:
                raise StaleWrite("absence attempt finalization failed")
            assert_transition(current.state, target)
            cursor = self.conn.execute(
                """
                UPDATE hh_autopilot_items
                SET state = ?, retry_stage = 'application',
                    version = version + 1, reconciliation_count = ?,
                    next_attempt_at = '', challenge_id = NULL,
                    last_outcome_code = ?, updated_at = ?
                WHERE id = ? AND version = ? AND state = 'reconciling'
                """,
                (
                    target.value,
                    count,
                    reason,
                    instant.isoformat(),
                    current.id,
                    current.version,
                ),
            )
            if cursor.rowcount != 1:
                raise StaleWrite("absence item compare-and-swap failed")
            self._finish_literal_target_for_update(
                prepared,
                succeeded=False,
                terminal=target is AutopilotState.DEAD,
                instant=instant,
            )
            self._insert_event(
                item_id=current.id,
                run_id=prepared.run_id,
                previous=current.state,
                target=target,
                reason=reason,
                metadata={"checks": count},
                created_at=instant,
            )
            return self._item_for_update(current.id), None

    def open_reconciliation_auth_challenge(
        self,
        context: ReconciliationContext,
        fencing_token: int,
        *,
        now: datetime | str | None = None,
    ) -> ChallengeRecord:
        if type(context) is not ReconciliationContext:
            raise TypeError("context must be an exact ReconciliationContext")
        fencing_token = _integer(fencing_token, field="fencing_token", minimum=1)
        instant = _instant(now, field="now")
        prepared = context.prepared
        if prepared.fencing_token != fencing_token:
            raise LostLease("reconciliation context fence changed")
        with self.immediate():
            current = self._validate_prepared_for_update(prepared, instant=instant)
            existing_row = self.conn.execute(
                """
                SELECT * FROM hh_autopilot_challenges
                WHERE account_profile_id = ? AND scope = 'account'
                  AND challenge_type = 'manual_auth'
                  AND status IN ('open','in_progress')
                ORDER BY id ASC LIMIT 1
                """,
                (prepared.account_id,),
            ).fetchone()
            if existing_row is None:
                cursor = self.conn.execute(
                    """
                    INSERT INTO hh_autopilot_challenges (
                        scope, challenge_type, account_profile_id, item_id,
                        reservation_id, sanitized_url, screenshot_path,
                        status, expires_at, metadata_json, created_at
                    ) VALUES ('account', 'manual_auth', ?, ?, ?, '', '',
                              'open', '', '{}', ?)
                    """,
                    (
                        prepared.account_id,
                        current.id,
                        prepared.reservation_id,
                        instant.isoformat(),
                    ),
                )
                challenge_id = _required_lastrowid(cursor)
            else:
                challenge_id = _persisted_integer(
                    existing_row["id"],
                    field="manual auth challenge id",
                    minimum=1,
                )
            reservation = self._reservation_for_update(prepared.reservation_id)
            if reservation.state is QuotaReservationState.RESERVED:
                self._change_reservation_state_for_update(
                    reservation.id,
                    fencing_token,
                    target=QuotaReservationState.HELD,
                    remote_negotiation_id=None,
                    instant=instant,
                )
            assert_transition(current.state, AutopilotState.MANUAL_CHALLENGE)
            cursor = self.conn.execute(
                """
                UPDATE hh_autopilot_items
                SET state = 'manual_challenge', version = version + 1,
                    next_attempt_at = '', challenge_id = ?,
                    last_outcome_code = 'manual_auth', updated_at = ?
                WHERE id = ? AND version = ? AND state = 'reconciling'
                """,
                (challenge_id, instant.isoformat(), current.id, current.version),
            )
            if cursor.rowcount != 1:
                raise StaleWrite("manual auth item compare-and-swap failed")
            self._insert_event(
                item_id=current.id,
                run_id=prepared.run_id,
                previous=current.state,
                target=AutopilotState.MANUAL_CHALLENGE,
                reason="manual_auth",
                metadata={},
                created_at=instant,
            )
            return self._challenge_for_update(challenge_id)

    def _reconciliation_context_for_update(
        self,
        item: ItemRecord,
        attempt: ApplicationAttemptRecord,
        reservation: QuotaReservationRecord,
        *,
        fencing_token: int,
        challenge_id: int | None = None,
        challenge_type: str = "",
    ) -> ReconciliationContext:
        if reservation.run_id != attempt.run_id or reservation.attempt_id != attempt.id:
            raise StaleWrite("reservation attempt provenance changed")
        dispatched_at = _instant(attempt.dispatched_at, field="attempt dispatched_at")
        return ReconciliationContext(
            prepared=PreparedDispatch(
                item_id=item.id,
                item_version=item.version,
                attempt_id=attempt.id,
                reservation_id=reservation.id,
                run_id=attempt.run_id,
                account_id=item.account_id,
                vacancy_id=item.vacancy_id,
                resume_id=item.resume_id,
                authorization_kind=attempt.authorization_kind,
                authorization_ref=attempt.authorization_ref,
                policy_hash=attempt.policy_hash,
                fencing_token=fencing_token,
                cover_letter_mode="none",
                timezone_name=reservation.timezone,
                attempt_count=item.application_attempt_count,
            ),
            dispatched_at=dispatched_at,
            initial_outcome_code=attempt.reason or item.last_outcome_code,
            challenge_id=challenge_id,
            challenge_type=challenge_type,
        )

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
                generation = (
                    _persisted_integer(
                        row["generation"],
                        field="grant generation high-water",
                    )
                    + 1
                )
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

    def create_one_shot_authorization(
        self,
        reference_id: str,
        *,
        authorization_type: str,
        account_id: str,
        targets: Sequence[tuple[str, str]],
        max_success: int,
        expires_at: datetime | str,
        now: datetime | str | None = None,
    ) -> None:
        reference_id = _required_text(reference_id, field="reference_id")
        if authorization_type not in {"manual", "canary"}:
            raise ValueError("authorization_type must be manual or canary")
        account_id = _canonical_identifier(account_id, field="account_id")
        if isinstance(targets, (str, bytes)) or not isinstance(targets, Sequence):
            raise TypeError("targets must be a sequence")
        if not targets or len(targets) > 500:
            raise ValueError("targets must contain 1..500 exact targets")
        parsed_targets: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for index, target in enumerate(targets):
            if not isinstance(target, tuple) or len(target) != 2:
                raise TypeError(
                    f"targets[{index}] must be a resume/vacancy tuple"
                )
            resume_id = _canonical_identifier(
                target[0],
                field=f"targets[{index}].resume_id",
            )
            vacancy_id = _required_text(
                target[1],
                field=f"targets[{index}].vacancy_id",
            )
            identity = (resume_id, vacancy_id)
            if identity in seen:
                raise ValueError("targets contains a duplicate exact target")
            seen.add(identity)
            parsed_targets.append(identity)
        max_success = _integer(
            max_success,
            field="max_success",
            minimum=1,
        )
        if max_success > len(parsed_targets):
            raise ValueError("max_success cannot exceed the immutable target count")
        if authorization_type == "canary" and (
            max_success != 1 or len(parsed_targets) != 1
        ):
            raise ValueError("canary requires exactly one target and one success")
        instant = _instant(now, field="now")
        expiry = _instant(expires_at, field="expires_at")
        if expiry <= instant:
            raise ValueError("one-shot authorization must expire in the future")

        with self.immediate():
            existing = self.conn.execute(
                """
                SELECT 1 FROM hh_autopilot_one_shot_authorizations
                WHERE reference_id = ?
                """,
                (reference_id,),
            ).fetchone()
            if existing is not None:
                raise StaleWrite("one-shot authorization reference already exists")
            self.conn.execute(
                """
                INSERT INTO hh_autopilot_one_shot_authorizations (
                    reference_id, authorization_type, account_profile_id,
                    max_success, consumed_success, active, created_at,
                    expires_at, finished_at
                ) VALUES (?, ?, ?, ?, 0, 1, ?, ?, '')
                """,
                (
                    reference_id,
                    authorization_type,
                    account_id,
                    max_success,
                    instant.isoformat(),
                    expiry.isoformat(),
                ),
            )
            self.conn.executemany(
                """
                INSERT INTO hh_autopilot_one_shot_targets (
                    authorization_ref, resume_id, vacancy_id, status,
                    active_attempt_id, updated_at
                ) VALUES (?, ?, ?, 'pending', NULL, ?)
                """,
                [
                    (
                        reference_id,
                        resume_id,
                        vacancy_id,
                        instant.isoformat(),
                    )
                    for resume_id, vacancy_id in parsed_targets
                ],
            )

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

    def prepare_dispatch(
        self,
        *,
        item_id: int,
        expected_version: int,
        authorization: LiteralConfirmation | LiveAuthorization,
        fencing_token: int,
        snapshot: DispatchConfigSnapshot,
        now: datetime | str | None = None,
    ) -> PreparedDispatch:
        """Atomically create the single durable boundary for one application POST."""
        from work_hunter.safety import require_hh_dispatch_authorization

        item_id = _integer(item_id, field="item_id", minimum=1)
        expected_version = _integer(
            expected_version,
            field="expected_version",
        )
        fencing_token = _integer(
            fencing_token,
            field="fencing_token",
            minimum=1,
        )
        if type(snapshot) is not DispatchConfigSnapshot:
            raise TypeError("snapshot must be an exact DispatchConfigSnapshot")
        if type(authorization) not in {LiteralConfirmation, LiveAuthorization}:
            raise PermissionError("typed HH application authorization required")
        instant = _instant(now, field="now")
        timezone_name, local_timezone = _timezone(snapshot.timezone_name)
        local_date = instant.astimezone(local_timezone).date().isoformat()

        with self.immediate():
            current = self._item_for_update(item_id)
            if current.version != expected_version:
                raise StaleWrite(f"item {item_id} version changed")
            if current.state is not AutopilotState.READY:
                raise StaleWrite("dispatch preparation requires a ready item")
            if snapshot.account_id != current.account_id:
                raise RepositoryAuthorizationDenied(
                    "authorization_state_mismatch"
                )
            require_hh_dispatch_authorization(
                authorization,
                account_id=current.account_id,
                lease_account_id=current.account_id,
                fencing_token=fencing_token,
            )
            self._assert_fence(current.account_id, fencing_token, instant)
            self._assert_cooldown_for_update(current.account_id, instant)
            if self._kill_switch_active_for_update(current.account_id):
                raise RepositoryAuthorizationDenied("kill_switch_active")

            run = self._run_for_update(current.last_run_id)
            if (
                run.account_id != current.account_id
                or run.status != "running"
                or run.fencing_token != fencing_token
            ):
                raise RepositoryAuthorizationDenied("run_not_active")

            authorization_kind: AuthorizationKind
            authorization_ref: str
            policy_hash: str
            literal_type = ""
            if type(authorization) is LiveAuthorization:
                live = authorization
                if (
                    not snapshot.enabled
                    or snapshot.authorization_generation is None
                ):
                    raise RepositoryAuthorizationDenied(
                        "authorization_state_mismatch"
                    )
                if snapshot.paused:
                    raise RepositoryAuthorizationDenied(
                        "autopilot_disabled_or_paused"
                    )
                if not snapshot.within_scheduler_window:
                    raise RepositoryAuthorizationDenied(
                        "outside_scheduler_window"
                    )
                if snapshot.policy_hash != live.policy_hash:
                    raise RepositoryAuthorizationDenied("policy_hash_mismatch")
                grant = self._active_grant_for_update(current.account_id)
                if (
                    grant is None
                    or grant.id != live.grant_id
                    or grant.generation != snapshot.authorization_generation
                ):
                    raise RepositoryAuthorizationDenied(
                        "authorization_state_mismatch"
                    )
                if grant.policy_hash != snapshot.policy_hash:
                    raise RepositoryAuthorizationDenied("policy_hash_mismatch")
                if self._pause_active_for_update(current.account_id):
                    raise RepositoryAuthorizationDenied(
                        "autopilot_disabled_or_paused"
                    )
                if (
                    live.scope != APPLICATION_SCOPE
                    or live.run_id != run.id
                    or live.fencing_token != fencing_token
                    or run.grant_id != grant.id
                    or run.policy_hash != grant.policy_hash
                ):
                    raise RepositoryAuthorizationDenied(
                        "authorization_state_mismatch"
                    )
                authorization_kind = AuthorizationKind.AUTOPILOT
                authorization_ref = str(grant.id)
                policy_hash = grant.policy_hash
            else:
                literal = cast(LiteralConfirmation, authorization)
                row = self.conn.execute(
                    """
                    SELECT * FROM hh_autopilot_one_shot_authorizations
                    WHERE reference_id = ?
                    """,
                    (literal.reference_id,),
                ).fetchone()
                if row is None:
                    raise RepositoryAuthorizationDenied(
                        "literal_authorization_inactive"
                    )
                if (
                    str(row["account_profile_id"]) != current.account_id
                    or _persisted_integer(
                        row["active"],
                        field="one-shot active",
                    )
                    != 1
                ):
                    raise RepositoryAuthorizationDenied(
                        "literal_authorization_inactive"
                    )
                expiry = _instant(
                    str(row["expires_at"]),
                    field="one-shot expires_at",
                )
                consumed = _persisted_integer(
                    row["consumed_success"],
                    field="one-shot consumed_success",
                )
                maximum = _persisted_integer(
                    row["max_success"],
                    field="one-shot max_success",
                    minimum=1,
                )
                if expiry <= instant or consumed >= maximum:
                    raise RepositoryAuthorizationDenied(
                        "literal_authorization_inactive"
                    )
                literal_type = _persisted_text(
                    row["authorization_type"],
                    field="one-shot authorization_type",
                )
                expected_trigger = (
                    "canary" if literal_type == "canary" else "manual"
                )
                if run.trigger != expected_trigger:
                    raise RepositoryAuthorizationDenied(
                        "literal_authorization_inactive"
                    )
                target = self.conn.execute(
                    """
                    SELECT status, active_attempt_id
                    FROM hh_autopilot_one_shot_targets
                    WHERE authorization_ref = ? AND resume_id = ?
                      AND vacancy_id = ?
                    """,
                    (
                        literal.reference_id,
                        current.resume_id,
                        current.vacancy_id,
                    ),
                ).fetchone()
                if target is None:
                    raise RepositoryAuthorizationDenied(
                        "literal_target_mismatch"
                    )
                if str(target["status"]) != "pending":
                    raise RepositoryAuthorizationDenied(
                        "literal_target_inactive"
                    )
                if target["active_attempt_id"] is not None:
                    raise StaleWrite("literal target already owns an attempt")
                active_targets = self.conn.execute(
                    """
                    SELECT COUNT(*) AS total
                    FROM hh_autopilot_one_shot_targets
                    WHERE authorization_ref = ? AND status = 'active'
                    """,
                    (literal.reference_id,),
                ).fetchone()
                active_count = _persisted_integer(
                    active_targets["total"],
                    field="one-shot active target count",
                )
                if consumed + active_count >= maximum:
                    raise RepositoryAuthorizationDenied(
                        "literal_authorization_inactive"
                    )
                authorization_kind = AuthorizationKind.LITERAL_CONFIRMATION
                authorization_ref = literal.reference_id
                policy_hash = run.policy_hash

            job = self.conn.execute(
                """
                SELECT id FROM jobs
                WHERE source = 'hh' AND source_id = ?
                """,
                (current.vacancy_id,),
            ).fetchone()
            if job is not None:
                duplicate = self.conn.execute(
                    """
                    SELECT 1 FROM applications
                    WHERE account_profile_id = ? AND job_id = ?
                      AND resume_id = ?
                    """,
                    (
                        current.account_id,
                        _persisted_integer(
                            job["id"],
                            field="job id",
                            minimum=1,
                        ),
                        current.resume_id,
                    ),
                ).fetchone()
                if duplicate is not None:
                    raise StaleWrite("exact application already exists")

            guard_row = self.conn.execute(
                """
                SELECT * FROM hh_application_account_guards
                WHERE account_profile_id = ? AND source = 'hh'
                  AND source_id = ?
                """,
                (current.account_id, current.vacancy_id),
            ).fetchone()
            existing_guard = (
                None if guard_row is None else self._guard_from_row(guard_row)
            )
            if existing_guard is not None:
                if existing_guard.status == "active":
                    raise StaleWrite("account-vacancy guard is active")
                if snapshot.resume_policy == "best_resume_only":
                    raise StaleWrite("account-vacancy guard is terminal")

            cursor = self.conn.execute(
                """
                INSERT INTO hh_application_attempts (
                    run_id, campaign_item_id, vacancy_id, resume_id, status,
                    reason, letter, raw_result_json, created_at,
                    account_profile_id, autopilot_run_id, autopilot_item_id,
                    autopilot_attempt_id, authorization_kind,
                    authorization_ref, policy_hash, delivery_certainty,
                    dispatched_at, finished_at
                ) VALUES (
                    NULL, NULL, ?, ?, 'applying', '', '', '{}', ?,
                    ?, ?, ?, NULL, ?, ?, ?, '', ?, ''
                )
                """,
                (
                    current.vacancy_id,
                    current.resume_id,
                    instant.isoformat(),
                    current.account_id,
                    run.id,
                    current.id,
                    authorization_kind.value,
                    authorization_ref,
                    policy_hash,
                    instant.isoformat(),
                ),
            )
            attempt_id = _required_lastrowid(cursor)
            cursor = self.conn.execute(
                """
                UPDATE hh_application_attempts
                SET autopilot_attempt_id = ?
                WHERE id = ? AND autopilot_attempt_id IS NULL
                """,
                (attempt_id, attempt_id),
            )
            if cursor.rowcount != 1:
                raise StaleWrite("attempt compatibility identity update failed")

            if existing_guard is None:
                self.conn.execute(
                    """
                    INSERT INTO hh_application_account_guards (
                        account_profile_id, source, source_id,
                        owner_attempt_id, first_resume_id, status,
                        application_id, application_count, created_at,
                        updated_at
                    ) VALUES (?, 'hh', ?, ?, ?, 'active', NULL, 0, ?, ?)
                    """,
                    (
                        current.account_id,
                        current.vacancy_id,
                        attempt_id,
                        current.resume_id,
                        instant.isoformat(),
                        instant.isoformat(),
                    ),
                )
            else:
                cursor = self.conn.execute(
                    """
                    UPDATE hh_application_account_guards
                    SET owner_attempt_id = ?, status = 'active',
                        updated_at = ?
                    WHERE account_profile_id = ? AND source = 'hh'
                      AND source_id = ? AND status != 'active'
                    """,
                    (
                        attempt_id,
                        instant.isoformat(),
                        current.account_id,
                        current.vacancy_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise StaleWrite("account-vacancy guard changed")

            reservation = self._reserve_quota_for_update(
                account_id=current.account_id,
                run_id=run.id,
                attempt_id=attempt_id,
                timezone_name=timezone_name,
                local_date=local_date,
                daily_limit=snapshot.daily_limit,
                run_limit=snapshot.run_limit,
                fencing_token=fencing_token,
                instant=instant,
            )
            if type(authorization) is LiteralConfirmation:
                cursor = self.conn.execute(
                    """
                    UPDATE hh_autopilot_one_shot_targets
                    SET status = 'active', active_attempt_id = ?,
                        updated_at = ?
                    WHERE authorization_ref = ? AND resume_id = ?
                      AND vacancy_id = ? AND status = 'pending'
                      AND active_attempt_id IS NULL
                    """,
                    (
                        attempt_id,
                        instant.isoformat(),
                        authorization_ref,
                        current.resume_id,
                        current.vacancy_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise StaleWrite("literal target changed")

            assert_transition(current.state, AutopilotState.APPLYING)
            cursor = self.conn.execute(
                """
                UPDATE hh_autopilot_items
                SET state = 'applying', retry_stage = 'application',
                    version = version + 1,
                    application_attempt_count = application_attempt_count + 1,
                    active_attempt_id = ?, challenge_id = NULL,
                    next_attempt_at = '', last_outcome_code = 'dispatch_prepared',
                    updated_at = ?
                WHERE id = ? AND version = ? AND state = 'ready'
                """,
                (
                    attempt_id,
                    instant.isoformat(),
                    current.id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                raise StaleWrite("dispatch item compare-and-swap failed")
            self._insert_event(
                item_id=current.id,
                run_id=run.id,
                previous=AutopilotState.READY,
                target=AutopilotState.APPLYING,
                reason="dispatch_prepared",
                metadata={
                    "attempt_id": attempt_id,
                    "reservation_id": reservation.id,
                },
                created_at=instant,
            )
            prepared_item = self._item_for_update(current.id)
            return PreparedDispatch(
                item_id=current.id,
                item_version=prepared_item.version,
                attempt_id=attempt_id,
                reservation_id=reservation.id,
                run_id=run.id,
                account_id=current.account_id,
                vacancy_id=current.vacancy_id,
                resume_id=current.resume_id,
                authorization_kind=authorization_kind,
                authorization_ref=authorization_ref,
                policy_hash=policy_hash,
                fencing_token=fencing_token,
                cover_letter_mode=snapshot.cover_letter_mode,
                timezone_name=timezone_name,
                attempt_count=prepared_item.application_attempt_count,
            )

    def finalize_applied(
        self,
        prepared: PreparedDispatch,
        outcome: DispatchOutcome,
        *,
        now: datetime | str | None = None,
    ) -> ItemRecord:
        if type(prepared) is not PreparedDispatch:
            raise TypeError("prepared must be an exact PreparedDispatch")
        if type(outcome) is not DispatchOutcome:
            raise TypeError("outcome must be an exact DispatchOutcome")
        if (
            outcome.code != "applied"
            or outcome.certainty is not DeliveryCertainty.DEFINITE_RESPONSE
        ):
            raise ValueError("finalize_applied requires a definite applied outcome")
        instant = _instant(now, field="now")
        stored_outcome = _dispatch_storage_payload(outcome)
        outcome_json = _json_dumps(stored_outcome, field="dispatch outcome")

        with self.immediate():
            return self._finalize_applied_for_update(
                prepared,
                outcome,
                stored_outcome=stored_outcome,
                outcome_json=outcome_json,
                instant=instant,
            )

    def _finalize_applied_for_update(
        self,
        prepared: PreparedDispatch,
        outcome: DispatchOutcome,
        *,
        stored_outcome: dict[str, Any],
        outcome_json: str,
        instant: datetime,
        allowed_states: frozenset[AutopilotState] | None = None,
        reason: str = "applied",
    ) -> ItemRecord:
        allowed = allowed_states or frozenset(
            {AutopilotState.APPLYING, AutopilotState.RECONCILING}
        )
        current = self._validate_prepared_for_update(
            prepared,
            instant=instant,
            allowed_states=allowed,
        )
        job_id = self._ensure_hh_job_for_update(current, instant=instant)
        cursor = self.conn.execute(
            """
            INSERT INTO applications (
                account_profile_id, job_id, status, notes, applied_at,
                updated_at, source, source_id, resume_id, resume_hash,
                plan_id, transport, sent_at, result_json, error,
                autopilot_run_id, autopilot_item_id, autopilot_attempt_id
            ) VALUES (
                ?, ?, 'applied', '', ?, ?, 'hh', ?, ?, '', NULL,
                'hh_autopilot', ?, ?, '', ?, ?, ?
            )
            """,
            (
                prepared.account_id,
                job_id,
                instant.isoformat(),
                instant.isoformat(),
                prepared.vacancy_id,
                prepared.resume_id,
                instant.isoformat(),
                outcome_json,
                prepared.run_id,
                prepared.item_id,
                prepared.attempt_id,
            ),
        )
        application_id = _required_lastrowid(cursor)
        self._finish_attempt_for_update(
            prepared,
            outcome,
            stored_outcome=stored_outcome,
            status="applied",
            instant=instant,
        )
        remote_id = _remote_negotiation_id(stored_outcome)
        self._change_reservation_state_for_update(
            prepared.reservation_id,
            prepared.fencing_token,
            target=QuotaReservationState.CONSUMED,
            remote_negotiation_id=remote_id,
            instant=instant,
        )
        if current.state not in {
            AutopilotState.MANUAL_CHALLENGE,
            AutopilotState.DEAD,
        }:
            assert_transition(current.state, AutopilotState.APPLIED)
        cursor = self.conn.execute(
            """
            UPDATE hh_autopilot_items
            SET state = 'applied', version = version + 1,
                next_attempt_at = '', challenge_id = NULL,
                last_outcome_code = 'applied', updated_at = ?
            WHERE id = ? AND version = ? AND active_attempt_id = ?
              AND state = ?
            """,
            (
                instant.isoformat(),
                prepared.item_id,
                prepared.item_version,
                prepared.attempt_id,
                current.state.value,
            ),
        )
        if cursor.rowcount != 1:
            raise StaleWrite("applied item compare-and-swap failed")
        cursor = self.conn.execute(
            """
            UPDATE hh_application_account_guards
            SET status = 'applied', application_id = ?,
                application_count = application_count + 1,
                updated_at = ?
            WHERE account_profile_id = ? AND source = 'hh'
              AND source_id = ? AND status = 'active'
              AND owner_attempt_id = ?
            """,
            (
                application_id,
                instant.isoformat(),
                prepared.account_id,
                prepared.vacancy_id,
                prepared.attempt_id,
            ),
        )
        if cursor.rowcount != 1:
            raise StaleWrite("application guard finalization failed")
        self._increment_success_counter_for_update(
            prepared.run_id,
            instant=instant,
        )
        self._finish_literal_target_for_update(
            prepared,
            succeeded=True,
            instant=instant,
        )
        self._insert_event(
            item_id=prepared.item_id,
            run_id=prepared.run_id,
            previous=current.state,
            target=AutopilotState.APPLIED,
            reason=reason,
            metadata=_dispatch_event_metadata(outcome),
            created_at=instant,
        )
        return self._item_for_update(prepared.item_id)

    def record_possibly_sent(
        self,
        prepared: PreparedDispatch,
        outcome: DispatchOutcome,
        decision: RetryDecision,
        *,
        now: datetime | str | None = None,
    ) -> ItemRecord:
        if type(prepared) is not PreparedDispatch:
            raise TypeError("prepared must be an exact PreparedDispatch")
        if type(outcome) is not DispatchOutcome:
            raise TypeError("outcome must be an exact DispatchOutcome")
        if type(decision) is not RetryDecision:
            raise TypeError("decision must be an exact RetryDecision")
        if decision.target is not AutopilotState.RECONCILING:
            raise ValueError("ambiguous dispatch must reconcile")
        if outcome.certainty is not DeliveryCertainty.POSSIBLY_SENT and (
            outcome.code not in {"duplicate", "ambiguous_remote_result"}
        ):
            raise ValueError("record_possibly_sent requires an ambiguous outcome")
        instant = _instant(now, field="now")
        stored_outcome = _dispatch_storage_payload(outcome)

        with self.immediate():
            current = self._validate_prepared_for_update(
                prepared,
                instant=instant,
            )
            self._finish_attempt_for_update(
                prepared,
                outcome,
                stored_outcome=stored_outcome,
                status="reconciling",
                instant=instant,
                terminal=False,
            )
            self._change_reservation_state_for_update(
                prepared.reservation_id,
                prepared.fencing_token,
                target=QuotaReservationState.HELD,
                remote_negotiation_id=None,
                instant=instant,
            )
            assert_transition(current.state, AutopilotState.RECONCILING)
            cursor = self.conn.execute(
                """
                UPDATE hh_autopilot_items
                SET state = 'reconciling', retry_stage = 'reconciliation',
                    version = version + 1, next_attempt_at = ?,
                    last_outcome_code = ?, updated_at = ?
                WHERE id = ? AND version = ? AND active_attempt_id = ?
                  AND state = 'applying'
                """,
                (
                    decision.next_attempt_at,
                    outcome.code,
                    instant.isoformat(),
                    prepared.item_id,
                    prepared.item_version,
                    prepared.attempt_id,
                ),
            )
            if cursor.rowcount != 1:
                raise StaleWrite("reconciling item compare-and-swap failed")
            self._insert_event(
                item_id=prepared.item_id,
                run_id=prepared.run_id,
                previous=current.state,
                target=AutopilotState.RECONCILING,
                reason=outcome.code,
                metadata=_dispatch_event_metadata(outcome),
                created_at=instant,
            )
            return self._item_for_update(prepared.item_id)

    def finalize_definite_failure(
        self,
        prepared: PreparedDispatch,
        outcome: DispatchOutcome,
        decision: RetryDecision,
        *,
        challenge_expiry_hours: int,
        now: datetime | str | None = None,
    ) -> ItemRecord:
        if type(prepared) is not PreparedDispatch:
            raise TypeError("prepared must be an exact PreparedDispatch")
        if type(outcome) is not DispatchOutcome:
            raise TypeError("outcome must be an exact DispatchOutcome")
        if type(decision) is not RetryDecision:
            raise TypeError("decision must be an exact RetryDecision")
        if outcome.certainty is DeliveryCertainty.POSSIBLY_SENT:
            raise ValueError("possibly-sent outcomes cannot use definite finalization")
        if decision.target not in {
            AutopilotState.RETRY_WAIT,
            AutopilotState.SKIPPED,
            AutopilotState.DEAD,
            AutopilotState.MANUAL_CHALLENGE,
        }:
            raise ValueError("unsupported definite failure target")
        challenge_expiry_hours = _integer(
            challenge_expiry_hours,
            field="challenge_expiry_hours",
            minimum=1,
        )
        instant = _instant(now, field="now")
        stored_outcome = _dispatch_storage_payload(outcome)
        challenge_url = ""
        if decision.target is AutopilotState.MANUAL_CHALLENGE:
            from .challenges import sanitize_hh_url

            challenge_url = sanitize_hh_url(str(stored_outcome.get("location", "")))

        with self.immediate():
            current = self._validate_prepared_for_update(
                prepared,
                instant=instant,
            )
            self._finish_attempt_for_update(
                prepared,
                outcome,
                stored_outcome=stored_outcome,
                status=decision.target.value,
                instant=instant,
            )
            self._change_reservation_state_for_update(
                prepared.reservation_id,
                prepared.fencing_token,
                target=QuotaReservationState.RELEASED,
                remote_negotiation_id=None,
                instant=instant,
            )
            cursor = self.conn.execute(
                """
                DELETE FROM hh_application_account_guards
                WHERE account_profile_id = ? AND source = 'hh'
                  AND source_id = ? AND status = 'active'
                  AND owner_attempt_id = ?
                """,
                (
                    prepared.account_id,
                    prepared.vacancy_id,
                    prepared.attempt_id,
                ),
            )
            if cursor.rowcount != 1:
                raise StaleWrite("failure guard release failed")

            challenge_id: int | None = None
            if decision.target is AutopilotState.MANUAL_CHALLENGE:
                challenge_type = (
                    "manual_auth"
                    if outcome.code == "auth_expired"
                    else outcome.code
                )
                scope = "account" if challenge_type == "manual_auth" else "item"
                expiry = (
                    ""
                    if scope == "account"
                    else (
                        instant + timedelta(hours=challenge_expiry_hours)
                    ).isoformat()
                )
                cursor = self.conn.execute(
                    """
                    INSERT INTO hh_autopilot_challenges (
                        scope, challenge_type, account_profile_id, item_id,
                        reservation_id, sanitized_url, screenshot_path,
                        status, expires_at, metadata_json, created_at
                    ) VALUES (?, ?, ?, ?, NULL, ?, '', 'open', ?, ?, ?)
                    """,
                    (
                        scope,
                        challenge_type,
                        prepared.account_id,
                        prepared.item_id,
                        challenge_url,
                        expiry,
                        _json_dumps(
                            _dispatch_event_metadata(outcome),
                            field="challenge metadata",
                        ),
                        instant.isoformat(),
                    ),
                )
                challenge_id = _required_lastrowid(cursor)

            if outcome.code in {"rate_limited", "hh_daily_limit"}:
                self._set_account_cooldown_for_update(
                    account_id=prepared.account_id,
                    blocked_until=_instant(
                        decision.next_attempt_at,
                        field="cooldown next_attempt_at",
                    ),
                    reason=outcome.code,
                    fencing_token=prepared.fencing_token,
                    hh_reset_json=_json_dumps(
                        {
                            "retry_after_seconds": outcome.retry_after_seconds,
                        },
                        field="hh_reset",
                    ),
                    instant=instant,
                )

            assert_transition(current.state, decision.target)
            cursor = self.conn.execute(
                """
                UPDATE hh_autopilot_items
                SET state = ?, retry_stage = 'application',
                    version = version + 1, next_attempt_at = ?,
                    challenge_id = ?, last_outcome_code = ?, updated_at = ?
                WHERE id = ? AND version = ? AND active_attempt_id = ?
                  AND state = 'applying'
                """,
                (
                    decision.target.value,
                    decision.next_attempt_at,
                    challenge_id,
                    decision.reason,
                    instant.isoformat(),
                    prepared.item_id,
                    prepared.item_version,
                    prepared.attempt_id,
                ),
            )
            if cursor.rowcount != 1:
                raise StaleWrite("failure item compare-and-swap failed")
            self._finish_literal_target_for_update(
                prepared,
                succeeded=False,
                terminal=decision.target
                in {AutopilotState.SKIPPED, AutopilotState.DEAD},
                instant=instant,
            )
            self._insert_event(
                item_id=prepared.item_id,
                run_id=prepared.run_id,
                previous=current.state,
                target=decision.target,
                reason=decision.reason,
                metadata=_dispatch_event_metadata(outcome),
                created_at=instant,
            )
            return self._item_for_update(prepared.item_id)

    def record_pre_dispatch_failure(
        self,
        *,
        item_id: int,
        expected_version: int,
        authorization: LiteralConfirmation | LiveAuthorization,
        fencing_token: int,
        outcome: DispatchOutcome,
        decision: RetryDecision,
        now: datetime | str | None = None,
    ) -> ItemRecord:
        """Persist a cover-letter/preflight failure without claiming a POST."""
        from work_hunter.safety import require_hh_dispatch_authorization

        item_id = _integer(item_id, field="item_id", minimum=1)
        expected_version = _integer(
            expected_version,
            field="expected_version",
        )
        fencing_token = _integer(
            fencing_token,
            field="fencing_token",
            minimum=1,
        )
        if (
            type(outcome) is not DispatchOutcome
            or outcome.certainty is not DeliveryCertainty.DEFINITELY_NOT_SENT
        ):
            raise ValueError("pre-dispatch failure must be definitely not sent")
        if (
            type(decision) is not RetryDecision
            or decision.target is not AutopilotState.RETRY_WAIT
        ):
            raise ValueError("pre-dispatch failure must be a persisted retry")
        instant = _instant(now, field="now")
        stored_outcome = _dispatch_storage_payload(outcome)

        with self.immediate():
            current = self._item_for_update(item_id)
            if (
                current.version != expected_version
                or current.state is not AutopilotState.READY
            ):
                raise StaleWrite("pre-dispatch item changed")
            require_hh_dispatch_authorization(
                authorization,
                account_id=current.account_id,
                lease_account_id=current.account_id,
                fencing_token=fencing_token,
            )
            self._assert_fence(current.account_id, fencing_token, instant)
            run = self._run_for_update(current.last_run_id)
            if type(authorization) is LiveAuthorization:
                live = cast(LiveAuthorization, authorization)
                authorization_kind = AuthorizationKind.AUTOPILOT
                authorization_ref = str(live.grant_id)
            else:
                literal = cast(LiteralConfirmation, authorization)
                authorization_kind = AuthorizationKind.LITERAL_CONFIRMATION
                authorization_ref = literal.reference_id
            cursor = self.conn.execute(
                """
                INSERT INTO hh_application_attempts (
                    run_id, campaign_item_id, vacancy_id, resume_id, status,
                    reason, letter, raw_result_json, created_at,
                    account_profile_id, autopilot_run_id, autopilot_item_id,
                    autopilot_attempt_id, authorization_kind,
                    authorization_ref, policy_hash, delivery_certainty,
                    dispatched_at, finished_at
                ) VALUES (
                    NULL, NULL, ?, ?, 'retry_wait', ?, '', ?, ?,
                    ?, ?, ?, NULL, ?, ?, ?, ?, '', ?
                )
                """,
                (
                    current.vacancy_id,
                    current.resume_id,
                    outcome.code,
                    _json_dumps(stored_outcome, field="pre-dispatch outcome"),
                    instant.isoformat(),
                    current.account_id,
                    run.id,
                    current.id,
                    authorization_kind.value,
                    authorization_ref,
                    run.policy_hash,
                    outcome.certainty.value,
                    instant.isoformat(),
                ),
            )
            attempt_id = _required_lastrowid(cursor)
            self.conn.execute(
                """
                UPDATE hh_application_attempts
                SET autopilot_attempt_id = ?
                WHERE id = ?
                """,
                (attempt_id, attempt_id),
            )
            assert_transition(
                AutopilotState.READY,
                AutopilotState.APPLYING,
            )
            assert_transition(
                AutopilotState.APPLYING,
                AutopilotState.RETRY_WAIT,
            )
            cursor = self.conn.execute(
                """
                UPDATE hh_autopilot_items
                SET state = 'retry_wait', retry_stage = 'application',
                    version = version + 1, active_attempt_id = ?,
                    next_attempt_at = ?, last_outcome_code = ?, updated_at = ?
                WHERE id = ? AND version = ? AND state = 'ready'
                """,
                (
                    attempt_id,
                    decision.next_attempt_at,
                    outcome.code,
                    instant.isoformat(),
                    current.id,
                    current.version,
                ),
            )
            if cursor.rowcount != 1:
                raise StaleWrite("pre-dispatch failure item changed")
            self._insert_event(
                item_id=current.id,
                run_id=run.id,
                previous=AutopilotState.READY,
                target=AutopilotState.APPLYING,
                reason="pre_dispatch_started",
                metadata={"attempt_id": attempt_id},
                created_at=instant,
            )
            self._insert_event(
                item_id=current.id,
                run_id=run.id,
                previous=AutopilotState.APPLYING,
                target=AutopilotState.RETRY_WAIT,
                reason=outcome.code,
                metadata=_dispatch_event_metadata(outcome),
                created_at=instant,
            )
            return self._item_for_update(current.id)

    def _validate_prepared_for_update(
        self,
        prepared: PreparedDispatch,
        *,
        instant: datetime,
        allowed_states: frozenset[AutopilotState] | None = None,
    ) -> ItemRecord:
        self._assert_fence(
            prepared.account_id,
            prepared.fencing_token,
            instant,
        )
        current = self._item_for_update(prepared.item_id)
        allowed = allowed_states or frozenset(
            {AutopilotState.APPLYING, AutopilotState.RECONCILING}
        )
        if (
            current.account_id != prepared.account_id
            or current.vacancy_id != prepared.vacancy_id
            or current.resume_id != prepared.resume_id
            or current.version != prepared.item_version
            or current.active_attempt_id != prepared.attempt_id
            or current.state not in allowed
        ):
            raise StaleWrite("prepared item provenance changed")
        attempt = self.get_application_attempt(prepared.attempt_id)
        if attempt is None or (
            attempt.account_id != prepared.account_id
            or attempt.run_id != prepared.run_id
            or attempt.item_id != prepared.item_id
            or attempt.autopilot_attempt_id != prepared.attempt_id
            or attempt.vacancy_id != prepared.vacancy_id
            or attempt.resume_id != prepared.resume_id
            or attempt.authorization_kind is not prepared.authorization_kind
            or attempt.authorization_ref != prepared.authorization_ref
            or attempt.policy_hash != prepared.policy_hash
            or attempt.status not in {"applying", "reconciling"}
        ):
            raise StaleWrite("prepared attempt provenance changed")
        reservation = self._reservation_for_update(prepared.reservation_id)
        if (
            reservation.attempt_id != prepared.attempt_id
            or reservation.run_id != prepared.run_id
            or reservation.account_id != prepared.account_id
            or reservation.fencing_token != prepared.fencing_token
            or reservation.state
            not in {QuotaReservationState.RESERVED, QuotaReservationState.HELD}
        ):
            raise StaleWrite("prepared reservation provenance changed")
        guard = self.get_guard(
            prepared.account_id,
            "hh",
            prepared.vacancy_id,
        )
        if (
            guard is None
            or guard.status != "active"
            or guard.owner_attempt_id != prepared.attempt_id
        ):
            raise StaleWrite("prepared guard provenance changed")
        return current

    def _finish_attempt_for_update(
        self,
        prepared: PreparedDispatch,
        outcome: DispatchOutcome,
        *,
        stored_outcome: dict[str, Any],
        status: str,
        instant: datetime,
        terminal: bool = True,
    ) -> None:
        cursor = self.conn.execute(
            """
            UPDATE hh_application_attempts
            SET status = ?, reason = ?, raw_result_json = ?,
                delivery_certainty = ?, finished_at = ?
            WHERE id = ? AND autopilot_attempt_id = ?
              AND status IN ('applying','reconciling')
              AND (delivery_certainty = '' OR status = 'reconciling')
            """,
            (
                status,
                outcome.code,
                _json_dumps(stored_outcome, field="dispatch outcome"),
                outcome.certainty.value,
                instant.isoformat() if terminal else "",
                prepared.attempt_id,
                prepared.attempt_id,
            ),
        )
        if cursor.rowcount != 1:
            raise StaleWrite("attempt finalization compare-and-swap failed")

    def _ensure_hh_job_for_update(
        self,
        item: ItemRecord,
        *,
        instant: datetime,
    ) -> int:
        row = self.conn.execute(
            "SELECT id FROM jobs WHERE source = 'hh' AND source_id = ?",
            (item.vacancy_id,),
        ).fetchone()
        if row is not None:
            return _persisted_integer(row["id"], field="job id", minimum=1)
        result = self.conn.execute(
            """
            SELECT normalized_json
            FROM hh_autopilot_search_results
            WHERE account_profile_id = ? AND vacancy_id = ?
              AND resume_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (item.account_id, item.vacancy_id, item.resume_id),
        ).fetchone()
        if result is None:
            raise StaleWrite("canonical HH vacancy is unavailable for application")
        normalized = _normalized_json_loads(
            result["normalized_json"],
            vacancy_id=item.vacancy_id,
        )
        job = normalized["job"]
        cursor = self.conn.execute(
            """
            INSERT INTO jobs (
                source, source_id, url, title, company, salary_text,
                salary_from, salary_to, currency, location, remote,
                description, published_at, fetched_at, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job["source"],
                job["source_id"],
                job["url"],
                job["title"],
                job["company"],
                job["salary_text"],
                job["salary_from"],
                job["salary_to"],
                job["currency"],
                job["location"],
                None if job["remote"] is None else int(job["remote"]),
                job["description"],
                job["published_at"],
                job["fetched_at"],
                job["status"],
            ),
        )
        del instant
        return _required_lastrowid(cursor)

    def _increment_success_counter_for_update(
        self,
        run_id: int,
        *,
        instant: datetime,
    ) -> None:
        row = self.conn.execute(
            "SELECT counters_json FROM hh_autopilot_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise StaleWrite("application run disappeared")
        counters = _json_loads(row["counters_json"], field="run counters")
        successful = counters.get("successful", 0)
        if type(successful) is not int or successful < 0:
            raise StaleWrite("run successful counter is malformed")
        counters["successful"] = successful + 1
        cursor = self.conn.execute(
            """
            UPDATE hh_autopilot_runs
            SET counters_json = ?
            WHERE id = ? AND counters_json = ?
            """,
            (
                _json_dumps(counters, field="run counters"),
                run_id,
                row["counters_json"],
            ),
        )
        del instant
        if cursor.rowcount != 1:
            raise StaleWrite("run successful counter changed")

    def _finish_literal_target_for_update(
        self,
        prepared: PreparedDispatch,
        *,
        succeeded: bool,
        instant: datetime,
        terminal: bool = False,
    ) -> None:
        if prepared.authorization_kind is not AuthorizationKind.LITERAL_CONFIRMATION:
            return
        target_status = "succeeded" if succeeded else ("closed" if terminal else "pending")
        cursor = self.conn.execute(
            """
            UPDATE hh_autopilot_one_shot_targets
            SET status = ?, active_attempt_id = NULL, updated_at = ?
            WHERE authorization_ref = ? AND resume_id = ?
              AND vacancy_id = ? AND status = 'active'
              AND active_attempt_id = ?
            """,
            (
                target_status,
                instant.isoformat(),
                prepared.authorization_ref,
                prepared.resume_id,
                prepared.vacancy_id,
                prepared.attempt_id,
            ),
        )
        if cursor.rowcount != 1:
            raise StaleWrite("literal target finalization failed")
        if succeeded:
            cursor = self.conn.execute(
                """
                UPDATE hh_autopilot_one_shot_authorizations
                SET consumed_success = consumed_success + 1
                WHERE reference_id = ? AND active = 1
                  AND consumed_success < max_success
                """,
                (prepared.authorization_ref,),
            )
            if cursor.rowcount != 1:
                raise StaleWrite("literal success cap changed")
        row = self.conn.execute(
            """
            SELECT max_success, consumed_success
            FROM hh_autopilot_one_shot_authorizations
            WHERE reference_id = ?
            """,
            (prepared.authorization_ref,),
        ).fetchone()
        if row is None:
            raise StaleWrite("literal authorization disappeared")
        unfinished = self.conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM hh_autopilot_one_shot_targets
            WHERE authorization_ref = ? AND status IN ('pending','active')
            """,
            (prepared.authorization_ref,),
        ).fetchone()
        cap_reached = _persisted_integer(
            row["consumed_success"],
            field="literal consumed_success",
        ) >= _persisted_integer(
            row["max_success"],
            field="literal max_success",
            minimum=1,
        )
        no_targets = _persisted_integer(
            unfinished["total"],
            field="literal unfinished target count",
        ) == 0
        if cap_reached or no_targets:
            self.conn.execute(
                """
                UPDATE hh_autopilot_one_shot_authorizations
                SET active = 0, finished_at = ?
                WHERE reference_id = ? AND active = 1
                """,
                (instant.isoformat(), prepared.authorization_ref),
            )

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

    def dispatch_reservation(self, item_id: int) -> QuotaReservationRecord | None:
        """Return the reservation attached to an item's latest dispatch attempt."""
        item_id = _integer(item_id, field="item_id", minimum=1)
        item = self.get_item(item_id)
        if item is None or item.active_attempt_id is None:
            return None
        row = self.conn.execute(
            """
            SELECT * FROM hh_autopilot_quota_reservations
            WHERE attempt_id = ? ORDER BY id DESC LIMIT 1
            """,
            (item.active_attempt_id,),
        ).fetchone()
        return self._reservation_from_row(row) if row is not None else None

    def open_manual_challenge(
        self,
        item_id: int,
        *,
        expected_version: int,
        challenge_type: str,
        sanitized_url: str,
        fencing_token: int,
        expiry_hours: int = 24,
        metadata: dict[str, Any] | None = None,
        now: datetime | str | None = None,
    ) -> ChallengeRecord:
        """Atomically stop a dispatch and hand a CAPTCHA/task/auth step to the user."""
        item_id = _integer(item_id, field="item_id", minimum=1)
        expected_version = _integer(expected_version, field="expected_version")
        challenge_type = _required_text(challenge_type, field="challenge_type")
        if challenge_type not in {
            "manual_captcha",
            "manual_assessment",
            "manual_auth",
        }:
            raise ValueError("unsupported manual challenge type")
        sanitized_url = _optional_text(
            sanitized_url,
            field="sanitized_url",
            maximum=2_000,
        )
        if "?" in sanitized_url or "#" in sanitized_url:
            raise ValueError("challenge URL must not contain query or fragment data")
        fencing_token = _integer(
            fencing_token,
            field="fencing_token",
            minimum=1,
        )
        expiry_hours = _integer(expiry_hours, field="expiry_hours", minimum=1)
        metadata_json = _json_dumps(metadata, field="challenge metadata")
        instant = _instant(now, field="now")
        scope = "account" if challenge_type == "manual_auth" else "item"

        with self.immediate():
            current = self._item_for_update(item_id)
            self._assert_fence(current.account_id, fencing_token, instant)
            existing_row = self.conn.execute(
                """
                SELECT * FROM hh_autopilot_challenges
                WHERE challenge_type = ? AND account_profile_id = ?
                  AND item_id = ? AND status IN ('open','in_progress')
                ORDER BY id ASC LIMIT 1
                """,
                (challenge_type, current.account_id, current.id),
            ).fetchone()
            if existing_row is not None:
                existing = self._challenge_from_row(existing_row)
                if current.challenge_id != existing.id:
                    raise StaleWrite("open challenge is not linked to the item")
                return existing
            if current.version != expected_version:
                raise StaleWrite(f"item {item_id} version changed")
            if current.state not in {
                AutopilotState.APPLYING,
                AutopilotState.RECONCILING,
            }:
                raise StaleWrite("manual challenge requires an active dispatch stage")
            if current.active_attempt_id is None:
                raise StaleWrite("manual challenge has no active attempt")
            reservation_row = self.conn.execute(
                """
                SELECT * FROM hh_autopilot_quota_reservations
                WHERE attempt_id = ? ORDER BY id DESC LIMIT 1
                """,
                (current.active_attempt_id,),
            ).fetchone()
            if reservation_row is None:
                raise StaleWrite("manual challenge has no dispatch reservation")
            reservation = self._reservation_from_row(reservation_row)
            keep_held = (
                challenge_type == "manual_auth"
                and current.state is AutopilotState.RECONCILING
                and reservation.state is QuotaReservationState.HELD
            )
            if not keep_held and reservation.state in {
                QuotaReservationState.RESERVED,
                QuotaReservationState.HELD,
            }:
                reservation = self._change_reservation_state_for_update(
                    reservation.id,
                    fencing_token,
                    target=QuotaReservationState.RELEASED,
                    remote_negotiation_id=None,
                    instant=instant,
                )
                self.conn.execute(
                    """
                    DELETE FROM hh_application_account_guards
                    WHERE account_profile_id = ? AND source = 'hh'
                      AND source_id = ? AND status = 'active'
                      AND owner_attempt_id = ?
                    """,
                    (current.account_id, current.vacancy_id, current.active_attempt_id),
                )
                self.conn.execute(
                    """
                    UPDATE hh_application_attempts
                    SET status = ?, reason = ?, finished_at = ?
                    WHERE id = ? AND status IN ('applying','reconciling')
                    """,
                    (
                        AutopilotState.MANUAL_CHALLENGE.value,
                        challenge_type,
                        instant.isoformat(),
                        current.active_attempt_id,
                    ),
                )

            expires_at = (
                ""
                if scope == "account"
                else (instant + timedelta(hours=expiry_hours)).isoformat()
            )
            cursor = self.conn.execute(
                """
                INSERT INTO hh_autopilot_challenges (
                    scope, challenge_type, account_profile_id, item_id,
                    reservation_id, sanitized_url, screenshot_path,
                    status, expires_at, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, '', 'open', ?, ?, ?)
                """,
                (
                    scope,
                    challenge_type,
                    current.account_id,
                    current.id,
                    reservation.id,
                    sanitized_url,
                    expires_at,
                    metadata_json,
                    instant.isoformat(),
                ),
            )
            challenge_id = _required_lastrowid(cursor)
            assert_transition(current.state, AutopilotState.MANUAL_CHALLENGE)
            cursor = self.conn.execute(
                """
                UPDATE hh_autopilot_items
                SET state = 'manual_challenge', version = version + 1,
                    challenge_id = ?, next_attempt_at = '',
                    last_outcome_code = ?, updated_at = ?
                WHERE id = ? AND version = ? AND state = ?
                """,
                (
                    challenge_id,
                    challenge_type,
                    instant.isoformat(),
                    current.id,
                    current.version,
                    current.state.value,
                ),
            )
            if cursor.rowcount != 1:
                raise StaleWrite("manual challenge item compare-and-swap failed")
            self._insert_event(
                item_id=current.id,
                run_id=current.last_run_id,
                previous=current.state,
                target=AutopilotState.MANUAL_CHALLENGE,
                reason=challenge_type,
                metadata={"challenge_id": challenge_id},
                created_at=instant,
            )
            return self._challenge_for_update(challenge_id)

    def resolve_manual_challenge(
        self,
        challenge_id: int,
        *,
        action: str,
        actor: str,
        fencing_token: int,
        now: datetime | str | None = None,
    ) -> ItemRecord:
        challenge_id = _integer(challenge_id, field="challenge_id", minimum=1)
        action = _required_text(action, field="action").casefold()
        if action in {"skip", "skipped"}:
            action = "dismiss"
        if action not in {"completed", "dismiss", "expire"}:
            raise ValueError("unsupported manual challenge resolution action")
        actor = _required_text(actor, field="actor")
        fencing_token = _integer(fencing_token, field="fencing_token", minimum=1)
        instant = _instant(now, field="now")

        with self.immediate():
            challenge = self._challenge_for_update(challenge_id)
            self._assert_fence(challenge.account_id, fencing_token, instant)
            if challenge.challenge_type not in {
                "manual_captcha",
                "manual_assessment",
                "manual_auth",
            }:
                raise ValueError("challenge is not a manual browser challenge")
            closed_status = {
                "completed": "resolved",
                "dismiss": "dismissed",
                "expire": "expired",
            }[action]
            linked_rows = self.conn.execute(
                """
                SELECT * FROM hh_autopilot_items
                WHERE challenge_id = ? ORDER BY id ASC
                """,
                (challenge.id,),
            ).fetchall()
            linked = [self._item_from_row(row) for row in linked_rows]
            if challenge.status == closed_status and challenge.resolution_action == action:
                if challenge.item_id is None:
                    raise StaleWrite("resolved account challenge has no primary item")
                return self._item_for_update(challenge.item_id)
            if challenge.status not in {"open", "in_progress"}:
                raise StaleWrite("manual challenge is already closed")
            if not linked:
                raise StaleWrite("manual challenge has no linked items")
            if action == "expire":
                if not challenge.expires_at or _instant(
                    challenge.expires_at,
                    field="challenge expires_at",
                ) > instant:
                    raise ValueError("manual challenge has not expired")

            primary_id = challenge.item_id or linked[0].id
            for current in linked:
                if current.state is not AutopilotState.MANUAL_CHALLENGE:
                    raise StaleWrite("manual challenge item changed")
                if action == "completed":
                    target = (
                        AutopilotState.RECONCILING
                        if current.retry_stage is RetryStage.RECONCILIATION
                        else AutopilotState.READY
                    )
                    reason = f"{challenge.challenge_type}_completed"
                else:
                    target = AutopilotState.SKIPPED
                    reason = (
                        "challenge_expired"
                        if action == "expire"
                        else "challenge_dismissed"
                    )
                    if current.active_attempt_id is not None:
                        reservation_row = self.conn.execute(
                            """
                            SELECT * FROM hh_autopilot_quota_reservations
                            WHERE attempt_id = ? ORDER BY id DESC LIMIT 1
                            """,
                            (current.active_attempt_id,),
                        ).fetchone()
                        if reservation_row is not None:
                            reservation = self._reservation_from_row(reservation_row)
                            if reservation.state in {
                                QuotaReservationState.RESERVED,
                                QuotaReservationState.HELD,
                            }:
                                self._change_reservation_state_for_update(
                                    reservation.id,
                                    fencing_token,
                                    target=QuotaReservationState.RELEASED,
                                    remote_negotiation_id=None,
                                    instant=instant,
                                )
                assert_transition(current.state, target)
                cursor = self.conn.execute(
                    """
                    UPDATE hh_autopilot_items
                    SET state = ?, version = version + 1, challenge_id = NULL,
                        next_attempt_at = ?, last_outcome_code = ?, updated_at = ?
                    WHERE id = ? AND version = ? AND state = 'manual_challenge'
                      AND challenge_id = ?
                    """,
                    (
                        target.value,
                        instant.isoformat()
                        if target is AutopilotState.RECONCILING
                        else "",
                        reason,
                        instant.isoformat(),
                        current.id,
                        current.version,
                        challenge.id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise StaleWrite("manual challenge resolution changed")
                self._insert_event(
                    item_id=current.id,
                    run_id=current.last_run_id,
                    previous=current.state,
                    target=target,
                    reason=reason,
                    metadata={"challenge_id": challenge.id, "actor": actor},
                    created_at=instant,
                )
            cursor = self.conn.execute(
                """
                UPDATE hh_autopilot_challenges
                SET status = ?, resolution_at = ?, resolution_actor = ?,
                    resolution_action = ?
                WHERE id = ? AND status IN ('open','in_progress')
                """,
                (
                    closed_status,
                    instant.isoformat(),
                    actor,
                    action,
                    challenge.id,
                ),
            )
            if cursor.rowcount != 1:
                raise StaleWrite("manual challenge close changed")
            return self._item_for_update(primary_id)

    def expire_challenge(
        self,
        challenge_id: int,
        *,
        fencing_token: int,
        now: datetime | str | None = None,
    ) -> ItemRecord:
        challenge_id = _integer(challenge_id, field="challenge_id", minimum=1)
        fencing_token = _integer(fencing_token, field="fencing_token", minimum=1)
        instant = _instant(now, field="now")
        with self.immediate():
            challenge = self._challenge_for_update(challenge_id)
            self._assert_fence(challenge.account_id, fencing_token, instant)
            if challenge.challenge_type != "ambiguous_application":
                raise ValueError("only ambiguity expiry is implemented here")
            if challenge.status not in {"open", "in_progress"}:
                if challenge.status == "expired" and challenge.item_id is not None:
                    return self._item_for_update(challenge.item_id)
                raise StaleWrite("challenge is already closed")
            if not challenge.expires_at or _instant(
                challenge.expires_at,
                field="challenge expires_at",
            ) > instant:
                raise ValueError("challenge has not expired")
            if challenge.item_id is None or challenge.reservation_id is None:
                raise StaleWrite("ambiguity challenge linkage is incomplete")
            current = self._item_for_update(challenge.item_id)
            if (
                current.state is not AutopilotState.MANUAL_CHALLENGE
                or current.challenge_id != challenge.id
            ):
                raise StaleWrite("ambiguity item changed")
            self._adopt_challenge_reservation_for_update(
                challenge.reservation_id,
                fencing_token,
                instant=instant,
            )
            cursor = self.conn.execute(
                """
                UPDATE hh_autopilot_challenges
                SET status = 'expired'
                WHERE id = ? AND status IN ('open','in_progress')
                """,
                (challenge.id,),
            )
            if cursor.rowcount != 1:
                raise StaleWrite("challenge expiry compare-and-swap failed")
            assert_transition(current.state, AutopilotState.DEAD)
            cursor = self.conn.execute(
                """
                UPDATE hh_autopilot_items
                SET state = 'dead', version = version + 1,
                    next_attempt_at = '',
                    last_outcome_code = 'challenge_expired', updated_at = ?
                WHERE id = ? AND version = ? AND challenge_id = ?
                  AND state = 'manual_challenge'
                """,
                (
                    instant.isoformat(),
                    current.id,
                    current.version,
                    challenge.id,
                ),
            )
            if cursor.rowcount != 1:
                raise StaleWrite("ambiguity expiry item changed")
            self._insert_event(
                item_id=current.id,
                run_id=None,
                previous=current.state,
                target=AutopilotState.DEAD,
                reason="challenge_expired",
                metadata={"challenge_id": challenge.id},
                created_at=instant,
            )
            return self._item_for_update(current.id)

    def resolve_challenge(
        self,
        challenge_id: int,
        *,
        action: str,
        actor: str,
        fencing_token: int,
        now: datetime | str | None = None,
    ) -> ItemRecord:
        challenge_id = _integer(challenge_id, field="challenge_id", minimum=1)
        action = _required_text(action, field="action")
        actor = _required_text(actor, field="actor")
        fencing_token = _integer(fencing_token, field="fencing_token", minimum=1)
        allowed_actions = {
            "confirmed_applied",
            "confirmed_not_applied_retry",
            "confirmed_not_applied_skip",
            "retry_reconciliation",
        }
        if action not in allowed_actions:
            raise ValueError("unsupported ambiguity resolution action")
        instant = _instant(now, field="now")
        with self.immediate():
            challenge = self._challenge_for_update(challenge_id)
            self._assert_fence(challenge.account_id, fencing_token, instant)
            if challenge.challenge_type != "ambiguous_application":
                raise ValueError("challenge is not an ambiguous application")
            if challenge.status == "resolved":
                if challenge.resolution_action != action or challenge.item_id is None:
                    raise StaleWrite("challenge was resolved differently")
                return self._item_for_update(challenge.item_id)
            if challenge.status not in {
                "open",
                "in_progress",
                "dismissed",
                "expired",
            }:
                raise StaleWrite("challenge cannot be resolved")
            if challenge.item_id is None or challenge.reservation_id is None:
                raise StaleWrite("ambiguity challenge linkage is incomplete")
            current = self._item_for_update(challenge.item_id)
            if current.challenge_id != challenge.id or current.state not in {
                AutopilotState.MANUAL_CHALLENGE,
                AutopilotState.DEAD,
            }:
                raise StaleWrite("ambiguity item changed")
            reservation = self._adopt_challenge_reservation_for_update(
                challenge.reservation_id,
                fencing_token,
                instant=instant,
            )
            if reservation.attempt_id is None:
                raise StaleWrite("ambiguity reservation has no attempt")
            attempt = self.get_application_attempt(reservation.attempt_id)
            if attempt is None or attempt.item_id != current.id:
                raise StaleWrite("ambiguity attempt changed")
            context = self._reconciliation_context_for_update(
                current,
                attempt,
                reservation,
                fencing_token=fencing_token,
                challenge_id=challenge.id,
                challenge_type=challenge.challenge_type,
            )
            prepared = context.prepared

            if action == "confirmed_applied":
                raw_ids = challenge.metadata.get("remote_negotiation_ids", ())
                if isinstance(raw_ids, (str, bytes)) or not isinstance(
                    raw_ids, (list, tuple)
                ) or not raw_ids:
                    raise ValueError("confirmed_applied requires a remote negotiation id")
                remote_id = _required_text(raw_ids[0], field="remote_negotiation_id")
                outcome = DispatchOutcome(
                    code="applied",
                    certainty=DeliveryCertainty.DEFINITE_RESPONSE,
                    payload={"id": remote_id},
                )
                stored_outcome = _dispatch_storage_payload(outcome)
                item = self._finalize_applied_for_update(
                    prepared,
                    outcome,
                    stored_outcome=stored_outcome,
                    outcome_json=_json_dumps(
                        stored_outcome,
                        field="dispatch outcome",
                    ),
                    instant=instant,
                    allowed_states=frozenset(
                        {
                            AutopilotState.MANUAL_CHALLENGE,
                            AutopilotState.DEAD,
                        }
                    ),
                    reason="confirmed_applied",
                )
            else:
                target = {
                    "confirmed_not_applied_retry": AutopilotState.READY,
                    "confirmed_not_applied_skip": AutopilotState.SKIPPED,
                    "retry_reconciliation": AutopilotState.RECONCILING,
                }[action]
                if target is not AutopilotState.RECONCILING:
                    self._change_reservation_state_for_update(
                        reservation.id,
                        fencing_token,
                        target=QuotaReservationState.RELEASED,
                        remote_negotiation_id=None,
                        instant=instant,
                    )
                    cursor = self.conn.execute(
                        """
                        DELETE FROM hh_application_account_guards
                        WHERE account_profile_id = ? AND source = 'hh'
                          AND source_id = ? AND status = 'active'
                          AND owner_attempt_id = ?
                        """,
                        (
                            prepared.account_id,
                            prepared.vacancy_id,
                            prepared.attempt_id,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise StaleWrite("ambiguity guard release failed")
                    cursor = self.conn.execute(
                        """
                        UPDATE hh_application_attempts
                        SET status = ?, reason = ?, finished_at = ?
                        WHERE id = ? AND status IN ('applying','reconciling')
                        """,
                        (
                            target.value,
                            action,
                            instant.isoformat(),
                            prepared.attempt_id,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise StaleWrite("ambiguity attempt resolution failed")
                    self._finish_literal_target_for_update(
                        prepared,
                        succeeded=False,
                        terminal=target is AutopilotState.SKIPPED,
                        instant=instant,
                    )
                cursor = self.conn.execute(
                    """
                    UPDATE hh_autopilot_items
                    SET state = ?, retry_stage = ?, version = version + 1,
                        next_attempt_at = ?, challenge_id = NULL,
                        last_outcome_code = ?, updated_at = ?
                    WHERE id = ? AND version = ? AND challenge_id = ?
                      AND state = ?
                    """,
                    (
                        target.value,
                        (
                            RetryStage.RECONCILIATION.value
                            if target is AutopilotState.RECONCILING
                            else RetryStage.APPLICATION.value
                        ),
                        instant.isoformat()
                        if target is AutopilotState.RECONCILING
                        else "",
                        action,
                        instant.isoformat(),
                        current.id,
                        current.version,
                        challenge.id,
                        current.state.value,
                    ),
                )
                if cursor.rowcount != 1:
                    raise StaleWrite("ambiguity item resolution failed")
                self._insert_event(
                    item_id=current.id,
                    run_id=None,
                    previous=current.state,
                    target=target,
                    reason=action,
                    metadata={"challenge_id": challenge.id, "actor": actor},
                    created_at=instant,
                )
                item = self._item_for_update(current.id)

            cursor = self.conn.execute(
                """
                UPDATE hh_autopilot_challenges
                SET status = 'resolved', resolution_at = ?,
                    resolution_actor = ?, resolution_action = ?
                WHERE id = ? AND status IN (
                    'open','in_progress','dismissed','expired'
                )
                """,
                (instant.isoformat(), actor, action, challenge.id),
            )
            if cursor.rowcount != 1:
                raise StaleWrite("challenge resolution compare-and-swap failed")
            return item

    def requeue_dead(
        self,
        item_id: int | None,
        *,
        actor: str,
        fencing_token: int,
        now: datetime | str | None = None,
    ) -> ItemRecord:
        item_id = _integer(item_id, field="item_id", minimum=1)
        actor = _required_text(actor, field="actor")
        fencing_token = _integer(fencing_token, field="fencing_token", minimum=1)
        instant = _instant(now, field="now")
        with self.immediate():
            current = self._item_for_update(item_id)
            self._assert_fence(current.account_id, fencing_token, instant)
            if current.state is not AutopilotState.DEAD:
                raise StaleWrite("only dead items can be requeued")
            if current.challenge_id is not None:
                challenge = self._challenge_for_update(current.challenge_id)
                if challenge.challenge_type == "ambiguous_application" and (
                    challenge.status != "resolved"
                ):
                    raise RuntimeError("unresolved_ambiguity")
            held = self.conn.execute(
                """
                SELECT 1 FROM hh_autopilot_quota_reservations
                WHERE attempt_id = ? AND state = 'held'
                """,
                (current.active_attempt_id,),
            ).fetchone()
            if held is not None:
                raise RuntimeError("unresolved_ambiguity")
            target = {
                RetryStage.ELIGIBILITY: AutopilotState.ELIGIBLE,
                RetryStage.APPLICATION: AutopilotState.READY,
                RetryStage.RECONCILIATION: AutopilotState.RECONCILING,
            }[current.retry_stage]
            cursor = self.conn.execute(
                """
                UPDATE hh_autopilot_items
                SET state = ?, version = version + 1,
                    next_attempt_at = ?, challenge_id = NULL,
                    last_outcome_code = 'operator_requeue', updated_at = ?
                WHERE id = ? AND version = ? AND state = 'dead'
                """,
                (
                    target.value,
                    instant.isoformat()
                    if target is AutopilotState.RECONCILING
                    else "",
                    instant.isoformat(),
                    current.id,
                    current.version,
                ),
            )
            if cursor.rowcount != 1:
                raise StaleWrite("dead item requeue compare-and-swap failed")
            self._insert_event(
                item_id=current.id,
                run_id=None,
                previous=current.state,
                target=target,
                reason="operator_requeue",
                metadata={"actor": actor},
                created_at=instant,
            )
            return self._item_for_update(current.id)

    def _adopt_challenge_reservation_for_update(
        self,
        reservation_id: int,
        fencing_token: int,
        *,
        instant: datetime,
    ) -> QuotaReservationRecord:
        reservation = self._reservation_for_update(reservation_id)
        self._assert_fence(reservation.account_id, fencing_token, instant)
        if reservation.state is not QuotaReservationState.HELD:
            raise StaleWrite("ambiguity reservation is not held")
        if reservation.fencing_token == fencing_token:
            return reservation
        cursor = self.conn.execute(
            """
            UPDATE hh_autopilot_quota_reservations
            SET fencing_token = ?
            WHERE id = ? AND fencing_token = ? AND state = 'held'
            """,
            (fencing_token, reservation.id, reservation.fencing_token),
        )
        if cursor.rowcount != 1:
            raise StaleWrite("ambiguity reservation adoption failed")
        return self._reservation_for_update(reservation.id)

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

    def _challenge_for_update(self, challenge_id: int) -> ChallengeRecord:
        row = self.conn.execute(
            "SELECT * FROM hh_autopilot_challenges WHERE id = ?",
            (challenge_id,),
        ).fetchone()
        if row is None:
            raise StaleWrite(f"challenge {challenge_id} does not exist")
        return self._challenge_from_row(row)

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

    @staticmethod
    def _search_mode(mode: Any) -> str:
        if type(mode) is not str:
            raise TypeError("mode must be a string")
        if mode not in {"live", "shadow"}:
            raise ValueError("mode must be live or shadow")
        return mode

    def _assert_search_run_for_update(
        self,
        run: RunRecord,
        *,
        account_id: str,
        policy_hash: str,
        fencing_token: int,
        mode: str,
        instant: datetime,
    ) -> None:
        self._assert_fence(account_id, fencing_token, instant)
        if run.account_id != account_id:
            raise RepositoryAuthorizationDenied("run_account_mismatch")
        if run.status != "running":
            raise RepositoryAuthorizationDenied("run_not_active")
        if run.policy_hash != policy_hash:
            raise RepositoryAuthorizationDenied("run_policy_mismatch")
        if run.fencing_token != fencing_token:
            raise RepositoryAuthorizationDenied("run_fencing_token_mismatch")
        if mode == "shadow":
            if run.trigger != "shadow":
                raise RepositoryAuthorizationDenied("shadow_run_required")
            if run.grant_id is not None:
                raise RepositoryAuthorizationDenied("shadow_run_must_be_grantless")
            return
        if run.trigger == "shadow":
            raise RepositoryAuthorizationDenied("live_search_forbids_shadow_run")
        grant = self._active_grant_for_update(account_id)
        if grant is None:
            raise RepositoryAuthorizationDenied("authorization_state_mismatch")
        if run.grant_id != grant.id:
            raise RepositoryAuthorizationDenied("run_grant_mismatch")
        if grant.policy_hash != policy_hash:
            raise RepositoryAuthorizationDenied("policy_hash_mismatch")
        if self._pause_active_for_update(account_id):
            raise RepositoryAuthorizationDenied("autopilot_disabled_or_paused")
        if self._kill_switch_active_for_update(account_id):
            raise RepositoryAuthorizationDenied("kill_switch_active")

    def _assert_search_runtime_for_update(
        self,
        cycle: SearchCycleRecord,
        *,
        account_id: str,
        run_id: int,
        policy_hash: str,
        fencing_token: int,
        mode: str,
        instant: datetime,
    ) -> None:
        if cycle.status != "running":
            raise StaleWrite("search cycle is not running")
        if cycle.account_id != account_id:
            raise RepositoryAuthorizationDenied("search_cycle_account_mismatch")
        if cycle.owner_run_id != run_id:
            raise StaleWrite("search cycle owner changed")
        if cycle.policy_hash != policy_hash:
            raise RepositoryAuthorizationDenied("search_cycle_policy_mismatch")
        if cycle.fencing_token != fencing_token:
            raise LostLease("search cycle fencing token changed")
        origin_mode = self._search_cycle_origin_mode_for_update(cycle)
        if origin_mode != mode:
            raise RepositoryAuthorizationDenied("search_cycle_mode_mismatch")
        run = self._assert_search_owner_provenance_for_update(
            cycle,
            origin_mode=origin_mode,
        )
        self._assert_search_run_for_update(
            run,
            account_id=account_id,
            policy_hash=policy_hash,
            fencing_token=fencing_token,
            mode=mode,
            instant=instant,
        )

    def _search_cycle_origin_mode_for_update(
        self, cycle: SearchCycleRecord
    ) -> str:
        mode = cycle.mode
        origin = self._run_for_update(cycle.origin_run_id)
        if origin.id != cycle.origin_run_id:
            raise StaleWrite("search cycle origin run id changed")
        if origin.account_id != cycle.account_id:
            raise StaleWrite("search cycle origin account changed")
        if origin.policy_hash != cycle.policy_hash:
            raise StaleWrite("search cycle origin policy changed")
        if (
            cycle.claim_version == 0
            and cycle.owner_run_id == cycle.origin_run_id
            and origin.fencing_token != cycle.fencing_token
        ):
            raise LostLease("search cycle origin fencing token changed")
        if mode == "shadow":
            if origin.trigger != "shadow" or origin.grant_id is not None:
                raise StaleWrite("shadow search origin provenance changed")
            return mode
        if origin.trigger == "shadow":
            raise StaleWrite("live search origin became shadow")
        self._assert_historical_search_grant_for_update(origin, cycle=cycle)
        return mode

    def _assert_search_owner_provenance_for_update(
        self,
        cycle: SearchCycleRecord,
        *,
        origin_mode: str | None = None,
    ) -> RunRecord:
        if origin_mode is None:
            origin_mode = self._search_cycle_origin_mode_for_update(cycle)
        elif origin_mode != cycle.mode:
            raise StaleWrite("search cycle validated mode changed")
        owner = self._run_for_update(cycle.owner_run_id)
        if owner.id != cycle.owner_run_id:
            raise StaleWrite("search cycle owner run id changed")
        if owner.account_id != cycle.account_id:
            raise StaleWrite("search cycle owner account changed")
        if owner.policy_hash != cycle.policy_hash:
            raise StaleWrite("search cycle owner policy changed")
        if owner.fencing_token != cycle.fencing_token:
            raise LostLease("search cycle owner fencing token changed")
        if origin_mode == "shadow":
            if (
                cycle.claim_version != 0
                or owner.id != cycle.origin_run_id
                or owner.trigger != "shadow"
                or owner.grant_id is not None
            ):
                raise StaleWrite("shadow search owner provenance changed")
        else:
            if cycle.claim_version == 0:
                if owner.id != cycle.origin_run_id:
                    raise StaleWrite("unclaimed live search owner changed")
            elif owner.trigger != "recovery":
                raise StaleWrite("claimed live search owner trigger changed")
            if owner.trigger == "shadow":
                raise StaleWrite("live search owner became shadow")
            self._assert_historical_search_grant_for_update(owner, cycle=cycle)
        return owner

    def _assert_historical_search_grant_for_update(
        self,
        run: RunRecord,
        *,
        cycle: SearchCycleRecord,
    ) -> None:
        grant_id = run.grant_id
        if grant_id is None:
            raise StaleWrite("live search run has no historical grant")
        row = self.conn.execute(
            "SELECT * FROM hh_autopilot_grants WHERE id = ?",
            (grant_id,),
        ).fetchone()
        if row is None:
            raise StaleWrite("live search historical grant is missing")
        try:
            stored_id = _persisted_integer(
                row["id"], field="historical grant id", minimum=1
            )
            account_id = _persisted_text(
                row["account_profile_id"],
                field="historical grant account_id",
                canonical=True,
            )
            scope = _persisted_text(
                row["scope"], field="historical grant scope"
            )
            policy_hash = _persisted_text(
                row["policy_hash"], field="historical grant policy_hash"
            )
            _persisted_integer(
                row["generation"],
                field="historical grant generation",
                minimum=1,
            )
            active = _persisted_integer(
                row["active"], field="historical grant active"
            )
        except (StaleWrite, TypeError, ValueError) as exc:
            raise StaleWrite("live search historical grant is malformed") from exc
        if active not in {0, 1}:
            raise StaleWrite("live search historical grant active flag is malformed")
        if (
            stored_id != grant_id
            or account_id != run.account_id
            or account_id != cycle.account_id
            or scope != APPLICATION_SCOPE
            or policy_hash != run.policy_hash
            or policy_hash != cycle.policy_hash
        ):
            raise StaleWrite("live search historical grant provenance changed")

    def _assert_no_running_search_siblings_for_update(
        self,
        cycle: SearchCycleRecord,
        *,
        old_owner: RunRecord,
    ) -> None:
        rows = self.conn.execute(
            """
            SELECT * FROM hh_autopilot_search_cycles
            WHERE owner_run_id = ? AND status = 'running' AND id != ?
            ORDER BY id ASC
            """,
            (old_owner.id, cycle.id),
        ).fetchall()
        for row in rows:
            sibling = self._search_cycle_from_row(row)
            sibling_mode = self._search_cycle_origin_mode_for_update(sibling)
            sibling_owner = self._assert_search_owner_provenance_for_update(
                sibling
            )
            if (
                sibling.owner_run_id != old_owner.id
                or sibling_owner.id != old_owner.id
                or sibling_mode != "live"
            ):
                raise StaleWrite(
                    "abandoned search owner sibling provenance changed"
                )
        if rows:
            raise StaleWrite(
                "abandoned search owner still owns another running cycle"
            )

    def _interrupt_abandoned_search_owner_for_update(
        self,
        owner: RunRecord,
        *,
        replacement_run_id: int,
        instant: datetime,
    ) -> None:
        if owner.id == replacement_run_id or owner.status in TERMINAL_RUN_STATUSES:
            return
        if owner.status not in {"created", "running", "stop_requested"}:
            raise StaleWrite("abandoned search owner has invalid nonterminal status")
        cursor = self.conn.execute(
            """
            UPDATE hh_autopilot_runs
            SET status = 'interrupted', error = ?, finished_at = ?
            WHERE id = ? AND status = ?
            """,
            (
                "search_cycle_adopted_after_lease_replacement",
                instant.isoformat(),
                owner.id,
                owner.status,
            ),
        )
        if cursor.rowcount != 1:
            raise StaleWrite("abandoned search owner status changed")

    def _assert_search_commit_provenance(
        self,
        cycle: SearchCycleRecord,
        *,
        owner_run_id: int | None,
        expected_claim_version: int | None,
        policy_hash: str | None,
        fencing_token: int,
        mode: str,
        instant: datetime,
    ) -> None:
        if owner_run_id is not None and cycle.owner_run_id != owner_run_id:
            raise StaleWrite("search cycle owner changed")
        if (
            expected_claim_version is not None
            and cycle.claim_version != expected_claim_version
        ):
            raise StaleWrite("search cycle claim version changed")
        if policy_hash is not None and cycle.policy_hash != policy_hash:
            raise RepositoryAuthorizationDenied("search_cycle_policy_mismatch")
        self._assert_search_runtime_for_update(
            cycle,
            account_id=cycle.account_id,
            run_id=cycle.owner_run_id,
            policy_hash=cycle.policy_hash,
            fencing_token=fencing_token,
            mode=mode,
            instant=instant,
        )

    def _supersede_and_reset_search_items_for_update(
        self,
        cycle: SearchCycleRecord,
        *,
        run_id: int,
        instant: datetime,
        expected_status: str,
    ) -> None:
        self.list_search_results(cycle_id=cycle.id)
        cursor = self.conn.execute(
            """
            UPDATE hh_autopilot_search_cycles
            SET status = 'superseded', updated_at = ?
            WHERE id = ? AND status = ? AND claim_version = ?
            """,
            (instant.isoformat(), cycle.id, expected_status, cycle.claim_version),
        )
        if cursor.rowcount != 1:
            raise StaleWrite("search cycle supersede compare-and-swap failed")
        rows = self.conn.execute(
            """
            SELECT * FROM hh_autopilot_items
            WHERE origin_run_id = ?
              AND account_profile_id = ?
              AND state IN ('discovered','eligible','ranked','ready','retry_wait')
              AND application_attempt_count = 0
              AND active_attempt_id IS NULL
              AND EXISTS (
                  SELECT 1 FROM hh_autopilot_search_results AS result
                  WHERE result.cycle_id = ?
                    AND result.account_profile_id = hh_autopilot_items.account_profile_id
                    AND result.resume_id = hh_autopilot_items.resume_id
                    AND result.vacancy_id = hh_autopilot_items.vacancy_id
              )
            ORDER BY id ASC
            """,
            (cycle.origin_run_id, cycle.account_id, cycle.id),
        ).fetchall()
        for row in rows:
            item_id = _persisted_integer(
                row["id"],
                field="search reset item id",
                minimum=1,
            )
            version = _persisted_integer(
                row["version"],
                field="search reset item version",
            )
            state_value = _persisted_text(
                row["state"],
                field="search reset item state",
            )
            try:
                state = AutopilotState(state_value)
            except ValueError as exc:
                raise StaleWrite("search reset item state is invalid") from exc
            cursor = self.conn.execute(
                """
                UPDATE hh_autopilot_items
                SET state = 'discovered', retry_stage = 'eligibility',
                    filter_json = '{}', deterministic_score = NULL,
                    ai_json = '{}', next_attempt_at = '',
                    last_outcome_code = '', challenge_id = NULL,
                    last_run_id = ?, version = version + 1, updated_at = ?
                WHERE id = ? AND version = ? AND application_attempt_count = 0
                  AND active_attempt_id IS NULL
                """,
                (run_id, instant.isoformat(), item_id, version),
            )
            if cursor.rowcount != 1:
                raise StaleWrite("search item reset compare-and-swap failed")
            self._insert_event(
                item_id=item_id,
                run_id=run_id,
                previous=state,
                target=AutopilotState.DISCOVERED,
                reason="search_policy_superseded",
                metadata={"cycle_id": cycle.id},
                created_at=instant,
            )

    def _search_cycle_for_update(self, cycle_id: int) -> SearchCycleRecord:
        row = self.conn.execute(
            "SELECT * FROM hh_autopilot_search_cycles WHERE id = ?", (cycle_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"search cycle {cycle_id} does not exist")
        return self._search_cycle_from_row(row)

    def _search_checkpoint_for_update(
        self, checkpoint_id: int
    ) -> SearchCheckpointRecord:
        row = self.conn.execute(
            "SELECT * FROM hh_autopilot_search_checkpoints WHERE id = ?",
            (checkpoint_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"search checkpoint {checkpoint_id} does not exist")
        return self._search_checkpoint_from_row(row)

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

    def _assert_active_owned_run_for_update(
        self,
        item: ItemRecord,
        *,
        run_id: int,
        fencing_token: int | None,
        instant: datetime,
        operation: str,
    ) -> RunRecord:
        run = self._run_for_update(run_id)
        if run.account_id != item.account_id:
            raise ValueError(f"{operation} run and item accounts must match")
        if run.status != "running":
            raise StaleWrite(f"{operation} requires a running run")
        if item.last_run_id != run_id:
            raise ValueError(f"{operation} requires the same run as the item")
        if run.fencing_token == 0:
            if run.trigger != "manual":
                raise LostLease(
                    f"{operation} requires a persisted run fence"
                )
            if fencing_token is not None:
                raise LostLease(
                    f"{operation} cannot add a fence to an unfenced run"
                )
            return run
        if fencing_token != run.fencing_token:
            raise LostLease(
                f"{operation} requires the run fencing token"
            )
        self._assert_fence(item.account_id, run.fencing_token, instant)
        return run

    def _assert_candidate_set_unsealed_for_update(
        self,
        *,
        account_id: str,
        vacancy_id: str,
        run_id: int,
        operation: str,
    ) -> None:
        row = self.conn.execute(
            """
            SELECT 1
            FROM hh_autopilot_events AS event
            JOIN hh_autopilot_items AS item ON item.id = event.item_id
            WHERE item.account_profile_id = ?
              AND item.vacancy_id = ?
              AND event.run_id = ?
              AND event.previous_state = 'ranked'
              AND (
                    (
                        event.next_state = 'ready'
                        AND event.reason_code = 'ready'
                    )
                    OR
                    (
                        event.next_state = 'skipped'
                        AND event.reason_code = 'not_best_resume'
                    )
              )
            LIMIT 1
            """,
            (account_id, vacancy_id, run_id),
        ).fetchone()
        if row is not None:
            raise StaleWrite(f"{operation} candidate set is sealed")

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
    def _search_cycle_from_row(row: sqlite3.Row) -> SearchCycleRecord:
        status = _persisted_text(row["status"], field="search cycle status")
        if status not in {"running", "complete", "failed", "interrupted", "superseded"}:
            raise StaleWrite("invalid search cycle status in storage")
        mode = _persisted_text(row["mode"], field="search cycle mode")
        if mode not in {"live", "shadow"}:
            raise StaleWrite("invalid search cycle mode in storage")
        distinct_vacancy_cap = row["distinct_vacancy_cap"]
        if distinct_vacancy_cap is not None:
            distinct_vacancy_cap = _persisted_integer(
                distinct_vacancy_cap,
                field="search cycle distinct_vacancy_cap",
            )
        return SearchCycleRecord(
            id=_persisted_integer(row["id"], field="search cycle id", minimum=1),
            account_id=_persisted_text(
                row["account_profile_id"],
                field="search cycle account_id",
                canonical=True,
            ),
            policy_hash=_persisted_text(
                row["policy_hash"], field="search cycle policy_hash"
            ),
            origin_run_id=_persisted_integer(
                row["origin_run_id"], field="search cycle origin_run_id", minimum=1
            ),
            owner_run_id=_persisted_integer(
                row["owner_run_id"], field="search cycle owner_run_id", minimum=1
            ),
            claim_version=_persisted_integer(
                row["claim_version"], field="search cycle claim_version"
            ),
            fencing_token=_persisted_integer(
                row["fencing_token"],
                field="search cycle fencing_token",
                minimum=1,
                error_type=LostLease,
            ),
            mode=mode,
            distinct_vacancy_cap=distinct_vacancy_cap,
            status=status,
            created_at=_persisted_text(
                row["created_at"], field="search cycle created_at"
            ),
            updated_at=_persisted_text(
                row["updated_at"], field="search cycle updated_at"
            ),
        )

    @staticmethod
    def _search_checkpoint_from_row(row: sqlite3.Row) -> SearchCheckpointRecord:
        status = _persisted_text(row["status"], field="search checkpoint status")
        if status not in {"pending", "running", "complete", "failed"}:
            raise StaleWrite("invalid search checkpoint status in storage")
        reported_total = row["reported_total"]
        if reported_total is not None:
            reported_total = _persisted_integer(
                reported_total, field="search checkpoint reported_total"
            )
        return SearchCheckpointRecord(
            id=_persisted_integer(
                row["id"], field="search checkpoint id", minimum=1
            ),
            cycle_id=_persisted_integer(
                row["cycle_id"], field="search checkpoint cycle_id", minimum=1
            ),
            resume_id=_persisted_text(
                row["resume_id"],
                field="search checkpoint resume_id",
                canonical=True,
            ),
            query_key=_persisted_text(
                row["query_key"], field="search checkpoint query_key"
            ),
            next_page=_persisted_integer(
                row["next_page"], field="search checkpoint next_page"
            ),
            reported_total=reported_total,
            unique_vacancy_count=_persisted_integer(
                row["unique_vacancy_count"],
                field="search checkpoint unique_vacancy_count",
            ),
            status=status,
            updated_at=_persisted_text(
                row["updated_at"], field="search checkpoint updated_at"
            ),
        )

    @staticmethod
    def _search_result_from_row(row: sqlite3.Row) -> SearchResultRecord:
        vacancy_id = _persisted_text(
            row["vacancy_id"], field="search result vacancy_id"
        )
        return SearchResultRecord(
            id=_persisted_integer(row["id"], field="search result id", minimum=1),
            cycle_id=_persisted_integer(
                row["cycle_id"], field="search result cycle_id", minimum=1
            ),
            checkpoint_id=_persisted_integer(
                row["checkpoint_id"],
                field="search result checkpoint_id",
                minimum=1,
            ),
            account_id=_persisted_text(
                row["account_profile_id"],
                field="search result account_id",
                canonical=True,
            ),
            resume_id=_persisted_text(
                row["resume_id"],
                field="search result resume_id",
                canonical=True,
            ),
            query_key=_persisted_text(
                row["query_key"], field="search result query_key"
            ),
            vacancy_id=vacancy_id,
            page=_persisted_integer(row["page"], field="search result page"),
            normalized=_normalized_json_loads(
                row["normalized_json"], vacancy_id=vacancy_id
            ),
            discovered_at=_persisted_text(
                row["discovered_at"], field="search result discovered_at"
            ),
        )

    @staticmethod
    def _shadow_result_from_row(row: sqlite3.Row) -> ShadowResultRecord:
        raw_score = row["deterministic_score"]
        if raw_score is not None:
            if isinstance(raw_score, bool) or not isinstance(raw_score, (int, float)):
                raise StaleWrite("shadow result score is malformed")
            raw_score = float(raw_score)
            if raw_score != raw_score or abs(raw_score) == float("inf"):
                raise StaleWrite("shadow result score is malformed")
        would_apply = _persisted_integer(
            row["would_apply"], field="shadow result would_apply"
        )
        if would_apply not in {0, 1}:
            raise StaleWrite("shadow result would_apply is malformed")
        return ShadowResultRecord(
            id=_persisted_integer(row["id"], field="shadow result id", minimum=1),
            run_id=_persisted_integer(
                row["run_id"], field="shadow result run_id", minimum=1
            ),
            account_id=_persisted_text(
                row["account_profile_id"],
                field="shadow result account_id",
                canonical=True,
            ),
            vacancy_id=_persisted_text(
                row["vacancy_id"], field="shadow result vacancy_id"
            ),
            resume_id=_persisted_text(
                row["resume_id"],
                field="shadow result resume_id",
                canonical=True,
            ),
            filter_data=_json_loads(
                row["filter_json"], field="shadow result filter_json"
            ),
            deterministic_score=raw_score,
            ai_data=_json_loads(row["ai_json"], field="shadow result ai_json"),
            would_apply=bool(would_apply),
            created_at=_persisted_text(
                row["created_at"], field="shadow result created_at"
            ),
        )

    @staticmethod
    def _run_from_row(row: sqlite3.Row) -> RunRecord:
        trigger = _persisted_text(row["trigger"], field="run trigger")
        status = _persisted_text(row["status"], field="run status")
        if trigger not in RUN_TRIGGERS:
            raise StaleWrite(f"invalid run trigger in storage: {trigger}")
        if status not in RUN_STATUSES:
            raise StaleWrite(f"invalid run status in storage: {status}")
        return RunRecord(
            id=_persisted_integer(row["id"], field="run id", minimum=1),
            account_id=_persisted_text(
                row["account_profile_id"],
                field="run account_id",
                canonical=True,
            ),
            trigger=trigger,
            status=status,
            grant_id=(
                None
                if row["grant_id"] is None
                else _persisted_integer(
                    row["grant_id"],
                    field="run grant_id",
                    minimum=1,
                )
            ),
            policy_hash=_persisted_text(
                row["policy_hash"], field="run policy_hash"
            ),
            fencing_token=_persisted_integer(
                row["fencing_token"],
                field="run fencing_token",
                minimum=0,
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
        state_value = _persisted_text(row["state"], field="item state")
        retry_value = _persisted_text(
            row["retry_stage"],
            field="item retry_stage",
        )
        try:
            state = AutopilotState(state_value)
            retry_stage = RetryStage(retry_value)
        except ValueError as exc:
            raise StaleWrite("item state or retry stage is invalid") from exc
        deterministic_score = _persisted_score(
            row["deterministic_score"],
            field="item deterministic_score",
        )
        ai_data = _persisted_ai_data(
            row["ai_json"],
            deterministic_score=deterministic_score,
        )
        filter_data = _persisted_filter_data(row["filter_json"])
        last_outcome_code = _persisted_optional_text(
            row["last_outcome_code"],
            field="item last_outcome_code",
            maximum=128,
        )
        if state in {AutopilotState.DISCOVERED, AutopilotState.ELIGIBLE} and (
            deterministic_score is not None or ai_data
        ):
            raise StaleWrite("pre-ranking item contains ranking evidence")
        if state in {AutopilotState.RANKED, AutopilotState.READY} and (
            deterministic_score is None or not ai_data
        ):
            raise StaleWrite("ranked item is missing a complete ranking decision")
        if state in {
            AutopilotState.ELIGIBLE,
            AutopilotState.RANKED,
            AutopilotState.READY,
        }:
            if not filter_data:
                raise StaleWrite("item state is missing its filter decision")
            try:
                filter_decision = FilterDecision(
                    passed=filter_data["passed"],
                    reason=filter_data["reason"],
                    evidence=filter_data["evidence"],
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise StaleWrite("item filter decision is malformed") from exc
            if not filter_decision.passed:
                raise StaleWrite(
                    "eligible item requires a passing filter decision"
                )
        ranking_decision: RankingDecision | None = None
        if state in {AutopilotState.RANKED, AutopilotState.READY}:
            ranking_decision = _ranking_decision_from_data(
                ai_data,
                field="item ranking decision",
            )
        if state is AutopilotState.READY and (
            ranking_decision is None
            or not ranking_decision.ready
            or ranking_decision.retry
        ):
            raise StaleWrite("ready item requires a ready ranking decision")
        if state is AutopilotState.RANKED and last_outcome_code != ai_data["reason"]:
            raise StaleWrite("ranked item outcome disagrees with ranking decision")
        return ItemRecord(
            id=_persisted_integer(row["id"], field="item id", minimum=1),
            origin_run_id=_persisted_integer(
                row["origin_run_id"],
                field="item origin_run_id",
                minimum=1,
            ),
            last_run_id=_persisted_integer(
                row["last_run_id"],
                field="item last_run_id",
                minimum=1,
            ),
            account_id=_persisted_text(
                row["account_profile_id"],
                field="item account_id",
                canonical=True,
            ),
            vacancy_id=_persisted_text(
                row["vacancy_id"],
                field="item vacancy_id",
            ),
            resume_id=_persisted_text(
                row["resume_id"],
                field="item resume_id",
                canonical=True,
            ),
            query_key=_persisted_text(
                row["query_key"],
                field="item query_key",
            ),
            state=state,
            retry_stage=retry_stage,
            version=_persisted_integer(
                row["version"],
                field="item version",
            ),
            filter_data=filter_data,
            deterministic_score=deterministic_score,
            ai_data=ai_data,
            published_at=_persisted_optional_text(
                row["published_at"],
                field="item published_at",
                maximum=128,
            ),
            application_attempt_count=_persisted_integer(
                row["application_attempt_count"],
                field="item application_attempt_count",
            ),
            reconciliation_count=_persisted_integer(
                row["reconciliation_count"],
                field="item reconciliation_count",
            ),
            next_attempt_at=_persisted_optional_text(
                row["next_attempt_at"],
                field="item next_attempt_at",
                maximum=128,
            ),
            last_outcome_code=last_outcome_code,
            active_attempt_id=(
                None
                if row["active_attempt_id"] is None
                else _persisted_integer(
                    row["active_attempt_id"],
                    field="item active_attempt_id",
                    minimum=1,
                )
            ),
            challenge_id=(
                None
                if row["challenge_id"] is None
                else _persisted_integer(
                    row["challenge_id"],
                    field="item challenge_id",
                    minimum=1,
                )
            ),
        )

    @staticmethod
    def _attempt_from_row(row: sqlite3.Row) -> ApplicationAttemptRecord:
        certainty_value = _persisted_optional_text(
            row["delivery_certainty"],
            field="attempt delivery_certainty",
            maximum=64,
        )
        certainty: DeliveryCertainty | None
        if certainty_value:
            try:
                certainty = DeliveryCertainty(certainty_value)
            except ValueError as exc:
                raise StaleWrite("attempt delivery certainty is invalid") from exc
        else:
            certainty = None
        try:
            authorization_kind = AuthorizationKind(
                _persisted_text(
                    row["authorization_kind"],
                    field="attempt authorization_kind",
                )
            )
        except ValueError as exc:
            raise StaleWrite("attempt authorization kind is invalid") from exc
        return ApplicationAttemptRecord(
            id=_persisted_integer(row["id"], field="attempt id", minimum=1),
            account_id=_persisted_text(
                row["account_profile_id"],
                field="attempt account_id",
                canonical=True,
            ),
            run_id=_persisted_integer(
                row["autopilot_run_id"],
                field="attempt run_id",
                minimum=1,
            ),
            item_id=_persisted_integer(
                row["autopilot_item_id"],
                field="attempt item_id",
                minimum=1,
            ),
            autopilot_attempt_id=_persisted_integer(
                row["autopilot_attempt_id"],
                field="attempt compatibility id",
                minimum=1,
            ),
            vacancy_id=_persisted_text(
                row["vacancy_id"],
                field="attempt vacancy_id",
            ),
            resume_id=_persisted_text(
                row["resume_id"],
                field="attempt resume_id",
                canonical=True,
            ),
            status=_persisted_text(row["status"], field="attempt status"),
            reason=_persisted_optional_text(
                row["reason"],
                field="attempt reason",
                maximum=128,
            ),
            authorization_kind=authorization_kind,
            authorization_ref=_persisted_text(
                row["authorization_ref"],
                field="attempt authorization_ref",
            ),
            policy_hash=_persisted_text(
                row["policy_hash"],
                field="attempt policy_hash",
            ),
            delivery_certainty=certainty,
            raw_result=_json_loads(
                row["raw_result_json"],
                field="attempt raw_result_json",
            ),
            created_at=_persisted_text(
                row["created_at"],
                field="attempt created_at",
            ),
            dispatched_at=_persisted_optional_text(
                row["dispatched_at"],
                field="attempt dispatched_at",
                maximum=128,
            ),
            finished_at=_persisted_optional_text(
                row["finished_at"],
                field="attempt finished_at",
                maximum=128,
            ),
        )

    @staticmethod
    def _guard_from_row(row: sqlite3.Row) -> ApplicationGuardRecord:
        status = _persisted_text(row["status"], field="guard status")
        if status not in {"active", "applied", "external_applied"}:
            raise StaleWrite("guard status is invalid")
        return ApplicationGuardRecord(
            account_id=_persisted_text(
                row["account_profile_id"],
                field="guard account_id",
                canonical=True,
            ),
            source=_persisted_text(
                row["source"],
                field="guard source",
                canonical=True,
            ),
            source_id=_persisted_text(
                row["source_id"],
                field="guard source_id",
            ),
            owner_attempt_id=(
                None
                if row["owner_attempt_id"] is None
                else _persisted_integer(
                    row["owner_attempt_id"],
                    field="guard owner_attempt_id",
                    minimum=1,
                )
            ),
            first_resume_id=_persisted_text(
                row["first_resume_id"],
                field="guard first_resume_id",
                canonical=True,
            ),
            status=status,
            application_id=(
                None
                if row["application_id"] is None
                else _persisted_integer(
                    row["application_id"],
                    field="guard application_id",
                    minimum=1,
                )
            ),
            application_count=_persisted_integer(
                row["application_count"],
                field="guard application_count",
            ),
            created_at=_persisted_text(
                row["created_at"],
                field="guard created_at",
            ),
            updated_at=_persisted_text(
                row["updated_at"],
                field="guard updated_at",
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
        active = _persisted_integer(
            row["active"],
            field="grant active",
        )
        if active not in {0, 1}:
            raise ValueError(f"invalid grant active flag in storage: {active}")
        return GrantRecord(
            id=int(row["id"]),
            account_id=str(row["account_profile_id"]),
            scope=scope,
            policy_hash=str(row["policy_hash"]),
            generation=_persisted_integer(
                row["generation"],
                field="grant generation",
                minimum=1,
            ),
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
    "ApplicationAttemptRecord",
    "ApplicationGuardRecord",
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
    "SearchCheckpointRecord",
    "SearchCycleRecord",
    "SearchResultRecord",
    "RepositoryAuthorizationDenied",
    "ShadowResultRecord",
    "StaleWrite",
    "TimezoneChangeUnsafe",
]
