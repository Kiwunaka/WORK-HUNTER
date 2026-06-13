# External API Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn discovered/HAR-captured non-HH job-board APIs into a repeatable local workflow: inspect, store a personal session, choose source/apply candidates, and safely replay explicit requests.

**Architecture:** Keep this as infrastructure inside `work_hunter`, not a second scraper app. Public discovery/probe stays unauthenticated; private flows use a local session vault imported from the user's own HAR and never print secrets. Adapter planning is data-driven: HAR endpoints are grouped into `jobs`, `profile`, and `apply` candidates before a source adapter is promoted.

**Tech Stack:** Python 3.12 stdlib, JSON files under `.work-hunter`, existing CLI, pytest, existing HAR recon/discovery modules.

---

## File Map

- `work_hunter/external_adapter_plan.py`: Convert HAR endpoint inventory into a source/apply adapter plan.
- `work_hunter/external_sessions.py`: Import, list, show, and explicitly call local personal API sessions.
- `work_hunter/cli.py`: Add `external-adapter-plan` and `external-session` commands.
- `docs/personal-api-recon.md`: Document the workflow from discovery to HAR to session/profile.
- `tests/test_external_adapter_plan.py`: Verify endpoint grouping and CLI output.
- `tests/test_external_sessions.py`: Verify local session import, masking, host guard, and dry-run/real call shape.

## Task 1: HAR-Driven Adapter Plan

- [x] Write failing tests for grouping HAR endpoints into `jobs`, `apply`, and `profile` candidates.
- [x] Implement `build_external_adapter_plan(...)`.
- [x] Add CLI `external-adapter-plan`.
- [x] Verify targeted tests pass.

## Task 2: Local External Session Vault

- [x] Write failing tests for importing session headers from HAR without printing secrets.
- [x] Implement `.work-hunter/external_sessions.json` storage.
- [x] Add host allow-list guard for session calls.
- [x] Add CLI `external-session import-har|list|show|call`.
- [x] Verify targeted tests pass.

## Task 3: Documentation And Verification

- [x] Update `docs/personal-api-recon.md` with the full non-HH workflow.
- [x] Run full `pytest -q`.
- [x] Smoke the new CLI with fixture-style commands where possible.

## Deferred Until User HARs Exist

- Build concrete authenticated adapters for `rvc`, `hirehi`, `getmatch`, and others from real session HARs.
- Promote stable apply endpoints into source-specific `prepare -> approve -> apply` flows.
- Add cockpit UI buttons for `Import HAR`, `Promote endpoint`, and session-backed calls.
