from __future__ import annotations

from pathlib import Path
from typing import Any


def screenshot_paths(event: dict[str, Any]) -> dict[str, Path]:
    screenshots = (event.get("data") or {}).get("screenshots") or {}
    if not isinstance(screenshots, dict):
        return {}
    return {str(name): Path(path) for name, path in screenshots.items() if str(path)}
