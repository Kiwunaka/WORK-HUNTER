from __future__ import annotations

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

from work_hunter.cli import main as cli_main
from work_hunter.services import WorkHunter
from work_hunter.web.server import make_handler


class FakeHHAuthStatusClient:
    whoami_calls = 0

    def __init__(self, config):
        self.config = config

    def has_token(self):
        return bool(self.config.get("access_token"))

    def whoami(self):
        type(self).whoami_calls += 1
        if self.config.get("access_token") == "bad":
            raise RuntimeError("HH API error 403: forbidden")
        return {"id": "me-1", "first_name": "Test"}


def test_hh_auth_status_reports_missing_access_token(tmp_path):
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["access_token"] = ""
    app.config["sources"]["hh"]["refresh_token"] = ""

    status = app.hh_auth_status()

    assert status["status"] == "missing_access_token"
    assert status["authorized"] is False
    assert "Set sources.hh.access_token" in status["actions"]


def test_hh_auth_status_checks_whoami_and_refresh_readiness(monkeypatch, tmp_path):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHAuthStatusClient)
    FakeHHAuthStatusClient.whoami_calls = 0
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"
    app.config["sources"]["hh"]["refresh_token"] = "refresh"
    app.config["sources"]["hh"]["client_id"] = "client"
    app.config["sources"]["hh"]["client_secret"] = "secret"

    status = app.hh_auth_status()

    assert status["status"] == "ok"
    assert status["authorized"] is True
    assert status["refresh_ready"] is True
    assert status["me"] == {"id": "me-1", "first_name": "Test"}
    assert "access_token" not in json.dumps(status)
    assert FakeHHAuthStatusClient.whoami_calls == 1


def test_hh_auth_status_cli(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHAuthStatusClient)
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"
    app.save_config(app.config)

    cli_main(["--root", str(tmp_path), "hh-auth-status"])

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"
    assert payload["authorized"] is True


def test_hh_auth_status_web_endpoint(tmp_path):
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["access_token"] = ""
    app.save_config(app.config)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(tmp_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{server.server_port}/api/hh/auth/status",
            timeout=5,
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert payload["status"] == "missing_access_token"
    assert payload["authorized"] is False
