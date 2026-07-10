from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import work_hunter.config as config_module
import work_hunter.services as services_module


_REPO_ROOT = Path(__file__).resolve().parents[1]


def test_after_fork_reset_replaces_process_locks_descriptors_and_thread_state(
    monkeypatch,
):
    reset_config = getattr(config_module, "_reset_config_state_after_fork", None)
    reset_identity = getattr(services_module, "_reset_hh_identity_lock_after_fork", None)
    assert callable(reset_config)
    assert callable(reset_identity)

    old_config_lock = config_module._CONFIG_WRITE_LOCK
    old_lock_state = config_module._CONFIG_LOCK_STATE
    old_lock_descriptors = config_module._CONFIG_LOCK_FDS
    old_identity_lock = services_module._HH_IDENTITY_WRITE_LOCK
    old_lock_state.process_id = 91
    old_lock_state.depths = {"inherited": 1}
    old_lock_descriptors.update({91, 92})
    closed: list[int] = []
    monkeypatch.setattr(config_module.os, "close", lambda fd: closed.append(fd))

    reset_config()
    reset_identity()

    assert config_module._CONFIG_WRITE_LOCK is not old_config_lock
    assert config_module._CONFIG_LOCK_STATE is not old_lock_state
    assert config_module._CONFIG_LOCK_FDS is not old_lock_descriptors
    assert config_module._CONFIG_LOCK_FDS == set()
    assert closed == [91, 92]
    assert not hasattr(config_module._CONFIG_LOCK_STATE, "process_id")
    assert not hasattr(config_module._CONFIG_LOCK_STATE, "depths")
    assert services_module._HH_IDENTITY_WRITE_LOCK is not old_identity_lock
    assert config_module._CONFIG_WRITE_LOCK.acquire(blocking=False)
    config_module._CONFIG_WRITE_LOCK.release()
    assert services_module._HH_IDENTITY_WRITE_LOCK.acquire(blocking=False)
    services_module._HH_IDENTITY_WRITE_LOCK.release()


@pytest.mark.skipif(os.name == "nt", reason="os.fork and SIGALRM are POSIX-only")
def test_fork_child_does_not_inherit_held_config_or_identity_rlocks(tmp_path):
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(
        item for item in (str(_REPO_ROOT), existing_pythonpath) if item
    )

    result = subprocess.run(
        [sys.executable, str(_REPO_ROOT / "tests" / "fork_safety_worker.py"), str(tmp_path)],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, (
        f"fork worker exit={result.returncode}; "
        f"stdout={result.stdout!r}; stderr={result.stderr!r}"
    )
    assert "child_exit=0" in result.stdout
