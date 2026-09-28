from __future__ import annotations

from types import SimpleNamespace

from work_hunter.models import Job
from work_hunter.services import WorkHunter, _ConfiguredHHCoverLetters


class _ResumeClient:
    def get_vacancy(self, vacancy_id: str):
        return {
            "id": vacancy_id,
            "name": "Senior Python Developer",
            "alternate_url": f"https://hh.ru/vacancy/{vacancy_id}",
            "employer": {"name": "Acme"},
            "area": {"name": "Москва"},
            "schedule": {"id": "remote"},
            "description": "<p>Нужны Python, PostgreSQL, RAG, embeddings и evals.</p>",
        }

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


def test_cover_letter_full_description_read_solves_captcha(monkeypatch, tmp_path):
    from work_hunter.hh_transport.errors import HHForbiddenError

    app = WorkHunter(root=tmp_path)
    client = _ResumeClient()
    get_vacancy = client.get_vacancy
    solved = []
    reads = []

    def load(vacancy_id):
        reads.append(vacancy_id)
        if not solved:
            raise HHForbiddenError("captcha required", status_code=403, code="captcha_required")
        return get_vacancy(vacancy_id)

    client.get_vacancy = load
    monkeypatch.setattr(app, "_hh_client_for_account", lambda _: client)
    renderer = _ConfiguredHHCoverLetters(app, captcha_solver=lambda error: solved.append(error.code))

    assert renderer.render(_context(mode="template"))
    assert solved == ["captcha_required"]
    assert reads == ["vac-1", "vac-1", "vac-1"]


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
    app.config["about"]["all_skills"] = ["Python", "PostgreSQL"]
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
    assert "Нужны Python, PostgreSQL, RAG, embeddings и evals" in prompts[0][1]["content"]
    assert "Нужны Python и PostgreSQL." not in prompts[0][1]["content"]
    assert "Разрабатываю backend-сервисы" in prompts[0][1]["content"]
    assert len(app.storage.conn.execute("SELECT * FROM letters").fetchall()) == 2

    # A READY item's older draft must be regenerated under the current prompt.
    app.config["ai"]["cover_letters"]["message_prompt"] = (
        "Новая инструкция для {vacancy_name}: {vacancy_description}"
    )
    assert renderer.render(_context(mode="ai", item_id=2)) == ai_body
    assert len(prompts) == 2
    assert prompts[1][1]["content"].startswith("Новая инструкция для Senior Python Developer")
    assert len(app.storage.conn.execute("SELECT * FROM letters").fetchall()) == 3

    # Preparation owns its SQLite connection; sender reuses the exact saved
    # item/policy draft without making another model call.
    from concurrent.futures import ThreadPoolExecutor
    from copy import deepcopy

    from work_hunter import services
    from work_hunter.config import database_path
    from work_hunter.storage import Storage

    original_job = services._hh_job_from_vacancy_payload
    fetched_at = "2026-09-27T10:00:00+00:00"

    def fetched_job(payload):
        job = original_job(payload)
        job.fetched_at = fetched_at
        return job

    monkeypatch.setattr(services, "_hh_job_from_vacancy_payload", fetched_job)

    def prepare(item_id):
        worker = WorkHunter(root=tmp_path)
        worker.config = deepcopy(app.config)
        worker._storage = Storage(database_path(tmp_path))
        worker._hh_client_for_account = lambda account: _ResumeClient()
        try:
            return _ConfiguredHHCoverLetters(worker).render(_context(mode="ai", item_id=item_id))
        finally:
            worker.storage.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        drafts = list(pool.map(prepare, (3, 4)))
    calls_before_send = len(prompts)
    fetched_at = "2026-09-27T10:01:00+00:00"
    assert [renderer.render(_context(mode="ai", item_id=i)) for i in (3, 4)] == drafts
    assert len(prompts) == calls_before_send

    # A real change to requirements still invalidates the draft, including text
    # beyond the old 6,000-character prompt truncation.
    old_vacancy = _ResumeClient.get_vacancy

    def changed_vacancy(self, vacancy_id):
        payload = old_vacancy(self, vacancy_id)
        payload["description"] += "<p>О компании. </p>" * 600
        payload["description"] += "<p>Обязательно: оценка качества RAG.</p>"
        return payload

    monkeypatch.setattr(_ResumeClient, "get_vacancy", changed_vacancy)
    renderer.render(_context(mode="ai", item_id=3))
    assert len(prompts) == calls_before_send + 1
    assert "Обязательно: оценка качества RAG." in prompts[-1][1]["content"]

    # A draft that volunteers missing skills is rewritten before it can be sent.
    revisions: list[list[dict]] = []
    answers = iter(
        (
            "С PostgreSQL в проде не работал, но готов учиться.",
            "Здравствуйте! Разрабатываю Python-сервисы и SQL-интеграции.",
        )
    )

    def revise_completion(messages, config):
        revisions.append(messages)
        return next(answers)

    monkeypatch.setattr("work_hunter.services.chat_completion", revise_completion)
    assert renderer.render(_context(mode="ai", item_id=5)).startswith("Здравствуйте!")
    assert len(revisions) == 2
    assert "Перепиши письмо с нуля" in revisions[1][1]["content"]
