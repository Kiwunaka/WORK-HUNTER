from __future__ import annotations

import copy
import html
import re
from typing import Any, Callable, Mapping, Sequence

from .types import FilterDecision, NormalizedVacancy


_MARKUP = re.compile(r"<[^>]*>")
_SPACES = re.compile(r"\s+")
_CLOSED_STATUSES = frozenset(
    {"archived", "closed", "deleted", "not_published", "unpublished"}
)
_CAPABILITIES = frozenset({"direct", "screening", "form"})
_EVIDENCE_LIMIT = 20
_FACT_LIMIT = 500


class _MissingFact(ValueError):
    def __init__(self, field: str):
        super().__init__(field)
        self.field = field


class _MalformedFact(ValueError):
    def __init__(self, field: str):
        super().__init__(field)
        self.field = field


def _text(value: Any, *, field: str, allow_empty: bool = False) -> str:
    if type(value) is not str:
        raise _MalformedFact(field)
    normalized = _SPACES.sub(
        " ",
        _MARKUP.sub(" ", html.unescape(value)),
    ).strip()
    if "\0" in normalized:
        raise _MalformedFact(field)
    if not normalized and not allow_empty:
        raise _MalformedFact(field)
    return normalized


def _normalized_text(value: Any, *, field: str, allow_empty: bool = False) -> str:
    return _text(value, field=field, allow_empty=allow_empty).casefold()


def _mapping(value: Any, *, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise _MalformedFact(field)
    try:
        return copy.deepcopy(dict(value))
    except Exception as exc:
        raise _MalformedFact(field) from exc


def _optional_mapping(source: Mapping[str, Any], key: str, *, field: str) -> dict[str, Any]:
    if key not in source:
        return {}
    return _mapping(source[key], field=field)


def _string_list(
    value: Any,
    *,
    field: str,
    maximum: int = _FACT_LIMIT,
    canonical: bool = True,
    mapping_key: str = "id",
) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _MalformedFact(field)
    if len(value) > maximum:
        raise _MalformedFact(field)
    result: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        try:
            raw = item
            if isinstance(item, Mapping):
                if mapping_key not in item:
                    raise _MalformedFact(field)
                raw = item[mapping_key]
            parsed = (
                _normalized_text(raw, field=f"{field}[{index}]")
                if canonical
                else _text(raw, field=f"{field}[{index}]")
            )
        except _MalformedFact as exc:
            raise _MalformedFact(field) from exc
        if parsed not in seen:
            seen.add(parsed)
            result.append(parsed)
    return tuple(result)


def _configured_list(
    filters: Mapping[str, Any],
    key: str,
    *,
    maximum: int = 1000,
) -> tuple[str, ...]:
    value = filters.get(key, [])
    return _string_list(
        value,
        field=f"filters.{key}",
        maximum=maximum,
    )


def _bool_fact(source: Mapping[str, Any], key: str, *, field: str) -> bool | None:
    if key not in source:
        return None
    value = source[key]
    if type(value) is not bool:
        raise _MalformedFact(field)
    return value


def _first_text(
    source: Mapping[str, Any],
    keys: Sequence[str],
    *,
    field: str,
) -> str:
    for key in keys:
        if key in source:
            return _normalized_text(source[key], field=field, allow_empty=True)
    return ""


def _id_set_from_keys(
    source: Mapping[str, Any],
    keys: Sequence[str],
    *,
    field: str,
) -> tuple[str, ...] | None:
    for key in keys:
        if key in source:
            return _string_list(source[key], field=field)
    return None


def _safe_terms(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(value[:100] for value in values[:_EVIDENCE_LIMIT])


def _missing(field: str) -> FilterDecision:
    return FilterDecision(False, "missing_required_data", {"field": field})


def _reject(reason: str, **evidence: Any) -> FilterDecision:
    return FilterDecision(False, reason, evidence)


class HardFilter:
    def __init__(self, filters: Mapping[str, Any]):
        self._filters = _mapping(filters, field="filters")

    def evaluate(
        self,
        vacancy: NormalizedVacancy | Mapping[str, Any],
        resume: Mapping[str, Any],
        candidate: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> FilterDecision:
        vacancy_data = (
            vacancy.to_dict()
            if isinstance(vacancy, NormalizedVacancy)
            else _mapping(vacancy, field="vacancy")
        )
        resume_data = _mapping(resume, field="resume")
        candidate_data = _mapping(candidate, field="candidate")
        context_data = _mapping(context, field="context")
        checks: tuple[
            Callable[
                [
                    Mapping[str, Any],
                    Mapping[str, Any],
                    Mapping[str, Any],
                    Mapping[str, Any],
                ],
                FilterDecision | None,
            ],
            ...,
        ] = (
            self._vacancy_open,
            self._history,
            self._blacklists,
            self._keywords_and_roles,
            self._area_and_relocation,
            self._work_format,
            self._experience,
            self._salary,
            self._candidate_constraints,
            self._application_capabilities,
        )
        try:
            for check in checks:
                decision = check(
                    vacancy_data,
                    resume_data,
                    candidate_data,
                    context_data,
                )
                if decision is not None:
                    return decision
        except (_MissingFact, _MalformedFact) as exc:
            return _missing(exc.field)
        return FilterDecision(
            True,
            "hard_filters_passed",
            {
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

    def _vacancy_open(
        self,
        vacancy: Mapping[str, Any],
        _resume: Mapping[str, Any],
        _candidate: Mapping[str, Any],
        _context: Mapping[str, Any],
    ) -> FilterDecision | None:
        archived = _bool_fact(
            vacancy,
            "archived",
            field="vacancy.archived",
        )
        status = _first_text(
            vacancy,
            ("status",),
            field="vacancy.status",
        )
        if archived is None and not status:
            raise _MissingFact("vacancy_open")
        if archived is True or status in _CLOSED_STATUSES:
            return _reject(
                "hard_filter:vacancy_closed",
                archived=archived is True,
                status=status[:100],
            )
        return None

    def _history(
        self,
        vacancy: Mapping[str, Any],
        _resume: Mapping[str, Any],
        _candidate: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> FilterDecision | None:
        if "history" not in context:
            raise _MissingFact("history")
        history = _mapping(context["history"], field="history")
        vacancy_id = _normalized_text(
            vacancy.get("id"),
            field="vacancy.id",
        )
        categories = (
            (
                ("already_applied", "existing"),
                ("applied_vacancy_ids", "existing_vacancy_ids"),
                "hard_filter:already_applied",
                "history.already_applied",
                "history.applied_vacancy_ids",
            ),
            (
                ("active", "active_application"),
                ("active_vacancy_ids",),
                "hard_filter:active_history",
                "history.active",
                "history.active_vacancy_ids",
            ),
            (
                ("permanently_skipped",),
                ("permanently_skipped_vacancy_ids",),
                "hard_filter:permanently_skipped",
                "history.permanently_skipped",
                "history.permanently_skipped_vacancy_ids",
            ),
        )
        for bool_keys, list_keys, reason, field, list_field in categories:
            explicit = False
            hit = False
            for key in bool_keys:
                flag = _bool_fact(history, key, field=field)
                if flag is not None:
                    explicit = True
                    hit = hit or flag
                    break
            ids = _id_set_from_keys(history, list_keys, field=list_field)
            if ids is not None:
                explicit = True
                hit = hit or vacancy_id in ids
            if not explicit:
                raise _MissingFact(field)
            if hit:
                return _reject(reason, vacancy_id=vacancy_id[:100])
        return None

    def _blacklists(
        self,
        vacancy: Mapping[str, Any],
        _resume: Mapping[str, Any],
        _candidate: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> FilterDecision | None:
        if "blacklist" not in context:
            raise _MissingFact("blacklist")
        blacklist = _mapping(context["blacklist"], field="blacklist")
        vacancy_id = _normalized_text(vacancy.get("id"), field="vacancy.id")
        employer_id = _first_text(
            vacancy,
            ("employer_id",),
            field="vacancy.employer_id",
        )
        vacancy_flag = _bool_fact(
            blacklist,
            "vacancy",
            field="blacklist.vacancy",
        )
        vacancy_ids = _id_set_from_keys(
            blacklist,
            ("vacancy_ids",),
            field="blacklist.vacancy_ids",
        )
        if vacancy_flag is None and vacancy_ids is None:
            raise _MissingFact("blacklist.vacancy")
        if vacancy_flag is True or (
            vacancy_ids is not None and vacancy_id in vacancy_ids
        ):
            return _reject(
                "hard_filter:vacancy_blacklist",
                vacancy_id=vacancy_id[:100],
            )
        use_employer_blacklist = self._filters.get(
            "use_employer_blacklist",
            True,
        )
        if type(use_employer_blacklist) is not bool:
            raise _MalformedFact("filters.use_employer_blacklist")
        if not use_employer_blacklist:
            return None
        employer_flag = _bool_fact(
            blacklist,
            "employer",
            field="blacklist.employer",
        )
        employer_ids = _id_set_from_keys(
            blacklist,
            ("employer_ids",),
            field="blacklist.employer_ids",
        )
        if employer_flag is None and employer_ids is None:
            raise _MissingFact("blacklist.employer")
        if employer_flag is True or (
            employer_ids is not None and employer_id in employer_ids
        ):
            return _reject(
                "hard_filter:employer_blacklist",
                employer_id=employer_id[:100],
            )
        return None

    def _keywords_and_roles(
        self,
        vacancy: Mapping[str, Any],
        _resume: Mapping[str, Any],
        candidate: Mapping[str, Any],
        _context: Mapping[str, Any],
    ) -> FilterDecision | None:
        skills: tuple[str, ...] = ()
        if "key_skills" in vacancy:
            skills = _string_list(
                vacancy["key_skills"],
                field="vacancy.key_skills",
                mapping_key="name",
            )
        employer_name = _first_text(
            vacancy,
            ("employer_name",),
            field="vacancy.employer_name",
        )
        if not employer_name and "employer" in vacancy:
            employer = _optional_mapping(
                vacancy,
                "employer",
                field="vacancy.employer",
            )
            employer_name = _first_text(
                employer,
                ("name",),
                field="vacancy.employer.name",
            )
        searchable = " ".join(
            part
            for part in (
                _first_text(vacancy, ("title", "name"), field="vacancy.title"),
                _first_text(
                    vacancy,
                    ("description",),
                    field="vacancy.description",
                ),
                employer_name,
                " ".join(skills),
            )
            if part
        )
        excluded = list(_configured_list(self._filters, "excluded_keywords"))
        if "stop_words" in candidate:
            excluded.extend(
                _string_list(
                    candidate["stop_words"],
                    field="candidate.stop_words",
                    maximum=1000,
                )
            )
        matched = tuple(
            term
            for term in dict.fromkeys(excluded)
            if term and term in searchable
        )
        if matched:
            return _reject(
                "hard_filter:excluded_keywords",
                matched=_safe_terms(matched),
            )
        required = _configured_list(self._filters, "required_keywords")
        missing = tuple(term for term in required if term not in searchable)
        if missing:
            return _reject(
                "hard_filter:required_keywords",
                missing=_safe_terms(missing),
            )
        allowed_roles = _configured_list(
            self._filters,
            "allowed_role_families",
        )
        if allowed_roles:
            role_ids = _id_set_from_keys(
                vacancy,
                (
                    "professional_role_ids",
                    "role_family_ids",
                    "role_families",
                ),
                field="professional_role_ids",
            )
            if not role_ids:
                raise _MissingFact("professional_role_ids")
            if not set(allowed_roles).intersection(role_ids):
                return _reject(
                    "hard_filter:allowed_role_families",
                    actual=_safe_terms(role_ids),
                    allowed=_safe_terms(allowed_roles),
                )
        return None

    def _area_and_relocation(
        self,
        vacancy: Mapping[str, Any],
        resume: Mapping[str, Any],
        candidate: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> FilterDecision | None:
        areas = _configured_list(self._filters, "areas", maximum=500)
        if not areas:
            return None
        area_id = _first_text(
            vacancy,
            ("area_id",),
            field="vacancy.area_id",
        )
        if not area_id and "area" in vacancy:
            area = _optional_mapping(vacancy, "area", field="vacancy.area")
            area_id = _first_text(area, ("id",), field="vacancy.area.id")
        if not area_id:
            raise _MissingFact("area_id")
        if area_id in areas:
            return None
        relocation_ids: tuple[str, ...] | None = None
        relocation_flag: bool | None = None
        for source, prefix in (
            (context, "context"),
            (candidate, "candidate"),
            (resume, "resume"),
            (vacancy, "vacancy"),
        ):
            if relocation_flag is None:
                for key in ("relocation_allowed", "allow_relocation"):
                    flag = _bool_fact(
                        source,
                        key,
                        field=f"{prefix}.relocation_allowed",
                    )
                    if flag is not None:
                        relocation_flag = flag
                        break
            if relocation_ids is None:
                relocation_ids = _id_set_from_keys(
                    source,
                    ("relocation_area_ids", "relocation_areas"),
                    field=f"{prefix}.relocation_area_ids",
                )
        if relocation_flag is None and relocation_ids is None:
            raise _MissingFact("relocation")
        if relocation_flag is True or (
            relocation_ids is not None and area_id in relocation_ids
        ):
            return None
        return _reject(
            "hard_filter:area",
            area_id=area_id[:100],
            relocation_allowed=False,
        )

    def _work_format(
        self,
        vacancy: Mapping[str, Any],
        _resume: Mapping[str, Any],
        _candidate: Mapping[str, Any],
        _context: Mapping[str, Any],
    ) -> FilterDecision | None:
        schedule = _first_text(
            vacancy,
            ("schedule_id",),
            field="vacancy.schedule_id",
        )
        if not schedule and "schedule" in vacancy:
            schedule_data = _optional_mapping(
                vacancy,
                "schedule",
                field="vacancy.schedule",
            )
            schedule = _first_text(
                schedule_data,
                ("id",),
                field="vacancy.schedule.id",
            )
        work_formats = _id_set_from_keys(
            vacancy,
            ("work_format_ids", "work_formats", "work_format"),
            field="vacancy.work_format_ids",
        )
        remote = _bool_fact(vacancy, "remote", field="vacancy.remote")
        derived_remote: bool | None = None
        if schedule:
            derived_remote = schedule == "remote"
        if work_formats:
            from_formats = "remote" in work_formats
            if derived_remote is not None and from_formats != derived_remote:
                raise _MalformedFact("vacancy.remote")
            derived_remote = from_formats
        if remote is not None and derived_remote is not None and remote != derived_remote:
            raise _MalformedFact("vacancy.remote")
        if remote is None:
            remote = derived_remote
        remote_mode = self._filters.get("remote", "any")
        if type(remote_mode) is not str or remote_mode not in {
            "any",
            "only",
            "exclude",
        }:
            raise _MalformedFact("filters.remote")
        if remote_mode != "any" and remote is None:
            raise _MissingFact("remote")
        if remote_mode == "only" and remote is not True:
            return _reject("hard_filter:remote", remote=False)
        if remote_mode == "exclude" and remote is True:
            return _reject("hard_filter:remote", remote=True)
        schedules = _configured_list(self._filters, "schedules", maximum=100)
        if schedules:
            if not schedule:
                raise _MissingFact("schedule_id")
            if schedule not in schedules:
                return _reject(
                    "hard_filter:schedule",
                    schedule=schedule[:100],
                )
        employment_types = _configured_list(
            self._filters,
            "employment_types",
            maximum=100,
        )
        if employment_types:
            employment = _first_text(
                vacancy,
                ("employment_id",),
                field="vacancy.employment_id",
            )
            if not employment and "employment" in vacancy:
                data = _optional_mapping(
                    vacancy,
                    "employment",
                    field="vacancy.employment",
                )
                employment = _first_text(
                    data,
                    ("id",),
                    field="vacancy.employment.id",
                )
            if not employment:
                raise _MissingFact("employment_id")
            if employment not in employment_types:
                return _reject(
                    "hard_filter:employment_type",
                    employment=employment[:100],
                )
        return None

    def _experience(
        self,
        vacancy: Mapping[str, Any],
        _resume: Mapping[str, Any],
        _candidate: Mapping[str, Any],
        _context: Mapping[str, Any],
    ) -> FilterDecision | None:
        allowed = _configured_list(
            self._filters,
            "experience_levels",
            maximum=100,
        )
        if not allowed:
            return None
        experience = _first_text(
            vacancy,
            ("experience_id",),
            field="vacancy.experience_id",
        )
        if not experience and "experience" in vacancy:
            data = _optional_mapping(
                vacancy,
                "experience",
                field="vacancy.experience",
            )
            experience = _first_text(
                data,
                ("id",),
                field="vacancy.experience.id",
            )
        if not experience:
            raise _MissingFact("experience_id")
        if experience not in allowed:
            return _reject(
                "hard_filter:experience",
                experience=experience[:100],
            )
        return None

    def _salary(
        self,
        vacancy: Mapping[str, Any],
        _resume: Mapping[str, Any],
        _candidate: Mapping[str, Any],
        _context: Mapping[str, Any],
    ) -> FilterDecision | None:
        floor = self._filters.get("minimum_salary", 0)
        if type(floor) is not int or floor < 0:
            raise _MalformedFact("filters.minimum_salary")
        if floor == 0:
            return None
        salary_from = self._salary_amount(vacancy, "salary_from")
        salary_to = self._salary_amount(vacancy, "salary_to")
        salary_mapping: Mapping[str, Any] = {}
        if "salary" in vacancy:
            salary_mapping = _optional_mapping(
                vacancy,
                "salary",
                field="vacancy.salary",
            )
            if salary_from is None:
                salary_from = self._salary_amount(salary_mapping, "from")
            if salary_to is None:
                salary_to = self._salary_amount(salary_mapping, "to")
        if salary_from is None and salary_to is None:
            unknown = self._filters.get("unknown_salary", "allow")
            if unknown == "allow":
                return None
            if unknown == "reject":
                return _reject(
                    "hard_filter:minimum_salary",
                    salary_known=False,
                    minimum=floor,
                )
            raise _MalformedFact("filters.unknown_salary")
        currency = _first_text(
            vacancy,
            ("salary_currency",),
            field="vacancy.salary_currency",
        )
        if not currency and salary_mapping:
            currency = _first_text(
                salary_mapping,
                ("currency",),
                field="vacancy.salary.currency",
            )
        if not currency:
            raise _MissingFact("salary_currency")
        expected_currency = _normalized_text(
            self._filters.get("salary_currency", ""),
            field="filters.salary_currency",
        )
        if currency != expected_currency:
            return _reject(
                "hard_filter:minimum_salary",
                currency=currency[:20],
                expected_currency=expected_currency[:20],
            )
        best = salary_to if salary_to is not None else salary_from
        assert best is not None
        if best < floor:
            return _reject(
                "hard_filter:minimum_salary",
                maximum=best,
                minimum=floor,
            )
        return None

    @staticmethod
    def _salary_amount(source: Mapping[str, Any], key: str) -> int | None:
        if key not in source or source[key] is None:
            return None
        value = source[key]
        if type(value) is not int or value < 0:
            raise _MalformedFact(f"vacancy.{key}")
        return value

    def _candidate_constraints(
        self,
        _vacancy: Mapping[str, Any],
        _resume: Mapping[str, Any],
        candidate: Mapping[str, Any],
        _context: Mapping[str, Any],
    ) -> FilterDecision | None:
        requirements = (
            ("languages", "hard_filter:languages", 100),
            ("citizenships", "hard_filter:citizenships", 500),
        )
        for field, reason, maximum in requirements:
            required = _configured_list(
                self._filters,
                field,
                maximum=maximum,
            )
            if not required:
                continue
            if field not in candidate:
                raise _MissingFact(field)
            actual = _string_list(
                candidate[field],
                field=f"candidate.{field}",
                maximum=maximum,
            )
            missing = tuple(value for value in required if value not in actual)
            if missing:
                return _reject(reason, missing=_safe_terms(missing))
        return None

    def _application_capabilities(
        self,
        vacancy: Mapping[str, Any],
        _resume: Mapping[str, Any],
        _candidate: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> FilterDecision | None:
        configured = set(
            _configured_list(
                self._filters,
                "required_application_capabilities",
                maximum=len(_CAPABILITIES),
            )
        )
        context_supported: set[str] | None = None
        if "supported_application_capabilities" in context:
            context_supported = set(
                _string_list(
                    context["supported_application_capabilities"],
                    field="context.supported_application_capabilities",
                    maximum=len(_CAPABILITIES),
                )
            )
            if not context_supported <= _CAPABILITIES:
                raise _MalformedFact("context.supported_application_capabilities")
        if configured and not configured <= _CAPABILITIES:
            raise _MalformedFact("filters.required_application_capabilities")
        if configured:
            available = (
                configured
                if context_supported is None
                else configured.intersection(context_supported)
            )
        elif context_supported is not None:
            available = context_supported
        else:
            return None

        required: set[str] | None = None
        if "application_capabilities" in vacancy:
            required = set(
                _string_list(
                    vacancy["application_capabilities"],
                    field="vacancy.application_capabilities",
                    maximum=len(_CAPABILITIES),
                )
            )
        elif "application_capability" in vacancy:
            required = {
                _normalized_text(
                    vacancy["application_capability"],
                    field="vacancy.application_capability",
                )
            }
        if required is not None:
            if not required or not required <= _CAPABILITIES:
                raise _MalformedFact("vacancy.application_capabilities")
        else:
            required = set()
            has_test = _bool_fact(
                vacancy,
                "has_test",
                field="vacancy.has_test",
            )
            if has_test is True:
                required.add("screening")
            response_url = _first_text(
                vacancy,
                ("response_url",),
                field="vacancy.response_url",
            )
            apply_url = _first_text(
                vacancy,
                ("apply_alternate_url",),
                field="vacancy.apply_alternate_url",
            )
            if response_url:
                required.add("direct")
            elif apply_url:
                required.add("form")
            elif has_test is False:
                required.add("direct")
            if not required:
                raise _MissingFact("application_capabilities")
        unavailable = tuple(sorted(required - available))
        if unavailable:
            return _reject(
                "hard_filter:required_application_capabilities",
                required=_safe_terms(tuple(sorted(required))),
                unavailable=_safe_terms(unavailable),
            )
        return None


__all__ = ["FilterDecision", "HardFilter"]
