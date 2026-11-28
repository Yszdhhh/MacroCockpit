"""Idempotent REST sync pipeline from MacroCockpit state to Notion Intelligence Hub.

Uses standard Notion REST API (`Notion-Version: 2022-06-28`) with:
- Rate limiting (<= 2.4 req/s) and 429/5xx exponential backoff.
- Idempotent upsert keyed by ``同步键`` / ``事件ID``.
- Native Notion DATE payloads for ``日期``, ``发布时间``, ``发布日期``, ``收录日期``.
- Strict protection of user-owned fields and manual review / quick-note databases.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend.runtime import BJT, COCKPIT_ROOT, DATA_DIR, finite_float as _safe_float, resolve_hermes_home
_NOTION_LOCAL_CFG = COCKPIT_ROOT / "config" / "notion_ids.json"
NOTION_IDS_PATH = _NOTION_LOCAL_CFG if _NOTION_LOCAL_CFG.is_file() else (COCKPIT_ROOT / "config" / "notion_ids.example.json")
STATE_PATH = DATA_DIR / "cockpit_state.json"


_HERMES_ROOT = resolve_hermes_home()
HERMES_ENV_PATH = _HERMES_ROOT / ".env"

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

USER_OWNED_FIELDS: frozenset[str] = frozenset(
    {"跟踪状态", "验证状态", "阅读状态", "实际仓位%", "复盘归因"}
)

SIGNAL_CATEGORY_MAP = {
    "equities": "美股",
    "macro": "宏观美联储",
    "crypto": "加密",
    "ai": "AI",
    "regulatory": "监管",
}
SIGNAL_DIRECTION_MAP = {
    "bearish": "利空",
    "bullish": "利多",
    "neutral": "中性",
}
SIGNAL_ALLOWED_SOURCES = {"方程式快讯", "AIHOT精选", "美股盘面", "美联储官方"}
SIGNAL_ALLOWED_TIERS = {"T1一手", "T1.5一线", "T2媒体KOL"}

STANCE_MAP = {
    "偏多": "多头阵营",
    "偏空": "空头阵营",
    "中性": "中性观察",
    "多头阵营": "多头阵营",
    "空头阵营": "空头阵营",
    "中性观察": "中性观察",
}

RISK_LIGHT_MAP = {
    "GREEN": "绿灯",
    "YELLOW": "黄灯",
    "RED": "红灯",
    "绿灯": "绿灯",
    "黄灯": "黄灯",
    "红灯": "红灯",
}

POS_CATEGORY_MAP = {
    "btc_trend": "核心趋势仓(BTC)",
    "gold_hedge": "黄金避险仓",
    "bear_basket": "熊市配对对冲仓",
    "crash_bounce": "超跌反弹短打仓",
    "portfolio": "组合总览",
}

MACRO_SOURCE_ALLOWED = {"FRED", "Binance", "腾讯财经", "计算合成"}


def _title_prop(text: str) -> dict[str, Any]:
    return {"title": [{"type": "text", "text": {"content": str(text or "")[:1900]}}]}


def _rich_text_prop(text: str) -> dict[str, Any]:
    clean = str(text or "").strip()
    if not clean:
        return {"rich_text": []}
    return {"rich_text": [{"type": "text", "text": {"content": clean[:1900]}}]}


def _status_prop(name: str | None) -> dict[str, Any]:
    clean = str(name or "").strip()
    return {"status": {"name": clean} if clean else None}


def _select_prop(name: str | None) -> dict[str, Any]:
    clean = str(name or "").strip()
    return {"select": {"name": clean} if clean else None}


def _multi_select_prop(names: list[str]) -> dict[str, Any]:
    return {"multi_select": [{"name": n} for n in names if str(n).strip()]}


def _number_prop(value: Any) -> dict[str, Any]:
    num = _safe_float(value)
    if num is None:
        return {"number": None}
    return {"number": int(num) if num.is_integer() else round(num, 6)}


def _url_prop(url: str | None) -> dict[str, Any]:
    clean = str(url or "").strip()
    if clean.startswith(("http://", "https://")):
        return {"url": clean}
    return {"url": None}


def _date_prop(start_iso: str | None) -> dict[str, Any]:
    clean = str(start_iso or "").strip()
    return {"date": {"start": clean} if clean else None}


def _relation_prop(page_ids: list[str]) -> dict[str, Any]:
    return {"relation": [{"id": pid} for pid in page_ids if pid]}


def strip_user_owned_fields(props: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow copy excluding all user-managed Notion properties."""
    return {k: v for k, v in props.items() if k not in USER_OWNED_FIELDS}


def _format_bjt_iso_datetime(time_str: str, fallback_date: str) -> str:
    s = str(time_str or "").strip()
    m = re.match(r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})(?::(\d{2}))?", s)
    if m:
        sec = m.group(3) or "00"
        return f"{m.group(1)}T{m.group(2)}:{sec}+08:00"
    m_date = re.match(r"^(\d{4}-\d{2}-\d{2})$", s)
    if m_date:
        return m_date.group(1)
    return fallback_date


def _extract_date_only(value: str, fallback: str) -> str:
    m = re.search(r"(\d{4}-\d{2}-\d{2})", str(value or ""))
    return m.group(1) if m else fallback


def infer_report_date(state: dict[str, Any]) -> str:
    """Resolve the active trading/intelligence report date (YYYY-MM-DD)."""
    corr_date = str((state.get("correlations") or {}).get("as_of") or "").strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", corr_date):
        return corr_date
    gen = str(state.get("generated_at_bjt") or "").strip()
    if len(gen) >= 10:
        return gen[:10]
    return datetime.now(BJT).strftime("%Y-%m-%d")


def _macro_lookup(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(m.get("id")): m for m in (state.get("macro_fred") or []) if m.get("id")}


def build_daily_report_properties(
    state: dict[str, Any], *, is_update: bool = False
) -> dict[str, Any]:
    """Build DB1 (金融日报库) REST property payload with native 日期 field."""
    report_date = infer_report_date(state)
    rg = state.get("risk_gate") or {}
    macros = _macro_lookup(state)

    light_cn = RISK_LIGHT_MAP.get(str(rg.get("light", "YELLOW")).upper(), "黄灯")
    reasons = [str(r) for r in (rg.get("reasons") or []) if str(r).strip()]
    eq_summary = str((state.get("us_equities") or {}).get("summary") or "").strip()
    exposure = str(rg.get("summary_exposure") or "").strip()
    one_liner = "；".join([p for p in (rg.get("label"), exposure, eq_summary) if p])[:280]

    props: dict[str, Any] = {
        "日期": _date_prop(report_date),
        "覆盖板块": _multi_select_prop(["美股", "AI", "加密", "美联储宏观"]),
        "风险灯号": _select_prop(light_cn),
        "一句话定调": _rich_text_prop(one_liner or "; ".join(reasons)),
        "BTC现价": _number_prop((macros.get("btc_spot") or {}).get("raw_value")),
        "标普500": _number_prop((macros.get("sp500") or {}).get("raw_value")),
        "VIX": _number_prop((macros.get("vix") or {}).get("raw_value")),
        "美债10Y": _number_prop((macros.get("us_10y") or {}).get("raw_value")),
        "同步键": _rich_text_prop(f"daily:{report_date}"),
    }
    if not is_update:
        props["标题"] = _title_prop(f"{report_date} 金融日报｜美股·AI·加密·{light_cn}")
    return props


def _map_signal_source(raw_source: str) -> str:
    s = str(raw_source or "").strip()
    if s in SIGNAL_ALLOWED_SOURCES:
        return s
    if "AIHOT" in s.upper():
        return "AIHOT精选"
    if "美联储" in s or "FRED" in s.upper():
        return "美联储官方"
    if "美股" in s:
        return "美股盘面"
    return "方程式快讯"


def build_signal_properties(
    signal: dict[str, Any], *, report_date: str = "", is_update: bool = False
) -> dict[str, Any]:
    """Build DB2 (精选信号流) REST property payload."""
    fallback_date = report_date or datetime.now(BJT).strftime("%Y-%m-%d")
    cat = SIGNAL_CATEGORY_MAP.get(str(signal.get("category", "")).lower(), "美股")
    direction = SIGNAL_DIRECTION_MAP.get(str(signal.get("direction", "")).lower(), "中性")
    tier = str(signal.get("source_tier_label") or "T1.5一线")
    if tier not in SIGNAL_ALLOWED_TIERS:
        tier = "T1.5一线"
    heat_badge = str(signal.get("heat_badge") or "持续")
    if heat_badge not in {"突发", "升温", "持续", "归档"}:
        heat_badge = "持续"
    group_rel = str(signal.get("group_relation") or "新事件")
    if group_rel not in {"新事件", "同事件合并", "后续进展"}:
        group_rel = "新事件"

    props: dict[str, Any] = {
        "标题": _title_prop(str(signal.get("title") or signal.get("id") or "未命名信号")),
        "事件ID": _rich_text_prop(str(signal.get("id") or "")),
        "发布时间": _date_prop(_format_bjt_iso_datetime(str(signal.get("time_bjt") or ""), fallback_date)),
        "分类": _select_prop(cat),
        "来源": _select_prop(_map_signal_source(str(signal.get("source") or ""))),
        "信源分级": _select_prop(tier),
        "多空方向": _select_prop(direction),
        "注意力分": _number_prop(signal.get("attention_score")),
        "48h独立热度": _number_prop(signal.get("heat_48h")),
        "热度徽章": _select_prop(heat_badge),
        "归组关系": _select_prop(group_rel),
        "事件簇ID": _rich_text_prop(str(signal.get("cluster_id") or "")),
        "影响研判": _rich_text_prop(str(signal.get("why_it_matters") or "")),
        "原文链接": _url_prop(signal.get("url")),
    }
    if not is_update:
        props["跟踪状态"] = _status_prop("新入库")
    return strip_user_owned_fields(props) if is_update else props


def _map_macro_source(item: dict[str, Any]) -> str:
    src = str(item.get("source") or "").strip()
    if src in MACRO_SOURCE_ALLOWED:
        return src
    if src == "Market":
        return "腾讯财经" if str(item.get("id")) == "gold" else "计算合成"
    return "计算合成"


def build_macro_properties(
    item: dict[str, Any], *, fallback_date: str = "", is_update: bool = False
) -> dict[str, Any]:
    """Build DB3 (宏观与关联快照) macro indicator row payload."""
    as_of = _extract_date_only(str(item.get("as_of") or ""), fallback_date or datetime.now(BJT).strftime("%Y-%m-%d"))
    note = str(item.get("note") or "").strip()
    if as_of and as_of not in note:
        note = f"{note}（数据日 {as_of}）" if note else f"数据日 {as_of}"
    group = str(item.get("group") or "跨资产温度")
    if group not in {"美联储与利率", "跨资产温度", "加密流动性", "跨资产相关性"}:
        group = "跨资产温度"
    return {
        "指标名称": _title_prop(str(item.get("label") or item.get("id") or "")),
        "日期": _date_prop(as_of),
        "指标分组": _select_prop(group),
        "当前读数": _rich_text_prop(str(item.get("value_display") or "")),
        "数值": _number_prop(item.get("raw_value")),
        "变动": _rich_text_prop(str(item.get("change_display") or "")),
        "背离判断": _select_prop("正常联动"),
        "数据来源": _select_prop(_map_macro_source(item)),
        "备注": _rich_text_prop(note),
        "同步键": _rich_text_prop(f"macro:{item.get('id')}"),
    }


def build_correlation_properties(
    pair_item: dict[str, Any], as_of: str, *, is_update: bool = False
) -> dict[str, Any]:
    """Build DB3 (宏观与关联快照) cross-asset correlation row payload."""
    c30 = float(pair_item.get("corr_30d") or 0.0)
    c90 = float(pair_item.get("corr_90d") or 0.0)
    delta = float(pair_item.get("delta") or 0.0)
    div = str(pair_item.get("divergence") or "正常联动")
    if div not in {"正常联动", "显著背离", "极值警戒"}:
        div = "正常联动"
    date_str = _extract_date_only(str(pair_item.get("as_of") or as_of), datetime.now(BJT).strftime("%Y-%m-%d"))
    note = str(pair_item.get("note") or "").strip()
    if date_str and date_str not in note:
        note = f"{note}（截止 {date_str}）" if note else f"截止 {date_str}"
    return {
        "指标名称": _title_prop(str(pair_item.get("pair") or pair_item.get("id") or "")),
        "日期": _date_prop(date_str),
        "指标分组": _select_prop("跨资产相关性"),
        "当前读数": _rich_text_prop(f"30D {c30:+.2f} / 90D {c90:+.2f}"),
        "数值": _number_prop(c30),
        "变动": _rich_text_prop(f"差值 {delta:+.2f}"),
        "相关性30D": _number_prop(c30),
        "相关性90D": _number_prop(c90),
        "背离判断": _select_prop(div),
        "数据来源": _select_prop("计算合成"),
        "备注": _rich_text_prop(note),
        "同步键": _rich_text_prop(f"corr:{pair_item.get('id')}"),
    }


def build_kol_properties(
    kol: dict[str, Any], *, fallback_date: str = "", is_update: bool = False
) -> dict[str, Any]:
    """Build DB4 (KOL观点时间树) REST property payload."""
    handle = str(kol.get("handle") or "").lstrip("@")
    date_str = _extract_date_only(str(kol.get("date") or ""), fallback_date or datetime.now(BJT).strftime("%Y-%m-%d"))
    stance = STANCE_MAP.get(str(kol.get("stance") or "中性"), "中性观察")
    group = str(kol.get("group") or "宏观与风控")
    if group not in {"宏观与风控", "加密与链上", "美股与AI"}:
        group = "宏观与风控"
    topic = str(kol.get("topic") or "市场情绪与估值")
    allowed_topics = {
        "AI资本开支与美股科技",
        "美联储流动性与美债",
        "BTC周期与山寨",
        "地缘与监管",
        "市场情绪与估值",
    }
    if topic not in allowed_topics:
        topic = "市场情绪与估值"
    takeaway = str(kol.get("takeaway") or "").strip()
    if date_str and date_str not in takeaway:
        takeaway = f"{takeaway}（{date_str}）"

    props: dict[str, Any] = {
        "观点标题": _title_prop(str(kol.get("headline") or f"@{handle}")),
        "KOL作者": _rich_text_prop(f"@{handle}"),
        "分组": _select_prop(group),
        "核心议题": _select_prop(topic),
        "多空立场": _select_prop(stance),
        "情绪分": _number_prop(kol.get("score")),
        "发布日期": _date_prop(date_str),
        "核心论点": _rich_text_prop(takeaway),
        "原文链接": _url_prop(kol.get("url")),
        "同步键": _rich_text_prop(f"kol:{handle}:{date_str}"),
    }
    if not is_update:
        props["验证状态"] = _status_prop("待验证")
    return strip_user_owned_fields(props) if is_update else props


def build_article_properties(
    article: dict[str, Any], *, fallback_date: str = "", is_update: bool = False
) -> dict[str, Any]:
    """Build DB5 (长文精读库) REST property payload."""
    fname = str(article.get("filename") or "")
    body = str(article.get("body_md") or "")
    date_str = _extract_date_only(str(article.get("mtime") or ""), fallback_date or datetime.now(BJT).strftime("%Y-%m-%d"))

    url_match = re.search(r"https?://[^\s)>\"']+", body)
    url = url_match.group(0) if url_match else ""

    raw_src = str(article.get("source") or "")
    if raw_src == "KOL深度长文":
        src = "X专栏" if "KobeissiLetter" in fname else ("X长推" if "x.com/" in url else "外部文章")
    else:
        src = "本地知识库"

    author = "个人知识库"
    m_author = re.search(r"`@([A-Za-z0-9_]+)`", body)
    if m_author:
        author = m_author.group(1)
    elif raw_src == "KOL深度长文" and "_" in fname:
        author = fname.split("_", 1)[0]
    elif "Podwise" in body or fname.startswith(("138.", "140.", "E111.")):
        author = "Podwise/知识库"

    themes: list[str] = []
    combined_text = f"{article.get('title', '')} {article.get('excerpt', '')}"
    if any(k in combined_text for k in ("美股", "CapEx", "NVDA", "S&P", "标普")):
        themes.append("美股")
    if any(k in combined_text for k in ("AI", "Agent", "大模型", "算力", "Claude", "OpenClaw")):
        themes.append("AI")
    if any(k in combined_text for k in ("BTC", "ETH", "加密")):
        themes.append("加密")
    if any(k in combined_text for k in ("宏观", "美联储", "通胀", "PCE", "Fed", "流动性")):
        themes.append("宏观")
    if any(k in combined_text for k in ("塔勒布", "交易", "知识库", "反脆弱", "YTC")):
        themes.append("方法论")
    if not themes:
        themes = ["AI"]

    word_count = len(body) if body else len(str(article.get("excerpt") or ""))
    props: dict[str, Any] = {
        "文章标题": _title_prop(str(article.get("title") or fname)),
        "作者": _rich_text_prop(author),
        "来源": _select_prop(src),
        "核心主题": _multi_select_prop(themes),
        "收录日期": _date_prop(date_str),
        "字数": _number_prop(word_count),
        "核心摘录": _rich_text_prop(str(article.get("excerpt") or "")),
        "原文链接": _url_prop(url),
        "同步键": _rich_text_prop(f"art:{fname}"),
    }
    if not is_update:
        props["阅读状态"] = _status_prop("待阅读")
    return strip_user_owned_fields(props) if is_update else props


def build_position_properties(
    pos: dict[str, Any],
    state: dict[str, Any],
    *,
    daily_page_id: str | None = None,
    is_update: bool = False,
) -> dict[str, Any]:
    """Build DB6 (仓位与操作复盘) system snapshot payload. Never writes 实际仓位% or 复盘归因 on update."""
    report_date = infer_report_date(state)
    pos_id = str(pos.get("id") or "")
    category = POS_CATEGORY_MAP.get(pos_id, str(pos.get("name") or "组合总览").replace(" (", "("))
    rg_light = RISK_LIGHT_MAP.get(str((state.get("risk_gate") or {}).get("light", "YELLOW")).upper(), "黄灯")
    pos_state = str(pos.get("state") or "空仓观望")

    props: dict[str, Any] = {
        "记录标题": _title_prop(f"{report_date} {category}｜{pos_state}"),
        "日期": _date_prop(report_date),
        "记录类型": _select_prop("系统仓位快照"),
        "仓位类别": _select_prop(category),
        "风险灯号": _select_prop(rg_light),
        "量化基准%": _number_prop(pos.get("baseline_pct")),
        "系统建议%": _number_prop(pos.get("recommended_pct")),
        "参考金额USD": _number_prop(pos.get("est_notional_usd")),
        "操作依据与备注": _rich_text_prop(str(pos.get("note") or "")),
        "同步键": _rich_text_prop(f"pos:{pos_id}:{report_date}"),
    }
    if daily_page_id:
        props["关联日报"] = _relation_prop([daily_page_id])
    if not is_update:
        props["复盘归因"] = _select_prop("待复盘")
    return strip_user_owned_fields(props) if is_update else props


HOME_LIVE_PREFIXES = (
    "风控与仓位", "美股与 AI 盘面", "美联储与加密温度", "今日高优快讯",
    "跨资产相关性背离", "多头阵营", "空头阵营",
)
_HOME_FORBIDDEN = ("测算口径", "公式解释", "逻辑解释", "联动说明", "待补", "待刷新", "N/A")


def _home_rt(content: str, *, bold: bool = False, url: str = "") -> dict[str, Any]:
    item: dict[str, Any] = {"type": "text", "text": {"content": str(content)[:1900]}}
    if bold:
        item["annotations"] = {"bold": True}
    if url.startswith(("http://", "https://")):
        item["text"]["link"] = {"url": url}
    return item


def build_home_live_sections_data(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Build user-facing Home callout headers and bullet rich_text specs."""
    macros = _macro_lookup(state)
    rg = state.get("risk_gate") or {}
    positions = {str(p.get("id")): p for p in state.get("positions") or []}
    bjt = str(state.get("generated_at_bjt") or "")[:16]

    def value(id_: str) -> str:
        item = macros.get(id_) or {}
        return str(item.get("value_display") or item.get("raw_value") or "—")

    def bullet(*parts: dict[str, Any]) -> dict[str, Any]:
        return {"rich_text": list(parts)}

    risk_bullets = [bullet(_home_rt(f"{RISK_LIGHT_MAP.get(str(rg.get('light', '')).upper(), str(rg.get('label') or '风险灯'))} · 多头风险上限 "), _home_rt(f"{float(rg.get('long_risk_cap') or 0) * 100:.0f}%", bold=True))]
    labels = {"btc_trend": "核心趋势", "gold_hedge": "黄金避险", "bear_basket": "熊市配对", "crash_bounce": "超跌短打"}
    for pid, label in labels.items():
        p = positions.get(pid) or {}
        risk_bullets.append(bullet(_home_rt(f"{label} · {p.get('state') or '观察'} · 建议 "), _home_rt(f"{float(p.get('recommended_pct') or 0):.0f}%", bold=True), _home_rt(f" · ${float(p.get('est_notional_usd') or 0):,.0f}")))

    eq = state.get("us_equities") or {}
    eq_lines = [bullet(_home_rt(f"截至 {eq.get('as_of') or bjt} · {eq.get('summary') or ''}"))]
    rows = (eq.get("indices") or []) + (eq.get("mag7_and_ai") or []) + (eq.get("sectors") or [])
    idx_parts: list[dict[str, Any]] = []
    for ticker in (".IXIC", "SPY", "QQQ", "DIA"):
        row = next((r for r in rows if ticker in (r.get("symbol"), r.get("ticker"), r.get("name"))), None)
        if row:
            if idx_parts:
                idx_parts.append(_home_rt(" · "))
            idx_parts.extend([_home_rt(f"{ticker} ", bold=True), _home_rt(f"{row.get('price', '—')} ({float(row.get('change_pct') or 0):+.2f}%)")])
    if idx_parts:
        eq_lines.append(bullet(*idx_parts))
    movers = [r for r in (eq.get("mag7_and_ai") or []) if r.get("symbol")]
    down_top = sorted(movers, key=lambda r: float(r.get("change_pct") or 0))[:4]
    if down_top:
        mover_parts: list[dict[str, Any]] = [_home_rt("承压领先：", bold=True)]
        for idx, row in enumerate(down_top):
            if idx:
                mover_parts.append(_home_rt(" · "))
            mover_parts.append(_home_rt(f"{row.get('symbol')} {row.get('price', '—')} ({float(row.get('change_pct') or 0):+.2f}%)"))
        eq_lines.append(bullet(*mover_parts))
    sectors = sorted(eq.get("sectors") or [], key=lambda r: _safe_float(r.get("change_pct"), 0.0) or 0.0, reverse=True)
    losers = sectors[3:][-3:] if len(sectors) > 3 else []
    for vals, title in ((sectors[:3], "领涨板块"), (losers, "领跌板块")):
        if vals:
            names = [f"{r.get('name') or r.get('symbol')} ({(_safe_float(r.get('change_pct'), 0.0) or 0.0):+.2f}%)" for r in vals]
            eq_lines.append(bullet(_home_rt(f"{title}：{', '.join(names)}")))

    macro_lines = [bullet(_home_rt(f"{label} ", bold=True), _home_rt(value(mid))) for mid, label in (("fedfunds", "联邦基金利率"), ("us_10y", "美国10年期"), ("vix", "VIX"), ("fed_net_liquidity", "美联储净流动性"), ("cpi_yoy", "CPI同比"), ("btc_spot", "BTC"), ("otc_usdt_premium", "场外USDT溢价"))]

    signals = sorted((s for s in state.get("signals") or [] if s.get("selected")), key=lambda s: float(s.get("attention_score") or 0), reverse=True)[:6]
    signal_lines = []
    for s in signals:
        direction = {"bearish": "利空", "bullish": "利多", "neutral": "中性"}.get(str(s.get("direction") or "").lower(), "中性")
        parts = [_home_rt(f"[{direction}] "), _home_rt(str(s.get("title") or ""), bold=True), _home_rt(f" · {s.get('attention_score', 0)} · {s.get('source') or '来源未标注'}")]
        if s.get("url"):
            parts.append(_home_rt(" · 原文", url=str(s["url"])))
        why = re.sub(r"\s+", " ", str(s.get("why_it_matters") or s.get("summary") or "")).strip()
        if why:
            parts.append(_home_rt(f" · {why[:220]}"))
        signal_lines.append(bullet(*parts))

    corr = state.get("correlations") or {}
    corr_lines = [bullet(_home_rt(f"{p.get('pair') or p.get('id') or ''} · {p.get('divergence') or ''} · "), _home_rt(f"30D {float(p.get('corr_30d') or 0):+.2f} / 90D {float(p.get('corr_90d') or 0):+.2f}", bold=True), _home_rt(f" · {p.get('note') or ''}")) for p in corr.get("pairs") or []]
    kols = state.get("kol_digest") or []
    def kol_lines(stance: str, reverse: bool) -> list[dict[str, Any]]:
        selected = sorted((k for k in kols if k.get("stance") == stance), key=lambda k: float(k.get("score") or 0), reverse=reverse)[:5]
        lines = []
        for k in selected:
            parts = [_home_rt(f"@{str(k.get('handle') or '').lstrip('@')} ", bold=True), _home_rt(f"{k.get('score', 0)} · {k.get('topic') or ''} · {str(k.get('takeaway') or '')[:220]}")]
            if k.get("url"):
                parts.append(_home_rt(" · X 原文", url=str(k["url"])))
            lines.append(bullet(*parts))
        return lines

    sections = {}
    content = [risk_bullets, eq_lines, macro_lines, signal_lines, corr_lines, kol_lines("偏多", True), kol_lines("偏空", False)]
    for prefix, lines in zip(HOME_LIVE_PREFIXES, content):
        sections[prefix] = {"header": f"{prefix} · {bjt} BJT" if prefix == "风控与仓位" else prefix, "bullets": lines}
    # Remove forbidden source fragments so generated lines can never expose internal placeholders.
    for spec in sections.values():
        for line in spec["bullets"]:
            for rt in line["rich_text"]:
                txt = rt.get("text", {}).get("content", "")
                for forbidden in _HOME_FORBIDDEN:
                    txt = txt.replace(forbidden, "")
                rt["text"]["content"] = txt[:1900]
    return sections


def _chunk_markdown_blocks(md_text: str, max_blocks: int = 80) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for raw_ln in str(md_text or "").splitlines():
        if len(blocks) >= max_blocks:
            break
        ln = raw_ln.strip()
        if not ln:
            continue
        if ln.startswith("## "):
            blocks.append({"object": "block", "type": "heading_2", "heading_2": {"rich_text": [{"type": "text", "text": {"content": ln[3:][:1900]}}]}})
        elif ln.startswith("# "):
            blocks.append({"object": "block", "type": "heading_1", "heading_1": {"rich_text": [{"type": "text", "text": {"content": ln[2:][:1900]}}]}})
        elif ln.startswith("- "):
            blocks.append({"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": ln[2:][:1900]}}]}})
        else:
            blocks.append({"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": ln[:1900]}}]}})
    return blocks


def load_notion_token(env_path: Path = HERMES_ENV_PATH) -> str:
    """Resolve NOTION_TOKEN from environment or Hermes .env without logging the value."""
    tok = os.environ.get("NOTION_TOKEN", "").strip().strip("'\"")
    if tok:
        return tok
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("NOTION_TOKEN=") and not s.startswith("#"):
                tok = s.split("=", 1)[1].strip().strip("'\"")
                if tok:
                    return tok
    return ""


def _extract_plain_text(prop_val: dict[str, Any]) -> str:
    ptype = prop_val.get("type")
    if ptype == "rich_text":
        return "".join(t.get("plain_text", "") for t in (prop_val.get("rich_text") or []))
    if ptype == "title":
        return "".join(t.get("plain_text", "") for t in (prop_val.get("title") or []))
    if ptype == "select":
        return str(((prop_val.get("select") or {}).get("name")) or "")
    return ""


class NotionSyncClient:
    """Rate-limited REST client for Notion database sync."""

    def __init__(self, token: str, ids_config: dict[str, Any], min_interval: float = 0.42) -> None:
        self.token = token
        self.ids = ids_config
        self.min_interval = min_interval
        self._last_req_ts = 0.0

    def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None, *, retries: int = 3
    ) -> tuple[int, dict[str, Any]]:
        url = f"{NOTION_API_BASE}{path}"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }
        for attempt in range(retries + 1):
            elapsed = time.monotonic() - self._last_req_ts
            if elapsed < self.min_interval:
                time.sleep(self.min_interval - elapsed)
            self._last_req_ts = time.monotonic()
            req = urllib.request.Request(url, data=body, headers=headers, method=method)
            allow_retry = not (method == "POST" and path == "/pages")
            try:
                with urllib.request.urlopen(req, timeout=20) as resp:
                    raw = resp.read().decode("utf-8")
                    return resp.status, (json.loads(raw) if raw else {})
            except urllib.error.HTTPError as exc:
                raw_err = exc.read().decode("utf-8", errors="ignore")
                try:
                    err_obj = json.loads(raw_err)
                except Exception:
                    err_obj = {"message": raw_err[:300]}
                if exc.code in {429, 500, 502, 503, 504} and attempt < retries and (allow_retry or exc.code == 429):
                    retry_after = exc.headers.get("Retry-After") if exc.headers else None
                    try:
                        wait_s = float(retry_after) if retry_after else (1.0 * (2**attempt))
                    except (TypeError, ValueError):
                        wait_s = 1.0 * (2**attempt)
                    time.sleep(min(max(wait_s, 0.2), 8.0))
                    continue
                return exc.code, err_obj
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt < retries and allow_retry:
                    time.sleep(1.0 * (2**attempt))
                    continue
                reason = getattr(exc, "reason", str(exc))
                return 0, {"message": str(reason)}
        return 0, {"message": "exhausted retries"}

    def check_access(self) -> dict[str, Any]:
        """Verify bot token validity and whether the target Notion home page is shared with it."""
        if not self.token:
            return {
                "token_valid": False,
                "bot_name": "",
                "home_page_connected": False,
                "status_code": 0,
                "message": "NOTION_TOKEN not configured",
            }
        me_code, me_data = self._request("GET", "/users/me")
        if me_code != 200:
            return {
                "token_valid": False,
                "bot_name": "",
                "home_page_connected": False,
                "status_code": me_code,
                "message": f"users/me failed ({me_code}): {me_data.get('message', '')}",
            }
        bot_name = str(me_data.get("name") or "")
        home_id = str(self.ids.get("home_page_id") or "")
        pg_code, pg_data = self._request("GET", f"/pages/{home_id}")
        connected = pg_code == 200
        db1_id = str(((self.ids.get("databases") or {}).get("daily_reports") or {}).get("database_id") or "")
        db_code, _ = self._request("GET", f"/databases/{db1_id}") if db1_id else (0, {})
        msg = (
            "Connected to home page and databases"
            if connected
            else f"Integration '{bot_name}' valid, awaiting Notion UI Connections -> {bot_name} on home page ({pg_code})"
        )
        return {
            "token_valid": True,
            "bot_name": bot_name,
            "home_page_connected": connected,
            "databases_accessible": db_code == 200,
            "status_code": pg_code,
            "message": msg,
        }

    def query_sync_index(
        self, database_id: str, key_prop: str = "同步键", extra_prop: str | None = None
    ) -> dict[str, dict[str, str]]:
        """Build {sync_key: {'page_id': ..., 'extra': ...}} index for a Notion database."""
        index: dict[str, dict[str, str]] = {}
        cursor: str | None = None
        while True:
            payload: dict[str, Any] = {"page_size": 100}
            if cursor:
                payload["start_cursor"] = cursor
            code, data = self._request("POST", f"/databases/{database_id}/query", payload)
            if code != 200:
                raise RuntimeError(f"Notion database query failed ({code}) for {database_id}")
            for row in data.get("results") or []:
                props = row.get("properties") or {}
                key_val = _extract_plain_text(props.get(key_prop) or {})
                if not key_val:
                    continue
                entry = {"page_id": str(row.get("id") or "")}
                if extra_prop:
                    entry["extra"] = _extract_plain_text(props.get(extra_prop) or {})
                index[key_val] = entry
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")
        return index

    def _upsert(
        self,
        db_id: str,
        sync_key: str,
        index: dict[str, dict[str, str]],
        create_props: dict[str, Any],
        update_props: dict[str, Any],
        *,
        children: list[dict[str, Any]] | None = None,
        icon_emoji: str | None = None,
        protect_extra_values: set[str] | None = None,
    ) -> tuple[str, str | None]:
        existing = index.get(sync_key)
        if existing:
            if protect_extra_values and existing.get("extra") in protect_extra_values:
                return "skipped", existing["page_id"]
            code, _ = self._request(
                "PATCH",
                f"/pages/{existing['page_id']}",
                {"properties": strip_user_owned_fields(update_props)},
            )
            return ("updated" if code == 200 else "failed"), existing["page_id"]

        payload: dict[str, Any] = {
            "parent": {"database_id": db_id},
            "properties": create_props,
        }
        if icon_emoji:
            payload["icon"] = {"type": "emoji", "emoji": icon_emoji}
        if children:
            payload["children"] = children
        code, data = self._request("POST", "/pages", payload)
        new_id = str(data.get("id") or "") if code == 200 else None
        if new_id:
            index[sync_key] = {"page_id": new_id}
        return ("created" if code == 200 else "failed"), new_id

    def sync_home_live_sections(self, state: dict[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
        sections = build_home_live_sections_data(state)
        home_id = str(self.ids.get("home_page_id") or "")
        matched: dict[str, dict[str, Any]] = {}
        protected = {"child_database", "linked_database", "embed", "child_page", "toggle"}

        def walk(parent_id: str, depth: int) -> None:
            if depth > 3:
                return
            cursor = None
            while True:
                path = f"/blocks/{parent_id}/children?page_size=100" + (f"&start_cursor={cursor}" if cursor else "")
                code, data = self._request("GET", path)
                if code != 200:
                    return
                for block in data.get("results") or []:
                    kind = block.get("type")
                    if kind in protected:
                        continue
                    if kind == "callout":
                        text = "".join(t.get("plain_text", t.get("text", {}).get("content", "")) for t in ((block.get("callout") or {}).get("rich_text") or []))
                        prefix = next((p for p in HOME_LIVE_PREFIXES if text.startswith(p)), None)
                        if prefix and prefix not in matched:
                            matched[prefix] = block
                    if block.get("has_children") and kind in {"column_list", "column"} and depth < 3:
                        walk(str(block.get("id") or ""), depth + 1)
                if not data.get("has_more"):
                    break
                cursor = data.get("next_cursor")

        if not dry_run:
            walk(home_id, 0)
        else:
            matched = {prefix: {} for prefix in HOME_LIVE_PREFIXES}
        updated = 0
        for prefix, block in matched.items():
            spec = sections[prefix]
            cid = str(block.get("id") or "")
            if dry_run:
                continue
            code, _ = self._request("PATCH", f"/blocks/{cid}", {"callout": {"rich_text": [_home_rt(spec["header"], bold=True)]}})
            if code != 200:
                continue
            old_bullets = []
            if block.get("has_children"):
                c_code, c_data = self._request("GET", f"/blocks/{cid}/children?page_size=100")
                if c_code != 200:
                    continue
                old_bullets = [b for b in c_data.get("results") or [] if b.get("type") == "bulleted_list_item"]
            section_ok = True
            for i, line in enumerate(spec["bullets"]):
                payload = {"bulleted_list_item": line}
                if i < len(old_bullets):
                    b_code, _ = self._request("PATCH", f"/blocks/{old_bullets[i]['id']}", payload)
                else:
                    b_code, _ = self._request("PATCH", f"/blocks/{cid}/children", {"children": [{"object": "block", "type": "bulleted_list_item", "bulleted_list_item": line}]})
                if b_code != 200:
                    section_ok = False
            for extra in old_bullets[len(spec["bullets"]):]:
                a_code, _ = self._request("PATCH", f"/blocks/{extra['id']}", {"archived": True})
                if a_code != 200:
                    section_ok = False
            if section_ok:
                updated += 1
        return {"matched_sections": list(matched), "updated_count": updated, "dry_run": dry_run}

    def sync_all(self, state: dict[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
        """Synchronize DB1-DB6 + home callout idempotently (never touches DB7 or user fields)."""
        dbs = self.ids.get("databases") or {}
        report_date = infer_report_date(state)
        selected_signals = [s for s in (state.get("signals") or []) if s.get("selected")][:20]
        macro_items = list(state.get("macro_fred") or [])
        corr_obj = state.get("correlations") or {}
        corr_pairs = list(corr_obj.get("pairs") or [])
        corr_as_of = str(corr_obj.get("as_of") or report_date)
        kol_items = list(state.get("kol_digest") or [])
        article_items = list(state.get("articles") or [])[:12]
        pos_items = list(state.get("positions") or [])

        planned = {
            "daily_reports": 1,
            "signals": len(selected_signals),
            "macro_correlations": len(macro_items) + len(corr_pairs),
            "kol_stances": len(kol_items),
            "longform_articles": len(article_items),
            "positions_reviews": len(pos_items),
            "quick_notes": 0,
        }
        if dry_run:
            # Verify all builders produce clean REST payloads with native dates and no user fields on update
            _ = build_daily_report_properties(state, is_update=True)
            for s in selected_signals:
                if USER_OWNED_FIELDS.intersection(build_signal_properties(s, report_date=report_date, is_update=True)):
                    raise ValueError("User-owned field leaked in DB2 update payload")
            for m in macro_items:
                _ = build_macro_properties(m, fallback_date=report_date, is_update=True)
            for p in corr_pairs:
                _ = build_correlation_properties(p, corr_as_of, is_update=True)
            for k in kol_items:
                if USER_OWNED_FIELDS.intersection(build_kol_properties(k, fallback_date=report_date, is_update=True)):
                    raise ValueError("User-owned field leaked in DB4 update payload")
            for a in article_items:
                if USER_OWNED_FIELDS.intersection(build_article_properties(a, fallback_date=report_date, is_update=True)):
                    raise ValueError("User-owned field leaked in DB5 update payload")
            for pos in pos_items:
                if USER_OWNED_FIELDS.intersection(build_position_properties(pos, state, is_update=True)):
                    raise ValueError("User-owned field leaked in DB6 update payload")
            live_res = self.sync_home_live_sections(state, dry_run=True)
            return {
                "dry_run": True,
                "connected": None,
                "report_date": report_date,
                "planned": planned,
                "created": 0,
                "updated": 0,
                "skipped": 0,
                "failed": 0,
                "callout_updated": False,
                "live_sections": live_res,
                "live_sections_updated": live_res.get("updated_count", 0),
            }

        access = self.check_access()
        if not access["home_page_connected"]:
            return {
                "dry_run": False,
                "connected": False,
                "access": access,
                "report_date": report_date,
                "planned": planned,
                "created": 0,
                "updated": 0,
                "skipped": 0,
                "failed": 0,
            }

        counts = {"created": 0, "updated": 0, "skipped": 0, "failed": 0}
        db_errors: list[str] = []

        def _tally(res: str) -> None:
            counts[res] = counts.get(res, 0) + 1

        # 1. DB1 daily report
        daily_page_id: str | None = None
        db1_id = dbs["daily_reports"]["database_id"]
        try:
            db1_idx = self.query_sync_index(db1_id, "同步键")
            daily_key = f"daily:{report_date}"
            res1, daily_page_id = self._upsert(
                db1_id,
                daily_key,
                db1_idx,
                build_daily_report_properties(state, is_update=False),
                build_daily_report_properties(state, is_update=True),
                icon_emoji="📰",
            )
            _tally(res1)
        except RuntimeError as exc:
            db_errors.append(str(exc))
            counts["failed"] += 1

        # 2. DB2 signals (sync_key = 事件ID)
        db2_id = dbs["signals"]["database_id"]
        try:
            db2_idx = self.query_sync_index(db2_id, "事件ID")
            for sig in selected_signals:
                eid = str(sig.get("id") or "")
                if not eid:
                    continue
                r, _ = self._upsert(
                    db2_id,
                    eid,
                    db2_idx,
                    build_signal_properties(sig, report_date=report_date, is_update=False),
                    build_signal_properties(sig, report_date=report_date, is_update=True),
                    children=_chunk_markdown_blocks(str(sig.get("summary") or ""), max_blocks=10),
                )
                _tally(r)
        except RuntimeError as exc:
            db_errors.append(str(exc))
            counts["failed"] += len(selected_signals)

        # 3. DB3 macro + correlations
        db3_id = dbs["macro_correlations"]["database_id"]
        try:
            db3_idx = self.query_sync_index(db3_id, "同步键")
            for m in macro_items:
                r, _ = self._upsert(
                    db3_id,
                    f"macro:{m.get('id')}",
                    db3_idx,
                    build_macro_properties(m, fallback_date=report_date, is_update=False),
                    build_macro_properties(m, fallback_date=report_date, is_update=True),
                )
                _tally(r)
            for p in corr_pairs:
                r, _ = self._upsert(
                    db3_id,
                    f"corr:{p.get('id')}",
                    db3_idx,
                    build_correlation_properties(p, corr_as_of, is_update=False),
                    build_correlation_properties(p, corr_as_of, is_update=True),
                )
                _tally(r)
        except RuntimeError as exc:
            db_errors.append(str(exc))
            counts["failed"] += len(macro_items) + len(corr_pairs)

        # 4. DB5 longform articles
        db5_id = dbs["longform_articles"]["database_id"]
        try:
            db5_idx = self.query_sync_index(db5_id, "同步键")
            for art in article_items:
                art_key = f"art:{art.get('filename')}"
                r, _ = self._upsert(
                    db5_id,
                    art_key,
                    db5_idx,
                    build_article_properties(art, fallback_date=report_date, is_update=False),
                    build_article_properties(art, fallback_date=report_date, is_update=True),
                    children=_chunk_markdown_blocks(str(art.get("body_md") or ""), max_blocks=80),
                )
                _tally(r)
        except RuntimeError as exc:
            db_errors.append(str(exc))
            counts["failed"] += len(article_items)

        # 5. DB4 KOL stances
        db4_id = dbs["kol_stances"]["database_id"]
        try:
            db4_idx = self.query_sync_index(db4_id, "同步键")
            for kol in kol_items:
                handle = str(kol.get("handle") or "").lstrip("@")
                d_str = _extract_date_only(str(kol.get("date") or ""), report_date)
                r, _ = self._upsert(
                    db4_id,
                    f"kol:{handle}:{d_str}",
                    db4_idx,
                    build_kol_properties(kol, fallback_date=report_date, is_update=False),
                    build_kol_properties(kol, fallback_date=report_date, is_update=True),
                )
                _tally(r)
        except RuntimeError as exc:
            db_errors.append(str(exc))
            counts["failed"] += len(kol_items)

        # 6. DB6 system position snapshots ONLY (never touch 手动操作复盘)
        db6_id = dbs["positions_reviews"]["database_id"]
        try:
            db6_idx = self.query_sync_index(db6_id, "同步键", extra_prop="记录类型")
            for pos in pos_items:
                pos_key = f"pos:{pos.get('id')}:{report_date}"
                r, _ = self._upsert(
                    db6_id,
                    pos_key,
                    db6_idx,
                    build_position_properties(pos, state, daily_page_id=daily_page_id, is_update=False),
                    build_position_properties(pos, state, daily_page_id=daily_page_id, is_update=True),
                    protect_extra_values={"手动操作复盘"},
                )
                _tally(r)
        except RuntimeError as exc:
            db_errors.append(str(exc))
            counts["failed"] += len(pos_items)

        # 7. Update top live callout on Home Page
        live_res = self.sync_home_live_sections(state, dry_run=False)
        res_out: dict[str, Any] = {
            "dry_run": False,
            "connected": True,
            "report_date": report_date,
            "planned": planned,
            "callout_updated": live_res.get("updated_count", 0) > 0,
            "live_sections": live_res,
            "live_sections_updated": live_res.get("updated_count", 0),
            **counts,
        }
        if db_errors:
            res_out["db_errors"] = db_errors
        return res_out


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="MacroCockpit -> Notion Intelligence Hub sync")
    parser.add_argument("--check-access", action="store_true", help="Check token and page connection")
    parser.add_argument("--dry-run", action="store_true", help="Validate payloads without Notion writes")
    parser.add_argument("--sync-all", action="store_true", help="Sync DB1-DB6 + home callout")
    parser.add_argument("--refresh", action="store_true", help="Rebuild cockpit_state.json before syncing")
    args = parser.parse_args()

    ids_cfg = json.loads(NOTION_IDS_PATH.read_text(encoding="utf-8"))
    token = load_notion_token()
    client = NotionSyncClient(token=token, ids_config=ids_cfg)

    if args.check_access and not (args.dry_run or args.sync_all):
        res = client.check_access()
        print(json.dumps(res, ensure_ascii=False, indent=2))
        sys.exit(0)

    if args.refresh:
        from backend import signal_aggregator

        state = signal_aggregator.generate_cockpit_state(refresh_fred=False, output_path=STATE_PATH)
    else:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))

    res = client.sync_all(state, dry_run=args.dry_run)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
