from __future__ import annotations

from types import SimpleNamespace

from work_hunter.models import Job
from work_hunter.services import WorkHunter, _ConfiguredHHCoverLetters


class _ResumeClient:
    def get_resume(self, resume_id: str):
        return {
            "id": resume_id,
            "title": "Python Backend Engineer",
            "first_name": "Иван",
            "last_name": "Иванов",
        }


def _context(*, mode: str, item_id: int = 1):
    return SimpleNamespace(
        item_id=item_id,
        account_id="default",
        vacancy_id="vac-1",
        resume_id="resume-1",
        cover_letter_mode=mode,
    )


def test_configured_cover_letters_render_template_ai_and_cache(monkeypatch, tmp_path):
    app = WorkHunter(root=tmp_path)
    app.config["profiles"]["default"].update(
        {
            "name": "Анна",
            "title": "Backend Engineer",
            "must_have_skills": ["Python", "PostgreSQL"],
        }
    )
    app.config["about"]["summary"] = "Разрабатываю backend-сервисы."
    application = app.config["sources"]["hh"]["autopilot"]["application"]
    application.update(
        {
            "cover_letter_mode": "template",
            "cover_letter_template": (
                "{Здравствуйте|Добрый день}, я {candidate_name}. "
                "Откликаюсь на {vacancy_name}; стек: {candidate_skills}."
            ),
            "cover_letter_max_characters": 500,
            "cover_letter_spintax": True,
            "reuse_saved_cover_letter": False,
        }
    )
    app.storage.upsert_job(
        Job(
            source="hh",
            source_id="vac-1",
            url="https://hh.ru/vacancy/vac-1",
            title="Senior Python Developer",
            company="Acme",
            description="Нужны Python и PostgreSQL.",
        )
    )
    monkeypatch.setattr(app, "_hh_client_for_account", lambda account: _ResumeClient())
    renderer = _ConfiguredHHCoverLetters(app)

    template_body = renderer.render(_context(mode="template"))
    cached_body = renderer.render(_context(mode="template"))

    assert template_body == cached_body
    assert template_body.startswith(("Здравствуйте", "Добрый день"))
    assert "Анна" in template_body
    assert "Senior Python Developer" in template_body
    assert "Python, PostgreSQL" in template_body
    assert len(app.storage.conn.execute("SELECT * FROM letters").fetchall()) == 1

    prompts: list[list[dict]] = []

    def fake_completion(messages, config):
        prompts.append(messages)
        return "Здравствуйте! Мой опыт Python подходит задачам вакансии."

    monkeypatch.setattr("work_hunter.services.chat_completion", fake_completion)
    application["cover_letter_mode"] = "ai"
    ai_body = renderer.render(_context(mode="ai", item_id=2))

    assert ai_body == "Здравствуйте! Мой опыт Python подходит задачам вакансии."
    assert "Нужны Python и PostgreSQL" in prompts[0][1]["content"]
    assert "Разрабатываю backend-сервисы" in prompts[0][1]["content"]
    assert len(app.storage.conn.execute("SELECT * FROM letters").fetchall()) == 2
