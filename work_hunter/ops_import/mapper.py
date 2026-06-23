from __future__ import annotations

from typing import Any, Iterable


OPS_TARGETS = (
    "AGENTS.md",
    "docs/ops/WO_TEMPLATE.md",
    "docs/ops/FLOW_STATE_TEMPLATE.md",
    "docs/ops/SMART_WAVE.md",
    "docs/ops/PATCH_WAVE.md",
    "docs/ops/REVIEW_CHECKLIST.md",
    ".agents/skills/*",
    ".codex/agents/*",
    ".opencode/agents/*",
)


def map_ops_import(classified_files: Iterable[dict[str, Any]], generated_files: dict[str, str]) -> dict[str, Any]:
    return {
        "source_files": [dict(item) for item in classified_files],
        "target_files": sorted(generated_files),
        "ops_targets": list(OPS_TARGETS),
    }
