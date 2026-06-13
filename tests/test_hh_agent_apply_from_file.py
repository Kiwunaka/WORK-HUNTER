from __future__ import annotations

import json

from work_hunter.cli import main as cli_main
from work_hunter.hh_agent.apply_from_file import extract_hh_vacancy_id, load_apply_from_file
from work_hunter.services import WorkHunter


def test_extract_hh_vacancy_id_from_common_values():
    assert extract_hh_vacancy_id("123456") == "123456"
    assert extract_hh_vacancy_id("https://hh.ru/vacancy/987654?from=search") == "987654"
    assert extract_hh_vacancy_id("https://hh.ru/applicant/vacancy_response?vacancyId=456789") == "456789"
    assert extract_hh_vacancy_id("not-a-vacancy") == ""


def test_load_apply_from_file_parses_csv_tsv_enabled_and_resume(tmp_path):
    csv_path = tmp_path / "vacancies.csv"
    csv_path.write_text(
        "\n".join(
            [
                "enabled,url,resume_id,title,letter",
                "true,https://hh.ru/vacancy/100,res-1,Python,\"Hi {title}\"",
                "false,https://hh.ru/vacancy/200,res-2,Skipped,Nope",
                "yes,300,,Data,Hello",
            ]
        ),
        encoding="utf-8",
    )
    tsv_path = tmp_path / "vacancies.tsv"
    tsv_path.write_text("vacancy_id\ttitle\n400\tGo\n", encoding="utf-8")

    csv_rows = load_apply_from_file(csv_path)
    tsv_rows = load_apply_from_file(tsv_path)

    assert [row.vacancy_id for row in csv_rows] == ["100", "200", "300"]
    assert [row.enabled for row in csv_rows] == [True, False, True]
    assert csv_rows[0].resume_id == "res-1"
    assert csv_rows[0].letter == "Hi {title}"
    assert csv_rows[0].row_key == "row-1"
    assert tsv_rows[0].vacancy_id == "400"


def test_apply_hh_from_file_dry_run_creates_jobs_and_state(tmp_path):
    csv_path = tmp_path / "vacancies.csv"
    csv_path.write_text(
        "\n".join(
            [
                "enabled,vacancy_id,title,company,resume_id,letter",
                "true,100,Python,Acme,res-row,Hello row",
                "false,200,SkipCo,SkipCo,,No",
                "true,,Broken,NoId,,No",
            ]
        ),
        encoding="utf-8",
    )
    app = WorkHunter(root=tmp_path)

    result = app.apply_hh_from_file(csv_path, dry_run=True, resume_id="res-default", limit=10)

    assert result["status"] == "planned"
    assert result["count"] == 1
    assert result["rows"][0]["status"] == "planned"
    assert result["rows"][0]["vacancy_id"] == "100"
    assert result["rows"][0]["resume_id"] == "res-row"
    assert result["rows"][1]["status"] == "skipped"
    assert result["rows"][2]["status"] == "invalid"
    jobs = app.storage.list_jobs(source="hh", limit=10)
    assert [job.source_id for job in jobs] == ["100"]
    state = app.storage.list_hh_apply_from_file_state()
    assert state[0]["source_path"] == str(csv_path)
    assert state[0]["row_key"] == "row-1"
    assert state[0]["status"] == "planned"


def test_apply_hh_from_file_real_apply_requires_confirm_and_uses_existing_confirm_path(tmp_path):
    csv_path = tmp_path / "vacancies.csv"
    csv_path.write_text("vacancy_id,title,company\n100,Python,Acme\n", encoding="utf-8")
    app = WorkHunter(root=tmp_path)
    calls: list[dict] = []

    def fake_confirm_apply(job_id: int, **kwargs):
        calls.append({"job_id": job_id, **kwargs})
        return {"status": "applied", "job_id": job_id, "resume_id": kwargs.get("resume_id")}

    app.confirm_apply = fake_confirm_apply  # type: ignore[method-assign]

    blocked = app.apply_hh_from_file(csv_path, dry_run=False, confirm=False, resume_id="res-1")
    result = app.apply_hh_from_file(csv_path, dry_run=False, confirm=True, resume_id="res-1")

    assert blocked["status"] == "blocked"
    assert calls == [{"job_id": result["rows"][0]["job_id"], "resume_id": "res-1", "letter": "", "confirm": True}]
    assert result["status"] == "completed"
    assert result["rows"][0]["status"] == "applied"
    state = app.storage.list_hh_apply_from_file_state()
    assert state[-1]["status"] == "applied"


def test_apply_hh_from_file_cli_outputs_report(tmp_path, capsys):
    csv_path = tmp_path / "vacancies.csv"
    csv_path.write_text("vacancy_id,title\n100,Python\n", encoding="utf-8")

    cli_main(["--root", str(tmp_path), "hh-apply-from-file", str(csv_path), "--resume-id", "res-1"])
    payload = json.loads(capsys.readouterr().out)

    assert payload["status"] == "planned"
    assert payload["rows"][0]["vacancy_id"] == "100"
