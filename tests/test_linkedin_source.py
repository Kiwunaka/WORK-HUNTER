from __future__ import annotations

import urllib.parse

from work_hunter.sources.linkedin import (
    LinkedInSource,
    linkedin_search_url,
    parse_linkedin_search_html,
)


SEARCH_HTML = """
<li>
  <div class="base-card job-search-card" data-entity-urn="urn:li:jobPosting:4435681577">
    <a class="base-card__full-link" href="https://ru.linkedin.com/jobs/view/python-engineer-4435681577?trackingId=x">
      <span class="sr-only">Python Engineer</span>
    </a>
    <div class="base-search-card__info">
      <h3 class="base-search-card__title">Python Engineer</h3>
      <h4 class="base-search-card__subtitle"><a>Acme</a></h4>
      <span class="job-search-card__location">Moscow, Russia</span>
      <time class="job-search-card__listdate" datetime="2026-08-05">2 days ago</time>
    </div>
  </div>
</li>
"""


def test_parse_linkedin_guest_search_card() -> None:
    jobs = parse_linkedin_search_html(SEARCH_HTML)

    assert len(jobs) == 1
    assert jobs[0].source == "linkedin"
    assert jobs[0].source_id == "4435681577"
    assert jobs[0].title == "Python Engineer"
    assert jobs[0].company == "Acme"
    assert jobs[0].location == "Moscow, Russia"
    assert jobs[0].published_at == "2026-08-05"
    assert jobs[0].url == "https://ru.linkedin.com/jobs/view/python-engineer-4435681577"


def test_linkedin_search_url_maps_filters() -> None:
    url = linkedin_search_url(
        query="python backend",
        location="Remote",
        start=25,
        config={
            "geo_id": "101728296",
            "date_posted_seconds": 86400,
            "remote_only": True,
        },
    )
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)

    assert query == {
        "keywords": ["python backend"],
        "location": ["Remote"],
        "start": ["25"],
        "geoId": ["101728296"],
        "f_TPR": ["r86400"],
        "f_WT": ["2"],
    }


def test_linkedin_source_collects_queries_and_deduplicates() -> None:
    calls: list[str] = []

    def fetcher(url: str) -> str:
        calls.append(url)
        return SEARCH_HTML

    source = LinkedInSource(
        {"pages": 1, "locations": ["Russia"]},
        fetcher=fetcher,
    )
    jobs = source.collect({"queries": ["python", "backend"]})

    assert len(jobs) == 1
    assert len(calls) == 2


def test_remote_location_sets_remote_filter_and_job_flag() -> None:
    calls: list[str] = []

    def fetcher(url: str) -> str:
        calls.append(url)
        return SEARCH_HTML

    source = LinkedInSource(
        {"pages": 1, "locations": ["Remote"], "remote_only": False},
        fetcher=fetcher,
    )

    jobs = source.collect({"queries": ["python"]})

    assert jobs[0].remote is True
    assert urllib.parse.parse_qs(urllib.parse.urlsplit(calls[0]).query)["f_WT"] == ["2"]
