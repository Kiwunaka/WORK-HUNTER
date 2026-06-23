from __future__ import annotations

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

from work_hunter.external_sessions import import_external_session_from_har
from work_hunter.models import Job, JobScore, Resume
from work_hunter.services import WorkHunter
from work_hunter.web.server import make_handler


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


def _external_apply_evidence() -> dict[str, object]:
    return {
        "tests": {"status": "passed", "command": "python -m pytest tests/test_hirehi_apply.py"},
        "replay": {"status": "passed", "run_id": 77},
        "redaction": {"status": "passed", "scan_id": "hirehi-redaction"},
        "dry_run": {"status": "dry_run_ready", "event_id": 88},
    }


def test_generic_source_and_browser_api_aliases(tmp_path):
    with _server(tmp_path) as base:
        source_status = _get_json(base, "/api/sources/status")
        source_sync = _post_json(base, "/api/sources/geekjob/sync", {"limit": 0})
        browser_status = _get_json(base, "/api/browser/status?source=getmatch")
        browser_setup = _post_json(base, "/api/browser/setup", {"source": "getmatch"})
        browser_login = _post_json(base, "/api/browser/open-login", {"source": "getmatch"})
        browser_check = _post_json(base, "/api/browser/check-session", {"source": "getmatch"})
        browser_dry_run = _post_json(
            base,
            "/api/browser/dry-run",
            {
                "source": "getmatch",
                "form": {"form_url": "https://getmatch.ru/apply", "fields": [{"name": "email"}]},
                "persona": {"facts": {"email": "me@example.test"}},
            },
        )

    assert source_status["hh"]["level"] == 6
    assert source_sync["geekjob"]["status"] == "planned"
    assert source_sync["geekjob"]["dry_run"] is True
    assert browser_status["source"] == "getmatch"
    assert browser_setup["status"] == "ready"
    assert browser_setup["source"] == "getmatch"
    assert browser_setup["launched"] is False
    assert browser_setup["profile_exists"] is True
    assert browser_login["status"] == "planned"
    assert browser_check["source"] == "getmatch"
    assert browser_dry_run["status"] == "dry_run_ready"
    assert browser_dry_run["submit"] is False


def test_generic_campaign_api_aliases_plan_review_pause_and_replay(tmp_path):
    with _server(tmp_path) as base:
        preset = _post_json(
            base,
            "/api/campaigns/presets",
            {"name": "main-python", "params": {"limit": 5, "min_score": 70, "skip_tests": True}},
        )
        presets = _get_json(base, "/api/campaigns/presets")
        planned = _post_json(base, "/api/campaigns/plan", {"preset": "main-python", "dry_run": True})
        enabled = _post_json(base, f"/api/campaigns/{planned['id']}/enable", {})
        review = _post_json(base, f"/api/campaigns/{planned['id']}/review", {})
        blocked_run = _post_json(base, f"/api/campaigns/{planned['id']}/run", {"real": False})
        paused = _post_json(base, f"/api/campaigns/{planned['id']}/pause", {"reason": "test"})
        resumed = _post_json(base, f"/api/campaigns/{planned['id']}/resume", {"reason": "resume test"})
        killed = _post_json(base, f"/api/campaigns/{planned['id']}/kill", {"reason": "kill test"})
        timeline = _get_json(base, f"/api/campaigns/{planned['id']}/timeline")
        replay_runs = _get_json(base, "/api/replay/runs")

    assert preset["name"] == "main-python"
    assert presets[0]["name"] == "main-python"
    assert planned["status"] == "planned"
    assert planned["filters"]["min_score"] == 70
    assert enabled["run"]["status"] == "enabled"
    assert review["run"]["id"] == planned["id"]
    assert blocked_run["status"] == "blocked"
    assert paused["paused"] is True
    assert resumed["paused"] is False
    assert killed["status"] == "killed"
    assert timeline["run"]["id"] == planned["id"]
    assert replay_runs[0]["id"] == planned["id"]


def test_generic_campaign_api_real_run_requires_enable_gate(monkeypatch, tmp_path):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHCampaignClient)
    FakeHHCampaignClient.apply_calls = []
    app = WorkHunter(root=tmp_path)
    job_id = app.storage.upsert_job(
        Job(source="hh", source_id="1", url="https://hh.ru/vacancy/1", title="Python")
    )
    app.storage.save_score(JobScore(job_id=job_id, total_score=95))
    planned = app.plan_hh_campaign(limit=5, min_score=70)

    with _server(tmp_path) as base:
        blocked = _post_json(base, f"/api/campaigns/{planned['id']}/run", {"real": True})
        enabled = _post_json(base, f"/api/campaigns/{planned['id']}/enable", {})

    assert blocked["status"] == "blocked"
    assert blocked["reason"] == "real_apply_requires_campaign_policy"
    assert blocked["run_status"] == "planned"
    assert blocked["submit"] is False
    assert enabled["run"]["status"] == "enabled"
    assert FakeHHCampaignClient.apply_calls == []


def test_generic_external_campaign_api_alias_runs_with_explicit_confirmation_gate(tmp_path):
    app = WorkHunter(root=tmp_path)
    app.storage.upsert_job(
        Job(
            source="hirehi",
            source_id="h1",
            url="https://hirehi.example/jobs/h1",
            title="Python backend",
            description="Python",
        )
    )
    app.storage.save_resume(Resume(name="Base", body="Python developer.", is_active=True))
    har_path = tmp_path / "hirehi.har"
    har_path.write_text(
        '{"log":{"entries":[{"request":{"url":"https://hirehi.example/api/applications","headers":[{"name":"Cookie","value":"sid=secret"}]}}]}}',
        encoding="utf-8",
    )
    import_external_session_from_har(tmp_path, "hirehi", har_path, allowed_hosts={"hirehi.example"})
    app.config["sources"]["hirehi"]["external_apply"] = {
        "certified": True,
        "level": 6,
        "session": "hirehi",
        "method": "POST",
        "url": "https://hirehi.example/api/applications",
        "payload_template": {"jobId": "{source_id}", "coverLetter": "{cover_letter}"},
        "evidence": _external_apply_evidence(),
    }
    app.save_config(app.config)

    with _server(tmp_path) as base:
        planned = _post_json(base, "/api/campaigns/external/plan", {"source": "hirehi", "limit": 10})
        blocked = _post_json(base, f"/api/campaigns/{planned['id']}/run-external", {"confirm": False})

    assert planned["status"] == "planned"
    assert planned["filters"]["mode"] == "external_campaign"
    assert blocked["status"] == "blocked"
    assert blocked["id"] == planned["id"]
    assert "confirmation" in blocked["message"].lower()


def test_generic_replay_event_screenshot_serves_local_file(tmp_path):
    app = WorkHunter(root=tmp_path)
    screenshot = tmp_path / ".work-hunter" / "browser_screenshots" / "hirehi" / "before.png"
    screenshot.parent.mkdir(parents=True, exist_ok=True)
    screenshot.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    event_id = app.record_replay_event(
        source="hirehi",
        event_type="browser_screenshot",
        title="Screenshot",
        data={"screenshots": {"before": str(screenshot)}},
    )

    with _server(tmp_path) as base:
        body, content_type = _get_bytes(base, f"/api/replay/events/{event_id}/screenshot?name=before")

    assert body.startswith(b"\x89PNG")
    assert content_type == "image/png"


def test_generic_candidate_and_resume_api_aliases(tmp_path):
    app = WorkHunter(root=tmp_path)
    job_id = app.storage.upsert_job(
        Job(source="geekjob", source_id="g1", url="https://geekjob.ru/vacancy/g1", title="FastAPI backend", description="FastAPI")
    )
    resume_id = app.storage.save_resume(Resume(name="Base", body="Python developer.", is_active=True))

    with _server(tmp_path) as base:
        profile = _post_json(base, "/api/candidate/profile", {"must_have_skills": ["FastAPI"]})
        fact = _post_json(
            base,
            "/api/candidate/facts",
            {"category": "experience", "key": "experience", "value": "FastAPI services.", "source": "api_test"},
        )
        confirmed = _post_json(base, "/api/onboarding/confirm-fact", {"fact_id": fact["facts"][0]["id"]})
        rejected_fact = _post_json(
            base,
            "/api/candidate/facts",
            {"category": "constraints", "key": "constraints", "value": "temporary", "source": "api_test"},
        )
        rejected = _post_json(base, "/api/onboarding/reject-fact", {"fact_id": rejected_fact["facts"][0]["id"]})
        variant = _post_json(base, "/api/resumes/build-variant", {"job_id": job_id, "resume_id": resume_id})
        variants = _get_json(base, "/api/resumes/variants")
        diff = _get_json(base, f"/api/resumes/variants/{variant['id']}/diff")

    assert profile["must_have_skills"] == ["FastAPI"]
    assert fact["status"] == "recorded"
    assert confirmed["status"] == "confirmed"
    assert rejected["status"] == "rejected"
    assert variant["status"] == "ready"
    assert variants[0]["id"] == variant["id"]
    assert "FastAPI services" in diff["diff"]


def test_generic_application_api_aliases_preview_dry_run_and_apply_block(tmp_path):
    app = WorkHunter(root=tmp_path)
    job_id = app.storage.upsert_job(
        Job(source="jabka", source_id="j1", url="https://jabka.work/jobs/j1", title="Python backend", description="Python")
    )

    with _server(tmp_path) as base:
        pack = _post_json(
            base,
            "/api/applications/build-pack",
            {
                "job_id": job_id,
                "resume_variant": {"id": "rv1", "body": "Python developer."},
                "cover_letter": "Hi",
                "source_payload": {"form_signature": "known"},
            },
        )
        detail = _get_json(base, f"/api/applications/{pack['id']}")
        preview = _post_json(base, f"/api/applications/{pack['id']}/preview", {})
        dry_run = _post_json(base, f"/api/applications/{pack['id']}/dry-run", {})
        blocked_apply = _post_json(base, f"/api/applications/{pack['id']}/apply", {"confirm": False})

    assert detail["id"] == pack["id"]
    assert preview["status"] == "preview"
    assert preview["submit"] is False
    assert dry_run["status"] == "dry_run_ready"
    assert dry_run["submit"] is False
    assert blocked_apply["status"] == "blocked"
    assert blocked_apply["reason"] == "confirmation_required"


def test_generic_setup_ai_source_browser_and_resume_utility_aliases(tmp_path):
    resume_path = tmp_path / "resume.md"
    resume_path.write_text("# Resume\n\nPython backend developer.", encoding="utf-8")

    with _server(tmp_path) as base:
        init_status = _get_json(base, "/api/init/status")
        init_report = _post_json(base, "/api/init", {"check": True, "with_ai": True, "dry_run": True})
        redaction = _post_json(
            base,
            "/api/init/redaction-scan",
            {
                "text": "Authorization: Bearer secret-token\nclient_secret=secret-token\nhttps://example.test/?access_token=abc",
                "headers": {"Cookie": "sid=secret"},
            },
        )
        ai_config = _post_json(
            base,
            "/api/ai/config",
            {"routes": {"openrouter": {"api_key": "sk-secret", "model": "openrouter/test"}}},
        )
        ai_run = _post_json(base, "/api/ai/run", {"route": "smart", "prompt": "ping", "dry_run": True})
        ai_runs = _get_json(base, "/api/ai/runs")
        generated = _post_json(base, "/api/onboarding/generate-profile", {})
        parsed_resume = _post_json(base, "/api/resumes/parse", {"path": str(resume_path)})
        source_test = _post_json(base, "/api/sources/hirehi/test", {})
        promoted = _post_json(base, "/api/sources/hirehi/promote-maturity", {"level": 5, "session": "hirehi"})
        promoted_with_evidence = _post_json(
            base,
            "/api/sources/hirehi/promote-maturity",
            {
                "level": 5,
                "session": "hirehi",
                "url": "https://hirehi.example/apply",
                "evidence": _external_apply_evidence(),
            },
        )
        record_flow = _post_json(base, "/api/browser/record-flow", {"source": "hirehi"})

    serialized_redaction = json.dumps(redaction, ensure_ascii=False)
    serialized_ai_config = json.dumps(ai_config, ensure_ascii=False)
    assert init_status["readiness"]["environment"]["python"] == "ok"
    assert init_status["readiness"]["safety"]["redaction_scan"] == "available"
    assert init_report["status"] in {"ok", "warning"}
    assert "secret-token" not in serialized_redaction
    assert "access_token=abc" not in serialized_redaction
    assert redaction["status"] == "ok"
    assert "sk-secret" not in serialized_ai_config
    assert ai_run["status"] == "dry_run"
    assert ai_runs[0]["tool_name"] == "ai_run"
    assert ai_runs[0]["route"] == "smart"
    assert ai_runs[0]["status"] == "dry_run"
    assert ai_runs[0]["output"]["status"] == "dry_run"
    assert generated["status"] in {"complete", "incomplete"}
    assert parsed_resume["status"] == "imported"
    assert source_test["source"] == "hirehi"
    assert source_test["status"] == "ok"
    assert promoted["hirehi"]["level"] == 2
    assert promoted["hirehi"]["can_real_apply"] is False
    assert promoted["hirehi"]["promotion"]["status"] == "blocked"
    assert promoted["hirehi"]["promotion"]["audit"]["missing"] == ["url", "tests", "replay", "redaction", "dry_run"]
    assert promoted_with_evidence["hirehi"]["level"] == 2
    assert promoted_with_evidence["hirehi"]["can_real_apply"] is False
    assert promoted_with_evidence["hirehi"]["promotion"]["status"] == "blocked"
    assert promoted_with_evidence["hirehi"]["promotion"]["audit"]["missing"] == ["replay", "redaction", "dry_run"]
    assert record_flow["status"] == "planned"
    assert record_flow["submit"] is False


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


def _get_json(base: str, path: str):
    with urllib.request.urlopen(f"{base}{path}", timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _get_bytes(base: str, path: str):
    with urllib.request.urlopen(f"{base}{path}", timeout=5) as response:
        return response.read(), response.headers.get_content_type()


def _post_json(base: str, path: str, payload: dict):
    request = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))
