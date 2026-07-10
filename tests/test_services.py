import json

from work_hunter.models import Job, JobScore
from work_hunter.services import WorkHunter
from work_hunter.sources import PUBLIC_BOARD_SOURCE_NAMES


def _use_python_profile(app: WorkHunter) -> None:
    app.config["profiles"]["python"] = {
        "queries": ["python"],
        "desired_roles": ["python"],
        "must_have_skills": ["python"],
        "nice_to_have_skills": [],
        "stop_words": [],
        "salary_min": 0,
    }
    app.config["profile"] = "python"


def _save_profile_score(
    app: WorkHunter,
    job_id: int,
    profile_id: str,
    total_score: int,
) -> None:
    app.storage.save_score(
        JobScore(
            job_id=job_id,
            profile_id=profile_id,
            total_score=total_score,
        )
    )


def _scored_job(
    app: WorkHunter,
    *,
    source_id: str,
    title: str,
    default_score: int,
    active_score: int,
) -> int:
    job_id = app.storage.upsert_job(
        Job(
            source="hh",
            source_id=source_id,
            url=f"https://example.test/{source_id}",
            title=title,
            company="Acme",
            description=f"{title} Python",
        )
    )
    _save_profile_score(app, job_id, "default", default_score)
    _save_profile_score(app, job_id, "python", active_score)
    return job_id


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


def test_strategy_and_daily_reports_use_active_profile_scores(tmp_path):
    app = WorkHunter(tmp_path)
    _use_python_profile(app)
    _scored_job(
        app,
        source_id="report",
        title="Python Report",
        default_score=10,
        active_score=90,
    )

    strategy = app.strategy_report("active-profile")
    daily = app.daily_report()

    assert strategy["top_jobs"][0]["score"]["profile_id"] == "python"
    assert daily["top"][0]["score"]["profile_id"] == "python"


def test_application_and_summary_exports_use_active_profile_scores(tmp_path):
    app = WorkHunter(tmp_path)
    _use_python_profile(app)
    job_id = _scored_job(
        app,
        source_id="export",
        title="Python Export",
        default_score=10,
        active_score=90,
    )
    app.storage.save_application(job_id, "applied")

    applications = json.loads(app.export_applications(format="json"))
    report = json.loads(app.export_report(format="json"))

    assert applications[0]["job"]["score"] == 90
    assert report["stats"]["score_distribution"] == {
        "0-20": 0,
        "21-40": 0,
        "41-60": 0,
        "61-80": 0,
        "81-100": 1,
    }


def test_description_search_ranks_jobs_for_active_profile(monkeypatch, tmp_path):
    app = WorkHunter(tmp_path)
    _use_python_profile(app)
    active_id = _scored_job(
        app,
        source_id="active-winner",
        title="Python Active Winner",
        default_score=10,
        active_score=90,
    )
    _scored_job(
        app,
        source_id="default-winner",
        title="Python Default Winner",
        default_score=95,
        active_score=20,
    )

    def fail_chat(*args, **kwargs):
        raise RuntimeError("offline")

    monkeypatch.setattr("work_hunter.services.chat_completion", fail_chat)

    jobs = app.search_by_description("Python", limit=1)

    assert jobs[0]["id"] == active_id
    assert jobs[0]["score"]["profile_id"] == "python"


def test_market_trends_samples_jobs_for_active_profile(monkeypatch, tmp_path):
    app = WorkHunter(tmp_path)
    _use_python_profile(app)
    _scored_job(
        app,
        source_id="active-winner",
        title="Python Active Winner",
        default_score=10,
        active_score=90,
    )
    _scored_job(
        app,
        source_id="default-winner",
        title="Python Default Winner",
        default_score=95,
        active_score=20,
    )
    prompts: list[str] = []

    def capture_chat(messages, config):
        prompts.append(messages[-1]["content"])
        return "trend"

    monkeypatch.setattr("work_hunter.services.chat_completion", capture_chat)

    assert app.market_trends(limit=1) == "trend"
    assert "Python Active Winner" in prompts[0]
    assert "Python Default Winner" not in prompts[0]


def test_campaign_eligibility_uses_active_profile_score(monkeypatch, tmp_path):
    app = WorkHunter(tmp_path)
    _use_python_profile(app)
    job_id = _scored_job(
        app,
        source_id="campaign",
        title="Python Campaign",
        default_score=10,
        active_score=90,
    )
    monkeypatch.setattr(
        app,
        "prepare_apply_plan",
        lambda job_id, resume_id=None: {
            "status": "ready",
            "resume_id": resume_id or "resume-1",
            "letter": "Hello",
            "risk_flags": [],
        },
    )

    run = app.plan_hh_campaign(limit=10, min_score=50)
    items = app.storage.list_hh_campaign_items(run["id"])

    assert len(items) == 1
    assert items[0].job_id == job_id
    assert items[0].status == "ready"


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
