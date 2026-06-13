from __future__ import annotations

import random


DEFAULT_ANDROID_MODELS = [
    "Pixel 7",
    "Pixel 8",
    "SM-G991B",
    "SM-S911B",
    "Mi 11",
    "M2102K1G",
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
        f"{package}/{app_version} "
        f"(Android {android_version}; {device_model}) "
        "HHApplicant/WorkHunter"
    )
