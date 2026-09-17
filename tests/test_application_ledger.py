from work_hunter.models import Job
from work_hunter.services import WorkHunter


def test_ledger_paginates_all_applications_and_distinguishes_outcomes(tmp_path):
    app = WorkHunter(tmp_path)
    try:
        for number in range(205):
            job_id = app.storage.upsert_job(Job(source="fixture", source_id=str(number),
                url=f"https://example.test/{number}", title=f"Job {number}"))
            app.storage.save_application(job_id, status="applied", transport="api")
        first = app.storage.application_ledger()
        second = app.storage.application_ledger(after=first["next_cursor"])
        assert len(first["items"]) == 200
        assert len(second["items"]) == 5
        assert second["next_cursor"] is None
        assert len({item["application_id"] for item in first["items"] + second["items"]}) == 205
        app.storage.save_application(job_id, status="applied")
        assert app.storage.application_ledger(after=204)["items"][0]["stage"] == "manual"
        for status, expected in [("submission_unknown", "unknown"), ("submitted_unconfirmed", "unknown"),
                                 ("offer", "offer"), ("rejected", "rejected"), ("interview", "interview")]:
            app.storage.save_application(job_id, status=status)
            assert app.storage.application_ledger(after=204)["items"][0]["stage"] == expected
    finally:
        app.storage.close()


def test_generic_source_does_not_claim_verified_auto_apply(tmp_path):
    app = WorkHunter(tmp_path)
    try:
        for item in app.source_capabilities().values():
            assert item["auto_apply_verified"] is False
            assert item["last_verified_at"] is None
    finally:
        app.storage.close()
