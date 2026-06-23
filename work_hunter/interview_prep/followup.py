from __future__ import annotations

from typing import Any


def build_after_interview_assets(job: Any, *, stage: str) -> dict[str, str]:
    company = getattr(job, "company", "") or "team"
    title = getattr(job, "title", "") or "the role"
    return {
        "thank_you_template": (
            f"Hi {company}, thank you for the conversation about {title}. "
            "I appreciated the context and would be glad to share any extra details if useful."
        ),
        "follow_up_template": (
            f"Hi {company}, I wanted to follow up after our {stage} conversation. "
            "The role still looks relevant, and I would be happy to discuss next steps."
        ),
    }
