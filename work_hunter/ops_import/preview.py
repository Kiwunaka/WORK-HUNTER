from __future__ import annotations

from pathlib import Path
from typing import Any

from ..product_lock import build_product_lock_preview
from .classifier import classify_ops_files
from .mapper import map_ops_import
from .scanner import scan_ops_source


PIPELINE_STAGES = ["scan", "classify", "redact", "map", "diff"]


def build_ops_import_preview(source_root: str | Path, target_root: str | Path) -> dict[str, Any]:
    preview = build_product_lock_preview(source_root, target_root)
    scanned = scan_ops_source(source_root)
    classified = classify_ops_files(scanned)
    mapping = map_ops_import(classified, dict(preview.get("files") or {}))
    return {
        **preview,
        "pipeline": list(PIPELINE_STAGES),
        "scanned_files": scanned,
        "classified_files": classified,
        "mapping": mapping,
    }
