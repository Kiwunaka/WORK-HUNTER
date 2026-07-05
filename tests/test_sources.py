from work_hunter.sources.geekjob import parse_geekjob_html
from work_hunter.sources.habr import parse_habr_rss
from work_hunter.sources.hh import HHApplyClient


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
