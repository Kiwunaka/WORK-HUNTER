from __future__ import annotations

import json
import math
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from typing import Any

import pytest

from work_hunter.hh_autopilot.ranking import (
    AI_SCHEMA,
    NoQualifyingCandidatesError,
    DeterministicRanker,
    RankingPolicy,
    StructuredAIRanker,
    select_resume,
)
from work_hunter.hh_autopilot.search import normalize_vacancy
from work_hunter.hh_autopilot.types import (
    AIDecision,
    FilterDecision,
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
UNICODE_UNSAFE_TEXT = (
    "Bearer\u200babc123",
    "access_token\u200b=abc123",
    "Bearer&#x200b;abc123",
    "Ｂｅａｒｅｒ　abc123",
    "access_token%3Dabc123",
    "access_token%253Dabc123",
    "%3Cscript%3Esecret%3C/script%3E",
    "＜script＞secret＜/script＞",
    "иван@example.ru",
    "john@пример.рф",
    "Москва, ул. Тверская, д. 12",
    "Pennsylvania Ave 1600",
    "socks5://username@localhost:8080",
    "http://user%3Apass@localhost",
    "http://user%253Apass@localhost",
    "Python\u200bEngineer",
    "Python\u202eEngineer",
    "Python\u0007Engineer",
)
COMPOSITE_UNSAFE_TEXT = (
    "%26lt%3Bscript%26gt%3Bsecret%26lt%3B/script%26gt%3B",
    "%42earer%26%2332%3Babc123",
    "%2526lt%253Bscript%2526gt%253Bsecret",
)
AUTH_TECHNICAL_PROSE = (
    "Basic Python knowledge",
    "Basic SQL",
    "Experience with Bearer token authentication",
    "Bearer platform engineer",
    "Experience with Bearer token-based authentication",
    "Bearer JWT-based authentication",
)
REAL_AUTH_CREDENTIALS = (
    "Bearer abc123",
    "Bearer eyJhbGciOiJIUzI1NiJ9.payload.signature",
    "Bearer opaque-token",
    "Bearer AbCdEfGhIjKlMnOpQrStUvWx",
    "Basic dXNlcjpwYXNz",
    "Authorization: Basic dXNlcjpwYXNz",
    "Authorization: Bearer opaque-token",
)


def _weights(**changes: float) -> dict[str, float]:
    values = {name: 1.0 for name in COMPONENTS}
    values.update(changes)
    return values


def _facts() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    vacancy = {
        "id": "v-1",
        "title": "Python Backend Engineer",
        "description": "FastAPI PostgreSQL",
        "professional_role_ids": ["96"],
        "key_skills": ["Python", "FastAPI"],
        "experience_id": "between3And6",
        "salary_from": 200_000,
        "salary_to": 250_000,
        "salary_currency": "RUR",
        "schedule_id": "remote",
        "work_format_ids": ["remote"],
        "area_id": "1",
        "industry_ids": ["7"],
        "published_at": "2026-07-16T09:00:00+03:00",
    }
    resume = {
        "id": "r-1",
        "title": "Python Backend Engineer",
        "professional_role_ids": ["96"],
        "skills": ["Python", "FastAPI"],
        "experience_level_id": "between3And6",
        "work_format_ids": ["remote"],
        "area_id": "1",
        "industry_ids": ["7"],
    }
    candidate = {
        "desired_roles": ["backend engineer"],
        "must_have_skills": ["Python", "FastAPI"],
        "experience_levels": ["between3And6"],
        "salary_min": 180_000,
        "salary_currency": "RUR",
        "remote_only": True,
        "area_ids": ["1"],
        "industry_ids": ["7"],
    }
    return vacancy, resume, candidate


def _rank_score(value: float) -> RankScore:
    components = {name: value for name in COMPONENTS}
    weights = {name: 1 / len(COMPONENTS) for name in COMPONENTS}
    return RankScore(score=value, components=components, weights=weights)


def _ai(
    *,
    available: bool = True,
    suitable: bool | None = True,
    confidence: float | None = 0.9,
) -> AIDecision:
    return AIDecision(
        available=available,
        suitable=suitable,
        confidence=confidence,
        evidence=("python",) if available else (),
        reasons=(),
        reason="available" if available else "ai_unavailable",
    )


def _ranking_decision(
    score: float,
    *,
    ready: bool = True,
    confidence: float | None = 0.9,
) -> RankingDecision:
    ai_decision = (
        None
        if confidence is None
        else _ai(confidence=confidence, suitable=ready)
    )
    return RankingDecision(
        ready=ready,
        retry=False,
        reason=(
            "deterministic_score"
            if confidence is None and ready
            else (
                "deterministic_below_minimum"
                if confidence is None
                else ("ai_suitable" if ready else "ai_unsuitable")
            )
        ),
        rank_score=_rank_score(score),
        ai_decision=ai_decision,
    )


def _candidate(
    resume_id: str,
    *,
    score: float = 80,
    confidence: float | None = 0.8,
    published_at: str = "2026-07-16T09:00:00+00:00",
    vacancy_id: str = "v-1",
    account_id: str = "default",
    ready: bool = True,
) -> RankedCandidate:
    return RankedCandidate(
        account_id=account_id,
        vacancy_id=vacancy_id,
        resume_id=resume_id,
        published_at=published_at,
        decision=_ranking_decision(
            score,
            ready=ready,
            confidence=confidence,
        ),
    )


def test_deterministic_ranker_exact_match_reaches_100() -> None:
    vacancy, resume, candidate = _facts()

    score = DeterministicRanker().score(
        vacancy,
        resume,
        candidate,
        _weights(),
    )

    assert score.score == 100.0
    assert set(score.components) == set(COMPONENTS)
    assert all(value == 100.0 for value in score.components.values())


def test_deterministic_role_mismatch_reaches_0_with_role_only_weight() -> None:
    vacancy, resume, candidate = _facts()
    vacancy["professional_role_ids"] = ["1"]
    vacancy["title"] = "Sales Manager"
    vacancy["description"] = "Enterprise sales"
    weights = {name: 0.0 for name in COMPONENTS}
    weights["role"] = 1.0

    score = DeterministicRanker().score(vacancy, resume, candidate, weights)

    assert score.components["role"] == 0.0
    assert score.score == 0.0


def test_missing_optional_ranking_facts_are_neutral_50() -> None:
    score = DeterministicRanker().score(
        {"id": "v-1"},
        {"id": "r-1"},
        {},
        _weights(),
    )

    assert score.score == 50.0
    assert all(value == 50.0 for value in score.components.values())


def test_weights_are_normalized_and_detached_from_caller() -> None:
    vacancy, resume, candidate = _facts()
    weights = _weights(role=3.0, skills=2.0)

    score = DeterministicRanker().score(vacancy, resume, candidate, weights)
    weights["role"] = 999

    assert math.isclose(sum(score.weights.values()), 1.0)
    assert score.weights["role"] == pytest.approx(3 / 10)
    with pytest.raises(TypeError):
        score.weights["role"] = 0.0


@pytest.mark.parametrize(
    "weights",
    [
        {**_weights(), "role": True},
        {**_weights(), "role": float("nan")},
        {**_weights(), "role": float("inf")},
        {**_weights(), "role": -1.0},
        {name: 0.0 for name in COMPONENTS},
        {key: value for key, value in _weights().items() if key != "industry"},
        {**_weights(), "custom": 1.0},
    ],
)
def test_invalid_weights_are_rejected(weights: dict[str, Any]) -> None:
    vacancy, resume, candidate = _facts()

    with pytest.raises((TypeError, ValueError)):
        DeterministicRanker().score(vacancy, resume, candidate, weights)


@pytest.mark.parametrize("bad", [True, float("nan"), float("inf"), "200000"])
def test_non_numeric_or_non_finite_component_facts_are_rejected(bad: Any) -> None:
    vacancy, resume, candidate = _facts()
    vacancy["salary_from"] = bad

    with pytest.raises((TypeError, ValueError)):
        DeterministicRanker().score(vacancy, resume, candidate, _weights())


def test_repeated_scoring_is_deterministic_and_isolated() -> None:
    vacancy, resume, candidate = _facts()
    original = (
        dict(vacancy),
        dict(resume),
        dict(candidate),
    )
    ranker = DeterministicRanker()

    first = ranker.score(vacancy, resume, candidate, _weights())
    second = ranker.score(vacancy, resume, candidate, _weights())

    assert first == second
    assert vacancy == original[0]
    assert resume == original[1]
    assert candidate == original[2]


def test_rank_score_rejects_invalid_direct_construction() -> None:
    valid_components = {name: 50.0 for name in COMPONENTS}
    valid_weights = {name: 1 / len(COMPONENTS) for name in COMPONENTS}

    with pytest.raises(TypeError):
        RankScore(True, valid_components, valid_weights)
    with pytest.raises(ValueError):
        RankScore(101, valid_components, valid_weights)
    with pytest.raises(ValueError):
        RankScore(
            50,
            {**valid_components, "role": float("nan")},
            valid_weights,
        )


def test_structured_ai_uses_exact_schema_and_only_bounded_allowlisted_facts() -> None:
    captured: dict[str, Any] = {}

    def structured_call(messages, ai_config, schema, **kwargs):
        captured["messages"] = messages
        captured["ai_config"] = ai_config
        captured["schema"] = schema
        captured["kwargs"] = kwargs
        return SimpleNamespace(
            parsed={
                "suitable": True,
                "confidence": 0.85,
                "evidence": ["Python"],
                "reasons": [],
            }
        )

    vacancy, resume, candidate = _facts()
    vacancy["description"] = "Python " * 10_000
    vacancy["authorization"] = "Bearer vacancy-secret"
    resume["cookie"] = "resume-secret"
    candidate["access_token"] = "candidate-secret"
    completion = object()
    ranker = StructuredAIRanker(
        {"model": "test-model", "max_retries": 1},
        structured_call=structured_call,
        completion=completion,
    )

    decision = ranker.evaluate(
        vacancy,
        resume,
        candidate,
        detail="heavy",
    )

    assert decision.available is True
    assert decision.suitable is True
    assert decision.confidence == 0.85
    assert captured["schema"].schema == AI_SCHEMA
    assert captured["schema"].schema["additionalProperties"] is False
    assert captured["kwargs"]["completion"] is completion
    prompt = repr(captured["messages"])
    assert "only supplied facts" in prompt.casefold()
    assert len(prompt) < 30_000
    assert "vacancy-secret" not in prompt
    assert "resume-secret" not in prompt
    assert "candidate-secret" not in prompt


@pytest.mark.parametrize(
    "parsed",
    [
        {"suitable": True, "confidence": 0.8, "evidence": []},
        {
            "suitable": True,
            "confidence": 0.8,
            "evidence": [],
            "reasons": [],
            "extra": 1,
        },
        {
            "suitable": 1,
            "confidence": 0.8,
            "evidence": [],
            "reasons": [],
        },
        {
            "suitable": True,
            "confidence": True,
            "evidence": [],
            "reasons": [],
        },
        {
            "suitable": True,
            "confidence": float("nan"),
            "evidence": [],
            "reasons": [],
        },
        {
            "suitable": True,
            "confidence": 1.1,
            "evidence": [],
            "reasons": [],
        },
        {
            "suitable": True,
            "confidence": 0.8,
            "evidence": ["x"] * 21,
            "reasons": [],
        },
        {
            "suitable": True,
            "confidence": 0.8,
            "evidence": ["x" * 301],
            "reasons": [],
        },
        {
            "suitable": True,
            "confidence": 0.8,
            "evidence": "not-a-list",
            "reasons": [],
        },
    ],
)
def test_structured_ai_locally_rejects_every_malformed_shape(
    parsed: dict[str, Any]
) -> None:
    vacancy, resume, candidate = _facts()
    ranker = StructuredAIRanker(
        {"model": "test"},
        structured_call=lambda *args, **kwargs: SimpleNamespace(parsed=parsed),
    )

    decision = ranker.evaluate(vacancy, resume, candidate, detail="light")

    assert decision.available is False
    assert decision.reason == "ai_unavailable"
    assert decision.evidence == ()
    assert decision.reasons == ()


def test_structured_ai_masks_backend_exception_text() -> None:
    vacancy, resume, candidate = _facts()

    def fail(*args, **kwargs):
        raise TimeoutError("Bearer super-secret timed out")

    decision = StructuredAIRanker(
        {"model": "test"},
        structured_call=fail,
    ).evaluate(vacancy, resume, candidate, detail="light")

    assert decision == _ai(
        available=False,
        suitable=None,
        confidence=None,
    )
    assert "secret" not in repr(decision)


def test_structured_ai_accepts_schema_valid_empty_evidence_strings() -> None:
    vacancy, resume, candidate = _facts()
    ranker = StructuredAIRanker(
        {"model": "test"},
        structured_call=lambda *args, **kwargs: SimpleNamespace(
            parsed={
                "suitable": True,
                "confidence": 0.8,
                "evidence": [""],
                "reasons": [""],
            }
        ),
    )

    decision = ranker.evaluate(vacancy, resume, candidate, detail="light")

    assert decision.available is True
    assert decision.evidence == ("",)
    assert decision.reasons == ("",)


class _AISpy:
    def __init__(self, result: AIDecision):
        self.result = result
        self.calls: list[tuple[Any, ...]] = []

    def evaluate(self, *args, **kwargs) -> AIDecision:
        self.calls.append((args, kwargs))
        return self.result


def _ranking_config(**changes: Any) -> dict[str, Any]:
    config = {
        "minimum_score": 60,
        "ai_mode": "borderline",
        "borderline_low": 50,
        "borderline_high": 70,
        "minimum_ai_confidence": 0.7,
        "ai_detail": "light",
        "ai_failure_policy": "retry",
    }
    config.update(changes)
    return config


def _passed() -> FilterDecision:
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


def _rejected() -> FilterDecision:
    return FilterDecision(
        False,
        "hard_filter:excluded_keywords",
        {"matched": ("bitrix",)},
    )


def test_hard_rejection_never_calls_ai() -> None:
    spy = _AISpy(_ai())
    decision = RankingPolicy(_ranking_config(ai_mode="all"), spy).decide(
        _rejected(),
        _rank_score(100),
        {},
        {},
        {},
    )

    assert decision.ready is False
    assert decision.retry is False
    assert decision.reason == "hard_filter:excluded_keywords"
    assert spy.calls == []


@pytest.mark.parametrize(
    ("score", "ready"),
    [(59.9999, False), (60.0, True), (100.0, True)],
)
def test_ai_off_uses_inclusive_minimum_score(score: float, ready: bool) -> None:
    spy = _AISpy(_ai())
    decision = RankingPolicy(_ranking_config(ai_mode="off"), spy).decide(
        _passed(), _rank_score(score), {}, {}, {}
    )

    assert decision.ready is ready
    assert spy.calls == []


@pytest.mark.parametrize(
    ("score", "calls", "ready"),
    [
        (49.9999, 0, False),
        (50.0, 1, True),
        (70.0, 1, True),
        (70.0001, 0, True),
    ],
)
def test_borderline_boundaries_are_exact(
    score: float, calls: int, ready: bool
) -> None:
    spy = _AISpy(_ai())
    decision = RankingPolicy(_ranking_config(), spy).decide(
        _passed(), _rank_score(score), {}, {}, {}
    )

    assert decision.ready is ready
    assert len(spy.calls) == calls


def test_ai_all_calls_for_every_hard_filter_passed_candidate() -> None:
    spy = _AISpy(_ai(suitable=False, confidence=0.95))

    decision = RankingPolicy(_ranking_config(ai_mode="all"), spy).decide(
        _passed(), _rank_score(100), {}, {}, {}
    )

    assert decision.ready is False
    assert decision.reason == "ai_unsuitable"
    assert len(spy.calls) == 1


def test_ranking_policy_converts_injected_ai_exception_to_typed_unavailable() -> None:
    class RaisingAI:
        def evaluate(self, *args, **kwargs):
            raise TimeoutError("Bearer secret")

    decision = RankingPolicy(
        _ranking_config(ai_mode="all", ai_failure_policy="retry"),
        RaisingAI(),
    ).decide(_passed(), _rank_score(80), {}, {}, {})

    assert decision.ready is False
    assert decision.retry is True
    assert decision.reason == "ai_unavailable"
    assert decision.ai_decision.available is False


@pytest.mark.parametrize(
    ("failure_policy", "score", "ready", "retry", "reason"),
    [
        ("retry", 80, False, True, "ai_unavailable"),
        ("deterministic", 60, True, False, "deterministic_fallback"),
        ("deterministic", 59.9, False, False, "deterministic_fallback"),
        ("skip", 80, False, False, "ai_unavailable"),
    ],
)
@pytest.mark.parametrize(
    "ai_result",
    [
        _ai(available=False, suitable=None, confidence=None),
        _ai(available=True, suitable=True, confidence=0.69),
    ],
)
def test_unavailable_or_low_confidence_ai_uses_exact_failure_policy(
    failure_policy: str,
    score: float,
    ready: bool,
    retry: bool,
    reason: str,
    ai_result: AIDecision,
) -> None:
    spy = _AISpy(ai_result)
    original = _rank_score(score)

    decision = RankingPolicy(
        _ranking_config(ai_mode="all", ai_failure_policy=failure_policy),
        spy,
    ).decide(_passed(), original, {}, {}, {})

    assert decision.ready is ready
    assert decision.retry is retry
    assert decision.reason == reason
    assert decision.rank_score == original
    assert decision.ai_decision is not None
    assert decision.ai_decision.available is False


def test_confident_ai_suitable_and_unsuitable_are_typed() -> None:
    suitable = RankingPolicy(
        _ranking_config(ai_mode="all"),
        _AISpy(_ai(suitable=True, confidence=0.7)),
    ).decide(_passed(), _rank_score(10), {}, {}, {})
    unsuitable = RankingPolicy(
        _ranking_config(ai_mode="all"),
        _AISpy(_ai(suitable=False, confidence=0.7)),
    ).decide(_passed(), _rank_score(100), {}, {}, {})

    assert (suitable.ready, suitable.reason) == (True, "ai_suitable")
    assert (unsuitable.ready, unsuitable.reason) == (False, "ai_unsuitable")


def test_resume_selection_uses_full_stable_order() -> None:
    candidates = [
        _candidate("r-score", score=90, confidence=None, vacancy_id="v-9"),
        _candidate("r-conf-low", score=80, confidence=0.7, vacancy_id="v-1"),
        _candidate(
            "r-old",
            score=80,
            confidence=0.8,
            published_at="2026-07-15T09:00:00+00:00",
            vacancy_id="v-1",
        ),
        _candidate(
            "r-vacancy",
            score=80,
            confidence=0.8,
            published_at="2026-07-16T09:00:00+00:00",
            vacancy_id="v-2",
        ),
        _candidate(
            "r-2",
            score=80,
            confidence=0.8,
            published_at="2026-07-16T09:00:00+00:00",
            vacancy_id="v-1",
        ),
        _candidate(
            "r-1",
            score=80,
            confidence=0.8,
            published_at="2026-07-16T09:00:00+00:00",
            vacancy_id="v-1",
        ),
    ]

    selected = select_resume(candidates, "per_resume")

    assert [candidate.resume_id for candidate in selected] == [
        "r-score",
        "r-1",
        "r-2",
        "r-vacancy",
        "r-old",
        "r-conf-low",
    ]


def test_missing_or_invalid_publication_time_sorts_as_oldest() -> None:
    selected = select_resume(
        [
            _candidate("missing", published_at=""),
            _candidate("invalid", published_at="16 July 2026"),
            _candidate("valid", published_at="2026-01-01T00:00:00+00:00"),
        ],
        "per_resume",
    )

    assert [candidate.resume_id for candidate in selected] == [
        "valid",
        "invalid",
        "missing",
    ]


def test_best_resume_only_returns_one_candidate() -> None:
    selected = select_resume(
        [
            _candidate("r-2", score=80, confidence=0.8),
            _candidate("r-1", score=80, confidence=0.8),
        ],
        "best_resume_only",
    )

    assert isinstance(selected, RankedCandidate)
    assert selected.resume_id == "r-1"


def test_unready_candidates_are_not_qualifying() -> None:
    selected = select_resume(
        [
            _candidate("skipped", score=100, ready=False),
            _candidate("ready", score=50, ready=True),
        ],
        "best_resume_only",
    )

    assert selected.resume_id == "ready"


def test_duplicate_candidate_identity_is_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        select_resume(
            [
                _candidate("r-1", score=80),
                _candidate("r-1", score=90),
            ],
            "per_resume",
        )


def test_empty_qualifying_set_has_a_typed_error() -> None:
    with pytest.raises(NoQualifyingCandidatesError):
        select_resume(
            [_candidate("r-1", ready=False)],
            "best_resume_only",
        )


def test_ranked_candidate_is_frozen_and_detached() -> None:
    candidate = _candidate("R-1")

    assert candidate.resume_id == "r-1"
    with pytest.raises(FrozenInstanceError):
        candidate.resume_id = "changed"


def test_rank_score_rejects_a_forged_weighted_total() -> None:
    with pytest.raises(ValueError, match="weighted"):
        RankScore(
            score=100,
            components={name: 0.0 for name in COMPONENTS},
            weights={name: 1 / len(COMPONENTS) for name in COMPONENTS},
        )


@pytest.mark.parametrize(
    "factory",
    [
        lambda: RankingDecision(
            ready=True,
            retry=False,
            reason="ai_unsuitable",
            rank_score=_rank_score(0),
            ai_decision=AIDecision(
                available=True,
                suitable=False,
                confidence=1.0,
                evidence=("python",),
                reasons=("unsuitable",),
            ),
        ),
        lambda: RankingDecision(
            ready=False,
            retry=False,
            reason="ai_suitable",
            rank_score=_rank_score(100),
            ai_decision=_ai(suitable=True),
        ),
        lambda: RankingDecision(
            ready=True,
            retry=False,
            reason="deterministic_score",
            rank_score=_rank_score(100),
            ai_decision=_ai(suitable=True),
        ),
        lambda: RankingDecision(
            ready=True,
            retry=False,
            reason="ai_unavailable",
            rank_score=_rank_score(100),
            ai_decision=_ai(
                available=False,
                suitable=None,
                confidence=None,
            ),
        ),
        lambda: RankingDecision(
            ready=False,
            retry=False,
            reason="deterministic_below_minimum",
            rank_score=_rank_score(10),
            ai_decision=_ai(
                available=False,
                suitable=None,
                confidence=None,
            ),
        ),
        lambda: RankingDecision(
            ready=True,
            retry=False,
            reason="deterministic_fallback",
            rank_score=_rank_score(100),
            ai_decision=_ai(suitable=True, confidence=0.1),
        ),
        lambda: RankingDecision(
            ready=False,
            retry=True,
            reason="ai_unavailable",
            rank_score=_rank_score(50),
            ai_decision=_ai(suitable=True, confidence=0.1),
        ),
        lambda: RankingDecision(
            ready=False,
            retry=False,
            reason="forged_reason",
            rank_score=_rank_score(50),
            ai_decision=None,
        ),
    ],
)
def test_ranking_decision_enforces_the_closed_semantic_matrix(factory) -> None:
    with pytest.raises((TypeError, ValueError)):
        factory()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda vacancy, resume, candidate: candidate.__setitem__(
            "must_have_skills", ["   "]
        ),
        lambda vacancy, resume, candidate: resume.__setitem__(
            "professional_role_ids", []
        ),
        lambda vacancy, resume, candidate: vacancy.__setitem__(
            "industry_ids", [""]
        ),
    ],
)
def test_present_empty_ranking_facts_are_malformed_not_neutral(mutate) -> None:
    vacancy, resume, candidate = _facts()
    mutate(vacancy, resume, candidate)

    with pytest.raises((TypeError, ValueError)):
        DeterministicRanker().score(vacancy, resume, candidate, _weights())


def test_present_empty_ranking_text_is_malformed_when_consumed() -> None:
    vacancy, resume, candidate = _facts()
    vacancy.pop("professional_role_ids")
    resume.pop("professional_role_ids")
    vacancy["title"] = "   "

    with pytest.raises((TypeError, ValueError)):
        DeterministicRanker().score(vacancy, resume, candidate, _weights())


@pytest.mark.parametrize(
    "component",
    ["role", "skills", "industry"],
)
def test_asymmetric_overlap_uses_vacancy_requirements_as_denominator(
    component: str,
) -> None:
    vacancy, resume, candidate = _facts()
    unrelated = [f"unrelated-{index}" for index in range(99)]
    if component == "role":
        vacancy["professional_role_ids"] = ["96"]
        resume["professional_role_ids"] = ["96", *unrelated]
        candidate.pop("professional_role_ids", None)
    elif component == "skills":
        vacancy["key_skills"] = ["Python"]
        candidate["must_have_skills"] = ["Python", *unrelated]
        resume["skills"] = ["Python", *unrelated]
    else:
        vacancy["industry_ids"] = ["7"]
        candidate["industry_ids"] = ["7", *unrelated]
        resume["industry_ids"] = ["7", *unrelated]
    weights = {name: 0.0 for name in COMPONENTS}
    weights[component] = 1.0

    score = DeterministicRanker().score(vacancy, resume, candidate, weights)

    assert score.components[component] == 100.0
    assert score.score == 100.0


@pytest.mark.parametrize(
    "mutate",
    [
        lambda vacancy, resume, candidate: resume.__setitem__(
            "role_family_ids", ["1"]
        ),
        lambda vacancy, resume, candidate: candidate.__setitem__(
            "skills", ["Rust"]
        ),
        lambda vacancy, resume, candidate: vacancy.__setitem__(
            "experience", {"id": "moreThan6"}
        ),
        lambda vacancy, resume, candidate: vacancy.__setitem__(
            "salary",
            {"from": 1, "to": 2, "currency": "USD"},
        ),
        lambda vacancy, resume, candidate: vacancy.__setitem__(
            "work_formats", ["office"]
        ),
        lambda vacancy, resume, candidate: vacancy.__setitem__(
            "area", {"id": "2"}
        ),
        lambda vacancy, resume, candidate: vacancy.__setitem__(
            "industries", ["999"]
        ),
    ],
)
def test_every_present_ranking_alias_must_be_valid_and_agree(mutate) -> None:
    vacancy, resume, candidate = _facts()
    mutate(vacancy, resume, candidate)

    with pytest.raises((TypeError, ValueError)):
        DeterministicRanker().score(vacancy, resume, candidate, _weights())


@pytest.mark.parametrize(
    ("detail", "limit"),
    [("light", 12_000), ("heavy", 20_000)],
)
def test_structured_ai_has_a_stable_whole_prompt_budget(
    detail: str,
    limit: int,
) -> None:
    prompts: list[list[dict[str, str]]] = []

    def structured_call(messages, *args, **kwargs):
        prompts.append(messages)
        return SimpleNamespace(
            parsed={
                "suitable": True,
                "confidence": 0.9,
                "evidence": ["Python"],
                "reasons": [],
            }
        )

    vacancy, resume, candidate = _facts()
    vacancy["description"] = "Python " * 20_000
    resume["skills"] = [f"skill-{index}-" + ("x" * 200) for index in range(100)]
    resume["experience"] = [
        {
            "role": "Python engineer " * 100,
            "summary": "Python systems " * 100,
            "details": ["Python detail " * 50 for _ in range(10)],
        }
        for _ in range(10)
    ]
    resume["education"] = [
        {
            "project": "Python project " * 100,
            "summary": "Python education " * 100,
        }
        for _ in range(10)
    ]
    ranker = StructuredAIRanker(
        {"model": "test"},
        structured_call=structured_call,
    )

    first = ranker.evaluate(vacancy, resume, candidate, detail=detail)
    second = ranker.evaluate(vacancy, resume, candidate, detail=detail)

    assert first.available is True
    assert second.available is True
    assert prompts[0] == prompts[1]
    total_chars = sum(len(message["content"]) for message in prompts[0])
    assert total_chars <= limit
    json.loads(prompts[0][1]["content"])


def test_structured_ai_global_budget_also_caps_utf8_bytes() -> None:
    captured: list[dict[str, str]] = []

    def structured_call(messages, *args, **kwargs):
        captured.extend(messages)
        return SimpleNamespace(
            parsed={
                "suitable": True,
                "confidence": 0.9,
                "evidence": ["Python"],
                "reasons": [],
            }
        )

    vacancy, resume, candidate = _facts()
    vacancy["description"] = "Python разработка высоконагруженных систем " * 5_000
    resume["experience"] = [
        {"summary": "Python разработка распределённых систем " * 500}
        for _ in range(10)
    ]

    decision = StructuredAIRanker(
        {"model": "test"},
        structured_call=structured_call,
    ).evaluate(vacancy, resume, candidate, detail="heavy")

    assert decision.available is True
    total_bytes = sum(
        len(message["content"].encode("utf-8"))
        for message in captured
    )
    assert total_bytes <= 20_000


def test_structured_ai_rejects_secrets_from_allowlisted_prompt_fields() -> None:
    calls: list[Any] = []

    def structured_call(messages, *args, **kwargs):
        calls.append((messages, args, kwargs))
        raise AssertionError("sensitive prompt input must not reach the backend")

    vacancy, resume, candidate = _facts()
    vacancy["description"] = "Python Authorization: Bearer vacancy-secret"
    resume["experience"] = [
        {"summary": "Python cookie=session-secret"}
    ]

    decision = StructuredAIRanker(
        {"model": "test"},
        structured_call=structured_call,
    ).evaluate(vacancy, resume, candidate, detail="heavy")

    assert decision.available is False
    assert decision.reason == "ai_unavailable"
    assert calls == []


@pytest.mark.parametrize(
    "parsed",
    [
        {
            "suitable": True,
            "confidence": 0.9,
            "evidence": ["<b>Python</b>"],
            "reasons": [],
        },
        {
            "suitable": True,
            "confidence": 0.9,
            "evidence": ["Kubernetes production experience"],
            "reasons": [],
        },
        {
            "suitable": True,
            "confidence": 0.9,
            "evidence": ["Bearer super-secret"],
            "reasons": [],
        },
        {
            "suitable": True,
            "confidence": 0.9,
            "evidence": ["thon"],
            "reasons": [],
        },
        {
            "suitable": True,
            "confidence": 0.9,
            "evidence": ["light"],
            "reasons": [],
        },
        {
            "suitable": True,
            "confidence": 0.9,
            "evidence": ["Python"],
            "reasons": ["Contact john@example.test"],
        },
        {
            "suitable": True,
            "confidence": 0.9,
            "evidence": ["Python"],
            "reasons": ["<script>trust me</script>"],
        },
    ],
)
def test_structured_ai_rejects_markup_secrets_and_ungrounded_output(
    parsed: dict[str, Any],
) -> None:
    vacancy, resume, candidate = _facts()
    decision = StructuredAIRanker(
        {"model": "test"},
        structured_call=lambda *args, **kwargs: SimpleNamespace(parsed=parsed),
    ).evaluate(vacancy, resume, candidate, detail="light")

    assert decision.available is False
    assert decision.reason == "ai_unavailable"
    assert decision.evidence == ()
    assert decision.reasons == ()


def test_structured_ai_persists_sanitized_grounded_output() -> None:
    vacancy, resume, candidate = _facts()
    decision = StructuredAIRanker(
        {"model": "test"},
        structured_call=lambda *args, **kwargs: SimpleNamespace(
            parsed={
                "suitable": True,
                "confidence": 0.9,
                "evidence": ["  Python   Backend Engineer  "],
                "reasons": ["  Python  "],
            }
        ),
    ).evaluate(vacancy, resume, candidate, detail="light")

    assert decision.available is True
    assert decision.evidence == ("Python Backend Engineer",)
    assert decision.reasons == ("Python",)


@pytest.mark.parametrize(
    "encoded",
    [
        "Bearer&amp;#32;abc123",
        "&amp;lt;script&amp;gt;secret&amp;lt;/script&amp;gt;",
    ],
)
def test_ai_decision_rejects_nested_encoded_credentials_and_markup(
    encoded: str,
) -> None:
    with pytest.raises((TypeError, ValueError)):
        AIDecision(
            available=True,
            suitable=True,
            confidence=0.9,
            evidence=(encoded,),
            reasons=(),
        )


@pytest.mark.parametrize(
    "personal",
    [
        "john@example.test",
        "+7 (999) 123-45-67",
        "John Smith lives at 123 Main Street",
    ],
)
def test_ai_decision_rejects_personal_data_defense_in_depth(
    personal: str,
) -> None:
    with pytest.raises((TypeError, ValueError)):
        AIDecision(
            available=True,
            suitable=True,
            confidence=0.9,
            evidence=(personal,),
            reasons=(),
        )


@pytest.mark.parametrize(
    "encoded",
    [
        "Bearer&amp;#32;abc123",
        "&amp;lt;script&amp;gt;secret&amp;lt;/script&amp;gt;",
    ],
)
def test_structured_ai_never_calls_backend_with_nested_encoded_secrets(
    encoded: str,
) -> None:
    calls: list[Any] = []
    vacancy, resume, candidate = _facts()
    vacancy["description"] = encoded
    decision = StructuredAIRanker(
        {"model": "test"},
        structured_call=lambda *args, **kwargs: calls.append((args, kwargs)),
    ).evaluate(vacancy, resume, candidate, detail="light")

    assert decision.available is False
    assert calls == []


@pytest.mark.parametrize(
    "word",
    [
        "Secretary",
        "secretary-role-1",
        "cookiecutter",
        "hotplug",
        "proxying",
    ],
)
def test_legitimate_job_words_survive_ai_prompt_and_output(word: str) -> None:
    captured: dict[str, Any] = {}

    def structured_call(messages, *args, **kwargs):
        captured["messages"] = messages
        return SimpleNamespace(
            parsed={
                "suitable": True,
                "confidence": 0.9,
                "evidence": [word],
                "reasons": [word],
            }
        )

    vacancy, resume, candidate = _facts()
    vacancy["title"] = word
    decision = StructuredAIRanker(
        {"model": "test"},
        structured_call=structured_call,
    ).evaluate(vacancy, resume, candidate, detail="light")

    assert decision.available is True
    assert decision.evidence == (word,)
    assert decision.reasons == (word,)
    assert word.casefold() in repr(captured["messages"]).casefold()


def test_structured_ai_list_aliases_agree_after_unicode_normalization() -> None:
    vacancy, resume, candidate = _facts()
    vacancy["skills"] = ["python", "fastapi"]
    decision = StructuredAIRanker(
        {"model": "test"},
        structured_call=lambda *args, **kwargs: SimpleNamespace(
            parsed={
                "suitable": True,
                "confidence": 0.9,
                "evidence": ["Python"],
                "reasons": [],
            }
        ),
    ).evaluate(vacancy, resume, candidate, detail="light")

    assert decision.available is True
    assert decision.evidence == ("Python",)


def test_deterministic_skill_phrases_do_not_cross_vacancy_facts() -> None:
    vacancy, resume, candidate = _facts()
    vacancy.pop("key_skills")
    vacancy["title"] = "Go"
    vacancy["description"] = "Engineer"
    candidate["must_have_skills"] = ["go engineer"]
    resume["skills"] = ["go engineer"]
    weights = {name: 0.0 for name in COMPONENTS}
    weights["skills"] = 1.0

    score = DeterministicRanker().score(vacancy, resume, candidate, weights)

    assert score.components["skills"] == 0.0
    assert score.score == 0.0


def test_deterministic_skill_matching_uses_phrase_boundaries() -> None:
    vacancy, resume, candidate = _facts()
    weights = {name: 0.0 for name in COMPONENTS}
    weights["skills"] = 1.0
    candidate["must_have_skills"] = ["go"]
    resume["skills"] = ["go"]
    vacancy["key_skills"] = ["Django"]
    assert (
        DeterministicRanker().score(vacancy, resume, candidate, weights).score
        == 0.0
    )

    for required, observed in (
        ("go", "Go"),
        (".net", ".NET"),
        ("c++", "C++"),
        ("strasse", "STRASSE"),
    ):
        vacancy["key_skills"] = [observed]
        candidate["must_have_skills"] = [required]
        resume["skills"] = [required]
        assert (
            DeterministicRanker().score(
                vacancy,
                resume,
                candidate,
                weights,
            ).score
            == 100.0
        )


def test_deterministic_ranker_rejects_inverted_salary_range() -> None:
    vacancy, resume, candidate = _facts()
    vacancy["salary_from"] = 250_000
    vacancy["salary_to"] = 100_000

    with pytest.raises((TypeError, ValueError)):
        DeterministicRanker().score(vacancy, resume, candidate, _weights())


def test_weight_normalization_corrects_a_positive_component() -> None:
    vacancy, resume, candidate = _facts()
    weights = dict(
        zip(
            COMPONENTS,
            [0.4, 0.7, 0.8, 0.7, 0.7, 0.8, 0.0],
            strict=True,
        )
    )

    score = DeterministicRanker().score(vacancy, resume, candidate, weights)

    assert math.fsum(score.weights.values()) == pytest.approx(1.0)
    assert score.weights["industry"] == 0.0
    assert all(value >= 0.0 for value in score.weights.values())


def test_rank_score_stores_only_the_canonical_rounded_total() -> None:
    components = {name: 80.0 for name in COMPONENTS}
    weights = {name: 1 / len(COMPONENTS) for name in COMPONENTS}

    score = RankScore(
        score=80.0000000005,
        components=components,
        weights=weights,
    )

    assert score.score == 80.0


def test_canonical_score_preserves_normal_tie_breaking() -> None:
    components = {name: 80.0 for name in COMPONENTS}
    weights = {name: 1 / len(COMPONENTS) for name in COMPONENTS}
    noisy = RankingDecision(
        ready=True,
        retry=False,
        reason="deterministic_score",
        rank_score=RankScore(80.0000000005, components, weights),
        ai_decision=None,
    )
    exact = RankingDecision(
        ready=True,
        retry=False,
        reason="deterministic_score",
        rank_score=RankScore(80.0, components, weights),
        ai_decision=None,
    )

    selected = select_resume(
        [
            RankedCandidate("default", "v-1", "r-2", "", noisy),
            RankedCandidate("default", "v-1", "r-1", "", exact),
        ],
        "best_resume_only",
    )

    assert selected.resume_id == "r-1"


def test_rank_score_canonicalizes_signed_zero() -> None:
    score = RankScore(
        score=-0.0,
        components={name: -0.0 for name in COMPONENTS},
        weights={
            name: (1.0 if name == "role" else -0.0)
            for name in COMPONENTS
        },
    )

    assert math.copysign(1.0, score.score) == 1.0
    assert all(math.copysign(1.0, value) == 1.0 for value in score.components.values())
    assert all(math.copysign(1.0, value) == 1.0 for value in score.weights.values())


def test_area_scoring_validates_all_relocation_aliases() -> None:
    vacancy, resume, candidate = _facts()
    vacancy["area_id"] = "2"
    candidate["area_ids"] = ["1"]
    candidate["relocation_allowed"] = True
    candidate["allow_relocation"] = False

    with pytest.raises((TypeError, ValueError)):
        DeterministicRanker().score(vacancy, resume, candidate, _weights())


def test_normalized_vacancy_empty_sentinels_are_optional_absence() -> None:
    vacancy = normalize_vacancy({"id": "absent"})
    score = DeterministicRanker().score(
        vacancy,
        {},
        {},
        _weights(),
    )
    assert score.score == 50.0
    assert all(value == 50.0 for value in score.components.values())

    calls: list[Any] = []

    def structured_call(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(
            parsed={
                "suitable": True,
                "confidence": 0.9,
                "evidence": [],
                "reasons": [],
            }
        )

    decision = StructuredAIRanker(
        {"model": "test"},
        structured_call=structured_call,
    ).evaluate(vacancy, {}, {}, detail="light")
    assert decision.available is True
    assert len(calls) == 1

    with pytest.raises((TypeError, ValueError)):
        DeterministicRanker().score(
            {"id": "explicit", "title": ""},
            {},
            {},
            _weights(),
        )


@pytest.mark.parametrize(
    "parsed",
    [
        {
            "suitable": True,
            "confidence": 0.9,
            "evidence": ["Python"],
            "reasons": ["Kubernetes"],
        },
        {
            "suitable": True,
            "confidence": 0.9,
            "evidence": ["Python"],
            "reasons": ["Go Engineer"],
        },
    ],
)
def test_structured_ai_grounds_reasons_in_one_supplied_fact(
    parsed: dict[str, Any],
) -> None:
    vacancy, resume, candidate = _facts()
    vacancy["title"] = "Go"
    vacancy["description"] = "Engineer"
    decision = StructuredAIRanker(
        {"model": "test"},
        structured_call=lambda *args, **kwargs: SimpleNamespace(parsed=parsed),
    ).evaluate(vacancy, resume, candidate, detail="light")

    assert decision.available is False
    assert decision.reason == "ai_unavailable"


def test_redacted_prompt_sentinel_cannot_ground_ai_output() -> None:
    vacancy, resume, candidate = _facts()
    vacancy["description"] = "access_token=abc123"
    decision = StructuredAIRanker(
        {"model": "test"},
        structured_call=lambda *args, **kwargs: SimpleNamespace(
            parsed={
                "suitable": True,
                "confidence": 0.9,
                "evidence": ["redacted"],
                "reasons": [],
            }
        ),
    ).evaluate(vacancy, resume, candidate, detail="light")

    assert decision.available is False
    assert decision.reason == "ai_unavailable"


@pytest.mark.parametrize("unsafe", UNICODE_UNSAFE_TEXT)
def test_ai_decision_rejects_unicode_obfuscated_sensitive_text(
    unsafe: str,
) -> None:
    with pytest.raises((TypeError, ValueError)):
        AIDecision(
            available=True,
            suitable=True,
            confidence=0.9,
            evidence=(unsafe,),
            reasons=(),
        )


@pytest.mark.parametrize("unsafe", UNICODE_UNSAFE_TEXT)
def test_structured_ai_never_sends_unicode_obfuscated_sensitive_text(
    unsafe: str,
) -> None:
    calls: list[Any] = []
    vacancy, resume, candidate = _facts()
    vacancy["description"] = unsafe
    decision = StructuredAIRanker(
        {"model": "test"},
        structured_call=lambda *args, **kwargs: calls.append((args, kwargs)),
    ).evaluate(vacancy, resume, candidate, detail="light")

    assert decision.available is False
    assert decision.reason == "ai_unavailable"
    assert calls == []


@pytest.mark.parametrize(
    "phone",
    [
        "+7 (999) 123-45-67",
        "+1 212 555 0123",
        "212-555-0123",
    ],
)
def test_ai_decision_keeps_detecting_real_phone_numbers(phone: str) -> None:
    with pytest.raises((TypeError, ValueError)):
        AIDecision(
            available=True,
            suitable=True,
            confidence=0.9,
            evidence=(phone,),
            reasons=(),
        )


def test_ai_decision_preserves_dates_and_ordinary_unicode() -> None:
    decision = AIDecision(
        available=True,
        suitable=True,
        confidence=0.9,
        evidence=(
            "2019-01-01 - 2025-12-31",
            "Разработчик Python — 東京",
            "100% remote",
        ),
        reasons=(),
    )

    assert decision.evidence == (
        "2019-01-01 - 2025-12-31",
        "Разработчик Python — 東京",
        "100% remote",
    )


@pytest.mark.parametrize("unsafe", COMPOSITE_UNSAFE_TEXT)
def test_ai_decision_rejects_composite_encoded_sensitive_text(
    unsafe: str,
) -> None:
    with pytest.raises((TypeError, ValueError)):
        AIDecision(
            available=True,
            suitable=True,
            confidence=0.9,
            evidence=(unsafe,),
            reasons=(),
        )


@pytest.mark.parametrize("unsafe", COMPOSITE_UNSAFE_TEXT)
def test_structured_ai_never_sends_composite_encoded_sensitive_text(
    unsafe: str,
) -> None:
    calls: list[Any] = []
    vacancy, resume, candidate = _facts()
    vacancy["description"] = unsafe
    decision = StructuredAIRanker(
        {"model": "test"},
        structured_call=lambda *args, **kwargs: calls.append((args, kwargs)),
    ).evaluate(vacancy, resume, candidate, detail="light")

    assert decision.available is False
    assert calls == []


@pytest.mark.parametrize(
    "unsafe",
    [
        "Bearer<b></b> abc123",
        "Bea<b></b>rer abc123",
        "access_<b></b>token=abc123",
    ],
)
def test_structured_ai_rescans_both_html_stripped_views(
    unsafe: str,
) -> None:
    calls: list[Any] = []
    vacancy, resume, candidate = _facts()
    vacancy["description"] = unsafe
    decision = StructuredAIRanker(
        {"model": "test"},
        structured_call=lambda *args, **kwargs: calls.append((args, kwargs)),
    ).evaluate(vacancy, resume, candidate, detail="light")

    assert decision.available is False
    assert calls == []


def test_structured_ai_preserves_spaces_when_stripping_safe_html() -> None:
    captured: dict[str, Any] = {}

    def structured_call(messages, *args, **kwargs):
        captured["payload"] = json.loads(messages[1]["content"])
        return SimpleNamespace(
            parsed={
                "suitable": True,
                "confidence": 0.9,
                "evidence": [],
                "reasons": [],
            }
        )

    vacancy, resume, candidate = _facts()
    vacancy["description"] = "Python<b></b>developer"
    decision = StructuredAIRanker(
        {"model": "test"},
        structured_call=structured_call,
    ).evaluate(vacancy, resume, candidate, detail="light")

    assert decision.available is True
    assert captured["payload"]["vacancy"]["description"] == "Python developer"


@pytest.mark.parametrize("prose", AUTH_TECHNICAL_PROSE)
def test_structured_ai_allows_authentication_technical_prose(
    prose: str,
) -> None:
    calls: list[Any] = []
    vacancy, resume, candidate = _facts()
    vacancy["description"] = prose
    decision = StructuredAIRanker(
        {"model": "test"},
        structured_call=lambda *args, **kwargs: (
            calls.append((args, kwargs))
            or SimpleNamespace(
                parsed={
                    "suitable": True,
                    "confidence": 0.9,
                    "evidence": [],
                    "reasons": [],
                }
            )
        ),
    ).evaluate(vacancy, resume, candidate, detail="light")

    assert decision.available is True
    assert len(calls) == 1


def test_ai_decision_allows_authentication_technical_prose() -> None:
    decision = AIDecision(
        available=True,
        suitable=True,
        confidence=0.9,
        evidence=AUTH_TECHNICAL_PROSE,
        reasons=(),
    )

    assert decision.evidence == AUTH_TECHNICAL_PROSE


@pytest.mark.parametrize("credential", REAL_AUTH_CREDENTIALS)
def test_ai_decision_still_rejects_real_auth_credentials(
    credential: str,
) -> None:
    with pytest.raises((TypeError, ValueError)):
        AIDecision(
            available=True,
            suitable=True,
            confidence=0.9,
            evidence=(credential,),
            reasons=(),
        )
