import sqlite3
from datetime import datetime, timedelta, timezone
from typing import cast

import pytest

from work_hunter.models import Job, JobScore, LetterDraft
from work_hunter.storage import Storage, _required_lastrowid, _utc_cutoff_days


class _FakeCursor:
    def __init__(self, lastrowid: int | None):
        self.lastrowid = lastrowid


def test_required_lastrowid_enforces_sqlite_insert_invariant():
    assert _required_lastrowid(cast(sqlite3.Cursor, _FakeCursor(42))) == 42

    with pytest.raises(RuntimeError, match="^SQLite INSERT did not produce a row id$"):
        _required_lastrowid(cast(sqlite3.Cursor, _FakeCursor(None)))


def test_upsert_job_and_status(tmp_path):
    storage = Storage(tmp_path / "db.sqlite3")
    job = Job(
        source="hh",
        source_id="1",
        url="https://example.test/1",
        title="Python Developer",
        company="Acme",
    )

    job_id = storage.upsert_job(job)

    assert storage.upsert_job(job) == job_id
    storage.set_status(job_id, "saved", "looks good")
    loaded = storage.get_job(job_id)
    assert loaded is not None
    assert loaded.status == "saved"


def test_save_score_and_letter(tmp_path):
    storage = Storage(tmp_path / "db.sqlite3")
    job_id = storage.upsert_job(
        Job(source="geekjob", source_id="abc", url="u", title="Backend", company="Acme")
    )

    storage.save_score(
        JobScore(
            job_id=job_id,
            total_score=88,
            reasons=["Python match"],
            red_flags=[],
        )
    )
    storage.save_letter(LetterDraft(job_id=job_id, body="Hello"))

    loaded = storage.get_job(job_id)
    letter = storage.get_latest_letter(job_id)
    assert loaded is not None
    assert loaded.score is not None
    assert loaded.score.total_score == 88
    assert letter is not None
    assert letter.body == "Hello"


def test_get_ghost_jobs_honors_days_cutoff(tmp_path):
    storage = Storage(tmp_path / "db.sqlite3")
    old_id = storage.upsert_job(
        Job(source="x", source_id="old", url="u1", title="Old")
    )
    fresh_id = storage.upsert_job(
        Job(source="x", source_id="fresh", url="u2", title="Fresh")
    )
    for job_id in (old_id, fresh_id):
        storage.set_status(job_id, "applied")
        storage.save_application(job_id, "applied")

    now = datetime.now(timezone.utc).replace(microsecond=0)
    storage.conn.execute(
        "UPDATE applications SET applied_at=? WHERE job_id=?",
        ((now - timedelta(days=31)).isoformat(), old_id),
    )
    storage.conn.execute(
        "UPDATE applications SET applied_at=? WHERE job_id=?",
        ((now - timedelta(days=1)).isoformat(), fresh_id),
    )
    storage.conn.commit()

    assert [job.id for job in storage.get_ghost_jobs(days=30)] == [old_id]


def test_get_ghost_jobs_compares_iso_timestamps_by_utc_instant(tmp_path):
    storage = Storage(tmp_path / "db.sqlite3")
    old_id = storage.upsert_job(
        Job(source="x", source_id="offset-old", url="u1", title="Old")
    )
    fresh_id = storage.upsert_job(
        Job(source="x", source_id="offset-fresh", url="u2", title="Fresh")
    )
    for job_id in (old_id, fresh_id):
        storage.set_status(job_id, "applied")
        storage.save_application(job_id, "applied")

    cutoff = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(days=30)
    old_at = (cutoff - timedelta(hours=1)).astimezone(
        timezone(timedelta(hours=14))
    )
    fresh_at = (cutoff + timedelta(hours=1)).astimezone(
        timezone(-timedelta(hours=12))
    )
    storage.conn.execute(
        "UPDATE applications SET applied_at=? WHERE job_id=?",
        (old_at.isoformat(), old_id),
    )
    storage.conn.execute(
        "UPDATE applications SET applied_at=? WHERE job_id=?",
        (fresh_at.isoformat(), fresh_id),
    )
    storage.conn.commit()

    assert [job.id for job in storage.get_ghost_jobs(days=30)] == [old_id]


def test_get_ghost_jobs_orders_mixed_offsets_by_utc_instant(tmp_path):
    storage = Storage(tmp_path / "db.sqlite3")
    older_id = storage.upsert_job(
        Job(source="x", source_id="older", url="u1", title="Older")
    )
    newer_id = storage.upsert_job(
        Job(source="x", source_id="newer", url="u2", title="Newer")
    )
    for job_id in (older_id, newer_id):
        storage.set_status(job_id, "applied")
        storage.save_application(job_id, "applied")

    cutoff = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(days=30)
    older_at = (cutoff - timedelta(hours=2)).astimezone(
        timezone(timedelta(hours=14))
    )
    newer_at = (cutoff - timedelta(hours=1)).astimezone(
        timezone(-timedelta(hours=12))
    )
    storage.conn.execute(
        "UPDATE applications SET applied_at=? WHERE job_id=?",
        (older_at.isoformat(), older_id),
    )
    storage.conn.execute(
        "UPDATE applications SET applied_at=? WHERE job_id=?",
        (newer_at.isoformat(), newer_id),
    )
    storage.conn.commit()

    assert [job.id for job in storage.get_ghost_jobs(days=30)] == [
        older_id,
        newer_id,
    ]


def test_get_ghost_jobs_preserves_fractional_cutoff_boundary(tmp_path, monkeypatch):
    storage = Storage(tmp_path / "db.sqlite3")
    exact_id = storage.upsert_job(
        Job(source="x", source_id="exact", url="u1", title="Exact")
    )
    fractional_id = storage.upsert_job(
        Job(source="x", source_id="fractional", url="u2", title="Fractional")
    )
    for job_id in (exact_id, fractional_id):
        storage.set_status(job_id, "applied")
        storage.save_application(job_id, "applied")

    cutoff = datetime(2026, 6, 10, 12, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "work_hunter.storage._utc_cutoff_days",
        lambda days: cutoff.isoformat(),
    )
    storage.conn.execute(
        "UPDATE applications SET applied_at=? WHERE job_id=?",
        (cutoff.isoformat(), exact_id),
    )
    storage.conn.execute(
        "UPDATE applications SET applied_at=? WHERE job_id=?",
        ((cutoff + timedelta(microseconds=999_999)).isoformat(), fractional_id),
    )
    storage.conn.commit()

    assert [job.id for job in storage.get_ghost_jobs(days=30)] == [exact_id]


def test_utc_cutoff_days_uses_supplied_utc_reference():
    now = datetime(2026, 7, 10, 12, 30, 0, 999_999, tzinfo=timezone.utc)

    assert _utc_cutoff_days(30, now=now) == "2026-06-10T12:30:00+00:00"


def test_utc_cutoff_days_clamps_non_positive_days():
    now = datetime(2026, 7, 10, 12, 30, tzinfo=timezone.utc)

    assert _utc_cutoff_days(0, now=now) == now.isoformat()
    assert _utc_cutoff_days(-30, now=now) == now.isoformat()


def test_utc_cutoff_days_rejects_non_numeric_days():
    with pytest.raises(ValueError):
        _utc_cutoff_days("invalid")  # type: ignore[arg-type]


@pytest.mark.parametrize("days", [1_000_000, 10**100])
def test_utc_cutoff_days_saturates_at_earliest_utc_time(days):
    now = datetime(2026, 7, 10, 12, 30, tzinfo=timezone.utc)

    assert _utc_cutoff_days(days, now=now) == "0001-01-01T00:00:00+00:00"


def test_get_ghost_jobs_ignores_malformed_legacy_timestamp(tmp_path):
    storage = Storage(tmp_path / "db.sqlite3")
    job_id = storage.upsert_job(
        Job(source="x", source_id="legacy", url="u", title="Legacy")
    )
    storage.set_status(job_id, "applied")
    storage.save_application(job_id, "applied")
    storage.conn.execute(
        "UPDATE applications SET applied_at=? WHERE job_id=?",
        ("0000-legacy", job_id),
    )
    storage.conn.commit()

    assert storage.get_ghost_jobs(days=0) == []


def test_application_transition_to_applied_refreshes_ghost_age(tmp_path):
    storage = Storage(tmp_path / "db.sqlite3")
    job_id = storage.upsert_job(
        Job(source="x", source_id="transition", url="u", title="Transition")
    )
    storage.save_application(job_id, "external_redirect")
    stale_applied_at = (
        datetime.now(timezone.utc).replace(microsecond=0) - timedelta(days=31)
    ).isoformat()
    storage.conn.execute(
        "UPDATE applications SET applied_at=? WHERE job_id=?",
        (stale_applied_at, job_id),
    )
    storage.conn.commit()

    storage.save_application(job_id, "applied")
    storage.set_status(job_id, "applied")

    application = storage.get_application(job_id)
    assert application is not None
    assert application.applied_at != stale_applied_at
    assert storage.get_ghost_jobs(days=30) == []


@pytest.mark.parametrize(
    ("existing_status", "next_status"),
    [
        ("applied", "applied"),
        ("external_redirect", "external_redirect"),
        ("applied", "external_redirect"),
    ],
)
def test_save_application_preserves_age_without_transition_to_applied(
    tmp_path,
    existing_status,
    next_status,
):
    storage = Storage(tmp_path / "db.sqlite3")
    job_id = storage.upsert_job(
        Job(source="x", source_id="preserve", url="u", title="Preserve")
    )
    storage.save_application(job_id, existing_status)
    original_applied_at = "2020-01-02T03:04:05+00:00"
    storage.conn.execute(
        "UPDATE applications SET applied_at=? WHERE job_id=?",
        (original_applied_at, job_id),
    )
    storage.conn.commit()

    storage.save_application(job_id, next_status)

    application = storage.get_application(job_id)
    assert application is not None
    assert application.applied_at == original_applied_at
