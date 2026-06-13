from __future__ import annotations

from work_hunter.models import Job
from work_hunter.services import WorkHunter


class FakeHHAIFilterClient:
    def __init__(self, config):
        self.config = config

    def has_token(self):
        return True

    def get_vacancy(self, vacancy_id: str):
        return {"id": vacancy_id, "alternate_url": f"https://hh.ru/vacancy/{vacancy_id}"}

    def suitable_resumes(self, vacancy_id: str):
        return [{"id": "resume-1"}]

    def list_resumes(self):
        return [{"id": "resume-1"}]


def _save_job(app: WorkHunter, source_id: str, title: str) -> int:
    return app.storage.upsert_job(
        Job(
            source="hh",
            source_id=source_id,
            url=f"https://hh.ru/vacancy/{source_id}",
            title=title,
            company="Acme",
            description=f"{title} description",
        )
    )


def test_hh_campaign_ai_filter_skips_rejected_jobs(monkeypatch, tmp_path):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHAIFilterClient)

    def fake_chat_completion(messages, ai_config):
        prompt = messages[-1]["content"]
        if "Bad Fit" in prompt:
            return '{"suitable": false, "reason": "different role"}'
        return '{"suitable": true, "reason": "matches backend"}'

    monkeypatch.setattr("work_hunter.services.chat_completion", fake_chat_completion)
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"
    _save_job(app, "good", "Python Backend")
    _save_job(app, "bad", "Bad Fit")

    result = app.plan_hh_campaign(limit=10, resume_id="resume-1", ai_filter_mode="light")
    items = {item.vacancy_id: item for item in app.storage.list_hh_campaign_items(result["id"])}

    assert items["good"].status == "ready"
    assert items["bad"].status == "skipped"
    assert items["bad"].reason == "ai_rejected"
    assert items["bad"].raw_result["ai_filter"] == {
        "mode": "light",
        "suitable": False,
        "reason": "different role",
    }


def test_hh_campaign_ai_filter_failure_marks_needs_review(monkeypatch, tmp_path):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHAIFilterClient)
    monkeypatch.setattr("work_hunter.services.chat_completion", lambda messages, ai_config: "not json")
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"
    _save_job(app, "uncertain", "Python Backend")

    result = app.plan_hh_campaign(limit=10, resume_id="resume-1", ai_filter_mode="heavy")
    item = app.storage.list_hh_campaign_items(result["id"])[0]

    assert item.status == "error"
    assert item.reason == "needs_review"
    assert item.raw_result["ai_filter"]["needs_review"] is True
