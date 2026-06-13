from __future__ import annotations

import random


def should_sanity_sample(*, rate: float, rng: random.Random | None = None) -> bool:
    if rate <= 0:
        return False
    if rate >= 1:
        return True
    generator = rng or random
    return generator.random() < rate
