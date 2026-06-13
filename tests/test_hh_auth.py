from __future__ import annotations

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from work_hunter.services import WorkHunter
from work_hunter.web.server import make_handler


class FakeRefreshHHClient:
    calls: list[str] = []

    def __init__(self, config):
        self.config = config

    def refresh_token(self):
        self.calls.append("refresh")
        return {
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "expires_at": "2030-01-01T00:00:00+00:00",
        }


class FakeFailingRefreshHHClient:
    def __init__(self, config):
        self.config = config

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
