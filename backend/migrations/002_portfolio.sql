-- Migration v2: Portfolio, Quotes, Curation Rules, Change Sets, Credentials & Audit Events
-- All monetary quantities, prices, margins, and weights are stored as canonical decimal TEXT.
-- Referenced account and instrument rows use ON DELETE RESTRICT.

CREATE TABLE IF NOT EXISTS account (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'general',
    venue TEXT NOT NULL DEFAULT '',
    base_currency TEXT NOT NULL DEFAULT 'USD',
    equity_mode TEXT NOT NULL DEFAULT 'total_ex_upl' CHECK (equity_mode IN ('total_ex_upl')),
    confirmed_debt_usd TEXT NOT NULL DEFAULT '0',
    notes TEXT NOT NULL DEFAULT '',
    archived INTEGER NOT NULL DEFAULT 0,
    rev INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS instrument (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    asset_class TEXT NOT NULL CHECK (
        asset_class IN ('cash', 'equity', 'crypto_spot', 'perp', 'futures', 'etf', 'commodity', 'other')
    ),
    quote_currency TEXT NOT NULL DEFAULT 'USD',
    base_asset TEXT NOT NULL DEFAULT '',
    settlement_currency TEXT NOT NULL DEFAULT 'USD',
    contract_type TEXT NOT NULL DEFAULT 'spot' CHECK (contract_type IN ('cash', 'spot', 'linear', 'inverse')),
    multiplier TEXT NOT NULL DEFAULT '1',
    lot_unit TEXT NOT NULL DEFAULT 'unit',
    exchange TEXT NOT NULL DEFAULT '',
    sector TEXT NOT NULL DEFAULT '',
    sleeve TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    rev INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS spot_balance (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES account(id) ON DELETE RESTRICT,
    instrument_id TEXT NOT NULL REFERENCES instrument(id) ON DELETE RESTRICT,
    qty TEXT NOT NULL,
    avg_cost TEXT NOT NULL DEFAULT '0',
    cost_currency TEXT NOT NULL DEFAULT 'USD',
    is_cash INTEGER NOT NULL DEFAULT 0,
    locked_margin_usd TEXT NOT NULL DEFAULT '0',
    sleeve TEXT NOT NULL DEFAULT '',
    stop_loss TEXT NULL,
    take_profit TEXT NULL,
    thesis TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'manual',
    as_of TEXT NOT NULL DEFAULT '',
    rev INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(account_id, instrument_id)
);

CREATE TABLE IF NOT EXISTS derivative_position (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES account(id) ON DELETE RESTRICT,
    instrument_id TEXT NOT NULL REFERENCES instrument(id) ON DELETE RESTRICT,
    side TEXT NOT NULL CHECK (side IN ('long', 'short')),
    margin_mode TEXT NOT NULL DEFAULT 'cross' CHECK (margin_mode IN ('isolated', 'cross')),
    qty TEXT NOT NULL,
    qty_unit TEXT NOT NULL DEFAULT 'coin' CHECK (qty_unit IN ('coin', 'contract')),
    contract_multiplier TEXT NOT NULL DEFAULT '1',
    contract_type TEXT NOT NULL DEFAULT 'linear' CHECK (contract_type IN ('linear', 'inverse')),
    entry_price TEXT NOT NULL,
    margin_usd TEXT NOT NULL DEFAULT '0',
    margin_amount TEXT NOT NULL DEFAULT '0',
    margin_currency TEXT NOT NULL DEFAULT 'USDT',
    leverage TEXT NULL,
    mmr TEXT NULL,
    venue_liq TEXT NULL,
    liq_asof TEXT NULL,
    mark_price TEXT NULL,
    funding_rate TEXT NULL,
    stop_loss TEXT NULL,
    take_profit TEXT NULL,
    sleeve TEXT NOT NULL DEFAULT '',
    thesis TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'manual',
    as_of TEXT NOT NULL DEFAULT '',
    rev INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS curation_rule (
    id TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 1,
    kind TEXT NOT NULL CHECK (kind IN ('instrument', 'sector', 'keyword', 'regex', 'topic', 'source')),
    target TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('watch', 'block', 'mute', 'adjust')),
    points TEXT NOT NULL DEFAULT '0',
    note TEXT NOT NULL DEFAULT '',
    rev INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_setting (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    rev INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS quote (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    instrument_id TEXT NOT NULL REFERENCES instrument(id) ON DELETE RESTRICT,
    kind TEXT NOT NULL,
    price TEXT NOT NULL,
    quote_currency TEXT NOT NULL DEFAULT 'USD',
    funding_rate TEXT NULL,
    change_24h_pct TEXT NULL,
    market_at TEXT NOT NULL,
    received_at TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    session TEXT NOT NULL DEFAULT '',
    raw_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS fx_quote (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pair TEXT NOT NULL,
    rate TEXT NOT NULL,
    basis TEXT NOT NULL DEFAULT 'offshore',
    label TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    market_at TEXT NOT NULL,
    received_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ok'
);

CREATE TABLE IF NOT EXISTS change_set (
    id TEXT PRIMARY KEY,
    actor_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    role TEXT NOT NULL,
    request_sha256 TEXT NOT NULL,
    expected_revision INTEGER NOT NULL,
    base_revision INTEGER NOT NULL,
    new_revision INTEGER NOT NULL,
    ops_count INTEGER NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'applied',
    response_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(actor_id, request_id)
);

CREATE TABLE IF NOT EXISTS change_item (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    change_set_id TEXT NOT NULL REFERENCES change_set(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    op TEXT NOT NULL CHECK (op IN ('put', 'remove')),
    entity TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    before_json TEXT NULL,
    after_json TEXT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS credential (
    id TEXT PRIMARY KEY,
    actor_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('READ_ONLY', 'PORTFOLIO_RW', 'SYNC_PUBLISH')),
    salt TEXT NOT NULL,
    token_hash TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    expires_at TEXT NULL,
    last_used_at TEXT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL,
    resource TEXT NOT NULL DEFAULT '',
    outcome TEXT NOT NULL DEFAULT 'ok',
    request_id TEXT NOT NULL DEFAULT '',
    detail_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_quote_inst_kind_market
    ON quote(instrument_id, kind, market_at DESC);

CREATE INDEX IF NOT EXISTS idx_curation_rule_enabled_kind
    ON curation_rule(enabled, kind);

CREATE INDEX IF NOT EXISTS idx_audit_event_at_actor
    ON audit_event(at, actor_id);

CREATE INDEX IF NOT EXISTS idx_fx_quote_pair_market
    ON fx_quote(pair, market_at DESC);

CREATE INDEX IF NOT EXISTS idx_change_item_set
    ON change_item(change_set_id, seq);

CREATE INDEX IF NOT EXISTS idx_credential_role_enabled
    ON credential(role, enabled);

CREATE INDEX IF NOT EXISTS idx_spot_balance_account
    ON spot_balance(account_id);

CREATE INDEX IF NOT EXISTS idx_derivative_position_account
    ON derivative_position(account_id);
