"""Unified MacroCockpit state aggregator (quant + EQN + AIHOT + US equities + KOL + articles)."""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:
    requests = None  # type: ignore[assignment]

# Support both `python -m backend.signal_aggregator` and `python signal_aggregator.py`
_BACKEND_DIR = Path(__file__).resolve().parent
if str(_BACKEND_DIR.parent) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR.parent))

from backend.article_store import (
    KOL_ARTICLES_DIR,
    STATE_OUTPUT_PATH,
    get_allowed_article_dirs,
    parse_markdown_article,
    sort_and_balance_articles as _sort_and_balance_articles,
)
from backend.guardrails import (
    has_private_article_content,
    safe_article_filename,
    sanitize_public_value,
    scrub_quant_thesis,
    strip_emojis,
)
from backend.runtime import (
    BJT,
    COCKPIT_ROOT,
    DATA_DIR,
    UTC,
    atomic_write_json,
    file_lock,
    read_json_safe as _read_json,
    resolve_hermes_home,
    resolve_kol_dir,
    resolve_user_home,
)
from backend.correlation_engine import compute_cross_asset_correlations, generate_notion_viz_html
from backend.calendar_events import build_calendar_events, filter_calendar_events
from backend.fred_and_market import (
    QUANT_LATEST_PATH,
    build_macro_fred,
    fetch_us_equities,
)
from backend.kazike_engine import process_financial_signals
from backend.event_feed import update_event_feed
from backend.mobile_push import get_push_channels_status
from backend.signal_text import (
    classify_kol_topic,
    clean_business_text,
    dedup_summary,
    eqn_why_it_matters,
    extract_subtopics,
    extract_tickers,
    infer_signal_direction,
)
from backend.rss_sources import (
    build_opml_export,
    collect_curated_articles,
    collect_curated_rss_signals,
)
from backend.sec_edgar import collect_recent_sec_signals, fetch_sec_filings_map
from backend.stock_research import build_quick_thesis, build_stock_profiles
from backend.guru_sources import collect_guru_articles, collect_guru_signals
from backend.kol_intel_engine import (
    DEFAULT_SCORECARD_CACHE_PATH,
    build_kol_actionable_signals,
    build_kol_digest,
    build_kol_scorecards,
    select_quality_kol_articles,
)
from backend.market_synthesis import build_market_synthesis
from backend.market_quotes import fetch_crypto_quotes, fetch_fx_snapshot, fetch_hybrid_perp_quotes
from backend.forum_sentiment import (
    build_sentiment_divergence,
    collect_forum_pulse,
    collect_public_discussion_trends,
    trim_forum_sentiment,
)
from backend.source_matrix import build_asset_source_matrix
from backend.periodic_report import generate_periodic_decision_report
from backend.signal_advisor import update_signal_advisor

AIHOT_CACHE_PATH = DATA_DIR / "aihot_cache.json"

_USER_HOME = resolve_user_home()
_HERMES_HOME = resolve_hermes_home(_USER_HOME)
DECISION_BRIEF_PATH = Path(
    os.environ.get("MACROCOCKPIT_DECISION_BRIEF_PATH")
    or (_HERMES_HOME / "scripts" / "news" / "outputs" / "decision" / "latest_brief.json")
)
EQN_CANDIDATE_PATHS = (
    _HERMES_HOME / "scripts" / "news" / "outputs" / "eqn" / "latest.json",
    _HERMES_HOME / "scripts" / "news" / "outputs" / "eqn" / "latest_events.json",
)
KOL_ROOT = resolve_kol_dir(_HERMES_HOME)
AIHOT_ACTOR_ID = (os.environ.get("AIHOT_ACTOR_ID") or "macrocockpit-agent-v2").strip()
AIHOT_UA = f"aihot-skill/2.0.0 (+https://aihot.news/aihot-skill/) aihot-actor/{AIHOT_ACTOR_ID}"
AIHOT_LATEST_URL = "https://aihot.news/api/v1/agent/latest?limit=20"
AIHOT_HOT_URL = "https://aihot.news/api/v1/agent/hot"

POSITION_SPEC: tuple[tuple[str, str, str], ...] = (
    ("S1", "btc_trend", "核心趋势仓 (BTC)"),
    ("S2", "gold_hedge", "黄金避险仓"),
    ("S3", "bear_basket", "熊市配对对冲仓"),
    ("S4", "crash_bounce", "超跌反弹短打仓"),
)


def _scrub_forbidden_text(text: str, *, preserve_newlines: bool = True) -> str:
    return scrub_quant_thesis(text, preserve_newlines=preserve_newlines)


def _sanitize_state_value(val: Any, key_name: str = "") -> Any:
    return sanitize_public_value(val, key_name=key_name, scrub_text=scrub_quant_thesis)


def _parse_iso_to_bjt_str(ts: str | None) -> str:
    if not ts:
        return ""
    raw = str(ts).strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(BJT).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return raw[:16]


_ASHARE_MATCH_RE = re.compile(
    r"(A股|沪指|深成指|创业板|科创板|上证|恒指|港股|中国证监会|北向|融资保证金|比亚迪|华为|宁德时代|茅台|中芯|寒武纪)"
)


def _ensure_signal_topics(sig: dict[str, Any]) -> dict[str, Any]:
    out = dict(sig)
    cat = str(out.get("category") or "macro").lower()
    txt = f"{out.get('title') or ''} {out.get('summary') or ''}"
    if cat != "ashare" and cat != "crypto" and _ASHARE_MATCH_RE.search(txt):
        if not any(t in ("NVDA", "SPY", "QQQ", "AAPL", "MSFT", "TSLA") for t in (out.get("tickers") or [])):
            cat = "ashare"
            out["category"] = "ashare"
    existing_topics = [str(t).strip() for t in (out.get("topics") or []) if str(t).strip()]
    if existing_topics:
        out["topics"] = existing_topics[:5]
    else:
        out["topics"] = extract_subtopics(txt, cat)
    return out


def _evaluate_cloud_risk_inputs(
    macro_fred: list[dict[str, Any]] | None,
    us_equities: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Synthesize conservative risk_gate & sleeve baselines from live macro/equity feeds when local quant files are absent."""
    by_id = {str(m.get("id") or ""): m for m in (macro_fred or []) if isinstance(m, dict)}
    vix_item = by_id.get("vix") or {}
    vix_val = float(vix_item["raw_value"]) if vix_item.get("raw_value") is not None else None
    sp_note = str((by_id.get("sp500") or {}).get("note") or "")
    indices = (us_equities or {}).get("indices") or []
    qqq_chg = next((float(x["change_pct"]) for x in indices if x.get("symbol") == "QQQ" and x.get("change_pct") is not None), None)

    reasons: list[str] = []
    if vix_val is not None and vix_val >= 30.0:
        light, label, cap = "RED", "红灯 · 防御控险", 0.0
        reasons.append(f"VIX 恐慌指数升至 {vix_val:.2f}（>=30 警戒线），多头仓位上限归零")
    elif (vix_val is not None and vix_val >= 22.0) or (qqq_chg is not None and qqq_chg <= -2.0) or ("低于均线" in sp_note):
        light, label, cap = "YELLOW", "黄灯 · 谨慎控险", 0.5
        if vix_val is not None:
            reasons.append(f"VIX 录得 {vix_val:.2f}，波动中枢偏高（触发半仓降档）")
        if qqq_chg is not None and qqq_chg <= -2.0:
            reasons.append(f"纳指100 ETF 单日波动 {qqq_chg:+.2f}%，短线防御控险")
        if "低于均线" in sp_note:
            reasons.append(f"标普500：{sp_note}")
    else:
        light, label, cap = "GREEN", "绿灯 · 顺势运行", 1.0
        if vix_val is not None:
            reasons.append(f"VIX 处于 {vix_val:.2f} 平稳区间")
        if sp_note:
            reasons.append(f"标普500：{sp_note}")
        if not reasons:
            reasons.append("云端跨市场宏观与美股基准正常，维持标准预算上限")

    synthesized_sleeves = {
        "S1": {"baseline_weight": 0.30, "recommended_weight": 0.30 * cap, "action": "跟随趋势预算" if cap > 0 else "防御降零"},
        "S2": {"baseline_weight": 0.20, "recommended_weight": 0.20, "action": "保留避险底仓"},
        "S3": {"baseline_weight": 0.20 if light == "RED" else 0.0, "recommended_weight": 0.20 if light == "RED" else 0.0, "action": "红灯防御配对" if light == "RED" else "空仓观望"},
        "S4": {"baseline_weight": 0.0, "recommended_weight": 0.0, "action": "空仓观望"},
    }
    synthesized_quant = {"sleeves": synthesized_sleeves}
    synthesized_brief = {
        "risk_gate": {"light": light, "label": label, "long_risk_cap": cap, "reasons": reasons},
        "sleeves": synthesized_sleeves,
    }
    return synthesized_quant, synthesized_brief


def _build_positions_and_risk_gate(
    quant_snap: dict[str, Any],
    decision_brief: dict[str, Any],
    *,
    macro_fred: list[dict[str, Any]] | None = None,
    us_equities: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not quant_snap and not decision_brief:
        quant_snap, decision_brief = _evaluate_cloud_risk_inputs(macro_fred, us_equities)
    default_capital = 100000.0
    q_sleeves = quant_snap.get("sleeves") or {}
    d_sleeves = decision_brief.get("sleeves") or {}
    d_risk = decision_brief.get("risk_gate") or {}

    light = str(d_risk.get("light") or "YELLOW").upper()
    if light not in ("GREEN", "YELLOW", "RED"):
        light = "YELLOW"
    cap = float(d_risk.get("long_risk_cap") if d_risk.get("long_risk_cap") is not None else 1.0)
    label = clean_business_text(str(d_risk.get("label") or "黄灯 · 谨慎控险"))

    raw_reasons = [str(r) for r in (d_risk.get("reasons") or []) if r]
    if d_risk.get("flush_relief_note"):
        raw_reasons.append(str(d_risk["flush_relief_note"]))
    reasons = [clean_business_text(r) for r in raw_reasons if r]
    if not reasons:
        reasons = ["按均线趋势与宏观风险门控综合生成"]

    positions: list[dict[str, Any]] = []
    for sleeve_key, pos_id, pos_name in POSITION_SPEC:
        q_sl = q_sleeves.get(sleeve_key) or {}
        d_sl = d_sleeves.get(sleeve_key) or {}

        base_w = q_sl.get("baseline_weight")
        if base_w is None:
            base_w = d_sl.get("baseline_weight")
        base_pct = round(max(0.0, float(base_w or 0.0) * 100.0), 2)

        rec_w = d_sl.get("recommended_weight")
        if rec_w is not None:
            rec_pct = round(max(0.0, float(rec_w) * 100.0), 2)
        elif sleeve_key in ("S1", "S4"):
            rec_pct = round(base_pct * cap, 2)
        else:
            rec_pct = base_pct

        # Hard invariant: baseline_pct == 0 => recommended_pct == 0
        if base_pct <= 0.0:
            base_pct = 0.0
            rec_pct = 0.0

        state_str = "持仓运行" if rec_pct > 0.0 else "空仓观望"
        notional = round(default_capital * (rec_pct / 100.0), 2)

        action_txt = str(d_sl.get("action") or "").strip()
        reason_txt = str(d_sl.get("reason") or q_sl.get("reason") or "").strip()
        if action_txt and reason_txt and action_txt not in reason_txt:
            note = f"{action_txt}；{reason_txt}"
        else:
            note = action_txt or reason_txt or state_str
        note = clean_business_text(note)

        positions.append(
            {
                "id": pos_id,
                "name": pos_name,
                "state": state_str,
                "baseline_pct": base_pct,
                "recommended_pct": rec_pct,
                "est_notional_usd": notional,
                "note": note,
            }
        )

    pos_map = {p["id"]: p for p in positions}
    dir_long = pos_map["btc_trend"]["recommended_pct"] + pos_map["crash_bounce"]["recommended_pct"]
    gold_pct = pos_map["gold_hedge"]["recommended_pct"]
    bear_pct = pos_map["bear_basket"]["recommended_pct"]

    s3_legs = (
        (decision_brief.get("summary_exposure") or {}).get("s3_paired_legs")
        or (q_sleeves.get("S3") or {}).get("paired_legs")
        or {}
    )
    if bear_pct > 0 and s3_legs:
        lb = float(s3_legs.get("long_btc") or 0.0) * 100.0
        sa = float(s3_legs.get("short_liquid_alts") or 0.0) * 100.0
        bear_desc = f"熊市配对 +{lb:.0f}% BTC / {sa:.0f}% 山寨"
    elif bear_pct > 0:
        bear_desc = f"熊市配对 {bear_pct:.0f}%"
    else:
        bear_desc = "熊市配对 0%"

    summary_exposure = f"方向性多头 {dir_long:.0f}% · 黄金避险 {gold_pct:.0f}% · {bear_desc}"

    risk_gate = {
        "light": light,
        "label": label,
        "long_risk_cap": round(cap, 2),
        "summary_exposure": summary_exposure,
        "reasons": reasons,
    }
    return (risk_gate, positions)


def _collect_eqn_signals(decision_brief: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    seen_nos: set[Any] = set()

    for cand_path in EQN_CANDIDATE_PATHS:
        data = _read_json(cand_path)
        if isinstance(data, dict) and isinstance(data.get("events"), list):
            for ev in data["events"]:
                no = ev.get("no")
                if no is not None and no not in seen_nos:
                    seen_nos.add(no)
                    events.append(ev)

    for ev in decision_brief.get("eqn_recent_24h") or []:
        no = ev.get("no")
        if no is not None and no not in seen_nos:
            seen_nos.add(no)
            events.append(ev)

    signals: list[dict[str, Any]] = []
    for ev in events[:24]:
        headline = clean_business_text(str(ev.get("headline") or "").lstrip("* "))
        if not headline:
            continue
        overview = clean_business_text(str(ev.get("overview_zh") or ""))
        zh_raw = clean_business_text(str(ev.get("zh") or "").lstrip("* "))
        raw_sum = overview or ("" if zh_raw == headline else zh_raw)
        if len(raw_sum) > 280:
            raw_sum = raw_sum[:277].rstrip() + "..."
        summary = dedup_summary(headline, raw_sum)

        domain = str(ev.get("domain") or "").lower()
        tickers = extract_tickers(f"{headline} {ev.get('zh', '')} {ev.get('en', '')}")
        if "crypto" in domain or any(t in ("BTC", "ETH") for t in tickers):
            category = "crypto"
        elif domain == "tradfi" and any(t in ("NVDA", "QQQ", "SPY", "ORCL", "CRWD", "ZS", "SKHY", "AMD", "TSM") for t in tickers):
            category = "equities"
        else:
            category = "macro"

        pol = str(ev.get("polarity") or "").lower()
        direction = "bearish" if pol == "adverse" else ("bullish" if pol == "supportive" else "neutral")
        tier = str(ev.get("tier") or "")
        score = int(ev.get("score") or 0)
        if category == "crypto":
            importance = "高" if (tier == "Tier-S" and score >= 8) else "中"
        else:
            importance = "高" if (tier == "Tier-S" or score >= 7) else "中"

        signals.append(
            {
                "id": f"eqn-{ev.get('no', len(signals) + 1)}",
                "source": "方程式快讯",
                "category": category,
                "direction": direction,
                "importance": importance,
                "time_bjt": _parse_iso_to_bjt_str(ev.get("time")),
                "title": headline,
                "summary": summary,
                "why_it_matters": eqn_why_it_matters(
                    f"{ev.get('headline', '')} {ev.get('overview_zh', '')}", category, direction,
                ),
                "tickers": tickers,
                "url": str(ev.get("url") or ev.get("source_url") or ""),
            }
        )
    return signals


def _normalize_aihot_time(mmdd_hhmm: str) -> str:
    m = re.search(r"(\d{2})-(\d{2})\s+(\d{2}:\d{2})", mmdd_hhmm)
    if not m:
        return datetime.now(BJT).strftime("%Y-%m-%d %H:%M")
    now_bjt = datetime.now(BJT)
    mm, dd, hhmm = int(m.group(1)), int(m.group(2)), m.group(3)
    yr = now_bjt.year
    if mm == 12 and now_bjt.month == 1:
        yr -= 1
    return f"{yr:04d}-{mm:02d}-{dd:02d} {hhmm}"


def _strip_prompt_safety_wrappers(md_text: str) -> str:
    lines = []
    in_block = False
    has_marker = "［AIHOT 不可信外部资料开始］" in md_text
    for line in md_text.splitlines():
        s = line.strip()
        if "［AIHOT 不可信外部资料开始］" in s:
            in_block = True
            continue
        if "［AIHOT 不可信外部资料结束］" in s:
            break
        if has_marker and not in_block:
            continue
        lines.append(line)
    return "\n".join(lines) if lines else md_text


def _parse_aihot_latest_md(md_text: str) -> list[dict[str, Any]]:
    body = _strip_prompt_safety_wrappers(md_text)
    blocks = re.split(r"\n(?=\d+\.\s+\[)", "\n" + body.strip())
    items: list[dict[str, Any]] = []
    for blk in blocks:
        blk = blk.strip()
        if not blk:
            continue
        head_m = re.match(r"^\d+\.\s+\[([^\]]+)\]\((https?://[^)]+)\)", blk)
        if not head_m:
            continue
        title = clean_business_text(head_m.group(1))
        aihot_url = head_m.group(2).strip()
        item_id = aihot_url.rstrip("/").split("/")[-1]

        meta_line = ""
        summary = ""
        reason = ""
        orig_url = ""
        for ln in blk.splitlines()[1:]:
            s = ln.strip()
            if "发布于" in s and not meta_line:
                meta_line = s
            elif s.startswith("摘要："):
                summary = clean_business_text(s[3:])
            elif s.startswith("推荐理由："):
                reason = clean_business_text(s[5:])
            elif s.startswith("原文："):
                cand = s[3:].strip()
                if cand.startswith(("http://", "https://")):
                    orig_url = cand

        time_bjt = _normalize_aihot_time(meta_line)
        combined = f"{title} {summary} {reason}"
        high_kw = ("Anthropic", "OpenAI", "ChatGPT", "Google", "Gemini", "Claude", "Apple", "Nvidia", "METR")
        imp = "高" if any(k.lower() in combined.lower() for k in high_kw) else "中"

        items.append(
            {
                "id": f"aihot-{item_id}",
                "source": "AIHOT精选",
                "category": "ai",
                "direction": infer_signal_direction(combined, aihot=True),
                "importance": imp,
                "time_bjt": time_bjt,
                "title": title,
                "summary": summary or title,
                "why_it_matters": reason or "前沿 AI 模型与工程生态动态，影响科技股与应用层估值预期。",
                "tickers": extract_tickers(combined),
                "url": orig_url or aihot_url,
                "_aihot_url": aihot_url,
            }
        )
    return items


def _parse_aihot_hot_md(md_text: str) -> list[dict[str, Any]]:
    body = _strip_prompt_safety_wrappers(md_text)
    blocks = re.split(r"\n(?=第\s*\d+\s*名：)", "\n" + body.strip())
    items: list[dict[str, Any]] = []
    for blk in blocks:
        blk = blk.strip()
        if not blk:
            continue
        head_m = re.match(r"^第\s*(\d+)\s*名：\[([^\]]+)\]\((https?://[^)]+)\)", blk)
        if not head_m:
            continue
        rank = int(head_m.group(1))
        title = clean_business_text(head_m.group(2))
        aihot_url = head_m.group(3).strip()
        item_id = aihot_url.rstrip("/").split("/")[-1]

        sources_line = ""
        story_url = ""
        for ln in blk.splitlines()[1:]:
            s = ln.strip()
            if s.startswith("信源："):
                sources_line = clean_business_text(s)
            elif s.startswith("来龙去脉："):
                cand = s[5:].strip()
                if cand.startswith(("http://", "https://")):
                    story_url = cand

        time_bjt = _normalize_aihot_time(sources_line)
        summary = sources_line.split("·")[0].strip() if sources_line else f"AIHOT 热点榜第 {rank} 名"
        items.append(
            {
                "id": f"aihot-hot-{item_id}",
                "source": "AIHOT精选",
                "category": "ai",
                "direction": infer_signal_direction(title, aihot=True),
                "importance": "高" if rank <= 6 else "中",
                "time_bjt": time_bjt,
                "title": title,
                "summary": summary,
                "why_it_matters": f"全网多信源同步讨论热点（热榜第 {rank} 名），直接映射 AI 资本开支与监管风向。",
                "tickers": extract_tickers(title),
                "url": aihot_url or story_url,
                "_aihot_url": aihot_url,
            }
        )
    return items


def _collect_aihot_signals() -> list[dict[str, Any]]:
    headers = {"User-Agent": AIHOT_UA}
    latest_items: list[dict[str, Any]] = []
    hot_items: list[dict[str, Any]] = []

    try:
        r_latest = requests.get(AIHOT_LATEST_URL, headers=headers, timeout=12)
        if r_latest.status_code == 200 and r_latest.text.strip():
            latest_items = _parse_aihot_latest_md(r_latest.text)
    except Exception:
        latest_items = []

    try:
        r_hot = requests.get(AIHOT_HOT_URL, headers=headers, timeout=10)
        if r_hot.status_code == 200 and r_hot.text.strip():
            hot_items = _parse_aihot_hot_md(r_hot.text)
    except Exception:
        hot_items = []

    merged: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()

    for it in latest_items + hot_items:
        aihot_u = str(it.pop("_aihot_url", "") or it.get("url") or "")
        norm_title = re.sub(r"\W+", "", it["title"].lower())[:24]
        if (aihot_u and aihot_u in seen_urls) or (norm_title and norm_title in seen_titles):
            continue
        if aihot_u:
            seen_urls.add(aihot_u)
        if norm_title:
            seen_titles.add(norm_title)
        merged.append(it)

    if merged:
        atomic_write_json(
            AIHOT_CACHE_PATH,
            {
                "updated_at_bjt": datetime.now(BJT).strftime("%Y-%m-%d %H:%M:%S+08:00"),
                "items": merged,
            },
        )
        return merged

    cached = _read_json(AIHOT_CACHE_PATH, {}) or {}
    return [dict(x) for x in (cached.get("items") or []) if isinstance(x, dict)]


def _collect_equity_signals(us_equities: dict[str, Any]) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    now_bjt = datetime.now(BJT).strftime("%Y-%m-%d %H:%M")
    indices = us_equities.get("indices") or []
    mag7_ai = us_equities.get("mag7_and_ai") or []

    idx_map = {x["symbol"]: x for x in indices}
    qqq = idx_map.get("QQQ")
    spy = idx_map.get("SPY")
    if qqq or spy:
        q_chg = float((qqq or spy)["change_pct"])
        direction = "bearish" if q_chg <= -0.8 else ("bullish" if q_chg >= 0.8 else "neutral")
        imp = "高" if abs(q_chg) >= 2.0 else "中"
        signals.append(
            {
                "id": "eq-index-wrap",
                "source": "美股盘面",
                "category": "equities",
                "direction": direction,
                "importance": imp,
                "time_bjt": now_bjt,
                "title": us_equities.get("summary") or "美股主要指数最新盘面",
                "summary": f"基准行情截止 {us_equities.get('as_of', '')}："
                + "；".join(f"{x['name']} {x['price']} ({x['change_pct']:+.2f}%)" for x in indices),
                "why_it_matters": "美股科技与指数风向直接决定全球风险资产流动性与加密市场情绪锚定。",
                "tickers": [x["symbol"].lstrip(".") for x in indices],
                "url": "",
            }
        )

    movers = sorted(mag7_ai, key=lambda x: abs(float(x.get("change_pct") or 0.0)), reverse=True)
    big_movers = [m for m in movers if abs(float(m.get("change_pct") or 0.0)) >= 4.0][:4]
    if big_movers:
        worst = min(big_movers, key=lambda x: x["change_pct"])
        best = max(big_movers, key=lambda x: x["change_pct"])
        avg_chg = sum(float(m["change_pct"]) for m in big_movers) / len(big_movers)
        direction = "bearish" if avg_chg < 0 else "bullish"
        lead = worst if abs(worst["change_pct"]) >= abs(best["change_pct"]) else best
        signals.append(
            {
                "id": "eq-ai-movers",
                "source": "美股盘面",
                "category": "equities",
                "direction": direction,
                "importance": "高" if abs(lead["change_pct"]) >= 6.0 else "中",
                "time_bjt": now_bjt,
                "title": f"七巨头与AI算力异动：{lead['name']} ({lead['symbol']}) {lead['change_pct']:+.2f}%",
                "summary": "显著波动标的："
                + "、".join(f"{m['name']}({m['symbol']}) ${m['price']:.2f} ({m['change_pct']:+.2f}%)" for m in big_movers),
                "why_it_matters": "AI 硬件与算力链估值重构直接影响科技股波动率（VIX）及资金风险预算。",
                "tickers": [m["symbol"] for m in big_movers],
                "url": "",
            }
        )
    return signals


def _find_kol_article_and_url(handle: str) -> tuple[str, str]:
    """Locate matching longform article filename and latest tweet URL for a KOL handle."""
    h_low = handle.lower()
    article_file = ""
    if KOL_ARTICLES_DIR.exists():
        for p in sorted(KOL_ARTICLES_DIR.glob("*.md"), reverse=True):
            if p.name.lower().startswith(f"{h_low}_"):
                article_file = p.name
                break

    url = ""
    if article_file:
        try:
            txt = (KOL_ARTICLES_DIR / article_file).read_text(encoding="utf-8", errors="replace")[:1500]
            m = re.search(r"https://(?:x|twitter)\.com/[A-Za-z0-9_]+/status/\d+", txt)
            if m:
                url = m.group(0)
        except OSError:
            pass

    if not url and KOL_ROOT.exists():
        for sub in ("macro", "crypto", "us-stocks", "investing", "quant"):
            sd = KOL_ROOT / sub
            if not sd.exists():
                continue
            matches = sorted(sd.glob(f"*_{handle}.md"), reverse=True)
            if not matches:
                matches = sorted(
                    [p for p in sd.glob("*.md") if p.name.lower().endswith(f"_{h_low}.md")],
                    reverse=True,
                )
            if matches:
                try:
                    txt = matches[0].read_text(encoding="utf-8", errors="replace")[:3000]
                    m = re.search(r"https://(?:x|twitter)\.com/[A-Za-z0-9_]+/status/\d+", txt)
                    if m:
                        url = m.group(0)
                        break
                except OSError:
                    pass
                break
    return (article_file, url or f"https://x.com/{handle}")


def _clean_kol_snippet(snippet: str) -> tuple[str, str]:
    s = html.unescape(snippet or "").strip()
    s = re.sub(r"^RT\s+@[A-Za-z0-9_]+:\s*", "", s).strip()
    s = re.sub(r"https?://\S+", "", s).strip()
    if s.startswith("【X长文】"):
        m = re.match(r"【X长文】《([^》]+)》(?:核心观点：)?(.*)$", s)
        if m:
            headline = m.group(1).strip()
            takeaway = m.group(2).strip() or headline
            return (clean_business_text(headline), clean_business_text(takeaway))
        m2 = re.match(r"【X长文】([^：]+)[：:]\s*(.*)$", s)
        if m2:
            return (clean_business_text(m2.group(1)), clean_business_text(m2.group(2) or m2.group(1)))
    short = s[:54].rstrip("，。；： ") + ("..." if len(s) > 54 else "")
    return (clean_business_text(short), clean_business_text(s))


def _collect_kol_digest(decision_brief: dict[str, Any]) -> list[dict[str, Any]]:
    kol_sum = decision_brief.get("kol_summary") or {}
    cards: list[dict[str, Any]] = []

    groups = (
        ("tier1_highlights", "宏观与风控"),
        ("crypto_highlights", "加密与链上"),
    )
    for key, group_label in groups:
        for row in kol_sum.get(key) or []:
            handle = str(row.get("handle") or "").strip()
            if not handle:
                continue
            stance = str(row.get("bias") or "中性")
            if stance not in ("偏空", "偏多", "中性"):
                stance = "中性"
            score = round(float(row.get("score") or 0.0), 3)
            date_str = str(row.get("day") or "")
            snippet = str(row.get("snippet") or "")
            headline, takeaway = _clean_kol_snippet(snippet)
            article_file, url = _find_kol_article_and_url(handle)
            topic = classify_kol_topic(headline, takeaway, group_label, decision_brief=True)
            cards.append(
                {
                    "handle": handle,
                    "group": group_label,
                    "topic": topic,
                    "stance": stance,
                    "score": score,
                    "date": date_str,
                    "headline": headline or f"@{handle} 最新观点",
                    "takeaway": takeaway or headline or "暂无摘要",
                    "article_file": article_file,
                    "url": url,
                }
            )
    return cards


def _collect_articles(kol_scorecards: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    articles: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for folder, src_label in get_allowed_article_dirs():
        if not folder.exists():
            continue
        if src_label == "KOL深度长文" and select_quality_kol_articles is not None:
            try:
                quality_kol = select_quality_kol_articles(
                    folder,
                    kol_dir=KOL_ROOT,
                    scorecards=kol_scorecards,
                    limit=40,
                )
            except Exception:
                quality_kol = []
            for art in quality_kol:
                if not isinstance(art, dict):
                    continue
                fname = safe_article_filename(art.get("filename"))
                if not fname or fname in seen_names or "异常告警" in fname:
                    continue
                if has_private_article_content(art):
                    continue
                clean_art = dict(art)
                clean_art["title"] = clean_business_text(str(clean_art.get("title") or fname))
                clean_art["excerpt"] = clean_business_text(str(clean_art.get("excerpt") or clean_art["title"]))
                clean_art["body_md"] = _scrub_forbidden_text(str(clean_art.get("body_md") or ""), preserve_newlines=True)
                if clean_art.get("publisher"):
                    clean_art["publisher"] = clean_business_text(str(clean_art["publisher"]))
                if clean_art.get("author_role"):
                    clean_art["author_role"] = clean_business_text(str(clean_art["author_role"]))
                seen_names.add(fname)
                articles.append(clean_art)
            continue

        files = sorted(folder.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
        for p in files:
            if p.name in seen_names or "异常告警" in p.name:
                continue
            art = parse_markdown_article(p, src_label)
            if art:
                seen_names.add(p.name)
                articles.append(art)
    return _sort_and_balance_articles(articles)


def _build_strategy_targets(
    positions: list[dict[str, Any]],
    risk_gate: dict[str, Any],
    quant_snap: dict[str, Any],
    decision_brief: dict[str, Any],
) -> dict[str, Any]:
    sample_cap = 100000.0
    q_sleeves = quant_snap.get("sleeves") or {}
    s3_legs = (
        (decision_brief.get("summary_exposure") or {}).get("s3_paired_legs")
        or (q_sleeves.get("S3") or {}).get("paired_legs")
        or {}
    )
    targets_list: list[dict[str, Any]] = []
    for p in positions:
        pid = p["id"]
        rec_pct = float(p.get("recommended_pct") or 0.0)
        legs: list[dict[str, Any]] = []
        if pid == "bear_basket":
            if rec_pct > 0 and s3_legs:
                lb = round(float(s3_legs.get("long_btc") or 0.35) * 100.0, 2)
                sa = round(float(s3_legs.get("short_liquid_alts") or -0.30) * 100.0, 2)
                n_alts = int(s3_legs.get("n_liquid_alts") or 41)
            elif rec_pct > 0:
                lb = rec_pct
                sa = round(-rec_pct * 30.0 / 35.0, 2)
                n_alts = 41
            else:
                lb = 0.0
                sa = 0.0
                n_alts = 0
            legs = [
                {
                    "instrument_id": "BTC",
                    "symbol": "BTC",
                    "side": "long",
                    "target_pct": lb,
                    "sample_notional_usd": round(sample_cap * abs(lb) / 100.0, 2),
                    "est_notional_usd": round(sample_cap * abs(lb) / 100.0, 2),
                    "label": f"+{lb:g}% BTC",
                },
                {
                    "instrument_id": "ALT_BASKET",
                    "symbol": "ALT_BASKET",
                    "side": "short",
                    "target_pct": sa,
                    "sample_notional_usd": round(sample_cap * abs(sa) / 100.0, 2),
                    "est_notional_usd": round(sample_cap * abs(sa) / 100.0, 2),
                    "n_assets": n_alts,
                    "label": f"{sa:g}% ALT_BASKET",
                },
            ]
            net_pct = round(lb + sa, 2)
            gross_pct = round(abs(lb) + abs(sa), 2)
            legs_summary = f"+{lb:g}% BTC / {sa:g}% ALT_BASKET" if rec_pct > 0 else "0%"
        else:
            inst_map = {"btc_trend": "BTC", "gold_hedge": "XAU", "crash_bounce": "CRYPTO_BOUNCE"}
            inst = inst_map.get(pid, pid.upper() or "ASSET")
            legs = [
                {
                    "instrument_id": inst,
                    "symbol": inst,
                    "side": "long",
                    "target_pct": rec_pct,
                    "sample_notional_usd": round(sample_cap * rec_pct / 100.0, 2),
                    "est_notional_usd": round(sample_cap * rec_pct / 100.0, 2),
                    "label": f"+{rec_pct:g}% {inst}" if rec_pct > 0 else f"0% {inst}",
                }
            ]
            net_pct = round(rec_pct, 2)
            gross_pct = round(abs(rec_pct), 2)
            legs_summary = legs[0]["label"]

        targets_list.append(
            {
                "id": pid,
                "name": p["name"],
                "state": p["state"],
                "baseline_pct": p["baseline_pct"],
                "target_pct": rec_pct,
                "recommended_pct": rec_pct,
                "sample_notional_usd": p["est_notional_usd"],
                "est_notional_usd": p["est_notional_usd"],
                "net_target_pct": net_pct,
                "gross_target_pct": gross_pct,
                "legs_summary": legs_summary,
                "legs": legs,
                "note": str(p.get("note") or ""),
            }
        )
    by_id = {t["id"]: t for t in targets_list}
    return {
        "sample_capital_usd": int(sample_cap),
        "risk_light": risk_gate["light"],
        "long_risk_cap": risk_gate["long_risk_cap"],
        "targets": targets_list,
        "by_id": by_id,
    }


def generate_cockpit_state(
    *,
    refresh_fred: bool = False,
    output_path: Path = STATE_OUTPUT_PATH,
) -> dict[str, Any]:
    """Assemble and persist public cockpit_state.json."""
    quant_snap = _read_json(QUANT_LATEST_PATH, {}) or {}
    decision_brief = _read_json(DECISION_BRIEF_PATH, {}) or {}

    macro_fred = build_macro_fred(quant_snap, refresh_fred=refresh_fred)
    us_equities = fetch_us_equities()
    risk_gate, positions = _build_positions_and_risk_gate(
        quant_snap,
        decision_brief,
        macro_fred=macro_fred,
        us_equities=us_equities,
    )
    strategy_targets = _build_strategy_targets(positions, risk_gate, quant_snap, decision_brief)

    eqn_signals = _collect_eqn_signals(decision_brief)
    aihot_signals = _collect_aihot_signals()
    rss_signals = collect_curated_rss_signals()
    try:
        filings_map = fetch_sec_filings_map()
        sec_signals = collect_recent_sec_signals(filings_map)
    except Exception:
        sec_signals = []
    eq_signals = _collect_equity_signals(us_equities)

    # (a) Guru longform articles ('大佬分享') & signals with exception isolation
    guru_articles: list[dict[str, Any]] = []
    guru_signals: list[dict[str, Any]] = []
    if collect_guru_articles is not None:
        try:
            guru_articles = collect_guru_articles(articles_dir=COCKPIT_ROOT / "articles")
        except Exception:
            guru_articles = []
    if collect_guru_signals is not None:
        try:
            guru_signals = collect_guru_signals(articles=guru_articles or None)
        except Exception:
            guru_signals = []

    # (b) KOL historical scorecards, readable digest (up to 36), & high-reliability actionable signals
    scorecard_cache_path = (
        DEFAULT_SCORECARD_CACHE_PATH
        if output_path == STATE_OUTPUT_PATH
        else output_path.with_name("kol_scorecard_cache.json")
    )
    kol_scorecards: list[dict[str, Any]] = []
    kol_digest: list[dict[str, Any]] = []
    kol_actionable_signals: list[dict[str, Any]] = []
    if build_kol_scorecards is not None and build_kol_digest is not None and build_kol_actionable_signals is not None:
        try:
            if KOL_ROOT.exists():
                kol_scorecards = build_kol_scorecards(
                    kol_dir=KOL_ROOT,
                    cache_path=scorecard_cache_path,
                    force_refresh=True,
                )
                kol_digest = build_kol_digest(
                    kol_dir=KOL_ROOT,
                    scorecards=kol_scorecards,
                    cache_path=scorecard_cache_path,
                    limit=36,
                )
            else:
                kol_scorecards = build_kol_scorecards(
                    kol_dir=KOL_ROOT,
                    cache_path=DEFAULT_SCORECARD_CACHE_PATH,
                    force_refresh=False,
                )
        except Exception:
            kol_scorecards = []
            kol_digest = []

        if not kol_digest:
            kol_digest = _collect_kol_digest(decision_brief)[:36]
        try:
            kol_actionable_signals = build_kol_actionable_signals(
                kol_digest_items=kol_digest,
                scorecards=kol_scorecards,
                min_reliability_score=68.0,
                limit=16,
            )
        except Exception:
            kol_actionable_signals = []
    else:
        kol_digest = _collect_kol_digest(decision_brief)[:36]

    # (c) Merge KOL actionable signals + Guru signals into Kazike signal pipeline
    raw_signals = [
        _ensure_signal_topics(s)
        for s in (
            eqn_signals
            + aihot_signals
            + rss_signals
            + sec_signals
            + eq_signals
            + kol_actionable_signals
            + guru_signals
        )
    ]
    all_signals, kazike_stats = process_financial_signals(raw_signals)
    signal_candidates = kazike_stats.pop("candidates", [])
    pre_cluster_items = kazike_stats.pop("pre_cluster_items", [])
    event_cards: list[dict[str, Any]] | None = None
    event_stats: dict[str, Any] = {}
    if update_event_feed is not None:
        try:
            event_cards, event_stats = update_event_feed(pre_cluster_items, data_dir=output_path.parent)
            if event_stats.get("ledger_persist_skipped"):
                event_cards = None
        except Exception:
            event_cards, event_stats = None, {}
    if event_cards is None and output_path.is_file():
        try:
            prior_obj = json.loads(output_path.read_text(encoding="utf-8"))
            if isinstance(prior_obj, dict) and isinstance(prior_obj.get("event_cards"), list):
                from backend.event_feed import project_event_cards

                event_cards = project_event_cards(prior_obj.get("event_cards"))
                if isinstance(prior_obj.get("event_stats"), dict) and not event_stats:
                    event_stats = dict(prior_obj["event_stats"])
        except (OSError, ValueError, TypeError):
            pass
    for sig_list in (all_signals, signal_candidates):
        for sig in sig_list:
            if isinstance(sig, dict):
                sig["title"] = strip_emojis(str(sig.get("title") or ""))
                sig["summary"] = dedup_summary(sig["title"], str(sig.get("summary") or ""))
    correlations = compute_cross_asset_correlations()
    stock_profiles = build_stock_profiles(force_refresh=False, risk_gate=risk_gate)
    calendar_events = filter_calendar_events(
        build_calendar_events(stock_profiles),
        days_ahead=120,
    )

    fx_snapshot: dict[str, Any] = {
        "status": "unavailable",
        "rate": None,
        "basis": None,
        "label": "人民币估值不可用",
    }
    if fetch_fx_snapshot is not None:
        try:
            fx_res = fetch_fx_snapshot()
            if isinstance(fx_res, dict) and fx_res:
                fx_snapshot = fx_res
        except Exception:
            pass

    crypto_quotes: dict[str, Any] = {}
    if fetch_crypto_quotes is not None:
        try:
            cq_res = fetch_crypto_quotes()
            if isinstance(cq_res, dict):
                crypto_quotes = dict(cq_res)
        except Exception:
            pass
    if fetch_hybrid_perp_quotes is not None:
        try:
            hq_res = fetch_hybrid_perp_quotes()
            if isinstance(hq_res, dict) and hq_res:
                quotes_map = dict(crypto_quotes.get("quotes") or {})
                swap_map = dict(crypto_quotes.get("swap") or {})
                proxy_map = dict(crypto_quotes.get("spot_proxies") or {})
                for sym_k, entry in hq_res.items():
                    if isinstance(entry, dict):
                        sk = str(sym_k).upper()
                        quotes_map[sk] = dict(entry)
                        if entry.get("kind") == "spot_proxy" or entry.get("is_proxy") is True:
                            proxy_map[sk] = dict(entry)
                        else:
                            swap_map[sk] = dict(entry)
                crypto_quotes["quotes"] = quotes_map
                crypto_quotes["swap"] = swap_map
                if proxy_map:
                    crypto_quotes["spot_proxies"] = proxy_map
        except Exception:
            pass

    try:
        collect_curated_articles()
    except Exception:
        pass
    try:
        build_opml_export()
    except Exception:
        pass

    articles = _collect_articles(kol_scorecards=kol_scorecards)
    push_channels = get_push_channels_status()

    # Recompute site-aware quick_thesis and attach latest ta_<SYM>_*.md deep report filename
    combined_sigs_for_thesis = signal_candidates or all_signals
    for sym, prof in stock_profiles.items():
        if not isinstance(prof, dict):
            continue
        prof["quick_thesis"] = build_quick_thesis(
            prof,
            risk_gate=risk_gate,
            signals=combined_sigs_for_thesis,
            kol_digest=kol_digest,
        )
        sym_pat = re.compile(rf"^ta_{re.escape(str(sym).upper())}_.*\.md$", re.IGNORECASE)
        ta_matches = [
            a for a in articles
            if isinstance(a, dict) and sym_pat.match(str(a.get("filename") or ""))
        ]
        if ta_matches:
            ta_matches.sort(key=lambda a: (str(a.get("mtime") or ""), str(a.get("filename") or "")), reverse=True)
            prof["deep_report_file"] = str(ta_matches[0]["filename"])

    # (d) Cross-source market synthesis
    market_analysis: dict[str, Any] = {}
    if build_market_synthesis is not None:
        try:
            market_analysis = build_market_synthesis(
                macro_fred=macro_fred,
                us_equities=us_equities,
                correlations=correlations,
                signals=all_signals,
                kol_digest=kol_digest,
                kol_scorecards=kol_scorecards,
                articles=articles,
                risk_gate=risk_gate,
                stock_profiles=stock_profiles,
            )
        except Exception:
            market_analysis = {}

    # (e) Forum sentiment divergence & 6-category asset source matrix
    forum_sentiment: dict[str, Any] = {}
    if collect_forum_pulse is not None and build_sentiment_divergence is not None:
        try:
            forum_pulse = collect_forum_pulse(force_refresh=False)
            forum_sentiment = build_sentiment_divergence(
                forum_pulse,
                kol_digest=kol_digest,
                kol_scorecards=kol_scorecards,
                signals=all_signals,
            )
            if collect_public_discussion_trends is not None and isinstance(forum_sentiment, dict):
                trends = collect_public_discussion_trends(output_path.parent)
                if trends:
                    forum_sentiment["discussion_trends"] = trends
                    if trim_forum_sentiment is not None:
                        forum_sentiment = trim_forum_sentiment(forum_sentiment)
        except Exception:
            forum_sentiment = {}

    asset_source_matrix: dict[str, Any] = {}
    if build_asset_source_matrix is not None:
        try:
            asset_source_matrix = build_asset_source_matrix(kol_scorecards=kol_scorecards)
        except Exception:
            asset_source_matrix = {}

    # (f) Ensure current weekly & monthly periodic decision reports exist, then re-collect articles if added
    articles_dir = COCKPIT_ROOT / "articles"
    if generate_periodic_decision_report is not None:
        partial_state = {
            "macro_fred": macro_fred,
            "us_equities": us_equities,
            "risk_gate": risk_gate,
            "positions": positions,
            "kol_digest": kol_digest,
            "kol_scorecards": kol_scorecards,
            "market_analysis": market_analysis,
            "forum_sentiment": forum_sentiment,
            "asset_source_matrix": asset_source_matrix,
            "signals": all_signals,
            "articles": articles,
            "calendar_events": calendar_events,
            "correlations": correlations,
            "crypto_quotes": crypto_quotes,
        }
        existing_fnames = {str(a.get("filename") or "") for a in articles if isinstance(a, dict)}
        need_recollect = False
        for _period in ("weekly", "monthly"):
            try:
                res_rep = generate_periodic_decision_report(
                    partial_state,
                    period=_period,
                    articles_dir=articles_dir,
                    force=False,
                )
                rep_fname = str(res_rep.get("filename") or "") if isinstance(res_rep, dict) else ""
                if rep_fname and rep_fname not in existing_fnames:
                    need_recollect = True
            except Exception:
                pass
        if need_recollect:
            articles = _collect_articles(kol_scorecards=kol_scorecards)

    cat_counts: dict[str, int] = {}
    for sig in all_signals:
        c = str(sig.get("category") or "macro")
        cat_counts[c] = cat_counts.get(c, 0) + 1

    kol_verified_authors_count = sum(
        1 for c in kol_scorecards if isinstance(c, dict) and int(c.get("verified_calls") or 0) >= 5
    )
    guru_articles_count = sum(
        1 for a in articles if isinstance(a, dict) and a.get("source") == "大佬分享"
    )
    periodic_reports_count = sum(
        1 for a in articles if isinstance(a, dict) and a.get("source") == "周月度深研"
    )
    forum_items_count = sum(
        int(c.get("count") or 0)
        for c in (forum_sentiment.get("channels_status") or [])
        if isinstance(c, dict)
    ) or len(forum_sentiment.get("hot_discussions") or [])
    source_matrix_count = len((asset_source_matrix or {}).get("categories") or [])
    cross_links_count = len((market_analysis or {}).get("cross_source_links") or [])

    signal_plans: list[dict[str, Any]] | None = None
    signal_plan_stats: dict[str, Any] | None = None
    if update_signal_advisor is not None:
        try:
            advisor_ctx = {
                "us_equities": us_equities,
                "macro_fred": macro_fred,
                "crypto_quotes": crypto_quotes,
                "risk_gate": risk_gate,
                "forum_sentiment": forum_sentiment,
            }
            signal_plans, signal_plan_stats = update_signal_advisor(advisor_ctx, data_dir=output_path.parent)
            if isinstance(signal_plan_stats, dict) and signal_plan_stats.get("ledger_persist_skipped"):
                signal_plans, signal_plan_stats = None, None
        except Exception:
            signal_plans, signal_plan_stats = None, None
    if signal_plans is None and output_path.is_file():
        try:
            prior_obj = json.loads(output_path.read_text(encoding="utf-8"))
            if isinstance(prior_obj, dict) and isinstance(prior_obj.get("signal_plans"), list):
                from backend.signal_advisor import project_signal_plan_stats, project_signal_plans

                signal_plans = project_signal_plans(prior_obj.get("signal_plans"))
                signal_plan_stats = project_signal_plan_stats(prior_obj.get("signal_plan_stats"))
        except (OSError, ValueError, TypeError):
            pass

    coverage = {
        "signals_count": len(all_signals),
        "signal_candidates_count": len(signal_candidates),
        "event_cards_count": len(event_cards or []),
        **({"signal_plans_count": len(signal_plans)} if signal_plans is not None else {}),
        "signal_sources_count": int(kazike_stats.get("source_count") or 0),
        "source_count": int(kazike_stats.get("source_count") or 0),
        "category_breakdown": cat_counts,
        "stock_profiles_count": len(stock_profiles),
        "sec_filings_count": sum(len(p.get("sec_filings") or []) for p in stock_profiles.values()),
        "articles_count": len(articles),
        "calendar_events_count": len(calendar_events),
        "kol_digest_count": len(kol_digest),
        "kol_verified_authors_count": kol_verified_authors_count,
        "guru_articles_count": guru_articles_count,
        "cross_links_count": cross_links_count,
        "forum_items_count": forum_items_count,
        "source_matrix_count": source_matrix_count,
        "periodic_reports_count": periodic_reports_count,
    }

    state: dict[str, Any] = {
        "schema_version": 2,
        "risk_gate": risk_gate,
        "positions": positions,
        "strategy_targets": strategy_targets,
        "macro_fred": macro_fred,
        "us_equities": us_equities,
        "stock_profiles": stock_profiles,
        "signals": all_signals,
        "signal_candidates": signal_candidates,
        **({"event_cards": event_cards, "event_stats": event_stats} if event_cards is not None else {}),
        **({"signal_plans": signal_plans, **({"signal_plan_stats": signal_plan_stats} if signal_plan_stats else {})} if signal_plans is not None else {}),
        "kazike_stats": kazike_stats,
        "correlations": correlations,
        "kol_digest": kol_digest,
        "kol_scorecards": kol_scorecards,
        "market_analysis": market_analysis,
        "forum_sentiment": forum_sentiment,
        "asset_source_matrix": asset_source_matrix,
        "articles": articles,
        "push_channels": push_channels,
        "calendar_events": calendar_events,
        "fx_snapshot": fx_snapshot,
        "crypto_quotes": crypto_quotes,
        "coverage": coverage,
    }
    state = _sanitize_state_value(state)

    counter_path = output_path.with_name("producer_generation.json")
    if output_path.resolve() == counter_path.resolve():
        raise ValueError("output_path conflicts with producer_generation.json")
    with file_lock(counter_path), file_lock(output_path):
        generation = 0
        if counter_path.exists():
            counter = json.loads(counter_path.read_text(encoding="utf-8"))
            generation = counter.get("generation") if isinstance(counter, dict) else None
            if type(generation) is not int or generation < 1:
                raise ValueError("invalid_producer_generation")
        state["generation"] = generation + 1
        state["generated_at_bjt"] = datetime.now(BJT).strftime("%Y-%m-%d %H:%M:%S+08:00")
        atomic_write_json(counter_path, {"generation": state["generation"]})
        atomic_write_json(output_path, state)
    try:
        if generate_notion_viz_html is not None:
            generate_notion_viz_html(state, correlations)
    except Exception:
        pass
    return state


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Generate MacroCockpit unified state JSON")
    parser.add_argument("--refresh-fred", action="store_true", help="Fetch latest FRED API / CSV observations")
    parser.add_argument("--out", default=str(STATE_OUTPUT_PATH), help="Output JSON file path")
    args = parser.parse_args()

    state = generate_cockpit_state(refresh_fred=args.refresh_fred, output_path=Path(args.out))
    summary = {
        "generated_at_bjt": state["generated_at_bjt"],
        "risk_light": state["risk_gate"]["light"],
        "positions": len(state["positions"]),
        "macro_fred": len(state["macro_fred"]),
        "us_indices": len(state["us_equities"]["indices"]),
        "mag7_and_ai": len(state["us_equities"]["mag7_and_ai"]),
        "sectors": len(state["us_equities"]["sectors"]),
        "signals": len(state["signals"]),
        "kol_digest": len(state["kol_digest"]),
        "articles": len(state["articles"]),
        "output": str(args.out),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
