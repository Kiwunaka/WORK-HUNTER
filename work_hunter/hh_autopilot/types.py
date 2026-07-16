from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class AutopilotState(StrEnum):
    DISCOVERED = "discovered"
    ELIGIBLE = "eligible"
    RANKED = "ranked"
    READY = "ready"
    APPLYING = "applying"
    RECONCILING = "reconciling"
    RETRY_WAIT = "retry_wait"
    MANUAL_CHALLENGE = "manual_challenge"
    APPLIED = "applied"
    SKIPPED = "skipped"
    DEAD = "dead"


class RetryStage(StrEnum):
    ELIGIBILITY = "eligibility"
    APPLICATION = "application"
    RECONCILIATION = "reconciliation"


class DeliveryCertainty(StrEnum):
    DEFINITELY_NOT_SENT = "definitely_not_sent"
    POSSIBLY_SENT = "possibly_sent"
    DEFINITE_RESPONSE = "definite_response"


class QuotaReservationState(StrEnum):
    RESERVED = "reserved"
    HELD = "held"
    CONSUMED = "consumed"
    RELEASED = "released"


STABLE_OUTCOME_CODES = frozenset(
    {
        "applied",
        "duplicate",
        "duplicate_external",
        "external_applied",
        "ambiguous_remote_result",
        "ambiguous_application",
        "unresolved_ambiguity",
        "vacancy_closed",
        "forbidden",
        "missing_required_data",
        "screening_disabled",
        "form_disabled",
        "ai_unavailable",
        "manual_assessment",
        "manual_captcha",
        "hh_daily_limit",
        "rate_limited",
        "auth_expired",
        "manual_auth",
        "challenge_expired",
        "challenge_dismissed",
        "pre_dispatch_network_error",
        "post_dispatch_network_error",
        "server_error",
        "read_parse_error",
        "post_dispatch_parse_error",
        "internal_error",
        "invalid_request",
        "retry_exhausted",
        "interrupted",
        "authorization_state_mismatch",
    }
)


class AuthorizationKind(StrEnum):
    LITERAL_CONFIRMATION = "literal_confirmation"
    AUTOPILOT = "autopilot"
    RECOVERY = "recovery"


@dataclass(frozen=True)
class LiteralConfirmation:
    account_id: str
    reference_id: str


@dataclass(frozen=True)
class LiveAuthorization:
    grant_id: int
    scope: str
    account_id: str
    run_id: int
    fencing_token: int
    policy_hash: str


@dataclass(frozen=True)
class RecoveryProvenance:
    attempt_id: int
    account_id: str
    authorization_kind: AuthorizationKind
    authorization_ref: str
    policy_hash: str


@dataclass(frozen=True)
class DispatchRequest:
    attempt_id: int
    account_id: str
    vacancy_id: str
    resume_id: str
    message: str = ""


@dataclass(frozen=True)
class DispatchOutcome:
    code: str
    certainty: DeliveryCertainty
    status_code: int | None = None
    retry_after_seconds: int | None = None
    location: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
