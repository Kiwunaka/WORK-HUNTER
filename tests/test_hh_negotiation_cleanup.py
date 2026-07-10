from __future__ import annotations

import json

from work_hunter.cli import main as cli_main
from work_hunter.services import WorkHunter


class FakeHHCleanupClient:
    cancelled: list[tuple[str, str]] = []
    blacklisted: list[str] = []

    def __init__(self, config, *, backend=None):
        self.config = config
        self.backend = backend

    def has_token(self):
        return True

    def list_negotiations(self, status: str = "active"):
        return [
            {
                "id": "neg-discard",
                "state": {"id": "discard"},
                "updated_at": "2026-06-08T10:00:00+03:00",
                "vacancy": {"id": "vac-1", "name": "Backend"},
                "employer": {"id": "emp-1", "name": "ATS Co"},
                "chat_id": "chat-1",
                "resume": {"id": "resume-1"},
            },
            {
                "id": "neg-old",
                "state": {"id": "response"},
                "updated_at": "2026-05-01T10:00:00+03:00",
                "vacancy": {"id": "vac-2", "name": "API"},
                "employer": {"id": "emp-2", "name": "Old Co"},
                "chat_id": "chat-2",
                "resume": {"id": "resume-1"},
            },
            {
                "id": "neg-fresh",
                "state": {"id": "response"},
                "updated_at": "2026-06-08T10:00:00+03:00",
                "vacancy": {"id": "vac-3", "name": "Fresh"},
                "employer": {"id": "emp-3", "name": "Fresh Co"},
                "chat_id": "chat-3",
                "resume": {"id": "resume-1"},
            },
        ]

    def cancel_negotiation(self, negotiation_id: str, message: str = ""):
        self.cancelled.append((negotiation_id, message))
        return {"status": "cancelled", "negotiation_id": negotiation_id}

    def blacklist_employer(self, employer_id: str):
        self.blacklisted.append(employer_id)
        return {"status": "blacklisted", "employer_id": employer_id}


def test_hh_negotiation_cleanup_dry_run_plans_without_side_effects(monkeypatch, tmp_path):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHCleanupClient)
    FakeHHCleanupClient.cancelled = []
    FakeHHCleanupClient.blacklisted = []
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"

    result = app.plan_hh_negotiation_cleanup(
        status="active",
        max_age_days=7,
        now="2026-06-09T00:00:00+03:00",
    )

    assert result["status"] == "planned"
    assert result["count"] == 2
    assert [action["negotiation_id"] for action in result["actions"]] == ["neg-discard", "neg-old"]
    assert [action["reason"] for action in result["actions"]] == ["discarded", "stale"]
    assert FakeHHCleanupClient.cancelled == []
    assert FakeHHCleanupClient.blacklisted == []
    events = app.storage.list_hh_cleanup_events()
    assert [(event["negotiation_id"], event["status"]) for event in events] == [
        ("neg-discard", "planned"),
        ("neg-old", "planned"),
    ]


def test_hh_negotiation_cleanup_confirm_cancels_and_blacklists(monkeypatch, tmp_path):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHCleanupClient)
    FakeHHCleanupClient.cancelled = []
    FakeHHCleanupClient.blacklisted = []
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"

    result = app.confirm_hh_negotiation_cleanup(
        status="active",
        max_age_days=7,
        blacklist=True,
        decline_message="Спасибо, не актуально",
        now="2026-06-09T00:00:00+03:00",
        confirm=True,
    )

    assert result["status"] == "completed"
    assert result["count"] == 2
    assert FakeHHCleanupClient.cancelled == [
        ("neg-discard", "Спасибо, не актуально"),
        ("neg-old", "Спасибо, не актуально"),
    ]
    assert FakeHHCleanupClient.blacklisted == ["emp-1", "emp-2"]
    events = app.storage.list_hh_cleanup_events()
    assert [(event["negotiation_id"], event["status"]) for event in events] == [
        ("neg-discard", "completed"),
        ("neg-old", "completed"),
    ]
    assert events[0]["raw_result"]["cancel_result"]["status"] == "cancelled"
    assert events[0]["raw_result"]["blacklist_result"]["status"] == "blacklisted"


def test_hh_negotiation_cleanup_cli_dry_run(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHCleanupClient)
    FakeHHCleanupClient.cancelled = []
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"
    app.save_config(app.config)

    cli_main(
        [
            "--root",
            str(tmp_path),
            "hh-negotiation-cleanup",
            "--max-age-days",
            "7",
            "--now",
            "2026-06-09T00:00:00+03:00",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "planned"
    assert payload["count"] == 2
    assert FakeHHCleanupClient.cancelled == []
