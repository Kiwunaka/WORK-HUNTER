# Task 8 Fix 5 Report

Base: `c9118fd fix: seal HH ranking candidate sets`

## RED evidence

The focused regressions were added before the production change.

- Dual HTML-strip views, safe output spacing, Bearer technical prose, and real
  credential controls: `5 failed, 13 passed in 0.73s`.

The failures were exact:

- `Bea<b></b>rer abc123` and `access_<b></b>token=abc123` reached the injected
  `StructuredAIRanker` backend because only the space-replaced form was
  rescanned;
- `Experience with Bearer token-based authentication` and
  `Bearer JWT-based authentication` were rejected as credentials;
- direct `AIDecision` construction failed on the same safe technical prose.

The existing and newly explicit controls for Authorization headers, terminal
`Bearer opaque-token`, JWT, digit-bearing, Base64 Basic, and high-entropy
Bearer payloads remained green during RED.

## Implemented

- For ordinary allowed HTML, the shared sanitizer now constructs and rescans
  both post-strip representations:
  - tags removed without a separator;
  - tags replaced with spaces.
- Markup, credential, PII, and control checks run against both stripped
  security views in addition to the original security view.
- The returned normalized value still comes only from the space-replaced form,
  preserving semantic word boundaries in the AI payload.
- Standalone Bearer classification now allows only the technical compound
  descriptors `token-based` and `JWT-based` when they directly describe
  `authentication`.
- Explicit Authorization key/value detection and terminal token-shape checks
  remain unchanged.

## GREEN evidence

- Exact focused regression package: `18 passed in 0.39s`.
- Full sanitizer/ranking package:
  `304 passed in 5.53s`.
- Required policy/ranking/repository/scoring command:
  `452 passed in 23.33s`.
- Required search/config/state-machine command:
  `302 passed in 13.35s`.

Both required pytest commands exited successfully. On this Windows host pytest
printed the established non-fatal temp-directory cleanup `PermissionError`
after each passing summary.

## Static verification

- Ruff on the changed production and test files: `All checks passed!`.
- Mypy on the changed production file with `--ignore-missing-imports`:
  `Success: no issues found in 1 source file`.
- Production imports: `imports ok`.
- `git diff --check`: passed.

Only `work_hunter/hh_autopilot/sanitization.py`, its focused ranking
regressions, and this report were changed. Repository candidate-set seal logic,
migrations, configuration, UI, executor, challenges, scheduler, and later-task
code were not touched.
