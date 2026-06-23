from __future__ import annotations

from typing import Any

from ..resume_payloads import normalize_hh_resume_payload, validate_hh_resume_payload


def adapt_hh_resume_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return normalize_hh_resume_payload(payload)


def validate_hh_resume(payload: dict[str, Any]) -> dict[str, Any]:
    return validate_hh_resume_payload(payload)
