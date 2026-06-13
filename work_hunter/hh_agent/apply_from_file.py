from __future__ import annotations

import csv
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


HH_VACANCY_ID_RE = re.compile(r"(?:/vacancy/|vacancyId=|vacancies/)(\d+)")
TRUTHY = {"1", "true", "yes", "y", "on", "да", "д", "enabled"}
FALSY = {"0", "false", "no", "n", "off", "нет", "н", "skip", "disabled"}


@dataclass
class ApplyFromFileRow:
    row_key: str
    source_index: int
    vacancy_id: str
    url: str = ""
    enabled: bool = True
    resume_id: str = ""
    title: str = ""
    company: str = ""
    letter: str = ""
    raw: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def extract_hh_vacancy_id(value: str) -> str:
    text = str(value or "").strip()
    if text.isdigit():
        return text
    match = HH_VACANCY_ID_RE.search(text)
    return match.group(1) if match else ""


def load_apply_from_file(path: str | Path) -> list[ApplyFromFileRow]:
    source_path = Path(path)
    delimiter = "\t" if source_path.suffix.lower() == ".tsv" else ","
    with source_path.open("r", encoding="utf-8-sig", newline="") as fh:
        sample = fh.read(4096)
        fh.seek(0)
        if source_path.suffix.lower() not in {".csv", ".tsv"}:
            try:
                delimiter = csv.Sniffer().sniff(sample, delimiters=",\t;").delimiter
            except csv.Error:
                delimiter = ","
        reader = csv.DictReader(fh, delimiter=delimiter)
        return [
            _row_from_mapping(index, _normalize_mapping(row))
            for index, row in enumerate(reader, start=1)
        ]


def _normalize_mapping(row: dict[str, Any]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in row.items():
        if key is None:
            continue
        normalized[str(key).strip().lower()] = str(value or "").strip()
    return normalized


def _row_from_mapping(index: int, row: dict[str, str]) -> ApplyFromFileRow:
    url = _first_present(row, "url", "alternate_url", "vacancy_url", "hh_url", "link")
    raw_vacancy_id = (
        _first_present(row, "vacancy_id", "hh_vacancy_id", "source_id")
        or extract_hh_vacancy_id(_first_present(row, "id"))
        or extract_hh_vacancy_id(url)
    )
    vacancy_id = extract_hh_vacancy_id(raw_vacancy_id) or raw_vacancy_id
    return ApplyFromFileRow(
        row_key=_first_present(row, "row_key", "key", "source_key") or f"row-{index}",
        source_index=index,
        vacancy_id=vacancy_id,
        url=url or (f"https://hh.ru/vacancy/{vacancy_id}" if vacancy_id else ""),
        enabled=_enabled(row.get("enabled", row.get("apply", "true"))),
        resume_id=_first_present(row, "resume_id", "hh_resume_id"),
        title=_first_present(row, "title", "name", "vacancy_name"),
        company=_first_present(row, "company", "employer", "employer_name"),
        letter=_first_present(row, "letter", "message", "cover_letter", "template"),
        raw=row,
    )


def _first_present(row: dict[str, str], *keys: str) -> str:
    for key in keys:
        value = row.get(key, "")
        if value:
            return value
    return ""


def _enabled(value: str) -> bool:
    normalized = str(value or "").strip().lower()
    if normalized in FALSY:
        return False
    if normalized in TRUTHY:
        return True
    return True
