"""Retrieval + RAG pipeline: analyse query -> hybrid search -> rerank -> build context -> cited answer."""
import json
import re
import time
from dataclasses import dataclass

from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel

from ingestion.config import GEMINI_API_KEY, GENERATION_MODEL
from ingestion.db import connect
from ingestion.embed import embed_query
from ingestion.local_docx import STATES

FALLBACK_MODELS = ["gemini-2.5-flash", "gemini-3.7-flash", "gemini-flash-lite-latest"]

_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

SYSTEM_PROMPT = """You answer questions about senior-care referral resources for one US state.

Rules:
- Use ONLY the context provided. Never use outside knowledge or invent names, phone numbers or websites.
- Recommend specific entries by name and location. Do not put website URLs in the answer text; they go in "sources".
- Format the answer as Markdown: one short lead sentence, then a bulleted list with ONE entry per line, like
  "- **Name** (City, ST) – what they do". Put a blank line between the lead sentence and the list.
  Group entries under a short bold heading (e.g. "**Statewide resources**") only if there are more than six.
- Keep it concise: at most 8 entries, most relevant first.
- If the context does not contain the answer, say so plainly and mention what related resources it does contain.
- In "sources", list only entries you actually mention, with their category and website from the context."""


NATIONWIDE_PROMPT = """You are a helpful assistant answering questions about senior care and caregiving using excerpts from
national organisation websites.

Rules:
- Use ONLY the context provided. Never invent names, phone numbers, programs or websites.
- Write a clear, friendly answer in Markdown: a short lead sentence, then a bulleted list of specific, practical points
  or organisations ("- **Name** - what it offers / how to reach it"). Include phone numbers or steps if the context has them.
- Keep it concise (at most 8 bullets), most relevant first.
- If the context does not contain the answer, say so plainly and mention what related information it does contain.
- In "sources", list the organisations or programs you mention (category may be "General"; website only if it appears in the context)."""


@dataclass
class Chunk:
    id: int
    section: str
    content: str
    score: float = 0.0


class Source(BaseModel):
    name: str
    category: str
    website: str | None = None


class Answer(BaseModel):
    answer: str
    sources: list[Source]


# --- 1. query analysis -------------------------------------------------------------------------
def analyse_query(state: str, question: str) -> dict:
    """Detect whether the question names a different state than the one selected."""
    mentioned = [code for code, name in STATES.items() if re.search(rf"\b{re.escape(name)}\b", question, re.I)]
    other = [c for c in mentioned if c != state]
    return {"state": state, "mentions_other_state": other[0] if other else None}


# --- 2. hybrid retrieval (vector + keyword) with state filter, merged by reciprocal rank fusion --------
def hybrid_search(state: str, question: str, k: int = 12, pool: int = 30) -> list[Chunk]:
    v = embed_query(question)
    with connect() as conn:
        vec = conn.execute(
            "SELECT id, section, content FROM chunks WHERE is_current AND state_code IN (%s, 'US') AND embedding IS NOT NULL "
            "ORDER BY embedding <=> %s::vector LIMIT %s", (state, v, pool)).fetchall()
        kw = conn.execute(
            "SELECT id, section, content FROM chunks WHERE is_current AND state_code IN (%s, 'US') "
            "AND content_tsv @@ websearch_to_tsquery('english', %s) "
            "ORDER BY ts_rank(content_tsv, websearch_to_tsquery('english', %s)) DESC LIMIT %s",
            (state, question, question, pool)).fetchall()

    scores: dict[int, float] = {}
    rows: dict[int, tuple] = {}
    for ranked in (vec, kw):
        for rank, row in enumerate(ranked):
            scores[row[0]] = scores.get(row[0], 0.0) + 1.0 / (60 + rank)
            rows[row[0]] = row
    best = sorted(scores, key=scores.get, reverse=True)[:k]
    return [Chunk(i, rows[i][1], rows[i][2], scores[i]) for i in best]


# --- 3. rerank with the LLM ------------------------------------------------------------------------
class Ranking(BaseModel):
    ranked_ids: list[int]


def rerank(question: str, chunks: list[Chunk], keep: int = 6) -> list[Chunk]:
    if len(chunks) <= keep or _client is None:
        return chunks[:keep]
    listing = "\n\n".join(f"[id {c.id}] ({c.section}) {c.content[:600]}" for c in chunks)
    try:
        resp = _client.models.generate_content(
            model=GENERATION_MODEL,
            contents=f"Question: {question}\n\nPassages:\n{listing}\n\n"
                     f"Return the ids of the passages most useful for answering the question, best first.",
            config=types.GenerateContentConfig(
                temperature=0, response_mime_type="application/json", response_schema=Ranking),
        )
        order = [i for i in json.loads(resp.text)["ranked_ids"] if i in {c.id for c in chunks}]
    except Exception:
        return chunks[:keep]  # fall back to the fusion order
    by_id = {c.id: c for c in chunks}
    ranked = [by_id[i] for i in dict.fromkeys(order)]
    ranked += [c for c in chunks if c not in ranked]
    return ranked[:keep]


# --- 4+5. build context and generate the cited answer ------------------------------------------------
def _looks_cut_off(text: str) -> bool:
    """Some models stop right after the lead sentence ("...These may involve:") yet report success."""
    t = text.strip()
    return len(t) < 120 or t.endswith(":") or ("- " not in t and len(t) < 300)


def _call_model(model: str, state: str, question: str, context: str) -> Answer:
    prompt = NATIONWIDE_PROMPT if state == "US" else SYSTEM_PROMPT
    resp = _client.models.generate_content(
        model=model,
        contents=f"State: {STATES.get(state, state)}\n\nContext:\n{context}\n\nQuestion: {question}",
        config=types.GenerateContentConfig(
            system_instruction=prompt, temperature=0.2,
            response_mime_type="application/json", response_schema=Answer),
    )
    return Answer.model_validate_json(resp.text)


def generate(state: str, question: str, chunks: list[Chunk]) -> Answer:
    context = "\n\n---\n\n".join((f"[Category: {c.section}]\n" if c.section else "") + c.content for c in chunks)
    # Try the configured model, then fall back to others. 503 = overloaded, 429 = that model's daily quota is used
    # up, and a cut-off answer means that model is unreliable here: in all three cases move on to the next model.
    models = list(dict.fromkeys([GENERATION_MODEL, *FALLBACK_MODELS]))
    last_err: Exception | None = None
    weak: Answer | None = None  # last resort if every model returns something that looks incomplete
    answer: Answer | None = None
    for model in models:
        for attempt in range(2):
            try:
                cand = _call_model(model, state, question, context)
            except genai_errors.ServerError as e:  # 503: retry this model once, then move on
                last_err = e
                time.sleep(2 ** attempt)
                continue
            except genai_errors.ClientError as e:
                if e.code != 429:
                    raise
                last_err = e  # quota used up for this model
                break
            except ValueError as e:  # invalid/truncated JSON
                last_err = e
                break
            if _looks_cut_off(cand.answer):
                weak = weak or cand
                last_err = RuntimeError(f"{model} returned an incomplete answer")
            else:
                answer = cand
            break
        if answer is not None:
            break
    answer = answer or weak
    if answer is None:
        raise last_err or RuntimeError("no model could answer")
    # drop any website the model did not copy from the retrieved context
    haystack = " ".join(c.content for c in chunks)
    for s in answer.sources:
        if s.website and s.website not in haystack:
            s.website = None
    return answer


def references(chunks: list[Chunk]) -> list[dict]:
    """Source pages (title + url) behind the chunks used, de-duplicated, best chunk first."""
    ids = [c.id for c in chunks]
    with connect() as conn:
        rows = conn.execute(
            "SELECT c.id, d.title, d.url FROM chunks c JOIN documents d ON d.id = c.document_id WHERE c.id = ANY(%s)",
            (ids,)).fetchall()
    by_id = {r[0]: r for r in rows}
    out, seen = [], set()
    for i in ids:
        if i in by_id and by_id[i][2] not in seen and by_id[i][2]:
            seen.add(by_id[i][2])
            out.append({"title": by_id[i][1] or by_id[i][2], "url": by_id[i][2]})
    return out


def ask(state: str, question: str) -> dict:
    if _client is None:
        raise RuntimeError("GEMINI_API_KEY is not set (add it to .env)")
    state = state.upper()
    analysis = analyse_query(state, question)
    candidates = hybrid_search(state, question)
    if not candidates:
        return {"answer": f"No information is stored for {STATES.get(state, state)} yet.", "sources": [],
                "analysis": analysis, "chunks_used": []}
    top = rerank(question, candidates)
    result = generate(state, question, top)
    note = ""
    if analysis["mentions_other_state"]:
        note = (f"\n\n(Note: your question mentions {STATES[analysis['mentions_other_state']]}, "
                f"but results are limited to {STATES.get(state, state)}. Change the state selector to search elsewhere.)")
    return {"answer": result.answer + note, "sources": [s.model_dump() for s in result.sources],
            "analysis": analysis, "references": references(top),
            "chunks_used": [{"id": c.id, "category": c.section} for c in top]}
