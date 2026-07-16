from __future__ import annotations

import copy
import html
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Mapping, Sequence


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


_MARKUP = re.compile(r"<[^>]*>")
_SPACES = re.compile(r"\s+")


def _clean_text(value: Any, *, field_name: str, maximum: int = 8_000) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if "\0" in value:
        raise ValueError(f"{field_name} must not contain NUL")
    cleaned = _SPACES.sub(" ", _MARKUP.sub(" ", html.unescape(value))).strip()
    return cleaned[:maximum]


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
        object.__setattr__(self, "resume_id", _text(self.resume_id, field_name="resume_id"))
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
