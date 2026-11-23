"""Curated official AI, global macro/equity RSS feeds, CN finance wires, curated articles & OPML exporter."""
from __future__ import annotations

import email.utils
import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:
    requests = None  # type: ignore[assignment]

_BACKEND_DIR = Path(__file__).resolve().parent
if str(_BACKEND_DIR.parent) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR.parent))

from backend.fred_and_market import COCKPIT_ROOT, DATA_DIR, atomic_write_json
from backend.public_snapshot import file_lock

BJT = timezone(timedelta(hours=8))
UTC = timezone.utc
RSS_CACHE_PATH = DATA_DIR / "rss_cache.json"
RSS_CACHE_TTL = 900  # 15 minutes
ARTICLE_CACHE_PATH = DATA_DIR / "curated_articles_cache.json"
ARTICLE_CACHE_TTL = 1800  # 30 minutes
ARTICLES_DIR = COCKPIT_ROOT / "articles"
DEFAULT_OPML_PATH = COCKPIT_ROOT / "dist" / "macro_cockpit_feeds.opml"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )
}

# Curated high-SNR feeds verified live (HTTP 200 OK)
CURATED_RSS_FEEDS: tuple[dict[str, str], ...] = (
    {
        "id": "openai_blog",
        "source": "OpenAI 官方",
        "url": "https://openai.com/news/rss.xml",
        "html_url": "https://openai.com/news/",
        "category": "ai",
        "tier": "T1",
        "why": "OpenAI 一手产品与模型发布，直接影响算力需求、云厂合作与 AI 应用估值叙事。",
        "identity_encoding": "1",
    },
    {
        "id": "deepmind_blog",
        "source": "DeepMind 官方",
        "url": "https://deepmind.google/blog/rss.xml",
        "html_url": "https://deepmind.google/discover/blog/",
        "category": "ai",
        "tier": "T1",
        "why": "Google DeepMind 前沿模型与科学计算突破，映射谷歌 AI 技术与算力护城河进展。",
    },
    {
        "id": "google_ai",
        "source": "Google AI",
        "url": "https://blog.google/technology/ai/rss/",
        "html_url": "https://blog.google/technology/ai/",
        "category": "ai",
        "tier": "T1",
        "why": "Google 搜索与云端 AI 商业化更新，反映大模型分发与资本开支变现效率。",
    },
    {
        "id": "anthropic_news",
        "source": "Anthropic 官方",
        "url": "https://raw.githubusercontent.com/alan-turing-institute/ai-rss-feeds/main/feeds/anthropic-news.xml",
        "html_url": "https://www.anthropic.com/news",
        "category": "ai",
        "tier": "T1",
        "max_age_days": "14",
        "why": "Anthropic 一手产品、企业级 Agent 与模型生态动态，直接影响云厂商与软件应用估值。",
    },
    {
        "id": "anthropic_research",
        "source": "Anthropic 研究",
        "url": "https://raw.githubusercontent.com/alan-turing-institute/ai-rss-feeds/main/feeds/anthropic-research.xml",
        "html_url": "https://www.anthropic.com/research",
        "category": "ai",
        "tier": "T1",
        "max_age_days": "21",
        "why": "Anthropic 前沿安全对齐与可解释性研究发布，指示大模型能力边界与合规方向。",
    },
    {
        "id": "hf_blog",
        "source": "Hugging Face",
        "url": "https://huggingface.co/blog/feed.xml",
        "html_url": "https://huggingface.co/blog",
        "category": "ai",
        "tier": "T1_5",
        "why": "开源模型与推理框架最新动向，衡量开源模型追赶速度与算力下沉趋势。",
    },
    {
        "id": "the_batch",
        "source": "The Batch",
        "url": "https://raw.githubusercontent.com/alan-turing-institute/ai-rss-feeds/main/feeds/the-batch.xml",
        "html_url": "https://www.deeplearning.ai/the-batch/",
        "category": "ai",
        "tier": "T1_5",
        "max_age_days": "14",
        "why": "DeepLearning.AI 精选产业与研究要闻，提炼 AI 商业化与工程落地拐点。",
    },
    {
        "id": "hn_ai",
        "source": "HN AI 精选",
        "url": "https://hnrss.org/newest?q=AI&points=100",
        "html_url": "https://news.ycombinator.com/",
        "category": "ai",
        "tier": "T1_5",
        "why": "Hacker News 高赞工程技术讨论，过滤公关稿后保留一线开发者真实技术共识。",
    },
    {
        "id": "arxiv_ai",
        "source": "arXiv AI前沿",
        "url": "https://export.arxiv.org/api/query?search_query=cat:cs.AI+OR+cat:cs.LG&sortBy=submittedDate&sortOrder=descending&max_results=6",
        "html_url": "https://arxiv.org/list/cs.AI/recent",
        "category": "ai",
        "tier": "T1",
        "max_age_days": "14",
        "why": "arXiv 人工智能与机器学习一手最新预印本论文，跟踪架构效率与推理范式演化。",
    },
    {
        "id": "qbitai",
        "source": "量子位",
        "url": "https://www.qbitai.com/feed",
        "html_url": "https://www.qbitai.com/",
        "category": "ai",
        "tier": "T2",
        "why": "中文科技一线报道，补充国产大模型、算力芯片与产业应用进展。",
    },
    {
        "id": "arstechnica_ai",
        "source": "Ars Technica",
        "url": "https://arstechnica.com/ai/feed/",
        "html_url": "https://arstechnica.com/ai/",
        "category": "ai",
        "tier": "T2",
        "why": "深度科技媒体对 AI 芯片供应链、监管与商业诉讼的深度追踪。",
    },
    {
        "id": "fed_press",
        "source": "美联储官方",
        "url": "https://www.federalreserve.gov/feeds/press_all.xml",
        "html_url": "https://www.federalreserve.gov/newsevents/pressreleases.htm",
        "category": "macro",
        "tier": "T1",
        "why": "美联储一手货币政策声明与监管发布，直接作用于美债收益率曲线与全球美元流动性。",
    },
    {
        "id": "cnbc_top",
        "source": "CNBC 美股",
        "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114",
        "html_url": "https://www.cnbc.com/us-news/",
        "category": "equities",
        "tier": "T1_5",
        "why": "华尔街核心财报、并购与宏观焦点快讯，补充美股科技与指数日内交易主线。",
    },
    {
        "id": "cnbc_finance",
        "source": "CNBC 财经",
        "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664",
        "html_url": "https://www.cnbc.com/finance/",
        "category": "macro",
        "tier": "T1_5",
        "why": "美国银行体系、美联储利率预期、美债与信贷市场一线金融新闻。",
    },
    {
        "id": "marketwatch",
        "source": "MarketWatch",
        "url": "https://feeds.content.dowjones.io/public/rss/mw_topstories",
        "html_url": "https://www.marketwatch.com/",
        "category": "equities",
        "tier": "T1_5",
        "why": "道琼斯旗下盘前盘后异动、宏观数据解读与重点公司财报速递。",
    },
    {
        "id": "seekingalpha_currents",
        "source": "Seeking Alpha",
        "url": "https://seekingalpha.com/market_currents.xml",
        "html_url": "https://seekingalpha.com/market-news",
        "category": "equities",
        "tier": "T1_5",
        "why": "华尔街机构评级调整、财报超预期/不及预期与盘中突发个股催化快讯。",
    },
    {
        "id": "ft_markets",
        "source": "FT 全球市场",
        "url": "https://www.ft.com/markets?format=rss",
        "html_url": "https://www.ft.com/markets",
        "category": "macro",
        "tier": "T1_5",
        "why": "金融时报全球跨资产、外汇、主权债与大宗商品定价主线分析。",
    },
    {
        "id": "arxiv_qfin",
        "source": "arXiv 量化金融",
        "url": "https://export.arxiv.org/api/query?search_query=cat:q-fin.*&sortBy=submittedDate&sortOrder=descending&max_results=6",
        "html_url": "https://arxiv.org/list/q-fin/recent",
        "category": "macro",
        "tier": "T1",
        "max_age_days": "14",
        "why": "arXiv 量化金融与微观结构研究一手预印本，洞察风险平价、波动率与市场流动性研究。",
    },
    {
        "id": "coindesk",
        "source": "CoinDesk",
        "url": "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "html_url": "https://www.coindesk.com/",
        "category": "crypto",
        "tier": "T1_5",
        "why": "主流加密机构与监管核心媒体动态，跟踪 ETF 资金流、稳定币立法与机构配置。",
    },
    {
        "id": "investing_macro",
        "source": "Investing.com",
        "url": "https://www.investing.com/rss/news.rss",
        "html_url": "https://www.investing.com/",
        "category": "macro",
        "tier": "T1_5",
        "why": "全球财经头条、央行预期、外汇商品与欧美盘中宏观事件即时汇总。",
    },
    {
        "id": "cointelegraph",
        "source": "Cointelegraph",
        "url": "https://cointelegraph.com/rss",
        "html_url": "https://cointelegraph.com/",
        "category": "crypto",
        "tier": "T2",
        "why": "加密行业链上动向、合规进展与全球市场交易情绪高频追踪。",
    },
)

CURATED_ARTICLE_FEEDS: tuple[dict[str, str], ...] = (
    {
        "id": "import_ai",
        "publisher": "Import AI",
        "url": "https://importai.substack.com/feed",
        "html_url": "https://importai.substack.com/",
        "category": "ai",
        "tier": "T1_5",
    },
    {
        "id": "last_week_in_ai",
        "publisher": "Last Week in AI",
        "url": "https://lastweekin.ai/feed",
        "html_url": "https://lastweekin.ai/",
        "category": "ai",
        "tier": "T1_5",
    },
    {
        "id": "simon_willison",
        "publisher": "Simon Willison",
        "url": "https://simonwillison.net/atom/entries/",
        "html_url": "https://simonwillison.net/",
        "category": "ai",
        "tier": "T1_5",
    },
    {
        "id": "mit_tech_review",
        "publisher": "MIT Tech Review",
        "url": "https://www.technologyreview.com/topic/artificial-intelligence/feed",
        "html_url": "https://www.technologyreview.com/topic/artificial-intelligence/",
        "category": "ai",
        "tier": "T1_5",
    },
    {
        "id": "seekingalpha_articles",
        "publisher": "Seeking Alpha",
        "url": "https://seekingalpha.com/feed.xml",
        "html_url": "https://seekingalpha.com/",
        "category": "equities",
        "tier": "T1_5",
    },
    {
        "id": "lillog",
        "publisher": "Lil'Log",
        "url": "https://lilianweng.github.io/index.xml",
        "html_url": "https://lilianweng.github.io/",
        "category": "ai",
        "tier": "T1",
        "max_age_days": "180",
    },
    {
        "id": "bair_blog",
        "publisher": "BAIR Blog",
        "url": "https://bair.berkeley.edu/blog/feed.xml",
        "html_url": "https://bair.berkeley.edu/blog/",
        "category": "ai",
        "tier": "T1",
        "max_age_days": "120",
    },
    {
        "id": "ahead_of_ai",
        "publisher": "Ahead of AI",
        "url": "https://magazine.sebastianraschka.com/feed",
        "html_url": "https://magazine.sebastianraschka.com/",
        "category": "ai",
        "tier": "T1_5",
        "max_age_days": "30",
    },
)

_FORBIDDEN_SCRUB = (
    (r"\bE33\b|\bE32\b|\bE21\b", "波动结构"),
    (r"\bTier-1\b", "核心"),
    (r"\bTier-S\b", "高优"),
    (r"\bTier-A\b", "重点"),
    (r"\bSleeves?\b", "仓位"),
    (r"\bRegimes?\b", "市场周期"),
    (r"\barbitration\b", "风险评估"),
    (r"\bN/A\b", "—"),
    (r"测算口径|公式解释|公式说明|逻辑解释|联动说明|方法论说明", "规则摘要"),
    (r"待刷新|待补", "—"),
)

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
    (r"\bSPY\b|标普500|S&P\s*500", "SPY"),
    (r"\bQQQ\b|纳指|纳斯达克|Nasdaq", "QQQ"),
    (r"\bBTC\b|Bitcoin|比特币", "BTC"),
    (r"\bETH\b|Ethereum|以太坊", "ETH"),
    (r"\bFed\b|Federal Reserve|FOMC|Treasury|美联储|鲍威尔|美债|通胀|\bCPI\b|\bPCE\b|非农", "US10Y"),
)

_BULL_WORDS = re.compile(
    r"(?i)(?:\b(?:surge|rally|beat|growth|launch|breakthrough|approve|record high|upgrade|inflow)\b|"
    r"上涨|大涨|飙升|突破|超预期|降息|批准|增持|利好|复苏|上调|回购)"
)
_BEAR_WORDS = re.compile(
    r"(?i)(?:\b(?:drop|plunge|miss|cut|delay|lawsuit|probe|warn|risk|decline|slump|hack|ban|layoff)\b|"
    r"下跌|暴跌|大跌|跳水|减持|调查|起诉|制裁|关税|警告|亏损|裁员|风险|承压|下调|脱锚)"
)


def _scrub_forbidden(text: str) -> str:
    s = str(text or "")
    for pat, rep in _FORBIDDEN_SCRUB:
        s = re.sub(pat, rep, s, flags=re.IGNORECASE)
    return s


def _clean_text(raw: Any, *, max_len: int = 240) -> str:
    txt = str(raw or "")
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = html.unescape(txt)
    txt = _scrub_forbidden(txt)
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
    s = _scrub_forbidden(s)

    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in s.splitlines()]
    cleaned = "\n".join(lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    if max_chars > 0 and len(cleaned) > max_chars:
        cleaned = cleaned[: max_chars - 3].rstrip() + "..."
    return cleaned


def _parse_rss_datetime(raw: str | None) -> datetime | None:
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


def extract_tickers(text: str) -> list[str]:
    """Extract canonical US/crypto/macro tickers from mixed English & Chinese financial copy."""
    if not text:
        return []
    out: list[str] = []
    for m in re.findall(r"\$([A-Z]{2,5})\b", text):
        if m not in out:
            out.append(m)
    for pat, sym in _TICKER_PATTERNS:
        if re.search(pat, text, re.IGNORECASE) and sym not in out:
            out.append(sym)
    return out[:6]


_extract_tickers = extract_tickers

_ASHARE_KEYWORDS_RE = re.compile(
    r"A股|沪指|深成指|创业板|科创板|上证|深证|北交所|恒指|恒生|港股|证监会|上交所|深交所|港交所|"
    r"北向|南向|融资保证金|比亚迪|华为|鸿蒙|昇腾|宁德时代|茅台|中芯|寒武纪"
)

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


def _infer_direction(text: str) -> str:
    bull = len(_BULL_WORDS.findall(text))
    bear = len(_BEAR_WORDS.findall(text))
    if bull > bear:
        return "bullish"
    if bear > bull:
        return "bearish"
    return "neutral"


def _strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower() if "}" in tag else tag.lower()


def _parse_feed_entries(
    xml_text: str,
    *,
    now_utc: datetime | None = None,
    max_age_days: int = 7,
) -> list[dict[str, Any]]:
    """Parse RSS 2.0 / Atom XML into raw normalized entry dicts."""
    if not xml_text or not xml_text.strip():
        return []
    now = now_utc or datetime.now(UTC)
    cutoff = now - timedelta(days=max(1, int(max_age_days)))

    cleaned_xml = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", xml_text)
    try:
        root = ET.fromstring(cleaned_xml)
    except ET.ParseError:
        return []

    entries_out: list[dict[str, Any]] = []
    nodes = [elem for elem in root.iter() if _strip_ns(elem.tag) in ("item", "entry")]
    for entry in nodes:
        title = ""
        link = ""
        pub_raw = ""
        bodies: dict[str, str] = {}
        for child in entry:
            name = _strip_ns(child.tag)
            if name == "title" and not title:
                title = child.text or "".join(child.itertext())
            elif name in ("encoded", "content", "description", "summary"):
                val = (child.text or "".join(child.itertext())).strip()
                if val and (name not in bodies or len(val) > len(bodies[name])):
                    bodies[name] = val
            elif name == "link":
                href = child.attrib.get("href") or (child.text or "").strip()
                if href and (not link or child.attrib.get("rel", "alternate") == "alternate"):
                    link = href.strip()
            elif name in ("pubdate", "published", "updated") and not pub_raw:
                pub_raw = (child.text or "").strip()

        dt = _parse_rss_datetime(pub_raw) or now
        if dt < cutoff:
            continue

        short_raw = bodies.get("description") or bodies.get("summary") or bodies.get("encoded") or bodies.get("content") or ""
        long_raw = max(
            (bodies.get("encoded", ""), bodies.get("content", ""), bodies.get("summary", ""), bodies.get("description", "")),
            key=len,
        )
        entries_out.append(
            {
                "title": title,
                "short_raw": short_raw,
                "long_raw": long_raw,
                "link": link if link.startswith(("http://", "https://")) else "",
                "dt_utc": dt,
            }
        )
    return entries_out


def parse_feed_xml(
    xml_text: str,
    spec: dict[str, str],
    *,
    now_utc: datetime | None = None,
    max_items: int = 5,
) -> list[dict[str, Any]]:
    """Parse RSS 2.0 or Atom XML bytes/string into Kazike raw signal items."""
    max_age_days = int(spec.get("max_age_days") or 7)
    raw_entries = _parse_feed_entries(xml_text, now_utc=now_utc, max_age_days=max_age_days)

    items_out: list[dict[str, Any]] = []
    spec_id = str(spec.get("id") or spec.get("name") or "custom")
    source_name = str(spec.get("source") or spec.get("name") or "外部订阅")
    category = str(spec.get("category") or "macro")
    tier = str(spec.get("tier") or "T1_5")
    why_text = str(spec.get("why") or "来自精选外部信息源。")
    for ent in raw_entries:
        if len(items_out) >= max_items:
            break
        clean_title = _clean_text(ent["title"], max_len=140)
        if not clean_title:
            continue
        clean_summary = _clean_text(ent["short_raw"], max_len=240) or clean_title
        time_bjt = ent["dt_utc"].astimezone(BJT).strftime("%Y-%m-%d %H:%M")

        combined_text = f"{clean_title} {clean_summary}"
        link = ent["link"]
        sig_id = f"rss-{spec_id}-{hashlib.sha1((clean_title + link).encode('utf-8')).hexdigest()[:10]}"
        items_out.append(
            {
                "id": sig_id,
                "source": source_name,
                "category": category,
                "source_tier": tier,
                "direction": _infer_direction(combined_text),
                "importance": "高" if tier == "T1" else "中",
                "time_bjt": time_bjt,
                "title": clean_title,
                "summary": clean_summary,
                "why_it_matters": why_text,
                "tickers": extract_tickers(combined_text),
                "topics": extract_subtopics(combined_text, category),
                "url": link,
            }
        )
    return items_out


def _fetch_one_feed(spec: dict[str, str], timeout: int = 7) -> list[dict[str, Any]]:
    if requests is None:
        return []
    headers = dict(_HEADERS)
    if spec.get("identity_encoding"):
        headers["Accept-Encoding"] = "identity"
    try:
        resp = requests.get(spec["url"], headers=headers, timeout=timeout)
        if resp.status_code != 200 or not resp.content:
            return []
        xml_text = resp.content.decode("utf-8", errors="replace")
        return parse_feed_xml(xml_text, spec, max_items=5)
    except Exception:
        return []


def _cls_sign(params: dict[str, Any]) -> str:
    qs = urllib.parse.urlencode(sorted((str(k), str(v)) for k, v in params.items()))
    sha1_hex = hashlib.sha1(qs.encode("utf-8")).hexdigest()
    return hashlib.md5(sha1_hex.encode("utf-8")).hexdigest()


def _split_cn_headline_and_summary(title: str, body: str) -> tuple[str, str]:
    t = _clean_text(title, max_len=140)
    b = _clean_text(body, max_len=260)
    if not t and b:
        m = re.match(r"^【([^】]{4,80})】\s*(.*)$", b)
        if m:
            t = m.group(1).strip()
            b = (m.group(2).strip() or t)
        else:
            first_sent = re.split(r"[。！？\n]", b, maxsplit=1)[0].strip()
            t = first_sent[:72] if first_sent else b[:72]
    if not b:
        b = t
    return (t, b)


def _infer_cn_category(text: str, default_cat: str) -> str:
    if _ASHARE_KEYWORDS_RE.search(text):
        return "ashare"
    low = text.lower()
    if any(k in low for k in ("bitcoin", "btc", "eth", "crypto", "比特币", "以太坊", "稳定币", "数字资产", "coinbase")):
        return "crypto"
    if any(k in low for k in ("openai", "anthropic", "claude", "gemini", "deepseek", "大模型", "算力", "芯片", "英伟达", "具身智能", "宇树", "ai")):
        return "ai"
    if any(k in low for k in ("美联储", "央行", "降息", "加息", "美债", "通胀", "cpi", "pce", "gdp", "非农", "关税", "国债")):
        return "macro"
    return default_cat


def collect_cn_finance_wires(timeout: int = 7) -> list[dict[str, Any]]:
    """Fetch CLS telegraph wires (incl. watch) and WallStreetCN live flashes (incl. A-share) into signal items."""
    if requests is None:
        return []
    signals: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    # 1. Cailianpress (财联社电报): hk_us + red + watch (看盘)
    cls_headers = {**_HEADERS, "Referer": "https://www.cls.cn/telegraph"}
    for cat, default_cat in (("hk_us", "equities"), ("red", "macro"), ("watch", "equities")):
        params: dict[str, Any] = {
            "appName": "CailianpressWeb",
            "category": cat,
            "lastTime": "",
            "os": "web",
            "rn": 10,
            "sv": "8.7.9",
        }
        params["sign"] = _cls_sign(params)
        try:
            resp = requests.get(
                "https://www.cls.cn/v1/roll/get_roll_list",
                params=params,
                headers=cls_headers,
                timeout=timeout,
            )
            if resp.status_code != 200 or not resp.content:
                continue
            payload = json.loads(resp.content.decode("utf-8", errors="replace"))
            rows = ((payload.get("data") or {}).get("roll_data")) or []
        except Exception:
            rows = []

        for row in rows:
            if not isinstance(row, dict):
                continue
            if str(row.get("is_ad") or "0") == "1":
                continue
            item_id = str(row.get("id") or "").strip()
            raw_title = str(row.get("title") or "").strip()
            raw_content = str(row.get("brief") or row.get("content") or "").strip()
            if "新闻联播" in raw_title or "新闻联播" in raw_content[:120]:
                continue
            title, summary = _split_cn_headline_and_summary(raw_title, raw_content)
            if not title:
                continue
            sig_key = f"cls-{item_id or hashlib.sha1(title.encode('utf-8')).hexdigest()[:10]}"
            if sig_key in seen_ids:
                continue
            seen_ids.add(sig_key)

            ctime = row.get("ctime")
            try:
                dt = datetime.fromtimestamp(int(ctime), tz=UTC)
            except Exception:
                dt = datetime.now(UTC)
            time_bjt = dt.astimezone(BJT).strftime("%Y-%m-%d %H:%M")

            combined = f"{title} {summary}"
            tickers = extract_tickers(combined)
            item_cat = _infer_cn_category(combined, default_cat)
            level = str(row.get("level") or "").upper()
            shareurl = str(row.get("shareurl") or "").strip()
            url = shareurl if shareurl.startswith(("http://", "https://")) else (
                f"https://www.cls.cn/detail/{item_id}" if item_id else ""
            )
            signals.append(
                {
                    "id": sig_key,
                    "source": "财联社电报",
                    "category": item_cat,
                    "source_tier": "T1_5",
                    "direction": _infer_direction(combined),
                    "importance": "高" if (level in ("A", "B") or len(tickers) >= 1 or item_cat == "ashare") else "中",
                    "time_bjt": time_bjt,
                    "title": title,
                    "summary": summary,
                    "why_it_matters": "国内一线中文电报实时快讯，同步追踪 A股·港股看盘与美股宏观动向。",
                    "tickers": tickers,
                    "topics": extract_subtopics(combined, item_cat),
                    "url": url,
                }
            )

    # 2. WallStreetCN (华尔街见闻): us-stock-channel + global-channel + a-stock-channel
    for ch, default_cat in (
        ("us-stock-channel", "equities"),
        ("global-channel", "macro"),
        ("a-stock-channel", "ashare"),
    ):
        try:
            resp = requests.get(
                "https://api-one.wallstcn.com/apiv1/content/lives",
                params={"channel": ch, "limit": 10},
                headers=_HEADERS,
                timeout=timeout,
            )
            if resp.status_code != 200 or not resp.content:
                continue
            payload = json.loads(resp.content.decode("utf-8", errors="replace"))
            rows = ((payload.get("data") or {}).get("items")) or []
        except Exception:
            rows = []

        for row in rows:
            if not isinstance(row, dict):
                continue
            item_id = str(row.get("id") or "").strip()
            score = int(row.get("score") or 0)
            raw_title = str(row.get("title") or "").strip()
            raw_body = str(row.get("content_text") or row.get("content") or "").strip()
            title, summary = _split_cn_headline_and_summary(raw_title, raw_body)
            if not title:
                continue
            combined = f"{title} {summary}"
            tickers = extract_tickers(combined)
            is_ashare_hit = bool(_ASHARE_KEYWORDS_RE.search(combined))
            if ch != "a-stock-channel" and score < 2 and not tickers and not is_ashare_hit:
                continue
            if ch == "a-stock-channel" and score < 1 and not tickers and not is_ashare_hit:
                continue

            sig_key = f"wscn-{item_id or hashlib.sha1(title.encode('utf-8')).hexdigest()[:10]}"
            if sig_key in seen_ids:
                continue
            seen_ids.add(sig_key)

            dtime = row.get("display_time")
            try:
                dt = datetime.fromtimestamp(int(dtime), tz=UTC)
            except Exception:
                dt = datetime.now(UTC)
            time_bjt = dt.astimezone(BJT).strftime("%Y-%m-%d %H:%M")

            item_cat = _infer_cn_category(combined, default_cat)
            uri = str(row.get("uri") or "").strip()
            url = uri if uri.startswith(("http://", "https://")) else (
                f"https://wallstreetcn.com/livenews/{item_id}" if item_id else ""
            )
            signals.append(
                {
                    "id": sig_key,
                    "source": "华尔街见闻",
                    "category": item_cat,
                    "source_tier": "T1_5",
                    "direction": _infer_direction(combined),
                    "importance": "高" if score >= 3 else "中",
                    "time_bjt": time_bjt,
                    "title": title,
                    "summary": summary,
                    "why_it_matters": "全球宏微观实时编译电讯，同步覆盖 A股·港股、美股盘中催化与利率汇率预期。",
                    "tickers": tickers,
                    "topics": extract_subtopics(combined, item_cat),
                    "url": url,
                }
            )

    return signals


EQUATION_NEWS_API_BASE = "https://api.eqn.news/public/v1"
EQUATION_DEFAULT_CHANNELS: tuple[str, ...] = ("main", "tradfi")
_EQN_CACHE_MAX_AGE_SECONDS = 86400
_EQN_US_EQUITIES_TICKERS: frozenset[str] = frozenset(
    {"NVDA", "AAPL", "MSFT", "AMZN", "GOOGL", "META", "TSLA", "AMD", "AVGO", "TSM", "ORCL", "SPY", "QQQ", "SKHY"}
)
_EQN_CRYPTO_RE = re.compile(
    r"(?i)(?:\b(?:btc|eth|sol|usdt|usdc|binance|okx|coinbase|crypto|defi|nft)\b|"
    r"比特币|以太坊|币安|欧易|链上|跨链桥|代币|永续合约|加密|稳定币|空投|交易所)"
)
_EQN_EQUITIES_RE = re.compile(
    r"(?i)(?:\b(?:nasdaq|s&p|dow|stock|shares|earnings|ipo|ceo|board|premarket|wall\s+street)\b|"
    r"股价|盘前|盘后|财报|营收|董事会|美股|纳指|标普|芯片|半导体|英伟达|海力士|软银|微软|苹果|谷歌|亚马逊|特斯拉)"
)


def _extract_eqn_overview(item: dict[str, Any]) -> str:
    ai = item.get("ai")
    if not isinstance(ai, dict):
        return ""
    ov = ai.get("overview")
    if isinstance(ov, dict):
        zh = ov.get("zh")
        if isinstance(zh, dict):
            paragraphs = zh.get("paragraphs")
            if isinstance(paragraphs, list):
                parts: list[str] = []
                for p in paragraphs:
                    if isinstance(p, dict):
                        txt = str(p.get("text") or "").strip()
                    elif isinstance(p, str):
                        txt = p.strip()
                    else:
                        txt = ""
                    if txt:
                        parts.append(txt)
                joined = _clean_text(" ".join(parts), max_len=260)
                if joined:
                    return joined
    summ = ai.get("summary")
    if isinstance(summ, str) and summ.strip():
        return _clean_text(summ, max_len=260)
    if isinstance(summ, dict):
        raw_s = str(summ.get("zh") or summ.get("en") or "").strip()
        if raw_s:
            return _clean_text(raw_s, max_len=260)
    return ""


def _parse_eqn_time_bjt(raw_ts: Any) -> str:
    dt = _parse_rss_datetime(str(raw_ts or "").strip())
    if dt is None:
        dt = datetime.now(UTC)
    return dt.astimezone(BJT).strftime("%Y-%m-%d %H:%M")


def _eqn_why_it_matters(combined: str, category: str) -> str:
    low = combined.lower()
    if any(k in low for k in ("fed", "fomc", "cpi", "pce", "treasury", "tariff", "美联储", "鲍威尔", "降息", "加息", "利率", "通胀", "美债", "国债", "关税", "非农")):
        return "直接牵动美联储利率路径与跨资产风险定价，影响美股估值与加密流动性。"
    if category == "ai" or any(k in low for k in ("nvda", "nvidia", "openai", "anthropic", "deepseek", "llm", "gpu", "hbm", "英伟达", "芯片", "算力", "大模型", "半导体")):
        return "涉及 AI 算力供应链或头部科技催化，直接影响科技权重股与算力链情绪。"
    if category == "crypto":
        return "方程式加密一线突发快讯，易引发短线波动与流动性溢价变化。"
    if category == "equities":
        return "方程式海外传统金融突发快讯，直接映射美股权重与板块盘面预期。"
    return "方程式全球宏观突发快讯，建议结合盘面波动与风险灯号跟踪后续发酵。"


def _normalize_equation_item(item: dict[str, Any], channel: str) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    no = item.get("no")
    if no is None or not str(no).strip():
        return None

    raw_zh = str(item.get("zh") or "").strip()
    raw_en = str(item.get("en") or "").strip()
    is_breaking = raw_zh.startswith("*") or raw_en.startswith("*")
    title = _clean_text((raw_zh or raw_en).lstrip("* ").strip(), max_len=140)
    if not title:
        return None

    overview = _extract_eqn_overview(item)
    en_clean = raw_en.lstrip("* ").strip()
    summary = _clean_text(overview or (en_clean if raw_zh else "") or title, max_len=260)

    coins = [str(c).strip().upper() for c in (item.get("coins") or []) if str(c).strip()]
    combined = f"{title} {raw_en} {summary}"
    extracted = extract_tickers(combined)
    tickers: list[str] = []
    for sym in extracted + coins:
        if sym and sym not in tickers:
            tickers.append(sym)
    tickers = tickers[:8]

    item_channels = [str(c).strip().lower() for c in (item.get("channels") or []) if str(c).strip()]
    if coins or _EQN_CRYPTO_RE.search(combined):
        category = "crypto"
    elif (channel == "tradfi" or "tradfi" in item_channels) and (
        any(t in _EQN_US_EQUITIES_TICKERS for t in tickers) or _EQN_EQUITIES_RE.search(combined)
    ):
        category = "equities"
    else:
        category = _infer_cn_category(combined, "macro")

    direction = _infer_direction(combined)
    importance = "高" if (is_breaking or bool(overview) or len(tickers) >= 1) else "中"

    raw_url = str(item.get("url") or "").strip()
    src_url = str(item.get("source_url") or "").strip()
    url = raw_url if raw_url.startswith(("http://", "https://")) else (
        src_url if src_url.startswith(("http://", "https://")) else ""
    )

    return {
        "id": f"eqn-{str(no).strip()}",
        "source": "方程式快讯",
        "source_tier": "T1_5",
        "category": category,
        "direction": direction,
        "importance": importance,
        "time_bjt": _parse_eqn_time_bjt(item.get("time")),
        "title": title,
        "summary": summary,
        "why_it_matters": _eqn_why_it_matters(combined, category),
        "tickers": tickers,
        "topics": extract_subtopics(combined, category),
        "url": url,
    }


def _load_eqn_cache(cache_p: Path | None) -> dict[str, Any]:
    if cache_p is None or not cache_p.is_file():
        return {"schema_version": 1, "channels": {}}
    try:
        with file_lock(cache_p):
            raw = json.loads(cache_p.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and isinstance(raw.get("channels"), dict):
            return raw
    except Exception:
        pass
    return {"schema_version": 1, "channels": {}}


def collect_equation_news_with_meta(
    *,
    channels: tuple[str, ...] = EQUATION_DEFAULT_CHANNELS,
    limit: int = 20,
    timeout: int = 7,
    cache_path: Path | str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fetch EquationNews public REST channels with ETag / If-None-Match incremental sync."""
    if requests is None:
        return ([], {"changed": False, "new_count": 0, "total_count": 0, "channels": {}})

    cache_p = Path(cache_path) if cache_path is not None else None
    cache_doc = _load_eqn_cache(cache_p)
    old_channels = cache_doc.get("channels") if isinstance(cache_doc.get("channels"), dict) else {}

    prior_all_ids: set[str] = set()
    for entry in old_channels.values():
        if isinstance(entry, dict):
            for sig in entry.get("signals") or []:
                if isinstance(sig, dict) and sig.get("id"):
                    prior_all_ids.add(str(sig["id"]))

    channel_results: dict[str, list[dict[str, Any]]] = {}
    channel_updates: dict[str, dict[str, Any]] = {}
    ch_meta: dict[str, dict[str, Any]] = {}
    any_changed = False
    need_write = False
    now_epoch = time.time()

    for ch in channels:
        ch_key = str(ch or "").strip().lower()
        if not ch_key:
            continue
        old_entry = old_channels.get(ch_key) if isinstance(old_channels.get(ch_key), dict) else {}
        old_etag = str(old_entry.get("etag") or "").strip()
        old_signals = [dict(s) for s in (old_entry.get("signals") or []) if isinstance(s, dict)]
        old_fetched = float(old_entry.get("fetched_epoch") or 0.0)
        old_fp = [(str(s.get("id") or ""), str(s.get("title") or ""), str(s.get("summary") or "")) for s in old_signals]
        old_ids = {str(s.get("id") or "") for s in old_signals if s.get("id")}

        headers = {**_HEADERS, "Accept": "application/json"}
        if old_etag and old_signals:
            headers["If-None-Match"] = old_etag

        url = f"{EQUATION_NEWS_API_BASE}/news"
        params = {"channel": ch_key, "limit": max(1, min(50, int(limit)))}
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=timeout)
            status_code = int(getattr(resp, "status_code", 0) or 0)
            if status_code == 304 and old_signals:
                channel_results[ch_key] = old_signals
                ch_meta[ch_key] = {
                    "status": 304,
                    "etag": old_etag,
                    "changed": False,
                    "new_count": 0,
                    "count": len(old_signals),
                    "from_cache": True,
                }
                continue

            if status_code == 200 and resp.content:
                payload = json.loads(resp.content.decode("utf-8", errors="replace"))
                items = payload.get("items") if isinstance(payload, dict) else None
                if not isinstance(items, list):
                    items = []
                norm_signals: list[dict[str, Any]] = []
                item_nos: list[Any] = []
                seen_ch_ids: set[str] = set()
                for raw_item in items:
                    if isinstance(raw_item, dict) and raw_item.get("no") is not None:
                        item_nos.append(raw_item.get("no"))
                    sig = _normalize_equation_item(raw_item, ch_key)
                    if sig is not None and sig["id"] not in seen_ch_ids:
                        seen_ch_ids.add(sig["id"])
                        norm_signals.append(sig)

                resp_headers = getattr(resp, "headers", None) or {}
                new_etag = str(resp_headers.get("ETag") or resp_headers.get("etag") or "").strip()
                new_fp = [(str(s.get("id") or ""), str(s.get("title") or ""), str(s.get("summary") or "")) for s in norm_signals]
                new_ids = {str(s.get("id") or "") for s in norm_signals if s.get("id")}
                ch_new_count = len(new_ids - old_ids)
                ch_changed = (new_fp != old_fp) or (bool(new_etag) and new_etag != old_etag and bool(norm_signals))

                channel_results[ch_key] = norm_signals
                channel_updates[ch_key] = {
                    "etag": new_etag or old_etag,
                    "fetched_epoch": now_epoch,
                    "item_nos": item_nos,
                    "signals": norm_signals,
                }
                if ch_changed or ch_key not in old_channels:
                    need_write = True
                if ch_changed:
                    any_changed = True
                ch_meta[ch_key] = {
                    "status": 200,
                    "etag": new_etag or old_etag,
                    "changed": ch_changed,
                    "new_count": ch_new_count,
                    "count": len(norm_signals),
                    "from_cache": False,
                }
                continue

            # Unexpected HTTP status: fall back to cache if fresh enough (< 24h)
            if old_signals and (now_epoch - old_fetched) < _EQN_CACHE_MAX_AGE_SECONDS:
                channel_results[ch_key] = old_signals
                ch_meta[ch_key] = {
                    "status": status_code,
                    "etag": old_etag,
                    "changed": False,
                    "new_count": 0,
                    "count": len(old_signals),
                    "from_cache": True,
                }
            else:
                channel_results[ch_key] = []
                ch_meta[ch_key] = {
                    "status": status_code,
                    "etag": old_etag,
                    "changed": False,
                    "new_count": 0,
                    "count": 0,
                    "from_cache": False,
                }
        except Exception:
            if old_signals and (now_epoch - old_fetched) < _EQN_CACHE_MAX_AGE_SECONDS:
                channel_results[ch_key] = old_signals
                ch_meta[ch_key] = {
                    "status": 0,
                    "etag": old_etag,
                    "changed": False,
                    "new_count": 0,
                    "count": len(old_signals),
                    "from_cache": True,
                }
            else:
                channel_results[ch_key] = []
                ch_meta[ch_key] = {
                    "status": 0,
                    "etag": old_etag,
                    "changed": False,
                    "new_count": 0,
                    "count": 0,
                    "from_cache": False,
                }

    # Merge across channels preserving richer summary on cross-channel duplicates
    merged_by_id: dict[str, dict[str, Any]] = {}
    ordered_ids: list[str] = []
    for ch in channels:
        ch_key = str(ch or "").strip().lower()
        for sig in channel_results.get(ch_key, []):
            sid = str(sig.get("id") or "")
            if not sid:
                continue
            if sid not in merged_by_id:
                merged_by_id[sid] = sig
                ordered_ids.append(sid)
            else:
                existing = merged_by_id[sid]
                if len(str(sig.get("summary") or "")) > len(str(existing.get("summary") or "")):
                    merged_by_id[sid] = sig

    merged_signals = [merged_by_id[sid] for sid in ordered_ids]
    total_new_count = len([sid for sid in ordered_ids if sid not in prior_all_ids])

    if cache_p is not None and need_write and channel_updates:
        try:
            cache_p.parent.mkdir(parents=True, exist_ok=True)
            with file_lock(cache_p):
                latest_doc: dict[str, Any] = {"schema_version": 1, "channels": {}}
                if cache_p.is_file():
                    try:
                        loaded = json.loads(cache_p.read_text(encoding="utf-8"))
                        if isinstance(loaded, dict) and isinstance(loaded.get("channels"), dict):
                            latest_doc = loaded
                    except Exception:
                        pass
                latest_channels = dict(latest_doc.get("channels") or {})
                latest_channels.update(channel_updates)
                latest_doc["schema_version"] = 1
                latest_doc["channels"] = latest_channels
                atomic_write_json(cache_p, latest_doc)
        except Exception:
            pass

    return (
        merged_signals,
        {
            "changed": any_changed,
            "new_count": total_new_count,
            "total_count": len(merged_signals),
            "channels": ch_meta,
        },
    )


def collect_equation_news_signals(
    *,
    channels: tuple[str, ...] = EQUATION_DEFAULT_CHANNELS,
    limit: int = 20,
    timeout: int = 7,
    cache_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Collect EquationNews public REST signals using optional ETag cache."""
    signals, _meta = collect_equation_news_with_meta(
        channels=channels,
        limit=limit,
        timeout=timeout,
        cache_path=cache_path,
    )
    return signals


_DEPRECATED_FEED_IDS: frozenset[str] = frozenset({"aidaily"})
_DEPRECATED_SOURCES: frozenset[str] = frozenset({"a.i. 日报", "a.i.日报", "aidaily", "aidaily.fyi"})


def _is_deprecated_feed_item(item: dict[str, Any]) -> bool:
    iid = str(item.get("id") or "").strip().lower()
    src = str(item.get("source") or "").strip().lower()
    url = str(item.get("url") or "").strip().lower()
    if any(iid.startswith(f"rss-{fid}-") for fid in _DEPRECATED_FEED_IDS):
        return True
    if src in _DEPRECATED_SOURCES:
        return True
    if "aidaily.fyi" in url:
        return True
    return False


def collect_curated_rss_signals(*, force_refresh: bool = False, timeout: int = 7) -> list[dict[str, Any]]:
    """Collect curated AI & finance RSS signals plus CN finance wires with a 15-minute disk cache."""
    if not force_refresh and RSS_CACHE_PATH.exists():
        try:
            cached = json.loads(RSS_CACHE_PATH.read_text(encoding="utf-8"))
            age = time.time() - float(cached.get("ts") or 0.0)
            items = cached.get("items") or []
            if cached.get("schema_tag") == "wave1_5" and age <= RSS_CACHE_TTL and isinstance(items, list) and items:
                out_cached: list[dict[str, Any]] = []
                for x in items:
                    if not isinstance(x, dict) or _is_deprecated_feed_item(x):
                        continue
                    row = dict(x)
                    if not isinstance(row.get("topics"), list):
                        row["topics"] = extract_subtopics(f"{row.get('title','')} {row.get('summary','')}", str(row.get("category") or ""))
                    out_cached.append(row)
                if out_cached:
                    return out_cached
        except Exception:
            pass

    specs = list(CURATED_RSS_FEEDS)
    rsshub_base = (os.environ.get("RSSHUB_BASE_URL") or "").strip().rstrip("/")
    if rsshub_base.startswith(("http://", "https://")):
        specs.append(
            {
                "id": "rsshub_36kr",
                "source": "36氪快讯",
                "url": f"{rsshub_base}/36kr/newsflash",
                "html_url": "https://36kr.com/newsflashes",
                "category": "ai",
                "tier": "T2",
                "why": "国内科技与产业快讯补充，映射国产 AI 应用与硬科技情绪。",
            }
        )

    collected: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(_fetch_one_feed, sp, timeout) for sp in specs]
        cn_future = pool.submit(collect_cn_finance_wires, timeout)
        for fut in as_completed(futures):
            try:
                collected.extend(fut.result())
            except Exception:
                pass
        try:
            collected.extend(cn_future.result())
        except Exception:
            pass

    collected = [x for x in collected if isinstance(x, dict) and not _is_deprecated_feed_item(x)]
    if collected:
        try:
            atomic_write_json(RSS_CACHE_PATH, {"schema_tag": "wave1_5", "ts": time.time(), "items": collected})
        except Exception:
            pass
        return collected

    if RSS_CACHE_PATH.exists():
        try:
            cached = json.loads(RSS_CACHE_PATH.read_text(encoding="utf-8"))
            return [dict(x) for x in (cached.get("items") or []) if isinstance(x, dict) and not _is_deprecated_feed_item(x)]
        except Exception:
            pass
    return []


def _fetch_article_candidates_from_feed(spec: dict[str, str], timeout: int = 8) -> list[dict[str, Any]]:
    if requests is None:
        return []
    try:
        resp = requests.get(spec["url"], headers=_HEADERS, timeout=timeout)
        if resp.status_code != 200 or not resp.content:
            return []
        xml_text = resp.content.decode("utf-8", errors="replace")
    except Exception:
        return []

    max_age_days = int(spec.get("max_age_days") or 21)
    raw_entries = _parse_feed_entries(xml_text, max_age_days=max_age_days)
    candidates: list[dict[str, Any]] = []
    for ent in raw_entries[:6]:
        title = _clean_text(ent["title"], max_len=150)
        if not title:
            continue
        body_md = html_to_clean_markdown(ent["long_raw"], max_chars=16000)
        if len(body_md) < 80:
            continue
        summary = _clean_text(ent["short_raw"], max_len=200)
        if not summary or len(summary) < 20:
            summary = _clean_text(body_md.splitlines()[0], max_len=200)
        url = ent["link"]
        time_bjt = ent["dt_utc"].astimezone(BJT).strftime("%Y-%m-%d %H:%M")
        candidates.append(
            {
                "id": f"art-{spec['id']}-{hashlib.sha1((title + url).encode('utf-8')).hexdigest()[:10]}",
                "source": spec["publisher"],
                "publisher": spec["publisher"],
                "category": spec["category"],
                "source_tier": spec["tier"],
                "direction": _infer_direction(f"{title} {summary}"),
                "time_bjt": time_bjt,
                "title": title,
                "summary": summary,
                "why_it_matters": f"{spec['publisher']} 精选深度长文，评估 AI 范式演进与美股/算力产业链映射。",
                "tickers": extract_tickers(f"{title} {summary} {body_md[:800]}"),
                "url": url,
                "body_md": body_md,
            }
        )
    return candidates


def _fetch_cls_depth_candidates(timeout: int = 8) -> list[dict[str, Any]]:
    if requests is None:
        return []
    params: dict[str, Any] = {"appName": "CailianpressWeb", "os": "web", "sv": "8.7.9"}
    params["sign"] = _cls_sign(params)
    try:
        resp = requests.get(
            "https://www.cls.cn/v3/depth/home/assembled/1000",
            params=params,
            headers={**_HEADERS, "Referer": "https://www.cls.cn/depth?id=1000"},
            timeout=timeout,
        )
        if resp.status_code != 200 or not resp.content:
            return []
        data = (json.loads(resp.content.decode("utf-8", errors="replace")).get("data")) or {}
    except Exception:
        return []

    raw_rows = list(data.get("top_article") or []) + list(data.get("depth_list") or [])
    candidates: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for row in raw_rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("is_ad") or "0") == "1":
            continue
        art_id = str(row.get("id") or "").strip()
        if not art_id or art_id in seen_ids:
            continue
        seen_ids.add(art_id)

        title = _clean_text(row.get("title") or "", max_len=140)
        brief = html_to_clean_markdown(str(row.get("brief") or ""), max_chars=4000)
        if not title or len(brief) < 40:
            continue
        ext_url = str(row.get("external_link") or "").strip()
        url = ext_url if ext_url.startswith(("http://", "https://")) else f"https://www.cls.cn/detail/{art_id}"
        try:
            dt = datetime.fromtimestamp(int(row.get("ctime") or 0), tz=UTC)
        except Exception:
            dt = datetime.now(UTC)
        time_bjt = dt.astimezone(BJT).strftime("%Y-%m-%d %H:%M")

        sub_src = _clean_text(row.get("source") or "财联社深度", max_len=40) or "财联社深度"
        summary = _clean_text(brief.replace("\n", " "), max_len=200)
        body_md = (
            f"# {title}\n\n"
            f"- **来源**：{sub_src}（财联社深度）\n"
            f"- **发布时间**：{time_bjt} (BJT)\n"
            f"- **原文链接**：[{url}]({url})\n\n"
            f"---\n\n{brief}\n"
        )
        candidates.append(
            {
                "id": f"art-cls-{art_id}",
                "source": "财联社深度",
                "publisher": "财联社深度",
                "category": _infer_cn_category(f"{title} {brief}", "equities"),
                "source_tier": "T1_5",
                "direction": _infer_direction(f"{title} {brief}"),
                "time_bjt": time_bjt,
                "title": title,
                "summary": summary,
                "why_it_matters": "财联社深度板块精华研判，跟踪中国资本市场、产业政策与科技产业链动向。",
                "tickers": extract_tickers(f"{title} {brief}"),
                "url": url,
                "body_md": body_md,
            }
        )
    return candidates


def _yaml_quote(value: str) -> str:
    clean = re.sub(r"[\r\n]+", " ", str(value or "")).strip()
    clean = clean.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{clean}"'


def _write_curated_article_files(articles: list[dict[str, Any]], articles_dir: Path = ARTICLES_DIR) -> None:
    articles_dir.mkdir(parents=True, exist_ok=True)
    for old_file in articles_dir.glob("feed_*.md"):
        try:
            old_file.unlink()
        except OSError:
            pass

    for idx, art in enumerate(articles, 1):
        title = str(art.get("title") or "深度研报").strip()
        summary = str(art.get("summary") or title).strip()
        publisher = str(art.get("publisher") or "AI与金融研报").strip()
        url = str(art.get("url") or "").strip()
        score = int(art.get("score") or 0)
        body_md = str(art.get("body_md") or summary).strip()

        slug = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff_-]+", "_", title).strip("_")[:28] or f"doc_{idx}"
        h_sfx = hashlib.sha1((title + url).encode("utf-8")).hexdigest()[:6]
        fname = f"feed_{slug}_{h_sfx}.md"
        art["filename"] = fname

        content = (
            "---\n"
            f"title: {_yaml_quote(title)}\n"
            f"summary: {_yaml_quote(summary)}\n"
            f"publisher: {_yaml_quote(publisher)}\n"
            f"url: {_yaml_quote(url)}\n"
            f"score: {score}\n"
            "---\n\n"
            f"{body_md}\n"
        )
        (articles_dir / fname).write_text(content, encoding="utf-8")


def collect_curated_articles(
    *,
    force_refresh: bool = False,
    timeout: int = 8,
    articles_dir: Path = ARTICLES_DIR,
    cache_path: Path = ARTICLE_CACHE_PATH,
) -> list[dict[str, Any]]:
    """Fetch, filter, score and persist top 10 curated AI/finance articles as articles/feed_*.md."""
    if not force_refresh and cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            age = time.time() - float(cached.get("ts") or 0.0)
            arts = cached.get("articles") or []
            if cached.get("schema_tag") == "wave1_5" and age <= ARTICLE_CACHE_TTL and isinstance(arts, list) and arts:
                if not list(articles_dir.glob("feed_*.md")):
                    _write_curated_article_files(arts, articles_dir=articles_dir)
                return [dict(x) for x in arts if isinstance(x, dict)]
        except Exception:
            pass

    from backend.kazike_engine import cluster_and_dedup, prefilter_financial, score_five_axes

    raw_candidates: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = [pool.submit(_fetch_article_candidates_from_feed, sp, timeout) for sp in CURATED_ARTICLE_FEEDS]
        cls_fut = pool.submit(_fetch_cls_depth_candidates, timeout)
        for fut in as_completed(futs):
            try:
                raw_candidates.extend(fut.result())
            except Exception:
                pass
        try:
            raw_candidates.extend(cls_fut.result())
        except Exception:
            pass

    scored_candidates: list[dict[str, Any]] = []
    for cand in raw_candidates:
        verdict, _reason = prefilter_financial(cand)
        if verdict == "BLOCK":
            continue
        sc = score_five_axes(cand, persist_receipt=False)
        cand_copy = dict(cand)
        cand_copy["attention_score"] = int(sc["score"])
        cand_copy["score"] = int(sc["score"])
        scored_candidates.append(cand_copy)

    if scored_candidates:
        deduped = cluster_and_dedup(scored_candidates)
        deduped.sort(
            key=lambda x: (int(x.get("attention_score") or 0), str(x.get("time_bjt") or "")),
            reverse=True,
        )
        top_articles = [
            {
                "filename": "",
                "title": str(it.get("title") or ""),
                "summary": str(it.get("summary") or ""),
                "publisher": str(it.get("publisher") or it.get("source") or "AI与金融研报"),
                "url": str(it.get("url") or ""),
                "score": int(it.get("attention_score") or 0),
                "time_bjt": str(it.get("time_bjt") or ""),
                "body_md": str(it.get("body_md") or ""),
            }
            for it in deduped[:10]
        ]
        _write_curated_article_files(top_articles, articles_dir=articles_dir)
        try:
            atomic_write_json(cache_path, {"schema_tag": "wave1_5", "ts": time.time(), "articles": top_articles})
        except Exception:
            pass
        return top_articles

    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            arts = cached.get("articles") or []
            if isinstance(arts, list) and arts:
                return [dict(x) for x in arts if isinstance(x, dict)]
        except Exception:
            pass
    return []


def build_opml_export(output_path: Path | str | None = None) -> str:
    """Build valid OPML 2.0 XML containing all curated signal & longform feeds and write to dist/."""
    out_path = Path(output_path) if output_path else DEFAULT_OPML_PATH

    opml = ET.Element("opml", {"version": "2.0"})
    head = ET.SubElement(opml, "head")
    ET.SubElement(head, "title").text = "MacroCockpit 精选金融与 AI 订阅源"
    ET.SubElement(head, "dateCreated").text = datetime.now(UTC).strftime("%a, %d %b %Y %H:%M:%S GMT")
    body = ET.SubElement(opml, "body")

    groups: dict[str, list[dict[str, str]]] = {
        "宏观与央行": [],
        "美股与金融市场": [],
        "A股·港股": [],
        "AI 官方与前沿研究": [],
        "数字资产": [],
        "深度长文与周刊": [],
    }
    cat_group_map = {
        "macro": "宏观与央行",
        "equities": "美股与金融市场",
        "ashare": "A股·港股",
        "ai": "AI 官方与前沿研究",
        "crypto": "数字资产",
    }
    seen_urls: set[str] = set()
    for sp in CURATED_RSS_FEEDS:
        u = sp["url"]
        if u in seen_urls:
            continue
        seen_urls.add(u)
        grp = cat_group_map.get(sp["category"], "美股与金融市场")
        groups[grp].append(
            {
                "text": sp["source"],
                "title": sp["source"],
                "type": "rss",
                "xmlUrl": u,
                "htmlUrl": sp.get("html_url") or u,
            }
        )
    for sp in CURATED_ARTICLE_FEEDS:
        u = sp["url"]
        if u in seen_urls:
            continue
        seen_urls.add(u)
        groups["深度长文与周刊"].append(
            {
                "text": sp["publisher"],
                "title": sp["publisher"],
                "type": "rss",
                "xmlUrl": u,
                "htmlUrl": sp.get("html_url") or u,
            }
        )

    for folder_name, items in groups.items():
        if not items:
            continue
        folder = ET.SubElement(body, "outline", {"text": folder_name, "title": folder_name})
        for it in items:
            ET.SubElement(folder, "outline", it)

    if hasattr(ET, "indent"):
        ET.indent(opml, space="  ")
    xml_bytes = ET.tostring(opml, encoding="utf-8", xml_declaration=True)
    xml_str = xml_bytes.decode("utf-8") + "\n"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp.write_text(xml_str, encoding="utf-8")
    os.replace(tmp, out_path)
    return xml_str


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    opml_str = build_opml_export()
    wires = collect_cn_finance_wires()
    arts = collect_curated_articles(force_refresh=True)
    items = collect_curated_rss_signals(force_refresh=True)
    by_src: dict[str, int] = {}
    for it in items:
        by_src[it["source"]] = by_src.get(it["source"], 0) + 1
    print(f"OPML bytes={len(opml_str)} | CN wires={len(wires)} | Articles={len(arts)} | RSS+Wires={len(items)}: {by_src}")
