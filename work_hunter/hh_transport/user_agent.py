from __future__ import annotations

import random


DEFAULT_ANDROID_MODELS = [
    "23053RN02A",
    "23053RN02Y",
    "23053RN02I",
    "23053RN02L",
    "23077RABDC",
    "2411DRN47C",
    "2409BRN2CA",
    "2409BRN2CG",
    "2409BRN2CY",
    "2508CRN2BE",
    "2508CRN2BC",
    "2508CRN2BG",
    "SM-A165F",
    "SM-A165F/DS",
    "SM-A165M",
    "SM-A165M/DS",
    "SM-A165F/DSB",
    "24108PCE2I",
    "MZB0KE1IN",
]


def build_android_user_agent(
    *,
    app_version: str = "7.83.0",
    android_version: str = "13",
    model: str | None = None,
    package: str = "ru.hh.android",
) -> str:
    device_model = model or random.choice(DEFAULT_ANDROID_MODELS)
    return (
        f"{package}/{app_version}, Device: {device_model}, "
        f"Android OS: {android_version}"
    )
