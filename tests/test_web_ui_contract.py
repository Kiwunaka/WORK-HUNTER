from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from work_hunter.models import Job, JobScore
from work_hunter.services import WorkHunter
from work_hunter.web.server import make_handler


STATIC_DIR = Path(__file__).resolve().parents[1] / "work_hunter" / "web" / "static"
UI_ROUTES = [
    "/",
    "/jobs",
    "/calendar",
    "/favorites",
    "/chat",
    "/agent",
    "/settings",
    "/sources",
    "/stats",
    "/trends",
]


def _read_static(name: str) -> str:
    return (STATIC_DIR / name).read_text(encoding="utf-8")


def _get_text(base: str, path: str) -> tuple[int, str, str]:
    with urllib.request.urlopen(f"{base}{path}", timeout=5) as response:
        return response.status, response.headers.get("Content-Type", ""), response.read().decode("utf-8")


def _post_json(base: str, path: str, body: dict) -> tuple[int, dict]:
    request = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_ui_deep_links_and_static_assets_are_served(tmp_path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(tmp_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        for path in UI_ROUTES:
            status, content_type, body = _get_text(base, path)
            assert status == 200
            assert "text/html" in content_type
            assert "<title>Work Hunter</title>" in body
            assert '<script src="/app.js"></script>' in body

        js_status, js_type, js_body = _get_text(base, "/app.js")
        assert js_status == 200
        assert "javascript" in js_type
        assert "const ROUTES" in js_body

        css_status, css_type, css_body = _get_text(base, "/app.css")
        assert css_status == 200
        assert "text/css" in css_type
        assert ".sidebar" in css_body

        manifest_status, _, manifest_body = _get_text(base, "/manifest.json")
        assert manifest_status == 200
        assert json.loads(manifest_body)["name"] == "Work Hunter"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_ghost_jobs_route_handles_arbitrarily_large_days(tmp_path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(tmp_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"

        status, content_type, body = _get_text(
            base,
            "/api/ghost-jobs?days=10000000000000000000000000000000000000000",
        )

        assert status == 200
        assert "application/json" in content_type
        assert json.loads(body) == []
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_ghost_jobs_route_serializes_active_profile_score(tmp_path):
    app = WorkHunter(tmp_path)
    app.config["profiles"]["python"] = {}
    app.config["profile"] = "python"
    app.save_config(app.config)
    job_id = app.storage.upsert_job(
        Job(
            source="hh",
            source_id="ghost-profile",
            url="https://example.test/ghost-profile",
            title="Ghost Profile",
        )
    )
    app.storage.set_status(job_id, "applied")
    app.storage.save_application(job_id, "applied")
    app.storage.conn.execute(
        "UPDATE applications SET applied_at = ? WHERE job_id = ?",
        ("2020-01-01T00:00:00+00:00", job_id),
    )
    app.storage.conn.commit()
    app.storage.save_score(
        JobScore(job_id=job_id, profile_id="default", total_score=10)
    )
    app.storage.save_score(
        JobScore(job_id=job_id, profile_id="python", total_score=90)
    )

    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(tmp_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"

        status, content_type, body = _get_text(base, "/api/ghost-jobs?days=30")

        assert status == 200
        assert "application/json" in content_type
        jobs = json.loads(body)
        assert jobs[0]["score"]["profile_id"] == "python"
        assert jobs[0]["score"]["total_score"] == 90
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_resume_api_rejects_invalid_ats_score(tmp_path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(tmp_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        for ats_score in (True, "87", -1, 101, 87.5):
            status, body = _post_json(
                base,
                "/api/resumes",
                {
                    "name": "Invalid ATS",
                    "body": "Body",
                    "profile_id": "default",
                    "is_active": False,
                    "ats_score": ats_score,
                },
            )
            assert status == 400
            assert body == {
                "error": "ats_score must be an integer between 0 and 100 or null"
            }
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_resume_api_rejects_missing_update_target(tmp_path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(tmp_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        status, body = _post_json(
            base,
            "/api/resumes",
            {
                "id": 999,
                "name": "Stale CV",
                "body": "Unsaved draft",
                "profile_id": "default",
                "is_active": True,
                "ats_score": 64,
            },
        )

        assert status == 404
        assert body == {"error": "not_found"}
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_ui_static_contract_has_routes_and_no_duplicate_ids():
    index = _read_static("index.html")
    app = _read_static("app.js")
    ids = re.findall(r'\bid="([^"]+)"', index)

    duplicates = sorted({item for item in ids if ids.count(item) > 1})
    assert duplicates == []
    assert 'id="letter-box"' not in index
    assert 'id="letter-box"' not in app
    assert 'id="ai-letter-box" data-letter-box' in index
    assert 'id="detail-letter-box" data-letter-box' in app
    for path in UI_ROUTES[1:]:
        assert f'path: "{path}"' in app
    assert "window.history.pushState" in app
    assert "window.addEventListener(\"popstate\"" in app


def test_ui_contract_keeps_trends_explicit_and_bulk_selection_stable():
    index = _read_static("index.html")
    app = _read_static("app.js")

    assert 'id="load-trends-button"' in index
    assert "loadMarketTrends()" in index
    assert "addEventListener(\"click\", loadMarketTrends)" not in app
    assert "event.stopPropagation();toggleJobSelect" in app
    assert "selectedJobIds.has(Number(job.id))" in app
    assert "syncBulkCheckboxes()" in app
    assert "research-and-apply" in app
    assert 'id="agent-research-output"' in index
    assert "/api/agent/preflight?live_auth=true" in app


def test_hh_api_lab_mutations_require_double_confirmation():
    app = _read_static("app.js")

    assert 'const HH_LAB_MUTATING_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);' in app
    assert "function confirmHhLabMutation(payload)" in app
    assert "if (!confirmHhLabMutation(payload)) return;" in app
    assert "payload.confirm = true;" in app
    assert "Final confirmation: this can change your HH account." in app


def test_agent_resume_update_ui_sends_literal_confirmation():
    app = _read_static("app.js")

    assert 'operation === "update-resumes"' in app
    assert "Update your HH resumes now?" in app
    assert "params.confirm = true;" in app
    assert "JSON.stringify({ operation, params })" in app


def test_resume_edit_and_ghost_actions_keep_explicit_record_identity():
    app = _read_static("app.js")

    assert "resumes: []" in app
    assert "function resetResumeForm()" in app
    assert "function resumePayloadFromForm()" in app
    assert "const original = state.resumes.find" in app
    assert "profile_id: original?.profile_id" in app
    assert "is_active: original?.is_active === true" in app
    assert "ats_score: original?.ats_score ?? null" in app
    assert "state.resumes = resumes;" in app
    assert "async function updateJobStatus(jobId, status)" in app
    assert "await api(`/api/jobs/${id}/apply`" in app
    assert "await api(`/api/jobs/${id}/record-event`" in app
    assert "async function markGhostJob(jobId)" in app
    assert 'onclick="markGhostJob(${j.id})"' in app
    assert 'aria-label="Редактировать резюме ${escapeAttr(r.name)}"' in app
    assert 'aria-label="Отметить ghosted: ${escapeAttr(j.title)}"' in app
    assert '$("#save-resume-button").textContent = editingResumeId' in app
