from __future__ import annotations

from .questions import onboarding_questions


def interview_outline() -> list[dict]:
    return onboarding_questions()
