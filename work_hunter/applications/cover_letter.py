from __future__ import annotations

from typing import Any

from .template_engine import render_template


APPLICATION_TEMPLATES: dict[str, str] = {
    "direct_human": "Hi {company}, I am a {candidate_title}. The {job_title} role looks relevant, especially {skills}.",
    "warm_recruiter": "Hello {company}, thanks for sharing the {job_title} opening. I would be glad to discuss how my {skills} experience fits.",
    "technical_fit": "For {job_title}, my strongest fit is {skills}. I focus on practical delivery, maintainable systems, and clear communication.",
    "startup_fast": "Hi {company}, I can move quickly on {job_title}: {skills}, ownership, and pragmatic delivery.",
    "remote_async": "Hi {company}, I work well remotely and asynchronously. For {job_title}, I can bring {skills} and clear written updates.",
    "career_switch_or_gap": "Hi {company}, my path includes transitions, but the relevant part for {job_title} is concrete experience with {skills}.",
    "minimal": "Hi {company}, I am interested in {job_title}. My relevant background: {skills}. Happy to talk.",
    "strong_match": "Hi {company}, this looks like a strong match: {job_title} aligns with my hands-on experience in {skills}.",
    "low_context": "Hi {company}, I would like to learn more about {job_title}. My relevant skills include {skills}.",
    "telegram_dm": "Hi! Saw {job_title} at {company}. I work with {skills}; happy to share details.",
}


def application_template_names() -> list[str]:
    return list(APPLICATION_TEMPLATES)


def render_cover_letter_template(name: str, context: dict[str, Any]) -> str:
    if name not in APPLICATION_TEMPLATES:
        raise ValueError(f"Unknown application template: {name}")
    return render_template(APPLICATION_TEMPLATES[name], context)
