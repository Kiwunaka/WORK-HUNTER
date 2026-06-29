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
    requests: list[tuple[str, str, object | None]] = []

    def __init__(self, config):
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

    def request_json(self, method: str, path: str, data=None):
        self.requests.append((method, path, data))
        return {"method": method, "path": path, "data": data}


class FakeHHWebClient:
    touched: list[str] = []
    sent_messages: list[tuple[str, str]] = []
    left_chats: list[str] = []

    def __init__(self, config):
        self.config = config

    def has_session(self):
        return True

    def touch_resume(self, resume_hash: str):
        self.touched.append(resume_hash)
        return {"status": "updated", "resume_hash": resume_hash}

    def chats_awaiting_reply(self, *, resume_id: str = "", applicant_user_id: str = "", max_pages: int = 10):
        return [
            {
                "chat_id": "chat-1",
                "vacancy_id": "vac-1",
                "vacancy_name": "Python Backend",
                "employer_name": "Good Co",
                "reply_to_message": "Вы готовы обсудить вакансию?",
                "reply_options": ["Да", "Нет"],
                "is_discard": False,
            },
            {
                "chat_id": "chat-discard",
                "vacancy_id": "vac-old",
                "vacancy_name": "Old",
                "employer_name": "Old Co",
                "reply_to_message": "Отказ",
                "reply_options": [],
                "is_discard": True,
            },
        ]

    def send_chat_message(self, chat_id: str, text: str):
        self.sent_messages.append((chat_id, text))
        return {"status": "sent", "chat_id": chat_id, "text": text}

    def leave_chat(self, chat_id: str):
        self.left_chats.append(chat_id)
        return {"status": "left", "chat_id": chat_id}

    def get_vacancy_tests(self, vacancy_id: str):
        return {
            vacancy_id: {
                "uidPk": "uid",
                "guid": "guid",
                "startTime": "start",
                "required": "true",
                "tasks": [{"id": 1, "description": "Почему вы?", "candidateSolutions": []}],
            }
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
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"

    result = app.update_hh_resumes()

    assert result == {"status": "ok", "count": 1, "updated": ["resume-1"]}
    assert FakeHHOperationsClient.updated_resumes == ["resume-1"]


def test_touch_hh_resume_web_requires_confirmation_and_uses_web_session(monkeypatch, tmp_path):
    monkeypatch.setattr("work_hunter.services.HHWebSessionClient", FakeHHWebClient)
    FakeHHWebClient.touched = []
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["resume_hash"] = "resume-hash"

    planned = app.touch_hh_resume_web()
    confirmed = app.touch_hh_resume_web(confirm=True)

    assert planned["status"] == "planned"
    assert planned["requires_confirmation"] is True
    assert confirmed["status"] == "updated"
    assert FakeHHWebClient.touched == ["resume-hash"]


def test_hh_chatik_auto_reply_plans_then_sends_with_confirmation(monkeypatch, tmp_path):
    monkeypatch.setattr("work_hunter.services.HHWebSessionClient", FakeHHWebClient)
    FakeHHWebClient.sent_messages = []
    FakeHHWebClient.left_chats = []
    app = WorkHunter(root=tmp_path)

    planned = app.auto_reply_hh_chats_web(confirm=False)
    sent = app.auto_reply_hh_chats_web(confirm=True)

    assert planned["status"] == "planned"
    assert planned["count"] == 2
    assert planned["replies"][0]["message"] == "Да"
    assert sent["status"] == "sent"
    assert FakeHHWebClient.sent_messages == [("chat-1", "Да")]
    assert FakeHHWebClient.left_chats == ["chat-discard"]


def test_extract_hh_vacancy_tests_web_uses_web_session(monkeypatch, tmp_path):
    monkeypatch.setattr("work_hunter.services.HHWebSessionClient", FakeHHWebClient)
    app = WorkHunter(root=tmp_path)

    result = app.extract_hh_vacancy_tests_web("vac-1")

    assert result["status"] == "ready"
    assert result["vacancy_id"] == "vac-1"
    assert result["count"] == 1
    assert result["tests"]["vac-1"]["tasks"][0]["description"] == "Почему вы?"


def test_hh_call_api_delegates_to_client(monkeypatch, tmp_path):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHOperationsClient)
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"

    result = app.hh_call_api("POST", "/test", data={"hello": "world"})

    assert result == {"method": "POST", "path": "/test", "data": {"hello": "world"}}


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


def test_hh_web_cli_commands_expose_touch_chats_tests_and_daemon(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("work_hunter.services.HHWebSessionClient", FakeHHWebClient)
    FakeHHWebClient.touched = []
    FakeHHWebClient.sent_messages = []
    FakeHHWebClient.left_chats = []
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["resume_hash"] = "resume-hash"
    app.save_config(app.config)

    cli_main(["--root", str(tmp_path), "hh-web-touch-resume", "--confirm"])
    touch_payload = json.loads(capsys.readouterr().out)
    cli_main(["--root", str(tmp_path), "hh-chats", "--max-pages", "1"])
    chats_payload = json.loads(capsys.readouterr().out)
    cli_main(["--root", str(tmp_path), "hh-chat-auto-reply", "--confirm"])
    reply_payload = json.loads(capsys.readouterr().out)
    cli_main(["--root", str(tmp_path), "hh-vacancy-tests", "vac-1"])
    tests_payload = json.loads(capsys.readouterr().out)
    cli_main(["--root", str(tmp_path), "hh-daemon-profile"])
    daemon_payload = json.loads(capsys.readouterr().out)

    assert touch_payload["status"] == "updated"
    assert chats_payload["count"] == 2
    assert reply_payload["status"] == "sent"
    assert tests_payload["status"] == "ready"
    assert {cycle["task"] for cycle in daemon_payload["cycles"]} >= {
        "hh-web-touch-resume",
        "hh-campaign-plan",
        "hh-chats-auto-reply",
    }


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
            data=b"{}",
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
