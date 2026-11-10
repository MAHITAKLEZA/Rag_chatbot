"""HTTP API for the chatbot.   Run:  uvicorn api.main:app --reload --port 8000"""
from fastapi import FastAPI, HTTPException
from pathlib import Path

from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ingestion.db import connect
from ingestion.local_docx import STATES

from .rag import ask

app = FastAPI(title="Senior Care RAG API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    state: str = Field(min_length=2, max_length=2)
    question: str = Field(min_length=3, max_length=1000)


@app.get("/api/states")
def states() -> list[dict]:
    """States that currently have indexed data."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT state_code FROM chunks WHERE is_current AND embedding IS NOT NULL AND state_code <> 'US' "
            "GROUP BY state_code ORDER BY state_code").fetchall()
    return [{"code": r[0], "name": STATES.get(r[0], r[0])} for r in rows]


@app.post("/api/chat")
def chat(req: ChatRequest) -> dict:
    if req.state.upper() != "US" and req.state.upper() not in STATES:
        raise HTTPException(400, f"Unknown state code {req.state!r}")
    try:
        return ask(req.state, req.question)
    except Exception as e:  # surface model/db errors to the UI without a stack trace
        raise HTTPException(502, f"Could not answer: {e}") from e


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


# The website (site/index.html) is served by the API itself: open http://localhost:8000
SITE_DIR = Path(__file__).resolve().parent.parent / "site"
if SITE_DIR.is_dir():
    app.mount("/", StaticFiles(directory=SITE_DIR, html=True), name="site")
