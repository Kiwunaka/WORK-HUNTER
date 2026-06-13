from __future__ import annotations

import json

from work_hunter.sources.geekjob import parse_geekjob_json
from work_hunter.sources.getmatch import GetmatchSource, parse_getmatch_offer
from work_hunter.sources.habr import parse_habr_api_response
from work_hunter.sources.relocate_me import (
    RelocateMeSource,
    parse_relocate_detail_html,
    parse_relocate_list_html,
)


def test_parse_habr_api_response_maps_structured_vacancy():
    payload = {
        "list": [
            {
                "id": 1000164602,
                "href": "/vacancies/1000164602",
                "title": "LEAD AI/ML ENGINEER",
                "remoteWork": True,
                "company": {"title": "Selecty"},
                "locations": [{"title": "Moscow"}],
                "salary": {"from": 300000, "to": 450000, "currency": "rur"},
                "skills": [{"title": "Python"}, {"title": "ML"}],
                "divisions": [{"title": "Backend"}],
                "employment": "full_time",
                "salaryQualification": {"title": "Lead"},
                "publishedDate": {"date": "2026-06-10T12:00:00+03:00"},
            }
        ],
    }

    jobs = parse_habr_api_response(payload)

    assert len(jobs) == 1
    assert jobs[0].source == "habr"
    assert jobs[0].source_id == "1000164602"
    assert jobs[0].url == "https://career.habr.com/vacancies/1000164602"
    assert jobs[0].title == "LEAD AI/ML ENGINEER"
    assert jobs[0].company == "Selecty"
    assert jobs[0].salary_text == "300000-450000 RUB"
    assert jobs[0].location == "Moscow"
    assert jobs[0].remote is True
    assert "Python" in jobs[0].description


def test_parse_geekjob_json_maps_native_format():
    payload = {
        "page": 1,
        "count": 1,
        "data": [
            {
                "position": "Backend Python Developer",
                "title": "Компания Acme ищет Backend Python Developer",
                "company": "Acme",
                "location": "Cyprus",
                "date": "2026-06-09",
                "salary": {"string": "300K ₽", "details": {"min": 300000, "max": 0, "cur": "RUB"}},
                "jobFormat": {"remote": True, "relocate": False, "parttime": False, "inhouse": False},
                "link": "https://geekjob.ru/vacancy/abc123",
                "description": "FastAPI, PostgreSQL",
                "keywords": ["Python", "FastAPI"],
                "experience": ["Middle"],
            }
        ],
    }

    jobs = parse_geekjob_json(payload)

    assert len(jobs) == 1
    assert jobs[0].source_id == "abc123"
    assert jobs[0].title == "Backend Python Developer"
    assert jobs[0].company == "Acme"
    assert jobs[0].salary_text == "300K ₽"
    assert jobs[0].location == "Cyprus"
    assert jobs[0].remote is True
    assert "FastAPI" in jobs[0].description


def test_getmatch_source_fetches_list_and_detail_payloads():
    calls: list[str] = []

    def fake_fetch(url: str) -> str:
        calls.append(url)
        if "/api/offers/" in url:
            return json.dumps(
                {
                    "id": 34397,
                    "position": "AQA Python Engineer",
                    "url": "/vacancies/aqa-python-engineer",
                    "is_active": True,
                    "company": {"name": "Bureau"},
                    "salary_display_from": 250000,
                    "salary_display_to": 350000,
                    "salary_currency": "₽",
                    "offer_description": "Pytest and API automation",
                    "stack": ["Python", "Pytest"],
                    "location_items": [{"label": "Remote", "format": "remote", "exclude": False}],
                    "published_at": "2026-06-09T10:00:00+03:00",
                }
            )
        return json.dumps(
            {
                "meta": {"total": 1, "limit": 50, "offset": 0},
                "offers": [{"id": 34397, "position": "AQA Python Engineer", "is_active": True}],
            }
        )

    source = GetmatchSource({"pages": 1, "per_page": 50, "fetch_details": True}, fetcher=fake_fetch)

    jobs = source.collect({"queries": ["python"]}, limit=10)

    assert len(jobs) == 1
    assert jobs[0].source == "getmatch"
    assert jobs[0].source_id == "34397"
    assert jobs[0].company == "Bureau"
    assert jobs[0].salary_text == "250000-350000 ₽"
    assert jobs[0].remote is True
    assert "Pytest" in jobs[0].description
    assert calls[0] == "https://getmatch.ru/api/offers?limit=50&offset=0"
    assert calls[1] == "https://getmatch.ru/api/offers/34397"


def test_parse_getmatch_offer_uses_list_payload_when_detail_is_disabled():
    job = parse_getmatch_offer(
        {
            "id": 7,
            "position": "Backend Developer",
            "url": "/vacancies/backend-developer",
            "company": {"name": "Acme"},
            "salary_description": "от 300 000 ₽",
            "location_items": [{"label": "Moscow", "format": "hybrid", "exclude": False}],
            "stack": ["Python"],
        }
    )

    assert job.source_id == "7"
    assert job.url == "https://getmatch.ru/vacancies/backend-developer"
    assert job.title == "Backend Developer"
    assert job.salary_text == "от 300 000 ₽"
    assert job.location == "Moscow"
    assert job.remote is None


def test_parse_relocate_list_and_detail_html():
    list_html = """
    <div class="job">
      <div class="job__title"><a href="/international-jobs/python-developer-123">Python Developer</a></div>
      <div class="job__company">Acme</div>
      <div class="job__location">Germany</div>
    </div>
    """
    detail_html = """
    <h1>Python Developer</h1>
    <div class="job-info__company"><a>Acme GmbH</a></div>
    <div class="job-info__country"><p>Remote, Germany</p></div>
    <div class="job-info__description"><p>Build APIs with Django.</p></div>
    """

    jobs = parse_relocate_list_html(list_html)
    detailed = parse_relocate_detail_html(detail_html, base_job=jobs[0])

    assert jobs[0].source == "relocate_me"
    assert jobs[0].source_id == "python-developer-123"
    assert jobs[0].url == "https://relocate.me/international-jobs/python-developer-123"
    assert detailed.company == "Acme GmbH"
    assert detailed.location == "Remote, Germany"
    assert detailed.remote is True
    assert "Django" in detailed.description


def test_relocate_source_fetches_listing_then_details():
    def fake_fetch(url: str) -> str:
        if "python-developer-123" in url:
            return """
            <h1>Python Developer</h1>
            <div class="job-info__company"><a>Acme</a></div>
            <div class="job-info__country"><p>Germany</p></div>
            <div class="job-info__description">Relocation package.</div>
            """
        return """
        <div class="job">
          <div class="job__title"><a href="/international-jobs/python-developer-123">Python Developer</a></div>
        </div>
        """

    source = RelocateMeSource({"pages": 1, "fetch_details": True}, fetcher=fake_fetch)

    jobs = source.collect({"queries": ["python"]}, limit=5)

    assert len(jobs) == 1
    assert jobs[0].company == "Acme"
    assert "Relocation package" in jobs[0].description


def test_relocate_parser_skips_paid_job_digest_promo_cards():
    html = """
    <div class="jobs-list__job">
      <div class="job__title">
        <a href="/remote/remote/the-global-move/1000-curated-visa-sponsorship-and-remote-tech-jobs-paid-option-10080">
          <b>1000+ Curated Visa Sponsorship and Remote Tech Jobs (Paid Option)</b>
        </a>
      </div>
    </div>
    <div class="jobs-list__job">
      <div class="job__title">
        <a href="/remote/remote/micro1/building-code-compliance-expert-10260">
          <b>Building Code Compliance Expert</b>
        </a>
      </div>
    </div>
    """

    jobs = parse_relocate_list_html(html)

    assert len(jobs) == 1
    assert jobs[0].title == "Building Code Compliance Expert"
