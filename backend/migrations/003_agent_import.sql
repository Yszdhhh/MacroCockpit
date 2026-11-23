-- Migration v3: Two-phase agent & text/CSV position import preview table

CREATE TABLE IF NOT EXISTS import_preview (
    id TEXT PRIMARY KEY,
    actor_id TEXT NOT NULL,
    account_id TEXT NOT NULL REFERENCES account(id) ON DELETE RESTRICT,
    format TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('upsert', 'reconcile')),
    body_sha256 TEXT NOT NULL,
    canonical_ops_json TEXT NOT NULL,
    issues_json TEXT NOT NULL DEFAULT '[]',
    diff_json TEXT NOT NULL DEFAULT '{}',
    expected_revision INTEGER NOT NULL,
    expires_at TEXT NOT NULL,
    committed_at TEXT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_import_preview_actor_created
    ON import_preview(actor_id, created_at DESC);
