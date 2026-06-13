from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable

from .approval import ApprovalQueue, should_escalate


MONEY_KEYWORDS = (
    "salary",
    "compensation",
    "rate",
    "\u0437\u0430\u0440\u043f",
    "\u043e\u043a\u043b\u0430\u0434",
    "\u0434\u0435\u043d\u044c\u0433",
)
PERSONAL_DATA_KEYWORDS = (
    "passport",
    "address",
    "personal data",
    "\u043f\u0430\u0441\u043f\u043e\u0440\u0442",
    "\u0430\u0434\u0440\u0435\u0441",
)


@dataclass
class ChatClassification:
    action: str = "wait"
    confidence: float = 0.0
    risk_flags: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    last_message_id: str = ""
    last_message_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReplyDraft:
    negotiation_id: str
    chat_id: str = ""
    vacancy_id: str = ""
    vacancy_name: str = ""
    employer_id: str = ""
    employer_name: str = ""
    resume_id: str = ""
    message: str = ""
    confidence: float = 0.0
    risk_flags: list[str] = field(default_factory=list)
    send_after: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def classify_chat_messages(messages: Iterable[dict[str, Any]]) -> ChatClassification:
    last = _last_relevant_message(messages)
    if not last:
        return ChatClassification(action="wait", confidence=0.0, reasons=["no_messages"])
    participant = _participant_type(last)
    text = _message_text(last)
    lowered = text.lower()
    if participant and participant != "employer":
        return ChatClassification(
            action="wait",
            confidence=0.9,
            reasons=["last_message_not_from_employer"],
            last_message_id=str(last.get("id") or ""),
            last_message_text=text,
        )
    risk_flags: list[str] = []
    reasons = ["last_message_from_employer"]
    confidence = 0.82
    if any(keyword in lowered for keyword in MONEY_KEYWORDS):
        risk_flags.append("money")
        reasons.append("money_discussion")
        confidence = min(confidence, 0.68)
    if any(keyword in lowered for keyword in PERSONAL_DATA_KEYWORDS):
        risk_flags.append("personal_data")
        reasons.append("personal_data_requested")
        confidence = min(confidence, 0.6)
    if "?" in text:
        reasons.append("question_detected")
    return ChatClassification(
        action="reply",
        confidence=confidence,
        risk_flags=risk_flags,
        reasons=reasons,
        last_message_id=str(last.get("id") or ""),
        last_message_text=text,
    )


class HHChatAgentService:
    def __init__(self, *, storage: Any, client: Any):
        self.storage = storage
        self.client = client
        self.approvals = ApprovalQueue(storage)

    def plan_reply(
        self,
        negotiation: dict[str, Any],
        *,
        persona: dict[str, Any] | None = None,
        template: str = "",
        delay_minutes: int = 0,
        now: str | None = None,
    ) -> dict[str, Any]:
        negotiation_id = str(negotiation.get("id") or "")
        if not negotiation_id:
            raise ValueError("Negotiation id is required")
        messages = self.client.list_negotiation_messages(negotiation_id)
        classification = classify_chat_messages(messages)
        decision_id = self.approvals.persist_ai_decision(
            action_type="reply",
            target_id=negotiation_id,
            model="heuristic",
            confidence=classification.confidence,
            reasons=classification.reasons,
            raw_result={"classification": classification.to_dict()},
        )
        if classification.action != "reply":
            return {
                "status": "skipped",
                "reason": "no_reply_needed",
                "classification": classification.to_dict(),
                "ai_decision_id": decision_id,
            }

        reply = draft_reply(
            negotiation,
            messages=messages,
            persona=persona or {},
            template=template,
            classification=classification,
        )
        if delay_minutes > 0:
            reply.send_after = _iso(_parse_datetime(now) + timedelta(minutes=delay_minutes))
        outbox_payload = {
            "reply": reply.to_dict(),
            "classification": classification.to_dict(),
            "ai_decision_id": decision_id,
        }
        outbox_status = "scheduled" if reply.send_after else "planned"
        pending_id: int | None = None
        if should_escalate(
            action_type="reply",
            confidence=classification.confidence,
            risk_flags=classification.risk_flags,
        ):
            pending_payload = {
                "reply": reply.to_dict(),
                "classification": classification.to_dict(),
                "risk_flags": classification.risk_flags,
            }
            pending_id = self.approvals.escalate_to_user(
                action_type="reply",
                payload=pending_payload,
                confidence=classification.confidence,
                reason="risky_reply",
                ai_decision_id=decision_id,
            )
            outbox_payload["pending_message_id"] = pending_id
            outbox_status = "pending_approval"
        outbox_id = self.storage.create_hh_agent_outbox(
            channel="hh_reply",
            target=negotiation_id,
            payload=outbox_payload,
            status=outbox_status,
        )
        return {
            "status": "needs_approval" if pending_id else outbox_status,
            "outbox_id": outbox_id,
            "pending_message_id": pending_id,
            "ai_decision_id": decision_id,
            "classification": classification.to_dict(),
            "reply": reply.to_dict(),
        }

    def send_due_replies(
        self,
        *,
        now: str | None = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        due_items = []
        now_dt = _parse_datetime(now)
        for status in ("planned", "scheduled"):
            for item in self.storage.list_hh_agent_outbox(status=status, channel="hh_reply"):
                reply = item.payload.get("reply") or {}
                send_after = str(reply.get("send_after") or "")
                if send_after and _parse_datetime(send_after) > now_dt:
                    continue
                due_items.append(item)
        if not confirm and due_items:
            return {
                "status": "blocked",
                "count": 0,
                "message": "Explicit confirm=True is required before sending HH chat replies.",
            }
        sent: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        for item in due_items:
            reply = item.payload.get("reply") or {}
            try:
                result = self.client.send_negotiation_message(
                    str(reply.get("negotiation_id") or item.target),
                    str(reply.get("message") or ""),
                    chat_id=str(reply.get("chat_id") or "") or None,
                )
                payload = {**item.payload, "send_result": result}
                self.storage.update_hh_agent_outbox(item.id, status="sent", payload=payload)
                sent.append({"outbox_id": item.id, "result": result})
            except Exception as exc:
                payload = {**item.payload, "error": str(exc)}
                self.storage.update_hh_agent_outbox(item.id, status="error", payload=payload)
                errors.append({"outbox_id": item.id, "error": str(exc)})
        return {
            "status": "sent" if not errors else "partial",
            "count": len(sent),
            "sent": sent,
            "errors": errors,
        }


def draft_reply(
    negotiation: dict[str, Any],
    *,
    messages: list[dict[str, Any]],
    persona: dict[str, Any],
    template: str = "",
    classification: ChatClassification | None = None,
) -> ReplyDraft:
    vacancy = negotiation.get("vacancy") or {}
    employer = negotiation.get("employer") or vacancy.get("employer") or {}
    resume = negotiation.get("resume") or {}
    facts = persona.get("facts") or {}
    context = {
        "negotiation_id": str(negotiation.get("id") or ""),
        "chat_id": str(negotiation.get("chat_id") or ""),
        "vacancy_id": _text_id(vacancy),
        "vacancy_name": str(vacancy.get("name") or vacancy.get("title") or ""),
        "employer_id": _text_id(employer),
        "employer_name": str(employer.get("name") or ""),
        "resume_id": _text_id(resume),
        "resume_title": str(resume.get("title") or ""),
        "summary": str(facts.get("summary") or persona.get("body") or ""),
        "last_message": _message_text(_last_relevant_message(messages) or {}),
    }
    if template:
        message = template.format_map(_SafeDict(context)).strip()
    else:
        employer_name = context["employer_name"] or "there"
        summary = context["summary"]
        message = f"Hello {employer_name}! Thank you for the message."
        if summary:
            message += f" {summary}"
        if context["vacancy_name"]:
            message += f" I am interested in {context['vacancy_name']} and can share more details."
    return ReplyDraft(
        negotiation_id=context["negotiation_id"],
        chat_id=context["chat_id"],
        vacancy_id=context["vacancy_id"],
        vacancy_name=context["vacancy_name"],
        employer_id=context["employer_id"],
        employer_name=context["employer_name"],
        resume_id=context["resume_id"],
        message=message,
        confidence=(classification.confidence if classification else 0.75),
        risk_flags=list(classification.risk_flags if classification else []),
    )


class _SafeDict(dict[str, str]):
    def __missing__(self, key: str) -> str:
        return ""


def _last_relevant_message(messages: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    for message in reversed(list(messages)):
        if _message_text(message):
            return message
    return None


def _participant_type(message: dict[str, Any]) -> str:
    author = message.get("author") or {}
    if isinstance(author, dict):
        return str(author.get("participant_type") or author.get("type") or "").lower()
    return str(author or "").lower()


def _message_text(message: dict[str, Any]) -> str:
    return str(message.get("text") or message.get("body") or message.get("message") or "").strip()


def _text_id(item: Any) -> str:
    if isinstance(item, dict):
        value = item.get("id")
        if isinstance(value, dict):
            return str(value.get("id") or value.get("name") or "")
        return str(value or "")
    return str(item or "")


def _parse_datetime(value: str | None) -> datetime:
    if not value:
        return datetime.now().replace(microsecond=0)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None, microsecond=0)


def _iso(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat()
