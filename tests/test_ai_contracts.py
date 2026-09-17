import json

import pytest

from work_hunter.llm.structured import (
    StructuredLLMError,
    StructuredOutputSchema,
    send_structured_chat,
)
from work_hunter.models import Job
from work_hunter.services import WorkHunter, _parse_fit_json

SCHEMA = StructuredOutputSchema("fixture", {"type": "object", "required": ["score", "decision"],
    "properties": {"score": {"type": "integer", "minimum": 0, "maximum": 100},
                   "decision": {"enum": ["review", "skip"]}}})


@pytest.mark.parametrize("body", [
    {}, {"score": "80", "decision": "review"}, {"score": True, "decision": "review"},
    {"score": 80, "decision": "send"}, {"score": 101, "decision": "review"},
    {"score": 80, "decision": "review", "extra": 1}, [{"score": 80, "decision": "review"}],
])
def test_invalid_structures_never_reach_executor(body):
    with pytest.raises(StructuredLLMError):
        send_structured_chat([], {}, SCHEMA, completion=lambda *args: json.dumps(body))


@pytest.mark.parametrize("raw", ["Not evaluated in 2026", '{"score":true,"reasoning":"x"}',
                                '{"score":101,"reasoning":"x"}', '{"score":NaN,"reasoning":"x"}'])
def test_invalid_fit_is_unknown_not_a_number(raw):
    result = _parse_fit_json(raw)
    assert result["status"] == "error"
    assert result["score"] is None


def test_ats_failure_is_not_zero_score(tmp_path, monkeypatch):
    def fail(*args):
        raise TimeoutError()
    monkeypatch.setattr("work_hunter.services.chat_completion", fail)
    app = WorkHunter(tmp_path)
    try:
        result = app.ats_score_resume("Candidate text")
        assert result["score"] is None
        assert result["status"] == "error"
    finally:
        app.storage.close()


def test_interview_prep_keeps_requirements_at_end_of_description(tmp_path, monkeypatch):
    captured = []

    def completion(messages, config):
        captured.extend(messages)
        return "SQL: оконные функции и план выполнения."

    monkeypatch.setattr("work_hunter.services.chat_completion", completion)
    app = WorkHunter(tmp_path)
    try:
        job_id = app.storage.upsert_job(Job(
            source="fixture", source_id="interview", url="https://example.test/job",
            title="BI analyst", description="Обязанности. " * 150 + "SQL window functions",
        ))
        assert "SQL" in app.interview_stage_prep(job_id, "tech")
        assert "SQL window functions" in captured[1]["content"]

        def fail(*args):
            raise TimeoutError("provider unavailable")

        monkeypatch.setattr("work_hunter.services.chat_completion", fail)
        with pytest.raises(ValueError, match="provider unavailable"):
            app.interview_stage_prep(job_id, "tech")
    finally:
        app.storage.close()


def test_doctor_reports_unsupported_ai_backend_without_calling_provider(tmp_path, monkeypatch):
    def unexpected_call(*args):
        pytest.fail("doctor must not send a paid AI request")

    monkeypatch.setattr("work_hunter.services.chat_completion", unexpected_call)
    app = WorkHunter(tmp_path)
    try:
        app.config["ai"]["backend"] = "codex_server"
        report = app.doctor()
        assert report["ai"]["status"] == "unsupported_backend"
        assert report["ai"]["live_verified"] is False
        assert "unsupported_ai_backend" in report["warnings"]
    finally:
        app.storage.close()
