from __future__ import annotations

INTERVIEW_STAGES = ["hr", "tech", "final", "offer"]

PIPELINE_AUTOMATION = [
    "applied_wait_3_days_follow_up",
    "reply_parse_schedule_interview_prep",
    "interview_generate_prep_pack",
    "after_interview_thank_you_follow_up",
]


def build_stage_focus(stage: str) -> dict[str, object]:
    normalized = stage if stage in INTERVIEW_STAGES else "hr"
    goals = {
        "hr": [
            "confirm motivation and expectations",
            "clarify salary range, remote setup and hiring timeline",
        ],
        "tech": [
            "connect stack questions to real projects",
            "prepare trade-offs and debugging examples",
        ],
        "final": [
            "show team fit and ownership style",
            "clarify first 90 days and decision process",
        ],
        "offer": [
            "align compensation with scope",
            "confirm benefits, contract format and start date",
        ],
    }
    return {"stage": normalized, "goals": goals[normalized]}
