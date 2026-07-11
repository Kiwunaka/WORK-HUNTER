from __future__ import annotations

import json

from work_hunter.cli import main as cli_main
from work_hunter.resume_payloads import load_hh_resume_payload, validate_hh_resume_payload
from work_hunter.services import WorkHunter


class FakeHHResumeImportClient:
    created_payloads: list[dict] = []

    def __init__(self, config, *, backend=None):
        self.config = config
        self.backend = backend

    def has_token(self):
        return True

    def create_resume(self, payload: dict):
        self.created_payloads.append(payload)
        return {"id": "resume-imported", "payload": payload}

    def publish_resume(self, resume_id: str):
        return {"status": "published", "resume_id": resume_id}


def test_load_hh_resume_payload_from_json_and_toml(tmp_path):
    json_path = tmp_path / "resume.json"
    json_path.write_text(
        json.dumps(
            {
                "title": "Python Backend",
                "first_name": "Test",
                "last_name": "User",
                "area": "1",
                "professional_roles": ["96"],
            }
        ),
        encoding="utf-8",
    )
    toml_path = tmp_path / "resume.toml"
    toml_path.write_text(
        "\n".join(
            [
                'title = "ML Engineer"',
                'first_name = "Test"',
                'last_name = "User"',
                'area = "1"',
                'professional_roles = ["165"]',
            ]
        ),
        encoding="utf-8",
    )

    json_payload = load_hh_resume_payload(json_path)
    toml_payload = load_hh_resume_payload(toml_path)

    assert json_payload["area"] == {"id": "1"}
    assert json_payload["professional_roles"] == [{"id": "96"}]
    assert toml_payload["title"] == "ML Engineer"
    assert toml_payload["professional_roles"] == [{"id": "165"}]


def test_load_hh_resume_payload_from_markdown(tmp_path):
    md_path = tmp_path / "resume.md"
    md_path.write_text(
        """# Python Backend
first_name: Test
last_name: User
area: 1
professional_roles: 96, 165
email: test@example.com
phone: +79990000000

## Skills
Python
FastAPI
PostgreSQL
""",
        encoding="utf-8",
    )

    payload = load_hh_resume_payload(md_path)

    assert payload["title"] == "Python Backend"
    assert payload["area"] == {"id": "1"}
    assert payload["professional_roles"] == [{"id": "96"}, {"id": "165"}]
    assert payload["contact"][0]["type"]["id"] == "email"
    assert "FastAPI" in payload["skills"]


def test_validate_hh_resume_payload_reports_missing_required_fields():
    result = validate_hh_resume_payload({"first_name": "Test"})

    assert result["valid"] is False
    assert "title" in result["missing"]
    assert "last_name" in result["missing"]
    assert "area" in result["missing"]
    assert "professional_roles" in result["missing"]


def test_create_hh_resume_from_file_blocks_invalid_payload(monkeypatch, tmp_path):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHResumeImportClient)
    FakeHHResumeImportClient.created_payloads = []
    path = tmp_path / "resume.json"
    path.write_text('{"first_name": "Test"}', encoding="utf-8")
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"

    result = app.create_hh_resume_from_file(path, validate=True)

    assert result["status"] == "invalid"
    assert "title" in result["validation"]["missing"]
    assert FakeHHResumeImportClient.created_payloads == []


def test_hh_create_resume_cli_accepts_payload_file(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHResumeImportClient)
    FakeHHResumeImportClient.created_payloads = []
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"
    app.save_config(app.config)
    path = tmp_path / "resume.md"
    path.write_text(
        """# Python Backend
first_name: Test
last_name: User
area: 1
professional_roles: 96
email: test@example.com
""",
        encoding="utf-8",
    )

    cli_main(
        [
            "--root",
            str(tmp_path),
            "hh-create-resume",
            "--payload-file",
            str(path),
            "--dry-run",
            "--validate",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "dry_run"
    assert payload["validation"]["valid"] is True
    assert payload["payload"]["title"] == "Python Backend"
