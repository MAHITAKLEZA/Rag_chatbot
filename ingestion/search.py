"""Search the stored chunks for a state.

    python -m ingestion.search CA "Which elder law attorneys can help with Medicaid planning?"
    python -m ingestion.search AK "daytime activities for seniors" --top 5 --full
"""
import argparse

from .db import connect
from .embed import embed_query


def search(state: str, question: str, top: int = 3) -> list[tuple[int, str, float, str]]:
    v = embed_query(question)
    with connect() as conn:
        return conn.execute(
            "SELECT id, section, 1 - (embedding <=> %s::vector) AS similarity, content "
            "FROM chunks WHERE is_current AND state_code=%s AND embedding IS NOT NULL "
            "ORDER BY embedding <=> %s::vector LIMIT %s",
            (v, state.upper(), v, top),
        ).fetchall()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("state", help="two-letter state code, e.g. CA")
    ap.add_argument("question")
    ap.add_argument("--top", type=int, default=3)
    ap.add_argument("--full", action="store_true", help="print the whole chunk instead of a preview")
    args = ap.parse_args()

    rows = search(args.state, args.question, args.top)
    if not rows:
        print(f"No chunks stored for state {args.state.upper()}.")
    for cid, section, sim, content in rows:
        text = content if args.full else content.replace("\n", " ")[:220] + "..."
        print(f"\n[{sim:.3f}] chunk {cid} | {section}\n{text}")


if __name__ == "__main__":
    main()
