from __future__ import annotations

import json

from work_hunter.cli import main as cli_main
from work_hunter.config import active_hh_config, default_config
from work_hunter.services import WorkHunter


def test_active_hh_config_overlays_account_profile_without_touching_search_profile():
    config = default_config()
    config["profile"] = "default"
    config["sources"]["hh"]["area"] = 113
    config["sources"]["hh"]["access_token"] = "base-token"
    config["hh_account_profile"] = "work"
    config["hh_account_profiles"] = {
        "work": {
            "access_token": "work-token",
            "refresh_token": "work-refresh",
        }
    }

    hh_config = active_hh_config(config)

    assert config["profile"] == "default"
    assert hh_config["area"] == 113
    assert hh_config["access_token"] == "work-token"
    assert hh_config["refresh_token"] == "work-refresh"


def test_hh_account_profile_service_create_use_and_list(tmp_path):
    app = WorkHunter(root=tmp_path)

    app.save_hh_account_profile(
        "personal",
        access_token="token-a",
        refresh_token="refresh-a",
        client_id="client-a",
    )
    result = app.use_hh_account_profile("personal")

    assert result["active"] == "personal"
    assert app.config["profile"] == "default"
    assert app.hh_config()["access_token"] == "token-a"
    accounts = app.list_hh_account_profiles()
    assert accounts["active"] == "personal"
    assert accounts["accounts"][0]["name"] == "personal"
    assert accounts["accounts"][0]["has_access_token"] is True


def test_hh_account_cli_set_use_and_list(tmp_path, capsys):
    cli_main(
        [
            "--root",
            str(tmp_path),
            "hh-account",
            "set-token",
            "work",
            "--access-token",
            "token-work",
            "--refresh-token",
            "refresh-work",
        ]
    )
    set_payload = json.loads(capsys.readouterr().out)
    assert set_payload["name"] == "work"
    assert set_payload["has_access_token"] is True

    cli_main(["--root", str(tmp_path), "hh-account", "use", "work"])
    use_payload = json.loads(capsys.readouterr().out)
    assert use_payload["active"] == "work"

    cli_main(["--root", str(tmp_path), "hh-account", "list"])
    list_payload = json.loads(capsys.readouterr().out)
    assert list_payload["active"] == "work"
    assert list_payload["accounts"][0]["name"] == "work"
