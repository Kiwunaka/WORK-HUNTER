from __future__ import annotations

import json
from datetime import datetime, timezone
from http.cookiejar import Cookie

import pytest

from work_hunter.hh_transport import (
    ChallengeKind,
    HHApiSession,
    HHBrowserSession,
    HHChallengeHandler,
    HHIdentity,
    HHOnlyCookieJar,
    HHWebSessionClient,
    HHWebActions,
    build_vacancy_test_response_payload,
    build_android_user_agent,
    extract_vacancy_tests,
    extract_xsrf_token,
)
from work_hunter.hh_transport.backends import DictConfigBackend, JsonCookieBackend


def make_cookie(domain: str, name: str = "sid", value: str = "1") -> Cookie:
    return Cookie(
        version=0,
        name=name,
        value=value,
        port=None,
        port_specified=False,
        domain=domain,
        domain_specified=True,
        domain_initial_dot=domain.startswith("."),
        path="/",
        path_specified=True,
        secure=False,
        expires=None,
        discard=True,
        comment=None,
        comment_url=None,
        rest={},
        rfc2109=False,
    )


def test_android_user_agent_is_hh_android_like():
    user_agent = build_android_user_agent(app_version="1.2.3", android_version="14", model="Pixel Test")

    assert "ru.hh.android/1.2.3" in user_agent
    assert "Android 14" in user_agent
    assert "Pixel Test" in user_agent


def test_hh_cookie_jar_rejects_non_hh_domains():
    jar = HHOnlyCookieJar()

    jar.set_cookie(make_cookie(".hh.ru", "good", "yes"))
    jar.set_cookie(make_cookie("evil.test", "bad", "no"))

    cookies = list(jar)
    assert [cookie.name for cookie in cookies] == ["good"]


def test_identity_refresh_payload_and_expiry():
    identity = HHIdentity.from_config(
        {
            "access_token": "access",
            "refresh_token": "refresh",
            "client_id": "cid",
            "client_secret": "secret",
            "access_expires_at": "2030-01-01T00:00:00+00:00",
        }
    )

    assert identity.authorization_header() == {"Authorization": "Bearer access"}
    assert identity.refresh_payload() == {
        "grant_type": "refresh_token",
        "refresh_token": "refresh",
        "client_id": "cid",
        "client_secret": "secret",
    }
    assert identity.is_access_expired(now=datetime(2029, 1, 1, tzinfo=timezone.utc)) is False


def test_api_session_refresh_updates_backend(monkeypatch):
    class Response:
        status_code = 200

        def json(self):
            return {
                "access_token": "new-access",
                "refresh_token": "new-refresh",
                "expires_at": "2031-01-01T00:00:00+00:00",
            }

    calls = []

    def fake_request(method, url, **kwargs):
        calls.append({"method": method, "url": url, **kwargs})
        return Response()

    config = {"refresh_token": "old-refresh", "client_id": "cid", "client_secret": "secret"}
    backend = DictConfigBackend(config)
    monkeypatch.setattr("requests.request", fake_request)

    payload = HHApiSession(config, backend=backend).refresh_token()

    assert payload["access_token"] == "new-access"
    assert config["access_token"] == "new-access"
    assert config["refresh_token"] == "new-refresh"
    assert calls[0]["data"]["refresh_token"] == "old-refresh"


def test_extract_xsrf_token_prefers_cookie_then_html():
    assert extract_xsrf_token(cookies=[{"name": "_xsrf", "value": "from-cookie"}]) == "from-cookie"
    assert extract_xsrf_token('<input name="_xsrf" value="from-html">') == "from-html"


def test_browser_session_persists_only_hh_cookies(tmp_path):
    backend = JsonCookieBackend(tmp_path / "cookies.json")
    session = HHBrowserSession(cookie_backend=backend)

    session.update_from_playwright_context(
        [
            {"name": "_xsrf", "value": "token", "domain": ".hh.ru", "path": "/"},
            {"name": "x", "value": "bad", "domain": "evil.test", "path": "/"},
        ]
    )

    reloaded = HHBrowserSession(cookie_backend=backend)
    reloaded.load()
    assert reloaded.xsrf_token == "token"
    assert [cookie["name"] for cookie in reloaded.cookies] == ["_xsrf"]


def test_web_actions_build_xsrf_headers():
    actions = HHWebActions(user_agent="ua", xsrf_token="xsrf")

    request = actions.response_popup_request(vacancy_id="vac-1", resume_id="res-1", message="Hi")

    assert request.method == "POST"
    assert request.url.endswith("/applicant/vacancy_response/popup")
    assert request.headers["X-Xsrftoken"] == "xsrf"
    assert request.headers["User-Agent"] == "ua"
    assert request.data["vacancy_id"] == "vac-1"


def test_web_session_client_touches_resume_with_cookie_xsrf(tmp_path):
    backend = JsonCookieBackend(tmp_path / "cookies.json")
    backend.save(
        [
            {"name": "_xsrf", "value": "xsrf-token", "domain": ".hh.ru", "path": "/"},
            {"name": "hhuid", "value": "session", "domain": ".hh.ru", "path": "/"},
            {"name": "evil", "value": "no", "domain": "evil.test", "path": "/"},
        ]
    )
    calls = []

    class Response:
        status_code = 200
        text = '{"success":true}'
        content = b'{"success":true}'

        def json(self):
            return {"success": True}

    def fake_request(method, url, **kwargs):
        calls.append({"method": method, "url": url, **kwargs})
        return Response()

    client = HHWebSessionClient(
        {"web_user_agent": "ua"},
        cookie_backend=backend,
        transport=fake_request,
    )

    result = client.touch_resume("resume-hash")

    assert result["status"] == "updated"
    assert calls[0]["method"] == "POST"
    assert calls[0]["url"] == "https://hh.ru/applicant/resumes/touch"
    assert calls[0]["headers"]["X-Xsrftoken"] == "xsrf-token"
    assert calls[0]["headers"]["User-Agent"] == "ua"
    assert "_xsrf=xsrf-token" in calls[0]["headers"]["Cookie"]
    assert "evil=no" not in calls[0]["headers"]["Cookie"]
    assert calls[0]["data"]["resume"] == "resume-hash"


def test_web_session_client_loads_netscape_cookies_txt(tmp_path):
    cookies_path = tmp_path / "cookies.txt"
    cookies_path.write_text(
        "\n".join(
            [
                "# Netscape HTTP Cookie File",
                ".hh.ru\tTRUE\t/\tFALSE\t0\t_xsrf\txsrf-token",
                "#HttpOnly_.hh.ru\tTRUE\t/\tTRUE\t0\thhid\thttp-session",
                ".hh.ru\tTRUE\t/\tFALSE\t0\thhuid\tsession",
                "evil.test\tFALSE\t/\tFALSE\t0\tevil\tno",
            ]
        ),
        encoding="utf-8",
    )
    calls = []

    class Response:
        status_code = 200
        text = '{"success":true}'
        content = b'{"success":true}'

        def json(self):
            return {"success": True}

    def fake_request(method, url, **kwargs):
        calls.append({"method": method, "url": url, **kwargs})
        return Response()

    client = HHWebSessionClient({"hh_cookie_file": str(cookies_path)}, transport=fake_request)

    assert client.touch_resume("resume-hash")["status"] == "updated"
    assert "_xsrf=xsrf-token" in calls[0]["headers"]["Cookie"]
    assert "hhid=http-session" in calls[0]["headers"]["Cookie"]
    assert "hhuid=session" in calls[0]["headers"]["Cookie"]
    assert "evil=no" not in calls[0]["headers"]["Cookie"]


def test_web_session_client_chatik_read_send_and_leave(tmp_path):
    backend = JsonCookieBackend(tmp_path / "cookies.json")
    backend.save([{"name": "_xsrf", "value": "xsrf-token", "domain": ".hh.ru", "path": "/"}])
    calls = []

    class Response:
        status_code = 200
        text = "{}"
        content = b"{}"

        def __init__(self, payload):
            self.payload = payload
            self.text = json.dumps(payload)
            self.content = self.text.encode("utf-8")

        def json(self):
            return self.payload

    def fake_request(method, url, **kwargs):
        calls.append({"method": method, "url": url, **kwargs})
        if "chatik/api/chats" in url:
            return Response({"chats": {"items": [], "pages": 1}})
        if "chatik/api/send" in url:
            return Response({"status": "sent"})
        if "chatik/api/leave" in url:
            return Response({"status": "left"})
        return Response({})

    client = HHWebSessionClient({}, cookie_backend=backend, transport=fake_request)

    chats = client.get_chats(page=0)
    sent = client.send_chat_message("chat-1", "Да")
    left = client.leave_chat("chat-1")

    assert chats["chats"]["pages"] == 1
    assert sent["status"] == "sent"
    assert left["status"] == "left"
    assert calls[0]["url"].startswith("https://chatik.hh.ru/chatik/api/chats")
    assert calls[1]["url"] == "https://chatik.hh.ru/chatik/api/send"
    assert calls[1]["json"]["chatId"] == "chat-1"
    assert calls[1]["json"]["text"] == "Да"
    assert calls[2]["url"] == "https://chatik.hh.ru/chatik/api/leave"


def test_extract_vacancy_tests_and_build_web_response_payload():
    html = (
        b'<script>window.__data={"x":1},"vacancyTests":{"123":'
        b'{"uidPk":"uid","guid":"guid","startTime":"start","required":"true",'
        b'"tasks":[{"id":7,"description":"Question?","candidateSolutions":[{"id":70,"description":"Yes"}]},'
        b'{"id":8,"description":"Text?","candidateSolutions":[]}]}},"tail":true}</script>'
    )

    tests = extract_vacancy_tests(html)
    payload = build_vacancy_test_response_payload(
        vacancy_id="123",
        resume_hash="resume-hash",
        xsrf_token="xsrf-token",
        letter="Hello",
        test=tests["123"],
        answers={
            7: {"solution_id": 70},
            8: {"text_answer": "Short text answer"},
        },
    )

    assert tests["123"]["tasks"][0]["id"] == 7
    assert payload["_xsrf"] == "xsrf-token"
    assert payload["vacancy_id"] == "123"
    assert payload["resume_hash"] == "resume-hash"
    assert payload["task_7"] == "70"
    assert payload["task_8_text"] == "Short text answer"


@pytest.mark.parametrize(
    ("result", "kind"),
    [
        ({"status": "error", "error": "captcha_required"}, ChallengeKind.CAPTCHA_REQUIRED),
        ({"status": "error", "error": "test_required"}, ChallengeKind.TEST_REQUIRED),
        ({"status": "redirect", "location": "https://hh.ru/applicant/vacancy_response"}, ChallengeKind.MANUAL_FORM_REQUIRED),
        ({"status": "created"}, ChallengeKind.SOLVED),
    ],
)
def test_challenge_handler_classifies_apply_results(result, kind):
    outcome = HHChallengeHandler().classify_apply_result(result)

    assert outcome.kind == kind
    assert outcome.to_dict()["blocked"] is (kind != ChallengeKind.SOLVED)
