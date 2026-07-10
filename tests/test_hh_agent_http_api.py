from __future__ import annotations

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

from work_hunter.hh_agent import HHVacancyResearchService, VacancyPolicy
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


class FakeResearchHHClient:
    def has_token(self):
        return True

    def search_vacancies(self, params):
        assert params["text"] == "python"
        return [
            {
                "id": "vac-1",
                "name": "Python Backend",
                "alternate_url": "https://hh.ru/vacancy/vac-1",
                "employer": {"name": "Acme"},
            }
        ]

    def get_vacancy(self, vacancy_id):
        return {
            "id": vacancy_id,
            "name": "Python Backend",
            "description": "FastAPI and PostgreSQL",
            "employer": {"id": "emp-1", "name": "Acme"},
        }


class FakeAgentResumeClient:
    constructed = 0
    updated_resumes: list[str] = []

    def __init__(self, config):
        type(self).constructed += 1
        self.config = config

    def has_token(self):
        return bool(self.config.get("access_token"))

    def list_resumes(self):
        return [{"id": "resume-1", "title": "Backend", "status": {"id": "published"}}]

    def update_resume(self, resume_id: str):
        self.updated_resumes.append(resume_id)
        return {"status": "updated", "resume_id": resume_id}


class FakeStructuredReply:
    parsed = {
        "score": 91,
        "recommended_action": "apply",
        "reasons": ["strong python match"],
        "risk_flags": [],
    }
    model = "test-model"

    def to_dict(self):
        return {"parsed": self.parsed, "model": self.model}


def fake_structured_chat(messages, ai_config, schema):
    assert schema.name == "hh_vacancy_analysis"
    assert "Python Backend" in messages[0]["content"]
    return FakeStructuredReply()


def fake_research_service(self, *, client, min_score=None):
    return HHVacancyResearchService(
        client=client,
        storage=self.storage,
        ai_config={"model": "test-model"},
        policy=VacancyPolicy.from_mapping({"min_score": min_score or 80, "force_message": "Hi"}),
        structured_chat=fake_structured_chat,
    )


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


def test_hh_agent_http_update_resumes_requires_literal_confirmation(
    monkeypatch, tmp_path
):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeAgentResumeClient)
    FakeAgentResumeClient.constructed = 0
    FakeAgentResumeClient.updated_resumes = []
    app = WorkHunter(tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"
    app.save_config(app.config)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(tmp_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        blocked = _post_json(
            base,
            "/api/agent/run",
            {"operation": "update-resumes", "params": {"confirm": "true"}},
        )
        confirmed = _post_json(
            base,
            "/api/agent/run",
            {"operation": "update-resumes", "params": {"confirm": True}},
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert blocked["result"]["status"] == "blocked"
    assert blocked["result"]["code"] == "resume_mutation_requires_confirmation"
    assert confirmed["result"] == {
        "status": "ok",
        "count": 1,
        "updated": ["resume-1"],
    }
    assert FakeAgentResumeClient.constructed == 1
    assert FakeAgentResumeClient.updated_resumes == ["resume-1"]


def test_hh_agent_web_api_runs_research_and_dry_run_apply(monkeypatch, tmp_path):
    monkeypatch.setattr(WorkHunter, "_hh_research_client", lambda self: FakeResearchHHClient())
    monkeypatch.setattr(WorkHunter, "_build_hh_research_service", fake_research_service)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(tmp_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        run = _post_json(
            base,
            "/api/agent/run",
            {
                "operation": "research-and-apply",
                "params": {"text": "python", "limit": 2, "resume_id": "res-1"},
            },
        )
        blocked = _post_json(
            base,
            "/api/agent/run",
            {
                "operation": "research-and-apply",
                "params": {"text": "python", "limit": 1, "resume_id": "res-1", "confirm_apply": True},
            },
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    app = WorkHunter(root=tmp_path)
    attempts = app.storage.list_hh_application_attempts("vac-1")
    analyses = app.storage.list_hh_vacancy_analysis("vac-1")

    assert run["operation"] == "research-and-apply"
    assert run["operation_id"] > 0
    assert run["result"]["status"] == "ok"
    assert run["result"]["counts"]["analyzed"] == 1
    assert run["result"]["counts"]["planned"] == 1
    assert run["result"]["items"][0]["vacancy_id"] == "vac-1"
    assert run["result"]["items"][0]["score"] == 91
    assert run["result"]["items"][0]["attempt_status"] == "planned"
    assert blocked["result"]["status"] == "blocked"
    assert blocked["result"]["reason"] == "real_apply_blocked"
    assert attempts[0].status == "planned"
    assert attempts[0].letter == "Hi"
    assert analyses[0].score == 91
