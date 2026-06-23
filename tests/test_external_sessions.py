from __future__ import annotations

import json

from work_hunter.cli import main as cli_main
from work_hunter.external_sessions import (
    ExternalHTTPResponse,
    call_external_session,
    import_external_session_from_har,
    list_external_sessions,
    sessions_file,
    show_external_session,
)


def _har() -> dict:
    return {
        "log": {
            "entries": [
                {
                    "request": {
                        "method": "GET",
                        "url": "https://hirehi.ru/api/search/jobs",
                        "headers": [
                            {"name": "Cookie", "value": "sessionid=very-secret-cookie"},
                            {"name": "Authorization", "value": "Bearer very-secret-token"},
                            {"name": "X-CSRF-Token", "value": "csrf-secret"},
                            {"name": "User-Agent", "value": "Browser UA"},
                            {"name": "Accept", "value": "application/json"},
                        ],
                    },
                    "response": {"status": 200, "content": {"mimeType": "application/json", "text": "{}"}},
                }
            ]
        }
    }


def test_import_external_session_from_har_stores_secrets_but_returns_masked_summary(tmp_path):
    har_path = tmp_path / "session.har"
    har_path.write_text(json.dumps(_har()), encoding="utf-8")

    summary = import_external_session_from_har(tmp_path, "hirehi", har_path, allowed_hosts={"hirehi.ru"})

    assert summary["name"] == "hirehi"
    assert summary["hosts"] == ["hirehi.ru"]
    assert summary["headers"]["hirehi.ru"]["Cookie"] == "***"
    assert summary["headers"]["hirehi.ru"]["Authorization"] == "***"
    assert summary["headers"]["hirehi.ru"]["User-Agent"] == "Browser UA"

    stored = json.loads(sessions_file(tmp_path).read_text(encoding="utf-8"))
    assert stored["sessions"]["hirehi"]["headers_by_host"]["hirehi.ru"]["Cookie"] == "sessionid=very-secret-cookie"

    listed = list_external_sessions(tmp_path)
    assert listed[0]["headers"]["hirehi.ru"]["X-CSRF-Token"] == "***"
    assert show_external_session(tmp_path, "hirehi")["headers"]["hirehi.ru"]["Authorization"] == "***"


def test_import_external_session_accepts_utf8_sig_har(tmp_path):
    har_path = tmp_path / "session-bom.har"
    har_path.write_text(json.dumps(_har()), encoding="utf-8-sig")

    summary = import_external_session_from_har(tmp_path, "hirehi", har_path, allowed_hosts={"hirehi.ru"})

    assert summary["headers"]["hirehi.ru"]["Cookie"] == "***"


def test_call_external_session_dry_run_masks_headers_and_blocks_unlisted_hosts(tmp_path):
    har_path = tmp_path / "session.har"
    har_path.write_text(json.dumps(_har()), encoding="utf-8")
    import_external_session_from_har(tmp_path, "hirehi", har_path, allowed_hosts={"hirehi.ru"})

    blocked = call_external_session(tmp_path, "hirehi", "GET", "https://evil.example/api/jobs")
    assert blocked["status"] == "blocked"
    assert blocked["reason"] == "host_not_in_session"

    planned = call_external_session(tmp_path, "hirehi", "GET", "https://hirehi.ru/api/search/jobs")
    assert planned["status"] == "planned"
    assert planned["request"]["headers"]["Cookie"] == "***"
    assert planned["request"]["headers"]["Authorization"] == "***"


def test_call_external_session_does_not_share_headers_with_uncaptured_subdomains(tmp_path):
    har_path = tmp_path / "session.har"
    har_path.write_text(json.dumps(_har()), encoding="utf-8")
    import_external_session_from_har(tmp_path, "hirehi", har_path, allowed_hosts={"hirehi.ru"})

    blocked = call_external_session(tmp_path, "hirehi", "GET", "https://api.hirehi.ru/api/search/jobs")

    assert blocked["status"] == "blocked"
    assert blocked["reason"] == "host_not_in_session"
    assert blocked["allowed_hosts"] == ["hirehi.ru"]


def test_call_external_session_real_uses_stored_headers_without_printing_them(tmp_path):
    har_path = tmp_path / "session.har"
    har_path.write_text(json.dumps(_har()), encoding="utf-8")
    import_external_session_from_har(tmp_path, "hirehi", har_path, allowed_hosts={"hirehi.ru"})

    seen: dict[str, object] = {}

    def fake_requester(method: str, url: str, *, headers: dict[str, str], data: str | None, timeout: int):
        seen["method"] = method
        seen["headers"] = headers
        return ExternalHTTPResponse(status=200, headers={"content-type": "application/json"}, body='{"ok":true}')

    result = call_external_session(
        tmp_path,
        "hirehi",
        "GET",
        "https://hirehi.ru/api/search/jobs",
        real=True,
        requester=fake_requester,
    )

    assert seen["headers"]["Cookie"] == "sessionid=very-secret-cookie"
    assert result["status"] == "ok"
    assert result["request"]["headers"]["Cookie"] == "***"
    assert result["response"]["json_preview"] == {"ok": True}


def test_call_external_session_blocks_apply_like_mutating_requests_even_with_unsafe_lab(tmp_path):
    har_path = tmp_path / "session.har"
    har_path.write_text(json.dumps(_har()), encoding="utf-8")
    import_external_session_from_har(tmp_path, "hirehi", har_path, allowed_hosts={"hirehi.ru"})
    calls: list[str] = []

    def fake_requester(method: str, url: str, *, headers: dict[str, str], data: str | None, timeout: int):
        calls.append(url)
        return ExternalHTTPResponse(status=200, headers={"content-type": "application/json"}, body='{"ok":true}')

    blocked = call_external_session(
        tmp_path,
        "hirehi",
        "POST",
        "https://hirehi.ru/api/applications",
        data='{"jobId":"1"}',
        real=True,
        requester=fake_requester,
    )
    blocked_unsafe = call_external_session(
        tmp_path,
        "hirehi",
        "POST",
        "https://hirehi.ru/api/applications",
        data='{"jobId":"1"}',
        real=True,
        unsafe_lab=True,
        requester=fake_requester,
    )

    assert blocked["status"] == "blocked"
    assert blocked["reason"] == "mutating_real_call_requires_unsafe_lab"
    assert blocked_unsafe["status"] == "blocked"
    assert blocked_unsafe["reason"] == "external_apply_submit_blocked"
    assert calls == []


def test_call_external_session_allows_apply_like_request_only_when_certified(tmp_path):
    har_path = tmp_path / "session.har"
    har_path.write_text(json.dumps(_har()), encoding="utf-8")
    import_external_session_from_har(tmp_path, "hirehi", har_path, allowed_hosts={"hirehi.ru"})
    calls: list[dict[str, object]] = []

    def fake_requester(method: str, url: str, *, headers: dict[str, str], data: str | None, timeout: int):
        calls.append({"method": method, "url": url, "headers": headers, "data": data})
        return ExternalHTTPResponse(status=201, headers={"content-type": "application/json"}, body='{"id":"app-1"}')

    result = call_external_session(
        tmp_path,
        "hirehi",
        "POST",
        "https://hirehi.ru/api/applications",
        data='{"jobId":"1","cover_letter":"Hi"}',
        real=True,
        unsafe_lab=True,
        certified_apply=True,
        requester=fake_requester,
    )

    assert result["status"] == "ok"
    assert result["response"]["status"] == 201
    assert calls[0]["method"] == "POST"
    assert calls[0]["headers"]["Cookie"] == "sessionid=very-secret-cookie"
    assert result["request"]["headers"]["Cookie"] == "***"


def test_call_external_session_redacts_real_response_and_dry_run_payload(tmp_path):
    har_path = tmp_path / "session.har"
    har_path.write_text(json.dumps(_har()), encoding="utf-8")
    import_external_session_from_har(tmp_path, "hirehi", har_path, allowed_hosts={"hirehi.ru"})

    planned = call_external_session(
        tmp_path,
        "hirehi",
        "POST",
        "https://hirehi.ru/api/search/jobs",
        data='\ufeff{"password":"pw","api_key":"key","query":"python"}',
    )
    assert planned["request"]["data"] == '{"password":"***","api_key":"***","query":"python"}'

    def fake_requester(method: str, url: str, *, headers: dict[str, str], data: str | None, timeout: int):
        return ExternalHTTPResponse(
            status=200,
            headers={"content-type": "application/json"},
            body='{"access_token":"secret","profile":{"email":"me@example.com","name":"Candidate"}}',
        )

    result = call_external_session(
        tmp_path,
        "hirehi",
        "GET",
        "https://hirehi.ru/api/search/jobs",
        real=True,
        requester=fake_requester,
    )

    assert result["response"]["json_preview"] == {
        "access_token": "***",
        "profile": {"email": "***", "name": "Candidate"},
    }
    assert "secret" not in result["response"]["body_preview"]
    assert "me@example.com" not in result["response"]["body_preview"]


def test_external_session_cli_import_list_and_dry_run_call(monkeypatch, tmp_path, capsys):
    har_path = tmp_path / "session.har"
    har_path.write_text(json.dumps(_har()), encoding="utf-8")

    cli_main(
        [
            "--root",
            str(tmp_path),
            "external-session",
            "import-har",
            "hirehi",
            str(har_path),
            "--host",
            "hirehi.ru",
        ]
    )
    imported = json.loads(capsys.readouterr().out)
    assert imported["headers"]["hirehi.ru"]["Cookie"] == "***"

    cli_main(["--root", str(tmp_path), "external-session", "list"])
    listed = json.loads(capsys.readouterr().out)
    assert listed[0]["name"] == "hirehi"

    cli_main(
        [
            "--root",
            str(tmp_path),
            "external-session",
            "call",
            "hirehi",
            "GET",
            "https://hirehi.ru/api/search/jobs",
        ]
    )
    planned = json.loads(capsys.readouterr().out)
    assert planned["status"] == "planned"
    assert planned["request"]["headers"]["Authorization"] == "***"
