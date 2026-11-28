"""Multi-channel forum & community sentiment collector and DESK vs STREET divergence engine."""
from __future__ import annotations

import argparse
import hashlib
import html
import http.cookiejar
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:
    requests = None  # type: ignore[assignment]

try:
    from curl_cffi import requests as curl_requests  # type: ignore
except ImportError:
    curl_requests = None  # type: ignore[assignment]

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None  # type: ignore[assignment]

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

_BACKEND_DIR = Path(__file__).resolve().parent
if str(_BACKEND_DIR.parent) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR.parent))

from backend.discussion_temperature import discussion_trends, refresh_discussion_history
from backend.fred_and_market import COCKPIT_ROOT, DATA_DIR, atomic_write_json
from backend.public_snapshot import has_private_article_text, project_market_pulse
from backend.rss_sources import _norm_dup_key, strip_emojis
from backend.zh_brief import attach_zh_briefs_batch

BJT = timezone(timedelta(hours=8))
UTC = timezone.utc

DEFAULT_FORUM_CACHE_PATH = DATA_DIR / "forum_pulse_cache.json"
FORUM_CACHE_TTL_SECONDS = 1800  # 30 minutes
MAX_OUTPUT_BYTES = 40 * 1024  # 40 KB strict contract

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

FORBIDDEN_TERMS: tuple[str, ...] = (
    "Sleeve",
    "E21",
    "E32",
    "E33",
    "Tier-1",
    "Regime",
    "arbitration",
    "测算口径",
    "公式解释",
    "公式说明",
    "逻辑解释",
    "联动说明",
    "方法论说明",
    "待补",
    "待刷新",
    "N/A",
)

_FORBIDDEN_REPLACEMENTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?<![A-Za-z0-9])E(?:21|32|33)(?![A-Za-z0-9])", re.IGNORECASE), "波动结构"),
    (re.compile(r"(?<![A-Za-z0-9])Tier-?[1SA](?![A-Za-z0-9])", re.IGNORECASE), "核心"),
    (re.compile(r"(?<![A-Za-z0-9])Sleeves?(?![A-Za-z0-9])", re.IGNORECASE), "仓位"),
    (re.compile(r"(?<![A-Za-z0-9])Regimes?(?![A-Za-z0-9])", re.IGNORECASE), "市场阶段"),
    (re.compile(r"(?<![A-Za-z0-9])arbitration(?![A-Za-z0-9])", re.IGNORECASE), "风险平衡"),
    (re.compile(r"测算口径|公式解释|公式说明|逻辑解释|联动说明|方法论说明"), "参考摘要"),
    (re.compile(r"待补充|待补|待刷新"), "—"),
    (re.compile(r"(?<![A-Za-z0-9])N/A(?![A-Za-z0-9])", re.IGNORECASE), "—"),
    (re.compile(r"N/A", re.IGNORECASE), "—"),
)

CORE_TOPICS: tuple[str, ...] = (
    "美股AI与科技",
    "A股与港股",
    "比特币与加密",
    "黄金与大宗",
    "美联储与宏观利率",
    "市场整体风险偏好",
)

NEWSNOW_API_URL = "https://newsnow.busiyi.world/api/s"
CN_COMMUNITY_SOURCES: tuple[tuple[str, str, bool], ...] = (
    ("wallstreetcn-hot", "华尔街见闻", True),
    ("cls-hot", "财联社热榜", True),
    ("weibo", "微博热搜", False),
    ("baidu", "百度热搜", False),
    ("toutiao", "今日头条", False),
    ("douyin", "抖音热榜", False),
    ("zhihu", "知乎热榜", False),
    ("36kr", "36氪", False),
    ("bilibili-hot-search", "B站热搜", False),
)
MASS_HOTLIST_IDS: frozenset[str] = frozenset(
    {"weibo", "baidu", "toutiao", "douyin", "zhihu", "bilibili-hot-search"}
)
BAIDU_FINANCE_BOARD_URL = "https://top.baidu.com/api/board?platform=wise&tab=finance"
EASTMONEY_HOTRANK_URL = "https://emappdata.eastmoney.com/stockrank/getAllCurrentList"
EASTMONEY_RZRQ_URL = (
    "https://datacenter-web.eastmoney.com/api/data/v1/get"
    "?reportName=RPTA_RZRQ_LSHJ"
    "&columns=DIM_DATE,RZYE,RZYEZB,RZMRE,RZJME,RQYE,RZRQYE"
    "&pageNumber=1&pageSize=5&sortTypes=-1&sortColumns=DIM_DATE"
)
EASTMONEY_SOUTHBOUND_URL = (
    "https://datacenter-web.eastmoney.com/api/data/v1/get"
    "?reportName=RPT_MUTUAL_DEAL_HISTORY"
    "&columns=TRADE_DATE,MUTUAL_TYPE,NET_DEAL_AMT,BUY_AMT,SELL_AMT,DEAL_AMT"
    "&filter=(MUTUAL_TYPE%20in%20(%22006%22))"
    "&pageNumber=1&pageSize=6&sortTypes=-1&sortColumns=TRADE_DATE"
)
TENCENT_QUOTES_BASE_URL = "https://qt.gtimg.cn/q="
TENCENT_MARKET_INDICES = "sh000001,sz399001,sz399006,sh000688,hkHSI,hkHSTECH"

HN_FRONT_PAGE_URL = "https://hn.algolia.com/api/v1/search?tags=front_page"
REDDIT_SUBREDDITS: tuple[str, ...] = ("wallstreetbets", "stocks", "investing", "options")
XHS_KEYWORDS: tuple[str, ...] = ("黄金", "美股", "A股", "港股", "比特币", "纳指")

_CN_FINANCE_KEYWORDS_RE = re.compile(
    r"(?i)(?:"
    r"财经|宏观|央行|美联储|联储|降息|加息|降准|利率|国债|美债|通胀|通缩|CPI|PCE|GDP|PMI|非农|"
    r"关税|汇率|美元|人民币|离岸|流动性|财政|刺激|赤字|沃什|鲍威尔|"
    r"A股|港股|美股|上证|深成指|创业板|科创板|恒生|沪指|北向|外资|中概|"
    r"纳指|标普|道指|英伟达|黄仁勋|台积电|苹果|微软|谷歌|亚马逊|特斯拉|博通|美光|超微|ASML|"
    r"AI|人工智能|算力|大模型|芯片|半导体|存储|光模块|PCB|GPU|HBM|机器人|自动驾驶|"
    r"黄金|金价|白银|原油|油价|铜|大宗|有色|稀土|锂矿|煤炭|能源|"
    r"比特币|以太坊|加密|稳定币|数字货币|币圈|BTC|ETH|"
    r"基金|ETF|券商|银行|保险|地产|消费|固态电池|新能源|光伏|军工|医药|"
    r"财报|业绩|营收|利润|市值|涨停|跌停|牛市|熊市|估值|分红|回购|IPO|融资|并购|股市|股票|股价|投资|理财|量化"
    r")"
)

_ENTERTAINMENT_NOISE_RE = re.compile(
    r"(?:明星|八卦|恋情|离婚|绯闻|综艺|选秀|电视剧|电影|票房|演员|歌手|男团|女团|粉丝|应援|"
    r"出轨|结婚|生子|婚礼|穿搭|美食|食谱|旅游|攻略|网红|吃瓜|塌房|热播|首播|大结局|"
    r"王者荣耀|原神|英雄联盟|和平精英|皮肤|电竞|手游|考研|高考|明星同款|宠物|萌宠|搞笑)"
)

_HN_FINANCE_TECH_RE = re.compile(
    r"(?i)\b(?:"
    r"ai|llm|gpu|nvidia|amd|intel|tsmc|asml|apple|microsoft|google|alphabet|meta|amazon|tesla|"
    r"openai|anthropic|semiconductor|chip|chips|datacenter|cloud|cuda|inference|silicon|"
    r"fed|inflation|tariff|tariffs|rate|rates|treasury|economy|recession|market|stock|stocks|"
    r"ipo|vc|funding|valuation|revenue|earnings|crypto|bitcoin|btc|ethereum|eth|gold|oil|energy|bank|finance"
    r")\b"
)

_REDDIT_NOISE_RE = re.compile(
    r"(?i)(?:daily\s+discussion|what\s+are\s+your\s+moves|weekend\s+discussion|"
    r"rate\s+my\s+portfolio|loss\s+porn|gain\s+porn|meme\s+monday|shitpost)"
)

_XHS_SPAM_RE = re.compile(
    r"(?:卖课|加微信|加V|私信领|开户返佣|代操作|带单|稳赚不赔|月入过万|副业变现|推广|广告|招商加盟)"
)
_XHS_FALSE_MATCH_RE = re.compile(
    r"(?:黄金时机|黄金时代|黄金周|黄金档|黄金年龄|学渣|学霸|逆袭|逛山姆|养生|新手投资教学|炒股黑科技|"
    r"私银|家族办公室|存款利率表|票据贴现|护肤|美妆|穿搭|减肥|探店|打卡攻略|情感语录)"
)

_BULL_RE = re.compile(
    r"(?i)\b(?:bull|bullish|buy|buying|long|breakout|rally|rallies|surge|soar|boom|beat|beats|"
    r"outperform|upside|rebound|recovery|upgrade|upgraded|raise|tailwind|accumulate|undervalued)\b|"
    r"看多|看好|看涨|抄底|做多|突破|低估|反弹|利好|买入|加仓|见底|回暖|超预期|暴涨|大涨|走强|飙升|创新高|上修|上调|景气|增持|爆发|"
    r"回购|注销|预增|扭亏|净流入|净买入|放量突破|涨停|连板|牛市|放量上涨|狂飙|收复|普涨"
)

_BEAR_RE = re.compile(
    r"(?i)\b(?:bear|bearish|short|shorting|sell|selloff|sell-off|crash|dump|bubble|overvalued|"
    r"recession|warning|plunge|drop|decline|slump|weak|miss|missed|downgrade|cut|layoff|default|risk-off)\b|"
    r"看空|看跌|做空|暴跌|大跌|跳水|泡沫|减仓|清仓|止损|破位|风险|利空|崩盘|熊市|承压|抛售|下跌|跌破|恐慌|走弱|不及预期|下修|下调|收紧|鹰派|警惕|高估|见顶|警示|"
    r"减持|折让配售|配股融资|预亏|立案|净流出|净卖出|终止上市|退市"
)

_FALSE_BEAR_RE = re.compile(r"(?i)\b(?:short\s+squeeze|short\s+term|short-term|shortage|drop\s+in\s+yields)\b")
_FALSE_BULL_RE = re.compile(r"(?i)\b(?:long\s+term|long-term|long\s+run|bull\s+trap)\b")

_TICKER_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\$NVDA\b|(?<![A-Z0-9$])NVDA(?![A-Z0-9])|(?i:\bNvidia\b|英伟达|黄仁勋|Blackwell|Rubin|CUDA)"), "NVDA"),
    (re.compile(r"\$AAPL\b|(?<![A-Z0-9$])AAPL(?![A-Z0-9])|(?i:\bApple\b|苹果|iPhone)"), "AAPL"),
    (re.compile(r"\$MSFT\b|(?<![A-Z0-9$])MSFT(?![A-Z0-9])|(?i:\bMicrosoft\b|微软|Azure)"), "MSFT"),
    (re.compile(r"\$GOOGL?\b|(?<![A-Z0-9$])GOOGL?(?![A-Z0-9])|(?i:\bAlphabet\b|\bGoogle\b|谷歌|Gemini)"), "GOOG"),
    (re.compile(r"\$META\b|(?<![A-Z0-9$])META(?![A-Z0-9])|(?i:\bMeta\s+Platforms\b|\bZuckerberg\b|扎克伯格)"), "META"),
    (re.compile(r"\$AMZN\b|(?<![A-Z0-9$])AMZN(?![A-Z0-9])|(?i:\bAmazon\b|\bAWS\b|亚马逊)"), "AMZN"),
    (re.compile(r"\$AVGO\b|(?<![A-Z0-9$])AVGO(?![A-Z0-9])|(?i:\bBroadcom\b|博通)"), "AVGO"),
    (re.compile(r"\$TSLA\b|(?<![A-Z0-9$])TSLA(?![A-Z0-9])|(?i:\bTesla\b|特斯拉|\bFSD\b|\bRobotaxi\b)"), "TSLA"),
    (re.compile(r"\$AMD\b|(?<![A-Z0-9$])AMD(?![A-Z0-9])|(?i:超威半导体|苏姿丰)"), "AMD"),
    (re.compile(r"\$TSMC?\b|(?<![A-Z0-9$])TSMC?(?![A-Z0-9])|(?i:台积电|\bCoWoS\b)"), "TSM"),
    (re.compile(r"\$INTC\b|(?<![A-Z0-9$])INTC(?![A-Z0-9])|(?i:\bIntel\b|英特尔)"), "INTC"),
    (re.compile(r"\$MU\b|(?<![A-Z0-9$])MU(?![A-Z0-9])|(?i:\bMicron\b|美光|\bHBM[34]?\b)"), "MU"),
    (re.compile(r"\$ARM\b|(?<![A-Z0-9$])ARM(?![A-Z0-9])|(?i:\bArm\s+Holdings\b)"), "ARM"),
    (re.compile(r"\$PLTR\b|(?<![A-Z0-9$])PLTR(?![A-Z0-9])|(?i:\bPalantir\b)"), "PLTR"),
    (re.compile(r"\$BABA\b|(?<![A-Z0-9$])(?:BABA|0?9988\.HK)(?![A-Z0-9])|(?i:\bAlibaba\b|阿里巴巴|阿里)"), "BABA"),
    (re.compile(r"(?<![A-Z0-9$])(?:0?0700\.HK|TCEHY)(?![A-Z0-9])|(?i:\bTencent\b|腾讯控股|腾讯)"), "0700.HK"),
    (re.compile(r"(?<![A-Z0-9$])0?3690\.HK(?![A-Z0-9])|(?i:\bMeituan\b|美团)"), "3690.HK"),
    (re.compile(r"(?<![A-Z0-9$])0?1810\.HK(?![A-Z0-9])|(?i:\bXiaomi\b|小米集团|小米汽车)"), "1810.HK"),
    (re.compile(r"(?<![A-Z0-9$])(?:0?0981\.HK|688981)(?![A-Z0-9])|(?i:\bSMIC\b|中芯国际)"), "0981.HK"),
    (re.compile(r"(?<![A-Z0-9$])(?:0?1211\.HK|002594)(?![A-Z0-9])|(?i:\bBYD\b|比亚迪)"), "1211.HK"),
    (re.compile(r"\$BTC\b|(?<![A-Z0-9$])BTC(?![A-Z0-9])|(?i:\bBitcoin\b|比特币)"), "BTC"),
    (re.compile(r"\$BNB\b|(?<![A-Z0-9$])BNB(?![A-Z0-9])|(?i:\bBinance\b|币安)"), "BNB"),
    (re.compile(r"\$ETH\b|(?<![A-Z0-9$])ETH(?![A-Z0-9])|(?i:\bEthereum\b|以太坊)"), "ETH"),
    (re.compile(r"\$SOL\b|(?<![A-Z0-9$])SOL(?![A-Z0-9])|(?i:\bSolana\b)"), "SOL"),
    (re.compile(r"(?<![A-Z0-9$])(?:ZHIPU|ZHIPUUSDT|2513\.HK|02513)(?![A-Z0-9])|(?i:智谱|\bChatGLM\b|\bAutoGLM\b)"), "ZHIPU"),
    (re.compile(r"\$SPY\b|\$SPX\b|(?<![A-Z0-9$])(?:SPY|SPX|SP500)(?![A-Z0-9])|(?i:S&P\s*500|标普)"), "SP500"),
    (re.compile(r"\$QQQ\b|\$NDX\b|(?<![A-Z0-9$])(?:QQQ|NDX)(?![A-Z0-9])|(?i:\bNasdaq\b|纳斯达克|纳指)"), "QQQ"),
    (re.compile(r"\$GLD\b|\$XAU\b|(?<![A-Z0-9$])(?:GLD|XAU|XAUUSD|PAXG)(?![A-Z0-9])|(?i:黄金|金价|伦敦金|\bgold\b)"), "GOLD"),
    (re.compile(r"\$WTI\b|\$USO\b|(?<![A-Z0-9$])WTI(?![A-Z0-9])|(?i:\bBrent\b|\bcrude\s+oil\b|原油|油价)"), "WTI"),
    (re.compile(r"\$DXY\b|(?<![A-Z0-9$])DXY(?![A-Z0-9])|(?i:\bdollar\s+index\b|美元指数|美元流动性)"), "DOLLAR"),
)

_AI_US_TICKERS = frozenset(
    {"NVDA", "TSM", "AAPL", "MSFT", "GOOG", "META", "AMZN", "AVGO", "AMD", "INTC", "QCOM", "MU", "ARM", "PLTR", "ORCL", "SMCI", "QQQ", "NFLX", "TSLA", "ASML"}
)
_ASHARE_HK_TICKERS = frozenset({"BABA", "ZHIPU", "0700.HK", "3690.HK", "1810.HK", "0981.HK", "1211.HK", "9988.HK", "00700.HK", "09988.HK", "00981.HK"})
_CRYPTO_TICKERS = frozenset({"BTC", "ETH", "SOL", "BNB", "COIN", "MSTR"})
_GOLD_COMM_TICKERS = frozenset({"GOLD", "PAXG", "WTI", "XAU", "GLD", "USO"})
_MACRO_TICKERS = frozenset({"DOLLAR", "US10Y", "US2Y", "SP500"})

_TOPIC_AI_US_RE = re.compile(
    r"(?i)\b(?:ai|llm|gpu|nvidia|tsmc|asml|apple|microsoft|google|meta|amazon|broadcom|tesla|amd|intel|micron|openai|anthropic|nasdaq|qqq|cowos|hbm)\b|"
    r"美股|纳指|标普|英伟达|黄仁勋|台积电|算力|芯片|半导体|光模块|人工智能|大模型|存储|科技股"
)
_TOPIC_ASHARE_HK_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:a-?share|csi|hsi|hstech|baba|alibaba|tencent|meituan|xiaomi|smic|zhipu|chatglm|autoglm)(?![A-Za-z0-9])|"
    r"(?<![A-Za-z0-9])(?:SH|SZ|BJ)\d{6}(?![A-Za-z0-9])|(?<![A-Za-z0-9])\d{6}\.(?:SZ|SS|SH|BJ)(?![A-Za-z0-9])|"
    r"(?<![A-Za-z0-9])\d{4,5}\.HK(?![A-Za-z0-9])|(?<![A-Za-z0-9])HK\d{4,5}(?![A-Za-z0-9])|"
    r"A股|港股|港交所|上证|深成指|创业板|科创板|科创50|沪深300|中证500|中证1000|中证2000|恒生科技|恒科|恒生|恒指|沪指|北向|南向|港股通|"
    r"两融|融资余额|融券|平准基金|汇金|国常会|国家队|中概|茅台|宁德|中际旭创|寒武纪|中芯|华为|腾讯|美团|小米|京东|快手|百度|网易|比亚迪|药明|泡泡玛特|券商|降准|两市|A50|智谱"
)
_TOPIC_CRYPTO_RE = re.compile(
    r"(?i)\b(?:bitcoin|btc|eth|ethereum|sol|solana|crypto|cryptocurrency|stablecoin|usdc|usdt|defi|coinbase|mstr)\b|"
    r"比特币|以太坊|加密|稳定币|币圈|链上|巨鲸"
)
_TOPIC_GOLD_COMM_RE = re.compile(
    r"(?i)\b(?:gold|xau|gld|silver|oil|wti|brent|copper|commodity|commodities)\b|"
    r"黄金|金价|伦敦金|白银|原油|油价|大宗|铜价|有色|稀土|能源"
)
_TOPIC_MACRO_FED_RE = re.compile(
    r"(?i)\b(?:fed|fomc|powell|warsh|cpi|pce|gdp|ism|nfp|inflation|rates?|yields?|treasury|dollar|dxy|tariff|tariffs|liquidity|deficit|recession)\b|"
    r"美联储|联储|沃什|鲍威尔|降息|加息|利率|通胀|通缩|非农|美债|收益率|美元|汇率|流动性|财政|赤字|关税|宏观"
)


def _resolve_user_home() -> Path:
    """Resolve user home across macOS/Linux/Windows, guarding against Windows SYSTEM profile."""
    env_home = os.environ.get("MACROCOCKPIT_USER_HOME", "").strip()
    if env_home:
        return Path(env_home).expanduser()
    try:
        home = Path.home()
    except RuntimeError:
        home = COCKPIT_ROOT
    if os.name == "nt" and "systemprofile" in str(home).lower():
        users_root = Path("C:/Users")
        if users_root.is_dir():
            for cand in sorted(users_root.iterdir()):
                if (
                    cand.is_dir()
                    and cand.name.lower() not in ("public", "default", "default user", "all users")
                    and (cand / "AppData" / "Local").is_dir()
                ):
                    return cand
    return home


def _resolve_hermes_home(user_home: Path | None = None) -> Path:
    """Resolve Hermes home directory across platforms."""
    env_hermes = os.environ.get("HERMES_HOME", "").strip()
    if env_hermes:
        return Path(env_hermes).expanduser()
    home = user_home or _resolve_user_home()
    win_hermes = home / "AppData" / "Local" / "hermes"
    if win_hermes.exists() or os.name == "nt":
        return win_hermes
    return home / ".hermes"


def scrub_forbidden(text: str) -> str:
    """Replace forbidden terms with plain Chinese business wording and strip stray emojis."""
    s = strip_emojis(str(text or ""))
    for rx, rep in _FORBIDDEN_REPLACEMENTS:
        s = rx.sub(rep, s)
    return re.sub(r"[ \t]{2,}", " ", s).strip()


def sanitize_output(obj: Any) -> Any:
    """Recursively scrub forbidden terms across nested structures without corrupting URLs."""
    if isinstance(obj, str):
        return scrub_forbidden(obj)
    if isinstance(obj, list):
        return [sanitize_output(x) for x in obj]
    if isinstance(obj, tuple):
        return [sanitize_output(x) for x in obj]
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            clean_k = scrub_forbidden(str(k))
            if clean_k == "url" and isinstance(v, str):
                out[clean_k] = _safe_http_url(v)
            else:
                out[clean_k] = sanitize_output(v)
        return out
    return obj


def _now_bjt_str() -> str:
    return datetime.now(BJT).strftime("%Y-%m-%d %H:%M")


def _strip_html(raw: str) -> str:
    text = re.sub(r"<[^>]+>", " ", raw or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _truncate(text: str, max_len: int) -> str:
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(s) <= max_len:
        return s
    return s[: max(1, max_len - 1)].rstrip() + "…"


def _clamp(val: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, val))


def _safe_http_url(url: str) -> str:
    s = str(url or "").strip()
    if not s.startswith(("https://", "http://")):
        return ""
    try:
        parsed = urllib.parse.urlsplit(s)
    except ValueError:
        return ""
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
        return ""
    return s


def _canon_hk_code(digits: str) -> str:
    d = str(digits or "").strip()
    if len(d) == 5 and d.startswith("0"):
        d = d[1:]
    return f"{d}.HK"


def _canon_ticker_sym(sym: str) -> str:
    s = str(sym or "").strip().upper()
    m_hk = re.fullmatch(r"(\d{4,5})\.HK", s)
    if m_hk:
        return _canon_hk_code(m_hk.group(1))
    m_hk_pref = re.fullmatch(r"HK(\d{4,5})", s)
    if m_hk_pref:
        return _canon_hk_code(m_hk_pref.group(1))
    return s


def _is_ahk_ticker(sym: str) -> bool:
    s = str(sym or "").strip().upper()
    return (
        bool(re.fullmatch(r"(?:SH|SZ|BJ)\d{6}", s))
        or s.endswith((".SZ", ".SS", ".SH", ".BJ", ".HK"))
        or s in _ASHARE_HK_TICKERS
    )


def extract_tickers(text: str) -> list[str]:
    """Extract canonical US/A-share/HK/crypto/macro tickers from mixed text."""
    if not text:
        return []
    alias_norm = {"GOOGL": "GOOG", "TSMC": "TSM", "SPY": "SP500", "SPX": "SP500", "GLD": "GOLD", "XAU": "GOLD", "DXY": "DOLLAR", "USO": "WTI"}
    out: list[str] = []
    for m in re.findall(r"\$([A-Z]{2,5})\b", text):
        sym = alias_norm.get(m, m)
        if sym not in out:
            out.append(sym)
    for m in re.findall(r"(?<![A-Za-z0-9])((?:SH|SZ|BJ)\d{6})(?![A-Za-z0-9])", text):
        if m not in out:
            out.append(m)
    for m in re.findall(r"(?<![A-Za-z0-9])(\d{6}\.(?:SZ|SS|SH|BJ))(?![A-Za-z0-9])", text, re.IGNORECASE):
        sym = m.upper()
        if sym not in out:
            out.append(sym)
    for m in re.findall(r"(?<![A-Za-z0-9])(\d{4,5})\.HK(?![A-Za-z0-9])", text, re.IGNORECASE):
        sym = _canon_hk_code(m)
        if sym not in out:
            out.append(sym)
    for m in re.findall(r"(?<![A-Za-z0-9])HK(\d{4,5})(?![A-Za-z0-9])", text, re.IGNORECASE):
        sym = _canon_hk_code(m)
        if sym not in out:
            out.append(sym)
    for rx, sym in _TICKER_RULES:
        if rx.search(text) and sym not in out:
            out.append(sym)
    return out[:6]


def _score_text_sentiment(text: str) -> tuple[int, str]:
    """Return sentiment score in [-100, 100] and label in {'偏多', '偏空', '中性'}."""
    cleaned = _FALSE_BEAR_RE.sub(" ", _FALSE_BULL_RE.sub(" ", text or ""))
    bull_hits = len(_BULL_RE.findall(cleaned))
    bear_hits = len(_BEAR_RE.findall(cleaned))
    if bull_hits == 0 and bear_hits == 0:
        return 0, "中性"
    diff = bull_hits - bear_hits
    if diff == 0:
        return 0, "中性"
    raw_score = int(_clamp(diff * 35.0, -90.0, 90.0))
    if raw_score >= 15:
        return raw_score, "偏多"
    if raw_score <= -15:
        return raw_score, "偏空"
    return raw_score, "中性"


def _classify_topics(text: str, tickers: list[str] | None = None) -> list[str]:
    """Classify text & tickers into the 6 core macro/market topics."""
    tks = set(tickers or [])
    topics: list[str] = []
    if (tks & _AI_US_TICKERS) or _TOPIC_AI_US_RE.search(text):
        topics.append("美股AI与科技")
    if _TOPIC_ASHARE_HK_RE.search(text) or any(_is_ahk_ticker(t) for t in tks):
        topics.append("A股与港股")
    if (tks & _CRYPTO_TICKERS) or _TOPIC_CRYPTO_RE.search(text):
        topics.append("比特币与加密")
    if (tks & _GOLD_COMM_TICKERS) or _TOPIC_GOLD_COMM_RE.search(text):
        topics.append("黄金与大宗")
    if (tks & _MACRO_TICKERS) or _TOPIC_MACRO_FED_RE.search(text):
        topics.append("美联储与宏观利率")
    if not topics or "市场" in text or "风险" in text or "SP500" in tks or "VIX" in tks:
        topics.append("市场整体风险偏好")
    return topics


def _load_xueqiu_cookie_from_config(user_home: Path) -> str:
    cfg_path = user_home / ".agent-reach" / "config.yaml"
    if not cfg_path.exists():
        return ""
    try:
        raw = cfg_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    if yaml is not None:
        try:
            parsed = yaml.safe_load(raw) or {}
            if isinstance(parsed, dict):
                val = parsed.get("xueqiu_cookie")
                if isinstance(val, str) and val.strip():
                    return val.strip()
        except Exception:
            pass
    m = re.search(r'^\s*xueqiu_cookie\s*:\s*["\']?([^"\'\r\n#]+)', raw, re.MULTILINE)
    return m.group(1).strip() if m else ""


def _inject_cookie_string(jar: http.cookiejar.CookieJar, cookie_str: str, domain: str = ".xueqiu.com") -> None:
    for pair in cookie_str.split(";"):
        pair = pair.strip()
        if "=" not in pair:
            continue
        name, _, value = pair.partition("=")
        cookie = http.cookiejar.Cookie(
            version=0,
            name=name.strip(),
            value=value.strip(),
            port=None,
            port_specified=False,
            domain=domain,
            domain_specified=True,
            domain_initial_dot=domain.startswith("."),
            path="/",
            path_specified=True,
            secure=True,
            expires=None,
            discard=True,
            comment=None,
            comment_url=None,
            rest={},
        )
        jar.set_cookie(cookie)


def _load_xueqiu_browser_cookies(jar: http.cookiejar.CookieJar) -> bool:
    try:
        try:
            import rookiepy  # type: ignore

            cookies = rookiepy.chrome([".xueqiu.com"])
            if not any(c.get("name") == "xq_a_token" for c in cookies):
                return False
            for c in cookies:
                _inject_cookie_string(jar, f"{c['name']}={c['value']}", domain=c.get("domain", ".xueqiu.com"))
            return True
        except ImportError:
            import browser_cookie3  # type: ignore

            cookies = list(browser_cookie3.chrome(domain_name=".xueqiu.com"))
            if not any(c.name == "xq_a_token" for c in cookies):
                return False
            for c in cookies:
                jar.set_cookie(c)
            return True
    except Exception:
        return False


def _fetch_xueqiu_channel(*, user_home: Path, timeout: int = 6) -> dict[str, Any]:
    """Collect Xueqiu hot stocks & public timeline posts with graceful auth degradation."""
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    headers = {"User-Agent": _UA, "Referer": "https://xueqiu.com/"}

    cookie_source = "homepage_fallback"
    cfg_cookie = _load_xueqiu_cookie_from_config(user_home)
    if cfg_cookie:
        _inject_cookie_string(jar, cfg_cookie)
        cookie_source = "config"
    elif _load_xueqiu_browser_cookies(jar):
        cookie_source = "browser"
    else:
        try:
            opener.open(urllib.request.Request("https://xueqiu.com/", headers={"User-Agent": _UA}), timeout=timeout)
        except Exception as exc:
            return {
                "id": "xueqiu",
                "name": "雪球社区",
                "status": "需登录态",
                "count": 0,
                "note": f"雪球入口暂不可达（{type(exc).__name__}），可配置 xueqiu_cookie 后重试",
                "cookie_source": cookie_source,
                "hot_stocks": [],
                "items": [],
            }

    def _get_json(url: str) -> tuple[dict[str, Any] | None, int | None, str]:
        try:
            req = urllib.request.Request(url, headers=headers)
            with opener.open(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8", errors="replace")), getattr(resp, "status", 200), ""
        except urllib.error.HTTPError as exc:
            return None, exc.code, str(exc.reason or exc.code)
        except Exception as exc:
            return None, None, type(exc).__name__

    hot_stocks_url = "https://stock.xueqiu.com/v5/stock/hot_stock/list.json?size=20&type=10"
    timeline_url = "https://xueqiu.com/v4/statuses/public_timeline_by_category.json?since_id=-1&max_id=-1&count=20&category=-1"

    hs_data, hs_code, hs_err = _get_json(hot_stocks_url)
    tl_data, tl_code, tl_err = _get_json(timeline_url)

    hot_stocks: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []

    for idx, s in enumerate(((hs_data or {}).get("data") or {}).get("items") or [], start=1):
        sym = str(s.get("code") or s.get("symbol") or "").strip()
        name = str(s.get("name") or "").strip()
        if not sym or not name:
            continue
        pct = s.get("percent")
        pct_num = float(pct) if isinstance(pct, (int, float)) else 0.0
        heat = int(_clamp(95 - (idx - 1) * 3, 45, 98))
        stock_title = f"雪球热股 #{idx} {name}({sym}) {pct_num:+.2f}%"
        tickers = extract_tickers(f"{sym} {name}")
        if sym not in tickers:
            tickers = [sym] + tickers
        topics = _classify_topics(f"{name} {sym}", tickers)
        hot_stocks.append(
            {
                "rank": idx,
                "symbol": sym,
                "name": name,
                "current": s.get("current"),
                "percent": pct_num,
                "value": s.get("value"),
                "increment": s.get("increment"),
                "url": f"https://xueqiu.com/S/{urllib.parse.quote(sym)}",
            }
        )
        if idx <= 8:
            items.append(
                {
                    "platform": "雪球",
                    "channel": "xueqiu",
                    "title": stock_title,
                    "summary": stock_title,
                    "sentiment": "人气榜参考",
                    "kind": "quote_rank",
                    "heat": heat,
                    "tickers": tickers[:4],
                    "topics": topics,
                    "url": f"https://xueqiu.com/S/{urllib.parse.quote(sym)}",
                }
            )

    for raw_item in (tl_data or {}).get("list") or []:
        post: dict[str, Any] = {}
        raw_payload = raw_item.get("data")
        if isinstance(raw_payload, str):
            try:
                post = json.loads(raw_payload)
            except json.JSONDecodeError:
                post = {}
        elif isinstance(raw_payload, dict):
            post = raw_payload
        if not post:
            continue
        title = _strip_html(str(post.get("title") or ""))
        body = _strip_html(str(post.get("text") or post.get("description") or ""))
        display_title = title or _truncate(body, 68)
        if not display_title:
            continue
        combined = f"{title} {body}".strip()
        likes = int(post.get("like_count") or 0)
        replies = int(post.get("reply_count") or 0)
        heat = int(_clamp(45 + min(35, likes * 0.15) + min(20, replies * 0.3), 35, 96))
        sent_score, sent_label = _score_text_sentiment(combined)
        tickers = extract_tickers(combined)
        topics = _classify_topics(combined, tickers)
        target = str(post.get("target") or "").strip()
        url = f"https://xueqiu.com{target}" if target.startswith("/") else _safe_http_url(target)
        items.append(
            {
                "platform": "雪球",
                "channel": "xueqiu",
                "title": _truncate(display_title, 76),
                "summary": _truncate(body or display_title, 140),
                "sentiment": sent_label,
                "score": sent_score,
                "heat": heat,
                "tickers": tickers,
                "topics": topics,
                "url": url or "https://xueqiu.com/",
            }
        )

    auth_blocked = hs_code in (401, 403) or tl_code in (401, 403)
    if hot_stocks and tl_data and items:
        status = "实时在线"
        post_cnt = max(0, len(items) - min(8, len(hot_stocks)))
        note = f"人气榜 {len(hot_stocks)} 只 · 热帖 {post_cnt} 条"
    elif hot_stocks or items:
        status = "需登录态" if tl_code in (401, 403) else "部分受限"
        note = (
            f"人气榜 {len(hot_stocks)} 只可用；热帖需登录态（配置 xueqiu_cookie 解锁）"
            if tl_code in (401, 403)
            else f"部分端点可用（人气榜 {len(hot_stocks)} 只，热帖异常 {tl_err or tl_code}）"
        )
    elif auth_blocked:
        status = "需登录态"
        note = "需登录态（配置 ~/.agent-reach/config.yaml 的 xueqiu_cookie 或浏览器登录雪球）"
    else:
        status = "部分受限"
        note = f"雪球接口暂不可用（{hs_err or tl_err or '空响应'}）"

    return {
        "id": "xueqiu",
        "name": "雪球社区",
        "status": status,
        "count": len(items),
        "note": note,
        "cookie_source": cookie_source,
        "hot_stocks": hot_stocks[:20],
        "items": items[:24],
    }


def _http_get_json(url: str, *, timeout: int = 6, headers: dict[str, str] | None = None) -> dict[str, Any]:
    req_headers = {"User-Agent": _UA, "Accept": "application/json, text/plain, */*"}
    if headers:
        req_headers.update(headers)
    if requests is not None:
        resp = requests.get(url, headers=req_headers, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    req = urllib.request.Request(url, headers=req_headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


_BAIDU_LIFESTYLE_NOISE_RE = re.compile(
    r"(?:住简陋房|老板|员工|工资|婚姻|情感|美食|手机(?:测评|拍照)|汽车(?:降价|内饰)|网红|景区|机票|酒店|奶茶|外卖)"
)


def _parse_real_heat(extra: Any, rank: int, *, is_finance_native: bool) -> int:
    """Derive heat from platform-supplied numerical metrics (e.g. '1818 万热度') before rank fallback."""
    if isinstance(extra, dict):
        info = str(extra.get("info") or "")
        m_wan = re.search(r"(\d+(?:\.\d+)?)\s*万", info)
        if m_wan:
            val_wan = float(m_wan.group(1))
            return int(_clamp(54.0 + min(42.0, val_wan / 40.0), 50.0, 96.0))
        m_raw = re.search(r"\b(\d{5,9})\b", info)
        if m_raw and "202" not in m_raw.group(1)[:3]:
            val_wan = float(m_raw.group(1)) / 10000.0
            return int(_clamp(54.0 + min(42.0, val_wan / 40.0), 50.0, 96.0))
    base = 88.0 if is_finance_native else 84.0
    return int(_clamp(base - (rank - 1) * 1.6, 42.0, 88.0))


def _fetch_cn_communities_channel(*, timeout: int = 6) -> dict[str, Any]:
    """Fetch Chinese finance & mass community hotlists + Baidu Finance board and compute mass penetration."""
    items: list[dict[str, Any]] = []
    ok_sources = 0
    seen_titles: set[str] = set()
    mass_stats = {
        "total_slots": 0,
        "finance_hits": 0,
        "ahk_hits": 0,
        "platforms": [],
    }

    def _fetch_one(src_id: str, label: str, is_finance_native: bool) -> tuple[str, str, bool, int, int, int, list[dict[str, Any]]]:
        url = f"{NEWSNOW_API_URL}?id={urllib.parse.quote(src_id)}&latest"
        try:
            data = _http_get_json(url, timeout=timeout)
            if data.get("status") not in ("success", "cache"):
                return src_id, label, False, 0, 0, 0, []
            raw_list = data.get("items") or []
            rows: list[dict[str, Any]] = []
            ahk_hits = 0
            for rank, raw in enumerate(raw_list, start=1):
                if not isinstance(raw, dict):
                    continue
                title = str(raw.get("title") or "").strip()
                if not title:
                    continue
                extra = raw.get("extra") if isinstance(raw.get("extra"), dict) else {}
                hover = _strip_html(str(extra.get("hover") or ""))
                has_fin = bool(_CN_FINANCE_KEYWORDS_RE.search(title))
                has_noise = bool(_ENTERTAINMENT_NOISE_RE.search(title))
                if is_finance_native:
                    if has_noise and not has_fin:
                        continue
                else:
                    if not has_fin or (has_noise and not extract_tickers(title)):
                        continue
                norm = re.sub(r"\W+", "", title.lower())
                if not norm:
                    continue
                scored_text = f"{title} {hover}".strip() if hover else title
                tickers = extract_tickers(scored_text)
                topics = _classify_topics(scored_text, tickers)
                if "A股与港股" in topics:
                    ahk_hits += 1
                sent_score, sent_label = _score_text_sentiment(scored_text)
                heat = _parse_real_heat(extra, rank, is_finance_native=is_finance_native)
                rows.append(
                    {
                        "norm": norm,
                        "platform": label,
                        "channel": "cn_communities",
                        "title": _truncate(title, 76),
                        "summary": _truncate(hover or title, 140),
                        "sentiment": sent_label,
                        "score": sent_score,
                        "heat": heat,
                        "tickers": tickers,
                        "topics": topics,
                        "url": _safe_http_url(str(raw.get("url") or "")) or f"https://newsnow.busiyi.world/?id={src_id}",
                    }
                )
            slot_cnt = len(raw_list) if src_id in MASS_HOTLIST_IDS else 0
            fin_cnt = len(rows) if src_id in MASS_HOTLIST_IDS else 0
            ahk_cnt = ahk_hits if src_id in MASS_HOTLIST_IDS else 0
            return src_id, label, True, slot_cnt, fin_cnt, ahk_cnt, rows
        except Exception:
            return src_id, label, False, 0, 0, 0, []

    def _fetch_baidu_finance() -> tuple[str, str, bool, int, int, int, list[dict[str, Any]]]:
        try:
            data = _http_get_json(BAIDU_FINANCE_BOARD_URL, timeout=timeout)
            cards = ((data.get("data") or {}).get("cards")) or []
            raw_list: list[dict[str, Any]] = []
            if cards and isinstance(cards[0], dict):
                c0 = (cards[0].get("content") or [])
                if c0 and isinstance(c0[0], dict):
                    raw_list = c0[0].get("content") or []
            if not raw_list:
                return "baidu-finance", "百度财经热搜", False, 0, 0, 0, []
            rows: list[dict[str, Any]] = []
            for rank, raw in enumerate(raw_list[:18], start=1):
                if not isinstance(raw, dict):
                    continue
                word = str(raw.get("word") or "").strip()
                desc = str(raw.get("desc") or "").strip()
                if not word:
                    continue
                if _BAIDU_LIFESTYLE_NOISE_RE.search(word) or _ENTERTAINMENT_NOISE_RE.search(word):
                    continue
                combined = f"{word} {desc}".strip()
                if not _CN_FINANCE_KEYWORDS_RE.search(combined) and not extract_tickers(combined):
                    continue
                norm = re.sub(r"\W+", "", word.lower())
                if not norm:
                    continue
                tickers = extract_tickers(combined)
                sent_score, sent_label = _score_text_sentiment(combined)
                heat = int(_clamp(86.0 - (rank - 1) * 1.8, 46.0, 88.0))
                rows.append(
                    {
                        "norm": norm,
                        "platform": "百度财经热搜",
                        "channel": "cn_communities",
                        "title": _truncate(word, 76),
                        "summary": _truncate(desc or word, 140),
                        "sentiment": sent_label,
                        "score": sent_score,
                        "heat": heat,
                        "tickers": tickers,
                        "topics": _classify_topics(combined, tickers),
                        "url": _safe_http_url(str(raw.get("rawUrl") or raw.get("url") or "")) or "https://top.baidu.com/board?tab=finance",
                    }
                )
            return "baidu-finance", "百度财经热搜", True, 0, 0, 0, rows[:10]
        except Exception:
            return "baidu-finance", "百度财经热搜", False, 0, 0, 0, []

    with ThreadPoolExecutor(max_workers=len(CN_COMMUNITY_SOURCES) + 1) as pool:
        futs = [pool.submit(_fetch_one, sid, lbl, nat) for sid, lbl, nat in CN_COMMUNITY_SOURCES]
        futs.append(pool.submit(_fetch_baidu_finance))
        for fut in as_completed(futs):
            sid, lbl, ok, slot_cnt, fin_cnt, ahk_cnt, rows = fut.result()
            if ok and sid != "baidu-finance":
                ok_sources += 1
            if slot_cnt > 0:
                mass_stats["total_slots"] += slot_cnt
                mass_stats["finance_hits"] += fin_cnt
                mass_stats["ahk_hits"] += ahk_cnt
                if fin_cnt > 0:
                    mass_stats["platforms"].append(lbl.replace("热搜", "").replace("热榜", ""))
            for row in rows:
                norm = row.pop("norm", "")
                if norm in seen_titles:
                    continue
                seen_titles.add(norm)
                items.append(row)

    items.sort(key=lambda x: x.get("heat", 0), reverse=True)
    if ok_sources >= 3 and items:
        status = "实时在线"
        note = f"华尔街见闻/财联社/百度/头条等 {ok_sources}/{len(CN_COMMUNITY_SOURCES)} 路热榜，过滤后 {len(items)} 条财经热议"
    elif ok_sources >= 1:
        status = "部分受限"
        note = f"{ok_sources}/{len(CN_COMMUNITY_SOURCES)} 路热榜可用，过滤后 {len(items)} 条财经热议"
    else:
        status = "部分受限"
        note = "中文热榜接口暂不可达，已降级"

    return {
        "id": "cn_communities",
        "name": "全网财经热榜",
        "status": status,
        "count": len(items),
        "note": note,
        "sources_ok": ok_sources,
        "mass_penetration": mass_stats,
        "items": items[:38],
    }


def _load_latest_reddit_archive(hermes_home: Path) -> tuple[str, list[dict[str, Any]]]:
    """Read newest news/reddit-insights/*.md archive as supplemental reference."""
    insights_dir = hermes_home / "scripts" / "news" / "reddit-insights"
    if not insights_dir.exists():
        return "", []
    md_files = sorted(insights_dir.glob("*_reddit_insights.md"), reverse=True)
    if not md_files:
        return "", []
    latest = md_files[0]
    date_match = re.match(r"(\d{4}-\d{2}-\d{2})_reddit_insights\.md", latest.name)
    archive_date = date_match.group(1) if date_match else ""
    try:
        text = latest.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return archive_date, []

    items: list[dict[str, Any]] = []
    current_title = ""
    current_en = ""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("- **") and "**" in line[4:]:
            end_idx = line.find("**", 4)
            current_title = line[4:end_idx].strip()
            current_en = ""
        elif line.startswith("- EN:") and current_title:
            current_en = line[5:].strip()
        elif line.startswith("- r/") and current_title:
            up_m = re.search(r"(\d+)↑", line)
            upvotes = int(up_m.group(1)) if up_m else 30
            combined = f"{current_title} {current_en}".strip()
            if _REDDIT_NOISE_RE.search(combined):
                current_title = ""
                continue
            tickers = extract_tickers(combined)
            sent_score, sent_label = _score_text_sentiment(combined)
            heat = int(_clamp(42 + min(45, upvotes * 0.06), 40, 88))
            prefix = f"[归档 {archive_date}] " if archive_date else "[归档] "
            items.append(
                {
                    "platform": "Reddit归档",
                    "channel": "reddit",
                    "title": _truncate(prefix + current_title, 76),
                    "summary": _truncate(current_en or current_title, 140),
                    "sentiment": sent_label,
                    "score": sent_score,
                    "heat": heat,
                    "tickers": tickers,
                    "topics": _classify_topics(combined, tickers),
                    "url": "https://www.reddit.com/r/wallstreetbets/",
                    "is_archive": True,
                }
            )
            current_title = ""
            if len(items) >= 8:
                break
    return archive_date, items


def _fetch_hn_supplement(*, timeout: int = 6) -> list[dict[str, Any]]:
    """Fetch public Hacker News front-page stories filtered to AI/tech/macro/finance."""
    try:
        data = _http_get_json(HN_FRONT_PAGE_URL, timeout=timeout)
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for hit in data.get("hits") or []:
        title = str(hit.get("title") or "").strip()
        if not title or not _HN_FINANCE_TECH_RE.search(title):
            continue
        points = int(hit.get("points") or 0)
        comments = int(hit.get("num_comments") or 0)
        oid = str(hit.get("objectID") or "").strip()
        url = _safe_http_url(str(hit.get("url") or "")) or (
            f"https://news.ycombinator.com/item?id={oid}" if oid else "https://news.ycombinator.com/"
        )
        tickers = extract_tickers(title)
        sent_score, sent_label = _score_text_sentiment(title)
        heat = int(_clamp(45 + min(35, points * 0.12) + min(15, comments * 0.15), 42, 92))
        out.append(
            {
                "platform": "Hacker News",
                "channel": "reddit",
                "title": _truncate(title, 76),
                "summary": f"HN {points}赞 · {comments}评论 · {title}",
                "sentiment": sent_label,
                "score": sent_score,
                "heat": heat,
                "tickers": tickers,
                "topics": _classify_topics(title, tickers),
                "url": url,
            }
        )
        if len(out) >= 10:
            break
    return out


EXA_MCP_URL = "https://mcp.exa.ai/mcp"
DDG_HTML_URL = "https://html.duckduckgo.com/html/"

_EXA_NOISE_TITLE_RE = re.compile(
    r"^(?:blocked|just a moment|sign in|login|single sign-on)$"
    r"|whoa there,?\s*pardner|校园招聘|实习生招聘|帮助中心",
    re.IGNORECASE,
)
_XHS_BOILERPLATE_LINE_RE = re.compile(
    r"^(?:登录|我|发现|发布|通知|关注|粉丝|获赞与收藏|加载中|去首页|登录后评论|LIVE|\d+/\d+|\d+|…|\.{3}|小红书号[:：].*|IP属地[:：].*|沪ICP备.*|营业执照.*)$"
)


def _http_post_text(
    url: str,
    *,
    json_payload: dict[str, Any],
    headers: dict[str, str] | None = None,
    timeout: int = 8,
) -> tuple[int, str]:
    req_headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "User-Agent": _UA,
    }
    if headers:
        req_headers.update(headers)
    body = json.dumps(json_payload, ensure_ascii=False).encode("utf-8")
    if curl_requests is not None:
        resp = curl_requests.post(url, data=body, headers=req_headers, impersonate="chrome", timeout=timeout)
        return int(resp.status_code), str(resp.text or "")
    if requests is not None:
        resp = requests.post(url, data=body, headers=req_headers, timeout=timeout)
        return int(resp.status_code), str(resp.text or "")
    req = urllib.request.Request(url, data=body, headers=req_headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return int(getattr(resp, "status", 200)), resp.read().decode("utf-8", errors="replace")


def _http_get_text(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: int = 6,
    encoding: str | None = None,
) -> tuple[int, str]:
    req_headers = {"User-Agent": _UA, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
    if headers:
        req_headers.update(headers)
    if curl_requests is not None:
        resp = curl_requests.get(url, headers=req_headers, impersonate="chrome", timeout=timeout)
        txt = resp.content.decode(encoding, errors="replace") if encoding else str(resp.text or "")
        return int(resp.status_code), txt
    if requests is not None:
        resp = requests.get(url, headers=req_headers, timeout=timeout)
        txt = resp.content.decode(encoding, errors="replace") if encoding else str(resp.text or "")
        return int(resp.status_code), txt
    req = urllib.request.Request(url, headers=req_headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return int(getattr(resp, "status", 200)), resp.read().decode(encoding or "utf-8", errors="replace")


def _get_text_with_encoding(url: str, *, encoding: str | None = None, timeout: int = 6) -> tuple[int, str]:
    """Call _http_get_text while remaining compatible with 1-kwarg test monkeypatches."""
    if encoding:
        try:
            return _http_get_text(url, timeout=timeout, encoding=encoding)
        except TypeError:
            pass
    return _http_get_text(url, timeout=timeout)


def _parse_exa_mcp_blocks(raw_text: str) -> list[dict[str, str]]:
    """Parse Exa MCP SSE or JSON text blocks into structured search hits, stripping forbidden placeholders."""
    text_chunks: list[str] = []
    payloads: list[dict[str, Any]] = []
    stripped = (raw_text or "").strip()
    if stripped.startswith("{"):
        try:
            obj = json.loads(stripped)
            if isinstance(obj, dict):
                payloads.append(obj)
        except json.JSONDecodeError:
            pass
    if not payloads:
        for line in stripped.splitlines():
            s = line.strip()
            if s.startswith("data:"):
                raw_json = s[5:].strip()
                if raw_json:
                    try:
                        obj = json.loads(raw_json)
                        if isinstance(obj, dict):
                            payloads.append(obj)
                    except json.JSONDecodeError:
                        continue

    for obj in payloads:
        contents = ((obj.get("result") or {}).get("content")) or []
        for item in contents:
            if isinstance(item, dict) and item.get("type") == "text" and item.get("text"):
                text_chunks.append(str(item["text"]))

    if not text_chunks:
        return []

    hits: list[dict[str, str]] = []
    combined_md = "\n---\n".join(text_chunks)
    for block in re.split(r"\n-{3,}\n", combined_md):
        blk = block.strip()
        if not blk:
            continue
        title = ""
        url = ""
        author = ""
        published = ""
        highlights_lines: list[str] = []
        in_highlights = False
        for ln in blk.splitlines():
            s = ln.strip()
            if s.startswith("Title:") and not title:
                title = s[6:].strip()
            elif s.startswith("URL:") and not url:
                url = s[4:].strip()
            elif s.startswith("Published:") and not published:
                pub = s[10:].strip()
                if pub.upper() != "N/A":
                    published = pub
            elif s.startswith("Author:") and not author:
                author = s[7:].strip()
            elif s.startswith("Highlights:"):
                in_highlights = True
                rest = s[11:].strip()
                if rest:
                    highlights_lines.append(rest)
            elif in_highlights:
                highlights_lines.append(ln)
        if not title or _EXA_NOISE_TITLE_RE.search(title):
            continue
        safe_url = _safe_http_url(url)
        if not safe_url:
            continue
        highlights_raw = "\n".join(highlights_lines).strip()
        highlights_clean = re.sub(r"\bN/A\b", "—", highlights_raw, flags=re.IGNORECASE)
        hits.append(
            {
                "title": scrub_forbidden(title),
                "url": safe_url,
                "author": scrub_forbidden(author),
                "published": scrub_forbidden(published),
                "highlights": scrub_forbidden(highlights_clean),
            }
        )
    return hits


def _exa_mcp_search(
    query: str,
    objective: str,
    *,
    num_results: int = 6,
    timeout: int = 8,
) -> list[dict[str, str]]:
    """Query free public Exa MCP search without requiring any API key."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "web_search_exa",
            "arguments": {
                "query": query,
                "objective": objective,
                "numResults": int(num_results),
            },
        },
    }
    try:
        status, body = _http_post_text(EXA_MCP_URL, json_payload=payload, timeout=timeout)
        if status != 200 or not body:
            return []
        return _parse_exa_mcp_blocks(body)
    except Exception:
        return []


def _extract_ddg_target_url(href: str) -> str:
    raw = html.unescape(str(href or "").strip())
    if not raw:
        return ""
    if raw.startswith("//"):
        raw = "https:" + raw
    elif raw.startswith("/l/?"):
        raw = "https://duckduckgo.com" + raw
    parsed = urllib.parse.urlparse(raw)
    if "duckduckgo.com" in parsed.netloc.lower() and parsed.path.startswith("/l/"):
        qs = urllib.parse.parse_qs(parsed.query)
        uddg = (qs.get("uddg") or [""])[0]
        return _safe_http_url(urllib.parse.unquote(uddg))
    return _safe_http_url(raw)


def _ddg_html_search_xhs(queries: list[str] | tuple[str, ...], *, timeout: int = 6) -> list[dict[str, Any]]:
    """Search public DuckDuckGo HTML for individual Xiaohongshu /explore/ finance notes without login."""
    out: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    xhs_fin_re = re.compile(
        r"美股|纳指|标普|道指|英伟达|特斯拉|苹果|微软|黄金|金价|白银|原油|A股|大A|沪指|创业板|港股|恒生|恒科|比特币|BTC|ETH|理财|基金|ETF|定投|定抛|国债|降息|通胀|PPI|CPI|实盘|回本|抄底|套牢|算力",
        re.IGNORECASE,
    )
    for q in queries:
        url = f"{DDG_HTML_URL}?q={urllib.parse.quote(q)}"
        try:
            status, body = _http_get_text(url, timeout=timeout)
        except Exception:
            continue
        if status != 200 or not body:
            continue
        pairs: list[tuple[str, str]] = []
        if BeautifulSoup is not None:
            soup = BeautifulSoup(body, "html.parser")
            for a in soup.select("a.result__a"):
                pairs.append((a.get_text(" ", strip=True), str(a.get("href") or "")))
        else:
            for m in re.finditer(r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', body, re.DOTALL):
                pairs.append((_strip_html(m.group(2)), m.group(1)))

        for raw_title, raw_href in pairs:
            target = _extract_ddg_target_url(raw_href)
            if not target:
                continue
            parsed = urllib.parse.urlparse(target)
            host = parsed.netloc.lower()
            path_low = parsed.path.lower()
            if "xiaohongshu.com" not in host:
                continue
            if "/explore/" not in path_low and "/discovery/item/" not in path_low:
                continue
            clean_title = re.sub(r"\s*[-–|·]\s*小红书\s*$", "", str(raw_title or "")).strip()
            if not clean_title or _EXA_NOISE_TITLE_RE.search(clean_title):
                continue
            if _XHS_SPAM_RE.search(clean_title) or _XHS_FALSE_MATCH_RE.search(clean_title) or _ENTERTAINMENT_NOISE_RE.search(clean_title):
                continue
            if not xhs_fin_re.search(clean_title):
                continue
            canon = target.split("?", 1)[0]
            if canon in seen_urls:
                continue
            seen_urls.add(canon)
            tickers = extract_tickers(clean_title)
            sent_score, sent_label = _score_text_sentiment(clean_title)
            out.append(
                {
                    "platform": "小红书",
                    "channel": "xiaohongshu",
                    "title": _truncate(clean_title, 76),
                    "summary": _truncate(f"小红书热议笔记：{clean_title}", 140),
                    "sentiment": sent_label,
                    "score": sent_score,
                    "heat": 74,
                    "tickers": tickers,
                    "topics": _classify_topics(clean_title, tickers),
                    "url": target,
                }
            )
    return out


def _fetch_reddit_no_login_via_exa(*, timeout: int = 8) -> list[dict[str, Any]]:
    """Fetch recent Reddit finance subreddit posts without login via Exa free semantic search."""
    hits = _exa_mcp_search(
        "site:reddit.com/r/wallstreetbets OR site:reddit.com/r/stocks OR site:reddit.com/r/investing OR site:reddit.com/r/options NVDA AI Fed rates gold oil Bitcoin 2026",
        "Rank recent 2026 posts from reddit.com/r/wallstreetbets, r/stocks, r/investing, or r/options discussing US equities, AI chips, Fed rates, oil, gold, or crypto. Exclude non-financial subreddits and daily discussion threads.",
        num_results=8,
        timeout=timeout,
    )
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for h in hits:
        url = h["url"]
        sub_m = re.search(r"reddit\.com/r/([A-Za-z0-9_]+)", url, re.IGNORECASE)
        if not sub_m:
            continue
        sub_name = sub_m.group(1)
        if sub_name.lower() not in {"wallstreetbets", "stocks", "investing", "options", "valueinvesting", "securityanalysis", "stockmarket", "economics"}:
            continue
        title = re.sub(rf"\s*:\s*r/{re.escape(sub_name)}\s*$", "", h["title"], flags=re.IGNORECASE).strip()
        highlights = h.get("highlights") or ""
        combined = f"{title} {highlights}".strip()
        if not title or _REDDIT_NOISE_RE.search(combined):
            continue
        norm = re.sub(r"\W+", "", title.lower())
        if not norm or norm in seen:
            continue
        seen.add(norm)
        pts_m = re.search(r"(\d+)\s+points?\s*[·•]\s*(\d+)\s+comments?", highlights, re.IGNORECASE)
        upvotes = int(pts_m.group(1)) if pts_m else 160
        comments = int(pts_m.group(2)) if pts_m else 40
        heat = int(_clamp(46 + min(36, upvotes * 0.04) + min(14, comments * 0.06), 45, 94))
        tickers = extract_tickers(combined)
        sent_score, sent_label = _score_text_sentiment(combined)
        snippet_clean = re.sub(r"^#\s*[^\n]+\n+", "", highlights).strip()
        items.append(
            {
                "platform": f"r/{sub_name}",
                "channel": "reddit",
                "title": _truncate(title, 76),
                "summary": _truncate(snippet_clean or title, 140),
                "sentiment": sent_label,
                "score": sent_score,
                "heat": heat,
                "tickers": tickers,
                "topics": _classify_topics(combined, tickers),
                "url": url,
            }
        )
    return items[:10]


def _fetch_xiaohongshu_no_login(*, timeout: int = 8) -> list[dict[str, Any]]:
    """Fetch public Xiaohongshu finance notes without login via DuckDuckGo explore search + Exa MCP."""
    fin_note_re = re.compile(
        r"美股|纳指|标普|英伟达|特斯拉|苹果|黄金|金价|A股|港股|恒生|恒科|创业板|科创|比特币|BTC|ETH|理财|基金|ETF|定投|期权|关税|降息|巴菲特|实盘|回本|抄底|亏|牛|泡沫|算力|中概",
        re.IGNORECASE,
    )
    items: list[dict[str, Any]] = []
    seen_norms: set[str] = set()

    def _push(row: dict[str, Any]) -> None:
        norm = re.sub(r"\W+", "", str(row.get("title") or "").lower())
        if not norm or norm in seen_norms:
            return
        seen_norms.add(norm)
        items.append(row)

    with ThreadPoolExecutor(max_workers=2) as pool:
        ddg_fut = pool.submit(
            _ddg_html_search_xhs,
            (
                "site:xiaohongshu.com/explore 美股 纳指 标普500 实盘 评论",
                "site:xiaohongshu.com/explore 黄金 基金 定投 见顶 比特币 散户",
                "site:xiaohongshu.com/explore A股 港股 恒生科技 基金 实盘 牛市",
            ),
            timeout=min(7, timeout),
        )
        exa_fut = pool.submit(
            _exa_mcp_search,
            "site:xiaohongshu.com 小红书 散户 实盘 评论 美股 纳指 黄金 A股 港股 恒生科技 创业板 基金 比特币 2026",
            "Only rank genuine retail investor posts, position discussions, and comment threads on xiaohongshu.com about gold, Nasdaq/US stocks, A-shares, Hong Kong stocks, funds, or crypto. Exclude courses, bank promos, beauty, lifestyle, and false matches like 黄金时机.",
            num_results=6,
            timeout=timeout,
        )
        for row in ddg_fut.result():
            _push(row)
        exa_hits = exa_fut.result()

    for h in exa_hits:
        url = h["url"]
        if "xiaohongshu.com" not in url.lower():
            continue
        raw_title = re.sub(r"\s*[-–|·]\s*小红书\s*$", "", h["title"]).strip()
        highlights = h.get("highlights") or ""
        if "/explore/" in url.lower() or "/discovery/item/" in url.lower():
            combined = f"{raw_title} {highlights}"
            if (
                fin_note_re.search(combined)
                and not _XHS_SPAM_RE.search(combined)
                and not _XHS_FALSE_MATCH_RE.search(combined)
                and not _ENTERTAINMENT_NOISE_RE.search(combined)
            ):
                tks = extract_tickers(combined)
                s_sc, s_lbl = _score_text_sentiment(combined)
                _push(
                    {
                        "platform": "小红书",
                        "channel": "xiaohongshu",
                        "title": _truncate(raw_title, 76),
                        "summary": _truncate(highlights or raw_title, 140),
                        "sentiment": s_lbl,
                        "score": s_sc,
                        "heat": 70,
                        "tickers": tks,
                        "topics": _classify_topics(combined, tks),
                        "url": url,
                    }
                )
            continue

        # Creator profile page: extract individual finance note headlines listed on the creator profile
        creator = raw_title or h.get("author") or "财经博主"
        if _XHS_FALSE_MATCH_RE.search(creator) or _XHS_SPAM_RE.search(creator):
            continue
        extracted_lines = 0
        for ln in highlights.splitlines():
            cand = re.sub(r"^[\-\*•📊📈🚀💰✨🔥\s]+", "", ln).strip()
            if not cand or len(cand) < 8 or len(cand) > 76:
                continue
            if _XHS_BOILERPLATE_LINE_RE.match(cand) or cand == creator:
                continue
            if _XHS_SPAM_RE.search(cand) or _XHS_FALSE_MATCH_RE.search(cand) or _ENTERTAINMENT_NOISE_RE.search(cand):
                continue
            if not fin_note_re.search(cand):
                continue
            tks = extract_tickers(cand)
            s_sc, s_lbl = _score_text_sentiment(cand)
            _push(
                {
                    "platform": "小红书",
                    "channel": "xiaohongshu",
                    "title": _truncate(f"{creator}：{cand}", 76),
                    "summary": _truncate(f"小红书 {creator} 笔记：{cand}", 140),
                    "sentiment": s_lbl,
                    "score": s_sc,
                    "heat": 66,
                    "tickers": tks,
                    "topics": _classify_topics(cand, tks),
                    "url": url,
                }
            )
            extracted_lines += 1
            if extracted_lines >= 4:
                break

    return items[:14]


def _fetch_reddit_channel(*, user_home: Path, hermes_home: Path, timeout: int = 6) -> dict[str, Any]:
    """Probe rdt CLI (non-blocking) with no-login Exa search + HN + archived Reddit fallback."""
    sub_timeout = max(5, min(8, int(timeout)))
    rdt_bin = shutil.which("rdt")
    cred_exists = (user_home / ".config" / "rdt-cli" / "credential.json").exists()
    authenticated = False

    if rdt_bin:
        try:
            proc = subprocess.run(
                [rdt_bin, "status", "--json"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=sub_timeout,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                payload = json.loads(proc.stdout)
                authenticated = bool((payload.get("data") or {}).get("authenticated", False))
        except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
            authenticated = False

    if rdt_bin and authenticated:
        items: list[dict[str, Any]] = []
        for sub in REDDIT_SUBREDDITS:
            try:
                proc = subprocess.run(
                    [rdt_bin, "sub", sub, "--sort", "hot", "--limit", "10", "--json"],
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=sub_timeout,
                )
                if proc.returncode != 0 or not proc.stdout.strip():
                    continue
                raw = json.loads(proc.stdout)
                posts = raw.get("data") if isinstance(raw, dict) else raw
                if isinstance(posts, dict):
                    posts = posts.get("posts") or posts.get("items") or posts.get("children") or []
                if not isinstance(posts, list):
                    continue
                for post in posts:
                    if not isinstance(post, dict):
                        continue
                    title = str(post.get("title") or "").strip()
                    body = str(post.get("selftext") or post.get("text") or "").strip()
                    combined = f"{title} {body}".strip()
                    if not title or _REDDIT_NOISE_RE.search(combined):
                        continue
                    upvotes = int(post.get("ups") or post.get("score") or post.get("upvotes") or 0)
                    comments = int(post.get("num_comments") or post.get("comments") or 0)
                    tickers = extract_tickers(combined)
                    if not tickers and upvotes < 100:
                        continue
                    sent_score, sent_label = _score_text_sentiment(combined)
                    heat = int(_clamp(45 + min(40, upvotes * 0.05) + min(15, comments * 0.08), 40, 96))
                    url = _safe_http_url(str(post.get("url") or "")) or "https://www.reddit.com/"
                    items.append(
                        {
                            "platform": f"r/{sub}",
                            "channel": "reddit",
                            "title": _truncate(title, 76),
                            "summary": _truncate(body or title, 140),
                            "sentiment": sent_label,
                            "score": sent_score,
                            "heat": heat,
                            "tickers": tickers,
                            "topics": _classify_topics(combined, tickers),
                            "url": url,
                        }
                    )
            except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
                continue

        if items:
            items.sort(key=lambda x: x.get("heat", 0), reverse=True)
            return {
                "id": "reddit",
                "name": "Reddit 社区",
                "status": "实时在线",
                "count": len(items),
                "note": f"已抓 r/wallstreetbets 等 4 个板块 {len(items)} 条热帖",
                "authenticated": True,
                "credential_present": cred_exists,
                "items": items[:20],
            }

    with ThreadPoolExecutor(max_workers=2) as pool:
        exa_fut = pool.submit(_fetch_reddit_no_login_via_exa, timeout=max(6, timeout))
        hn_fut = pool.submit(_fetch_hn_supplement, timeout=timeout)
        exa_items = exa_fut.result()
        hn_items = hn_fut.result()

    if exa_items:
        combined_items = exa_items + hn_items
        return {
            "id": "reddit",
            "name": "Reddit 社区",
            "status": "免登直采",
            "count": len(combined_items),
            "note": f"Exa 语义搜索直采 Reddit {len(exa_items)} 条 + HN {len(hn_items)} 条（免登录生效）",
            "authenticated": False,
            "credential_present": cred_exists,
            "items": combined_items[:18],
        }

    archive_date, archive_items = _load_latest_reddit_archive(hermes_home)
    combined_items = hn_items + archive_items
    archive_hint = f"与历史归档({archive_date})" if archive_date else ""
    note = f"需登录态（运行 rdt login 解锁实时抓帖）；已接入 Hacker News 公开热议{archive_hint}"
    return {
        "id": "reddit",
        "name": "Reddit 社区",
        "status": "需登录态",
        "count": len(combined_items),
        "note": note,
        "authenticated": False,
        "credential_present": cred_exists,
        "items": combined_items[:18],
    }


def _fetch_xhs_note_comments(xhs_bin: str, note_id: str, *, timeout: int = 5) -> list[str]:
    """Fetch top discussion comments for an authenticated Xiaohongshu note without blocking."""
    if not xhs_bin or not note_id:
        return []
    sub_timeout = max(5, min(8, int(timeout)))
    try:
        proc = subprocess.run(
            [xhs_bin, "comments", note_id, "--json"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=sub_timeout,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return []
        raw = json.loads(proc.stdout)
        rows = (
            raw
            if isinstance(raw, list)
            else (raw.get("comments") or raw.get("items") or (raw.get("data") or {}).get("comments") or (raw.get("data") or {}).get("items") or [])
        )
        out: list[str] = []
        for c in rows:
            if not isinstance(c, dict):
                continue
            txt = str(c.get("content") or c.get("text") or "").strip()
            if not txt or len(txt) < 4 or _XHS_SPAM_RE.search(txt):
                continue
            out.append(_truncate(txt, 45))
            if len(out) >= 3:
                break
        return out
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return []


def _fetch_xiaohongshu_channel(*, timeout: int = 6) -> dict[str, Any]:
    """Probe xhs CLI (non-blocking) with no-login search engine fallback when logged out."""
    sub_timeout = max(5, min(8, int(timeout)))
    xhs_bin = shutil.which("xhs")
    authenticated = False
    if xhs_bin:
        try:
            proc = subprocess.run(
                [xhs_bin, "status"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=min(5, sub_timeout),
            )
            out = ((proc.stdout or "") + "\n" + (proc.stderr or "")).lower()
            if proc.returncode == 0 and "ok: true" in out:
                authenticated = True
        except (subprocess.TimeoutExpired, OSError):
            authenticated = False

    if not authenticated:
        no_login_items = _fetch_xiaohongshu_no_login(timeout=max(6, timeout))
        if no_login_items:
            return {
                "id": "xiaohongshu",
                "name": "小红书理财",
                "status": "免登直采",
                "count": len(no_login_items),
                "note": f"Exa 与网页检索免登直采 {len(no_login_items)} 条理财热点",
                "authenticated": False,
                "items": no_login_items[:14],
            }
        return {
            "id": "xiaohongshu",
            "name": "小红书理财",
            "status": "需登录态",
            "count": 0,
            "note": "需登录态（安装或登录 xhs login 解锁全量；暂未检出免登新帖）" if not xhs_bin else "需登录态（运行 xhs login 解锁全量笔记）",
            "authenticated": False,
            "items": [],
        }

    items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for kw in XHS_KEYWORDS:
        try:
            proc = subprocess.run(
                [xhs_bin, "search", kw, "--sort", "popular", "--json"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=sub_timeout,
            )
            if proc.returncode != 0 or not proc.stdout.strip():
                continue
            raw = json.loads(proc.stdout)
            notes = raw if isinstance(raw, list) else (raw.get("items") or (raw.get("data") or {}).get("items") or [])
            for n in notes:
                if not isinstance(n, dict):
                    continue
                inner = n.get("note_card") or n.get("note") or n
                nid = str(inner.get("note_id") or inner.get("id") or "")
                title = str(inner.get("title") or "").strip()
                desc = str(inner.get("desc") or inner.get("content") or "").strip()
                combined = f"{title} {desc}".strip()
                if not title or _XHS_SPAM_RE.search(combined) or _XHS_FALSE_MATCH_RE.search(combined):
                    continue
                if nid and nid in seen_ids:
                    continue
                if nid:
                    seen_ids.add(nid)
                interact = inner.get("interact_info") or {}
                likes = int(interact.get("liked_count") or inner.get("liked_count") or 0)
                comments_preview = _fetch_xhs_note_comments(xhs_bin, nid, timeout=sub_timeout) if nid else []
                comment_blob = " ".join(comments_preview)
                full_text = f"{combined} {comment_blob}".strip()
                tickers = extract_tickers(f"{kw} {full_text}")
                sent_score, sent_label = _score_text_sentiment(full_text)
                heat = int(_clamp(48 + min(42, likes * 0.04) + min(8, len(comments_preview) * 2), 42, 95))
                url = f"https://www.xiaohongshu.com/explore/{nid}" if nid else "https://www.xiaohongshu.com/"
                summary_base = desc or title
                if comment_blob:
                    summary_base = f"{summary_base} | 热评：{comment_blob}"
                items.append(
                    {
                        "platform": "小红书",
                        "channel": "xiaohongshu",
                        "title": _truncate(title, 76),
                        "summary": _truncate(summary_base, 140),
                        "comments_preview": comments_preview,
                        "sentiment": sent_label,
                        "score": sent_score,
                        "heat": heat,
                        "tickers": tickers,
                        "topics": _classify_topics(f"{kw} {full_text}", tickers),
                        "url": url,
                    }
                )
        except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
            continue

    return {
        "id": "xiaohongshu",
        "name": "小红书理财",
        "status": "实时在线" if items else "部分受限",
        "count": len(items),
        "note": f"已采集黄金/美股/A股/加密等 {len(items)} 条热门理财笔记" if items else "已登录但暂未返回有效笔记",
        "authenticated": True,
        "items": items[:18],
    }


def _format_yi_cny(yi_val: float) -> str:
    if yi_val >= 10000.0:
        return f"{yi_val / 10000.0:.2f}万亿"
    return f"{yi_val:.0f}亿"


def _parse_tencent_quotes(raw_text: str) -> dict[str, dict[str, float | str]]:
    """Parse qt.gtimg.cn semicolon lines into {symbol_key: {name, price, pct, amt_wan}}."""
    parsed: dict[str, dict[str, float | str]] = {}
    for line in str(raw_text or "").splitlines():
        if '="' not in line:
            continue
        lhs, _, rhs = line.partition('="')
        sym_key = lhs.replace("v_", "").strip().lower()
        body = rhs.split('"', 1)[0]
        parts = body.split("~")
        if len(parts) < 38:
            continue
        name = str(parts[1] or "").strip()
        try:
            price = float(parts[3]) if parts[3] else 0.0
        except ValueError:
            price = 0.0
        try:
            pct = float(parts[32]) if parts[32] else 0.0
        except ValueError:
            pct = 0.0
        try:
            amt_wan = float(parts[37]) if parts[37] else 0.0
        except ValueError:
            amt_wan = 0.0
        parsed[sym_key] = {"name": name, "price": price, "pct": pct, "amt_wan": amt_wan}
    return parsed


def _fetch_eastmoney_guba_channel(*, timeout: int = 6) -> dict[str, Any]:
    """Fetch Eastmoney A-share stock popularity Top-15 and enrich names/prices via Tencent quotes."""
    try:
        status, body = _http_post_text(
            EASTMONEY_HOTRANK_URL,
            json_payload={
                "appId": "jg9524909",
                "globalId": "786e4c21-70dc-435a-93bb-38",
                "marketType": "",
                "pageNo": 1,
                "pageSize": 15,
            },
            timeout=timeout,
        )
        if status != 200 or not body.strip():
            return {
                "id": "eastmoney",
                "name": "东财股吧",
                "status": "部分受限",
                "count": 0,
                "note": "东财人气榜暂未返回排行数据",
                "items": [],
            }
        data = json.loads(body)
        raw_rows = data.get("data") or []
    except Exception:
        return {
            "id": "eastmoney",
            "name": "东财股吧",
            "status": "部分受限",
            "count": 0,
            "note": "东财人气榜接口暂不可达",
            "items": [],
        }

    ranked_codes: list[tuple[int, str]] = []
    for idx, row in enumerate(raw_rows[:15], start=1):
        if not isinstance(row, dict):
            continue
        sc = str(row.get("sc") or "").strip().upper()
        if not re.match(r"^(?:SH|SZ|BJ)\d{6}$", sc):
            continue
        rk = int(row.get("rk") or idx)
        ranked_codes.append((rk, sc))

    if not ranked_codes:
        return {
            "id": "eastmoney",
            "name": "东财股吧",
            "status": "部分受限",
            "count": 0,
            "note": "东财人气榜暂无有效标的",
            "items": [],
        }

    quotes_map: dict[str, dict[str, float | str]] = {}
    try:
        q_syms = ",".join(sc.lower() for _rk, sc in ranked_codes)
        q_status, q_body = _get_text_with_encoding(f"{TENCENT_QUOTES_BASE_URL}{q_syms}", encoding="gbk", timeout=timeout)
        if q_status == 200 and q_body:
            quotes_map = _parse_tencent_quotes(q_body)
    except Exception:
        quotes_map = {}

    items: list[dict[str, Any]] = []
    for rk, sc in ranked_codes:
        q = quotes_map.get(sc.lower())
        if not q or not str(q.get("name") or "").strip():
            continue
        name = str(q["name"]).strip()
        pct = float(q.get("pct") or 0.0)
        amt_yi = round(float(q.get("amt_wan") or 0.0) / 10000.0, 1)
        heat = int(_clamp(round(94 - (rk - 1) * 2.4), 48, 96))
        title = f"东财热股 #{rk} {name}({sc}) {pct:+.2f}%"
        summary = f"{title} · 成交 {amt_yi:.1f} 亿" if amt_yi > 0 else title
        tickers = extract_tickers(f"{sc} {name}")
        if sc not in tickers:
            tickers = [sc] + tickers
        items.append(
            {
                "platform": "东财股吧",
                "channel": "eastmoney",
                "title": _truncate(title, 76),
                "summary": _truncate(summary, 140),
                "sentiment": "人气榜参考",
                "kind": "quote_rank",
                "heat": heat,
                "tickers": tickers[:4],
                "topics": _classify_topics(f"{name} {sc}", tickers),
                "url": f"https://guba.eastmoney.com/list,{sc[2:]}.html",
            }
        )

    return {
        "id": "eastmoney",
        "name": "东财股吧",
        "status": "实时在线" if items else "部分受限",
        "count": len(items),
        "note": f"全市场人气前 {len(items)} 只实时个股热度" if items else "东财人气榜行情匹配为空",
        "items": items[:15],
    }


def _fetch_cn_hk_market_pulse(*, timeout: int = 6) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Fetch A-share/HK real-money breadth (turnover, financing, Southbound flow) and Gelonghui desk items."""
    pulse_metrics: dict[str, Any] = {}
    desk_items: list[dict[str, Any]] = []

    def _job_quotes() -> dict[str, Any]:
        out: dict[str, Any] = {}
        try:
            st, body = _get_text_with_encoding(f"{TENCENT_QUOTES_BASE_URL}{TENCENT_MARKET_INDICES}", encoding="gbk", timeout=timeout)
            if st != 200 or not body:
                return out
            qm = _parse_tencent_quotes(body)
            sh = qm.get("sh000001") or {}
            sz = qm.get("sz399001") or {}
            cyb = qm.get("sz399006") or {}
            kc50 = qm.get("sh000688") or {}
            hsi = qm.get("hkhsi") or {}
            hstech = qm.get("hkhstech") or {}
            sh_wan = float(sh.get("amt_wan") or 0.0)
            sz_wan = float(sz.get("amt_wan") or 0.0)
            if sh_wan > 0 and sz_wan > 0:
                a_yi = round((sh_wan + sz_wan) / 10000.0, 1)
                out["a_turnover_yi"] = a_yi
                out["a_turnover_display"] = _format_yi_cny(a_yi)
            if sh:
                out["sh_pct"] = round(float(sh.get("pct") or 0.0), 2)
            if cyb:
                out["cyb_pct"] = round(float(cyb.get("pct") or 0.0), 2)
            if kc50:
                out["kc50_pct"] = round(float(kc50.get("pct") or 0.0), 2)
            hsi_wan = float(hsi.get("amt_wan") or 0.0)
            if hsi_wan > 0:
                out["hk_turnover_yi_hkd"] = round(hsi_wan / 10000.0, 1)
            if hsi:
                out["hsi_pct"] = round(float(hsi.get("pct") or 0.0), 2)
            if hstech:
                out["hstech_pct"] = round(float(hstech.get("pct") or 0.0), 2)
        except Exception:
            pass
        return out

    def _job_rzrq() -> dict[str, Any]:
        out: dict[str, Any] = {}
        try:
            data = _http_get_json(EASTMONEY_RZRQ_URL, timeout=timeout)
            rows = (data.get("result") or {}).get("data") or []
            if rows and isinstance(rows[0], dict):
                r0 = rows[0]
                rzrqye = r0.get("RZRQYE")
                rzye = r0.get("RZYE")
                rqye = r0.get("RQYE")
                if isinstance(rzrqye, (int, float)) and not isinstance(rzrqye, bool) and rzrqye > 0:
                    total_bal: float | None = float(rzrqye)
                elif (
                    isinstance(rzye, (int, float))
                    and not isinstance(rzye, bool)
                    and rzye > 0
                    and isinstance(rqye, (int, float))
                    and not isinstance(rqye, bool)
                    and rqye >= 0
                ):
                    total_bal = float(rzye) + float(rqye)
                else:
                    total_bal = None
                rzmre = r0.get("RZMRE")
                rzjme = r0.get("RZJME")
                rzyezb = r0.get("RZYEZB")
                dt_str = str(r0.get("DIM_DATE") or "")[:10]
                if total_bal is not None and total_bal > 0:
                    bal_yi = round(total_bal / 1e8, 1)
                    out["rzrq_balance_yi"] = bal_yi
                    out["rzrq_balance_display"] = _format_yi_cny(bal_yi)
                if isinstance(rzmre, (int, float)) and rzmre > 0:
                    out["rzrq_buy_yi"] = round(float(rzmre) / 1e8, 1)
                if isinstance(rzjme, (int, float)):
                    out["rzrq_net_buy_yi"] = round(float(rzjme) / 1e8, 1)
                if isinstance(rzyezb, (int, float)) and rzyezb > 0:
                    out["rzrq_float_pct"] = round(float(rzyezb), 2)
                if dt_str:
                    out["rzrq_date"] = dt_str
        except Exception:
            pass
        return out

    def _job_southbound() -> dict[str, Any]:
        out: dict[str, Any] = {}
        try:
            data = _http_get_json(EASTMONEY_SOUTHBOUND_URL, timeout=timeout)
            rows = [r for r in ((data.get("result") or {}).get("data") or []) if isinstance(r, dict) and isinstance(r.get("NET_DEAL_AMT"), (int, float))]
            if rows:
                r0 = rows[0]
                net_yi = round(float(r0["NET_DEAL_AMT"]) / 100.0, 2)
                out["southbound_net_yi_hkd"] = net_yi
                if isinstance(r0.get("DEAL_AMT"), (int, float)) and r0["DEAL_AMT"] > 0:
                    out["southbound_deal_yi_hkd"] = round(float(r0["DEAL_AMT"]) / 100.0, 1)
                dt_str = str(r0.get("TRADE_DATE") or "")[:10]
                if dt_str:
                    out["southbound_date"] = dt_str
                out["southbound_5d_net_yi_hkd"] = round(sum(float(r["NET_DEAL_AMT"]) for r in rows[:5]) / 100.0, 2)
        except Exception:
            pass
        return out

    def _job_gelonghui() -> list[dict[str, Any]]:
        rows_out: list[dict[str, Any]] = []
        try:
            data = _http_get_json(f"{NEWSNOW_API_URL}?id=gelonghui&latest", timeout=timeout)
            if data.get("status") not in ("success", "cache"):
                return rows_out
            for raw in (data.get("items") or [])[:15]:
                if not isinstance(raw, dict):
                    continue
                title = str(raw.get("title") or "").strip()
                if not title or _ENTERTAINMENT_NOISE_RE.search(title):
                    continue
                extra = raw.get("extra") if isinstance(raw.get("extra"), dict) else {}
                info = str(extra.get("info") or "").strip()
                combined = f"{title} {info}".strip()
                tickers = extract_tickers(combined)
                topics = _classify_topics(combined, tickers)
                if "港股" in info and "A股与港股" not in topics:
                    topics.append("A股与港股")
                sent_score, _sent_label = _score_text_sentiment(title)
                dir_val = round(float(sent_score) / 100.0, 2) if sent_score != 0 else 0.0
                rows_out.append(
                    {
                        "author": "格隆汇",
                        "summary": _truncate(title, 88),
                        "url": _safe_http_url(str(raw.get("url") or "")) or "https://www.gelonghui.com/",
                        "dir_val": _clamp(dir_val, -1.0, 1.0),
                        "weight": 0.72,
                        "tickers": tickers,
                        "topics": topics,
                    }
                )
        except Exception:
            pass
        return rows_out

    with ThreadPoolExecutor(max_workers=4) as pool:
        f_q = pool.submit(_job_quotes)
        f_rz = pool.submit(_job_rzrq)
        f_sb = pool.submit(_job_southbound)
        f_glh = pool.submit(_job_gelonghui)
        pulse_metrics.update(f_q.result())
        pulse_metrics.update(f_rz.result())
        pulse_metrics.update(f_sb.result())
        desk_items.extend(f_glh.result())

    return pulse_metrics, desk_items


def collect_forum_pulse(
    *,
    cache_path: Path | str | None = DEFAULT_FORUM_CACHE_PATH,
    force_refresh: bool = False,
    timeout: int = 6,
    user_home: Path | str | None = None,
    hermes_home: Path | str | None = None,
) -> dict[str, Any]:
    """Collect 5-channel community pulse + A/HK market breadth with disk cache (TTL=1800s)."""
    resolved_cache = Path(cache_path) if cache_path is not None else DEFAULT_FORUM_CACHE_PATH
    if not force_refresh and resolved_cache.exists():
        try:
            cached = json.loads(resolved_cache.read_text(encoding="utf-8"))
            cached_at = float(cached.get("cached_at") or 0.0)
            if (time.time() - cached_at) <= FORUM_CACHE_TTL_SECONDS and isinstance(cached.get("channels"), dict):
                cached["from_cache"] = True
                return sanitize_output(cached)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass

    home = Path(user_home) if user_home is not None else _resolve_user_home()
    h_home = Path(hermes_home) if hermes_home is not None else _resolve_hermes_home(home)

    results: dict[str, dict[str, Any]] = {}
    market_pulse: dict[str, Any] = {}
    desk_supplements: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = {
            pool.submit(_fetch_xueqiu_channel, user_home=home, timeout=timeout): "xueqiu",
            pool.submit(_fetch_eastmoney_guba_channel, timeout=timeout): "eastmoney",
            pool.submit(_fetch_cn_communities_channel, timeout=timeout): "cn_communities",
            pool.submit(_fetch_reddit_channel, user_home=home, hermes_home=h_home, timeout=timeout): "reddit",
            pool.submit(_fetch_xiaohongshu_channel, timeout=timeout): "xiaohongshu",
        }
        mp_fut = pool.submit(_fetch_cn_hk_market_pulse, timeout=timeout)
        for fut in as_completed(futs):
            cid = futs[fut]
            try:
                results[cid] = fut.result()
            except Exception as exc:
                results[cid] = {
                    "id": cid,
                    "name": cid,
                    "status": "部分受限",
                    "count": 0,
                    "note": f"采集异常已隔离（{type(exc).__name__}）",
                    "items": [],
                }
        try:
            market_pulse, desk_supplements = mp_fut.result()
        except Exception:
            market_pulse, desk_supplements = {}, []

    cn_mass = (results.get("cn_communities") or {}).get("mass_penetration") or {}
    total_slots = int(cn_mass.get("total_slots") or 0)
    if total_slots > 0:
        fin_hits = int(cn_mass.get("finance_hits") or 0)
        market_pulse["mass_total_slots"] = total_slots
        market_pulse["mass_finance_hits"] = fin_hits
        market_pulse["mass_ahk_hits"] = int(cn_mass.get("ahk_hits") or 0)
        market_pulse["mass_penetration_pct"] = round(fin_hits / total_slots * 100.0, 1)
        plats = [str(p) for p in (cn_mass.get("platforms") or []) if p]
        if plats:
            market_pulse["mass_platforms"] = plats[:6]

    channel_order = ("xueqiu", "eastmoney", "cn_communities", "reddit", "xiaohongshu")
    per_channel_clean: dict[str, list[dict[str, Any]]] = {}
    for cid in channel_order:
        ch_items = [
            it for it in (results.get(cid, {}).get("items") or [])
            if isinstance(it, dict) and not has_private_article_text(json.dumps(it, ensure_ascii=False))
        ]
        if cid in results:
            results[cid]["items"] = ch_items
            results[cid]["count"] = len(ch_items)
        per_channel_clean[cid] = sorted(ch_items, key=lambda x: int(x.get("heat") or 0), reverse=True)

    combined_items: list[dict[str, Any]] = []
    seen_comb: set[int] = set()
    for cid in channel_order:
        for it in per_channel_clean.get(cid, [])[:12]:
            seen_comb.add(id(it))
            combined_items.append(it)
    rest_items: list[dict[str, Any]] = []
    for cid in channel_order:
        for it in per_channel_clean.get(cid, []):
            if id(it) not in seen_comb:
                rest_items.append(it)
    rest_items.sort(key=lambda x: int(x.get("heat") or 0), reverse=True)
    combined_items.extend(rest_items)
    combined_items.sort(key=lambda x: int(x.get("heat") or 0), reverse=True)

    clean_desk_supplements = [
        d for d in desk_supplements
        if isinstance(d, dict) and not has_private_article_text(json.dumps(d, ensure_ascii=False))
    ]
    if has_private_article_text(json.dumps(market_pulse, ensure_ascii=False)):
        market_pulse = {}

    channels_status = []
    for cid in channel_order:
        if cid not in results:
            continue
        raw_name = str(results[cid].get("name") or cid)
        if has_private_article_text(raw_name):
            raw_name = cid
        raw_note = str(results[cid].get("note") or "—")
        if has_private_article_text(raw_note):
            raw_note = "渠道状态正常"
        channels_status.append(
            {
                "id": cid,
                "name": raw_name,
                "status": str(results[cid].get("status") or "部分受限"),
                "count": int(results[cid].get("count") or 0),
                "note": _truncate(raw_note, 90),
            }
        )

    payload = sanitize_output(
        {
            "updated_at_bjt": _now_bjt_str(),
            "cached_at": round(time.time(), 2),
            "from_cache": False,
            "channels_status": channels_status,
            "channels": {cid: results[cid] for cid in channel_order if cid in results},
            "market_pulse": market_pulse,
            "desk_supplements": clean_desk_supplements[:15],
            "items": combined_items[:100],
        }
    )

    try:
        atomic_write_json(resolved_cache, payload)
    except Exception:
        resolved_cache.parent.mkdir(parents=True, exist_ok=True)
        resolved_cache.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    return payload


_CONCRETE_AHK_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:hsi|hstech|csi|baba|zhipu)(?![A-Za-z0-9])|"
    r"(?<![A-Za-z0-9])(?:SH|SZ|BJ)\d{6}(?![A-Za-z0-9])|(?<![A-Za-z0-9])\d{6}\.(?:SZ|SS|SH|BJ)(?![A-Za-z0-9])|"
    r"(?<![A-Za-z0-9])\d{4,5}\.HK(?![A-Za-z0-9])|(?<![A-Za-z0-9])HK\d{4,5}(?![A-Za-z0-9])|"
    r"上证|深成指|创业板|科创|沪指|恒生|恒指|恒科|港股|港交所|南向|北向|两融|平准基金|汇金|国常会|A50|中概|"
    r"茅台|宁德|中际旭创|寒武纪|中芯|腾讯|阿里|美团|小米|比亚迪|智谱"
)

_TRACKING_QP = frozenset(
    {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "spm", "from", "os", "sv", "app", "share_token"}
)


def _norm_desk_url(url: str) -> str:
    s = _safe_http_url(url)
    if not s:
        return ""
    try:
        parts = urllib.parse.urlsplit(s)
    except ValueError:
        return ""
    path = parts.path.rstrip("/")
    kept_q = [
        (k, v)
        for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=False)
        if k.lower() not in _TRACKING_QP
    ]
    if not path and not kept_q:
        return ""
    kept_q.sort()
    query_str = urllib.parse.urlencode(kept_q)
    return urllib.parse.urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, query_str, ""))


def _extract_desk_entries(
    kol_digest: list[dict[str, Any]] | None,
    kol_scorecards: list[dict[str, Any]] | None,
    signals: list[dict[str, Any]] | None,
    *,
    desk_supplements: list[dict[str, Any]] | None = None,
    market_pulse: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Normalize DESK layer (KOL + guru + curated signals + A/HK capital flows & Gelonghui) with dedup."""
    card_map: dict[str, dict[str, Any]] = {}
    for c in kol_scorecards or []:
        if isinstance(c, dict) and c.get("handle"):
            card_map[str(c["handle"]).lower()] = c

    entries: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    seen_texts: set[str] = set()

    def _merge_tickers(raw_list: Any, text: str) -> list[str]:
        base = [_canon_ticker_sym(str(t)) for t in (raw_list or []) if t]
        extracted = extract_tickers(text)
        return list(dict.fromkeys(base + extracted))

    def _append_entry(entry: dict[str, Any], tickers: list[str], combined_text: str, raw_dedup_text: str = "") -> None:
        topics = list(entry.get("topics") or [])
        tks = set(tickers or [])
        if (
            "A股与港股" in topics
            and (tks & (_AI_US_TICKERS | _CRYPTO_TICKERS | _GOLD_COMM_TICKERS))
            and not any(_is_ahk_ticker(t) for t in tks)
            and not _CONCRETE_AHK_RE.search(combined_text)
        ):
            topics = [t for t in topics if t != "A股与港股"] or ["市场整体风险偏好"]
            entry["topics"] = topics
        u_key = _norm_desk_url(str(entry.get("url") or ""))
        dedup_src = raw_dedup_text or str(entry.get("summary") or "")
        t_key = _norm_dup_key(dedup_src)
        if u_key and u_key in seen_urls:
            return
        if t_key and t_key in seen_texts:
            return
        if u_key:
            seen_urls.add(u_key)
        if t_key:
            seen_texts.add(t_key)
        entries.append(entry)

    for k in kol_digest or []:
        if not isinstance(k, dict):
            continue
        handle = str(k.get("handle") or "").strip()
        stance = str(k.get("stance") or "中性").strip()
        raw_score = k.get("score")
        if stance == "偏多":
            dir_val = abs(float(raw_score)) if isinstance(raw_score, (int, float)) and raw_score != 0 else 0.7
        elif stance == "偏空":
            dir_val = -(abs(float(raw_score)) if isinstance(raw_score, (int, float)) and raw_score != 0 else 0.7)
        else:
            dir_val = 0.0

        card = card_map.get(handle.lower(), {})
        rel = float(k.get("reliability_score") or card.get("reliability_score") or 60.0)
        verified = int(k.get("verified_calls") or card.get("verified_calls") or 0)
        weight = (rel / 100.0) * (1.25 if verified >= 5 and rel >= 70 else 1.0)
        headline = strip_emojis(str(k.get("headline") or k.get("takeaway") or "").strip())
        combined = f"{k.get('topic', '')} {headline}".strip()
        tickers = _merge_tickers(k.get("tickers"), combined)
        topics = _classify_topics(combined, tickers)
        if topics == ["市场整体风险偏好"] and k.get("group"):
            topics = _classify_topics(f"{k.get('group', '')} {combined}", tickers)
        _append_entry(
            {
                "author": f"@{handle}" if handle else str(k.get("name") or "KOL"),
                "summary": _truncate(headline, 88),
                "url": _safe_http_url(str(k.get("url") or "")),
                "dir_val": _clamp(dir_val, -1.0, 1.0),
                "weight": max(0.3, weight),
                "topics": topics,
            },
            tickers,
            combined,
            headline,
        )

    desk_sources = {"X核心观点", "X深度长文", "大佬分享", "AI与金融研报", "华尔街见闻", "财联社电报", "FED官方", "SEC EDGAR"}
    for sig in signals or []:
        if not isinstance(sig, dict):
            continue
        src = str(sig.get("source") or "").strip()
        if src and src not in desk_sources and sig.get("source_tier") not in ("T1", "T1_5"):
            continue
        direction = str(sig.get("direction") or "neutral").lower()
        dir_val = 0.65 if direction == "bullish" else (-0.65 if direction == "bearish" else 0.0)
        title = str(sig.get("title") or sig.get("summary") or "").strip()
        if not title:
            continue
        combined = f"{sig.get('category', '')} {title}".strip()
        tickers = _merge_tickers(sig.get("tickers"), title)
        topics = _classify_topics(combined, tickers)
        _append_entry(
            {
                "author": src or "机构研报",
                "summary": _truncate(title, 88),
                "url": _safe_http_url(str(sig.get("url") or "")),
                "dir_val": dir_val,
                "weight": 0.65 if sig.get("importance") == "高" else 0.45,
                "topics": topics,
            },
            tickers,
            combined,
            title,
        )

    for sup in desk_supplements or []:
        if not isinstance(sup, dict):
            continue
        summary = str(sup.get("summary") or "").strip()
        if not summary:
            continue
        tickers = _merge_tickers(sup.get("tickers"), summary)
        topics = list(sup.get("topics") or _classify_topics(summary, tickers))
        _append_entry(
            {
                "author": str(sup.get("author") or "格隆汇"),
                "summary": _truncate(summary, 88),
                "url": _safe_http_url(str(sup.get("url") or "")),
                "dir_val": _clamp(float(sup.get("dir_val") or 0.0), -1.0, 1.0),
                "weight": float(sup.get("weight") or 0.7),
                "topics": topics,
            },
            tickers,
            summary,
            summary,
        )

    mp = market_pulse if isinstance(market_pulse, dict) else {}
    sb_net = mp.get("southbound_net_yi_hkd")
    if isinstance(sb_net, (int, float)):
        sb_dir = 0.72 if sb_net >= 30 else (-0.72 if sb_net <= -30 else (0.32 if sb_net > 5 else (-0.32 if sb_net < -5 else 0.0)))
        sb_5d = mp.get("southbound_5d_net_yi_hkd")
        sb_tail = f"（近5日 {sb_5d:+.1f} 亿）" if isinstance(sb_5d, (int, float)) else ""
        hsi_pct = mp.get("hsi_pct")
        hsi_part = f" · 恒指 {hsi_pct:+.2f}%" if isinstance(hsi_pct, (int, float)) else ""
        _append_entry(
            {
                "author": "港股通南向",
                "summary": f"南向资金当日净流向 {sb_net:+.2f} 亿港元{sb_tail}{hsi_part}",
                "url": "https://data.eastmoney.com/hsgt/index.html",
                "dir_val": sb_dir,
                "weight": 1.15,
                "topics": ["A股与港股"],
            },
            [],
            "港股通南向资金 恒生",
        )
    rz_bal = mp.get("rzrq_balance_display")
    rz_net = mp.get("rzrq_net_buy_yi")
    a_turn = mp.get("a_turnover_display")
    if rz_bal or a_turn:
        net_val = float(rz_net) if isinstance(rz_net, (int, float)) else 0.0
        a_yi = float(mp.get("a_turnover_yi") or 0.0)
        sh_pct = float(mp.get("sh_pct") or 0.0)
        if net_val >= 80 or (a_yi >= 18000 and sh_pct >= 1.0):
            rz_dir = 0.65
        elif net_val <= -120 and sh_pct < -0.5:
            rz_dir = -0.55
        elif net_val > 15:
            rz_dir = 0.25
        elif net_val < -50:
            rz_dir = -0.25
        else:
            rz_dir = 0.0
        parts = []
        if a_turn:
            parts.append(f"沪深成交 {a_turn}")
        if rz_bal:
            parts.append(f"两融余额 {rz_bal}")
        if isinstance(rz_net, (int, float)):
            parts.append(f"融资净买入 {rz_net:+.1f} 亿")
        _append_entry(
            {
                "author": "两融与量能",
                "summary": " · ".join(parts),
                "url": "https://data.eastmoney.com/rzrq/total.html",
                "dir_val": rz_dir,
                "weight": 1.0,
                "topics": ["A股与港股"],
            },
            [],
            "沪深两市 融资余额 两融",
        )
    return entries


def collect_public_discussion_trends(data_dir: Path) -> dict[str, Any]:
    """Use only the existing unauthenticated public hotlists, not local account paths."""
    def fetch(platform: str) -> dict[str, Any]:
        return _http_get_json(f"{NEWSNOW_API_URL}?id={urllib.parse.quote(platform)}", timeout=5)

    now = datetime.now(BJT)
    return discussion_trends(refresh_discussion_history(data_dir, fetch, now=now), today=now.date())


def _is_quote_rank(item: dict[str, Any]) -> bool:
    # Old cached rankings lack the typed marker.
    title = str(item.get("title") or "")
    return (
        item.get("kind") == "quote_rank" or item.get("channel") == "eastmoney"
        or title.startswith(("雪球热股 #", "东财热股 #"))
    )


def _is_cn_mass_crowd_item(item: dict[str, Any]) -> bool:
    plat = str(item.get("platform") or "")
    ch = str(item.get("channel") or "")
    return ch == "xiaohongshu" or any(k in plat for k in ("小红书", "微博", "百度", "头条", "抖音", "知乎", "B站"))


def _judge_divergence(
    desk_score: int,
    street_score: int | None,
    street_heat: int | None,
    *,
    desk_count: int = 0,
    cn_crowd_count: int = 0,
    cn_platform_count: int = 0,
    xhs_count: int = 0,
    xhs_heat: int = 0,
    xhs_score: int = 0,
    cn_crowd_heat: int = 0,
    cn_crowd_score: int = 0,
    mass_penetration_pct: float = 0.0,
    mass_topic_hits: int = 0,
) -> tuple[str, str, str]:
    """Describe the current sample only; never infer a top, bottom, or trend."""
    if street_score is None or street_heat is None:
        return (
            "暂无样本",
            "neutral",
            "当前赛道暂未采集到有效社区讨论样本，暂参考专业层动向。",
        )
    gap = street_score - desk_score
    # These branches describe a single snapshot, independent from the history-based warning.
    if cn_platform_count >= 2 and street_score >= 30 and xhs_count >= 3 and xhs_heat >= 76 and xhs_score >= 35:
        return (
            "当期关注较高",
            "neutral",
            "本次样本关注度较高，不代表持续升温。",
        )
    if street_score >= 30 and (
        (cn_platform_count >= 2 and cn_crowd_count >= 4 and cn_crowd_heat >= 76 and cn_crowd_score >= 35)
        or (mass_topic_hits >= 6 and mass_penetration_pct >= 14.0 and street_score >= 32 and cn_crowd_heat >= 74)
    ):
        return (
            "当期关注较高",
            "neutral",
            "本次样本关注度较高，不代表持续升温。",
        )
    if desk_count == 0:
        return ("仅社区样本", "neutral", "本次没有可作对照的专业样本。")
    if street_heat >= 62 and street_score >= 28 and (desk_score <= 15 or gap >= 25):
        return ("当期看多分歧", "neutral", "本次社区样本偏多，专业样本未显示相同倾向。")
    if street_score <= -25 and street_heat >= 50 and (desk_score >= -5 or desk_score - street_score >= 25):
        return ("当期看空分歧", "neutral", "本次社区样本偏空，专业样本未显示相同倾向。")
    if abs(desk_score) >= 22 and street_heat < 58 and (
        (desk_score > 0 and -15 < street_score < 22) or (desk_score < 0 and -20 < street_score < 15)
    ):
        tone = "bull" if desk_score > 0 else "warn"
        hint = (
            "本次专业样本偏多，社区样本倾向不明显。"
            if desk_score > 0
            else "本次专业样本偏空，社区样本倾向不明显。"
        )
        return ("专业倾向较强", tone, hint)
    if (
        (desk_score >= 15 and street_score <= -15)
        or (desk_score <= -15 and street_score >= 15)
        or (abs(gap) >= 42 and street_heat >= 58)
    ):
        return (
            "多空分歧",
            "warn",
            "本次专业与社区样本倾向不同。",
        )
    if desk_score >= 18 and street_score >= 18:
        return (
            "多头共振",
            "bull",
            "本次专业与社区样本同向偏多。",
        )
    if desk_score <= -20 and street_score <= -20:
        return (
            "同步谨慎",
            "bear",
            "本次专业与社区样本同向偏空。",
        )
    return (
        "情绪平稳",
        "neutral",
        "本次样本未显示明显的方向分歧。",
    )


def build_sentiment_divergence(
    forum_pulse: dict[str, Any] | None,
    kol_digest: list[dict[str, Any]] | None = None,
    kol_scorecards: list[dict[str, Any]] | None = None,
    signals: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compare DESK (KOL + institutional wires) vs STREET (forums) across 6 core tracks (<= 40KB)."""
    pulse = forum_pulse if isinstance(forum_pulse, dict) else {}
    street_items: list[dict[str, Any]] = []
    if isinstance(pulse.get("items"), list) and pulse["items"]:
        street_items = [
            x for x in pulse["items"]
            if isinstance(x, dict) and not has_private_article_text(json.dumps(x, ensure_ascii=False))
        ]
    else:
        for ch in (pulse.get("channels") or {}).values():
            if isinstance(ch, dict):
                street_items.extend(
                    x for x in (ch.get("items") or [])
                    if isinstance(x, dict) and not has_private_article_text(json.dumps(x, ensure_ascii=False))
                )

    street_items = [item for item in street_items if not _is_quote_rank(item)]

    raw_market_pulse = pulse.get("market_pulse") if isinstance(pulse.get("market_pulse"), dict) else {}
    clean_market_pulse = project_market_pulse(raw_market_pulse)
    if clean_market_pulse and has_private_article_text(json.dumps(clean_market_pulse, ensure_ascii=False)):
        clean_market_pulse = {}

    raw_supplements = pulse.get("desk_supplements") if isinstance(pulse.get("desk_supplements"), list) else []
    desk_entries = [
        e for e in _extract_desk_entries(
            kol_digest,
            kol_scorecards,
            signals,
            desk_supplements=raw_supplements,
            market_pulse=clean_market_pulse,
        )
        if not has_private_article_text(json.dumps(e, ensure_ascii=False))
    ]

    divergences: list[dict[str, Any]] = []
    topic_heats: list[float] = []
    topic_street_scores: list[float] = []
    global_seen_d: set[str] = set()
    global_seen_s: set[str] = set()

    for topic in CORE_TOPICS:
        if topic == "市场整体风险偏好":
            d_pool = [e for e in desk_entries if topic in e["topics"]] or desk_entries
            s_pool = [s for s in street_items if topic in (s.get("topics") or [])] or street_items
        else:
            d_pool = [e for e in desk_entries if topic in e["topics"]]
            s_pool = [s for s in street_items if topic in (s.get("topics") or [])]

        if d_pool:
            w_sum = sum(e["weight"] for e in d_pool) or 1.0
            desk_score = int(_clamp(round(sum(e["dir_val"] * e["weight"] for e in d_pool) / w_sum * 100.0), -95, 95))
        else:
            desk_score = 0
        desk_bias = "偏多" if desk_score >= 15 else ("偏空" if desk_score <= -15 else "中性")

        street_score: int | None
        street_heat: int | None
        if s_pool:
            heats = [float(s.get("heat") or 50.0) for s in s_pool]
            weights = [max(0.4, h / 100.0) for h in heats]
            w_s = sum(weights) or 1.0
            street_score = int(_clamp(round(sum(float(s.get("score") or 0.0) * w for s, w in zip(s_pool, weights)) / w_s), -95, 95))
            avg_h = sum(heats) / len(heats)
            breadth_weight = 0.55 + 0.45 * min(1.0, len(s_pool) / 5.0)
            street_heat = int(_clamp(round(avg_h * breadth_weight), 18, 98))
            topic_heats.append(float(street_heat))
            topic_street_scores.append(float(street_score))
        else:
            street_score = None
            street_heat = None

        cn_pool = [s for s in s_pool if _is_cn_mass_crowd_item(s)]
        cn_platform_count = len(
            {
                str(s.get("platform") or s.get("channel") or "").strip()
                for s in cn_pool
                if str(s.get("platform") or s.get("channel") or "").strip()
            }
        )
        xhs_pool = [s for s in cn_pool if str(s.get("channel") or "") == "xiaohongshu" or "小红书" in str(s.get("platform") or "")]
        xhs_count = len(xhs_pool)
        if xhs_pool:
            xhs_heat = int(round(sum(float(s.get("heat") or 50.0) for s in xhs_pool) / len(xhs_pool)))
            xhs_score = int(round(sum(float(s.get("score") or 0.0) for s in xhs_pool) / len(xhs_pool)))
        else:
            xhs_heat = 0
            xhs_score = 0
        if cn_pool:
            cn_heats = [float(s.get("heat") or 50.0) for s in cn_pool]
            cn_heat = int(round(sum(cn_heats) / len(cn_heats)))
            cn_score = int(round(sum(float(s.get("score") or 0.0) for s in cn_pool) / len(cn_pool)))
        else:
            cn_heat = 0
            cn_score = 0

        mass_pct_for_topic = float(clean_market_pulse.get("mass_penetration_pct") or 0.0) if topic == "A股与港股" else 0.0
        mass_hits_for_topic = int(clean_market_pulse.get("mass_ahk_hits") or 0) if topic == "A股与港股" else 0
        tag, tone, hint = _judge_divergence(
            desk_score,
            street_score,
            street_heat,
            desk_count=len(d_pool),
            cn_crowd_count=len(cn_pool),
            cn_platform_count=cn_platform_count,
            xhs_count=xhs_count,
            xhs_heat=xhs_heat,
            xhs_score=xhs_score,
            cn_crowd_heat=cn_heat,
            cn_crowd_score=cn_score,
            mass_penetration_pct=mass_pct_for_topic,
            mass_topic_hits=mass_hits_for_topic,
        )

        # Pick representative non-empty samples (prefer directional / higher weight & unseen across tracks)
        d_sorted = sorted(d_pool, key=lambda e: (abs(e["dir_val"]) * e["weight"]), reverse=True)
        desk_samples = []
        seen_d: set[str] = set()
        for pass_idx in (0, 1):
            for e in d_sorted:
                key = _norm_desk_url(str(e.get("url") or "")) or _norm_dup_key(str(e.get("summary") or ""))
                if not key or key in seen_d:
                    continue
                if pass_idx == 0 and key in global_seen_d:
                    continue
                seen_d.add(key)
                global_seen_d.add(key)
                desk_samples.append({"author": e["author"], "summary": e["summary"], "url": e["url"]})
                if len(desk_samples) >= 2:
                    break
            if len(desk_samples) >= 2:
                break

        s_sorted = sorted(s_pool, key=lambda x: (int(x.get("heat") or 0) + abs(int(x.get("score") or 0)) * 0.4), reverse=True)
        street_samples = []
        seen_s: set[str] = set()
        for pass_idx in (0, 1):
            for s in s_sorted:
                title = _truncate(strip_emojis(str(s.get("title") or "")), 76)
                url = _safe_http_url(str(s.get("url") or ""))
                s_key = _norm_desk_url(url) or _norm_dup_key(title)
                if not title or not s_key or s_key in seen_s:
                    continue
                if pass_idx == 0 and s_key in global_seen_s:
                    continue
                seen_s.add(s_key)
                global_seen_s.add(s_key)
                street_samples.append(
                    {
                        "platform": str(s.get("platform") or "社区"),
                        "title": title,
                        "url": url,
                    }
                )
                if len(street_samples) >= 2:
                    break
            if len(street_samples) >= 2:
                break

        divergences.append(
            {
                "topic": topic,
                "desk_score": desk_score,
                "desk_bias": desk_bias,
                "desk_count": len(d_pool),
                "street_score": street_score,
                "street_heat": street_heat,
                "street_count": len(s_pool),
                "divergence_tag": tag,
                "tone": tone,
                "action_hint": hint,
                "desk_samples": desk_samples,
                "street_samples": street_samples,
            }
        )

    if not topic_heats:
        overall_crowd_temp: int | None = None
        overall_label = "暂无样本"
    else:
        avg_heat = sum(topic_heats) / len(topic_heats)
        avg_score = sum(topic_street_scores) / len(topic_street_scores) if topic_street_scores else 0.0
        overall_crowd_temp = int(_clamp(round(50.0 + 0.35 * avg_score + 0.35 * (avg_heat - 50.0)), 5, 95))
        if overall_crowd_temp >= 72:
            overall_label = "情绪亢奋"
        elif overall_crowd_temp >= 58:
            overall_label = "偏暖活跃"
        elif overall_crowd_temp >= 42:
            overall_label = "情绪平稳"
        elif overall_crowd_temp >= 28:
            overall_label = "谨慎降温"
        else:
            overall_label = "恐慌降温"

    channels_status = []
    for c in (pulse.get("channels_status") or []):
        if not isinstance(c, dict):
            continue
        raw_name = str(c.get("name") or "")
        if has_private_article_text(raw_name):
            continue
        raw_note = str(c.get("note") or "—")
        if has_private_article_text(raw_note):
            raw_note = "渠道状态正常"
        row_c = {
            "id": str(c.get("id") or ""),
            "name": raw_name,
            "status": str(c.get("status") or "部分受限"),
            "count": int(c.get("count") or 0),
            "note": _truncate(raw_note, 80),
        }
        if not has_private_article_text(json.dumps(row_c, ensure_ascii=False)):
            channels_status.append(row_c)

    hot_discussions: list[dict[str, Any]] = []
    seen_hot: set[str] = set()
    sorted_street = sorted(street_items, key=lambda x: int(x.get("heat") or 0), reverse=True)

    def _to_hot_row(item: dict[str, Any]) -> dict[str, Any] | None:
        title = _truncate(strip_emojis(str(item.get("title") or "")), 76)
        norm = _norm_dup_key(title)
        if not title or norm in seen_hot:
            return None
        seen_hot.add(norm)
        topics = item.get("topics") or ["市场整体风险偏好"]
        primary_topic = next((t for t in topics if t != "市场整体风险偏好"), topics[0])
        return {
            "platform": str(item.get("platform") or "社区"),
            "topic": primary_topic,
            "title": title,
            "sentiment": str(item.get("sentiment") or "中性"),
            "heat": int(item.get("heat") or 50),
            "tickers": [str(t) for t in (item.get("tickers") or [])[:4]],
            "url": _safe_http_url(str(item.get("url") or "")),
        }

    # Ensure top ranks surface cross-channel voices (Xiaohongshu / Xueqiu / Reddit / CN hotlists) cleanly
    by_ch: dict[str, list[dict[str, Any]]] = {}
    for it in sorted_street:
        ch_id = str(it.get("channel") or it.get("platform") or "other")
        by_ch.setdefault(ch_id, []).append(it)
    for ch_id in ("xiaohongshu", "eastmoney", "xueqiu", "reddit", "cn_communities"):
        for it in by_ch.get(ch_id, []):
            row = _to_hot_row(it)
            if row is not None:
                hot_discussions.append(row)
                break
    for item in sorted_street:
        if len(hot_discussions) >= 20:
            break
        row = _to_hot_row(item)
        if row is not None:
            hot_discussions.append(row)

    allow_net = not bool(os.environ.get("PYTEST_CURRENT_TEST"))
    brief_targets: list[dict[str, Any]] = list(hot_discussions)
    for d in divergences:
        for ss in d.get("street_samples") or []:
            brief_targets.append(ss)
    attach_zh_briefs_batch(
        brief_targets,
        title_key="title",
        summary_key="title",
        allow_network=allow_net,
        max_network_items=24,
    )
    desk_brief_targets = [
        ds for d in divergences for ds in (d.get("desk_samples") or [])
    ]
    attach_zh_briefs_batch(
        desk_brief_targets,
        title_key="summary",
        summary_key="summary",
        allow_network=allow_net,
        max_network_items=12,
    )

    result = sanitize_output(
        {
            "updated_at_bjt": str(pulse.get("updated_at_bjt") or _now_bjt_str()),
            "overall_crowd_temp": overall_crowd_temp,
            "overall_label": overall_label,
            "market_pulse": clean_market_pulse,
            "channels_status": channels_status,
            "divergences": divergences,
            "hot_discussions": hot_discussions,
        }
    )

    return trim_forum_sentiment(result)


def trim_forum_sentiment(result: dict[str, Any]) -> dict[str, Any]:
    """Keep public trend summaries ahead of low-priority discussion examples."""
    hot = result.get("hot_discussions") or []
    while len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > MAX_OUTPUT_BYTES and hot:
        hot.pop()
    while len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > MAX_OUTPUT_BYTES:
        trimmed = False
        for d in result.get("divergences") or []:
            if len(d["desk_samples"]) > 1:
                d["desk_samples"].pop()
                trimmed = True
            if len(d["street_samples"]) > 1:
                d["street_samples"].pop()
                trimmed = True
        if not trimmed:
            break

    return result


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="MacroCockpit 4-channel forum sentiment collector")
    parser.add_argument("--refresh", action="store_true", help="Force refresh forum pulse cache")
    parser.add_argument("--cache-path", type=str, default=str(DEFAULT_FORUM_CACHE_PATH), help="Cache JSON path")
    parser.add_argument("--timeout", type=int, default=6, help="HTTP/CLI timeout seconds")
    args = parser.parse_args(argv)

    pulse = collect_forum_pulse(
        cache_path=Path(args.cache_path),
        force_refresh=bool(args.refresh),
        timeout=max(5, int(args.timeout)),
    )
    div = build_sentiment_divergence(pulse)
    summary = {
        "updated_at_bjt": pulse.get("updated_at_bjt"),
        "from_cache": pulse.get("from_cache", False),
        "channels_status": pulse.get("channels_status"),
        "total_items": len(pulse.get("items") or []),
        "overall_crowd_temp": div.get("overall_crowd_temp"),
        "overall_label": div.get("overall_label"),
        "divergence_bytes": len(json.dumps(div, ensure_ascii=False).encode("utf-8")),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
