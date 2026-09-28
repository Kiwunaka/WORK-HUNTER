"""Submit one external application described by a local JSON manifest.

The receipt is created before browser submission. An interrupted run is never
automatically repeated because the remote form may already have accepted it.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from .external_apply import ExternalApplyDispatcher, ExternalApplyRequest
from .models import Job


def _save(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    receipt = args.manifest.with_name(args.manifest.stem + ".receipt.json")
    if receipt.exists():
        raise SystemExit(f"Existing receipt requires reconciliation before retry: {receipt}")
    request = ExternalApplyRequest(
        root=Path(manifest["root"]).resolve(),
        job=Job(**manifest["job"]),
        letter=manifest.get("letter", ""),
        profile=manifest["profile"],
        about=manifest.get("about", {}),
        ai_config=manifest.get("ai_config", {}),
        source_config=manifest.get("source_config", {}),
        global_config=manifest.get("global_config", {"enabled": True}),
    )
    dispatcher = ExternalApplyDispatcher()
    plan = dispatcher.plan(request)
    if plan["status"] != "ready":
        raise SystemExit(str(plan["message"]))
    _save(receipt, {"status": "dispatching", "started_at": datetime.now(timezone.utc).isoformat(),
                    "job_url": request.job.url})
    result = dispatcher.apply(request)
    _save(receipt, {"status": result.status, "job_url": request.job.url, "result": result.to_dict(),
                    "finished_at": datetime.now(timezone.utc).isoformat()})
    print(json.dumps({"status": result.status, "receipt": str(receipt)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
