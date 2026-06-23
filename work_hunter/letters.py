from __future__ import annotations

import json
import logging
from typing import Any

from .ai import backend_requires_api_key, chat_completion
from .models import Job

logger = logging.getLogger(__name__)


HUMAN_COVER_LETTER_TEMPLATES: dict[str, dict[str, str]] = {
    "A": {"name": "short", "label": "Template A: short"},
    "B": {"name": "technical", "label": "Template B: technical"},
    "C": {"name": "friendly", "label": "Template C: friendly"},
    "D": {"name": "confident", "label": "Template D: confident"},
}


def human_cover_letter_variants(
    job: Job,
    profile: dict[str, Any],
    *,
    selected_template: str = "A",
    use_for_campaign: bool = False,
) -> dict[str, Any]:
    """Build deterministic human-style cover letter variants."""
    selected = selected_template if selected_template in HUMAN_COVER_LETTER_TEMPLATES else "A"
    variants = [
        {
            "template": template,
            "name": meta["name"],
            "label": meta["label"],
            "body": _human_cover_letter_body(job, profile, template),
            "style_rules": [
                "short",
                "specific",
                "2-4 paragraphs",
                "1-2 concrete matches",
                "confident CTA",
            ],
        }
        for template, meta in HUMAN_COVER_LETTER_TEMPLATES.items()
    ]
    campaign_letter = next(variant for variant in variants if variant["template"] == selected)
    return {
        "status": "ready",
        "job_id": job.id,
        "source": job.source,
        "selected_template": selected,
        "use_for_campaign": bool(use_for_campaign),
        "variants": variants,
        "campaign_letter": campaign_letter,
        "forbidden_phrases": [
            "Я являюсь",
            "имею богатый опыт",
            "позвольте представиться",
        ],
    }


def _human_cover_letter_body(job: Job, profile: dict[str, Any], template: str) -> str:
    title = job.title or "роль"
    company = job.company or "команда"
    skills = _letter_matches(job, profile)
    primary = skills[0] if skills else "практическими задачами"
    secondary = ", ".join(skills[1:3]) if len(skills) > 1 else primary
    recent = _recent_work(profile, primary=primary, secondary=secondary)
    cta = f"Готов обсудить, чем могу быть полезен {company}. Резюме приложил."

    if template == "B":
        paragraphs = [
            f"Привет! Заинтересовала роль {title}: здесь важны {primary} и {secondary}, это близко к моему текущему стеку.",
            f"Пример из опыта: {recent}. По описанию вижу совпадение с задачами по {secondary}.",
            cta,
        ]
    elif template == "C":
        paragraphs = [
            f"Привет! Роль {title} в {company} выглядит хорошим совпадением по {primary} и {secondary}.",
            f"Мне комфортен формат, где нужно спокойно разбираться в продукте, писать понятный код и договариваться с командой. Из практики: {recent}.",
            "Буду рад коротко созвониться и понять, где мой опыт может быть полезен.",
        ]
    elif template == "D":
        paragraphs = [
            f"Привет! Хочу откликнуться на {title}: у меня есть практический опыт с {primary} и {secondary}.",
            f"Могу быстро включиться в backend-задачи, где важны надежные API, аккуратная работа с данными и понятная коммуникация. Пример из опыта: {recent}.",
            cta,
        ]
    else:
        paragraphs = [
            f"Привет! Заинтересовала роль {title}, потому что у меня есть практический опыт с {primary} и {secondary}.",
            f"Пример из опыта: {recent}. По описанию вижу совпадение с 1-2 ключевыми задачами роли.",
            cta,
        ]
    return "\n\n".join(_trim_sentence(paragraph) for paragraph in paragraphs if paragraph.strip())


def _letter_matches(job: Job, profile: dict[str, Any]) -> list[str]:
    skills = [
        str(skill).strip()
        for skill in (profile.get("must_have_skills") or []) + (profile.get("nice_to_have_skills") or [])
        if str(skill).strip()
    ]
    text = f"{job.title} {job.description}".lower()
    matched = [skill for skill in skills if skill.lower() in text]
    if not matched:
        matched = skills[:3]
    seen: set[str] = set()
    result: list[str] = []
    for skill in matched:
        key = skill.lower()
        if key not in seen:
            seen.add(key)
            result.append(skill)
    return result[:3]


def _recent_work(profile: dict[str, Any], *, primary: str, secondary: str) -> str:
    summary = str(profile.get("summary") or "").strip().rstrip(".")
    if summary and _safe_summary_for_letter(summary):
        first_sentence = summary.split(".")[0].strip()
        if first_sentence:
            return _trim_sentence(first_sentence).removeprefix("I ").removeprefix("Я ")
    return f"делал backend-функции на {primary}, связывал их с {secondary} и доводил изменения до понятного результата"


def _trim_sentence(text: str) -> str:
    cleaned = " ".join(str(text).split())
    return cleaned.rstrip(".") + "."


def _safe_summary_for_letter(summary: str) -> bool:
    lowered = summary.lower()
    blocked = [
        "access_token",
        "refresh_token",
        "client_secret",
        "authorization:",
        "cookie:",
        "invented ",
        "fake ",
        "unsupported",
        "unconfirmed",
    ]
    return not any(marker in lowered for marker in blocked)


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

    if backend_requires_api_key(ai_config) and (not api_key or not base_url or not model):
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
