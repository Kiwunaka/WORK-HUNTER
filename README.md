# Work Hunter — локальный центр поиска работы

Приватный local-first инструмент: собирает вакансии, оценивает соответствие вашему опыту, готовит сопроводительные и ведёт автоотклики на HH — с явными подтверждениями опасных действий.

- Секреты, куки, токены и база живут только локально в `.work-hunter/` и никогда не коммитятся.
- Реальные отклики требуют буквального `confirm: true` — строки `"true"` ничего не разрешают.
- Письма и ответы строятся только по вашей базе фактов: неизвестное уходит на уточнение, а не выдумывается.

## Быстрый старт за 5 минут

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[browser,ui]"
python -m playwright install chromium
python -m work_hunter init
python -m work_hunter doctor --json
python -m work_hunter ui --host 127.0.0.1 --port 8787
```

Откройте `http://127.0.0.1:8787` и пройдите 4 шага:

| № | Что сделать | Где |
|---|---|---|
| 1 | Заполните имя и контакты | Настройки → Профиль |
| 2 | Сохраните реальный опыт: должности, даты, проекты, вклад, результаты, навыки | Резюме → База опыта |
| 3 | Выберите ИИ и нажмите «Проверить запросом» (рекомендуется Muse, см. ниже) | Настройки → AI |
| 4 | Нажмите «Проверить без входа в HH» — диагностика без обращений к HH | Площадки и автоотклики |

Та же диагностика доступна из CLI без платных вызовов:

```powershell
python -m work_hunter launch-readiness --json
```

## Вход в HH и запуск автооткликов

1. Нажмите **«Войти и продолжить»** — откроется одно окно Chromium. Войдите (пароль/код/капча — руками), дождитесь самозакрытия окна. Повторный клик второе окно не открывает: увидите «Окно входа уже открыто».
2. Проверка: `python -m work_hunter hh web status` → `"status": "ok"`, `"can_chatik": true`. Статус `hh auth status = invalid_access_token` при этом нормален — автопилот работает по web-кукам.
3. Свяжите локальную версию резюме с ID опубликованного резюме HH (привязка выбирает резюме для откликов, текст на HH не меняет).
4. Прогоните безопасный цикл:

```powershell
work-hunter hh autopilot validate --account default
work-hunter hh autopilot shadow --account default
work-hunter hh autopilot canary --account default --resume RESUME_ID --vacancy VACANCY_ID --confirm
work-hunter hh autopilot enable --account default --confirm
work-hunter runner --plan examples/hh-autopilot-runner.json
```

Лимиты по умолчанию: `50 откликов/день`, `10 за запуск`, пауза `45–120 с`, окно `08:00–21:00 Europe/Moscow`. Без `enable --confirm` отправок нет, флаг `allow_broad_apply` ничего не разрешает.

## Почему по умолчанию Muse Spark 1.3 Contributor

Мы прогнали Muse, GLM и DeepSeek через одинаковые 7 заданий (2 отбора, письмо, 2 ответа работодателю, шпаргалка к собесу, SQL на данных) и оставили основную модель по итогам, а не по рекламным рейтингам.

| Проверка (финал 10.09.2026, режим high/max) | Muse high | GLM 5.3 Flash max | DeepSeek V4.1 Flash high |
|---|---|---|---|
| Отбор: стаж 4/5 лет | consider, корректно | ошибка цитаты | consider, корректно |
| Отбор: неизвестные dbt/English | review, корректно | review, корректно | review, корректно |
| Письмо | факты сохранены | лишний акцент на стаже | факты сохранены, есть повторы |
| Ответ без фактов | 3 уточнения, без выдумок | 3 уточнения | 3 уточнения |
| Ответ по фактам | первое лицо, парсер пройден | третье лицо (править руками) | первое лицо |
| SQL на данных | верно | верно | верно |
| Шпаргалка | ошибка COUNT(*) после LEFT JOIN | доли без группировки | аккуратнее всех, но рамка окна неполная |
| Цена 7 заданий | **$0.0031** | $0.0036 | $0.0252 (×8 от Muse) |
| Медиана времени | 31 с | 12 с | 9 с |

Коротко: Muse — единственная, кто корректно прошла оба отбора, сохранила факты в письме и ответила от первого лица, оставшись самой дешёвой. GLM low/high вообще не прошли отбор (0/2) и путали неизвестное с отсутствием опыта; DeepSeek в 8 раз дороже за тот же набор — он настроен только резервом на случай недоступности Muse. Слабые места Muse тоже известны: шпаргалки к собесу проверяйте руками (ошибка COUNT(*) подтверждена контрпримером на данных).

Детали и методология: [первый прогон Muse](docs/2026-09-08-ai-model-reasoning-evaluation.md), [GLM/DeepSeek-перепроверка](docs/2026-09-08-glm-deepseek-recheck.md), [дешёвые модели](docs/2026-09-09-cheap-models-benchmark.md), [бюджет ×10](docs/2026-09-09-tenfold-model-budget.md), [финал Muse/GLM/V4.1](docs/2026-09-10-final-model-comparison.md).

### Как это настроено

```jsonc
{
  "model": "meta/muse-spark-1.3-contributor",
  "reasoning": { "effort": "high" },
  "model_fallbacks": { "meta/muse-spark-1.3-contributor": "deepseek/deepseek-v4.1-flash" },
  "model_max_prices": { "meta/muse-spark-1.3-contributor": { "prompt": 0.1, "completion": 0.2 } }
}
```

Резерв срабатывает только при 404/410/503 или удалённом ID модели — ошибки ключа, баланса и 403 не переключают модель. Лимит цены ($0.10/$0.20 за млн токенов = текущий тариф Contributor) защищает от подорожавших маршрутов. Пустые поля в «Моделях по задачам» наследуют основную модель — отдельно ничего настраивать не нужно.

## Что внутри (карта репозитория)

| Путь | Назначение |
|---|---|
| `work_hunter/cli.py` | CLI: точка входа и маршрутизация команд. |
| `work_hunter/services.py` | Фасад `WorkHunter` — через него идут почти все сценарии. |
| `work_hunter/storage.py` | SQLite-схема и хранение. |
| `work_hunter/sources/` | Адаптеры источников вакансий. |
| `work_hunter/hh_transport/` | Транспорт HH: API, браузер, куки, XSRF. |
| `work_hunter/hh_agent/` | Агент HH: политика, согласования, формы, события. |
| `work_hunter/llm/` и `work_hunter/ai_backends.py` | Работа с ИИ: direct OpenAI-совместимый HTTP и OpenCode. |
| `work_hunter/web/` | Локальный HTTP-сервер и статический UI. |
| `tests/` | Pytest: исполняемый контракт поведения. |
| `docs/` | Планы, безопасность, заметки recon, отчёты о моделях. |

## Не коммитить

Локальное и только локальное (уже в `.gitignore`):

- `.work-hunter/` — конфиг, SQLite, состояние HH, логи, токены, куки;
- `external/`, `.codex_compare/`, `.compare-forks/`, `.compare-hh-applicant-tool/` — доноры и исследования;
- `.venv/`, `.pytest_cache/`, `__pycache__/`, `work_hunter.egg-info/`;
- `.research/`, `.playwright-mcp/`, дампы HAR/сессий/кук;
- любые `*.db`, `*.sqlite3`, `*.log`, `*.har`, `*.pem`, `*.key`, `.env*`.

Проверка перед коммитом (пустой вывод — хорошо):

```powershell
git diff --cached --name-only | rg "^(\.work-hunter/|external/|\.venv/|\.research/|\.codex_compare/|\.compare-forks/|\.compare-hh-applicant-tool/|\.playwright-mcp/|.*\.(db|sqlite|sqlite3|log|har|pem|key|p12|pfx)$)"
git grep --cached -n -I -E "(-----BEGIN [A-Z ]*PRIVATE KEY-----|sk-[A-Za-z0-9_-]{20,}|sk-or-v1-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9_]{20,})"
```

## Частые команды

Проверка состояния (токены не печатаются):

```powershell
python -m work_hunter doctor --json
python -m work_hunter hh-auth-status
python -m work_hunter launch-readiness --json
```

Сбор, оценка и список вакансий:

```powershell
python -m work_hunter sync --source habr --limit 20
python -m work_hunter sync --source linkedin --limit 20
python -m work_hunter sync --source indeed --limit 20
python -m work_hunter score
python -m work_hunter list --limit 20 --min-score 50
```

План отклика и отправка (только с `--confirm`):

```powershell
python -m work_hunter apply-plan 123
python -m work_hunter apply 123 --letter-file .\letter.txt --confirm
python -m work_hunter browser-login linkedin
python -m work_hunter browser-login indeed
```

Локальный UI и MCP:

```powershell
python -m work_hunter ui --host 127.0.0.1 --port 8787
python -m work_hunter mcp
python -m work_hunter source-capabilities
```

### Экраны интерфейса

- **Сегодня** — готовность, до трёх приоритетных действий, свежие совпадения, события и решения;
- **Вакансии** — поиск, фильтры, оценка, избранное, письма и единый сценарий отклика;
- **Отклики** — воронка, согласования агента и автоматизация;
- **Календарь** — собеседования, follow-up, напоминания, задачи;
- **Ассистент** — чат и черновики с учётом вакансий;
- **Аналитика** — воронка, распределение оценок, эффективность источников;
- **Источники** — здоровье подключений, последний синк, ошибки;
- **Настройки** — профили, резюме, поиск, HH/AI, внешний вид, помощь, API Lab.

При первом запуске — мастер из трёх шагов (цель, источники, резюме). **«Настроить позже»** откладывает только на текущую вкладку. Повтор — через **Настройки → Помощь → Пройти введение заново**.

## Отправка откликов: правила безопасности

Отклики — чувствительные действия, поэтому везде один контракт «план → подтверждение»:

- Отправка только по буквальному `confirm: true` (в CLI — `--confirm`). Строка `"true"` не разрешает ничего.
- Автономные отклики HH — только при активном гранте `enable --confirm` на конкретный аккаунт и политику.
- В UI действия с полномочиями идут через отдельный экран подтверждения; сервер перепроверяет всё сам.
- Токены, куки, client secret, Telegram-токены и полные auth-заголовки никогда не печатаются.
- Тесты/формы вакансий и CAPTCHA при отклике решаются автоматически при настроенном AI; непонятные случаи уходят на ручной разбор по конкретной вакансии.

Проверки:

```powershell
pytest tests/test_mcp_safety.py tests/test_hh_agent_mcp.py -q
pytest tests/test_hh_agent_approval.py tests/test_hh_agent_research.py -q
```

## API Recon и внешние сессии

Инструменты recon — только для ваших собственных аккаунтов и сессий, секреты в отчётах затираются:

```powershell
python -m work_hunter api-discover-url https://example.com --host example.com
python -m work_hunter api-probe-url https://example.com --host example.com --limit 20
python -m work_hunter api-recon-har .\session.har --host example.com
python -m work_hunter external-adapter-plan .\session.har --source example --host example.com
```

Прямые вызовы `external-session call` — лабораторный интерфейс нижнего уровня. Обычным откликам `--unsafe-lab` не нужен: подтверждённый `apply` сам вызывает настроенный адаптер.

```powershell
python -m work_hunter external-session call example POST https://example.com/api/apply --data-file .\payload.json --real --unsafe-lab
```

Только по явной просьбе владельца для реального вызова из личного аккаунта.

## Тесты

Полный прогон:

```powershell
pytest -q
```

Точечные наборы:

```powershell
pytest tests/test_config.py tests/test_ai_backend.py -q
pytest tests/test_sources.py tests/test_public_boards.py -q
pytest tests/test_hh_auth.py tests/test_hh_transport.py -q
pytest tests/test_external_sessions.py tests/test_api_recon.py -q
```

Правило: сначала допишите/обновите точечный тест под новое поведение. Покрыты маскирование секретов, безопасность HH, хранилище агента, API Lab, отчёты планировщика, редактура внешних сессий.

## Разработка и релиз

Требуется Python 3.11+.

```powershell
python -m pip install --upgrade "pip>=26.1.2"
python -m pip install -e ".[dev,browser,ui,release]"
python -m playwright install chromium
pytest -q
ruff check .
mypy work_hunter
python -m build
```

Кабина слушает только loopback. Проверка секретов и релизная сборка не читают `.work-hunter`; смоуки гоняются на изолированных корнях.

## Доки для старта агента

1. `README.md`
2. `docs/superpowers/specs/2026-04-26-work-hunter-design.md`
3. `docs/superpowers/plans/2026-06-09-ultimate-hh-tool-harvest-plan.md`
4. `docs/superpowers/plans/2026-06-10-external-api-parity.md`
5. `work_hunter/services.py`
6. нужные тесты в `tests/`

Известные ограничения (проверять по тестам, а не на слово):

- backend ИИ: direct OpenAI-совместимый HTTP и OpenCode; реестр провайдеров под подписки Codex/OpenCode ещё чистить;
- статусу источников в UI нужна более богатая агрегация.

## MCP для HH: честная связка research → точечный отклик

MCP-путь HH — это два шага, а не одна кнопка «откликнуться на всё»:

1. `hh_research_and_apply` / `hh_research_vacancies` — только ищут, оценивают и складывают dry-run планы. Каждый `planned` item уже содержит `job_id` и готовые `next_actions`. Массовая отправка пачкой здесь заблокирована навсегда: `confirm_apply=true` внутри research отвечает `real_apply_blocked`.
2. `hh_apply_vacancy` (`confirm_apply=true`) или `apply_job` (`confirm=true`) — отправка по одной вакансии. Вакансия обязана уже лежать в локальной базе (research её туда кладёт сам через `job_id`), иначе получите `blocked: not in local storage`.

Почему так: пачка откликов одним вызовом обходит гранты автопилота, квоты и попарную проверку тестов/форм. Точечный confirm идёт через тот же движок, что ручной отклик, — с авторизацией, дедупом и записью в историю (`hh_autopilot_history`).

Сквозной сценарий для агента: research → взять `job_id` из `planned` items → `hh_apply_vacancy` по каждой вакансии отдельно → проверить `hh_autopilot_history`. Тесты связки: `tests/test_hh_agent_mcp.py`.

Полный операторский гайд HH: [docs/hh-autopilot.md](docs/hh-autopilot.md). Сверка с донорами: [hh-reference-parity](docs/hh-reference-parity.md), [аудит 30.08](docs/2026-08-30-upstream-audit.md), [аудит 08.09](docs/2026-09-08-upstream-readiness-audit.md).

## Git: приватность прежде всего

Репозиторий приватный — таким и остаётся.

```powershell
git status --short --ignored
git diff --stat
pytest -q
git add <explicit files>
git diff --cached --check
git commit -m "<short change summary>"
git push
```

Никакого `git add -A` без просмотра untracked: рядом лежат приватные рантайм-данные.

## License And Donor Code

Licensed upstream/fork research exists locally but is not committed in the initial baseline. If future work ports substantial donor code, preserve attribution in docs or commit messages and never copy donor secrets, personal prompts, or machine-specific scripts as defaults.
