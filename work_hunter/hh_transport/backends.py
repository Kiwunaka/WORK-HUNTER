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
