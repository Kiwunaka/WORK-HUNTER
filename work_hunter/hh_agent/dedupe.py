from __future__ import annotations

import hashlib
import re
from typing import Any


TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")
PUNCT_RE = re.compile(r"[^\w\s]+", re.UNICODE)


def normalize_text(value: str) -> str:
    value = TAG_RE.sub(" ", value)
    value = PUNCT_RE.sub(" ", value.lower())
    return SPACE_RE.sub(" ", value).strip()


def build_vacancy_dedupe_key(vacancy: dict[str, Any]) -> str:
    employer = vacancy.get("employer") or {}
    snippet = vacancy.get("snippet") or {}
    employer_key = str(employer.get("id") or employer.get("name") or vacancy.get("employer_name") or "")
    title = str(vacancy.get("name") or vacancy.get("title") or "")
    description = " ".join(
        str(part or "")
        for part in (
            vacancy.get("description"),
            snippet.get("requirement"),
            snippet.get("responsibility"),
            vacancy.get("snippet"),
        )
        if part
    )
    normalized = "|".join(
        [
            normalize_text(employer_key),
            normalize_text(title),
            normalize_text(description)[:4000],
        ]
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
