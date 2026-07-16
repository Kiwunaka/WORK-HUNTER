# Task 8 Fix 3 Report

Base: `8d3548b fix: close HH ranking review gaps`

## RED evidence

All regressions were added before their production changes.

- Unicode/IDNA/control/percent/address/URL/date value-object and injected-AI
  package: `50 failed, 6 passed in 1.53s`.
- Generic effective-run ownership package: `7 failed in 1.34s`.
- Follow-up percent/fullwidth markup package:
  `6 failed, 48 passed in 0.83s`.

The failures showed the missing behavior: obfuscated sensitive text could be
constructed or sent to the backend, ISO date ranges were classified as phone
numbers, generic transitions could omit effective-run ownership, and markup
revealed only by NFKC/percent decoding was not rejected.

## Implemented

- Extended the single shared sanitizer with a bounded NFKC security view and
  bounded fixed-point percent decoding.
- Rejected hidden formatting, bidi, zero-width, surrogate, and unexpected
  control characters before sensitive-data matching.
- Added Unicode-local/IDNA-domain email validation.
- Added number-first, street-first, and Russian address detection.
- Added URL parsing after percent decoding and rejected username-only or
  username/password userinfo.
- Excluded valid ISO date tokens from phone scanning while retaining 10–15
  digit Russian, international, and hyphenated phone detection.
- Rejected markup exposed only by compatibility normalization or percent
  decoding while retaining ordinary raw-HTML handling and ordinary percent
  text.
- Made every successful generic transition validate and persist its effective
  run ID.
- Rejected completed effective runs, newer-lease token borrowing, and
  zero-fence non-manual live runs while preserving the explicit unfenced
  manual compatibility path.

## GREEN evidence

- Focused Unicode/IDNA sanitizer package: `56 passed in 0.54s`.
- Focused generic effective-run package: `7 passed in 0.90s`.
- Full policy/ranking files: `265 passed in 3.46s` before the final markup
  additions; the final required combined run below includes those additions.
- Full repository file: `135 passed in 18.80s`.
- Required policy/ranking/repository/scoring command:
  `409 passed in 19.14s`.
- Required search/config/state-machine command:
  `302 passed in 13.79s`.

Both required pytest commands exited successfully. On this Windows host pytest
printed a non-fatal temp-directory cleanup `PermissionError` after the passing
summary.

## Static verification

- Ruff on all changed Python files: `All checks passed!`.
- Mypy on both changed production files with `--ignore-missing-imports`:
  `Success: no issues found in 2 source files`.
- Production imports: passed.
- `git diff --check`: passed.

No migrations, configuration defaults/schema, UI, executor, challenges,
scheduler, or later-task code were changed. Existing stage-owned transition
blocking and post-`BEGIN IMMEDIATE` timestamp capture remain intact.
