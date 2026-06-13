from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any


def _list_value(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(part).strip() for part in value if str(part).strip()]
    return [str(value).strip()] if str(value).strip() else []


@dataclass(frozen=True)
class VacancyPolicy:
    must_have: list[str] = field(default_factory=list)
    nice_to_have: list[str] = field(default_factory=list)
    avoid: list[str] = field(default_factory=list)
    dealbreakers: list[str] = field(default_factory=list)
    excluded_employers: list[str] = field(default_factory=list)
    excluded_keywords: list[str] = field(default_factory=list)
    excluded_texts: list[str] = field(default_factory=list)
    min_score: int = 0
    cover_letter_style: str = ""
    cover_letter_language: str = ""
    force_message: str = ""
    notes: str = ""
    skip_blacklisted_employers: bool = True

    @classmethod
    def from_mapping(cls, value: dict[str, Any] | None) -> "VacancyPolicy":
        source = value or {}
        return cls(
            must_have=_list_value(source.get("must_have")),
            nice_to_have=_list_value(source.get("nice_to_have")),
            avoid=_list_value(source.get("avoid")),
            dealbreakers=_list_value(source.get("dealbreakers")),
            excluded_employers=_list_value(source.get("excluded_employers")),
            excluded_keywords=_list_value(source.get("excluded_keywords")),
            excluded_texts=_list_value(source.get("excluded_texts")),
            min_score=int(source.get("min_score") or 0),
            cover_letter_style=str(source.get("cover_letter_style") or ""),
            cover_letter_language=str(source.get("cover_letter_language") or ""),
            force_message=str(source.get("force_message") or ""),
            notes=str(source.get("notes") or ""),
            skip_blacklisted_employers=bool(source.get("skip_blacklisted_employers", True)),
        )

    def to_canonical_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key, item in list(data.items()):
            if isinstance(item, list):
                data[key] = sorted({part.strip().lower() for part in item if part.strip()})
        return data

    def hash(self) -> str:
        payload = json.dumps(self.to_canonical_dict(), ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
