"""Step 2: fetch content and keep the raw file."""
import hashlib
import time
from dataclasses import dataclass

import requests

from .config import RAW_DIR, REQUEST_DELAY, USER_AGENT


@dataclass
class Fetched:
    content: bytes
    doc_type: str  # html | pdf | docx | other
    content_hash: str
    raw_path: str


def _doc_type(url: str, content_type: str) -> str:
    u, ct = url.lower().split("?")[0], content_type.lower()
    if "pdf" in ct or u.endswith(".pdf"):
        return "pdf"
    if "wordprocessingml" in ct or u.endswith(".docx"):
        return "docx"
    if "html" in ct:
        return "html"
    return "other"


def fetch(url: str, state: str) -> Fetched:
    time.sleep(REQUEST_DELAY)
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=60)
    r.raise_for_status()
    doc_type = _doc_type(url, r.headers.get("content-type", ""))
    digest = hashlib.sha256(r.content).hexdigest()
    path = RAW_DIR / state / f"{digest}.{doc_type if doc_type != 'other' else 'bin'}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(r.content)
    return Fetched(r.content, doc_type, digest, str(path))
