"""Run the ingestion pipeline for one state.

    python -m ingestion.pipeline --state CA            # discover + ingest documents
    python -m ingestion.pipeline --state CA --limit 5  # try a few documents first
"""
import argparse
import traceback

from .chunk import chunk_segments
from .db import connect
from .discover import discover
from .embed import embed_texts
from .extract import extract
from .fetch import fetch


def discover_state(conn, state: str) -> None:
    sources = conn.execute(
        "SELECT id, base_url, source_type, crawl_rules FROM sources WHERE state_code=%s AND is_active",
        (state,),
    ).fetchall()
    for sid, base, stype, rules in sources:
        urls = discover(base, stype, rules)
        for url in urls:
            conn.execute(
                "INSERT INTO documents (source_id, state_code, url) VALUES (%s,%s,%s) "
                "ON CONFLICT (source_id, url) DO NOTHING",
                (sid, state, url),
            )
        conn.execute("UPDATE sources SET last_crawled_at = now() WHERE id=%s", (sid,))
        conn.commit()
        print(f"[discover] {base}: {len(urls)} URLs")


def ingest_document(conn, doc_id: int, state: str, url: str) -> str:
    fetched = fetch(url, state)

    prev = conn.execute(
        "SELECT v.content_hash FROM documents d "
        "JOIN document_versions v ON v.id = d.current_version_id WHERE d.id=%s",
        (doc_id,),
    ).fetchone()
    if prev and prev[0] == fetched.content_hash:
        conn.execute("UPDATE documents SET last_fetched_at=now(), status='indexed' WHERE id=%s", (doc_id,))
        return "unchanged"

    title, segments = extract(fetched.content, fetched.doc_type)
    chunks = chunk_segments(segments)
    if not chunks:
        raise ValueError("no extractable text (scanned PDF needing OCR, or empty page)")
    vectors = embed_texts([c.content for c in chunks])

    version = conn.execute(
        "SELECT COALESCE(MAX(version), 0) + 1 FROM document_versions WHERE document_id=%s", (doc_id,)
    ).fetchone()[0]
    version_id = conn.execute(
        "INSERT INTO document_versions (document_id, version, content_hash, raw_file_key, extracted_text) "
        "VALUES (%s,%s,%s,%s,%s) RETURNING id",
        (doc_id, version, fetched.content_hash, fetched.raw_path, "\n\n".join(t for _, t in segments)),
    ).fetchone()[0]

    conn.execute("UPDATE chunks SET is_current = FALSE WHERE document_id=%s", (doc_id,))
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO chunks (version_id, document_id, state_code, chunk_index, content, section, "
            "page_number, token_count, embedding) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            [
                (version_id, doc_id, state, c.index, c.content, c.section, c.page_number, c.token_count, v)
                for c, v in zip(chunks, vectors)
            ],
        )
    conn.execute(
        "UPDATE documents SET title=%s, doc_type=%s, current_version_id=%s, status='indexed', "
        "last_fetched_at=now() WHERE id=%s",
        (title, fetched.doc_type, version_id, doc_id),
    )
    return f"{len(chunks)} chunks (v{version})"


def ingest_state(conn, state: str, limit: int | None) -> None:
    sql = "SELECT id, url FROM documents WHERE state_code=%s AND status <> 'removed' ORDER BY id"
    docs = conn.execute(sql + (f" LIMIT {int(limit)}" if limit else ""), (state,)).fetchall()
    for doc_id, url in docs:
        job_id = conn.execute(
            "INSERT INTO ingestion_jobs (document_id, job_type, status, attempts, started_at) "
            "VALUES (%s,'index','running',1,now()) RETURNING id",
            (doc_id,),
        ).fetchone()[0]
        conn.commit()
        try:
            result = ingest_document(conn, doc_id, state, url)
            conn.execute("UPDATE ingestion_jobs SET status='succeeded', finished_at=now() WHERE id=%s", (job_id,))
            print(f"[ok] {url} -> {result}")
        except Exception as e:
            conn.rollback()
            conn.execute("UPDATE documents SET status='failed' WHERE id=%s", (doc_id,))
            conn.execute(
                "UPDATE ingestion_jobs SET status='failed', error=%s, finished_at=now() WHERE id=%s",
                (f"{e}\n{traceback.format_exc()[-1500:]}", job_id),
            )
            print(f"[fail] {url}: {e}")
        conn.commit()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--skip-discover", action="store_true")
    args = ap.parse_args()
    state = args.state.upper()
    with connect() as conn:
        if not args.skip_discover:
            discover_state(conn, state)
        ingest_state(conn, state, args.limit)


if __name__ == "__main__":
    main()
