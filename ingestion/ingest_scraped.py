"""Load pages scraped by scrape.py (scraped/pages.jsonl) into Postgres as nationwide ('US') website sources.

    python -m ingestion.ingest_scraped --no-embed           # parse + chunk + store (no API calls)
    python -m ingestion.ingest_scraped                      # ... then embed missing chunks with Gemini
    python -m ingestion.ingest_scraped --embed-only         # only fill in missing embeddings
    python -m ingestion.ingest_scraped --limit 20 --no-embed

Safe to re-run: pages whose text is unchanged are skipped; duplicate URLs in the jsonl are collapsed.
"""
import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse

from .chunk import chunk_segments
from .config import ROOT
from .db import connect
from .ingest_local import embed_missing

STATE = "US"
DEFAULT_FILE = ROOT / "scraped" / "pages.jsonl"


def load_pages(path: Path) -> list[dict]:
    pages: dict[str, dict] = {}
    with path.open(encoding="utf-8", newline="\n") as f:  # not splitlines(): page text may hold U+2028 etc.
        for line in f:
            try:
                p = json.loads(line)
            except json.JSONDecodeError:
                continue  # e.g. a half-written last line while a scrape is still running
            pages[p["url"].rstrip("/")] = p  # later duplicates win
    return list(pages.values())


def ingest_page(conn, source_ids: dict[str, int], page: dict) -> str:
    url, title, text = page["url"], page.get("title") or "", page["text"]
    host = urlparse(url).netloc.lower().removeprefix("www.")
    if host not in source_ids:
        source_ids[host] = conn.execute(
            "INSERT INTO sources (state_code, name, base_url, source_type) VALUES (%s,%s,%s,'website') "
            "ON CONFLICT (state_code, base_url) DO UPDATE SET name = EXCLUDED.name RETURNING id",
            (STATE, host, f"https://{host}"),
        ).fetchone()[0]
    source_id = source_ids[host]

    digest = hashlib.sha256(text.encode()).hexdigest()
    doc_id = conn.execute(
        "INSERT INTO documents (source_id, state_code, url, title, doc_type) VALUES (%s,%s,%s,%s,'html') "
        "ON CONFLICT (source_id, url) DO UPDATE SET title = EXCLUDED.title RETURNING id",
        (source_id, STATE, url, title),
    ).fetchone()[0]
    prev = conn.execute(
        "SELECT v.content_hash FROM documents x JOIN document_versions v ON v.id = x.current_version_id "
        "WHERE x.id=%s", (doc_id,)
    ).fetchone()
    if prev and prev[0] == digest:
        return "unchanged"

    chunks = chunk_segments([(None, text)])
    if not chunks:
        return "empty"
    version = conn.execute(
        "SELECT COALESCE(MAX(version), 0) + 1 FROM document_versions WHERE document_id=%s", (doc_id,)
    ).fetchone()[0]
    version_id = conn.execute(
        "INSERT INTO document_versions (document_id, version, content_hash, raw_file_key, extracted_text) "
        "VALUES (%s,%s,%s,%s,%s) RETURNING id",
        (doc_id, version, digest, "scraped/pages.jsonl", text),
    ).fetchone()[0]
    conn.execute("UPDATE chunks SET is_current = FALSE WHERE document_id=%s", (doc_id,))
    label = title or host
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO chunks (version_id, document_id, state_code, chunk_index, content, section, "
            "token_count) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            [(version_id, doc_id, STATE, c.index, f"[{host} | {label}]\n{c.content}", c.section, c.token_count)
             for c in chunks],
        )
    # 'fetched' until embeddings exist; embed_missing() flips it to 'indexed'
    conn.execute(
        "UPDATE documents SET current_version_id=%s, status='fetched', last_fetched_at=now() WHERE id=%s",
        (version_id, doc_id),
    )
    return f"{len(chunks)} chunks"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", type=Path, default=DEFAULT_FILE)
    ap.add_argument("--limit", type=int, default=0, help="only load the first N pages (0 = all)")
    ap.add_argument("--no-embed", action="store_true", help="store chunks without calling the embedding API")
    ap.add_argument("--embed-only", action="store_true")
    args = ap.parse_args()

    with connect() as conn:
        if not args.embed_only:
            pages = load_pages(args.file)
            if args.limit:
                pages = pages[: args.limit]
            print(f"{len(pages)} unique pages")
            source_ids: dict[str, int] = {}
            stats: dict[str, int] = {}
            for i, page in enumerate(pages, 1):
                try:
                    r = ingest_page(conn, source_ids, page)
                    conn.commit()
                except Exception as e:
                    conn.rollback()
                    r = "fail"
                    print(f"[fail] {page['url']}: {e}")
                stats[r.split()[-1] if r[0].isdigit() else r] = stats.get(r.split()[-1] if r[0].isdigit() else r, 0) + 1
                if i % 200 == 0:
                    print(f"  {i}/{len(pages)}")
            print("stored:", stats)
        if not args.no_embed:
            print(f"[embed] filled {embed_missing(conn)} missing embeddings")
            conn.commit()


if __name__ == "__main__":
    main()
