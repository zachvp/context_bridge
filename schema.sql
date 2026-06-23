-- chat_memory.db schema. Fully rebuilt on every pipeline run (see PLAN.md) —
-- no migrations needed, this file is just the shape of a fresh DB.

CREATE TABLE chunks (
    id          TEXT PRIMARY KEY,   -- e.g. "<conversation_uuid>:<window_idx>" or "memories"
    text        TEXT NOT NULL,
    source_type TEXT NOT NULL,      -- "conversation" | "memory" | "project_doc" | "code_session"
    title       TEXT NOT NULL,
    timestamp   TEXT NOT NULL,
    embedding   BLOB NOT NULL,      -- float32 vector, raw bytes (np.ndarray.tobytes())
    source      TEXT NOT NULL DEFAULT 'claude_ai',  -- "claude_ai" | "claude_code"
    project     TEXT                -- human-readable project name (claude_code sessions only)
);

CREATE INDEX idx_chunks_source_type ON chunks(source_type);
CREATE INDEX idx_chunks_source ON chunks(source);
CREATE INDEX idx_chunks_project ON chunks(project);

-- Tracks ingested sessions for incremental refresh.
-- Composite PK on (session_uuid, source) prevents collisions across providers.
CREATE TABLE IF NOT EXISTS sessions (
    session_uuid  TEXT NOT NULL,
    source        TEXT NOT NULL DEFAULT 'claude_code',
    file_mtime    REAL NOT NULL,
    ingested_at   TEXT NOT NULL,
    PRIMARY KEY (session_uuid, source)
);

-- Single-row table recording what produced this DB, so query.py can load the
-- matching embedding model instead of assuming one, and so a mismatched
-- rebuild (different model/dim) is caught loudly rather than silently.
CREATE TABLE meta (
    model_name TEXT NOT NULL,
    dim        INTEGER NOT NULL,
    built_at   TEXT NOT NULL
);
