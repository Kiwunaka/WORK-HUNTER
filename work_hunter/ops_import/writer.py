from __future__ import annotations

from pathlib import Path
from typing import Any

from ..product_lock import import_product_lock_assets


def write_ops_import(source_root: str | Path, target_root: str | Path, *, dry_run: bool = False) -> dict[str, Any]:
    return import_product_lock_assets(source_root, target_root, dry_run=dry_run)
