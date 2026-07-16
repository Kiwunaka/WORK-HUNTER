from __future__ import annotations

import json
import os
import re
import tempfile
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
            self.cookie_backend = JsonCookieBackend(
                Path(".work-hunter") / "private" / "hh-sessions" / "default.json"
            )
        self.cookies: list[dict[str, Any]] = []
        self.xsrf_token = ""

    def load(self) -> None:
        self.cookies = _validated_hh_cookies(
            self.cookie_backend.load(),
            reject_invalid=True,
        )
        self.xsrf_token = extract_xsrf_token(cookies=self.cookies)

    def save(self) -> None:
        self.cookies = _validated_hh_cookies(self.cookies, reject_invalid=True)
        if isinstance(self.cookie_backend, JsonCookieBackend):
            _atomic_cookie_save(self.cookie_backend.path, self.cookies)
        else:
            self.cookie_backend.save(self.cookies)

    def update_from_playwright_context(self, cookies: list[dict[str, Any]], html: str = "") -> None:
        self.cookies = [cookie for cookie in cookies if _is_hh_cookie(cookie)]
        self.xsrf_token = extract_xsrf_token(html, self.cookies)
        self.save()

    def load_from_json(self, value: str) -> None:
        payload = json.loads(value)
        if not isinstance(payload, list):
            raise ValueError("cookie export must be a JSON list")
        self.cookies = _validated_hh_cookies(payload, reject_invalid=True)
        self.xsrf_token = extract_xsrf_token(cookies=self.cookies)

    def load_cookie(self, name: str) -> str:
        wanted = str(name).casefold()
        for cookie in self.cookies:
            if str(cookie.get("name") or "").casefold() == wanted:
                return str(cookie.get("value") or "")
        return ""

    def clear(self) -> None:
        self.cookies = []
        self.xsrf_token = ""
        self.save()

    def diagnostics(self) -> dict[str, Any]:
        return {
            "authenticated": bool(self.cookies),
            "cookie_count": len(self.cookies),
            "cookies": [
                {
                    "name": str(cookie.get("name") or ""),
                    "domain": str(cookie.get("domain") or ""),
                    "value": "***" if cookie.get("value") else "",
                }
                for cookie in self.cookies
            ],
            "xsrf": "***" if self.xsrf_token else "",
        }


def _is_hh_cookie(cookie: dict[str, Any]) -> bool:
    domain = str(cookie.get("domain") or "").lower().lstrip(".")
    return domain == "hh.ru" or domain.endswith(".hh.ru")


def _validated_hh_cookies(
    cookies: list[Any],
    *,
    reject_invalid: bool,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for cookie in cookies:
        if not isinstance(cookie, dict) or not _is_hh_cookie(cookie):
            if reject_invalid:
                raise ValueError("all imported cookies must belong to hh.ru")
            continue
        name = str(cookie.get("name") or "").strip()
        if not name:
            if reject_invalid:
                raise ValueError("cookie name is required")
            continue
        result.append(dict(cookie))
    return result


def _atomic_cookie_save(path: Path, cookies: list[dict[str, Any]]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(cookies, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
        except OSError:
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            except OSError:
                pass
            finally:
                os.close(directory_fd)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise
