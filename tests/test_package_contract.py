from __future__ import annotations

import json
from importlib.resources import files

import pytest

from work_hunter.cli import main as cli_main
from work_hunter.config import config_path, default_config, save_config
from work_hunter.services import WorkHunter


def test_package_contains_autopilot_migrations_and_ui() -> None:
    package = files("work_hunter")
    for name in (
        "0002_account_aware_applications.sql",
        "0003_hh_autopilot_runtime.sql",
        "0004_hh_autopilot_search_budget.sql",
        "0005_hh_autopilot_search_mode.sql",
    ):
        resource = package.joinpath("migrations", name)
        assert resource.is_file()
        assert resource.read_bytes()
    for name in ("index.html", "app.css", "app.js"):
        resource = package.joinpath("web", "static", name)
        assert resource.is_file()
        assert resource.read_bytes()


def test_fresh_install_is_disabled_without_grant_or_quota(tmp_path) -> None:
    app = WorkHunter(root=tmp_path)
    try:
        status = app.hh_autopilot_status(account="default")
    finally:
        app.storage.close()

    assert status["controls"]["enabled"] is False
    assert status["grant"] is None
    assert status["quota"]["used"] == 0


def test_cli_help_lists_autopilot_and_config_export_masks_secrets(
    tmp_path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as root_help:
        cli_main(["--help"])
    assert root_help.value.code == 0
    root_help_text = capsys.readouterr().out
    assert "hh" in root_help_text
    assert "runner" in root_help_text

    with pytest.raises(SystemExit) as autopilot_help:
        cli_main(["hh", "autopilot", "--help"])
    assert autopilot_help.value.code == 0
    help_text = capsys.readouterr().out
    for command in (
        "validate",
        "enable",
        "disable",
        "status",
        "shadow",
        "canary",
        "run-now",
        "recover-now",
        "pause",
        "resume",
        "stop",
        "kill-switch",
        "clear-kill-switch",
        "retry",
        "challenges",
        "resolve-challenge",
        "history",
    ):
        assert command in help_text

    config = default_config()
    config["sources"]["hh"]["access_token"] = "token-value-must-not-leak"
    config["package_contract_secrets"] = {
        "cookie_token": "cookie-value-must-not-leak",
        "proxy_password": "proxy-value-must-not-leak",
        "form_secret": "form-value-must-not-leak",
    }
    save_config(config_path(tmp_path), config)

    cli_main(["--root", str(tmp_path), "config", "--json"])
    exported = capsys.readouterr().out
    payload = json.loads(exported)
    assert payload["sources"]["hh"]["access_token"] == "***"
    assert set(payload["package_contract_secrets"].values()) == {"***"}
    for secret in (
        "token-value-must-not-leak",
        "cookie-value-must-not-leak",
        "proxy-value-must-not-leak",
        "form-value-must-not-leak",
    ):
        assert secret not in exported
