import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from work_hunter import __version__
from work_hunter import services as services_module
from work_hunter.models import Job, JobScore
from work_hunter.hh_autopilot.types import RunReport
from work_hunter.services import WorkHunter, _package_details
from work_hunter.sources import PUBLIC_BOARD_SOURCE_NAMES


class _AutopilotProbeRepository:
    def __init__(self) -> None:
        self.authorizations: list[dict[str, object]] = []

    def create_one_shot_authorization(self, reference_id, **kwargs) -> None:
        self.authorizations.append({"reference_id": reference_id, **kwargs})

    def status(self, account_id=None):
        return {"account": account_id, "quota": {"used": 1}}

    def history(self, account_id=None, vacancy_id=None, limit=100):
        return {"events": [{"account": account_id, "vacancy_id": vacancy_id, "limit": limit}]}

    def challenges(self, account_id=None, limit=100):
        return {"challenges": [{"account": account_id, "limit": limit}]}


class _AutopilotProbeEngine:
    def __init__(self) -> None:
        self.requests = []

    def run(self, request):
        self.requests.append(request)
        return RunReport(
            account_id=request.account_id,
            trigger=request.trigger,
            status="completed",
            run_id=len(self.requests),
            applied=1,
        )


def _autopilot_probe():
    repository = _AutopilotProbeRepository()
    engine = _AutopilotProbeEngine()
    components = SimpleNamespace(
        repository=repository,
        engine=engine,
        scheduler=SimpleNamespace(tick=lambda: {"runs": []}),
        recovery=SimpleNamespace(run=lambda account_id=None: {"account": account_id}),
    )
    return components


def test_hh_autopilot_facade_uses_one_cached_injectable_factory(tmp_path):
    components = _autopilot_probe()
    calls = []
    app = WorkHunter(
        tmp_path,
        hh_autopilot_factory=lambda service: calls.append(service) or components,
    )

    assert app.tick_hh_autopilot() == {"runs": []}
    assert app.recover_hh_autopilot("default") == {"account": "default"}
    assert app.hh_autopilot_status("default")["quota"]["used"] == 1
    assert app.hh_autopilot_history(account="default", vacancy_id="v-1")["events"]
    assert app.hh_autopilot_challenges(account="default")["challenges"]
    assert calls == [app]


def test_confirm_apply_enters_common_engine_with_exact_literal_target(tmp_path):
    components = _autopilot_probe()
    app = WorkHunter(tmp_path, hh_autopilot_factory=lambda _service: components)
    job_id = app.storage.upsert_job(
        Job(source="hh", source_id="v-1", url="https://hh.ru/vacancy/v-1", title="Python")
    )
    app.prepare_apply_plan = lambda *args, **kwargs: {
        "status": "ready",
        "source": "hh",
        "resume_id": "r-1",
        "letter": "Hello",
    }

    result = app.confirm_apply(job_id, resume_id="r-1", account="default", confirm=True)

    request = components.engine.requests[0]
    authorization = components.repository.authorizations[0]
    assert (request.account_id, request.resume_id, request.vacancy_id) == ("default", "r-1", "v-1")
    assert request.authorization.reference_id == authorization["reference_id"]
    assert authorization["targets"] == (("r-1", "v-1"),)
    assert result["status"] == "applied"
    assert result["run_id"] == 1


def test_confirm_hh_campaign_freezes_targets_in_one_authorization(tmp_path):
    components = _autopilot_probe()
    app = WorkHunter(tmp_path, hh_autopilot_factory=lambda _service: components)
    run_id = app.storage.create_hh_campaign_run()
    for index in (1, 2):
        job_id = app.storage.upsert_job(
            Job(source="hh", source_id=f"v-{index}", url=f"https://hh.ru/vacancy/v-{index}", title="Python")
        )
        app.storage.save_hh_campaign_item(
            services_module.HHCampaignItem(
                run_id=run_id,
                job_id=job_id,
                vacancy_id=f"v-{index}",
                resume_id="r-1",
                letter=f"Letter {index}",
            )
        )

    result = app.confirm_hh_campaign(run_id, confirm=True)

    authorization = components.repository.authorizations[0]
    assert authorization["targets"] == (("r-1", "v-1"), ("r-1", "v-2"))
    assert len(components.repository.authorizations) == 1
    assert {request.vacancy_id for request in components.engine.requests} == {"v-1", "v-2"}
    assert result["counts"]["applied"] == 2


class _FakeDistribution:
    def __init__(
        self,
        *,
        version: str,
        package_path: Path,
        direct_url=None,
        wheel: bool = False,
        direct_url_error: Exception | None = None,
    ) -> None:
        self.version = version
        self.package_path = package_path
        self.direct_url = direct_url
        self.wheel = wheel
        self.direct_url_error = direct_url_error

    def read_text(self, filename: str):
        if filename == "direct_url.json":
            if self.direct_url_error is not None:
                raise self.direct_url_error
            return self.direct_url
        if filename == "WHEEL":
            return "Wheel-Version: 1.0\n" if self.wheel else None
        return None

    def locate_file(self, filename: str) -> Path:
        assert filename == "work_hunter"
        return self.package_path


def _mock_package_distributions(monkeypatch, distributions) -> None:
    def fake_distributions(*, name: str):
        assert name == "work-hunter"
        return iter(distributions)

    monkeypatch.setattr(services_module.importlib.metadata, "distributions", fake_distributions)


def test_doctor_reports_version_install_mode_and_runtime_resources(tmp_path):
    doctor = WorkHunter(tmp_path).doctor()

    package = doctor["core"]["package"]
    assert package["version"] == "1.0.0"
    assert package["install_mode"] in {"editable", "wheel", "source"}
    assert package["editable"] is (package["install_mode"] == "editable")
    assert package["static"]["status"] == "ok"
    assert package["migrations"]["status"] == "ok"
    assert "config_missing" in doctor["warnings"]
    assert doctor["next_actions"][0] == "work-hunter init"


def test_doctor_blocks_when_package_resources_are_missing(monkeypatch, tmp_path):
    empty_package = tmp_path / "empty-package"
    empty_package.mkdir()
    monkeypatch.setattr("work_hunter.services.PACKAGE_ROOT", empty_package)

    doctor = WorkHunter(tmp_path / "runtime").doctor()

    assert doctor["status"] == "blocked"
    assert "missing_ui_static" in doctor["blocked"]
    assert "missing_migrations" in doctor["blocked"]


def test_package_details_skips_local_egg_info_before_matching_editable(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    package_root = project_root / "work_hunter"
    package_root.mkdir(parents=True)
    local_egg_info = _FakeDistribution(
        version="0.0.1",
        package_path=package_root,
    )
    matching_editable = _FakeDistribution(
        version="9.9.9",
        package_path=tmp_path / "site-packages" / "work_hunter",
        direct_url=json.dumps(
            {"url": project_root.as_uri(), "dir_info": {"editable": True}}
        ),
        wheel=True,
    )
    _mock_package_distributions(monkeypatch, [local_egg_info, matching_editable])

    package = _package_details(package_root)

    assert package["install_mode"] == "editable"
    assert package["editable"] is True
    assert package["version"] == __version__


@pytest.mark.parametrize("with_local_egg_info", [False, True])
def test_package_details_reports_unbound_or_ordinary_source(
    monkeypatch,
    tmp_path,
    with_local_egg_info,
):
    package_root = tmp_path / "project" / "work_hunter"
    package_root.mkdir(parents=True)
    distributions = []
    if with_local_egg_info:
        distributions.append(
            _FakeDistribution(version="8.8.8", package_path=package_root)
        )
    _mock_package_distributions(monkeypatch, distributions)

    package = _package_details(package_root)

    assert package["install_mode"] == "source"
    assert package["editable"] is False
    assert package["version"] == __version__


def test_package_details_reports_only_matching_installed_wheel(monkeypatch, tmp_path):
    package_root = tmp_path / "site-packages" / "work_hunter"
    package_root.mkdir(parents=True)
    wheel = _FakeDistribution(
        version="7.8.9",
        package_path=package_root,
        wheel=True,
    )
    _mock_package_distributions(monkeypatch, [wheel])

    package = _package_details(package_root)

    assert package["install_mode"] == "wheel"
    assert package["editable"] is False
    assert package["version"] == "7.8.9"


def test_package_details_ignores_unrelated_installed_distribution(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    package_root = project_root / "work_hunter"
    package_root.mkdir(parents=True)
    unrelated_root = tmp_path / "unrelated"
    unrelated = _FakeDistribution(
        version="6.6.6",
        package_path=unrelated_root / "work_hunter",
        direct_url=json.dumps(
            {"url": unrelated_root.as_uri(), "dir_info": {"editable": True}}
        ),
        wheel=True,
    )
    _mock_package_distributions(monkeypatch, [unrelated])

    package = _package_details(package_root)

    assert package["install_mode"] == "source"
    assert package["version"] == __version__


@pytest.mark.parametrize(
    "direct_url_case",
    [
        "missing",
        "invalid_json",
        "non_object",
        "url_wrong_type",
        "dir_info_wrong_type",
        "editable_wrong_type",
        "wrong_encoding_type",
        "decode_error",
    ],
)
def test_package_details_rejects_malformed_editable_metadata(
    monkeypatch,
    tmp_path,
    direct_url_case,
):
    project_root = tmp_path / "project"
    package_root = project_root / "work_hunter"
    package_root.mkdir(parents=True)
    direct_url = None
    direct_url_error = None
    if direct_url_case == "invalid_json":
        direct_url = "{"
    elif direct_url_case == "non_object":
        direct_url = "[]"
    elif direct_url_case == "url_wrong_type":
        direct_url = json.dumps({"url": 42, "dir_info": {"editable": True}})
    elif direct_url_case == "dir_info_wrong_type":
        direct_url = json.dumps({"url": project_root.as_uri(), "dir_info": []})
    elif direct_url_case == "editable_wrong_type":
        direct_url = json.dumps(
            {"url": project_root.as_uri(), "dir_info": {"editable": "true"}}
        )
    elif direct_url_case == "wrong_encoding_type":
        direct_url = b"\xff"
    elif direct_url_case == "decode_error":
        direct_url_error = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid byte")
    distribution = _FakeDistribution(
        version="5.5.5",
        package_path=package_root,
        direct_url=direct_url,
        direct_url_error=direct_url_error,
    )
    _mock_package_distributions(monkeypatch, [distribution])

    package = _package_details(package_root)

    assert package["install_mode"] == "source"
    assert package["editable"] is False
    assert package["version"] == __version__


@pytest.mark.parametrize("direct_url", ["file:", "file:relative", "%00"])
def test_package_details_rejects_non_absolute_or_nul_file_url(
    monkeypatch,
    tmp_path,
    direct_url,
):
    monkeypatch.chdir(tmp_path)
    project_name = "relative" if direct_url == "file:relative" else "project"
    project_root = tmp_path if direct_url == "file:" else tmp_path / project_name
    package_root = project_root / "work_hunter"
    package_root.mkdir(parents=True)
    if direct_url == "%00":
        direct_url = f"{project_root.as_uri()}%00"
    distribution = _FakeDistribution(
        version="4.4.4",
        package_path=package_root,
        direct_url=json.dumps({"url": direct_url, "dir_info": {"editable": True}}),
    )
    _mock_package_distributions(monkeypatch, [distribution])

    try:
        package = _package_details(package_root)
    except Exception as exc:
        pytest.fail(f"_package_details raised for malformed file URL: {exc!r}")

    assert package["install_mode"] == "source"
    assert package["editable"] is False
    assert package["version"] == __version__


def _use_python_profile(app: WorkHunter) -> None:
    app.config["profiles"]["python"] = {
        "queries": ["python"],
        "desired_roles": ["python"],
        "must_have_skills": ["python"],
        "nice_to_have_skills": [],
        "stop_words": [],
        "salary_min": 0,
    }
    app.config["profile"] = "python"


def _save_profile_score(
    app: WorkHunter,
    job_id: int,
    profile_id: str,
    total_score: int,
) -> None:
    app.storage.save_score(
        JobScore(
            job_id=job_id,
            profile_id=profile_id,
            total_score=total_score,
        )
    )


def _scored_job(
    app: WorkHunter,
    *,
    source_id: str,
    title: str,
    default_score: int,
    active_score: int,
) -> int:
    job_id = app.storage.upsert_job(
        Job(
            source="hh",
            source_id=source_id,
            url=f"https://example.test/{source_id}",
            title=title,
            company="Acme",
            description=f"{title} Python",
        )
    )
    _save_profile_score(app, job_id, "default", default_score)
    _save_profile_score(app, job_id, "python", active_score)
    return job_id


def test_score_existing_jobs(tmp_path):
    app = WorkHunter(root=tmp_path)
    job_id = app.storage.upsert_job(
        Job(
            source="hh",
            source_id="1",
            url="u",
            title="Python Backend",
            company="Acme",
            description="Python",
        )
    )

    count = app.score_jobs()

    assert count == 1
    loaded = app.storage.get_job(job_id)
    assert loaded is not None
    assert loaded.score is not None


def test_scores_for_two_profiles_coexist_and_reads_use_active_profile(tmp_path):
    app = WorkHunter(tmp_path)
    app.config["profiles"]["python"] = {
        "desired_roles": ["python"],
        "must_have_skills": ["python"],
        "nice_to_have_skills": [],
        "stop_words": [],
        "salary_min": 0,
    }
    app.config["profiles"]["java"] = {
        "desired_roles": ["java"],
        "must_have_skills": ["java"],
        "nice_to_have_skills": [],
        "stop_words": [],
        "salary_min": 0,
    }
    job_id = app.storage.upsert_job(
        Job(
            source="x",
            source_id="1",
            url="https://example.test/1",
            title="Python Engineer",
            description="Python",
        )
    )

    app.switch_profile("python")
    app.score_jobs()
    python_score = app.get_job(job_id).score

    app.switch_profile("java")
    app.score_jobs()
    java_score = app.get_job(job_id).score

    rows = app.storage.query_readonly(
        f"SELECT profile_id, total_score FROM job_scores "
        f"WHERE job_id = {job_id} ORDER BY profile_id"
    )
    assert [row["profile_id"] for row in rows] == ["java", "python"]
    assert python_score.profile_id == "python"
    assert java_score.profile_id == "java"
    assert python_score.total_score > java_score.total_score

    app.switch_profile("python")
    assert app.list_jobs()[0].score.profile_id == "python"
    assert json.loads(app.export_jobs())[0]["score"]["profile_id"] == "python"


def test_strategy_and_daily_reports_use_active_profile_scores(tmp_path):
    app = WorkHunter(tmp_path)
    _use_python_profile(app)
    _scored_job(
        app,
        source_id="report",
        title="Python Report",
        default_score=10,
        active_score=90,
    )

    strategy = app.strategy_report("active-profile")
    daily = app.daily_report()

    assert strategy["top_jobs"][0]["score"]["profile_id"] == "python"
    assert daily["top"][0]["score"]["profile_id"] == "python"


def test_application_and_summary_exports_use_active_profile_scores(tmp_path):
    app = WorkHunter(tmp_path)
    _use_python_profile(app)
    job_id = _scored_job(
        app,
        source_id="export",
        title="Python Export",
        default_score=10,
        active_score=90,
    )
    app.storage.save_application(job_id, "applied")

    applications = json.loads(app.export_applications(format="json"))
    report = json.loads(app.export_report(format="json"))

    assert applications[0]["job"]["score"] == 90
    assert report["stats"]["score_distribution"] == {
        "0-20": 0,
        "21-40": 0,
        "41-60": 0,
        "61-80": 0,
        "81-100": 1,
    }


def test_description_search_ranks_jobs_for_active_profile(monkeypatch, tmp_path):
    app = WorkHunter(tmp_path)
    _use_python_profile(app)
    active_id = _scored_job(
        app,
        source_id="active-winner",
        title="Python Active Winner",
        default_score=10,
        active_score=90,
    )
    _scored_job(
        app,
        source_id="default-winner",
        title="Python Default Winner",
        default_score=95,
        active_score=20,
    )

    def fail_chat(*args, **kwargs):
        raise RuntimeError("offline")

    monkeypatch.setattr("work_hunter.services.chat_completion", fail_chat)

    jobs = app.search_by_description("Python", limit=1)

    assert jobs[0]["id"] == active_id
    assert jobs[0]["score"]["profile_id"] == "python"


def test_market_trends_samples_jobs_for_active_profile(monkeypatch, tmp_path):
    app = WorkHunter(tmp_path)
    _use_python_profile(app)
    _scored_job(
        app,
        source_id="active-winner",
        title="Python Active Winner",
        default_score=10,
        active_score=90,
    )
    _scored_job(
        app,
        source_id="default-winner",
        title="Python Default Winner",
        default_score=95,
        active_score=20,
    )
    prompts: list[str] = []

    def capture_chat(messages, config):
        prompts.append(messages[-1]["content"])
        return "trend"

    monkeypatch.setattr("work_hunter.services.chat_completion", capture_chat)

    assert app.market_trends(limit=1) == "trend"
    assert "Python Active Winner" in prompts[0]
    assert "Python Default Winner" not in prompts[0]


def test_campaign_eligibility_uses_active_profile_score(monkeypatch, tmp_path):
    app = WorkHunter(tmp_path)
    _use_python_profile(app)
    job_id = _scored_job(
        app,
        source_id="campaign",
        title="Python Campaign",
        default_score=10,
        active_score=90,
    )
    monkeypatch.setattr(
        app,
        "prepare_apply_plan",
        lambda job_id, resume_id=None: {
            "status": "ready",
            "resume_id": resume_id or "resume-1",
            "letter": "Hello",
            "risk_flags": [],
        },
    )

    run = app.plan_hh_campaign(limit=10, min_score=50)
    items = app.storage.list_hh_campaign_items(run["id"])

    assert len(items) == 1
    assert items[0].job_id == job_id
    assert items[0].status == "ready"


@pytest.mark.parametrize(
    "legacy_profile",
    [
        pytest.param({"desired_roles": ["python"]}, id="flat-dict"),
        pytest.param("", id="empty"),
    ],
)
def test_chat_uses_default_score_for_legacy_profile(
    monkeypatch,
    tmp_path,
    legacy_profile,
):
    app = WorkHunter(tmp_path)
    app.config["profile"] = legacy_profile
    job_id = app.storage.upsert_job(
        Job(
            source="hh",
            source_id="chat-legacy",
            url="https://example.test/chat-legacy",
            title="Python Chat",
        )
    )
    _save_profile_score(app, job_id, "default", 73)
    captured_messages: list[list[dict[str, str]]] = []

    def capture_chat(messages, config):
        captured_messages.append(messages)
        return "ok"

    monkeypatch.setattr("work_hunter.services.chat_completion", capture_chat)

    assert app.chat([{"role": "user", "content": "fit?"}], job_id=job_id) == "ok"
    assert "Score: total=73" in captured_messages[0][0]["content"]


def test_prepare_letter_for_existing_job(tmp_path):
    app = WorkHunter(root=tmp_path)
    job_id = app.storage.upsert_job(
        Job(source="hh", source_id="1", url="u", title="Python Backend", company="Acme")
    )

    draft = app.prepare_letter(job_id)

    assert draft.job_id == job_id
    assert "Acme" in draft.body


def test_sync_sources_default_includes_public_board_sources(monkeypatch, tmp_path):
    app = WorkHunter(root=tmp_path)
    collected: list[str] = []

    class EmptyCollector:
        def collect(self, profile, limit=None):
            return []

    def fake_collector(self, source_name, source_config, *, backend=None):
        collected.append(source_name)
        return EmptyCollector()

    monkeypatch.setattr(WorkHunter, "_collector", fake_collector)

    app.sync_sources(sources=None, limit=0)

    assert collected == ["hh", "habr", "geekjob", "telegram", *PUBLIC_BOARD_SOURCE_NAMES]
