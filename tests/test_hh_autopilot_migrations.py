from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from work_hunter.config import config_path, database_path, default_config, save_config
from work_hunter.models import Application, Job
from work_hunter.services import WorkHunter
from work_hunter.storage import Storage


LEGACY_APPLICATION_COLUMNS = {
    "source": "hh",
    "source_id": "v1",
    "resume_id": "resume-1",
    "resume_hash": "resume-hash-1",
    "plan_id": 17,
    "transport": "api",
    "sent_at": "2026-07-01T10:00:00+00:00",
    "result_json": '{"status":"created"}',
    "error": "",
}


def _create_legacy_database(
    path: Path,
    *,
    compatibility_columns: bool = True,
    application_count: int = 1,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    compatibility_sql = ""
    compatibility_names = ""
    compatibility_values = ""
    if compatibility_columns:
        compatibility_sql = """
            source TEXT NOT NULL DEFAULT '',
            source_id TEXT NOT NULL DEFAULT '',
            resume_id TEXT NOT NULL DEFAULT '',
            resume_hash TEXT NOT NULL DEFAULT '',
            plan_id INTEGER,
            transport TEXT NOT NULL DEFAULT '',
            sent_at TEXT NOT NULL DEFAULT '',
            result_json TEXT NOT NULL DEFAULT '{}',
            error TEXT NOT NULL DEFAULT '',
        """
        compatibility_names = (
            ", source, source_id, resume_id, resume_hash, plan_id, transport, "
            "sent_at, result_json, error"
        )
        compatibility_values = (
            ", 'hh', ?, ?, 'resume-hash-1', 17, 'api', "
            "'2026-07-01T10:00:00+00:00', '{\"status\":\"created\"}', ''"
        )
    connection = sqlite3.connect(path)
    connection.executescript(
        f"""
        CREATE TABLE jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            source_id TEXT NOT NULL,
            url TEXT NOT NULL,
            title TEXT NOT NULL,
            company TEXT NOT NULL DEFAULT '',
            salary_text TEXT NOT NULL DEFAULT '',
            salary_from INTEGER,
            salary_to INTEGER,
            currency TEXT NOT NULL DEFAULT '',
            location TEXT NOT NULL DEFAULT '',
            remote INTEGER,
            description TEXT NOT NULL DEFAULT '',
            published_at TEXT NOT NULL DEFAULT '',
            fetched_at TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'new',
            UNIQUE(source, source_id)
        );
        CREATE TABLE applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'applied',
            notes TEXT NOT NULL DEFAULT '',
            applied_at TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT '',
            {compatibility_sql}
            FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
        );
        """
    )
    for index in range(1, application_count + 1):
        source_id = f"v{index}"
        connection.execute(
            "INSERT INTO jobs (source, source_id, url, title) VALUES ('hh', ?, ?, ?)",
            (source_id, f"https://hh.ru/vacancy/{source_id}", f"Python {index}"),
        )
        connection.execute(
            f"""
            INSERT INTO applications (
                job_id, status, notes, applied_at, updated_at{compatibility_names}
            ) VALUES (
                ?, 'applied', ?, '2026-07-01T09:00:00+00:00',
                '2026-07-01T09:00:00+00:00'{compatibility_values}
            )
            """,
            (
                index,
                f"legacy note {index}",
                *(
                    (source_id, f"resume-{index}")
                    if compatibility_columns
                    else ()
                ),
            ),
        )
    connection.commit()
    connection.close()


@pytest.fixture
def legacy_db(tmp_path: Path) -> Path:
    path = database_path(tmp_path)
    _create_legacy_database(path)
    return path


@pytest.fixture
def app_factory() -> Callable[..., WorkHunter]:
    def build(
        db: Path,
        *,
        hh_account_profiles: dict[str, dict[str, Any]] | None = None,
        legacy_hh_identity: dict[str, Any] | None = None,
        active_account: str = "default",
    ) -> WorkHunter:
        root = db.parent.parent
        config = default_config()
        config["hh_account_profile"] = active_account
        if hh_account_profiles is not None:
            config["hh_account_profiles"] = hh_account_profiles
        if legacy_hh_identity:
            config["sources"]["hh"].update(legacy_hh_identity)
        save_config(config_path(root), config)
        return WorkHunter(root)

    return build


def _job(storage: Storage, source_id: str = "v1") -> int:
    return storage.upsert_job(
        Job(
            source="hh",
            source_id=source_id,
            url=f"https://hh.ru/vacancy/{source_id}",
            title="Python",
        )
    )


def test_application_history_is_account_aware(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite3")
    job_id = _job(storage)

    storage.save_application(
        job_id,
        "applied",
        account_profile_id="a",
        resume_id="r1",
    )
    storage.save_application(
        job_id,
        "applied",
        account_profile_id="b",
        resume_id="r1",
    )

    rows = storage.list_applications()
    assert {(row.account_profile_id, row.resume_id) for row in rows} == {
        ("a", "r1"),
        ("b", "r1"),
    }


def test_save_application_defaults_to_legacy_and_upserts_only_exact_identity(
    tmp_path: Path,
) -> None:
    storage = Storage(tmp_path / "db.sqlite3")
    job_id = _job(storage)
    storage.save_application(job_id, "applied")
    storage.save_application(
        job_id,
        "external_redirect",
        account_profile_id="work",
        resume_id="r1",
    )
    storage.save_application(
        job_id,
        "applied",
        account_profile_id="work",
        resume_id="r2",
    )
    storage.save_application(
        job_id,
        "applied",
        account_profile_id="personal",
        resume_id="r1",
    )

    storage.save_application(
        job_id,
        "applied",
        notes="updated exact row",
        account_profile_id="work",
        resume_id="r1",
    )

    rows = storage.list_applications()
    assert len(rows) == 4
    assert {
        (row.account_profile_id, row.resume_id, row.status, row.notes)
        for row in rows
    } == {
        ("legacy", "", "applied", ""),
        ("work", "r1", "applied", "updated exact row"),
        ("work", "r2", "applied", ""),
        ("personal", "r1", "applied", ""),
    }


def test_get_application_supports_exact_and_deterministic_aggregate_reads(
    tmp_path: Path,
) -> None:
    storage = Storage(tmp_path / "db.sqlite3")
    job_id = _job(storage)
    storage.save_application(
        job_id,
        "applied",
        account_profile_id="a",
        resume_id="r1",
    )
    storage.save_application(
        job_id,
        "external_redirect",
        account_profile_id="b",
        resume_id="r2",
    )
    storage.conn.execute(
        "UPDATE applications SET applied_at = ?, updated_at = ? WHERE job_id = ?",
        (
            "2026-07-01T10:00:00+00:00",
            "2026-07-01T10:00:00+00:00",
            job_id,
        ),
    )
    storage.conn.commit()

    aggregate = storage.get_application(job_id)
    exact = storage.get_application(
        job_id,
        account_profile_id="a",
        resume_id="r1",
    )

    assert aggregate is not None
    assert exact is not None
    assert (aggregate.account_profile_id, aggregate.resume_id) == ("b", "r2")
    assert (exact.account_profile_id, exact.resume_id) == ("a", "r1")


def test_application_stats_keep_one_newest_aggregate_per_job(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite3")
    job_id = _job(storage)
    storage.save_application(
        job_id,
        "applied",
        account_profile_id="a",
        resume_id="r1",
    )
    storage.save_application(
        job_id,
        "external_redirect",
        account_profile_id="b",
        resume_id="r2",
    )

    stats = storage.get_stats()

    assert stats["total_applications"] == 1
    assert stats["applications_by_status"] == {"external_redirect": 1}


def test_ghost_reader_does_not_duplicate_multi_identity_job(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite3")
    job_id = _job(storage)
    storage.set_status(job_id, "applied")
    storage.save_application(
        job_id,
        "applied",
        account_profile_id="a",
        resume_id="r1",
    )
    storage.save_application(
        job_id,
        "applied",
        account_profile_id="b",
        resume_id="r2",
    )
    storage.conn.execute(
        "UPDATE applications SET applied_at = ? WHERE job_id = ?",
        ("2020-01-01T00:00:00+00:00", job_id),
    )
    storage.conn.commit()

    assert [job.id for job in storage.get_ghost_jobs(days=30)] == [job_id]


def test_strategy_report_keeps_aggregate_application_count(tmp_path: Path) -> None:
    app = WorkHunter(tmp_path)
    job_id = _job(app.storage)
    app.storage.save_application(
        job_id,
        account_profile_id="a",
        resume_id="r1",
    )
    app.storage.save_application(
        job_id,
        account_profile_id="b",
        resume_id="r2",
    )

    report = app.strategy_report("active-profile")

    assert report["counts"]["applications"] == 1


def test_application_to_dict_contains_account_and_resume_identity() -> None:
    application = Application(
        id=1,
        job_id=2,
        status="applied",
        account_profile_id="work",
        resume_id="resume-1",
    )

    assert application.to_dict()["account_profile_id"] == "work"
    assert application.to_dict()["resume_id"] == "resume-1"


def test_packaged_migration_rebuilds_minimal_legacy_unique_job_table(
    tmp_path: Path,
) -> None:
    db = tmp_path / "legacy.sqlite3"
    _create_legacy_database(db, compatibility_columns=False)

    storage = Storage(db)

    row = storage.conn.execute(
        "SELECT account_profile_id FROM applications WHERE job_id = 1"
    ).fetchone()
    assert row["account_profile_id"] == "legacy"


def test_packaged_migration_preserves_every_compatibility_column(
    tmp_path: Path,
) -> None:
    db = tmp_path / "legacy.sqlite3"
    _create_legacy_database(db)

    storage = Storage(db)

    row = storage.conn.execute(
        "SELECT * FROM applications WHERE job_id = 1"
    ).fetchone()
    assert row["account_profile_id"] == "legacy"
    for column, expected in LEGACY_APPLICATION_COLUMNS.items():
        assert row[column] == expected
    assert row["autopilot_run_id"] is None
    assert row["autopilot_item_id"] is None
    assert row["autopilot_attempt_id"] is None


def test_packaged_migration_adds_attempt_provenance_without_losing_rows(
    tmp_path: Path,
) -> None:
    db = tmp_path / "legacy.sqlite3"
    _create_legacy_database(db)
    connection = sqlite3.connect(db)
    connection.executescript(
        """
        CREATE TABLE hh_application_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER,
            campaign_item_id INTEGER,
            vacancy_id TEXT NOT NULL DEFAULT '',
            resume_id TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT '',
            reason TEXT NOT NULL DEFAULT '',
            letter TEXT NOT NULL DEFAULT '',
            raw_result_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT ''
        );
        INSERT INTO hh_application_attempts (
            vacancy_id, resume_id, status, created_at
        ) VALUES ('v1', 'resume-1', 'applied', '2026-07-01T10:00:00+00:00');
        """
    )
    connection.commit()
    connection.close()

    storage = Storage(db)

    columns = {
        row["name"]
        for row in storage.conn.execute(
            "PRAGMA table_info(hh_application_attempts)"
        ).fetchall()
    }
    assert {
        "account_profile_id",
        "autopilot_run_id",
        "autopilot_item_id",
        "autopilot_attempt_id",
        "authorization_kind",
        "authorization_ref",
        "policy_hash",
        "delivery_certainty",
        "dispatched_at",
        "finished_at",
    } <= columns
    attempt = storage.conn.execute(
        "SELECT * FROM hh_application_attempts WHERE id = 1"
    ).fetchone()
    assert attempt["vacancy_id"] == "v1"
    assert attempt["account_profile_id"] == "legacy"


def test_legacy_identity_listing_is_deterministic_and_excludes_payloads(
    tmp_path: Path,
) -> None:
    storage = Storage(tmp_path / "db.sqlite3")
    second_job_id = _job(storage, "v2")
    first_job_id = _job(storage, "v1")
    storage.save_application(
        second_job_id,
        notes="must not be reported",
        result={"access_token": "must-not-leak"},
    )
    storage.save_application(first_job_id)

    rows = storage.list_legacy_application_identities()

    assert [row["id"] for row in rows] == sorted(row["id"] for row in rows)
    assert {
        "id",
        "account_profile_id",
        "job_id",
        "resume_id",
        "source",
        "source_id",
    } <= rows[0].keys()
    serialized = json.dumps(rows, ensure_ascii=False)
    assert "must not be reported" not in serialized
    assert "must-not-leak" not in serialized


@pytest.mark.parametrize("profile_id", [None, "", "   ", "legacy"])
def test_legacy_reassignment_rejects_invalid_account_profile_ids(
    tmp_path: Path,
    profile_id: Any,
) -> None:
    storage = Storage(tmp_path / "db.sqlite3")
    job_id = _job(storage)
    storage.save_application(job_id)

    with pytest.raises(ValueError, match="account profile"):
        storage.reassign_legacy_application_account(profile_id)

    assert storage.get_application(job_id).account_profile_id == "legacy"


def test_legacy_reassignment_is_atomic_when_target_identity_conflicts(
    tmp_path: Path,
) -> None:
    storage = Storage(tmp_path / "db.sqlite3")
    first_job_id = _job(storage, "v1")
    second_job_id = _job(storage, "v2")
    storage.save_application(first_job_id, account_profile_id="legacy", resume_id="r1")
    storage.save_application(second_job_id, account_profile_id="legacy", resume_id="r2")
    storage.save_application(first_job_id, account_profile_id="work", resume_id="r1")

    with pytest.raises(sqlite3.IntegrityError):
        storage.reassign_legacy_application_account("work")

    assert {
        (row["job_id"], row["resume_id"])
        for row in storage.list_legacy_application_identities()
    } == {(first_job_id, "r1"), (second_job_id, "r2")}


def test_single_unambiguous_legacy_hh_profile_is_reassigned(
    app_factory: Callable[..., WorkHunter],
    legacy_db: Path,
) -> None:
    app = app_factory(
        legacy_db,
        hh_account_profiles={"work": {"access_token": "account-secret"}},
    )

    assert app.storage.list_legacy_application_identities() == []
    assert app.storage.list_applications()[0].account_profile_id == "work"


def test_startup_reassignment_conflict_leaves_all_sentinels_reportable(
    app_factory: Callable[..., WorkHunter],
    legacy_db: Path,
) -> None:
    bootstrap = Storage(legacy_db)
    bootstrap.save_application(
        1,
        account_profile_id="work",
        resume_id="resume-1",
    )
    bootstrap.close()
    app = app_factory(
        legacy_db,
        hh_account_profiles={"work": {"access_token": "account-secret"}},
    )

    report = app.application_identity_migration_report()

    assert report["sentinel_count"] == 1
    assert app.storage.get_application(
        1,
        account_profile_id="work",
        resume_id="resume-1",
    ) is not None


def test_default_config_without_hh_identity_leaves_legacy_sentinel(
    legacy_db: Path,
) -> None:
    app = WorkHunter(legacy_db.parent.parent)

    report = app.application_identity_migration_report()

    assert report["sentinel_count"] == 1
    assert report["rows"][0]["account_profile_id"] == "legacy"


def test_legacy_top_level_hh_identity_is_reassigned_to_default_profile(
    app_factory: Callable[..., WorkHunter],
    legacy_db: Path,
) -> None:
    app = app_factory(
        legacy_db,
        hh_account_profiles={"default": {}},
        legacy_hh_identity={"access_token": "legacy-top-level-secret"},
    )

    assert app.storage.list_legacy_application_identities() == []
    assert app.storage.list_applications()[0].account_profile_id == "default"


def test_existing_authenticated_cookie_profile_proves_single_identity(
    app_factory: Callable[..., WorkHunter],
    legacy_db: Path,
) -> None:
    cookie_file = legacy_db.parent.parent / "work.cookies.txt"
    cookie_file.write_text(".hh.ru\tTRUE\t/\tTRUE\t0\t_xsrf\tproof\n", encoding="utf-8")
    app = app_factory(
        legacy_db,
        hh_account_profiles={"work": {"hh_cookie_file": str(cookie_file)}},
    )

    assert app.storage.list_legacy_application_identities() == []
    assert app.storage.list_applications()[0].account_profile_id == "work"


def test_client_credentials_without_user_auth_leave_legacy_sentinel(
    app_factory: Callable[..., WorkHunter],
    legacy_db: Path,
) -> None:
    app = app_factory(
        legacy_db,
        hh_account_profiles={
            "work": {"client_id": "client", "client_secret": "client-secret"}
        },
    )

    report = app.application_identity_migration_report()

    assert report["sentinel_count"] == 1
    assert app.storage.list_applications()[0].account_profile_id == "legacy"


def test_legacy_and_named_user_credentials_are_ambiguous(
    app_factory: Callable[..., WorkHunter],
    legacy_db: Path,
) -> None:
    app = app_factory(
        legacy_db,
        hh_account_profiles={"work": {"access_token": "named-secret"}},
        legacy_hh_identity={"refresh_token": "legacy-secret"},
    )

    report = app.application_identity_migration_report()

    assert report["sentinel_count"] == 1
    assert app.storage.list_applications()[0].account_profile_id == "legacy"


def test_multiple_effective_profiles_leave_deterministic_masked_report(
    app_factory: Callable[..., WorkHunter],
    legacy_db: Path,
) -> None:
    app = app_factory(
        legacy_db,
        hh_account_profiles={
            "b": {"access_token": "secret-b"},
            "a": {"refresh_token": "secret-a"},
        },
    )

    first = app.application_identity_migration_report()
    second = app.application_identity_migration_report()

    assert first == second
    assert first["sentinel_count"] == 1
    assert first["rows"][0]["account_profile_id"] == "legacy"
    serialized = json.dumps(first, ensure_ascii=False, sort_keys=True)
    assert "secret-a" not in serialized
    assert "secret-b" not in serialized


def test_multiple_empty_profile_stubs_do_not_prove_an_identity(
    app_factory: Callable[..., WorkHunter],
    legacy_db: Path,
) -> None:
    app = app_factory(
        legacy_db,
        hh_account_profiles={"a": {}, "b": {}},
    )

    report = app.application_identity_migration_report()

    assert report["sentinel_count"] == 1
    assert app.storage.list_applications()[0].account_profile_id == "legacy"
