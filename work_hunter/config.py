from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from .sources.public_boards import PUBLIC_BOARD_SOURCE_NAMES, public_board_default_config


CONFIG_FILENAME = "work_hunter_config.json"
DATA_DIRNAME = ".work-hunter"
MASK = "***"
SENSITIVE_EXACT = {"token", "password", "secret", "api_key", "key"}
SENSITIVE_SUFFIXES = ("_token", "_password", "_secret", "_api_key", "_key")
CONFIG_SECRET_CLEAR_PATHS = frozenset(
    {
        ("ai", "api_key"),
        ("hh_agent", "telegram", "bot_token"),
        ("sources", "hh", "access_token"),
        ("sources", "hh", "refresh_token"),
        ("sources", "hh", "client_secret"),
    }
)
HH_ACCOUNT_SECRET_KEYS = frozenset(
    {"access_token", "refresh_token", "client_secret"}
)


def _default_profile() -> dict[str, Any]:
    return {
        "name": "Кандидат",
        "title": "",
        "queries": ["python backend", "fullstack python", "fastapi"],
        "desired_roles": ["backend", "python", "developer"],
        "must_have_skills": ["python"],
        "nice_to_have_skills": [
            "fastapi",
            "django",
            "postgresql",
            "docker",
            "linux",
            "api",
        ],
        "stop_words": [
            "bitrix",
            "стажер",
            "стажёр",
            "junior",
            "без опыта",
            "1с",
        ],
        "salary_min": 0,
        "locations": ["remote", "Москва"],
        "remote_only": False,
    }


def default_config() -> dict[str, Any]:
    public_board_sources = {
        name: public_board_default_config(name)
        for name in PUBLIC_BOARD_SOURCE_NAMES
    }
    public_board_sources["getmatch"].update({
        "per_page": 50,
        "fetch_details": True,
    })
    public_board_sources["relocate_me"].update({
        "fetch_details": True,
    })
    return {
        "profile": "default",
        "profiles": {
            "default": _default_profile(),
        },
        "hh_account_profile": "default",
        "hh_account_profiles": {
            "default": {},
        },
        "about": {
            "summary": "",
            "experience": [],
            "all_skills": [],
        },
        "ai": {
            "backend": "direct",
            "provider": "",
            "api_key": "",
            "base_url": "",
            "model": "",
            "temperature": 0.7,
            "max_tokens": 1500,
            "opencode_transport": "cli",
            "opencode_command": "opencode",
            "opencode_model": "",
            "opencode_agent": "work-hunter-ai",
            "opencode_server_url": "http://127.0.0.1:4096",
            "opencode_timeout": 300,
        },
        "research": {
            "max_results": 200,
        },
        "hh_agent": {
            "paused": False,
            "telegram": {
                "enabled": False,
                "bot_token": "",
                "allowed_user_ids": [],
                "cockpit_base_url": "http://127.0.0.1:8787",
                "oauth_start_path": "/hh/oauth/start",
            },
        },
        "sources": {
            "hh": {
                "enabled": True,
                "area": 113,
                "per_page": 25,
                "pages": 1,
                "access_token": "",
                "refresh_token": "",
                "access_expires_at": "",
                "client_id": "",
                "client_secret": "",
                "hh_user_agent": "kiwun-work-hunter/0.1 (kiwun@users.noreply.github.com)",
                "android_user_agent": "",
                "hh_cookie_file": "",
                "challenge_mode": "manual",
                "form_mode": "manual",
                "web_fallback": True,
                "web_user_agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36"
                ),
                "hh_applicant_tool_command": "hh-applicant-tool",
                "hh_applicant_tool_config_dir": "",
                "allow_broad_apply": False,
            },
            "habr": {
                "enabled": True,
                "per_page": 25,
            },
            "geekjob": {
                "enabled": True,
                "pages": 1,
                "fallback_unfiltered": True,
            },
            "telegram": {
                "enabled": True,
                "channels": [
                    "myjob_it",
                    "job_channel_it",
                    "rabota_it",
                    "dev_jobs",
                    "python_jobs_ru",
                    "remote_job_search",
                    "forpython",
                    "fordevops",
                    "forfrontend",
                    "fordataengineer",
                ],
            },
            **public_board_sources,
        },
        "ui": {
            "host": "127.0.0.1",
            "port": 8787,
        },
    }


def data_dir(root: str | Path | None = None) -> Path:
    base = Path(root) if root is not None else Path.cwd()
    return base / DATA_DIRNAME


def config_path(root: str | Path | None = None) -> Path:
    return data_dir(root) / CONFIG_FILENAME


def database_path(root: str | Path | None = None) -> Path:
    return data_dir(root) / "work_hunter.sqlite3"


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return lowered in SENSITIVE_EXACT or any(
        lowered.endswith(suffix) for suffix in SENSITIVE_SUFFIXES
    )


def _contains_sensitive_key(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            is_sensitive_key(key) or _contains_sensitive_key(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_sensitive_key(item) for item in value)
    return False


def merge_masked_config(
    stored: dict[str, Any],
    submitted: dict[str, Any],
) -> dict[str, Any]:
    merged = copy.deepcopy(stored)
    for key, value in submitted.items():
        current = merged.get(key)
        if is_sensitive_key(key):
            if not isinstance(value, str) or value == "" or value == MASK:
                continue
        if _contains_sensitive_key(current) and not (
            isinstance(current, dict) and isinstance(value, dict)
        ):
            continue
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = merge_masked_config(current, value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def clear_config_secret_value(
    config: dict[str, Any],
    path: str,
) -> dict[str, Any]:
    parts = tuple(path.split("."))
    clear_parts = parts if parts in CONFIG_SECRET_CLEAR_PATHS else ()
    account_prefix = "hh_account_profiles."
    if not clear_parts and path.startswith(account_prefix):
        account_name, separator, secret_key = path[len(account_prefix) :].rpartition(
            "."
        )
        accounts = config.get("hh_account_profiles")
        if (
            separator
            and account_name
            and isinstance(accounts, dict)
            and account_name in accounts
            and isinstance(accounts[account_name], dict)
            and secret_key in HH_ACCOUNT_SECRET_KEYS
        ):
            clear_parts = ("hh_account_profiles", account_name, secret_key)
    if not clear_parts:
        raise ValueError(f"Configuration secret path is not allowed: {path}")

    cleared = copy.deepcopy(config)
    parent: dict[str, Any] = cleared
    for key in clear_parts[:-1]:
        child = parent.get(key)
        if not isinstance(child, dict):
            raise ValueError(f"Configuration secret path is not allowed: {path}")
        parent = child
    parent[clear_parts[-1]] = ""
    return cleared


def load_config(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        return default_config()
    with path.open("r", encoding="utf-8") as fh:
        loaded = json.load(fh)
    return _deep_merge(default_config(), loaded)


def save_config(path: str | Path, config: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(config, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def active_profile(config: dict[str, Any]) -> dict[str, Any]:
    """Return the currently selected profile dict."""
    profile_id = config.get("profile", "default")
    profiles = config.get("profiles") or {}

    # New multi-profile format
    if profiles and isinstance(profile_id, str) and profile_id in profiles:
        return profiles[profile_id]

    # Legacy flat format: "profile" is a dict itself
    if isinstance(profile_id, dict):
        return profile_id

    # Legacy: config has a "profile" dict at root level (old format)
    if isinstance(config.get("profile"), dict):
        return config["profile"]

    return _default_profile()


def active_hh_config(config: dict[str, Any]) -> dict[str, Any]:
    base = copy.deepcopy((config.get("sources") or {}).get("hh") or {})
    account_id = str(config.get("hh_account_profile") or "default")
    accounts = config.get("hh_account_profiles") or {}
    account = accounts.get(account_id) or {}
    if isinstance(account, dict):
        base.update({key: value for key, value in account.items() if value is not None})
    return base


def mask_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        masked: dict[str, Any] = {}
        for key, item in value.items():
            if is_sensitive_key(key):
                masked[key] = MASK
            else:
                masked[key] = mask_secrets(item)
        return masked
    if isinstance(value, list):
        return [mask_secrets(item) for item in value]
    return value
