"""Attach external-board adapters to the explicitly enabled local browser bridge."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

BRIDGE_SOURCES = frozenset({"habr", "geekjob", "hirehi", "careerspace", "getmatch", "rvc"})


def connect_browser_bridge(playwright: Any, root: Path, source: str) -> Any | None:
    """Return an attached browser, or None when this source has not opted in.

    An enabled but disconnected bridge fails closed: silently using the old
    profile could send the wrong account's resume. No cookies are transferred.
    """
    if source not in BRIDGE_SOURCES:
        return None
    state_path = root / ".work-hunter" / "browser-bridge.json"
    if not state_path.exists():
        return None
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if not state.get("enabled"):
        return None
    endpoint = str(state.get("endpoint") or "")
    if not endpoint.startswith("\\\\.\\pipe\\pw-"):
        raise RuntimeError("Мост браузера отключён. Запусти scripts/browser-bridge/start.py.")
    try:
        browser = playwright.chromium.connect(endpoint, timeout=10_000)
    except Exception:
        # Do not include the private local endpoint in public error output.
        raise RuntimeError("Нет связи с основным браузером. Перезапусти мост Work Hunter.") from None
    if not browser.contexts:
        browser.close()
        raise RuntimeError("Мост подключён, но рабочая группа вкладок недоступна.")
    return browser
