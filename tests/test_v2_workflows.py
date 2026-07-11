from __future__ import annotations

import json

from work_hunter.cli import main as cli_main
from work_hunter.services import WorkHunter
from work_hunter.storage import Storage


class FakeReplyClient:
    sent: list[tuple[str, str, str | None]] = []

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
                "vacancy": {"id": "vac-1", "name": "Python Backend"},
                "employer": {"id": "emp-1", "name": "Acme"},
                "resume": {"id": "resume-1", "title": "Backend"},
            }
        ]

    def list_negotiation_messages(self, negotiation_id: str):
        return [
            {
                "id": "msg-1",
                "text": "Can you share more details?",
                "author": {"participant_type": "employer"},
            }
        ]

    def send_negotiation_message(self, negotiation_id: str, message: str, chat_id: str | None = None):
        self.sent.append((negotiation_id, message, chat_id))
        return {"status": "sent", "access_token": "secret-token"}


def test_strategy_dry_run_is_local_and_persisted(tmp_path, capsys):
    app = WorkHunter(root=tmp_path)
    active_name = str(app.config.get("profile") or "default")

    listed = app.list_strategies()
    result = app.run_strategy(active_name, dry_run=True)
    cli_main(["--root", str(tmp_path), "strategy", "report", active_name])
    report = json.loads(capsys.readouterr().out)

    assert listed["status"] == "ok"
    assert result["status"] == "planned"
    assert result["dry_run"] is True
    assert result["requires_confirmation"] is True
    assert app.storage.get_search_preset(active_name)["last_result"]["status"] == "planned"
    assert report["status"] == "ok"


def test_import_jobs_and_export_jobs_roundtrip(tmp_path):
    csv_path = tmp_path / "jobs.csv"
    csv_path.write_text(
        "source,source_id,title,url,company,remote\n"
        "custom,job-1,Python Backend,https://example.test/jobs/1,Acme,yes\n",
        encoding="utf-8",
    )
    app = WorkHunter(root=tmp_path)

    imported = app.import_jobs_file(csv_path)
    exported_csv = app.export_jobs(source="custom", format="csv")
    exported_jsonl = app.export_jobs(source="custom", format="jsonl")

    assert imported["status"] == "ok"
    assert imported["count"] == 1
    assert "Python Backend" in exported_csv
    assert json.loads(exported_jsonl.splitlines()[0])["source_id"] == "job-1"


def test_hh_reply_plan_persists_and_confirm_sends_once(monkeypatch, tmp_path):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeReplyClient)
    FakeReplyClient.sent = []
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"

    plan = app.plan_hh_reply(
        negotiation_id="neg-1",
        template="Hello {employer_name}, happy to share details.",
    )
    blocked = app.confirm_hh_reply(plan["plan_id"], confirm=False)
    sent = app.confirm_hh_reply(plan["plan_id"], confirm=True)
    repeat = app.confirm_hh_reply(plan["plan_id"], confirm=True)

    assert plan["status"] == "planned"
    assert plan["requires_confirmation"] is True
    assert blocked["status"] == "blocked"
    assert blocked["code"] == "confirm_required"
    assert sent["status"] == "sent"
    assert sent["result"]["access_token"] == "***"
    assert repeat["status"] == "sent"
    assert FakeReplyClient.sent == [("neg-1", "Hello Acme, happy to share details.", "chat-1")]


def test_storage_redacts_secret_json_fields(tmp_path):
    storage = Storage(tmp_path / "db.sqlite3")

    run_id = storage.start_hh_agent_mcp_run("x", {"access_token": "secret"})
    storage.finish_hh_agent_mcp_run(run_id, status="ok", output={"cookie": "session-secret"})
    storage.append_hh_operation_log(
        operation_id=run_id,
        level="info",
        message="stored",
        payload={"nested": {"refresh_token": "secret-refresh"}},
    )

    run = storage.get_hh_agent_mcp_run(run_id)
    logs = storage.list_hh_operation_logs(run_id)

    assert run.input["access_token"] == "***"
    assert run.output["cookie"] == "***"
    assert logs[0].payload["nested"]["refresh_token"] == "***"
