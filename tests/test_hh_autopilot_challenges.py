from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from work_hunter.hh_autopilot.browser import HHBrowserApplicationAdapter
from work_hunter.hh_autopilot.challenges import (
    GroundedAnswerMapper,
    HHChallengeHandler,
)
from work_hunter.hh_autopilot.repository import AutopilotRepository
from work_hunter.hh_autopilot.types import (
    AutopilotState,
    FilterDecision,
    RankScore,
    RankingDecision,
)
from work_hunter.hh_transport.authorize import HHBrowserAuthorizer
from work_hunter.hh_transport.browser_session import HHBrowserSession
from work_hunter.storage import Storage


FIXTURES = Path(__file__).parent / "fixtures" / "hh_autopilot"


def required(name: str, kind: str = "text", **extra):
    return {"name": name, "type": kind, "required": True, **extra}


def test_grounded_mapping_uses_only_explicit_supported_facts() -> None:
    result = GroundedAnswerMapper().map(
        fields=[
            required("first_name"),
            required("city", "select", options=["Москва", "Казань"]),
            required("phone", "tel"),
            required("remote_ready", "radio", options=["yes", "no"]),
            {"name": "cover_letter", "type": "textarea", "required": False},
        ],
        candidate={
            "first_name": "Анна",
            "city": "Москва",
            "phone": "+79990000000",
            "remote_ready": True,
        },
        resume={"title": "Python developer"},
        prepared={"cover_letter": "Короткое письмо"},
    )

    assert result.complete is True
    assert result.answers == {
        "first_name": "Анна",
        "city": "Москва",
        "phone": "+79990000000",
        "remote_ready": "yes",
        "cover_letter": "Короткое письмо",
    }


def test_unknown_required_data_and_knowledge_question_are_never_guessed() -> None:
    mapper = GroundedAnswerMapper()
    missing = mapper.map(
        [required("desired_relocation_date")], candidate={}, resume={}
    )
    assessment = mapper.map(
        [required("solve_python_task", "knowledge")], candidate={}, resume={}
    )

    assert missing.outcome == "missing_required_data"
    assert missing.unknown_required == ("desired_relocation_date",)
    assert assessment.outcome == "manual_assessment"
    assert assessment.answers == {}


@pytest.mark.parametrize(
    ("kind", "outcome"),
    [("screening", "screening_disabled"), ("form", "form_disabled")],
)
def test_off_modes_do_not_touch_the_browser(kind: str, outcome: str) -> None:
    browser = _Calls()
    result = HHChallengeHandler(browser=browser).handle_required_flow(
        kind=kind,
        mode="off",
    )
    assert result.outcome == outcome
    assert browser.calls == []


def test_fixture_form_is_inspected_and_submitted_without_captcha_clicks() -> None:
    state: dict = {"html": (FIXTURES / "form.html").read_text(encoding="utf-8")}
    adapter = HHBrowserApplicationAdapter(lambda: _FakeContext(state))

    flow = adapter.inspect("https://hh.ru/applicant/vacancy_response?token=secret")
    result = adapter.submit_grounded_form(
        flow,
        {
            "first_name": "Анна",
            "city": "Москва",
            "phone": "+79990000000",
            "cover_letter": "Здравствуйте",
        },
    )

    assert flow.kind == "form"
    assert [field.name for field in flow.fields] == [
        "first_name",
        "city",
        "phone",
        "cover_letter",
    ]
    assert result.code == "applied"
    assert state["filled"]["city"] == "Москва"
    assert state["submit_clicks"] == 1

    captcha_state = {"html": (FIXTURES / "captcha.html").read_text(encoding="utf-8")}
    captcha = HHBrowserApplicationAdapter(lambda: _FakeContext(captcha_state)).inspect(
        "https://hh.ru/captcha?token=secret"
    )
    assert captcha.kind == "captcha"
    assert "secret" not in captcha.url
    assert captcha_state.get("submit_clicks", 0) == 0


def test_captcha_handoff_is_idempotent_releases_quota_and_resumes_with_cookies(
    tmp_path: Path,
) -> None:
    storage, repo, item, lease = _applying_case(tmp_path)
    session = HHBrowserSession(cookie_path=tmp_path / "private" / "session.json")
    session.update_from_playwright_context(
        [{"name": "hhtoken", "value": "cookie-value", "domain": ".hh.ru"}]
    )
    handler = HHChallengeHandler(repo, browser_session=session)
    try:
        first = handler.handle_captcha(
            item,
            url="https://hh.ru/captcha?token=secret",
            lease=lease,
        )
        second = handler.handle_captcha(
            item,
            url="https://hh.ru/captcha?token=secret",
            lease=lease,
        )

        assert first.challenge_id == second.challenge_id
        challenge = repo.get_challenge(first.challenge_id)
        assert challenge is not None
        assert challenge.challenge_type == "manual_captcha"
        assert "secret" not in challenge.sanitized_url
        assert repo.dispatch_reservation(item.id).state.value == "released"
        assert repo.get_item(item.id).state is AutopilotState.MANUAL_CHALLENGE

        resolved = handler.resolve(first.challenge_id, action="completed", actor="ui")
        reloaded = HHBrowserSession(cookie_path=tmp_path / "private" / "session.json")
        reloaded.load()
        assert resolved.state is AutopilotState.READY
        assert reloaded.load_cookie("hhtoken") == "cookie-value"
    finally:
        storage.close()


def test_scheduler_expiry_closes_due_manual_challenge(tmp_path: Path) -> None:
    storage, repo, item, lease = _applying_case(tmp_path)
    handler = HHChallengeHandler(
        repo,
        challenge_expiry_hours=1,
        lease_ttl_provider=lambda: 120,
        owner_token_factory=lambda: "expiry-owner",
    )
    try:
        opened = handler.handle_captcha(
            item,
            url="https://hh.ru/captcha",
            lease=lease,
        )
        challenge = repo.get_challenge(opened.challenge_id)
        assert challenge is not None
        expires_at = datetime.fromisoformat(challenge.expires_at)

        updates = handler.expire_due(expires_at)

        assert updates == (
            {
                "challenge_id": challenge.id,
                "account_id": "default",
                "item_id": item.id,
                "state": "skipped",
                "status": "expired",
            },
        )
        assert repo.get_item(item.id).state is AutopilotState.SKIPPED
        assert repo.get_challenge(challenge.id).status == "expired"
    finally:
        storage.close()


@pytest.mark.parametrize(
    ("challenge_type", "action", "expected"),
    [
        ("manual_assessment", "dismiss", AutopilotState.SKIPPED),
        ("manual_auth", "completed", AutopilotState.READY),
    ],
)
def test_assessment_and_auth_have_manual_resolution_lifecycle(
    tmp_path: Path,
    challenge_type: str,
    action: str,
    expected: AutopilotState,
) -> None:
    storage, repo, item, lease = _applying_case(tmp_path)
    handler = HHChallengeHandler(repo)
    try:
        outcome = "auth_expired" if challenge_type == "manual_auth" else challenge_type
        opened = handler.handle(outcome, item, lease)
        challenge = repo.get_challenge(opened.challenge_id)
        assert challenge is not None
        assert challenge.challenge_type == challenge_type
        assert bool(challenge.expires_at) is (challenge_type != "manual_auth")
        assert handler.resolve(opened.challenge_id, action=action, actor="ui").state is expected
    finally:
        storage.close()


def test_private_session_save_and_browser_authorizer_are_atomic_and_redacted(
    tmp_path: Path,
) -> None:
    root = tmp_path / "private"

    def sessions(profile: str) -> HHBrowserSession:
        return HHBrowserSession(cookie_path=root / "hh-sessions" / f"{profile}.json")

    authorizer = HHBrowserAuthorizer(None, private_root=root, session_factory=sessions)
    diagnostics = authorizer.import_cookies(
        "main",
        [{"name": "hhtoken", "value": "super-secret", "domain": ".hh.ru"}],
    )
    assert diagnostics["status"] == "authenticated"
    assert "super-secret" not in str(diagnostics)
    assert diagnostics["credentials_embedded"] is False
    assert not list((root / "hh-sessions").glob("*.tmp"))
    with pytest.raises(ValueError):
        authorizer.import_cookies(
            "main", [{"name": "evil", "value": "x", "domain": "example.com"}]
        )
    with pytest.raises(PermissionError):
        authorizer.logout("main", confirmation="yes")
    assert authorizer.logout("main", confirmation="LOGOUT main")["status"] == "manual_auth"


class _Calls:
    def __init__(self) -> None:
        self.calls: list = []


class _FakeLocator:
    def __init__(self, state: dict, selector: str) -> None:
        self.state = state
        self.selector = selector

    def fill(self, value: str) -> None:
        self.state.setdefault("filled", {})[_selector_name(self.selector)] = value

    def select_option(self, value: str) -> None:
        self.fill(value)

    def check(self) -> None:
        self.fill("checked")

    def uncheck(self) -> None:
        self.fill("unchecked")

    def click(self, **_kwargs) -> None:
        self.state["submit_clicks"] = self.state.get("submit_clicks", 0) + 1
        self.state["html"] = "<main data-qa='application-success'>sent</main>"


class _FakePage:
    def __init__(self, state: dict) -> None:
        self.state = state
        self.url = "https://hh.ru/applicant/vacancy_response"

    def goto(self, url: str, **_kwargs) -> None:
        self.url = url

    def content(self) -> str:
        return self.state["html"]

    def locator(self, selector: str) -> _FakeLocator:
        return _FakeLocator(self.state, selector)


class _FakeContext:
    def __init__(self, state: dict) -> None:
        self.state = state

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def new_page(self) -> _FakePage:
        return _FakePage(self.state)

    def add_cookies(self, cookies) -> None:
        self.state["cookies"] = cookies

    def cookies(self):
        return self.state.get("cookies", [])


def _selector_name(selector: str) -> str:
    if '[name="' not in selector:
        return "submit"
    return selector.split('[name="', 1)[1].split('"', 1)[0]


def _applying_case(tmp_path: Path):
    storage = Storage(tmp_path / "work-hunter.db")
    repo = AutopilotRepository(storage)
    now = datetime.now(UTC).replace(microsecond=0)
    lease = repo.acquire_lease("default", "owner", ttl_seconds=3600, now=now)
    assert lease is not None
    run = repo.create_run(
        "default",
        trigger="manual",
        policy_hash="test-policy",
        fencing_token=lease.fencing_token,
    )
    item = repo.create_item(run.id, "default", "v-1", "r-1", "preset:test")
    item = repo.record_filter_decision(
        item.id,
        expected_version=item.version,
        decision=FilterDecision(
            True,
            "hard_filters_passed",
            {
                "checks": (
                    "vacancy_open",
                    "history",
                    "blacklists",
                    "keywords_and_roles",
                    "area_and_relocation",
                    "work_format",
                    "experience",
                    "salary",
                    "candidate_constraints",
                    "application_capabilities",
                )
            },
        ),
        run_id=run.id,
        fencing_token=lease.fencing_token,
    )
    components = {
        name: 90.0
        for name in (
            "role",
            "skills",
            "experience",
            "salary",
            "work_format",
            "area",
            "industry",
        )
    }
    item = repo.record_ranking_decision(
        item.id,
        expected_version=item.version,
        decision=RankingDecision(
            True,
            False,
            "deterministic_score",
            RankScore(
                90.0,
                components,
                {name: 1 / len(components) for name in components},
            ),
        ),
        run_id=run.id,
        fencing_token=lease.fencing_token,
    )
    item = repo.finalize_ranked_candidates(
        {item.id: item.version},
        selected_item_ids=[item.id],
        resume_policy="best_resume_only",
        run_id=run.id,
        fencing_token=lease.fencing_token,
    )[0]
    cursor = storage.conn.execute(
        """
        INSERT INTO hh_application_attempts (
            vacancy_id, resume_id, status, created_at, account_profile_id,
            autopilot_run_id, autopilot_item_id, authorization_kind,
            authorization_ref, policy_hash, dispatched_at
        ) VALUES ('v-1','r-1','applying',?,'default',?,?,'autopilot',
                  'test-auth','test-policy',?)
        """,
        (now.isoformat(), run.id, item.id, now.isoformat()),
    )
    attempt_id = int(cursor.lastrowid)
    storage.conn.execute(
        "UPDATE hh_application_attempts SET autopilot_attempt_id = ? WHERE id = ?",
        (attempt_id, attempt_id),
    )
    storage.conn.execute(
        """
        INSERT INTO hh_autopilot_quota_reservations (
            attempt_id, run_id, source, account_profile_id, timezone,
            local_date, state, fencing_token, created_at
        ) VALUES (?,?,'dispatch','default','Europe/Moscow',?,'reserved',?,?)
        """,
        (attempt_id, run.id, now.date().isoformat(), lease.fencing_token, now.isoformat()),
    )
    storage.conn.execute(
        """
        INSERT INTO hh_application_account_guards (
            account_profile_id, source, source_id, owner_attempt_id,
            first_resume_id, status, application_count, created_at, updated_at
        ) VALUES ('default','hh','v-1',?,'r-1','active',0,?,?)
        """,
        (attempt_id, now.isoformat(), now.isoformat()),
    )
    storage.conn.execute(
        """
        UPDATE hh_autopilot_items
        SET state='applying', retry_stage='application', version=version+1,
            application_attempt_count=1, active_attempt_id=?, updated_at=?
        WHERE id=?
        """,
        (attempt_id, now.isoformat(), item.id),
    )
    storage.conn.commit()
    return storage, repo, repo.get_item(item.id), lease
