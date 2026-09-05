# Паритет с hh-applicant-tool 1.8.26 и hh-ai-responder 0.2.4

Повторно проверено 30 августа 2026 года по точным ревизиям:

- [`hh-applicant-tool v1.8.26`](https://github.com/s3rgeym/hh-applicant-tool/tree/v1.8.26),
  `63210bcce74e`;
- [`hh-ai-responder v0.2.4`](https://github.com/s3rgeym/hh-ai-responder/releases/tag/v0.2.4),
  `6d18ea02baaf`.

## Итог

Work Hunter закрывает пользовательский сценарий обоих приложений в одном
локальном интерфейсе: поиск, оценка, отклики, тесты/формы, резюме, переговоры,
AI-письма, AI-ответы работодателям, кнопочные вопросы Chatik, очистка отказных
чатов, поднятие резюме и статус «ищу работу». Для Chatik и операций профиля
используются локальные browser cookies; для официальных методов — HH API token.

Это функциональный паритет, а не заявление, что реализован каждый внутренний
метод Android APK. APK-разведка хранит каталог обнаруженных методов отдельно;
в продукт включены методы, которые нужны перечисленным рабочим сценариям.

## Что изменилось в свежих upstream

В `hh-applicant-tool` после 1.8.20 появились custom prompt для AI-фильтра,
строгий формат ответа фильтра, дополнительные auth/redirect-проверки, обработка
vacancy test и ссылки в UI. В Work Hunter строгая схема AI-ranking уже сильнее
текстового протокола донора; auth, redirect и challenge-контуры сохранены.

В `hh-ai-responder 0.2.4` изменились applicant URL и обработка cookies. Work
Hunter переведён на `/applicant/my_resumes`, декодирует HTML-escaped
`redirectConfig`, умеет брать applicant id из `_attributes.user` и использует
актуальный browser User-Agent.

## Матрица hh-applicant-tool 1.8.26

| Возможность оригинала | Work Hunter |
|---|---|
| OAuth/manual auth, refresh, logout, whoami | `hh auth ...`, несколько account profiles, browser login/cookies |
| Список, создание, клонирование, публикация и обновление резюме | HH resume CLI/API/UI; API update с web-touch fallback |
| Поиск по всем фильтрам, URL поиска, похожие вакансии | HH search/campaign/autopilot и импорт HH search URL |
| Отклики с письмами | campaign/autopilot, шаблонные и AI-письма |
| Тесты вакансии и CAPTCHA | native challenge pipeline, AI answer helpers, ручной fallback |
| Ответы работодателям | официальный negotiation API и новый Chatik transport |
| Очистка переговоров, skipped, blacklist | negotiation cleanup, clear-skipped, ATS blacklist/hide chat |
| Контакты и email follow-up | employer enrichment и SMTP follow-up с cooldown |
| Произвольный HH API | API Lab, CLI `hh api call` |
| SQL/export/config/UI | operator query/export, config, локальный web cockpit |
| Cron/фоновые запуски | restart-safe JSON runner, lock, отчёты, Windows Task Scheduler/cron |

## Матрица hh-ai-responder

| Возможность оригинала | Work Hunter |
|---|---|
| `cookies.txt` | JSON browser export и Netscape cookies.txt |
| Поднятие резюме через `/applicant/resumes/touch` | `hh web touch-resumes`; fallback команды Update resumes |
| Статус `looking_for_offers` | `hh web job-search-active` |
| Поиск и массовые отклики | HH campaign/autopilot с фильтрами, квотами и журналом |
| AI-сопроводительные | grounded AI cover letters |
| Решение тестов | native test/form challenge pipeline |
| Чаты `chatik.hh.ru` | list/chat_data/send/leave transport |
| Кнопки чат-ботов | варианты извлекаются; ответ обязан точно совпасть с одной кнопкой |
| AI-ответ с историей | grounded prompt по профилю, резюме и последним сообщениям |
| Уход из `DISCARD`-чатов | планируемая/подтверждаемая очистка с журналом |
| Защита от дублей | fingerprint входящего сообщения + стабильный idempotency UUID |
| Циклы 15 мин / 4 ч / 24 ч | отдельные runner plans для внешнего Task Scheduler/cron |

Промпт оригинала, который предлагает заявлять любые навыки, намеренно не
перенесён. Work Hunter отвечает только фактами из локального профиля — иначе
автоматизация быстро создаёт проблемы уже на собеседовании.

## Первый запуск

```powershell
# Вариант 1: войти в обычном браузерном окне Work Hunter
work-hunter --root . hh auth login --account default

# Вариант 2: импортировать Netscape cookies.txt
work-hunter --root . hh web import-cookies .\cookies.txt

# Проверить, что профиль и Chatik читаются
work-hunter --root . hh web profile
work-hunter --root . hh chats list --limit 20

# Сначала увидеть точные ответы без отправки
work-hunter --root . hh chats reply --use-ai --limit 10

# После проверки реально отправить
work-hunter --root . hh chats reply --use-ai --limit 10 --confirm
```

Кнопки `HH Chats` в локальном UI делают то же: загрузка, план AI-ответов,
просмотр текста и отдельное подтверждение реальной отправки.

Поднятие резюме и статус:

```powershell
work-hunter --root . hh web touch-resumes
work-hunter --root . hh web touch-resumes --confirm
work-hunter --root . hh web job-search-active
work-hunter --root . hh web job-search-active --confirm
```

## Фоновые циклы

Готовые планы находятся в `examples/`:

- `hh-chat-responder-runner.json` — запускать каждые 15 минут;
- `hh-resume-touch-runner.json` — каждые 4 часа;
- `hh-job-status-runner.json` — раз в сутки;
- `hh-maintenance-runner.json` — общий расширенный проход.

Все планы поставляются с `confirm: false`: они строят и журналируют план. После
проверки AI-конфига и первого dry-run поменяйте `confirm` на `true` только в
локальной копии нужного плана.

```powershell
Copy-Item examples\hh-chat-responder-runner.json .work-hunter\hh-chat-responder-runner.json
notepad.exe .work-hunter\hh-chat-responder-runner.json
work-hunter --root . runner --plan .work-hunter\hh-chat-responder-runner.json
```

## Что ещё требует живой проверки

Контракт покрыт unit/integration-тестами, но текущая HH-сессия должна быть
создана самим пользователем. До входа нельзя честно утверждать, что HH сегодня
принял реальный Chatik POST или touch: недокументированный web contract может
измениться. Первый live-run делайте с `limit: 1`, затем проверьте сообщение в
обычном интерфейсе HH.

Атрибуция и лицензионные ограничения зафиксированы в
[`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md).
