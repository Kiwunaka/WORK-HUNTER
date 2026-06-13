from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..models import HHAIDecision, HHPendingMessage


@dataclass(frozen=True)
class ApprovalPolicy:
    min_confidence: float = 0.75
    escalate_actions: set[str] = field(default_factory=lambda: {"apply", "reply", "form_submit"})
    escalate_risk_flags: set[str] = field(
        default_factory=lambda: {
            "manual_form_required",
            "test_required",
            "captcha_required",
            "low_confidence",
            "money",
            "personal_data",
        }
    )


def should_escalate(
    *,
    action_type: str,
    confidence: float,
    risk_flags: list[str] | None = None,
    policy: ApprovalPolicy | None = None,
) -> bool:
    approval_policy = policy or ApprovalPolicy()
    risks = set(risk_flags or [])
    return (
        action_type in approval_policy.escalate_actions
        and (
            confidence < approval_policy.min_confidence
            or bool(risks & approval_policy.escalate_risk_flags)
        )
    )


class ApprovalQueue:
    def __init__(self, storage: Any, *, policy: ApprovalPolicy | None = None):
        self.storage = storage
        self.policy = policy or ApprovalPolicy()

    def persist_ai_decision(
        self,
        *,
        action_type: str,
        target_id: str,
        model: str = "",
        policy_hash: str = "",
        confidence: float = 0.0,
        reasons: list[str] | None = None,
        raw_result: dict[str, Any] | None = None,
    ) -> int:
        return self.storage.save_hh_ai_decision(
            HHAIDecision(
                action_type=action_type,
                target_id=target_id,
                model=model,
                policy_hash=policy_hash,
                confidence=confidence,
                reasons=reasons or [],
                raw_result=raw_result or {},
            )
        )

    def escalate_to_user(
        self,
        *,
        action_type: str,
        payload: dict[str, Any],
        confidence: float,
        reason: str,
        ai_decision_id: int | None = None,
    ) -> int:
        return self.storage.create_hh_pending_message(
            HHPendingMessage(
                action_type=action_type,
                payload=payload,
                confidence=confidence,
                reason=reason,
                ai_decision_id=ai_decision_id,
            )
        )

    def approve(self, message_id: int, *, reason: str = "approved") -> None:
        self.storage.update_hh_pending_message(message_id, status="approved", reason=reason)

    def reject(self, message_id: int, *, reason: str = "rejected") -> None:
        self.storage.update_hh_pending_message(message_id, status="rejected", reason=reason)

    def modify(
        self,
        message_id: int,
        *,
        instruction: str,
        payload_patch: dict[str, Any] | None = None,
        regenerate: Any | None = None,
    ) -> None:
        messages = [item for item in self.storage.list_hh_pending_messages() if item.id == message_id]
        payload = dict(messages[0].payload) if messages else {}
        payload["modify_instruction"] = instruction
        payload.update(payload_patch or {})
        if regenerate is not None:
            regenerated = regenerate(dict(payload), instruction) or {}
            payload.update(dict(regenerated))
            payload["regenerated"] = True
        self.storage.update_hh_pending_message(
            message_id,
            status="modified",
            reason="user_modified",
            payload=payload,
        )

    def flag(self, message_id: int, *, reason: str = "flagged") -> None:
        self.storage.update_hh_pending_message(message_id, status="flagged", reason=reason)
