from __future__ import annotations

from pathlib import Path


def chromium_open_command(*, profile_dir: str | Path, url: str) -> list[str]:
    return [
        "python",
        "-m",
        "playwright",
        "open",
        "--browser",
        "chromium",
        "--user-data-dir",
        str(profile_dir),
        url,
    ]
