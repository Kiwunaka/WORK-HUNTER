from __future__ import annotations

import html
import re
from copy import deepcopy
from typing import Any, Mapping, Protocol

from work_hunter.models import Job

from .repository import AutopilotRepository, LeaseKeeper
from .types import NormalizedVacancy, SearchPage, SearchRequest, SearchResult


_MARKUP = re.compile(r"<[^>]*>")
_WHITESPACE = re.compile(r"\s+")
_MAX_TEXT = 8_000
_MAX_COLLECTION = 100


class SearchTransport(Protocol):
    def search_vacancies_page(self, params: dict[str, Any]) -> SearchPage: ...

    def search_recommended_vacancies_page(
        self, resume_id: str, params: dict[str, Any]
    ) -> SearchPage: ...


def _clean(value: Any, *, limit: int = _MAX_TEXT) -> str:
    if value is None:
        return ""
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return ""
    text = html.unescape(str(value))
    text = _MARKUP.sub(" ", text)
    text = _WHITESPACE.sub(" ", text).strip()
    return text[:limit]


def _object(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _fact_object(source: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    if key not in source or source[key] is None:
        return {}
    value = source[key]
    if not isinstance(value, Mapping):
        raise TypeError(f"{key} must be a mapping or None")
    return value


def _optional_amount(source: Mapping[str, Any], key: str) -> int | None:
    if key not in source or source[key] is None:
        return None
    value = source[key]
    if type(value) is not int:
        raise TypeError(f"salary.{key} must be an integer or None")
    if value < 0:
        raise ValueError(f"salary.{key} must not be negative")
    return value


def _optional_bool_fact(source: Mapping[str, Any], key: str, *, prefix: str = "") -> bool | None:
    if key not in source or source[key] is None:
        return None
    value = source[key]
    if type(value) is not bool:
        name = f"{prefix}.{key}" if prefix else key
        raise TypeError(f"{name} must be a boolean or None")
    return value


def _optional_text_fact(
    source: Mapping[str, Any],
    key: str,
    *,
    limit: int,
) -> str:
    if key not in source or source[key] is None:
        return ""
    value = source[key]
    if type(value) is not str:
        raise TypeError(f"{key} must be a string or None")
    return _clean(value, limit=limit)


def _identifier_list(
    source: Mapping[str, Any],
    key: str,
    *,
    named: bool = False,
) -> tuple[str, ...]:
    if key not in source or source[key] is None:
        return ()
    value = source[key]
    if not isinstance(value, (list, tuple)):
        raise TypeError(f"{key} must be a list, tuple, or None")
    result: list[str] = []
    seen: set[str] = set()
    for entry in value:
        field_name = "name" if named else "id"
        if type(entry) is str:
            raw = entry
        elif isinstance(entry, Mapping):
            if field_name not in entry or type(entry[field_name]) is not str:
                raise TypeError(
                    f"{key} entries must contain an exact string {field_name}"
                )
            raw = entry[field_name]
        else:
            raise TypeError(f"{key} entries must be strings or mappings")
        cleaned = _clean(raw, limit=256)
        if not cleaned:
            raise ValueError(f"{key} entries must not be empty")
        if cleaned not in seen and len(result) < _MAX_COLLECTION:
            result.append(cleaned)
            seen.add(cleaned)
    return tuple(result)


def _relation_list(source: Mapping[str, Any]) -> tuple[str, ...]:
    if "relations" not in source or source["relations"] is None:
        return ()
    value = source["relations"]
    if not isinstance(value, (list, tuple)):
        raise TypeError("relations must be a list, tuple, or None")
    result: list[str] = []
    seen: set[str] = set()
    for entry in value:
        if type(entry) is str:
            raw = entry
        elif isinstance(entry, Mapping):
            if "id" not in entry or type(entry["id"]) is not str:
                raise TypeError(
                    "relation mappings must contain an exact string id"
                )
            raw = entry["id"]
        else:
            raise TypeError("relations entries must be strings or mappings")
        relation = _clean(raw, limit=128)
        if not relation:
            raise ValueError("relations entries must not be empty")
        if relation not in seen and len(result) < _MAX_COLLECTION:
            result.append(relation)
            seen.add(relation)
    return tuple(result)


def normalize_vacancy(raw: Mapping[str, Any]) -> NormalizedVacancy:
    if not isinstance(raw, Mapping):
        raise TypeError("vacancy must be a mapping")
    raw_id = raw.get("id")
    if not isinstance(raw_id, str):
        raise TypeError("vacancy id must be a string")
    vacancy_id = _clean(raw_id, limit=256)
    if not vacancy_id:
        raise ValueError("vacancy id must not be empty")
    if "\0" in vacancy_id:
        raise ValueError("vacancy id must not contain NUL")

    employer = _fact_object(raw, "employer")
    area = _fact_object(raw, "area")
    salary = _fact_object(raw, "salary")
    schedule = _fact_object(raw, "schedule")
    employment = _fact_object(raw, "employment")
    experience = _fact_object(raw, "experience")
    vacancy_type = _fact_object(raw, "type")
    snippet = _fact_object(raw, "snippet")
    description = _clean(
        " ".join(
            part
            for part in (
                _clean(snippet.get("requirement")),
                _clean(snippet.get("responsibility")),
                _clean(raw.get("description")),
            )
            if part
        )
    )
    title = _clean(raw.get("name"), limit=1_000)
    employer_name = _clean(employer.get("name"), limit=1_000)
    area_name = _clean(area.get("name"), limit=1_000)
    salary_from = _optional_amount(salary, "from")
    salary_to = _optional_amount(salary, "to")
    currency = _clean(salary.get("currency"), limit=32)
    schedule_id = _clean(schedule.get("id"), limit=128)
    published_at = _clean(raw.get("published_at"), limit=128)
    alternate_url = _optional_text_fact(raw, "alternate_url", limit=2_000)
    apply_alternate_url = _optional_text_fact(
        raw, "apply_alternate_url", limit=2_000
    )
    response_url = _optional_text_fact(raw, "response_url", limit=2_000)
    url = alternate_url or apply_alternate_url
    salary_text = ""
    if salary_from is not None and salary_to is not None:
        salary_text = f"{salary_from}-{salary_to} {currency}".strip()
    elif salary_from is not None:
        salary_text = f"from {salary_from} {currency}".strip()
    elif salary_to is not None:
        salary_text = f"up to {salary_to} {currency}".strip()
    job = Job(
        source="hh",
        source_id=vacancy_id,
        url=url,
        title=title,
        company=employer_name,
        salary_text=salary_text,
        salary_from=salary_from,
        salary_to=salary_to,
        currency=currency,
        location=area_name,
        remote=None if not schedule_id else schedule_id == "remote",
        description=description,
        published_at=published_at,
    ).to_dict()
    archived = _optional_bool_fact(raw, "archived")
    has_test = _optional_bool_fact(raw, "has_test")
    letter_required = _optional_bool_fact(raw, "response_letter_required")
    incomplete = _optional_bool_fact(raw, "accept_incomplete_resumes")
    temporary = _optional_bool_fact(raw, "accept_temporary")
    status_value = raw.get("status")
    if isinstance(status_value, Mapping):
        status_value = status_value.get("id")
    status = _clean(status_value, limit=128)
    if not status and archived is not None:
        status = "archived" if archived else "open"
    return NormalizedVacancy(
        id=vacancy_id,
        title=title,
        employer_id=_clean(employer.get("id"), limit=256),
        employer_name=employer_name,
        area_id=_clean(area.get("id"), limit=256),
        area_name=area_name,
        salary_from=salary_from,
        salary_to=salary_to,
        salary_currency=currency,
        salary_gross=_optional_bool_fact(salary, "gross", prefix="salary"),
        schedule_id=schedule_id,
        work_format_ids=_identifier_list(raw, "work_format"),
        employment_id=_clean(employment.get("id"), limit=128),
        experience_id=_clean(experience.get("id"), limit=128),
        professional_role_ids=_identifier_list(raw, "professional_roles"),
        key_skills=_identifier_list(raw, "key_skills", named=True),
        published_at=published_at,
        url=url,
        archived=archived,
        status=status,
        vacancy_type=_clean(vacancy_type.get("id"), limit=128),
        description=description,
        response_url=response_url,
        apply_alternate_url=apply_alternate_url,
        relations=_relation_list(raw),
        has_test=has_test,
        response_letter_required=letter_required,
        accept_incomplete_resumes=incomplete,
        accept_temporary=temporary,
        job=job,
    )


class HHSearchProvider:
    def __init__(
        self,
        transport: SearchTransport,
        repository: AutopilotRepository,
        lease_keeper: LeaseKeeper | None = None,
    ) -> None:
        if not isinstance(repository, AutopilotRepository):
            raise TypeError("repository must be an AutopilotRepository")
        self.transport = transport
        self.repository = repository
        self.lease_keeper = lease_keeper

    def _ensure_fence(self, request: SearchRequest) -> None:
        keeper = self.lease_keeper
        if keeper is None:
            self.repository.assert_fence(request.account_id, request.fencing_token)
            return
        lease = keeper.lease
        if (
            lease.account_id != request.account_id
            or lease.fencing_token != request.fencing_token
        ):
            raise ValueError("lease keeper does not match search request")
        current = keeper.ensure_current()
        if current.fencing_token != request.fencing_token:
            raise ValueError("lease renewal changed the fencing token")

    def _fetch_page(self, request: SearchRequest, page_number: int) -> SearchPage:
        self._ensure_fence(request)
        params: dict[str, Any] = deepcopy(dict(request.params))
        params["page"] = page_number
        params["per_page"] = request.per_page
        if request.query_key in {"recommendations", "__recommendations__"}:
            page = self.transport.search_recommended_vacancies_page(
                request.resume_id, params
            )
        else:
            page = self.transport.search_vacancies_page(params)
        if not isinstance(page, SearchPage):
            raise TypeError("search transport must return SearchPage")
        if page.page != page_number:
            raise ValueError("response page does not match requested page")
        if page.per_page != request.per_page:
            raise ValueError("response per_page does not match requested per_page")
        return page

    def collect(self, request: SearchRequest) -> SearchResult:
        if not isinstance(request, SearchRequest):
            raise TypeError("request must be a SearchRequest")
        cycle = self.repository.get_or_create_owned_cycle(
            account_id=request.account_id,
            run_id=request.run_id,
            policy_hash=request.policy_hash,
            fencing_token=request.fencing_token,
            mode=request.mode,
        )
        checkpoint = self.repository.ensure_search_checkpoint(
            cycle.id, request.resume_id, request.query_key
        )
        cycle = self.repository.initialize_search_cycle_distinct_cap(
            cycle.id,
            request.remaining_budget,
            fencing_token=request.fencing_token,
            mode=request.mode,
            owner_run_id=request.run_id,
            expected_claim_version=cycle.claim_version,
            policy_hash=request.policy_hash,
        )
        absolute_distinct_cap = cycle.distinct_vacancy_cap
        if absolute_distinct_cap is None:
            raise RuntimeError("search cycle distinct cap was not initialized")
        if checkpoint.status == "complete":
            return SearchResult((), checkpoint.next_page, cycle.id, 0, 0)

        known_cycle_ids = set(self.repository.list_search_cycle_vacancy_ids(cycle.id))
        newly_budgeted: set[str] = set()
        result: dict[str, NormalizedVacancy] = {}
        page_number = checkpoint.next_page
        inserted_references = 0
        new_distinct = 0
        current_checkpoint = checkpoint
        while page_number < request.max_pages and len(newly_budgeted) < request.remaining_budget:
            page = self._fetch_page(request, page_number)
            normalized_page: list[NormalizedVacancy] = []
            seen_page: set[str] = set()
            for raw in page.items:
                vacancy = normalize_vacancy(raw)
                if vacancy.id in seen_page:
                    continue
                seen_page.add(vacancy.id)
                is_new = vacancy.id not in known_cycle_ids and vacancy.id not in newly_budgeted
                if is_new and len(newly_budgeted) >= request.remaining_budget:
                    continue
                normalized_page.append(vacancy)
                if is_new:
                    newly_budgeted.add(vacancy.id)

            next_page = page_number + 1
            terminal = (
                next_page >= request.max_pages
                or len(page.items) < request.per_page
                or (page.total > 0 and next_page * page.per_page >= page.total)
                or (page.pages > 0 and next_page >= page.pages)
                or len(newly_budgeted) >= request.remaining_budget
            )
            outcome = self.repository.commit_search_page(
                checkpoint.id,
                expected_next_page=page_number,
                page=page,
                normalized=normalized_page,
                fencing_token=request.fencing_token,
                mode=request.mode,
                owner_run_id=request.run_id,
                expected_claim_version=cycle.claim_version,
                policy_hash=request.policy_hash,
                terminal=terminal,
                absolute_distinct_cap=absolute_distinct_cap,
            )
            accepted_ids = set(outcome.accepted_vacancy_ids)
            for vacancy in normalized_page:
                if vacancy.id in accepted_ids:
                    result.setdefault(vacancy.id, vacancy)
            known_cycle_ids.update(outcome.accepted_vacancy_ids)
            inserted_references += outcome.inserted_reference_count
            new_distinct += outcome.new_distinct_count
            current_checkpoint = outcome.checkpoint
            page_number = outcome.checkpoint.next_page

            if outcome.checkpoint.status == "complete":
                break

        if current_checkpoint.status != "complete":
            current_checkpoint = self.repository.complete_checkpoint(
                checkpoint.id,
                request.fencing_token,
                owner_run_id=request.run_id,
                expected_claim_version=cycle.claim_version,
                policy_hash=request.policy_hash,
            )
        return SearchResult(
            tuple(result.values()),
            current_checkpoint.next_page,
            cycle.id,
            inserted_references,
            new_distinct,
        )


__all__ = [
    "HHSearchProvider",
    "NormalizedVacancy",
    "SearchPage",
    "SearchRequest",
    "SearchResult",
    "normalize_vacancy",
]
