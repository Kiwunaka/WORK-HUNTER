from __future__ import annotations

from work_hunter.models import Job
from work_hunter.services import WorkHunter


class FakeHHOutcomeClient:
    apply_result = {"status": "created"}

    def __init__(self, config, *, backend=None):
        self.config = config
        self.backend = backend

    def has_token(self):
        return True

    def get_vacancy(self, vacancy_id: str):
        payloads = {
            "archived": {"id": vacancy_id, "archived": True},
            "relations": {"id": vacancy_id, "relations": ["got_response"]},
            "test": {"id": vacancy_id, "has_test": True},
        }
        return payloads.get(vacancy_id, {"id": vacancy_id})

    def suitable_resumes(self, vacancy_id: str):
        return [{"id": "resume-1"}]

    def list_resumes(self):
        return [{"id": "resume-1"}]

    def apply(self, vacancy_id: str, resume_id: str, message: str):
        return self.apply_result


def _save_hh_job(app: WorkHunter, source_id: str, title: str = "Python") -> int:
    return app.storage.upsert_job(
        Job(
            source="hh",
            source_id=source_id,
            url=f"https://hh.ru/vacancy/{source_id}",
            title=title,
            company="Acme",
        )
    )


def test_hh_campaign_skips_archived_relations_tests_and_previous_skips(monkeypatch, tmp_path):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHOutcomeClient)
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"
    _save_hh_job(app, "archived")
    _save_hh_job(app, "relations")
    _save_hh_job(app, "test")
    _save_hh_job(app, "skipped")
    app.storage.save_hh_skipped_vacancy(
        resume_id="resume-1",
        vacancy_id="skipped",
        reason="ai_rejected",
        alternate_url="https://hh.ru/vacancy/skipped",
        name="Skipped",
        employer_name="Acme",
    )

    result = app.plan_hh_campaign(limit=10, skip_tests=True, resume_id="resume-1")
    items = app.storage.list_hh_campaign_items(result["id"])
    reasons = {item.vacancy_id: item.reason for item in items}
    statuses = {item.vacancy_id: item.status for item in items}

    assert statuses == {
        "archived": "skipped",
        "relations": "skipped",
        "test": "skipped",
        "skipped": "skipped",
    }
    assert reasons == {
        "archived": "archived",
        "relations": "already_applied",
        "test": "test_required",
        "skipped": "skipped_before",
    }
    assert result["counts"]["skipped"] == 4


def test_confirm_apply_preserves_limit_outcome(monkeypatch, tmp_path):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHOutcomeClient)
    FakeHHOutcomeClient.apply_result = {
        "status": "error",
        "error": "limit_exceeded",
        "status_code": 400,
    }
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"
    job_id = _save_hh_job(app, "ready")

    result = app.confirm_apply(job_id, resume_id="resume-1", confirm=True)

    assert result["status"] == "limit_exceeded"
    assert result["raw_result"]["error"] == "limit_exceeded"
