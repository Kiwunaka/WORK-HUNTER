from __future__ import annotations

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

from work_hunter.models import Job, JobScore, Resume
from work_hunter.services import WorkHunter
from work_hunter.web.server import make_handler


def test_web_api_builds_resume_variant_after_confirmed_fact(tmp_path):
    app = WorkHunter(root=tmp_path)
    answer = app.answer_onboarding("experience", "FastAPI and PostgreSQL APIs.", source="test")
    app.confirm_candidate_fact(answer["facts"][0]["id"])
    job_id = app.storage.upsert_job(
        Job(source="geekjob", source_id="g1", url="u", title="FastAPI backend", description="FastAPI")
    )
    resume_id = app.storage.save_resume(Resume(name="Base", body="Python developer.", is_active=True))

    with _server(tmp_path) as base:
        result = _post_json(base, "/api/resume-variants/build", {"job_id": job_id, "resume_id": resume_id})

    assert result["status"] == "ready"
    assert "FastAPI" in result["body"]


def test_web_api_application_pack_preview_masks_secrets(tmp_path):
    app = WorkHunter(root=tmp_path)
    job_id = app.storage.upsert_job(Job(source="habr", source_id="h1", url="u", title="Python"))

    with _server(tmp_path) as base:
        result = _post_json(
            base,
            "/api/applications/build-pack",
            {
                "job_id": job_id,
                "resume_variant": {"id": "rv1", "body": "Python"},
                "cover_letter": "Hi",
                "source_payload": {
                    "form_signature": "known",
                    "headers": {"Authorization": "Bearer secret", "Cookie": "sid=secret"},
                },
            },
        )

    serialized = json.dumps(result, ensure_ascii=False)
    assert result["policy_status"] == "ready"
    assert "Bearer secret" not in serialized
    assert "sid=secret" not in serialized


def test_web_api_inbox_alias_lists_filtered_job_queue(tmp_path):
    app = WorkHunter(root=tmp_path)
    hh_id = app.storage.upsert_job(Job(source="hh", source_id="hh-1", url="https://hh.ru/vacancy/1", title="HH ready"))
    low_id = app.storage.upsert_job(Job(source="hh", source_id="hh-2", url="https://hh.ru/vacancy/2", title="HH low"))
    app.storage.upsert_job(Job(source="geekjob", source_id="g-1", url="https://geekjob.ru/vacancy/1", title="Other source"))
    app.storage.save_score(JobScore(job_id=hh_id, total_score=85))
    app.storage.save_score(JobScore(job_id=low_id, total_score=20))

    with _server(tmp_path) as base:
        result = _get_json(base, "/api/inbox?source=hh&min_score=70&limit=10")

    assert [job["title"] for job in result] == ["HH ready"]
    assert result[0]["source"] == "hh"
    assert result[0]["score"]["total_score"] == 85


def test_web_api_plans_hh_search_campaign_with_rich_filters(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    def fake_plan(self, **kwargs):
        captured.update(kwargs)
        return {"id": 77, "status": "planned", "counts": {"ready": 0}}

    monkeypatch.setattr("work_hunter.services.WorkHunter.plan_hh_search_campaign", fake_plan)

    with _server(tmp_path) as base:
        result = _post_json(
            base,
            "/api/hh/search-campaigns/plan",
            {
                "text": "python",
                "area": ["1"],
                "professional_role": ["96"],
                "limit": 10,
                "min_score": 70,
                "skip_tests": True,
                "ai_filter_mode": "light",
                "resume_id": "resume-1",
            },
        )

    assert result["id"] == 77
    assert captured["text"] == "python"
    assert captured["area"] == ["1"]
    assert captured["ai_filter_mode"] == "light"


def test_web_api_campaign_plan_forwards_ai_filter_mode_and_lists_runs_items(tmp_path):
    app = WorkHunter(root=tmp_path)
    job_id = app.storage.upsert_job(Job(source="hh", source_id="123", url="https://hh.ru/vacancy/123", title="Python"))
    app.storage.save_score(JobScore(job_id=job_id, total_score=95))

    with _server(tmp_path) as base:
        planned = _post_json(
            base,
            "/api/hh/campaigns/plan",
            {
                "limit": 10,
                "min_score": 70,
                "skip_tests": True,
                "ai_filter_mode": "heavy",
                "resume_id": "r1",
                "daily_cap": 2,
            },
        )
        runs = _get_json(base, "/api/hh/campaigns")
        detail = _get_json(base, f"/api/hh/campaigns/{planned['id']}")

    assert planned["filters"]["ai_filter_mode"] == "heavy"
    assert planned["filters"]["daily_cap"] == 2
    assert runs[0]["id"] == planned["id"]
    assert detail["run"]["id"] == planned["id"]
    assert detail["items"][0]["job"]["title"] == "Python"


def test_web_api_exposes_external_campaign_plan_endpoint(tmp_path):
    app = WorkHunter(root=tmp_path)
    app.storage.upsert_job(Job(source="hirehi", source_id="h1", url="https://hirehi.ru/jobs/h1", title="Python"))

    with _server(tmp_path) as base:
        result = _post_json(
            base,
            "/api/campaigns/external/plan",
            {"source": "hirehi", "limit": 10, "min_score": 70, "daily_cap": 1},
        )

    assert result["status"] == "blocked"
    assert result["source"] == "hirehi"
    assert result["reason"] in {"external_source_requires_l6_campaign_certification", "external_session_missing"}


def test_web_api_replay_run_and_source_capabilities_mask_secrets(tmp_path):
    app = WorkHunter(root=tmp_path)
    run_id = app.storage.create_hh_campaign_run(filters={"access_token": "secret"})
    app.record_replay_event(
        run_id=run_id,
        source="hh",
        event_type="policy_decision",
        title="Decision",
        data={"headers": {"Authorization": "Bearer secret"}},
    )

    with _server(tmp_path) as base:
        replay = _get_json(base, f"/api/replay/runs/{run_id}")
        capabilities = _get_json(base, "/api/source-capabilities")
        security = _get_json(base, "/api/security/status")

    serialized = json.dumps(replay, ensure_ascii=False)
    assert "Bearer secret" not in serialized
    assert replay["events"][0]["data"]["headers"]["Authorization"] == "***"
    assert capabilities["hh"]["level"] == 6
    assert security["source_capabilities"]["hh"]["can_real_apply"] is True


class _server:
    def __init__(self, root):
        self.root = root
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


def _post_json(base: str, path: str, payload: dict):
    request = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))
