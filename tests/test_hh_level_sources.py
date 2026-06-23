from __future__ import annotations

import json

from work_hunter.sources.geekjob import GeekJobSource, parse_geekjob_apply_mechanism, parse_geekjob_json
from work_hunter.sources.getmatch import GetmatchSource, parse_getmatch_offer
from work_hunter.sources.habr import HabrSource, parse_habr_api_response, parse_habr_apply_mechanism
from work_hunter.sources.hh import HHSource
from work_hunter.sources.public_boards import PublicJobBoardSource, parse_public_board_apply_mechanism
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


def test_parse_geekjob_apply_mechanism_extracts_known_form():
    html = """
    <main>
      <a class="btn apply" href="/vacancy/abc123/respond">Apply</a>
      <form action="/responses/abc123" method="post">
        <input type="hidden" name="_csrf" value="secret-token">
        <label>Name <input type="text" name="full_name" required></label>
        <label>Email <input type="email" name="email" required></label>
        <label>Cover <textarea name="cover_letter" required></textarea></label>
      </form>
    </main>
    """

    mechanism = parse_geekjob_apply_mechanism(html, source_id="abc123", url="https://geekjob.ru/vacancy/abc123")

    assert mechanism["status"] == "detected"
    assert mechanism["mechanism"] == "form"
    assert mechanism["form_signature"] == "geekjob_apply_form:v1"
    assert mechanism["apply_url"] == "https://geekjob.ru/vacancy/abc123/respond"
    assert mechanism["form"]["form_url"] == "https://geekjob.ru/responses/abc123"
    assert mechanism["form"]["method"] == "POST"
    assert [field["name"] for field in mechanism["form"]["fields"]] == ["full_name", "email", "cover_letter"]
    assert mechanism["cover_letter_required"] is True
    assert "secret-token" not in json.dumps(mechanism, ensure_ascii=False)


def test_parse_habr_apply_mechanism_extracts_known_form():
    html = """
    <main>
      <a class="button" href="/vacancies/1000164602/respond">Respond</a>
      <form action="/applications" method="post">
        <input type="hidden" name="authenticity_token" value="secret-token">
        <input type="text" name="name" required>
        <input type="email" name="email" required>
        <textarea name="message" required></textarea>
      </form>
    </main>
    """

    mechanism = parse_habr_apply_mechanism(
        html,
        source_id="1000164602",
        url="https://career.habr.com/vacancies/1000164602",
    )

    assert mechanism["status"] == "detected"
    assert mechanism["mechanism"] == "form"
    assert mechanism["form_signature"] == "habr_apply_form:v1"
    assert mechanism["apply_url"] == "https://career.habr.com/vacancies/1000164602/respond"
    assert mechanism["form"]["form_url"] == "https://career.habr.com/applications"
    assert [field["name"] for field in mechanism["form"]["fields"]] == ["name", "email", "message"]
    assert mechanism["cover_letter_required"] is True
    assert "secret-token" not in json.dumps(mechanism, ensure_ascii=False)


def test_parse_public_board_apply_mechanism_blocks_login_form_and_extracts_apply_form():
    html = """
    <main>
      <form action="/login" method="post">
        <input type="email" name="email">
        <input type="password" name="password">
      </form>
      <a href="/jobs/python/apply">Apply now</a>
      <form class="apply-form" action="/jobs/python/apply" method="post">
        <input type="hidden" name="csrf" value="secret-token">
        <input name="full_name" required>
        <input type="email" name="email" required>
        <textarea name="cover_letter" required></textarea>
      </form>
    </main>
    """

    mechanism = parse_public_board_apply_mechanism(
        html,
        source="jabka",
        source_id="python",
        url="https://jabka.work/jobs/python",
        base_url="https://jabka.work",
    )

    assert mechanism["status"] == "detected"
    assert mechanism["form_signature"] == "jabka_apply_form:v1"
    assert mechanism["apply_url"] == "https://jabka.work/jobs/python/apply"
    assert mechanism["form"]["form_url"] == "https://jabka.work/jobs/python/apply"
    assert [field["name"] for field in mechanism["form"]["fields"]] == ["full_name", "email", "cover_letter"]
    assert mechanism["cover_letter_required"] is True
    assert "password" not in [field["name"] for field in mechanism["form"]["fields"]]
    assert "secret-token" not in json.dumps(mechanism, ensure_ascii=False)


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


def test_getmatch_source_detail_fetches_offer_payload():
    def fake_fetch(url: str) -> str:
        assert url == "https://getmatch.ru/api/offers/34397"
        return json.dumps(
            {
                "id": 34397,
                "position": "AQA Python Engineer",
                "company": {"name": "Bureau"},
                "offer_description": "Pytest and API automation",
            }
        )

    source = GetmatchSource({}, fetcher=fake_fetch)

    job = source.detail("34397")

    assert job.source == "getmatch"
    assert job.source_id == "34397"
    assert job.title == "AQA Python Engineer"
    assert job.company == "Bureau"
    assert "Pytest" in job.description


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


def test_public_board_source_detail_fetches_candidate_detail_url():
    calls: list[str] = []

    def fake_fetch(url: str) -> str:
        calls.append(url)
        return """
        <main>
          <h1>Python Backend @ Acme</h1>
          <section>Remote FastAPI role</section>
        </main>
        """

    source = PublicJobBoardSource(
        {"base_url": "https://hirehi.example", "paths": ["/jobs"]},
        source_name="hirehi",
        fetcher=fake_fetch,
    )

    job = source.detail("python-backend")

    assert calls[0] == "https://hirehi.example/jobs/python-backend"
    assert job.source == "hirehi"
    assert job.source_id == "python-backend"
    assert job.url == "https://hirehi.example/jobs/python-backend"
    assert job.title == "Python Backend @ Acme"
    assert job.company == "Acme"
    assert job.remote is True
    assert "FastAPI" in job.description


def test_habr_source_detail_fetches_public_vacancy_page(monkeypatch):
    def fake_fetch(url: str) -> str:
        assert url == "https://career.habr.com/vacancies/1000164602"
        return """
        <html>
          <h1>LEAD AI/ML ENGINEER</h1>
          <a class="company_name">Selecty</a>
          <div class="location">Remote, Moscow</div>
          <article>Python, ML, platform work.</article>
        </html>
        """

    monkeypatch.setattr("work_hunter.sources.habr.fetch_url", fake_fetch)

    job = HabrSource({}).detail("1000164602")

    assert job.source == "habr"
    assert job.source_id == "1000164602"
    assert job.title == "LEAD AI/ML ENGINEER"
    assert job.company == "Selecty"
    assert job.remote is True
    assert "Python" in job.description


def test_geekjob_source_detail_fetches_public_vacancy_page(monkeypatch):
    def fake_fetch(url: str) -> str:
        assert url == "https://geekjob.ru/vacancy/abc123"
        return """
        <html>
          <h1>Backend Python Developer</h1>
          <a class="company-name">Acme</a>
          <div class="location">Remote</div>
          <section>FastAPI, PostgreSQL</section>
        </html>
        """

    monkeypatch.setattr("work_hunter.sources.geekjob.fetch_url", fake_fetch)

    job = GeekJobSource({}).detail("abc123")

    assert job.source == "geekjob"
    assert job.source_id == "abc123"
    assert job.title == "Backend Python Developer"
    assert job.company == "Acme"
    assert job.remote is True
    assert "FastAPI" in job.description


def test_hh_source_detail_fetches_public_vacancy_page(monkeypatch):
    def fake_fetch(url: str, headers=None) -> str:
        assert url == "https://hh.ru/vacancy/123456"
        assert headers and "User-Agent" in headers
        return """
        <html>
          <h1 data-qa="vacancy-title">Python Backend Developer</h1>
          <a data-qa="vacancy-company-name">Acme</a>
          <p data-qa="vacancy-view-location">Remote</p>
          <div data-qa="vacancy-description">FastAPI services</div>
        </html>
        """

    monkeypatch.setattr("work_hunter.sources.hh.fetch_url", fake_fetch)

    job = HHSource({"web_user_agent": "test-agent"}).detail("123456")

    assert job.source == "hh"
    assert job.source_id == "123456"
    assert job.title == "Python Backend Developer"
    assert job.company == "Acme"
    assert job.remote is True
    assert "FastAPI" in job.description


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
