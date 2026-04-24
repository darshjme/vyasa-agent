-- Graphify v2 — initial schema.
-- Single SQLite database; WAL enabled at connection open (see store.py).
-- Authored by Prometheus under design-07-graphify-v2.md (Dr. Bose) and
-- design-03-memory.md (Aryabhata). Schema is additive over the v1 JSON
-- and upgrades the opaque `linked_nodes` array into typed edge rows.

-- =============================================================
-- nodes
-- =============================================================
CREATE TABLE IF NOT EXISTS nodes (
    id                  TEXT    PRIMARY KEY,
    type                TEXT    NOT NULL,
    source_path         TEXT,
    line_range          TEXT,
    summary             TEXT    NOT NULL,
    key_claims_json     TEXT    NOT NULL DEFAULT '[]',
    entities_json       TEXT    NOT NULL DEFAULT '[]',
    symbols_json        TEXT    NOT NULL DEFAULT '[]',
    owner_employee_id   TEXT    NOT NULL,
    visibility          TEXT    NOT NULL
        CHECK (visibility IN ('private', 'team', 'fleet')),
    subject_tags_json   TEXT    NOT NULL DEFAULT '[]',
    supersedes_json     TEXT    NOT NULL DEFAULT '[]',
    episode_id          TEXT,
    pii_scrubbed        INTEGER NOT NULL DEFAULT 0
        CHECK (pii_scrubbed IN (0, 1)),
    embedding_vector_id TEXT,
    checksum            TEXT    NOT NULL UNIQUE,
    status              TEXT    NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'archived', 'flagged_stale')),
    confidence_score    REAL    NOT NULL DEFAULT 0.8
        CHECK (confidence_score >= 0.0 AND confidence_score <= 1.0),
    archived_at         TEXT,
    ttl_days            INTEGER,
    created_at          TEXT    NOT NULL,
    updated_at          TEXT    NOT NULL,
    updated_by          TEXT
);

-- =============================================================
-- edges
-- =============================================================
CREATE TABLE IF NOT EXISTS edges (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    from_node   TEXT    NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    to_node     TEXT    NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    kind        TEXT    NOT NULL
        CHECK (kind IN (
            'depends_on',
            'supersedes',
            'handed_off_to',
            'contradicts',
            'evidence_for',
            'derived_from',
            'answers',
            'mentioned_in'
        )),
    note        TEXT,
    created_at  TEXT    NOT NULL
);

-- =============================================================
-- episodes
-- =============================================================
CREATE TABLE IF NOT EXISTS episodes (
    id                TEXT PRIMARY KEY,
    platform          TEXT NOT NULL,
    platform_chat_id  TEXT,
    platform_user_id  TEXT,
    started_at        TEXT NOT NULL,
    ended_at          TEXT
);

-- =============================================================
-- indexes
-- =============================================================
CREATE INDEX IF NOT EXISTS idx_nodes_type
    ON nodes (type);

CREATE INDEX IF NOT EXISTS idx_nodes_owner
    ON nodes (owner_employee_id);

CREATE INDEX IF NOT EXISTS idx_nodes_visibility
    ON nodes (visibility);

CREATE INDEX IF NOT EXISTS idx_nodes_episode
    ON nodes (episode_id);

-- checksum already UNIQUE at column level; re-declare explicit index so
-- EXPLAIN QUERY PLAN shows it by name during dedup lookups.
CREATE UNIQUE INDEX IF NOT EXISTS idx_nodes_checksum
    ON nodes (checksum);

CREATE INDEX IF NOT EXISTS idx_nodes_status
    ON nodes (status);

CREATE INDEX IF NOT EXISTS idx_edges_from
    ON edges (from_node, kind);

CREATE INDEX IF NOT EXISTS idx_edges_to
    ON edges (to_node, kind);
