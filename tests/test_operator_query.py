from __future__ import annotations

import json

import pytest

from work_hunter.cli import main as cli_main
from work_hunter.models import Job
from work_hunter.storage import Storage


def test_readonly_query_returns_rows(tmp_path):
    storage = Storage(tmp_path / "db.sqlite3")
    storage.upsert_job(Job(source="hh", source_id="1", url="u", title="Python", company="Acme"))

    rows = storage.query_readonly("SELECT title, company FROM jobs")

    assert rows == [{"title": "Python", "company": "Acme"}]


def test_readonly_query_rejects_mutating_sql(tmp_path):
    storage = Storage(tmp_path / "db.sqlite3")

    with pytest.raises(ValueError):
        storage.query_readonly("DELETE FROM jobs")


def test_query_cli_outputs_json(tmp_path, capsys):
    storage = Storage(tmp_path / ".work-hunter" / "work_hunter.sqlite3")
    storage.upsert_job(Job(source="hh", source_id="1", url="u", title="Python", company="Acme"))

    cli_main(["--root", str(tmp_path), "query", "SELECT title FROM jobs"])

    payload = json.loads(capsys.readouterr().out)
    assert payload == [{"title": "Python"}]
