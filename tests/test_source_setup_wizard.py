from __future__ import annotations

import json
import threading
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

from work_hunter.services import WorkHunter
from work_hunter.web.server import make_handler


def test_source_setup_guide_classifies_hh_missing_preflight(tmp_path):
    app = WorkHunter(root=tmp_path)

    guide = app.source_setup_guide("hh")

    assert guide["source"] == "hh"
    assert guide["lane"] == "hh"
    assert guide["goal_status"] == "blocked"
    assert guide["can_certify"] is False
    assert guide["next_action"]["action"] == "preflight"
    assert "preflight" in guide["available_actions"]
    assert [step["id"] for step in guide["steps"]] == ["auth", "resumes", "preflight", "confirm_flow"]
    assert guide["steps"][0]["status"] == "blocked"
    assert guide["diagnostics"]["preflight"]["auth"]["status"] == "missing_access_token"


def test_source_setup_guide_reports_external_missing_evidence(tmp_path):
    app = WorkHunter(root=tmp_path)

    guide = app.source_setup_guide("getmatch", level=5)

    assert guide["source"] == "getmatch"
    assert guide["lane"] == "certifiable_external"
    assert guide["goal_status"] == "blocked"
    assert guide["can_certify"] is False
    assert guide["next_action"]["action"] == "har_import"
    assert "har_import" in guide["available_actions"]
    assert "certify" not in guide["available_actions"]
    assert {step["id"] for step in guide["steps"]} >= {"session", "target", "redaction", "dry_run", "tests", "certify"}
    assert "session" in guide["diagnostics"]["audit"]["missing"]
    assert "url" in guide["diagnostics"]["audit"]["missing"]


def test_source_setup_guide_marks_manual_sources_without_real_apply_promise(tmp_path):
    app = WorkHunter(root=tmp_path)

    guide = app.source_setup_guide("telegram")

    assert guide["source"] == "telegram"
    assert guide["lane"] == "manual_or_search_only"
    assert guide["goal_status"] == "manual_handoff"
    assert guide["can_certify"] is False
    assert "certify" not in guide["available_actions"]
    assert guide["next_action"]["action"] in {"source_sync", "source_status"}
    assert "manual handoff" in guide["diagnostics"]["note"].lower()


def test_source_setup_action_blocks_unsupported_actions(tmp_path):
    app = WorkHunter(root=tmp_path)

    result = app.source_setup_action("run_shell", source="hh")

    assert result["status"] == "blocked"
    assert result["reason"] == "unsupported_source_setup_action"
    assert result["submit"] is False


def test_source_setup_action_imports_har_configures_target_and_masks_secrets(tmp_path):
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
    app = WorkHunter(root=tmp_path)

    result = app.source_setup_action(
        "har_import",
        source="getmatch",
        path=str(har_path),
        allowed_hosts=["getmatch.ru"],
    )

    serialized = json.dumps(result, ensure_ascii=False)
    assert result["status"] == "ok"
    assert result["action"] == "har_import"
    assert result["result"]["status"] == "configured"
    assert result["guide"]["source"] == "getmatch"
    assert result["guide"]["next_action"]["action"] == "redaction_scan"
    assert "very-secret" not in serialized
    assert "resume-secret" not in serialized


def test_source_setup_web_api_exposes_guide_and_action(tmp_path):
    with _server(tmp_path) as base:
        guide = _get_json(base, "/api/source-setup/guide?source=hh")
        blocked = _post_json(base, "/api/source-setup/action", {"source": "hh", "action": "run_shell"})

    assert guide["lane"] == "hh"
    assert guide["next_action"]["action"] == "preflight"
    assert blocked["status"] == "blocked"
    assert blocked["reason"] == "unsupported_source_setup_action"


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
