from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import requests

from work_hunter import mcp_server
from work_hunter.cli import main as cli_main
from work_hunter.hh_transport.applicant_web import (
    HHApplicantWebClient,
    applicant_profile_summary,
)
from work_hunter.hh_transport.chatik import (
    HHChatikClient,
    chatik_message_count,
    chatik_message_history,
    chatik_write_allowed,
    extract_chatik_candidates,
    load_hh_cookie_file,
)
from work_hunter.scheduler import SafeTaskRunner
from work_hunter.services import WorkHunter


class FakeResponse:
    def __init__(
        self,
        payload: dict[str, Any],
        status_code: int = 200,
        *,
        text: str = "",
    ) -> None:
        self.payload = payload
        self.status_code = status_code
        self.text = text
        self.content = json.dumps(payload).encode("utf-8") if payload else b""

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def json(self) -> dict[str, Any]:
        return self.payload


class FakeHTTPSession:
    def __init__(self, responses: list[Any] | None = None) -> None:
        self.headers: dict[str, str] = {}
        self.cookies = requests.cookies.RequestsCookieJar()
        self.responses = list(responses or [{}])
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        response = self.responses.pop(0)
        return response if isinstance(response, FakeResponse) else FakeResponse(response)


def _chatik_page() -> dict[str, Any]:
    created = datetime.now(UTC).isoformat()
    return {
        "chats": {
            "page": 0,
            "pages": 1,
            "items": [
                {
                    "id": 101,
                    "currentParticipantId": "77",
                    "resources": {"VACANCY": ["501"], "RESUME": ["601"]},
                    "lastMessage": {
                        "id": "m-1",
                        "creationTime": created,
                        "text": "Готовы выйти в понедельник?",
                        "participantId": "88",
                        "participantDisplay": {"name": "Анна"},
                        "actions": {
                            "text_buttons": [
                                {"text": "Да"},
                                {"text": "Нет"},
                            ]
                        },
                    },
                },
                {
                    "id": 102,
                    "currentParticipantId": "77",
                    "resources": {"VACANCY": ["501"], "RESUME": ["601"]},
                    "lastMessage": {
                        "id": "m-2",
                        "creationTime": created,
                        "text": "Мы выбрали другого кандидата",
                        "participantId": "88",
                        "participantDisplay": {"name": "Анна"},
                        "workflowTransition": {"applicantState": "DISCARD"},
                    },
                },
                {
                    "id": 103,
                    "currentParticipantId": "77",
                    "resources": {"VACANCY": ["501"], "RESUME": ["601"]},
                    "lastMessage": {
                        "id": "m-3",
                        "creationTime": created,
                        "text": "Мой собственный ответ",
                        "participantId": "77",
                    },
                },
            ],
        },
        "resources": {
            "vacancies": {
                "501": {
                    "vacancyId": 501,
                    "name": "Python Developer",
                    "company": {"id": 301, "name": "Example"},
                    "links": {"desktop": "https://hh.ru/vacancy/501"},
                }
            },
            "resumes": {
                "601": {"id": 601, "hash": "resume-hash", "title": "Backend"}
            },
        },
    }


def _chat_detail() -> dict[str, Any]:
    return {
        "chatStates": {"writeMessageState": {"allowed": True}},
        "chat": {
            "messages": {
                "items": [
                    {
                        "creationTime": "2026-08-07T10:00:00Z",
                        "text": "Расскажите про Python",
                        "participantDisplay": {"name": "Анна"},
                    }
                ]
            }
        },
    }


def _applicant_profile() -> dict[str, Any]:
    return {
        "redirectConfig": {},
        "latestResumeHash": "hash-1",
        "account": {
            "firstName": "Иван",
            "middleName": "",
            "lastName": "Иванов",
            "email": "candidate@example.com",
        },
        "userNotifications": [{"userId": 77}],
        "applicantResumes": [
            {
                "_attributes": {"id": "601", "hash": "hash-1"},
                "title": [{"string": "Backend"}],
            }
        ],
        "config": {
            "externalMicroFrontendHosts": {
                "chatik": "https://chatik.hh.ru",
                "resume-profile-front": "https://resume-profile.hh.ru",
            }
        },
    }


def test_cookie_loader_supports_json_and_netscape(tmp_path: Path) -> None:
    json_path = tmp_path / "cookies.json"
    json_path.write_text(
        json.dumps(
            {
                "cookies": [
                    {"domain": ".hh.ru", "path": "/", "name": "_xsrf", "value": "x"},
                    {"domain": ".example.com", "path": "/", "name": "bad", "value": "y"},
                ]
            }
        ),
        encoding="utf-8",
    )
    netscape_path = tmp_path / "cookies.txt"
    netscape_path.write_text(
        "# Netscape HTTP Cookie File\n"
        "#HttpOnly_.hh.ru\tTRUE\t/\tTRUE\t0\t_xsrf\tsecret\n",
        encoding="utf-8",
    )

    assert [cookie["name"] for cookie in load_hh_cookie_file(json_path)] == ["_xsrf"]
    loaded = load_hh_cookie_file(netscape_path)
    assert loaded[0]["domain"] == ".hh.ru"
    assert loaded[0]["httpOnly"] is True


def test_chatik_client_uses_exact_protocol_and_idempotency() -> None:
    http = FakeHTTPSession([{"chats": {}}, {"ok": True}, {"left": True}])
    client = HHChatikClient(
        cookies=[{"domain": ".hh.ru", "path": "/", "name": "hhtoken", "value": "v"}],
        xsrf_token="xsrf",
        user_agent="Work Hunter",
        session=http,
    )
    key = str(uuid.uuid4())

    client.list_chats(page=2)
    client.send_message("101", "Да", idempotency_key=key)
    client.leave_chat(102)

    assert http.calls[0]["url"] == "https://chatik.hh.ru/chatik/api/chats"
    assert http.calls[0]["params"]["page"] == 2
    assert http.calls[0]["headers"]["X-Xsrftoken"] == "xsrf"
    assert http.calls[1]["json"] == {
        "chatId": 101,
        "text": "Да",
        "idempotencyKey": key,
    }
    assert http.calls[2]["url"] == "https://chatik.hh.ru/chatik/api/leave"
    assert http.calls[2]["headers"]["X-hhtmSource"] == "app"


def test_chatik_client_rejects_non_hh_transport() -> None:
    with pytest.raises(ValueError, match="chatik.hh.ru"):
        HHChatikClient(
            cookies=[{"domain": ".hh.ru", "name": "_xsrf", "value": "x"}],
            xsrf_token="x",
            user_agent="",
            base_url="https://evil.example",
        )


def test_applicant_web_load_touch_and_status_protocol() -> None:
    profile = _applicant_profile()
    page = f"<html><script>{json.dumps(profile)}</script></html>"
    http = FakeHTTPSession(
        [
            FakeResponse({}, text=page),
            {"updated": True},
            {"status": "looking_for_offers"},
        ]
    )
    client = HHApplicantWebClient(
        cookies=[{"domain": ".hh.ru", "name": "hhtoken", "value": "v"}],
        xsrf_token="xsrf",
        user_agent="Work Hunter",
        session=http,
    )

    loaded = client.load_profile_data()
    touch = client.touch_resume("hash-1")
    active = client.set_looking_for_offers(loaded)

    assert applicant_profile_summary(loaded)["applicant_id"] == "77"
    assert touch["updated"] is True
    assert http.calls[0]["url"] == "https://hh.ru/applicant/my_resumes"
    assert http.calls[1]["url"] == "https://hh.ru/applicant/resumes/touch"
    assert http.calls[1]["files"]["resume"] == (None, "hash-1")
    assert http.calls[1]["headers"]["Referer"] == "https://hh.ru/applicant/my_resumes"
    assert http.calls[2]["url"].startswith(
        "https://resume-profile.hh.ru/profile/shards/user_statuses/job_search_status"
    )
    assert http.calls[2]["params"] == {"status": "looking_for_offers"}
    assert active["status"] == "looking_for_offers"


def test_applicant_web_decodes_html_entities_and_uses_resume_user_id() -> None:
    profile = _applicant_profile()
    profile["userNotifications"] = []
    profile["applicantResumes"][0]["_attributes"]["user"] = "88"
    encoded = json.dumps(profile).replace('"', "&#34;")
    http = FakeHTTPSession([FakeResponse({}, text=f"<html><script>{encoded}</script></html>")])
    client = HHApplicantWebClient(
        cookies=[{"domain": ".hh.ru", "name": "hhtoken", "value": "v"}],
        xsrf_token="xsrf",
        user_agent="Work Hunter",
        session=http,
    )

    loaded = client.load_profile_data()

    assert applicant_profile_summary(loaded)["applicant_id"] == "88"


def test_extract_chatik_candidates_handles_buttons_discard_and_own_message() -> None:
    candidates = extract_chatik_candidates(_chatik_page())

    assert [candidate.chat_id for candidate in candidates] == ["101", "102"]
    assert candidates[0].reply_options == ("Да", "Нет")
    assert candidates[0].company_name == "Example"
    assert candidates[1].discarded is True


def test_chat_detail_helpers() -> None:
    detail = _chat_detail()

    assert chatik_write_allowed(detail) is True
    assert chatik_message_count(detail) == 1
    assert "Анна: Расскажите про Python" in chatik_message_history(detail)


class FakeChatikClient:
    def __init__(self, page: dict[str, Any]) -> None:
        self.page = page
        self.sent: list[tuple[str, str, str]] = []
        self.left: list[str] = []

    def list_chats(self, *, page: int = 0) -> dict[str, Any]:
        return self.page

    def get_chat_data(self, chat_id: str, applicant_id: str) -> dict[str, Any]:
        return _chat_detail()

    def send_message(
        self,
        chat_id: str,
        text: str,
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        self.sent.append((chat_id, text, idempotency_key))
        return {"status": "sent"}

    def leave_chat(self, chat_id: str) -> dict[str, Any]:
        self.left.append(chat_id)
        return {"status": "left"}


class FakeApplicantWebClient:
    def __init__(self) -> None:
        self.touched: list[str] = []
        self.status_updates = 0

    def load_profile_data(self) -> dict[str, Any]:
        return _applicant_profile()

    def touch_resume(self, resume_hash: str) -> dict[str, Any]:
        self.touched.append(resume_hash)
        return {"updated": True}

    def set_looking_for_offers(self, profile_data: dict[str, Any]) -> dict[str, Any]:
        self.status_updates += 1
        return {"updated": True}


def test_service_plans_sends_leaves_and_deduplicates(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = FakeChatikClient(_chatik_page())
    app = WorkHunter(root=tmp_path)
    monkeypatch.setattr(app, "_hh_chatik_client", lambda **kwargs: fake)

    plan = app.reply_hh_chatik(template="{first_option}", dry_run=True)
    first = app.reply_hh_chatik(
        template="{first_option}",
        dry_run=False,
        confirm=True,
        send_delay_min_seconds=0,
        send_delay_max_seconds=0,
    )
    second = app.reply_hh_chatik(
        template="{first_option}",
        dry_run=False,
        confirm=True,
        send_delay_min_seconds=0,
        send_delay_max_seconds=0,
    )

    assert plan["status"] == "planned"
    assert plan["count"] == 1
    assert plan["leave_count"] == 1
    assert first["status"] == "sent"
    assert fake.sent[0][0:2] == ("101", "Да")
    uuid.UUID(fake.sent[0][2])
    assert fake.left == ["102"]
    assert second["count"] == 0
    assert second["leave_count"] == 0


def test_service_ai_button_must_return_exact_option(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = FakeChatikClient(_chatik_page())
    app = WorkHunter(root=tmp_path)
    monkeypatch.setattr(app, "_hh_chatik_client", lambda **kwargs: fake)
    monkeypatch.setattr("work_hunter.services.chat_completion", lambda messages, config: "Наверное, да")

    result = app.reply_hh_chatik(use_ai=True, dry_run=True)

    assert result["status"] == "partial"
    assert result["count"] == 0
    assert "exactly match" in result["errors"][0]["error"]


def test_service_web_resume_touch_and_job_status_are_plan_first(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake = FakeApplicantWebClient()
    app = WorkHunter(root=tmp_path)
    monkeypatch.setattr(app, "_hh_applicant_web_client", lambda **kwargs: fake)

    touch_plan = app.touch_hh_resumes_web()
    status_plan = app.set_hh_job_search_active()
    touch_result = app.touch_hh_resumes_web(dry_run=False, confirm=True)
    status_result = app.set_hh_job_search_active(dry_run=False, confirm=True)

    assert touch_plan == {
        "status": "planned",
        "account": "default",
        "count": 1,
        "resume_hashes": ["hash-1"],
    }
    assert status_plan["status"] == "planned"
    assert touch_result["transport"] == "hh_web"
    assert fake.touched == ["hash-1"]
    assert status_result["job_search_status"] == "looking_for_offers"
    assert fake.status_updates == 1


def test_chatik_cli_is_dry_run_without_confirm(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured: dict[str, Any] = {}

    def fake_reply(self: WorkHunter, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"status": "planned", "count": 0}

    monkeypatch.setattr(WorkHunter, "reply_hh_chatik", fake_reply)
    cli_main(["--root", str(tmp_path), "hh-chatik-reply", "--use-ai"])

    assert json.loads(capsys.readouterr().out)["status"] == "planned"
    assert captured["dry_run"] is True
    assert captured["confirm"] is False


def test_chatik_mcp_lists_tools_and_defaults_to_plan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, Any] = {}

    def fake_reply(self: WorkHunter, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"status": "planned", "count": 0}

    monkeypatch.setattr(mcp_server, "_root", tmp_path)
    monkeypatch.setattr(WorkHunter, "reply_hh_chatik", fake_reply)

    names = {tool.name for tool in asyncio.run(mcp_server.list_tools())}
    result = asyncio.run(mcp_server.call_tool("hh_reply_chats", {}))
    payload = json.loads(result[0].text)

    assert {"hh_list_chats", "hh_reply_chats"} <= names
    assert payload["status"] == "planned"
    assert captured["dry_run"] is True
    assert captured["confirm"] is False


def test_runner_supports_chatik_and_job_status_plan_first(tmp_path: Path) -> None:
    class RunnerApp:
        root = tmp_path

        def __init__(self) -> None:
            self.chat_calls: list[dict[str, Any]] = []
            self.status_calls: list[dict[str, Any]] = []

        def reply_hh_chatik(self, **kwargs: Any) -> dict[str, Any]:
            self.chat_calls.append(kwargs)
            return {"status": "planned" if kwargs["dry_run"] else "sent"}

        def set_hh_job_search_active(self, **kwargs: Any) -> dict[str, Any]:
            self.status_calls.append(kwargs)
            return {"status": "planned" if kwargs["dry_run"] else "ok"}

    app = RunnerApp()
    report = SafeTaskRunner(app, root=tmp_path).run(
        [
            {"task": "hh-chatik-reply", "use_ai": True},
            {"task": "hh-job-search-active"},
        ]
    )

    assert report["status"] == "completed"
    assert app.chat_calls[0]["dry_run"] is True
    assert app.chat_calls[0]["confirm"] is False
    assert app.status_calls[0]["dry_run"] is True
    assert app.status_calls[0]["confirm"] is False


def test_chatik_ui_contract_is_present() -> None:
    static_root = Path(__file__).resolve().parents[1] / "work_hunter" / "web" / "static"
    html = (static_root / "index.html").read_text(encoding="utf-8")
    javascript = (static_root / "app.js").read_text(encoding="utf-8")

    assert 'data-agent-panel="chats"' in html
    assert 'id="agent-chats-plan-button"' in html
    assert 'id="agent-chats-send-button"' in html
    assert '>Сообщения HH</button>' in html
    assert "Проверить и отправить" in html
    assert "agent-chats-connection" in html
    assert 'id="agent-chats-login-button"' in html
    assert 'api("/api/hh/chats/reply"' in javascript
    assert 'api("/api/hh/auth/login"' in javascript
    assert "renderAgentChatReplyPlan(result)" in javascript
    assert "openLiveAction(descriptor)" in javascript
