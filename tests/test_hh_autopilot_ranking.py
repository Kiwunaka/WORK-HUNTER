from __future__ import annotations

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
    return RankingDecision(
        ready=ready,
        retry=False,
        reason="ai_suitable" if ready else "ai_unsuitable",
        rank_score=_rank_score(score),
        ai_decision=(
            None
            if confidence is None
            else _ai(confidence=confidence, suitable=ready)
        ),
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
        {"id": "v-1", "title": ""},
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
    return FilterDecision(True, "hard_filters_passed", {"checks": ("all",)})


def _rejected() -> FilterDecision:
    return FilterDecision(
        False,
        "hard_filter:excluded_keywords",
        {"field": "keywords"},
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
        ("deterministic", 59.9, False, False, "deterministic_below_minimum"),
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
