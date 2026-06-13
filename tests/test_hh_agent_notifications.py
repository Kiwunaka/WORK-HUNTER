from __future__ import annotations

import json

from work_hunter.hh_agent.notifications import (
    FileNotificationSink,
    MemoryNotificationSink,
    NotificationEvent,
    TelegramNotificationSink,
    WebhookNotificationSink,
    agenda_notification_event,
    run_summary_event,
    telegram_html_message,
)
from work_hunter.models import HHAgentEvent, HHAgentTask


def test_telegram_html_message_escapes_user_text():
    event = NotificationEvent(event_type="pending", title="<Apply>", body="Use <b>bold?</b>")

    message = telegram_html_message(event)

    assert "&lt;Apply&gt;" in message
    assert "&lt;b&gt;bold?&lt;/b&gt;" in message


def test_memory_notification_sink_and_run_summary():
    sink = MemoryNotificationSink()
    event = run_summary_event(
        operation="research",
        status="ok",
        counts={"planned": 2},
        run_id="run-1",
        duration_seconds=1.25,
    )

    result = sink.send(event)

    assert result == {"status": "sent", "event_type": "run_summary"}
    assert sink.events[0].payload["counts"] == {"planned": 2}
    assert sink.events[0].payload["run_id"] == "run-1"
    assert sink.events[0].payload["duration_seconds"] == 1.25
    assert "duration_seconds: 1.25" in sink.events[0].body


def test_file_telegram_and_webhook_notification_sinks(tmp_path):
    file_path = tmp_path / "notifications.jsonl"
    event = NotificationEvent(
        event_type="run_summary",
        title="HH runner",
        body="status: completed",
        payload={"run_id": "run-1"},
    )
    telegram_calls: list[dict] = []
    webhook_calls: list[dict] = []

    def telegram_transport(url: str, *, body: bytes, headers: dict[str, str]):
        telegram_calls.append({"url": url, "body": json.loads(body.decode("utf-8")), "headers": headers})
        return {"status": 200}

    def webhook_transport(url: str, *, body: bytes, headers: dict[str, str]):
        webhook_calls.append({"url": url, "body": json.loads(body.decode("utf-8")), "headers": headers})
        return {"status": 202}

    file_result = FileNotificationSink(file_path).send(event)
    telegram_result = TelegramNotificationSink(
        bot_token="token",
        chat_id="42",
        transport=telegram_transport,
    ).send(event)
    webhook_result = WebhookNotificationSink(
        url="https://hooks.example.test/work-hunter",
        secret="secret",
        transport=webhook_transport,
    ).send(event)

    saved = json.loads(file_path.read_text(encoding="utf-8").strip())
    assert file_result == {"status": "sent", "event_type": "run_summary", "sink": "file"}
    assert saved["payload"]["run_id"] == "run-1"
    assert telegram_result["sink"] == "telegram"
    assert telegram_calls[0]["url"].endswith("/bottoken/sendMessage")
    assert telegram_calls[0]["body"]["chat_id"] == "42"
    assert telegram_calls[0]["body"]["parse_mode"] == "HTML"
    assert webhook_result["sink"] == "webhook"
    assert webhook_calls[0]["body"]["event_type"] == "run_summary"
    assert webhook_calls[0]["headers"]["X-Work-Hunter-Event"] == "run_summary"
    assert webhook_calls[0]["headers"]["X-Work-Hunter-Signature"].startswith("sha256=")


def test_agenda_notification_event_builds_telegram_ready_summary():
    event = agenda_notification_event(
        events=[
            HHAgentEvent(
                event_type="interview",
                title="Interview: Python Developer",
                event_at="2026-06-12T15:30:00",
            )
        ],
        tasks=[
            HHAgentTask(
                task_type="test",
                title="Test: Python Developer",
                due_at="2026-06-13T18:00:00",
            )
        ],
    )

    message = telegram_html_message(event)

    assert event.event_type == "agenda"
    assert event.payload == {"events": 1, "tasks": 1}
    assert "Interview: Python Developer" in message
    assert "Test: Python Developer" in message
