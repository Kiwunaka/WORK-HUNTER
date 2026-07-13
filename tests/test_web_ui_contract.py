from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from work_hunter.models import Job, JobScore, Resume
from work_hunter.services import WorkHunter
from work_hunter.web.server import _compute_stats, _job_json, make_handler


STATIC_DIR = Path(__file__).resolve().parents[1] / "work_hunter" / "web" / "static"
UI_ROUTES = [
    "/",
    "/today",
    "/jobs",
    "/applications",
    "/calendar",
    "/favorites",
    "/chat",
    "/assistant",
    "/agent",
    "/analytics",
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


def _get_json(base: str, path: str) -> tuple[int, object]:
    try:
        status, _, body = _get_text(base, path)
        return status, json.loads(body)
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_stats_api_uses_one_application_status_mapping(tmp_path):
    app = WorkHunter(tmp_path)
    for index, status in enumerate(("applied", "response", "interview", "offer"), 1):
        job_id = app.storage.upsert_job(
            Job(source="x", source_id=str(index), url=f"https://x/{index}", title=status)
        )
        app.storage.set_status(job_id, status)
    stats = _compute_stats(app.list_jobs(limit=20), app.storage)
    funnel = {item["status"]: item["count"] for item in stats["application_funnel"]}
    assert stats["total_applications"] == 4
    assert funnel["applied"] == 1
    assert funnel["response"] == 1
    assert funnel["interview"] == 1
    assert funnel["offer"] == 1


def test_jobs_api_includes_bounded_note_preview(tmp_path):
    app = WorkHunter(tmp_path)
    job_id = app.storage.upsert_job(
        Job(source="x", source_id="noted", url="https://x/noted", title="Noted")
    )
    app.storage.save_note(job_id, "n" * 200)
    payload = _job_json(app.get_job(job_id), app.storage)
    assert payload["note_preview"] == "n" * 120


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
            assert '<script src="/ui-core.js"></script>' in body
            assert '<script src="/app.js"></script>' in body

        core_status, core_type, core_body = _get_text(base, "/ui-core.js")
        assert core_status == 200
        assert "javascript" in core_type
        assert "WorkHunterUI" in core_body

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


def test_resume_api_filters_by_valid_profile_and_preserves_default_route(tmp_path):
    app = WorkHunter(tmp_path)
    app.config["profiles"]["backend"] = {
        **app.config["profiles"]["default"],
        "name": "Backend",
    }
    app.save_config(app.config)
    app.storage.save_resume(Resume(name="Default CV", body="A", profile_id="default"))
    app.storage.save_resume(Resume(name="Backend CV", body="B", profile_id="backend"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(tmp_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        default_status, default_payload = _get_json(base, "/api/resumes")
        backend_status, backend_payload = _get_json(
            base, "/api/resumes?profile_id=backend"
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert default_status == 200
    assert [item["name"] for item in default_payload] == ["Default CV"]
    assert backend_status == 200
    assert [item["name"] for item in backend_payload] == ["Backend CV"]


def test_resume_api_rejects_unknown_profile(tmp_path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(tmp_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        status, payload = _get_json(base, "/api/resumes?profile_id=missing")
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert status == 400
    assert payload == {"error": "invalid_profile"}


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
    core = _read_static("ui-core.js")
    ids = re.findall(r'\bid="([^"]+)"', index)

    duplicates = sorted({item for item in ids if ids.count(item) > 1})
    assert duplicates == []
    assert 'id="letter-box"' not in index
    assert 'id="letter-box"' not in app
    assert 'id="ai-letter-box" data-letter-box' in index
    assert 'id="detail-letter-box" data-letter-box' in app
    legacy_routes = ["/jobs", "/calendar", "/favorites", "/chat", "/agent", "/settings", "/sources", "/stats", "/trends"]
    for path in legacy_routes:
        assert f'path: "{path}"' in app
    for path in ["/today", "/applications", "/assistant", "/analytics"]:
        assert f'"{path}"' in core
    assert "window.history.pushState" in app
    assert "window.addEventListener(\"popstate\"" in app


def test_ui_static_assets_have_no_remote_runtime_dependencies():
    combined = "\n".join(
        _read_static(name) for name in ("index.html", "app.js", "app.css")
    )
    for forbidden in (
        "fonts.googleapis.com",
        "fonts.gstatic.com",
        "unpkg.com",
        "@latest",
        "data-lucide",
        "refreshIcons",
    ):
        assert forbidden not in combined


def test_feedback_roots_and_live_regions_are_local_and_accessible():
    html = _read_static("index.html")

    assert 'id="toast-region"' in html
    assert 'aria-live="polite"' in html
    assert 'id="toast-alert-region"' in html
    assert 'aria-live="assertive"' in html
    assert 'id="sheet-root"' in html
    assert 'id="popover-root"' in html
    assert html.index('/ui-core.js') < html.index('/ui-feedback.js') < html.index('/app.js')


def test_ui_uses_feedback_controller_instead_of_browser_alerts():
    app = _read_static("app.js")

    assert "window.alert(" not in app
    assert not re.search(r"(?<![\w.])alert\(", app)


def test_onboarding_asset_and_accessible_wizard_contract():
    html = _read_static("index.html")
    onboarding = _read_static("ui-onboarding.js")

    assert html.index('/ui-feedback.js') < html.index('/ui-onboarding.js') < html.index('/app.js')
    assert 'work-hunter:onboarding:v2' in onboarding
    assert 'work-hunter:guidance-session:v2' in onboarding
    assert "deriveReadiness" in onboarding
    assert "createController" in onboarding
    assert 'data-onboarding-step' in onboarding
    assert 'id="onboarding-roles"' in onboarding
    assert 'id="onboarding-resume-body"' in onboarding


def test_primary_navigation_has_exactly_eight_russian_destinations_and_local_icons():
    index = _read_static("index.html")
    nav = re.search(r"<nav[^>]*>(.*?)</nav>", index, re.DOTALL)
    assert nav is not None
    markup = nav.group(1)

    assert len(re.findall(r'class="nav-button(?: active)?"', markup)) == 8
    for label in (
        "Сегодня",
        "Вакансии",
        "Отклики",
        "Календарь",
        "Ассистент",
        "Аналитика",
        "Источники",
        "Настройки",
    ):
        assert f">{label}<" in markup or label in markup
    assert markup.count("<svg") == 8
    assert "http://" not in markup
    assert "https://" not in markup


def test_shell_uses_system_typography_and_no_emoji_action_icons():
    index = _read_static("index.html")
    app = _read_static("app.js")
    css = _read_static("app.css")

    assert 'font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif' in css
    assert "📤" not in index + app


def test_destination_tabs_and_capability_ids_are_present():
    index = _read_static("index.html")
    app = _read_static("app.js")
    combined = index + app

    for tab in ("pipeline", "agent", "automation"):
        assert f'data-applications-tab="{tab}"' in index
    for tab in ("overview", "trends"):
        assert f'data-analytics-tab="{tab}"' in index
    for section in (
        "profile",
        "resumes",
        "search",
        "hh",
        "ai",
        "notifications",
        "appearance",
        "help",
        "advanced",
    ):
        assert f'data-settings-tab="{section}"' in index

    for action_id in (
        "agent.refresh",
        "agent.live-auth",
        "agent.research.plan",
        "agent.research.run",
        "event.create",
        "chat.send",
        "stats.refresh",
        "stats.export-csv",
        "trends.run",
        "profile.save",
        "resume.create",
        "config.save",
        "onboarding.restart",
        "hh.lab.run",
    ):
        assert f'data-action-id="{action_id}"' in combined


def test_destination_subview_switchers_are_url_synchronized():
    app = _read_static("app.js")

    assert "function switchApplicationsTab" in app
    assert "function switchAnalyticsTab" in app
    assert "function switchSettingsSection" in app
    assert 'window.history.pushState' in app


def test_static_and_dynamic_actions_avoid_inline_javascript():
    index = _read_static("index.html")
    app = _read_static("app.js")

    assert 'function requirePositiveInteger(value, fieldName = "id")' in app
    assert "Number.isSafeInteger(parsed)" in app
    assert re.search(r"\bonclick\s*=", f"{index}\n{app}", re.IGNORECASE) is None
    assert "data-ui-action" in index
    assert "handleDelegatedUiAction" in app
    assert "data-record-action" in app
    assert "handleDynamicRecordAction" in app


def test_ui_contract_keeps_trends_explicit_and_bulk_selection_stable():
    index = _read_static("index.html")
    app = _read_static("app.js")

    assert 'id="load-trends-button"' in index
    assert "loadMarketTrends()" not in index
    assert '"load-market-trends": () => loadMarketTrends()' in app
    assert 'data-record-action="toggle-job-select"' in app
    assert 'control.matches(".job-checkbox")' in app
    assert "selectedJobIds.has(jobId)" in app
    assert "syncBulkCheckboxes()" in app
    assert "research-and-apply" in app
    assert 'id="agent-research-output"' in index
    assert "/api/agent/preflight?live_auth=true" in app


def test_hh_api_lab_mutations_use_typed_safety_sheet():
    app = _read_static("app.js")

    assert 'const HH_LAB_MUTATING_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);' in app
    assert "function buildLabMutationDescriptor" in app
    assert 'operationType: "api_lab"' in app
    assert "openLiveAction(descriptor)" in app
    assert "execute: async (confirm)" in app


def test_agent_resume_update_ui_sends_literal_confirmation():
    app = _read_static("app.js")

    assert 'operation === "update-resumes"' in app
    assert 'operationType: "resume_account"' in app
    assert "confirm === true" in app
    assert "JSON.stringify({ operation, params })" in app


def test_live_mutations_do_not_use_browser_confirmation():
    app = _read_static("app.js")

    assert "window.confirm(" not in app
    assert not re.search(r"(?<![\w.])confirm\(", app)


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
    assert 'data-record-action="mark-ghost-job"' in app
    assert 'aria-label="Редактировать резюме ${escapeAttr(r.name)}"' in app
    assert 'aria-label="Отметить ghosted: ${escapeAttr(j.title)}"' in app
    assert '$("#save-resume-button").textContent = editingResumeId' in app
