from __future__ import annotations

import os
import signal
import sys
from pathlib import Path
from typing import Any

import work_hunter.config as config_module


root = Path(sys.argv[1])
path = config_module.config_path(root)
config_module.save_config(path, config_module.default_config())
fork_result = -1


def fork_inside_update(config: dict[str, Any]) -> None:
    global fork_result
    fork_result = os.fork()
    if fork_result == 0:
        signal.signal(signal.SIGALRM, lambda *_: os._exit(125))
        signal.alarm(3)
        config.setdefault("about", {})["summary"] = "child-unlocked-save"
    else:
        config.setdefault("about", {})["summary"] = "parent-locked-save"


try:
    config_module.update_config(path, fork_inside_update)
except RuntimeError as exc:
    if fork_result == 0 and "fork" in str(exc).lower():
        os._exit(0)
    raise

if fork_result == 0:
    os._exit(124)

_, child_status = os.waitpid(fork_result, 0)
child_exit = os.waitstatus_to_exitcode(child_status)
print(f"active_child_exit={child_exit}", flush=True)
raise SystemExit(child_exit)
