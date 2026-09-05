# Аудит upstream и форков — 30 августа 2026

Все локальные донорские клоны обновлены через `git remote update --prune`.
Ничего из upstream не сливалось вслепую: изменения сравнивались с собственными
транспортами, политиками, UI и тестами Work Hunter.

## Основные upstream

| Репозиторий | Проверенная ревизия | Что изменилось | Решение |
| --- | --- | --- | --- |
| [s3rgeym/hh-applicant-tool](https://github.com/s3rgeym/hh-applicant-tool/releases/tag/v1.8.26) | `63210bcce74e`, v1.8.26, 2026-08-15 | custom AI-filter prompt/output, auth/redirect, vacancy tests, UI links | Совместимые auth/redirect/challenge части сверены; текстовый AI-протокол не копируется, потому что native structured ranking строже |
| [s3rgeym/hh-ai-responder](https://github.com/s3rgeym/hh-ai-responder/releases/tag/v0.2.4) | `6d18ea02baaf`, v0.2.4, 2026-08-23 | `/applicant/my_resumes`, encoded `redirectConfig`, applicant id fallback, cookies | Изменения перенесены в applicant web transport и покрыты тестами |
| [ever-jobs/ever-jobs](https://github.com/ever-jobs/ever-jobs) | `20079a64e123`, develop, 2026-08-21 | крупное расширение источников, диагностика, retry/redaction, MCP | Оставлен донором идей и источников; целиком не встраивается из-за другого Node/NestJS runtime |
| [GodsScion/Auto_job_applier_linkedIn](https://github.com/GodsScion/Auto_job_applier_linkedIn) | `0ca5550`, main, 2026-08-10 | MIT relicense, LangChain/LangGraph AI layer, tests/control panel, обработка исключений | Новая ветка интересна, но live LinkedIn Easy Apply всё равно требует отдельного canary после ручного login |
| [AIHawkProject/AIHawk](https://github.com/AIHawkProject/AIHawk) | `79155b5`, main, 2026-08-19 | после предыдущего pin только README/banner | Нового рабочего LinkedIn provider flow не появилось |
| [orelkrylatiy/work-optimization](https://github.com/orelkrylatiy/work-optimization) | `de5aebb2934c`, main, commit date 2026-08-31 | TLS verify, timeout bounds, hh-applicant 1.8.26, chat fixes | Security-идеи учтены; дата коммита впереди даты проверки, поэтому ревизия важнее timestamp |
| [JOYCEQL/magic-resume](https://github.com/JOYCEQL/magic-resume) | `0a08e50dc213`, main/v2.0.7, 2026-08-06 | актуальный resume editor | Только UX/reference; runtime не встраивается |
| [gmen1057/headhunter-mcp-server](https://github.com/gmen1057/headhunter-mcp-server) | `877434539a0e`, main, 2025-10-13 | без свежих изменений | MCP-контракт Work Hunter уже шире и безопаснее по подтверждению записи |
| [hukenovs/hh_research](https://github.com/hukenovs/hh_research) | `a265c1a0e036`, master, 2024-03-03 | неактивен | Архивный reference |
| [Londeren/hh-skill-verifications-quizzes](https://github.com/Londeren/hh-skill-verifications-quizzes) | `d99e0debe222`, main, 2026-06-03 | база skill quizzes | Reference для подготовки, не runtime-зависимость |

## Форки hh-applicant-tool

Большинство просмотренных форков отстают от `s3rgeym/main`. Полезные отдельные
ветки проверены отдельно:

- `ring-rong/fix/email-placeholders-not-substituted` — `c6a6afd79d5e`,
  подстановка placeholders в email subject/body;
- `SpitiusK/sprint/agent-rework-2026-04-17` — `0e92443aaf2a`, agent rework;
- `roman-redl/my-changes` — `cbbc928d8116`, набор локальных изменений;
- `0FL01/main` — `cfc3d6fcab7f`, заметно отстаёт от upstream;
- `Reancoree/hh-applicant-tool` больше не доступен на GitHub (404); локальная
  копия `e2675b0d028e` считается архивной и не должна быть источником обновлений.

Ни один форк не содержит более полного и одновременно более свежего HH core,
чем основной upstream v1.8.26. Точечные ветки остаются reference, а не базой для
merge.

## Готовность продуктового контура

- ATS: deterministic scoring/red flags, structured AI ranking и отдельный
  resume audit; MCP-инструмент `ats_resume_audit` доступен агенту.
- Массовые отклики: default 50/day, hard administrative ceiling 200/day,
  per-run quota, атомарные reservations, cooldown на 429/HH daily limit,
  idempotency и restart recovery. 100–200/day разрешаются конфигом, но не
  считаются live-проверенными до canary на текущем аккаунте.
- Календарь: локальные события, UI и agent ICS работают. MCP-инструмент
  `save_calendar_event` пишет событие в локальный календарь. Прямого Google
  Calendar OAuth/sync в репозитории пока нет.
- Собеседование: summary, вопросы работодателю и grounded STAR pitch объединены
  в MCP-инструмент `prepare_interview_brief`.

## Что нельзя доказать офлайн

Тесты доказывают локальную логику, request contracts, квоты и UI. Они не могут
доказать, что недокументированный HH/LinkedIn web endpoint не поменялся для
конкретной учётной записи. Перед серией нужен ручной login и один реальный
отклик-canary; затем 5–10, затем рабочий лимит. При 45–120 сек между отправками
100 откликов занимают примерно 75–200 минут, 200 — 150–400 минут без учёта
форм, CAPTCHA, пауз и внешних rate limits.

## Doctor текущей установки

Проверка `.venv\\Scripts\\python.exe -m work_hunter --root . doctor --json`
30 августа 2026 года вернула `status: ok` для core, package, migrations,
dependencies, config, SQLite, MCP и UI. Live-отправка сейчас не готова по
состоянию аккаунтов:

- профиль `bi_analyst` не содержит `email`, `phone` и существующий
  `resume_path`;
- HH access token невалиден (`403 bad_authorization`), browser cookies/XSRF не
  импортированы;
- LinkedIn/Indeed требуют ручной browser login;
- рекомендованный режим до исправления — `external_first_hh_after_reauth`.

Это операционные блокеры, а не падение кода. До заполнения профиля, повторной
HH-авторизации и live-canary нельзя включать массовую отправку.
