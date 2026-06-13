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
