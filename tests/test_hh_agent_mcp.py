from __future__ import annotations

import asyncio
import json

from work_hunter import mcp_server
from work_hunter.services import WorkHunter


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
