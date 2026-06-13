from __future__ import annotations

from work_hunter.hh_agent.approval import ApprovalQueue, should_escalate
from work_hunter.storage import Storage


def test_should_escalate_low_confidence_or_risk():
    assert should_escalate(action_type="apply", confidence=0.4, risk_flags=[]) is True
    assert should_escalate(action_type="apply", confidence=0.9, risk_flags=["test_required"]) is True
    assert should_escalate(action_type="apply", confidence=0.9, risk_flags=[]) is False
    assert should_escalate(action_type="search", confidence=0.1, risk_flags=[]) is False


def test_approval_queue_persists_and_transitions(tmp_path):
    storage = Storage(tmp_path / "db.sqlite3")
    queue = ApprovalQueue(storage)
    decision_id = queue.persist_ai_decision(
        action_type="apply",
        target_id="vac-1",
        model="test-model",
        confidence=0.5,
        reasons=["low confidence"],
        raw_result={"score": 50},
    )
    message_id = queue.escalate_to_user(
        action_type="apply",
        payload={"vacancy_id": "vac-1", "message": "Hi"},
        confidence=0.5,
        reason="low_confidence",
        ai_decision_id=decision_id,
    )

    queue.modify(message_id, instruction="make it warmer", payload_patch={"message": "Warm hi"})
    queue.approve(message_id)

    pending = storage.list_hh_pending_messages()[0]
    decisions = storage.list_hh_ai_decisions()
    assert decisions[0].target_id == "vac-1"
    assert pending.status == "approved"
    assert pending.reason == "approved"
    assert pending.payload["message"] == "Warm hi"
    assert pending.payload["modify_instruction"] == "make it warmer"


def test_approval_queue_modify_can_regenerate_payload(tmp_path):
    storage = Storage(tmp_path / "db.sqlite3")
    queue = ApprovalQueue(storage)
    message_id = queue.escalate_to_user(
        action_type="reply",
        payload={"message": "Hi", "tone": "neutral"},
        confidence=0.5,
        reason="manual_review",
    )

    def regenerate(payload, instruction):
        return {
            "message": f"{payload['message']} / regenerated: {instruction}",
            "tone": "warm",
        }

    queue.modify(message_id, instruction="make it warmer", regenerate=regenerate)

    pending = storage.list_hh_pending_messages()[0]
    assert pending.status == "modified"
    assert pending.payload["message"] == "Hi / regenerated: make it warmer"
    assert pending.payload["tone"] == "warm"
    assert pending.payload["regenerated"] is True
