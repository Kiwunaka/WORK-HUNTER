from __future__ import annotations


def dry_run_campaign(run_id: int) -> dict:
    return {"status": "dry_run_ready", "run_id": run_id, "submit": False}
