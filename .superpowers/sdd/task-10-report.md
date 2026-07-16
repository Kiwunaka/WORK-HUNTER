# Task 10 Report

Base: `e324361 fix: reserve literal application capacity`

## RED evidence

- Compact reconciliation contract was written before production code.
- Initial run failed during collection with
  `ModuleNotFoundError: work_hunter.hh_autopilot.reconcile`.
- First implementation pass produced `5 passed, 11 failed`; the failures
  exposed the expected missing challenge lifecycle/requeue methods and the
  Task 9 finalizer CAS that could not finish an already `reconciling` attempt.

## Implemented

- Added strict typed `NegotiationSnapshot` normalization plus account-wide,
  configurable-status pagination and remote-ID deduplication.
- Added `HHApplicationReconciler` classification for same-resume/current,
  external or predating, bounded confirmed absence, unclassifiable history,
  and read-auth failure.
- Same-resume history consumes the original held reservation and finalizes the
  original attempt/application. It never sends a second application POST.
- External history releases the dispatch reservation, closes the item as
  `duplicate_external`, updates the account-vacancy guard, and syncs current
  local-day quota idempotently.
- Bounded absence returns to `ready` only while POST attempts remain;
  exhausted attempts go to `dead`. Duplicate/unclassifiable history opens one
  held `ambiguous_application` challenge.
- Reconciliation auth failure opens/reuses one non-expiring account
  `manual_auth` challenge while retaining the held reservation.
- Added grant-independent `HHRecoverySweep` for stale `applying` and due
  `reconciling` items. Its injected port exposes negotiation reads only.
- Added atomic ambiguity expiry and all four exact late actions:
  `confirmed_applied`, `confirmed_not_applied_retry`,
  `confirmed_not_applied_skip`, and `retry_reconciliation`.
- Generic dead-item requeue rejects unresolved ambiguity and otherwise writes
  an audited `operator_requeue` transition to the recorded retry stage.
- Reused the Task 9 application finalizer and widened only its CAS so a durable
  `reconciling` attempt can be finalized by read-only recovery.

## GREEN evidence

- Compact Task 10 suite: `16 passed in 2.36s`.
- Required reconciliation + executor + repository command:
  `196 passed in 23.98s`.
- Both commands exited successfully. The known non-fatal Windows pytest
  temp-directory cleanup `PermissionError` appeared after the passing summary.

## Static verification

- Ruff on all changed Python files: `All checks passed!`.
- Mypy on five changed production modules with
  `--follow-imports=skip --ignore-missing-imports`:
  `Success: no issues found in 5 source files`.
- `git diff --check`: passed.

## Remaining scope

- No live HH mutation was run. Current-HH behavior still requires the explicit
  opt-in canary.
- Scheduler wiring and supported form/CAPTCHA continuation remain later plan
  tasks; recovery itself is now grant-independent and POST-inaccessible.
