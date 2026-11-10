"""Step 3: extract text. Returns (title, segments); each segment is (page_number | None, text)."""
import io

import pymupdf
import trafilatura
from docx import Document


def extract(content: bytes, doc_type: str) -> tuple[str | None, list[tuple[int | None, str]]]:
    if doc_type == "html":
        html = content.decode("utf-8", errors="replace")
        meta = trafilatura.extract_metadata(html)
        text = trafilatura.extract(
            html, output_format="markdown", include_tables=True, include_links=False, favor_recall=True
        )
        return (meta.title if meta else None), ([(None, text)] if text else [])

    if doc_type == "pdf":
        with pymupdf.open(stream=content, filetype="pdf") as pdf:
            title = (pdf.metadata or {}).get("title") or None
            pages = [(i + 1, p.get_text().strip()) for i, p in enumerate(pdf)]
        # Pages with no text layer are scans; OCR is not implemented yet.
        return title, [(n, t) for n, t in pages if t]

    if doc_type == "docx":
        doc = Document(io.BytesIO(content))
        lines = [p.text for p in doc.paragraphs if p.text.strip()]
        for table in doc.tables:
            for row in table.rows:
                lines.append(" | ".join(c.text.strip() for c in row.cells))
        title = doc.core_properties.title or None
        return title, ([(None, "\n\n".join(lines))] if lines else [])

    return None, []
