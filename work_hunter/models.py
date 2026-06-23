from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class JobScore:
    job_id: int | None = None
    profile_id: str = "default"
    total_score: int = 0
    title_score: int = 0
    skills_score: int = 0
    salary_score: int = 0
    remote_score: int = 0
    penalty_score: int = 0
    reasons: list[str] = field(default_factory=list)
    red_flags: list[str] = field(default_factory=list)
    scored_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Job:
    source: str
    source_id: str
    url: str
    title: str
    company: str = ""
    salary_text: str = ""
    salary_from: int | None = None
    salary_to: int | None = None
    currency: str = ""
    location: str = ""
    remote: bool | None = None
    description: str = ""
    published_at: str = ""
    fetched_at: str = field(default_factory=utc_now)
    id: int | None = None
    status: str = "new"
    score: JobScore | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["score"] = self.score.to_dict() if self.score else None
        return data


@dataclass
class StatusEvent:
    job_id: int
    status: str
    note: str = ""
    changed_at: str = field(default_factory=utc_now)


@dataclass
class LetterDraft:
    job_id: int
    body: str
    template_name: str = "default"
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ApplyPlan:
    job_id: int
    source: str
    mode: str = "api"
    resume_id: str | None = None
    letter: str = ""
    risk_flags: list[str] = field(default_factory=list)
    requires_confirmation: bool = True
    status: str = "ready"
    external_url: str = ""
    raw_result: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class HHResume:
    id: str
    title: str = ""
    url: str = ""
    alternate_url: str = ""
    status_id: str = ""
    status_name: str = ""
    can_publish_or_update: bool = False
    total_views: int = 0
    new_views: int = 0
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class HHEmployer:
    id: str
    name: str = ""
    type: str = ""
    description: str = ""
    site_url: str = ""
    alternate_url: str = ""
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class HHContact:
    id: str = ""
    vacancy_id: str = ""
    employer_id: str = ""
    employer_name: str = ""
    name: str = ""
    email: str = ""
    phone_numbers: str = ""
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class HHNegotiation:
    id: str
    state: str = ""
    vacancy_id: str = ""
    employer_id: str = ""
    chat_id: str = ""
    resume_id: str = ""
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class HHSkippedVacancy:
    id: int = 0
    resume_id: str = ""
    vacancy_id: str = ""
    reason: str = ""
    alternate_url: str = ""
    name: str = ""
    employer_name: str = ""
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class HHCampaignRun:
    id: int = 0
    status: str = "planned"
    filters: dict[str, Any] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    started_at: str = field(default_factory=utc_now)
    finished_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class HHCampaignItem:
    id: int = 0
    run_id: int = 0
    job_id: int = 0
    vacancy_id: str = ""
    status: str = "ready"
    reason: str = ""
    resume_id: str = ""
    letter: str = ""
    risk_flags: list[str] = field(default_factory=list)
    raw_result: dict[str, Any] = field(default_factory=dict)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class HHAgentMCPRun:
    id: int = 0
    tool_name: str = ""
    input: dict[str, Any] = field(default_factory=dict)
    status: str = "running"
    output: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    started_at: str = field(default_factory=utc_now)
    finished_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class HHAgentEvent:
    id: int = 0
    event_type: str = ""
    title: str = ""
    source_id: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    event_at: str = ""
    status: str = "active"
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class HHAgentTask:
    id: int = 0
    task_type: str = ""
    title: str = ""
    source_id: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    due_at: str = ""
    status: str = "open"
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class HHAgentOutboxItem:
    id: int = 0
    channel: str = ""
    target: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    status: str = "pending"
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class HHAgentWebhookDelivery:
    id: int = 0
    event_type: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    status: str = "pending"
    attempts: int = 0
    last_error: str = ""
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class HHVacancyAnalysis:
    id: int = 0
    run_id: int | None = None
    vacancy_id: str = ""
    resume_id: str = ""
    policy_hash: str = ""
    score: int = 0
    recommended_action: str = ""
    reasons: list[str] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    model: str = ""
    raw_result: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class HHApplicationAttempt:
    id: int = 0
    run_id: int | None = None
    campaign_item_id: int | None = None
    vacancy_id: str = ""
    resume_id: str = ""
    status: str = ""
    reason: str = ""
    letter: str = ""
    raw_result: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class HHPendingMessage:
    id: int = 0
    action_type: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    status: str = "pending"
    reason: str = ""
    ai_decision_id: int | None = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class HHAIDecision:
    id: int = 0
    action_type: str = ""
    target_id: str = ""
    model: str = ""
    policy_hash: str = ""
    confidence: float = 0.0
    reasons: list[str] = field(default_factory=list)
    raw_result: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class HHOperationLog:
    id: int = 0
    operation_id: int | None = None
    level: str = "info"
    message: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class JobNote:
    id: int = 0
    job_id: int = 0
    body: str = ""
    created_at: str = ""
    updated_at: str = ""


@dataclass
class Application:
    id: int = 0
    job_id: int = 0
    status: str = "applied"
    notes: str = ""
    applied_at: str = ""
    updated_at: str = ""

    def to_dict(self):
        return {
            "id": self.id,
            "job_id": self.job_id,
            "status": self.status,
            "notes": self.notes,
            "applied_at": self.applied_at,
            "updated_at": self.updated_at,
        }


@dataclass
class JobSummary:
    job_id: int = 0
    summary: str = ""
    created_at: str = ""


@dataclass
class Resume:
    id: int = 0
    name: str = ""
    body: str = ""
    profile_id: str = "default"
    is_active: bool = False
    ats_score: int | None = None
    source_format: str = ""
    imported_from: str = ""
    canonical: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CalendarEvent:
    id: int = 0
    job_id: int | None = None
    title: str = ""
    event_type: str = "interview"
    event_date: str = ""
    notes: str = ""
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SavedSearch:
    id: int = 0
    name: str = ""
    query: str = ""
    filters_json: str = "{}"
    alert_enabled: bool = False
    last_checked_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LearningEvent:
    id: int = 0
    job_id: int = 0
    action: str = ""
    timestamp: str = ""
