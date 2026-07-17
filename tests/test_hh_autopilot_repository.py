from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from threading import Barrier, Event, Thread
from typing import Any

import pytest

import work_hunter.hh_autopilot.repository as repository_module
import work_hunter.hh_autopilot.types as autopilot_types
from work_hunter.hh_autopilot.repository import (
    AutopilotRepository,
    ChallengeRecord,
    ItemRecord,
    LeaseRecord,
    LostLease,
    RunRecord,
    StaleWrite,
)
from work_hunter.hh_autopilot.types import (
    AIDecision,
    AutopilotState,
    FilterDecision,
    RankScore,
    RankingDecision,
)
from work_hunter.storage import Storage


UTC = timezone.utc
LEASE_START = datetime(2099, 1, 1, 9, 0, tzinfo=UTC)


RUNTIME_TABLE_COLUMNS = {
    "hh_autopilot_runs": (
        "id",
        "account_profile_id",
        "trigger",
        "status",
        "grant_id",
        "policy_hash",
        "fencing_token",
        "counters_json",
        "error",
        "started_at",
        "finished_at",
        "created_at",
    ),
    "hh_autopilot_items": (
        "id",
        "origin_run_id",
        "last_run_id",
        "account_profile_id",
        "vacancy_id",
        "resume_id",
        "query_key",
        "state",
        "retry_stage",
        "version",
        "filter_json",
        "deterministic_score",
        "ai_json",
        "application_attempt_count",
        "reconciliation_count",
        "next_attempt_at",
        "last_outcome_code",
        "active_attempt_id",
        "challenge_id",
        "idempotency_key",
        "published_at",
        "created_at",
        "updated_at",
    ),
    "hh_autopilot_events": (
        "id",
        "run_id",
        "item_id",
        "previous_state",
        "next_state",
        "reason_code",
        "metadata_json",
        "created_at",
    ),
    "hh_autopilot_leases": (
        "account_profile_id",
        "owner_token",
        "fencing_token",
        "expires_at",
        "updated_at",
    ),
    "hh_autopilot_quota_reservations": (
        "id",
        "attempt_id",
        "run_id",
        "source",
        "remote_negotiation_id",
        "account_profile_id",
        "timezone",
        "local_date",
        "state",
        "fencing_token",
        "created_at",
        "resolved_at",
    ),
    "hh_autopilot_challenges": (
        "id",
        "scope",
        "challenge_type",
        "account_profile_id",
        "item_id",
        "reservation_id",
        "sanitized_url",
        "screenshot_path",
        "status",
        "expires_at",
        "resolution_at",
        "resolution_actor",
        "resolution_action",
        "metadata_json",
        "created_at",
    ),
    "hh_autopilot_search_cycles": (
        "id",
        "account_profile_id",
        "policy_hash",
        "origin_run_id",
        "owner_run_id",
        "claim_version",
        "fencing_token",
        "status",
        "created_at",
        "updated_at",
        "distinct_vacancy_cap",
        "mode",
    ),
    "hh_autopilot_search_checkpoints": (
        "id",
        "cycle_id",
        "resume_id",
        "query_key",
        "next_page",
        "reported_total",
        "unique_vacancy_count",
        "status",
        "updated_at",
    ),
    "hh_autopilot_search_results": (
        "id",
        "cycle_id",
        "checkpoint_id",
        "account_profile_id",
        "resume_id",
        "query_key",
        "vacancy_id",
        "page",
        "normalized_json",
        "discovered_at",
    ),
    "hh_autopilot_shadow_results": (
        "id",
        "run_id",
        "account_profile_id",
        "vacancy_id",
        "resume_id",
        "filter_json",
        "deterministic_score",
        "ai_json",
        "would_apply",
        "created_at",
    ),
    "hh_autopilot_account_state": (
        "account_profile_id",
        "blocked_until",
        "block_reason",
        "hh_reset_json",
        "last_scheduled_at",
        "next_scheduled_at",
        "version",
        "updated_at",
    ),
    "hh_application_account_guards": (
        "account_profile_id",
        "source",
        "source_id",
        "owner_attempt_id",
        "first_resume_id",
        "status",
        "application_id",
        "application_count",
        "created_at",
        "updated_at",
    ),
    "hh_autopilot_grants": (
        "id",
        "account_profile_id",
        "scope",
        "policy_hash",
        "generation",
        "active",
        "actor",
        "source",
        "created_at",
        "revoked_at",
    ),
    "hh_autopilot_one_shot_authorizations": (
        "reference_id",
        "authorization_type",
        "account_profile_id",
        "max_success",
        "consumed_success",
        "active",
        "created_at",
        "expires_at",
        "finished_at",
    ),
    "hh_autopilot_one_shot_targets": (
        "authorization_ref",
        "resume_id",
        "vacancy_id",
        "status",
        "active_attempt_id",
        "updated_at",
    ),
    "hh_autopilot_controls": (
        "scope_type",
        "scope_id",
        "paused",
        "kill_switch",
        "version",
        "updated_at",
    ),
}

RUNTIME_INDEXES = {
    "hh_autopilot_runs": {"idx_hh_autopilot_runs_account_status"},
    "hh_autopilot_items": {
        "idx_hh_autopilot_items_due",
        "idx_hh_autopilot_items_challenge",
    },
    "hh_autopilot_events": {"idx_hh_autopilot_events_item"},
    "hh_autopilot_leases": set(),
    "hh_autopilot_quota_reservations": {"idx_hh_autopilot_quota_day"},
    "hh_autopilot_challenges": set(),
    "hh_autopilot_search_cycles": {"idx_hh_autopilot_cycles_recovery"},
    "hh_autopilot_search_checkpoints": set(),
    "hh_autopilot_search_results": {"idx_hh_autopilot_search_results_vacancy"},
    "hh_autopilot_shadow_results": set(),
    "hh_autopilot_account_state": set(),
    "hh_application_account_guards": set(),
    "hh_autopilot_grants": {"idx_hh_autopilot_active_grant"},
    "hh_autopilot_one_shot_authorizations": set(),
    "hh_autopilot_one_shot_targets": set(),
    "hh_autopilot_controls": set(),
}

RUNTIME_FOREIGN_KEYS = {
    "hh_autopilot_runs": set(),
    "hh_autopilot_items": {
        ("origin_run_id", "hh_autopilot_runs", "id", "NO ACTION"),
        ("last_run_id", "hh_autopilot_runs", "id", "NO ACTION"),
        ("active_attempt_id", "hh_application_attempts", "id", "NO ACTION"),
    },
    "hh_autopilot_events": {
        ("run_id", "hh_autopilot_runs", "id", "NO ACTION"),
        ("item_id", "hh_autopilot_items", "id", "NO ACTION"),
    },
    "hh_autopilot_leases": set(),
    "hh_autopilot_quota_reservations": {
        ("attempt_id", "hh_application_attempts", "id", "NO ACTION"),
        ("run_id", "hh_autopilot_runs", "id", "NO ACTION"),
    },
    "hh_autopilot_challenges": {
        ("item_id", "hh_autopilot_items", "id", "NO ACTION"),
        ("reservation_id", "hh_autopilot_quota_reservations", "id", "NO ACTION"),
    },
    "hh_autopilot_search_cycles": {
        ("origin_run_id", "hh_autopilot_runs", "id", "NO ACTION"),
        ("owner_run_id", "hh_autopilot_runs", "id", "NO ACTION"),
    },
    "hh_autopilot_search_checkpoints": {
        ("cycle_id", "hh_autopilot_search_cycles", "id", "CASCADE"),
    },
    "hh_autopilot_search_results": {
        ("cycle_id", "hh_autopilot_search_cycles", "id", "CASCADE"),
        ("checkpoint_id", "hh_autopilot_search_checkpoints", "id", "CASCADE"),
    },
    "hh_autopilot_shadow_results": {
        ("run_id", "hh_autopilot_runs", "id", "NO ACTION"),
    },
    "hh_autopilot_account_state": set(),
    "hh_application_account_guards": {
        ("owner_attempt_id", "hh_application_attempts", "id", "NO ACTION"),
        ("application_id", "applications", "id", "NO ACTION"),
    },
    "hh_autopilot_grants": set(),
    "hh_autopilot_one_shot_authorizations": set(),
    "hh_autopilot_one_shot_targets": {
        (
            "authorization_ref",
            "hh_autopilot_one_shot_authorizations",
            "reference_id",
            "CASCADE",
        ),
        ("active_attempt_id", "hh_application_attempts", "id", "NO ACTION"),
    },
    "hh_autopilot_controls": set(),
}

RUNTIME_CHECKS = {
    "hh_autopilot_runs": (
        "trigger IN ('schedule','manual','shadow','retry','recovery','canary')",
        "status IN ('created','running','stop_requested','completed','failed','interrupted','cancelled')",
    ),
    "hh_autopilot_quota_reservations": (
        "source IN ('dispatch','external_sync')",
        "state IN ('reserved','held','consumed','released')",
    ),
    "hh_autopilot_challenges": (
        "scope IN ('item','account')",
        "status IN ('open','in_progress','resolved','dismissed','expired')",
    ),
    "hh_autopilot_search_cycles": (
        "status IN ('running','complete','failed','interrupted','superseded')",
        "typeof(mode) = 'text'",
        "mode IN ('live', 'shadow')",
    ),
    "hh_autopilot_search_checkpoints": (
        "status IN ('pending','running','complete','failed')",
    ),
    "hh_autopilot_shadow_results": ("would_apply IN (0,1)",),
    "hh_application_account_guards": (
        "status IN ('active','applied','external_applied')",
    ),
    "hh_autopilot_grants": ("scope = 'applications'", "active IN (0,1)"),
    "hh_autopilot_one_shot_authorizations": (
        "authorization_type IN ('manual','canary')",
        "max_success >= 1",
        "consumed_success >= 0 AND consumed_success <= max_success",
        "active IN (0,1)",
        "authorization_type != 'canary' OR max_success = 1",
    ),
    "hh_autopilot_one_shot_targets": (
        "status IN ('pending','active','succeeded','closed')",
    ),
    "hh_autopilot_controls": (
        "scope_type IN ('global','account')",
        "paused IN (0,1)",
        "kill_switch IN (0,1)",
    ),
}


@pytest.fixture
def repo(tmp_path):
    storage = Storage(tmp_path / "work-hunter.db")
    yield AutopilotRepository(storage)
    storage.close()


RANK_COMPONENTS = (
    "role",
    "skills",
    "experience",
    "salary",
    "work_format",
    "area",
    "industry",
)


def _filter_decision(*, passed: bool = True) -> FilterDecision:
    return FilterDecision(
        passed=passed,
        reason="hard_filters_passed" if passed else "hard_filter:area",
        evidence=(
            {
                "checks": (
                    "vacancy_open",
                    "history",
                    "blacklists",
                    "keywords_and_roles",
                    "area_and_relocation",
                    "work_format",
                    "experience",
                    "salary",
                    "candidate_constraints",
                    "application_capabilities",
                )
            }
            if passed
            else {"area_id": "1", "relocation_allowed": False}
        ),
    )


def _canonical_filter_json() -> str:
    return json.dumps(
        _filter_decision().to_dict(),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _ranking_decision(score: float = 80.0) -> RankingDecision:
    rank_score = RankScore(
        score=score,
        components={name: score for name in RANK_COMPONENTS},
        weights={name: 1 / len(RANK_COMPONENTS) for name in RANK_COMPONENTS},
    )
    ai = AIDecision(
        available=True,
        suitable=True,
        confidence=0.9,
        evidence=("python",),
        reasons=(),
        reason="available",
    )
    return RankingDecision(
        ready=True,
        retry=False,
        reason="ai_suitable",
        rank_score=rank_score,
        ai_decision=ai,
    )


def _nonqualifying_ranking_decision(score: float = 95.0) -> RankingDecision:
    rank_score = RankScore(
        score=score,
        components={name: score for name in RANK_COMPONENTS},
        weights={name: 1 / len(RANK_COMPONENTS) for name in RANK_COMPONENTS},
    )
    return RankingDecision(
        ready=False,
        retry=False,
        reason="ai_unsuitable",
        rank_score=rank_score,
        ai_decision=AIDecision(
            available=True,
            suitable=False,
            confidence=0.95,
            evidence=("python",),
            reasons=("role mismatch",),
            reason="available",
        ),
    )


def _retryable_ranking_decision(score: float = 99.0) -> RankingDecision:
    rank_score = RankScore(
        score=score,
        components={name: score for name in RANK_COMPONENTS},
        weights={name: 1 / len(RANK_COMPONENTS) for name in RANK_COMPONENTS},
    )
    return RankingDecision(
        ready=False,
        retry=True,
        reason="ai_unavailable",
        rank_score=rank_score,
        ai_decision=AIDecision(
            available=False,
            suitable=None,
            confidence=None,
            evidence=(),
            reasons=(),
            reason="ai_unavailable",
        ),
    )


def test_transition_updates_item_and_event_in_one_commit(repo) -> None:
    run = repo.create_run("default", trigger="manual", policy_hash="hash")
    item = repo.create_item(
        origin_run_id=run.id,
        account_id="default",
        vacancy_id="v-1",
        resume_id="r-1",
        query_key="preset:python",
    )

    eligible = repo.record_filter_decision(
        item.id,
        expected_version=item.version,
        decision=_filter_decision(),
        run_id=run.id,
    )
    changed = repo.transition_item(
        eligible.id,
        expected_version=eligible.version,
        target=AutopilotState.RETRY_WAIT,
        reason="internal_error",
        metadata={"authorization": "Bearer secret", "score": 82},
        run_id=run.id,
    )

    assert changed.state is AutopilotState.RETRY_WAIT
    assert changed.version == eligible.version + 1
    assert repo.list_events(item.id)[-1]["reason_code"] == "internal_error"
    assert "secret" not in repo.list_events(item.id)[-1]["metadata_json"]


def test_eligibility_retry_persists_backoff_and_exhaustion(repo) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    lease = repo.acquire_lease("default", "retry-owner", ttl_seconds=300, now=now)
    assert lease is not None
    run = repo.create_run(
        "default",
        trigger="retry",
        policy_hash="hash",
        fencing_token=lease.fencing_token,
    )
    item = repo.create_item(run.id, "default", "v-ai", "r-1", "preset")
    eligible = repo.record_filter_decision(
        item.id,
        expected_version=item.version,
        decision=_filter_decision(),
        run_id=run.id,
        fencing_token=lease.fencing_token,
    )

    waiting = repo.schedule_eligibility_retry(
        eligible.id,
        expected_version=eligible.version,
        decision=_retryable_ranking_decision(),
        attempt_number=1,
        max_attempts=2,
        next_attempt_at=now + timedelta(seconds=60),
        run_id=run.id,
        fencing_token=lease.fencing_token,
        now=now,
    )

    assert waiting.state is AutopilotState.RETRY_WAIT
    assert waiting.next_attempt_at == (now + timedelta(seconds=60)).isoformat()
    assert repo.eligibility_retry_count(waiting.id) == 1

    eligible_again = repo.activate_due_retry(
        waiting.id,
        waiting.version,
        run_id=run.id,
        fencing_token=lease.fencing_token,
        now=now + timedelta(seconds=60),
    )
    exhausted = repo.schedule_eligibility_retry(
        eligible_again.id,
        expected_version=eligible_again.version,
        decision=_retryable_ranking_decision(),
        attempt_number=2,
        max_attempts=2,
        next_attempt_at=now + timedelta(seconds=120),
        run_id=run.id,
        fencing_token=lease.fencing_token,
        now=now + timedelta(seconds=60),
    )

    assert exhausted.state is AutopilotState.DEAD
    assert exhausted.next_attempt_at == ""
    assert exhausted.last_outcome_code == "retry_exhausted"


def test_stale_version_cannot_write_or_append_an_event(repo) -> None:
    run = repo.create_run("default", trigger="manual", policy_hash="hash")
    item = repo.create_item(run.id, "default", "v-1", "r-1", "preset:python")
    eligible = repo.record_filter_decision(
        item.id,
        expected_version=item.version,
        decision=_filter_decision(),
        run_id=run.id,
    )
    repo.transition_item(
        eligible.id,
        eligible.version,
        AutopilotState.RETRY_WAIT,
        "internal_error",
        run_id=run.id,
    )

    with pytest.raises(StaleWrite):
        repo.transition_item(
            eligible.id,
            eligible.version,
            AutopilotState.RETRY_WAIT,
            "stale",
            run_id=run.id,
        )

    assert [event["reason_code"] for event in repo.list_events(item.id)] == [
        "discovered",
        "hard_filters_passed",
        "internal_error",
    ]


def test_runtime_migration_has_every_table_column_index_and_foreign_key(repo) -> None:
    connection = repo.conn
    migrated_tables = {
        row["name"]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = ?", ("table",)
        ).fetchall()
    }
    assert set(RUNTIME_TABLE_COLUMNS) <= migrated_tables

    for table, expected_columns in RUNTIME_TABLE_COLUMNS.items():
        columns = tuple(
            row["name"]
            for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        )
        assert columns == expected_columns, table

        explicit_indexes = {
            row["name"]
            for row in connection.execute(f"PRAGMA index_list({table})").fetchall()
            if not row["name"].startswith("sqlite_autoindex_")
        }
        assert explicit_indexes == RUNTIME_INDEXES[table], table

        foreign_keys = {
            (row["from"], row["table"], row["to"], row["on_delete"])
            for row in connection.execute(
                f"PRAGMA foreign_key_list({table})"
            ).fetchall()
        }
        assert foreign_keys == RUNTIME_FOREIGN_KEYS[table], table


def test_runtime_migration_has_every_declared_check_and_unique_index(repo) -> None:
    for table, checks in RUNTIME_CHECKS.items():
        row = repo.conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = ? AND name = ?",
            ("table", table),
        ).fetchone()
        compact_sql = "".join(str(row["sql"]).split())
        for check in checks:
            assert "".join(check.split()) in compact_sql, (table, check)

    active_grant = repo.conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = ? AND name = ?",
        ("index", "idx_hh_autopilot_active_grant"),
    ).fetchone()
    assert "UNIQUE" in active_grant["sql"].upper()
    assert "WHERE active = 1" in active_grant["sql"]
    challenge_columns = [
        row["name"]
        for row in repo.conn.execute(
            "PRAGMA index_info(idx_hh_autopilot_items_challenge)"
        ).fetchall()
    ]
    assert challenge_columns == ["challenge_id"]


def test_run_lifecycle_returns_records_and_fresh_redacted_json(repo) -> None:
    run = repo.create_run(
        " Default ",
        trigger="manual",
        policy_hash="hash",
        counters={"authorization": "Bearer secret", "seen": 2},
    )

    assert isinstance(run, RunRecord)
    assert run.account_id == "default"
    assert run.status == "running"
    assert run.counters == {"authorization": "***", "seen": 2}
    run.counters["seen"] = 99
    assert repo.get_run(run.id).counters["seen"] == 2

    finished = repo.finish_run(
        run.id,
        status="completed",
        counters={"refresh_token": "secret", "applied": 1},
    )

    assert finished.status == "completed"
    assert finished.finished_at
    assert finished.counters == {"refresh_token": "***", "applied": 1}
    assert repo.get_run(run.id) == finished


@pytest.mark.parametrize("trigger", ["invalid", 1, None])
def test_create_run_rejects_invalid_trigger(repo, trigger) -> None:
    with pytest.raises((TypeError, ValueError)):
        repo.create_run("default", trigger=trigger, policy_hash="hash")


def test_finish_run_rejects_nonterminal_status_without_mutating(repo) -> None:
    run = repo.create_run("default", trigger="manual", policy_hash="hash")

    with pytest.raises(ValueError):
        repo.finish_run(run.id, status="running")

    assert repo.get_run(run.id) == run


def test_create_item_uses_exact_hash_and_collision_has_one_initial_event(repo) -> None:
    run = repo.create_run("default", trigger="manual", policy_hash="hash")
    item = repo.create_item(run.id, "default", "v-1", " R-1 ", "preset:python")
    duplicate = repo.create_item(run.id, "default", "v-1", "r-1", "preset:other")

    expected_key = hashlib.sha256(b"default\0r-1\0v-1\0apply").hexdigest()
    row = repo.conn.execute(
        "SELECT idempotency_key FROM hh_autopilot_items WHERE id = ?", (item.id,)
    ).fetchone()
    assert isinstance(item, ItemRecord)
    assert item.resume_id == "r-1"
    assert row["idempotency_key"] == expected_key
    assert duplicate == item
    assert [event["reason_code"] for event in repo.list_events(item.id)] == [
        "discovered"
    ]
    assert repo.get_item(item.id) == item


def test_create_item_and_initial_event_roll_back_together(repo) -> None:
    run = repo.create_run("default", trigger="manual", policy_hash="hash")
    repo.conn.execute(
        """
        CREATE TRIGGER abort_initial_autopilot_event
        BEFORE INSERT ON hh_autopilot_events
        BEGIN
            SELECT RAISE(ABORT, 'event rejected');
        END
        """
    )

    with pytest.raises(sqlite3.IntegrityError, match="event rejected"):
        repo.create_item(run.id, "default", "v-1", "r-1", "preset:python")

    assert (
        repo.conn.execute("SELECT COUNT(*) FROM hh_autopilot_items").fetchone()[0] == 0
    )


def test_transition_rolls_back_when_event_insert_fails(repo) -> None:
    run = repo.create_run("default", trigger="manual", policy_hash="hash")
    item = repo.create_item(run.id, "default", "v-1", "r-1", "preset:python")
    eligible = repo.record_filter_decision(
        item.id,
        expected_version=item.version,
        decision=_filter_decision(),
        run_id=run.id,
    )
    repo.conn.execute(
        """
        CREATE TRIGGER abort_transition_autopilot_event
        BEFORE INSERT ON hh_autopilot_events
        WHEN NEW.reason_code = 'reject-me'
        BEGIN
            SELECT RAISE(ABORT, 'event rejected');
        END
        """
    )

    with pytest.raises(sqlite3.IntegrityError, match="event rejected"):
        repo.transition_item(
            eligible.id,
            eligible.version,
            AutopilotState.RETRY_WAIT,
            "reject-me",
            run_id=run.id,
        )

    assert repo.get_item(item.id) == eligible
    assert [event["reason_code"] for event in repo.list_events(item.id)] == [
        "discovered",
        "hard_filters_passed",
    ]


def test_append_event_redacts_json_and_returns_fresh_copies(repo) -> None:
    run = repo.create_run("default", trigger="manual", policy_hash="hash")
    item = repo.create_item(run.id, "default", "v-1", "r-1", "preset:python")

    event = repo.append_event(
        item.id,
        "observed",
        {"nested": {"access_token": "secret", "value": 3}},
        run_id=run.id,
    )

    assert event["previous_state"] == AutopilotState.DISCOVERED.value
    assert event["next_state"] == AutopilotState.DISCOVERED.value
    assert event["metadata_json"] == {"nested": {"access_token": "***", "value": 3}}
    event["metadata_json"]["nested"]["value"] = 99
    assert repo.list_events(item.id)[-1]["metadata_json"]["nested"]["value"] == 3


def test_fencing_token_must_match_live_account_lease(repo) -> None:
    run = repo.create_run(
        "default",
        trigger="manual",
        policy_hash="hash",
        fencing_token=7,
    )
    item = repo.create_item(run.id, "default", "v-1", "r-1", "preset:python")
    repo.conn.execute(
        """
        INSERT INTO hh_autopilot_leases (
            account_profile_id, owner_token, fencing_token, expires_at, updated_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            "default",
            "owner",
            7,
            "2099-01-01T00:00:00+00:00",
            "2026-01-01T00:00:00+00:00",
        ),
    )
    repo.conn.commit()

    lease = repo.get_lease("default")
    assert isinstance(lease, LeaseRecord)
    assert lease.fencing_token == 7
    with pytest.raises(LostLease):
        repo.transition_item(
            item.id,
            item.version,
            AutopilotState.SKIPPED,
            "internal_error",
            run_id=run.id,
            fencing_token=6,
        )

    assert repo.get_item(item.id) == item
    changed = repo.transition_item(
        item.id,
        item.version,
        AutopilotState.SKIPPED,
        "internal_error",
        run_id=run.id,
        fencing_token=7,
    )
    assert changed.state is AutopilotState.SKIPPED


def test_get_challenge_parses_fresh_redacted_metadata(repo) -> None:
    run = repo.create_run("default", trigger="manual", policy_hash="hash")
    item = repo.create_item(run.id, "default", "v-1", "r-1", "preset:python")
    cursor = repo.conn.execute(
        """
        INSERT INTO hh_autopilot_challenges (
            scope, challenge_type, account_profile_id, item_id, status,
            metadata_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "item",
            "captcha",
            "default",
            item.id,
            "open",
            '{"authorization":"***","step":1}',
            "2026-01-01T00:00:00+00:00",
        ),
    )
    repo.conn.commit()

    challenge = repo.get_challenge(cursor.lastrowid)

    assert isinstance(challenge, ChallengeRecord)
    assert challenge.metadata == {"authorization": "***", "step": 1}
    challenge.metadata["step"] = 2
    assert repo.get_challenge(challenge.id).metadata["step"] == 1


def test_list_due_items_filters_account_state_and_time_deterministically(repo) -> None:
    run = repo.create_run("default", trigger="manual", policy_hash="hash")
    other_run = repo.create_run("other", trigger="manual", policy_hash="hash")
    items = [
        repo.create_item(run.id, "default", f"v-{index}", "r-1", "preset")
        for index in range(1, 4)
    ]
    other = repo.create_item(other_run.id, "other", "v-4", "r-1", "preset")
    filter_json = _canonical_filter_json()
    updates = [
        (
            AutopilotState.RETRY_WAIT.value,
            "2026-01-02T00:00:00+00:00",
            filter_json,
            items[0].id,
        ),
        (AutopilotState.ELIGIBLE.value, "", filter_json, items[1].id),
        (
            AutopilotState.ELIGIBLE.value,
            "2026-01-03T00:00:00+00:00",
            filter_json,
            items[2].id,
        ),
        (AutopilotState.ELIGIBLE.value, "", filter_json, other.id),
    ]
    repo.conn.executemany(
        "UPDATE hh_autopilot_items "
        "SET state = ?, next_attempt_at = ?, filter_json = ? WHERE id = ?",
        updates,
    )
    repo.conn.commit()

    due = repo.list_due_items(
        "default",
        states=[AutopilotState.ELIGIBLE, AutopilotState.RETRY_WAIT],
        now=datetime(2026, 1, 2, tzinfo=timezone.utc),
        limit=10,
    )

    assert [item.id for item in due] == [items[1].id, items[0].id]
    assert repo.list_due_items(
        "default",
        states=[AutopilotState.RETRY_WAIT, AutopilotState.ELIGIBLE],
        now=datetime(2026, 1, 2, tzinfo=timezone.utc),
        limit=1,
    ) == [due[0]]


def test_repository_rejects_raw_state_strings_and_bad_identifier_types(repo) -> None:
    run = repo.create_run("default", trigger="manual", policy_hash="hash")
    item = repo.create_item(run.id, "default", "v-1", "r-1", "preset")

    with pytest.raises(TypeError):
        repo.transition_item(item.id, item.version, "eligible", "passed")
    with pytest.raises(TypeError):
        repo.list_due_items(
            "default",
            states=["eligible"],
            now=datetime.now(timezone.utc),
            limit=10,
        )
    with pytest.raises(TypeError):
        repo.create_item(run.id, "default", 1, "r-1", "preset")


def test_immediate_rolls_back_when_deferred_foreign_key_fails_at_commit(repo) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        with repo.immediate():
            repo.conn.execute("PRAGMA defer_foreign_keys = ON")
            repo.conn.execute(
                """
                INSERT INTO hh_autopilot_events (
                    run_id, item_id, previous_state, next_state, reason_code,
                    metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    None,
                    999_999,
                    "discovered",
                    "discovered",
                    "deferred_fk_probe",
                    "{}",
                    "2026-01-01T00:00:00+00:00",
                ),
            )

    assert repo.conn.in_transaction is False
    assert (
        repo.conn.execute(
            "SELECT COUNT(*) FROM hh_autopilot_events WHERE reason_code = ?",
            ("deferred_fk_probe",),
        ).fetchone()[0]
        == 0
    )


def test_repository_rejects_nul_in_every_idempotency_component(repo) -> None:
    with pytest.raises(ValueError, match="NUL"):
        repo.create_run("default\0other", trigger="manual", policy_hash="hash")

    run = repo.create_run("default", trigger="manual", policy_hash="hash")
    injected_pairs = [
        ("vacancy", "v-1\0tail", "r-1"),
        ("resume", "v-1", "r-1\0tail"),
        ("resume-boundary", "x", "r\0v"),
        ("vacancy-boundary", "v\0x", "r"),
    ]
    for _label, vacancy_id, resume_id in injected_pairs:
        with pytest.raises(ValueError, match="NUL"):
            repo.create_item(
                run.id,
                "default",
                vacancy_id,
                resume_id,
                "preset",
            )

    with pytest.raises(ValueError, match="NUL"):
        repo.create_item(run.id, "default", "v-1", "r-1", "preset\0other")
    assert (
        repo.conn.execute("SELECT COUNT(*) FROM hh_autopilot_items").fetchone()[0] == 0
    )


def test_unambiguous_valid_idempotency_pairs_remain_distinct(repo) -> None:
    run = repo.create_run("default", trigger="manual", policy_hash="hash")

    first = repo.create_item(run.id, "default", "c", "ab", "preset")
    second = repo.create_item(run.id, "default", "bc", "a", "preset")

    assert first.id != second.id


def test_tuple_nested_secrets_are_redacted_from_run_counters(repo) -> None:
    run = repo.create_run(
        "default",
        trigger="manual",
        policy_hash="hash",
        counters={"steps": ({"access_token": "create-secret", "count": 1},)},
    )
    created_raw = repo.conn.execute(
        "SELECT counters_json FROM hh_autopilot_runs WHERE id = ?", (run.id,)
    ).fetchone()[0]

    assert "create-secret" not in created_raw
    assert run.counters == {
        "steps": [{"access_token": "***", "count": 1}],
    }

    finished = repo.finish_run(
        run.id,
        counters={"steps": (({"refresh_token": "finish-secret"},),)},
    )
    finished_raw = repo.conn.execute(
        "SELECT counters_json FROM hh_autopilot_runs WHERE id = ?", (run.id,)
    ).fetchone()[0]

    assert "finish-secret" not in finished_raw
    assert finished.counters == {
        "steps": [[{"refresh_token": "***"}]],
    }


def test_due_timestamp_offsets_are_normalized_before_boundary_comparison(repo) -> None:
    run = repo.create_run("default", trigger="manual", policy_hash="hash")
    item = repo.create_item(run.id, "default", "v-1", "r-1", "preset")
    repo.conn.execute(
        """
        UPDATE hh_autopilot_items
        SET state = ?, next_attempt_at = ?, filter_json = ?
        WHERE id = ?
        """,
        (
            AutopilotState.ELIGIBLE.value,
            "2026-01-01T22:30:00+00:00",
            _canonical_filter_json(),
            item.id,
        ),
    )
    repo.conn.commit()

    assert (
        repo.list_due_items(
            "default",
            states=[AutopilotState.ELIGIBLE],
            now="2026-01-02T00:00:00+02:00",
            limit=10,
        )
        == []
    )
    assert repo.list_due_items(
        "default",
        states=[AutopilotState.ELIGIBLE],
        now="2026-01-02T01:00:00+02:00",
        limit=10,
    ) == [repo.get_item(item.id)]
    assert repo.list_due_items(
        "default",
        states=[AutopilotState.ELIGIBLE],
        now="2026-01-01T23:00:00Z",
        limit=10,
    ) == [repo.get_item(item.id)]


@pytest.mark.parametrize(
    "now",
    ["not-a-timestamp", "2026-01-01T23:00:00"],
)
def test_due_timestamp_rejects_invalid_or_naive_strings(repo, now) -> None:
    run = repo.create_run("default", trigger="manual", policy_hash="hash")
    repo.create_item(run.id, "default", "v-1", "r-1", "preset")

    with pytest.raises(ValueError):
        repo.list_due_items(
            "default",
            states=[AutopilotState.DISCOVERED],
            now=now,
            limit=10,
        )


def test_append_event_rejects_forged_state_arguments(repo) -> None:
    run = repo.create_run("default", trigger="manual", policy_hash="hash")
    item = repo.create_item(run.id, "default", "v-1", "r-1", "preset")

    with pytest.raises((TypeError, ValueError)):
        repo.append_event(
            item.id,
            "forged",
            previous_state=AutopilotState.APPLYING,
            next_state=AutopilotState.APPLIED,
        )

    assert [event["reason_code"] for event in repo.list_events(item.id)] == [
        "discovered"
    ]


def _insert_autopilot_attempt(
    repo: AutopilotRepository,
    *,
    account_id: str,
    run_id: int,
    item_id: int | None = None,
    vacancy_id: str = "v-1",
) -> int:
    cursor = repo.conn.execute(
        """
        INSERT INTO hh_application_attempts (
            vacancy_id, resume_id, status, created_at,
            account_profile_id, autopilot_run_id, autopilot_item_id
        ) VALUES (?, ?, 'prepared', ?, ?, ?, ?)
        """,
        (
            vacancy_id,
            "r-1",
            LEASE_START.isoformat(),
            account_id,
            run_id,
            item_id,
        ),
    )
    repo.conn.commit()
    assert cursor.lastrowid is not None
    return int(cursor.lastrowid)


def _dispatch_context(
    repo: AutopilotRepository,
    *,
    account_id: str = "default",
    owner_token: str = "owner-a",
    now: datetime = LEASE_START,
    vacancy_id: str = "v-1",
) -> tuple[Any, RunRecord, ItemRecord, int]:
    lease = repo.acquire_lease(
        account_id,
        owner_token,
        ttl_seconds=120,
        now=now,
    )
    assert lease is not None
    run = repo.create_run(
        account_id,
        trigger="manual",
        policy_hash="policy-hash",
        fencing_token=lease.fencing_token,
    )
    item = repo.create_item(
        run.id,
        account_id,
        vacancy_id,
        "r-1",
        "preset:python",
    )
    attempt_id = _insert_autopilot_attempt(
        repo,
        account_id=account_id,
        run_id=run.id,
        item_id=item.id,
        vacancy_id=vacancy_id,
    )
    return lease, run, item, attempt_id


class _FakeClock:
    def __init__(self, current: datetime):
        self.current = current

    def __call__(self) -> datetime:
        return self.current

    def advance(self, seconds: int) -> None:
        self.current += timedelta(seconds=seconds)


def test_lease_acquisition_excludes_active_owner_and_never_has_aba(repo) -> None:
    first = repo.acquire_lease(
        " Default ",
        "owner-a",
        ttl_seconds=120,
        now=LEASE_START,
    )

    assert isinstance(first, LeaseRecord)
    assert first.account_id == "default"
    assert first.fencing_token == 1
    assert first.expires_at == (LEASE_START + timedelta(seconds=120)).isoformat()

    same_owner = repo.acquire_lease(
        "default",
        "owner-a",
        ttl_seconds=120,
        now=LEASE_START + timedelta(seconds=30),
    )
    assert same_owner.fencing_token == first.fencing_token
    before_rejected_owner = repo.get_lease("default")
    assert (
        repo.acquire_lease(
            "default",
            "owner-b",
            ttl_seconds=120,
            now=LEASE_START + timedelta(seconds=31),
        )
        is None
    )
    assert repo.get_lease("default") == before_rejected_owner

    takeover = repo.acquire_lease(
        "default",
        "owner-b",
        ttl_seconds=120,
        now=LEASE_START + timedelta(seconds=150),
    )
    assert takeover.fencing_token == first.fencing_token + 1

    same_text_after_expiry = repo.acquire_lease(
        "default",
        "owner-b",
        ttl_seconds=120,
        now=LEASE_START + timedelta(seconds=270),
    )
    assert same_text_after_expiry.fencing_token == takeover.fencing_token + 1


def test_lease_renew_and_release_require_exact_live_owner_and_token(repo) -> None:
    lease = repo.acquire_lease("default", "owner", ttl_seconds=60, now=LEASE_START)
    assert lease is not None
    wrong_token = replace(lease, fencing_token=lease.fencing_token + 1)
    wrong_owner = replace(lease, owner_token="someone-else")

    with pytest.raises(LostLease):
        repo.renew_lease(
            wrong_token,
            ttl_seconds=60,
            now=LEASE_START + timedelta(seconds=10),
        )
    assert repo.release_lease(wrong_owner) is False
    assert repo.get_lease("default") == lease

    renewed = repo.renew_lease(
        lease,
        ttl_seconds=60,
        now=LEASE_START + timedelta(seconds=10),
    )
    assert renewed.fencing_token == lease.fencing_token
    assert renewed.expires_at == (LEASE_START + timedelta(seconds=70)).isoformat()
    with pytest.raises(LostLease):
        repo.renew_lease(
            renewed,
            ttl_seconds=60,
            now=LEASE_START + timedelta(seconds=70),
        )

    assert repo.release_lease(lease) is True
    assert repo.get_lease("default") is None


def test_lease_validation_finishes_before_begin_immediate(repo) -> None:
    invalid_calls = [
        lambda: repo.acquire_lease(
            "default\0other", "owner", ttl_seconds=60, now=LEASE_START
        ),
        lambda: repo.acquire_lease(
            "default", "owner\0other", ttl_seconds=60, now=LEASE_START
        ),
        lambda: repo.acquire_lease(
            "default", "owner", ttl_seconds=True, now=LEASE_START
        ),
        lambda: repo.acquire_lease("default", "owner", ttl_seconds=0, now=LEASE_START),
        lambda: repo.acquire_lease(
            "default", "owner", ttl_seconds=60, now=LEASE_START.replace(tzinfo=None)
        ),
    ]
    for invalid_call in invalid_calls:
        statements: list[str] = []
        repo.conn.set_trace_callback(statements.append)
        try:
            with pytest.raises((TypeError, ValueError)):
                invalid_call()
        finally:
            repo.conn.set_trace_callback(None)
        assert not any(statement.startswith("BEGIN") for statement in statements)


def test_renew_and_release_validate_record_timestamps_before_begin(repo) -> None:
    lease = repo.acquire_lease("default", "owner", ttl_seconds=60, now=LEASE_START)
    assert lease is not None
    invalid_records = [
        replace(lease, expires_at="2099-01-01T09:01:00"),
        replace(lease, updated_at="not-a-time"),
    ]
    for invalid_record in invalid_records:
        for operation in (
            lambda record=invalid_record: repo.renew_lease(
                record, ttl_seconds=60, now=LEASE_START
            ),
            lambda record=invalid_record: repo.release_lease(record),
        ):
            statements: list[str] = []
            repo.conn.set_trace_callback(statements.append)
            try:
                with pytest.raises(ValueError):
                    operation()
            finally:
                repo.conn.set_trace_callback(None)
            assert not any(statement.startswith("BEGIN") for statement in statements)
    assert repo.get_lease("default") == lease


def test_malformed_persisted_lease_expiry_fails_closed(repo) -> None:
    repo.conn.execute(
        """
        INSERT INTO hh_autopilot_leases (
            account_profile_id, owner_token, fencing_token, expires_at, updated_at
        ) VALUES ('default', 'owner-a', 7, 'not-a-time', ?)
        """,
        (LEASE_START.isoformat(),),
    )
    repo.conn.commit()

    with pytest.raises(LostLease, match="expiry"):
        repo.acquire_lease(
            "default",
            "owner-b",
            ttl_seconds=60,
            now=LEASE_START,
        )

    stored = repo.get_lease("default")
    assert stored.owner_token == "owner-a"
    assert stored.fencing_token == 7
    assert stored.expires_at == "not-a-time"


def test_lease_keeper_renews_with_fake_clock_beyond_original_ttl(repo) -> None:
    lease = repo.acquire_lease("default", "owner", ttl_seconds=10, now=LEASE_START)
    assert lease is not None
    clock = _FakeClock(LEASE_START)
    keeper = repository_module.LeaseKeeper(
        repo,
        lease,
        ttl_seconds=10,
        renewal_margin_seconds=3,
        clock=clock,
    )

    clock.advance(6)
    assert (
        keeper.ensure_current().expires_at
        == (LEASE_START + timedelta(seconds=10)).isoformat()
    )
    clock.advance(1)
    assert (
        keeper.ensure_current().expires_at
        == (LEASE_START + timedelta(seconds=17)).isoformat()
    )
    clock.advance(6)
    assert keeper.ensure_current().fencing_token == lease.fencing_token
    clock.advance(1)
    current = keeper.ensure_current()

    assert clock.current > LEASE_START + timedelta(seconds=10)
    assert current.fencing_token == lease.fencing_token
    assert current.expires_at == (LEASE_START + timedelta(seconds=24)).isoformat()


def test_lease_keeper_rejects_strict_and_naive_inputs(repo) -> None:
    lease = repo.acquire_lease("default", "owner", ttl_seconds=10, now=LEASE_START)
    assert lease is not None
    with pytest.raises(TypeError):
        repository_module.LeaseKeeper(
            repo,
            lease,
            ttl_seconds=True,
            renewal_margin_seconds=3,
        )
    with pytest.raises(TypeError):
        repository_module.LeaseKeeper(
            repo,
            lease,
            ttl_seconds=10,
            renewal_margin_seconds=False,
        )
    forged = replace(lease, owner_token="owner\0tail")
    with pytest.raises(ValueError, match="NUL"):
        repository_module.LeaseKeeper(
            repo,
            forged,
            ttl_seconds=10,
            renewal_margin_seconds=3,
        )
    keeper = repository_module.LeaseKeeper(
        repo,
        lease,
        ttl_seconds=10,
        renewal_margin_seconds=3,
        clock=lambda: LEASE_START.replace(tzinfo=None),
    )
    with pytest.raises(ValueError, match="timezone-aware"):
        keeper.ensure_current()


def test_expired_owner_is_fenced_after_takeover_on_protected_write(repo) -> None:
    first, run, item, attempt_id = _dispatch_context(repo)
    takeover_at = LEASE_START + timedelta(seconds=120)
    second = repo.acquire_lease("default", "owner-b", ttl_seconds=120, now=takeover_at)
    assert second is not None

    with pytest.raises(LostLease):
        repo.reserve_quota(
            "default",
            run.id,
            attempt_id,
            "Europe/Moscow",
            10,
            10,
            first.fencing_token,
            now=takeover_at,
        )

    assert repo.get_item(item.id) == item
    assert (
        repo.conn.execute(
            "SELECT COUNT(*) FROM hh_autopilot_quota_reservations"
        ).fetchone()[0]
        == 0
    )


def test_recover_stale_applying_only_moves_items_to_reconciling(repo) -> None:
    lease, run, first_item, first_attempt_id = _dispatch_context(repo)
    second_item = repo.create_item(run.id, "default", "v-2", "r-1", "preset:python")
    second_attempt_id = _insert_autopilot_attempt(
        repo,
        account_id="default",
        run_id=run.id,
        item_id=second_item.id,
        vacancy_id="v-2",
    )
    repo.conn.executemany(
        """
        UPDATE hh_autopilot_items
        SET state = 'applying', version = 4, active_attempt_id = ?
        WHERE id = ?
        """,
        [
            (first_attempt_id, first_item.id),
            (second_attempt_id, second_item.id),
        ],
    )
    reservation_cursor = repo.conn.execute(
        """
        INSERT INTO hh_autopilot_quota_reservations (
            attempt_id, run_id, source, account_profile_id, timezone,
            local_date, state, fencing_token, created_at
        ) VALUES (?, ?, 'dispatch', 'default', 'Europe/Moscow',
                  '2099-01-01', 'held', ?, ?)
        """,
        (first_attempt_id, run.id, lease.fencing_token, LEASE_START.isoformat()),
    )
    repo.conn.commit()

    recovered = repo.recover_stale_applying(
        "default",
        lease.fencing_token,
        run_id=run.id,
        now=LEASE_START + timedelta(seconds=10),
    )

    assert [item.id for item in recovered] == [first_item.id, second_item.id]
    assert all(item.state is AutopilotState.RECONCILING for item in recovered)
    assert all(item.version == 5 for item in recovered)
    assert all(item.last_run_id == run.id for item in recovered)
    for item in recovered:
        event = repo.list_events(item.id)[-1]
        assert event["previous_state"] == AutopilotState.APPLYING.value
        assert event["next_state"] == AutopilotState.RECONCILING.value
        assert event["reason_code"] == "stale_applying_recovered"
        assert event["run_id"] == run.id
        assert event["metadata_json"] == {"fence": lease.fencing_token}
    assert (
        repo.conn.execute("SELECT COUNT(*) FROM hh_application_attempts").fetchone()[0]
        == 2
    )
    reservation = repo.conn.execute(
        "SELECT state FROM hh_autopilot_quota_reservations WHERE id = ?",
        (reservation_cursor.lastrowid,),
    ).fetchone()
    assert reservation["state"] == "held"


def test_recovery_validates_run_account_and_fence_without_mutation(repo) -> None:
    lease, run, item, attempt_id = _dispatch_context(repo)
    repo.conn.execute(
        """
        UPDATE hh_autopilot_items
        SET state = 'applying', active_attempt_id = ?
        WHERE id = ?
        """,
        (attempt_id, item.id),
    )
    repo.conn.commit()
    other_run = repo.create_run(
        "other",
        trigger="recovery",
        policy_hash="policy-hash",
        fencing_token=lease.fencing_token,
    )

    with pytest.raises(ValueError, match="account"):
        repo.recover_stale_applying(
            "default",
            lease.fencing_token,
            run_id=other_run.id,
            now=LEASE_START + timedelta(seconds=10),
        )
    mismatched_fence_run = repo.create_run(
        "default",
        trigger="recovery",
        policy_hash="policy-hash",
        fencing_token=lease.fencing_token + 1,
    )
    with pytest.raises(LostLease, match="run"):
        repo.recover_stale_applying(
            "default",
            lease.fencing_token,
            run_id=mismatched_fence_run.id,
            now=LEASE_START + timedelta(seconds=10),
        )

    assert repo.get_item(item.id).state is AutopilotState.APPLYING


def test_recovery_rolls_back_item_updates_when_event_insert_fails(repo) -> None:
    lease, run, item, attempt_id = _dispatch_context(repo)
    repo.conn.execute(
        """
        UPDATE hh_autopilot_items
        SET state = 'applying', version = 3, active_attempt_id = ?
        WHERE id = ?
        """,
        (attempt_id, item.id),
    )
    repo.conn.execute(
        """
        CREATE TRIGGER abort_recovery_event
        BEFORE INSERT ON hh_autopilot_events
        WHEN NEW.reason_code = 'stale_applying_recovered'
        BEGIN
            SELECT RAISE(ABORT, 'recovery event rejected');
        END
        """
    )
    repo.conn.commit()

    with pytest.raises(sqlite3.IntegrityError, match="recovery event rejected"):
        repo.recover_stale_applying(
            "default",
            lease.fencing_token,
            run_id=run.id,
            now=LEASE_START + timedelta(seconds=10),
        )

    unchanged = repo.get_item(item.id)
    assert unchanged.state is AutopilotState.APPLYING
    assert unchanged.version == 3
    assert [event["reason_code"] for event in repo.list_events(item.id)] == [
        "discovered"
    ]


def _quota_worker(
    database_path: Any,
    barrier: Barrier,
    *,
    run_id: int,
    attempt_id: int,
    fencing_token: int,
) -> tuple[str, Any]:
    storage = Storage(database_path)
    worker_repo = AutopilotRepository(storage)
    try:
        barrier.wait()
        try:
            reservation = worker_repo.reserve_quota(
                "default",
                run_id,
                attempt_id,
                "Europe/Moscow",
                1,
                1,
                fencing_token,
                now=LEASE_START + timedelta(seconds=1),
            )
            return "reserved", reservation.id
        except Exception as exc:  # Result is asserted by exact exception type name.
            return type(exc).__name__, str(exc)
    finally:
        storage.close()


def test_two_connections_cannot_both_reserve_limit_one(repo) -> None:
    lease, run, item, first_attempt_id = _dispatch_context(repo)
    second_item = repo.create_item(run.id, "default", "v-2", "r-1", "preset:python")
    second_attempt_id = _insert_autopilot_attempt(
        repo,
        account_id="default",
        run_id=run.id,
        item_id=second_item.id,
        vacancy_id="v-2",
    )
    barrier = Barrier(2)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [
            future.result()
            for future in [
                pool.submit(
                    _quota_worker,
                    repo.storage.path,
                    barrier,
                    run_id=run.id,
                    attempt_id=attempt_id,
                    fencing_token=lease.fencing_token,
                )
                for attempt_id in (first_attempt_id, second_attempt_id)
            ]
        ]

    assert sorted(result[0] for result in results) == ["QuotaExceeded", "reserved"]
    rows = repo.conn.execute("SELECT * FROM hh_autopilot_quota_reservations").fetchall()
    assert len(rows) == 1
    assert rows[0]["state"] == "reserved"


def test_quota_uses_moscow_local_day_and_counts_unreleased_states(repo) -> None:
    instant = datetime(2099, 1, 1, 20, 59, tzinfo=UTC)
    lease = repo.acquire_lease("default", "owner", ttl_seconds=200_000, now=instant)
    assert lease is not None
    run = repo.create_run(
        "default",
        trigger="manual",
        policy_hash="hash",
        fencing_token=lease.fencing_token,
    )
    attempts: list[int] = []
    for index in range(4):
        item = repo.create_item(
            run.id,
            "default",
            f"rollover-v-{index}",
            "r-1",
            "preset",
        )
        attempts.append(
            _insert_autopilot_attempt(
                repo,
                account_id="default",
                run_id=run.id,
                item_id=item.id,
                vacancy_id=f"rollover-v-{index}",
            )
        )

    previous_day = repo.reserve_quota(
        "default",
        run.id,
        attempts[0],
        "Europe/Moscow",
        1,
        10,
        lease.fencing_token,
        now=instant,
    )
    next_day_instant = instant + timedelta(minutes=1)
    next_day = repo.reserve_quota(
        "default",
        run.id,
        attempts[1],
        "Europe/Moscow",
        1,
        10,
        lease.fencing_token,
        now=next_day_instant,
    )
    assert previous_day.local_date == "2099-01-01"
    assert next_day.local_date == "2099-01-02"

    held = repo.hold_reservation(next_day.id, lease.fencing_token, now=next_day_instant)
    assert held.state is autopilot_types.QuotaReservationState.HELD
    with pytest.raises(repository_module.QuotaExceeded) as held_error:
        repo.reserve_quota(
            "default",
            run.id,
            attempts[2],
            "Europe/Moscow",
            1,
            10,
            lease.fencing_token,
            now=next_day_instant,
        )
    assert held_error.value.dimension == "daily"

    repo.release_reservation(held.id, lease.fencing_token, now=next_day_instant)
    consumed = repo.consume_reservation(
        repo.reserve_quota(
            "default",
            run.id,
            attempts[2],
            "Europe/Moscow",
            1,
            10,
            lease.fencing_token,
            now=next_day_instant,
        ).id,
        lease.fencing_token,
        remote_negotiation_id="neg-rollover",
        now=next_day_instant,
    )
    assert consumed.state is autopilot_types.QuotaReservationState.CONSUMED
    with pytest.raises(repository_module.QuotaExceeded):
        repo.reserve_quota(
            "default",
            run.id,
            attempts[3],
            "Europe/Moscow",
            1,
            10,
            lease.fencing_token,
            now=next_day_instant,
        )


def test_reservation_cas_is_idempotent_and_terminal_states_are_strict(repo) -> None:
    lease, run, item, first_attempt_id = _dispatch_context(repo)
    second_item = repo.create_item(run.id, "default", "v-2", "r-1", "preset")
    second_attempt_id = _insert_autopilot_attempt(
        repo,
        account_id="default",
        run_id=run.id,
        item_id=second_item.id,
        vacancy_id="v-2",
    )
    now = LEASE_START + timedelta(seconds=1)

    first = repo.reserve_quota(
        "default",
        run.id,
        first_attempt_id,
        "UTC",
        10,
        10,
        lease.fencing_token,
        now=now,
    )
    assert (
        repo.reserve_quota(
            "default",
            run.id,
            first_attempt_id,
            "UTC",
            10,
            10,
            lease.fencing_token,
            now=now,
        )
        == first
    )
    held = repo.hold_reservation(first.id, lease.fencing_token, now=now)
    assert repo.hold_reservation(first.id, lease.fencing_token, now=now) == held
    consumed = repo.consume_reservation(
        first.id,
        lease.fencing_token,
        remote_negotiation_id="neg-1",
        now=now,
    )
    assert (
        repo.consume_reservation(
            first.id,
            lease.fencing_token,
            remote_negotiation_id="neg-1",
            now=now,
        )
        == consumed
    )
    with pytest.raises(StaleWrite, match="remote negotiation"):
        repo.consume_reservation(
            first.id,
            lease.fencing_token,
            remote_negotiation_id="neg-2",
            now=now,
        )
    with pytest.raises(StaleWrite):
        repo.release_reservation(first.id, lease.fencing_token, now=now)

    second = repo.reserve_quota(
        "default",
        run.id,
        second_attempt_id,
        "UTC",
        10,
        10,
        lease.fencing_token,
        now=now,
    )
    released = repo.release_reservation(second.id, lease.fencing_token, now=now)
    assert repo.release_reservation(second.id, lease.fencing_token, now=now) == released
    with pytest.raises(StaleWrite):
        repo.hold_reservation(second.id, lease.fencing_token, now=now)
    with pytest.raises(StaleWrite):
        repo.consume_reservation(second.id, lease.fencing_token, now=now)
    with pytest.raises(StaleWrite):
        repo.reserve_quota(
            "default",
            run.id,
            second_attempt_id,
            "UTC",
            10,
            10,
            lease.fencing_token,
            now=now,
        )

    assert repo.get_reservation(first.id) == consumed
    assert repo.active_reservation_for_attempt(first_attempt_id) == consumed
    assert [row.id for row in repo.list_active_reservations("default")] == [first.id]
    assert repo.count_active_reservations("default") == 1
    assert item.account_id == consumed.account_id


def test_reservation_row_fence_cannot_be_reused_after_takeover(repo) -> None:
    lease, run, _item, attempt_id = _dispatch_context(repo)
    reservation = repo.reserve_quota(
        "default",
        run.id,
        attempt_id,
        "UTC",
        10,
        10,
        lease.fencing_token,
        now=LEASE_START,
    )
    takeover = repo.acquire_lease(
        "default",
        "owner-b",
        ttl_seconds=120,
        now=LEASE_START + timedelta(seconds=120),
    )
    assert takeover is not None

    with pytest.raises(StaleWrite, match="fencing"):
        repo.hold_reservation(
            reservation.id,
            takeover.fencing_token,
            now=LEASE_START + timedelta(seconds=120),
        )
    assert repo.get_reservation(reservation.id).state is (
        autopilot_types.QuotaReservationState.RESERVED
    )


def test_held_reservation_has_no_time_expiry_or_cleanup(repo) -> None:
    lease, run, _item, attempt_id = _dispatch_context(repo)
    reservation = repo.reserve_quota(
        "default",
        run.id,
        attempt_id,
        "UTC",
        10,
        10,
        lease.fencing_token,
        now=LEASE_START,
    )
    held = repo.hold_reservation(reservation.id, lease.fencing_token, now=LEASE_START)

    assert repo.get_reservation(held.id) == held
    assert repo.active_reservation_for_attempt(attempt_id) == held
    assert repo.count_active_reservations("default") == 1


def test_external_quota_sync_is_exact_idempotent(repo) -> None:
    lease = repo.acquire_lease("default", "owner", ttl_seconds=120, now=LEASE_START)
    assert lease is not None
    occurred_at = datetime(2099, 1, 1, 21, 0, tzinfo=UTC)

    first = repo.sync_external_quota(
        "default",
        "neg-external",
        "Europe/Moscow",
        lease.fencing_token,
        occurred_at=occurred_at,
        now=LEASE_START,
    )
    replay = repo.sync_external_quota(
        "default",
        "neg-external",
        "Europe/Moscow",
        lease.fencing_token,
        occurred_at=occurred_at,
        now=LEASE_START,
    )

    assert replay == first
    assert first.source == "external_sync"
    assert first.state is autopilot_types.QuotaReservationState.CONSUMED
    assert first.local_date == "2099-01-02"
    assert (
        repo.conn.execute(
            """
        SELECT COUNT(*) FROM hh_autopilot_quota_reservations
        WHERE remote_negotiation_id = 'neg-external'
        """
        ).fetchone()[0]
        == 1
    )

    other_lease = repo.acquire_lease(
        "other", "other-owner", ttl_seconds=120, now=LEASE_START
    )
    assert other_lease is not None
    with pytest.raises(StaleWrite):
        repo.sync_external_quota(
            "other",
            "neg-external",
            "Europe/Moscow",
            other_lease.fencing_token,
            occurred_at=occurred_at,
            now=LEASE_START,
        )


def test_external_sync_returns_dispatch_reservation_with_same_remote_id(repo) -> None:
    lease, run, _item, attempt_id = _dispatch_context(repo)
    now = LEASE_START + timedelta(seconds=1)
    reservation = repo.reserve_quota(
        "default",
        run.id,
        attempt_id,
        "UTC",
        10,
        10,
        lease.fencing_token,
        now=now,
    )
    consumed = repo.consume_reservation(
        reservation.id,
        lease.fencing_token,
        remote_negotiation_id="neg-dispatch",
        now=now,
    )

    synced = repo.sync_external_quota(
        "default",
        "neg-dispatch",
        "Europe/Moscow",
        lease.fencing_token,
        occurred_at=now,
        now=now,
    )

    assert synced == consumed
    assert (
        repo.conn.execute(
            "SELECT COUNT(*) FROM hh_autopilot_quota_reservations"
        ).fetchone()[0]
        == 1
    )


def test_quota_inputs_are_strict_and_validated_before_begin(repo) -> None:
    lease, run, _item, attempt_id = _dispatch_context(repo)
    invalid_calls = [
        lambda: repo.reserve_quota(
            "default",
            True,
            attempt_id,
            "UTC",
            1,
            1,
            lease.fencing_token,
            now=LEASE_START,
        ),
        lambda: repo.reserve_quota(
            "default",
            run.id,
            0,
            "UTC",
            1,
            1,
            lease.fencing_token,
            now=LEASE_START,
        ),
        lambda: repo.reserve_quota(
            "default",
            run.id,
            attempt_id,
            "Not/A_Real_Zone",
            1,
            1,
            lease.fencing_token,
            now=LEASE_START,
        ),
        lambda: repo.reserve_quota(
            "default",
            run.id,
            attempt_id,
            "UTC",
            True,
            1,
            lease.fencing_token,
            now=LEASE_START,
        ),
        lambda: repo.reserve_quota(
            "default",
            run.id,
            attempt_id,
            "UTC",
            1,
            0,
            lease.fencing_token,
            now=LEASE_START,
        ),
        lambda: repo.reserve_quota(
            "default",
            run.id,
            attempt_id,
            "UTC",
            1,
            1,
            True,
            now=LEASE_START,
        ),
        lambda: repo.reserve_quota(
            "default",
            run.id,
            attempt_id,
            "UTC",
            1,
            1,
            lease.fencing_token,
            now=LEASE_START.replace(tzinfo=None),
        ),
        lambda: repo.sync_external_quota(
            "default",
            "neg\0tail",
            "UTC",
            lease.fencing_token,
            now=LEASE_START,
        ),
    ]

    for invalid_call in invalid_calls:
        statements: list[str] = []
        repo.conn.set_trace_callback(statements.append)
        try:
            with pytest.raises((TypeError, ValueError)):
                invalid_call()
        finally:
            repo.conn.set_trace_callback(None)
        assert not any(statement.startswith("BEGIN") for statement in statements)
    assert (
        repo.conn.execute(
            "SELECT COUNT(*) FROM hh_autopilot_quota_reservations"
        ).fetchone()[0]
        == 0
    )


def test_reserve_validates_run_attempt_account_and_fence(repo) -> None:
    lease, run, item, attempt_id = _dispatch_context(repo)
    now = LEASE_START + timedelta(seconds=1)
    other_run = repo.create_run(
        "other",
        trigger="manual",
        policy_hash="hash",
        fencing_token=lease.fencing_token,
    )
    other_item = repo.create_item(other_run.id, "other", "other-v", "r-1", "preset")
    other_attempt = _insert_autopilot_attempt(
        repo,
        account_id="other",
        run_id=other_run.id,
        item_id=other_item.id,
        vacancy_id="other-v",
    )

    with pytest.raises(ValueError, match="run account"):
        repo.reserve_quota(
            "default",
            other_run.id,
            other_attempt,
            "UTC",
            10,
            10,
            lease.fencing_token,
            now=now,
        )
    with pytest.raises(ValueError, match="attempt account"):
        repo.reserve_quota(
            "default",
            run.id,
            other_attempt,
            "UTC",
            10,
            10,
            lease.fencing_token,
            now=now,
        )
    repo.conn.execute(
        "UPDATE hh_application_attempts SET autopilot_run_id = NULL WHERE id = ?",
        (attempt_id,),
    )
    repo.conn.commit()
    with pytest.raises(ValueError, match="attempt run"):
        repo.reserve_quota(
            "default",
            run.id,
            attempt_id,
            "UTC",
            10,
            10,
            lease.fencing_token,
            now=now,
        )

    repo.conn.execute(
        "UPDATE hh_application_attempts SET autopilot_run_id = ? WHERE id = ?",
        (run.id, attempt_id),
    )
    repo.conn.execute(
        "UPDATE hh_application_attempts SET autopilot_item_id = NULL WHERE id = ?",
        (attempt_id,),
    )
    repo.conn.commit()
    with pytest.raises(ValueError, match="attempt item"):
        repo.reserve_quota(
            "default",
            run.id,
            attempt_id,
            "UTC",
            10,
            10,
            lease.fencing_token,
            now=now,
        )
    repo.conn.execute(
        "UPDATE hh_application_attempts SET autopilot_item_id = ? WHERE id = ?",
        (item.id, attempt_id),
    )
    repo.conn.execute(
        "UPDATE hh_autopilot_runs SET fencing_token = ? WHERE id = ?",
        (lease.fencing_token + 1, run.id),
    )
    repo.conn.commit()
    with pytest.raises(LostLease, match="run"):
        repo.reserve_quota(
            "default",
            run.id,
            attempt_id,
            "UTC",
            10,
            10,
            lease.fencing_token,
            now=now,
        )
    assert repo.get_item(item.id) is not None


def test_reserve_rejects_missing_attempt_and_non_running_run(repo) -> None:
    lease, run, _item, attempt_id = _dispatch_context(repo)
    now = LEASE_START + timedelta(seconds=1)
    with pytest.raises(StaleWrite, match="does not exist"):
        repo.reserve_quota(
            "default",
            run.id,
            attempt_id + 999,
            "UTC",
            10,
            10,
            lease.fencing_token,
            now=now,
        )
    repo.finish_run(run.id, status="completed")
    with pytest.raises(StaleWrite, match="not running"):
        repo.reserve_quota(
            "default",
            run.id,
            attempt_id,
            "UTC",
            10,
            10,
            lease.fencing_token,
            now=now,
        )


def test_account_cooldown_blocks_all_ready_items_then_expires(repo) -> None:
    lease, run, first_item, _attempt_id = _dispatch_context(repo)
    second_item = repo.create_item(run.id, "default", "v-2", "r-1", "preset")
    repo.conn.executemany(
        "UPDATE hh_autopilot_items SET state = 'ready' WHERE id = ?",
        [(first_item.id,), (second_item.id,)],
    )
    repo.conn.commit()

    cooldown = repo.set_account_cooldown(
        "default",
        LEASE_START + timedelta(seconds=60),
        "hh_daily_limit",
        lease.fencing_token,
        hh_reset={"authorization": "Bearer secret", "remaining": 0},
        now=LEASE_START,
    )
    assert cooldown.blocked_until == (LEASE_START + timedelta(seconds=60)).isoformat()
    assert cooldown.hh_reset == {"authorization": "***", "remaining": 0}
    cooldown.hh_reset["remaining"] = 9
    assert repo.get_account_state("default").hh_reset["remaining"] == 0

    for _ready_item in (first_item, second_item):
        with pytest.raises(repository_module.CooldownActive) as error:
            repo.assert_dispatch_available(
                "default",
                lease.fencing_token,
                now=LEASE_START + timedelta(seconds=59),
            )
        assert error.value.reason == "hh_daily_limit"
        assert error.value.until == cooldown.blocked_until

    repo.assert_dispatch_available(
        "default",
        lease.fencing_token,
        now=LEASE_START + timedelta(seconds=60),
    )


def test_cooldown_inputs_are_strict_and_validated_before_begin(repo) -> None:
    lease, _run, _item, _attempt_id = _dispatch_context(repo)
    invalid_calls = [
        lambda: repo.set_account_cooldown(
            "default",
            LEASE_START.replace(tzinfo=None),
            "rate_limited",
            lease.fencing_token,
            now=LEASE_START,
        ),
        lambda: repo.set_account_cooldown(
            "default",
            LEASE_START + timedelta(seconds=60),
            "rate\0limited",
            lease.fencing_token,
            now=LEASE_START,
        ),
        lambda: repo.set_account_cooldown(
            "default",
            LEASE_START + timedelta(seconds=60),
            "rate_limited",
            True,
            now=LEASE_START,
        ),
        lambda: repo.set_account_cooldown(
            "default",
            LEASE_START + timedelta(seconds=60),
            "rate_limited",
            lease.fencing_token,
            now=LEASE_START.replace(tzinfo=None),
        ),
        lambda: repo.set_account_cooldown(
            "default",
            LEASE_START + timedelta(seconds=60),
            "rate_limited",
            lease.fencing_token,
            hh_reset={"unsafe": object()},
            now=LEASE_START,
        ),
        lambda: repo.assert_dispatch_available(
            "default\0other", lease.fencing_token, now=LEASE_START
        ),
    ]
    for invalid_call in invalid_calls:
        statements: list[str] = []
        repo.conn.set_trace_callback(statements.append)
        try:
            with pytest.raises((TypeError, ValueError)):
                invalid_call()
        finally:
            repo.conn.set_trace_callback(None)
        assert not any(statement.startswith("BEGIN") for statement in statements)


def test_timezone_change_is_blocked_only_by_live_authority_or_unresolved_quota(
    repo,
) -> None:
    repo.create_grants([("default", "hash", "operator", "cli")])
    with pytest.raises(repository_module.TimezoneChangeUnsafe) as grant_error:
        repo.assert_timezone_change_safe("default")
    assert grant_error.value.reason == "active_grant"
    repo.revoke_grants(["default"], actor="operator", reason="test")

    lease, run, _item, first_attempt_id = _dispatch_context(repo)
    second_item = repo.create_item(run.id, "default", "v-2", "r-1", "preset")
    second_attempt_id = _insert_autopilot_attempt(
        repo,
        account_id="default",
        run_id=run.id,
        item_id=second_item.id,
        vacancy_id="v-2",
    )
    now = LEASE_START + timedelta(seconds=1)
    consumed = repo.consume_reservation(
        repo.reserve_quota(
            "default",
            run.id,
            first_attempt_id,
            "UTC",
            10,
            10,
            lease.fencing_token,
            now=now,
        ).id,
        lease.fencing_token,
        now=now,
    )
    assert consumed.state is autopilot_types.QuotaReservationState.CONSUMED
    repo.assert_timezone_change_safe("default")

    held = repo.hold_reservation(
        repo.reserve_quota(
            "default",
            run.id,
            second_attempt_id,
            "UTC",
            10,
            10,
            lease.fencing_token,
            now=now,
        ).id,
        lease.fencing_token,
        now=now,
    )
    with pytest.raises(repository_module.TimezoneChangeUnsafe) as quota_error:
        repo.assert_timezone_change_safe("default")
    assert quota_error.value.reason == "unresolved_reservation"
    repo.release_reservation(held.id, lease.fencing_token, now=now)
    repo.assert_timezone_change_safe("default")


def test_lease_quota_and_cooldown_writes_roll_back_on_trigger_abort(repo) -> None:
    repo.conn.execute(
        """
        CREATE TRIGGER abort_lease_insert
        BEFORE INSERT ON hh_autopilot_leases
        BEGIN
            SELECT RAISE(ABORT, 'lease rejected');
        END
        """
    )
    repo.conn.commit()
    with pytest.raises(sqlite3.IntegrityError, match="lease rejected"):
        repo.acquire_lease("default", "owner", ttl_seconds=120, now=LEASE_START)
    assert repo.get_lease("default") is None
    repo.conn.execute("DROP TRIGGER abort_lease_insert")
    repo.conn.commit()

    lease, run, _item, attempt_id = _dispatch_context(repo)
    repo.conn.execute(
        """
        CREATE TRIGGER abort_quota_insert
        BEFORE INSERT ON hh_autopilot_quota_reservations
        BEGIN
            SELECT RAISE(ABORT, 'quota rejected');
        END
        """
    )
    repo.conn.commit()
    with pytest.raises(sqlite3.IntegrityError, match="quota rejected"):
        repo.reserve_quota(
            "default",
            run.id,
            attempt_id,
            "UTC",
            10,
            10,
            lease.fencing_token,
            now=LEASE_START + timedelta(seconds=1),
        )
    assert (
        repo.conn.execute(
            "SELECT COUNT(*) FROM hh_autopilot_quota_reservations"
        ).fetchone()[0]
        == 0
    )
    repo.conn.execute("DROP TRIGGER abort_quota_insert")
    repo.conn.commit()

    repo.conn.execute(
        """
        CREATE TRIGGER abort_cooldown_insert
        BEFORE INSERT ON hh_autopilot_account_state
        BEGIN
            SELECT RAISE(ABORT, 'cooldown rejected');
        END
        """
    )
    repo.conn.commit()
    with pytest.raises(sqlite3.IntegrityError, match="cooldown rejected"):
        repo.set_account_cooldown(
            "default",
            LEASE_START + timedelta(seconds=60),
            "rate_limited",
            lease.fencing_token,
            now=LEASE_START + timedelta(seconds=1),
        )
    assert repo.get_account_state("default") is None


def test_reservation_cas_rolls_back_when_update_trigger_aborts(repo) -> None:
    lease, run, _item, attempt_id = _dispatch_context(repo)
    now = LEASE_START + timedelta(seconds=1)
    reservation = repo.reserve_quota(
        "default",
        run.id,
        attempt_id,
        "UTC",
        10,
        10,
        lease.fencing_token,
        now=now,
    )
    repo.conn.execute(
        """
        CREATE TRIGGER abort_quota_hold
        BEFORE UPDATE ON hh_autopilot_quota_reservations
        WHEN NEW.state = 'held'
        BEGIN
            SELECT RAISE(ABORT, 'hold rejected');
        END
        """
    )
    repo.conn.commit()

    with pytest.raises(sqlite3.IntegrityError, match="hold rejected"):
        repo.hold_reservation(reservation.id, lease.fencing_token, now=now)
    assert repo.get_reservation(reservation.id).state is (
        autopilot_types.QuotaReservationState.RESERVED
    )


def test_task9_primitives_compose_without_nested_begin_and_roll_back(repo) -> None:
    lease, run, item, attempt_id = _dispatch_context(repo)
    now = LEASE_START + timedelta(seconds=1)
    repo.conn.execute(
        """
        CREATE TRIGGER abort_composed_cooldown
        BEFORE INSERT ON hh_autopilot_account_state
        BEGIN
            SELECT RAISE(ABORT, 'composed cooldown rejected');
        END
        """
    )
    repo.conn.commit()

    with pytest.raises(sqlite3.IntegrityError, match="composed cooldown rejected"):
        with repo.immediate():
            reservation = repo._reserve_quota_for_update(
                account_id="default",
                run_id=run.id,
                attempt_id=attempt_id,
                timezone_name="UTC",
                local_date="2099-01-01",
                daily_limit=10,
                run_limit=10,
                fencing_token=lease.fencing_token,
                instant=now,
            )
            repo._transition_item_for_update(
                item.id,
                item.version,
                AutopilotState.SKIPPED,
                "composed_transition",
                {},
                run_id=run.id,
                fencing_token=lease.fencing_token,
                fence_instant=now,
            )
            repo._change_reservation_state_for_update(
                reservation.id,
                lease.fencing_token,
                target=autopilot_types.QuotaReservationState.HELD,
                remote_negotiation_id=None,
                instant=now,
            )
            repo._set_account_cooldown_for_update(
                account_id="default",
                blocked_until=now + timedelta(seconds=60),
                reason="rate_limited",
                fencing_token=lease.fencing_token,
                hh_reset_json="{}",
                instant=now,
            )

    assert repo.get_item(item.id) == item
    assert [event["reason_code"] for event in repo.list_events(item.id)] == [
        "discovered"
    ]
    assert repo.get_reservation(1) is None
    assert repo.get_account_state("default") is None


def test_release_tombstone_preserves_fence_high_water_and_public_absence(repo) -> None:
    first = repo.acquire_lease("default", "same-owner", ttl_seconds=60, now=LEASE_START)
    assert first is not None

    assert repo.release_lease(first) is True
    assert repo.get_lease("default") is None
    tombstone = repo.conn.execute(
        "SELECT * FROM hh_autopilot_leases WHERE account_profile_id = 'default'"
    ).fetchone()
    assert tombstone is not None
    assert tombstone["owner_token"] == ""
    assert tombstone["fencing_token"] == first.fencing_token
    assert tombstone["expires_at"] == "0001-01-01T00:00:00+00:00"

    reacquired = repo.acquire_lease(
        "default", "same-owner", ttl_seconds=60, now=LEASE_START
    )
    assert reacquired is not None
    assert reacquired.fencing_token == first.fencing_token + 1
    assert repo.release_lease(first) is False
    assert repo.get_lease("default") == reacquired


def test_release_tombstone_rejects_historical_fence_and_old_protected_write(
    repo,
) -> None:
    first, run, _item, attempt_id = _dispatch_context(repo)
    assert repo.release_lease(first) is True
    with pytest.raises(LostLease):
        repo.assert_fence(
            "default",
            first.fencing_token,
            now=datetime.min.replace(tzinfo=UTC),
        )

    reacquired = repo.acquire_lease(
        "default", "new-owner", ttl_seconds=120, now=LEASE_START
    )
    assert reacquired is not None
    with pytest.raises(LostLease):
        repo.reserve_quota(
            "default",
            run.id,
            attempt_id,
            "UTC",
            10,
            10,
            first.fencing_token,
            now=LEASE_START,
        )
    assert (
        repo.conn.execute(
            "SELECT COUNT(*) FROM hh_autopilot_quota_reservations"
        ).fetchone()[0]
        == 0
    )


def _lease_release_worker(
    database_path: Any,
    barrier: Barrier,
    lease: LeaseRecord,
) -> bool:
    storage = Storage(database_path)
    worker_repo = AutopilotRepository(storage)
    try:
        barrier.wait()
        return worker_repo.release_lease(lease)
    finally:
        storage.close()


def test_concurrent_exact_release_has_one_winner_and_keeps_high_water(repo) -> None:
    lease = repo.acquire_lease("default", "owner", ttl_seconds=60, now=LEASE_START)
    assert lease is not None
    barrier = Barrier(2)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = [
            future.result()
            for future in [
                pool.submit(
                    _lease_release_worker,
                    repo.storage.path,
                    barrier,
                    lease,
                )
                for _ in range(2)
            ]
        ]

    assert sorted(outcomes) == [False, True]
    assert repo.get_lease("default") is None
    next_lease = repo.acquire_lease("default", "owner", ttl_seconds=60, now=LEASE_START)
    assert next_lease is not None
    assert next_lease.fencing_token == lease.fencing_token + 1


def _recovery_adoption_context(
    repo: AutopilotRepository,
    *,
    reservation_state: str,
) -> tuple[Any, Any, RunRecord, ItemRecord, int, Any]:
    old_lease, old_run, item, attempt_id = _dispatch_context(repo)
    before_takeover = LEASE_START + timedelta(seconds=1)
    repo.conn.execute(
        """
        UPDATE hh_autopilot_items
        SET state = 'applying', active_attempt_id = ?
        WHERE id = ?
        """,
        (attempt_id, item.id),
    )
    repo.conn.commit()
    reservation = repo.reserve_quota(
        "default",
        old_run.id,
        attempt_id,
        "UTC",
        10,
        10,
        old_lease.fencing_token,
        now=before_takeover,
    )
    if reservation_state == "held":
        reservation = repo.hold_reservation(
            reservation.id,
            old_lease.fencing_token,
            now=before_takeover,
        )
    else:
        assert reservation_state == "reserved"

    takeover_at = LEASE_START + timedelta(seconds=120)
    current_lease = repo.acquire_lease(
        "default",
        "recovery-owner",
        ttl_seconds=120,
        now=takeover_at,
    )
    assert current_lease is not None
    recovery_run = repo.create_run(
        "default",
        trigger="recovery",
        policy_hash="policy-hash",
        fencing_token=current_lease.fencing_token,
    )
    if reservation_state == "held":
        repo.conn.execute(
            """
            UPDATE hh_autopilot_items
            SET state = 'reconciling', last_run_id = ?
            WHERE id = ?
            """,
            (recovery_run.id, item.id),
        )
        repo.conn.commit()
    return (
        old_lease,
        current_lease,
        recovery_run,
        item,
        attempt_id,
        reservation,
    )


def test_recovery_adopts_reserved_applying_then_normal_cas_resolves(repo) -> None:
    old, current, recovery_run, _item, _attempt_id, reservation = (
        _recovery_adoption_context(repo, reservation_state="reserved")
    )
    now = LEASE_START + timedelta(seconds=120)

    adopted = repo.adopt_reservation_for_recovery(
        reservation.id,
        recovery_run.id,
        current.fencing_token,
        expected_fencing_token=old.fencing_token,
        now=now,
    )
    replay = repo.adopt_reservation_for_recovery(
        reservation.id,
        recovery_run.id,
        current.fencing_token,
        expected_fencing_token=old.fencing_token,
        now=now,
    )

    assert replay == adopted
    assert adopted.state is autopilot_types.QuotaReservationState.RESERVED
    assert adopted.fencing_token == current.fencing_token
    held = repo.hold_reservation(
        adopted.id,
        current.fencing_token,
        now=now,
    )
    assert held.state is autopilot_types.QuotaReservationState.HELD


def test_recovery_primitive_adopts_held_reconciling_then_release_resolves(repo) -> None:
    old, current, recovery_run, _item, _attempt_id, reservation = (
        _recovery_adoption_context(repo, reservation_state="held")
    )
    now = LEASE_START + timedelta(seconds=120)

    with repo.immediate():
        adopted = repo._adopt_reservation_for_recovery_for_update(
            reservation.id,
            recovery_run.id,
            current.fencing_token,
            expected_fencing_token=old.fencing_token,
            instant=now,
        )

    assert adopted.state is autopilot_types.QuotaReservationState.HELD
    assert adopted.fencing_token == current.fencing_token
    released = repo.release_reservation(
        adopted.id,
        current.fencing_token,
        now=now,
    )
    assert released.state is autopilot_types.QuotaReservationState.RELEASED


def test_recovery_adopts_held_reconciling_with_original_dispatch_last_run(repo) -> None:
    old_lease, old_run, item, attempt_id = _dispatch_context(repo)
    before_takeover = LEASE_START + timedelta(seconds=1)
    repo.conn.execute(
        """
        UPDATE hh_autopilot_items
        SET state = 'applying', active_attempt_id = ?
        WHERE id = ?
        """,
        (attempt_id, item.id),
    )
    repo.conn.commit()
    reservation = repo.reserve_quota(
        "default",
        old_run.id,
        attempt_id,
        "UTC",
        10,
        10,
        old_lease.fencing_token,
        now=before_takeover,
    )
    reservation = repo.hold_reservation(
        reservation.id,
        old_lease.fencing_token,
        now=before_takeover,
    )
    repo.conn.execute(
        "UPDATE hh_autopilot_items SET state = 'reconciling' WHERE id = ?",
        (item.id,),
    )
    repo.conn.commit()
    assert repo.get_item(item.id).last_run_id == old_run.id

    takeover_at = LEASE_START + timedelta(seconds=120)
    current_lease = repo.acquire_lease(
        "default",
        "recovery-owner",
        ttl_seconds=120,
        now=takeover_at,
    )
    assert current_lease is not None
    recovery_run = repo.create_run(
        "default",
        trigger="recovery",
        policy_hash="policy-hash",
        fencing_token=current_lease.fencing_token,
    )

    adopted = repo.adopt_reservation_for_recovery(
        reservation.id,
        recovery_run.id,
        current_lease.fencing_token,
        expected_fencing_token=old_lease.fencing_token,
        now=takeover_at,
    )

    assert adopted.state is autopilot_types.QuotaReservationState.HELD
    assert adopted.fencing_token == current_lease.fencing_token
    assert repo.get_item(item.id).last_run_id == old_run.id


def test_recovery_adoption_supports_repeated_takeovers_without_item_rewrite(
    repo,
) -> None:
    old, current, recovery_run, item, _attempt_id, reservation = (
        _recovery_adoption_context(repo, reservation_state="held")
    )
    first_takeover_at = LEASE_START + timedelta(seconds=120)
    adopted = repo.adopt_reservation_for_recovery(
        reservation.id,
        recovery_run.id,
        current.fencing_token,
        expected_fencing_token=old.fencing_token,
        now=first_takeover_at,
    )
    repo.finish_run(recovery_run.id, status="interrupted")

    previous_lease = current
    for takeover_index, elapsed_seconds in enumerate((300, 480), start=2):
        takeover_at = LEASE_START + timedelta(seconds=elapsed_seconds)
        next_lease = repo.acquire_lease(
            "default",
            f"recovery-owner-{takeover_index}",
            ttl_seconds=120,
            now=takeover_at,
        )
        assert next_lease is not None
        assert next_lease.fencing_token == previous_lease.fencing_token + 1
        next_recovery_run = repo.create_run(
            "default",
            trigger="recovery",
            policy_hash="policy-hash",
            fencing_token=next_lease.fencing_token,
        )

        adopted = repo.adopt_reservation_for_recovery(
            adopted.id,
            next_recovery_run.id,
            next_lease.fencing_token,
            expected_fencing_token=previous_lease.fencing_token,
            now=takeover_at,
        )
        repo.finish_run(next_recovery_run.id, status="interrupted")
        previous_lease = next_lease

    assert adopted.state is autopilot_types.QuotaReservationState.HELD
    assert adopted.fencing_token == old.fencing_token + 3
    assert repo.get_item(item.id).last_run_id == recovery_run.id


def test_recovery_adoption_rejects_wrong_trigger_status_and_expected_fence(
    repo,
) -> None:
    old, current, recovery_run, _item, _attempt_id, reservation = (
        _recovery_adoption_context(repo, reservation_state="reserved")
    )
    now = LEASE_START + timedelta(seconds=120)
    manual_run = repo.create_run(
        "default",
        trigger="manual",
        policy_hash="hash",
        fencing_token=current.fencing_token,
    )

    with pytest.raises(StaleWrite, match="recovery"):
        repo.adopt_reservation_for_recovery(
            reservation.id,
            manual_run.id,
            current.fencing_token,
            expected_fencing_token=old.fencing_token,
            now=now,
        )
    repo.finish_run(recovery_run.id, status="completed")
    with pytest.raises(StaleWrite, match="running"):
        repo.adopt_reservation_for_recovery(
            reservation.id,
            recovery_run.id,
            current.fencing_token,
            expected_fencing_token=old.fencing_token,
            now=now,
        )
    next_recovery_run = repo.create_run(
        "default",
        trigger="recovery",
        policy_hash="hash",
        fencing_token=current.fencing_token,
    )
    with pytest.raises(StaleWrite, match="expected"):
        repo.adopt_reservation_for_recovery(
            reservation.id,
            next_recovery_run.id,
            current.fencing_token,
            expected_fencing_token=current.fencing_token + 10,
            now=now,
        )
    assert repo.get_reservation(reservation.id).fencing_token == old.fencing_token


def test_recovery_adoption_rolls_back_on_fence_cas_trigger(repo) -> None:
    old, current, recovery_run, _item, _attempt_id, reservation = (
        _recovery_adoption_context(repo, reservation_state="held")
    )
    repo.conn.execute(
        f"""
        CREATE TRIGGER abort_reservation_adoption
        BEFORE UPDATE ON hh_autopilot_quota_reservations
        WHEN NEW.fencing_token = {current.fencing_token}
        BEGIN
            SELECT RAISE(ABORT, 'adoption rejected');
        END
        """
    )
    repo.conn.commit()

    with pytest.raises(sqlite3.IntegrityError, match="adoption rejected"):
        repo.adopt_reservation_for_recovery(
            reservation.id,
            recovery_run.id,
            current.fencing_token,
            expected_fencing_token=old.fencing_token,
            now=LEASE_START + timedelta(seconds=120),
        )
    row = repo.conn.execute(
        "SELECT fencing_token, state FROM hh_autopilot_quota_reservations WHERE id = ?",
        (reservation.id,),
    ).fetchone()
    assert row["fencing_token"] == old.fencing_token
    assert row["state"] == "held"


def test_recovery_adoption_validates_every_input_before_begin(repo) -> None:
    old, current, recovery_run, _item, _attempt_id, reservation = (
        _recovery_adoption_context(repo, reservation_state="reserved")
    )
    valid = (
        reservation.id,
        recovery_run.id,
        current.fencing_token,
        old.fencing_token,
    )
    invalid_calls = [
        lambda: repo.adopt_reservation_for_recovery(
            True, valid[1], valid[2], expected_fencing_token=valid[3]
        ),
        lambda: repo.adopt_reservation_for_recovery(
            valid[0], True, valid[2], expected_fencing_token=valid[3]
        ),
        lambda: repo.adopt_reservation_for_recovery(
            valid[0], valid[1], True, expected_fencing_token=valid[3]
        ),
        lambda: repo.adopt_reservation_for_recovery(
            valid[0], valid[1], valid[2], expected_fencing_token=True
        ),
        lambda: repo.adopt_reservation_for_recovery(
            valid[0],
            valid[1],
            valid[2],
            expected_fencing_token=valid[3],
            now=LEASE_START.replace(tzinfo=None),
        ),
    ]
    for invalid_call in invalid_calls:
        statements: list[str] = []
        repo.conn.set_trace_callback(statements.append)
        try:
            with pytest.raises((TypeError, ValueError)):
                invalid_call()
        finally:
            repo.conn.set_trace_callback(None)
        assert not any(statement.startswith("BEGIN") for statement in statements)


def _unsafe_real_update(
    repo: AutopilotRepository,
    statement: str,
    parameters: tuple[Any, ...],
) -> None:
    repo.conn.commit()
    repo.conn.execute("PRAGMA foreign_keys = OFF")
    try:
        repo.conn.execute(statement, parameters)
        repo.conn.commit()
    finally:
        repo.conn.execute("PRAGMA foreign_keys = ON")


@pytest.mark.parametrize(
    ("corruption", "expected_error"),
    [
        ("run_fence", LostLease),
        ("attempt_run", StaleWrite),
        ("attempt_item", StaleWrite),
        ("item_last_run", StaleWrite),
    ],
)
def test_reserve_rejects_lossy_real_provenance_without_mutation(
    repo,
    corruption,
    expected_error,
) -> None:
    lease, run, item, attempt_id = _dispatch_context(repo)
    if corruption == "run_fence":
        repo.conn.execute(
            "UPDATE hh_autopilot_runs SET fencing_token = 1.5 WHERE id = ?",
            (run.id,),
        )
        repo.conn.commit()
    elif corruption == "attempt_run":
        repo.conn.execute(
            "UPDATE hh_application_attempts SET autopilot_run_id = 1.5 WHERE id = ?",
            (attempt_id,),
        )
        repo.conn.commit()
    elif corruption == "attempt_item":
        repo.conn.execute(
            "UPDATE hh_application_attempts SET autopilot_item_id = 1.5 WHERE id = ?",
            (attempt_id,),
        )
        repo.conn.commit()
    else:
        _unsafe_real_update(
            repo,
            "UPDATE hh_autopilot_items SET last_run_id = 1.5 WHERE id = ?",
            (item.id,),
        )

    with pytest.raises(expected_error):
        repo.reserve_quota(
            "default",
            run.id,
            attempt_id,
            "UTC",
            10,
            10,
            lease.fencing_token,
            now=LEASE_START + timedelta(seconds=1),
        )
    assert (
        repo.conn.execute(
            "SELECT COUNT(*) FROM hh_autopilot_quota_reservations"
        ).fetchone()[0]
        == 0
    )


def test_reservation_cas_rejects_real_fence_before_update(repo) -> None:
    lease, run, _item, attempt_id = _dispatch_context(repo)
    reservation = repo.reserve_quota(
        "default",
        run.id,
        attempt_id,
        "UTC",
        10,
        10,
        lease.fencing_token,
        now=LEASE_START + timedelta(seconds=1),
    )
    repo.conn.execute(
        "UPDATE hh_autopilot_quota_reservations SET fencing_token = 1.5 WHERE id = ?",
        (reservation.id,),
    )
    repo.conn.commit()
    statements: list[str] = []
    repo.conn.set_trace_callback(statements.append)
    try:
        with pytest.raises(StaleWrite, match="fencing"):
            repo.hold_reservation(
                reservation.id,
                lease.fencing_token,
                now=LEASE_START + timedelta(seconds=1),
            )
    finally:
        repo.conn.set_trace_callback(None)

    assert not any(
        "UPDATE hh_autopilot_quota_reservations" in statement
        for statement in statements
    )
    row = repo.conn.execute(
        "SELECT state, fencing_token FROM hh_autopilot_quota_reservations WHERE id = ?",
        (reservation.id,),
    ).fetchone()
    assert row["state"] == "reserved"
    assert row["fencing_token"] == 1.5


@pytest.mark.parametrize(
    "corruption",
    [
        "reservation_attempt_null",
        "reservation_run_real",
        "reservation_fence_real",
        "attempt_account",
        "attempt_run_real",
        "attempt_item_real",
        "item_active_real",
        "item_state_ready",
    ],
)
def test_recovery_adoption_rejects_wrong_or_lossy_provenance(repo, corruption) -> None:
    old, current, recovery_run, item, attempt_id, reservation = (
        _recovery_adoption_context(repo, reservation_state="held")
    )
    expected_stored_fence: int | float = old.fencing_token
    if corruption == "reservation_attempt_null":
        repo.conn.execute(
            "UPDATE hh_autopilot_quota_reservations SET attempt_id = NULL WHERE id = ?",
            (reservation.id,),
        )
        repo.conn.commit()
    elif corruption == "reservation_run_real":
        _unsafe_real_update(
            repo,
            "UPDATE hh_autopilot_quota_reservations SET run_id = 1.5 WHERE id = ?",
            (reservation.id,),
        )
    elif corruption == "reservation_fence_real":
        repo.conn.execute(
            "UPDATE hh_autopilot_quota_reservations SET fencing_token = 1.5 WHERE id = ?",
            (reservation.id,),
        )
        repo.conn.commit()
        expected_stored_fence = 1.5
    elif corruption == "attempt_account":
        repo.conn.execute(
            "UPDATE hh_application_attempts SET account_profile_id = 'other' WHERE id = ?",
            (attempt_id,),
        )
        repo.conn.commit()
    elif corruption == "attempt_run_real":
        repo.conn.execute(
            "UPDATE hh_application_attempts SET autopilot_run_id = 1.5 WHERE id = ?",
            (attempt_id,),
        )
        repo.conn.commit()
    elif corruption == "attempt_item_real":
        repo.conn.execute(
            "UPDATE hh_application_attempts SET autopilot_item_id = 1.5 WHERE id = ?",
            (attempt_id,),
        )
        repo.conn.commit()
    elif corruption == "item_active_real":
        _unsafe_real_update(
            repo,
            "UPDATE hh_autopilot_items SET active_attempt_id = 1.5 WHERE id = ?",
            (item.id,),
        )
    else:
        repo.conn.execute(
            "UPDATE hh_autopilot_items SET state = 'ready' WHERE id = ?",
            (item.id,),
        )
        repo.conn.commit()

    with pytest.raises(StaleWrite):
        repo.adopt_reservation_for_recovery(
            reservation.id,
            recovery_run.id,
            current.fencing_token,
            expected_fencing_token=old.fencing_token,
            now=LEASE_START + timedelta(seconds=120),
        )
    raw = repo.conn.execute(
        "SELECT fencing_token, state FROM hh_autopilot_quota_reservations WHERE id = ?",
        (reservation.id,),
    ).fetchone()
    assert raw["fencing_token"] == expected_stored_fence
    assert raw["state"] == "held"


def test_external_sync_replay_requires_exact_occurrence_instant(repo) -> None:
    lease = repo.acquire_lease("default", "owner", ttl_seconds=120, now=LEASE_START)
    assert lease is not None
    occurred = LEASE_START + timedelta(seconds=10)
    first = repo.sync_external_quota(
        "default",
        "neg-exact-time",
        "UTC",
        lease.fencing_token,
        occurred_at=occurred,
        now=LEASE_START,
    )

    with pytest.raises(StaleWrite, match="occurrence"):
        repo.sync_external_quota(
            "default",
            "neg-exact-time",
            "UTC",
            lease.fencing_token,
            occurred_at=occurred + timedelta(seconds=1),
            now=LEASE_START,
        )

    assert repo.get_reservation(first.id) == first
    assert (
        repo.conn.execute(
            "SELECT COUNT(*) FROM hh_autopilot_quota_reservations"
        ).fetchone()[0]
        == 1
    )


@pytest.mark.parametrize(
    ("column", "stored_value"),
    [
        ("created_at", "not-a-time"),
        ("created_at", "2099-01-01T09:00:10"),
        ("resolved_at", "not-a-time"),
        ("resolved_at", "2099-01-01T09:00:10"),
    ],
)
def test_external_sync_replay_rejects_malformed_stored_occurrence(
    repo,
    column,
    stored_value,
) -> None:
    lease = repo.acquire_lease("default", "owner", ttl_seconds=120, now=LEASE_START)
    assert lease is not None
    occurred = LEASE_START + timedelta(seconds=10)
    reservation = repo.sync_external_quota(
        "default",
        "neg-malformed-time",
        "UTC",
        lease.fencing_token,
        occurred_at=occurred,
        now=LEASE_START,
    )
    repo.conn.execute(
        f"UPDATE hh_autopilot_quota_reservations SET {column} = ? WHERE id = ?",
        (stored_value, reservation.id),
    )
    repo.conn.commit()

    with pytest.raises(StaleWrite, match="occurrence"):
        repo.sync_external_quota(
            "default",
            "neg-malformed-time",
            "UTC",
            lease.fencing_token,
            occurred_at=occurred,
            now=LEASE_START,
        )


def test_dispatch_remote_replay_keeps_occurrence_compatibility(repo) -> None:
    lease, run, _item, attempt_id = _dispatch_context(repo)
    reserved = repo.reserve_quota(
        "default",
        run.id,
        attempt_id,
        "UTC",
        10,
        10,
        lease.fencing_token,
        now=LEASE_START + timedelta(seconds=1),
    )
    consumed = repo.consume_reservation(
        reserved.id,
        lease.fencing_token,
        remote_negotiation_id="neg-dispatch-time",
        now=LEASE_START + timedelta(seconds=1),
    )

    replay = repo.sync_external_quota(
        "default",
        "neg-dispatch-time",
        "Europe/Moscow",
        lease.fencing_token,
        occurred_at=LEASE_START + timedelta(seconds=50),
        now=LEASE_START + timedelta(seconds=2),
    )
    assert replay == consumed


def _live_grant_snapshot_context(repo: AutopilotRepository) -> tuple[Any, Any, int]:
    policy_hash = "live-policy-hash"
    generation = repo.create_grants([("default", policy_hash, "operator", "test")])[
        "default"
    ]
    grant = repo.active_grant("default")
    assert grant is not None
    run = repo.create_run(
        "default",
        trigger="manual",
        policy_hash=policy_hash,
        grant_id=grant.id,
        fencing_token=11,
    )
    return grant, run, generation


def test_live_snapshot_rejects_real_run_grant_id(repo) -> None:
    grant, run, generation = _live_grant_snapshot_context(repo)
    _unsafe_real_update(
        repo,
        "UPDATE hh_autopilot_runs SET grant_id = 1.5 WHERE id = ?",
        (run.id,),
    )
    stored = repo.conn.execute(
        "SELECT grant_id, typeof(grant_id) AS storage_type "
        "FROM hh_autopilot_runs WHERE id = ?",
        (run.id,),
    ).fetchone()
    assert stored["grant_id"] == 1.5
    assert stored["storage_type"] == "real"

    with pytest.raises(StaleWrite, match="run grant_id"):
        repo.validate_live_authorization_snapshot(
            "default",
            generation=generation,
            policy_hash=grant.policy_hash,
            run_id=run.id,
            fencing_token=run.fencing_token,
        )


def test_live_snapshot_rejects_real_grant_generation(repo) -> None:
    grant, run, generation = _live_grant_snapshot_context(repo)
    _unsafe_real_update(
        repo,
        "UPDATE hh_autopilot_grants SET generation = 1.5 WHERE id = ?",
        (grant.id,),
    )
    stored = repo.conn.execute(
        "SELECT generation, typeof(generation) AS storage_type "
        "FROM hh_autopilot_grants WHERE id = ?",
        (grant.id,),
    ).fetchone()
    assert stored["generation"] == 1.5
    assert stored["storage_type"] == "real"

    with pytest.raises(StaleWrite, match="grant generation"):
        repo.validate_live_authorization_snapshot(
            "default",
            generation=generation,
            policy_hash=grant.policy_hash,
            run_id=run.id,
            fencing_token=run.fencing_token,
        )


def _unsafe_grant_active_update(
    repo: AutopilotRepository,
    grant_id: int,
    value: float,
) -> None:
    repo.conn.commit()
    repo.conn.execute("PRAGMA ignore_check_constraints = ON")
    try:
        repo.conn.execute(
            "UPDATE hh_autopilot_grants SET active = ? WHERE id = ?",
            (value, grant_id),
        )
        repo.conn.commit()
    finally:
        repo.conn.execute("PRAGMA ignore_check_constraints = OFF")


def test_grant_parser_rejects_real_active_flag(repo) -> None:
    grant, _run, _generation = _live_grant_snapshot_context(repo)
    _unsafe_grant_active_update(repo, grant.id, 1.5)
    row = repo.conn.execute(
        "SELECT *, typeof(active) AS active_storage_type "
        "FROM hh_autopilot_grants WHERE id = ?",
        (grant.id,),
    ).fetchone()
    assert row["active"] == 1.5
    assert row["active_storage_type"] == "real"

    with pytest.raises(StaleWrite, match="grant active"):
        repo._grant_from_row(row)


def test_create_grants_rolls_back_batch_on_real_generation_high_water(repo) -> None:
    repo.create_grants(
        [
            ("first", "old-first", "operator", "test"),
            ("broken", "old-broken", "operator", "test"),
        ]
    )
    broken = repo.active_grant("broken")
    assert broken is not None
    _unsafe_real_update(
        repo,
        "UPDATE hh_autopilot_grants SET generation = 1.5 WHERE id = ?",
        (broken.id,),
    )
    before = [
        tuple(row)
        for row in repo.conn.execute(
            "SELECT * FROM hh_autopilot_grants ORDER BY id"
        ).fetchall()
    ]

    with pytest.raises(StaleWrite, match="grant generation"):
        repo.create_grants(
            [
                ("first", "new-first", "operator", "test"),
                ("broken", "new-broken", "operator", "test"),
            ]
        )

    after = [
        tuple(row)
        for row in repo.conn.execute(
            "SELECT * FROM hh_autopilot_grants ORDER BY id"
        ).fetchall()
    ]
    assert after == before


def _prepare_ranked_items(
    repo: AutopilotRepository,
    *,
    account_id: str = "default",
    vacancy_id: str = "v-1",
    resume_ids: tuple[str, ...] = ("r-1", "r-2"),
) -> tuple[RunRecord, tuple[ItemRecord, ...]]:
    run = repo.create_run(account_id, trigger="manual", policy_hash="hash")
    ranked: list[ItemRecord] = []
    for index, resume_id in enumerate(resume_ids):
        item = repo.create_item(
            run.id,
            account_id,
            vacancy_id,
            resume_id,
            "preset:python",
        )
        eligible = repo.record_filter_decision(
            item.id,
            expected_version=item.version,
            decision=_filter_decision(),
            run_id=run.id,
            published_at=f"2026-07-{16 - index:02d}T09:00:00+00:00",
        )
        ranked.append(
            repo.record_ranking_decision(
                eligible.id,
                expected_version=eligible.version,
                decision=_ranking_decision(90 - index),
                run_id=run.id,
            )
        )
    return run, tuple(ranked)


def test_record_filter_decision_persists_evidence_and_one_transition(repo) -> None:
    run = repo.create_run("default", trigger="manual", policy_hash="hash")
    item = repo.create_item(run.id, "default", "v-1", "r-1", "preset:python")
    before_events = len(repo.list_events(item.id))

    changed = repo.record_filter_decision(
        item.id,
        expected_version=item.version,
        decision=_filter_decision(),
        run_id=run.id,
        published_at="2026-07-16T09:00:00+00:00",
    )

    assert changed.state is AutopilotState.ELIGIBLE
    assert changed.query_key == "preset:python"
    assert changed.filter_data == _filter_decision().to_dict()
    assert changed.published_at == "2026-07-16T09:00:00+00:00"
    assert changed.last_outcome_code == "hard_filters_passed"
    events = repo.list_events(item.id)
    assert len(events) == before_events + 1
    assert events[-1]["reason_code"] == "hard_filters_passed"

    changed.filter_data["evidence"]["checks"][0] = "mutated"
    assert (
        repo.get_item(item.id).filter_data["evidence"]["checks"][0]
        == "vacancy_open"
    )


def test_record_filter_rejection_goes_directly_to_skipped(repo) -> None:
    run = repo.create_run("default", trigger="manual", policy_hash="hash")
    item = repo.create_item(run.id, "default", "v-1", "r-1", "preset:python")

    changed = repo.record_filter_decision(
        item.id,
        expected_version=item.version,
        decision=_filter_decision(passed=False),
        run_id=run.id,
    )

    assert changed.state is AutopilotState.SKIPPED
    assert changed.last_outcome_code == "hard_filter:area"


def test_policy_evidence_writes_require_the_items_current_run(repo) -> None:
    origin = repo.create_run("default", trigger="manual", policy_hash="hash")
    other = repo.create_run("default", trigger="manual", policy_hash="hash")
    item = repo.create_item(
        origin.id,
        "default",
        "v-1",
        "r-1",
        "preset:python",
    )

    with pytest.raises(ValueError, match="same run"):
        repo.record_filter_decision(
            item.id,
            expected_version=item.version,
            decision=_filter_decision(),
            run_id=other.id,
        )

    assert repo.get_item(item.id).state is AutopilotState.DISCOVERED


def test_filter_decision_stale_write_and_event_failure_roll_back_everything(repo) -> None:
    run = repo.create_run("default", trigger="manual", policy_hash="hash")
    item = repo.create_item(run.id, "default", "v-1", "r-1", "preset:python")
    repo.conn.execute(
        """
        CREATE TRIGGER abort_filter_decision_event
        BEFORE INSERT ON hh_autopilot_events
        WHEN NEW.reason_code = 'hard_filters_passed'
        BEGIN
            SELECT RAISE(ABORT, 'filter event rejected');
        END
        """
    )
    repo.conn.commit()

    with pytest.raises(sqlite3.IntegrityError, match="filter event rejected"):
        repo.record_filter_decision(
            item.id,
            expected_version=item.version,
            decision=_filter_decision(),
            run_id=run.id,
            published_at="2026-07-16T09:00:00+00:00",
        )

    unchanged = repo.get_item(item.id)
    assert unchanged.state is AutopilotState.DISCOVERED
    assert unchanged.version == item.version
    assert unchanged.filter_data == {}
    assert unchanged.published_at == ""
    assert [event["reason_code"] for event in repo.list_events(item.id)] == [
        "discovered"
    ]

    repo.conn.execute("DROP TRIGGER abort_filter_decision_event")
    repo.conn.commit()
    changed = repo.record_filter_decision(
        item.id,
        expected_version=item.version,
        decision=_filter_decision(),
        run_id=run.id,
    )
    with pytest.raises(StaleWrite):
        repo.record_filter_decision(
            item.id,
            expected_version=item.version,
            decision=_filter_decision(),
            run_id=run.id,
        )
    assert repo.get_item(item.id) == changed


def test_record_ranking_decision_persists_finite_score_and_structured_ai(repo) -> None:
    run = repo.create_run("default", trigger="manual", policy_hash="hash")
    item = repo.create_item(run.id, "default", "v-1", "r-1", "preset:python")
    eligible = repo.record_filter_decision(
        item.id,
        expected_version=item.version,
        decision=_filter_decision(),
        run_id=run.id,
    )

    ranked = repo.record_ranking_decision(
        eligible.id,
        expected_version=eligible.version,
        decision=_ranking_decision(82.5),
        run_id=run.id,
    )

    assert ranked.state is AutopilotState.RANKED
    assert ranked.deterministic_score == 82.5
    assert ranked.ai_data == _ranking_decision(82.5).to_dict()
    assert repo.list_events(item.id)[-1]["reason_code"] == "ai_suitable"
    assert (
        repo.list_events(item.id)[-1]["metadata_json"]
        == _ranking_decision(82.5).to_dict()
    )


def test_ranking_event_failure_rolls_back_score_and_state(repo) -> None:
    run = repo.create_run("default", trigger="manual", policy_hash="hash")
    item = repo.create_item(run.id, "default", "v-1", "r-1", "preset:python")
    eligible = repo.record_filter_decision(
        item.id,
        expected_version=item.version,
        decision=_filter_decision(),
        run_id=run.id,
    )
    repo.conn.execute(
        """
        CREATE TRIGGER abort_ranking_event
        BEFORE INSERT ON hh_autopilot_events
        WHEN NEW.reason_code = 'ai_suitable'
        BEGIN
            SELECT RAISE(ABORT, 'ranking event rejected');
        END
        """
    )
    repo.conn.commit()

    with pytest.raises(sqlite3.IntegrityError, match="ranking event rejected"):
        repo.record_ranking_decision(
            eligible.id,
            expected_version=eligible.version,
            decision=_ranking_decision(),
            run_id=run.id,
        )

    unchanged = repo.get_item(item.id)
    assert unchanged.state is AutopilotState.ELIGIBLE
    assert unchanged.version == eligible.version
    assert unchanged.deterministic_score is None
    assert unchanged.ai_data == {}


def test_finalize_best_resume_marks_one_ready_and_others_not_best(repo) -> None:
    run, ranked = _prepare_ranked_items(repo)

    changed = repo.finalize_ranked_candidates(
        {item.id: item.version for item in ranked},
        selected_item_ids=[ranked[0].id],
        resume_policy="best_resume_only",
        run_id=run.id,
    )

    by_id = {item.id: item for item in changed}
    assert by_id[ranked[0].id].state is AutopilotState.READY
    assert by_id[ranked[0].id].last_outcome_code == "ready"
    assert by_id[ranked[1].id].state is AutopilotState.SKIPPED
    assert by_id[ranked[1].id].last_outcome_code == "not_best_resume"
    assert repo.count_guards() == 0
    assert repo.count_application_attempts() == 0
    assert repo.count_reservations() == 0


def test_finalize_best_resume_rejects_a_non_winning_tie_break_selection(repo) -> None:
    run = repo.create_run("default", trigger="manual", policy_hash="hash")
    ranked: list[ItemRecord] = []
    for resume_id in ("r-2", "r-1"):
        item = repo.create_item(
            run.id,
            "default",
            "v-1",
            resume_id,
            "preset:python",
        )
        eligible = repo.record_filter_decision(
            item.id,
            expected_version=item.version,
            decision=_filter_decision(),
            run_id=run.id,
            published_at="2026-07-16T09:00:00+00:00",
        )
        ranked.append(
            repo.record_ranking_decision(
                eligible.id,
                expected_version=eligible.version,
                decision=_ranking_decision(80),
                run_id=run.id,
            )
        )

    with pytest.raises(ValueError, match="stable best"):
        repo.finalize_ranked_candidates(
            {item.id: item.version for item in ranked},
            selected_item_ids=[ranked[0].id],
            resume_policy="best_resume_only",
            run_id=run.id,
        )

    changed = repo.finalize_ranked_candidates(
        {item.id: item.version for item in ranked},
        selected_item_ids=[ranked[1].id],
        resume_policy="best_resume_only",
        run_id=run.id,
    )
    by_id = {item.id: item for item in changed}
    assert by_id[ranked[1].id].state is AutopilotState.READY


def test_finalize_per_resume_marks_every_qualifying_candidate_ready(repo) -> None:
    run, ranked = _prepare_ranked_items(repo)

    changed = repo.finalize_ranked_candidates(
        {item.id: item.version for item in ranked},
        selected_item_ids=[item.id for item in ranked],
        resume_policy="per_resume",
        run_id=run.id,
    )

    assert [item.state for item in changed] == [
        AutopilotState.READY,
        AutopilotState.READY,
    ]
    assert repo.count_guards() == 0
    assert repo.count_application_attempts() == 0
    assert repo.count_reservations() == 0


def test_finalize_rejects_stale_mixed_or_invalid_candidate_sets(repo) -> None:
    run, ranked = _prepare_ranked_items(repo)
    other_run, other_ranked = _prepare_ranked_items(
        repo,
        account_id="other",
        vacancy_id="v-1",
        resume_ids=("r-3",),
    )
    assert other_run.id != run.id

    with pytest.raises(ValueError, match="account"):
        repo.finalize_ranked_candidates(
            {
                ranked[0].id: ranked[0].version,
                other_ranked[0].id: other_ranked[0].version,
            },
            selected_item_ids=[ranked[0].id],
            resume_policy="best_resume_only",
            run_id=run.id,
        )
    with pytest.raises(StaleWrite):
        repo.finalize_ranked_candidates(
            {ranked[0].id: ranked[0].version + 1},
            selected_item_ids=[ranked[0].id],
            resume_policy="best_resume_only",
            run_id=run.id,
        )
    with pytest.raises(ValueError, match="every candidate"):
        repo.finalize_ranked_candidates(
            {item.id: item.version for item in ranked},
            selected_item_ids=[ranked[0].id],
            resume_policy="per_resume",
            run_id=run.id,
        )

    assert all(
        repo.get_item(item.id).state is AutopilotState.RANKED for item in ranked
    )


def test_finalize_candidate_set_rolls_back_all_items_when_late_event_fails(repo) -> None:
    run, ranked = _prepare_ranked_items(repo)
    repo.conn.execute(
        """
        CREATE TRIGGER abort_not_best_event
        BEFORE INSERT ON hh_autopilot_events
        WHEN NEW.reason_code = 'not_best_resume'
        BEGIN
            SELECT RAISE(ABORT, 'not-best event rejected');
        END
        """
    )
    repo.conn.commit()
    before_events = {
        item.id: len(repo.list_events(item.id))
        for item in ranked
    }

    with pytest.raises(sqlite3.IntegrityError, match="not-best event rejected"):
        repo.finalize_ranked_candidates(
            {item.id: item.version for item in ranked},
            selected_item_ids=[ranked[0].id],
            resume_policy="best_resume_only",
            run_id=run.id,
        )

    for item in ranked:
        unchanged = repo.get_item(item.id)
        assert unchanged.state is AutopilotState.RANKED
        assert unchanged.version == item.version
        assert len(repo.list_events(item.id)) == before_events[item.id]


def test_policy_repository_methods_honor_optional_current_fence(repo) -> None:
    lease = repo.acquire_lease(
        "default",
        "owner",
        ttl_seconds=3600,
        now=LEASE_START,
    )
    run = repo.create_run(
        "default",
        trigger="manual",
        policy_hash="hash",
        fencing_token=lease.fencing_token,
    )
    item = repo.create_item(run.id, "default", "v-1", "r-1", "preset:python")

    with pytest.raises(LostLease):
        repo.record_filter_decision(
            item.id,
            expected_version=item.version,
            decision=_filter_decision(),
            run_id=run.id,
            fencing_token=lease.fencing_token + 1,
        )
    assert repo.get_item(item.id).state is AutopilotState.DISCOVERED

    changed = repo.record_filter_decision(
        item.id,
        expected_version=item.version,
        decision=_filter_decision(),
        run_id=run.id,
        fencing_token=lease.fencing_token,
    )
    assert changed.state is AutopilotState.ELIGIBLE


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("query_key", b"preset:python"),
        ("filter_json", '{"x":NaN}'),
        ("filter_json", '{"x":1}'),
        ("ai_json", "[]"),
        ("ai_json", '{"x":1}'),
        ("deterministic_score", 101.0),
        ("published_at", b"7"),
        ("last_outcome_code", b"ai_suitable"),
        ("account_profile_id", " Default "),
        ("resume_id", "R-1"),
    ],
)
def test_item_reader_rejects_malformed_persisted_policy_facts(
    repo, column: str, value: Any
) -> None:
    run = repo.create_run("default", trigger="manual", policy_hash="hash")
    item = repo.create_item(run.id, "default", "v-1", "r-1", "preset:python")
    repo.conn.execute(
        f"UPDATE hh_autopilot_items SET {column} = ? WHERE id = ?",
        (value, item.id),
    )
    repo.conn.commit()

    with pytest.raises((StaleWrite, ValueError)):
        repo.get_item(item.id)


def test_finalize_rejects_a_caller_selected_ranked_subset_atomically(repo) -> None:
    run, ranked = _prepare_ranked_items(repo)
    low = ranked[1]
    before = {
        item.id: (repo.get_item(item.id), tuple(repo.list_events(item.id)))
        for item in ranked
    }

    with pytest.raises((StaleWrite, ValueError), match="complete|candidate"):
        repo.finalize_ranked_candidates(
            {low.id: low.version},
            selected_item_ids=[low.id],
            resume_policy="best_resume_only",
            run_id=run.id,
        )

    for item in ranked:
        assert (repo.get_item(item.id), tuple(repo.list_events(item.id))) == before[
            item.id
        ]


def test_finalize_uses_only_the_complete_canonical_qualifying_subset(repo) -> None:
    run = repo.create_run("default", trigger="manual", policy_hash="hash")
    ranked: list[ItemRecord] = []
    for resume_id, decision in (
        ("ready", _ranking_decision(80)),
        ("ai-rejected", _nonqualifying_ranking_decision(99)),
    ):
        item = repo.create_item(
            run.id,
            "default",
            "v-1",
            resume_id,
            "preset:python",
        )
        eligible = repo.record_filter_decision(
            item.id,
            expected_version=item.version,
            decision=_filter_decision(),
            run_id=run.id,
        )
        ranked.append(
            repo.record_ranking_decision(
                eligible.id,
                expected_version=eligible.version,
                decision=decision,
                run_id=run.id,
            )
        )

    changed = repo.finalize_ranked_candidates(
        {ranked[0].id: ranked[0].version},
        selected_item_ids=[ranked[0].id],
        resume_policy="best_resume_only",
        run_id=run.id,
    )

    assert [item.id for item in changed] == [ranked[0].id]
    assert changed[0].state is AutopilotState.READY
    assert repo.get_item(ranked[1].id).state is AutopilotState.RANKED


def test_finalize_per_resume_cannot_omit_a_ranked_candidate(repo) -> None:
    run, ranked = _prepare_ranked_items(repo)
    first = ranked[0]

    with pytest.raises((StaleWrite, ValueError), match="complete|candidate"):
        repo.finalize_ranked_candidates(
            {first.id: first.version},
            selected_item_ids=[first.id],
            resume_policy="per_resume",
            run_id=run.id,
        )

    assert all(
        repo.get_item(item.id).state is AutopilotState.RANKED for item in ranked
    )


def test_finalize_requires_a_persisted_passing_filter_decision(repo) -> None:
    run, ranked = _prepare_ranked_items(repo)
    rejected = FilterDecision(
        False,
        "hard_filter:area",
        {"area_id": "2", "relocation_allowed": False},
    )
    repo.conn.execute(
        "UPDATE hh_autopilot_items SET filter_json = ? WHERE id = ?",
        (
            json.dumps(
                rejected.to_dict(),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
            ranked[0].id,
        ),
    )
    repo.conn.commit()

    with pytest.raises((StaleWrite, ValueError), match="filter|qualifying"):
        repo.finalize_ranked_candidates(
            {item.id: item.version for item in ranked},
            selected_item_ids=[ranked[0].id],
            resume_policy="best_resume_only",
            run_id=run.id,
        )

    stored_states = [
        repo.conn.execute(
            "SELECT state FROM hh_autopilot_items WHERE id = ?",
            (item.id,),
        ).fetchone()["state"]
        for item in ranked
    ]
    assert stored_states == [
        AutopilotState.RANKED.value
        for _ in ranked
    ]


def test_persisted_ranking_scalar_and_semantics_are_strictly_reconstructed(
    repo,
) -> None:
    run, ranked = _prepare_ranked_items(repo, resume_ids=("r-1",))
    item = ranked[0]
    repo.conn.execute(
        "UPDATE hh_autopilot_items SET deterministic_score = ? WHERE id = ?",
        (item.deterministic_score + 1, item.id),
    )
    repo.conn.commit()

    with pytest.raises(StaleWrite, match="score|ranking"):
        repo.get_item(item.id)

    repo.conn.execute(
        """
        UPDATE hh_autopilot_items
        SET deterministic_score = ?,
            ai_json = json_set(
                ai_json,
                '$.ready', json('true'),
                '$.retry', json('false'),
                '$.reason', 'ai_unsuitable',
                '$.ai_decision.suitable', json('false')
            )
        WHERE id = ?
        """,
        (item.deterministic_score, item.id),
    )
    repo.conn.commit()

    with pytest.raises(StaleWrite, match="decision|ranking|canonical"):
        repo.get_item(item.id)


def test_completed_run_rejects_every_task8_write_without_side_effects(repo) -> None:
    filter_run = repo.create_run("filter", trigger="manual", policy_hash="hash")
    discovered = repo.create_item(
        filter_run.id,
        "filter",
        "v-1",
        "r-1",
        "preset:python",
    )
    repo.finish_run(filter_run.id, status="completed")
    before_filter = (repo.get_item(discovered.id), repo.list_events(discovered.id))

    with pytest.raises(StaleWrite, match="running"):
        repo.record_filter_decision(
            discovered.id,
            expected_version=discovered.version,
            decision=_filter_decision(),
            run_id=filter_run.id,
        )
    assert (repo.get_item(discovered.id), repo.list_events(discovered.id)) == before_filter

    ranking_run = repo.create_run("ranking", trigger="manual", policy_hash="hash")
    ranking_item = repo.create_item(
        ranking_run.id,
        "ranking",
        "v-1",
        "r-1",
        "preset:python",
    )
    eligible = repo.record_filter_decision(
        ranking_item.id,
        expected_version=ranking_item.version,
        decision=_filter_decision(),
        run_id=ranking_run.id,
    )
    repo.finish_run(ranking_run.id, status="completed")
    before_ranking = (repo.get_item(eligible.id), repo.list_events(eligible.id))

    with pytest.raises(StaleWrite, match="running"):
        repo.record_ranking_decision(
            eligible.id,
            expected_version=eligible.version,
            decision=_ranking_decision(),
            run_id=ranking_run.id,
        )
    assert (repo.get_item(eligible.id), repo.list_events(eligible.id)) == before_ranking

    final_run, ranked = _prepare_ranked_items(
        repo,
        account_id="final",
    )
    repo.finish_run(final_run.id, status="completed")
    before_final = {
        item.id: (repo.get_item(item.id), repo.list_events(item.id))
        for item in ranked
    }

    with pytest.raises(StaleWrite, match="running"):
        repo.finalize_ranked_candidates(
            {item.id: item.version for item in ranked},
            selected_item_ids=[ranked[0].id],
            resume_policy="best_resume_only",
            run_id=final_run.id,
        )
    for item in ranked:
        assert (repo.get_item(item.id), repo.list_events(item.id)) == before_final[
            item.id
        ]


def test_run_fence_cannot_be_replaced_by_a_newer_lease_token(repo) -> None:
    old_lease = repo.acquire_lease(
        "default",
        "old-owner",
        ttl_seconds=1,
        now=LEASE_START,
    )
    run = repo.create_run(
        "default",
        trigger="manual",
        policy_hash="hash",
        fencing_token=old_lease.fencing_token,
    )
    item = repo.create_item(run.id, "default", "v-1", "r-1", "preset:python")
    new_lease = repo.acquire_lease(
        "default",
        "new-owner",
        ttl_seconds=3600,
        now=LEASE_START + timedelta(seconds=2),
    )
    before = (repo.get_item(item.id), repo.list_events(item.id))

    with pytest.raises(LostLease, match="run|lease|fence"):
        repo.record_filter_decision(
            item.id,
            expected_version=item.version,
            decision=_filter_decision(),
            run_id=run.id,
            fencing_token=new_lease.fencing_token,
        )

    assert (repo.get_item(item.id), repo.list_events(item.id)) == before


@pytest.mark.parametrize(
    "operation",
    ["filter", "ranking", "finalize", "transition"],
)
def test_fenced_run_rejects_omitted_token_after_lease_takeover(
    repo,
    operation: str,
) -> None:
    old_lease = repo.acquire_lease(
        "default",
        "old-owner",
        ttl_seconds=1,
        now=LEASE_START,
    )
    run = repo.create_run(
        "default",
        trigger="manual",
        policy_hash="hash",
        fencing_token=old_lease.fencing_token,
    )
    item = repo.create_item(run.id, "default", "v-1", "r-1", "preset:python")
    current = item
    if operation in {"ranking", "finalize"}:
        current = repo.record_filter_decision(
            item.id,
            expected_version=item.version,
            decision=_filter_decision(),
            run_id=run.id,
            fencing_token=old_lease.fencing_token,
        )
    if operation == "finalize":
        current = repo.record_ranking_decision(
            current.id,
            expected_version=current.version,
            decision=_ranking_decision(),
            run_id=run.id,
            fencing_token=old_lease.fencing_token,
        )
    repo.acquire_lease(
        "default",
        "new-owner",
        ttl_seconds=3600,
        now=LEASE_START + timedelta(seconds=2),
    )
    before = (repo.get_item(current.id), repo.list_events(current.id))

    with pytest.raises(LostLease):
        if operation == "filter":
            repo.record_filter_decision(
                current.id,
                expected_version=current.version,
                decision=_filter_decision(),
                run_id=run.id,
            )
        elif operation == "ranking":
            repo.record_ranking_decision(
                current.id,
                expected_version=current.version,
                decision=_ranking_decision(),
                run_id=run.id,
            )
        elif operation == "finalize":
            repo.finalize_ranked_candidates(
                {current.id: current.version},
                selected_item_ids=[current.id],
                resume_policy="best_resume_only",
                run_id=run.id,
            )
        else:
            repo.transition_item(
                current.id,
                current.version,
                AutopilotState.SKIPPED,
                "internal_error",
                run_id=run.id,
            )

    assert (repo.get_item(current.id), repo.list_events(current.id)) == before


def test_unfenced_run_cannot_invent_ownership_on_generic_transition(repo) -> None:
    lease = repo.acquire_lease(
        "default",
        "owner",
        ttl_seconds=3600,
        now=LEASE_START,
    )
    run = repo.create_run("default", trigger="manual", policy_hash="hash")
    item = repo.create_item(run.id, "default", "v-1", "r-1", "preset:python")

    with pytest.raises(LostLease):
        repo.transition_item(
            item.id,
            item.version,
            AutopilotState.SKIPPED,
            "internal_error",
            run_id=run.id,
            fencing_token=lease.fencing_token,
        )

    assert repo.get_item(item.id) == item


@pytest.mark.parametrize(
    "operation",
    ["filter", "ranking", "finalize", "transition"],
)
def test_task8_lease_expiry_is_checked_after_waiting_for_write_lock(
    repo,
    operation: str,
) -> None:
    now = datetime.now(timezone.utc)
    lease = repo.acquire_lease(
        "default",
        "owner",
        ttl_seconds=1,
        now=now,
    )
    run = repo.create_run(
        "default",
        trigger="manual",
        policy_hash="hash",
        fencing_token=lease.fencing_token,
    )
    item = repo.create_item(run.id, "default", "v-1", "r-1", "preset:python")
    current = item
    if operation in {"ranking", "finalize"}:
        current = repo.record_filter_decision(
            item.id,
            expected_version=item.version,
            decision=_filter_decision(),
            run_id=run.id,
            fencing_token=lease.fencing_token,
        )
    if operation == "finalize":
        current = repo.record_ranking_decision(
            current.id,
            expected_version=current.version,
            decision=_ranking_decision(),
            run_id=run.id,
            fencing_token=lease.fencing_token,
        )
    before = (repo.get_item(current.id), repo.list_events(current.id))
    locked = Event()

    def hold_write_lock() -> None:
        connection = sqlite3.connect(repo.storage.path)
        try:
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute("BEGIN IMMEDIATE")
            locked.set()
            time.sleep(1.25)
            connection.commit()
        finally:
            connection.close()

    holder = Thread(target=hold_write_lock)
    holder.start()
    assert locked.wait(timeout=2)
    try:
        with pytest.raises(LostLease):
            if operation == "filter":
                repo.record_filter_decision(
                    current.id,
                    expected_version=current.version,
                    decision=_filter_decision(),
                    run_id=run.id,
                    fencing_token=lease.fencing_token,
                )
            elif operation == "ranking":
                repo.record_ranking_decision(
                    current.id,
                    expected_version=current.version,
                    decision=_ranking_decision(),
                    run_id=run.id,
                    fencing_token=lease.fencing_token,
                )
            elif operation == "finalize":
                repo.finalize_ranked_candidates(
                    {current.id: current.version},
                    selected_item_ids=[current.id],
                    resume_policy="best_resume_only",
                    run_id=run.id,
                    fencing_token=lease.fencing_token,
                )
            else:
                repo.transition_item(
                    current.id,
                    current.version,
                    AutopilotState.SKIPPED,
                    "internal_error",
                    run_id=run.id,
                    fencing_token=lease.fencing_token,
                )
    finally:
        holder.join(timeout=3)

    assert not holder.is_alive()
    assert (repo.get_item(current.id), repo.list_events(current.id)) == before


def test_generic_transition_rejects_task8_stage_owned_edges(repo) -> None:
    run = repo.create_run("default", trigger="manual", policy_hash="hash")
    discovered = repo.create_item(
        run.id,
        "default",
        "v-discovered",
        "r-1",
        "preset:python",
    )
    with pytest.raises((ValueError, StaleWrite), match="stage-owned"):
        repo.transition_item(
            discovered.id,
            discovered.version,
            AutopilotState.ELIGIBLE,
            "hard_filters_passed",
            run_id=run.id,
        )

    eligible_item = repo.create_item(
        run.id,
        "default",
        "v-eligible",
        "r-1",
        "preset:python",
    )
    eligible = repo.record_filter_decision(
        eligible_item.id,
        expected_version=eligible_item.version,
        decision=_filter_decision(),
        run_id=run.id,
    )
    with pytest.raises((ValueError, StaleWrite), match="stage-owned"):
        repo.transition_item(
            eligible.id,
            eligible.version,
            AutopilotState.RANKED,
            "deterministic_score",
            run_id=run.id,
        )

    ranked_item = repo.create_item(
        run.id,
        "default",
        "v-ranked",
        "r-1",
        "preset:python",
    )
    ranked_eligible = repo.record_filter_decision(
        ranked_item.id,
        expected_version=ranked_item.version,
        decision=_filter_decision(),
        run_id=run.id,
    )
    ranked = repo.record_ranking_decision(
        ranked_eligible.id,
        expected_version=ranked_eligible.version,
        decision=_ranking_decision(),
        run_id=run.id,
    )
    with pytest.raises((ValueError, StaleWrite), match="stage-owned"):
        repo.transition_item(
            ranked.id,
            ranked.version,
            AutopilotState.READY,
            "ready",
            run_id=run.id,
        )

    assert repo.get_item(discovered.id) == discovered
    assert repo.get_item(eligible.id) == eligible
    assert repo.get_item(ranked.id) == ranked


def test_completed_run_cannot_generic_transition_lower_ranked_resume_ready(
    repo,
) -> None:
    run = repo.create_run("default", trigger="manual", policy_hash="hash")
    ranked: list[ItemRecord] = []
    for resume_id, score in (("r-high", 99.0), ("r-low", 1.0)):
        item = repo.create_item(
            run.id,
            "default",
            "v-1",
            resume_id,
            "preset:python",
        )
        eligible = repo.record_filter_decision(
            item.id,
            expected_version=item.version,
            decision=_filter_decision(),
            run_id=run.id,
        )
        ranked.append(
            repo.record_ranking_decision(
                eligible.id,
                expected_version=eligible.version,
                decision=_ranking_decision(score),
                run_id=run.id,
            )
        )
    repo.finish_run(run.id, status="completed")
    before = {
        item.id: (repo.get_item(item.id), repo.list_events(item.id))
        for item in ranked
    }

    with pytest.raises((ValueError, StaleWrite, LostLease), match="stage-owned|running"):
        repo.transition_item(
            ranked[1].id,
            ranked[1].version,
            AutopilotState.READY,
            "ready",
            run_id=run.id,
        )

    for item in ranked:
        assert (repo.get_item(item.id), repo.list_events(item.id)) == before[item.id]


def test_strict_item_reader_requires_state_owned_filter_and_ranking_evidence(
    repo,
) -> None:
    run = repo.create_run("default", trigger="manual", policy_hash="hash")
    eligible_item = repo.create_item(
        run.id,
        "default",
        "v-eligible",
        "r-1",
        "preset:python",
    )
    eligible = repo.record_filter_decision(
        eligible_item.id,
        expected_version=eligible_item.version,
        decision=_filter_decision(),
        run_id=run.id,
    )
    repo.conn.execute(
        "UPDATE hh_autopilot_items SET filter_json = '{}' WHERE id = ?",
        (eligible.id,),
    )
    repo.conn.commit()
    with pytest.raises(StaleWrite, match="filter"):
        repo.get_item(eligible.id)

    ranked_item = repo.create_item(
        run.id,
        "default",
        "v-ranked",
        "r-1",
        "preset:python",
    )
    ranked_eligible = repo.record_filter_decision(
        ranked_item.id,
        expected_version=ranked_item.version,
        decision=_filter_decision(),
        run_id=run.id,
    )
    ranked = repo.record_ranking_decision(
        ranked_eligible.id,
        expected_version=ranked_eligible.version,
        decision=_ranking_decision(),
        run_id=run.id,
    )
    repo.conn.execute(
        "UPDATE hh_autopilot_items SET filter_json = '{}' WHERE id = ?",
        (ranked.id,),
    )
    repo.conn.commit()
    with pytest.raises(StaleWrite, match="filter"):
        repo.get_item(ranked.id)


def test_strict_ready_reader_rejects_canonical_nonready_ranking(repo) -> None:
    run = repo.create_run("default", trigger="manual", policy_hash="hash")
    item = repo.create_item(run.id, "default", "v-1", "r-1", "preset:python")
    eligible = repo.record_filter_decision(
        item.id,
        expected_version=item.version,
        decision=_filter_decision(),
        run_id=run.id,
    )
    ranked = repo.record_ranking_decision(
        eligible.id,
        expected_version=eligible.version,
        decision=_nonqualifying_ranking_decision(),
        run_id=run.id,
    )
    repo.conn.execute(
        "UPDATE hh_autopilot_items SET state = 'ready', last_outcome_code = 'ready' "
        "WHERE id = ?",
        (ranked.id,),
    )
    repo.conn.commit()

    with pytest.raises(StaleWrite, match="ready|ranking"):
        repo.get_item(ranked.id)


def test_signed_zero_ranking_round_trips_canonically_through_sqlite(repo) -> None:
    run = repo.create_run("default", trigger="manual", policy_hash="hash")
    item = repo.create_item(run.id, "default", "v-1", "r-1", "preset:python")
    eligible = repo.record_filter_decision(
        item.id,
        expected_version=item.version,
        decision=_filter_decision(),
        run_id=run.id,
    )
    rank_score = RankScore(
        score=-0.0,
        components={name: -0.0 for name in RANK_COMPONENTS},
        weights={
            name: (1.0 if name == "role" else -0.0)
            for name in RANK_COMPONENTS
        },
    )
    decision = RankingDecision(
        ready=True,
        retry=False,
        reason="ai_suitable",
        rank_score=rank_score,
        ai_decision=AIDecision(
            available=True,
            suitable=True,
            confidence=0.9,
            evidence=("python",),
            reasons=(),
        ),
    )

    ranked = repo.record_ranking_decision(
        eligible.id,
        expected_version=eligible.version,
        decision=decision,
        run_id=run.id,
    )
    row = repo.conn.execute(
        "SELECT deterministic_score, ai_json FROM hh_autopilot_items WHERE id = ?",
        (ranked.id,),
    ).fetchone()

    assert math.copysign(1.0, ranked.deterministic_score) == 1.0
    assert math.copysign(1.0, float(row["deterministic_score"])) == 1.0
    assert "-0.0" not in row["ai_json"]


def test_generic_transition_omission_cannot_borrow_a_newer_lease(repo) -> None:
    old_lease = repo.acquire_lease(
        "default",
        "old-owner",
        ttl_seconds=1,
        now=LEASE_START,
    )
    run = repo.create_run(
        "default",
        trigger="manual",
        policy_hash="hash",
        fencing_token=old_lease.fencing_token,
    )
    item = repo.create_item(run.id, "default", "v-1", "r-1", "preset:python")
    new_lease = repo.acquire_lease(
        "default",
        "new-owner",
        ttl_seconds=3600,
        now=LEASE_START + timedelta(seconds=2),
    )
    assert new_lease is not None
    before = (repo.get_item(item.id), repo.list_events(item.id))

    with pytest.raises(LostLease):
        repo.transition_item(
            item.id,
            item.version,
            AutopilotState.SKIPPED,
            "internal_error",
            fencing_token=new_lease.fencing_token,
        )

    assert (repo.get_item(item.id), repo.list_events(item.id)) == before


def test_generic_transition_omission_rejects_a_completed_effective_run(
    repo,
) -> None:
    run = repo.create_run("default", trigger="manual", policy_hash="hash")
    item = repo.create_item(run.id, "default", "v-1", "r-1", "preset:python")
    repo.finish_run(run.id, status="completed")
    before = (repo.get_item(item.id), repo.list_events(item.id))

    with pytest.raises(StaleWrite, match="running"):
        repo.transition_item(
            item.id,
            item.version,
            AutopilotState.SKIPPED,
            "internal_error",
        )

    assert (repo.get_item(item.id), repo.list_events(item.id)) == before


def test_generic_transition_persists_the_effective_unfenced_manual_run(
    repo,
) -> None:
    run = repo.create_run("default", trigger="manual", policy_hash="hash")
    item = repo.create_item(run.id, "default", "v-1", "r-1", "preset:python")

    changed = repo.transition_item(
        item.id,
        item.version,
        AutopilotState.SKIPPED,
        "internal_error",
    )

    assert changed.last_run_id == run.id
    assert repo.list_events(item.id)[-1]["run_id"] == run.id


@pytest.mark.parametrize(
    "trigger",
    ["schedule", "retry", "recovery", "canary"],
)
def test_generic_transition_rejects_unfenced_live_runs(
    repo,
    trigger: str,
) -> None:
    run = repo.create_run("default", trigger=trigger, policy_hash="hash")
    item = repo.create_item(run.id, "default", "v-1", "r-1", "preset:python")
    before = (repo.get_item(item.id), repo.list_events(item.id))

    with pytest.raises(LostLease, match="fence"):
        repo.transition_item(
            item.id,
            item.version,
            AutopilotState.SKIPPED,
            "internal_error",
        )

    assert (repo.get_item(item.id), repo.list_events(item.id)) == before


def test_sealed_set_rejects_late_ranking_and_preserves_the_winner(repo) -> None:
    run = repo.create_run("default", trigger="manual", policy_hash="hash")
    low_item = repo.create_item(
        run.id,
        "default",
        "v-1",
        "r-low",
        "preset:python",
    )
    low_eligible = repo.record_filter_decision(
        low_item.id,
        expected_version=low_item.version,
        decision=_filter_decision(),
        run_id=run.id,
    )
    low_ranked = repo.record_ranking_decision(
        low_eligible.id,
        expected_version=low_eligible.version,
        decision=_ranking_decision(1),
        run_id=run.id,
    )
    ready = repo.finalize_ranked_candidates(
        {low_ranked.id: low_ranked.version},
        selected_item_ids=[low_ranked.id],
        resume_policy="best_resume_only",
        run_id=run.id,
    )[0]

    late = repo.create_item(
        run.id,
        "default",
        "v-1",
        "r-high",
        "preset:python",
    )
    repo.conn.execute(
        """
        UPDATE hh_autopilot_items
        SET state = 'eligible', version = 1, filter_json = ?,
            last_outcome_code = 'hard_filters_passed'
        WHERE id = ?
        """,
        (_canonical_filter_json(), late.id),
    )
    repo.conn.commit()
    late_eligible = repo.get_item(late.id)
    before_events = repo.list_events(late.id)

    with pytest.raises(StaleWrite, match="sealed"):
        repo.record_ranking_decision(
            late_eligible.id,
            expected_version=late_eligible.version,
            decision=_ranking_decision(99),
            run_id=run.id,
        )

    assert repo.get_item(ready.id).state is AutopilotState.READY
    assert repo.get_item(ready.id).deterministic_score == 1
    assert repo.get_item(late.id).state is AutopilotState.ELIGIBLE
    assert repo.list_events(late.id) == before_events


def test_sealed_set_blocks_late_filter_after_ready_has_moved_on(repo) -> None:
    run, ranked = _prepare_ranked_items(repo, resume_ids=("r-1",))
    ready = repo.finalize_ranked_candidates(
        {ranked[0].id: ranked[0].version},
        selected_item_ids=[ranked[0].id],
        resume_policy="best_resume_only",
        run_id=run.id,
    )[0]
    applying = repo.transition_item(
        ready.id,
        ready.version,
        AutopilotState.APPLYING,
        "internal_error",
        run_id=run.id,
    )
    later_run = repo.create_run(
        "default",
        trigger="manual",
        policy_hash="later",
    )
    repo.conn.execute(
        "UPDATE hh_autopilot_items SET last_run_id = ? WHERE id = ?",
        (later_run.id, applying.id),
    )
    repo.conn.commit()
    late = repo.create_item(
        run.id,
        "default",
        "v-1",
        "r-late",
        "preset:python",
    )
    before = (repo.get_item(late.id), repo.list_events(late.id))

    with pytest.raises(StaleWrite, match="sealed"):
        repo.record_filter_decision(
            late.id,
            expected_version=late.version,
            decision=_filter_decision(),
            run_id=run.id,
        )

    assert repo.get_item(applying.id).state is AutopilotState.APPLYING
    assert repo.get_item(applying.id).last_run_id == later_run.id
    assert (repo.get_item(late.id), repo.list_events(late.id)) == before


@pytest.mark.parametrize(
    "pending_state",
    ["discovered", "eligible", "retry_wait"],
)
def test_incomplete_candidate_set_blocks_first_finalization(
    repo,
    pending_state: str,
) -> None:
    run, ranked = _prepare_ranked_items(repo, resume_ids=("r-ready",))
    pending = repo.create_item(
        run.id,
        "default",
        "v-1",
        "r-pending",
        "preset:python",
    )
    if pending_state in {"eligible", "retry_wait"}:
        pending = repo.record_filter_decision(
            pending.id,
            expected_version=pending.version,
            decision=_filter_decision(),
            run_id=run.id,
        )
    if pending_state == "retry_wait":
        pending = repo.transition_item(
            pending.id,
            pending.version,
            AutopilotState.RETRY_WAIT,
            "internal_error",
            run_id=run.id,
        )
    before = {
        item.id: (repo.get_item(item.id), repo.list_events(item.id))
        for item in (ranked[0], pending)
    }

    with pytest.raises(StaleWrite, match="incomplete"):
        repo.finalize_ranked_candidates(
            {ranked[0].id: ranked[0].version},
            selected_item_ids=[ranked[0].id],
            resume_policy="best_resume_only",
            run_id=run.id,
        )

    for item in (ranked[0], pending):
        assert (repo.get_item(item.id), repo.list_events(item.id)) == before[item.id]


def test_retryable_ranked_candidate_blocks_finalization(repo) -> None:
    run, ranked = _prepare_ranked_items(repo, resume_ids=("r-ready",))
    retry_item = repo.create_item(
        run.id,
        "default",
        "v-1",
        "r-retry",
        "preset:python",
    )
    retry_eligible = repo.record_filter_decision(
        retry_item.id,
        expected_version=retry_item.version,
        decision=_filter_decision(),
        run_id=run.id,
    )
    retry_ranked = repo.record_ranking_decision(
        retry_eligible.id,
        expected_version=retry_eligible.version,
        decision=_retryable_ranking_decision(),
        run_id=run.id,
    )

    with pytest.raises(StaleWrite, match="incomplete|retry"):
        repo.finalize_ranked_candidates(
            {ranked[0].id: ranked[0].version},
            selected_item_ids=[ranked[0].id],
            resume_policy="best_resume_only",
            run_id=run.id,
        )

    assert repo.get_item(ranked[0].id).state is AutopilotState.RANKED
    assert repo.get_item(retry_ranked.id).state is AutopilotState.RANKED


def test_hard_filter_terminal_skip_does_not_block_finalization(repo) -> None:
    run, ranked = _prepare_ranked_items(repo, resume_ids=("r-ready",))
    rejected = repo.create_item(
        run.id,
        "default",
        "v-1",
        "r-rejected",
        "preset:python",
    )
    rejected = repo.record_filter_decision(
        rejected.id,
        expected_version=rejected.version,
        decision=_filter_decision(passed=False),
        run_id=run.id,
    )

    changed = repo.finalize_ranked_candidates(
        {ranked[0].id: ranked[0].version},
        selected_item_ids=[ranked[0].id],
        resume_policy="best_resume_only",
        run_id=run.id,
    )

    assert changed[0].state is AutopilotState.READY
    assert repo.get_item(rejected.id).state is AutopilotState.SKIPPED


def test_repeated_finalization_rejects_the_append_only_seal(repo) -> None:
    run, ranked = _prepare_ranked_items(repo)
    changed = repo.finalize_ranked_candidates(
        {item.id: item.version for item in ranked},
        selected_item_ids=[ranked[0].id],
        resume_policy="best_resume_only",
        run_id=run.id,
    )
    ready = next(item for item in changed if item.state is AutopilotState.READY)
    before = {
        item.id: (repo.get_item(item.id), repo.list_events(item.id))
        for item in changed
    }

    with pytest.raises(StaleWrite, match="sealed"):
        repo.finalize_ranked_candidates(
            {item.id: item.version for item in changed},
            selected_item_ids=[ready.id],
            resume_policy="best_resume_only",
            run_id=run.id,
        )

    for item in changed:
        assert (repo.get_item(item.id), repo.list_events(item.id)) == before[item.id]


def test_per_resume_seal_rejects_late_candidates(repo) -> None:
    run, ranked = _prepare_ranked_items(repo)
    repo.finalize_ranked_candidates(
        {item.id: item.version for item in ranked},
        selected_item_ids=[item.id for item in ranked],
        resume_policy="per_resume",
        run_id=run.id,
    )
    late = repo.create_item(
        run.id,
        "default",
        "v-1",
        "r-late",
        "preset:python",
    )

    with pytest.raises(StaleWrite, match="sealed"):
        repo.record_filter_decision(
            late.id,
            expected_version=late.version,
            decision=_filter_decision(),
            run_id=run.id,
        )

    assert repo.get_item(late.id).state is AutopilotState.DISCOVERED
    assert repo.count_guards() == 0
    assert repo.count_application_attempts() == 0
    assert repo.count_reservations() == 0
    assert repo.count_challenges() == 0


def _paused_finalization_worker(
    database_path: Any,
    entered: Event,
    release: Event,
    *,
    run_id: int,
    item_id: int,
    version: int,
) -> str:
    storage = Storage(database_path)
    worker = AutopilotRepository(storage)

    def pause_candidate_seal() -> int:
        entered.set()
        if not release.wait(timeout=5):
            raise TimeoutError("race release was not signaled")
        return 0

    worker.conn.create_function(
        "pause_candidate_seal",
        0,
        pause_candidate_seal,
    )
    try:
        worker.finalize_ranked_candidates(
            {item_id: version},
            selected_item_ids=[item_id],
            resume_policy="best_resume_only",
            run_id=run_id,
        )
        return "sealed"
    finally:
        storage.close()


def _late_ranking_worker(
    database_path: Any,
    *,
    run_id: int,
    item_id: int,
    target_vacancy_id: str,
) -> str:
    storage = Storage(database_path)
    worker = AutopilotRepository(storage)
    try:
        worker.conn.create_function("pause_candidate_seal", 0, lambda: 0)
        worker.conn.execute(
            "UPDATE hh_autopilot_items SET vacancy_id = ? WHERE id = ?",
            (target_vacancy_id, item_id),
        )
        worker.conn.commit()
        current = worker.get_item(item_id)
        try:
            worker.record_ranking_decision(
                current.id,
                expected_version=current.version,
                decision=_ranking_decision(99),
                run_id=run_id,
            )
        except StaleWrite as exc:
            return str(exc)
        return "ranked"
    finally:
        storage.close()


def test_finalization_and_late_ranking_race_produces_one_durable_seal(
    repo,
) -> None:
    run, ranked = _prepare_ranked_items(repo, resume_ids=("r-low",))
    late = repo.create_item(
        run.id,
        "default",
        "v-hidden",
        "r-high",
        "preset:python",
    )
    late = repo.record_filter_decision(
        late.id,
        expected_version=late.version,
        decision=_filter_decision(),
        run_id=run.id,
    )
    late_event_count = len(repo.list_events(late.id))
    repo.conn.execute(
        """
        CREATE TRIGGER pause_ready_seal_event
        BEFORE INSERT ON hh_autopilot_events
        WHEN NEW.reason_code = 'ready'
        BEGIN
            SELECT pause_candidate_seal();
        END
        """
    )
    repo.conn.commit()
    entered = Event()
    release = Event()

    with ThreadPoolExecutor(max_workers=2) as pool:
        finalizing = pool.submit(
            _paused_finalization_worker,
            repo.storage.path,
            entered,
            release,
            run_id=run.id,
            item_id=ranked[0].id,
            version=ranked[0].version,
        )
        assert entered.wait(timeout=3)
        late_ranking = pool.submit(
            _late_ranking_worker,
            repo.storage.path,
            run_id=run.id,
            item_id=late.id,
            target_vacancy_id="v-1",
        )
        time.sleep(0.1)
        release.set()
        final_outcome = finalizing.result(timeout=5)
        ranking_outcome = late_ranking.result(timeout=5)

    assert final_outcome == "sealed"
    assert "sealed" in ranking_outcome
    assert repo.get_item(ranked[0].id).state is AutopilotState.READY
    assert repo.get_item(late.id).state is AutopilotState.ELIGIBLE
    assert len(repo.list_events(late.id)) == late_event_count
    seal_events = [
        event
        for event in repo.list_events(ranked[0].id)
        if event["previous_state"] == "ranked"
        and event["next_state"] == "ready"
        and event["reason_code"] == "ready"
    ]
    assert len(seal_events) == 1
    assert repo.count_guards() == 0
    assert repo.count_application_attempts() == 0
    assert repo.count_reservations() == 0
    assert repo.count_challenges() == 0
