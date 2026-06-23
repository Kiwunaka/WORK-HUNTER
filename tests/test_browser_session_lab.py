from __future__ import annotations

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

from work_hunter.browser.profiles import browser_profile_path
from work_hunter.browser.redaction import redact_browser_payload
from work_hunter.browser.session_store import ensure_session_inside_workspace, session_path_for_source
from work_hunter.services import WorkHunter
from work_hunter.web.server import make_handler


def test_browser_lab_open_login_plan_creates_profile_and_status(tmp_path):
    app = WorkHunter(root=tmp_path)

    plan = app.browser_lab_open_login("getmatch")
    status = app.browser_lab_status("getmatch")

    assert plan["status"] == "planned"
    assert plan["source"] == "getmatch"
    assert plan["login_url"].startswith("https://getmatch.ru")
    assert "--user-data-dir" in " ".join(plan["command"])
    assert "getmatch" in plan["profile_dir"]
    assert status["profile_exists"] is True
    assert status["session_state"] == "profile_ready"


def test_browser_session_safety_helpers_keep_profiles_local_and_redacted(tmp_path):
    profile = browser_profile_path(tmp_path, "getmatch")
    session = session_path_for_source(tmp_path, "getmatch")
    redacted = redact_browser_payload(
        {
            "headers": {
                "Authorization": "Bearer secret",
                "Cookie": "sid=secret",
                "X-XSRF-Token": "xsrf",
            }
        }
    )

    assert profile == tmp_path / ".work-hunter" / "browser-profiles" / "getmatch"
    assert session == tmp_path / ".work-hunter" / "browser-sessions" / "getmatch.json"
    assert ensure_session_inside_workspace(tmp_path, session) is True
    assert ensure_session_inside_workspace(tmp_path, tmp_path.parent / "session.json") is False
    assert redacted["headers"] == {"Authorization": "***", "Cookie": "***", "X-XSRF-Token": "***"}


def test_browser_network_recorder_persists_redacted_recording(tmp_path):
    from work_hunter.browser.network_recorder import save_browser_recording

    app = WorkHunter(root=tmp_path)

    recorded = save_browser_recording(
        app.storage,
        source="hirehi",
        recording={
            "url": "https://hirehi.ru/apply?access_token=secret",
            "headers": {"Cookie": "sid=secret", "Authorization": "Bearer secret"},
            "steps": [{"type": "fill", "value": "client_secret=secret"}],
        },
    )
    rows = app.storage.list_browser_recordings(source="hirehi")
    serialized = json.dumps({"recorded": recorded, "rows": rows}, ensure_ascii=False)

    assert recorded["status"] == "recorded"
    assert rows[0]["id"] == recorded["id"]
    assert rows[0]["source"] == "hirehi"
    assert rows[0]["recording"]["headers"] == {"Cookie": "***", "Authorization": "***"}
    assert "sid=secret" not in serialized
    assert "Bearer secret" not in serialized
    assert "access_token=secret" not in serialized
    assert "client_secret=secret" not in serialized


def test_browser_lab_import_har_redacts_and_updates_session_status(tmp_path):
    har_path = tmp_path / "session.har"
    har_path.write_text(
        json.dumps(
            {
                "log": {
                    "entries": [
                        {
                            "request": {
                                "method": "GET",
                                "url": "https://hirehi.ru/api/search/jobs?access_token=secret",
                                "headers": [
                                    {"name": "Cookie", "value": "sessionid=very-secret"},
                                    {"name": "Authorization", "value": "Bearer secret"},
                                ],
                            },
                            "response": {
                                "status": 200,
                                "headers": [],
                                "content": {
                                    "mimeType": "application/json",
                                    "text": '{"token":"secret","items":[]}',
                                },
                            },
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    app = WorkHunter(root=tmp_path)

    imported = app.browser_lab_import_har("hirehi", har_path, allowed_hosts={"hirehi.ru"})
    status = app.browser_lab_status("hirehi")
    serialized = json.dumps(imported, ensure_ascii=False)

    assert imported["status"] == "imported"
    assert imported["session"]["headers"]["hirehi.ru"]["Cookie"] == "***"
    assert "very-secret" not in serialized
    assert "Bearer secret" not in serialized
    assert imported["recon"]["endpoints"][0]["url"] == "https://hirehi.ru/api/search/jobs?access_token=%2A%2A%2A"
    assert status["session_state"] == "har_session_imported"


def test_getmatch_har_import_configures_external_apply_target_from_recon(tmp_path):
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
                                "headers": [
                                    {"name": "Cookie", "value": "sid=very-secret"},
                                    {"name": "Content-Type", "value": "application/json"},
                                ],
                                "postData": {
                                    "text": json.dumps(
                                        {
                                            "offer_id": "34397",
                                            "cover_letter": "Hi from candidate",
                                            "resume_id": "resume-secret",
                                        }
                                    )
                                },
                            },
                            "response": {
                                "status": 201,
                                "headers": [],
                                "content": {"mimeType": "application/json", "text": '{"application_id":"app-1"}'},
                            },
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    app = WorkHunter(root=tmp_path)

    result = app.configure_source_external_apply_from_har(
        "getmatch",
        har_path,
        allowed_hosts={"getmatch.ru"},
        level=5,
    )

    target = result["configured"]["target"]
    serialized = json.dumps(result, ensure_ascii=False)
    assert result["status"] == "configured"
    assert result["source"] == "getmatch"
    assert result["apply_endpoint"]["method"] == "POST"
    assert target["url"] == "https://getmatch.ru/api/applications"
    assert target["payload_template"] == {
        "offer_id": "{source_id}",
        "cover_letter": "{cover_letter}",
        "resume_id": "{resume_id}",
    }
    assert result["configured"]["audit"]["missing"] == ["tests", "replay", "redaction", "dry_run"]
    assert app.config["sources"]["getmatch"]["external_apply"]["session"] == "getmatch"
    assert "very-secret" not in serialized
    assert "resume-secret" not in serialized


def test_browser_lab_form_mapping_and_dry_run_never_submit_unknown_forms(tmp_path):
    app = WorkHunter(root=tmp_path)
    form = {
        "form_url": "https://hirehi.ru/apply/1",
        "fields": [
            {"name": "email", "label": "Email", "required": True},
            {"name": "cover_letter", "label": "Cover letter", "required": True},
            {"name": "screening_task", "label": "Solve this test", "required": True},
        ],
    }
    persona = {"facts": {"email": "me@example.test", "summary": "Python backend developer"}}

    mapped = app.browser_lab_map_form(form, source="hirehi", persona=persona)
    dry_run = app.browser_lab_dry_run_form_fill(form, source="hirehi", persona=persona)

    assert mapped["status"] == "needs_manual_review"
    assert [field["name"] for field in mapped["unknown_fields"]] == ["screening_task"]
    assert dry_run["status"] == "blocked_manual_review"
    assert dry_run["submit"] is False
    assert dry_run["actions"][0]["action"] == "fill"
    assert dry_run["actions"][0]["selector"] == "[name='email']"
    assert "unknown_form_fields" in dry_run["risk_flags"]


def test_browser_lab_dry_run_escapes_selectors_and_redacts_action_values(tmp_path):
    app = WorkHunter(root=tmp_path)
    form = {
        "form_url": "https://hirehi.ru/apply/2",
        "fields": [
            {"name": "cover'letter\\field", "label": "Cover", "required": True},
        ],
    }

    dry_run = app.browser_lab_dry_run_form_fill(
        form,
        source="hirehi",
        extra_answers={"cover'letter\\field": "access_token=browser-secret"},
    )

    assert dry_run["status"] == "dry_run_ready"
    assert dry_run["actions"][0]["selector"] == "[name='cover\\'letter\\\\field']"
    assert dry_run["actions"][0]["value"] == "access_token=***"
    assert "browser-secret" not in json.dumps(dry_run, ensure_ascii=False)


def test_browser_lab_executor_runs_fill_screenshots_without_submit(tmp_path):
    app = WorkHunter(root=tmp_path)
    calls = []

    class FakeExecutor:
        def execute(self, **kwargs):
            calls.append(kwargs)
            return {
                "status": "ok",
                "actions_executed": len(kwargs["actions"]),
                "screenshots": {
                    "before_fill": str(tmp_path / "before.png"),
                    "after_fill": str(tmp_path / "after.png"),
                },
            }

    result = app.browser_lab_execute_dry_run_form_fill(
        {
            "form_url": "https://hirehi.ru/apply/3",
            "fields": [{"name": "email", "label": "Email", "required": True}],
        },
        source="hirehi",
        persona={"facts": {"email": "me@example.test"}},
        executor=FakeExecutor(),
    )

    assert result["status"] == "executed_dry_run"
    assert result["submit"] is False
    assert calls
    assert calls[0]["form_url"] == "https://hirehi.ru/apply/3"
    assert calls[0]["actions"][0]["selector"] == "[name='email']"
    assert calls[0]["actions"][0]["value"] == "me@example.test"
    assert result["executor"]["actions_executed"] == 1
    assert "before.png" in result["executor"]["screenshots"]["before_fill"]

    app.configure_source_external_apply_target(
        "hirehi",
        session="hirehi",
        url="https://hirehi.ru/apply/{source_id}",
        payload_template={"jobId": "{source_id}"},
    )
    app.record_source_redaction_scan("hirehi", payload={"headers": {"Authorization": "Bearer secret"}})
    audit = app.source_certification_audit(
        "hirehi",
        evidence={
            "tests": {"status": "passed", "command": "pytest hirehi-browser-lab"},
        },
    )

    assert audit["missing"] == []
    assert audit["evidence"]["dry_run"]["event_type"] == "browser_lab_form_execute_dry_run"
    assert audit["evidence"]["dry_run"]["status"] == "executed_dry_run"


def test_browser_lab_import_har_without_allowed_hosts_does_not_create_session(tmp_path):
    har_path = tmp_path / "evil.har"
    har_path.write_text(
        json.dumps(
            {
                "log": {
                    "entries": [
                        {
                            "request": {
                                "method": "GET",
                                "url": "https://evil.example/api/apply",
                                "headers": [{"name": "Cookie", "value": "sid=evil-secret"}],
                            },
                            "response": {"status": 200, "headers": [], "content": {"mimeType": "application/json", "text": "{}"}},
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    app = WorkHunter(root=tmp_path)

    imported = app.browser_lab_import_har("hirehi", har_path, allowed_hosts={"hirehi.ru"})
    status = app.browser_lab_status("hirehi")

    assert imported["status"] == "blocked"
    assert imported["reason"] == "no_allowed_har_entries"
    assert status["session_state"] == "missing"
    assert "evil-secret" not in json.dumps(imported, ensure_ascii=False)


def test_browser_lab_web_api_exposes_status_plan_and_dry_run(tmp_path):
    with _server(tmp_path) as base:
        plan = _post_json(base, "/api/browser-lab/open-login", {"source": "jabka"})
        status = _get_json(base, "/api/browser-lab/status?source=jabka")
        dry_run = _post_json(
            base,
            "/api/browser-lab/forms/dry-run",
            {
                "source": "jabka",
                "form": {"form_url": "https://jabka.work/apply", "fields": [{"name": "email", "label": "Email"}]},
                "persona": {"facts": {"email": "me@example.test"}},
            },
        )

    assert plan["status"] == "planned"
    assert status["profile_exists"] is True
    assert dry_run["status"] == "dry_run_ready"
    assert dry_run["submit"] is False


def test_browser_lab_web_api_import_har_can_configure_external_apply_target(tmp_path):
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
                                "headers": [{"name": "Cookie", "value": "sid=very-secret"}],
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

    with _server(tmp_path) as base:
        result = _post_json(
            base,
            "/api/browser-lab/import-har",
            {
                "source": "getmatch",
                "path": str(har_path),
                "allowed_hosts": ["getmatch.ru"],
                "configure_external_apply": True,
            },
        )

    serialized = json.dumps(result, ensure_ascii=False)
    assert result["status"] == "configured"
    assert result["configured"]["target"]["url"] == "https://getmatch.ru/api/applications"
    assert result["configured"]["target"]["payload_template"]["offer_id"] == "{source_id}"
    assert result["configured"]["audit"]["missing"] == ["tests", "replay", "redaction", "dry_run"]
    assert "very-secret" not in serialized
    assert "resume-secret" not in serialized


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


def _post_json(base: str, path: str, payload: dict):
    request = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))
