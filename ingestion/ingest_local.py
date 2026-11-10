"""Chunk the parsed referral-directory DOCX files and store them in Postgres.

    python -m ingestion.ingest_local --limit 5            # parse + chunk + embed (local model) + store
    python -m ingestion.ingest_local --limit 5 --force    # re-process files even if unchanged
    python -m ingestion.ingest_local --embed-only         # fill in any missing embeddings
"""
import argparse
import hashlib
from pathlib import Path

from .chunk import chunk_segments
from .db import connect
from .embed import embed_texts
from .local_docx import DEFAULT_DIR, STATES, parse_directory


def ingest_file(conn, path: Path, force: bool = False) -> str:
    d = parse_directory(path)
    if not d.entries or not d.state_code:
        raise ValueError("; ".join(d.warnings) or "nothing parsed")
    state = d.state_code
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    uri = path.resolve().as_uri()

    source_id = conn.execute(
        "INSERT INTO sources (state_code, name, base_url, source_type) VALUES (%s,%s,%s,'docx') "
        "ON CONFLICT (state_code, base_url) DO UPDATE SET name = EXCLUDED.name RETURNING id",
        (state, f"{d.title} (local file)", uri),
    ).fetchone()[0]
    doc_id = conn.execute(
        "INSERT INTO documents (source_id, state_code, url, title, doc_type, topic) "
        "VALUES (%s,%s,%s,%s,'docx','referral network') "
        "ON CONFLICT (source_id, url) DO UPDATE SET title = EXCLUDED.title RETURNING id",
        (source_id, state, uri, d.title),
    ).fetchone()[0]

    prev = conn.execute(
        "SELECT v.content_hash FROM documents x JOIN document_versions v ON v.id = x.current_version_id "
        "WHERE x.id=%s", (doc_id,)
    ).fetchone()
    if prev and prev[0] == digest and not force:
        return "unchanged"

    chunks = chunk_segments(d.segments())
    # give every chunk its state and category so it is understandable (and embeddable) on its own
    texts = [f"[{STATES[state]} | {c.section or d.title}]\n{c.content}" for c in chunks]
    vectors = embed_texts(texts)

    version = conn.execute(
        "SELECT COALESCE(MAX(version), 0) + 1 FROM document_versions WHERE document_id=%s", (doc_id,)
    ).fetchone()[0]
    version_id = conn.execute(
        "INSERT INTO document_versions (document_id, version, content_hash, raw_file_key, extracted_text) "
        "VALUES (%s,%s,%s,%s,%s) RETURNING id",
        (doc_id, version, digest, str(path), "\n\n".join(t for _, t in d.segments())),
    ).fetchone()[0]
    conn.execute("UPDATE chunks SET is_current = FALSE WHERE document_id=%s", (doc_id,))
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO chunks (version_id, document_id, state_code, chunk_index, content, section, "
            "topic, token_count, embedding) VALUES (%s,%s,%s,%s,%s,%s,'referral network',%s,%s)",
            [(version_id, doc_id, state, c.index, t, c.section, c.token_count, v)
             for c, t, v in zip(chunks, texts, vectors)],
        )
    conn.execute(
        "UPDATE documents SET current_version_id=%s, status='indexed', last_fetched_at=now() WHERE id=%s",
        (version_id, doc_id),
    )
    return f"{state}: {len(d.entries)} entries -> {len(chunks)} chunks"


def embed_missing(conn) -> int:
    rows = conn.execute("SELECT id, content FROM chunks WHERE embedding IS NULL AND is_current ORDER BY id").fetchall()
    for i in range(0, len(rows), 40):
        batch = rows[i : i + 40]
        for (cid, _), vec in zip(batch, embed_texts([t for _, t in batch])):
            conn.execute("UPDATE chunks SET embedding=%s WHERE id=%s", (vec, cid))
        conn.commit()  # progress is saved after every batch, so a stop loses at most one
        if i // 40 % 25 == 0:
            print(f"  embedded {i + len(batch)}/{len(rows)}", flush=True)
    conn.execute(
        "UPDATE documents SET status='indexed' WHERE status='fetched' AND NOT EXISTS "
        "(SELECT 1 FROM chunks c WHERE c.document_id = documents.id AND c.is_current AND c.embedding IS NULL)"
    )
    return len(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--embed-only", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    with connect() as conn:
        if not args.embed_only:
            for path in sorted(args.dir.glob("*.docx"))[: args.limit]:
                try:
                    print(f"[ok] {path.name}: {ingest_file(conn, path, args.force)}")
                except Exception as e:
                    conn.rollback()
                    print(f"[fail] {path.name}: {e}")
                conn.commit()
        if args.embed_only:
            print(f"[embed] filled {embed_missing(conn)} missing embeddings")
            conn.commit()


if __name__ == "__main__":
    main()
