"""Step 4: split text into ~CHUNK_TOKENS chunks with overlap, tracking section headings."""
from dataclasses import dataclass

import tiktoken

from .config import CHUNK_OVERLAP, CHUNK_TOKENS

_enc = tiktoken.get_encoding("cl100k_base")


@dataclass
class Chunk:
    index: int
    content: str
    section: str | None
    page_number: int | None
    token_count: int


def _ntok(text: str) -> int:
    return len(_enc.encode(text))


def _split_long(text: str) -> list[str]:
    toks = _enc.encode(text)
    step = CHUNK_TOKENS - CHUNK_OVERLAP
    return [_enc.decode(toks[i : i + CHUNK_TOKENS]) for i in range(0, len(toks), step)]


def chunk_segments(segments: list[tuple[int | None, str]]) -> list[Chunk]:
    chunks: list[Chunk] = []
    section: str | None = None

    def emit(text: str, sec: str | None, page: int | None) -> None:
        chunks.append(Chunk(len(chunks), text, sec, page, _ntok(text)))

    for page, text in segments:
        buf: list[str] = []
        buf_tokens = 0
        buf_section = section

        def flush(carry: bool = True) -> None:
            """Emit the buffer; keep its last paragraph as overlap if it is small (never across a heading)."""
            nonlocal buf, buf_tokens, buf_section
            if buf:
                emit("\n\n".join(buf), buf_section, page)
            tail = buf[-1] if carry and buf and _ntok(buf[-1]) <= CHUNK_OVERLAP else None
            buf, buf_tokens = ([tail], _ntok(tail)) if tail else ([], 0)
            buf_section = section

        for para in (p.strip() for p in text.split("\n\n")):
            if not para:
                continue
            if para.startswith("#"):
                section = para.lstrip("# ").strip()
                flush(carry=False)  # a new section always starts a new chunk, with no carried-over entry
                buf_section = section
            n = _ntok(para)
            if n > CHUNK_TOKENS:
                flush()
                for piece in _split_long(para):
                    emit(piece, section, page)
                continue
            if buf_tokens + n > CHUNK_TOKENS:
                flush()
            buf.append(para)
            buf_tokens += n
        if buf:
            emit("\n\n".join(buf), buf_section, page)
    return chunks
