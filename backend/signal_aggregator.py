"""Unified MacroCockpit state aggregator (quant + EQN + AIHOT + US equities + KOL + articles)."""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
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

try:
    from backend.correlation_engine import compute_cross_asset_correlations, generate_notion_viz_html
except ImportError:
    compute_cross_asset_correlations = None
    generate_notion_viz_html = None
from backend.calendar_events import build_calendar_events, filter_calendar_events
from backend.fred_and_market import (
    COCKPIT_ROOT,
    DATA_DIR,
    QUANT_LATEST_PATH,
    atomic_write_json,
    build_macro_fred,
    fetch_us_equities,
)
from backend.kazike_engine import lexical_similarity, normalize_url, process_financial_signals
from backend.mobile_push import get_push_channels_status
from backend.public_snapshot import file_lock, has_private_article_content, safe_article_filename
import backend.rss_sources as _rss_mod
from backend.rss_sources import (
    _dedup_summary,
    build_opml_export,
    collect_curated_articles,
    collect_curated_rss_signals,
    extract_tickers as _rss_extract_tickers,
    strip_emojis,
)
from backend.sec_edgar import collect_recent_sec_signals, fetch_sec_filings_map
from backend.stock_research import build_quick_thesis, build_stock_profiles

try:
    from backend.guru_sources import collect_guru_articles, collect_guru_signals
except ImportError:
    collect_guru_articles = None  # type: ignore[assignment]
    collect_guru_signals = None  # type: ignore[assignment]

try:
    from backend.kol_intel_engine import (
        DEFAULT_SCORECARD_CACHE_PATH,
        build_kol_actionable_signals,
        build_kol_digest,
        build_kol_scorecards,
        select_quality_kol_articles,
    )
except ImportError:
    DEFAULT_SCORECARD_CACHE_PATH = DATA_DIR / "kol_scorecard_cache.json"  # type: ignore[assignment]
    build_kol_actionable_signals = None  # type: ignore[assignment]
    build_kol_digest = None  # type: ignore[assignment]
    build_kol_scorecards = None  # type: ignore[assignment]
    select_quality_kol_articles = None  # type: ignore[assignment]

try:
    from backend.market_synthesis import build_market_synthesis
except ImportError:
    build_market_synthesis = None  # type: ignore[assignment]

try:
    from backend.market_quotes import fetch_crypto_quotes, fetch_fx_snapshot, fetch_hybrid_perp_quotes
except ImportError:
    fetch_crypto_quotes = None  # type: ignore[assignment]
    fetch_fx_snapshot = None  # type: ignore[assignment]
    fetch_hybrid_perp_quotes = None  # type: ignore[assignment]

try:
    from backend.forum_sentiment import build_sentiment_divergence, collect_forum_pulse
except ImportError:
    build_sentiment_divergence = None  # type: ignore[assignment]
    collect_forum_pulse = None  # type: ignore[assignment]

try:
    from backend.source_matrix import build_asset_source_matrix
except ImportError:
    build_asset_source_matrix = None  # type: ignore[assignment]

try:
    from backend.periodic_report import generate_periodic_decision_report
except ImportError:
    generate_periodic_decision_report = None  # type: ignore[assignment]

STATE_OUTPUT_PATH = DATA_DIR / "cockpit_state.json"
AIHOT_CACHE_PATH = DATA_DIR / "aihot_cache.json"

def _safe_home(fallback: Path) -> Path:
    try:
        return Path.home()
    except RuntimeError:
        return fallback


_USER_HOME = _safe_home(COCKPIT_ROOT)
_HERMES_HOME = Path(os.environ.get("HERMES_HOME") or (_USER_HOME / "AppData" / "Local" / "hermes"))
DECISION_BRIEF_PATH = Path(
    os.environ.get("MACROCOCKPIT_DECISION_BRIEF_PATH")
    or (_HERMES_HOME / "scripts" / "news" / "outputs" / "decision" / "latest_brief.json")
)
EQN_CANDIDATE_PATHS = (
    _HERMES_HOME / "scripts" / "news" / "outputs" / "eqn" / "latest.json",
    _HERMES_HOME / "scripts" / "news" / "outputs" / "eqn" / "latest_events.json",
)
KOL_ROOT = Path(
    os.environ.get("MACROCOCKPIT_KOL_DIR")
    or (_HERMES_HOME / "scripts" / "news" / "x-kol-notes")
)
KOL_ARTICLES_DIR = KOL_ROOT / "articles"
OBSIDIAN_INBOX_DIR = Path(
    os.environ.get("MACROCOCKPIT_NOTES_DIR")
    or (_USER_HOME / "Documents" / "Obsidian Vault" / "01_Inbox")
)

AIHOT_ACTOR_ID = (os.environ.get("AIHOT_ACTOR_ID") or "macrocockpit-agent-v2").strip()
AIHOT_UA = f"aihot-skill/2.0.0 (+https://aihot.news/aihot-skill/) aihot-actor/{AIHOT_ACTOR_ID}"
AIHOT_LATEST_URL = "https://aihot.news/api/v1/agent/latest?limit=20"
AIHOT_HOT_URL = "https://aihot.news/api/v1/agent/hot"

BJT = timezone(timedelta(hours=8))

POSITION_SPEC: tuple[tuple[str, str, str], ...] = (
    ("S1", "btc_trend", "核心趋势仓 (BTC)"),
    ("S2", "gold_hedge", "黄金避险仓"),
    ("S3", "bear_basket", "熊市配对对冲仓"),
    ("S4", "crash_bounce", "超跌反弹短打仓"),
)


def _read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


_CLEAN_BUSINESS_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    (r"E33\+E32\s*液态孤立慢洗", "高流动性超跌结构"),
    (r"(?<![A-Za-z0-9])(?:E33|E32)(?![A-Za-z0-9])", "超跌结构"),
    (r"(?<![A-Za-z0-9])E21(?![A-Za-z0-9])", "清算风暴"),
    (r"(?<![A-Za-z0-9])Tier-1(?![A-Za-z0-9])", "核心"),
    (r"(?<![A-Za-z0-9])Tier-S(?![A-Za-z0-9])", "高优"),
    (r"(?<![A-Za-z0-9])Tier-A(?![A-Za-z0-9])", "重点"),
    (r"(?<![A-Za-z0-9])Sleeves?(?![A-Za-z0-9])", "仓位"),
    (r"(?<![A-Za-z0-9])Regimes?(?![A-Za-z0-9])", "市场状态"),
    (r"(?<![A-Za-z0-9])arbitration(?![A-Za-z0-9])", "风险评估"),
    (r"(?<![A-Za-z0-9])N/A(?![A-Za-z0-9])", "—"),
    (r"测算口径|公式解释|公式说明|逻辑解释|联动说明|方法论说明", "规则摘要"),
    (r"待刷新|待补充|待补", "—"),
    (r"液态山寨", "高流动性山寨"),
    (r"MA50/MA120", "50/120日均线"),
    (r"MA100", "100日均线"),
    (r"（量化未确认，禁止提前抄底）", "（趋势未确认，严禁左侧提前抄底）"),
    (r"量化禁止左侧趋势做多", "趋势未转强，严禁左侧提前做多"),
)

_SKIP_SANITIZE_KEYS: frozenset[str] = frozenset({"url", "html_url", "filename", "id", "receipt_id", "sha256"})


def _scrub_forbidden_text(text: str, *, preserve_newlines: bool = True) -> str:
    """Replace internal jargon and forbidden placeholders while optionally preserving line breaks."""
    if not text:
        return ""
    s = strip_emojis(html.unescape(str(text)))
    for pat, rep in _CLEAN_BUSINESS_REPLACEMENTS:
        s = re.sub(pat, rep, s, flags=re.IGNORECASE)
    if preserve_newlines:
        return re.sub(r"[ \t]{2,}", " ", s).strip()
    return re.sub(r"\s{2,}", " ", s).strip()


def _clean_business_text(text: str) -> str:
    """Replace internal quant jargon with plain Chinese business wording."""
    return _scrub_forbidden_text(text, preserve_newlines=False)


def _sanitize_state_value(val: Any, key_name: str = "") -> Any:
    if isinstance(val, str):
        if key_name in _SKIP_SANITIZE_KEYS:
            return val
        return _scrub_forbidden_text(val, preserve_newlines=(key_name == "body_md" or "\n" in val))
    if isinstance(val, list):
        return [_sanitize_state_value(item, key_name) for item in val]
    if isinstance(val, dict):
        return {k: _sanitize_state_value(v, str(k)) for k, v in val.items()}
    return val


def _parse_iso_to_bjt_str(ts: str | None) -> str:
    if not ts:
        return ""
    raw = str(ts).strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(BJT).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return raw[:16]


_ASHARE_MATCH_RE = re.compile(
    r"(A股|沪指|深成指|创业板|科创板|上证|恒指|港股|中国证监会|北向|融资保证金|比亚迪|华为|宁德时代|茅台|中芯|寒武纪)"
)


def _fallback_extract_subtopics(text: str, category: str) -> list[str]:
    low = str(text or "").lower()
    topics: list[str] = []
    rules = (
        (r"deepseek|深度求索", "DeepSeek"),
        (r"\bopenai\b|chatgpt|gpt-", "OpenAI"),
        (r"anthropic|claude", "Anthropic"),
        (r"nvda|nvidia|英伟达|gpu|算力|芯片|半导体|台积电|tsm|amd|avgo", "算力芯片"),
        (r"具身智能|宇树|机器人|optimus", "具身智能"),
        (r"自动驾驶|智驾|fsd|比亚迪", "自动驾驶"),
        (r"美联储|fed|fomc|powell|鲍威尔|利率|加息|降息", "美联储"),
        (r"cpi|pce|通胀|非农|payroll", "CPI·通胀"),
        (r"黄金|gold|原油|wti|brent|大宗", "黄金·大宗"),
        (r"科创板|中芯|寒武纪|半导体|国产算力", "科创·半导体"),
        (r"港股|恒指|恒生科技|腾讯|阿里", "港股科技"),
        (r"财报|业绩|10-k|10-q|营收|利润", "财报业绩"),
        (r"etf|sec|监管|牌照|稳定币", "ETF·监管"),
    )
    for pat, label in rules:
        if re.search(pat, low, re.IGNORECASE) and label not in topics:
            topics.append(label)
    if not topics:
        defaults = {"ashare": "A股·港股", "ai": "AI前沿", "equities": "美股聚焦", "crypto": "加密动态", "macro": "宏观周期"}
        topics.append(defaults.get(category, "市场热点"))
    return topics[:4]


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
        fn = getattr(_rss_mod, "extract_subtopics", None)
        if callable(fn):
            out["topics"] = list(fn(txt, cat))[:5]
        else:
            out["topics"] = _fallback_extract_subtopics(txt, cat)
    return out


def _extract_tickers(text: str) -> list[str]:
    return _rss_extract_tickers(text)


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
    label = _clean_business_text(str(d_risk.get("label") or "黄灯 · 谨慎控险"))

    raw_reasons = [str(r) for r in (d_risk.get("reasons") or []) if r]
    if d_risk.get("flush_relief_note"):
        raw_reasons.append(str(d_risk["flush_relief_note"]))
    reasons = [_clean_business_text(r) for r in raw_reasons if r]
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
        note = _clean_business_text(note)

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


def _eqn_why_it_matters(ev: dict[str, Any], category: str, direction: str) -> str:
    txt = f"{ev.get('headline', '')} {ev.get('overview_zh', '')}".lower()
    if any(k in txt for k in ("fed", "美联储", "加息", "降息", "利率", "treasury", "pce", "通胀")):
        return "直接影响美联储利率路径与无风险收益率定价，牵动美股估值及加密流动性预期。"
    if any(k in txt for k in ("nvda", "英伟达", "sk海力士", "芯片", "openai", "甲骨文", "orcl")):
        return "涉及 AI 算力供应链或资本开支置信度，直接左右科技权重与芯片板块风险偏好。"
    if category == "crypto":
        return "对加密现货情绪及场外风险溢价有直接冲击，建议延续配对对冲、忌盲目追涨杀跌。"
    if direction == "bearish":
        return "高优先级风险事件，短期压制市场风险偏好，建议维持审慎仓位。"
    if direction == "bullish":
        return "有助于修复局部风险偏好，可继续跟踪后续成交量与均线确认。"
    return "重要市场结构信号，建议结合盘面波动与风险灯号观察后续传导。"


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
        headline = _clean_business_text(str(ev.get("headline") or "").lstrip("* "))
        if not headline:
            continue
        overview = _clean_business_text(str(ev.get("overview_zh") or ""))
        zh_raw = _clean_business_text(str(ev.get("zh") or "").lstrip("* "))
        raw_sum = overview or ("" if zh_raw == headline else zh_raw)
        if len(raw_sum) > 280:
            raw_sum = raw_sum[:277].rstrip() + "..."
        summary = _dedup_summary(headline, raw_sum)

        domain = str(ev.get("domain") or "").lower()
        tickers = _extract_tickers(f"{headline} {ev.get('zh', '')} {ev.get('en', '')}")
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
                "why_it_matters": _eqn_why_it_matters(ev, category, direction),
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


def _infer_ai_direction(text: str) -> str:
    bear_hits = len(re.findall(r"回落|下跌|暴跌|征税|限制|阻止|质疑|争议|调查|禁令|封杀|亏损", text))
    bull_hits = len(re.findall(r"收涨|突破|开源|发布|推出|上线|融资|增长|翻倍|加速|证明", text))
    if bear_hits > bull_hits:
        return "bearish"
    if bull_hits > bear_hits:
        return "bullish"
    return "neutral"


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
        title = _clean_business_text(head_m.group(1))
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
                summary = _clean_business_text(s[3:])
            elif s.startswith("推荐理由："):
                reason = _clean_business_text(s[5:])
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
                "direction": _infer_ai_direction(combined),
                "importance": imp,
                "time_bjt": time_bjt,
                "title": title,
                "summary": summary or title,
                "why_it_matters": reason or "前沿 AI 模型与工程生态动态，影响科技股与应用层估值预期。",
                "tickers": _extract_tickers(combined),
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
        title = _clean_business_text(head_m.group(2))
        aihot_url = head_m.group(3).strip()
        item_id = aihot_url.rstrip("/").split("/")[-1]

        sources_line = ""
        story_url = ""
        for ln in blk.splitlines()[1:]:
            s = ln.strip()
            if s.startswith("信源："):
                sources_line = _clean_business_text(s)
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
                "direction": _infer_ai_direction(title),
                "importance": "高" if rank <= 6 else "中",
                "time_bjt": time_bjt,
                "title": title,
                "summary": summary,
                "why_it_matters": f"全网多信源同步讨论热点（热榜第 {rank} 名），直接映射 AI 资本开支与监管风向。",
                "tickers": _extract_tickers(title),
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
            return (_clean_business_text(headline), _clean_business_text(takeaway))
        m2 = re.match(r"【X长文】([^：]+)[：:]\s*(.*)$", s)
        if m2:
            return (_clean_business_text(m2.group(1)), _clean_business_text(m2.group(2) or m2.group(1)))
    short = s[:54].rstrip("，。；： ") + ("..." if len(s) > 54 else "")
    return (_clean_business_text(short), _clean_business_text(s))


def _classify_kol_topic(headline: str, takeaway: str, group_label: str) -> str:
    txt = f"{headline} {takeaway}".lower()
    if any(k in txt for k in ("ai", "capex", "nvda", "intc", "芯片", "数据中心", "算力", "微软", "谷歌", "半导体", "hardware")):
        return "AI资本开支与美股科技"
    if any(k in txt for k in ("fed", "fomc", "liquidity", "treasury", "rate", "cpi", "pce", "美联储", "美债", "利率", "流动性", "降息", "通胀")):
        return "美联储流动性与美债"
    if any(k in txt for k in ("sec", "cftc", "tariff", "ban", "trump", "监管", "关税", "州长", "政策", "调查")):
        return "地缘与监管"
    if "加密" in group_label or any(k in txt for k in ("btc", "eth", "sol", "crypto", "etf", "defi", "比特币", "以太坊", "山寨", "链上")):
        return "BTC周期与山寨"
    return "市场情绪与估值"


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
            topic = _classify_kol_topic(headline, takeaway, group_label)
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


def get_allowed_article_dirs() -> list[tuple[Path, str]]:
    return [
        (COCKPIT_ROOT / "articles", "同步深度长文"),
        (KOL_ARTICLES_DIR, "KOL深度长文"),
        (OBSIDIAN_INBOX_DIR, "本地知识库"),
    ]


def read_article_markdown(
    path: Path, source_label: str, *, max_bytes: int | None = None
) -> str | None:
    inbox = source_label == "本地知识库" or path.resolve().is_relative_to(OBSIDIAN_INBOX_DIR.resolve())
    if inbox:
        max_bytes = min(max_bytes, 256 * 1024) if max_bytes is not None else 256 * 1024
    try:
        with path.open("rb") as handle:
            raw = handle.read(max_bytes + 1) if max_bytes is not None else handle.read()
    except OSError:
        return None
    if max_bytes is not None and len(raw) > max_bytes:
        return None
    text = raw.decode("utf-8-sig", errors="replace").replace("\r\n", "\n")
    if inbox:
        frontmatter = re.match(r"\A---[ \t]*\n(.*?)^---[ \t]*$", text, flags=re.MULTILINE | re.DOTALL)
        if not frontmatter or not re.search(r"^publish:[ \t]*true[ \t]*(?:#.*)?$", frontmatter[1], re.MULTILINE):
            return None
        try:
            import yaml
        except ImportError:
            return None
        try:
            meta = yaml.safe_load(frontmatter[1])
        except yaml.YAMLError:
            return None
        if not isinstance(meta, dict) or meta.get("publish") is not True:
            return None
    return text if not has_private_article_content({"filename": path.name, "content": text}) else None


def parse_markdown_article(path: Path, source_label: str) -> dict[str, Any] | None:
    """Parse a markdown file into the shared article schema."""
    if not path.is_file() or not safe_article_filename(path.name):
        return None
    try:
        raw = read_article_markdown(path, source_label)
        if raw is None:
            return None
        mtime_str = datetime.fromtimestamp(path.stat().st_mtime, tz=BJT).strftime("%Y-%m-%d %H:%M")
    except OSError:
        return None

    title = ""
    excerpt = ""
    publisher = ""
    author_role = ""
    art_url = ""
    art_score: int | None = None
    art_tickers: list[str] = []
    report_kind = ""
    body_md = raw.strip()

    # Check YAML frontmatter
    if body_md.startswith("---"):
        parts = body_md.split("---", 2)
        if len(parts) == 3:
            fm_raw = parts[1]
            body_md = parts[2].strip()
            fm_meta: dict[str, Any] = {}
            try:
                import yaml

                parsed_fm = yaml.safe_load(fm_raw)
                if isinstance(parsed_fm, dict):
                    fm_meta = parsed_fm
            except Exception:
                fm_meta = {}

            if fm_meta:
                title = str(fm_meta.get("title") or "").strip()
                excerpt = str(fm_meta.get("summary") or "").strip()
                publisher = str(fm_meta.get("publisher") or "").strip()
                author_role = str(fm_meta.get("author_role") or "").strip()
                art_url = str(fm_meta.get("url") or "").strip()
                report_kind = str(fm_meta.get("report_kind") or "").strip().lower()
                raw_score = fm_meta.get("score")
                if isinstance(raw_score, (int, float)) and not isinstance(raw_score, bool):
                    art_score = int(round(float(raw_score)))
                raw_tickers = fm_meta.get("tickers")
                if isinstance(raw_tickers, list):
                    art_tickers = [str(t).strip().upper() for t in raw_tickers if str(t).strip()][:8]
            else:
                m_title = re.search(r'^title:\s*["\']?(.+?)["\']?\s*$', fm_raw, re.MULTILINE)
                m_sum = re.search(r'^summary:\s*["\']?(.+?)["\']?\s*$', fm_raw, re.MULTILINE)
                m_pub = re.search(r'^publisher:\s*["\']?(.+?)["\']?\s*$', fm_raw, re.MULTILINE)
                m_role = re.search(r'^author_role:\s*["\']?(.+?)["\']?\s*$', fm_raw, re.MULTILINE)
                m_url = re.search(r'^url:\s*["\']?(.+?)["\']?\s*$', fm_raw, re.MULTILINE)
                m_rk = re.search(r'^report_kind:\s*["\']?(.+?)["\']?\s*$', fm_raw, re.MULTILINE)
                m_scr = re.search(r'^score:\s*(\d+(?:\.\d+)?)\s*$', fm_raw, re.MULTILINE)
                if m_title:
                    title = m_title.group(1).strip()
                if m_sum:
                    excerpt = m_sum.group(1).strip()
                if m_pub:
                    publisher = m_pub.group(1).strip()
                if m_role:
                    author_role = m_role.group(1).strip()
                if m_url:
                    art_url = m_url.group(1).strip()
                if m_rk:
                    report_kind = m_rk.group(1).strip().lower()
                if m_scr:
                    art_score = int(round(float(m_scr.group(1))))

    if not title:
        for ln in body_md.splitlines():
            s = ln.strip()
            if s.startswith("# "):
                title = s[2:].strip()
                break
    if not title:
        title = path.stem

    if not excerpt:
        m_abs = re.search(r"\*\*核心摘要\*\*[：:]\s*(.+)", body_md)
        if m_abs:
            excerpt = m_abs.group(1).strip()
        else:
            for ln in body_md.splitlines():
                s = ln.strip()
                if s and not s.startswith(("#", "-", "*", ">", "---", "```")) and len(s) >= 24:
                    excerpt = s
                    break
    if len(excerpt) > 180:
        excerpt = excerpt[:177].rstrip() + "..."

    fname_lower = path.name.lower()
    if fname_lower.startswith(("weekly_", "monthly_", "dr_")) or report_kind in ("weekly", "monthly", "deep_research"):
        resolved_source = "周月度深研"
    elif fname_lower.startswith("ta_"):
        resolved_source = "多智能体深研"
    elif fname_lower.startswith("guru_"):
        resolved_source = "大佬分享"
    elif publisher:
        resolved_source = "AI与金融研报"
    else:
        resolved_source = source_label
    clean_body = _scrub_forbidden_text(body_md, preserve_newlines=True)
    out: dict[str, Any] = {
        "filename": path.name,
        "title": _clean_business_text(title),
        "source": resolved_source,
        "mtime": mtime_str,
        "excerpt": _clean_business_text(excerpt) or _clean_business_text(title),
        "body_md": clean_body,
    }
    if publisher:
        out["publisher"] = _clean_business_text(publisher)
    if author_role:
        out["author_role"] = _clean_business_text(author_role)
    if report_kind:
        out["report_kind"] = report_kind
    if art_tickers:
        out["tickers"] = art_tickers
    if art_url.startswith(("http://", "https://")):
        out["url"] = art_url
    if art_score is not None:
        out["score"] = art_score
    return out


def _dedup_articles(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    seen_titles: list[str] = []
    seen_ta_syms: set[str] = set()
    seen_periodic_fnames: set[str] = set()
    for art in items:
        fname = str(art.get("filename") or "")
        if art.get("source") == "周月度深研" or fname.lower().startswith(("weekly_", "monthly_", "dr_")):
            pf_key = fname.lower()
            if pf_key in seen_periodic_fnames:
                continue
            seen_periodic_fnames.add(pf_key)
            deduped.append(art)
            continue
        m_ta = re.match(r"^ta_([A-Za-z0-9._-]+)_", fname, re.IGNORECASE)
        if m_ta:
            sym_key = m_ta.group(1).upper()
            if sym_key in seen_ta_syms:
                continue
            seen_ta_syms.add(sym_key)
            deduped.append(art)
            continue
        norm_u = normalize_url(art.get("url"))
        title = str(art.get("title") or "").strip()
        if norm_u and norm_u in seen_urls:
            continue
        if title and any(lexical_similarity(title, prev) >= 0.68 for prev in seen_titles):
            continue
        if norm_u:
            seen_urls.add(norm_u)
        if title:
            seen_titles.append(title)
        deduped.append(art)
    return deduped


def _interleave_article_buckets(buckets: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Round-robin interleave non-empty article source buckets so every source remains represented."""
    queues = [list(b) for b in buckets if b]
    merged: list[dict[str, Any]] = []
    while queues:
        next_queues: list[list[dict[str, Any]]] = []
        for q in queues:
            merged.append(q.pop(0))
            if q:
                next_queues.append(q)
        queues = next_queues
    return merged


def _sort_and_balance_articles(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    periodic_arts = sorted(
        [a for a in articles if a.get("source") == "周月度深研"],
        key=lambda x: (int(x.get("score") or 0), str(x.get("mtime") or "")),
        reverse=True,
    )
    guru_arts = sorted(
        [a for a in articles if a.get("source") == "大佬分享"],
        key=lambda x: (int(x.get("score") or 0), str(x.get("mtime") or "")),
        reverse=True,
    )
    ai_arts = sorted(
        [a for a in articles if a.get("source") == "AI与金融研报"],
        key=lambda x: (int(x.get("score") or 0), str(x.get("mtime") or "")),
        reverse=True,
    )
    kol_arts = sorted(
        [a for a in articles if a.get("source") == "KOL深度长文"],
        key=lambda x: (int(x.get("score") or 0), str(x.get("mtime") or "")),
        reverse=True,
    )
    ta_arts = sorted(
        [a for a in articles if a.get("source") == "多智能体深研"],
        key=lambda x: str(x.get("mtime") or ""),
        reverse=True,
    )
    kb_arts = sorted(
        [
            a
            for a in articles
            if a.get("source") not in ("周月度深研", "大佬分享", "AI与金融研报", "KOL深度长文", "多智能体深研")
        ],
        key=lambda x: str(x.get("mtime") or ""),
        reverse=True,
    )
    interleaved = _interleave_article_buckets([periodic_arts, guru_arts, ai_arts, kol_arts, ta_arts, kb_arts])
    return _dedup_articles(interleaved)


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
                clean_art["title"] = _clean_business_text(str(clean_art.get("title") or fname))
                clean_art["excerpt"] = _clean_business_text(str(clean_art.get("excerpt") or clean_art["title"]))
                clean_art["body_md"] = _scrub_forbidden_text(str(clean_art.get("body_md") or ""), preserve_newlines=True)
                if clean_art.get("publisher"):
                    clean_art["publisher"] = _clean_business_text(str(clean_art["publisher"]))
                if clean_art.get("author_role"):
                    clean_art["author_role"] = _clean_business_text(str(clean_art["author_role"]))
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
    for sig_list in (all_signals, signal_candidates):
        for sig in sig_list:
            if isinstance(sig, dict):
                sig["title"] = strip_emojis(str(sig.get("title") or ""))
                sig["summary"] = _dedup_summary(sig["title"], str(sig.get("summary") or ""))
    if compute_cross_asset_correlations is None:
        raise RuntimeError("State refresh requires pandas and numpy")
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

    coverage = {
        "signals_count": len(all_signals),
        "signal_candidates_count": len(signal_candidates),
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
