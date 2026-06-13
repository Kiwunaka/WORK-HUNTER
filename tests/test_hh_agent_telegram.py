from __future__ import annotations

from work_hunter.config import default_config
from work_hunter.hh_agent.telegram_bot import TelegramRemote, TelegramReply


class FakeCockpitAPI:
    def __init__(self):
        self.get_calls: list[str] = []
        self.post_calls: list[tuple[str, dict]] = []
        self.responses = {
            "/api/agent/preflight": {
                "status": "ok",
                "counts": {"pending_approvals": 2, "agent_events": 1, "agent_tasks": 1, "outbox": 3},
                "auth": {"authorized": True},
            },
            "/api/agent/digest?limit=5": {
                "status": "ok",
                "summary": {"resumes": {"total": 2}, "skipped": {"total": 1}},
                "approvals": {"by_status": {"pending": 2}, "recent": [{"id": 10, "action_type": "reply", "reason": "risky_reply"}]},
                "runs": {"total": 4, "recent": [{"id": 7, "tool_name": "hh_agent:digest", "status": "ok"}]},
                "agenda": {
                    "events": {"total": 1, "recent": [{"title": "Interview", "event_at": "2026-06-12T15:30:00"}]},
                    "tasks": {"total": 1, "recent": [{"title": "Test", "due_at": "2026-06-13T18:00:00"}]},
                },
                "outbox": {"total": 3},
                "webhooks": {"total": 1},
            },
            "/api/approvals?status=pending": [
                {"id": 10, "action_type": "reply", "reason": "risky_reply", "payload": {"reply": {"message": "<hello>"}}},
            ],
            "/api/agent/events?limit=5": [
                {"id": 1, "event_type": "interview", "title": "Interview", "event_at": "2026-06-12T15:30:00"},
            ],
            "/api/operations?limit=5": {"runs": [{"id": 7, "tool_name": "hh_agent:digest", "status": "ok"}]},
            "/api/hh/skipped": [{"vacancy_id": "100", "reason": "test_required", "name": "Python"}],
        }

    def get(self, path: str):
        self.get_calls.append(path)
        return self.responses[path]

    def post(self, path: str, payload: dict | None = None):
        body = payload or {}
        self.post_calls.append((path, body))
        return {"status": "ok", "path": path, "payload": body}


def _bot(api: FakeCockpitAPI | None = None) -> TelegramRemote:
    config = default_config()["hh_agent"]["telegram"]
    config["allowed_user_ids"] = [42]
    return TelegramRemote(config=config, api=api or FakeCockpitAPI(), state_factory=lambda: "state-123")


def test_default_config_has_safe_telegram_remote_settings():
    config = default_config()

    telegram = config["hh_agent"]["telegram"]

    assert telegram["enabled"] is False
    assert telegram["bot_token"] == ""
    assert telegram["allowed_user_ids"] == []
    assert telegram["cockpit_base_url"] == "http://127.0.0.1:8787"


def test_allowed_user_guard_blocks_without_touching_cockpit_api():
    api = FakeCockpitAPI()
    bot = _bot(api)

    reply = bot.handle_update({"message": {"from": {"id": 7}, "text": "/status"}})

    assert reply.text == "Access denied."
    assert api.get_calls == []
    assert api.post_calls == []


def test_status_digest_pending_next_and_lists_use_cockpit_api_and_escape_html():
    api = FakeCockpitAPI()
    bot = _bot(api)

    status = bot.handle_update({"message": {"from": {"id": 42}, "text": "/status"}})
    digest = bot.handle_update({"message": {"from": {"id": 42}, "text": "/digest"}})
    pending = bot.handle_update({"message": {"from": {"id": 42}, "text": "/pending"}})
    next_item = bot.handle_update({"message": {"from": {"id": 42}, "text": "/next"}})
    events = bot.handle_update({"message": {"from": {"id": 42}, "text": "/events"}})
    runs = bot.handle_update({"message": {"from": {"id": 42}, "text": "/runs"}})
    skipped = bot.handle_update({"message": {"from": {"id": 42}, "text": "/skipped"}})

    assert isinstance(status, TelegramReply)
    assert "pending: 2" in status.text
    assert "resumes: 2" in digest.text
    assert "&lt;hello&gt;" in pending.text
    assert pending.reply_markup[0][0]["callback_data"] == "approve:10"
    assert "reply #10" in next_item.text
    assert "Interview" in events.text
    assert "hh_agent:digest" in runs.text
    assert "test_required" in skipped.text
    assert api.get_calls == [
        "/api/agent/preflight",
        "/api/agent/digest?limit=5",
        "/api/approvals?status=pending",
        "/api/agent/digest?limit=5",
        "/api/agent/events?limit=5",
        "/api/operations?limit=5",
        "/api/hh/skipped",
    ]


def test_reply_pause_resume_oauth_and_inline_callbacks_post_to_cockpit_api():
    api = FakeCockpitAPI()
    bot = _bot(api)

    reply = bot.handle_update({"message": {"from": {"id": 42}, "text": "/reply 10 Hello there"}})
    pause = bot.handle_update({"message": {"from": {"id": 42}, "text": "/pause"}})
    resume = bot.handle_update({"message": {"from": {"id": 42}, "text": "/resume"}})
    oauth = bot.handle_update({"message": {"from": {"id": 42}, "text": "/oauth"}})
    approved = bot.handle_update({"callback_query": {"from": {"id": 42}, "data": "approve:10"}})
    rejected = bot.handle_update({"callback_query": {"from": {"id": 42}, "data": "reject:11"}})
    flagged = bot.handle_update({"callback_query": {"from": {"id": 42}, "data": "flag:12"}})
    sanity = bot.handle_update({"callback_query": {"from": {"id": 42}, "data": "sanity:13"}})
    modified = bot.handle_update({"callback_query": {"from": {"id": 42}, "data": "modify:14:Warmer"}})

    assert "updated #10" in reply.text
    assert "paused" in pause.text
    assert "resumed" in resume.text
    assert "state=state-123" in oauth.text
    assert "approved #10" in approved.text
    assert "rejected #11" in rejected.text
    assert "flagged #12" in flagged.text
    assert "sanity requested #13" in sanity.text
    assert "modified #14" in modified.text
    assert api.post_calls == [
        ("/api/approvals/10/modify", {"instruction": "telegram_reply", "payload_patch": {"message": "Hello there"}}),
        ("/api/agent/pause", {"reason": "telegram"}),
        ("/api/agent/resume", {"reason": "telegram"}),
        ("/api/approvals/10/approve", {"reason": "telegram_approve"}),
        ("/api/approvals/11/reject", {"reason": "telegram_reject"}),
        ("/api/approvals/12/flag", {"reason": "telegram_flag"}),
        ("/api/approvals/13/flag", {"reason": "sanity_check_requested"}),
        ("/api/approvals/14/modify", {"instruction": "Warmer", "payload_patch": {}}),
    ]
