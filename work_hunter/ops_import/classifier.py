from __future__ import annotations

from typing import Any, Iterable


def classify_ops_files(scanned_files: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    classified: list[dict[str, Any]] = []
    for item in scanned_files:
        relative = str(item.get("relative_path") or "")
        classified.append({**item, "kind": _kind_for_path(relative)})
    return classified


def _kind_for_path(relative: str) -> str:
    lowered = relative.lower()
    if "flow-state" in lowered:
        return "flow_state"
    if "wo.template" in lowered:
        return "wo_template"
    if "wo-authoring-guide" in lowered:
        return "runbook"
    if "work-orders/readme" in lowered:
        return "runbook"
    return "ops_context"
