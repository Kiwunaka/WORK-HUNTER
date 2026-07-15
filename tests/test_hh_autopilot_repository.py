from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone

import pytest

from work_hunter.hh_autopilot.repository import (
    AutopilotRepository,
    ChallengeRecord,
    ItemRecord,
    LeaseRecord,
    LostLease,
    RunRecord,
    StaleWrite,
)
from work_hunter.hh_autopilot.types import AutopilotState
from work_hunter.storage import Storage


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
            for row in connection.execute(f"PRAGMA foreign_key_list({table})").fetchall()
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

    assert repo.conn.execute("SELECT COUNT(*) FROM hh_autopilot_items").fetchone()[0] == 0


def test_transition_rolls_back_when_event_insert_fails(repo) -> None:
    run = repo.create_run("default", trigger="manual", policy_hash="hash")
    item = repo.create_item(run.id, "default", "v-1", "r-1", "preset:python")
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
            item.id,
            item.version,
            AutopilotState.ELIGIBLE,
            "reject-me",
        )

    assert repo.get_item(item.id) == item
    assert [event["reason_code"] for event in repo.list_events(item.id)] == [
        "discovered"
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
    assert event["metadata_json"] == {
        "nested": {"access_token": "***", "value": 3}
    }
    event["metadata_json"]["nested"]["value"] = 99
    assert repo.list_events(item.id)[-1]["metadata_json"]["nested"]["value"] == 3


def test_fencing_token_must_match_live_account_lease(repo) -> None:
    run = repo.create_run("default", trigger="manual", policy_hash="hash")
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
            AutopilotState.ELIGIBLE,
            "passed",
            fencing_token=6,
        )

    assert repo.get_item(item.id) == item
    changed = repo.transition_item(
        item.id,
        item.version,
        AutopilotState.ELIGIBLE,
        "passed",
        fencing_token=7,
    )
    assert changed.state is AutopilotState.ELIGIBLE


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
    updates = [
        (AutopilotState.RETRY_WAIT.value, "2026-01-02T00:00:00+00:00", items[0].id),
        (AutopilotState.ELIGIBLE.value, "", items[1].id),
        (AutopilotState.ELIGIBLE.value, "2026-01-03T00:00:00+00:00", items[2].id),
        (AutopilotState.ELIGIBLE.value, "", other.id),
    ]
    repo.conn.executemany(
        "UPDATE hh_autopilot_items SET state = ?, next_attempt_at = ? WHERE id = ?",
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
    assert repo.conn.execute("SELECT COUNT(*) FROM hh_autopilot_items").fetchone()[0] == 0


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
        SET state = ?, next_attempt_at = ?
        WHERE id = ?
        """,
        (
            AutopilotState.ELIGIBLE.value,
            "2026-01-01T22:30:00+00:00",
            item.id,
        ),
    )
    repo.conn.commit()

    assert repo.list_due_items(
        "default",
        states=[AutopilotState.ELIGIBLE],
        now="2026-01-02T00:00:00+02:00",
        limit=10,
    ) == []
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
