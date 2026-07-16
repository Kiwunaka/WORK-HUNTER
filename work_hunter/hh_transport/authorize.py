from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Sequence

from .browser_session import HHBrowserSession


_PROFILE_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}", re.ASCII)


class HHBrowserAuthorizer:
    """Browser-assisted auth. Password, OTP and CAPTCHA remain user actions."""

    def __init__(
        self,
        browser: Any,
        *,
        private_root: str | Path = Path(".work-hunter") / "private",
        session_factory: Callable[[str], HHBrowserSession] | None = None,
        authentication_timeout_ms: int = 300_000,
    ) -> None:
        self.browser = browser
        self.private_root = Path(private_root)
        self.authentication_timeout_ms = int(authentication_timeout_ms)
        self._session_factory = session_factory

    def profile_dir(self, profile_id: str) -> Path:
        profile = _valid_profile_id(profile_id)
        return self.private_root / "hh-browser" / profile

    def session(self, profile_id: str) -> HHBrowserSession:
        profile = _valid_profile_id(profile_id)
        if self._session_factory is not None:
            return self._session_factory(profile)
        return HHBrowserSession(
            cookie_path=self.private_root / "hh-sessions" / f"{profile}.json"
        )

    def login(
        self,
        profile_id: str,
        *,
        login_url: str = "https://hh.ru/account/login",
    ) -> dict[str, Any]:
        profile = _valid_profile_id(profile_id)
        profile_dir = self.profile_dir(profile)
        profile_dir.mkdir(parents=True, exist_ok=True)
        with self.browser.launch_persistent_context(
            str(profile_dir),
            headless=False,
        ) as context:
            page = context.new_page()
            page.goto(login_url, wait_until="domcontentloaded")
            self.wait_until_authenticated(page, context)
            session = self.session(profile)
            session.update_from_playwright_context(context.cookies(), page.content())
        return self.diagnostics(profile)

    def wait_until_authenticated(self, page: Any, context: Any) -> None:
        page.wait_for_function(
            """() => !location.pathname.includes('/account/login') &&
               (!!document.querySelector('[data-qa="mainmenu_applicantProfile"]') ||
                !!document.querySelector('[data-qa="mainmenu_logout"]'))""",
            timeout=self.authentication_timeout_ms,
        )
        if not any(_is_hh_cookie(cookie) for cookie in context.cookies()):
            raise RuntimeError("manual_auth")

    def import_cookies(
        self,
        profile_id: str,
        cookies: str | Sequence[dict[str, Any]],
    ) -> dict[str, Any]:
        session = self.session(profile_id)
        if isinstance(cookies, str):
            payload = json.loads(cookies)
        else:
            payload = list(cookies)
        if isinstance(payload, dict):
            payload = payload.get("cookies")
        if not isinstance(payload, list):
            raise ValueError("cookie export must contain a list")
        session.load_from_json(json.dumps(payload, ensure_ascii=False))
        session.save()
        return self.diagnostics(profile_id)

    def logout(self, profile_id: str, *, confirmation: str) -> dict[str, Any]:
        profile = _valid_profile_id(profile_id)
        if confirmation != f"LOGOUT {profile}":
            raise PermissionError(f'literal confirmation "LOGOUT {profile}" is required')
        session = self.session(profile)
        session.clear()
        return self.diagnostics(profile)

    def diagnostics(self, profile_id: str) -> dict[str, Any]:
        profile = _valid_profile_id(profile_id)
        session = self.session(profile)
        session.load()
        result = session.diagnostics()
        result.update(
            {
                "profile_id": profile,
                "status": "authenticated" if result["authenticated"] else "manual_auth",
                "credentials_embedded": False,
            }
        )
        return result


def _valid_profile_id(value: str) -> str:
    profile = str(value or "").strip().casefold()
    if _PROFILE_ID.fullmatch(profile) is None:
        raise ValueError("profile_id must be a simple local identifier")
    return profile


def _is_hh_cookie(cookie: dict[str, Any]) -> bool:
    domain = str(cookie.get("domain") or "").casefold().lstrip(".")
    return domain == "hh.ru" or domain.endswith(".hh.ru")


__all__ = ["HHBrowserAuthorizer"]
