from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import data_dir, mask_secrets
from .hh_agent.notifications import NotificationSink, run_summary_event


SAFE_TASKS = {
    "sync",
    "score",
    "hh-refresh-token",
    "hh-update-resumes",
    "hh-campaign-plan",
}


class SafeTaskRunner:
    def __init__(
        self,
        app: Any,
        *,
        root: str | Path | None = None,
        notification_sinks: list[NotificationSink] | None = None,
    ):
        self.app = app
        self.root = Path(root) if root is not None else Path(getattr(app, "root", Path.cwd()))
        self.run_dir = data_dir(self.root)
        self.report_dir = self.run_dir / "reports"
        self.lock_path = self.run_dir / "runner.lock"
        self.notification_sinks = list(notification_sinks or [])

    def run(self, tasks: list[dict[str, Any]]) -> dict[str, Any]:
        started_at = _now()
        run_id = f"runner-{_file_stamp()}"
        if self.lock_path.exists():
            return self._finish_report(
                {
                    "run_id": run_id,
                    "status": "blocked",
                    "started_at": started_at,
                    "finished_at": _now(),
                    "duration_seconds": 0.0,
                    "counts": {"completed": 0, "failed": 0, "blocked": 1},
                    "command_log": [],
                    "items": [
                        {
                            "task": "",
                            "status": "blocked",
                            "reason": "runner_locked",
                            "lock_path": str(self.lock_path),
                        }
                    ],
                }
            )

        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.lock_path.write_text(started_at, encoding="utf-8")
        items: list[dict[str, Any]] = []
        command_log: list[dict[str, Any]] = []
        try:
            for task in tasks:
                task_name = str(task.get("task") or task.get("name") or "")
                task_started_at = _now()
                if task_name not in SAFE_TASKS:
                    item = {
                        "task": task_name,
                        "status": "blocked",
                        "reason": "task_not_safe",
                    }
                    items.append(item)
                    command_log.append(
                        _command_log_entry(task_name, task, task_started_at, _now(), item["status"], item)
                    )
                    continue
                item = self._run_task(task_name, task)
                items.append(item)
                command_log.append(_command_log_entry(task_name, task, task_started_at, _now(), item["status"], item))
        finally:
            try:
                self.lock_path.unlink()
            except FileNotFoundError:
                pass

        counts = {
            "completed": sum(1 for item in items if item["status"] == "completed"),
            "failed": sum(1 for item in items if item["status"] == "failed"),
            "blocked": sum(1 for item in items if item["status"] == "blocked"),
        }
        status = "completed"
        if counts["failed"] or counts["blocked"]:
            status = "partial"
        if counts["completed"] == 0 and counts["blocked"] and not counts["failed"]:
            status = "blocked"
        finished_at = _now()
        return self._finish_report(
            {
                "run_id": run_id,
                "status": status,
                "started_at": started_at,
                "finished_at": finished_at,
                "duration_seconds": _duration_seconds(started_at, finished_at),
                "counts": counts,
                "command_log": command_log,
                "items": items,
            }
        )

    def _run_task(self, task_name: str, task: dict[str, Any]) -> dict[str, Any]:
        try:
            if task_name == "sync":
                result = self.app.sync_sources(
                    sources=task.get("sources"),
                    limit=task.get("limit"),
                )
            elif task_name == "score":
                result = {"scored": self.app.score_jobs(limit=int(task.get("limit") or 10000))}
            elif task_name == "hh-refresh-token":
                result = self.app.refresh_hh_token()
            elif task_name == "hh-update-resumes":
                if not _explicit_real(task):
                    result = {
                        "status": "planned",
                        "message": "Scheduled hh-update-resumes requires confirm=true or real=true.",
                    }
                else:
                    result = self.app.update_hh_resumes(confirm=True)
            elif task_name == "hh-campaign-plan":
                result = self.app.plan_hh_campaign(
                    limit=int(task.get("limit") or 100),
                    min_score=int(task.get("min_score") or task.get("min-score") or 0),
                    skip_tests=bool(task.get("skip_tests") or task.get("skip-tests") or False),
                    ai_filter_mode=str(task.get("ai_filter_mode") or task.get("ai-filter-mode") or "off"),
                    resume_id=task.get("resume_id") or task.get("resume-id"),
                )
            else:
                return {"task": task_name, "status": "blocked", "reason": "task_not_safe"}
            safe_result = mask_secrets(result)
            if isinstance(result, dict) and result.get("status") == "blocked":
                return {
                    "task": task_name,
                    "status": "blocked",
                    "reason": str(result.get("code") or result.get("message") or "operation_blocked"),
                    "result": safe_result,
                }
            return {"task": task_name, "status": "completed", "result": safe_result}
        except Exception as exc:
            return {"task": task_name, "status": "failed", "error": str(exc)}

    def _finish_report(self, report: dict[str, Any]) -> dict[str, Any]:
        if "duration_seconds" not in report:
            report["duration_seconds"] = _duration_seconds(report["started_at"], report["finished_at"])
        report = self._write_report(report)
        report["notifications"] = self._send_notifications(report)
        self._write_report(report, path=Path(report["report_path"]))
        return report

    def _write_report(self, report: dict[str, Any], *, path: Path | None = None) -> dict[str, Any]:
        self.report_dir.mkdir(parents=True, exist_ok=True)
        report_path = path or self.report_dir / f"runner-{_file_stamp()}.json"
        report["report_path"] = str(report_path)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report

    def _send_notifications(self, report: dict[str, Any]) -> list[dict[str, Any]]:
        if not self.notification_sinks:
            return []
        error = _report_error_summary(report)
        event = run_summary_event(
            operation="runner",
            status=str(report.get("status") or ""),
            counts=dict(report.get("counts") or {}),
            error=error,
            run_id=str(report.get("run_id") or ""),
            duration_seconds=float(report.get("duration_seconds") or 0.0),
        )
        results: list[dict[str, Any]] = []
        for sink in self.notification_sinks:
            try:
                results.append(sink.send(event))
            except Exception as exc:
                results.append({"status": "error", "sink": sink.__class__.__name__, "error": str(exc)})
        return results


def load_task_plan(path: str | Path) -> list[dict[str, Any]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("tasks"), list):
        return data["tasks"]
    raise ValueError("Runner plan must be a JSON list or an object with a tasks list.")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _file_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _duration_seconds(started_at: str, finished_at: str) -> float:
    started = datetime.fromisoformat(started_at)
    finished = datetime.fromisoformat(finished_at)
    return round(max(0.0, (finished - started).total_seconds()), 3)


def _command_log_entry(
    task_name: str,
    task: dict[str, Any],
    started_at: str,
    finished_at: str,
    status: str,
    item: dict[str, Any],
) -> dict[str, Any]:
    params = {key: value for key, value in task.items() if key not in {"task", "name"}}
    entry = {
        "task": task_name,
        "params": mask_secrets(params),
        "status": status,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_seconds": _duration_seconds(started_at, finished_at),
    }
    if item.get("error"):
        entry["error"] = str(item["error"])
    if item.get("reason"):
        entry["reason"] = str(item["reason"])
    return entry


def _explicit_real(task: dict[str, Any]) -> bool:
    return bool(task.get("confirm") is True or task.get("real") is True or task.get("dry_run") is False)


def _report_error_summary(report: dict[str, Any]) -> str:
    errors: list[str] = []
    for item in report.get("items") or []:
        if item.get("status") == "failed" and item.get("error"):
            errors.append(f"{item.get('task')}: {item.get('error')}")
        elif item.get("status") == "blocked" and item.get("reason"):
            errors.append(f"{item.get('task') or 'runner'}: {item.get('reason')}")
    return "; ".join(errors)
