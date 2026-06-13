from __future__ import annotations

from work_hunter.hh_agent.policy import VacancyPolicy


def test_policy_hash_is_stable_for_list_order_and_unknown_keys():
    first = VacancyPolicy.from_mapping(
        {
            "must_have": ["Python", "FastAPI"],
            "excluded_keywords": "bitrix, 1c",
            "unknown": "ignored",
        }
    )
    second = VacancyPolicy.from_mapping(
        {
            "must_have": ["fastapi", "python"],
            "excluded_keywords": ["1c", "bitrix"],
        }
    )

    assert first.hash() == second.hash()
    assert first.to_canonical_dict()["must_have"] == ["fastapi", "python"]


def test_policy_defaults_are_conservative():
    policy = VacancyPolicy.from_mapping(None)

    assert policy.min_score == 0
    assert policy.skip_blacklisted_employers is True
    assert policy.excluded_texts == []
