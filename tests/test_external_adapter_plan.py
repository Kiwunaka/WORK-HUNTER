from __future__ import annotations

import json

from work_hunter.cli import main as cli_main
from work_hunter.external_adapter_plan import build_external_adapter_plan


def _har(entries: list[dict]) -> dict:
    return {"log": {"entries": entries}}


def _entry(
    method: str,
    url: str,
    *,
    status: int = 200,
    response_text: str = "{}",
    mime: str = "application/json",
    post_data: str = "",
) -> dict:
    request: dict = {
        "method": method,
        "url": url,
        "headers": [{"name": "User-Agent", "value": "Browser"}],
    }
    if post_data:
        request["postData"] = {"text": post_data}
    return {
        "request": request,
        "response": {
            "status": status,
            "content": {
                "mimeType": mime,
                "text": response_text,
                "size": len(response_text),
            },
        },
    }


def test_build_external_adapter_plan_groups_jobs_apply_and_profile_candidates(tmp_path):
    har_path = tmp_path / "hirehi.har"
    har_path.write_text(
        json.dumps(
            _har(
                [
                    _entry(
                        "GET",
                        "https://hirehi.ru/api/search/jobs?query=python",
                        response_text='{"items":[{"id":"job-1","title":"Python Developer"}]}',
                    ),
                    _entry("GET", "https://hirehi.ru/api/auth/me", response_text='{"id":"me"}'),
                    _entry(
                        "POST",
                        "https://hirehi.ru/api/applications",
                        post_data='{"jobId":"job-1","coverLetter":"secret letter"}',
                        response_text='{"ok":true}',
                    ),
                    _entry("GET", "https://other.example/api/jobs"),
                ]
            ),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    plan = build_external_adapter_plan(har_path, source="hirehi", allowed_hosts={"hirehi.ru"})

    assert plan["source"] == "hirehi"
    assert plan["hosts"] == ["hirehi.ru"]
    assert plan["capabilities"] == {
        "jobs_api": True,
        "profile_api": True,
        "apply_api": True,
        "session_recommended": True,
    }
    assert plan["candidates"]["jobs"][0]["url"] == "https://hirehi.ru/api/search/jobs?query=python"
    assert plan["candidates"]["profile"][0]["url"] == "https://hirehi.ru/api/auth/me"
    assert plan["candidates"]["apply"][0]["method"] == "POST"
    assert plan["adapter_skeleton"]["source_name"] == "hirehi"
    assert plan["adapter_skeleton"]["jobs_endpoint"]["path"] == "/api/search/jobs"
    assert "external-session import-har hirehi" in plan["next_actions"][0]["command"]


def test_build_external_adapter_plan_accepts_utf8_sig_har(tmp_path):
    har_path = tmp_path / "hirehi-bom.har"
    har_path.write_text(
        json.dumps(_har([_entry("GET", "https://hirehi.ru/api/search/jobs")])),
        encoding="utf-8-sig",
    )

    plan = build_external_adapter_plan(har_path, source="hirehi", allowed_hosts={"hirehi.ru"})

    assert plan["capabilities"]["jobs_api"] is True


def test_external_adapter_plan_cli_outputs_json(monkeypatch, tmp_path, capsys):
    def fake_plan(path, **kwargs):
        return {
            "source": kwargs["source"],
            "hosts": sorted(kwargs["allowed_hosts"]),
            "capabilities": {"jobs_api": True},
            "candidates": {"jobs": []},
        }

    monkeypatch.setattr("work_hunter.cli.build_external_adapter_plan", fake_plan)

    cli_main(
        [
            "--root",
            str(tmp_path),
            "external-adapter-plan",
            str(tmp_path / "session.har"),
            "--source",
            "hirehi",
            "--host",
            "hirehi.ru",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["source"] == "hirehi"
    assert payload["hosts"] == ["hirehi.ru"]
