from __future__ import annotations

from pathlib import Path

from ..resume_engine import render_canonical_resume


def export_pdf(canonical: dict, path: str | Path) -> dict:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    text = render_canonical_resume(canonical)
    content = _content_stream(text.splitlines())
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(content)).encode("ascii") + b" >>\nstream\n" + content + b"\nendstream",
    ]
    data = _pdf_bytes(objects)
    target.write_bytes(data)
    return {
        "status": "exported",
        "source_format": "pdf",
        "path": str(target),
        "bytes": len(data),
    }


def _content_stream(lines: list[str]) -> bytes:
    commands = ["BT", "/F1 12 Tf", "14 TL", "72 740 Td"]
    for line in [item for item in lines if item.strip()]:
        commands.append(f"({_pdf_escape(line)}) Tj")
        commands.append("T*")
    commands.append("ET")
    return "\n".join(commands).encode("utf-8")


def _pdf_bytes(objects: list[bytes]) -> bytes:
    chunks = [b"%PDF-1.4\n"]
    offsets: list[int] = []
    size = len(chunks[0])
    for index, obj in enumerate(objects, start=1):
        offsets.append(size)
        chunk = f"{index} 0 obj\n".encode("ascii") + obj + b"\nendobj\n"
        chunks.append(chunk)
        size += len(chunk)
    xref_offset = size
    xref = [b"xref\n", f"0 {len(objects) + 1}\n".encode("ascii"), b"0000000000 65535 f \n"]
    for offset in offsets:
        xref.append(f"{offset:010d} 00000 n \n".encode("ascii"))
    trailer = (
        b"trailer\n"
        + f"<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    return b"".join(chunks + xref + [trailer])


def _pdf_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
