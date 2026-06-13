from __future__ import annotations

import urllib.parse
from pathlib import Path
from typing import Any

from .api_recon import analyze_har


def build_external_adapter_plan(
    path: str | Path,
    *,
    source: str,
    allowed_hosts: set[str] | None = None,
) -> dict[str, Any]:
    recon = analyze_har(path, allowed_hosts=allowed_hosts)
    endpoints = recon["endpoints"]
    candidates = {
        "jobs": _rank_candidates(endpoints, include_tag="jobs", prefer_methods={"GET"}),
        "profile": _rank_candidates(endpoints, include_tag="profile", prefer_methods={"GET"}),
        "apply": _rank_candidates(endpoints, include_tag="apply", prefer_methods={"POST", "PUT", "PATCH"}),
    }
    hosts = sorted(allowed_hosts or recon.get("by_host", {}).keys())
    skeleton = _adapter_skeleton(source, candidates)
    return {
        "source": source,
        "hosts": hosts,
        "total_endpoints": recon["total_endpoints"],
        "by_host": recon["by_host"],
        "by_tag": recon["by_tag"],
        "capabilities": {
            "jobs_api": bool(candidates["jobs"]),
            "profile_api": bool(candidates["profile"]),
            "apply_api": bool(candidates["apply"]),
            "session_recommended": _session_recommended(candidates),
        },
        "candidates": candidates,
        "adapter_skeleton": skeleton,
        "next_actions": _next_actions(source, hosts, candidates),
    }


def _rank_candidates(
    endpoints: list[dict[str, Any]],
    *,
    include_tag: str,
    prefer_methods: set[str],
    limit: int = 8,
) -> list[dict[str, Any]]:
    tagged = [endpoint for endpoint in endpoints if include_tag in set(endpoint.get("tags") or [])]
    ranked = sorted(tagged, key=lambda endpoint: (_candidate_score(endpoint, prefer_methods), endpoint["url"]))
    return [_candidate_view(endpoint) for endpoint in ranked[:limit]]


def _candidate_score(endpoint: dict[str, Any], prefer_methods: set[str]) -> int:
    method = str(endpoint.get("method") or "GET").upper()
    tags = set(endpoint.get("tags") or [])
    score = 0
    if method not in prefer_methods:
        score += 20
    if "api" not in tags:
        score += 50
    if method in {"POST", "PUT", "PATCH", "DELETE"}:
        score -= 5
    if int(endpoint.get("status") or 0) in {401, 403}:
        score += 3
    return score


def _candidate_view(endpoint: dict[str, Any]) -> dict[str, Any]:
    parsed = urllib.parse.urlsplit(endpoint["url"])
    query_keys = [key for key, _ in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)]
    return {
        "method": endpoint["method"],
        "url": endpoint["url"],
        "host": parsed.netloc,
        "path": parsed.path,
        "query_keys": query_keys,
        "status": endpoint["status"],
        "tags": endpoint["tags"],
        "response_mime": endpoint["response_mime"],
        "response_json_preview": endpoint["response_json_preview"],
        "post_data": endpoint["post_data"],
    }


def _adapter_skeleton(source: str, candidates: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    jobs = candidates["jobs"][0] if candidates["jobs"] else None
    profile = candidates["profile"][0] if candidates["profile"] else None
    apply = candidates["apply"][0] if candidates["apply"] else None
    return {
        "source_name": source,
        "jobs_endpoint": _endpoint_shape(jobs),
        "profile_endpoint": _endpoint_shape(profile),
        "apply_endpoint": _endpoint_shape(apply),
        "implementation_targets": [
            f"work_hunter/sources/{source}.py",
            "work_hunter/services.py",
            "tests/test_hh_level_sources.py",
            "tests/test_apply_plan.py",
        ],
    }


def _endpoint_shape(candidate: dict[str, Any] | None) -> dict[str, Any] | None:
    if not candidate:
        return None
    return {
        "method": candidate["method"],
        "host": candidate["host"],
        "path": candidate["path"],
        "query_keys": candidate["query_keys"],
        "requires_session": candidate["status"] in {401, 403},
    }


def _session_recommended(candidates: dict[str, list[dict[str, Any]]]) -> bool:
    return any(
        item["status"] in {401, 403} or item["method"] != "GET"
        for group in candidates.values()
        for item in group
    )


def _next_actions(source: str, hosts: list[str], candidates: dict[str, list[dict[str, Any]]]) -> list[dict[str, str]]:
    host_args = " ".join(f"--host {host}" for host in hosts)
    actions = [
        {
            "type": "import_session",
            "command": f"work-hunter external-session import-har {source} <session.har> {host_args}".strip(),
        }
    ]
    if candidates["jobs"]:
        actions.append(
            {
                "type": "promote_jobs",
                "command": f"create source adapter from {candidates['jobs'][0]['method']} {candidates['jobs'][0]['path']}",
            }
        )
    if candidates["apply"]:
        actions.append(
            {
                "type": "promote_apply",
                "command": f"create external apply action from {candidates['apply'][0]['method']} {candidates['apply'][0]['path']}",
            }
        )
    return actions
