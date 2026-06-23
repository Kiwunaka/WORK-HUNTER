from __future__ import annotations

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from work_hunter.external_sessions import ExternalHTTPResponse, import_external_session_from_har
from work_hunter.models import Job, JobScore
from work_hunter.services import WorkHunter
from work_hunter.web.server import make_handler


EXTERNAL_APPLY_SOURCES = ["geekjob", "habr", "getmatch", "hirehi", "careerspace", "jabka"]
EXTERNAL_APPLY_HOSTS = {
    "geekjob": "geekjob.ru",
    "habr": "career.habr.com",
    "getmatch": "getmatch.ru",
    "hirehi": "hirehi.ru",
    "careerspace": "careerspace.app",
    "jabka": "jabka.work",
}


def _external_job(app: WorkHunter, source: str = "hirehi", score: int = 92) -> int:
    app.config["profiles"]["default"]["email"] = "me@example.test"
    app.save_config(app.config)
    job_id = app.storage.upsert_job(
        Job(
            source=source,
            source_id="ext-1",
            url=f"https://{source}.example/jobs/ext-1",
            title="Python backend",
            company="Acme",
            description="FastAPI, PostgreSQL",
        )
    )
    app.storage.save_score(JobScore(job_id=job_id, total_score=score))
    return job_id


def _import_external_session(app: WorkHunter, source: str = "hirehi") -> None:
    host = EXTERNAL_APPLY_HOSTS[source]
    har_path = app.root / f"{source}.har"
    har_path.write_text(
        json.dumps(
            {
                "log": {
                    "entries": [
                        {
                            "request": {
                                "url": f"https://{host}/api/applications",
                                "headers": [
                                    {"name": "Cookie", "value": "sid=secret-cookie"},
                                    {"name": "X-CSRF-Token", "value": "csrf-secret"},
                                    {"name": "Content-Type", "value": "application/json"},
                                ],
                            }
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    import_external_session_from_har(app.root, source, har_path, allowed_hosts={host})


def _import_hirehi_session(app: WorkHunter) -> None:
    _import_external_session(app, "hirehi")


def _external_apply_evidence(source: str = "hirehi") -> dict[str, object]:
    return {
        "tests": {"status": "passed", "command": f"python -m pytest tests/test_{source}_apply.py"},
        "replay": {"status": "passed", "run_id": 10},
        "redaction": {"status": "passed", "scan_id": f"{source}-redaction"},
        "dry_run": {"status": "dry_run_ready", "event_id": 11},
    }


def _certify_external_source(app: WorkHunter, source: str = "hirehi", *, level: int = 5, evidence: bool = True) -> None:
    host = EXTERNAL_APPLY_HOSTS[source]
    app.config["sources"][source]["external_apply"] = {
        "certified": True,
        "level": level,
        "session": source,
        "method": "POST",
        "url": f"https://{host}/api/applications",
        "payload_template": {
            "jobId": "{source_id}",
            "coverLetter": "{cover_letter}",
            "message": "{short_message}",
            "resume": "{resume_body}",
        },
    }
    if evidence:
        app.config["sources"][source]["external_apply"]["evidence"] = _external_apply_evidence(source)
    app.save_config(app.config)


def _certify_hirehi(app: WorkHunter, *, level: int = 5, evidence: bool = True) -> None:
    _certify_external_source(app, "hirehi", level=level, evidence=evidence)


def test_external_apply_dry_run_blocks_unknown_forms_without_submit(tmp_path):
    app = WorkHunter(root=tmp_path)
    job_id = _external_job(app)
    form = {
        "form_url": "https://hirehi.ru/apply/ext-1",
        "fields": [
            {"name": "email", "label": "Email", "required": True},
            {"name": "cover_letter", "label": "Cover letter", "required": True},
            {"name": "screening_task", "label": "Solve this test", "required": True},
        ],
    }

    result = app.external_apply_dry_run(
        job_id,
        form=form,
        cover_letter="Hi",
        campaign_policy={"min_score": 70},
    )

    assert result["status"] == "blocked_manual_review"
    assert result["submit"] is False
    assert "unknown_form_fields" in result["risk_flags"]
    assert result["application_pack"]["policy_status"] == "ready"
    events = app.replay_for_job(job_id, event_type="external_apply_dry_run")["events"]
    assert events[0]["data"]["status"] == "blocked_manual_review"


def test_external_apply_dry_run_blocks_empty_detected_form(monkeypatch, tmp_path):
    def fake_fetch(url: str):
        return "<main><h1>Python backend</h1><p>Send us an email manually.</p></main>"

    monkeypatch.setattr("work_hunter.sources.public_boards.fetch_url", fake_fetch)
    app = WorkHunter(root=tmp_path)
    job_id = _external_job(app, source="jabka")

    result = app.external_apply_dry_run(
        job_id,
        form={},
        cover_letter="Hi",
        campaign_policy={"min_score": 70},
    )

    assert result["status"] == "blocked_manual_review"
    assert result["reason"] == "external_apply_form_not_detected"
    assert result["submit"] is False
    assert "unknown_form" in result["risk_flags"]
    assert result["dry_run"]["status"] == "blocked_manual_review"
    assert result["actions"] == [{"type": "open_url", "url": "https://jabka.example/jobs/ext-1"}]
    events = app.replay_for_job(job_id, event_type="external_apply_dry_run")["events"]
    assert events[0]["data"]["reason"] == "external_apply_form_not_detected"


def test_external_apply_confirm_requires_flag_and_prepares_manual_submit_plan(tmp_path):
    app = WorkHunter(root=tmp_path)
    job_id = _external_job(app, source="geekjob")
    form = {
        "form_url": "https://geekjob.ru/apply/ext-1",
        "fields": [
            {"name": "email", "label": "Email", "required": True},
            {"name": "cover_letter", "label": "Cover letter", "required": True},
        ],
    }

    blocked = app.confirm_external_apply(job_id, form=form, cover_letter="Hi", confirm=False)
    result = app.confirm_external_apply(job_id, form=form, cover_letter="Hi", confirm=True)

    assert blocked["status"] == "blocked"
    assert result["status"] == "manual_submit_ready"
    assert result["submit"] is False
    assert result["final_submit_requires_user"] is True
    assert app.storage.get_application(job_id).status == "external_manual_ready"
    events = app.replay_for_job(job_id, event_type="external_apply_confirmed")["events"]
    assert events[0]["data"]["status"] == "manual_submit_ready"


@pytest.mark.parametrize("source", EXTERNAL_APPLY_SOURCES)
def test_certified_external_apply_submit_contract_covers_priority_sources(source: str, tmp_path):
    app = WorkHunter(root=tmp_path)
    job_id = _external_job(app, source=source)
    _import_external_session(app, source)
    _certify_external_source(app, source, level=5)
    host = EXTERNAL_APPLY_HOSTS[source]
    calls: list[dict[str, object]] = []

    def fake_requester(method: str, url: str, *, headers: dict[str, str], data: str | None, timeout: int):
        calls.append({"method": method, "url": url, "headers": headers, "data": data})
        return ExternalHTTPResponse(status=201, headers={"content-type": "application/json"}, body='{"application_id":"app-1"}')

    result = app.confirm_external_apply(
        job_id,
        form={
            "form_url": f"https://{host}/apply/ext-1",
            "fields": [{"name": "cover_letter", "label": "Cover letter", "required": False}],
            "form_signature": f"{source}_apply_form:v1",
        },
        resume_variant={"id": "rv1", "body": "Python backend resume"},
        cover_letter=f"Hi from Work Hunter for {source}",
        short_message="Short",
        campaign_policy={"min_score": 70},
        confirm=True,
        submit=True,
        requester=fake_requester,
    )

    assert result["status"] == "external_applied"
    assert result["source"] == source
    assert result["submit"] is True
    assert calls[0]["method"] == "POST"
    assert calls[0]["url"] == f"https://{host}/api/applications"
    assert json.loads(str(calls[0]["data"]))["jobId"] == "ext-1"
    events = app.replay_for_job(job_id, event_type="external_apply_submitted")["events"]
    assert events[0]["source"] == source
    assert events[0]["data"]["status"] == "external_applied"


def test_certified_external_apply_confirm_submits_through_session_and_records_replay(tmp_path):
    app = WorkHunter(root=tmp_path)
    job_id = _external_job(app, source="hirehi")
    _import_hirehi_session(app)
    _certify_hirehi(app, level=5)
    calls: list[dict[str, object]] = []

    def fake_requester(method: str, url: str, *, headers: dict[str, str], data: str | None, timeout: int):
        calls.append({"method": method, "url": url, "headers": headers, "data": data})
        return ExternalHTTPResponse(status=201, headers={"content-type": "application/json"}, body='{"application_id":"app-1"}')

    result = app.confirm_external_apply(
        job_id,
        form={
            "form_url": "https://hirehi.ru/apply/ext-1",
            "fields": [
                {"name": "email", "label": "Email", "required": True},
                {"name": "cover_letter", "label": "Cover letter", "required": True},
            ],
        },
        resume_variant={"id": "rv1", "body": "Python backend resume"},
        cover_letter="Hi from Work Hunter",
        short_message="Short",
        campaign_policy={"min_score": 70},
        confirm=True,
        submit=True,
        requester=fake_requester,
    )

    assert result["status"] == "external_applied"
    assert result["submit"] is True
    assert calls[0]["method"] == "POST"
    assert calls[0]["url"] == "https://hirehi.ru/api/applications"
    assert json.loads(calls[0]["data"]) == {
        "jobId": "ext-1",
        "coverLetter": "Hi from Work Hunter",
        "message": "Short",
        "resume": "Python backend resume",
    }
    assert calls[0]["headers"]["Cookie"] == "sid=secret-cookie"
    assert app.storage.get_application(job_id).status == "external_applied"
    events = app.replay_for_job(job_id, event_type="external_apply_submitted")["events"]
    assert events[0]["data"]["response"]["status"] == 201
    assert "secret-cookie" not in json.dumps(events, ensure_ascii=False)


def test_certified_external_apply_requires_explicit_session_and_url(tmp_path):
    app = WorkHunter(root=tmp_path)
    job_id = _external_job(app, source="hirehi")
    app.config["sources"]["hirehi"]["external_apply"] = {
        "certified": True,
        "level": 5,
        "method": "POST",
        "url": "https://hirehi.ru/api/applications",
    }
    app.save_config(app.config)

    blocked_session = app.confirm_external_apply(
        job_id,
        form={"form_url": "https://hirehi.ru/apply/ext-1", "fields": [{"name": "email"}]},
        resume_variant={"id": "rv1", "body": "Python backend resume"},
        cover_letter="Hi",
        campaign_policy={"enabled": True, "real_apply": True, "min_score": 70},
        confirm=True,
        submit=True,
    )
    app.config["sources"]["hirehi"]["external_apply"] = {
        "certified": True,
        "level": 5,
        "session": "hirehi",
        "method": "POST",
    }
    app.save_config(app.config)
    blocked_url = app.confirm_external_apply(
        job_id,
        form={"form_url": "https://hirehi.ru/apply/ext-1", "fields": [{"name": "email"}]},
        resume_variant={"id": "rv1", "body": "Python backend resume"},
        cover_letter="Hi",
        campaign_policy={"enabled": True, "real_apply": True, "min_score": 70},
        confirm=True,
        submit=True,
    )

    assert blocked_session["status"] == "blocked"
    assert blocked_session["reason"] == "external_apply_session_missing"
    assert blocked_url["status"] == "blocked"
    assert blocked_url["reason"] == "external_apply_url_missing"


def test_certified_external_apply_submit_requires_certification_evidence(tmp_path):
    app = WorkHunter(root=tmp_path)
    job_id = _external_job(app, source="hirehi")
    _import_hirehi_session(app)
    _certify_hirehi(app, level=5, evidence=False)

    result = app.confirm_external_apply(
        job_id,
        form={"form_url": "https://hirehi.ru/apply/ext-1", "fields": [{"name": "email"}]},
        resume_variant={"id": "rv1", "body": "Python backend resume"},
        cover_letter="Hi",
        campaign_policy={"enabled": True, "real_apply": True, "min_score": 70},
        confirm=True,
        submit=True,
    )

    assert result["status"] == "blocked"
    assert result["reason"] == "external_source_requires_l5_certification"
    assert result["level"] == 2
    assert "external_apply_dry_run_missing" in result["readiness"]["blockers"]
    assert app.storage.get_application(job_id) is None


def test_certified_external_apply_submit_respects_kill_switch(tmp_path):
    app = WorkHunter(root=tmp_path)
    job_id = _external_job(app, source="hirehi")
    _import_hirehi_session(app)
    _certify_hirehi(app, level=5)
    app.pause_hh_agent(reason="operator_stop")
    calls: list[dict[str, object]] = []

    def fake_requester(method: str, url: str, *, headers: dict[str, str], data: str | None, timeout: int):
        calls.append({"method": method, "url": url, "headers": headers, "data": data})
        return ExternalHTTPResponse(status=201, headers={"content-type": "application/json"}, body='{"ok":true}')

    result = app.confirm_external_apply(
        job_id,
        form={"form_url": "https://hirehi.ru/apply/ext-1", "fields": [{"name": "email"}]},
        resume_variant={"id": "rv1", "body": "Python backend resume"},
        cover_letter="Hi",
        campaign_policy={"min_score": 70},
        confirm=True,
        submit=True,
        requester=fake_requester,
    )

    assert result["status"] == "blocked"
    assert result["reason"] == "campaign_kill_switch_paused"
    assert result["submit"] is False
    assert calls == []
    assert app.storage.get_application(job_id) is None


def test_external_campaign_policy_apply_requires_l6_certification(tmp_path):
    app = WorkHunter(root=tmp_path)
    job_id = _external_job(app, source="hirehi")
    _import_hirehi_session(app)
    _certify_hirehi(app, level=5)

    def fake_requester(method: str, url: str, *, headers: dict[str, str], data: str | None, timeout: int):
        return ExternalHTTPResponse(status=200, headers={"content-type": "application/json"}, body='{"ok":true}')

    blocked = app.confirm_external_apply(
        job_id,
        form={"form_url": "https://hirehi.ru/apply/ext-1", "fields": [{"name": "email"}]},
        cover_letter="Hi",
        campaign_policy={"enabled": True, "real_apply": True, "min_score": 70},
        confirm=True,
        submit=True,
        campaign_policy_apply=True,
        requester=fake_requester,
    )
    _certify_hirehi(app, level=6)
    applied = app.confirm_external_apply(
        job_id,
        form={"form_url": "https://hirehi.ru/apply/ext-1", "fields": [{"name": "email"}]},
        cover_letter="Hi",
        campaign_policy={"enabled": True, "real_apply": True, "min_score": 70},
        confirm=True,
        submit=True,
        campaign_policy_apply=True,
        requester=fake_requester,
    )

    assert blocked["status"] == "blocked"
    assert blocked["reason"] == "external_source_requires_l6_campaign_certification"
    assert applied["status"] == "external_applied"
    assert applied["campaign_policy_apply"] is True


def test_external_apply_web_api_exposes_dry_run(monkeypatch, tmp_path):
    def fail_fetch(url: str):
        raise AssertionError(f"external dry-run with a supplied form must not fetch {url}")

    monkeypatch.setattr("work_hunter.sources.public_boards.fetch_url", fail_fetch)
    app = WorkHunter(root=tmp_path)
    job_id = _external_job(app, source="jabka")

    with _server(tmp_path) as base:
        result = _post_json(
            base,
            f"/api/jobs/{job_id}/external-apply/dry-run",
            {
                "form": {
                    "form_url": "https://jabka.work/apply/ext-1",
                    "fields": [{"name": "email", "label": "Email"}],
                },
                "cover_letter": "Hi",
                "campaign_policy": {"min_score": 70},
            },
        )

    assert result["status"] == "dry_run_ready"
    assert result["submit"] is False
    assert result["source"] == "jabka"


class _server:
    def __init__(self, root):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(root))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return f"http://127.0.0.1:{self.server.server_port}"

    def __exit__(self, exc_type, exc, tb):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()


def _post_json(base: str, path: str, payload: dict):
    request = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))
