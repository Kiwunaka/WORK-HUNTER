from __future__ import annotations

from work_hunter.models import (
    HHAIDecision,
    HHApplicationAttempt,
    HHPendingMessage,
    HHVacancyAnalysis,
)
from work_hunter.storage import Storage


def test_hh_agent_mcp_run_lifecycle(tmp_path):
    storage = Storage(tmp_path / "db.sqlite3")

    run_id = storage.start_hh_agent_mcp_run("hh_search_vacancies", {"text": "python"})
    storage.finish_hh_agent_mcp_run(run_id, status="ok", output={"count": 2})

    runs = storage.list_hh_agent_mcp_runs()
    assert len(runs) == 1
    assert runs[0].tool_name == "hh_search_vacancies"
    assert runs[0].input == {"text": "python"}
    assert runs[0].status == "ok"
    assert runs[0].output == {"count": 2}
    assert runs[0].finished_at


def test_hh_agent_analysis_and_attempt_storage(tmp_path):
    storage = Storage(tmp_path / "db.sqlite3")
    run_id = storage.start_hh_agent_mcp_run("hh_analyze_vacancy", {})

    analysis_id = storage.save_hh_vacancy_analysis(
        HHVacancyAnalysis(
            run_id=run_id,
            vacancy_id="vac-1",
            resume_id="res-1",
            policy_hash="hash",
            score=87,
            recommended_action="apply",
            reasons=["python match"],
            risk_flags=["manual_form_possible"],
            model="test-model",
            raw_result={"ok": True},
        )
    )
    attempt_id = storage.save_hh_application_attempt(
        HHApplicationAttempt(
            run_id=run_id,
            vacancy_id="vac-1",
            resume_id="res-1",
            status="planned",
            reason="dry_run",
            letter="Hello",
            raw_result={"status": "planned"},
        )
    )

    analyses = storage.list_hh_vacancy_analysis("vac-1")
    attempts = storage.list_hh_application_attempts("vac-1")
    assert analysis_id > 0
    assert attempt_id > 0
    assert analyses[0].score == 87
    assert analyses[0].reasons == ["python match"]
    assert attempts[0].status == "planned"
    assert attempts[0].raw_result == {"status": "planned"}


def test_hh_response_dedupe_is_per_resume(tmp_path):
    storage = Storage(tmp_path / "db.sqlite3")

    first_id = storage.save_hh_response_dedupe(
        resume_id="res-1",
        dedupe_key="employer:title:description",
        vacancy_id="vac-1",
    )
    second_id = storage.save_hh_response_dedupe(
        resume_id="res-1",
        dedupe_key="employer:title:description",
        vacancy_id="vac-2",
    )
    storage.save_hh_response_dedupe(
        resume_id="res-2",
        dedupe_key="employer:title:description",
        vacancy_id="vac-3",
    )

    assert second_id == first_id
    assert storage.get_hh_response_dedupe(
        resume_id="res-1",
        dedupe_key="employer:title:description",
    )["vacancy_id"] == "vac-2"
    assert storage.get_hh_response_dedupe(
        resume_id="res-2",
        dedupe_key="employer:title:description",
    )["vacancy_id"] == "vac-3"


def test_hh_pending_message_and_ai_decision_storage(tmp_path):
    storage = Storage(tmp_path / "db.sqlite3")
    decision_id = storage.save_hh_ai_decision(
        HHAIDecision(
            action_type="apply",
            target_id="vac-1",
            model="test-model",
            policy_hash="hash",
            confidence=0.61,
            reasons=["borderline"],
            raw_result={"score": 61},
        )
    )

    pending_id = storage.create_hh_pending_message(
        HHPendingMessage(
            action_type="apply",
            payload={"vacancy_id": "vac-1"},
            confidence=0.61,
            reason="low_confidence",
            ai_decision_id=decision_id,
        )
    )
    storage.update_hh_pending_message(pending_id, status="modified", reason="user_changed", payload={"message": "warmer"})

    decisions = storage.list_hh_ai_decisions()
    pending = storage.list_hh_pending_messages()
    assert decisions[0].raw_result == {"score": 61}
    assert pending[0].status == "modified"
    assert pending[0].reason == "user_changed"
    assert pending[0].payload == {"message": "warmer"}
    assert pending[0].ai_decision_id == decision_id


def test_hh_operation_logs_roundtrip(tmp_path):
    storage = Storage(tmp_path / "db.sqlite3")

    log_id = storage.append_hh_operation_log(
        operation_id=10,
        level="info",
        message="started",
        payload={"operation": "research"},
    )

    logs = storage.list_hh_operation_logs(10)
    assert log_id > 0
    assert logs[0].level == "info"
    assert logs[0].message == "started"
    assert logs[0].payload == {"operation": "research"}
