"""Contract tests for SQLite 0->3 migrations, WAL hot backup, Decimal valuation, curation, import, and Card 2.0 push."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

COCKPIT_ROOT = Path(__file__).resolve().parent.parent
if str(COCKPIT_ROOT) not in sys.path:
    sys.path.insert(0, str(COCKPIT_ROOT))

from backend import (  # noqa: E402
    backup_journal,
    curation,
    import_positions,
    mobile_push,
    valuation,
)
from backend.journal_store import JournalStore, RevisionConflictError  # noqa: E402
from backend.portfolio_store import (  # noqa: E402
    ConstraintViolationError,
    IdempotencyConflictError,
    PortfolioStore,
)


def _create_legacy_v0_db(db_path: Path) -> None:
    """Create a v0 SQLite database resembling pre-V2 production schema with existing rows."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    try:
        con.executescript(
            """
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE trade_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT NOT NULL,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                size_pct REAL DEFAULT 0,
                price_usd REAL DEFAULT 0,
                pnl_usd REAL DEFAULT 0,
                status TEXT NOT NULL,
                thesis TEXT DEFAULT '',
                execution_note TEXT DEFAULT '',
                lesson TEXT DEFAULT '',
                tags_json TEXT DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE essays (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                mood TEXT DEFAULT '冷静',
                tags_json TEXT DEFAULT '[]',
                body_md TEXT NOT NULL,
                pinned INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE article_status (
                filename TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'unread',
                note TEXT DEFAULT '',
                updated_at TEXT NOT NULL
            );
            INSERT INTO meta(key, value) VALUES('seeded_v1', '1');
            INSERT INTO trade_reviews(trade_date,symbol,direction,size_pct,price_usd,pnl_usd,status,thesis,created_at,updated_at)
                VALUES('2026-11-20','BTC','做空',10.0,60000.0,250.0,'持有中','v0历史记录','2026-11-20T10:00:00+08:00','2026-11-20T10:00:00+08:00');
            INSERT INTO essays(title,mood,tags_json,body_md,pinned,created_at,updated_at)
                VALUES('v0旧随笔','冷静','["宏观"]','旧库正文保留',1,'2026-11-20T10:00:00+08:00','2026-11-20T10:00:00+08:00');
            INSERT INTO article_status(filename,status,note,updated_at)
                VALUES('legacy.md','read','旧笔记','2026-11-20T10:00:00+08:00');
            PRAGMA user_version = 0;
            """
        )
        con.commit()
    finally:
        con.close()


def test_migrations_0_to_3_wal_backup_and_restrict_constraints(tmp_path: Path) -> None:
    """Verify v0->v3 atomic migration, WAL hot backup with retention, and ON DELETE RESTRICT."""
    db_path = tmp_path / "data" / "user_journal.db"
    _create_legacy_v0_db(db_path)

    store = PortfolioStore(db_path)
    with store._connect("read") as db:
        uv = int(db.execute("PRAGMA user_version").fetchone()[0])
        assert uv == 3
        jm = str(db.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        assert jm == "wal"
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []

        tables = {
            str(r[0])
            for r in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }
        expected_v2_v3 = {
            "meta", "trade_reviews", "essays", "article_status",
            "account", "instrument", "spot_balance", "derivative_position",
            "curation_rule", "user_setting", "quote", "fx_quote",
            "change_set", "change_item", "credential", "audit_event",
            "import_preview",
        }
        assert expected_v2_v3.issubset(tables)

        rev_row = db.execute("SELECT symbol, position_id, rev FROM trade_reviews").fetchone()
        assert rev_row["symbol"] == "BTC"
        assert rev_row["position_id"] is None
        assert int(rev_row["rev"]) == 0

    # Insert spot referencing default account & AAPL instrument -> ON DELETE RESTRICT blocks delete
    store.apply_changes(
        request_id="req-seed-aapl",
        expected_revision=0,
        actor_id="owner",
        ops=[
            {
                "op": "put",
                "entity": "spot",
                "data": {"instrument_id": "AAPL", "qty": "5", "avg_cost": "200"},
            }
        ],
    )
    with pytest.raises(ConstraintViolationError):
        store.apply_changes(
            request_id="req-del-acct",
            expected_revision=1,
            actor_id="owner",
            ops=[{"op": "remove", "entity": "account", "id": "acct_default"}],
        )

    # Create 4 WAL backups keeping latest 3
    backup_dir = tmp_path / "backups"
    reports = []
    for _ in range(4):
        reports.append(backup_journal.create_verified_backup(db_path, backup_dir, keep=3))
        time.sleep(0.01)

    last = reports[-1]
    assert last["ok"] is True
    assert last["integrity_check"] == "ok"
    assert last["foreign_key_check"] == []
    assert last["user_version"] == 3
    assert last["retained_count"] == 3
    assert last["table_counts"]["trade_reviews"] == 1
    assert last["table_counts"]["spot_balance"] == 1

    # Verify restored snapshot readable cleanly
    restored = JournalStore(last["backup_path"]).get_snapshot()
    assert restored["trade_reviews"][0]["symbol"] == "BTC"
    assert restored["essays"][0]["title"] == "v0旧随笔"


def test_valuation_1520_case_liquidation_inverse_depeg_zero_equity_and_fx() -> None:
    """Verify pure-Decimal wallet equity ('total_ex_upl'), risk, liquidation, depeg, and FX projection."""
    # 1. Canonical $1,520 example: $1,000 cash (incl $100 margin) + $500 stock + short 0.01 BTC (E=50000, P=48000)
    res = valuation.evaluate_portfolio(
        spot_balances=[
            {
                "instrument_id": "USD",
                "symbol": "USD",
                "is_cash": True,
                "qty": "1000",
                "avg_cost": "1",
                "locked_margin_usd": "100",
            },
            {
                "instrument_id": "AAPL",
                "symbol": "AAPL",
                "is_cash": False,
                "qty": "5",
                "avg_cost": "90",
                "sleeve": "other",
            },
        ],
        derivative_positions=[
            {
                "instrument_id": "BTC-USDT-SWAP",
                "symbol": "BTC-USDT-SWAP",
                "side": "short",
                "margin_mode": "isolated",
                "contract_type": "linear",
                "qty": "0.01",
                "qty_unit": "coin",
                "contract_multiplier": "1",
                "entry_price": "50000",
                "margin_amount": "100",
                "margin_usd": "100",
                "mmr": "0.005",
                "sleeve": "bear_basket",
            }
        ],
        quotes={
            "AAPL": {"price": "100"},
            "BTC-USDT-SWAP": {"mark_price": "48000"},
        },
        strategy_targets=[
            {"id": "btc_trend", "recommended_pct": 0.0},
            {"id": "gold_hedge", "recommended_pct": 0.0},
            {"id": "bear_basket", "recommended_pct": 35.0},
            {"id": "crash_bounce", "recommended_pct": 0.0},
        ],
    )
    assert res["equity_mode"] == "total_ex_upl"
    assert res["derivatives_upl_usd"] == Decimal("20")
    assert res["net_equity_usd"] == Decimal("1520")
    assert res["long_risk_usd"] == Decimal("500")
    assert res["short_risk_usd"] == Decimal("480")
    assert res["net_direction_pct"] == Decimal("1.32")
    assert res["effective_leverage"] == Decimal("0.645")

    deriv_out = res["derivative_positions"][0]
    assert deriv_out["liq_source"] == "estimated_isolated"
    # Short isolated linear: (0.01 * 50000 + 100) / (0.01 * (1 + 0.005)) = 600 / 0.01005
    expected_pliq = Decimal("600") / Decimal("0.01005")
    assert abs(deriv_out["liq_price"] - expected_pliq) < Decimal("0.01")
    assert deriv_out["liq_distance_pct"] > Decimal("0")

    # 2. FX single-point projection USD -> CNY: equities/notionals scale; unit prices & %/leverage invariant
    cny_proj = valuation.project_valuation_currency(
        res,
        currency="CNY",
        fx_snapshot={"rate": "7.0", "status": "ok", "label": "人民币估值 · 离岸价"},
    )
    assert cny_proj["net_equity"] == "10640"
    assert cny_proj["net_equity_usd"] == "1520"
    assert cny_proj["net_direction_pct"] == "1.32"
    assert cny_proj["effective_leverage"] == "0.645"
    assert cny_proj["derivative_positions"][0]["entry_price"] == "50000"
    assert cny_proj["derivative_positions"][0]["mark_price"] == "48000"

    unavail_cny = valuation.project_valuation_currency(
        res,
        currency="CNY",
        fx_snapshot={"rate": None, "status": "unavailable"},
    )
    assert unavail_cny["fx_status"] == "unavailable"
    assert unavail_cny["net_equity"] is None

    # 3. Cross margin without venue_liq -> 无法估算; venue_liq overrides; inverse contract; two-way; USDT depeg
    cross_and_inv = valuation.evaluate_portfolio(
        spot_balances=[
            {"instrument_id": "USDT", "symbol": "USDT", "is_cash": True, "qty": "1000", "avg_cost": "1"},
            {"instrument_id": "BTC", "symbol": "BTC", "is_cash": False, "qty": "0.1", "avg_cost": "45000"},
        ],
        derivative_positions=[
            {
                "instrument_id": "ETH-USDT-SWAP",
                "symbol": "ETH-USDT-SWAP",
                "side": "long",
                "margin_mode": "cross",
                "contract_type": "linear",
                "qty": "1",
                "qty_unit": "coin",
                "entry_price": "2000",
            },
            {
                "instrument_id": "BTC-USD-SWAP",
                "symbol": "BTC-USD-SWAP",
                "side": "short",
                "margin_mode": "cross",
                "contract_type": "inverse",
                "qty": "10",
                "qty_unit": "contract",
                "contract_multiplier": "100",
                "entry_price": "50000",
                "venue_liq": "62000",
                "liq_asof": "2026-11-22 23:00",
            },
        ],
        quotes={
            "USDT-USD": {"price": "0.97"},
            "BTC": {"price": "48000"},
            "ETH-USDT-SWAP": {"mark_price": "2100"},
            "BTC-USD-SWAP": {"mark_price": "48000"},
        },
    )
    # 1000 * 0.97 = 970 USD cash (USDT depeg reflected)
    assert cross_and_inv["cash_usd"] == Decimal("970")
    eth_pos, btc_inv = cross_and_inv["derivative_positions"]
    assert eth_pos["liq_price"] is None and eth_pos["liq_status"] == "无法估算"
    assert btc_inv["liq_source"] == "venue" and btc_inv["liq_price"] == Decimal("62000")
    assert btc_inv["upl_coin"] > Decimal("0")

    # 4. Zero / negative net equity -> percentage & leverage gracefully None
    zero_eq = valuation.evaluate_portfolio(
        spot_balances=[{"instrument_id": "USD", "symbol": "USD", "is_cash": True, "qty": "100", "avg_cost": "1"}],
        derivative_positions=[],
        confirmed_debt_usd="150",
    )
    assert zero_eq["net_equity_usd"] == Decimal("-50")
    assert zero_eq["net_direction_pct"] is None
    assert zero_eq["effective_leverage"] is None

    # 5. Strict decimal validation
    assert valuation.parse_decimal_str("-0.000") == "0"
    for bad in (1.5, True, "NaN", "Infinity", "1e5", "0." + "1" * 19, "1" * 39):
        with pytest.raises(ValueError):
            valuation.parse_decimal_str(bad)


def test_revision_conflict_and_idempotency_in_stores(tmp_path: Path) -> None:
    """Verify optimistic revision 409 conflict and (actor_id, request_id) idempotency enforcement."""
    store = PortfolioStore(tmp_path / "rev.db")
    r1 = store.apply_changes(
        request_id="idem-1",
        expected_revision=0,
        actor_id="agent-rw",
        ops=[{"op": "put", "entity": "spot", "data": {"instrument_id": "USD", "is_cash": True, "qty": "1000"}}],
    )
    assert r1["revision"] == 1 and r1["idempotent_replay"] is False

    # Identical retry replays idempotently
    r1_replay = store.apply_changes(
        request_id="idem-1",
        expected_revision=0,
        actor_id="agent-rw",
        ops=[{"op": "put", "entity": "spot", "data": {"instrument_id": "USD", "is_cash": True, "qty": "1000"}}],
    )
    assert r1_replay["idempotent_replay"] is True and r1_replay["revision"] == 1

    # Same request_id with differing payload -> IdempotencyConflictError (409)
    with pytest.raises(IdempotencyConflictError):
        store.apply_changes(
            request_id="idem-1",
            expected_revision=0,
            actor_id="agent-rw",
            ops=[{"op": "put", "entity": "spot", "data": {"instrument_id": "USD", "is_cash": True, "qty": "9999"}}],
        )

    # Stale expected_revision -> RevisionConflictError (409)
    with pytest.raises(RevisionConflictError):
        store.apply_changes(
            request_id="idem-2",
            expected_revision=0,
            actor_id="agent-rw",
            ops=[{"op": "put", "entity": "spot", "data": {"instrument_id": "NVDA", "qty": "2"}}],
        )


def test_curation_astra_scores_radar_and_sort_modes() -> None:
    """Verify Astra B=42->42.0 noise gate, strong holding boost, bilingual alias matching, and sort=importance|time."""
    ws = {
        "summary": {"net_equity_usd": "10000"},
        "spot_balances": [
            {"instrument_id": "NVDA", "symbol": "NVDA", "is_cash": False, "qty": "10", "market_value_usd": "2000", "pct_of_equity": "20.0"},
        ],
        "derivative_positions": [
            {"instrument_id": "BTC-USDT-SWAP", "symbol": "BTC", "side": "short", "qty": "0.1", "notional_usd": "2000", "pct_of_equity": "20.0"},
        ],
    }
    rules = [
        {"kind": "instrument", "target": "NVDA", "action": "watch", "points": 0, "enabled": True},
    ]
    candidates = [
        {
            "id": "b42-noise",
            "source": "T2媒体",
            "category": "ai",
            "direction": "bullish",
            "attention_score": 42,
            "selected": False,
            "participant_count": 2,
            "heat_48h": 60.0,
            "is_backfill": False,
            "tickers": ["NVDA"],
            "time_bjt": "2026-11-22 22:00",
            "title": "NVDA 蹭词短讯",
            "summary": "低质量内容",
        },
        {
            "id": "b75-held-watch",
            "source": "SEC EDGAR",
            "category": "equities",
            "direction": "bullish",
            "attention_score": 75,
            "selected": True,
            "participant_count": 2,
            "heat_48h": 60.0,
            "is_backfill": False,
            "tickers": ["NVDA"],
            "time_bjt": "2026-11-22 20:00",
            "title": "NVDA 营收与算力订单超预期",
            "summary": "数据中心业务增长显著",
        },
        {
            "id": "other-high",
            "source": "CNBC 美股",
            "category": "equities",
            "direction": "bullish",
            "attention_score": 88,
            "selected": True,
            "participant_count": 1,
            "heat_48h": 10.0,
            "is_backfill": False,
            "tickers": ["MSFT"],
            "time_bjt": "2026-11-22 21:30",
            "title": "Microsoft Azure expands enterprise capacity",
            "summary": "Cloud demand steady.",
        },
        {
            "id": "nvda-cn-only",
            "source": "华尔街见闻",
            "category": "ai",
            "direction": "bullish",
            "attention_score": 70,
            "selected": False,
            "participant_count": 1,
            "heat_48h": 10.0,
            "is_backfill": False,
            "tickers": [],
            "time_bjt": "2026-11-22 20:30",
            "title": "英伟达新一代算力服务器出货量超预期",
            "summary": "产业链订单能见度延续。",
        },
        {
            "id": "btc-short-tailwind",
            "source": "方程式快讯",
            "category": "crypto",
            "direction": "bearish",
            "attention_score": 80,
            "selected": True,
            "participant_count": 1,
            "heat_48h": 40.0,
            "is_backfill": False,
            "tickers": ["BTC"],
            "time_bjt": "2026-11-22 21:00",
            "title": "BTC 现货 ETF 出现单日大额净流出",
            "summary": "短线承压",
        },
        {
            "id": "sec-routine",
            "source": "SEC EDGAR",
            "category": "equities",
            "direction": "bearish",
            "attention_score": 65,
            "selected": True,
            "participant_count": 1,
            "heat_48h": 10.0,
            "is_backfill": False,
            "tickers": ["NVDA"],
            "time_bjt": "2026-11-22 18:00",
            "title": "英伟达 (NVDA) 提交 季度财报 (10-Q)",
            "summary": "例行法定披露",
        },
    ]
    by_imp = curation.rank_curated_signals(candidates, rules=rules, workspace=ws, sort="importance")
    by_id = {x["id"]: x for x in by_imp}
    assert by_id["b42-noise"]["final_score"] == 42.0
    assert by_id["b75-held-watch"]["final_score"] == 97.7
    assert by_id["b75-held-watch"]["personal_high"] is True
    assert by_id["b75-held-watch"]["held_match"] is True
    assert by_id["b75-held-watch"]["reasons"] == ["客观 75", "持仓与自选 +21.2", "热度 +1.4"]
    assert by_id["b75-held-watch"]["holding_radar"]["relation"] == "顺风"
    assert by_id["nvda-cn-only"]["held_match"] is True
    assert by_id["nvda-cn-only"]["personal_high"] is True
    assert by_id["nvda-cn-only"]["final_score"] == 91.2
    assert by_id["nvda-cn-only"]["holding_radar"]["relation"] == "顺风"
    # Held items outrank non-held high-score general news
    ids_imp = [x["id"] for x in by_imp]
    assert ids_imp.index("b75-held-watch") < ids_imp.index("other-high")
    assert ids_imp.index("nvda-cn-only") < ids_imp.index("other-high")
    assert by_id["btc-short-tailwind"]["holding_radar"]["relation"] == "顺风"
    assert by_id["sec-routine"]["holding_radar"]["relation"] == "方向未明"

    by_time = curation.rank_curated_signals(candidates, rules=rules, workspace=ws, sort="time")
    assert [x["id"] for x in by_time] == ["b42-noise", "other-high", "btc-short-tailwind", "nvda-cn-only", "b75-held-watch", "sec-routine"]

    with pytest.raises(ValueError):
        curation.validate_curation_rule("regex", "(a+)+", "block", 0)


def test_import_positions_unit_guards_and_commit_flow(tmp_path: Path) -> None:
    """Verify two-phase position import rejects ambiguous units/multipliers and commits cleanly."""
    store = PortfolioStore(tmp_path / "imp.db")

    # Missing coin vs contract unit -> error
    bad_unit = import_positions.preview_import(
        store, "acct_default", "text", "BTC 做空 0.01 @50000", "upsert", "owner"
    )
    assert bad_unit["has_errors"] is True
    assert bad_unit["can_commit"] is False
    assert any(i["code"] == "AMBIGUOUS_QTY_UNIT" for i in bad_unit["issues"])

    # Unknown contract multiplier when using 张 without explicit m=
    bad_mult = import_positions.preview_import(
        store, "acct_default", "text", "FOOBAR 做空 10 张 @100", "upsert", "owner"
    )
    assert bad_mult["has_errors"] is True
    assert any(i["code"] == "UNKNOWN_CONTRACT_MULTIPLIER" for i in bad_mult["issues"])

    # Valid $1,520 multi-clause Chinese import
    good = import_positions.preview_import(
        store,
        "acct_default",
        "text",
        "USD 钱包 1000（含 100 美元保证金）；AAPL 5 股 @100；BTC 永续 做空 0.01 个 @50000 逐仓 保证金 100 mmr 0.5%",
        "upsert",
        "owner",
    )
    assert good["has_errors"] is False and good["can_commit"] is True and good["ops_count"] == 3

    commit_res = import_positions.commit_import(
        store, good["preview_id"], "req-imp-1", expected_revision=0, actor_id="owner"
    )
    assert commit_res["revision"] == 1 and commit_res["idempotent_replay"] is False

    # Replay same request_id -> idempotent; re-commit same preview with new request_id -> RevisionConflictError
    replay_res = import_positions.commit_import(
        store, good["preview_id"], "req-imp-1", expected_revision=0, actor_id="owner"
    )
    assert replay_res["idempotent_replay"] is True
    with pytest.raises(RevisionConflictError):
        import_positions.commit_import(
            store, good["preview_id"], "req-imp-2", expected_revision=1, actor_id="owner"
        )

    ws = store.build_workspace(
        public_state={
            "us_equities": {"indices": [], "mag7_and_ai": [{"symbol": "AAPL", "price": 100}], "sectors": []},
            "crypto_quotes": {"swap": {"BTC-USDT-SWAP": {"mark_price": "48000"}}},
            "fx_snapshot": {"rate": "7.0", "status": "ok"},
        }
    )
    assert ws["summary"]["net_equity_usd"] == "1520"


def test_mobile_push_card_v2_masking_and_wecom_wechat_payloads(tmp_path: Path) -> None:
    """Verify Feishu Card 2.0 schema, strict amount masking, WeCom/WeChat payloads, and oc_* block."""
    state = json.loads((COCKPIT_ROOT / "data" / "cockpit_state.json").read_text(encoding="utf-8"))
    ws = {
        "revision": 3,
        "summary": {
            "net_equity_usd": "152000",
            "net_direction_pct": "1.32",
            "effective_leverage": "0.645",
            "min_liq_distance_pct": "24.8",
        },
        "strategy_comparison": [
            {"id": "bear_basket", "name": "熊市配对对冲仓", "actual_pct": "17.0", "target_pct": "5.0", "delta_pp": "+12.0"}
        ],
        "signals": [
            {
                "id": "hw-1",
                "source": "SEC EDGAR",
                "title": "BTC 相关仓位逆风提示",
                "holding_radar": "持仓关联 · 逆风",
                "importance": "高",
                "final_score": 85.0,
            }
        ],
    }

    masked = mobile_push.build_mobile_digest_payload(
        state, workspace=ws, mode="headwind", include_amounts=False
    )
    assert masked["include_amounts"] is False
    assert masked["feishu_card_v2"]["schema"] == "2.0"
    assert masked["wecom_card"]["card_type"] == "text_notice"
    assert "净方向性敞口" in masked["wechat_markdown"]
    assert masked["schedule_preset"] == "morning_evening_alert"

    serialized_masked = json.dumps(
        {
            "text": masked["text"],
            "feishu_card": masked["feishu_card"],
            "feishu_card_v2": masked["feishu_card_v2"],
            "wecom_card": masked["wecom_card"],
            "wechat_markdown": masked["wechat_markdown"],
        },
        ensure_ascii=False,
    )
    assert "152,000" not in serialized_masked and "152000" not in serialized_masked
    assert "35,000" not in serialized_masked

    alerts_1 = mobile_push.evaluate_alert_triggers(state, ws)
    assert len(alerts_1) >= 1
    seen = {a["dedup_key"] for a in alerts_1}
    assert mobile_push.evaluate_alert_triggers(state, ws, seen_keys=seen) == []

    with pytest.raises(ValueError):
        mobile_push.dispatch_mobile_push(
            state, dry_run=True, feishu_open_id="oc_00000000000000000000000000000000"
        )


def test_load_push_env_rw_token_gating(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify MACROCOCKPIT_RW_TOKEN remains blocked unless include_rw_token=True, and password is always blocked."""
    env_file = tmp_path / "hermes.env"
    env_file.write_text(
        "\n".join(
            [
                "MACROCOCKPIT_ACCESS_PASSWORD=pw_secret",
                "MACROCOCKPIT_READ_TOKEN=ro_secret",
                "MACROCOCKPIT_RW_TOKEN=rw_secret",
                "MACROCOCKPIT_SYNC_TOKEN=sync_secret",
            ]
        ),
        encoding="utf-8",
    )
    for k in (
        "MACROCOCKPIT_ACCESS_PASSWORD",
        "MACROCOCKPIT_READ_TOKEN",
        "MACROCOCKPIT_RW_TOKEN",
        "MACROCOCKPIT_SYNC_TOKEN",
    ):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(mobile_push, "HERMES_ENV_PATHS", (env_file,))

    mobile_push.load_push_env(include_sync_token=True)
    assert os.environ.get("MACROCOCKPIT_SYNC_TOKEN") == "sync_secret"
    assert "MACROCOCKPIT_RW_TOKEN" not in os.environ
    assert "MACROCOCKPIT_ACCESS_PASSWORD" not in os.environ
    assert "MACROCOCKPIT_READ_TOKEN" not in os.environ

    mobile_push.load_push_env(include_sync_token=True, include_rw_token=True)
    assert os.environ.get("MACROCOCKPIT_RW_TOKEN") == "rw_secret"
    assert "MACROCOCKPIT_ACCESS_PASSWORD" not in os.environ
    assert "MACROCOCKPIT_READ_TOKEN" not in os.environ


def test_vps_sync_portfolio_rw_anti_rollback_and_rbac(tmp_path: Path) -> None:
    """Verify PORTFOLIO_RW portfolio sync, idempotent noop, anti-rollback protection, and consumer RBAC."""
    import threading
    import urllib.error
    import urllib.request
    from backend import public_snapshot, server, vps_sync
    from backend.portfolio_store import PortfolioStore

    # 1. Seed local private DB with 2 accounts, 2 spots, 1 derivative, 1 curation rule, watchlist
    local_db = tmp_path / "local_journal.db"
    local_store = PortfolioStore(local_db)
    local_store.apply_changes(
        request_id="seed-local-1",
        expected_revision=0,
        actor_id="owner",
        role="PORTFOLIO_RW",
        summary="seed local portfolio",
        ops=[
            {"op": "put", "entity": "account", "id": "acct_default", "data": {"name": "主账户", "kind": "spot"}},
            {"op": "put", "entity": "account", "id": "acct_trading", "data": {"name": "交易账户", "kind": "futures"}},
            {
                "op": "put",
                "entity": "spot",
                "id": "spot:acct_default:BTC",
                "data": {"account_id": "acct_default", "instrument_id": "BTC", "symbol": "BTC", "name": "比特币", "asset_class": "crypto_spot", "qty": "0.1", "avg_cost": "60000", "sleeve": "btc_trend"},
            },
            {
                "op": "put",
                "entity": "spot",
                "id": "spot:acct_default:USDT",
                "data": {"account_id": "acct_default", "instrument_id": "USDT", "symbol": "USDT", "name": "USDT", "asset_class": "cash", "is_cash": True, "qty": "2500"},
            },
            {
                "op": "put",
                "entity": "derivative",
                "id": "deriv:acct_trading:ZHIPUUSDT",
                "data": {"account_id": "acct_trading", "instrument_id": "ZHIPUUSDT", "symbol": "ZHIPUUSDT", "name": "智谱永续", "side": "short", "qty": "2.59", "qty_unit": "coin", "entry_price": "37.93", "margin_usd": "50.81", "leverage": "2", "venue_liq": "386.03", "stop_loss": "40", "sleeve": "bear_basket"},
            },
            {
                "op": "put",
                "entity": "rule",
                "id": "rule_watch_zhipu",
                "data": {"kind": "instrument", "target": "ZHIPU", "action": "watch", "points": "0", "note": "盯盘"},
            },
            {
                "op": "put",
                "entity": "setting",
                "id": "watchlist",
                "data": {"key": "watchlist", "value": ["BTC", "ZHIPU"]},
            },
        ],
    )

    # 2. Boot a consumer server with published public snapshot
    remote_dir = tmp_path / "remote_vps"
    (remote_dir / "data").mkdir(parents=True)
    pub_state = {
        "schema_version": 2,
        "generation": 1,
        "generated_at_bjt": "2026-11-27 15:00:00+08:00",
        "risk_gate": {"light": "YELLOW", "long_risk_cap": 1.0, "label": "均衡"},
        "positions": [],
        "signals": [],
        "articles": [],
        "crypto_quotes": {
            "quotes": {
                "BTC": {"price": "61500", "quote_currency": "USD"},
                "ZHIPUUSDT": {"price": "39.0", "quote_currency": "USDT", "mark_price": "39.0"},
            },
            "spot": {"BTC-USDT": {"price": "61500", "quote_currency": "USD"}},
            "swap": {"ZHIPUUSDT": {"price": "39.0", "quote_currency": "USDT", "mark_price": "39.0"}},
        },
        "fx_snapshot": {"pair": "USD/CNH", "rate": "6.69", "status": "ok"},
    }
    public_snapshot.publish_generation(remote_dir, pub_state, [], producer_id="windows-primary", generation=1)
    srv = server.create_server(
        "127.0.0.1",
        0,
        web_dir=COCKPIT_ROOT / "web",
        state_path=remote_dir / "data" / "cockpit_state.json",
        db_path=remote_dir / "data" / "user_journal.db",
        articles_dir=remote_dir / "articles",
        access_password="pw_user_only",
        read_token="tok_ro",
        rw_token="tok_rw",
        sync_token="tok_sync",
        role="consumer",
    )
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    base = f"http://127.0.0.1:{srv.server_port}"
    try:
        # Unauthenticated & READ_ONLY boundaries
        with pytest.raises(urllib.error.HTTPError) as e_401:
            urllib.request.urlopen(f"{base}/api/v2/workspace", timeout=5)
        assert e_401.value.code == 401

        req_ro_ws = urllib.request.Request(f"{base}/api/v2/workspace", headers={"Authorization": "Bearer tok_ro"})
        with pytest.raises(urllib.error.HTTPError) as e_403:
            urllib.request.urlopen(req_ro_ws, timeout=5)
        assert e_403.value.code == 403

        # SYNC_PUBLISH token must be rejected from reading/writing portfolio
        ok_bad, info_bad = vps_sync.sync_portfolio_via_http(local_db, url=base, rw_token="tok_sync")
        assert ok_bad is False and info_bad["reason"] == "HTTP_403"

        # Valid PORTFOLIO_RW initial sync
        ok1, info1 = vps_sync.sync_portfolio_via_http(local_db, url=base, rw_token="tok_rw")
        assert ok1 is True and info1["portfolio_action"] == "synced"
        assert info1["accounts_count"] == 2 and info1["spots_count"] == 2 and info1["derivatives_count"] == 1

        # Verify READ_ONLY /api/state never leaks portfolio
        req_ro_st = urllib.request.Request(f"{base}/api/state", headers={"Authorization": "Bearer tok_ro"})
        with urllib.request.urlopen(req_ro_st, timeout=5) as resp_st:
            pub_resp = json.loads(resp_st.read().decode("utf-8"))
        assert "portfolio" not in pub_resp and "journal" not in pub_resp and "workspace" not in pub_resp

        # Verify PORTFOLIO_RW /api/v2/workspace contains synced holdings
        req_rw_ws = urllib.request.Request(f"{base}/api/v2/workspace", headers={"Authorization": "Bearer tok_rw"})
        with urllib.request.urlopen(req_rw_ws, timeout=5) as resp_ws:
            remote_ws = json.loads(resp_ws.read().decode("utf-8"))
        assert len(remote_ws["spot_balances"]) == 2
        assert len(remote_ws["derivative_positions"]) == 1
        assert remote_ws["derivative_positions"][0]["stop_loss"] == "40"
        assert remote_ws["preferences"]["watchlist"] == ["BTC", "ZHIPU"]

        # Second sync is noop_in_sync
        ok2, info2 = vps_sync.sync_portfolio_via_http(local_db, url=base, rw_token="tok_rw")
        assert ok2 is True and info2["portfolio_action"] == "noop_in_sync"

        # Simulate a newer mobile edit on remote (update stop_loss -> 42 and advance timestamp)
        time.sleep(1.1)
        srv.portfolio_store.apply_changes(
            request_id="mobile-edit-1",
            expected_revision=int(remote_ws["revision"]),
            actor_id="owner:cookie",
            role="PORTFOLIO_RW",
            summary="mobile edit",
            ops=[
                {
                    "op": "put",
                    "entity": "derivative",
                    "id": "deriv:acct_trading:ZHIPUUSDT",
                    "data": {"stop_loss": "42"},
                }
            ],
        )
        ok3, info3 = vps_sync.sync_portfolio_via_http(local_db, url=base, rw_token="tok_rw")
        assert ok3 is True and info3["portfolio_action"] == "skipped_remote_newer"

        # Remote edit remains preserved
        with urllib.request.urlopen(req_rw_ws, timeout=5) as resp_ws2:
            remote_ws2 = json.loads(resp_ws2.read().decode("utf-8"))
        assert remote_ws2["derivative_positions"][0]["stop_loss"] == "42"

        # Explicit force overrides back to local
        ok4, info4 = vps_sync.sync_portfolio_via_http(local_db, url=base, rw_token="tok_rw", force=True)
        assert ok4 is True and info4["portfolio_action"] == "synced"
        with urllib.request.urlopen(req_rw_ws, timeout=5) as resp_ws3:
            remote_ws3 = json.loads(resp_ws3.read().decode("utf-8"))
        assert remote_ws3["derivative_positions"][0]["stop_loss"] == "40"

        # Mobile deletes ALL remaining holdings -> next local sync MUST return skipped_remote_newer (not restore deleted positions!)
        time.sleep(1.1)
        srv.portfolio_store.apply_changes(
            request_id="mobile-delete-all",
            expected_revision=int(remote_ws3["revision"]),
            actor_id="owner:cookie",
            role="PORTFOLIO_RW",
            summary="mobile delete all",
            ops=[
                {"op": "remove", "entity": "spot", "id": "spot:acct_default:BTC"},
                {"op": "remove", "entity": "spot", "id": "spot:acct_default:USDT"},
                {"op": "remove", "entity": "derivative", "id": "deriv:acct_trading:ZHIPUUSDT"},
            ],
        )
        ok5, info5 = vps_sync.sync_portfolio_via_http(local_db, url=base, rw_token="tok_rw")
        assert ok5 is True and info5["portfolio_action"] == "skipped_remote_newer"
        with urllib.request.urlopen(req_rw_ws, timeout=5) as resp_ws4:
            remote_ws4 = json.loads(resp_ws4.read().decode("utf-8"))
        assert remote_ws4["spot_balances"] == [] and remote_ws4["derivative_positions"] == []
    finally:
        srv.shutdown()
        srv.server_close()
        th.join(timeout=3)


def test_vps_sync_spot_natural_key_chunk_resume_https_and_proxy_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify (account_id, instrument_id) spot matching, >80-op chunk resume, HTTPS/redirect guard, and spot_proxy valuation guard."""
    import threading
    import urllib.request
    from backend import market_quotes, public_snapshot, server, vps_sync
    from backend.portfolio_store import PortfolioStore

    # 1. Non-loopback HTTP & redirect guards
    local_db = tmp_path / "chunk_local.db"
    st = PortfolioStore(local_db)
    ops_seed = []
    for i in range(82):
        sym = f"TKN{i:03d}"
        ops_seed.append(
            {
                "op": "put",
                "entity": "spot",
                "id": f"custom_spot_{i}",
                "data": {"account_id": "acct_default", "instrument_id": sym, "symbol": sym, "qty": "10", "avg_cost": "1.2345"},
            }
        )
    # Also seed a derivative with 4-decimal margin & manual mark_price
    ops_seed.append(
        {
            "op": "put",
            "entity": "derivative",
            "id": "deriv:acct_default:ZHIPUUSDT",
            "data": {"account_id": "acct_default", "instrument_id": "ZHIPUUSDT", "side": "short", "qty": "2.59", "entry_price": "37.93", "margin_usd": "50.8123", "mark_price": "39.19"},
        }
    )
    st.apply_changes(
        request_id="seed-82",
        expected_revision=0,
        actor_id="owner",
        role="PORTFOLIO_RW",
        summary="seed 82 spots",
        ops=ops_seed,
    )

    ok_http, info_http = vps_sync.sync_portfolio_via_http(local_db, url="http://evil.example.com", rw_token="tok_rw")
    assert ok_http is False and info_http["reason"] == "insecure_http_forbidden"

    class _Fake307Resp:
        status_code = 307
        headers = {"Location": "https://attacker.example.com/steal"}

    monkeypatch.setattr(vps_sync.requests, "get", lambda *a, **kw: _Fake307Resp())
    ok_redir, info_redir = vps_sync.sync_portfolio_via_http(local_db, url="https://news.example.com", rw_token="tok_rw")
    assert ok_redir is False and info_redir["reason"] == "redirect_blocked_307"
    monkeypatch.undo()

    # 2. Boot consumer server pre-seeded with remote spot under DIFFERENT id for TKN000 (created earlier than local)
    remote_dir = tmp_path / "remote_chunk"
    (remote_dir / "data").mkdir(parents=True)
    public_snapshot.publish_generation(
        remote_dir,
        {"schema_version": 2, "generation": 1, "generated_at_bjt": "2026-11-27 15:00:00+08:00", "risk_gate": {"light": "YELLOW", "long_risk_cap": 1.0, "label": "ok"}, "positions": [], "signals": [], "articles": []},
        [],
        producer_id="windows-primary",
        generation=1,
    )
    remote_store = PortfolioStore(remote_dir / "data" / "user_journal.db")
    remote_store.apply_changes(
        request_id="remote-preseed",
        expected_revision=0,
        actor_id="owner",
        role="PORTFOLIO_RW",
        summary="preseed different id",
        ops=[{"op": "put", "entity": "spot", "id": "legacy_remote_id", "data": {"account_id": "acct_default", "instrument_id": "TKN000", "symbol": "TKN000", "qty": "5"}}],
    )
    # Backdate remote timestamps so first sync treats local as newer
    with sqlite3.connect(remote_dir / "data" / "user_journal.db") as rdb:
        old_ts = "2026-11-26T01:00:00+08:00"
        rdb.execute("UPDATE account SET updated_at=?", (old_ts,))
        rdb.execute("UPDATE spot_balance SET updated_at=?", (old_ts,))
        rdb.execute("UPDATE change_item SET created_at=?", (old_ts,))
        rdb.commit()

    srv = server.create_server(
        "127.0.0.1",
        0,
        web_dir=COCKPIT_ROOT / "web",
        state_path=remote_dir / "data" / "cockpit_state.json",
        db_path=remote_dir / "data" / "user_journal.db",
        articles_dir=remote_dir / "articles",
        access_password="pw",
        read_token="tok_ro",
        rw_token="tok_rw",
        sync_token="tok_sync",
        role="consumer",
    )
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    base = f"http://127.0.0.1:{srv.server_port}"
    try:
        # Simulate failure on chunk 2 (post #2 fails), then retry succeeds!
        real_post = vps_sync.requests.post
        post_calls = {"n": 0}

        def _flaky_post(*a, **kw):
            post_calls["n"] += 1
            if post_calls["n"] == 2:
                raise vps_sync.requests.ConnectionError("transient drop on chunk 2")
            return real_post(*a, **kw)

        monkeypatch.setattr(vps_sync.requests, "post", _flaky_post)
        ok_part, info_part = vps_sync.sync_portfolio_via_http(local_db, url=base, rw_token="tok_rw")
        assert ok_part is False and info_part["reason"] == "ConnectionError"

        monkeypatch.undo()
        ok_resume, info_resume = vps_sync.sync_portfolio_via_http(local_db, url=base, rw_token="tok_rw")
        assert ok_resume is True and info_resume["portfolio_action"] == "synced"

        # Immediately running again MUST return noop_in_sync (spot ID mismatch & 4-dec margin_usd & manual mark_price all aligned!)
        ok_noop, info_noop = vps_sync.sync_portfolio_via_http(local_db, url=base, rw_token="tok_rw")
        assert ok_noop is True and info_noop["portfolio_action"] == "noop_in_sync"

        req_ws = urllib.request.Request(f"{base}/api/v2/workspace", headers={"Authorization": "Bearer tok_rw"})
        with urllib.request.urlopen(req_ws, timeout=5) as r:
            ws = json.loads(r.read().decode("utf-8"))
        assert len(ws["spot_balances"]) == 82
        assert any(s["instrument_id"] == "TKN000" and s["qty"] == "10" for s in ws["spot_balances"])
        assert ws["raw_portfolio"]["derivative_positions"][0]["mark_price"] == "39.19"
    finally:
        srv.shutdown()
        srv.server_close()
        th.join(timeout=3)

    # 3. spot_proxy must NEVER be used as derivative mark_price in valuation
    monkeypatch.setattr(market_quotes, "DATA_DIR", tmp_path)

    class _FakeResp:
        def __init__(self, *, status_code: int = 200, content: bytes = b"") -> None:
            self.status_code = status_code
            self.content = content

    class _ProxyOnlyReq:
        @staticmethod
        def get(url: str, **kw) -> _FakeResp:
            if "bitget.com" in url:
                return _FakeResp(status_code=503)
            if "qt.gtimg.cn" in url:
                return _FakeResp(content='v_usQQQ="200~QQQ~QQQ~650.00~";'.encode("gbk"))
            return _FakeResp(status_code=404)

    monkeypatch.setattr(market_quotes, "requests", _ProxyOnlyReq)
    pq = market_quotes.fetch_hybrid_perp_quotes(symbols=["QQQUSDT"], force_refresh=True, timeout=2)
    assert pq["QQQUSDT"]["kind"] == "spot_proxy" and pq["QQQUSDT"]["is_proxy"] is True
    assert "mark_price" not in pq["QQQUSDT"]

    ev = valuation.evaluate_portfolio(
        spot_balances=[],
        derivative_positions=[{"id": "d1", "account_id": "acct_default", "instrument_id": "QQQUSDT", "symbol": "QQQUSDT", "side": "long", "qty": "1", "qty_unit": "coin", "entry_price": "640", "margin_usd": "100"}],
        quotes=pq,
    )
    assert ev["derivative_positions"][0]["mark_price"] is None
    assert ev["derivative_positions"][0]["price_source"] == "missing"
    assert ev["unpriced_items"] == ["QQQUSDT"]

    # 4. #13: QQQUSDT perp with symbol='QQQ' must NEVER steal public us_equities 'QQQ' spot price
    eq_only_quotes = PortfolioStore._extract_public_quotes({"us_equities": {"indices": [{"symbol": "QQQ", "price": "650"}], "mag7_and_ai": [], "sectors": []}}) if hasattr(PortfolioStore, "_extract_public_quotes") else {}
    from backend.portfolio_store import _extract_public_quotes

    pub_q = _extract_public_quotes({"us_equities": {"indices": [{"symbol": "QQQ", "price": "650"}], "mag7_and_ai": [], "sectors": []}})
    assert pub_q["QQQ"]["kind"] == "equity_spot"
    ev_qqq = valuation.evaluate_portfolio(
        spot_balances=[],
        derivative_positions=[{"id": "d2", "account_id": "acct_default", "instrument_id": "QQQUSDT", "symbol": "QQQ", "side": "long", "qty": "1", "qty_unit": "coin", "entry_price": "640", "margin_usd": "100"}],
        quotes=pub_q,
    )
    assert ev_qqq["derivative_positions"][0]["mark_price"] is None
    assert ev_qqq["unpriced_items"] == ["QQQUSDT"]


def test_vps_sync_round2_domain_isolation_dropped_ack_spot_rename_and_url_safety(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cover Round-2 fixes #2 (watchlist vs deleted holdings & local clear-all), #3 (dropped HTTP ack), #4 (spot rename), #6 (funding_rate/liq_asof), #8 (--url isolation)."""
    import threading
    import urllib.request
    from backend import market_quotes, portfolio_store, public_snapshot, server, vps_sync
    from backend.portfolio_store import PortfolioStore

    monkeypatch.setattr(portfolio_store, "fetch_crypto_quotes", lambda **kw: {"quotes": {}, "spot": {}, "swap": {}})
    monkeypatch.setattr(market_quotes, "fetch_hybrid_perp_quotes", lambda **kw: {"ZHIPUUSDT": {"inst_id": "ZHIPUUSDT", "mark_price": "40.0", "price": "40.0"}})

    remote_dir = tmp_path / "r2_remote"
    (remote_dir / "data").mkdir(parents=True)
    public_snapshot.publish_generation(
        remote_dir,
        {"schema_version": 2, "generation": 1, "generated_at_bjt": "2026-11-27 15:00:00+08:00", "risk_gate": {"light": "YELLOW", "long_risk_cap": 1.0, "label": "ok"}, "positions": [], "signals": [], "articles": []},
        [],
        producer_id="windows-primary",
        generation=1,
    )
    srv = server.create_server(
        "127.0.0.1",
        0,
        web_dir=COCKPIT_ROOT / "web",
        state_path=remote_dir / "data" / "cockpit_state.json",
        db_path=remote_dir / "data" / "user_journal.db",
        articles_dir=remote_dir / "articles",
        access_password="pw",
        read_token="tok_ro",
        rw_token="tok_rw",
        sync_token="tok_sync",
        role="consumer",
    )
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    base = f"http://127.0.0.1:{srv.server_port}"
    try:
        # #2 + #6: Seed local DB with 2 spots + 1 derivative incl funding_rate & liq_asof
        local_db = tmp_path / "r2_local.db"
        lst = PortfolioStore(local_db)
        lst.apply_changes(
            request_id="r2-seed",
            expected_revision=0,
            actor_id="owner",
            role="PORTFOLIO_RW",
            summary="seed",
            ops=[
                {"op": "put", "entity": "spot", "id": "spot:acct_default:SPY", "data": {"account_id": "acct_default", "instrument_id": "SPY", "symbol": "SPY", "qty": "2"}},
                {"op": "put", "entity": "spot", "id": "spot:acct_default:TSLA", "data": {"account_id": "acct_default", "instrument_id": "TSLA", "symbol": "TSLA", "qty": "3"}},
                {
                    "op": "put",
                    "entity": "derivative",
                    "id": "deriv:acct_default:ZHIPUUSDT",
                    "data": {
                        "account_id": "acct_default",
                        "instrument_id": "ZHIPUUSDT",
                        "side": "short",
                        "qty": "2.59",
                        "entry_price": "37.93",
                        "funding_rate": "-0.00234",
                        "liq_asof": "2026-11-27 12:00",
                    },
                },
            ],
        )
        ok1, info1 = vps_sync.sync_portfolio_via_http(local_db, url=base, rw_token="tok_rw")
        assert ok1 is True and info1["portfolio_action"] == "synced"

        # Verify #6 funding_rate & liq_asof arrived on remote
        req_ws = urllib.request.Request(f"{base}/api/v2/workspace", headers={"Authorization": "Bearer tok_rw"})
        with urllib.request.urlopen(req_ws, timeout=5) as r:
            ws1 = json.loads(r.read().decode("utf-8"))
        d0 = ws1["raw_portfolio"]["derivative_positions"][0]
        assert d0["funding_rate"] == "-0.00234"
        assert d0["liq_asof"] == "2026-11-27 12:00"

        # #2: Mobile deletes all holdings; local tweaks watchlist same-second & later -> deleted holdings MUST NOT come back
        time.sleep(1.1)
        srv.portfolio_store.apply_changes(
            request_id="r2-mob-clear",
            expected_revision=int(ws1["revision"]),
            actor_id="owner:cookie",
            role="PORTFOLIO_RW",
            summary="clear holdings",
            ops=[
                {"op": "remove", "entity": "spot", "id": "spot:acct_default:SPY"},
                {"op": "remove", "entity": "spot", "id": "spot:acct_default:TSLA"},
                {"op": "remove", "entity": "derivative", "id": "deriv:acct_default:ZHIPUUSDT"},
            ],
        )
        lst.apply_changes(
            request_id="r2-watch-1",
            expected_revision=1,
            actor_id="owner",
            role="PORTFOLIO_RW",
            summary="watchlist",
            ops=[{"op": "put", "entity": "setting", "id": "watchlist", "data": {"key": "watchlist", "value": ["NVDA"]}}],
        )
        ok2, info2 = vps_sync.sync_portfolio_via_http(local_db, url=base, rw_token="tok_rw")
        assert ok2 is True and info2["portfolio_action"] == "synced"
        assert info2["holdings_synced"] is False and info2["prefs_synced"] is True
        with urllib.request.urlopen(req_ws, timeout=5) as r:
            ws2 = json.loads(r.read().decode("utf-8"))
        assert ws2["spot_balances"] == [] and ws2["derivative_positions"] == []
        assert ws2["preferences"]["watchlist"] == ["NVDA"]

        # Re-sync local with force so both sides have SPY+TSLA, then test #4 spot rename on remote + local sync
        ok_f, _ = vps_sync.sync_portfolio_via_http(local_db, url=base, rw_token="tok_rw", force=True)
        assert ok_f is True
        with urllib.request.urlopen(req_ws, timeout=5) as r:
            ws_f = json.loads(r.read().decode("utf-8"))
        time.sleep(1.1)
        srv.portfolio_store.apply_changes(
            request_id="r2-rename-tsla-to-spy",
            expected_revision=int(ws_f["revision"]),
            actor_id="owner:cookie",
            role="PORTFOLIO_RW",
            summary="rename spot",
            ops=[
                {"op": "remove", "entity": "spot", "id": "spot:acct_default:SPY"},
                {"op": "put", "entity": "spot", "id": "spot:acct_default:TSLA", "data": {"account_id": "acct_default", "instrument_id": "SPY", "symbol": "SPY", "qty": "9"}},
            ],
        )
        # Local touches TSLA newer -> local now puts BOTH TSLA and SPY without overwriting remote's renamed SPY row!
        time.sleep(1.1)
        lst.apply_changes(
            request_id="r2-local-tsla-newer",
            expected_revision=2,
            actor_id="owner",
            role="PORTFOLIO_RW",
            summary="touch tsla",
            ops=[{"op": "put", "entity": "spot", "id": "spot:acct_default:TSLA", "data": {"account_id": "acct_default", "instrument_id": "TSLA", "symbol": "TSLA", "qty": "4"}}],
        )
        ok_ren, info_ren = vps_sync.sync_portfolio_via_http(local_db, url=base, rw_token="tok_rw")
        assert ok_ren is True and info_ren["portfolio_action"] == "synced"
        with urllib.request.urlopen(req_ws, timeout=5) as r:
            ws_ren = json.loads(r.read().decode("utf-8"))
        iids_ren = {s["instrument_id"] for s in ws_ren["spot_balances"]}
        assert iids_ren == {"SPY", "TSLA"}

        # #2 reverse: local intentionally clears all holdings after prior sync -> next sync clears remote too!
        time.sleep(1.1)
        lst.apply_changes(
            request_id="r2-local-clear-all",
            expected_revision=3,
            actor_id="owner",
            role="PORTFOLIO_RW",
            summary="close all",
            ops=[
                {"op": "remove", "entity": "spot", "id": "spot:acct_default:SPY"},
                {"op": "remove", "entity": "spot", "id": "spot:acct_default:TSLA"},
                {"op": "remove", "entity": "derivative", "id": "deriv:acct_default:ZHIPUUSDT"},
            ],
        )
        ok_clr, info_clr = vps_sync.sync_portfolio_via_http(local_db, url=base, rw_token="tok_rw")
        assert ok_clr is True and info_clr["portfolio_action"] == "synced"
        with urllib.request.urlopen(req_ws, timeout=5) as r:
            ws_clr = json.loads(r.read().decode("utf-8"))
        assert ws_clr["spot_balances"] == [] and ws_clr["derivative_positions"] == []

        # #3: 161 items -> dropped HTTP response after server commits chunk 1 -> retry resumes chunk 2 & 3 to exact 161!
        ack_db = tmp_path / "r2_161.db"
        st161 = PortfolioStore(ack_db)
        ops_161 = [
            {
                "op": "put",
                "entity": "spot",
                "id": f"sp_{i:03d}",
                "data": {"account_id": "acct_default", "instrument_id": f"M{i:03d}", "symbol": f"M{i:03d}", "qty": "1"},
            }
            for i in range(161)
        ]
        st161.apply_changes(
            request_id="seed-161-a",
            expected_revision=0,
            actor_id="owner",
            role="PORTFOLIO_RW",
            summary="seed 161 a",
            ops=ops_161[:100],
        )
        st161.apply_changes(
            request_id="seed-161-b",
            expected_revision=1,
            actor_id="owner",
            role="PORTFOLIO_RW",
            summary="seed 161 b",
            ops=ops_161[100:],
        )
        real_post = vps_sync.requests.post
        post_n = {"n": 0}

        def _drop_ack_after_commit(*a, **kw):
            resp = real_post(*a, **kw)
            post_n["n"] += 1
            if post_n["n"] == 1:
                raise vps_sync.requests.ConnectionError("ack lost after server committed chunk 1")
            return resp

        time.sleep(1.1)
        from unittest.mock import patch

        with patch.object(vps_sync.requests, "post", side_effect=_drop_ack_after_commit):
            ok_drop, info_drop = vps_sync.sync_portfolio_via_http(ack_db, url=base, rw_token="tok_rw")
            assert ok_drop is False and info_drop["reason"] == "ConnectionError"
        ok_rec, info_rec = vps_sync.sync_portfolio_via_http(ack_db, url=base, rw_token="tok_rw")
        assert ok_rec is True and info_rec["portfolio_action"] == "synced"
        with urllib.request.urlopen(req_ws, timeout=5) as r:
            ws_161 = json.loads(r.read().decode("utf-8"))
        assert len(ws_161["spot_balances"]) == 161

        # #8: --url public publish destination must NEVER override MACROCOCKPIT_PORTFOLIO_URL
        seen_pf_urls: list[str] = []
        state_tmp = tmp_path / "cockpit_state.json"
        state_tmp.write_text(
            json.dumps({"schema_version": 2, "generation": 1, "generated_at_bjt": "2026-11-27 15:00:00+08:00", "signals": [], "articles": []}),
            encoding="utf-8",
        )
        monkeypatch.setenv("MACROCOCKPIT_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("MACROCOCKPIT_SYNC_TOKEN", "tok_sync")
        monkeypatch.setenv("MACROCOCKPIT_RW_TOKEN", "tok_rw")
        monkeypatch.setenv("MACROCOCKPIT_PORTFOLIO_URL", "https://private-portfolio.example.com")
        monkeypatch.setattr(vps_sync, "load_push_env", lambda **kw: None)
        monkeypatch.setattr(vps_sync, "publish_via_http", lambda payload, url=None, **kw: (True, {"action": "mock", "pub_url": url}))
        monkeypatch.setattr(
            vps_sync,
            "sync_portfolio_via_http",
            lambda db_p, url=None, **kw: (seen_pf_urls.append(str(url)) or True, {"portfolio_action": "noop_in_sync"}),
        )
        monkeypatch.setattr(
            sys,
            "argv",
            ["vps_sync", "--url", "https://public-only.example.com/api/v2/publish", "--sync-portfolio", "--journal-db", str(ack_db)],
        )
        monkeypatch.setattr(vps_sync, "STATE_PATH", state_tmp)
        vps_sync.main()
        assert seen_pf_urls == ["None"]
    finally:
        srv.shutdown()
        srv.server_close()
        th.join(timeout=3)


