from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .backends import CookieBackend, JsonCookieBackend


XSRF_PATTERNS = [
    re.compile(r'name=["\']_xsrf["\']\s+value=["\'](?P<token>[^"\']+)["\']', re.I),
    re.compile(r'value=["\'](?P<token>[^"\']+)["\']\s+name=["\']_xsrf["\']', re.I),
    re.compile(r'"xsrfToken"\s*:\s*"(?P<token>[^"]+)"', re.I),
    re.compile(r'"_xsrf"\s*:\s*"(?P<token>[^"]+)"', re.I),
]


def extract_xsrf_token(html: str = "", cookies: list[dict[str, Any]] | None = None) -> str:
    for cookie in cookies or []:
        name = str(cookie.get("name") or "").lower()
        if name in {"_xsrf", "xsrf", "hhxsrf"}:
            return str(cookie.get("value") or "")
    for pattern in XSRF_PATTERNS:
        match = pattern.search(html)
        if match:
            return match.group("token")
    return ""


class HHBrowserSession:
    def __init__(
        self,
        *,
        cookie_backend: CookieBackend | None = None,
        cookie_path: str | Path | None = None,
    ):
        if cookie_backend is not None:
            self.cookie_backend = cookie_backend
        elif cookie_path is not None:
            self.cookie_backend = JsonCookieBackend(cookie_path)
        else:
            self.cookie_backend = JsonCookieBackend(Path(".work-hunter") / "hh_cookies.json")
        self.cookies: list[dict[str, Any]] = []
        self.xsrf_token = ""

    def load(self) -> None:
        self.cookies = self.cookie_backend.load()
        self.xsrf_token = extract_xsrf_token(cookies=self.cookies)

    def save(self) -> None:
        self.cookie_backend.save(self.cookies)

    def update_from_playwright_context(self, cookies: list[dict[str, Any]], html: str = "") -> None:
        self.cookies = [cookie for cookie in cookies if _is_hh_cookie(cookie)]
        self.xsrf_token = extract_xsrf_token(html, self.cookies)
        self.save()

    def load_from_json(self, value: str) -> None:
        payload = json.loads(value)
        self.cookies = list(payload) if isinstance(payload, list) else []
        self.xsrf_token = extract_xsrf_token(cookies=self.cookies)


def _is_hh_cookie(cookie: dict[str, Any]) -> bool:
    domain = str(cookie.get("domain") or "").lower().lstrip(".")
    return domain == "hh.ru" or domain.endswith(".hh.ru")
