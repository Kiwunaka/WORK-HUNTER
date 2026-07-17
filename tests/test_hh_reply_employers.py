from __future__ import annotations

import json

from work_hunter.cli import main as cli_main
from work_hunter.services import WorkHunter


class FakeHHReplyClient:
    sent_messages: list[tuple[str, str, str | None]] = []

    def __init__(self, config, *, backend=None):
        self.config = config
        self.backend = backend

    def has_token(self):
        return True

    def list_negotiations(self, status: str = "active"):
        return [
            {
                "id": "neg-1",
                "chat_id": "chat-1",
                "state": {"id": "active"},
                "viewed_by_opponent": True,
                "vacancy": {
                    "id": "vac-1",
                    "name": "Python Backend",
                    "employer": {"id": "emp-1", "name": "Good Co"},
                },
                "resume": {"id": "resume-1", "title": "Backend"},
            },
            {
                "id": "neg-2",
                "chat_id": "chat-2",
                "state": {"id": "active"},
                "viewed_by_opponent": True,
                "vacancy": {
                    "id": "vac-2",
                    "name": "Data Engineer",
                    "employer": {"id": "emp-2", "name": "Quiet Co"},
                },
                "resume": {"id": "resume-2", "title": "Data"},
            },
        ]

    def list_negotiation_messages(self, negotiation_id: str):
        if negotiation_id == "neg-1":
            return [
                {
                    "id": "msg-1",
                    "text": "Can you tell us more?",
                    "author": {"participant_type": "employer"},
                }
            ]
        return [
            {
                "id": "msg-2",
                "text": "Thanks, I will wait.",
                "author": {"participant_type": "applicant"},
            }
        ]

    def send_negotiation_message(self, negotiation_id: str, message: str, chat_id: str | None = None):
        self.sent_messages.append((negotiation_id, message, chat_id))
        return {"status": "sent", "negotiation_id": negotiation_id, "chat_id": chat_id}


def test_reply_hh_employers_dry_run_plans_without_sending(monkeypatch, tmp_path):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHReplyClient)
    FakeHHReplyClient.sent_messages = []
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"

    result = app.reply_hh_employers(template="Здравствуйте, {employer_name}!", dry_run=True)

    assert result["status"] == "planned"
    assert result["count"] == 1
    assert result["replies"][0]["negotiation_id"] == "neg-1"
    assert result["replies"][0]["message"] == "Здравствуйте, Good Co!"
    assert FakeHHReplyClient.sent_messages == []


def test_reply_hh_employers_confirm_sends_only_employer_threads(monkeypatch, tmp_path):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHReplyClient)
    FakeHHReplyClient.sent_messages = []
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"

    result = app.reply_hh_employers(
        template="Здравствуйте, {employer_name}!",
        dry_run=False,
        confirm=True,
    )

    assert result["status"] == "sent"
    assert result["count"] == 1
    assert FakeHHReplyClient.sent_messages == [
        ("neg-1", "Здравствуйте, Good Co!", "chat-1")
    ]


def test_reply_hh_employers_ai_paginates_and_deduplicates_source_message(
    monkeypatch,
    tmp_path,
):
    class PaginatedReplyClient(FakeHHReplyClient):
        pagination_calls: list[tuple[str, int]] = []

        def list_resumes(self):
            return [
                {
                    "id": "resume-1",
                    "title": "Backend",
                    "status": {"id": "published"},
                }
            ]

        def list_negotiations_paginated(self, *, status, max_pages, per_page=100):
            self.pagination_calls.append(("negotiations", max_pages))
            return super().list_negotiations(status=status)[:1]

        def list_negotiation_messages_paginated(
            self,
            negotiation_id,
            *,
            max_pages,
            per_page=100,
        ):
            self.pagination_calls.append(("messages", max_pages))
            return super().list_negotiation_messages(negotiation_id)

    prompts: list[list[dict]] = []

    def fake_completion(messages, config):
        prompts.append(messages)
        return "Да, расскажу подробнее о Python backend."

    monkeypatch.setattr("work_hunter.services.HHApplyClient", PaginatedReplyClient)
    monkeypatch.setattr("work_hunter.services.chat_completion", fake_completion)
    PaginatedReplyClient.sent_messages = []
    PaginatedReplyClient.pagination_calls = []
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"

    first = app.reply_hh_employers(
        use_ai=True,
        dry_run=False,
        confirm=True,
        max_pages=7,
        message_max_pages=9,
        send_delay_min_seconds=0,
        send_delay_max_seconds=0,
    )
    second = app.reply_hh_employers(
        use_ai=True,
        dry_run=False,
        confirm=True,
        max_pages=7,
        message_max_pages=9,
        send_delay_min_seconds=0,
        send_delay_max_seconds=0,
    )

    assert first["status"] == "sent"
    assert first["count"] == 1
    assert second["count"] == 0
    assert PaginatedReplyClient.sent_messages == [
        ("neg-1", "Да, расскажу подробнее о Python backend.", "chat-1")
    ]
    assert PaginatedReplyClient.pagination_calls == [
        ("negotiations", 7),
        ("messages", 9),
        ("negotiations", 7),
        ("messages", 9),
    ]
    assert "Can you tell us more?" in prompts[0][1]["content"]
    outbox = app.storage.list_hh_agent_outbox(channel="hh_reply_auto")
    assert len(outbox) == 1
    assert outbox[0].status == "sent"


def test_reply_hh_employers_cli_requires_confirm_for_send(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHReplyClient)
    FakeHHReplyClient.sent_messages = []
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"
    app.save_config(app.config)

    cli_main(
        [
            "--root",
            str(tmp_path),
            "hh-reply-employers",
            "--template",
            "Здравствуйте, {employer_name}!",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "planned"
    assert payload["count"] == 1
    assert FakeHHReplyClient.sent_messages == []
