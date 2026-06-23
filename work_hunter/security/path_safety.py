from __future__ import annotations

from pathlib import Path


def is_safe_project_path(root: str | Path, path: str | Path) -> bool:
    target = Path(path).resolve()
    try:
        target.relative_to(Path(root).resolve())
    except ValueError:
        return False
    parts = {part.lower() for part in target.parts}
    if ".codex" in parts or ".opencode" in parts or target.name.lower() == "auth.json":
        return False
    return True
