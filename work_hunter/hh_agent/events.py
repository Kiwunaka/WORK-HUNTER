from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from ..models import HHAgentEvent, HHAgentTask


ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{1,2}):(\d{2}))?")
DOT_DATE_RE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})(?:\s+(\d{1,2}):(\d{2}))?")
TIME_RE = re.compile(r"\b(\d{1,2}):(\d{2})\b")
HTML_TAG_RE = re.compile(r"<[^>]+>")

INTERVIEW_KEYWORDS = (
    "interview",
    "screening",
    "meeting",
    "meet",
    "call",
    "\u0441\u043e\u0431\u0435\u0441\u0435\u0434",
    "\u0438\u043d\u0442\u0435\u0440\u0432\u044c\u044e",
    "\u0432\u0441\u0442\u0440\u0435\u0447",
    "\u0441\u043e\u0437\u0432\u043e\u043d",
)
TEST_KEYWORDS = (
    "test task",
    "test assignment",
    "challenge",
    "\u0442\u0435\u0441\u0442\u043e\u0432",
    "\u0437\u0430\u0434\u0430\u043d",
)
DEADLINE_KEYWORDS = (
    "deadline",
    "due",
    "by ",
    "\u0434\u0435\u0434\u043b\u0430\u0439\u043d",
    "\u0441\u0440\u043e\u043a",
    "\u0434\u043e ",
)
FOLLOW_UP_KEYWORDS = (
    "follow up",
    "follow-up",
    "feedback",
    "get back",
    "\u0444\u0438\u0434\u0431\u0435\u043a",
    "\u0441\u0432\u044f\u0436",
    "\u0432\u0435\u0440\u043d\u0435\u043c\u0441\u044f",
)
REJECTION_KEYWORDS = (
    "unfortunately",
    "cannot proceed",
    "not proceed",
    "reject",
    "\u043a \u0441\u043e\u0436\u0430\u043b\u0435\u043d\u0438\u044e",
    "\u043e\u0442\u043a\u0430\u0437",
    "\u043e\u0442\u043a\u043b\u043e\u043d",
)
OFFER_KEYWORDS = (
    "offer",
    "job offer",
    "\u043e\u0444\u0444\u0435\u0440",
    "\u043e\u0444\u0435\u0440",
    "\u043f\u0440\u0435\u0434\u043b\u043e\u0436\u0435\u043d",
)


@dataclass
class HHAgentDetectedItems:
    events: list[HHAgentEvent] = field(default_factory=list)
    tasks: list[HHAgentTask] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "events": [event.to_dict() for event in self.events],
            "tasks": [task.to_dict() for task in self.tasks],
        }


def detect_hh_agent_items(
    messages: Iterable[dict[str, Any]],
    *,
    now: str | None = None,
) -> HHAgentDetectedItems:
    reference_now = _parse_datetime(now) or datetime.now().replace(microsecond=0)
    result = HHAgentDetectedItems()
    for message in messages:
        if not _is_employer_message(message):
            continue
        text = _message_text(message)
        if not text:
            continue
        lowered = text.lower()
        payload = _message_payload(message, text)
        source_id = _source_id(message)
        event_at = _extract_datetime(text, reference_now) or _message_datetime(message, reference_now)
        title_target = payload.get("vacancy_name") or payload.get("employer_name") or source_id

        if _contains(lowered, INTERVIEW_KEYWORDS):
            result.events.append(
                HHAgentEvent(
                    event_type="interview",
                    title=f"Interview: {title_target}",
                    source_id=source_id,
                    payload=payload,
                    event_at=_iso(event_at),
                    status="active",
                )
            )
        if _contains(lowered, REJECTION_KEYWORDS):
            result.events.append(
                HHAgentEvent(
                    event_type="rejection",
                    title=f"Rejection: {title_target}",
                    source_id=source_id,
                    payload=payload,
                    event_at=_iso(event_at),
                    status="closed",
                )
            )
        if _contains(lowered, OFFER_KEYWORDS):
            result.events.append(
                HHAgentEvent(
                    event_type="offer",
                    title=f"Offer: {title_target}",
                    source_id=source_id,
                    payload=payload,
                    event_at=_iso(event_at),
                    status="active",
                )
            )

        due_at = _extract_datetime(text, reference_now) or _message_datetime(message, reference_now)
        if _contains(lowered, TEST_KEYWORDS):
            result.tasks.append(
                HHAgentTask(
                    task_type="test",
                    title=f"Test: {title_target}",
                    source_id=source_id,
                    payload=payload,
                    due_at=_iso(due_at),
                    status="open",
                )
            )
        elif _contains(lowered, DEADLINE_KEYWORDS):
            result.tasks.append(
                HHAgentTask(
                    task_type="deadline",
                    title=f"Deadline: {title_target}",
                    source_id=source_id,
                    payload=payload,
                    due_at=_iso(due_at),
                    status="open",
                )
            )
        elif _contains(lowered, FOLLOW_UP_KEYWORDS):
            result.tasks.append(
                HHAgentTask(
                    task_type="follow_up",
                    title=f"Follow-up: {title_target}",
                    source_id=source_id,
                    payload=payload,
                    due_at=_iso(due_at),
                    status="open",
                )
            )
    return result


def export_hh_agent_calendar_ics(
    events: Iterable[HHAgentEvent],
    tasks: Iterable[HHAgentTask] = (),
    *,
    calendar_name: str = "Work Hunter HH Agent",
) -> str:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Work Hunter//HH Agent//EN",
        f"X-WR-CALNAME:{_ics_escape(calendar_name)}",
    ]
    for event in events:
        uid = f"hh-agent-event-{event.id or event.source_id}-{event.event_type}@work-hunter"
        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{_ics_escape(uid)}",
                f"DTSTAMP:{_ics_now()}",
                f"DTSTART:{_ics_datetime(event.event_at)}",
                f"SUMMARY:{_ics_escape(event.title)}",
                f"DESCRIPTION:{_ics_escape(_description(event.payload))}",
                f"STATUS:{'CANCELLED' if event.status == 'closed' else 'CONFIRMED'}",
                "END:VEVENT",
            ]
        )
    for task in tasks:
        uid = f"hh-agent-task-{task.id or task.source_id}-{task.task_type}@work-hunter"
        lines.extend(
            [
                "BEGIN:VTODO",
                f"UID:{_ics_escape(uid)}",
                f"DTSTAMP:{_ics_now()}",
                f"DUE:{_ics_datetime(task.due_at)}",
                f"SUMMARY:{_ics_escape(task.title)}",
                f"DESCRIPTION:{_ics_escape(_description(task.payload))}",
                f"STATUS:{'COMPLETED' if task.status == 'done' else 'NEEDS-ACTION'}",
                "END:VTODO",
            ]
        )
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def export_hh_agent_agenda_markdown(
    events: Iterable[HHAgentEvent],
    tasks: Iterable[HHAgentTask] = (),
) -> str:
    event_list = list(events)
    task_list = list(tasks)
    lines = ["# HH Agent Agenda", "", "## Events"]
    if not event_list:
        lines.append("- No detected events.")
    for event in event_list:
        lines.append(f"- {event.event_at} - {event.title} [{event.event_type}]")
    lines.extend(["", "## Tasks"])
    if not task_list:
        lines.append("- No detected tasks.")
    for task in task_list:
        lines.append(f"- {task.due_at} - {task.title} [{task.task_type}]")
    return "\n".join(lines) + "\n"


def agent_agenda_notification_payload(
    events: Iterable[HHAgentEvent],
    tasks: Iterable[HHAgentTask] = (),
) -> dict[str, Any]:
    event_list = list(events)
    task_list = list(tasks)
    return {
        "event_type": "agenda",
        "title": "HH agenda",
        "body": export_hh_agent_agenda_markdown(event_list[:5], task_list[:5]),
        "payload": {"events": len(event_list), "tasks": len(task_list)},
    }


def _is_employer_message(message: dict[str, Any]) -> bool:
    author = message.get("author") or {}
    if isinstance(author, dict):
        participant = str(author.get("participant_type") or author.get("type") or "").lower()
    else:
        participant = str(author or "").lower()
    if not participant:
        return True
    return participant in {"employer", "manager", "hr", "recruiter"}


def _message_text(message: dict[str, Any]) -> str:
    text = str(message.get("text") or message.get("body") or message.get("message") or "")
    return HTML_TAG_RE.sub(" ", text).replace("&nbsp;", " ").strip()


def _message_payload(message: dict[str, Any], text: str) -> dict[str, Any]:
    vacancy = message.get("vacancy") or {}
    employer = message.get("employer") or vacancy.get("employer") or {}
    return {
        "message_id": str(message.get("id") or ""),
        "negotiation_id": str(message.get("negotiation_id") or message.get("negotiationId") or ""),
        "chat_id": str(message.get("chat_id") or message.get("chatId") or ""),
        "vacancy_id": str(vacancy.get("id") or message.get("vacancy_id") or ""),
        "vacancy_name": str(vacancy.get("name") or vacancy.get("title") or message.get("vacancy_name") or ""),
        "employer_id": str(employer.get("id") or message.get("employer_id") or ""),
        "employer_name": str(employer.get("name") or message.get("employer_name") or ""),
        "text": text,
    }


def _source_id(message: dict[str, Any]) -> str:
    negotiation_id = str(message.get("negotiation_id") or message.get("negotiationId") or "")
    message_id = str(message.get("id") or message.get("message_id") or "")
    if negotiation_id and message_id:
        return f"{negotiation_id}:{message_id}"
    return message_id or negotiation_id or str(hash(_message_text(message)))


def _message_datetime(message: dict[str, Any], fallback: datetime) -> datetime:
    for key in ("created_at", "createdAt", "updated_at", "updatedAt"):
        parsed = _parse_datetime(str(message.get(key) or ""))
        if parsed:
            return parsed
    return fallback


def _extract_datetime(text: str, now: datetime) -> datetime | None:
    match = ISO_DATE_RE.search(text)
    if match:
        year, month, day, hour, minute = match.groups()
        return datetime(
            int(year),
            int(month),
            int(day),
            int(hour or 9),
            int(minute or 0),
        )
    match = DOT_DATE_RE.search(text)
    if match:
        day, month, year, hour, minute = match.groups()
        return datetime(
            int(year),
            int(month),
            int(day),
            int(hour or 9),
            int(minute or 0),
        )
    lowered = text.lower()
    if "tomorrow" in lowered or "\u0437\u0430\u0432\u0442\u0440\u0430" in lowered:
        time_match = TIME_RE.search(text)
        hour = int(time_match.group(1)) if time_match else 9
        minute = int(time_match.group(2)) if time_match else 0
        return (now + timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    return None


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None, microsecond=0)
    except ValueError:
        return None


def _iso(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat()


def _contains(text: str, keywords: Iterable[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def _ics_datetime(value: str) -> str:
    parsed = _parse_datetime(value) or datetime.now().replace(microsecond=0)
    return parsed.strftime("%Y%m%dT%H%M%S")


def _ics_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y%m%dT%H%M%SZ")


def _ics_escape(value: str) -> str:
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def _description(payload: dict[str, Any]) -> str:
    parts = [
        str(payload.get("employer_name") or ""),
        str(payload.get("vacancy_name") or ""),
        str(payload.get("text") or ""),
    ]
    return "\n".join(part for part in parts if part)
