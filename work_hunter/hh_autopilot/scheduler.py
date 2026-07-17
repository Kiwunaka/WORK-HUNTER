from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from zoneinfo import ZoneInfo

from work_hunter.hh_agent.notifications import NotificationEvent

from .config import AccountSettings, AutopilotSettings, parse_autopilot_settings
from .types import RunReport, RunRequest


@dataclass(frozen=True)
class ScheduledAccount:
    profile_id: str
    enabled: bool
    paused: bool
    authorization_generation: int | None
    timezone: str
    days: tuple[int, ...]
    start: time
    end: time
    interval_minutes: int

    @classmethod
    def from_settings(
        cls,
        account: AccountSettings,
        settings: AutopilotSettings,
    ) -> ScheduledAccount:
        schedule = settings.schedule
        return cls(
            profile_id=account.profile_id,
            enabled=account.enabled,
            paused=account.paused,
            authorization_generation=account.authorization_generation,
            timezone=settings.timezone,
            days=tuple(int(value) for value in schedule["days"]),
            start=time.fromisoformat(str(schedule["start"])),
            end=time.fromisoformat(str(schedule["end"])),
            interval_minutes=int(schedule["interval_minutes"]),
        )


@dataclass(frozen=True)
class SchedulerReport:
    recovery: Any
    challenge_updates: Any
    runs: tuple[RunReport, ...]
    retention: Any = None
    notifications: tuple[dict[str, Any], ...] = ()


class SchedulePolicy:
    def __init__(self, repository: Any) -> None:
        self.repository = repository

    def is_due(self, account: ScheduledAccount, now: datetime) -> bool:
        instant = _utc(now)
        if not self.is_window_open(account, instant):
            return False
        last = self.repository.last_scheduled_at(account.profile_id)
        return last is None or instant >= last + timedelta(
            minutes=account.interval_minutes
        )

    @staticmethod
    def is_window_open(account: ScheduledAccount, now: datetime) -> bool:
        instant = _utc(now)
        local = instant.astimezone(ZoneInfo(account.timezone))
        local_time = local.timetz().replace(tzinfo=None)
        return bool(
            local.isoweekday() in account.days
            and account.start <= local_time <= account.end
        )

    def next_due(self, account: ScheduledAccount, now: datetime) -> datetime:
        instant = _utc(now)
        last = self.repository.last_scheduled_at(account.profile_id)
        local_now = instant.astimezone(ZoneInfo(account.timezone))
        local_time = local_now.timetz().replace(tzinfo=None)
        inside_window = (
            local_now.isoweekday() in account.days
            and account.start <= local_time <= account.end
        )
        if inside_window:
            interval = timedelta(minutes=account.interval_minutes)
            due_from_last = None if last is None else last + interval
            base = (
                due_from_last
                if due_from_last is not None and due_from_last > instant
                else instant + interval
            )
        else:
            base = instant
        return self._advance_to_window(account, base)

    @staticmethod
    def _advance_to_window(
        account: ScheduledAccount,
        candidate: datetime,
    ) -> datetime:
        zone = ZoneInfo(account.timezone)
        local = candidate.astimezone(zone)
        for _ in range(8):
            local_time = local.timetz().replace(tzinfo=None)
            if local.isoweekday() in account.days:
                if local_time < account.start:
                    local = datetime.combine(local.date(), account.start, zone)
                    return local.astimezone(timezone.utc)
                if local_time <= account.end:
                    return local.astimezone(timezone.utc)
            next_day = local.date() + timedelta(days=1)
            local = datetime.combine(next_day, account.start, zone)
        raise RuntimeError("schedule has no reachable configured day")


class HHAutopilotScheduler:
    def __init__(
        self,
        *,
        repository: Any,
        config_loader: Any,
        engine: Any,
        recovery_sweep: Any,
        challenge_handler: Any | None = None,
        retention_root: str | Path | None = None,
        notification_sinks: Mapping[str, Any] | Sequence[Any] = (),
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.repository = repository
        self.config_loader = config_loader
        self.engine = engine
        self.recovery_sweep = recovery_sweep
        self.challenge_handler = challenge_handler
        self.retention_root = None if retention_root is None else Path(retention_root)
        self.notification_sinks = _notification_targets(notification_sinks)
        self.clock = clock
        self.policy = SchedulePolicy(repository)

    def tick(self, now: datetime | None = None) -> SchedulerReport:
        instant = _utc(self.clock() if now is None else now)
        recovery = self.recovery_sweep.run(now=instant)
        challenge_updates: Any = ()
        expire_due = getattr(self.challenge_handler, "expire_due", None)
        if callable(expire_due):
            challenge_updates = expire_due(instant)

        settings = self._load_settings()
        retention = self.repository.prune_retention(
            event_days=int(settings.retention["event_days"]),
            challenge_artifact_days=int(
                settings.retention["challenge_artifact_days"]
            ),
            artifact_root=self.retention_root,
            now=instant,
        )
        runs: list[RunReport] = []
        for raw_account in settings.accounts:
            account = ScheduledAccount.from_settings(raw_account, settings)
            if not self._dispatchable(account):
                continue
            trigger = ""
            if self.policy.is_due(account, instant):
                next_due = self.policy.next_due(account, instant)
                if not self.repository.claim_schedule_slot(
                    account.profile_id,
                    instant,
                    next_due,
                    interval_minutes=account.interval_minutes,
                ):
                    continue
                trigger = "schedule"
            elif self.policy.is_window_open(
                account,
                instant,
            ) and self.repository.has_due_dispatch_work(
                account.profile_id,
                now=instant,
            ):
                trigger = "retry"
            if not trigger:
                continue
            report = self.engine.run(
                RunRequest(account.profile_id, trigger=trigger)
            )
            if not isinstance(report, RunReport):
                raise TypeError("engine returned an invalid run report")
            runs.append(report)
        notifications = self._deliver_notifications(settings, instant)
        return SchedulerReport(
            recovery=recovery,
            challenge_updates=challenge_updates,
            runs=tuple(runs),
            retention=retention,
            notifications=notifications,
        )

    def _load_settings(self) -> AutopilotSettings:
        loader = self.config_loader
        loaded = loader() if callable(loader) else loader.load()
        if isinstance(loaded, AutopilotSettings):
            return loaded
        if isinstance(loaded, Mapping):
            return parse_autopilot_settings(dict(loaded))
        settings = getattr(loaded, "settings", None)
        if isinstance(settings, AutopilotSettings):
            return settings
        raise TypeError("config loader returned an invalid snapshot")

    def _dispatchable(self, account: ScheduledAccount) -> bool:
        if not account.enabled or account.paused:
            return False
        if self.repository.pause_active(account.profile_id):
            return False
        if self.repository.kill_switch_active(account.profile_id):
            return False
        grant = self.repository.active_grant(account.profile_id)
        return bool(
            grant is not None
            and account.authorization_generation is not None
            and grant.generation == account.authorization_generation
        )

    def _deliver_notifications(
        self,
        settings: AutopilotSettings,
        instant: datetime,
    ) -> tuple[dict[str, Any], ...]:
        if not self.notification_sinks:
            return ()
        challenge_enabled = bool(settings.notifications["challenge"])
        run_failure_enabled = bool(settings.notifications["run_failure"])
        allowed_types = {
            event_type
            for event_type, enabled in (
                ("challenge", challenge_enabled),
                ("run_failure", run_failure_enabled),
            )
            if enabled
        }
        if not allowed_types:
            return ()
        sink_keys = tuple(self.notification_sinks)
        self.repository.enqueue_notification_candidates(
            sink_keys,
            challenge_enabled=challenge_enabled,
            run_failure_enabled=run_failure_enabled,
            now=instant,
        )
        deliveries = self.repository.due_notification_deliveries(
            sink_keys,
            now=instant,
        )
        results: list[dict[str, Any]] = []
        for delivery in deliveries:
            if delivery.event_type not in allowed_types:
                continue
            sink = self.notification_sinks[delivery.sink_key]
            try:
                sink.send(_notification_event(delivery))
            except Exception as exc:
                error = type(exc).__name__
                updated = self.repository.mark_notification_failed(
                    delivery.id,
                    error,
                    now=instant,
                )
                results.append(
                    {
                        "delivery_id": delivery.id,
                        "event_type": delivery.event_type,
                        "reference_id": delivery.reference_id,
                        "sink_key": delivery.sink_key,
                        "status": "retry_scheduled",
                        "attempt_count": updated.attempt_count,
                        "next_attempt_at": updated.next_attempt_at,
                        "error": error,
                    }
                )
                continue
            self.repository.mark_notification_sent(delivery.id, now=instant)
            results.append(
                {
                    "delivery_id": delivery.id,
                    "event_type": delivery.event_type,
                    "reference_id": delivery.reference_id,
                    "sink_key": delivery.sink_key,
                    "status": "sent",
                }
            )
        return tuple(results)


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError("scheduler time must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("scheduler time must be timezone-aware")
    return value.astimezone(timezone.utc)


def _notification_targets(
    configured: Mapping[str, Any] | Sequence[Any],
) -> dict[str, Any]:
    if isinstance(configured, Mapping):
        entries = list(configured.items())
    elif isinstance(configured, Sequence) and not isinstance(configured, (str, bytes)):
        entries = [
            (
                f"{sink.__class__.__module__}.{sink.__class__.__qualname__}:{index}",
                sink,
            )
            for index, sink in enumerate(configured)
        ]
    else:
        raise TypeError("notification_sinks must be a mapping or sequence")
    result: dict[str, Any] = {}
    for raw_key, sink in entries:
        if type(raw_key) is not str or not raw_key.strip():
            raise TypeError("notification sink keys must be non-empty strings")
        key = raw_key.strip()
        if len(key) > 200 or "\0" in key:
            raise ValueError("notification sink key is invalid")
        if key in result:
            raise ValueError(f"duplicate notification sink key: {key}")
        if not callable(getattr(sink, "send", None)):
            raise TypeError(f"notification sink {key!r} must expose send")
        result[key] = sink
    return result


def _notification_event(delivery: Any) -> NotificationEvent:
    payload = dict(delivery.payload)
    if delivery.event_type == "challenge":
        parts = [
            f"account: {payload.get('account_id', delivery.account_id)}",
            f"challenge_id: {payload.get('challenge_id', delivery.reference_id)}",
            f"type: {payload.get('challenge_type', '')}",
        ]
        if payload.get("item_id") is not None:
            parts.append(f"item_id: {payload['item_id']}")
        if payload.get("expires_at"):
            parts.append(f"expires_at: {payload['expires_at']}")
        if payload.get("url"):
            parts.append(f"url: {payload['url']}")
        return NotificationEvent(
            event_type="hh_autopilot_challenge",
            title="HH autopilot requires attention",
            body="\n".join(parts),
            payload=payload,
        )
    if delivery.event_type == "run_failure":
        parts = [
            f"account: {payload.get('account_id', delivery.account_id)}",
            f"run_id: {payload.get('run_id', delivery.reference_id)}",
            f"trigger: {payload.get('trigger', '')}",
        ]
        if payload.get("error"):
            parts.append(f"error: {payload['error']}")
        return NotificationEvent(
            event_type="hh_autopilot_run_failure",
            title="HH autopilot run failed",
            body="\n".join(parts),
            payload=payload,
        )
    raise ValueError(f"unsupported notification event type: {delivery.event_type}")


__all__ = [
    "HHAutopilotScheduler",
    "SchedulePolicy",
    "ScheduledAccount",
    "SchedulerReport",
]
