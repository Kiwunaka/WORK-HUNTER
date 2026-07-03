from __future__ import annotations

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

