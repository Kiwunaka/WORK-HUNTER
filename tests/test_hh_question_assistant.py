from __future__ import annotations

from work_hunter.services import WorkHunter


class FakeHHQuestionClient:
    submitted: list[dict] = []

    def __init__(self, config):
        self.config = config

    def has_token(self):
        return True

    def get_vacancy(self, vacancy_id: str):
        return {
            "id": vacancy_id,
            "name": "Python Backend",
            "description": "FastAPI, PostgreSQL, async services",
            "test": {
                "required": True,
                "questions": [
                    {"id": "q1", "text": "Почему хотите работать у нас?", "type": "text"},
                    {"id": "q2", "text": "Опишите опыт с FastAPI", "type": "text"},
                ],
            },
        }

    def get_resume(self, resume_id: str):
        return {
            "id": resume_id,
            "title": "Python Backend",
            "skills": "Python, FastAPI, PostgreSQL",
            "experience": [{"position": "Backend Engineer", "description": "Built APIs"}],
        }

    def submit_vacancy_test(self, vacancy_id: str, resume_id: str, answers: list[dict]):
        self.submitted.append(
            {"vacancy_id": vacancy_id, "resume_id": resume_id, "answers": answers}
        )
        return {"status": "submitted", "count": len(answers)}


def test_detect_hh_question_requirements_from_payloads(tmp_path):
    app = WorkHunter(root=tmp_path)

    assert app.detect_hh_question_requirements({"error": "test_required"})["required"] is True
    assert app.detect_hh_question_requirements({"has_test": True})["required"] is True
    assert app.detect_hh_question_requirements({"test": {"required": True}})["required"] is True
    assert app.detect_hh_question_requirements({"test": {"questions": [{"id": "q"}]}})["count"] == 1


def test_plan_hh_test_answers_drafts_suggestions(monkeypatch, tmp_path):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHQuestionClient)

    def fake_chat_completion(messages, ai_config):
        assert "Python Backend" in messages[-1]["content"]
        return "Короткий уверенный ответ про FastAPI и релевантный опыт."

    monkeypatch.setattr("work_hunter.services.chat_completion", fake_chat_completion)
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"

    result = app.plan_hh_test_answers("vac-1", resume_id="resume-1")

    assert result["status"] == "planned"
    assert result["requires_confirmation"] is True
    assert result["count"] == 2
    assert result["answers"][0]["question_id"] == "q1"
    assert "FastAPI" in result["answers"][1]["answer"]


def test_confirm_hh_test_answers_requires_confirm_before_submit(monkeypatch, tmp_path):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHQuestionClient)
    monkeypatch.setattr(
        "work_hunter.services.chat_completion",
        lambda messages, ai_config: "Ответ с учетом вакансии и резюме.",
    )
    FakeHHQuestionClient.submitted = []
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"

    blocked = app.confirm_hh_test_answers("vac-1", resume_id="resume-1", confirm=False)
    sent = app.confirm_hh_test_answers("vac-1", resume_id="resume-1", confirm=True)

    assert blocked["status"] == "blocked"
    assert FakeHHQuestionClient.submitted == [
        {
            "vacancy_id": "vac-1",
            "resume_id": "resume-1",
            "answers": [
                {"question_id": "q1", "answer": "Ответ с учетом вакансии и резюме."},
                {"question_id": "q2", "answer": "Ответ с учетом вакансии и резюме."},
            ],
        }
    ]
    assert sent["status"] == "submitted"
    assert sent["result"]["count"] == 2
