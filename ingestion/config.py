import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# An SSLKEYLOGFILE pointing at an unwritable path makes ssl/httpx raise PermissionError on any HTTPS call.
_keylog = os.environ.get("SSLKEYLOGFILE")
if _keylog:
    try:
        open(_keylog, "a").close()
    except OSError:
        os.environ.pop("SSLKEYLOGFILE")

DB_DSN = "postgresql://{u}:{p}@{h}:{port}/{d}".format(
    u=os.getenv("POSTGRES_USER", "rag"),
    p=os.getenv("POSTGRES_PASSWORD", "rag_password"),
    h=os.getenv("POSTGRES_HOST", "localhost"),
    port=os.getenv("POSTGRES_PORT", "5432"),
    d=os.getenv("POSTGRES_DB", "rag_chatbot"),
)

RAW_DIR = ROOT / "data" / "raw"
USER_AGENT = os.getenv("CRAWLER_USER_AGENT", "SeniorCareRAGBot/0.1 (+contact: set CRAWLER_USER_AGENT)")
REQUEST_DELAY = float(os.getenv("CRAWL_DELAY_SECONDS", "1.0"))

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GENERATION_MODEL = os.getenv("GENERATION_MODEL", "gemini-3.8-flash")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "gemini-embedding-001")
EMBEDDING_BACKEND = os.getenv("EMBEDDING_BACKEND", "local")  # "local" (no API key) or "gemini"
LOCAL_EMBEDDING_MODEL = os.getenv("LOCAL_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")  # must match EMBEDDING_DIM
LOCAL_MAX_TOKENS = int(os.getenv("LOCAL_MAX_TOKENS", "256"))  # truncation for CPU speed
EMBEDDING_DIM = 384  # must match vector(384) in db/init/02_schema.sql

CHUNK_TOKENS = int(os.getenv("CHUNK_TOKENS", "500"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "50"))
