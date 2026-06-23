from __future__ import annotations


def build_application_policy(reasons: list[str]) -> dict:
    return {"can_apply": not reasons, "reasons": list(reasons)}
