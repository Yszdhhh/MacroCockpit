-- Migration v1: Baseline journal schema (meta, trade_reviews, essays, article_status)
-- Empty DBs get complete v1 columns directly; legacy v0 DBs are safely backfilled
-- with rev INTEGER NOT NULL DEFAULT 0 and trade_reviews.position_id TEXT NULL
-- by the migration runner via PRAGMA table_info checks.

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS trade_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    size_pct REAL NOT NULL DEFAULT 0.0,
    price_usd REAL NOT NULL DEFAULT 0.0,
    pnl_usd REAL NOT NULL DEFAULT 0.0,
    status TEXT NOT NULL,
    thesis TEXT NOT NULL DEFAULT '',
    execution_note TEXT NOT NULL DEFAULT '',
    lesson TEXT NOT NULL DEFAULT '',
    tags_json TEXT NOT NULL DEFAULT '[]',
    position_id TEXT NULL,
    rev INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS essays (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    mood TEXT NOT NULL DEFAULT '冷静',
    tags_json TEXT NOT NULL DEFAULT '[]',
    body_md TEXT NOT NULL,
    pinned INTEGER NOT NULL DEFAULT 0,
    rev INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS article_status (
    filename TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'unread',
    note TEXT NOT NULL DEFAULT '',
    rev INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

INSERT OR IGNORE INTO meta (key, value) VALUES ('portfolio_revision', '0');
