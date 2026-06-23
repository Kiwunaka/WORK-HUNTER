from __future__ import annotations

from typing import Any


ONBOARDING_QUESTIONS: tuple[dict[str, str], ...] = (
    {
        "id": "identity",
        "title": "Identity",
        "prompt": "Who are you, where are you based, what languages and timezone should Work Hunter use?",
        "category": "identity",
    },
    {
        "id": "stack",
        "title": "Stack",
        "prompt": "What technologies, tools, frameworks, databases, cloud, and testing skills can be claimed truthfully?",
        "category": "skills",
    },
    {
        "id": "roles",
        "title": "Roles",
        "prompt": "Which roles, seniority, industries, and job themes are a good fit?",
        "category": "target",
    },
    {
        "id": "experience",
        "title": "Experience",
        "prompt": "Describe practical experience, companies, projects, stack, and measurable outcomes.",
        "category": "experience",
    },
    {
        "id": "projects",
        "title": "Projects",
        "prompt": "Which projects can be discussed, linked, or used as proof?",
        "category": "portfolio",
    },
    {
        "id": "achievements",
        "title": "Achievements",
        "prompt": "Which achievements can be proven by metrics, links, references, or artifacts?",
        "category": "experience",
    },
    {
        "id": "forbidden_claims",
        "title": "Forbidden Claims",
        "prompt": "What must Work Hunter never claim in resumes, letters, or applications?",
        "category": "constraints",
    },
    {
        "id": "salary_format",
        "title": "Salary And Format",
        "prompt": "What salary, location, remote, relocation, schedule, and format constraints apply?",
        "category": "constraints",
    },
    {
        "id": "avoid",
        "title": "Avoid",
        "prompt": "Which companies, industries, topics, or vacancy patterns should be excluded?",
        "category": "constraints",
    },
    {
        "id": "writing_style",
        "title": "Writing Style",
        "prompt": "What tone and communication style should cover letters and messages use or avoid?",
        "category": "writing_style",
    },
    {
        "id": "resume_assets",
        "title": "Resume Assets",
        "prompt": "Which HH resume IDs, PDFs, DOCX, Markdown, or JSON resumes already exist?",
        "category": "resume_assets",
    },
    {
        "id": "apply_permissions",
        "title": "Apply Permissions",
        "prompt": "Where may Work Hunter login, keep local browser profiles, preview applications, or apply after confirmation?",
        "category": "permissions",
    },
)


def onboarding_questions() -> list[dict[str, Any]]:
    return [dict(item) for item in ONBOARDING_QUESTIONS]
