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

Legacy routes use `history.replaceState` so browser history does not contain a redundant redirect entry. The mapping is exact:

| Incoming path | Canonical state |
|---|---|
| `/` or `/today` | Today |
| `/jobs` | Vacancies; preserve recognized `source`, `min_score`, `status`, and `job` query values |
| `/favorites` | Vacancies with `filter=saved`; merge recognized incoming query values |
| `/calendar` | Calendar; preserve an `event` query value |
| `/chat` | Assistant; preserve a `job` query value |
| `/agent` | Applications with `tab=agent`; preserve recognized `approval`, `run`, and `operation` query values |
| `/settings` | Settings; preserve recognized `section` query values |
| `/sources` | Sources |
| `/stats` | Analytics with `tab=overview` |
| `/trends` | Analytics with `tab=trends` |

Unknown query keys and hash fragments remain in the URL but do not affect UI state. Invalid recognized identifiers are removed and reported through a non-blocking warning toast. The router never converts an invalid identifier into a different valid record.

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
2. overdue then due-today tasks or events, earliest due time first;
3. `new` vacancies with score at least 70, highest score first;
4. incomplete readiness actions in the order goal, source, resume;
5. source failures, most recent failure first.

Take the first three after sorting. Ties use the stable numeric record ID ascending. Fresh matches use `status == "new"`, then total score descending, `published_at` or `fetched_at` descending, and ID descending. There is no separate `unseen` concept in this redesign.

Each Today input is a `ResourceState<T>` with `status` (`loading`, `ready`, or `error`), optional `data`, optional safe error metadata, optional `updatedAt`, and `stale: boolean`. Source loaders derive `stale` from backend-reported last-sync/error state; other resources default it to `false`. The composer receives these resource states, not bare arrays, so loading, genuine empty, stale, and failed inputs cannot be confused.

## First-Run Onboarding

### Readiness state

Readiness begins as `loading`. It becomes:

- `ready` only when config/profile, source configuration, and resumes loaded successfully and all three core conditions are satisfied;
- `incomplete` only when those three requests loaded successfully and at least one condition is absent;
- `unknown` when any core readiness request failed.

The three core conditions are: at least one non-empty desired role or search query, at least one enabled source, and one active resume. HH authentication and AI credentials are recommendations, not readiness requirements.

The wizard never auto-opens while readiness is `loading` or `unknown`. `unknown` renders a Today readiness error with Retry. `incomplete` opens the first incomplete step unless onboarding was deferred for the current tab session. If the product data later stops satisfying a completed step, readiness returns to `incomplete`; presentation progress never overrides domain truth.

### Persistence schema

Permanent presentation state is JSON in `localStorage["work-hunter:onboarding:v2"]`:

```json
{
  "version": 2,
  "completed": false,
  "completedSteps": ["goal"],
  "completedCoachMarks": [],
  "dismissedCoachMarks": [],
  "updatedAt": "2026-07-13T12:00:00Z"
}
```

Current-tab state is JSON in `sessionStorage["work-hunter:guidance-session:v2"]`:

```json
{
  "onboardingDeferred": false,
  "coachMarkShown": false,
  "snoozedCoachMarks": []
}
```

Malformed, wrong-version, or wrong-shape values are ignored and replaced with defaults; they never change product configuration. Permanent changes are reconciled across tabs through the `storage` event. The one-new-mark limit is per tab session. Settings → **Run introduction again** clears both keys in the current tab, writes the default permanent state for other tabs, and leaves domain configuration untouched.

**Set up later** sets `onboardingDeferred=true` in session storage and closes the wizard until that tab session ends; it does not complete or skip any step. There is no per-step skip for the three core requirements. Only optional HH setup can be skipped. Completion requires all three domain readiness conditions.

### Wizard state transitions

| Current state/event | Required behavior |
|---|---|
| Boot, readiness `loading` | Render shell and section skeletons; do not open a sheet |
| Readiness `unknown` | Render Today readiness error with Retry; do not infer missing configuration |
| Readiness `ready` | Mark wizard completed, render Today, then schedule guidance |
| Readiness `incomplete` and not deferred | Open first incomplete step |
| **Set up later** | Set session deferral, close sheet, keep readiness action visible |
| Step submit | Disable all submit paths for that step, show busy state, send one request |
| Step request success | Reload affected resource, verify the domain condition, record step completion, advance |
| Step request failure or failed verification | Stay on the step, preserve input, restore controls, show inline error and Retry |
| Final sync success | Mark completed, close sheet, route to Today, show success toast |
| Final sync failure | Keep completed settings, stay on the summary with Retry and **Continue to Today**; only the latter or a later successful sync closes the wizard |

### Step 1 — Search goal

Collect and save only the minimum useful profile fields through the existing config update path:

- desired role/search query;
- preferred work format;
- minimum monthly salary.

The screen explains that detailed skills, exclusions, and locations can be edited later.

Validation requires a trimmed role/query, a recognized work-format value, and a non-negative integer salary. Submit updates the in-memory masked config snapshot and sends one `POST /api/config`. Only a 2xx response followed by a successful config reload can complete the step. A rapid double click cannot create a second request.

### Step 2 — Sources and HH

- Show available sources and their current enabled/connection state.
- Allow enabling public sources without credentials by updating the masked config snapshot through one `POST /api/config`.
- Require at least one enabled source before Continue is enabled.
- Present HH credentials as an optional expandable area with an explanation of what access enables. Cancel discards unsaved credential edits; masked sentinels are never submitted as new credentials.
- After a successful config save, **Check HH connection** explicitly calls `GET /api/agent/preflight?live_auth=true`. Failure stays in the optional area and never blocks core completion.
- This redesign does not introduce an OAuth browser redirect or callback. If OAuth is added later, it requires a separate design and safety review.

### Step 3 — Resume and first search

- Load resumes through `GET /api/resumes`. Select an existing resume and activate it through `POST /api/resumes/{id}/activate`, or render the existing local resume fields inside the onboarding sheet and create it with one `POST /api/resumes`.
- Show a readiness summary.
- Resume name and non-empty body are required. Submit is locked until its request settles. A successful create is followed by resume reload and explicit activation verification before the step completes.
- Finish with **Find first vacancies**, which sends one `POST /api/sync` with `{"score": true}` and follows the final-sync transitions above.
- Failure to sync does not roll back completed settings or create a duplicate resume; show an inline error with Retry.

## Contextual Guidance

After the setup sheet, define exactly four ordered coach marks and show no more than one previously unseen mark per tab session.

1. Find vacancies on Today.
2. Read the vacancy match score.
3. Save, hide, or move a vacancy into Applications.
4. Review the safety sheet before a real HH action.

The scheduler scans the ordered list and chooses the first mark whose prerequisite is satisfied and whose anchor is visible on the current route. An unavailable anchor remains pending; it is neither completed nor dismissed and does not prevent a later currently eligible mark from appearing. Once any new mark has appeared, `coachMarkShown=true` prevents another new mark in that tab session.

Coach-mark actions have explicit semantics:

| Action | Permanent state | Current session | Ordering effect |
|---|---|---|---|
| **Got it** | Add ID to `completedCoachMarks` | Close | Mark is no longer eligible |
| Close button / **Do not show again** | Add ID to `dismissedCoachMarks` | Close | Mark is no longer eligible |
| Escape or navigation | No permanent change | Add ID to `snoozedCoachMarks`, close | Mark may return next tab session |
| Anchor disappears | No permanent change | Close without snoozing | Re-evaluate on the next stable render |
| **Skip introduction** | Add all four IDs to `dismissedCoachMarks` | Close | No coach marks remain eligible |

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

The overlay manager owns one blocking sheet and one optional child popover.

| Request | Arbitration rule |
|---|---|
| Open a sheet while no sheet exists | Close the current base-layer popover, then open the sheet |
| Open another sheet while a sheet exists | Reject the request and keep the current sheet; no silent replacement or queue |
| Open help from inside a sheet | Open one child popover anchored inside that sheet; a new popover replaces the old popover only |
| Close a sheet | Close its child popover first, then restore focus to the sheet trigger if it still exists |
| Request a safety sheet while another sheet exists | Reject and show a warning toast; live action controls are not rendered inside onboarding/configuration sheets |
| Toast while any overlay exists | Allow it; toast never steals focus or changes overlay ownership |

The onboarding resume form is rendered inside the onboarding sheet rather than opening the ordinary resume sheet. No flow depends on nested blocking sheets.

### Live-action safety sheet

Every real HH mutation opens a dedicated confirmation sheet before the request is sent. It shows:

- vacancy and company;
- selected resume;
- cover-letter presence and length;
- the exact live consequence;
- risk flags or blockers;
- an explicit acknowledgement control.

The destructive button remains disabled until the acknowledgement is selected. The client sends the JSON boolean `true`, never a truthy string. The service and transport guards remain authoritative. Cancelling or closing the sheet sends no mutation request.

### Inline error

Use when a specific panel or source failed. The failed region keeps its context, explains what did and did not complete, and provides Retry when safe. Other regions continue to load and function.

### Empty state

Explain why the region is empty and offer one relevant next step. Empty states must not imply that a sync or search succeeded when it did not.

## Component Boundaries

The implementation keeps data operations separate from presentation behavior.

| Unit | Responsibility | Inputs/outputs |
|---|---|---|
| App shell/router | Sidebar, canonical routes, legacy redirects, page title, responsive shell | pathname and navigation events → active destination |
| Today composer | Derive readiness and ordered daily actions while preserving loading/error/stale distinctions | `ResourceState` values for profile, sources, resumes, jobs, tasks, events, approvals → section view models |
| Onboarding controller | Determine the current step, persist presentation progress, call existing config/resume/sync APIs | readiness plus local progress → sheet state and save actions |
| Guidance controller | Schedule eligible coach marks and permanent dismissals | current route, anchors, progress → at most one visible coach mark |
| Overlay manager | Enforce the sheet/child-popover arbitration table, focus handling, Escape behavior, and restoration | overlay requests plus current owner → accepted/rejected lifecycle result |
| Notification center | Queue, deduplicate, announce, expire, and action toasts | typed notification events → visible toast stack |
| View loaders | Load each route or panel independently and expose loading/error/retry state | API functions → section state |
| Existing domain actions | Sync, score, update status, prepare letters, applications, HH operations | unchanged API contracts and safety guards |

No component owns both server data mutation policy and UI confirmation. Confirmation improves user understanding; server guards enforce safety.

## Data Flow

1. Boot the shell and resolve the current route.
2. Load config/profile, resumes, source status, jobs, calendar/tasks, and agent approvals independently into `ResourceState` values with settled results and timestamps.
3. Render available regions immediately; skeleton only the regions still loading.
4. Derive the Today view model and onboarding readiness only after inspecting each resource status; errors produce `unknown`, not missing data.
5. If readiness is `incomplete` and not deferred for the session, open the first incomplete onboarding step. `loading` and `unknown` never open it automatically.
6. Otherwise render Today and schedule the next eligible contextual coach mark after its anchor exists.
7. Mutations flow through existing API helpers. UI components emit typed success, warning, or error events to the notification center.
8. A successful mutation refreshes only affected view models. A failure preserves the user's input and offers a local retry.

## Visual and Responsive Rules

- Use the system font stack beginning with `-apple-system` and `BlinkMacSystemFont`.
- Keep a clear hierarchy between 28–30 px page titles, 15–17 px section titles, 13 px primary content, and 11–12 px metadata/control labels.
- Use an 8 px base spacing rhythm, restrained 8–16 px radii, fine neutral borders, and subtle elevation only for overlays or floating panels.
- Do not use decorative gradients, badges, glass panels, or pills as filler. Translucency is limited to the sidebar/titlebar and must retain contrast without backdrop-filter support.
- Use local SVG icons with one coherent stroke/fill family; emoji and text glyphs are not production icons.
- At viewport widths of 961 px and above, use the full 232 px sidebar.
- From 721–960 px, use a 72 px icon sidebar with accessible names and hover/focus tooltips.
- At 720 px and below, replace the sidebar with a compact top app bar and menu sheet; stack Today regions into one column, keep the primary action visible, and prevent horizontal scrolling.
- Long tables may use a focused mobile row/list representation, but data and actions remain equivalent.

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
- A source that becomes unavailable during onboarding remains incomplete and offers Retry or **Set up later**. Only optional HH setup offers **Skip**.
- A core source configuration request that fails makes readiness `unknown`; **Skip** is available only for optional HH setup, never for the requirement to enable one source.
- Closing a live-action sheet never counts as confirmation.
- Duplicate notifications merge instead of creating an unbounded stack.
- Local onboarding progress with an unknown future version is ignored safely; product configuration is never reset.
- Existing configured users are not forced to re-enter profile or secret values.
- Masked secrets remain display sentinels and are never persisted as actual credentials.

## Testing and Acceptance

### Contract tests

- Exact canonical route, recognized-query, invalid-identifier, and legacy redirect mapping.
- Presence of the eight navigation destinations and Today regions.
- Local-only assets, system font stack, responsive rules, dialog semantics, toast live region, and reduced-motion support.
- No production `window.alert` or `window.confirm` remains for application feedback or live-action authorization.

### Browser tests

- Incomplete first launch opens the correct step and resumes it after reload.
- A failed core readiness request reports `unknown` and never opens onboarding.
- Ready existing configuration skips the wizard.
- Set up later survives reload in the same tab, does not mark a step complete, and leaves a Today readiness action; a new tab/session may show the wizard.
- Run introduction again resets only presentation progress.
- Permanent progress synchronizes across tabs without sharing per-tab one-mark limits.
- Coach marks follow the state table, at most one new mark per session, defer missing anchors, and stay completed or dismissed.
- Keyboard focus, Escape, outside click, and focus restoration work for each overlay type.
- Overlay conflicts follow the arbitration table; rapid double submission produces one request.
- Success toast expires; actionable error remains and Retry runs only the failed operation.
- One failed boot request does not prevent other Today regions from rendering.
- Live HH action sends no request on Cancel and sends literal `confirm: true` only after acknowledgement.
- Today, Vacancies, Applications, and Settings retain core workflows after navigation reorganization.
- Viewports 1440×900, 900×900, and 680×844 cover full sidebar, compact sidebar, and single-column modes with no clipped primary content or horizontal overflow.

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
