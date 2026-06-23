from __future__ import annotations

import asyncio
import json

from work_hunter import mcp_server
from work_hunter.hh_agent.mcp_handlers import HHMCPToolHandlers
from work_hunter.services import WorkHunter
from work_hunter.storage import Storage


def decode(result):
    return json.loads(result[0].text)


def test_mcp_lists_hh_agent_tools():
    tools = asyncio.run(mcp_server.list_tools())
    names = {tool.name for tool in tools}

    assert "hh_search_vacancies" in names
    assert "hh_apply_vacancy" in names
    assert "hh_research_and_apply" in names


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

    result = asyncio.run(
        mcp_server.call_tool(
            "hh_research_and_apply",
            {"text": "python", "limit": 3},
        )
    )
    payload = decode(result)

    assert payload["status"] == "planned"
    assert payload["counts"] == {"planned": 0, "blocked": 0, "applied": 0}


def test_hh_mcp_analyze_vacancy_uses_research_service_and_persists_analysis(monkeypatch, tmp_path):
    class FakeReply:
        parsed = {
            "score": 88,
            "recommended_action": "apply",
            "reasons": ["FastAPI match"],
            "risk_flags": [],
        }
        model = "test-model"

        def to_dict(self):
            return {"parsed": self.parsed, "model": self.model}

    def fake_structured_chat(messages, ai_config, schema):
        assert schema.name == "hh_vacancy_analysis"
        assert "FastAPI" in messages[0]["content"]
        return FakeReply()

    class FakeService:
        def __init__(self):
            self.storage = Storage(tmp_path / "work_hunter.sqlite3")
            self.config = {"ai": {"model": "test-model"}, "hh_agent": {"policy": {"min_score": 70}}}

        def hh_call_api(self, method, path, data=None):
            assert method == "GET"
            assert path == "/vacancies/vac-1"
            return {
                "id": "vac-1",
                "name": "Python Backend",
                "description": "FastAPI and PostgreSQL",
                "employer": {"id": "emp-1", "name": "Acme"},
            }

        def candidate_map(self):
            return {"summary": "Backend developer"}

    service = FakeService()
    monkeypatch.setattr("work_hunter.hh_agent.mcp_handlers.send_structured_chat", fake_structured_chat, raising=False)

    payload = HHMCPToolHandlers(service).handle("hh_analyze_vacancy", {"vacancy_id": "vac-1", "resume_id": "res-1"})
    analyses = service.storage.list_hh_vacancy_analysis("vac-1")

    assert payload["status"] == "analyzed"
    assert payload["analysis"]["score"] == 88
    assert analyses[0].score == 88
    assert analyses[0].model == "test-model"


def test_hh_mcp_apply_vacancy_uses_research_service_and_persists_attempt(tmp_path):
    class FakeService:
        def __init__(self):
            self.storage = Storage(tmp_path / "work_hunter.sqlite3")
            self.config = {"ai": {}, "hh_agent": {"policy": {"min_score": 70, "force_message": "Hi"}}}

        def hh_call_api(self, method, path, data=None):
            assert method == "GET"
            assert path == "/vacancies/vac-1"
            return {
                "id": "vac-1",
                "name": "Python Backend",
                "description": "FastAPI and PostgreSQL",
                "employer": {"id": "emp-1", "name": "Acme"},
            }

        def candidate_map(self):
            return {"summary": "Backend developer"}

    service = FakeService()

    payload = HHMCPToolHandlers(service).handle("hh_apply_vacancy", {"vacancy_id": "vac-1", "resume_id": "res-1"})
    attempts = service.storage.list_hh_application_attempts("vac-1")

    assert payload["status"] == "planned"
    assert payload["dry_run"] is True
    assert payload["attempt"]["reason"] == "dry_run"
    assert attempts[0].status == "planned"
    assert attempts[0].letter == "Hi"


def test_hh_mcp_research_and_apply_searches_and_plans_attempts(tmp_path):
    class FakeService:
        def __init__(self):
            self.storage = Storage(tmp_path / "work_hunter.sqlite3")
            self.config = {"ai": {}, "hh_agent": {"policy": {"force_message": "Hi"}}}

        def hh_call_api(self, method, path, data=None):
            assert method == "GET"
            if path == "/vacancies":
                assert data["text"] == "python"
                return {
                    "items": [
                        {
                            "id": "vac-1",
                            "name": "Python Backend",
                            "alternate_url": "https://hh.ru/vacancy/vac-1",
                            "employer": {"name": "Acme"},
                        }
                    ]
                }
            if path == "/vacancies/vac-1":
                return {
                    "id": "vac-1",
                    "name": "Python Backend",
                    "description": "FastAPI and PostgreSQL",
                    "employer": {"id": "emp-1", "name": "Acme"},
                }
            raise AssertionError(path)

        def candidate_map(self):
            return {"summary": "Backend developer"}

    service = FakeService()

    payload = HHMCPToolHandlers(service).handle(
        "hh_research_and_apply",
        {"text": "python", "limit": 1, "resume_id": "res-1"},
    )
    attempts = service.storage.list_hh_application_attempts("vac-1")

    assert payload["status"] == "planned"
    assert payload["counts"] == {"planned": 1, "blocked": 0, "applied": 0}
    assert payload["items"][0]["vacancy_id"] == "vac-1"
    assert attempts[0].status == "planned"
