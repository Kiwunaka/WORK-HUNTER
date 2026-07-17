# HH Autopilot Design

**Date:** 2026-07-15

**Scope:** one native, restart-safe HH automation pipeline inside Work Hunter

## Goal

Build a single production path that continuously performs:

`multi-page search -> hard filters -> ranking -> quotas -> application -> test/form/CAPTCHA handling -> journal -> retry -> scheduler`

Once the operator enables an entry in `sources.hh.autopilot.accounts[]` through the confirmed enable command, the scheduler may execute this path for that account without per-run or per-vacancy confirmation. The enable command atomically records a scoped durable authorization grant; directly editing the account flag does not create a grant. Every account defaults to `enabled=false`. Every autonomous live mutation must honor the active grant, kill switch, account lease, quotas, configured time window, and current policy immediately before dispatch; one-shot manual mutations follow the separate safety matrix below.

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
- Application CAPTCHA follows the reference tool's flow: capture the HH CAPTCHA element, recognize it through a configurable OpenAI-compatible Vision model, submit it in the same authenticated browser session, persist refreshed cookies, and continue the original application. `vision_then_manual` falls back to an operator handoff only after bounded automatic attempts fail. Login OTP remains an operator action, matching the reference flow.
- Profile-grounded screening answers are preferred. With `screening_mode=ai`, vacancy tests and knowledge questions are answered by the configured model and submitted through HH's native vacancy-response form; there is no random/middle-option fallback. Unsupported or failed flows become typed per-vacancy outcomes and do not stop the run.
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
| `HHApplicationExecutor` | Send one locally guarded application attempt and classify delivery certainty/result | Existing HH API/web transport |
| `HHChallengeHandler` | Resolve tests/forms/Vision CAPTCHA or create a manual fallback challenge | Authenticated browser session, profile/resume data, AI backend |
| `HHRetryPolicy` | Decide terminal versus retryable outcomes and calculate `next_attempt_at` | Typed outcome only |
| `HHApplicationReconciler` | Resolve ambiguous or duplicate remote outcomes against HH negotiations/history | Read-only HH negotiation transport |
| `HHAutopilotAuthorizer` | Create, validate, scope, and revoke durable application grants | Existing safety guard and `Storage` |
| `HHRecoverySweep` | Recover stale applying/reconciling attempts without authorizing a new application | Attempt provenance, lease, reconciler, `Storage` |

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
manual_challenge -> reconciling
manual_challenge -> applied
manual_challenge -> skipped
manual_challenge -> dead
```

`eligible` means every hard filter passed. `applied`, `skipped`, and `dead` are terminal unless the operator explicitly requeues a `dead` item. A filtered-out item moves directly from `discovered` to terminal `skipped` with a stable hard-filter reason. No error may leave an item indefinitely in `applying`; lease recovery moves an abandoned `applying` item to `reconciling`, never directly to a new dispatch, because the remote POST may already have succeeded.

Stable outcome families include:

- `applied`
- `duplicate`
- `duplicate_external`
- `external_applied`
- `ambiguous_remote_result`
- `ambiguous_application`
- `unresolved_ambiguity`
- `vacancy_closed`
- `forbidden`
- `hard_filter:<filter_name>`
- `missing_required_data`
- `screening_disabled`
- `form_disabled`
- `ai_unavailable`
- `manual_assessment`
- `manual_captcha`
- `hh_daily_limit`
- `rate_limited`
- `auth_expired`
- `manual_auth`
- `challenge_expired`
- `challenge_dismissed`
- `pre_dispatch_network_error`
- `post_dispatch_network_error`
- `server_error`
- `read_parse_error`
- `post_dispatch_parse_error`
- `internal_error`
- `invalid_request`
- `retry_exhausted`
- `interrupted`
- `authorization_state_mismatch`

## Persistence

Add versioned SQLite migrations and storage methods for:

### `hh_autopilot_runs`

Stores account profile, trigger (`schedule`, `manual`, `shadow`, `retry`, `recovery`, or `canary`), status, grant ID, immutable configuration hash, lease fencing token, start/finish times, counters, and masked top-level error. Run states are `created`, `running`, `stop_requested`, `completed`, `failed`, `interrupted`, and `cancelled`. A recovered process creates a new `recovery` run; it does not impersonate the dead process.

### `hh_autopilot_items`

Stores `origin_run_id`, `last_run_id`, account, vacancy, selected resume, query/preset source, state, `retry_stage` (`eligibility`, `application`, or `reconciliation`), compare-and-swap version, hard-filter decision, deterministic score, structured AI decision, application attempt count, `next_attempt_at`, last outcome code, active attempt ID, challenge ID, and timestamps. Due retries can therefore be processed by a later run without changing their origin and return to `eligible`, `ready`, or `reconciling` without skipping the failed stage.

The local idempotency key is derived from account, resume, vacancy, and operation type. It prevents duplicate local dispatch records but does not claim to make the remote HH POST idempotent. A separate account-plus-vacancy guard prevents simultaneous or subsequent applications through a second resume when the configured policy is `best_resume_only`.

### `hh_autopilot_events`

Append-only transition journal containing previous state, next state, stable reason code, masked metadata, and timestamp. Every state change and event insert occurs in one transaction.

### `hh_autopilot_leases`

Provides one account-scoped lease with owner token, monotonically increasing fencing token, and expiry. Acquisition and renewal are atomic. Every queue/quota/application write supplies the fencing token and is rejected after a newer owner acquires the account.

The default lease TTL is 120 seconds, application HTTP timeout is at most 30 seconds, and renewal safety margin is 45 seconds; all are configurable subject to `lease_ttl >= request_timeout + safety_margin`. The owner renews immediately before dispatch. A replacement owner must reconcile every stale `applying` attempt before dispatching new work. These invariants prevent two owners from intentionally overlapping requests and ensure a late response from an expired owner cannot commit local state.

### `hh_autopilot_quota_reservations`

Stores one row per dispatch attempt or synchronized external application: reservation ID, optional attempt ID, source (`dispatch` or `external_sync`), optional unique remote negotiation ID, account, timezone, local date, state (`reserved`, `held`, `consumed`, or `released`), fencing token, created time, and resolved time. A `BEGIN IMMEDIATE` transaction counts `reserved + held + consumed` for the account/date and inserts a unique dispatch reservation only when both daily and per-run capacity remain. Unique remote negotiation ID and compare-and-swap state changes prevent double external count or double hold/consume/release.

A reservation is never expired merely by wall clock while its attempt is `applying`, `reconciling`, or challenged as `ambiguous_application`. Recovery resolves the remote outcome first, then consumes or releases it. Definite failures, skips, CAPTCHA, assessment, form, and definite pre-dispatch authentication challenges release their reservation; a later dispatch reserves again. An authentication challenge encountered while reconciling a possibly-sent request retains the reservation as `held` until authentication recovery allows remote reconciliation. An ambiguous application also changes the reservation to `held`, so it conservatively consumes capacity until resolved. Timezone is a high-risk setting and cannot change while a grant or unresolved reservation is active. Local-day rollover creates reservations for the new date but does not reassign old rows.

### `hh_autopilot_challenges`

Stores scope (`item` or `account`), type, account, optional related item, sanitized URL, optional local screenshot path, status, expiry, resolution timestamp, resolution actor, resolution action, and masked metadata. General status transitions are `open -> in_progress -> resolved`, `open|in_progress -> dismissed`, and `open|in_progress -> expired`. For `ambiguous_application` only, late classification additionally permits `dismissed|expired -> resolved` with one of the explicit applied/not-applied resolution actions below.

- `manual_captcha` and `manual_assessment`: successful completion returns the item to `ready`; dismissal/expiry moves it to `skipped`.
- `manual_auth`: account-scoped, pauses dispatch for that account only, and does not auto-expire. If raised by a definite pre-dispatch/401 rejection, the reservation is released and resolution returns the item to its stored retry stage. If raised while reconciling a possibly-sent attempt, the reservation is held and resolution returns the item to `reconciling` with that same reservation.
- `ambiguous_application`: retains a `held` quota reservation. Resolution actions are `confirmed_applied` (atomically consume and move to `applied`), `confirmed_not_applied_retry` (release and move to `ready`), `confirmed_not_applied_skip` (release and move to `skipped`), or `retry_reconciliation` (retain and move to `reconciling`). Dismissal/expiry moves the item to `dead` but retains the hold until an explicit applied/not-applied resolution; it never silently returns to `ready`. Generic dead-item retry/requeue rejects such an item with `unresolved_ambiguity` until the challenge is legally resolved.

Default item-challenge expiry is 24 hours and account authentication challenges do not auto-expire. Both policies are configurable.

### `hh_autopilot_search_cycles` and `hh_autopilot_search_checkpoints`

A search cycle stores account, configuration hash, origin run, current owner run, claim version, fencing token, and status (`running`, `complete`, `failed`, `interrupted`, or `superseded`). Legal terminal transitions include `running|interrupted -> superseded` on policy-hash mismatch. Checkpoints reference the cycle and store resume, preset/query key, next page, reported total, unique vacancy count, and status (`pending`, `running`, `complete`, or `failed`). Updating a checkpoint and persisting normalized page results occurs in one transaction.

When a matching active grant still exists, an authorized `recovery` run may compare-and-swap claim an interrupted search cycle with its new run ID, claim version, and fencing token. It never rewrites `origin_run_id`. Only the current claimed run may advance checkpoints. Claim requires the cycle configuration hash to equal the current authorized policy hash. On mismatch, the old cycle is marked `superseded` and its never-dispatched candidate items reset for policy re-evaluation; obsolete pagination is never resumed. The next authorized live run (or isolated shadow run) starts a new cycle at page zero. The grant-independent application recovery sweep never claims or advances search cycles.

### `hh_autopilot_shadow_results`

Stores shadow run, account, vacancy, candidate resume, filter evidence, deterministic/AI decision, and `would_apply` without creating queue items, quota reservations, application guards, attempts, or live authorization. Shadow rows cannot transition into the live state machine; a later live run independently re-fetches and re-evaluates the vacancy.

### `hh_autopilot_account_state`

Stores account-level `blocked_until`, stable block reason, update version, and HH reset metadata. A definite `rate_limited` or `hh_daily_limit` response atomically releases the current reservation, defers the item, and updates this row. Every ready-item dispatch checks it; while active, no application POST for that account is sent. `Retry-After` or HH reset time is authoritative. Without one, rate limit uses retry policy and daily limit uses the next local-day boundary in the account timezone.

### `hh_application_account_guards`

Stores unique `(account_profile_id, source, source_id)` with current owner attempt, first-seen resume, status (`active`, `applied`, or `external_applied`), authoritative application ID when known, application count, and timestamps. This is the concrete account-plus-vacancy concurrency/summary guard. `best_resume_only` treats any applied/external row as terminal. `per_resume` still serializes through the active guard but permits a later different-resume attempt only when no identical account/job/resume application exists and HH has not returned an account-wide duplicate.

### `hh_autopilot_grants`

Stores account, scope (`applications` in this specification), policy hash, creation/revocation timestamps, grant version, and masked actor/source. `HHAutopilotAuthorizer` creates the row only from the existing literal-boolean confirmed enable boundary and passes a typed `LiveAuthorization(kind="autopilot", grant_id, scope, account_id, run_id, fencing_token)` to the service mutation guard.

The guard validates the active grant, account, scope, policy hash, configuration flag, pause state, and kill switch immediately before dispatch. Direct configuration edits cannot create or widen a grant. Disabling or invoking the kill switch revokes the grant atomically. Any canonical policy input change invalidates the hash and requires a new confirmed enable action.

`policy_hash` is SHA-256 over canonical UTF-8 JSON with sorted object keys, normalized arrays where order is not semantic, and no secret values. The canonical input contains the account mapping; effective HH auth profile ID; candidate-profile facts/version; selected resume IDs and content/version hashes; fully expanded search preset definitions; recommendation flag and search budgets; all hard filters; ranking thresholds, weights, AI modes, model identifier, and prompt/template versions; quotas; timezone and schedule; send delays; retry/reconciliation policy; cover-letter mode and template/version; screening/form/CAPTCHA policy; effective proxy/browser transport identity; and administrative maxima. Secret credentials are represented by stable local secret-version IDs rather than values.

The account's `enabled`/`paused` state, notification destinations, log level, retention, run counters, and current timestamps are excluded because they are runtime controls rather than policy content. Any canonical input change blocks autonomous dispatch until a new confirmed enable action creates a new grant generation. An observed `enabled=false`, the `disable` command, or a kill switch permanently revokes the current generation; setting the file back to `true` cannot reactivate it. On every config load, `enabled=false` is persisted as revocation before work continues. A mismatch between the account entry's recorded grant generation and the active database grant stops the scheduler with `authorization_state_mismatch`.

Existing application history must become account-aware before multi-account autopilot is enabled. A versioned table-rebuild migration adds `account_profile_id TEXT NOT NULL`, retains `job_id` as a foreign key rather than a unique key, and creates `UNIQUE(account_profile_id, job_id, resume_id)`. Existing rows migrate with the active legacy profile ID when it can be determined, otherwise the explicit sentinel `legacy`; the migration report lists sentinel rows for later reassignment. `get_application`/`save_application` require account profile and resume identity for HH calls, while compatibility readers may request an aggregate view.

`hh_application_attempts` likewise gains `account_profile_id`, `autopilot_run_id`, `autopilot_item_id`, `autopilot_attempt_id`, authorization kind/reference, policy hash, delivery certainty, and dispatch timestamps. This immutable provenance permits reconciliation/finalization after a grant is revoked without authorizing a new POST. Campaign, employer, contact, negotiation, form review, and operation log tables remain authoritative for their current consumers.

Finalizing a successful or reconciled application is one SQLite transaction that verifies item version and fencing token, upserts the existing application row, writes the existing attempt outcome, consumes the quota reservation, advances the item to `applied`, inserts its event, updates the account-vacancy guard, and increments cached run counters. Any failure rolls back the whole transaction. Non-success transitions similarly update attempt, reservation, item, event, and challenge atomically.

## Configuration Contract

`sources.hh.autopilot` has these validated groups:

```json
{
  "timezone": "Europe/Moscow",
  "accounts": [
    {
      "profile_id": "default",
      "candidate_profile_id": "default",
      "enabled": false,
      "paused": false,
      "authorization_generation": null,
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
      "industry": 0.00
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
    "cover_letter_mode": "template",
    "screening_mode": "ai",
    "form_mode": "ai",
    "captcha_mode": "vision_then_manual",
    "challenge_attempts": 3,
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

`profile_id` identifies the HH authentication account. `candidate_profile_id` identifies the Work Hunter candidate facts/scoring profile used for truthful answers and ranking. `authorization_generation` is a service-managed nullable integer written only by confirmed enable/disable flows; user configuration updates cannot set it. `preset_names` exclusively reference existing top-level `hh_campaign_presets`; there is no second inline preset collection or precedence rule. Presets contain the actual HH query, specialization, area, schedule, employment, experience, salary, and other supported search parameters. An empty `preset_names` list is valid only when recommendations are enabled. `published:*` expands to every published resume in the account; explicit resume IDs are also accepted. The same resume cannot appear twice for one preset after expansion.

Enums are fixed as follows: `remote` is `any|only|exclude`; `unknown_salary` is `allow|reject`; `ai_mode` is `off|borderline|all`; `ai_detail` is `light|heavy`; `ai_failure_policy` is `retry|deterministic|skip`; `resume_policy` is `best_resume_only|per_resume`; `cover_letter_mode` is `none|template|ai`; screening and form modes are `off|profile_grounded|ai`; CAPTCHA mode is `manual_handoff|vision_then_manual`.

All numeric values have explicit bounds: page size `1..100`, pages `1..100`, run search results `1..10000`, interval minutes `1..1440`, salary `0..1000000000`, daily and per-run success `1..administrative_max_daily_success`, administrative maximum `1..200`, send delays `0..3600`, retry and reconciliation checks `1..20`, retry/reconciliation delays `1..86400`, jitter ratio `0..1`, lease/request/navigation times `1..600`, automatic challenge attempts `1..10`, challenge expiry `1..720` hours, and retention `1..3650` days. The fixed role, skills, experience, salary, work-format, area, and industry weights are non-negative finite numbers with a positive sum and are normalized at runtime. Arbitrary custom signal names are not accepted in this slice.

Array and identifier domains are also fixed: accounts `1..100`; resume mappings `1..500` per account; preset names `0..100` per mapping; keyword and role lists `0..1000` strings of `1..200` characters; areas/citizenships `0..500` HH ID strings; schedules, employment types, and experience levels `0..100` values from the current HH dictionaries; languages `0..100` BCP-47 tags; and required application capabilities from `direct|screening|form`. All arrays reject duplicates after normalization. Query preset fields are validated by the existing HH search schema before hashing or execution. Boolean fields accept JSON booleans only.

Structural validation requires valid IANA timezone, unique weekday integers `1..7`, valid `HH:MM` values with `start <= end`, positive interval, `per_run_success <= daily_success`, minimum send delay not above maximum, `borderline_low <= minimum_score <= borderline_high`, AI confidence in `0..1`, lease TTL at least request timeout plus renewal margin, unique account mappings, existing HH and candidate profiles/presets, at least one resolvable published resume, supported modes, and a usable application transport. Unknown configuration keys are rejected.

Authorization validation depends on run mode: autonomous schedule/run-now requires a matching active grant; manual campaign and canary require a valid one-shot `LiteralConfirmation`; shadow requires neither because it cannot mutate HH; grant-independent recovery requires immutable attempt provenance and cannot send a new POST. The enable command performs structural validation first and creates the grant only after it passes. Invalid structure cannot enable or start any live mode.

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

The deterministic ranker returns a normalized `0..100` score with the fixed components role, skills, experience, salary, work format, area, and industry. Every component's weight is configurable and normalized before use; this slice has no unevaluated custom-expression language.

AI evaluation is optional and structured. It returns suitability, confidence, explicit evidence from the vacancy/resume text, and rejection reasons. It cannot override a hard filter and does not mutate the deterministic score.

- `off`: score at or above `minimum_score` becomes ready; lower score is skipped.
- `borderline`: score below `borderline_low` is skipped, score above `borderline_high` becomes ready, and scores inside the inclusive interval require AI. `suitable=true` with confidence at or above `minimum_ai_confidence` becomes ready; `suitable=false` at that confidence is skipped; lower confidence follows `ai_failure_policy`.
- `all`: every hard-filter-eligible vacancy requires the same confident AI decision; deterministic score only orders accepted vacancies.

On required AI failure, `retry` moves the item to `retry_wait`, `deterministic` applies the `minimum_score` threshold, and `skip` terminates with `ai_unavailable`.

The same vacancy can be ranked against multiple resumes. Under `best_resume_only`, candidates are ordered by deterministic score descending, AI confidence descending when present, vacancy publication time descending, vacancy ID ascending, then resume ID ascending. The first eligible resume creates one ready item. This choice is recorded before application dispatch.

## Quotas And Dispatch

The scheduler evaluates the configured local timezone and window. Missed intervals do not create a burst of catch-up runs; at most one run starts when the process resumes. A live account lease prevents concurrent manual, scheduled, retry, or application recovery mutation for the same account.

Before every autonomous application POST, not only at run start, the orchestrator checks:

1. active application grant, matching account/scope/policy hash, and typed authorization context;
2. the selected account entry's `enabled` and `paused` flags;
3. global and account kill-switch state;
4. account lease ownership and current fencing token;
5. time window;
6. per-run remaining quota;
7. daily remaining quota or an existing reservation for this attempt;
8. HH-reported limits;
9. durable account `blocked_until` state;
10. item compare-and-swap version, idempotency guard, and current state.

Dispatch uses a configurable randomized delay. A stopped or paused run commits its current state and exits cleanly.

### Grant-independent recovery sweep

Scheduler startup and every scheduler tick run `HHRecoverySweep` before deciding whether any account is enabled. The sweep selects accounts with stale `applying`, due `reconciling`, or unresolved held reservations, acquires the normal account lease/fencing token, and performs only read-only HH reconciliation plus local durable finalization. It cannot search for new vacancies, reserve a new dispatch, or send an application POST.

Recovery remains eligible after pause, disable, grant revocation, policy-hash change, or kill switch because those controls must stop new mutations without abandoning an already possibly-sent request. The immutable attempt authorization provenance is sufficient only for negotiation-history reads, narrowly required session/token refresh, and local finalization. If read-only access cannot be restored, the sweep creates/retains `manual_auth` and keeps the reservation held. The safe task runner exposes `hh-autopilot-recover`; it is always scheduled locally and `recover-now` is also available from CLI/UI.

## Application And Challenge Flow

`HHApplicationExecutor` sends a standard application through the supported HH transport and maps API/web results to typed outcomes. It never parses an error string to make retry decisions when a typed transport status is available.

Every POST outcome includes delivery certainty: `definitely_not_sent`, `possibly_sent`, or `definite_response`. Only connection/DNS/TLS/preflight failures proven to occur before request bytes are written may be `definitely_not_sent`. Timeout, connection reset, process interruption after dispatch begins, malformed/partial POST response, or any transport exception without proof is conservatively `possibly_sent` and enters reconciliation. A parse failure on a read-only search may retry normally; a parse failure after a possibly successful application POST may not issue another POST before reconciliation.

- A normal success atomically records the existing application and attempt rows, consumes its quota reservation, and advances to `applied`.
- Closed, forbidden, invalid, and other permanent responses release the reservation and advance to `skipped` with their stable code.
- A duplicate response enters reconciliation; it is not treated as an ordinary skip.
- Network failures, `429`, and retryable `5xx` outcomes use `HHRetryPolicy`.
- HH account limits stop further sends for the account until the returned or calculated reset time.
- Authentication expiry schedules token refresh once, then retries the original item. Repeated authentication failure becomes a manual account challenge instead of a tight loop.
- Redirects, required screening, forms, assessments, and CAPTCHA enter `HHChallengeHandler`.

With `screening_mode=profile_grounded`, supported screening fields are mapped from explicit candidate profile fields and resume data; knowledge tests become `manual_assessment`. With `screening_mode=ai`, the same grounded mapping runs first and the configured text model answers remaining HH vacancy-test tasks. Multiple-choice answers must resolve to one supplied solution ID; free-text answers are bounded text. With `screening_mode=off`, any vacancy requiring screening terminates as `screening_disabled` without opening or submitting it.

With `form_mode=profile_grounded`, supported forms are filled in the authenticated browser session using the same mapping; unknown required fields produce `missing_required_data`. With `form_mode=ai`, grounded values are retained and remaining fields are answered by the configured model using their labels and supplied options. With `form_mode=off`, any redirect to a required form terminates as `form_disabled`. Neither `off` mode silently creates a manual challenge or sends a partial response.

Vacancies with `has_test=true` use the HH web protocol used by the reference tool: parse embedded `vacancyTests`, build `task_*` fields, and POST the native `/applicant/vacancy_response/popup` payload with the browser XSRF token and cookies. Application CAPTCHA is solved inline from `account-captcha-picture` into `account-captcha-input`; after success the original API/web mutation is retried within the configured bound. `manual_captcha` is created only when automatic solving is disabled or all Vision attempts fail. The challenge stores only sanitized context. Ambiguous and authentication challenges follow their type-specific transitions defined in persistence.

Contact discovery and employer snapshots use the existing storage tables. Scheduled recruiter email is outside this core specification and remains an existing separately confirmed operation until the maintenance slice defines its grant and durable scheduler.

### Ambiguous remote result reconciliation

Before every remote POST, the orchestrator creates an attempt and quota reservation, transitions the item to `applying`, and commits those records. If the process loses the response or crashes before finalization, recovery moves the item to `reconciling`.

`HHApplicationReconciler` queries read-only HH negotiation/application history for the same account and vacancy across every resume, then compares resume identity and remote timestamps with the prepared attempt:

- a matching same-resume negotiation created at or after the attempt finalizes the existing attempt as `applied` and consumes its original reservation;
- a negotiation that clearly predates the attempt or belongs to another resume is recorded through the account-vacancy guard as `external_applied`, releases the current reservation, and terminates the item as `duplicate_external` without another POST. If its unique remote ID and timestamp place it in the current local day, an idempotent `external_sync` consumed quota row counts it toward the account's daily total; older external applications do not consume today's quota;
- a confirmed absence after the configured consistency delay releases the reservation and permits a bounded new attempt;
- a duplicate response followed by a matching negotiation finalizes as `applied` rather than `skipped`;
- a negotiation whose resume/time cannot be classified, or an outcome still ambiguous after the reconciliation limit, holds the reservation, creates an item challenge `ambiguous_application`, and does not issue another POST automatically.

The default consistency delay is 30 seconds and reconciliation limit is 3 read-only checks; both are configurable within the retry bounds. This is reconciliation, not remote idempotency.

## Retry Policy

Retryability is determined by the following complete outcome families:

| Outcome | Action |
|---|---|
| any `possibly_sent` POST outcome, `post_dispatch_network_error`, `post_dispatch_parse_error` | `reconciling` before any retry |
| `pre_dispatch_network_error`, `read_parse_error`, pre-dispatch `internal_error`, definite retryable `server_error` | `retry_wait`, bounded by `max_attempts` |
| `rate_limited`, `hh_daily_limit` | `retry_wait` at `Retry-After`/reset, without a tight loop |
| `auth_expired` | one token/session recovery; then `manual_auth` account challenge |
| `duplicate`, `ambiguous_remote_result`, abandoned `applying` | `reconciling` |
| `manual_captcha`, `manual_assessment` | `manual_challenge` |
| `duplicate_external`, `external_applied`, `vacancy_closed`, `forbidden`, `invalid_request`, hard filter, missing data, `screening_disabled`, `form_disabled`, `ai_unavailable` under skip policy | terminal `skipped` |
| `challenge_expired`, `challenge_dismissed` | terminal `skipped`, except ambiguous application becomes `dead` with held quota |
| `authorization_state_mismatch` | stop account run; no item dispatch |
| exhausted retry or reconciliation | `dead` or `ambiguous_application` challenge as specified above |

`max_attempts` counts actual application POST dispatches, including requests whose response was lost. Read-only reconciliation checks and token refresh do not increment it. Authentication recovery is separately bounded by `auth_recovery_attempts`.

Every direct transition to `retry_wait` releases the current dispatch reservation in the same transaction. A possibly-sent outcome never transitions directly to `retry_wait`; reconciliation retains or holds its reservation until remote state is classified.

The default attempts are bounded and persisted. Delay is exponential with bounded jitter:

`min(max_delay, base_delay * 2^(attempt - 1)) * factor`, where `factor` is sampled uniformly from `1 - jitter_ratio` through `1 + jitter_ratio`.

`Retry-After` or a typed HH reset timestamp takes precedence when longer. `next_attempt_at` is stored in UTC. A scheduler tick processes due retries before new search results, subject to the same quotas and window. Exhausted retries advance to `dead` with the last masked error. CLI and UI can explicitly requeue `dead` or resolved manual items.

## Authentication And Session Lifecycle

Work Hunter provides browser-assisted login by password or one-time code, cookie import, token refresh, logout, profile selection, and session diagnostics through the existing HH account profile boundary. Login OTP and login CAPTCHA remain interactive, matching the reference authorization operation. Application CAPTCHA is automatic when `captcha_mode=vision_then_manual`. Refreshed tokens and cookies are written atomically to the active profile and reused by API and browser transports.

The implementation does not depend on undeclared or decompiled third-party client credentials. Required OAuth client configuration must be user-provided or otherwise legally distributable.

## Reference Feature Parity

The finished system must expose or preserve the following reference behaviors through Work Hunter's native interfaces:

| Behavior | Work Hunter target |
|---|---|
| Multi-page query and recommendation search | Unified search provider and presets |
| Multiple accounts and resumes | Profile-scoped runs, leases, quotas, and mappings |
| AI light/heavy filtering | Configurable structured ranker modes |
| Template and AI cover letters | Existing letter service used by executor |
| Vacancy tests and forms | Grounded-first AI challenge resolver using HH native payloads |
| CAPTCHA continuity | Vision solve in the persisted authenticated session, automatic retry, manual fallback |
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

Random/middle-option test guesses, fabricated candidate claims, undeclared client secrets, and deliberate duplicate spam are not parity targets. Vision CAPTCHA solving is a parity target and uses only the operator-configured AI backend.

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

The pre-dispatch matrix is:

| Check | Autonomous POST | Manual confirmed POST | Read-only reconciliation | Local finalization |
|---|---:|---:|---:|---:|
| Matching active grant, `enabled`, not paused | required | not required | not required | authorization provenance on attempt only |
| Scheduler time window | required | not required | not required | not required |
| Global/account kill switch | must be clear | must be clear | read-only allowed | allowed to close durable state |
| Account lease and fencing token | required | required | required | required |
| Account cooldown | required | required | read-only allowed | allowed |
| Daily/per-run quota reservation | required | required | existing reservation only | existing reservation only |
| Item version and account-vacancy guard | required | required | required | required |

Thus pausing or disabling the autopilot does not break a one-shot manual confirmation, and a stopped schedule cannot prevent safe read-only reconciliation of an already attempted POST. Manual success still counts toward the same account daily quota because HH enforces one remote account limit.

Manual campaign confirmation remains available, but its items pass through the same executor, reservation, reconciliation, application record, and journal code. It does not create a durable grant. The scheduler can use only `LiveAuthorization`. Existing plan/dry-run calls remain read-only.

MCP may inspect configuration, status, plans, history, and challenges but cannot enable or disable the autopilot, clear the kill switch, create a grant, or initiate a live run. The current agent `real_apply_blocked` rule remains for arbitrary agent calls; the scheduler is not treated as an agent call.

The legacy `allow_broad_apply` boolean is deprecated and cannot authorize a mutation. Compatibility reads may display it, but live permission is derived solely from the typed authorization forms above. The application grant includes the narrowly required session/token refresh recovery scope; it does not include resume update, reply, email, cleanup, raw API mutation, or account-profile mutation.

## CLI And UI Contract

Add native commands and equivalent local API/UI actions. Commands capable of authorizing or issuing a new HH mutation require exactly one `--account PROFILE_ID` or an explicit `--all`; they never silently select the first configured account. Read-only views and grant-independent recovery default to all accounts and accept an optional account filter.

- `hh autopilot validate [--account ...]`
- `hh autopilot enable --account ... --confirm` or `--all --confirm`
- `hh autopilot disable --account ... --confirm` or `--all --confirm`
- `hh autopilot status [--account ...]`
- `hh autopilot shadow --account ... [--resume ...] [--preset ...]`
- `hh autopilot canary --account ... --resume ... --vacancy ... --confirm`
- `hh autopilot run-now --account ...` or `--all`
- `hh autopilot recover-now [--account ...]`
- `hh autopilot pause --account ...` or `--all`
- `hh autopilot resume --account ...` or `--all`
- `hh autopilot stop --account ... --run-id ...`
- `hh autopilot kill-switch --account ... --confirm`, `--all --confirm`, or `--global --confirm`
- `hh autopilot clear-kill-switch --account ... --confirm`, `--all --confirm`, or `--global --confirm`
- `hh autopilot retry --account ... --item-id ...`
- `hh autopilot challenges [--account ...]`
- `hh autopilot resolve-challenge --account ... --challenge-id ... --action ...`
- `hh autopilot history [--account ...]`

`enable` validates each selected account and atomically creates one grant per account; `--all` fails without changing any account if any selected account is invalid. It sets the per-account enabled projection. `disable` revokes each selected grant, clears its enabled projection, and requests its current run to stop after the in-flight network call. `pause` preserves the grant/configuration but prevents new runs and new dispatches for the selected accounts; `resume` clears that state. `stop` affects only the named account/run, so its next scheduled interval remains eligible. Account/all/global kill switches revoke the affected grants and block every new mutation immediately; clearing them does not recreate grants, so each account must be enabled again.

`run-now` does not require per-run confirmation when a valid application grant exists. Enabling, disabling, killing, clearing the kill switch, and reauthorizing after a high-risk setting change require the existing literal-boolean safety acknowledgement in CLI/UI/API. Direct configuration edits are visible to validation but cannot create authorization.

`shadow` requires no grant or confirmation because it performs only real read-only search plus filters/ranking and writes isolated shadow results; it cannot enqueue or mutate HH. `canary` requires one account, one published resume, one vacancy ID, and literal confirmation. It creates a `canary` run with `LiteralConfirmation`, hard-filters the named vacancy, reserves at most one success, and uses the normal lease, cooldown, quota, executor, reconciliation, challenge, and journal paths. It does not create a durable grant or authorize any second vacancy. `recover-now` is grant-independent and exposes only the recovery sweep described above.

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
- search-cycle claim, checkpoint commit, fenced ownership, and recovery-run audit;
- search-cycle supersession on policy-hash mismatch;
- every hard filter and missing-value policy;
- score normalization, AI modes, and resume tie-breaking;
- quota reservation/consume/release, compare-and-swap races, abandoned reservation recovery, and local-day rollover;
- held ambiguous reservations and account cooldown enforcement;
- late ambiguity resolution after expiry/dismissal and requeue rejection while unresolved;
- account-aware application migration, legacy sentinel reporting, and unique account/job/resume identity;
- retry classification, backoff, `Retry-After`, and exhaustion;
- configuration validation and masking;
- deterministic `off` behavior for required screening/forms;
- shadow-result isolation and exact one-vacancy canary authorization;
- kill-switch checks before every mutation.

### Contract tests

A local fake HH server represents pagination, recommendations, normal application, duplicate, closed vacancy, redirect, screening, form, CAPTCHA response, authentication expiry, `429`, retryable `5xx`, timeout, and HH daily limit. Tests assert transport calls and durable business outcomes rather than mock call counts alone.

The fake server can accept a POST and close the connection before returning its response. The test must prove that recovery reconciles the remote negotiation, consumes exactly one reservation, creates exactly one authoritative application record, and never issues an unsafe second POST. A duplicate response with a matching same-resume negotiation must produce `applied`; a pre-existing or other-resume negotiation must produce `external_applied` without consuming the new reservation or issuing another POST. An unclassifiable duplicate must hold quota and create `ambiguous_application`.

### Browser integration tests

Local fixtures exercise authenticated form filling, native vacancy-test payloads, Vision CAPTCHA element capture/fill, session-cookie persistence, manual fallback, and automatic continuation. No live CAPTCHA is part of the default suite.

### Restart and concurrency tests

Tests terminate a run after `applying`, expire its lease, restart the service, and assert one reconciled application without duplicates. A stale fenced owner cannot commit after takeover. Concurrent runners against one account must produce one lease owner; the replacement reconciles stale in-flight attempts before dispatch. Different accounts may apply to the same vacancy without overwriting each other's authoritative history. Additional sequences cover process death after reservation, policy change with queued items, pause/disable/kill during an in-flight request followed by grant-independent recovery, authentication failure during reconciliation retaining quota, account cooldown with other ready items, challenge expiry, ambiguous challenge resolution, and attempted timezone change with an active grant or unresolved reservation.

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
- possibly-sent transport failures never issue another POST before account-wide reconciliation;
- recovery resolves possibly-sent attempts after pause, disable, revocation, restart, or kill switch without authorizing a new POST;
- account-aware application history preserves independent results for two HH accounts on one vacancy;
- fencing prevents a stale runner from committing after lease takeover;
- quota reservations cannot leak or overshoot the configured daily/per-run limit after failure or restart;
- HH rate/daily-limit cooldown blocks every ready item for the affected account until reset;
- retryable failures resume after persisted backoff;
- permanent and missing-data outcomes do not retry;
- supported tests/forms and application CAPTCHA resolve inline; exhausted/disabled CAPTCHA or unsupported assessment creates one manual challenge without stopping other vacancies;
- application authorization is durable, account/scope/policy-bound, immediately revocable, and cannot be created by editing configuration directly;
- manual literal confirmation, autonomous dispatch, read-only reconciliation, and local finalization obey their distinct safety-check matrix;
- shadow runs cannot create live queue/guard/quota state, and a canary can authorize exactly one named vacancy only;
- CLI and UI expose enable, disable, validation, status, pause, stop, kill switch, retry, challenge resolution, quota, and history;
- secrets and personal payloads are absent from normal logs and journal events;
- unit, contract, browser, restart, concurrency, compatibility, lint, type, build, and package smoke gates pass;
- documentation describes actual behavior and does not claim live HH compatibility without a successful opt-in canary.
