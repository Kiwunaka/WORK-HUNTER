from __future__ import annotations

import copy
import json
import sqlite3
from pathlib import Path

import pytest

from work_hunter.config import default_config
from work_hunter.hh_autopilot.authorization import (
    AuthorizationDenied,
    HHAutopilotAuthorizer,
)
from work_hunter.hh_autopilot.config import PolicyMaterial
from work_hunter.hh_autopilot.repository import AutopilotRepository
from work_hunter.hh_autopilot.types import LiveAuthorization
from work_hunter.storage import Storage


def _canonical(value: str) -> str:
    return value.strip().casefold()


def _material(config: dict[str, object], account_id: str) -> PolicyMaterial:
    account_key = _canonical(account_id)
    accounts = config["sources"]["hh"]["autopilot"]["accounts"]  # type: ignore[index]
    account = next(
        item
        for item in accounts
        if _canonical(item["profile_id"]) == account_key
    )
    candidate_id = _canonical(account["candidate_profile_id"])
    profiles = config.get("profiles", {})
    candidate = copy.deepcopy(profiles.get(candidate_id, {"name": candidate_id}))  # type: ignore[union-attr]
    return PolicyMaterial(
        effective_auth_profile_id=account_key,
        candidate_profile=candidate,
        candidate_profile_version=f"{candidate_id}-v1",
        resumes=[{"id": f"{account_key}-resume", "content_hash": "resume-v1"}],
        presets={},
        model_id="model-v1",
        prompt_versions={"rank": "rank-v1"},
        transport_identity={"kind": "hh-api", "profile_id": account_key},
        secret_versions={"access_token": 1},
    )


@pytest.fixture
def valid_config() -> dict[str, object]:
    return default_config()


@pytest.fixture
def repository(tmp_path: Path) -> AutopilotRepository:
    storage = Storage(tmp_path / "state.sqlite3")
    try:
        yield AutopilotRepository(storage)
    finally:
        storage.conn.close()


@pytest.fixture
def config_file(tmp_path: Path, valid_config: dict[str, object]) -> Path:
    path = tmp_path / "work_hunter_config.json"
    path.write_text(
        json.dumps(valid_config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def authorizer(
    repository: AutopilotRepository,
    config_file: Path,
) -> HHAutopilotAuthorizer:
    return HHAutopilotAuthorizer(
        repository,
        config_path=config_file,
        policy_material_resolver=_material,
    )


@pytest.fixture
def enabled_result(authorizer, valid_config):
    return authorizer.enable(
        ["default"],
        valid_config,
        confirm=True,
        actor="cli",
        source="test",
    )


def _running_authorized_run(authorizer, enabled_result, *, account_id="default"):
    grant = authorizer.repository.active_grant(account_id, "applications")
    assert grant is not None
    return authorizer.repository.create_run(
        account_id,
        trigger="manual",
        policy_hash=enabled_result.policy_hashes[_canonical(account_id)],
        grant_id=grant.id,
        fencing_token=11,
    )


def test_direct_config_edit_cannot_create_a_grant(
    authorizer, valid_config
) -> None:
    edited = copy.deepcopy(valid_config)
    account = edited["sources"]["hh"]["autopilot"]["accounts"][0]
    account["enabled"] = True
    account["authorization_generation"] = 999

    with pytest.raises(AuthorizationDenied, match="authorization_state_mismatch"):
        authorizer.issue_live_authorization(
            "default", edited, run_id=1, fencing_token=7
        )

    assert authorizer.repository.active_grant("default", "applications") is None


def test_enable_requires_literal_true(authorizer, valid_config) -> None:
    for confirmation in (False, 1, "true", {"confirm": True}):
        with pytest.raises(
            AuthorizationDenied, match="literal_confirmation_required"
        ):
            authorizer.enable(
                ["default"],
                valid_config,
                confirm=confirmation,
                actor="cli",
                source="test",
            )
    assert authorizer.repository.active_grant("default", "applications") is None


def test_missing_or_raising_policy_material_resolver_cannot_create_grant(
    repository, config_file, valid_config
) -> None:
    missing = HHAutopilotAuthorizer(repository, config_path=config_file)

    with pytest.raises(AuthorizationDenied, match="policy_material_unavailable"):
        missing.enable(
            ["default"], valid_config, confirm=True, actor="cli", source="test"
        )

    def raising(config, account_id):
        raise RuntimeError("material backend unavailable")

    unavailable = HHAutopilotAuthorizer(
        repository,
        config_path=config_file,
        policy_material_resolver=raising,
    )
    with pytest.raises(AuthorizationDenied, match="policy_material_unavailable"):
        unavailable.enable(
            ["default"], valid_config, confirm=True, actor="cli", source="test"
        )
    assert repository.active_grant("default", "applications") is None


def test_confirmed_enable_creates_policy_bound_generation(
    authorizer, valid_config
) -> None:
    enabled = authorizer.enable(
        [" DEFAULT "],
        valid_config,
        confirm=True,
        actor="cli",
        source="hh autopilot enable",
    )
    run = _running_authorized_run(authorizer, enabled)
    auth = authorizer.issue_live_authorization(
        "Default", enabled.config, run_id=run.id, fencing_token=11
    )

    assert isinstance(auth, LiveAuthorization)
    assert auth.scope == "applications"
    assert auth.account_id == "default"
    assert auth.policy_hash == enabled.policy_hashes["default"]
    assert enabled.generations == {"default": 1}


def test_policy_change_invalidates_existing_generation(
    authorizer, enabled_result
) -> None:
    run = _running_authorized_run(authorizer, enabled_result)
    changed = copy.deepcopy(enabled_result.config)
    changed["sources"]["hh"]["autopilot"]["limits"]["daily_success"] = 51

    with pytest.raises(AuthorizationDenied, match="policy_hash_mismatch"):
        authorizer.issue_live_authorization(
            "default", changed, run_id=run.id, fencing_token=11
        )


def test_generation_change_invalidates_existing_grant(
    authorizer, enabled_result
) -> None:
    edited = copy.deepcopy(enabled_result.config)
    edited["sources"]["hh"]["autopilot"]["accounts"][0][
        "authorization_generation"
    ] = 99

    with pytest.raises(AuthorizationDenied, match="authorization_state_mismatch"):
        authorizer.issue_live_authorization(
            "default", edited, run_id=1, fencing_token=11
        )


def test_disable_revokes_and_requests_running_run_stop(
    authorizer, enabled_result
) -> None:
    run = _running_authorized_run(authorizer, enabled_result)

    disabled = authorizer.disable(
        ["default"], enabled_result.config, confirm=True, actor="cli"
    )

    account = disabled.config["sources"]["hh"]["autopilot"]["accounts"][0]
    assert account["enabled"] is False
    assert account["authorization_generation"] is None
    assert authorizer.repository.active_grant("default", "applications") is None
    assert authorizer.repository.get_run(run.id).status == "stop_requested"


def test_disable_requires_literal_true_and_is_atomic(
    authorizer, enabled_result
) -> None:
    run = _running_authorized_run(authorizer, enabled_result)

    with pytest.raises(AuthorizationDenied, match="literal_confirmation_required"):
        authorizer.disable(
            ["default"], enabled_result.config, confirm=1, actor="cli"
        )

    assert authorizer.repository.active_grant("default", "applications") is not None
    assert authorizer.repository.get_run(run.id).status == "running"


def test_enable_all_prevalidates_every_account_and_changes_nothing(
    authorizer, valid_config, config_file
) -> None:
    invalid = copy.deepcopy(valid_config)
    first = invalid["sources"]["hh"]["autopilot"]["accounts"][0]
    second = copy.deepcopy(first)
    second["profile_id"] = "second"
    second["candidate_profile_id"] = "second"
    second["resume_queries"] = []
    invalid["profiles"]["second"] = {"name": "Second"}
    invalid["sources"]["hh"]["autopilot"]["accounts"].append(second)
    before = config_file.read_bytes()

    with pytest.raises(Exception, match="resume_queries"):
        authorizer.enable(
            ["default", "second"],
            invalid,
            confirm=True,
            actor="cli",
            source="--all",
        )

    assert authorizer.repository.active_grant("default", "applications") is None
    assert authorizer.repository.active_grant("second", "applications") is None
    assert config_file.read_bytes() == before
    assert first["enabled"] is False
    assert first["authorization_generation"] is None


def test_enable_rejects_canonical_duplicate_account_ids_before_writes(
    authorizer, valid_config
) -> None:
    with pytest.raises(ValueError, match="duplicate"):
        authorizer.enable(
            [" default ", "DEFAULT"],
            valid_config,
            confirm=True,
            actor="cli",
            source="test",
        )
    assert authorizer.repository.active_grant("default", "applications") is None


def test_enable_cannot_race_an_account_kill_and_leave_a_dormant_grant(
    authorizer, valid_config, monkeypatch
) -> None:
    real_create = authorizer.repository.create_grants

    def kill_then_create(requests):
        authorizer.repository.set_kill_switch(
            "account", "default", actor="racing-operator"
        )
        return real_create(requests)

    monkeypatch.setattr(authorizer.repository, "create_grants", kill_then_create)

    with pytest.raises(AuthorizationDenied, match="kill_switch_active"):
        authorizer.enable(
            ["default"], valid_config, confirm=True, actor="cli", source="test"
        )

    assert authorizer.repository.active_grant("default", "applications") is None
    authorizer.repository.clear_kill_switch(
        "account", "default", actor="operator"
    )
    assert authorizer.repository.active_grant("default", "applications") is None


def test_enable_revalidates_policy_from_fresh_projection_before_success(
    authorizer, valid_config, monkeypatch
) -> None:
    real_write = authorizer._write_projection

    def return_concurrently_changed_policy(*args, **kwargs):
        projected = real_write(*args, **kwargs)
        projected["sources"]["hh"]["autopilot"]["limits"][
            "daily_success"
        ] = 51
        return projected

    monkeypatch.setattr(
        authorizer, "_write_projection", return_concurrently_changed_policy
    )

    with pytest.raises(AuthorizationDenied, match="policy_hash_mismatch"):
        authorizer.enable(
            ["default"], valid_config, confirm=True, actor="cli", source="test"
        )

    assert authorizer.repository.active_grant("default", "applications") is None


def test_enable_revalidates_exact_active_db_generation_after_projection(
    authorizer, valid_config, monkeypatch
) -> None:
    real_write = authorizer._write_projection

    def create_newer_grant_after_projection(*args, **kwargs):
        projected = real_write(*args, **kwargs)
        authorizer.repository.create_grants(
            [("default", "newer-hash", "scheduler", "concurrent")]
        )
        return projected

    monkeypatch.setattr(
        authorizer, "_write_projection", create_newer_grant_after_projection
    )

    with pytest.raises(AuthorizationDenied, match="authorization_state_mismatch"):
        authorizer.enable(
            ["default"], valid_config, confirm=True, actor="cli", source="test"
        )

    active = authorizer.repository.active_grant("default", "applications")
    assert active is not None
    assert active.generation == 2
    assert active.policy_hash == "newer-hash"


def test_repository_create_grants_validates_all_requests_before_begin(
    repository,
) -> None:
    statements: list[str] = []
    repository.conn.set_trace_callback(statements.append)
    try:
        with pytest.raises(ValueError, match="policy_hash"):
            repository.create_grants(
                [
                    ("default", "hash", "cli", "test"),
                    ("second", "", "cli", "test"),
                ]
            )
    finally:
        repository.conn.set_trace_callback(None)

    assert not any(statement.startswith("BEGIN") for statement in statements)
    assert repository.active_grant("default", "applications") is None


def test_repository_create_grants_rejects_canonical_duplicates_before_begin(
    repository,
) -> None:
    statements: list[str] = []
    repository.conn.set_trace_callback(statements.append)
    try:
        with pytest.raises(ValueError, match="duplicate"):
            repository.create_grants(
                [
                    (" Default ", "hash-a", "cli", "test"),
                    ("DEFAULT", "hash-b", "cli", "test"),
                ]
            )
    finally:
        repository.conn.set_trace_callback(None)

    assert not any(statement.startswith("BEGIN") for statement in statements)


def test_repository_create_grants_rolls_back_every_account_on_insert_failure(
    repository,
) -> None:
    repository.conn.execute(
        """
        CREATE TRIGGER abort_second_grant
        BEFORE INSERT ON hh_autopilot_grants
        WHEN NEW.account_profile_id = 'second'
        BEGIN
            SELECT RAISE(ABORT, 'second grant failure');
        END
        """
    )

    with pytest.raises(sqlite3.IntegrityError, match="second grant failure"):
        repository.create_grants(
            [
                ("default", "hash-a", "cli", "test"),
                ("second", "hash-b", "cli", "test"),
            ]
        )

    assert repository.active_grant("default", "applications") is None
    assert repository.active_grant("second", "applications") is None


def test_repository_grants_are_canonical_and_reads_are_fresh(repository) -> None:
    generations = repository.create_grants(
        [(" Default ", "hash", "cli", "test")]
    )
    first = repository.active_grant("DEFAULT", "applications")
    second = repository.active_grant(" default ", "applications")

    assert generations == {"default": 1}
    assert first is not second
    assert first == second
    assert first.account_id == "default"


def test_repository_exposes_run_stop_request_read(repository) -> None:
    run = repository.create_run(
        "default", trigger="manual", policy_hash="hash"
    )

    assert repository.run_stop_requested(run.id) is False
    repository.request_stop(["default"])
    assert repository.run_stop_requested(run.id) is True


def test_repository_exposes_fresh_canonical_account_state_read(repository) -> None:
    repository.conn.execute(
        """
        INSERT INTO hh_autopilot_account_state (
            account_profile_id, blocked_until, block_reason, hh_reset_json,
            last_scheduled_at, next_scheduled_at, version, updated_at
        ) VALUES (?, '', '', ?, '', '', 0, ?)
        """,
        ("default", '{"remaining": 2}', "2026-07-16T00:00:00+00:00"),
    )
    repository.conn.commit()

    first = repository.get_account_state(" DEFAULT ")
    second = repository.get_account_state("default")

    assert first is not second
    assert first == second
    assert first.account_id == "default"
    assert first.hh_reset == {"remaining": 2}


def test_disable_transaction_rolls_back_grant_when_stop_update_fails(
    repository,
) -> None:
    repository.create_grants([("default", "hash", "cli", "test")])
    run = repository.create_run(
        "default", trigger="manual", policy_hash="hash"
    )
    repository.conn.execute(
        """
        CREATE TRIGGER abort_authorization_stop
        BEFORE UPDATE OF status ON hh_autopilot_runs
        WHEN NEW.status = 'stop_requested'
        BEGIN
            SELECT RAISE(ABORT, 'stop failure');
        END
        """
    )

    with pytest.raises(sqlite3.IntegrityError, match="stop failure"):
        repository.disable_accounts(
            ["default"], actor="cli", reason="disabled"
        )

    assert repository.active_grant("default", "applications") is not None
    assert repository.get_run(run.id).status == "running"


def test_kill_transaction_rolls_back_control_and_grant_on_stop_failure(
    repository,
) -> None:
    repository.create_grants([("default", "hash", "cli", "test")])
    repository.create_run("default", trigger="manual", policy_hash="hash")
    repository.conn.execute(
        """
        CREATE TRIGGER abort_kill_stop
        BEFORE UPDATE OF status ON hh_autopilot_runs
        WHEN NEW.status = 'stop_requested'
        BEGIN
            SELECT RAISE(ABORT, 'kill stop failure');
        END
        """
    )

    with pytest.raises(sqlite3.IntegrityError, match="kill stop failure"):
        repository.set_kill_switch("account", "default", actor="cli")

    assert repository.get_control("account", "default") is None
    assert repository.active_grant("default", "applications") is not None


def test_pause_preserves_grant_but_blocks_live_authorization(
    authorizer, enabled_result
) -> None:
    run = _running_authorized_run(authorizer, enabled_result)
    grant = authorizer.repository.active_grant("default", "applications")

    paused = authorizer.set_pause(
        "account",
        "default",
        enabled_result.config,
        paused=True,
        confirm=True,
        actor="cli",
    )

    assert paused.config["sources"]["hh"]["autopilot"]["accounts"][0]["paused"] is True
    assert authorizer.repository.active_grant("default", "applications") == grant
    with pytest.raises(
        AuthorizationDenied, match="autopilot_disabled_or_paused"
    ):
        authorizer.issue_live_authorization(
            "default", paused.config, run_id=run.id, fencing_token=11
        )
    forged = copy.deepcopy(enabled_result.config)
    forged["sources"]["hh"]["autopilot"]["accounts"][0]["paused"] = False
    with pytest.raises(
        AuthorizationDenied, match="autopilot_disabled_or_paused"
    ):
        authorizer.issue_live_authorization(
            "default", forged, run_id=run.id, fencing_token=11
        )


def test_global_pause_blocks_every_account_without_revoking(
    authorizer, valid_config
) -> None:
    enabled = authorizer.enable(
        ["default"], valid_config, confirm=True, actor="cli", source="test"
    )
    run = _running_authorized_run(authorizer, enabled)

    paused = authorizer.set_pause(
        "global", "global", enabled.config, paused=True, confirm=True, actor="cli"
    )

    assert authorizer.repository.active_grant("default", "applications") is not None
    with pytest.raises(
        AuthorizationDenied, match="autopilot_disabled_or_paused"
    ):
        authorizer.issue_live_authorization(
            "default", paused.config, run_id=run.id, fencing_token=11
        )


def test_account_kill_revokes_grant_and_stops_only_affected_runs(
    authorizer, valid_config, config_file
) -> None:
    config = copy.deepcopy(valid_config)
    second = copy.deepcopy(config["sources"]["hh"]["autopilot"]["accounts"][0])
    second["profile_id"] = "second"
    second["candidate_profile_id"] = "second"
    config["profiles"]["second"] = {"name": "Second"}
    config["sources"]["hh"]["autopilot"]["accounts"].append(second)
    config_file.write_text(json.dumps(config), encoding="utf-8")
    enabled = authorizer.enable(
        ["default", "second"], config, confirm=True, actor="cli", source="test"
    )
    default_run = _running_authorized_run(authorizer, enabled)
    second_run = _running_authorized_run(authorizer, enabled, account_id="second")

    killed = authorizer.set_kill_switch(
        "account", "default", enabled.config, confirm=True, actor="cli"
    )

    assert authorizer.repository.kill_switch_active("default") is True
    assert authorizer.repository.kill_switch_active("second") is False
    assert authorizer.repository.active_grant("default", "applications") is None
    assert authorizer.repository.active_grant("second", "applications") is not None
    assert authorizer.repository.get_run(default_run.id).status == "stop_requested"
    assert authorizer.repository.get_run(second_run.id).status == "running"
    killed_default = killed.config["sources"]["hh"]["autopilot"]["accounts"][0]
    assert killed_default["enabled"] is False


def test_global_kill_revokes_all_grants_and_stops_all_running_runs(
    authorizer, valid_config, config_file
) -> None:
    config = copy.deepcopy(valid_config)
    second = copy.deepcopy(config["sources"]["hh"]["autopilot"]["accounts"][0])
    second["profile_id"] = "second"
    second["candidate_profile_id"] = "second"
    config["profiles"]["second"] = {"name": "Second"}
    config["sources"]["hh"]["autopilot"]["accounts"].append(second)
    config_file.write_text(json.dumps(config), encoding="utf-8")
    enabled = authorizer.enable(
        ["default", "second"], config, confirm=True, actor="cli", source="test"
    )
    runs = [
        _running_authorized_run(authorizer, enabled, account_id=account_id)
        for account_id in ("default", "second")
    ]

    authorizer.set_kill_switch(
        "global", "global", enabled.config, confirm=True, actor="cli"
    )

    for account_id in ("default", "second"):
        assert authorizer.repository.kill_switch_active(account_id) is True
        assert authorizer.repository.active_grant(account_id, "applications") is None
    assert [authorizer.repository.get_run(run.id).status for run in runs] == [
        "stop_requested",
        "stop_requested",
    ]


def test_clearing_kill_switch_never_recreates_grant(
    authorizer, enabled_result
) -> None:
    killed = authorizer.set_kill_switch(
        "account", "default", enabled_result.config, confirm=True, actor="cli"
    )

    cleared = authorizer.clear_kill_switch(
        "account", "default", killed.config, confirm=True, actor="cli"
    )

    assert authorizer.repository.kill_switch_active("default") is False
    assert authorizer.repository.active_grant("default", "applications") is None
    account = cleared.config["sources"]["hh"]["autopilot"]["accounts"][0]
    assert account["enabled"] is False
    assert account["authorization_generation"] is None


def test_projection_write_failure_revokes_exact_created_generation(
    authorizer, valid_config, monkeypatch, config_file
) -> None:
    real_write = authorizer._write_projection

    def write_then_raise(*args, **kwargs):
        real_write(*args, **kwargs)
        raise RuntimeError("after projection replace")

    monkeypatch.setattr(authorizer, "_write_projection", write_then_raise)

    with pytest.raises(RuntimeError, match="after projection"):
        authorizer.enable(
            ["default"], valid_config, confirm=True, actor="cli", source="test"
        )

    projected = json.loads(config_file.read_text(encoding="utf-8"))
    account = projected["sources"]["hh"]["autopilot"]["accounts"][0]
    assert account["enabled"] is True
    assert account["authorization_generation"] == 1
    assert authorizer.repository.active_grant("default", "applications") is None
    with pytest.raises(AuthorizationDenied, match="authorization_state_mismatch"):
        authorizer.issue_live_authorization(
            "default", projected, run_id=1, fencing_token=1
        )


def test_projection_compensation_does_not_revoke_newer_generation(
    authorizer, valid_config, monkeypatch
) -> None:
    real_write = authorizer._write_projection

    def write_create_newer_then_raise(*args, **kwargs):
        real_write(*args, **kwargs)
        authorizer.repository.create_grants(
            [("default", "concurrent-hash", "scheduler", "concurrent")]
        )
        raise RuntimeError("late projection failure")

    monkeypatch.setattr(
        authorizer, "_write_projection", write_create_newer_then_raise
    )

    with pytest.raises(RuntimeError, match="late projection"):
        authorizer.enable(
            ["default"], valid_config, confirm=True, actor="cli", source="test"
        )

    active = authorizer.repository.active_grant("default", "applications")
    assert active is not None
    assert active.generation == 2
    assert active.policy_hash == "concurrent-hash"


def test_reconciliation_never_creates_grant_for_direct_enable_edit(
    authorizer, valid_config
) -> None:
    edited = copy.deepcopy(valid_config)
    edited["sources"]["hh"]["autopilot"]["accounts"][0].update(
        enabled=True,
        authorization_generation=88,
    )

    result = authorizer.reconcile_config_projection(edited, actor="startup")

    assert result.mismatches == {"default": "authorization_state_mismatch"}
    assert authorizer.repository.active_grant("default", "applications") is None


def test_reconciliation_revokes_active_grant_when_projection_is_disabled(
    authorizer, enabled_result
) -> None:
    edited = copy.deepcopy(enabled_result.config)
    edited["sources"]["hh"]["autopilot"]["accounts"][0]["enabled"] = False

    result = authorizer.reconcile_config_projection(edited, actor="startup")

    assert result.revoked_accounts == ("default",)
    assert authorizer.repository.active_grant("default", "applications") is None


def test_reconciliation_revokes_orphan_newer_generation_after_crash(
    authorizer, enabled_result
) -> None:
    assert enabled_result.generations == {"default": 1}
    authorizer.repository.create_grants(
        [("default", "new-policy", "cli", "interrupted-enable")]
    )

    result = authorizer.reconcile_config_projection(
        enabled_result.config, actor="startup"
    )

    assert result.mismatches == {"default": "authorization_state_mismatch"}
    assert result.revoked_accounts == ("default",)
    assert authorizer.repository.active_grant("default", "applications") is None


def test_reconciliation_revokes_grant_for_account_removed_from_config(
    authorizer, enabled_result
) -> None:
    changed = copy.deepcopy(enabled_result.config)
    account = copy.deepcopy(
        changed["sources"]["hh"]["autopilot"]["accounts"][0]
    )
    account.update(
        profile_id="new",
        candidate_profile_id="new",
        enabled=False,
        authorization_generation=None,
    )
    changed["profiles"]["new"] = {"name": "New"}
    changed["sources"]["hh"]["autopilot"]["accounts"] = [account]

    result = authorizer.reconcile_config_projection(changed, actor="startup")

    assert result.revoked_accounts == ("default",)
    assert authorizer.repository.active_grant("default", "applications") is None


@pytest.mark.parametrize("status", ["stop_requested", "completed", "failed"])
def test_stopped_or_terminal_run_cannot_receive_live_authorization(
    authorizer, enabled_result, status
) -> None:
    run = _running_authorized_run(authorizer, enabled_result)
    if status == "stop_requested":
        authorizer.repository.request_stop(["default"])
    else:
        authorizer.repository.finish_run(run.id, status=status, fencing_token=None)

    with pytest.raises(AuthorizationDenied, match="run_not_active"):
        authorizer.issue_live_authorization(
            "default", enabled_result.config, run_id=run.id, fencing_token=11
        )


def test_live_authorization_checks_run_account_policy_and_grant(
    authorizer, enabled_result
) -> None:
    wrong_account = authorizer.repository.create_run(
        "other", trigger="manual", policy_hash=enabled_result.policy_hashes["default"]
    )
    wrong_policy = authorizer.repository.create_run(
        "default", trigger="manual", policy_hash="wrong", fencing_token=11
    )
    wrong_grant = authorizer.repository.create_run(
        "default",
        trigger="manual",
        policy_hash=enabled_result.policy_hashes["default"],
        grant_id=999,
        fencing_token=11,
    )

    for run, code in (
        (wrong_account, "run_account_mismatch"),
        (wrong_policy, "run_policy_mismatch"),
        (wrong_grant, "run_grant_mismatch"),
    ):
        with pytest.raises(AuthorizationDenied, match=code):
            authorizer.issue_live_authorization(
                "default", enabled_result.config, run_id=run.id, fencing_token=11
            )


def test_live_authorization_rejects_missing_run_and_bool_boundaries(
    authorizer, enabled_result
) -> None:
    with pytest.raises(AuthorizationDenied, match="run_not_found"):
        authorizer.issue_live_authorization(
            "default", enabled_result.config, run_id=999, fencing_token=11
        )
    for bad_value in (True, False):
        with pytest.raises(TypeError):
            authorizer.issue_live_authorization(
                "default",
                enabled_result.config,
                run_id=bad_value,
                fencing_token=11,
            )
        with pytest.raises(TypeError):
            authorizer.issue_live_authorization(
                "default",
                enabled_result.config,
                run_id=1,
                fencing_token=bad_value,
            )


def test_live_authorization_requires_exact_run_fencing_token(
    authorizer, enabled_result
) -> None:
    run = _running_authorized_run(authorizer, enabled_result)

    with pytest.raises(AuthorizationDenied, match="run_fencing_token_mismatch"):
        authorizer.issue_live_authorization(
            "default", enabled_result.config, run_id=run.id, fencing_token=12
        )
