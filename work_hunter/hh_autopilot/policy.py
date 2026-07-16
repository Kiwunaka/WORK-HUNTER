from __future__ import annotations

import copy
from typing import Any, Callable, Mapping, Sequence

from .sanitization import phrase_matches_fact, sanitize_text
from .types import FilterDecision, NormalizedVacancy


_CLOSED_STATUSES = frozenset(
    {"archived", "closed", "deleted", "not_published", "unpublished"}
)
_CAPABILITIES = frozenset({"direct", "screening", "form"})
_EVIDENCE_LIMIT = 20
_FACT_LIMIT = 500
_TEXT_LIMIT = 20_000
_ABSENT = object()


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
    try:
        return sanitize_text(
            value,
            field=field,
            maximum=_TEXT_LIMIT,
            allow_empty=allow_empty,
            markup="strip",
            sensitive="redact",
        )
    except (TypeError, ValueError) as exc:
        raise _MalformedFact(field) from exc


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
    return _bool_fact_from_keys(source, (key,), field=field)


def _agree_aliases(
    values: Sequence[Any],
    *,
    field: str,
    set_like: bool = False,
) -> Any:
    present = [value for value in values if value is not _ABSENT]
    if not present:
        return _ABSENT
    expected = frozenset(present[0]) if set_like else present[0]
    for value in present[1:]:
        actual = frozenset(value) if set_like else value
        if actual != expected:
            raise _MalformedFact(field)
    if set_like:
        return tuple(sorted(expected))
    return present[0]


def _bool_fact_from_keys(
    source: Mapping[str, Any],
    keys: Sequence[str],
    *,
    field: str,
) -> bool | None:
    values: list[Any] = []
    for key in keys:
        if key not in source:
            values.append(_ABSENT)
            continue
        value = source[key]
        if type(value) is not bool:
            raise _MalformedFact(field)
        values.append(value)
    agreed = _agree_aliases(values, field=field)
    return None if agreed is _ABSENT else agreed


def _text_fact_from_keys(
    source: Mapping[str, Any],
    keys: Sequence[str],
    *,
    field: str,
) -> str | object:
    values = [
        (
            _normalized_text(source[key], field=field, allow_empty=True)
            if key in source
            else _ABSENT
        )
        for key in keys
    ]
    return _agree_aliases(values, field=field)


def _first_text(
    source: Mapping[str, Any],
    keys: Sequence[str],
    *,
    field: str,
) -> str:
    value = _text_fact_from_keys(source, keys, field=field)
    return "" if value is _ABSENT else str(value)


def _id_set_from_keys(
    source: Mapping[str, Any],
    keys: Sequence[str],
    *,
    field: str,
) -> tuple[str, ...] | None:
    return _string_set_from_keys(
        source,
        keys,
        field=field,
        mapping_key="id",
    )


def _string_set_from_keys(
    source: Mapping[str, Any],
    keys: Sequence[str],
    *,
    field: str,
    mapping_key: str,
) -> tuple[str, ...] | None:
    values = [
        (
            _string_list(
                source[key],
                field=field,
                mapping_key=mapping_key,
            )
            if key in source
            else _ABSENT
        )
        for key in keys
    ]
    agreed = _agree_aliases(values, field=field, set_like=True)
    return None if agreed is _ABSENT else agreed


def _merge_text_facts(
    first: str | object,
    second: str | object,
    *,
    field: str,
) -> str:
    agreed = _agree_aliases((first, second), field=field)
    return "" if agreed is _ABSENT else str(agreed)


def _safe_terms(values: Sequence[str]) -> tuple[str, ...]:
    safe: list[str] = []
    for index, value in enumerate(values[:_EVIDENCE_LIMIT]):
        try:
            parsed = sanitize_text(
                value,
                field=f"evidence[{index}]",
                maximum=_TEXT_LIMIT,
                markup="reject",
                sensitive="redact",
            )
        except (TypeError, ValueError):
            parsed = "redacted"
        safe.append(parsed[:100])
    return tuple(safe)


def _matches_any_fact(term: str, facts: Sequence[str]) -> bool:
    return any(
        (term == "redacted" and fact == "redacted")
        or phrase_matches_fact(term, fact)
        for fact in facts
    )


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
            flag = _bool_fact_from_keys(history, bool_keys, field=field)
            ids = _id_set_from_keys(history, list_keys, field=list_field)
            listed = None if ids is None else vacancy_id in ids
            if flag is None and listed is None:
                raise _MissingFact(field)
            if flag is not None and listed is not None and flag != listed:
                raise _MalformedFact(field)
            hit = flag if flag is not None else bool(listed)
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
        vacancy_listed = (
            None if vacancy_ids is None else vacancy_id in vacancy_ids
        )
        if (
            vacancy_flag is not None
            and vacancy_listed is not None
            and vacancy_flag != vacancy_listed
        ):
            raise _MalformedFact("blacklist.vacancy")
        if vacancy_flag is True or vacancy_listed is True:
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
        employer_listed = (
            None if employer_ids is None else employer_id in employer_ids
        )
        if (
            employer_flag is not None
            and employer_listed is not None
            and employer_flag != employer_listed
        ):
            raise _MalformedFact("blacklist.employer")
        if employer_flag is True or employer_listed is True:
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
        skills = _string_set_from_keys(
            vacancy,
            ("key_skills", "skills"),
            field="vacancy.key_skills",
            mapping_key="name",
        )
        if skills is None:
            skills = ()
        top_employer_name = _text_fact_from_keys(
            vacancy,
            ("employer_name",),
            field="vacancy.employer_name",
        )
        nested_employer_name: str | object = _ABSENT
        if "employer" in vacancy:
            employer = _optional_mapping(
                vacancy,
                "employer",
                field="vacancy.employer",
            )
            nested_employer_name = _text_fact_from_keys(
                employer,
                ("name",),
                field="vacancy.employer.name",
            )
        employer_name = _merge_text_facts(
            top_employer_name,
            nested_employer_name,
            field="vacancy.employer_name",
        )
        facts = tuple(
            part
            for part in (
                _first_text(vacancy, ("title", "name"), field="vacancy.title"),
                _first_text(
                    vacancy,
                    ("description",),
                    field="vacancy.description",
                ),
                employer_name,
                *skills,
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
            if term and _matches_any_fact(term, facts)
        )
        if matched:
            return _reject(
                "hard_filter:excluded_keywords",
                matched=_safe_terms(matched),
            )
        required = _configured_list(self._filters, "required_keywords")
        missing = tuple(
            term
            for term in required
            if not _matches_any_fact(term, facts)
        )
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
        top_area = _text_fact_from_keys(
            vacancy,
            ("area_id",),
            field="vacancy.area_id",
        )
        nested_area: str | object = _ABSENT
        if "area" in vacancy:
            area = _optional_mapping(vacancy, "area", field="vacancy.area")
            nested_area = _text_fact_from_keys(
                area,
                ("id",),
                field="vacancy.area.id",
            )
        area_id = _merge_text_facts(
            top_area,
            nested_area,
            field="vacancy.area_id",
        )
        if not area_id:
            raise _MissingFact("area_id")
        if area_id in areas:
            return None
        relocation_id_values: list[Any] = []
        relocation_flag_values: list[Any] = []
        for source, prefix in (
            (context, "context"),
            (candidate, "candidate"),
            (resume, "resume"),
            (vacancy, "vacancy"),
        ):
            flag = _bool_fact_from_keys(
                source,
                ("relocation_allowed", "allow_relocation"),
                field=f"{prefix}.relocation_allowed",
            )
            relocation_flag_values.append(
                _ABSENT if flag is None else flag
            )
            ids = _id_set_from_keys(
                source,
                ("relocation_area_ids", "relocation_areas"),
                field=f"{prefix}.relocation_area_ids",
            )
            relocation_id_values.append(
                _ABSENT if ids is None else ids
            )
        relocation_flag = _agree_aliases(
            relocation_flag_values,
            field="relocation",
        )
        relocation_ids = _agree_aliases(
            relocation_id_values,
            field="relocation",
            set_like=True,
        )
        flag_value = None if relocation_flag is _ABSENT else bool(relocation_flag)
        listed_value = (
            None
            if relocation_ids is _ABSENT
            else area_id in relocation_ids
        )
        if flag_value is None and listed_value is None:
            raise _MissingFact("relocation")
        if (
            flag_value is not None
            and listed_value is not None
            and flag_value != listed_value
        ):
            raise _MalformedFact("relocation")
        if flag_value is True or listed_value is True:
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
        top_schedule = _text_fact_from_keys(
            vacancy,
            ("schedule_id",),
            field="vacancy.schedule_id",
        )
        nested_schedule: str | object = _ABSENT
        if "schedule" in vacancy:
            schedule_data = _optional_mapping(
                vacancy,
                "schedule",
                field="vacancy.schedule",
            )
            nested_schedule = _text_fact_from_keys(
                schedule_data,
                ("id",),
                field="vacancy.schedule.id",
            )
        schedule = _merge_text_facts(
            top_schedule,
            nested_schedule,
            field="vacancy.schedule_id",
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
            top_employment = _text_fact_from_keys(
                vacancy,
                ("employment_id",),
                field="vacancy.employment_id",
            )
            nested_employment: str | object = _ABSENT
            if "employment" in vacancy:
                data = _optional_mapping(
                    vacancy,
                    "employment",
                    field="vacancy.employment",
                )
                nested_employment = _text_fact_from_keys(
                    data,
                    ("id",),
                    field="vacancy.employment.id",
                )
            employment = _merge_text_facts(
                top_employment,
                nested_employment,
                field="vacancy.employment_id",
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
        top_experience = _text_fact_from_keys(
            vacancy,
            ("experience_id",),
            field="vacancy.experience_id",
        )
        nested_experience: str | object = _ABSENT
        if "experience" in vacancy:
            data = _optional_mapping(
                vacancy,
                "experience",
                field="vacancy.experience",
            )
            nested_experience = _text_fact_from_keys(
                data,
                ("id",),
                field="vacancy.experience.id",
            )
        experience = _merge_text_facts(
            top_experience,
            nested_experience,
            field="vacancy.experience_id",
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
        top_salary_from = self._salary_amount_fact(
            vacancy,
            "salary_from",
            field="vacancy.salary_from",
        )
        top_salary_to = self._salary_amount_fact(
            vacancy,
            "salary_to",
            field="vacancy.salary_to",
        )
        salary_mapping: Mapping[str, Any] = {}
        nested_salary_from: int | None | object = _ABSENT
        nested_salary_to: int | None | object = _ABSENT
        if "salary" in vacancy:
            salary_mapping = _optional_mapping(
                vacancy,
                "salary",
                field="vacancy.salary",
            )
            nested_salary_from = self._salary_amount_fact(
                salary_mapping,
                "from",
                field="vacancy.salary.from",
            )
            nested_salary_to = self._salary_amount_fact(
                salary_mapping,
                "to",
                field="vacancy.salary.to",
            )
        salary_from_value = _agree_aliases(
            (top_salary_from, nested_salary_from),
            field="vacancy.salary_from",
        )
        salary_to_value = _agree_aliases(
            (top_salary_to, nested_salary_to),
            field="vacancy.salary_to",
        )
        salary_from = (
            None
            if salary_from_value is _ABSENT
            else salary_from_value
        )
        salary_to = (
            None if salary_to_value is _ABSENT else salary_to_value
        )
        if (
            salary_from is not None
            and salary_to is not None
            and salary_from > salary_to
        ):
            raise _MalformedFact("vacancy.salary")
        top_currency = _text_fact_from_keys(
            vacancy,
            ("salary_currency", "currency"),
            field="vacancy.salary_currency",
        )
        nested_currency: str | object = _ABSENT
        if salary_mapping:
            nested_currency = _text_fact_from_keys(
                salary_mapping,
                ("currency",),
                field="vacancy.salary.currency",
            )
        currency = _merge_text_facts(
            top_currency,
            nested_currency,
            field="vacancy.salary_currency",
        )
        if floor == 0:
            return None
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
    def _salary_amount_fact(
        source: Mapping[str, Any],
        key: str,
        *,
        field: str,
    ) -> int | None | object:
        if key not in source:
            return _ABSENT
        if source[key] is None:
            return None
        value = source[key]
        if type(value) is not int or value < 0:
            raise _MalformedFact(field)
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
        explicit_values: list[Any] = []
        if "application_capabilities" in vacancy:
            explicit_values.append(
                tuple(
                    sorted(
                        _string_list(
                            vacancy["application_capabilities"],
                            field="vacancy.application_capabilities",
                            maximum=len(_CAPABILITIES),
                        )
                    )
                )
            )
        else:
            explicit_values.append(_ABSENT)
        if "application_capability" in vacancy:
            explicit_values.append(
                (
                    _normalized_text(
                        vacancy["application_capability"],
                        field="vacancy.application_capability",
                    ),
                )
            )
        else:
            explicit_values.append(_ABSENT)
        explicit = _agree_aliases(
            explicit_values,
            field="vacancy.application_capabilities",
            set_like=True,
        )
        if explicit is not _ABSENT and (
            not explicit or not set(explicit) <= _CAPABILITIES
        ):
            raise _MalformedFact("vacancy.application_capabilities")

        derived: set[str] = set()
        has_test = _bool_fact(
            vacancy,
            "has_test",
            field="vacancy.has_test",
        )
        if has_test is True:
            derived.add("screening")
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
            derived.add("direct")
        elif apply_url:
            derived.add("form")
        elif has_test is False:
            derived.add("direct")

        if explicit is not _ABSENT:
            if not isinstance(explicit, tuple):
                raise _MalformedFact("vacancy.application_capabilities")
            required = set(explicit)
        else:
            required = derived
        if not required:
            raise _MissingFact("application_capabilities")

        if configured:
            available = (
                configured
                if context_supported is None
                else configured.intersection(context_supported)
            )
        elif context_supported is not None:
            available = context_supported
        else:
            raise _MissingFact("context.supported_application_capabilities")

        unavailable = tuple(sorted(required - available))
        if unavailable:
            return _reject(
                "hard_filter:required_application_capabilities",
                required=_safe_terms(tuple(sorted(required))),
                unavailable=_safe_terms(unavailable),
            )
        return None


__all__ = ["FilterDecision", "HardFilter"]
