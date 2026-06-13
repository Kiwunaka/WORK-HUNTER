from __future__ import annotations

import json

from work_hunter.cli import main as cli_main
from work_hunter.services import WorkHunter


class FakeHHResumeClient:
    created_payloads: list[dict] = []
    published: list[str] = []

    def __init__(self, config):
        self.config = config

    def has_token(self):
        return True

    def get_resume(self, resume_id: str):
        return {
            "id": resume_id,
            "title": "Old Backend",
            "first_name": "Test",
            "last_name": "User",
            "alternate_url": f"https://hh.ru/resume/{resume_id}",
            "created_at": "2026-01-01T00:00:00+03:00",
            "updated_at": "2026-01-02T00:00:00+03:00",
        }

    def create_resume(self, payload: dict):
        self.created_payloads.append(payload)
        return {"id": "new-resume", "status": "created", "payload": payload}

    def publish_resume(self, resume_id: str):
        self.published.append(resume_id)
        return {"status": "published", "resume_id": resume_id}


def test_create_hh_resume_dry_run_does_not_post(monkeypatch, tmp_path):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHResumeClient)
    FakeHHResumeClient.created_payloads = []
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"
    payload = {"title": "Python Backend", "first_name": "Test"}

    result = app.create_hh_resume(payload, dry_run=True)

    assert result == {"status": "dry_run", "payload": payload}
    assert FakeHHResumeClient.created_payloads == []


def test_create_hh_resume_can_publish_after_create(monkeypatch, tmp_path):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHResumeClient)
    FakeHHResumeClient.created_payloads = []
    FakeHHResumeClient.published = []
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"

    result = app.create_hh_resume({"title": "Python Backend"}, publish=True)

    assert result["status"] == "created"
    assert result["id"] == "new-resume"
    assert result["publish_result"] == {"status": "published", "resume_id": "new-resume"}
    assert FakeHHResumeClient.created_payloads == [{"title": "Python Backend"}]
    assert FakeHHResumeClient.published == ["new-resume"]


def test_clone_hh_resume_builds_clean_payload_without_identity_fields(monkeypatch, tmp_path):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHResumeClient)
    FakeHHResumeClient.created_payloads = []
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"

    result = app.clone_hh_resume("resume-1", title="New Backend", dry_run=True)

    assert result["status"] == "dry_run"
    assert result["payload"]["title"] == "New Backend"
    assert result["payload"]["first_name"] == "Test"
    assert "id" not in result["payload"]
    assert "alternate_url" not in result["payload"]
    assert "created_at" not in result["payload"]
    assert FakeHHResumeClient.created_payloads == []


def test_create_hh_resume_cli_accepts_json_payload(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHResumeClient)
    FakeHHResumeClient.created_payloads = []
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"
    app.save_config(app.config)

    cli_main(
        [
            "--root",
            str(tmp_path),
            "hh-create-resume",
            "--payload",
            '{"title": "Python Backend"}',
            "--dry-run",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload == {"status": "dry_run", "payload": {"title": "Python Backend"}}
