"""Ask a question about one state and get a cited answer.

    python -m ingestion.ask CA "Which elder law attorneys can help with Medicaid planning?"
    python -m ingestion.ask AK "Where can seniors go for daytime activities?" --top 6 --show-context
"""
import argparse

from google import genai
from google.genai import types

from .config import GEMINI_API_KEY, GENERATION_MODEL
from .search import search

SYSTEM_PROMPT = """You answer questions about senior-care referral resources for one US state.

Rules:
- Use ONLY the context provided. Never use outside knowledge or invent names, phone numbers or websites.
- Recommend specific entries by name, with their location and website exactly as written in the context.
- Organize longer answers as a short list. Be concise.
- If the context does not contain the answer, say so plainly and mention what related resources it does contain.
- End with a "Sources" line listing the categories you used."""


def answer(state: str, question: str, top: int = 5) -> tuple[str, list[tuple[int, str, float, str]]]:
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not set (add it to .env)")
    chunks = search(state, question, top)
    if not chunks:
        return f"No information is stored for state {state.upper()} yet.", []

    context = "\n\n---\n\n".join(f"[Category: {section}]\n{content}" for _, section, _, content in chunks)
    prompt = f"State: {state.upper()}\n\nContext:\n{context}\n\nQuestion: {question}"
    client = genai.Client(api_key=GEMINI_API_KEY)  # keep a reference: a temporary client is closed on garbage collection
    resp = client.models.generate_content(
        model=GENERATION_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT, temperature=0.2),
    )
    return (resp.text or "").strip(), chunks


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("state", help="two-letter state code, e.g. CA")
    ap.add_argument("question")
    ap.add_argument("--top", type=int, default=5, help="how many chunks to give the model")
    ap.add_argument("--show-context", action="store_true", help="also print which chunks were used")
    args = ap.parse_args()

    text, chunks = answer(args.state, args.question, args.top)
    print(f"\n{text}\n")
    if args.show_context:
        print("Retrieved chunks:")
        for cid, section, sim, _ in chunks:
            print(f"  [{sim:.3f}] chunk {cid} | {section}")


if __name__ == "__main__":
    main()
