from __future__ import annotations

import json

from work_hunter.cli import main as cli_main
from work_hunter.models import Job, JobScore
from work_hunter.services import WorkHunter


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


def _read_json(capsys):
    return json.loads(capsys.readouterr().out)


def _read_text(capsys):
    return capsys.readouterr().out


def _configure_external_apply(app: WorkHunter, source: str = "hirehi") -> None:
    app.config["sources"][source]["external_apply"] = {
        "certified": True,
        "level": 5,
        "session": source,
        "method": "POST",
        "url": f"https://{source}.example/apply",
        "payload_template": {"jobId": "{source_id}"},
    }
    app.save_config(app.config)


def test_cli_exposes_generic_browser_source_onboard_profile_and_resume_commands(tmp_path, capsys):
    cli_main(["--root", str(tmp_path), "init", "--check", "--json", "--onboard"])
    init_report = _read_json(capsys)

    cli_main(["--root", str(tmp_path), "browser", "status", "getmatch"])
    browser_status = _read_json(capsys)

    cli_main(["--root", str(tmp_path), "browser", "login", "getmatch"])
    browser_login = _read_json(capsys)

    cli_main(["--root", str(tmp_path), "source", "status"])
    source_status = _read_json(capsys)

    cli_main(["--root", str(tmp_path), "source", "certification-matrix", "--level", "5"])
    source_certification_matrix = _read_json(capsys)

    cli_main(["--root", str(tmp_path), "source", "certification-plan", "--level", "5", "--source", "hirehi"])
    source_certification_plan = _read_json(capsys)

    cli_main(["--root", str(tmp_path), "source", "sync", "geekjob", "--limit", "0"])
    source_sync = _read_json(capsys)

    cli_main(["--root", str(tmp_path), "onboard"])
    onboard = _read_json(capsys)

    cli_main(["--root", str(tmp_path), "profile", "show"])
    profile = _read_json(capsys)

    resume_path = tmp_path / "resume.json"
    resume_path.write_text(
        json.dumps({"name": "Base", "body": "Python developer.", "skills": ["Python"]}),
        encoding="utf-8",
    )
    cli_main(["--root", str(tmp_path), "resume", "import", str(resume_path), "--activate"])
    imported = _read_json(capsys)

    assert init_report["onboarding"]["next_question"]["id"] == "experience"
    assert browser_status["source"] == "getmatch"
    assert browser_login["status"] == "planned"
    assert source_status["hh"]["level"] == 6
    assert source_certification_matrix["summary"] == {"total": 6, "ready": 0, "blocked": 6}
    assert source_certification_matrix["sources"]["geekjob"]["ready"] is False
    assert source_certification_plan["summary"] == {"total": 1, "ready": 0, "blocked": 1}
    assert source_certification_plan["sources"]["hirehi"]["actions"][0]["requirement"] == "session"
    assert source_sync["geekjob"]["status"] == "planned"
    assert source_sync["geekjob"]["dry_run"] is True
    assert onboard["next_question"]["id"] == "experience"
    assert profile["completeness"]["status"] == "incomplete"
    assert imported["status"] == "imported"
    assert imported["source_format"] == "json"


def test_cli_source_certification_audit_and_certify(tmp_path, capsys):
    cli_main(["--root", str(tmp_path), "source", "certification-audit", "hirehi", "--level", "5"])
    missing = _read_json(capsys)

    cli_main(
        [
            "--root",
            str(tmp_path),
            "source",
            "external-apply-target",
            "hirehi",
            "--session",
            "hirehi",
            "--url",
            "https://hirehi.example/apply",
            "--method",
            "post",
            "--payload-template",
            '{"jobId": "{source_id}"}',
        ]
    )
    configured = _read_json(capsys)

    cli_main(
        [
            "--root",
            str(tmp_path),
            "source",
            "certification-evidence",
            "hirehi",
            "--level",
            "5",
            "--evidence",
            '{"tests": {"status": "passed", "command": "pytest hirehi"}}',
        ]
    )
    recorded = _read_json(capsys)

    app = WorkHunter(root=tmp_path)
    app.record_replay_event(
        source="hirehi",
        event_type="external_apply_dry_run",
        data={"status": "dry_run_ready", "submit": False},
    )

    cli_main(
        [
            "--root",
            str(tmp_path),
            "source",
            "redaction-scan",
            "hirehi",
            "--payload",
            '{"headers":{"Authorization":"Bearer secret-token"}}',
            "--text",
            "client_secret=secret-token",
        ]
    )
    redaction = _read_json(capsys)

    cli_main(
        [
            "--root",
            str(tmp_path),
            "source",
            "certify",
            "hirehi",
            "--level",
            "5",
        ]
    )
    certified = _read_json(capsys)

    assert missing["ready"] is False
    assert missing["missing"] == ["session", "url", "tests", "replay", "redaction", "dry_run"]
    assert configured["status"] == "configured"
    assert configured["audit"]["missing"] == ["tests", "replay", "redaction", "dry_run"]
    assert recorded["status"] == "recorded"
    assert recorded["audit"]["missing"] == ["replay", "redaction", "dry_run"]
    assert redaction["status"] == "recorded"
    assert redaction["audit"]["missing"] == []
    assert "secret-token" not in json.dumps(redaction, ensure_ascii=False)
    assert certified["status"] == "certified"
    assert certified["capabilities"]["level"] == 5
    assert certified["capabilities"]["can_real_apply"] is True


def test_cli_source_external_apply_from_har_configures_target(tmp_path, capsys):
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

    cli_main(
        [
            "--root",
            str(tmp_path),
            "source",
            "external-apply-from-har",
            "getmatch",
            str(har_path),
            "--host",
            "getmatch.ru",
        ]
    )
    result = _read_json(capsys)

    assert result["status"] == "configured"
    assert result["configured"]["target"]["url"] == "https://getmatch.ru/api/applications"
    assert result["configured"]["target"]["payload_template"]["offer_id"] == "{source_id}"
    assert "resume-secret" not in json.dumps(result, ensure_ascii=False)


def test_cli_source_prepare_and_dry_run_external_apply_without_submit(tmp_path, capsys):
    app = WorkHunter(root=tmp_path)
    app.config["profiles"]["default"]["email"] = "me@example.test"
    app.save_config(app.config)
    job_id = app.storage.upsert_job(
        Job(
            source="jabka",
            source_id="j1",
            url="https://jabka.work/jobs/j1",
            title="Python backend",
            description="Python FastAPI",
        )
    )
    app.storage.save_score(JobScore(job_id=job_id, total_score=90))
    form = {
        "form_url": "https://jabka.work/apply/j1",
        "fields": [
            {"name": "email", "label": "Email", "required": True},
            {"name": "cover_letter", "label": "Cover letter", "required": True},
        ],
    }

    cli_main(["--root", str(tmp_path), "source", "prepare-apply", str(job_id), "--letter", "Hi"])
    prepared = _read_json(capsys)

    cli_main(
        [
            "--root",
            str(tmp_path),
            "source",
            "dry-run-apply",
            str(job_id),
            "--form",
            json.dumps(form),
            "--cover-letter",
            "Hi",
            "--campaign-policy",
            '{"min_score": 70}',
        ]
    )
    dry_run = _read_json(capsys)

    assert prepared["source"] == "jabka"
    assert prepared["submit"] is False
    assert prepared["status"] == "external"
    assert dry_run["source"] == "jabka"
    assert dry_run["status"] == "dry_run_ready"
    assert dry_run["submit"] is False
    assert dry_run["dry_run"]["submit"] is False


def test_cli_replay_exports_job_and_campaign_markdown(tmp_path, capsys):
    app = WorkHunter(root=tmp_path)
    run_id = app.storage.create_hh_campaign_run(filters={"source": "hh"})
    app.record_replay_event(job_id=7, source="hh", event_type="selected", title="Selected", summary="score 95")
    app.record_replay_event(job_id=8, source="hh", event_type="selected", title="Other job")
    app.record_replay_event(run_id=run_id, source="hh", event_type="campaign_planned", title="Campaign planned")

    cli_main(["--root", str(tmp_path), "replay", "job", "7", "--markdown"])
    job_markdown = _read_text(capsys)

    cli_main(["--root", str(tmp_path), "replay", "campaign", str(run_id), "--markdown"])
    run_markdown = _read_text(capsys)

    assert "# Replay Timeline" in job_markdown
    assert "- job_id: 7" in job_markdown
    assert "Selected" in job_markdown
    assert "Other job" not in job_markdown
    assert "# Replay Timeline" in run_markdown
    assert f"- run_id: {run_id}" in run_markdown
    assert "Campaign planned" in run_markdown


def test_cli_exposes_generic_campaign_and_replay_commands(tmp_path, capsys):
    cli_main(
        [
            "--root",
            str(tmp_path),
            "campaign",
            "preset",
            "create",
            "main-python",
            "--params",
            '{"limit": 5, "min_score": 70, "skip_tests": true, "daily_cap": 1}',
        ]
    )
    preset = _read_json(capsys)

    cli_main(["--root", str(tmp_path), "campaign", "plan", "main-python", "--dry-run"])
    planned = _read_json(capsys)

    cli_main(["--root", str(tmp_path), "campaign", "run", "latest"])
    blocked_run = _read_json(capsys)

    cli_main(["--root", str(tmp_path), "campaign", "pause", "main-python"])
    paused = _read_json(capsys)

    cli_main(["--root", str(tmp_path), "campaign", "review", "latest"])
    review = _read_json(capsys)

    cli_main(["--root", str(tmp_path), "campaign", "resume", "latest"])
    resumed = _read_json(capsys)

    cli_main(["--root", str(tmp_path), "campaign", "kill", "latest", "--reason", "test kill"])
    killed = _read_json(capsys)

    cli_main(["--root", str(tmp_path), "replay", "campaign", "latest"])
    replay = _read_json(capsys)

    assert preset["name"] == "main-python"
    assert planned["status"] == "planned"
    assert planned["filters"]["min_score"] == 70
    assert blocked_run["status"] == "blocked"
    assert paused["paused"] is True
    assert review["run"]["id"] == planned["id"]
    assert resumed["paused"] is False
    assert killed["status"] == "killed"
    assert killed["paused"] is True
    assert killed["run"]["status"] == "killed"
    assert replay["run"]["id"] == planned["id"]


def test_cli_campaign_real_run_requires_enable_gate(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHCampaignClient)
    FakeHHCampaignClient.apply_calls = []
    app = WorkHunter(root=tmp_path)
    job_id = app.storage.upsert_job(
        Job(source="hh", source_id="1", url="https://hh.ru/vacancy/1", title="Python")
    )
    app.storage.save_score(JobScore(job_id=job_id, total_score=95))
    planned = app.plan_hh_campaign(limit=5, min_score=70)

    cli_main(["--root", str(tmp_path), "campaign", "run", "latest", "--real"])
    blocked = _read_json(capsys)

    cli_main(["--root", str(tmp_path), "campaign", "enable", "latest"])
    enabled = _read_json(capsys)

    cli_main(["--root", str(tmp_path), "campaign", "run", "latest", "--real"])
    confirmed = _read_json(capsys)

    assert blocked["status"] == "blocked"
    assert blocked["reason"] == "real_apply_requires_campaign_policy"
    assert blocked["run_status"] == "planned"
    assert enabled["run"]["status"] == "enabled"
    assert confirmed["status"] == "confirmed"
    assert planned["id"] == confirmed["id"]
    assert len(FakeHHCampaignClient.apply_calls) == 1
