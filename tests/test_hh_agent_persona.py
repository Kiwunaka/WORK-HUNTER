from __future__ import annotations

from work_hunter.hh_agent.persona import load_persona_markdown, persona_from_profile


def test_load_persona_markdown_extracts_simple_facts(tmp_path):
    path = tmp_path / "persona.md"
    path.write_text("Name: Candidate\nTone: direct\n\nPython backend developer", encoding="utf-8")

    persona = load_persona_markdown(path)

    assert persona.facts["name"] == "Candidate"
    assert "Python backend" in persona.to_prompt()


def test_persona_from_profile_merges_profile_and_about():
    persona = persona_from_profile(
        {"name": "Candidate", "title": "Backend", "desired_roles": ["python"], "must_have_skills": ["Python"]},
        {"summary": "Builds APIs", "all_skills": ["FastAPI", "PostgreSQL"]},
    )

    assert persona.facts["name"] == "Candidate"
    assert "FastAPI" in persona.facts["skills"]
