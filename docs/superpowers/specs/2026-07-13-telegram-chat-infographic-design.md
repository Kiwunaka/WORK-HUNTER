# Telegram Chat Retrospective — Design Specification

## Goal

Create one polished, fully offline HTML infographic for the Telegram export covering 2026-07-11 00:00:49 through 2026-07-13 19:03:25. The page must turn the locked dataset in this specification into a readable, link-rich editorial experience with restrained motion and code-native SVG data graphics.

The final deliverable is a single file:

`telegram-chat-retrospective-2026-07-11-13.html`

It must open directly from disk and require no server, CDN, external font, image, script, stylesheet, or data file. Telegram message URLs are the only allowed external destinations and are visited only after an explicit click.

## Approved Art Direction

Direction: **Editorial Dossier**.

Visual reference: `docs/design-references/telegram-chat-infographic/editorial-dossier-reference.png`.

The page uses a warm mineral-paper field, near-black typographic slabs, cobalt blue, and signal vermilion. The composition is an asymmetric editorial split with oversized numerals, strong rules, deliberately uneven card spans, forensic annotations, and large empty areas. It should feel like a premium investigative magazine crossed with a data atlas, not a SaaS dashboard.

Avoid:

- purple gradients, generic glassmorphism, neon cyberpunk, and stock AI imagery;
- symmetric three-column dashboard grids;
- generic gray card borders and harsh drop shadows;
- Inter, Roboto, Arial, Helvetica, Font Awesome, Material Icons, or CDN assets;
- decorative animation without information value.

Offline typography uses Windows-native stacks: Bahnschrift/Aptos/Segoe UI Variable for display and UI copy, with Georgia as a restrained editorial accent. The page remains readable when only generic `sans-serif` and `serif` fallbacks exist.

## Source Facts and Data Boundaries

The authoritative input is:

- path: `C:\Users\kiwun\Downloads\Telegram Desktop\ChatExport_2026-07-13 (1)\result.json`;
- size: 2,085,892 bytes;
- SHA-256: `BE65A707D8AC48A775998D745A91D3400720EB36996226F0AB4DD09494BCC0BF`;
- Telegram chat name: `Чат Котенков и Горь`;
- chat ID: `1778093200`.

The page embeds a reviewed static snapshot from this file. It does not read the JSON at runtime and does not depend on a prose summary as a source of truth.

Export timestamps contain no explicit offset. For display, treat them as the local timezone supplied with the task, `Europe/Moscow` (`UTC+03:00`), and label the range `11 июля, 00:00 — 13 июля, 19:03 MSK`.

Headline facts:

- 2,743 messages;
- 5,088 total reactions;
- 758 reacted messages;
- 67.04 hours covered;
- 268 unique sender IDs;
- 1,858 reply messages;
- 85 channel-originated messages receiving 2,047 reactions;
- 313 photos, 58 stickers, and 22 animations were not included as viewable media.

Reaction totals sum every exported emoji, custom emoji, and paid reaction count. Poll voters are excluded from reaction totals. External claims in chat messages are presented as statements made in the chat, not independently verified facts.

Every quoted or ranked message uses the canonical link shape:

`https://t.me/c/1778093200/<message_id>`

## Locked Content Dataset

The implementation uses the following fixed content. No model judgment or runtime ranking is allowed to substitute different messages.

### Topic chapters

1. **Модели и лимиты** — defaults should start around medium/high and increase only when needed; anchor message `497642`.
2. **AI-2040** — the scenario proposes US–China coordination, chip auditing, controlled data centers, and UBI; the selected objections concern geopolitical incentives and engineering feasibility; anchor messages `497797`, `497798`, `497840`, and `497855`.
3. **Пол модели** — poll results and the resulting comedy thread; anchor messages `498677`, `498680`, and `498682`.
4. **AtCoder** — the post reports a system close to GPT-5.6 winning both rounds and leading on 1,997 of 2,000 heuristic tests; anchor message `499375`.
5. **Открытые веса** — poll expectations for the first jurisdiction to impose a threshold restriction; anchor message `499557`.
6. **Харнессы и безопасность** — the same model behaves differently in Codex and Claude Code, followed by remote-session and context-bomb threads; anchor messages `500156`, `500250`, `499892`, and `500369`.

### Top-10 reaction ledger

Sort order is descending total reaction count, then descending message ID for a tie. Each row is locked as follows:

| Rank | ID | Total | Label | Primary reaction detail |
|---:|---:|---:|---|---|
| 1 | 497797 | 419 | AI-2040, часть 1 | 🤡 213 |
| 2 | 497798 | 369 | AI-2040, часть 2 | 🤡 178 |
| 3 | 498680 | 329 | «Мне русские модели сами пишут» | 😁 313 |
| 4 | 497477 | 307 | Иск Apple к OpenAI | 🤯 136 |
| 5 | 499982 | 261 | Суперфорекастер о сроках GPT-6 | 🤔 156 |
| 6 | 499375 | 210 | Модель на AtCoder | ❤‍🔥 104 |
| 7 | 498677 | 174 | Опрос о поле модели | 🤔 89 |
| 8 | 499557 | 168 | Опрос о запрете открытых весов | 🤡 92 |
| 9 | 498682 | 148 | «После третьего сообщения кинула бы в ЧС» | 😁 141 |
| 10 | 497823 | 87 | «Папа, а что ты делал, когда рождался AGI?» | 😁 76; 🤣 9 |

### Joke index

The joke score is the sum of `😁`, `🤣`, and `😂` reaction counts. The locked selection excludes channel-originated posts, one-word replies, and replies whose necessary parent is outside the export. Display in descending joke score, then descending total reactions, then ascending message ID. Displayed copy below is verbatim except that line breaks may become spaces:

| ID | Joke score | Total | Quote label |
|---:|---:|---:|---|
| 498680 | 313 | 329 | Мне русские модели сами пишут, я не пишу первый |
| 498682 | 141 | 148 | Если писать ей как девушке -  после второго сообщения она бы не отвечала. После третьего кинула бы в чс |
| 497823 | 85 | 87 | — Папа, а что ты делал, когда рождался AGI? — Мониторил ситуацию — Fucking legend |
| 497511 | 47 | 47 | «Абсолютно все в этой комнате было ворованное, и даже воздух был какой-то спертый» |
| 498690 | 25 | 25 | Называю все модели Игорь. Запрещаю делать rm fm |
| 498732 | 25 | 25 | Вместо «как дела» пишу «кд», сэкономленное время посвящаю саморазвитию |
| 497480 | 22 | 23 | А в чем прикол воровать у эпла их документацию? Типа дизайн доки? Или что? Почему тогда у приложения все равно такой уебский интерфейс? Непонятно… |
| 498722 | 19 | 19 | Ого, что ты делаешь с 0,12 сэкономленных секунд? |

### Grails and hidden diamonds

These eight entries are editorially locked. `Under-reacted` means every primary message has at most nine reactions; paired follow-ups remain visible when they complete the finding.

1. `workflow` — `500156` (9) + `500250` (8): same Sol Max model, Claude Code digs deeper but costs about 5× time/tokens; use it only for difficult work.
2. `infrastructure` — `499892` (0) + `499896` (1): persistent remote Codex through SSH/tmux and app/phone handoff.
3. `workflow` — `498539` (0): Opus/Fable for orchestration, Codex for detailed plan review.
4. `method` — `498540` (5): prefer ML over AI, heuristics over ML, and regex over heuristics when sufficient.
5. `method` — `498794` (8): a 90-minute source video became a 15-minute edit after about two hours, with one reported audio-cut error.
6. `method` — `497846` (3) + `498345` (1): real automation often needs good data and simple algorithms; AI frequently writes code for established systems.
7. `method` — `500329` (3): assistants optimize instructions without accounting for whether a human will actually execute them.
8. `theory` — `498087` (1): selected long-form critique of the AI-2040 control assumptions.

### Poll data and rounding

Gender poll `498677`, denominator 9,183:

- exclusively masculine: 6,663;
- mostly masculine: 680;
- 50/50: 311;
- mostly feminine: 255;
- exclusively feminine: 236;
- `паркет или кафель`: 1,038.

Derived figures use the full 9,183 denominator: exclusively masculine 72.6%; exclusively + mostly masculine 80.0%; mostly + exclusively feminine 5.3%; joke option 11.3%. Keep 50/50 separate.

Open-weight poll `499557`, denominator 6,066:

- US by end of 2026: 1,143;
- China by end of 2026: 656;
- US through June 2027: 834;
- China through June 2027: 408;
- US after June 2027: 283;
- China after June 2027: 115;
- never: 830;
- view results: 1,797.

The substantive denominator excludes only `view results`: 4,269. Aggregate US = 2,260 (52.9%), China = 1,179 (27.6%), never = 830 (19.4%). Percentages are `count / denominator * 100`, rounded to one decimal with ordinary half-up behavior.

### SVG chart encodings

All axes start at zero; every chart prints exact counts beside its visual encoding.

- **Daily message bars:** 11 July = 930, 12 July = 1,073, 13 July = 740; fixed linear axis 0–1,100; 13 July is visibly labeled `неполный день, до 19:03 MSK`.
- **Reacted share ring:** reacted = 758 (27.6%), unreacted = 1,985 (72.4%), denominator 2,743.
- **Channel-skew comparison:** message counts channel/native = 85/2,658 (3.1%/96.9%); reaction counts channel/native = 2,047/3,041 (40.2%/59.8%). Two separately normalized 100% bars prevent count-scale confusion.
- **Top-10 reaction composition:** denominator 2,472 reactions across the locked top ten; `😁` 530, `🤡` 505, `🤔` 301, `👍` 287, `🤯` 196, `❤‍🔥` 183, all other exported reaction types 470. One 100% stacked strip uses this fixed order and labels count plus percentage rounded to one decimal with the same half-up rule as the polls.
- **Ranking bars:** total reactions per locked top-10 row, linear scale 0–419.
- **Poll bars:** raw option counts on a zero-based scale within each poll; aggregated derived figures appear in text and are not mixed into the raw-option axis.

## Information Architecture

### 1. Opening Dossier

- Full-width editorial hero with the title, date range, and a compact TL;DR.
- Oversized `2,743` and `5,088` numerals form the primary visual anchor.
- Code-native SVG plates implement the locked daily-message, reacted-share, and channel-skew encodings above.
- A small scope note explains that the export covers 67 hours and that media files were absent.

### 2. What Happened

- The six locked topic chapters from `Locked Content Dataset`.
- Chapters use a staggered editorial timeline rather than uniform cards.
- Each chapter includes its locked takeaway and links every anchor message listed for that chapter.

### 3. Reaction Ledger

- Exact top-10 ranking by summed reactions.
- Each row contains rank, reaction count, short label, primary reaction detail, and a Telegram deep link.
- The first two AI-2040 rows are visually bracketed to show that they are two parts of one post.
- The locked top-10 reaction-composition strip makes clear that high engagement is not the same as approval without inferring sentiment beyond the exported emoji categories.

### 4. Poll Plates

- Gender poll: 9,183 voters; 72.6% exclusively masculine; about 80% predominantly masculine; 5.3% female in either form; 11.3% `паркет или кафель`.
- Open-weight poll: among substantive answers, about 53% expect the US to ban first, 28% China, and 19% no ban.
- The raw counts, denominators, aggregations, and rounding rules from `Poll data and rounding` render as accessible SVG bars with exact text labels beside them.

### 5. Joke Index

- The eight locked text jokes, ordered by the defined joke score and tie-breakers.
- Quotes remain short and retain the original tone, including profanity.
- Cards behave like linked editorial pull quotes, not chat bubbles.

### 6. Grails and Hidden Diamonds

- The eight locked under-reacted entries from `Grails and hidden diamonds`.
- A faceted SVG diamond/constellation groups these into `workflow`, `infrastructure`, `method`, and `theory`.
- Every entry shows its low reaction count to make the under-attention thesis explicit.
- Every primary and paired follow-up message ID is a Telegram deep link.

### 7. Method and Caveats

- Explain reaction summation, channel skew, missing parent messages, absent media, and lack of external fact-checking.
- End with a compact source line and a control that copies the export date range or jumps back to the top.

## Component Boundaries

Canonical content is semantic HTML authored in the document. JavaScript never creates, replaces, or duplicates rankings, quotes, links, poll labels, gems, or caveats. Numeric elements expose reviewed values through `data-*` attributes only for visual enhancement.

The single HTML file contains independent enhancement units:

- `initMetricCounters()`: animates already-visible metric text from zero to each element's `data-value`, then restores the exact authored string.
- `initChartReveals()`: reveals already-authored inline SVG bars and paths; axes, labels, values, and fallback geometry exist in static markup.
- `initRevealMotion()`: applies IntersectionObserver-based entry animation.
- `initDossierNav()`: controls the detached section navigator and reading-progress state.
- `initLinkActions()`: handles copy/open affordances while preserving normal anchors.
- `setReducedMotionMode()`: disables nonessential motion when `prefers-reduced-motion` is active.

Each initializer is isolated in its own function, accepts a root element or selector, and returns without side effects when its target is absent. Failure of any initializer must not hide content, alter numeric truth, or break links.

## Motion System

Motion communicates reading order and data relationships:

- hero numerals count up once after first paint;
- SVG paths draw in with dash-offset animation;
- ranking bars reveal from their origin using transforms;
- topic chapters enter with staggered opacity/translate interpolation;
- the detached dossier navigator updates section state through IntersectionObserver;
- all timing uses custom cubic-bezier curves with a heavy, physical finish.

No animation changes `top`, `left`, `width`, or `height`. Continuous scroll listeners are prohibited. No large scrolling element uses blur. `prefers-reduced-motion: reduce` shows the final visual state immediately.

## Interaction Design

- Telegram deep links remain ordinary semantic anchors and open in a new tab.
- Every new-tab anchor uses `rel="noopener noreferrer"`.
- Hover/focus states reveal a nested arrow island and the full message ID.
- The detached navigator jumps to major sections and exposes the current section.
- A copy-link control copies a message URL when the Clipboard API is available; the underlying anchor remains the fallback.
- Interactive touch targets are at least 44×44 CSS pixels.
- Keyboard navigation follows DOM order and has visible high-contrast focus treatment.

## Responsive Behavior

- Desktop uses the asymmetric editorial split and varied spans.
- Below 900 px, hero and data plates collapse to a two-column reading sequence.
- Below 700 px, all major blocks become one column; visual rotations and overlaps are removed.
- The detached navigator becomes a compact horizontal section rail.
- Typography uses `clamp()` and never depends on fixed viewport height.
- No content or Telegram link is hidden on mobile.

## Accessibility and Progressive Enhancement

- Semantic headings, landmarks, ordered lists, blockquotes, and anchors are present in the initial HTML.
- The root element declares `lang="ru"`.
- SVG graphics include titles/descriptions or are marked decorative when duplicated by visible text.
- Color is never the only carrier of meaning.
- Static content remains usable with JavaScript disabled.
- Clipboard status uses a visually integrated `aria-live="polite"` region; it is not the only indication that an action occurred.
- Contrast targets WCAG AA for body text and controls.

## Performance and Offline Constraints

- One HTML file, with all CSS, JavaScript, SVG, and data inline.
- A meta Content Security Policy is exactly `default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:; connect-src 'none'; font-src 'none'; media-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'`. It does not block explicit user navigation through Telegram anchors.
- No network request on load and no unbounded `requestAnimationFrame`, timer, WebSocket, EventSource, or polling loop.
- No base64 raster assets are needed in the final page; the imagegen board is a design reference only.
- Animations use transforms, opacity, and bounded SVG stroke effects.
- IntersectionObserver replaces continuous scroll handlers.
- The uncompressed HTML target is at most 300 KB. Initial enhancement work completes in bounded passes; no single authored loop processes more than the page's fixed visible element set.

## Error Handling

- JavaScript is progressive enhancement; static HTML is the fallback.
- Clipboard failures leave the Telegram anchor functional and show a short nonblocking status message when scripting is available.
- Missing browser support for IntersectionObserver reveals all content immediately.
- Unsupported SVG animation leaves complete static charts.

## Verification Plan

1. Parse the final HTML and verify it is structurally valid and contains one document.
2. Statically verify no external `script`, stylesheet `link`, `@import`, CSS `url()`, `img/src`, `srcset`, media source, iframe/frame, object/embed, SVG external reference, preload/prefetch, module import, fetch, XHR, WebSocket, EventSource, or `sendBeacon` can reference a remote asset.
3. Before reading source records, assert the authoritative JSON size and SHA-256 from `Source Facts and Data Boundaries`; fail verification on mismatch.
4. Verify every Telegram URL has the correct chat ID and references a message ID present in the source export.
5. Recompute headline totals, daily totals, reacted/unreacted counts, channel/native message and reaction counts, top-10 ordering and reaction composition, raw poll counts and derived percentages, joke scores, and every gem reaction count from the JSON. Compare them with authored HTML text, SVG values, and canonical `data-*` attributes; there is no JavaScript `DATA` object.
6. Check JavaScript syntax with the available Node runtime.
7. Render and inspect desktop (1440 px), tablet (900 px), and mobile (390 px) views.
8. Exercise navigation, deep links, keyboard focus, copy controls, the clipboard live region, and reduced-motion mode.
9. In a browser test, record requests during initial `file://` load and assert zero HTTP(S), WebSocket, or other external requests.
10. Inspect for clipping, horizontal overflow at 390 px, illegible text, broken SVG, or animation jank before handoff.

## Out of Scope

- Live JSON loading or a reusable dashboard framework;
- server hosting, analytics, authentication, or database storage;
- external fact-checking of news and technical claims in the chat;
- embedding missing Telegram media;
- editing or integrating the existing `work_hunter` application.
