import json

import pytest
import requests

from work_hunter.hh_transport import HHAuthError, HHForbiddenError, HHTransportError
from work_hunter.models import Job
from work_hunter.sources.geekjob import parse_geekjob_html
from work_hunter.sources.habr import parse_habr_rss
from work_hunter.sources.hh import HHApplyClient, HHSource


def test_parse_habr_rss_item():
    xml = """<?xml version="1.0"?><rss><channel><item><title>Python Dev</title><link>https://career.habr.com/vacancies/1</link><description>Acme</description><pubDate>Sun, 26 Apr 2026 10:00:00 +0300</pubDate></item></channel></rss>"""

    jobs = parse_habr_rss(xml)

    assert jobs[0].source == "habr"
    assert jobs[0].title == "Python Dev"
    assert jobs[0].source_id == "1"


def test_parse_geekjob_html_item():
    html = """<li class="collection-item avatar"><a href="/vacancy/abc" class="title">Backend Dev</a><p class="truncate company-name"><a>Acme</a></p><span class="salary">300K ₽</span><span class="remote-label">remote</span></li>"""

    jobs = parse_geekjob_html(html)

    assert jobs[0].source_id == "abc"
    assert jobs[0].title == "Backend Dev"
    assert jobs[0].company == "Acme"
    assert jobs[0].remote is True


def test_hh_apply_client_posts_exact_negotiation_form(monkeypatch):
    calls = []

    class Response:
        status_code = 201
        headers = {"Location": "/negotiations/123"}

        def json(self):
            return {}

    def fake_request(_session, method, url, **kwargs):
        calls.append({"method": method, "url": url, **kwargs})
        return Response()

    monkeypatch.setattr("requests.sessions.Session.request", fake_request)
    client = HHApplyClient({"access_token": "token", "hh_user_agent": "test-agent"})

    result = client.apply("123", "resume-1", "Hi")

    assert result["status"] == "created"
    assert calls[0]["method"] == "POST"
    assert calls[0]["url"] == "https://api.hh.ru/negotiations"
    assert calls[0]["data"] == {
        "resume_id": "resume-1",
        "vacancy_id": "123",
        "message": "Hi",
    }
    assert calls[0]["headers"]["Authorization"] == "Bearer token"
    assert calls[0]["headers"]["HH-User-Agent"] == "test-agent"


def test_hh_apply_client_surfaces_apply_errors(monkeypatch):
    class Response:
        status_code = 400
        headers = {}

        def json(self):
            return {"errors": [{"type": "bad_argument", "value": "already_applied"}]}

    monkeypatch.setattr("requests.sessions.Session.request", lambda *args, **kwargs: Response())
    client = HHApplyClient({"access_token": "token"})

    result = client.apply("123", "resume-1", "Hi")

    assert result["status"] == "error"
    assert result["error"] == "already_applied"


def test_hh_source_uses_web_fallback_for_request_timeout(monkeypatch):
    fallback_job = Job(
        source="hh",
        source_id="web-1",
        url="https://hh.ru/vacancy/web-1",
        title="Fallback",
    )
    source = HHSource({"access_token": "token", "web_fallback": True})

    def fail(*args, **kwargs):
        raise requests.Timeout("offline")

    monkeypatch.setattr("requests.sessions.Session.request", fail)
    monkeypatch.setattr(source, "_collect_web", lambda profile, limit: [fallback_job])

    assert source.collect({"queries": ["python"]}) == [fallback_job]


def test_hh_source_keeps_fallback_for_parse_failure(monkeypatch):
    fallback_job = Job(
        source="hh",
        source_id="web-1",
        url="https://hh.ru/vacancy/web-1",
        title="Fallback",
    )
    source = HHSource({"access_token": "token", "web_fallback": True})
    parse_error = json.JSONDecodeError("invalid HH payload", "{", 1)

    def fail(*args, **kwargs):
        raise parse_error

    monkeypatch.setattr(
        "work_hunter.sources.hh.HHApiSession.search_vacancies",
        fail,
    )
    monkeypatch.setattr(source, "_collect_web", lambda profile, limit: [fallback_job])

    assert source.collect({"queries": ["python"]}) == [fallback_job]


def test_hh_source_does_not_relabel_or_fallback_on_auth_error(monkeypatch):
    source = HHSource({"access_token": "token", "web_fallback": True})
    auth_error = HHAuthError("expired", code="token_expired")

    def fail(*args, **kwargs):
        raise auth_error

    monkeypatch.setattr(
        "work_hunter.sources.hh.HHApiSession.search_vacancies",
        fail,
    )
    monkeypatch.setattr(
        source,
        "_collect_web",
        lambda *args, **kwargs: pytest.fail("auth error must not use web fallback"),
    )

    with pytest.raises(HHAuthError) as error:
        source.collect({"queries": ["python"]})

    assert error.value is auth_error
    assert error.value.code == "token_expired"


def test_hh_source_does_not_fallback_on_challenge_error(monkeypatch):
    source = HHSource({"access_token": "token", "web_fallback": True})
    challenge_error = HHForbiddenError("captcha", code="captcha_required")

    def fail(*args, **kwargs):
        raise challenge_error

    monkeypatch.setattr(
        "work_hunter.sources.hh.HHApiSession.search_vacancies",
        fail,
    )
    monkeypatch.setattr(
        source,
        "_collect_web",
        lambda *args, **kwargs: pytest.fail(
            "challenge error must not use web fallback"
        ),
    )

    with pytest.raises(HHForbiddenError) as error:
        source.collect({"queries": ["python"]})

    assert error.value is challenge_error
    assert error.value.code == "captcha_required"


def test_hh_source_does_not_fallback_on_untyped_runtime_error(monkeypatch):
    source = HHSource({"access_token": "token", "web_fallback": True})
    programmer_error = RuntimeError("programmer bug")

    def fail(*args, **kwargs):
        raise programmer_error

    monkeypatch.setattr(
        "work_hunter.sources.hh.HHApiSession.search_vacancies",
        fail,
    )
    monkeypatch.setattr(
        source,
        "_collect_web",
        lambda *args, **kwargs: pytest.fail(
            "untyped runtime error must not use web fallback"
        ),
    )

    with pytest.raises(RuntimeError) as error:
        source.collect({"queries": ["python"]})

    assert error.value is programmer_error


def test_hh_source_uses_web_fallback_for_nonempty_invalid_api_json(monkeypatch):
    fallback_job = Job(
        source="hh",
        source_id="web-1",
        url="https://hh.ru/vacancy/web-1",
        title="Fallback",
    )

    class Response:
        status_code = 200
        headers: dict[str, str] = {}
        content = b"not-json"

        def json(self):
            raise json.JSONDecodeError("invalid", "not-json", 0)

    source = HHSource({"access_token": "token", "web_fallback": True})
    monkeypatch.setattr(
        "requests.sessions.Session.request",
        lambda *args, **kwargs: Response(),
    )
    monkeypatch.setattr(source, "_collect_web", lambda profile, limit: [fallback_job])

    assert source.collect({"queries": ["python"]}) == [fallback_job]


def test_hh_source_classifies_challenge_before_refresh(monkeypatch):
    class Response:
        status_code = 403
        headers: dict[str, str] = {}
        content = b'{"errors":[{"value":"captcha_required"}]}'

        def json(self):
            return {"errors": [{"type": "forbidden", "value": "captcha_required"}]}

    refresh_calls = 0

    def fail_refresh(*args, **kwargs):
        nonlocal refresh_calls
        refresh_calls += 1
        raise requests.Timeout("offline")

    source = HHSource(
        {
            "access_token": "token",
            "refresh_token": "refresh",
            "client_id": "client-id",
            "client_secret": "client-secret",
            "web_fallback": True,
        }
    )
    monkeypatch.setattr(
        "requests.sessions.Session.request",
        lambda *args, **kwargs: Response(),
    )
    monkeypatch.setattr("requests.request", fail_refresh)
    monkeypatch.setattr(
        source,
        "_collect_web",
        lambda *args, **kwargs: pytest.fail(
            "challenge error must not use web fallback"
        ),
    )

    with pytest.raises(HHForbiddenError) as error:
        source.collect({"queries": ["python"]})

    assert error.value.code == "captcha_required"
    assert refresh_calls == 0


def test_hh_source_does_not_fallback_on_api_redirect(monkeypatch):
    class Response:
        status_code = 302
        headers = {"Location": "https://hh.ru/login?access_token=secret"}
        content = b"<html>captcha secret</html>"

        def json(self):
            raise json.JSONDecodeError("invalid", "<html>captcha secret</html>", 0)

    source = HHSource({"access_token": "token", "web_fallback": True})
    monkeypatch.setattr(
        "requests.sessions.Session.request",
        lambda *args, **kwargs: Response(),
    )
    monkeypatch.setattr(
        source,
        "_collect_web",
        lambda *args, **kwargs: pytest.fail(
            "API redirect must not use web fallback"
        ),
    )

    with pytest.raises(HHTransportError) as error:
        source.collect({"queries": ["python"]})

    assert error.value.status_code == 302
    assert error.value.code == "redirect"
    assert "secret" not in str(error.value)
