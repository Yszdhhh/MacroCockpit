"""Periodic weekly/monthly deep research report generator and external Deep Research ingestion."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_BACKEND_DIR = Path(__file__).resolve().parent
if str(_BACKEND_DIR.parent) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR.parent))

from backend.fred_and_market import COCKPIT_ROOT, DATA_DIR
from backend.kol_intel_engine import (
    FORBIDDEN_TERMS,
    MIN_VERIFIED_SAMPLE,
    extract_tickers,
    scrub_forbidden,
)
from backend.public_snapshot import (
    _atomic_write_text,
    has_private_article_content,
    safe_article_filename,
)

BJT = timezone(timedelta(hours=8))
DEFAULT_ARTICLES_DIR = COCKPIT_ROOT / "articles"
DEFAULT_STATE_PATH = DATA_DIR / "cockpit_state.json"
DEFAULT_GEMINI_API_EXE = Path.home() / ".local" / "bin" / "gemini-api.EXE"
PERIODIC_SOURCE_LABEL = "周月度深研"
GEMINI_DR_AGENT = "deep-research-preview-04-2026"

_EXTRA_SCRUB_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bTier-?S\b", re.IGNORECASE), "高优"),
    (re.compile(r"\bTier-?A\b", re.IGNORECASE), "重点"),
    (re.compile(r"E21|E32|E33", re.IGNORECASE), "波动结构"),
    (re.compile(r"Tier-?1", re.IGNORECASE), "核心"),
    (re.compile(r"Sleeves?", re.IGNORECASE), "仓位"),
    (re.compile(r"Regimes?", re.IGNORECASE), "市场阶段"),
    (re.compile(r"arbitration", re.IGNORECASE), "风险平衡"),
    (re.compile(r"测算口径|公式解释|公式说明|逻辑解释|联动说明|方法论说明"), "参考摘要"),
    (re.compile(r"待补充|待补|待刷新"), "—"),
    (re.compile(r"N/A", re.IGNORECASE), "—"),
)

_FRONTMATTER_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)^---[ \t]*(?:\r?\n|$)", re.MULTILINE | re.DOTALL)


def _scrub_forbidden(text: Any) -> str:
    """Scrub all forbidden jargon and placeholders while preserving markdown line breaks."""
    raw = str(text or "").replace("\r\n", "\n")
    s = scrub_forbidden(raw)
    for rx, rep in _EXTRA_SCRUB_PATTERNS:
        s = rx.sub(rep, s)
    lines = [re.sub(r"[ \t]{2,}", " ", ln).rstrip() for ln in s.splitlines()]
    return "\n".join(lines).strip()


def _yaml_quote(value: str) -> str:
    escaped = str(value or "").replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").strip()
    return f'"{escaped}"'


def _resolve_bjt_datetime(as_of: datetime | date | str | None = None) -> datetime:
    if as_of is None:
        return datetime.now(BJT)
    if isinstance(as_of, datetime):
        return as_of.astimezone(BJT) if as_of.tzinfo is not None else as_of.replace(tzinfo=BJT)
    if isinstance(as_of, date):
        return datetime(as_of.year, as_of.month, as_of.day, 9, 0, tzinfo=BJT)
    raw = str(as_of).strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    parsed = datetime.fromisoformat(raw)
    return parsed.astimezone(BJT) if parsed.tzinfo is not None else parsed.replace(tzinfo=BJT)


def _fred_lookup(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for item in state.get("macro_fred") or []:
        if isinstance(item, dict) and item.get("id"):
            out[str(item["id"]).strip()] = item
    return out


def _fmt_fred_item(fmap: dict[str, dict[str, Any]], key: str, default: str = "—") -> str:
    item = fmap.get(key)
    if not isinstance(item, dict):
        return default
    val = str(item.get("value_display") or "").strip()
    chg = str(item.get("change_display") or "").strip()
    if not val:
        return default
    return f"{val}（{chg}）" if chg else val


def _equities_lookup(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    eq = state.get("us_equities")
    if not isinstance(eq, dict):
        return out
    for bucket in ("indices", "mag7_and_ai", "sectors"):
        for row in eq.get(bucket) or []:
            if isinstance(row, dict) and row.get("symbol"):
                out[str(row["symbol"]).strip().upper()] = row
    return out


def _fmt_pct(val: Any) -> str:
    try:
        num = float(val)
    except (TypeError, ValueError):
        return "—"
    if num != num:
        return "—"
    sign = "+" if num > 0 else ""
    return f"{sign}{num:.2f}%"


def _fmt_price(val: Any, prefix: str = "$") -> str:
    try:
        num = float(val)
    except (TypeError, ValueError):
        return "—"
    if num != num:
        return "—"
    if num >= 1000:
        return f"{prefix}{num:,.2f}" if num % 1 else f"{prefix}{num:,.0f}"
    if num >= 1:
        return f"{prefix}{num:.2f}"
    return f"{prefix}{num:.4f}"


def _build_yaml_frontmatter(
    *,
    title: str,
    summary: str,
    publisher: str,
    author_role: str,
    report_kind: str,
    score: int,
    tickers: list[str],
    url: str = "",
    related_period: str = "",
) -> str:
    lines = [
        "---",
        f"title: {_yaml_quote(title)}",
        f"summary: {_yaml_quote(summary)}",
        f"publisher: {_yaml_quote(publisher)}",
        f"author_role: {_yaml_quote(author_role)}",
        f"report_kind: {_yaml_quote(report_kind)}",
        f"score: {int(score)}",
    ]
    if tickers:
        tickers_yaml = ", ".join(_yaml_quote(t) for t in tickers[:8])
        lines.append(f"tickers: [{tickers_yaml}]")
    else:
        lines.append("tickers: []")
    if url and url.startswith(("http://", "https://")):
        lines.append(f"url: {_yaml_quote(url)}")
    if related_period:
        lines.append(f"related_period: {_yaml_quote(related_period)}")
    lines.append("---")
    return "\n".join(lines)


def _strip_frontmatter(raw_markdown: str) -> tuple[dict[str, Any], str]:
    text = str(raw_markdown or "").replace("\r\n", "\n").lstrip("\ufeff")
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text.strip()
    fm_raw = match.group(1)
    body = text[match.end() :].strip()
    meta: dict[str, Any] = {}
    try:
        import yaml

        parsed = yaml.safe_load(fm_raw)
        if isinstance(parsed, dict):
            meta = parsed
    except Exception:
        meta = {}
    return meta, body


def _parse_article_dict(
    *,
    filename: str,
    title: str,
    summary: str,
    publisher: str,
    author_role: str,
    report_kind: str,
    score: int,
    tickers: list[str],
    body_md: str,
    mtime_str: str,
    url: str = "",
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "filename": filename,
        "title": _scrub_forbidden(title),
        "source": PERIODIC_SOURCE_LABEL,
        "mtime": mtime_str,
        "excerpt": _scrub_forbidden(summary or title)[:180],
        "body_md": _scrub_forbidden(body_md),
        "publisher": _scrub_forbidden(publisher),
        "author_role": _scrub_forbidden(author_role),
        "report_kind": report_kind,
        "score": int(score),
        "tickers": list(tickers[:8]),
    }
    if url and url.startswith(("http://", "https://")):
        out["url"] = url
    return out


def _read_existing_periodic_article(path: Path, report_kind: str, default_publisher: str) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    meta, body = _strip_frontmatter(raw)
    title = str(meta.get("title") or "").strip()
    if not title:
        for ln in body.splitlines():
            s = ln.strip()
            if s.startswith("# "):
                title = s[2:].strip()
                break
    title = title or path.stem
    summary = str(meta.get("summary") or "").strip()
    if not summary:
        for ln in body.splitlines():
            s = ln.strip()
            if s and not s.startswith(("#", "-", "*", ">", "```", "|")):
                summary = s
                break
    raw_tickers = meta.get("tickers")
    tickers = (
        [str(t).strip().upper() for t in raw_tickers if str(t).strip()][:8]
        if isinstance(raw_tickers, list)
        else extract_tickers(f"{title}\n{body}")
    )
    score_val = meta.get("score")
    score = int(round(float(score_val))) if isinstance(score_val, (int, float)) and not isinstance(score_val, bool) else 92
    mtime_str = datetime.fromtimestamp(path.stat().st_mtime, tz=BJT).strftime("%Y-%m-%d %H:%M")
    return _parse_article_dict(
        filename=path.name,
        title=title,
        summary=summary,
        publisher=str(meta.get("publisher") or default_publisher),
        author_role=str(meta.get("author_role") or "周期辅助决策"),
        report_kind=str(meta.get("report_kind") or report_kind),
        score=score,
        tickers=tickers,
        body_md=body,
        mtime_str=mtime_str,
        url=str(meta.get("url") or ""),
    )


def _collect_top_tickers(state: dict[str, Any]) -> list[str]:
    counts: dict[str, int] = {}
    for link in (state.get("market_analysis") or {}).get("cross_source_links") or []:
        if isinstance(link, dict):
            for t in link.get("tickers") or []:
                sym = str(t).strip().upper()
                if sym:
                    counts[sym] = counts.get(sym, 0) + 3
    for card in state.get("kol_scorecards") or []:
        if isinstance(card, dict) and int(card.get("verified_calls") or 0) >= MIN_VERIFIED_SAMPLE:
            for t in card.get("best_tickers") or []:
                sym = str(t).strip().upper()
                if sym:
                    counts[sym] = counts.get(sym, 0) + 2
    for sig in state.get("signals") or []:
        if isinstance(sig, dict):
            for t in sig.get("tickers") or []:
                sym = str(t).strip().upper()
                if sym:
                    counts[sym] = counts.get(sym, 0) + 1
    ranked = [sym for sym, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    for fallback in ("NVDA", "SPY", "QQQ", "BTC", "GOLD"):
        if len(ranked) >= 8:
            break
        if fallback not in ranked:
            ranked.append(fallback)
    return ranked[:8]


def _extract_verified_kols(state: dict[str, Any], limit: int = 8) -> list[dict[str, Any]]:
    cards = [
        c
        for c in (state.get("kol_scorecards") or [])
        if isinstance(c, dict)
        and int(c.get("verified_calls") or 0) >= MIN_VERIFIED_SAMPLE
        and c.get("win_rate_20d") is not None
    ]
    cards.sort(
        key=lambda c: (
            float(c.get("reliability_score") or 0),
            float(c.get("win_rate_20d") or 0),
            int(c.get("verified_calls") or 0),
        ),
        reverse=True,
    )
    return cards[:limit]


def _summarize_kol_consensus(state: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    digest = [d for d in (state.get("kol_digest") or []) if isinstance(d, dict)]
    bulls = [d for d in digest if str(d.get("stance") or "") == "偏多"]
    bears = [d for d in digest if str(d.get("stance") or "") == "偏空"]
    neutrals = [d for d in digest if str(d.get("stance") or "") not in ("偏多", "偏空")]
    for bucket in (bulls, bears, neutrals):
        bucket.sort(
            key=lambda d: (
                int(d.get("verified_calls") or 0) >= MIN_VERIFIED_SAMPLE,
                float(d.get("reliability_score") or 0),
                abs(float(d.get("score") or 0)),
            ),
            reverse=True,
        )
    return bulls, bears, neutrals


def _format_kol_line(item: dict[str, Any]) -> str:
    handle = str(item.get("handle") or "").strip().lstrip("@")
    group = str(item.get("group") or "市场观点").strip()
    headline = _scrub_forbidden(item.get("takeaway") or item.get("headline") or "").strip()
    headline = re.sub(r"^\[[^\]]+\]\s*", "", headline)
    if len(headline) > 110:
        headline = headline[:107].rstrip() + "..."
    vc = int(item.get("verified_calls") or 0)
    wr20 = item.get("win_rate_20d")
    if vc >= MIN_VERIFIED_SAMPLE and isinstance(wr20, (int, float)):
        cred = f"20日胜率 {float(wr20):.1f}% / {vc}次核验"
    else:
        cred = "样本积累"
    return f"- **@{handle}**（{group} · {cred}）：{headline}"


def _build_deep_research_prompts(
    *,
    period_label: str,
    as_of_date: str,
    fmap: dict[str, dict[str, Any]],
    eq_map: dict[str, dict[str, Any]],
    crypto_spot: dict[str, Any],
    risk_gate: dict[str, Any],
    links: list[dict[str, Any]],
    forum_sentiment: dict[str, Any] | None,
    source_matrix: dict[str, Any] | None,
) -> list[tuple[str, str]]:
    """Build 3 to 4 independent copy-ready Deep Research prompts grounded in current state."""
    us10y = _fmt_fred_item(fmap, "us_10y")
    us2y = _fmt_fred_item(fmap, "us_2y")
    cpi = _fmt_fred_item(fmap, "cpi_yoy")
    net_liq = _fmt_fred_item(fmap, "fed_net_liquidity")
    vix = _fmt_fred_item(fmap, "vix")
    dxy = _fmt_fred_item(fmap, "dollar")
    gold = _fmt_fred_item(fmap, "gold")
    wti = _fmt_fred_item(fmap, "wti")

    nvda = eq_map.get("NVDA") or {}
    mu = eq_map.get("MU") or {}
    tsm = eq_map.get("TSM") or {}
    spy = eq_map.get("SPY") or {}
    qqq = eq_map.get("QQQ") or {}
    btc = crypto_spot.get("BTC-USDT") or {}
    eth = crypto_spot.get("ETH-USDT") or {}

    risk_label = str(risk_gate.get("label") or "稳健中性")
    prompts: list[tuple[str, str]] = []

    # Prompt 1: Macro liquidity & rates ceiling
    p1 = "\n".join(
        [
            f"研究主题：美国长端利率高位与美元净流动性对风险资产估值的约束评估（截至 {as_of_date}，{period_label}）",
            "",
            "一、当前盘面与宏观实测背景：",
            f"- 10年期美债收益率 {us10y}，2年期美债 {us2y}，CPI同比 {cpi}；",
            f"- 美联储净流动性 {net_liq}，美元广义指数 {dxy}，VIX 恐慌指数 {vix}；",
            f"- 美股标普500 ETF { _fmt_price(spy.get('price')) }（{_fmt_pct(spy.get('change_pct'))}），纳指100 ETF { _fmt_price(qqq.get('price')) }（{_fmt_pct(qqq.get('change_pct'))}）；当前风控状态：{risk_label}。",
            "",
            "二、请重点核实与深挖：",
            "1. 结合未来 4~8 周美国财政部 TGA 账户余额变化、季度再融资发债（QRA）节奏与美联储隔夜逆回购（ON RRP）余量，判断美元净流动性是否会出现实质性收缩或企稳回流。",
            "2. 关税政策与能源价格（WTI 原油 " + wti + "）对未来 2 个月美国核心 PCE / CPI 的滞后传导幅度，找出市场忽略的二次通胀反例。",
            "3. 在 10Y 美债处于 4.90%~5.20% 区间时，历史上纳指估值与盈利增速的匹配上限是多少？给出明确的证伪标准。",
            "",
            "三、输出要求与决策阈值：",
            "- 列出支持「利率见顶回落」与「二次紧缩冲击」的硬数据对照表（附一手美联储/财政部出处）；",
            "- 给出触发风险降仓/解除警戒的具体观测阈值：例如 10Y 美债跌破 4.75% 且 VIX 回落至 22 以下，或 10Y 突破 5.25% 且净流动性跌破 $5.6T。",
        ]
    )
    prompts.append(("课题一：美元流动性拐点与美债 10Y 利率压制上限", p1))

    # Prompt 2: AI compute capex & US-China supply chain
    a_share_hint = "胜宏科技(300476.SZ)、工业富联(601138.SH)、中际旭创(300308.SZ)"
    if isinstance(source_matrix, dict):
        for cat in source_matrix.get("categories") or []:
            if isinstance(cat, dict) and cat.get("id") == "us_ai_semi":
                bridges = cat.get("supply_chain_bridges") or []
                names = []
                for b in bridges[:3]:
                    for a in (b.get("a_shares") or [])[:2]:
                        if a.get("name") and a.get("symbol"):
                            names.append(f"{a['name']}({a['symbol']})")
                if names:
                    a_share_hint = "、".join(names[:5])
                break
    p2 = "\n".join(
        [
            f"研究主题：AI 算力资本开支周期与美股/A股供应链瓶颈核实（截至 {as_of_date}）",
            "",
            "一、当前市场分歧与价格表现：",
            f"- 算力权重近期波动放大：NVDA { _fmt_price(nvda.get('price')) }（{_fmt_pct(nvda.get('change_pct'))}），TSM { _fmt_price(tsm.get('price')) }（{_fmt_pct(tsm.get('change_pct'))}），MU { _fmt_price(mu.get('price')) }（{_fmt_pct(mu.get('change_pct'))}）；",
            f"- 跨源关联显示 AI 算力链出现多空拉锯，联动 A 股核心映射包括：{a_share_hint}。",
            "",
            "二、请重点核实与深挖：",
            "1. 对比微软、谷歌、Meta、亚马逊及甲骨文最新财报披露的 CapEx 指引与实际采购承诺（Purchase Commitments），区分「真实推理/训练需求」与「重复下单泡沫」。",
            "2. 核查台积电 CoWoS 先进封装、HBM3e/HBM4 存储良率及数据中心电力变压器交付周期，哪一环是未来 2 个季度的真实刚性瓶颈？",
            "3. 梳理近期看空算力折旧与 ROIC 回报率不足的机构反例，评估其对英伟达与存储板块盈利预期下修的潜在冲击。",
            "",
            "三、输出要求与决策阈值：",
            "- 给出算力供应链景气度验证清单（晶圆代工月度营收、云厂资本开支同比增速、HBM 合约价走势）；",
            "- 明确中左侧/右侧介入与回避边界：如云厂 CapEx 增速维持 25%+ 且库存周转未恶化则维持核心配置，若出现大客户砍单信号或单季指引下修则收紧多头暴露。",
        ]
    )
    prompts.append(("课题二：AI 算力资本开支持续性与中美供应链瓶颈核查", p2))

    # Prompt 3: Crypto cycle vs Gold safe-haven & altcoin fragility
    btc_px = _fmt_price(btc.get("price")) if btc else _fmt_fred_item(fmap, "btc_spot")
    btc_chg = _fmt_pct(btc.get("change_24h_pct")) if btc else "—"
    eth_px = _fmt_price(eth.get("price")) if eth else "—"
    p3 = "\n".join(
        [
            f"研究主题：比特币流动性Beta属性切换、山寨币尾部脆弱性与黄金避险对比（截至 {as_of_date}）",
            "",
            "一、当前资产定价背景：",
            f"- BTC 现价 {btc_px}（24h {btc_chg}），ETH {eth_px}，黄金 {gold}，VIX {vix}；",
            "- 近期加密资产与美股科技股联动偏高，而高估值山寨币整体跑输比特币，风险控制模型维持谨慎配对防线。",
            "",
            "二、请重点核实与深挖：",
            "1. 统计过去 30 天美国比特币/以太坊现货 ETF 净申赎、稳定币（USDT/USDC）总市值增速与 CME 机构期货基差变化，判断是增量资金进场还是空头回补反弹。",
            "2. 核查未来 60 天主流山寨币大额代币解锁（Token Unlocks）与永续合约资金费率结构，评估「多 BTC / 空高流动性山寨篮子」配对策略的失效风险（如山寨暴力补涨轧空）。",
            "3. 对比央行购金、美债实际利率与比特币在滞胀/避险场景下的历史回撤表现，解释为何黄金现阶段相对加密资产呈现更强防御性。",
            "",
            "三、输出要求与决策阈值：",
            "- 给出 BTC 重新确立右侧多头趋势的 3 项硬指标（如站稳 50/120 日均线、ETF 连续 5 日净流入超 $500M、稳定币周度净增发转正）；",
            "- 给出配对对冲仓止损/降仓的尾部预警阈值（如山寨币相对 BTC 7日超额反弹超过 12% 或全网资金费率极度负值）。",
        ]
    )
    prompts.append(("课题三：BTC 资金面真伪、山寨尾部风险与黄金防御性对照", p3))

    # Prompt 4: Sentiment divergence / cross-source conflict
    div_topic = ""
    if isinstance(forum_sentiment, dict) and forum_sentiment.get("divergences"):
        divs = [
            d for d in forum_sentiment["divergences"]
            if isinstance(d, dict) and d.get("street_score") is not None and d.get("street_heat") is not None
        ]
        divs.sort(key=lambda d: abs(float(d.get("street_score") or 0) - float(d.get("desk_score") or 0)), reverse=True)
        if divs:
            top_d = divs[0]
            div_topic = (
                f"当前「{top_d.get('topic')}」赛道出现「{top_d.get('divergence_tag')}」背离"
                f"（专业层评分 {top_d.get('desk_score')} vs 散户层评分 {top_d.get('street_score')}，散户热度 {top_d.get('street_heat')}）"
            )
    if not div_topic and links:
        top_l = links[0]
        div_topic = f"当前「{top_l.get('focus')}」呈现「{top_l.get('pattern')}」（可信度 {top_l.get('confidence')}%）"
    if div_topic:
        p4 = "\n".join(
            [
                f"研究主题：专业机构与散户舆论背离的反转信号检验（截至 {as_of_date}）",
                "",
                "一、当前跨源分歧现象：",
                f"- {div_topic}；",
                "- 需要检验该背离是「散户情绪过热引发均值回归」，还是「产业基本面拐点领先机构认知」。",
                "",
                "二、请重点核实与深挖：",
                "1. 检索过去 2 周机构买方持仓报告、期权看跌/看涨偏度（Put/Call Skew）与暗池大宗成交变化，核实聪明钱是在逢高派发还是悄然吸筹。",
                "2. 对比雪球、Reddit 与中文财经热榜的高频讨论焦点，识别是否存在单一事件驱动的短期情绪错杀或FOMO挤兑。",
                "3. 找出历史上类似「专业谨慎 vs 散户狂热（或极度恐慌）」背离后 20 个交易日的真实胜率与最大回撤分布。",
                "",
                "三、输出要求与决策阈值：",
                "- 给出区分「趋势启动」与「情绪诱多/诱空」的 3 个盘面确认条件与明确时间窗口（5日/20日）。",
            ]
        )
        prompts.append(("课题四：机构与散户情绪背离的均值回归检验", p4))

    return prompts


def generate_periodic_decision_report(
    state: dict[str, Any],
    *,
    period: str = "weekly",
    articles_dir: Path | str | None = None,
    as_of: datetime | date | str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Aggregate cockpit state into a 5-section weekly or monthly decision research Markdown report."""
    norm_period = str(period or "weekly").strip().lower()
    if norm_period not in ("weekly", "monthly"):
        raise ValueError(f"不支持的报告周期: {period!r}，仅支持 'weekly' 或 'monthly'")

    if not isinstance(state, dict):
        raise ValueError("state 必须为字典")

    dt = _resolve_bjt_datetime(as_of)
    date_str = dt.strftime("%Y-%m-%d")
    if norm_period == "weekly":
        iso_year, iso_week, _ = dt.isocalendar()
        filename = f"weekly_{iso_year}-W{iso_week:02d}.md"
        period_tag = f"{iso_year}-W{iso_week:02d}"
        period_label = f"{iso_year}年第{iso_week:02d}周"
        publisher = "本地深研周报"
        title = f"{period_label}宏观与多资产深研决策周报（{date_str}）"
    else:
        filename = f"monthly_{dt.year:04d}-{dt.month:02d}.md"
        period_tag = f"{dt.year:04d}-{dt.month:02d}"
        period_label = f"{dt.year}年{dt.month:02d}月"
        publisher = "本地深研月报"
        title = f"{period_label}宏观与多资产深研决策月报（{date_str}）"

    if not safe_article_filename(filename):
        raise ValueError(f"非法报告文件名: {filename}")

    target_dir = Path(articles_dir) if articles_dir is not None else DEFAULT_ARTICLES_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    dest = target_dir / filename

    if dest.exists() and not force:
        return _read_existing_periodic_article(dest, norm_period, publisher)

    fmap = _fred_lookup(state)
    eq_map = _equities_lookup(state)
    crypto_spot = (state.get("crypto_quotes") or {}).get("spot") or {}
    risk_gate = state.get("risk_gate") or {}
    positions = [p for p in (state.get("positions") or []) if isinstance(p, dict)]
    market_analysis = state.get("market_analysis") or {}
    dims = [d for d in (market_analysis.get("market_dimensions") or []) if isinstance(d, dict)]
    links = [l for l in (market_analysis.get("cross_source_links") or []) if isinstance(l, dict)]
    verified_kols = _extract_verified_kols(state)
    bulls, bears, neutrals = _summarize_kol_consensus(state)
    articles = [a for a in (state.get("articles") or []) if isinstance(a, dict)]
    forum_sentiment = state.get("forum_sentiment") if isinstance(state.get("forum_sentiment"), dict) else None
    source_matrix = state.get("asset_source_matrix") if isinstance(state.get("asset_source_matrix"), dict) else None
    cal_events = [e for e in (state.get("calendar_events") or []) if isinstance(e, dict)]
    corr_pairs = [c for c in ((state.get("correlations") or {}).get("pairs") or []) if isinstance(c, dict)]

    top_tickers = _collect_top_tickers(state)
    risk_label = _scrub_forbidden(risk_gate.get("label") or "稳健观察")
    long_cap_raw = risk_gate.get("long_risk_cap")
    long_cap_pct = f"{float(long_cap_raw) * 100:.0f}%" if isinstance(long_cap_raw, (int, float)) else "—"
    exposure_summary = _scrub_forbidden(risk_gate.get("summary_exposure") or "按趋势确认与风控灯号动态调整")
    headline_verdict = _scrub_forbidden(market_analysis.get("headline_verdict") or "")

    # ── Section 1: 本期定调与仓位风控结论 ──
    sec1: list[str] = [
        "## 1. 本期定调与仓位风控结论",
        "",
        f"> 生成日期：{date_str}（北京时间）｜ 周期标识：{period_tag} ｜ 当前风控灯号：**{risk_label}** ｜ 方向性多头上限：**{long_cap_pct}**",
        "",
    ]
    if headline_verdict:
        sec1.append(f"**全局定调**：{headline_verdict}")
        sec1.append("")
    sec1.extend(
        [
            "### 宏观流动性周期与关键水位",
            f"- **利率与通胀锚**：联邦基金利率 {_fmt_fred_item(fmap, 'fedfunds')}｜10年期美债 {_fmt_fred_item(fmap, 'us_10y')}｜2年期美债 {_fmt_fred_item(fmap, 'us_2y')}｜10Y-2Y 利差 {_fmt_fred_item(fmap, 'us_10y_2y_spread')}｜美国 CPI 同比 {_fmt_fred_item(fmap, 'cpi_yoy')}。",
            f"- **流动性与波动率**：美联储净流动性 {_fmt_fred_item(fmap, 'fed_net_liquidity')}｜美元广义指数 {_fmt_fred_item(fmap, 'dollar')}｜VIX 恐慌指数 {_fmt_fred_item(fmap, 'vix')}｜WTI 原油 {_fmt_fred_item(fmap, 'wti')}｜黄金 {_fmt_fred_item(fmap, 'gold')}。",
        ]
    )
    if dims:
        sec1.append("")
        sec1.append("### 市场四维诊断")
        for d in dims[:4]:
            sec1.append(
                f"- **{d.get('name', '维度')}（{d.get('status', '跟踪')}）**：{d.get('metric', '')} —— {d.get('implication', '')}"
            )
    sec1.extend(
        [
            "",
            "### 辅助仓位比例基准（仅展示公共比例与纪律，不含私人资金数额）",
            f"- **组合总约束**：{exposure_summary}",
        ]
    )
    for reason in (risk_gate.get("reasons") or [])[:4]:
        sec1.append(f"- **风控触发依据**：{_scrub_forbidden(reason)}")
    if positions:
        sec1.append("")
        sec1.append("| 策略模块 | 当前状态 | 基准比例 | 建议比例 | 执行纪律摘要 |")
        sec1.append("|---|---|---:|---:|---|")
        for pos in positions:
            pname = _scrub_forbidden(pos.get("name") or pos.get("id") or "策略模块")
            pstate = _scrub_forbidden(pos.get("state") or "跟踪")
            bpct = f"{float(pos.get('baseline_pct') or 0):.1f}%"
            rpct = f"{float(pos.get('recommended_pct') or 0):.1f}%"
            pnote = _scrub_forbidden(pos.get("note") or "按规则执行").replace("|", "｜")
            if len(pnote) > 72:
                pnote = pnote[:69].rstrip() + "..."
            sec1.append(f"| {pname} | {pstate} | {bpct} | {rpct} | {pnote} |")

    # ── Section 2: 高胜率 KOL 共识 vs 全网舆论温度对照 ──
    sec2: list[str] = [
        "## 2. 高胜率 KOL 共识 vs 全网舆论温度对照",
        "",
        "### 实测高胜率 KOL 跟踪榜（仅展示核验样本 ≥ 5 次的作者）",
    ]
    if verified_kols:
        sec2.append("")
        sec2.append("| 博主 | 领域定位 | 核验次数 | 5日胜率 | 20日胜率 | 擅长标的 | 最新态度 |")
        sec2.append("|---|---|---:|---:|---:|---|---|")
        for card in verified_kols:
            handle = str(card.get("handle") or "").lstrip("@")
            role_lbl = _scrub_forbidden(card.get("role_label") or card.get("category_label") or "市场研究")
            vc = int(card.get("verified_calls") or 0)
            wr5 = f"{float(card['win_rate_5d']):.1f}%" if isinstance(card.get("win_rate_5d"), (int, float)) else "—"
            wr20 = f"{float(card['win_rate_20d']):.1f}%" if isinstance(card.get("win_rate_20d"), (int, float)) else "—"
            bt = ", ".join(str(x) for x in (card.get("best_tickers") or [])[:3]) or "综合"
            stance = _scrub_forbidden(card.get("latest_stance") or "中性")
            sec2.append(f"| @{handle} | {role_lbl} | {vc} | {wr5} | {wr20} | {bt} | {stance} |")
    else:
        sec2.append("- 本期暂无达到 5 次历史核验门槛的作者样本，所有胜率评估保持「样本积累」定性跟踪。")

    sec2.extend(["", "### 专业观点共识与多空分歧"])
    if bulls:
        sec2.append(f"**偏多主线阵营（{len(bulls)} 条高信号观点）**：")
        for item in bulls[:4]:
            sec2.append(_format_kol_line(item))
    else:
        sec2.append("**偏多主线阵营**：当前高信噪 KOL 中明确右侧偏多信号较少，多集中在局部细分业绩兑现。")

    sec2.append("")
    if bears:
        sec2.append(f"**谨慎/看空防御阵营（{len(bears)} 条高信号观点）**：")
        for item in bears[:4]:
            sec2.append(_format_kol_line(item))
    else:
        sec2.append("**谨慎/看空防御阵营**：当前谨慎阵营主要聚焦高利率持续性与估值消化压力。")

    if neutrals:
        sec2.append("")
        sec2.append(f"**结构跟踪/中性观察（{len(neutrals)} 条）**：")
        for item in neutrals[:2]:
            sec2.append(_format_kol_line(item))

    sec2.extend(["", "### 全网舆论温度对照（专业层 vs 散户社区）"])
    if forum_sentiment:
        temp = forum_sentiment.get("overall_crowd_temp")
        olbl = _scrub_forbidden(forum_sentiment.get("overall_label") or "情绪平稳")
        temp_str = f"{temp}/100" if isinstance(temp, (int, float)) else "—"
        sec2.append(f"- **全网散户综合温度**：**{temp_str}（{olbl}）**。")
        channels = [c for c in (forum_sentiment.get("channels_status") or []) if isinstance(c, dict)]
        if channels:
            ch_parts = [
                f"{_scrub_forbidden(c.get('name') or c.get('id'))}·{_scrub_forbidden(c.get('status'))}({int(c.get('count') or 0)}条)"
                for c in channels[:6]
            ]
            sec2.append(f"- **平台覆盖状态**：{' ｜ '.join(ch_parts)}")
        mp = forum_sentiment.get("market_pulse") if isinstance(forum_sentiment.get("market_pulse"), dict) else {}
        mp_parts: list[str] = []
        if mp.get("a_turnover_display"):
            mp_parts.append(f"沪深成交 {mp['a_turnover_display']}")
        if mp.get("rzrq_balance_display"):
            rz_txt = f"两融余额 {mp['rzrq_balance_display']}"
            if isinstance(mp.get("rzrq_buy_yi"), (int, float)):
                rz_txt += f"（单日融资买入 {mp['rzrq_buy_yi']:.0f}亿）"
            mp_parts.append(rz_txt)
        if isinstance(mp.get("southbound_net_yi_hkd"), (int, float)):
            mp_parts.append(f"南向净流向 {mp['southbound_net_yi_hkd']:+.1f}亿港元")
        if isinstance(mp.get("mass_penetration_pct"), (int, float)) and mp.get("mass_total_slots"):
            mp_parts.append(
                f"大众热榜财经占榜 {mp['mass_penetration_pct']:.1f}% ({int(mp.get('mass_finance_hits') or 0)}/{int(mp['mass_total_slots'])}条)"
            )
        if mp_parts:
            sec2.append(f"- **A股/港股资金与破圈大盘**：{' ｜ '.join(mp_parts)}")
        divs = [d for d in (forum_sentiment.get("divergences") or []) if isinstance(d, dict)]
        if divs:
            sec2.append("")
            sec2.append("| 核心赛道 | 专业层评分/倾向 | 散户层评分/热度 | 背离标签 | 应对提示 |")
            sec2.append("|---|---|---|---:|---|")
            for dv in divs[:6]:
                topic = _scrub_forbidden(dv.get("topic") or "")
                d_cnt = dv.get("desk_count")
                d_suffix = f"·{int(d_cnt)}条" if isinstance(d_cnt, int) and d_cnt > 0 else ""
                d_info = f"{dv.get('desk_score', 0)}（{_scrub_forbidden(dv.get('desk_bias') or '中性')}{d_suffix}）"
                s_cnt = dv.get("street_count")
                s_suffix = f"·{int(s_cnt)}条" if isinstance(s_cnt, int) and s_cnt > 0 else ""
                s_info = (
                    f"{dv['street_score']} / 热度 {dv['street_heat']}{s_suffix}"
                    if dv.get("street_score") is not None and dv.get("street_heat") is not None
                    else "暂无样本"
                )
                dtag = _scrub_forbidden(dv.get("divergence_tag") or "情绪平稳")
                hint = _scrub_forbidden(dv.get("action_hint") or "").replace("|", "｜")
                sec2.append(f"| {topic} | {d_info} | {s_info} | {dtag} | {hint} |")
            # Highlight top street samples
            sample_lines: list[str] = []
            for dv in divs[:4]:
                for ss in (dv.get("street_samples") or [])[:1]:
                    if isinstance(ss, dict) and ss.get("title"):
                        sample_lines.append(
                            f"- [{_scrub_forbidden(ss.get('platform') or '社区')}] {_scrub_forbidden(ss.get('title'))}"
                        )
            if sample_lines:
                sec2.append("")
                sec2.append("**散户层代表热议采样**：")
                sec2.extend(sample_lines[:4])
    else:
        sec2.append("- 当前快照未附带社区热榜温度切片，本期以高胜率 KOL 与宏观盘面交错验证为主；刷新社区情绪采集后将自动对照散户过热与恐慌背离。")

    # ── Section 3: 主线资产、产业传导与关键数据复盘 ──
    sec3: list[str] = [
        "## 3. 主线资产、产业传导与关键数据复盘",
        "",
        "### 核心股指、科技权重与加密资产复盘",
    ]
    eq_summary = _scrub_forbidden((state.get("us_equities") or {}).get("summary") or "")
    if eq_summary:
        sec3.append(f"- **美股盘面综述**：{eq_summary}")
    idx_items = []
    for sym in ("SPY", "QQQ", "DIA", ".IXIC"):
        if sym in eq_map:
            row = eq_map[sym]
            idx_items.append(f"{row.get('name') or sym} {_fmt_price(row.get('price'))}（{_fmt_pct(row.get('change_pct'))}）")
    if idx_items:
        sec3.append(f"- **主要指数表现**：{' ｜ '.join(idx_items)}")

    leader_items = []
    for sym in ("NVDA", "MSFT", "GOOG", "META", "AMZN", "AAPL", "TSLA", "AVGO", "TSM", "MU"):
        if sym in eq_map:
            row = eq_map[sym]
            leader_items.append(f"{sym} {_fmt_price(row.get('price'))}（{_fmt_pct(row.get('change_pct'))}）")
    if leader_items:
        sec3.append(f"- **核心算力与七巨头**：{' ｜ '.join(leader_items)}")

    crypto_items = []
    for inst in ("BTC-USDT", "ETH-USDT", "SOL-USDT"):
        row = crypto_spot.get(inst)
        if isinstance(row, dict):
            sym = row.get("symbol") or inst.split("-")[0]
            crypto_items.append(f"{sym} {_fmt_price(row.get('price'))}（24h {_fmt_pct(row.get('change_24h_pct'))}）")
    if crypto_items:
        sec3.append(f"- **加密现货报价**：{' ｜ '.join(crypto_items)}")

    if corr_pairs:
        corr_parts = []
        for p in corr_pairs[:4]:
            if p.get("corr_30d") is None:
                continue
            cname = _scrub_forbidden(p.get("pair") or p.get("label") or p.get("id"))
            csig = _scrub_forbidden(p.get("divergence") or p.get("signal") or "").strip()
            corr_parts.append(f"{cname} {p.get('corr_30d')}" + (f"（{csig}）" if csig else ""))
        if corr_parts:
            sec3.append(f"- **跨资产 30 日相关性**：{' ｜ '.join(corr_parts)}")

    if links:
        sec3.extend(["", "### 跨源共振与主线传导"])
        for link in links[:5]:
            t_str = ", ".join(str(t) for t in (link.get("tickers") or [])[:5])
            trigger = _scrub_forbidden(link.get("watch_trigger") or link.get("action_trigger") or "").strip()
            mcheck = _scrub_forbidden(link.get("market_check") or "").strip()
            extra_parts = []
            if mcheck:
                extra_parts.append(f"盘面校验：{mcheck}")
            if trigger:
                extra_parts.append(f"关键观察：{trigger}")
            extra_str = (" " + "；".join(extra_parts)) if extra_parts else ""
            sec3.append(
                f"- **{_scrub_forbidden(link.get('focus'))}**（{t_str} · {_scrub_forbidden(link.get('pattern'))} · 可信度 {link.get('confidence', 0)}%）："
                f"{_scrub_forbidden(link.get('conclusion') or '')}{extra_str}"
            )

    sec3.extend(["", "### 中美算力与宏观产业链映射"])
    if source_matrix and (source_matrix.get("categories") or []):
        mapped_lines: list[str] = []
        for cat in source_matrix.get("categories") or []:
            if not isinstance(cat, dict):
                continue
            bridges = [b for b in (cat.get("supply_chain_bridges") or []) if isinstance(b, dict)]
            for b in bridges[:3]:
                a_shares = [
                    f"{_scrub_forbidden(a.get('name'))}({a.get('symbol')}/{_scrub_forbidden(a.get('sector'))})"
                    for a in (b.get("a_shares") or [])[:3]
                    if isinstance(a, dict) and a.get("name")
                ]
                if a_shares:
                    mapped_lines.append(
                        f"- **{_scrub_forbidden(cat.get('name'))} · {b.get('us_leader')}（{_scrub_forbidden(b.get('us_name'))}）**："
                        f"{_scrub_forbidden(b.get('focus') or '')} → A股映射：{'、'.join(a_shares)}"
                    )
        if mapped_lines:
            sec3.extend(mapped_lines[:8])
        else:
            sec3.append("- 当前信源矩阵已加载，主线聚焦美股算力龙头资本开支向光模块、PCB 及国产替代传导。")
    else:
        sec3.append("- 结合美股算力链（NVDA/TSM/AVGO/MU）与国内光通信、高速 PCB 及晶圆制造景气度进行联动跟踪，优先验证海外云厂资本开支向订单的传导。")

    guru_articles = [a for a in articles if a.get("source") == "大佬分享"]
    research_articles = [a for a in articles if a.get("source") == "AI与金融研报"]
    featured_articles = (guru_articles[:4] + research_articles[:2])[:5]
    sec3.extend(["", "### 大佬深度长文核心摘录"])
    if featured_articles:
        for art in featured_articles:
            pub = _scrub_forbidden(art.get("publisher") or art.get("source") or "深度长文")
            atitle = _scrub_forbidden(art.get("title") or art.get("filename") or "")
            aexc = _scrub_forbidden(art.get("excerpt") or "").strip()
            if len(aexc) > 130:
                aexc = aexc[:127].rstrip() + "..."
            t_list = [str(t) for t in (art.get("tickers") or [])[:4]]
            t_tag = f"（涉及：{'/'.join(t_list)}）" if t_list else ""
            sec3.append(f"- **[{pub}] {atitle}**{t_tag}：{aexc}")
    else:
        sec3.append("- 本期暂无新增大佬长文归档，保持对半导体与宏观专栏的定期追踪。")

    # ── Section 4: 逆向证伪清单与下阶段风险节点 ──
    sec4: list[str] = [
        "## 4. 逆向证伪清单与下阶段风险节点",
        "",
        "### 安全边际与尾部脆弱性检验（如何证伪当前判断）",
        f"- **流动性与利率证伪线**：若 10年期美债突破 **5.25%** 且美联储净流动性跌破 **$5.60T**（当前 {_fmt_fred_item(fmap, 'fed_net_liquidity')}），长久期科技与高估值风险资产需进一步压缩多头暴露；反之若 10Y 有效回落至 **4.75%** 下方且 VIX 回到 **22** 以下，则考虑逐步恢复右侧趋势配置。",
        "- **AI 算力链证伪线**：若云厂商单季资本开支指引增速明显放缓，或 HBM/CoWoS 出现实质性砍单传闻且 NVDA 跌破中期关键均线支撑，说明盈利预期存在下修风险，禁止盲目左侧抄底。",
        "- **加密与尾部风险检验**：目前加密与美股科技股相关性仍高，若宏观避险情绪升温导致山寨币流动性抽血加剧，需防范高杠杆山寨币联动闪崩及配对仓位的阶段性轧空风险。",
        "- **逆向安全边际检查**：对散户情绪明显过热的赛道坚持「不追高、等回踩承接」；仅在专业高胜率买方与产业数据形成双重确认时提高单票权重。",
        "",
        "### 下阶段核心事件与时间节点",
    ]
    if cal_events:
        for ev in cal_events[:6]:
            ev_date = _scrub_forbidden(ev.get("time_bjt") or ev.get("date_local") or ev.get("date") or ev.get("time") or "").strip()
            ev_title = _scrub_forbidden(ev.get("title") or ev.get("event") or "").strip()
            ev_imp = _scrub_forbidden(ev.get("impact") or ev.get("category_cn") or ev.get("category") or "重要节点").strip()
            if ev_title:
                date_prefix = f"**{ev_date}**｜" if ev_date else ""
                sec4.append(f"- {date_prefix}[{ev_imp}] {ev_title}")
    else:
        sec4.extend(
            [
                "- 关注下一期美国 CPI / PCE 通胀数据、非农就业与美联储议息会议纪要对长端收益率的重新定价。",
                "- 跟踪美股核心算力与云厂商财报窗口期的资本开支指引及台积电月度营收数据。",
            ]
        )

    # ── Section 5: 待外部 Deep Research 深挖课题与一键提问词 ──
    prompts = _build_deep_research_prompts(
        period_label=period_label,
        as_of_date=date_str,
        fmap=fmap,
        eq_map=eq_map,
        crypto_spot=crypto_spot,
        risk_gate=risk_gate,
        links=links,
        forum_sentiment=forum_sentiment,
        source_matrix=source_matrix,
    )
    sec5: list[str] = [
        "## 5. 待外部 Deep Research 深挖课题与一键提问词",
        "",
        "以下针对本期分歧最突出的核心命题生成独立提问词，可直接复制至 Gemini Deep Research / Perplexity / ChatGPT Deep Research 执行深挖，完成后通过「导入深研」回流本地知识库：",
        "",
    ]
    for idx, (heading, prompt_body) in enumerate(prompts, start=1):
        sec5.append(f"### {idx}. {heading}")
        sec5.append("")
        sec5.append("```text")
        sec5.append(_scrub_forbidden(prompt_body))
        sec5.append("```")
        sec5.append("")

    body_md = _scrub_forbidden("\n".join([f"# {title}", ""] + sec1 + [""] + sec2 + [""] + sec3 + [""] + sec4 + [""] + sec5))
    summary = _scrub_forbidden(
        f"{period_label}决策{ '周报' if norm_period == 'weekly' else '月报' }：当前风控「{risk_label}」（多头上限 {long_cap_pct}），"
        f"10Y美债 {_fmt_fred_item(fmap, 'us_10y')}、VIX {_fmt_fred_item(fmap, 'vix')}，汇集 {len(verified_kols)} 位高胜率 KOL 与跨市场产业链观察，含 {len(prompts)} 组 Deep Research 一键提问词。"
    )
    if len(summary) > 180:
        summary = summary[:177].rstrip() + "..."

    frontmatter = _build_yaml_frontmatter(
        title=_scrub_forbidden(title),
        summary=summary,
        publisher=publisher,
        author_role="周期辅助决策",
        report_kind=norm_period,
        score=92,
        tickers=top_tickers,
    )
    full_doc = f"{frontmatter}\n\n{body_md}\n"

    article_dict = _parse_article_dict(
        filename=filename,
        title=title,
        summary=summary,
        publisher=publisher,
        author_role="周期辅助决策",
        report_kind=norm_period,
        score=92,
        tickers=top_tickers,
        body_md=body_md,
        mtime_str=dt.strftime("%Y-%m-%d %H:%M"),
    )
    if has_private_article_content(article_dict) or has_private_article_content({"filename": filename, "content": full_doc}):
        raise ValueError("周期报告内容触发隐私红线，已中止写入")

    _atomic_write_text(dest, full_doc)
    return article_dict


def _normalize_tickers_input(tickers: list[str] | tuple[str, ...] | str | None, text_for_extract: str) -> list[str]:
    out: list[str] = []
    if isinstance(tickers, str):
        raw_items = re.split(r"[,，\s]+", tickers.strip())
    elif isinstance(tickers, (list, tuple, set)):
        raw_items = [str(x).strip() for x in tickers]
    else:
        raw_items = []
    for item in raw_items:
        sym = item.strip().lstrip("$").upper()
        if sym and sym not in out:
            out.append(sym)
    for inferred in extract_tickers(text_for_extract):
        if inferred not in out:
            out.append(inferred)
    return out[:8]


def _resolve_ingest_title(explicit_title: str, fm_meta: dict[str, Any], body: str) -> str:
    if explicit_title and explicit_title.strip():
        return explicit_title.strip()
    for ln in body.splitlines():
        s = ln.strip()
        if s.startswith("# "):
            heading = s[2:].strip()
            if heading:
                return heading
    fm_title = str(fm_meta.get("title") or "").strip()
    if fm_title:
        return fm_title
    plain = re.sub(r"[#>*`\[\]()\-]+", " ", body)
    plain = re.sub(r"\s+", " ", plain).strip()
    if plain:
        return plain[:20]
    return "外部深研补充"


def ingest_deep_research_markdown(
    raw_markdown: str,
    *,
    title: str = "",
    tickers: list[str] | tuple[str, ...] | str | None = None,
    related_period: str = "",
    articles_dir: Path | str | None = None,
    url: str = "",
    publisher: str = "Deep Research 深度补充",
    author_role: str = "外部深研闭环",
) -> dict[str, Any]:
    """Sanitize, privacy-check, persist, and return an external Deep Research markdown article."""
    raw_text = str(raw_markdown or "").strip()
    if not raw_text:
        raise ValueError("外部深研 Markdown 正文不能为空")

    fm_meta, stripped_body = _strip_frontmatter(raw_text)
    if not stripped_body:
        raise ValueError("剥离头部元信息后正文为空")

    resolved_title = _resolve_ingest_title(title, fm_meta, stripped_body)
    resolved_url = str(url or fm_meta.get("url") or "").strip()
    if not resolved_url.startswith(("http://", "https://")):
        resolved_url = ""

    # Privacy & credential redline BEFORE any persistence
    if has_private_article_content(
        {
            "filename": "dr_ingest_check.md",
            "title": resolved_title,
            "body_md": raw_text,
            "content": raw_text,
        }
    ):
        raise ValueError("内容包含敏感私人持仓或密钥信息，已拦截入库")

    clean_title = _scrub_forbidden(resolved_title)
    clean_body = _scrub_forbidden(stripped_body)
    clean_period = _scrub_forbidden(related_period).strip()

    if clean_period and f"关联周期" not in clean_body[:160]:
        lines = clean_body.splitlines()
        quote_line = f"> 关联周期研报：{clean_period}"
        if lines and lines[0].strip().startswith("# "):
            clean_body = "\n".join([lines[0], "", quote_line, ""] + lines[1:]).strip()
        else:
            clean_body = f"# {clean_title}\n\n{quote_line}\n\n{clean_body}".strip()

    fm_tickers = fm_meta.get("tickers") if isinstance(fm_meta.get("tickers"), (list, str)) else None
    norm_tickers = _normalize_tickers_input(tickers if tickers is not None else fm_tickers, f"{clean_title}\n{clean_body}")

    # Extract concise summary
    summary = _scrub_forbidden(fm_meta.get("summary") or "")
    if not summary:
        for ln in clean_body.splitlines():
            s = ln.strip()
            if s and not s.startswith(("#", "-", "*", ">", "```", "|")) and len(s) >= 12:
                summary = s
                break
    if not summary:
        summary = clean_title
    if len(summary) > 180:
        summary = summary[:177].rstrip() + "..."

    # Double-check scrubbed text against privacy redline
    if has_private_article_content(
        {
            "filename": "dr_ingest_clean.md",
            "title": clean_title,
            "summary": summary,
            "body_md": clean_body,
            "content": clean_body,
        }
    ):
        raise ValueError("内容包含敏感私人持仓或密钥信息，已拦截入库")

    now_bjt = datetime.now(BJT)
    digest8 = hashlib.sha256(f"{clean_title}\n{clean_body}".encode("utf-8")).hexdigest()[:8]
    filename = f"dr_{now_bjt.strftime('%Y%m%d')}_{digest8}.md"
    if not safe_article_filename(filename):
        raise ValueError(f"非法文件名: {filename}")

    clean_pub = _scrub_forbidden(publisher) or "Deep Research 深度补充"
    clean_role = _scrub_forbidden(author_role) or "外部深研闭环"
    frontmatter = _build_yaml_frontmatter(
        title=clean_title,
        summary=summary,
        publisher=clean_pub,
        author_role=clean_role,
        report_kind="deep_research",
        score=90,
        tickers=norm_tickers,
        url=resolved_url,
        related_period=clean_period,
    )
    full_doc = f"{frontmatter}\n\n{clean_body}\n"

    target_dir = Path(articles_dir) if articles_dir is not None else DEFAULT_ARTICLES_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(target_dir / filename, full_doc)

    return _parse_article_dict(
        filename=filename,
        title=clean_title,
        summary=summary,
        publisher=clean_pub,
        author_role=clean_role,
        report_kind="deep_research",
        score=90,
        tickers=norm_tickers,
        body_md=clean_body,
        mtime_str=now_bjt.strftime("%Y-%m-%d %H:%M"),
        url=resolved_url,
    )


def check_deep_research_capabilities() -> dict[str, Any]:
    """Inspect local Deep Research CLI, DemoLab parallel runner, env key presence (bool only), and desktop debate bridge."""
    which_cli = shutil.which("gemini-api") or shutil.which("gemini-api.exe")
    cli_path: str | None = None
    if which_cli:
        cli_path = which_cli
    elif DEFAULT_GEMINI_API_EXE.is_file():
        cli_path = str(DEFAULT_GEMINI_API_EXE)

    # Strictly check boolean existence without reading or exposing the key value
    has_api_key = bool(os.environ.get("GEMINI_API_KEY"))

    debate_script = COCKPIT_ROOT / "desktop" / "multi_agent_debate.py"
    acp_bridge_script = COCKPIT_ROOT / "desktop" / "acp_bridge.py"
    has_debate = debate_script.is_file()
    has_agy_bridge = acp_bridge_script.is_file()
    can_run_gemini_dr = bool(cli_path and has_api_key)

    demolab_caps: dict[str, Any] = {
        "demolab_skill_found": False,
        "agy_cli_found": False,
        "staff_hub_found": False,
        "can_run_demolab_dr": False,
        "available_runners": [],
    }
    try:
        from backend.deep_research_runner import probe_demolab_capabilities

        demolab_caps = probe_demolab_capabilities()
    except Exception:
        pass

    can_run_demolab_dr = bool(demolab_caps.get("can_run_demolab_dr"))
    if can_run_gemini_dr:
        rec_mode = "gemini_deep_research"
    elif can_run_demolab_dr:
        rec_mode = "demolab_parallel"
    else:
        rec_mode = "manual_prompt_copy"

    return {
        "gemini_api_cli_found": bool(cli_path),
        "gemini_api_cli_path": cli_path,
        "has_gemini_api_key": has_api_key,
        "can_run_gemini_dr": can_run_gemini_dr,
        "demolab_skill_found": bool(demolab_caps.get("demolab_skill_found")),
        "agy_cli_found": bool(demolab_caps.get("agy_cli_found")),
        "staff_hub_found": bool(demolab_caps.get("staff_hub_found")),
        "can_run_demolab_dr": can_run_demolab_dr,
        "can_run_any_dr": bool(can_run_gemini_dr or can_run_demolab_dr),
        "available_runners": list(demolab_caps.get("available_runners") or []),
        "default_agent": GEMINI_DR_AGENT,
        "auto_paid_calls_disabled": True,
        "has_multi_agent_debate": has_debate,
        "has_agy_bridge": has_agy_bridge,
        "recommended_mode": rec_mode,
    }


def _extract_gemini_text_output(stdout_text: str) -> tuple[str | None, str, str]:
    raw = (stdout_text or "").strip()
    if not raw:
        return None, "", ""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None, "", raw
    if not isinstance(payload, dict):
        return None, "", raw
    run_id = str(payload.get("id") or "") or None
    status = str(payload.get("status") or "")
    chunks: list[str] = []
    for step in payload.get("steps") or []:
        if not isinstance(step, dict):
            continue
        if step.get("type") == "model_output":
            for part in step.get("content") or []:
                if isinstance(part, dict) and part.get("type") == "text" and part.get("text"):
                    chunks.append(str(part["text"]))
    return run_id, status, "\n\n".join(chunks).strip()


def run_gemini_deep_research(
    prompt: str,
    *,
    execute: bool = False,
    articles_dir: Path | str | None = None,
    ingest: bool = False,
    title: str = "",
    tickers: list[str] | str | None = None,
    related_period: str = "",
    timeout: int = 30,
) -> dict[str, Any]:
    """Run Gemini Deep Research CLI only when explicitly requested (`execute=True`) and authorized."""
    clean_prompt = str(prompt or "").strip()
    caps = check_deep_research_capabilities()

    if not execute:
        return {
            "executed": False,
            "status": "skipped_default_no_auto_paid_call",
            "message": "默认不自动触发付费 Deep Research 接口；请使用 --run-gemini-dr 显式触发或复制提问词手动执行。",
            "capabilities": caps,
        }
    if not clean_prompt:
        raise ValueError("Deep Research prompt 不能为空")
    if not caps["gemini_api_cli_found"]:
        return {
            "executed": False,
            "status": "missing_gemini_api_cli",
            "message": "未检测到 gemini-api CLI 可执行文件。",
            "capabilities": caps,
        }
    if not caps["has_gemini_api_key"]:
        return {
            "executed": False,
            "status": "missing_gemini_api_key",
            "message": "未检测到 GEMINI_API_KEY 环境变量，已跳过外部接口调用。",
            "capabilities": caps,
        }

    cmd = [
        str(caps["gemini_api_cli_path"]),
        "agent",
        "run",
        clean_prompt,
        "--agent",
        GEMINI_DR_AGENT,
        "--background",
        "-o",
        "json",
    ]
    proc = subprocess.run(
        cmd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    run_id, status, text_out = _extract_gemini_text_output(proc.stdout)
    result: dict[str, Any] = {
        "executed": True,
        "returncode": proc.returncode,
        "run_id": run_id,
        "status": status or ("submitted" if proc.returncode == 0 else "error"),
        "output_preview": _scrub_forbidden(text_out[:300]) if text_out else "",
    }
    if proc.returncode != 0 and proc.stderr:
        result["error"] = _scrub_forbidden(proc.stderr.strip()[:300])
    if ingest and text_out and proc.returncode == 0:
        article = ingest_deep_research_markdown(
            text_out,
            title=title,
            tickers=tickers,
            related_period=related_period,
            articles_dir=articles_dir,
        )
        result["ingested_article"] = article
    return result


def _load_state_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="MacroCockpit 周期决策深研报告与外部深研入库 CLI")
    parser.add_argument("--period", choices=("weekly", "monthly"), default="weekly", help="生成周报或月报")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_PATH, help="cockpit_state.json 路径")
    parser.add_argument("--articles-dir", type=Path, default=DEFAULT_ARTICLES_DIR, help="articles 输出目录")
    parser.add_argument("--as-of", type=str, default=None, help="指定报告基准日期（ISO 格式）")
    parser.add_argument("--force", action="store_true", help="强制覆盖本期已存在的周/月报")
    parser.add_argument("--ingest", type=Path, default=None, help="导入外部 Deep Research Markdown 文件路径")
    parser.add_argument("--title", type=str, default="", help="导入时可选覆盖文章标题")
    parser.add_argument("--tickers", type=str, default="", help="导入时可选逗号分隔标的代码")
    parser.add_argument("--related-period", type=str, default="", help="导入时可选关联周期标识（如 2026-W48）")
    parser.add_argument("--url", type=str, default="", help="导入时可选来源链接")
    parser.add_argument("--status", action="store_true", help="输出本地 Deep Research 调用能力 JSON")
    parser.add_argument("--run-gemini-dr", action="store_true", help="显式触发 Gemini Deep Research（需 GEMINI_API_KEY）")
    parser.add_argument("--run-demolab-dr", action="store_true", help="显式触发 DemoLab 多智能体并行深研")
    parser.add_argument("--prompt", type=str, default="", help="配合 --run-gemini-dr / --run-demolab-dr 使用的提问词")
    parser.add_argument("--ingest-result", action="store_true", help="若接口同步返回正文则自动入库")

    args = parser.parse_args(argv)

    if args.status:
        print(json.dumps(check_deep_research_capabilities(), ensure_ascii=False, indent=2))
        return 0

    if args.ingest is not None:
        raw_md = args.ingest.read_text(encoding="utf-8")
        art = ingest_deep_research_markdown(
            raw_md,
            title=args.title,
            tickers=args.tickers or None,
            related_period=args.related_period,
            articles_dir=args.articles_dir,
            url=args.url,
        )
        print(
            json.dumps(
                {
                    "ok": True,
                    "filename": art["filename"],
                    "title": art["title"],
                    "tickers": art["tickers"],
                    "publisher": art["publisher"],
                    "report_kind": art["report_kind"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if args.run_demolab_dr:
        from backend.deep_research_runner import run_demolab_deep_research

        state = _load_state_file(args.state)
        res = run_demolab_deep_research(
            args.prompt,
            execute=True,
            state=state,
            articles_dir=args.articles_dir,
            ingest=args.ingest_result,
            title=args.title,
            tickers=args.tickers or None,
            related_period=args.related_period,
        )
        print(json.dumps({k: v for k, v in res.items() if k != "markdown"}, ensure_ascii=False, indent=2))
        return 0 if res.get("status") == "completed" else 1

    if args.run_gemini_dr:
        res = run_gemini_deep_research(
            args.prompt,
            execute=True,
            articles_dir=args.articles_dir,
            ingest=args.ingest_result,
            title=args.title,
            tickers=args.tickers or None,
            related_period=args.related_period,
        )
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0 if res.get("returncode", 0) == 0 else 1

    state = _load_state_file(args.state)
    art = generate_periodic_decision_report(
        state,
        period=args.period,
        articles_dir=args.articles_dir,
        as_of=args.as_of,
        force=args.force,
    )
    print(
        json.dumps(
            {
                "ok": True,
                "filename": art["filename"],
                "title": art["title"],
                "report_kind": art["report_kind"],
                "publisher": art["publisher"],
                "tickers": art["tickers"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
