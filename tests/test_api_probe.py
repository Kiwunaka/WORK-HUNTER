from __future__ import annotations

import json
import urllib.parse

from work_hunter.api_probe import ProbeHTTPResponse, probe_api_candidates_from_url, probe_api_endpoints
from work_hunter.cli import main as cli_main


def test_probe_api_endpoints_classifies_public_auth_and_method_statuses():
    def fake_requester(url: str, *, timeout: int) -> ProbeHTTPResponse:
        path = urllib.parse.urlsplit(url).path
        if path.endswith("/public"):
            return ProbeHTTPResponse(
                status=200,
                headers={"content-type": "application/json"},
                body='{"items":[{"title":"Python"}]}',
            )
        if path.endswith("/private"):
            return ProbeHTTPResponse(status=403, headers={}, body="")
        return ProbeHTTPResponse(status=405, headers={}, body="")

    report = probe_api_endpoints(
        [
            "https://hirehi.ru/api/public?access_token=secret",
            "https://api.rvc.global/candidate/private",
            "https://getmatch.ru/api/apply",
        ],
        requester=fake_requester,
    )

    probes = {item["url"]: item for item in report["probes"]}
    public = probes["https://hirehi.ru/api/public?access_token=%2A%2A%2A"]
    assert public["access"] == "public"
    assert public["json_preview"] == {"items": [{"title": "Python"}]}
    assert probes["https://api.rvc.global/candidate/private"]["access"] == "auth_required"
    assert probes["https://getmatch.ru/api/apply"]["access"] == "method_or_payload_required"


def test_probe_api_candidates_from_url_discovers_then_probes():
    def fake_fetch(url: str) -> str:
        if url.endswith("/app.js"):
            return 'fetch("/api/search/jobs"); fetch("/api/auth/me")'
        return '<script src="/app.js"></script>'

    requested: list[str] = []

    def fake_requester(url: str, *, timeout: int) -> ProbeHTTPResponse:
        requested.append(url)
        status = 401 if url.endswith("/api/auth/me") else 200
        return ProbeHTTPResponse(status=status, headers={"content-type": "application/json"}, body="{}")

    report = probe_api_candidates_from_url(
        "https://hirehi.ru/",
        fetcher=fake_fetch,
        requester=fake_requester,
        allowed_hosts={"hirehi.ru"},
        limit=1,
    )

    assert report["discovery"]["total_endpoints"] == 2
    assert report["probe"]["total_probed"] == 1
    assert requested == ["https://hirehi.ru/api/search/jobs"]


def test_probe_api_candidates_prioritizes_api_endpoints_before_html_routes():
    def fake_fetch(url: str) -> str:
        if url.endswith("/app.js"):
            return 'const routes = ["/vacancies/python", "/api/search/jobs"];'
        return '<script src="/app.js"></script>'

    requested: list[str] = []

    def fake_requester(url: str, *, timeout: int) -> ProbeHTTPResponse:
        requested.append(url)
        return ProbeHTTPResponse(status=200, headers={}, body="")

    probe_api_candidates_from_url(
        "https://hirehi.ru/",
        fetcher=fake_fetch,
        requester=fake_requester,
        allowed_hosts={"hirehi.ru"},
        limit=1,
    )

    assert requested == ["https://hirehi.ru/api/search/jobs"]


def test_api_probe_url_cli_outputs_json(monkeypatch, tmp_path, capsys):
    def fake_probe(url: str, **kwargs):
        return {
            "url": url,
            "discovery": {"total_endpoints": 1},
            "probe": {
                "total_probed": 1,
                "by_access": {"public": 1},
                "probes": [{"url": "https://hirehi.ru/api/search/jobs", "status": 200, "access": "public"}],
            },
        }

    monkeypatch.setattr("work_hunter.cli.probe_api_candidates_from_url", fake_probe)

    cli_main(["--root", str(tmp_path), "api-probe-url", "https://hirehi.ru/", "--host", "hirehi.ru", "--limit", "1"])

    payload = json.loads(capsys.readouterr().out)
    assert payload["probe"]["by_access"] == {"public": 1}
    assert payload["probe"]["probes"][0]["status"] == 200
