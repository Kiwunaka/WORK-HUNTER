from __future__ import annotations

import difflib
import json
import re
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree


COMMON_WORDS = {
    "and",
    "the",
    "with",
    "for",
    "services",
    "developer",
    "engineer",
    "built",
    "опыт",
    "разработка",
    "сервисов",
}


def import_resume_file(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    suffix = source.suffix.lower()
    source_format = suffix.lstrip(".")
    if suffix == ".json":
        payload = json.loads(source.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, dict):
            raise ValueError("Resume JSON must be an object.")
        body = render_canonical_resume(canonicalize_resume_payload(payload))
        canonical = canonicalize_resume_payload(payload, fallback_body=body)
        name = str(payload.get("name") or payload.get("title") or source.stem)
        return _imported(name=name, body=body, canonical=canonical, source=source, source_format="json")
    if suffix in {".md", ".markdown", ".txt"}:
        text = source.read_text(encoding="utf-8-sig")
        canonical = canonicalize_resume_text(text, source_name=source.stem)
        body = text.strip()
        return _imported(
            name=str(canonical.get("title") or source.stem),
            body=body,
            canonical=canonical,
            source=source,
            source_format="md" if suffix != ".txt" else "txt",
        )
    if suffix == ".pdf":
        text = _optional_pdf_text(source)
        if text is None:
            return _unsupported(source_format="pdf", reason="pdf parser is not installed")
        canonical = canonicalize_resume_text(text, source_name=source.stem)
        return _imported(str(canonical.get("title") or source.stem), text, canonical, source, "pdf")
    if suffix == ".docx":
        text = _optional_docx_text(source)
        if text is None:
            return _unsupported(source_format="docx", reason="docx parser is not installed")
        canonical = canonicalize_resume_text(text, source_name=source.stem)
        return _imported(str(canonical.get("title") or source.stem), text, canonical, source, "docx")
    return _unsupported(source_format=source_format or "unknown", reason=f"unsupported resume format: {suffix or '<none>'}")


def canonicalize_resume_payload(payload: dict[str, Any], *, fallback_body: str = "") -> dict[str, Any]:
    title = _clean(payload.get("title") or payload.get("name") or payload.get("role") or payload.get("position"))
    summary = _clean(payload.get("summary") or payload.get("description") or payload.get("about") or fallback_body)
    skills = _normalize_skills(payload.get("skills") or payload.get("skill_set"))
    experience = _normalize_lines(payload.get("experience") or payload.get("work_experience"))
    return {
        "title": title,
        "summary": summary,
        "skills": skills,
        "experience": experience,
    }


def canonicalize_resume_text(text: str, *, source_name: str = "") -> dict[str, Any]:
    sections = _markdown_sections(text)
    title = sections.get("title") or source_name
    summary = "\n".join(sections.get("summary", []) or sections.get("about", [])).strip()
    skills = _normalize_skills(sections.get("skills", []))
    experience = _normalize_lines(sections.get("experience", []))
    if not summary:
        summary = _first_non_heading_line(text)
    return {
        "title": title,
        "summary": summary,
        "skills": skills,
        "experience": experience,
    }


def render_canonical_resume(canonical: dict[str, Any]) -> str:
    parts: list[str] = []
    title = _clean(canonical.get("title"))
    if title:
        parts.append(f"# {title}")
    summary = _clean(canonical.get("summary"))
    if summary:
        parts.append(f"## Summary\n{summary}")
    skills = list(canonical.get("skills") or [])
    if skills:
        parts.append("## Skills\n" + "\n".join(str(skill) for skill in skills))
    experience = list(canonical.get("experience") or [])
    if experience:
        parts.append("## Experience\n" + "\n".join(str(item) for item in experience))
    return "\n\n".join(parts).strip()


def merge_resume_canonical_with_claims(base: dict[str, Any], claims: list[dict[str, Any]]) -> dict[str, Any]:
    canonical = {
        "title": _clean(base.get("title")),
        "summary": _clean(base.get("summary")),
        "skills": sorted({str(skill).lower() for skill in base.get("skills", []) if str(skill).strip()}),
        "experience": _normalize_lines(base.get("experience", [])),
    }
    for claim in claims:
        value = str(claim.get("value") or "")
        for token in _claim_keywords(value):
            canonical["skills"].append(token)
    canonical["skills"] = sorted(set(canonical["skills"]))
    return canonical


def diff_resume_text(base: str, variant: str) -> dict[str, list[str]]:
    base_lines = _diff_lines(base)
    variant_lines = _diff_lines(variant)
    diff = list(difflib.ndiff(base_lines, variant_lines))
    return {
        "added_lines": [line[2:] for line in diff if line.startswith("+ ")],
        "removed_lines": [line[2:] for line in diff if line.startswith("- ")],
    }


def _imported(
    name: str,
    body: str,
    canonical: dict[str, Any],
    source: Path,
    source_format: str,
) -> dict[str, Any]:
    return {
        "status": "imported",
        "name": name,
        "body": body.strip(),
        "canonical": canonical,
        "imported_from": str(source),
        "source_format": source_format,
    }


def _unsupported(*, source_format: str, reason: str) -> dict[str, Any]:
    return {"status": "unsupported", "source_format": source_format, "reason": reason}


def _optional_pdf_text(source: Path) -> str | None:
    try:
        from pypdf import PdfReader  # type: ignore
    except Exception:
        return _basic_pdf_text(source)
    try:
        reader = PdfReader(str(source))
        text = "\n".join(page.extract_text() or "" for page in reader.pages).strip()
        return text or _basic_pdf_text(source)
    except Exception:
        return _basic_pdf_text(source)


def _optional_docx_text(source: Path) -> str | None:
    try:
        import docx  # type: ignore
    except Exception:
        return _docx_text_from_zip(source)
    try:
        document = docx.Document(str(source))
        text = "\n".join(paragraph.text for paragraph in document.paragraphs).strip()
        return text or _docx_text_from_zip(source)
    except Exception:
        return _docx_text_from_zip(source)


def _docx_text_from_zip(source: Path) -> str | None:
    try:
        with zipfile.ZipFile(source) as archive:
            raw = archive.read("word/document.xml")
    except (KeyError, OSError, zipfile.BadZipFile):
        return None
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError:
        return None
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs: list[str] = []
    for paragraph in root.findall(".//w:p", namespace):
        parts = [
            node.text or ""
            for node in paragraph.findall(".//w:t", namespace)
            if node.text
        ]
        text = "".join(parts).strip()
        if text:
            paragraphs.append(text)
    return "\n".join(paragraphs).strip() or None


def _basic_pdf_text(source: Path) -> str | None:
    try:
        raw = source.read_bytes()
    except OSError:
        return None
    lines: list[str] = []
    for match in re.finditer(rb"\(((?:\\.|[^\\)])*)\)\s*Tj", raw, flags=re.DOTALL):
        value = _decode_pdf_literal(match.group(1))
        if value.strip():
            lines.append(value.strip())
    return "\n".join(lines).strip() or None


def _decode_pdf_literal(value: bytes) -> str:
    result = bytearray()
    index = 0
    while index < len(value):
        current = value[index]
        if current == 92 and index + 1 < len(value):
            index += 1
            escaped = value[index]
            result.append({ord("n"): 10, ord("r"): 13, ord("t"): 9}.get(escaped, escaped))
        else:
            result.append(current)
        index += 1
    return result.decode("utf-8", errors="replace")


def _markdown_sections(text: str) -> dict[str, Any]:
    sections: dict[str, Any] = {}
    current = ""
    aliases = {
        "profile": "summary",
        "about": "summary",
        "about me": "summary",
        "summary": "summary",
        "skills": "skills",
        "skill set": "skills",
        "experience": "experience",
        "work experience": "experience",
    }
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("# "):
            sections["title"] = line[2:].strip()
            current = ""
            continue
        if line.startswith("## "):
            key = line[3:].strip().lower()
            current = aliases.get(key, key)
            sections.setdefault(current, [])
            continue
        if current:
            sections.setdefault(current, []).append(line.lstrip("-* ").strip())
    return sections


def _normalize_skills(value: Any) -> list[str]:
    return sorted({item.lower() for item in _normalize_lines(value) if item})


def _normalize_lines(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_items = re.split(r"[\n,;]+", value)
    elif isinstance(value, (list, tuple, set)):
        raw_items = [str(item) for item in value]
    else:
        raw_items = [str(value)]
    return [_clean(item) for item in raw_items if _clean(item)]


def _claim_keywords(value: str) -> list[str]:
    tokens = []
    for token in re.split(r"[^A-Za-zА-Яа-я0-9_+#.-]+", value.lower()):
        token = token.strip("._-")
        if len(token) < 2 or token in COMMON_WORDS:
            continue
        tokens.append(token)
    return tokens


def _first_non_heading_line(text: str) -> str:
    for raw in text.splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            return line
    return ""


def _diff_lines(value: str) -> list[str]:
    return [line.rstrip() for line in value.splitlines() if line.strip()]


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()
