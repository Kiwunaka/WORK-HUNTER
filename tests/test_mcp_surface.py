from __future__ import annotations

import asyncio
import json

from work_hunter import mcp_server
from work_hunter.models import Job, JobScore, Resume
from work_hunter.services import WorkHunter


def _call(name: str, args: dict | None = None) -> dict | list:
    result = asyncio.run(mcp_server.call_tool(name, args or {}))
    return json.loads(result[0].text)


def test_mcp_exposes_roadmap_tool_surface():
    tools = asyncio.run(mcp_server.list_tools())
    names = {tool.name for tool in tools}

    assert {
        "search_jobs",
        "score_jobs",
        "list_jobs",
        "get_job",
        "prepare_cover_letter",
        "build_application_pack",
        "preview_application",
        "apply_job",
        "mark_job",
        "daily_report",
        "candidate_onboarding_next",
        "candidate_add_fact",
        "candidate_confirm_fact",
        "candidate_profile_summary",
        "resume_build_variant",
        "campaign_plan",
        "campaign_review",
        "campaign_run",
        "campaign_pause",
        "campaign_kill",
        "campaign_replay",
        "browser_check_session",
        "source_status",
        "source_certification_matrix",
        "source_certification_plan",
        "source_certification_evidence",
        "source_certify",
        "source_redaction_scan",
        "source_external_apply_target",
        "source_external_apply_from_har",
        "source_sync",
        "source_prepare_apply",
        "source_dry_run_apply",
    }.issubset(names)


def test_mcp_candidate_resume_and_application_pack_flow_masks_secrets(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_server, "_root", tmp_path)
    app = WorkHunter(root=tmp_path)
    job_id = app.storage.upsert_job(
        Job(
            source="geekjob",
            source_id="g1",
            url="https://geekjob.ru/vacancy/g1",
            title="FastAPI backend",
            company="Acme",
            description="FastAPI PostgreSQL",
        )
    )
    resume_id = app.storage.save_resume(
        Resume(name="Base", body="Python developer.", profile_id="default", is_active=True)
    )

    next_step = _call("candidate_onboarding_next")
    added = _call(
        "candidate_add_fact",
        {
            "question_id": "experience",
            "answer": "I build FastAPI services and PostgreSQL APIs.",
            "source": "mcp_test",
        },
    )
    confirmed = _call("candidate_confirm_fact", {"fact_id": added["facts"][0]["id"]})
    profile = _call("candidate_profile_summary")
    variant = _call("resume_build_variant", {"job_id": job_id, "resume_id": resume_id})
    pack = _call(
        "build_application_pack",
        {
            "job_id": job_id,
            "resume_variant": {"id": variant["id"], "body": variant["body"]},
            "cover_letter": "Hi",
            "source_payload": {
                "form_signature": "known",
                "headers": {"Authorization": "Bearer secret", "Cookie": "sid=secret"},
            },
        },
    )

    serialized = json.dumps(pack, ensure_ascii=False)
    assert next_step["next_question"]["id"] == "experience"
    assert added["status"] == "recorded"
    assert confirmed["status"] == "confirmed"
    assert profile["completeness"]["confirmed_facts"] == 1
    assert variant["status"] == "ready"
    assert "FastAPI" in variant["body"]
    assert pack["policy_status"] == "ready"
    assert "Bearer secret" not in serialized
    assert "sid=secret" not in serialized


def test_mcp_apply_job_blocks_real_send_but_allows_preview(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_server, "_root", tmp_path)
    app = WorkHunter(root=tmp_path)
    job_id = app.storage.upsert_job(
        Job(source="habr", source_id="h1", url="https://career.habr.com/vacancies/1", title="Python")
    )

    blocked = _call("apply_job", {"job_id": job_id, "dry_run": False})
    preview = _call("preview_application", {"job_id": job_id, "letter": "Hi"})

    assert blocked["status"] == "blocked"
    assert blocked["reason"] == "real_apply_requires_campaign_policy"
    assert "MCP" in blocked["message"]
    assert preview["status"] == "preview"
    assert preview["plan"]["job_id"] == job_id
    assert preview["plan"]["submit"] is False


def test_mcp_source_and_browser_tools_cover_external_apply_dry_run(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_server, "_root", tmp_path)
    app = WorkHunter(root=tmp_path)
    app.config["profiles"]["default"]["email"] = "me@example.test"
    app.save_config(app.config)
    job_id = app.storage.upsert_job(
        Job(
            source="jabka",
            source_id="j1",
            url="https://jabka.work/jobs/j1",
            title="Python backend",
            description="Python",
        )
    )
    app.storage.save_score(JobScore(job_id=job_id, total_score=90))

    status = _call("source_status", {"source": "jabka"})
    browser = _call("browser_check_session", {"source": "jabka"})
    plan = _call("source_prepare_apply", {"job_id": job_id, "letter": "Hi"})
    dry_run = _call(
        "source_dry_run_apply",
        {
            "job_id": job_id,
            "form": {
                "form_url": "https://jabka.work/apply/j1",
                "fields": [{"name": "email", "label": "Email", "required": True}],
            },
            "cover_letter": "Hi",
            "campaign_policy": {"min_score": 70},
        },
    )

    assert status["jabka"]["can_prepare_apply"] is True
    assert browser["source"] == "jabka"
    assert plan["status"] == "external"
    assert plan["submit"] is False
    assert dry_run["status"] == "dry_run_ready"
    assert dry_run["submit"] is False


def test_mcp_source_certification_matrix_reports_external_evidence_gaps(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_server, "_root", tmp_path)

    matrix = _call("source_certification_matrix", {"level": 5, "sources": ["hirehi", "jabka"]})

    assert matrix["status"] == "blocked"
    assert matrix["summary"] == {"total": 2, "ready": 0, "blocked": 2}
    assert matrix["sources"]["hirehi"]["ready"] is False
    assert matrix["missing_by_source"]["jabka"] == ["session", "url", "tests", "replay", "redaction", "dry_run"]


def test_mcp_source_certification_plan_reports_next_actions(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_server, "_root", tmp_path)

    plan = _call("source_certification_plan", {"level": 5, "sources": ["hirehi"]})

    assert plan["status"] == "blocked"
    assert plan["sources"]["hirehi"]["actions"][0]["surface"] == "browser_lab"
    assert "work-hunter browser login hirehi" in plan["sources"]["hirehi"]["actions"][0]["command"]


def test_mcp_source_certification_evidence_records_without_promoting(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_server, "_root", tmp_path)
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hirehi"]["external_apply"] = {
        "certified": True,
        "level": 5,
        "session": "hirehi",
        "url": "https://hirehi.example/apply",
    }
    app.save_config(app.config)

    result = _call(
        "source_certification_evidence",
        {
            "source": "hirehi",
            "level": 5,
            "evidence": {"tests": {"status": "passed", "command": "pytest hirehi"}},
        },
    )

    assert result["status"] == "recorded"
    assert result["recorded"] == ["tests"]
    assert result["audit"]["missing"] == ["replay", "redaction", "dry_run"]
    assert result["capabilities"]["level"] == 2


def test_mcp_source_certify_promotes_only_complete_redacted_evidence(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_server, "_root", tmp_path)
    app = WorkHunter(root=tmp_path)
    app.configure_source_external_apply_target(
        "hirehi",
        session="hirehi",
        url="https://hirehi.example/apply",
        payload_template={"jobId": "{source_id}"},
    )

    blocked = _call("source_certify", {"source": "hirehi", "level": 5})

    assert blocked["status"] == "blocked"
    assert blocked["reason"] == "source_certification_evidence_missing"
    assert blocked["capabilities"]["level"] == 2

    app.record_replay_event(
        source="hirehi",
        event_type="external_apply_dry_run",
        data={"status": "dry_run_ready", "submit": False},
    )
    app.record_replay_event(
        source="hirehi",
        event_type="external_apply_redaction_scan",
        data={"status": "passed"},
    )

    certified = _call(
        "source_certify",
        {
            "source": "hirehi",
            "level": 5,
            "evidence": {"tests": {"status": "passed", "command": "pytest hirehi --api-key sk-secret"}},
        },
    )

    serialized = json.dumps(certified, ensure_ascii=False)
    assert certified["status"] == "certified"
    assert certified["capabilities"]["level"] == 5
    assert certified["capabilities"]["can_real_apply"] is True
    assert "sk-secret" not in serialized
    assert certified["audit"]["evidence"]["tests"]["command"] == "pytest hirehi --api-key ***"


def test_mcp_source_external_apply_target_configures_without_promoting(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_server, "_root", tmp_path)

    result = _call(
        "source_external_apply_target",
        {
            "source": "hirehi",
            "session": "hirehi",
            "url": "https://hirehi.example/apply",
            "method": "post",
            "payload_template": {"jobId": "{source_id}"},
        },
    )

    assert result["status"] == "configured"
    assert result["target"]["method"] == "POST"
    assert result["audit"]["missing"] == ["tests", "replay", "redaction", "dry_run"]
    assert result["capabilities"]["level"] == 2


def test_mcp_source_external_apply_from_har_configures_without_promoting(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_server, "_root", tmp_path)
    har_path = tmp_path / "getmatch.har"
    har_path.write_text(
        json.dumps(
            {
                "log": {
                    "entries": [
                        {
                            "request": {
                                "method": "POST",
                                "url": "https://getmatch.ru/api/applications",
                                "headers": [{"name": "Cookie", "value": "sid=secret"}],
                                "postData": {
                                    "text": json.dumps(
                                        {
                                            "offer_id": "34397",
                                            "cover_letter": "Hi",
                                            "resume_id": "resume-secret",
                                        }
                                    )
                                },
                            },
                            "response": {
                                "status": 201,
                                "headers": [],
                                "content": {"mimeType": "application/json", "text": "{}"},
                            },
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    result = _call(
        "source_external_apply_from_har",
        {"source": "getmatch", "path": str(har_path), "hosts": ["getmatch.ru"]},
    )

    assert result["status"] == "configured"
    assert result["configured"]["target"]["payload_template"]["offer_id"] == "{source_id}"
    assert result["configured"]["audit"]["missing"] == ["tests", "replay", "redaction", "dry_run"]
    assert "resume-secret" not in json.dumps(result, ensure_ascii=False)


def test_mcp_source_redaction_scan_records_certification_evidence(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_server, "_root", tmp_path)
    app = WorkHunter(root=tmp_path)
    app.configure_source_external_apply_target(
        "hirehi",
        session="hirehi",
        url="https://hirehi.example/apply",
        payload_template={"jobId": "{source_id}"},
    )
    app.record_replay_event(
        source="hirehi",
        event_type="external_apply_dry_run",
        data={"status": "dry_run_ready", "submit": False},
    )

    result = _call(
        "source_redaction_scan",
        {
            "source": "hirehi",
            "payload": {"headers": {"Authorization": "Bearer secret-token"}},
            "text": "access_token=secret-token",
        },
    )

    assert result["status"] == "recorded"
    assert result["audit"]["missing"] == ["tests"]
    assert "secret-token" not in json.dumps(result, ensure_ascii=False)


def test_mcp_campaign_aliases_plan_pause_and_replay(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_server, "_root", tmp_path)
    app = WorkHunter(root=tmp_path)
    job_id = app.storage.upsert_job(
        Job(source="hh", source_id="1", url="https://hh.ru/vacancy/1", title="Python")
    )
    app.storage.save_score(JobScore(job_id=job_id, total_score=90))

    planned = _call("campaign_plan", {"limit": 5, "min_score": 70})
    review = _call("campaign_review", {"run_id": planned["id"]})
    blocked_run = _call("campaign_run", {"run_id": planned["id"], "confirm": False})
    replay = _call("campaign_replay", {"run_id": planned["id"]})
    paused = _call("campaign_pause", {"reason": "test"})
    killed = _call("campaign_kill", {"reason": "test kill"})

    assert planned["status"] == "planned"
    assert review["run"]["id"] == planned["id"]
    assert blocked_run["status"] == "blocked"
    assert replay["run"]["id"] == planned["id"]
    assert paused["paused"] is True
    assert killed["paused"] is True
