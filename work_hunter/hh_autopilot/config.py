from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Any, Collection, Mapping, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .types import AuthorizationKind


class AutopilotConfigError(ValueError):
    pass


class ValidationMode(StrEnum):
    AUTONOMOUS = "autonomous"
    MANUAL = "manual"
    CANARY = "canary"
    SHADOW = "shadow"
    RECOVERY = "recovery"


@dataclass(frozen=True)
class AccountSettings:
    profile_id: str
    candidate_profile_id: str
    enabled: bool = False
    paused: bool = False
    authorization_generation: int | None = None
    resume_queries: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class SearchSettings:
    include_recommendations: bool = True
    per_page: int = 100
    max_pages: int = 20
    max_results_per_run: int = 2000


@dataclass(frozen=True)
class LimitSettings:
    daily_success: int = 50
    per_run_success: int = 10
    administrative_max_daily_success: int = 200
    send_delay_min_seconds: int = 45
    send_delay_max_seconds: int = 120


@dataclass(frozen=True)
class LeaseSettings:
    ttl_seconds: int = 120
    request_timeout_seconds: int = 30
    renewal_margin_seconds: int = 45


@dataclass(frozen=True)
class AutopilotSettings:
    timezone: str
    accounts: tuple[AccountSettings, ...]
    schedule: dict[str, Any]
    search: SearchSettings
    filters: dict[str, Any]
    limits: LimitSettings
    ranking: dict[str, Any]
    retry: dict[str, Any]
    lease: LeaseSettings
    application: dict[str, Any]
    browser: dict[str, Any]
    notifications: dict[str, Any]
    retention: dict[str, Any]


@dataclass(frozen=True)
class PolicyMaterial:
    effective_auth_profile_id: str
    candidate_profile: dict[str, Any]
    candidate_profile_version: str
    resumes: list[dict[str, Any]]
    presets: dict[str, dict[str, Any]]
    model_id: str = ""
    prompt_versions: dict[str, str] = field(default_factory=dict)
    cover_letter_template_version: str = ""
    transport_identity: dict[str, Any] = field(default_factory=dict)
    secret_versions: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class RuntimeValidationContext:
    """External state required before enable or a concrete run.

    Parsing deliberately does not consult these dependencies. Callers construct
    this context from their storage/transport snapshot and pass it to
    ``validate_runtime_dependencies`` or ``validate_run_mode``.
    """

    config: dict[str, Any]
    published_resumes: Mapping[str, Sequence[Mapping[str, Any]]]
    usable_application_transports: Mapping[str, Any] = field(default_factory=dict)
    dictionary_values: Mapping[str, Collection[str]] = field(default_factory=dict)


def default_autopilot_config() -> dict[str, Any]:
    return {
        "timezone": "Europe/Moscow",
        "accounts": [
            {
                "profile_id": "default",
                "candidate_profile_id": "default",
                "enabled": False,
                "paused": False,
                "authorization_generation": None,
                "resume_queries": [
                    {"resume_id": "published:*", "preset_names": []}
                ],
            }
        ],
        "schedule": {
            "days": [1, 2, 3, 4, 5, 6, 7],
            "start": "08:00",
            "end": "21:00",
            "interval_minutes": 60,
        },
        "search": {
            "include_recommendations": True,
            "per_page": 100,
            "max_pages": 20,
            "max_results_per_run": 2000,
        },
        "filters": {
            "excluded_keywords": [],
            "required_keywords": [],
            "allowed_role_families": [],
            "areas": [],
            "remote": "any",
            "schedules": [],
            "employment_types": [],
            "experience_levels": [],
            "languages": [],
            "citizenships": [],
            "required_application_capabilities": [],
            "minimum_salary": 0,
            "salary_currency": "RUR",
            "unknown_salary": "allow",
            "use_employer_blacklist": True,
        },
        "limits": {
            "daily_success": 50,
            "per_run_success": 10,
            "administrative_max_daily_success": 200,
            "send_delay_min_seconds": 45,
            "send_delay_max_seconds": 120,
        },
        "ranking": {
            "minimum_score": 60,
            "ai_mode": "borderline",
            "borderline_low": 50,
            "borderline_high": 70,
            "minimum_ai_confidence": 0.7,
            "ai_detail": "light",
            "ai_failure_policy": "retry",
            "weights": {
                "role": 0.30,
                "skills": 0.30,
                "experience": 0.15,
                "salary": 0.10,
                "work_format": 0.10,
                "area": 0.05,
                "industry": 0.0,
            },
        },
        "retry": {
            "max_attempts": 4,
            "base_delay_seconds": 60,
            "max_delay_seconds": 3600,
            "jitter_ratio": 0.25,
            "auth_recovery_attempts": 1,
            "reconciliation_delay_seconds": 30,
            "reconciliation_checks": 3,
        },
        "lease": {
            "ttl_seconds": 120,
            "request_timeout_seconds": 30,
            "renewal_margin_seconds": 45,
        },
        "application": {
            "resume_policy": "best_resume_only",
            "cover_letter_mode": "template",
            "screening_mode": "profile_grounded",
            "form_mode": "profile_grounded",
            "captcha_mode": "manual_handoff",
            "challenge_expiry_hours": 24,
        },
        "browser": {"headless": True, "navigation_timeout_seconds": 30},
        "notifications": {"challenge": True, "run_failure": True},
        "retention": {"challenge_artifact_days": 7, "event_days": 180},
    }


_TOP_LEVEL_KEYS = frozenset(default_autopilot_config())
_ACCOUNT_KEYS = frozenset(
    {
        "profile_id",
        "candidate_profile_id",
        "enabled",
        "paused",
        "authorization_generation",
        "resume_queries",
    }
)
_RESUME_QUERY_KEYS = frozenset({"resume_id", "preset_names"})
_SCHEDULE_KEYS = frozenset({"days", "start", "end", "interval_minutes"})
_SEARCH_KEYS = frozenset(
    {"include_recommendations", "per_page", "max_pages", "max_results_per_run"}
)
_FILTER_KEYS = frozenset(default_autopilot_config()["filters"])
_LIMIT_KEYS = frozenset(default_autopilot_config()["limits"])
_RANKING_KEYS = frozenset(default_autopilot_config()["ranking"])
_WEIGHT_KEYS = frozenset(default_autopilot_config()["ranking"]["weights"])
_RETRY_KEYS = frozenset(default_autopilot_config()["retry"])
_LEASE_KEYS = frozenset(default_autopilot_config()["lease"])
_APPLICATION_KEYS = frozenset(default_autopilot_config()["application"])
_BROWSER_KEYS = frozenset(default_autopilot_config()["browser"])
_NOTIFICATION_KEYS = frozenset(default_autopilot_config()["notifications"])
_RETENTION_KEYS = frozenset(default_autopilot_config()["retention"])

_REMOTE_MODES = frozenset({"any", "only", "exclude"})
_UNKNOWN_SALARY_MODES = frozenset({"allow", "reject"})
_AI_MODES = frozenset({"off", "borderline", "all"})
_AI_DETAILS = frozenset({"light", "heavy"})
_AI_FAILURE_POLICIES = frozenset({"retry", "deterministic", "skip"})
_RESUME_POLICIES = frozenset({"best_resume_only", "per_resume"})
_COVER_LETTER_MODES = frozenset({"none", "template", "ai"})
_GROUNDED_MODES = frozenset({"off", "profile_grounded"})
_CAPTCHA_MODES = frozenset({"manual_handoff"})
_APPLICATION_CAPABILITIES = frozenset({"direct", "screening", "form"})

_HH_SEARCH_PRESET_KEYS = frozenset(
    {
        "text",
        "area",
        "professional_role",
        "industry",
        "salary",
        "schedule",
        "experience",
        "employment",
        "date_from",
        "date_to",
        "search_field",
        "employer_id",
        "excluded_employer_id",
        "only_with_salary",
        "order_by",
        "period",
        "currency",
        "no_magic",
        "premium",
    }
)
_HH_SEARCH_FIELDS = frozenset({"name", "company_name", "description"})
_HH_SEARCH_ORDER = frozenset(
    {"publication_time", "salary_desc", "salary_asc", "relevance", "distance"}
)

_TIME_RE = re.compile(r"^(?P<hour>\d{2}):(?P<minute>\d{2})$")
_HH_ID_RE = re.compile(r"^\d+$")
_HH_COMPOSITE_ID_RE = re.compile(r"^\d+(?:\.\d+)*$")
_GRANDFATHERED_LANGUAGE_TAGS = frozenset(
    {
        "art-lojban",
        "cel-gaulish",
        "en-gb-oed",
        "i-ami",
        "i-bnn",
        "i-default",
        "i-enochian",
        "i-hak",
        "i-klingon",
        "i-lux",
        "i-mingo",
        "i-navajo",
        "i-pwn",
        "i-tao",
        "i-tay",
        "i-tsu",
        "no-bok",
        "no-nyn",
        "sgn-be-fr",
        "sgn-be-nl",
        "sgn-ch-de",
        "zh-guoyu",
        "zh-hakka",
        "zh-min",
        "zh-min-nan",
        "zh-xiang",
    }
)


def _mapping(name: str, value: Any, allowed: Collection[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AutopilotConfigError(f"{name} must be an object")
    unknown = sorted(str(key) for key in value if key not in allowed)
    if unknown:
        raise AutopilotConfigError(
            f"{name} contains unknown key(s): {', '.join(unknown)}"
        )
    return value


def _array(name: str, value: Any, low: int, high: int) -> list[Any]:
    if not isinstance(value, list):
        raise AutopilotConfigError(f"{name} must be an array")
    if not low <= len(value) <= high:
        raise AutopilotConfigError(f"{name} must contain {low}..{high} items")
    return value


def _boolean(name: str, value: Any) -> bool:
    if type(value) is not bool:
        raise AutopilotConfigError(f"{name} must be a JSON boolean")
    return value


def _integer(name: str, value: Any) -> int:
    if type(value) is not int:
        raise AutopilotConfigError(f"{name} must be an integer")
    return value


def _bounded(name: str, value: Any, low: int, high: int) -> int:
    parsed = _integer(name, value)
    if not low <= parsed <= high:
        raise AutopilotConfigError(f"{name} must be in {low}..{high}")
    return parsed


def _number(name: str, value: Any, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AutopilotConfigError(f"{name} must be a finite number")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise AutopilotConfigError(f"{name} must be a finite number")
    if not low <= parsed <= high:
        raise AutopilotConfigError(f"{name} must be in {low}..{high}")
    return parsed


def _text(
    name: str,
    value: Any,
    *,
    low: int = 1,
    high: int = 200,
    casefold: bool = False,
) -> str:
    if not isinstance(value, str):
        raise AutopilotConfigError(f"{name} must be a string")
    parsed = value.strip()
    if not low <= len(parsed) <= high:
        raise AutopilotConfigError(f"{name} length must be in {low}..{high}")
    return parsed.casefold() if casefold else parsed


def _enum(name: str, value: Any, allowed: Collection[str]) -> str:
    parsed = _text(name, value, casefold=True)
    if parsed not in allowed:
        choices = "|".join(sorted(allowed))
        raise AutopilotConfigError(f"{name} must be one of {choices}")
    return parsed


def _identifier(name: str, value: Any) -> str:
    return _text(name, value, casefold=True)


def _identifier_key(value: str) -> str:
    return value.strip().casefold()


def _hh_id(name: str, value: Any) -> str:
    parsed = _text(name, value)
    if not _HH_ID_RE.fullmatch(parsed):
        raise AutopilotConfigError(f"{name} must be an HH ID string")
    return str(int(parsed))


def _composite_hh_id(name: str, value: Any) -> str:
    parsed = _text(name, value)
    if not _HH_COMPOSITE_ID_RE.fullmatch(parsed):
        raise AutopilotConfigError(f"{name} must be an HH dictionary ID string")
    return ".".join(str(int(part)) for part in parsed.split("."))


def _language(name: str, value: Any) -> str:
    parsed = _text(name, value, casefold=True)
    if parsed in _GRANDFATHERED_LANGUAGE_TAGS:
        return parsed
    subtags = parsed.split("-")

    def alpha(item: str) -> bool:
        return item.isascii() and item.isalpha()

    def alphanumeric(item: str) -> bool:
        return item.isascii() and item.isalnum()

    def invalid() -> None:
        raise AutopilotConfigError(f"{name} must be a BCP-47 language tag")

    if subtags[0] == "x":
        if len(subtags) < 2 or any(
            not 1 <= len(item) <= 8 or not alphanumeric(item)
            for item in subtags[1:]
        ):
            invalid()
        return parsed

    primary = subtags[0]
    if not alpha(primary) or not 2 <= len(primary) <= 8:
        invalid()
    index = 1
    if len(primary) <= 3:
        extlang_count = 0
        while (
            index < len(subtags)
            and len(subtags[index]) == 3
            and alpha(subtags[index])
            and extlang_count < 3
        ):
            index += 1
            extlang_count += 1
    if index < len(subtags) and len(subtags[index]) == 4 and alpha(subtags[index]):
        index += 1
    if index < len(subtags) and (
        (len(subtags[index]) == 2 and alpha(subtags[index]))
        or (len(subtags[index]) == 3 and subtags[index].isascii() and subtags[index].isdigit())
    ):
        index += 1

    variants: set[str] = set()
    while index < len(subtags):
        item = subtags[index]
        is_variant = alphanumeric(item) and (
            5 <= len(item) <= 8
            or (len(item) == 4 and item[0].isdigit())
        )
        if not is_variant:
            break
        if item in variants:
            invalid()
        variants.add(item)
        index += 1

    extensions: set[str] = set()
    while index < len(subtags) and (
        len(subtags[index]) == 1
        and alphanumeric(subtags[index])
        and subtags[index] != "x"
    ):
        singleton = subtags[index]
        if singleton in extensions:
            invalid()
        extensions.add(singleton)
        index += 1
        start = index
        while (
            index < len(subtags)
            and 2 <= len(subtags[index]) <= 8
            and alphanumeric(subtags[index])
        ):
            index += 1
        if index == start:
            invalid()

    if index < len(subtags) and subtags[index] == "x":
        index += 1
        start = index
        while (
            index < len(subtags)
            and 1 <= len(subtags[index]) <= 8
            and alphanumeric(subtags[index])
        ):
            index += 1
        if index == start:
            invalid()
    if index != len(subtags):
        invalid()
    return parsed


def _normalized_array(
    name: str,
    value: Any,
    *,
    low: int,
    high: int,
    normalize: Any,
    duplicate_key: Any | None = None,
) -> list[Any]:
    items = _array(name, value, low, high)
    normalized: list[Any] = []
    seen: set[Any] = set()
    for index, item in enumerate(items):
        parsed = normalize(f"{name}[{index}]", item)
        key = duplicate_key(parsed) if duplicate_key is not None else parsed
        try:
            duplicate = key in seen
        except TypeError as exc:
            raise AutopilotConfigError(f"{name} contains an invalid item") from exc
        if duplicate:
            raise AutopilotConfigError(f"{name} contains a duplicate after normalization")
        seen.add(key)
        normalized.append(parsed)
    return normalized


def _deep_defaults(defaults: Any, override: Any) -> Any:
    if isinstance(defaults, dict) and isinstance(override, dict):
        merged = copy.deepcopy(defaults)
        for key, value in override.items():
            if key in merged:
                merged[key] = _deep_defaults(merged[key], value)
            else:
                merged[key] = copy.deepcopy(value)
        return merged
    return copy.deepcopy(override)


def _raw_autopilot_config(config: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(config, dict):
        raise AutopilotConfigError("configuration root must be an object")
    sources = config.get("sources")
    if sources is None:
        return default_autopilot_config()
    if not isinstance(sources, dict):
        raise AutopilotConfigError("sources must be an object")
    hh = sources.get("hh")
    if hh is None:
        return default_autopilot_config()
    if not isinstance(hh, dict):
        raise AutopilotConfigError("sources.hh must be an object")
    if "autopilot" not in hh:
        return default_autopilot_config()
    supplied = hh["autopilot"]
    if supplied == {}:
        return default_autopilot_config()
    if not isinstance(supplied, dict):
        raise AutopilotConfigError("sources.hh.autopilot must be an object")
    return _deep_defaults(default_autopilot_config(), supplied)


def _parse_accounts(raw: Any, include_recommendations: bool) -> tuple[AccountSettings, ...]:
    items = _array("accounts", raw, 1, 100)
    accounts: list[AccountSettings] = []
    profile_ids: set[str] = set()
    for index, item in enumerate(items):
        name = f"accounts[{index}]"
        account = _mapping(name, item, _ACCOUNT_KEYS)
        profile_id = _identifier(f"{name}.profile_id", account.get("profile_id"))
        profile_key = _identifier_key(profile_id)
        if profile_key in profile_ids:
            raise AutopilotConfigError("account profile ids must be unique")
        profile_ids.add(profile_key)
        candidate_profile_id = _identifier(
            f"{name}.candidate_profile_id", account.get("candidate_profile_id")
        )
        enabled = _boolean(f"{name}.enabled", account.get("enabled", False))
        paused = _boolean(f"{name}.paused", account.get("paused", False))
        generation = account.get("authorization_generation")
        if generation is not None:
            generation = _integer(f"{name}.authorization_generation", generation)
            if generation < 1:
                raise AutopilotConfigError(
                    f"{name}.authorization_generation must be a positive integer or null"
                )

        raw_queries = _array(f"{name}.resume_queries", account.get("resume_queries"), 1, 500)
        queries: list[dict[str, Any]] = []
        query_keys: set[tuple[str, tuple[str, ...]]] = set()
        for query_index, raw_query in enumerate(raw_queries):
            query_name = f"{name}.resume_queries[{query_index}]"
            query = _mapping(query_name, raw_query, _RESUME_QUERY_KEYS)
            resume_id = _identifier(f"{query_name}.resume_id", query.get("resume_id"))
            if resume_id.casefold() == "published:*":
                resume_id = "published:*"
            preset_names = _normalized_array(
                f"{query_name}.preset_names",
                query.get("preset_names"),
                low=0,
                high=100,
                normalize=lambda item_name, value: _text(
                    item_name, value, casefold=True
                ),
            )
            if not preset_names and not include_recommendations:
                raise AutopilotConfigError(
                    f"{query_name}.preset_names may be empty only when recommendations are enabled"
                )
            query_key = (
                _identifier_key(resume_id),
                tuple(sorted(preset_names)),
            )
            if query_key in query_keys:
                raise AutopilotConfigError(
                    f"{name}.resume_queries contains a duplicate after normalization"
                )
            query_keys.add(query_key)
            queries.append({"resume_id": resume_id, "preset_names": preset_names})
        accounts.append(
            AccountSettings(
                profile_id=profile_id,
                candidate_profile_id=candidate_profile_id,
                enabled=enabled,
                paused=paused,
                authorization_generation=generation,
                resume_queries=tuple(queries),
            )
        )
    return tuple(accounts)


def _parse_schedule(raw: Any) -> dict[str, Any]:
    value = _mapping("schedule", raw, _SCHEDULE_KEYS)
    days = _normalized_array(
        "schedule.days",
        value["days"],
        low=1,
        high=7,
        normalize=lambda name, item: _bounded(name, item, 1, 7),
    )
    start = _clock_time("schedule.start", value["start"])
    end = _clock_time("schedule.end", value["end"])
    if start > end:
        raise AutopilotConfigError("schedule.start must not be after schedule.end")
    return {
        "days": days,
        "start": start,
        "end": end,
        "interval_minutes": _bounded(
            "schedule.interval_minutes", value["interval_minutes"], 1, 1440
        ),
    }


def _clock_time(name: str, value: Any) -> str:
    parsed = _text(name, value, low=5, high=5)
    match = _TIME_RE.fullmatch(parsed)
    if match is None:
        raise AutopilotConfigError(f"{name} must be HH:MM")
    hour = int(match.group("hour"))
    minute = int(match.group("minute"))
    if hour > 23 or minute > 59:
        raise AutopilotConfigError(f"{name} must be HH:MM")
    return parsed


def _parse_search(raw: Any) -> SearchSettings:
    value = _mapping("search", raw, _SEARCH_KEYS)
    return SearchSettings(
        include_recommendations=_boolean(
            "search.include_recommendations", value["include_recommendations"]
        ),
        per_page=_bounded("search.per_page", value["per_page"], 1, 100),
        max_pages=_bounded("search.max_pages", value["max_pages"], 1, 100),
        max_results_per_run=_bounded(
            "search.max_results_per_run", value["max_results_per_run"], 1, 10_000
        ),
    )


def _parse_filters(raw: Any) -> dict[str, Any]:
    value = _mapping("filters", raw, _FILTER_KEYS)

    def casefolded_text(name: str, item: Any) -> str:
        return _text(name, item, casefold=True)

    salary_currency = _text("filters.salary_currency", value["salary_currency"])
    if not re.fullmatch(r"[A-Za-z]{3}", salary_currency):
        raise AutopilotConfigError(
            "filters.salary_currency must be a three-letter currency code"
        )
    return {
        "excluded_keywords": _normalized_array(
            "filters.excluded_keywords",
            value["excluded_keywords"],
            low=0,
            high=1000,
            normalize=casefolded_text,
        ),
        "required_keywords": _normalized_array(
            "filters.required_keywords",
            value["required_keywords"],
            low=0,
            high=1000,
            normalize=casefolded_text,
        ),
        "allowed_role_families": _normalized_array(
            "filters.allowed_role_families",
            value["allowed_role_families"],
            low=0,
            high=1000,
            normalize=casefolded_text,
        ),
        "areas": _normalized_array(
            "filters.areas", value["areas"], low=0, high=500, normalize=_hh_id
        ),
        "remote": _enum("filters.remote", value["remote"], _REMOTE_MODES),
        "schedules": _normalized_array(
            "filters.schedules",
            value["schedules"],
            low=0,
            high=100,
            normalize=casefolded_text,
        ),
        "employment_types": _normalized_array(
            "filters.employment_types",
            value["employment_types"],
            low=0,
            high=100,
            normalize=casefolded_text,
        ),
        "experience_levels": _normalized_array(
            "filters.experience_levels",
            value["experience_levels"],
            low=0,
            high=100,
            normalize=casefolded_text,
        ),
        "languages": _normalized_array(
            "filters.languages",
            value["languages"],
            low=0,
            high=100,
            normalize=_language,
        ),
        "citizenships": _normalized_array(
            "filters.citizenships",
            value["citizenships"],
            low=0,
            high=500,
            normalize=_hh_id,
        ),
        "required_application_capabilities": _normalized_array(
            "filters.required_application_capabilities",
            value["required_application_capabilities"],
            low=0,
            high=len(_APPLICATION_CAPABILITIES),
            normalize=lambda name, item: _enum(
                name, item, _APPLICATION_CAPABILITIES
            ),
        ),
        "minimum_salary": _bounded(
            "filters.minimum_salary", value["minimum_salary"], 0, 1_000_000_000
        ),
        "salary_currency": salary_currency.upper(),
        "unknown_salary": _enum(
            "filters.unknown_salary", value["unknown_salary"], _UNKNOWN_SALARY_MODES
        ),
        "use_employer_blacklist": _boolean(
            "filters.use_employer_blacklist", value["use_employer_blacklist"]
        ),
    }


def _parse_limits(raw: Any) -> LimitSettings:
    value = _mapping("limits", raw, _LIMIT_KEYS)
    administrative = _bounded(
        "limits.administrative_max_daily_success",
        value["administrative_max_daily_success"],
        1,
        200,
    )
    daily = _bounded("limits.daily_success", value["daily_success"], 1, administrative)
    per_run = _bounded(
        "limits.per_run_success", value["per_run_success"], 1, administrative
    )
    if per_run > daily:
        raise AutopilotConfigError(
            "limits.per_run_success must not exceed limits.daily_success"
        )
    minimum_delay = _bounded(
        "limits.send_delay_min_seconds", value["send_delay_min_seconds"], 0, 3600
    )
    maximum_delay = _bounded(
        "limits.send_delay_max_seconds", value["send_delay_max_seconds"], 0, 3600
    )
    if minimum_delay > maximum_delay:
        raise AutopilotConfigError(
            "limits.send_delay_min_seconds must not exceed limits.send_delay_max_seconds"
        )
    return LimitSettings(
        daily_success=daily,
        per_run_success=per_run,
        administrative_max_daily_success=administrative,
        send_delay_min_seconds=minimum_delay,
        send_delay_max_seconds=maximum_delay,
    )


def _parse_ranking(raw: Any) -> dict[str, Any]:
    value = _mapping("ranking", raw, _RANKING_KEYS)
    weights_raw = _mapping("ranking.weights", value["weights"], _WEIGHT_KEYS)
    weights = {
        key: _number(f"ranking.weights.{key}", weights_raw[key], 0.0, float("inf"))
        for key in sorted(_WEIGHT_KEYS)
    }
    weight_sum = sum(weights.values())
    if not math.isfinite(weight_sum) or weight_sum <= 0:
        raise AutopilotConfigError("ranking.weights must have a positive finite sum")
    weights = {key: number / weight_sum for key, number in weights.items()}
    minimum_score = _bounded("ranking.minimum_score", value["minimum_score"], 0, 100)
    borderline_low = _bounded(
        "ranking.borderline_low", value["borderline_low"], 0, 100
    )
    borderline_high = _bounded(
        "ranking.borderline_high", value["borderline_high"], 0, 100
    )
    if not borderline_low <= minimum_score <= borderline_high:
        raise AutopilotConfigError(
            "ranking requires borderline_low <= minimum_score <= borderline_high"
        )
    return {
        "minimum_score": minimum_score,
        "ai_mode": _enum("ranking.ai_mode", value["ai_mode"], _AI_MODES),
        "borderline_low": borderline_low,
        "borderline_high": borderline_high,
        "minimum_ai_confidence": _number(
            "ranking.minimum_ai_confidence", value["minimum_ai_confidence"], 0.0, 1.0
        ),
        "ai_detail": _enum("ranking.ai_detail", value["ai_detail"], _AI_DETAILS),
        "ai_failure_policy": _enum(
            "ranking.ai_failure_policy", value["ai_failure_policy"], _AI_FAILURE_POLICIES
        ),
        "weights": weights,
    }


def _parse_retry(raw: Any) -> dict[str, Any]:
    value = _mapping("retry", raw, _RETRY_KEYS)
    base_delay = _bounded(
        "retry.base_delay_seconds", value["base_delay_seconds"], 1, 86_400
    )
    max_delay = _bounded(
        "retry.max_delay_seconds", value["max_delay_seconds"], 1, 86_400
    )
    if base_delay > max_delay:
        raise AutopilotConfigError(
            "retry.base_delay_seconds must not exceed retry.max_delay_seconds"
        )
    return {
        "max_attempts": _bounded("retry.max_attempts", value["max_attempts"], 1, 20),
        "base_delay_seconds": base_delay,
        "max_delay_seconds": max_delay,
        "jitter_ratio": _number("retry.jitter_ratio", value["jitter_ratio"], 0.0, 1.0),
        "auth_recovery_attempts": _bounded(
            "retry.auth_recovery_attempts", value["auth_recovery_attempts"], 1, 20
        ),
        "reconciliation_delay_seconds": _bounded(
            "retry.reconciliation_delay_seconds",
            value["reconciliation_delay_seconds"],
            1,
            86_400,
        ),
        "reconciliation_checks": _bounded(
            "retry.reconciliation_checks", value["reconciliation_checks"], 1, 20
        ),
    }


def _parse_lease(raw: Any) -> LeaseSettings:
    value = _mapping("lease", raw, _LEASE_KEYS)
    lease = LeaseSettings(
        ttl_seconds=_bounded("lease.ttl_seconds", value["ttl_seconds"], 1, 600),
        request_timeout_seconds=_bounded(
            "lease.request_timeout_seconds", value["request_timeout_seconds"], 1, 600
        ),
        renewal_margin_seconds=_bounded(
            "lease.renewal_margin_seconds", value["renewal_margin_seconds"], 1, 600
        ),
    )
    if lease.ttl_seconds < lease.request_timeout_seconds + lease.renewal_margin_seconds:
        raise AutopilotConfigError(
            "lease ttl must cover request timeout plus renewal margin"
        )
    return lease


def _parse_application(raw: Any) -> dict[str, Any]:
    value = _mapping("application", raw, _APPLICATION_KEYS)
    return {
        "resume_policy": _enum(
            "application.resume_policy", value["resume_policy"], _RESUME_POLICIES
        ),
        "cover_letter_mode": _enum(
            "application.cover_letter_mode",
            value["cover_letter_mode"],
            _COVER_LETTER_MODES,
        ),
        "screening_mode": _enum(
            "application.screening_mode", value["screening_mode"], _GROUNDED_MODES
        ),
        "form_mode": _enum(
            "application.form_mode", value["form_mode"], _GROUNDED_MODES
        ),
        "captcha_mode": _enum(
            "application.captcha_mode", value["captcha_mode"], _CAPTCHA_MODES
        ),
        "challenge_expiry_hours": _bounded(
            "application.challenge_expiry_hours",
            value["challenge_expiry_hours"],
            1,
            720,
        ),
    }


def _parse_browser(raw: Any) -> dict[str, Any]:
    value = _mapping("browser", raw, _BROWSER_KEYS)
    return {
        "headless": _boolean("browser.headless", value["headless"]),
        "navigation_timeout_seconds": _bounded(
            "browser.navigation_timeout_seconds",
            value["navigation_timeout_seconds"],
            1,
            600,
        ),
    }


def _parse_notifications(raw: Any) -> dict[str, Any]:
    value = _mapping("notifications", raw, _NOTIFICATION_KEYS)
    return {
        "challenge": _boolean("notifications.challenge", value["challenge"]),
        "run_failure": _boolean("notifications.run_failure", value["run_failure"]),
    }


def _parse_retention(raw: Any) -> dict[str, Any]:
    value = _mapping("retention", raw, _RETENTION_KEYS)
    return {
        "challenge_artifact_days": _bounded(
            "retention.challenge_artifact_days", value["challenge_artifact_days"], 1, 3650
        ),
        "event_days": _bounded("retention.event_days", value["event_days"], 1, 3650),
    }


def _timezone(name: str, value: Any) -> str:
    parsed = _text(name, value)
    try:
        ZoneInfo(parsed)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise AutopilotConfigError(f"{name} must be a valid IANA timezone") from exc
    return parsed


def parse_autopilot_settings(config: dict[str, Any]) -> AutopilotSettings:
    raw = _mapping("sources.hh.autopilot", _raw_autopilot_config(config), _TOP_LEVEL_KEYS)
    search = _parse_search(raw["search"])
    return AutopilotSettings(
        timezone=_timezone("timezone", raw["timezone"]),
        accounts=_parse_accounts(raw["accounts"], search.include_recommendations),
        schedule=_parse_schedule(raw["schedule"]),
        search=search,
        filters=_parse_filters(raw["filters"]),
        limits=_parse_limits(raw["limits"]),
        ranking=_parse_ranking(raw["ranking"]),
        retry=_parse_retry(raw["retry"]),
        lease=_parse_lease(raw["lease"]),
        application=_parse_application(raw["application"]),
        browser=_parse_browser(raw["browser"]),
        notifications=_parse_notifications(raw["notifications"]),
        retention=_parse_retention(raw["retention"]),
    )


def _casefold_index(name: str, values: Any) -> dict[str, tuple[str, Any]]:
    if not isinstance(values, Mapping):
        raise AutopilotConfigError(f"{name} must be an object")
    indexed: dict[str, tuple[str, Any]] = {}
    for key, value in values.items():
        normalized = _identifier_key(_text(f"{name} key", key))
        if normalized in indexed:
            raise AutopilotConfigError(
                f"{name} contains duplicate identifiers after normalization"
            )
        indexed[normalized] = (str(key).strip(), value)
    return indexed


def _selected_accounts(
    settings: AutopilotSettings, account_id: str | None
) -> tuple[AccountSettings, ...]:
    if account_id is None:
        return settings.accounts
    wanted = _identifier_key(_identifier("account_id", account_id))
    selected = tuple(
        account
        for account in settings.accounts
        if _identifier_key(account.profile_id) == wanted
    )
    if not selected:
        raise AutopilotConfigError(f"Unknown HH autopilot account: {account_id}")
    return selected


def _published_status(resume: Mapping[str, Any]) -> bool:
    if "published" in resume:
        return resume.get("published") is True
    if "status" not in resume:
        return True
    status = resume.get("status")
    if isinstance(status, Mapping):
        status = status.get("id") or status.get("value") or status.get("name")
    return isinstance(status, str) and status.strip().casefold() == "published"


def _published_resume_index(
    account: AccountSettings,
    context: RuntimeValidationContext,
) -> dict[str, tuple[str, dict[str, Any]]]:
    resume_sets = _casefold_index("published_resumes", context.published_resumes)
    entry = resume_sets.get(_identifier_key(account.profile_id))
    if entry is None:
        raise AutopilotConfigError(
            f"account {account.profile_id} has no resolvable published resume"
        )
    raw_resumes = entry[1]
    if not isinstance(raw_resumes, Sequence) or isinstance(raw_resumes, (str, bytes)):
        raise AutopilotConfigError(
            f"published_resumes.{account.profile_id} must be an array"
        )
    resumes: dict[str, tuple[str, dict[str, Any]]] = {}
    for index, raw_resume in enumerate(raw_resumes):
        if not isinstance(raw_resume, Mapping):
            raise AutopilotConfigError(
                f"published_resumes.{account.profile_id}[{index}] must be an object"
            )
        if not _published_status(raw_resume):
            continue
        resume_id = _identifier(
            f"published_resumes.{account.profile_id}[{index}].id",
            raw_resume.get("id") or raw_resume.get("resume_id"),
        )
        key = _identifier_key(resume_id)
        if key in resumes:
            raise AutopilotConfigError(
                f"published_resumes.{account.profile_id} contains duplicate resume IDs"
            )
        resumes[key] = (resume_id, dict(raw_resume))
    if not resumes:
        raise AutopilotConfigError(
            f"account {account.profile_id} has no resolvable published resume"
        )
    return resumes


def _dictionary_values(
    context: RuntimeValidationContext,
    name: str,
    *aliases: str,
) -> frozenset[str] | None:
    values: Any = None
    for key in (name, *aliases):
        if key in context.dictionary_values:
            values = context.dictionary_values[key]
            break
    if values is None:
        return None
    if isinstance(values, (str, bytes)) or not isinstance(values, Collection):
        raise AutopilotConfigError(f"dictionary_values.{name} must be a collection")
    normalized: set[str] = set()
    for value in values:
        normalized.add(_text(f"dictionary_values.{name}", value, casefold=True))
    return frozenset(normalized)


def _validate_dictionary_members(
    field_name: str,
    values: Sequence[str],
    allowed: frozenset[str] | None,
) -> None:
    if not values:
        return
    if allowed is None:
        raise AutopilotConfigError(
            f"{field_name} cannot be validated because the current HH dictionary is unavailable"
        )
    unsupported = sorted(value for value in values if value.casefold() not in allowed)
    if unsupported:
        raise AutopilotConfigError(
            f"{field_name} contains unsupported HH dictionary value(s): {', '.join(unsupported)}"
        )


def _validate_preset_date(name: str, value: Any) -> str:
    parsed = _text(name, value, high=10)
    try:
        date.fromisoformat(parsed)
    except ValueError as exc:
        raise AutopilotConfigError(f"{name} must be an ISO date") from exc
    return parsed


def _validate_hh_search_preset(
    name: str,
    raw: Any,
    context: RuntimeValidationContext,
) -> dict[str, Any]:
    path = f"hh_campaign_presets.{name}"
    value = _mapping(path, raw, _HH_SEARCH_PRESET_KEYS)
    if not value:
        raise AutopilotConfigError(f"{path} must contain HH search parameters")
    normalized: dict[str, Any] = {}
    if "text" in value:
        normalized["text"] = _text(f"{path}.text", value["text"])
    id_arrays = {
        "area": 500,
        "professional_role": 500,
        "employer_id": 500,
        "excluded_employer_id": 500,
    }
    for field_name, maximum in id_arrays.items():
        if field_name in value:
            normalized[field_name] = _normalized_array(
                f"{path}.{field_name}",
                value[field_name],
                low=1,
                high=maximum,
                normalize=_hh_id,
            )
    if "industry" in value:
        normalized["industry"] = _normalized_array(
            f"{path}.industry",
            value["industry"],
            low=1,
            high=500,
            normalize=lambda item_name, item: _composite_hh_id(item_name, item),
        )
    schedules = _dictionary_values(context, "schedules", "schedule")
    employment = _dictionary_values(
        context, "employment_types", "employment", "employments"
    )
    experience = _dictionary_values(
        context, "experience_levels", "experience", "experiences"
    )
    if "schedule" in value:
        normalized["schedule"] = _text(
            f"{path}.schedule", value["schedule"], casefold=True
        )
        _validate_dictionary_members(path + ".schedule", [normalized["schedule"]], schedules)
    if "employment" in value:
        normalized["employment"] = _normalized_array(
            f"{path}.employment",
            value["employment"],
            low=1,
            high=100,
            normalize=lambda item_name, item: _text(item_name, item, casefold=True),
        )
        _validate_dictionary_members(path + ".employment", normalized["employment"], employment)
    if "experience" in value:
        normalized["experience"] = _text(
            f"{path}.experience", value["experience"], casefold=True
        )
        _validate_dictionary_members(
            path + ".experience", [normalized["experience"]], experience
        )
    if "salary" in value:
        normalized["salary"] = _bounded(
            f"{path}.salary", value["salary"], 0, 1_000_000_000
        )
    for field_name in ("only_with_salary", "no_magic", "premium"):
        if field_name in value:
            normalized[field_name] = _boolean(
                f"{path}.{field_name}", value[field_name]
            )
    for field_name in ("date_from", "date_to"):
        if field_name in value:
            normalized[field_name] = _validate_preset_date(
                f"{path}.{field_name}", value[field_name]
            )
    if "date_from" in normalized and "date_to" in normalized:
        if normalized["date_from"] > normalized["date_to"]:
            raise AutopilotConfigError(f"{path}.date_from must not be after date_to")
    if "search_field" in value:
        normalized["search_field"] = _normalized_array(
            f"{path}.search_field",
            value["search_field"],
            low=1,
            high=len(_HH_SEARCH_FIELDS),
            normalize=lambda item_name, item: _enum(
                item_name, item, _HH_SEARCH_FIELDS
            ),
        )
    if "order_by" in value:
        normalized["order_by"] = _enum(
            f"{path}.order_by", value["order_by"], _HH_SEARCH_ORDER
        )
    if "period" in value:
        normalized["period"] = _bounded(f"{path}.period", value["period"], 1, 30)
    if "currency" in value:
        currency = _text(f"{path}.currency", value["currency"])
        if not re.fullmatch(r"[A-Za-z]{3}", currency):
            raise AutopilotConfigError(f"{path}.currency must be a three-letter code")
        normalized["currency"] = currency.upper()
    return normalized


def _transport_entry_usable(value: Any) -> bool:
    accepted = {"api", "browser", "authenticated_browser", "web_cookie"}
    if type(value) is bool:
        return value
    if isinstance(value, str):
        return value.strip().casefold() in accepted
    if isinstance(value, Mapping):
        return any(
            str(key).strip().casefold() in accepted and item is True
            for key, item in value.items()
        )
    if isinstance(value, Collection) and not isinstance(value, (str, bytes)):
        return any(_transport_entry_usable(item) for item in value)
    return False


def _configured_transport_usable(
    account: AccountSettings,
    context: RuntimeValidationContext,
) -> bool:
    explicit = _casefold_index(
        "usable_application_transports", context.usable_application_transports
    ).get(_identifier_key(account.profile_id))
    if explicit is not None:
        return _transport_entry_usable(explicit[1])

    hh = ((context.config.get("sources") or {}).get("hh") or {})
    if not isinstance(hh, dict):
        return False
    effective = dict(hh)
    account_profiles = context.config.get("hh_account_profiles") or {}
    if isinstance(account_profiles, Mapping):
        profile = _casefold_index("hh_account_profiles", account_profiles).get(
            _identifier_key(account.profile_id)
        )
        if profile is not None and isinstance(profile[1], Mapping):
            effective.update(profile[1])
    if str(effective.get("access_token") or "").strip() or str(
        effective.get("refresh_token") or ""
    ).strip():
        return True
    cookie_path = str(effective.get("hh_cookie_file") or "").strip()
    if not cookie_path:
        return False
    path = Path(cookie_path)
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def validate_runtime_dependencies(
    settings: AutopilotSettings,
    context: RuntimeValidationContext,
    *,
    account_id: str | None = None,
) -> dict[str, tuple[dict[str, Any], ...]]:
    if not isinstance(context, RuntimeValidationContext):
        raise AutopilotConfigError("runtime validation context is required")
    auth_profiles = _casefold_index(
        "hh_account_profiles", context.config.get("hh_account_profiles") or {}
    )
    candidate_profiles = _casefold_index(
        "profiles", context.config.get("profiles") or {}
    )
    presets = _casefold_index(
        "hh_campaign_presets", context.config.get("hh_campaign_presets") or {}
    )
    schedules = _dictionary_values(context, "schedules", "schedule")
    employment = _dictionary_values(
        context, "employment_types", "employment", "employments"
    )
    experience = _dictionary_values(
        context, "experience_levels", "experience", "experiences"
    )
    _validate_dictionary_members("filters.schedules", settings.filters["schedules"], schedules)
    _validate_dictionary_members(
        "filters.employment_types", settings.filters["employment_types"], employment
    )
    _validate_dictionary_members(
        "filters.experience_levels", settings.filters["experience_levels"], experience
    )

    expanded_by_account: dict[str, tuple[dict[str, Any], ...]] = {}
    for account in _selected_accounts(settings, account_id):
        account_key = _identifier_key(account.profile_id)
        if account_key not in auth_profiles:
            raise AutopilotConfigError(
                f"HH authentication profile does not exist: {account.profile_id}"
            )
        if _identifier_key(account.candidate_profile_id) not in candidate_profiles:
            raise AutopilotConfigError(
                f"candidate profile does not exist: {account.candidate_profile_id}"
            )
        resumes = _published_resume_index(account, context)
        expanded: list[dict[str, Any]] = []
        expanded_keys: set[tuple[str, str]] = set()
        for mapping in account.resume_queries:
            wanted_resume = _identifier_key(str(mapping["resume_id"]))
            if wanted_resume == "published:*":
                selected_resumes = list(resumes.values())
            else:
                selected = resumes.get(wanted_resume)
                if selected is None:
                    raise AutopilotConfigError(
                        f"account {account.profile_id} cannot resolve published resume: "
                        f"{mapping['resume_id']}"
                    )
                selected_resumes = [selected]
            preset_names = list(mapping["preset_names"])
            if not preset_names:
                preset_names = [""]
            for preset_name in preset_names:
                preset_definition: dict[str, Any] | None = None
                canonical_preset = "__recommendations__"
                if preset_name:
                    preset = presets.get(_identifier_key(preset_name))
                    if preset is None:
                        raise AutopilotConfigError(
                            f"HH campaign preset does not exist: {preset_name}"
                        )
                    canonical_preset = _identifier_key(preset[0])
                    preset_definition = _validate_hh_search_preset(
                        preset[0], preset[1], context
                    )
                for resume_id, resume in selected_resumes:
                    pair = (_identifier_key(resume_id), canonical_preset)
                    if pair in expanded_keys:
                        raise AutopilotConfigError(
                            "duplicate expanded resume/preset mapping for "
                            f"{resume_id}/{preset_name or 'recommendations'}"
                        )
                    expanded_keys.add(pair)
                    expanded.append(
                        {
                            "resume_id": resume_id,
                            "resume": copy.deepcopy(resume),
                            "preset_name": preset_name or None,
                            "preset": copy.deepcopy(preset_definition),
                        }
                    )
        if not expanded:
            raise AutopilotConfigError(
                f"account {account.profile_id} has no resolvable published resume"
            )
        if not _configured_transport_usable(account, context):
            raise AutopilotConfigError(
                f"account {account.profile_id} requires a usable API or authenticated-browser "
                "application transport"
            )
        expanded_by_account[account.profile_id] = tuple(expanded)
    return expanded_by_account


def validate_run_mode(
    settings: AutopilotSettings,
    *,
    account_id: str,
    mode: ValidationMode,
    has_grant: bool,
    literal_confirmed: bool,
    runtime_context: RuntimeValidationContext | None = None,
    has_recovery_provenance: bool = False,
) -> AuthorizationKind | None:
    if isinstance(mode, str):
        try:
            mode = ValidationMode(mode.strip().casefold())
        except ValueError as exc:
            raise AutopilotConfigError(f"Unknown HH autopilot validation mode: {mode}") from exc
    if not isinstance(mode, ValidationMode):
        raise AutopilotConfigError("mode must be a ValidationMode")
    has_grant = _boolean("has_grant", has_grant)
    literal_confirmed = _boolean("literal_confirmed", literal_confirmed)
    has_recovery_provenance = _boolean(
        "has_recovery_provenance", has_recovery_provenance
    )
    account = _selected_accounts(settings, account_id)[0]
    if runtime_context is not None and mode is not ValidationMode.RECOVERY:
        validate_runtime_dependencies(settings, runtime_context, account_id=account.profile_id)
    if mode is ValidationMode.AUTONOMOUS:
        if not (account.enabled and not account.paused and has_grant):
            raise AutopilotConfigError(
                "autonomous run requires enabled, unpaused account and active grant"
            )
        return AuthorizationKind.AUTOPILOT
    if mode in {ValidationMode.MANUAL, ValidationMode.CANARY}:
        if not literal_confirmed:
            raise AutopilotConfigError(
                f"{mode.value} run requires literal confirmation"
            )
        return AuthorizationKind.LITERAL_CONFIRMATION
    if mode is ValidationMode.RECOVERY:
        if not has_recovery_provenance:
            raise AutopilotConfigError(
                "recovery requires immutable application attempt provenance"
            )
        return AuthorizationKind.RECOVERY
    return None


_UNORDERED_ARRAY_KEYS = frozenset(
    {
        "days",
        "resume_queries",
        "preset_names",
        "resumes",
        "excluded_keywords",
        "required_keywords",
        "allowed_role_families",
        "areas",
        "schedules",
        "employment_types",
        "experience_levels",
        "languages",
        "citizenships",
        "required_application_capabilities",
        "desired_roles",
        "must_have_skills",
        "nice_to_have_skills",
        "stop_words",
        "locations",
        "all_skills",
        "skills",
        "professional_roles",
        "area",
        "professional_role",
        "industry",
        "employment",
        "search_field",
        "employer_id",
        "excluded_employer_id",
        "no_proxy",
    }
)
_CASEFOLD_ARRAY_KEYS = frozenset(
    {
        "excluded_keywords",
        "required_keywords",
        "allowed_role_families",
        "schedules",
        "employment_types",
        "experience_levels",
        "languages",
        "required_application_capabilities",
        "desired_roles",
        "must_have_skills",
        "nice_to_have_skills",
        "stop_words",
        "locations",
        "all_skills",
        "skills",
        "professional_roles",
        "employment",
        "search_field",
    }
)
_CASEFOLD_VALUE_KEYS = frozenset(
    {
        "id",
        "profile_id",
        "candidate_profile_id",
        "resume_id",
        "preset_name",
        "effective_auth_profile_id",
    }
)
_HH_ID_ARRAY_KEYS = frozenset(
    {
        "areas",
        "citizenships",
        "area",
        "professional_role",
        "professional_roles",
        "employer_id",
        "excluded_employer_id",
    }
)
_COMPOSITE_HH_ID_ARRAY_KEYS = frozenset({"industry"})
_NORMALIZED_MAPPING_KEY_PARENTS = frozenset(
    {"presets", "prompt_versions", "secret_versions"}
)


def _credential_like_key(key: str) -> bool:
    separated = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key.strip())
    separated = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", "_", separated)
    lowered = separated.casefold()
    parts = tuple(part for part in re.split(r"[^a-z0-9]+", lowered) if part)
    compact = "".join(parts)
    if compact in {
        "api_key",
        "apikey",
        "authorization",
        "authorization_header",
        "authorizationheader",
        "key",
        "proxy_password",
        "proxypassword",
    }:
        return True
    if parts and parts[-1] == "key" and any(
        part in {"access", "api", "private"} for part in parts[:-1]
    ):
        return True
    if any(
        part
        in {
            "authorization",
            "cookie",
            "cookies",
            "password",
            "secret",
            "token",
        }
        for part in parts
    ):
        return True
    return compact.endswith(
        ("authorization", "authorizationheader", "cookie", "cookies", "password", "secret", "token")
    )


def _canonical_json_sort_key(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _canonicalize(value: Any, path: tuple[str, ...]) -> Any:
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        parent = path[-1] if path else ""
        for raw_key, item in value.items():
            if not isinstance(raw_key, str):
                raise AutopilotConfigError("policy object keys must be strings")
            key = raw_key.strip()
            if not key:
                raise AutopilotConfigError("policy object keys must not be blank")
            if parent in _NORMALIZED_MAPPING_KEY_PARENTS:
                key = key.casefold()
            if key in normalized:
                raise AutopilotConfigError(
                    f"policy object contains duplicate normalized key: {key}"
                )
            child_path = (*path, key)
            if key == "secret_versions":
                if path:
                    raise AutopilotConfigError(
                        "secret_versions is allowed only as the dedicated root policy object"
                    )
                if not isinstance(item, Mapping):
                    raise AutopilotConfigError("secret_versions must be an object")
                versions: dict[str, int] = {}
                for secret_name, version in item.items():
                    canonical_name = _text(
                        "secret_versions key", secret_name, casefold=True
                    )
                    if canonical_name in versions:
                        raise AutopilotConfigError(
                            "secret_versions contains duplicate normalized keys"
                        )
                    parsed_version = _integer(
                        f"secret_versions.{canonical_name}", version
                    )
                    if parsed_version < 1:
                        raise AutopilotConfigError(
                            f"secret_versions.{canonical_name} must be a positive integer"
                        )
                    versions[canonical_name] = parsed_version
                normalized[key] = versions
                continue
            if _credential_like_key(key):
                dotted = ".".join(child_path)
                raise AutopilotConfigError(
                    f"policy contains raw credential-like key outside secret_versions: {dotted}"
                )
            normalized[key] = _canonicalize(item, child_path)
        return normalized
    if isinstance(value, (list, tuple)):
        normalized_items = [_canonicalize(item, (*path, "[]")) for item in value]
        key = path[-1] if path else ""
        if key in _HH_ID_ARRAY_KEYS:
            normalized_items = [
                _hh_id(f"policy.{key}[{index}]", item)
                for index, item in enumerate(normalized_items)
            ]
        elif key in _COMPOSITE_HH_ID_ARRAY_KEYS:
            normalized_items = [
                _composite_hh_id(f"policy.{key}[{index}]", item)
                for index, item in enumerate(normalized_items)
            ]
        if key in _CASEFOLD_ARRAY_KEYS:
            normalized_items = [
                item.casefold() if isinstance(item, str) else item
                for item in normalized_items
            ]
        if key in _UNORDERED_ARRAY_KEYS:
            normalized_items.sort(key=_canonical_json_sort_key)
            normalized_keys = [
                _canonical_json_sort_key(item) for item in normalized_items
            ]
            if len(normalized_keys) != len(set(normalized_keys)):
                raise AutopilotConfigError(
                    f"policy array contains a duplicate after normalization: {key}"
                )
        return normalized_items
    if isinstance(value, str):
        normalized_text = value.strip()
        key = path[-1] if path else ""
        if key in _CASEFOLD_VALUE_KEYS:
            normalized_text = normalized_text.casefold()
        return normalized_text
    if value is None or type(value) is bool or type(value) is int:
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise AutopilotConfigError("policy numbers must be finite")
        return value
    raise AutopilotConfigError(
        f"policy contains unsupported value at {'.'.join(path) or '<root>'}"
    )


def canonicalize_policy_payload(payload: Any) -> Any:
    return _canonicalize(payload, ())


def _validate_policy_material(material: PolicyMaterial) -> None:
    if not isinstance(material, PolicyMaterial):
        raise AutopilotConfigError("policy material is required")
    _identifier("effective_auth_profile_id", material.effective_auth_profile_id)
    if not isinstance(material.candidate_profile, dict):
        raise AutopilotConfigError("candidate_profile must be an object")
    _text("candidate_profile_version", material.candidate_profile_version)
    if not isinstance(material.resumes, list) or not material.resumes:
        raise AutopilotConfigError("policy material requires at least one selected resume")
    seen_resumes: set[str] = set()
    for index, resume in enumerate(material.resumes):
        if not isinstance(resume, dict):
            raise AutopilotConfigError(f"resumes[{index}] must be an object")
        resume_id = _identifier(f"resumes[{index}].id", resume.get("id"))
        key = _identifier_key(resume_id)
        if key in seen_resumes:
            raise AutopilotConfigError("policy material contains duplicate resume IDs")
        seen_resumes.add(key)
        version = (
            resume.get("content_hash")
            or resume.get("version_hash")
            or resume.get("version")
        )
        _text(f"resumes[{index}].content_hash", version)
    if not isinstance(material.presets, dict):
        raise AutopilotConfigError("presets must be an object")
    if not isinstance(material.prompt_versions, dict):
        raise AutopilotConfigError("prompt_versions must be an object")
    if not isinstance(material.transport_identity, dict):
        raise AutopilotConfigError("transport_identity must be an object")
    if not isinstance(material.secret_versions, dict):
        raise AutopilotConfigError("secret_versions must be an object")


def policy_hash(
    settings: AutopilotSettings,
    account_id: str,
    material: PolicyMaterial,
) -> str:
    account = _selected_accounts(settings, account_id)[0]
    _validate_policy_material(material)
    account_policy = {
        key: value
        for key, value in asdict(account).items()
        if key not in {"enabled", "paused", "authorization_generation"}
    }
    payload = {
        "account": account_policy,
        "timezone": settings.timezone,
        "schedule": settings.schedule,
        "search": asdict(settings.search),
        "filters": settings.filters,
        "limits": asdict(settings.limits),
        "ranking": settings.ranking,
        "retry": settings.retry,
        "lease": asdict(settings.lease),
        "application": settings.application,
        "browser": settings.browser,
        "effective_auth_profile_id": material.effective_auth_profile_id,
        "candidate_profile": material.candidate_profile,
        "candidate_profile_version": material.candidate_profile_version,
        "resumes": material.resumes,
        "presets": material.presets,
        "model_id": material.model_id,
        "prompt_versions": material.prompt_versions,
        "cover_letter_template_version": material.cover_letter_template_version,
        "transport_identity": material.transport_identity,
        "secret_versions": material.secret_versions,
    }
    encoded = json.dumps(
        canonicalize_policy_payload(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "AccountSettings",
    "AutopilotConfigError",
    "AutopilotSettings",
    "LeaseSettings",
    "LimitSettings",
    "PolicyMaterial",
    "RuntimeValidationContext",
    "SearchSettings",
    "ValidationMode",
    "canonicalize_policy_payload",
    "default_autopilot_config",
    "parse_autopilot_settings",
    "policy_hash",
    "validate_run_mode",
    "validate_runtime_dependencies",
]
