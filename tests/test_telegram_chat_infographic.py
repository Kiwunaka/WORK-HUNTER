from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import datetime
from html import unescape
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "telegram-chat-retrospective-2026-07-11-13.html"
SOURCE = Path(
    r"C:\Users\kiwun\Downloads\Telegram Desktop\ChatExport_2026-07-13 (1)\result.json"
)
if not SOURCE.is_file():
    pytest.skip(
        "authoritative Telegram export is not available on this machine",
        allow_module_level=True,
    )
SOURCE_SHA256 = "BE65A707D8AC48A775998D745A91D3400720EB36996226F0AB4DD09494BCC0BF"
REQUIRED_SECTIONS = ("overview", "story", "ledger", "polls", "jokes", "gems", "method")
TOPIC_IDS = [
    497642,
    497797,
    497798,
    497840,
    497855,
    498677,
    498680,
    498682,
    499375,
    499557,
    500156,
    500250,
    499892,
    500369,
]
TOP_IDS = [497797, 497798, 498680, 497477, 499982, 499375, 498677, 499557, 498682, 497823]
TOP_TOTALS = [419, 369, 329, 307, 261, 210, 174, 168, 148, 87]
JOKE_IDS = [498680, 498682, 497823, 497511, 498690, 498732, 497480, 498722]
JOKE_SCORES = [313, 141, 85, 47, 25, 25, 22, 19]
GEM_IDS = [500156, 500250, 499892, 499896, 498539, 498540, 498794, 497846, 498345, 500329, 498087]
GEM_REACTIONS = [9, 8, 0, 1, 0, 5, 8, 3, 1, 3, 1]


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
    all_ids = set(TOPIC_IDS + TOP_IDS + JOKE_IDS + GEM_IDS)
    for message_id in all_ids:
        assert f'data-message-id="{message_id}"' in html
        assert f'https://t.me/c/1778093200/{message_id}' in html
    assert html.count('target="_blank"') >= len(all_ids)
    assert html.count('rel="noopener noreferrer"') >= len(all_ids)


def test_every_telegram_link_targets_an_exported_message():
    html = _html()
    by_id = _by_id()
    urls = re.findall(r'href="https://t\.me/c/(\d+)/(\d+)"', html)
    assert urls
    assert all(chat_id == "1778093200" for chat_id, _ in urls)
    assert all(int(message_id) in by_id for _, message_id in urls)


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
        "messages": 2743,
        "reactions": 5088,
        "reacted": 758,
        "unreacted": 1985,
        "channel-messages": 85,
        "native-messages": 2658,
        "channel-reactions": 2047,
        "native-reactions": 3041,
        "hours": 67.04,
        "sender-ids": 268,
        "replies": 1858,
        "photos": 313,
        "stickers": 58,
        "animations": 22,
        "gender-voters": 9183,
        "weights-substantive": 4269,
    }
    for key, value in required.items():
        assert f'data-metric="{key}" data-value="{value}"' in html


def test_all_derived_series_match_source_and_authored_values():
    messages = _source()["messages"]
    by_id = _by_id()
    html = _html()

    assert Counter(message["date"][:10] for message in messages) == {
        "2026-07-11": 930,
        "2026-07-12": 1073,
        "2026-07-13": 740,
    }
    for day, value in (("2026-07-11", 930), ("2026-07-12", 1073), ("2026-07-13", 740)):
        assert f'data-day="{day}" data-value="{value}"' in html

    totals = [_reaction_total(message) for message in messages]
    channel = [
        message
        for message in messages
        if str(message.get("from_id", "")).startswith("channel")
    ]
    assert sum(totals) == 5088
    assert sum(value > 0 for value in totals) == 758
    assert len(channel) == 85
    assert sum(_reaction_total(message) for message in channel) == 2047
    assert len({message.get("from_id") for message in messages if message.get("from_id")}) == 268
    assert sum(bool(message.get("reply_to_message_id")) for message in messages) == 1858
    assert sum("photo" in message for message in messages) == 313
    assert sum(message.get("media_type") == "sticker" for message in messages) == 58
    assert sum(message.get("media_type") == "animation" for message in messages) == 22
    assert "11 июля, 00:00 — 13 июля, 19:03 MSK" in html

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


def test_required_svg_plates_and_visual_tokens_exist():
    html = _html()
    charts = (
        "daily-messages",
        "reacted-share",
        "channel-skew",
        "top-composition",
        "gender-poll",
        "weights-poll",
        "gem-constellation",
    )
    for chart in charts:
        opening = re.search(rf'<svg[^>]*data-chart="{chart}"[^>]*>', html)
        assert opening, chart
        assert 'role="img"' in opening.group(0)
    for token in ("--paper:", "--paper-hi:", "--ink:", "--cobalt:", "--signal:"):
        assert token in html
    assert "@media (max-width:900px)" in html
    assert "@media (max-width:700px)" in html
    assert "@media (prefers-reduced-motion:reduce)" in html
    assert "Bahnschrift" in html and "Georgia" in html


def test_svg_values_are_source_bound_and_accessibly_labeled():
    html = _html()
    for value in (930, 1073, 740, 530, 505, 301, 287, 196, 183, 470):
        assert f'data-value="{value}"' in html
    assert html.count("<title") >= 7
    assert "неполный день, до 19:03 MSK" in html
    assert "758 сообщений с реакциями из 2743" in html
    assert "Канальные сообщения: 3,1% сообщений и 40,2% реакций" in html


def test_progressive_enhancement_functions_and_network_bans():
    html = _html()
    for name in (
        "initMetricCounters",
        "initChartReveals",
        "initRevealMotion",
        "initDossierNav",
        "initLinkActions",
        "setReducedMotionMode",
    ):
        assert f"function {name}" in html
    forbidden = (
        "fetch(",
        "XMLHttpRequest",
        "WebSocket",
        "EventSource",
        "sendBeacon",
        "setInterval(",
        'addEventListener("scroll"',
    )
    assert not any(token in html for token in forbidden)
    assert 'aria-live="polite"' in html
    assert "data-copy-link=" in html
    assert "IntersectionObserver" in html
    assert "function resolveRoot" in html
    assert html.count("const scope = resolveRoot(root);") == 5
    compact_css = re.sub(r"\s+", "", html)
    assert (
        '.ledger-row[data-reveal-ready="true"]:not([data-visible="true"])::before'
        "{transform:scaleX(0)}"
    ) in compact_css


def _relative_luminance(hex_color: str) -> float:
    channels = [int(hex_color[index : index + 2], 16) / 255 for index in (1, 3, 5)]

    def linearize(channel: float) -> float:
        if channel <= 0.04045:
            return channel / 12.92
        return ((channel + 0.055) / 1.055) ** 2.4

    red, green, blue = (linearize(channel) for channel in channels)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _contrast_ratio(foreground: str, background: str) -> float:
    lighter, darker = sorted(
        (_relative_luminance(foreground), _relative_luminance(background)), reverse=True
    )
    return (lighter + 0.05) / (darker + 0.05)


def test_core_color_tokens_keep_body_text_at_wcag_aa_contrast():
    html = _html()
    tokens = dict(re.findall(r"--([\w-]+):(#[0-9a-fA-F]{6})", html))
    assert _contrast_ratio(tokens["muted"], tokens["paper"]) >= 4.5
    assert _contrast_ratio(tokens["muted"], tokens["paper-hi"]) >= 4.5
    assert _contrast_ratio(tokens["paper-hi"], tokens["cobalt"]) >= 4.5
    assert _contrast_ratio(tokens["ink"], tokens["acid"]) >= 4.5
    assert _contrast_ratio(tokens["ink"], tokens["signal"]) >= 4.5

    compact_css = re.sub(r"\s+", "", html)
    for selector in (
        ".tldr.eyebrow",
        ".chapter-no",
        ".chapter--signal>div",
        ".chapter--signalp:not(.eyebrow)",
        ".joke-score",
    ):
        assert re.search(
            rf"{re.escape(selector)}\{{[^}}]*color:var\(--ink\)", compact_css
        ), selector


def test_all_joke_quotes_preserve_source_wording():
    html = _html()
    by_id = _by_id()
    for message_id in JOKE_IDS:
        match = re.search(
            rf'<article[^>]*data-message-id="{message_id}"[^>]*>.*?'
            r"<blockquote>(.*?)</blockquote>",
            html,
            re.DOTALL,
        )
        assert match, message_id
        quote = re.sub(r"<br\s*/?>", "\n", match.group(1), flags=re.IGNORECASE)
        quote = unescape(re.sub(r"<[^>]+>", "", quote))
        source_text = by_id[message_id]["text"]
        assert isinstance(source_text, str)
        assert " ".join(quote.split()) == " ".join(source_text.split())


def test_constellation_connectors_have_a_real_dash_pattern_to_animate():
    html = _html()
    for selector in ("connector", "facet"):
        rule = re.search(rf"\.gem-constellation \.{selector}\{{([^}}]*)\}}", html)
        assert rule
        assert "stroke-dasharray:" in rule.group(1)


def test_rank_order_duration_and_derived_poll_copy_are_recomputed():
    messages = _source()["messages"]
    ranked = sorted(
        messages,
        key=lambda message: (_reaction_total(message), int(message["id"])),
        reverse=True,
    )[:10]
    assert [int(message["id"]) for message in ranked] == TOP_IDS

    first = datetime.fromisoformat(messages[0]["date"])
    last = datetime.fromisoformat(messages[-1]["date"])
    assert round((last - first).total_seconds() / 3600, 2) == 67.04

    html = _html()
    for text in ("72,6%", "80,0%", "5,3%", "11,3%", "52,9%", "27,6%", "19,4%"):
        assert text in html


def test_artifact_is_one_self_contained_offline_document():
    html = _html()
    csp = (
        "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
        "img-src data:; connect-src 'none'; font-src 'none'; media-src 'none'; "
        "frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'"
    )
    assert f'content="{csp}"' in html
    assert html.lower().count("<!doctype html>") == 1
    assert html.lower().count("<html ") == 1
    assert ARTIFACT.stat().st_size <= 307_200
    assert "base64," not in html

    forbidden_patterns = (
        r"<script[^>]+\bsrc=",
        r"<link[^>]+\bstylesheet\b",
        r"@import\b",
        r"url\(",
        r"<img\b",
        r"\bsrcset=",
        r"<(?:iframe|frame|object|embed|source|video|audio)\b",
        r"<use[^>]+(?:href|xlink:href)=\"(?!#)",
        r"\bimport\s*\(",
    )
    for pattern in forbidden_patterns:
        assert not re.search(pattern, html, re.IGNORECASE)

    urls = re.findall(r"https?://[^\"'<\s]+", html)
    assert urls
    assert all(re.fullmatch(r"https://t\.me/c/1778093200/\d+", url) for url in urls)

    telegram_anchors = re.findall(
        r'<a\b[^>]*href="https://t\.me/c/1778093200/\d+"[^>]*>', html, re.IGNORECASE
    )
    assert telegram_anchors
    for anchor in telegram_anchors:
        assert 'target="_blank"' in anchor
        assert 'rel="noopener noreferrer"' in anchor
