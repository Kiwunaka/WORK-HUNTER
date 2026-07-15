# HH Autopilot Design

**Date:** 2026-07-15

**Scope:** one native, restart-safe HH automation pipeline inside Work Hunter

## Goal

Build a single production path that continuously performs:

`multi-page search -> hard filters -> ranking -> quotas -> application -> supported screening/form handling -> journal -> retry -> scheduler`

Once `sources.hh.autopilot.enabled` is set to `true`, the scheduler may execute this path without per-run or per-vacancy confirmation. The default remains `false`. Every live mutation must still honor the kill switch, account lease, quotas, configured time window, and current policy immediately before dispatch.

The implementation must use the existing Work Hunter HH transport, storage, scoring, AI, campaign, notification, resume, negotiation, email, CLI, and web boundaries. It must replace the current fragmented orchestration paths with one state machine rather than add another independent application engine.

## Product Decisions

- Autonomy mode: persistent configuration authorization. Enabling the autopilot authorizes future scheduled runs until it is disabled or paused.
- Default daily quota: 50 successful applications per HH account and local calendar day.
- Default per-run quota: 10 successful applications.
- Default schedule: every 60 minutes from 08:00 through 21:00 in the configured timezone.
- Unknown required application data: skip only that vacancy, record the exact missing fields, and continue the run.
- All defaults, filters, weights, quotas, delays, schedules, retry rules, notification channels, and optional operations are configurable and validated before enablement.
- One vacancy is sent from at most one resume per account by default. The best-scoring resume wins. This policy is configurable but cannot bypass HH's own duplicate-application response.
- CAPTCHA is detected and handed to the operator in the authenticated browser context. After the operator resolves it, the original operation is retried automatically. Work Hunter does not implement Vision-based CAPTCHA bypass.
- Profile-grounded screening questions may be completed automatically. Knowledge assessments, unknown task types, and questions that cannot be answered truthfully from stored candidate data become manual challenges; the system does not guess or fabricate.
- Functional behavior is implemented natively. Source code from the reference repository is not copied.

## Selected Architecture

Add a focused `HHAutopilot` orchestrator. It owns run coordination and state transitions but delegates domain work through explicit ports:

| Unit | Responsibility | Dependency boundary |
|---|---|---|
| `HHAutopilot` | Acquire lease, create run, invoke stages, enforce kill switch and quotas | Ports below and `Storage` |
| `HHSearchProvider` | Fetch recommendation or query result pages and normalize vacancies | Existing HH client/session |
| `HHEligibilityPolicy` | Apply deterministic hard filters and return stable reason codes | Profile, preset, employer blacklist |
| `HHVacancyRanker` | Produce deterministic score and optional structured AI decision | Existing scoring and AI backends |
| `HHApplicationExecutor` | Send one idempotent application and classify the transport result | Existing HH API/web transport |
| `HHChallengeHandler` | Handle supported screening/forms or create a manual challenge | Authenticated browser session, profile data |
| `HHRetryPolicy` | Decide terminal versus retryable outcomes and calculate `next_attempt_at` | Typed outcome only |
| `HHAutoMaintenance` | Invoke existing resume update, token refresh, reply, email, and cleanup operations | Existing Work Hunter services |

Each unit receives typed input and returns a typed outcome. It must not update unrelated tables directly. `HHAutopilot` is the only unit that advances queue state; `Storage` performs each transition and its journal event atomically.

The existing manual campaign, CLI, MCP, and UI entry points must call the same search, policy, ranking, execution, and transition services. Legacy methods may remain as compatibility facades, but they cannot maintain a second set of application rules.

## State Machine

The durable item states are:

```text
discovered -> filtered
discovered -> ranked
ranked -> ready
ranked -> skipped
ready -> applying
applying -> applied
applying -> skipped
applying -> retry_wait
applying -> manual_challenge
applying -> dead
retry_wait -> ready
retry_wait -> dead
manual_challenge -> ready
manual_challenge -> skipped
```

`applied`, `skipped`, and `dead` are terminal unless the operator explicitly requeues a `dead` or manually challenged item. A filtered-out item is stored as terminal `skipped` with a stable reason code. No error may leave an item indefinitely in `applying`; lease recovery converts an abandoned `applying` item to `retry_wait` with reason `interrupted`.

Stable outcome families include:

- `applied`
- `duplicate`
- `vacancy_closed`
- `hard_filter:<filter_name>`
- `missing_required_data`
- `manual_screening`
- `manual_assessment`
- `manual_captcha`
- `hh_daily_limit`
- `rate_limited`
- `auth_expired`
- `network_error`
- `server_error`
- `invalid_request`
- `retry_exhausted`
- `interrupted`

## Persistence

Add versioned SQLite migrations and storage methods for:

### `hh_autopilot_runs`

Stores account profile, trigger (`schedule`, `manual`, `retry`, or `canary`), status, immutable configuration hash, start/finish times, counters, and masked top-level error.

### `hh_autopilot_items`

Stores run, account, vacancy, selected resume, query/preset source, state, hard-filter decision, deterministic score, structured AI decision, application attempt count, `next_attempt_at`, last outcome code, challenge ID, and timestamps.

The idempotency key is derived from account, resume, vacancy, and operation type. A separate account-plus-vacancy guard prevents simultaneous or subsequent applications through a second resume when the configured policy is `best_resume_only`.

### `hh_autopilot_events`

Append-only transition journal containing previous state, next state, stable reason code, masked metadata, and timestamp. Every state change and event insert occurs in one transaction.

### `hh_autopilot_leases`

Provides one account-scoped lease with owner token and expiry. Acquisition and renewal are atomic. Expired leases can be recovered; live leases cannot be stolen.

### `hh_autopilot_quotas`

Stores account, local date, successful application count, and update timestamp. Reserving capacity and recording success are transactional. Failed, skipped, and challenged items do not consume success quota. A reservation is released when an attempt does not succeed.

### `hh_autopilot_challenges`

Stores challenge type, related item, sanitized URL, optional local screenshot path, status, expiry, resolution timestamp, and masked metadata. Tokens, cookies, raw credentials, and complete personal form payloads are never stored in the event journal.

Existing `applications`, `hh_application_attempts`, campaign, employer, contact, negotiation, form review, and operation log tables remain authoritative for their current consumers. Successful autopilot work writes the existing records and links them to the new run/item IDs instead of creating a second application history.

## Configuration Contract

`sources.hh.autopilot` has these validated groups:

```json
{
  "enabled": false,
  "paused": false,
  "timezone": "Europe/Moscow",
  "schedule": {
    "days": [1, 2, 3, 4, 5, 6, 7],
    "start": "08:00",
    "end": "21:00",
    "interval_minutes": 60
  },
  "search": {
    "include_recommendations": true,
    "presets": [],
    "per_page": 100,
    "max_pages": 20
  },
  "limits": {
    "daily_success": 50,
    "per_run_success": 10,
    "send_delay_min_seconds": 45,
    "send_delay_max_seconds": 120
  },
  "ranking": {
    "minimum_score": 60,
    "ai_mode": "borderline",
    "ai_failure_policy": "retry"
  },
  "retry": {
    "max_attempts": 4,
    "base_delay_seconds": 60,
    "max_delay_seconds": 3600,
    "jitter_ratio": 0.25
  },
  "application": {
    "resume_policy": "best_resume_only",
    "send_cover_letter": true,
    "screening_mode": "profile_grounded",
    "form_mode": "profile_grounded",
    "captcha_mode": "manual_handoff"
  },
  "maintenance": {
    "refresh_token": true,
    "update_resumes": true,
    "reply_employers": false,
    "send_email_followup": false,
    "cleanup_negotiations": false
  }
}
```

The final schema also exposes configurable hard filters, score weights, salary missing-value policy, included and excluded roles/skills, areas, schedules, employment types, experience levels, employer blacklist behavior, query-to-resume mappings, templates, proxy, browser, notification, reply, follow-up, cleanup, and maintenance schedules.

Validation rejects contradictory schedules, invalid timezones, non-positive page sizes, negative weights, minimum delays greater than maximum delays, empty resume mappings, quotas above configured administrative maxima, unsupported modes, and enabled live automation without a usable HH profile. Invalid configuration cannot enable or start the autopilot.

## Search And Deduplication

For each published resume and mapped preset, `HHSearchProvider` requests pages sequentially. It stops when it reaches `max_pages`, receives fewer than `per_page` vacancies, reaches the HH-reported total, or the account/run search budget is exhausted. Recommendation search follows the same interface.

Vacancies are normalized and persisted before policy evaluation. Duplicates are collapsed by HH vacancy ID across pages, queries, and resumes. Search progress is journaled by preset and page so a failed run can resume without repeating completed pages unnecessarily.

The default supports 20 pages of 100 results. Both values and the run-wide search budget are configurable.

## Hard Filters

Hard filters run before AI and return machine-readable evidence. Supported filters include:

- vacancy closed or archived;
- already applied, active, or permanently skipped;
- vacancy and employer blacklist;
- excluded words in title, description, employer, and extracted skills;
- required words and allowed role families;
- area and relocation policy;
- remote, schedule, and employment type;
- experience range;
- salary floor and configurable unknown-salary behavior;
- language and citizenship constraints represented in the profile;
- configured application capability requirements.

Stop words are hard exclusions when configured as such; they are not merely a score penalty. A filter cannot infer missing candidate facts. Every rejection stores the filter name and sanitized evidence.

## Ranking And Resume Selection

The deterministic ranker returns a normalized `0..100` score with components for role, skills, experience, salary, work format, area, industry, and configured custom signals. Weights are configurable and normalized before use.

AI evaluation is optional and structured. It returns suitability, confidence, explicit evidence from the vacancy/resume text, and rejection reasons. It cannot override a hard filter. Modes are `off`, `borderline`, and `all`. AI failure policy is configurable as `retry`, `deterministic`, or `skip`; the default is `retry` when AI evaluation is required.

The same vacancy can be ranked against multiple resumes. Under `best_resume_only`, the system selects the highest eligible score, breaks ties deterministically, and creates one ready item. This choice is recorded before application dispatch.

## Quotas And Dispatch

The scheduler evaluates the configured local timezone and window. Missed intervals do not create a burst of catch-up runs; at most one run starts when the process resumes. A live account lease prevents concurrent manual, scheduled, retry, or maintenance mutation for the same account.

Before every application mutation, not only at run start, the orchestrator checks:

1. `enabled` and `paused`;
2. kill-switch state;
3. account lease ownership;
4. time window;
5. per-run remaining quota;
6. daily remaining quota;
7. HH-reported limits;
8. item idempotency and current state.

Dispatch uses a configurable randomized delay. A stopped or paused run commits its current state and exits cleanly.

## Application And Challenge Flow

`HHApplicationExecutor` sends a standard application through the supported HH transport and maps API/web results to typed outcomes. It never parses an error string to make retry decisions when a typed transport status is available.

- A normal success records the existing application and attempt rows, increments quota, and advances to `applied`.
- Duplicate, closed, forbidden, invalid, and other permanent responses advance to `skipped` with their stable code.
- Network failures, `429`, and retryable `5xx` outcomes use `HHRetryPolicy`.
- HH account limits stop further sends for the account until the returned or calculated reset time.
- Authentication expiry schedules token refresh once, then retries the original item. Repeated authentication failure becomes a manual account challenge instead of a tight loop.
- Redirects, required screening, forms, assessments, and CAPTCHA enter `HHChallengeHandler`.

Supported screening fields are mapped from explicit candidate profile fields and resume data. Supported forms are filled in the authenticated browser session using the same mapping. Unknown required fields produce `missing_required_data` and skip only the vacancy.

Knowledge assessments and unsupported task types produce `manual_assessment`. CAPTCHA produces `manual_captcha`. The challenge stores enough sanitized context to reopen the same authenticated browser flow. Resolution moves the item back to `ready`, and the executor repeats the original operation idempotently.

Optional recruiter email is a separate post-application action. Its failure does not roll back a successful HH application and has its own idempotency key and retry record. Contact discovery and employer snapshots use the existing storage tables.

## Retry Policy

Retryability is determined by stable outcome code. The default attempts are bounded and persisted. Delay is exponential with bounded jitter:

`min(max_delay, base_delay * 2^(attempt - 1)) * jitter`

`Retry-After` or a typed HH reset timestamp takes precedence when longer. `next_attempt_at` is stored in UTC. A scheduler tick processes due retries before new search results, subject to the same quotas and window. Exhausted retries advance to `dead` with the last masked error. CLI and UI can explicitly requeue `dead` or resolved manual items.

## Authentication And Session Lifecycle

Work Hunter provides browser-assisted login by password or one-time code, cookie import, token refresh, logout, profile selection, and session diagnostics through the existing HH account profile boundary. CAPTCHA during login is manual in the same browser session. Refreshed tokens and cookies are written atomically to the active profile and reused by API and browser transports.

The implementation does not depend on undeclared or decompiled third-party client credentials. Required OAuth client configuration must be user-provided or otherwise legally distributable.

## Reference Feature Parity

The finished system must expose or preserve the following reference behaviors through Work Hunter's native interfaces:

| Behavior | Work Hunter target |
|---|---|
| Multi-page query and recommendation search | Unified search provider and presets |
| Multiple accounts and resumes | Profile-scoped runs, leases, quotas, and mappings |
| AI light/heavy filtering | Configurable structured ranker modes |
| Template and AI cover letters | Existing letter service used by executor |
| Profile-grounded screening/forms | Challenge handler with typed outcomes |
| CAPTCHA continuity | Manual handoff, session persistence, automatic retry |
| Recruiter email | Idempotent post-application action |
| Employer contact/history persistence | Existing employer, contact, application, and event tables |
| Employer chat replies | Existing reply planner/executor scheduled through maintenance policy |
| Resume update/raise | Existing resume operation scheduled per profile |
| Create and clone resumes | Existing native CLI/UI operation preserved |
| Clear negotiations/skipped state | Existing cleanup operations, separately configurable |
| Proxy and session diagnostics | Existing transport status and proxy checks preserved |
| Token refresh | Scheduled and on-demand recovery |
| Raw API diagnostics | Existing guarded API Lab preserved |
| SQLite, masked logs, CLI, UI, Docker | Existing Work Hunter facilities extended for autopilot status/control |

Random test answers, fabricated candidate claims, Vision CAPTCHA solving, undeclared client secrets, and deliberate duplicate spam are not parity targets.

## Scheduled Maintenance

`HHAutoMaintenance` invokes existing operations under the same account lease and policy checks. Each operation has its own enable flag, schedule, limit, idempotency, retry record, and journal result:

- token refresh;
- resume update/raise;
- employer reply planning and sending;
- recruiter email follow-up;
- negotiation cleanup;
- skipped-state cleanup;
- notification delivery.

Application dispatch has priority over non-critical maintenance once due retries are processed. Maintenance failure does not corrupt the application queue.

## CLI And UI Contract

Add native commands and equivalent local API/UI actions for:

- `hh autopilot validate`
- `hh autopilot status`
- `hh autopilot run-now`
- `hh autopilot pause`
- `hh autopilot resume`
- `hh autopilot stop`
- `hh autopilot retry --item-id ...`
- `hh autopilot challenges`
- `hh autopilot resolve-challenge --challenge-id ...`
- `hh autopilot history`

`run-now` does not require per-run confirmation when autopilot is enabled. Enabling autopilot and changing high-risk live settings require the existing literal-boolean safety acknowledgement in UI/API. The kill switch is available from CLI and UI and is checked immediately before each mutation.

Status shows lease owner/expiry, schedule, next run, current run, daily quota, pending/retry/manual/dead counts, last successful application, and last masked failure. History exposes filter and ranking evidence without secrets.

## Error Handling And Recovery

- A failure in one vacancy cannot abort unrelated items unless the account is no longer safe to mutate, the quota is exhausted, the lease is lost, or the kill switch is active.
- An unhandled item exception is converted to a masked `internal_error`, journaled, and retried within the bounded policy.
- Storage transition failure rolls back both state and event.
- Lease loss stops new mutations immediately.
- Process termination leaves enough durable state for the next run to recover.
- Config changes are loaded before each run; kill switch and pause changes are checked during dispatch.
- Notification failure is retried independently and never changes the underlying application outcome.

## Security And Privacy

- Access tokens, refresh tokens, cookies, passwords, OTP values, authorization headers, proxy credentials, SMTP credentials, raw session exports, and full personal form values are masked or excluded from logs and events.
- Browser artifacts and screenshots are stored under the private application root with restrictive local permissions and configurable retention.
- UI remains loopback-only and uses the existing origin, host, content-type, and mutation guards.
- Configuration export masks secrets.
- Database and configuration updates remain process-safe and atomic.

## Test Strategy

Implementation follows test-driven development. Every behavior is first represented by a failing test.

### Unit tests

- allowed and forbidden state transitions;
- lease acquisition, renewal, expiry, and recovery;
- pagination stop rules and deduplication;
- every hard filter and missing-value policy;
- score normalization, AI modes, and resume tie-breaking;
- quota reservation/release and local-day rollover;
- retry classification, backoff, `Retry-After`, and exhaustion;
- configuration validation and masking;
- kill-switch checks before every mutation.

### Contract tests

A local fake HH server represents pagination, recommendations, normal application, duplicate, closed vacancy, redirect, screening, form, CAPTCHA response, authentication expiry, `429`, retryable `5xx`, timeout, and HH daily limit. Tests assert transport calls and durable business outcomes rather than mock call counts alone.

### Browser integration tests

Local HTML fixtures exercise authenticated form filling, unknown required fields, manual CAPTCHA handoff creation, session-cookie persistence, challenge resolution, and automatic continuation. They do not solve a CAPTCHA.

### Restart and concurrency tests

Tests terminate a run after `applying`, expire its lease, restart the service, and assert one eventual application without duplicates. Concurrent runners against one account must produce one lease owner. Different accounts may run independently.

### Compatibility tests

Existing campaign, CLI, MCP, UI, resume, reply, email, cleanup, storage, packaging, and safety suites remain green. Manual entry points must produce the same state and journal records as scheduler entry points.

### Optional live canary

A live canary is opt-in, names one account, resume, and vacancy explicitly, enforces a one-success cap, and records the result. It is never part of the default test suite. Without a configured HH session and a successful canary, the release may claim local contract verification but not proven current-HH end-to-end application compatibility.

## Rollout

1. Run the unified pipeline in shadow mode: real search, filters, ranking, journal, and zero mutations.
2. Run the opt-in one-vacancy canary.
3. Enable one account with per-run success quota `1` and inspect outcomes.
4. Increase to configured per-run and daily quotas.
5. Enable optional maintenance operations independently.

The default remains disabled through migration and upgrade. No existing user becomes autonomous because the software updated.

## Definition Of Done

The work is complete only when:

- one scheduler entry performs multi-page search through durable application outcomes;
- all live application entry points use the same policy, executor, idempotency, quota, retry, and journal boundaries;
- hard filters run before AI and store stable evidence;
- ranking selects the best resume and never exceeds configured limits;
- successful applications survive restart without duplication;
- retryable failures resume after persisted backoff;
- permanent and missing-data outcomes do not retry;
- a CAPTCHA or unsupported assessment creates one manual challenge, leaves other vacancies running, and automatically retries after resolution;
- token refresh, resume update, replies, email, and cleanup can be scheduled per account through the same lease and logging boundary;
- CLI and UI expose validation, status, pause, stop, retry, challenge resolution, quota, and history;
- secrets and personal payloads are absent from normal logs and journal events;
- unit, contract, browser, restart, concurrency, compatibility, lint, type, build, and package smoke gates pass;
- documentation describes actual behavior and does not claim live HH compatibility without a successful opt-in canary.

