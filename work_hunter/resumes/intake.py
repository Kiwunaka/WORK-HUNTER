from __future__ import annotations

from pathlib import Path
from typing import Any

from ..resume_engine import import_resume_file


def intake_resume(path: str | Path) -> dict[str, Any]:
    return import_resume_file(path)
