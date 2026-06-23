from __future__ import annotations

from typing import Any

from ..config import mask_secrets

TIMELINE_EVENT_TYPES = [
    "campaign_started",
    "source_sync_started",
    "vacancy_found",
    "vacancy_scored",
    "ai_fit_completed",
    "resume_variant_created",
    "cover_letter_generated",
    "preview_created",
    "policy_blocked",
    "dry_run_started",
    "browser_opened",
    "form_detected",
    "fields_filled",
    "before_submit_screenshot",
    "apply_submitted",
    "after_submit_screenshot",
    "success_detected",
    "error_detected",
    "manual_review_required",
    "campaign_paused",
    "campaign_resumed",
    "campaign_completed",
]


def normalize_timeline_event(event: dict[str, Any], *, actor: str = "work-hunter") -> dict[str, Any]:
    data = mask_secrets(dict(event.get("data") or {}))
    return {
        "id": event.get("id"),
        "run_id": event.get("run_id"),
        "job_id": event.get("job_id"),
        "source": event.get("source"),
        "timestamp": event.get("timestamp") or event.get("created_at"),
        "actor": event.get("actor") or actor,
        "event_type": event.get("event_type"),
        "title": mask_secrets(event.get("title") or event.get("event_type") or "event"),
        "summary": mask_secrets(event.get("summary") or ""),
        "data": data,
        "redacted": True,
    }
