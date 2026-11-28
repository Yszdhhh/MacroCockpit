"""Curated official AI, global macro/equity RSS feeds, CN finance wires, curated articles & OPML exporter."""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import socket
import sys
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:
    requests = None  # type: ignore[assignment]

_BACKEND_DIR = Path(__file__).resolve().parent
if str(_BACKEND_DIR.parent) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR.parent))

from backend.guardrails import (
    http_get_no_redirect,
    is_safe_public_url,
    resolve_safe_public_ips,
    scrub_forbidden,
    strip_emojis as strip_emojis,
)
from backend.runtime import BJT, COCKPIT_ROOT, DATA_DIR, UTC, atomic_write_json, file_lock

from backend.signal_text import (
    WIRE_DATELINE_RE,
    clean_feed_text,
    decode_http_text,
    dedup_summary as _dedup_summary,
    eqn_why_it_matters,
    extract_subtopics,
    extract_tickers,
    html_to_clean_markdown,
    infer_signal_direction,
    norm_dup_key,
    parse_rss_datetime,
    strip_xml_ns,
    yaml_quote,
)

RSS_CACHE_PATH = DATA_DIR / "rss_cache.json"
RSS_CACHE_TTL = 900  # 15 minutes
ARTICLE_CACHE_PATH = DATA_DIR / "curated_articles_cache.json"
ARTICLE_CACHE_TTL = 1800  # 30 minutes
FULLTEXT_CACHE_PATH = DATA_DIR / "fulltext_cache.json"
FULLTEXT_CACHE_TTL = 86400  # 24 hours
FULLTEXT_SKIP_CACHE_TTL = 21600  # 6 hours
FULLTEXT_CACHE_MAX_ITEMS = 300
ARTICLES_DIR = COCKPIT_ROOT / "articles"
DEFAULT_OPML_PATH = COCKPIT_ROOT / "dist" / "macro_cockpit_feeds.opml"
_FULLTEXT_CACHE_LOCK = threading.Lock()

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Encoding": "gzip, deflate",
}

_FULLTEXT_SKIP_DOMAINS: tuple[str, ...] = (
    "mp.weixin.qq.com",
    "ft.com",
    "wsj.com",
    "bloomberg.com",
    "economist.com",
    "barrons.com",
    "nytimes.com",
    "theinformation.com",
    "seekingalpha.com",
    "reuters.com",
)

_BOT_CHALLENGE_TOKENS: tuple[str, ...] = (
    "just a moment",
    "attention required",
    "access denied",
    "verify you are human",
    "please enable js",
    "enable javascript",
    "captcha",
    "cloudflare",
    "环境异常",
    "当前环境异常",
    "完成验证",
)

_STRONG_CHALLENGE_TOKENS: tuple[str, ...] = (
    "just a moment",
    "attention required",
    "verify you are human",
    "please enable js",
    "enable javascript",
    "access denied",
    "环境异常",
    "当前环境异常",
    "完成验证",
)

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
        "tier": "T2",
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

GURU_ARTICLE_FEEDS: tuple[dict[str, str], ...] = (
    {
        "id": "awealthofcs",
        "publisher": "Ben Carlson · 常识财富",
        "author": "Ben Carlson",
        "author_role": "资产配置与行为金融",
        "url": "https://awealthofcommonsense.com/feed/",
        "html_url": "https://awealthofcommonsense.com/",
        "category": "equities",
        "tier": "T1_5",
        "max_age_days": "30",
        "background": "Ritholtz 财富管理机构董事总经理 Ben Carlson 专栏，专注美股估值周期、长期资产配置与投资者行为偏差分析。",
    },
    {
        "id": "pragcap",
        "publisher": "Cullen Roche · 宏观货币",
        "author": "Cullen Roche",
        "author_role": "宏观货币与资产配置",
        "url": "https://www.pragcap.com/feed/",
        "html_url": "https://www.pragcap.com/",
        "category": "macro",
        "tier": "T1_5",
        "max_age_days": "30",
        "background": "Discipline Funds 创始人 Cullen Roche 主笔，擅长从货币银行机制、国债供给与美联储资产负债表拆解宏观流动性。",
    },
    {
        "id": "kylascanlon",
        "publisher": "Kyla Scanlon · 宏观叙事",
        "author": "Kyla Scanlon",
        "author_role": "宏观叙事与市场情绪",
        "url": "https://kylascanlon.substack.com/feed",
        "html_url": "https://kylascanlon.substack.com/",
        "category": "macro",
        "tier": "T1_5",
        "max_age_days": "45",
        "background": "知名财经作者 Kyla Scanlon 专栏，聚焦美国通胀体感、劳动力市场与零售/机构情绪叙事转向。",
    },
    {
        "id": "cryptohayes",
        "publisher": "Arthur Hayes · 加密宏观",
        "author": "Arthur Hayes",
        "author_role": "加密宏观与美元流动性",
        "url": "https://cryptohayes.substack.com/feed",
        "html_url": "https://cryptohayes.substack.com/",
        "category": "crypto",
        "tier": "T1",
        "max_age_days": "60",
        "background": "BitMEX 联合创始人 Arthur Hayes 长文专栏，核心围绕美元财政赤字、美债流动性投放与比特币周期定价。",
    },
    {
        "id": "semianalysis",
        "publisher": "SemiAnalysis · 半导体深研",
        "author": "Dylan Patel / SemiAnalysis",
        "author_role": "AI算力与半导体供应链",
        "url": "https://www.semianalysis.com/feed",
        "html_url": "https://www.semianalysis.com/",
        "category": "ai",
        "tier": "T1",
        "max_age_days": "45",
        "background": "全球顶级半导体与 AI 数据中心产业研究机构，深度追踪英伟达、台积电、HBM 存储与云厂商算力资本开支。",
    },
    {
        "id": "fabricated",
        "publisher": "Fabricated Knowledge · 芯片投研",
        "author": "Doug O'Laughlin",
        "author_role": "半导体与晶圆制造投研",
        "url": "https://www.fabricatedknowledge.com/feed",
        "html_url": "https://www.fabricatedknowledge.com/",
        "category": "ai",
        "tier": "T1_5",
        "max_age_days": "60",
        "background": "半导体买方背景研究员 Doug O'Laughlin 专栏，专注晶圆代工、先进封装、光刻设备与芯片周期供需。",
    },
    {
        "id": "stratechery",
        "publisher": "Ben Thompson · Stratechery",
        "author": "Ben Thompson",
        "author_role": "科技战略与平台商业",
        "url": "https://stratechery.com/feed/",
        "html_url": "https://stratechery.com/",
        "category": "equities",
        "tier": "T1",
        "max_age_days": "30",
        "background": "硅谷最具影响力的科技战略分析师 Ben Thompson 主笔，拆解美股七巨头商业模式、AI 分发壁垒与反垄断格局。",
    },
    {
        "id": "dwarkesh",
        "publisher": "Dwarkesh Patel · 深度访谈",
        "author": "Dwarkesh Patel",
        "author_role": "AI前沿与深度访谈",
        "url": "https://www.dwarkeshpatel.com/feed",
        "html_url": "https://www.dwarkeshpatel.com/",
        "category": "ai",
        "tier": "T1_5",
        "max_age_days": "90",
        "background": "顶级技术播客与长文作者 Dwarkesh Patel 专栏，长期对话一线大模型实验室掌舵人，追踪 Scaling Law 与算力边界。",
    },
    {
        "id": "notboring",
        "publisher": "Packy McCormick · Not Boring",
        "author": "Packy McCormick",
        "author_role": "科技趋势与产业商业",
        "url": "https://www.notboring.co/feed",
        "html_url": "https://www.notboring.co/",
        "category": "equities",
        "tier": "T1_5",
        "max_age_days": "45",
        "background": "Not Boring 主理人 Packy McCormick 深度商业长文，覆盖前沿软件、国防科技、能源基建与美股成长叙事。",
    },
    {
        "id": "doomberg",
        "publisher": "Doomberg · 能源与大宗",
        "author": "Doomberg",
        "author_role": "全球能源与大宗商品",
        "url": "https://doomberg.substack.com/feed",
        "html_url": "https://doomberg.substack.com/",
        "category": "macro",
        "tier": "T1_5",
        "max_age_days": "45",
        "background": "高人气能源与大宗商品评论团队 Doomberg，聚焦原油、天然气、核电与 AI 数据中心电力瓶颈。",
    },
    {
        "id": "vitalik",
        "publisher": "Vitalik Buterin · 以太坊研究",
        "author": "Vitalik Buterin",
        "author_role": "以太坊协议与链上架构",
        "url": "https://vitalik.eth.limo/feed.xml",
        "html_url": "https://vitalik.eth.limo/",
        "category": "crypto",
        "tier": "T1",
        "max_age_days": "120",
        "background": "以太坊联合创始人 Vitalik Buterin 官方研究博客，发布底层扩容、ZK 证明、链上治理与加密安全一线路线图。",
    },
    {
        "id": "paulgraham",
        "publisher": "Paul Graham · 商业随笔",
        "author": "Paul Graham",
        "author_role": "科技创业与商业随笔",
        "url": "http://www.aaronsw.com/2002/feeds/pgessays.rss",
        "html_url": "https://paulgraham.com/articles.html",
        "category": "ai",
        "tier": "T1_5",
        "max_age_days": "180",
        "background": "Y Combinator 联合创始人 Paul Graham 经典随笔，揭示硅谷技术代际切换、创业组织与长期复利逻辑。",
    },
)

_EQN_ROUTINE_LISTING_RE = re.compile(
    r"(?i)(?:will\s+list\b|market\s+support\s+for|新增.*交易支持|交易支持公告|"
    r"新增.*(?:交易对|币对)|上线.*(?:永续合约|现货)|暂停.*充提|恢复.*充提|支持.*网络升级)"
)


_scrub_forbidden = scrub_forbidden
_norm_dup_key = norm_dup_key


_ASHARE_KEYWORDS_RE = re.compile(
    r"A股|沪指|深成指|创业板|科创板|科创50|沪深300|中证500|上证|深证|北交所|恒指|恒生|恒生科技|恒科|HSTECH|港股|港股通|"
    r"证监会|上交所|深交所|港交所|北向|南向|两融|融资余额|融资保证金|融券|平准基金|汇金|国常会|"
    r"(?<![A-Za-z0-9])\d{4,5}\.HK(?![A-Za-z0-9])|(?<![A-Za-z0-9])HK\d{4,5}(?![A-Za-z0-9])|(?<![A-Za-z0-9])\d{6}\.(?:SZ|SS|SH|BJ)(?![A-Za-z0-9])|"
    r"比亚迪|华为|鸿蒙|昇腾|宁德时代|茅台|中芯|寒武纪|腾讯|阿里|美团|小米"
)


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
    nodes = [elem for elem in root.iter() if strip_xml_ns(elem.tag) in ("item", "entry")]
    for entry in nodes:
        title = ""
        link = ""
        pub_raw = ""
        bodies: dict[str, str] = {}
        for child in entry:
            name = strip_xml_ns(child.tag)
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

        dt = parse_rss_datetime(pub_raw) or now
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
        clean_title = clean_feed_text(ent["title"], max_len=140)
        if not clean_title:
            continue
        raw_summary = clean_feed_text(ent["short_raw"], max_len=240)
        clean_summary = _dedup_summary(clean_title, raw_summary)
        time_bjt = ent["dt_utc"].astimezone(BJT).strftime("%Y-%m-%d %H:%M")

        combined_text = f"{clean_title} {raw_summary}".strip()
        link = ent["link"]
        sig_id = f"rss-{spec_id}-{hashlib.sha1((clean_title + link).encode('utf-8')).hexdigest()[:10]}"
        items_out.append(
            {
                "id": sig_id,
                "source": source_name,
                "category": category,
                "source_tier": tier,
                "direction": infer_signal_direction(combined_text),
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
        xml_text = decode_http_text(resp)
        return parse_feed_xml(xml_text, spec, max_items=5)
    except Exception:
        return []


def _cls_sign(params: dict[str, Any]) -> str:
    qs = urllib.parse.urlencode(sorted((str(k), str(v)) for k, v in params.items()))
    sha1_hex = hashlib.sha1(qs.encode("utf-8")).hexdigest()
    return hashlib.md5(sha1_hex.encode("utf-8")).hexdigest()


def _split_cn_headline_and_summary(title: str, body: str) -> tuple[str, str]:
    t = clean_feed_text(title, max_len=140)
    b = clean_feed_text(body, max_len=260)
    if not t and b:
        m = re.match(r"^【([^】]{4,80})】\s*(.*)$", b)
        if m:
            t = m.group(1).strip()
            b = m.group(2).strip()
        else:
            b_nodate = WIRE_DATELINE_RE.sub("", b).strip()
            first_sent = re.split(r"[。！？\n]", b_nodate or b, maxsplit=1)[0].strip()
            t = first_sent[:72] if first_sent else b[:72]
    stripped_t = WIRE_DATELINE_RE.sub("", t).strip()
    if len(stripped_t) >= 8:
        t = stripped_t
    return (t, _dedup_summary(t, b))


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

            raw_combined = f"{title} {clean_feed_text(raw_content, max_len=260)}".strip()
            tickers = extract_tickers(raw_combined)
            item_cat = _infer_cn_category(raw_combined, default_cat)
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
                    "direction": infer_signal_direction(raw_combined),
                    "importance": "高" if (level in ("A", "B") or len(tickers) >= 1 or item_cat == "ashare") else "中",
                    "time_bjt": time_bjt,
                    "title": title,
                    "summary": summary,
                    "why_it_matters": "国内一线中文电报实时快讯，同步追踪 A股·港股看盘与美股宏观动向。",
                    "tickers": tickers,
                    "topics": extract_subtopics(raw_combined, item_cat),
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
            combined = f"{title} {clean_feed_text(raw_body, max_len=260)}".strip()
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
                    "direction": infer_signal_direction(combined),
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
                joined = clean_feed_text(" ".join(parts), max_len=260)
                if joined:
                    return joined
    summ = ai.get("summary")
    if isinstance(summ, str) and summ.strip():
        return clean_feed_text(summ, max_len=260)
    if isinstance(summ, dict):
        raw_s = str(summ.get("zh") or summ.get("en") or "").strip()
        if raw_s:
            return clean_feed_text(raw_s, max_len=260)
    return ""


def _parse_eqn_time_bjt(raw_ts: Any) -> str:
    dt = parse_rss_datetime(str(raw_ts or "").strip())
    if dt is None:
        dt = datetime.now(UTC)
    return dt.astimezone(BJT).strftime("%Y-%m-%d %H:%M")


def _normalize_equation_item(item: dict[str, Any], channel: str) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    no = item.get("no")
    if no is None or not str(no).strip():
        return None

    raw_zh = str(item.get("zh") or "").strip()
    raw_en = str(item.get("en") or "").strip()
    is_breaking = raw_zh.startswith("*") or raw_en.startswith("*")
    title = clean_feed_text((raw_zh or raw_en).lstrip("* ").strip(), max_len=140)
    if not title:
        return None
    if _EQN_ROUTINE_LISTING_RE.search(f"{title} {raw_en}"):
        return None

    overview = _extract_eqn_overview(item)
    en_clean = raw_en.lstrip("* ").strip()
    raw_summary = clean_feed_text(overview or (en_clean if not raw_zh else ""), max_len=260)
    summary = _dedup_summary(title, raw_summary)

    coins = [str(c).strip().upper() for c in (item.get("coins") or []) if str(c).strip()]
    combined = f"{title} {raw_en} {raw_summary}".strip()
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

    direction = infer_signal_direction(combined)
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
        "why_it_matters": eqn_why_it_matters(combined, category),
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
        xml_text = decode_http_text(resp)
    except Exception:
        return []

    max_age_days = int(spec.get("max_age_days") or 21)
    raw_entries = _parse_feed_entries(xml_text, max_age_days=max_age_days)
    candidates: list[dict[str, Any]] = []
    for ent in raw_entries[:6]:
        title = clean_feed_text(ent["title"], max_len=150)
        if not title:
            continue
        body_md = html_to_clean_markdown(ent["long_raw"], max_chars=16000)
        if len(body_md) < 80:
            continue
        summary = clean_feed_text(ent["short_raw"], max_len=200)
        if not summary or len(summary) < 20:
            summary = clean_feed_text(body_md.splitlines()[0], max_len=200)
        url = ent["link"]
        time_bjt = ent["dt_utc"].astimezone(BJT).strftime("%Y-%m-%d %H:%M")
        candidates.append(
            {
                "id": f"art-{spec['id']}-{hashlib.sha1((title + url).encode('utf-8')).hexdigest()[:10]}",
                "source": spec["publisher"],
                "publisher": spec["publisher"],
                "category": spec["category"],
                "source_tier": spec["tier"],
                "direction": infer_signal_direction(f"{title} {summary}"),
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


def _load_fulltext_cache(cache_path: Path | None = None) -> dict[str, dict[str, Any]]:
    path = cache_path or FULLTEXT_CACHE_PATH
    with _FULLTEXT_CACHE_LOCK:
        if not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            items = data.get("items") if isinstance(data, dict) else None
            return dict(items) if isinstance(items, dict) else {}
        except Exception:
            return {}


def _get_cached_fulltext(
    cache_key: str,
    *,
    cache_path: Path | None = None,
) -> tuple[str, str] | None:
    if not cache_key:
        return None
    items = _load_fulltext_cache(cache_path)
    entry = items.get(cache_key)
    if not isinstance(entry, dict):
        return None
    ts = float(entry.get("ts") or 0.0)
    status = str(entry.get("status") or "")
    text = str(entry.get("text") or "")
    ttl = FULLTEXT_SKIP_CACHE_TTL if status == "skipped_paywall" else FULLTEXT_CACHE_TTL
    if (time.time() - ts) > ttl:
        return None
    if status == "skipped_paywall":
        return ("", status)
    if len(text) >= 200 and status:
        return (text, status)
    return None


def _save_cached_fulltext(
    cache_key: str,
    *,
    text: str,
    status: str,
    cache_path: Path | None = None,
) -> None:
    if not cache_key:
        return
    if status != "skipped_paywall" and len(text) < 200:
        return
    path = cache_path or FULLTEXT_CACHE_PATH
    with _FULLTEXT_CACHE_LOCK:
        items: dict[str, dict[str, Any]] = {}
        if path.is_file():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, dict) and isinstance(raw.get("items"), dict):
                    items = dict(raw["items"])
            except Exception:
                items = {}
        items[cache_key] = {
            "ts": time.time(),
            "status": status,
            "text": text if status != "skipped_paywall" else "",
        }
        if len(items) > FULLTEXT_CACHE_MAX_ITEMS:
            ordered = sorted(
                items.items(),
                key=lambda kv: float((kv[1] or {}).get("ts") or 0.0),
                reverse=True,
            )[:FULLTEXT_CACHE_MAX_ITEMS]
            items = dict(ordered)
        try:
            atomic_write_json(path, {"schema_version": 1, "items": items})
        except Exception:
            pass


def _is_skipped_paywall_domain(url: str) -> bool:
    try:
        host = (urllib.parse.urlsplit(str(url or "")).hostname or "").strip().lower()
    except Exception:
        return False
    if not host:
        return False
    return any(host == dom or host.endswith(f".{dom}") for dom in _FULLTEXT_SKIP_DOMAINS)


def _looks_like_bot_challenge(text: str) -> bool:
    low = re.sub(r"\s+", " ", str(text or "").strip().lower())
    if not low:
        return False
    if len(low) < 1200 and any(tok in low for tok in _BOT_CHALLENGE_TOKENS):
        return True
    head = low[:300]
    return any(tok in head for tok in _STRONG_CHALLENGE_TOKENS)


def _find_balanced_container_end(html_str: str, open_end: int, tag_name: str) -> int:
    tag_re = re.compile(rf"<(/?){tag_name}\b[^>]*>", re.IGNORECASE)
    depth = 1
    for m in tag_re.finditer(html_str, open_end):
        if m.group(1) == "/":
            depth -= 1
            if depth == 0:
                return m.start()
        else:
            depth += 1
    return -1


def _extract_main_html_fragment(raw_html: str) -> str:
    """Extract primary article HTML container cleanly without external HTML parser deps."""
    cleaned = re.sub(
        r"<(script|style|noscript|svg|iframe|header|nav|footer|aside|form)\b[^>]*>.*?</\1>",
        " ",
        str(raw_html or ""),
        flags=re.IGNORECASE | re.DOTALL,
    )

    def _plain_len(frag: str) -> int:
        txt = re.sub(r"<[^>]+>", " ", frag)
        return len(re.sub(r"\s+", " ", html.unescape(txt)).strip())

    for tag in ("article", "main"):
        matches = re.findall(rf"<{tag}\b[^>]*>(.*?)</{tag}>", cleaned, flags=re.IGNORECASE | re.DOTALL)
        if matches:
            best = max(matches, key=_plain_len)
            if _plain_len(best) >= 250:
                return best

    container_re = re.compile(
        r"<(div|section)\b[^>]*(?:class|id)\s*=\s*[\"'][^\"']*"
        r"(?:article[-_]?content|post[-_]?content|entry[-_]?content|story[-_]?body|markdown[-_]?body|\bprose\b)"
        r"[^\"']*[\"'][^>]*>",
        re.IGNORECASE,
    )
    best_div = ""
    best_len = 0
    for m in container_re.finditer(cleaned):
        end_idx = _find_balanced_container_end(cleaned, m.end(), m.group(1))
        if end_idx > m.end():
            frag = cleaned[m.end() : end_idx]
            plen = _plain_len(frag)
            if plen > best_len:
                best_len = plen
                best_div = frag
    if best_len >= 250:
        return best_div

    body_m = re.search(r"<body\b[^>]*>(.*?)</body>", cleaned, flags=re.IGNORECASE | re.DOTALL)
    body_scope = body_m.group(1) if body_m else cleaned
    blocks: list[str] = []
    for bm in re.finditer(
        r"<(?:p|h[1-4]|li|blockquote)\b[^>]*>.*?</(?:p|h[1-4]|li|blockquote)>",
        body_scope,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        frag = bm.group(0)
        if _plain_len(frag) >= 25:
            blocks.append(frag)
    return "\n".join(blocks)


def fetch_url_fulltext_markdown(
    url: str,
    *,
    timeout: int = 10,
    max_chars: int = 16000,
    allow_jina_fallback: bool = True,
    use_cache: bool = True,
    cache_path: Path | None = None,
) -> tuple[str, str]:
    """Safely fetch and extract clean Markdown fulltext from X or public web articles."""
    target_url = str(url or "").strip()
    safe_ok, initial_ips = resolve_safe_public_ips(target_url, socket_mod=socket)
    if not safe_ok:
        return ("", "blocked_ssrf")
    if _is_skipped_paywall_domain(target_url):
        return ("", "skipped_paywall")
    if requests is None:
        return ("", "failed")

    cache_key = f"url:{target_url}"
    if use_cache:
        hit = _get_cached_fulltext(cache_key, cache_path=cache_path)
        if hit is not None:
            return (hit[0][:max_chars], hit[1])

    # Route 1: X / Twitter status URLs via api.fxtwitter.com (no auto-redirects)
    m_x = re.match(r"^https?://(?:www\.)?(?:x|twitter)\.com/([^/]+)/status/(\d+)", target_url, re.I)
    if m_x:
        author, tid = m_x.group(1), m_x.group(2)
        fx_url = f"https://api.fxtwitter.com/{urllib.parse.quote(author)}/status/{tid}"
        fx_ok, fx_ips = resolve_safe_public_ips(fx_url, socket_mod=socket)
        if not fx_ok:
            return ("", "blocked_ssrf")
        try:
            resp = http_get_no_redirect(
                fx_url,
                headers={**_HEADERS, "Accept": "application/json"},
                timeout=timeout,
                pinned_ip=fx_ips[0] if fx_ips else None,
                requests_mod=requests,
            )
            if resp.status_code in (301, 302, 303, 307, 308):
                return ("", "failed")
            if resp.status_code == 200 and resp.content:
                fx = json.loads(decode_http_text(resp))
                tw = (fx or {}).get("tweet") or {}
                art = tw.get("article") or {}
                parts: list[str] = []
                art_title = str(art.get("title") or "").strip() if isinstance(art, dict) else ""
                if isinstance(art, dict) and art:
                    blocks = (art.get("content") or {}).get("blocks") or []
                    if isinstance(blocks, list):
                        parts = [
                            str(b.get("text") or "").strip()
                            for b in blocks
                            if isinstance(b, dict) and str(b.get("text") or "").strip()
                        ]
                    if not parts and art.get("preview_text"):
                        parts = [str(art.get("preview_text") or "").strip()]
                body_txt = "\n\n".join(parts) if parts else str(tw.get("text") or "").strip()
                if art_title and body_txt and not body_txt.startswith(art_title):
                    body_txt = f"# {art_title}\n\n{body_txt}"
                body_clean = scrub_forbidden(re.sub(r"\n{3,}", "\n\n", body_txt).strip(), preserve_newlines=True)
                if len(body_clean) >= 200 and not _looks_like_bot_challenge(body_clean):
                    body_out = body_clean[:max_chars]
                    if use_cache:
                        _save_cached_fulltext(cache_key, text=body_out, status="fxtwitter", cache_path=cache_path)
                    return (body_out, "fxtwitter")
        except Exception:
            pass
        return ("", "failed")

    # Route 2: Direct HTML fetch & extraction (manual hop-by-hop redirect + pinned DNS validation)
    hit_bot_challenge = False
    try:
        cur_url = target_url
        resp = None
        req_headers = {**_HEADERS, "Accept": "text/html,application/xhtml+xml"}
        for _hop in range(5):
            if _hop == 0:
                safe_ok, resolved_ips = True, initial_ips
            else:
                safe_ok, resolved_ips = resolve_safe_public_ips(cur_url, socket_mod=socket)
            if not safe_ok:
                return ("", "blocked_ssrf")
            if _is_skipped_paywall_domain(cur_url):
                if use_cache:
                    _save_cached_fulltext(cache_key, text="", status="skipped_paywall", cache_path=cache_path)
                return ("", "skipped_paywall")
            resp = http_get_no_redirect(
                cur_url,
                headers=req_headers,
                timeout=timeout,
                pinned_ip=resolved_ips[0] if resolved_ips else None,
                requests_mod=requests,
            )
            if resp.status_code in (301, 302, 303, 307, 308):
                loc = str((getattr(resp, "headers", None) or {}).get("Location") or (getattr(resp, "headers", None) or {}).get("location") or "").strip()
                if not loc:
                    break
                cur_url = urllib.parse.urljoin(cur_url, loc)
                continue
            break
        if resp is None:
            return ("", "failed")
        final_url = str(getattr(resp, "url", "") or cur_url)
        if final_url and final_url != cur_url and not is_safe_public_url(final_url, socket_mod=socket):
            return ("", "blocked_ssrf")
        if final_url and _is_skipped_paywall_domain(final_url):
            if use_cache:
                _save_cached_fulltext(cache_key, text="", status="skipped_paywall", cache_path=cache_path)
            return ("", "skipped_paywall")
        if resp.status_code == 200 and resp.content and len(resp.content) <= 2_500_000:
            raw_html = decode_http_text(resp)
            m_title = re.search(r"<title[^>]*>(.*?)</title>", raw_html, re.IGNORECASE | re.DOTALL)
            page_title = clean_feed_text(m_title.group(1), max_len=200) if m_title else ""
            if _looks_like_bot_challenge(page_title) or _looks_like_bot_challenge(raw_html[:3000]):
                hit_bot_challenge = True
            else:
                frag = _extract_main_html_fragment(raw_html)
                md = html_to_clean_markdown(frag, max_chars=max_chars)
                if _looks_like_bot_challenge(md):
                    hit_bot_challenge = True
                elif len(md) >= 400:
                    if use_cache:
                        _save_cached_fulltext(cache_key, text=md, status="direct_html", cache_path=cache_path)
                    return (md, "direct_html")
    except Exception:
        pass

    if hit_bot_challenge:
        return ("", "failed")

    # Route 3: Jina Reader fallback for JS-rendered pages (never bypasses bot challenges, no auto-redirects)
    if allow_jina_fallback:
        jina_url = f"https://r.jina.ai/{target_url}"
        j_ok, j_ips = resolve_safe_public_ips(jina_url, socket_mod=socket)
        if j_ok:
            try:
                resp_j = http_get_no_redirect(
                    jina_url,
                    headers={**_HEADERS, "Accept": "text/markdown, text/plain"},
                    timeout=timeout,
                    pinned_ip=j_ips[0] if j_ips else None,
                    requests_mod=requests,
                )
                if resp_j.status_code in (301, 302, 303, 307, 308):
                    return ("", "failed")
                if resp_j.status_code == 200 and resp_j.content and len(resp_j.content) <= 2_500_000:
                    jina_raw = decode_http_text(resp_j).strip()
                    marker = "Markdown Content:"
                    if marker in jina_raw[:1500]:
                        jina_raw = jina_raw.split(marker, 1)[1].strip()
                    jina_clean = scrub_forbidden(re.sub(r"\n{3,}", "\n\n", jina_raw).strip(), preserve_newlines=True)
                    if not _looks_like_bot_challenge(jina_clean) and len(jina_clean) >= 400:
                        jina_out = jina_clean[:max_chars]
                        if use_cache:
                            _save_cached_fulltext(cache_key, text=jina_out, status="jina", cache_path=cache_path)
                        return (jina_out, "jina")
            except Exception:
                pass

    return ("", "failed")


def _fetch_cls_article_detail(
    art_id: str,
    timeout: int = 8,
    *,
    use_cache: bool = True,
    cache_path: Path | None = None,
) -> str:
    """Fetch full HTML body of a CLS depth article via signed v3/article/detail API."""
    clean_id = str(art_id or "").strip()
    if not clean_id or requests is None:
        return ""
    cache_key = f"cls:{clean_id}"
    if use_cache:
        hit = _get_cached_fulltext(cache_key, cache_path=cache_path)
        if hit is not None and hit[0]:
            return hit[0]

    params: dict[str, Any] = {
        "appName": "CailianpressWeb",
        "id": clean_id,
        "os": "web",
        "sv": "8.7.9",
    }
    params["sign"] = _cls_sign(params)
    try:
        resp = requests.get(
            "https://www.cls.cn/v3/article/detail",
            params=params,
            headers={**_HEADERS, "Referer": f"https://www.cls.cn/detail/{clean_id}"},
            timeout=timeout,
        )
        if resp.status_code != 200 or not resp.content:
            return ""
        payload = json.loads(decode_http_text(resp))
        if int(payload.get("errno") or 0) != 0:
            return ""
        data = payload.get("data") or {}
        raw_content = str(data.get("content") or "").strip()
        if not raw_content:
            return ""
        md = html_to_clean_markdown(raw_content, max_chars=16000)
        if len(md) >= 200:
            if use_cache:
                _save_cached_fulltext(cache_key, text=md, status="cls_v3", cache_path=cache_path)
            return md
    except Exception:
        pass
    return ""


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
        data = (json.loads(decode_http_text(resp)).get("data")) or {}
    except Exception:
        return []

    raw_rows = list(data.get("top_article") or []) + list(data.get("depth_list") or [])
    prepped: list[dict[str, Any]] = []
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

        title = clean_feed_text(row.get("title") or "", max_len=140)
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
        sub_src = clean_feed_text(row.get("source") or "财联社深度", max_len=40) or "财联社深度"
        summary = clean_feed_text(brief.replace("\n", " "), max_len=200)
        prepped.append(
            {
                "art_id": art_id,
                "title": title,
                "brief": brief,
                "summary": summary,
                "sub_src": sub_src,
                "time_bjt": time_bjt,
                "url": url,
            }
        )
        if len(prepped) >= 8:
            break

    if not prepped:
        return []

    detail_map: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {pool.submit(_fetch_cls_article_detail, item["art_id"], timeout): item["art_id"] for item in prepped}
        for fut in as_completed(futs):
            aid = futs[fut]
            try:
                detail_map[aid] = fut.result() or ""
            except Exception:
                detail_map[aid] = ""

    candidates: list[dict[str, Any]] = []
    for item in prepped:
        art_id = item["art_id"]
        title = item["title"]
        brief = item["brief"]
        summary = item["summary"]
        sub_src = item["sub_src"]
        time_bjt = item["time_bjt"]
        url = item["url"]
        full_md = detail_map.get(art_id) or ""

        if len(full_md) >= 200:
            body_md = (
                f"# {title}\n\n"
                f"- **来源**：{sub_src}（财联社深度）\n"
                f"- **发布时间**：{time_bjt} (BJT)\n"
                f"- **原文链接**：[{url}]({url})\n\n"
                f"> **核心要点**：{summary}\n\n"
                f"---\n\n{full_md}\n"
            )
            combined_for_meta = f"{title} {brief} {full_md[:1200]}"
        else:
            body_md = (
                f"# {title}\n\n"
                f"- **来源**：{sub_src}（财联社深度）\n"
                f"- **发布时间**：{time_bjt} (BJT)\n"
                f"- **原文链接**：[{url}]({url})\n\n"
                f"---\n\n{brief}\n"
            )
            combined_for_meta = f"{title} {brief}"

        candidates.append(
            {
                "id": f"art-cls-{art_id}",
                "source": "财联社深度",
                "publisher": "财联社深度",
                "category": _infer_cn_category(combined_for_meta, "equities"),
                "source_tier": "T1_5",
                "direction": infer_signal_direction(combined_for_meta),
                "time_bjt": time_bjt,
                "title": title,
                "summary": summary,
                "why_it_matters": "财联社深度板块精华研判，跟踪中国资本市场、产业政策与科技产业链动向。",
                "tickers": extract_tickers(combined_for_meta),
                "url": url,
                "body_md": body_md,
            }
        )
    return candidates


def _fetch_wscn_article_detail(
    art_id: str,
    timeout: int = 8,
    *,
    use_cache: bool = True,
    cache_path: Path | None = None,
) -> dict[str, Any] | None:
    """Fetch free WallStreetCN article fulltext; return None for VIP/paid gated articles."""
    clean_id = str(art_id or "").strip()
    if not clean_id or requests is None:
        return None
    cache_key = f"wscn:{clean_id}"
    if use_cache:
        hit = _get_cached_fulltext(cache_key, cache_path=cache_path)
        if hit is not None:
            if hit[1] == "skipped_paywall":
                return None
            try:
                cached_obj = json.loads(hit[0])
                if isinstance(cached_obj, dict) and cached_obj.get("content_md"):
                    return cached_obj
            except Exception:
                pass

    try:
        resp = requests.get(
            f"https://api-one.wallstcn.com/apiv1/content/articles/{urllib.parse.quote(clean_id)}",
            params={"extract": 0},
            headers=_HEADERS,
            timeout=timeout,
        )
        if resp.status_code != 200 or not resp.content:
            return None
        d = (json.loads(decode_http_text(resp)).get("data")) or {}
        if not isinstance(d, dict):
            return None
        if d.get("is_paid") or d.get("is_priced"):
            if use_cache:
                _save_cached_fulltext(cache_key, text="", status="skipped_paywall", cache_path=cache_path)
            return None
        content_md = html_to_clean_markdown(str(d.get("content") or ""), max_chars=16000)
        if len(content_md) < 300:
            return None
        author_obj = d.get("author") if isinstance(d.get("author"), dict) else {}
        detail = {
            "id": clean_id,
            "title": clean_feed_text(d.get("title") or "", max_len=140),
            "summary": clean_feed_text(d.get("content_short") or "", max_len=200),
            "author": clean_feed_text((author_obj or {}).get("display_name") or "", max_len=40),
            "display_time": d.get("display_time"),
            "uri": str(d.get("uri") or "").strip(),
            "content_md": content_md,
        }
        if use_cache:
            _save_cached_fulltext(
                cache_key,
                text=json.dumps(detail, ensure_ascii=False),
                status="wscn_detail",
                cache_path=cache_path,
            )
        return detail
    except Exception:
        return None


def _fetch_wscn_hot_articles(timeout: int = 8) -> list[dict[str, Any]]:
    """Fetch WallStreetCN daily hot articles (free fulltext only)."""
    if requests is None:
        return []
    try:
        resp = requests.get(
            "https://api-one.wallstcn.com/apiv1/content/articles/hot",
            params={"period": "all"},
            headers=_HEADERS,
            timeout=timeout,
        )
        if resp.status_code != 200 or not resp.content:
            return []
        data = (json.loads(decode_http_text(resp)).get("data")) or {}
    except Exception:
        return []

    day_items = list(data.get("day_items") or [])[:8]
    if not day_items:
        return []

    rows_by_id: dict[str, dict[str, Any]] = {}
    ordered_ids: list[str] = []
    for row in day_items:
        if not isinstance(row, dict):
            continue
        aid = str(row.get("id") or "").strip()
        if aid and aid not in rows_by_id:
            rows_by_id[aid] = row
            ordered_ids.append(aid)

    details: dict[str, dict[str, Any] | None] = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {pool.submit(_fetch_wscn_article_detail, aid, timeout): aid for aid in ordered_ids}
        for fut in as_completed(futs):
            aid = futs[fut]
            try:
                details[aid] = fut.result()
            except Exception:
                details[aid] = None

    candidates: list[dict[str, Any]] = []
    for aid in ordered_ids:
        row = rows_by_id[aid]
        det = details.get(aid)
        if not det:
            continue
        title = det.get("title") or clean_feed_text(row.get("title") or "", max_len=140)
        content_md = str(det.get("content_md") or "").strip()
        if not title or len(content_md) < 300:
            continue
        raw_uri = det.get("uri") or str(row.get("uri") or "").strip()
        url = raw_uri if raw_uri.startswith(("http://", "https://")) else f"https://wallstreetcn.com/articles/{aid}"
        ts_val = det.get("display_time") or row.get("display_time") or 0
        try:
            dt = datetime.fromtimestamp(int(ts_val), tz=UTC)
        except Exception:
            dt = datetime.now(UTC)
        time_bjt = dt.astimezone(BJT).strftime("%Y-%m-%d %H:%M")
        first_para = next((ln for ln in content_md.splitlines() if ln.strip() and not ln.strip().startswith("#")), "")
        summary = det.get("summary") or clean_feed_text(first_para, max_len=200) or title
        author = det.get("author") or "华尔街见闻"
        pageviews = row.get("pageviews")
        pv_str = f" · 热度 {int(pageviews)}" if isinstance(pageviews, (int, float)) and pageviews > 0 else ""

        body_md = (
            f"# {title}\n\n"
            f"- **来源**：{author}（华尔街见闻热门{pv_str}）\n"
            f"- **发布时间**：{time_bjt} (BJT)\n"
            f"- **原文链接**：[{url}]({url})\n\n"
            f"---\n\n{content_md}\n"
        )
        combined = f"{title} {summary} {content_md[:1200]}"
        candidates.append(
            {
                "id": f"art-wscn-{aid}",
                "source": "华尔街见闻热门",
                "publisher": "华尔街见闻热门",
                "category": _infer_cn_category(combined, "macro"),
                "source_tier": "T1_5",
                "direction": infer_signal_direction(combined),
                "time_bjt": time_bjt,
                "title": title,
                "summary": summary,
                "why_it_matters": "华尔街见闻热门全网高阅读深度稿，聚焦全球宏观、利率定价与科技龙头催化。",
                "tickers": extract_tickers(combined),
                "url": url,
                "body_md": body_md,
            }
        )
    return candidates


def _fetch_aihot_hot_articles(timeout: int = 10) -> list[dict[str, Any]]:
    """Fetch fulltext articles from AIHOT hot-topics & selected AI items (skipping paywalls/WeChat)."""
    if requests is None:
        return []
    actor_id = (os.environ.get("AIHOT_ACTOR_ID") or "macrocockpit-agent-v2").strip()
    aihot_headers = {
        **_HEADERS,
        "User-Agent": f"aihot-skill/2.0.0 (+https://aihot.news/aihot-skill/) aihot-actor/{actor_id}",
        "Accept": "application/json",
    }

    def _get_json(ep: str) -> dict[str, Any]:
        try:
            r = requests.get(ep, headers=aihot_headers, timeout=timeout)
            if r.status_code == 200 and r.content:
                data = json.loads(decode_http_text(r))
                return data if isinstance(data, dict) else {}
        except Exception:
            pass
        return {}

    with ThreadPoolExecutor(max_workers=2) as pool:
        ht_fut = pool.submit(_get_json, "https://aihot.news/api/v1/hot-topics")
        sel_fut = pool.submit(_get_json, "https://aihot.news/api/v1/items?mode=selected&limit=12")
        ht_items = list((ht_fut.result() or {}).get("items") or [])
        sel_items = list((sel_fut.result() or {}).get("items") or [])

    sel_by_id: dict[str, dict[str, Any]] = {}
    sel_by_url: dict[str, dict[str, Any]] = {}
    for it in sel_items:
        if not isinstance(it, dict):
            continue
        iid = str(it.get("id") or "").strip()
        orig = str(((it.get("links") or {}).get("original")) or "").strip()
        if iid:
            sel_by_id[iid] = it
        if orig:
            sel_by_url[orig] = it

    merged_specs: list[dict[str, Any]] = []
    seen_orig: set[str] = set()
    for raw in ht_items + sel_items:
        if not isinstance(raw, dict):
            continue
        aid = str(raw.get("id") or "").strip()
        orig = str(((raw.get("links") or {}).get("original")) or "").strip()
        if not orig or not is_safe_public_url(orig, socket_mod=socket) or _is_skipped_paywall_domain(orig):
            continue
        host = (urllib.parse.urlsplit(orig).hostname or "").lower()
        if host in ("aihot.news", "www.aihot.news"):
            continue
        if orig in seen_orig:
            continue
        seen_orig.add(orig)

        paired = sel_by_id.get(aid) or sel_by_url.get(orig) or {}
        src_name = clean_feed_text(
            ((raw.get("source") or {}).get("name")) or ((paired.get("source") or {}).get("name")) or "AIHOT",
            max_len=60,
        )
        title = clean_feed_text(raw.get("title") or paired.get("title") or "", max_len=140)
        if not title:
            continue
        summary_raw = str(raw.get("summary") or paired.get("summary") or "").strip()
        reason_raw = str(raw.get("reason") or paired.get("reason") or "").strip()
        ts_raw = raw.get("latestAt") or raw.get("publishedAt") or paired.get("publishedAt") or ""
        dt = parse_rss_datetime(str(ts_raw)) or datetime.now(UTC)
        merged_specs.append(
            {
                "id": aid or hashlib.sha1(orig.encode("utf-8")).hexdigest()[:10],
                "rank": raw.get("rank"),
                "title": title,
                "orig_url": orig,
                "src_name": src_name,
                "aihot_summary": clean_feed_text(summary_raw, max_len=240),
                "aihot_reason": clean_feed_text(reason_raw, max_len=200),
                "time_bjt": dt.astimezone(BJT).strftime("%Y-%m-%d %H:%M"),
            }
        )
        if len(merged_specs) >= 8:
            break

    if not merged_specs:
        return []

    fulltext_map: dict[str, tuple[str, str]] = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {
            pool.submit(fetch_url_fulltext_markdown, sp["orig_url"], timeout=timeout, max_chars=16000): sp["orig_url"]
            for sp in merged_specs
        }
        for fut in as_completed(futs):
            u = futs[fut]
            try:
                fulltext_map[u] = fut.result()
            except Exception:
                fulltext_map[u] = ("", "failed")

    candidates: list[dict[str, Any]] = []
    for sp in merged_specs:
        orig = sp["orig_url"]
        full_md, status = fulltext_map.get(orig, ("", "failed"))
        if status not in ("fxtwitter", "direct_html", "jina") or len(full_md) < 400:
            continue
        rank_tag = f"第 {int(sp['rank'])} 名" if isinstance(sp.get("rank"), int) else "精选"
        meta_lines = [
            f"# {sp['title']}",
            "",
            f"- **来源**：AIHOT 热榜{rank_tag}（原信源：{sp['src_name']}）",
            f"- **发布时间**：{sp['time_bjt']} (BJT)",
            f"- **原文链接**：[{orig}]({orig})",
        ]
        if sp["aihot_summary"]:
            meta_lines.append(f"\n> **AIHOT 摘要**：{sp['aihot_summary']}")
        body_md = scrub_forbidden("\n".join(meta_lines) + f"\n\n---\n\n{full_md}\n", preserve_newlines=True) + "\n"

        first_line = next((ln for ln in full_md.splitlines() if ln.strip() and not ln.strip().startswith("#")), "")
        summary = sp["aihot_summary"] or clean_feed_text(first_line, max_len=200) or sp["title"]
        combined = f"{sp['title']} {summary} {full_md[:1200]}"
        candidates.append(
            {
                "id": f"art-aihot-{sp['id']}",
                "source": "AIHOT热榜",
                "publisher": "AIHOT热榜",
                "category": "ai",
                "source_tier": "T1_5",
                "direction": infer_signal_direction(combined),
                "time_bjt": sp["time_bjt"],
                "title": sp["title"],
                "summary": summary,
                "why_it_matters": sp["aihot_reason"] or "AIHOT 全网热门议题原文全文，追踪大模型与 AI 产业链前沿动向。",
                "tickers": extract_tickers(combined),
                "url": orig,
                "body_md": body_md,
            }
        )
    return candidates


def _select_balanced_articles(
    deduped: list[dict[str, Any]],
    *,
    limit: int = 14,
    max_per_publisher: int = 4,
) -> list[dict[str, Any]]:
    """Select top articles by score/time while preventing a single publisher from dominating."""
    ordered = sorted(
        deduped,
        key=lambda x: (int(x.get("attention_score") or x.get("score") or 0), str(x.get("time_bjt") or "")),
        reverse=True,
    )
    picked: list[dict[str, Any]] = []
    picked_ids: set[int] = set()
    pub_counts: dict[str, int] = {}

    for idx, it in enumerate(ordered):
        if len(picked) >= limit:
            break
        pub = str(it.get("publisher") or it.get("source") or "AI与金融研报").strip()
        if pub_counts.get(pub, 0) >= max_per_publisher:
            continue
        picked.append(it)
        picked_ids.add(idx)
        pub_counts[pub] = pub_counts.get(pub, 0) + 1

    if len(picked) < limit:
        for idx, it in enumerate(ordered):
            if idx in picked_ids:
                continue
            picked.append(it)
            if len(picked) >= limit:
                break

    return picked


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
            f"title: {yaml_quote(title)}\n"
            f"summary: {yaml_quote(summary)}\n"
            f"publisher: {yaml_quote(publisher)}\n"
            f"url: {yaml_quote(url)}\n"
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
    """Fetch, filter, score and persist balanced fulltext AI/finance articles as articles/feed_*.md."""
    if not force_refresh and cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            age = time.time() - float(cached.get("ts") or 0.0)
            arts = cached.get("articles") or []
            if cached.get("schema_tag") == "wave2_fulltext" and age <= ARTICLE_CACHE_TTL and isinstance(arts, list) and arts:
                if not list(articles_dir.glob("feed_*.md")):
                    _write_curated_article_files(arts, articles_dir=articles_dir)
                return [dict(x) for x in arts if isinstance(x, dict)]
        except Exception:
            pass

    from backend.kazike_engine import cluster_and_dedup, prefilter_financial, score_five_axes

    raw_candidates: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = [pool.submit(_fetch_article_candidates_from_feed, sp, timeout) for sp in CURATED_ARTICLE_FEEDS]
        cls_fut = pool.submit(_fetch_cls_depth_candidates, timeout)
        wscn_fut = pool.submit(_fetch_wscn_hot_articles, timeout)
        aihot_fut = pool.submit(_fetch_aihot_hot_articles, max(timeout, 10))
        for fut in as_completed(futs):
            try:
                raw_candidates.extend(fut.result())
            except Exception:
                pass
        for extra_fut in (cls_fut, wscn_fut, aihot_fut):
            try:
                raw_candidates.extend(extra_fut.result())
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
        balanced = _select_balanced_articles(deduped, limit=14, max_per_publisher=4)
        top_articles = [
            {
                "filename": "",
                "title": _scrub_forbidden(str(it.get("title") or "")),
                "summary": _scrub_forbidden(str(it.get("summary") or "")),
                "publisher": str(it.get("publisher") or it.get("source") or "AI与金融研报"),
                "url": str(it.get("url") or ""),
                "score": int(it.get("attention_score") or 0),
                "time_bjt": str(it.get("time_bjt") or ""),
                "body_md": scrub_forbidden(it.get("body_md"), preserve_newlines=True),
            }
            for it in balanced
        ]
        _write_curated_article_files(top_articles, articles_dir=articles_dir)
        try:
            atomic_write_json(cache_path, {"schema_tag": "wave2_fulltext", "ts": time.time(), "articles": top_articles})
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
        "大佬分享与深度访谈": [],
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
    for sp in GURU_ARTICLE_FEEDS:
        u = str(sp.get("url") or "").strip()
        if not u or u in seen_urls:
            continue
        seen_urls.add(u)
        label = str(sp.get("publisher") or sp.get("author") or sp.get("id") or "大佬专栏")
        groups["大佬分享与深度访谈"].append(
            {
                "text": label,
                "title": label,
                "type": "rss",
                "xmlUrl": u,
                "htmlUrl": str(sp.get("html_url") or u),
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
