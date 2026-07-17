# HH Ops Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Work Hunter functionally complete as a safe HH career operations cockpit, covering rich campaigns, outcome safety, onboarding, AI filtering, negotiation cleanup, resume tooling, scheduler, enrichment, test assistance, and multi-account isolation.

**Architecture:** Keep domain behavior in `work_hunter.services.WorkHunter`, API wrappers in `work_hunter.sources.hh.HHApplyClient`, durable state in `work_hunter.storage.Storage`, and CLI/web as thin wrappers. External side effects stay dry-run/plan-first and require explicit confirmation.

**Tech Stack:** Python 3.12+, SQLite, pytest, argparse CLI, existing Work Hunter HTTP API, optional Playwright/browser work only in later isolated tasks.

---

## Build Order

1. Rich HH search campaigns. Done in first slice.
2. Anti-duplicate and HH outcome model. Done in first slice.
3. Auth/token onboarding diagnostics. Done in second slice.
4. AI filter stage inside campaigns. Done in second slice.
5. Negotiation cleanup: decline/cancel/blacklist/fast-reject.
6. Resume import/validate from Markdown/TOML/JSON.
7. Safe scheduler/runner.
8. Employer enrichment and email follow-up.
9. Test/question assistant.
10. Multi-HH-account profile isolation.

## Task 1: Rich HH Search Campaigns

**Files:**
- Modify: `work_hunter/sources/hh.py`
- Modify: `work_hunter/services.py`
- Modify: `work_hunter/cli.py`
- Test: `tests/test_hh_rich_campaign.py`

- [x] **Step 1: Write failing tests**

Add tests for `WorkHunter.plan_hh_search_campaign(...)` proving it calls HH `/vacancies` with rich filters, stores imported jobs, and creates campaign items.

- [x] **Step 2: Verify RED**

Run: `pytest tests/test_hh_rich_campaign.py -q`

- [x] **Step 3: Implement minimal client/service/CLI**

Add `HHApplyClient.search_vacancies(params)` and `WorkHunter.plan_hh_search_campaign(...)`.

- [x] **Step 4: Verify GREEN**

Run: `pytest tests/test_hh_rich_campaign.py -q`

## Task 2: Outcome And Anti-Duplicate Model

**Files:**
- Modify: `work_hunter/models.py`
- Modify: `work_hunter/storage.py`
- Modify: `work_hunter/services.py`
- Modify: `work_hunter/sources/hh.py`
- Test: `tests/test_hh_campaign_outcomes.py`

- [x] **Step 1: Write failing tests**

Cover outcomes: `already_applied`, `limit_exceeded`, `overall_limit`, `test_required`, `archived`, `has_relations`, `skipped_before`, `local_application_exists`.

- [x] **Step 2: Verify RED**

Run: `pytest tests/test_hh_campaign_outcomes.py -q`

- [x] **Step 3: Implement outcome mapping**

Store outcome/reason on campaign items and skipped vacancies. Real confirm must preserve HH raw result.

- [x] **Step 4: Verify GREEN**

Run: `pytest tests/test_hh_campaign_outcomes.py -q`

## Task 3: Auth And Token Onboarding

**Files:**
- Modify: `work_hunter/services.py`
- Modify: `work_hunter/cli.py`
- Modify: `work_hunter/web/server.py`
- Test: `tests/test_hh_onboarding.py`

- [x] Add `hh_auth_status()` that validates access token, refresh token, expiry, client credentials, and returns actionable diagnostics.
- [x] Add CLI `hh-auth-status`.
- [x] Add web endpoint `GET /api/hh/auth/status`.
- [ ] Keep OAuth/browser login as a later isolated opt-in; do not copy upstream bypass constants.

## Task 4: AI Filter Stage

**Files:**
- Modify: `work_hunter/services.py`
- Modify: `work_hunter/storage.py`
- Test: `tests/test_hh_ai_filter_stage.py`

- [x] Add campaign option `ai_filter_mode=light|heavy|off`.
- [x] Persist AI decision JSON on campaign item raw result.
- [x] Save skipped reason `ai_rejected` with vacancy/resume context.
- [x] Make AI failures conservative: mark `needs_review`, not silently apply.

## Task 5: Negotiation Cleanup

**Files:**
- Modify: `work_hunter/sources/hh.py`
- Modify: `work_hunter/services.py`
- Modify: `work_hunter/cli.py`
- Test: `tests/test_hh_negotiation_cleanup.py`

- [x] Add dry-run planner for old/discarded negotiations.
- [x] Add explicit confirm for decline/cancel.
- [x] Add employer blacklist action.
- [x] Store cleanup outcome locally.
- [ ] Add fast-reject/ATS-specific cleanup rules after live HH payload samples.

## Task 6: Resume Import And Validation

**Files:**
- Create: `work_hunter/resume_payloads.py`
- Modify: `work_hunter/services.py`
- Modify: `work_hunter/cli.py`
- Test: `tests/test_hh_resume_payloads.py`

- [x] Load JSON directly.
- [x] Load TOML when `tomllib` can parse it.
- [x] Load Markdown into a conservative structured payload.
- [x] Validate required HH fields and return missing fields before POST.

## Task 7: Safe Scheduler/Runner

**Files:**
- Create: `work_hunter/scheduler.py`
- Modify: `work_hunter/cli.py`
- Test: `tests/test_scheduler.py`

- [x] Add safe task runner for `sync`, `score`, `hh-refresh-token`, `hh-update-resumes`, `hh-campaign-plan`.
- [x] No real external send by default.
- [x] Add lock file to prevent overlapping runs.
- [x] Add JSON run report.

## Task 8: Employer Enrichment And Email Follow-Up

**Files:**
- Modify: `work_hunter/models.py`
- Modify: `work_hunter/storage.py`
- Modify: `work_hunter/services.py`
- Test: `tests/test_hh_employer_enrichment.py`

- [x] Store employer site snapshots.
- [x] Parse obvious public emails from employer site HTML.
- [x] Add SMTP dry-run/confirm follow-up plan.

## Task 9: Test/Question Assistant

**Files:**
- Modify: `work_hunter/services.py`
- Test: `tests/test_hh_question_assistant.py`

- [x] Detect `test_required` and question-required states from vacancy/apply payloads.
- [x] Draft answer suggestions from vacancy and resume context.
- [x] Require explicit user confirmation before any answer submission.

## Task 10: Multi-HH-Account Profile Isolation

**Files:**
- Modify: `work_hunter/config.py`
- Modify: `work_hunter/services.py`
- Modify: `work_hunter/cli.py`
- Test: `tests/test_hh_account_profiles.py`

- [x] Add `hh_account_profile` config section.
- [x] Allow separate HH tokens per account profile.
- [x] Keep account profile separate from search profile.
- [x] Add CLI switch/list commands.

## Verification

- Run targeted tests after each task.
- Run `pytest -q` after each completed slice.
- Run `python -m compileall work_hunter` before completion.
- Update this plan with completed slices and remaining work.

## Task 11: Scheduled Post-Apply Parity

- [x] Paginate negotiations and message history for employer replies.
- [x] Add resume, invitation, age, blacklist, AI, history, and delay controls.
- [x] Journal automatic replies and suppress duplicate replies to the same message.
- [x] Suppress duplicate recruiter email with configurable repeat cooldown.
- [x] Add configurable ATS fast-reject detection to cleanup.
- [x] Expose resume raise, sync, replies, enrichment, email, cleanup, and clear-skipped through the JSON runner.
- [x] Add a complete maintenance runner example while keeping each live task separately opt-in.

## Progress

- 2026-06-09: Implemented rich HH search campaign planning via `plan_hh_search_campaign`, `HHApplyClient.search_vacancies`, and CLI `hh-search-campaign-plan`.
- 2026-06-09: Implemented first outcome/anti-duplicate layer for archived vacancies, HH relations, previous skipped vacancies, local applications, `test_required`, and HH apply errors such as `limit_exceeded`.
- 2026-06-09: Implemented `hh_auth_status` diagnostics and CLI `hh-auth-status`.
- 2026-06-09: Implemented campaign `ai_filter_mode=off|light|heavy` with persisted AI decision and `ai_rejected` skip reason.
- Verification: `pytest tests/test_hh_rich_campaign.py tests/test_hh_campaign_outcomes.py -q` passed; `pytest -q` passed with 67 tests; `python -m compileall work_hunter` passed.
