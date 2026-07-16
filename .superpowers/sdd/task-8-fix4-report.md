# Task 8 Fix 4 Report

Base: `866f444 fix: secure HH ranking sanitization`

## RED evidence

All regressions were added before their production changes.

- Composite decode, post-strip rescan, and authentication-prose package:
  `16 failed, 10 passed in 0.86s`.
- Sequential candidate-set seal and completeness package:
  `7 failed in 1.31s`.
- Concurrency mutation check with the ranking-side seal assertion temporarily
  removed: `1 failed in 1.28s`; the late ranking returned `ranked` instead of
  being rejected as `sealed`.

The failures showed that independently ordered decoding passes missed
cross-encoding payloads, stripped HTML could join text into a credential,
ordinary Basic/Bearer technical prose was over-rejected, finalization did not
durably close the candidate set, and incomplete or late candidates could still
change the winner.

## Implemented

- Replaced independent decoding passes with one bounded composite fixed-point
  loop: HTML entity decoding, NFKC normalization, percent decoding, and hidden
  control validation repeat until the complete value stabilizes.
- Rebuilt and rescanned the security view after ordinary raw-HTML stripping.
- Kept explicit credential key/value rejection while distinguishing standalone
  Basic Base64 credentials and token-shaped Bearer values from technical prose.
- Added an append-only candidate-set seal based on canonical ranking
  finalization events for the same account, vacancy, and run.
- Enforced the seal inside the existing `BEGIN IMMEDIATE` transactions for
  filter writes, ranking writes, and repeated finalization.
- Made finalization inspect every exact account/vacancy/run item, blocking
  discovered, eligible, retry-wait, and retryable-ranked candidates while
  allowing hard-filter skips and canonical terminal nonready rankings.
- Preserved exact qualifying-set version checks and the existing stable-winner
  logic.

## GREEN evidence

- Composite sanitizer/prose package: `26 passed in 0.47s`.
- Sequential seal/completeness package: `7 passed in 2.05s`.
- Restored two-connection finalization/late-ranking race:
  `1 passed in 0.51s`.
- Seal durability after `READY -> APPLYING` and a later raw `last_run_id`
  change, plus the terminal-nonready positive: `2 passed in 0.61s`.
- Required policy/ranking/repository/scoring command:
  `445 passed in 22.10s`.
- Required search/config/state-machine command:
  `302 passed in 12.87s`.

Both required pytest commands exited successfully. On this Windows host pytest
printed a non-fatal temp-directory cleanup `PermissionError` after each passing
summary.

## Static verification

- Ruff on all five changed Python files: `All checks passed!`.
- Mypy on both changed production files with `--ignore-missing-imports`:
  `Success: no issues found in 2 source files`.
- Production imports: `imports ok`.
- `git diff --check`: passed.

No migrations, configuration, UI, executor, challenge, scheduler, or
later-task code was changed. No guard, application-attempt, reservation, or
challenge rows are created by candidate-set finalization or rejected late
writes.
