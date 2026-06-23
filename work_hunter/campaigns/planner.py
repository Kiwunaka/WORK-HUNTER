from __future__ import annotations

from .presets import main_python_backend_preset


def draft_campaign_plan(preset: dict | None = None) -> dict:
    return {"stage": "draft", "preset": dict(preset or main_python_backend_preset())}
