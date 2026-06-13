from __future__ import annotations

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from work_hunter.models import Job
from work_hunter.services import WorkHunter
from work_hunter.cli import main as cli_main
from work_hunter.web.server import make_handler


class FakeHHClient:
    apply_calls: list[tuple[str, str, str]] = []

    def __init__(self, config):
        self.config = config

    def has_token(self):
        return True

    def get_vacancy(self, vacancy_id: str):
        return {
            "id": vacancy_id,
            "response_letter_required": True,
            "has_test": False,
            "alternate_url": f"https://hh.ru/vacancy/{vacancy_id}",
        }

    def list_resumes(self):
        return [{"id": "fallback-resume", "title": "Fallback"}]

    def suitable_resumes(self, vacancy_id: str):
        return [{"id": "resume-1", "title": "Python Backend"}]

    def apply(self, vacancy_id: str, resume_id: str, message: str):
        self.apply_calls.append((vacancy_id, resume_id, message))
        return {
            "status": "created",
            "status_code": 201,
            "location": f"/negotiations/{vacancy_id}",
            "raw_result": {},
        }


def test_prepare_apply_plan_for_hh_uses_exact_vacancy_without_sending(monkeypatch, tmp_path):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHClient)
    FakeHHClient.apply_calls = []
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"
    job_id = app.storage.upsert_job(
        Job(source="hh", source_id="123", url="https://hh.ru/vacancy/123", title="Python", company="Acme")
    )

    plan = app.prepare_apply_plan(job_id)

    assert plan["status"] == "ready"
    assert plan["source"] == "hh"
    assert plan["resume_id"] == "resume-1"
    assert plan["requires_confirmation"] is True
    assert "letter_required" in plan["risk_flags"]
    assert FakeHHClient.apply_calls == []


def test_confirm_apply_requires_explicit_confirmation(monkeypatch, tmp_path):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHClient)
    FakeHHClient.apply_calls = []
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"
    job_id = app.storage.upsert_job(
        Job(source="hh", source_id="123", url="https://hh.ru/vacancy/123", title="Python", company="Acme")
    )

    result = app.confirm_apply(job_id, resume_id="resume-1", letter="Hi", confirm=False)

    assert result["status"] == "blocked"
    assert "confirmation" in result["message"].lower()
    assert FakeHHClient.apply_calls == []


def test_confirm_apply_posts_exact_vacancy_after_confirmation(monkeypatch, tmp_path):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHClient)
    FakeHHClient.apply_calls = []
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"
    job_id = app.storage.upsert_job(
        Job(source="hh", source_id="123", url="https://hh.ru/vacancy/123", title="Python", company="Acme")
    )

    result = app.confirm_apply(job_id, resume_id="resume-1", letter="Hi", confirm=True)

    assert result["status"] == "applied"
    assert FakeHHClient.apply_calls == [("123", "resume-1", "Hi")]
    assert app.storage.get_application(job_id).status == "applied"


def test_non_hh_apply_plan_is_external_prepare_only(tmp_path):
    app = WorkHunter(root=tmp_path)
    job_id = app.storage.upsert_job(
        Job(source="habr", source_id="h1", url="https://career.habr.com/vacancies/1", title="Python")
    )

    plan = app.prepare_apply_plan(job_id)

    assert plan["status"] == "external"
    assert plan["mode"] == "external_page"
    assert plan["external_url"] == "https://career.habr.com/vacancies/1"
    assert plan["raw_result"]["capabilities"]["search"] == "frontend_json"
    assert "external_manual_apply" in plan["risk_flags"]


def test_getmatch_apply_plan_fetches_detail_and_marks_personal_auth_recon(monkeypatch, tmp_path):
    def fake_fetch(url: str, **kwargs):
        assert url == "https://getmatch.ru/api/offers/34397"
        return json.dumps(
            {
                "id": 34397,
                "position": "AQA Python Engineer",
                "url": "/vacancies/34397-aqa-python",
                "company": {"name": "Bureau"},
                "cover_letter_required": True,
                "cover_letter_placeholder": "Расскажите про опыт API automation",
                "application": {"state": "available"},
                "offer_description": "Pytest and API automation",
            }
        )

    monkeypatch.setattr("work_hunter.sources.getmatch.fetch_url", fake_fetch)
    app = WorkHunter(root=tmp_path)
    job_id = app.storage.upsert_job(
        Job(
            source="getmatch",
            source_id="34397",
            url="https://getmatch.ru/vacancies/34397-aqa-python",
            title="AQA Python Engineer",
            company="Bureau",
        )
    )

    plan = app.prepare_apply_plan(job_id)

    assert plan["status"] == "external"
    assert plan["mode"] == "external_api_recon"
    assert plan["external_url"] == "https://getmatch.ru/vacancies/34397-aqa-python"
    assert "source_detail_api_available" in plan["risk_flags"]
    assert "letter_required" in plan["risk_flags"]
    assert "personal_auth_required" in plan["risk_flags"]
    assert plan["raw_result"]["apply"]["cover_letter_required"] is True
    assert plan["raw_result"]["apply"]["cover_letter_placeholder"] == "Расскажите про опыт API automation"
    assert plan["raw_result"]["source_detail"]["position"] == "AQA Python Engineer"
    assert plan["raw_result"]["next_actions"][0]["type"] == "open_url"
    assert any(action["type"] == "api_recon_har" for action in plan["raw_result"]["next_actions"])
    assert any(action["type"] == "cover_letter_hint" for action in plan["raw_result"]["next_actions"])


def test_source_capabilities_describe_hh_level_integrations(tmp_path):
    app = WorkHunter(root=tmp_path)

    capabilities = app.source_capabilities()

    assert capabilities["hh"]["apply"] == "official_api"
    assert capabilities["getmatch"]["detail"] == "public_json"
    assert capabilities["getmatch"]["apply"] == "personal_auth_recon"
    assert capabilities["rvc"]["search"] == "personal_auth_recon"


def test_source_capabilities_cli_outputs_json(tmp_path, capsys):
    cli_main(["--root", str(tmp_path), "source-capabilities"])

    payload = json.loads(capsys.readouterr().out)

    assert payload["hh"]["apply"] == "official_api"
    assert payload["getmatch"]["detail"] == "public_json"


def test_apply_plan_cli_outputs_plan_for_any_source(tmp_path, capsys):
    app = WorkHunter(root=tmp_path)
    job_id = app.storage.upsert_job(
        Job(
            source="habr",
            source_id="h1",
            url="https://career.habr.com/vacancies/1",
            title="Python Developer",
            company="Acme",
        )
    )

    cli_main(["--root", str(tmp_path), "apply-plan", str(job_id), "--resume-id", "resume-1"])

    payload = json.loads(capsys.readouterr().out)
    assert payload["source"] == "habr"
    assert payload["resume_id"] == "resume-1"
    assert payload["mode"] == "external_page"
    assert payload["raw_result"]["next_actions"][0] == {
        "type": "open_url",
        "url": "https://career.habr.com/vacancies/1",
    }


def test_apply_plan_endpoint_returns_plan(monkeypatch, tmp_path):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHClient)
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"
    app.save_config(app.config)
    job_id = app.storage.upsert_job(
        Job(source="hh", source_id="123", url="https://hh.ru/vacancy/123", title="Python", company="Acme")
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(tmp_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/api/jobs/{job_id}/apply-plan"
        req = urllib.request.Request(url, data=b"{}", method="POST", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert payload["status"] == "ready"
    assert payload["resume_id"] == "resume-1"
