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
from work_hunter.hh_transport.errors import HHNetworkError, HHParseError
from work_hunter.hh_autopilot.types import DeliveryCertainty, SearchPage
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

    assert error.value.code == "read_parse_error"
    assert (
        error.value.delivery_certainty
        is DeliveryCertainty.DEFINITELY_NOT_SENT
    )
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


def test_post_connect_timeout_is_proven_not_sent(monkeypatch):
    session = HHApiSession({"access_token": "token"})
    monkeypatch.setattr(
        session.http,
        "request",
        lambda *args, **kwargs: (_ for _ in ()).throw(requests.ConnectTimeout()),
    )

    with pytest.raises(HHNetworkError) as raised:
        session.apply("v-1", "r-1", "")

    assert (
        raised.value.delivery_certainty
        is DeliveryCertainty.DEFINITELY_NOT_SENT
    )


@pytest.mark.parametrize(
    "error",
    [requests.ReadTimeout(), requests.ConnectionError()],
)
def test_post_unknown_or_late_network_failure_is_possibly_sent(
    monkeypatch, error
):
    session = HHApiSession({"access_token": "token"})
    monkeypatch.setattr(
        session.http,
        "request",
        lambda *args, **kwargs: (_ for _ in ()).throw(error),
    )

    with pytest.raises(HHNetworkError) as raised:
        session.apply("v-1", "r-1", "")

    assert raised.value.delivery_certainty is DeliveryCertainty.POSSIBLY_SENT


def test_read_network_failure_is_proven_not_sent(monkeypatch):
    session = HHApiSession({"access_token": "token"})
    monkeypatch.setattr(
        session.http,
        "request",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            requests.ConnectionError()
        ),
    )

    with pytest.raises(HHNetworkError) as raised:
        session.get_vacancy("v-1")

    assert (
        raised.value.delivery_certainty
        is DeliveryCertainty.DEFINITELY_NOT_SENT
    )


def test_application_401_is_not_hidden_by_an_internal_post_replay(monkeypatch):
    class Response:
        status_code = 401
        headers: dict[str, str] = {}
        content = b"{}"

        @staticmethod
        def json():
            return {"error": "invalid_token"}

    session = HHApiSession(
        {
            "access_token": "expired-token",
            "refresh_token": "refresh-token",
            "client_id": "client-id",
            "client_secret": "client-secret",
        }
    )
    post_calls: list[str] = []
    refresh_calls: list[bool] = []

    def request(*args, **kwargs):
        post_calls.append(str(args[0]))
        return Response()

    monkeypatch.setattr(session.http, "request", request)
    monkeypatch.setattr(
        session,
        "refresh_token",
        lambda: refresh_calls.append(True) or {},
    )

    response = session.apply("v-1", "r-1", "")

    assert response.status_code == 401
    assert post_calls == ["POST"]
    assert refresh_calls == []


def test_token_refresh_timeout_is_not_application_delivery_uncertainty(
    monkeypatch,
):
    session = HHApiSession(
        {
            "refresh_token": "refresh-token",
            "client_id": "client-id",
            "client_secret": "client-secret",
        }
    )
    monkeypatch.setattr(
        requests,
        "request",
        lambda *args, **kwargs: (_ for _ in ()).throw(requests.ReadTimeout()),
    )

    with pytest.raises(HHNetworkError) as raised:
        session.refresh_token()

    assert (
        raised.value.delivery_certainty
        is DeliveryCertainty.DEFINITELY_NOT_SENT
    )


class _DispatchResponse:
    def __init__(
        self,
        status_code,
        payload=None,
        *,
        location="",
        retry_after="",
        raw_content=None,
        json_error=None,
    ):
        self.status_code = status_code
        self._payload = payload
        self._json_error = json_error
        self.headers = {}
        if location:
            self.headers["Location"] = location
        if retry_after:
            self.headers["Retry-After"] = retry_after
        if raw_content is None:
            raw_content = b"" if payload is None else b"{}"
        self.content = raw_content
        self.text = raw_content.decode("utf-8", errors="replace")

    def json(self):
        if self._json_error is not None:
            raise self._json_error
        if self._payload is None:
            raise json.JSONDecodeError("empty", "", 0)
        return self._payload


def test_malformed_successful_post_response_is_possibly_sent(monkeypatch):
    client = HHApplyClient({"access_token": "token"})
    response = _DispatchResponse(
        201,
        raw_content=b"not-json",
        json_error=json.JSONDecodeError("invalid", "not-json", 0),
    )
    monkeypatch.setattr(client.session, "apply", lambda *args, **kwargs: response)

    outcome = client.apply_outcome("v-1", "r-1", "")

    assert outcome.code == "post_dispatch_parse_error"
    assert outcome.certainty is DeliveryCertainty.POSSIBLY_SENT
    assert outcome.status_code == 201
    assert outcome.payload == {}


@pytest.mark.parametrize(
    ("response", "code", "certainty", "retry_after"),
    [
        (
            _DispatchResponse(201, {"id": "n-1"}),
            "applied",
            DeliveryCertainty.DEFINITE_RESPONSE,
            None,
        ),
        (
            _DispatchResponse(201, None, raw_content=b""),
            "applied",
            DeliveryCertainty.DEFINITE_RESPONSE,
            None,
        ),
        (
            _DispatchResponse(
                303,
                None,
                location="https://hh.ru/applicant/vacancy_response?vacancyId=1",
            ),
            "form_required",
            DeliveryCertainty.DEFINITE_RESPONSE,
            None,
        ),
        (
            _DispatchResponse(
                303,
                None,
                location="https://hh.ru/account/captcha?token=secret",
            ),
            "manual_captcha",
            DeliveryCertainty.DEFINITE_RESPONSE,
            None,
        ),
        (
            _DispatchResponse(
                303,
                None,
                location="https://hh.ru/applicant/vacancy_response/test/1",
            ),
            "manual_assessment",
            DeliveryCertainty.DEFINITE_RESPONSE,
            None,
        ),
        (
            _DispatchResponse(
                400,
                {"errors": [{"type": "bad_argument", "value": "already_applied"}]},
            ),
            "duplicate",
            DeliveryCertainty.DEFINITE_RESPONSE,
            None,
        ),
        (
            _DispatchResponse(
                400,
                {"errors": [{"type": "not_found", "value": "vacancy_closed"}]},
            ),
            "vacancy_closed",
            DeliveryCertainty.DEFINITE_RESPONSE,
            None,
        ),
        (
            _DispatchResponse(
                400,
                {"errors": [{"type": "bad_argument", "value": "message"}]},
            ),
            "invalid_request",
            DeliveryCertainty.DEFINITE_RESPONSE,
            None,
        ),
        (
            _DispatchResponse(401, {"error": "invalid_token"}),
            "auth_expired",
            DeliveryCertainty.DEFINITE_RESPONSE,
            None,
        ),
        (
            _DispatchResponse(403, {"error": "access_denied"}),
            "forbidden",
            DeliveryCertainty.DEFINITE_RESPONSE,
            None,
        ),
        (
            _DispatchResponse(
                429,
                {"error": "too_many_requests"},
                retry_after="120",
            ),
            "rate_limited",
            DeliveryCertainty.DEFINITE_RESPONSE,
            120,
        ),
        (
            _DispatchResponse(
                403,
                {
                    "errors": [
                        {
                            "type": "limit_exceeded",
                            "value": "negotiations_limit_exceeded",
                        }
                    ]
                },
            ),
            "hh_daily_limit",
            DeliveryCertainty.DEFINITE_RESPONSE,
            None,
        ),
        (
            _DispatchResponse(503, {"error": "unavailable"}),
            "server_error",
            DeliveryCertainty.DEFINITE_RESPONSE,
            None,
        ),
    ],
)
def test_apply_outcome_maps_every_supported_response(
    monkeypatch, response, code, certainty, retry_after
):
    client = HHApplyClient({"access_token": "token"})
    monkeypatch.setattr(
        client.session,
        "apply",
        lambda *args, **kwargs: response,
    )

    outcome = client.apply_outcome("v-1", "r-1", "")

    assert outcome.code == code
    assert outcome.certainty is certainty
    assert outcome.retry_after_seconds == retry_after
    assert "secret" not in outcome.location
    assert "secret" not in json.dumps(outcome.payload)


def test_apply_outcome_maps_typed_transport_errors_without_string_matching(
    monkeypatch,
):
    client = HHApplyClient({"access_token": "token"})
    error = HHNetworkError(
        "late failure",
        code="network_error",
        delivery_certainty=DeliveryCertainty.POSSIBLY_SENT,
    )
    monkeypatch.setattr(
        client.session,
        "apply",
        lambda *args, **kwargs: (_ for _ in ()).throw(error),
    )

    outcome = client.apply_outcome("v-1", "r-1", "")

    assert outcome.code == "post_dispatch_network_error"
    assert outcome.certainty is DeliveryCertainty.POSSIBLY_SENT


@pytest.mark.parametrize("status_code", [200, 202, 204])
def test_unknown_noncontract_success_status_fails_closed(
    monkeypatch, status_code
):
    client = HHApplyClient({"access_token": "token"})
    monkeypatch.setattr(
        client.session,
        "apply",
        lambda *args, **kwargs: _DispatchResponse(
            status_code,
            None,
            raw_content=b"",
        ),
    )

    outcome = client.apply_outcome("v-1", "r-1", "")

    assert outcome.code == "post_dispatch_parse_error"
    assert outcome.certainty is DeliveryCertainty.POSSIBLY_SENT


def test_error_classification_uses_exact_typed_fields_not_substrings(
    monkeypatch,
):
    client = HHApplyClient({"access_token": "token"})
    monkeypatch.setattr(
        client.session,
        "apply",
        lambda *args, **kwargs: _DispatchResponse(
            400,
            {
                "errors": [
                    {
                        "type": "bad_argument",
                        "value": "not_already_applied",
                        "description": "captcha is explained in documentation",
                    }
                ]
            },
        ),
    )

    outcome = client.apply_outcome("v-1", "r-1", "")

    assert outcome.code == "invalid_request"
    assert outcome.certainty is DeliveryCertainty.DEFINITE_RESPONSE


def test_legacy_apply_shape_is_preserved_by_typed_adapter(monkeypatch):
    client = HHApplyClient({"access_token": "token"})
    monkeypatch.setattr(
        client.session,
        "apply",
        lambda *args, **kwargs: _DispatchResponse(201, {"id": "n-1"}),
    )

    result = client.apply("v-1", "r-1", "")

    assert result == {
        "status": "created",
        "status_code": 201,
        "location": "",
        "raw_result": {"id": "n-1"},
    }


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
    hide_request = actions.hide_negotiation_chat_request("neg-1")

    assert request.method == "POST"
    assert request.url.endswith("/applicant/vacancy_response/popup")
    assert request.headers["X-Xsrftoken"] == "xsrf"
    assert request.headers["User-Agent"] == "ua"
    assert request.data["vacancy_id"] == "vac-1"
    assert hide_request.url.endswith("/applicant/negotiations/trash")
    assert hide_request.data == {
        "topic": "neg-1",
        "query": "?hhtmFrom=main&hhtmFromLabel=header",
        "substate": "HIDE",
    }


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
