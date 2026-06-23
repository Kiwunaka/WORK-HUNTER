from __future__ import annotations

from ..resume_engine import render_canonical_resume


def export_markdown(canonical: dict) -> str:
    return render_canonical_resume(canonical)
