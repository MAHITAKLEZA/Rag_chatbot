"""Step 5: generate embeddings with the Gemini API (gemini-embedding-001)."""
import math
import os
import time

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from .config import EMBEDDING_BACKEND, EMBEDDING_DIM, EMBEDDING_MODEL, GEMINI_API_KEY, LOCAL_EMBEDDING_MODEL, LOCAL_MAX_TOKENS

_client: genai.Client | None = None


_last_call = 0.0
# Free tier allows 30k embedding tokens/min. A 40-chunk batch is ~17-21k tokens, so one call per ~40s stays under it.
PACE_SECONDS = 40.0


def _embed(texts: list[str], task_type: str, batch_size: int = 40, pace: float = 0.0) -> list[list[float]]:
    global _client, _last_call
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not set (add it to .env)")
    _client = _client or genai.Client(api_key=GEMINI_API_KEY)
    out: list[list[float]] = []
    for i in range(0, len(texts), batch_size):
        for attempt in range(8):
            if pace:
                time.sleep(max(0.0, _last_call + pace - time.monotonic()))
            _last_call = time.monotonic()
            try:
                resp = _client.models.embed_content(
                    model=EMBEDDING_MODEL,
                    contents=texts[i : i + batch_size],
                    config=types.EmbedContentConfig(task_type=task_type, output_dimensionality=EMBEDDING_DIM),
                )
                break
            except genai_errors.ClientError as e:
                # the free tier caps embedding tokens per minute; wait for the window to reset
                if e.code != 429 or "PerMinute" not in str(e) or attempt == 7:
                    raise
                time.sleep(35)
        for e in resp.embeddings:
            # Gemini only returns unit-length vectors at the full 3072 dims; normalize the truncated ones.
            norm = math.sqrt(sum(x * x for x in e.values)) or 1.0
            out.append([x / norm for x in e.values])
    return out


_local = None
BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


def _embed_local(texts: list[str], is_query: bool) -> list[list[float]]:
    """Local CPU embeddings (no API key, no quota). bge-small-en-v1.5 is 384-dim, matching vector(384)."""
    global _local
    if _local is None:
        from fastembed import TextEmbedding
        _local = TextEmbedding(LOCAL_EMBEDDING_MODEL, threads=os.cpu_count())
        _local.model.tokenizer.enable_truncation(max_length=LOCAL_MAX_TOKENS)
    if is_query:
        texts = [BGE_QUERY_PREFIX + t for t in texts]
    return [v.tolist() for v in _local.embed(texts, batch_size=16)]  # fastembed returns unit-length vectors


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed documents/chunks for storage."""
    if EMBEDDING_BACKEND == "local":
        return _embed_local(texts, False)
    return _embed(texts, "RETRIEVAL_DOCUMENT", pace=PACE_SECONDS)


def embed_query(question: str) -> list[float]:
    """Embed a search query (uses the query task type so it matches the stored documents)."""
    if EMBEDDING_BACKEND == "local":
        return _embed_local([question], True)[0]
    return _embed([question], "RETRIEVAL_QUERY")[0]
