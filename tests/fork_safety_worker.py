from __future__ import annotations

import os
import signal
import sys
import threading
from pathlib import Path

import work_hunter.config as config_module
import work_hunter.services as services_module
from work_hunter.services import WorkHunter


root = Path(sys.argv[1])
app = WorkHunter(root)
app.init()
locks_held = threading.Event()
held_descriptors: list[int] = []
original_lock_config_fd = config_module._lock_config_fd


def record_locked_descriptor(fd: int) -> None:
    original_lock_config_fd(fd)
    held_descriptors.append(fd)
    locks_held.set()


config_module._lock_config_fd = record_locked_descriptor


def hold_parent_locks() -> None:
    with services_module._HH_IDENTITY_WRITE_LOCK:
        with config_module._CONFIG_WRITE_LOCK:
            with config_module._config_file_lock(app.config_path):
                threading.Event().wait(10)


holder = threading.Thread(target=hold_parent_locks)
holder.start()
if not locks_held.wait(3):
    raise RuntimeError("parent lock holder did not start")

child_pid = os.fork()
if child_pid == 0:
    signal.signal(signal.SIGALRM, lambda *_: os._exit(124))
    signal.alarm(3)
    try:
        app._persist_hh_identity_patch(
            "default",
            {
                "access_token": "child-access",
                "refresh_token": "child-refresh",
            },
        )
    except BaseException:
        os._exit(126)
    signal.alarm(0)
    os._exit(0)

os.close(held_descriptors[0])
_, child_status = os.waitpid(child_pid, 0)
child_exit = os.waitstatus_to_exitcode(child_status)
print(f"child_exit={child_exit}", flush=True)
os._exit(child_exit)
