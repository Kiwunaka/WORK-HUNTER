from __future__ import annotations

import asyncio
import json
from typing import ClassVar

from work_hunter import mcp_server
from work_hunter.hh_agent import HHVacancyResearchService, VacancyPolicy
from work_hunter.models import Job
from work_hunter.services import WorkHunter


def decode(result):
    return json.loads(result[0].text)


class FakeResearchHHClient:
    def has_token(self):
        return True

    def search_vacancies(self, params):
        assert params["text"] == "python"
        return [
            {
                "id": "vac-1",
                "name": "Python Backend",
                "alternate_url": "https://hh.ru/vacancy/vac-1",
                "employer": {"name": "Acme"},
            }
        ]

    def get_vacancy(self, vacancy_id):
        return {
            "id": vacancy_id,
            "name": "Python Backend",
            "description": "FastAPI and PostgreSQL",
            "employer": {"id": "emp-1", "name": "Acme"},
        }


class FakeStructuredReply:
    parsed: ClassVar[dict[str, object]] = {
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


def fake_research_service(self, *, client, min_score=None):
    return HHVacancyResearchService(
        client=client,
        storage=self.storage,
        ai_config={"model": "test-model"},
        policy=VacancyPolicy.from_mapping({"min_score": min_score or 80, "force_message": "Hi"}),
        structured_chat=fake_structured_chat,
    )


def test_mcp_lists_hh_agent_tools():
    tools = asyncio.run(mcp_server.list_tools())
    names = {tool.name for tool in tools}

    assert "doctor" in names
    assert "source_capabilities" in names
    assert "hh_auth_status" in names
    assert "hh_search_vacancies" in names
    assert "hh_apply_vacancy" in names
    assert "hh_research_and_apply" in names
    assert "ats_resume_audit" in names
    assert "prepare_interview_brief" in names
    assert "save_calendar_event" in names


def test_mcp_productivity_tools_are_callable(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_server, "_root", tmp_path)
    app = WorkHunter(root=tmp_path)
    job_id = app.storage.upsert_job(
        Job(
            source="hh",
            source_id="vac-productivity",
            url="https://hh.ru/vacancy/vac-productivity",
            title="Python Backend",
            company="Acme",
        )
    )
    monkeypatch.setattr(WorkHunter, "ats_audit", lambda self, text, job_id=None: f"ATS:{job_id}:{text}")
    monkeypatch.setattr(WorkHunter, "summarize_job", lambda self, job_id: f"TLDR:{job_id}")
    monkeypatch.setattr(WorkHunter, "interview_questions", lambda self, job_id: f"Q:{job_id}")
    monkeypatch.setattr(WorkHunter, "experience_pitch", lambda self, job_id: f"STAR:{job_id}")

    ats = decode(
        asyncio.run(
            mcp_server.call_tool(
                "ats_resume_audit",
                {"job_id": job_id, "resume_text": "Python, FastAPI"},
            )
        )
    )
    brief = decode(
        asyncio.run(mcp_server.call_tool("prepare_interview_brief", {"job_id": job_id}))
    )
    calendar = decode(
        asyncio.run(
            mcp_server.call_tool(
                "save_calendar_event",
                {
                    "job_id": job_id,
                    "title": "Техническое интервью",
                    "event_type": "interview",
                    "event_date": "2026-09-01T12:00:00+03:00",
                    "notes": "Повторить PostgreSQL",
                },
            )
        )
    )

    assert ats == {"content": f"ATS:{job_id}:Python, FastAPI"}
    assert brief["job"]["title"] == "Python Backend"
    assert brief["tldr"] == f"TLDR:{job_id}"
    assert brief["questions"] == f"Q:{job_id}"
    assert brief["star_pitch"] == f"STAR:{job_id}"
    assert calendar["status"] == "saved"
    assert calendar["calendar"] == "local"
    saved = WorkHunter(root=tmp_path).storage.list_events()
    assert saved[0].title == "Техническое интервью"
    assert saved[0].job_id == job_id


def test_mcp_doctor_and_source_capabilities_are_read_only(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_server, "_root", tmp_path)

    doctor = decode(asyncio.run(mcp_server.call_tool("doctor", {})))
    capabilities = decode(asyncio.run(mcp_server.call_tool("source_capabilities", {})))

    assert doctor["core"]["database"]["status"] == "ok"
    assert doctor["hh_api"]["status"] == "missing_access_token"
    assert capabilities["hh"]["apply"] == "official_api"


def test_mcp_hh_apply_vacancy_is_planned_and_audited(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_server, "_root", tmp_path)
    app = WorkHunter(root=tmp_path)
    app.storage.upsert_job(
        Job(
            source="hh",
            source_id="vac-1",
            url="https://hh.ru/vacancy/vac-1",
            title="Python Backend",
            company="Acme",
        )
    )
    monkeypatch.setattr(
        WorkHunter,
        "prepare_apply_plan",
        lambda self, job_id, *, resume_id=None, letter=None: {
            "status": "ready",
            "job_id": job_id,
            "resume_id": resume_id,
        },
    )

    result = asyncio.run(
        mcp_server.call_tool(
            "hh_apply_vacancy",
            {"vacancy_id": "vac-1", "resume_id": "res-1"},
        )
    )
    payload = decode(result)
    app = WorkHunter(root=tmp_path)
    runs = app.storage.list_hh_agent_mcp_runs()

    assert payload["status"] == "ready"
    assert payload["resume_id"] == "res-1"
    assert payload["mcp_run_id"] == runs[0].id
    assert runs[0].tool_name == "hh_apply_vacancy"
    assert runs[0].status == "ok"


def test_mcp_hh_apply_vacancy_forwards_literal_confirmation(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_server, "_root", tmp_path)
    app = WorkHunter(root=tmp_path)
    app.storage.upsert_job(
        Job(
            source="hh",
            source_id="vac-1",
            url="https://hh.ru/vacancy/vac-1",
            title="Python Backend",
        )
    )
    calls = []

    def fake_confirm(self, job_id, **kwargs):
        calls.append({"job_id": job_id, **kwargs})
        return {"status": "sent"}

    monkeypatch.setattr(WorkHunter, "confirm_apply", fake_confirm)

    result = asyncio.run(
        mcp_server.call_tool(
            "hh_apply_vacancy",
            {"vacancy_id": "vac-1", "resume_id": "res-1", "confirm_apply": True},
        )
    )
    payload = decode(result)

    assert payload["status"] == "sent"
    assert calls[0]["confirm"] is True


def test_mcp_research_and_apply_is_dry_run(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_server, "_root", tmp_path)
    monkeypatch.setattr(WorkHunter, "_hh_research_client", lambda self: FakeResearchHHClient())
    monkeypatch.setattr(WorkHunter, "_build_hh_research_service", fake_research_service)

    result = asyncio.run(
        mcp_server.call_tool(
            "hh_research_and_apply",
            {"text": "python", "limit": 3, "resume_id": "res-1"},
        )
    )
    payload = decode(result)
    app = WorkHunter(root=tmp_path)
    attempts = app.storage.list_hh_application_attempts("vac-1")
    analyses = app.storage.list_hh_vacancy_analysis("vac-1")

    assert payload["status"] == "ok"
    assert payload["counts"]["analyzed"] == 1
    assert payload["counts"]["planned"] == 1
    assert payload["items"][0]["vacancy_id"] == "vac-1"
    assert payload["items"][0]["score"] == 91
    assert payload["items"][0]["attempt_status"] == "planned"
    assert payload["mcp_run_id"] > 0
    assert attempts[0].status == "planned"
    assert attempts[0].letter == "Hi"
    assert analyses[0].model == "test-model"


def test_mcp_research_and_apply_blocks_confirm_apply(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_server, "_root", tmp_path)
    monkeypatch.setattr(WorkHunter, "_hh_research_client", lambda self: FakeResearchHHClient())
    monkeypatch.setattr(WorkHunter, "_build_hh_research_service", fake_research_service)

    result = asyncio.run(
        mcp_server.call_tool(
            "hh_research_and_apply",
            {"text": "python", "limit": 1, "resume_id": "res-1", "confirm_apply": True},
        )
    )
    payload = decode(result)

    assert payload["status"] == "blocked"
    assert payload["reason"] == "real_apply_blocked"
    assert payload["items"] == []
