from __future__ import annotations

import json
from pathlib import Path

import pytest

from work_hunter.candidate import candidate_facts
from work_hunter.models import Job
from work_hunter.services import WorkHunter


def candidate_app(tmp_path):
    app = WorkHunter(tmp_path)
    app.config["profiles"]["default"].update(name="Анна Иванова", email="anna@example.test")
    app.config["about"] = {"summary": "BI-аналитик", "all_skills": ["SQL"], "experience": [
        {"role": "Аналитик", "company": "Пример", "project": "Отчётность", "start": "2022-01",
         "end": "2025-12", "contribution": "Создала витрины данных", "details": ["SQL запросы"],
         "results": ["Сократила время подготовки отчёта с 4 часов до 20 минут"], "tech": ["SQL"]},
    ]}
    return app


def test_facts_version_export_binding_and_offline_readiness(tmp_path, monkeypatch):
    app = candidate_app(tmp_path)
    monkeypatch.setattr("requests.sessions.Session.request", lambda *a, **k: pytest.fail("offline readiness contacted a server"))
    try:
        created = app.create_resume_version("BI-аналитик")
        resume = app.storage.get_resume(created["id"])
        assert "2022-01" in resume.body and "20 минут" in resume.body
        assert "fastapi" not in resume.body
        app.storage.set_active_resume(resume.id)
        exported = app.export_resume(resume.id, "docx")
        assert exported["validation"]["status"] == "ok"
        assert "anna@example.test" in exported["validation"]["text"]
        assert Path(exported["path"]).is_file()
        app.config["ai"].update(api_key="test-key", base_url="https://example.test/chat", model="test-model")
        app.storage.record_ai_request("default", {"backend": "direct", "model": "test-model", "status": "ok", "error_code": "", "duration_ms": 1})
        ready = app.launch_readiness()
        assert ready["status"] == "local_ready" and ready["live_ready"] is False
        with pytest.raises(ValueError, match="Привяжите"):
            app.use_resume_for_hh(resume.id, "default")
        resume = app.storage.get_resume(resume.id)
        resume.hh_resume_id, resume.hh_account_profile_id = "hh-resume", "default"
        app.storage.save_resume(resume)
        assert app.use_resume_for_hh(resume.id, "default")["reauthorization_required"] is True
        assert app.use_resume_for_hh(resume.id, "default")["reauthorization_required"] is False
        fresh = WorkHunter(tmp_path)
        try:
            assert fresh.config["sources"]["hh"]["autopilot"]["accounts"][0]["resume_queries"][0]["resume_id"] == "hh-resume"
            assert fresh.storage.get_resume(resume.id).file_path == exported["path"]
        finally:
            fresh.storage.close()
    finally:
        app.storage.close()


def test_requirement_evidence_cache_and_fact_changes(tmp_path, monkeypatch):
    app = candidate_app(tmp_path)
    calls = []
    response = {"score": 80, "reasoning": "SQL подтверждён, английский неизвестен", "requirements": [
        {"requirement": "SQL", "required": True, "status": "supported", "evidence": "SQL запросы", "note": "Есть опыт"},
        {"requirement": "English B2", "required": True, "status": "unknown", "evidence": "", "note": "Уточнить уровень"},
    ]}
    monkeypatch.setattr("work_hunter.services.chat_completion", lambda messages, config: calls.append(messages) or json.dumps(response))
    try:
        job_id = app.storage.upsert_job(Job(source="hh", source_id="fixture", title="BI analyst", url="https://example.test/job", description="SQL, English B2"))
        first = app.ai_fit(job_id)
        assert first["decision"] == "review"
        assert app.ai_fit(job_id)["cached"] is True and len(calls) == 1
        app.config["about"]["summary"] += ". Нужна удалённая работа."
        assert app.ai_fit(job_id)["cached"] is False and len(calls) == 2
        response["requirements"][0]["evidence"] = "Managed 100 employees"
        app.storage.update_job_description(job_id, "SQL, English B2. New description")
        invalid = app.ai_fit(job_id)
        assert invalid["status"] == "error" and invalid["score"] is None
        assert app.ai_fit(job_id)["cached"] is False  # malformed results are not cached
    finally:
        app.storage.close()


def test_ai_usage_records_provider_cost_and_safe_error(tmp_path, monkeypatch):
    import requests

    from work_hunter.ai_backends import chat_completion

    app = WorkHunter(tmp_path)
    app.config["ai"].update(api_key="do-not-log-this", base_url="https://example.test/chat", model="test")
    response = requests.Response()
    response.status_code = 200
    response._content = json.dumps({"choices": [{"message": {"content": "готово"}}],
                                   "usage": {"prompt_tokens": 4, "completion_tokens": 2, "cost": 0.001}}).encode()
    monkeypatch.setattr("work_hunter.ai_backends.requests.post", lambda *a, **k: response)
    try:
        assert chat_completion([], app.ai_config()) == "готово"
        response.status_code = 403
        with pytest.raises(requests.HTTPError):
            chat_completion([], app.ai_config())
        usage = app.storage.ai_usage_report("default")
        assert usage["requests"] == 2 and usage["errors"] == 1
        assert usage["reported_cost_usd"] == 0.001 and usage["requests_with_cost"] == 1
        assert usage["recent"][0]["error_code"] == "http_403"
        assert "do-not-log-this" not in json.dumps(usage)
    finally:
        app.storage.close()


def test_unknown_employer_answer_survives_restart_and_uses_user_answer(tmp_path, monkeypatch):
    from work_hunter.candidate_reply import MissingCandidateFacts

    app = candidate_app(tmp_path)
    app.init()
    context = {"chat_id": "chat-1", "last_message": "Когда можете приступить?"}
    prompts = []
    monkeypatch.setattr("work_hunter.services.chat_completion", lambda messages, config: prompts.append(messages) or '{"answer":"","missing_facts":["Дата выхода"],"evidence":[]}')
    try:
        with pytest.raises(MissingCandidateFacts) as error:
            app._draft_hh_chatik_reply_ai(context=context, options=[], system_prompt="Ответь", message_prompt="{history}")
        assert "Создала витрины данных" in prompts[0][0]["content"]
        assert "Когда можете приступить?" in prompts[0][1]["content"]
        pending_id = app._queue_reply_question(context, error.value)
        assert app._queue_reply_question(context, error.value) == pending_id
        with pytest.raises(ValueError, match="Сначала напишите"):
            app.approve_hh_approval(pending_id)
        app.modify_hh_approval(pending_id, instruction="Мой ответ", payload_patch={"reply": {"message": "Могу приступить 1 октября"}})
        app.approve_hh_approval(pending_id)
    finally:
        app.storage.close()
    restarted = WorkHunter(tmp_path)
    try:
        monkeypatch.setattr("work_hunter.services.chat_completion", lambda *args: pytest.fail("approved answer should not be regenerated"))
        answer = restarted._draft_hh_chatik_reply_ai(context=context, options=[], system_prompt="Ответь", message_prompt="{last_message}")
        assert answer == "Могу приступить 1 октября"
        assert restarted.storage.list_hh_pending_messages(status="pending") == []
    finally:
        restarted.storage.close()


def test_candidate_preferences_are_not_facts_and_invalid_edits_rejected(tmp_path):
    app = candidate_app(tmp_path)
    try:
        facts = candidate_facts(app.config["profiles"]["default"], app.config["about"])
        assert "must_have_skills" not in facts and "nice_to_have_skills" not in facts
        with pytest.raises(ValueError, match="Invalid candidate facts"):
            app.update_candidate_facts({"experience": "I know everything"})
        assert isinstance(app.config["about"]["experience"], list)
    finally:
        app.storage.close()
