from __future__ import annotations

import copy
import errno
import importlib
import json
import os
import shutil
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from .hh_autopilot.config import default_autopilot_config
from .sources.public_boards import PUBLIC_BOARD_SOURCE_NAMES, public_board_default_config


CONFIG_FILENAME = "work_hunter_config.json"
DATA_DIRNAME = ".work-hunter"
CURRENT_UI_ONBOARDING_VERSION = 2
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
_MISSING_CONFIG_VALUE = object()
_CONFIG_WRITE_LOCK = threading.RLock()
_CONFIG_LOCK_STATE = threading.local()
_CONFIG_LOCK_FDS: set[int] = set()
AUTOPILOT_MANAGED_ACCOUNT_KEYS = frozenset(
    {"enabled", "authorization_generation"}
)


def _reset_config_state_after_fork() -> None:
    global _CONFIG_WRITE_LOCK, _CONFIG_LOCK_STATE, _CONFIG_LOCK_FDS
    for fd in sorted(_CONFIG_LOCK_FDS):
        try:
            os.close(fd)
        except OSError:
            pass
    _CONFIG_WRITE_LOCK = threading.RLock()
    _CONFIG_LOCK_STATE = threading.local()
    _CONFIG_LOCK_FDS = set()


_register_at_fork = getattr(os, "register_at_fork", None)
if callable(_register_at_fork):
    _register_at_fork(after_in_child=_reset_config_state_after_fork)


def _default_profile() -> dict[str, Any]:
    return {
        "name": "Кандидат",
        "email": "",
        "phone": "",
        "city": "",
        "linkedin_url": "",
        "portfolio_url": "",
        "resume_path": "",
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
    public_board_sources["indeed"].update({
        "browser_fallback": True,
        "headless": False,
        "timeout_seconds": 45,
        "login_wait_seconds": 45,
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
            "tests": {
                "model": "",
                "temperature": 0.0,
                "max_tokens": 512,
                "max_retries": 2,
                "retry_base_seconds": 1.0,
                "timeout": 60,
                "system_prompt": (
                    "Отвечай на тест вакансии кратко и профессионально. "
                    "Для вопроса с вариантами выбери только один переданный ID."
                ),
                "selection_prompt": (
                    "Вопрос: {question}\nВарианты:\n{options}\n"
                    "Верни только ID лучшего ответа."
                ),
                "text_prompt": "Дай краткий профессиональный ответ: {question}",
                "external_link_answer": (
                    "Готов обсудить задание и предоставить ответ внутри HH."
                ),
            },
            "forms": {
                "model": "",
                "temperature": 0.0,
                "max_tokens": 512,
                "max_retries": 2,
                "retry_base_seconds": 1.0,
                "timeout": 60,
                "system_prompt": (
                    "Заполняй поле отклика правдиво по данным кандидата и резюме. "
                    "Отвечай только значением поля."
                ),
                "selection_prompt": (
                    "Поле: {question}\nВарианты:\n{options}\n"
                    "Данные кандидата:\n{facts}\nВерни только ID варианта."
                ),
                "text_prompt": (
                    "Поле: {question}\nДанные кандидата:\n{facts}\n"
                    "Верни только краткий ответ для поля."
                ),
            },
            "cover_letters": {
                "model": "",
                "temperature": 0.4,
                "max_tokens": 700,
                "timeout": 60,
                "failure_policy": "template",
                "system_prompt": (
                    "Ты пишешь короткие сопроводительные письма от первого "
                    "лица. Используй только переданные факты, не выдумывай "
                    "опыт, цифры, контакты и условия. Верни только текст письма."
                ),
                "message_prompt": (
                    "Вакансия: {vacancy_name}\n"
                    "Компания: {employer_name}\n"
                    "Описание: {vacancy_description}\n"
                    "Резюме: {resume_title}\n"
                    "Профиль кандидата: {candidate_profile}\n"
                    "Опыт и факты: {candidate_about}\n\n"
                    "Напиши персональное письмо на 4-6 предложений."
                ),
            },
            "replies": {
                "model": "",
                "temperature": 0.2,
                "max_tokens": 350,
                "timeout": 60,
                "system_prompt": (
                    "Ты — соискатель на HeadHunter. Отвечай от первого лица, "
                    "вежливо, кратко и только по фактам из профиля кандидата. "
                    "Не выдумывай опыт, условия и контакты."
                ),
                "message_prompt": (
                    "Вакансия: {vacancy_name}\n"
                    "Работодатель: {employer_name}\n"
                    "Резюме: {resume_title}\n"
                    "Профиль кандидата: {candidate_profile}\n"
                    "Дополнительные факты: {candidate_about}\n"
                    "История переписки:\n{history}\n\n"
                    "Напиши только готовый короткий ответ работодателю."
                ),
            },
            "captcha": {
                "backend": "direct",
                "model": "",
                "temperature": 0.0,
                "max_tokens": 20,
                "max_retries": 3,
                "retry_base_seconds": 1.0,
                "timeout": 60,
                "system_prompt": (
                    "Распознай текст на изображении. Верни только текст "
                    "без объяснений и дополнительных символов."
                ),
                "prompt": "Распознай текст CAPTCHA и верни только результат.",
            },
        },
        "research": {
            "max_results": 200,
        },
        "external_apply": {
            "enabled": True,
            "transport": "browser",
            "headless": False,
            "browser": "chromium",
            "timeout_seconds": 45,
            "login_wait_seconds": 180,
            "max_steps": 12,
            "slow_mo_ms": 100,
            "answer_with_ai": True,
            "answers": {},
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
                    "Chrome/151.0.0.0 Safari/537.36"
                ),
                "chatik": {
                    "base_url": "https://chatik.hh.ru",
                    "max_age_hours": 72,
                    "max_pages": 10,
                    "history_limit": 20,
                    "message_limit": 20,
                    "send_delay_min_seconds": 1.0,
                    "send_delay_max_seconds": 3.0,
                    "leave_discarded": True,
                },
                "hh_applicant_tool_command": "hh-applicant-tool",
                "hh_applicant_tool_config_dir": "",
                "allow_broad_apply": False,
                "autopilot": default_autopilot_config(),
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
            "linkedin": {
                "enabled": True,
                "pages": 1,
                "page_size": 25,
                "locations": ["Russia", "Remote"],
                "geo_id": "101728296",
                "date_posted_seconds": 604800,
                "remote_only": False,
                "apply_adapter": {
                    "transport": "browser",
                    "kind": "linkedin_easy_apply",
                    "answers": {},
                },
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
            "onboarding_version": 0,
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


def _list_item_identity(
    item: Any,
) -> tuple[tuple[str, str | int], ...] | None:
    if not isinstance(item, dict):
        return None

    keys: dict[str, list[Any]] = {}
    for key, value in item.items():
        if not isinstance(key, str) or is_sensitive_key(key):
            continue
        keys.setdefault(key.casefold(), []).append(value)

    def identity_for(identity_keys: list[str]) -> tuple[tuple[str, str | int], ...] | None:
        if not identity_keys:
            return None
        identity: list[tuple[str, str | int]] = []
        for key in identity_keys:
            values = keys[key]
            if len(values) != 1:
                return None
            value = values[0]
            if isinstance(value, bool) or not isinstance(value, (str, int)):
                return None
            if isinstance(value, str) and not value:
                return None
            identity.append((key, value))
        return tuple(identity)

    for exact_key in ("id", "uid", "uuid"):
        if exact_key in keys:
            return identity_for([exact_key])

    id_keys = sorted(key for key in keys if key.endswith("_id"))
    if id_keys:
        return identity_for(id_keys)
    return None


def _merge_masked_list(stored: Any, submitted: list[Any]) -> list[Any]:
    stored_list = stored if isinstance(stored, list) else []
    requires_identity = _contains_sensitive_key(stored_list) or _contains_sensitive_key(
        submitted
    )
    if not requires_identity:
        return [
            _merge_masked_value(
                stored_list[index]
                if index < len(stored_list)
                else _MISSING_CONFIG_VALUE,
                value,
            )
            for index, value in enumerate(submitted)
        ]

    stored_by_identity: dict[
        tuple[tuple[str, str | int], ...],
        list[Any],
    ] = {}
    for item in stored_list:
        identity = _list_item_identity(item)
        if identity is not None:
            stored_by_identity.setdefault(identity, []).append(item)

    submitted_identity_counts: dict[tuple[tuple[str, str | int], ...], int] = {}
    for item in submitted:
        identity = _list_item_identity(item)
        if identity is not None:
            submitted_identity_counts[identity] = (
                submitted_identity_counts.get(identity, 0) + 1
            )

    merged: list[Any] = []
    for item in submitted:
        identity = _list_item_identity(item)
        stored_matches = stored_by_identity.get(identity, []) if identity else []
        if (
            identity is not None
            and len(stored_matches) == 1
            and submitted_identity_counts[identity] == 1
        ):
            current = stored_matches[0]
        else:
            current = _MISSING_CONFIG_VALUE
        merged.append(_merge_masked_value(current, item))
    return merged


def _merge_masked_value(
    stored: Any,
    submitted: Any,
    *,
    sensitive_key: bool = False,
) -> Any:
    if sensitive_key:
        if not isinstance(submitted, str) or submitted == "" or submitted == MASK:
            if stored is _MISSING_CONFIG_VALUE:
                return _MISSING_CONFIG_VALUE
            return copy.deepcopy(stored)
        return copy.deepcopy(submitted)

    stored_has_secrets = _contains_sensitive_key(stored)
    same_container_type = (
        isinstance(stored, dict)
        and isinstance(submitted, dict)
        or isinstance(stored, list)
        and isinstance(submitted, list)
    )
    if stored_has_secrets and not same_container_type:
        return copy.deepcopy(stored)

    if isinstance(submitted, dict):
        stored_dict = stored if isinstance(stored, dict) else {}
        merged = copy.deepcopy(stored_dict)
        for key, value in submitted.items():
            current = stored_dict.get(key, _MISSING_CONFIG_VALUE)
            updated = _merge_masked_value(
                current,
                value,
                sensitive_key=is_sensitive_key(key),
            )
            if updated is _MISSING_CONFIG_VALUE:
                merged.pop(key, None)
            else:
                merged[key] = updated
        return merged

    if isinstance(submitted, list):
        return _merge_masked_list(stored, submitted)

    return copy.deepcopy(submitted)


def merge_masked_config(
    stored: dict[str, Any],
    submitted: dict[str, Any],
) -> dict[str, Any]:
    submitted_accounts = _autopilot_accounts(submitted)
    generations: dict[str, Any] = {}
    for item in _autopilot_accounts(stored) or []:
        if not isinstance(item, dict):
            continue
        profile_id = item.get("profile_id")
        if isinstance(profile_id, str) and profile_id.strip():
            generations[profile_id.strip().casefold()] = copy.deepcopy(
                item.get("authorization_generation")
            )
    if submitted_accounts is not None:
        for item in submitted_accounts:
            if isinstance(item, dict) and "authorization_generation" in item:
                profile_id = item.get("profile_id")
                key = (
                    profile_id.strip().casefold()
                    if isinstance(profile_id, str)
                    else ""
                )
                submitted_generation = item.get("authorization_generation")
                stored_generation = generations.get(key, _MISSING_CONFIG_VALUE)
                unchanged = (
                    stored_generation is not _MISSING_CONFIG_VALUE
                    and _config_values_equal(stored_generation, submitted_generation)
                )
                if not unchanged:
                    raise ValueError(
                        "HH autopilot authorization_generation is service-managed"
                    )

    merged = _merge_masked_value(stored, submitted)
    return preserve_managed_autopilot_fields(stored, merged)


def _autopilot_accounts(config: Any) -> list[Any] | None:
    if not isinstance(config, dict):
        return None
    sources = config.get("sources")
    if not isinstance(sources, dict):
        return None
    hh = sources.get("hh")
    if not isinstance(hh, dict):
        return None
    autopilot = hh.get("autopilot")
    if not isinstance(autopilot, dict):
        return None
    accounts = autopilot.get("accounts")
    return accounts if isinstance(accounts, list) else None


def _canonical_profile_id(value: Any, *, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    canonical = value.strip().casefold()
    if not canonical:
        raise ValueError(f"{field} must not be empty")
    if "\0" in canonical:
        raise ValueError(f"{field} must not contain NUL")
    return canonical


def _account_index(config: Any, *, field: str) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(_autopilot_accounts(config) or []):
        if not isinstance(item, dict):
            continue
        profile_id = _canonical_profile_id(
            item.get("profile_id"), field=f"{field}[{index}].profile_id"
        )
        if profile_id in indexed:
            raise ValueError(f"{field} contains a duplicate profile_id")
        indexed[profile_id] = item
    return indexed


def preserve_managed_autopilot_fields(
    stored: dict[str, Any], submitted: dict[str, Any]
) -> dict[str, Any]:
    if not isinstance(stored, dict) or not isinstance(submitted, dict):
        raise TypeError("stored and submitted configuration must be dictionaries")
    merged = copy.deepcopy(submitted)
    stored_accounts = _account_index(stored, field="stored autopilot accounts")
    submitted_accounts = _account_index(
        merged, field="submitted autopilot accounts"
    )
    for profile_id, item in submitted_accounts.items():
        old = stored_accounts.get(profile_id)
        if old is None:
            item["enabled"] = False
            item["authorization_generation"] = None
            continue
        item["enabled"] = copy.deepcopy(old.get("enabled", False))
        item["authorization_generation"] = copy.deepcopy(
            old.get("authorization_generation")
        )
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


def _migrate_ui_config_document(raw: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    migrated = copy.deepcopy(raw)
    changed = False
    profiles = migrated.get("profiles")
    if not isinstance(profiles, dict):
        profiles = {}
        migrated["profiles"] = profiles
        changed = True

    flat_profile = migrated.get("profile")
    if isinstance(flat_profile, dict):
        if "default" not in profiles or profiles.get("default") == flat_profile:
            target = "default"
        else:
            suffix = 1
            target = "legacy"
            while target in profiles:
                suffix += 1
                target = f"legacy-{suffix}"
        profiles[target] = copy.deepcopy(flat_profile)
        migrated["profile"] = target
        changed = True

    ui = migrated.get("ui")
    if not isinstance(ui, dict):
        ui = {}
        migrated["ui"] = ui
        changed = True
    if "onboarding_version" not in ui:
        ui["onboarding_version"] = CURRENT_UI_ONBOARDING_VERSION
        changed = True
    return migrated, changed


def load_config(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        return default_config()
    with path.open("r", encoding="utf-8") as fh:
        loaded = json.load(fh)
    migrated, changed = _migrate_ui_config_document(loaded)
    config = _deep_merge(default_config(), migrated)
    if changed:
        save_config(path, config)
    return config


@contextmanager
def locked_current_config_snapshot(
    path: str | Path,
) -> Iterator[dict[str, Any] | None]:
    """Yield the current disk snapshot while excluding cooperating writers.

    A missing file is represented by ``None``. No in-memory fallback is used,
    so service-owned authorization fields cannot be projected from stale state.
    The process and cross-process locks remain held until the caller exits.
    """
    path = Path(path)
    owner_pid = os.getpid()
    with _CONFIG_WRITE_LOCK:
        with _config_file_lock(path):
            snapshot = load_config(path) if path.exists() else None
            _assert_config_transaction_owner(owner_pid)
            yield None if snapshot is None else copy.deepcopy(snapshot)
            _assert_config_transaction_owner(owner_pid)


def merge_config_snapshot_changes(
    baseline: dict[str, Any],
    desired: dict[str, Any],
    fresh: dict[str, Any],
) -> dict[str, Any]:
    """Apply baseline-to-desired changes onto a fresh config snapshot.

    Unchanged values come from ``fresh``. Changed scalar values and lists replace
    the fresh value, mappings merge recursively, and keys removed from ``desired``
    are removed from the result.
    """
    merged = _merge_config_value(baseline, desired, fresh)
    if not isinstance(merged, dict):
        raise TypeError("Configuration root must be a mapping")
    return merged


def _merge_config_value(baseline: Any, desired: Any, fresh: Any) -> Any:
    if _config_values_equal(baseline, desired):
        if fresh is _MISSING_CONFIG_VALUE:
            return _MISSING_CONFIG_VALUE
        return copy.deepcopy(fresh)
    if isinstance(baseline, dict) and isinstance(desired, dict):
        if not isinstance(fresh, dict):
            return copy.deepcopy(desired)
        merged = copy.deepcopy(fresh)
        keys = dict.fromkeys((*baseline.keys(), *desired.keys()))
        for key in keys:
            baseline_value = baseline.get(key, _MISSING_CONFIG_VALUE)
            desired_value = desired.get(key, _MISSING_CONFIG_VALUE)
            if desired_value is _MISSING_CONFIG_VALUE:
                merged.pop(key, None)
                continue
            if baseline_value is _MISSING_CONFIG_VALUE:
                merged[key] = copy.deepcopy(desired_value)
                continue
            fresh_value = fresh.get(key, _MISSING_CONFIG_VALUE)
            merged_value = _merge_config_value(
                baseline_value,
                desired_value,
                fresh_value,
            )
            if merged_value is _MISSING_CONFIG_VALUE:
                merged.pop(key, None)
            else:
                merged[key] = merged_value
        return merged
    return copy.deepcopy(desired)


def _config_values_equal(baseline: Any, desired: Any) -> bool:
    if type(baseline) is not type(desired):
        return False
    if isinstance(baseline, dict):
        if baseline.keys() != desired.keys():
            return False
        return all(
            _config_values_equal(baseline[key], desired[key])
            for key in baseline
        )
    if isinstance(baseline, list):
        return len(baseline) == len(desired) and all(
            _config_values_equal(baseline_value, desired_value)
            for baseline_value, desired_value in zip(baseline, desired)
        )
    return bool(baseline == desired)


def save_config(path: str | Path, config: dict[str, Any]) -> dict[str, Any]:
    path = Path(path)
    with _CONFIG_WRITE_LOCK:
        with _config_file_lock(path):
            try:
                with path.open("r", encoding="utf-8") as fh:
                    loaded = json.load(fh)
            except FileNotFoundError:
                stored: dict[str, Any] = {}
            else:
                if not isinstance(loaded, dict):
                    raise ValueError("configuration root must be an object")
                stored = loaded
            protected = preserve_managed_autopilot_fields(stored, config)
            _save_config(path, protected)
            return copy.deepcopy(protected)


def _update_autopilot_authorization_projection(
    path: str | Path,
    fallback: dict[str, Any],
    projections: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Update service-owned HH autopilot fields under the config write lock."""
    if not isinstance(fallback, dict):
        raise TypeError("fallback configuration must be a dictionary")
    if not isinstance(projections, dict) or not projections:
        raise ValueError("projections must be a non-empty dictionary")
    validated: dict[str, dict[str, Any]] = {}
    allowed = AUTOPILOT_MANAGED_ACCOUNT_KEYS | {"paused"}
    for raw_account_id, raw_values in projections.items():
        account_id = _canonical_profile_id(
            raw_account_id, field="projection account_id"
        )
        if account_id in validated:
            raise ValueError("projections contains a duplicate account identifier")
        if not isinstance(raw_values, dict) or not raw_values:
            raise TypeError("projection values must be a non-empty dictionary")
        unknown = set(raw_values).difference(allowed)
        if unknown:
            raise ValueError(
                f"unsupported autopilot projection field: {sorted(unknown)[0]}"
            )
        values = copy.deepcopy(raw_values)
        if "enabled" in values and type(values["enabled"]) is not bool:
            raise TypeError("projection enabled must be a boolean")
        if "paused" in values and type(values["paused"]) is not bool:
            raise TypeError("projection paused must be a boolean")
        if "authorization_generation" in values:
            generation = values["authorization_generation"]
            if generation is not None and (
                isinstance(generation, bool)
                or not isinstance(generation, int)
                or generation < 1
            ):
                raise ValueError(
                    "projection authorization_generation must be positive or null"
                )
        validated[account_id] = values

    path = Path(path)
    owner_pid = os.getpid()
    with _CONFIG_WRITE_LOCK:
        with _config_file_lock(path):
            if path.exists():
                current = load_config(path)
            else:
                current = preserve_managed_autopilot_fields({}, fallback)
            accounts = _account_index(
                current, field="persisted autopilot accounts"
            )
            missing = sorted(set(validated).difference(accounts))
            if missing:
                raise ValueError(
                    f"unknown HH autopilot projection account: {missing[0]}"
                )
            for account_id, values in validated.items():
                accounts[account_id].update(copy.deepcopy(values))
            _assert_config_transaction_owner(owner_pid)
            _save_config(path, current)
            return copy.deepcopy(current)


def _save_config(path: str | Path, config: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        # Existing files retain portable mode bits. New files keep mkstemp's
        # restrictive platform default; ACL and ownership follow os.replace.
        if path.exists():
            try:
                shutil.copymode(path, temporary, follow_symlinks=False)
            except (NotImplementedError, OSError):
                pass
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(config, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _fsync_directory(directory: str | Path) -> None:
    """Best-effort durability for the replaced directory entry."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        directory_fd = os.open(directory, flags)
    except (NotImplementedError, OSError):
        return
    try:
        try:
            os.fsync(directory_fd)
        except (NotImplementedError, OSError):
            pass
    finally:
        os.close(directory_fd)


def _config_lock_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.lock")


def _lock_config_fd(fd: int) -> None:
    os.lseek(fd, 0, os.SEEK_SET)
    if os.name == "nt":
        _lock_windows_config_fd(fd)
        return
    fcntl = importlib.import_module("fcntl")
    fcntl.flock(fd, fcntl.LOCK_EX)


def _lock_windows_config_fd(fd: int) -> None:
    msvcrt = importlib.import_module("msvcrt")
    contention_errnos = {
        errno.EACCES,
        errno.EAGAIN,
        getattr(errno, "EDEADLK", errno.EACCES),
        getattr(errno, "EDEADLOCK", errno.EACCES),
    }
    while True:
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return
        except OSError as exc:
            if exc.errno not in contention_errnos:
                raise
            time.sleep(0.05)


def _unlock_config_fd(fd: int) -> None:
    os.lseek(fd, 0, os.SEEK_SET)
    if os.name == "nt":
        msvcrt = importlib.import_module("msvcrt")
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        return
    fcntl = importlib.import_module("fcntl")
    fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def _config_file_lock(path: Path) -> Iterator[None]:
    lock_path = _config_lock_path(path.resolve(strict=False))
    lock_key = os.path.normcase(str(lock_path))
    process_id = os.getpid()
    if getattr(_CONFIG_LOCK_STATE, "process_id", None) != process_id:
        _CONFIG_LOCK_STATE.process_id = process_id
        _CONFIG_LOCK_STATE.depths = {}
    depths: dict[str, int] = _CONFIG_LOCK_STATE.depths
    depth = depths.get(lock_key, 0)
    if depth:
        depths[lock_key] = depth + 1
        try:
            yield
        finally:
            depths[lock_key] -= 1
        return

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    _CONFIG_LOCK_FDS.add(fd)
    locked = False
    try:
        if os.fstat(fd).st_size == 0:
            os.write(fd, b"\0")
        _lock_config_fd(fd)
        locked = True
        depths[lock_key] = 1
        yield
    finally:
        if os.getpid() == process_id:
            try:
                depths.pop(lock_key, None)
                if locked:
                    _unlock_config_fd(fd)
            finally:
                _CONFIG_LOCK_FDS.discard(fd)
                os.close(fd)


def update_config(
    path: str | Path,
    update: Callable[[dict[str, Any]], None],
    *,
    fallback: dict[str, Any] | None = None,
) -> dict[str, Any]:
    path = Path(path)
    owner_pid = os.getpid()
    with _CONFIG_WRITE_LOCK:
        with _config_file_lock(path):
            existed = path.exists()
            if existed:
                config = load_config(path)
            else:
                config = copy.deepcopy(fallback or default_config())
            stored = copy.deepcopy(config) if existed else {}
            update(config)
            config = preserve_managed_autopilot_fields(stored, config)
            _assert_config_transaction_owner(owner_pid)
            _save_config(path, config)
            return config


def _assert_config_transaction_owner(owner_pid: int) -> None:
    if os.getpid() != owner_pid:
        raise RuntimeError(
            "Config transaction inherited across fork must be restarted"
        )


def reconcile_config_snapshot(
    path: str | Path,
    baseline: dict[str, Any],
    desired: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    path = Path(path)
    with _CONFIG_WRITE_LOCK:
        with _config_file_lock(path):
            if path.exists():
                fresh = load_config(path)
            else:
                fresh = copy.deepcopy(baseline)
            reconciled = merge_config_snapshot_changes(baseline, desired, fresh)
            return fresh, reconciled


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
