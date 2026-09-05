from __future__ import annotations

import json

import work_hunter.cli as cli_module
from work_hunter.cli import main as cli_main
from work_hunter.models import Job
from work_hunter.services import WorkHunter


class FakeHHClient:
    apply_calls: list[tuple[str, str, str]] = []

    def __init__(self, config, *, backend=None):
        self.config = config
        self.backend = backend

    def has_token(self):
        return True

    def whoami(self):
        return {"id": "me", "auth_type": "applicant"}

    def search_vacancies(self, params):
        if int(params.get("page", 0)) > 0:
            return []
        return [
            {
                "id": "vac-1",
                "name": "Python Backend",
                "alternate_url": "https://hh.ru/vacancy/vac-1",
                "employer": {"name": "Acme"},
                "area": {"name": "Moscow"},
                "schedule": {"id": "remote"},
                "snippet": {"requirement": "Python", "responsibility": "APIs"},
            }
        ]

    def get_vacancy(self, vacancy_id):
        return {"id": vacancy_id, "alternate_url": f"https://hh.ru/vacancy/{vacancy_id}"}

    def suitable_resumes(self, vacancy_id):
        return [{"id": "resume-1"}]

    def list_resumes(self):
        return [{"id": "resume-1"}]

    def apply(self, vacancy_id, resume_id, message):
        self.apply_calls.append((vacancy_id, resume_id, message))
        return {"status": "created", "status_code": 201, "location": "/negotiations/1"}


def test_doctor_and_nested_status_cli_are_json(tmp_path, capsys):
    cli_main(["--root", str(tmp_path), "doctor", "--json"])
    doctor = json.loads(capsys.readouterr().out)
    assert doctor["core"]["database"]["status"] == "ok"
    assert doctor["hh_api"]["status"] == "missing_access_token"
    assert "config_missing" in doctor["warnings"]
    assert doctor["next_actions"][0] == "work-hunter init"

    cli_main(["--root", str(tmp_path), "hh", "auth", "status"])
    status = json.loads(capsys.readouterr().out)
    assert status["status"] == "missing_access_token"

    cli_main(["--root", str(tmp_path), "source", "list", "--json"])
    sources = json.loads(capsys.readouterr().out)
    assert sources["hh"]["search"] == "official_api"


def test_hh_search_imports_jobs_without_campaign_plan(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHClient)
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"
    app.save_config(app.config)

    cli_main([
        "--root",
        str(tmp_path),
        "hh",
        "search",
        "--text",
        "python",
        "--area",
        "1",
        "--limit",
        "5",
    ])

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"
    assert payload["count"] == 1
    assert app.storage.query_readonly("SELECT COUNT(*) AS count FROM hh_campaign_runs")[0]["count"] == 0


def test_hh_apply_plan_persists_and_confirm_requires_flag(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHClient)
    FakeHHClient.apply_calls = []
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"
    app.save_config(app.config)
    job_id = app.storage.upsert_job(
        Job(
            source="hh",
            source_id="vac-1",
            url="https://hh.ru/vacancy/vac-1",
            title="Python Backend",
            company="Acme",
        )
    )

    cli_main(["--root", str(tmp_path), "hh", "apply", "plan", "--job-id", str(job_id)])
    plan = json.loads(capsys.readouterr().out)
    assert plan["status"] == "ready"
    assert plan["plan_id"] > 0

    cli_main(["--root", str(tmp_path), "hh", "apply", "confirm", "--plan-id", str(plan["plan_id"])])
    blocked = json.loads(capsys.readouterr().out)
    assert blocked["status"] == "blocked"
    assert FakeHHClient.apply_calls == []


def test_profile_update_persists_external_application_fields(tmp_path, capsys):
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-test")
    payload = {
        "name": "Candidate",
        "email": "candidate@example.test",
        "phone": "+79990000000",
        "resume_path": str(resume),
    }

    cli_main(
        [
            "--root",
            str(tmp_path),
            "profile",
            "update",
            "--json",
            json.dumps(payload),
        ]
    )

    updated = json.loads(capsys.readouterr().out)
    assert updated["email"] == payload["email"]
    doctor = WorkHunter(root=tmp_path).doctor()
    assert doctor["profile"]["application_fields"]["status"] == "ready"


def test_browser_login_cli_uses_persistent_source_session(monkeypatch, tmp_path, capsys):
    calls = []

    def fake_open_browser_session(**kwargs):
        calls.append(kwargs)
        return {"status": "authenticated", "source": kwargs["source"]}

    monkeypatch.setattr(cli_module, "open_browser_session", fake_open_browser_session)

    cli_main(
        [
            "--root",
            str(tmp_path),
            "browser-login",
            "linkedin",
            "--wait",
            "120",
        ]
    )

    assert json.loads(capsys.readouterr().out)["status"] == "authenticated"
    assert calls[0]["source"] == "linkedin"
    assert calls[0]["wait_seconds"] == 120
