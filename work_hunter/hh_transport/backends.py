from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol


class ConfigBackend(Protocol):
    def load(self) -> dict[str, Any]:
        ...

    def save(self, patch: dict[str, Any]) -> None:
        ...


class CookieBackend(Protocol):
    def load(self) -> list[dict[str, Any]]:
        ...

    def save(self, cookies: list[dict[str, Any]]) -> None:
        ...


class DictConfigBackend:
    def __init__(self, config: dict[str, Any]):
        self.config = config

    def load(self) -> dict[str, Any]:
        return dict(self.config)

    def save(self, patch: dict[str, Any]) -> None:
        self.config.update(patch)


class JsonFileBackend:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        with self.path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
        return payload if isinstance(payload, dict) else {}

    def save(self, patch: dict[str, Any]) -> None:
        payload = self.load()
        payload.update(patch)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.write("\n")


class JsonCookieBackend:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with self.path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
        return list(payload) if isinstance(payload, list) else []

    def save(self, cookies: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as fh:
            json.dump(cookies, fh, ensure_ascii=False, indent=2)
            fh.write("\n")


class NetscapeCookieBackend:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        cookies: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            http_only = False
            if line.startswith("#HttpOnly_"):
                line = line.removeprefix("#HttpOnly_")
                http_only = True
            elif line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 7:
                continue
            domain, _include_subdomains, path, secure, expires, name, value = parts[:7]
            normalized = domain.lower().lstrip(".")
            if normalized != "hh.ru" and not normalized.endswith(".hh.ru"):
                continue
            cookies.append(
                {
                    "name": name,
                    "value": value,
                    "domain": domain,
                    "path": path or "/",
                    "expires": int(expires) if str(expires).isdigit() else -1,
                    "secure": secure.upper() == "TRUE",
                    "httpOnly": http_only,
                }
            )
        return cookies

    def save(self, cookies: list[dict[str, Any]]) -> None:
        lines = ["# Netscape HTTP Cookie File"]
        for cookie in cookies:
            domain = str(cookie.get("domain") or ".hh.ru")
            include_subdomains = "TRUE" if domain.startswith(".") else "FALSE"
            path = str(cookie.get("path") or "/")
            secure = "TRUE" if cookie.get("secure") else "FALSE"
            expires = str(cookie.get("expires") or 0)
            name = str(cookie.get("name") or "")
            value = str(cookie.get("value") or "")
            if name:
                lines.append("\t".join([domain, include_subdomains, path, secure, expires, name, value]))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
