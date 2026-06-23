from __future__ import annotations

from datetime import datetime


def render_timeline_cards(timeline: dict) -> list[str]:
    cards: list[str] = []
    for event in timeline.get("events") or []:
        timestamp = _time_label(str(event.get("timestamp") or ""))
        title = str(event.get("title") or event.get("event_type") or "event")
        summary = str(event.get("summary") or "")
        cards.append(f"{timestamp} {title}" + (f" — {summary}" if summary else ""))
    return cards


def _time_label(value: str) -> str:
    try:
        return datetime.fromisoformat(value).strftime("%H:%M")
    except ValueError:
        return value[:5] if value else "--:--"
