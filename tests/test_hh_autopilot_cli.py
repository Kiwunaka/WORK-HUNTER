from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from work_hunter.cli import main


class FakeApp:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __getattr__(self, name: str):
        def call(**kwargs: Any) -> dict[str, Any]:
            self.calls.append((name, kwargs))
            return {"status": "ok", "method": name}

        return call


@pytest.fixture
def fake_app(monkeypatch: pytest.MonkeyPatch) -> FakeApp:
    app = FakeApp()
    monkeypatch.setattr("work_hunter.cli.WorkHunter", lambda _root: app)
    return app


@pytest.mark.parametrize(
    "argv",
    [
        ["hh", "autopilot", "enable", "--confirm"],
        ["hh", "autopilot", "disable", "--confirm"],
        ["hh", "autopilot", "run-now"],
        ["hh", "autopilot", "pause"],
        ["hh", "autopilot", "resume"],
        ["hh", "autopilot", "kill-switch", "--confirm"],
        ["hh", "autopilot", "enable", "--account", "work", "--all", "--confirm"],
        ["hh", "autopilot", "kill-switch", "--account", "work", "--global", "--confirm"],
    ],
)
def test_mutations_require_one_unambiguous_scope(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        main(argv)
    assert raised.value.code == 2


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["validate"], ("validate_hh_autopilot", {"account": None})),
        (["enable", "--account", "work", "--confirm"], ("enable_hh_autopilot", {"accounts": ["work"], "confirm": True})),
        (["disable", "--all", "--confirm"], ("disable_hh_autopilot", {"accounts": None, "confirm": True})),
        (["status"], ("hh_autopilot_status", {"account": None})),
        (["shadow", "--account", "work", "--resume", "r-1", "--preset", "backend"], ("shadow_hh_autopilot", {"account": "work", "resume_id": "r-1", "preset": "backend"})),
        (["canary", "--account", "work", "--resume", "r-1", "--vacancy", "42", "--confirm"], ("canary_hh_autopilot", {"account": "work", "resume_id": "r-1", "vacancy_id": "42", "confirm": True})),
        (["run-now", "--all"], ("run_hh_autopilot", {"accounts": None})),
        (["recover-now"], ("recover_hh_autopilot", {"account": None})),
        (["pause", "--account", "work"], ("pause_hh_autopilot", {"accounts": ["work"]})),
        (["resume", "--all"], ("resume_hh_autopilot", {"accounts": None})),
        (["stop", "--account", "work", "--run-id", "7"], ("stop_hh_autopilot", {"account": "work", "run_id": 7})),
        (["kill-switch", "--global", "--confirm"], ("kill_hh_autopilot", {"accounts": None, "global_scope": True, "confirm": True})),
        (["clear-kill-switch", "--account", "work", "--confirm"], ("clear_hh_autopilot_kill_switch", {"accounts": ["work"], "global_scope": False, "confirm": True})),
        (["retry", "--account", "work", "--item-id", "8"], ("retry_hh_autopilot", {"account": "work", "item_id": 8})),
        (["challenges"], ("hh_autopilot_challenges", {"account": None})),
        (["resolve-challenge", "--account", "work", "--challenge-id", "9", "--action", "completed"], ("resolve_hh_autopilot_challenge", {"account": "work", "challenge_id": 9, "action": "completed"})),
        (["history", "--limit", "25"], ("hh_autopilot_history", {"account": None, "limit": 25})),
    ],
)
def test_autopilot_commands_route_once_as_json(
    argv: list[str], expected: tuple[str, dict[str, Any]], fake_app: FakeApp, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["hh", "autopilot", *argv])
    assert fake_app.calls == [expected]
    assert json.loads(capsys.readouterr().out)["method"] == expected[0]


@pytest.mark.parametrize(
    "argv",
    [
        ["stop", "--account", "work", "--run-id", "0"],
        ["retry", "--account", "work", "--item-id", "-1"],
        ["canary", "--account", "work", "--resume", "r", "--vacancy", "0", "--confirm"],
        ["resolve-challenge", "--account", "work", "--challenge-id", "0", "--action", "completed"],
        ["resolve-challenge", "--account", "work", "--challenge-id", "1", "--action", "invented"],
        ["history", "--limit", "0"],
    ],
)
def test_autopilot_rejects_invalid_ids_limits_and_actions(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        main(["hh", "autopilot", *argv])
    assert raised.value.code == 2


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["login", "--account", "work"], ("login_hh_account", {"account": "work"})),
        (["import-cookies", "--account", "work", "--file", "cookies.json"], ("import_hh_account_cookies", {"account": "work", "path": Path("cookies.json")})),
        (["logout", "--account", "work"], ("logout_hh_account", {"account": "work", "confirm": False})),
        (["refresh", "--account", "work"], ("refresh_hh_account", {"account": "work"})),
        (["status", "--account", "work"], ("hh_auth_status", {"account": "work"})),
        (["select-profile", "--account", "work", "--confirm"], ("select_hh_account_profile", {"account": "work", "confirm": True})),
    ],
)
def test_auth_commands_route_without_browser_in_cli_tests(
    argv: list[str], expected: tuple[str, dict[str, Any]], fake_app: FakeApp, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["hh", "auth", *argv])
    assert fake_app.calls == [expected]
    assert json.loads(capsys.readouterr().out)["status"] == "ok"


@pytest.mark.parametrize("command", ["oauth-start", "oauth-callback"])
def test_oauth_commands_require_user_client_configuration(command: str, fake_app: FakeApp, capsys: pytest.CaptureFixture[str]) -> None:
    main(["hh", "auth", command])
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "blocked"
    assert {"client_id", "client_secret", "redirect_uri"} <= set(payload["required_configuration"])
    assert fake_app.calls == []
