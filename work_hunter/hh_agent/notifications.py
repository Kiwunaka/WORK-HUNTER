from __future__ import annotations

import hashlib
import html
import hmac
import json
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Protocol

from .events import export_hh_agent_agenda_markdown


@dataclass
class NotificationEvent:
    event_type: str
    title: str
    body: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class NotificationSink(Protocol):
    def send(self, event: NotificationEvent) -> dict[str, Any]:
        ...


class MemoryNotificationSink:
    def __init__(self):
        self.events: list[NotificationEvent] = []

    def send(self, event: NotificationEvent) -> dict[str, Any]:
        self.events.append(event)
        return {"status": "sent", "event_type": event.event_type}


class FileNotificationSink:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def send(self, event: NotificationEvent) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
        return {"status": "sent", "event_type": event.event_type, "sink": "file"}


class TelegramNotificationSink:
    def __init__(
        self,
        *,
        bot_token: str,
        chat_id: str | int,
        transport: Any | None = None,
    ):
        if not bot_token:
            raise ValueError("Telegram bot token is required")
        if not str(chat_id).strip():
            raise ValueError("Telegram chat_id is required")
        self.bot_token = bot_token
        self.chat_id = str(chat_id)
        self.transport = transport or _urllib_transport

    def send(self, event: NotificationEvent) -> dict[str, Any]:
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": telegram_html_message(event),
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        response = self.transport(
            url,
            body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        return {"status": "sent", "event_type": event.event_type, "sink": "telegram", "response": response}


class WebhookNotificationSink:
    def __init__(
        self,
        *,
        url: str,
        secret: str = "",
        transport: Any | None = None,
    ):
        if not url:
            raise ValueError("Webhook url is required")
        self.url = url
        self.secret = secret
        self.transport = transport or _urllib_transport

    def send(self, event: NotificationEvent) -> dict[str, Any]:
        payload = event.to_dict()
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "X-Work-Hunter-Event": event.event_type,
        }
        if self.secret:
            signature = hmac.new(self.secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
            headers["X-Work-Hunter-Signature"] = f"sha256={signature}"
        response = self.transport(self.url, body=body, headers=headers)
        return {"status": "sent", "event_type": event.event_type, "sink": "webhook", "response": response}


def telegram_html_message(event: NotificationEvent) -> str:
    title = html.escape(event.title)
    body = html.escape(event.body)
    if body:
        return f"<b>{title}</b>\n{body}"
    return f"<b>{title}</b>"


def run_summary_event(
    *,
    operation: str,
    status: str,
    counts: dict[str, int],
    error: str = "",
    run_id: str = "",
    duration_seconds: float | None = None,
) -> NotificationEvent:
    count_text = ", ".join(f"{key}: {value}" for key, value in sorted(counts.items()))
    body = f"status: {status}"
    if run_id:
        body += f"\nrun_id: {run_id}"
    if duration_seconds is not None:
        body += f"\nduration_seconds: {duration_seconds}"
    if count_text:
        body += f"\n{count_text}"
    if error:
        body += f"\nerror: {error}"
    payload: dict[str, Any] = {"operation": operation, "status": status, "counts": counts, "error": error}
    if run_id:
        payload["run_id"] = run_id
    if duration_seconds is not None:
        payload["duration_seconds"] = duration_seconds
    return NotificationEvent(
        event_type="run_summary",
        title=f"HH {operation}",
        body=body,
        payload=payload,
    )


def agenda_notification_event(*, events: Iterable[Any], tasks: Iterable[Any]) -> NotificationEvent:
    event_list = list(events)
    task_list = list(tasks)
    return NotificationEvent(
        event_type="agenda",
        title="HH agenda",
        body=export_hh_agent_agenda_markdown(event_list[:5], task_list[:5]),
        payload={"events": len(event_list), "tasks": len(task_list)},
    )


def _urllib_transport(url: str, *, body: bytes, headers: dict[str, str]) -> dict[str, Any]:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=10) as response:
        return {"status": response.status, "body": response.read().decode("utf-8", errors="replace")}
