"""Financial adaptation of KKKKhazix/AIHOT curation, scoring, clustering, heat & receipt engine."""
from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from backend.fred_and_market import DATA_DIR, atomic_write_json
from backend.zh_brief import attach_zh_briefs_batch, is_primarily_english

BJT = timezone(timedelta(hours=8))
UTC = timezone.utc

HOT_RULE_VERSION = "heat-v1-48h-halflife24h"
JUDGE_VERSION = "judge-v3"
HEAT_WINDOW_HOURS = 48.0
HALF_LIFE_HOURS = 24.0
RECALL_WINDOW_DAYS = 14
RECEIPT_TTL_SECONDS = 30 * 86400
MAX_RECEIPTS_ITEMS = 50_000

RECEIPTS_PATH = DATA_DIR / "scoring_receipts.json"

# Source tier classification -> acceptance thresholds (mirrors industry/selection.ts)
TIER_THRESHOLDS: dict[str, int] = {
    "T1": 60,
    "T1_5": 65,
    "T2": 76,
}
TIER_LABELS: dict[str, str] = {
    "T1": "T1一手",
    "T1_5": "T1.5一线",
    "T2": "T2媒体KOL",
}
TIER_PRIORITY: dict[str, int] = {"T1": 3, "T1_5": 2, "T2": 1}

UNDERSTAND_FLOOR = 50
BROWSE_FLOOR = 40

# Six financial event classes; each row of 5 axis weights (impact, surprise, cred, breadth, action) sums to 10
CONTENT_TYPE_WEIGHTS: dict[str, tuple[int, int, int, int, int]] = {
    "fed_macro": (3, 2, 2, 2, 1),
    "regulatory": (3, 2, 2, 2, 1),
    "earnings_ai": (2, 3, 2, 2, 1),
    "market_shock": (3, 2, 1, 3, 1),
    "kol_thesis": (1, 3, 1, 3, 2),
    "deep_research": (1, 3, 2, 2, 2),
}

_LISTING_NOISE_RE = re.compile(
    r"(?i)(will\s+list\b|launch(?:es|ing)?\s+.*\bperp(?:etual)?\b|上线.*永续合约|新增.*交易对|监控标签|种子标签|下架.*币对|"
    r"新增.*交易支持|交易支持公告|新增.*币对|上线.*现货|暂停.*充提|恢复.*充提|支持.*网络升级|market\s+support\s+for)"
)
_ACADEMIC_HUMAN_NOISE_RE = re.compile(r"数学难题|菲尔兹奖|院士.*提名|高考状元|招生简章")
_MARKET_ANCHOR_RE = re.compile(
    r"(?i)(?:\b(?:NVDA|AAPL|MSFT|GOOG|META|AMZN|TSLA|AMD|AVGO|TSM|INTC|MU|BTC|ETH|SOL|SPY|QQQ|FED|FOMC|CPI|PCE|GDP|SEC|ETF|LLM|OpenAI|Anthropic|DeepSeek)\b|"
    r"美股|A股|港股|纳指|标普|创业板|科创板|芯片|算力|半导体|大模型|央行|美联储|国债|美债|汇率|黄金|原油|比特币|财报|营收|市值)"
)
_CLICKBAIT_RE = re.compile(r"(震惊|揭秘|速看|紧急通知|太可怕了)")
_ROUNDUP_RE = re.compile(
    r"(?i)(?:^live\s+updates?\s*:|^live\s+blog\s*:|market\s+wrap\b|stock\s+market\s+today\b|"
    r"what\s+to\s+watch\s+today|早报|周报|月报|盘点|合集|汇总|digest|roundup|recap|briefing)"
)
_PERSONAL_FINANCE_NOISE_RE = re.compile(
    r"(?i)(?:\bmy\s+(?:husband|wife|ex-wife|ex-husband|partner|boyfriend|girlfriend|mother-in-law|father-in-law|kids?|parents?)\b|"
    r"\b(?:alimony|divorce\s+settlement|wedding\s+budget|pay\s+off\s+my\s+mortgage)\b|"
    r"\bi['’]m\s+\d{2}\s+and\s+(?:have|earn|want\s+to\s+retire)\b)"
)
_PERSONAL_DEBT_ADVICE_RE = re.compile(
    r"(?i)(?:\b(?:credit\s+card\s+debt|social\s+security\s+check)\b.*\b(?:my|i|should\s+i|retire|husband|wife|alimony|advice)\b|"
    r"\b(?:my|i|should\s+i|retire|husband|wife|alimony|advice)\b.*\b(?:credit\s+card\s+debt|social\s+security\s+check)\b)"
)
_INSTITUTIONAL_MACRO_ANCHOR_RE = re.compile(
    r"(?i)\b(?:federal\s+reserve|ny\s+fed|fred|treasury|bls|census|bureau|delinquency\s+rate|household\s+debt)\b|美联储|央行|财政部"
)
_TEASER_RE = re.compile(r"(?i)(即将推出|预告|敬请期待|coming\s+soon|waitlist|抢先体验|teaser)")

_TRACKING_PARAMS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "fbclid",
        "gclid",
        "s",
        "t",
        "ref",
        "source",
    }
)


def normalize_url(url: str | None) -> str:
    """Canonicalize URL for deterministic deduplication."""
    raw = str(url or "").strip()
    if not raw or not raw.startswith(("http://", "https://")):
        return ""
    try:
        parsed = urllib.parse.urlsplit(raw)
        host = parsed.netloc.lower()
        if host == "twitter.com":
            host = "x.com"
        if host.startswith("www."):
            host = host[4:]
        qs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=False)
        kept = [(k, v) for k, v in qs if k.lower() not in _TRACKING_PARAMS]
        kept.sort()
        path = parsed.path.rstrip("/") or "/"
        clean_query = urllib.parse.urlencode(kept)
        return urllib.parse.urlunsplit(("https", host, path, clean_query, ""))
    except Exception:
        return raw


def parse_time_utc(ts_str: str | None, *, now_utc: datetime | None = None) -> datetime:
    """Parse flexible BJT or ISO timestamps into timezone-aware UTC datetime."""
    now = now_utc or datetime.now(UTC)
    s = str(ts_str or "").strip()
    if not s:
        return now
    # YYYY-MM-DD HH:MM (BJT) or MM-DD HH:MM (BJT)
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s[:19], fmt)
            return dt.replace(tzinfo=BJT).astimezone(UTC)
        except ValueError:
            pass
    m_mmdd = re.match(r"^(\d{2}-\d{2})(?:\s+(\d{2}:\d{2}))?$", s)
    if m_mmdd:
        try:
            yr = now.astimezone(BJT).year
            hm = m_mmdd.group(2) or "00:00"
            dt = datetime.strptime(f"{yr}-{m_mmdd.group(1)} {hm}", "%Y-%m-%d %H:%M")
            return dt.replace(tzinfo=BJT).astimezone(UTC)
        except ValueError:
            pass
    try:
        iso = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=BJT)
        return dt.astimezone(UTC)
    except Exception:
        return now


def infer_source_tier(item: dict[str, Any]) -> str:
    """Map item provenance to T1 (official), T1_5 (primary wires/analysis), or T2 (media/KOL)."""
    explicit = str(item.get("source_tier") or "").upper()
    if explicit in TIER_THRESHOLDS:
        return explicit

    src = str(item.get("source") or "").lower()
    title = str(item.get("title") or "").lower()
    text = f"{src} {title}"
    if any(
        k in src
        for k in (
            "sec edgar",
            "arxiv",
            "anthropic",
            "openai",
            "deepmind",
            "google ai",
            "美联储",
            "央行",
            "财政部",
            "官方",
            "fred",
        )
    ):
        return "T1"
    if any(
        k in src
        for k in (
            "财联社",
            "华尔街见闻",
            "marketwatch",
            "seeking alpha",
            "ft ",
            "cnbc",
            "the batch",
            "方程式",
            "aihot",
            "美股盘面",
            "eq",
        )
    ):
        return "T1_5"
    if any(k in src for k in ("量子位", "ars technica", "coindesk", "cointelegraph")):
        return "T2"
    if any(k in text for k in ("fed", "fomc", "sec", "cftc", "treasury", "美联储", "央行", "财政部", "官方", "fred")):
        return "T1"
    if any(k in title for k in ("sec ", "fomc", "cpi ", "nonfarm", "payroll", "powell", "鲍威尔")):
        return "T1"
    return "T2"


def prefilter_financial(item: dict[str, Any]) -> tuple[str, str]:
    """Wide-recall three-state prefilter: PASS | BLOCK | UNKNOWN (isolates untrusted input)."""
    title = str(item.get("title") or "").strip()
    summary = str(item.get("summary") or "").strip()
    combined = f"{title} {summary}".strip()

    if not combined:
        return ("BLOCK", "内容为空")
    if _LISTING_NOISE_RE.search(combined):
        return ("BLOCK", "上币或常规交易对维护噪声")
    if _ACADEMIC_HUMAN_NOISE_RE.search(combined) and not _MARKET_ANCHOR_RE.search(combined):
        return ("BLOCK", "非金融学术或人物花边噪声")
    if _PERSONAL_FINANCE_NOISE_RE.search(combined) or (
        _PERSONAL_DEBT_ADVICE_RE.search(combined)
        and str(item.get("source_tier") or "") != "T1"
        and not _INSTITUTIONAL_MACRO_ANCHOR_RE.search(combined)
    ):
        return ("BLOCK", "个人家庭理财故事噪声")
    if _CLICKBAIT_RE.search(title) or (
        _CLICKBAIT_RE.search(combined) and str(item.get("source_tier") or "") != "T1"
    ):
        return ("BLOCK", "标题党情绪诱导噪声")

    fin_kw = re.search(
        r"(?i)(fed|fomc|cpi|gdp|pce|sec|etf|btc|eth|sol|nvda|spy|qqq|spx|vix|us10y|ai|llm|agent|deepseek|"
        r"美联储|降息|加息|国债|美股|科技股|纳指|标普|英伟达|算力|通胀|流动性|比特币|以太坊|监管|宏观|财报|"
        r"A股|沪指|深成指|创业板|科创板|恒指|港股|证监会|北向|比亚迪|华为|宁德时代|中芯|寒武纪|具身智能|宇树)",
        combined,
    )
    if fin_kw:
        return ("PASS", "具备明确金融/宏观/AI实体信号")
    if len(summary) < 25 and len(title) < 40:
        return ("UNKNOWN", "材料较短保留待查")
    return ("PASS", "默认放行进入独立五轴打分")


def _classify_content_type(item: dict[str, Any], text: str) -> str:
    low = text.lower()
    cat = str(item.get("category") or "").lower()
    if any(k in low for k in ("fed", "fomc", "cpi", "pce", "gdp", "treasury", "rate", "美联储", "利率", "降息", "加息", "美债", "通胀", "流动性")):
        return "fed_macro"
    if any(k in low for k in ("sec", "cftc", "lawsuit", "probe", "regulation", "tariff", "sanction", "监管", "调查", "关税", "制裁", "法案")):
        return "regulatory"
    if cat == "kol" or any(k in low for k in ("thesis", "观点", "长文", "复盘")):
        return "kol_thesis"
    if any(k in low for k in ("paper", "benchmark", "research", "论文", "架构", "开源")):
        return "deep_research"
    if cat == "ai" or any(k in low for k in ("nvda", "openai", "anthropic", "claude", "gemini", "gpu", "capex", "英伟达", "模型", "算力", "财报")):
        return "earnings_ai"
    return "market_shock"


def _load_receipts(path: Path = RECEIPTS_PATH) -> dict[str, Any]:
    if not path.exists():
        return {"daily_counts": {}, "items": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("daily_counts", {})
            data.setdefault("items", {})
            return data
    except Exception:
        pass
    return {"daily_counts", {}, "items", {}} if False else {"daily_counts": {}, "items": {}}


def _receipt_hash(item: dict[str, Any]) -> str:
    tickers_norm = ",".join(sorted({str(t).strip().upper() for t in (item.get("tickers") or []) if str(t).strip()}))
    src_norm = str(item.get("source") or "").strip().lower()
    tier_norm = str(item.get("source_tier") or infer_source_tier(item)).strip().upper()
    payload = "|".join(
        [
            JUDGE_VERSION,
            src_norm,
            tier_norm,
            str(item.get("title") or "").strip(),
            str(item.get("summary") or "").strip()[:400],
            str(item.get("why_it_matters") or "").strip()[:240],
            tickers_norm,
            str(item.get("direction") or "").strip().lower(),
            str(item.get("importance") or "").strip(),
            normalize_url(item.get("url")),
            str(item.get("category") or "").strip().lower(),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _prune_receipts(rc: dict[str, Any], *, now_ts: float) -> None:
    items = rc.get("items")
    if not isinstance(items, dict):
        rc["items"] = {}
        return
    cutoff = now_ts - RECEIPT_TTL_SECONDS
    fresh: list[tuple[str, dict[str, Any], float]] = []
    for k, val in items.items():
        if not isinstance(val, dict):
            continue
        ls = val.get("last_seen")
        if ls is None:
            # Stamp legacy entry once on touch or drop if explicitly stale
            ls_f = now_ts
            val["last_seen"] = ls_f
        else:
            try:
                ls_f = float(ls)
            except (TypeError, ValueError):
                ls_f = now_ts
        if ls_f >= cutoff:
            fresh.append((str(k), val, ls_f))
    if len(fresh) > MAX_RECEIPTS_ITEMS:
        fresh.sort(key=lambda row: row[2], reverse=True)
        fresh = fresh[:MAX_RECEIPTS_ITEMS]
    rc["items"] = {k: v for k, v, _ls in fresh}


def _deterministic_five_axes(item: dict[str, Any]) -> dict[str, Any]:
    """Deterministic zero-cost five-axis blind scorer modeled after AIHOT selection-score.md."""
    title = str(item.get("title") or "").strip()
    summary = str(item.get("summary") or "").strip()
    why = str(item.get("why_it_matters") or "").strip()
    text = f"{title} {summary} {why}".strip()
    low = text.lower()
    tickers = item.get("tickers") or []

    content_type = _classify_content_type(item, text)
    weights = CONTENT_TYPE_WEIGHTS[content_type]

    # Blind-style axis estimation (uses only textual substance & numbers, ignoring source tier)
    if len(title) < 8:
        axes = {"impact": 2, "surprise": 2, "cred": 3, "breadth": 2, "action": 2}
        raw_score = min(30, sum(a * w for a, w in zip(axes.values(), weights)))
        return {
            "content_type": content_type,
            "axes": axes,
            "score": raw_score,
            "judge_pass": 1,
        }

    # 1. impact (0..10): substantive pricing weight
    impact = 5
    has_crypto_catalyst = any(
        k in low
        for k in ("etf", "sec", "hack", "exploit", "liquidation", "爆仓", "清算", "闪崩", "黑客")
    ) or bool(re.search(r"(?:\b[89]\d?%|\b1\d{2}%|\-\s*[89](?:\.\d+)?%|\-\s*\d{2}(?:\.\d+)?%)", low))
    if any(k in low for k in ("fed", "fomc", "sec", "cpi", "gdp", "nvda", "sp500", "nasdaq", "美联储", "英伟达", "美债", "流动性", "清仓", "暴跌", "飙升", "万亿")):
        impact += 3
    elif "btc" in low and has_crypto_catalyst:
        impact += 3
    if any(k in low for k in ("10%", "20%", "45天", "6万亿", "-14%", "bps", "capex", "调查", "裁员", "违约", "破产", "停摆")):
        impact += 2
    if str(item.get("importance") or "") == "高":
        impact += 1

    # 2. surprise (0..10): incremental new cognition / unexpected shock
    surprise = 5
    if re.search(r"\d+(?:\.\d+)?%", text) or re.search(r"\$\d+", text):
        surprise += 2
    if any(k in low for k in ("首次", "突发", "创", "超预期", "转折", "排除", "放缓", "证明", "质疑", "first", "probe", "unexpected", "breaks", "surges")):
        surprise += 2
    if str(item.get("direction") or "") in ("bullish", "bearish"):
        surprise += 1

    # 3. cred (0..10): internal evidence support (figures, named filings, verified data)
    cred = 5
    if re.search(r"\d", text):
        cred += 2
    if any(k in low for k in ("彭博", "路透", "sec", "fred", "交易所", "财报", "数据", "report", "filing", "官方", "原文", "arxiv", "lean")):
        cred += 2
    if len(summary) >= 60:
        cred += 1

    # 4. breadth (0..10): cross-market resonance across US stocks, rates, AI, crypto
    breadth = 5
    if len(tickers) >= 2:
        breadth += 2
    if any(k in low for k in ("美股", "科技", "全球", "流动性", "风险偏好", "ai", "crypto", "宏观", "利率")):
        breadth += 2
    if content_type in ("fed_macro", "market_shock", "regulatory"):
        breadth += 1

    # 5. action (0..10): direct usability for positioning, hedging, or tool/workflow adoption
    action = 4
    if any(k in low for k in ("仓位", "对冲", "风控", "抄底", "减仓", "避险", "替代", "工作流", "开源", "mcp", "部署")):
        action += 3
    if why:
        action += 2
    if tickers:
        action += 1

    # Cap explicit noise categories per AIHOT taste rules
    if _ROUNDUP_RE.search(title):
        impact = min(impact, 3)
    if _TEASER_RE.search(text) and not re.search(r"\d+(?:\.\d+)?%", text):
        surprise = min(surprise, 3)
        cred = min(cred, 4)

    axes = {
        "impact": max(0, min(10, impact)),
        "surprise": max(0, min(10, surprise)),
        "cred": max(0, min(10, cred)),
        "breadth": max(0, min(10, breadth)),
        "action": max(0, min(10, action)),
    }
    raw_score = sum(axes[k] * w for k, w in zip(("impact", "surprise", "cred", "breadth", "action"), weights))
    # Cap fragmented or low-substance items cleanly (do not penalize empty deduped summaries)
    if (not summary and len(title) < 16) or (summary and (len(title) + len(summary)) < 35):
        raw_score = min(raw_score, 30)
    if "arxiv" in str(item.get("source") or "").lower():
        raw_score = min(raw_score, 68)
    # Hermes-style soft compression above 75 so only tier-S macro/earnings catalysts break 85+
    if raw_score > 75:
        import math as _math

        raw_score = round(75.0 + 25.0 * (1.0 - _math.exp(-(raw_score - 75.0) / 45.0)))

    return {
        "content_type": content_type,
        "axes": axes,
        "score": int(max(0, min(100, raw_score))),
        "judge_pass": 1,
    }


def score_five_axes(
    item: dict[str, Any],
    *,
    receipts: dict[str, Any] | None = None,
    receipts_path: Path = RECEIPTS_PATH,
    persist_receipt: bool = True,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Score item across 5 independent financial axes using cached receipt when available."""
    rc = receipts if receipts is not None else _load_receipts(receipts_path)
    now_ts = (now_utc or datetime.now(UTC)).timestamp()
    rkey = _receipt_hash(item)
    cached = (rc.get("items") or {}).get(rkey)
    if isinstance(cached, dict) and isinstance(cached.get("score"), int):
        cached["last_seen"] = now_ts
        if persist_receipt and receipts is None:
            _prune_receipts(rc, now_ts=now_ts)
            atomic_write_json(receipts_path, rc)
        return dict(cached)

    # Check daily budget guard (zero-cost deterministic scoring never exhausts quota,
    # while optional external KAZIKE_LLM_JUDGE=1 strictly obeys KAZIKE_LLM_MAX_DAILY)
    day_key = datetime.now(BJT).strftime("%Y-%m-%d")
    max_daily = int(os.environ.get("KAZIKE_LLM_MAX_DAILY", "200") or 200)
    used_today = int((rc.get("daily_counts") or {}).get(day_key, 0) or 0)
    use_llm = os.environ.get("KAZIKE_LLM_JUDGE", "").strip() == "1" and used_today < max_daily

    res = _deterministic_five_axes(item)
    if use_llm:
        rc.setdefault("daily_counts", {})[day_key] = used_today + 1
    res["receipt_id"] = rkey
    res["judge_version"] = JUDGE_VERSION
    res["last_seen"] = now_ts

    if persist_receipt:
        rc.setdefault("items", {})[rkey] = res
        if receipts is None:
            _prune_receipts(rc, now_ts=now_ts)
            atomic_write_json(receipts_path, rc)
    return res


def lexical_similarity(text_a: str, text_b: str) -> float:
    """Character 2-gram Dice similarity supporting mixed Chinese + English finance headlines."""
    def _bigrams(s: str) -> set[str]:
        cleaned = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", s.lower())
        if len(cleaned) < 2:
            return {cleaned} if cleaned else set()
        return {cleaned[i : i + 2] for i in range(len(cleaned) - 1)}

    bg_a = _bigrams(text_a)
    bg_b = _bigrams(text_b)
    if not bg_a or not bg_b:
        return 0.0
    inter = len(bg_a & bg_b)
    return round((2.0 * inter) / (len(bg_a) + len(bg_b)), 4)


def _select_best_representative(group: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Pick representative copy prioritizing T1 > T1_5 > T2, above-floor quality, score (+3 Chinese near-tie), summary len."""
    ordered = sorted(
        group,
        key=lambda it: (
            TIER_PRIORITY.get(str(it.get("source_tier") or "T2"), 1),
            1 if int(it.get("attention_score") or 0) >= BROWSE_FLOOR else 0,
            int(it.get("attention_score") or 0) + (0 if is_primarily_english(str(it.get("title") or "")) else 3),
            0 if is_primarily_english(str(it.get("title") or "")) else 1,
            len(str(it.get("summary") or "")),
        ),
        reverse=True,
    )
    return ordered[0], ordered[1:]


def _extract_isolation_keys(item: dict[str, Any]) -> dict[str, set[str]]:
    """Extract SEC accession, filing form/date, fiscal quarter, and official notice IDs to prevent over-merging."""
    url = str(item.get("url") or "")
    item_id = str(item.get("id") or "")
    title = str(item.get("title") or "")
    summary = str(item.get("summary") or "")
    src = str(item.get("source") or "")
    text = f"{title} {summary}"

    sec_acc: set[str] = set()
    for m in re.findall(r"/Archives/edgar/data/\d+/(\d{10,20})/", url, re.IGNORECASE):
        sec_acc.add(m)
    for m in re.findall(r"\b(\d{10}-\d{2}-\d{6})\b", f"{url} {text}"):
        sec_acc.add(m.replace("-", ""))

    sec_form_date: set[str] = set()
    forms = re.findall(r"\b(10-K|10-Q|8-K|20-F|6-K)\b", text, re.IGNORECASE)
    if forms or "sec" in src.lower() or sec_acc:
        d_str = str(item.get("time_bjt") or "")[:10]
        syms = ",".join(sorted(str(t).upper() for t in (item.get("tickers") or []) if t))
        for fm in forms or ["FILING"]:
            sec_form_date.add(f"{syms}:{fm.upper()}:{d_str}")

    fiscal_period: set[str] = set()
    for yr, qn in re.findall(r"\b(20\d{2})\s*[-/]?\s*(Q[1-4])\b", text, re.IGNORECASE):
        fiscal_period.add(f"{yr}{qn.upper()}")
    if not fiscal_period:
        for qn in re.findall(r"\b(Q[1-4])\b", text, re.IGNORECASE):
            fiscal_period.add(qn.upper())
        for cn_q in re.findall(r"第([一二三四1234])财季", text):
            q_map = {"一": "Q1", "1": "Q1", "二": "Q2", "2": "Q2", "三": "Q3", "3": "Q3", "四": "Q4", "4": "Q4"}
            if cn_q in q_map:
                fiscal_period.add(q_map[cn_q])

    notice_id: set[str] = set()
    if item_id.startswith(("sec-", "eqn-", "cal-")):
        notice_id.add(item_id)

    return {
        "sec_acc": sec_acc,
        "sec_form_date": sec_form_date,
        "fiscal_period": fiscal_period,
        "notice_id": notice_id,
    }


def _extract_numeric_anchors(text: str) -> set[str]:
    """Extract normalized percentage, bps, model-version, and numeric anchors for cross-lingual matching."""
    low = str(text or "").lower()
    anchors: set[str] = set()
    for num, unit in re.findall(r"(\d+(?:\.\d+)?)\s*(%|bps|bp|个基点|基点)", low):
        u_norm = "%" if unit == "%" else "bps"
        val_norm = num.rstrip("0").rstrip(".") if "." in num else num
        if val_norm:
            anchors.add(f"{val_norm}{u_norm}")
    for model, ver in re.findall(r"\b(gpt|claude|gemini|llama|qwen|deepseek)[-\s]?(\d+(?:\.\d+)?)", low):
        anchors.add(f"{model}{ver}")
    for raw_num in re.findall(r"(?<![a-z0-9.])\d+(?:\.\d+)?(?![a-z0-9.])", low):
        if "." in raw_num:
            val_norm = raw_num.rstrip("0").rstrip(".")
            if val_norm:
                anchors.add(val_norm)
        else:
            try:
                iv = int(raw_num)
            except ValueError:
                continue
            if 2020 <= iv <= 2035 or iv < 32:
                continue
            anchors.add(str(iv))
    return anchors


def _isolation_conflict(keys_a: dict[str, set[str]], keys_b: dict[str, set[str]]) -> bool:
    for field in ("sec_acc", "sec_form_date", "fiscal_period", "notice_id"):
        sa = keys_a.get(field) or set()
        sb = keys_b.get(field) or set()
        if sa and sb and not (sa & sb):
            return True
    return False


def cluster_and_dedup(
    items: list[dict[str, Any]],
    *,
    now_utc: datetime | None = None,
    window_days: int = RECALL_WINDOW_DAYS,
) -> list[dict[str, Any]]:
    """Recall past 14-day peers, classify SAME_OCCURRENCE/SAME_STORY/ROUNDUP/UNRELATED, and dedup."""
    now = now_utc or datetime.now(UTC)
    cutoff = now - timedelta(days=window_days)

    clusters: list[dict[str, Any]] = []
    processed: list[dict[str, Any]] = []

    # Sort newest/highest-tier first so T1 anchors clusters cleanly
    ordered_in = sorted(
        items,
        key=lambda x: (
            TIER_PRIORITY.get(str(x.get("source_tier") or "T2"), 1),
            int(x.get("attention_score") or 0),
            parse_time_utc(x.get("time_bjt"), now_utc=now).timestamp(),
        ),
        reverse=True,
    )

    for raw_item in ordered_in:
        it = dict(raw_item)
        title = str(it.get("title") or "")
        summary = str(it.get("summary") or "")
        norm_u = normalize_url(it.get("url"))
        ts = parse_time_utc(it.get("time_bjt"), now_utc=now)
        tickers = {str(t).upper() for t in (it.get("tickers") or []) if t}
        num_anchors = _extract_numeric_anchors(f"{title} {summary[:160]}")
        iso_keys = _extract_isolation_keys(it)

        item_is_crypto = str(it.get("category") or "").lower() == "crypto"
        domain_tag = "crypto" if item_is_crypto else "mkt"

        if _ROUNDUP_RE.search(title):
            cid = f"clu-{hashlib.sha1(f'roundup|{domain_tag}|{title}'.encode('utf-8')).hexdigest()[:10]}"
            it["cluster_id"] = cid
            it["group_relation"] = "新事件"
            it["relation_code"] = "ROUNDUP"
            processed.append(it)
            continue

        matched_cluster: dict[str, Any] | None = None
        matched_sim = 0.0
        matched_same_url = False

        for cl in clusters:
            if cl["latest_ts"] < cutoff:
                continue
            if item_is_crypto != bool(cl.get("is_crypto")):
                continue
            if _isolation_conflict(iso_keys, cl["iso_keys"]):
                continue
            if norm_u and norm_u in cl["urls"]:
                matched_cluster = cl
                matched_same_url = True
                matched_sim = 1.0
                break
            sim = lexical_similarity(f"{title} {summary[:120]}", cl["rep_text"])
            shared_tickers = tickers & cl["tickers"]
            shared_nums = num_anchors & cl["num_anchors"]
            hours_from_rep = abs((ts - cl["latest_ts"]).total_seconds()) / 3600.0
            cross_lingual_hit = bool(
                len(shared_tickers) >= 1
                and hours_from_rep <= 4.0
                and (len(shared_nums) >= 2 or any(x.endswith(("%", "bps")) for x in shared_nums))
            )
            eff_sim = max(sim, 0.72) if cross_lingual_hit else sim
            if sim >= 0.52 or (sim >= 0.34 and len(shared_tickers) >= 1) or cross_lingual_hit:
                if eff_sim > matched_sim:
                    matched_cluster = cl
                    matched_sim = eff_sim

        if matched_cluster is None:
            cid = f"clu-{hashlib.sha1(f'{domain_tag}|{norm_u or title}'.encode('utf-8')).hexdigest()[:10]}"
            it["cluster_id"] = cid
            it["group_relation"] = "新事件"
            it["relation_code"] = "UNRELATED"
            clusters.append(
                {
                    "id": cid,
                    "rep_text": f"{title} {summary[:120]}",
                    "urls": {norm_u} if norm_u else set(),
                    "tickers": set(tickers),
                    "num_anchors": set(num_anchors),
                    "iso_keys": {k: set(v) for k, v in iso_keys.items()},
                    "is_crypto": str(it.get("category") or "").lower() == "crypto",
                    "latest_ts": ts,
                    "members": [it],
                }
            )
            processed.append(it)
        else:
            it["cluster_id"] = matched_cluster["id"]
            rep_ts = matched_cluster["latest_ts"]
            hours_apart = abs((ts - rep_ts).total_seconds()) / 3600.0
            if matched_same_url or matched_sim >= 0.65 or hours_apart <= 8.0:
                it["relation_code"] = "SAME_OCCURRENCE"
                it["group_relation"] = "同事件合并"
            else:
                it["relation_code"] = "SAME_STORY"
                it["group_relation"] = "后续进展"

            if norm_u:
                matched_cluster["urls"].add(norm_u)
            matched_cluster["tickers"] |= tickers
            matched_cluster["num_anchors"] |= num_anchors
            for field, vals in iso_keys.items():
                matched_cluster["iso_keys"][field] |= vals
            if ts > matched_cluster["latest_ts"]:
                matched_cluster["latest_ts"] = ts
            matched_cluster["members"].append(it)

    # Collapse SAME_OCCURRENCE duplicates to one representative machine row (AIHOT scope rule),
    # while recording merged_sources metadata on the surviving representative.
    deduped: list[dict[str, Any]] = []
    for cl in clusters:
        members = cl["members"]
        occ_group = [m for m in members if m.get("relation_code") in ("UNRELATED", "SAME_OCCURRENCE")]
        followups = [m for m in members if m.get("relation_code") == "SAME_STORY"]

        if occ_group:
            rep, merged_others = _select_best_representative(occ_group)
            all_sources = list(dict.fromkeys([str(m.get("source") or "") for m in occ_group if m.get("source")]))
            merged_topics: list[str] = []
            for m in occ_group:
                for tp in m.get("topics") or []:
                    tp_s = str(tp).strip()
                    if tp_s and tp_s not in merged_topics:
                        merged_topics.append(tp_s)
            rep = dict(rep)
            if merged_topics:
                rep["topics"] = merged_topics[:6]
            rep["merged_sources"] = all_sources
            rep["merged_count"] = len(occ_group)
            if merged_others:
                rep["group_relation"] = "同事件合并"
            else:
                rep["group_relation"] = "新事件"
            # Preserve participants list across occurrence copies for 48h unique-participant heat
            rep["_participants"] = [
                {
                    "source": str(m.get("source") or "unknown"),
                    "tier": str(m.get("source_tier") or "T2"),
                    "ts": parse_time_utc(m.get("time_bjt"), now_utc=now),
                }
                for m in occ_group
            ]
            deduped.append(rep)

        for fw in followups:
            fw_copy = dict(fw)
            fw_copy["merged_sources"] = [str(fw_copy.get("source") or "")]
            fw_copy["merged_count"] = 1
            fw_copy["_participants"] = [
                {
                    "source": str(fw_copy.get("source") or "unknown"),
                    "tier": str(fw_copy.get("source_tier") or "T2"),
                    "ts": parse_time_utc(fw_copy.get("time_bjt"), now_utc=now),
                }
            ]
            deduped.append(fw_copy)

    # Append ROUNDUP items directly
    for it in processed:
        if it.get("relation_code") == "ROUNDUP":
            it_copy = dict(it)
            it_copy["merged_sources"] = [str(it_copy.get("source") or "")]
            it_copy["merged_count"] = 1
            it_copy["_participants"] = [
                {
                    "source": str(it_copy.get("source") or "unknown"),
                    "tier": str(it_copy.get("source_tier") or "T2"),
                    "ts": parse_time_utc(it_copy.get("time_bjt"), now_utc=now),
                }
            ]
            deduped.append(it_copy)

    return deduped


def compute_48h_heat(
    items: list[dict[str, Any]],
    *,
    now_utc: datetime | None = None,
) -> list[dict[str, Any]]:
    """Compute heat-v1-48h-halflife24h with 1-vote-per-participant cap and 48h backfill guard."""
    now = now_utc or datetime.now(UTC)

    # Group cluster-wide participants across surviving items so follow-ups inherit story momentum
    cluster_participants: dict[str, dict[str, tuple[datetime, str]]] = {}
    for it in items:
        cid = str(it.get("cluster_id") or it.get("id") or "")
        part_map = cluster_participants.setdefault(cid, {})
        parts = it.get("_participants") or [
            {
                "source": str(it.get("source") or "unknown"),
                "tier": str(it.get("source_tier") or "T2"),
                "ts": parse_time_utc(it.get("time_bjt"), now_utc=now),
            }
        ]
        for p in parts:
            src_name = str(p.get("source") or "unknown")
            pts = p.get("ts") or now
            ptier = str(p.get("tier") or "T2")
            if src_name not in part_map or pts > part_map[src_name][0]:
                part_map[src_name] = (pts, ptier)

    enriched: list[dict[str, Any]] = []
    for it in items:
        out = dict(it)
        out.pop("_participants", None)
        cid = str(out.get("cluster_id") or out.get("id") or "")
        item_ts = parse_time_utc(out.get("time_bjt"), now_utc=now)
        item_age_h = max(0.0, (now - item_ts).total_seconds() / 3600.0)
        is_backfill = item_age_h > HEAT_WINDOW_HOURS

        part_map = cluster_participants.get(cid) or {}
        heat_sum = 0.0
        active_participants = 0
        for pts, ptier in part_map.values():
            age_h = max(0.0, (now - pts).total_seconds() / 3600.0)
            if age_h > HEAT_WINDOW_HOURS:
                continue
            active_participants += 1
            tier_w = {"T1": 1.4, "T1_5": 1.15, "T2": 1.0}.get(ptier, 1.0)
            decay = 2.0 ** (-age_h / HALF_LIFE_HOURS)
            heat_sum += tier_w * decay

        heat_val = round(min(100.0, heat_sum * 20.0), 1)
        if is_backfill:
            badge = "归档"
        elif item_age_h <= 4.0 and (int(out.get("attention_score") or 0) >= 65 or heat_val >= 20.0):
            badge = "突发"
        elif active_participants >= 2 or heat_val >= 25.0:
            badge = "升温"
        else:
            badge = "持续"

        out["is_backfill"] = is_backfill
        out["heat_rule"] = HOT_RULE_VERSION
        out["participant_count"] = active_participants
        out["heat_48h"] = heat_val
        out["heat_badge"] = badge
        enriched.append(out)

    return enriched


def process_financial_signals(
    raw_items: list[dict[str, Any]],
    *,
    now_utc: datetime | None = None,
    receipts_path: Path = RECEIPTS_PATH,
    browse_floor: int = BROWSE_FLOOR,
    max_per_source: int = 10,
    max_visible: int = 85,
    max_candidates: int = 250,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run full Kazike pipeline: prefilter -> blind 5-axis score -> tier gate -> 14d cluster/dedup -> 48h heat."""
    now = now_utc or datetime.now(UTC)
    rc = _load_receipts(receipts_path)

    kept_pre: list[dict[str, Any]] = []
    dropped_prefilter = 0
    for raw in raw_items:
        it = dict(raw)
        verdict, pre_reason = prefilter_financial(it)
        if verdict == "BLOCK":
            dropped_prefilter += 1
            continue

        tier = infer_source_tier(it)
        scored = score_five_axes(it, receipts=rc, receipts_path=receipts_path, persist_receipt=True, now_utc=now)
        score = int(scored["score"])
        threshold = TIER_THRESHOLDS.get(tier, 76)
        selected = (score >= threshold) and (verdict == "PASS")

        it["prefilter_label"] = verdict
        it["prefilter_reason"] = pre_reason
        it["source_tier"] = tier
        it["source_tier_label"] = TIER_LABELS.get(tier, "T2媒体KOL")
        it["content_type"] = scored["content_type"]
        it["axes"] = scored["axes"]
        it["attention_score"] = score
        it["selected"] = selected
        it["receipt_id"] = scored["receipt_id"]
        if selected and score >= 75:
            it["importance"] = "高"
        kept_pre.append(it)

    _prune_receipts(rc, now_ts=now.timestamp())
    atomic_write_json(receipts_path, rc)

    clustered = cluster_and_dedup(kept_pre, now_utc=now, window_days=RECALL_WINDOW_DAYS)
    heated = compute_48h_heat(clustered, now_utc=now)

    # Sort all deduplicated heated items for bounded candidate pool (up to max_candidates)
    heated_sorted = sorted(
        heated,
        key=lambda s: (
            0 if s.get("is_backfill") else 1,
            1 if s.get("selected") else 0,
            int(s.get("attention_score") or 0),
            float(s.get("heat_48h") or 0.0),
            str(s.get("time_bjt") or ""),
        ),
        reverse=True,
    )
    bounded_candidates = [
        {k: v for k, v in item.items() if not str(k).startswith("_")}
        for item in heated_sorted[: max(0, int(max_candidates))]
    ]

    # Suppress < browse_floor spam in default listing while retaining critical live market wrappers
    candidates = [
        x
        for x in heated_sorted
        if int(x.get("attention_score") or 0) >= browse_floor or str(x.get("source") or "") == "美股盘面"
    ]

    # Per-source rate cap (always exempt "美股盘面" summary cards) + 5-sector balance guarantee
    core_sectors = ("equities", "ashare", "ai", "crypto", "macro")
    crypto_cap = max(2, max_visible // 8) if max_visible > 0 else 0

    # Group non-wrap items by source, keeping round-robin sector diversity within each source
    by_source: dict[str, list[dict[str, Any]]] = {}
    market_wraps: list[dict[str, Any]] = []
    for item in candidates:
        src_name = str(item.get("source") or "unknown")
        if src_name == "美股盘面":
            market_wraps.append(item)
        else:
            by_source.setdefault(src_name, []).append(item)

    allowed_ids: set[int] = {id(x) for x in market_wraps}
    for src_name, src_items in by_source.items():
        if max_per_source <= 0 or len(src_items) <= max_per_source:
            allowed_ids.update(id(x) for x in src_items)
            continue
        # Interleave categories from the same source so a single wire with ashare+equities doesn't crowd out one category
        cat_buckets: dict[str, list[dict[str, Any]]] = {}
        for it in src_items:
            c = str(it.get("category") or "other").lower()
            cat_buckets.setdefault(c, []).append(it)
        picked_for_src: list[dict[str, Any]] = []
        bucket_keys = sorted(
            cat_buckets.keys(),
            key=lambda ck: (
                0 if cat_buckets[ck][0].get("is_backfill") else 1,
                1 if cat_buckets[ck][0].get("selected") else 0,
                int(cat_buckets[ck][0].get("attention_score") or 0),
            ),
            reverse=True,
        )
        while len(picked_for_src) < max_per_source:
            progressed = False
            for ck in bucket_keys:
                if cat_buckets[ck] and len(picked_for_src) < max_per_source:
                    picked_for_src.append(cat_buckets[ck].pop(0))
                    progressed = True
            if not progressed:
                break
        allowed_ids.update(id(x) for x in picked_for_src)

    capped = [item for item in candidates if id(item) in allowed_ids]
    non_crypto_total = sum(1 for x in capped if str(x.get("category") or "").lower() != "crypto")
    if len(capped) > 4:
        eff_crypto_cap = max(2, min(crypto_cap if crypto_cap > 0 else len(capped), len(capped) // 4))
    else:
        eff_crypto_cap = crypto_cap if crypto_cap > 0 else len(capped)

    if max_visible > 0 and len(capped) > max_visible:
        # Reserve balanced sector floor (up to min_per_sector each across 5 core sectors) + market wraps
        min_per_sector = min(8, max(2, max_visible // 10))
        chosen_ids: set[int] = {id(w) for w in market_wraps}
        chosen_crypto = sum(1 for w in market_wraps if str(w.get("category") or "").lower() == "crypto")
        for sec in core_sectors:
            sec_limit = min(min_per_sector, eff_crypto_cap) if sec == "crypto" else min_per_sector
            sec_hits = [
                x for x in capped
                if id(x) not in chosen_ids and str(x.get("category") or "").lower() == sec
            ]
            for x in sec_hits[:sec_limit]:
                if len(chosen_ids) < max_visible:
                    chosen_ids.add(id(x))
                    if sec == "crypto":
                        chosen_crypto += 1
        for x in capped:
            if len(chosen_ids) >= max_visible:
                break
            if id(x) in chosen_ids:
                continue
            is_cr = str(x.get("category") or "").lower() == "crypto"
            if is_cr and chosen_crypto >= eff_crypto_cap:
                continue
            chosen_ids.add(id(x))
            if is_cr:
                chosen_crypto += 1
        if len(chosen_ids) < max_visible and non_crypto_total < max_visible:
            for x in capped:
                if len(chosen_ids) >= max_visible:
                    break
                if id(x) not in chosen_ids and str(x.get("category") or "").lower() != "crypto":
                    chosen_ids.add(id(x))
        # Ensure market_wraps are never truncated if max_visible < len(market_wraps)
        visible = [x for x in capped if id(x) in chosen_ids][:max_visible]
        present_ids = {id(x) for x in visible}
        missing_wraps = [w for w in market_wraps if id(w) not in present_ids]
        if missing_wraps:
            non_wraps = [x for x in visible if str(x.get("source") or "") != "美股盘面"]
            keep_n = max(0, max_visible - len(market_wraps))
            visible = non_wraps[:keep_n] + market_wraps
            visible.sort(
                key=lambda s: (
                    0 if s.get("is_backfill") else 1,
                    1 if s.get("selected") else 0,
                    int(s.get("attention_score") or 0),
                    float(s.get("heat_48h") or 0.0),
                    str(s.get("time_bjt") or ""),
                ),
                reverse=True,
            )
    else:
        # Enforce crypto quota even when total candidates <= max_visible
        visible = []
        cr_seen = 0
        for x in capped:
            is_cr = str(x.get("category") or "").lower() == "crypto"
            if is_cr and len(capped) > 4:
                if cr_seen >= eff_crypto_cap:
                    continue
                cr_seen += 1
            visible.append(x)

    if len(visible) > 2:
        head_kept: list[dict[str, Any]] = []
        non_cr_tail: list[dict[str, Any]] = []
        cr_tail: list[dict[str, Any]] = []
        cr_head = 0
        for x in visible:
            is_cr = str(x.get("category") or "").lower() == "crypto"
            if len(head_kept) < 15:
                if is_cr and cr_head >= 2:
                    cr_tail.append(x)
                else:
                    head_kept.append(x)
                    if is_cr:
                        cr_head += 1
            elif is_cr:
                cr_tail.append(x)
            else:
                non_cr_tail.append(x)
        visible = head_kept + non_cr_tail + cr_tail

    attach_zh_briefs_batch(
        visible + bounded_candidates[:80],
        allow_network=not bool(os.environ.get("PYTEST_CURRENT_TEST")),
        max_network_items=60,
    )

    source_tally: dict[str, int] = {}
    for x in visible:
        src_name = str(x.get("source") or "").strip()
        if src_name:
            source_tally[src_name] = source_tally.get(src_name, 0) + 1

    stats = {
        "raw_count": len(raw_items),
        "dropped_prefilter": dropped_prefilter,
        "after_dedup": len(heated),
        "visible_ge_browse_floor": len(visible),
        "selected_count": sum(1 for x in visible if x.get("selected")),
        "backfill_count": sum(1 for x in visible if x.get("is_backfill")),
        "source_count": len(source_tally),
        "source_breakdown": dict(sorted(source_tally.items(), key=lambda kv: (-kv[1], kv[0]))),
        "candidates_count": len(bounded_candidates),
        "candidates": bounded_candidates,
    }
    return (visible, stats)
