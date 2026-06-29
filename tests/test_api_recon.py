from __future__ import annotations

import json

from work_hunter.api_recon import analyze_har, endpoint_inventory_from_har, redact_sensitive_value


def test_endpoint_inventory_from_har_redacts_auth_and_classifies_jobs():
    har = {
        "log": {
            "entries": [
                {
                    "request": {
                        "method": "GET",
                        "url": "https://getmatch.ru/api/vacancies?text=python&access_token=secret",
                        "headers": [
                            {"name": "Authorization", "value": "Bearer abc"},
                            {"name": "Cookie", "value": "sessionid=xyz"},
                        ],
                        "postData": {"text": '{"password":"secret"}'},
                    },
                    "response": {
                        "status": 200,
                        "content": {
                            "mimeType": "application/json",
                            "text": '{"items":[{"id":1,"title":"Python"}]}',
                        },
                    },
                }
            ]
        }
    }

    endpoints = endpoint_inventory_from_har(har, allowed_hosts={"getmatch.ru"})

    assert len(endpoints) == 1
    endpoint = endpoints[0]
    assert endpoint.method == "GET"
    assert endpoint.url == "https://getmatch.ru/api/vacancies?text=python&access_token=%2A%2A%2A"
    assert endpoint.headers["Authorization"] == "***"
    assert endpoint.headers["Cookie"] == "***"
    assert endpoint.post_data == '{"password":"***"}'
    assert "jobs" in endpoint.tags
    assert endpoint.response_json_preview == {"items": [{"id": 1, "title": "Python"}]}


def test_endpoint_inventory_redacts_sensitive_response_preview():
    har = {
        "log": {
            "entries": [
                {
                    "request": {
                        "method": "GET",
                        "url": "https://hirehi.ru/api/auth/me",
                        "headers": [],
                    },
                    "response": {
                        "status": 200,
                        "content": {
                            "mimeType": "application/json",
                            "text": json.dumps(
                                {
                                    "access_token": "secret-token",
                                    "profile": {
                                        "email": "me@example.com",
                                        "name": "Candidate",
                                    },
                                }
                            ),
                        },
                    },
                }
            ]
        }
    }

    endpoint = endpoint_inventory_from_har(har, allowed_hosts={"hirehi.ru"})[0]

    assert endpoint.response_json_preview == {
        "access_token": "***",
        "profile": {
            "email": "***",
            "name": "Candidate",
        },
    }


def test_endpoint_inventory_redacts_resume_identifiers_from_payloads_and_urls():
    har = {
        "log": {
            "entries": [
                {
                    "request": {
                        "method": "POST",
                        "url": "https://getmatch.ru/api/apply?resume_id=resume-secret",
                        "headers": [],
                        "postData": {"text": '{"resume_id":"resume-secret","offer_id":"34397"}'},
                    },
                    "response": {"status": 201, "content": {"mimeType": "application/json", "text": "{}"}},
                }
            ]
        }
    }

    endpoint = endpoint_inventory_from_har(har, allowed_hosts={"getmatch.ru"})[0]
    serialized = json.dumps(endpoint.to_dict(), ensure_ascii=False)

    assert "resume-secret" not in serialized
    assert endpoint.url == "https://getmatch.ru/api/apply?resume_id=%2A%2A%2A"
    assert endpoint.post_data == '{"resume_id":"***","offer_id":"34397"}'


def test_analyze_har_returns_ranked_candidate_summary(tmp_path):
    path = tmp_path / "session.har"
    path.write_text(
        json.dumps(
            {
                "log": {
                    "entries": [
                        {
                            "request": {
                                "method": "POST",
                                "url": "https://career.example/api/apply",
                                "headers": [{"name": "X-Csrf-Token", "value": "token"}],
                                "postData": {"text": '{"vacancy_id":"42","message":"hi"}'},
                            },
                            "response": {"status": 201, "content": {"mimeType": "application/json", "text": "{}"}},
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    report = analyze_har(path, allowed_hosts={"career.example"})

    assert report["total_endpoints"] == 1
    assert report["by_tag"]["apply"] == 1
    assert report["endpoints"][0]["headers"]["X-Csrf-Token"] == "***"


def test_redact_sensitive_value_handles_json_and_plain_text():
    assert redact_sensitive_value('{"access_token":"abc","nested":{"cookie":"x"}}') == (
        '{"access_token":"***","nested":{"cookie":"***"}}'
    )
    assert redact_sensitive_value("Bearer abc.def") == "***"
