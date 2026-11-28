"""Tests for backend/kol_intel_engine.py: zero-lookahead backtest, sample gates, noise filtering, and forbidden terms."""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from backend import kol_intel_engine as engine

BJT = timezone(timedelta(hours=8))


def _assert_no_forbidden_recursive(obj: Any, path: str = "root") -> None:
    if isinstance(obj, str):
        low = obj.lower()
        for term in engine.FORBIDDEN_TERMS:
            assert term.lower() not in low, f"Forbidden term {term!r} found at {path}: {obj!r}"
    elif isinstance(obj, dict):
        for k, v in obj.items():
            k_low = str(k).lower()
            for term in engine.FORBIDDEN_TERMS:
                assert term.lower() not in k_low, f"Forbidden term {term!r} in key at {path}.{k}"
            _assert_no_forbidden_recursive(v, f"{path}.{k}")
    elif isinstance(obj, (list, tuple)):
        for i, item in enumerate(obj):
            _assert_no_forbidden_recursive(item, f"{path}[{i}]")


def _write_daily_note(
    root: Path,
    subdir: str,
    day: str,
    handle: str,
    tweets: list[tuple[str, str, str]],
    *,
    mtime_ts: float | None = None,
) -> Path:
    d = root / subdir
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{day}_{handle}.md"
    lines = [
        f"# {handle} ({handle}) - {day}",
        "",
        "- 关注领域：AI算力/美股",
        f"- 类别：{subdir}",
        "",
        "## 最新动态",
        "",
    ]
    for idx, (hhmm, body, url) in enumerate(tweets, start=1):
        dt = datetime.strptime(day, "%Y-%m-%d")
        lines.extend(
            [
                f"### {idx}. [{dt.month:02d}月{dt.day:02d}日 {hhmm}]",
                "",
                f"> {body}",
                "",
                "- 转发: 12 | 点赞: 88",
                f"- 链接: {url}",
                "",
            ]
        )
    p.write_text("\n".join(lines), encoding="utf-8")
    if mtime_ts is not None:
        os.utime(p, (mtime_ts, mtime_ts))
    return p


def _make_synthetic_prices(start_date: str = "2026-08-03", n_days: int = 90) -> tuple[list[str], list[float]]:
    dates: list[str] = []
    closes: list[float] = []
    cur = datetime.strptime(start_date, "%Y-%m-%d").date()
    px = 100.0
    while len(dates) < n_days:
        if cur.weekday() < 5:
            dates.append(cur.isoformat())
            # Deterministic piecewise regime-free step ups and downs
            step = 1.01 if (len(dates) % 10 != 0) else 0.92
            px = round(px * step, 4)
            closes.append(px)
        cur += timedelta(days=1)
    return dates, closes


def test_zero_lookahead_and_exact_win_rates(tmp_path: Path) -> None:
    kol_dir = tmp_path / "x-kol-notes"
    kol_dir.mkdir()
    cfg = {
        "accounts": [
            {"handle": "AlphaChip", "name": "Alpha Chip", "focus": "半导体", "credibility": 9, "type": "kol", "category": "us-stocks", "role": "ai_semi"},
            {"handle": "SmallSample", "name": "Small Sample", "focus": "宏观", "credibility": 8, "type": "kol", "category": "macro"},
            {"handle": "ZeroCall", "name": "Zero Call", "focus": "心理", "credibility": 8, "type": "kol", "category": "investing"},
            {"handle": "Benzinga", "name": "Benzinga", "focus": "快讯", "credibility": 7, "type": "news"},
        ]
    }
    (kol_dir / "config.json").write_text(json.dumps(cfg), encoding="utf-8")

    # Build exact predictable price series for NVDA:
    # Mon-Fri from 2026-08-03 for 18 weeks (~90 trading days)
    dates: list[str] = []
    cur = datetime(2026, 8, 3).date()
    while len(dates) < 90:
        if cur.weekday() < 5:
            dates.append(cur.isoformat())
        cur += timedelta(days=1)

    # Constant +1% per trading bar except bars 45..70 where it drops -0.5% per bar
    closes: list[float] = []
    p = 100.0
    for i in range(90):
        if i == 0:
            closes.append(p)
        elif 45 <= i <= 65:
            p = round(p * 0.99, 4)
            closes.append(p)
        else:
            p = round(p * 1.01, 4)
            closes.append(p)

    sp_path = tmp_path / "stock_profiles_cache.json"
    sp_path.write_text(
        json.dumps({"NVDA": {"chart": {"dates": dates, "closes": closes}}}),
        encoding="utf-8",
    )
    fred_path = tmp_path / "fred_cache.json"
    fred_path.write_text(json.dumps({"series": {}}), encoding="utf-8")

    as_of_dt = datetime(2026, 11, 20, 12, 0, tzinfo=BJT)
    as_of_ts = as_of_dt.timestamp()
    past_ts = as_of_ts - 86400

    # AlphaChip posts 5 independent bullish NVDA calls + 2 duplicate spam posts within 5 days of call #1
    # Call dates chosen on exact trading bars:
    # idx 0 (2026-08-03): bullish -> T+5 up, T+20 up (WIN)
    # 2026-08-04 & 2026-08-06: same stance bullish within 5 calendar days -> MUST consolidate into call #1!
    # idx 10 (2026-08-17): bullish -> T+5 up, T+20 up (WIN)
    # idx 20 (2026-08-31): bullish -> T+5 up, T+20 up (WIN)
    # idx 35 (2026-09-21): bullish -> T+5 up, T+20 (idx 55) down! (5d WIN, 20d LOSS)
    # idx 46 (2026-10-06): bearish -> T+5 down (edge >0 WIN), T+20 (idx 66) down (edge >0 WIN)
    # Plus 1 recent pending call on idx 80 (2026-11-23 > as_of_date -> excluded; idx 75 is 2026-11-16 -> pending!)
    calls_spec = [
        ("2026-08-03", "Bullish $NVDA CoWoS demand surge!"),
        ("2026-08-04", "Still bullish $NVDA on Blackwell ramp!"),
        ("2026-08-06", "Adding $NVDA long today!"),
        ("2026-08-17", "Bullish $NVDA datacenter breakout!"),
        ("2026-08-31", "Strong buy $NVDA ahead of Rubin!"),
        ("2026-09-21", "Bullish $NVDA rally continues!"),
        ("2026-10-06", "Bearish $NVDA short-term overvalued, warning on pullback!"),
        (dates[75], "Bullish $NVDA rebound setup!"),
    ]
    for d_str, body in calls_spec:
        _write_daily_note(kol_dir, "us-stocks", d_str, "AlphaChip", [("10:00", body, f"https://x.com/AlphaChip/status/{d_str.replace('-', '')}")], mtime_ts=past_ts)

    # Future file with mtime > as_of_ts MUST be ignored even if date is earlier!
    _write_daily_note(
        kol_dir,
        "us-stocks",
        "2026-10-20",
        "AlphaChip",
        [("10:00", "Bearish $NVDA crash call!", "https://x.com/AlphaChip/status/999")],
        mtime_ts=as_of_ts + 3600,
    )

    # SmallSample posts 4 winning calls (< 5 threshold -> MUST return None for win rates!)
    for d_str in (dates[1], dates[11], dates[21], dates[31]):
        _write_daily_note(kol_dir, "macro", d_str, "SmallSample", [("09:00", "Bullish $NVDA long!", "https://x.com/SmallSample/status/1")], mtime_ts=past_ts)

    # ZeroCall posts only non-ticker philosophical chatter
    _write_daily_note(kol_dir, "investing", "2026-11-18", "ZeroCall", [("09:00", "Happy Thanksgiving! Good morning everyone.", "https://x.com/ZeroCall/status/1")], mtime_ts=past_ts)

    # Benzinga news wire MUST be excluded completely
    _write_daily_note(kol_dir, "us-stocks", "2026-11-19", "Benzinga", [("09:00", "Bullish $NVDA breaking alert!", "https://x.com/Benzinga/status/1")], mtime_ts=past_ts)

    cache_file = tmp_path / "scorecard_cache.json"
    cards = engine.build_kol_scorecards(
        kol_dir=kol_dir,
        stock_profiles_path=sp_path,
        fred_cache_path=fred_path,
        cache_path=cache_file,
        as_of_ts=as_of_ts,
        force_refresh=True,
    )

    by_h = {c["handle"]: c for c in cards}
    assert "Benzinga" not in by_h

    alpha = by_h["AlphaChip"]
    # 5 verified calls (spam on 08-04 & 08-06 consolidated; future mtime ignored; dates[75] is pending)
    assert alpha["verified_calls"] == 5
    assert alpha["pending_calls"] == 1
    assert alpha["win_rate_5d"] == 100.0
    assert alpha["win_rate_20d"] == 80.0
    assert isinstance(alpha["avg_edge_20d_pct"], float)
    assert alpha["reliability_badge"] in {"高胜率", "稳健参考"}

    small = by_h["SmallSample"]
    assert small["verified_calls"] == 4
    assert small["win_rate_5d"] is None
    assert small["win_rate_20d"] is None
    assert small["avg_edge_5d_pct"] is None
    assert small["avg_edge_20d_pct"] is None
    assert small["profit_factor_20d"] is None
    assert small["reliability_badge"] == "样本积累"

    zero = by_h["ZeroCall"]
    assert zero["verified_calls"] == 0
    assert zero["win_rate_20d"] is None
    assert zero["reliability_badge"] == "定性观察"
    assert cache_file.exists()


def test_noise_filtering_english_distillation_and_forbidden_scrub(tmp_path: Path) -> None:
    kol_dir = tmp_path / "x-kol-notes"
    kol_dir.mkdir()
    cfg = {
        "accounts": [
            {"handle": "Serenity", "name": "Serenity", "focus": "AI算力", "credibility": 9, "type": "kol", "category": "us-stocks", "role": "ai_semi"},
            {"handle": "DeItaone", "name": "Walter Bloomberg", "focus": "快讯", "credibility": 8, "type": "news"},
            {"handle": "FirstSquawk", "name": "FirstSquawk", "focus": "快讯", "credibility": 7, "type": "news"},
            {"handle": "WatcherGuru", "name": "WatcherGuru", "focus": "快讯", "credibility": 7, "type": "news"},
        ]
    }
    (kol_dir / "config.json").write_text(json.dumps(cfg), encoding="utf-8")

    as_of_ts = datetime(2026, 11, 26, 20, 0, tzinfo=BJT).timestamp()
    past_ts = as_of_ts - 600

    # Adversarial tweet containing forbidden words + English AI/semiconductor thesis + lowercase "arm" (must NOT match ARM)
    _write_daily_note(
        kol_dir,
        "us-stocks",
        "2026-11-25",
        "Serenity",
        [
            ("08:00", "Happy Thanksgiving! Enjoying breakfast with family.", "https://x.com/Serenity/status/10"),
            (
                "10:30",
                "Bullish $NVDA and $TSM: CoWoS supply remains constrained through 2027, an arms race in HBM4 pricing despite the new rate Regime and Tier-1 Sleeve arbitration. 待补充 N/A",
                "https://x.com/Serenity/status/11",
            ),
        ],
        mtime_ts=past_ts,
    )

    for nw in ("DeItaone", "FirstSquawk", "WatcherGuru"):
        _write_daily_note(
            kol_dir,
            "us-stocks",
            "2026-11-25",
            nw,
            [("11:00", "BREAKING: $NVDA surge on AI demand!", f"https://x.com/{nw}/status/99")],
            mtime_ts=past_ts,
        )

    # Articles directory with Benzinga/DeItaone spam + high-quality KOL article
    art_dir = kol_dir / "articles"
    art_dir.mkdir()
    (art_dir / "Benzinga_2026-11-25_NVDA_news_123.md").write_text(
        "# @Benzinga\n\n---\n\nBenzinga flash news about $NVDA stock moving up 2% today in premarket trading with strong volume and market interest across semiconductor names.",
        encoding="utf-8",
    )
    (art_dir / "DeItaone_2026-11-25_Headline_124.md").write_text(
        "# @DeItaone\n\n---\n\n$NVDA headline wire flash from WSJ about chip demand and supply chain checks across Asia suppliers.",
        encoding="utf-8",
    )
    long_kol = art_dir / "Serenity_2026-11-25_HBM4_CoWoS_deep_125.md"
    long_kol.write_text(
        "# @Serenity X 长文 · 2026-11-25\n\n- **原文链接**：https://x.com/Serenity/status/11\n\n---\n\n"
        "$NVDA purchase commitments surged to $390B while $TSM CoWoS packaging and $MU HBM4 pricing remain tight into 2027. "
        "Hyperscaler capex continues to expand across Blackwell and Rubin clusters, supporting gross margins across key memory and foundry leaders.",
        encoding="utf-8",
    )
    os.utime(long_kol, (past_ts, past_ts))

    sp_path = tmp_path / "stock_profiles_cache.json"
    sp_path.write_text(json.dumps({}), encoding="utf-8")
    fred_path = tmp_path / "fred_cache.json"
    fred_path.write_text(json.dumps({"series": {}}), encoding="utf-8")

    bundle = engine.generate_kol_intel(
        kol_dir=kol_dir,
        stock_profiles_path=sp_path,
        fred_cache_path=fred_path,
        cache_path=tmp_path / "cache.json",
        as_of_ts=as_of_ts,
    )

    digest = bundle["kol_digest"]
    assert len(digest) == 1
    item = digest[0]
    assert item["handle"] == "Serenity"
    assert item["stance"] == "偏多"
    assert "NVDA" in item["tickers"] and "TSM" in item["tickers"]
    assert "ARM" not in item["tickers"], "lowercase 'arms race' must not trigger ARM ticker"
    assert "原话要点" in item["takeaway"]
    assert len(item["why_usable"]) >= 10
    assert item["win_rate_20d"] is None

    # Signals contract check
    sigs = bundle["kol_actionable_signals"]
    assert len(sigs) == 1
    sig = sigs[0]
    assert sig["category"] in {"macro", "equities", "ashare", "crypto", "ai"}
    assert sig["direction"] == "bullish"
    assert sig["importance"] in {"高", "中"}
    assert sig["source"] in {"X核心观点", "X深度长文"}

    # Articles check: media wires filtered out, Serenity kept
    arts = bundle["quality_articles"]
    assert len(arts) == 1
    assert arts[0]["filename"] == "Serenity_2026-11-25_HBM4_CoWoS_deep_125.md"
    assert "NVDA" in arts[0]["tickers"]

    # Zero forbidden terms across entire bundle
    _assert_no_forbidden_recursive(bundle)


def test_real_local_kol_root_smoke(tmp_path: Path) -> None:
    if not engine.DEFAULT_KOL_DIR.exists():
        pytest.skip("Local x-kol-notes corpus not present")
    sp_override: Path | None = None
    if not engine.DEFAULT_STOCK_PROFILES_CACHE_PATH.is_file() and not engine.DEFAULT_FRED_CACHE_PATH.is_file():
        today_bjt = datetime.now(BJT).date()
        dates = [(today_bjt - timedelta(days=180 - idx)).isoformat() for idx in range(181)]
        closes = [100.0 + idx * 0.4 for idx in range(181)]
        fallback_sp = tmp_path / "fallback_stock_profiles.json"
        fallback_sp.write_text(
            json.dumps(
                {
                    sym: {"chart": {"dates": dates, "closes": closes}}
                    for sym in ("NVDA", "BTC", "ETH", "SOL", "SPY", "QQQ", "TSLA", "AAPL", "MSFT", "META", "AMZN", "GOOGL", "AMD", "TSM")
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        sp_override = fallback_sp
    bundle = engine.generate_kol_intel(
        kol_dir=engine.DEFAULT_KOL_DIR,
        stock_profiles_path=sp_override,
        cache_path=tmp_path / "kol_scorecard_cache.json",
    )
    stats = bundle["stats"]
    assert stats["total_kols"] >= 20
    assert stats["verified_kols"] >= 1
    assert 1 <= len(bundle["kol_digest"]) <= 36
    assert len(bundle["kol_actionable_signals"]) >= 1
    assert len(bundle["quality_articles"]) >= 5

    for item in bundle["kol_digest"]:
        assert item["handle"].lower() not in {"benzinga", "deitaone", "firstsquawk", "watcherguru"}
        if item["verified_calls"] < 5:
            assert item["win_rate_5d"] is None
            assert item["win_rate_20d"] is None
            assert item["avg_edge_20d_pct"] is None

    for art in bundle["quality_articles"]:
        assert not art["filename"].lower().startswith(("benzinga_", "deitaone_", "firstsquawk_", "watcherguru_"))
        assert "偏多逻辑延续" not in art["title"]
        assert "结构性动向跟踪" not in art["title"]
        assert "承压与风控提示" not in art["title"]

    _assert_no_forbidden_recursive(bundle)


def test_polysemy_ai_tokens_call_put_and_no_boilerplate_headlines(tmp_path: Path) -> None:
    kol_dir = tmp_path / "x-kol-notes"
    kol_dir.mkdir()
    cfg = {
        "accounts": [
            {"handle": "AnthropicAI", "name": "Anthropic", "focus": "Claude研究", "credibility": 10, "type": "news", "category": "us-stocks", "role": "ai_semi"},
            {"handle": "Venu_7_", "name": "Venu", "focus": "美股科技", "credibility": 8, "type": "kol", "category": "us-stocks", "role": "ai_semi"},
        ]
    }
    (kol_dir / "config.json").write_text(json.dumps(cfg), encoding="utf-8")

    as_of_ts = datetime(2026, 11, 26, 20, 0, tzinfo=BJT).timestamp()
    past_ts = as_of_ts - 600

    # 1. Tweet using bare "call"/"put"/"tokens" in non-options, non-crypto AI/tech context
    _write_daily_note(
        kol_dir,
        "us-stocks",
        "2026-11-25",
        "Venu_7_",
        [
            (
                "10:00",
                "Bullish $NVDA and $GOOG: LLM context tokens doubled while researchers call it a breakthrough and put inference throughput at 2x.",
                "https://x.com/Venu_7_/status/2001",
            )
        ],
        mtime_ts=past_ts,
    )

    # 2. AnthropicAI research article containing "tokens", "neurons", "put", "calls", "something" (substring "eth")
    art_dir = kol_dir / "articles"
    art_dir.mkdir()
    anth_art = art_dir / "AnthropicAI_2026-11-24_Interpretability_tokens_999.md"
    anth_art.write_text(
        "# @AnthropicAI X 长文 · 2026-11-24\n\n- **原文链接**：https://x.com/AnthropicAI/status/999\n\n---\n\n"
        "New Anthropic Fellows research: Returning to the neuron basis for interpretability. "
        "When Claude processes 100k context tokens during inference, researchers call these circuits polysemantic and put safeguards across model outputs so something subtle never slips through.",
        encoding="utf-8",
    )
    os.utime(anth_art, (past_ts, past_ts))

    sp_path = tmp_path / "stock_profiles_cache.json"
    sp_path.write_text(json.dumps({}), encoding="utf-8")
    fred_path = tmp_path / "fred_cache.json"
    fred_path.write_text(json.dumps({"series": {}}), encoding="utf-8")

    bundle = engine.generate_kol_intel(
        kol_dir=kol_dir,
        stock_profiles_path=sp_path,
        fred_cache_path=fred_path,
        cache_path=tmp_path / "cache.json",
        as_of_ts=as_of_ts,
    )

    dig = bundle["kol_digest"]
    assert len(dig) == 1
    hl = dig[0]["headline"]
    assert hl.startswith("[看多 NVDA/GOOG] "), f"Unexpected headline format: {hl}"
    assert len(hl) <= 65
    for cliché in ("偏多逻辑延续", "结构性动向跟踪", "承压与风控提示", "美股科技与基本面跟踪"):
        assert cliché not in hl
    assert dig[0]["topic"] == "AI算力与半导体"

    arts = bundle["quality_articles"]
    assert len(arts) == 1
    art = arts[0]
    assert art["title"].startswith("@AnthropicAI · ")
    assert "Returning to the neuron basis" in art["title"]
    assert "加密" not in art["excerpt"]
    assert "期权" not in art["excerpt"]
    assert "AI模型架构与安全研究" in art["excerpt"]
    _assert_no_forbidden_recursive(bundle)

