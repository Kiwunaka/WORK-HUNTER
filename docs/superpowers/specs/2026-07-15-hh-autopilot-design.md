# HH Autopilot Design

**Date:** 2026-07-15

**Scope:** one native, restart-safe HH automation pipeline inside Work Hunter

## Goal

Build a single production path that continuously performs:

`multi-page search -> hard filters -> ranking -> quotas -> application -> supported screening/form handling -> journal -> retry -> scheduler`

Once the operator enables `sources.hh.autopilot.enabled` through the confirmed enable command, the scheduler may execute this path without per-run or per-vacancy confirmation. The enable command atomically records a scoped durable authorization grant; directly editing the configuration flag does not create a grant. The default remains `false`. Every live mutation must still honor the active grant, kill switch, account lease, quotas, configured time window, and current policy immediately before dispatch.

The implementation must use the existing Work Hunter HH transport, storage, scoring, AI, campaign, notification, resume, CLI, and web boundaries. It must replace the current fragmented application orchestration paths with one state machine rather than add another independent application engine.

This specification is the first delivery slice of the approved parity program. It covers search through durable application/challenge outcomes plus the authentication recovery needed by that path. Independently scheduled resume raising, employer replies, recruiter email, negotiation cleanup, and notification delivery require a follow-up maintenance specification and grant model after the application core is complete. Their existing manual behavior is preserved during this slice.

## Product Decisions

- Autonomy mode: a persistent, account-scoped application grant created by an explicit confirmed enable action. The grant authorizes future scheduled application runs until revoked, disabled, or invalidated by a high-risk configuration change.
- Default daily quota: 50 successful applications per HH account and local calendar day.
- Default per-run quota: 10 successful applications.
- Default schedule: every 60 minutes from 08:00 through 21:00 in the configured timezone.
- Unknown required application data: skip only that vacancy, record the exact missing fields, and continue the run.
- All defaults, filters, weights, quotas, delays, schedules, retry rules, notification channels, and optional operations are configurable and validated before enablement.
- One vacancy is sent from at most one resume per account by default. The best-scoring resume wins. This policy is configurable but cannot bypass HH's own duplicate-application response.
- CAPTCHA is detected and handed to the operator in the authenticated browser context. After the operator resolves it, the original operation is retried automatically. Work Hunter does not implement Vision-based CAPTCHA bypass.
- Profile-grounded screening questions may be completed automatically. Knowledge assessments, unknown task types, and questions that cannot be answered truthfully from stored candidate data become manual challenges; the system does not guess or fabricate.
- Functional behavior is implemented natively. Source code from the reference repository is not copied.
- Enabling applications does not authorize resume mutations, employer replies, recruiter email, cleanup, or any other live operation. Each follow-up operation requires its own scope and opt-in.

## Selected Architecture

Add a focused `HHAutopilot` orchestrator. It owns run coordination and state transitions but delegates domain work through explicit ports:

| Unit | Responsibility | Dependency boundary |
|---|---|---|
| `HHAutopilot` | Validate grant, acquire lease, create/recover run, invoke stages, enforce kill switch and quotas | Ports below and `Storage` |
| `HHSearchProvider` | Fetch recommendation or query result pages and normalize vacancies | Existing HH client/session |
| `HHEligibilityPolicy` | Apply deterministic hard filters and return stable reason codes | Profile, preset, employer blacklist |
| `HHVacancyRanker` | Produce deterministic score and optional structured AI decision | Existing scoring and AI backends |
| `HHApplicationExecutor` | Send one idempotent application and classify the transport result | Existing HH API/web transport |
| `HHChallengeHandler` | Handle supported screening/forms or create a manual challenge | Authenticated browser session, profile data |
| `HHRetryPolicy` | Decide terminal versus retryable outcomes and calculate `next_attempt_at` | Typed outcome only |
| `HHApplicationReconciler` | Resolve ambiguous or duplicate remote outcomes against HH negotiations/history | Read-only HH negotiation transport |
| `HHAutopilotAuthorizer` | Create, validate, scope, and revoke durable application grants | Existing safety guard and `Storage` |

Each unit receives typed input and returns a typed outcome. It must not update unrelated tables directly. `HHAutopilot` is the only unit that requests queue transitions; `Storage` validates a compare-and-swap item version plus the current lease fencing token and performs each transition and journal event atomically.

The existing manual campaign, CLI, MCP, and UI entry points must call the same search, policy, ranking, execution, and transition services. Legacy methods may remain as compatibility facades, but they cannot maintain a second set of application rules.

## State Machine

The canonical durable item states and transitions are:

```text
discovered -> eligible
discovered -> skipped
eligible -> ranked
eligible -> retry_wait
ranked -> ready
ranked -> skipped
ranked -> retry_wait
ready -> applying
applying -> applied
applying -> skipped
applying -> retry_wait
applying -> manual_challenge
applying -> reconciling
applying -> dead
reconciling -> applied
reconciling -> skipped
reconciling -> retry_wait
reconciling -> manual_challenge
reconciling -> dead
retry_wait -> ready
retry_wait -> eligible
retry_wait -> reconciling
retry_wait -> dead
manual_challenge -> ready
manual_challenge -> skipped
```

`eligible` means every hard filter passed. `applied`, `skipped`, and `dead` are terminal unless the operator explicitly requeues a `dead` item. A filtered-out item moves directly from `discovered` to terminal `skipped` with a stable hard-filter reason. No error may leave an item indefinitely in `applying`; lease recovery moves an abandoned `applying` item to `reconciling`, never directly to a new dispatch, because the remote POST may already have succeeded.

Stable outcome families include:

- `applied`
- `duplicate`
- `ambiguous_remote_result`
- `vacancy_closed`
- `forbidden`
- `parse_error`
- `hard_filter:<filter_name>`
- `missing_required_data`
- `manual_assessment`
- `manual_captcha`
- `hh_daily_limit`
- `rate_limited`
- `auth_expired`
- `manual_auth`
- `network_error`
- `server_error`
- `internal_error`
- `invalid_request`
- `retry_exhausted`
- `interrupted`

## Persistence

Add versioned SQLite migrations and storage methods for:

### `hh_autopilot_runs`

Stores account profile, trigger (`schedule`, `manual`, `retry`, `recovery`, or `canary`), status, grant ID, immutable configuration hash, lease fencing token, start/finish times, counters, and masked top-level error. Run states are `created`, `running`, `stop_requested`, `completed`, `failed`, `interrupted`, and `cancelled`. A recovered process creates a new `recovery` run; it does not impersonate the dead process.

### `hh_autopilot_items`

Stores `origin_run_id`, `last_run_id`, account, vacancy, selected resume, query/preset source, state, `retry_stage` (`eligibility`, `application`, or `reconciliation`), compare-and-swap version, hard-filter decision, deterministic score, structured AI decision, application attempt count, `next_attempt_at`, last outcome code, active attempt ID, challenge ID, and timestamps. Due retries can therefore be processed by a later run without changing their origin and return to `eligible`, `ready`, or `reconciling` without skipping the failed stage.

The local idempotency key is derived from account, resume, vacancy, and operation type. It prevents duplicate local dispatch records but does not claim to make the remote HH POST idempotent. A separate account-plus-vacancy guard prevents simultaneous or subsequent applications through a second resume when the configured policy is `best_resume_only`.

### `hh_autopilot_events`

Append-only transition journal containing previous state, next state, stable reason code, masked metadata, and timestamp. Every state change and event insert occurs in one transaction.

### `hh_autopilot_leases`

Provides one account-scoped lease with owner token, monotonically increasing fencing token, and expiry. Acquisition and renewal are atomic. Every queue/quota/application write supplies the fencing token and is rejected after a newer owner acquires the account.

The default lease TTL is 120 seconds, application HTTP timeout is at most 30 seconds, and renewal safety margin is 45 seconds; all are configurable subject to `lease_ttl >= request_timeout + safety_margin`. The owner renews immediately before dispatch. A replacement owner must reconcile every stale `applying` attempt before dispatching new work. These invariants prevent two owners from intentionally overlapping requests and ensure a late response from an expired owner cannot commit local state.

### `hh_autopilot_quota_reservations`

Stores one row per dispatch attempt: reservation ID, attempt ID, account, timezone, local date, state (`reserved`, `consumed`, or `released`), fencing token, created time, and resolved time. A `BEGIN IMMEDIATE` transaction counts `reserved + consumed` for the account/date and inserts a unique reservation only when both daily and per-run capacity remain. Compare-and-swap state changes prevent double consume/release.

A reservation is never expired merely by wall clock while its attempt is `applying` or `reconciling`. Recovery resolves the remote outcome first, then consumes or releases it. Failed, skipped, and challenged items release their reservation; a later retry reserves again. Timezone is a high-risk setting and cannot change while a grant or unresolved reservation is active. Local-day rollover creates reservations for the new date but does not reassign old rows.

### `hh_autopilot_challenges`

Stores scope (`item` or `account`), type, account, optional related item, sanitized URL, optional local screenshot path, status, expiry, resolution timestamp, resolution actor, and masked metadata. Status transitions are `open -> in_progress -> resolved`, `open|in_progress -> dismissed`, and `open|in_progress -> expired`. An account-scoped `manual_auth` challenge pauses dispatch for that account only. Resolving it returns due account items to reconciliation or readiness. An expired/dismissed item challenge moves its item to `skipped` with `challenge_expired` or `challenge_dismissed`; explicit requeue remains available.

Default item-challenge expiry is 24 hours and account authentication challenges do not auto-expire. Both policies are configurable.

### `hh_autopilot_search_checkpoints`

Stores run, account, resume, preset/query key, next page, reported total, unique vacancy count, and status (`pending`, `running`, `complete`, or `failed`). Updating a checkpoint and persisting the normalized page results occurs in one transaction. Recovery may resume an interrupted search from `next_page`; a new scheduled run starts a new search cycle.

### `hh_autopilot_grants`

Stores account, scope (`applications` in this specification), policy hash, creation/revocation timestamps, grant version, and masked actor/source. `HHAutopilotAuthorizer` creates the row only from the existing literal-boolean confirmed enable boundary and passes a typed `LiveAuthorization(kind="autopilot", grant_id, scope, account_id, run_id, fencing_token)` to the service mutation guard.

The guard validates the active grant, account, scope, policy hash, configuration flag, pause state, and kill switch immediately before dispatch. Direct configuration edits cannot create or widen a grant. Disabling or invoking the kill switch revokes the grant atomically. Changing account/resume mappings, timezone, schedule window, quotas, send delays, application modes, proxy, browser transport, or administrative maxima invalidates the policy hash and requires a new confirmed enable action.

Existing `applications`, `hh_application_attempts`, campaign, employer, contact, negotiation, form review, and operation log tables remain authoritative for their current consumers. Migrations add nullable `autopilot_run_id`, `autopilot_item_id`, and `autopilot_attempt_id` links where applicable.

Finalizing a successful or reconciled application is one SQLite transaction that verifies item version and fencing token, upserts the existing application row, writes the existing attempt outcome, consumes the quota reservation, advances the item to `applied`, inserts its event, updates the account-vacancy guard, and increments cached run counters. Any failure rolls back the whole transaction. Non-success transitions similarly update attempt, reservation, item, event, and challenge atomically.

## Configuration Contract

`sources.hh.autopilot` has these validated groups:

```json
{
  "enabled": false,
  "paused": false,
  "timezone": "Europe/Moscow",
  "accounts": [
    {
      "profile_id": "default",
      "candidate_profile_id": "default",
      "resume_queries": [
        {"resume_id": "published:*", "preset_names": []}
      ]
    }
  ],
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
    "max_pages": 20,
    "max_results_per_run": 2000
  },
  "filters": {
    "excluded_keywords": [],
    "required_keywords": [],
    "allowed_role_families": [],
    "areas": [],
    "remote": "any",
    "schedules": [],
    "employment_types": [],
    "experience_levels": [],
    "languages": [],
    "citizenships": [],
    "required_application_capabilities": [],
    "minimum_salary": 0,
    "salary_currency": "RUR",
    "unknown_salary": "allow",
    "use_employer_blacklist": true
  },
  "limits": {
    "daily_success": 50,
    "per_run_success": 10,
    "administrative_max_daily_success": 200,
    "send_delay_min_seconds": 45,
    "send_delay_max_seconds": 120
  },
  "ranking": {
    "minimum_score": 60,
    "ai_mode": "borderline",
    "borderline_low": 50,
    "borderline_high": 70,
    "minimum_ai_confidence": 0.7,
    "ai_detail": "light",
    "ai_failure_policy": "retry",
    "weights": {
      "role": 0.30,
      "skills": 0.30,
      "experience": 0.15,
      "salary": 0.10,
      "work_format": 0.10,
      "area": 0.05,
      "industry": 0.00,
      "custom": {}
    }
  },
  "retry": {
    "max_attempts": 4,
    "base_delay_seconds": 60,
    "max_delay_seconds": 3600,
    "jitter_ratio": 0.25,
    "auth_recovery_attempts": 1,
    "reconciliation_delay_seconds": 30,
    "reconciliation_checks": 3
  },
  "lease": {
    "ttl_seconds": 120,
    "request_timeout_seconds": 30,
    "renewal_margin_seconds": 45
  },
  "application": {
    "resume_policy": "best_resume_only",
    "send_cover_letter": true,
    "cover_letter_mode": "template",
    "screening_mode": "profile_grounded",
    "form_mode": "profile_grounded",
    "captcha_mode": "manual_handoff",
    "challenge_expiry_hours": 24
  },
  "browser": {
    "headless": true,
    "navigation_timeout_seconds": 30
  },
  "notifications": {
    "challenge": true,
    "run_failure": true
  },
  "retention": {
    "challenge_artifact_days": 7,
    "event_days": 180
  }
}
```

`profile_id` identifies the HH authentication account. `candidate_profile_id` identifies the Work Hunter candidate facts/scoring profile used for truthful answers and ranking. `preset_names` reference existing HH campaign presets, which contain the actual HH query, specialization, area, schedule, employment, experience, salary, and other supported search parameters. `published:*` expands to every published resume in the account; explicit resume IDs are also accepted. The same resume cannot appear twice for one preset after expansion.

Enums are fixed as follows: `remote` is `any|only|exclude`; `unknown_salary` is `allow|reject`; `ai_mode` is `off|borderline|all`; `ai_detail` is `light|heavy`; `ai_failure_policy` is `retry|deterministic|skip`; `resume_policy` is `best_resume_only|per_resume`; `cover_letter_mode` is `none|template|ai`; screening and form modes are `off|profile_grounded`; CAPTCHA mode is only `manual_handoff` in this specification.

All numeric values have explicit bounds: page size `1..100`, pages `1..100`, run search results `1..10000`, daily and per-run success `1..administrative_max_daily_success`, administrative maximum `1..200`, delays `0..3600`, retry and reconciliation checks `1..20`, retry/reconciliation delays `1..86400`, jitter ratio `0..1`, lease/request/navigation times `1..600`, challenge expiry `1..720` hours, and retention `1..3650` days. Weight values are non-negative finite numbers with a positive sum and are normalized at runtime.

Validation also requires valid IANA timezone, unique weekday integers `1..7`, valid `HH:MM` values with `start <= end`, positive interval, `per_run_success <= daily_success`, minimum send delay not above maximum, `borderline_low <= minimum_score <= borderline_high`, AI confidence in `0..1`, lease TTL at least request timeout plus renewal margin, unique account mappings, existing HH and candidate profiles/presets, at least one resolvable published resume, supported modes, a usable application transport, and an active application grant before a live run. Unknown configuration keys are rejected. Invalid configuration cannot enable or start the autopilot.

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

AI evaluation is optional and structured. It returns suitability, confidence, explicit evidence from the vacancy/resume text, and rejection reasons. It cannot override a hard filter and does not mutate the deterministic score.

- `off`: score at or above `minimum_score` becomes ready; lower score is skipped.
- `borderline`: score below `borderline_low` is skipped, score above `borderline_high` becomes ready, and scores inside the inclusive interval require AI. `suitable=true` with confidence at or above `minimum_ai_confidence` becomes ready; `suitable=false` at that confidence is skipped; lower confidence follows `ai_failure_policy`.
- `all`: every hard-filter-eligible vacancy requires the same confident AI decision; deterministic score only orders accepted vacancies.

On required AI failure, `retry` moves the item to `retry_wait`, `deterministic` applies the `minimum_score` threshold, and `skip` terminates with `ai_unavailable`.

The same vacancy can be ranked against multiple resumes. Under `best_resume_only`, candidates are ordered by deterministic score descending, AI confidence descending when present, vacancy publication time descending, vacancy ID ascending, then resume ID ascending. The first eligible resume creates one ready item. This choice is recorded before application dispatch.

## Quotas And Dispatch

The scheduler evaluates the configured local timezone and window. Missed intervals do not create a burst of catch-up runs; at most one run starts when the process resumes. A live account lease prevents concurrent manual, scheduled, retry, or application recovery mutation for the same account.

Before every application mutation, not only at run start, the orchestrator checks:

1. active application grant, matching account/scope/policy hash, and typed authorization context;
2. `enabled` and `paused`;
3. global and account kill-switch state;
4. account lease ownership and current fencing token;
5. time window;
6. per-run remaining quota;
7. daily remaining quota or an existing reservation for this attempt;
8. HH-reported limits;
9. item compare-and-swap version, idempotency guard, and current state.

Dispatch uses a configurable randomized delay. A stopped or paused run commits its current state and exits cleanly.

## Application And Challenge Flow

`HHApplicationExecutor` sends a standard application through the supported HH transport and maps API/web results to typed outcomes. It never parses an error string to make retry decisions when a typed transport status is available.

- A normal success atomically records the existing application and attempt rows, consumes its quota reservation, and advances to `applied`.
- Closed, forbidden, invalid, and other permanent responses release the reservation and advance to `skipped` with their stable code.
- A duplicate response enters reconciliation; it is not treated as an ordinary skip.
- Network failures, `429`, and retryable `5xx` outcomes use `HHRetryPolicy`.
- HH account limits stop further sends for the account until the returned or calculated reset time.
- Authentication expiry schedules token refresh once, then retries the original item. Repeated authentication failure becomes a manual account challenge instead of a tight loop.
- Redirects, required screening, forms, assessments, and CAPTCHA enter `HHChallengeHandler`.

Supported screening fields are mapped from explicit candidate profile fields and resume data. Supported forms are filled in the authenticated browser session using the same mapping. Unknown required fields produce `missing_required_data` and skip only the vacancy.

Knowledge assessments and unsupported task types produce `manual_assessment`. CAPTCHA produces `manual_captcha`. The challenge stores enough sanitized context to reopen the same authenticated browser flow. Resolution moves the item back to `ready`, and the executor repeats the original operation through the local idempotency guard.

Contact discovery and employer snapshots use the existing storage tables. Scheduled recruiter email is outside this core specification and remains an existing separately confirmed operation until the maintenance slice defines its grant and durable scheduler.

### Ambiguous remote result reconciliation

Before every remote POST, the orchestrator creates an attempt and quota reservation, transitions the item to `applying`, and commits those records. If the process loses the response or crashes before finalization, recovery moves the item to `reconciling`.

`HHApplicationReconciler` queries read-only HH negotiation/application history for the same account, vacancy, and resume:

- a matching negotiation finalizes the existing attempt as `applied` and consumes its original reservation;
- a confirmed absence after the configured consistency delay releases the reservation and permits a bounded new attempt;
- a duplicate response followed by a matching negotiation finalizes as `applied` rather than `skipped`;
- an outcome still ambiguous after the reconciliation limit creates an item challenge `ambiguous_application` and does not issue another POST automatically.

The default consistency delay is 30 seconds and reconciliation limit is 3 read-only checks; both are configurable within the retry bounds. This is reconciliation, not remote idempotency.

## Retry Policy

Retryability is determined by the following complete outcome families:

| Outcome | Action |
|---|---|
| `network_error`, `server_error`, `internal_error`, `parse_error` | `retry_wait`, bounded by `max_attempts` |
| `rate_limited`, `hh_daily_limit` | `retry_wait` at `Retry-After`/reset, without a tight loop |
| `auth_expired` | one token/session recovery; then `manual_auth` account challenge |
| `duplicate`, `ambiguous_remote_result`, abandoned `applying` | `reconciling` |
| `manual_captcha`, `manual_assessment` | `manual_challenge` |
| `vacancy_closed`, `forbidden`, `invalid_request`, hard filter, missing data | terminal `skipped` |
| exhausted retry or reconciliation | `dead` or `ambiguous_application` challenge as specified above |

`max_attempts` counts actual application POST dispatches, including requests whose response was lost. Read-only reconciliation checks and token refresh do not increment it. Authentication recovery is separately bounded by `auth_recovery_attempts`.

The default attempts are bounded and persisted. Delay is exponential with bounded jitter:

`min(max_delay, base_delay * 2^(attempt - 1)) * factor`, where `factor` is sampled uniformly from `1 - jitter_ratio` through `1 + jitter_ratio`.

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
| Recruiter email | Existing confirmed operation preserved; durable scheduling is parity slice 2 |
| Employer contact/history persistence | Existing employer, contact, application, and event tables |
| Employer chat replies | Existing confirmed operation preserved; durable scheduling is parity slice 2 |
| Resume update/raise | Existing confirmed operation preserved; durable scheduling is parity slice 2 |
| Create and clone resumes | Existing native CLI/UI operation preserved |
| Clear negotiations/skipped state | Existing confirmed operations preserved; durable scheduling is parity slice 2 |
| Proxy and session diagnostics | Existing transport status and proxy checks preserved |
| Token refresh | On-demand application recovery in this slice; independent schedule is parity slice 2 |
| Raw API diagnostics | Existing guarded API Lab preserved |
| SQLite, masked logs, CLI, UI, Docker | Existing Work Hunter facilities extended for autopilot status/control |

Random test answers, fabricated candidate claims, Vision CAPTCHA solving, undeclared client secrets, and deliberate duplicate spam are not parity targets.

## Parity Program Sequencing

This specification produces one implementation plan for the application core. The remaining approved parity work is a second independently reviewed specification after the core interfaces exist:

1. account-scoped token refresh schedule;
2. resume update/raise schedule;
3. employer reply planning and sending;
4. recruiter email follow-up;
5. negotiation and skipped-state cleanup;
6. notification delivery retry.

Slice 2 must use the same account lease primitive but separate durable grants for `resume_update`, `employer_reply`, `recruiter_email`, and `cleanup`. Enabling `applications` cannot authorize any of them. Splitting the plan changes delivery order, not the approved parity goal.

## Compatibility And Safety Guard Mapping

The service mutation guard accepts exactly two typed authorization forms:

- `LiteralConfirmation`, created for one existing manual CLI/UI operation from the current literal JSON boolean `confirm=true` boundary;
- `LiveAuthorization`, created only by `HHAutopilotAuthorizer` from an active durable application grant.

Manual campaign confirmation remains available, but its items pass through the same executor, reservation, reconciliation, application record, and journal code. It does not create a durable grant. The scheduler can use only `LiveAuthorization`. Existing plan/dry-run calls remain read-only.

MCP may inspect configuration, status, plans, history, and challenges but cannot enable or disable the autopilot, clear the kill switch, create a grant, or initiate a live run. The current agent `real_apply_blocked` rule remains for arbitrary agent calls; the scheduler is not treated as an agent call.

The legacy `allow_broad_apply` boolean is deprecated and cannot authorize a mutation. Compatibility reads may display it, but live permission is derived solely from the typed authorization forms above. The application grant includes the narrowly required session/token refresh recovery scope; it does not include resume update, reply, email, cleanup, raw API mutation, or account-profile mutation.

## CLI And UI Contract

Add native commands and equivalent local API/UI actions for:

- `hh autopilot validate`
- `hh autopilot enable --confirm`
- `hh autopilot disable --confirm`
- `hh autopilot status`
- `hh autopilot run-now`
- `hh autopilot pause`
- `hh autopilot resume`
- `hh autopilot stop --run-id ...`
- `hh autopilot kill-switch --confirm`
- `hh autopilot clear-kill-switch --confirm`
- `hh autopilot retry --item-id ...`
- `hh autopilot challenges`
- `hh autopilot resolve-challenge --challenge-id ...`
- `hh autopilot history`

`enable` validates configuration, records the application grant, and sets `enabled=true` atomically. `disable` revokes the grant, sets `enabled=false`, and requests the current run to stop after its in-flight network call. `pause` preserves the grant and configuration but prevents new runs and new dispatches; `resume` clears that state. `stop` affects only the named current run, so the next scheduled interval remains eligible. The account/global kill switch revokes the grant and blocks every new mutation immediately; clearing it does not recreate the revoked grant, so the operator must enable again.

`run-now` does not require per-run confirmation when a valid application grant exists. Enabling, disabling, killing, clearing the kill switch, and reauthorizing after a high-risk setting change require the existing literal-boolean safety acknowledgement in CLI/UI/API. Direct configuration edits are visible to validation but cannot create authorization.

Status shows lease owner/expiry, schedule, next run, current run, daily quota, pending/retry/manual/dead counts, last successful application, and last masked failure. History exposes filter and ranking evidence without secrets.

## Error Handling And Recovery

- A failure in one vacancy cannot abort unrelated items unless the account is no longer safe to mutate, the quota is exhausted, the lease is lost, or the kill switch is active.
- An unhandled item exception is converted to a masked `internal_error`, journaled, and retried within the bounded policy.
- Storage transition failure rolls back item, attempt, reservation, application, guard, counter, challenge, and event changes together.
- Lease loss stops new mutations immediately; a late response from the old owner is reconciled by the new fenced owner.
- Process termination leaves enough durable state for the next run to recover.
- Config changes are loaded before each run. A high-risk policy-hash change invalidates authorization; kill switch and pause changes are checked during dispatch.
- After a policy-hash change, nonterminal items that never reached `applying` return to `discovered` for full re-evaluation under the new policy. Items in `applying` or `reconciling` are reconciled before any reset. Retry items with a previous POST return through reconciliation first; other retry items return to their recorded `retry_stage`. Manual challenges remain visible but cannot resume dispatch until a matching grant is created.
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
- grant creation, scope validation, policy-hash invalidation, revocation, and direct-config-edit rejection;
- lease acquisition, renewal, fencing, expiry, takeover, and recovery;
- pagination stop rules and deduplication;
- search checkpoint commit and resume;
- every hard filter and missing-value policy;
- score normalization, AI modes, and resume tie-breaking;
- quota reservation/consume/release, compare-and-swap races, abandoned reservation recovery, and local-day rollover;
- retry classification, backoff, `Retry-After`, and exhaustion;
- configuration validation and masking;
- kill-switch checks before every mutation.

### Contract tests

A local fake HH server represents pagination, recommendations, normal application, duplicate, closed vacancy, redirect, screening, form, CAPTCHA response, authentication expiry, `429`, retryable `5xx`, timeout, and HH daily limit. Tests assert transport calls and durable business outcomes rather than mock call counts alone.

The fake server can accept a POST and close the connection before returning its response. The test must prove that recovery reconciles the remote negotiation, consumes exactly one reservation, creates exactly one authoritative application record, and never issues an unsafe second POST. A duplicate response with a matching negotiation must produce `applied`, not `skipped`.

### Browser integration tests

Local HTML fixtures exercise authenticated form filling, unknown required fields, manual CAPTCHA handoff creation, session-cookie persistence, challenge resolution, and automatic continuation. They do not solve a CAPTCHA.

### Restart and concurrency tests

Tests terminate a run after `applying`, expire its lease, restart the service, and assert one reconciled application without duplicates. A stale fenced owner cannot commit after takeover. Concurrent runners against one account must produce one lease owner; the replacement reconciles stale in-flight attempts before dispatch. Different accounts may run independently. Additional sequences cover process death after reservation, policy change with queued items, pause/disable/kill during a run, challenge expiry, and attempted timezone change with an active grant or unresolved reservation.

### Compatibility tests

Existing campaign, CLI, MCP, UI, resume, reply, email, cleanup, storage, packaging, and safety suites remain green. Manual entry points must produce the same state and journal records as scheduler entry points.

### Optional live canary

A live canary is opt-in, names one account, resume, and vacancy explicitly, enforces a one-success cap, and records the result. It is never part of the default test suite. Without a configured HH session and a successful canary, the release may claim local contract verification but not proven current-HH end-to-end application compatibility.

## Rollout

1. Run the unified pipeline in shadow mode: real search, filters, ranking, journal, and zero mutations.
2. Run the opt-in one-vacancy canary.
3. Enable one account with per-run success quota `1` and inspect outcomes.
4. Increase to configured per-run and daily quotas.
5. Begin the separately specified parity-maintenance slice only after the core acceptance gates pass.

The default remains disabled through migration and upgrade. No existing user becomes autonomous because the software updated.

## Definition Of Done

The work is complete only when:

- one scheduler entry performs multi-page search through durable application outcomes;
- all live application entry points use the same policy, executor, idempotency, quota, retry, and journal boundaries;
- hard filters run before AI and store stable evidence;
- ranking selects the best resume and never exceeds configured limits;
- successful applications survive restart without duplication;
- ambiguous remote outcomes reconcile to one authoritative application or one manual ambiguity challenge;
- fencing prevents a stale runner from committing after lease takeover;
- quota reservations cannot leak or overshoot the configured daily/per-run limit after failure or restart;
- retryable failures resume after persisted backoff;
- permanent and missing-data outcomes do not retry;
- a CAPTCHA or unsupported assessment creates one manual challenge, leaves other vacancies running, and automatically retries after resolution;
- application authorization is durable, account/scope/policy-bound, immediately revocable, and cannot be created by editing configuration directly;
- CLI and UI expose enable, disable, validation, status, pause, stop, kill switch, retry, challenge resolution, quota, and history;
- secrets and personal payloads are absent from normal logs and journal events;
- unit, contract, browser, restart, concurrency, compatibility, lint, type, build, and package smoke gates pass;
- documentation describes actual behavior and does not claim live HH compatibility without a successful opt-in canary.
