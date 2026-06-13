# Work Hunter Design

Date: 2026-04-26

## Goal

Build a personal local job-search assistant named `work-hunter`.

The tool should help one user find work faster across HH, Habr Career, and GeekJob by collecting vacancies, scoring them against the user's resume/search profile, preparing responses, tracking status, and exposing the workflow through both MCP tools and a local UI.

This is a personal non-commercial project. `hh-applicant-tool` can be used as the primary HH engine under its personal-use license, but `work-hunter` should stay separate so upstream updates remain easy.

## Recommended Approach

Create a separate `work-hunter` project in the current workspace.

`work-hunter` will use an adapter around `hh-applicant-tool` instead of copying its code. The adapter can call its CLI or, where stable, import its Python package APIs. HH-specific actions such as authorization, apply dry-runs, real HH applies, local HH SQLite history, and resume refresh stay delegated to `hh-applicant-tool`.

HH search should prefer the official API when an `HH_ACCESS_TOKEN` or local access token is configured. Because current HH vacancy search is no longer reliably anonymous, MVP also keeps a personal-use HTML fallback for collecting visible search results.

Habr Career and GeekJob start as read-only sources:

- Habr Career: RSS from search pages first, HTML fallback when needed.
- GeekJob: HTML vacancy pages first, Telegram channel URLs stored as discoverable external sources.

Real apply automation outside HH is out of MVP scope. For non-HH vacancies, the UI/MCP should prepare a message and open the original vacancy/contact.

## Architecture

### Backend

Use Python for the backend because the strongest existing HH tool is Python and the MCP Python SDK is official and stable.

Core modules:

- `work_hunter.config`: local settings, profile, paths, source enablement, stop words, scoring weights.
- `work_hunter.storage`: SQLite database for normalized vacancies, source metadata, status history, generated letters, notes, and run logs.
- `work_hunter.sources.hh`: HH collector with OAuth API support, personal-use HTML fallback, and `hh-applicant-tool` apply adapter.
- `work_hunter.sources.habr`: Habr Career RSS/HTML collector.
- `work_hunter.sources.geekjob`: GeekJob HTML collector plus curated Telegram channel registry.
- `work_hunter.scoring`: deterministic score and reasons: stack match, title match, seniority, salary, remote/location, company blacklist, stop words.
- `work_hunter.letters`: cover-letter draft generation from templates first; optional AI hook later.
- `work_hunter.mcp_server`: MCP tools for agent workflows.
- `work_hunter.web`: local UI server.

### MCP Tools

Expose tools that are useful from Codex/Claude:

- `search_jobs`: collect fresh vacancies from enabled sources.
- `score_jobs`: score stored vacancies against the active profile.
- `list_jobs`: return filtered/sorted jobs with reasons.
- `get_job`: show one normalized vacancy with source-specific details.
- `prepare_cover_letter`: draft a response for a job.
- `apply_hh`: apply to HH vacancy through `hh-applicant-tool`; default to dry-run unless explicitly confirmed.
- `mark_job`: set status such as `saved`, `hidden`, `applied`, `rejected`, `interview`, `offer`.
- `daily_report`: summarize fresh matches and follow-up actions.

### UI

Build a compact local web UI, not a marketing page.

Primary screens:

- Inbox: table of jobs sorted by match score, with source, title, company, salary, remote/location, status, and score reasons.
- Job detail: description, matched skills, red flags, source link, letter draft, and action buttons.
- Sources: enabled sources, last sync, source errors, GeekJob channel list.
- Profile/settings: search queries, desired roles, must-have skills, nice-to-have skills, stop words, salary floor, locations, remote preference.
- Pipeline: statuses and follow-up queue.

Important UI behavior:

- HH apply requires explicit user action.
- Non-HH apply opens the source/contact and stores a prepared letter.
- Every automated skip must show a human-readable reason.
- Secrets and tokens must not be rendered in the UI.

## Data Model

SQLite tables:

- `jobs`: normalized vacancy fields: source, source_id, url, title, company, salary text/range, location, remote flag, description text, published_at, fetched_at.
- `job_scores`: job_id, profile_id, total_score, component scores, reasons JSON, red_flags JSON.
- `job_status`: job_id, status, note, changed_at.
- `letters`: job_id, template_name, body, created_at.
- `sources`: source name, enabled, last_sync_at, last_error.
- `runs`: command/tool runs with counts, duration, and errors.

## Error Handling

- Source collectors should fail independently. If GeekJob fails, HH and Habr still work.
- Network errors should be stored in `sources.last_error` and shown in UI.
- HH authorization errors should surface as "run hh-applicant-tool authorize" with the configured profile/path.
- Real HH apply should return the raw safe summary from `hh-applicant-tool`, not hide failures.
- Duplicate jobs should be merged by `(source, source_id)` and optionally by URL.

## Security And Safety

- Local-only by default: bind UI to `127.0.0.1`.
- Store secrets only in existing `hh-applicant-tool` config or local `.env`; never echo them in MCP or UI.
- Default destructive or external actions to dry-run.
- Do not bypass site protections for Habr Career or GeekJob in MVP.
- Keep the tool for personal use; do not package it as a paid/public automation service.

## Testing

Start with focused tests:

- scoring rules produce expected scores and reasons;
- storage upsert and status transitions work;
- Habr RSS parser handles empty feeds and normal items;
- GeekJob parser extracts list items from saved HTML fixtures;
- MCP tool functions call service layer without requiring real network;
- HH adapter can be tested with a fake `hh-applicant-tool` command runner.

Manual verification:

- run local UI and verify desktop/mobile usable layout;
- run MCP tool list and one dry-run search;
- verify no secret values appear in logs/UI/tool output.

## MVP Milestones

1. Scaffold `work-hunter` Python project, config, storage, and CLI.
2. Implement Habr RSS collector and GeekJob HTML collector.
3. Implement scoring and normalized job list.
4. Add HH adapter with safe dry-run integration to `hh-applicant-tool`.
5. Add MCP server tools.
6. Add local UI for inbox, job detail, sources, and settings.
7. Verify end-to-end: collect, score, inspect in UI, prepare letter, mark status, HH dry-run apply.

## Open Decisions

- Whether to call `hh-applicant-tool` only through CLI first or import its package APIs for tighter integration.
- Whether to use FastAPI for both UI API and static serving, or keep the UI as a lighter single-process server.
- Whether AI letter generation should be included in MVP or deferred after deterministic templates work.
