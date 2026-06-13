# Ultimate HH Personal Tool Harvest Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the ultimate personal HH automation tool inside `work_hunter` by harvesting the strongest licensed parts from the original project and forks: hybrid transport, agent brain, human approval loop, cockpit UI, Telegram remote, curated apply, events, forms, and raw API lab.

**Architecture:** Keep `work_hunter` as the single product shell. Do not create a second `hh_applicant_tool` app inside it. Add layered HH modules: transport facade, agent/audit brain, approval queue, cockpit HTTP API, web UI, Telegram remote, and optional automation surfaces. Real apply/reply remains explicit, auditable, and routed through existing `WorkHunter` campaign/apply confirmation points.

**Tech Stack:** Python 3.11+, SQLite, existing `work_hunter` CLI/web server, HH REST API, Playwright/browser cookies, MCP, provider-neutral structured LLM calls, Telegram via aiogram or thin HTTP sink.

**Licensing:** User confirmed licenses are acceptable for harvesting. Preserve source attribution in commit messages/docs when directly copying substantial code. Still avoid copying personal secrets, personal prompts, and machine-specific scripts as defaults.

---

## 3-Agent Audit Result

This plan incorporates three high-effort read-only agent audits:

- Agent A: architecture core: `0FL01`, original `s3rgeym`, `KOVALSKl`, `SpitiusK`.
- Agent B: cockpit/UI/Telegram/control plane: `orelkrylatiy`, `pomogashkin`, `probox36`, `1MaxSpb`, `MyNameRoman`, `SpitiusK`.
- Agent C: small dirty superpowers: `Kj9516`, `roman-redl`, `Eastonn`, `Cafedupont`, `Reancoree`, `LazarenkoA`, `Lexwxd`, `ressiwage`, `ring-rong`, original transport details.

Key correction from the audit: `roman-redl`'s `create_resume.py`, `resume_md.py`, `clear_skipped.py`, and scripts exist on `origin/my-changes`, even if the local working tree is on another branch. Use branch links/source tree, not the checked-out branch, when harvesting.

---

## Final Product Shape

The final tool should feel like a personal command center, not a public SaaS app:

1. `HHHybridClient`: API + browser-cookie + web-action facade.
2. `HHVacancyResearchService`: search, analyze, precheck, plan, apply.
3. `HHAgentAudit`: every LLM decision, run, attempt, approval, and webhook stored.
4. `Approval Queue`: autonomous until uncertainty/risk; then Approve/Reject/Modify.
5. `Persona Context`: stable user profile used for letters, replies, forms, and resumes.
6. `Cockpit HTTP API`: stable local endpoints for UI, MCP, Telegram, and future agents.
7. `Web UI`: dashboard, runs, approvals, inbox, events, API lab, templates, blacklist.
8. `Telegram Remote`: status, digest, pending approvals, modify loop, pause/resume.
9. `Curated Apply`: CSV/TSV target lists with deterministic state and reports.
10. `Events/Tasks`: detect interviews, deadlines, tests, follow-ups; export ICS/Markdown.
11. `Raw HH API Lab`: arbitrary HH method/path/body calls with saved snippets.
12. `Notification Sink`: Telegram/webhook/file summaries for scheduled and manual runs.

---

## Existing `work_hunter` Strengths To Keep

Do not replace these; wire new modules into them:

- HH config/account profiles and secret masking: `work_hunter/config.py`.
- HH API client surface: `work_hunter/sources/hh.py`.
- Explicit apply confirmation: `WorkHunter.prepare_apply_plan`, `WorkHunter.confirm_apply`, `WorkHunter.confirm_hh_campaign`.
- Existing HH storage: resumes, employers, contacts, negotiations, skipped vacancies, campaign runs/items.
- Existing web UI/server: `work_hunter/web/server.py`, `work_hunter/web/static/*`.
- Existing CLI commands: `hh-call-api`, `hh-campaign-plan`, `hh-campaign-confirm`, `hh-resumes`, `hh-negotiations`, `hh-reply-employers`.
- Existing tests: HH auth, operations, onboarding, campaign outcomes, AI filter, question assistant, resume operations, MCP safety.

---

## Donor Matrix

### Take Almost Whole

#### Original + 0FL01: Hybrid HH Transport

Sources:

- Original upstream/client shape: [s3rgeym api/client.py](https://github.com/s3rgeym/hh-applicant-tool/blob/main/src/hh_applicant_tool/api/client.py)
- Original cookie jar: [s3rgeym utils/cookiejar.py](https://github.com/s3rgeym/hh-applicant-tool/blob/main/src/hh_applicant_tool/utils/cookiejar.py)
- Original authorization: [s3rgeym operations/authorize.py](https://github.com/s3rgeym/hh-applicant-tool/blob/main/src/hh_applicant_tool/operations/authorize.py)
- Friend OAuth/client identity: [0FL01 api/client.py](https://github.com/0FL01/hh-applicant-tool/blob/llm-agent/src/hh_applicant_tool/api/client.py)
- Friend client constants mechanism: [0FL01 api/client_keys.py](https://github.com/0FL01/hh-applicant-tool/blob/llm-agent/src/hh_applicant_tool/api/client_keys.py)
- Friend user agent: [0FL01 api/user_agent.py](https://github.com/0FL01/hh-applicant-tool/blob/llm-agent/src/hh_applicant_tool/api/user_agent.py)

Take:

- Android-style OAuth/client identity mechanism.
- Token refresh lifecycle.
- Android-like User-Agent generation.
- HH-only cookie jar.
- Playwright auth and cookie sync.
- XSRF extraction.
- Web-only action support for response popup and cleanup flows.
- Structured challenge outcomes: captcha, test, manual form, failed, solved.

Put in:

- `work_hunter/hh_transport/identity.py`
- `work_hunter/hh_transport/api_session.py`
- `work_hunter/hh_transport/browser_session.py`
- `work_hunter/hh_transport/web_actions.py`
- `work_hunter/hh_transport/challenges.py`
- `work_hunter/hh_transport/user_agent.py`
- `work_hunter/hh_transport/cookiejar.py`

Important:

- Keep actual token/client-secret/cookie values in config/env/storage.
- Never print secrets in CLI/UI/logs.
- The mechanism is useful; personal credential values are not code.

#### 0FL01 `llm-agent`: Agent Brain

Sources:

- MCP tools: [0FL01 mcp/tools.py](https://github.com/0FL01/hh-applicant-tool/blob/llm-agent/src/hh_applicant_tool/mcp/tools.py)
- Research service: [0FL01 vacancy_research.py](https://github.com/0FL01/hh-applicant-tool/blob/llm-agent/src/hh_applicant_tool/services/vacancy_research.py)
- Policy: [0FL01 policy.py](https://github.com/0FL01/hh-applicant-tool/blob/llm-agent/src/hh_applicant_tool/services/policy.py)
- DTOs: [0FL01 types.py](https://github.com/0FL01/hh-applicant-tool/blob/llm-agent/src/hh_applicant_tool/services/types.py)
- Structured LLM: [0FL01 openrouter.py](https://github.com/0FL01/hh-applicant-tool/blob/llm-agent/hh_llm_agent/openrouter.py)
- Chat agent: [0FL01 service.py](https://github.com/0FL01/hh-applicant-tool/blob/llm-agent/hh_llm_agent/service.py)
- Audit schema: [0FL01 schema.sql](https://github.com/0FL01/hh-applicant-tool/blob/llm-agent/src/hh_applicant_tool/storage/queries/schema.sql)

Take:

- MCP HH tool taxonomy.
- Run accounting: start, finish, error, summaries.
- `VacancyPolicy` shape.
- Research/analyze/apply service boundaries.
- Hard precheck pattern.
- Dedupe key idea.
- Structured LLM schema parsing/retry behavior.
- Tables for MCP runs, vacancy analysis, attempts, response dedupe.
- Chat classifier/outbox/webhook concepts.

Put in:

- `work_hunter/hh_agent/policy.py`
- `work_hunter/hh_agent/types.py`
- `work_hunter/hh_agent/dedupe.py`
- `work_hunter/hh_agent/research.py`
- `work_hunter/hh_agent/mcp_handlers.py`
- `work_hunter/hh_agent/chat_service.py`
- `work_hunter/hh_agent/webhook.py`
- `work_hunter/llm/structured.py`

#### KOVALSKl: Async Client And Backends

Sources:

- [KOVALSKl async_client.py](https://github.com/KOVALSKl/hh-applicant-tool/blob/main/src/hh_applicant_tool/api/async_client.py)
- [KOVALSKl backends.py](https://github.com/KOVALSKl/hh-applicant-tool/blob/main/src/hh_applicant_tool/backends.py)

Take:

- Backend protocols for config/cookies.
- Async OAuth/API session shape.
- Cooperative rate limiting.
- Timeout/429/5xx retry logic.
- Async close/lifecycle.

Put in:

- `work_hunter/hh_transport/backends.py`
- `work_hunter/hh_transport/async_api_session.py`

Use as contract/shape, not as a wholesale replacement for existing `HHApplyClient` in one step.

#### SpitiusK: Approval Spine

Sources:

- Approval: [SpitiusK approval.py](https://github.com/SpitiusK/hh-applicant-tool/blob/sprint/agent-rework-2026-04-17/src/hh_applicant_tool/approval.py)
- Agent flow: [SpitiusK agent_flow.md](https://github.com/SpitiusK/hh-applicant-tool/blob/sprint/agent-rework-2026-04-17/docs/agent_flow.md)
- Pending messages model: [SpitiusK pending_message.py](https://github.com/SpitiusK/hh-applicant-tool/blob/sprint/agent-rework-2026-04-17/src/hh_applicant_tool/storage/models/pending_message.py)
- AI decision model: [SpitiusK ai_decision.py](https://github.com/SpitiusK/hh-applicant-tool/blob/sprint/agent-rework-2026-04-17/src/hh_applicant_tool/storage/models/ai_decision.py)
- Pending migration: [SpitiusK pending_messages migration](https://github.com/SpitiusK/hh-applicant-tool/blob/sprint/agent-rework-2026-04-17/src/hh_applicant_tool/storage/queries/migrations/20260419_pending_messages.sql)
- Decisions migration: [SpitiusK ai_decisions migration](https://github.com/SpitiusK/hh-applicant-tool/blob/sprint/agent-rework-2026-04-17/src/hh_applicant_tool/storage/queries/migrations/20260419_ai_decisions.sql)
- Modify handler: [SpitiusK modify_handler.py](https://github.com/SpitiusK/hh-applicant-tool/blob/sprint/agent-rework-2026-04-17/src/hh_applicant_tool/messaging/modify_handler.py)
- Sanity sampling: [SpitiusK sanity.py](https://github.com/SpitiusK/hh-applicant-tool/blob/sprint/agent-rework-2026-04-17/src/hh_applicant_tool/messaging/sanity.py)
- Persona context: [SpitiusK ai/context.py](https://github.com/SpitiusK/hh-applicant-tool/blob/sprint/agent-rework-2026-04-17/src/hh_applicant_tool/ai/context.py)
- Persona template: [SpitiusK persona.example.md](https://github.com/SpitiusK/hh-applicant-tool/blob/sprint/agent-rework-2026-04-17/config/persona.example.md)

Take:

- `should_escalate` decision policy.
- Persisted AI decisions.
- Pending messages.
- Approve/Reject/Modify state machine.
- Modify regeneration loop.
- Sanity sampling.
- Persona/context injection.

Adapt:

- Store in `work_hunter` SQLite, not loose JSONL as final state.
- Make LLM provider-neutral, not Claude-only.
- Use local HTTP API/UI/Telegram as surfaces for the same approval queue.

Put in:

- `work_hunter/hh_agent/approval.py`
- `work_hunter/hh_agent/persona.py`
- `work_hunter/hh_agent/sanity.py`
- `work_hunter/hh_agent/notifications.py`

#### orelkrylatiy: Local Cockpit Contract

Sources:

- [orelkrylatiy admin/app.py](https://github.com/orelkrylatiy/work-optimization/blob/main/admin/app.py)
- [orelkrylatiy admin/index.html](https://github.com/orelkrylatiy/work-optimization/blob/main/admin/index.html)
- [orelkrylatiy AGENT_GUIDE.md](https://github.com/orelkrylatiy/work-optimization/blob/main/docs/AGENT_GUIDE.md)

Take:

- Agent-friendly HTTP contract.
- Token status/preflight.
- Background operation tracking.
- Digest.
- Inbox/reply workflows.
- Letter templates.
- Employer blacklist.
- Operation status/cancel shape.

Put in:

- `work_hunter/hh_agent/http_api.py`
- `work_hunter/web/server.py`
- `work_hunter/web/static/*`

Use current `work_hunter` web server first. FastAPI is optional later; the important harvest is the endpoint contract and cockpit UX.

#### Kj9516: Curated Apply From File

Sources:

- [Kj9516 apply_from_file.py](https://github.com/Kj9516/hh-applicant-tool/blob/main/hh_applicant_tool/operations/apply_from_file.py)
- [Kj9516 applications.py](https://github.com/Kj9516/hh-applicant-tool/blob/main/hh_applicant_tool/applications.py)

Take:

- CSV/TSV input.
- Vacancy id or URL parsing.
- `enabled` column.
- Dry-run.
- Limit.
- Min interval.
- Processed-state.
- Row-level skip/report reasons.

Put in:

- `work_hunter/hh_agent/apply_from_file.py`
- `tests/test_hh_agent_apply_from_file.py`

This is a core personal power feature: manually curate a target list, then apply deterministically.

### Take Partially

#### 1MaxSpb: Raw API Lab And Progress UI

Sources:

- [1MaxSpb web_shell.py](https://github.com/1MaxSpb/hh-applicant-tool/blob/copilot/fix-c8e0f31d-422d-430d-803c-2ab88dbbe62b/hh_applicant_tool/operations/web_shell.py)
- [1MaxSpb WEB_SHELL.md](https://github.com/1MaxSpb/hh-applicant-tool/blob/copilot/fix-c8e0f31d-422d-430d-803c-2ab88dbbe62b/WEB_SHELL.md)
- [1MaxSpb MASS_APPLY_GUIDE.md](https://github.com/1MaxSpb/hh-applicant-tool/blob/copilot/fix-c8e0f31d-422d-430d-803c-2ab88dbbe62b/MASS_APPLY_GUIDE.md)

Take:

- HH API Lab concept.
- Quick calls.
- Arbitrary method/path/body.
- Pretty JSON output.
- Saved snippets.
- Progress counters/log shape.

Do not take:

- Single-file stdlib HTTP server as final implementation.
- Global mass-apply state.

#### pomogashkin + probox36: Telegram Remote

Sources:

- [pomogashkin bot directory](https://github.com/pomogashkin/hh-applicant-tool/tree/main/hh_applicant_tool/bot)
- [probox36 tg_client.py](https://github.com/probox36/hh-applicant-tool-tg/blob/tg_reports/hh_applicant_tool/telegram/tg_client.py)
- [probox36 formatters.py](https://github.com/probox36/hh-applicant-tool-tg/blob/tg_reports/hh_applicant_tool/formatters.py)

Take:

- aiogram skeleton.
- Middleware shape.
- OAuth callback with `state` as optional onboarding.
- Per-user token/preference storage concept.
- Telegram HTML sender.
- Vacancy/run summary formatters.

Adapt:

- Bot talks to local `work_hunter` HTTP API.
- Bot does not talk directly to HH in normal flow.
- Allowlist Telegram user ids.
- Escape Telegram HTML.

#### SpitiusK Events And Forms

Sources:

- Event watcher: [SpitiusK watch_events.py](https://github.com/SpitiusK/hh-applicant-tool/blob/sprint/agent-rework-2026-04-17/src/hh_applicant_tool/operations/watch_events.py)
- Export: [SpitiusK export_events.py](https://github.com/SpitiusK/hh-applicant-tool/blob/sprint/agent-rework-2026-04-17/src/hh_applicant_tool/operations/export_events.py)
- Event model: [SpitiusK event.py](https://github.com/SpitiusK/hh-applicant-tool/blob/sprint/agent-rework-2026-04-17/src/hh_applicant_tool/storage/models/event.py)
- Forms: [SpitiusK forms/filler.py](https://github.com/SpitiusK/hh-applicant-tool/blob/sprint/agent-rework-2026-04-17/src/hh_applicant_tool/forms/filler.py)
- Forms reviewer: [SpitiusK forms/reviewer.py](https://github.com/SpitiusK/hh-applicant-tool/blob/sprint/agent-rework-2026-04-17/src/hh_applicant_tool/forms/reviewer.py)
- Forms journal: [SpitiusK forms/journal.py](https://github.com/SpitiusK/hh-applicant-tool/blob/sprint/agent-rework-2026-04-17/src/hh_applicant_tool/forms/journal.py)

Take:

- Event/task detector idea.
- ICS/Markdown export idea.
- Form URL detection.
- Fill-review-submit pipeline concept.
- Unknown form field escalation.

Adapt:

- Manual-first: `form_mode = off | manual | agent`.
- Unknown/risky form submissions become pending approvals.
- Store events/forms in SQLite.

#### roman-redl: Personal Batch/Resume/Email Tricks

Sources:

- [roman-redl create_resume.py](https://github.com/roman-redl/hh-applicant-tool/blob/my-changes/src/hh_applicant_tool/operations/create_resume.py)
- [roman-redl resume_md.py](https://github.com/roman-redl/hh-applicant-tool/blob/my-changes/src/hh_applicant_tool/utils/resume_md.py)
- [roman-redl clear_skipped.py](https://github.com/roman-redl/hh-applicant-tool/blob/my-changes/src/hh_applicant_tool/operations/clear_skipped.py)
- [roman-redl apply_vacancies.py](https://github.com/roman-redl/hh-applicant-tool/blob/my-changes/src/hh_applicant_tool/operations/apply_vacancies.py)
- [roman-redl apply_all.sh](https://github.com/roman-redl/hh-applicant-tool/blob/my-changes/scripts/apply_all.sh)
- [roman-redl Dockerfile cache fix](https://github.com/roman-redl/hh-applicant-tool/blob/feature/dockerfile-fix/Dockerfile)

Take:

- Resume Markdown to HH resume payload.
- Multi-resume/multi-search batch matrix.
- Clear skipped workflow.
- Email/contact fixes.
- `--skip-tests` style explicit challenge switch.
- Cached Chromium/Playwright Docker layer idea.

Adapt:

- Batch matrix becomes config/UI preset, not personal shell script.
- Resume templates live under persona/profile model.
- Email/contact behavior gets tests against `work_hunter/sources/hh.py`.

#### Small Patches

Sources and harvest:

- Reancoree [apply_similar.py](https://github.com/Reancoree/hh-applicant-tool/blob/main/src/hh_applicant_tool/operations/apply_similar.py): `excluded_texts` substring filter.
- Eastonn [apply_similar.py](https://github.com/Eastonn/hh-applicant-tool/blob/fix/send-email-and-dockerfile/src/hh_applicant_tool/operations/apply_similar.py): `send_email` AttributeError/email handling.
- Eastonn [Dockerfile](https://github.com/Eastonn/hh-applicant-tool/blob/fix/send-email-and-dockerfile/Dockerfile): Docker deps hardening.
- Cafedupont [apply_similar.py](https://github.com/Cafedupont/hh-applicant-tool/blob/main/hh_applicant_tool/operations/apply_similar.py): experience/schedule/excluded employer filters.
- Cafedupont [clear_negotiations.py](https://github.com/Cafedupont/hh-applicant-tool/blob/main/hh_applicant_tool/operations/clear_negotiations.py): cleanup/blacklist hygiene.
- ressiwage [resume_info.py](https://github.com/ressiwage/CONT-hh-applicant-tool/blob/main/hh_applicant_tool/operations/resume_info.py): resume dump/export idea.
- MyNameRoman [`+ features/hh-update.sh`](https://github.com/MyNameRoman/hh-applicant-tool/blob/main/+%20features/hh-update.sh): scheduled command success/error notification model.
- Lexwxd [authorize.py](https://github.com/Lexwxd/hh-applicant-tool/blob/main/hh_applicant_tool/operations/authorize.py): `QT_QUICK_BACKEND=software` auth compatibility.
- LazarenkoA [Dockerfile](https://github.com/LazarenkoA/hh-applicant-tool/blob/patch-1/docker/Dockerfile): `dos2unix` line-ending hardening.
- ring-rong [crontab](https://github.com/ring-rong/hh-applicant-tool/blob/patch-1/docker/crontab): cron timing examples only.

---

## Do Not Pull As Defaults

- Do not import full `hh_applicant_tool` package layout into `work_hunter`.
- Do not base on `0FL01/main`; use `llm-agent`.
- Do not merge full SpitiusK sprint blindly; port subsystems.
- Do not accept KOVALSKl's unrelated upstream deletions/reshuffles.
- Do not make Claude, aiogram, Playwright, or captcha vision mandatory core dependencies.
- Do not allow real apply from MCP without explicit config + confirmation + audit.
- Do not copy personal shell scripts as default scheduled automation.
- Do not print or commit tokens, cookies, OAuth secrets, webhook secrets, SMTP secrets, or personal prompts.
- Do not make scheduled real apply/reply default.

---

## Target Module Layout

Create:

- `work_hunter/hh_transport/__init__.py`
- `work_hunter/hh_transport/identity.py`
- `work_hunter/hh_transport/backends.py`
- `work_hunter/hh_transport/api_session.py`
- `work_hunter/hh_transport/async_api_session.py`
- `work_hunter/hh_transport/browser_session.py`
- `work_hunter/hh_transport/web_actions.py`
- `work_hunter/hh_transport/challenges.py`
- `work_hunter/hh_transport/cookiejar.py`
- `work_hunter/hh_transport/user_agent.py`
- `work_hunter/hh_agent/__init__.py`
- `work_hunter/hh_agent/policy.py`
- `work_hunter/hh_agent/types.py`
- `work_hunter/hh_agent/dedupe.py`
- `work_hunter/hh_agent/research.py`
- `work_hunter/hh_agent/mcp_handlers.py`
- `work_hunter/hh_agent/approval.py`
- `work_hunter/hh_agent/persona.py`
- `work_hunter/hh_agent/sanity.py`
- `work_hunter/hh_agent/events.py`
- `work_hunter/hh_agent/forms.py`
- `work_hunter/hh_agent/notifications.py`
- `work_hunter/hh_agent/http_api.py`
- `work_hunter/hh_agent/apply_from_file.py`
- `work_hunter/hh_agent/resume_templates.py`
- `work_hunter/hh_agent/chat_service.py`
- `work_hunter/hh_agent/webhook.py`
- `work_hunter/hh_agent/telegram_bot.py`
- `work_hunter/llm/__init__.py`
- `work_hunter/llm/structured.py`

Modify:

- `work_hunter/storage.py`
- `work_hunter/models.py`
- `work_hunter/services.py`
- `work_hunter/mcp_server.py`
- `work_hunter/cli.py`
- `work_hunter/config.py`
- `work_hunter/web/server.py`
- `work_hunter/web/static/index.html`
- `work_hunter/web/static/app.js`
- `work_hunter/web/static/app.css`

---

## Target Storage Additions

Add tables:

- `hh_agent_mcp_runs`
- `hh_vacancy_analysis`
- `hh_application_attempts`
- `hh_vacancy_response_dedup`
- `hh_pending_messages`
- `hh_ai_decisions`
- `hh_agent_runs`
- `hh_operation_logs`
- `hh_agent_outbox`
- `hh_agent_webhooks`
- `hh_agent_events`
- `hh_agent_tasks`
- `hh_form_reviews`
- `hh_personas`
- `hh_apply_from_file_state`
- `hh_notification_events`
- `hh_letter_templates`
- `hh_employer_blacklist`
- `hh_api_lab_snippets`

Link new records back to existing tables where possible:

- `hh_campaign_runs`
- `hh_campaign_items`
- `hh_resumes`
- `hh_negotiations`
- `hh_contacts`
- `hh_skipped_vacancies`

---

## Build Order

### Task 0: Baseline And Safety Harness

**Files:**

- Modify: `docs/superpowers/plans/2026-06-09-hh-cleanroom-roadmap.md`
- Test: existing HH suite

- [x] Run `pytest tests/test_hh_auth.py tests/test_hh_campaign_outcomes.py tests/test_hh_operations.py tests/test_mcp_safety.py -q`.
- [x] Record current pass/fail state in the implementation log.
- [x] Confirm no secrets are present in planned source files with `rg -n "access_token|refresh_token|client_secret|cookie|telegram|smtp" work_hunter docs`.

### Task 1: Hybrid Transport Foundation

**Files:**

- Create: `work_hunter/hh_transport/*`
- Modify: `work_hunter/sources/hh.py`
- Modify: `work_hunter/config.py`
- Test: `tests/test_hh_transport.py`

- [x] Port HH-only cookie jar.
- [x] Port Android-style User-Agent generator.
- [x] Add `HHIdentity` with token lifecycle and overridable OAuth client values.
- [x] Add sync `HHApiSession`.
- [x] Add async `HHAsyncApiSession`.
- [x] Add backend protocols and file/config implementations.
- [x] Add `HHBrowserSession` for Playwright auth, cookies, and XSRF extraction.
- [x] Add `HHWebActions` for response popup and cleanup actions.
- [x] Add `HHChallengeHandler` with structured outcomes.
- [x] Keep existing `HHApplyClient` working while gradually delegating to the facade.
- [x] Run `pytest tests/test_hh_transport.py tests/test_hh_auth.py -q`.

### Task 2: Agent Audit Storage

**Files:**

- Modify: `work_hunter/storage.py`
- Modify: `work_hunter/models.py`
- Test: `tests/test_hh_agent_storage.py`

- [x] Add audit tables listed in "Target Storage Additions".
- [x] Add dataclasses and storage methods.
- [x] Add per-resume dedupe lookup.
- [x] Add pending approval state transitions.
- [x] Add operation log append/list methods.
- [x] Test insert/list/update for every new table family.
- [x] Run `pytest tests/test_hh_agent_storage.py tests/test_storage.py -q`.

### Task 3: Policy, DTOs, Dedupe, Prechecks

**Files:**

- Create: `work_hunter/hh_agent/policy.py`
- Create: `work_hunter/hh_agent/types.py`
- Create: `work_hunter/hh_agent/dedupe.py`
- Modify: `work_hunter/hh_agent/research.py`
- Test: `tests/test_hh_agent_policy.py`
- Test: `tests/test_hh_agent_research.py`

- [x] Port `VacancyPolicy` with canonical hash.
- [x] Add DTOs for search, analysis, prechecks, attempt results, cover-letter requests.
- [x] Add dedupe key: resume id + employer id/name + normalized title + normalized description.
- [x] Add prechecks: archived, has test, manual form, already applied, existing relation, skipped, excluded employer, excluded text, excluded keyword, dedupe hit.
- [x] Include Reancoree/Cafedupont filters in policy/prechecks.
- [x] Run `pytest tests/test_hh_agent_policy.py tests/test_hh_agent_research.py -q`.

### Task 4: Structured LLM Client

**Files:**

- Create: `work_hunter/llm/__init__.py`
- Create: `work_hunter/llm/structured.py`
- Modify: `work_hunter/ai_backends.py`
- Test: `tests/test_hh_agent_openrouter.py`

- [x] Port structured schema contract.
- [x] Parse plain JSON and fenced JSON.
- [x] Store model/config metadata.
- [x] Add retry/rate-limit behavior.
- [x] Add invalid JSON typed errors.
- [x] Keep provider-neutral interface.
- [x] Run `pytest tests/test_hh_agent_openrouter.py tests/test_ai_backend.py -q`.

### Task 5: Vacancy Research/Analyze/Apply Planner

**Files:**

- Create: `work_hunter/hh_agent/research.py`
- Modify: `work_hunter/services.py`
- Test: `tests/test_hh_agent_research.py`

- [x] Implement `search_vacancies`.
- [x] Implement `get_similar_vacancies`.
- [x] Implement `get_vacancy_details`.
- [x] Implement `analyze_vacancy` with persisted analysis.
- [x] Implement `plan_apply_vacancy` with prechecks and cover-letter draft.
- [x] Implement `apply_vacancy` through existing `confirm_apply`/`confirm_hh_campaign`.
- [x] Persist every attempt.
- [x] Run `pytest tests/test_hh_agent_research.py tests/test_hh_campaign_outcomes.py -q`.

### Task 6: MCP HH Tools

**Files:**

- Create: `work_hunter/hh_agent/mcp_handlers.py`
- Modify: `work_hunter/mcp_server.py`
- Test: `tests/test_hh_agent_mcp.py`

- [x] Register `hh_whoami`.
- [x] Register `hh_list_resumes`.
- [x] Register `hh_search_vacancies`.
- [x] Register `hh_get_vacancy`.
- [x] Register `hh_analyze_vacancy`.
- [x] Register `hh_research_vacancies`.
- [x] Register `hh_apply_vacancy`.
- [x] Register `hh_research_and_apply`.
- [x] Persist MCP run start/finish/error.
- [x] Keep real apply blocked unless `allow_apply_from_mcp` and `confirm_apply=True`.
- [x] Run `pytest tests/test_hh_agent_mcp.py tests/test_mcp_safety.py -q`.

### Task 7: Approval Queue And Persona

**Files:**

- Create: `work_hunter/hh_agent/approval.py`
- Create: `work_hunter/hh_agent/persona.py`
- Create: `work_hunter/hh_agent/sanity.py`
- Create: `work_hunter/hh_agent/notifications.py`
- Modify: `work_hunter/storage.py`
- Modify: `work_hunter/config.py`
- Test: `tests/test_hh_agent_approval.py`

- [x] Add `PendingMessage`.
- [x] Add `AIDecision`.
- [x] Add `should_escalate`.
- [x] Add approve/reject/modify/flag/sanity transitions.
- [x] Add modify regeneration hook.
- [x] Add persona markdown load/save/generate.
- [x] Inject persona into analysis, cover letters, replies, forms, and resume templates.
- [x] Add notification sink interface.
- [x] Run `pytest tests/test_hh_agent_approval.py tests/test_hh_agent_notifications.py -q`.

### Task 8: Cockpit HTTP API

**Files:**

- Create: `work_hunter/hh_agent/http_api.py`
- Modify: `work_hunter/web/server.py`
- Test: `tests/test_hh_agent_http_api.py`

- [x] Add `GET /api/agent/preflight`.
- [x] Add `POST /api/agent/run`.
- [x] Add `GET /api/agent/digest`.
- [x] Add `GET /api/operations`.
- [x] Add `GET /api/operation-status/{op_id}`.
- [x] Add `POST /api/cancel/{op_id}`.
- [x] Add `GET /api/approvals`.
- [x] Add `POST /api/approvals/{id}/approve`.
- [x] Add `POST /api/approvals/{id}/reject`.
- [x] Add `POST /api/approvals/{id}/modify`.
- [x] Add `POST /api/approvals/{id}/flag`.
- [x] Add template/blacklist endpoints.
- [x] Mask secrets in every response.
- [x] Run `pytest tests/test_hh_agent_http_api.py -q`.

### Task 9: Web UI Cockpit

**Files:**

- Modify: `work_hunter/web/static/index.html`
- Modify: `work_hunter/web/static/app.js`
- Modify: `work_hunter/web/static/app.css`
- Test: manual UI smoke

- [x] Add tabs: Dashboard, Runs, Approvals, Inbox, HH API Lab, Templates, Blacklist, Events, Settings.
- [x] Add Approve/Reject/Modify controls.
- [x] Add operation status/progress log.
- [x] Add digest view.
- [x] Add event/task list.
- [x] Add template and blacklist editors.
- [x] Manual smoke: `work-hunter ui`.

### Task 10: HH API Lab

**Files:**

- Modify: `work_hunter/hh_agent/http_api.py`
- Modify: `work_hunter/web/static/*`
- Test: `tests/test_hh_api_lab.py`

- [x] Add `POST /api/hh/lab/call`.
- [x] Add saved snippets table and endpoints.
- [x] Add quick calls: `/me`, `/resumes/mine`, `/negotiations`, `/vacancies`.
- [x] Add method/path/body validation.
- [x] Add pretty JSON output.
- [x] Ensure secrets are not exposed in responses/logs.
- [x] Run `pytest tests/test_hh_api_lab.py -q`.

### Task 11: Curated Apply From File

**Files:**

- Create: `work_hunter/hh_agent/apply_from_file.py`
- Modify: `work_hunter/cli.py`
- Modify: `work_hunter/storage.py`
- Test: `tests/test_hh_agent_apply_from_file.py`

- [x] Add CSV/TSV loader.
- [x] Parse vacancy id and URL.
- [x] Support `enabled`.
- [x] Support resume id, template, limit, min interval, dry-run.
- [x] Persist processed state.
- [x] Produce row-level report.
- [x] Real apply routes through existing confirmation path.
- [x] Add CLI `hh-apply-from-file`.
- [x] Run `pytest tests/test_hh_agent_apply_from_file.py -q`.

### Task 12: Events, Tasks, Calendar Export

**Files:**

- Create: `work_hunter/hh_agent/events.py`
- Modify: `work_hunter/services.py`
- Modify: `work_hunter/hh_agent/http_api.py`
- Test: `tests/test_hh_agent_events.py`

- [x] Detect interview invitations.
- [x] Detect deadlines/tests.
- [x] Detect follow-ups.
- [x] Detect rejections/offers.
- [x] Persist events/tasks.
- [x] Export ICS.
- [x] Export Markdown agenda.
- [x] Wire into digest and Telegram notifications.
- [x] Run `pytest tests/test_hh_agent_events.py -q`.

### Task 13: Forms And Challenges

**Files:**

- Create: `work_hunter/hh_agent/forms.py`
- Modify: `work_hunter/hh_transport/challenges.py`
- Test: `tests/test_hh_agent_forms.py`

- [x] Detect manual form URL.
- [x] Draft answers from persona/resume/vacancy context.
- [x] Escalate unknown fields to pending approval.
- [x] Add `form_mode = off | manual | agent`.
- [x] Add `challenge_mode = off | manual | ai`.
- [x] Store form review journal.
- [x] Never submit without explicit approval in manual/agent modes.
- [x] Run `pytest tests/test_hh_agent_forms.py -q`.

### Task 14: Chat Agent, Outbox, Webhook

**Files:**

- Create: `work_hunter/hh_agent/chat_service.py`
- Create: `work_hunter/hh_agent/webhook.py`
- Modify: `work_hunter/storage.py`
- Test: `tests/test_hh_agent_chat_service.py`
- Test: `tests/test_hh_agent_webhook.py`

- [x] Port classifier schema.
- [x] Port reply schema.
- [x] Fetch chat history through HH facade.
- [x] Persist decisions and outbox.
- [x] Add delayed send.
- [x] Route risky replies through approval queue.
- [x] Add signed webhook delivery/retry.
- [x] Run `pytest tests/test_hh_agent_chat_service.py tests/test_hh_agent_webhook.py -q`.

### Task 15: Telegram Remote

**Files:**

- Create: `work_hunter/hh_agent/telegram_bot.py`
- Modify: `work_hunter/config.py`
- Test: `tests/test_hh_agent_telegram.py`

- [x] Add allowed user id guard.
- [x] Add `/status`.
- [x] Add `/digest`.
- [x] Add `/pending`.
- [x] Add `/next`.
- [x] Add `/reply`.
- [x] Add `/pause` and `/resume`.
- [x] Add `/events`, `/runs`, `/skipped`.
- [x] Add inline callbacks: approve, reject, modify, flag, sanity.
- [x] Add optional OAuth onboarding using `state`.
- [x] Bot talks to local cockpit API, not directly to HH.
- [x] Run `pytest tests/test_hh_agent_telegram.py -q`.

### Task 16: Resume Templates And Batch Presets

**Files:**

- Create: `work_hunter/hh_agent/resume_templates.py`
- Modify: `work_hunter/resume_payloads.py`
- Modify: `work_hunter/cli.py`
- Modify: `work_hunter/web/static/*`
- Test: `tests/test_hh_agent_resume_templates.py`

- [x] Port roman-redl resume Markdown ideas into existing resume payload system.
- [x] Add persona/profile-aware resume template variables.
- [x] Add draft conversion to HH resume payload.
- [x] Add dry-run preview.
- [x] Add batch preset matrix: resumes, search presets, letters, limits.
- [x] Avoid machine-specific shell scripts as defaults.
- [x] Run `pytest tests/test_hh_agent_resume_templates.py tests/test_hh_resume_payloads.py -q`.

### Task 17: Notification/Scheduler Wrapper

**Files:**

- Modify: `work_hunter/scheduler.py`
- Modify: `work_hunter/hh_agent/notifications.py`
- Test: `tests/test_hh_agent_notifications.py`
- Test: `tests/test_scheduler.py`

- [x] Add success/error/duration/run-id notification events.
- [x] Add Telegram sink.
- [x] Add file/webhook sinks.
- [x] Add command log capture.
- [x] Ensure scheduled jobs default to dry-run/planned unless explicitly configured.
- [x] Run `pytest tests/test_hh_agent_notifications.py tests/test_scheduler.py -q`.

### Task 18: Docker/Auth Compatibility Hardening

**Files:**

- Modify: `Dockerfile` or `docker/*` if present.
- Modify: docs/config as needed.
- Test: smoke only

- [x] Add Docker line-ending hardening where scripts are used.
- [x] Add cached Playwright/Chromium layer where applicable.
- [x] Add auth preflight note for `QT_QUICK_BACKEND=software`.
- [x] Add config examples without secrets.

### Task 19: Final Verification

**Files:**

- Modify: docs as needed.

- [x] Run `pytest -q`.
- [x] Run `work-hunter hh-auth-status`.
- [x] Run dry-run `hh-agent-research` or equivalent fixture-backed flow.
- [x] Run MCP stdio smoke.
- [x] Run web UI smoke at `work-hunter ui`.
- [x] Verify `rg -n "access_token|refresh_token|client_secret|cookie|telegram|smtp" work_hunter docs` does not reveal secret values.
- [x] Update roadmap with completed pieces.

---

## Implementation Log

2026-06-09 final state:

- `pytest -q`: passed, 177 tests.
- `pytest tests/test_hh_agent_notifications.py tests/test_scheduler.py -q`: passed, 10 tests.
- `work-hunter --root .work-hunter-final-smoke hh-auth-status`: passed, reported `missing_access_token` as expected for a secret-free smoke root.
- Dry-run research smoke: `hh_research_and_apply` returned `planned` with zero real applies.
- MCP stdio smoke: initialized via `mcp.client.stdio`, listed 17 tools including `hh_research_and_apply`.
- Web UI smoke: `work-hunter ui` served `/`, `/api/agent/events`, `/api/agent/tasks`; HH Agent Inbox/Events/Settings tabs were present.
- Secret scan: sensitive-key `rg` output contained field names, code references, and empty/masked docs examples only; no real secret values found.
- Docker smoke: Docker CLI exists, but Docker Desktop Linux engine was not running, so `docker build` could not connect to the daemon. Dockerfile syntax/dependency issue found during editable install was fixed via explicit package discovery and Starlette pin.
- Known non-failing local warning: pytest on Windows still emits an atexit cleanup `PermissionError` for `pytest-current`; test exit codes are 0.

---

## Recommended Slices

### Slice 1: Real Agent Core

Tasks: 1-7.

Result: HH transport, audit, policy, LLM analysis, research/apply planning, MCP tools, approval queue.

### Slice 2: Personal Cockpit

Tasks: 8-10.

Result: local control panel with runs, approvals, digest, operation status, API lab.

### Slice 3: Personal Power Tools

Tasks: 11-13.

Result: curated apply from file, events/tasks/calendar, forms/challenges with escalation.

### Slice 4: Remote And Daemon

Tasks: 14-17.

Result: chat/outbox/webhook, Telegram remote, scheduler notifications, batch presets.

### Slice 5: Hardening

Tasks: 18-19.

Result: Docker/auth compatibility, full test pass, no secret leakage.

---

## Acceptance Criteria

- HH transport supports API tokens, refresh, browser cookies, XSRF web actions, and structured challenge outcomes.
- MCP can research/analyze vacancies with persisted reasons.
- MCP cannot real-apply unless config and confirmation both explicitly allow it.
- Every LLM decision stores policy hash, model metadata, confidence, reasons, and raw structured result.
- Every application attempt is persisted and deduped per resume.
- Risky/uncertain apply/reply/form/event actions enter pending approval.
- Approve/Reject/Modify works from UI and Telegram.
- Persona context affects letters, replies, forms, and resume drafts.
- Curated CSV/TSV apply works deterministically with processed state.
- Employer messages create events/tasks and export to ICS/Markdown.
- HH API Lab supports arbitrary local HH calls without leaking secrets.
- Scheduled automation defaults to dry-run/planned and emits success/error summaries.
- Existing `work_hunter` HH commands and tests continue to pass.
