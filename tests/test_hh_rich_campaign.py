from __future__ import annotations

import json

from work_hunter.cli import main as cli_main
from work_hunter.services import WorkHunter


class FakeHHRichSearchClient:
    search_calls: list[dict] = []

    def __init__(self, config):
        self.config = config

    def has_token(self):
        return True

    def search_vacancies(self, params: dict):
        self.search_calls.append(params)
        return [
            {
                "id": "vac-1",
                "name": "Python Backend",
                "alternate_url": "https://hh.ru/vacancy/vac-1",
                "employer": {"name": "Acme"},
                "salary": {"from": 250000, "to": 320000, "currency": "RUR"},
                "area": {"name": "Москва"},
                "schedule": {"id": "remote"},
                "snippet": {"requirement": "Python", "responsibility": "Backend"},
                "published_at": "2026-06-01T10:00:00+03:00",
            },
            {
                "id": "vac-2",
                "name": "API Engineer",
                "alternate_url": "https://hh.ru/vacancy/vac-2",
                "employer": {"name": "Beta"},
                "salary": None,
                "area": {"name": "Санкт-Петербург"},
                "schedule": {"id": "fullDay"},
                "snippet": {"requirement": "HTTP", "responsibility": "API"},
            },
        ]

    def get_vacancy(self, vacancy_id: str):
        return {
            "id": vacancy_id,
            "alternate_url": f"https://hh.ru/vacancy/{vacancy_id}",
            "response_letter_required": False,
            "has_test": False,
        }

    def suitable_resumes(self, vacancy_id: str):
        return [{"id": "resume-1"}]

    def list_resumes(self):
        return [{"id": "resume-1"}]


def test_plan_hh_search_campaign_fetches_with_rich_filters(monkeypatch, tmp_path):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHRichSearchClient)
    FakeHHRichSearchClient.search_calls = []
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"

    result = app.plan_hh_search_campaign(
        text="python backend",
        area=["1", "2"],
        professional_role=["96"],
        industry=["7"],
        salary=250000,
        schedule="remote",
        experience="between3And6",
        employment=["full"],
        date_from="2026-06-01",
        date_to="2026-06-09",
        search_field=["name", "company_name"],
        employer_id=["123"],
        excluded_employer_id=["456"],
        only_with_salary=True,
        limit=2,
        resume_id="resume-1",
    )

    assert FakeHHRichSearchClient.search_calls == [
        {
            "text": "python backend",
            "area": ["1", "2"],
            "professional_role": ["96"],
            "industry": ["7"],
            "salary": 250000,
            "schedule": "remote",
            "experience": "between3And6",
            "employment": ["full"],
            "date_from": "2026-06-01",
            "date_to": "2026-06-09",
            "search_field": ["name", "company_name"],
            "employer_id": ["123"],
            "excluded_employer_id": ["456"],
            "only_with_salary": True,
            "per_page": 2,
            "page": 0,
        }
    ]
    assert result["status"] == "planned"
    assert result["counts"]["ready"] == 2
    jobs = app.storage.list_jobs(limit=10, source="hh")
    assert {job.source_id for job in jobs} == {"vac-1", "vac-2"}
    assert app.storage.list_hh_campaign_items(result["id"])[0].resume_id == "resume-1"


def test_hh_search_campaign_cli_accepts_filter_arguments(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHRichSearchClient)
    FakeHHRichSearchClient.search_calls = []
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"
    app.save_config(app.config)

    cli_main(
        [
            "--root",
            str(tmp_path),
            "hh-search-campaign-plan",
            "--text",
            "python",
            "--area",
            "1",
            "--professional-role",
            "96",
            "--salary",
            "250000",
            "--schedule",
            "remote",
            "--experience",
            "between3And6",
            "--only-with-salary",
            "--limit",
            "1",
            "--resume-id",
            "resume-1",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "planned"
    assert payload["counts"]["ready"] == 2
    assert FakeHHRichSearchClient.search_calls[0]["text"] == "python"
    assert FakeHHRichSearchClient.search_calls[0]["area"] == ["1"]
    assert FakeHHRichSearchClient.search_calls[0]["professional_role"] == ["96"]
    assert FakeHHRichSearchClient.search_calls[0]["only_with_salary"] is True
