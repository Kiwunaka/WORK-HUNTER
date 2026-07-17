# HH Native Challenge Resolution — Implementation Delta

**Goal:** connect the already-built HH autopilot executor to real automatic vacancy tests, forms, and application CAPTCHA handling, matching the current `s3rgeym/hh-applicant-tool` protocol while reusing Work Hunter's persisted authenticated browser session.

## Task 1: Configurable AI and browser challenge primitives

- Add `off|profile_grounded|ai` screening/form modes, `manual_handoff|vision_then_manual` CAPTCHA modes, and bounded challenge attempts.
- Add configurable per-purpose `ai.tests` and `ai.captcha` overrides that inherit the existing AI endpoint/model credentials.
- Extend the browser adapter to preserve an in-memory runtime URL, fill labelled forms, solve `account-captcha-picture`, submit `account-captcha-input`, and save updated cookies.

## Task 2: Native HH test/form/CAPTCHA transport

- Parse embedded `vacancyTests` from HH's vacancy-response page.
- Generate exact supplied solution IDs or bounded free text and POST HH's native `task_*` payload to `/applicant/vacancy_response/popup`.
- Wrap the real application client so API CAPTCHA is solved and the original request retried, while redirects/forms are handled in the same authenticated browser context.
- Wire the wrapper into `_build_hh_engine`; manual challenge codes remain the final fallback only.

## Task 3: Focused verification and operator docs

- Add small deterministic tests for AI answer selection, native test payload submission, same-session Vision CAPTCHA continuation, and config parsing.
- Run only these tests plus the existing challenge/executor slice and lint changed files; do not rerun the unrelated full browser suite.
- Document the one-time browser login/cookie requirement and the Vision-capable AI settings.
