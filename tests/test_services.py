import json

from work_hunter.models import Job
from work_hunter.services import WorkHunter
from work_hunter.sources import PUBLIC_BOARD_SOURCE_NAMES


def test_score_existing_jobs(tmp_path):
    app = WorkHunter(root=tmp_path)
    job_id = app.storage.upsert_job(
        Job(
            source="hh",
            source_id="1",
            url="u",
            title="Python Backend",
            company="Acme",
            description="Python",
        )
    )

    count = app.score_jobs()

    assert count == 1
    loaded = app.storage.get_job(job_id)
    assert loaded is not None
    assert loaded.score is not None


def test_scores_for_two_profiles_coexist_and_reads_use_active_profile(tmp_path):
    app = WorkHunter(tmp_path)
    app.config["profiles"]["python"] = {
        "desired_roles": ["python"],
        "must_have_skills": ["python"],
        "nice_to_have_skills": [],
        "stop_words": [],
        "salary_min": 0,
    }
    app.config["profiles"]["java"] = {
        "desired_roles": ["java"],
        "must_have_skills": ["java"],
        "nice_to_have_skills": [],
        "stop_words": [],
        "salary_min": 0,
    }
    job_id = app.storage.upsert_job(
        Job(
            source="x",
            source_id="1",
            url="https://example.test/1",
            title="Python Engineer",
            description="Python",
        )
    )

    app.switch_profile("python")
    app.score_jobs()
    python_score = app.get_job(job_id).score

    app.switch_profile("java")
    app.score_jobs()
    java_score = app.get_job(job_id).score

    rows = app.storage.query_readonly(
        f"SELECT profile_id, total_score FROM job_scores "
        f"WHERE job_id = {job_id} ORDER BY profile_id"
    )
    assert [row["profile_id"] for row in rows] == ["java", "python"]
    assert python_score.profile_id == "python"
    assert java_score.profile_id == "java"
    assert python_score.total_score > java_score.total_score

    app.switch_profile("python")
    assert app.list_jobs()[0].score.profile_id == "python"
    assert json.loads(app.export_jobs())[0]["score"]["profile_id"] == "python"


def test_prepare_letter_for_existing_job(tmp_path):
    app = WorkHunter(root=tmp_path)
    job_id = app.storage.upsert_job(
        Job(source="hh", source_id="1", url="u", title="Python Backend", company="Acme")
    )

    draft = app.prepare_letter(job_id)

    assert draft.job_id == job_id
    assert "Acme" in draft.body


def test_sync_sources_default_includes_public_board_sources(monkeypatch, tmp_path):
    app = WorkHunter(root=tmp_path)
    collected: list[str] = []

    class EmptyCollector:
        def collect(self, profile, limit=None):
            return []

    def fake_collector(self, source_name, source_config, *, backend=None):
        collected.append(source_name)
        return EmptyCollector()

    monkeypatch.setattr(WorkHunter, "_collector", fake_collector)

    app.sync_sources(sources=None, limit=0)

    assert collected == ["hh", "habr", "geekjob", "telegram", *PUBLIC_BOARD_SOURCE_NAMES]
