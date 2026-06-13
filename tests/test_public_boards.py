from __future__ import annotations

from work_hunter.services import WorkHunter
from work_hunter.sources.public_boards import (
    PublicBoardSpec,
    PublicJobBoardSource,
    parse_public_board_html,
)


def test_parse_public_board_json_ld_job_posting():
    html = """
    <html><head>
      <script type="application/ld+json">
        {
          "@context": "https://schema.org",
          "@type": "JobPosting",
          "title": "Backend Python Engineer",
          "url": "/vacancies/32154-python-engineer",
          "datePosted": "2026-06-01",
          "description": "<p>FastAPI, PostgreSQL, remote.</p>",
          "hiringOrganization": {"name": "Acme"},
          "jobLocation": {"address": {"addressLocality": "Remote"}},
          "baseSalary": {
            "currency": "RUB",
            "value": {"minValue": 250000, "maxValue": 350000}
          }
        }
      </script>
    </head></html>
    """

    jobs = parse_public_board_html(html, source="getmatch", base_url="https://getmatch.ru")

    assert len(jobs) == 1
    assert jobs[0].source == "getmatch"
    assert jobs[0].source_id == "32154-python-engineer"
    assert jobs[0].url == "https://getmatch.ru/vacancies/32154-python-engineer"
    assert jobs[0].title == "Backend Python Engineer"
    assert jobs[0].company == "Acme"
    assert jobs[0].location == "Remote"
    assert jobs[0].remote is True
    assert jobs[0].salary_text == "250000-350000 RUB"


def test_parse_public_board_next_data_job():
    html = """
    <script id="__NEXT_DATA__" type="application/json">
      {
        "props": {
          "pageProps": {
            "jobs": [
              {
                "id": 9741,
                "title": "Python Developer",
                "company": {"name": "OneMarketData"},
                "href": "/vacancies/9741-python-developer-dashboard",
                "salary": "4 500 - 6 500 $",
                "location": "Belgrade",
                "remote": false,
                "description": "Dashboard work"
              }
            ]
          }
        }
      }
    </script>
    """

    jobs = parse_public_board_html(html, source="getmatch", base_url="https://getmatch.ru")

    assert len(jobs) == 1
    assert jobs[0].source_id == "9741"
    assert jobs[0].url == "https://getmatch.ru/vacancies/9741-python-developer-dashboard"
    assert jobs[0].title == "Python Developer"
    assert jobs[0].company == "OneMarketData"
    assert jobs[0].salary_text == "4 500 - 6 500 $"
    assert jobs[0].remote is False


def test_parse_public_board_anchor_fallback_job_card():
    html = """
    <article class="vacancy-card">
      <a class="title" href="/skills/python">Python Developer @ JetBrains</a>
      <div class="meta">JetBrains удаленка 6д Европа 10K $-16K $</div>
    </article>
    """

    jobs = parse_public_board_html(html, source="jabka", base_url="https://jabka.work")

    assert len(jobs) == 1
    assert jobs[0].source_id == "python"
    assert jobs[0].url == "https://jabka.work/skills/python"
    assert jobs[0].title == "Python Developer @ JetBrains"
    assert jobs[0].remote is True
    assert "JetBrains" in jobs[0].description


def test_public_board_source_collects_query_pages_and_dedupes():
    calls: list[str] = []

    def fake_fetch(url: str) -> str:
        calls.append(url)
        return """
        <script type="application/ld+json">
          {"@type":"JobPosting","title":"Python Dev","url":"/jobs/1","hiringOrganization":{"name":"Acme"}}
        </script>
        <a href="/jobs/1">Python Dev</a>
        """

    source = PublicJobBoardSource(
        {"paths": ["/jobs"], "query_params": ["q"], "pages": 1},
        source_name="hirehi",
        spec=PublicBoardSpec(
            name="hirehi",
            base_url="https://hirehi.ru",
            paths=("/jobs",),
            query_params=("q",),
        ),
        fetcher=fake_fetch,
    )

    jobs = source.collect({"queries": ["python"]}, limit=10)

    assert len(jobs) == 1
    assert jobs[0].source == "hirehi"
    assert calls == ["https://hirehi.ru/jobs?q=python"]


def test_work_hunter_syncs_configured_public_board_source(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "work_hunter.sources.public_boards.fetch_url",
        lambda url: """
        <script id="__NEXT_DATA__" type="application/json">
          {"props":{"pageProps":{"items":[{"id":"rvc-1","name":"API Engineer","companyName":"RVC","url":"/vacancy/view/api-engineer-1"}]}}}
        </script>
        """,
    )

    app = WorkHunter(root=tmp_path)
    app.config["sources"]["rvc"]["enabled"] = True
    app.config["sources"]["rvc"]["paths"] = ["/vacancy/view/api-engineer-1"]
    app.config["sources"]["rvc"]["query_params"] = []

    result = app.sync_sources(sources=["rvc"], limit=5)

    assert result["rvc"] == {"status": "ok", "count": 1}
    jobs = app.storage.list_jobs(limit=10, source="rvc")
    assert len(jobs) == 1
    assert jobs[0].title == "API Engineer"
    assert jobs[0].company == "RVC"
