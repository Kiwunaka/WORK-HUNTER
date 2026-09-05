from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import urllib.parse
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .config import data_dir


URL_RE = re.compile(r"https?://[^\s\"'<>\\]{5,}", re.IGNORECASE)
CONSTANT_RE = re.compile(
    r"(?i)\b(client[_-]?id|client[_-]?secret|api[_-]?key|app[_-]?id)\b"
    r"[\s\"':=]{1,24}([A-Za-z0-9._~+/=-]{6,})"
)
ASCII_STRINGS_RE = re.compile(rb"[\x20-\x7e]{4,}")
TEXT_SUFFIXES = {
    ".xml",
    ".json",
    ".txt",
    ".js",
    ".kt",
    ".java",
    ".smali",
    ".properties",
    ".gradle",
}
APK_REPORT_VERSION = 2


@dataclass(frozen=True)
class APKCredentialCandidate:
    kind: str
    value: str
    origin: str

    def private_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "value": self.value, "origin": self.origin}

    def safe_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "value": _mask_value(self.value),
            "origin": self.origin,
        }


def analyze_apk(
    path: str | Path,
    *,
    root: str | Path,
    source: str,
    run_jadx: bool = False,
    timeout_seconds: int = 180,
) -> dict[str, Any]:
    """Inspect an APK and persist raw recon only inside ignored local state."""

    apk_path = Path(path).expanduser().resolve()
    if not apk_path.is_file():
        raise FileNotFoundError(apk_path)
    if not zipfile.is_zipfile(apk_path):
        raise ValueError(f"Not a valid APK/ZIP archive: {apk_path}")
    safe_source = _safe_source_name(source)
    output_dir = data_dir(root) / "research" / "apk" / safe_source
    output_dir.mkdir(parents=True, exist_ok=True)
    private_path = output_dir / "report.private.json"
    apk_digest = _file_sha256(apk_path)
    cached = _load_cached_report(private_path, apk_digest, run_jadx=run_jadx)
    if cached is not None:
        return _safe_report(cached, private_path, cache_hit=True)

    strings: list[tuple[str, str]] = []
    entries: list[str] = []
    with zipfile.ZipFile(apk_path) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            entries.append(info.filename)
            if not _interesting_entry(info.filename, info.file_size):
                continue
            try:
                payload = archive.read(info)
            except (KeyError, OSError, RuntimeError):
                continue
            strings.extend((info.filename, item) for item in _extract_strings(payload))

    jadx_result: dict[str, Any] = {"status": "not_requested"}
    if run_jadx:
        jadx_result = _run_jadx(
            apk_path,
            output_dir / "jadx",
            timeout_seconds=timeout_seconds,
        )
        if jadx_result.get("status") in {"ok", "partial", "reused"}:
            strings.extend(_strings_from_tree(output_dir / "jadx"))

    urls = sorted({url.rstrip(".,;)") for _, text in strings for url in URL_RE.findall(text)})
    credentials = _credential_candidates(strings)
    constants = {item.kind: item.value for item in credentials}
    hosts = sorted(
        {
            urllib.parse.urlsplit(url).hostname or ""
            for url in urls
            if urllib.parse.urlsplit(url).hostname
        }
    )
    private_report = {
        "status": "ok",
        "report_version": APK_REPORT_VERSION,
        "apk_sha256": apk_digest,
        "source": safe_source,
        "apk": str(apk_path),
        "entries": entries,
        "urls": urls,
        "hosts": hosts,
        "credential_candidates": [item.private_dict() for item in credentials],
        "constants": constants,
        "jadx": jadx_result,
        "adapter_hints": _adapter_hints(urls),
        "api_hints": _api_hints(urls),
        "route_hints": _route_hints(strings),
    }
    with private_path.open("w", encoding="utf-8") as handle:
        json.dump(private_report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return _safe_report(private_report, private_path, cache_hit=False)


def _run_jadx(
    apk_path: Path,
    output_dir: Path,
    *,
    timeout_seconds: int,
) -> dict[str, Any]:
    executable = shutil.which("jadx") or shutil.which("jadx.bat")
    if executable is None:
        return {
            "status": "missing",
            "message": "jadx is not installed or not in PATH",
        }
    output_dir.mkdir(parents=True, exist_ok=True)
    apk_digest = _file_sha256(apk_path)
    digest_path = output_dir / ".apk.sha256"
    existing_files = sum(1 for path in output_dir.rglob("*") if path.is_file())
    if (
        existing_files
        and digest_path.is_file()
        and digest_path.read_text(encoding="utf-8", errors="replace").strip()
        == apk_digest
    ):
        return {
            "status": "reused",
            "exit_code": 0,
            "stderr": "",
            "output_dir": str(output_dir),
            "generated_files": existing_files,
        }
    completed = subprocess.run(
        [executable, "--show-bad-code", "-d", str(output_dir), str(apk_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=max(10, timeout_seconds),
        check=False,
    )
    generated_files = sum(1 for path in output_dir.rglob("*") if path.is_file())
    status = "ok" if completed.returncode == 0 else "error"
    if completed.returncode != 0 and generated_files:
        status = "partial"
    if generated_files:
        digest_path.write_text(apk_digest + "\n", encoding="utf-8")
    return {
        "status": status,
        "exit_code": completed.returncode,
        "stderr": completed.stderr[-1000:],
        "output_dir": str(output_dir),
        "generated_files": generated_files,
    }


def _interesting_entry(name: str, size: int) -> bool:
    if size <= 0 or size > 50 * 1024 * 1024:
        return False
    lowered = name.casefold()
    return (
        lowered.endswith((".dex", ".arsc"))
        or Path(lowered).suffix in TEXT_SUFFIXES
        or lowered.startswith(("assets/", "res/raw/", "res/xml/"))
        or lowered == "androidmanifest.xml"
    )


def _extract_strings(payload: bytes) -> list[str]:
    found = {
        match.group(0).decode("utf-8", errors="replace")
        for match in ASCII_STRINGS_RE.finditer(payload)
    }
    if b"\x00" in payload:
        decoded = payload.decode("utf-16le", errors="ignore")
        found.update(
            match.group(0)
            for match in re.finditer(r"[\x20-\x7e]{4,}", decoded)
        )
    return sorted(found)


def _strings_from_tree(root: Path) -> Iterable[tuple[str, str]]:
    rg = shutil.which("rg") or shutil.which("rg.exe")
    if rg is not None:
        yield from _strings_from_tree_rg(root, rg)
        return
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.casefold() not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        relative = str(path.relative_to(root))
        for line in text.splitlines():
            clean = line.strip()
            if clean and _relevant_source_line(clean):
                yield relative, clean


def _strings_from_tree_rg(root: Path, executable: str) -> Iterable[tuple[str, str]]:
    pattern = (
        r"https?://|client[_-]?(?:id|secret)|api[_-]?key|"
        r"/(?:vacanc|negotiat|applic|resume|oauth|token|search)"
    )
    process = subprocess.Popen(
        [
            executable,
            "--json",
            "--ignore-case",
            "--glob",
            "*.{java,kt,xml,json,txt,smali,properties,gradle}",
            "--regexp",
            pattern,
            str(root),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert process.stdout is not None
    for raw_line in process.stdout:
        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "match":
            continue
        data = event.get("data") or {}
        path_data = data.get("path") or {}
        lines_data = data.get("lines") or {}
        origin = str(path_data.get("text") or "")
        clean = str(lines_data.get("text") or "").strip()
        if clean:
            yield origin, clean
    process.stdout.close()
    process.wait()


def _relevant_source_line(value: str) -> bool:
    lowered = value.casefold()
    return (
        "http://" in lowered
        or "https://" in lowered
        or CONSTANT_RE.search(value) is not None
        or any(
            marker in lowered
            for marker in (
                "/vacanc",
                "/negotiat",
                "/applic",
                "/resume",
                "/oauth",
                "/token",
                "/search",
            )
        )
    )


def _credential_candidates(
    strings: Iterable[tuple[str, str]],
) -> list[APKCredentialCandidate]:
    candidates: list[APKCredentialCandidate] = []
    seen: set[tuple[str, str]] = set()
    for origin, text in strings:
        for match in CONSTANT_RE.finditer(text):
            kind = match.group(1).casefold().replace("-", "_")
            value = match.group(2)
            key = (kind, value)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                APKCredentialCandidate(kind=kind, value=value, origin=origin)
            )
    return candidates


def _adapter_hints(urls: list[str]) -> dict[str, list[str]]:
    return {
        "oauth": [url for url in urls if any(item in url.casefold() for item in ("oauth", "token", "authorize"))],
        "jobs": [url for url in urls if any(item in url.casefold() for item in ("vacancy", "vacancies", "jobs", "offers"))],
        "apply": [url for url in urls if any(item in url.casefold() for item in ("apply", "response", "negotiation", "application"))],
    }


def _api_hints(urls: list[str]) -> list[str]:
    markers = (
        "/api/",
        "/v1/",
        "/v2/",
        "vacancy",
        "jobs",
        "apply",
        "application",
        "oauth",
        "token",
    )
    return [url for url in urls if any(marker in url.casefold() for marker in markers)]


def _route_hints(strings: Iterable[tuple[str, str]]) -> list[dict[str, str]]:
    markers = (
        "/vacanc",
        "/negotiat",
        "/applic",
        "/resume",
        "/oauth",
        "/token",
        "/search",
    )
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for origin, text in strings:
        lowered = text.casefold()
        if not any(marker in lowered for marker in markers):
            continue
        if CONSTANT_RE.search(text):
            continue
        clean = re.sub(r"\s+", " ", text).strip()[:500]
        key = (origin, clean)
        if not clean or key in seen:
            continue
        seen.add(key)
        result.append({"origin": origin, "text": clean})
        if len(result) >= 500:
            break
    return result


def _mask_value(value: str) -> str:
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}***{value[-4:]}"


def _safe_source_name(value: str) -> str:
    source = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value.strip()).strip("-.")
    if not source:
        raise ValueError("source name is required")
    return source


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_cached_report(
    path: Path,
    apk_digest: str,
    *,
    run_jadx: bool,
) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if payload.get("report_version") != APK_REPORT_VERSION:
        return None
    if payload.get("apk_sha256") != apk_digest:
        return None
    jadx_status = str((payload.get("jadx") or {}).get("status") or "")
    if run_jadx and jadx_status not in {"ok", "partial", "reused"}:
        return None
    return payload


def _safe_report(
    private_report: dict[str, Any],
    private_path: Path,
    *,
    cache_hit: bool,
) -> dict[str, Any]:
    safe = dict(private_report)
    candidates = []
    for item in private_report.get("credential_candidates") or []:
        if not isinstance(item, dict):
            continue
        candidates.append(
            {
                "kind": str(item.get("kind") or ""),
                "value": _mask_value(str(item.get("value") or "")),
                "origin": str(item.get("origin") or ""),
            }
        )
    safe["credential_candidates"] = candidates
    safe["constants"] = {
        str(key): _mask_value(str(value))
        for key, value in (private_report.get("constants") or {}).items()
    }
    safe["private_report"] = str(private_path)
    safe["secrets_redacted"] = True
    safe["cache_hit"] = cache_hit
    return safe
