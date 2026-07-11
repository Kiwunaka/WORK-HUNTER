from __future__ import annotations

import json
from pathlib import Path

from work_hunter.hh_agent.notifications import MemoryNotificationSink
from work_hunter.cli import main as cli_main
from work_hunter.scheduler import SafeTaskRunner
from work_hunter.services import WorkHunter


class FakeSchedulerHHClient:
    constructed = 0
    updated_resumes: list[str] = []

    def __init__(self, config, *, backend=None):
        type(self).constructed += 1
        self.config = config
        self.backend = backend

    def has_token(self):
        return bool(self.config.get("access_token"))

    def list_resumes(self):
        return [{"id": "resume-1", "title": "Backend", "status": {"id": "published"}}]

    def update_resume(self, resume_id: str):
        self.updated_resumes.append(resume_id)
        return {"status": "updated", "resume_id": resume_id}


class FakeApp:
    def __init__(self, root: Path):
        self.root = root
        self.calls: list[tuple[str, dict]] = []

    def sync_sources(self, *, sources=None, limit=None):
        self.calls.append(("sync", {"sources": sources, "limit": limit}))
        return {"hh": {"status": "ok", "count": 2}}

    def score_jobs(self, *, limit=10000):
        self.calls.append(("score", {"limit": limit}))
        return 3

    def refresh_hh_token(self):
        self.calls.append(("hh-refresh-token", {}))
        return {"status": "ok"}

    def update_hh_resumes(self, *, confirm=False):
        self.calls.append(("hh-update-resumes", {"confirm": confirm}))
        return {"status": "ok", "count": 1}

    def plan_hh_campaign(self, **kwargs):
        self.calls.append(("hh-campaign-plan", kwargs))
        return {"status": "planned", "id": 42, "count": 5}


def test_safe_task_runner_runs_whitelisted_tasks_and_writes_json_report(tmp_path):
    app = FakeApp(tmp_path)
    sink = MemoryNotificationSink()
    runner = SafeTaskRunner(app, root=tmp_path, notification_sinks=[sink])

    report = runner.run(
        [
            {"task": "sync", "sources": ["hh"], "limit": 10},
            {"task": "score", "limit": 50},
            {"task": "hh-campaign-plan", "limit": 5, "min_score": 80, "skip_tests": True},
        ]
    )

    assert report["status"] == "completed"
    assert report["run_id"].startswith("runner-")
    assert report["counts"] == {"completed": 3, "failed": 0, "blocked": 0}
    assert report["duration_seconds"] >= 0
    assert [entry["task"] for entry in report["command_log"]] == ["sync", "score", "hh-campaign-plan"]
    assert all(entry["duration_seconds"] >= 0 for entry in report["command_log"])
    assert app.calls == [
        ("sync", {"sources": ["hh"], "limit": 10}),
        ("score", {"limit": 50}),
        (
            "hh-campaign-plan",
            {
                "limit": 5,
                "min_score": 80,
                "skip_tests": True,
                "ai_filter_mode": "off",
                "resume_id": None,
            },
        ),
    ]
    report_path = Path(report["report_path"])
    saved = json.loads(report_path.read_text(encoding="utf-8"))
    assert saved["status"] == "completed"
    assert saved["items"][0]["task"] == "sync"
    assert sink.events[0].event_type == "run_summary"
    assert sink.events[0].payload["run_id"] == report["run_id"]
    assert sink.events[0].payload["duration_seconds"] == report["duration_seconds"]


def test_safe_task_runner_notifies_error_and_captures_command_log(tmp_path):
    class FailingApp(FakeApp):
        def score_jobs(self, *, limit=10000):
            self.calls.append(("score", {"limit": limit}))
            raise RuntimeError("score failed")

    app = FailingApp(tmp_path)
    sink = MemoryNotificationSink()
    runner = SafeTaskRunner(app, root=tmp_path, notification_sinks=[sink])

    report = runner.run([{"task": "score", "limit": 7}])

    assert report["status"] == "partial"
    assert report["counts"] == {"completed": 0, "failed": 1, "blocked": 0}
    assert report["command_log"][0]["status"] == "failed"
    assert report["command_log"][0]["error"] == "score failed"
    assert sink.events[0].payload["status"] == "partial"
    assert "score failed" in sink.events[0].body


def test_safe_task_runner_plans_mutating_resume_update_by_default(tmp_path):
    app = FakeApp(tmp_path)
    runner = SafeTaskRunner(app, root=tmp_path)

    planned = runner.run([{"task": "hh-update-resumes"}])
    confirmed = runner.run([{"task": "hh-update-resumes", "confirm": True}])

    assert planned["items"][0]["status"] == "completed"
    assert planned["items"][0]["result"] == {
        "status": "planned",
        "message": "Scheduled hh-update-resumes requires confirm=true or real=true.",
    }
    assert confirmed["items"][0]["result"] == {"status": "ok", "count": 1}
    assert app.calls == [("hh-update-resumes", {"confirm": True})]


def test_safe_task_runner_forwards_confirmation_to_real_work_hunter(
    monkeypatch, tmp_path
):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeSchedulerHHClient)
    FakeSchedulerHHClient.constructed = 0
    FakeSchedulerHHClient.updated_resumes = []
    app = WorkHunter(tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"

    report = SafeTaskRunner(app, root=tmp_path).run(
        [{"task": "hh-update-resumes", "confirm": True}]
    )

    assert report["status"] == "completed"
    assert report["counts"] == {"completed": 1, "failed": 0, "blocked": 0}
    assert report["items"][0]["result"] == {
        "status": "ok",
        "count": 1,
        "updated": ["resume-1"],
    }
    assert FakeSchedulerHHClient.updated_resumes == ["resume-1"]


def test_safe_task_runner_classifies_real_work_hunter_block_as_blocked(
    monkeypatch, tmp_path
):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeSchedulerHHClient)
    FakeSchedulerHHClient.constructed = 0
    FakeSchedulerHHClient.updated_resumes = []
    app = WorkHunter(tmp_path)
    sink = MemoryNotificationSink()

    report = SafeTaskRunner(app, root=tmp_path, notification_sinks=[sink]).run(
        [{"task": "hh-update-resumes", "confirm": True}]
    )

    assert report["status"] == "blocked"
    assert report["counts"] == {"completed": 0, "failed": 0, "blocked": 1}
    assert report["items"][0]["status"] == "blocked"
    assert report["items"][0]["result"]["status"] == "blocked"
    assert report["command_log"][0]["status"] == "blocked"
    assert sink.events[0].payload["status"] == "blocked"
    assert FakeSchedulerHHClient.updated_resumes == []


def test_safe_task_runner_reports_mixed_completed_and_blocked_results_as_partial(
    monkeypatch, tmp_path
):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeSchedulerHHClient)
    app = WorkHunter(tmp_path)
    sink = MemoryNotificationSink()

    report = SafeTaskRunner(app, root=tmp_path, notification_sinks=[sink]).run(
        [
            {"task": "score"},
            {"task": "hh-update-resumes", "confirm": True},
        ]
    )

    assert report["status"] == "partial"
    assert report["counts"] == {"completed": 1, "failed": 0, "blocked": 1}
    assert [item["status"] for item in report["items"]] == ["completed", "blocked"]
    assert sink.events[0].payload["status"] == "partial"


def test_safe_task_runner_masks_sensitive_task_results_in_memory_and_saved_report(tmp_path):
    class TokenApp(FakeApp):
        def refresh_hh_token(self):
            self.calls.append(("hh-refresh-token", {}))
            return {
                "status": "ok",
                "access_token": "new-access-token",
                "refresh_token": "new-refresh-token",
                "nested": {"client_secret": "client-secret"},
            }

    app = TokenApp(tmp_path)
    runner = SafeTaskRunner(app, root=tmp_path)

    report = runner.run([{"task": "hh-refresh-token"}])
    saved = json.loads(Path(report["report_path"]).read_text(encoding="utf-8"))

    assert report["items"][0]["result"]["access_token"] == "***"
    assert report["items"][0]["result"]["refresh_token"] == "***"
    assert report["items"][0]["result"]["nested"]["client_secret"] == "***"
    assert "new-access-token" not in json.dumps(report, ensure_ascii=False)
    assert "new-refresh-token" not in json.dumps(saved, ensure_ascii=False)


def test_safe_task_runner_blocks_unknown_or_mutating_tasks_by_default(tmp_path):
    app = FakeApp(tmp_path)
    runner = SafeTaskRunner(app, root=tmp_path)

    report = runner.run(
        [
            {"task": "hh-campaign-confirm", "run_id": 42, "confirm": True},
            {"task": "apply-hh", "job_id": 1, "real": True},
        ]
    )

    assert report["status"] == "blocked"
    assert report["counts"] == {"completed": 0, "failed": 0, "blocked": 2}
    assert [item["status"] for item in report["items"]] == ["blocked", "blocked"]
    assert app.calls == []


def test_safe_task_runner_uses_lock_file_to_prevent_overlap(tmp_path):
    app = FakeApp(tmp_path)
    runner = SafeTaskRunner(app, root=tmp_path)
    runner.lock_path.parent.mkdir(parents=True, exist_ok=True)
    runner.lock_path.write_text("busy", encoding="utf-8")

    report = runner.run([{"task": "score"}])

    assert report["status"] == "blocked"
    assert report["counts"]["blocked"] == 1
    assert report["items"][0]["reason"] == "runner_locked"
    assert app.calls == []


def test_scheduler_cli_runs_plan_file_and_outputs_report(monkeypatch, tmp_path, capsys):
    created_apps: list[FakeApp] = []

    def fake_work_hunter(root):
        app = FakeApp(root)
        created_apps.append(app)
        return app

    monkeypatch.setattr("work_hunter.cli.WorkHunter", fake_work_hunter)
    plan_path = tmp_path / "schedule.json"
    plan_path.write_text(json.dumps([{"task": "score", "limit": 12}]), encoding="utf-8")

    cli_main(["--root", str(tmp_path), "runner", "--plan", str(plan_path)])

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "completed"
    assert created_apps[0].calls == [("score", {"limit": 12})]
