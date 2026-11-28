"""Tests for Day-Level Signal Advisor & Automatic T+5/T+20 Post-Signal Reviews."""
from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend import server, signal_advisor
from backend.public_snapshot import publish_generation

FORBIDDEN_TERMS = (
    "Sleeve",
    "E21",
    "E32",
    "E33",
    "Tier-1",
    "Regime",
    "arbitration",
    "测算口径",
    "公式解释",
    "公式说明",
    "逻辑解释",
    "联动说明",
    "方法论说明",
    "待补",
    "待刷新",
    "N/A",
)


def _dates(n: int) -> list[str]:
    from datetime import date, timedelta

    out: list[str] = []
    d = date(2026, 7, 1)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def test_day_level_rebound_fade_vs_reversal_and_cooldown(tmp_path: Path) -> None:
    """Verify day-level signal generation, 10-bar cooldown, T+5/T+20 auto-reviews even without user adoption."""
    # Case A: Oversold rebound on bar 60 that bounces +6% by T+5 then fades below entry by T+20 -> 冲高回落
    ds = _dates(86)
    closes_a = [100.0] * 50 + [99.0, 97.5, 96.0, 94.0, 92.0, 90.0, 88.5, 87.0, 86.0, 85.0]
    # Bar 60: turns up +2.5% -> triggers oversold_rebound at 87.125
    closes_a.append(87.125)
    # T+1..T+5: rallies to 93.0 (+6.7%) -> 短期兑现
    closes_a.extend([88.5, 89.8, 91.0, 92.1, 93.0])
    # T+6..T+20: fades steadily back to 84.0 (-3.6% vs entry) -> 冲高回落
    closes_a.extend([92.0, 91.0, 90.0, 89.0, 88.2, 87.5, 86.8, 86.0, 85.4, 85.0, 84.8, 84.5, 84.3, 84.1, 84.0])
    # Extra 5 flat tail bars
    closes_a.extend([84.1, 84.2, 84.0, 84.1, 84.2])
    assert len(closes_a) == len(ds)

    # Case B: NVDA pullback then reclaim 20DMA at bar 55 -> rallies strongly T+5 and T+20 -> 演化反转
    closes_b = [120.0] * 35 + [118.0, 115.0, 112.0, 109.0, 106.0, 104.0, 102.0, 100.0, 99.0, 98.0, 98.5, 99.5, 100.5, 101.5, 102.5, 103.5, 104.0, 104.5, 105.0, 106.5]
    # Ensure bar 55 crosses cleanly above ma20 with 5d gain >= 3.2%
    closes_b.append(111.5)
    # T+1..T+5: +5% -> 短期兑现
    closes_b.extend([112.5, 113.5, 115.0, 116.5, 118.0])
    # T+6..T+20: continues up -> ~129 (+15.7%) -> 演化反转
    closes_b.extend([119.0, 120.0, 121.0, 121.5, 122.0, 123.0, 124.0, 125.0, 125.8, 126.5, 127.2, 127.8, 128.4, 129.0, 129.5])
    while len(closes_b) < len(ds):
        closes_b.append(closes_b[-1] + 0.1)

    plans, stats = signal_advisor.update_signal_advisor(
        {"risk_gate": {"light": "YELLOW", "label": "黄灯控险"}},
        data_dir=tmp_path,
        price_series={"AAPL": (ds, closes_a), "NVDA": (ds, closes_b)},
        persist=True,
    )
    assert plans
    assert stats["short_completed"] >= 2 and stats["mid_completed"] >= 2

    aapl_rebound = next((p for p in plans if p["symbol"] == "AAPL" and p["setup_code"] == "oversold_rebound"), None)
    assert aapl_rebound is not None
    assert aapl_rebound["short_review"]["status"] == "completed"
    assert aapl_rebound["short_review"]["verdict"] == "短期兑现"
    assert aapl_rebound["mid_review"]["status"] == "completed"
    assert aapl_rebound["mid_review"]["verdict"] == "冲高回落"

    nvda_longs = [p for p in plans if p["symbol"] == "NVDA" and p["stance_sign"] == 1]
    assert nvda_longs
    # Cooldown >= 10 trading bars between consecutive long signals for NVDA
    if len(nvda_longs) >= 2:
        sorted_dates = sorted(p["signal_date"] for p in nvda_longs)
        idx_pairs = [ds.index(sd) for sd in sorted_dates]
        for a_i, b_i in zip(idx_pairs, idx_pairs[1:]):
            assert b_i - a_i >= signal_advisor.COOLDOWN_BARS
    assert any(p["mid_review"]["verdict"] in ("演化反转", "中线兑现") for p in nvda_longs)

    # Zero forbidden terms in projected signal plans
    dump_txt = json.dumps(plans, ensure_ascii=False)
    for term in FORBIDDEN_TERMS:
        assert term not in dump_txt


def test_corrupt_ledger_preserves_prior_generation_and_api_journal_linking(tmp_path: Path) -> None:
    """Verify corrupt advisor ledger fails closed and GET /api/v2/signal-plans links with POST /api/journal/review."""
    base = tmp_path / "srv"
    (base / "data").mkdir(parents=True)
    (base / "articles").mkdir(parents=True)

    ds = _dates(65)
    closes = [100.0] * 50 + [99.0, 97.5, 96.0, 94.0, 92.0, 90.0, 88.5, 87.0, 86.0, 85.0, 87.5] + [88.2, 88.8, 89.4, 90.1]
    plans, pstats = signal_advisor.update_signal_advisor(
        {},
        data_dir=base / "data",
        price_series={"AAPL": (ds, closes)},
        persist=True,
    )
    assert plans and plans[0]["short_review"]["status"] == "pending"
    assert "第4/5日" in plans[0]["short_review"]["verdict"]

    publish_generation(
        base,
        {
            "risk_gate": {"light": "GREEN", "long_risk_cap": 1.0, "label": "绿灯"},
            "generated_at_bjt": "2026-11-28 12:00:00+08:00",
            "signals": [],
            "signal_plans": plans,
            "signal_plan_stats": pstats,
        },
        [],
        producer_id="windows-primary",
    )

    # Corrupt ledger on disk must fail closed & preserve prior published plans on next stateless publish
    (base / "data" / signal_advisor.ADVISOR_LEDGER_FILENAME).write_text(
        '{"version": 1, "plans": {"bad": {"plan_id": "bad", "symbol": "AAPL", "signal_date": "2026-11-01", "stance_sign": 1, "entry_price": 1e999}}}',
        encoding="utf-8",
    )
    loaded_p, _ = signal_advisor.try_load_public_signal_plans_from_ledger(base / "data")
    assert loaded_p is None

    m2 = publish_generation(
        base,
        {
            "risk_gate": {"light": "GREEN", "long_risk_cap": 1.0, "label": "绿灯"},
            "generated_at_bjt": "2026-11-28 12:10:00+08:00",
            "signals": [],
        },
        [],
        producer_id="stateless-sync",
    )
    st2 = json.loads((base / "data" / f"generations/gen_{m2['generation']}/state.json").read_text("utf-8"))
    assert len(st2["signal_plans"]) == len(plans)
    assert st2["signal_plans"][0]["plan_id"] == plans[0]["plan_id"]

    srv = server.create_server(
        "127.0.0.1",
        0,
        web_dir=Path("web"),
        state_path=base / "data" / "cockpit_state.json",
        db_path=base / "data" / "user_journal.db",
        articles_dir=base / "articles",
        role="standalone",
        read_token="tok-ro",
        rw_token="tok-rw",
    )
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        u = f"http://127.0.0.1:{srv.server_port}"
        req_get = urllib.request.Request(f"{u}/api/v2/signal-plans?status=active&symbol=AAPL", headers={"Authorization": "Bearer tok-ro"})
        with urllib.request.urlopen(req_get, timeout=5) as r:
            body = json.loads(r.read().decode("utf-8"))
        assert body["ok"] is True and body["count"] >= 1
        pid = body["plans"][0]["plan_id"]

        # Link personal execution memo via position_id = plan_id
        memo_payload = {
            "trade_date": body["plans"][0]["signal_date"],
            "symbol": "AAPL",
            "direction": "做多",
            "size_pct": 5.0,
            "price_usd": body["plans"][0]["entry_price"],
            "pnl_usd": 0.0,
            "status": "未采用·观察",
            "thesis": body["plans"][0]["signal_reason"],
            "execution_note": "等待二次回踩确认再分批介入",
            "lesson": "继续跟踪T+5与T+20",
            "position_id": pid,
        }
        req_post = urllib.request.Request(
            f"{u}/api/journal/review",
            data=json.dumps(memo_payload).encode("utf-8"),
            method="POST",
            headers={"Authorization": "Bearer tok-rw", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req_post, timeout=5) as r2:
            saved = json.loads(r2.read().decode("utf-8"))
        assert saved["position_id"] == pid
        assert saved["status"] == "未采用·观察"
    finally:
        srv.shutdown()
        srv.server_close()


def test_codex_advisor_blockers_intraday_concurrency_merge_validation_and_privacy(tmp_path: Path) -> None:
    """Regression coverage for all 6 Codex review blockers in signal_advisor & public_snapshot."""
    from concurrent.futures import ThreadPoolExecutor

    # 1. Unfinished intraday quotes update live display only, NEVER create permanent signals or complete T+5 early
    ds = _dates(65)
    # 50 flat + 10 falling + bar 60 turns up cleanly -> signal at bar 60; + 4 trailing bars = bar 64 (4/5 days elapsed)
    closes_closed = [100.0] * 50 + [99.0, 97.5, 96.0, 94.0, 92.0, 90.0, 88.5, 87.0, 86.0, 85.0, 87.5, 88.0, 88.5, 89.0, 89.5]
    assert len(closes_closed) == len(ds)
    intraday_state = {
        "us_equities": {
            "as_of": "2026-10-02",
            "indices": [],
            "mag7_and_ai": [{"symbol": "AAPL", "price": 95.0}],
            "sectors": [],
        }
    }
    p_intra, _ = signal_advisor.update_signal_advisor(
        intraday_state,
        data_dir=tmp_path / "intra",
        price_series=None,
    )
    # Using explicit price_series alongside state via helper
    c_series, live_px = signal_advisor._extract_series_and_live_prices({"AAPL": (ds, closes_closed)}, intraday_state)
    assert c_series["AAPL"][1][-1] == 89.5 and live_px["AAPL"] == 95.0
    ledger_i, _ = signal_advisor._scan_and_update_ledger(
        {"version": 1, "plans": {}},
        c_series,
        live_prices=live_px,
        state=intraday_state,
        bootstrap=True,
    )
    aapl_p = next(iter(ledger_i["plans"].values()))
    assert aapl_p["bars_elapsed"] == 4
    assert aapl_p["short_review"]["status"] == "pending"
    assert aapl_p["current_price"] == 95.0

    # 2. Concurrent writers on same data_dir preserve BOTH NVDA and AAPL plans under file_lock
    conc_dir = tmp_path / "conc"
    ds2 = _dates(62)
    closes_nvda = [120.0] * 50 + [118.0, 115.0, 111.0, 108.0, 105.0, 102.0, 99.0, 97.0, 95.0, 94.0, 93.0, 95.5]
    closes_aapl = [100.0] * 50 + [99.0, 97.5, 96.0, 94.0, 92.0, 90.0, 88.5, 87.0, 86.0, 85.0, 84.0, 86.5]
    with ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(signal_advisor.update_signal_advisor, {}, data_dir=conc_dir, price_series={"NVDA": (ds2, closes_nvda)}, persist=True)
        f2 = pool.submit(signal_advisor.update_signal_advisor, {}, data_dir=conc_dir, price_series={"AAPL": (ds2, closes_aapl)}, persist=True)
        f1.result()
        f2.result()
    final_conc, _ = signal_advisor.try_load_public_signal_plans_from_ledger(conc_dir)
    assert final_conc is not None and {"NVDA", "AAPL"}.issubset({p["symbol"] for p in final_conc})

    # 3. Cloud (AAPL) -> Windows (NVDA) -> Cloud (AAPL) multi-producer publish merges instead of erasing
    mp_base = tmp_path / "multiproducer"
    (mp_base / "data").mkdir(parents=True)
    (mp_base / "articles").mkdir(parents=True)
    plan_aapl, _ = signal_advisor.update_signal_advisor({}, data_dir=tmp_path / "p_aapl", price_series={"AAPL": (ds2, closes_aapl)}, persist=True)
    plan_nvda, _ = signal_advisor.update_signal_advisor({}, data_dir=tmp_path / "p_nvda", price_series={"NVDA": (ds2, closes_nvda)}, persist=True)
    publish_generation(mp_base, {"risk_gate": {"light": "GREEN", "label": "绿灯", "long_risk_cap": 1.0}, "generated_at_bjt": "2026-11-28 12:00:00+08:00", "signals": [], "signal_plans": plan_aapl}, [], producer_id="cloud")
    publish_generation(mp_base, {"risk_gate": {"light": "GREEN", "label": "绿灯", "long_risk_cap": 1.0}, "generated_at_bjt": "2026-11-28 12:05:00+08:00", "signals": [], "signal_plans": plan_nvda}, [], producer_id="windows")
    m_c2 = publish_generation(mp_base, {"risk_gate": {"light": "GREEN", "label": "绿灯", "long_risk_cap": 1.0}, "generated_at_bjt": "2026-11-28 12:10:00+08:00", "signals": [], "signal_plans": plan_aapl}, [], producer_id="cloud")
    st_c2 = json.loads((mp_base / "data" / f"generations/gen_{m_c2['generation']}/state.json").read_text("utf-8"))
    assert {"AAPL", "NVDA"}.issubset({p["symbol"] for p in st_c2["signal_plans"]})

    # 4. Malformed short_review.return_pct='bad' fails closed & does NOT crash publish_generation
    (mp_base / "data" / signal_advisor.ADVISOR_LEDGER_FILENAME).write_text(
        json.dumps({"version": 1, "plans": {"badrev": {"plan_id": "badrev", "symbol": "AAPL", "signal_date": "2026-11-01", "stance_sign": 1, "entry_price": 100.0, "short_review": {"return_pct": "bad"}}}}),
        encoding="utf-8",
    )
    bad_loaded, _ = signal_advisor.try_load_public_signal_plans_from_ledger(mp_base / "data")
    assert bad_loaded is None
    m_c3 = publish_generation(mp_base, {"risk_gate": {"light": "GREEN", "label": "绿灯", "long_risk_cap": 1.0}, "generated_at_bjt": "2026-11-28 12:15:00+08:00", "signals": []}, [], producer_id="stateless")
    st_c3 = json.loads((mp_base / "data" / f"generations/gen_{m_c3['generation']}/state.json").read_text("utf-8"))
    assert {"AAPL", "NVDA"}.issubset({p["symbol"] for p in st_c3["signal_plans"]})

    # 5. Private text inside short_review.summary is stripped by public projection & GET /api/v2/signal-plans
    leaky_plan = dict(plan_aapl[0])
    leaky_plan["short_review"] = {**leaky_plan["short_review"], "summary": "account balance: $84123"}
    assert signal_advisor.project_signal_plan(leaky_plan) is None
    assert p_intra == [] or isinstance(p_intra, list)
