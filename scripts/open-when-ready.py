"""Дождаться готовности UI и открыть его в браузере.

Запускается из start-work-hunter.bat фоново, чтобы браузер не показывал
«сайт не отвечает», пока сервер ещё поднимается.
"""

from __future__ import annotations

import os
import sys
import time
import urllib.request
import webbrowser


def wait_until_ready(url: str, timeout_seconds: float) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1):
                return True
        except Exception:
            time.sleep(1)
    return False


def main(argv: list[str]) -> int:
    url = argv[1] if len(argv) > 1 else "http://127.0.0.1:8787"
    timeout_seconds = float(argv[2]) if len(argv) > 2 else 60.0
    if not wait_until_ready(url, timeout_seconds):
        return 1
    if os.environ.get("WORK_HUNTER_NO_BROWSER") != "1":
        webbrowser.open(url)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))