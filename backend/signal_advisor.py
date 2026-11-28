"""Day-level signal suggestion & automatic short/medium-term post-signal review ledger."""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import uuid
from bisect import bisect_left
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from backend.fred_and_market import DATA_DIR
from backend.kol_intel_engine import load_price_series, scrub_forbidden

BJT = timezone(timedelta(hours=8))

ADVISOR_LEDGER_VERSION = 1
ADVISOR_LEDGER_FILENAME = "signal_advisor_ledger.json"
MAX_LEDGER_PLANS = 160
MAX_PUBLIC_SIGNAL_PLANS = 20

SHORT_REVIEW_BARS = 5
MID_REVIEW_BARS = 20
COOLDOWN_BARS = 10
BOOTSTRAP_LOOKBACK_BARS = 40

BROAD_ASSETS = frozenset({"SPY", "SP500", "QQQ", "GOLD", "WTI", "DOLLAR"})

SYMBOL_ZH_NAMES: dict[str, str] = {
    "BTC": "比特币",
    "GOLD": "现货黄金",
    "SPY": "标普500指数",
    "SP500": "标普500指数",
    "QQQ": "纳指100",
    "NVDA": "英伟达",
    "TSM": "台积电",
    "MSFT": "微软",
    "META": "Meta",
    "AMZN": "亚马逊",
    "GOOG": "谷歌",
    "AAPL": "苹果",
    "TSLA": "特斯拉",
    "AMD": "超威半导体",
    "AVGO": "博通",
    "MU": "美光",
    "PLTR": "Palantir",
    "BABA": "阿里巴巴",
    "COIN": "Coinbase",
    "ORCL": "甲骨文",
    "INTC": "英特尔",
    "MSTR": "MicroStrategy",
    "NFLX": "奈飞",
    "ARM": "Arm",
}

ASSET_CATEGORY_MAP: dict[str, str] = {
    "BTC": "crypto",
    "MSTR": "crypto",
    "COIN": "crypto",
    "GOLD": "macro",
    "SPY": "macro",
    "SP500": "macro",
    "DOLLAR": "macro",
    "WTI": "macro",
    "QQQ": "equities",
    "NVDA": "ai",
    "TSM": "ai",
    "AMD": "ai",
    "AVGO": "ai",
    "MU": "ai",
    "ARM": "ai",
    "PLTR": "ai",
    "MSFT": "ai",
    "GOOG": "ai",
    "META": "ai",
    "AMZN": "equities",
    "AAPL": "equities",
    "TSLA": "equities",
    "BABA": "ashare",
}

SETUP_LABELS: dict[str, str] = {
    "oversold_rebound": "日线超跌反弹",
    "trend_reversal": "日线底部反转",
    "trend_breakout": "主升顺风布局",
    "overheat_trim": "过热背离防守",
}

TRACK_FOR_SYMBOL: dict[str, str] = {
    "BTC": "BTC",
    "COIN": "BTC",
    "MSTR": "BTC",
    "GOLD": "GOLD",
    "NVDA": "US_AI",
    "QQQ": "US_AI",
    "TSM": "US_AI",
    "AMD": "US_AI",
    "AVGO": "US_AI",
    "MU": "US_AI",
    "MSFT": "US_AI",
    "GOOG": "US_AI",
    "META": "US_AI",
    "AMZN": "US_AI",
    "AAPL": "US_AI",
    "SPY": "FED_MACRO",
    "SP500": "FED_MACRO",
    "BABA": "ASHARE_HK",
}


def _now_bjt_iso() -> str:
    return datetime.now(BJT).strftime("%Y-%m-%d %H:%M+08:00")


def _fmt_px(px: float) -> str:
    if px >= 1000:
        return f"{px:,.0f}"
    if px >= 100:
        return f"{px:,.1f}"
    return f"{px:,.2f}"


def _clean_text(val: Any, *, max_len: int = 240) -> str:
    txt = scrub_forbidden(str(val or "")).strip()
    return re.sub(r"\s+", " ", txt)[:max_len]


def _wilder_rsi14(closes: list[float]) -> float:
    if len(closes) < 15:
        return 50.0
    start = max(1, len(closes) - 42)
    gains = 0.0
    losses = 0.0
    init_end = start + 14
    for i in range(start, init_end):
        diff = closes[i] - closes[i - 1]
        if diff >= 0:
            gains += diff
        else:
            losses -= diff
    avg_gain = gains / 14.0
    avg_loss = losses / 14.0
    for i in range(init_end, len(closes)):
        diff = closes[i] - closes[i - 1]
        g = diff if diff > 0 else 0.0
        l = -diff if diff < 0 else 0.0
        avg_gain = (avg_gain * 13.0 + g) / 14.0
        avg_loss = (avg_loss * 13.0 + l) / 14.0
    if avg_loss <= 1e-12:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _reject_nonfinite_json(_const: str) -> Any:
    raise ValueError("non-finite JSON")


def _is_valid_review_block_schema(block: Any) -> bool:
    if block is None:
        return True
    if not isinstance(block, dict):
        return False
    hb = block.get("horizon_bars")
    if hb is not None and (type(hb) is not int or hb < 0 or hb > 10**6):
        return False
    for num_k in ("close_price", "return_pct", "max_up_pct", "max_drawdown_pct"):
        if num_k in block and block[num_k] is not None:
            v = block[num_k]
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)):
                return False
    for str_k in ("status", "review_date", "verdict", "outcome", "summary"):
        if str_k in block and block[str_k] is not None and not isinstance(block[str_k], str):
            return False
    return True


def _is_valid_plan_schema(plan: Any) -> bool:
    if not isinstance(plan, dict):
        return False
    pid = plan.get("plan_id")
    sym = plan.get("symbol")
    sdate = plan.get("signal_date")
    if not isinstance(pid, str) or not pid.strip():
        return False
    if not isinstance(sym, str) or not sym.strip():
        return False
    if not isinstance(sdate, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", sdate):
        return False
    stance = plan.get("stance_sign")
    if type(stance) is not int or stance not in (-1, 1):
        return False
    for num_k in ("entry_price", "target_price", "stop_price", "current_price", "live_return_pct"):
        if num_k in plan and plan[num_k] is not None:
            v = plan[num_k]
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)):
                return False
    be = plan.get("bars_elapsed")
    if be is not None and (type(be) is not int or be < 0 or be > 10**6):
        return False
    ep = plan.get("entry_price")
    if isinstance(ep, bool) or not isinstance(ep, (int, float)) or not math.isfinite(float(ep)) or float(ep) <= 0:
        return False
    if not _is_valid_review_block_schema(plan.get("short_review")):
        return False
    if not _is_valid_review_block_schema(plan.get("mid_review")):
        return False
    return True


def _try_load_advisor_ledger(path: Path) -> tuple[dict[str, Any], str]:
    try:
        if not path.exists():
            return {"version": ADVISOR_LEDGER_VERSION, "plans": {}}, "missing"
        if path.stat().st_size > 16 * 1024 * 1024:
            return {"version": ADVISOR_LEDGER_VERSION, "plans": {}}, "error"
        raw = json.loads(path.read_text(encoding="utf-8"), parse_constant=_reject_nonfinite_json)
        if (
            isinstance(raw, dict)
            and type(raw.get("version")) is int
            and raw.get("version") == ADVISOR_LEDGER_VERSION
            and isinstance(raw.get("plans"), dict)
            and all(isinstance(k, str) and _is_valid_plan_schema(v) for k, v in raw["plans"].items())
        ):
            return raw, "ok"
        return {"version": ADVISOR_LEDGER_VERSION, "plans": {}}, "error"
    except (OSError, ValueError, TypeError, OverflowError):
        return {"version": ADVISOR_LEDGER_VERSION, "plans": {}}, "error"


def _extract_series_and_live_prices(
    series: dict[str, tuple[list[str], list[float]]],
    state: dict[str, Any] | None,
) -> tuple[dict[str, tuple[list[str], list[float]]], dict[str, float]]:
    """Keep strictly completed daily bars separate from live intraday quotes."""
    closed_out = {
        k: (list(v[0]), list(v[1]))
        for k, v in series.items()
        if v and len(v[0]) == len(v[1]) and len(v[0]) >= 2
    }
    if "SPY" in closed_out:
        closed_out.pop("SP500", None)
    live_prices: dict[str, float] = {}
    if not isinstance(state, dict):
        return closed_out, live_prices

    eq = state.get("us_equities") or {}
    for bucket in ("indices", "mag7_and_ai", "sectors"):
        for row in eq.get(bucket) or []:
            if not isinstance(row, dict):
                continue
            sym = str(row.get("symbol") or "").strip().upper()
            px = row.get("price")
            if (
                sym in SYMBOL_ZH_NAMES
                and not isinstance(px, bool)
                and isinstance(px, (int, float))
                and math.isfinite(float(px))
                and float(px) > 0
            ):
                live_prices[sym] = float(px)

    cq = ((state.get("crypto_quotes") or {}).get("spot") or {}).get("BTC-USDT")
    if isinstance(cq, dict):
        px = cq.get("price")
        if not isinstance(px, bool) and isinstance(px, (int, float)) and math.isfinite(float(px)) and float(px) > 0:
            live_prices["BTC"] = float(px)

    for mf in state.get("macro_fred") or []:
        if not isinstance(mf, dict):
            continue
        mid = mf.get("id")
        px = mf.get("raw_value")
        if not isinstance(px, bool) and isinstance(px, (int, float)) and math.isfinite(float(px)) and float(px) > 0:
            if mid == "gold":
                live_prices["GOLD"] = float(px)
            elif mid == "btc_spot" and "BTC" not in live_prices:
                live_prices["BTC"] = float(px)

    return closed_out, live_prices


def _detect_day_setup(sym: str, dates: list[str], closes: list[float], idx: int) -> dict[str, Any] | None:
    """Detect day-level swing setups at bar index idx using ONLY data up to idx (zero lookahead)."""
    if idx < 50 or idx >= len(closes):
        return None
    c0 = float(closes[idx])
    c_prev = float(closes[idx - 1])
    if c0 <= 0 or c_prev <= 0:
        return None

    win = closes[: idx + 1]
    ma20 = sum(win[-20:]) / 20.0
    ma20_prev = sum(closes[idx - 20 : idx]) / 20.0
    ma50 = sum(win[-50:]) / 50.0
    ret_1d = (c0 / c_prev - 1.0) * 100.0
    ret_5d = (c0 / closes[idx - 5] - 1.0) * 100.0
    ret_20d = (c0 / closes[idx - 20] - 1.0) * 100.0
    high_20d = max(win[-20:])
    high_20d_prev = max(closes[idx - 20 : idx])
    low_10d = min(win[-10:])
    dd_20d = (c0 / high_20d - 1.0) * 100.0
    rsi14 = _wilder_rsi14(win)
    is_broad = sym in BROAD_ASSETS

    # 1. Overheat divergence trim / defense (take-profit discipline)
    if (ret_20d >= (9.0 if is_broad else 20.0) or rsi14 >= (73.0 if is_broad else 77.0)) and ret_1d <= (-0.6 if is_broad else -1.5):
        stop_px = round(high_20d * (1.015 if is_broad else 1.03), 2)
        target_px = round(ma20, 2)
        return {
            "setup_code": "overheat_trim",
            "setup_label": SETUP_LABELS["overheat_trim"],
            "direction": "防守减仓",
            "stance_sign": -1,
            "horizon_label": "5–20交易日",
            "entry_price": round(c0, 2),
            "target_price": target_px,
            "stop_price": stop_px,
            "signal_reason": f"近20个交易日急涨 {ret_20d:+.1f}%（日线强弱指标 {rsi14:.0f}），当日回吐 {ret_1d:+.1f}%，日线追高盈亏比走弱",
            "action_plan": f"已有浮盈多单建议分批兑现 1/3–1/2 锁定利润，严禁高位追涨；耐心等待回踩 20 日均线（${_fmt_px(ma20)}）企稳再看承接。",
            "invalidation": f"日线连续 3 个交易日放量站稳 ${_fmt_px(stop_px)} 上方则解除防守提示",
        }

    # 2. Day-level oversold rebound (requires turning green on the day, never blind knife-catching)
    if (dd_20d <= (-5.5 if is_broad else -11.0) or rsi14 <= (36.0 if is_broad else 33.0)) and ret_1d >= (0.5 if is_broad else 1.0):
        stop_px = round(low_10d * (0.988 if is_broad else 0.975), 2)
        target_px = round(max(ma20, c0 * (1.03 if is_broad else 1.05)), 2)
        return {
            "setup_code": "oversold_rebound",
            "setup_label": SETUP_LABELS["oversold_rebound"],
            "direction": "做多",
            "stance_sign": 1,
            "horizon_label": "5–20交易日",
            "entry_price": round(c0, 2),
            "target_price": target_px,
            "stop_price": stop_px,
            "signal_reason": f"近20日回撤 {dd_20d:.1f}%（日线强弱指标 {rsi14:.0f}），日线收涨 {ret_1d:+.1f}% 出现止跌企稳信号",
            "action_plan": f"分两档低吸博弈日线级反弹修复，首目标看 20 日均线（${_fmt_px(ma20)}）附近分批减半；未站稳 20 日线前按反弹对待。",
            "invalidation": f"日线收盘跌破 ${_fmt_px(stop_px)} 则无条件止损退出，不扛下跌中继",
        }

    # 3. Day-level bottom reversal (reclaiming 20DMA after deeper pullback)
    if (
        min(closes[idx - 20 : idx]) < ma50 * (0.98 if is_broad else 0.95)
        and c_prev <= ma20_prev
        and c0 > ma20 * 1.004
        and ret_5d >= (1.8 if is_broad else 3.2)
        and 46.0 <= rsi14 <= 68.0
    ):
        stop_px = round(ma20 * (0.988 if is_broad else 0.975), 2)
        target_px = round(max(ma50, c0 * (1.05 if is_broad else 1.10)), 2)
        return {
            "setup_code": "trend_reversal",
            "setup_label": SETUP_LABELS["trend_reversal"],
            "direction": "做多",
            "stance_sign": 1,
            "horizon_label": "10–20交易日",
            "entry_price": round(c0, 2),
            "target_price": target_px,
            "stop_price": stop_px,
            "signal_reason": f"前期调整后日线重返 20 日均线（${_fmt_px(ma20)}）上方，近5日累计修复 {ret_5d:+.1f}%",
            "action_plan": f"右侧反转确认结构，可在现价或回踩 20 日均线（${_fmt_px(ma20)}）不破时分批建仓，按 2–4 周波段跟踪。",
            "invalidation": f"连续两个交易日收盘跌回 ${_fmt_px(stop_px)} 下方视为假突破离场",
        }

    # 4. Trend-following breakout
    if (
        c0 > ma20 > ma50
        and c_prev <= high_20d_prev * 1.002
        and c0 > high_20d_prev * (1.004 if is_broad else 1.008)
        and 52.0 <= rsi14 <= 73.0
    ):
        stop_px = round(ma20, 2)
        target_px = round(c0 * (1.06 if is_broad else 1.12), 2)
        return {
            "setup_code": "trend_breakout",
            "setup_label": SETUP_LABELS["trend_breakout"],
            "direction": "做多",
            "stance_sign": 1,
            "horizon_label": "10–20交易日",
            "entry_price": round(c0, 2),
            "target_price": target_px,
            "stop_price": stop_px,
            "signal_reason": f"日线均线多头排列并突破近 20 日高点（${_fmt_px(high_20d_prev)}），中线趋势延续性较强",
            "action_plan": f"顺大级别趋势分批布局或回踩突破位（${_fmt_px(high_20d_prev)}）加配，以 20 日均线（${_fmt_px(ma20)}）作为移动保护。",
            "invalidation": f"日线收盘失守 20 日均线（${_fmt_px(ma20)}）则减仓落袋",
        }

    return None


def _build_context_note(sym: str, stance_sign: int, state: dict[str, Any] | None) -> str:
    if not isinstance(state, dict):
        return ""
    notes: list[str] = []
    rg = state.get("risk_gate") or {}
    if isinstance(rg, dict) and rg.get("label"):
        notes.append(f"宏观风控：{rg.get('label')}")
        if stance_sign > 0 and str(rg.get("light") or "").upper() in ("YELLOW", "RED"):
            notes.append("大环境偏谨慎，注意轻仓试单")

    track_id = TRACK_FOR_SYMBOL.get(sym, "")
    trends = ((state.get("forum_sentiment") or {}).get("discussion_trends") or {}).get("assets") or []
    for asset in trends:
        if isinstance(asset, dict) and asset.get("asset") == track_id:
            w2 = next((w for w in (asset.get("windows") or []) if isinstance(w, dict) and w.get("days") == 7), None)
            if w2 and w2.get("label"):
                notes.append(f"近7日社媒温度：{w2['label']}")
            break
    return _clean_text("；".join(notes), max_len=140)


def _evaluate_plan_reviews(
    plan: dict[str, Any],
    series: dict[str, tuple[list[str], list[float]]],
    live_prices: dict[str, float] | None = None,
) -> dict[str, Any]:
    out = dict(plan)
    sym = str(out.get("symbol") or "").upper()
    sdate = str(out.get("signal_date") or "")
    entry = float(out.get("entry_price") or 0.0)
    stance_sign = int(out.get("stance_sign") or 1)
    is_broad = sym in BROAD_ASSETS

    pair = series.get(sym)
    live_px = (live_prices or {}).get(sym)
    if not pair or entry <= 0:
        cur_fallback = float(live_px if live_px is not None else (out.get("current_price") or entry))
        out["current_price"] = round(cur_fallback, 2)
        out["live_return_pct"] = round((cur_fallback / entry - 1.0) * 100.0, 2) if entry > 0 else 0.0
        return out

    dates, closes = pair
    idx_0 = bisect_left(dates, sdate)
    if idx_0 >= len(dates):
        idx_0 = len(dates) - 1
    bars_elapsed = max(0, (len(dates) - 1) - idx_0)
    cur_px = float(live_px if live_px is not None else closes[-1])
    live_ret = round((cur_px / entry - 1.0) * 100.0, 2)
    out["current_price"] = round(cur_px, 2)
    out["live_return_pct"] = live_ret
    out["bars_elapsed"] = bars_elapsed

    thr5 = 1.0 if is_broad else 2.0
    thr20 = 2.0 if is_broad else 4.0

    # 1. Short-term auto-review (5 trading days)
    if bars_elapsed >= SHORT_REVIEW_BARS:
        idx_5 = idx_0 + SHORT_REVIEW_BARS
        p5 = float(closes[idx_5])
        d5 = dates[idx_5]
        ret5 = round((p5 / entry - 1.0) * 100.0, 2)
        win5 = [float(x) for x in closes[idx_0 + 1 : idx_5 + 1]]
        max_up5 = round((max(win5) / entry - 1.0) * 100.0, 2)
        max_dn5 = round((min(win5) / entry - 1.0) * 100.0, 2)
        if stance_sign > 0:
            if ret5 >= thr5:
                verdict5, outcome5 = "短期兑现", "hit"
            elif ret5 <= -thr5:
                verdict5, outcome5 = "短期失效", "miss"
            else:
                verdict5, outcome5 = "短期震荡", "flat"
            sum5 = f"满5个交易日（{d5}）收盘 ${_fmt_px(p5)}（{ret5:+.2f}%，期间最高 {max_up5:+.1f}% / 最大回撤 {max_dn5:+.1f}%）"
        else:
            if ret5 <= -thr5 or max_dn5 <= -thr5 * 1.5:
                verdict5, outcome5 = "防守有效", "hit"
            elif ret5 >= thr5:
                verdict5, outcome5 = "继续冲高", "miss"
            else:
                verdict5, outcome5 = "高位盘整", "flat"
            sum5 = f"提示防守后5个交易日（{d5}）收盘 ${_fmt_px(p5)}（{ret5:+.2f}%，期间最大回撤 {max_dn5:+.1f}%）"
        short_rev = {
            "status": "completed",
            "horizon_bars": SHORT_REVIEW_BARS,
            "review_date": d5,
            "close_price": round(p5, 2),
            "return_pct": ret5,
            "max_up_pct": max_up5,
            "max_drawdown_pct": max_dn5,
            "verdict": verdict5,
            "outcome": outcome5,
            "summary": _clean_text(sum5, max_len=180),
        }
    else:
        short_rev = {
            "status": "pending",
            "horizon_bars": SHORT_REVIEW_BARS,
            "review_date": "",
            "close_price": round(cur_px, 2),
            "return_pct": live_ret,
            "max_up_pct": live_ret,
            "max_drawdown_pct": live_ret,
            "verdict": f"第{bars_elapsed}/5日",
            "outcome": "pending",
            "summary": f"已跟踪 {bars_elapsed}/5 个交易日（现价 ${_fmt_px(cur_px)}，{live_ret:+.2f}%），满 5 个交易日自动出具短期复盘",
        }

    # 2. Medium-term auto-review (20 trading days)
    if bars_elapsed >= MID_REVIEW_BARS:
        idx_20 = idx_0 + MID_REVIEW_BARS
        p20 = float(closes[idx_20])
        d20 = dates[idx_20]
        ret20 = round((p20 / entry - 1.0) * 100.0, 2)
        win20 = [float(x) for x in closes[idx_0 + 1 : idx_20 + 1]]
        max_up20 = round((max(win20) / entry - 1.0) * 100.0, 2)
        max_dn20 = round((min(win20) / entry - 1.0) * 100.0, 2)
        r5_val = float(short_rev["return_pct"])
        if stance_sign > 0:
            if r5_val >= thr5 and ret20 >= thr20:
                verdict20, outcome20, note20 = "演化反转", "hit", "短期反弹延续为中线波段趋势"
            elif r5_val >= thr5 and ret20 <= 0.0:
                verdict20, outcome20, note20 = "冲高回落", "flat", "仅属日线反弹而非趋势反转，印证分批落袋必要"
            elif ret20 >= thr20:
                verdict20, outcome20, note20 = "中线兑现", "hit", "经历短期震荡洗盘后完成中线修复"
            elif ret20 <= -thr20:
                verdict20, outcome20, note20 = "中线失效", "miss", "日线关键支撑失守，反转结构未成立"
            else:
                verdict20, outcome20, note20 = "区间拉锯", "flat", "维持箱体震荡，尚未走出单边趋势"
        else:
            if ret20 <= -thr20 or max_dn20 <= -thr20 * 1.4:
                verdict20, outcome20, note20 = "规避深调", "hit", "成功规避中线级别回撤"
            elif ret20 >= thr20:
                verdict20, outcome20, note20 = "中线踏空", "miss", "强势消化过热并延续主升"
            else:
                verdict20, outcome20, note20 = "横盘消化", "flat", "以时间换空间消化前期超买"
        sum20 = f"满20个交易日（{d20}）收盘 ${_fmt_px(p20)}（{ret20:+.2f}%，最高 {max_up20:+.1f}% / 回撤 {max_dn20:+.1f}%），{note20}"
        mid_rev = {
            "status": "completed",
            "horizon_bars": MID_REVIEW_BARS,
            "review_date": d20,
            "close_price": round(p20, 2),
            "return_pct": ret20,
            "max_up_pct": max_up20,
            "max_drawdown_pct": max_dn20,
            "verdict": verdict20,
            "outcome": outcome20,
            "summary": _clean_text(sum20, max_len=200),
        }
    else:
        mid_rev = {
            "status": "pending",
            "horizon_bars": MID_REVIEW_BARS,
            "review_date": "",
            "close_price": round(cur_px, 2),
            "return_pct": live_ret,
            "max_up_pct": live_ret,
            "max_drawdown_pct": live_ret,
            "verdict": f"第{bars_elapsed}/20日",
            "outcome": "pending",
            "summary": f"已跟踪 {bars_elapsed}/20 个交易日（{live_ret:+.2f}%），满 20 个交易日自动检验反弹与反转成色",
        }

    out["short_review"] = short_rev
    out["mid_review"] = mid_rev
    return out


def _finite_float(val: Any, default: float = 0.0) -> float:
    if val is None or isinstance(val, bool):
        return default
    try:
        num = float(val)
        return num if math.isfinite(num) else default
    except (TypeError, ValueError, OverflowError):
        return default


def _project_review_block(block: Any) -> dict[str, Any] | None:
    if not isinstance(block, dict):
        return None
    return {
        "status": str(block.get("status") or "pending"),
        "horizon_bars": int(_finite_float(block.get("horizon_bars"), 0.0)),
        "review_date": str(block.get("review_date") or ""),
        "close_price": round(_finite_float(block.get("close_price"), 0.0), 2),
        "return_pct": round(_finite_float(block.get("return_pct"), 0.0), 2),
        "max_up_pct": round(_finite_float(block.get("max_up_pct"), 0.0), 2),
        "max_drawdown_pct": round(_finite_float(block.get("max_drawdown_pct"), 0.0), 2),
        "verdict": _clean_text(block.get("verdict") or "", max_len=24),
        "outcome": str(block.get("outcome") or "pending"),
        "summary": _clean_text(block.get("summary") or "", max_len=180),
    }


def project_signal_plan(plan: Any) -> dict[str, Any] | None:
    """Strictly allowlist public-safe fields for one day-level signal plan & auto-review."""
    from backend.public_snapshot import has_private_article_text

    if not _is_valid_plan_schema(plan):
        return None
    pid = str(plan["plan_id"]).strip()
    sym = str(plan["symbol"]).strip().upper()
    sdate = str(plan["signal_date"]).strip()

    short_rev = _project_review_block(plan.get("short_review"))
    mid_rev = _project_review_block(plan.get("mid_review"))
    projected_obj = {
        "plan_id": pid,
        "symbol": sym,
        "name": _clean_text(plan.get("name") or SYMBOL_ZH_NAMES.get(sym, sym), max_len=30),
        "category": str(plan.get("category") or ASSET_CATEGORY_MAP.get(sym, "equities")),
        "setup_code": str(plan.get("setup_code") or "oversold_rebound"),
        "setup_label": _clean_text(plan.get("setup_label") or SETUP_LABELS.get(str(plan.get("setup_code")), "日线波段信号"), max_len=24),
        "direction": _clean_text(plan.get("direction") or "做多", max_len=16),
        "stance_sign": int(plan["stance_sign"]),
        "horizon_label": _clean_text(plan.get("horizon_label") or "5–20交易日", max_len=20),
        "signal_date": sdate,
        "entry_price": round(_finite_float(plan["entry_price"], 0.0), 2),
        "target_price": round(_finite_float(plan.get("target_price"), float(plan["entry_price"])), 2),
        "stop_price": round(_finite_float(plan.get("stop_price"), float(plan["entry_price"])), 2),
        "current_price": round(_finite_float(plan.get("current_price"), float(plan["entry_price"])), 2),
        "live_return_pct": round(_finite_float(plan.get("live_return_pct"), 0.0), 2),
        "bars_elapsed": max(0, int(_finite_float(plan.get("bars_elapsed"), 0.0))),
        "signal_reason": _clean_text(plan.get("signal_reason") or "", max_len=180),
        "action_plan": _clean_text(plan.get("action_plan") or "", max_len=180),
        "invalidation": _clean_text(plan.get("invalidation") or "", max_len=140),
        "context_note": _clean_text(plan.get("context_note") or "", max_len=120),
        "short_review": short_rev,
        "mid_review": mid_rev,
        "created_at": str(plan.get("created_at") or ""),
    }
    if has_private_article_text(json.dumps(projected_obj, ensure_ascii=False)):
        return None
    return projected_obj


def project_signal_plans(plans: Any) -> list[dict[str, Any]]:
    if not isinstance(plans, list):
        return []
    out: list[dict[str, Any]] = []
    for row in plans[:MAX_PUBLIC_SIGNAL_PLANS]:
        clean = project_signal_plan(row)
        if clean is not None:
            out.append(clean)
    return out


def merge_signal_plans(
    primary: Any,
    secondary: Any,
    *,
    limit: int = MAX_PUBLIC_SIGNAL_PLANS,
) -> list[dict[str, Any]]:
    """Merge signal plans from multiple producers (e.g. Cloud & Windows) without losing either history."""

    def _richness(p: dict[str, Any], is_primary: bool) -> tuple[int, int, int, int]:
        sr = p.get("short_review") or {}
        mr = p.get("mid_review") or {}
        return (
            1 if isinstance(mr, dict) and mr.get("status") == "completed" else 0,
            1 if isinstance(sr, dict) and sr.get("status") == "completed" else 0,
            int(p.get("bars_elapsed") or 0),
            1 if is_primary else 0,
        )

    merged: dict[str, tuple[tuple[int, int, int, int], dict[str, Any]]] = {}
    for is_prim, raw_list in ((False, secondary), (True, primary)):
        if not isinstance(raw_list, list):
            continue
        for row in raw_list:
            clean = project_signal_plan(row)
            if clean is None:
                continue
            pid = clean["plan_id"]
            rank = _richness(clean, is_prim)
            if pid not in merged or rank >= merged[pid][0]:
                merged[pid] = (rank, clean)

    ordered = sorted(
        (pair[1] for pair in merged.values()),
        key=lambda p: (str(p.get("signal_date") or ""), str(p.get("plan_id") or "")),
        reverse=True,
    )
    return ordered[:limit]


def compute_signal_plan_stats(plans: list[dict[str, Any]]) -> dict[str, Any]:
    s_done = [p["short_review"] for p in plans if isinstance(p.get("short_review"), dict) and p["short_review"].get("status") == "completed"]
    m_done = [p["mid_review"] for p in plans if isinstance(p.get("mid_review"), dict) and p["mid_review"].get("status") == "completed"]
    s_hits = sum(1 for r in s_done if r.get("outcome") == "hit")
    m_hits = sum(1 for r in m_done if r.get("outcome") == "hit")
    active = sum(1 for p in plans if not (isinstance(p.get("mid_review"), dict) and p["mid_review"].get("status") == "completed"))
    return {
        "total": len(plans),
        "active_count": active,
        "short_completed": len(s_done),
        "short_hits": s_hits,
        "mid_completed": len(m_done),
        "mid_hits": m_hits,
    }


def project_signal_plan_stats(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    out: dict[str, Any] = {}
    for k in ("total", "active_count", "short_completed", "short_hits", "mid_completed", "mid_hits"):
        v = raw.get(k)
        out[k] = int(v) if type(v) is int and v >= 0 else 0
    return out


def _scan_and_update_ledger(
    ledger: dict[str, Any],
    series: dict[str, tuple[list[str], list[float]]],
    *,
    live_prices: dict[str, float] | None = None,
    state: dict[str, Any] | None = None,
    bootstrap: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    plans_map: dict[str, dict[str, Any]] = dict(ledger.get("plans") or {})
    new_count = 0

    for sym in sorted(series.keys()):
        if sym not in SYMBOL_ZH_NAMES:
            continue
        dates, closes = series[sym]
        if len(dates) < 52:
            continue

        start_idx = max(50, len(dates) - BOOTSTRAP_LOOKBACK_BARS) if bootstrap else (len(dates) - 1)
        last_bar_by_sign: dict[int, int] = {}
        for p in plans_map.values():
            if str(p.get("symbol") or "").upper() == sym:
                p_idx = bisect_left(dates, str(p.get("signal_date") or ""))
                sign = int(p.get("stance_sign") or 1)
                if p_idx > last_bar_by_sign.get(sign, -999):
                    last_bar_by_sign[sign] = p_idx

        for idx in range(start_idx, len(dates)):
            sdate = dates[idx]
            setup = _detect_day_setup(sym, dates, closes, idx)
            if setup is None:
                continue
            sign = int(setup["stance_sign"])
            if idx - last_bar_by_sign.get(sign, -999) < COOLDOWN_BARS:
                continue

            pid = f"sigplan_{sym}_{sdate}_{setup['setup_code']}"
            if pid not in plans_map:
                ctx = _build_context_note(sym, sign, state) if idx == (len(dates) - 1) else ""
                plans_map[pid] = {
                    "plan_id": pid,
                    "symbol": sym,
                    "name": SYMBOL_ZH_NAMES.get(sym, sym),
                    "category": ASSET_CATEGORY_MAP.get(sym, "equities"),
                    **setup,
                    "signal_date": sdate,
                    "context_note": ctx,
                    "created_at": _now_bjt_iso(),
                }
                new_count += 1
            last_bar_by_sign[sign] = idx

    # Evaluate short & mid reviews on every stored plan using strictly closed bars for verdicts
    evaluated: dict[str, dict[str, Any]] = {}
    for pid, plan in plans_map.items():
        if _is_valid_plan_schema(plan):
            evaluated[pid] = _evaluate_plan_reviews(plan, series, live_prices=live_prices)

    # Prune ledger keeping newest MAX_LEDGER_PLANS
    ordered_pairs = sorted(
        evaluated.items(),
        key=lambda kv: (str(kv[1].get("signal_date") or ""), str(kv[0])),
        reverse=True,
    )[:MAX_LEDGER_PLANS]
    pruned_map = dict(ordered_pairs)

    out_ledger = {
        "version": ADVISOR_LEDGER_VERSION,
        "updated_at_bjt": _now_bjt_iso(),
        "plans": pruned_map,
    }
    ordered_list = [dict(v) for _k, v in ordered_pairs]
    stats = compute_signal_plan_stats(ordered_list)
    stats["new_signals"] = new_count
    return out_ledger, stats


def update_signal_advisor(
    state: dict[str, Any] | None = None,
    *,
    data_dir: Path | str = DATA_DIR,
    price_series: dict[str, tuple[list[str], list[float]]] | None = None,
    persist: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Scan day-level setups, auto-review T+5/T+20 outcomes, persist ledger under lock, and project public plans."""
    from backend.public_snapshot import file_lock

    root_dir = Path(data_dir)
    ledger_path = root_dir / ADVISOR_LEDGER_FILENAME
    raw_series = load_price_series(price_series=price_series)
    closed_series, live_prices = _extract_series_and_live_prices(
        raw_series,
        state if price_series is None else None,
    )

    if not persist:
        out_ledger, stats = _scan_and_update_ledger(
            {"version": ADVISOR_LEDGER_VERSION, "plans": {}},
            closed_series,
            live_prices=live_prices,
            state=state,
            bootstrap=True,
        )
        ordered = sorted(
            out_ledger["plans"].values(),
            key=lambda p: (str(p.get("signal_date") or ""), str(p.get("plan_id") or "")),
            reverse=True,
        )
        return project_signal_plans(ordered), stats

    with file_lock(ledger_path):
        ledger, load_status = _try_load_advisor_ledger(ledger_path)
        if load_status == "error":
            mem_ledger, mem_stats = _scan_and_update_ledger(
                {"version": ADVISOR_LEDGER_VERSION, "plans": {}},
                closed_series,
                live_prices=live_prices,
                state=state,
                bootstrap=True,
            )
            ordered = sorted(
                mem_ledger["plans"].values(),
                key=lambda p: (str(p.get("signal_date") or ""), str(p.get("plan_id") or "")),
                reverse=True,
            )
            return project_signal_plans(ordered), {**mem_stats, "ledger_persist_skipped": True}

        bootstrap = load_status == "missing" or not (ledger.get("plans") or {})
        out_ledger, stats = _scan_and_update_ledger(
            ledger,
            closed_series,
            live_prices=live_prices,
            state=state,
            bootstrap=bootstrap,
        )

        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = ledger_path.with_name(f".{ledger_path.name}.{os.getpid()}.{uuid.uuid4().hex[:6]}.tmp")
        try:
            tmp_path.write_text(json.dumps(out_ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="")
            os.replace(tmp_path, ledger_path)
        except OSError:
            stats["ledger_persist_skipped"] = True
        finally:
            tmp_path.unlink(missing_ok=True)

        ordered = sorted(
            out_ledger["plans"].values(),
            key=lambda p: (str(p.get("signal_date") or ""), str(p.get("plan_id") or "")),
            reverse=True,
        )
        return project_signal_plans(ordered), stats


def try_load_public_signal_plans_from_ledger(
    data_dir: Path | str = DATA_DIR,
) -> tuple[list[dict[str, Any]] | None, dict[str, Any] | None]:
    """Load public signal plans from advisor ledger; return (None, None) when missing or corrupt."""
    path = Path(data_dir) / ADVISOR_LEDGER_FILENAME
    ledger, status = _try_load_advisor_ledger(path)
    if status != "ok":
        return None, None
    try:
        plans_map = ledger.get("plans") or {}
        ordered = sorted(
            plans_map.values(),
            key=lambda p: (str(p.get("signal_date") or ""), str(p.get("plan_id") or "")),
            reverse=True,
        )
        projected = project_signal_plans(ordered)
        return projected, compute_signal_plan_stats(projected)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return None, None


def main() -> None:
    parser = argparse.ArgumentParser(description="MacroCockpit Day-Level Signal Advisor")
    parser.add_argument("--data-dir", default=str(DATA_DIR), help="Data directory")
    args = parser.parse_args()
    plans, stats = update_signal_advisor(data_dir=args.data_dir, persist=True)
    print(json.dumps({"stats": stats, "count": len(plans), "latest": plans[:3]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
