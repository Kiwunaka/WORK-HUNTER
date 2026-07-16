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


def _optional_number(value: Any) -> int | None:
    return value if type(value) is int else None


def _optional_bool(value: Any) -> bool | None:
    return value if type(value) is bool else None


def _identifier_list(value: Any, *, named: bool = False) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        return ()
    result: list[str] = []
    seen: set[str] = set()
    for entry in value[:_MAX_COLLECTION]:
        item = _object(entry)
        raw = item.get("name" if named else "id")
        cleaned = _clean(raw, limit=256)
        if cleaned and cleaned not in seen:
            result.append(cleaned)
            seen.add(cleaned)
    return tuple(result)


def _relation_list(value: Any) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        return ()
    result: list[str] = []
    for entry in value[:_MAX_COLLECTION]:
        raw = entry.get("id") if isinstance(entry, Mapping) else entry
        relation = _clean(raw, limit=128)
        if relation and relation not in result:
            result.append(relation)
    return tuple(result)


def normalize_vacancy(raw: Mapping[str, Any]) -> NormalizedVacancy:
    if not isinstance(raw, Mapping):
        raise TypeError("vacancy must be a mapping")
    vacancy_id = _clean(raw.get("id"), limit=256)
    if not vacancy_id:
        raise ValueError("vacancy id must not be empty")
    if "\0" in vacancy_id:
        raise ValueError("vacancy id must not contain NUL")

    employer = _object(raw.get("employer"))
    area = _object(raw.get("area"))
    salary = _object(raw.get("salary"))
    schedule = _object(raw.get("schedule"))
    employment = _object(raw.get("employment"))
    experience = _object(raw.get("experience"))
    vacancy_type = _object(raw.get("type"))
    snippet = _object(raw.get("snippet"))
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
    salary_from = _optional_number(salary.get("from"))
    salary_to = _optional_number(salary.get("to"))
    currency = _clean(salary.get("currency"), limit=32)
    schedule_id = _clean(schedule.get("id"), limit=128)
    published_at = _clean(raw.get("published_at"), limit=128)
    url = _clean(raw.get("alternate_url") or raw.get("apply_alternate_url"), limit=2_000)
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
        remote=schedule_id == "remote",
        description=description,
        published_at=published_at,
    ).to_dict()
    archived = _optional_bool(raw.get("archived"))
    has_test = _optional_bool(raw.get("has_test"))
    letter_required = _optional_bool(raw.get("response_letter_required"))
    incomplete = _optional_bool(raw.get("accept_incomplete_resumes"))
    temporary = _optional_bool(raw.get("accept_temporary"))
    status_value = raw.get("status")
    if isinstance(status_value, Mapping):
        status_value = status_value.get("id")
    status = _clean(status_value, limit=128)
    if not status:
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
        salary_gross=_optional_bool(salary.get("gross")),
        schedule_id=schedule_id,
        work_format_ids=_identifier_list(raw.get("work_format")),
        employment_id=_clean(employment.get("id"), limit=128),
        experience_id=_clean(experience.get("id"), limit=128),
        professional_role_ids=_identifier_list(raw.get("professional_roles")),
        key_skills=_identifier_list(raw.get("key_skills"), named=True),
        published_at=published_at,
        url=url,
        archived=False if archived is None else archived,
        status=status,
        vacancy_type=_clean(vacancy_type.get("id"), limit=128),
        description=description,
        response_url=_clean(raw.get("response_url"), limit=2_000),
        apply_alternate_url=_clean(raw.get("apply_alternate_url"), limit=2_000),
        relations=_relation_list(raw.get("relations")),
        has_test=False if has_test is None else has_test,
        response_letter_required=False if letter_required is None else letter_required,
        accept_incomplete_resumes=False if incomplete is None else incomplete,
        accept_temporary=False if temporary is None else temporary,
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
        if checkpoint.status == "complete":
            return SearchResult((), checkpoint.next_page, cycle.id, 0)

        known_cycle_ids = set(self.repository.list_search_cycle_vacancy_ids(cycle.id))
        newly_budgeted: set[str] = set()
        result: dict[str, NormalizedVacancy] = {}
        page_number = checkpoint.next_page
        inserted_before = self.repository.count_search_results(cycle_id=cycle.id)
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

            self.repository.commit_search_page(
                checkpoint.id,
                expected_next_page=page_number,
                page=page,
                normalized=normalized_page,
                fencing_token=request.fencing_token,
                mode=request.mode,
                owner_run_id=request.run_id,
                expected_claim_version=cycle.claim_version,
                policy_hash=request.policy_hash,
            )
            for vacancy in normalized_page:
                result.setdefault(vacancy.id, vacancy)
            known_cycle_ids.update(item.id for item in normalized_page)
            page_number += 1

            if len(newly_budgeted) >= request.remaining_budget:
                break
            if len(page.items) < request.per_page:
                break
            if page.total > 0 and page_number * page.per_page >= page.total:
                break
            if page.pages > 0 and page_number >= page.pages:
                break

        self.repository.complete_checkpoint(
            checkpoint.id,
            request.fencing_token,
            owner_run_id=request.run_id,
            expected_claim_version=cycle.claim_version,
            policy_hash=request.policy_hash,
        )
        inserted_after = self.repository.count_search_results(cycle_id=cycle.id)
        return SearchResult(
            tuple(result.values()),
            page_number,
            cycle.id,
            inserted_after - inserted_before,
        )


__all__ = [
    "HHSearchProvider",
    "NormalizedVacancy",
    "SearchPage",
    "SearchRequest",
    "SearchResult",
    "normalize_vacancy",
]
