from __future__ import annotations

import copy
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from .sanitization import sanitize_text


class AutopilotState(StrEnum):
    DISCOVERED = "discovered"
    ELIGIBLE = "eligible"
    RANKED = "ranked"
    READY = "ready"
    APPLYING = "applying"
    RECONCILING = "reconciling"
    RETRY_WAIT = "retry_wait"
    MANUAL_CHALLENGE = "manual_challenge"
    APPLIED = "applied"
    SKIPPED = "skipped"
    DEAD = "dead"


class RetryStage(StrEnum):
    ELIGIBILITY = "eligibility"
    APPLICATION = "application"
    RECONCILIATION = "reconciliation"


class DeliveryCertainty(StrEnum):
    DEFINITELY_NOT_SENT = "definitely_not_sent"
    POSSIBLY_SENT = "possibly_sent"
    DEFINITE_RESPONSE = "definite_response"


class QuotaReservationState(StrEnum):
    RESERVED = "reserved"
    HELD = "held"
    CONSUMED = "consumed"
    RELEASED = "released"


STABLE_OUTCOME_CODES = frozenset(
    {
        "applied",
        "duplicate",
        "duplicate_external",
        "external_applied",
        "ambiguous_remote_result",
        "ambiguous_application",
        "unresolved_ambiguity",
        "vacancy_closed",
        "forbidden",
        "missing_required_data",
        "screening_disabled",
        "form_disabled",
        "ai_unavailable",
        "manual_assessment",
        "manual_captcha",
        "hh_daily_limit",
        "rate_limited",
        "auth_expired",
        "manual_auth",
        "challenge_expired",
        "challenge_dismissed",
        "pre_dispatch_network_error",
        "post_dispatch_network_error",
        "server_error",
        "read_parse_error",
        "post_dispatch_parse_error",
        "internal_error",
        "invalid_request",
        "retry_exhausted",
        "interrupted",
        "authorization_state_mismatch",
    }
)


class AuthorizationKind(StrEnum):
    LITERAL_CONFIRMATION = "literal_confirmation"
    AUTOPILOT = "autopilot"
    RECOVERY = "recovery"


@dataclass(frozen=True)
class LiteralConfirmation:
    account_id: str
    reference_id: str


@dataclass(frozen=True)
class LiveAuthorization:
    grant_id: int
    scope: str
    account_id: str
    run_id: int
    fencing_token: int
    policy_hash: str


@dataclass(frozen=True)
class RecoveryProvenance:
    attempt_id: int
    account_id: str
    authorization_kind: AuthorizationKind
    authorization_ref: str
    policy_hash: str


@dataclass(frozen=True)
class DispatchRequest:
    attempt_id: int
    account_id: str
    vacancy_id: str
    resume_id: str
    message: str = ""


@dataclass(frozen=True)
class DispatchOutcome:
    code: str
    certainty: DeliveryCertainty
    status_code: int | None = None
    retry_after_seconds: int | None = None
    location: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


def _text(value: Any, *, field_name: str, canonical: bool = False) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    value = value.strip()
    if canonical:
        value = value.casefold()
    if not value:
        raise ValueError(f"{field_name} must not be empty")
    if "\0" in value:
        raise ValueError(f"{field_name} must not contain NUL")
    return value


def _int(value: Any, *, field_name: str, minimum: int, maximum: int | None = None) -> int:
    if type(value) is not int:
        raise TypeError(f"{field_name} must be an integer")
    if value < minimum:
        raise ValueError(f"{field_name} must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{field_name} must be at most {maximum}")
    return value


def _mapping(value: Any, *, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be a mapping")
    detached: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise TypeError(f"{field_name} keys must be strings")
        _text(key, field_name=f"{field_name} key")
        detached[key] = copy.deepcopy(item)
    return detached


_FILTER_CHECKS = (
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
_CLOSED_VACANCY_STATUSES = frozenset(
    {"archived", "closed", "deleted", "not_published", "unpublished"}
)
_APPLICATION_CAPABILITIES = frozenset({"direct", "screening", "form"})
_ALLOWED_MISSING_FIELDS = frozenset(
    {
        "application_capabilities",
        "area_id",
        "blacklist",
        "blacklist.employer",
        "blacklist.employer_ids",
        "blacklist.vacancy",
        "blacklist.vacancy_ids",
        "candidate.citizenships",
        "candidate.languages",
        "candidate.relocation_allowed",
        "candidate.relocation_area_ids",
        "candidate.stop_words",
        "citizenships",
        "context.relocation_allowed",
        "context.relocation_area_ids",
        "context.supported_application_capabilities",
        "employment_id",
        "experience_id",
        "filters.allowed_role_families",
        "filters.areas",
        "filters.citizenships",
        "filters.employment_types",
        "filters.excluded_keywords",
        "filters.experience_levels",
        "filters.languages",
        "filters.minimum_salary",
        "filters.remote",
        "filters.required_application_capabilities",
        "filters.required_keywords",
        "filters.salary_currency",
        "filters.schedules",
        "filters.unknown_salary",
        "filters.use_employer_blacklist",
        "history",
        "history.active",
        "history.active_vacancy_ids",
        "history.already_applied",
        "history.applied_vacancy_ids",
        "history.permanently_skipped",
        "history.permanently_skipped_vacancy_ids",
        "languages",
        "professional_role_ids",
        "relocation",
        "remote",
        "resume.relocation_allowed",
        "resume.relocation_area_ids",
        "salary_currency",
        "schedule_id",
        "vacancy.application_capabilities",
        "vacancy.application_capability",
        "vacancy.apply_alternate_url",
        "vacancy.archived",
        "vacancy.area",
        "vacancy.area.id",
        "vacancy.area_id",
        "vacancy.description",
        "vacancy.employer",
        "vacancy.employer.name",
        "vacancy.employer_id",
        "vacancy.employer_name",
        "vacancy.employment",
        "vacancy.employment.id",
        "vacancy.employment_id",
        "vacancy.experience",
        "vacancy.experience.id",
        "vacancy.experience_id",
        "vacancy.has_test",
        "vacancy.id",
        "vacancy.key_skills",
        "vacancy.relocation_allowed",
        "vacancy.relocation_area_ids",
        "vacancy.remote",
        "vacancy.response_url",
        "vacancy.salary",
        "vacancy.salary.currency",
        "vacancy.salary.from",
        "vacancy.salary.to",
        "vacancy.salary_currency",
        "vacancy.salary_from",
        "vacancy.salary_to",
        "vacancy.schedule",
        "vacancy.schedule.id",
        "vacancy.schedule_id",
        "vacancy.status",
        "vacancy.title",
        "vacancy.work_format_ids",
        "vacancy_open",
    }
)
_HARD_FILTER_REASONS = frozenset(
    {
        "hard_filter:vacancy_closed",
        "hard_filter:already_applied",
        "hard_filter:active_history",
        "hard_filter:permanently_skipped",
        "hard_filter:vacancy_blacklist",
        "hard_filter:employer_blacklist",
        "hard_filter:excluded_keywords",
        "hard_filter:required_keywords",
        "hard_filter:allowed_role_families",
        "hard_filter:area",
        "hard_filter:remote",
        "hard_filter:schedule",
        "hard_filter:employment_type",
        "hard_filter:experience",
        "hard_filter:minimum_salary",
        "hard_filter:languages",
        "hard_filter:citizenships",
        "hard_filter:required_application_capabilities",
    }
)
_FILTER_REASONS = frozenset(
    {"hard_filters_passed", "missing_required_data", *_HARD_FILTER_REASONS}
)
_RANKING_REASONS = frozenset(
    {
        "deterministic_score",
        "deterministic_below_minimum",
        "deterministic_fallback",
        "ai_suitable",
        "ai_unsuitable",
        "ai_unavailable",
        "missing_required_data",
        *_HARD_FILTER_REASONS,
    }
)


def _clean_text(value: Any, *, field_name: str, maximum: int = 8_000) -> str:
    if type(value) is not str:
        raise TypeError(f"{field_name} must be a string")
    return sanitize_text(
        value,
        field=field_name,
        maximum=maximum,
        allow_empty=True,
        markup="strip",
        sensitive="redact",
        overflow="truncate",
    )


def _safe_output_text(
    value: Any,
    *,
    field_name: str,
    maximum: int,
    allow_empty: bool = False,
) -> str:
    if type(value) is not str:
        raise TypeError(f"{field_name} must be a string")
    return sanitize_text(
        value,
        field=field_name,
        maximum=maximum,
        allow_empty=allow_empty,
        markup="reject",
        sensitive="reject",
    )


def _filter_sequence(
    value: Any,
    *,
    field_name: str,
    maximum_items: int = 20,
) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TypeError(f"{field_name} must be a sequence")
    if len(value) > maximum_items:
        raise ValueError(f"{field_name} has too many values")
    return tuple(
        _safe_output_text(
            item,
            field_name=f"{field_name}[{index}]",
            maximum=100,
        )
        for index, item in enumerate(value)
    )


def _stable_identifier(value: str, *, field_name: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,99}", value):
        raise ValueError(f"{field_name} must be a stable identifier")
    return value


def _filter_evidence(
    reason: str,
    value: Any,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("evidence must be a mapping")
    if any(type(key) is not str for key in value):
        raise TypeError("evidence keys must be strings")
    evidence = dict(value)
    shape: tuple[frozenset[str], ...]
    if reason == "hard_filters_passed":
        shape = (frozenset({"checks"}),)
    elif reason == "missing_required_data":
        shape = (frozenset({"field"}),)
    elif reason == "hard_filter:vacancy_closed":
        shape = (frozenset({"archived", "status"}),)
    elif reason in {
        "hard_filter:already_applied",
        "hard_filter:active_history",
        "hard_filter:permanently_skipped",
        "hard_filter:vacancy_blacklist",
    }:
        shape = (frozenset({"vacancy_id"}),)
    elif reason == "hard_filter:employer_blacklist":
        shape = (frozenset({"employer_id"}),)
    elif reason == "hard_filter:excluded_keywords":
        shape = (frozenset({"matched"}),)
    elif reason in {
        "hard_filter:required_keywords",
        "hard_filter:languages",
        "hard_filter:citizenships",
    }:
        shape = (frozenset({"missing"}),)
    elif reason == "hard_filter:allowed_role_families":
        shape = (frozenset({"actual", "allowed"}),)
    elif reason == "hard_filter:area":
        shape = (frozenset({"area_id", "relocation_allowed"}),)
    elif reason == "hard_filter:remote":
        shape = (frozenset({"remote"}),)
    elif reason == "hard_filter:schedule":
        shape = (frozenset({"schedule"}),)
    elif reason == "hard_filter:employment_type":
        shape = (frozenset({"employment"}),)
    elif reason == "hard_filter:experience":
        shape = (frozenset({"experience"}),)
    elif reason == "hard_filter:minimum_salary":
        shape = (
            frozenset({"salary_known", "minimum"}),
            frozenset({"currency", "expected_currency"}),
            frozenset({"maximum", "minimum"}),
        )
    elif reason == "hard_filter:required_application_capabilities":
        shape = (frozenset({"required", "unavailable"}),)
    else:
        raise ValueError("unsupported filter reason")
    if frozenset(evidence) not in shape:
        raise ValueError("evidence shape does not match filter reason")

    frozen: dict[str, Any] = {}
    for key, item in evidence.items():
        if key in {
            "checks",
            "matched",
            "missing",
            "actual",
            "allowed",
            "required",
            "unavailable",
        }:
            frozen[key] = _filter_sequence(item, field_name=f"evidence.{key}")
        elif key in {"archived", "relocation_allowed", "remote", "salary_known"}:
            if type(item) is not bool:
                raise TypeError(f"evidence.{key} must be a boolean")
            frozen[key] = item
        elif key in {"minimum", "maximum"}:
            if type(item) not in {int, float}:
                raise TypeError(f"evidence.{key} must be a number")
            parsed_number = float(item)
            if (
                not math.isfinite(parsed_number)
                or not 0 <= parsed_number <= 1_000_000_000
            ):
                raise ValueError(f"evidence.{key} is out of range")
            frozen[key] = item
        else:
            maximum = 128 if key == "field" else 100
            parsed_text = _safe_output_text(
                item,
                field_name=f"evidence.{key}",
                maximum=maximum,
                allow_empty=key == "status",
            )
            if key == "field" and not re.fullmatch(
                r"[A-Za-z0-9_.\[\]-]+",
                parsed_text,
            ):
                raise ValueError("evidence.field is not a stable field name")
            if key == "field" and parsed_text not in _ALLOWED_MISSING_FIELDS:
                raise ValueError("evidence.field is not emitted by the hard filter")
            if key in {
                "area_id",
                "currency",
                "employer_id",
                "employment",
                "expected_currency",
                "experience",
                "schedule",
                "vacancy_id",
            } or (key == "status" and parsed_text):
                parsed_text = _stable_identifier(
                    parsed_text,
                    field_name=f"evidence.{key}",
                )
            frozen[key] = parsed_text
    if reason == "hard_filters_passed" and frozen["checks"] != _FILTER_CHECKS:
        raise ValueError("passing evidence must contain the complete filter checks")
    if reason in {
        "hard_filter:excluded_keywords",
        "hard_filter:required_keywords",
        "hard_filter:languages",
        "hard_filter:citizenships",
    }:
        key = "matched" if reason == "hard_filter:excluded_keywords" else "missing"
        if not frozen[key]:
            raise ValueError(f"evidence.{key} must not be empty")
    if reason == "hard_filter:allowed_role_families":
        actual = frozen["actual"]
        allowed = frozen["allowed"]
        if not actual or not allowed:
            raise ValueError("role evidence sets must not be empty")
        for key, values in (("actual", actual), ("allowed", allowed)):
            for index, item in enumerate(values):
                _stable_identifier(
                    item,
                    field_name=f"evidence.{key}[{index}]",
                )
        if set(actual).intersection(allowed):
            raise ValueError("rejected role evidence sets must not overlap")
    if (
        reason == "hard_filter:area"
        and frozen["relocation_allowed"] is not False
    ):
        raise ValueError("rejected area evidence must deny relocation")
    if reason == "hard_filter:minimum_salary":
        keys = frozenset(frozen)
        if keys == {"salary_known", "minimum"}:
            if frozen["salary_known"] is not False or frozen["minimum"] <= 0:
                raise ValueError("unknown salary evidence is contradictory")
        elif keys == {"currency", "expected_currency"}:
            if frozen["currency"].casefold() == frozen["expected_currency"].casefold():
                raise ValueError("currency rejection must contain a mismatch")
        elif keys == {"maximum", "minimum"}:
            if frozen["minimum"] <= 0 or frozen["maximum"] >= frozen["minimum"]:
                raise ValueError("salary rejection must be below the minimum")
    if reason == "hard_filter:required_application_capabilities":
        required = frozen["required"]
        unavailable = frozen["unavailable"]
        if (
            not required
            or not unavailable
            or not set(required) <= _APPLICATION_CAPABILITIES
            or not set(unavailable) <= set(required)
        ):
            raise ValueError("capability evidence is contradictory")
    if reason == "hard_filter:vacancy_closed":
        if (
            frozen["archived"] is not True
            and frozen["status"].casefold() not in _CLOSED_VACANCY_STATUSES
        ):
            raise ValueError("vacancy-closed evidence does not prove closure")
    return MappingProxyType(frozen)


def _json_value(value: Any, *, field_name: str, depth: int = 0) -> Any:
    if depth > 8:
        raise ValueError(f"{field_name} is nested too deeply")
    if value is None or type(value) in {bool, int}:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError(f"{field_name} contains a non-finite number")
        return value
    if isinstance(value, str):
        if "\0" in value:
            raise ValueError(f"{field_name} must not contain NUL")
        return value
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        if len(value) > 500:
            raise ValueError(f"{field_name} has too many keys")
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{field_name} keys must be strings")
            _text(key, field_name=f"{field_name} key")
            result[key] = _json_value(
                item, field_name=f"{field_name}.{key}", depth=depth + 1
            )
        return result
    if isinstance(value, (list, tuple)):
        if len(value) > 500:
            raise ValueError(f"{field_name} has too many values")
        return [
            _json_value(item, field_name=f"{field_name}[{index}]", depth=depth + 1)
            for index, item in enumerate(value)
        ]
    raise TypeError(f"{field_name} contains a non-JSON value")


def _strict_number(
    value: Any,
    *,
    field_name: str,
    minimum: float,
    maximum: float,
) -> float:
    if type(value) not in {int, float}:
        raise TypeError(f"{field_name} must be a number")
    parsed = float(value)
    if type(value) is int and int(parsed) != value:
        raise ValueError(f"{field_name} cannot be represented without loss")
    if not math.isfinite(parsed):
        raise ValueError(f"{field_name} must be finite")
    if not minimum <= parsed <= maximum:
        raise ValueError(f"{field_name} must be in {minimum}..{maximum}")
    return 0.0 if parsed == 0.0 else parsed


def _bounded_text(
    value: Any,
    *,
    field_name: str,
    maximum: int,
    allow_empty: bool = False,
    canonical: bool = False,
) -> str:
    if type(value) is not str:
        raise TypeError(f"{field_name} must be a string")
    parsed = re.sub(r"\s+", " ", value).strip()
    if canonical:
        parsed = parsed.casefold()
    if not parsed and not allow_empty:
        raise ValueError(f"{field_name} must not be empty")
    if "\0" in parsed:
        raise ValueError(f"{field_name} must not contain NUL")
    if len(parsed) > maximum:
        raise ValueError(f"{field_name} must be at most {maximum} characters")
    return parsed


def _freeze_json(value: Any, *, field_name: str, depth: int = 0) -> Any:
    if depth > 6:
        raise ValueError(f"{field_name} is nested too deeply")
    if value is None or type(value) is bool:
        return value
    if type(value) is int:
        if abs(value) > 1_000_000_000:
            raise ValueError(f"{field_name} contains an oversized number")
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError(f"{field_name} contains a non-finite number")
        if abs(value) > 1_000_000_000:
            raise ValueError(f"{field_name} contains an oversized number")
        return value
    if type(value) is str:
        if "\0" in value:
            raise ValueError(f"{field_name} contains NUL")
        if len(value) > 300:
            raise ValueError(f"{field_name} contains an oversized string")
        return value
    if isinstance(value, Mapping):
        if len(value) > 30:
            raise ValueError(f"{field_name} has too many keys")
        detached: dict[str, Any] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise TypeError(f"{field_name} keys must be strings")
            normalized_key = _bounded_text(
                key,
                field_name=f"{field_name} key",
                maximum=100,
            )
            if normalized_key in detached:
                raise ValueError(f"{field_name} has duplicate normalized keys")
            detached[normalized_key] = _freeze_json(
                item,
                field_name=f"{field_name}.{normalized_key}",
                depth=depth + 1,
            )
        return MappingProxyType(detached)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        if len(value) > 20:
            raise ValueError(f"{field_name} has too many values")
        return tuple(
            _freeze_json(
                item,
                field_name=f"{field_name}[{index}]",
                depth=depth + 1,
            )
            for index, item in enumerate(value)
        )
    raise TypeError(f"{field_name} contains a non-JSON value")


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _component_mapping(
    value: Any,
    *,
    field_name: str,
    minimum: float,
    maximum: float,
) -> Mapping[str, float]:
    components = (
        "role",
        "skills",
        "experience",
        "salary",
        "work_format",
        "area",
        "industry",
    )
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be a mapping")
    if set(value) != set(components):
        raise ValueError(f"{field_name} must contain exactly the fixed components")
    return MappingProxyType(
        {
            name: _strict_number(
                value[name],
                field_name=f"{field_name}.{name}",
                minimum=minimum,
                maximum=maximum,
            )
            for name in components
        }
    )


@dataclass(frozen=True)
class FilterDecision:
    passed: bool
    reason: str
    evidence: Mapping[str, Any]

    def __post_init__(self) -> None:
        if type(self.passed) is not bool:
            raise TypeError("passed must be a boolean")
        reason = _bounded_text(self.reason, field_name="reason", maximum=128)
        if reason not in _FILTER_REASONS:
            raise ValueError("reason is not an approved filter reason")
        evidence = _filter_evidence(reason, self.evidence)
        if self.passed and reason != "hard_filters_passed":
            raise ValueError("a passing filter decision must use hard_filters_passed")
        if not self.passed and reason == "hard_filters_passed":
            raise ValueError("a rejected filter decision cannot use hard_filters_passed")
        object.__setattr__(self, "reason", reason)
        object.__setattr__(self, "evidence", evidence)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "reason": self.reason,
            "evidence": {
                key: list(item) if isinstance(item, tuple) else item
                for key, item in self.evidence.items()
            },
        }


@dataclass(frozen=True)
class RankScore:
    score: float
    components: Mapping[str, float]
    weights: Mapping[str, float]

    def __post_init__(self) -> None:
        score = _strict_number(
            self.score,
            field_name="score",
            minimum=0.0,
            maximum=100.0,
        )
        components = _component_mapping(
            self.components,
            field_name="components",
            minimum=0.0,
            maximum=100.0,
        )
        weights = _component_mapping(
            self.weights,
            field_name="weights",
            minimum=0.0,
            maximum=1.0,
        )
        total = math.fsum(weights.values())
        if not math.isclose(total, 1.0, rel_tol=0.0, abs_tol=1e-9):
            raise ValueError("weights must sum to 1")
        expected_score = round(
            math.fsum(
                components[name] * weights[name] for name in components
            ),
            4,
        )
        if expected_score == 0.0:
            expected_score = 0.0
        if not math.isclose(
            score,
            expected_score,
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            raise ValueError("score must equal the canonical weighted total")
        object.__setattr__(self, "score", expected_score)
        object.__setattr__(self, "components", components)
        object.__setattr__(self, "weights", weights)

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "components": dict(self.components),
            "weights": dict(self.weights),
        }


@dataclass(frozen=True)
class AIDecision:
    available: bool
    suitable: bool | None
    confidence: float | None
    evidence: tuple[str, ...] | Sequence[str] = ()
    reasons: tuple[str, ...] | Sequence[str] = ()
    reason: str = "available"

    def __post_init__(self) -> None:
        if type(self.available) is not bool:
            raise TypeError("available must be a boolean")
        reason = _bounded_text(self.reason, field_name="reason", maximum=128)
        evidence = self._strings(self.evidence, field_name="evidence")
        reasons = self._strings(self.reasons, field_name="reasons")
        if self.available:
            if type(self.suitable) is not bool:
                raise TypeError("available AI suitable must be a boolean")
            if self.confidence is None:
                raise TypeError("available AI confidence is required")
            confidence = _strict_number(
                self.confidence,
                field_name="confidence",
                minimum=0.0,
                maximum=1.0,
            )
            if reason != "available":
                raise ValueError("available AI decision must use reason available")
        else:
            if self.suitable is not None or self.confidence is not None:
                raise ValueError(
                    "unavailable AI decision cannot contain suitability or confidence"
                )
            if evidence or reasons:
                raise ValueError("unavailable AI decision cannot retain model output")
            if reason != "ai_unavailable":
                raise ValueError(
                    "unavailable AI decision must use reason ai_unavailable"
                )
            confidence = None
        object.__setattr__(self, "confidence", confidence)
        object.__setattr__(self, "evidence", evidence)
        object.__setattr__(self, "reasons", reasons)
        object.__setattr__(self, "reason", reason)

    @staticmethod
    def _strings(value: Any, *, field_name: str) -> tuple[str, ...]:
        if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
            raise TypeError(f"{field_name} must be a sequence")
        if len(value) > 20:
            raise ValueError(f"{field_name} must contain at most 20 values")
        return tuple(
            _safe_output_text(
                item,
                field_name=f"{field_name}[{index}]",
                maximum=300,
                allow_empty=True,
            )
            for index, item in enumerate(value)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "suitable": self.suitable,
            "confidence": self.confidence,
            "evidence": list(self.evidence),
            "reasons": list(self.reasons),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class RankingDecision:
    ready: bool
    retry: bool
    reason: str
    rank_score: RankScore
    ai_decision: AIDecision | None = None

    def __post_init__(self) -> None:
        if type(self.ready) is not bool or type(self.retry) is not bool:
            raise TypeError("ready and retry must be booleans")
        if self.ready and self.retry:
            raise ValueError("a ranking decision cannot be ready and retry")
        if not isinstance(self.rank_score, RankScore):
            raise TypeError("rank_score must be a RankScore")
        if self.ai_decision is not None and not isinstance(
            self.ai_decision, AIDecision
        ):
            raise TypeError("ai_decision must be an AIDecision or None")
        reason = _bounded_text(self.reason, field_name="reason", maximum=128)
        if reason not in _RANKING_REASONS:
            raise ValueError("reason is not an approved ranking reason")
        ai = self.ai_decision
        if reason in _HARD_FILTER_REASONS or reason == "missing_required_data":
            if self.ready or self.retry or ai is not None:
                raise ValueError("hard-filter ranking outcomes cannot be ready or use AI")
        elif reason == "deterministic_score":
            if not self.ready or self.retry or ai is not None:
                raise ValueError("deterministic_score must be a ready non-AI outcome")
        elif reason == "deterministic_below_minimum":
            if self.ready or self.retry or ai is not None:
                raise ValueError(
                    "deterministic_below_minimum must be a pure skipped outcome"
                )
        elif reason == "deterministic_fallback":
            if self.retry or ai is None or ai.available:
                raise ValueError(
                    "deterministic_fallback requires an unavailable AI result"
                )
        elif reason == "ai_suitable":
            if (
                not self.ready
                or self.retry
                or ai is None
                or not ai.available
                or ai.suitable is not True
            ):
                raise ValueError("ai_suitable requires an available suitable AI result")
        elif reason == "ai_unsuitable":
            if (
                self.ready
                or self.retry
                or ai is None
                or not ai.available
                or ai.suitable is not False
            ):
                raise ValueError(
                    "ai_unsuitable requires an available unsuitable AI result"
                )
        elif reason == "ai_unavailable":
            if self.ready or ai is None or ai.available:
                raise ValueError(
                    "ai_unavailable requires an unavailable non-ready AI outcome"
                )
        object.__setattr__(self, "reason", reason)

    @property
    def score(self) -> float:
        return self.rank_score.score

    @property
    def ai_confidence(self) -> float | None:
        return (
            None
            if self.ai_decision is None or not self.ai_decision.available
            else self.ai_decision.confidence
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "retry": self.retry,
            "reason": self.reason,
            "rank_score": self.rank_score.to_dict(),
            "ai_decision": (
                None
                if self.ai_decision is None
                else self.ai_decision.to_dict()
            ),
        }


@dataclass(frozen=True)
class RankedCandidate:
    account_id: str
    vacancy_id: str
    resume_id: str
    published_at: str
    decision: RankingDecision
    item_id: int | None = None
    expected_version: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "account_id",
            _bounded_text(
                self.account_id,
                field_name="account_id",
                maximum=256,
                canonical=True,
            ),
        )
        object.__setattr__(
            self,
            "vacancy_id",
            _bounded_text(
                self.vacancy_id,
                field_name="vacancy_id",
                maximum=256,
            ),
        )
        object.__setattr__(
            self,
            "resume_id",
            _bounded_text(
                self.resume_id,
                field_name="resume_id",
                maximum=256,
                canonical=True,
            ),
        )
        object.__setattr__(
            self,
            "published_at",
            _bounded_text(
                self.published_at,
                field_name="published_at",
                maximum=128,
                allow_empty=True,
            ),
        )
        if not isinstance(self.decision, RankingDecision):
            raise TypeError("decision must be a RankingDecision")
        if self.item_id is not None:
            object.__setattr__(
                self,
                "item_id",
                _int(self.item_id, field_name="item_id", minimum=1),
            )
        if self.expected_version is not None:
            object.__setattr__(
                self,
                "expected_version",
                _int(
                    self.expected_version,
                    field_name="expected_version",
                    minimum=0,
                ),
            )
        if (self.item_id is None) != (self.expected_version is None):
            raise ValueError("item_id and expected_version must be supplied together")

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "vacancy_id": self.vacancy_id,
            "resume_id": self.resume_id,
            "published_at": self.published_at,
            "decision": self.decision.to_dict(),
            "item_id": self.item_id,
            "expected_version": self.expected_version,
        }


@dataclass(frozen=True)
class SearchPage:
    items: tuple[dict[str, Any], ...] | Sequence[Mapping[str, Any]]
    page: int
    pages: int
    per_page: int
    total: int

    def __post_init__(self) -> None:
        if isinstance(self.items, (str, bytes)) or not isinstance(self.items, Sequence):
            raise TypeError("items must be a sequence of mappings")
        detached: list[dict[str, Any]] = []
        for index, item in enumerate(self.items):
            detached.append(_mapping(item, field_name=f"items[{index}]"))
        page = _int(self.page, field_name="page", minimum=0)
        pages = _int(self.pages, field_name="pages", minimum=0)
        per_page = _int(self.per_page, field_name="per_page", minimum=1, maximum=100)
        total = _int(self.total, field_name="total", minimum=0)
        if len(detached) > per_page:
            raise ValueError("items cannot exceed per_page")
        if pages > 0 and page >= pages:
            raise ValueError("page must be lower than pages")
        if detached and total == 0:
            raise ValueError("a non-empty page must report a positive total")
        if pages == 0 and total > 0:
            raise ValueError("a positive total must report at least one page")
        if page * per_page + len(detached) > total and total > 0:
            raise ValueError("page items exceed the reported total")
        object.__setattr__(self, "items", tuple(detached))
        object.__setattr__(self, "page", page)
        object.__setattr__(self, "pages", pages)
        object.__setattr__(self, "per_page", per_page)
        object.__setattr__(self, "total", total)


@dataclass(frozen=True)
class SearchRequest:
    account_id: str
    run_id: int
    resume_id: str
    query_key: str
    params: dict[str, Any] | Mapping[str, Any]
    per_page: int
    max_pages: int
    remaining_budget: int
    policy_hash: str
    fencing_token: int
    mode: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "account_id", _text(self.account_id, field_name="account_id", canonical=True))
        object.__setattr__(self, "run_id", _int(self.run_id, field_name="run_id", minimum=1))
        object.__setattr__(
            self,
            "resume_id",
            _text(self.resume_id, field_name="resume_id", canonical=True),
        )
        object.__setattr__(self, "query_key", _text(self.query_key, field_name="query_key"))
        if not isinstance(self.params, Mapping):
            raise TypeError("params must be a mapping")
        detached_params = _json_value(self.params, field_name="params")
        assert isinstance(detached_params, dict)
        object.__setattr__(self, "params", detached_params)
        object.__setattr__(self, "per_page", _int(self.per_page, field_name="per_page", minimum=1, maximum=100))
        object.__setattr__(self, "max_pages", _int(self.max_pages, field_name="max_pages", minimum=1, maximum=100))
        object.__setattr__(self, "remaining_budget", _int(self.remaining_budget, field_name="remaining_budget", minimum=0))
        object.__setattr__(self, "policy_hash", _text(self.policy_hash, field_name="policy_hash"))
        object.__setattr__(self, "fencing_token", _int(self.fencing_token, field_name="fencing_token", minimum=1))
        if type(self.mode) is not str or self.mode not in {"live", "shadow"}:
            raise ValueError("mode must be live or shadow")


@dataclass(frozen=True)
class NormalizedVacancy:
    id: str
    title: str
    employer_id: str = ""
    employer_name: str = ""
    area_id: str = ""
    area_name: str = ""
    salary_from: int | None = None
    salary_to: int | None = None
    salary_currency: str = ""
    salary_gross: bool | None = None
    schedule_id: str = ""
    work_format_ids: tuple[str, ...] = ()
    employment_id: str = ""
    experience_id: str = ""
    professional_role_ids: tuple[str, ...] = ()
    key_skills: tuple[str, ...] = ()
    published_at: str = ""
    url: str = ""
    archived: bool | None = None
    status: str = ""
    vacancy_type: str = ""
    description: str = ""
    response_url: str = ""
    apply_alternate_url: str = ""
    relations: tuple[str, ...] = ()
    has_test: bool | None = None
    response_letter_required: bool | None = None
    accept_incomplete_resumes: bool | None = None
    accept_temporary: bool | None = None
    job: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _text(self.id, field_name="vacancy id"))
        for field_name in (
            "title",
            "employer_id",
            "employer_name",
            "area_id",
            "area_name",
            "salary_currency",
            "schedule_id",
            "employment_id",
            "experience_id",
            "published_at",
            "url",
            "status",
            "vacancy_type",
            "description",
            "response_url",
            "apply_alternate_url",
        ):
            object.__setattr__(
                self,
                field_name,
                _clean_text(getattr(self, field_name), field_name=field_name),
            )
        for field_name in ("salary_from", "salary_to"):
            value = getattr(self, field_name)
            if value is not None and type(value) is not int:
                raise TypeError(f"{field_name} must be an integer or None")
            if value is not None and value < 0:
                raise ValueError(f"{field_name} must not be negative")
        if self.salary_gross is not None and type(self.salary_gross) is not bool:
            raise TypeError("salary_gross must be a boolean or None")
        for field_name in (
            "archived",
            "has_test",
            "response_letter_required",
            "accept_incomplete_resumes",
            "accept_temporary",
        ):
            value = getattr(self, field_name)
            if value is not None and type(value) is not bool:
                raise TypeError(f"{field_name} must be a boolean or None")
        for field_name in (
            "work_format_ids",
            "professional_role_ids",
            "key_skills",
            "relations",
        ):
            value = getattr(self, field_name)
            if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
                raise TypeError(f"{field_name} must be a sequence")
            cleaned = tuple(
                _clean_text(item, field_name=f"{field_name} item", maximum=256)
                for item in value[:100]
            )
            object.__setattr__(self, field_name, cleaned)
        if not isinstance(self.job, Mapping):
            raise TypeError("job must be a mapping")
        expected_job_keys = {
            "source",
            "source_id",
            "url",
            "title",
            "company",
            "salary_text",
            "salary_from",
            "salary_to",
            "currency",
            "location",
            "remote",
            "description",
            "published_at",
            "fetched_at",
            "id",
            "status",
            "score",
        }
        if set(self.job) != expected_job_keys:
            raise ValueError("job must use the exact normalized Job schema")
        detached_job = _json_value(self.job, field_name="job")
        assert isinstance(detached_job, dict)
        for key in (
            "source",
            "source_id",
            "url",
            "title",
            "company",
            "salary_text",
            "currency",
            "location",
            "description",
            "published_at",
            "fetched_at",
            "status",
        ):
            if type(detached_job[key]) is not str:
                raise TypeError(f"job.{key} must be a string")
        for key, value in tuple(detached_job.items()):
            if isinstance(value, str):
                detached_job[key] = _clean_text(
                    value, field_name=f"job.{key}", maximum=8_000
                )
        for key in ("salary_from", "salary_to"):
            value = detached_job[key]
            if value is not None and (type(value) is not int or value < 0):
                raise TypeError(f"job.{key} must be a nonnegative integer or None")
        if detached_job["remote"] is not None and type(detached_job["remote"]) is not bool:
            raise TypeError("job.remote must be a boolean or None")
        if detached_job["id"] is not None or detached_job["score"] is not None:
            raise ValueError("normalized job id and score must be None")
        if detached_job["source"] != "hh" or detached_job["source_id"] != self.id:
            raise ValueError("normalized job source provenance does not match vacancy")
        expected_salary_text = ""
        if self.salary_from is not None and self.salary_to is not None:
            expected_salary_text = (
                f"{self.salary_from}-{self.salary_to} {self.salary_currency}".strip()
            )
        elif self.salary_from is not None:
            expected_salary_text = (
                f"from {self.salary_from} {self.salary_currency}".strip()
            )
        elif self.salary_to is not None:
            expected_salary_text = (
                f"up to {self.salary_to} {self.salary_currency}".strip()
            )
        if detached_job["salary_text"] != expected_salary_text:
            raise ValueError("normalized job salary_text does not match salary facts")
        if detached_job["status"] != "new":
            raise ValueError("normalized job status must be new")
        try:
            fetched_at = datetime.fromisoformat(detached_job["fetched_at"])
        except ValueError as exc:
            raise ValueError("normalized job fetched_at must be ISO-8601") from exc
        if (
            fetched_at.tzinfo is None
            or fetched_at.utcoffset() != timedelta(0)
            or fetched_at.microsecond != 0
            or fetched_at.isoformat() != detached_job["fetched_at"]
        ):
            raise ValueError("normalized job fetched_at must be canonical UTC seconds")
        expected_remote = None if not self.schedule_id else self.schedule_id == "remote"
        if detached_job["remote"] is not expected_remote:
            raise ValueError("normalized job remote fact does not match schedule")
        mirrored = {
            "url": self.url,
            "title": self.title,
            "company": self.employer_name,
            "salary_from": self.salary_from,
            "salary_to": self.salary_to,
            "currency": self.salary_currency,
            "location": self.area_name,
            "description": self.description,
            "published_at": self.published_at,
        }
        for key, expected in mirrored.items():
            if detached_job[key] != expected:
                raise ValueError(f"normalized job {key} does not match vacancy")
        object.__setattr__(self, "job", detached_job)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> NormalizedVacancy:
        if not isinstance(value, Mapping):
            raise TypeError("normalized vacancy must be a mapping")
        expected = {
            "id",
            "title",
            "employer_id",
            "employer_name",
            "area_id",
            "area_name",
            "salary_from",
            "salary_to",
            "salary_currency",
            "salary_gross",
            "schedule_id",
            "work_format_ids",
            "employment_id",
            "experience_id",
            "professional_role_ids",
            "key_skills",
            "published_at",
            "url",
            "archived",
            "status",
            "vacancy_type",
            "description",
            "response_url",
            "apply_alternate_url",
            "relations",
            "has_test",
            "response_letter_required",
            "accept_incomplete_resumes",
            "accept_temporary",
            "job",
        }
        if set(value) != expected:
            raise ValueError("normalized vacancy must use the exact persisted schema")
        return cls(**copy.deepcopy(dict(value)))

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(
            {
                "id": self.id,
                "title": self.title,
                "employer_id": self.employer_id,
                "employer_name": self.employer_name,
                "area_id": self.area_id,
                "area_name": self.area_name,
                "salary_from": self.salary_from,
                "salary_to": self.salary_to,
                "salary_currency": self.salary_currency,
                "salary_gross": self.salary_gross,
                "schedule_id": self.schedule_id,
                "work_format_ids": list(self.work_format_ids),
                "employment_id": self.employment_id,
                "experience_id": self.experience_id,
                "professional_role_ids": list(self.professional_role_ids),
                "key_skills": list(self.key_skills),
                "published_at": self.published_at,
                "url": self.url,
                "archived": self.archived,
                "status": self.status,
                "vacancy_type": self.vacancy_type,
                "description": self.description,
                "response_url": self.response_url,
                "apply_alternate_url": self.apply_alternate_url,
                "relations": list(self.relations),
                "has_test": self.has_test,
                "response_letter_required": self.response_letter_required,
                "accept_incomplete_resumes": self.accept_incomplete_resumes,
                "accept_temporary": self.accept_temporary,
                "job": self.job,
            }
        )


@dataclass(frozen=True)
class SearchResult:
    vacancies: tuple[NormalizedVacancy, ...]
    next_page: int
    cycle_id: int
    inserted_count: int = 0
    new_distinct_count: int = 0

    @property
    def inserted_reference_count(self) -> int:
        return self.inserted_count

    def __post_init__(self) -> None:
        if isinstance(self.vacancies, (str, bytes)) or not isinstance(
            self.vacancies, Sequence
        ):
            raise TypeError("vacancies must be a sequence")
        vacancies = tuple(self.vacancies)
        if any(not isinstance(item, NormalizedVacancy) for item in vacancies):
            raise TypeError("vacancies must contain only NormalizedVacancy values")
        object.__setattr__(self, "vacancies", vacancies)
        object.__setattr__(
            self, "next_page", _int(self.next_page, field_name="next_page", minimum=0)
        )
        object.__setattr__(
            self, "cycle_id", _int(self.cycle_id, field_name="cycle_id", minimum=1)
        )
        object.__setattr__(
            self,
            "inserted_count",
            _int(self.inserted_count, field_name="inserted_count", minimum=0),
        )
        object.__setattr__(
            self,
            "new_distinct_count",
            _int(
                self.new_distinct_count,
                field_name="new_distinct_count",
                minimum=0,
            ),
        )
