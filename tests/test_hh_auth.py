from __future__ import annotations

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from work_hunter.services import WorkHunter
from work_hunter.web.server import make_handler


class FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.headers: dict[str, str] = {}

    def json(self):
        return self._payload


class FakeRefreshHHClient:
    calls: list[str] = []

    def __init__(self, config, *, backend=None):
        self.config = config
        self.backend = backend

    def refresh_token(self):
        self.calls.append("refresh")
        return {
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "expires_at": "2030-01-01T00:00:00+00:00",
        }


class FakeFailingRefreshHHClient:
    def __init__(self, config, *, backend=None):
        self.config = config
        self.backend = backend

    def refresh_token(self):
        raise RuntimeError("HH API error 400: invalid_grant")


def test_refresh_hh_token_saves_new_token_only_after_success(monkeypatch, tmp_path):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeRefreshHHClient)
    FakeRefreshHHClient.calls = []
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["access_token"] = "old-access"
    app.config["sources"]["hh"]["refresh_token"] = "old-refresh"
    app.save_config(app.config)

    result = app.refresh_hh_token()

    reloaded = WorkHunter(root=tmp_path)
    hh_config = reloaded.config["sources"]["hh"]
    assert result["status"] == "ok"
    assert result["access_token"] == "new-access"
    assert result["refresh_token"] == "new-refresh"
    assert hh_config["access_token"] == "new-access"
    assert hh_config["refresh_token"] == "new-refresh"
    assert FakeRefreshHHClient.calls == ["refresh"]


def test_refresh_hh_token_preserves_existing_token_on_failure(monkeypatch, tmp_path):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeFailingRefreshHHClient)
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["access_token"] = "old-access"
    app.config["sources"]["hh"]["refresh_token"] = "old-refresh"
    app.save_config(app.config)

    with pytest.raises(RuntimeError):
        app.refresh_hh_token()

    reloaded = WorkHunter(root=tmp_path)
    hh_config = reloaded.config["sources"]["hh"]
    assert hh_config["access_token"] == "old-access"
    assert hh_config["refresh_token"] == "old-refresh"


def test_rotated_refresh_token_persists_to_client_app_and_disk(monkeypatch, tmp_path):
    app = WorkHunter(tmp_path)
    app.save_hh_account_profile(
        "personal",
        access_token="expired-access",
        refresh_token="old-refresh",
        access_expires_at="2000-01-01T00:00:00+00:00",
    )
    app.use_hh_account_profile("personal")

    token_response = FakeResponse(
        200,
        {
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "expires_at": "2031-01-01T00:00:00+00:00",
        },
    )
    me_response = FakeResponse(200, {"id": "me-1"})
    monkeypatch.setattr("requests.request", lambda *args, **kwargs: token_response)
    monkeypatch.setattr(
        "requests.sessions.Session.request",
        lambda *args, **kwargs: me_response,
    )

    client = app._hh_client()
    assert client.whoami() == {"id": "me-1"}
    assert client.session.identity.refresh_token == "new-refresh"
    assert app.hh_config()["refresh_token"] == "new-refresh"

    reloaded = WorkHunter(tmp_path)
    assert reloaded.hh_config()["access_token"] == "new-access"
    assert reloaded.hh_config()["refresh_token"] == "new-refresh"
    assert reloaded._hh_client().session.identity.refresh_token == "new-refresh"


def test_rotated_default_refresh_token_updates_account_and_source(monkeypatch, tmp_path):
    app = WorkHunter(tmp_path)
    app.save_hh_account_profile(
        "default",
        access_token="expired-access",
        refresh_token="old-refresh",
        access_expires_at="2000-01-01T00:00:00+00:00",
    )

    token_response = FakeResponse(
        200,
        {
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "expires_at": "2031-01-01T00:00:00+00:00",
        },
    )
    monkeypatch.setattr("requests.request", lambda *args, **kwargs: token_response)
    monkeypatch.setattr(
        "requests.sessions.Session.request",
        lambda *args, **kwargs: FakeResponse(200, {"id": "me-1"}),
    )

    assert app._hh_client().whoami() == {"id": "me-1"}
    assert app.hh_config()["refresh_token"] == "new-refresh"
    assert app.config["hh_account_profiles"]["default"]["refresh_token"] == "new-refresh"
    assert app.config["sources"]["hh"]["refresh_token"] == "new-refresh"

    reloaded = WorkHunter(tmp_path)
    assert reloaded.hh_config()["refresh_token"] == "new-refresh"


def test_hh_refresh_cli_outputs_masked_result(monkeypatch, tmp_path, capsys):
    from work_hunter.cli import main as cli_main

    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeRefreshHHClient)
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["refresh_token"] = "old-refresh"
    app.save_config(app.config)

    cli_main(["--root", str(tmp_path), "hh-refresh-token"])

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"
    assert payload["access_token"] == "***"
    assert payload["refresh_token"] == "***"


def test_hh_refresh_web_api_masks_tokens(monkeypatch, tmp_path):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeRefreshHHClient)
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["refresh_token"] = "old-refresh"
    app.save_config(app.config)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(tmp_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/hh/token/refresh",
            data=b"{}",
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert payload["status"] == "ok"
    assert payload["access_token"] == "***"
    assert payload["refresh_token"] == "***"
