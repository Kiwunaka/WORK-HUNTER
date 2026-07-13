# Apple HIG UI Design Inventory

## Accepted references

- `today-reference.png` — primary 1440×900 command-center composition.
- `onboarding-reference.png` — three-step setup sheet and field anatomy.
- `safety-states-reference.png` — live-action sheet, help popover, empty state, and recoverable error.

The references are visual guides; code-native Russian copy and the approved design spec remain authoritative when generated lettering differs.

## Color lock

- App background: cool gray `#f5f5f7`, never cream or beige.
- Primary surface: `rgba(255, 255, 255, 0.86)` with solid `#ffffff` fallback.
- Primary text: `#1d1d1f`.
- Secondary text: `#6e6e73`.
- Separator: `rgba(60, 60, 67, 0.18)`.
- Accent: `#0a84ff`; hover `#0071e3`.
- Success: `#34c759`; attention: `#ff9f0a`; destructive: `#ff453a`.
- Sidebar: translucent cool white/gray with blur; no tinted color wash.

## Typography

- Family: `-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`.
- Page title: 29 px / 1.15 / 700 / `-0.03em`.
- Section title: 15–17 px / 1.25 / 650.
- Primary content: 13 px / 1.45 / 450–600.
- Control text: 12–13 px / 1.2 / 550–650.
- Metadata: 11–12 px / 1.4 / 450.
- No uppercase utility labels except compact table headings.

## Spacing and geometry

- Base rhythm: 8 px; common gaps: 8, 12, 16, 24, 32 px.
- Full sidebar: 232 px; compact sidebar: 72 px.
- Controls: 8–10 px radius; panels/sheets: 14–18 px radius.
- Primary targets: 36–44 px high depending on density.
- Shadows only on overlays and a small number of elevated panels.
- Tables and semantic lists remain open and dense; do not turn them into a bento grid.

## Allowed first-viewport copy

- Navigation: `Сегодня`, `Вакансии`, `Отклики`, `Календарь`, `Ассистент`, `Аналитика`, `Источники`, `Настройки`.
- Title: `Доброе утро`.
- Summary template: `<weekday>, <date> · <count> действия требуют внимания`.
- Primary action: `Найти вакансии`.
- Readiness: `Всё готово к поиску` or a truthful incomplete/unknown variant.
- Sections: `В фокусе`, `Свежие совпадения`, `Ближайшее`.

No marketing headline, hero kicker, fake metric, plan badge, search field, notification bell, or account avatar is allowed above the fold.

## Component families

- App shell: full sidebar, icon sidebar, compact top bar plus menu sheet.
- Navigation row: local 18 px SVG, label, selected blue wash and leading selection line.
- Buttons: primary blue, secondary neutral, destructive red, quiet text action.
- Readiness strip: success, incomplete, unknown/error variants.
- Focus row: fixed order number/icon, title, metadata, disclosure chevron.
- Vacancy list/table: score, title/company, location/salary, status, disclosure/action menu.
- Overlay: one blocking sheet, one child popover, one base coach mark/popover.
- Feedback: success/info toast, persistent actionable warning/error, inline retry error.
- Empty state: clear reason and one relevant action.
- Wizard: Goal, Sources/optional HH, Resume, Summary.

## Icon inventory

Use local SVGs with `viewBox="0 0 24 24"`, 1.75 px rounded strokes, `currentColor`, optical 18 px size. Required metaphors: home/today, briefcase/vacancies, paper-plane/applications, calendar, assistant/spark or bot, analytics/bars, sources/link, settings/gear, check, warning, search, document, chevron, close, retry. Emoji and text glyphs are not production icons.

## Motion

- View transition: 180 ms opacity/translateY(4 px).
- Sheet/popover: 180–220 ms opacity/scale from 0.98.
- Busy/state transitions: 160 ms.
- Under `prefers-reduced-motion`, remove transforms and nonessential animation.

## Responsive continuation

- `>= 961px`: 232 px sidebar, two-column Today layout.
- `721–960px`: 72 px icon sidebar, reduced gutters, two columns only when content remains readable.
- `<= 720px`: compact top bar, menu uses blocking-sheet slot, single-column Today, row Actions menus preserve every capability ID.
- At 680×844, title, readiness, and primary action appear within the first 600 CSS px; no horizontal document overflow.

## Core interaction path

Open Today → finish or defer setup → find vacancies → select a scored vacancy → save/hide/apply or prepare content → review typed safety sheet → send only after revalidation and literal confirmation.
