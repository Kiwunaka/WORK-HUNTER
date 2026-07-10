from __future__ import annotations

import asyncio
import json

from work_hunter import mcp_server
from work_hunter.sources import PUBLIC_BOARD_SOURCE_NAMES


class FakeWorkHunter:
    def apply_hh(self, *args, **kwargs):
        raise AssertionError("MCP must not call real HH apply")


def test_mcp_blocks_real_hh_apply(monkeypatch):
    monkeypatch.setattr(mcp_server, "WorkHunter", lambda root: FakeWorkHunter())

    result = asyncio.run(mcp_server.call_tool("apply_hh", {"job_id": 1, "dry_run": False}))
    payload = json.loads(result[0].text)

    assert payload["status"] == "blocked"
    assert "MCP" in payload["message"]


def test_mcp_source_enums_include_public_board_sources():
    tools = asyncio.run(mcp_server.list_tools())
    search_jobs = next(tool for tool in tools if tool.name == "search_jobs")
    list_jobs = next(tool for tool in tools if tool.name == "list_jobs")

    search_enum = search_jobs.inputSchema["properties"]["sources"]["items"]["enum"]
    list_enum = list_jobs.inputSchema["properties"]["source"]["enum"]

    for source_name in PUBLIC_BOARD_SOURCE_NAMES:
        assert source_name in search_enum
        assert source_name in list_enum


def test_mcp_run_strategy_requires_literal_true_confirmation(monkeypatch, tmp_path):
    calls = []

    def fake_run_strategy(self, name, *, dry_run, confirm, resume_id):
        calls.append(
            {
                "name": name,
                "dry_run": dry_run,
                "confirm": confirm,
                "resume_id": resume_id,
            }
        )
        return {"status": "planned"}

    monkeypatch.setattr(mcp_server, "_root", tmp_path)
    monkeypatch.setattr(mcp_server.WorkHunter, "run_strategy", fake_run_strategy)

    result = asyncio.run(
        mcp_server.call_tool(
            "run_strategy",
            {"name": "default", "confirm": "false", "resume_id": "resume-1"},
        )
    )

    assert json.loads(result[0].text) == {"status": "planned"}
    assert calls == [
        {
            "name": "default",
            "dry_run": True,
            "confirm": False,
            "resume_id": "resume-1",
        }
    ]


def test_mcp_research_and_apply_requires_literal_true_confirmation(
    monkeypatch, tmp_path
):
    calls = []

    def fake_research(self, **kwargs):
        calls.append(kwargs)
        return {"status": "ok"}

    monkeypatch.setattr(mcp_server, "_root", tmp_path)
    monkeypatch.setattr(
        mcp_server.WorkHunter,
        "run_hh_research_operation",
        fake_research,
    )

    result = asyncio.run(
        mcp_server.call_tool(
            "hh_research_and_apply",
            {
                "text": "python",
                "resume_id": "resume-1",
                "confirm_apply": "false",
            },
        )
    )

    assert json.loads(result[0].text)["status"] == "ok"
    assert calls[0]["plan_apply"] is True
    assert calls[0]["confirm_apply"] is False
