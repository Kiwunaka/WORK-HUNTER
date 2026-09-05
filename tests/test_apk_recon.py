from __future__ import annotations

import json
import zipfile

from work_hunter.apk_recon import analyze_apk


def test_apk_recon_extracts_endpoints_and_keeps_constants_private(tmp_path):
    apk_path = tmp_path / "sample.apk"
    config = {
        "api": "https://api.example.test/v1/jobs",
        "apply": "https://api.example.test/v1/applications",
        "client_id": "client-abcdef123456",
        "client_secret": "secret-987654321",
    }
    with zipfile.ZipFile(apk_path, "w") as archive:
        archive.writestr("assets/config.json", json.dumps(config))

    report = analyze_apk(
        apk_path,
        root=tmp_path,
        source="example",
        run_jadx=False,
    )

    assert report["status"] == "ok"
    assert "api.example.test" in report["hosts"]
    assert "https://api.example.test/v1/jobs" in report["api_hints"]
    assert report["constants"]["client_id"] != config["client_id"]
    assert report["constants"]["client_secret"] != config["client_secret"]

    private_report = tmp_path / report["private_report"]
    private_payload = json.loads(private_report.read_text(encoding="utf-8"))
    assert private_payload["constants"]["client_id"] == config["client_id"]
    assert private_payload["constants"]["client_secret"] == config["client_secret"]

    cached = analyze_apk(
        apk_path,
        root=tmp_path,
        source="example",
        run_jadx=False,
    )
    assert cached["cache_hit"] is True
