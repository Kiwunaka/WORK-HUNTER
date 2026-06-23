from __future__ import annotations

from work_hunter.services import WorkHunter


def test_database_model_contains_roadmap_tables(tmp_path):
    app = WorkHunter(root=tmp_path)
    rows = app.storage.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    tables = {row["name"] for row in rows}

    expected = {
        "candidate_profiles",
        "candidate_facts",
        "candidate_claims",
        "candidate_evidence",
        "resume_assets",
        "resume_variants",
        "resume_variant_diffs",
        "application_packs",
        "application_templates",
        "application_previews",
        "source_sessions",
        "source_capabilities",
        "source_adapter_maturity",
        "browser_profiles",
        "browser_recordings",
        "form_mappings",
        "campaign_presets",
        "hh_campaign_runs",
        "hh_campaign_items",
        "policy_decisions",
        "replay_events",
        "replay_screenshots",
        "ai_runs",
        "agent_runs",
        "audit_logs",
    }

    assert expected <= tables
