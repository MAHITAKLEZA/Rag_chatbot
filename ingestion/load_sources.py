"""Load sources.csv into the sources table.  Usage: python -m ingestion.load_sources [path]"""
import csv
import json
import sys
from pathlib import Path

from .db import connect

DEFAULT_CSV = Path(__file__).parent / "sources.csv"


def main(path: Path) -> None:
    with connect() as conn, open(path, newline="", encoding="utf-8") as f:
        n = 0
        for row in csv.DictReader(f):
            rules = json.loads(row.get("crawl_rules") or "{}")
            conn.execute(
                """
                INSERT INTO sources (state_code, name, base_url, source_type, crawl_rules)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (state_code, base_url) DO UPDATE
                  SET name = EXCLUDED.name,
                      source_type = EXCLUDED.source_type,
                      crawl_rules = EXCLUDED.crawl_rules
                """,
                (row["state"].upper(), row["name"], row["base_url"], row["type"], json.dumps(rules)),
            )
            n += 1
    print(f"Loaded {n} sources from {path}")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CSV)
