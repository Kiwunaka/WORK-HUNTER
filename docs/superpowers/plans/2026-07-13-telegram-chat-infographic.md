# Telegram Chat Retrospective Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and verify one fully offline, high-end animated HTML infographic for the supplied Telegram chat export.

**Architecture:** The deliverable is one semantic HTML document containing all CSS, JavaScript, data attributes, and inline SVG. Static HTML is the source of truth; JavaScript adds bounded counters, reveals, navigation state, and copy affordances without creating content or making requests. Pytest contract and Playwright tests independently recompute source facts and enforce offline, responsive, and accessibility requirements.

**Tech Stack:** HTML5, CSS, vanilla JavaScript, inline SVG, Python 3.12 standard library, pytest, Playwright Chromium.

## Global Constraints

- Final artifact: `telegram-chat-retrospective-2026-07-11-13.html`.
- Source JSON: `C:\Users\kiwun\Downloads\Telegram Desktop\ChatExport_2026-07-13 (1)\result.json`, SHA-256 `BE65A707D8AC48A775998D745A91D3400720EB36996226F0AB4DD09494BCC0BF`.
- One fully offline HTML file; no runtime JSON, CDN, external font, image, stylesheet, script, media, frame, or data file.
- Telegram links use `https://t.me/c/1778093200/{message_id}`, `target="_blank"`, and `rel="noopener noreferrer"`.
- Canonical copy, values, message IDs, categories, chart encodings, CSP, and rounding rules come from `docs/superpowers/specs/2026-07-13-telegram-chat-infographic-design.md`.
- Static content works with JavaScript disabled; motion respects `prefers-reduced-motion`.
- Mobile acceptance width is 390 px with no horizontal overflow; every interactive touch target is at least 44×44 CSS pixels.
- Uncompressed HTML stays at or below 300 KB.

## File Map

- Create `telegram-chat-retrospective-2026-07-11-13.html`: the only user-facing artifact and all inline presentation/behavior.
- Create `tests/test_telegram_chat_infographic.py`: source, data, content, CSP, offline, link, SVG, and JavaScript contract checks.
- Create `tests/test_telegram_chat_infographic_browser.py`: browser rendering, zero-request, responsive, reduced-motion, and interaction checks.

---

### Task 1: Lock the source snapshot and offline document shell

**Files:**
- Create: `tests/test_telegram_chat_infographic.py`
- Create: `telegram-chat-retrospective-2026-07-11-13.html`

**Interfaces:**
- Consumes: authoritative JSON path and SHA-256 from the approved spec.
- Produces: `_source() -> dict`, `_html() -> str`, and a valid offline HTML shell with the required section IDs.

- [ ] **Step 1: Write the failing source and shell tests**

```python
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "telegram-chat-retrospective-2026-07-11-13.html"
SOURCE = Path(r"C:\Users\kiwun\Downloads\Telegram Desktop\ChatExport_2026-07-13 (1)\result.json")
SOURCE_SHA256 = "BE65A707D8AC48A775998D745A91D3400720EB36996226F0AB4DD09494BCC0BF"
REQUIRED_SECTIONS = ("overview", "story", "ledger", "polls", "jokes", "gems", "method")


def _source() -> dict:
    return json.loads(SOURCE.read_text(encoding="utf-8"))


def _html() -> str:
    return ARTIFACT.read_text(encoding="utf-8")


def test_authoritative_source_snapshot_is_unchanged():
    raw = SOURCE.read_bytes()
    assert len(raw) == 2_085_892
    assert hashlib.sha256(raw).hexdigest().upper() == SOURCE_SHA256


def test_offline_document_shell_exists():
    html = _html()
    assert '<html lang="ru">' in html
    assert '<meta http-equiv="Content-Security-Policy"' in html
    assert "default-src 'none'" in html
    assert "connect-src 'none'" in html
    assert "font-src 'none'" in html
    assert "<main" in html and "</main>" in html
    for section_id in REQUIRED_SECTIONS:
        assert re.search(rf'<section[^>]+id="{section_id}"', html)
```

- [ ] **Step 2: Run the shell test and verify the missing-artifact failure**

Run: `python -m pytest tests/test_telegram_chat_infographic.py -q`

Expected: source snapshot test passes; shell test fails with `FileNotFoundError` for `telegram-chat-retrospective-2026-07-11-13.html`.

- [ ] **Step 3: Create the minimal semantic offline shell**

```html
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:; connect-src 'none'; font-src 'none'; media-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'">
  <title>Чат Котенков и Горь — 67 часов внутри AI-гонки</title>
  <style>html{color-scheme:light}body{margin:0}section{min-height:1px}</style>
</head>
<body>
  <header><a href="#overview">К обзору</a></header>
  <main>
    <section id="overview"><h1>Чат как тестовый полигон</h1></section>
    <section id="story"><h2>Что происходило</h2></section>
    <section id="ledger"><h2>Реестр реакций</h2></section>
    <section id="polls"><h2>Опросы</h2></section>
    <section id="jokes"><h2>Шутки</h2></section>
    <section id="gems"><h2>Граали</h2></section>
    <section id="method"><h2>Метод</h2></section>
  </main>
  <div id="copy-status" role="status" aria-live="polite"></div>
</body>
</html>
```

- [ ] **Step 4: Run the shell tests**

Run: `python -m pytest tests/test_telegram_chat_infographic.py -q`

Expected: `2 passed`.

- [ ] **Step 5: Commit the contract and shell**

```powershell
git add -- tests/test_telegram_chat_infographic.py telegram-chat-retrospective-2026-07-11-13.html
git commit -m "test: lock Telegram infographic contract"
```

---

### Task 2: Author all canonical content and exact data attributes

**Files:**
- Modify: `tests/test_telegram_chat_infographic.py`
- Modify: `telegram-chat-retrospective-2026-07-11-13.html`

**Interfaces:**
- Consumes: `_source()`, `_html()`, the locked top/joke/gem IDs and poll counts.
- Produces: semantic static sections, `data-message-id`, `data-reactions`, `data-value`, `data-category`, and ordinary Telegram anchors.

- [ ] **Step 1: Add failing locked-content tests**

```python
from collections import Counter

TOP_IDS = [497797, 497798, 498680, 497477, 499982, 499375, 498677, 499557, 498682, 497823]
TOP_TOTALS = [419, 369, 329, 307, 261, 210, 174, 168, 148, 87]
JOKE_IDS = [498680, 498682, 497823, 497511, 498690, 498732, 497480, 498722]
JOKE_SCORES = [313, 141, 85, 47, 25, 25, 22, 19]
GEM_IDS = [500156, 500250, 499892, 499896, 498539, 498540, 498794, 497846, 498345, 500329, 498087]
GEM_REACTIONS = [9, 8, 0, 1, 0, 5, 8, 3, 1, 3, 1]


def _reaction_total(message: dict) -> int:
    return sum(int(item.get("count", 0)) for item in message.get("reactions", []))


def _joke_score(message: dict) -> int:
    return sum(
        int(item.get("count", 0))
        for item in message.get("reactions", [])
        if item.get("emoji") in {"😁", "🤣", "😂"}
    )


def _by_id() -> dict[int, dict]:
    return {int(message["id"]): message for message in _source()["messages"]}


def test_locked_message_rows_and_links_are_authored():
    html = _html()
    for message_id in TOP_IDS + JOKE_IDS + GEM_IDS:
        assert f'data-message-id="{message_id}"' in html
        assert f'https://t.me/c/1778093200/{message_id}' in html
    assert html.count('target="_blank"') >= len(set(TOP_IDS + JOKE_IDS + GEM_IDS))
    assert 'rel="noopener noreferrer"' in html


def test_top_totals_match_source_and_authored_values():
    by_id = _by_id()
    actual = [_reaction_total(by_id[message_id]) for message_id in TOP_IDS]
    assert actual == TOP_TOTALS
    html = _html()
    for message_id, total in zip(TOP_IDS, TOP_TOTALS, strict=True):
        pattern = rf'data-message-id="{message_id}"[^>]*data-reactions="{total}"'
        assert re.search(pattern, html)


def test_headline_and_poll_values_are_authored():
    html = _html()
    required = {
        "messages": 2743, "reactions": 5088, "reacted": 758,
        "unreacted": 1985, "channel-messages": 85,
        "native-messages": 2658, "channel-reactions": 2047,
        "native-reactions": 3041, "hours": 67.04, "sender-ids": 268, "replies": 1858,
        "photos": 313, "stickers": 58, "animations": 22,
        "gender-voters": 9183, "weights-substantive": 4269,
    }
    for key, value in required.items():
        assert f'data-metric="{key}" data-value="{value}"' in html


def test_all_derived_series_match_source_and_authored_values():
    messages = _source()["messages"]
    by_id = _by_id()
    html = _html()

    assert Counter(message["date"][:10] for message in messages) == {
        "2026-07-11": 930, "2026-07-12": 1073, "2026-07-13": 740,
    }
    for day, value in (("2026-07-11", 930), ("2026-07-12", 1073), ("2026-07-13", 740)):
        assert f'data-day="{day}" data-value="{value}"' in html

    totals = [_reaction_total(message) for message in messages]
    channel = [message for message in messages if str(message.get("from_id", "")).startswith("channel")]
    assert sum(totals) == 5088
    assert sum(value > 0 for value in totals) == 758
    assert len(channel) == 85
    assert sum(_reaction_total(message) for message in channel) == 2047
    assert len({message.get("from_id") for message in messages if message.get("from_id")}) == 268
    assert sum(bool(message.get("reply_to_message_id")) for message in messages) == 1858
    assert sum("photo" in message for message in messages) == 313
    assert sum(message.get("media_type") == "sticker" for message in messages) == 58
    assert sum(message.get("media_type") == "animation" for message in messages) == 22
    assert '11 июля, 00:00 — 13 июля, 19:03 MSK' in html

    assert [_joke_score(by_id[message_id]) for message_id in JOKE_IDS] == JOKE_SCORES
    for message_id, score in zip(JOKE_IDS, JOKE_SCORES, strict=True):
        assert re.search(rf'data-message-id="{message_id}"[^>]*data-joke-score="{score}"', html)

    assert [_reaction_total(by_id[message_id]) for message_id in GEM_IDS] == GEM_REACTIONS
    for message_id, total in zip(GEM_IDS, GEM_REACTIONS, strict=True):
        assert re.search(rf'data-message-id="{message_id}"[^>]*data-reactions="{total}"', html)

    composition = Counter()
    for message_id in TOP_IDS:
        for item in by_id[message_id].get("reactions", []):
            key = item.get("emoji") or item.get("type")
            composition[key] += int(item.get("count", 0))
    assert sum(composition.values()) == 2472
    locked = {"😁": 530, "🤡": 505, "🤔": 301, "👍": 287, "🤯": 196, "❤‍🔥": 183}
    assert {key: composition[key] for key in locked} == locked
    assert sum(composition.values()) - sum(locked.values()) == 470
    for key, value in locked.items():
        assert f'data-reaction="{key}" data-value="{value}"' in html
    assert 'data-reaction="other" data-value="470"' in html


def test_raw_poll_answers_and_derivations_match_source():
    by_id = _by_id()
    html = _html()
    gender = [answer["voters"] for answer in by_id[498677]["poll"]["answers"]]
    weights = [answer["voters"] for answer in by_id[499557]["poll"]["answers"]]
    assert gender == [6663, 680, 311, 255, 236, 1038]
    assert weights == [1143, 656, 834, 408, 283, 115, 830, 1797]
    for index, value in enumerate(gender):
        assert f'data-poll="gender" data-option="{index}" data-value="{value}"' in html
    for index, value in enumerate(weights):
        assert f'data-poll="weights" data-option="{index}" data-value="{value}"' in html
    assert sum(weights) - weights[-1] == 4269
    assert sum(weights[index] for index in (0, 2, 4)) == 2260
    assert sum(weights[index] for index in (1, 3, 5)) == 1179
    assert weights[6] == 830
```

- [ ] **Step 2: Verify the locked-content tests fail**

Run: `python -m pytest tests/test_telegram_chat_infographic.py -q`

Expected: failures report missing `data-message-id`, Telegram URLs, and metric attributes.

- [ ] **Step 3: Replace the shell section headings with the approved static content**

Author all seven sections directly in HTML. Use the exact lists and copy from the approved spec. Every linked item follows this concrete pattern:

```html
<article class="ledger-row" data-message-id="497797" data-reactions="419">
  <span class="ledger-rank" aria-hidden="true">01</span>
  <div class="ledger-copy">
    <p class="eyebrow">AI-2040 · часть 1</p>
    <h3>419 реакций</h3>
    <p>Главный сигнал: 🤡 213</p>
  </div>
  <a class="message-link" href="https://t.me/c/1778093200/497797" target="_blank" rel="noopener noreferrer">
    <span>Открыть #497797</span><span class="arrow-island" aria-hidden="true">↗</span>
  </a>
</article>
```

Metric text and fallback values remain visible without JavaScript:

```html
<strong class="metric-value" data-metric="messages" data-value="2743">2 743</strong>
<strong class="metric-value" data-metric="reactions" data-value="5088">5 088</strong>
<span data-metric="reacted" data-value="758">758</span>
<span data-metric="unreacted" data-value="1985">1 985</span>
```

Gems expose category and all primary/follow-up links:

```html
<article class="gem-card" data-category="workflow" data-message-id="500156" data-reactions="9">
  <p class="eyebrow">WORKFLOW · 9 РЕАКЦИЙ</p>
  <h3>Харнесс меняет модель</h3>
  <p>Claude Code копает глубже, но в этом тесте съел около 5× времени и токенов.</p>
  <div class="link-pair">
    <a href="https://t.me/c/1778093200/500156" target="_blank" rel="noopener noreferrer">500156</a>
    <a data-message-id="500250" data-reactions="8" href="https://t.me/c/1778093200/500250" target="_blank" rel="noopener noreferrer">500250</a>
  </div>
</article>
```

Poll and chart elements expose deterministic source keys, not anonymous numbers:

```html
<g class="chart-bar" data-day="2026-07-11" data-value="930"><rect x="0" y="0" width="422.5" height="42"/><text x="438" y="28">930</text></g>
<g class="reaction-segment" data-reaction="😁" data-value="530"><rect x="0" y="0" width="154.4" height="28"/><title>😁 — 530</title></g>
<g class="poll-bar" data-poll="gender" data-option="0" data-value="6663"><rect x="0" y="0" width="560" height="32"/><text x="576" y="23">6 663</text></g>
<article class="joke-card" data-message-id="498680" data-joke-score="313" data-reactions="329"><blockquote>Мне русские модели сами пишут, я не пишу первый</blockquote><a href="https://t.me/c/1778093200/498680" target="_blank" rel="noopener noreferrer">Открыть #498680</a></article>
```

- [ ] **Step 4: Run the data/content tests**

Run: `python -m pytest tests/test_telegram_chat_infographic.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit canonical content**

```powershell
git add -- tests/test_telegram_chat_infographic.py telegram-chat-retrospective-2026-07-11-13.html
git commit -m "feat: add Telegram retrospective content"
```

---

### Task 3: Build the Editorial Dossier visual system and exact SVG plates

**Files:**
- Modify: `tests/test_telegram_chat_infographic.py`
- Modify: `telegram-chat-retrospective-2026-07-11-13.html`

**Interfaces:**
- Consumes: canonical static elements and numeric `data-*` attributes from Task 2.
- Produces: CSS tokens/layout and inline SVG with `data-chart` names and exact accessible values.

- [ ] **Step 1: Add failing SVG and responsive contract tests**

```python
def test_required_svg_plates_and_visual_tokens_exist():
    html = _html()
    for chart in ("daily-messages", "reacted-share", "channel-skew", "top-composition", "gender-poll", "weights-poll", "gem-constellation"):
        assert f'data-chart="{chart}"' in html
    for value in (930, 1073, 740, 530, 505, 301, 287, 196, 183, 470):
        assert f'data-value="{value}"' in html
    assert "--paper:" in html
    assert "--ink:" in html
    assert "--cobalt:" in html
    assert "--signal:" in html
    assert "@media (max-width: 700px)" in html
    assert "@media (prefers-reduced-motion: reduce)" in html
```

- [ ] **Step 2: Run and observe missing visual contracts**

Run: `python -m pytest tests/test_telegram_chat_infographic.py::test_required_svg_plates_and_visual_tokens_exist -q`

Expected: failure at the first missing `data-chart`.

- [ ] **Step 3: Add the visual foundation and responsive rules**

Use these exact foundation tokens and layout primitives, then complete section-specific styles in the same vocabulary:

```css
:root {
  --paper:#ece4d4; --paper-hi:#f7f1e5; --ink:#11110f;
  --muted:#6d685e; --cobalt:#1246d8; --signal:#f04424;
  --acid:#c9f44a; --rule:rgba(17,17,15,.18);
  --display:Bahnschrift,"Aptos Display","Segoe UI Variable Display",sans-serif;
  --text:"Aptos","Segoe UI Variable Text","Segoe UI",sans-serif;
  --serif:Georgia,"Times New Roman",serif;
  --mass:cubic-bezier(.32,.72,0,1);
}
*{box-sizing:border-box}
html{scroll-behavior:smooth;background:var(--paper);color:var(--ink)}
body{margin:0;font-family:var(--text);background:var(--paper);overflow-x:clip}
body::before{content:"";position:fixed;inset:0;pointer-events:none;z-index:3;opacity:.055;background-image:radial-gradient(rgba(17,17,15,.7) .55px,transparent .7px);background-size:5px 5px}
.page-shell{width:min(1520px,100%);margin:0 auto;padding:0 clamp(20px,4vw,72px)}
.section{padding-block:clamp(96px,12vw,176px)}
.double-bezel{padding:7px;border:1px solid rgba(17,17,15,.22);border-radius:34px;background:rgba(17,17,15,.035)}
.double-bezel__core{border-radius:27px;background:var(--paper-hi);box-shadow:inset 0 1px 0 rgba(255,255,255,.7);overflow:hidden}
.message-link{min-height:44px;display:inline-flex;align-items:center;gap:12px;border-radius:999px;padding:6px 6px 6px 18px;background:var(--ink);color:var(--paper-hi);text-decoration:none;transition:transform .65s var(--mass),background-color .65s var(--mass)}
.arrow-island{width:34px;height:34px;border-radius:50%;display:grid;place-items:center;background:var(--paper-hi);color:var(--ink);transition:transform .65s var(--mass)}
.message-link:hover .arrow-island,.message-link:focus-visible .arrow-island{transform:translate(2px,-2px) rotate(4deg)}
:focus-visible{outline:3px solid var(--cobalt);outline-offset:4px}
@media (max-width:700px){.page-shell{padding-inline:16px}.section{padding-block:84px}.editorial-grid{grid-template-columns:1fr}.rotated{transform:none}.overlap{margin:0}}
@media (prefers-reduced-motion:reduce){*,*::before,*::after{scroll-behavior:auto!important;animation-duration:.001ms!important;animation-iteration-count:1!important;transition-duration:.001ms!important}}
```

- [ ] **Step 4: Add exact inline SVG plates**

Author each SVG with visible text fallbacks and exact `data-value` attributes. The daily chart uses a 0–1,100 domain and transform-based bar reveals:

```html
<svg data-chart="daily-messages" viewBox="0 0 720 320" role="img" aria-labelledby="daily-title daily-desc">
  <title id="daily-title">Сообщения по дням</title>
  <desc id="daily-desc">11 июля — 930; 12 июля — 1073; 13 июля до 19:03 MSK — 740.</desc>
  <g class="chart-bar" data-day="2026-07-11" data-value="930" style="--bar-scale:.845"><rect x="70" y="82" width="500" height="46" rx="4"/></g>
  <g class="chart-bar" data-day="2026-07-12" data-value="1073" style="--bar-scale:.975"><rect x="70" y="146" width="500" height="46" rx="4"/></g>
  <g class="chart-bar" data-day="2026-07-13" data-value="740" style="--bar-scale:.673"><rect x="70" y="210" width="500" height="46" rx="4"/></g>
  <g aria-hidden="true"><text x="590" y="112">930</text><text x="590" y="176">1 073</text><text x="590" y="240">740*</text></g>
</svg>
```

Add the other six plates with these exact source-bound structures; styling may add rules, labels, and texture but must not change their `data-*` values or proportions:

```html
<svg data-chart="reacted-share" viewBox="0 0 260 260" role="img" aria-label="758 сообщений с реакциями из 2743">
  <circle cx="130" cy="130" r="92" pathLength="100" class="ring-base"/>
  <circle cx="130" cy="130" r="92" pathLength="100" class="ring-value" data-metric="reacted" data-value="758" stroke-dasharray="27.6 72.4"/>
  <text x="130" y="126" text-anchor="middle">758</text><text x="130" y="150" text-anchor="middle">27,6%</text>
</svg>

<svg data-chart="channel-skew" viewBox="0 0 720 230" role="img" aria-label="Канальные сообщения: 3,1% сообщений и 40,2% реакций">
  <g data-metric="channel-messages" data-value="85"><rect x="70" y="62" width="18.6" height="42"/><text x="98" y="90">85 / 2 743</text></g>
  <g data-metric="native-messages" data-value="2658"><rect x="88.6" y="62" width="581.4" height="42"/></g>
  <g data-metric="channel-reactions" data-value="2047"><rect x="70" y="142" width="241.2" height="42"/><text x="321" y="170">2 047 / 5 088</text></g>
  <g data-metric="native-reactions" data-value="3041"><rect x="311.2" y="142" width="358.8" height="42"/></g>
</svg>

<svg data-chart="top-composition" viewBox="0 0 720 110" role="img" aria-label="Реакции топ-10, всего 2472">
  <g data-reaction="😁" data-value="530"><rect x="0" y="22" width="154.4" height="42"/><title>😁 530 · 21,4%</title></g>
  <g data-reaction="🤡" data-value="505"><rect x="154.4" y="22" width="147.1" height="42"/><title>🤡 505 · 20,4%</title></g>
  <g data-reaction="🤔" data-value="301"><rect x="301.5" y="22" width="87.7" height="42"/><title>🤔 301 · 12,2%</title></g>
  <g data-reaction="👍" data-value="287"><rect x="389.2" y="22" width="83.6" height="42"/><title>👍 287 · 11,6%</title></g>
  <g data-reaction="🤯" data-value="196"><rect x="472.8" y="22" width="57.1" height="42"/><title>🤯 196 · 7,9%</title></g>
  <g data-reaction="❤‍🔥" data-value="183"><rect x="529.9" y="22" width="53.3" height="42"/><title>❤‍🔥 183 · 7,4%</title></g>
  <g data-reaction="other" data-value="470"><rect x="583.2" y="22" width="136.8" height="42"/><title>Прочие 470 · 19,0%</title></g>
</svg>

<svg data-chart="gender-poll" viewBox="0 0 720 360" role="img" aria-label="Опрос о поле модели, 9183 голоса">
  <g data-poll="gender" data-option="0" data-value="6663"><rect x="0" y="20" width="560" height="32"/><text x="576" y="43">6 663</text></g>
  <g data-poll="gender" data-option="1" data-value="680"><rect x="0" y="72" width="57.1" height="32"/><text x="73" y="95">680</text></g>
  <g data-poll="gender" data-option="2" data-value="311"><rect x="0" y="124" width="26.1" height="32"/><text x="42" y="147">311</text></g>
  <g data-poll="gender" data-option="3" data-value="255"><rect x="0" y="176" width="21.4" height="32"/><text x="37" y="199">255</text></g>
  <g data-poll="gender" data-option="4" data-value="236"><rect x="0" y="228" width="19.8" height="32"/><text x="36" y="251">236</text></g>
  <g data-poll="gender" data-option="5" data-value="1038"><rect x="0" y="280" width="87.2" height="32"/><text x="103" y="303">1 038</text></g>
</svg>

<svg data-chart="weights-poll" viewBox="0 0 720 460" role="img" aria-label="Опрос о запрете открытых весов, 6066 голосов">
  <g data-poll="weights" data-option="0" data-value="1143"><rect x="0" y="20" width="318" height="28"/><text x="330" y="41">1 143</text></g>
  <g data-poll="weights" data-option="1" data-value="656"><rect x="0" y="72" width="182.5" height="28"/><text x="195" y="93">656</text></g>
  <g data-poll="weights" data-option="2" data-value="834"><rect x="0" y="124" width="232.1" height="28"/><text x="245" y="145">834</text></g>
  <g data-poll="weights" data-option="3" data-value="408"><rect x="0" y="176" width="113.5" height="28"/><text x="126" y="197">408</text></g>
  <g data-poll="weights" data-option="4" data-value="283"><rect x="0" y="228" width="78.7" height="28"/><text x="91" y="249">283</text></g>
  <g data-poll="weights" data-option="5" data-value="115"><rect x="0" y="280" width="32" height="28"/><text x="45" y="301">115</text></g>
  <g data-poll="weights" data-option="6" data-value="830"><rect x="0" y="332" width="230.9" height="28"/><text x="244" y="353">830</text></g>
  <g data-poll="weights" data-option="7" data-value="1797"><rect x="0" y="384" width="500" height="28"/><text x="513" y="405">1 797</text></g>
</svg>

<svg data-chart="gem-constellation" viewBox="0 0 720 520" role="img" aria-label="8 граалей: workflow, infrastructure, method, theory">
  <g data-category="workflow" data-message-id="500156" data-reactions="9"><circle cx="180" cy="120" r="18"/><text x="208" y="126">500156</text></g>
  <g data-category="infrastructure" data-message-id="499892" data-reactions="0"><circle cx="530" cy="132" r="18"/><text x="558" y="138">499892</text></g>
  <g data-category="workflow" data-message-id="498539" data-reactions="0"><circle cx="242" cy="250" r="18"/><text x="270" y="256">498539</text></g>
  <g data-category="method" data-message-id="498540" data-reactions="5"><circle cx="390" cy="210" r="18"/><text x="418" y="216">498540</text></g>
  <g data-category="method" data-message-id="498794" data-reactions="8"><circle cx="476" cy="316" r="18"/><text x="504" y="322">498794</text></g>
  <g data-category="method" data-message-id="497846" data-reactions="3"><circle cx="322" cy="382" r="18"/><text x="350" y="388">497846</text></g>
  <g data-category="method" data-message-id="500329" data-reactions="3"><circle cx="160" cy="360" r="18"/><text x="188" y="366">500329</text></g>
  <g data-category="theory" data-message-id="498087" data-reactions="1"><circle cx="574" cy="402" r="18"/><text x="602" y="408">498087</text></g>
</svg>
```

- [ ] **Step 5: Run visual contract tests**

Run: `python -m pytest tests/test_telegram_chat_infographic.py -q`

Expected: all tests pass.

- [ ] **Step 6: Commit visual system**

```powershell
git add -- tests/test_telegram_chat_infographic.py telegram-chat-retrospective-2026-07-11-13.html
git commit -m "feat: add editorial SVG infographic system"
```

---

### Task 4: Add bounded progressive motion and interactions

**Files:**
- Modify: `tests/test_telegram_chat_infographic.py`
- Modify: `telegram-chat-retrospective-2026-07-11-13.html`

**Interfaces:**
- Consumes: static `.metric-value`, `[data-reveal]`, `.chart-bar`, `[data-nav-target]`, and `[data-copy-link]` elements.
- Produces: `initMetricCounters`, `initChartReveals`, `initRevealMotion`, `initDossierNav`, `initLinkActions`, and `setReducedMotionMode` with no network or content-rendering behavior.

- [ ] **Step 1: Add failing script contract tests**

```python
def test_progressive_enhancement_functions_and_network_bans():
    html = _html()
    for name in ("initMetricCounters", "initChartReveals", "initRevealMotion", "initDossierNav", "initLinkActions", "setReducedMotionMode"):
        assert f"function {name}" in html
    forbidden = ("fetch(", "XMLHttpRequest", "WebSocket", "EventSource", "sendBeacon", "setInterval(")
    assert not any(token in html for token in forbidden)
    assert 'aria-live="polite"' in html
```

- [ ] **Step 2: Run and observe missing enhancement functions**

Run: `python -m pytest tests/test_telegram_chat_infographic.py::test_progressive_enhancement_functions_and_network_bans -q`

Expected: failure on `function initMetricCounters`.

- [ ] **Step 3: Add the complete progressive-enhancement script**

```javascript
(() => {
  "use strict";
  const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)");

  function setReducedMotionMode() {
    document.documentElement.dataset.motion = reduceMotion.matches ? "reduced" : "full";
  }

  function initMetricCounters(root = document) {
    if (reduceMotion.matches) return;
    root.querySelectorAll(".metric-value[data-value]").forEach((node) => {
      const finalText = node.textContent;
      const target = Number(node.dataset.value);
      if (!Number.isFinite(target)) return;
      const started = performance.now();
      const tick = (now) => {
        const progress = Math.min(1, (now - started) / 900);
        const eased = 1 - Math.pow(1 - progress, 4);
        node.textContent = Math.round(target * eased).toLocaleString("ru-RU");
        if (progress < 1) requestAnimationFrame(tick); else node.textContent = finalText;
      };
      requestAnimationFrame(tick);
    });
  }

  function initChartReveals(root = document) {
    const charts = [...root.querySelectorAll("[data-chart]")];
    if (reduceMotion.matches || !("IntersectionObserver" in window)) {
      charts.forEach((chart) => chart.dataset.visible = "true");
      return;
    }
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        entry.target.dataset.visible = "true";
        observer.unobserve(entry.target);
      });
    }, { threshold: 0.18 });
    charts.forEach((chart) => observer.observe(chart));
  }

  function initRevealMotion(root = document) {
    const items = [...root.querySelectorAll("[data-reveal]")];
    if (reduceMotion.matches || !("IntersectionObserver" in window)) {
      items.forEach((item) => item.dataset.visible = "true");
      return;
    }
    items.forEach((item) => item.dataset.revealReady = "true");
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        entry.target.dataset.visible = "true";
        observer.unobserve(entry.target);
      });
    }, { threshold: 0.12 });
    items.forEach((item) => observer.observe(item));
  }

  function initDossierNav(root = document) {
    if (!("IntersectionObserver" in window)) return;
    const links = new Map([...root.querySelectorAll("[data-nav-target]")].map((link) => [link.dataset.navTarget, link]));
    const observer = new IntersectionObserver((entries) => {
      entries.filter((entry) => entry.isIntersecting).forEach((entry) => {
        links.forEach((link, id) => link.toggleAttribute("aria-current", id === entry.target.id));
      });
    }, { rootMargin: "-30% 0px -60%", threshold: 0 });
    links.forEach((_, id) => { const section = document.getElementById(id); if (section) observer.observe(section); });
  }

  function initLinkActions(root = document) {
    const status = root.getElementById("copy-status");
    root.querySelectorAll("[data-copy-link]").forEach((button) => {
      button.addEventListener("click", async () => {
        const url = button.dataset.copyLink;
        try {
          await navigator.clipboard.writeText(url);
          button.dataset.copied = "true";
          if (status) status.textContent = `Ссылка ${url} скопирована`;
          setTimeout(() => { button.dataset.copied = "false"; }, 1200);
        } catch (_) {
          if (status) status.textContent = "Буфер обмена недоступен — откройте ссылку обычной кнопкой";
        }
      });
    });
  }

  setReducedMotionMode();
  initMetricCounters();
  initChartReveals();
  initRevealMotion();
  initDossierNav();
  initLinkActions();
})();
```

- [ ] **Step 4: Extract and syntax-check inline JavaScript**

Run:

```powershell
$html = [IO.File]::ReadAllText('telegram-chat-retrospective-2026-07-11-13.html')
$script = [regex]::Matches($html, '<script>([\s\S]*?)</script>') | ForEach-Object { $_.Groups[1].Value }
$script | node --check -
```

Expected: exit code 0 with no syntax error.

- [ ] **Step 5: Run contract tests**

Run: `python -m pytest tests/test_telegram_chat_infographic.py -q`

Expected: all tests pass.

- [ ] **Step 6: Commit motion and interactions**

```powershell
git add -- tests/test_telegram_chat_infographic.py telegram-chat-retrospective-2026-07-11-13.html
git commit -m "feat: add offline infographic motion"
```

---

### Task 5: Browser verification and visual polish

**Files:**
- Create: `tests/test_telegram_chat_infographic_browser.py`
- Modify: `telegram-chat-retrospective-2026-07-11-13.html`

**Interfaces:**
- Consumes: final offline artifact and Playwright Chromium.
- Produces: deterministic browser checks for zero requests, responsive layout, controls, and reduced motion.

- [ ] **Step 1: Write failing browser acceptance tests**

```python
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright


ARTIFACT = Path(__file__).resolve().parents[1] / "telegram-chat-retrospective-2026-07-11-13.html"


@pytest.fixture
def browser():
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch(headless=True)
        try:
            yield instance
        finally:
            instance.close()


def _open(browser, width: int, reduced_motion: str = "no-preference"):
    context = browser.new_context(viewport={"width": width, "height": 900}, reduced_motion=reduced_motion)
    requests: list[str] = []
    context.on("request", lambda request: requests.append(request.url))
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(ARTIFACT.as_uri(), wait_until="load")
    return context, page, requests, errors


@pytest.mark.parametrize("width", [1440, 900, 390])
def test_offline_layout_has_no_external_requests_or_overflow(browser, width):
    context, page, requests, errors = _open(browser, width)
    try:
        assert requests == [ARTIFACT.as_uri()]
        assert errors == []
        overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert overflow <= 1
        assert page.locator("[data-message-id='497797']").is_visible()
    finally:
        context.close()


def test_navigation_links_and_reduced_motion(browser):
    context, page, _, errors = _open(browser, 390, "reduce")
    try:
        page.locator("a[href='#gems']").first.click()
        assert page.locator("#gems").is_visible()
        assert page.locator("html").get_attribute("data-motion") == "reduced"
        assert errors == []
    finally:
        context.close()


def test_copy_failure_keeps_normal_anchor_available(browser):
    context, page, _, _ = _open(browser, 1440)
    try:
        button = page.locator("[data-copy-link]").first
        if button.count():
            button.click()
            assert page.locator("#copy-status").get_attribute("aria-live") == "polite"
        assert page.locator("a[href='https://t.me/c/1778093200/497797']").count() >= 1
    finally:
        context.close()
```

- [ ] **Step 2: Run browser tests and record failures**

Run: `python -m pytest tests/test_telegram_chat_infographic_browser.py -q`

Expected: any layout, selector, or behavior gap fails with a specific assertion before polish.

- [ ] **Step 3: Render desktop, tablet, and mobile screenshots**

Use Playwright or the available browser-control surface to capture 1440, 900, and 390 px screenshots into `tmp/telegram-infographic/`. Inspect the hero, every chart, ledger rows, joke cards, gem constellation, nav, focus states, and footer.

- [ ] **Step 4: Apply targeted visual corrections**

Edit only defects found in rendered output: clipping, hierarchy, rhythm, contrast, overflow, focus, SVG label placement, and motion timing. Preserve all locked data and IDs. Do not add new content, dependencies, charts, or interactions.

- [ ] **Step 5: Re-run browser and contract tests**

Run:

```powershell
python -m pytest tests/test_telegram_chat_infographic.py tests/test_telegram_chat_infographic_browser.py -q
```

Expected: all tests pass with no console/page errors and zero external load requests.

- [ ] **Step 6: Commit browser verification and polish**

```powershell
git add -- tests/test_telegram_chat_infographic_browser.py telegram-chat-retrospective-2026-07-11-13.html
git commit -m "test: verify Telegram infographic in browser"
```

---

### Task 6: Final independent verification and handoff

**Files:**
- Verify: `telegram-chat-retrospective-2026-07-11-13.html`
- Verify: `tests/test_telegram_chat_infographic.py`
- Verify: `tests/test_telegram_chat_infographic_browser.py`

**Interfaces:**
- Consumes: completed artifact and both QA suites.
- Produces: verified one-file user artifact with exact source-backed content.

- [ ] **Step 1: Run the focused suites from a clean process**

Run: `python -m pytest tests/test_telegram_chat_infographic.py tests/test_telegram_chat_infographic_browser.py -q`

Expected: all focused tests pass.

- [ ] **Step 2: Check size, working tree, and forbidden remote patterns**

```powershell
Get-Item -LiteralPath 'telegram-chat-retrospective-2026-07-11-13.html' | Select-Object FullName,Length
rg.exe --pcre2 -n "https?://(?!t\.me/c/1778093200/)|@import|srcset=|fetch\(|XMLHttpRequest|WebSocket|EventSource|sendBeacon" telegram-chat-retrospective-2026-07-11-13.html
git status --short
```

Expected: HTML length is at most 307,200 bytes; the only HTTP URLs are Telegram message links; no uncommitted implementation changes remain.

- [ ] **Step 3: Open the final file once more at 1440 and 390 px**

Expected: complete static content, correct section navigation, readable charts, no horizontal overflow, visible focus, and no animation when reduced motion is enabled.

- [ ] **Step 4: Hand off the absolute file link and verification summary**

Report the exact HTML path, focused test command/result, zero-request result, tested viewport widths, and the caveat that external Telegram links require access to the private chat.
