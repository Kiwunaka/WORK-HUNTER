from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from work_hunter.services import WorkHunter
from work_hunter.web.server import make_handler


class FakeHHApiLabClient:
    requests: list[dict] = []

    def __init__(self, config, *, backend=None):
        self.config = config
        self.backend = backend

    def has_token(self):
        return True

    def request_json(self, method: str, path: str, data=None, params=None):
        payload = {
            "method": method,
            "path": path,
            "data": data,
            "params": params or {},
            "access_token": "secret-token",
            "nested": {"client_secret": "secret-client"},
        }
        self.requests.append(payload)
        return payload


def _get_json(base: str, path: str):
    with urllib.request.urlopen(f"{base}{path}", timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _post_json(base: str, path: str, payload: dict):
    request = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def test_hh_api_lab_call_validates_masks_and_audits(monkeypatch, tmp_path):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHApiLabClient)
    FakeHHApiLabClient.requests = []
    app = WorkHunter(root=tmp_path)

    result = app.hh_api_lab_call(
        method="GET",
        path="/vacancies?text=python",
        params={"area": 113, "access_token": "should-mask"},
    )

    assert result["status"] == "ok"
    assert result["method"] == "GET"
    assert result["path"] == "/vacancies"
    assert result["params"]["access_token"] == "***"
    assert result["result"]["access_token"] == "***"
    assert result["result"]["nested"]["client_secret"] == "***"
    assert FakeHHApiLabClient.requests[0]["params"] == {
        "text": "python",
        "area": 113,
        "access_token": "should-mask",
    }
    runs = app.storage.list_hh_agent_mcp_runs()
    assert runs[0].tool_name == "hh_api_lab_call"
    assert runs[0].output["result"]["access_token"] == "***"


@pytest.mark.parametrize("value", [None, False, "true", "false", 1, 0, [], {}])
def test_hh_api_lab_mutation_requires_literal_true(monkeypatch, tmp_path, value):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHApiLabClient)
    FakeHHApiLabClient.requests = []
    app = WorkHunter(tmp_path)

    result = app.hh_api_lab_call(
        method="POST",
        path="/negotiations",
        body={"vacancy_id": "vac-1"},
        confirm=value,
    )

    assert result["status"] == "blocked"
    assert result["code"] == "hh_api_lab_mutation_requires_confirmation"
    assert result["requires_confirmation"] is True
    assert "hh_api_mutation" in result["risk_flags"]
    assert FakeHHApiLabClient.requests == []


def test_hh_api_lab_confirmed_mutation_is_masked_and_audited(monkeypatch, tmp_path):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHApiLabClient)
    FakeHHApiLabClient.requests = []
    app = WorkHunter(tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"

    result = app.hh_api_lab_call(
        method="POST",
        path="/negotiations",
        body={"access_token": "secret"},
        confirm=True,
    )

    assert result["status"] == "ok"
    assert result["confirmed_by_user"] is True
    assert result["body"]["access_token"] == "***"
    assert FakeHHApiLabClient.requests[0]["method"] == "POST"
    assert FakeHHApiLabClient.requests[0]["data"] == {"access_token": "secret"}
    log = app.storage.list_hh_operation_logs()[-1]
    assert log.payload["body"]["access_token"] == "***"
    assert log.payload["confirmed_by_user"] is True


def test_hh_api_lab_http_rejects_string_confirmation_without_transport(
    monkeypatch, tmp_path
):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHApiLabClient)
    FakeHHApiLabClient.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(tmp_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = _post_json(
            f"http://127.0.0.1:{server.server_port}",
            "/api/hh/lab/call",
            {"method": "POST", "path": "/negotiations", "confirm": "false"},
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert result["status"] == "blocked"
    assert FakeHHApiLabClient.requests == []


def test_hh_call_api_mutation_requires_literal_true(monkeypatch, tmp_path):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHApiLabClient)
    FakeHHApiLabClient.requests = []
    app = WorkHunter(tmp_path)

    result = app.hh_call_api(
        "POST",
        "/negotiations",
        data={"vacancy_id": "vac-1"},
        confirm="false",
    )

    assert result["status"] == "blocked"
    assert result["confirmed_by_user"] is False
    assert FakeHHApiLabClient.requests == []


def test_hh_api_lab_blocks_unsafe_paths(tmp_path):
    app = WorkHunter(root=tmp_path)

    for path in ("https://api.hh.ru/me", "//evil.test/me", "/oauth/token", "/me#token"):
        try:
            app.hh_api_lab_call(method="GET", path=path)
        except ValueError as exc:
            assert "HH API Lab" in str(exc)
        else:
            raise AssertionError(f"path should be blocked: {path}")


def test_hh_api_lab_snippets_roundtrip(tmp_path):
    app = WorkHunter(root=tmp_path)

    snippet = app.save_hh_api_lab_snippet(
        name="search-python",
        method="GET",
        path="/vacancies",
        params={"text": "python"},
        body={"access_token": "should-mask"},
    )
    snippets = app.list_hh_api_lab_snippets()
    deleted = app.delete_hh_api_lab_snippet("search-python")

    assert snippet["name"] == "search-python"
    assert snippet["params"] == {"text": "python"}
    assert snippet["body"]["access_token"] == "***"
    assert snippets[0]["name"] == "search-python"
    assert deleted == {"status": "ok", "deleted": 1}


def test_hh_api_lab_web_api_exposes_calls_quick_calls_and_snippets(monkeypatch, tmp_path):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHApiLabClient)
    FakeHHApiLabClient.requests = []
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"
    app.save_config(app.config)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(tmp_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        quick_calls = _get_json(base, "/api/hh/lab/quick-calls")
        call = _post_json(base, "/api/hh/lab/call", {"quick": "me"})
        snippet = _post_json(
            base,
            "/api/hh/lab/snippets",
            {
                "name": "me",
                "method": "GET",
                "path": "/me",
                "params": {},
                "body": {},
            },
        )
        snippets = _get_json(base, "/api/hh/lab/snippets")
        deleted = _post_json(base, "/api/hh/lab/snippets/delete", {"name": "me"})
        bad_request = urllib.request.Request(
            f"{base}/api/hh/lab/call",
            data=json.dumps({"method": "GET", "path": "https://api.hh.ru/me"}).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            urllib.request.urlopen(bad_request, timeout=5)
        except urllib.error.HTTPError as exc:
            bad_status = exc.code
        else:
            bad_status = 200
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert {item["id"] for item in quick_calls} >= {"me", "resumes", "negotiations", "vacancies"}
    assert call["status"] == "ok"
    assert call["path"] == "/me"
    assert call["result"]["access_token"] == "***"
    assert snippet["name"] == "me"
    assert snippets[0]["path"] == "/me"
    assert deleted == {"status": "ok", "deleted": 1}
    assert bad_status == 400
