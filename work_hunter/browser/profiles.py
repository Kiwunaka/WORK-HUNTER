from __future__ import annotations

from pathlib import Path

from ..config import data_dir


def browser_profile_path(root: str | Path, source: str) -> Path:
    return data_dir(root) / "browser-profiles" / _source_name(source)


def browser_screenshots_path(root: str | Path, source: str) -> Path:
    return data_dir(root) / "browser-screenshots" / _source_name(source)


def _source_name(value: str) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace("/", "_").replace("\\", "_")
