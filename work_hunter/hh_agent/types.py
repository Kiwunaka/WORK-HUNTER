from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class SearchFilters:
    text: str = ""
    area: list[str] = field(default_factory=list)
    professional_role: list[str] = field(default_factory=list)
    schedule: str = ""
    experience: str = ""
    employment: list[str] = field(default_factory=list)
    salary: int | None = None
    page: int = 0
    per_page: int = 25

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class VacancySearchResult:
    vacancy_id: str
    name: str = ""
    employer_name: str = ""
    alternate_url: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PrecheckResult:
    status: str = "passed"
    reason: str = ""
    risk_flags: list[str] = field(default_factory=list)
    dedupe_key: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.status == "passed"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class VacancyAnalysisResult:
    vacancy_id: str
    score: int = 0
    recommended_action: str = "skip"
    reasons: list[str] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    raw_result: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CoverLetterRequest:
    vacancy_id: str
    resume_id: str = ""
    style: str = ""
    language: str = ""
    notes: str = ""
    vacancy: dict[str, Any] = field(default_factory=dict)
    persona: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CoverLetterResult:
    vacancy_id: str
    body: str = ""
    model: str = ""
    raw_result: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ApplicationAttemptResult:
    vacancy_id: str
    resume_id: str = ""
    status: str = ""
    reason: str = ""
    raw_result: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
