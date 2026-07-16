from __future__ import annotations

import json
from datetime import datetime, timezone
from http.cookiejar import Cookie

import pytest
import requests

from work_hunter.hh_transport import (
    ChallengeKind,
    HHAuthError,
    HHApiSession,
    HHBrowserSession,
    HHChallengeHandler,
    HHIdentity,
    HHOnlyCookieJar,
    HHRateLimitError,
    HHTransportError,
    HHWebActions,
    build_android_user_agent,
    extract_xsrf_token,
)
from work_hunter.hh_transport.backends import DictConfigBackend, JsonCookieBackend
from work_hunter.hh_transport.errors import HHParseError
from work_hunter.hh_autopilot.types import SearchPage
from work_hunter.sources.hh import HHApplyClient


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


def test_search_page_preserves_metadata_without_mutating_params(monkeypatch):
    session = HHApiSession({"access_token": "token"})
    params = {"text": "python", "page": 2, "per_page": 50, "area": [1, 2]}
    calls = []

    def fake_request_json(method, path, **kwargs):
        calls.append((method, path, kwargs))
        kwargs["params"]["area"].append(3)
        return {
            "items": [{"id": "1"}],
            "page": 2,
            "pages": 4,
            "per_page": 50,
            "found": 151,
        }

    monkeypatch.setattr(session, "request_json", fake_request_json)

    page = session.search_vacancies_page(params)

    assert page == SearchPage([{"id": "1"}], 2, 4, 50, 151)
    assert params == {"text": "python", "page": 2, "per_page": 50, "area": [1, 2]}
    assert calls[0][0:2] == ("GET", "/vacancies")


def test_resume_recommendation_page_uses_original_private_encoded_endpoint(monkeypatch):
    session = HHApiSession({"access_token": "token"})
    calls = []

    def fake_request_json(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {
            "items": [{"id": "9"}],
            "page": 1,
            "pages": 3,
            "per_page": 20,
            "found": 42,
        }

    monkeypatch.setattr(session, "request_json", fake_request_json)

    page = session.search_recommended_vacancies_page(
        "resume/id +", {"page": 1, "per_page": 20}
    )

    assert page.page == 1
    assert page.total == 42
    assert calls == [
        (
            "GET",
            "/resumes/resume%2Fid%20%2B/similar_vacancies",
            {"params": {"page": 1, "per_page": 20}},
        )
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"items": "bad", "page": 0, "pages": 1, "per_page": 20, "found": 1},
        {"items": [], "page": False, "pages": 1, "per_page": 20, "found": 0},
        {"items": [], "page": 0, "pages": 1.5, "per_page": 20, "found": 0},
        {"items": [], "page": 0, "pages": 1, "per_page": 101, "found": 0},
    ],
)
def test_search_page_rejects_malformed_shape_and_metadata(monkeypatch, payload):
    session = HHApiSession({"access_token": "token"})
    monkeypatch.setattr(session, "request_json", lambda *args, **kwargs: payload)

    with pytest.raises(HHParseError):
        session.search_vacancies_page({"page": 0, "per_page": 20})


def test_list_search_and_vacancy_similar_apis_remain_compatible(monkeypatch):
    session = HHApiSession({"access_token": "token"})
    upstream = {"id": "1", "nested": {"value": "first"}}
    monkeypatch.setattr(
        session,
        "search_vacancies_page",
        lambda params: SearchPage([upstream], 0, 1, 20, 1),
    )

    items = session.search_vacancies({"text": "python"})
    items[0]["nested"]["value"] = "changed"
    assert upstream["nested"]["value"] == "first"

    client = HHApplyClient({"access_token": "token"})
    calls = []
    monkeypatch.setattr(
        client,
        "_request_json",
        lambda method, path, **kwargs: calls.append((method, path, kwargs))
        or {"items": [{"id": "2"}]},
    )
    assert client.get_similar_vacancies("vacancy-1") == [{"id": "2"}]
    assert calls == [("GET", "/vacancies/vacancy-1/similar_vacancies", {})]


def test_android_user_agent_is_hh_android_like():
    user_agent = build_android_user_agent(app_version="1.2.3", android_version="14", model="Pixel Test")

    assert "ru.hh.android/1.2.3" in user_agent
    assert "Android OS: 14" in user_agent
    assert "Device: Pixel Test" in user_agent


def test_hh_cookie_jar_rejects_non_hh_domains():
    jar = HHOnlyCookieJar()

    jar.set_cookie(make_cookie(".hh.ru", "good", "yes"))
    jar.set_cookie(make_cookie(".hh.kz", "kz", "yes"))
    jar.set_cookie(make_cookie("evil.test", "bad", "no"))
    jar.set_cookie(make_cookie("israel.hh.ru", "excluded", "no"))

    cookies = list(jar)
    assert [cookie.name for cookie in cookies] == ["good", "kz"]


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


def test_identity_accepts_expires_in_token_response():
    identity = HHIdentity(refresh_token="refresh")

    identity.update_from_token_response({"access_token": "access", "expires_in": 3600})

    assert identity.access_token == "access"
    assert identity.access_expires_at is not None
    assert identity.is_access_expired(now=datetime.now(timezone.utc)) is False


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


def test_api_session_retries_once_after_unauthorized(monkeypatch):
    class Response:
        def __init__(self, status_code: int, payload: dict):
            self.status_code = status_code
            self._payload = payload
            self.headers: dict[str, str] = {}

        def json(self):
            return self._payload

    calls = []

    def fake_request(method, url, **kwargs):
        calls.append({"method": method, "url": url, **kwargs})
        if method == "POST" and url.endswith("/token"):
            return Response(
                200,
                {
                    "access_token": "new-access",
                    "refresh_token": "new-refresh",
                    "expires_at": "2031-01-01T00:00:00+00:00",
                },
            )
        if len([call for call in calls if call["method"] == "GET"]) == 1:
            return Response(401, {"errors": [{"type": "oauth", "value": "token_expired"}]})
        return Response(200, {"id": "me-1"})

    def fake_session_request(_session, method, url, **kwargs):
        return fake_request(method, url, **kwargs)

    config = {
        "access_token": "old-access",
        "refresh_token": "old-refresh",
        "client_id": "cid",
        "client_secret": "secret",
    }
    monkeypatch.setattr("requests.request", fake_request)
    monkeypatch.setattr("requests.sessions.Session.request", fake_session_request)

    payload = HHApiSession(config, backend=DictConfigBackend(config)).request_json("GET", "/me")

    assert payload == {"id": "me-1"}
    assert config["access_token"] == "new-access"
    get_calls = [call for call in calls if call["method"] == "GET"]
    assert get_calls[0]["headers"]["Authorization"] == "Bearer old-access"
    assert get_calls[1]["headers"]["Authorization"] == "Bearer new-access"


def test_api_session_refreshes_before_request_when_only_refresh_token_exists(monkeypatch):
    class Response:
        def __init__(self, status_code: int, payload: dict):
            self.status_code = status_code
            self._payload = payload
            self.headers: dict[str, str] = {}

        def json(self):
            return self._payload

    calls = []

    def fake_request(method, url, **kwargs):
        calls.append({"method": method, "url": url, **kwargs})
        if method == "POST" and url.endswith("/token"):
            return Response(
                200,
                {
                    "access_token": "new-access",
                    "refresh_token": "new-refresh",
                    "expires_at": "2031-01-01T00:00:00+00:00",
                },
            )
        return Response(200, {"id": "me-1"})

    def fake_session_request(_session, method, url, **kwargs):
        return fake_request(method, url, **kwargs)

    config = {
        "refresh_token": "old-refresh",
        "client_id": "cid",
        "client_secret": "secret",
    }
    monkeypatch.setattr("requests.request", fake_request)
    monkeypatch.setattr("requests.sessions.Session.request", fake_session_request)

    payload = HHApiSession(config, backend=DictConfigBackend(config)).request_json("GET", "/me")

    assert payload == {"id": "me-1"}
    assert config["access_token"] == "new-access"
    get_call = [call for call in calls if call["method"] == "GET"][0]
    assert get_call["headers"]["Authorization"] == "Bearer new-access"


def test_api_session_raises_typed_errors(monkeypatch):
    class Response:
        status_code = 429
        headers = {}

        def json(self):
            return {"errors": [{"type": "too_many_requests"}]}

    def fake_session_request(_session, *args, **kwargs):
        return Response()

    monkeypatch.setattr("requests.sessions.Session.request", fake_session_request)

    with pytest.raises(HHRateLimitError) as exc:
        HHApiSession({"access_token": "token"}).request_json("GET", "/me")

    assert exc.value.status_code == 429
    assert exc.value.code == "too_many_requests"


def test_api_session_requires_access_token():
    with pytest.raises(HHAuthError) as exc:
        HHApiSession({}).request_json("GET", "/me")

    assert exc.value.code == "auth_missing"


def test_api_session_masks_and_wraps_request_exception(monkeypatch):
    original = requests.ConnectionError(
        "https://api.hh.ru/vacancies?access_token=secret"
    )

    def fail(*args, **kwargs):
        raise original

    monkeypatch.setattr("requests.sessions.Session.request", fail)
    session = HHApiSession({"access_token": "token"})

    with pytest.raises(HHTransportError) as error:
        session.request_json("get", "/vacancies?access_token=secret")

    assert error.value.code == "network_error"
    assert error.value.payload == {"method": "GET", "path": "/vacancies"}
    assert str(error.value) == "GET /vacancies failed: ConnectionError"
    assert "secret" not in str(error.value)
    assert error.value.__cause__ is original


def test_api_session_masks_and_wraps_refresh_request_exception(monkeypatch):
    original = requests.Timeout("refresh_token=secret")

    def fail(*args, **kwargs):
        raise original

    monkeypatch.setattr("requests.request", fail)
    session = HHApiSession(
        {
            "refresh_token": "secret",
            "client_id": "client-id",
            "client_secret": "client-secret",
        }
    )

    with pytest.raises(HHTransportError) as error:
        session.refresh_token()

    assert error.value.code == "network_error"
    assert error.value.payload == {"method": "POST", "path": "/token"}
    assert str(error.value) == "POST /token failed: Timeout"
    assert "secret" not in str(error.value)
    assert error.value.__cause__ is original


def test_api_session_wraps_nonempty_invalid_json_as_parse_error(monkeypatch):
    original = json.JSONDecodeError("invalid", "not-json-secret", 0)

    class Response:
        status_code = 200
        headers: dict[str, str] = {}
        content = b"not-json-secret"

        def json(self):
            raise original

    monkeypatch.setattr(
        "requests.sessions.Session.request",
        lambda *args, **kwargs: Response(),
    )

    with pytest.raises(HHTransportError) as error:
        HHApiSession({"access_token": "token"}).request_json("GET", "/vacancies")

    assert error.value.code == "parse_error"
    assert error.value.status_code == 200
    assert str(error.value) == "HH API response contained invalid JSON: JSONDecodeError"
    assert "secret" not in str(error.value)
    assert error.value.__cause__ is original


def test_api_session_accepts_empty_no_content_response(monkeypatch):
    class Response:
        status_code = 204
        headers: dict[str, str] = {}
        content = b""

        def json(self):
            raise json.JSONDecodeError("empty", "", 0)

    monkeypatch.setattr(
        "requests.sessions.Session.request",
        lambda *args, **kwargs: Response(),
    )

    assert HHApiSession({"access_token": "token"}).request_json("GET", "/empty") == {}


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
