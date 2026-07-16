from __future__ import annotations

import copy
from typing import Any

import pytest

from work_hunter.hh_autopilot.policy import FilterDecision, HardFilter
from work_hunter.hh_autopilot.search import normalize_vacancy


def _base_case() -> dict[str, Any]:
    return {
        "filters": {
            "excluded_keywords": [],
            "required_keywords": ["python"],
            "allowed_role_families": ["96"],
            "areas": ["1"],
            "remote": "only",
            "schedules": ["remote"],
            "employment_types": ["full"],
            "experience_levels": ["between3and6"],
            "languages": ["ru"],
            "citizenships": ["113"],
            "required_application_capabilities": ["direct"],
            "minimum_salary": 150_000,
            "salary_currency": "RUR",
            "unknown_salary": "allow",
            "use_employer_blacklist": True,
        },
        "vacancy": {
            "id": "v-1",
            "title": "Python Backend Engineer",
            "description": "FastAPI services",
            "employer_id": "e-1",
            "employer_name": "Acme",
            "area_id": "1",
            "archived": False,
            "status": "open",
            "schedule_id": "remote",
            "work_format_ids": ["remote"],
            "employment_id": "full",
            "experience_id": "between3And6",
            "professional_role_ids": ["96"],
            "key_skills": ["Python", "FastAPI"],
            "salary_from": 180_000,
            "salary_to": 240_000,
            "salary_currency": "RUR",
            "response_url": "https://example.test/respond/v-1",
            "apply_alternate_url": "",
            "has_test": False,
            "published_at": "2026-07-16T09:00:00+03:00",
        },
        "resume": {
            "id": "r-1",
            "title": "Python Backend Engineer",
            "professional_role_ids": ["96"],
            "skills": ["Python", "FastAPI"],
            "experience_level_id": "between3And6",
            "area_id": "1",
            "work_format_ids": ["remote"],
            "industry_ids": ["7"],
        },
        "candidate": {
            "desired_roles": ["backend"],
            "must_have_skills": ["Python"],
            "nice_to_have_skills": ["FastAPI"],
            "stop_words": ["bitrix"],
            "languages": ["ru", "en"],
            "citizenships": ["113"],
            "area_ids": ["1"],
            "remote_only": True,
            "experience_levels": ["between3And6"],
            "salary_min": 150_000,
            "salary_currency": "RUR",
            "industry_ids": ["7"],
        },
        "context": {
            "history": {
                "already_applied": False,
                "active": False,
                "permanently_skipped": False,
                "applied_vacancy_ids": [],
                "active_vacancy_ids": [],
                "permanently_skipped_vacancy_ids": [],
            },
            "blacklist": {
                "vacancy": False,
                "employer": False,
                "vacancy_ids": [],
                "employer_ids": [],
            },
            "relocation_allowed": False,
            "supported_application_capabilities": [
                "direct",
                "screening",
                "form",
            ],
        },
    }


def _evaluate(case: dict[str, Any]):
    return HardFilter(case["filters"]).evaluate(
        case["vacancy"],
        case["resume"],
        case["candidate"],
        case["context"],
    )


def _set(case: dict[str, Any], path: str, value: Any) -> None:
    target = case
    parts = path.split(".")
    for part in parts[:-1]:
        target = target[part]
    target[parts[-1]] = value


@pytest.mark.parametrize(
    ("path", "value", "reason"),
    [
        ("vacancy.archived", True, "hard_filter:vacancy_closed"),
        ("context.history.already_applied", True, "hard_filter:already_applied"),
        ("context.history.active", True, "hard_filter:active_history"),
        (
            "context.history.permanently_skipped",
            True,
            "hard_filter:permanently_skipped",
        ),
        ("context.blacklist.vacancy", True, "hard_filter:vacancy_blacklist"),
        ("context.blacklist.employer", True, "hard_filter:employer_blacklist"),
        (
            "vacancy.title",
            "Senior Bitrix Developer",
            "hard_filter:excluded_keywords",
        ),
        ("vacancy.professional_role_ids", ["1"], "hard_filter:allowed_role_families"),
        ("vacancy.area_id", "2", "hard_filter:area"),
        ("vacancy.schedule_id", "office", "hard_filter:remote"),
        ("vacancy.employment_id", "part", "hard_filter:employment_type"),
        ("vacancy.experience_id", "moreThan6", "hard_filter:experience"),
        ("vacancy.salary_to", 100_000, "hard_filter:minimum_salary"),
        ("candidate.languages", [], "hard_filter:languages"),
        ("candidate.citizenships", [], "hard_filter:citizenships"),
        (
            "vacancy.application_capabilities",
            ["form"],
            "hard_filter:required_application_capabilities",
        ),
    ],
)
def test_each_hard_filter_has_a_stable_reason(
    path: str, value: Any, reason: str
) -> None:
    case = _base_case()
    _set(case, path, value)
    if path == "vacancy.title":
        case["filters"]["excluded_keywords"] = ["bitrix"]
    if path == "vacancy.area_id":
        case["context"]["relocation_allowed"] = False
    if path == "vacancy.schedule_id":
        case["vacancy"]["work_format_ids"] = ["office"]

    decision = _evaluate(case)

    assert decision.passed is False
    assert decision.reason == reason
    assert "access_token" not in repr(decision.evidence)


def test_filters_return_first_rejection_in_the_approved_order() -> None:
    case = _base_case()
    case["vacancy"]["archived"] = True
    case["context"]["history"]["already_applied"] = True
    case["context"]["blacklist"]["vacancy"] = True

    assert _evaluate(case).reason == "hard_filter:vacancy_closed"


@pytest.mark.parametrize(
    ("mutation", "field"),
    [
        (lambda case: case["vacancy"].pop("archived"), "vacancy_open"),
        (
            lambda case: case["context"]["history"].__setitem__(
                "already_applied", "false"
            ),
            "history.already_applied",
        ),
        (
            lambda case: case["context"]["history"].__setitem__(
                "applied_vacancy_ids", ["v-2", object()]
            ),
            "history.applied_vacancy_ids",
        ),
        (
            lambda case: case["vacancy"].__setitem__(
                "professional_role_ids", []
            ),
            "professional_role_ids",
        ),
        (
            lambda case: case["filters"].__setitem__(
                "use_employer_blacklist", "false"
            ),
            "filters.use_employer_blacklist",
        ),
        (lambda case: case["candidate"].pop("languages"), "languages"),
        (lambda case: case["candidate"].pop("citizenships"), "citizenships"),
    ],
)
def test_unknown_or_malformed_required_facts_fail_closed(
    mutation, field: str
) -> None:
    case = _base_case()
    if field == "vacancy_open":
        case["vacancy"].pop("status")
    mutation(case)

    decision = _evaluate(case)

    assert decision.reason == "missing_required_data"
    assert decision.evidence["field"] == field


def test_history_id_sets_are_exact_bounded_facts() -> None:
    case = _base_case()
    case["context"]["history"]["applied_vacancy_ids"] = ["v-1"]

    decision = _evaluate(case)

    assert decision.reason == "hard_filter:already_applied"
    assert decision.evidence["vacancy_id"] == "v-1"


def test_required_keywords_require_every_term() -> None:
    case = _base_case()
    case["filters"]["required_keywords"] = ["python", "kubernetes"]

    decision = _evaluate(case)

    assert decision.reason == "hard_filter:required_keywords"
    assert decision.evidence["missing"] == ("kubernetes",)


def test_keywords_use_unicode_casefold_and_collapsed_whitespace() -> None:
    case = _base_case()
    case["filters"]["required_keywords"] = ["strasse service"]
    case["vacancy"]["description"] = "STRASSE   SERVICE"

    assert _evaluate(case).passed is True


def test_candidate_stop_words_are_hard_exclusions() -> None:
    case = _base_case()
    case["vacancy"]["description"] = "Python plus BITRIX integration"

    decision = _evaluate(case)

    assert decision.reason == "hard_filter:excluded_keywords"
    assert decision.evidence["matched"] == ("bitrix",)


def test_role_family_requires_explicit_exact_ids() -> None:
    case = _base_case()
    case["vacancy"]["professional_role_ids"] = ["096"]

    assert _evaluate(case).reason == "hard_filter:allowed_role_families"


def test_area_mismatch_can_pass_only_with_explicit_relocation_permission() -> None:
    case = _base_case()
    case["vacancy"]["area_id"] = "2"
    case["context"]["relocation_allowed"] = True

    assert _evaluate(case).passed is True


def test_area_mismatch_without_relocation_fact_is_missing_data() -> None:
    case = _base_case()
    case["vacancy"]["area_id"] = "2"
    case["context"].pop("relocation_allowed")

    decision = _evaluate(case)

    assert decision.reason == "missing_required_data"
    assert decision.evidence["field"] == "relocation"


def test_schedule_is_checked_after_remote() -> None:
    case = _base_case()
    case["filters"]["remote"] = "any"
    case["vacancy"]["schedule_id"] = "flyInFlyOut"
    case["vacancy"]["work_format_ids"] = ["flyInFlyOut"]

    assert _evaluate(case).reason == "hard_filter:schedule"


@pytest.mark.parametrize(
    ("mode", "schedule", "passed"),
    [
        ("any", "office", True),
        ("only", "remote", True),
        ("only", "office", False),
        ("exclude", "office", True),
        ("exclude", "remote", False),
    ],
)
def test_remote_modes_are_exact(mode: str, schedule: str, passed: bool) -> None:
    case = _base_case()
    case["filters"]["remote"] = mode
    case["filters"]["schedules"] = []
    case["vacancy"]["schedule_id"] = schedule
    case["vacancy"]["work_format_ids"] = [schedule]

    assert _evaluate(case).passed is passed


def test_unknown_salary_allow_passes_and_reject_mode_skips() -> None:
    case = _base_case()
    case["vacancy"]["salary_from"] = None
    case["vacancy"]["salary_to"] = None
    case["vacancy"]["salary_currency"] = ""

    assert _evaluate(case).passed is True

    case["filters"]["unknown_salary"] = "reject"
    assert _evaluate(case).reason == "hard_filter:minimum_salary"


def test_wrong_salary_currency_never_converts() -> None:
    case = _base_case()
    case["vacancy"]["salary_currency"] = "USD"

    decision = _evaluate(case)

    assert decision.reason == "hard_filter:minimum_salary"
    assert decision.evidence["currency"] == "usd"


@pytest.mark.parametrize(
    ("vacancy_changes", "expected"),
    [
        ({"application_capabilities": ["screening"]}, True),
        ({"application_capabilities": ["form"]}, False),
        ({"has_test": True, "response_url": ""}, True),
        (
            {
                "has_test": False,
                "response_url": "",
                "apply_alternate_url": "https://example.test/form",
            },
            False,
        ),
    ],
)
def test_application_capabilities_use_explicit_or_normalized_hh_facts(
    vacancy_changes: dict[str, Any], expected: bool
) -> None:
    case = _base_case()
    case["filters"]["required_application_capabilities"] = [
        "direct",
        "screening",
    ]
    case["vacancy"].update(vacancy_changes)

    assert _evaluate(case).passed is expected


def test_standard_hh_vacancy_without_redirect_or_test_is_direct() -> None:
    case = _base_case()
    case["vacancy"].update(
        {
            "has_test": False,
            "response_url": "",
            "apply_alternate_url": "",
        }
    )

    assert _evaluate(case).passed is True


def test_normalized_vacancy_is_accepted_without_mutation() -> None:
    raw = {
        "id": "v-1",
        "name": "Python Backend Engineer",
        "employer": {"id": "e-1", "name": "Acme"},
        "area": {"id": "1", "name": "Moscow"},
        "salary": {"from": 180_000, "to": 240_000, "currency": "RUR"},
        "schedule": {"id": "remote"},
        "work_format": [{"id": "remote"}],
        "employment": {"id": "full"},
        "experience": {"id": "between3And6"},
        "professional_roles": [{"id": "96"}],
        "key_skills": [{"name": "Python"}, {"name": "FastAPI"}],
        "published_at": "2026-07-16T09:00:00+03:00",
        "alternate_url": "https://example.test/v-1",
        "response_url": "https://example.test/respond/v-1",
        "archived": False,
        "has_test": False,
        "status": {"id": "open"},
        "description": "FastAPI services",
    }
    vacancy = normalize_vacancy(raw)
    before = vacancy.to_dict()
    case = _base_case()
    case["vacancy"] = vacancy

    assert _evaluate(case).passed is True
    assert vacancy.to_dict() == before


def test_evidence_is_bounded_allowlisted_and_detached() -> None:
    case = _base_case()
    terms = [f"blocked-{index}" for index in range(30)]
    case["filters"]["excluded_keywords"] = terms
    case["vacancy"]["description"] = " ".join(terms)
    case["vacancy"]["access_token"] = "secret-token"
    case["candidate"]["cookie"] = "secret-cookie"

    decision = _evaluate(case)
    case["filters"]["excluded_keywords"][0] = "changed"
    case["vacancy"]["description"] = "changed"

    assert decision.reason == "hard_filter:excluded_keywords"
    assert len(decision.evidence["matched"]) <= 20
    rendered = repr(decision.evidence)
    assert "secret-token" not in rendered
    assert "secret-cookie" not in rendered
    with pytest.raises(TypeError):
        decision.evidence["field"] = "changed"


@pytest.mark.parametrize(
    "evidence",
    [
        {"access_token": "secret"},
        {"raw_html": "<div>private</div>"},
        {"field": "x" * 301},
        {"matched": ["x"] * 21},
    ],
)
def test_filter_decision_itself_rejects_unbounded_or_forbidden_evidence(
    evidence: dict[str, Any]
) -> None:
    with pytest.raises((TypeError, ValueError)):
        FilterDecision(False, "missing_required_data", evidence)


def test_evaluating_one_vacancy_cannot_mutate_any_input_or_next_evaluation() -> None:
    first = _base_case()
    second = _base_case()
    first_before = copy.deepcopy(first)
    second_before = copy.deepcopy(second)
    hard_filter = HardFilter(first["filters"])

    first_decision = hard_filter.evaluate(
        first["vacancy"], first["resume"], first["candidate"], first["context"]
    )
    second_decision = hard_filter.evaluate(
        second["vacancy"], second["resume"], second["candidate"], second["context"]
    )

    assert first_decision == second_decision
    assert first == first_before
    assert second == second_before
