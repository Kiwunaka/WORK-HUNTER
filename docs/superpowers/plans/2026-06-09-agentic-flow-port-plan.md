# Agentic HH Flow Port Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port the useful agentic flow from `0FL01/hh-applicant-tool` into `work-hunter` without replacing our local CRM, multi-source pipeline, UI, or explicit campaign confirmation model.

**Architecture:** Keep `work_hunter` as the product shell and add an HH agent layer around existing campaign primitives. Port the hybrid HH session layer early: API identity, Android-like API session, browser cookies, XSRF extraction, web actions, and challenge outcomes. Reuse the friend's design shape heavily: policy, analysis, mcp runs, application attempts, dedupe, structured LLM outputs, chat classifier, outbox, and webhooks. Use adapters where the friend's code expects `hh_applicant_tool` internals.

**Tech Stack:** Python 3.11+, SQLite, MCP, existing `work_hunter` services/storage/web UI, OpenAI/OpenRouter-compatible chat completions.

---

## Source Map

Friend fork, branch `llm-agent`:

- Full MCP server entrypoint: `C:\Users\kiwun\Документы\work hiring\.compare-hh-applicant-tool\friend\src\hh_applicant_tool\mcp\server.py`
- MCP tool handlers: `C:\Users\kiwun\Документы\work hiring\.compare-hh-applicant-tool\friend\src\hh_applicant_tool\mcp\tools.py`
- MCP runtime/config: `C:\Users\kiwun\Документы\work hiring\.compare-hh-applicant-tool\friend\src\hh_applicant_tool\mcp\context.py`
- MCP input parsing: `C:\Users\kiwun\Документы\work hiring\.compare-hh-applicant-tool\friend\src\hh_applicant_tool\mcp\schemas.py`
- Vacancy research/apply core: `C:\Users\kiwun\Документы\work hiring\.compare-hh-applicant-tool\friend\src\hh_applicant_tool\services\vacancy_research.py`
- Vacancy policy: `C:\Users\kiwun\Документы\work hiring\.compare-hh-applicant-tool\friend\src\hh_applicant_tool\services\policy.py`
- Service DTOs: `C:\Users\kiwun\Документы\work hiring\.compare-hh-applicant-tool\friend\src\hh_applicant_tool\services\types.py`
- Cover letter helper: `C:\Users\kiwun\Документы\work hiring\.compare-hh-applicant-tool\friend\src\hh_applicant_tool\services\cover_letter.py`
- LLM/OpenRouter client: `C:\Users\kiwun\Документы\work hiring\.compare-hh-applicant-tool\friend\hh_llm_agent\openrouter.py`
- Chat agent service: `C:\Users\kiwun\Документы\work hiring\.compare-hh-applicant-tool\friend\hh_llm_agent\service.py`
- Chat agent config: `C:\Users\kiwun\Документы\work hiring\.compare-hh-applicant-tool\friend\hh_llm_agent\config.py`
- Chat timing: `C:\Users\kiwun\Документы\work hiring\.compare-hh-applicant-tool\friend\hh_llm_agent\timing.py`
- Webhook client: `C:\Users\kiwun\Документы\work hiring\.compare-hh-applicant-tool\friend\hh_llm_agent\webhook.py`
- Contact extraction: `C:\Users\kiwun\Документы\work hiring\.compare-hh-applicant-tool\friend\hh_llm_agent\contact_extract.py`
- Telegram/contact collector: `C:\Users\kiwun\Документы\work hiring\.compare-hh-applicant-tool\friend\hh_llm_agent\tg_contact_collector.py`
- Schema additions: `C:\Users\kiwun\Документы\work hiring\.compare-hh-applicant-tool\friend\src\hh_applicant_tool\storage\queries\schema.sql`
- Tests worth mirroring: `C:\Users\kiwun\Документы\work hiring\.compare-hh-applicant-tool\friend\tests`

Additional fork harvest:

- `0FL01/hh-applicant-tool`, branch `llm-agent`: agentic MCP/audit/research/chat spine.
- `SpitiusK/hh-applicant-tool`, branch `sprint/agent-rework-2026-04-17`: human approval loop, pending messages, Telegram Approve/Modify/Reject, persona context, event/task detector, calendar export, form-filler escalation.
- `orelkrylatiy/work-optimization`, branch `main`: personal admin cockpit, agent-friendly HTTP API, token preflight, digest, inbox, letter templates, blacklist.
- `KOVALSKl/hh-applicant-tool`, branch `main`: async HH API client, config/cookie backend abstractions, online smoke isolation, runtime import stabilization.
- `pomogashkin/hh-applicant-tool`, branch `main`: Telegram bot shell, HH OAuth callback server with `state`, user preferences, SQLite token storage.
- `probox36/hh-applicant-tool-tg`, branch `tg_reports`: Telegram report sink and vacancy/run formatters.
- `1MaxSpb/hh-applicant-tool`, branch `copilot/fix-c8e0f31d-422d-430d-803c-2ab88dbbe62b`: browser web-shell, raw API lab, mass-apply progress monitor.
- `Kj9516/hh-applicant-tool`, branch `main`: apply-from-CSV/TSV workflow with `enabled`, dedupe, dry-run, processed-state, and reports.
- `roman-redl/hh-applicant-tool`, branch `my-changes`: resume Markdown template to HH resume API, multi-resume batch scripts, AI vacancy filter, clear skipped DB, email/contact fixes, Docker Chromium cache idea.
- `Eastonn/hh-applicant-tool`, branch `fix/send-email-and-dockerfile`: `send_email` AttributeError fix and Docker build dependency fix.
- `Cafedupont/hh-applicant-tool`, branch `main`: older but useful hygiene ideas: experience/schedule filters, rejected-employer blacklist, safer clear-negotiations.
- `ressiwage/CONT-hh-applicant-tool`, branch `main`: rough resume-info dump and importable API init helpers.
- `MyNameRoman/hh-applicant-tool`, branch `main`: cron/update wrappers with Telegram success/error notifications.
- `ring-rong/hh-applicant-tool`, branch `patch-1`: cron schedule examples only.
- `Reancoree/hh-applicant-tool`, branch `main`: `--excluded-texts` local vacancy text/snippet filter.
- `LazarenkoA/hh-applicant-tool`, branch `patch-1`: Docker build reliability / `dos2unix`.
- `Lexwxd/hh-applicant-tool`, branch `main`: Qt software renderer compatibility for auth on fragile Windows/GUI setups.

Primary GitHub source links for the new high-value forks:

- SpitiusK approval loop: [approval.py](https://github.com/SpitiusK/hh-applicant-tool/blob/sprint/agent-rework-2026-04-17/src/hh_applicant_tool/approval.py), [agent_flow.md](https://github.com/SpitiusK/hh-applicant-tool/blob/sprint/agent-rework-2026-04-17/docs/agent_flow.md), [telegram_client.py](https://github.com/SpitiusK/hh-applicant-tool/blob/sprint/agent-rework-2026-04-17/src/hh_applicant_tool/messaging/telegram_client.py), [modify_handler.py](https://github.com/SpitiusK/hh-applicant-tool/blob/sprint/agent-rework-2026-04-17/src/hh_applicant_tool/messaging/modify_handler.py), [watch_events.py](https://github.com/SpitiusK/hh-applicant-tool/blob/sprint/agent-rework-2026-04-17/src/hh_applicant_tool/operations/watch_events.py), [export_events.py](https://github.com/SpitiusK/hh-applicant-tool/blob/sprint/agent-rework-2026-04-17/src/hh_applicant_tool/operations/export_events.py), [forms/filler.py](https://github.com/SpitiusK/hh-applicant-tool/blob/sprint/agent-rework-2026-04-17/src/hh_applicant_tool/forms/filler.py), [persona.example.md](https://github.com/SpitiusK/hh-applicant-tool/blob/sprint/agent-rework-2026-04-17/config/persona.example.md), [ai/context.py](https://github.com/SpitiusK/hh-applicant-tool/blob/sprint/agent-rework-2026-04-17/src/hh_applicant_tool/ai/context.py).
- 0FL01 agent core: [mcp/tools.py](https://github.com/0FL01/hh-applicant-tool/blob/llm-agent/src/hh_applicant_tool/mcp/tools.py), [vacancy_research.py](https://github.com/0FL01/hh-applicant-tool/blob/llm-agent/src/hh_applicant_tool/services/vacancy_research.py), [hh_llm_agent/service.py](https://github.com/0FL01/hh-applicant-tool/blob/llm-agent/hh_llm_agent/service.py), [schema.sql](https://github.com/0FL01/hh-applicant-tool/blob/llm-agent/src/hh_applicant_tool/storage/queries/schema.sql).
- orelkrylatiy control plane: [admin/app.py](https://github.com/orelkrylatiy/work-optimization/blob/main/admin/app.py), [admin/index.html](https://github.com/orelkrylatiy/work-optimization/blob/main/admin/index.html), [AGENT_GUIDE.md](https://github.com/orelkrylatiy/work-optimization/blob/main/docs/AGENT_GUIDE.md).
- KOVALSKl async/backends: [async_client.py](https://github.com/KOVALSKl/hh-applicant-tool/blob/main/src/hh_applicant_tool/api/async_client.py), [backends.py](https://github.com/KOVALSKl/hh-applicant-tool/blob/main/src/hh_applicant_tool/backends.py).
- Telegram donors: pomogashkin [bot/](https://github.com/pomogashkin/hh-applicant-tool/tree/main/hh_applicant_tool/bot), probox36 [tg_client.py](https://github.com/probox36/hh-applicant-tool-tg/blob/tg_reports/hh_applicant_tool/telegram/tg_client.py), [formatters.py](https://github.com/probox36/hh-applicant-tool-tg/blob/tg_reports/hh_applicant_tool/formatters.py).
- Utility donors: Kj9516 [apply_from_file.py](https://github.com/Kj9516/hh-applicant-tool/blob/main/hh_applicant_tool/operations/apply_from_file.py), roman-redl [create_resume.py](https://github.com/roman-redl/hh-applicant-tool/blob/my-changes/src/hh_applicant_tool/operations/create_resume.py), [resume_md.py](https://github.com/roman-redl/hh-applicant-tool/blob/my-changes/src/hh_applicant_tool/utils/resume_md.py), Eastonn [apply_similar.py](https://github.com/Eastonn/hh-applicant-tool/blob/fix/send-email-and-dockerfile/src/hh_applicant_tool/operations/apply_similar.py), Cafedupont [apply_similar.py](https://github.com/Cafedupont/hh-applicant-tool/blob/main/hh_applicant_tool/operations/apply_similar.py), ressiwage [resume_info.py](https://github.com/ressiwage/CONT-hh-applicant-tool/blob/main/hh_applicant_tool/operations/resume_info.py).

License note: user confirmed the relevant licenses/rights allow this harvest. Preserve attribution when directly copying substantial code, and still keep personal secrets, tokens, cookies, and machine-specific scripts out of committed source.

Our integration points:

- CLI commands: `C:\Users\kiwun\Документы\work hiring\work_hunter\cli.py`
- WorkHunter service core: `C:\Users\kiwun\Документы\work hiring\work_hunter\services.py`
- SQLite storage: `C:\Users\kiwun\Документы\work hiring\work_hunter\storage.py`
- MCP server: `C:\Users\kiwun\Документы\work hiring\work_hunter\mcp_server.py`
- HH adapter/client: `C:\Users\kiwun\Документы\work hiring\work_hunter\sources\hh.py`
- AI backend wrapper: `C:\Users\kiwun\Документы\work hiring\work_hunter\ai_backends.py`
- Config/secrets loading: `C:\Users\kiwun\Документы\work hiring\work_hunter\config.py`
- Web UI API/static surface: `C:\Users\kiwun\Документы\work hiring\work_hunter\web\server.py`, `C:\Users\kiwun\Документы\work hiring\work_hunter\web\static`

---

## What To Take Almost Whole

### 0. Hybrid HH Session Layer

This is the power core. Take the architecture and most of the implementation shape from:

- OAuth/API client identity: `C:\Users\kiwun\Документы\work hiring\.compare-hh-applicant-tool\friend\src\hh_applicant_tool\api\client.py`
- Public client-key constants mechanism: `C:\Users\kiwun\Документы\work hiring\.compare-hh-applicant-tool\friend\src\hh_applicant_tool\api\client_keys.py`
- Android User-Agent generation: `C:\Users\kiwun\Документы\work hiring\.compare-hh-applicant-tool\friend\src\hh_applicant_tool\api\user_agent.py`
- Browser/Playwright auth flow: `C:\Users\kiwun\Документы\work hiring\.compare-hh-applicant-tool\friend\src\hh_applicant_tool\operations\authorize.py`
- Cookie jar restricted to HH domains: `C:\Users\kiwun\Документы\work hiring\.compare-hh-applicant-tool\friend\src\hh_applicant_tool\utils\cookiejar.py`
- XSRF extraction and browser session persistence: `C:\Users\kiwun\Документы\work hiring\.compare-hh-applicant-tool\friend\src\hh_applicant_tool\main.py`
- Web apply actions and challenge handling shape: `C:\Users\kiwun\Документы\work hiring\.compare-hh-applicant-tool\friend\src\hh_applicant_tool\operations\apply_vacancies.py`
- Web negotiation cleanup action shape: `C:\Users\kiwun\Документы\work hiring\.compare-hh-applicant-tool\friend\src\hh_applicant_tool\operations\clear_negotiations.py`

Port as first-class modules:

- Create `work_hunter/hh_transport/identity.py`
- Create `work_hunter/hh_transport/api_session.py`
- Create `work_hunter/hh_transport/browser_session.py`
- Create `work_hunter/hh_transport/web_actions.py`
- Create `work_hunter/hh_transport/challenges.py`
- Create `work_hunter/hh_transport/user_agent.py`
- Create `work_hunter/hh_transport/cookiejar.py`

Target objects:

- `HHIdentity`: access token, refresh token, OAuth client id/secret, expiry, refresh behavior.
- `HHApiSession`: API requests with Android-style headers and token refresh.
- `HHBrowserSession`: Playwright auth, cookies, XSRF extraction, session health.
- `HHWebActions`: web-only actions such as vacancy response popup and negotiation cleanup.
- `HHChallengeHandler`: captcha/test/manual form/question outcomes as structured results.
- `HHHybridClient`: facade used by `WorkHunter`, MCP, and agent services.

Important: keep credential values configurable. The transport should support default public-client constants if present, but every value must be overridable from config/env so we can rotate without code edits.

### 0.5. Async Runtime And Storage Backends

Take from `KOVALSKl/hh-applicant-tool`:

- Config/cookie backend protocols: `C:\Users\kiwun\Документы\work hiring\.compare-forks\KOVALSKl_hh-applicant-tool\src\hh_applicant_tool\backends.py`
- Async API/OAuth client: `C:\Users\kiwun\Документы\work hiring\.compare-forks\KOVALSKl_hh-applicant-tool\src\hh_applicant_tool\api\async_client.py`

Port as:

- `work_hunter/hh_transport/backends.py`
- `work_hunter/hh_transport/async_api_session.py`

Why this matters for the ultimate personal tool:

- web UI, Telegram bot, daemon, MCP, and scheduler should not block each other on sync requests;
- config/cookie backends let us run the same HH identity from files, SQLite, future encrypted storage, or Telegram-user storage.

### 1. MCP Tool Shape

Take the tool taxonomy and run accounting from:

- `mcp/tools.py`: `MCPToolHandlers` around lines 27-446.
- Tools to mirror: `hh_whoami`, `hh_list_resumes`, `hh_search_vacancies`, `hh_get_vacancy`, `hh_analyze_vacancy`, `hh_research_vacancies`, `hh_apply_vacancy`, `hh_research_and_apply`.
- Take `_start_run`, `_finish_run`, `_analysis_summary`, `_attempt_summary`, and the wrapper error pattern.

Do not replace our current `work_hunter/mcp_server.py` all at once. Add new HH-specific tools beside existing general tools. Our current MCP starts at `work_hunter/mcp_server.py:15` and tool registration starts at `work_hunter/mcp_server.py:22`.

Target names:

- `hh_search_vacancies`
- `hh_get_vacancy`
- `hh_analyze_vacancy`
- `hh_research_vacancies`
- `hh_apply_vacancy`
- `hh_research_and_apply`
- keep existing `search_jobs`, `list_jobs`, `prepare_apply_plan`, `daily_report`

### 2. Policy Object

Take the `VacancyPolicy` dataclass almost directly from:

- `services/policy.py`

Port it as:

- Create `work_hunter/hh_agent/policy.py`

Keep fields:

- `must_have`
- `nice_to_have`
- `avoid`
- `dealbreakers`
- `excluded_employers`
- `excluded_keywords`
- `min_score`
- `cover_letter_style`
- `cover_letter_language`
- `force_message`
- `notes`
- `skip_blacklisted_employers`

This is clean, useful, and maps well to our campaign presets.

### 3. Structured LLM Client Pattern

Take the design of:

- `hh_llm_agent/openrouter.py`
- especially `StructuredOutputSchema`, `OpenRouterChatClient`, structured response parsing, retries, and error classes.

Port as:

- Create `work_hunter/llm/structured.py`
- Reuse our config naming and `work_hunter/ai_backends.py` where possible.

Keep OpenAI/OpenRouter compatibility. Do not hard-code one provider into the product layer.

### 4. Analysis/Audit Tables

Take the table model almost whole from schema lines:

- `mcp_runs`
- `vacancy_analysis`
- `application_attempts`
- `vacancy_response_dedup`

Friend source:

- `storage/queries/schema.sql`, lines 60, 180, 204, 232, and indexes around 276-295.

Port into `work_hunter/storage.py` near existing HH tables:

- existing `hh_skipped_vacancies`: `work_hunter/storage.py:254`
- existing `hh_campaign_runs`: `work_hunter/storage.py:266`
- existing `hh_campaign_items`: `work_hunter/storage.py:275`

Use `hh_` prefixes to match our DB style:

- `hh_agent_mcp_runs`
- `hh_vacancy_analysis`
- `hh_application_attempts`
- `hh_vacancy_response_dedup`

### 5. Dedupe Key Idea

Take the idea from:

- `VacancyResearchService.build_vacancy_dedupe_key`, `services/vacancy_research.py:179`

Port into:

- Create `work_hunter/hh_agent/dedupe.py`

Use employer id/name + normalized title + normalized description. Store per resume, because the same vacancy may be valid for one resume and duplicate for another.

### 6. Chat Agent State Machine

Take the state machine structure from:

- `hh_llm_agent/service.py`
- `CLASSIFIER_RESPONSE_SCHEMA`: line 37
- `REPLY_RESPONSE_SCHEMA`: line 68
- `ChatAgentService`: line 118
- `run`: line 186
- `_save_run`: line 1225
- `_save_decision`: line 1446

Port as:

- Create `work_hunter/hh_agent/chat_service.py`
- Create `work_hunter/hh_agent/chat_config.py`
- Create `work_hunter/hh_agent/timing.py`
- Create `work_hunter/hh_agent/webhook.py`

Keep the loop, classifier, reply generator, outbox, timing, dry-run, and webhook concepts.

### 7. Personal Admin Cockpit API

Take the product shape from `orelkrylatiy/work-optimization`:

- FastAPI admin backend: `C:\Users\kiwun\Документы\work hiring\.compare-forks\orelkrylatiy_work-optimization\admin\app.py`
- Admin UI: `C:\Users\kiwun\Документы\work hiring\.compare-forks\orelkrylatiy_work-optimization\admin\index.html`
- Agent API guide: `C:\Users\kiwun\Документы\work hiring\.compare-forks\orelkrylatiy_work-optimization\docs\AGENT_GUIDE.md`

Port concepts:

- `/api/token-status`
- `/api/agent/preflight`
- `/api/agent/run`
- `/api/agent/digest`
- `/api/inbox`
- `/api/inbox/{id}/messages`
- `/api/inbox/{id}/reply`
- `/api/inbox/clear-rejections`
- `/api/letter-templates`
- `/api/employers/blacklist`

This is the "personal command bridge": an LLM or the browser UI can operate the tool through stable HTTP endpoints instead of shelling out blindly.

### 8. Raw API Lab / Web Shell

Take from `1MaxSpb/hh-applicant-tool` branch `copilot/fix-c8e0f31d-422d-430d-803c-2ab88dbbe62b`:

- Raw web shell: `C:\Users\kiwun\Документы\work hiring\.compare-forks\1MaxSpb_hh-applicant-tool\hh_applicant_tool\operations\web_shell.py`
- Docs: `C:\Users\kiwun\Документы\work hiring\.compare-forks\1MaxSpb_hh-applicant-tool\WEB_SHELL.md`
- Mass apply guide: `C:\Users\kiwun\Документы\work hiring\.compare-forks\1MaxSpb_hh-applicant-tool\MASS_APPLY_GUIDE.md`

Port as a local "HH API Lab" tab:

- quick calls: `/me`, `/resumes/mine`, `/negotiations`, `/vacancies`;
- arbitrary method/path/JSON params;
- pretty JSON output;
- save useful calls as reusable snippets;
- convert a successful raw call into a typed tool later.

### 9. Human Approval, Persona, Events, And Forms

Take the product spine from `SpitiusK/hh-applicant-tool` branch `sprint/agent-rework-2026-04-17`:

- Human approval loop: [approval.py](https://github.com/SpitiusK/hh-applicant-tool/blob/sprint/agent-rework-2026-04-17/src/hh_applicant_tool/approval.py)
- Agent flow docs: [docs/agent_flow.md](https://github.com/SpitiusK/hh-applicant-tool/blob/sprint/agent-rework-2026-04-17/docs/agent_flow.md)
- Telegram delivery: [messaging/telegram_client.py](https://github.com/SpitiusK/hh-applicant-tool/blob/sprint/agent-rework-2026-04-17/src/hh_applicant_tool/messaging/telegram_client.py)
- Modify iteration handler: [messaging/modify_handler.py](https://github.com/SpitiusK/hh-applicant-tool/blob/sprint/agent-rework-2026-04-17/src/hh_applicant_tool/messaging/modify_handler.py)
- Sanity sampling: [messaging/sanity.py](https://github.com/SpitiusK/hh-applicant-tool/blob/sprint/agent-rework-2026-04-17/src/hh_applicant_tool/messaging/sanity.py)
- Persona context: [config/persona.example.md](https://github.com/SpitiusK/hh-applicant-tool/blob/sprint/agent-rework-2026-04-17/config/persona.example.md), [ai/context.py](https://github.com/SpitiusK/hh-applicant-tool/blob/sprint/agent-rework-2026-04-17/src/hh_applicant_tool/ai/context.py)
- Event watcher: [operations/watch_events.py](https://github.com/SpitiusK/hh-applicant-tool/blob/sprint/agent-rework-2026-04-17/src/hh_applicant_tool/operations/watch_events.py)
- Calendar export: [operations/export_events.py](https://github.com/SpitiusK/hh-applicant-tool/blob/sprint/agent-rework-2026-04-17/src/hh_applicant_tool/operations/export_events.py)
- Form filling and escalation: [forms/filler.py](https://github.com/SpitiusK/hh-applicant-tool/blob/sprint/agent-rework-2026-04-17/src/hh_applicant_tool/forms/filler.py)

Port as first-class `work_hunter` modules:

- `work_hunter/hh_agent/approval.py`
- `work_hunter/hh_agent/persona.py`
- `work_hunter/hh_agent/events.py`
- `work_hunter/hh_agent/forms.py`
- `work_hunter/hh_agent/sanity.py`
- `work_hunter/hh_agent/notifications.py`

Target behavior:

- The agent can apply, reply, classify, fill, and schedule until confidence or policy says "ask the human".
- Every uncertain or risky action becomes a pending message with structured payload, not a console prompt.
- Telegram buttons support Approve, Reject, Modify, Flag, and Sanity Check.
- Modify is a real iteration loop: user edits intent, agent regenerates, stores the new decision, and asks again.
- Persona markdown becomes long-lived context for cover letters, replies, form filling, and resume generation.
- Incoming employer messages are classified into events/tasks/deadlines and exported to ICS/Markdown.

### 10. Curated Apply From File

Take the workflow from `Kj9516/hh-applicant-tool`:

- [apply_from_file.py](https://github.com/Kj9516/hh-applicant-tool/blob/main/hh_applicant_tool/operations/apply_from_file.py)
- [applications.py](https://github.com/Kj9516/hh-applicant-tool/blob/main/hh_applicant_tool/applications.py)

Port as:

- `work_hunter/hh_agent/apply_from_file.py`

Target behavior:

- Load CSV/TSV with vacancy id or URL.
- Respect an `enabled` column.
- Deduplicate against local attempts and HH relations.
- Support dry-run, limit, resume id, cover letter strategy, and report output.
- Maintain a processed-state file or DB table so manual curated lists are deterministic.

---

## What To Take Partially

### 0. Challenge Automation

Source:

- Friend captcha/auth shape: `operations/authorize.py`
- Upstream captcha solver shape: `C:\Users\kiwun\Документы\work hiring\external\hh-applicant-tool\src\hh_applicant_tool\operations\apply_vacancies.py`
- Upstream vision call: `C:\Users\kiwun\Документы\work hiring\external\hh-applicant-tool\src\hh_applicant_tool\ai\openai.py`

Take:

- structured detection of `captcha_required`
- Playwright page flow to open challenge URL
- screenshot extraction
- provider-neutral vision call interface
- cookie sync back into session

Adapt:

- keep it behind `hh_agent.challenge_mode = "manual" | "ai" | "off"`
- store every challenge attempt in audit
- return `blocked: captcha_required` when disabled or failed

### 1. VacancyResearchService

Source:

- `services/vacancy_research.py`
- main class at line 47
- `search_vacancies`: line 105
- `get_similar_vacancies`: line 121
- `run_hard_prechecks`: line 188
- `analyze_vacancy`: line 296
- `apply_vacancy`: line 455

Take the method boundaries and DTO flow. Do not copy its dependency assumptions literally, because it expects `HHProfileContext` and original storage facades.

Port as:

- Create `work_hunter/hh_agent/research.py`

Map dependencies:

- Friend `context.api_client` -> our `HHApplyClient` / `HHApplicantToolAdapter` in `work_hunter/sources/hh.py`
- Friend `storage.vacancies` -> our `Storage.upsert_job` and HH-specific tables
- Friend `application_attempts` -> new `Storage.save_hh_application_attempt`
- Friend `vacancy_analysis` -> new `Storage.save_hh_vacancy_analysis`

Keep these behaviors:

- direct search and similar vacancy search
- full vacancy fetch before analysis
- hard prechecks before LLM
- persisted LLM analysis
- persisted application attempts
- per-run summaries

Adapt these behaviors:

- output should use our `Job`, `HHCampaignRun`, `HHCampaignItem`
- apply should integrate with existing `confirm_apply` at `work_hunter/services.py:660`
- campaign planning should integrate with `plan_hh_search_campaign` at `work_hunter/services.py:1153` and `plan_hh_campaign` at `work_hunter/services.py:1239`

### 2. Apply Safety Gate

Source:

- `mcp/tools.py:187` and `mcp/tools.py:235`

Take:

- explicit `dry_run`
- explicit `confirm_apply`
- max applications per run/day
- blocked/planned/applied result statuses

Adapt:

- keep our existing CLI confirm path instead of allowing hidden real apply through MCP.
- allow real MCP apply only if config explicitly enables it and the tool call passes `confirm_apply=True`.

### 3. Agent Config

Source:

- `hh_llm_agent/config.py`

Take:

- dataclass structure
- env/config precedence
- classifier config
- webhook config
- timing config

Adapt:

- use our `work_hunter/config.py` profile config.
- add config under:

```json
{
  "hh_agent": {
    "enabled": true,
    "allow_apply_from_mcp": false,
    "max_applications_per_day": 20,
    "llm": {},
    "classifier": {},
    "webhook": {},
    "timing": {}
  }
}
```

### 4. Contact Collector

Source:

- `hh_llm_agent/contact_extract.py`
- `hh_llm_agent/tg_contact_collector.py`
- `hh_llm_agent/tg_collector_store.py`

Take:

- contact extraction parsing
- webhook event shape
- persistence pattern

Adapt:

- first implementation should store contacts into our existing `hh_contacts` table at `work_hunter/storage.py:219`.
- Telegram delivery can be second pass; first pass should expose contacts in web UI and daily report.

### 5. Webhook Client

Source:

- `hh_llm_agent/webhook.py`

Take:

- signed delivery shape
- timeout/retry concepts
- pending webhook table

Adapt:

- route through our `Storage` and config.
- use it for chat-agent events and contact-offer events.

### 6. Tests

Source tests to mirror:

- `tests/test_mcp_stdio.py`
- `tests/test_mcp_tools.py`
- `tests/test_vacancy_research_service.py`
- `tests/test_vacancy_audit_storage.py`
- `tests/test_chat_agent_service.py`
- `tests/test_openrouter_client.py`
- `tests/test_tg_contact_collector.py`
- `tests/test_webhook_client.py`

Port test intent, not exact fixtures. Our existing tests already cover campaign outcomes around `tests/test_hh_campaign_outcomes.py`.

### 7. Telegram Control Surface

Source:

- `C:\Users\kiwun\Документы\work hiring\.compare-forks\pomogashkin_hh-applicant-tool\hh_applicant_tool\bot`

Take:

- aiogram router/middleware shape;
- OAuth callback server with `state`;
- per-user token storage;
- browse/next vacancy flow;
- notification/menu idea.

Adapt:

- make it a personal remote control for our local daemon, not a separate product;
- replace hardcoded role defaults with our saved searches/profiles;
- use our `WorkHunter` and `HHHybridClient` instead of the fork's minimal async wrapper.

### 8. Telegram Reports And Notification Sink

Source:

- probox36 [tg_client.py](https://github.com/probox36/hh-applicant-tool-tg/blob/tg_reports/hh_applicant_tool/telegram/tg_client.py)
- probox36 [formatters.py](https://github.com/probox36/hh-applicant-tool-tg/blob/tg_reports/hh_applicant_tool/formatters.py)
- probox36 [apply_similar.py](https://github.com/probox36/hh-applicant-tool-tg/blob/tg_reports/hh_applicant_tool/operations/apply_similar.py)
- MyNameRoman cron/update wrappers in `+ features/`

Take:

- simple HTML Telegram message sender;
- vacancy summary formatter;
- run summary counts;
- success/error notification wrapper around scheduled commands.

Adapt:

- implement as a generic notification sink, not as inline Telegram calls inside apply code;
- emit from operation events and audit tables;
- support Telegram first, then webhook/file sinks later.

### 9. Resume Markdown, Batch Runs, And Email Fixes

Source:

- roman-redl [create_resume.py](https://github.com/roman-redl/hh-applicant-tool/blob/my-changes/src/hh_applicant_tool/operations/create_resume.py)
- roman-redl [resume_md.py](https://github.com/roman-redl/hh-applicant-tool/blob/my-changes/src/hh_applicant_tool/utils/resume_md.py)
- roman-redl [clear_skipped.py](https://github.com/roman-redl/hh-applicant-tool/blob/my-changes/src/hh_applicant_tool/operations/clear_skipped.py)
- Eastonn [apply_similar.py](https://github.com/Eastonn/hh-applicant-tool/blob/fix/send-email-and-dockerfile/src/hh_applicant_tool/operations/apply_similar.py)
- roman-redl [Dockerfile cache fix](https://github.com/roman-redl/hh-applicant-tool/blob/feature/dockerfile-fix/Dockerfile)

Take:

- markdown-to-HH-resume template idea;
- multi-resume/multi-search batch orchestration shape;
- `--skip-tests` style switch where challenge behavior should be explicit;
- email/contact retrieval and `send_email` bug fixes;
- cached Playwright/Chromium Docker layer idea.

Adapt:

- keep resume templates under our persona/profile model;
- store batch matrices in config/UI, not personal shell scripts;
- keep email/contact fixes as tests against our HH adapter;
- keep VLM captcha solving behind `challenge_mode = "ai"` and audit it.

### 10. Filters, Hygiene, And Compatibility Patches

Small but useful:

- `Reancoree`: `--excluded-texts` over title + snippet.
- `Cafedupont`: `--experience`, `--schedule`, rejected-employer blacklist, safer clear-negotiations.
- `ressiwage`: quick resume-info dump.
- `Lexwxd`: `QT_QUICK_BACKEND=software` before Qt auth.
- `LazarenkoA`: Docker line-ending normalization with `dos2unix`.
- `ring-rong`: cron timing examples only.

Port these into:

- search/campaign filters;
- employer blacklist and reply filters;
- debug/export commands;
- auth environment setup;
- Dockerfile build hardening.

---

## What Not To Pull In As A Product Default

This is not a morality section; it is about keeping the code shippable and maintainable.

### 1. `0FL01/main` As A Base

Do not base us on `0FL01/main`.

Reason: it is old relative to upstream and deletes useful upstream UI/create-resume/clear-skipped code. Its useful changes are already superseded by `llm-agent` or by our own project.

### 2. Full Original Package Layout

Do not import `hh_applicant_tool` as a dependency layer inside `work_hunter`.

Reason: it would create two competing app cores: original operation modules and our `WorkHunter` service. Use small adapters and copy/port the agent modules into our namespace instead.

### 3. Hard-Coded Provider Defaults

Do not bake in the friend's exact OpenRouter models/prompts as immutable defaults.

Reason: models change, cost changes, and we already have config surfaces. Keep their structure; make model/prompt configurable.

### 4. Whole SpitiusK Sprint As A Merge

Do not merge the full SpitiusK sprint branch directly.

Reason: its approval/persona/event flow is excellent, but its config, storage migrations, Claude-specific assumptions, and operation wiring need to become `work_hunter` subsystems. Port the approval queue, modify loop, persona context, sanity sampling, events, and forms; adapt the surrounding package layout.

### 5. Whole Telegram Bot Package Layouts

Do not wholesale import pomogashkin/probox36 Telegram code as another parallel app.

Reason: they are useful as bot/reporting donors, but the final bot should call our local daemon HTTP API and audit tables. One control plane, many surfaces.

### 6. Personal Shell Scripts As Defaults

Do not copy roman-redl/MyNameRoman personal batch scripts into default commands.

Reason: keep the workflows, not machine-specific paths, logs, schedules, or one-off assumptions. Encode batches as profiles/config/UI presets.

### 7. Destructive Automation Defaults

Do not make scheduled real apply the default behavior.

Reason: it will make debugging and trust miserable. Keep the flow powerful, but default to `planned` and `dry_run` unless a campaign/run is explicitly approved.

### 8. README/Tone/Docs Copy

Do not copy README content or rant-style text into our docs.

Reason: it makes the project harder to maintain, harder to share, and unrelated to agent capability.

---

## Target File Structure

Create:

- `work_hunter/hh_transport/__init__.py`
- `work_hunter/hh_transport/identity.py`
- `work_hunter/hh_transport/api_session.py`
- `work_hunter/hh_transport/browser_session.py`
- `work_hunter/hh_transport/web_actions.py`
- `work_hunter/hh_transport/challenges.py`
- `work_hunter/hh_transport/user_agent.py`
- `work_hunter/hh_transport/cookiejar.py`
- `work_hunter/hh_transport/backends.py`
- `work_hunter/hh_transport/async_api_session.py`
- `work_hunter/hh_agent/__init__.py`
- `work_hunter/hh_agent/policy.py`
- `work_hunter/hh_agent/types.py`
- `work_hunter/hh_agent/dedupe.py`
- `work_hunter/hh_agent/research.py`
- `work_hunter/hh_agent/mcp_handlers.py`
- `work_hunter/hh_agent/approval.py`
- `work_hunter/hh_agent/persona.py`
- `work_hunter/hh_agent/events.py`
- `work_hunter/hh_agent/forms.py`
- `work_hunter/hh_agent/sanity.py`
- `work_hunter/hh_agent/notifications.py`
- `work_hunter/hh_agent/apply_from_file.py`
- `work_hunter/hh_agent/resume_templates.py`
- `work_hunter/hh_agent/chat_config.py`
- `work_hunter/hh_agent/chat_service.py`
- `work_hunter/hh_agent/timing.py`
- `work_hunter/hh_agent/webhook.py`
- `work_hunter/hh_agent/http_api.py`
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
- `work_hunter/web/static/app.js`
- `work_hunter/web/static/index.html`
- `work_hunter/web/static/app.css`

Tests:

- `tests/test_hh_agent_policy.py`
- `tests/test_hh_agent_storage.py`
- `tests/test_hh_agent_research.py`
- `tests/test_hh_agent_mcp.py`
- `tests/test_hh_agent_approval.py`
- `tests/test_hh_agent_events.py`
- `tests/test_hh_agent_apply_from_file.py`
- `tests/test_hh_agent_resume_templates.py`
- `tests/test_hh_agent_notifications.py`
- `tests/test_hh_agent_openrouter.py`
- `tests/test_hh_agent_chat_service.py`
- `tests/test_hh_agent_webhook.py`
- `tests/test_hh_agent_contacts.py`

---

## Build Order

### Task 0: Port Hybrid HH Transport

**Files:**

- Create: `work_hunter/hh_transport/__init__.py`
- Create: `work_hunter/hh_transport/identity.py`
- Create: `work_hunter/hh_transport/api_session.py`
- Create: `work_hunter/hh_transport/browser_session.py`
- Create: `work_hunter/hh_transport/web_actions.py`
- Create: `work_hunter/hh_transport/challenges.py`
- Create: `work_hunter/hh_transport/user_agent.py`
- Create: `work_hunter/hh_transport/cookiejar.py`
- Modify: `work_hunter/config.py`
- Modify: `work_hunter/sources/hh.py`
- Test: `tests/test_hh_transport.py`

- [ ] Port HH-only cookie jar.
- [ ] Port Android User-Agent generator.
- [ ] Add `HHIdentity` with access/refresh token lifecycle and configurable OAuth client values.
- [ ] Add `HHApiSession` with token refresh and Android-like headers.
- [ ] Add `HHBrowserSession` with cookie file load/save and XSRF extraction.
- [ ] Add Playwright authorization flow that syncs cookies and tokens back to config.
- [ ] Add `HHWebActions` for web-only endpoints used by apply/cleanup.
- [ ] Add `HHChallengeHandler` returning structured outcomes: `captcha_required`, `test_required`, `manual_form_required`, `solved`, `failed`.
- [ ] Test user-agent shape.
- [ ] Test cookie jar rejects non-HH domains.
- [ ] Test token refresh updates persisted token data.
- [ ] Test XSRF extraction from fixture HTML.
- [ ] Test web action builds required XSRF headers without network calls.
- [ ] Run: `pytest tests/test_hh_transport.py -v`

### Task 0.5: Add Async Client And Backend Interfaces

**Files:**

- Create: `work_hunter/hh_transport/backends.py`
- Create: `work_hunter/hh_transport/async_api_session.py`
- Modify: `work_hunter/hh_transport/identity.py`
- Test: `tests/test_hh_transport_async.py`

- [ ] Add `ConfigBackend` and `CookieBackend` protocols.
- [ ] Add file-backed config/cookie backend implementations.
- [ ] Add async API/OAuth client with cooperative rate limiting.
- [ ] Add retry/backoff for timeout, network errors, 429, and 5xx.
- [ ] Add async token refresh and `aclose`.
- [ ] Test concurrent calls reserve separate rate-limit slots.
- [ ] Test refresh updates in-memory identity and backend.
- [ ] Run: `pytest tests/test_hh_transport_async.py -v`

### Task 1: Port Policy And DTOs

**Files:**

- Create: `work_hunter/hh_agent/policy.py`
- Create: `work_hunter/hh_agent/types.py`
- Test: `tests/test_hh_agent_policy.py`

- [ ] Add `VacancyPolicy` with `from_mapping`, `to_canonical_dict`, and `hash`.
- [ ] Add DTOs for `SearchFilters`, `VacancySearchResult`, `VacancyAnalysisResult`, `ApplicationAttemptResult`, `CoverLetterRequest`, `CoverLetterResult`, `PrecheckResult`.
- [ ] Test that policy hash is stable regardless of dict insertion order.
- [ ] Test that unknown config keys are ignored.
- [ ] Run: `pytest tests/test_hh_agent_policy.py -v`

### Task 2: Add Audit Storage

**Files:**

- Modify: `work_hunter/storage.py`
- Modify: `work_hunter/models.py`
- Test: `tests/test_hh_agent_storage.py`

- [ ] Add tables: `hh_agent_mcp_runs`, `hh_vacancy_analysis`, `hh_application_attempts`, `hh_vacancy_response_dedup`, `hh_agent_runs`, `hh_agent_decisions`, `hh_agent_outbox`, `hh_agent_webhooks`, `hh_pending_messages`, `hh_ai_decisions`, `hh_personas`, `hh_agent_events`, `hh_agent_tasks`, `hh_apply_from_file_state`, `hh_notification_events`.
- [ ] Add model dataclasses matching table columns.
- [ ] Add `save/list/get` methods for each table.
- [ ] Test insert/list for each table.
- [ ] Test dedupe lookup by `(resume_id, dedupe_key)`.
- [ ] Run: `pytest tests/test_hh_agent_storage.py -v`

### Task 3: Port Structured LLM Client

**Files:**

- Create: `work_hunter/llm/structured.py`
- Modify: `work_hunter/ai_backends.py`
- Test: `tests/test_hh_agent_openrouter.py`

- [ ] Port `StructuredOutputSchema`, client error types, response parsing, retry handling, and rate-limit handling.
- [ ] Expose a provider-neutral `send_structured_chat(...)`.
- [ ] Test valid structured JSON response.
- [ ] Test invalid JSON returns a useful typed error.
- [ ] Test retry-after parsing.
- [ ] Run: `pytest tests/test_hh_agent_openrouter.py -v`

### Task 4: Port Dedupe And Prechecks

**Files:**

- Create: `work_hunter/hh_agent/dedupe.py`
- Create/modify: `work_hunter/hh_agent/research.py`
- Test: `tests/test_hh_agent_research.py`

- [ ] Port vacancy dedupe key generation.
- [ ] Add hard prechecks: archived, manual form/redirect, has test, already relations, excluded employer, excluded keywords, existing local application, existing skipped vacancy, existing dedupe key.
- [ ] Integrate with `Storage.list_hh_skipped_vacancies` and existing campaign status.
- [ ] Test every hard precheck reason.
- [ ] Run: `pytest tests/test_hh_agent_research.py -v`

### Task 5: Port Vacancy Research Service

**Files:**

- Modify: `work_hunter/hh_agent/research.py`
- Modify: `work_hunter/services.py`
- Test: `tests/test_hh_agent_research.py`

- [ ] Add `search_vacancies` using our HH client.
- [ ] Add `get_similar_vacancies` using resume id.
- [ ] Add `get_vacancy_details`.
- [ ] Add `analyze_vacancy` using structured LLM output.
- [ ] Persist analysis to `hh_vacancy_analysis`.
- [ ] Add `apply_vacancy` that calls existing `WorkHunter.confirm_apply` for real apply and stores `hh_application_attempts`.
- [ ] Run: `pytest tests/test_hh_agent_research.py tests/test_hh_campaign_outcomes.py -v`

### Task 6: Upgrade MCP

**Files:**

- Create: `work_hunter/hh_agent/mcp_handlers.py`
- Modify: `work_hunter/mcp_server.py`
- Test: `tests/test_hh_agent_mcp.py`

- [ ] Add handler class modeled after friend's `MCPToolHandlers`.
- [ ] Register HH-specific tools beside current MCP tools.
- [ ] Add run persistence for every HH MCP call.
- [ ] Keep real apply behind `allow_apply_from_mcp` and `confirm_apply`.
- [ ] Test dry-run apply returns planned/blocked without calling real HH apply.
- [ ] Test `research_and_apply` aggregates summary counts.
- [ ] Run: `pytest tests/test_hh_agent_mcp.py -v`

### Task 6.5: Add Approval Queue, Persona Context, Events, And Forms

**Files:**

- Create: `work_hunter/hh_agent/approval.py`
- Create: `work_hunter/hh_agent/persona.py`
- Create: `work_hunter/hh_agent/events.py`
- Create: `work_hunter/hh_agent/forms.py`
- Create: `work_hunter/hh_agent/sanity.py`
- Create: `work_hunter/hh_agent/notifications.py`
- Modify: `work_hunter/storage.py`
- Modify: `work_hunter/config.py`
- Test: `tests/test_hh_agent_approval.py`
- Test: `tests/test_hh_agent_events.py`
- Test: `tests/test_hh_agent_notifications.py`

- [ ] Add `PendingMessage` model with action type, payload, confidence, status, and audit references.
- [ ] Add approve/reject/modify handlers that update pending state and emit decision events.
- [ ] Add persona markdown loader/generator and structured persona context for LLM calls.
- [ ] Add sanity sampling policy for low-confidence or randomly sampled actions.
- [ ] Add form-filler service that answers known questions and escalates unknown form fields to pending messages.
- [ ] Add event/task detector for employer messages: interview, deadline, test, follow-up, rejection, offer.
- [ ] Add ICS and Markdown export for detected events/tasks.
- [ ] Add notification sink abstraction for Telegram/webhook/file delivery.
- [ ] Test approve, reject, and modify state transitions.
- [ ] Test persona context is injected into analysis/cover-letter/reply calls.
- [ ] Test unknown form fields create pending messages.
- [ ] Test event extraction writes events and exports valid ICS.
- [ ] Run: `pytest tests/test_hh_agent_approval.py tests/test_hh_agent_events.py tests/test_hh_agent_notifications.py -v`

### Task 7: Add CLI Surface

**Files:**

- Modify: `work_hunter/cli.py`
- Test: `tests/test_hh_agent_cli.py`

- [ ] Add `hh-agent-research`.
- [ ] Add `hh-agent-apply`.
- [ ] Add `hh-agent-chat`.
- [ ] Add `hh-agent-outbox`.
- [ ] Keep output JSON-first.
- [ ] Run: `pytest tests/test_hh_agent_cli.py -v`

### Task 7.5: Add Curated Apply From File And Resume Templates

**Files:**

- Create: `work_hunter/hh_agent/apply_from_file.py`
- Create: `work_hunter/hh_agent/resume_templates.py`
- Modify: `work_hunter/cli.py`
- Modify: `work_hunter/storage.py`
- Test: `tests/test_hh_agent_apply_from_file.py`
- Test: `tests/test_hh_agent_resume_templates.py`

- [ ] Add CSV/TSV parser that accepts vacancy id, vacancy URL, resume id, cover-letter template, and `enabled`.
- [ ] Add dry-run, limit, processed-state, report output, and dedupe against attempts/relations.
- [ ] Add CLI command `hh-apply-from-file`.
- [ ] Add markdown resume parser/generator for HH resume payload drafts.
- [ ] Add CLI command `hh-resume-from-md` in draft/dry-run mode first.
- [ ] Test vacancy URL/id parsing.
- [ ] Test disabled rows are skipped.
- [ ] Test processed-state prevents duplicate applies.
- [ ] Test markdown resume conversion on fixtures.
- [ ] Run: `pytest tests/test_hh_agent_apply_from_file.py tests/test_hh_agent_resume_templates.py -v`

### Task 8: Port Chat Agent

**Files:**

- Create: `work_hunter/hh_agent/chat_config.py`
- Create: `work_hunter/hh_agent/chat_service.py`
- Create: `work_hunter/hh_agent/timing.py`
- Test: `tests/test_hh_agent_chat_service.py`

- [ ] Port classifier schema.
- [ ] Port reply schema.
- [ ] Add chat history fetch through our HH client/adapter.
- [ ] Add dry-run decision persistence.
- [ ] Add outbox persistence.
- [ ] Add delayed send behavior.
- [ ] Add daemon loop as an explicit CLI mode, not default.
- [ ] Run: `pytest tests/test_hh_agent_chat_service.py -v`

### Task 9: Port Webhook And Contact Extraction

**Files:**

- Create: `work_hunter/hh_agent/webhook.py`
- Create: `work_hunter/hh_agent/contact_extract.py`
- Test: `tests/test_hh_agent_webhook.py`
- Test: `tests/test_hh_agent_contacts.py`

- [ ] Port contact extraction.
- [ ] Persist extracted contacts into `hh_contacts`.
- [ ] Port webhook signing/retry/pending storage.
- [ ] Add event types: `agent_decision`, `agent_reply_planned`, `agent_reply_sent`, `recruiter_contact_offer`.
- [ ] Run: `pytest tests/test_hh_agent_webhook.py tests/test_hh_agent_contacts.py -v`

### Task 10: Add Web UI Views

**Files:**

- Modify: `work_hunter/web/server.py`
- Modify: `work_hunter/web/static/app.js`
- Modify: `work_hunter/web/static/index.html`
- Modify: `work_hunter/web/static/app.css`

- [ ] Add API endpoints for analysis, attempts, mcp runs, agent decisions, outbox, webhooks.
- [ ] Add UI tabs or panels for agent runs and campaign audit.
- [ ] Add approve/send controls only for pending/planned items.
- [ ] Add filters by resume, run, status, employer, recommended action.
- [ ] Manual verify at `work-hunter ui`.

### Task 10.5: Add Personal Agent HTTP API

**Files:**

- Create: `work_hunter/hh_agent/http_api.py`
- Modify: `work_hunter/web/server.py`
- Test: `tests/test_hh_agent_http_api.py`

- [ ] Add token status endpoint.
- [ ] Add agent preflight endpoint.
- [ ] Add agent digest endpoint.
- [ ] Add agent run endpoint for `sync`, `update-resumes`, `research`, `apply`, `reply-employers`, and `refresh-token`.
- [ ] Add inbox endpoints.
- [ ] Add letter-template CRUD endpoints.
- [ ] Add employer blacklist endpoints.
- [ ] Run: `pytest tests/test_hh_agent_http_api.py -v`

### Task 10.6: Add HH API Lab

**Files:**

- Modify: `work_hunter/web/server.py`
- Modify: `work_hunter/web/static/app.js`
- Modify: `work_hunter/web/static/index.html`
- Modify: `work_hunter/web/static/app.css`
- Test: `tests/test_hh_api_lab.py`

- [ ] Add raw HH API endpoint that accepts method, path, params/body.
- [ ] Add UI tab with quick actions and arbitrary request form.
- [ ] Add saved snippets table/config.
- [ ] Add "promote to preset/tool" output helper.
- [ ] Run: `pytest tests/test_hh_api_lab.py -v`

### Task 10.7: Add Telegram Remote Control

**Files:**

- Create: `work_hunter/hh_agent/telegram_bot.py`
- Modify: `work_hunter/config.py`
- Test: `tests/test_hh_agent_telegram.py`

- [ ] Add aiogram bot entrypoint.
- [ ] Add `/status`, `/digest`, `/next`, `/reply`, `/pause`, `/resume`.
- [ ] Add inline Approve/Reject/Modify/Flag/Sanity actions for pending messages.
- [ ] Add modify FSM: collect revised instruction, regenerate draft, and requeue for approval.
- [ ] Add Telegram notification sink for run summaries, pending approvals, events, and errors.
- [ ] Add OAuth callback/state flow only if needed for remote onboarding.
- [ ] Use local daemon HTTP API as the bot backend.
- [ ] Run: `pytest tests/test_hh_agent_telegram.py -v`

### Task 11: Final Integration

**Files:**

- Modify: docs as needed.
- Test: full suite.

- [ ] Run: `pytest -q`
- [ ] Run a dry-run HH research flow with fake client fixtures.
- [ ] Run MCP stdio smoke test.
- [ ] Run web UI smoke test.
- [ ] Update `docs/superpowers/plans/2026-06-09-hh-cleanroom-roadmap.md` to mark ported agent pieces.

---

## Priority Slice

If we want the fastest useful result, implement Tasks 0-6.5 first.

That gives:

- hybrid HH transport if Task 0 is included
- policy
- audit tables
- structured LLM analysis
- dedupe
- research/apply service
- MCP agent flow
- approval queue, persona context, events, forms, and notification sink

Updated priority slice: implement Tasks 0-6.5 first. Without Task 0, the agent has good judgment but weak hands. Without Task 6.5, it has hands but no human-in-the-loop nervous system. Then implement `orelkrylatiy`-style HTTP cockpit before Telegram, because every remote/UI/agent control surface becomes easier once the HTTP API is stable.

---

## Acceptance Criteria

- A user can call MCP `hh_research_vacancies` and get analyzed vacancies with persisted reasons.
- A user can call MCP `hh_research_and_apply` in dry-run and receive planned/skipped/blocked counts.
- A user can explicitly confirm a planned application and see an `hh_application_attempts` record.
- A risky/uncertain apply, reply, form-fill, or event action can become a pending approval with Approve/Reject/Modify outcomes.
- Persona markdown affects analysis, cover letters, replies, and form-filling in a traceable way.
- A curated CSV/TSV vacancy list can be dry-run and applied without duplicate attempts.
- Employer messages can create events/tasks and export to ICS/Markdown.
- The HH client can operate through API session, browser cookies, XSRF-backed web actions, and structured challenge outcomes behind one facade.
- Re-running the same research/apply flow does not duplicate equivalent vacancies for the same resume.
- Every LLM decision stores model/config metadata, policy hash, reason, score, and raw-ish structured result.
- Existing `work-hunter` commands and tests continue to pass.
