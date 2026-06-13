from __future__ import annotations

import re
from typing import Any

from .models import Job, JobScore


def score_job(job: Job, profile: dict[str, Any]) -> JobScore:
    text = _normalize(
        " ".join(
            [
                job.title,
                job.company,
                job.description,
                job.location,
                job.salary_text,
            ]
        )
    )
    title_text = _normalize(job.title)
    reasons: list[str] = []
    red_flags: list[str] = []

    title_score = _title_score(title_text, profile, reasons)
    skills_score = _skills_score(text, profile, reasons)
    salary_score = _salary_score(job, profile, reasons)
    remote_score = _remote_score(job, profile, reasons, red_flags)
    penalty_score = _penalty_score(text, profile, red_flags)

    total = title_score + skills_score + salary_score + remote_score + penalty_score
    total = max(0, min(100, total))
    if not reasons and not red_flags:
        reasons.append("No strong match signals found yet")

    return JobScore(
        job_id=job.id,
        total_score=total,
        title_score=title_score,
        skills_score=skills_score,
        salary_score=salary_score,
        remote_score=remote_score,
        penalty_score=penalty_score,
        reasons=reasons,
        red_flags=red_flags,
    )


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").lower()).strip()


def _contains(text: str, needle: str) -> bool:
    needle = _normalize(needle)
    if not needle:
        return False
    return needle in text


def _title_score(title: str, profile: dict[str, Any], reasons: list[str]) -> int:
    desired = profile.get("desired_roles") or []
    matches = [role for role in desired if _contains(title, str(role))]
    if matches:
        reasons.append(f"Title matches desired role: {', '.join(matches[:3])}")
        return 25
    must = profile.get("must_have_skills") or []
    skill_matches = [skill for skill in must if _contains(title, str(skill))]
    if skill_matches:
        reasons.append(f"Title contains key skill: {', '.join(skill_matches[:3])}")
        return 16
    return 0


def _skills_score(text: str, profile: dict[str, Any], reasons: list[str]) -> int:
    must = [str(skill) for skill in profile.get("must_have_skills") or []]
    nice = [str(skill) for skill in profile.get("nice_to_have_skills") or []]
    must_matches = [skill for skill in must if _contains(text, skill)]
    nice_matches = [skill for skill in nice if _contains(text, skill)]

    score = 0
    if must:
        score += round(30 * len(must_matches) / len(must))
    if nice:
        score += round(15 * len(nice_matches) / len(nice))
    if must_matches:
        reasons.append(f"Must-have skills found: {', '.join(must_matches[:5])}")
    if nice_matches:
        reasons.append(f"Nice-to-have skills found: {', '.join(nice_matches[:5])}")
    return min(45, score)


def _salary_score(job: Job, profile: dict[str, Any], reasons: list[str]) -> int:
    salary_min = int(profile.get("salary_min") or 0)
    if salary_min <= 0:
        return 5 if job.salary_text else 2
    best = job.salary_to or job.salary_from
    if best is None:
        reasons.append("Salary is not specified")
        return 3
    if best >= salary_min:
        reasons.append(f"Salary reaches configured floor: {job.salary_text}")
        return 10
    reasons.append(f"Salary appears below configured floor: {job.salary_text}")
    return -5


def _remote_score(
    job: Job,
    profile: dict[str, Any],
    reasons: list[str],
    red_flags: list[str],
) -> int:
    remote_only = bool(profile.get("remote_only"))
    if job.remote is True:
        reasons.append("Remote-compatible vacancy")
        return 10
    if remote_only:
        red_flags.append("Remote-only profile, but vacancy is not marked remote")
        return -10
    return 0


def _penalty_score(text: str, profile: dict[str, Any], red_flags: list[str]) -> int:
    stop_words = [str(word) for word in profile.get("stop_words") or []]
    matches = [word for word in stop_words if _contains(text, word)]
    if not matches:
        return 0
    red_flags.append(f"Stop words found: {', '.join(matches[:5])}")
    return -30
