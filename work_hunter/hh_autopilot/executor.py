from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Protocol
from zoneinfo import ZoneInfo

from work_hunter.safety import require_hh_dispatch_authorization

from .config import AutopilotSettings
from .repository import AutopilotRepository, ItemRecord, LeaseRecord
from .types import (
    AutopilotState,
    DeliveryCertainty,
    DispatchConfigSnapshot,
    DispatchOutcome,
    ExecutionResult,
    LiteralConfirmation,
    LiveAuthorization,
    RetryDecision,
)


PERMANENT_CODES = frozenset(
    {
        "duplicate_external",
        "external_applied",
        "vacancy_closed",
        "forbidden",
        "invalid_request",
        "missing_required_data",
        "screening_disabled",
        "form_disabled",
        "ai_unavailable",
    }
)
MANUAL_CODES = frozenset(
    {
        "auth_expired",
        "manual_auth",
        "manual_captcha",
        "manual_assessment",
        "form_required",
    }
)
RECONCILIATION_CODES = frozenset(
    {
        "duplicate",
        "ambiguous_remote_result",
        "post_dispatch_parse_error",
        "post_dispatch_network_error",
    }
)


class ApplicationTransport(Protocol):
    def apply_outcome(
        self,
        vacancy_id: str,
        resume_id: str,
        message: str,
        *,
        timeout_seconds: int,
    ) -> DispatchOutcome: ...


class CoverLetterPort(Protocol):
    def render(self, context: CoverLetterContext) -> str: ...


@dataclass(frozen=True)
class CoverLetterContext:
    item_id: int
    account_id: str
    vacancy_id: str
    resume_id: str
    cover_letter_mode: str


class HHRetryPolicy:
    def __init__(self, config: Mapping[str, Any]) -> None:
        if not isinstance(config, Mapping):
            raise TypeError("retry config must be a mapping")
        self.max_attempts = _config_int(config, "max_attempts", 1, 20)
        self.base_delay_seconds = _config_int(
            config,
            "base_delay_seconds",
            1,
            86_400,
        )
        self.max_delay_seconds = _config_int(
            config,
            "max_delay_seconds",
            self.base_delay_seconds,
            86_400,
        )
        jitter = config.get("jitter_ratio")
        if isinstance(jitter, bool) or not isinstance(jitter, (int, float)):
            raise TypeError("retry.jitter_ratio must be numeric")
        self.jitter_ratio = float(jitter)
        if not 0.0 <= self.jitter_ratio <= 1.0:
            raise ValueError("retry.jitter_ratio must be in 0..1")
        self.reconciliation_delay_seconds = _config_int(
            config,
            "reconciliation_delay_seconds",
            1,
            86_400,
        )

    def classify(
        self,
        outcome: DispatchOutcome,
        *,
        attempt_count: int,
        now: datetime,
        random_value: float,
    ) -> RetryDecision:
        if type(outcome) is not DispatchOutcome:
            raise TypeError("outcome must be an exact DispatchOutcome")
        if type(attempt_count) is not int or attempt_count < 0:
            raise ValueError("attempt_count must be a nonnegative integer")
        instant = _utc_instant(now)
        if type(random_value) not in {int, float}:
            raise TypeError("random_value must be numeric")
        sample = float(random_value)
        if not 0.0 <= sample <= 1.0:
            raise ValueError("random_value must be in 0..1")

        if (
            outcome.certainty is DeliveryCertainty.POSSIBLY_SENT
            or outcome.code in {"duplicate", "ambiguous_remote_result"}
        ):
            return RetryDecision(
                target=AutopilotState.RECONCILING,
                reason=outcome.code,
                next_attempt_at=(
                    instant
                    + timedelta(seconds=self.reconciliation_delay_seconds)
                ).isoformat(),
            )
        if outcome.code in MANUAL_CODES:
            return RetryDecision(
                target=AutopilotState.MANUAL_CHALLENGE,
                reason=outcome.code,
            )
        if outcome.code in PERMANENT_CODES:
            return RetryDecision(
                target=AutopilotState.SKIPPED,
                reason=outcome.code,
            )
        if attempt_count >= self.max_attempts:
            return RetryDecision(
                target=AutopilotState.DEAD,
                reason="retry_exhausted",
            )
        base = min(
            self.max_delay_seconds,
            self.base_delay_seconds * (2 ** max(0, attempt_count - 1)),
        )
        factor = (
            (1.0 - self.jitter_ratio)
            + 2.0 * self.jitter_ratio * sample
        )
        delay = base * factor
        if outcome.retry_after_seconds is not None:
            delay = max(delay, outcome.retry_after_seconds)
        return RetryDecision(
            target=AutopilotState.RETRY_WAIT,
            reason=outcome.code,
            next_attempt_at=(
                instant + timedelta(seconds=delay)
            ).isoformat(),
        )


class HHApplicationExecutor:
    def __init__(
        self,
        repository: AutopilotRepository,
        transport: ApplicationTransport,
        cover_letters: CoverLetterPort,
        *,
        settings_provider: Callable[[], AutopilotSettings],
        policy_hash_provider: Callable[[str], str],
        random_source: Callable[[], float] = random.random,
    ) -> None:
        if not isinstance(repository, AutopilotRepository):
            raise TypeError("repository must be an AutopilotRepository")
        if not callable(settings_provider):
            raise TypeError("settings_provider must be callable")
        if not callable(policy_hash_provider):
            raise TypeError("policy_hash_provider must be callable")
        if not callable(random_source):
            raise TypeError("random_source must be callable")
        if not callable(getattr(transport, "apply_outcome", None)):
            raise TypeError("transport must expose apply_outcome")
        if not callable(getattr(cover_letters, "render", None)):
            raise TypeError("cover_letters must expose render")
        self.repository = repository
        self.transport = transport
        self.cover_letters = cover_letters
        self.settings_provider = settings_provider
        self.policy_hash_provider = policy_hash_provider
        self.random_source = random_source

    def execute(
        self,
        item_id: int,
        authorization: LiteralConfirmation | LiveAuthorization,
        lease: LeaseRecord,
        now: datetime | str | None = None,
    ) -> ExecutionResult:
        instant = _utc_instant(now)
        current = self.repository.get_item(item_id)
        if current is None:
            raise KeyError(f"item {item_id} does not exist")
        if not isinstance(lease, LeaseRecord):
            raise TypeError("lease must be a LeaseRecord")
        require_hh_dispatch_authorization(
            authorization,
            account_id=current.account_id,
            lease_account_id=lease.account_id,
            fencing_token=lease.fencing_token,
        )
        render_snapshot, render_settings = self._current_snapshot(
            current,
            authorization,
            now=instant,
        )
        retry_policy = HHRetryPolicy(render_settings.retry)

        try:
            message = self.cover_letters.render(
                CoverLetterContext(
                    item_id=current.id,
                    account_id=current.account_id,
                    vacancy_id=current.vacancy_id,
                    resume_id=current.resume_id,
                    cover_letter_mode=render_snapshot.cover_letter_mode,
                )
            )
            if type(message) is not str:
                raise TypeError("cover-letter renderer must return text")
        except Exception:
            outcome = DispatchOutcome(
                code="internal_error",
                certainty=DeliveryCertainty.DEFINITELY_NOT_SENT,
            )
            decision = retry_policy.classify(
                outcome,
                attempt_count=current.application_attempt_count,
                now=instant,
                random_value=self.random_source(),
            )
            failed = self.repository.record_pre_dispatch_failure(
                item_id=current.id,
                expected_version=current.version,
                authorization=authorization,
                fencing_token=lease.fencing_token,
                outcome=outcome,
                decision=decision,
                now=instant,
            )
            return ExecutionResult(
                item_id=failed.id,
                attempt_id=failed.active_attempt_id,
                reservation_id=None,
                state=failed.state,
                outcome_code=outcome.code,
            )

        # Re-read the authoritative projection and policy after bounded letter
        # rendering.  Providers used by the application wire this read through
        # the existing config lock; prepare_dispatch then compares the snapshot
        # to the DB grant/run/control state in one BEGIN IMMEDIATE.
        snapshot, settings = self._current_snapshot(
            current,
            authorization,
            now=instant,
        )
        if snapshot.cover_letter_mode != render_snapshot.cover_letter_mode:
            raise RuntimeError("cover-letter policy changed before dispatch")
        retry_policy = HHRetryPolicy(settings.retry)
        # No blocking/error-prone local work is allowed between this renewal,
        # the atomic dispatch preparation, and the one wire POST.
        lease = self.repository.renew_lease(
            lease,
            ttl_seconds=snapshot.lease_ttl_seconds,
            now=instant,
        )
        prepared = self.repository.prepare_dispatch(
            item_id=current.id,
            expected_version=current.version,
            authorization=authorization,
            fencing_token=lease.fencing_token,
            snapshot=snapshot,
            now=instant,
        )
        try:
            outcome = self.transport.apply_outcome(
                prepared.vacancy_id,
                prepared.resume_id,
                message,
                timeout_seconds=snapshot.request_timeout_seconds,
            )
            if type(outcome) is not DispatchOutcome:
                raise TypeError("transport returned an invalid dispatch outcome")
        except Exception:
            outcome = DispatchOutcome(
                code="post_dispatch_network_error",
                certainty=DeliveryCertainty.POSSIBLY_SENT,
            )

        finalized_at = instant if now is not None else datetime.now(timezone.utc)
        if outcome.code == "applied":
            item = self.repository.finalize_applied(
                prepared,
                outcome,
                now=finalized_at,
            )
        else:
            decision = retry_policy.classify(
                outcome,
                attempt_count=prepared.attempt_count,
                now=finalized_at,
                random_value=self.random_source(),
            )
            if (
                outcome.code == "hh_daily_limit"
                and outcome.retry_after_seconds is None
            ):
                decision = RetryDecision(
                    target=AutopilotState.RETRY_WAIT,
                    reason=outcome.code,
                    next_attempt_at=_next_local_day(
                        finalized_at,
                        prepared.timezone_name,
                    ).isoformat(),
                )
            if decision.target is AutopilotState.RECONCILING:
                item = self.repository.record_possibly_sent(
                    prepared,
                    outcome,
                    decision,
                    now=finalized_at,
                )
            else:
                item = self.repository.finalize_definite_failure(
                    prepared,
                    outcome,
                    decision,
                    challenge_expiry_hours=int(
                        settings.application["challenge_expiry_hours"]
                    ),
                    now=finalized_at,
                )
        return ExecutionResult(
            item_id=item.id,
            attempt_id=prepared.attempt_id,
            reservation_id=prepared.reservation_id,
            state=item.state,
            outcome_code=outcome.code,
        )

    def _current_snapshot(
        self,
        item: ItemRecord,
        authorization: LiteralConfirmation | LiveAuthorization,
        *,
        now: datetime,
    ) -> tuple[DispatchConfigSnapshot, AutopilotSettings]:
        settings = self.settings_provider()
        if type(settings) is not AutopilotSettings:
            raise TypeError("settings_provider returned an invalid snapshot")
        account = next(
            (
                value
                for value in settings.accounts
                if value.profile_id.strip().casefold() == item.account_id
            ),
            None,
        )
        if account is None:
            raise RuntimeError("current settings do not contain the item account")
        if type(authorization) is LiveAuthorization:
            current_policy_hash = self.policy_hash_provider(item.account_id)
            if (
                not isinstance(current_policy_hash, str)
                or not current_policy_hash.strip()
            ):
                raise RuntimeError("current policy hash is unavailable")
            policy_hash = current_policy_hash.strip()
        else:
            run = self.repository.get_run(item.last_run_id)
            if run is None:
                raise RuntimeError("literal run disappeared")
            policy_hash = run.policy_hash
        snapshot = DispatchConfigSnapshot(
            account_id=item.account_id,
            enabled=account.enabled,
            paused=account.paused,
            authorization_generation=account.authorization_generation,
            policy_hash=policy_hash,
            within_scheduler_window=_within_scheduler_window(settings, now),
            timezone_name=settings.timezone,
            daily_limit=settings.limits.daily_success,
            run_limit=settings.limits.per_run_success,
            resume_policy=str(settings.application["resume_policy"]),
            cover_letter_mode=str(settings.application["cover_letter_mode"]),
            lease_ttl_seconds=settings.lease.ttl_seconds,
            request_timeout_seconds=settings.lease.request_timeout_seconds,
        )
        return snapshot, settings


def _config_int(
    config: Mapping[str, Any],
    key: str,
    minimum: int,
    maximum: int,
) -> int:
    value = config.get(key)
    if type(value) is not int:
        raise TypeError(f"retry.{key} must be an integer")
    if not minimum <= value <= maximum:
        raise ValueError(f"retry.{key} is out of range")
    return value


def _utc_instant(value: datetime | str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.strip())
    elif isinstance(value, datetime):
        parsed = value
    else:
        raise TypeError("now must be a datetime or ISO timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _within_scheduler_window(
    settings: AutopilotSettings,
    now: datetime,
) -> bool:
    local = now.astimezone(ZoneInfo(settings.timezone))
    schedule = settings.schedule
    if local.isoweekday() not in schedule["days"]:
        return False
    clock = local.strftime("%H:%M")
    return str(schedule["start"]) <= clock <= str(schedule["end"])


def _next_local_day(now: datetime, timezone_name: str) -> datetime:
    zone = ZoneInfo(timezone_name)
    local = now.astimezone(zone)
    tomorrow = (local + timedelta(days=1)).date()
    return datetime.combine(
        tomorrow,
        datetime.min.time(),
        tzinfo=zone,
    ).astimezone(timezone.utc)


__all__ = [
    "CoverLetterContext",
    "HHApplicationExecutor",
    "HHRetryPolicy",
]
