from __future__ import annotations

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

from work_hunter.cli import main as cli_main
from work_hunter.models import Job, JobScore
from work_hunter.hh_autopilot.types import DeliveryCertainty, DispatchOutcome
from work_hunter.services import WorkHunter
from work_hunter.web.server import make_handler


class FakeHHFoundationClient:
    apply_calls: list[tuple[str, str, str]] = []

    def __init__(self, config, *, backend=None):
        self.config = config
        self.backend = backend

    def has_token(self):
        return True

    def whoami(self):
        return {"id": "me", "first_name": "Test", "auth_type": "applicant"}

    def list_resumes(self):
        return [
            {
                "id": "resume-1",
                "title": "Python Backend",
                "alternate_url": "https://hh.ru/resume/resume-1",
                "status": {"id": "published", "name": "published"},
                "can_publish_or_update": True,
                "total_views": 42,
                "new_views": 3,
            },
            {
                "id": "resume-2",
                "title": "Fullstack",
                "alternate_url": "https://hh.ru/resume/resume-2",
                "status": {"id": "not_published", "name": "hidden"},
                "can_publish_or_update": False,
                "total_views": 5,
                "new_views": 0,
            },
        ]

    def suitable_resumes(self, vacancy_id: str):
        return [{"id": "resume-1", "title": "Python Backend"}]

    def get_vacancy(self, vacancy_id: str):
        return {
            "id": vacancy_id,
            "name": "Python Backend",
            "archived": False,
            "response_letter_required": vacancy_id == "letter",
            "has_test": vacancy_id == "test",
            "alternate_url": f"https://hh.ru/vacancy/{vacancy_id}",
        }

    def apply(self, vacancy_id: str, resume_id: str, message: str):
        self.apply_calls.append((vacancy_id, resume_id, message))
        return {
            "status": "created",
            "status_code": 201,
            "location": f"/negotiations/{vacancy_id}",
            "raw_result": {},
        }

    def apply_outcome(self, vacancy_id, resume_id, message, *, timeout_seconds=None):
        self.apply(vacancy_id, resume_id, message)
        return DispatchOutcome(
            code="applied",
            certainty=DeliveryCertainty.DEFINITE_RESPONSE,
            status_code=201,
        )


def _hh_job(app: WorkHunter, source_id: str, title: str, score: int) -> int:
    job_id = app.storage.upsert_job(
        Job(
            source="hh",
            source_id=source_id,
            url=f"https://hh.ru/vacancy/{source_id}",
            title=title,
            company="Acme",
        )
    )
    app.storage.save_score(JobScore(job_id=job_id, total_score=score))
    return job_id


def test_sync_hh_resumes_persists_foundation_records(monkeypatch, tmp_path):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHFoundationClient)
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"

    result = app.sync_hh_resumes()

    resumes = app.storage.list_hh_resumes()
    assert result == {"status": "ok", "count": 2}
    assert [resume.id for resume in resumes] == ["resume-1", "resume-2"]
    assert resumes[0].title == "Python Backend"
    assert resumes[0].status_id == "published"
    assert resumes[0].total_views == 42


def test_hh_whoami_returns_account_payload(monkeypatch, tmp_path):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHFoundationClient)
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"

    payload = app.hh_whoami()

    assert payload["auth_type"] == "applicant"
    assert payload["first_name"] == "Test"


def test_hh_campaign_plan_records_ready_and_skipped_items(monkeypatch, tmp_path):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHFoundationClient)
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"
    ready_id = _hh_job(app, "good", "Python Backend", 95)
    test_id = _hh_job(app, "test", "Python With Test", 90)
    low_id = _hh_job(app, "low", "Python Junior", 40)
    applied_id = _hh_job(app, "applied", "Already Applied", 99)
    app.storage.save_application(applied_id, "applied", "sent")
    app.storage.upsert_job(
        Job(source="habr", source_id="h1", url="https://career.habr.com/vacancies/1", title="Python", company="Acme")
    )

    run = app.plan_hh_campaign(limit=10, min_score=70, skip_tests=True)

    items = app.storage.list_hh_campaign_items(run["id"])
    by_job = {item.job_id: item for item in items}
    assert run["counts"] == {"ready": 1, "skipped": 3, "error": 0, "applied": 0}
    assert by_job[ready_id].status == "ready"
    assert by_job[test_id].status == "skipped"
    assert by_job[test_id].reason == "test_required"
    assert by_job[low_id].reason == "below_min_score"
    assert by_job[applied_id].reason == "already_applied"


def test_hh_campaign_confirm_requires_confirmation_and_updates_items(monkeypatch, tmp_path):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHFoundationClient)
    FakeHHFoundationClient.apply_calls = []
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"
    app.config["sources"]["hh"]["autopilot"]["ranking"].update(
        {"minimum_score": 0, "borderline_low": 0, "ai_mode": "off"}
    )
    _hh_job(app, "good", "Python Backend", 95)
    run = app.plan_hh_campaign(limit=10, min_score=70)

    blocked = app.confirm_hh_campaign(run["id"], confirm=False)
    assert blocked["status"] == "blocked"
    assert FakeHHFoundationClient.apply_calls == []

    result = app.confirm_hh_campaign(run["id"], confirm=True)

    items = app.storage.list_hh_campaign_items(run["id"])
    assert result["counts"]["applied"] == 1
    assert items[0].status == "applied"
    assert FakeHHFoundationClient.apply_calls == [("good", "resume-1", items[0].letter)]


def test_hh_resumes_cli_sync_outputs_json(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHFoundationClient)

    cli_main(["--root", str(tmp_path), "hh-resumes", "--sync"])

    payload = json.loads(capsys.readouterr().out)
    assert payload == {"status": "ok", "count": 2}


def test_hh_whoami_cli_outputs_json(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHFoundationClient)

    cli_main(["--root", str(tmp_path), "hh-whoami"])

    payload = json.loads(capsys.readouterr().out)
    assert payload["id"] == "me"
    assert payload["auth_type"] == "applicant"


def test_hh_campaign_cli_plan_outputs_counts(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHFoundationClient)
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"
    app.save_config(app.config)
    _hh_job(app, "good", "Python Backend", 95)

    cli_main(["--root", str(tmp_path), "hh-campaign-plan", "--min-score", "70", "--limit", "10"])

    payload = json.loads(capsys.readouterr().out)
    assert payload["counts"]["ready"] == 1


def test_hh_foundation_web_api_syncs_resumes_and_plans_campaign(monkeypatch, tmp_path):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHFoundationClient)
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"
    app.save_config(app.config)
    _hh_job(app, "good", "Python Backend", 95)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(tmp_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        req = urllib.request.Request(
            f"{base}/api/hh/resumes/sync",
            data=b"{}",
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            sync_payload = json.loads(response.read().decode("utf-8"))
        with urllib.request.urlopen(f"{base}/api/hh/resumes", timeout=5) as response:
            resumes_payload = json.loads(response.read().decode("utf-8"))
        req = urllib.request.Request(
            f"{base}/api/hh/campaigns/plan",
            data=json.dumps({"min_score": 70, "limit": 10}).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            plan_payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert sync_payload == {"status": "ok", "count": 2}
    assert [resume["id"] for resume in resumes_payload] == ["resume-1", "resume-2"]
    assert plan_payload["counts"]["ready"] == 1
