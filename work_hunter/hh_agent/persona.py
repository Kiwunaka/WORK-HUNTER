from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class PersonaContext:
    body: str = ""
    facts: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_prompt(self) -> str:
        if not self.facts:
            return self.body.strip()
        facts = "\n".join(f"- {key}: {value}" for key, value in sorted(self.facts.items()))
        return "\n".join(part for part in [self.body.strip(), facts] if part).strip()


def load_persona_markdown(path: str | Path) -> PersonaContext:
    body = Path(path).read_text(encoding="utf-8")
    return PersonaContext(body=body, facts=_extract_frontmatter_like_facts(body))


def persona_from_profile(profile: dict[str, Any], about: dict[str, Any] | None = None) -> PersonaContext:
    about = about or {}
    facts = {
        "name": profile.get("name", ""),
        "title": profile.get("title", ""),
        "desired_roles": ", ".join(str(item) for item in profile.get("desired_roles") or []),
        "skills": ", ".join(str(item) for item in about.get("all_skills") or profile.get("must_have_skills") or []),
        "summary": about.get("summary", ""),
    }
    for key in (
        "email",
        "phone",
        "github",
        "linkedin",
        "portfolio",
        "website",
        "city",
        "location",
        "locations",
        "desired_salary",
        "salary",
        "salary_min",
    ):
        if key not in facts and profile.get(key):
            facts[key] = profile.get(key)
    body = "\n".join(str(value) for value in facts.values() if value).strip()
    return PersonaContext(body=body, facts={key: value for key, value in facts.items() if value})


def _extract_frontmatter_like_facts(body: str) -> dict[str, str]:
    facts: dict[str, str] = {}
    for line in body.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower().replace(" ", "_")
        value = value.strip()
        if key and value and len(key) <= 40:
            facts[key] = value
    return facts
