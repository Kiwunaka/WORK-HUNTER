from work_hunter.letters import draft_cover_letter
from work_hunter.models import Job
from work_hunter.scoring import score_job


def test_score_job_with_matching_skill():
    profile = {
        "desired_roles": ["backend"],
        "must_have_skills": ["python"],
        "nice_to_have_skills": ["fastapi"],
        "stop_words": ["bitrix"],
        "remote_only": True,
    }
    job = Job(
        source="hh",
        source_id="1",
        url="u",
        title="Python Backend Developer",
        company="Acme",
        description="FastAPI remote",
        remote=True,
    )

    score = score_job(job, profile)

    assert score.total_score >= 70
    assert "python" in " ".join(score.reasons).lower()


def test_score_job_with_stop_word_penalty():
    profile = {
        "must_have_skills": ["python"],
        "nice_to_have_skills": [],
        "stop_words": ["bitrix"],
    }
    job = Job(
        source="hh",
        source_id="1",
        url="u",
        title="Python Developer",
        description="Bitrix integration",
    )

    score = score_job(job, profile)

    assert score.penalty_score == -30
    assert score.red_flags


def test_draft_cover_letter_contains_company_and_title():
    job = Job(
        source="hh",
        source_id="1",
        url="u",
        title="Python Backend Developer",
        company="Acme",
    )

    text = draft_cover_letter(job, {"name": "Кандидат", "must_have_skills": ["Python"]})

    assert "Acme" in text
    assert "Python Backend Developer" in text
