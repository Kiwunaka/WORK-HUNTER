import copy
import errno
import json
import os
import shutil
import stat
from pathlib import Path

import pytest

import work_hunter.config as config_module
from work_hunter.config import (
    active_profile,
    default_config,
    load_config,
    mask_secrets,
    save_config,
    update_config,
)


def test_save_and_load_config(tmp_path):
    path = tmp_path / "config.json"
    cfg = default_config()
    cfg["profiles"]["default"]["queries"] = ["python backend"]
    save_config(path, cfg)

    loaded = load_config(path)

    profile = active_profile(loaded)
    assert profile["queries"] == ["python backend"]
    assert loaded["sources"]["hh"]["enabled"] is True


def test_save_config_replaces_complete_temp_file(monkeypatch, tmp_path):
    path = tmp_path / "nested" / "config.json"
    config = {"profile": "тест", "nested": {"enabled": True}}
    replacements: list[dict[str, object]] = []
    original_replace = os.replace

    def inspect_replace(source, destination):
        temporary = Path(source)
        replacements.append(
            {
                "source": temporary,
                "destination": Path(destination),
                "payload": json.loads(temporary.read_text(encoding="utf-8")),
            }
        )
        original_replace(source, destination)

    monkeypatch.setattr(os, "replace", inspect_replace)

    save_config(path, config)

    assert len(replacements) == 1
    assert replacements[0]["source"].parent == path.parent
    assert replacements[0]["source"] != path
    assert replacements[0]["destination"] == path
    assert replacements[0]["payload"] == config
    assert json.loads(path.read_text(encoding="utf-8")) == config


def test_save_config_replace_failure_preserves_old_file_and_cleans_temp(
    monkeypatch,
    tmp_path,
):
    path = tmp_path / "config.json"
    path.write_text('{"version": "old"}\n', encoding="utf-8")
    copied_modes: list[tuple[Path, Path]] = []
    replacement_sources: list[Path] = []
    directory_syncs: list[Path] = []
    original_copymode = shutil.copymode

    def record_copymode(source, destination, *args, **kwargs):
        copied_modes.append((Path(source), Path(destination)))
        return original_copymode(source, destination, *args, **kwargs)

    def fail_replace(source, destination):
        replacement_sources.append(Path(source))
        raise OSError("replace failed")

    monkeypatch.setattr(shutil, "copymode", record_copymode)
    monkeypatch.setattr(os, "replace", fail_replace)
    monkeypatch.setattr(
        config_module,
        "_fsync_directory",
        lambda directory: directory_syncs.append(Path(directory)),
        raising=False,
    )

    with pytest.raises(OSError, match="replace failed"):
        save_config(path, {"version": "new"})

    assert path.read_text(encoding="utf-8") == '{"version": "old"}\n'
    assert len(copied_modes) == 1
    assert copied_modes[0][0] == path
    assert replacement_sources == [copied_modes[0][1]]
    assert not replacement_sources[0].exists()
    assert directory_syncs == []


def test_save_config_serialization_failure_preserves_old_file_and_cleans_temp(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"version": "old"}\n', encoding="utf-8")

    with pytest.raises(TypeError):
        save_config(path, {"unsupported": object()})

    assert path.read_text(encoding="utf-8") == '{"version": "old"}\n'
    assert list(tmp_path.glob(f".{path.name}.*.tmp")) == []

    save_config(path, {"version": "after-error"})

    assert load_config(path)["version"] == "after-error"


def test_save_config_fsyncs_parent_directory_after_replace(monkeypatch, tmp_path):
    path = tmp_path / "nested" / "config.json"
    directory_syncs: list[Path] = []
    monkeypatch.setattr(
        config_module,
        "_fsync_directory",
        lambda directory: directory_syncs.append(Path(directory)),
        raising=False,
    )

    save_config(path, {"status": "ok"})

    assert directory_syncs == [path.parent]


def test_fsync_directory_uses_and_closes_directory_descriptor(monkeypatch, tmp_path):
    sync_directory = getattr(config_module, "_fsync_directory", None)
    assert callable(sync_directory)
    opened: list[Path] = []
    synced: list[int] = []
    closed: list[int] = []

    def open_directory(path, flags):
        opened.append(Path(path))
        return 91

    monkeypatch.setattr(config_module.os, "open", open_directory)
    monkeypatch.setattr(config_module.os, "fsync", lambda fd: synced.append(fd))
    monkeypatch.setattr(config_module.os, "close", lambda fd: closed.append(fd))

    sync_directory(tmp_path)

    assert opened == [tmp_path]
    assert synced == [91]
    assert closed == [91]


def test_fsync_directory_ignores_unsupported_platform(monkeypatch, tmp_path):
    sync_directory = getattr(config_module, "_fsync_directory", None)
    assert callable(sync_directory)
    monkeypatch.setattr(
        config_module.os,
        "open",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("unsupported")),
    )

    sync_directory(tmp_path)


def test_windows_config_lock_retries_until_contention_clears(monkeypatch):
    lock_windows = getattr(config_module, "_lock_windows_config_fd", None)
    assert callable(lock_windows)
    attempts: list[tuple[int, int, int]] = []
    sleeps: list[float] = []

    class FakeMsvcrt:
        LK_NBLCK = 1

        @staticmethod
        def locking(fd, mode, length):
            attempts.append((fd, mode, length))
            if len(attempts) < 3:
                raise OSError(errno.EACCES, "locked")

    monkeypatch.setattr(
        config_module.importlib,
        "import_module",
        lambda name: FakeMsvcrt,
    )
    monkeypatch.setattr(config_module.time, "sleep", lambda seconds: sleeps.append(seconds))

    lock_windows(91)

    assert attempts == [(91, FakeMsvcrt.LK_NBLCK, 1)] * 3
    assert sleeps == [0.05, 0.05]


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits are not portable to Windows")
def test_save_config_preserves_existing_posix_mode(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{}\n", encoding="utf-8")
    path.chmod(0o640)

    save_config(path, {"status": "ok"})

    assert stat.S_IMODE(path.stat().st_mode) == 0o640


@pytest.mark.parametrize(
    ("baseline_value", "desired_value", "fresh_value"),
    [
        (True, 1, False),
        ([True], [1], ["fresh"]),
    ],
)
def test_three_way_config_merge_distinguishes_json_value_types(
    baseline_value,
    desired_value,
    fresh_value,
):
    merged = config_module.merge_config_snapshot_changes(
        {"value": baseline_value},
        {"value": desired_value},
        {"value": fresh_value},
    )

    assert merged == {"value": desired_value}
    assert type(merged["value"]) is type(desired_value)


def test_mask_secrets():
    masked = mask_secrets({"token": "abc", "max_tokens": 1500, "nested": {"client_secret": "def"}})

    assert masked["token"] == "***"
    assert masked["max_tokens"] == 1500
    assert masked["nested"]["client_secret"] == "***"


def test_default_config_includes_opencode_and_research_defaults():
    cfg = default_config()

    assert cfg["ui"]["onboarding_version"] == 0
    assert cfg["ai"]["backend"] == "direct"
    assert cfg["ai"]["opencode_transport"] == "cli"
    assert cfg["ai"]["opencode_command"] == "opencode"
    assert cfg["ai"]["opencode_agent"] == "work-hunter-ai"
    assert cfg["ai"]["opencode_server_url"] == "http://127.0.0.1:4096"
    assert cfg["research"]["max_results"] == 200


@pytest.mark.parametrize(
    ("profiles", "expected_profile"),
    [
        ({}, "default"),
        ({"default": {"queries": ["go"]}}, "legacy"),
        (
            {
                "default": {"queries": ["go"]},
                "legacy": {"queries": ["rust"]},
                "legacy-2": {"queries": ["java"]},
            },
            "legacy-3",
        ),
    ],
)
def test_load_config_migrates_flat_profile_without_overwriting_named_profiles(
    tmp_path, profiles, expected_profile
):
    path = tmp_path / "config.json"
    raw = {
        "profile": {
            "name": "Кандидат",
            "queries": ["python"],
            "desired_roles": ["backend"],
        },
        "profiles": profiles,
        "ui": {"host": "127.0.0.1", "port": 8787},
    }
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    loaded = load_config(path)

    assert loaded["profile"] == expected_profile
    assert loaded["profiles"][expected_profile]["queries"] == ["python"]
    assert loaded["ui"]["onboarding_version"] == 2
    for profile_id, profile in profiles.items():
        assert loaded["profiles"][profile_id]["queries"] == profile["queries"]
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["profile"] == expected_profile
    assert persisted["ui"]["onboarding_version"] == 2


def test_load_config_reuses_structurally_equal_default_profile(tmp_path):
    path = tmp_path / "config.json"
    flat_profile = {"desired_roles": ["backend"], "queries": ["python"]}
    path.write_text(
        json.dumps(
            {
                "profile": {"queries": ["python"], "desired_roles": ["backend"]},
                "profiles": {"default": flat_profile},
                "ui": {"host": "127.0.0.1", "port": 8787},
            }
        ),
        encoding="utf-8",
    )

    loaded = load_config(path)

    assert loaded["profile"] == "default"
    assert set(loaded["profiles"]) == {"default"}


def test_default_config_includes_hh_transport_defaults():
    cfg = default_config()
    hh_config = cfg["sources"]["hh"]

    assert hh_config["android_user_agent"] == ""
    assert hh_config["hh_cookie_file"] == ""
    assert hh_config["challenge_mode"] == "manual"
    assert hh_config["form_mode"] == "manual"


def test_sensitive_key_predicate_matches_masking_rules():
    assert config_module.is_sensitive_key("API_KEY") is True
    assert config_module.is_sensitive_key("client_secret") is True
    assert config_module.is_sensitive_key("max_tokens") is False


def test_merge_masked_config_preserves_nested_secrets_for_mask_empty_and_none():
    stored = {
        "ai": {"api_key": "saved-ai-value", "model": "model-a"},
        "sources": {
            "hh": {
                "access_token": "saved-hh-value",
                "refresh_token": "saved-refresh-value",
            }
        },
    }
    submitted = {
        "ai": {"api_key": "***", "model": "model-b"},
        "sources": {"hh": {"access_token": "", "refresh_token": None}},
    }

    merged = config_module.merge_masked_config(stored, submitted)

    assert merged["ai"] == {"api_key": "saved-ai-value", "model": "model-b"}
    assert merged["sources"]["hh"] == {
        "access_token": "saved-hh-value",
        "refresh_token": "saved-refresh-value",
    }


@pytest.mark.parametrize(
    ("submitted", "preserves_list_secret"),
    [
        ({"ai": None}, True),
        ({"ai": {"api_key": []}}, True),
        ({"sources": {"hh": None}}, True),
        ({"hh_account_profiles": {"work": None}}, True),
        ({"custom": None}, True),
        ({"custom": [{"access_token": "***"}]}, False),
    ],
)
def test_merge_masked_config_rejects_structural_secret_clear_bypasses(
    submitted,
    preserves_list_secret,
):
    stored = {
        "ai": {"api_key": "saved-ai-value", "model": "model-a"},
        "sources": {"hh": {"access_token": "saved-hh-value"}},
        "hh_account_profiles": {
            "work": {"refresh_token": "saved-profile-value"}
        },
        "custom": [{"access_token": "saved-list-value"}],
    }

    merged = config_module.merge_masked_config(stored, submitted)

    assert merged["ai"]["api_key"] == "saved-ai-value"
    assert merged["sources"]["hh"]["access_token"] == "saved-hh-value"
    assert (
        merged["hh_account_profiles"]["work"]["refresh_token"]
        == "saved-profile-value"
    )
    if preserves_list_secret:
        assert merged["custom"][0]["access_token"] == "saved-list-value"
    else:
        assert "access_token" not in merged["custom"][0]


def test_clear_config_secret_value_copies_and_clears_only_allowlisted_path():
    stored = {
        "sources": {
            "hh": {
                "access_token": "saved-access-value",
                "refresh_token": "saved-refresh-value",
            }
        }
    }

    cleared = config_module.clear_config_secret_value(
        stored,
        "sources.hh.access_token",
    )

    assert cleared["sources"]["hh"] == {
        "access_token": "",
        "refresh_token": "saved-refresh-value",
    }
    assert stored["sources"]["hh"]["access_token"] == "saved-access-value"
    with pytest.raises(ValueError, match="not allowed"):
        config_module.clear_config_secret_value(stored, "sources.hh.enabled")


def test_clear_config_secret_value_allows_only_existing_hh_account_profiles():
    stored = {
        "hh_account_profiles": {
            "primary": {
                "access_token": "saved-account-value",
                "refresh_token": "saved-refresh-value",
            }
        }
    }

    cleared = config_module.clear_config_secret_value(
        stored,
        "hh_account_profiles.primary.refresh_token",
    )

    assert cleared["hh_account_profiles"]["primary"] == {
        "access_token": "saved-account-value",
        "refresh_token": "",
    }
    with pytest.raises(ValueError, match="not allowed"):
        config_module.clear_config_secret_value(
            stored,
            "hh_account_profiles.missing.refresh_token",
        )


def test_clear_config_secret_value_supports_existing_profile_names_with_dots():
    stored = {
        "hh_account_profiles": {
            "work.prod": {"client_secret": "saved-profile-value"},
        }
    }

    cleared = config_module.clear_config_secret_value(
        stored,
        "hh_account_profiles.work.prod.client_secret",
    )

    assert cleared["hh_account_profiles"]["work.prod"]["client_secret"] == ""


def test_preserve_managed_autopilot_fields_matches_canonical_profile_ids():
    stored = default_config()
    stored_account = stored["sources"]["hh"]["autopilot"]["accounts"][0]
    stored_account.update(
        profile_id=" Default ", enabled=True, authorization_generation=7
    )
    submitted = copy.deepcopy(stored)
    submitted_account = submitted["sources"]["hh"]["autopilot"]["accounts"][0]
    submitted_account.update(
        profile_id="DEFAULT", enabled=False, authorization_generation=999
    )

    preserved = config_module.preserve_managed_autopilot_fields(stored, submitted)

    account = preserved["sources"]["hh"]["autopilot"]["accounts"][0]
    assert account["enabled"] is True
    assert account["authorization_generation"] == 7


def test_preserve_managed_autopilot_fields_disables_new_accounts_and_allows_missing():
    stored = default_config()
    stored_account = stored["sources"]["hh"]["autopilot"]["accounts"][0]
    stored_account.update(enabled=True, authorization_generation=4)
    submitted = copy.deepcopy(stored)
    submitted["sources"]["hh"]["autopilot"]["accounts"] = [
        {
            **copy.deepcopy(stored_account),
            "profile_id": "new",
            "enabled": True,
            "authorization_generation": 999,
        }
    ]

    preserved = config_module.preserve_managed_autopilot_fields(stored, submitted)

    assert preserved["sources"]["hh"]["autopilot"]["accounts"][0]["enabled"] is False
    assert (
        preserved["sources"]["hh"]["autopilot"]["accounts"][0][
            "authorization_generation"
        ]
        is None
    )


def test_preserve_managed_autopilot_fields_rejects_canonical_duplicates():
    stored = default_config()
    submitted = copy.deepcopy(stored)
    duplicate = copy.deepcopy(
        submitted["sources"]["hh"]["autopilot"]["accounts"][0]
    )
    duplicate["profile_id"] = " DEFAULT "
    submitted["sources"]["hh"]["autopilot"]["accounts"].append(duplicate)

    with pytest.raises(ValueError, match="duplicate"):
        config_module.preserve_managed_autopilot_fields(stored, submitted)


def test_save_config_preserves_managed_authorization_fields(tmp_path):
    path = tmp_path / "config.json"
    stored = default_config()
    stored_account = stored["sources"]["hh"]["autopilot"]["accounts"][0]
    stored_account.update(enabled=True, authorization_generation=5)
    path.write_text(json.dumps(stored), encoding="utf-8")
    submitted = copy.deepcopy(stored)
    submitted_account = submitted["sources"]["hh"]["autopilot"]["accounts"][0]
    submitted_account.update(enabled=False, authorization_generation=999)

    save_config(path, submitted)

    persisted = json.loads(path.read_text(encoding="utf-8"))
    account = persisted["sources"]["hh"]["autopilot"]["accounts"][0]
    assert account["enabled"] is True
    assert account["authorization_generation"] == 5


def test_save_config_returns_exact_protected_persisted_snapshot(tmp_path):
    path = tmp_path / "config.json"
    stored = default_config()
    stored_account = stored["sources"]["hh"]["autopilot"]["accounts"][0]
    stored_account.update(enabled=False, authorization_generation=None)
    path.write_text(json.dumps(stored), encoding="utf-8")
    submitted = copy.deepcopy(stored)
    submitted_account = submitted["sources"]["hh"]["autopilot"]["accounts"][0]
    submitted_account.update(enabled=True, authorization_generation=999)

    persisted = save_config(path, submitted)

    assert persisted == json.loads(path.read_text(encoding="utf-8"))
    account = persisted["sources"]["hh"]["autopilot"]["accounts"][0]
    assert account["enabled"] is False
    assert account["authorization_generation"] is None
    persisted["sources"]["hh"]["autopilot"]["accounts"][0]["enabled"] = True
    assert json.loads(path.read_text(encoding="utf-8")) != persisted


def test_save_config_does_not_mask_permission_error_reading_existing_file(
    monkeypatch, tmp_path
):
    path = tmp_path / "config.json"
    path.write_text("{}\n", encoding="utf-8")
    original_open = Path.open

    def deny_target(self, *args, **kwargs):
        if self == path:
            raise PermissionError("config read denied")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", deny_target)

    with pytest.raises(PermissionError, match="config read denied"):
        save_config(path, default_config())

    with original_open(path, "r", encoding="utf-8") as fh:
        assert fh.read() == "{}\n"


def test_save_config_rejects_corrupt_existing_json_without_overwrite(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"broken":', encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        save_config(path, default_config())

    assert path.read_text(encoding="utf-8") == '{"broken":'


def test_first_ordinary_save_cannot_self_authorize_new_account(tmp_path):
    path = tmp_path / "config.json"
    submitted = default_config()
    account = submitted["sources"]["hh"]["autopilot"]["accounts"][0]
    account.update(enabled=True, authorization_generation=999)

    save_config(path, submitted)

    persisted = json.loads(path.read_text(encoding="utf-8"))
    account = persisted["sources"]["hh"]["autopilot"]["accounts"][0]
    assert account["enabled"] is False
    assert account["authorization_generation"] is None


def test_update_config_preserves_managed_authorization_fields(tmp_path):
    path = tmp_path / "config.json"
    stored = default_config()
    stored_account = stored["sources"]["hh"]["autopilot"]["accounts"][0]
    stored_account.update(enabled=True, authorization_generation=3)
    path.write_text(json.dumps(stored), encoding="utf-8")

    updated = update_config(
        path,
        lambda config: config["sources"]["hh"]["autopilot"]["accounts"][0].update(
            enabled=False,
            authorization_generation=123,
        ),
    )

    account = updated["sources"]["hh"]["autopilot"]["accounts"][0]
    assert account["enabled"] is True
    assert account["authorization_generation"] == 3


def test_update_config_missing_file_cannot_authorize_through_fallback(tmp_path):
    path = tmp_path / "missing.json"
    fallback = default_config()
    account = fallback["sources"]["hh"]["autopilot"]["accounts"][0]
    account.update(enabled=True, authorization_generation=77)

    updated = update_config(path, lambda config: None, fallback=fallback)

    account = updated["sources"]["hh"]["autopilot"]["accounts"][0]
    assert account["enabled"] is False
    assert account["authorization_generation"] is None


def test_merge_masked_config_preserves_managed_enabled_projection():
    stored = default_config()
    stored_account = stored["sources"]["hh"]["autopilot"]["accounts"][0]
    stored_account.update(enabled=True, authorization_generation=6)
    submitted = copy.deepcopy(stored)
    submitted_account = submitted["sources"]["hh"]["autopilot"]["accounts"][0]
    submitted_account["enabled"] = False

    merged = config_module.merge_masked_config(stored, submitted)

    account = merged["sources"]["hh"]["autopilot"]["accounts"][0]
    assert account["enabled"] is True
    assert account["authorization_generation"] == 6


def test_work_hunter_init_overwrite_accepts_exact_protected_snapshot(tmp_path):
    from work_hunter.services import WorkHunter

    config = default_config()
    path = config_module.config_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config), encoding="utf-8")
    app = WorkHunter(tmp_path)
    account = app.config["sources"]["hh"]["autopilot"]["accounts"][0]
    account.update(enabled=True, authorization_generation=91)

    app.init(overwrite=True)

    disk = load_config(path)
    assert app.config == disk
    account = app.config["sources"]["hh"]["autopilot"]["accounts"][0]
    assert account["enabled"] is False
    assert account["authorization_generation"] is None
    app.storage.close()


def test_work_hunter_reset_defaults_keeps_disk_and_memory_identical(tmp_path):
    from work_hunter.services import WorkHunter

    config = default_config()
    account = config["sources"]["hh"]["autopilot"]["accounts"][0]
    account.update(enabled=True, authorization_generation=7)
    path = config_module.config_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config), encoding="utf-8")
    app = WorkHunter(tmp_path)

    app.reset_config_defaults()

    disk = load_config(path)
    assert app.config == disk
    account = app.config["sources"]["hh"]["autopilot"]["accounts"][0]
    assert account["enabled"] is True
    assert account["authorization_generation"] == 7
