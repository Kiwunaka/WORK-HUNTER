from __future__ import annotations

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

from work_hunter.models import HHAIDecision, HHPendingMessage
from work_hunter.services import WorkHunter
from work_hunter.web.server import make_handler


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


def test_hh_agent_service_exposes_preflight_digest_operations_and_approvals(tmp_path):
    app = WorkHunter(root=tmp_path)
    decision_id = app.storage.save_hh_ai_decision(
        HHAIDecision(
            action_type="apply",
            target_id="vac-1",
            model="test-model",
            confidence=0.62,
            reasons=["borderline"],
            raw_result={"score": 62},
        )
    )
    pending_id = app.storage.create_hh_pending_message(
        HHPendingMessage(
            action_type="apply",
            payload={"vacancy_id": "vac-1", "letter": "Hello"},
            confidence=0.62,
            reason="low_confidence",
            ai_decision_id=decision_id,
        )
    )

    preflight = app.hh_agent_preflight()
    digest = app.hh_agent_digest(limit=5)
    run = app.run_hh_agent_operation("digest", {"limit": 5})
    modified = app.modify_hh_approval(
        pending_id,
        instruction="make it warmer",
        payload_patch={"letter": "Warmer hello"},
    )
    approved = app.approve_hh_approval(pending_id, reason="looks good")
    operations = app.list_hh_agent_operations()

    assert preflight["status"] == "blocked"
    assert preflight["counts"]["pending_approvals"] == 1
    assert digest["approvals"]["total"] == 1
    assert digest["ai_decisions"]["total"] == 1
    assert run["operation_id"] > 0
    assert run["result"]["status"] == "ok"
    assert modified["status"] == "modified"
    assert modified["payload"]["modify_instruction"] == "make it warmer"
    assert approved["status"] == "approved"
    assert approved["reason"] == "looks good"
    assert operations["runs"][0]["tool_name"] == "hh_agent:digest"
    assert operations["logs"]


def test_hh_agent_service_manages_operation_status_templates_and_blacklist(tmp_path):
    app = WorkHunter(root=tmp_path)
    run_id = app.storage.start_hh_agent_mcp_run("hh_agent:long-running", {"kind": "test"})

    status = app.hh_agent_operation_status(run_id)
    cancelled = app.cancel_hh_agent_operation(run_id, reason="user_cancelled")
    paused_state = app.pause_hh_agent(reason="test")
    blocked_run = app.run_hh_agent_operation("sync-negotiations", {})
    resumed_state = app.resume_hh_agent(reason="test")
    template = app.save_hh_letter_template(name="warm", body="Hello {name}")
    blacklist_item = app.save_hh_employer_blacklist(
        employer_id="emp-1",
        employer_name="Acme",
        reason="spam",
    )
    templates = app.list_hh_letter_templates()
    blacklist = app.list_hh_employer_blacklist()
    deleted_template = app.delete_hh_letter_template("warm")
    deleted_blacklist = app.delete_hh_employer_blacklist("emp-1")

    assert status["operation"]["status"] == "running"
    assert cancelled["operation"]["status"] == "cancelled"
    assert cancelled["operation"]["output"]["reason"] == "user_cancelled"
    assert cancelled["logs"][0]["level"] == "warning"
    assert paused_state["paused"] is True
    assert blocked_run["status"] == "paused"
    assert resumed_state["paused"] is False
    assert template["name"] == "warm"
    assert templates[0]["body"] == "Hello {name}"
    assert blacklist_item["employer_id"] == "emp-1"
    assert blacklist[0]["reason"] == "spam"
    assert deleted_template == {"status": "ok", "deleted": 1}
    assert deleted_blacklist == {"status": "ok", "deleted": 1}


def test_hh_agent_web_api_exposes_cockpit_and_approval_queue(tmp_path):
    app = WorkHunter(root=tmp_path)
    pending_id = app.storage.create_hh_pending_message(
        HHPendingMessage(
            action_type="reply",
            payload={"negotiation_id": "neg-1", "message": "Hello"},
            confidence=0.51,
            reason="manual_review",
        )
    )
    running_id = app.storage.start_hh_agent_mcp_run("hh_agent:queued", {})
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(tmp_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        preflight = _get_json(base, "/api/agent/preflight")
        digest = _get_json(base, "/api/agent/digest?limit=5")
        approvals = _get_json(base, "/api/approvals?status=pending")
        modified = _post_json(
            base,
            f"/api/approvals/{pending_id}/modify",
            {
                "instruction": "shorten",
                "payload_patch": {"message": "Short hello"},
            },
        )
        approved = _post_json(
            base,
            f"/api/agent/approvals/{pending_id}/approve",
            {"reason": "approved in cockpit"},
        )
        run = _post_json(base, "/api/agent/run", {"operation": "digest", "params": {"limit": 5}})
        paused = _post_json(base, "/api/agent/pause", {"reason": "telegram"})
        resumed = _post_json(base, "/api/agent/resume", {"reason": "telegram"})
        operations = _get_json(base, "/api/operations?limit=10")
        cancelled = _post_json(base, f"/api/cancel/{running_id}", {"reason": "user_cancelled"})
        operation_status = _get_json(base, f"/api/operation-status/{running_id}")
        template = _post_json(base, "/api/templates", {"name": "warm", "body": "Hello {name}"})
        templates = _get_json(base, "/api/agent/templates")
        deleted_template = _post_json(base, "/api/templates/delete", {"name": "warm"})
        blacklist_item = _post_json(
            base,
            "/api/blacklist",
            {"employer_id": "emp-1", "employer_name": "Acme", "reason": "spam"},
        )
        blacklist = _get_json(base, "/api/agent/blacklist")
        deleted_blacklist = _post_json(base, "/api/blacklist/delete", {"employer_id": "emp-1"})
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert preflight["counts"]["pending_approvals"] == 1
    assert digest["approvals"]["total"] == 1
    assert approvals[0]["id"] == pending_id
    assert modified["status"] == "modified"
    assert modified["payload"]["message"] == "Short hello"
    assert approved["status"] == "approved"
    assert approved["reason"] == "approved in cockpit"
    assert run["operation"] == "digest"
    assert run["result"]["status"] == "ok"
    assert paused["paused"] is True
    assert resumed["paused"] is False
    assert operations["runs"][0]["tool_name"] == "hh_agent:digest"
    assert operations["logs"]
    assert cancelled["operation"]["status"] == "cancelled"
    assert operation_status["operation"]["output"]["reason"] == "user_cancelled"
    assert template["name"] == "warm"
    assert templates[0]["body"] == "Hello {name}"
    assert deleted_template == {"status": "ok", "deleted": 1}
    assert blacklist_item["employer_id"] == "emp-1"
    assert blacklist[0]["reason"] == "spam"
    assert deleted_blacklist == {"status": "ok", "deleted": 1}
