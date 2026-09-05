# HH Foundation Parity Plan

Date: 2026-06-09

## Goal

Make Work Hunter's HH layer feature-complete enough to stand on the same foundation as `hh-applicant-tool`, then keep Work Hunter's AI, CRM, and multi-source features as a higher layer.

## Principles

- Functional parity first: implement the missing HH workflows before adding more AI surface.
- Reuse compatible open-source implementation pieces under their licenses and use APK/web/session reverse engineering where it shortens the personal-use integration path.
- Human confirmation by default: dry-run and explicit confirmation for actions that send applications or messages.
- Auditable state: every HH campaign/action should leave local records with status, reason, and raw result.

## Foundation Parity Backlog

1. HH account/API foundation
   - whoami
   - token status/refresh hooks
   - normalized API errors

2. HH local data model
   - resumes
   - employers
   - contacts
   - negotiations
   - skipped vacancies
   - campaign runs/items

3. HH resume operations
   - list/sync resumes
   - update/publish hooks
   - clone/create from local markdown payload

4. HH application campaigns
   - plan/dry-run campaign
   - skip reasons and risk flags
   - confirm campaign
   - result persistence

5. HH employer/negotiation operations
   - sync negotiations
   - reply employer conversations
   - clear negotiations/skipped local records

6. Operator tooling
   - CLI commands
   - local web API endpoints
   - UI controls/presets
   - cron/Docker runner

7. Work Hunter layer on top
   - AI fit and letter generation
   - Habr/GeekJob/Telegram source funnel
   - ATS/gap/interview prep
   - saved searches, calendar, CRM views

## First Implementation Slice

Implement the local HH foundation and campaign runner:

- Add HH dataclasses for resumes, employers, contacts, negotiations, skipped vacancies, campaign runs, and campaign items.
- Add SQLite schema and storage methods for these entities.
- Add `WorkHunter.sync_hh_resumes`, `WorkHunter.plan_hh_campaign`, and `WorkHunter.confirm_hh_campaign`.
- Add CLI commands for `hh-resumes`, `hh-campaign-plan`, and `hh-campaign-confirm`.
- Add web API endpoints for campaign plan/confirm.
- Cover behavior with failing tests first, then implementation.

## Verification

- Targeted pytest for new HH foundation tests.
- Existing apply-plan tests must continue to pass.
- Full pytest suite must pass before marking this slice complete.

## Progress

- Done: HH account/API foundation, local HH data model, resume sync/update, campaign plan/confirm, query/operator summary, web API endpoints.
- Done: HH resume create/clone/publish service and CLI commands with dry-run support.
- Done: HH employer reply planner/sender with explicit confirmation, template variables, latest-employer-message filtering, and chat endpoint support.
- Verified: `pytest -q` passed with 59 tests; `python -m compileall work_hunter` passed.

## Next Functional Slice

- Add richer negotiation actions: decline/cancel with message, blacklist employer, archive/clear local negotiations.
- Add resume payload helpers: import from markdown/JSON file, validate required HH fields before create, generate HH-ready payload from local resume text.
- Add campaign analytics: response rates by search preset, employer, role, salary band, and skip reason.
- Add scheduler/runner: safe repeatable sync/score/plan jobs with no UI dependency.
