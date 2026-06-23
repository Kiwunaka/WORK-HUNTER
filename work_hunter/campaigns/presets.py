from __future__ import annotations


CAMPAIGN_STAGES = ["draft", "planned", "reviewed", "enabled", "running", "paused", "completed", "cancelled", "failed"]
PER_VACANCY_STATES = [
    "found",
    "scored",
    "below_min_score",
    "duplicate",
    "blacklisted",
    "needs_resume",
    "needs_template",
    "pack_ready",
    "preview_ready",
    "blocked_test",
    "blocked_unknown_form",
    "blocked_captcha",
    "blocked_auth",
    "dry_run_ok",
    "ready_to_apply",
    "applied",
    "failed",
    "manual_review",
]


def main_python_backend_preset() -> dict:
    sources = ["hh", "geekjob", "habr", "getmatch", "hirehi", "careerspace", "jabka"]
    return {
        "name": "main-python-backend",
        "enabled": False,
        "real_apply": False,
        "sources": sources,
        "queries": ["python backend", "fastapi", "backend developer"],
        "min_score": 75,
        "daily_cap": 25,
        "per_source_cap": {
            "hh": 15,
            "geekjob": 5,
            "habr": 5,
            "getmatch": 5,
            "hirehi": 5,
            "careerspace": 5,
            "jabka": 5,
        },
        "per_company_cap": 1,
        "skip_tests": True,
        "skip_unknown_forms": True,
        "skip_captcha": True,
        "skip_challenges": True,
        "blacklist_enabled": True,
        "template_strategy": "auto_with_preview",
        "resume_strategy": "best_variant",
        "ai_filter_mode": "heavy",
        "preview_required_for_first_n": 10,
        "pause_on_error_rate": 0.15,
        "pause_on_rejection_spike": False,
        "time_window": {"enabled": True, "from": "09:00", "to": "21:00"},
    }
