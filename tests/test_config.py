import pytest

import work_hunter.config as config_module
from work_hunter.config import active_profile, default_config, load_config, mask_secrets, save_config


def test_save_and_load_config(tmp_path):
    path = tmp_path / "config.json"
    cfg = default_config()
    cfg["profiles"]["default"]["queries"] = ["python backend"]
    save_config(path, cfg)

    loaded = load_config(path)

    profile = active_profile(loaded)
    assert profile["queries"] == ["python backend"]
    assert loaded["sources"]["hh"]["enabled"] is True


def test_mask_secrets():
    masked = mask_secrets({"token": "abc", "max_tokens": 1500, "nested": {"client_secret": "def"}})

    assert masked["token"] == "***"
    assert masked["max_tokens"] == 1500
    assert masked["nested"]["client_secret"] == "***"


def test_default_config_includes_opencode_and_research_defaults():
    cfg = default_config()

    assert cfg["ai"]["backend"] == "direct"
    assert cfg["ai"]["opencode_transport"] == "cli"
    assert cfg["ai"]["opencode_command"] == "opencode"
    assert cfg["ai"]["opencode_agent"] == "work-hunter-ai"
    assert cfg["ai"]["opencode_server_url"] == "http://127.0.0.1:4096"
    assert cfg["research"]["max_results"] == 200


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
    "submitted",
    [
        {"ai": None},
        {"ai": {"api_key": []}},
        {"sources": {"hh": None}},
        {"hh_account_profiles": {"work": None}},
        {"custom": None},
        {"custom": [{"access_token": "***"}]},
    ],
)
def test_merge_masked_config_rejects_structural_secret_clear_bypasses(submitted):
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
    assert merged["custom"][0]["access_token"] == "saved-list-value"


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
