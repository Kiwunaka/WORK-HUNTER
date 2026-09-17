# Changelog

## Unreleased - 2026-09-17

### HH autopilot: живой прогон до отклика

- `filters.required_keywords` теперь «хотя бы одно слово»: список ролей из правил поиска больше не требует одновременного присутствия всех фраз, из-за чего отбраковывались все рекомендации.
- Policy hash считается по стабильной проекции резюме: счётчики просмотров, `next_publish_at`, `actions` и прочие меняющиеся поля больше не инвалидируют grant автопилота.
- Элементы прерванных прогонов переиспользуются: `discovered` заново закрепляется за текущим run, `ready` усыновляется перед отправкой; иначе `prepare_dispatch` падал с `run_not_active`, а повторная оценка сотен отклонённых вакансий жгла AI-вызовы.
- Lease продлевается перед записью после долгих операций (AI-ранжирование), терминальные элементы не переоцениваются.
- AI-ранкер читает `professional_role_segments` из резюме HH, а промпт требует дословных цитат из фактов вместо пересказа.
- Сопроводительное рендерится и для вакансий, которых ещё нет в `jobs`: вакансия подтягивается из HH API и сохраняется вместе с письмом.
- Ошибка прогона хранит сообщение, а не только класс исключения.

### Проверено живым прогоном

- Три отклика отправлены (Ozon, Лента, ProfiStaff): сопроводительные сохранены, HH ответил `success`, в `hh_negotiations` появились записи `response`.

### Запуск

- `start-work-hunter.bat` берёт host/port из конфига, ждёт готовности сервера и только потом открывает браузер; `.gitattributes` фиксирует CRLF для `.bat`, иначе cmd ломает разбор файла.

## Unreleased - 2026-07-13

### HH native challenge resolution

- Submit current HH `vacancyTests` through the native `task_*` web payload with configurable AI answers.
- Fill supported redirect forms grounded-first, with configurable AI completion for remaining fields.
- Solve application CAPTCHA through a Vision-capable OpenAI-compatible model in the persisted HH browser session, synchronize cookies back to the API transport, retry the original application, and retain manual fallback.

### HH application and maintenance parity

- Render configurable template or AI cover letters inside the production autopilot instead of requiring a pre-saved draft; cache each rendered letter for stable retries.
- Feed the production hard filter with the persisted employer blacklist and the application capabilities actually enabled by `screening_mode` and `form_mode`.
- Optionally hide cleaned negotiation chats through the authenticated HH web endpoint, matching the original tool's `--delete-chat` flow.
- Expire due CAPTCHA/form/ambiguity challenges from the production scheduler under the account lease instead of leaving items stuck indefinitely.
- Dispatch due application and AI-ranking retries between full search intervals with configured exponential backoff, jitter, and bounded exhaustion.
- Run paginated employer replies, resume raising, recruiter follow-up, negotiation cleanup, and skipped-state maintenance through the configurable JSON runner.
- Generate employer replies from full paginated chat history and suppress duplicate replies and email follow-ups.

### Apple HIG cockpit redesign

- Replace the legacy navigation with eight canonical destinations and a deterministic **Today** dashboard.
- Add a three-step first-run onboarding flow with safe deferral, reload recovery, and profile-scoped resume activation.
- Add contextual coach marks, accessible notifications, sheets, popovers, inline failures, and empty states.
- Add full, compact, and mobile responsive shell modes with local SVG icons and reduced-motion support.
- Group Applications, Analytics, and Settings into URL-synchronized tabs while preserving existing workflows.

### Safety and compatibility

- Replace browser alerts and confirms with typed feedback and live-action safety sheets.
- Revalidate HH mutations immediately before execution and retain literal-boolean server confirmation guards.
- Preserve legacy deep links through canonical URL replacement and package all local UI controller assets.

## 1.0.0 - 2026-07-10

### Safety

- Require literal confirmation for every HH mutation, including API Lab and resume operations.
- Reject cross-origin, non-JSON, DNS-rebinding, and non-loopback cockpit mutations.
- Preserve masked configuration secrets during UI updates.

### Data correctness

- Persist OAuth token rotation atomically and isolate scores by active profile.
- Enforce SQLite foreign keys and atomic migrations.
- Preserve query-based vacancy identity and honor ghost-job age.

### Browser cockpit

- Preserve resume fields, target ghost rows explicitly, and default agent preflight to dry mode.
- Add keyboard operation, resilient loading, safe rendering, and offline assets.

### Packaging and verification

- Package UI and SQL resources in the 1.0.0 wheel.
- Add a non-root wheel-based Docker build definition; final image build and safe smoke remain pending release verification.
