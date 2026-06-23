from __future__ import annotations

import html
import zipfile
from pathlib import Path

from ..resume_engine import render_canonical_resume


def export_docx(canonical: dict, path: str | Path) -> dict:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    body = render_canonical_resume(canonical)
    document_xml = _document_xml(body.splitlines())
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>""",
        )
        archive.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>""",
        )
        archive.writestr("word/document.xml", document_xml)
    return {
        "status": "exported",
        "source_format": "docx",
        "path": str(target),
        "bytes": target.stat().st_size,
    }


def _document_xml(lines: list[str]) -> str:
    paragraphs = "\n".join(_paragraph(line) for line in lines if line.strip())
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    {paragraphs}
    <w:sectPr/>
  </w:body>
</w:document>"""


def _paragraph(text: str) -> str:
    return f"<w:p><w:r><w:t>{html.escape(text, quote=False)}</w:t></w:r></w:p>"
