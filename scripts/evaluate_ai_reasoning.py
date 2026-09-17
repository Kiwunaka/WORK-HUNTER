"""Paid, manual comparison of OpenRouter models on synthetic Work Hunter tasks."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from work_hunter.candidate import candidate_prompt
from work_hunter.candidate_fit import FIT_PROMPT, parse_fit
from work_hunter.candidate_reply import (
    REPLY_INSTRUCTION,
    MissingCandidateFacts,
    parse_candidate_reply,
)
from work_hunter.llm.structured import StructuredParseError
from work_hunter.services import WorkHunter

MODELS = [
    "meta/muse-spark-1.3-contributor",
    "z-ai/glm-5.3-flash",
    "openai/gpt-5.6-luna",
    "deepseek/deepseek-v4-pro-0813",
    "tencent/hy4-preview",
]
FACTS = {
    "summary": "BI-аналитик, 4 года коммерческого опыта",
    "all_skills": ["SQL", "Power BI"],
    "experience": [{"role": "BI-аналитик", "company": "Учебный пример",
                    "contribution": "Разработала витрины данных и дашборды продаж",
                    "results": ["Сократила подготовку отчёта с 4 часов до 20 минут"],
                    "tech": ["SQL", "Power BI"]}],
}
CONTEXT = candidate_prompt({"name": "Анна", "must_have_skills": ["dbt", "English B2"]}, FACTS)
GOOD = "Нужен BI-аналитик. Обязательно: SQL, Power BI. Опыт от 5 лет. Задачи: дашборды продаж."
GAPS = "Нужен аналитик. Обязательно: SQL, dbt, English B2. Опыт от 5 лет."
CASES = [
    ("fit_one_year", FIT_PROMPT, CONTEXT + "\nВакансия:\n" + GOOD),
    ("fit_unknown_skills", FIT_PROMPT, CONTEXT + "\nВакансия:\n" + GAPS),
    ("letter", "Напиши сопроводительное на русском, 60–100 слов. Только факты кандидата; без штампов и выдумок. Верни только письмо.", CONTEXT + "\nВакансия:\n" + GOOD),
    ("reply_unknown", "Ответь работодателю по фактам кандидата. " + REPLY_INSTRUCTION, CONTEXT + "\nРаботодатель: У вас English B2 и опыт руководства пятью сотрудниками? Сможете начать в понедельник?"),
    ("reply_known", "Ответь работодателю по фактам кандидата. " + REPLY_INSTRUCTION, CONTEXT + "\nРаботодатель: С каким BI-инструментом работали? Какой конкретный результат получили?"),
    ("interview", "Составь полезную учебную шпаргалку к техническому собеседованию BI-аналитика на русском, до 450 слов. Три темы: LEFT JOIN и фильтр в ON/WHERE; оконная SUM OVER с PARTITION BY и ORDER BY; мера DAX и контекст фильтра в Power BI. Для каждой: 2-3 предложения объяснения, короткий конкретный пример и типичная ошибка. Затем 2 технических вопроса с правильными ответами и короткий рассказ о реальном проекте кандидата. Не добавляй к проекту ответственность, методы и результаты, которых нет в фактах. Разница 4 и 5 лет допустима: не пиши, что кандидат из-за неё не подходит. Нужны объяснения, а не перечисление названий технологий.", CONTEXT + "\nВакансия:\n" + GOOD),
    ("sql_test", "Реши учебную SQL-задачу для подготовки к собеседованию. Верни только JSON с полями sql (PostgreSQL) и explanation (коротко по-русски).", "Таблицы customers(id), orders(id, customer_id, status), order_items(order_id, qty, price). Для каждого клиента выведи id, число оплаченных заказов и их общую выручку. Сохрани клиентов без оплаченных заказов с нулями. Один заказ может иметь много позиций. Оплаченный status='paid'. Не удваивай число заказов из-за JOIN с позициями."),
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", nargs="+", default=MODELS)
    parser.add_argument("--efforts", nargs="+", help="Test only the specified reasoning efforts")
    parser.add_argument("--cases", nargs="+", choices=[case[0] for case in CASES])
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/ai-reasoning-2026-09-08.jsonl")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    app = WorkHunter(ROOT)
    config = app.ai_config()
    catalog = requests.get("https://openrouter.ai/api/v1/models", timeout=30)
    catalog.raise_for_status()
    metadata = {m["id"]: m for m in catalog.json()["data"]}
    completed = set()
    if args.output.exists():
        completed = {(r["model"], r["effort"], r["case"]) for line in args.output.read_text(encoding="utf-8").splitlines() if (r := json.loads(line))}

    def request(model: str, effort: str, name: str, system: str, prompt: str) -> dict:
        payload = {"model": model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}], "reasoning": {"effort": effort, "exclude": True}, "max_tokens": 16000}
        if effort == "off":
            payload["reasoning"] = {"enabled": False, "exclude": True}
        if model == "z-ai/glm-5.3-flash":
            payload["provider"] = {"only": ["novita/fp8", "z-ai/fp8", "gmicloud/fp8"]}
        started = time.monotonic()
        result = {"model": model, "effort": effort, "case": name, "messages": payload["messages"], "max_tokens": payload["max_tokens"]}
        try:
            response = requests.post(config["base_url"], headers={"Authorization": "Bearer " + config["api_key"]}, json=payload, timeout=150)
            data = response.json()
            choice = (data.get("choices") or [{}])[0]
            result.update(http=response.status_code, provider=data.get("provider"), response_model=data.get("model"), text=choice.get("message", {}).get("content") or "", finish_reason=choice.get("finish_reason"), usage=data.get("usage") or {}, error=data.get("error"), generation_id=data.get("id"))
        except requests.RequestException as error:
            result.update(http=None, text="", usage={}, error=type(error).__name__)
        result["seconds"] = round(time.monotonic() - started, 2)
        if result["http"] == 200 and result["text"]:
            try:
                if name.startswith("fit_"):
                    result["parsed"] = parse_fit(result["text"], GOOD if name == "fit_one_year" else GAPS, FACTS)
                elif name.startswith("reply_"):
                    try:
                        result["draft"] = parse_candidate_reply(result["text"], FACTS)
                        result["queued_for_user"] = False
                    except MissingCandidateFacts as error:
                        result["queued_for_user"] = True
                        result["missing_facts"] = str(error)
                elif name == "sql_test":
                    from work_hunter.llm.structured import extract_json_object
                    result["parsed"] = extract_json_object(result["text"])
            except (ValueError, TypeError, KeyError, StructuredParseError) as error:
                result["contract_error"] = str(error)
        usage = result["usage"]
        app.storage.record_ai_request(app.active_profile_id(), {"backend": "direct", "model": model, "status": "ok" if result["http"] == 200 and result["text"] else "error", "error_code": "" if result["http"] == 200 else f'http_{result["http"]}', "duration_ms": round(result["seconds"] * 1000), "prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": usage.get("completion_tokens"), "cost_usd": usage.get("cost")})
        with args.output.open("a", encoding="utf-8") as out:
            out.write(json.dumps(result, ensure_ascii=False) + "\n")
        print(json.dumps({k: result.get(k) for k in ("model", "effort", "case", "http", "seconds", "finish_reason", "contract_error")}, ensure_ascii=False), flush=True)
        return result

    try:
        for model in args.models:
            efforts = args.efforts or list(reversed(metadata[model]["reasoning"]["supported_efforts"]))
            if not args.efforts and metadata[model]["reasoning"].get("mandatory") is False and "none" not in efforts:
                efforts.insert(0, "off")
            print(json.dumps({"model": model, "supported_efforts": efforts}, ensure_ascii=False), flush=True)
            probe = request(model, efforts[0], "connectivity", "Ответь кратко", "Напиши слово готово")
            if probe["http"] != 200 or not probe["text"]:
                continue
            # Rotate effort order between tasks to reduce a systematic time-of-run bias.
            for index, case in enumerate(CASES):
                if args.cases and case[0] not in args.cases:
                    continue
                for effort in efforts[index % len(efforts):] + efforts[:index % len(efforts)]:
                    if (model, effort, case[0]) not in completed:
                        request(model, effort, *case)
    finally:
        app.storage.close()


if __name__ == "__main__":
    main()
