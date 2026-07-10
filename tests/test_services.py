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
