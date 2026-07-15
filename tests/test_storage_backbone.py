from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from work_hunter.storage import Storage


def _columns(storage: Storage, table: str) -> set[str]:
    rows = storage.conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row["name"] for row in rows}


def test_backbone_tables_and_columns_exist(tmp_path):
    storage = Storage(tmp_path / "work_hunter.sqlite3")

    tables = {
        row["name"]
        for row in storage.conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }

    for table in {
        "schema_migrations",
        "jobs",
        "job_scores",
        "applications",
        "apply_plans",
        "hh_auth_profiles",
        "source_runs",
        "operation_logs",
        "agent_decisions",
        "approvals",
        "search_presets",
    }:
        assert table in tables

    assert {"canonical_key", "apply_url", "company_id", "snippet", "raw_json"} <= _columns(storage, "jobs")
    assert {"seniority_score", "company_score", "ats_score", "created_at"} <= _columns(storage, "job_scores")
    assert {
        "source",
        "source_id",
        "resume_id",
        "resume_hash",
        "plan_id",
        "transport",
        "sent_at",
        "result_json",
        "error",
    } <= _columns(storage, "applications")


def test_migrations_are_idempotent(tmp_path):
    db_path = tmp_path / "work_hunter.sqlite3"
    first = Storage(db_path)
    first.conn.execute("INSERT INTO jobs (source, source_id, url, title) VALUES ('x', '1', 'u', 't')")
    first.conn.commit()
    first.close()

    second = Storage(db_path)
    count = second.conn.execute("SELECT COUNT(*) AS count FROM jobs").fetchone()["count"]
    applied = second.conn.execute("SELECT COUNT(*) AS count FROM schema_migrations").fetchone()["count"]

    assert count == 1
    assert applied >= 1


def test_storage_enables_foreign_keys_and_busy_timeout(tmp_path):
    storage = Storage(tmp_path / "db.sqlite3")

    assert storage.conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert storage.conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    with pytest.raises(sqlite3.IntegrityError):
        storage.save_application(999, "applied")


def test_concurrent_storage_initialization_is_serialized(tmp_path):
    path = tmp_path / "db.sqlite3"
    barrier = threading.Barrier(8)

    def construct(_: int) -> None:
        barrier.wait()
        storage = Storage(path)
        storage.close()

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(construct, index) for index in range(8)]
        for future in futures:
            future.result()

    storage = Storage(path)
    assert storage.conn.execute(
        "SELECT COUNT(*) FROM schema_migrations"
    ).fetchone()[0] == len(list(storage.migrations_dir.glob("*.sql")))


def test_failed_migration_rolls_back_schema_and_version(tmp_path):
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "0001_valid.sql").write_text(
        "CREATE TABLE migration_before_failure(id INTEGER);",
        encoding="utf-8",
    )
    (migrations / "9999_broken.sql").write_text(
        "CREATE TABLE partial_schema(id INTEGER); THIS IS INVALID;",
        encoding="utf-8",
    )
    path = tmp_path / "db.sqlite3"

    with pytest.raises(sqlite3.Error):
        Storage(path, migrations_dir=migrations)

    conn = sqlite3.connect(path)
    names = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert "jobs" not in names
    assert "schema_migrations" not in names
    assert "migration_before_failure" not in names
    assert "partial_schema" not in names


def test_failed_migration_closes_constructor_connection(tmp_path):
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "9999_broken.sql").write_text("THIS IS INVALID;", encoding="utf-8")
    storage = Storage.__new__(Storage)

    with pytest.raises(sqlite3.Error):
        Storage.__init__(storage, tmp_path / "db.sqlite3", migrations_dir=migrations)

    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        storage.conn.execute("SELECT 1")


@pytest.mark.parametrize(
    ("script", "verification_sql", "expected"),
    [
        pytest.param(
            "CREATE TABLE custom(value TEXT); -- valid footer;",
            "SELECT value FROM custom",
            [],
            id="trailing-line-comment",
        ),
        pytest.param(
            "CREATE TABLE custom(value TEXT); /* valid footer; */",
            "SELECT value FROM custom",
            [],
            id="trailing-block-comment",
        ),
        pytest.param(
            "-- comment-only migration;",
            None,
            [],
            id="line-comment-only",
        ),
        pytest.param(
            "/* comment-only migration; */",
            None,
            [],
            id="block-comment-only",
        ),
        pytest.param(
            """
            CREATE TABLE custom(value TEXT);
            INSERT INTO custom(value) VALUES ('inside;string');
            """,
            "SELECT value FROM custom",
            ["inside;string"],
            id="semicolon-in-string",
        ),
        pytest.param(
            """
            CREATE TABLE custom(value TEXT);
            CREATE TRIGGER add_second AFTER INSERT ON custom
            WHEN NEW.value = 'first;value'
            BEGIN
                INSERT INTO custom(value) VALUES ('second');
            END;
            INSERT INTO custom(value) VALUES ('first;value');
            """,
            "SELECT value FROM custom ORDER BY rowid",
            ["first;value", "second"],
            id="trigger-with-inner-statements",
        ),
    ],
)
def test_valid_migration_script_is_applied_and_versioned(
    tmp_path,
    script: str,
    verification_sql: str | None,
    expected: list[str],
):
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    version = "0001_custom.sql"
    (migrations / version).write_text(script, encoding="utf-8")

    storage = Storage(tmp_path / "db.sqlite3", migrations_dir=migrations)

    applied = storage.conn.execute(
        "SELECT version FROM schema_migrations"
    ).fetchall()
    assert [row["version"] for row in applied] == [version]
    if verification_sql is not None:
        rows = storage.conn.execute(verification_sql).fetchall()
        assert [row[0] for row in rows] == expected


@pytest.mark.parametrize(
    "script",
    [
        pytest.param("CREATE TABLE incomplete(", id="incomplete-statement"),
        pytest.param(
            "CREATE TABLE missing_terminator(id INTEGER)",
            id="missing-semicolon",
        ),
        pytest.param("THIS IS INVALID;", id="invalid-statement"),
        pytest.param("/* unterminated comment", id="unterminated-comment"),
    ],
)
def test_incomplete_or_invalid_migration_rolls_back(tmp_path, script: str):
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "0001_invalid.sql").write_text(script, encoding="utf-8")
    path = tmp_path / "db.sqlite3"

    with pytest.raises(sqlite3.Error):
        Storage(path, migrations_dir=migrations)

    conn = sqlite3.connect(path)
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    assert tables == set()
