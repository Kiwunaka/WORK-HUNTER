"""Local launch checks. No HH requests and no paid AI calls."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from .candidate import candidate_facts, content_hash, validate_about
from .config import MASK, active_profile
from .hh_autopilot.config import parse_autopilot_settings

if TYPE_CHECKING:
    from .services import WorkHunter


def launch_readiness(app: WorkHunter) -> dict[str, Any]:
    profile = active_profile(app.config)
    about = app.config.get("about") or {}
    checks: list[dict[str, str]] = []

    def check(code: str, ok: bool, message: str) -> None:
        checks.append({"code": code, "status": "ok" if ok else "blocked", "message": message})

    check("contacts", bool(profile.get("name") and (profile.get("email") or profile.get("phone"))),
          "Заполните имя и хотя бы один контакт для связи")
    try:
        validate_about(about)
        facts_valid = bool(about.get("summary") or about.get("experience"))
    except ValueError:
        facts_valid = False
    check("candidate_facts", facts_valid, "Заполните базу реального опыта")
    resumes = app.storage.list_resumes(app.active_profile_id())
    active = next((resume for resume in resumes if resume.is_active), None)
    check("active_resume", active is not None and bool(active.body.strip()), "Выберите основную версию резюме с текстом")
    warnings: list[str] = []
    if active is not None:
        if active.facts_hash and active.facts_hash != content_hash(candidate_facts(profile, about)):
            warnings.append("База опыта изменилась после создания резюме — проверьте его текст")
        if active.file_path:
            path = Path(active.file_path).expanduser()
            if not path.is_absolute():
                path = app.root / path
            check("resume_file", path.is_file(), "Файл выбранного резюме должен существовать")
        else:
            check("resume_file", False, "Экспортируйте выбранное резюме в PDF или DOCX")
    ai = app.config.get("ai") or {}
    backend = str(ai.get("backend") or "direct")
    ai_configured = backend == "opencode" or (backend == "direct" and all(
        ai.get(key) and ai.get(key) != MASK for key in ("api_key", "base_url", "model")
    ))
    check("ai_config", bool(ai_configured), "Укажите поддерживаемый AI backend, модель и действующий ключ")
    model = str(ai.get("opencode_model" if backend == "opencode" else "model") or "")
    usage = app.storage.ai_usage_report(app.active_profile_id())
    latest = next((item for item in usage["recent"] if item["model"] == model and item["backend"] == backend), None)
    ai_status = "not_probed" if latest is None else latest["status"]
    check("ai_generation", ai_status == "ok", "Проверьте успешную генерацию через сохранённый AI API")
    if ai_status != "ok":
        warnings.append("Проверьте генерацию AI: " + (latest["error_code"] if latest else "запрос ещё не выполнялся"))
    try:
        settings = parse_autopilot_settings(app.config)
        check("autopilot_config", True, "Конфигурация автопилота корректна")
        start = settings.schedule["start"]
        end = settings.schedule["end"]
        minutes = lambda value: int(value[:2]) * 60 + int(value[3:])
        window_minutes = minutes(end) - minutes(start)
        runs = window_minutes // settings.schedule["interval_minutes"] + 1
        capacity = min(settings.limits.daily_success, runs * settings.limits.per_run_success)
        limits = {"daily": settings.limits.daily_success, "per_run": settings.limits.per_run_success,
                  "scheduled_upper_bound": capacity, "timezone": settings.timezone,
                  "schedule": settings.schedule,
                  "delay_minutes_for_200": [round(199 * value / 60) for value in (
                      settings.limits.send_delay_min_seconds, settings.limits.send_delay_max_seconds,
                  )],
                  "note": "Верхняя граница по квотам; время запросов, форм и ограничений HH уменьшает объём"}
        if capacity < 100:
            warnings.append(f"Текущие квоты допускают максимум {capacity} откликов за рабочий день")
        controls = [{"account": item.profile_id, "enabled": item.enabled,
                     "paused": item.paused, "resume_queries": item.resume_queries} for item in settings.accounts]
    except ValueError as exc:
        check("autopilot_config", False, str(exc))
        limits, controls = {}, []
    hh = app.hh_config()
    token_configured = bool(hh.get("access_token") and hh.get("access_token") != MASK)
    return {"status": "local_ready" if all(item["status"] == "ok" for item in checks) else "blocked",
            "live_ready": False, "checks": checks, "warnings": warnings,
            "ai": {"status": ai_status, "last_request": latest}, "limits": limits,
            "accounts": controls, "hh": {"token_configured": token_configured, "live_verified": False},
            "pending_questions": len(app.storage.list_hh_pending_messages(status="pending")),
            "live_checks_remaining": ["Вход в HH", "Проверка опубликованных резюме и их привязок",
                                      "Один контрольный отклик и проверка истории HH", "Включение автопилота"]}
