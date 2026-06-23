from __future__ import annotations

from ..resume_engine import canonicalize_resume_text


def parse_resume_text(text: str, *, source_name: str = "") -> dict:
    return canonicalize_resume_text(text, source_name=source_name)
