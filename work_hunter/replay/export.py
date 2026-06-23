from __future__ import annotations

from .renderer import render_timeline_cards


def export_timeline_markdown(timeline: dict) -> str:
    lines = ["# Replay Timeline", ""]
    lines.extend(f"- {card}" for card in render_timeline_cards(timeline))
    return "\n".join(lines).strip() + "\n"
