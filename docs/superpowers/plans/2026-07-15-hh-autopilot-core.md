# HH Autopilot Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build one restart-safe native HH pipeline that performs multi-page search, hard filtering, ranking, quota-controlled application, supported form handling, reconciliation, journaling, retry, and scheduled execution.

**Architecture:** Add a focused `work_hunter.hh_autopilot` package. `HHAutopilot` orchestrates typed ports; `AutopilotRepository` owns durable state and atomic transitions; existing `WorkHunter`, CLI, scheduler, MCP, and web code become thin entry points into the same executor. A remote application POST is never treated as idempotent: possibly-sent outcomes enter read-only reconciliation before any retry.

**Tech Stack:** Python 3.11+, SQLite, requests, Playwright, argparse, standard-library HTTP server, pytest, Ruff, mypy.

## Global Constraints

- The approved design is `docs/superpowers/specs/2026-07-15-hh-autopilot-design.md` and is authoritative.
- Every account defaults to `enabled=false`; autonomous dispatch requires an account-scoped durable application grant.
- Defaults are 50 successful applications per local account day, 10 per run, 45–120 seconds between sends, 08:00–21:00 in `Europe/Moscow`, one run every 60 minutes, 100 results per page, and 20 pages.
- All defaults, filters, weights, limits, delays, schedules, retry rules, and browser settings remain configurable within the validated bounds from the design.
- Unknown required candidate data is answered from explicit facts or AI; unresolved fields pause only that vacancy with a stable reason.
- CAPTCHA uses the configured Vision model with authenticated-browser handoff as fallback.
- Screening fields and assessments use grounded AI answers with browser handoff when confidence is insufficient.
- Possibly-sent POST failures reconcile before another POST. No local key is represented as remote idempotency.
- Manual confirmation, autonomous dispatch, read-only reconciliation, and local finalization use the distinct safety matrix from the design.
- MCP agent calls may authorize live applications only with literal `confirm=true`.
- Compatible reference source may be reused under its license; local reverse-engineering artifacts remain ignored.
- Use test-first RED/GREEN cycles for every production change.

## File Structure

**Create:**

- `work_hunter/hh_autopilot/__init__.py` — stable public exports.
- `work_hunter/hh_autopilot/types.py` — enums and immutable port/result types.
- `work_hunter/hh_autopilot/state_machine.py` — legal transitions and recovery targets.
- `work_hunter/hh_autopilot/config.py` — defaults, parsing, validation, canonical policy hash.
- `work_hunter/hh_autopilot/repository.py` — all autopilot SQL transactions and CAS/fencing checks.
- `work_hunter/hh_autopilot/authorization.py` — durable grant lifecycle and typed mutation authorization.
- `work_hunter/hh_autopilot/search.py` — multi-page search, checkpoints, deduplication, shadow results.
- `work_hunter/hh_autopilot/policy.py` — hard filters and stable evidence.
- `work_hunter/hh_autopilot/ranking.py` — deterministic score, structured AI gate, best-resume selection.
- `work_hunter/hh_autopilot/executor.py` — one guarded application attempt and delivery certainty.
- `work_hunter/hh_autopilot/reconcile.py` — account-wide remote negotiation reconciliation.
- `work_hunter/hh_autopilot/challenges.py` — screening/form mapping and durable manual challenges.
- `work_hunter/hh_autopilot/browser.py` — authenticated Playwright form/CAPTCHA handoff adapter.
- `work_hunter/hh_autopilot/engine.py` — the single orchestration pipeline.
- `work_hunter/hh_autopilot/scheduler.py` — due-window policy, recovery sweep, and daemon tick.
- `work_hunter/hh_transport/authorize.py` — browser-assisted OAuth/password/OTP login.
- `work_hunter/migrations/0002_account_aware_applications.sql` — multi-account application identity.
- `work_hunter/migrations/0003_hh_autopilot_runtime.sql` — runs, queue, journal, grants, leases, quotas, cooldown, challenges, search, and shadow tables.
- `docs/hh-autopilot.md` — operator setup, configuration, safety, rollout, recovery, and evidence guide.
- `tests/test_hh_autopilot_state_machine.py`
- `tests/test_hh_autopilot_config.py`
- `tests/test_hh_autopilot_migrations.py`
- `tests/test_hh_autopilot_repository.py`
- `tests/test_hh_autopilot_authorization.py`
- `tests/test_hh_autopilot_search.py`
- `tests/test_hh_autopilot_policy.py`
- `tests/test_hh_autopilot_ranking.py`
- `tests/test_hh_autopilot_executor.py`
- `tests/test_hh_autopilot_reconcile.py`
- `tests/test_hh_autopilot_challenges.py`
- `tests/test_hh_autopilot_engine.py`
- `tests/test_hh_autopilot_scheduler.py`
- `tests/test_hh_autopilot_cli.py`
- `tests/test_hh_autopilot_web.py`
- `tests/test_hh_autopilot_e2e.py`
- `tests/test_package_contract.py`
- `tests/fakes/__init__.py`
- `tests/fakes/hh_autopilot_server.py`
- `tests/fixtures/hh_autopilot/form.html`
- `tests/fixtures/hh_autopilot/captcha.html`
- `examples/hh-autopilot-runner.json`

**Modify:**

- `work_hunter/config.py:75` — insert validated autopilot defaults and protect service-managed generation fields.
- `work_hunter/models.py:292` — make application attempts account-aware; extend application identity.
- `work_hunter/storage.py:681` — migration ordering and compatibility application API only.
- `work_hunter/safety.py:1` — typed one-shot and durable authorization contexts.
- `work_hunter/hh_transport/api_session.py:125` — page metadata and delivery-phase transport errors.
- `work_hunter/hh_transport/errors.py:6` — delivery certainty on transport exceptions.
- `work_hunter/sources/hh.py:333` — expose page metadata and guarded apply response.
- `work_hunter/services.py:913` — construct ports and expose thin autopilot facade methods.
- `work_hunter/scheduler.py:12` — allow only grant-validated live tick and grant-independent recovery.
- `work_hunter/cli.py:29` — `hh autopilot` and `hh auth login` commands.
- `work_hunter/mcp_server.py` — read-only status/history/challenge tools only.
- `work_hunter/web/server.py:174` — local API endpoints with literal-confirmation boundaries.
- `work_hunter/web/static/index.html` — autopilot settings/status surface.
- `work_hunter/web/static/app.js` — status, enable/disable, pause, kill, shadow, canary, retry, challenge actions.
- `work_hunter/web/static/app.css` — existing-style status and queue presentation.
- `README.md` — setup, scheduler, safety, canary, and honest verification status.
- `.github/workflows/ci.yml` — deterministic, restart/concurrency, and browser gates.
- `Dockerfile` — packaged migrations/UI and private persistent data runtime.
- `pyproject.toml` — pytest markers and package/release verification.

---

### Task 1: Typed State Machine And Outcomes

**Files:**
- Create: `work_hunter/hh_autopilot/__init__.py`
- Create: `work_hunter/hh_autopilot/types.py`
- Create: `work_hunter/hh_autopilot/state_machine.py`
- Test: `tests/test_hh_autopilot_state_machine.py`

**Interfaces:**
- Produces: `AutopilotState`, `RetryStage`, `DeliveryCertainty`, `AuthorizationKind`, `LiteralConfirmation`, `LiveAuthorization`, `RecoveryProvenance`, `DispatchRequest`, `DispatchOutcome`, and `STABLE_OUTCOME_CODES`.
- Produces: `assert_transition(current, target) -> None` and `recovery_target(certainty) -> AutopilotState`.

- [ ] **Step 1: Write the failing state-machine tests**

```python
import pytest

from work_hunter.hh_autopilot.state_machine import assert_transition, recovery_target
from work_hunter.hh_autopilot.types import AutopilotState, DeliveryCertainty, STABLE_OUTCOME_CODES


def test_possibly_sent_recovery_enters_reconciliation() -> None:
    assert recovery_target(DeliveryCertainty.POSSIBLY_SENT) is AutopilotState.RECONCILING


def test_ready_cannot_skip_directly_to_applied() -> None:
    with pytest.raises(ValueError, match="ready -> applied"):
        assert_transition(AutopilotState.READY, AutopilotState.APPLIED)


def test_late_ambiguity_can_be_classified() -> None:
    assert_transition(AutopilotState.MANUAL_CHALLENGE, AutopilotState.APPLIED)


def test_complete_stable_outcome_vocabulary_is_declared() -> None:
    assert {
        "ambiguous_remote_result",
        "challenge_expired",
        "challenge_dismissed",
        "pre_dispatch_network_error",
        "invalid_request",
        "authorization_state_mismatch",
    } <= STABLE_OUTCOME_CODES
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_hh_autopilot_state_machine.py -q`

Expected: FAIL during import because `work_hunter.hh_autopilot` does not exist.

- [ ] **Step 3: Add the typed contracts and legal transition graph**

```python
# work_hunter/hh_autopilot/types.py
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


STABLE_OUTCOME_CODES = frozenset({
    "applied", "duplicate", "duplicate_external", "external_applied",
    "ambiguous_remote_result", "ambiguous_application", "unresolved_ambiguity",
    "vacancy_closed", "forbidden", "missing_required_data", "screening_disabled",
    "form_disabled", "ai_unavailable", "manual_assessment", "manual_captcha",
    "hh_daily_limit", "rate_limited", "auth_expired", "manual_auth",
    "challenge_expired", "challenge_dismissed", "pre_dispatch_network_error",
    "post_dispatch_network_error", "server_error", "read_parse_error",
    "post_dispatch_parse_error", "internal_error", "invalid_request",
    "retry_exhausted", "interrupted", "authorization_state_mismatch",
})


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
```

```python
# work_hunter/hh_autopilot/state_machine.py
from .types import AutopilotState, DeliveryCertainty


ALLOWED_TRANSITIONS = {
    AutopilotState.DISCOVERED: {AutopilotState.ELIGIBLE, AutopilotState.SKIPPED},
    AutopilotState.ELIGIBLE: {AutopilotState.RANKED, AutopilotState.RETRY_WAIT},
    AutopilotState.RANKED: {AutopilotState.READY, AutopilotState.SKIPPED, AutopilotState.RETRY_WAIT},
    AutopilotState.READY: {AutopilotState.APPLYING},
    AutopilotState.APPLYING: {
        AutopilotState.APPLIED,
        AutopilotState.SKIPPED,
        AutopilotState.RETRY_WAIT,
        AutopilotState.MANUAL_CHALLENGE,
        AutopilotState.RECONCILING,
        AutopilotState.DEAD,
    },
    AutopilotState.RECONCILING: {
        AutopilotState.APPLIED,
        AutopilotState.SKIPPED,
        AutopilotState.RETRY_WAIT,
        AutopilotState.MANUAL_CHALLENGE,
        AutopilotState.DEAD,
    },
    AutopilotState.RETRY_WAIT: {
        AutopilotState.ELIGIBLE,
        AutopilotState.READY,
        AutopilotState.RECONCILING,
        AutopilotState.DEAD,
    },
    AutopilotState.MANUAL_CHALLENGE: {
        AutopilotState.READY,
        AutopilotState.RECONCILING,
        AutopilotState.APPLIED,
        AutopilotState.SKIPPED,
        AutopilotState.DEAD,
    },
    AutopilotState.APPLIED: set(),
    AutopilotState.SKIPPED: set(),
    AutopilotState.DEAD: set(),
}


def assert_transition(current: AutopilotState, target: AutopilotState) -> None:
    if target not in ALLOWED_TRANSITIONS[current]:
        raise ValueError(f"Illegal HH autopilot transition: {current.value} -> {target.value}")


def recovery_target(certainty: DeliveryCertainty) -> AutopilotState:
    if certainty is DeliveryCertainty.POSSIBLY_SENT:
        return AutopilotState.RECONCILING
    return AutopilotState.RETRY_WAIT
```

- [ ] **Step 4: Export the public types**

```python
# work_hunter/hh_autopilot/__init__.py
from .types import (
    AuthorizationKind,
    AutopilotState,
    DeliveryCertainty,
    DispatchOutcome,
    DispatchRequest,
    LiteralConfirmation,
    LiveAuthorization,
    RecoveryProvenance,
    RetryStage,
    STABLE_OUTCOME_CODES,
)

__all__ = [
    "AuthorizationKind",
    "AutopilotState",
    "DeliveryCertainty",
    "DispatchOutcome",
    "DispatchRequest",
    "LiteralConfirmation",
    "LiveAuthorization",
    "RecoveryProvenance",
    "RetryStage",
    "STABLE_OUTCOME_CODES",
]
```

- [ ] **Step 5: Run focused tests and verify GREEN**

Run: `.venv\Scripts\python.exe -m pytest tests/test_hh_autopilot_state_machine.py -q`

Expected: `4 passed`.

- [ ] **Step 6: Commit**

```powershell
git add work_hunter/hh_autopilot tests/test_hh_autopilot_state_machine.py
git commit -m "feat: add HH autopilot state contracts"
```

### Task 2: Validated Configuration And Canonical Policy Hash

**Files:**
- Create: `work_hunter/hh_autopilot/config.py`
- Modify: `work_hunter/config.py:75-180`
- Test: `tests/test_hh_autopilot_config.py`

**Interfaces:**
- Consumes: `AuthorizationKind` from Task 1.
- Produces: `AutopilotSettings`, `AccountSettings`, `ValidationMode`, `default_autopilot_config()`, `parse_autopilot_settings(config)`, `validate_run_mode(...)`, `policy_hash(...)`.

- [ ] **Step 1: Write failing tests for defaults, bounds, run modes, and hash invalidation**

```python
from copy import deepcopy

import pytest

from work_hunter.config import default_config
from work_hunter.hh_autopilot.config import (
    AutopilotConfigError,
    PolicyMaterial,
    ValidationMode,
    parse_autopilot_settings,
    policy_hash,
    validate_run_mode,
)


def test_autopilot_defaults_are_disabled_and_bounded() -> None:
    settings = parse_autopilot_settings(default_config())
    account = settings.accounts[0]
    assert account.enabled is False
    assert settings.limits.daily_success == 50
    assert settings.search.per_page == 100
    assert settings.search.max_pages == 20


def test_invalid_lease_window_is_rejected() -> None:
    config = default_config()
    config["sources"]["hh"]["autopilot"]["lease"] = {
        "ttl_seconds": 30,
        "request_timeout_seconds": 30,
        "renewal_margin_seconds": 45,
    }
    with pytest.raises(AutopilotConfigError, match="lease"):
        parse_autopilot_settings(config)


def test_canary_needs_literal_confirmation_not_grant() -> None:
    settings = parse_autopilot_settings(default_config())
    validate_run_mode(
        settings,
        account_id="default",
        mode=ValidationMode.CANARY,
        has_grant=False,
        literal_confirmed=True,
    )


def test_filter_change_invalidates_policy_hash() -> None:
    settings = parse_autopilot_settings(default_config())
    material = PolicyMaterial(
        effective_auth_profile_id="default",
        candidate_profile={"desired_roles": ["python"]},
        candidate_profile_version="profile-v1",
        resumes=[{"id": "r1", "title": "Python", "content_hash": "resume-v1"}],
        presets={},
        secret_versions={"access_token": 1},
    )
    before = policy_hash(settings, "default", material)
    changed = deepcopy(default_config())
    changed["sources"]["hh"]["autopilot"]["filters"]["excluded_keywords"] = ["1c"]
    after = policy_hash(parse_autopilot_settings(changed), "default", material)
    assert before != after
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_hh_autopilot_config.py -q`

Expected: FAIL because autopilot defaults and parser do not exist.

- [ ] **Step 3: Add the exact default configuration**

```python
# work_hunter/hh_autopilot/config.py
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class AutopilotConfigError(ValueError):
    pass


class ValidationMode(StrEnum):
    AUTONOMOUS = "autonomous"
    MANUAL = "manual"
    CANARY = "canary"
    SHADOW = "shadow"
    RECOVERY = "recovery"


@dataclass(frozen=True)
class AccountSettings:
    profile_id: str
    candidate_profile_id: str
    enabled: bool = False
    paused: bool = False
    authorization_generation: int | None = None
    resume_queries: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class SearchSettings:
    include_recommendations: bool = True
    per_page: int = 100
    max_pages: int = 20
    max_results_per_run: int = 2000


@dataclass(frozen=True)
class LimitSettings:
    daily_success: int = 50
    per_run_success: int = 10
    administrative_max_daily_success: int = 200
    send_delay_min_seconds: int = 45
    send_delay_max_seconds: int = 120


@dataclass(frozen=True)
class LeaseSettings:
    ttl_seconds: int = 120
    request_timeout_seconds: int = 30
    renewal_margin_seconds: int = 45


@dataclass(frozen=True)
class AutopilotSettings:
    timezone: str
    accounts: tuple[AccountSettings, ...]
    schedule: dict[str, Any]
    search: SearchSettings
    filters: dict[str, Any]
    limits: LimitSettings
    ranking: dict[str, Any]
    retry: dict[str, Any]
    lease: LeaseSettings
    application: dict[str, Any]
    browser: dict[str, Any]
    notifications: dict[str, Any]
    retention: dict[str, Any]


@dataclass(frozen=True)
class PolicyMaterial:
    effective_auth_profile_id: str
    candidate_profile: dict[str, Any]
    candidate_profile_version: str
    resumes: list[dict[str, Any]]
    presets: dict[str, dict[str, Any]]
    model_id: str = ""
    prompt_versions: dict[str, str] = field(default_factory=dict)
    cover_letter_template_version: str = ""
    transport_identity: dict[str, Any] = field(default_factory=dict)
    secret_versions: dict[str, int] = field(default_factory=dict)


def default_autopilot_config() -> dict[str, Any]:
    return {
        "timezone": "Europe/Moscow",
        "accounts": [{
            "profile_id": "default",
            "candidate_profile_id": "default",
            "enabled": False,
            "paused": False,
            "authorization_generation": None,
            "resume_queries": [{"resume_id": "published:*", "preset_names": []}],
        }],
        "schedule": {"days": [1, 2, 3, 4, 5, 6, 7], "start": "08:00", "end": "21:00", "interval_minutes": 60},
        "search": {"include_recommendations": True, "per_page": 100, "max_pages": 20, "max_results_per_run": 2000},
        "filters": {
            "excluded_keywords": [], "required_keywords": [], "allowed_role_families": [],
            "areas": [], "remote": "any", "schedules": [], "employment_types": [],
            "experience_levels": [], "languages": [], "citizenships": [],
            "required_application_capabilities": [], "minimum_salary": 0,
            "salary_currency": "RUR", "unknown_salary": "allow", "use_employer_blacklist": True,
        },
        "limits": {"daily_success": 50, "per_run_success": 10, "administrative_max_daily_success": 200, "send_delay_min_seconds": 45, "send_delay_max_seconds": 120},
        "ranking": {
            "minimum_score": 60, "ai_mode": "borderline", "borderline_low": 50,
            "borderline_high": 70, "minimum_ai_confidence": 0.7, "ai_detail": "light",
            "ai_failure_policy": "retry",
            "weights": {"role": 0.30, "skills": 0.30, "experience": 0.15, "salary": 0.10, "work_format": 0.10, "area": 0.05, "industry": 0.0},
        },
        "retry": {"max_attempts": 4, "base_delay_seconds": 60, "max_delay_seconds": 3600, "jitter_ratio": 0.25, "auth_recovery_attempts": 1, "reconciliation_delay_seconds": 30, "reconciliation_checks": 3},
        "lease": {"ttl_seconds": 120, "request_timeout_seconds": 30, "renewal_margin_seconds": 45},
        "application": {"resume_policy": "best_resume_only", "cover_letter_mode": "template", "screening_mode": "profile_grounded", "form_mode": "profile_grounded", "captcha_mode": "manual_handoff", "challenge_expiry_hours": 24},
        "browser": {"headless": True, "navigation_timeout_seconds": 30},
        "notifications": {"challenge": True, "run_failure": True},
        "retention": {"challenge_artifact_days": 7, "event_days": 180},
    }
```

- [ ] **Step 4: Implement parsing, validation, and canonical hashing**

```python
def _bounded(name: str, value: Any, low: int, high: int) -> int:
    parsed = int(value)
    if not low <= parsed <= high:
        raise AutopilotConfigError(f"{name} must be in {low}..{high}")
    return parsed


def parse_autopilot_settings(config: dict[str, Any]) -> AutopilotSettings:
    raw = copy.deepcopy(config.get("sources", {}).get("hh", {}).get("autopilot") or default_autopilot_config())
    accounts = tuple(AccountSettings(**item) for item in raw["accounts"])
    search = SearchSettings(**raw["search"])
    limits = LimitSettings(**raw["limits"])
    lease = LeaseSettings(**raw["lease"])
    if lease.ttl_seconds < lease.request_timeout_seconds + lease.renewal_margin_seconds:
        raise AutopilotConfigError("lease ttl must cover request timeout plus renewal margin")
    _bounded("search.per_page", search.per_page, 1, 100)
    _bounded("search.max_pages", search.max_pages, 1, 100)
    _bounded("limits.administrative_max_daily_success", limits.administrative_max_daily_success, 1, 200)
    if not 1 <= limits.per_run_success <= limits.daily_success <= limits.administrative_max_daily_success:
        raise AutopilotConfigError("application limits are inconsistent")
    if limits.send_delay_min_seconds > limits.send_delay_max_seconds:
        raise AutopilotConfigError("minimum send delay exceeds maximum")
    if len({item.profile_id for item in accounts}) != len(accounts):
        raise AutopilotConfigError("account profile ids must be unique")
    return AutopilotSettings(
        timezone=str(raw["timezone"]), accounts=accounts, schedule=dict(raw["schedule"]),
        search=search, filters=dict(raw["filters"]), limits=limits,
        ranking=dict(raw["ranking"]), retry=dict(raw["retry"]), lease=lease,
        application=dict(raw["application"]), browser=dict(raw["browser"]),
        notifications=dict(raw["notifications"]), retention=dict(raw["retention"]),
    )


def validate_run_mode(
    settings: AutopilotSettings,
    *,
    account_id: str,
    mode: ValidationMode,
    has_grant: bool,
    literal_confirmed: bool,
) -> None:
    account = next((item for item in settings.accounts if item.profile_id == account_id), None)
    if account is None:
        raise AutopilotConfigError(f"Unknown HH autopilot account: {account_id}")
    if mode is ValidationMode.AUTONOMOUS and not (account.enabled and has_grant):
        raise AutopilotConfigError("autonomous run requires enabled account and active grant")
    if mode in {ValidationMode.MANUAL, ValidationMode.CANARY} and not literal_confirmed:
        raise AutopilotConfigError(f"{mode.value} run requires literal confirmation")


def policy_hash(settings: AutopilotSettings, account_id: str, material: PolicyMaterial) -> str:
    account = next(item for item in settings.accounts if item.profile_id == account_id)
    account_policy = {key: value for key, value in asdict(account).items() if key not in {"enabled", "paused", "authorization_generation"}}
    payload = {
        "account": account_policy,
        "timezone": settings.timezone,
        "schedule": settings.schedule,
        "search": asdict(settings.search),
        "filters": settings.filters,
        "limits": asdict(settings.limits),
        "ranking": settings.ranking,
        "retry": settings.retry,
        "lease": asdict(settings.lease),
        "application": settings.application,
        "browser": settings.browser,
        "effective_auth_profile_id": material.effective_auth_profile_id,
        "candidate_profile": material.candidate_profile,
        "candidate_profile_version": material.candidate_profile_version,
        "resumes": sorted(material.resumes, key=lambda item: str(item.get("id") or "")),
        "presets": material.presets,
        "model_id": material.model_id,
        "prompt_versions": material.prompt_versions,
        "cover_letter_template_version": material.cover_letter_template_version,
        "transport_identity": material.transport_identity,
        "secret_versions": material.secret_versions,
    }
    encoded = json.dumps(
        canonicalize_policy_payload(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
```

Implement the entire validation contract, not only the short invariant sample above:

- recursively reject unknown object keys;
- require JSON booleans for boolean fields and fixed enums for every mode;
- enforce every numeric bound from the design, including retry/reconciliation/navigation/retention;
- validate IANA timezone, unique weekdays `1..7`, `HH:MM`, `start <= end`, interval, score/confidence relationships, finite non-negative weights with positive sum, and delay ordering;
- normalize and reject duplicates in every array after case/ID normalization; enforce account, mapping, preset, keyword, role, HH ID, dictionary enum, language, and capability cardinality/length domains;
- require existing authentication/candidate profiles and presets, validate presets with the existing HH search schema, resolve at least one published resume, reject duplicate expanded resume/preset mappings, and require a usable API or authenticated-browser application transport;
- allow an empty preset list only when recommendations are enabled.

`canonicalize_policy_payload()` sorts only arrays whose order has no meaning, normalizes identifiers/strings exactly as validation does, and raises if a key outside the dedicated `secret_versions` object looks like a raw credential (`token`, `cookie`, `password`, `secret`, authorization header, proxy password). The dedicated object contains stable local integer version IDs instead of values. Add tests proving that enabled/paused/notification/log/retention-runtime changes do not widen a grant, while auth-profile mapping, candidate/profile version, resume content hash, expanded preset, search/filter/ranking/model/prompt, quota/timezone/schedule/delay/retry, cover-letter template, form/CAPTCHA policy, transport identity, browser proxy identity, administrative maximum, or secret-version change does change the hash. Add an array-order invariance test for semantically unordered lists.

- [ ] **Step 5: Wire defaults into the existing config**

```python
# work_hunter/config.py
from .hh_autopilot.config import default_autopilot_config

# inside default_config()["sources"]["hh"]
"autopilot": default_autopilot_config(),
```

Update masked config merging so `authorization_generation` is preserved from stored state and rejected from ordinary user patches.

- [ ] **Step 6: Run focused and existing config tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_hh_autopilot_config.py tests/test_config.py tests/test_config_fork_safety.py -q`

Expected: all tests pass.

- [ ] **Step 7: Commit**

```powershell
git add work_hunter/hh_autopilot/config.py work_hunter/config.py tests/test_hh_autopilot_config.py
git commit -m "feat: define HH autopilot configuration"
```

### Task 3: Account-Aware Application Identity Migration

**Files:**
- Create: `work_hunter/migrations/0002_account_aware_applications.sql`
- Modify: `work_hunter/storage.py:726-790,1080-1245`
- Modify: `work_hunter/models.py:360-380`
- Modify: `work_hunter/services.py:913-965`
- Test: `tests/test_hh_autopilot_migrations.py`

**Interfaces:**
- Produces: account-aware `applications` uniqueness `(account_profile_id, job_id, resume_id)`.
- Produces: `Storage.save_application(..., account_profile_id="legacy")` and `Storage.get_application(..., account_profile_id=None, resume_id=None)` compatibility API.

- [ ] **Step 1: Write the failing legacy-migration and two-account tests**

```python
import sqlite3

from work_hunter.models import Job
from work_hunter.storage import Storage


def test_application_history_is_account_aware(tmp_path) -> None:
    storage = Storage(tmp_path / "db.sqlite3")
    job_id = storage.upsert_job(Job(source="hh", source_id="v1", url="https://hh.ru/vacancy/v1", title="Python"))
    storage.save_application(job_id, "applied", account_profile_id="a", resume_id="r1")
    storage.save_application(job_id, "applied", account_profile_id="b", resume_id="r1")
    rows = storage.list_applications()
    assert {(row.account_profile_id, row.resume_id) for row in rows} == {("a", "r1"), ("b", "r1")}


def test_packaged_migration_rebuilds_legacy_unique_job_table(tmp_path) -> None:
    db = tmp_path / "legacy.sqlite3"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE jobs (id INTEGER PRIMARY KEY, source TEXT, source_id TEXT, url TEXT, title TEXT);
        CREATE TABLE applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'applied',
            notes TEXT NOT NULL DEFAULT '',
            applied_at TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT ''
        );
        INSERT INTO jobs VALUES (1, 'hh', 'v1', 'u', 't');
        INSERT INTO applications(job_id, status) VALUES (1, 'applied');
    """)
    conn.commit()
    conn.close()
    storage = Storage(db)
    row = storage.conn.execute("SELECT account_profile_id FROM applications WHERE job_id=1").fetchone()
    assert row["account_profile_id"] == "legacy"


def test_single_unambiguous_legacy_hh_profile_is_reassigned(app_factory, legacy_db) -> None:
    app = app_factory(legacy_db, hh_account_profiles={"work": {"access_token": "masked"}})
    assert app.storage.list_legacy_application_identities() == []
    assert app.storage.list_applications()[0].account_profile_id == "work"


def test_ambiguous_profiles_leave_a_visible_sentinel_report(app_factory, legacy_db) -> None:
    app = app_factory(legacy_db, hh_account_profiles={"a": {}, "b": {}})
    report = app.application_identity_migration_report()
    assert report["sentinel_count"] == 1
    assert report["rows"][0]["account_profile_id"] == "legacy"
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_hh_autopilot_migrations.py -q`

Expected: FAIL because `applications.job_id` is still unique and `Application` has no account identity.

- [ ] **Step 3: Make compatibility columns exist before versioned migrations**

```python
# work_hunter/storage.py
def _migrate(self) -> None:
    with self._schema_transaction():
        self._execute_sql_script(BASE_SCHEMA_SQL)
        self._ensure_backbone_columns()
        self._apply_migrations()
        self._ensure_backbone_columns()
```

- [ ] **Step 4: Add the table-rebuild migration**

```sql
-- work_hunter/migrations/0002_account_aware_applications.sql
ALTER TABLE applications RENAME TO applications_legacy_0002;

CREATE TABLE applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_profile_id TEXT NOT NULL DEFAULT 'legacy',
    job_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'applied',
    notes TEXT NOT NULL DEFAULT '',
    applied_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    source_id TEXT NOT NULL DEFAULT '',
    resume_id TEXT NOT NULL DEFAULT '',
    resume_hash TEXT NOT NULL DEFAULT '',
    plan_id INTEGER,
    transport TEXT NOT NULL DEFAULT '',
    sent_at TEXT NOT NULL DEFAULT '',
    result_json TEXT NOT NULL DEFAULT '{}',
    error TEXT NOT NULL DEFAULT '',
    autopilot_run_id INTEGER,
    autopilot_item_id INTEGER,
    autopilot_attempt_id INTEGER,
    FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE,
    UNIQUE(account_profile_id, job_id, resume_id)
);

INSERT INTO applications (
    id, account_profile_id, job_id, status, notes, applied_at, updated_at,
    source, source_id, resume_id, resume_hash, plan_id, transport, sent_at,
    result_json, error
)
SELECT
    id, 'legacy', job_id, status, notes, applied_at, updated_at,
    source, source_id, resume_id, resume_hash, plan_id, transport, sent_at,
    result_json, error
FROM applications_legacy_0002;

DROP TABLE applications_legacy_0002;
CREATE INDEX idx_applications_account_sent ON applications(account_profile_id, sent_at);

ALTER TABLE hh_application_attempts ADD COLUMN account_profile_id TEXT NOT NULL DEFAULT 'legacy';
ALTER TABLE hh_application_attempts ADD COLUMN autopilot_run_id INTEGER;
ALTER TABLE hh_application_attempts ADD COLUMN autopilot_item_id INTEGER;
ALTER TABLE hh_application_attempts ADD COLUMN autopilot_attempt_id INTEGER;
ALTER TABLE hh_application_attempts ADD COLUMN authorization_kind TEXT NOT NULL DEFAULT '';
ALTER TABLE hh_application_attempts ADD COLUMN authorization_ref TEXT NOT NULL DEFAULT '';
ALTER TABLE hh_application_attempts ADD COLUMN policy_hash TEXT NOT NULL DEFAULT '';
ALTER TABLE hh_application_attempts ADD COLUMN delivery_certainty TEXT NOT NULL DEFAULT '';
ALTER TABLE hh_application_attempts ADD COLUMN dispatched_at TEXT NOT NULL DEFAULT '';
ALTER TABLE hh_application_attempts ADD COLUMN finished_at TEXT NOT NULL DEFAULT '';
```

- [ ] **Step 5: Extend the model and storage API without breaking aggregate readers**

```python
# work_hunter/models.py
@dataclass
class Application:
    id: int
    job_id: int
    status: str
    notes: str = ""
    applied_at: str = ""
    updated_at: str = ""
    account_profile_id: str = "legacy"
    resume_id: str = ""
```

Change `save_application()` to insert/upsert on `(account_profile_id, job_id, resume_id)`. When `get_application()` receives no account, return the newest aggregate row; when account/resume are supplied, query the exact identity.

Add `list_legacy_application_identities()` and `reassign_legacy_application_account(profile_id)`. During `WorkHunter` startup, reassign sentinel rows only when configuration proves there is exactly one effective HH authentication profile; with zero or multiple possible profiles, leave them as `legacy` and expose a masked `application_identity_migration_report()` for explicit later reassignment. Never guess among multiple accounts.

- [ ] **Step 6: Run migration, storage, and report tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_hh_autopilot_migrations.py tests/test_storage.py tests/test_storage_backbone.py tests/test_services.py -q`

Expected: all tests pass and existing aggregate reports retain their counts.

- [ ] **Step 7: Commit**

```powershell
git add work_hunter/migrations/0002_account_aware_applications.sql work_hunter/storage.py work_hunter/models.py work_hunter/services.py tests/test_hh_autopilot_migrations.py
git commit -m "feat: make application history account aware"
```

### Task 4: Durable Runtime Schema And Atomic Repository

**Files:**
- Create: `work_hunter/migrations/0003_hh_autopilot_runtime.sql`
- Create: `work_hunter/hh_autopilot/repository.py`
- Test: `tests/test_hh_autopilot_repository.py`

**Interfaces:**
- Produces: `AutopilotRepository`, `StaleWrite`, `LostLease`, `RunRecord`, `ItemRecord`, `LeaseRecord`, and `ChallengeRecord`.
- Produces atomic `create_run()`, `finish_run()`, `create_item()`, `transition_item()`, `append_event()`, `get_item()`, `list_events()`, and due-item queries.
- Owns every write to the new runtime tables; callers never issue ad-hoc SQL against them.

- [ ] **Step 1: Write failing atomic-transition and compare-and-swap tests**

```python
# tests/test_hh_autopilot_repository.py
import pytest

from work_hunter.hh_autopilot.repository import AutopilotRepository, StaleWrite
from work_hunter.hh_autopilot.types import AutopilotState
from work_hunter.storage import Storage


@pytest.fixture
def repo(tmp_path):
    storage = Storage(tmp_path / "work-hunter.db")
    yield AutopilotRepository(storage)
    storage.close()


def test_transition_updates_item_and_event_in_one_commit(repo) -> None:
    run = repo.create_run("default", trigger="manual", policy_hash="hash")
    item = repo.create_item(
        origin_run_id=run.id,
        account_id="default",
        vacancy_id="v-1",
        resume_id="r-1",
        query_key="preset:python",
    )

    changed = repo.transition_item(
        item.id,
        expected_version=item.version,
        target=AutopilotState.ELIGIBLE,
        reason="hard_filters_passed",
        metadata={"authorization": "Bearer secret", "score": 82},
    )

    assert changed.state is AutopilotState.ELIGIBLE
    assert changed.version == item.version + 1
    assert repo.list_events(item.id)[-1]["reason_code"] == "hard_filters_passed"
    assert "secret" not in repo.list_events(item.id)[-1]["metadata_json"]


def test_stale_version_cannot_write_or_append_an_event(repo) -> None:
    run = repo.create_run("default", trigger="manual", policy_hash="hash")
    item = repo.create_item(run.id, "default", "v-1", "r-1", "preset:python")
    repo.transition_item(item.id, item.version, AutopilotState.ELIGIBLE, "passed")

    with pytest.raises(StaleWrite):
        repo.transition_item(item.id, item.version, AutopilotState.SKIPPED, "stale")

    assert [event["reason_code"] for event in repo.list_events(item.id)] == [
        "discovered",
        "passed",
    ]
```

- [ ] **Step 2: Run the repository tests and verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_hh_autopilot_repository.py -q`

Expected: FAIL because migration `0003_hh_autopilot_runtime.sql` and `AutopilotRepository` do not exist.

- [ ] **Step 3: Add the complete runtime migration**

Create `work_hunter/migrations/0003_hh_autopilot_runtime.sql` with these concrete tables and constraints:

```sql
CREATE TABLE hh_autopilot_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_profile_id TEXT NOT NULL,
    trigger TEXT NOT NULL CHECK (trigger IN ('schedule','manual','shadow','retry','recovery','canary')),
    status TEXT NOT NULL CHECK (status IN ('created','running','stop_requested','completed','failed','interrupted','cancelled')),
    grant_id INTEGER,
    policy_hash TEXT NOT NULL,
    fencing_token INTEGER NOT NULL DEFAULT 0,
    counters_json TEXT NOT NULL DEFAULT '{}',
    error TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX idx_hh_autopilot_runs_account_status
    ON hh_autopilot_runs(account_profile_id, status, created_at);

CREATE TABLE hh_autopilot_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    origin_run_id INTEGER NOT NULL REFERENCES hh_autopilot_runs(id),
    last_run_id INTEGER NOT NULL REFERENCES hh_autopilot_runs(id),
    account_profile_id TEXT NOT NULL,
    vacancy_id TEXT NOT NULL,
    resume_id TEXT NOT NULL,
    query_key TEXT NOT NULL,
    state TEXT NOT NULL,
    retry_stage TEXT NOT NULL DEFAULT 'eligibility',
    version INTEGER NOT NULL DEFAULT 0,
    filter_json TEXT NOT NULL DEFAULT '{}',
    deterministic_score REAL,
    ai_json TEXT NOT NULL DEFAULT '{}',
    application_attempt_count INTEGER NOT NULL DEFAULT 0,
    reconciliation_count INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT NOT NULL DEFAULT '',
    last_outcome_code TEXT NOT NULL DEFAULT '',
    active_attempt_id INTEGER REFERENCES hh_application_attempts(id),
    challenge_id INTEGER,
    idempotency_key TEXT NOT NULL,
    published_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(account_profile_id, idempotency_key)
);
CREATE INDEX idx_hh_autopilot_items_due
    ON hh_autopilot_items(account_profile_id, state, next_attempt_at);

CREATE TABLE hh_autopilot_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER REFERENCES hh_autopilot_runs(id),
    item_id INTEGER NOT NULL REFERENCES hh_autopilot_items(id),
    previous_state TEXT NOT NULL,
    next_state TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX idx_hh_autopilot_events_item ON hh_autopilot_events(item_id, id);

CREATE TABLE hh_autopilot_leases (
    account_profile_id TEXT PRIMARY KEY,
    owner_token TEXT NOT NULL,
    fencing_token INTEGER NOT NULL,
    expires_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE hh_autopilot_quota_reservations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    attempt_id INTEGER UNIQUE REFERENCES hh_application_attempts(id),
    run_id INTEGER REFERENCES hh_autopilot_runs(id),
    source TEXT NOT NULL CHECK (source IN ('dispatch','external_sync')),
    remote_negotiation_id TEXT UNIQUE,
    account_profile_id TEXT NOT NULL,
    timezone TEXT NOT NULL,
    local_date TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('reserved','held','consumed','released')),
    fencing_token INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    resolved_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX idx_hh_autopilot_quota_day
    ON hh_autopilot_quota_reservations(account_profile_id, local_date, state);

CREATE TABLE hh_autopilot_challenges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL CHECK (scope IN ('item','account')),
    challenge_type TEXT NOT NULL,
    account_profile_id TEXT NOT NULL,
    item_id INTEGER REFERENCES hh_autopilot_items(id),
    reservation_id INTEGER REFERENCES hh_autopilot_quota_reservations(id),
    sanitized_url TEXT NOT NULL DEFAULT '',
    screenshot_path TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL CHECK (status IN ('open','in_progress','resolved','dismissed','expired')),
    expires_at TEXT NOT NULL DEFAULT '',
    resolution_at TEXT NOT NULL DEFAULT '',
    resolution_actor TEXT NOT NULL DEFAULT '',
    resolution_action TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE hh_autopilot_search_cycles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_profile_id TEXT NOT NULL,
    policy_hash TEXT NOT NULL,
    origin_run_id INTEGER NOT NULL REFERENCES hh_autopilot_runs(id),
    owner_run_id INTEGER NOT NULL REFERENCES hh_autopilot_runs(id),
    claim_version INTEGER NOT NULL DEFAULT 0,
    fencing_token INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running','complete','failed','interrupted','superseded')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX idx_hh_autopilot_cycles_recovery
    ON hh_autopilot_search_cycles(account_profile_id, policy_hash, status);

CREATE TABLE hh_autopilot_search_checkpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id INTEGER NOT NULL REFERENCES hh_autopilot_search_cycles(id) ON DELETE CASCADE,
    resume_id TEXT NOT NULL,
    query_key TEXT NOT NULL,
    next_page INTEGER NOT NULL DEFAULT 0,
    reported_total INTEGER,
    unique_vacancy_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL CHECK (status IN ('pending','running','complete','failed')),
    updated_at TEXT NOT NULL,
    UNIQUE(cycle_id, resume_id, query_key)
);

CREATE TABLE hh_autopilot_search_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id INTEGER NOT NULL REFERENCES hh_autopilot_search_cycles(id) ON DELETE CASCADE,
    checkpoint_id INTEGER NOT NULL REFERENCES hh_autopilot_search_checkpoints(id) ON DELETE CASCADE,
    account_profile_id TEXT NOT NULL,
    resume_id TEXT NOT NULL,
    query_key TEXT NOT NULL,
    vacancy_id TEXT NOT NULL,
    page INTEGER NOT NULL,
    normalized_json TEXT NOT NULL,
    discovered_at TEXT NOT NULL,
    UNIQUE(cycle_id, resume_id, query_key, vacancy_id)
);
CREATE INDEX idx_hh_autopilot_search_results_vacancy
    ON hh_autopilot_search_results(account_profile_id, vacancy_id);

CREATE TABLE hh_autopilot_shadow_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES hh_autopilot_runs(id),
    account_profile_id TEXT NOT NULL,
    vacancy_id TEXT NOT NULL,
    resume_id TEXT NOT NULL,
    filter_json TEXT NOT NULL,
    deterministic_score REAL,
    ai_json TEXT NOT NULL DEFAULT '{}',
    would_apply INTEGER NOT NULL CHECK (would_apply IN (0,1)),
    created_at TEXT NOT NULL,
    UNIQUE(run_id, vacancy_id, resume_id)
);

CREATE TABLE hh_autopilot_account_state (
    account_profile_id TEXT PRIMARY KEY,
    blocked_until TEXT NOT NULL DEFAULT '',
    block_reason TEXT NOT NULL DEFAULT '',
    hh_reset_json TEXT NOT NULL DEFAULT '{}',
    last_scheduled_at TEXT NOT NULL DEFAULT '',
    next_scheduled_at TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE hh_application_account_guards (
    account_profile_id TEXT NOT NULL,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    owner_attempt_id INTEGER REFERENCES hh_application_attempts(id),
    first_resume_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active','applied','external_applied')),
    application_id INTEGER REFERENCES applications(id),
    application_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(account_profile_id, source, source_id)
);

CREATE TABLE hh_autopilot_grants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_profile_id TEXT NOT NULL,
    scope TEXT NOT NULL CHECK (scope = 'applications'),
    policy_hash TEXT NOT NULL,
    generation INTEGER NOT NULL,
    active INTEGER NOT NULL CHECK (active IN (0,1)),
    actor TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    revoked_at TEXT NOT NULL DEFAULT '',
    UNIQUE(account_profile_id, scope, generation)
);
CREATE UNIQUE INDEX idx_hh_autopilot_active_grant
    ON hh_autopilot_grants(account_profile_id, scope) WHERE active = 1;

CREATE TABLE hh_autopilot_one_shot_authorizations (
    reference_id TEXT PRIMARY KEY,
    authorization_type TEXT NOT NULL CHECK (authorization_type IN ('manual','canary')),
    account_profile_id TEXT NOT NULL,
    max_success INTEGER NOT NULL CHECK (max_success >= 1),
    consumed_success INTEGER NOT NULL DEFAULT 0 CHECK (consumed_success >= 0 AND consumed_success <= max_success),
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    finished_at TEXT NOT NULL DEFAULT '',
    CHECK (authorization_type != 'canary' OR max_success = 1)
);

CREATE TABLE hh_autopilot_one_shot_targets (
    authorization_ref TEXT NOT NULL REFERENCES hh_autopilot_one_shot_authorizations(reference_id) ON DELETE CASCADE,
    resume_id TEXT NOT NULL,
    vacancy_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending','active','succeeded','closed')),
    active_attempt_id INTEGER REFERENCES hh_application_attempts(id),
    updated_at TEXT NOT NULL,
    PRIMARY KEY(authorization_ref, resume_id, vacancy_id)
);

CREATE TABLE hh_autopilot_controls (
    scope_type TEXT NOT NULL CHECK (scope_type IN ('global','account')),
    scope_id TEXT NOT NULL,
    paused INTEGER NOT NULL DEFAULT 0 CHECK (paused IN (0,1)),
    kill_switch INTEGER NOT NULL DEFAULT 0 CHECK (kill_switch IN (0,1)),
    version INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(scope_type, scope_id)
);
```

After the table declarations, add the circular challenge reference only at repository level; SQLite cannot add that foreign key after table creation, so `hh_autopilot_items.challenge_id` remains an indexed logical reference. Add `CREATE INDEX idx_hh_autopilot_items_challenge ON hh_autopilot_items(challenge_id);`.

- [ ] **Step 4: Implement repository records, redaction, transactions, and CAS transitions**

```python
# work_hunter/hh_autopilot/repository.py
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterator

from work_hunter.storage import Storage, redact_for_storage

from .state_machine import assert_transition
from .types import AutopilotState, RetryStage


class StaleWrite(RuntimeError):
    pass


class LostLease(RuntimeError):
    pass


@dataclass(frozen=True)
class ItemRecord:
    id: int
    origin_run_id: int
    last_run_id: int
    account_id: str
    vacancy_id: str
    resume_id: str
    state: AutopilotState
    retry_stage: RetryStage
    version: int
    active_attempt_id: int | None


class AutopilotRepository:
    def __init__(self, storage: Storage):
        self.storage = storage
        self.conn = storage.conn

    @contextmanager
    def immediate(self) -> Iterator[None]:
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.conn.rollback()
            raise
        else:
            self.conn.commit()

    def transition_item(
        self,
        item_id: int,
        expected_version: int,
        target: AutopilotState,
        reason: str,
        metadata: dict[str, Any] | None = None,
        *,
        run_id: int | None = None,
        fencing_token: int | None = None,
    ) -> ItemRecord:
        with self.immediate():
            current = self._item_for_update(item_id)
            if current.version != expected_version:
                raise StaleWrite(f"item {item_id} version changed")
            if fencing_token is not None:
                self._assert_fence(current.account_id, fencing_token)
            assert_transition(current.state, target)
            cursor = self.conn.execute(
                "UPDATE hh_autopilot_items SET state=?, version=version+1, "
                "last_run_id=COALESCE(?, last_run_id), last_outcome_code=?, updated_at=? "
                "WHERE id=? AND version=?",
                (target.value, run_id, reason, _utc_now(), item_id, expected_version),
            )
            if cursor.rowcount != 1:
                raise StaleWrite(f"item {item_id} compare-and-swap failed")
            self._insert_event(
                item_id=item_id,
                run_id=run_id,
                previous=current.state,
                target=target,
                reason=reason,
                metadata=redact_for_storage(metadata or {}),
            )
            return self._item_for_update(item_id)
```

Implement the remaining declared methods with parameterized SQL. `create_item()` derives `idempotency_key` as SHA-256 over `account_id + "\0" + resume_id + "\0" + vacancy_id + "\0apply"`, inserts the initial `discovered` event in the same transaction, and returns the existing row on a uniqueness collision. Every method returning JSON parses it into a copy; no mutable dictionary is shared between records.

- [ ] **Step 5: Add migration-shape and rollback assertions**

Extend `tests/test_hh_autopilot_repository.py` to query `PRAGMA table_info` and `PRAGMA index_list` for every table above. Add a trigger that aborts an event insert, assert `transition_item()` raises, then assert the item version and state did not change.

- [ ] **Step 6: Run focused and migration tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_hh_autopilot_repository.py tests/test_hh_autopilot_migrations.py -q`

Expected: all tests pass, including rollback and index assertions.

- [ ] **Step 7: Commit**

```powershell
git add work_hunter/migrations/0003_hh_autopilot_runtime.sql work_hunter/hh_autopilot/repository.py tests/test_hh_autopilot_repository.py
git commit -m "feat: add durable HH autopilot repository"
```

### Task 5: Durable Grants And Typed Authorization Boundary

**Files:**
- Create: `work_hunter/hh_autopilot/authorization.py`
- Modify: `work_hunter/hh_autopilot/repository.py`
- Modify: `work_hunter/safety.py`
- Modify: `work_hunter/config.py`
- Test: `tests/test_hh_autopilot_authorization.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `HHAutopilotAuthorizer.enable()`, `.disable()`, `.reconcile_config_projection()`, `.issue_live_authorization()`, `.set_pause()`, `.set_kill_switch()`, and `.clear_kill_switch()`.
- Produces: `require_hh_dispatch_authorization(auth, checks) -> None` accepting only `LiteralConfirmation | LiveAuthorization`.
- `hh_autopilot_grants` is authoritative. `enabled` and `authorization_generation` in JSON are service-managed projections and never grant authority by themselves.

- [ ] **Step 1: Write failing grant, edit-rejection, invalidation, and revocation tests**

```python
# tests/test_hh_autopilot_authorization.py
import copy

import pytest

from work_hunter.hh_autopilot.authorization import (
    AuthorizationDenied,
    HHAutopilotAuthorizer,
)
from work_hunter.hh_autopilot.types import LiveAuthorization


def test_direct_config_edit_cannot_create_a_grant(authorizer, valid_config) -> None:
    edited = copy.deepcopy(valid_config)
    account = edited["sources"]["hh"]["autopilot"]["accounts"][0]
    account["enabled"] = True
    account["authorization_generation"] = 999

    with pytest.raises(AuthorizationDenied, match="authorization_state_mismatch"):
        authorizer.issue_live_authorization("default", edited, run_id=1, fencing_token=7)

    assert authorizer.repository.active_grant("default", "applications") is None


def test_confirmed_enable_creates_policy_bound_generation(authorizer, valid_config) -> None:
    enabled = authorizer.enable(
        ["default"],
        valid_config,
        confirm=True,
        actor="cli",
        source="hh autopilot enable",
    )
    run = authorizer.repository.create_run("default", trigger="manual", policy_hash=enabled.policy_hashes["default"])
    auth = authorizer.issue_live_authorization(
        "default", enabled.config, run_id=run.id, fencing_token=11
    )

    assert isinstance(auth, LiveAuthorization)
    assert auth.scope == "applications"
    assert auth.policy_hash == enabled.policy_hashes["default"]


def test_policy_change_invalidates_existing_generation(authorizer, enabled_config) -> None:
    changed = copy.deepcopy(enabled_config)
    changed["sources"]["hh"]["autopilot"]["limits"]["daily_success"] = 51

    with pytest.raises(AuthorizationDenied, match="policy_hash_mismatch"):
        authorizer.issue_live_authorization("default", changed, run_id=2, fencing_token=12)


def test_disable_and_kill_revoke_instead_of_pausing_generation(authorizer, enabled_config) -> None:
    disabled = authorizer.disable(["default"], enabled_config, confirm=True, actor="cli")
    assert disabled.config["sources"]["hh"]["autopilot"]["accounts"][0]["enabled"] is False
    assert authorizer.repository.active_grant("default", "applications") is None

    reenabled = authorizer.enable(["default"], disabled.config, confirm=True, actor="cli", source="test")
    authorizer.set_kill_switch("account", "default", reenabled.config, confirm=True, actor="cli")
    assert authorizer.repository.active_grant("default", "applications") is None
```

Add an `enable --all` fixture with two accounts where the second is structurally invalid. Assert no grant is created and neither account projection changes. Add a test that `paused=true` preserves the active grant but blocks `issue_live_authorization()`. Add a test that clearing a kill switch does not recreate a grant.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_hh_autopilot_authorization.py -q`

Expected: FAIL because the authorizer and typed safety guard do not exist.

- [ ] **Step 3: Add repository grant/control transactions**

```python
# work_hunter/hh_autopilot/repository.py
def create_grants(
    self,
    requests: list[tuple[str, str, str, str]],
) -> dict[str, int]:
    """Insert all (account, policy_hash, actor, source) generations atomically."""
    with self.immediate():
        generations: dict[str, int] = {}
        for account_id, policy_hash, actor, source in requests:
            current = self.conn.execute(
                "SELECT COALESCE(MAX(generation), 0) FROM hh_autopilot_grants "
                "WHERE account_profile_id=? AND scope='applications'",
                (account_id,),
            ).fetchone()[0]
            generation = int(current) + 1
            self.conn.execute(
                "UPDATE hh_autopilot_grants SET active=0, revoked_at=? "
                "WHERE account_profile_id=? AND scope='applications' AND active=1",
                (_utc_now(), account_id),
            )
            self.conn.execute(
                "INSERT INTO hh_autopilot_grants "
                "(account_profile_id, scope, policy_hash, generation, active, actor, source, created_at) "
                "VALUES (?, 'applications', ?, ?, 1, ?, ?, ?)",
                (account_id, policy_hash, generation, actor, source, _utc_now()),
            )
            generations[account_id] = generation
        return generations

def revoke_grants(self, account_ids: list[str], *, actor: str, reason: str) -> None:
    with self.immediate():
        self.conn.executemany(
            "UPDATE hh_autopilot_grants SET active=0, revoked_at=? "
            "WHERE account_profile_id=? AND scope='applications' AND active=1",
            [(_utc_now(), account_id) for account_id in account_ids],
        )
```

Add atomic reads for the active grant, controls, current run stop request, and account state. `set_kill_switch()` updates the control row and revokes every affected application grant in the same SQLite transaction. `disable()` revokes and changes any `running` run to `stop_requested` in the same transaction.

- [ ] **Step 4: Protect managed config fields on ordinary saves**

```python
# work_hunter/config.py
AUTOPILOT_MANAGED_ACCOUNT_KEYS = frozenset({"enabled", "authorization_generation"})


def preserve_managed_autopilot_fields(
    stored: dict[str, Any], submitted: dict[str, Any]
) -> dict[str, Any]:
    merged = copy.deepcopy(submitted)
    stored_accounts = {
        str(item.get("profile_id")): item
        for item in (((stored.get("sources") or {}).get("hh") or {}).get("autopilot") or {}).get("accounts", [])
        if isinstance(item, dict)
    }
    submitted_accounts = (((merged.get("sources") or {}).get("hh") or {}).get("autopilot") or {}).get("accounts", [])
    for item in submitted_accounts:
        old = stored_accounts.get(str(item.get("profile_id")))
        if old:
            for key in AUTOPILOT_MANAGED_ACCOUNT_KEYS:
                item[key] = copy.deepcopy(old.get(key))
        else:
            item["enabled"] = False
            item["authorization_generation"] = None
    return merged
```

Call this from ordinary `save_config()` and `update_config()` before persistence. Add a private `update_autopilot_authorization_projection()` used only by `HHAutopilotAuthorizer`; it acquires the existing process/file lock, updates exactly the selected account projections, and writes through the existing atomic replacement path. On startup/config load, call `reconcile_config_projection()`: any observed `enabled=false` revokes the active generation before a scheduler decision; `enabled=true` with no exact active generation is reported as `authorization_state_mismatch` and is never repaired by creating a grant.

- [ ] **Step 5: Implement the authorizer and exact typed safety matrix**

```python
# work_hunter/hh_autopilot/authorization.py
class AuthorizationDenied(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class HHAutopilotAuthorizer:
    def enable(self, account_ids, config, *, confirm, actor, source):
        if confirm is not True:
            raise AuthorizationDenied("literal_confirmation_required")
        parsed = parse_autopilot_config(config)
        selected = [parsed.account(account_id) for account_id in account_ids]
        hashes = {account.profile_id: policy_hash(parsed, account.profile_id) for account in selected}
        generations = self.repository.create_grants(
            [(account_id, hashes[account_id], actor, source) for account_id in account_ids]
        )
        try:
            projected = self._write_projection(config, generations, enabled=True)
        except BaseException:
            self.repository.revoke_exact_generations(generations, actor=actor, reason="projection_write_failed")
            raise
        return EnableResult(projected, generations, hashes)

    def issue_live_authorization(self, account_id, config, *, run_id, fencing_token):
        account = parse_autopilot_config(config).account(account_id)
        grant = self.repository.active_grant(account_id, "applications")
        if grant is None or account.authorization_generation != grant.generation:
            raise AuthorizationDenied("authorization_state_mismatch")
        if not account.enabled or account.paused:
            raise AuthorizationDenied("autopilot_disabled_or_paused")
        if grant.policy_hash != policy_hash(config, account_id):
            raise AuthorizationDenied("policy_hash_mismatch")
        if self.repository.kill_switch_active(account_id):
            raise AuthorizationDenied("kill_switch_active")
        return LiveAuthorization(
            grant_id=grant.id,
            scope="applications",
            account_id=account_id,
            run_id=run_id,
            fencing_token=fencing_token,
            policy_hash=grant.policy_hash,
        )
```

```python
# work_hunter/safety.py
from .hh_autopilot.types import LiteralConfirmation, LiveAuthorization


def require_hh_dispatch_authorization(
    authorization: LiteralConfirmation | LiveAuthorization,
    *,
    account_id: str,
    lease_account_id: str,
    fencing_token: int,
) -> None:
    if not isinstance(authorization, (LiteralConfirmation, LiveAuthorization)):
        raise PermissionError("typed HH application authorization required")
    if authorization.account_id != account_id or lease_account_id != account_id:
        raise PermissionError("HH account authorization mismatch")
    if isinstance(authorization, LiveAuthorization):
        if authorization.scope != "applications" or authorization.fencing_token != fencing_token:
            raise PermissionError("invalid live HH application authorization")
```

Keep `require_mutation_confirmation()` for existing manual operations. Do not add a recovery authorization variant to this mutation guard; read-only reconciliation uses immutable `RecoveryProvenance` and a separate repository check.

The database creates all selected account generations in one transaction. A projection-write exception compensates by revoking those exact generations. A process crash between the database commit and projection write leaves `enabled=false` or a generation mismatch, so pre-dispatch validation fails closed and startup reconciliation revokes the orphaned generation.

- [ ] **Step 6: Test crash-safe mismatch and managed-field preservation**

Inject a failure after the JSON projection write but before normal enable completion. Assert the resulting projection cannot dispatch unless the exact active database generation exists. Exercise `save_config()` with submitted `enabled=true` and `authorization_generation=999`; assert stored managed values remain unchanged. Exercise a direct file edit followed by `reconcile_config_projection()`; assert it cannot create a grant.

- [ ] **Step 7: Run authorization, config, and safety suites**

Run: `.venv\Scripts\python.exe -m pytest tests/test_hh_autopilot_authorization.py tests/test_config.py tests/test_mcp_safety.py tests/test_strict_type_boundaries.py -q`

Expected: all tests pass; arbitrary dictionaries, booleans, and MCP-shaped input are rejected by the dispatch guard.

- [ ] **Step 8: Commit**

```powershell
git add work_hunter/hh_autopilot/authorization.py work_hunter/hh_autopilot/repository.py work_hunter/safety.py work_hunter/config.py tests/test_hh_autopilot_authorization.py tests/test_config.py tests/test_mcp_safety.py tests/test_strict_type_boundaries.py
git commit -m "feat: enforce durable HH application grants"
```

### Task 6: Fenced Account Lease, Quota Reservations, And Cooldown

**Files:**
- Modify: `work_hunter/hh_autopilot/repository.py`
- Modify: `work_hunter/hh_autopilot/types.py`
- Test: `tests/test_hh_autopilot_repository.py`

**Interfaces:**
- Produces: `acquire_lease()`, `renew_lease()`, `release_lease()`, `assert_fence()`, and `recover_stale_applying()`.
- Produces: `LeaseKeeper.ensure_current()` for renewal before any bounded remote phase when the safety margin is reached.
- Produces: `reserve_quota()`, `hold_reservation()`, `consume_reservation()`, `release_reservation()`, and `sync_external_quota()`.
- Produces: `set_account_cooldown()`, `assert_dispatch_available()`, and `assert_timezone_change_safe()`.

- [ ] **Step 1: Write failing fencing, quota-race, held-reservation, and cooldown tests**

```python
# tests/test_hh_autopilot_repository.py
from datetime import datetime, timedelta, timezone

import pytest

from work_hunter.hh_autopilot.repository import CooldownActive, LostLease, QuotaExceeded


UTC = timezone.utc


def test_expired_owner_is_fenced_after_takeover(repo) -> None:
    now = datetime(2026, 7, 15, 8, tzinfo=UTC)
    first = repo.acquire_lease("default", "owner-a", ttl_seconds=120, now=now)
    assert repo.acquire_lease("default", "owner-b", ttl_seconds=120, now=now) is None

    second = repo.acquire_lease(
        "default", "owner-b", ttl_seconds=120, now=now + timedelta(seconds=121)
    )
    assert second.fencing_token == first.fencing_token + 1
    with pytest.raises(LostLease):
        repo.assert_fence("default", first.fencing_token, now=now + timedelta(seconds=122))


def test_reservation_counts_reserved_held_and_consumed(repo, prepared_attempt) -> None:
    lease = repo.acquire_lease("default", "owner", ttl_seconds=120)
    reservation = repo.reserve_quota(
        account_id="default",
        run_id=prepared_attempt.run_id,
        attempt_id=prepared_attempt.id,
        timezone_name="Europe/Moscow",
        daily_limit=1,
        run_limit=1,
        fencing_token=lease.fencing_token,
    )
    repo.hold_reservation(reservation.id, lease.fencing_token)

    with pytest.raises(QuotaExceeded):
        repo.reserve_quota(
            account_id="default",
            run_id=prepared_attempt.run_id,
            attempt_id=prepared_attempt.other_id,
            timezone_name="Europe/Moscow",
            daily_limit=1,
            run_limit=1,
            fencing_token=lease.fencing_token,
        )


def test_account_cooldown_blocks_every_ready_item(repo) -> None:
    lease = repo.acquire_lease("default", "owner", ttl_seconds=120)
    repo.set_account_cooldown(
        "default",
        blocked_until="2026-07-16T00:00:00+00:00",
        reason="hh_daily_limit",
        fencing_token=lease.fencing_token,
    )
    with pytest.raises(CooldownActive, match="hh_daily_limit"):
        repo.assert_dispatch_available(
            "default", lease.fencing_token, now="2026-07-15T12:00:00+00:00"
        )
```

Add a two-connection race using `ThreadPoolExecutor`: both workers call `reserve_quota()` with a daily/run limit of one; assert exactly one reservation exists and the other raises `QuotaExceeded`. Add local-day rollover assertions using `Europe/Moscow`. Add `sync_external_quota()` twice with the same remote negotiation ID and assert one consumed row. Add `assert_timezone_change_safe()` tests for an active grant and an unresolved `reserved|held` row.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_hh_autopilot_repository.py -q`

Expected: FAIL on missing lease/quota methods.

- [ ] **Step 3: Implement monotonic fencing lease acquisition and renewal**

```python
# work_hunter/hh_autopilot/repository.py
def acquire_lease(self, account_id, owner_token, *, ttl_seconds, now=None):
    current_time = now or datetime.now(timezone.utc)
    expires_at = current_time + timedelta(seconds=ttl_seconds)
    with self.immediate():
        row = self.conn.execute(
            "SELECT * FROM hh_autopilot_leases WHERE account_profile_id=?",
            (account_id,),
        ).fetchone()
        if row and row["owner_token"] != owner_token and _parse_utc(row["expires_at"]) > current_time:
            return None
        fencing_token = int(row["fencing_token"]) if row and row["owner_token"] == owner_token else int(row["fencing_token"] if row else 0) + 1
        self.conn.execute(
            "INSERT INTO hh_autopilot_leases "
            "(account_profile_id, owner_token, fencing_token, expires_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(account_profile_id) DO UPDATE SET "
            "owner_token=excluded.owner_token, fencing_token=excluded.fencing_token, "
            "expires_at=excluded.expires_at, updated_at=excluded.updated_at",
            (account_id, owner_token, fencing_token, _iso(expires_at), _iso(current_time)),
        )
        return LeaseRecord(account_id, owner_token, fencing_token, expires_at)

def renew_lease(self, lease, *, ttl_seconds, now=None):
    current_time = now or datetime.now(timezone.utc)
    with self.immediate():
        cursor = self.conn.execute(
            "UPDATE hh_autopilot_leases SET expires_at=?, updated_at=? "
            "WHERE account_profile_id=? AND owner_token=? AND fencing_token=? AND expires_at>?",
            (_iso(current_time + timedelta(seconds=ttl_seconds)), _iso(current_time),
             lease.account_id, lease.owner_token, lease.fencing_token, _iso(current_time)),
        )
        if cursor.rowcount != 1:
            raise LostLease(lease.account_id)
        return self._lease(lease.account_id)
```

`assert_fence()` checks account, token, owner expiry, and current time inside the same `BEGIN IMMEDIATE` transaction as each protected write. `release_lease()` deletes only the exact owner/token row. `recover_stale_applying()` changes every abandoned `applying` item to `reconciling` with an event; it never chooses `ready`.

`LeaseKeeper.ensure_current()` renews when `expires_at - now <= renewal_margin_seconds` and otherwise returns the current lease. Search calls it before each page, ranking calls it before a remote AI evaluation, reconciliation calls it before each negotiation page, browser handling calls it before bounded navigation, and executor renews unconditionally immediately before dispatch preparation. Add a fake-clock test spanning more than one TTL and assert the fencing token stays constant while expiry advances.

- [ ] **Step 4: Implement atomic reservation states and account cooldown**

```python
ACTIVE_QUOTA_STATES = ("reserved", "held", "consumed")


def reserve_quota(
    self,
    *,
    account_id: str,
    run_id: int,
    attempt_id: int,
    timezone_name: str,
    daily_limit: int,
    run_limit: int,
    fencing_token: int,
    now: datetime | None = None,
):
    instant = now or datetime.now(timezone.utc)
    local_date = instant.astimezone(ZoneInfo(timezone_name)).date().isoformat()
    with self.immediate():
        self._assert_fence(account_id, fencing_token, instant)
        self._assert_cooldown(account_id, instant)
        daily = self.conn.execute(
            "SELECT COUNT(*) FROM hh_autopilot_quota_reservations "
            "WHERE account_profile_id=? AND local_date=? AND state IN ('reserved','held','consumed')",
            (account_id, local_date),
        ).fetchone()[0]
        per_run = self.conn.execute(
            "SELECT COUNT(*) FROM hh_autopilot_quota_reservations "
            "WHERE run_id=? AND state IN ('reserved','held','consumed')",
            (run_id,),
        ).fetchone()[0]
        if daily >= daily_limit or per_run >= run_limit:
            raise QuotaExceeded(account_id)
        cursor = self.conn.execute(
            "INSERT INTO hh_autopilot_quota_reservations "
            "(attempt_id, run_id, source, account_profile_id, timezone, local_date, state, fencing_token, created_at) "
            "VALUES (?, ?, 'dispatch', ?, ?, ?, 'reserved', ?, ?)",
            (attempt_id, run_id, account_id, timezone_name, local_date, fencing_token, _iso(instant)),
        )
        return self._reservation(int(cursor.lastrowid))
```

State updates use compare-and-swap (`reserved -> held|consumed|released`, `held -> consumed|released`) plus the current fence. `consume_reservation()` and `release_reservation()` are idempotent only when the row is already in that exact terminal state; contradictory terminal changes raise `StaleWrite`. A reservation tied to an `applying`, `reconciling`, or unresolved ambiguous item has no time-based expiry path.

For `rate_limited` and `hh_daily_limit`, update the item, release its current reservation, and set `hh_autopilot_account_state` in one transaction. Parse an HH reset or `Retry-After` when present; otherwise use retry backoff for rate limits and the next account-local midnight for the daily limit.

- [ ] **Step 5: Enforce lease timing and timezone invariants in config validation**

Extend `parse_autopilot_config()` so `lease.ttl_seconds >= lease.request_timeout_seconds + lease.renewal_margin_seconds`. Before authorizing a timezone change, call `assert_timezone_change_safe()`, which rejects while any active grant or any `reserved|held` reservation exists for the account.

- [ ] **Step 6: Run repository, authorization, and config tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_hh_autopilot_repository.py tests/test_hh_autopilot_authorization.py tests/test_hh_autopilot_config.py -q`

Expected: all tests pass, including the concurrent limit-one race and stale-owner rejection.

- [ ] **Step 7: Commit**

```powershell
git add work_hunter/hh_autopilot/repository.py work_hunter/hh_autopilot/types.py work_hunter/hh_autopilot/config.py tests/test_hh_autopilot_repository.py tests/test_hh_autopilot_config.py
git commit -m "feat: fence HH dispatch and reserve quota"
```

### Task 7: Multi-Page Search, Checkpoints, Deduplication, And Shadow Isolation

**Files:**
- Create: `work_hunter/hh_autopilot/search.py`
- Modify: `work_hunter/hh_autopilot/types.py`
- Modify: `work_hunter/hh_autopilot/repository.py`
- Modify: `work_hunter/hh_transport/api_session.py`
- Modify: `work_hunter/sources/hh.py`
- Test: `tests/test_hh_autopilot_search.py`
- Test: `tests/test_hh_transport.py`

**Interfaces:**
- Produces: `SearchPage(items, page, pages, per_page, total)`, `SearchRequest`, `NormalizedVacancy`, and `HHSearchProvider.collect()`.
- Adds compatibility-preserving `HHApiSession.search_vacancies_page()` and `HHApplyClient.search_vacancies_page()`; existing `search_vacancies()` continues returning only items.
- Repository owns `create_search_cycle()`, `claim_search_cycle()`, `commit_search_page()`, `complete_checkpoint()`, `supersede_search_cycle()`, and `save_shadow_result()`.

- [ ] **Step 1: Write failing pagination, resume, dedupe, and shadow tests**

```python
# tests/test_hh_autopilot_search.py
from work_hunter.hh_autopilot.search import HHSearchProvider, SearchPage, SearchRequest


class FakePages:
    def __init__(self, pages):
        self.pages = pages
        self.requested = []

    def search_vacancies_page(self, params):
        page = int(params["page"])
        self.requested.append(page)
        return self.pages[page]


def test_search_walks_pages_and_deduplicates_by_hh_id(repo, run_and_lease) -> None:
    transport = FakePages([
        SearchPage([{"id": "1"}, {"id": "2"}], 0, 3, 2, 5),
        SearchPage([{"id": "2"}, {"id": "3"}], 1, 3, 2, 5),
        SearchPage([{"id": "4"}], 2, 3, 2, 5),
    ])
    provider = HHSearchProvider(transport, repo)

    result = provider.collect(
        SearchRequest(
            account_id="default",
            run_id=run_and_lease.run.id,
            resume_id="r-1",
            query_key="preset:python",
            params={"text": "python"},
            per_page=2,
            max_pages=20,
            remaining_budget=20,
            policy_hash="hash",
            fencing_token=run_and_lease.lease.fencing_token,
            mode="live",
        )
    )

    assert transport.requested == [0, 1, 2]
    assert [vacancy.id for vacancy in result.vacancies] == ["1", "2", "3", "4"]


def test_interrupted_checkpoint_resumes_at_next_uncommitted_page(repo, run_and_lease) -> None:
    cycle = repo.create_search_cycle(
        account_id="default", run_id=run_and_lease.run.id, policy_hash="hash",
        fencing_token=run_and_lease.lease.fencing_token,
    )
    checkpoint = repo.ensure_search_checkpoint(cycle.id, "r-1", "preset:python")
    repo.commit_search_page(
        checkpoint.id, expected_next_page=0, page=SearchPage([{"id": "1"}], 0, 2, 1, 2),
        normalized=[normalized("1")], fencing_token=run_and_lease.lease.fencing_token,
    )
    repo.interrupt_search_cycle(cycle.id, run_and_lease.lease.fencing_token)

    replacement = recover_run_and_lease(repo, account_id="default")
    claimed = repo.claim_search_cycle(
        cycle.id,
        expected_claim_version=cycle.claim_version,
        new_run_id=replacement.run.id,
        policy_hash="hash",
        fencing_token=replacement.lease.fencing_token,
    )
    assert repo.get_checkpoint(claimed.id, "r-1", "preset:python").next_page == 1


def test_shadow_search_persists_input_but_cannot_create_live_rows(repo, shadow_run_and_lease, fake_pages) -> None:
    HHSearchProvider(fake_pages, repo).collect(shadow_request(shadow_run_and_lease))
    assert repo.count_items() == 0
    assert repo.count_guards() == 0
    assert repo.count_reservations() == 0
    assert repo.count_search_results(shadow_run_and_lease.run.id) > 0
    assert repo.count_shadow_results(shadow_run_and_lease.run.id) == 0
```

Add stop-rule tests for `max_pages`, short page, reported `total`, and run-wide budget. Add duplicate-across-two-presets and duplicate-across-two-resumes cases. Add recommendation mode using `query_key="recommendations"`. Add policy mismatch: `claim_search_cycle()` marks the old cycle `superseded`, resets only never-dispatched nonterminal candidates to `discovered`, and returns no claim.

- [ ] **Step 2: Run focused search and transport tests and verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_hh_autopilot_search.py tests/test_hh_transport.py -q`

Expected: FAIL because page metadata and search-cycle methods do not exist.

- [ ] **Step 3: Preserve HH page metadata at the transport boundary**

```python
# work_hunter/hh_transport/api_session.py
from work_hunter.hh_autopilot.types import SearchPage


def search_vacancies_page(self, params: dict[str, Any]) -> SearchPage:
    data = self.request_json("GET", "/vacancies", params=params)
    return SearchPage(
        items=list(data.get("items") or []),
        page=int(data.get("page") or 0),
        pages=int(data.get("pages") or 0),
        per_page=int(data.get("per_page") or params.get("per_page") or 0),
        total=int(data.get("found") or 0),
    )

def search_vacancies(self, params: dict[str, Any]) -> list[dict[str, Any]]:
    return self.search_vacancies_page(params).items
```

Expose the same page method from `HHApplyClient`. Add a recommendation-page adapter that normalizes any HH recommendation/similar-vacancy response to `SearchPage`; keep `get_similar_vacancies()` unchanged for existing callers.

- [ ] **Step 4: Implement normalized search requests and exact stop rules**

```python
# work_hunter/hh_autopilot/search.py
@dataclass(frozen=True)
class SearchRequest:
    account_id: str
    run_id: int
    resume_id: str
    query_key: str
    params: dict[str, object]
    per_page: int
    max_pages: int
    remaining_budget: int
    policy_hash: str
    fencing_token: int
    mode: str


class HHSearchProvider:
    def collect(self, request: SearchRequest) -> SearchResult:
        cycle = self.repository.get_or_create_owned_cycle(request)
        checkpoint = self.repository.ensure_search_checkpoint(
            cycle.id, request.resume_id, request.query_key
        )
        unique: dict[str, NormalizedVacancy] = {}
        page_number = checkpoint.next_page
        while page_number < request.max_pages and len(unique) < request.remaining_budget:
            page = self._fetch_page(request, page_number)
            normalized = [normalize_vacancy(item) for item in page.items]
            normalized = [item for item in normalized if item.id]
            self.repository.commit_search_page(
                checkpoint.id,
                expected_next_page=page_number,
                page=page,
                normalized=normalized,
                fencing_token=request.fencing_token,
                mode=request.mode,
            )
            for vacancy in normalized:
                unique.setdefault(vacancy.id, vacancy)
                if len(unique) == request.remaining_budget:
                    break
            page_number += 1
            reached_total = page.total > 0 and page_number * request.per_page >= page.total
            if len(page.items) < request.per_page or reached_total or page_number >= page.pages > 0:
                break
        self.repository.complete_checkpoint(checkpoint.id, request.fencing_token)
        return SearchResult(tuple(unique.values()), page_number)
```

Normalization stores only fields used by filters/ranking plus the existing `Job` representation. Sanitize HTML before event evidence. Preserve the first discovery provenance while merging later query/resume references.

- [ ] **Step 5: Make checkpoint advancement and normalized persistence one transaction**

`commit_search_page()` must verify the cycle's current owner run, claim version, policy hash, and fencing token; persist normalized rows in `hh_autopilot_search_results`; insert live `discovered` items only in live mode; then compare-and-swap `next_page = page + 1`. Roll back all of it if any row fails. Shadow mode creates no item, guard, attempt, reservation, or final `hh_autopilot_shadow_results` row at this stage; the engine writes that final row only after filters/ranking complete. A live duplicate returns the existing item; it never creates a second idempotency key for the same account/resume/vacancy operation.

`claim_search_cycle()` is available only to an authorized `recovery` run with a matching active grant and policy hash. The grant-independent application recovery query is separate and cannot invoke it.

- [ ] **Step 6: Run search, transport, and existing HH source tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_hh_autopilot_search.py tests/test_hh_transport.py tests/test_sources.py -q`

Expected: all tests pass; existing list-returning search callers remain compatible.

- [ ] **Step 7: Commit**

```powershell
git add work_hunter/hh_autopilot/search.py work_hunter/hh_autopilot/types.py work_hunter/hh_autopilot/repository.py work_hunter/hh_transport/api_session.py work_hunter/sources/hh.py tests/test_hh_autopilot_search.py tests/test_hh_transport.py
git commit -m "feat: add restart-safe HH pagination"
```

### Task 8: Hard Filters, Deterministic Ranking, AI Gate, And Resume Selection

**Files:**
- Create: `work_hunter/hh_autopilot/policy.py`
- Create: `work_hunter/hh_autopilot/ranking.py`
- Modify: `work_hunter/hh_autopilot/types.py`
- Modify: `work_hunter/hh_autopilot/repository.py`
- Test: `tests/test_hh_autopilot_policy.py`
- Test: `tests/test_hh_autopilot_ranking.py`

**Interfaces:**
- Produces: `HardFilter.evaluate(vacancy, resume, candidate, context) -> FilterDecision`.
- Produces: `DeterministicRanker.score() -> RankScore`, `StructuredAIRanker.evaluate() -> AIDecision`, and `RankingPolicy.decide() -> RankingDecision`.
- Produces: `select_resume(candidates, resume_policy) -> RankedCandidate` with the approved stable tie-break order.

- [ ] **Step 1: Write a failing hard-filter decision table**

```python
# tests/test_hh_autopilot_policy.py
import pytest

from work_hunter.hh_autopilot.policy import HardFilter


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"vacancy": {"archived": True}}, "hard_filter:vacancy_closed"),
        ({"history": {"already_applied": True}}, "hard_filter:already_applied"),
        ({"blacklist": {"vacancy": True}}, "hard_filter:vacancy_blacklist"),
        ({"blacklist": {"employer": True}}, "hard_filter:employer_blacklist"),
        ({"vacancy": {"name": "Senior Bitrix"}}, "hard_filter:excluded_keywords"),
        ({"vacancy": {"area_id": "2"}}, "hard_filter:area"),
        ({"vacancy": {"remote": False}}, "hard_filter:remote"),
        ({"vacancy": {"schedule": "flyInFlyOut"}}, "hard_filter:schedule"),
        ({"vacancy": {"employment": "part"}}, "hard_filter:employment_type"),
        ({"vacancy": {"experience": "moreThan6"}}, "hard_filter:experience"),
        ({"vacancy": {"salary": {"from": 100000, "currency": "RUR"}}}, "hard_filter:minimum_salary"),
        ({"candidate": {"languages": []}}, "hard_filter:languages"),
        ({"candidate": {"citizenships": []}}, "hard_filter:citizenships"),
        ({"vacancy": {"application_capability": "form"}}, "hard_filter:required_application_capabilities"),
    ],
)
def test_each_hard_filter_has_a_stable_reason(base_case, change, reason) -> None:
    case = deep_merge(base_case, change)
    decision = HardFilter(case["filters"]).evaluate(
        case["vacancy"], case["resume"], case["candidate"], case["context"]
    )
    assert decision.passed is False
    assert decision.reason == reason
    assert "access_token" not in str(decision.evidence)


def test_unknown_required_candidate_fact_skips_only_that_vacancy(base_case) -> None:
    base_case["candidate"].pop("citizenships")
    decision = HardFilter(base_case["filters"]).evaluate(
        base_case["vacancy"], base_case["resume"], base_case["candidate"], base_case["context"]
    )
    assert decision.reason == "missing_required_data"
    assert decision.evidence["field"] == "citizenships"
```

Add passing tests for `unknown_salary=allow`, rejecting tests for `unknown_salary=reject`, required keyword semantics, allowed role families, relocation policy, and configurable stop words. Assert filtering one item does not change another item in the same run.

- [ ] **Step 2: Write failing deterministic/AI-mode/tie-break tests**

```python
# tests/test_hh_autopilot_ranking.py
from work_hunter.hh_autopilot.ranking import RankingPolicy, select_resume


def test_hard_rejection_never_calls_ai(base_rank_case, ai_spy) -> None:
    base_rank_case.filter_decision = rejected("hard_filter:excluded_keywords")
    decision = RankingPolicy(base_rank_case.config, ai_spy).decide(base_rank_case)
    assert decision.ready is False
    assert ai_spy.calls == []


def test_borderline_requires_confident_structured_ai(base_rank_case, ai_spy) -> None:
    base_rank_case.score = 60
    ai_spy.result = {"suitable": True, "confidence": 0.85, "evidence": ["Python"], "reasons": []}
    decision = RankingPolicy(borderline_config(), ai_spy).decide(base_rank_case)
    assert decision.ready is True
    assert decision.reason == "ai_suitable"


def test_best_resume_tie_break_is_stable() -> None:
    selected = select_resume(
        [candidate("r-2", score=80, confidence=.8), candidate("r-1", score=80, confidence=.8)],
        resume_policy="best_resume_only",
    )
    assert selected.resume_id == "r-1"
```

Add assertions for `off`, `borderline`, and `all`; confidence below threshold with each `ai_failure_policy`; deterministic score boundaries 0 and 100; normalized weights; non-finite component rejection; ordering by score, AI confidence, publication time, vacancy ID, then resume ID; and `per_resume` returning every eligible resume while still preserving the account-vacancy guard requirement.

- [ ] **Step 3: Run focused tests and verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_hh_autopilot_policy.py tests/test_hh_autopilot_ranking.py -q`

Expected: FAIL because policy and ranking modules do not exist.

- [ ] **Step 4: Implement ordered hard filters with sanitized evidence**

```python
# work_hunter/hh_autopilot/policy.py
@dataclass(frozen=True)
class FilterDecision:
    passed: bool
    reason: str
    evidence: dict[str, object]


class HardFilter:
    def evaluate(self, vacancy, resume, candidate, context) -> FilterDecision:
        checks = (
            self._vacancy_open,
            self._history,
            self._blacklists,
            self._keywords_and_roles,
            self._area_and_relocation,
            self._work_format,
            self._experience,
            self._salary,
            self._candidate_constraints,
            self._application_capabilities,
        )
        for check in checks:
            decision = check(vacancy, resume, candidate, context)
            if decision is not None:
                return decision
        return FilterDecision(True, "hard_filters_passed", {"checks": [fn.__name__ for fn in checks]})
```

Normalize text with Unicode case-folding and whitespace collapse. Search title, description, employer name, and extracted key skills for keyword rules. Evidence stores matched normalized terms and stable IDs, never raw tokens, cookies, full personal facts, or unbounded HTML.

- [ ] **Step 5: Implement fixed-component normalized scoring**

```python
# work_hunter/hh_autopilot/ranking.py
COMPONENTS = ("role", "skills", "experience", "salary", "work_format", "area", "industry")


class DeterministicRanker:
    def score(self, vacancy, resume, candidate, weights) -> RankScore:
        raw = {
            "role": role_match(vacancy, resume, candidate),
            "skills": skill_match(vacancy, resume),
            "experience": experience_match(vacancy, candidate),
            "salary": salary_match(vacancy, candidate),
            "work_format": work_format_match(vacancy, candidate),
            "area": area_match(vacancy, candidate),
            "industry": industry_match(vacancy, candidate),
        }
        total_weight = sum(float(weights[name]) for name in COMPONENTS)
        normalized = {name: float(weights[name]) / total_weight for name in COMPONENTS}
        value = sum(raw[name] * normalized[name] for name in COMPONENTS)
        return RankScore(score=round(max(0.0, min(100.0, value)), 4), components=raw, weights=normalized)
```

Each component returns `0..100` from explicit vacancy/resume/profile values. Missing optional ranking data yields a neutral documented component; any fact configured as a hard requirement has already been handled by `HardFilter` and is not inferred here.

- [ ] **Step 6: Implement structured AI output and the exact gate matrix**

Use `work_hunter.llm.structured.send_structured_chat` with a schema requiring:

```python
AI_SCHEMA = {
    "type": "object",
    "required": ["suitable", "confidence", "evidence", "reasons"],
    "properties": {
        "suitable": {"type": "boolean"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "evidence": {"type": "array", "items": {"type": "string", "maxLength": 300}, "maxItems": 20},
        "reasons": {"type": "array", "items": {"type": "string", "maxLength": 300}, "maxItems": 20},
    },
    "additionalProperties": False,
}
```

The prompt tells the model to use only supplied resume/profile/vacancy text. Parse failure, timeout, or schema failure becomes typed `ai_unavailable`; `RankingPolicy` maps it to persisted retry, deterministic fallback, or skip exactly from `ai_failure_policy`. AI cannot change a failed hard-filter decision or deterministic score.

`ai_detail=light` supplies normalized title, role, key skills, short description excerpt, resume title/skills, and explicit preference facts. `ai_detail=heavy` supplies the full sanitized vacancy description plus bounded resume experience/education/skills and explicit candidate facts. Both use the same schema/model identifier and configured prompt version, enforce deterministic input-size limits, and store only bounded evidence/reasons.

- [ ] **Step 7: Persist evidence and choose the best resume before readiness**

Repository methods write filter evidence on `discovered -> eligible`, then score/AI evidence on `eligible -> ranked`. `select_resume()` sorts by `(-score, -confidence, -published_timestamp, vacancy_id, resume_id)` and transitions only the selected candidate to `ready` under `best_resume_only`; the other same-vacancy resume candidates become `skipped` with `not_best_resume`. Under `per_resume`, each qualifying candidate can become ready, but executor serialization still uses the account-vacancy guard.

- [ ] **Step 8: Run policy/ranking plus existing scoring tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_hh_autopilot_policy.py tests/test_hh_autopilot_ranking.py tests/test_scoring_letters.py -q`

Expected: all tests pass.

- [ ] **Step 9: Commit**

```powershell
git add work_hunter/hh_autopilot/policy.py work_hunter/hh_autopilot/ranking.py work_hunter/hh_autopilot/types.py work_hunter/hh_autopilot/repository.py tests/test_hh_autopilot_policy.py tests/test_hh_autopilot_ranking.py
git commit -m "feat: filter and rank HH vacancies"
```

### Task 9: Guarded Executor, Delivery Certainty, Atomic Finalization, And Retry Policy

**Files:**
- Create: `work_hunter/hh_autopilot/executor.py`
- Modify: `work_hunter/hh_autopilot/repository.py`
- Modify: `work_hunter/hh_autopilot/types.py`
- Modify: `work_hunter/hh_transport/errors.py`
- Modify: `work_hunter/hh_transport/api_session.py`
- Modify: `work_hunter/sources/hh.py`
- Test: `tests/test_hh_autopilot_executor.py`
- Test: `tests/test_hh_transport.py`

**Interfaces:**
- Produces: `HHApplicationExecutor.execute(item_id, authorization, lease, now) -> ExecutionResult`.
- Produces: `HHRetryPolicy.classify(outcome, attempt_count, now) -> RetryDecision`.
- Adds `HHApplyClient.apply_outcome() -> DispatchOutcome`; keeps legacy `apply()` result shape for compatibility.
- Every application transport error exposes `DeliveryCertainty`; executor never derives retry safety by matching an exception string.

- [ ] **Step 1: Write failing transport-certainty and response-mapping tests**

```python
# tests/test_hh_transport.py
import requests

from work_hunter.hh_autopilot.types import DeliveryCertainty
from work_hunter.hh_transport.errors import HHNetworkError, HHParseError


def test_post_connect_timeout_is_proven_not_sent(api_session, monkeypatch) -> None:
    monkeypatch.setattr(
        api_session.http,
        "request",
        lambda *args, **kwargs: (_ for _ in ()).throw(requests.ConnectTimeout()),
    )
    with pytest.raises(HHNetworkError) as raised:
        api_session.apply("v-1", "r-1", "")
    assert raised.value.delivery_certainty is DeliveryCertainty.DEFINITELY_NOT_SENT


@pytest.mark.parametrize("error", [requests.ReadTimeout(), requests.ConnectionError()])
def test_post_unknown_or_late_network_failure_is_possibly_sent(api_session, monkeypatch, error) -> None:
    monkeypatch.setattr(
        api_session.http,
        "request",
        lambda *args, **kwargs: (_ for _ in ()).throw(error),
    )
    with pytest.raises(HHNetworkError) as raised:
        api_session.apply("v-1", "r-1", "")
    assert raised.value.delivery_certainty is DeliveryCertainty.POSSIBLY_SENT


def test_malformed_successful_post_response_is_possibly_sent(hh_client, response_factory) -> None:
    hh_client.session.apply = lambda *args: response_factory(201, body=b"not-json")
    outcome = hh_client.apply_outcome("v-1", "r-1", "")
    assert outcome.code == "post_dispatch_parse_error"
    assert outcome.certainty is DeliveryCertainty.POSSIBLY_SENT
```

Add response cases for `201`, `303`, duplicate, closed vacancy, `400` validation, `401`, `403`, `429` with `Retry-After`, retryable `5xx`, HH daily-limit code, screening/form redirect, CAPTCHA, and empty-body success. Assert every returned outcome has an explicit certainty.

- [ ] **Step 2: Write failing executor safety, quota, and atomicity tests**

```python
# tests/test_hh_autopilot_executor.py
from work_hunter.hh_autopilot.types import AutopilotState, DeliveryCertainty, DispatchOutcome


def test_success_prepares_before_post_and_finalizes_once(executor_case) -> None:
    executor_case.transport.outcome = DispatchOutcome(
        code="applied",
        certainty=DeliveryCertainty.DEFINITE_RESPONSE,
        status_code=201,
        location="/negotiations/n-1",
        payload={"id": "n-1"},
    )
    result = executor_case.executor.execute(
        executor_case.item.id,
        executor_case.authorization,
        executor_case.lease,
        now=executor_case.now,
    )

    assert result.state is AutopilotState.APPLIED
    assert executor_case.transport.observed_prepared_state == "applying"
    assert executor_case.repo.count_consumed_reservations() == 1
    assert executor_case.repo.count_applications("default", "v-1", "r-1") == 1
    assert executor_case.repo.get_guard("default", "hh", "v-1").status == "applied"


def test_possibly_sent_never_becomes_retry_ready(executor_case) -> None:
    executor_case.transport.outcome = DispatchOutcome(
        code="post_dispatch_network_error",
        certainty=DeliveryCertainty.POSSIBLY_SENT,
    )
    result = executor_case.executor.execute(
        executor_case.item.id, executor_case.authorization, executor_case.lease
    )
    assert result.state is AutopilotState.RECONCILING
    assert executor_case.repo.active_reservation(result.item_id).state == "held"
    assert executor_case.repo.get_item(result.item_id).next_attempt_at != ""


def test_stale_fence_is_rejected_before_transport(executor_case) -> None:
    executor_case.take_over_lease()
    with pytest.raises(LostLease):
        executor_case.executor.execute(
            executor_case.item.id, executor_case.authorization, executor_case.lease
        )
    assert executor_case.transport.calls == []
```

Add tests that no typed authorization, wrong account, paused/grant mismatch, kill switch, cooldown, quota exhaustion, guard conflict, item-version change, or expired lease reaches transport. Add a database trigger that aborts application upsert during success finalization; assert application, attempt, reservation, item, event, guard, and run counter all roll back together.

- [ ] **Step 3: Run focused executor and transport tests and verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_hh_autopilot_executor.py tests/test_hh_transport.py -q`

Expected: FAIL on missing delivery certainty and executor.

- [ ] **Step 4: Annotate typed transport failures conservatively**

```python
# work_hunter/hh_transport/errors.py
from work_hunter.hh_autopilot.types import DeliveryCertainty


class HHTransportError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str = "hh_transport_error",
        payload: dict[str, Any] | None = None,
        delivery_certainty: DeliveryCertainty = DeliveryCertainty.DEFINITE_RESPONSE,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.payload = payload or {}
        self.delivery_certainty = delivery_certainty
```

In `_network_error(method, path, exc)`, use `DEFINITELY_NOT_SENT` only for a POST `requests.ConnectTimeout` or another explicitly tested failure proving the connection never completed. All other POST exceptions are `POSSIBLY_SENT`; read-only requests remain `DEFINITELY_NOT_SENT` for retry classification. A malformed successful POST response produces `post_dispatch_parse_error/POSSIBLY_SENT`; a malformed GET produces `read_parse_error/DEFINITELY_NOT_SENT`.

- [ ] **Step 5: Add a typed application response adapter**

```python
# work_hunter/sources/hh.py
def apply_outcome(self, vacancy_id: str, resume_id: str, message: str) -> DispatchOutcome:
    try:
        response = self.session.apply(vacancy_id, resume_id, message)
        raw = _response_json_for_dispatch(response)
    except HHTransportError as exc:
        return DispatchOutcome(
            code=_dispatch_error_code(exc),
            certainty=exc.delivery_certainty,
            status_code=exc.status_code,
            retry_after_seconds=_retry_after(exc.payload),
            payload=_sanitized_dispatch_payload(exc.payload),
        )
    return _dispatch_outcome_from_response(response, raw)
```

Map `201` to `applied`; `303` and recognized HTML/form locations to `form_required`; recognized CAPTCHA to `manual_captcha`; recognized knowledge test to `manual_assessment`; account duplicate to `duplicate`; `401` to `auth_expired`; `429` to `rate_limited`; HH application-limit responses to `hh_daily_limit`; closed/forbidden/invalid to their stable codes; and `5xx` to `server_error`. Preserve only sanitized response fields required for reconciliation/challenge display.

- [ ] **Step 6: Prepare attempt, guard, quota, and applying state in one transaction**

Add `AutopilotRepository.prepare_dispatch()` that:

1. verifies the current fence and account cooldown;
2. compares the item version and requires `ready`;
3. acquires or validates `(account_profile_id, 'hh', vacancy_id)` guard according to `best_resume_only|per_resume`;
4. rejects an existing exact `(account, job, resume)` application;
5. inserts one immutable `hh_application_attempts` row with authorization kind/reference, policy hash, and empty delivery certainty, then sets its compatibility `autopilot_attempt_id` to that same new row ID (legacy attempts remain nullable);
6. reserves daily/run quota for that attempt;
7. increments `application_attempt_count`, sets `active_attempt_id`, transitions to `applying`, and appends the event.

All seven actions use one `BEGIN IMMEDIATE`. Return `PreparedDispatch` containing IDs and the updated item version. Renew the lease immediately before calling it, and enforce `request_timeout_seconds` on the remote POST.

`validate_pre_dispatch()` branches on the typed authorization. `LiveAuthorization` requires the exact active grant/generation/policy hash, enabled and not paused, and the scheduler window. `LiteralConfirmation` requires the exact active one-shot account/resume/vacancy record but no durable grant, enabled flag, pause flag, or scheduler window. Both require clear global/account kill switches, a current account lease/fence, no cooldown, available normal quota, item CAS, and the account-vacancy guard. Read-only reconciliation never enters this function. Local finalization verifies immutable attempt provenance, item/fence/reservation identity, but deliberately does not re-check a now-revoked grant or kill switch.

- [ ] **Step 7: Implement complete atomic outcome transactions**

`finalize_applied()` verifies `applying|reconciling`, item version, attempt ID, reservation ID, and fence, then in one transaction:

- upserts `applications` on `(account_profile_id, job_id, resume_id)`;
- updates the existing attempt with `applied`, certainty, masked payload, and timestamps;
- consumes the reservation;
- transitions the item to `applied` and appends its event;
- sets the account-vacancy guard to `applied` and increments its count once;
- increments the run's successful counter once.

For a one-shot manual/canary authorization, the same success transaction marks the exact target `succeeded` and increments `consumed_success`. A canary then becomes inactive after its only target. A manual campaign has an immutable target set and `max_success` equal to its approved bounded campaign cap; it becomes inactive when every target is terminal or the cap is reached. Definite terminal failure closes only that target. Retry/reconciliation may keep that same target active until bounded terminal resolution, but the authorization can never widen to a resume/vacancy absent from `hh_autopilot_one_shot_targets`.

`finalize_definite_failure()` updates attempt, releases reservation, then atomically chooses terminal skip, `retry_wait`, account cooldown, auth challenge, or supported form/challenge outcome. `record_possibly_sent()` updates attempt certainty, changes the reservation to `held`, and transitions to `reconciling`; it never releases quota or creates a ready retry.

- [ ] **Step 8: Implement bounded persisted retry decisions**

```python
# work_hunter/hh_autopilot/executor.py
class HHRetryPolicy:
    def classify(self, outcome, *, attempt_count, now, random_value):
        if outcome.certainty is DeliveryCertainty.POSSIBLY_SENT:
            return RetryDecision.reconcile_at(now + self.reconciliation_delay)
        if outcome.code in PERMANENT_CODES:
            return RetryDecision.skip(outcome.code)
        if attempt_count >= self.config.max_attempts:
            return RetryDecision.dead("retry_exhausted")
        base = min(
            self.config.max_delay_seconds,
            self.config.base_delay_seconds * (2 ** max(0, attempt_count - 1)),
        )
        factor = (1 - self.config.jitter_ratio) + 2 * self.config.jitter_ratio * random_value
        delay = base * factor
        if outcome.retry_after_seconds is not None:
            delay = max(delay, outcome.retry_after_seconds)
        return RetryDecision.retry_at(now + timedelta(seconds=delay))
```

The executor does not sleep. It persists `next_attempt_at`; scheduler dispatches due work. Actual POST count increments during `prepare_dispatch()`. Reconciliation reads and token refreshes do not increment it.

- [ ] **Step 9: Implement the single guarded execution flow**

```python
class HHApplicationExecutor:
    def execute(self, item_id, authorization, lease, now=None):
        self.authorizer.validate_pre_dispatch(authorization, lease, item_id, now=now)
        lease = self.repository.renew_lease(lease, ttl_seconds=self.config.lease.ttl_seconds, now=now)
        prepared = self.repository.prepare_dispatch(
            item_id=item_id,
            authorization=authorization,
            fencing_token=lease.fencing_token,
            limits=self.config.limits,
            timezone_name=self.config.timezone,
            resume_policy=self.config.application.resume_policy,
            now=now,
        )
        outcome = self.transport.apply_outcome(
            prepared.vacancy_id, prepared.resume_id, self.cover_letters.render(prepared)
        )
        return self._finalize(prepared, outcome, lease, now=now)
```

Use existing template/AI cover-letter services through a small injected port. `cover_letter_mode=none` sends an empty message; template and AI output remain grounded in stored candidate/resume/vacancy values. A cover-letter failure before POST follows `definitely_not_sent` retry/skip policy and releases the reservation.

- [ ] **Step 10: Run executor, transport, storage, and letter tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_hh_autopilot_executor.py tests/test_hh_transport.py tests/test_storage.py tests/test_scoring_letters.py -q`

Expected: all tests pass, including the finalization rollback test.

- [ ] **Step 11: Commit**

```powershell
git add work_hunter/hh_autopilot/executor.py work_hunter/hh_autopilot/repository.py work_hunter/hh_autopilot/types.py work_hunter/hh_transport/errors.py work_hunter/hh_transport/api_session.py work_hunter/sources/hh.py tests/test_hh_autopilot_executor.py tests/test_hh_transport.py
git commit -m "feat: execute fenced HH applications safely"
```

### Task 10: Account-Wide Reconciliation And Grant-Independent Recovery Sweep

**Files:**
- Create: `work_hunter/hh_autopilot/reconcile.py`
- Modify: `work_hunter/hh_autopilot/repository.py`
- Modify: `work_hunter/hh_transport/api_session.py`
- Modify: `work_hunter/sources/hh.py`
- Test: `tests/test_hh_autopilot_reconcile.py`

**Interfaces:**
- Produces: `HHApplicationReconciler.reconcile(item_id, provenance, lease, now) -> ReconcileResult`.
- Produces: `HHRecoverySweep.run(account_id=None, now=None) -> RecoveryReport`.
- Produces: `NegotiationSnapshot` and a read-only, paginated account-history reader covering all relevant negotiation states.
- Recovery requires immutable attempt provenance and the normal lease/fence, but no active application grant; it cannot call search or application POST methods.

- [ ] **Step 1: Write the accepted-then-disconnected no-duplicate contract test**

```python
# tests/test_hh_autopilot_reconcile.py
def test_accepted_then_disconnected_reconciles_without_second_post(reconcile_case) -> None:
    reconcile_case.server.accept_application_then_close_connection(
        vacancy_id="v-1", resume_id="r-1", negotiation_id="n-1"
    )
    first = reconcile_case.executor.execute(
        reconcile_case.item.id, reconcile_case.authorization, reconcile_case.lease
    )
    assert first.state.value == "reconciling"
    assert reconcile_case.server.application_post_count == 1

    reconcile_case.disable_and_kill_account()
    report = reconcile_case.recovery.run(account_id="default", now=reconcile_case.after_delay)

    assert report.applied == 1
    assert reconcile_case.server.application_post_count == 1
    assert reconcile_case.repo.count_consumed_reservations() == 1
    assert reconcile_case.repo.count_applications("default", "v-1", "r-1") == 1
```

- [ ] **Step 2: Write failing reconciliation classification and ambiguity tests**

```python
@pytest.mark.parametrize("initial_code", ["duplicate", "post_dispatch_network_error"])
def test_same_resume_current_negotiation_finalizes_applied(reconcile_case, initial_code) -> None:
    case = reconcile_case.with_held_attempt(initial_code)
    case.transport.negotiations = [negotiation("n-1", "v-1", "r-1", case.after_attempt)]
    result = case.reconciler.reconcile(case.item.id, case.provenance, case.lease, case.now)
    assert result.outcome == "applied"
    assert case.repo.active_reservation(case.item.id).state == "consumed"


def test_other_resume_or_predating_negotiation_is_external(reconcile_case) -> None:
    case = reconcile_case.with_held_attempt("duplicate")
    case.transport.negotiations = [negotiation("n-old", "v-1", "r-2", case.before_attempt)]
    result = case.reconciler.reconcile(case.item.id, case.provenance, case.lease, case.now)
    assert result.outcome == "duplicate_external"
    assert case.repo.dispatch_reservation(case.item.id).state == "released"
    assert case.repo.get_guard("default", "hh", "v-1").status == "external_applied"


def test_unclassifiable_negotiation_holds_quota_and_opens_ambiguity(reconcile_case) -> None:
    case = reconcile_case.with_held_attempt("duplicate", reconciliation_count=2)
    case.transport.negotiations = [negotiation("n-?", "v-1", resume_id="", created_at="")]
    result = case.reconciler.reconcile(case.item.id, case.provenance, case.lease, case.now)
    assert result.outcome == "ambiguous_application"
    assert case.repo.dispatch_reservation(case.item.id).state == "held"
    assert case.repo.get_challenge(result.challenge_id).challenge_type == "ambiguous_application"
```

Add cases for: confirmed absence on each check and release-to-ready only after the configured final check; a same-resume negotiation that clearly predates the attempt; today's idempotent external quota sync versus an older external application; auth failure during reconciliation retaining a held reservation and opening one account `manual_auth`; repeated recovery idempotence; stale `applying` conversion; and stale fenced recovery owner rejection.

- [ ] **Step 3: Write failing late ambiguity-resolution tests**

Test all four actions:

```python
@pytest.mark.parametrize(
    ("action", "state", "reservation"),
    [
        ("confirmed_applied", "applied", "consumed"),
        ("confirmed_not_applied_retry", "ready", "released"),
        ("confirmed_not_applied_skip", "skipped", "released"),
        ("retry_reconciliation", "reconciling", "held"),
    ],
)
def test_ambiguity_can_be_resolved_after_expiry(reconcile_case, action, state, reservation) -> None:
    challenge = reconcile_case.expired_ambiguity()
    reconcile_case.repo.resolve_challenge(
        challenge.id, action=action, actor="cli", fencing_token=reconcile_case.lease.fencing_token
    )
    assert reconcile_case.repo.get_item(challenge.item_id).state.value == state
    assert reconcile_case.repo.reservation(challenge.reservation_id).state == reservation
```

Assert generic dead-item requeue raises `unresolved_ambiguity` until one of these legal actions resolves the hold. Dismiss/expiry moves ambiguity to `dead` but does not release its reservation.

Add a non-ambiguous dead-item case: `requeue_dead()` is the one audited administrative exception to terminal-state transition validation. It compare-and-swaps the item, writes `operator_requeue`, and returns it to its recorded retry stage (`eligible`, `ready`, or `reconciling`). Any item with a held unresolved ambiguity is rejected; the method never dispatches by itself and later processing still requires the normal live grant/lease/quota checks.

- [ ] **Step 4: Run focused reconciliation tests and verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_hh_autopilot_reconcile.py -q`

Expected: FAIL because the reconciler and recovery sweep do not exist.

- [ ] **Step 5: Add paginated account-wide negotiation snapshots**

```python
# work_hunter/hh_transport/api_session.py
def list_negotiations_page(self, *, status: str, page: int, per_page: int = 100) -> SearchPage:
    data = self.request_json(
        "GET", "/negotiations", params={"status": status, "page": page, "per_page": per_page}
    )
    return SearchPage(
        items=list(data.get("items") or []),
        page=int(data.get("page") or page),
        pages=int(data.get("pages") or 0),
        per_page=int(data.get("per_page") or per_page),
        total=int(data.get("found") or 0),
    )
```

`HHApplyClient.negotiation_snapshots()` walks configured relevant statuses and pages, normalizes unique remote negotiation ID, vacancy ID, resume ID, created timestamp, and status, then deduplicates by remote ID. It exposes no application POST method through the reconciler's injected `NegotiationReader` protocol.

- [ ] **Step 6: Implement classification using resume identity and attempt time**

```python
# work_hunter/hh_autopilot/reconcile.py
class HHApplicationReconciler:
    def reconcile(self, item_id, provenance, lease, now=None):
        prepared = self.repository.load_reconciliation_context(
            item_id, provenance, lease.fencing_token, now=now
        )
        try:
            snapshots = self.reader.negotiation_snapshots(prepared.account_id)
        except HHAuthError:
            if self.session_recovery.try_refresh(prepared, provenance):
                snapshots = self.reader.negotiation_snapshots(prepared.account_id)
            else:
                return self.repository.open_reconciliation_auth_challenge(prepared, lease.fencing_token)
        relevant = [row for row in snapshots if row.vacancy_id == prepared.vacancy_id]
        exact = [row for row in relevant if row.resume_id == prepared.resume_id and row.created_at >= prepared.dispatched_at]
        if exact:
            return self.repository.finalize_reconciled_applied(prepared, exact[0], lease.fencing_token)
        external = [row for row in relevant if row.resume_id != prepared.resume_id or row.created_at < prepared.dispatched_at]
        if external and all(row.is_classifiable for row in external):
            return self.repository.finalize_external_application(prepared, external[0], lease.fencing_token)
        return self._record_absent_or_ambiguous(prepared, relevant, lease, now)
```

Use an explicit timestamp tolerance from config when comparing remote/local clocks. An empty or malformed remote resume/timestamp is not coerced into external or exact. On a confident external result, release the dispatch reservation and insert an `external_sync` consumed reservation only when the unique remote ID is from the current account-local date.

- [ ] **Step 7: Implement bounded absence checks and ambiguity challenge creation**

Each no-match check increments `reconciliation_count` and persists the next UTC check time. Before the configured limit, remain `reconciling` with the reservation held. At the limit:

- no relevant negotiation after the consistency window: release reservation and return to `ready` if POST attempts remain, otherwise `dead/retry_exhausted`;
- relevant but unclassifiable negotiation, duplicate response that remains unclassified, or inconsistent history: retain held reservation, transition to `manual_challenge`, and create exactly one `ambiguous_application` challenge.

Repository challenge resolution updates challenge, item, reservation, application/guard if needed, and event in one transaction. It accepts late resolution for ambiguity only; other expired/dismissed challenges remain terminal.

- [ ] **Step 8: Implement the grant-independent sweep with a capability-limited port**

```python
class HHRecoverySweep:
    def run(self, account_id=None, now=None):
        report = RecoveryReport()
        for account in self.repository.accounts_needing_recovery(account_id, now=now):
            lease = self.repository.acquire_lease(
                account, self.owner_token(), ttl_seconds=self.config(account).lease.ttl_seconds, now=now
            )
            if lease is None:
                report.busy += 1
                continue
            run = self.repository.create_run(account, trigger="recovery", policy_hash="recovery")
            try:
                self.repository.recover_stale_applying(account, run.id, lease.fencing_token, now=now)
                for item in self.repository.due_reconciliation_items(account, now=now):
                    provenance = self.repository.recovery_provenance(item.active_attempt_id)
                    report.add(self.reconciler(account).reconcile(item.id, provenance, lease, now=now))
            finally:
                self.repository.release_lease(lease)
        return report
```

The sweep's dependency object contains only negotiation-history reads, narrowly bounded token/session refresh, and local finalization. It has no `search`, `reserve_quota`, or `apply_outcome` attribute. Run it before checking config `enabled`, pause, policy hash, or kill switch. These controls still prevent every new POST.

- [ ] **Step 9: Run reconciliation, executor, and repository suites**

Run: `.venv\Scripts\python.exe -m pytest tests/test_hh_autopilot_reconcile.py tests/test_hh_autopilot_executor.py tests/test_hh_autopilot_repository.py -q`

Expected: all tests pass and every possibly-sent branch is either reconciled or held for manual classification.

- [ ] **Step 10: Commit**

```powershell
git add work_hunter/hh_autopilot/reconcile.py work_hunter/hh_autopilot/repository.py work_hunter/hh_transport/api_session.py work_hunter/sources/hh.py tests/test_hh_autopilot_reconcile.py
git commit -m "feat: reconcile uncertain HH applications"
```

### Task 11: Grounded Screening/Forms, Manual CAPTCHA Continuity, And Browser Login

**Files:**
- Create: `work_hunter/hh_autopilot/challenges.py`
- Create: `work_hunter/hh_autopilot/browser.py`
- Create: `work_hunter/hh_transport/authorize.py`
- Modify: `work_hunter/hh_transport/browser_session.py`
- Modify: `work_hunter/hh_transport/challenges.py`
- Modify: `work_hunter/hh_agent/forms.py`
- Modify: `work_hunter/hh_autopilot/repository.py`
- Test: `tests/test_hh_autopilot_challenges.py`
- Create: `tests/fixtures/hh_autopilot/form.html`
- Create: `tests/fixtures/hh_autopilot/captcha.html`

**Interfaces:**
- Produces: `GroundedAnswerMapper.map(fields, candidate, resume) -> MappingResult`.
- Produces: `HHChallengeHandler.handle(outcome, item, lease) -> ChallengeResult` and `.resolve()`.
- Produces: `HHBrowserApplicationAdapter.inspect()`, `.submit_grounded_form()`, `.open_manual_handoff()`, and `.capture_session()`.
- Produces: `HHBrowserAuthorizer.login()`, `.import_cookies()`, `.logout()`, and `.diagnostics()`.
- CAPTCHA solving remains a human action in the same authenticated browser context. No image-to-answer or solver integration is present.

- [ ] **Step 1: Add local deterministic form and CAPTCHA fixtures**

```html
<!-- tests/fixtures/hh_autopilot/form.html -->
<form id="response-form" action="/submitted" method="post">
  <label>Имя <input name="first_name" required></label>
  <label>Город <input name="city" required></label>
  <label>Телефон <input name="phone" required></label>
  <label>Комментарий <textarea name="cover_letter"></textarea></label>
  <button type="submit">Откликнуться</button>
</form>
```

```html
<!-- tests/fixtures/hh_autopilot/captcha.html -->
<main data-qa="captcha-page">
  <h1>Подтвердите, что вы не робот</h1>
  <iframe title="captcha" src="/captcha-frame"></iframe>
</main>
```

- [ ] **Step 2: Write failing grounded-mapping and off-mode tests**

```python
# tests/test_hh_autopilot_challenges.py
def test_required_fields_are_mapped_only_from_explicit_facts(mapper) -> None:
    result = mapper.map(
        fields=[required("first_name"), required("city"), required("phone")],
        candidate={"first_name": "Анна", "city": "Москва", "phone": "+79990000000"},
        resume={"title": "Python developer"},
    )
    assert result.complete is True
    assert result.answers == {
        "first_name": "Анна",
        "city": "Москва",
        "phone": "+79990000000",
    }


def test_unknown_required_field_skips_without_guessing(mapper) -> None:
    result = mapper.map(
        fields=[required("desired_relocation_date")], candidate={}, resume={}
    )
    assert result.complete is False
    assert result.outcome == "missing_required_data"
    assert result.unknown_required == ("desired_relocation_date",)


@pytest.mark.parametrize(
    ("kind", "mode", "outcome"),
    [("screening", "off", "screening_disabled"), ("form", "off", "form_disabled")],
)
def test_off_modes_do_not_open_or_submit(kind, mode, outcome, challenge_case) -> None:
    result = challenge_case.handler.handle_required_flow(kind=kind, mode=mode)
    assert result.outcome == outcome
    assert challenge_case.browser.calls == []
```

Add mapping tests for select, radio, checkbox, text, number, email, phone, cover letter, and explicit yes/no profile facts. Unsupported knowledge questions become `manual_assessment`; they never receive random or model-invented answers.

- [ ] **Step 3: Write failing CAPTCHA and browser-session continuity tests**

```python
def test_captcha_creates_one_manual_handoff_and_releases_dispatch_quota(challenge_case) -> None:
    result = challenge_case.handler.handle_captcha(
        challenge_case.item, url="https://hh.ru/captcha?token=secret", lease=challenge_case.lease
    )
    challenge = challenge_case.repo.get_challenge(result.challenge_id)
    assert challenge.challenge_type == "manual_captcha"
    assert "secret" not in challenge.sanitized_url
    assert challenge_case.repo.dispatch_reservation(challenge.item_id).state == "released"
    assert challenge_case.repo.get_item(challenge.item_id).state.value == "manual_challenge"


def test_completed_captcha_reuses_saved_cookies_and_returns_ready(challenge_case) -> None:
    challenge = challenge_case.open_captcha_with_cookie("hhtoken", "value")
    challenge_case.handler.resolve(challenge.id, action="completed", actor="ui")
    assert challenge_case.browser_session.load_cookie("hhtoken") == "value"
    assert challenge_case.repo.get_item(challenge.item_id).state.value == "ready"
```

Add expiry/dismissal tests for CAPTCHA and assessment (`skipped`), account `manual_auth` with no automatic expiry, challenge URL query redaction, screenshot private-root enforcement, and automatic continuation on the next due scheduler tick after successful resolution.

- [ ] **Step 4: Run focused challenge tests and verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_hh_autopilot_challenges.py -q`

Expected: FAIL because the new mapper/browser/challenge adapters do not exist.

- [ ] **Step 5: Implement a strict grounded answer registry**

```python
# work_hunter/hh_autopilot/challenges.py
FIELD_SOURCES = {
    "first_name": ("candidate", "first_name"),
    "last_name": ("candidate", "last_name"),
    "city": ("candidate", "city"),
    "phone": ("candidate", "phone"),
    "email": ("candidate", "email"),
    "citizenship": ("candidate", "citizenships"),
    "work_permit": ("candidate", "work_permits"),
    "salary": ("candidate", "salary_min"),
    "cover_letter": ("prepared", "cover_letter"),
}


class GroundedAnswerMapper:
    def map(self, fields, candidate, resume, prepared=None):
        answers = {}
        unknown = []
        sources = {"candidate": candidate, "resume": resume, "prepared": prepared or {}}
        for field in fields:
            semantic_id = normalize_field_id(field)
            source_path = FIELD_SOURCES.get(semantic_id)
            value = lookup_explicit_value(sources, source_path) if source_path else None
            if is_missing(value) and field.required:
                unknown.append(semantic_id)
            elif not is_missing(value):
                answers[field.name] = validate_field_value(field, value)
        return MappingResult.from_values(answers, unknown)
```

Use grounded LLM answers as a fallback for unknown required fields, using candidate/profile facts and explicit configured answers. If the model returns unknown or cannot match an allowed option, pause the vacancy for browser review. Persist field identifiers and masked source labels, not raw full answer payloads, in journal metadata.

- [ ] **Step 6: Implement challenge transitions and reservation rules**

`HHChallengeHandler` maps:

- supported screening/form + complete grounded mapping: browser continuation under the next executor attempt;
- unknown required value: atomic `missing_required_data` skip and reservation release;
- screening/form mode off: atomic `screening_disabled|form_disabled` skip and release;
- CAPTCHA: `manual_captcha`, release current reservation, `manual_challenge`;
- assessment/unsupported task: `manual_assessment`, release current reservation, `manual_challenge`;
- auth before a definite dispatch: account `manual_auth`, release reservation, retain retry stage;
- auth during reconciliation: account `manual_auth`, keep reservation held and return to reconciliation when resolved.

Create one open challenge per `(type, account, item)` and make repeated classification idempotent. Resolution action `completed` returns CAPTCHA/assessment to `ready`; `dismiss|expire` moves them to `skipped`. Account auth resolution returns each affected item to its stored retry stage.

- [ ] **Step 7: Implement authenticated browser inspection and form submission**

```python
# work_hunter/hh_autopilot/browser.py
class HHBrowserApplicationAdapter:
    def inspect(self, url: str) -> BrowserFlow:
        with self._context() as context:
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=self.navigation_timeout_ms)
            if page.locator('[data-qa*="captcha"], iframe[title*="captcha" i]').count():
                return BrowserFlow.captcha(sanitize_hh_url(page.url))
            fields = extract_supported_fields(page)
            if detect_knowledge_assessment(page, fields):
                return BrowserFlow.assessment(sanitize_hh_url(page.url))
            return BrowserFlow.form(sanitize_hh_url(page.url), fields)

    def submit_grounded_form(self, flow, answers) -> DispatchOutcome:
        with self._context() as context:
            page = context.new_page()
            page.goto(flow.url, wait_until="domcontentloaded", timeout=self.navigation_timeout_ms)
            fill_exact_fields(page, flow.fields, answers)
            with page.expect_response(
                is_application_completion_response,
                timeout=self.navigation_timeout_ms,
            ) as response_info:
                page.locator(flow.submit_selector).click()
            return classify_browser_submission(response_info.value, page)
```

The executor treats this browser submission as the remote application attempt: it prepares attempt/guard/quota first and uses the same delivery-certainty/finalization rules. Browser timeout after clicking is `POSSIBLY_SENT` and enters reconciliation. Inspection and field mapping occur before dispatch preparation. No CAPTCHA element is clicked or answered automatically.

- [ ] **Step 8: Make cookie/session persistence atomic and private**

Extend `HHBrowserSession.save()` to write a temporary file in the same private directory, `fsync`, and replace the target. Validate all cookie domains as `hh.ru` subdomains and mask values from diagnostics. Challenge screenshots live under `.work-hunter/private/hh-challenges/<account>/<challenge-id>/`, reject path traversal, and are deleted according to configured retention.

- [ ] **Step 9: Implement browser-assisted account login without undeclared credentials**

```python
# work_hunter/hh_transport/authorize.py
class HHBrowserAuthorizer:
    def login(self, profile_id: str, *, login_url: str = "https://hh.ru/account/login"):
        with self.browser.launch_persistent_context(self.profile_dir(profile_id), headless=False) as context:
            page = context.new_page()
            page.goto(login_url, wait_until="domcontentloaded")
            self.wait_until_authenticated(page, context)
            self.session(profile_id).update_from_playwright_context(context.cookies(), page.content())
            self.session(profile_id).save()
            return self.diagnostics(profile_id)
```

The user enters password/OTP and solves any login CAPTCHA directly in the browser. `import_cookies()` validates and atomically stores a user-provided cookie export. `logout()` deletes local cookies/tokens only after literal confirmation. Token refresh uses only user-provided OAuth client configuration; if absent, diagnostics reports `manual_auth` instead of embedding third-party credentials.

- [ ] **Step 10: Run challenge, browser-session, form, and transport tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_hh_autopilot_challenges.py tests/test_hh_agent_forms.py tests/test_hh_transport.py -q`

Expected: all deterministic tests pass. If Playwright is installed, also run: `.venv\Scripts\python.exe -m pytest tests/test_hh_autopilot_challenges.py -m browser -q`.

- [ ] **Step 11: Commit**

```powershell
git add work_hunter/hh_autopilot/challenges.py work_hunter/hh_autopilot/browser.py work_hunter/hh_transport/authorize.py work_hunter/hh_transport/browser_session.py work_hunter/hh_transport/challenges.py work_hunter/hh_agent/forms.py work_hunter/hh_autopilot/repository.py tests/test_hh_autopilot_challenges.py tests/fixtures/hh_autopilot
git commit -m "feat: handle HH forms and manual challenges"
```

### Task 12: Single Orchestration Engine For Live, Manual, Shadow, Canary, And Retry

**Files:**
- Create: `work_hunter/hh_autopilot/engine.py`
- Modify: `work_hunter/hh_autopilot/types.py`
- Modify: `work_hunter/hh_autopilot/repository.py`
- Modify: `work_hunter/hh_autopilot/authorization.py`
- Test: `tests/test_hh_autopilot_engine.py`

**Interfaces:**
- Produces: `HHAutopilot.run(RunRequest) -> RunReport`.
- `RunRequest.trigger` is `schedule|manual|shadow|retry|recovery|canary`; manual/canary requests carry an exact one-shot `LiteralConfirmation`, while the engine creates `LiveAuthorization` internally after it has the run ID and fencing token.
- All live callers converge on the same filter/rank/guard/quota/executor/reconcile/journal path.

- [ ] **Step 1: Write the failing ordered-pipeline test**

```python
# tests/test_hh_autopilot_engine.py
def test_live_run_orders_retries_search_filters_rank_and_dispatch(engine_case) -> None:
    engine_case.repo.seed_due_retry("v-retry", retry_stage="application")
    engine_case.search.pages = [[vacancy("v-pass"), vacancy("v-filtered")]]
    engine_case.policy.reject("v-filtered", "hard_filter:excluded_keywords")

    report = engine_case.engine.run(engine_case.live_request())

    assert engine_case.trace.index("retry:v-retry") < engine_case.trace.index("search:page:0")
    assert engine_case.trace.index("filter:v-pass") < engine_case.trace.index("rank:v-pass")
    assert engine_case.trace.index("rank:v-pass") < engine_case.trace.index("post:v-pass")
    assert report.applied == 2
    assert engine_case.repo.get_item_by_vacancy("v-filtered").state.value == "skipped"
```

- [ ] **Step 2: Write failing isolation, stop, policy-change, shadow, and canary tests**

```python
def test_manual_captcha_does_not_abort_unrelated_vacancies(engine_case) -> None:
    engine_case.search.pages = [[vacancy("v-captcha"), vacancy("v-ok")]]
    engine_case.transport.outcomes["v-captcha"] = manual_captcha()
    engine_case.transport.outcomes["v-ok"] = applied("n-ok")
    report = engine_case.engine.run(engine_case.live_request())
    assert report.applied == 1
    assert report.manual == 1
    assert engine_case.repo.get_item_by_vacancy("v-ok").state.value == "applied"


def test_pause_between_items_stops_new_post_but_commits_current_result(engine_case) -> None:
    engine_case.after_first_post(lambda: engine_case.authorizer.set_pause(["default"], True))
    engine_case.search.pages = [[vacancy("v-1"), vacancy("v-2")]]
    report = engine_case.engine.run(engine_case.live_request())
    assert report.applied == 1
    assert engine_case.transport.posted_vacancies == ["v-1"]
    assert report.status == "interrupted"


def test_shadow_is_real_read_only_search_but_has_zero_live_state(engine_case) -> None:
    report = engine_case.engine.run(engine_case.shadow_request())
    assert report.shadow_results > 0
    assert engine_case.transport.application_posts == []
    assert engine_case.repo.count_items() == 0
    assert engine_case.repo.count_reservations() == 0


def test_canary_literal_confirmation_authorizes_exactly_one_named_vacancy(engine_case) -> None:
    report = engine_case.engine.run(engine_case.canary_request("r-1", "v-1", confirm=True))
    assert report.applied <= 1
    assert engine_case.transport.posted_vacancies == ["v-1"]
    with pytest.raises(AuthorizationDenied):
        engine_case.engine.run(engine_case.canary_request("r-1", "v-2", reuse_confirmation=True))
```

Add tests for per-run/daily limits, randomized delay between sends only, account cooldown stopping all ready items, lease loss, kill/disable during an in-flight response followed by recovery, config policy change resetting never-dispatched candidates to `discovered`, persisted eligibility/AI retry stage, one item `internal_error` not aborting others, and authentication loss stopping only the affected account.

- [ ] **Step 3: Run focused engine tests and verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_hh_autopilot_engine.py -q`

Expected: FAIL because the orchestration engine does not exist.

- [ ] **Step 4: Implement typed run requests and exact one-shot canary scope**

```python
# work_hunter/hh_autopilot/types.py
@dataclass(frozen=True)
class RunRequest:
    account_id: str
    trigger: str
    authorization: LiteralConfirmation | None = None
    resume_id: str | None = None
    vacancy_id: str | None = None
    preset_name: str | None = None


def canary_reference(account_id: str, resume_id: str, vacancy_id: str) -> str:
    return f"canary:{account_id}:{resume_id}:{vacancy_id}"
```

The authorizer creates a one-use database nonce for a canary literal confirmation and consumes it during `prepare_dispatch()`. The nonce is bound to exact account/resume/vacancy and a success cap of one. It creates no durable grant. A manual direct apply/campaign literal confirmation creates a separate short-lived authorization with an immutable exact target set and bounded maximum successes; every target still reserves the normal daily quota and passes the same executor.

`_prepare_exact_canary()` verifies the named resume is published in the named account, fetches only the named vacancy, runs the normal hard filters/ranking/cover-letter policy, and creates at most that one ready item. It performs no query/recommendation search and cannot fall through to another vacancy when the named one is filtered or challenged.

- [ ] **Step 5: Implement run lifecycle, lease ownership, and recovery-first processing**

```python
# work_hunter/hh_autopilot/engine.py
class HHAutopilot:
    def run(self, request: RunRequest) -> RunReport:
        config = self.config_loader.load_account(request.account_id)
        self.authorizer.reconcile_config_projection(request.account_id, config.raw)
        lease = self.repository.acquire_lease(
            request.account_id,
            self.owner_token(),
            ttl_seconds=config.lease.ttl_seconds,
            now=self.clock.now(),
        )
        if lease is None:
            return RunReport.busy(request.account_id)
        run = self.repository.create_run(
            request.account_id,
            trigger=request.trigger,
            policy_hash=config.policy_hash,
            fencing_token=lease.fencing_token,
        )
        try:
            authorization = self._authorization_for_run(request, run, config, lease)
            if request.trigger == "shadow":
                self._discover_rank_and_save_shadow(run, request, config, lease)
                return self.repository.complete_run(run.id, lease.fencing_token)
            self.repository.recover_stale_applying(
                request.account_id, run.id, lease.fencing_token, now=self.clock.now()
            )
            self._process_due_reconciliation(run, config, lease)
            self._process_due_retries(run, authorization, config, lease)
            if request.trigger not in {"retry", "recovery", "canary"}:
                self._discover_and_rank(run, request, config, lease)
            if request.trigger == "canary":
                self._prepare_exact_canary(run, request, config, lease)
            self._dispatch_ready(run, authorization, config, lease)
            return self.repository.complete_run(run.id, lease.fencing_token)
        except LostLease:
            return self.repository.interrupt_run(run.id, "lease_lost")
        finally:
            self.repository.release_lease(lease)
```

`_authorization_for_run()` requires the one-shot nonce for manual/canary, returns no mutation capability for shadow, and calls `issue_live_authorization(account, config, run.id, lease.fencing_token)` for schedule/run-now/retry/authorized search recovery. It then records the grant ID on the run. Grant-independent scheduler recovery remains the separate `HHRecoverySweep`; `_process_due_reconciliation()` here handles already-held work while a non-shadow live run owns the lease. Before an authorized recovery run resumes an interrupted search cycle, validate the active grant and exact policy hash.

- [ ] **Step 6: Implement expanded resume/preset search and deterministic evaluation**

Resolve `published:*` once per run against the named HH account. Expand only configured existing preset names; recommendation-only mapping is legal only when enabled. Track one run-wide search budget across mappings. For each normalized vacancy:

1. evaluate hard filters and persist evidence;
2. compute deterministic score;
3. invoke AI only under the configured mode;
4. persist rank/AI evidence;
5. choose best resume or per-resume candidates;
6. transition selected live items to ready in stable order, or write final filter/rank/`would_apply` evidence only to `hh_autopilot_shadow_results` for shadow mode.

On a policy hash mismatch, do not dispatch. Reconcile any applying/reconciling item, reset never-dispatched nonterminal items to `discovered`, supersede old pagination, and require a newly confirmed durable grant for future autonomous work.

Shadow evaluation ends after `save_shadow_result()`. There is no repository method that promotes a shadow row into `hh_autopilot_items`; a later live run re-fetches the vacancy and re-evaluates it under its current authorized policy.

- [ ] **Step 7: Implement controlled dispatch loop and failure isolation**

```python
def _dispatch_ready(self, run, authorization, config, lease):
    sent = 0
    for item in self.repository.ready_items(run.account_id, limit=config.limits.per_run_success):
        if self.repository.stop_requested(run.id) or not self.authorizer.may_continue(authorization, config):
            self.repository.interrupt_run(run.id, "stop_requested")
            break
        if sent:
            delay = self.random.uniform(
                config.limits.send_delay_min_seconds,
                config.limits.send_delay_max_seconds,
            )
            self.sleeper.sleep(delay)
        try:
            result = self.executor.execute(item.id, authorization, lease, now=self.clock.now())
        except AccountUnsafe as exc:
            self.repository.interrupt_run(run.id, exc.code)
            break
        except Exception as exc:
            self.repository.record_internal_error(item.id, run.id, lease.fencing_token, exc)
            continue
        sent += int(result.remote_post_dispatched)
```

Re-check grant/pause/kill/window/lease/cooldown/quota/item version before every autonomous POST inside executor, not just here. Only masked exception type and stable code enter events. A challenge, permanent skip, or item retry continues to the next vacancy; account-unsafe conditions stop that account run.

- [ ] **Step 8: Run engine plus all core unit suites**

Run: `.venv\Scripts\python.exe -m pytest tests/test_hh_autopilot_engine.py tests/test_hh_autopilot_search.py tests/test_hh_autopilot_policy.py tests/test_hh_autopilot_ranking.py tests/test_hh_autopilot_executor.py tests/test_hh_autopilot_reconcile.py -q`

Expected: all tests pass.

- [ ] **Step 9: Commit**

```powershell
git add work_hunter/hh_autopilot/engine.py work_hunter/hh_autopilot/types.py work_hunter/hh_autopilot/repository.py work_hunter/hh_autopilot/authorization.py tests/test_hh_autopilot_engine.py
git commit -m "feat: orchestrate the HH application pipeline"
```

### Task 13: Due-Window Scheduler, Recovery-First Tick, And Safe Runner Tasks

**Files:**
- Create: `work_hunter/hh_autopilot/scheduler.py`
- Modify: `work_hunter/hh_autopilot/repository.py`
- Modify: `work_hunter/scheduler.py`
- Create: `examples/hh-autopilot-runner.json`
- Test: `tests/test_hh_autopilot_scheduler.py`
- Test: `tests/test_scheduler.py`

**Interfaces:**
- Produces: `SchedulePolicy.is_due(account, now)`, `.next_due(account, now)`, and `HHAutopilotScheduler.tick(now=None)`.
- Adds safe runner tasks `hh-autopilot-recover` and `hh-autopilot-tick`.
- Every tick runs grant-independent recovery before reading `enabled`; live runs start only with an exact active grant.

- [ ] **Step 1: Write failing window, interval, restart, and recovery-first tests**

```python
# tests/test_hh_autopilot_scheduler.py
from datetime import datetime, timezone


def test_schedule_uses_account_timezone_days_and_window(schedule_case) -> None:
    assert schedule_case.policy.is_due(
        schedule_case.account,
        datetime(2026, 7, 15, 5, 0, tzinfo=timezone.utc),  # 08:00 Moscow
    )
    assert not schedule_case.policy.is_due(
        schedule_case.account,
        datetime(2026, 7, 15, 19, 0, tzinfo=timezone.utc),  # 22:00 Moscow
    )


def test_restart_starts_at_most_one_missed_run(schedule_case) -> None:
    schedule_case.repo.set_last_scheduled_at("default", "2026-07-15T05:00:00+00:00")
    schedule_case.scheduler.tick(now=parse("2026-07-15T12:30:00+00:00"))
    assert schedule_case.engine.scheduled_accounts == ["default"]


def test_disabled_killed_account_still_runs_read_only_recovery_first(schedule_case) -> None:
    schedule_case.disable_and_kill("default")
    schedule_case.repo.seed_stale_possibly_sent("default")
    report = schedule_case.scheduler.tick(now=parse("2026-07-15T08:00:00+00:00"))
    assert schedule_case.trace[0] == "recovery:default"
    assert report.recovery.applied == 1
    assert schedule_case.engine.scheduled_accounts == []
    assert schedule_case.transport.application_posts == []
```

Add tests for all weekdays, exact start/end boundaries, interval one and 1440, daylight-saving time with another valid IANA timezone, paused account, policy mismatch, active lease/busy account, two independent accounts, account cooldown, quota exhausted, and next-run display.

- [ ] **Step 2: Write failing SafeTaskRunner integration tests**

```python
# tests/test_scheduler.py
def test_autopilot_tick_and_recovery_are_safe_typed_tasks(app, tmp_path) -> None:
    runner = SafeTaskRunner(app, root=tmp_path)
    report = runner.run([
        {"task": "hh-autopilot-recover"},
        {"task": "hh-autopilot-tick"},
    ])
    assert [item["status"] for item in report["items"]] == ["completed", "completed"]
    assert app.calls == ["recover_hh_autopilot", "tick_hh_autopilot"]


def test_runner_cannot_supply_confirm_or_widen_tick_scope(app, tmp_path) -> None:
    runner = SafeTaskRunner(app, root=tmp_path)
    report = runner.run([{"task": "hh-autopilot-tick", "confirm": True, "vacancy_id": "v-1"}])
    assert report["items"][0]["status"] == "blocked"
    assert report["items"][0]["reason"] == "invalid_autopilot_task_parameters"
```

- [ ] **Step 3: Run focused scheduler tests and verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_hh_autopilot_scheduler.py tests/test_scheduler.py -q`

Expected: FAIL because due-window policy and runner task names do not exist.

- [ ] **Step 4: Persist scheduler timestamps and compute local due times**

Use the `last_scheduled_at` and `next_scheduled_at` columns already created by migration `0003`. Repository compares and updates them using UTC instants under `BEGIN IMMEDIATE`; do not rewrite an already applied migration at this stage.

```python
# work_hunter/hh_autopilot/scheduler.py
class SchedulePolicy:
    def is_due(self, account, now):
        local = now.astimezone(ZoneInfo(account.timezone))
        if local.isoweekday() not in account.schedule.days:
            return False
        if not (account.schedule.start <= local.time() <= account.schedule.end):
            return False
        last = self.repository.last_scheduled_at(account.profile_id)
        return last is None or now >= last + timedelta(minutes=account.schedule.interval_minutes)
```

`next_due()` advances to the next valid configured weekday/window without fabricating catch-up intervals. The persisted `next_scheduled_at` is informational; `is_due()` recomputes from current validated config.

- [ ] **Step 5: Implement recovery-first multi-account tick**

```python
class HHAutopilotScheduler:
    def tick(self, now=None):
        instant = now or self.clock.now()
        recovery = self.recovery_sweep.run(now=instant)
        challenge_updates = self.challenge_handler.expire_due(instant)
        retention = self.repository.prune_retention(self.config_loader.load(), now=instant)
        reports = []
        config = self.config_loader.load()
        for account in config.accounts:
            self.authorizer.reconcile_config_projection(account.profile_id, config.raw)
            if not self.policy.is_due(account, instant):
                continue
            if not self.authorizer.has_dispatchable_grant(account.profile_id, config.raw):
                continue
            if self.repository.account_cooldown_active(account.profile_id, instant):
                continue
            if not self.repository.claim_schedule_slot(account.profile_id, instant, account.schedule):
                continue
            reports.append(self.engine.run(RunRequest(account.profile_id, trigger="schedule")))
        return SchedulerReport(
            recovery=recovery,
            challenge_updates=challenge_updates,
            retention=retention,
            runs=tuple(reports),
        )
```

`HHAutopilot.run()` creates the run after lease acquisition, then requests `LiveAuthorization` using that exact run ID and fencing token. A schedule claim that later finds the account busy records the next normal interval; it does not spin or start a burst.

Challenge expiry is grant-independent local finalization: CAPTCHA/assessment expiry skips and releases as already defined, ambiguity expiry moves to dead while retaining the hold, and `manual_auth` never auto-expires. Retention removes only events/artifacts older than configured bounds that are not referenced by an open/in-progress challenge. Existing notification sinks receive masked challenge/run-failure events; a sink failure is recorded separately and never changes the application state.

- [ ] **Step 6: Add strictly parameterized SafeTaskRunner routes**

```python
# work_hunter/scheduler.py
SAFE_TASKS = {
    "sync",
    "score",
    "hh-refresh-token",
    "hh-update-resumes",
    "hh-campaign-plan",
    "hh-autopilot-recover",
    "hh-autopilot-tick",
}
```

`hh-autopilot-recover` accepts only optional `account`; `hh-autopilot-tick` accepts no mutation parameters. Reject `confirm`, `real`, `vacancy_id`, resume overrides, policy overrides, arbitrary account lists, and unknown keys. Route to `app.recover_hh_autopilot()` and `app.tick_hh_autopilot()`; each service method constructs its own capability-limited dependencies.

- [ ] **Step 7: Add the exact runner example**

```json
{
  "tasks": [
    {"task": "hh-autopilot-recover"},
    {"task": "hh-autopilot-tick"}
  ]
}
```

- [ ] **Step 8: Run scheduler, engine, and existing runner suites**

Run: `.venv\Scripts\python.exe -m pytest tests/test_hh_autopilot_scheduler.py tests/test_scheduler.py tests/test_hh_autopilot_engine.py -q`

Expected: all tests pass.

- [ ] **Step 9: Commit**

```powershell
git add work_hunter/hh_autopilot/scheduler.py work_hunter/hh_autopilot/repository.py work_hunter/scheduler.py examples/hh-autopilot-runner.json tests/test_hh_autopilot_scheduler.py tests/test_scheduler.py
git commit -m "feat: schedule recovery-first HH autopilot runs"
```

### Task 14: WorkHunter Facade, Manual-Apply Unification, And Read-Only MCP

**Files:**
- Modify: `work_hunter/services.py`
- Modify: `work_hunter/mcp_server.py`
- Modify: `work_hunter/safety.py`
- Test: `tests/test_services.py`
- Test: `tests/test_mcp_safety.py`
- Test: `tests/test_hh_autopilot_engine.py`

**Interfaces:**
- `WorkHunter` exposes thin autopilot validate/control/status/run/recovery/history/challenge methods.
- Existing `confirm_apply()` and `confirm_hh_campaign()` create one-shot `LiteralConfirmation` requests and enter the common engine/executor.
- MCP exposes read-only status/history/challenge inspection only and cannot create any authorization or live run.

- [ ] **Step 1: Write failing service-facade and manual-path equivalence tests**

```python
# tests/test_services.py
def test_confirm_apply_uses_common_executor_and_daily_quota(app, prepared_job) -> None:
    result = app.confirm_apply(
        prepared_job.id,
        resume_id="r-1",
        account="default",
        confirm=True,
    )
    assert result["status"] in {"applied", "manual_challenge", "reconciling"}
    events = app.hh_autopilot_history(account="default", vacancy_id=prepared_job.source_id)
    assert any(event["next_state"] == "applying" for event in events["events"])
    assert app.hh_autopilot_status(account="default")["quota"]["used"] == 1


def test_manual_and_scheduled_success_have_same_provenance_shape(app_factory) -> None:
    manual = app_factory().run_manual_success()
    scheduled = app_factory().run_scheduled_success()
    fields = {"account_profile_id", "autopilot_run_id", "autopilot_item_id", "autopilot_attempt_id",
              "authorization_kind", "authorization_ref", "policy_hash", "delivery_certainty"}
    assert fields <= manual["attempt"].keys()
    assert fields <= scheduled["attempt"].keys()
    assert manual["attempt"]["authorization_kind"] == "literal_confirmation"
    assert scheduled["attempt"]["authorization_kind"] == "autopilot"
```

Add compatibility tests for `confirm_hh_campaign()`, apply-from-file confirmation, existing return keys/statuses, `allow_broad_apply=true` not authorizing a dispatch, manual quota exhaustion, and a manual possibly-sent response entering the same reconciliation path.

- [ ] **Step 2: Write failing MCP capability tests**

```python
# tests/test_mcp_safety.py
def test_mcp_has_read_only_autopilot_tools_only(mcp_tools) -> None:
    names = {tool.name for tool in mcp_tools}
    assert {"hh_autopilot_status", "hh_autopilot_history", "hh_autopilot_challenges"} <= names
    assert "hh_autopilot_enable" not in names
    assert "hh_autopilot_run_now" not in names
    assert "hh_autopilot_resolve_challenge" not in names


def test_agent_confirm_flags_still_cannot_send_real_application(app) -> None:
    result = app.run_hh_agent_task("apply", {"confirm": True, "confirm_apply": True})
    assert result["reason"] == "real_apply_blocked"
```

- [ ] **Step 3: Run focused service and MCP tests and verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_services.py tests/test_mcp_safety.py -q`

Expected: FAIL because the facade methods and unified manual executor do not exist.

- [ ] **Step 4: Add one component factory and thin facade methods**

```python
# work_hunter/services.py
def _hh_autopilot(self) -> HHAutopilotComponents:
    config = self.load_config()
    repository = AutopilotRepository(self.storage)
    transports = HHAccountTransportFactory(config, root=self.root)
    return build_hh_autopilot_components(
        config=config,
        repository=repository,
        transports=transports,
        candidate_profiles=self.profile_service,
        cover_letters=self.letter_service,
        root=self.root,
    )

def tick_hh_autopilot(self) -> dict[str, Any]:
    return self._hh_autopilot().scheduler.tick().to_dict()

def recover_hh_autopilot(self, account: str | None = None) -> dict[str, Any]:
    return self._hh_autopilot().recovery.run(account_id=account).to_dict()

def validate_hh_autopilot(self, account: str | None = None) -> dict[str, Any]:
    return self._hh_autopilot().config.validate(account).to_dict()
```

Add facade methods for enable, disable, pause, resume, stop, kill/clear, shadow, canary, run-now, retry, list/resolve challenges, status, and history. Mutation methods receive primitive request data but create typed authorization only inside the authorizer; they do not accept caller-constructed `LiveAuthorization` objects.

- [ ] **Step 5: Route confirmed manual operations into one-shot engine requests**

```python
def confirm_apply(self, job_id, *, resume_id=None, account=None, confirm=False):
    blocked = require_mutation_confirmation(
        confirm,
        code="apply_requires_confirmation",
        message="Explicit confirmation is required before sending a real application.",
        risk_flags=("external_mutation", "job_application"),
    )
    if blocked:
        return blocked
    account_id = require_explicit_hh_account(account)
    job = self.storage.get_job(job_id)
    confirmation = LiteralConfirmation(
        account_id=account_id,
        reference_id=manual_reference(account_id, str(job.source_id), str(resume_id or "")),
    )
    return self._hh_autopilot().engine.run(
        RunRequest(
            account_id=account_id,
            trigger="manual",
            authorization=confirmation,
            resume_id=resume_id,
            vacancy_id=str(job.source_id),
        )
    ).to_legacy_apply_dict()
```

Create the manual run/item from the existing job or campaign row, evaluate it under the same hard filters, then use the common executor. Preserve old dry-run/plan behavior as read-only. Remove direct `HHApplyClient.apply()` calls from `confirm_apply`, `confirm_apply_plan`, `confirm_hh_campaign`, and real apply-from-file paths. A repository test/grep gate in Step 8 ensures only `HHApplicationExecutor` calls `apply_outcome()`.

For campaign/apply-from-file confirmation, create one `LiteralConfirmation` reference whose target rows are exactly the user-confirmed `(resume_id, vacancy_id)` items visible in that immutable plan/file snapshot. Set `max_success` to the smaller of the approved target count and normal per-run capacity; later plan edits cannot widen it. Each target is independently terminal/retryable, while the reference closes when all targets finish or the cap is reached.

- [ ] **Step 6: Expose only masked read-only MCP views**

Add MCP schemas for optional `account`, `limit`, and item/challenge filters. Route them to `hh_autopilot_status`, `hh_autopilot_history`, and challenge listing. Pass every result through `mask_secrets()`. Do not register control, challenge-resolution, canary, run, grant, kill-switch, or raw retry tools. Keep the existing `real_apply_blocked` behavior for agent calls.

- [ ] **Step 7: Preserve independently confirmed maintenance operations**

Do not route resume update, recruiter email, employer reply, cleanup, raw API mutation, profile mutation, or user-authored knowledge-test answer submission through the application grant. Their existing literal confirmation remains unchanged. Add an assertion that `LiveAuthorization(scope="applications")` is rejected by each of those service methods. The autopilot never calls `submit_vacancy_test()` with generated answers.

- [ ] **Step 8: Run service/MCP/compatibility tests and enforce the single POST caller**

Run: `.venv\Scripts\python.exe -m pytest tests/test_services.py tests/test_mcp_safety.py tests/test_hh_foundation.py tests/test_hh_campaign_outcomes.py tests/test_hh_rich_campaign.py tests/test_hh_agent_apply_from_file.py tests/test_strict_type_boundaries.py -q`

Run: `rg.exe -n "\.apply_outcome\(" work_hunter`

Run: `rg.exe -n "\.submit_grounded_form\(" work_hunter`

Expected: tests pass and, for each adapter method, the only production caller outside its own definition is `work_hunter/hh_autopilot/executor.py`. Existing service/campaign/file paths contain no direct `session.apply()` or browser form-submit call.

- [ ] **Step 9: Commit**

```powershell
git add work_hunter/services.py work_hunter/mcp_server.py work_hunter/safety.py tests/test_services.py tests/test_mcp_safety.py tests/test_hh_autopilot_engine.py
git commit -m "feat: unify HH application entry points"
```

### Task 15: Complete CLI Control Surface And Browser Authentication Commands

**Files:**
- Modify: `work_hunter/cli.py`
- Test: `tests/test_hh_autopilot_cli.py`
- Test: `tests/test_cli_contract.py`

**Interfaces:**
- Adds the complete `hh autopilot ...` command family from the design.
- Adds `hh auth login`, `import-cookies`, `logout`, `refresh`, `status`, and explicit profile selection.
- Any command that can authorize or initiate a new HH mutation requires exactly one `--account PROFILE_ID` or `--all`; no fallback to the first account.

- [ ] **Step 1: Write failing parser/account-scope/confirmation contract tests**

```python
# tests/test_hh_autopilot_cli.py
import pytest

from work_hunter.cli import main


@pytest.mark.parametrize(
    "argv",
    [
        ["hh", "autopilot", "enable", "--confirm"],
        ["hh", "autopilot", "disable", "--confirm"],
        ["hh", "autopilot", "run-now"],
        ["hh", "autopilot", "pause"],
        ["hh", "autopilot", "kill-switch", "--confirm"],
    ],
)
def test_mutating_commands_never_select_first_account(argv, capsys) -> None:
    with pytest.raises(SystemExit) as raised:
        main(argv)
    assert raised.value.code == 2


def test_enable_requires_literal_confirm_and_exact_account(fake_app, monkeypatch) -> None:
    install_app(monkeypatch, fake_app)
    main(["hh", "autopilot", "enable", "--account", "work", "--confirm"])
    assert fake_app.calls == [("enable_hh_autopilot", {"accounts": ["work"], "confirm": True})]


def test_read_only_status_defaults_to_all_accounts(fake_app, monkeypatch) -> None:
    install_app(monkeypatch, fake_app)
    main(["hh", "autopilot", "status"])
    assert fake_app.calls == [("hh_autopilot_status", {"account": None})]
```

Add mutual-exclusion tests for `--account` plus `--all`; `--all` atomic enable failure; canary requiring account/resume/vacancy/confirm; run-now requiring account/all but no confirm; recover/status/history/challenges optional account; stop requiring account and run ID; retry requiring account/item ID; and every ambiguity resolution action.

- [ ] **Step 2: Write failing browser-auth command tests**

```python
def test_auth_login_opens_named_profile(fake_app, monkeypatch) -> None:
    install_app(monkeypatch, fake_app)
    main(["hh", "auth", "login", "--account", "work"])
    assert fake_app.calls == [("login_hh_account", {"account": "work"})]


def test_auth_logout_requires_confirm(fake_app, monkeypatch) -> None:
    install_app(monkeypatch, fake_app)
    main(["hh", "auth", "logout", "--account", "work"])
    assert fake_app.calls == [("logout_hh_account", {"account": "work", "confirm": False})]
```

Assert `oauth-start`/`oauth-callback` report user-provided client configuration requirements instead of embedding undeclared credentials.

- [ ] **Step 3: Run CLI tests and verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_hh_autopilot_cli.py tests/test_cli_contract.py -q`

Expected: FAIL because `hh autopilot` and new auth subcommands are absent.

- [ ] **Step 4: Add reusable explicit account selectors**

```python
# work_hunter/cli.py
def add_account_scope(parser, *, allow_all: bool, required: bool) -> None:
    group = parser.add_mutually_exclusive_group(required=required)
    group.add_argument("--account")
    if allow_all:
        group.add_argument("--all", action="store_true", dest="all_accounts")


def selected_accounts(args) -> list[str] | None:
    if getattr(args, "all_accounts", False):
        return None
    account = getattr(args, "account", None)
    return [account] if account else None
```

Use `None` only as an explicit all-accounts marker in facade methods documented to accept it. Do not let mutation routes translate a missing parser value into `None`; argparse rejects it first.

- [ ] **Step 5: Add every autopilot parser exactly once**

Create nested parsers for:

```text
validate [--account]
enable (--account PROFILE | --all) --confirm
disable (--account PROFILE | --all) --confirm
status [--account]
shadow --account PROFILE [--resume ID] [--preset NAME]
canary --account PROFILE --resume ID --vacancy ID --confirm
run-now (--account PROFILE | --all)
recover-now [--account PROFILE]
pause (--account PROFILE | --all)
resume (--account PROFILE | --all)
stop --account PROFILE --run-id ID
kill-switch (--account PROFILE | --all | --global) --confirm
clear-kill-switch (--account PROFILE | --all | --global) --confirm
retry --account PROFILE --item-id ID
challenges [--account PROFILE]
resolve-challenge --account PROFILE --challenge-id ID --action ACTION
history [--account PROFILE] [--limit N]
```

For kill commands, use one required mutually exclusive group containing account/all/global. Restrict `ACTION` to `completed|dismissed|confirmed_applied|confirmed_not_applied_retry|confirmed_not_applied_skip|retry_reconciliation|auth_restored`. Validate positive IDs and limits before calling services.

- [ ] **Step 6: Route commands to thin service methods and stable JSON output**

Each branch calls one facade method and passes literal `args.confirm` without coercing strings. Return masked JSON through existing `print_json()`. `enable --all` invokes one service call with all accounts so validation and grant creation remain atomic; it does not loop in CLI.

- [ ] **Step 7: Add browser-assisted auth routes**

Add:

```text
hh auth login --account PROFILE
hh auth import-cookies --account PROFILE --file PATH
hh auth logout --account PROFILE --confirm
hh auth refresh --account PROFILE
hh auth status [--account PROFILE]
hh auth select-profile --account PROFILE --confirm
```

`login` deliberately opens a visible browser for password/OTP/CAPTCHA entry. Cookie import reads only the explicitly named local file. Logout and active-profile change use existing literal confirmation. Status masks token/cookie values and shows expiration/session health.

- [ ] **Step 8: Run full CLI and safety tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_hh_autopilot_cli.py tests/test_cli_contract.py tests/test_mcp_safety.py tests/test_strict_type_boundaries.py -q`

Expected: all tests pass.

- [ ] **Step 9: Commit**

```powershell
git add work_hunter/cli.py tests/test_hh_autopilot_cli.py tests/test_cli_contract.py
git commit -m "feat: expose HH autopilot CLI controls"
```

### Task 16: Loopback Web API And Full Configurable Autopilot UI

**Files:**
- Modify: `work_hunter/web/server.py`
- Modify: `work_hunter/web/static/index.html`
- Modify: `work_hunter/web/static/app.js`
- Modify: `work_hunter/web/static/app.css`
- Test: `tests/test_hh_autopilot_web.py`
- Test: `tests/test_web_security.py`
- Test: `tests/test_web_ui_contract.py`

**Interfaces:**
- Adds local read endpoints for validation, status, history, challenges, and masked configuration.
- Adds local mutation endpoints for every CLI control with the same explicit account and confirmation rules.
- UI exposes every validated autopilot configuration group, durable authorization state, quota, queue, challenges, shadow, canary, and history.

- [ ] **Step 1: Write failing API safety and account-scope tests**

```python
# tests/test_hh_autopilot_web.py
def test_enable_requires_json_boolean_true_and_explicit_account(web_client) -> None:
    for payload in (
        {},
        {"account": "default", "confirm": False},
        {"account": "default", "confirm": "true"},
        {"confirm": True},
    ):
        response = web_client.post("/api/hh/autopilot/enable", json=payload)
        assert response.status_code in {400, 409}
        assert response.json()["status"] == "blocked"

    response = web_client.post(
        "/api/hh/autopilot/enable", json={"account": "default", "confirm": True}
    )
    assert response.status_code == 200


def test_status_is_masked_and_read_only(web_client) -> None:
    response = web_client.get("/api/hh/autopilot/status?account=default")
    body = response.json()
    assert response.status_code == 200
    assert "access_token" not in response.text
    assert {"schedule", "quota", "queue", "grant", "lease"} <= body.keys()


def test_cross_origin_autopilot_mutation_is_blocked(web_client) -> None:
    response = web_client.post(
        "/api/hh/autopilot/pause",
        json={"account": "default"},
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403
```

Add endpoint tests for enable-all atomicity; disable; pause/resume; stop; account/all/global kill and clear; shadow; exact canary; run-now; recovery without grant; retry; challenge listing/resolution; history pagination; invalid content type; duplicate Origin; invalid Host; and path/query account disagreement.

- [ ] **Step 2: Write failing static UI contract tests**

```python
def test_ui_contains_autopilot_controls_and_no_broad_apply_authorizer(static_files) -> None:
    html = static_files.index_html
    script = static_files.app_js
    for element_id in (
        "hh-autopilot-account",
        "hh-autopilot-enable",
        "hh-autopilot-disable",
        "hh-autopilot-pause",
        "hh-autopilot-run-now",
        "hh-autopilot-shadow",
        "hh-autopilot-canary",
        "hh-autopilot-config",
        "hh-autopilot-queue",
        "hh-autopilot-challenges",
        "hh-autopilot-history",
    ):
        assert f'id="{element_id}"' in html
    assert "allow_broad_apply" not in script
```

- [ ] **Step 3: Run web tests and verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_hh_autopilot_web.py tests/test_web_security.py tests/test_web_ui_contract.py -q`

Expected: FAIL because routes and UI controls do not exist.

- [ ] **Step 4: Add masked GET routes**

Add to `do_GET()`:

```text
/api/hh/autopilot/config
/api/hh/autopilot/validate
/api/hh/autopilot/status
/api/hh/autopilot/history
/api/hh/autopilot/challenges
```

Parse optional account once, reject repeated/conflicting values, call the corresponding facade method, and pass the result through `mask_secrets()`. Config output contains effective validated settings and service-managed state but never tokens, cookies, proxy credentials, or raw candidate form answers.

- [ ] **Step 5: Add JSON-only mutation routes with shared guards**

Add to `do_POST()`:

```text
/api/hh/autopilot/config
/api/hh/autopilot/enable
/api/hh/autopilot/disable
/api/hh/autopilot/pause
/api/hh/autopilot/resume
/api/hh/autopilot/stop
/api/hh/autopilot/kill-switch
/api/hh/autopilot/clear-kill-switch
/api/hh/autopilot/shadow
/api/hh/autopilot/canary
/api/hh/autopilot/run-now
/api/hh/autopilot/recover-now
/api/hh/autopilot/retry
/api/hh/autopilot/resolve-challenge
```

Use a route table whose entry declares `account_required`, `allows_all`, `allows_global`, and `confirmation_required`. Check `payload.get("confirm") is True` for enable/disable/kill/clear/canary. Config writes run full validation, reject managed `enabled`/`authorization_generation` changes, and return whether the policy hash changed and reauthorization is required. Preserve existing loopback, Host, Origin, content-type, body-size, and method guards.

- [ ] **Step 6: Build the settings and status surface**

In `index.html`, add an HH Autopilot section with:

- explicit account selector and account/all badges;
- enabled/grant/policy-match/paused/kill status;
- lease owner/expiry, current/next run, last success/failure;
- daily and per-run quota bars;
- counts for pending, retry, reconciling, manual, and dead;
- buttons for validate, confirmed enable/disable, pause/resume, run-now, stop, kill/clear, recover, shadow, and exact canary;
- a generated settings form for timezone, accounts/resume mappings, schedule, search, every hard filter, ranking/AI, limits/delays, retry/reconciliation, lease, application modes, browser, notifications, and retention;
- queue/history evidence and challenge action lists.

Use an advanced JSON editor backed by the validated config schema for nested resume mappings/presets while keeping common limits/schedule/filter fields as normal inputs. This ensures every parameter is configurable without introducing unvalidated arbitrary keys.

- [ ] **Step 7: Implement UI actions with explicit confirmation descriptors**

`app.js` loads status/config/challenges/history for the selected account. Reuse the existing danger-confirmation modal for enable, disable, kill, clear, and canary; send literal JSON `true` only after the user confirms. Save settings separately from authorization and display “policy changed: enable again” when hash changes. Never send the service-managed generation fields from the editor.

Challenge cards show sanitized type/URL/expiry and legal actions for that type. CAPTCHA action opens the local browser handoff instruction and then resolves only after the user clicks completed. Ambiguity cards expose all four explicit classifications and warn when quota is held.

- [ ] **Step 8: Remove the misleading legacy broad-apply UI control**

Delete `#hh-allow-broad` and its save/load code. If legacy config contains `allow_broad_apply`, show it only in a read-only migration note: it is deprecated and grants no permission. Keep backend compatibility reads until a later config migration removes the key.

- [ ] **Step 9: Run web, service, security, and static contract tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_hh_autopilot_web.py tests/test_web_security.py tests/test_web_ui_contract.py tests/test_services.py -q`

Expected: all tests pass.

- [ ] **Step 10: Commit**

```powershell
git add work_hunter/web/server.py work_hunter/web/static/index.html work_hunter/web/static/app.js work_hunter/web/static/app.css tests/test_hh_autopilot_web.py tests/test_web_security.py tests/test_web_ui_contract.py
git commit -m "feat: add HH autopilot web controls"
```

### Task 17: Fake-HH Contract, Browser, Restart, Concurrency, And Canary Gates

**Files:**
- Create: `tests/fakes/hh_autopilot_server.py`
- Create: `tests/fakes/__init__.py`
- Create: `tests/test_hh_autopilot_e2e.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Produces an in-process HTTP HH contract server with deterministic scenario controls and an append-only request journal.
- Verifies durable business outcomes through real HTTP and SQLite, including an accepted POST whose response connection is lost.
- Adds pytest markers `browser`, `restart`, `concurrency`, and `live_canary`; only `live_canary` is opt-in.

- [ ] **Step 1: Scaffold the fake-HH server with unimplemented scenario dispatch**

```python
# tests/fakes/hh_autopilot_server.py
@dataclass
class FakeHHState:
    vacancies: list[dict[str, object]] = field(default_factory=list)
    negotiations: list[dict[str, object]] = field(default_factory=list)
    application_scenarios: dict[str, str] = field(default_factory=dict)
    requests: list[dict[str, object]] = field(default_factory=list)
    valid_token: str = "token"


class FakeHHHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/vacancies"):
            return self.server.reply_json(200, paginated_vacancies(self.server.state, self.path))
        if self.path.startswith("/negotiations"):
            return self.server.reply_json(200, paginated_negotiations(self.server.state, self.path))
        if self.path.startswith("/vacancies/"):
            return self.server.reply_json(200, vacancy_by_path(self.server.state, self.path))
        return self.server.reply_json(404, {"errors": [{"value": "not_found"}]})

    def do_POST(self):
        if self.path == "/negotiations":
            payload = self.server.read_form(self)
            return dispatch_application_scenario(self, payload)
        if self.path == "/token":
            return dispatch_token_scenario(self)
        return self.server.reply_json(404, {"errors": [{"value": "not_found"}]})
```

At this point, leave `dispatch_application_scenario()` raising `NotImplementedError`; the first end-to-end test must prove the harness is exercising the real application path.

- [ ] **Step 2: Write the core end-to-end happy path and multipage test**

```python
# tests/test_hh_autopilot_e2e.py
def test_multipage_filter_rank_limit_apply_journal_scheduler(fake_hh, app_factory) -> None:
    fake_hh.seed_pages(3, per_page=2, duplicate_ids={"v-2"})
    fake_hh.set_vacancy("v-filter", title="Bitrix developer")
    app = app_factory(fake_hh.base_url, daily_limit=2, per_run_limit=2, send_delay=(0, 0))
    app.enable_hh_autopilot(accounts=["default"], confirm=True)

    result = app.tick_hh_autopilot()

    assert fake_hh.application_post_count == 2
    assert result["runs"][0]["applied"] == 2
    assert app.storage.query_readonly(
        "SELECT COUNT(*) AS n FROM hh_autopilot_items WHERE last_outcome_code LIKE 'hard_filter:%'"
    )[0]["n"] >= 1
    assert app.hh_autopilot_status(account="default")["quota"]["used"] == 2
    assert app.hh_autopilot_history(account="default")["events"]
```

- [ ] **Step 3: Run the first contract test and verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_hh_autopilot_e2e.py::test_multipage_filter_rank_limit_apply_journal_scheduler -q`

Expected: FAIL at the fake server's unimplemented application scenario after real pagination/filter/ranking reaches `POST /negotiations`.

- [ ] **Step 4: Complete every deterministic fake-HH scenario**

Implement scenarios `created`, `accepted_then_close`, `duplicate`, `closed`, `forbidden`, `redirect_form`, `screening`, `captcha`, `assessment`, `auth_expired`, `rate_limited`, `server_error`, `invalid_json_after_accept`, and `hh_daily_limit`. Every request journal entry stores method, path, vacancy/resume IDs, and timestamp but excludes Authorization/cookies and message contents. Re-run the focused test and expect it to pass.

- [ ] **Step 5: Write the real-socket accepted-then-disconnected restart test**

```python
@pytest.mark.restart
def test_restart_reconciles_accepted_post_without_duplicate(fake_hh, app_factory, tmp_path) -> None:
    fake_hh.scenario("v-1", "accepted_then_close")
    first = app_factory(fake_hh.base_url, root=tmp_path, send_delay=(0, 0))
    first.enable_hh_autopilot(accounts=["default"], confirm=True)
    first.run_hh_autopilot(accounts=["default"])
    assert fake_hh.application_post_count == 1
    first.close()

    fake_hh.advance_clock(seconds=121)
    second = app_factory(fake_hh.base_url, root=tmp_path, send_delay=(0, 0))
    second.kill_hh_autopilot(accounts=["default"], confirm=True)
    recovered = second.recover_hh_autopilot(account="default")

    assert recovered["applied"] == 1
    assert fake_hh.application_post_count == 1
    assert second.storage.query_readonly(
        "SELECT COUNT(*) AS n FROM applications WHERE account_profile_id='default'"
    )[0]["n"] == 1
    assert second.storage.query_readonly(
        "SELECT state FROM hh_autopilot_quota_reservations"
    ) == [{"state": "consumed"}]
```

Use an actual socket close after the fake server commits its negotiation. Do not inject a direct executor outcome in this test.

- [ ] **Step 6: Add the complete remote-outcome contract matrix**

Parameterize and assert durable outcomes:

| Fake outcome | Item state/outcome | Reservation | New POST allowed |
|---|---|---|---|
| `201 created` | `applied` | consumed | no |
| duplicate + matching same resume | reconciled `applied` | consumed | no |
| duplicate + older/other resume | `duplicate_external` | released, external sync if today | no |
| unclassifiable duplicate | `manual_challenge/ambiguous_application` | held | no |
| vacancy closed/forbidden/invalid | skipped stable code | released | no |
| pre-connect failure | persisted retry | released | after due time |
| accepted then close/timeout/invalid JSON | reconciling | held | only after confirmed absence |
| `429` | retry + account cooldown | released | after reset |
| HH daily limit | retry + local-day cooldown | released | after reset |
| retryable `5xx` | bounded retry | released | after due time |
| auth before dispatch | account manual auth | released | after auth resolution |
| auth during reconciliation | account manual auth | held | reconciliation only |
| form/screening complete | browser/application continuation | consumed on success | no duplicate |
| CAPTCHA/assessment | manual challenge | released | after completed action |

For each scenario, inspect the item, attempt, reservation, application, guard, event, run counters, and fake-server request journal.

- [ ] **Step 7: Add restart and concurrency sequence tests**

Use two separate `Storage`/`WorkHunter` instances on the same database and `ThreadPoolExecutor` to verify:

- one account has one lease owner;
- a stale owner cannot commit after takeover;
- replacement owner reconciles stale `applying` before any new POST;
- process death immediately after reservation does not leak or overshoot quota;
- two accounts may apply to the same vacancy and retain separate application rows;
- policy change resets never-dispatched candidates but reconciles possibly-sent work first;
- pause/disable/kill during an in-flight request blocks the next POST but permits recovery/finalization;
- cooldown blocks every ready item for one account while another account continues;
- timezone change is rejected with an active grant or unresolved held reservation;
- challenge expiry/dismissal and late ambiguity classification obey their distinct rules.

Each concurrency test uses barriers around the exact lease/reservation/POST/finalization phase; no timing-only race assertions.

- [ ] **Step 8: Add browser fixture integration tests**

With `pytest.importorskip("playwright.sync_api")`, serve the local form/CAPTCHA fixtures and verify real DOM inspection, exact grounded field fill, cookie persistence, unknown required field rejection, manual CAPTCHA detection, and continuation after resolution. Assert the CAPTCHA iframe receives no automated click or input event.

- [ ] **Step 9: Add an opt-in live canary test without default claims**

```python
@pytest.mark.live_canary
def test_live_named_vacancy_canary(live_app):
    if os.environ.get("WORK_HUNTER_HH_LIVE_CANARY") != "1":
        pytest.skip("explicit live canary opt-in required")
    account = require_env("WORK_HUNTER_HH_CANARY_ACCOUNT")
    resume = require_env("WORK_HUNTER_HH_CANARY_RESUME")
    vacancy = require_env("WORK_HUNTER_HH_CANARY_VACANCY")
    result = live_app.canary_hh_autopilot(
        account=account, resume_id=resume, vacancy_id=vacancy, confirm=True
    )
    assert result["successful_applications"] <= 1
```

The default suite excludes `live_canary`. Store the result in the normal journal and never print credentials.

- [ ] **Step 10: Register markers and run deterministic contract gates**

Add marker descriptions to `pyproject.toml`, then run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_hh_autopilot_e2e.py -m "not live_canary" -q
.venv\Scripts\python.exe -m pytest tests/test_hh_autopilot_e2e.py -m restart -q
.venv\Scripts\python.exe -m pytest tests/test_hh_autopilot_e2e.py -m concurrency -q
```

Expected: all deterministic fake-server, restart, and concurrency tests pass. Browser-marked cases pass when Playwright is installed or skip with an explicit dependency reason.

- [ ] **Step 11: Commit**

```powershell
git add tests/fakes tests/test_hh_autopilot_e2e.py pyproject.toml
git commit -m "test: verify HH autopilot contracts end to end"
```

### Task 18: Documentation, CI, Packaging, Security Audit, And Release Evidence

**Files:**
- Create: `docs/hh-autopilot.md`
- Modify: `README.md`
- Modify: `.github/workflows/ci.yml`
- Modify: `Dockerfile`
- Test: `tests/test_package_contract.py`

**Interfaces:**
- Documents exact setup, all configuration, account authorization, shadow/canary rollout, scheduler installation, recovery, CAPTCHA/forms, and operational limits.
- Records deterministic contract verification separately from optional current-HH live-canary evidence.
- CI and package smoke tests include migrations, static UI, fake-HH contracts, restart/concurrency, and browser fixture coverage.

- [ ] **Step 1: Write failing package/default-safety contract tests**

```python
# tests/test_package_contract.py
from importlib.resources import files


def test_package_contains_autopilot_migrations_and_ui() -> None:
    package = files("work_hunter")
    assert package.joinpath("migrations/0002_account_aware_applications.sql").is_file()
    assert package.joinpath("migrations/0003_hh_autopilot_runtime.sql").is_file()
    assert package.joinpath("web/static/index.html").is_file()


def test_fresh_install_is_disabled_and_has_no_grant(tmp_path) -> None:
    app = WorkHunter(root=tmp_path)
    status = app.hh_autopilot_status(account="default")
    assert status["enabled"] is False
    assert status["grant"]["active"] is False
    assert status["quota"]["used"] == 0
```

Add an upgrade test from a pre-0002 database, sentinel migration reporting, CLI `--help` command presence, and a masked config/status export test containing representative token/cookie/proxy/form secrets.

- [ ] **Step 2: Run package contract tests and verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_package_contract.py -q`

Expected: FAIL until documentation/package/CLI contracts from previous tasks are present.

- [ ] **Step 3: Write the operator guide with exact commands and safety behavior**

Create `docs/hh-autopilot.md` with these sections:

1. Architecture and durable state flow.
2. Install extras: `.[browser,ui]`, Chromium setup, local private data directory.
3. HH account login/import/status/logout and user-supplied OAuth requirements.
4. Full `sources.hh.autopilot` schema, defaults, bounds, enums, and examples for multiple accounts/resumes/presets.
5. Validate -> shadow -> named canary -> per-run one -> normal enable rollout.
6. Every CLI command and equivalent loopback UI action.
7. Windows Task Scheduler and Linux cron invocation using `examples/hh-autopilot-runner.json`.
8. Quotas, delays, time windows, cooldowns, retries, lease takeover, and recovery behavior.
9. Screening/forms: profile-grounded only; unknown required data skips the vacancy.
10. CAPTCHA/assessment: manual browser handoff, cookie continuity, resolution/expiry actions, no automatic solver.
11. Ambiguous application classification and held-quota consequences.
12. Pause vs disable vs stop vs kill switch.
13. Secret masking, private screenshots, retention, and backup/restore.
14. Troubleshooting stable outcome codes.
15. Verification statement and opt-in live-canary procedure.

The guide must say plainly: fake-HH/local browser contracts do not prove the current live HH site; only a successful opt-in named canary provides that evidence. It must also say that autonomous resume raise, replies, recruiter email, cleanup, and independent token-refresh schedules belong to the separately approved parity-maintenance slice; their existing manually confirmed operations remain available but are not authorized by the application grant.

- [ ] **Step 4: Update README quick start and remove obsolete authorization claims**

Link the operator guide. Replace any suggestion that `allow_broad_apply` authorizes live sends. Add concise commands:

```powershell
work-hunter hh auth login --account default
work-hunter hh autopilot validate --account default
work-hunter hh autopilot shadow --account default
work-hunter hh autopilot canary --account default --resume RESUME_ID --vacancy VACANCY_ID --confirm
work-hunter hh autopilot enable --account default --confirm
work-hunter runner --plan examples/hh-autopilot-runner.json
```

Document default limits `50/day`, `10/run`, `45–120s`, `08:00–21:00 Europe/Moscow`, hourly, `20x100`, and that all are validated/configurable.

- [ ] **Step 5: Extend CI with deterministic and browser autopilot gates**

In the normal test job, run the full suite excluding only `live_canary` and the existing browser-only file. In the browser job, install Chromium and run both existing browser UI tests and `tests/test_hh_autopilot_e2e.py -m browser`. Add a dedicated restart/concurrency invocation so failures are visible by name. Keep Python 3.11 and 3.12 matrix coverage.

- [ ] **Step 6: Verify Docker runtime and private persistence**

Ensure `Dockerfile` installs the package with UI support, includes migrations/static assets, runs as the existing non-root user, and declares the existing data root as a volume. Document that interactive browser login/CAPTCHA handoff requires either a host browser session/cookie import or a supported visible browser environment; do not claim headless CAPTCHA solving.

- [ ] **Step 7: Run all deterministic tests**

```powershell
.venv\Scripts\python.exe -m pytest -q -m "not live_canary"
.venv\Scripts\python.exe -m pytest tests/test_hh_autopilot_e2e.py -m restart -q
.venv\Scripts\python.exe -m pytest tests/test_hh_autopilot_e2e.py -m concurrency -q
```

Expected: all deterministic tests pass; browser tests either pass with installed Playwright or skip only for the explicit missing optional dependency.

- [ ] **Step 8: Run static quality, migration, build, audit, and wheel smoke gates**

```powershell
.venv\Scripts\ruff.exe check .
.venv\Scripts\ruff.exe format --check .
.venv\Scripts\mypy.exe work_hunter
git diff --check
.venv\Scripts\python.exe -m build
.venv\Scripts\python.exe -m pip check
.venv\Scripts\python.exe -m pip_audit
```

Create a temporary virtual environment outside the repository, install the built wheel, then run:

```powershell
& $smokePython -m pip install (Get-ChildItem dist\work_hunter-*.whl | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
& $smokePython -m work_hunter --help
& $smokePython -m work_hunter hh autopilot status
```

Expected: build/audit/type/lint/format/diff gates pass, the wheel imports without the source tree, migrations create a disabled fresh database, and CLI help/status works.

- [ ] **Step 9: Perform the definition-of-done evidence audit**

In `docs/hh-autopilot.md`, add a verification table mapping every Definition of Done bullet from the design to one concrete test module/test name. Run:

```powershell
rg.exe -n "access_token|refresh_token|Authorization|cookie|proxy" .work-hunter\reports .work-hunter\private
```

Expected: no secret values appear in normal reports/events. File names or masked key names may appear. Inspect one successful, one skipped, one retry, one CAPTCHA, and one ambiguous journal sequence and record only stable codes/IDs in the verification table.

- [ ] **Step 10: Optionally run the explicitly named live canary**

Only when the operator supplies all three canary environment values and sets `WORK_HUNTER_HH_LIVE_CANARY=1`, run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_hh_autopilot_e2e.py -m live_canary -q
```

If it is not run, release notes say “deterministic contract verified; current live HH application flow not proven.” If it succeeds, record date, software revision, masked account, named vacancy ID, outcome, and journal ID without credentials or personal answers.

- [ ] **Step 11: Commit documentation and release gates**

```powershell
git add docs/hh-autopilot.md README.md .github/workflows/ci.yml Dockerfile tests/test_package_contract.py
git commit -m "docs: ship HH autopilot operations guide"
```

---

## Specification Coverage Map

| Approved design area | Implementation tasks |
|---|---|
| State machine and stable outcomes | 1, 4, 9, 10, 11 |
| Account-aware history and durable persistence | 3, 4 |
| Validated configuration and canonical authorization hash | 2, 5 |
| Durable grants, one-shot confirmation, pause/kill safety matrix | 5, 9, 12, 14 |
| Fenced lease, quotas, held ambiguity, cooldown | 6, 9, 10 |
| Multipage search, checkpoints, dedupe, recommendations, shadow | 7, 12 |
| Hard filters, deterministic/AI ranking, resume selection | 8 |
| Delivery certainty, retry, atomic application finalization | 9 |
| Account-wide reconciliation and grant-independent recovery | 10, 13 |
| Grounded screening/forms, manual CAPTCHA/assessment, auth session | 11 |
| Unified live/manual/canary pipeline and scheduler | 12, 13, 14 |
| Complete CLI and loopback UI contracts | 15, 16 |
| Contract, browser, restart, concurrency, compatibility evidence | 17, 18 |
| Honest reference parity boundary and maintenance-slice separation | 14, 18 |

---

## Execution Completion Checklist

- [ ] All 18 task commits are present in order and each task's focused RED/GREEN cycle was observed.
- [ ] No production path outside `HHApplicationExecutor` can issue a new HH application POST.
- [ ] Recovery can finalize possibly-sent attempts with grants revoked and kill switches active, but cannot search/reserve/POST.
- [ ] Fresh installs and upgrades remain disabled until a literal confirmed enable creates an exact account/scope/policy generation.
- [ ] Full deterministic, restart, concurrency, compatibility, lint, format, type, build, dependency, package, and secret-masking gates pass.
- [ ] Documentation distinguishes local contract verification from an optional live HH canary.
- [ ] Begin the separate parity-maintenance specification only after these core acceptance gates are green; its grants remain distinct from `applications`.
