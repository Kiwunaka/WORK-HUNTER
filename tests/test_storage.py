from datetime import datetime, timedelta, timezone

import pytest

from work_hunter.models import Job, JobScore, LetterDraft
from work_hunter.storage import Storage, _utc_cutoff_days


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
