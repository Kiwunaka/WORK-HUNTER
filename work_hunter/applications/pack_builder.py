from __future__ import annotations

from typing import Any

from .preview import build_application_preview
from .validators import build_application_policy


def build_application_pack_preview(
    *,
    job: dict[str, Any],
    resume_variant: dict[str, Any],
    cover_letter: str,
    short_message: str,
    source_payload: dict[str, Any],
) -> dict[str, Any]:
    return build_application_preview(
        job=job,
        resume_variant=resume_variant,
        cover_letter=cover_letter,
        short_message=short_message,
        source_payload=source_payload,
    )


__all__ = ["build_application_pack_preview", "build_application_policy"]
