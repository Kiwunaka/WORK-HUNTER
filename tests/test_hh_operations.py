from __future__ import annotations

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

from work_hunter.cli import main as cli_main
from work_hunter.services import WorkHunter
from work_hunter.web.server import make_handler


class FakeHHOperationsClient:
    updated_resumes: list[str] = []
    requests: list[tuple[str, str, object | None, object]] = []
    constructed = 0

    def __init__(self, config):
        type(self).constructed += 1
        self.config = config

    def has_token(self):
        return True

    def list_resumes(self):
        return [
            {"id": "resume-1", "title": "Backend", "status": {"id": "published"}},
            {"id": "resume-2", "title": "Hidden", "status": {"id": "not_published"}},
        ]

    def update_resume(self, resume_id: str):
        self.updated_resumes.append(resume_id)
        return {"status": "updated", "resume_id": resume_id}

    def list_negotiations(self, status: str = "active"):
        return [
            {
                "id": "neg-1",
                "state": {"id": "response"},
                "vacancy": {
                    "id": "vac-1",
                    "name": "Python Backend",
                    "alternate_url": "https://hh.ru/vacancy/vac-1",
                    "contacts": {
                        "name": "Recruiter",
                        "email": "hr@example.test",
                        "phones": [{"formatted": "+7 999 000-00-00"}],
                    },
                },
                "employer": {
                    "id": "emp-1",
                    "name": "Acme",
                    "type": "company",
                    "alternate_url": "https://hh.ru/employer/emp-1",
                    "site_url": "https://acme.test",
                },
                "chat_id": "chat-1",
                "resume": {"id": "resume-1"},
            }
        ]

    def request_json(self, method: str, path: str, data=None, params=None):
        self.requests.append((method, path, data, params or {}))
        return {
            "method": method,
            "path": path,
            "data": data,
            "params": params or {},
            "access_token": "secret-token",
        }


def test_sync_hh_negotiations_persists_related_records(monkeypatch, tmp_path):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHOperationsClient)
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"

    result = app.sync_hh_negotiations(status="active")

    assert result == {"status": "ok", "count": 1}
    negotiations = app.storage.list_hh_negotiations()
    employers = app.storage.list_hh_employers()
    contacts = app.storage.list_hh_contacts()
    assert negotiations[0].id == "neg-1"
    assert negotiations[0].vacancy_id == "vac-1"
    assert employers[0].id == "emp-1"
    assert employers[0].site_url == "https://acme.test"
    assert contacts[0].email == "hr@example.test"
    assert contacts[0].phone_numbers == "+7 999 000-00-00"


def test_hh_skipped_vacancies_can_be_saved_listed_and_cleared(tmp_path):
    app = WorkHunter(root=tmp_path)

    app.storage.save_hh_skipped_vacancy(
        resume_id="resume-1",
        vacancy_id="vac-1",
        reason="test_required",
        alternate_url="https://hh.ru/vacancy/vac-1",
        name="Python",
        employer_name="Acme",
    )

    skipped = app.storage.list_hh_skipped_vacancies()
    assert len(skipped) == 1
    assert skipped[0].reason == "test_required"
    assert app.clear_hh_skipped_vacancies() == {"status": "ok", "count": 1}
    assert app.storage.list_hh_skipped_vacancies() == []


def test_update_hh_resumes_updates_only_publishable_resumes(monkeypatch, tmp_path):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHOperationsClient)
    FakeHHOperationsClient.updated_resumes = []
    FakeHHOperationsClient.constructed = 0
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"

    result = app.update_hh_resumes(confirm=True)

    assert result == {"status": "ok", "count": 1, "updated": ["resume-1"]}
    assert FakeHHOperationsClient.updated_resumes == ["resume-1"]


def test_update_hh_resumes_blocks_before_transport_without_confirmation(
    monkeypatch, tmp_path
):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHOperationsClient)
    FakeHHOperationsClient.updated_resumes = []
    FakeHHOperationsClient.constructed = 0
    app = WorkHunter(tmp_path)

    result = app.update_hh_resumes()

    assert result["status"] == "blocked"
    assert result["code"] == "resume_mutation_requires_confirmation"
    assert FakeHHOperationsClient.updated_resumes == []
    assert FakeHHOperationsClient.constructed == 0


def test_update_hh_resumes_cli_requires_confirm(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHOperationsClient)
    FakeHHOperationsClient.updated_resumes = []
    FakeHHOperationsClient.constructed = 0
    app = WorkHunter(tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"
    app.save_config(app.config)
    arguments = ["--root", str(tmp_path), "hh-update-resumes"]

    cli_main(arguments)
    blocked = json.loads(capsys.readouterr().out)
    assert blocked["status"] == "blocked"
    assert FakeHHOperationsClient.updated_resumes == []
    assert FakeHHOperationsClient.constructed == 0

    cli_main([*arguments, "--confirm"])
    updated = json.loads(capsys.readouterr().out)
    assert updated == {"status": "ok", "count": 1, "updated": ["resume-1"]}
    assert FakeHHOperationsClient.updated_resumes == ["resume-1"]


def test_hh_call_api_blocks_mutations_without_confirm_and_masks_result(monkeypatch, tmp_path):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHOperationsClient)
    FakeHHOperationsClient.requests = []
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"

    blocked = app.hh_call_api("POST", "/test", data={"hello": "world"})
    result = app.hh_call_api("POST", "/test?access_token=secret", data={"hello": "world"}, confirm=True)

    assert blocked["status"] == "blocked"
    assert blocked["code"] == "mutation_requires_confirm"
    assert result["status"] == "ok"
    assert result["path"] == "/test"
    assert result["params"]["access_token"] == "***"
    assert result["result"]["access_token"] == "***"
    assert FakeHHOperationsClient.requests == [
        ("POST", "/test", {"hello": "world"}, {"access_token": "secret"})
    ]


def test_hh_search_uses_web_fallback_without_access_token(monkeypatch, tmp_path):
    html = """
    <div class="vacancy-serp-item">
      <a data-qa="serp-item__title" href="/vacancy/123">Python Backend</a>
      <span data-qa="vacancy-serp__vacancy-employer-text">Acme</span>
      <span data-qa="vacancy-serp__vacancy-address">Remote</span>
    </div>
    """

    monkeypatch.setattr("work_hunter.sources.hh.fetch_url", lambda *args, **kwargs: html)
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["access_token"] = ""
    app.config["sources"]["hh"]["refresh_token"] = ""
    app.config["sources"]["hh"]["web_fallback"] = True

    result = app.search_hh_vacancies(text="python", area=["1"], limit=1)

    assert result["status"] == "ok"
    assert result["transport"] == "web"
    assert result["fallback_reason"] == "missing_access_token"
    assert result["count"] == 1
    jobs = app.storage.list_jobs(source="hh", limit=5)
    assert jobs[0].source_id == "123"
    assert jobs[0].title == "Python Backend"


def test_hh_operations_cli_syncs_negotiations_and_clears_skipped(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHOperationsClient)
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"
    app.save_config(app.config)
    app.storage.save_hh_skipped_vacancy(resume_id="r", vacancy_id="v", reason="old")

    cli_main(["--root", str(tmp_path), "hh-negotiations", "--sync"])
    sync_payload = json.loads(capsys.readouterr().out)
    cli_main(["--root", str(tmp_path), "hh-skipped", "--clear"])
    clear_payload = json.loads(capsys.readouterr().out)

    assert sync_payload == {"status": "ok", "count": 1}
    assert clear_payload == {"status": "ok", "count": 1}


def test_hh_operations_web_api_exposes_operational_loop(monkeypatch, tmp_path):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHOperationsClient)
    FakeHHOperationsClient.updated_resumes = []
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"
    app.save_config(app.config)
    app.storage.save_hh_skipped_vacancy(resume_id="r", vacancy_id="v", reason="old")
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(tmp_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        req = urllib.request.Request(
            f"{base}/api/hh/negotiations/sync",
            data=b"{}",
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            sync_payload = json.loads(response.read().decode("utf-8"))
        with urllib.request.urlopen(f"{base}/api/hh/negotiations", timeout=5) as response:
            negotiations_payload = json.loads(response.read().decode("utf-8"))
        req = urllib.request.Request(
            f"{base}/api/hh/resumes/update",
            data=b'{"confirm": true}',
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            update_payload = json.loads(response.read().decode("utf-8"))
        req = urllib.request.Request(
            f"{base}/api/hh/skipped/clear",
            data=b"{}",
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            clear_payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert sync_payload == {"status": "ok", "count": 1}
    assert negotiations_payload[0]["id"] == "neg-1"
    assert update_payload == {"status": "ok", "count": 1, "updated": ["resume-1"]}
    assert clear_payload == {"status": "ok", "count": 1}


def test_resume_http_update_rejects_string_confirmation(monkeypatch, tmp_path):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHOperationsClient)
    FakeHHOperationsClient.updated_resumes = []
    FakeHHOperationsClient.constructed = 0
    app = WorkHunter(tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"
    app.save_config(app.config)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(tmp_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/hh/resumes/update",
            data=b'{"confirm": "false"}',
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            result = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert result["status"] == "blocked"
    assert result["code"] == "resume_mutation_requires_confirmation"
    assert FakeHHOperationsClient.updated_resumes == []
    assert FakeHHOperationsClient.constructed == 0


def test_hh_operator_summary_exposes_cockpit_metrics(monkeypatch, tmp_path):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHOperationsClient)
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"
    app.sync_hh_resumes()
    app.sync_hh_negotiations()
    app.storage.save_hh_skipped_vacancy(resume_id="r1", vacancy_id="v1", reason="test_required")
    app.storage.save_hh_skipped_vacancy(resume_id="r1", vacancy_id="v2", reason="below_min_score")
    app.storage.save_hh_skipped_vacancy(resume_id="r1", vacancy_id="v3", reason="test_required")

    summary = app.hh_operator_summary()

    assert summary["resumes"]["total"] == 2
    assert summary["negotiations"]["total"] == 1
    assert summary["contacts"]["total"] == 1
    assert summary["skipped"]["total"] == 3
    assert summary["skipped"]["by_reason"] == {"below_min_score": 1, "test_required": 2}
    assert "test_required" in summary["recommendations"][0]
