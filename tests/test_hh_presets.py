from __future__ import annotations

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

from work_hunter.cli import main as cli_main
from work_hunter.services import WorkHunter
from work_hunter.web.server import make_handler


def test_hh_campaign_preset_roundtrip_masks_secret_like_fields(tmp_path):
    app = WorkHunter(root=tmp_path)

    saved = app.save_hh_campaign_preset(
        "backend-high-score",
        {
            "limit": 50,
            "min_score": 80,
            "skip_tests": True,
            "resume_id": "resume-1",
            "access_token": "secret",
        },
    )

    loaded = app.get_hh_campaign_preset("backend-high-score")
    presets = app.list_hh_campaign_presets()
    assert saved["name"] == "backend-high-score"
    assert loaded["params"] == {
        "limit": 50,
        "min_score": 80,
        "skip_tests": True,
        "resume_id": "resume-1",
    }
    assert presets == [loaded]


def test_hh_campaign_preset_delete(tmp_path):
    app = WorkHunter(root=tmp_path)
    app.save_hh_campaign_preset("temp", {"limit": 1})

    result = app.delete_hh_campaign_preset("temp")

    assert result == {"status": "ok"}
    assert app.list_hh_campaign_presets() == []


def test_hh_campaign_preset_cli_roundtrip(tmp_path, capsys):
    cli_main(
        [
            "--root",
            str(tmp_path),
            "hh-preset",
            "save",
            "backend",
            "--params",
            '{"limit": 10, "min_score": 90, "access_token": "secret"}',
        ]
    )
    saved = json.loads(capsys.readouterr().out)
    cli_main(["--root", str(tmp_path), "hh-preset", "list"])
    listed = json.loads(capsys.readouterr().out)

    assert saved["params"] == {"limit": 10, "min_score": 90}
    assert listed == [saved]


def test_hh_campaign_preset_web_api_roundtrip(tmp_path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(tmp_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        req = urllib.request.Request(
            f"{base}/api/hh/presets",
            data=json.dumps({"name": "backend", "params": {"limit": 10, "token": "secret"}}).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            saved = json.loads(response.read().decode("utf-8"))
        with urllib.request.urlopen(f"{base}/api/hh/presets", timeout=5) as response:
            listed = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert saved == {"name": "backend", "params": {"limit": 10}}
    assert listed == [saved]
