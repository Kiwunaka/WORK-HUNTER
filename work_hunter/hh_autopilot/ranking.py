from __future__ import annotations

import copy
import json
import math
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

from work_hunter.llm.structured import (
    StructuredOutputSchema,
    send_structured_chat,
)

from .sanitization import phrase_matches_fact, sanitize_text
from .types import (
    AIDecision,
    FilterDecision,
    NormalizedVacancy,
    RankScore,
    RankedCandidate,
    RankingDecision,
)


COMPONENTS = (
    "role",
    "skills",
    "experience",
    "salary",
    "work_format",
    "area",
    "industry",
)
AI_SCHEMA = {
    "type": "object",
    "required": ["suitable", "confidence", "evidence", "reasons"],
    "properties": {
        "suitable": {"type": "boolean"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "evidence": {
            "type": "array",
            "items": {"type": "string", "maxLength": 300},
            "maxItems": 20,
        },
        "reasons": {
            "type": "array",
            "items": {"type": "string", "maxLength": 300},
            "maxItems": 20,
        },
    },
    "additionalProperties": False,
}
AI_OUTPUT_SCHEMA = StructuredOutputSchema(
    name="hh_autopilot_ranking_v1",
    schema=AI_SCHEMA,
    strict=True,
)

_ABSENT = object()
_SYSTEM_PROMPT = (
    "Judge job suitability using only supplied facts. "
    "Do not infer or invent candidate experience."
)
_PROMPT_LIMITS = {"light": 12_000, "heavy": 20_000}


class NoQualifyingCandidatesError(LookupError):
    pass


def _mapping(value: Any, *, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} must be a mapping")
    return copy.deepcopy(dict(value))


def _vacancy_mapping(
    value: NormalizedVacancy | Mapping[str, Any],
) -> dict[str, Any]:
    if isinstance(value, NormalizedVacancy):
        return {
            key: item
            for key, item in value.to_dict().items()
            if item is not None
            and item != ""
            and not (
                isinstance(item, (list, tuple))
                and not item
            )
        }
    return _mapping(value, field="vacancy")


def _text(
    value: Any,
    *,
    field: str,
    maximum: int = 8_000,
    allow_empty: bool = True,
) -> str:
    if type(value) is not str:
        raise TypeError(f"{field} must be a string")
    return sanitize_text(
        value,
        field=field,
        maximum=maximum,
        allow_empty=allow_empty,
        markup="strip",
        sensitive="redact",
        overflow="truncate",
    )


def _normalized(value: Any, *, field: str) -> str:
    return _text(value, field=field, allow_empty=False).casefold()


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
            raise ValueError(f"{field} aliases disagree")
    if set_like:
        return tuple(sorted(expected))
    return present[0]


def _string_set(
    source: Mapping[str, Any],
    keys: Sequence[str],
    *,
    field: str,
    mapping_key: str = "id",
) -> tuple[str, ...] | None:
    values: list[Any] = []
    for key in keys:
        if key not in source:
            values.append(_ABSENT)
            continue
        raw = source[key]
        if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
            raise TypeError(f"{field} must be a sequence")
        if not raw:
            raise ValueError(f"{field} must not be empty when present")
        if len(raw) > 500:
            raise ValueError(f"{field} has too many values")
        result: list[str] = []
        seen: set[str] = set()
        for index, item in enumerate(raw):
            if isinstance(item, Mapping):
                if mapping_key not in item:
                    raise TypeError(f"{field}[{index}] requires {mapping_key}")
                item = item[mapping_key]
            parsed = _normalized(item, field=f"{field}[{index}]")
            if parsed not in seen:
                seen.add(parsed)
                result.append(parsed)
        values.append(tuple(result))
    agreed = _agree_aliases(values, field=field, set_like=True)
    return None if agreed is _ABSENT else agreed


def _first_text(
    source: Mapping[str, Any],
    keys: Sequence[str],
    *,
    field: str,
) -> str | None:
    values = [
        (
            _normalized(source[key], field=field)
            if key in source
            else _ABSENT
        )
        for key in keys
    ]
    agreed = _agree_aliases(values, field=field)
    return None if agreed is _ABSENT else agreed


def _numeric(
    value: Any,
    *,
    field: str,
    minimum: float = 0.0,
) -> float:
    if type(value) not in {int, float}:
        raise TypeError(f"{field} must be numeric")
    parsed = float(value)
    if type(value) is int and int(parsed) != value:
        raise ValueError(f"{field} cannot be represented without loss")
    if not math.isfinite(parsed):
        raise ValueError(f"{field} must be finite")
    if parsed < minimum:
        raise ValueError(f"{field} must be at least {minimum}")
    return parsed


def _optional_numeric(
    source: Mapping[str, Any],
    keys: Sequence[str],
    *,
    field: str,
) -> float | None:
    agreed = _numeric_fact_from_keys(source, keys, field=field)
    if agreed is _ABSENT or agreed is None:
        return None
    if isinstance(agreed, bool) or not isinstance(agreed, (int, float)):
        raise TypeError(f"{field} must be numeric")
    return float(agreed)


def _numeric_fact_from_keys(
    source: Mapping[str, Any],
    keys: Sequence[str],
    *,
    field: str,
) -> float | None | object:
    values: list[Any] = []
    for key in keys:
        if key not in source:
            values.append(_ABSENT)
        elif source[key] is None:
            values.append(None)
        else:
            values.append(_numeric(source[key], field=field))
    agreed = _agree_aliases(values, field=field)
    return agreed


def _merge_optional(
    first: Any,
    second: Any,
    *,
    field: str,
) -> Any:
    agreed = _agree_aliases(
        (
            _ABSENT if first is _ABSENT else first,
            _ABSENT if second is _ABSENT else second,
        ),
        field=field,
    )
    return agreed


def _coverage(required: Sequence[str], observed: Sequence[str]) -> float:
    required_set = set(required)
    observed_set = set(observed)
    if not required_set or not observed_set:
        return 50.0
    return round(100.0 * len(required_set.intersection(observed_set)) / len(required_set), 4)


def _text_coverage(required: Sequence[str], facts: Sequence[str]) -> float:
    required_set = set(required)
    if not required_set or not facts:
        return 50.0
    return round(
        100.0
        * sum(
            any(phrase_matches_fact(term, fact) for fact in facts)
            for term in required_set
        )
        / len(required_set),
        4,
    )


class DeterministicRanker:
    def score(
        self,
        vacancy: NormalizedVacancy | Mapping[str, Any],
        resume: Mapping[str, Any],
        candidate: Mapping[str, Any],
        weights: Mapping[str, Any],
    ) -> RankScore:
        vacancy_data = _vacancy_mapping(vacancy)
        resume_data = _mapping(resume, field="resume")
        candidate_data = _mapping(candidate, field="candidate")
        normalized_weights = self._weights(weights)
        raw = {
            "role": self._role(vacancy_data, resume_data, candidate_data),
            "skills": self._skills(vacancy_data, resume_data, candidate_data),
            "experience": self._experience(
                vacancy_data,
                resume_data,
                candidate_data,
            ),
            "salary": self._salary(vacancy_data, candidate_data),
            "work_format": self._work_format(
                vacancy_data,
                resume_data,
                candidate_data,
            ),
            "area": self._area(vacancy_data, resume_data, candidate_data),
            "industry": self._industry(
                vacancy_data,
                resume_data,
                candidate_data,
            ),
        }
        for name, value in raw.items():
            if not math.isfinite(value) or not 0.0 <= value <= 100.0:
                raise ValueError(f"component {name} must be finite in 0..100")
        total = math.fsum(
            raw[name] * normalized_weights[name] for name in COMPONENTS
        )
        total = round(max(0.0, min(100.0, total)), 4)
        return RankScore(
            score=total,
            components=raw,
            weights=normalized_weights,
        )

    @staticmethod
    def _weights(weights: Mapping[str, Any]) -> dict[str, float]:
        if not isinstance(weights, Mapping):
            raise TypeError("weights must be a mapping")
        if set(weights) != set(COMPONENTS):
            raise ValueError("weights must contain exactly the fixed components")
        parsed = {
            name: _numeric(
                weights[name],
                field=f"weights.{name}",
            )
            for name in COMPONENTS
        }
        total = math.fsum(parsed.values())
        if not math.isfinite(total) or total <= 0:
            raise ValueError("weights must have a positive finite sum")
        normalized = {name: parsed[name] / total for name in COMPONENTS}
        correction = 1.0 - math.fsum(normalized.values())
        target = max(
            (name for name in COMPONENTS if normalized[name] > 0.0),
            key=lambda name: normalized[name],
        )
        normalized[target] += correction
        for name, value in normalized.items():
            if value == 0.0:
                normalized[name] = 0.0
        return normalized

    @staticmethod
    def _role(
        vacancy: Mapping[str, Any],
        resume: Mapping[str, Any],
        candidate: Mapping[str, Any],
    ) -> float:
        vacancy_ids = _string_set(
            vacancy,
            ("professional_role_ids", "role_family_ids", "role_families"),
            field="vacancy.professional_role_ids",
        )
        desired_ids = _string_set(
            resume,
            ("professional_role_ids", "professional_roles", "role_family_ids"),
            field="resume.professional_role_ids",
        )
        if desired_ids is None:
            desired_ids = _string_set(
                candidate,
                ("professional_role_ids", "professional_roles", "role_family_ids"),
                field="candidate.professional_role_ids",
            )
        if vacancy_ids is not None and desired_ids is not None:
            return _coverage(vacancy_ids, desired_ids)
        desired_text = _string_set(
            candidate,
            ("desired_roles",),
            field="candidate.desired_roles",
            mapping_key="name",
        )
        if desired_text is None:
            desired_text = _string_set(
                resume,
                ("desired_roles",),
                field="resume.desired_roles",
                mapping_key="name",
            )
        title = _first_text(vacancy, ("title", "name"), field="vacancy.title")
        if desired_text is None or title is None:
            return 50.0
        return _text_coverage(desired_text, (title,))

    @staticmethod
    def _skills(
        vacancy: Mapping[str, Any],
        resume: Mapping[str, Any],
        candidate: Mapping[str, Any],
    ) -> float:
        desired = _string_set(
            candidate,
            ("must_have_skills", "skills", "all_skills"),
            field="candidate.skills",
            mapping_key="name",
        )
        if desired is None:
            desired = _string_set(
                resume,
                ("skills", "key_skills", "all_skills"),
                field="resume.skills",
                mapping_key="name",
            )
        vacancy_skills = _string_set(
            vacancy,
            ("key_skills", "skills"),
            field="vacancy.key_skills",
            mapping_key="name",
        )
        if desired is None:
            return 50.0
        if vacancy_skills is not None:
            return _coverage(vacancy_skills, desired)
        facts = tuple(
            value
            for value in (
                _first_text(vacancy, ("title", "name"), field="vacancy.title"),
                _first_text(
                    vacancy,
                    ("description",),
                    field="vacancy.description",
                ),
            )
            if value
        )
        if not facts:
            return 50.0
        return _text_coverage(desired, facts)

    @staticmethod
    def _experience(
        vacancy: Mapping[str, Any],
        resume: Mapping[str, Any],
        candidate: Mapping[str, Any],
    ) -> float:
        top_vacancy_level = _first_text(
            vacancy,
            ("experience_id",),
            field="vacancy.experience_id",
        )
        nested_vacancy_level: str | None = None
        if "experience" in vacancy:
            nested = _mapping(
                vacancy["experience"],
                field="vacancy.experience",
            )
            nested_vacancy_level = _first_text(
                nested,
                ("id",),
                field="vacancy.experience.id",
            )
        vacancy_level_value = _agree_aliases(
            (
                _ABSENT if top_vacancy_level is None else top_vacancy_level,
                (
                    _ABSENT
                    if nested_vacancy_level is None
                    else nested_vacancy_level
                ),
            ),
            field="vacancy.experience_id",
        )
        vacancy_level = (
            None
            if vacancy_level_value is _ABSENT
            else vacancy_level_value
        )
        accepted = _string_set(
            candidate,
            ("experience_levels",),
            field="candidate.experience_levels",
        )
        if accepted is None:
            accepted = _string_set(
                resume,
                ("experience_levels",),
                field="resume.experience_levels",
            )
        if accepted is None:
            single = _first_text(
                resume,
                ("experience_level_id", "experience_id"),
                field="resume.experience_level_id",
            )
            if single is not None:
                accepted = (single,)
        if vacancy_level is None or accepted is None:
            return 50.0
        return 100.0 if vacancy_level in accepted else 0.0

    @staticmethod
    def _salary(
        vacancy: Mapping[str, Any],
        candidate: Mapping[str, Any],
    ) -> float:
        top_salary_from = _numeric_fact_from_keys(
            vacancy,
            ("salary_from",),
            field="vacancy.salary_from",
        )
        top_salary_to = _numeric_fact_from_keys(
            vacancy,
            ("salary_to",),
            field="vacancy.salary_to",
        )
        salary_mapping: Mapping[str, Any] = {}
        nested_salary_from: float | None | object = _ABSENT
        nested_salary_to: float | None | object = _ABSENT
        if "salary" in vacancy:
            salary_mapping = _mapping(vacancy["salary"], field="vacancy.salary")
            nested_salary_from = _numeric_fact_from_keys(
                salary_mapping,
                ("from",),
                field="vacancy.salary.from",
            )
            nested_salary_to = _numeric_fact_from_keys(
                salary_mapping,
                ("to",),
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
            if salary_from_value in {_ABSENT, None}
            else salary_from_value
        )
        salary_to = (
            None if salary_to_value in {_ABSENT, None} else salary_to_value
        )
        if (
            salary_from is not None
            and salary_to is not None
            and salary_from > salary_to
        ):
            raise ValueError("vacancy.salary range is inverted")
        top_vacancy_currency = _first_text(
            vacancy,
            ("salary_currency", "currency"),
            field="vacancy.salary_currency",
        )
        nested_vacancy_currency: str | None = None
        if salary_mapping:
            nested_vacancy_currency = _first_text(
                salary_mapping,
                ("currency",),
                field="vacancy.salary.currency",
            )
        vacancy_currency_value = _agree_aliases(
            (
                (
                    _ABSENT
                    if top_vacancy_currency is None
                    else top_vacancy_currency
                ),
                (
                    _ABSENT
                    if nested_vacancy_currency is None
                    else nested_vacancy_currency
                ),
            ),
            field="vacancy.salary_currency",
        )
        vacancy_currency = (
            None
            if vacancy_currency_value is _ABSENT
            else vacancy_currency_value
        )
        minimum = _optional_numeric(
            candidate,
            ("salary_min", "minimum_salary"),
            field="candidate.salary_min",
        )
        desired_currency = _first_text(
            candidate,
            ("salary_currency", "currency"),
            field="candidate.salary_currency",
        )
        if minimum is None or minimum <= 0:
            return 50.0
        best = salary_to if salary_to is not None else salary_from
        if best is None:
            return 50.0
        if vacancy_currency is None or desired_currency is None:
            return 50.0
        if vacancy_currency != desired_currency:
            return 0.0
        return round(min(100.0, 100.0 * best / minimum), 4)

    @staticmethod
    def _work_format(
        vacancy: Mapping[str, Any],
        resume: Mapping[str, Any],
        candidate: Mapping[str, Any],
    ) -> float:
        vacancy_formats = _string_set(
            vacancy,
            ("work_format_ids", "work_formats", "work_format"),
            field="vacancy.work_format_ids",
        )
        top_schedule = _first_text(
            vacancy,
            ("schedule_id",),
            field="vacancy.schedule_id",
        )
        nested_schedule: str | None = None
        if "schedule" in vacancy:
            schedule_mapping = _mapping(
                vacancy["schedule"],
                field="vacancy.schedule",
            )
            nested_schedule = _first_text(
                schedule_mapping,
                ("id",),
                field="vacancy.schedule.id",
            )
        schedule_value = _agree_aliases(
            (
                _ABSENT if top_schedule is None else top_schedule,
                _ABSENT if nested_schedule is None else nested_schedule,
            ),
            field="vacancy.schedule_id",
        )
        schedule = None if schedule_value is _ABSENT else schedule_value
        if vacancy_formats is None and schedule is not None:
            vacancy_formats = (schedule,)
        remote_only: bool | None = None
        if "remote_only" in candidate:
            if type(candidate["remote_only"]) is not bool:
                raise TypeError("candidate.remote_only must be a boolean")
            remote_only = candidate["remote_only"]
        if remote_only is True:
            if vacancy_formats is None:
                return 50.0
            return 100.0 if "remote" in vacancy_formats else 0.0
        preferred = _string_set(
            candidate,
            ("work_format_ids", "preferred_work_formats"),
            field="candidate.work_format_ids",
        )
        if preferred is None:
            preferred = _string_set(
                resume,
                ("work_format_ids", "preferred_work_formats"),
                field="resume.work_format_ids",
            )
        if vacancy_formats is None or preferred is None:
            return 50.0
        return 100.0 if set(vacancy_formats).intersection(preferred) else 0.0

    @staticmethod
    def _area(
        vacancy: Mapping[str, Any],
        resume: Mapping[str, Any],
        candidate: Mapping[str, Any],
    ) -> float:
        top_area = _first_text(vacancy, ("area_id",), field="vacancy.area_id")
        nested_area: str | None = None
        if "area" in vacancy:
            area_mapping = _mapping(vacancy["area"], field="vacancy.area")
            nested_area = _first_text(
                area_mapping,
                ("id",),
                field="vacancy.area.id",
            )
        area_value = _agree_aliases(
            (
                _ABSENT if top_area is None else top_area,
                _ABSENT if nested_area is None else nested_area,
            ),
            field="vacancy.area_id",
        )
        area = None if area_value is _ABSENT else area_value

        def desired_areas(
            source: Mapping[str, Any],
            *,
            prefix: str,
        ) -> tuple[str, ...] | None:
            multiple = _string_set(
                source,
                ("area_ids", "areas"),
                field=f"{prefix}.area_ids",
            )
            single = _first_text(
                source,
                ("area_id",),
                field=f"{prefix}.area_id",
            )
            if (
                multiple is not None
                and single is not None
                and single not in multiple
            ):
                raise ValueError(f"{prefix} area aliases disagree")
            if multiple is not None:
                return multiple
            return None if single is None else (single,)

        candidate_areas = desired_areas(candidate, prefix="candidate")
        resume_areas = desired_areas(resume, prefix="resume")
        desired = (
            candidate_areas
            if candidate_areas is not None
            else resume_areas
        )

        relocation_values: list[Any] = []
        for source, prefix in ((candidate, "candidate"), (resume, "resume")):
            aliases: list[Any] = []
            for key in ("relocation_allowed", "allow_relocation"):
                if key not in source:
                    aliases.append(_ABSENT)
                    continue
                flag = source[key]
                if type(flag) is not bool:
                    raise TypeError(
                        f"{prefix}.{key} must be a boolean"
                    )
                aliases.append(flag)
            relocation_values.append(
                _agree_aliases(
                    aliases,
                    field=f"{prefix}.relocation_allowed",
                )
            )
        relocation_value = _agree_aliases(
            relocation_values,
            field="relocation_allowed",
        )
        if area is None or desired is None:
            return 50.0
        if area in desired:
            return 100.0
        return 100.0 if relocation_value is True else 0.0

    @staticmethod
    def _industry(
        vacancy: Mapping[str, Any],
        resume: Mapping[str, Any],
        candidate: Mapping[str, Any],
    ) -> float:
        vacancy_industries = _string_set(
            vacancy,
            ("industry_ids", "industries", "industry"),
            field="vacancy.industry_ids",
        )
        desired = _string_set(
            candidate,
            ("industry_ids", "industries"),
            field="candidate.industry_ids",
        )
        if desired is None:
            desired = _string_set(
                resume,
                ("industry_ids", "industries"),
                field="resume.industry_ids",
            )
        if vacancy_industries is None or desired is None:
            return 50.0
        return _coverage(vacancy_industries, desired)


class StructuredAIRanker:
    def __init__(
        self,
        ai_config: Mapping[str, Any],
        *,
        structured_call: Callable[..., Any] = send_structured_chat,
        completion: Any = None,
    ) -> None:
        self._ai_config = _mapping(ai_config, field="ai_config")
        if not callable(structured_call):
            raise TypeError("structured_call must be callable")
        self._structured_call = structured_call
        self._completion = completion

    def evaluate(
        self,
        vacancy: NormalizedVacancy | Mapping[str, Any],
        resume: Mapping[str, Any],
        candidate: Mapping[str, Any],
        *,
        detail: str,
    ) -> AIDecision:
        try:
            if detail not in {"light", "heavy"}:
                raise ValueError("detail must be light or heavy")
            payload = self._payload(
                _vacancy_mapping(vacancy),
                _mapping(resume, field="resume"),
                _mapping(candidate, field="candidate"),
                detail=detail,
            )
            payload = self._fit_prompt_payload(
                payload,
                maximum=_PROMPT_LIMITS[detail] - len(_SYSTEM_PROMPT),
            )
            user_content = json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            prompt_characters = len(_SYSTEM_PROMPT) + len(user_content)
            prompt_bytes = len(_SYSTEM_PROMPT.encode("utf-8")) + len(
                user_content.encode("utf-8")
            )
            if max(prompt_characters, prompt_bytes) > _PROMPT_LIMITS[detail]:
                raise ValueError("structured AI prompt exceeds its global budget")
            kwargs: dict[str, Any] = {"max_retries": 1}
            if self._completion is not None:
                kwargs["completion"] = self._completion
            reply = self._structured_call(
                [
                    {
                        "role": "system",
                        "content": _SYSTEM_PROMPT,
                    },
                    {
                        "role": "user",
                        "content": user_content,
                    },
                ],
                copy.deepcopy(self._ai_config),
                AI_OUTPUT_SCHEMA,
                **kwargs,
            )
            parsed = reply.parsed if hasattr(reply, "parsed") else reply
            return self._decision(parsed, payload=payload)
        except Exception:
            return AIDecision(
                available=False,
                suitable=None,
                confidence=None,
                evidence=(),
                reasons=(),
                reason="ai_unavailable",
            )

    @classmethod
    def _payload(
        cls,
        vacancy: Mapping[str, Any],
        resume: Mapping[str, Any],
        candidate: Mapping[str, Any],
        *,
        detail: str,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "detail": detail,
            "vacancy": {
                "title": cls._safe_scalar(vacancy, ("title", "name"), 300),
                "professional_role_ids": cls._safe_list(
                    vacancy,
                    ("professional_role_ids", "role_family_ids"),
                    20,
                ),
                "key_skills": cls._safe_list(
                    vacancy,
                    ("key_skills", "skills"),
                    30,
                    mapping_key="name",
                ),
                "description": cls._safe_scalar(
                    vacancy,
                    ("description",),
                    2_000 if detail == "light" else 8_000,
                ),
                "experience_id": cls._safe_scalar(
                    vacancy,
                    ("experience_id",),
                    100,
                ),
                "area_id": cls._safe_scalar(vacancy, ("area_id",), 100),
                "work_format_ids": cls._safe_list(
                    vacancy,
                    ("work_format_ids",),
                    20,
                ),
            },
            "resume": {
                "title": cls._safe_scalar(resume, ("title",), 300),
                "professional_role_ids": cls._safe_list(
                    resume,
                    ("professional_role_ids", "professional_roles"),
                    20,
                ),
                "skills": cls._safe_list(
                    resume,
                    ("skills", "key_skills", "all_skills"),
                    50 if detail == "light" else 100,
                    mapping_key="name",
                ),
            },
            "candidate_preferences": {
                "desired_roles": cls._safe_list(
                    candidate,
                    ("desired_roles",),
                    20,
                    mapping_key="name",
                ),
                "must_have_skills": cls._safe_list(
                    candidate,
                    ("must_have_skills",),
                    50,
                    mapping_key="name",
                ),
                "area_ids": cls._safe_list(
                    candidate,
                    ("area_ids", "areas"),
                    20,
                ),
                "experience_levels": cls._safe_list(
                    candidate,
                    ("experience_levels",),
                    20,
                ),
                "remote_only": (
                    cls._safe_optional_bool(candidate, "remote_only")
                ),
            },
        }
        if detail == "heavy":
            payload["resume"]["experience"] = cls._safe_blocks(
                resume.get("experience"),
                maximum_blocks=10,
            )
            payload["resume"]["education"] = cls._safe_blocks(
                resume.get("education"),
                maximum_blocks=10,
            )
        return payload

    @staticmethod
    def _safe_prompt_text(
        value: Any,
        *,
        field: str,
        maximum: int,
    ) -> str:
        if type(value) is not str:
            raise TypeError(f"{field} must be a string")
        return sanitize_text(
            value,
            field=field,
            maximum=maximum,
            allow_empty=True,
            markup="strip",
            sensitive="reject",
            overflow="truncate",
        )

    @staticmethod
    def _safe_scalar(
        source: Mapping[str, Any],
        keys: Sequence[str],
        maximum: int,
    ) -> str:
        values: list[Any] = []
        for key in keys:
            if key not in source:
                values.append(_ABSENT)
                continue
            if type(source[key]) is not str:
                raise TypeError(f"{key} must be a string")
            values.append(
                StructuredAIRanker._safe_prompt_text(
                    source[key],
                    field=key,
                    maximum=maximum,
                )
            )
        agreed = _agree_aliases(values, field=keys[0])
        return "" if agreed is _ABSENT else agreed

    @staticmethod
    def _safe_optional_bool(source: Mapping[str, Any], key: str) -> bool | None:
        if key not in source:
            return None
        value = source[key]
        if type(value) is not bool:
            raise TypeError(f"{key} must be a boolean")
        return value

    @staticmethod
    def _safe_list(
        source: Mapping[str, Any],
        keys: Sequence[str],
        maximum: int,
        *,
        mapping_key: str = "id",
    ) -> list[str]:
        aliases: list[Any] = []
        for key in keys:
            if key not in source:
                aliases.append(_ABSENT)
                continue
            raw = source[key]
            if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
                raise TypeError(f"{keys[0]} must be a sequence")
            if not raw:
                raise ValueError(f"{keys[0]} must not be empty when present")
            if len(raw) > 500:
                raise ValueError(f"{keys[0]} has too many values")
            parsed_values: list[str] = []
            seen: set[str] = set()
            for index, item in enumerate(raw):
                if isinstance(item, Mapping):
                    if mapping_key not in item:
                        raise TypeError(
                            f"{keys[0]}[{index}] requires {mapping_key}"
                        )
                    item = item[mapping_key]
                parsed = StructuredAIRanker._safe_prompt_text(
                    item,
                    field=f"{keys[0]}[{index}]",
                    maximum=100,
                )
                if not parsed:
                    raise ValueError(f"{keys[0]}[{index}] must not be empty")
                canonical = parsed.casefold()
                if canonical not in seen:
                    seen.add(canonical)
                    parsed_values.append(canonical)
            aliases.append(tuple(parsed_values))
        values = _agree_aliases(aliases, field=keys[0], set_like=True)
        if values is _ABSENT:
            return []
        return list(values[:maximum])

    @staticmethod
    def _safe_blocks(value: Any, *, maximum_blocks: int) -> list[dict[str, str]]:
        if value is None:
            return []
        if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
            raise TypeError("structured AI blocks must be a sequence")
        allowed = ("role", "position", "company", "project", "summary", "details")
        blocks: list[dict[str, str]] = []
        if len(value) > maximum_blocks:
            value = value[:maximum_blocks]
        for index, raw in enumerate(value):
            if not isinstance(raw, Mapping):
                raise TypeError(f"structured AI block {index} must be a mapping")
            block: dict[str, str] = {}
            for key in allowed:
                if key not in raw:
                    continue
                item = raw.get(key)
                if type(item) is str:
                    block[key] = StructuredAIRanker._safe_prompt_text(
                        item,
                        field=f"block.{key}",
                        maximum=500,
                    )
                elif isinstance(item, Sequence) and not isinstance(item, (str, bytes)):
                    if len(item) > 10:
                        item = item[:10]
                    strings: list[str] = []
                    for entry_index, entry in enumerate(item):
                        if type(entry) is not str:
                            raise TypeError(
                                f"block.{key}[{entry_index}] must be a string"
                            )
                        strings.append(
                            StructuredAIRanker._safe_prompt_text(
                                entry,
                                field=f"block.{key}",
                                maximum=200,
                            )
                        )
                    block[key] = " | ".join(strings)[:1_000]
                elif item is not None:
                    raise TypeError(f"block.{key} must be text or a text sequence")
            if block:
                blocks.append(block)
        return blocks

    @classmethod
    def _fit_prompt_payload(
        cls,
        payload: Mapping[str, Any],
        *,
        maximum: int,
    ) -> dict[str, Any]:
        fitted = copy.deepcopy(dict(payload))

        def dump() -> str:
            return json.dumps(
                fitted,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )

        def size(value: str) -> int:
            return max(len(value), len(value.encode("utf-8")))

        def string_leaves(
            value: Any,
            path: tuple[str | int, ...] = (),
        ) -> list[tuple[tuple[str | int, ...], str]]:
            leaves: list[tuple[tuple[str | int, ...], str]] = []
            if type(value) is str:
                leaves.append((path, value))
            elif isinstance(value, Mapping):
                for key in sorted(value):
                    leaves.extend(string_leaves(value[key], (*path, key)))
            elif isinstance(value, list):
                for index, item in enumerate(value):
                    leaves.extend(string_leaves(item, (*path, index)))
            return leaves

        def replace(path: tuple[str | int, ...], value: str) -> None:
            target: Any = fitted
            for part in path[:-1]:
                target = target[part]
            target[path[-1]] = value

        rendered = dump()
        while size(rendered) > maximum:
            leaves = [
                leaf
                for leaf in string_leaves(fitted)
                if leaf[0] != ("detail",) and leaf[1]
            ]
            if not leaves:
                raise ValueError("structured AI payload cannot fit its budget")
            leaves.sort(
                key=lambda leaf: (
                    -len(leaf[1]),
                    tuple(str(part) for part in leaf[0]),
                )
            )
            path, text = leaves[0]
            excess = size(rendered) - maximum
            remove = max(excess, max(1, len(text) // 4))
            replace(path, text[: max(0, len(text) - remove)].rstrip())
            rendered = dump()
        return fitted

    @staticmethod
    def _safe_ai_output(value: Any, *, field: str) -> str:
        if type(value) is not str:
            raise TypeError(f"AI {field} must be a string")
        return sanitize_text(
            value,
            field=f"AI {field}",
            maximum=300,
            allow_empty=True,
            markup="reject",
            sensitive="reject",
        )

    @staticmethod
    def _grounding_values(value: Any) -> tuple[str, ...]:
        grounded: list[str] = []
        if type(value) is str:
            parsed = _text(
                value,
                field="AI grounding fact",
                maximum=20_000,
            ).casefold()
            if parsed and parsed != "redacted":
                grounded.append(parsed)
        elif isinstance(value, Mapping):
            for key, item in value.items():
                if key == "detail":
                    continue
                grounded.extend(StructuredAIRanker._grounding_values(item))
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            for item in value:
                grounded.extend(StructuredAIRanker._grounding_values(item))
        return tuple(grounded)

    @classmethod
    def _decision(
        cls,
        value: Any,
        *,
        payload: Mapping[str, Any],
    ) -> AIDecision:
        if not isinstance(value, Mapping):
            raise TypeError("AI response must be a mapping")
        required = {"suitable", "confidence", "evidence", "reasons"}
        if set(value) != required:
            raise ValueError("AI response keys do not match schema")
        if type(value["suitable"]) is not bool:
            raise TypeError("AI suitable must be a boolean")
        confidence = _numeric(
            value["confidence"],
            field="AI confidence",
        )
        if confidence > 1:
            raise ValueError("AI confidence must be in 0..1")
        for field in ("evidence", "reasons"):
            items = value[field]
            if not isinstance(items, list):
                raise TypeError(f"AI {field} must be a list")
            if len(items) > 20:
                raise ValueError(f"AI {field} has too many values")
        evidence = tuple(
            cls._safe_ai_output(item, field="evidence")
            for item in value["evidence"]
        )
        reasons = tuple(
            cls._safe_ai_output(item, field="reasons")
            for item in value["reasons"]
        )
        grounding = cls._grounding_values(payload)
        for field, items in (("evidence", evidence), ("reasons", reasons)):
            for item in items:
                if item and not any(
                    phrase_matches_fact(item, fact)
                    for fact in grounding
                ):
                    raise ValueError(
                        f"AI {field} is not grounded in supplied facts"
                    )
        return AIDecision(
            available=True,
            suitable=value["suitable"],
            confidence=confidence,
            evidence=evidence,
            reasons=reasons,
            reason="available",
        )


class RankingPolicy:
    def __init__(
        self,
        config: Mapping[str, Any],
        ai_ranker: Any,
    ) -> None:
        self._config = _mapping(config, field="ranking config")
        if not hasattr(ai_ranker, "evaluate") or not callable(ai_ranker.evaluate):
            raise TypeError("ai_ranker must provide evaluate")
        self._ai_ranker = ai_ranker
        self._validate_config()

    def _validate_config(self) -> None:
        for field in (
            "minimum_score",
            "borderline_low",
            "borderline_high",
            "minimum_ai_confidence",
        ):
            if field not in self._config:
                raise ValueError(f"ranking config requires {field}")
            value = _numeric(self._config[field], field=field)
            maximum = 1.0 if field == "minimum_ai_confidence" else 100.0
            if value > maximum:
                raise ValueError(f"{field} is out of range")
            self._config[field] = value
        if not (
            self._config["borderline_low"]
            <= self._config["minimum_score"]
            <= self._config["borderline_high"]
        ):
            raise ValueError("borderline thresholds do not contain minimum_score")
        if self._config.get("ai_mode") not in {"off", "borderline", "all"}:
            raise ValueError("invalid ai_mode")
        if self._config.get("ai_detail") not in {"light", "heavy"}:
            raise ValueError("invalid ai_detail")
        if self._config.get("ai_failure_policy") not in {
            "retry",
            "deterministic",
            "skip",
        }:
            raise ValueError("invalid ai_failure_policy")

    def decide(
        self,
        filter_decision: FilterDecision,
        rank_score: RankScore,
        vacancy: NormalizedVacancy | Mapping[str, Any],
        resume: Mapping[str, Any],
        candidate: Mapping[str, Any],
    ) -> RankingDecision:
        if not isinstance(filter_decision, FilterDecision):
            raise TypeError("filter_decision must be a FilterDecision")
        if not isinstance(rank_score, RankScore):
            raise TypeError("rank_score must be a RankScore")
        if not filter_decision.passed:
            return RankingDecision(
                ready=False,
                retry=False,
                reason=filter_decision.reason,
                rank_score=rank_score,
                ai_decision=None,
            )
        mode = self._config["ai_mode"]
        score = rank_score.score
        if mode == "off":
            return self._deterministic(rank_score)
        if mode == "borderline":
            if score < self._config["borderline_low"]:
                return RankingDecision(
                    ready=False,
                    retry=False,
                    reason="deterministic_below_minimum",
                    rank_score=rank_score,
                    ai_decision=None,
                )
            if score > self._config["borderline_high"]:
                return RankingDecision(
                    ready=True,
                    retry=False,
                    reason="deterministic_score",
                    rank_score=rank_score,
                    ai_decision=None,
                )
        try:
            ai = self._ai_ranker.evaluate(
                _vacancy_mapping(vacancy),
                _mapping(resume, field="resume"),
                _mapping(candidate, field="candidate"),
                detail=self._config["ai_detail"],
            )
        except Exception:
            ai = AIDecision(
                available=False,
                suitable=None,
                confidence=None,
                evidence=(),
                reasons=(),
                reason="ai_unavailable",
            )
        if not isinstance(ai, AIDecision):
            ai = AIDecision(
                available=False,
                suitable=None,
                confidence=None,
                evidence=(),
                reasons=(),
                reason="ai_unavailable",
            )
        confident = (
            ai.available
            and ai.confidence is not None
            and ai.confidence >= self._config["minimum_ai_confidence"]
        )
        if confident:
            return RankingDecision(
                ready=ai.suitable is True,
                retry=False,
                reason="ai_suitable" if ai.suitable else "ai_unsuitable",
                rank_score=rank_score,
                ai_decision=ai,
            )
        unavailable_ai = AIDecision(
            available=False,
            suitable=None,
            confidence=None,
            evidence=(),
            reasons=(),
            reason="ai_unavailable",
        )
        failure_policy = self._config["ai_failure_policy"]
        if failure_policy == "retry":
            return RankingDecision(
                ready=False,
                retry=True,
                reason="ai_unavailable",
                rank_score=rank_score,
                ai_decision=unavailable_ai,
            )
        if failure_policy == "deterministic":
            return self._deterministic_fallback(
                rank_score,
                unavailable_ai,
            )
        return RankingDecision(
            ready=False,
            retry=False,
            reason="ai_unavailable",
            rank_score=rank_score,
            ai_decision=unavailable_ai,
        )

    def _deterministic(
        self,
        rank_score: RankScore,
    ) -> RankingDecision:
        ready = rank_score.score >= self._config["minimum_score"]
        return RankingDecision(
            ready=ready,
            retry=False,
            reason="deterministic_score" if ready else "deterministic_below_minimum",
            rank_score=rank_score,
            ai_decision=None,
        )

    def _deterministic_fallback(
        self,
        rank_score: RankScore,
        unavailable_ai: AIDecision,
    ) -> RankingDecision:
        if unavailable_ai.available:
            raise ValueError("deterministic fallback requires unavailable AI")
        return RankingDecision(
            ready=rank_score.score >= self._config["minimum_score"],
            retry=False,
            reason="deterministic_fallback",
            rank_score=rank_score,
            ai_decision=unavailable_ai,
        )


def _publication_timestamp(value: str) -> float:
    if not value:
        return float("-inf")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, OverflowError, OSError):
        return float("-inf")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return float("-inf")
    return parsed.astimezone(timezone.utc).timestamp()


def select_resume(
    candidates: Sequence[RankedCandidate],
    resume_policy: str,
) -> RankedCandidate | tuple[RankedCandidate, ...]:
    if isinstance(candidates, (str, bytes)) or not isinstance(candidates, Sequence):
        raise TypeError("candidates must be a sequence")
    if resume_policy not in {"best_resume_only", "per_resume"}:
        raise ValueError("resume_policy must be best_resume_only or per_resume")
    identities: set[tuple[str, str, str]] = set()
    detached: list[RankedCandidate] = []
    for candidate in candidates:
        if not isinstance(candidate, RankedCandidate):
            raise TypeError("candidates must contain RankedCandidate values")
        identity = (
            candidate.account_id,
            candidate.vacancy_id,
            candidate.resume_id,
        )
        if identity in identities:
            raise ValueError("duplicate ranked candidate identity")
        identities.add(identity)
        if candidate.decision.ready:
            detached.append(candidate)
    if not detached:
        raise NoQualifyingCandidatesError("no qualifying ranked candidates")
    detached.sort(
        key=lambda candidate: (
            -candidate.decision.score,
            -(
                candidate.decision.ai_confidence
                if candidate.decision.ai_confidence is not None
                else -1.0
            ),
            -_publication_timestamp(candidate.published_at),
            candidate.vacancy_id,
            candidate.resume_id,
        )
    )
    if resume_policy == "best_resume_only":
        return detached[0]
    return tuple(detached)


__all__ = [
    "AI_SCHEMA",
    "COMPONENTS",
    "DeterministicRanker",
    "NoQualifyingCandidatesError",
    "RankingPolicy",
    "StructuredAIRanker",
    "select_resume",
]
