from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from work_hunter.external_apply import ExternalApplyResult
from work_hunter.models import Job, Resume
from work_hunter.services import WorkHunter


def seed(app):
    return app.storage.upsert_job(Job(
        source="habr", source_id="fixture", url="https://example.test/job", title="Fixture",
    ))


@pytest.mark.parametrize("outcome", ["submission_unknown", "submitted_unconfirmed", "timeout"])
def test_restart_does_not_resend_uncertain_application(tmp_path, monkeypatch, outcome):
    calls = []

    def dispatch(self, request):
        calls.append(request)
        if outcome == "timeout":
            raise TimeoutError("response lost")
        return ExternalApplyResult(status=outcome, mode="browser")

    monkeypatch.setattr("work_hunter.services.ExternalApplyDispatcher.apply", dispatch)
    app = WorkHunter(tmp_path)
    job_id = seed(app)
    resume_a = app.storage.save_resume(Resume(name="A", body="Candidate A"))
    resume_b = app.storage.save_resume(Resume(name="B", body="Candidate B"))
    try:
        first = app.confirm_apply(job_id, resume_id=str(resume_a), letter="fixture", confirm=True)
        assert first["status"] == "submission_unknown"
        assert app.storage.get_application(job_id).status == "submission_unknown"
        assert app.storage.get_job(job_id).status != "applied"
        assert app.storage.get_stats()["total_applications"] == 0
    finally:
        app.storage.close()

    restarted = WorkHunter(tmp_path)
    try:
        # Changing the resume is not permission to bypass reconciliation.
        second = restarted.confirm_apply(job_id, resume_id=str(resume_b), letter="changed", confirm=True)
        assert second["status"] == "reconciliation_required"
        assert second["previous_attempt"]["plan_id"] == first["id"]
        assert len(calls) == 1
    finally:
        restarted.storage.close()


def test_restart_after_crash_between_claim_and_dispatch(tmp_path):
    app = WorkHunter(tmp_path)
    job_id = seed(app)
    plan = app.prepare_apply_plan(job_id, letter="fixture")
    assert app.storage.claim_external_apply(plan["id"]) is None
    app.storage.close()

    restarted = WorkHunter(tmp_path)
    try:
        result = restarted.confirm_apply(job_id, letter="fixture", confirm=True)
        assert result["status"] == "reconciliation_required"
        assert result["previous_attempt"]["status"] == "submitting"
    finally:
        restarted.storage.close()


def test_two_workers_dispatch_only_once(tmp_path, monkeypatch):
    app = WorkHunter(tmp_path)
    job_id = seed(app)
    app.storage.close()
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def dispatch(self, request):
        calls.append(request)
        entered.set()
        assert release.wait(10)
        return ExternalApplyResult(status="submission_unknown", mode="session")

    monkeypatch.setattr("work_hunter.services.ExternalApplyDispatcher.apply", dispatch)

    def run():
        worker = WorkHunter(tmp_path)
        try:
            return worker.confirm_apply(job_id, letter="fixture", confirm=True)
        finally:
            worker.storage.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(run)
        try:
            assert entered.wait(10)
            second = pool.submit(run).result(timeout=10)
            assert second["status"] == "reconciliation_required"
        finally:
            release.set()
        assert first.result(timeout=10)["status"] == "submission_unknown"
    assert len(calls) == 1
