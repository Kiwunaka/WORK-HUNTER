from datetime import datetime, timezone

from work_hunter.models import Job
from work_hunter.services import WorkHunter
from work_hunter.web.analytics import application_analytics


def test_application_cohort_deduplicates_resumes_and_excludes_uncertain(tmp_path):
    app = WorkHunter(tmp_path)
    for index, (status, stamp) in enumerate([
        ("applied", "2026-09-19T22:00:00+00:00"),
        ("submission_unknown", "2026-09-20T10:00:00+00:00"),
        ("prepared", "2026-09-20T10:00:00+00:00"),
        ("applied", "2026-08-01T10:00:00+00:00"),
    ], 1):
        job_id = app.storage.upsert_job(Job(source="habr", source_id=str(index), url=f"https://example.com/{index}", title="AI"))
        app.storage.save_application(job_id, status=status, sent_at=stamp, resume_id="7")
        if index == 1:
            app.storage.save_application(job_id, status="interview", sent_at=stamp, resume_id="8")
    report = application_analytics(app.storage, 7, now=datetime(2026, 9, 20, 12, tzinfo=timezone.utc))
    assert report["sent"] == 1
    assert report["pending"] == 1
    assert report["sources"][0]["interview"] == 1
    assert report["daily"][-1] == {"date": "2026-09-20", "count": 1}
    assert sum(day["count"] for day in report["daily"]) == report["sent"]


def test_empty_application_cohort_has_zero_filled_calendar(tmp_path):
    app = WorkHunter(tmp_path)
    report = application_analytics(app.storage, 30)
    assert report["sent"] == report["pending"] == 0
    assert report["sources"] == []
    assert len(report["daily"]) == 30


def test_uncertain_hh_attempt_is_visible_before_application_exists(tmp_path):
    app = WorkHunter(tmp_path)
    app.storage.conn.execute("""
        INSERT INTO hh_application_attempts (vacancy_id, account_profile_id, status)
        VALUES ('v-1', 'default', 'reconciling')
    """)
    report = application_analytics(app.storage)
    assert report["sent"] == 0
    assert report["pending"] == 1
    app.storage.conn.execute("UPDATE hh_application_attempts SET status='skipped'")
    assert application_analytics(app.storage)["pending"] == 0
