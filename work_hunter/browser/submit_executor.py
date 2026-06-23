from __future__ import annotations


def browser_submit_executor(plan: dict, policy: dict) -> dict:
    if not policy.get("can_apply"):
        return {"status": "blocked", "submit": False, "reason": "source_adapter_policy_required"}
    return {"status": "ready_for_confirmation", "submit": False, "plan": plan}
