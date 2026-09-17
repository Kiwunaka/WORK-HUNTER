"""Напечатать host и port UI из конфига для start-work-hunter.bat.

Отдельный скрипт, потому что вложенные кавычки и скобки в bat-строке
`for /f` ломают разбор команды.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()

sys.path.insert(0, str(ROOT))

from work_hunter.config import config_path, load_config  # noqa: E402


def main() -> int:
    try:
        config = load_config(config_path(ROOT))
        ui = config.get("ui") or {}
        host = str(ui.get("host") or "127.0.0.1")
        port = int(ui.get("port") or 8787)
    except Exception:
        host, port = "127.0.0.1", 8787
    print(f"{host} {port}")
    return 0


if __name__ == "__main__":
    sys.exit(main())