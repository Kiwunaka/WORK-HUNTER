from __future__ import annotations

from pathlib import Path
from typing import Any

from ..product_lock import ALLOWLISTED_PRODUCT_LOCK_FILES


def scan_ops_source(source_root: str | Path) -> list[dict[str, Any]]:
    source = Path(source_root)
    if not source.exists():
        return []
    source_resolved = source.resolve()
    results: list[dict[str, Any]] = []
    for relative in ALLOWLISTED_PRODUCT_LOCK_FILES:
        path = (source / relative).resolve()
        if not _is_inside(path, source_resolved) or not path.exists() or not path.is_file():
            continue
        results.append(
            {
                "path": str(path),
                "relative_path": relative.as_posix(),
                "allowlisted": True,
            }
        )
    return results


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
