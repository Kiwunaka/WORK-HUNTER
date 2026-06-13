from __future__ import annotations

import pytest

from work_hunter.hh_agent import (
    HHVacancyResearchService,
    VacancyPolicy,
    build_vacancy_dedupe_key,
    run_hard_prechecks,
)
from work_hunter.storage import Storage


def vacancy(**overrides):
    payload = {
        "id": "vac-1",
        "name": "Python Backend Developer",
        "description": "FastAPI and PostgreSQL",
        "employer": {"id": "emp-1", "name": "Acme"},
    }
    payload.update(overrides)
    return payload


def test_vacancy_dedupe_key_normalizes_text():
    first = build_vacancy_dedupe_key(vacancy(name="Python Backend!"))
    second = build_vacancy_dedupe_key(vacancy(name="python backend"))

    assert first == second


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        (vacancy(archived=True), "archived"),
        (vacancy(has_test=True), "test_required"),
        (vacancy(response_url="https://example.test/form"), "manual_form_required"),
        (vacancy(relations=[{"id": "got_response"}]), "already_applied"),
    ],
)
def test_hard_prechecks_block_terminal_vacancy_states(payload, reason):
    result = run_hard_prechecks(
        payload,
        resume_id="res-1",
        policy=VacancyPolicy.from_mapping({}),
    )

    assert result.status == "blocked"
    assert result.reason == reason


def test_hard_prechecks_apply_policy_filters():
    policy = VacancyPolicy.from_mapping(
        {
            "excluded_employers": ["Acme"],
            "excluded_keywords": ["bitrix"],
            "excluded_texts": ["legacy stack"],
        }
    )

    assert run_hard_prechecks(vacancy(), resume_id="res-1", policy=policy).reason == "excluded_employer"
    keyword_policy = VacancyPolicy.from_mapping({"excluded_keywords": ["postgresql"]})
    assert run_hard_prechecks(vacancy(), resume_id="res-1", policy=keyword_policy).reason == "excluded_keyword"
    text_policy = VacancyPolicy.from_mapping({"excluded_texts": ["fastapi and postgresql"]})
    assert run_hard_prechecks(vacancy(), resume_id="res-1", policy=text_policy).reason == "excluded_text"


def test_hard_prechecks_use_skipped_and_dedupe_storage(tmp_path):
    storage = Storage(tmp_path / "db.sqlite3")
    storage.save_hh_skipped_vacancy(resume_id="res-1", vacancy_id="vac-1", reason="test_required")

    skipped = run_hard_prechecks(
        vacancy(),
        resume_id="res-1",
        policy=VacancyPolicy.from_mapping({}),
        storage=storage,
    )
    assert skipped.reason == "test_required"

    storage.clear_hh_skipped_vacancies()
    dedupe_key = build_vacancy_dedupe_key(vacancy())
    storage.save_hh_response_dedupe(resume_id="res-1", dedupe_key=dedupe_key, vacancy_id="old-vac")

    deduped = run_hard_prechecks(
        vacancy(),
        resume_id="res-1",
        policy=VacancyPolicy.from_mapping({}),
        storage=storage,
    )
    assert deduped.reason == "dedupe_hit"


class FakeHHClient:
    def search_vacancies(self, params):
        return [
            {
                "id": "vac-1",
                "name": "Python Backend",
                "alternate_url": "https://hh.ru/vacancy/vac-1",
                "employer": {"name": "Acme"},
            }
        ]

    def get_vacancy(self, vacancy_id):
        return vacancy(id=vacancy_id)

    def get_similar_vacancies(self, vacancy_id):
        return [
            {
                "id": f"{vacancy_id}-similar",
                "name": "Python API Engineer",
                "alternate_url": f"https://hh.ru/vacancy/{vacancy_id}-similar",
                "employer": {"name": "Similar Co"},
            }
        ]


class FakeStructuredReply:
    parsed = {
        "score": 91,
        "recommended_action": "apply",
        "reasons": ["strong python match"],
        "risk_flags": [],
    }
    model = "test-model"

    def to_dict(self):
        return {"parsed": self.parsed, "model": self.model}


def fake_structured_chat(messages, ai_config, schema):
    assert schema.name == "hh_vacancy_analysis"
    assert "Python Backend" in messages[0]["content"]
    return FakeStructuredReply()


def test_research_service_searches_and_fetches_details(tmp_path):
    service = HHVacancyResearchService(
        client=FakeHHClient(),
        storage=Storage(tmp_path / "db.sqlite3"),
    )

    results = service.search_vacancies({"text": "python"})
    similar = service.get_similar_vacancies("vac-1")
    details = service.get_vacancy_details("vac-1")

    assert results[0].vacancy_id == "vac-1"
    assert similar[0].vacancy_id == "vac-1-similar"
    assert similar[0].employer_name == "Similar Co"
    assert results[0].employer_name == "Acme"
    assert details["id"] == "vac-1"


def test_research_service_persists_analysis_and_planned_attempt(tmp_path):
    storage = Storage(tmp_path / "db.sqlite3")
    run_id = storage.start_hh_agent_mcp_run("hh_research_vacancies", {})
    service = HHVacancyResearchService(
        client=FakeHHClient(),
        storage=storage,
        ai_config={"model": "test-model"},
        policy=VacancyPolicy.from_mapping({"min_score": 80, "force_message": "Hi"}),
        structured_chat=fake_structured_chat,
    )

    analysis = service.analyze_vacancy(vacancy(), resume_id="res-1", run_id=run_id)
    attempt = service.plan_apply_vacancy(
        vacancy(),
        resume_id="res-1",
        run_id=run_id,
        analysis=analysis,
    )

    assert analysis.score == 91
    assert attempt.status == "planned"
    assert attempt.reason == "dry_run"
    assert storage.list_hh_vacancy_analysis("vac-1")[0].model == "test-model"
    assert storage.list_hh_application_attempts("vac-1")[0].letter == "Hi"


def test_research_analysis_prompt_includes_persona_context(tmp_path):
    captured: dict[str, str] = {}

    def structured_chat(messages, ai_config, schema):
        captured["prompt"] = messages[0]["content"]
        return FakeStructuredReply()

    service = HHVacancyResearchService(
        client=FakeHHClient(),
        storage=Storage(tmp_path / "db.sqlite3"),
        persona={"name": "Alex", "summary": "Backend automation specialist"},
        structured_chat=structured_chat,
    )

    service.analyze_vacancy(vacancy(), resume_id="res-1")

    assert "Backend automation specialist" in captured["prompt"]
    assert "Alex" in captured["prompt"]


def test_research_service_persists_blocked_attempt(tmp_path):
    storage = Storage(tmp_path / "db.sqlite3")
    service = HHVacancyResearchService(
        client=FakeHHClient(),
        storage=storage,
        policy=VacancyPolicy.from_mapping({}),
    )

    attempt = service.plan_apply_vacancy(vacancy(has_test=True), resume_id="res-1")

    assert attempt.status == "blocked"
    assert attempt.reason == "test_required"
    assert storage.list_hh_application_attempts("vac-1")[0].status == "blocked"


def test_research_service_apply_vacancy_uses_confirm_callback_and_requires_confirm(tmp_path):
    storage = Storage(tmp_path / "db.sqlite3")
    calls: list[dict] = []

    def fake_confirm_apply(job_id: int, **kwargs):
        calls.append({"job_id": job_id, **kwargs})
        return {"status": "applied", "job_id": job_id}

    service = HHVacancyResearchService(
        client=FakeHHClient(),
        storage=storage,
        apply_callback=fake_confirm_apply,
    )

    blocked = service.apply_vacancy(
        vacancy(id="vac-apply"),
        resume_id="res-1",
        job_id=12,
        letter="Hi",
        confirm=False,
    )
    applied = service.apply_vacancy(
        vacancy(id="vac-apply"),
        resume_id="res-1",
        job_id=12,
        letter="Hi",
        confirm=True,
    )
    attempts = storage.list_hh_application_attempts("vac-apply")

    assert blocked.status == "blocked"
    assert blocked.reason == "explicit_confirmation_required"
    assert applied.status == "applied"
    assert calls == [{"job_id": 12, "resume_id": "res-1", "letter": "Hi", "confirm": True}]
    assert [attempt.status for attempt in attempts] == ["blocked", "applied"]
