"""Local candidate artifacts and deterministic approval fingerprints."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .models import Resume


def candidate_facts(profile: dict[str, Any], about: dict[str, Any]) -> dict[str, Any]:
    """Candidate statements only. Search keywords are preferences, not experience."""
    contacts = {key: profile[key] for key in (
        "name", "first_name", "last_name", "email", "phone", "city", "location",
        "github", "linkedin", "linkedin_url", "portfolio", "portfolio_url", "website",
    ) if profile.get(key)}
    return copy.deepcopy({"contacts": contacts, **about})


def candidate_prompt(profile: dict[str, Any], about: dict[str, Any]) -> str:
    return (
        "Факты кандидата (со слов пользователя; отсутствующее неизвестно):\n"
        + json.dumps(candidate_facts(profile, about), ensure_ascii=False, indent=2)
        + "\nПредпочтения поиска, не доказательство опыта:\n"
        + json.dumps({key: profile[key] for key in (
            "title", "desired_roles", "must_have_skills", "nice_to_have_skills",
            "desired_salary", "desired_cities", "remote_only",
        ) if key in profile}, ensure_ascii=False)
    )


def validate_about(about: dict[str, Any]) -> None:
    from jsonschema import Draft202012Validator

    strings = {"type": "array", "items": {"type": "string"}}
    schema = {"type": "object", "properties": {
        "summary": {"type": "string"}, "all_skills": strings,
        "experience": {"type": "array", "items": {"type": "object", "properties": {
            **{key: {"type": "string"} for key in (
                "role", "project", "company", "start", "end", "contribution", "evidence_url",
            )},
            **{key: strings for key in ("details", "tech", "results")},
        }}},
    }}
    error = next(Draft202012Validator(schema).iter_errors(about), None)
    if error is not None:
        raise ValueError(f"Invalid candidate facts at {'.'.join(map(str, error.path))}: {error.message}")


def resume_from_facts(profile: dict[str, Any], about: dict[str, Any], role: str) -> str:
    """A deterministic, editable starting point; never fill missing facts."""
    validate_about(about)
    contacts = candidate_facts(profile, about)["contacts"]
    lines = [f"# {contacts.get('name') or role}", f"## {role}"]
    lines.extend(str(contacts[key]) for key in ("email", "phone", "city", "portfolio_url", "github") if contacts.get(key))
    if about.get("summary"):
        lines += ["", "## О себе", about["summary"]]
    if about.get("all_skills"):
        lines += ["", "## Навыки", ", ".join(about["all_skills"])]
    if about.get("experience"):
        lines += ["", "## Опыт"]
        for exp in about["experience"]:
            lines += ["", "### " + " · ".join(str(exp[key]) for key in ("role", "company", "project") if exp.get(key))]
            if exp.get("start") or exp.get("end"):
                lines.append(" — ".join(str(exp[key]) for key in ("start", "end") if exp.get(key)))
            if exp.get("contribution"):
                lines.append(exp["contribution"])
            lines.extend(f"- {item}" for key in ("details", "results") for item in exp.get(key, []))
            if exp.get("tech"):
                lines.append("Технологии: " + ", ".join(exp["tech"]))
            if exp.get("evidence_url"):
                lines.append(exp["evidence_url"])
    return "\n".join(lines).strip()


def content_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def resume_artifact(root: Path, profile_id: str, resume: Resume | None,
                    fallback_path: str = "") -> dict[str, Any]:
    if resume is not None and resume.profile_id != profile_id:
        raise ValueError("Selected resume belongs to another candidate profile")
    source_path = str(resume.file_path if resume is not None else fallback_path).strip()
    if source_path:
        path = Path(source_path).expanduser()
        path = path if path.is_absolute() else root / path
        if path.stat().st_size > 10 * 1024 * 1024:
            raise ValueError("Resume file exceeds 10 MiB")
        data = path.read_bytes()
        suffix = path.suffix.lower()
        if suffix not in {".pdf", ".docx", ".txt", ".md"}:
            raise ValueError("Resume must be PDF, DOCX or text")
        source_path = str(path.resolve())
        name = resume.name if resume else path.name
    elif resume is not None and resume.body.strip():
        data = resume.body.encode("utf-8")
        suffix = ".txt"
        name = resume.name
    else:
        return {}
    if not data:
        raise ValueError("Resume file is empty")
    digest = hashlib.sha256(data).hexdigest()
    directory = root / ".work-hunter" / "artifacts" / "resumes"
    directory.mkdir(parents=True, exist_ok=True)
    artifact = directory / f"{digest}{suffix}"
    # Atomic replacement means concurrent preparation never reads a partial file.
    with tempfile.NamedTemporaryFile(dir=directory, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        except OSError:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, artifact)
    finally:
        temporary.unlink(missing_ok=True)
    return {"id": str(resume.id) if resume else None, "name": name,
            "profile_id": profile_id, "source_path": source_path,
            "path": str(artifact), "sha256": digest, "size": len(data),
            "version": content_hash(resume.to_dict()) if resume else digest,
            "format": suffix.removeprefix(".")}
