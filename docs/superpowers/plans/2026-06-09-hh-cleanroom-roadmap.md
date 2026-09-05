# HH Integration And Donor Roadmap

Date: 2026-06-09

## Research Scope

Reviewed `s3rgeym/hh-applicant-tool`, `0FL01/hh-applicant-tool`, local copy in `external/hh-applicant-tool`, GitHub issues, PRs, discussions, forks, PyPI, SourcePulse, and adjacent HH automation products.

This is a private, personal-use integration roadmap. Compatible open-source code may be reused under its license, and local APK/web/HAR reverse engineering may be used to recover endpoints, public client configuration, payload shapes, and browser behavior. Account credentials and captured sessions stay in local ignored state.

## Core Product Principle

The original project is an autonomous HH automation engine:

- Local-first profile folders: config, tokens, cookies, logs, SQLite state.
- Hybrid API plus web session: API where available, browser/web flow where API lacks coverage.
- CLI-first operations: UI is a wrapper over the same operation layer.
- Cron/server-first usage: update resumes, refresh token, and apply on schedule.
- Broad HH control surface: many search filters, resume operations, messaging, skipped analysis, cleanup, tests, captcha, email.
- Partial safety: dry-run exists, but real mass apply can run from cron.

Work Hunter should not become only a clone of that. Our better direction is a safer career operations cockpit:

- Plan first, confirm explicitly before external side effects.
- Explain why each vacancy is ready/skipped/risky.
- Keep auditable state for every campaign item, letter, negotiation, and follow-up.
- Let UI be the next layer over solid service/CLI primitives.

## What We Already Have

- Multi-source pipeline: HH, Habr, GeekJob, Telegram.
- Local jobs, scores, statuses, letters, applications, calendar, saved searches.
- HH resumes, employers, contacts, negotiations, skipped vacancies, campaign runs/items.
- HH whoami, token refresh, auth diagnostics, account profiles, resume sync/update/create/clone/publish.
- HH rich search campaigns, similar-vacancy research, campaign plan/confirm with explicit confirmation.
- Reply employers with dry-run/confirm, chat outbox, webhook delivery, Telegram remote controls.
- Agent cockpit: preflight, digest, runs, approvals, API lab, templates, blacklist, inbox, events/tasks, resume templates.
- Events/tasks/calendar export, forms/challenge escalation, curated apply-from-file, batch preset matrices.
- Safe scheduler/runner with notifications, command logs, dry-run/planned defaults, and Docker/native hardening notes.
- Query/operator summary and web API endpoints with secret masking on sensitive responses.
- MCP and UI share the same literal-confirmation contract for real apply.

## Main Gaps Versus Original

### P0 Functional Gaps

1. Rich HH search campaigns
   - Support direct HH search params: area, role, industry, salary, schedule, experience, employment, date, period, only_with_salary, search_field, premium, employer include/exclude.
   - Let campaign plan query HH directly, not only use already-synced local jobs.

2. Stronger anti-duplicate and limit model
   - Check local applications, HH negotiations, HH vacancy relations, skipped vacancies, and resume-specific attempts.
   - Store skip reasons per resume and vacancy.
   - Treat `already_applied`, `limit_exceeded`, `overall_limit`, `test_required`, archived vacancy/resume as first-class outcomes.

3. Full campaign audit payload
   - For each item store selected resume, letter, risk flags, vacancy URL, employer, response requirements, test flags, raw HH outcome.

4. HH auth/token onboarding
   - At minimum: token health, documented import, refresh diagnostics, config validation.
   - Later: clean OAuth/onboarding flow, if implemented legally and maintainably.

### P1 Functional Gaps

5. AI vacancy filter stage
   - Add light/heavy relevance filter as a separate campaign stage.
   - Save AI decision, reason, prompt version, and confidence.

6. Negotiation cleanup and CRM actions
   - Clear/decline old negotiations with dry-run first.
   - Blacklist employers.
   - Detect fast-reject/ATS patterns.
   - Sync message snapshots for inbox/follow-up logic.

7. Resume payload tooling
   - Import resume payload from JSON/TOML/Markdown.
   - Validate required HH fields before create.
   - Generate role-specific resume variants and diff them.

8. Scheduler/runner
   - Safe scheduled tasks by default: sync, score, update resumes, refresh token, build plan.
   - No scheduled real apply unless a campaign has already been explicitly approved.
   - Docker/native runner with writable config volume and read-only app files.

### P2 Functional Gaps

9. Employer enrichment and email follow-up
   - Store employer sites, parsed emails, vacancy contact snapshots.
   - Optional SMTP follow-up with dry-run/confirm.

10. Test/question assistant
   - Detect tests/questions and draft answers.
   - Keep human confirmation for high-risk or non-trivial tests.

11. Reference data and preset UX backend
   - Sync HH areas, professional roles, industries, dictionaries.
   - Save reusable campaign presets with validation.

12. Multi-account/profile isolation
   - Separate HH account profiles with isolated tokens/cookies/db state.
   - Keep search profiles separate from account profiles.

## External Signals

- Upstream `s3rgeym/hh-applicant-tool`: active, about 425 stars, 57 forks, 0 open issues, 1 open PR, latest tags up to `v1.8.10`.
- `0FL01/hh-applicant-tool`: low-signal fork, 0 stars, 0 forks, issues/discussions disabled, pushed 2026-05-19; useful mainly as a small fork signal, not as a base.
- Issues/PR themes:
  - auth/token fragility;
  - search filters and relevance;
  - HH limits;
  - test-required/redirect flows;
  - captcha;
  - Windows/Docker onboarding;
  - UI wrapper;
  - AI filtering;
  - email/contact parsing.
- Fork themes:
  - Telegram wrappers;
  - UI/login improvements;
  - AI filtering/captcha;
  - exclude keyword logic;
  - Docker ergonomics.
- Commercial products converge on:
  - filters, AI matching, AI letters;
  - working hours, limits, retries;
  - stats/dashboard;
  - Telegram notifications;
  - resume optimization;
  - question/test handling.

## Integration Rules

- Reuse upstream code when its license is compatible with this private personal-use project.
- APK/HAR/browser recon may recover client configuration, endpoints, selectors, and payload shapes into ignored local state.
- API/session is preferred when stable; persistent browser automation is the universal fallback.
- Real external actions use the shared plan/confirm contract and leave an application record.
- Every automated decision is explainable and logged.

## Completion Update

2026-06-09: the main HH parity/agentic-tooling slice is implemented and verified locally. Remaining work should now be treated as optional enhancement, not foundation parity:

1. Live OAuth/browser onboarding polish.
2. Deeper UI ergonomics and visual QA.
3. More live HH payload samples for niche cleanup/test/captcha states.
4. Optional encrypted secret storage.
5. Optional cross-board API adapters beyond HH.
