from __future__ import annotations

from typing import Any


def build_application_preview(
    *,
    job: dict[str, Any],
    resume_variant: dict[str, Any],
    cover_letter: str,
    short_message: str,
    source_payload: dict[str, Any],
) -> dict[str, Any]:
    diff = resume_variant.get("diff") if isinstance(resume_variant, dict) else {}
    fields = source_payload.get("fields") if isinstance(source_payload, dict) else []
    return {
        "job": job,
        "resume_variant": resume_variant,
        "resume_changes": list((diff or {}).get("added_lines") or []),
        "letter": cover_letter,
        "cover_letter": cover_letter,
        "short_message": short_message,
        "fields": list(fields or []),
        "payload": source_payload,
        "what_will_be_sent": {
            "resume_variant_id": str(resume_variant.get("id") or "") if isinstance(resume_variant, dict) else "",
            "cover_letter": bool(cover_letter),
            "short_message": bool(short_message),
            "fields": len(list(fields or [])),
        },
    }
