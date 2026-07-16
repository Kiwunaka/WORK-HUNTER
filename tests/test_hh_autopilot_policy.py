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
    if path == "vacancy.salary_to":
        case["vacancy"]["salary_from"] = 100_000
    if path == "context.history.already_applied":
        case["context"]["history"]["applied_vacancy_ids"] = ["v-1"]
    if path == "context.history.active":
        case["context"]["history"]["active_vacancy_ids"] = ["v-1"]
    if path == "context.history.permanently_skipped":
        case["context"]["history"]["permanently_skipped_vacancy_ids"] = ["v-1"]
    if path == "context.blacklist.vacancy":
        case["context"]["blacklist"]["vacancy_ids"] = ["v-1"]
    if path == "context.blacklist.employer":
        case["context"]["blacklist"]["employer_ids"] = ["e-1"]

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
    case["context"]["history"]["already_applied"] = True
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


def test_policy_redacts_secret_bearing_matched_terms_before_evidence() -> None:
    case = _base_case()
    case["filters"]["excluded_keywords"] = ["Bearer super-secret"]
    case["candidate"]["stop_words"] = []
    case["vacancy"]["description"] = "Bearer super-secret"

    decision = _evaluate(case)

    assert decision.reason == "hard_filter:excluded_keywords"
    assert decision.evidence["matched"] == ("redacted",)
    assert "secret" not in repr(decision.evidence).casefold()


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


@pytest.mark.parametrize(
    "history_changes",
    [
        {"already_applied": False, "existing": True},
        {"already_applied": False, "existing": "not-a-bool"},
    ],
)
def test_every_present_history_alias_must_be_valid_and_agree(
    history_changes: dict[str, Any],
) -> None:
    case = _base_case()
    case["context"]["history"].update(history_changes)

    decision = _evaluate(case)

    assert decision.passed is False
    assert decision.reason == "missing_required_data"
    assert decision.evidence == {"field": "history.already_applied"}


@pytest.mark.parametrize(
    "mutate",
    [
        lambda case: case["vacancy"].__setitem__("role_family_ids", ["1"]),
        lambda case: case["vacancy"].__setitem__("area", {"id": "2"}),
        lambda case: case["vacancy"].__setitem__("schedule", {"id": "office"}),
        lambda case: case["vacancy"].__setitem__("work_formats", ["office"]),
        lambda case: case["vacancy"].__setitem__("employment", {"id": "part"}),
        lambda case: case["vacancy"].__setitem__(
            "experience", {"id": "moreThan6"}
        ),
        lambda case: case["vacancy"].__setitem__(
            "salary",
            {"from": 170_000, "to": 240_000, "currency": "RUR"},
        ),
        lambda case: case["vacancy"].__setitem__("currency", "USD"),
        lambda case: case["context"]["blacklist"].__setitem__(
            "vacancy_ids", ["v-1"]
        ),
        lambda case: (
            case["vacancy"].__setitem__("area_id", "2"),
            case["candidate"].__setitem__("relocation_allowed", True),
        ),
        lambda case: case["vacancy"].update(
            {
                "application_capabilities": ["direct"],
                "application_capability": "form",
            }
        ),
    ],
)
def test_present_filter_aliases_must_agree_across_nested_and_top_level_facts(
    mutate,
) -> None:
    case = _base_case()
    mutate(case)

    decision = _evaluate(case)

    assert decision.passed is False
    assert decision.reason == "missing_required_data"


def test_explicit_vacancy_capability_requires_explicit_supported_facts() -> None:
    case = _base_case()
    case["filters"]["required_application_capabilities"] = []
    case["vacancy"]["application_capabilities"] = ["form"]
    case["context"].pop("supported_application_capabilities")

    decision = _evaluate(case)

    assert decision.passed is False
    assert decision.reason == "missing_required_data"
    assert decision.evidence == {
        "field": "context.supported_application_capabilities"
    }

    case["context"]["supported_application_capabilities"] = ["form"]
    assert _evaluate(case).passed is True


@pytest.mark.parametrize(
    "decision",
    [
        lambda: FilterDecision(
            False,
            "forged_reason",
            {
                "field": {
                    "secret": "Bearer abc",
                    "raw_html": "<script>x</script>",
                }
            },
        ),
        lambda: FilterDecision(
            True,
            "hard_filters_passed",
            {"field": "area"},
        ),
        lambda: FilterDecision(
            False,
            "hard_filter:area",
            {"matched": ("python",)},
        ),
        lambda: FilterDecision(
            False,
            "missing_required_data",
            {"field": "access_token"},
        ),
        lambda: FilterDecision(
            False,
            "hard_filter:excluded_keywords",
            {"matched": ("<b>python</b>",)},
        ),
    ],
)
def test_filter_decision_is_a_closed_reason_specific_safe_value_object(
    decision,
) -> None:
    with pytest.raises((TypeError, ValueError)):
        decision()


def test_keyword_matching_uses_alphanumeric_phrase_boundaries() -> None:
    case = _base_case()
    case["filters"]["required_keywords"] = []
    case["filters"]["excluded_keywords"] = ["go"]
    case["candidate"]["stop_words"] = []
    case["vacancy"]["title"] = "Django Backend Engineer"
    case["vacancy"]["description"] = "Python services"

    assert _evaluate(case).passed is True

    case["filters"]["excluded_keywords"] = []
    case["filters"]["required_keywords"] = ["go"]
    assert _evaluate(case).reason == "hard_filter:required_keywords"

    case["vacancy"]["description"] = "Python, Go."
    assert _evaluate(case).passed is True

    case["filters"]["required_keywords"] = ["go engineer"]
    case["vacancy"]["description"] = "Python and Go Engineer services"
    assert _evaluate(case).passed is True


@pytest.mark.parametrize(
    "encoded",
    [
        "Bearer&amp;#32;abc123",
        "&amp;lt;script&amp;gt;secret&amp;lt;/script&amp;gt;",
    ],
)
def test_filter_decision_rejects_nested_encoded_credentials_and_markup(
    encoded: str,
) -> None:
    with pytest.raises((TypeError, ValueError)):
        FilterDecision(
            False,
            "hard_filter:excluded_keywords",
            {"matched": (encoded,)},
        )


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("matched", "john@example.test"),
        ("missing", "+7 (999) 123-45-67"),
    ],
)
def test_filter_decision_rejects_personal_data_in_term_evidence(
    key: str,
    value: str,
) -> None:
    reason = (
        "hard_filter:excluded_keywords"
        if key == "matched"
        else "hard_filter:required_keywords"
    )
    with pytest.raises((TypeError, ValueError)):
        FilterDecision(False, reason, {key: (value,)})


@pytest.mark.parametrize(
    "word",
    [
        "secretary",
        "secretary-role-1",
        "cookiecutter",
        "hotplug",
        "proxying",
    ],
)
def test_legitimate_job_words_are_not_treated_as_credentials(word: str) -> None:
    decision = FilterDecision(
        False,
        "hard_filter:excluded_keywords",
        {"matched": (word,)},
    )
    assert decision.evidence["matched"] == (word,)

    case = _base_case()
    case["candidate"]["stop_words"] = []
    case["filters"]["excluded_keywords"] = [word]
    case["filters"]["required_keywords"] = []
    case["vacancy"]["title"] = word
    result = _evaluate(case)
    assert result.reason == "hard_filter:excluded_keywords"
    assert result.evidence["matched"] == (word,)


@pytest.mark.parametrize(
    "factory",
    [
        lambda: FilterDecision(
            False,
            "hard_filter:area",
            {"area_id": "2", "relocation_allowed": True},
        ),
        lambda: FilterDecision(
            False,
            "hard_filter:required_keywords",
            {"missing": ()},
        ),
        lambda: FilterDecision(
            False,
            "missing_required_data",
            {"field": "made_up"},
        ),
        lambda: FilterDecision(
            False,
            "hard_filter:already_applied",
            {"vacancy_id": "John Smith lives at 123 Main Street"},
        ),
        lambda: FilterDecision(
            False,
            "hard_filter:minimum_salary",
            {"salary_known": True, "minimum": 100},
        ),
        lambda: FilterDecision(
            False,
            "hard_filter:required_application_capabilities",
            {"required": ("direct",), "unavailable": ("form",)},
        ),
        lambda: FilterDecision(
            False,
            "hard_filter:allowed_role_families",
            {"actual": ("96",), "allowed": ("96",)},
        ),
        lambda: FilterDecision(
            False,
            "hard_filter:minimum_salary",
            {"currency": "RUR", "expected_currency": "RUR"},
        ),
        lambda: FilterDecision(
            False,
            "hard_filter:minimum_salary",
            {"maximum": 200, "minimum": 100},
        ),
        lambda: FilterDecision(
            False,
            "hard_filter:vacancy_closed",
            {"archived": False, "status": "open"},
        ),
    ],
)
def test_filter_decision_rejects_semantically_forged_evidence(factory) -> None:
    with pytest.raises((TypeError, ValueError)):
        factory()


def test_keyword_phrases_cannot_cross_independent_vacancy_facts() -> None:
    case = _base_case()
    case["candidate"]["stop_words"] = []
    case["vacancy"]["title"] = "Go"
    case["vacancy"]["description"] = "Engineer"
    case["vacancy"]["employer_name"] = "Acme"
    case["vacancy"]["key_skills"] = ["Python"]
    case["filters"]["required_keywords"] = ["go engineer"]

    required = _evaluate(case)
    assert required.reason == "hard_filter:required_keywords"
    assert required.evidence["missing"] == ("go engineer",)

    case["filters"]["required_keywords"] = []
    case["filters"]["excluded_keywords"] = ["go engineer"]
    assert _evaluate(case).passed is True


def test_keyword_matching_keeps_per_fact_skill_and_symbol_boundaries() -> None:
    case = _base_case()
    case["candidate"]["stop_words"] = []
    case["vacancy"]["title"] = "Backend"
    case["vacancy"]["description"] = "Services"
    case["vacancy"]["employer_name"] = "Acme"
    case["vacancy"]["key_skills"] = ["Django"]
    case["filters"]["required_keywords"] = ["go"]
    assert _evaluate(case).reason == "hard_filter:required_keywords"

    for term, fact in (
        ("go", "Go,"),
        ("go engineer", "Go Engineer"),
        (".net", ".NET"),
        ("c++", "C++"),
        ("strasse", "STRASSE"),
    ):
        positive = _base_case()
        positive["candidate"]["stop_words"] = []
        positive["filters"]["required_keywords"] = [term]
        positive["vacancy"]["title"] = fact
        positive["vacancy"]["description"] = ""
        positive["vacancy"]["key_skills"] = ["Python"]
        assert _evaluate(positive).passed is True


def test_hard_filter_validates_salary_range_even_when_floor_is_disabled() -> None:
    case = _base_case()
    case["filters"]["minimum_salary"] = 0
    case["vacancy"]["salary_from"] = 250_000
    case["vacancy"]["salary_to"] = 100_000

    decision = _evaluate(case)

    assert decision.reason == "missing_required_data"
    assert decision.evidence["field"] == "vacancy.salary"


@pytest.mark.parametrize(
    "unsafe",
    [
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
    ],
)
def test_filter_decision_rejects_unicode_obfuscated_sensitive_text(
    unsafe: str,
) -> None:
    with pytest.raises((TypeError, ValueError)):
        FilterDecision(
            False,
            "hard_filter:excluded_keywords",
            {"matched": (unsafe,)},
        )


@pytest.mark.parametrize(
    "phone",
    [
        "+7 (999) 123-45-67",
        "+1 212 555 0123",
        "212-555-0123",
    ],
)
def test_filter_decision_keeps_detecting_real_phone_numbers(
    phone: str,
) -> None:
    with pytest.raises((TypeError, ValueError)):
        FilterDecision(
            False,
            "hard_filter:excluded_keywords",
            {"matched": (phone,)},
        )


def test_filter_decision_preserves_dates_and_ordinary_unicode() -> None:
    decision = FilterDecision(
        False,
        "hard_filter:excluded_keywords",
        {
            "matched": (
                "2019-01-01 - 2025-12-31",
                "Разработчик Python — 東京",
                "100% remote",
            )
        },
    )

    assert decision.evidence["matched"] == (
        "2019-01-01 - 2025-12-31",
        "Разработчик Python — 東京",
        "100% remote",
    )
