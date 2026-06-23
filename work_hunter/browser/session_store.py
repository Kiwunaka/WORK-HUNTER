from __future__ import annotations

from pathlib import Path

from ..config import data_dir
from .profiles import _source_name


def session_path_for_source(root: str | Path, source: str) -> Path:
    return data_dir(root) / "browser-sessions" / f"{_source_name(source)}.json"


def ensure_session_inside_workspace(root: str | Path, path: str | Path) -> bool:
    workspace = data_dir(root).resolve()
    try:
        Path(path).resolve().relative_to(workspace)
        return True
    except ValueError:
        return False
