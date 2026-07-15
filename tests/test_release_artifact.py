from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import tarfile
import textwrap
import tomllib
import zipfile
from pathlib import Path

import work_hunter


ROOT = Path(__file__).resolve().parents[1]
STATIC_FILES = {
    "app.css",
    "app.js",
    "index.html",
    "manifest.json",
    "sw.js",
    "ui-core.js",
    "ui-feedback.js",
    "ui-onboarding.js",
    "ui-today.js",
}
MIGRATION_FILES = {
    "0001_backbone.sql",
    "0002_account_aware_applications.sql",
}


def _docker_stages(dockerfile: str) -> dict[str, list[str]]:
    stages: dict[str, list[str]] = {}
    current_stage: str | None = None
    continued: list[str] = []

    for raw_line in dockerfile.splitlines():
        line = raw_line.strip()
        if not line or (line.startswith("#") and not continued):
            continue
        is_continued = line.endswith("\\")
        continued.append(line[:-1].rstrip() if is_continued else line)
        if is_continued:
            continue

        instruction = " ".join(continued)
        continued.clear()
        if instruction.upper().startswith("FROM "):
            parts = instruction.split()
            assert len(parts) >= 4 and parts[-2].upper() == "AS"
            current_stage = parts[-1]
            stages[current_stage] = []
            continue
        assert current_stage is not None
        stages[current_stage].append(instruction)

    assert not continued
    return stages


def _docker_json_instruction(stage: list[str], name: str) -> list[str]:
    matches = [line for line in stage if line.upper().startswith(f"{name} ")]
    assert len(matches) == 1
    value = json.loads(matches[0].split(maxsplit=1)[1])
    assert isinstance(value, list) and all(isinstance(item, str) for item in value)
    return value


def _offline_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["PIP_NO_INDEX"] = "1"
    environment["UV_OFFLINE"] = "1"
    environment.pop("PYTHONPATH", None)
    return environment


def _copy_clean_source(destination: Path) -> Path:
    source = destination / "source"
    source.mkdir()
    tracked_files = subprocess.run(
        [
            "git",
            "ls-files",
            "--",
            "pyproject.toml",
            "README.md",
            "work_hunter",
        ],
        check=True,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.splitlines()
    for relative_name in tracked_files:
        target = source / relative_name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative_name, target)
    assert tracked_files
    return source


def _build_artifact(tmp_path: Path, kind: str) -> Path:
    source = _copy_clean_source(tmp_path)
    output = tmp_path / "dist"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--no-isolation",
            f"--{kind}",
            "--outdir",
            str(output),
            str(source),
        ],
        check=True,
        cwd=tmp_path,
        env=_offline_environment(),
    )
    pattern = "*.whl" if kind == "wheel" else "*.tar.gz"
    artifacts = list(output.glob(pattern))
    assert len(artifacts) == 1
    return artifacts[0]


def test_project_and_module_versions_are_1_0_0():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["version"] == "1.0.0"
    assert work_hunter.__version__ == "1.0.0"


def test_release_metadata_declares_safe_bounds_and_build_tools():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["build-system"] == {
        "requires": ["setuptools>=75,<81"],
        "build-backend": "setuptools.build_meta",
    }
    assert project["project"]["readme"] == "README.md"
    assert project["project"]["dependencies"] == [
        "mcp>=1.27,<2",
        "requests>=2.32,<3",
        "starlette>=1.3.1,<2",
        "tzdata>=2025.2; sys_platform == 'win32'",
    ]
    extras = project["project"]["optional-dependencies"]
    assert extras["dev"] == [
        "mypy>=1.10,<3",
        "pytest>=8,<10",
        "ruff>=0.5,<1",
        "types-requests>=2.32",
    ]
    assert extras["browser"] == [
        "beautifulsoup4>=4.12,<5",
        "playwright>=1.45,<2",
    ]
    assert extras["ui"] == ["uvicorn>=0.30,<1"]
    assert extras["release"] == ["build>=1.2,<2", "pip-audit>=2.10,<3"]


def test_built_wheel_contains_ui_migrations_and_consistent_metadata(tmp_path):
    wheel = _build_artifact(tmp_path, "wheel")
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        metadata_name = next(name for name in names if name.endswith(".dist-info/METADATA"))
        metadata = archive.read(metadata_name).decode("utf-8")

    assert wheel.name.startswith("work_hunter-1.0.0-")
    assert {f"work_hunter/web/static/{name}" for name in STATIC_FILES} <= names
    assert {f"work_hunter/migrations/{name}" for name in MIGRATION_FILES} <= names
    assert "Version: 1.0.0" in metadata.splitlines()


def test_built_sdist_contains_ui_and_migrations(tmp_path):
    archive_path = _build_artifact(tmp_path, "sdist")
    with tarfile.open(archive_path, "r:gz") as archive:
        names = set(archive.getnames())

    prefix = "work_hunter-1.0.0/"
    assert archive_path.name == "work_hunter-1.0.0.tar.gz"
    assert {prefix + f"work_hunter/web/static/{name}" for name in STATIC_FILES} <= names
    assert {prefix + f"work_hunter/migrations/{name}" for name in MIGRATION_FILES} <= names


def test_installed_wheel_uses_its_own_runtime_resources(tmp_path):
    wheel = _build_artifact(tmp_path, "wheel")
    environment = _offline_environment()
    virtualenv = tmp_path / "venv"
    subprocess.run(
        [sys.executable, "-m", "venv", str(virtualenv)],
        check=True,
        cwd=tmp_path,
        env=environment,
    )
    virtualenv_python = virtualenv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    subprocess.run(
        [
            str(virtualenv_python),
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--no-index",
            str(wheel),
        ],
        check=True,
        cwd=runtime,
        env=environment,
    )
    smoke = textwrap.dedent(
        """
        import sys
        from importlib import metadata, resources
        from pathlib import Path

        import work_hunter
        from work_hunter.storage import Storage

        assert metadata.version("work-hunter") == "1.0.0"
        assert work_hunter.__version__ == "1.0.0"
        package = resources.files("work_hunter")
        static = package.joinpath("web", "static")
        for name in ("app.css", "app.js", "index.html", "manifest.json", "sw.js", "ui-core.js", "ui-feedback.js", "ui-onboarding.js", "ui-today.js"):
            resource = static.joinpath(name)
            assert resource.is_file()
            assert resource.read_bytes()
        for name in ("0001_backbone.sql", "0002_account_aware_applications.sql"):
            migration = package.joinpath("migrations", name)
            assert migration.is_file()
            assert migration.read_text(encoding="utf-8").strip()

        storage = Storage(Path(sys.argv[1]) / "smoke.sqlite3")
        try:
            versions = {
                row[0]
                for row in storage.conn.execute(
                    "SELECT version FROM schema_migrations"
                ).fetchall()
            }
        finally:
            storage.close()
        assert {"0001_backbone.sql", "0002_account_aware_applications.sql"} <= versions
        print(Path(work_hunter.__file__).resolve())
        """
    )
    completed = subprocess.run(
        [str(virtualenv_python), "-I", "-c", smoke, str(runtime)],
        check=True,
        cwd=runtime,
        env=environment,
        capture_output=True,
        text=True,
    )
    installed_module = Path(completed.stdout.strip())
    assert installed_module.is_file()
    assert not installed_module.is_relative_to(ROOT)


def test_dockerfile_installs_wheel_with_dependencies_as_non_root():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    stages = _docker_stages(dockerfile)
    assert "FROM python:3.12-slim AS builder" in dockerfile
    assert "FROM python:3.12-slim AS runtime" in dockerfile
    assert any("python -m build --wheel" in line for line in stages["builder"])
    runtime_wheel_installs = [
        line
        for line in stages["runtime"]
        if line.startswith("RUN ")
        and "python -m pip install /tmp/work_hunter" in line
    ]
    assert len(runtime_wheel_installs) == 1
    install_tokens = shlex.split(runtime_wheel_installs[0])
    assert not any(token.startswith("--no-deps") for token in install_tokens)
    assert "groupadd --gid 10001 workhunter" in dockerfile
    assert "useradd --uid 10001 --gid 10001" in dockerfile
    assert "chown -R 10001:10001 /data" in dockerfile
    assert "USER 10001:10001" in dockerfile

    entrypoint = _docker_json_instruction(stages["runtime"], "ENTRYPOINT")
    default_command = _docker_json_instruction(stages["runtime"], "CMD")
    assert entrypoint + default_command == [
        "/usr/bin/tini",
        "--",
        "work-hunter",
        "--root",
        "/data",
        "hh-auth-status",
    ]
    assert entrypoint + ["doctor"] == [
        "/usr/bin/tini",
        "--",
        "work-hunter",
        "--root",
        "/data",
        "doctor",
    ]
    workdirs = [line for line in stages["runtime"] if line.startswith("WORKDIR ")]
    assert workdirs[-1] == "WORKDIR /data"
    assert stages["runtime"].index(workdirs[-1]) > next(
        index
        for index, line in enumerate(stages["runtime"])
        if "chown -R 10001:10001 /data" in line
    )
    assert "WORK_HUNTER_ROOT" not in dockerfile


def test_optional_browser_image_path_installs_complete_browser_extra():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    runtime = _docker_stages(dockerfile)["runtime"]
    assert "ARG INSTALL_PLAYWRIGHT=false" in dockerfile
    assert '"beautifulsoup4>=4.12,<5"' in dockerfile
    assert '"playwright>=1.45,<2"' in dockerfile
    browser_installs = [
        line
        for line in runtime
        if line.startswith("RUN if ")
        and "python -m playwright install --with-deps chromium" in line
    ]
    assert len(browser_installs) == 1
    browser_install = browser_installs[0]
    assert "chmod -R a+rX /ms-playwright" in browser_install
    assert "rm -rf /var/lib/apt/lists/*" in browser_install
    assert browser_install.index("rm -rf /var/lib/apt/lists/*") > browser_install.index(
        "python -m playwright install --with-deps chromium"
    )


def test_dockerignore_allowlists_only_release_build_inputs():
    patterns = [
        line
        for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]
    assert patterns[:5] == [
        "**",
        "!pyproject.toml",
        "!README.md",
        "!work_hunter/",
        "!work_hunter/**",
    ]
    assert [pattern for pattern in patterns if pattern.startswith("!")] == patterns[1:5]
    last_exception = 4
    required_recursive_excludes = {
        "**/.superpowers",
        "**/.superpowers/**",
        "**/.work-hunter",
        "**/.work-hunter/**",
        "**/.env*",
        "**/*.db",
        "**/*.sqlite",
        "**/*.sqlite3",
        "**/*.har",
        "**/*.session",
        "**/*.cookies",
        "**/cookies.txt",
        "**/*cookies*.txt",
        "**/*cookies*.json",
        "**/*.pem",
        "**/*.key",
        "**/*.p12",
        "**/*.pfx",
    }
    for required in required_recursive_excludes:
        assert required in patterns
        assert patterns.index(required) > last_exception
