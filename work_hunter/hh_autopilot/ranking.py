from __future__ import annotations

import copy
import html
import json
import math
import re
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

from work_hunter.llm.structured import (
    StructuredOutputSchema,
    send_structured_chat,
)

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

_MARKUP = re.compile(r"<[^>]*>")
_SPACES = re.compile(r"\s+")


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
        return value.to_dict()
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
    cleaned = _SPACES.sub(
        " ",
        _MARKUP.sub(" ", html.unescape(value)),
    ).strip()
    if "\0" in cleaned:
        raise ValueError(f"{field} must not contain NUL")
    if not cleaned and not allow_empty:
        raise ValueError(f"{field} must not be empty")
    return cleaned[:maximum]


def _normalized(value: Any, *, field: str) -> str:
    return _text(value, field=field).casefold()


def _string_set(
    source: Mapping[str, Any],
    keys: Sequence[str],
    *,
    field: str,
    mapping_key: str = "id",
) -> tuple[str, ...] | None:
    raw: Any = None
    present = False
    for key in keys:
        if key in source:
            raw = source[key]
            present = True
            break
    if not present:
        return None
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        raise TypeError(f"{field} must be a sequence")
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
        if parsed and parsed not in seen:
            seen.add(parsed)
            result.append(parsed)
    return tuple(result)


def _first_text(
    source: Mapping[str, Any],
    keys: Sequence[str],
    *,
    field: str,
) -> str | None:
    for key in keys:
        if key in source:
            return _normalized(source[key], field=field)
    return None


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
    for key in keys:
        if key in source:
            if source[key] is None:
                return None
            return _numeric(source[key], field=field)
    return None


def _coverage(required: Sequence[str], observed: Sequence[str]) -> float:
    required_set = set(required)
    observed_set = set(observed)
    if not required_set or not observed_set:
        return 50.0
    return round(100.0 * len(required_set.intersection(observed_set)) / len(required_set), 4)


def _text_coverage(required: Sequence[str], text: str) -> float:
    required_set = set(required)
    if not required_set or not text:
        return 50.0
    return round(
        100.0 * sum(term in text for term in required_set) / len(required_set),
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
        total = sum(raw[name] * normalized_weights[name] for name in COMPONENTS)
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
        total = sum(parsed.values())
        if not math.isfinite(total) or total <= 0:
            raise ValueError("weights must have a positive finite sum")
        normalized = {name: parsed[name] / total for name in COMPONENTS}
        correction = 1.0 - sum(normalized.values())
        normalized[COMPONENTS[-1]] += correction
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
            return _coverage(desired_ids, vacancy_ids)
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
        return _text_coverage(desired_text, title)

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
        if vacancy_skills:
            return _coverage(desired, vacancy_skills)
        text = " ".join(
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
        if not text:
            return 50.0
        return _text_coverage(desired, text)

    @staticmethod
    def _experience(
        vacancy: Mapping[str, Any],
        resume: Mapping[str, Any],
        candidate: Mapping[str, Any],
    ) -> float:
        vacancy_level = _first_text(
            vacancy,
            ("experience_id",),
            field="vacancy.experience_id",
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
        salary_from = _optional_numeric(
            vacancy,
            ("salary_from",),
            field="vacancy.salary_from",
        )
        salary_to = _optional_numeric(
            vacancy,
            ("salary_to",),
            field="vacancy.salary_to",
        )
        salary_mapping: Mapping[str, Any] = {}
        if "salary" in vacancy:
            salary_mapping = _mapping(vacancy["salary"], field="vacancy.salary")
            if salary_from is None:
                salary_from = _optional_numeric(
                    salary_mapping,
                    ("from",),
                    field="vacancy.salary.from",
                )
            if salary_to is None:
                salary_to = _optional_numeric(
                    salary_mapping,
                    ("to",),
                    field="vacancy.salary.to",
                )
        minimum = _optional_numeric(
            candidate,
            ("salary_min", "minimum_salary"),
            field="candidate.salary_min",
        )
        if minimum is None or minimum <= 0:
            return 50.0
        best = salary_to if salary_to is not None else salary_from
        if best is None:
            return 50.0
        vacancy_currency = _first_text(
            vacancy,
            ("salary_currency", "currency"),
            field="vacancy.salary_currency",
        )
        if vacancy_currency is None and salary_mapping:
            vacancy_currency = _first_text(
                salary_mapping,
                ("currency",),
                field="vacancy.salary.currency",
            )
        desired_currency = _first_text(
            candidate,
            ("salary_currency", "currency"),
            field="candidate.salary_currency",
        )
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
        schedule = _first_text(
            vacancy,
            ("schedule_id",),
            field="vacancy.schedule_id",
        )
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
        area = _first_text(vacancy, ("area_id",), field="vacancy.area_id")
        desired = _string_set(
            candidate,
            ("area_ids", "areas"),
            field="candidate.area_ids",
        )
        if desired is None:
            single = _first_text(
                candidate,
                ("area_id",),
                field="candidate.area_id",
            )
            if single is None:
                single = _first_text(
                    resume,
                    ("area_id",),
                    field="resume.area_id",
                )
            if single is not None:
                desired = (single,)
        if area is None or desired is None:
            return 50.0
        if area in desired:
            return 100.0
        for source, prefix in ((candidate, "candidate"), (resume, "resume")):
            if "relocation_allowed" in source:
                flag = source["relocation_allowed"]
                if type(flag) is not bool:
                    raise TypeError(f"{prefix}.relocation_allowed must be a boolean")
                if flag:
                    return 100.0
        return 0.0

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
        return _coverage(desired, vacancy_industries)


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
            kwargs: dict[str, Any] = {"max_retries": 1}
            if self._completion is not None:
                kwargs["completion"] = self._completion
            reply = self._structured_call(
                [
                    {
                        "role": "system",
                        "content": (
                            "Judge job suitability using only supplied facts. "
                            "Do not infer or invent candidate experience."
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            payload,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    },
                ],
                copy.deepcopy(self._ai_config),
                AI_OUTPUT_SCHEMA,
                **kwargs,
            )
            parsed = reply.parsed if hasattr(reply, "parsed") else reply
            return self._decision(parsed)
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
                    candidate["remote_only"]
                    if type(candidate.get("remote_only")) is bool
                    else None
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
    def _safe_scalar(
        source: Mapping[str, Any],
        keys: Sequence[str],
        maximum: int,
    ) -> str:
        for key in keys:
            if key in source and type(source[key]) is str:
                return _text(
                    source[key],
                    field=key,
                    maximum=maximum,
                )
        return ""

    @staticmethod
    def _safe_list(
        source: Mapping[str, Any],
        keys: Sequence[str],
        maximum: int,
        *,
        mapping_key: str = "id",
    ) -> list[str]:
        values = _string_set(
            source,
            keys,
            field=keys[0],
            mapping_key=mapping_key,
        )
        if values is None:
            return []
        return [value[:100] for value in values[:maximum]]

    @staticmethod
    def _safe_blocks(value: Any, *, maximum_blocks: int) -> list[dict[str, str]]:
        if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
            return []
        allowed = ("role", "position", "company", "project", "summary", "details")
        blocks: list[dict[str, str]] = []
        for raw in value[:maximum_blocks]:
            if not isinstance(raw, Mapping):
                continue
            block: dict[str, str] = {}
            for key in allowed:
                item = raw.get(key)
                if type(item) is str:
                    block[key] = _text(
                        item,
                        field=f"block.{key}",
                        maximum=500,
                    )
                elif isinstance(item, Sequence) and not isinstance(item, (str, bytes)):
                    strings = [
                        _text(entry, field=f"block.{key}", maximum=200)
                        for entry in item[:10]
                        if type(entry) is str
                    ]
                    block[key] = " | ".join(strings)[:1_000]
            if block:
                blocks.append(block)
        return blocks

    @staticmethod
    def _decision(value: Any) -> AIDecision:
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
            for item in items:
                _text(
                    item,
                    field=f"AI {field}",
                    maximum=300,
                    allow_empty=True,
                )
                if len(item) > 300:
                    raise ValueError(f"AI {field} item is too long")
        return AIDecision(
            available=True,
            suitable=value["suitable"],
            confidence=confidence,
            evidence=tuple(value["evidence"]),
            reasons=tuple(value["reasons"]),
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
            return self._deterministic(rank_score, ai=None)
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
        failure_policy = self._config["ai_failure_policy"]
        if failure_policy == "retry":
            return RankingDecision(
                ready=False,
                retry=True,
                reason="ai_unavailable",
                rank_score=rank_score,
                ai_decision=ai,
            )
        if failure_policy == "deterministic":
            return self._deterministic(
                rank_score,
                ai=ai,
                success_reason="deterministic_fallback",
            )
        return RankingDecision(
            ready=False,
            retry=False,
            reason="ai_unavailable",
            rank_score=rank_score,
            ai_decision=ai,
        )

    def _deterministic(
        self,
        rank_score: RankScore,
        *,
        ai: AIDecision | None,
        success_reason: str = "deterministic_score",
    ) -> RankingDecision:
        ready = rank_score.score >= self._config["minimum_score"]
        return RankingDecision(
            ready=ready,
            retry=False,
            reason=success_reason if ready else "deterministic_below_minimum",
            rank_score=rank_score,
            ai_decision=ai,
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
