"""Shared text, symbol and topic rules for market signals and articles."""
from __future__ import annotations

import email.utils
import html
import re
import unicodedata
import urllib.parse
from datetime import datetime
from typing import Any, Literal

from backend.guardrails import scrub_forbidden, scrub_quant_thesis, strip_emojis
from backend.runtime import UTC


def strip_xml_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower() if "}" in tag else tag.lower()


def decode_http_text(resp: Any) -> str:
    raw_bytes = getattr(resp, "content", b"") or b""
    if not raw_bytes:
        return str(getattr(resp, "text", "") or "")
    enc = str(getattr(resp, "encoding", "") or "").strip()
    if enc and enc.lower() not in ("iso-8859-1", "latin-1", "latin1"):
        try:
            return raw_bytes.decode(enc, errors="replace")
        except LookupError:
            pass
    return raw_bytes.decode("utf-8", errors="replace")


def strip_html(raw: str) -> str:
    text = re.sub(r"<[^>]+>", " ", raw or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def clean_feed_text(raw: Any, *, max_len: int = 240) -> str:
    txt = strip_html(str(raw or ""))
    txt = scrub_forbidden(txt)
    txt = strip_emojis(txt)
    txt = re.sub(r"\s+", " ", txt).strip()
    if max_len > 0 and len(txt) > max_len:
        txt = txt[: max_len - 3].rstrip() + "..."
    return txt


def html_to_clean_markdown(raw_html: str, *, max_chars: int = 16000) -> str:
    """Convert feed/article HTML into clean, safe Markdown without XSS or forbidden jargon."""
    s = str(raw_html or "").strip()
    if not s:
        return ""
    s = re.sub(r"<(script|style|noscript|svg|iframe|object|embed)\b[^>]*>.*?</\1>", " ", s, flags=re.IGNORECASE | re.DOTALL)

    def _replace_anchor(match: re.Match[str]) -> str:
        href = html.unescape(match.group(1)).strip()
        label = re.sub(r"<[^>]+>", " ", match.group(2))
        label = re.sub(r"\s+", " ", html.unescape(label)).strip()
        if not label:
            label = href
        if href.startswith(("http://", "https://")):
            safe_url = href.replace("(", "%28").replace(")", "%29").replace(" ", "%20")
            safe_lbl = label.replace("[", "\\[").replace("]", "\\]")
            return f"[{safe_lbl}]({safe_url})"
        return label

    s = re.sub(
        r"<a\b[^>]*\bhref\s*=\s*[\"']([^\"']+)[\"'][^>]*>(.*?)</a>",
        _replace_anchor,
        s,
        flags=re.IGNORECASE | re.DOTALL,
    )

    for lvl in ("1", "2", "3", "4"):
        hashes = "#" * int(lvl)
        s = re.sub(
            rf"<h{lvl}\b[^>]*>(.*?)</h{lvl}>",
            lambda m, h=hashes: f"\n\n{h} {re.sub(r'<[^>]+>', ' ', m.group(1)).strip()}\n\n",
            s,
            flags=re.IGNORECASE | re.DOTALL,
        )

    s = re.sub(
        r"<(?:strong|b)\b[^>]*>(.*?)</(?:strong|b)>",
        lambda m: f"**{re.sub(r'<[^>]+>', ' ', m.group(1)).strip()}**",
        s,
        flags=re.IGNORECASE | re.DOTALL,
    )
    s = re.sub(
        r"<blockquote\b[^>]*>(.*?)</blockquote>",
        lambda m: "\n\n> " + re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(1))).strip() + "\n\n",
        s,
        flags=re.IGNORECASE | re.DOTALL,
    )
    s = re.sub(
        r"<li\b[^>]*>(.*?)</li>",
        lambda m: "\n- " + re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(1))).strip(),
        s,
        flags=re.IGNORECASE | re.DOTALL,
    )
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"<hr\s*/?>", "\n\n---\n\n", s, flags=re.IGNORECASE)
    s = re.sub(r"</(?:p|div|section|article|ul|ol|tr|table)>", "\n\n", s, flags=re.IGNORECASE)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = scrub_forbidden(s, preserve_newlines=True)
    s = strip_emojis(s)

    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in s.splitlines()]
    cleaned = "\n".join(lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    if max_chars > 0 and len(cleaned) > max_chars:
        cleaned = cleaned[: max_chars - 3].rstrip() + "..."
    return cleaned


def parse_rss_datetime(raw: str | None) -> datetime | None:
    s = str(raw or "").strip()
    if not s:
        return None
    try:
        dt = email.utils.parsedate_to_datetime(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        pass
    try:
        iso = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


def yaml_quote(value: str) -> str:
    clean = re.sub(r"[\r\n]+", " ", str(value or "")).strip()
    clean = clean.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{clean}"'


WIRE_DATELINE_RE = re.compile(
    r"^(?:财联社|华尔街见闻|科创板日报|证券时报|新京报)\s*\d{1,2}月\d{1,2}日(?:电|讯)[，,：:\s]*"
)


def norm_dup_key(text: str) -> str:
    s = unicodedata.normalize("NFKC", str(text or "")).replace("\u2212", "-").lower()
    s = re.sub(r"(?<!\d)\.(?!\d)", "", s)
    return re.sub(r"[^\u4e00-\u9fffa-z0-9+\-%‰$¥€£.]+", "", s)


def dedup_summary(title: str, summary: str) -> str:
    """Strip duplicate bracketed titles and wire datelines; preserve diffs in signs, numbers, or CJK text."""
    t = strip_emojis(str(title or "").strip())
    s = strip_emojis(str(summary or "").strip())
    if not s:
        return ""
    m = re.match(r"^【([^】]{2,140})】\s*(.*)$", s)
    if m:
        bracket_txt = m.group(1).strip()
        rest = m.group(2).strip()
        nt = norm_dup_key(t)
        nb = norm_dup_key(bracket_txt)
        if nb and nt and nb == nt:
            s = rest
    s = WIRE_DATELINE_RE.sub("", s).strip()
    if not s:
        return ""
    nt = norm_dup_key(t)
    ns = norm_dup_key(s)
    if not nt:
        return s
    if ns == nt or (s.endswith(("...", "…")) and len(ns) >= 10 and nt.startswith(ns)):
        return ""
    return s


_BULL_WORDS = re.compile(
    r"(?i)(?:\b(?:surge|rally|beat|growth|launch|breakthrough|approve|record high|upgrade|inflow)\b|"
    r"上涨|大涨|飙升|突破|超预期|降息|批准|增持|利好|复苏|上调|回购)"
)


_BEAR_WORDS = re.compile(
    r"(?i)(?:\b(?:drop|plunge|miss|cut|delay|lawsuit|probe|warn|risk|decline|slump|hack|ban|layoff)\b|"
    r"下跌|暴跌|大跌|跳水|减持|调查|起诉|制裁|关税|警告|亏损|裁员|风险|承压|下调|脱锚)"
)


_AI_BULL_WORDS = re.compile(r"收涨|突破|开源|发布|推出|上线|融资|增长|翻倍|加速|证明")
_AI_BEAR_WORDS = re.compile(r"回落|下跌|暴跌|征税|限制|阻止|质疑|争议|调查|禁令|封杀|亏损")


def infer_signal_direction(text: str, *, aihot: bool = False) -> str:
    bull = len((_AI_BULL_WORDS if aihot else _BULL_WORDS).findall(text))
    bear = len((_AI_BEAR_WORDS if aihot else _BEAR_WORDS).findall(text))
    if bull > bear:
        return "bullish"
    if bear > bull:
        return "bearish"
    return "neutral"


def canon_hk_code(digits: str) -> str:
    d = str(digits or "").strip()
    if len(d) == 5 and d.startswith("0"):
        d = d[1:]
    return f"{d}.HK"


def canon_ticker_sym(sym: str) -> str:
    s = str(sym or "").strip().upper()
    m_hk = re.fullmatch(r"(\d{4,5})\.HK", s)
    if m_hk:
        return canon_hk_code(m_hk.group(1))
    m_hk_pref = re.fullmatch(r"HK(\d{4,5})", s)
    if m_hk_pref:
        return canon_hk_code(m_hk_pref.group(1))
    return s


_TICKER_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bAAPL\b|Apple|苹果|iPhone|iPad|MacBook", "AAPL"),
    (r"\bNVDA\b|Nvidia|英伟达|黄仁勋|Blackwell|Rubin|CUDA", "NVDA"),
    (r"\bMSFT\b|Microsoft|微软|Azure|OpenAI|GPT-", "MSFT"),
    (r"\bGOOG\b|\bGOOGL\b|Google|Alphabet|DeepMind|Gemini|谷歌", "GOOG"),
    (r"\bMETA\b|Llama|扎克伯格|Zuckerberg|Facebook|Instagram", "META"),
    (r"\bAMZN\b|Amazon|\bAWS\b|亚马逊|贝索斯", "AMZN"),
    (r"\bTSLA\b|Tesla|特斯拉|马斯克|\bFSD\b", "TSLA"),
    (r"\bAMD\b|超威半导体|苏姿丰", "AMD"),
    (r"\bAVGO\b|Broadcom|博通", "AVGO"),
    (r"\bTSM\b|TSMC|台积电|CoWoS", "TSM"),
    (r"\bMU\b|Micron|美光|\bHBM\b", "MU"),
    (r"\bINTC\b|Intel|英特尔", "INTC"),
    (r"\bQCOM\b|Qualcomm|高通", "QCOM"),
    (r"\bPLTR\b|Palantir", "PLTR"),
    (r"\bORCL\b|Oracle|甲骨文", "ORCL"),
    (r"\bNFLX\b|Netflix|奈飞", "NFLX"),
    (r"\bARM\b|Arm Holdings", "ARM"),
    (r"\bSMCI\b|Super Micro|超微电脑", "SMCI"),
    (r"\bCOIN\b|Coinbase", "COIN"),
    (r"\bMSTR\b|MicroStrategy", "MSTR"),
    (r"(?<![A-Z0-9$])(?:0?0700\.HK|TCEHY)(?![A-Z0-9])|腾讯控股|腾讯", "0700.HK"),
    (r"(?<![A-Z0-9$])(?:0?9988\.HK|\bBABA\b)(?![A-Z0-9])|阿里巴巴|阿里", "BABA"),
    (r"(?<![A-Z0-9$])0?3690\.HK(?![A-Z0-9])|美团", "3690.HK"),
    (r"(?<![A-Z0-9$])0?1810\.HK(?![A-Z0-9])|小米集团|小米汽车", "1810.HK"),
    (r"(?<![A-Z0-9$])(?:0?0981\.HK|688981)(?![A-Z0-9])|中芯国际", "0981.HK"),
    (r"(?<![A-Z0-9$])(?:0?1211\.HK|002594)(?![A-Z0-9])|比亚迪", "1211.HK"),
    (r"(?<![A-Z0-9$])(?:ZHIPU|0?2513\.HK|02513)(?![A-Z0-9])|智谱", "ZHIPU"),
    (r"\bSPY\b|标普500|S&P\s*500", "SPY"),
    (r"\bQQQ\b|纳指|纳斯达克|Nasdaq", "QQQ"),
    (r"\bBTC\b|Bitcoin|比特币", "BTC"),
    (r"\bETH\b|Ethereum|以太坊", "ETH"),
    (r"\bFed\b|Federal Reserve|FOMC|Treasury|美联储|鲍威尔|美债|通胀|\bCPI\b|\bPCE\b|非农", "US10Y"),
)


# Case-sensitive bare ticker patterns (avoid matching English words like "arm" or "mu")
# plus case-insensitive company / asset names.
_KOL_TICKERS = {
    "NVDA": re.compile(r"\$NVDA\b|(?<![A-Z0-9$])NVDA(?![A-Z0-9])|(?i:\bNvidia\b|英伟达|黄仁勋|Blackwell|Rubin|CUDA)"),
    "AAPL": re.compile(r"\$AAPL\b|(?<![A-Z0-9$])AAPL(?![A-Z0-9])|(?i:\bApple\b|苹果|iPhone|iPad|MacBook)"),
    "MSFT": re.compile(r"\$MSFT\b|(?<![A-Z0-9$])MSFT(?![A-Z0-9])|(?i:\bMicrosoft\b|微软|Azure)"),
    "GOOG": re.compile(r"\$GOOGL?\b|(?<![A-Z0-9$])GOOGL?(?![A-Z0-9])|(?i:\bAlphabet\b|\bGoogle\b|谷歌|DeepMind|Gemini)"),
    "META": re.compile(r"\$META\b|(?<![A-Z0-9$])META(?![A-Z0-9])|(?i:\bMeta\s+Platforms\b|\bLlama\s*[34]|\bZuckerberg\b|扎克伯格)"),
    "AMZN": re.compile(r"\$AMZN\b|(?<![A-Z0-9$])AMZN(?![A-Z0-9])|(?i:\bAmazon\b|\bAWS\b|亚马逊)"),
    "AVGO": re.compile(r"\$AVGO\b|(?<![A-Z0-9$])AVGO(?![A-Z0-9])|(?i:\bBroadcom\b|博通)"),
    "TSLA": re.compile(r"\$TSLA\b|(?<![A-Z0-9$])TSLA(?![A-Z0-9])|(?i:\bTesla\b|特斯拉|\bFSD\b|\bRobotaxi\b)"),
    "AMD": re.compile(r"\$AMD\b|(?<![A-Z0-9$])AMD(?![A-Z0-9])|(?i:超威半导体|苏姿丰)"),
    "TSM": re.compile(r"\$TSMC?\b|(?<![A-Z0-9$])TSMC?(?![A-Z0-9])|(?i:台积电|\bCoWoS\b)"),
    "INTC": re.compile(r"\$INTC\b|(?<![A-Z0-9$])INTC(?![A-Z0-9])|(?i:\bIntel\b|英特尔)"),
    "QCOM": re.compile(r"\$QCOM\b|(?<![A-Z0-9$])QCOM(?![A-Z0-9])|(?i:\bQualcomm\b|高通)"),
    "MU": re.compile(r"\$MU\b|(?<![A-Z0-9$])MU(?![A-Z0-9])|(?i:\bMicron\b|美光|\bHBM[34]?\b|\bDRAM\b|\bNAND\b)"),
    "NFLX": re.compile(r"\$NFLX\b|(?<![A-Z0-9$])NFLX(?![A-Z0-9])|(?i:\bNetflix\b|奈飞)"),
    "ARM": re.compile(r"\$ARM\b|(?<![A-Z0-9$])ARM(?![A-Z0-9])|(?i:\bArm\s+Holdings\b)"),
    "PLTR": re.compile(r"\$PLTR\b|(?<![A-Z0-9$])PLTR(?![A-Z0-9])|(?i:\bPalantir\b)"),
    "ORCL": re.compile(r"\$ORCL\b|(?<![A-Z0-9$])ORCL(?![A-Z0-9])|(?i:\bOracle\b|甲骨文)"),
    "SMCI": re.compile(r"\$SMCI\b|(?<![A-Z0-9$])SMCI(?![A-Z0-9])|(?i:\bSuper\s*Micro\b|超微电脑)"),
    "COIN": re.compile(r"\$COIN\b|(?<![A-Z0-9$])COIN(?![A-Z0-9])|(?i:\bCoinbase\b)"),
    "MSTR": re.compile(r"\$MSTR\b|(?<![A-Z0-9$])MSTR(?![A-Z0-9])|(?i:\bMicroStrategy\b|\bStrategy\s+Inc\b)"),
    "BABA": re.compile(r"\$BABA\b|(?<![A-Z0-9$])BABA(?![A-Z0-9])|(?i:\bAlibaba\b|阿里巴巴|阿里)"),
    "BTC": re.compile(r"\$BTC\b|(?<![A-Z0-9$])BTC(?![A-Z0-9])|(?i:\bBitcoin\b|比特币)"),
    "ETH": re.compile(r"\$ETH\b|(?<![A-Z0-9$])ETH(?![A-Z0-9])|(?i:\bEthereum\b|以太坊)"),
    "SOL": re.compile(r"\$SOL\b|(?<![A-Z0-9$])SOL(?![A-Z0-9])|(?i:\bSolana\b)"),
    "SP500": re.compile(r"\$SPY\b|\$SPX\b|(?<![A-Z0-9$])(?:SPY|SPX|SP500)(?![A-Z0-9])|(?i:S&P\s*500|标普\s*500|标普)"),
    "QQQ": re.compile(r"\$QQQ\b|\$NDX\b|(?<![A-Z0-9$])(?:QQQ|NDX)(?![A-Z0-9])|(?i:\bNasdaq\b|纳斯达克|纳指)"),
    "GOLD": re.compile(r"\$GLD\b|\$XAU\b|(?<![A-Z0-9$])(?:GLD|XAU|XAUUSD)(?![A-Z0-9])|(?i:黄金|金价|\bgold\s+(?:price|rally|bull|bear|etf|futures|reserve|miners?))"),
    "WTI": re.compile(r"\$WTI\b|\$USO\b|(?<![A-Z0-9$])WTI(?![A-Z0-9])|(?i:\bBrent\b|\bcrude\s+oil\b|原油|油价)"),
    "DOLLAR": re.compile(r"\$DXY\b|(?<![A-Z0-9$])DXY(?![A-Z0-9])|(?i:\bdollar\s+index\b|美元指数|美元流动性)"),
}


# Forum headlines also cover Hong Kong listings and commodity prices.
_FORUM_TICKERS = {
    "NVDA": _KOL_TICKERS["NVDA"],
    "AAPL": re.compile(r"\$AAPL\b|(?<![A-Z0-9$])AAPL(?![A-Z0-9])|(?i:\bApple\b|苹果|iPhone)"),
    "MSFT": _KOL_TICKERS["MSFT"],
    "GOOG": re.compile(r"\$GOOGL?\b|(?<![A-Z0-9$])GOOGL?(?![A-Z0-9])|(?i:\bAlphabet\b|\bGoogle\b|谷歌|Gemini)"),
    "META": re.compile(r"\$META\b|(?<![A-Z0-9$])META(?![A-Z0-9])|(?i:\bMeta\s+Platforms\b|\bZuckerberg\b|扎克伯格)"),
    "AMZN": _KOL_TICKERS["AMZN"],
    "AVGO": _KOL_TICKERS["AVGO"],
    "TSLA": _KOL_TICKERS["TSLA"],
    "AMD": _KOL_TICKERS["AMD"],
    "TSM": _KOL_TICKERS["TSM"],
    "INTC": _KOL_TICKERS["INTC"],
    "MU": re.compile(r"\$MU\b|(?<![A-Z0-9$])MU(?![A-Z0-9])|(?i:\bMicron\b|美光|\bHBM[34]?\b)"),
    "ARM": _KOL_TICKERS["ARM"],
    "PLTR": _KOL_TICKERS["PLTR"],
    "BABA": re.compile(r"\$BABA\b|(?<![A-Z0-9$])(?:BABA|0?9988\.HK)(?![A-Z0-9])|(?i:\bAlibaba\b|阿里巴巴|阿里)"),
    "0700.HK": re.compile(r"(?<![A-Z0-9$])(?:0?0700\.HK|TCEHY)(?![A-Z0-9])|(?i:\bTencent\b|腾讯控股|腾讯)"),
    "3690.HK": re.compile(r"(?<![A-Z0-9$])0?3690\.HK(?![A-Z0-9])|(?i:\bMeituan\b|美团)"),
    "1810.HK": re.compile(r"(?<![A-Z0-9$])0?1810\.HK(?![A-Z0-9])|(?i:\bXiaomi\b|小米集团|小米汽车)"),
    "0981.HK": re.compile(r"(?<![A-Z0-9$])(?:0?0981\.HK|688981)(?![A-Z0-9])|(?i:\bSMIC\b|中芯国际)"),
    "1211.HK": re.compile(r"(?<![A-Z0-9$])(?:0?1211\.HK|002594)(?![A-Z0-9])|(?i:\bBYD\b|比亚迪)"),
    "BTC": _KOL_TICKERS["BTC"],
    "BNB": re.compile(r"\$BNB\b|(?<![A-Z0-9$])BNB(?![A-Z0-9])|(?i:\bBinance\b|币安)"),
    "ETH": _KOL_TICKERS["ETH"],
    "SOL": _KOL_TICKERS["SOL"],
    "ZHIPU": re.compile(r"(?<![A-Z0-9$])(?:ZHIPU|ZHIPUUSDT|2513\.HK|02513)(?![A-Z0-9])|(?i:智谱|\bChatGLM\b|\bAutoGLM\b)"),
    "SP500": re.compile(r"\$SPY\b|\$SPX\b|(?<![A-Z0-9$])(?:SPY|SPX|SP500)(?![A-Z0-9])|(?i:S&P\s*500|标普)"),
    "QQQ": _KOL_TICKERS["QQQ"],
    "GOLD": re.compile(r"\$GLD\b|\$XAU\b|(?<![A-Z0-9$])(?:GLD|XAU|XAUUSD|PAXG)(?![A-Z0-9])|(?i:黄金|金价|伦敦金|\bgold\b)"),
    "WTI": _KOL_TICKERS["WTI"],
    "DOLLAR": _KOL_TICKERS["DOLLAR"],
}


_TICKER_ALIASES = {
    "GOOGL": "GOOG",
    "TSMC": "TSM",
    "SPY": "SP500",
    "SPX": "SP500",
    "GLD": "GOLD",
    "XAU": "GOLD",
    "DXY": "DOLLAR",
    "USO": "WTI",
}


def extract_tickers(text: str, *, source: Literal["rss", "forum", "kol"] = "rss") -> list[str]:
    """Preserve each source's symbol vocabulary, match order and six-ticker limit."""
    if not text:
        return []
    symbols = re.findall(r"\$([A-Z]{2,5})\b", text)
    if source != "rss":
        symbols = [_TICKER_ALIASES.get(sym, sym) for sym in symbols]
    if source != "kol":
        for pattern, flags in (
            (r"(?<![A-Za-z0-9])(?:SH|SZ|BJ)\d{6}(?![A-Za-z0-9])", 0),
            (r"(?<![A-Za-z0-9])\d{6}\.(?:SZ|SS|SH|BJ)(?![A-Za-z0-9])", re.I),
            (r"(?<![A-Za-z0-9])\d{4,5}\.HK(?![A-Za-z0-9])", re.I),
            (r"(?<![A-Za-z0-9])HK\d{4,5}(?![A-Za-z0-9])", re.I),
        ):
            symbols.extend(canon_ticker_sym(code) for code in re.findall(pattern, text, flags))
    if source == "rss":
        symbols.extend(sym for pat, sym in _TICKER_PATTERNS if re.search(pat, text, re.I))
    else:
        rules = _FORUM_TICKERS if source == "forum" else _KOL_TICKERS
        symbols.extend(sym for sym, rx in rules.items() if rx.search(text))
    return list(dict.fromkeys(symbols))[:6]


_SUBTOPIC_RULES: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("DeepSeek", ("ai", "ashare"), r"\bDeepSeek\b|深度求索"),
    ("OpenAI", ("ai", "equities"), r"\bOpenAI\b|\bChatGPT\b|\bGPT-?[45o]\b|\bo[134]-?mini\b|\bSora\b"),
    ("Anthropic", ("ai", "equities"), r"\bAnthropic\b|\bClaude\b"),
    ("国产大模型", ("ai", "ashare"), r"通义|千问|\bQwen\b|MiniMax|\bGLM\b|智谱|\bKimi\b|月之暗面|豆包|文心|混元"),
    (
        "算力芯片",
        ("ai", "equities", "ashare"),
        r"\bNVDA\b|Nvidia|英伟达|Blackwell|Rubin|\bGPU\b|\bHBM\b|算力|芯片|半导体|晶圆|光刻|\bTSMC\b|台积电|\bAMD\b|\bAVGO\b|博通|\bCoWoS\b|美光",
    ),
    ("具身智能", ("ai", "ashare"), r"具身智能|人形机器人|宇树|Optimus|Robotics|机器狗"),
    ("自动驾驶", ("ai", "equities", "ashare"), r"自动驾驶|智驾|\bFSD\b|Robotaxi|端到端驾驶|高阶智驾"),
    ("科创·半导体", ("ashare",), r"科创板|半导体|中芯|寒武纪|国产芯片|存储芯片|芯片国产"),
    ("华为·汽车", ("ashare",), r"华为|鸿蒙|昇腾|比亚迪|新能源车|问界|小米汽车|理想|蔚来|小鹏|宁德时代"),
    ("港股科技", ("ashare",), r"恒指|恒生|港股|港交所|南向|腾讯|阿里|美团|快手|百度"),
    ("A股政策·两融", ("ashare",), r"A股|沪指|深成指|创业板|上证|深证|证监会|北向|两融|融资保证金|茅台"),
    ("财报业绩", ("equities", "ashare"), r"财报|业绩|营收|\bEPS\b|Guidance|指引|10-K|10-Q|8-K|20-F|6-K|超预期|不及预期"),
    ("七巨头·云厂", ("equities", "ai"), r"\bAAPL\b|Apple|苹果|\bMSFT\b|微软|\bAMZN\b|亚马逊|\bGOOG\b|谷歌|\bMETA\b|\bTSLA\b|特斯拉|\bAWS\b|Azure"),
    ("美联储", ("macro",), r"美联储|\bFed\b|\bFOMC\b|鲍威尔|Powell|降息|加息|点阵图|缩表|利率决议"),
    ("CPI·通胀", ("macro",), r"\bCPI\b|\bPCE\b|\bPPI\b|通胀|非农|\bNFP\b|失业率|\bGDP\b|就业"),
    ("黄金·大宗", ("macro",), r"黄金|金价|\bXAU\b|\bGold\b|原油|\bWTI\b|\bBrent\b|白银|铜价|大宗商品"),
    ("美债·汇率", ("macro",), r"美债|10年期|国债收益率|美元指数|\bDXY\b|人民币|离岸|汇率|关税"),
    ("ETF·监管", ("crypto",), r"\bETF\b|\bSEC\b|牌照|立法|稳定币|USDC|USDT|合规"),
    ("BTC·链上生态", ("crypto",), r"比特币|\bBTC\b|以太坊|\bETH\b|\bSOL\b|Solana|Coinbase|Binance|OKX|U溢价|资金费率"),
)


def extract_subtopics(text: str, category: str = "") -> list[str]:
    """Extract structured subtopic tags aligned to the signal's primary sector."""
    src = str(text or "")
    if not src.strip():
        return []
    cat = str(category or "").strip().lower()
    primary: list[str] = []
    secondary: list[str] = []
    for label, cats, pat in _SUBTOPIC_RULES:
        if re.search(pat, src, re.IGNORECASE):
            if cat and cat in cats:
                if label not in primary:
                    primary.append(label)
            elif label not in secondary:
                secondary.append(label)
    combined = primary + [x for x in secondary if x not in primary]
    return combined[:4]


AI_TOKEN_CONTEXT_RE = re.compile(
    r"(?i)\b(?:llm|llms|model|models|context|claude|gpt|gemini|inference|anthropic|openai|"
    r"prompt|embedding|attention|transformer|weights|neuron|interpretability|sandbox|agent|agents)\b"
)


CRYPTO_EXPLICIT_RE = re.compile(
    r"(?i)\b(?:bitcoin|btc|eth|ethereum|sol|solana|crypto|cryptocurrency|stablecoin|usdc|usdt|"
    r"on-?chain|defi|perp|airdrop|memecoin|altcoin|halving)\b|比特币|以太坊|稳定币|链上|巨鲸|加密"
)


_BRIEF_KOL_TOPICS = (
    ("AI资本开支与美股科技", ("ai", "capex", "nvda", "intc", "芯片", "数据中心", "算力", "微软", "谷歌", "半导体", "hardware")),
    ("美联储流动性与美债", ("fed", "fomc", "liquidity", "treasury", "rate", "cpi", "pce", "美联储", "美债", "利率", "流动性", "降息", "通胀")),
    ("地缘与监管", ("sec", "cftc", "tariff", "ban", "trump", "监管", "关税", "州长", "政策", "调查")),
    ("BTC周期与山寨", ("btc", "eth", "sol", "crypto", "etf", "defi", "比特币", "以太坊", "山寨", "链上")),
)


def classify_kol_topic(headline: str, takeaway: str, category: str, *, decision_brief: bool = False) -> str:
    txt = f"{headline} {takeaway}"
    if decision_brief:
        # Decision brief topics are also Notion select values.
        low = txt.lower()
        for label, terms in _BRIEF_KOL_TOPICS:
            if any(term in low for term in terms):
                return label
        return "BTC周期与山寨" if "加密" in category else "市场情绪与估值"
    if re.search(r"(?i)\b(?:nvda|tsm|amd|avgo|intc|mu|hbm4?|cowos|gpu|anthropic|openai|claude|llm)\b|算力|芯片|半导体|存储|大模型", txt):
        return "AI算力与半导体"
    if re.search(r"(?i)\b(?:fed|fomc|cpi|pce|treasury|yields?|rates?)\b|美联储|美债|利率|流动性|降息|通胀|财政|赤字", txt):
        return "美联储流动性与宏观"
    if category == "crypto" or (CRYPTO_EXPLICIT_RE.search(txt) and not AI_TOKEN_CONTEXT_RE.search(txt)):
        return "加密周期与链上"
    if re.search(r"(?i)\b(?:tariff|sanction|sec)\b|关税|制裁|监管|地缘", txt):
        return "地缘与政策监管"
    if category == "quant":
        return "量化因子与风控"
    return "美股策略与估值"


_EQN_WIRE_RULES = (
    (
        ("fed", "fomc", "cpi", "pce", "treasury", "tariff", "美联储", "鲍威尔", "降息", "加息", "利率", "通胀", "美债", "国债", "关税", "非农"),
        "直接牵动美联储利率路径与跨资产风险定价，影响美股估值与加密流动性。",
    ),
    (
        ("nvda", "nvidia", "openai", "anthropic", "deepseek", "llm", "gpu", "hbm", "英伟达", "芯片", "算力", "大模型", "半导体"),
        "涉及 AI 算力供应链或头部科技催化，直接影响科技权重股与算力链情绪。",
    ),
)

_EQN_BRIEF_RULES = (
    (
        ("fed", "美联储", "加息", "降息", "利率", "treasury", "pce", "通胀"),
        "直接影响美联储利率路径与无风险收益率定价，牵动美股估值及加密流动性预期。",
    ),
    (
        ("nvda", "英伟达", "sk海力士", "芯片", "openai", "甲骨文", "orcl"),
        "涉及 AI 算力供应链或资本开支置信度，直接左右科技权重与芯片板块风险偏好。",
    ),
)


def eqn_why_it_matters(combined: str, category: str, direction: str | None = None) -> str:
    low = combined.lower()
    macro_rule, ai_rule = _EQN_WIRE_RULES if direction is None else _EQN_BRIEF_RULES
    if any(term in low for term in macro_rule[0]):
        return macro_rule[1]
    if (direction is None and category == "ai") or any(term in low for term in ai_rule[0]):
        return ai_rule[1]
    if direction is None:
        if category == "crypto":
            return "方程式加密一线突发快讯，易引发短线波动与流动性溢价变化。"
        if category == "equities":
            return "方程式海外传统金融突发快讯，直接映射美股权重与板块盘面预期。"
        return "方程式全球宏观突发快讯，建议结合盘面波动与风险灯号跟踪后续发酵。"
    if category == "crypto":
        return "对加密现货情绪及场外风险溢价有直接冲击，建议延续配对对冲、忌盲目追涨杀跌。"
    if direction == "bearish":
        return "高优先级风险事件，短期压制市场风险偏好，建议维持审慎仓位。"
    if direction == "bullish":
        return "有助于修复局部风险偏好，可继续跟踪后续成交量与均线确认。"
    return "重要市场结构信号，建议结合盘面波动与风险灯号观察后续传导。"


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


def clean_business_text(text: str) -> str:
    """Replace internal quant jargon with plain Chinese business wording."""
    return scrub_quant_thesis(text, preserve_newlines=False)
