from work_hunter.models import Job
from work_hunter.scoring import score_job


def job(**kwargs):
    return Job(source="fixture", source_id="1", url="https://example.test", title="Backend", **kwargs)


def test_skill_tokens_do_not_confuse_go_java_with_django_javascript():
    score = score_job(job(description="Django JavaScript"), {"must_have_skills": ["go", "java"]})
    assert score.skills_score == 0


def test_incomparable_salary_is_unknown():
    profile = {"salary_min": 100000, "salary_currency": "RUB", "salary_period": "month", "salary_gross": False}
    for currency, text in [("USD", "150000 USD/year net"), ("EUR", "200 EUR/hour net"), ("RUB", "200000 RUB/month")]:
        score = score_job(job(salary_to=200000, currency=currency, salary_text=text), profile)
        assert score.salary_score == 0
        assert any("unknown" in reason for reason in score.reasons)


def test_comparable_salary_and_maximum_are_reachable():
    profile = {"desired_roles": ["backend"], "must_have_skills": ["python"], "nice_to_have_skills": ["sql"],
               "salary_min": 100000, "salary_currency": "RUB", "salary_period": "month", "salary_gross": False}
    score = score_job(job(description="Python SQL", remote=True, salary_to=200000,
                          currency="RUB", salary_text="200000 RUB/month net"), profile)
    assert score.total_score == 100


def test_remote_unknown_is_distinct_from_no():
    assert score_job(job(remote=None), {"remote_only": True}).remote_score == 0
    assert score_job(job(remote=False), {"remote_only": True}).remote_score == -10
