# Проверка upstream, форков и готовности — 8 сентября 2026

Локальные проверки проходят, но текущая установка ещё не готова к автономным
100–200 откликам в день: нет рабочей авторизации HH, не заполнены контакты/CV,
а OpenRouter отклоняет генерацию. Ниже разделены состояние кода и реальные
внешние проверки. Ранее существовавшие незакоммиченные изменения сохранены.

## Что проверено в Git

Найден 31 локальный клон/снимок, представляющий 26 разных репозиториев.
Проверены ссылки из README, THIRD_PARTY_NOTICES и docs. Выполнены fetch/prune
и ls-remote; деревья доноров не переключались на новые версии и не сливались
в Work Hunter. Два временных сетевых сбоя GitHub исчезли при повторном запросе.
Reancoree остался недоступен. Снимок hh-applicant-tool 1.8.19 оставлен закреплённым;
его upstream HEAD проверен отдельно.

Сравнивались свежие remote refs: часть рабочих деревьев доноров старее даже
ревизий прошлого аудита. Формулировка «клон обновлён» сама по себе не означает,
что checkout или установленный пакет обновились.

| Источник | Текущая проверенная ревизия | Вывод по коду |
|---|---|---|
| [s3rgeym/hh-applicant-tool](https://github.com/s3rgeym/hh-applicant-tool) | `a2d4a9f6f0f3`, версия в pyproject 1.8.28 | После 1.8.26 изменены login selectors под Magritte, разбор scalar config и UA. Наш HHBrowserAuthorizer использует ручной вход и не заполняет эти поля; перенос чужого автоматического login не требуется. Живой вход всё ещё нужен. |
| [s3rgeym/hh-ai-responder](https://github.com/s3rgeym/hh-ai-responder/releases/tag/v0.2.5) | `2fe3dc0fd2b5`, v0.2.5 | HTML-unescape перед чтением embedded JSON анкет/поиска. В нашем native test transport найден и исправлен аналогичный дефект. |
| [ever-jobs/ever-jobs](https://github.com/ever-jobs/ever-jobs) | `54965d7e3eac`, develop | С прошлого pin изменены 86 файлов: новые компании/источники, Pinpoint location/remote, ограничения навигации браузера своим доменом, ошибки crawler. Эти конкретные плагины не являются нашими runtime-адаптерами; новых источников в этом аудите не добавляли. |
| [feder-cr/AIHawk](https://github.com/feder-cr/AIHawk) | `472863b3b76d` | Оригинальная ссылка feder-cr/Jobs_Applier_AI_Agent_AIHawk перенаправляет сюда. Теперь это общий browser agent/MCP с новым runtime. Старое утверждение «только README» уже неверно; это не готовое обновление нашего LinkedIn flow. |
| [GodsScion/Auto_job_applier_linkedIn](https://github.com/GodsScion/Auto_job_applier_linkedIn) | `0ca5550f8aa8` | Совпадает с прошлым аудитом. Проверенного живого LinkedIn отклика у нашей установки по-прежнему нет. |
| [JOYCEQL/magic-resume](https://github.com/JOYCEQL/magic-resume) | `0181e37a9a3f` | После прошлого pin появились 2.0.8, длинный PDF export, multi-model AI, импорт PDF и миграции состояния. Остаётся reference редактора; наш export не становится проверенным от наличия этих изменений у донора. |
| [gmen1057/headhunter-mcp-server](https://github.com/gmen1057/headhunter-mcp-server) | `877434539a0e` | Без изменений относительно прошлого pin. |
| [hukenovs/hh_research](https://github.com/hukenovs/hh_research) | `a265c1a0e036` | Без изменений относительно прошлого pin. |
| [Londeren/hh-skill-verifications-quizzes](https://github.com/Londeren/hh-skill-verifications-quizzes) | `d99e0debe222` | Без изменений относительно прошлого pin. Учебный reference, не зависимость генератора. |

Ссылка `AIHawkProject/AIHawk` из старого отчёта возвращает Repository not found.
Оригинальный проект найден через remote существовавшего донорского клона;
его актуальный адрес указан выше.

## Форки

Проверено 35 origin-веток, включая контрольный upstream и повторные локальные
копии. В 32 сравнениях Git не нашёл общего предка с текущим upstream; численные
ahead/behind для них не используются как показатель актуальности.
Сверялись ревизии, деревья и новые source-коммиты. В просмотренных ветках новые
source-коммиты с 30 августа найдены у work-optimization.

| Репозиторий на GitHub | HEAD основной ветки |
|---|---|
| [0FL01/hh-applicant-tool](https://github.com/0FL01/hh-applicant-tool) | `cfc3d6fcab7f` |
| [1MaxSpb/hh-applicant-tool](https://github.com/1MaxSpb/hh-applicant-tool) | `5d7d84ffb499` |
| [Cafedupont/hh-applicant-tool](https://github.com/Cafedupont/hh-applicant-tool) | `43081d9390bd` |
| [Eastonn/hh-applicant-tool](https://github.com/Eastonn/hh-applicant-tool) | `3c9ded520bf4` |
| [Kj9516/hh-applicant-tool](https://github.com/Kj9516/hh-applicant-tool) | `261c2c31516d` |
| [KOVALSKl/hh-applicant-tool](https://github.com/KOVALSKl/hh-applicant-tool) | `9408bb18a45d` |
| [LazarenkoA/hh-applicant-tool](https://github.com/LazarenkoA/hh-applicant-tool) | `bf020b1332c5` |
| [Lexwxd/hh-applicant-tool](https://github.com/Lexwxd/hh-applicant-tool) | `f064e16e3e0c` |
| [MyNameRoman/hh-applicant-tool](https://github.com/MyNameRoman/hh-applicant-tool) | `ac4a586cda84` |
| [orelkrylatiy/work-optimization](https://github.com/orelkrylatiy/work-optimization) | `b7b273a90ffa` |
| [pomogashkin/hh-applicant-tool](https://github.com/pomogashkin/hh-applicant-tool) | `5356a6a20842` |
| [probox36/hh-applicant-tool-tg](https://github.com/probox36/hh-applicant-tool-tg) | `4857719160b7`, tg_reports |
| [ressiwage/CONT-hh-applicant-tool](https://github.com/ressiwage/CONT-hh-applicant-tool) | `0164e1a66033` |
| [ring-rong/hh-applicant-tool](https://github.com/ring-rong/hh-applicant-tool) | `fb4667a3d31d` |
| [roman-redl/hh-applicant-tool](https://github.com/roman-redl/hh-applicant-tool) | `95341705b94d` |
| [SpitiusK/hh-applicant-tool](https://github.com/SpitiusK/hh-applicant-tool) | `d9f3ff6c13c7` |
| [Reancoree/hh-applicant-tool](https://github.com/Reancoree/hh-applicant-tool) | Repository not found; локальный архив не доказывает доступность upstream |

Дополнительно сверены ring-rong/email-placeholders `c6a6afd79d5e`,
SpitiusK/agent-rework `0e92443aaf2a`, roman-redl/my-changes `cbbc928d8116`,
0FL01/llm-agent `654b0ee02bf1`, review-ветка work-optimization `1426d3bcf1ad`.
Email-placeholders уже входит в историю текущего upstream.

В work-optimization появились автономный reply worker, cron/locks, проверка
ответов LLM и статический fallback при сбое модели. Его автоматическая отправка
шаблонного ответа при недоступном AI не переносилась: это меняет поведение
общения с работодателями и не устраняет наш отказ провайдера. Исполнение чужих
донорских тестов не заявляется: проверялся их код и применимость к Work Hunter.

## Исправления в Work Hunter

- Native HH parser теперь читает `vacancyTests` с `&#34;` и `&quot;`.
  Два варианта воспроизводимо падали до исправления; теперь проходят вместе
  с исходным неэкранированным вариантом. Unescape выполняется, когда исходный
  JSON marker не найден, чтобы не менять содержимое обычного JSON.
- MCP `prepare_interview_brief` включает `study_guide`: приоритетные темы,
  краткие объяснения, вероятные вопросы/ответы и практические задачи.
  Используется существующий `interview_stage_prep`; убрано обрезание описания
  до 1000 символов. Ошибка модели становится ошибкой вызова, а не текстом
  «успешной подготовки».
- Текстовый ATS audit больше не предлагает утверждать по одному тексту,
  что исходные колонки/PDF не читаются. Он должен явно отличать анализ текста
  от проверки файла и не обещать прохождение системы работодателя.
- Doctor сообщает о неподдерживаемом AI backend. Поддерживаемый backend
  помечается `not_probed`, а не как доказанно рабочий API.
- Маска секрета `***` отклоняется AI-клиентом до HTTP-запроса.

## Живое состояние установки

**AI.** В конфиге стояли неподдерживаемый `codex_server` и маска `***` вместо
ключа. Первый вызов падал локально; после выбора direct маска дала HTTP 401.
Пользователь разрешил платные запросы через настроенный API. Найден его
существующий OpenRouter API key в OpenCode: `/api/v1/key` вернул 200.
В Work Hunter сохранены `backend=direct` и этот ключ; модель оставлена
`google/gemini-2.5-flash`. Значение ключа не выводилось в отчёты.

Генерация с действующим ключом возвращает HTTP 403:
`The request is prohibited due to a violation of provider Terms Of Service.`
Причина этого ограничения по ответу API не установлена. Успешной генерации
на этой модели/учётной записи нет; необходимо разрешить блокировку у провайдера
или настроить разрешённый доступ. OpenCode с тем же провайдером сам по себе
эту проблему не исправляет.

**HH.** Doctor воспроизвёл `403 bad_authorization`. Сохранённые access/refresh
token тоже оказались масками `***`; browser cookies/XSRF отсутствуют.
Нужен новый вход. Активный профиль `bi_analyst` не содержит email, phone и
resume_path. Автопилот выключен; текущие лимиты — 50/day и 10/run,
расписание 08:00–21:00 Europe/Moscow, интервал 60 минут.

**Google Calendar.** В коде Work Hunter — локальные CalendarEvent, agent agenda
и ICS, без Google OAuth/sync. В текущем чате доступен Google Calendar connector;
чтение профиля пользователя прошло. По конкретному запросу события можно
записывать через ассистента. Создание Google-события этим аудитом не проверялось.

## Что доказано тестами

| Проверка | Результат |
|---|---|
| Основной deterministic набор до правок этого аудита | 1727 passed, 5 skipped |
| Browser UI, infographic, external forms | 117 passed |
| HH transport/executor/recovery + MCP/AI после исправления parser | 119 passed |
| 200 откликов через реальный HHApplyClient в локальный FakeHHServer | 200 записей; 201-й отклонён до HTTP; тест прошёл |
| Финальный набор затронутых AI/MCP/services/CLI/native/E2E сценариев | 75 passed |
| Wheel/sdist и изолированная установка после основных правок | 8 passed |
| mypy | 87 source files, ошибок нет |
| pip check | Успешно |
| pip-audit установленного окружения | Известных уязвимостей не найдено; собственный work-hunter не аудируется через PyPI |
| Полный ruff | 409 замечаний исходной базы; зелёный quality gate не заявляется |
| git diff --check | Успешно |

Наборы пересекаются, количества не складываются. Пропуски основного набора:
Windows не исполняет POSIX permissions/fork/SIGALRM проверки, отсутствует
исходный Telegram export. Установленный вспомогательный hh-applicant-tool
в общем venv всё ещё 1.8.10; он не обновлялся автоматически. Основной native
autopilot использует собственные транспорты Work Hunter.

Тест 200 отправок проверяет executor, HTTP-контракт, SQLite, квоты и остановку
на границе. Он не измеряет пропускную способность HH: часы теста фиксированы,
ожидания scheduler и решения LLM в этом тесте не исполняются. Scheduler,
повторный запуск, cooldown и конкуренция покрыты существующим основным набором.
При паузах 45–120 секунд одни интервалы для 100 отправок занимают около
75–200 минут, для 200 — 150–400 минут. Формы и ограничения площадки добавят время.

Для рабочего запуска: восстановить HH login, контакты/CV и AI generation;
выбрать конкретные вакансии и резюме для одного живого контрольного отклика.
Затем настроить дневную квоту и per-run/schedule под нужный объём и явно
включить автопилот. Достигнутые 200/day на живом аккаунте не заявляются.
ATS-аудит и фильтры улучшают соответствие текста и отбор вакансий, но не могут
гарантировать прохождение скрытых правил работодателя. Быстрый отказ — только
эвристика, а не доказательство работы ATS.

## Артефакты и воспроизведение

Локальные игнорируемые результаты находятся в `outputs/audit-2026-09-08/`:
`upstreams.json`, `fork-branches.json`, JUnit XML, `ruff-final.json`,
`pip-audit.json`, `doctor-final.json`, `live-ai-final-status.json`.
Логи рядом имеют префикс `outputs/audit-2026-09-08-`.

```powershell
$env:PYTHONUTF8='1'
.venv/Scripts/python.exe -m pytest -q --ignore=tests/test_web_ui_browser.py --ignore=tests/test_telegram_chat_infographic_browser.py --ignore=tests/test_external_apply_browser.py
.venv/Scripts/python.exe -m pytest tests/test_web_ui_browser.py tests/test_telegram_chat_infographic_browser.py tests/test_external_apply_browser.py -q
.venv/Scripts/python.exe -m pytest tests/test_hh_autopilot_e2e.py -k daily_200 -q
.venv/Scripts/python.exe -m mypy work_hunter
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m work_hunter --root . doctor --json
```
