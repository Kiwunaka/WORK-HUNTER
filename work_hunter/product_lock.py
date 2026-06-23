from __future__ import annotations

import difflib
import re
from pathlib import Path
from typing import Any


ALLOWLISTED_PRODUCT_LOCK_FILES = (
    Path("docs/developer/orchestration/flow-state.md"),
    Path("docs/developer/orchestration/wo-authoring-guide.md"),
    Path("docs/developer/orchestration/templates/WO.template.md"),
    Path("docs/developer/work-orders/README.md"),
)


def build_product_lock_preview(source_root: str | Path, target_root: str | Path) -> dict[str, Any]:
    source = Path(source_root)
    target = Path(target_root)
    if not source.exists():
        return {
            "status": "missing",
            "source": str(source),
            "read_files": [],
            "proposed_files": [],
            "files": {},
        }
    snippets = _read_allowlisted_snippets(source)
    files = _build_generated_files(snippets)
    diffs = _build_diffs(files, target)
    return {
        "status": "ok",
        "source": str(source),
        "target": str(target),
        "read_files": [Path(item).name for item in snippets],
        "read_paths": list(snippets),
        "proposed_files": sorted(files),
        "diffs": diffs,
        "files": files,
    }


def import_product_lock_assets(
    source_root: str | Path,
    target_root: str | Path,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    preview = build_product_lock_preview(source_root, target_root)
    if preview["status"] != "ok":
        preview["status"] = "preview" if dry_run else preview["status"]
        return preview
    if dry_run:
        preview["status"] = "preview"
        return preview
    target = Path(target_root)
    written: list[str] = []
    for relative, content in preview["files"].items():
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        written.append(relative)
    preview["status"] = "imported"
    preview["written_files"] = written
    return preview


def redact_product_lock_text(text: str) -> str:
    value = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    patterns = [
        (r"(?im)^(Authorization\s*:\s*)Bearer\s+\S+", r"\1***"),
        (r"(?im)^((?:Cookie|Set-Cookie|X-CSRF-Token|X-XSRF-Token)\s*:\s*).*$", r"\1***"),
        (r"(?im)^TELEGRAM_INIT_DATA\s*=.*$", "telegram_init_data=***"),
        (r"(?i)(--(?:api-key|token|access-token|refresh-token)\s+)\S+", r"\1***"),
        (r"(?i)([?&](?:access_token|refresh_token|api_key|token|hash)=)[^&\s]+", r"\1***"),
    ]
    for pattern, replacement in patterns:
        value = re.sub(pattern, replacement, value)
    return value


def _read_allowlisted_snippets(source: Path) -> dict[str, str]:
    snippets: dict[str, str] = {}
    source_resolved = source.resolve()
    for relative in ALLOWLISTED_PRODUCT_LOCK_FILES:
        path = (source / relative).resolve()
        if not _is_inside(path, source_resolved) or not path.exists() or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        snippets[relative.as_posix()] = redact_product_lock_text(text)
    return snippets


def _build_generated_files(snippets: dict[str, str]) -> dict[str, str]:
    flow_state = snippets.get("docs/developer/orchestration/flow-state.md", "")
    guide = snippets.get("docs/developer/orchestration/wo-authoring-guide.md", "")
    wo_template = snippets.get("docs/developer/orchestration/templates/WO.template.md", "")
    work_orders = snippets.get("docs/developer/work-orders/README.md", "")
    common_header = (
        "# Work Hunter Local Agent Context\n\n"
        "This file was generated from an allow-listed, redacted WO/FLOW_STATE import. "
        "It must not contain secrets, auth caches, HAR cookies, or API tokens.\n"
    )
    return {
        "AGENTS.md": (
            f"{common_header}\n"
            "## Operating Notes\n\n"
            "- Work Hunter is Windows-only, local-first, and private to one owner.\n"
            "- Real apply requires policy pass, audit, and explicit source maturity.\n\n"
            "## Imported Flow State\n\n"
            f"{_fenced(flow_state)}\n"
        ),
        ".agents/skills/work-hunter/SKILL.md": (
            "---\nname: work-hunter\n"
            "description: Local Work Hunter project context generated from redacted WO/FLOW_STATE docs\n---\n\n"
            f"{common_header}\n\n## Work Order Guide\n\n{_fenced(guide)}\n"
        ),
        ".codex/agents/work-hunter.md": (
            f"{common_header}\n\n## Codex Agent Rules\n\n"
            "- Do not read Codex/OpenCode auth cache files.\n"
            "- Use preview/diff before importing external workflow state.\n\n"
            f"## Work Orders\n\n{_fenced(work_orders)}\n"
        ),
        ".opencode/agents/work-hunter-ai.md": (
            f"{common_header}\n\n## OpenCode Agent\n\n"
            "Use official OpenCode runtime auth only; never copy stored provider credentials.\n\n"
            f"## Authoring Guide\n\n{_fenced(guide)}\n"
        ),
        "docs/developer/orchestration/FLOW_STATE.md": (
            "# FLOW_STATE\n\n"
            "Imported and redacted from the allow-listed WO/FLOW_STATE source.\n\n"
            f"{flow_state}\n"
        ),
        "docs/developer/orchestration/WO.template.md": (
            "# Work Order Template\n\n"
            "Imported and redacted from the allow-listed WO template source.\n\n"
            f"{wo_template}\n"
        ),
        "docs/ops/FLOW_STATE_TEMPLATE.md": (
            "# FLOW_STATE Template\n\n"
            "Work Hunter local operating state template imported from redacted orchestration context.\n\n"
            f"{flow_state}\n"
        ),
        "docs/ops/WO_TEMPLATE.md": (
            "# Work Order Template\n\n"
            "Work Hunter local work-order template imported from redacted orchestration context.\n\n"
            f"{wo_template}\n"
        ),
        "docs/ops/SMART_WAVE.md": (
            "# Smart Wave\n\n"
            "- Model: gpt-5.5\n"
            "- Reasoning: high\n"
            "- Max agents: 5\n"
            "- Use for architecture, source adapter design, browser flow design, campaign policy, "
            "resume/onboarding logic, UI/UX planning, safety review, and test design.\n"
        ),
        "docs/ops/PATCH_WAVE.md": (
            "# Patch Wave\n\n"
            "- Model: gpt-5.3-codex-spark\n"
            "- Reasoning: high\n"
            "- Max agents: 2\n"
            "- Use only for mechanical refactors, renames, docs, repetitive tests, and config key migrations.\n"
            "- Do not touch secrets, auth, OAuth, cookies, real apply, payment, or browser session work.\n"
        ),
        "docs/ops/REVIEW_CHECKLIST.md": (
            "# Review Checklist\n\n"
            "- Verify generated artifacts are redacted.\n"
            "- Verify real apply remains policy-gated and audited.\n"
            "- Verify candidate claims are backed by confirmed facts.\n"
            "- Verify browser/session artifacts stay inside `.work-hunter` and are not logged.\n"
        ),
        "docs/developer/work-orders/README.md": (
            "# Work Orders\n\n"
            "Imported and redacted from the allow-listed work-orders README.\n\n"
            f"{work_orders}\n"
        ),
    }


def _build_diffs(files: dict[str, str], target: Path) -> dict[str, str]:
    diffs: dict[str, str] = {}
    for relative, content in files.items():
        old = _safe_existing_text(target / relative)
        diff = difflib.unified_diff(
            redact_product_lock_text(old).splitlines(),
            content.splitlines(),
            fromfile=relative,
            tofile=relative,
            lineterm="",
        )
        diffs[relative] = "\n".join(diff)
    return diffs


def _safe_existing_text(path: Path) -> str:
    parts = {part.lower() for part in path.parts}
    if ".codex" in parts or ".opencode" in parts or path.name.lower() == "auth.json":
        return ""
    if not path.exists() or not path.is_file():
        return ""
    return path.read_text(encoding="utf-8-sig", errors="replace")


def _fenced(text: str) -> str:
    return f"```markdown\n{text.strip()}\n```"


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
