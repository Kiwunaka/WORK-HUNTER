from __future__ import annotations

from work_hunter.models import Job
from work_hunter.services import WorkHunter


class StaticCollector:
    def __init__(self, jobs: list[Job]):
        self.jobs = jobs
        self.limits: list[int | None] = []

    def collect(self, profile, limit=None):
        self.limits.append(limit)
        return self.jobs[:limit] if limit is not None else list(self.jobs)


def test_sync_sources_caps_total_unique_results_and_dedupes_between_sources(tmp_path):
    collectors = {
        "hh": StaticCollector(
            [
                Job(source="hh", source_id="1", url="https://hh.ru/vacancy/1", title="Python", company="Acme"),
                Job(source="hh", source_id="2", url="https://hh.ru/vacancy/2", title="Django", company="Acme"),
            ]
        ),
        "habr": StaticCollector(
            [
                Job(source="habr", source_id="h1", url="https://career.habr.com/vacancies/1", title="Python", company="Acme"),
                Job(source="habr", source_id="h2", url="https://career.habr.com/vacancies/2", title="FastAPI", company="Beta"),
            ]
        ),
        "geekjob": StaticCollector(
            [
                Job(source="geekjob", source_id="g1", url="https://geekjob.ru/vacancy/1", title="Go", company="Gamma"),
            ]
        ),
    }

    class App(WorkHunter):
        def _collector(self, source_name, source_config, *, backend=None):
            return collectors[source_name]

    app = App(root=tmp_path)
    app.config["research"]["max_results"] = 3

    result = app.sync_sources(sources=["hh", "habr", "geekjob"])

    assert result["hh"]["count"] == 2
    assert result["habr"]["count"] == 1
    assert result["geekjob"]["count"] == 0
    jobs = app.storage.list_jobs(limit=10)
    assert len(jobs) == 3
    assert {job.title for job in jobs} == {"Python", "Django", "FastAPI"}
