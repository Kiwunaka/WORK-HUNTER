# Task 9 Report

Base: `925be61 docs: record Task 8 completion`

## RED evidence

Tests were written before production implementation.

- Transport certainty and response adapter:
  `19 failed, 29 passed in 0.97s`.
  Failures were the missing `delivery_certainty` contract and missing
  `HHApplyClient.apply_outcome()`.
- Executor collection:
  `ModuleNotFoundError: work_hunter.hh_autopilot.executor`.
- Hidden application replay regression:
  a 401 with a refresh token produced two `POST /negotiations` calls instead
  of one.
- Conservative transport regressions:
  `5 failed` for token-refresh certainty, unknown 2xx responses, and
  substring-based false classifications.

## Implemented

- Added explicit `DeliveryCertainty` to every typed HH transport failure.
  Only `POST /negotiations` late/unknown errors are `possibly_sent`;
  connect timeout and non-application/preflight failures are
  `definitely_not_sent`.
- Disabled generic 401 refresh/replay for an application POST. One prepared
  attempt now produces at most one wire POST.
- Added `HHApplyClient.apply_outcome()` with conservative exact-field and
  exact-location response mapping for 201/empty 201, redirect/form, CAPTCHA,
  assessment, duplicate, closed vacancy, invalid request, authentication,
  forbidden, 429/Retry-After, HH daily limit, 5xx, and malformed/unknown
  successful responses. Legacy `apply()` keeps its existing result shape.
- Added the single `HHApplicationExecutor` and bounded persisted
  `HHRetryPolicy`. The executor renders the cover letter before preparation,
  re-reads the current settings/policy snapshot, renews the lease immediately,
  performs one atomic prepare, performs one bounded POST, and never sleeps.
- Added atomic `prepare_dispatch()`:
  current typed authorization, config projection/policy/window, DB grant,
  pause/kill, run, fence, cooldown, item CAS, exact application identity,
  account-vacancy guard, immutable attempt/provenance, quota reservation,
  literal target, applying state, and journal event are checked/written in one
  `BEGIN IMMEDIATE`.
- Added atomic applied, definite-failure, and ambiguous-result finalization.
  Application, attempt, reservation, item, event, guard, run counter, and
  one-shot target/cap changes commit or roll back together.
- Possibly-sent and duplicate results retain quota as `held`, enter
  `reconciling`, and cannot become retry-ready.
- Definite failures release quota and persist retry/skip/dead/challenge
  decisions. Rate and HH daily-limit responses also persist account cooldown.
- Cover-letter failure records a definitely-not-sent pre-dispatch failure
  without a quota/guard leak and without incrementing the actual POST count.
- Local finalization intentionally does not re-check a grant, pause, kill
  switch, window, or cooldown after a remote response; it still requires the
  immutable attempt provenance and current fencing token.
- Literal confirmation is limited to its immutable account/resume/vacancy
  target set and bounded success cap.
- Repository persistence sanitizes the outcome again instead of trusting a
  transport-supplied payload.
- Successful finalization can atomically materialize the canonical HH job from
  the persisted search result before inserting the FK-backed application.

## GREEN evidence

- Executor suite: `34 passed in 6.80s`.
- Transport suite: `54 passed in 1.79s`.
- Required Step 10 command: `108 passed in 7.37s`.
- Repository, authorization, config, state-machine, strict-boundary, and
  legacy source compatibility command: `406 passed in 40.82s`.
- Final combined focused/affected command: `514 passed in 34.91s`.

Every pytest command exited successfully. This Windows host printed the known
non-fatal pytest temp-directory cleanup `PermissionError` after the passing
summary.

## Static verification

- Ruff on all changed Python files: `All checks passed!`.
- Mypy on the six changed production modules with
  `--follow-imports=skip --ignore-missing-imports`:
  `Success: no issues found in 6 source files`.
- Production import smoke: `imports-ok`.
- `git diff --check`: passed.

## Remaining scope

- No live HH mutation was run; current-HH behavior still requires the explicit
  opt-in canary from the rollout plan.
- The scheduler/orchestrator task must wire `settings_provider` and
  `policy_hash_provider` to the existing locked current-config reader. The
  executor already re-reads them immediately before atomic preparation.
- Automatic supported form handling, CAPTCHA continuation, reconciliation,
  recovery sweep, and scheduler integration remain the later planned tasks.
