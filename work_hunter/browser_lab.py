from __future__ import annotations

import urllib.parse
from pathlib import Path
from typing import Any

from .api_recon import analyze_har
from .browser.chromium import chromium_open_command
from .browser.profiles import browser_profile_path, browser_screenshots_path
from .config import mask_secrets
from .external_sessions import import_external_session_from_har, show_external_session
from .hh_agent.forms import draft_form_review


DEFAULT_LOGIN_URLS = {
    "hh": "https://hh.ru/account/login",
    "habr": "https://career.habr.com/users/sign_in",
    "geekjob": "https://geekjob.ru/login",
    "getmatch": "https://getmatch.ru/login",
    "hirehi": "https://hirehi.ru/login",
    "careerspace": "https://careerspace.app/login",
    "jabka": "https://jabka.work/login",
}


class PlaywrightDryRunExecutor:
    def execute(
        self,
        *,
        root: str | Path,
        source: str,
        form_url: str,
        actions: list[dict[str, Any]],
        profile_dir: str | Path,
        screenshots_dir: str | Path,
        headless: bool = True,
        timeout_ms: int = 15000,
    ) -> dict[str, Any]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return {
                "status": "unavailable",
                "reason": "playwright_missing",
                "install_command": "python -m playwright install chromium",
                "actions_executed": 0,
            }

        if not form_url:
            return {"status": "blocked", "reason": "missing_form_url", "actions_executed": 0}

        profile = Path(profile_dir)
        screenshots = Path(screenshots_dir)
        profile.mkdir(parents=True, exist_ok=True)
        screenshots.mkdir(parents=True, exist_ok=True)
        before = screenshots / f"{_source_name(source)}-before-fill.png"
        after = screenshots / f"{_source_name(source)}-after-fill.png"
        executed = 0

        try:
            with sync_playwright() as playwright:
                context = playwright.chromium.launch_persistent_context(
                    str(profile),
                    headless=headless,
                    viewport={"width": 1366, "height": 900},
                )
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(form_url, wait_until="domcontentloaded", timeout=timeout_ms)
                page.screenshot(path=str(before), full_page=True)
                for action in actions:
                    if action.get("action") != "fill" or not action.get("selector"):
                        continue
                    page.locator(str(action["selector"])).first.fill(
                        str(action.get("value") or ""),
                        timeout=timeout_ms,
                    )
                    executed += 1
                page.screenshot(path=str(after), full_page=True)
                context.close()
        except Exception as exc:
            return {
                "status": "failed",
                "reason": type(exc).__name__,
                "error": str(exc)[:300],
                "actions_executed": executed,
                "screenshots": {
                    "before_fill": str(before) if before.exists() else "",
                    "after_fill": str(after) if after.exists() else "",
                },
            }

        return {
            "status": "ok",
            "actions_executed": executed,
            "screenshots": {
                "before_fill": str(before),
                "after_fill": str(after),
            },
            "submit": False,
        }


def browser_lab_status(root: str | Path, source: str) -> dict[str, Any]:
    source_name = _source_name(source)
    profile = browser_profile_dir(root, source_name)
    session = show_external_session(root, source_name)
    session_imported = session.get("status") != "missing"
    if session_imported:
        state = "har_session_imported"
    elif profile.exists():
        state = "profile_ready"
    else:
        state = "missing"
    return {
        "source": source_name,
        "session_state": state,
        "profile_dir": str(profile),
        "profile_exists": profile.exists(),
        "session": session,
        "screenshots_dir": str(browser_screenshots_dir(root, source_name)),
    }


def browser_lab_setup(root: str | Path, source: str) -> dict[str, Any]:
    source_name = _source_name(source)
    profile = browser_profile_dir(root, source_name)
    screenshots = browser_screenshots_dir(root, source_name)
    profile.mkdir(parents=True, exist_ok=True)
    screenshots.mkdir(parents=True, exist_ok=True)
    status = browser_lab_status(root, source_name)
    return {
        **status,
        "status": "ready",
        "launched": False,
        "setup_only": True,
    }


def browser_lab_open_login(root: str | Path, source: str, *, login_url: str = "") -> dict[str, Any]:
    source_name = _source_name(source)
    profile = browser_profile_dir(root, source_name)
    screenshots = browser_screenshots_dir(root, source_name)
    profile.mkdir(parents=True, exist_ok=True)
    screenshots.mkdir(parents=True, exist_ok=True)
    url = login_url or DEFAULT_LOGIN_URLS.get(source_name) or f"https://{source_name}/login"
    command = chromium_open_command(profile_dir=profile, url=url)
    return {
        "status": "planned",
        "source": source_name,
        "login_url": url,
        "profile_dir": str(profile),
        "screenshots_dir": str(screenshots),
        "command": command,
        "next_steps": [
            "Login manually in the opened Chromium profile.",
            "Export a HAR from the same logged-in session.",
            "Import the HAR into Browser Session Lab before enabling any apply mapping.",
        ],
    }


def browser_lab_import_har(
    root: str | Path,
    source: str,
    har_path: str | Path,
    *,
    allowed_hosts: set[str],
) -> dict[str, Any]:
    source_name = _source_name(source)
    session = import_external_session_from_har(root, source_name, har_path, allowed_hosts=allowed_hosts)
    recon = analyze_har(har_path, allowed_hosts=allowed_hosts)
    if session.get("status") == "blocked":
        return {
            "status": "blocked",
            "reason": session.get("reason") or "har_import_blocked",
            "source": source_name,
            "session": session,
            "recon": recon,
            "network_recorder": {
                "har_path": str(har_path),
                "allowed_hosts": sorted(allowed_hosts),
                "redacted": True,
            },
        }
    return {
        "status": "imported",
        "source": source_name,
        "session": session,
        "recon": recon,
        "network_recorder": {
            "har_path": str(har_path),
            "allowed_hosts": sorted(allowed_hosts),
            "redacted": True,
        },
    }


def browser_lab_map_form(
    form: dict[str, Any],
    *,
    source: str,
    persona: dict[str, Any] | None = None,
    resume: dict[str, Any] | None = None,
    vacancy: dict[str, Any] | None = None,
    extra_answers: dict[str, str] | None = None,
) -> dict[str, Any]:
    review = draft_form_review(
        form,
        persona=persona or {},
        resume=resume or {},
        vacancy=vacancy or {},
        extra_answers=extra_answers or {},
    )
    mapped = review.to_dict()
    mapped.update(
        {
            "source": _source_name(source),
            "status": "needs_manual_review" if review.unknown_fields else "mapped",
            "submit": False,
        }
    )
    return mask_secrets(mapped)


def browser_lab_dry_run_form_fill(
    root: str | Path,
    form: dict[str, Any],
    *,
    source: str,
    persona: dict[str, Any] | None = None,
    resume: dict[str, Any] | None = None,
    vacancy: dict[str, Any] | None = None,
    extra_answers: dict[str, str] | None = None,
) -> dict[str, Any]:
    mapped = browser_lab_map_form(
        form,
        source=source,
        persona=persona,
        resume=resume,
        vacancy=vacancy,
        extra_answers=extra_answers,
    )
    actions = [
        {
            "action": "fill",
            "field": name,
            "selector": _name_selector(name),
            "value": value,
        }
        for name, value in (mapped.get("answers") or {}).items()
    ]
    risk_flags = list(mapped.get("risk_flags") or [])
    if mapped.get("unknown_fields") and "unknown_form_fields" not in risk_flags:
        risk_flags.append("unknown_form_fields")
    return mask_secrets(
        {
            "status": "blocked_manual_review" if mapped.get("unknown_fields") else "dry_run_ready",
            "source": _source_name(source),
            "form_url": mapped.get("form_url") or form.get("form_url") or "",
            "submit": False,
            "actions": actions,
            "unknown_fields": mapped.get("unknown_fields") or [],
            "risk_flags": risk_flags,
            "screenshots": {
                "directory": str(browser_screenshots_dir(root, _source_name(source))),
                "planned": ["before_fill", "after_fill"],
            },
        }
    )


def browser_lab_execute_dry_run_form_fill(
    root: str | Path,
    form: dict[str, Any],
    *,
    source: str,
    persona: dict[str, Any] | None = None,
    resume: dict[str, Any] | None = None,
    vacancy: dict[str, Any] | None = None,
    extra_answers: dict[str, str] | None = None,
    executor: Any | None = None,
    headless: bool = True,
    timeout_ms: int = 15000,
) -> dict[str, Any]:
    dry_run = browser_lab_dry_run_form_fill(
        root,
        form,
        source=source,
        persona=persona,
        resume=resume,
        vacancy=vacancy,
        extra_answers=extra_answers,
    )
    if dry_run.get("status") != "dry_run_ready":
        return mask_secrets(
            {
                **dry_run,
                "executor": {
                    "status": "skipped",
                    "reason": str(dry_run.get("status") or "not_ready"),
                    "actions_executed": 0,
                },
            }
        )

    source_name = _source_name(source)
    runner = executor or PlaywrightDryRunExecutor()
    try:
        executed = runner.execute(
            root=root,
            source=source_name,
            form_url=str(dry_run.get("form_url") or ""),
            actions=list(dry_run.get("actions") or []),
            profile_dir=browser_profile_dir(root, source_name),
            screenshots_dir=browser_screenshots_dir(root, source_name),
            headless=headless,
            timeout_ms=timeout_ms,
        )
    except Exception as exc:
        executed = {
            "status": "failed",
            "reason": type(exc).__name__,
            "error": str(exc)[:300],
            "actions_executed": 0,
        }

    executor_status = str(executed.get("status") or "")
    if executor_status == "ok":
        status = "executed_dry_run"
    elif executor_status == "unavailable":
        status = "executor_unavailable"
    else:
        status = "executor_failed"

    return mask_secrets(
        {
            **dry_run,
            "status": status,
            "submit": False,
            "executor": executed,
        }
    )


def browser_profile_dir(root: str | Path, source: str) -> Path:
    return browser_profile_path(root, source)


def browser_screenshots_dir(root: str | Path, source: str) -> Path:
    return browser_screenshots_path(root, source)


def _source_name(value: str) -> str:
    return str(value or "").strip().lower().replace("-", "_")


def _name_selector(name: str) -> str:
    escaped = str(name).replace("\\", "\\\\").replace("'", "\\'")
    return f"[name='{escaped}']"
