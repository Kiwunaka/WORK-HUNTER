# HH Autopilot: запуск и эксплуатация

HH Autopilot проходит полный цикл: многостраничный поиск, жёсткие фильтры, ранжирование, лимиты, отклик, формы, ручная CAPTCHA, журнал, retry и scheduler.

По умолчанию автопилот выключен. Новый grant на отклики появляется только после `enable --confirm`. Устаревший `sources.hh.allow_broad_apply` ничего не разрешает.

## Быстрый запуск

Команды ниже запускаются из корня репозитория в PowerShell.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[browser,ui]"
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\work-hunter.exe --root . init
.\.venv\Scripts\work-hunter.exe --root . hh auth login --account default
.\.venv\Scripts\work-hunter.exe --root . hh auth status --account default
.\.venv\Scripts\work-hunter.exe --root . hh resumes sync
.\.venv\Scripts\work-hunter.exe --root . hh autopilot validate --account default
.\.venv\Scripts\work-hunter.exe --root . hh autopilot shadow --account default
```

Проверьте shadow-журнал. Затем выполните один именованный live-canary и только после него выдавайте постоянный grant:

```powershell
.\.venv\Scripts\work-hunter.exe --root . hh autopilot canary --account default --resume RESUME_ID --vacancy VACANCY_ID --confirm
.\.venv\Scripts\work-hunter.exe --root . hh autopilot enable --account default --confirm
.\.venv\Scripts\work-hunter.exe --root . runner --plan examples\hh-autopilot-runner.json
```

Замените `RESUME_ID` и `VACANCY_ID` реальными опубликованным резюме и вакансией. Canary разрешает не больше одного успеха и не создаёт постоянный grant.

## 1. Архитектура и durable state flow

Один run обрабатывает данные в таком порядке:

```text
HH search/recommendations
  -> normalize + durable search checkpoint
  -> hard filters
  -> deterministic/AI ranking + best resume
  -> quota reservation + account lease
  -> application POST or grounded form
  -> applied | skipped | retry_wait | reconciling | manual_challenge | dead
  -> journal + scheduler recovery
```

Основное состояние хранится в `.work-hunter/work_hunter.sqlite3`. Миграции создают account-aware историю, runs, search cycles/checkpoints, items, attempts, quota reservations, grants, leases, cooldowns, challenges и events. Перезапуск процесса не сбрасывает очередь.

Состояния item: `discovered`, `eligible`, `ranked`, `ready`, `applying`, `reconciling`, `retry_wait`, `manual_challenge`, `applied`, `skipped`, `dead`. Search checkpoint фиксирует следующую страницу. Lease с fencing token не даёт старому runner записать результат после takeover.

Shadow использует реальный read-only поиск и пишет изолированные результаты. Он не создаёт live queue, application guard или quota reservation.

## 2. Установка browser/UI и приватный data root

Минимальная установка для CLI:

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
```

Для browser-assisted login, форм и локального UI:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[browser,ui]"
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\work-hunter.exe --root . ui --host 127.0.0.1 --port 8787
```

`--root` определяет приватный data root. При `--root .` данные находятся в `.work-hunter/`. Не коммитьте этот каталог. UI принимает только loopback-host.

Docker собирает wheel с migrations/static UI, работает как UID/GID `10001` и использует volume `/data`:

```powershell
docker build -t work-hunter .
docker run --rm -v work-hunter-data:/data work-hunter init
docker run --rm -v work-hunter-data:/data work-hunter hh autopilot status --account default
```

Browser-образ включается отдельно:

```powershell
docker build --build-arg INSTALL_PLAYWRIGHT=true -t work-hunter-browser .
```

Интерактивный login/CAPTCHA в контейнере требует видимую browser-среду либо импорт cookies из host-сессии. Headless-решения CAPTCHA нет.

## 3. HH login, import, status и logout

Browser login открывает Chromium с постоянным локальным profile. Пароль, OTP и CAPTCHA вводит пользователь. Таймаут ожидания авторизации — 5 минут.

```powershell
work-hunter --root . hh auth login --account default
work-hunter --root . hh auth status --account default
work-hunter --root . hh auth refresh --account default
work-hunter --root . hh auth logout --account default --confirm
```

Импорт Playwright-compatible JSON cookies:

```powershell
work-hunter --root . hh auth import-cookies --account default --file C:\private\hh-cookies.json
```

Импорт OAuth token в именованный профиль:

```powershell
work-hunter --root . hh auth import-token --profile default --access-token ACCESS_TOKEN --refresh-token REFRESH_TOKEN --client-id CLIENT_ID --client-secret CLIENT_SECRET --access-expires-at 2026-07-31T12:00:00+00:00
```

Не передавайте secrets через общую shell history. Для постоянной работы храните их только в локальном config/profile с закрытым доступом.

`hh auth oauth-start` и `oauth-callback` не используют чужие или встроенные client credentials. Пока собственные `client_id`, `client_secret` и `redirect_uri` не настроены, команды возвращают `oauth_flow_not_configured`. Поддерживаемый обход — user-supplied token/cookies.

## 4. Полная схема конфигурации

Autopilot читает `sources.hh.autopilot` из `.work-hunter/work_hunter_config.json`. Пропущенные поля получают defaults. Неизвестные поля отвергаются. `enabled` и `authorization_generation` управляются сервисом; правьте их только через `enable`/`disable`.

### Аккаунты, schedule и search

| Путь | Default | Допустимые значения |
|---|---|---|
| `timezone` | `Europe/Moscow` | IANA timezone, строка 1..200 |
| `accounts` | один `default` | 1..100 объектов, уникальный `profile_id` |
| `accounts[].profile_id` | `default` | строка 1..200, trim/casefold; существующий HH auth profile |
| `accounts[].candidate_profile_id` | `default` | строка 1..200; существующий `profiles` profile |
| `accounts[].enabled` | `false` | boolean, service-managed |
| `accounts[].paused` | `false` | boolean |
| `accounts[].authorization_generation` | `null` | `null` или integer >= 1, service-managed |
| `accounts[].resume_queries` | `published:*` + recommendations | 1..500 уникальных mappings |
| `resume_queries[].resume_id` | `published:*` | строка 1..200; `published:*` или ID опубликованного резюме |
| `resume_queries[].preset_names` | `[]` | 0..100 уникальных имён; пусто только при recommendations |
| `schedule.days` | `[1..7]` | 1..7 уникальных ISO weekday, Monday=1 |
| `schedule.start` | `08:00` | `HH:MM` |
| `schedule.end` | `21:00` | `HH:MM`, не раньше `start` |
| `schedule.interval_minutes` | `60` | 1..1440 |
| `search.include_recommendations` | `true` | boolean |
| `search.per_page` | `100` | 1..100 |
| `search.max_pages` | `20` | 1..100 |
| `search.max_results_per_run` | `2000` | 1..10000, общий distinct budget run |

Default search — `20 x 100`, но distinct cap остаётся `2000`. Pagination прекращается при конце выдачи, достижении page/result budget или durable checkpoint.

### Hard filters

| Путь | Default | Допустимые значения |
|---|---|---|
| `filters.excluded_keywords` | `[]` | 0..1000 строк 1..200, case-insensitive |
| `filters.required_keywords` | `[]` | 0..1000 строк 1..200 |
| `filters.allowed_role_families` | `[]` | 0..1000 строк 1..200 |
| `filters.areas` | `[]` | 0..500 HH numeric ID strings |
| `filters.remote` | `any` | `any`, `only`, `exclude` |
| `filters.schedules` | `[]` | 0..100 current HH dictionary values |
| `filters.employment_types` | `[]` | 0..100 current HH dictionary values |
| `filters.experience_levels` | `[]` | 0..100 current HH dictionary values |
| `filters.languages` | `[]` | 0..100 BCP-47 tags |
| `filters.citizenships` | `[]` | 0..500 HH numeric ID strings |
| `filters.required_application_capabilities` | `[]` | subset of `direct`, `screening`, `form` |
| `filters.minimum_salary` | `0` | integer 0..1000000000 |
| `filters.salary_currency` | `RUR` | три ASCII letter, нормализуется uppercase |
| `filters.unknown_salary` | `allow` | `allow`, `reject` |
| `filters.use_employer_blacklist` | `true` | boolean |

Непустые schedule/employment/experience сверяются с текущими HH dictionaries. Если dictionary недоступен, live runtime validation не угадывает значение.

### Limits и ranking

| Путь | Default | Допустимые значения |
|---|---|---|
| `limits.daily_success` | `50` | 1..`administrative_max_daily_success` |
| `limits.per_run_success` | `10` | 1..daily и <= administrative max |
| `limits.administrative_max_daily_success` | `200` | 1..200 |
| `limits.send_delay_min_seconds` | `45` | 0..3600 |
| `limits.send_delay_max_seconds` | `120` | min..3600 |
| `ranking.minimum_score` | `60` | 0..100 |
| `ranking.ai_mode` | `borderline` | `off`, `borderline`, `all` |
| `ranking.borderline_low` | `50` | 0..minimum score |
| `ranking.borderline_high` | `70` | minimum score..100 |
| `ranking.minimum_ai_confidence` | `0.7` | finite 0..1 |
| `ranking.ai_detail` | `light` | `light`, `heavy` |
| `ranking.ai_failure_policy` | `retry` | `retry`, `deterministic`, `skip` |
| `ranking.weights.role` | `0.30` | finite >= 0 |
| `ranking.weights.skills` | `0.30` | finite >= 0 |
| `ranking.weights.experience` | `0.15` | finite >= 0 |
| `ranking.weights.salary` | `0.10` | finite >= 0 |
| `ranking.weights.work_format` | `0.10` | finite >= 0 |
| `ranking.weights.area` | `0.05` | finite >= 0 |
| `ranking.weights.industry` | `0.0` | finite >= 0 |

Сумма weights должна быть положительной; parser нормализует её к `1.0`. Hard filters всегда выполняются до ranking/AI.

### Retry, lease, application, browser и retention

| Путь | Default | Допустимые значения |
|---|---|---|
| `retry.max_attempts` | `4` | 1..20 actual application dispatches |
| `retry.base_delay_seconds` | `60` | 1..86400 |
| `retry.max_delay_seconds` | `3600` | base..86400 |
| `retry.jitter_ratio` | `0.25` | finite 0..1 |
| `retry.auth_recovery_attempts` | `1` | 1..20 |
| `retry.reconciliation_delay_seconds` | `30` | 1..86400 |
| `retry.reconciliation_checks` | `3` | 1..20 |
| `lease.ttl_seconds` | `120` | 1..600 |
| `lease.request_timeout_seconds` | `30` | 1..600 |
| `lease.renewal_margin_seconds` | `45` | 1..600 |
| `application.resume_policy` | `best_resume_only` | `best_resume_only`, `per_resume` |
| `application.cover_letter_mode` | `template` | `none`, `template`, `ai` |
| `application.screening_mode` | `profile_grounded` | `off`, `profile_grounded` |
| `application.form_mode` | `profile_grounded` | `off`, `profile_grounded` |
| `application.captcha_mode` | `manual_handoff` | только `manual_handoff` |
| `application.challenge_expiry_hours` | `24` | 1..720 |
| `browser.headless` | `true` | boolean; auth/handoff всё равно требует visible browser |
| `browser.navigation_timeout_seconds` | `30` | 1..600 |
| `notifications.challenge` | `true` | boolean |
| `notifications.run_failure` | `true` | boolean |
| `retention.challenge_artifact_days` | `7` | 1..3650 |
| `retention.event_days` | `180` | 1..3650 |

Lease требует `ttl_seconds >= request_timeout_seconds + renewal_margin_seconds`.

### HH search presets

Preset хранится рядом, в root-объекте `hh_campaign_presets`, а `preset_names` только ссылается на него.

| Поле preset | Допустимые значения |
|---|---|
| `text` | строка 1..200 |
| `area`, `professional_role`, `employer_id`, `excluded_employer_id` | 1..500 HH numeric ID strings |
| `industry` | 1..500 composite HH IDs, например `7.540` |
| `schedule` | один current HH dictionary value |
| `employment` | 1..100 current HH dictionary values |
| `experience` | один current HH dictionary value |
| `salary` | integer 0..1000000000 |
| `only_with_salary`, `no_magic`, `premium` | boolean |
| `date_from`, `date_to` | ISO date; from <= to |
| `search_field` | 1..3 из `name`, `company_name`, `description` |
| `order_by` | `publication_time`, `salary_desc`, `salary_asc`, `relevance`, `distance` |
| `period` | 1..30 days |
| `currency` | три ASCII letter, uppercase |

### Пример: два аккаунта, три резюме, два preset

Это фрагмент общего config. Остальные поля autopilot возьмут defaults.

```json
{
  "hh_account_profiles": {
    "default": {"access_token": "LOCAL_SECRET"},
    "second": {"access_token": "LOCAL_SECRET"}
  },
  "profiles": {
    "default": {"skills": ["Python", "FastAPI"], "salary_min": 250000},
    "second": {"skills": ["TypeScript", "React"], "salary_min": 220000}
  },
  "hh_campaign_presets": {
    "backend": {
      "text": "Python OR FastAPI",
      "area": ["1", "2"],
      "schedule": "remote",
      "order_by": "publication_time"
    },
    "frontend": {
      "text": "TypeScript React",
      "area": ["1"],
      "employment": ["full"],
      "period": 7
    }
  },
  "sources": {
    "hh": {
      "autopilot": {
        "accounts": [
          {
            "profile_id": "default",
            "candidate_profile_id": "default",
            "enabled": false,
            "paused": false,
            "authorization_generation": null,
            "resume_queries": [
              {"resume_id": "111111", "preset_names": ["backend"]},
              {"resume_id": "222222", "preset_names": ["backend"]}
            ]
          },
          {
            "profile_id": "second",
            "candidate_profile_id": "second",
            "enabled": false,
            "paused": false,
            "authorization_generation": null,
            "resume_queries": [
              {"resume_id": "333333", "preset_names": ["frontend"]}
            ]
          }
        ],
        "limits": {"daily_success": 20, "per_run_success": 3},
        "search": {"per_page": 100, "max_pages": 20, "max_results_per_run": 2000}
      }
    }
  }
}
```

После изменения policy-полей существующий grant больше не соответствует policy hash. Повторите validation, shadow/canary и `enable --confirm`.

## 5. Rollout: validate -> shadow -> canary -> one -> normal

1. `validate` проверяет schema/bounds и выбранный account.
2. `shadow` проверяет реальный read-only поиск, filters и ranking без HH mutation.
3. `canary` отправляет максимум один отклик на явно названную пару resume/vacancy.
4. Перед первым обычным enable задайте `limits.per_run_success: 1`.
5. После просмотра status/history верните нужный per-run limit и заново выдайте grant.

```powershell
work-hunter --root . hh autopilot validate --account default
work-hunter --root . hh autopilot shadow --account default --resume RESUME_ID --preset backend
work-hunter --root . hh autopilot history --account default
work-hunter --root . hh autopilot canary --account default --resume RESUME_ID --vacancy VACANCY_ID --confirm
work-hunter --root . hh autopilot enable --account default --confirm
work-hunter --root . hh autopilot run-now --account default
work-hunter --root . hh autopilot status --account default
```

`validate` сам по себе не доказывает auth, published resume или live HH layout. Эти зависимости проявляются при shadow/canary/enable/run.

## 6. CLI и эквивалентные UI actions

UI запускается командой `work-hunter --root . ui` и находится в **Applications -> HH Autopilot**.

| CLI | UI |
|---|---|
| `validate [--account ID]` | **Проверить** |
| `status [--account ID]` | status/queue + **Обновить** |
| `enable (--account ID|--all) --confirm` | **Включить**, confirmation sheet |
| `disable (--account ID|--all) --confirm` | **Выключить**, confirmation sheet |
| `shadow --account ID [--resume ID] [--preset NAME]` | **Shadow run** |
| `canary --account ID --resume ID --vacancy ID --confirm` | **Canary 1 отклик**, confirmation sheet |
| `run-now (--account ID|--all)` | **Запустить сейчас** |
| `recover-now [--account ID]` | **Recovery** |
| `pause (--account ID|--all)` | **Пауза** |
| `resume (--account ID|--all)` | **Продолжить** |
| `stop --account ID --run-id N` | run id + **Stop** |
| `kill-switch (--account ID|--all|--global) --confirm` | **Kill**, выбранный account/all scope |
| `clear-kill-switch (...) --confirm` | **Снять kill** |
| `retry --account ID --item-id N` | queue item retry action/API |
| `challenges [--account ID]` | **Challenges** list |
| `resolve-challenge --account ID --challenge-id N --action ACTION` | challenge action |
| `history [--account ID] [--limit N]` | **Журнал** + **Ещё** |

Challenge actions: `completed`, `dismissed`, `confirmed_applied`, `confirmed_not_applied_retry`, `confirmed_not_applied_skip`, `retry_reconciliation`, `auth_restored`.

Advanced JSON в UI редактирует все обычные config-поля. `enabled` и `authorization_generation` остаются service-managed. Loopback API использует те же действия под `/api/hh/autopilot/*`; подтверждение — literal JSON `true`, не строка `"true"`.

## 7. Scheduler на Windows и Linux

`examples/hh-autopilot-runner.json` сначала запускает grant-independent recovery, затем scheduler tick. Внутренний default interval — 60 минут; внешний tick можно вызывать каждые 15 минут, чтобы быстрее подхватывать recovery и границу окна.

### Windows Task Scheduler

Выполните из корня репозитория:

```powershell
$root = (Resolve-Path .).Path
$exe = (Resolve-Path .\.venv\Scripts\work-hunter.exe).Path
$plan = (Resolve-Path .\examples\hh-autopilot-runner.json).Path
$taskCommand = "`"$exe`" --root `"$root`" runner --plan `"$plan`""
schtasks.exe /Create /TN "Work Hunter HH Autopilot" /SC MINUTE /MO 15 /TR $taskCommand /F
schtasks.exe /Run /TN "Work Hunter HH Autopilot"
schtasks.exe /Query /TN "Work Hunter HH Autopilot" /V /FO LIST
```

Удаление task:

```powershell
schtasks.exe /Delete /TN "Work Hunter HH Autopilot" /F
```

### Linux cron

Сначала один раз выполните `work-hunter --root /opt/work-hunter init`. Затем добавьте через `crontab -e`:

```cron
*/15 * * * * /opt/work-hunter/.venv/bin/work-hunter --root /opt/work-hunter runner --plan /opt/work-hunter/examples/hh-autopilot-runner.json >> /opt/work-hunter/.work-hunter/runner-cron.log 2>&1
```

Runner использует `.work-hunter/runner.lock`. Второй процесс возвращает `runner_locked`. JSON reports находятся в `.work-hunter/reports/`.

## 8. Quotas, delays, windows, cooldown, retry и recovery

- Default quota: `50/day`, `10/run`. День считается в `timezone` аккаунта.
- Reservation создаётся до POST. Success её consume; точно не отправленная ошибка или permanent skip освобождает.
- Manual/canary success учитывается в том же account daily quota.
- Delay между отправками выбирается в диапазоне `45..120s` по default.
- Scheduler отправляет только в `days`, `08:00..21:00 Europe/Moscow`, раз в 60 минут по default.
- `429`/`rate_limited` и `hh_daily_limit` ставят account-wide cooldown. `Retry-After` имеет приоритет; без reset daily-limit ждёт следующего локального дня.
- Retry backoff: `min(max, base * 2^(attempt-1))`, затем jitter `1 +/- jitter_ratio`. Default: 4 dispatch attempts, 60s base, 3600s max, 25% jitter.
- Possibly-sent результат никогда не получает немедленный второй POST. Он переходит в `reconciling` и сначала читает negotiations.
- Recovery подбирает stale `applying`/`reconciling`, захватывает новый lease и использует сохранённую authorization provenance. Новый application grant для read-only reconciliation не нужен.
- Lease default: TTL 120s, request timeout 30s, renewal margin 45s. Потеря fencing ownership прекращает новые mutations.

Ручной recovery и retry:

```powershell
work-hunter --root . hh autopilot recover-now --account default
work-hunter --root . hh autopilot retry --account default --item-id ITEM_ID
```

`retry` не обходит unresolved ambiguity, quota, cooldown, kill switch или grant checks.

## 9. Screening и формы

`profile_grounded` заполняет только явно сохранённые факты: имя, город, телефон, email, citizenship/work permit, salary и подготовленное cover letter. Значения проверяются против field type/options.

Автопилот не ищет ответы в произвольной подписи поля и не придумывает опыт. Результаты:

- неизвестное обязательное поле -> `missing_required_data`, vacancy пропускается;
- обязательный test/assessment/file/неподдерживаемый тип -> `manual_assessment` challenge;
- `screening_mode: off` -> `screening_disabled`;
- `form_mode: off` -> `form_disabled`;
- поддерживаемая форма с полными grounded answers может быть отправлена normal executor.

Другие vacancies продолжают run, пока одна ждёт пользователя.

## 10. CAPTCHA и assessment: только manual handoff

Автоматического CAPTCHA solver нет. Режим только один: `application.captcha_mode: manual_handoff`.

При CAPTCHA автопилот:

1. не кликает CAPTCHA и не отправляет случайный ответ;
2. создаёт один idempotent `manual_captcha` challenge с URL без query/fragment;
3. освобождает обычную dispatch reservation;
4. сохраняет HH cookies в приватной browser session;
5. ждёт ручного решения в видимом Chromium;
6. после `completed` возвращает item в `ready` или `reconciling` по исходной стадии.

```powershell
work-hunter --root . hh autopilot challenges --account default
work-hunter --root . hh autopilot resolve-challenge --account default --challenge-id CHALLENGE_ID --action completed
```

Если пользователь отказался:

```powershell
work-hunter --root . hh autopilot resolve-challenge --account default --challenge-id CHALLENGE_ID --action dismissed
```

`dismissed`/expiry переводит обычный CAPTCHA/assessment item в `skipped`. Login CAPTCHA также остаётся ручной в browser profile. Cookie continuity не равна CAPTCHA solving.

## 11. Ambiguous application и held quota

Если POST мог быть принят, а ответ потерян, reservation переходит в `held`. Пока ambiguity не снята, слот считается занятым и повторный POST запрещён.

Read-only reconciliation ищет negotiation по account, vacancy, resume и времени:

- точное совпадение -> `applied`, held quota становится consumed;
- старый/другой resume -> `duplicate_external`/`external_applied`, новая reservation освобождается;
- нет совпадения после checks -> item может вернуться в `ready`, если dispatch budget ещё есть;
- неразличимый remote record -> `ambiguous_application` challenge, quota остаётся held.

Ручные actions для ambiguity:

- `confirmed_applied` — зафиксировать application и consume quota;
- `confirmed_not_applied_retry` — освободить quota и вернуть в `ready`;
- `confirmed_not_applied_skip` — освободить quota и завершить `skipped`;
- `retry_reconciliation` — оставить held и повторить read-only проверку.

Expired/dismissed ambiguity становится `dead`, но held quota остаётся до явной классификации. Это намеренно: неизвестный POST нельзя считать точно неотправленным.

## 12. Pause, disable, stop и kill switch

| Control | Grant | Текущий run | Следующий schedule | Recovery |
|---|---|---|---|---|
| `pause` | сохраняется | новые dispatch прекращаются | блокируется | разрешён |
| `disable --confirm` | отзывается | получает stop request после in-flight call | блокируется | разрешён |
| `stop --run-id` | сохраняется | останавливает только named run | остаётся доступен | разрешён |
| `kill-switch --account/--all` | отзывается | новые mutations запрещены сразу | блокируется | read-only/local finalization разрешены |
| `kill-switch --global` | все grants отозваны | блокирует все accounts | блокируется | разрешён |

`clear-kill-switch --confirm` только снимает блокировку. Он не восстанавливает grants: после него нужен новый `enable --confirm`.

## 13. Secrets, screenshots, retention и backup/restore

`config --json`, CLI responses, runner reports и journal маскируют token/password/secret/key-поля. Events не должны содержать access/refresh tokens, cookies, Authorization headers, proxy passwords или полные form values.

```powershell
work-hunter --root . config --json
rg.exe -n "access_token|refresh_token|Authorization|cookie|proxy" .work-hunter\reports .work-hunter\private
```

Совпадение имени masked-поля или имени файла допустимо. Реальное secret value — нет.

Browser cookies/profile хранятся в `.work-hunter/private/`. Core не делает CAPTCHA screenshot автоматически. Если сохраняете screenshot вручную, кладите его только под `private/`, не в reports и не в git.

`retention.challenge_artifact_days` и `retention.event_days` валидируются как policy values. В текущем core нет отдельного automatic purge job. Эти значения не являются доказательством удаления; чистите старые artifacts/events вручную до parity-maintenance cleanup.

Для backup сначала остановите scheduled task/UI/runner, затем копируйте весь private state:

```powershell
$stamp = Get-Date -Format yyyyMMdd-HHmmss
Copy-Item .work-hunter ".work-hunter-backup-$stamp" -Recurse
```

Restore: остановите процессы, переименуйте повреждённую `.work-hunter`, верните backup под именем `.work-hunter`, затем выполните `hh autopilot status` и `recover-now`. Не объединяйте две SQLite-копии вручную.

## 14. Troubleshooting и stable outcome codes

Journal использует стабильные outcome codes. Filter/ranking evidence дополнительно использует `hard_filter:*`, `deterministic_*` и `ai_*` reasons.

| Codes | Что делать |
|---|---|
| `applied` | Успех; проверьте application/history |
| `duplicate` | Запустить/дождаться reconciliation; второй POST запрещён |
| `duplicate_external`, `external_applied` | На HH уже есть отклик; новый POST/quota не нужен |
| `ambiguous_remote_result`, `ambiguous_application`, `unresolved_ambiguity` | Не requeue; классифицировать challenge |
| `vacancy_closed`, `forbidden`, `invalid_request` | Permanent skip; исправить target/policy, не retry вслепую |
| `missing_required_data`, `screening_disabled`, `form_disabled`, `ai_unavailable` | Дополнить profile/config либо оставить skip |
| `manual_assessment`, `manual_captcha`, `form_required` | Открыть challenges, выполнить ручной шаг |
| `hh_daily_limit`, `rate_limited` | Ждать account cooldown/reset |
| `auth_expired`, `manual_auth` | `hh auth login/refresh`, затем `auth_restored` или `completed` |
| `challenge_expired`, `challenge_dismissed` | Item завершён; ambiguity всё ещё требует явной классификации |
| `pre_dispatch_network_error` | Точно не отправлено; bounded retry допустим |
| `post_dispatch_network_error`, `post_dispatch_parse_error` | Возможно отправлено; только reconciliation |
| `server_error`, `read_parse_error`, `internal_error`, `interrupted` | Посмотреть masked journal, затем bounded retry/recovery |
| `retry_exhausted` | Исправить причину и вручную `retry`; проверки не обходятся |
| `authorization_state_mismatch` | Policy/grant изменился; validate -> shadow/canary -> enable |

Полезная диагностика:

```powershell
work-hunter --root . doctor --json
work-hunter --root . hh auth status --account default
work-hunter --root . hh autopilot status --account default
work-hunter --root . hh autopilot history --account default --limit 100
work-hunter --root . hh autopilot challenges --account default
work-hunter --root . hh autopilot recover-now --account default
```

## 15. Verification, live-canary и границы maintenance slice

Deterministic gates проверяют локальный контракт, fake HH transport, restart/concurrency и HTML fixtures. Они не доказывают текущую live-верстку, anti-bot поведение или API HH.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_package_contract.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_hh_autopilot_e2e.py -m restart -q
.\.venv\Scripts\python.exe -m pytest tests/test_hh_autopilot_e2e.py -m concurrency -q
.\.venv\Scripts\python.exe -m pytest tests/test_hh_autopilot_challenges.py tests/test_hh_autopilot_web.py -q
```

| Проверяемый контракт | Deterministic evidence |
|---|---|
| packaged migrations/static/default-disabled/help masking | `tests/test_package_contract.py` |
| fake HH POST и safe journal | `test_real_hh_client_creates_application_and_journals_only_safe_fields` |
| accepted-then-disconnected restart без второго POST | `test_socket_close_after_accept_reconciles_without_a_second_post` |
| account lease/concurrency | `test_account_lease_allows_one_owner_and_keeps_other_account_independent` |
| grounded form и manual CAPTCHA cookie continuity | `tests/test_hh_autopilot_challenges.py` |
| exact account scope и literal UI/API confirmation | `tests/test_hh_autopilot_web.py` |

Fake-HH и local browser contracts **не доказывают текущий live HH**. Такое доказательство даёт только успешный opt-in named canary:

```powershell
$account = "default"
$resume = "RESUME_ID"
$vacancy = "VACANCY_ID"
work-hunter --root . hh autopilot canary --account $account --resume $resume --vacancy $vacancy --confirm
work-hunter --root . hh autopilot history --account $account --limit 20
```

Запишите дату, git revision, masked account, vacancy ID, outcome и journal ID. Не записывайте credentials или form answers. Пока такого результата нет, корректная формулировка релиза: **deterministic contract verified; current live HH application flow not proven**.

Текущий application grant не разрешает автономный resume raise/update, employer replies, recruiter email, negotiation/skipped cleanup или независимый token-refresh schedule. Их существующие вручную подтверждаемые операции остаются доступны. Автоматизация этих действий относится к отдельно одобренному parity-maintenance slice и требует отдельных grants.
