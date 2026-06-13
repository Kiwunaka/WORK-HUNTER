from __future__ import annotations

import json

from work_hunter.api_discovery import discover_api_candidates_from_url
from work_hunter.cli import main as cli_main


def test_discover_api_candidates_from_html_and_scripts():
    calls: list[str] = []

    def fake_fetch(url: str) -> str:
        calls.append(url)
        if url.endswith("/app.js"):
            return """
            const apiBase = "https://api.rvc.global";
            fetch(apiBase + "/candidate/vacancies/browse");
            fetch("/api/offers?limit=50&access_token=secret");
            fetch("/candidate/cvs");
            """
        return """
        <html>
          <script src="/app.js"></script>
          <a href="/vacancies/python">Python</a>
        </html>
        """

    report = discover_api_candidates_from_url(
        "https://app.rvc.global/",
        fetcher=fake_fetch,
        allowed_hosts={"app.rvc.global", "api.rvc.global"},
    )

    urls = {item["url"] for item in report["endpoints"]}
    assert calls == ["https://app.rvc.global/", "https://app.rvc.global/app.js"]
    assert "https://api.rvc.global/candidate/vacancies/browse" in urls
    assert "https://app.rvc.global/api/offers?limit=50&access_token=%2A%2A%2A" in urls
    assert "https://app.rvc.global/candidate/cvs" in urls
    assert report["by_tag"]["jobs"] >= 2
    assert report["by_tag"]["profile"] >= 1


def test_discover_api_candidates_pairs_public_api_origins_with_relative_routes():
    def fake_fetch(url: str) -> str:
        if url.endswith("/chunk.js"):
            return """
            window.__config = { baseURL: "https://api.rvc.global" };
            const routes = ["/candidate/vacancies/browse", "/candidate/cvs"];
            """
        return '<script src="/chunk.js"></script>'

    report = discover_api_candidates_from_url(
        "https://app.rvc.global/",
        fetcher=fake_fetch,
        allowed_hosts={"app.rvc.global", "api.rvc.global"},
    )

    urls = {item["url"] for item in report["endpoints"]}
    assert "https://api.rvc.global/candidate/vacancies/browse" in urls
    assert "https://api.rvc.global/candidate/cvs" in urls


def test_discover_api_candidates_filters_to_allowed_hosts():
    def fake_fetch(url: str) -> str:
        if url.endswith("/bundle.js"):
            return """
            fetch("https://evil.example/api/token");
            fetch("https://getmatch.ru/api/offers");
            """
        return '<script src="/bundle.js"></script>'

    report = discover_api_candidates_from_url(
        "https://getmatch.ru/vacancies",
        fetcher=fake_fetch,
        allowed_hosts={"getmatch.ru"},
    )

    assert [item["url"] for item in report["endpoints"]] == ["https://getmatch.ru/api/offers"]


def test_discover_api_candidates_limits_scripts():
    def fake_fetch(url: str) -> str:
        if url.endswith("/one.js"):
            return 'fetch("/api/first")'
        if url.endswith("/two.js"):
            return 'fetch("/api/second")'
        return '<script src="/one.js"></script><script src="/two.js"></script>'

    report = discover_api_candidates_from_url(
        "https://example.com",
        fetcher=fake_fetch,
        max_scripts=1,
    )

    assert report["scripts_scanned"] == 1
    assert [item["url"] for item in report["endpoints"]] == ["https://example.com/api/first"]


def test_api_discover_url_cli_outputs_json(monkeypatch, tmp_path, capsys):
    def fake_discover(url: str, **kwargs):
        return {
            "url": url,
            "scripts_found": 0,
            "scripts_scanned": 0,
            "total_endpoints": 1,
            "by_tag": {"jobs": 1},
            "endpoints": [{"url": "https://getmatch.ru/api/offers", "host": "getmatch.ru", "tags": ["jobs", "api"]}],
        }

    monkeypatch.setattr("work_hunter.cli.discover_api_candidates_from_url", fake_discover)

    cli_main(["--root", str(tmp_path), "api-discover-url", "https://getmatch.ru/vacancies", "--host", "getmatch.ru"])

    payload = json.loads(capsys.readouterr().out)
    assert payload["total_endpoints"] == 1
    assert payload["endpoints"][0]["url"] == "https://getmatch.ru/api/offers"
