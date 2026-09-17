from __future__ import annotations

from dataclasses import replace

import pytest

from work_hunter.external_apply import ExternalApplyRequest, resolve_typed_answer
from work_hunter.models import Job, Resume
from work_hunter.services import WorkHunter


def request(tmp_path, profile=None, answers=None):
    return ExternalApplyRequest(root=tmp_path,
        job=Job(source="habr", source_id="1", url="https://example.test/job/1", title="Fixture"),
        letter="Letter", profile=profile or {}, about={}, ai_config={},
        source_config={"apply_adapter": {"answers": answers or {}}}, global_config={})


@pytest.mark.parametrize("label,key", [
    ("First name", "first_name"), ("Имя", "first_name"),
    ("Last name", "last_name"), ("Фамилия", "last_name"),
    ("Full name", "name"), ("ФИО", "name"), ("Company name", "employer_name"),
])
def test_name_entities_are_distinct(tmp_path, label, key):
    profile = {"first_name": "Анна", "last_name": "Иванова-Петрова", "name": "Анна Иванова-Петрова",
               "employer_name": "Компания"}
    answer = resolve_typed_answer(label, field_type="text", options=None, request=request(tmp_path, profile))
    assert answer.value == profile[key]
    assert answer.provenance == f"profile.{key}"


@pytest.mark.parametrize("label", ["First name", "Last name", "Company name"])
def test_display_name_does_not_fill_missing_entities(tmp_path, label):
    answer = resolve_typed_answer(label, field_type="text", options=None,
                                  request=request(tmp_path, {"name": "Arbitrary display name"}))
    assert answer.status == "needs_answer"


def test_consent_is_bound_to_job_origin_label_and_expiry(tmp_path):
    decision = {"source": "user", "value": True, "origin": "https://example.test", "job_id": "1",
                "label": "data processing", "expires_at": "2099-01-01T00:00:00+00:00"}
    original = request(tmp_path, answers={"consent": decision})
    def resolve(req, label="Data processing"):
        return resolve_typed_answer(label, field_id="consent", field_type="checkbox", options=None, request=req)
    assert resolve(original).value == "true"
    assert resolve(original, "Work authorization").value is None
    assert resolve(replace(original, job=replace(original.job, source_id="2"))).value is None
    assert resolve(replace(original, job=replace(original.job, url="https://other.test/job/1"))).value is None
    expired = request(tmp_path, answers={"consent": {**decision, "expires_at": "2000-01-01T00:00:00+00:00"}})
    assert resolve(expired).value is None


def test_selected_resume_uses_its_artifact_and_changed_file_invalidates_approval(tmp_path, monkeypatch):
    app = WorkHunter(tmp_path)
    file_a = tmp_path / "A.txt"
    file_b = tmp_path / "B.txt"
    file_a.write_text("Candidate A", encoding="utf-8")
    file_b.write_text("Candidate B", encoding="utf-8")
    app.config["profiles"]["default"]["resume_path"] = str(file_a)
    resume_id = app.storage.save_resume(Resume(name="B", body="Candidate B", file_path=str(file_b)))
    job_id = app.storage.upsert_job(request(tmp_path).job)
    try:
        plan = app.prepare_apply_plan(job_id, resume_id=str(resume_id), letter="Letter")
        assert plan["status"] == "ready"
        from pathlib import Path
        artifact = Path(plan["bundle"]["resume"]["path"])
        assert artifact.read_text("utf-8") == "Candidate B"
        file_b.write_text("Changed after review", encoding="utf-8")
        monkeypatch.setattr("work_hunter.services.ExternalApplyDispatcher.apply",
                            lambda *args: pytest.fail("stale approval reached dispatcher"))
        result = app.confirm_apply_plan(plan["id"], confirm=True)
        assert result["code"] == "application_bundle_changed"
        assert artifact.read_text("utf-8") == "Candidate B"
    finally:
        app.storage.close()


def test_resume_from_another_profile_is_blocked(tmp_path):
    app = WorkHunter(tmp_path)
    try:
        resume_id = app.storage.save_resume(Resume(name="Other", body="Other candidate", profile_id="other"))
        job_id = app.storage.upsert_job(request(tmp_path).job)
        assert app.prepare_apply_plan(job_id, resume_id=str(resume_id))["status"] == "blocked"
    finally:
        app.storage.close()
