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
    masked = mask_secrets(
        {
            "token": "abc",
            "max_tokens": 1500,
            "nested": {"client_secret": "def"},
            "command": "pytest hirehi --api-key sk-secret",
            "note": "OpenRouter key sk-or-v1-secret should never echo",
        }
    )

    assert masked["token"] == "***"
    assert masked["max_tokens"] == 1500
    assert masked["nested"]["client_secret"] == "***"
    assert masked["command"] == "pytest hirehi --api-key ***"
    assert masked["note"] == "OpenRouter key *** should never echo"


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
