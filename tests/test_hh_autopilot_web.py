from __future__ import annotations

import json
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

import pytest

from work_hunter.services import WorkHunter
from work_hunter.web import server as web_server
from work_hunter.web.server import make_handler


class _WebAutopilotProbe:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def enable_hh_autopilot(self, **kwargs):
        self.calls.append(("enable", kwargs))
        return {"status": "ok"}

    def pause_hh_autopilot(self, **kwargs):
        self.calls.append(("pause", kwargs))
        return {"status": "ok"}

    def canary_hh_autopilot(self, **kwargs):
        self.calls.append(("canary", kwargs))
        return {"status": "ok"}

    def resolve_hh_autopilot_challenge(self, **kwargs):
        self.calls.append(("resolve", kwargs))
        return {"status": "ok"}

    def hh_autopilot_status(self, account=None):
        return {
            "account": account,
            "access_token": "must-not-leak",
            "schedule": {},
            "quota": {},
            "queue": {},
            "grant": None,
            "lease": None,
        }

    def hh_autopilot_config(self, account=None):
        return {"account": account, "config": {"refresh_token": "must-not-leak"}}


@pytest.fixture
def autopilot_web(tmp_path, monkeypatch):
    probe = _WebAutopilotProbe()
    monkeypatch.setattr(web_server, "WorkHunter", lambda _root: probe)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(tmp_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, probe
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _request(server, method: str, path: str, payload=None, headers=None):
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request_headers = dict(headers or {})
    if body is not None:
        request_headers.setdefault("Content-Type", "application/json")
    connection.request(method, path, body=body, headers=request_headers)
    response = connection.getresponse()
    result = response.status, json.loads(response.read().decode("utf-8"))
    connection.close()
    return result


def test_enable_requires_explicit_account_and_literal_json_true(autopilot_web):
    server, probe = autopilot_web
    for payload in ({}, {"account": "default", "confirm": False}, {"account": "default", "confirm": "true"}, {"confirm": True}):
        status, body = _request(server, "POST", "/api/hh/autopilot/enable", payload)
        assert status == 400
        assert body["status"] == "blocked"

    status, body = _request(
        server,
        "POST",
        "/api/hh/autopilot/enable",
        {"account": "default", "confirm": True},
    )
    assert (status, body["status"]) == (200, "ok")
    assert probe.calls == [("enable", {"accounts": ["default"], "confirm": True})]


def test_masked_gets_and_existing_request_guards_cover_autopilot(autopilot_web):
    server, _ = autopilot_web
    for path in ("/api/hh/autopilot/status?account=default", "/api/hh/autopilot/config?account=default"):
        status, body = _request(server, "GET", path)
        assert status == 200
        assert "must-not-leak" not in json.dumps(body)

    assert _request(
        server,
        "POST",
        "/api/hh/autopilot/pause",
        {"account": "default"},
        {"Origin": "https://evil.example"},
    )[0] == 403
    assert _request(
        server,
        "POST",
        "/api/hh/autopilot/pause",
        {"account": "default"},
        {"Content-Type": "text/plain"},
    )[0] == 415


def test_pause_canary_and_challenge_routes_keep_exact_scope(autopilot_web):
    server, probe = autopilot_web
    assert _request(server, "POST", "/api/hh/autopilot/pause", {})[0] == 400
    assert _request(server, "POST", "/api/hh/autopilot/pause", {"account": "default"})[0] == 200
    assert _request(
        server,
        "POST",
        "/api/hh/autopilot/canary",
        {"all": True, "resume_id": "r-1", "vacancy_id": "v-1", "confirm": True},
    )[0] == 400
    assert _request(
        server,
        "POST",
        "/api/hh/autopilot/canary",
        {"account": "default", "resume_id": "r-1", "vacancy_id": "v-1", "confirm": True},
    )[0] == 200
    assert _request(
        server,
        "POST",
        "/api/hh/autopilot/resolve-challenge?account=other",
        {"account": "default", "challenge_id": 7, "action": "completed"},
    )[0] == 400
    assert _request(
        server,
        "POST",
        "/api/hh/autopilot/resolve-challenge",
        {"account": "default", "challenge_id": 7, "action": "completed"},
    )[0] == 200
    assert [name for name, _ in probe.calls] == ["pause", "canary", "resolve"]


def test_config_facade_validates_and_never_accepts_managed_fields(tmp_path):
    app = WorkHunter(tmp_path)
    initial = app.hh_autopilot_config(account="default")
    account = initial["config"]["accounts"][0]
    assert "enabled" not in account
    assert "authorization_generation" not in account
    assert initial["managed"]["accounts"][0]["enabled"] is False

    updated = app.update_hh_autopilot_config(
        {"limits": {"daily_success": 49}},
        account="default",
    )
    assert updated["config"]["limits"]["daily_success"] == 49
    assert updated["policy_changed"] is True
    assert updated["reauthorization_required"] is True
    with pytest.raises(ValueError, match="service-managed"):
        app.update_hh_autopilot_config(
            {"accounts": [{**account, "enabled": True}]},
            account="default",
        )
