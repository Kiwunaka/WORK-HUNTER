from __future__ import annotations


def replay_hook_event(event_type: str, title: str) -> dict:
    return {"event_type": event_type, "title": title}
