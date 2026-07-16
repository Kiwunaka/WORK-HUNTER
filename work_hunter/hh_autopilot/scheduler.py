from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

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


class SchedulePolicy:
    def __init__(self, repository: Any) -> None:
        self.repository = repository

    def is_due(self, account: ScheduledAccount, now: datetime) -> bool:
        instant = _utc(now)
        local = instant.astimezone(ZoneInfo(account.timezone))
        local_time = local.timetz().replace(tzinfo=None)
        if local.isoweekday() not in account.days:
            return False
        if not account.start <= local_time <= account.end:
            return False
        last = self.repository.last_scheduled_at(account.profile_id)
        return last is None or instant >= last + timedelta(
            minutes=account.interval_minutes
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
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.repository = repository
        self.config_loader = config_loader
        self.engine = engine
        self.recovery_sweep = recovery_sweep
        self.challenge_handler = challenge_handler
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
        runs: list[RunReport] = []
        for raw_account in settings.accounts:
            account = ScheduledAccount.from_settings(raw_account, settings)
            if not self.policy.is_due(account, instant):
                continue
            if not self._dispatchable(account):
                continue
            next_due = self.policy.next_due(account, instant)
            if not self.repository.claim_schedule_slot(
                account.profile_id,
                instant,
                next_due,
                interval_minutes=account.interval_minutes,
            ):
                continue
            report = self.engine.run(
                RunRequest(account.profile_id, trigger="schedule")
            )
            if not isinstance(report, RunReport):
                raise TypeError("engine returned an invalid run report")
            runs.append(report)
        return SchedulerReport(
            recovery=recovery,
            challenge_updates=challenge_updates,
            runs=tuple(runs),
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


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError("scheduler time must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("scheduler time must be timezone-aware")
    return value.astimezone(timezone.utc)


__all__ = [
    "HHAutopilotScheduler",
    "SchedulePolicy",
    "ScheduledAccount",
    "SchedulerReport",
]
