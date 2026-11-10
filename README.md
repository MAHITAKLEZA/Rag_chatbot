# Senior Care RAG Chatbot

A retrieval-augmented chatbot that answers senior-care and caregiving questions using text scraped from
public organisation websites. Every answer links back to the pages it came from.

```
scrape  ->  clean & chunk  ->  embed  ->  PostgreSQL + pgvector  ->  hybrid search  ->  Gemini answer  ->  web chat
```

## How it works

| Step | What happens | Where |
|---|---|---|
| 1. Scrape | Crawl sites and save page text (respects robots.txt) | `scrape.py`, `scrape_js.py` |
| 2. Clean & chunk | Split pages into ~500-token chunks, keeping source URL and title | `ingestion/chunk.py`, `ingestion/ingest_scraped.py` |
| 3. Embed | Turn each chunk into a vector, locally, with no API key | `ingestion/embed.py` |
| 4. Store | Sources, documents, versions and chunks in Postgres with pgvector and a full-text index | `db/init/`, `docker-compose.yml` |
| 5. Retrieve | Vector search plus keyword search, merged with reciprocal rank fusion, then LLM rerank | `api/rag.py` |
| 6. Answer | Gemini writes a short answer from the retrieved text only | `api/rag.py` |
| 7. Chat UI | Single-page website served by the API | `site/index.html`, `api/main.py` |

Embeddings use `BAAI/bge-small-en-v1.5` (384 dimensions) through `fastembed` on CPU. Only answer generation
and reranking call the Gemini API.

## Requirements

- Python 3.11+
- Docker Desktop (for Postgres with pgvector)
- A Gemini API key from https://aistudio.google.com/apikey (answers only; not needed for scraping or embedding)

## Setup

```powershell
# 1. Python environment
python -m venv .venv
.venv\Scripts\activate
pip install -r ingestion/requirements.txt

# 2. Configuration
copy .env.example .env
# edit .env and set GEMINI_API_KEY (and change POSTGRES_PASSWORD)

# 3. Database
docker compose up -d
```

The schema in `db/init/` is applied automatically the first time the database container starts.

## Build the knowledge base

```powershell
# Scrape pages (output goes to scraped/pages.jsonl)
python scrape.py https://www.parkinson.org/resources-support/helpline --max-pages 50 --depth 2
python scrape.py --file urls.txt --out scraped          # many sites from a list

# JavaScript-rendered sites (needs: pip install playwright && playwright install chromium)
python scrape_js.py https://www.endloneliness.com --max-pages 30 --depth 2

# Chunk and store in Postgres, then embed
python -m ingestion.ingest_scraped --no-embed           # parse + chunk + store
python -m ingestion.ingest_scraped --embed-only         # embed chunks that have no vector yet
```

Both ingestion commands are safe to re-run: unchanged pages are skipped, and embedding resumes where it left off.
Embedding about 16,000 chunks takes roughly 1.5 to 2 hours on a 4-core CPU.

## Run the website

```powershell
python -m uvicorn api.main:app --port 8000
```

Open http://localhost:8000. The API and the chat page are served together, so no separate frontend
build is needed. Docker Desktop must be running so the database is reachable.

Try the retrieval step alone from the command line:

```powershell
python -m ingestion.search US "How do I find respite care for a family caregiver?" --top 4
```

## API

| Method | Path | Body | Purpose |
|---|---|---|---|
| POST | `/api/chat` | `{"state": "US", "question": "..."}` | Answer with source pages |
| GET | `/api/states` | | States that have state-specific data |
| GET | `/api/health` | | Health check |

Use `"state": "US"` for nationwide questions.

## Configuration

Set these in `.env`:

| Variable | Default | Meaning |
|---|---|---|
| `GEMINI_API_KEY` | none | Required for answers and reranking |
| `GENERATION_MODEL` | `gemini-3.8-flash` | First model to try. If it is overloaded, out of quota, or returns a cut-off answer, the code falls back to other Gemini Flash models |
| `EMBEDDING_BACKEND` | `local` | `local` (no key) or `gemini` |
| `LOCAL_EMBEDDING_MODEL` | `BAAI/bge-small-en-v1.5` | Must match the `vector(384)` column |
| `LOCAL_MAX_TOKENS` | `256` | Tokens of each chunk used for its embedding; higher is more accurate and slower |
| `CHUNK_TOKENS` / `CHUNK_OVERLAP` | `500` / `50` | Chunk size and overlap |
| `POSTGRES_*` | see `.env.example` | Database connection |

If you change the embedding model or its dimension, update `vector(384)` in `db/init/02_schema.sql`, alter the
`chunks.embedding` column, and re-embed everything. Vectors from different models cannot be mixed.

## Project layout

```
api/            FastAPI app: chat endpoint, hybrid search, reranking, answer generation
db/init/        SQL that creates the tables, extensions and indexes
ingestion/      Fetch, extract, chunk, embed and load code, plus search and ask command-line tools
site/           The chat website (one HTML file)
web/            Earlier Next.js frontend (optional; the site/ page replaces it)
scrape.py       Standalone crawler
scrape_js.py    Crawler for JavaScript-rendered pages
```

## Known limits

- The free Gemini tier has small daily limits, and each question uses two requests (rerank and answer).
- Chunks are embedded from their first 256 tokens only, so relevance for long chunks can be uneven.
- Answers come only from scraped pages. If a page was not scraped, the bot cannot answer from it.
- This is general information, not medical or legal advice.
