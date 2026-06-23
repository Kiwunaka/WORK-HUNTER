from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .agent_profiles import public_policy


def agent_orchestrator_policy() -> dict[str, Any]:
    return public_policy()


def build_agent_orchestrator_files(policy: Mapping[str, Any] | None = None) -> dict[str, str]:
    resolved_policy = dict(policy or agent_orchestrator_policy())
    waves = list(resolved_policy.get("waves") or [])
    repo_skills = [str(item) for item in resolved_policy.get("repo_skills") or []]
    files: dict[str, str] = {
        "AGENTS.md": _agents_md(resolved_policy),
        ".opencode/agents/work-hunter-ai.md": _opencode_agent_md(resolved_policy),
    }
    for wave in waves:
        files[f".codex/agents/{wave['name']}.md"] = _wave_agent_md(wave)
    for skill in repo_skills:
        files[f".agents/skills/{skill}/SKILL.md"] = _skill_md(skill)
    return files


def agent_orchestrator_status(root: str | Path, *, dry_run: bool = False) -> dict[str, Any]:
    target = Path(root)
    files = build_agent_orchestrator_files()
    proposed = sorted(files)
    existing = sorted(relative for relative in proposed if (target / relative).exists())
    missing = sorted(relative for relative in proposed if not (target / relative).exists())
    status = "preview" if dry_run else ("ready" if not missing else "partial")
    return {
        "status": status,
        "proposed_files": proposed,
        "existing_files": existing,
        "missing_files": missing,
        "policy": agent_orchestrator_policy(),
    }


def write_agent_orchestrator_assets(root: str | Path, *, dry_run: bool = False) -> dict[str, Any]:
    target = Path(root)
    if dry_run:
        return agent_orchestrator_status(target, dry_run=True)

    files = build_agent_orchestrator_files()
    written: list[str] = []
    for relative, content in files.items():
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        written.append(relative)

    status = agent_orchestrator_status(target, dry_run=False)
    status["status"] = "ready"
    status["written_files"] = sorted(written)
    status["missing_files"] = []
    return status


def _agents_md(policy: Mapping[str, Any]) -> str:
    skill_lines = "\n".join(f"- {skill}" for skill in policy.get("repo_skills") or [])
    wave_lines = "\n".join(
        f"- {wave['name']}: {wave['model']} / {wave['reasoning']} / max_agents {wave['max_agents']}"
        for wave in policy.get("waves") or []
    )
    return (
        "# Work Hunter Agent Orchestrator\n\n"
        "Work Hunter is a Windows-only, local-first command center for one owner.\n\n"
        "## Safety\n\n"
        "- Never read Codex/OpenCode auth cache files.\n"
        "- Never copy API keys, cookies, browser sessions, auth headers, or tokens into prompts.\n"
        "- Real apply stays policy-gated, audited, and source-maturity gated.\n\n"
        "## Waves\n\n"
        f"{wave_lines}\n\n"
        "## Repo Skills\n\n"
        f"{skill_lines}\n"
    )


def _opencode_agent_md(policy: Mapping[str, Any]) -> str:
    return (
        "# Work Hunter AI Agent\n\n"
        "Use the local runtime authentication configured by the user. Do not read or export "
        "stored Codex/OpenCode auth caches.\n\n"
        "## Default Orchestration\n\n"
        f"{_policy_block(policy)}\n"
    )


def _wave_agent_md(wave: Mapping[str, Any]) -> str:
    allowed = "\n".join(f"  - {item}" for item in wave.get("allowed_tasks") or [])
    forbidden_paths = "\n".join(f"  - {item}" for item in wave.get("forbidden_paths") or [])
    forbidden_topics = "\n".join(f"  - {item}" for item in wave.get("forbidden_topics") or [])
    text = (
        f"# {wave['name']}\n\n"
        f"name: {wave['name']}\n"
        f"max_agents: {wave['max_agents']}\n"
        f"model: {wave['model']}\n"
        f"reasoning: {wave['reasoning']}\n\n"
        "allowed_tasks:\n"
        f"{allowed}\n"
    )
    if forbidden_paths:
        text += f"\nforbidden_paths:\n{forbidden_paths}\n"
    if forbidden_topics:
        text += f"\nforbidden_topics:\n{forbidden_topics}\n"
    return text


def _skill_md(skill: str) -> str:
    title = skill.replace("-", " ").title()
    return (
        "---\n"
        f"name: {skill}\n"
        f"description: Work Hunter repo-local guidance for {title}\n"
        "---\n\n"
        f"# {title}\n\n"
        "Use this skill only inside the Work Hunter repository. Keep all outputs truthful, "
        "local-first, redacted, and policy-gated.\n\n"
        "## Rules\n\n"
        "- Do not read Codex/OpenCode auth cache files.\n"
        "- Do not expose tokens, cookies, API keys, auth headers, or browser sessions.\n"
        "- Prefer dry-run, preview, and audit trails before any irreversible action.\n"
    )


def _policy_block(policy: Mapping[str, Any]) -> str:
    lines: list[str] = []
    for wave in policy.get("waves") or []:
        lines.extend(
            [
                f"- name: {wave['name']}",
                f"  max_agents: {wave['max_agents']}",
                f"  model: {wave['model']}",
                f"  reasoning: {wave['reasoning']}",
            ]
        )
    return "\n".join(lines)
