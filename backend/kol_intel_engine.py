"""KOL historical backtest evaluator, Chinese view distiller, and signal converter."""
from __future__ import annotations

import hashlib
import json
import math
import re
import sys
from bisect import bisect_left
from datetime import date, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any

_BACKEND_DIR = Path(__file__).resolve().parent
if str(_BACKEND_DIR.parent) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR.parent))

from backend.guardrails import (
    FORBIDDEN_TERMS as FORBIDDEN_TERMS,
    has_private_article_content,
    safe_article_filename,
    sanitize_public_value,
    scrub_forbidden,
)
from backend.runtime import (
    BJT,
    DATA_DIR,
    UTC,
    atomic_write_json,
    read_json_safe as _read_json,
    resolve_kol_dir,
)
from backend.fred_and_market import FRED_CACHE_PATH
from backend.signal_text import AI_TOKEN_CONTEXT_RE, CRYPTO_EXPLICIT_RE, classify_kol_topic, extract_tickers as _extract_tickers

extract_tickers = partial(_extract_tickers, source="kol")

DEFAULT_KOL_DIR = resolve_kol_dir()
DEFAULT_STOCK_PROFILES_CACHE_PATH = DATA_DIR / "stock_profiles_cache.json"
DEFAULT_FRED_CACHE_PATH = FRED_CACHE_PATH
DEFAULT_SCORECARD_CACHE_PATH = DATA_DIR / "kol_scorecard_cache.json"

MIN_VERIFIED_SAMPLE = 5
CONSOLIDATION_DAYS = 5
LOOKAHEAD_DAYS_5 = 5
LOOKAHEAD_DAYS_20 = 20

# Pure news wires / spam accounts to exclude from KOL evaluation & article digests
MEDIA_WIRE_HANDLES: frozenset[str] = frozenset(
    {
        "benzinga",
        "deitaone",
        "firstsquawk",
        "watcherguru",
        "stockmktnewz",
        "reuters",
        "business",
        "bloomberg",
        "ft",
        "cnbc",
        "wsjmarkets",
        "yahoofinance",
        "investing.com",
        "investingcom",
        "politlcsglobal",
        "politlcsuk",
        "therundownai",
        "theblock__",
        "theblock",
        "csis",
        "fxhedge",
        "zerohedge",
        "unusualwhales_news",
        "financialjuice",
        "tier10k",
        "newsquawk",
    }
)

# Official AI labs / research leaders allowed in quality longform articles when deep & substantial
OFFICIAL_AI_RESEARCH_HANDLES: frozenset[str] = frozenset(
    {
        "openai",
        "anthropicai",
        "googleai",
        "deepmind",
        "nvidia",
        "karpathy",
        "sama",
        "gdb",
        "demishassabis",
        "ylecun",
    }
)

# Backtest symbol mapping (to real price series keys)
_BACKTEST_PRICE_ALIASES: dict[str, str] = {
    "SPY": "SP500",
    "SPX": "SP500",
    "SP500": "SP500",
    "GLD": "GOLD",
    "XAU": "GOLD",
    "GOLD": "GOLD",
    "USO": "WTI",
    "WTI": "WTI",
    "DXY": "DOLLAR",
    "DOLLAR": "DOLLAR",
    "BTC": "BTC",
}

# Stance regexes with negation awareness
_BULL_RE = re.compile(
    r"(?i)\b(?:bull|bullish|buy|buying|bought|long|longing|breakout|rally|rallies|accumulate|"
    r"undervalued|upside|rebound|recovery|outperform|beat|beats|surge|soar|boom|tailwind|"
    r"accelerate|accelerating|constrained\s+to|price\s+hike|raise\s+guidance|upgraded?)\b|"
    r"看多|看好|看涨|抄底|做多|突破|低估|反弹|利好|买入|加仓|建仓|见底|回暖|超预期|大涨|走强|上修|上调|供不应求|景气|顺风|增持"
)
_BEAR_RE = re.compile(
    r"(?i)\b(?:bear|bearish|short|shorting|sell|selling|sold|crash|dump|bubble|overvalued|"
    r"recession|outflow|warning|beware|breakdown|liquidation|risk-off|tightening|headwind|"
    r"selloff|sell-off|plunge|drop|decline|slump|weak|miss|missed|cut\s+guidance|downgrade|"
    r"drawdown|default|bankrupt|layoff|tariff\s+shock)\b|"
    r"看空|看跌|做空|暴跌|泡沫|减仓|清仓|止损|破位|风险|利空|抽血|崩盘|熊市|缩表|承压|撤退|"
    r"割肉|抛售|亏损|下跌|跌至|爆仓|砸盘|恐慌|走弱|不及预期|下修|下调|收紧|鹰派|警惕|高估|见顶"
)
_NEGATION_PREFIX_RE = re.compile(
    r"(?i)(?:\b(?:not|never|no|hardly|unlikely\s+to|don't|do\s+not|isn't|aren't|won't)\s+$|"
    r"(?:并非|不是|并不|不要|别|切勿|尚未|难以)$)"
)
_FALSE_BEAR_PHRASES_RE = re.compile(r"(?i)\b(?:short\s+squeeze|short\s+term|short-term|shortage|short\s+supply|in\s+short|sell\s+out|sold\s+out|drop\s+in\s+yields)\b")
_FALSE_BULL_PHRASES_RE = re.compile(r"(?i)\b(?:long\s+term|long-term|long\s+run|long\s+way|before\s+long|bull\s+trap)\b")

_FINANCE_ENTITY_RE = re.compile(
    r"(?i)(?:\$[A-Z]{2,5}\b|\b(?:fed|fomc|cpi|pce|gdp|ism|nfp|powell|treasury|yields?|rates?|"
    r"inflation|deflation|recession|liquidity|deficit|debt|tariff|equity|equities|stocks?|"
    r"market|markets|nasdaq|sp500|s&p|dow|russell|vix|etf|options?|option\s+(?:calls?|puts?)|"
    r"(?:calls?|puts?)\s+(?:sweep|sweeps|flow|volume|oi|open\s+interest)|call/put|put/call|gamma|strike\s+price|"
    r"earnings|revenue|guidance|margin|capex|valuation|pe\s+ratio|buyback|dividend|"
    r"semiconductor|gpu|hbm|dram|nand|cowos|foundry|wafer|datacenter|data\s+center|"
    r"bitcoin|crypto|btc|eth|sol|stablecoin|usdc|usdt|on-?chain|whale|exchange|defi|perp|"
    r"gold|silver|crude|oil|wti|brent|dollar|dxy|fx|yen|jpy|eur|cnh|portfolio|hedge)\b|"
    r"美联储|联储|利率|降息|加息|通胀|通缩|非农|美债|收益率|财政|赤字|流动性|估值|市盈率|"
    r"财报|营收|利润|毛利率|资本开支|回购|股息|期权|看涨期权|看跌期权|做多|做空|看多|看空|牛市|熊市|回调|"
    r"仓位|止损|杠杆|量化|因子|回撤|波动率|标普|纳指|美股|港股|A股|半导体|晶圆|算力|"
    r"芯片|存储|链上|巨鲸|鲸鱼|交易所|稳定币|比特币|以太坊|加密货币|黄金|原油|美元|汇率|股市|股票|股价|市场|宏观|经济|关税)"
)

_OPTIONS_EXPLICIT_RE = re.compile(
    r"(?i)\b(?:options?|option\s+(?:calls?|puts?)|(?:calls?|puts?)\s+(?:sweep|sweeps|flow|volume|oi|open\s+interest)|"
    r"(?:call|put)\s+option|call/put|put/call|gamma|dark\s+pool|strike\s+price)\b|期权|看涨期权|看跌期权"
)

_CHATTER_NOISE_RE = re.compile(
    r"(?i)(?:happy\s+thanksgiving|merry\s+christmas|good\s+morning|standup\s+comedy|"
    r"updog|mimosas|breakfast|breakkie|bands\s+have\s+the\s+most\s+misleading|"
    r"manage\s+women['’]s\s+emotions|psychology\s+of\s+money\s+podcast|"
    r"being\s+an\s+expert\s+on\s+a\s+life|how\s+do\s+you\s+come\s+up\s+with\s+your\s+jokes)"
)

_AD_FOOTER_RE = re.compile(
    r"(?:本条由\s*@\S+\s*赞助.*|[-—]{6,}\s*#?(?:Bitget|OKX|Bybit|Binance).*|"
    r"「?(?:Bitget|OKX|Bybit)[^」\n]*(?:返现|邀请|VIP|买美股).*)",
    re.IGNORECASE | re.DOTALL,
)

_CATEGORY_LABEL_MAP: dict[str, str] = {
    "macro": "宏观与流动性",
    "us-stocks": "AI与美股",
    "quant": "量化与因子",
    "crypto": "加密与链上",
    "investing": "长期配置",
}

_GROUP_LABEL_MAP: dict[str, str] = {
    "macro": "宏观与风控",
    "us-stocks": "AI与美股",
    "quant": "量化与配置",
    "crypto": "加密与链上",
    "investing": "量化与配置",
}

_ROLE_LABEL_MAP: dict[str, str] = {
    "macro_guru": "宏观策略",
    "hedge_fund": "机构投研",
    "ai_semi": "AI算力产业",
    "quant_trader": "量化系统",
    "crypto_alpha": "加密链上",
    "news_wire": "媒体快讯",
}

_TRANSLATION_TOPICS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)\b(?:cowos|packaging|foundry|wafer|tsmc)\b|台积电"), "先进封装与晶圆产能"),
    (re.compile(r"(?i)\b(?:hbm4?|dram|nand|memory|storage)\b|存储|美光"), "存储与高带宽内存供需"),
    (re.compile(r"(?i)\b(?:blackwell|rubin|gpu|datacenter|capex)\b|data\s+center|算力|服务器"), "AI算力资本开支"),
    (re.compile(r"(?i)purchase\s+commitments|\b(?:backlog|constrained)\b|supply\s+chain|供应链|排产"), "算力供应链订单约束"),
    (re.compile(r"(?i)\b(?:earnings|revenue|guidance|margin|eps)\b|财报|营收|利润|指引"), "业绩指引与盈利兑现"),
    (re.compile(r"(?i)\b(?:fed|fomc|powell|interest\s+rate)\b|rate\s+cut|rate\s+hike|美联储|降息|加息|利率"), "美联储利率路径"),
    (re.compile(r"(?i)\b(?:cpi|pce|inflation|stagflation)\b|通胀|物价"), "通胀粘性与政策掣肘"),
    (re.compile(r"(?i)\b(?:deficit|fiscal|treasury|auction|debt)\b|bond\s+yield|财政|赤字|美债|发债"), "财政赤字与美债供给"),
    (re.compile(r"(?i)\b(?:sp500|nasdaq|qqq|spy|correction|breadth|drawdown)\b|s&p\s*500|回调|市场宽度|均线"), "美股指数与市场宽度"),
    (_OPTIONS_EXPLICIT_RE, "期权资金流与波动"),
    (re.compile(r"(?i)\b(?:whale|on-?chain|inflow|outflow)\b|鲸鱼|链上|交易所|转入|转出"), "链上巨鲸与筹码流向"),
    (CRYPTO_EXPLICIT_RE, "加密资金面与周期"),
    (re.compile(r"(?i)\b(?:gold|silver|oil|wti|brent|commodity)\b|黄金|原油|大宗"), "大宗商品与避险资产"),
    (re.compile(r"(?i)\b(?:tariff|sanction|geopolit\w*)\b|export\s+control|关税|制裁|出口管制|地缘"), "地缘政策与贸易冲击"),
    (re.compile(r"(?i)\b(?:valuation|multiple|bubble|overvalued|undervalued)\b|估值|泡沫|低估"), "估值性价比与风险溢价"),
)


def sanitize_output(obj: Any) -> Any:
    return sanitize_public_value(obj, scrub_keys=True, skip_keys=frozenset())


def _is_media_wire(handle: str, acct_info: dict[str, Any] | None = None, *, allow_official_ai: bool = False) -> bool:
    h_low = handle.strip().lower()
    if h_low in MEDIA_WIRE_HANDLES:
        return True
    if allow_official_ai and h_low in OFFICIAL_AI_RESEARCH_HANDLES:
        return False
    if acct_info:
        if str(acct_info.get("role") or "").lower() == "news_wire":
            return True
        if str(acct_info.get("type") or "").lower() == "news":
            if allow_official_ai and h_low in OFFICIAL_AI_RESEARCH_HANDLES:
                return False
            return True
    return False


def _infer_category(handle: str, subdir: str, acct_info: dict[str, Any]) -> str:
    cat = str(acct_info.get("category") or "").strip().lower()
    if cat in _CATEGORY_LABEL_MAP:
        return cat
    sub = subdir.strip().lower()
    if sub in _CATEGORY_LABEL_MAP:
        return sub
    tags = {str(t).strip().lower() for t in (acct_info.get("tags") or [])}
    if "加密" in tags or "链上分析" in tags:
        return "crypto"
    if "量化" in tags or "因子" in tags:
        return "quant"
    if "宏观" in tags and "美股" not in tags and "ai" not in tags:
        return "macro"
    if "投资" in tags and "美股" not in tags:
        return "investing"
    return "us-stocks"


def _infer_role_label(category: str, acct_info: dict[str, Any]) -> str:
    role = str(acct_info.get("role") or "").strip().lower()
    if role in _ROLE_LABEL_MAP:
        return _ROLE_LABEL_MAP[role]
    tags = " ".join(str(t) for t in (acct_info.get("tags") or []))
    focus = str(acct_info.get("focus") or "")
    combined = f"{tags} {focus}".lower()
    if any(k in combined for k in ("机构", "高盛", "aqr", "hedge", "fomc", "经济学家")):
        return "机构投研"
    if any(k in combined for k in ("半导体", "存储", "ai", "芯片", "机器人")):
        return "AI算力产业"
    if category == "crypto" or "链上" in combined or "鲸鱼" in combined:
        return "加密链上"
    if category == "quant" or "量化" in combined or "因子" in combined:
        return "量化系统"
    if category == "macro" or "宏观" in combined or "利率" in combined:
        return "宏观策略"
    if category == "investing":
        return "长期配置"
    return "美股实战"


def _strip_tweet_noise(raw_text: str) -> str:
    s = raw_text.strip()
    s = _AD_FOOTER_RE.sub("", s).strip()
    s = re.sub(r"https?://\S+", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _is_third_party_rt(raw_text: str, handle: str) -> bool:
    m = re.match(r"^\s*RT\s+@([A-Za-z0-9_]+)\s*:", raw_text, re.IGNORECASE)
    if not m:
        return False
    return m.group(1).lower() != handle.lower()


def _detect_stance(text: str) -> tuple[str, float]:
    """Return ('偏多'|'偏空'|'中性', directional_score in [-1.0, 1.0])."""
    cleaned = _FALSE_BEAR_PHRASES_RE.sub(" ", text)
    cleaned = _FALSE_BULL_PHRASES_RE.sub(" ", cleaned)

    bull_hits = 0
    for m in _BULL_RE.finditer(cleaned):
        prefix = cleaned[max(0, m.start() - 18) : m.start()]
        if _NEGATION_PREFIX_RE.search(prefix):
            bull_hits -= 1
        else:
            bull_hits += 1

    bear_hits = 0
    for m in _BEAR_RE.finditer(cleaned):
        prefix = cleaned[max(0, m.start() - 18) : m.start()]
        if _NEGATION_PREFIX_RE.search(prefix):
            bear_hits -= 1
        else:
            bear_hits += 1

    bull_n = max(0, bull_hits)
    bear_n = max(0, bear_hits)
    if bull_n == 0 and bear_n == 0:
        return ("中性", 0.0)
    total = bull_n + bear_n
    score = (bull_n - bear_n) / (total + 1.0)
    if bull_n > bear_n and score >= 0.15:
        return ("偏多", round(score, 3))
    if bear_n > bull_n and score <= -0.15:
        return ("偏空", round(score, 3))
    return ("中性", round(score, 3))


def _is_market_relevant(text: str, tickers: list[str]) -> bool:
    if not text or len(text.strip()) < 14:
        return False
    if _CHATTER_NOISE_RE.search(text) and not tickers:
        return False
    if tickers:
        return True
    return bool(_FINANCE_ENTITY_RE.search(text))


def _parse_daily_file(
    path: Path,
    subdir: str,
    *,
    as_of_date: date,
) -> tuple[str, list[dict[str, Any]]]:
    """Parse one daily KOL note file into structured per-tweet records."""
    fname = path.name
    if "profile" in fname.lower() or fname.lower().startswith(("readme", "kol_", "library_")):
        return ("", [])

    m_std = re.match(r"^(\d{4}-\d{2}-\d{2})_([A-Za-z0-9_.-]+)\.md$", fname)
    if not m_std:
        return ("", [])

    file_date_str = m_std.group(1)
    handle_raw = re.sub(r"_scraped$", "", m_std.group(2), flags=re.IGNORECASE)
    handle = handle_raw.lower()
    try:
        file_date = datetime.strptime(file_date_str, "%Y-%m-%d").date()
    except ValueError:
        return ("", [])

    if file_date > as_of_date:
        return ("", [])

    try:
        txt = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ("", [])

    if "_无新内容_" in txt or len(txt.strip()) < 80:
        return (handle, [])

    tweets: list[dict[str, Any]] = []
    # Standard daily note blocks: ### 1. [11月25日 20:43] or ### 1. [06-21 01:17] or ### 2025-11-14 | ...
    parts = re.split(r"(?m)^###\s+", txt)
    for blk in parts[1:]:
        lines = blk.splitlines()
        if not lines:
            continue
        hdr = lines[0].strip()
        tweet_date = file_date
        tweet_hhmm = "12:00"

        m_cn = re.search(r"\[(\d{1,2})月(\d{1,2})日\s+(\d{2}:\d{2})\]", hdr)
        m_md = re.search(r"\[(\d{2})-(\d{2})\s+(\d{2}:\d{2})\]", hdr)
        m_iso = re.match(r"(\d{4}-\d{2}-\d{2})\b", hdr)
        if m_cn:
            mm, dd, hhmm = int(m_cn.group(1)), int(m_cn.group(2)), m_cn.group(3)
            yr = file_date.year - 1 if (file_date.month == 1 and mm == 12) else file_date.year
            try:
                tweet_date = date(yr, mm, dd)
                tweet_hhmm = hhmm
            except ValueError:
                pass
        elif m_md:
            mm, dd, hhmm = int(m_md.group(1)), int(m_md.group(2)), m_md.group(3)
            yr = file_date.year - 1 if (file_date.month == 1 and mm == 12) else file_date.year
            try:
                tweet_date = date(yr, mm, dd)
                tweet_hhmm = hhmm
            except ValueError:
                pass
        elif m_iso:
            try:
                tweet_date = datetime.strptime(m_iso.group(1), "%Y-%m-%d").date()
            except ValueError:
                pass

        if tweet_date > as_of_date:
            continue

        quote_lines: list[str] = []
        body_lines: list[str] = []
        url = ""
        likes = 0
        retweets = 0

        for ln in lines[1:]:
            s = ln.strip()
            if s.startswith(">"):
                quote_lines.append(s.lstrip("> ").strip())
            elif re.search(r"https?://(?:x|twitter)\.com/[A-Za-z0-9_]+/status/\d+", s):
                m_u = re.search(r"https?://(?:x|twitter)\.com/[A-Za-z0-9_]+/status/\d+", s)
                if m_u:
                    url = m_u.group(0)
            elif s.startswith(("- 转发", "- RT", "- 互动")):
                m_rt = re.search(r"(?:转发|RT)\s*[:：]\s*(\d+)", s)
                m_lk = re.search(r"(?:点赞|❤️)\s*[:：]\s*(\d+)", s)
                if m_rt:
                    retweets = int(m_rt.group(1))
                if m_lk:
                    likes = int(m_lk.group(1))
            elif s and not s.startswith(("-", "*", "#")):
                body_lines.append(s)

        raw_body = "\n".join(quote_lines if quote_lines else body_lines).strip()
        if not raw_body:
            continue

        third_party_rt = _is_third_party_rt(raw_body, handle_raw)
        clean_body = re.sub(r"^RT\s+@[A-Za-z0-9_]+\s*:\s*", "", _strip_tweet_noise(raw_body), flags=re.IGNORECASE).strip()
        if len(clean_body) < 16:
            continue

        is_longform = "【X长文】" in raw_body or len(clean_body) >= 420
        tickers = extract_tickers(clean_body)
        if not _is_market_relevant(clean_body, tickers):
            continue

        stance, conv_score = _detect_stance(clean_body)
        has_numbers = bool(re.search(r"\d+(?:\.\d+)?\s*(?:%|B|M|K|亿|万|倍|bps|bp|x)\b|\$\d+", clean_body, re.IGNORECASE))

        tweets.append(
            {
                "handle": handle,
                "subdir": subdir,
                "date": tweet_date.isoformat(),
                "date_obj": tweet_date,
                "time_bjt": f"{tweet_date.isoformat()} {tweet_hhmm}",
                "raw_text": raw_body,
                "clean_text": clean_body,
                "tickers": tickers,
                "stance": stance,
                "conviction": conv_score,
                "third_party_rt": third_party_rt,
                "is_longform": is_longform,
                "has_numbers": has_numbers,
                "likes": likes,
                "retweets": retweets,
                "url": url,
            }
        )

    return (handle, tweets)


def load_price_series(
    *,
    stock_profiles_path: Path | None = None,
    fred_cache_path: Path | None = None,
    price_series: dict[str, tuple[list[str], list[float]]] | None = None,
) -> dict[str, tuple[list[str], list[float]]]:
    """Load daily closing price series from stock_profiles_cache.json and fred_cache.json."""
    if price_series is not None:
        return price_series

    out: dict[str, tuple[list[str], list[float]]] = {}
    sp_path = stock_profiles_path or DEFAULT_STOCK_PROFILES_CACHE_PATH
    sp_data = _read_json(sp_path, {})
    if isinstance(sp_data, dict):
        for sym, payload in sp_data.items():
            if not isinstance(payload, dict):
                continue
            chart = payload.get("chart") or {}
            dates = chart.get("dates") or []
            closes = chart.get("closes") or []
            if len(dates) == len(closes) and len(dates) >= 2:
                paired = sorted(
                    ((str(d)[:10], float(c)) for d, c in zip(dates, closes) if d and c is not None and float(c) > 0),
                    key=lambda x: x[0],
                )
                if paired:
                    out[str(sym).upper()] = ([p[0] for p in paired], [p[1] for p in paired])

    fc_path = fred_cache_path or DEFAULT_FRED_CACHE_PATH
    fc_data = _read_json(fc_path, {})
    series_map = fc_data.get("series") if isinstance(fc_data, dict) else {}
    if isinstance(series_map, dict):
        # Skip US10Y & VIX in directional price backtest due to yield-vs-price polarity ambiguity
        for key in ("BTC", "GOLD", "SP500", "WTI", "DOLLAR"):
            pts = series_map.get(key) or []
            paired = sorted(
                ((str(p[0])[:10], float(p[1])) for p in pts if isinstance(p, (list, tuple)) and len(p) >= 2 and p[1] is not None and float(p[1]) > 0),
                key=lambda x: x[0],
            )
            if paired:
                out[key] = ([p[0] for p in paired], [p[1] for p in paired])

    return out


def _evaluate_call_return(
    call_date: date,
    stance: str,
    dates: list[str],
    closes: list[float],
    as_of_date: date,
) -> dict[str, Any]:
    """Compute zero-lookahead T+5 and T+20 directional returns on trading bars."""
    call_str = call_date.isoformat()
    idx_0 = bisect_left(dates, call_str)
    if idx_0 >= len(dates):
        return {"status": "no_entry", "edge_5d": None, "edge_20d": None}

    entry_date = datetime.strptime(dates[idx_0], "%Y-%m-%d").date()
    if entry_date > as_of_date or (entry_date - call_date).days > 4:
        return {"status": "no_entry", "edge_5d": None, "edge_20d": None}

    p0 = closes[idx_0]
    if p0 <= 0:
        return {"status": "no_entry", "edge_5d": None, "edge_20d": None}

    sign = 1.0 if stance == "偏多" else (-1.0 if stance == "偏空" else 0.0)
    if sign == 0.0:
        return {"status": "neutral", "edge_5d": None, "edge_20d": None}

    edge_5d: float | None = None
    idx_5 = idx_0 + LOOKAHEAD_DAYS_5
    if idx_5 < len(dates) and dates[idx_5] <= as_of_date.isoformat():
        edge_5d = sign * ((closes[idx_5] / p0) - 1.0) * 100.0

    edge_20d: float | None = None
    idx_20 = idx_0 + LOOKAHEAD_DAYS_20
    if idx_20 < len(dates) and dates[idx_20] <= as_of_date.isoformat():
        edge_20d = sign * ((closes[idx_20] / p0) - 1.0) * 100.0

    status = "verified" if edge_20d is not None else "pending"
    return {"status": status, "edge_5d": edge_5d, "edge_20d": edge_20d}


def _consolidate_directional_calls(tweets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate same-author + same-ticker + same-stance calls within 5 calendar days."""
    raw_calls: list[dict[str, Any]] = []
    for tw in tweets:
        if tw.get("third_party_rt"):
            continue
        stance = tw.get("stance")
        if stance not in ("偏多", "偏空"):
            continue
        for sym in tw.get("tickers") or []:
            mapped = _BACKTEST_PRICE_ALIASES.get(sym, sym)
            raw_calls.append(
                {
                    "handle": tw["handle"],
                    "ticker": sym,
                    "price_key": mapped,
                    "stance": stance,
                    "date_obj": tw["date_obj"],
                    "date": tw["date"],
                }
            )

    raw_calls.sort(key=lambda c: (c["handle"], c["ticker"], c["date_obj"]))
    consolidated: list[dict[str, Any]] = []
    last_seen: dict[tuple[str, str], tuple[date, str]] = {}

    for c in raw_calls:
        key = (c["handle"], c["ticker"])
        prev = last_seen.get(key)
        if prev is not None:
            prev_date, prev_stance = prev
            if prev_stance == c["stance"] and (c["date_obj"] - prev_date).days <= CONSOLIDATION_DAYS:
                continue
        last_seen[key] = (c["date_obj"], c["stance"])
        consolidated.append(c)

    return consolidated


def _compute_reliability_metrics(
    *,
    handle: str,
    acct_info: dict[str, Any],
    category: str,
    verified_outcomes: list[dict[str, Any]],
    pending_count: int,
    all_tweets: list[dict[str, Any]],
    article_count: int,
) -> dict[str, Any]:
    """Compute Empirical Bayes reliability score, badge, and strict sample-gated stats."""
    n_verified = len(verified_outcomes)

    cred = float(acct_info.get("credibility") or 7.0)
    bg_score = max(40.0, min(90.0, cred * 9.0))
    role = str(acct_info.get("role") or "").lower()
    if role in ("macro_guru", "hedge_fund", "quant_trader", "ai_semi"):
        bg_score = min(92.0, bg_score + 4.0)
    if handle == "themarketmemo" or "争议" in str(acct_info.get("notes") or ""):
        bg_score = max(35.0, bg_score - 12.0)

    # Argument / evidence density
    num_ratio = (sum(1 for t in all_tweets if t.get("has_numbers")) / len(all_tweets)) if all_tweets else 0.0
    long_ratio = (sum(1 for t in all_tweets if t.get("is_longform")) / len(all_tweets)) if all_tweets else 0.0
    evidence_score = 45.0 + 25.0 * min(1.0, num_ratio * 1.4) + 15.0 * min(1.0, long_ratio * 2.0 + article_count * 0.15)
    evidence_score += 10.0 * min(1.0, n_verified / 12.0)
    evidence_score = max(35.0, min(95.0, evidence_score))

    win_rate_5d: float | None = None
    win_rate_20d: float | None = None
    avg_edge_5d_pct: float | None = None
    avg_edge_20d_pct: float | None = None
    profit_factor_20d: float | None = None
    best_tickers: list[str] = []

    if n_verified >= MIN_VERIFIED_SAMPLE:
        edges_20 = [float(o["edge_20d"]) for o in verified_outcomes]
        edges_5 = [float(o["edge_5d"]) for o in verified_outcomes if o["edge_5d"] is not None]
        wins_20 = sum(1 for e in edges_20 if e > 0.0)
        wins_5 = sum(1 for e in edges_5 if e > 0.0)

        win_rate_20d = round(wins_20 / n_verified * 100.0, 1)
        win_rate_5d = round(wins_5 / len(edges_5) * 100.0, 1) if edges_5 else None
        avg_edge_20d_pct = round(sum(edges_20) / n_verified, 2)
        avg_edge_5d_pct = round(sum(edges_5) / len(edges_5), 2) if edges_5 else None

        pos_sum = sum(e for e in edges_20 if e > 0.0)
        neg_sum = sum(abs(e) for e in edges_20 if e < 0.0)
        if neg_sum > 0:
            profit_factor_20d = round(min(9.99, pos_sum / neg_sum), 2)
        elif pos_sum > 0:
            profit_factor_20d = 9.99
        else:
            profit_factor_20d = 0.0

        # Empirical Bayes shrinkage toward 50% prior (m=6)
        prior_m = 6.0
        eb_win_20 = (wins_20 + prior_m * 0.50) / (n_verified + prior_m)
        eb_win_5 = (wins_5 + prior_m * 0.50) / (max(1, len(edges_5)) + prior_m)
        blended_win = 0.72 * eb_win_20 + 0.28 * eb_win_5

        edge_bonus = math.tanh(avg_edge_20d_pct / 7.0) * 16.0
        pf_bonus = max(-10.0, min(10.0, (profit_factor_20d - 1.0) * 6.5))
        perf_score = max(15.0, min(98.0, 52.0 + (blended_win - 0.50) * 145.0 + edge_bonus + pf_bonus))

        sample_trust = min(1.0, n_verified / 10.0)
        w_perf = 0.35 + 0.35 * sample_trust
        w_bg = 0.45 - 0.25 * sample_trust
        w_ev = 0.20 - 0.10 * sample_trust
        reliability_score = int(round(max(10.0, min(99.0, w_perf * perf_score + w_bg * bg_score + w_ev * evidence_score))))

        if win_rate_20d < 42.0 and avg_edge_20d_pct < 0.0:
            reliability_badge = "反向警示"
        elif (win_rate_20d >= 60.0 and avg_edge_20d_pct > 0.5 and n_verified >= 6) or (
            eb_win_20 >= 0.58 and avg_edge_20d_pct >= 2.0
        ):
            reliability_badge = "高胜率"
        elif win_rate_20d >= 52.0 and avg_edge_20d_pct >= 0.0:
            reliability_badge = "稳健参考"
        else:
            reliability_badge = "观点敏锐"

        # Best tickers ranked by directional edge & count
        by_tkr: dict[str, list[float]] = {}
        for o in verified_outcomes:
            by_tkr.setdefault(o["ticker"], []).append(float(o["edge_20d"]))
        ranked_tkrs = sorted(
            by_tkr.items(),
            key=lambda kv: (sum(1 for x in kv[1] if x > 0) / len(kv[1]), sum(kv[1]) / len(kv[1]), len(kv[1])),
            reverse=True,
        )
        best_tickers = [t for t, vals in ranked_tkrs if sum(vals) > 0][:3]
        if not best_tickers:
            best_tickers = [t for t, _ in ranked_tkrs[:3]]

        sample_note = f"20日胜率 {win_rate_20d:.1f}%（{n_verified}次核验，20日均幅 {avg_edge_20d_pct:+.2f}%）"
    else:
        # Honest small-sample gate: strictly None for win rates & edges
        raw_qual = 0.72 * bg_score + 0.28 * evidence_score
        if n_verified > 0:
            reliability_score = int(round(max(38.0, min(72.0, raw_qual))))
            reliability_badge = "样本积累"
            sample_note = f"已核验 {n_verified} 次（不足 {MIN_VERIFIED_SAMPLE} 次暂不计胜率），待验证 {pending_count} 次"
        else:
            reliability_score = int(round(max(35.0, min(70.0, raw_qual - (4.0 if not all_tweets else 0.0)))))
            reliability_badge = "定性观察"
            if pending_count > 0:
                sample_note = f"定性研究观察，近期 {pending_count} 次观点待满20交易日核验"
            else:
                sample_note = "定性研究观察，以行业框架与宏观叙事为主"

        # Most frequently discussed tickers
        freq: dict[str, int] = {}
        for t in all_tweets:
            for sym in t.get("tickers") or []:
                freq[sym] = freq.get(sym, 0) + 1
        best_tickers = [k for k, _ in sorted(freq.items(), key=lambda x: -x[1])[:3]]

    return {
        "reliability_score": reliability_score,
        "reliability_badge": reliability_badge,
        "verified_calls": n_verified,
        "pending_calls": pending_count,
        "win_rate_5d": win_rate_5d,
        "win_rate_20d": win_rate_20d,
        "avg_edge_5d_pct": avg_edge_5d_pct,
        "avg_edge_20d_pct": avg_edge_20d_pct,
        "profit_factor_20d": profit_factor_20d,
        "best_tickers": best_tickers,
        "sample_note": sample_note,
    }


def _has_cjk(text: str, *, min_chars: int = 4) -> bool:
    return len(re.findall(r"[\u4e00-\u9fff]", text)) >= min_chars


def _distill_zh_topic_phrase(text: str, category: str) -> str:
    for rx, label in _TRANSLATION_TOPICS:
        if rx.search(text):
            return label
    defaults = {
        "macro": "宏观流动性与政策走向",
        "us-stocks": "美股科技与基本面跟踪",
        "quant": "系统化因子与仓位纪律",
        "crypto": "加密资金面与结构演变",
        "investing": "长期资产配置与估值纪律",
    }
    return defaults.get(category, "市场热点与结构观察")


def _extract_key_numbers_snippet(text: str) -> str:
    nums = re.findall(r"(?:(?<![0-9])[+-]?\d+(?:\.\d+)?%|\$\d+(?:\.\d+)?[BMKTbmkt]?|\d+(?:\.\d+)?\s*(?:bps|bp|x|倍|亿|万))", text)
    uniq: list[str] = []
    for n in nums:
        c = n.strip()
        if c not in uniq:
            uniq.append(c)
        if len(uniq) >= 3:
            break
    return "、".join(uniq)


def _extract_best_core_sentence(text: str, *, max_chars: int = 46) -> str:
    """Pick the highest-information sentence or data excerpt from a tweet/article."""
    cleaned = re.sub(r"^【X长文】《?([^》\n]+)》?[:：]?", r"\1：", text.strip())
    cleaned = re.sub(r"^(?:BREAKING|UPDATE|THREAD)[:：]\s*", "", cleaned, flags=re.IGNORECASE)
    chunks = [c.strip(" \t\r\n-•|") for c in re.split(r"(?<=[。！？.!?])\s+|\n+", cleaned) if c.strip()]
    if not chunks:
        return cleaned[:max_chars]

    def _score_chunk(s: str) -> tuple[int, int]:
        if len(s) < 12:
            return (-10, len(s))
        sc = 0
        if re.search(r"\d+(?:\.\d+)?%|\$\d+|\d+[BMKbmk]\b|\d+亿|\d+万", s):
            sc += 5
        if extract_tickers(s):
            sc += 4
        if not re.match(r"^(?:here'?s|this is|a quick|thread|look at|sometimes)\b", s, re.IGNORECASE):
            sc += 2
        if 18 <= len(s) <= 140:
            sc += 2
        return (sc, -abs(len(s) - 48))

    best = max(chunks[:4], key=_score_chunk)
    if len(best) > max_chars:
        cut = best[: max_chars - 1].rstrip(" ,;，；：")
        if " " in cut and not _has_cjk(cut, min_chars=2) and len(cut) > 20:
            cut = cut.rsplit(" ", 1)[0]
        return cut + "…"
    return best


def distill_chinese_view(
    *,
    clean_text: str,
    stance: str,
    tickers: list[str],
    category: str,
    name: str,
    reliability_badge: str,
    win_rate_20d: float | None,
    verified_calls: int,
    is_longform: bool,
) -> tuple[str, str, str]:
    """Convert English or mixed tweets into concrete headline, takeaway, and why_usable."""
    verb = "看多" if stance == "偏多" else ("看空" if stance == "偏空" else "跟踪")
    tkr_label = "/".join(tickers[:2]) if tickers else _CATEGORY_LABEL_MAP.get(category, "市场")
    # Detect Chinese vs English BEFORE scrubbing English forbidden terms into Chinese placeholders
    is_zh_source = _has_cjk(clean_text, min_chars=6)
    raw_clean = scrub_forbidden(clean_text)

    if is_zh_source:
        core_zh = _extract_best_core_sentence(raw_clean, max_chars=48)
        prefix = f"[{verb} {tkr_label}]"
        headline = f"{prefix} {core_zh}"
        if len(headline) > 65:
            headline = headline[:64].rstrip(" ,;，；：") + "…"
        takeaway = raw_clean[:220].rstrip() + ("..." if len(raw_clean) > 220 else "")
    else:
        core_en = _extract_best_core_sentence(raw_clean, max_chars=50)
        prefix = f"[{verb} {tkr_label}]"
        headline = f"{prefix} {core_en}"
        if len(headline) > 65:
            headline = headline[:64].rstrip(" ,;，；：") + "…"

        # Topic summary in plain Chinese without template fluff
        topic_phrase = _distill_zh_topic_phrase(raw_clean, category)
        if AI_TOKEN_CONTEXT_RE.search(raw_clean) and not CRYPTO_EXPLICIT_RE.search(raw_clean):
            topic_phrase = "AI模型架构与安全研究"
        num_hint = _extract_key_numbers_snippet(raw_clean)
        quote_excerpt = raw_clean[:160].rstrip() + ("..." if len(raw_clean) > 160 else "")
        num_part = f"（关键数据：{num_hint}）" if num_hint else ""
        action_phrase = (
            "给出偏多依据" if stance == "偏多" else ("提示下行与风控风险" if stance == "偏空" else "聚焦实证细节")
        )
        takeaway = f"{name}围绕{'/'.join(tickers[:3]) or _CATEGORY_LABEL_MAP.get(category, '市场')}聚焦{topic_phrase}{num_part}，{action_phrase}。｜原话要点：\"{quote_excerpt}\""

    # Construct 1-sentence actionable trading reference
    target_str = "/".join(tickers[:2]) if tickers else _CATEGORY_LABEL_MAP.get(category, "相关资产")
    if win_rate_20d is not None and verified_calls >= MIN_VERIFIED_SAMPLE:
        if reliability_badge == "反向警示":
            why_usable = f"该博主历史20日顺向胜率仅 {win_rate_20d:.1f}%（{verified_calls}次核验），当前对 {target_str} 的{stance}观点宜作反向情绪风控参考。"
        elif stance == "偏多":
            why_usable = f"该博主历史20日胜率 {win_rate_20d:.1f}%（{verified_calls}次核验），可结合 {target_str} 均线趋势与量能确认顺势跟踪。"
        elif stance == "偏空":
            why_usable = f"该博主历史20日胜率 {win_rate_20d:.1f}%（{verified_calls}次核验），对 {target_str} 的谨慎提示可用于收紧追高与多头仓位。"
        else:
            why_usable = f"该博主历史20日胜率 {win_rate_20d:.1f}%（{verified_calls}次核验），可作为 {target_str} 基本面与资金面跟踪锚点。"
    else:
        depth_hint = "长文" if is_longform else "观点"
        why_usable = f"历史已核验样本尚少（{verified_calls}次），建议将该{depth_hint}作为 {target_str} 产业链交叉验证线索，不单独作为买卖依据。"

    return (scrub_forbidden(headline), scrub_forbidden(takeaway), scrub_forbidden(why_usable))


def _scan_corpus(
    *,
    kol_dir: Path,
    as_of_ts: float,
) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]], dict[str, int], dict[str, Any]]:
    """Scan daily KOL notes and articles with strict mtime <= as_of_ts gate."""
    as_of_date = datetime.fromtimestamp(as_of_ts, tz=BJT).date()
    cfg = _read_json(kol_dir / "config.json", {}) or {}
    acct_map: dict[str, dict[str, Any]] = {}
    for a in cfg.get("accounts") or []:
        if isinstance(a, dict) and a.get("handle"):
            acct_map[str(a["handle"]).strip().lower()] = a

    tweets_by_handle: dict[str, list[dict[str, Any]]] = {}
    inferred_sub_by_handle: dict[str, str] = {}
    article_counts: dict[str, int] = {}
    latest_article_by_handle: dict[str, tuple[str, str]] = {}
    stats = {"scanned_files": 0, "excluded_future_files": 0, "parsed_tweets": 0}

    for sub in ("macro", "us-stocks", "crypto", "quant", "investing"):
        sd = kol_dir / sub
        if not sd.exists():
            continue
        for p in sorted(sd.glob("*.md")):
            try:
                st = p.stat()
            except OSError:
                continue
            if st.st_mtime > as_of_ts:
                stats["excluded_future_files"] += 1
                continue
            handle, tws = _parse_daily_file(p, sub, as_of_date=as_of_date)
            if not handle:
                continue
            stats["scanned_files"] += 1
            inferred_sub_by_handle.setdefault(handle, sub)
            if tws:
                tweets_by_handle.setdefault(handle, []).extend(tws)
                stats["parsed_tweets"] += len(tws)

    articles_dir = kol_dir / "articles"
    if articles_dir.exists():
        for p in sorted(articles_dir.glob("*.md"), reverse=True):
            try:
                st = p.stat()
            except OSError:
                continue
            if st.st_mtime > as_of_ts:
                stats["excluded_future_files"] += 1
                continue
            m = re.match(r"^([A-Za-z0-9_.-]+)_(\d{4}-\d{2}-\d{2})_", p.name)
            if not m:
                continue
            h_low = m.group(1).lower()
            if h_low in MEDIA_WIRE_HANDLES:
                continue
            article_counts[h_low] = article_counts.get(h_low, 0) + 1
            if h_low not in latest_article_by_handle:
                url = ""
                try:
                    head = p.read_text(encoding="utf-8", errors="replace")[:1500]
                    mu = re.search(r"https?://(?:x|twitter)\.com/[A-Za-z0-9_]+/status/\d+", head)
                    if mu:
                        url = mu.group(0)
                except OSError:
                    pass
                latest_article_by_handle[h_low] = (p.name, url)

    meta_ctx = {
        "inferred_sub": inferred_sub_by_handle,
        "latest_article": latest_article_by_handle,
        "stats": stats,
    }
    return (acct_map, tweets_by_handle, article_counts, meta_ctx)


def build_kol_scorecards(
    *,
    kol_dir: Path | str | None = None,
    stock_profiles_path: Path | str | None = None,
    fred_cache_path: Path | str | None = None,
    price_series: dict[str, tuple[list[str], list[float]]] | None = None,
    cache_path: Path | str | None = DEFAULT_SCORECARD_CACHE_PATH,
    as_of_ts: float | None = None,
    force_refresh: bool = True,
) -> list[dict[str, Any]]:
    """Run zero-lookahead historical backtest across all KOLs and rank scorecards."""
    root = Path(kol_dir) if kol_dir is not None else DEFAULT_KOL_DIR
    dec_ts = float(as_of_ts if as_of_ts is not None else datetime.now(UTC).timestamp())
    as_of_date = datetime.fromtimestamp(dec_ts, tz=BJT).date()

    resolved_cache = Path(cache_path) if cache_path is not None else None
    if not force_refresh and resolved_cache and resolved_cache.exists():
        cached = _read_json(resolved_cache, {})
        if isinstance(cached, dict) and isinstance(cached.get("scorecards"), list) and cached.get("scorecards"):
            return sanitize_output(cached["scorecards"])

    prices = load_price_series(
        stock_profiles_path=Path(stock_profiles_path) if stock_profiles_path else None,
        fred_cache_path=Path(fred_cache_path) if fred_cache_path else None,
        price_series=price_series,
    )

    acct_map, tweets_by_handle, article_counts, meta_ctx = _scan_corpus(kol_dir=root, as_of_ts=dec_ts)
    inferred_sub = meta_ctx["inferred_sub"]

    all_handles = set(acct_map.keys()) | set(tweets_by_handle.keys())
    scorecards: list[dict[str, Any]] = []
    total_consolidated_calls = 0
    total_verified_calls = 0

    for h_low in sorted(all_handles):
        acct_info = acct_map.get(h_low, {})
        if _is_media_wire(h_low, acct_info, allow_official_ai=False):
            continue

        tws = sorted(tweets_by_handle.get(h_low, []), key=lambda x: (x["date"], x["time_bjt"]))
        subdir = inferred_sub.get(h_low, "")
        if not tws and h_low not in acct_map:
            continue
        if not tws and str(acct_info.get("type") or "").lower() != "kol":
            continue

        canonical_handle = str(acct_info.get("handle") or h_low).strip()
        name = str(acct_info.get("name") or canonical_handle).strip()
        category = _infer_category(h_low, subdir, acct_info)
        category_label = _CATEGORY_LABEL_MAP.get(category, "美股与市场")
        role_label = _infer_role_label(category, acct_info)
        focus = scrub_forbidden(str(acct_info.get("focus") or category_label))

        calls = _consolidate_directional_calls(tws)
        total_consolidated_calls += len(calls)

        verified_outcomes: list[dict[str, Any]] = []
        pending_count = 0
        for c in calls:
            pkey = c["price_key"]
            if pkey not in prices:
                continue
            ds, cs = prices[pkey]
            ev = _evaluate_call_return(c["date_obj"], c["stance"], ds, cs, as_of_date)
            if ev["status"] == "verified":
                verified_outcomes.append(
                    {
                        "ticker": c["ticker"],
                        "date": c["date"],
                        "stance": c["stance"],
                        "edge_5d": ev["edge_5d"],
                        "edge_20d": ev["edge_20d"],
                    }
                )
            elif ev["status"] == "pending":
                pending_count += 1

        total_verified_calls += len(verified_outcomes)

        metrics = _compute_reliability_metrics(
            handle=h_low,
            acct_info=acct_info,
            category=category,
            verified_outcomes=verified_outcomes,
            pending_count=pending_count,
            all_tweets=tws,
            article_count=article_counts.get(h_low, 0),
        )

        # Latest view summary
        latest_tw = max(tws, key=lambda x: (x["date"], not x["third_party_rt"], x["likes"], len(x["clean_text"]))) if tws else None
        if latest_tw:
            latest_stance = latest_tw["stance"]
            latest_date = latest_tw["date"]
            latest_headline, _, _ = distill_chinese_view(
                clean_text=latest_tw["clean_text"],
                stance=latest_stance,
                tickers=latest_tw["tickers"],
                category=category,
                name=name,
                reliability_badge=metrics["reliability_badge"],
                win_rate_20d=metrics["win_rate_20d"],
                verified_calls=metrics["verified_calls"],
                is_longform=latest_tw["is_longform"],
            )
        else:
            latest_stance = "中性"
            latest_date = ""
            latest_headline = f"{name} · {focus}"

        scorecards.append(
            {
                "handle": canonical_handle,
                "name": name,
                "role_label": role_label,
                "category": category,
                "category_label": category_label,
                "focus": focus,
                "reliability_score": metrics["reliability_score"],
                "reliability_badge": metrics["reliability_badge"],
                "verified_calls": metrics["verified_calls"],
                "pending_calls": metrics["pending_calls"],
                "win_rate_5d": metrics["win_rate_5d"],
                "win_rate_20d": metrics["win_rate_20d"],
                "avg_edge_5d_pct": metrics["avg_edge_5d_pct"],
                "avg_edge_20d_pct": metrics["avg_edge_20d_pct"],
                "profit_factor_20d": metrics["profit_factor_20d"],
                "best_tickers": metrics["best_tickers"],
                "latest_stance": latest_stance,
                "latest_date": latest_date,
                "latest_headline": latest_headline,
                "sample_note": metrics["sample_note"],
            }
        )

    scorecards.sort(
        key=lambda x: (
            float(x["reliability_score"]),
            int(x["verified_calls"]),
            float(x["win_rate_20d"] or 0.0),
            float(x["avg_edge_20d_pct"] or 0.0),
        ),
        reverse=True,
    )

    cleaned = sanitize_output(scorecards)
    if resolved_cache is not None:
        try:
            atomic_write_json(
                resolved_cache,
                {
                    "updated_at_bjt": datetime.now(BJT).strftime("%Y-%m-%d %H:%M:%S+08:00"),
                    "as_of_date": as_of_date.isoformat(),
                    "total_kols": len(cleaned),
                    "verified_kols": sum(1 for s in cleaned if s["verified_calls"] >= MIN_VERIFIED_SAMPLE),
                    "total_consolidated_calls": total_consolidated_calls,
                    "total_verified_calls": total_verified_calls,
                    "scorecards": cleaned,
                },
            )
        except Exception:
            pass

    return cleaned


def kol_scorecards(
    *,
    kol_dir: Path | str | None = None,
    stock_profiles_path: Path | str | None = None,
    fred_cache_path: Path | str | None = None,
    price_series: dict[str, tuple[list[str], list[float]]] | None = None,
    cache_path: Path | str | None = DEFAULT_SCORECARD_CACHE_PATH,
    as_of_ts: float | None = None,
    force_refresh: bool = True,
) -> list[dict[str, Any]]:
    """Alias entry point returning ranked KOL scorecards."""
    return build_kol_scorecards(
        kol_dir=kol_dir,
        stock_profiles_path=stock_profiles_path,
        fred_cache_path=fred_cache_path,
        price_series=price_series,
        cache_path=cache_path,
        as_of_ts=as_of_ts,
        force_refresh=force_refresh,
    )


def build_kol_digest(
    *,
    kol_dir: Path | str | None = None,
    scorecards: list[dict[str, Any]] | None = None,
    stock_profiles_path: Path | str | None = None,
    fred_cache_path: Path | str | None = None,
    price_series: dict[str, tuple[list[str], list[float]]] | None = None,
    cache_path: Path | str | None = DEFAULT_SCORECARD_CACHE_PATH,
    lookback_days: int = 5,
    as_of_ts: float | None = None,
    limit: int = 36,
) -> list[dict[str, Any]]:
    """Build balanced readable Chinese KOL digest (up to 36 items across 4 tracks)."""
    root = Path(kol_dir) if kol_dir is not None else DEFAULT_KOL_DIR
    dec_ts = float(as_of_ts if as_of_ts is not None else datetime.now(UTC).timestamp())
    as_of_date = datetime.fromtimestamp(dec_ts, tz=BJT).date()
    min_date = as_of_date - timedelta(days=max(1, lookback_days) - 1)

    if scorecards is None:
        scorecards = build_kol_scorecards(
            kol_dir=root,
            stock_profiles_path=stock_profiles_path,
            fred_cache_path=fred_cache_path,
            price_series=price_series,
            cache_path=cache_path,
            as_of_ts=dec_ts,
            force_refresh=True,
        )
    sc_by_handle = {str(s["handle"]).lower(): s for s in scorecards}

    acct_map, tweets_by_handle, _, meta_ctx = _scan_corpus(kol_dir=root, as_of_ts=dec_ts)
    inferred_sub = meta_ctx["inferred_sub"]
    latest_article = meta_ctx["latest_article"]

    candidates_by_group: dict[str, list[dict[str, Any]]] = {
        "宏观与风控": [],
        "AI与美股": [],
        "量化与配置": [],
        "加密与链上": [],
    }

    for h_low, tws in tweets_by_handle.items():
        acct_info = acct_map.get(h_low, {})
        if _is_media_wire(h_low, acct_info, allow_official_ai=False):
            continue
        recent = [t for t in tws if min_date <= t["date_obj"] <= as_of_date]
        if not recent:
            continue

        # Prefer original / directional / ticker-bearing / longform tweets over third-party RTs
        recent.sort(
            key=lambda t: (
                not t["third_party_rt"],
                len(t["tickers"]) > 0,
                t["stance"] != "中性",
                t["is_longform"],
                t["has_numbers"],
                t["date"],
                t["likes"] + t["retweets"] * 2,
                len(t["clean_text"]),
            ),
            reverse=True,
        )
        best_tw = recent[0]

        sc = sc_by_handle.get(h_low, {})
        category = sc.get("category") or _infer_category(h_low, inferred_sub.get(h_low, ""), acct_info)
        group_label = _GROUP_LABEL_MAP.get(category, "AI与美股")
        name = str(sc.get("name") or acct_info.get("name") or h_low)
        canonical_handle = str(sc.get("handle") or acct_info.get("handle") or h_low)
        role_label = str(sc.get("role_label") or _infer_role_label(category, acct_info))
        rel_score = int(sc.get("reliability_score") or 62)
        rel_badge = str(sc.get("reliability_badge") or "定性观察")
        verified_calls = int(sc.get("verified_calls") or 0)
        win_5d = sc.get("win_rate_5d")
        win_20d = sc.get("win_rate_20d")
        edge_20d = sc.get("avg_edge_20d_pct")
        best_tickers = list(sc.get("best_tickers") or [])

        # Aggregate directional stance across author's recent window if consistent, anchored on best_tw
        bull_c = sum(1 for t in recent if not t["third_party_rt"] and t["stance"] == "偏多")
        bear_c = sum(1 for t in recent if not t["third_party_rt"] and t["stance"] == "偏空")
        stance = best_tw["stance"]
        score_val = float(best_tw["conviction"])
        if stance == "中性" and (bull_c != bear_c):
            stance = "偏多" if bull_c > bear_c else "偏空"
            score_val = round((bull_c - bear_c) / (bull_c + bear_c + 1.0), 3)

        headline, takeaway, why_usable = distill_chinese_view(
            clean_text=best_tw["clean_text"],
            stance=stance,
            tickers=best_tw["tickers"],
            category=category,
            name=name,
            reliability_badge=rel_badge,
            win_rate_20d=win_20d,
            verified_calls=verified_calls,
            is_longform=best_tw["is_longform"],
        )

        art_file, art_url = latest_article.get(h_low, ("", ""))
        url = best_tw["url"] or art_url or f"https://x.com/{canonical_handle}"
        topic = classify_kol_topic(headline, takeaway, category)

        card = {
            # Legacy keys
            "handle": canonical_handle,
            "group": group_label,
            "topic": topic,
            "stance": stance,
            "score": score_val,
            "date": best_tw["date"],
            "headline": headline,
            "takeaway": takeaway,
            "article_file": art_file,
            "url": url,
            # New intelligence keys
            "name": name,
            "role_label": role_label,
            "tickers": best_tw["tickers"],
            "why_usable": why_usable,
            "is_longform": bool(best_tw["is_longform"] or art_file),
            "reliability_score": rel_score,
            "reliability_badge": rel_badge,
            "verified_calls": verified_calls,
            "win_rate_5d": win_5d,
            "win_rate_20d": win_20d,
            "avg_edge_20d_pct": edge_20d,
            "best_tickers": best_tickers,
            "_rank": (rel_score + (8 if best_tw["tickers"] else 0) + (5 if stance != "中性" else 0) + (4 if best_tw["is_longform"] else 0)),
        }
        candidates_by_group.setdefault(group_label, []).append(card)

    for grp in candidates_by_group:
        candidates_by_group[grp].sort(key=lambda c: (c["_rank"], c["date"], abs(c["score"])), reverse=True)

    # Balanced round-robin quota across all 4 tracks up to `limit` (36)
    quotas = {
        "宏观与风控": 9,
        "AI与美股": 13,
        "量化与配置": 6,
        "加密与链上": 8,
    }
    selected: list[dict[str, Any]] = []
    used_handles: set[str] = set()

    for grp, quota in quotas.items():
        for c in candidates_by_group.get(grp, [])[:quota]:
            h_k = c["handle"].lower()
            if h_k not in used_handles and len(selected) < limit:
                used_handles.add(h_k)
                selected.append(c)

    if len(selected) < limit:
        leftover = [
            c
            for grp_list in candidates_by_group.values()
            for c in grp_list
            if c["handle"].lower() not in used_handles
        ]
        leftover.sort(key=lambda c: (c["_rank"], c["date"]), reverse=True)
        for c in leftover:
            if len(selected) >= limit:
                break
            used_handles.add(c["handle"].lower())
            selected.append(c)

    selected.sort(key=lambda c: (c["reliability_score"], c["date"], abs(c["score"])), reverse=True)
    for c in selected:
        c.pop("_rank", None)

    return sanitize_output(selected)


def kol_digest(
    *,
    kol_dir: Path | str | None = None,
    scorecards: list[dict[str, Any]] | None = None,
    lookback_days: int = 5,
    as_of_ts: float | None = None,
    limit: int = 36,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    """Alias entry point returning balanced KOL digest cards."""
    return build_kol_digest(
        kol_dir=kol_dir,
        scorecards=scorecards,
        lookback_days=lookback_days,
        as_of_ts=as_of_ts,
        limit=limit,
        **kwargs,
    )


def _map_signal_category(card: dict[str, Any]) -> str:
    group = str(card.get("group") or "")
    topic = str(card.get("topic") or "")
    tickers = set(card.get("tickers") or [])
    txt = f"{card.get('headline', '')} {card.get('takeaway', '')}".lower()

    if group == "加密与链上" or bool(tickers & {"BTC", "ETH", "SOL", "COIN", "MSTR"}):
        return "crypto"
    if any(k in txt for k in ("a股", "沪指", "科创板", "港股", "恒指", "baba", "阿里", "中芯")):
        return "ashare"
    if "AI" in topic or bool(tickers & {"NVDA", "TSM", "AMD", "AVGO", "MU", "SMCI", "ARM"}) or any(
        k in txt for k in ("openai", "anthropic", "claude", "算力", "芯片", "半导体", "blackwell", "hbm")
    ):
        return "ai"
    if group == "宏观与风控" or bool(tickers & {"GOLD", "WTI", "DOLLAR", "SP500"}) or any(
        k in txt for k in ("美联储", "fed", "fomc", "利率", "通胀", "cpi", "pce", "美债", "财政")
    ):
        return "macro"
    return "equities"


def build_kol_actionable_signals(
    *,
    kol_digest_items: list[dict[str, Any]] | None = None,
    scorecards: list[dict[str, Any]] | None = None,
    kol_dir: Path | str | None = None,
    min_reliability_score: float = 68.0,
    lookback_days: int = 5,
    as_of_ts: float | None = None,
    limit: int = 16,
) -> list[dict[str, Any]]:
    """Convert high-reliability directional KOL views into standard cockpit signals."""
    if kol_digest_items is None:
        kol_digest_items = build_kol_digest(
            kol_dir=kol_dir,
            scorecards=scorecards,
            lookback_days=lookback_days,
            as_of_ts=as_of_ts,
        )

    sc_map = {str(s["handle"]).lower(): s for s in (scorecards or [])}
    signals: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for idx, item in enumerate(kol_digest_items):
        rel_score = float(item.get("reliability_score") or sc_map.get(str(item.get("handle", "")).lower(), {}).get("reliability_score") or 0.0)
        if rel_score < min_reliability_score:
            continue

        stance = str(item.get("stance") or "中性")
        if stance not in ("偏多", "偏空"):
            continue
        tickers = list(item.get("tickers") or [])
        cat = _map_signal_category(item)
        if not tickers and cat not in ("macro", "crypto", "ai"):
            continue

        direction = "bullish" if stance == "偏多" else "bearish"
        is_longform = bool(item.get("is_longform") or item.get("article_file"))
        source = "X深度长文" if is_longform else "X核心观点"
        verified_calls = int(item.get("verified_calls") or 0)
        win_20d = item.get("win_rate_20d")
        importance = "高" if (rel_score >= 78.0 or (verified_calls >= MIN_VERIFIED_SAMPLE and (win_20d or 0.0) >= 60.0) or is_longform) else "中"

        handle = str(item.get("handle") or "")
        date_str = str(item.get("date") or datetime.now(BJT).strftime("%Y-%m-%d"))
        sig_id = f"kol-{handle.lower()}-{date_str}-{idx}"
        if sig_id in seen_ids:
            continue
        seen_ids.add(sig_id)

        badge = str(item.get("reliability_badge") or "")
        name = str(item.get("name") or handle)
        win_tag = f"20日胜率 {win_20d:.1f}%" if win_20d is not None else badge
        summary_txt = f"@{handle}（{name} · {win_tag}）：{item.get('takeaway') or item.get('headline')}"
        if len(summary_txt) > 220:
            summary_txt = summary_txt[:217].rstrip() + "..."

        signals.append(
            {
                "id": sig_id,
                "source": source,
                "category": cat,
                "direction": direction,
                "importance": importance,
                "time_bjt": f"{date_str} 09:00",
                "title": str(item.get("headline") or f"{name} 观点更新"),
                "summary": summary_txt,
                "why_it_matters": str(item.get("why_usable") or "高可靠度观察信源，建议结合盘面均线验证。"),
                "tickers": tickers,
                "url": str(item.get("url") or f"https://x.com/{handle}"),
                "topics": [str(item.get("topic") or "市场观点")],
            }
        )
        if len(signals) >= limit:
            break

    return sanitize_output(signals)


def kol_actionable_signals(
    *,
    kol_digest_items: list[dict[str, Any]] | None = None,
    scorecards: list[dict[str, Any]] | None = None,
    kol_dir: Path | str | None = None,
    min_reliability_score: float = 68.0,
    lookback_days: int = 5,
    as_of_ts: float | None = None,
    limit: int = 16,
) -> list[dict[str, Any]]:
    """Alias entry point converting high-reliability KOL digest views into signals."""
    return build_kol_actionable_signals(
        kol_digest_items=kol_digest_items,
        scorecards=scorecards,
        kol_dir=kol_dir,
        min_reliability_score=min_reliability_score,
        lookback_days=lookback_days,
        as_of_ts=as_of_ts,
        limit=limit,
    )


def select_quality_kol_articles(
    articles_dir: Path | str | None = None,
    *,
    kol_dir: Path | str | None = None,
    scorecards: list[dict[str, Any]] | None = None,
    as_of_ts: float | None = None,
    limit: int = 40,
) -> list[dict[str, Any]]:
    """Filter out media wire spam and keep high-quality KOL deep articles with Chinese summaries."""
    root = Path(kol_dir) if kol_dir is not None else DEFAULT_KOL_DIR
    art_root = Path(articles_dir) if articles_dir is not None else (root / "articles")
    if not art_root.exists():
        return []

    cfg = _read_json(root / "config.json", {}) or {}
    acct_map = {
        str(a["handle"]).strip().lower(): a
        for a in (cfg.get("accounts") or [])
        if isinstance(a, dict) and a.get("handle")
    }
    sc_map = {str(s["handle"]).lower(): s for s in (scorecards or [])}

    articles: list[dict[str, Any]] = []
    seen_hashes: set[str] = set()

    files = sorted(art_root.glob("*.md"), key=lambda p: p.stat().st_mtime if p.exists() else 0.0, reverse=True)
    for p in files:
        if not safe_article_filename(p.name):
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        if as_of_ts is not None and st.st_mtime > float(as_of_ts):
            continue

        m = re.match(r"^([A-Za-z0-9_.-]+)_(\d{4}-\d{2}-\d{2})_", p.name)
        handle = m.group(1) if m else p.stem.split("_")[0]
        h_low = handle.lower()
        acct_info = acct_map.get(h_low, {})

        if _is_media_wire(h_low, acct_info, allow_official_ai=True):
            continue

        try:
            raw = p.read_text(encoding="utf-8-sig", errors="replace").replace("\r\n", "\n")
        except OSError:
            continue

        if has_private_article_content({"filename": p.name, "content": raw}):
            continue

        # Strip markdown metadata header to measure true substance length
        body_parts = raw.split("\n---\n", 1)
        body_content = body_parts[1].strip() if len(body_parts) == 2 else raw.strip()
        body_clean = _strip_tweet_noise(body_content)
        if len(body_clean) < 180 and h_low not in OFFICIAL_AI_RESEARCH_HANDLES:
            continue

        tickers = extract_tickers(body_content)
        is_official_ai = h_low in OFFICIAL_AI_RESEARCH_HANDLES
        if not is_official_ai and not _is_market_relevant(body_clean, tickers):
            continue
        if is_official_ai and len(body_clean) < 120:
            continue

        # Dedup by content prefix hash
        chash = hashlib.sha1(body_clean[:240].encode("utf-8", errors="ignore")).hexdigest()[:12]
        if chash in seen_hashes:
            continue
        seen_hashes.add(chash)

        url = ""
        mu = re.search(r"https?://(?:x|twitter)\.com/[A-Za-z0-9_]+/status/\d+", raw[:1500])
        if mu:
            url = mu.group(0)

        sc = sc_map.get(h_low, {})
        name = str(sc.get("name") or acct_info.get("name") or handle)
        category = "us-stocks" if is_official_ai else _infer_category(h_low, "", acct_info)
        role_label = "AI前沿实验室" if is_official_ai else str(sc.get("role_label") or _infer_role_label(category, acct_info))
        stance, _ = _detect_stance(body_clean)
        rel_score = int(sc.get("reliability_score") or max(60, int((acct_info.get("credibility") or 8) * 9)))
        rel_badge = str(sc.get("reliability_badge") or "定性观察")

        _, zh_takeaway, _ = distill_chinese_view(
            clean_text=body_clean,
            stance=stance,
            tickers=tickers,
            category=category,
            name=name,
            reliability_badge=rel_badge,
            win_rate_20d=sc.get("win_rate_20d"),
            verified_calls=int(sc.get("verified_calls") or 0),
            is_longform=True,
        )

        # Preserve real H1 title or authentic first-line/first-sentence article lead
        m_h1 = re.search(r"^#\s+(.+)$", body_content, re.MULTILINE)
        if m_h1 and "X 长文" not in m_h1.group(1):
            art_title = scrub_forbidden(m_h1.group(1).strip())
            if len(art_title) > 68:
                art_title = art_title[:67].rstrip(" ,;，；：") + "…"
        else:
            first_para = next((ln.strip() for ln in body_content.splitlines() if ln.strip() and not ln.strip().startswith(("#", "-", "*"))), "")
            first_para = _strip_tweet_noise(first_para) if first_para else body_clean
            first_sent = re.split(r"(?<=[。！？.!?])\s+", first_para)[0].strip() if first_para else ""
            lead = first_sent if len(first_sent) >= 16 else _extract_best_core_sentence(body_clean, max_chars=54)
            lead = re.sub(r"^New\s+Anthropic(?:\s+Fellows)?\s+research[:：]\s*", "", lead, flags=re.IGNORECASE)
            if len(lead) > 54:
                cut = lead[:53].rstrip(" ,;，；：")
                if " " in cut and not _has_cjk(cut, min_chars=2) and len(cut) > 20:
                    cut = cut.rsplit(" ", 1)[0]
                lead = cut + "…"
            if _has_cjk(lead, min_chars=6):
                art_title = lead
            else:
                art_title = f"@{handle} · {lead}"
                if len(art_title) > 68:
                    art_title = art_title[:67].rstrip(" ,;，；：") + "…"
        art_title = scrub_forbidden(art_title)

        mtime_str = datetime.fromtimestamp(st.st_mtime, tz=BJT).strftime("%Y-%m-%d %H:%M")
        excerpt = zh_takeaway[:180].rstrip() + ("..." if len(zh_takeaway) > 180 else "")

        articles.append(
            {
                "filename": p.name,
                "title": art_title,
                "source": "KOL深度长文",
                "publisher": name,
                "handle": str(sc.get("handle") or acct_info.get("handle") or handle),
                "author_role": role_label,
                "tickers": tickers,
                "mtime": mtime_str,
                "excerpt": excerpt,
                "body_md": scrub_forbidden(raw, preserve_newlines=True),
                "url": url or f"https://x.com/{handle}",
                "score": rel_score,
            }
        )

    articles.sort(key=lambda a: (int(a.get("score") or 0), str(a.get("mtime") or "")), reverse=True)
    return sanitize_output(articles[:limit])


def generate_kol_intel(
    *,
    kol_dir: Path | str | None = None,
    stock_profiles_path: Path | str | None = None,
    fred_cache_path: Path | str | None = None,
    cache_path: Path | str | None = DEFAULT_SCORECARD_CACHE_PATH,
    lookback_days: int = 5,
    as_of_ts: float | None = None,
    digest_limit: int = 36,
    signal_limit: int = 16,
    article_limit: int = 40,
) -> dict[str, Any]:
    """Run full KOL evaluation, digest distillation, signal generation, and article filtering."""
    root = Path(kol_dir) if kol_dir is not None else DEFAULT_KOL_DIR
    dec_ts = float(as_of_ts if as_of_ts is not None else datetime.now(UTC).timestamp())

    cards = build_kol_scorecards(
        kol_dir=root,
        stock_profiles_path=stock_profiles_path,
        fred_cache_path=fred_cache_path,
        cache_path=cache_path,
        as_of_ts=dec_ts,
        force_refresh=True,
    )
    digest = build_kol_digest(
        kol_dir=root,
        scorecards=cards,
        lookback_days=lookback_days,
        as_of_ts=dec_ts,
        limit=digest_limit,
    )
    signals = build_kol_actionable_signals(
        kol_digest_items=digest,
        scorecards=cards,
        min_reliability_score=68.0,
        limit=signal_limit,
    )
    articles = select_quality_kol_articles(
        root / "articles",
        kol_dir=root,
        scorecards=cards,
        as_of_ts=dec_ts,
        limit=article_limit,
    )
    return sanitize_output(
        {
            "kol_scorecards": cards,
            "kol_digest": digest,
            "kol_actionable_signals": signals,
            "quality_articles": articles,
            "stats": {
                "total_kols": len(cards),
                "verified_kols": sum(1 for s in cards if s["verified_calls"] >= MIN_VERIFIED_SAMPLE),
                "total_verified_calls": sum(int(s["verified_calls"]) for s in cards),
                "digest_count": len(digest),
                "signals_count": len(signals),
                "articles_count": len(articles),
            },
        }
    )


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    bundle = generate_kol_intel()
    print(json.dumps(bundle["stats"], ensure_ascii=False, indent=2))
    print("\nTop 10 Scorecards:")
    for row in bundle["kol_scorecards"][:10]:
        print(
            f"  @{row['handle']:18s} | {row['reliability_score']:2d} | {row['reliability_badge']:4s} "
            f"| N={row['verified_calls']:2d} | 5d={row['win_rate_5d']} | 20d={row['win_rate_20d']} | {row['best_tickers']}"
        )
