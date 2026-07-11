from __future__ import annotations

import json

import pytest

from work_hunter.cli import main as cli_main
from work_hunter.services import WorkHunter


class FakeHHResumeClient:
    created_payloads: list[dict] = []
    published: list[str] = []
    constructed = 0

    def __init__(self, config, *, backend=None):
        type(self).constructed += 1
        self.config = config
        self.backend = backend

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
    FakeHHResumeClient.constructed = 0
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"
    payload = {"title": "Python Backend", "first_name": "Test"}

    result = app.create_hh_resume(payload, dry_run=True)

    assert result == {"status": "dry_run", "payload": payload}
    assert FakeHHResumeClient.created_payloads == []
    assert FakeHHResumeClient.constructed == 0


def test_create_hh_resume_defaults_to_dry_run_without_transport(monkeypatch, tmp_path):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHResumeClient)
    FakeHHResumeClient.created_payloads = []
    FakeHHResumeClient.constructed = 0
    app = WorkHunter(tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"

    result = app.create_hh_resume({"title": "Python Backend"})

    assert result == {"status": "dry_run", "payload": {"title": "Python Backend"}}
    assert FakeHHResumeClient.created_payloads == []
    assert FakeHHResumeClient.constructed == 0


@pytest.mark.parametrize("confirm", [None, False, "true", "false", 1])
def test_create_hh_resume_real_mode_rejects_nonliteral_confirmation(
    monkeypatch, tmp_path, confirm
):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHResumeClient)
    FakeHHResumeClient.created_payloads = []
    FakeHHResumeClient.constructed = 0
    app = WorkHunter(tmp_path)

    result = app.create_hh_resume(
        {"title": "Python Backend"},
        dry_run=False,
        confirm=confirm,
    )

    assert result["status"] == "blocked"
    assert result["code"] == "resume_mutation_requires_confirmation"
    assert FakeHHResumeClient.created_payloads == []
    assert FakeHHResumeClient.constructed == 0


def test_create_hh_resume_can_publish_after_create(monkeypatch, tmp_path):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHResumeClient)
    FakeHHResumeClient.created_payloads = []
    FakeHHResumeClient.published = []
    FakeHHResumeClient.constructed = 0
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"

    result = app.create_hh_resume(
        {"title": "Python Backend"},
        dry_run=False,
        publish=True,
        confirm=True,
    )

    assert result["status"] == "created"
    assert result["id"] == "new-resume"
    assert result["publish_result"] == {
        "status": "published",
        "resume_id": "new-resume",
    }
    assert FakeHHResumeClient.created_payloads == [{"title": "Python Backend"}]
    assert FakeHHResumeClient.published == ["new-resume"]


def test_create_hh_resume_from_file_defaults_to_dry_run_without_transport(
    monkeypatch, tmp_path
):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHResumeClient)
    FakeHHResumeClient.created_payloads = []
    FakeHHResumeClient.constructed = 0
    path = tmp_path / "resume.json"
    path.write_text('{"title": "Python Backend"}', encoding="utf-8")
    app = WorkHunter(root=tmp_path)

    result = app.create_hh_resume_from_file(path, validate=False)

    assert result == {"status": "dry_run", "payload": {"title": "Python Backend"}}
    assert FakeHHResumeClient.created_payloads == []
    assert FakeHHResumeClient.constructed == 0


def test_create_hh_resume_from_file_real_mode_requires_confirmation(
    monkeypatch, tmp_path
):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHResumeClient)
    FakeHHResumeClient.created_payloads = []
    FakeHHResumeClient.constructed = 0
    path = tmp_path / "resume.json"
    path.write_text('{"title": "Python Backend"}', encoding="utf-8")
    app = WorkHunter(root=tmp_path)

    result = app.create_hh_resume_from_file(path, dry_run=False, validate=False)

    assert result["status"] == "blocked"
    assert result["code"] == "resume_mutation_requires_confirmation"
    assert FakeHHResumeClient.created_payloads == []
    assert FakeHHResumeClient.constructed == 0


def test_create_hh_resume_from_file_confirmed_real_mode_is_allowed(
    monkeypatch, tmp_path
):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHResumeClient)
    FakeHHResumeClient.created_payloads = []
    path = tmp_path / "resume.json"
    path.write_text('{"title": "Python Backend"}', encoding="utf-8")
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"

    result = app.create_hh_resume_from_file(
        path,
        dry_run=False,
        validate=False,
        confirm=True,
    )

    assert result["status"] == "created"
    assert FakeHHResumeClient.created_payloads == [{"title": "Python Backend"}]


def test_clone_hh_resume_builds_clean_payload_without_identity_fields(monkeypatch, tmp_path):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHResumeClient)
    FakeHHResumeClient.created_payloads = []
    FakeHHResumeClient.constructed = 0
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"

    result = app.clone_hh_resume("resume-1", title="New Backend")

    assert result["status"] == "dry_run"
    assert result["payload"]["title"] == "New Backend"
    assert result["payload"]["first_name"] == "Test"
    assert "id" not in result["payload"]
    assert "alternate_url" not in result["payload"]
    assert "created_at" not in result["payload"]
    assert FakeHHResumeClient.created_payloads == []


def test_clone_hh_resume_real_mode_requires_confirmation_before_transport(
    monkeypatch, tmp_path
):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHResumeClient)
    FakeHHResumeClient.created_payloads = []
    FakeHHResumeClient.constructed = 0
    app = WorkHunter(root=tmp_path)

    result = app.clone_hh_resume("resume-1", dry_run=False)

    assert result["status"] == "blocked"
    assert result["code"] == "resume_mutation_requires_confirmation"
    assert FakeHHResumeClient.created_payloads == []
    assert FakeHHResumeClient.constructed == 0


def test_clone_hh_resume_confirmed_real_mode_is_allowed(monkeypatch, tmp_path):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHResumeClient)
    FakeHHResumeClient.created_payloads = []
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"

    result = app.clone_hh_resume("resume-1", dry_run=False, confirm=True)

    assert result["status"] == "created"
    assert FakeHHResumeClient.created_payloads == [
        {
            "title": "Old Backend",
            "first_name": "Test",
            "last_name": "User",
        }
    ]


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


def test_resume_cli_is_dry_run_by_default_and_requires_real_plus_confirm(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHResumeClient)
    FakeHHResumeClient.created_payloads = []
    FakeHHResumeClient.constructed = 0
    app = WorkHunter(tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"
    app.save_config(app.config)
    arguments = [
        "--root",
        str(tmp_path),
        "hh-create-resume",
        "--payload",
        '{"title":"Python Backend"}',
    ]

    cli_main(arguments)
    assert json.loads(capsys.readouterr().out)["status"] == "dry_run"
    cli_main([*arguments, "--real"])
    assert json.loads(capsys.readouterr().out)["status"] == "blocked"
    assert FakeHHResumeClient.created_payloads == []
    assert FakeHHResumeClient.constructed == 0


def test_resume_cli_confirmed_real_publish_is_allowed(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHResumeClient)
    FakeHHResumeClient.created_payloads = []
    FakeHHResumeClient.published = []
    app = WorkHunter(tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"
    app.save_config(app.config)

    cli_main(
        [
            "--root",
            str(tmp_path),
            "hh-create-resume",
            "--payload",
            '{"title":"Python Backend"}',
            "--real",
            "--confirm",
            "--publish",
        ]
    )

    result = json.loads(capsys.readouterr().out)
    assert result["publish_result"] == {
        "status": "published",
        "resume_id": "new-resume",
    }


def test_resume_cli_confirmed_real_from_file_is_allowed(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHResumeClient)
    FakeHHResumeClient.created_payloads = []
    app = WorkHunter(tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"
    app.save_config(app.config)
    path = tmp_path / "resume.json"
    path.write_text('{"title": "Python Backend"}', encoding="utf-8")

    cli_main(
        [
            "--root",
            str(tmp_path),
            "hh-create-resume",
            "--payload-file",
            str(path),
            "--real",
            "--confirm",
        ]
    )

    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "created"
    assert FakeHHResumeClient.created_payloads == [{"title": "Python Backend"}]


def test_clone_resume_cli_real_mode_requires_confirm(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHResumeClient)
    FakeHHResumeClient.created_payloads = []
    FakeHHResumeClient.constructed = 0
    app = WorkHunter(tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"
    app.save_config(app.config)
    arguments = ["--root", str(tmp_path), "hh-clone-resume", "resume-1", "--real"]

    cli_main(arguments)
    blocked = json.loads(capsys.readouterr().out)
    assert blocked["status"] == "blocked"
    assert FakeHHResumeClient.constructed == 0

    cli_main([*arguments, "--confirm"])
    created = json.loads(capsys.readouterr().out)
    assert created["status"] == "created"
    assert FakeHHResumeClient.created_payloads == [
        {
            "title": "Old Backend",
            "first_name": "Test",
            "last_name": "User",
        }
    ]
