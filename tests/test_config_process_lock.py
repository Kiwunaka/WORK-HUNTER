from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from work_hunter.services import WorkHunter


_REPO_ROOT = Path(__file__).resolve().parents[1]
_WORKER_CODE = r"""
import os
import sys
import time
from pathlib import Path

import work_hunter.config as config_module
from work_hunter.services import WorkHunter


mode = sys.argv[1]
root = Path(sys.argv[2])
started = Path(sys.argv[3])
entered = Path(sys.argv[4])
release = Path(sys.argv[5])
attempting = Path(f"{started}.attempting")


def mark(path: Path) -> None:
    path.write_text("ready", encoding="utf-8")


def wait_for_release() -> None:
    deadline = time.monotonic() + 10
    while not release.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError("worker release timeout")
        time.sleep(0.02)


if mode in {"identity_hold", "identity_crash"}:
    app = WorkHunter(root)
    original_load_config = config_module.load_config

    def blocking_load_config(path):
        config = original_load_config(path)
        mark(entered)
        wait_for_release()
        if mode == "identity_crash":
            os._exit(17)
        return config

    config_module.load_config = blocking_load_config
    mark(started)
    app._persist_hh_identity_patch(
        "alice",
        {
            "access_token": "alice-new-access",
            "refresh_token": "alice-new-refresh",
        },
    )
elif mode == "config_patch":
    original_lock_config_fd = config_module._lock_config_fd

    def marked_lock_config_fd(fd):
        mark(attempting)
        return original_lock_config_fd(fd)

    config_module._lock_config_fd = marked_lock_config_fd
    mark(started)

    def patch_config(config):
        mark(entered)
        config.setdefault("about", {})["summary"] = "newer-process-state"

    config_module.update_config(config_module.config_path(root), patch_config)
elif mode == "nested_update":
    mark(started)

    def outer_patch(config):
        mark(entered)
        config.setdefault("about", {})["summary"] = "nested-state"
        config_module.save_config(config_module.config_path(root), config)

    config_module.update_config(config_module.config_path(root), outer_patch)
else:
    raise RuntimeError(f"unknown worker mode: {mode}")
"""


def _wait_for_path(path: Path, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.02)
    return path.exists()


def _start_worker(
    mode: str,
    root: Path,
    started: Path,
    entered: Path,
    release: Path,
) -> subprocess.Popen[str]:
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(
        item for item in (str(_REPO_ROOT), existing_pythonpath) if item
    )
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            _WORKER_CODE,
            mode,
            str(root),
            str(started),
            str(entered),
            str(release),
        ],
        cwd=_REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _finish_worker(
    process: subprocess.Popen[str],
    *,
    expected_code: int = 0,
    timeout: float = 10.0,
) -> None:
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate(timeout=5)
        pytest.fail(
            f"worker timed out; stdout={stdout!r}; stderr={stderr!r}",
        )
    assert process.returncode == expected_code, (
        f"worker exit={process.returncode}; stdout={stdout!r}; stderr={stderr!r}"
    )


def _initial_app(root: Path) -> WorkHunter:
    app = WorkHunter(root)
    app.save_hh_account_profile(
        "alice",
        access_token="alice-old-access",
        refresh_token="alice-old-refresh",
    )
    app.config["about"]["summary"] = "initial-state"
    app.save_config(app.config)
    return app


def test_config_transaction_is_mutually_exclusive_across_processes(tmp_path):
    app = _initial_app(tmp_path)
    first_started = tmp_path / "first.started"
    first_entered = tmp_path / "first.entered"
    second_started = tmp_path / "second.started"
    second_entered = tmp_path / "second.entered"
    release = tmp_path / "release"
    first = _start_worker(
        "identity_hold",
        tmp_path,
        first_started,
        first_entered,
        release,
    )
    second: subprocess.Popen[str] | None = None
    try:
        assert _wait_for_path(first_started)
        assert _wait_for_path(first_entered)
        second = _start_worker(
            "config_patch",
            tmp_path,
            second_started,
            second_entered,
            release,
        )
        assert _wait_for_path(second_started)
        assert _wait_for_path(Path(f"{second_started}.attempting"))
        second_was_blocked = not _wait_for_path(second_entered, timeout=1.0)
    finally:
        release.write_text("release", encoding="utf-8")
        _finish_worker(first)
        if second is not None:
            _finish_worker(second)

    assert second_was_blocked
    assert second_entered.exists()
    reloaded = WorkHunter(tmp_path)
    assert reloaded.config["about"]["summary"] == "newer-process-state"
    assert (
        reloaded.config["hh_account_profiles"]["alice"]["refresh_token"]
        == "alice-new-refresh"
    )
    lock_path = app.config_path.with_name(f".{app.config_path.name}.lock")
    assert lock_path.exists()
    assert lock_path != app.config_path


def test_config_transaction_lock_releases_when_process_exits(tmp_path):
    _initial_app(tmp_path)
    first_started = tmp_path / "crash.started"
    first_entered = tmp_path / "crash.entered"
    second_started = tmp_path / "after-crash.started"
    second_entered = tmp_path / "after-crash.entered"
    release = tmp_path / "crash-release"
    first = _start_worker(
        "identity_crash",
        tmp_path,
        first_started,
        first_entered,
        release,
    )
    second: subprocess.Popen[str] | None = None
    try:
        assert _wait_for_path(first_started)
        assert _wait_for_path(first_entered)
        second = _start_worker(
            "config_patch",
            tmp_path,
            second_started,
            second_entered,
            release,
        )
        assert _wait_for_path(second_started)
        assert _wait_for_path(Path(f"{second_started}.attempting"))
        second_was_blocked = not _wait_for_path(second_entered, timeout=1.0)
    finally:
        release.write_text("release", encoding="utf-8")
        _finish_worker(first, expected_code=17)
        if second is not None:
            _finish_worker(second)

    assert second_was_blocked
    assert second_entered.exists()
    assert WorkHunter(tmp_path).config["about"]["summary"] == "newer-process-state"


def test_config_transaction_lock_is_reentrant_in_one_process(tmp_path):
    _initial_app(tmp_path)
    started = tmp_path / "nested.started"
    entered = tmp_path / "nested.entered"
    process = _start_worker(
        "nested_update",
        tmp_path,
        started,
        entered,
        tmp_path / "unused-release",
    )

    assert _wait_for_path(started)
    _finish_worker(process, timeout=2.0)

    assert entered.exists()
    assert WorkHunter(tmp_path).config["about"]["summary"] == "nested-state"
