from __future__ import annotations

import copy
import json
import sqlite3
import threading
from pathlib import Path

import pytest

from work_hunter.config import (
    config_path,
    database_path,
    default_config,
    save_config,
)
from work_hunter.hh_autopilot.authorization import (
    AuthorizationDenied,
    HHAutopilotAuthorizer,
)
from work_hunter.hh_autopilot.config import PolicyMaterial
from work_hunter.hh_autopilot.repository import (
    AutopilotRepository,
    RepositoryAuthorizationDenied,
)
from work_hunter.hh_autopilot.types import LiveAuthorization
from work_hunter.storage import Storage


def _two_account_config(config: dict[str, object]) -> dict[str, object]:
    expanded = copy.deepcopy(config)
    accounts = expanded["sources"]["hh"]["autopilot"]["accounts"]  # type: ignore[index]
    second = copy.deepcopy(accounts[0])
    second["profile_id"] = "second"
    second["candidate_profile_id"] = "second"
    accounts.append(second)
    expanded["profiles"]["second"] = {"name": "Second"}  # type: ignore[index]
    return expanded


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


def test_persisted_policy_change_rejects_stale_live_authorization_config(
    authorizer, enabled_result, config_file
) -> None:
    run = _running_authorized_run(authorizer, enabled_result)
    persisted = copy.deepcopy(enabled_result.config)
    persisted["sources"]["hh"]["autopilot"]["limits"][
        "daily_success"
    ] = 51
    save_config(config_file, persisted)

    with pytest.raises(AuthorizationDenied, match="policy_hash_mismatch"):
        authorizer.issue_live_authorization(
            "default",
            enabled_result.config,
            run_id=run.id,
            fencing_token=11,
        )


def test_live_authorization_requires_persisted_config_path(
    authorizer, enabled_result
) -> None:
    run = _running_authorized_run(authorizer, enabled_result)
    resolver_calls = 0

    def counted_material(config_snapshot, account_id):
        nonlocal resolver_calls
        resolver_calls += 1
        return _material(config_snapshot, account_id)

    without_path = HHAutopilotAuthorizer(
        authorizer.repository,
        policy_material_resolver=counted_material,
    )

    with pytest.raises(
        AuthorizationDenied, match="config_projection_path_required"
    ):
        without_path.issue_live_authorization(
            "default",
            enabled_result.config,
            run_id=run.id,
            fencing_token=11,
        )
    assert resolver_calls == 0


@pytest.mark.parametrize("scenario", ["missing", "corrupt", "semantic"])
def test_live_authorization_fails_closed_without_valid_current_snapshot(
    authorizer, enabled_result, config_file, scenario
) -> None:
    run = _running_authorized_run(authorizer, enabled_result)
    if scenario == "missing":
        config_file.unlink()
    elif scenario == "corrupt":
        config_file.write_text('{"broken":', encoding="utf-8")
    else:
        malformed = copy.deepcopy(enabled_result.config)
        malformed["sources"]["hh"]["autopilot"]["accounts"] = "broken"
        config_file.write_text(json.dumps(malformed), encoding="utf-8")

    with pytest.raises(
        AuthorizationDenied, match="config_projection_unavailable"
    ):
        authorizer.issue_live_authorization(
            "default",
            enabled_result.config,
            run_id=run.id,
            fencing_token=11,
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
    authorizer, valid_config, config_file, monkeypatch
) -> None:
    real_write = authorizer._write_projection

    def persist_concurrently_changed_policy(*args, **kwargs):
        projected = real_write(*args, **kwargs)
        changed = copy.deepcopy(projected)
        changed["sources"]["hh"]["autopilot"]["limits"][
            "daily_success"
        ] = 51
        save_config(config_file, changed)
        return projected

    monkeypatch.setattr(
        authorizer, "_write_projection", persist_concurrently_changed_policy
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


def test_enable_final_validation_rejects_single_generation_replaced_during_resolver(
    authorizer, valid_config
) -> None:
    resolver_calls = 0
    resolver_blocked = threading.Event()
    replacement_committed = threading.Event()
    worker_errors: list[BaseException] = []
    database = authorizer.repository.storage.path

    def blocking_material(config, account_id):
        nonlocal resolver_calls
        resolver_calls += 1
        if resolver_calls == 2:
            resolver_blocked.set()
            assert replacement_committed.wait(timeout=5)
        return _material(config, account_id)

    def replace_generation() -> None:
        try:
            assert resolver_blocked.wait(timeout=5)
            storage = Storage(database)
            try:
                concurrent = AutopilotRepository(storage)
                current = concurrent.active_grant("default", "applications")
                assert current is not None
                replacement = concurrent.create_grants(
                    [
                        (
                            "default",
                            current.policy_hash,
                            "scheduler",
                            "concurrent",
                        )
                    ]
                )
                assert replacement == {"default": current.generation + 1}
            finally:
                storage.close()
        except BaseException as exc:
            worker_errors.append(exc)
        finally:
            replacement_committed.set()

    authorizer.policy_material_resolver = blocking_material
    worker = threading.Thread(target=replace_generation)
    worker.start()
    try:
        with pytest.raises(
            AuthorizationDenied, match="authorization_state_mismatch"
        ):
            authorizer.enable(
                ["default"],
                valid_config,
                confirm=True,
                actor="cli",
                source="test",
            )
    finally:
        replacement_committed.set()
        worker.join(timeout=5)

    assert worker.is_alive() is False
    assert worker_errors == []
    active = authorizer.repository.active_grant("default", "applications")
    assert active is not None
    assert active.generation == 2


def test_enable_final_validation_rechecks_account_a_after_account_b_resolver(
    repository, tmp_path, valid_config
) -> None:
    config = _two_account_config(valid_config)
    path = tmp_path / "two-account-enable.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    resolver_calls: dict[str, int] = {}
    second_resolver_blocked = threading.Event()
    replacement_committed = threading.Event()
    worker_errors: list[BaseException] = []
    database = repository.storage.path

    def blocking_material(config_snapshot, account_id):
        canonical = _canonical(account_id)
        resolver_calls[canonical] = resolver_calls.get(canonical, 0) + 1
        if canonical == "second" and resolver_calls[canonical] == 2:
            second_resolver_blocked.set()
            assert replacement_committed.wait(timeout=5)
        return _material(config_snapshot, account_id)

    def replace_default_generation() -> None:
        try:
            assert second_resolver_blocked.wait(timeout=5)
            storage = Storage(database)
            try:
                concurrent = AutopilotRepository(storage)
                current = concurrent.active_grant("default", "applications")
                assert current is not None
                replacement = concurrent.create_grants(
                    [
                        (
                            "default",
                            current.policy_hash,
                            "scheduler",
                            "concurrent",
                        )
                    ]
                )
                assert replacement == {"default": current.generation + 1}
            finally:
                storage.close()
        except BaseException as exc:
            worker_errors.append(exc)
        finally:
            replacement_committed.set()

    authorizer = HHAutopilotAuthorizer(
        repository,
        config_path=path,
        policy_material_resolver=blocking_material,
    )
    worker = threading.Thread(target=replace_default_generation)
    worker.start()
    try:
        with pytest.raises(
            AuthorizationDenied, match="authorization_state_mismatch"
        ):
            authorizer.enable(
                ["default", "second"],
                config,
                confirm=True,
                actor="cli",
                source="test",
            )
    finally:
        replacement_committed.set()
        worker.join(timeout=5)

    assert worker.is_alive() is False
    assert worker_errors == []
    active_default = repository.active_grant("default", "applications")
    assert active_default is not None
    assert active_default.generation == 2
    assert repository.active_grant("second", "applications") is None


def test_enable_rejects_policy_saved_after_projection_before_current_snapshot(
    authorizer, valid_config, config_file, monkeypatch
) -> None:
    projection_written = threading.Event()
    writer_finished = threading.Event()
    writer_errors: list[BaseException] = []
    real_write = authorizer._write_projection

    def wait_for_policy_writer(*args, **kwargs):
        projected = real_write(*args, **kwargs)
        projection_written.set()
        assert writer_finished.wait(timeout=5)
        return projected

    def save_changed_policy() -> None:
        try:
            assert projection_written.wait(timeout=5)
            changed = copy.deepcopy(valid_config)
            changed["sources"]["hh"]["autopilot"]["limits"][
                "daily_success"
            ] = 51
            save_config(config_file, changed)
        except BaseException as exc:
            writer_errors.append(exc)
        finally:
            writer_finished.set()

    monkeypatch.setattr(authorizer, "_write_projection", wait_for_policy_writer)
    writer = threading.Thread(target=save_changed_policy)
    writer.start()
    try:
        with pytest.raises(AuthorizationDenied, match="policy_hash_mismatch"):
            authorizer.enable(
                ["default"],
                valid_config,
                confirm=True,
                actor="cli",
                source="test",
            )
    finally:
        writer_finished.set()
        writer.join(timeout=5)

    assert writer.is_alive() is False
    assert writer_errors == []
    assert authorizer.repository.active_grant("default", "applications") is None
    persisted = json.loads(config_file.read_text(encoding="utf-8"))
    assert persisted["sources"]["hh"]["autopilot"]["limits"][
        "daily_success"
    ] == 51


def test_enable_returns_exact_current_snapshot_after_non_policy_save(
    authorizer, valid_config, config_file, monkeypatch
) -> None:
    projection_written = threading.Event()
    writer_finished = threading.Event()
    writer_errors: list[BaseException] = []
    real_write = authorizer._write_projection

    def wait_for_config_writer(*args, **kwargs):
        projected = real_write(*args, **kwargs)
        projection_written.set()
        assert writer_finished.wait(timeout=5)
        return projected

    def save_non_policy_change() -> None:
        try:
            assert projection_written.wait(timeout=5)
            changed = copy.deepcopy(valid_config)
            changed["research"]["max_results"] = 321
            save_config(config_file, changed)
        except BaseException as exc:
            writer_errors.append(exc)
        finally:
            writer_finished.set()

    monkeypatch.setattr(authorizer, "_write_projection", wait_for_config_writer)
    writer = threading.Thread(target=save_non_policy_change)
    writer.start()
    try:
        enabled = authorizer.enable(
            ["default"],
            valid_config,
            confirm=True,
            actor="cli",
            source="test",
        )
    finally:
        writer_finished.set()
        writer.join(timeout=5)

    assert writer.is_alive() is False
    assert writer_errors == []
    persisted = json.loads(config_file.read_text(encoding="utf-8"))
    assert enabled.config == persisted
    assert enabled.config["research"]["max_results"] == 321


def test_policy_writer_blocks_during_enable_snapshot_and_stale_live_auth_denies(
    authorizer, valid_config, config_file
) -> None:
    resolver_calls = 0
    current_resolver_entered = threading.Event()
    writer_finished = threading.Event()
    writer_errors: list[BaseException] = []
    writer_committed_during_resolver: list[bool] = []

    def blocking_material(config_snapshot, account_id):
        nonlocal resolver_calls
        resolver_calls += 1
        if resolver_calls == 2:
            current_resolver_entered.set()
            writer_committed_during_resolver.append(
                writer_finished.wait(timeout=1)
            )
        return _material(config_snapshot, account_id)

    def save_changed_policy() -> None:
        try:
            assert current_resolver_entered.wait(timeout=5)
            changed = copy.deepcopy(valid_config)
            changed["sources"]["hh"]["autopilot"]["limits"][
                "daily_success"
            ] = 51
            save_config(config_file, changed)
        except BaseException as exc:
            writer_errors.append(exc)
        finally:
            writer_finished.set()

    authorizer.policy_material_resolver = blocking_material
    writer = threading.Thread(target=save_changed_policy)
    writer.start()
    try:
        enabled = authorizer.enable(
            ["default"],
            valid_config,
            confirm=True,
            actor="cli",
            source="test",
        )
    finally:
        writer_finished.wait(timeout=5)
        writer.join(timeout=5)

    assert writer.is_alive() is False
    assert writer_errors == []
    assert writer_committed_during_resolver == [False]
    grant = authorizer.repository.active_grant("default", "applications")
    assert grant is not None
    run = authorizer.repository.create_run(
        "default",
        trigger="manual",
        policy_hash=enabled.policy_hashes["default"],
        grant_id=grant.id,
        fencing_token=11,
    )
    with pytest.raises(AuthorizationDenied, match="policy_hash_mismatch"):
        authorizer.issue_live_authorization(
            "default",
            enabled.config,
            run_id=run.id,
            fencing_token=11,
        )


def test_policy_writer_blocks_during_live_authorization_snapshot(
    authorizer, enabled_result, config_file, monkeypatch
) -> None:
    run = _running_authorized_run(authorizer, enabled_result)
    snapshot_entered = threading.Event()
    writer_finished = threading.Event()
    writer_errors: list[BaseException] = []
    writer_committed_during_snapshot: list[bool] = []
    real_validate = authorizer.repository.validate_live_authorization_snapshot

    def blocking_validate(*args, **kwargs):
        snapshot_entered.set()
        writer_committed_during_snapshot.append(
            writer_finished.wait(timeout=1)
        )
        return real_validate(*args, **kwargs)

    def save_changed_policy() -> None:
        try:
            assert snapshot_entered.wait(timeout=5)
            changed = copy.deepcopy(enabled_result.config)
            changed["sources"]["hh"]["autopilot"]["limits"][
                "daily_success"
            ] = 51
            save_config(config_file, changed)
        except BaseException as exc:
            writer_errors.append(exc)
        finally:
            writer_finished.set()

    monkeypatch.setattr(
        authorizer.repository,
        "validate_live_authorization_snapshot",
        blocking_validate,
    )
    writer = threading.Thread(target=save_changed_policy)
    writer.start()
    try:
        authorization = authorizer.issue_live_authorization(
            "default",
            enabled_result.config,
            run_id=run.id,
            fencing_token=11,
        )
    finally:
        writer_finished.wait(timeout=5)
        writer.join(timeout=5)

    assert isinstance(authorization, LiveAuthorization)
    assert writer.is_alive() is False
    assert writer_errors == []
    assert writer_committed_during_snapshot == [False]


def test_enable_rejects_reentrant_policy_save_from_current_resolver(
    authorizer, valid_config, config_file
) -> None:
    resolver_calls = 0

    def mutating_material(config_snapshot, account_id):
        nonlocal resolver_calls
        resolver_calls += 1
        if resolver_calls == 2:
            changed = copy.deepcopy(config_snapshot)
            changed["sources"]["hh"]["autopilot"]["limits"][
                "daily_success"
            ] = 51
            save_config(config_file, changed)
        return _material(config_snapshot, account_id)

    authorizer.policy_material_resolver = mutating_material

    with pytest.raises(
        AuthorizationDenied, match="config_projection_unavailable"
    ):
        authorizer.enable(
            ["default"],
            valid_config,
            confirm=True,
            actor="cli",
            source="test",
        )

    assert authorizer.repository.active_grant("default", "applications") is None


def test_live_authorization_rejects_reentrant_policy_save_from_current_resolver(
    authorizer, enabled_result, config_file
) -> None:
    run = _running_authorized_run(authorizer, enabled_result)
    resolver_calls = 0

    def mutating_material(config_snapshot, account_id):
        nonlocal resolver_calls
        resolver_calls += 1
        if resolver_calls == 2:
            changed = copy.deepcopy(config_snapshot)
            changed["sources"]["hh"]["autopilot"]["limits"][
                "daily_success"
            ] = 51
            save_config(config_file, changed)
        return _material(config_snapshot, account_id)

    authorizer.policy_material_resolver = mutating_material

    with pytest.raises(
        AuthorizationDenied, match="config_projection_unavailable"
    ):
        authorizer.issue_live_authorization(
            "default",
            enabled_result.config,
            run_id=run.id,
            fencing_token=11,
        )


def test_enable_rejects_reentrant_policy_save_from_repository_snapshot(
    authorizer, valid_config, config_file
) -> None:
    policy_saved = False

    def save_policy_during_grant_read(statement: str) -> None:
        nonlocal policy_saved
        normalized = " ".join(statement.split()).casefold()
        if policy_saved or "select * from hh_autopilot_grants" not in normalized:
            return
        policy_saved = True
        changed = copy.deepcopy(valid_config)
        changed["sources"]["hh"]["autopilot"]["limits"][  # type: ignore[index]
            "daily_success"
        ] = 51
        save_config(config_file, changed)

    authorizer.repository.conn.set_trace_callback(save_policy_during_grant_read)
    try:
        with pytest.raises(
            AuthorizationDenied, match="config_projection_unavailable"
        ):
            authorizer.enable(
                ["default"],
                valid_config,
                confirm=True,
                actor="cli",
                source="test",
            )
    finally:
        authorizer.repository.conn.set_trace_callback(None)

    assert policy_saved is True
    assert authorizer.repository.active_grant("default", "applications") is None


def test_live_authorization_rejects_reentrant_policy_save_from_repository_snapshot(
    authorizer, enabled_result, config_file
) -> None:
    run = _running_authorized_run(authorizer, enabled_result)
    policy_saved = False

    def save_policy_during_grant_read(statement: str) -> None:
        nonlocal policy_saved
        normalized = " ".join(statement.split()).casefold()
        if policy_saved or "select * from hh_autopilot_grants" not in normalized:
            return
        policy_saved = True
        changed = copy.deepcopy(enabled_result.config)
        changed["sources"]["hh"]["autopilot"]["limits"][  # type: ignore[index]
            "daily_success"
        ] = 51
        save_config(config_file, changed)

    authorizer.repository.conn.set_trace_callback(save_policy_during_grant_read)
    try:
        with pytest.raises(
            AuthorizationDenied, match="config_projection_unavailable"
        ):
            authorizer.issue_live_authorization(
                "default",
                enabled_result.config,
                run_id=run.id,
                fencing_token=11,
            )
    finally:
        authorizer.repository.conn.set_trace_callback(None)

    assert policy_saved is True


def test_missing_file_enable_sanitizes_unselected_fallback_projections(
    repository, tmp_path, valid_config
) -> None:
    config = _two_account_config(valid_config)
    accounts = config["sources"]["hh"]["autopilot"]["accounts"]
    accounts[0].update(enabled=True, authorization_generation=41)
    accounts[1].update(enabled=True, authorization_generation=42)
    path = tmp_path / "missing-config.json"
    authorizer = HHAutopilotAuthorizer(
        repository,
        config_path=path,
        policy_material_resolver=_material,
    )

    enabled = authorizer.enable(
        ["default"], config, confirm=True, actor="cli", source="test"
    )

    projected = enabled.config["sources"]["hh"]["autopilot"]["accounts"]
    assert projected[0]["enabled"] is True
    assert projected[0]["authorization_generation"] == 1
    assert projected[1]["enabled"] is False
    assert projected[1]["authorization_generation"] is None
    assert json.loads(path.read_text(encoding="utf-8")) == enabled.config


def test_missing_file_pause_only_sanitizes_every_fallback_projection(
    repository, tmp_path, valid_config
) -> None:
    config = _two_account_config(valid_config)
    accounts = config["sources"]["hh"]["autopilot"]["accounts"]
    accounts[0].update(enabled=True, authorization_generation=41)
    accounts[1].update(enabled=True, authorization_generation=42)
    path = tmp_path / "missing-pause-config.json"
    authorizer = HHAutopilotAuthorizer(
        repository,
        config_path=path,
        policy_material_resolver=_material,
    )

    paused = authorizer.set_pause(
        "account",
        "default",
        config,
        paused=True,
        confirm=True,
        actor="cli",
    )

    projected = paused.config["sources"]["hh"]["autopilot"]["accounts"]
    assert projected[0]["paused"] is True
    for account in projected:
        assert account["enabled"] is False
        assert account["authorization_generation"] is None
    assert json.loads(path.read_text(encoding="utf-8")) == paused.config


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


@pytest.mark.parametrize(
    "expectations",
    [
        {"default": (True, "hash")},
        {"default": (1, "hash"), " DEFAULT ": (1, "hash")},
        {"default": (1, "hash"), "second": (1, "")},
    ],
)
def test_validate_exact_active_grants_validates_everything_before_begin(
    repository, expectations
) -> None:
    statements: list[str] = []
    repository.conn.set_trace_callback(statements.append)
    try:
        with pytest.raises((TypeError, ValueError)):
            repository.validate_exact_active_grants(expectations)
    finally:
        repository.conn.set_trace_callback(None)

    assert not any(statement.startswith("BEGIN") for statement in statements)


def test_validate_exact_active_grants_rolls_back_snapshot_on_generation_mismatch(
    repository,
) -> None:
    generations = repository.create_grants(
        [
            ("default", "hash-a", "cli", "test"),
            ("second", "hash-b", "cli", "test"),
        ]
    )
    statements: list[str] = []
    repository.conn.set_trace_callback(statements.append)
    try:
        with pytest.raises(
            RepositoryAuthorizationDenied,
            match="authorization_state_mismatch",
        ):
            repository.validate_exact_active_grants(
                {
                    "default": (generations["default"], "hash-a"),
                    "second": (generations["second"] + 1, "hash-b"),
                }
            )
    finally:
        repository.conn.set_trace_callback(None)

    assert [statement for statement in statements if statement.startswith("BEGIN")] == [
        "BEGIN IMMEDIATE"
    ]
    assert "ROLLBACK" in statements
    assert "COMMIT" not in statements
    assert repository.active_grant("default", "applications") is not None
    assert repository.active_grant("second", "applications") is not None


def test_validate_exact_active_grants_reports_hash_mismatch_and_commits_success(
    repository,
) -> None:
    generation = repository.create_grants(
        [("default", "hash", "cli", "test")]
    )["default"]
    with pytest.raises(
        RepositoryAuthorizationDenied, match="policy_hash_mismatch"
    ):
        repository.validate_exact_active_grants(
            {"default": (generation, "different")}
        )

    statements: list[str] = []
    repository.conn.set_trace_callback(statements.append)
    try:
        repository.validate_exact_active_grants(
            {" DEFAULT ": (generation, "hash")}
        )
    finally:
        repository.conn.set_trace_callback(None)

    assert [statement for statement in statements if statement.startswith("BEGIN")] == [
        "BEGIN IMMEDIATE"
    ]
    assert "COMMIT" in statements


@pytest.mark.parametrize(
    "kwargs",
    [
        {
            "projections": {
                "default": (False, None),
                "second": (True, True),
            }
        },
        {
            "projections": {"default": (True, 1)},
            "policy_hashes": {"other": "hash"},
        },
        {
            "projections": {"default": (True, 1)},
            "policy_errors": {"other": "policy_material_unavailable"},
        },
    ],
)
def test_reconcile_authorization_projection_validates_everything_before_begin(
    repository, kwargs
) -> None:
    statements: list[str] = []
    repository.conn.set_trace_callback(statements.append)
    try:
        with pytest.raises((TypeError, ValueError)):
            repository.reconcile_authorization_projection(
                actor="startup",
                reason="test",
                **kwargs,
            )
    finally:
        repository.conn.set_trace_callback(None)

    assert not any(statement.startswith("BEGIN") for statement in statements)


def test_reconcile_authorization_projection_rolls_back_revoke_when_stop_fails(
    repository,
) -> None:
    generation = repository.create_grants(
        [("default", "hash", "cli", "test")]
    )["default"]
    grant = repository.active_grant("default", "applications")
    run = repository.create_run(
        "default",
        trigger="manual",
        policy_hash="hash",
        grant_id=grant.id,
    )
    repository.conn.execute(
        """
        CREATE TRIGGER abort_reconciliation_stop
        BEFORE UPDATE OF status ON hh_autopilot_runs
        WHEN NEW.status = 'stop_requested'
        BEGIN
            SELECT RAISE(ABORT, 'reconciliation stop failure');
        END
        """
    )

    with pytest.raises(
        sqlite3.IntegrityError, match="reconciliation stop failure"
    ):
        repository.reconcile_authorization_projection(
            {"default": (False, generation)},
            actor="startup",
            reason="test",
        )

    assert repository.active_grant("default", "applications") is not None
    assert repository.get_run(run.id).status == "running"


def test_reconcile_authorization_projection_revokes_current_replacement_generation(
    repository,
) -> None:
    generation = repository.create_grants(
        [("default", "hash", "cli", "first")]
    )["default"]
    first = repository.active_grant("default", "applications")
    run = repository.create_run(
        "default",
        trigger="manual",
        policy_hash="hash",
        grant_id=first.id,
    )
    replacement_generation = repository.create_grants(
        [("default", "hash", "scheduler", "replacement")]
    )["default"]
    assert replacement_generation == generation + 1

    result = repository.reconcile_authorization_projection(
        {"default": (True, generation)},
        actor="startup",
        reason="test",
    )

    assert result.mismatches == {"default": "authorization_state_mismatch"}
    assert result.revoked_accounts == ("default",)
    assert result.stopped_accounts == ("default",)
    assert repository.active_grant("default", "applications") is None
    assert repository.get_run(run.id).status == "stop_requested"


def test_reconcile_authorization_projection_result_order_is_stable(repository) -> None:
    repository.create_grants(
        [
            ("second", "hash", "cli", "test"),
            ("default", "hash", "cli", "test"),
        ]
    )
    repository.create_run("second", trigger="manual", policy_hash="hash")
    repository.create_run("default", trigger="manual", policy_hash="hash")

    result = repository.reconcile_authorization_projection(
        {}, actor="startup", reason="missing_config"
    )

    assert result.revoked_accounts == ("default", "second")
    assert result.stopped_accounts == ("default", "second")


def test_live_authorization_snapshot_validates_request_before_begin(repository) -> None:
    statements: list[str] = []
    repository.conn.set_trace_callback(statements.append)
    try:
        with pytest.raises(TypeError, match="run_id"):
            repository.validate_live_authorization_snapshot(
                "default",
                generation=1,
                policy_hash="hash",
                run_id=True,
                fencing_token=1,
            )
    finally:
        repository.conn.set_trace_callback(None)

    assert not any(statement.startswith("BEGIN") for statement in statements)


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


def test_reconciliation_revokes_current_generation_created_during_policy_resolution(
    authorizer, enabled_result
) -> None:
    run = _running_authorized_run(authorizer, enabled_result)
    expected_hash = enabled_result.policy_hashes["default"]
    raced = False

    def racing_material(config, account_id):
        nonlocal raced
        if not raced:
            raced = True
            authorizer.repository.create_grants(
                [("default", expected_hash, "scheduler", "concurrent")]
            )
        return _material(config, account_id)

    authorizer.policy_material_resolver = racing_material

    result = authorizer.reconcile_config_projection(
        enabled_result.config, actor="startup"
    )

    assert raced is True
    assert result.mismatches == {"default": "authorization_state_mismatch"}
    assert result.revoked_accounts == ("default",)
    assert authorizer.repository.active_grant("default", "applications") is None
    assert authorizer.repository.get_run(run.id).status == "stop_requested"


@pytest.mark.parametrize(
    ("scenario", "survives"),
    [
        ("disabled", False),
        ("generation_mismatch", False),
        ("removed", False),
        ("missing_config", False),
        ("malformed", False),
        ("matching", True),
    ],
)
def test_work_hunter_startup_reconciles_authoritative_projection(
    tmp_path, scenario, survives
) -> None:
    from work_hunter.services import WorkHunter

    storage = Storage(database_path(tmp_path))
    repository = AutopilotRepository(storage)
    generation = repository.create_grants(
        [("default", "stored-policy", "cli", "seed")]
    )["default"]
    grant = repository.active_grant("default", "applications")
    run = repository.create_run(
        "default",
        trigger="manual",
        policy_hash="stored-policy",
        grant_id=grant.id,
        fencing_token=1,
    )
    storage.close()

    config = default_config()
    account = config["sources"]["hh"]["autopilot"]["accounts"][0]
    account.update(enabled=True, authorization_generation=generation)
    if scenario == "disabled":
        account["enabled"] = False
    elif scenario == "generation_mismatch":
        account["authorization_generation"] = generation + 1
    elif scenario == "removed":
        config = _two_account_config(config)
        config["sources"]["hh"]["autopilot"]["accounts"] = [
            config["sources"]["hh"]["autopilot"]["accounts"][1]
        ]
    elif scenario == "malformed":
        config["sources"]["hh"]["autopilot"]["accounts"] = "broken"
    if scenario != "missing_config":
        path = config_path(tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(config), encoding="utf-8")

    app = WorkHunter(tmp_path)
    restarted_repository = AutopilotRepository(app.storage)
    active = restarted_repository.active_grant("default", "applications")

    assert (active is not None) is survives
    assert restarted_repository.get_run(run.id).status == (
        "running" if survives else "stop_requested"
    )
    app.storage.close()


def test_lazy_stale_work_hunter_startup_uses_current_persisted_projection(
    tmp_path,
) -> None:
    from work_hunter.services import WorkHunter

    path = config_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(default_config()), encoding="utf-8")
    stale_instance = WorkHunter(tmp_path)
    enabling_instance = WorkHunter(tmp_path)
    enabling_repository = AutopilotRepository(enabling_instance.storage)
    authorizer = HHAutopilotAuthorizer(
        enabling_repository,
        config_path=path,
        policy_material_resolver=_material,
    )
    enabled = authorizer.enable(
        ["default"],
        enabling_instance.config,
        confirm=True,
        actor="cli",
        source="test",
    )
    grant = enabling_repository.active_grant("default", "applications")
    assert grant is not None
    run = enabling_repository.create_run(
        "default",
        trigger="manual",
        policy_hash=enabled.policy_hashes["default"],
        grant_id=grant.id,
        fencing_token=1,
    )

    restarted_repository = AutopilotRepository(stale_instance.storage)

    assert restarted_repository.active_grant("default", "applications") is not None
    assert restarted_repository.get_run(run.id).status == "running"
    stale_instance.storage.close()
    enabling_instance.storage.close()


def test_startup_holds_config_snapshot_lock_through_reconciliation(
    tmp_path, monkeypatch
) -> None:
    from work_hunter.services import WorkHunter

    path = config_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(default_config()), encoding="utf-8")
    database = database_path(tmp_path)
    migrated = Storage(database)
    migrated.close()
    stale_instance = WorkHunter(tmp_path)
    startup_reconcile_entered = threading.Event()
    allow_startup_reconcile = threading.Event()
    startup_finished = threading.Event()
    fresh_resolver_entered = threading.Event()
    allow_fresh_resolver = threading.Event()
    startup_errors: list[BaseException] = []
    enable_errors: list[BaseException] = []
    enable_results: list[object] = []
    resolver_calls = 0
    real_reconcile = AutopilotRepository.reconcile_authorization_projection

    def blocking_startup_reconcile(repository, projections, **kwargs):
        startup_reconcile_entered.set()
        assert allow_startup_reconcile.wait(timeout=5)
        return real_reconcile(repository, projections, **kwargs)

    def blocking_material(config_snapshot, account_id):
        nonlocal resolver_calls
        resolver_calls += 1
        if resolver_calls == 2:
            fresh_resolver_entered.set()
            assert allow_fresh_resolver.wait(timeout=5)
        return _material(config_snapshot, account_id)

    def start_stale_instance() -> None:
        try:
            startup_storage = stale_instance.storage
            startup_storage.close()
        except BaseException as exc:
            startup_errors.append(exc)
        finally:
            startup_finished.set()

    def enable_account() -> None:
        storage = Storage(database)
        try:
            authorizer = HHAutopilotAuthorizer(
                AutopilotRepository(storage),
                config_path=path,
                policy_material_resolver=blocking_material,
            )
            try:
                enable_results.append(
                    authorizer.enable(
                        ["default"],
                        default_config(),
                        confirm=True,
                        actor="cli",
                        source="test",
                    )
                )
            except BaseException as exc:
                enable_errors.append(exc)
        finally:
            storage.close()

    monkeypatch.setattr(
        AutopilotRepository,
        "reconcile_authorization_projection",
        blocking_startup_reconcile,
    )
    startup_worker = threading.Thread(target=start_stale_instance)
    startup_worker.start()
    assert startup_reconcile_entered.wait(timeout=5)
    enable_worker = threading.Thread(target=enable_account)
    enable_worker.start()
    leaked_past_config_lock = fresh_resolver_entered.wait(timeout=2)
    allow_startup_reconcile.set()
    assert startup_finished.wait(timeout=5)
    assert fresh_resolver_entered.wait(timeout=5)
    allow_fresh_resolver.set()
    startup_worker.join(timeout=5)
    enable_worker.join(timeout=5)

    assert leaked_past_config_lock is False
    assert startup_worker.is_alive() is False
    assert enable_worker.is_alive() is False
    assert startup_errors == []
    assert enable_results == []
    assert len(enable_errors) == 1
    assert isinstance(enable_errors[0], AuthorizationDenied)
    assert enable_errors[0].code == "authorization_state_mismatch"
    inspection = Storage(database)
    try:
        assert (
            AutopilotRepository(inspection).active_grant(
                "default", "applications"
            )
            is None
        )
    finally:
        inspection.close()


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


def test_live_authorization_observes_pause_committed_while_resolver_is_blocked(
    authorizer, enabled_result
) -> None:
    run = _running_authorized_run(authorizer, enabled_result)
    resolver_started = threading.Event()
    pause_committed = threading.Event()
    worker_errors: list[BaseException] = []
    database = authorizer.repository.storage.path

    def blocking_material(config, account_id):
        resolver_started.set()
        assert pause_committed.wait(timeout=5)
        return _material(config, account_id)

    def commit_pause() -> None:
        try:
            assert resolver_started.wait(timeout=5)
            storage = Storage(database)
            try:
                AutopilotRepository(storage).set_pause(
                    "account", "default", True
                )
            finally:
                storage.close()
            pause_committed.set()
        except BaseException as exc:
            worker_errors.append(exc)
            pause_committed.set()

    authorizer.policy_material_resolver = blocking_material
    worker = threading.Thread(target=commit_pause)
    worker.start()
    try:
        with pytest.raises(
            AuthorizationDenied, match="autopilot_disabled_or_paused"
        ):
            authorizer.issue_live_authorization(
                "default",
                enabled_result.config,
                run_id=run.id,
                fencing_token=11,
            )
    finally:
        worker.join(timeout=5)

    assert worker.is_alive() is False
    assert worker_errors == []
