from __future__ import annotations

from pathlib import Path

from .profiles import browser_screenshots_path


def screenshot_dir_for_source(root: str | Path, source: str) -> Path:
    path = browser_screenshots_path(root, source)
    path.mkdir(parents=True, exist_ok=True)
    return path
