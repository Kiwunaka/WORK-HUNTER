from __future__ import annotations

import logging
from typing import Any

from .ai_backends import chat_completion
from .models import Job

logger = logging.getLogger(__name__)


def draft_cover_letter(job: Job, profile: dict[str, Any]) -> str:
    """Quick template-based cover letter (no AI)."""
    name = str(profile.get("name") or "Кандидат")
    skills = [str(skill) for skill in profile.get("must_have_skills") or []]
    nice = [str(skill) for skill in profile.get("nice_to_have_skills") or []]
    skills_text = ", ".join((skills + nice)[:6]) or "релевантным стеком"
    company = job.company or "вашей компании"
    title = job.title or "вакансии"

    return (
        f"Здравствуйте! Меня зовут {name}.\n\n"
        f"Хочу откликнуться на позицию {title} в {company}. "
        f"По описанию вижу хороший матч с моим опытом: {skills_text}. "
        "Мне близок формат, где нужно быстро разбираться в задачах, аккуратно "
        "доводить решения до результата и общаться с командой без лишней воды.\n\n"
        "Буду рад обсудить, чем могу быть полезен, и ответить на вопросы по опыту."
    )


def draft_cover_letter_ai(
    job: Job,
    profile: dict[str, Any],
    about: dict[str, Any],
    ai_config: dict[str, Any],
) -> str:
    """Generate a cover letter using OpenRouter or any OpenAI-compatible API."""
    api_key = ai_config.get("api_key", "")
    base_url = ai_config.get("base_url", "")
    model = ai_config.get("model", "")

    backend = str(ai_config.get("backend") or "direct").lower()
    if backend != "opencode" and (not api_key or not base_url or not model):
        logger.warning("AI config incomplete, falling back to template")
        return draft_cover_letter(job, profile)

    name = profile.get("name", "Кандидат")
    title_role = profile.get("title", "Developer")
    summary = about.get("summary", "")
    skills = about.get("all_skills", [])
    experience_blocks = about.get("experience", [])

    experience_text = ""
    for exp in experience_blocks[:4]:
        role = exp.get("role", "")
        project = exp.get("project", "")
        details = exp.get("details", [])
        tech = exp.get("tech", [])
        experience_text += f"\n• {role} — {project}\n"
        for detail in details[:3]:
            experience_text += f"  - {detail}\n"
        if tech:
            experience_text += f"  Технологии: {', '.join(tech)}\n"

    system_prompt = (
        "Ты — помощник по написанию сопроводительных писем для IT-специалистов. "
        "Пиши кратко, по делу, без воды. Не используй канцеляризмы и штампы. "
        "Тон: уверенный профессионал, но дружелюбный. "
        "Длина: 4-6 предложений. Язык: русский, если вакансия на русском, "
        "иначе английский. "
        "НЕ выдумывай опыт, используй ТОЛЬКО то что указано в контексте."
    )

    user_prompt = f"""Напиши сопроводительное письмо для отклика на вакансию.

ВАКАНСИЯ:
- Позиция: {job.title}
- Компания: {job.company or 'не указана'}
- Описание: {job.description[:800] if job.description else 'нет описания'}
- Зарплата: {job.salary_text or 'не указана'}
- Локация: {job.location or 'не указана'}
- Удалёнка: {'да' if job.remote else 'нет/не указано'}

МОЙ ПРОФИЛЬ:
- Имя: {name}
- Целевая роль: {title_role}
- Резюме: {summary}
- Ключевые навыки: {', '.join(skills[:15])}

МОЙ ОПЫТ:
{experience_text}

Требования к письму:
1. Начни с приветствия и имени
2. Кратко объясни, почему эта вакансия интересна
3. Приведи 2-3 конкретных примера релевантного опыта из моего профиля
4. Закончи готовностью обсудить детали
5. Не используй слова "уважаемый", "с уважением", "позвольте представиться"
"""

    try:
        return chat_completion(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            ai_config,
        )
    except Exception as exc:
        logger.error("AI cover letter generation failed: %s", exc)
        return draft_cover_letter(job, profile)
