from __future__ import annotations

import copy
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class AgentWaveProfile:
    name: str
    max_agents: int
    model: str
    reasoning: str
    allowed_tasks: tuple[str, ...]
    forbidden_paths: tuple[str, ...] = ()
    forbidden_topics: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return asdict(self)


SMART_WAVE = AgentWaveProfile(
    name="smart-wave",
    max_agents=5,
    model="gpt-5.5",
    reasoning="high",
    allowed_tasks=(
        "architecture",
        "source adapter design",
        "browser flow design",
        "campaign policy",
        "resume/onboarding logic",
        "UI/UX planning",
        "safety review",
        "test design",
    ),
)

PATCH_WAVE = AgentWaveProfile(
    name="patch-wave",
    max_agents=2,
    model="gpt-5.3-codex-spark",
    reasoning="high",
    allowed_tasks=(
        "mechanical refactor",
        "rename fields",
        "update docs",
        "add repetitive tests",
        "migrate config keys",
    ),
    forbidden_paths=(
        "work_hunter/browser/session_store.py",
        "work_hunter/ai/redaction.py",
        "work_hunter/campaigns/policy.py",
        "work_hunter/sources/*/apply*.py",
        "work_hunter/storage.py migrations",
    ),
    forbidden_topics=(
        "secrets",
        "auth",
        "OAuth",
        "cookies",
        "real apply",
        "payment",
        "browser session",
    ),
)

WAVE_PROFILES = (SMART_WAVE, PATCH_WAVE)
REPO_SKILLS = (
    "work-hunter-ops",
    "work-hiring",
    "candidate-memory",
    "resume-ats",
    "source-adapter",
    "browser-session-lab",
    "campaign-policy",
    "replay-timeline",
    "ui-smoke",
    "secret-redaction",
)


def wave_profiles_as_dicts() -> list[dict]:
    return [_public_profile(profile) for profile in WAVE_PROFILES]


def wave_profile_by_name(name: str) -> AgentWaveProfile | None:
    normalized = str(name or "").strip()
    for profile in WAVE_PROFILES:
        if profile.name == normalized:
            return profile
    return None


def public_policy() -> dict:
    return {
        "waves": wave_profiles_as_dicts(),
        "repo_skills": list(REPO_SKILLS),
    }


def _public_profile(profile: AgentWaveProfile) -> dict:
    data = copy.deepcopy(profile.to_dict())
    data["allowed_tasks"] = list(data["allowed_tasks"])
    data["forbidden_paths"] = list(data["forbidden_paths"])
    data["forbidden_topics"] = list(data["forbidden_topics"])
    return data
