from __future__ import annotations

import json

from work_hunter.cli import main as cli_main
from work_hunter.services import WorkHunter


class FakeHHReplyClient:
    sent_messages: list[tuple[str, str, str | None]] = []

    def __init__(self, config):
        self.config = config

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
