from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

import pytest

from work_hunter.hh_autopilot.config import (
    AutopilotSettings,
    default_autopilot_config,
    parse_autopilot_settings,
)
from work_hunter.hh_autopilot.repository import AutopilotRepository
from work_hunter.hh_autopilot.scheduler import (
    HHAutopilotScheduler,
    SchedulePolicy,
    ScheduledAccount,
)
from work_hunter.hh_autopilot.types import RunReport
from work_hunter.storage import Storage


UTC = timezone.utc


@pytest.fixture
def repo(tmp_path):
    storage = Storage(tmp_path / "work-hunter.db")
    yield AutopilotRepository(storage)
    storage.close()


def _settings(*accounts: tuple[str, bool]) -> AutopilotSettings:
    raw = default_autopilot_config()
    raw["timezone"] = "Europe/Moscow"
    raw["schedule"] = {
        "days": [1, 2, 3, 4, 5, 6, 7],
        "start": "08:00",
        "end": "18:00",
        "interval_minutes": 60,
    }
    template = raw["accounts"][0]
    raw["accounts"] = []
    for profile_id, enabled in accounts:
        account = copy.deepcopy(template)
        account.update(
            {
                "profile_id": profile_id,
                "candidate_profile_id": profile_id,
                "enabled": enabled,
                "authorization_generation": 1 if enabled else None,
            }
        )
        raw["accounts"].append(account)
    return parse_autopilot_settings({"sources": {"hh": {"autopilot": raw}}})


class _Recovery:
    def __init__(self, trace: list[str]) -> None:
        self.trace = trace
        self.calls = 0

    def run(self, *, now):
        self.calls += 1
        self.trace.append("recovery")
        return {"applied": 0}


class _Engine:
    def __init__(self, trace: list[str]) -> None:
        self.trace = trace
        self.accounts: list[str] = []

    def run(self, request):
        self.trace.append(f"engine:{request.account_id}")
        self.accounts.append(request.account_id)
        return RunReport(request.account_id, request.trigger, "completed")


class _Challenges:
    def __init__(self, trace: list[str]) -> None:
        self.trace = trace

    def expire_due(self, now):
        self.trace.append("challenges")
        return (now.isoformat(),)


def _scheduler(repo, settings, trace, *, challenges=None):
    engine = _Engine(trace)
    scheduler = HHAutopilotScheduler(
        repository=repo,
        config_loader=lambda: settings,
        engine=engine,
        recovery_sweep=_Recovery(trace),
        challenge_handler=challenges,
    )
    return scheduler, engine


def test_moscow_window_boundaries_and_interval_are_due(repo) -> None:
    settings = _settings(("default", True))
    account = ScheduledAccount.from_settings(settings.accounts[0], settings)
    policy = SchedulePolicy(repo)
    start = datetime(2026, 7, 15, 5, 0, tzinfo=UTC)  # 08:00 Moscow

    assert policy.is_due(account, start)
    assert policy.is_due(account, datetime(2026, 7, 15, 15, 0, tzinfo=UTC))
    assert not policy.is_due(account, datetime(2026, 7, 15, 15, 1, tzinfo=UTC))

    next_due = policy.next_due(account, start)
    assert repo.claim_schedule_slot(
        account.profile_id,
        start,
        next_due,
        interval_minutes=account.interval_minutes,
    )
    assert not policy.is_due(account, start + timedelta(minutes=59))
    assert policy.next_due(account, start + timedelta(minutes=30)) == start + timedelta(
        minutes=60
    )
    assert policy.is_due(account, start + timedelta(minutes=60))


def test_restart_claims_at_most_one_run_without_catch_up_burst(repo) -> None:
    settings = _settings(("default", True))
    repo.create_grants([("default", "policy", "test", "test")])
    old = datetime(2026, 7, 15, 5, 0, tzinfo=UTC)
    repo.claim_schedule_slot(
        "default",
        old,
        old + timedelta(hours=1),
        interval_minutes=60,
    )
    trace: list[str] = []
    scheduler, engine = _scheduler(repo, settings, trace)
    restart = datetime(2026, 7, 15, 12, 30, tzinfo=UTC)

    scheduler.tick(now=restart)
    scheduler.tick(now=restart)

    assert engine.accounts == ["default"]
    state = repo.get_account_state("default")
    assert state is not None
    assert state.last_scheduled_at == restart.isoformat()
    assert state.next_scheduled_at == (restart + timedelta(hours=1)).isoformat()


def test_due_retry_runs_between_search_intervals(repo) -> None:
    settings = _settings(("default", True))
    repo.create_grants([("default", "policy", "test", "test")])
    scheduled = datetime(2026, 7, 15, 5, 0, tzinfo=UTC)
    retry_due = scheduled + timedelta(minutes=30)
    repo.claim_schedule_slot(
        "default",
        scheduled,
        scheduled + timedelta(hours=1),
        interval_minutes=60,
    )
    run = repo.create_run("default", trigger="schedule", policy_hash="policy")
    item = repo.create_item(run.id, "default", "v-retry", "r-1", "preset")
    repo.conn.execute(
        """
        UPDATE hh_autopilot_items
        SET state = 'retry_wait', retry_stage = 'application',
            next_attempt_at = ?
        WHERE id = ?
        """,
        (retry_due.isoformat(), item.id),
    )
    repo.conn.commit()
    trace: list[str] = []
    scheduler, _engine = _scheduler(repo, settings, trace)

    report = scheduler.tick(now=retry_due)

    assert [run.trigger for run in report.runs] == ["retry"]
    assert repo.last_scheduled_at("default") == scheduled


def test_disabled_and_killed_account_still_runs_recovery_first(repo) -> None:
    settings = _settings(("default", False))
    repo.set_kill_switch("account", "default", actor="test")
    trace: list[str] = []
    challenges = _Challenges(trace)
    scheduler, engine = _scheduler(repo, settings, trace, challenges=challenges)

    report = scheduler.tick(now=datetime(2026, 7, 15, 8, 0, tzinfo=UTC))

    assert trace == ["recovery", "challenges"]
    assert report.recovery == {"applied": 0}
    assert report.challenge_updates
    assert engine.accounts == []


def test_multi_account_tick_skips_busy_and_cooldown_claims(repo) -> None:
    settings = _settings(("busy", True), ("cool", True), ("ready", True))
    repo.create_grants(
        [
            ("busy", "policy-busy", "test", "test"),
            ("cool", "policy-cool", "test", "test"),
            ("ready", "policy-ready", "test", "test"),
        ]
    )
    now = datetime(2026, 7, 15, 8, 0, tzinfo=UTC)
    repo.acquire_lease("busy", "other-worker", ttl_seconds=120, now=now)
    cool_lease = repo.acquire_lease("cool", "setup", ttl_seconds=120, now=now)
    assert cool_lease is not None
    repo.set_account_cooldown(
        "cool",
        now + timedelta(hours=1),
        "hh_limit",
        cool_lease.fencing_token,
        now=now,
    )
    repo.release_lease(cool_lease)
    trace: list[str] = []
    scheduler, engine = _scheduler(repo, settings, trace)

    scheduler.tick(now=now)

    assert engine.accounts == ["ready"]
    assert repo.get_account_state("busy") is None
    assert repo.get_account_state("cool").last_scheduled_at == ""
    assert repo.get_account_state("ready").last_scheduled_at == now.isoformat()
