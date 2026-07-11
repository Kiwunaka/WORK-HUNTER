from __future__ import annotations

import asyncio
import json

from work_hunter.hh_agent import HHVacancyResearchService, VacancyPolicy
from work_hunter import mcp_server
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


def test_mcp_doctor_and_source_capabilities_are_read_only(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_server, "_root", tmp_path)

    doctor = decode(asyncio.run(mcp_server.call_tool("doctor", {})))
    capabilities = decode(asyncio.run(mcp_server.call_tool("source_capabilities", {})))

    assert doctor["core"]["database"]["status"] == "ok"
    assert doctor["hh_api"]["status"] == "missing_access_token"
    assert capabilities["hh"]["apply"] == "official_api"


def test_mcp_hh_apply_vacancy_is_planned_and_audited(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_server, "_root", tmp_path)

    result = asyncio.run(
        mcp_server.call_tool(
            "hh_apply_vacancy",
            {"vacancy_id": "vac-1", "resume_id": "res-1"},
        )
    )
    payload = decode(result)
    app = WorkHunter(root=tmp_path)
    runs = app.storage.list_hh_agent_mcp_runs()

    assert payload["status"] == "planned"
    assert payload["dry_run"] is True
    assert payload["mcp_run_id"] == runs[0].id
    assert runs[0].tool_name == "hh_apply_vacancy"
    assert runs[0].status == "ok"


def test_mcp_hh_apply_vacancy_blocks_confirm_apply(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_server, "_root", tmp_path)

    result = asyncio.run(
        mcp_server.call_tool(
            "hh_apply_vacancy",
            {"vacancy_id": "vac-1", "resume_id": "res-1", "confirm_apply": True},
        )
    )
    payload = decode(result)

    assert payload["status"] == "blocked"
    assert "MCP real apply is blocked" in payload["message"]


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
