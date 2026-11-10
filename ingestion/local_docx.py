"""Parse the state referral-directory DOCX files (C:\\Mahi\\Kleza\\Healtcare).

The files share a shape (numbered categories, one Name/Location/Description/Website table per
category) but differ in layout, so the document body is walked in order and these variants handled:
  * category headings as paragraphs, emoji-prefixed paragraphs, or inside 1x1 layout tables
  * tables with a blank header row, or a first column holding "Name / Location" on two lines
  * websites shown as link text such as "Visit Website" (the URL is read from the hyperlink)
  * category list only in a table of contents at the top (tables paired with categories by order)

Output is markdown-ish text that chunk.py understands: "## Category" headings, one record per paragraph.

    python -m ingestion.local_docx [--dir PATH] [--limit 5]     # parse and print a summary
"""
import argparse
import re
from dataclasses import dataclass, field
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

DEFAULT_DIR = Path(r"C:\Mahi\Kleza\Healtcare")

STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
    "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia",
    "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}
DEFAULT_COLUMNS = ["Name", "Location", "Description", "Website"]
HEADING_RE = re.compile(r"^\W*(\d+)\.\s+(.+)$", re.UNICODE)
DESC_LINE_RE = re.compile(r"^(?P<loc>.+?)\s*Description:\s*(?P<desc>.*)$")
LINK_LINE_RE = re.compile(r"^(?:Link|Website):\s*(\S.*)$", re.I)


@dataclass
class Entry:
    category: str
    fields: dict[str, str]


@dataclass
class ParsedDirectory:
    path: Path
    state_code: str | None
    title: str
    categories: list[str]
    notes: list[str] = field(default_factory=list)
    entries: list[Entry] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def segments(self) -> list[tuple[int | None, str]]:
        """Shape expected by chunk_segments: [(page_number, text)]."""
        parts = [f"# {self.title}", *self.notes[:2]]
        current = None
        for e in self.entries:
            if e.category != current:
                current = e.category
                parts.append(f"## {current}")
            parts.append("; ".join(f"{k}: {v}" for k, v in e.fields.items() if v))
        return [(None, "\n\n".join(parts))]


def _detect_state(*texts: str) -> str | None:
    hay = " ".join(texts).lower()
    for code, name in sorted(STATES.items(), key=lambda kv: -len(kv[1])):
        if name.lower().replace(" ", "") in hay.replace(" ", "").replace("_", ""):
            return code
    return None


def _cell_url(cell, part) -> str | None:
    for link in cell._tc.iter(qn("w:hyperlink")):
        rid = link.get(qn("r:id"))
        if rid and rid in part.rels:
            return part.rels[rid].target_ref
    return None


def _blocks(doc):
    """Yield ('p', Paragraph) / ('t', Table) in document order."""
    for child in doc.element.body.iterchildren():
        if child.tag == qn("w:p"):
            yield "p", Paragraph(child, doc)
        elif child.tag == qn("w:tbl"):
            yield "t", Table(child, doc)


def _lines(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def parse_directory(path: Path) -> ParsedDirectory:
    doc = Document(path)
    result = ParsedDirectory(path=path, state_code=None, title="", categories=[])

    current_cat: str | None = None
    cat_before_table: str | None = None   # heading seen since the previous data table
    data_tables = 0

    def handle_text(text: str) -> None:
        nonlocal current_cat, cat_before_table
        lines = _lines(text)
        if not lines:
            return
        if not result.title:
            result.title = lines[0].title()
        m = HEADING_RE.match(lines[0])
        if m:
            current_cat = m.group(2).strip().title() if m.group(2).isupper() else m.group(2).strip()
            cat_before_table = current_cat
            if current_cat not in result.categories:
                result.categories.append(current_cat)
            if len(lines) > 1:  # description line under a heading in a 1x1 layout table
                result.notes.append(f"{current_cat}: {' '.join(lines[1:])}")
        elif len(text) > 40:
            result.notes.append(" ".join(lines))

    # paragraph-record layout (no tables): "Name" / "City, ST   Description: ..." / "Link: https://..."
    pending: dict[str, str] | None = None

    def flush_pending() -> None:
        nonlocal pending
        if pending and current_cat:
            if "Location" in pending:
                result.entries.append(Entry(current_cat, pending))
            else:  # a name with no "Description:" line was ordinary text, not a record
                handle_text(pending["Name"])
        pending = None

    for kind, block in _blocks(doc):
        if kind == "p":
            text = block.text.strip()
            is_heading = bool(HEADING_RE.match(text))
            if current_cat and text and not is_heading:
                desc = DESC_LINE_RE.match(text)
                if desc and pending is not None:
                    pending["Location"], pending["Description"] = desc["loc"].strip(), desc["desc"].strip()
                    continue
                link = LINK_LINE_RE.match(text)
                if link and pending is not None:
                    pending["Website"] = link[1].strip()
                    flush_pending()
                    continue
                if len(text) <= 120:
                    flush_pending()
                    pending = {"Name": text}
                    continue
            if is_heading:
                flush_pending()
            handle_text(block.text)
            continue
        flush_pending()

        rows = [r.cells for r in block.rows]
        if len(block.columns) < 3 and len(rows) <= 2:   # layout table: treat as text
            text = "\n".join(c.text for r in rows for c in r)
            lines = _lines(text)
            # one entry per 1x1 table: "Name / Location: ... / Description: ... / Website: ..."
            if current_cat and len(lines) > 1 and lines[1].lower().startswith("location:"):
                fields = {"Name": lines[0]}
                for ln in lines[1:]:
                    key, _, value = ln.partition(":")
                    fields[key.strip().title() if key.strip().lower() != "link" else "Website"] = value.strip()
                fields["Website"] = _cell_url(rows[0][0], doc.part) or fields.get("Website", "")
                result.entries.append(Entry(current_cat, fields))
                continue
            handle_text(text)
            continue

        # data table: find header (first non-empty row whose first cell starts with "Name")
        header: list[str] | None = None
        start = 0
        # a merged first row ("✝  CHURCHES" repeated across the columns) is the category title
        merged = {c.text.strip() for c in rows[0]} if rows else set()
        if cat_before_table is None and len(merged) == 1 and merged != {""} and not next(iter(merged)).lower().startswith("name"):
            title = re.sub(r"^\W+", "", next(iter(merged)))
            cat_before_table = title.title() if title.isupper() else title
            if cat_before_table not in result.categories:
                result.categories.append(cat_before_table)
        for i, r in enumerate(rows[:2]):
            if r and r[0].text.strip().lower().startswith("name"):
                header, start = [c.text.strip() for c in r], i + 1
                break
        if header is None:
            header = list(DEFAULT_COLUMNS[: len(block.columns)])
            start = 1 if rows and not rows[0] else 0
        # expand a two-line "Name\nLocation" header cell
        def parts(h: str) -> list[str]:
            return [p.strip() for p in re.split(r"\n|/|&", h) if p.strip()] or [h]

        wide = [p for h in header for p in parts(h)]
        split_first = len(header) < len(wide) and len(parts(header[0])) == 2

        category = cat_before_table or (
            result.categories[data_tables] if data_tables < len(result.categories) else f"Section {data_tables + 1}"
        )
        cat_before_table = None
        data_tables += 1

        for r in rows[start:]:
            if not r or not any(c.text.strip() for c in r):
                continue
            cells = [c.text.strip() for c in r]
            if split_first:
                top = _lines(cells[0])
                cells = [top[0] if top else "", " ".join(top[1:]), *cells[1:]]
            fields = dict(zip(wide, cells))
            # prefer the real URL behind a link such as "Visit Website"
            for idx, (key, cell) in enumerate(zip(wide, r if not split_first else [r[0], r[0], *r[1:]])):
                if key.lower().startswith("website"):
                    fields[key] = _cell_url(cell, doc.part) or cells[idx]
            if fields.get("Name"):
                result.entries.append(Entry(category, fields))

    flush_pending()
    result.state_code = _detect_state(result.title, path.stem)
    if not result.entries:
        result.warnings.append("no entries parsed")
    if result.state_code is None:
        result.warnings.append("could not detect state")
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    ap.add_argument("--limit", type=int, default=5)
    args = ap.parse_args()

    for path in sorted(args.dir.glob("*.docx"))[: args.limit]:
        d = parse_directory(path)
        per_cat = {c: sum(e.category == c for e in d.entries) for c in dict.fromkeys(e.category for e in d.entries)}
        print(f"\n== {path.name} -> state={d.state_code} '{d.title}': {len(d.entries)} entries")
        for cat, n in per_cat.items():
            print(f"   {n:3} {cat}")
        for w in d.warnings:
            print(f"   WARNING: {w}")
        if d.entries:
            print(f"   e.g. {d.entries[-1].fields}")


if __name__ == "__main__":
    main()
