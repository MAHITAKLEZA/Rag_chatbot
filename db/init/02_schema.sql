-- Embedding dimension 384 = BAAI/bge-small-en-v1.5 (local). Gemini also works with output_dimensionality=384.
-- If you switch models, change vector(384) here and re-embed everything.

-- Reference: the 50 states (+ DC)
CREATE TABLE states (
    code CHAR(2) PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

-- A crawlable source (website, sitemap, PDF collection) belonging to a state
CREATE TABLE sources (
    id               BIGSERIAL PRIMARY KEY,
    state_code       CHAR(2) NOT NULL REFERENCES states(code),
    name             TEXT NOT NULL,
    base_url         TEXT NOT NULL,
    source_type      TEXT NOT NULL CHECK (source_type IN ('website', 'sitemap', 'pdf', 'docx')),
    crawl_rules      JSONB NOT NULL DEFAULT '{}'::jsonb,  -- allowed paths, depth, excludes
    crawl_interval   INTERVAL NOT NULL DEFAULT '7 days',
    is_active        BOOLEAN NOT NULL DEFAULT TRUE,
    last_crawled_at  TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (state_code, base_url)
);
CREATE INDEX idx_sources_state ON sources (state_code);

-- One row per URL / file. Versions hold the actual content history.
CREATE TABLE documents (
    id                  BIGSERIAL PRIMARY KEY,
    source_id           BIGINT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    state_code          CHAR(2) NOT NULL REFERENCES states(code),
    url                 TEXT NOT NULL,
    title               TEXT,
    doc_type            TEXT NOT NULL DEFAULT 'html' CHECK (doc_type IN ('html', 'pdf', 'docx', 'other')),
    topic               TEXT,
    status              TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'fetched', 'indexed', 'failed', 'removed')),
    current_version_id  BIGINT,  -- FK added below (circular reference)
    last_fetched_at     TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_id, url)
);
CREATE INDEX idx_documents_state  ON documents (state_code);
CREATE INDEX idx_documents_status ON documents (status);

CREATE TABLE document_versions (
    id            BIGSERIAL PRIMARY KEY,
    document_id   BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    version       INTEGER NOT NULL,
    content_hash  TEXT NOT NULL,   -- detect changes on re-crawl
    raw_file_key  TEXT,            -- path/key in S3/MinIO/local storage
    extracted_text TEXT,
    fetched_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (document_id, version)
);
CREATE INDEX idx_versions_hash ON document_versions (document_id, content_hash);

ALTER TABLE documents
    ADD CONSTRAINT fk_documents_current_version
    FOREIGN KEY (current_version_id) REFERENCES document_versions(id) ON DELETE SET NULL;

-- Retrievable chunks with embeddings and a full-text index
CREATE TABLE chunks (
    id            BIGSERIAL PRIMARY KEY,
    version_id    BIGINT NOT NULL REFERENCES document_versions(id) ON DELETE CASCADE,
    document_id   BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    state_code    CHAR(2) NOT NULL REFERENCES states(code),  -- denormalized for fast filtering
    chunk_index   INTEGER NOT NULL,
    content       TEXT NOT NULL,
    section       TEXT,
    page_number   INTEGER,
    topic         TEXT,
    metadata      JSONB NOT NULL DEFAULT '{}'::jsonb,
    token_count   INTEGER,
    embedding     vector(384),
    content_tsv   tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
    is_current    BOOLEAN NOT NULL DEFAULT TRUE,  -- false once a newer version supersedes it
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (version_id, chunk_index)
);
CREATE INDEX idx_chunks_state_current ON chunks (state_code) WHERE is_current;
CREATE INDEX idx_chunks_document      ON chunks (document_id);
CREATE INDEX idx_chunks_embedding     ON chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX idx_chunks_tsv           ON chunks USING gin (content_tsv);

-- Ingestion job tracking (one row per pipeline run / per URL job)
CREATE TABLE ingestion_jobs (
    id            BIGSERIAL PRIMARY KEY,
    source_id     BIGINT REFERENCES sources(id) ON DELETE CASCADE,
    document_id   BIGINT REFERENCES documents(id) ON DELETE CASCADE,
    job_type      TEXT NOT NULL CHECK (job_type IN ('discover', 'fetch', 'extract', 'chunk', 'embed', 'index', 'crawl')),
    status        TEXT NOT NULL DEFAULT 'queued'
                  CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
    attempts      INTEGER NOT NULL DEFAULT 0,
    error         TEXT,
    started_at    TIMESTAMPTZ,
    finished_at   TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_jobs_status ON ingestion_jobs (status, created_at);
CREATE INDEX idx_jobs_source ON ingestion_jobs (source_id);

-- Chat logging (optional, useful for evaluating answer quality)
CREATE TABLE chat_logs (
    id           BIGSERIAL PRIMARY KEY,
    state_code   CHAR(2) REFERENCES states(code),
    question     TEXT NOT NULL,
    answer       TEXT,
    chunk_ids    BIGINT[],
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Keep updated_at fresh
CREATE FUNCTION set_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_sources_updated   BEFORE UPDATE ON sources   FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_documents_updated BEFORE UPDATE ON documents FOR EACH ROW EXECUTE FUNCTION set_updated_at();
