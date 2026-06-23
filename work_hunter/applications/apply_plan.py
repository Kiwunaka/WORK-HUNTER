from __future__ import annotations


def dry_run_apply_plan(*, can_apply: bool) -> dict:
    return {"submit": False, "can_apply": bool(can_apply)}
