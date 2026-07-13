# Work Hunter Apple HIG UI and Onboarding Design

**Date:** 2026-07-13

**Status:** approved for implementation planning

## Goal

Replace the current dense browser cockpit with a clear, desktop-first command center inspired by Apple Human Interface Guidelines. A first-time user should understand what is configured, what requires attention, and what to do next without reading documentation. Existing Work Hunter capabilities and safety guarantees remain available.

The primary post-onboarding destination is a new **Today** screen. The first-run experience is a three-step setup wizard followed by at most four contextual coach marks. Feedback uses a consistent system of sheets, popovers, toasts, inline errors, and empty states instead of browser alerts or unexplained output blocks.

## Product Constraints

- Work Hunter remains a private, local-first web application for one user.
- The existing standard-library HTTP server, static frontend, service facade, SQLite storage, and API routes remain the implementation foundation.
- No UI path may weaken the current literal-boolean confirmation requirements or server-side guards for live HH mutations.
- Runtime UI assets remain local. No remote fonts, scripts, analytics, or executable dependencies are introduced.
- Existing functionality is reorganized, not removed.
- The redesign is desktop-first but must remain usable on a narrow browser viewport.

## Approved Direction

Use a native command-center model rather than a linear wizard-only product or an AI-first shell.

- A translucent, restrained sidebar provides stable global navigation.
- Content uses open space, semantic lists, tables, and a small number of purposeful panels instead of nested card grids.
- The Today screen prioritizes current tasks, fresh matches, upcoming events, and decisions awaiting approval.
- Advanced and dangerous controls appear through progressive disclosure.
- Motion is brief and functional and respects `prefers-reduced-motion`.
- The visual language uses system fonts, Apple-like spacing and hierarchy, quiet neutral surfaces, blue for primary actions, green for completed/safe status, amber for attention, and red only for destructive or live irreversible actions.

## Information Architecture

The persistent navigation contains eight destinations.

| Destination | Purpose | Existing capability mapping |
|---|---|---|
| Today | Daily priorities, fresh matches, upcoming events, readiness, pending approvals | New composition of existing jobs, tasks, events, approvals, resumes, and source state |
| Vacancies | Search results, filters, saved items, vacancy detail, scoring, letters | `/jobs` plus the current Favorites view as a saved filter |
| Applications | Application pipeline, agent queue, approvals, runs, outcomes, ghost follow-up | Relevant `/agent` workflows and application statuses |
| Calendar | Interviews, follow-ups, reminders | `/calendar` |
| Assistant | Job-aware chat, drafting, analysis, preparation | `/chat` and job AI actions |
| Analytics | Funnel, score distribution, sources, trends | `/stats` and `/trends` |
| Sources | Source connectivity, capability, last sync, errors | `/sources` |
| Settings | Profiles, resumes, AI, HH auth, notifications, appearance, help | `/settings`; HH API Lab and low-frequency operator tools live under an Advanced subsection |

Canonical destination paths are `/today`, `/jobs`, `/applications`, `/calendar`, `/assistant`, `/analytics`, `/sources`, and `/settings`. Legacy routes use `history.replaceState` so browser history does not contain a redundant redirect entry. The mapping is exact:

| Incoming path | Canonical output URL |
|---|---|
| `/` or `/today` | `/today` |
| `/jobs` | `/jobs?filter=all` plus valid recognized query values |
| `/favorites` | `/jobs?filter=saved` plus valid recognized query values |
| `/calendar` | `/calendar` with a valid `event` value |
| `/chat` or `/assistant` | `/assistant` with a valid `job` value |
| `/agent` | `/applications?tab=agent` plus valid record query values |
| `/applications` | `/applications?tab=pipeline` or another valid `tab`, plus record query values |
| `/settings` | `/settings?section=profile` or another valid `section` |
| `/sources` | `/sources` |
| `/stats` | `/analytics?tab=overview` |
| `/trends` | `/analytics?tab=trends` |
| `/analytics` | `/analytics?tab=overview` or `tab=trends` |

Recognized query values are:

- Vacancies: `filter` is `all` or `saved`; `source` is a loaded source key; `min_score` is an integer from 0–100; `status` is `new`, `saved`, `hidden`, `applied`, or `ghosted`; `job` is a positive safe integer.
- Calendar: `event` is a positive safe integer.
- Assistant: `job` is a positive safe integer.
- Applications: `tab` is `pipeline`, `agent`, or `automation`; `approval`, `run`, and `operation` are positive safe integers.
- Analytics: `tab` is `overview` or `trends`.
- Settings: `section` is `profile`, `resumes`, `search`, `hh`, `ai`, `notifications`, `appearance`, `help`, or `advanced`.

Canonical URLs always materialize destination defaults: Vacancies includes `filter=all`, Applications includes `tab=pipeline`, Analytics includes `tab=overview`, and Settings includes `section=profile`. Legacy `/favorites` forces `filter=saved` and ignores any conflicting incoming `filter`; `/agent`, `/stats`, and `/trends` likewise force the destination states shown in the table.

For every recognized key, the first occurrence wins and later duplicates are removed. An invalid first occurrence removes that recognized key and does not fall through to a later duplicate. Serialize integers as unsigned base-10 without a plus sign or leading zeroes (`0` is valid only where the range permits it). Serialize recognized values in the order listed above, followed by unknown keys in their original order; preserve unknown values and hash fragments verbatim.

Source validation is intentionally two-phase. Before source state loads, retain a syntactically non-empty `source` value as pending. After source keys load, keep it only if it exactly matches a loaded key; otherwise remove it with `replaceState` and show one warning. All other recognized values are validated during initial resolution. `popstate` re-runs both phases without adding or replacing a history entry unless canonical normalization is required, in which case it may call `replaceState` once. The router never converts an invalid identifier into a different valid record.

### Capability inventory

Every current UI capability has a defined home:

| Capability group | Required destination |
|---|---|
| Sync, score, source/minimum-score/status filters, select all, bulk status actions, open vacancy, saved-only view | Vacancies |
| Save, hide, mark applied, external vacancy link, note editing, full-description fetch | Vacancies detail |
| Local and AI cover letters, fit analysis, ATS audit/resume output, summary, resume tips, interview questions, experience pitch, classification, structure, gap analysis, Telegram share | Vacancies detail under progressive **Prepare** and **More** sections; Assistant may deep-link back to the same job |
| HH dry-run apply plan and real apply confirmation | Vacancies detail; the live action always uses the safety sheet |
| Application pipeline, ghost follow-up, agent digest, operations, approvals, decisions, outcomes, research plan/run, campaign planning and review | Applications |
| Agent templates, blacklist, resume preview, batch matrix, negotiations and other low-frequency HH operator utilities | Applications under **Automation**; API Lab and saved snippets move to Settings → Advanced |
| Calendar event create/edit/delete and agent tasks/reminders | Calendar |
| Job-aware chat and saved-search AI search | Assistant; saved-search CRUD remains in Settings → Search |
| Funnel, score distribution, market trends, refresh and CSV export | Analytics |
| Source health, last-sync state, per-source error and retry | Sources |
| Profile switch/edit, raw config fallback, secret clearing, HH credentials/auth check, AI backend, resumes, saved searches, auto-sync, theme, help reset | Settings |

The implementation plan must turn this inventory into a browser regression checklist. A capability may move or receive new presentation, but it may not disappear.

## Today Screen

Today answers one question: **What should I do now?** It contains four bounded regions.

1. **Readiness strip** — shows whether the active profile, at least one source, and an active resume are available. Optional HH and AI configuration appear as recommendations, not blockers for public-source search.
2. **In focus** — up to three deterministic actions derived from real state.
3. **Fresh matches** — the three highest-ranked vacancies whose persisted status is `new`, with score, role, company, work format, and salary summary.
4. **Upcoming and decisions** — the next two calendar items and a compact entry point for pending agent approvals.

The primary action is **Find vacancies**. The screen must not invent metrics or tasks when data is absent; it uses the defined empty states instead.

Focus actions are selected in this priority order:

1. pending live-action approvals, oldest creation time first;
2. open tasks overdue by no more than 30 local calendar days, then open tasks due today, earliest due time first; calendar events are eligible here only when scheduled today and are never carried forward after their date;
3. `new` vacancies with score at least 70, highest score first;
4. incomplete readiness actions in the order goal, source, resume;
5. source failures, most recent failure first.

Approval eligibility requires `status == "pending"`; task eligibility requires `status == "open"`. Use the injected browser-local clock/timezone for Today boundaries. Parse timestamps with offsets as absolute instants converted to that timezone; parse a timezone-less datetime as local wall time with Temporal-compatible disambiguation (choose the earlier instant in an overlap and shift forward by the gap duration for a nonexistent time); parse a date-only `YYYY-MM-DD` as local midnight with the same rule. Exclude missing or invalid timestamps from Today and show their record in Calendar/Applications with a date warning. Tasks more than 30 local days overdue stay in Calendar but do not monopolize Focus.

Take the first three after sorting. Within category 2, tasks sort before Calendar events when timestamp and ID match. Numeric records then use ID ascending for ties. Synthetic readiness actions use the fixed goal/source/resume order. Source failures use source key ascending after failure time. These category-specific keys resolve every tie; no cross-category tie occurs because category priority is primary.

The Upcoming region contains the next two valid Calendar events in the half-open interval `[now, startOfLocalDay(today + 31 days))`, ordered by parsed event time then numeric ID ascending. Construct the upper bound with local calendar-date arithmetic so daylight-saving changes do not add or remove a calendar day. Past, invalid, and more distant events remain in Calendar but do not appear on Today.

Fresh matches require `status == "new"` and a finite numeric total score. They sort by total score descending, timestamp descending, and ID descending. Timestamp is parsed `published_at` when valid; otherwise parsed `fetched_at` when valid; otherwise invalid and after every valid timestamp. Unscored jobs remain in Vacancies and never enter Today until the ordinary Score action produces a finite score. There is no separate `unseen` concept in this redesign.

Each Today input is a `ResourceState<T>` with `status` (`loading`, `ready`, or `error`), optional `data`, optional safe error metadata, optional `updatedAt`, and `stale: boolean`. Source loaders derive `stale` from backend-reported last-sync/error state; other resources default it to `false`. The Today composer also requires an injected `now` instant and IANA timezone string; production supplies the browser values and tests supply fixed values. The composer receives these inputs, not bare arrays or a hidden global clock, so loading, genuine empty, stale, failed, and time-dependent results cannot be confused.

## First-Run Onboarding

### Readiness state

Readiness begins as `loading`. It becomes:

- `ready` only when config/profile, source configuration, and resumes loaded successfully and all three core conditions are satisfied;
- `incomplete` only when those three requests loaded successfully and at least one condition is absent;
- `unknown` when any core readiness request failed.

The three core conditions are: at least one non-empty desired role or search query, at least one enabled source, and one active resume. HH authentication and AI credentials are recommendations, not readiness requirements.

Readiness is separate from first-run confirmation. Add `ui.onboarding_version` to the config schema with new-install default `0`. The config loader must inspect the raw saved config before applying defaults: when a legacy config lacks this key, persist `ui.onboarding_version=2`; a newly initialized config already contains `0`. Thus a genuine new install reviews all three steps even though default roles and sources are prefilled, while an existing installation is not forced to reconfirm unchanged configuration. A legacy installation that is actually incomplete still opens the missing core step because domain readiness remains authoritative.

Normalize the supported legacy flat profile before onboarding reads or writes it. When raw `config.profile` is an object, deep-copy it into a deterministic target without overwriting a named profile: use `default` when absent; reuse `default` when it is structurally JSON-equal with object-key order ignored and array order preserved; otherwise use the first free ID in `legacy`, `legacy-2`, `legacy-3`, and so on. Replace `config.profile` with that target ID and preserve every other named profile. Persist this migration atomically with the onboarding-version migration. If the normalized active profile ID is missing from `config.profiles`, readiness is `unknown` with a Settings recovery action; onboarding never invents a write target.

The wizard never auto-opens while readiness is `loading` or `unknown`. `unknown` renders a Today readiness error with Retry. At stable boot, open the wizard when either `ui.onboarding_version < 2` or readiness is `incomplete`, unless onboarding was deferred for the current tab session. If version is below 2, start at the first step not present in `completedSteps`, even when its prefilled domain condition is already satisfied. If version is 2 and readiness is incomplete, start at the first missing domain condition. If product data later stops satisfying a completed step, readiness returns to `incomplete`; presentation progress never overrides domain truth.

### Persistence schema

Permanent presentation state is JSON in `localStorage["work-hunter:onboarding:v2"]`:

```json
{
  "version": 2,
  "completed": false,
  "completedSteps": ["goal"],
  "pendingResumeActivationId": null,
  "finalization": {
    "syncStatus": "not_started",
    "syncCompletedAt": null,
    "safeErrorCode": null
  },
  "completedCoachMarks": [],
  "dismissedCoachMarks": [],
  "updatedAt": "2026-07-13T12:00:00Z"
}
```

Current-tab state is JSON in `sessionStorage["work-hunter:guidance-session:v2"]`:

```json
{
  "onboardingDeferred": false,
  "forceReview": false,
  "coachMarkShown": false,
  "snoozedCoachMarks": []
}
```

`finalization.syncStatus` is exactly `not_started`, `succeeded`, or `failed`. Write `succeeded` to local storage immediately after a successful sync response and before attempting the version-2 config save. Write `failed` plus a stable non-secret error code after a failed sync. When all three completed steps exist and server version remains below 2, always open Summary: a stored `succeeded` result offers **Finish setup** and retries only the version save; `failed` offers Retry Sync and **Continue to Today**; `not_started` offers **Find first vacancies**.

Finalization field transitions are atomic per local-storage write:

| Event | `syncStatus` | `syncCompletedAt` | `safeErrorCode` |
|---|---|---|---|
| Default, reset introduction, or start/retry sync | `not_started` | `null` | `null` |
| Sync response succeeds | `succeeded` | current ISO instant | `null` |
| Sync response fails | `failed` | current ISO instant | stable non-secret code |
| Version-2 config save succeeds or fails | unchanged | unchanged | unchanged |
| Post-onboarding toast Retry succeeds | `succeeded` | replace with current ISO instant | `null` |
| Post-onboarding toast Retry fails | `failed` | replace with current ISO instant | replace safe code |

Malformed, wrong-version, or wrong-shape values are ignored and replaced with defaults; they never change product configuration. Permanent changes are reconciled across tabs through the `storage` event. The one-new-mark limit is per tab session. Settings → **Run introduction again** resets the permanent presentation state, sets `forceReview=true` in the current tab, and opens Goal with current values prefilled; it does not lower the server onboarding version or delete product configuration. Completing this voluntary review clears `forceReview`. Settings also exposes **Show tips again**, which resets only `completedCoachMarks`, `dismissedCoachMarks`, `coachMarkShown`, and `snoozedCoachMarks`; it does not change `onboardingDeferred`, `forceReview`, completed steps, or finalization state.

**Set up later** sets `onboardingDeferred=true`, sets `forceReview=false`, and closes the wizard until that tab session ends; it does not complete or skip any core step. A voluntarily restarted review therefore stays closed until the user explicitly runs it again. There is no per-step skip for the three core requirements. Only optional HH setup can be skipped. Completion requires all three domain readiness conditions.

`completed=true` is written only after all three core conditions are verified and the server has successfully persisted `ui.onboarding_version=2`. On every boot, a stored `completed=true` is ignored when the server version is below 2 or readiness is not `ready`.

### Wizard state transitions

| Current state/event | Required behavior |
|---|---|
| Boot, readiness `loading` | Render shell and section skeletons; do not open a sheet |
| Readiness `unknown` | Render Today readiness error with Retry; do not infer missing configuration |
| Stable boot, version 2 and readiness `ready` | Render Today, then schedule guidance |
| Stable boot, version below 2 and not deferred | Open first locally unconfirmed step, beginning with Goal on a new install |
| Stable boot, readiness `incomplete` and not deferred | Open first missing core step regardless of stored presentation completion |
| `forceReview=true`, readiness is stable, fewer than three completed steps | Open the first locally unconfirmed step with current values, regardless of server version |
| All three completed steps, version below 2 | Open Summary and branch on persisted `finalization.syncStatus`; never wrap back to Goal |
| `forceReview=true` and all three completed steps | Open Summary regardless of server version; **Finish review** sets `forceReview=false`, writes local `completed=true`, and closes without a config-version write or required sync |
| **Set up later** | Set session deferral, clear `forceReview`, close sheet, keep readiness action visible |
| Step submit | Disable all submit paths for that step, show busy state, send one request |
| Step request success | Reload affected resource, verify the domain condition, record step completion, advance |
| Step request failure or failed verification | Stay on the step, preserve input, restore controls, show inline error and Retry |
| Resume activation makes readiness `ready` | Stay on the wizard summary; readiness changes never auto-close an already open wizard |
| Final sync success | Persist local `syncStatus=succeeded` first, then persist `ui.onboarding_version=2`; after that save succeeds, write local `completed=true`, close, route to Today, show success toast |
| Final sync failure | Persist local `syncStatus=failed` and a safe error code, keep completed settings, stay on the summary with Retry and **Continue to Today** |
| **Continue to Today** after sync failure | Persist `ui.onboarding_version=2` without repeating sync; after that save succeeds, write local `completed=true`, close, and show a persistent sync-failure toast with Retry |
| Persisting version 2 fails | Stay on summary, preserve sync result, and Retry only the config-version save; never repeat a successful sync |

### Step 1 — Search goal

Collect and save only the minimum useful profile fields through the existing config update path:

- desired role/search query;
- preferred work format;
- minimum monthly salary.

The screen explains that detailed skills, exclusions, and locations can be edited later.

Let `profileId = config.profile` and `profile = config.profiles[profileId]`. The role field writes the same trimmed value to `profile.title`, `profile.queries = [value]`, and `profile.desired_roles = [value]`. Salary writes `profile.salary_min` as a non-negative integer.

Work format has exactly three UI values and maps to existing fields:

- `remote`: set `profile.remote_only=true`; preserve `profile.locations`.
- `hybrid`: set `profile.remote_only=false`; ensure one case-insensitive `remote` entry exists in `profile.locations`, preserving other entries and order.
- `office`: set `profile.remote_only=false`; remove case-insensitive `remote` entries from `profile.locations`, preserving other entries and order.

Reload inference is `remote` when `remote_only` is true, otherwise `hybrid` when locations contains `remote`, otherwise `office`. Validation requires a trimmed role/query, one of the three exact format values, and a non-negative safe integer salary. Submit updates the in-memory masked config snapshot and sends one `POST /api/config`. Only a 2xx response followed by a successful reload whose active profile equals these mappings can complete the step. A rapid double click cannot create a second request.

### Step 2 — Sources and HH

- Show every configured source key and its current enabled/connection state. Source capability/health may be ready, unavailable, or unknown, but is advisory for onboarding.
- Each toggle writes the boolean `config.sources[sourceKey].enabled`; `sourceKey` must already exist in the loaded config. Save all changed toggles together by updating the masked config snapshot through one `POST /api/config`, then verify the returned/reloaded booleans.
- Require at least one enabled source before Continue is enabled.
- Core source readiness is defined only by a successfully loaded config containing at least one `config.sources[key].enabled === true`. Temporary capability/health failure does not make onboarding incomplete; it produces an inline warning and Retry while the enabled source still satisfies setup.
- Let `accountId = config.hh_account_profile || "default"` and ensure `config.hh_account_profiles[accountId]` exists before editing. HH `client_id` and non-secret transport fields write under `config.sources.hh`; `access_token`, `refresh_token`, and `client_secret` write under `config.hh_account_profiles[accountId]`. A displayed `***` or untouched blank secret field means preserve the stored value and is omitted from the change set. Clearing a secret is available only through `POST /api/config/secret/clear` with the exact allowlisted account-profile path and literal confirmation. Cancel discards the change set.
- After a successful config save, **Check HH connection** explicitly calls `GET /api/agent/preflight?live_auth=true`. Failure stays in the optional area and never blocks core completion.
- This redesign does not introduce an OAuth browser redirect or callback. If OAuth is added later, it requires a separate design and safety review.

### Step 3 — Resume and first search

- Extend the read endpoint non-breakingly: bare `GET /api/resumes` keeps its legacy default-profile behavior, while onboarding calls `GET /api/resumes?profile_id=<activeProfileId>`. The server validates that ID against normalized `config.profiles` and calls `list_resumes(profile_id)`. Readiness requires one returned resume with `profile_id == activeProfileId` and `is_active == true`; other-profile resumes are not loaded or shown in onboarding.
- Select a returned resume and activate it through `POST /api/resumes/{id}/activate`, or render the existing local resume fields inside the onboarding sheet and create it through one `POST /api/resumes` with `profile_id=activeProfileId` and `is_active=false`, followed by `POST /api/resumes/{newId}/activate`. Before activation, persist the selected or newly returned ID as `pendingResumeActivationId`. Verify via the profile-scoped GET that exactly that resume is active before completion, then clear the pending ID.
- Show a readiness summary.
- Resume name and non-empty body are required. Submit is locked until its request settles. A successful create is followed by resume reload and explicit activation verification before the step completes.
- If create succeeds but activation/verification fails, remain on Step 3 and Retry only `POST /api/resumes/{pendingResumeActivationId}/activate`; never repeat create while the pending ID exists. After reload, reconcile the pending ID against the profile-scoped GET: complete when it is active, offer activation Retry when it exists but is inactive, or clear it and restore create/select when it no longer exists.
- Finish with **Find first vacancies**, which sends one `POST /api/sync` with `{"score": true}` and follows the final-sync transitions above.
- Failure to sync does not roll back completed settings or create a duplicate resume; show an inline error with Retry.

## Contextual Guidance

After the setup sheet, define exactly four ordered coach marks and show no more than one previously unseen mark per tab session.

| Order / stable ID | Canonical route | Prerequisite | Stable anchor |
|---|---|---|---|
| 1. `find-vacancies` | `/today` | readiness is `ready` and no sync is running | `[data-guide="find-vacancies"]` |
| 2. `match-score` | `/jobs?filter=all` or saved filter | selected job has a finite numeric score | `[data-guide="match-score"]` within the selected detail |
| 3. `vacancy-actions` | `/jobs?filter=all` or saved filter | a job is selected and status actions are enabled | `[data-guide="vacancy-actions"]` |
| 4. `live-safety` | `/jobs?filter=all` or saved filter | selected HH job has a complete dry-run plan and enabled live-action trigger | `[data-guide="live-hh-action"]` |

The scheduler scans the ordered list and chooses the first mark whose prerequisite is satisfied and whose anchor is visible on the current route. Visible means connected, not `hidden` or inert, intersecting the viewport, and not covered at its center point according to `document.elementFromPoint`. An unavailable or covered anchor remains pending; it is neither completed nor dismissed and does not prevent a later currently eligible mark from appearing. Once any new mark has appeared, `coachMarkShown=true` prevents another new mark in that tab session.

Coach-mark actions have explicit semantics:

| Action | Permanent state | Current session | Ordering effect |
|---|---|---|---|
| **Got it** | Add ID to `completedCoachMarks` | Close | Mark is no longer eligible |
| Close button / **Do not show again** | Add ID to `dismissedCoachMarks` | Close | Mark is no longer eligible |
| Escape or navigation | No permanent change | Add ID to `snoozedCoachMarks`, close | Mark may return next tab session |
| Anchor disappears | No permanent change | Close without snoozing | Re-evaluate on the next stable render |
| **Skip introduction** | Add all four IDs to `dismissedCoachMarks` | Close | No coach marks remain eligible |

Explicit router navigation is processed before the route DOM is removed and therefore uses the **Escape or navigation** snooze rule. Anchor disappearance without a router navigation event—for example a data refresh or conditional rerender—uses the no-snooze **Anchor disappears** rule. This event precedence is authoritative.

Each coach mark:

- is anchored to a visible, currently relevant control;
- never covers the target or essential content;
- shows progress such as `1 of 4`;
- can be dismissed permanently;
- closes on Escape and returns focus to the anchor;
- is deferred, not completed, when its anchor is unavailable;
- does not block unrelated UI interaction.

Short explanations for terms such as match score use on-demand help popovers, not coach marks. Popovers link to deeper help only when useful.

## Feedback and Overlay System

### Toast

Use for the result of a background or user-triggered action that does not require a decision.

- Success and informational toasts disappear after 5 seconds; warnings without an action after 8 seconds. Timers pause on hover, focus, or document visibility loss.
- Warning and error toasts with a recovery action remain until dismissed or resolved. Errors without a safe recovery action also remain until dismissed.
- Toasts expose an action such as Open, Undo, Configure, or Retry only when that action is valid.
- Notifications use a stable deduplication key `type:scope:code`. Repeats within 2 seconds update the existing toast and occurrence count instead of adding a new item. At most four toasts are visible; persistent errors are never evicted by transient success.
- Success/info use `aria-live="polite"`; blocking failure uses `assertive`. Announcements never move focus.

### Popover

Use for short contextual help or compact non-destructive controls. It closes on outside click or Escape, traps no focus, and returns focus to its trigger when closed by keyboard.

### Sheet

Use for onboarding steps, multi-field configuration, and decisions that must be completed or cancelled. Sheets use a visible title, concise consequence text, predictable Cancel placement, focus containment, and focus restoration.

### Overlay arbitration

The overlay manager owns one blocking-sheet slot. On mobile, the navigation menu uses that same slot with `kind=mobileMenu`; it is not a separate simultaneous owner. The manager also owns one optional child popover and at most one base-layer educational/context overlay (`coachMark` or base popover). Hover/focus tooltips are ephemeral but are suppressed on a coach-mark anchor while its coach mark is visible.

| Request | Arbitration rule |
|---|---|
| Open a sheet while no sheet exists | Close the current base-layer popover, then open the sheet |
| Open another sheet while a sheet exists | Reject the request and keep the current sheet; no silent replacement or queue |
| Open mobile menu while blocking-sheet slot is occupied | Reject; keep the current sheet and its child popover |
| Open mobile menu while slot is free | Close/snooze coach mark or close base popover, then claim the blocking-sheet slot; base content becomes inert |
| Open sheet, safety sheet, child/base popover, or coach mark while mobile menu owns slot | Reject/defer exactly as for any other occupied blocking sheet; menu content never hosts a child popover |
| Open help from inside a sheet | Open one child popover anchored inside that sheet; a new popover replaces the old popover only |
| Close a sheet | Close its child popover first, then restore focus to the sheet trigger if it still exists |
| Request a safety sheet while another sheet exists | Reject and show a warning toast; live action controls are not rendered inside onboarding/configuration sheets |
| Open a sheet or mobile menu while a coach mark is visible | Close and snooze the coach mark for the current session, then open the sheet/menu |
| Open a base popover while a coach mark is visible | Close and snooze the coach mark, then open the popover |
| Schedule a coach mark while a sheet, mobile menu, or base popover is visible | Defer it until the base layer is clear and the anchor passes visibility checks |
| Coach-mark anchor becomes covered, inert, disconnected, or off-screen | Close it without permanent completion and re-evaluate on a later stable render |
| Toast while any overlay exists | Allow it; toast never steals focus or changes overlay ownership |

Escape closes only the topmost dismissible layer: child popover, then base coach mark/popover, then blocking sheet/mobile menu. The onboarding resume form is rendered inside the onboarding sheet rather than opening the ordinary resume sheet. No flow depends on nested blocking sheets.

### Live-action safety sheet

Every real HH mutation opens a dedicated confirmation sheet built from a typed `LiveActionDescriptor`:

```text
operationType: apply | reply | campaign | cleanup | resume_account | api_lab
title: user-facing action name
consequence: exact live consequence
targetRows: ordered {key, label, safeValue} summaries with unique stable keys
preview: optional masked message/filter/body preview
riskFlags: stable safe codes and user-facing descriptions
acknowledgement: exact checkbox label
confirmLabel: exact button label
fingerprint: stable identifier for the reviewed plan/state
revalidate(): read-only or dry-run LiveValidationResult
execute(confirm: true): live request result
```

Required operation-specific row keys are:

| Type | Required `targetRows[].key` values |
|---|---|
| Apply | `vacancy`, `company`, `resume`, `letter` |
| Reply | `negotiation`, `employer`, `recipient`, `message` |
| Campaign | `run`, `count`, `filters`, `resume` |
| Cleanup | `object_type`, `count`, `criteria` |
| Resume/account | `resume_or_account`, `changes` |
| API Lab mutation | `method`, `path`, `params`, `body` |

`LiveValidationResult` has this discriminated shape:

```text
status: executable | changed | blocked | auth_required | capability_lost | error
fingerprint: current stable fingerprint
auth: {status: ready | missing | expired | error, safeMessage?: string}
capability: {available: boolean, code: string, safeMessage?: string}
blockers: ordered [{code: string, safeMessage: string}]
riskFlags: ordered [{code: string, safeMessage: string}]
canExecute: boolean
updatedDescriptor: complete LiveActionDescriptor when status is changed; absent otherwise
```

Validation status precedence is exact: return `error` when validation cannot produce a trustworthy result; otherwise `auth_required` when auth is not ready; otherwise `capability_lost` when capability is unavailable; otherwise `blocked` when blockers exist; otherwise `changed` when reviewed descriptor content or fingerprint changed; otherwise `executable`. Auth and capability summaries are transient validation evidence, not fields of `LiveActionDescriptor`, so their loss uses the dedicated statuses and never requires a replacement descriptor.

`status=changed` requires a complete `updatedDescriptor`, `canExecute=false`, and the current fingerprint. If the descriptor is absent or invalid, treat the result as `error`, keep the old reviewed descriptor visible, clear acknowledgement, and block execution. Any changed consequence, fingerprint, target row, preview, or risk flag must return `status=changed`; an `executable` result with changed reviewed descriptor content is invalid and blocks.

`canExecute` may be true only when status is `executable`, auth is ready, capability is available, blockers are empty, and the fingerprint exactly matches the acknowledged descriptor. A missing/duplicate required row key or missing revalidation function blocks the UI before confirmation. The destructive button remains disabled until the acknowledgement is selected. On click, call `revalidate()` first with the button busy. A valid changed result replaces the displayed descriptor, clears acknowledgement, and requires review again. Auth loss, capability loss, blocker, or validation error sends no mutation. Only a matching executable result calls `execute(confirm: true)`. The client sends the JSON boolean `true`, never a truthy string. A blocked/error response from the authoritative service guard stays in the sheet and never displays success. Cancelling or closing sends neither validation nor mutation.

### Inline error

Use when a specific panel or source failed. The failed region keeps its context, explains what did and did not complete, and provides Retry when safe. Other regions continue to load and function.

### Empty state

Explain why the region is empty and offer one relevant next step. Empty states must not imply that a sync or search succeeded when it did not.

## Component Boundaries

The implementation keeps data operations separate from presentation behavior.

| Unit | Responsibility | Inputs/outputs |
|---|---|---|
| App shell/router | Sidebar, canonical routes, legacy redirects, page title, responsive shell | pathname and navigation events → active destination |
| Today composer | Derive readiness and ordered daily actions while preserving loading/error/stale/time distinctions | `ResourceState` values plus injected `now` and IANA timezone → section view models |
| Onboarding controller | Determine the current step, persist presentation progress, call existing config/resume/sync APIs | readiness plus local progress → sheet state and save actions |
| Guidance controller | Schedule eligible coach marks and permanent dismissals | current route, anchors, progress → at most one visible coach mark |
| Overlay manager | Enforce sheet, child-popover, coach-mark, base-popover, mobile-menu, focus, and Escape arbitration | overlay requests plus current owner → accepted/rejected lifecycle result |
| Notification center | Queue, deduplicate, announce, expire, and action toasts | typed notification events → visible toast stack |
| Live-action registry | Build and validate operation-specific safety descriptors without owning server policy | domain action key/state → complete `LiveActionDescriptor` or blocked UI result |
| View loaders | Load each route or panel independently and expose loading/error/retry state | API functions → section state |
| Existing domain actions | Sync, score, update status, prepare letters, applications, HH operations | unchanged API contracts and safety guards |

No component owns both server data mutation policy and UI confirmation. Confirmation improves user understanding; server guards enforce safety.

`ResourceState<T>` is a discriminated union: `loading` carries no data/error and has `stale=false`; `ready` requires `data`, `stale`, and optional `updatedAt`; `error` requires safe error metadata and `stale`, and may carry last-known data only when `stale=true`. Consumers must exhaustively branch on status.

## Data Flow

1. Boot the shell and resolve the current route.
2. Load config/profile, resumes, source status, jobs, calendar/tasks, and agent approvals independently into `ResourceState` values with settled results and timestamps.
3. Render available regions immediately; skeleton only the regions still loading.
4. Derive the Today view model and onboarding readiness only after inspecting each resource status; errors produce `unknown`, not missing data.
5. While readiness is `loading`, open no onboarding UI. When it is `unknown`, render the recovery error and open no onboarding UI.
6. When readiness is stable and onboarding is deferred, render Today without opening the wizard; schedule guidance only if readiness is `ready`.
7. Otherwise apply the Wizard state-transition table in this precedence: `forceReview`, server version below 2, domain readiness incomplete, then ready. Fewer than three completed review steps open the first local step; three completed steps open Summary regardless of server version. A version-2 incomplete configuration opens the first missing domain step. Only version 2 plus ready state and no forced review proceeds directly to Today/guidance.
8. Mutations flow through existing API helpers. UI components emit typed success, warning, or error events to the notification center.
9. A successful mutation refreshes only affected view models. A failure preserves the user's input and offers a local retry.

## Visual and Responsive Rules

- Use the system font stack beginning with `-apple-system` and `BlinkMacSystemFont`.
- Keep a clear hierarchy between 28–30 px page titles, 15–17 px section titles, 13 px primary content, and 11–12 px metadata/control labels.
- Use an 8 px base spacing rhythm, restrained 8–16 px radii, fine neutral borders, and subtle elevation only for overlays or floating panels.
- Do not use decorative gradients, badges, glass panels, or pills as filler. Translucency is limited to the sidebar/titlebar and must retain contrast without backdrop-filter support.
- Use local SVG icons with one coherent stroke/fill family; emoji and text glyphs are not production icons.
- At viewport widths of 961 px and above, use the full 232 px sidebar.
- From 721–960 px, use a 72 px icon sidebar with accessible names and hover/focus tooltips.
- At 720 px and below, replace the sidebar with a compact top app bar and menu sheet; stack Today regions into one column, keep the primary action visible, and prevent horizontal scrolling.
- At 680×844, the Today title, readiness state, and **Find vacancies** action must all appear within the first 600 CSS pixels of document content without scrolling horizontally.
- Long tables may use a mobile row/list representation. Each mobile vacancy row must expose score, role, company, status, and salary/location summary; every desktop row/detail action remains reachable through the row or a labeled **Actions** menu using the same stable action identifier.
- Every interactive capability in the capability inventory receives a stable `data-action-id` independent of viewport and presentation. For seeded route state, desktop and mobile must expose equal action-ID sets after opening any labeled row **Actions** menu. This applies to Vacancies, Applications pipeline, Calendar events/tasks, Sources, Settings resumes/searches, agent approvals/runs/automation, and Advanced/API Lab surfaces—not only vacancy rows.
- At every acceptance viewport, `document.documentElement.scrollWidth <= document.documentElement.clientWidth`. Every visible interactive element's bounding box must fit within the viewport width, and opening the mobile menu must make all eight destinations keyboard reachable.

## Accessibility

- All navigation, rows, overlays, coach marks, and toast actions are keyboard reachable.
- Dialog sheets use native or equivalent dialog semantics, initial focus, focus containment, Escape handling where safe, and trigger focus restoration.
- Popovers and tooltips expose accessible relationships to their triggers.
- Icon-only controls have visible tooltips and accessible names.
- Color is never the only status signal.
- Minimum touch/click targets follow a practical 36–44 px control size depending on density.
- Animation is removed or reduced under `prefers-reduced-motion`.

## Error and Edge Cases

- Partial boot failure never aborts unrelated route initialization.
- Stale or deleted records keep unsaved form input and report the conflict locally.
- A coach-mark anchor removed during navigation closes the mark safely without advancing completion.
- A configured source that becomes temporarily unavailable stays enabled and satisfies source readiness, while its health warning offers Retry. A config-load/save failure makes readiness `unknown` and offers Retry or **Set up later**. Only optional HH setup offers **Skip**.
- A core source configuration request that fails makes readiness `unknown`; **Skip** is available only for optional HH setup, never for the requirement to enable one source.
- Closing a live-action sheet never counts as confirmation.
- Duplicate notifications merge instead of creating an unbounded stack.
- Local onboarding progress with an unknown future version is ignored safely; product configuration is never reset.
- Existing configured users are not forced to re-enter profile or secret values.
- Masked secrets remain display sentinels and are never persisted as actual credentials.

## Testing and Acceptance

### Contract tests

- Exact canonical route, recognized-query, invalid-identifier, and legacy redirect mapping.
- Route normalization covers duplicate recognized keys, forced legacy-state conflicts, canonical numeric spelling, default materialization, and source validation before/after source loading.
- `popstate` rehydrates canonical state without adding history, including Applications and Analytics tabs.
- New default config contains onboarding version 0; loading a legacy raw config without the key persists version 2 before default merge hides the distinction.
- Flat legacy profiles migrate atomically with absent, equal, and conflicting `profiles.default` cases; an invalid normalized active profile yields readiness `unknown` and no onboarding write.
- Presence of the eight navigation destinations and Today regions.
- Deterministic Focus/Fresh/Upcoming ordering covers status filters, invalid dates, published-to-fetched fallback, DST gap/overlap disambiguation, browser-local boundaries, more-than-30-day overdue records, and every category tie key.
- Local-only assets, system font stack, responsive rules, dialog semantics, toast live region, and reduced-motion support.
- No production `window.alert` or `window.confirm` remains for application feedback or live-action authorization.

### Browser tests

- Incomplete first launch opens the correct step and resumes it after reload.
- A genuine new install with prefilled default goals/sources still begins at Goal and requires confirmation of all three steps.
- A failed core readiness request reports `unknown` and never opens onboarding.
- Ready existing configuration skips the wizard.
- Set up later survives reload in the same tab, does not mark a step complete, and leaves a Today readiness action; a new tab/session may show the wizard.
- Run introduction again resets only presentation progress. A version-2 forced review resumes at its first incomplete step or Summary, **Finish review** clears the flag without sync/version save, and Set up later clears the forced review.
- Permanent progress synchronizes across tabs without sharing per-tab one-mark limits.
- Coach marks follow the state table, at most one new mark per session, defer missing anchors, and stay completed or dismissed.
- Keyboard focus, Escape, outside click, and focus restoration work for each overlay type.
- Overlay conflicts follow the arbitration table; rapid double submission produces one request.
- Coach anchors/prerequisites use the four stable IDs; navigation snoozes before DOM removal while data-rerender disappearance does not. Mobile menu mutually excludes the blocking-sheet slot and base overlays.
- Resume activation does not auto-close the wizard. Sync failure plus reload returns to Summary; **Continue to Today** persists version 2 without repeating sync.
- Successful sync plus failed version save persists `syncStatus=succeeded`; reload offers only Finish setup and cannot repeat sync. Retry/reset/success/failure transitions clear or replace finalization timestamp/error exactly as specified.
- Source toggles, temporary health failure, active HH account secret preservation/clear, profile-scoped resume GET, inactive create, activation, and exact current-profile readiness follow their defined paths.
- Create-success/activation-failure persists `pendingResumeActivationId`; retry and reload never create a duplicate and clear the ID only after verified activation or record disappearance.
- Success toast expires; actionable error remains and Retry runs only the failed operation.
- One failed boot request does not prevent other Today regions from rendering.
- Live HH action sends no request on Cancel and sends literal `confirm: true` only after acknowledgement.
- Apply, reply, campaign, cleanup, resume/account, and API Lab descriptors render their required summaries. Changed fingerprint/content requires a complete valid replacement descriptor and fresh acknowledgement; missing replacement or lost auth/capability sends no mutation.
- Today, Vacancies, Applications, and Settings retain core workflows after navigation reorganization.
- Viewports 1440×900, 900×900, and 680×844 cover full sidebar, compact sidebar, and single-column modes. Assert the scroll-width and first-600-pixel rules, all eight mobile destinations, and equality of stable `data-action-id` sets for every seeded destination/surface named in the capability inventory.

### Safety regression

All existing web security, HH confirmation, secret masking, auth preflight, resume editing, job status, and partial-load tests remain passing. The UI is not accepted if visual confirmation becomes the only mutation guard.

## Out of Scope

- Replacing the Python server or service/storage architecture.
- New job-source integrations.
- Autonomous live application behavior.
- Cloud accounts, multi-user synchronization, telemetry, or remote analytics.
- A native macOS binary.
- Rewriting existing domain APIs solely to support presentation.

## Acceptance Criteria

The redesign is ready when:

1. a new user can configure a search goal, choose sources, select/create a resume, and launch the first search through the three-step wizard;
2. a configured user lands on Today and can identify the next useful action without opening another section;
3. every existing major capability remains reachable through the new information architecture;
4. feedback and help use the defined component hierarchy consistently;
5. real HH actions remain explicitly described, auditable, and server-guarded;
6. keyboard, reduced-motion, partial-error, narrow-viewport, and legacy-route tests pass;
7. the rendered UI matches the typography, color semantics, spacing, component anatomy, breakpoints, and interaction state tables in this specification; this document is the acceptance source of truth, while brainstorming mockups are illustrative only.
