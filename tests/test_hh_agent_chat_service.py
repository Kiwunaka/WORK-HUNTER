from __future__ import annotations

from work_hunter.hh_agent.chat_service import HHChatAgentService, classify_chat_messages
from work_hunter.services import WorkHunter
from work_hunter.storage import Storage


class FakeHHChatClient:
    def __init__(self, messages_by_negotiation: dict[str, list[dict]]):
        self.messages_by_negotiation = messages_by_negotiation
        self.fetched: list[str] = []
        self.sent: list[tuple[str, str, str | None]] = []

    def list_negotiation_messages(self, negotiation_id: str):
        self.fetched.append(negotiation_id)
        return self.messages_by_negotiation.get(negotiation_id, [])

    def send_negotiation_message(self, negotiation_id: str, message: str, chat_id: str | None = None):
        self.sent.append((negotiation_id, message, chat_id))
        return {"status": "sent", "negotiation_id": negotiation_id, "chat_id": chat_id}


def _negotiation() -> dict:
    return {
        "id": "neg-1",
        "chat_id": "chat-1",
        "vacancy": {
            "id": "vac-1",
            "name": "Python Backend",
            "employer": {"id": "emp-1", "name": "Acme"},
        },
        "resume": {"id": "res-1", "title": "Backend Python"},
    }


def test_classify_chat_messages_schema_detects_reply_and_risk():
    classification = classify_chat_messages(
        [
            {
                "id": "msg-1",
                "text": "What salary are you looking for?",
                "author": {"participant_type": "employer"},
            }
        ]
    )

    assert classification.action == "reply"
    assert classification.confidence < 0.75
    assert "money" in classification.risk_flags
    assert classification.to_dict()["last_message_id"] == "msg-1"


def test_plan_reply_fetches_history_persists_decision_outbox_and_escalates_risky(tmp_path):
    storage = Storage(tmp_path / "db.sqlite3")
    client = FakeHHChatClient(
        {
            "neg-1": [
                {
                    "id": "msg-1",
                    "text": "What salary are you looking for?",
                    "author": {"participant_type": "employer"},
                }
            ]
        }
    )
    service = HHChatAgentService(storage=storage, client=client)

    result = service.plan_reply(
        _negotiation(),
        persona={"facts": {"name": "Alex", "summary": "Python backend developer."}},
        now="2026-06-10T10:00:00",
    )

    decisions = storage.list_hh_ai_decisions()
    outbox = storage.list_hh_agent_outbox()
    pending = storage.list_hh_pending_messages(status="pending")

    assert client.fetched == ["neg-1"]
    assert result["status"] == "needs_approval"
    assert result["pending_message_id"] == pending[0].id
    assert decisions[0].action_type == "reply"
    assert decisions[0].target_id == "neg-1"
    assert outbox[0].status == "pending_approval"
    assert outbox[0].payload["pending_message_id"] == pending[0].id
    assert pending[0].payload["reply"]["negotiation_id"] == "neg-1"


def test_scheduled_reply_waits_until_due_and_requires_confirm_before_send(tmp_path):
    storage = Storage(tmp_path / "db.sqlite3")
    client = FakeHHChatClient(
        {
            "neg-1": [
                {
                    "id": "msg-1",
                    "text": "Can you tell us more about your backend experience?",
                    "author": {"participant_type": "employer"},
                }
            ]
        }
    )
    service = HHChatAgentService(storage=storage, client=client)

    planned = service.plan_reply(
        _negotiation(),
        persona={"facts": {"summary": "I build Python APIs."}},
        delay_minutes=30,
        now="2026-06-10T10:00:00",
    )
    early = service.send_due_replies(now="2026-06-10T10:20:00", confirm=True)
    blocked = service.send_due_replies(now="2026-06-10T10:40:00", confirm=False)
    sent = service.send_due_replies(now="2026-06-10T10:40:00", confirm=True)

    outbox = storage.list_hh_agent_outbox()

    assert planned["status"] == "scheduled"
    assert planned["reply"]["send_after"] == "2026-06-10T10:30:00"
    assert early["count"] == 0
    assert blocked["status"] == "blocked"
    assert sent["status"] == "sent"
    assert client.sent == [("neg-1", planned["reply"]["message"], "chat-1")]
    assert outbox[0].status == "sent"


def test_workhunter_digest_includes_outbox_and_webhook_counts(tmp_path):
    app = WorkHunter(root=tmp_path)
    app.storage.create_hh_agent_outbox(
        channel="hh_reply",
        target="neg-1",
        payload={"reply": {"message": "Hello"}},
        status="planned",
    )
    app.storage.create_hh_agent_webhook(
        event_type="agent_reply_planned",
        payload={"reply_id": 1},
        status="pending",
    )

    digest = app.hh_agent_digest(limit=5)
    preflight = app.hh_agent_preflight()

    assert digest["outbox"]["total"] == 1
    assert digest["webhooks"]["total"] == 1
    assert preflight["counts"]["outbox"] == 1
    assert preflight["counts"]["webhooks"] == 1
