from __future__ import annotations

import hashlib

import work_hunter.sources.common as source_common
from work_hunter.models import Job
from work_hunter.services import WorkHunter
from work_hunter.storage import Storage
from work_hunter.sources.public_boards import (
    PublicBoardSpec,
    PublicJobBoardSource,
    extract_jobs_from_json_like,
    parse_public_board_html,
)


def test_canonicalize_job_url_preserves_repeated_identity_query_pairs():
    url = (
        "HTTPS://App.RVC.Global/vacancy/view?tag=python%20api&utm_source=x"
        "&tag=backend&id=1&gclid=ignored#top"
    )

    canonical = source_common.canonicalize_job_url(url)

    assert canonical == (
        "https://app.rvc.global/vacancy/view?id=1&tag=backend&tag=python+api"
    )


def test_invalid_percent_octets_remain_distinct_job_identities():
    first = source_common.canonicalize_job_url(
        "https://app.rvc.global/vacancy/view?id=%FF"
    )
    second = source_common.canonicalize_job_url(
        "https://app.rvc.global/vacancy/view?id=%FE"
    )
    html = """
      <a href="/vacancy/view?id=%FF">Backend One</a>
      <a href="/vacancy/view?id=%FE">Backend Two</a>
    """

    jobs = parse_public_board_html(
        html,
        source="rvc",
        base_url="https://app.rvc.global",
    )

    assert first == "https://app.rvc.global/vacancy/view?id=%FF"
    assert second == "https://app.rvc.global/vacancy/view?id=%FE"
    assert source_common.canonicalize_job_url(first) == first
    assert source_common.canonicalize_job_url(second) == second
    assert len(jobs) == 2
    assert len({job.source_id for job in jobs}) == 2


def test_canonicalize_query_bytes_preserves_malformed_repeated_blank_pairs():
    canonical = source_common.canonicalize_job_url(
        "https://app.rvc.global/vacancy/view"
        "?utm_%FF=keep&flag&flag=&id=%F&id=%G0"
    )

    assert canonical == (
        "https://app.rvc.global/vacancy/view"
        "?flag=&flag=&id=%25F&id=%25G0&utm_%FF=keep"
    )
    assert source_common.canonicalize_job_url(canonical) == canonical


def test_tracking_filter_only_removes_safe_ascii_keys():
    canonical = source_common.canonicalize_job_url(
        "https://app.rvc.global/vacancy/view"
        "?UTM_Source=x&%75tm_medium=y&gclid=z&YCLID=q"
        "&utm_%=keep&utm_%G0=also&=blank&&"
    )

    assert canonical == (
        "https://app.rvc.global/vacancy/view"
        "?=blank&utm_%25=keep&utm_%25G0=also"
    )


def test_query_identity_is_distinct_and_tracking_params_are_canonicalized():
    first = source_common.canonicalize_job_url(
        "https://app.rvc.global/vacancy/view?b=2&id=1&utm_source=x#top"
    )
    equivalent = source_common.canonicalize_job_url(
        "https://app.rvc.global/vacancy/view?id=1&b=2"
    )
    assert first == equivalent

    html = """
      <a href="/vacancy/view?id=1&amp;b=2&amp;utm_source=x">Backend Engineer</a>
      <a href="/vacancy/view?b=2&amp;id=2">Backend Engineer</a>
    """

    jobs = parse_public_board_html(
        html,
        source="rvc",
        base_url="https://app.rvc.global",
    )

    assert len(jobs) == 2
    assert len({job.source_id for job in jobs}) == 2
    expected = "rvc-" + hashlib.sha256(first.encode("utf-8")).hexdigest()[:24]
    assert jobs[0].source_id == expected


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
    assert jobs[0].url == "https://getmatch.ru/vacancies/32154-python-engineer"
    expected_id = "getmatch-" + hashlib.sha256(
        source_common.canonicalize_job_url(jobs[0].url).encode("utf-8")
    ).hexdigest()[:24]
    assert jobs[0].source_id == expected_id
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


def test_payload_vacancy_identifiers_take_priority_over_url_fallback():
    jobs = extract_jobs_from_json_like(
        [
            {
                "title": "Backend Engineer",
                "company": "Acme",
                "url": "/vacancy/view?id=99",
                "vacancyId": "vac-42",
                "id": "generic-42",
            },
            {
                "title": "Python Engineer",
                "company": "Beta",
                "url": "/vacancy/view?id=100",
                "@id": "https://app.rvc.global/nodes/posting-42",
                "identifier": {"value": "schema-42"},
            },
            {
                "title": "Platform Engineer",
                "company": "Gamma",
                "url": "/vacancy/view?id=101",
                "vacancy_id": 0,
            },
        ],
        source="rvc",
        base_url="https://app.rvc.global",
    )

    assert [job.source_id for job in jobs] == ["vac-42", "schema-42", "0"]


def test_payload_ids_accept_only_supported_scalar_values():
    invalid_ids = [False, [1], {}, None, 1.5]
    payloads = [
        {
            "title": f"Backend Engineer {index}",
            "company": "Acme",
            "url": f"/vacancy/view?id={index}",
            "vacancy_id": invalid_id,
        }
        for index, invalid_id in enumerate(invalid_ids, start=1)
    ]
    payloads.extend(
        [
            {
                "title": "Nested Invalid Identifier",
                "company": "Acme",
                "url": "/vacancy/view?id=6",
                "identifier": {"value": [6]},
            },
            {
                "title": "Lower Priority Identifier",
                "company": "Acme",
                "url": "/vacancy/view?id=7",
                "vacancy_id": True,
                "id": "valid-lower-id",
            },
        ]
    )

    jobs = extract_jobs_from_json_like(
        payloads,
        source="rvc",
        base_url="https://app.rvc.global",
    )

    expected_fallback_ids = [
        "rvc-"
        + hashlib.sha256(
            source_common.canonicalize_job_url(job.url).encode("utf-8")
        ).hexdigest()[:24]
        for job in jobs[:6]
    ]
    assert [job.source_id for job in jobs] == [
        *expected_fallback_ids,
        "valid-lower-id",
    ]


def test_invalid_payload_ids_do_not_collapse_jobs_on_disk(tmp_path):
    jobs = extract_jobs_from_json_like(
        [
            {
                "title": "Backend One",
                "company": "Acme",
                "url": "/vacancy/view?id=one",
                "vacancy_id": False,
            },
            {
                "title": "Backend Two",
                "company": "Beta",
                "url": "/vacancy/view?id=two",
                "vacancy_id": False,
            },
        ],
        source="rvc",
        base_url="https://app.rvc.global",
    )
    storage = Storage(tmp_path / "jobs.sqlite3")

    storage.upsert_jobs(jobs)
    persisted = storage.list_jobs(limit=10, source="rvc")
    storage.close()

    assert len(jobs) == 2
    assert {job.url for job in persisted} == {job.url for job in jobs}
    assert len({job.source_id for job in jobs}) == 2


def test_url_less_payload_fallback_ids_use_sha256_prefix():
    payload = {
        "title": "Backend Engineer",
        "company": "Acme",
    }

    jobs = extract_jobs_from_json_like(
        [payload, {**payload, "company": "Beta"}],
        source="rvc",
        base_url="https://app.rvc.global",
    )

    assert len(jobs) == 2
    assert len({job.source_id for job in jobs}) == 2
    assert jobs[0].source_id.startswith("rvc-")
    assert len(jobs[0].source_id.removeprefix("rvc-")) == 24


def test_parse_public_board_anchor_fallback_job_card():
    html = """
    <article class="vacancy-card">
      <a class="title" href="/skills/python">Python Developer @ JetBrains</a>
      <div class="meta">JetBrains удаленка 6д Европа 10K $-16K $</div>
    </article>
    """

    jobs = parse_public_board_html(html, source="jabka", base_url="https://jabka.work")

    assert len(jobs) == 1
    assert jobs[0].url == "https://jabka.work/skills/python"
    expected_id = "jabka-" + hashlib.sha256(
        source_common.canonicalize_job_url(jobs[0].url).encode("utf-8")
    ).hexdigest()[:24]
    assert jobs[0].source_id == expected_id
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


def test_public_board_source_dedupes_canonical_urls_across_pages():
    pages = {
        "https://app.rvc.global/page-1": (
            '<a href="/vacancy/view?id=1&amp;b=2&amp;utm_source=x">Backend Engineer</a>'
        ),
        "https://app.rvc.global/page-2": (
            '<a href="/vacancy/view?b=2&amp;id=1#top">Backend Engineer</a>'
        ),
        "https://app.rvc.global/page-3": (
            '<a href="/vacancy/view?b=2&amp;id=2">Backend Engineer</a>'
        ),
    }
    source = PublicJobBoardSource(
        {
            "paths": ["/page-1", "/page-2", "/page-3"],
            "query_params": [],
        },
        source_name="rvc",
        fetcher=pages.__getitem__,
    )

    jobs = source.collect({}, limit=10)

    assert len(jobs) == 2
    assert len({job.source_id for job in jobs}) == 2


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


def test_work_hunter_url_fallback_dedupe_preserves_meaningful_query(
    tmp_path,
    monkeypatch,
):
    class StaticCollector:
        def collect(self, profile, limit=None):
            return [
                Job(
                    source="rvc",
                    source_id="one-tracked",
                    url="https://app.rvc.global/vacancy/view?id=1&utm_source=x",
                    title="Backend Engineer",
                ),
                Job(
                    source="rvc",
                    source_id="one-equivalent",
                    url="https://app.rvc.global/vacancy/view?id=1#top",
                    title="Backend Engineer",
                ),
                Job(
                    source="rvc",
                    source_id="two",
                    url="https://app.rvc.global/vacancy/view?id=2",
                    title="Backend Engineer",
                ),
            ]

    app = WorkHunter(root=tmp_path)
    app.config["sources"]["rvc"]["enabled"] = True
    monkeypatch.setattr(
        app,
        "_collector",
        lambda source_name, source_config: StaticCollector(),
    )

    result = app.sync_sources(sources=["rvc"], limit=10)

    assert result["rvc"] == {"status": "ok", "count": 2}
    assert len(app.storage.list_jobs(source="rvc", limit=10)) == 2
