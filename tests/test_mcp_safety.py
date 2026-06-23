from __future__ import annotations

import asyncio
import json

from work_hunter import mcp_server
from work_hunter.models import Job, JobScore
from work_hunter.services import WorkHunter
from work_hunter.sources import PUBLIC_BOARD_SOURCE_NAMES
from work_hunter.storage import Storage


class FakeWorkHunter:
    def apply_hh(self, *args, **kwargs):
        raise AssertionError("MCP must not call real HH apply")


class FakeHHCampaignClient:
    apply_calls: list[tuple[str, str, str]] = []

    def __init__(self, config):
        self.config = config

    def has_token(self):
        return True

    def get_vacancy(self, vacancy_id: str):
        return {"id": vacancy_id, "alternate_url": f"https://hh.ru/vacancy/{vacancy_id}"}

    def suitable_resumes(self, vacancy_id: str):
        return [{"id": "resume-1", "title": "Backend"}]

    def apply(self, vacancy_id: str, resume_id: str, message: str):
        self.apply_calls.append((vacancy_id, resume_id, message))
        return {"status": "created", "status_code": 201}


def test_mcp_blocks_real_hh_apply(monkeypatch):
    monkeypatch.setattr(mcp_server, "WorkHunter", lambda root: FakeWorkHunter())

    result = asyncio.run(mcp_server.call_tool("apply_hh", {"job_id": 1, "dry_run": False}))
    payload = json.loads(result[0].text)

    assert payload["status"] == "blocked"
    assert "MCP" in payload["message"]


def test_mcp_campaign_run_requires_enabled_policy_before_real_send(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_server, "_root", tmp_path)
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHCampaignClient)
    FakeHHCampaignClient.apply_calls = []
    app = WorkHunter(root=tmp_path)
    job_id = app.storage.upsert_job(
        Job(source="hh", source_id="1", url="https://hh.ru/vacancy/1", title="Python")
    )
    app.storage.save_score(JobScore(job_id=job_id, total_score=95))
    planned = app.plan_hh_campaign(limit=5, min_score=70)

    result = asyncio.run(mcp_server.call_tool("campaign_run", {"run_id": planned["id"], "confirm": True}))
    payload = json.loads(result[0].text)

    assert payload["status"] == "blocked"
    assert payload["reason"] == "real_apply_requires_campaign_policy"
    assert "enabled" in payload["message"]
    assert FakeHHCampaignClient.apply_calls == []


def test_mcp_campaign_run_requires_explicit_enabled_preset_policy(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_server, "_root", tmp_path)
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHCampaignClient)
    FakeHHCampaignClient.apply_calls = []
    app = WorkHunter(root=tmp_path)
    job_id = app.storage.upsert_job(
        Job(source="hh", source_id="1", url="https://hh.ru/vacancy/1", title="Python")
    )
    app.storage.save_score(JobScore(job_id=job_id, total_score=95))
    planned = app.plan_hh_campaign(limit=5, min_score=70)
    app.enable_hh_campaign(planned["id"])

    missing_policy = asyncio.run(mcp_server.call_tool("campaign_run", {"run_id": planned["id"], "confirm": True}))
    missing_payload = json.loads(missing_policy[0].text)
    app.save_hh_campaign_preset(
        "main-python",
        {"enabled": True, "real_apply": True, "min_score": 70, "skip_tests": True, "daily_cap": 5},
    )
    confirmed = asyncio.run(
        mcp_server.call_tool("campaign_run", {"run_id": planned["id"], "confirm": True, "preset": "main-python"})
    )
    confirmed_payload = json.loads(confirmed[0].text)

    assert missing_payload["status"] == "blocked"
    assert missing_payload["reason"] == "real_apply_requires_campaign_policy"
    assert missing_payload["policy_reasons"] == ["mcp_preset_required"]
    assert confirmed_payload["status"] == "confirmed"
    assert confirmed_payload["counts"]["applied"] == 1
    assert len(FakeHHCampaignClient.apply_calls) == 1
    assert FakeHHCampaignClient.apply_calls[0][:2] == ("1", "resume-1")


def test_mcp_source_enums_include_public_board_sources():
    tools = asyncio.run(mcp_server.list_tools())
    search_jobs = next(tool for tool in tools if tool.name == "search_jobs")
    list_jobs = next(tool for tool in tools if tool.name == "list_jobs")

    search_enum = search_jobs.inputSchema["properties"]["sources"]["items"]["enum"]
    list_enum = list_jobs.inputSchema["properties"]["source"]["enum"]

    for source_name in PUBLIC_BOARD_SOURCE_NAMES:
        assert source_name in search_enum
        assert source_name in list_enum


def test_mcp_run_audit_masks_sensitive_input_and_output(tmp_path):
    storage = Storage(tmp_path / "db.sqlite3")

    run_id = storage.start_hh_agent_mcp_run(
        "apply_job",
        {
            "headers": {"Authorization": "Bearer secret", "Cookie": "sid=secret"},
            "access_token": "secret-token",
            "job_id": 1,
        },
    )
    storage.finish_hh_agent_mcp_run(
        run_id,
        output={
            "status": "blocked",
            "refresh_token": "refresh-secret",
            "headers": {"Set-Cookie": "sid=next"},
        },
    )

    run = storage.list_hh_agent_mcp_runs()[0]

    assert run.input["headers"]["Authorization"] == "***"
    assert run.input["headers"]["Cookie"] == "***"
    assert run.input["access_token"] == "***"
    assert run.output["refresh_token"] == "***"
    assert run.output["headers"]["Set-Cookie"] == "***"
