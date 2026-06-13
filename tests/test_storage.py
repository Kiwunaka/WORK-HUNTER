from work_hunter.models import Job, JobScore, LetterDraft
from work_hunter.storage import Storage


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
