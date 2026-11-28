"""US equity individual stock fundamentals, valuation, quarterly financials & 1Y chart builder."""
from __future__ import annotations

import json
import math
import re
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any

try:
    import requests
except ImportError:
    requests = None  # type: ignore[assignment]

from backend.guardrails import scrub_forbidden
from backend.runtime import BJT, DATA_DIR, UTC, atomic_write_json, safe_float as _finite
from backend.fred_and_market import _infer_us_session
from backend.sec_edgar import SEC_CIK_MAP, fetch_sec_filings_map

CACHE_PATH = DATA_DIR / "stock_profiles_cache.json"
CACHE_TTL_SECONDS = 6 * 3600
ON_DEMAND_TTL_SECONDS = 600
_ON_DEMAND_LOCK = threading.Lock()
_ON_DEMAND_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )
}

# (symbol, cn_name, exchange, sector_cn, peer_group, business_overview_cn)
CORE_RESEARCH_STOCKS: tuple[tuple[str, str, str, str, str, str], ...] = (
    (
        "AAPL",
        "苹果",
        "NASDAQ",
        "科技 · 消费电子",
        "核心平台巨头",
        "以 iPhone、Mac、iPad 与穿戴设备为硬件入口，依托 App Store、iCloud 与 Apple Pay 构建高黏性高毛利服务生态，通过自研芯片强化软硬件一体护城河。",
    ),
    (
        "NVDA",
        "英伟达",
        "NASDAQ",
        "科技 · AI 算力",
        "AI 芯片与算力",
        "全球数据中心 AI 训练与推理 GPU 绝对龙头，结合 NVLink 高速互联与 CUDA 软件生态垄断云厂商资本开支核心环节。",
    ),
    (
        "MSFT",
        "微软",
        "NASDAQ",
        "科技 · 云与企业软件",
        "核心平台巨头",
        "以 Azure 云计算和 Office 365 企业办公套件为现金牛，率先将 Copilot AI 与 OpenAI 算力商业化嵌入全球企业工作流。",
    ),
    (
        "GOOG",
        "谷歌",
        "NASDAQ",
        "科技 · 搜索与云计算",
        "核心平台巨头",
        "涵盖全球搜索广告、YouTube、Google Cloud 与 Gemini 大模型及 TPU 自研算力，具备充沛自由现金流与全栈 AI 分发入口。",
    ),
    (
        "META",
        "Meta",
        "NASDAQ",
        "科技 · 社交与开源 AI",
        "核心平台巨头",
        "坐拥全球逾 30 亿日活社交矩阵，通过 AI 推荐与定向广告系统持续提升变现效率，同时以开源 Llama 与端侧硬件卡位下一代交互入口。",
    ),
    (
        "AMZN",
        "亚马逊",
        "NASDAQ",
        "科技 · 电商与云计算",
        "核心平台巨头",
        "全球云基础设施 AWS 第一大份额持有者，叠加北美高效履约电商网络与高毛利站内数字广告业务驱动利润率跃升。",
    ),
    (
        "TSLA",
        "特斯拉",
        "NASDAQ",
        "可选消费 · 智能电车与机器人",
        "硬科技与高弹性成长",
        "集纯电整车制造、FSD 端到端自动驾驶、Megapack 储能电站与人形机器人研发于一体，估值定价核心锚定 AI 物理落地进度。",
    ),
    (
        "AMD",
        "超威半导体",
        "NASDAQ",
        "科技 · AI 芯片与 CPU",
        "AI 芯片与算力",
        "数据中心服务器 EPYC CPU 份额持续扩张，MI 系列 AI 加速卡作为云厂商核心第二供应商切入推理与训练集群。",
    ),
    (
        "AVGO",
        "博通",
        "NASDAQ",
        "科技 · 定制 AI 芯片与网络",
        "AI 芯片与算力",
        "深耕超大规模云厂商定制 ASIC 芯片与 AI 集群高速以太网交换芯片，叠加 VMware 企业软件带来稳定高现金流。",
    ),
    (
        "TSM",
        "台积电",
        "NYSE",
        "科技 · 先进制程晶圆代工",
        "半导体制造与存储",
        "掌握全球最先进 3nm/2nm 逻辑制程及 CoWoS 先进封装核心产能，是英伟达、苹果、AMD 与定制 AI 芯片无法绕开的上游枢纽。",
    ),
    (
        "MU",
        "美光科技",
        "NASDAQ",
        "科技 · HBM 与存储芯片",
        "半导体制造与存储",
        "全球三大 DRAM 与 NAND 存储原厂之一，高带宽内存 HBM3E/HBM4 随着 AI 服务器单卡显存容量翻倍进入高景气周期。",
    ),
    (
        "INTC",
        "英特尔",
        "NASDAQ",
        "科技 · x86 与芯片代工",
        "半导体制造与存储",
        "传统 PC 与服务器 x86 架构核心厂商，正推进 18A 先进制程代工转型与资产重构，关注代工良率与毛利率修复节奏。",
    ),
    (
        "QCOM",
        "高通",
        "NASDAQ",
        "科技 · 端侧 AI 芯片",
        "AI 芯片与算力",
        "移动 SoC 与基带专利龙头，业务正从旗舰手机芯片向 AI PC、车载智能座舱与端侧边缘推理芯片延伸。",
    ),
    (
        "PLTR",
        "Palantir",
        "NASDAQ",
        "科技 · 企业 AI 决策软件",
        "软件与新经济平台",
        "为西方防务机构及大型企业提供 Foundry 与 AIP 人工智能决策平台，在企业级私有化 AI 落地中具备强合同黏性。",
    ),
    (
        "ORCL",
        "甲骨文",
        "NYSE",
        "科技 · 云基础设施与数据库",
        "软件与新经济平台",
        "依托高性价比 OCI GPU 集群承接多家大模型训练算力需求，传统企业数据库上云与 AI 算力积压订单（RPO）高速扩张。",
    ),
    (
        "NFLX",
        "奈飞",
        "NASDAQ",
        "通信服务 · 全球流媒体",
        "核心平台巨头",
        "全球流媒体订阅龙头，借由付费密码管控、广告低价档位与体育直播内容持续扩大自由现金流规模与营业利润率。",
    ),
    (
        "ARM",
        "Arm",
        "NASDAQ",
        "科技 · 处理器指令集架构",
        "AI 芯片与算力",
        "全球移动端及 AI 服务器低功耗 CPU 核心架构授权方，通过 v9 高费率架构升级与算力芯片渗透提升单颗芯片版税收入。",
    ),
    (
        "SMCI",
        "超微电脑",
        "NASDAQ",
        "科技 · AI 液冷服务器",
        "硬科技与高弹性成长",
        "专注于高性能液冷 AI 服务器与机柜级集成方案，业绩对数据中心 GPU 交付与机房供电/散热扩建高度敏感。",
    ),
    (
        "COIN",
        "Coinbase",
        "NASDAQ",
        "金融 · 合规加密交易与托管",
        "数字资产关联股",
        "美国最大合规数字资产交易所及多家比特币/以太坊现货 ETF 核心托管行，受益于链上活跃度、稳定币利息与监管清晰度提升。",
    ),
    (
        "MSTR",
        "MicroStrategy",
        "NASDAQ",
        "科技 · 比特币财库平台",
        "数字资产关联股",
        "主营企业商业智能软件并通过可转债/股权融资持续增持比特币资产，股票本身呈现高 Beta 比特币杠杆代理属性。",
    ),
)

STOCK_META_MAP: dict[str, tuple[str, str, str, str, str, str]] = {
    row[0]: row for row in CORE_RESEARCH_STOCKS
}

# (symbol, cn_name, exchange, sector_cn, peer_group, overview_cn, market, currency, currency_symbol)
CORE_MULTI_MARKET_ASSETS: tuple[tuple[str, str, str, str, str, str, str, str, str], ...] = (
    # A股核心代表
    (
        "600519.SH",
        "贵州茅台",
        "上交所",
        "消费 · 白酒龙头",
        "A股核心蓝筹",
        "中国高端酱香型白酒绝对龙头，凭借稀缺产能、强品牌定价权与直销渠道改革维持超高毛利率与充沛经营现金流。",
        "cn",
        "CNY",
        "¥",
    ),
    (
        "300750.SZ",
        "宁德时代",
        "深交所",
        "新能源 · 动力与储能电池",
        "A股制造与科技龙头",
        "全球动力电池与储能系统装机量第一，依托神行/麒麟电池技术迭代与海外产能布局保持领先盈利能力。",
        "cn",
        "CNY",
        "¥",
    ),
    (
        "002594.SZ",
        "比亚迪",
        "深交所",
        "新能源车 · 整车与智驾",
        "A股制造与科技龙头",
        "具备三电全产业链垂直整合能力的新能源乘用车龙头，高端车型出海与高阶智驾平权推动单车盈利持续优化。",
        "cn",
        "CNY",
        "¥",
    ),
    (
        "601318.SH",
        "中国平安",
        "上交所",
        "金融 · 综合保险与资管",
        "A股核心蓝筹",
        "国内综合金融与寿险龙头，估值对十年期国债收益率与权益投资收益高度敏感，兼具高股息防御属性。",
        "cn",
        "CNY",
        "¥",
    ),
    (
        "688981.SH",
        "中芯国际",
        "上交所",
        "半导体 · 晶圆代工",
        "A股制造与科技龙头",
        "中国大陆技术最先进、规模最大的集成电路晶圆代工企业，核心受益于本土算力芯片与成熟制程国产替代需求。",
        "cn",
        "CNY",
        "¥",
    ),
    # 港股核心代表
    (
        "00700.HK",
        "腾讯控股",
        "港交所",
        "互联网 · 社交与游戏及AI",
        "港股核心科技",
        "坐拥微信超级流量入口与全球头部游戏研发发行能力，混元大模型与视频号商业化持续增厚高毛利广告与现金流。",
        "hk",
        "HKD",
        "HK$",
    ),
    (
        "09988.HK",
        "阿里巴巴-W",
        "港交所",
        "互联网 · 电商与阿里云",
        "港股核心科技",
        "国内电商基础设施与阿里云底座龙头，通义千问开源生态与云智能 AI 收入加速构成估值重估主线。",
        "hk",
        "HKD",
        "HK$",
    ),
    (
        "03690.HK",
        "美团-W",
        "港交所",
        "互联网 · 本地生活与即时零售",
        "港股核心科技",
        "中国本地生活服务与即时配送绝对领先平台，到店酒旅竞争格局趋稳叠加无人机及中东出海打开增量空间。",
        "hk",
        "HKD",
        "HK$",
    ),
    (
        "01810.HK",
        "小米集团-W",
        "港交所",
        "硬科技 · 人车家全生态",
        "港股核心科技",
        "集智能手机全球前三、IoT 消费级生态与小米汽车高速放量于一体，汽车毛利率超预期驱动估值框架切换。",
        "hk",
        "HKD",
        "HK$",
    ),
    # 加密核心现货
    (
        "BTC",
        "比特币",
        "现货 · Binance/Gate",
        "数字资产 · 核心价值存储",
        "加密核心资产",
        "全球市值最大去中心化数字资产，兼具宏观流动性风向标与机构现货 ETF 配置底仓属性。",
        "crypto",
        "USD",
        "$",
    ),
    (
        "ETH",
        "以太坊",
        "现货 · Binance/Gate",
        "数字资产 · 智能合约与结算层",
        "加密核心资产",
        "全球最大的智能合约、稳定币与链上金融结算公链生态，价格走势对链上活跃度与风险偏好高度敏感。",
        "crypto",
        "USD",
        "$",
    ),
    (
        "SOL",
        "Solana",
        "现货 · Binance/Gate",
        "数字资产 · 高性能公链",
        "加密核心资产",
        "高吞吐量、低手续费单体架构公链，活跃度集中在链上交易、消费级应用与高频金融基础设施。",
        "crypto",
        "USD",
        "$",
    ),
    (
        "BNB",
        "币安币",
        "现货 · Binance/Gate",
        "数字资产 · 交易所与链上生态",
        "加密核心资产",
        "全球头部加密交易平台及 BNB Chain 核心生态资产，表现与全市场现货成交活跃度及销毁通缩机制挂钩。",
        "crypto",
        "USD",
        "$",
    ),
)

MULTI_MARKET_META_MAP: dict[str, dict[str, str]] = {
    row[0]: {
        "symbol": row[0],
        "name_cn": row[1],
        "exchange": row[2],
        "sector_cn": row[3],
        "peer_group": row[4],
        "overview_cn": row[5],
        "market": "us",
        "currency": "USD",
        "currency_symbol": "$",
    }
    for row in CORE_RESEARCH_STOCKS
}
for _row in CORE_MULTI_MARKET_ASSETS:
    MULTI_MARKET_META_MAP[_row[0]] = {
        "symbol": _row[0],
        "name_cn": _row[1],
        "exchange": _row[2],
        "sector_cn": _row[3],
        "peer_group": _row[4],
        "overview_cn": _row[5],
        "market": _row[6],
        "currency": _row[7],
        "currency_symbol": _row[8],
    }

KNOWN_CRYPTO_BASES: frozenset[str] = frozenset(
    {
        "BTC", "ETH", "SOL", "BNB", "DOGE", "XRP", "ADA", "AVAX", "LINK", "SUI",
        "AAVE", "UNI", "ARB", "OP", "TON", "LTC", "TRX", "PEPE", "HYPE", "TAO",
        "RENDER", "WIF", "APT", "NEAR", "FIL", "ATOM", "DOT", "POL", "BCH", "ENA",
        "ONDO", "SHIB", "ICP", "INJ", "SEI", "TIA", "FET", "STX", "ORDI", "XLM", "ETC",
    }
)

_RESEARCH_ALIASES: dict[str, tuple[str, str]] = {
    "茅台": ("600519.SH", "cn"),
    "贵州茅台": ("600519.SH", "cn"),
    "宁德": ("300750.SZ", "cn"),
    "宁德时代": ("300750.SZ", "cn"),
    "比亚迪": ("002594.SZ", "cn"),
    "平安": ("601318.SH", "cn"),
    "中国平安": ("601318.SH", "cn"),
    "中芯": ("688981.SH", "cn"),
    "中芯国际": ("688981.SH", "cn"),
    "五粮液": ("000858.SZ", "cn"),
    "招行": ("600036.SH", "cn"),
    "招商银行": ("600036.SH", "cn"),
    "腾讯": ("00700.HK", "hk"),
    "腾讯控股": ("00700.HK", "hk"),
    "阿里": ("09988.HK", "hk"),
    "阿里巴巴": ("09988.HK", "hk"),
    "美团": ("03690.HK", "hk"),
    "小米": ("01810.HK", "hk"),
    "小米集团": ("01810.HK", "hk"),
    "比特币": ("BTC", "crypto"),
    "大饼": ("BTC", "crypto"),
    "以太坊": ("ETH", "crypto"),
    "以太": ("ETH", "crypto"),
    "二饼": ("ETH", "crypto"),
    "狗狗币": ("DOGE", "crypto"),
    "瑞波": ("XRP", "crypto"),
    "苹果": ("AAPL", "us"),
    "英伟达": ("NVDA", "us"),
    "微软": ("MSFT", "us"),
    "谷歌": ("GOOG", "us"),
    "亚马逊": ("AMZN", "us"),
    "特斯拉": ("TSLA", "us"),
    "台积电": ("TSM", "us"),
    "美光": ("MU", "us"),
    "英特尔": ("INTC", "us"),
    "高通": ("QCOM", "us"),
    "博通": ("AVGO", "us"),
    "甲骨文": ("ORCL", "us"),
    "奈飞": ("NFLX", "us"),
    "超微": ("SMCI", "us"),
    "拼多多": ("PDD", "us"),
    "百度": ("BIDU", "us"),
    "京东": ("JD", "us"),
}
for _sym, _meta in MULTI_MARKET_META_MAP.items():
    _RESEARCH_ALIASES.setdefault(_meta["name_cn"].upper(), (_sym, _meta["market"]))

def _scrub(text: Any) -> str:
    return scrub_forbidden(text, collapse_cjk_spaces=True)


def _fmt_money_compact(val: Any, *, currency: str = "$") -> str:
    num = _finite(val)
    if num is None:
        return "—"
    sign = "-" if num < 0 else ""
    a = abs(num)
    if a >= 1e12:
        return f"{sign}{currency}{a / 1e12:.2f}T"
    if a >= 1e9:
        return f"{sign}{currency}{a / 1e9:.2f}B"
    if a >= 1e6:
        return f"{sign}{currency}{a / 1e6:.2f}M"
    if a >= 1e3:
        return f"{sign}{currency}{a / 1e3:.2f}K"
    return f"{sign}{currency}{a:,.2f}"


def _fmt_px(val: Any, currency: str = "$") -> str:
    num = _finite(val)
    if num is None:
        return "—"
    a = abs(num)
    if a == 0:
        return f"{currency}0.00"
    if a >= 1.0:
        return f"{currency}{num:,.2f}"
    if a >= 0.01:
        return f"{currency}{num:.4f}"
    return f"{currency}{num:.6f}"


def _fmt_num(val: Any, decimals: int = 2, suffix: str = "") -> str:
    num = _finite(val)
    if num is None:
        return "—"
    return f"{num:.{decimals}f}{suffix}"


def _fmt_pct_from_ratio(val: Any, *, decimals: int = 1, signed: bool = False) -> str:
    num = _finite(val)
    if num is None:
        return "—"
    pct = num * 100.0
    prefix = "+" if (signed and pct > 0) else ""
    return f"{prefix}{pct:.{decimals}f}%"


def _gtimg_code(symbol: str) -> str:
    clean = symbol.strip().upper().lstrip(".")
    return f"us{clean}"


def _smartbox_lookup(query: str, *, limit: int = 8, timeout: int = 3) -> list[dict[str, str]]:
    """Query Tencent smartbox for A-share, HK, and US symbols by code, pinyin, or Chinese name."""
    if requests is None or not query.strip():
        return []
    try:
        resp = requests.get(
            f"https://smartbox.gtimg.cn/s3/?v=2&q={urllib.parse.quote(query.strip())}&t=all",
            headers=_HEADERS,
            timeout=timeout,
        )
        if resp.status_code != 200 or not resp.text:
            return []
    except Exception:
        return []

    m = re.search(r'v_hint="([^"]*)"', resp.text)
    if not m or not m.group(1):
        return []

    raw_items = m.group(1).split("^")
    scored: list[tuple[int, dict[str, str]]] = []
    seen: set[str] = set()
    for entry in raw_items:
        parts = entry.split("~")
        if len(parts) < 5:
            continue
        mkt_raw = parts[0].strip().lower()
        code_raw = parts[1].strip()
        try:
            name_raw = code_raw if not parts[2] else parts[2].encode("utf-8").decode("unicode_escape")
        except Exception:
            name_raw = parts[2]
        name_clean = _scrub(name_raw)
        kind = parts[4].strip().upper()
        if "ZS" in kind or "JJ" in kind:
            continue

        canon = ""
        market = ""
        exch_cn = ""
        priority = 10
        if mkt_raw in ("sh", "sz", "bj") and re.fullmatch(r"\d{6}", code_raw):
            if mkt_raw == "sh" and code_raw.startswith(("50", "51", "56", "58")):
                priority = 40
            elif mkt_raw == "sz" and code_raw.startswith(("15", "16", "18")):
                priority = 40
            elif "GP" in kind:
                priority = 10
            else:
                priority = 30
            canon = f"{code_raw}.{mkt_raw.upper()}"
            market = "cn"
            exch_cn = {"sh": "上交所", "sz": "深交所", "bj": "北交所"}[mkt_raw]
        elif mkt_raw == "hk" and re.fullmatch(r"\d{1,5}", code_raw):
            code5 = code_raw.zfill(5)
            # Deprioritize HK RMB dual-counter tickers (80xxx/89xxx)
            priority = 60 if code5.startswith(("80", "89")) else 15
            canon = f"{code5}.HK"
            market = "hk"
            exch_cn = "港交所"
        elif mkt_raw == "us":
            base_us = code_raw.split(".")[0].strip().upper()
            if not re.fullmatch(r"[A-Z]{1,6}(?:-[A-Z])?", base_us):
                continue
            canon = base_us
            market = "us"
            exch_cn = "美股"
            # Prefer primary NYSE/NASDAQ over OTC pink sheets (.ps)
            priority = 50 if code_raw.lower().endswith(".ps") else 12
        else:
            continue

        if canon in seen:
            continue
        seen.add(canon)
        scored.append(
            (
                priority,
                {
                    "symbol": canon,
                    "name": name_clean or canon,
                    "market": market,
                    "exchange": exch_cn,
                },
            )
        )

    scored.sort(key=lambda pair: pair[0])
    return [item for _, item in scored[:limit]]


def resolve_research_symbol(raw: str, *, timeout: int = 3) -> tuple[str, str] | None:
    """Resolve code or Chinese company/coin name into (canonical_symbol, market)."""
    q = str(raw or "").strip()
    if not q or len(q) > 32:
        return None
    if any(ch in q for ch in ("/", "\\", "..", "<", ">", "\x00", "\n", "\r")):
        return None

    up = q.upper()
    if q in _RESEARCH_ALIASES:
        return _RESEARCH_ALIASES[q]
    if up in _RESEARCH_ALIASES:
        return _RESEARCH_ALIASES[up]
    if up in MULTI_MARKET_META_MAP:
        return (up, MULTI_MARKET_META_MAP[up]["market"])

    if up.startswith("CRYPTO:"):
        base = up.split(":", 1)[1].strip()
        base = re.sub(r"(?:-|_)?USDT?$", "", base)
        if re.fullmatch(r"[A-Z0-9]{2,12}", base):
            return (base, "crypto")
        return None

    # Crypto pair formats like BTC-USDT, ETH_USDT, SOL-USD, BTCUSDT
    m_pair = re.fullmatch(r"([A-Z0-9]{2,10})(?:[-_](?:USDT?|USDC)|USDT)", up)
    if m_pair:
        base = m_pair.group(1)
        if base in KNOWN_CRYPTO_BASES or "-" in up or "_" in up or up.endswith("USDT"):
            return (base, "crypto")
    if up in KNOWN_CRYPTO_BASES:
        return (up, "crypto")

    # A-share explicit suffixes/prefixes or standard 6-digit code
    m_cn_suf = re.fullmatch(r"(\d{6})\.(SH|SZ|BJ)", up)
    if m_cn_suf:
        return (f"{m_cn_suf.group(1)}.{m_cn_suf.group(2)}", "cn")
    m_cn_pre = re.fullmatch(r"(SH|SZ|BJ)(\d{6})", up)
    if m_cn_pre:
        return (f"{m_cn_pre.group(2)}.{m_cn_pre.group(1)}", "cn")
    if re.fullmatch(r"\d{6}", up):
        if up.startswith(("6", "5")):
            return (f"{up}.SH", "cn")
        if up.startswith(("0", "3", "1")):
            return (f"{up}.SZ", "cn")
        if up.startswith(("4", "8", "92")):
            return (f"{up}.BJ", "cn")

    # HK explicit suffixes/prefixes or 5-digit 0xxxx
    m_hk_suf = re.fullmatch(r"0*(\d{1,5})\.HK", up)
    if m_hk_suf:
        return (f"{int(m_hk_suf.group(1)):05d}.HK", "hk")
    m_hk_pre = re.fullmatch(r"HK0*(\d{1,5})", up)
    if m_hk_pre:
        return (f"{int(m_hk_pre.group(1)):05d}.HK", "hk")
    if re.fullmatch(r"0\d{4}", up):
        return (f"{up}.HK", "hk")

    # US ticker (ASCII letters/dots, optionally with .US)
    m_us_suf = re.fullmatch(r"([A-Z]{1,6}(?:\.[A-Z]{1,2})?)\.US", up)
    if m_us_suf:
        return (m_us_suf.group(1), "us")
    if re.fullmatch(r"[A-Z]{1,6}(?:\.[A-Z]{1,2})?", up):
        return (up, "us")

    # Fallback: smartbox for Chinese names / Pinyin / partial queries
    sb = _smartbox_lookup(q, limit=1, timeout=timeout)
    if sb:
        return (sb[0]["symbol"], sb[0]["market"])
    return None


def search_research_symbols(query: str, limit: int = 8, *, timeout: int = 3) -> list[dict[str, str]]:
    """Return autocomplete candidates across US, A-share, HK, and Crypto markets."""
    q = str(query or "").strip()
    if not q or len(q) > 40:
        return []
    if any(ch in q for ch in ("/", "\\", "..", "<", ">", "\x00", "\n", "\r")):
        return []

    up = q.upper()
    out: list[dict[str, str]] = []
    seen: set[str] = set()

    def _add(item: dict[str, str]) -> None:
        sym = item["symbol"]
        if sym in seen or len(out) >= limit:
            return
        seen.add(sym)
        out.append(item)

    # Local presets & aliases first
    for sym, meta in MULTI_MARKET_META_MAP.items():
        if up in sym.upper() or q in meta["name_cn"] or up in meta["name_cn"].upper():
            _add(
                {
                    "symbol": sym,
                    "name": meta["name_cn"],
                    "market": meta["market"],
                    "exchange": meta["exchange"],
                }
            )

    for alias_key, (canon, mkt) in _RESEARCH_ALIASES.items():
        if q in alias_key or up in alias_key.upper():
            meta = MULTI_MARKET_META_MAP.get(canon)
            _add(
                {
                    "symbol": canon,
                    "name": meta["name_cn"] if meta else alias_key,
                    "market": mkt,
                    "exchange": meta["exchange"] if meta else {"cn": "A股", "hk": "港股", "us": "美股", "crypto": "加密现货"}[mkt],
                }
            )

    if re.fullmatch(r"[A-Z0-9]{1,8}", up) and any(c.startswith(up) for c in KNOWN_CRYPTO_BASES):
        for coin in sorted(KNOWN_CRYPTO_BASES):
            if coin.startswith(up):
                meta = MULTI_MARKET_META_MAP.get(coin)
                _add(
                    {
                        "symbol": coin,
                        "name": meta["name_cn"] if meta else f"{coin} 现货",
                        "market": "crypto",
                        "exchange": "现货 · Binance/Gate",
                    }
                )

    if len(out) < limit:
        for sb_item in _smartbox_lookup(q, limit=limit, timeout=timeout):
            _add(sb_item)

    return out[:limit]


def fetch_gtimg_batch(symbols: list[str], timeout: int = 6) -> dict[str, dict[str, Any]]:
    """Fast batch quote + valuation snapshot for US tickers from Tencent qt.gtimg.cn."""
    if requests is None or not symbols:
        return {}
    codes = [_gtimg_code(s) for s in symbols]
    out: dict[str, dict[str, Any]] = {}
    received_at = datetime.now(BJT).strftime("%Y-%m-%d %H:%M:%S+08:00")
    for start in range(0, len(codes), 25):
        batch = codes[start : start + 25]
        try:
            resp = requests.get(f"https://qt.gtimg.cn/q={','.join(batch)}", headers=_HEADERS, timeout=timeout)
            if resp.status_code != 200 or not resp.content:
                continue
        except Exception:
            continue
        raw_text = resp.content.decode("gbk", errors="replace")
        for line in raw_text.splitlines():
            if "=" not in line or "~" not in line:
                continue
            parts = line.split("~")
            if len(parts) < 50:
                continue
            kline_sym = parts[2].strip()
            raw_sym = kline_sym.split(".")[0].strip().upper()
            price = _finite(parts[3])
            if not raw_sym or price is None or price <= 0:
                continue
            mcap_yi = _finite(parts[45])
            mcap_usd = mcap_yi * 1e8 if mcap_yi is not None else None
            div_pct = _finite(parts[52]) if len(parts) > 52 else None
            if div_pct is not None and (div_pct < 0 or div_pct >= 25.0):
                div_pct = None
            ts_raw = parts[30].strip()
            out[raw_sym] = {
                "price": round(price, 4 if price < 1 else 2),
                "prev_close": _finite(parts[4]),
                "change_amt": _finite(parts[31]),
                "change_pct": _finite(parts[32]),
                "as_of": ts_raw,
                "market_at": f"{ts_raw} 美东" if ts_raw else received_at,
                "received_at": received_at,
                "source": "tencent_gtimg",
                "session": _infer_us_session(ts_raw),
                "volume": _finite(parts[36]),
                "turnover": _finite(parts[37]),
                "pe_ttm": _finite(parts[39]),
                "pb": _finite(parts[51]) if len(parts) > 51 else None,
                "mcap_raw": mcap_usd,
                "name_cn": parts[1].strip(),
                "name_en": parts[46].strip(),
                "eps_ttm": _finite(parts[47]),
                "high_52w": _finite(parts[48]),
                "low_52w": _finite(parts[49]),
                "dividend_yield_pct": div_pct,
                "gtimg_kline_sym": kline_sym,
            }
    return out


def fetch_ifzq_kline(
    kline_code: str,
    *,
    is_us: bool = False,
    limit: int = 252,
    timeout: int = 5,
) -> dict[str, Any]:
    """Fetch 1Y daily qfq OHLC history from Tencent web.ifzq.gtimg.cn (CN-direct)."""
    if requests is None or not kline_code:
        return {}
    endpoint = (
        f"https://web.ifzq.gtimg.cn/appstock/app/usfqkline/get?param={urllib.parse.quote(kline_code)},day,,,{limit},qfq"
        if is_us
        else f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={urllib.parse.quote(kline_code)},day,,,{limit},qfq"
    )
    try:
        resp = requests.get(endpoint, headers=_HEADERS, timeout=timeout)
        if resp.status_code != 200:
            return {}
        data = (resp.json() or {}).get("data") or {}
    except Exception:
        return {}

    entry = data.get(kline_code)
    if not isinstance(entry, dict) and data:
        first_val = next(iter(data.values()), None)
        entry = first_val if isinstance(first_val, dict) else {}
    if not isinstance(entry, dict):
        return {}

    rows = entry.get("qfqday") or entry.get("day") or []
    dates: list[str] = []
    closes: list[float] = []
    highs: list[float] = []
    lows: list[float] = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 3:
            continue
        dt = str(row[0] or "").strip()[:10]
        cl = _finite(row[2])
        if not dt or cl is None or cl <= 0:
            continue
        hi = _finite(row[3]) if len(row) > 3 else cl
        lo = _finite(row[4]) if len(row) > 4 else cl
        dates.append(dt)
        closes.append(round(cl, 4 if cl < 1 else 2))
        if hi is not None and hi > 0:
            highs.append(hi)
        if lo is not None and lo > 0:
            lows.append(lo)

    if not closes:
        return {}
    return {
        "dates": dates[-limit:],
        "closes": closes[-limit:],
        "high_52w": round(max(highs), 4 if max(highs) < 1 else 2) if highs else None,
        "low_52w": round(min(lows), 4 if min(lows) < 1 else 2) if lows else None,
    }


def _fetch_cn_quote(symbol: str, timeout: int = 4) -> dict[str, Any]:
    """Fetch live A-share quote from qt.gtimg.cn using exact CN field indices."""
    if requests is None:
        return {}
    m = re.fullmatch(r"(\d{6})\.(SH|SZ|BJ)", symbol.strip().upper())
    if not m:
        return {}
    code6, exch = m.group(1), m.group(2)
    q_code = f"{exch.lower()}{code6}"
    try:
        resp = requests.get(f"https://qt.gtimg.cn/q={q_code}", headers=_HEADERS, timeout=timeout)
        if resp.status_code != 200 or not resp.content:
            return {}
    except Exception:
        return {}
    received_at = datetime.now(BJT).strftime("%Y-%m-%d %H:%M:%S+08:00")
    for line in resp.content.decode("gbk", errors="replace").splitlines():
        if "=" not in line or "~" not in line:
            continue
        parts = line.split("~")
        if len(parts) < 47:
            continue
        price = _finite(parts[3])
        if price is None or price <= 0:
            continue
        ts_raw = parts[30].strip()
        ts_fmt = (
            f"{ts_raw[0:4]}-{ts_raw[4:6]}-{ts_raw[6:8]} {ts_raw[8:10]}:{ts_raw[10:12]}"
            if len(ts_raw) >= 12 and ts_raw.isdigit()
            else ts_raw
        )
        vol_lots = _finite(parts[36])
        turnover_wan = _finite(parts[37])
        mcap_yi = _finite(parts[45])
        return {
            "symbol": symbol,
            "name_cn": parts[1].strip(),
            "name_en": parts[1].strip(),
            "price": round(price, 2),
            "prev_close": _finite(parts[4]),
            "change_amt": _finite(parts[31]),
            "change_pct": _finite(parts[32]),
            "as_of": ts_fmt,
            "market_at": f"{ts_fmt} BJT" if ts_fmt else received_at,
            "received_at": received_at,
            "source": "tencent_gtimg",
            "session": "closed",
            "volume": vol_lots * 100.0 if vol_lots is not None else None,
            "turnover": turnover_wan * 1e4 if turnover_wan is not None else None,
            "pe_ttm": _finite(parts[39]),
            "pb": _finite(parts[46]),
            "mcap_raw": mcap_yi * 1e8 if mcap_yi is not None else None,
            "q_code": q_code,
        }
    return {}


def _fetch_cn_f10(symbol: str, timeout: int = 4) -> dict[str, Any]:
    """Fetch A-share fundamental indicators & latest quarters from EastMoney F10."""
    if requests is None:
        return {}
    m = re.fullmatch(r"(\d{6})\.(SH|SZ|BJ)", symbol.strip().upper())
    if not m:
        return {}
    em_code = f"{m.group(2)}{m.group(1)}"
    try:
        resp = requests.get(
            f"https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/ZYZBAjaxNew?type=0&code={em_code}",
            headers=_HEADERS,
            timeout=timeout,
        )
        if resp.status_code != 200:
            return {}
        rows = (resp.json() or {}).get("data") or []
    except Exception:
        return {}
    if not rows or not isinstance(rows[0], dict):
        return {}

    latest = rows[0]
    gm_pct = _finite(latest.get("XSMLL"))
    nm_pct = _finite(latest.get("XSJLL"))
    roe_pct = _finite(latest.get("ROEJQ"))
    rev_yoy_pct = _finite(latest.get("TOTALOPERATEREVETZ"))
    earn_yoy_pct = _finite(latest.get("PARENTNETPROFITTZ"))
    zcfzl = _finite(latest.get("ZCFZL"))
    debt_eq = (zcfzl / (100.0 - zcfzl) * 100.0) if (zcfzl is not None and 0 < zcfzl < 99.0) else None

    quarters: list[dict[str, str]] = []
    for row in rows[:4]:
        if not isinstance(row, dict):
            continue
        period = str(row.get("REPORT_DATE_NAME") or row.get("REPORT_DATE") or "").strip()[:12]
        if not period:
            continue
        rev = _finite(row.get("TOTALOPERATEREVE"))
        net_inc = _finite(row.get("PARENTNETPROFIT"))
        row_gm = _finite(row.get("XSMLL"))
        gp = (rev * row_gm / 100.0) if (rev is not None and row_gm is not None and row_gm > 0) else None
        quarters.append(
            {
                "period": period,
                "revenue_fmt": _fmt_money_compact(rev, currency="¥"),
                "gross_profit_fmt": _fmt_money_compact(gp, currency="¥"),
                "op_income_fmt": "—",
                "net_income_fmt": _fmt_money_compact(net_inc, currency="¥"),
                "fcf_fmt": "—",
            }
        )

    return {
        "eps_ttm": _finite(latest.get("EPSJB")),
        "gross_margin": gm_pct / 100.0 if gm_pct is not None else None,
        "net_margin": nm_pct / 100.0 if nm_pct is not None else None,
        "roe": roe_pct / 100.0 if roe_pct is not None else None,
        "rev_growth": rev_yoy_pct / 100.0 if rev_yoy_pct is not None else None,
        "earnings_growth": earn_yoy_pct / 100.0 if earn_yoy_pct is not None else None,
        "current_ratio": _finite(latest.get("LD")),
        "debt_to_equity": round(debt_eq, 1) if debt_eq is not None else None,
        "ttm_revenue": _finite(latest.get("TOTALOPERATEREVE")),
        "quarters": quarters,
    }


def _fetch_hk_quote(symbol: str, timeout: int = 4) -> dict[str, Any]:
    """Fetch live HK quote from qt.gtimg.cn using exact HK field indices."""
    if requests is None:
        return {}
    m = re.fullmatch(r"(\d{5})\.HK", symbol.strip().upper())
    if not m:
        return {}
    q_code = f"hk{m.group(1)}"
    try:
        resp = requests.get(f"https://qt.gtimg.cn/q={q_code}", headers=_HEADERS, timeout=timeout)
        if resp.status_code != 200 or not resp.content:
            return {}
    except Exception:
        return {}
    received_at = datetime.now(BJT).strftime("%Y-%m-%d %H:%M:%S+08:00")
    for line in resp.content.decode("gbk", errors="replace").splitlines():
        if "=" not in line or "~" not in line:
            continue
        parts = line.split("~")
        if len(parts) < 50:
            continue
        price = _finite(parts[3])
        if price is None or price <= 0:
            continue
        mcap_yi = _finite(parts[45])
        div_pct = _finite(parts[47])
        if div_pct is not None and (div_pct <= 0 or div_pct >= 25.0):
            div_pct = None
        ts_raw = parts[30].strip()
        return {
            "symbol": symbol,
            "name_cn": parts[1].strip(),
            "name_en": parts[46].strip() or parts[1].strip(),
            "price": round(price, 3 if price < 10 else 2),
            "prev_close": _finite(parts[4]),
            "change_amt": _finite(parts[31]),
            "change_pct": _finite(parts[32]),
            "as_of": ts_raw,
            "market_at": f"{ts_raw} HKT" if ts_raw else received_at,
            "received_at": received_at,
            "source": "tencent_gtimg",
            "session": "closed",
            "volume": _finite(parts[36]),
            "turnover": _finite(parts[37]),
            "pe_ttm": _finite(parts[39]),
            "mcap_raw": mcap_yi * 1e8 if mcap_yi is not None else None,
            "dividend_yield_pct": div_pct,
            "high_52w": _finite(parts[48]),
            "low_52w": _finite(parts[49]),
            "q_code": q_code,
        }
    return {}


def _fetch_hk_f10(symbol: str, timeout: int = 4) -> dict[str, Any]:
    """Fetch HK fundamental indicators sorted newest-first from EastMoney Datacenter."""
    if requests is None:
        return {}
    m = re.fullmatch(r"(\d{5})\.HK", symbol.strip().upper())
    if not m:
        return {}
    code5 = m.group(1)
    url = (
        "https://datacenter.eastmoney.com/securities/api/data/v1/get"
        f"?reportName=RPT_HKF10_FN_MAININDICATOR&columns=ALL"
        f"&filter=(SECURITY_CODE%3D%22{code5}%22)"
        "&sortColumns=REPORT_DATE&sortTypes=-1&pageSize=4"
    )
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=timeout)
        if resp.status_code != 200:
            return {}
        rows = ((resp.json() or {}).get("result") or {}).get("data") or []
    except Exception:
        return {}
    if not rows or not isinstance(rows[0], dict):
        return {}

    latest = rows[0]
    rpt_cur = "¥" if str(latest.get("CURRENCY") or "").upper() in ("CNY", "RMB", "人民币") else "HK$"
    gm_pct = _finite(latest.get("GROSS_PROFIT_RATIO"))
    nm_pct = _finite(latest.get("NET_PROFIT_RATIO"))
    roe_pct = _finite(latest.get("ROE_YEARLY") if latest.get("ROE_YEARLY") is not None else latest.get("ROE_AVG"))
    roa_pct = _finite(latest.get("ROA"))
    rev_yoy = _finite(latest.get("OPERATE_INCOME_YOY"))
    earn_yoy = _finite(latest.get("HOLDER_PROFIT_YOY"))
    div_pct = _finite(latest.get("DIVIDEND_RATE"))
    if div_pct is not None and (div_pct <= 0 or div_pct >= 25.0):
        div_pct = None
    liab = _finite(latest.get("TOTAL_LIABILITIES"))
    eq = _finite(latest.get("TOTAL_PARENT_EQUITY"))
    debt_eq = (liab / eq * 100.0) if (liab is not None and eq is not None and eq > 0) else None
    if debt_eq is None:
        eq_ratio = _finite(latest.get("EQUITY_RATIO"))
        if eq_ratio is not None:
            debt_eq = eq_ratio * 100.0 if eq_ratio < 5.0 else eq_ratio

    quarters: list[dict[str, str]] = []
    for row in rows[:4]:
        if not isinstance(row, dict):
            continue
        period = str(row.get("REPORT_TYPE") or row.get("REPORT_DATE") or "").strip()[:14]
        if not period:
            continue
        rcur = "¥" if str(row.get("CURRENCY") or "").upper() in ("CNY", "RMB", "人民币") else "HK$"
        rev = _finite(row.get("OPERATE_INCOME"))
        gp = _finite(row.get("GROSS_PROFIT"))
        if gp == 0 and rev is not None and rev > 0:
            gp = None
        quarters.append(
            {
                "period": period,
                "revenue_fmt": _fmt_money_compact(rev, currency=rcur),
                "gross_profit_fmt": _fmt_money_compact(gp, currency=rcur),
                "op_income_fmt": _fmt_money_compact(_finite(row.get("OPERATE_PROFIT")), currency=rcur),
                "net_income_fmt": _fmt_money_compact(_finite(row.get("HOLDER_PROFIT")), currency=rcur),
                "fcf_fmt": _fmt_money_compact(_finite(row.get("NETCASH_OPERATE")), currency=rcur),
            }
        )

    return {
        "pe_ttm": _finite(latest.get("PE_TTM")),
        "pb": _finite(latest.get("PB_TTM")),
        "eps_ttm": _finite(latest.get("EPS_TTM") if latest.get("EPS_TTM") is not None else latest.get("BASIC_EPS")),
        "dividend_yield_pct": div_pct,
        "gross_margin": gm_pct / 100.0 if gm_pct is not None else None,
        "net_margin": nm_pct / 100.0 if nm_pct is not None else None,
        "roe": roe_pct / 100.0 if roe_pct is not None else None,
        "roa": roa_pct / 100.0 if roa_pct is not None else None,
        "rev_growth": rev_yoy / 100.0 if rev_yoy is not None else None,
        "earnings_growth": earn_yoy / 100.0 if earn_yoy is not None else None,
        "current_ratio": _finite(latest.get("CURRENT_RATIO")),
        "debt_to_equity": round(debt_eq, 1) if debt_eq is not None else None,
        "total_cash": _finite(latest.get("END_CASH")),
        "total_debt": liab,
        "ttm_revenue": _finite(latest.get("OPERATE_INCOME")),
        "ttm_ocf": _finite(latest.get("NETCASH_OPERATE")),
        "quarters": quarters,
        "report_currency": rpt_cur,
    }


def _fetch_us_f10(symbol: str, timeout: int = 4) -> dict[str, Any]:
    """Fetch CN-reachable US fundamental indicators from EastMoney Datacenter."""
    if requests is None:
        return {}
    sym = symbol.strip().upper()
    if not re.fullmatch(r"[A-Z]{1,6}(?:\.[A-Z]{1,2})?", sym):
        return {}
    url = (
        "https://datacenter.eastmoney.com/securities/api/data/v1/get"
        f"?reportName=RPT_USF10_FN_GMAININDICATOR&columns=ALL"
        f"&filter=(SECURITY_CODE%3D%22{urllib.parse.quote(sym)}%22)"
        "&sortColumns=REPORT_DATE&sortTypes=-1&pageSize=4"
    )
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=timeout)
        if resp.status_code != 200:
            return {}
        rows = ((resp.json() or {}).get("result") or {}).get("data") or []
    except Exception:
        return {}
    if not rows or not isinstance(rows[0], dict):
        return {}

    latest = rows[0]
    gm_pct = _finite(latest.get("GROSS_PROFIT_RATIO"))
    nm_pct = _finite(latest.get("NET_PROFIT_RATIO"))
    roe_pct = _finite(latest.get("ROE_AVG"))
    roa_pct = _finite(latest.get("ROA"))
    rev_yoy = _finite(latest.get("OPERATE_INCOME_YOY"))
    earn_yoy = _finite(latest.get("PARENT_HOLDER_NETPROFIT_YOY"))

    quarters: list[dict[str, str]] = []
    for row in rows[:4]:
        if not isinstance(row, dict):
            continue
        period = str(row.get("REPORT_TYPE") or row.get("REPORT_DATE") or "").strip()[:14]
        if not period:
            continue
        cur_abbr = str(row.get("CURRENCY_ABBR") or row.get("CURRENCY") or "").upper()
        rcur = "¥" if cur_abbr in ("CNY", "RMB", "人民币") else "$"
        rev = _finite(row.get("OPERATE_INCOME"))
        gp = _finite(row.get("GROSS_PROFIT"))
        if gp == 0 and rev is not None and rev > 0:
            gp = None
        quarters.append(
            {
                "period": period,
                "revenue_fmt": _fmt_money_compact(rev, currency=rcur),
                "gross_profit_fmt": _fmt_money_compact(gp, currency=rcur),
                "op_income_fmt": "—",
                "net_income_fmt": _fmt_money_compact(_finite(row.get("PARENT_HOLDER_NETPROFIT")), currency=rcur),
                "fcf_fmt": "—",
            }
        )

    return {
        "eps_ttm": _finite(latest.get("BASIC_EPS")),
        "gross_margin": gm_pct / 100.0 if gm_pct is not None else None,
        "net_margin": nm_pct / 100.0 if nm_pct is not None else None,
        "roe": roe_pct / 100.0 if roe_pct is not None else None,
        "roa": roa_pct / 100.0 if roa_pct is not None else None,
        "rev_growth": rev_yoy / 100.0 if rev_yoy is not None else None,
        "earnings_growth": earn_yoy / 100.0 if earn_yoy is not None else None,
        "current_ratio": _finite(latest.get("CURRENT_RATIO")),
        "debt_to_equity": _finite(latest.get("EQUITY_RATIO")),
        "ttm_revenue": _finite(latest.get("OPERATE_INCOME")),
        "quarters": quarters,
    }


def _create_yahoo_session(timeout: int = 7) -> tuple[Any, str] | tuple[None, str]:
    if requests is None:
        return (None, "")
    sess = requests.Session()
    sess.headers.update(_HEADERS)
    try:
        sess.get("https://fc.yahoo.com", timeout=timeout)
        crumb_resp = sess.get("https://query2.finance.yahoo.com/v1/test/getcrumb", timeout=timeout)
        crumb = crumb_resp.text.strip() if crumb_resp.status_code == 200 else ""
        if crumb and "<" not in crumb and len(crumb) <= 64:
            return (sess, crumb)
    except Exception:
        pass
    return (None, "")


def _y_raw(obj: Any) -> float | None:
    if isinstance(obj, dict):
        return _finite(obj.get("raw"))
    return _finite(obj)


def _compute_return(closes: list[float], trading_days: int) -> float | None:
    if trading_days <= 0 or len(closes) < trading_days + 1:
        return None
    idx = len(closes) - 1 - trading_days
    base = closes[idx]
    last = closes[-1]
    if base <= 0:
        return None
    return round((last - base) / base * 100.0, 2)


def _fetch_yahoo_one(symbol: str, sess: Any, crumb: str, timeout: int = 8) -> dict[str, Any] | None:
    sym_u = symbol.strip().upper()
    if sess is None:
        return None

    summary: dict[str, Any] = {}
    if crumb:
        mods = (
            "assetProfile,summaryDetail,defaultKeyStatistics,"
            "financialData,incomeStatementHistoryQuarterly,"
            "cashflowStatementHistoryQuarterly,calendarEvents,price"
        )
        try:
            rj = sess.get(
                f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{sym_u}",
                params={"modules": mods, "crumb": crumb},
                timeout=timeout,
            )
            if rj.status_code == 200:
                results = ((rj.json() or {}).get("quoteSummary") or {}).get("result") or []
                if results and isinstance(results[0], dict):
                    summary = results[0]
        except Exception:
            summary = {}

    dates: list[str] = []
    closes: list[float] = []
    try:
        rc = sess.get(
            f"https://query2.finance.yahoo.com/v8/finance/chart/{sym_u}",
            params={"range": "1y", "interval": "1d"},
            timeout=timeout,
        )
        if rc.status_code == 200:
            res = ((rc.json() or {}).get("chart") or {}).get("result") or []
            if res:
                ts_list = res[0].get("timestamp") or []
                raw_closes = (((res[0].get("indicators") or {}).get("quote") or [{}])[0]).get("close") or []
                for ts, c in zip(ts_list, raw_closes):
                    cf = _finite(c)
                    if cf is not None and cf > 0:
                        dates.append(datetime.fromtimestamp(int(ts), tz=UTC).strftime("%Y-%m-%d"))
                        closes.append(round(cf, 2))
    except Exception:
        pass

    if not summary and not closes:
        return None

    sd = summary.get("summaryDetail") or {}
    ks = summary.get("defaultKeyStatistics") or {}
    fd = summary.get("financialData") or {}
    ap = summary.get("assetProfile") or {}
    ce = summary.get("calendarEvents") or {}
    pr = summary.get("price") or {}

    earnings_date = ""
    earn_list = (ce.get("earnings") or {}).get("earningsDate") or []
    if earn_list and isinstance(earn_list[0], dict):
        earnings_date = str(earn_list[0].get("fmt") or "").strip()[:10]

    # Normalize dividend yield strictly as percentage (e.g. 0.30 means 0.30%)
    div_raw = _y_raw(sd.get("dividendYield"))
    div_pct: float | None = None
    if div_raw is not None:
        candidate = div_raw * 100.0 if div_raw <= 0.2 else div_raw
        if 0 <= candidate < 25.0:
            div_pct = round(candidate, 2)

    # Parse up to 4 recent quarters from incomeStatementHistoryQuarterly + cashflowStatementHistoryQuarterly
    inc_q = (summary.get("incomeStatementHistoryQuarterly") or {}).get("incomeStatementHistory") or []
    cf_q = (summary.get("cashflowStatementHistoryQuarterly") or {}).get("cashflowStatements") or []
    cf_by_end: dict[str, dict[str, Any]] = {}
    for row in cf_q:
        end_fmt = ((row.get("endDate") or {}).get("fmt") or "").strip()
        if end_fmt:
            cf_by_end[end_fmt] = row

    quarters: list[dict[str, str]] = []
    for row in inc_q[:4]:
        end_fmt = ((row.get("endDate") or {}).get("fmt") or "").strip()
        if not end_fmt:
            continue
        cf_match = cf_by_end.get(end_fmt) or {}
        ocf = _y_raw(cf_match.get("totalCashFromOperatingActivities"))
        capex = _y_raw(cf_match.get("capitalExpenditures"))
        fcf_val: float | None = None
        if ocf is not None and capex is not None:
            fcf_val = ocf - abs(capex)
        rev_raw = _y_raw(row.get("totalRevenue"))
        gp_raw = _y_raw(row.get("grossProfit"))
        if gp_raw == 0 and rev_raw is not None and rev_raw > 0:
            gp_raw = None
        quarters.append(
            {
                "period": end_fmt,
                "revenue_fmt": _fmt_money_compact(rev_raw),
                "gross_profit_fmt": _fmt_money_compact(gp_raw),
                "op_income_fmt": _fmt_money_compact(_y_raw(row.get("operatingIncome"))),
                "net_income_fmt": _fmt_money_compact(_y_raw(row.get("netIncome"))),
                "fcf_fmt": _fmt_money_compact(fcf_val),
            }
        )

    rec_key = str(fd.get("recommendationKey") or "").lower().replace("_", " ").strip()
    rec_cn_map = {
        "strong buy": "强烈买入",
        "buy": "买入",
        "hold": "持有",
        "underperform": "跑输大盘",
        "sell": "卖出",
    }
    analyst_rating = rec_cn_map.get(rec_key, rec_key.title() if rec_key else "—")

    return {
        "name_en": _scrub(pr.get("longName") or pr.get("shortName") or ""),
        "exchange": _scrub(pr.get("exchangeName") or ""),
        "sector_en": _scrub(ap.get("sector") or ""),
        "industry_en": _scrub(ap.get("industry") or ""),
        "price": _y_raw(fd.get("currentPrice")) or _y_raw(pr.get("regularMarketPrice")),
        "change_pct": (_y_raw(pr.get("regularMarketChangePercent")) * 100.0)
        if _y_raw(pr.get("regularMarketChangePercent")) is not None
        else None,
        "change_amt": _y_raw(pr.get("regularMarketChange")),
        "market_cap_raw": _y_raw(sd.get("marketCap")) or _y_raw(pr.get("marketCap")),
        "pe_ttm": _y_raw(sd.get("trailingPE")),
        "pe_forward": _y_raw(sd.get("forwardPE")) or _y_raw(ks.get("forwardPE")),
        "ps_ttm": _y_raw(sd.get("priceToSalesTrailing12Months")),
        "pb": _y_raw(ks.get("priceToBook")),
        "ev_ebitda": _y_raw(ks.get("enterpriseToEbitda")),
        "peg": _y_raw(ks.get("pegRatio")),
        "eps_ttm": _y_raw(ks.get("trailingEps")),
        "dividend_yield_pct": div_pct,
        "volume_raw": _y_raw(sd.get("volume")) or _y_raw(pr.get("regularMarketVolume")),
        "low_52w": _y_raw(sd.get("fiftyTwoWeekLow")),
        "high_52w": _y_raw(sd.get("fiftyTwoWeekHigh")),
        "beta": _y_raw(sd.get("beta")) or _y_raw(ks.get("beta")),
        "short_ratio": _y_raw(ks.get("shortRatio")),
        "insider_ratio": _y_raw(ks.get("heldPercentInsiders")),
        "institution_ratio": _y_raw(ks.get("heldPercentInstitutions")),
        "target_mean_price": _y_raw(fd.get("targetMeanPrice")),
        "analyst_rating": analyst_rating,
        "analyst_count": int(fd["numberOfAnalystOpinions"]["raw"])
        if isinstance(fd.get("numberOfAnalystOpinions"), dict) and _finite(fd["numberOfAnalystOpinions"].get("raw")) is not None
        else None,
        "rev_growth": _y_raw(fd.get("revenueGrowth")),
        "earnings_growth": _y_raw(fd.get("earningsGrowth")),
        "gross_margin": _y_raw(fd.get("grossMargins")),
        "operating_margin": _y_raw(fd.get("operatingMargins")),
        "net_margin": _y_raw(fd.get("profitMargins")),
        "roe": _y_raw(fd.get("returnOnEquity")),
        "roa": _y_raw(fd.get("returnOnAssets")),
        "current_ratio": _y_raw(fd.get("currentRatio")),
        "debt_to_equity": _y_raw(fd.get("debtToEquity")),
        "total_cash": _y_raw(fd.get("totalCash")),
        "total_debt": _y_raw(fd.get("totalDebt")),
        "ttm_revenue": _y_raw(fd.get("totalRevenue")),
        "ttm_ebitda": _y_raw(fd.get("ebitda")),
        "ttm_ocf": _y_raw(fd.get("operatingCashflow")),
        "ttm_fcf": _y_raw(fd.get("freeCashflow")),
        "earnings_date": earnings_date,
        "quarters": quarters,
        "chart": {
            "dates": dates[-252:],
            "closes": closes[-252:],
            "return_1w": _compute_return(closes, 5),
            "return_1m": _compute_return(closes, 21),
            "return_3m": _compute_return(closes, 63),
            "return_6m": _compute_return(closes, 126),
            "return_1y": _compute_return(closes, 250),
        },
        "yahoo_fetched_at": time.time(),
    }


def _build_health(yahoo: dict[str, Any], gtimg: dict[str, Any]) -> dict[str, Any]:
    net_m = _finite(yahoo.get("net_margin"))
    roe = _finite(yahoo.get("roe"))
    gross_m = _finite(yahoo.get("gross_margin"))
    rev_g = _finite(yahoo.get("rev_growth"))
    fcf = _finite(yahoo.get("ttm_fcf"))
    curr_r = _finite(yahoo.get("current_ratio"))
    pe = _finite(gtimg.get("pe_ttm") if gtimg.get("pe_ttm") is not None else yahoo.get("pe_ttm"))

    net_pass = (net_m is not None and net_m > 0) or (pe is not None and pe > 0)
    roe_pass = roe is not None and roe > 0.10
    gross_pass = gross_m is not None and gross_m > 0.30
    rev_pass = rev_g is not None and rev_g > 0

    checks = [
        {"label": "净利率 > 0", "pass": bool(net_pass), "detail": _fmt_pct_from_ratio(net_m)},
        {"label": "ROE > 10%", "pass": bool(roe_pass), "detail": _fmt_pct_from_ratio(roe)},
        {"label": "毛利率 > 30%", "pass": bool(gross_pass), "detail": _fmt_pct_from_ratio(gross_m)},
        {"label": "营收正增长", "pass": bool(rev_pass), "detail": _fmt_pct_from_ratio(rev_g, signed=True)},
    ]

    score = 2
    if net_pass:
        score += 2
    if roe_pass:
        score += 2
    if gross_pass:
        score += 1
    if rev_pass:
        score += 1
    if fcf is not None and fcf > 0:
        score += 1
    if curr_r is not None and curr_r >= 1.0:
        score += 1
    score = max(1, min(10, score))
    return {"score": score, "checks": checks}


def _load_default_risk_gate() -> dict[str, Any]:
    state_p = DATA_DIR / "cockpit_state.json"
    if state_p.exists():
        try:
            raw = json.loads(state_p.read_text(encoding="utf-8"))
            rg = raw.get("risk_gate")
            if isinstance(rg, dict):
                return rg
        except Exception:
            pass
    return {"light": "YELLOW", "label": "黄灯 · 常规控险", "long_risk_cap": 0.5}


def _build_pre_trade_checklist(
    *,
    price: float,
    closes: list[float],
    low_52w: float | None,
    high_52w: float | None,
    risk_gate: dict[str, Any] | None = None,
    currency_symbol: str = "$",
) -> dict[str, Any]:
    px_prec = 4 if closes and abs(price) < 1 else 2
    ma20 = round(sum(closes[-20:]) / 20.0, px_prec) if len(closes) >= 20 else None
    ma50 = round(sum(closes[-50:]) / 50.0, px_prec) if len(closes) >= 50 else None
    bias20_pct = round((price - ma20) / ma20 * 100.0, 2) if (ma20 is not None and ma20 > 0) else None
    bias50_pct = round((price - ma50) / ma50 * 100.0, 2) if (ma50 is not None and ma50 > 0) else None

    range_52w_pos: float | None = None
    if low_52w is not None and high_52w is not None and high_52w > low_52w:
        range_52w_pos = round(max(0.0, min(100.0, (price - low_52w) / (high_52w - low_52w) * 100.0)), 1)

    checks: list[dict[str, str]] = []
    if ma20 is not None and bias20_pct is not None:
        above20 = price >= ma20
        checks.append(
            {
                "key": "ma20_trend",
                "label": "20日均线位置",
                "status": "pass" if above20 else "warn",
                "detail": f"现价 {_fmt_px(price, currency_symbol)} {'站上' if above20 else '低于'} MA20 ({_fmt_px(ma20, currency_symbol)})，乖离 {bias20_pct:+.2f}%",
            }
        )
    if ma50 is not None and bias50_pct is not None:
        above50 = price >= ma50
        checks.append(
            {
                "key": "ma50_trend",
                "label": "50日均线趋势",
                "status": "pass" if above50 else "warn",
                "detail": f"现价 {_fmt_px(price, currency_symbol)} {'站上' if above50 else '低于'} MA50 ({_fmt_px(ma50, currency_symbol)})，乖离 {bias50_pct:+.2f}%",
            }
        )

    chase_alert = "unknown"
    if bias20_pct is not None:
        if bias20_pct > 8.0:
            chase_alert = "overextended_long"
            checks.append(
                {
                    "key": "chase_guard",
                    "label": "顺势追高纪律",
                    "status": "warn",
                    "detail": f"MA20 乖离 {bias20_pct:+.2f}% > +8%，处于短线过热区，禁止盲目追涨",
                }
            )
        elif bias20_pct < -8.0:
            chase_alert = "overextended_short"
            checks.append(
                {
                    "key": "chase_guard",
                    "label": "急跌防追空纪律",
                    "status": "warn",
                    "detail": f"MA20 乖离 {bias20_pct:+.2f}% < -8%，短线超跌远离均线，谨防回抽且不盲目抄底",
                }
            )
        else:
            chase_alert = "normal"
            checks.append(
                {
                    "key": "chase_guard",
                    "label": "乖离率安全带",
                    "status": "pass",
                    "detail": f"MA20 乖离 {bias20_pct:+.2f}% 位于 [-8%, +8%] 可控区间",
                }
            )

    if range_52w_pos is not None:
        pos_status = "warn" if range_52w_pos >= 90.0 else "pass"
        pos_desc = "接近52周高位" if range_52w_pos >= 80.0 else ("位于52周低位区" if range_52w_pos <= 25.0 else "处于52周中位带")
        checks.append(
            {
                "key": "range_52w",
                "label": "52周区间位置",
                "status": pos_status,
                "detail": f"处于 52 周区间 {range_52w_pos:.1f}% 分位（{pos_desc}）",
            }
        )

    rg = risk_gate or _load_default_risk_gate()
    rg_light = str(rg.get("light") or "YELLOW").upper()
    rg_cap = float(rg.get("long_risk_cap") if rg.get("long_risk_cap") is not None else 0.5)
    rg_label = _scrub(rg.get("label") or "宏观控险门控")
    if rg_light == "RED" or rg_cap <= 0.0:
        rg_status = "block"
        rg_detail = f"{rg_label}（方向性多头上限 {rg_cap:.1f}x），系统冻结新开多头"
    elif rg_light == "YELLOW" or rg_cap < 1.0:
        rg_status = "warn"
        rg_detail = f"{rg_label}（方向性多头上限 {rg_cap:.1f}x），单笔仓位须按上限折减"
    else:
        rg_status = "pass"
        rg_detail = f"{rg_label}（方向性多头上限 {rg_cap:.1f}x），宏观门控放行"
    checks.append(
        {
            "key": "risk_gate",
            "label": "宏观风控门控",
            "status": rg_status,
            "detail": rg_detail,
        }
    )

    if rg_status == "block":
        verdict = "红灯拦截 · 暂不开新多"
    elif chase_alert == "overextended_long":
        verdict = "短线过热 · 等回踩均线"
    elif chase_alert == "overextended_short":
        verdict = "超跌远离均线 · 勿追空或盲目接飞刀"
    elif rg_status == "warn":
        verdict = f"控险可做 · 多头上限 {rg_cap:.1f}x"
    else:
        verdict = "纪律通过 · 按计划执行"

    return {
        "ma20": ma20,
        "ma50": ma50,
        "bias20_pct": bias20_pct,
        "bias50_pct": bias50_pct,
        "range_52w_pos": range_52w_pos,
        "chase_alert": chase_alert,
        "risk_gate_light": rg_light,
        "risk_gate_cap": round(rg_cap, 2),
        "checks": checks,
        "verdict": verdict,
    }


def _parse_pct_value(raw: Any) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw) if math.isfinite(float(raw)) else None
    s = str(raw).strip().replace(",", "").rstrip("%")
    if not s or s in ("—", "-"):
        return None
    try:
        v = float(s)
        return v if math.isfinite(v) else None
    except ValueError:
        return None


def _build_crypto_quick_thesis(
    profile: dict[str, Any],
    *,
    risk_gate: dict[str, Any] | None = None,
    signals: list[dict[str, Any]] | None = None,
    kol_digest: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    sym = str(profile.get("symbol") or "").strip().upper()
    cn_name = str(profile.get("name_cn") or sym).strip()
    price = _finite(profile.get("price"))
    bias20 = _finite(profile.get("bias20_pct"))
    bias50 = _finite(profile.get("bias50_pct"))
    pos_52w = _finite(profile.get("range_52w_pos"))
    checklist = profile.get("pre_trade_checklist") or {}
    chase_alert = str(checklist.get("chase_alert") or "normal")
    health = profile.get("health") or {}
    h_score = _finite(health.get("score"))
    cm = profile.get("crypto_metrics") or {}
    vol_30d = _finite(cm.get("vol_30d_ann_pct"))
    mdd_90d = _finite(cm.get("max_drawdown_90d_pct"))
    vol_ratio = _finite(cm.get("vol_ratio_30d"))
    turnover_24h = str(cm.get("turnover_24h_fmt") or (profile.get("overview") or {}).get("turnover_fmt") or "—")

    rg = risk_gate or {}
    gate_light = str(rg.get("light") or checklist.get("risk_gate_light") or "YELLOW").upper()
    gate_cap = _finite(rg.get("long_risk_cap"))
    if gate_cap is None:
        gate_cap = _finite(checklist.get("risk_gate_cap"))
    if gate_cap is None:
        gate_cap = 0.0 if gate_light == "RED" else (0.5 if gate_light == "YELLOW" else 1.0)
    gate_label = str(rg.get("label") or ("红灯防守" if gate_light == "RED" else ("绿灯顺风" if gate_light == "GREEN" else "黄灯控险"))).strip()

    bull_sig_titles: list[str] = []
    bear_sig_titles: list[str] = []
    sym_re = re.compile(rf"(?<![A-Z0-9]){re.escape(sym)}(?![A-Z0-9])") if sym else None
    for s in signals or []:
        if not isinstance(s, dict):
            continue
        tickers_up = {str(t).strip().upper() for t in (s.get("tickers") or []) if t}
        ttl = str(s.get("title") or "").strip()
        txt = f"{ttl} {s.get('summary') or ''}"
        matched = (sym in tickers_up) or (sym_re is not None and bool(sym_re.search(txt.upper())))
        if not matched and cn_name and len(cn_name) >= 2 and cn_name in txt:
            matched = True
        if not matched:
            continue
        d = str(s.get("direction") or "neutral").lower()
        if d == "bullish" and ttl and ttl not in bull_sig_titles:
            bull_sig_titles.append(ttl)
        elif d == "bearish" and ttl and ttl not in bear_sig_titles:
            bear_sig_titles.append(ttl)

    kol_bull = 0
    kol_bear = 0
    for k in kol_digest or []:
        if not isinstance(k, dict):
            continue
        k_txt = f"{k.get('headline') or ''} {k.get('takeaway') or ''}"
        if (sym_re is not None and sym_re.search(k_txt.upper())) or (cn_name and len(cn_name) >= 2 and cn_name in k_txt):
            st_k = str(k.get("stance") or "")
            if "偏多" in st_k:
                kol_bull += 1
            elif "偏空" in st_k:
                kol_bear += 1

    raw_score = 52.0
    if h_score is not None:
        raw_score += (h_score - 5.0) * 3.5
    if bias50 is not None:
        raw_score += 7.0 if bias50 > 0 else -7.0
    if chase_alert == "overextended_long" or (bias20 is not None and bias20 > 10.0):
        raw_score -= 6.0
    elif bias20 is not None and 0.0 <= bias20 <= 8.0:
        raw_score += 5.0
    elif bias20 is not None and bias20 < -10.0:
        raw_score -= 8.0
    if vol_ratio is not None and vol_ratio >= 1.3 and (profile.get("change_pct") or 0) > 0:
        raw_score += 4.0
    if mdd_90d is not None and abs(mdd_90d) >= 35.0:
        raw_score -= 6.0
    raw_score += max(-4, min(4, len(bull_sig_titles) - len(bear_sig_titles))) * 2.0
    raw_score += max(-2, min(2, kol_bull - kol_bear)) * 2.0
    if gate_light == "RED":
        raw_score -= 12.0
    elif gate_light == "YELLOW":
        raw_score -= 4.0
    elif gate_light == "GREEN":
        raw_score += 3.0

    score = int(round(max(0.0, min(100.0, raw_score))))
    downtrend_stretched = bias20 is not None and bias20 < -8.0

    if chase_alert == "overextended_long" or (bias20 is not None and bias20 > 8.0):
        stance = "追高警示"
    elif gate_light == "RED" and (downtrend_stretched or score < 62):
        stance = "偏空逆风"
    elif score <= 44:
        stance = "偏空逆风"
    elif score >= 64 and gate_light != "RED" and not downtrend_stretched:
        stance = "偏多顺风"
    else:
        stance = "中性观望"

    bulls: list[str] = []
    if bias50 is not None and bias50 > 0:
        bulls.append(f"现价站稳 50 日均线（偏离 {bias50:+.1f}%），中期结构维持多头")
    if turnover_24h and turnover_24h != "—":
        vr_txt = f"（较 30 日均值 {vol_ratio:.2f}x）" if vol_ratio is not None else ""
        bulls.append(f"24h 现货成交额 {turnover_24h}{vr_txt}，流动性充沛")
    if bull_sig_titles:
        bulls.append(f"站内命中 {len(bull_sig_titles)} 条偏多加密快讯：{bull_sig_titles[0][:44]}")
    if pos_52w is not None and len(bulls) < 2:
        px_txt = f"最新价 {_fmt_px(price, '$')}，" if price is not None else ""
        bulls.append(f"{px_txt}位于近一年区间 {pos_52w:.1f}% 分位")
    if len(bulls) < 2:
        bulls.append(f"{cn_name} 现货连续交易度高，关注 ETF 资金流与宏观流动性催化")

    bears: list[str] = []
    if gate_light in ("RED", "YELLOW"):
        bears.append(f"宏观风控「{gate_label}」限制方向性多头上限至 {gate_cap:.1f}x，忌高杠杆重仓")
    if bias20 is not None:
        if bias20 > 8.0:
            bears.append(f"MA20 正乖离 {bias20:+.1f}% 已超过 +8% 警示线，短线谨防插针回踩")
        elif bias20 < -8.0:
            bears.append(f"现价低于 MA20 达 {abs(bias20):.1f}%，空头动量占优，忌盲目左侧抄底")
        else:
            bears.append(f"MA20 乖离 {bias20:+.1f}%，留意关键均线放量得失")
    if vol_30d is not None or mdd_90d is not None:
        v_part = f"30日年化波动 {vol_30d:.1f}%" if vol_30d is not None else ""
        d_part = f"90日最大回撤 {mdd_90d:.1f}%" if mdd_90d is not None else ""
        sep = "，" if (v_part and d_part) else ""
        bears.append(f"{v_part}{sep}{d_part}，仓位须按波动率严格折减")
    if bear_sig_titles and len(bears) < 3:
        bears.append(f"近期录得 {len(bear_sig_titles)} 条偏空快讯提醒：{bear_sig_titles[0][:44]}")
    if len(bears) < 2:
        bears.append("数字资产 7×24 小时交易易受宏观数据与衍生品去杠杆放大波动，务必预留强平缓冲")

    if stance == "追高警示":
        action_hint = "短线乖离处于过热区，严禁合约追高加杠杆，等回踩 MA20 缩量企稳后再评估。"
    elif stance == "偏多顺风":
        action_hint = f"趋势结构与流动性共振偏强，可结合风控上限（{gate_cap:.1f}x）分批配置并设好移动止损。"
    elif stance == "偏空逆风":
        action_hint = "宏观门控与均线趋势双紧，优先控制杠杆与强平距离，等待日线站稳均线后再转攻。"
    else:
        action_hint = f"多空处在震荡消化期（多头上限 {gate_cap:.1f}x），轻仓观察成交量与关键支撑位响应。"

    return {
        "stance": stance,
        "score": score,
        "bull_points": [_scrub(x) for x in bulls[:3]],
        "bear_points": [_scrub(x) for x in bears[:3]],
        "action_hint": _scrub(action_hint),
    }


def build_quick_thesis(
    profile: dict[str, Any],
    *,
    risk_gate: dict[str, Any] | None = None,
    signals: list[dict[str, Any]] | None = None,
    kol_digest: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Synthesize deterministic fast multi-angle stock/crypto thesis."""
    if str(profile.get("market") or "").lower() == "crypto" or str(profile.get("asset_type") or "").lower() == "crypto":
        return _build_crypto_quick_thesis(profile, risk_gate=risk_gate, signals=signals, kol_digest=kol_digest)

    sym = str(profile.get("symbol") or "").strip().upper()
    cn_name = str(profile.get("name_cn") or sym).strip()
    cur_sym = str(profile.get("currency_symbol") or "$").strip() or "$"
    price = _finite(profile.get("price"))
    ma20 = _finite(profile.get("ma20"))
    ma50 = _finite(profile.get("ma50"))
    bias20 = _finite(profile.get("bias20_pct"))
    bias50 = _finite(profile.get("bias50_pct"))
    pos_52w = _finite(profile.get("range_52w_pos"))

    checklist = profile.get("pre_trade_checklist") or {}
    chase_alert = str(checklist.get("chase_alert") or "normal")
    health = profile.get("health") or {}
    h_score = _finite(health.get("score"))
    h_label = str(health.get("label") or "").strip()

    overview = profile.get("overview") or {}
    financials = profile.get("financials") or {}
    valuation = profile.get("valuation") or {}
    sec_filings = profile.get("sec_filings") or []

    pe_ttm = _finite(overview.get("pe_ttm") if overview.get("pe_ttm") is not None else valuation.get("pe_ttm"))
    pe_fwd = _finite(overview.get("pe_forward") if overview.get("pe_forward") is not None else valuation.get("pe_forward"))
    target_px = _finite(overview.get("target_mean_price"))
    mcap_fmt = str(overview.get("market_cap_fmt") or "").strip()

    rev_g = _parse_pct_value(financials.get("rev_growth_yoy"))
    earn_g = _parse_pct_value(financials.get("earnings_growth_yoy"))
    gm_val = _parse_pct_value(financials.get("gross_margin"))
    fcf_display = str(financials.get("ttm_fcf_fmt") or "")
    fcf_sign = _parse_pct_value(fcf_display.replace("HK$", "").replace("$", "").replace("¥", "").rstrip("KMBT"))
    growth = [(label, val) for label, val in (("营收", rev_g), ("盈利", earn_g)) if val is not None]
    declines = [f"{label}同比 {val:g}%" for label, val in growth if val < 0]

    rg = risk_gate or {}
    gate_light = str(rg.get("light") or checklist.get("risk_gate_light") or "YELLOW").upper()
    gate_cap = _finite(rg.get("long_risk_cap"))
    if gate_cap is None:
        gate_cap = _finite(checklist.get("risk_gate_cap"))
    if gate_cap is None:
        gate_cap = 0.0 if gate_light == "RED" else (0.5 if gate_light == "YELLOW" else 1.0)
    gate_label = str(rg.get("label") or ("红灯防守" if gate_light == "RED" else ("绿灯顺风" if gate_light == "GREEN" else "黄灯控险"))).strip()

    # Match relevant station signals & KOL notes
    bull_sig_titles: list[str] = []
    bear_sig_titles: list[str] = []
    sym_code = sym.split(".")[0]
    sym_re = re.compile(rf"(?<![A-Z0-9]){re.escape(sym_code)}(?![A-Z0-9])") if sym_code else None
    for s in signals or []:
        if not isinstance(s, dict):
            continue
        tickers_up = {str(t).strip().upper() for t in (s.get("tickers") or []) if t}
        ttl = str(s.get("title") or "").strip()
        txt = f"{ttl} {s.get('summary') or ''}"
        matched = (
            (sym in tickers_up)
            or (sym_code in tickers_up)
            or (sym_re is not None and bool(sym_re.search(txt.upper())))
        )
        if not matched and cn_name and len(cn_name) >= 2 and cn_name in txt:
            matched = True
        if not matched:
            continue
        d = str(s.get("direction") or "neutral").lower()
        if d == "bullish" and ttl and ttl not in bull_sig_titles:
            bull_sig_titles.append(ttl)
        elif d == "bearish" and ttl and ttl not in bear_sig_titles:
            bear_sig_titles.append(ttl)

    kol_bull = 0
    kol_bear = 0
    for k in kol_digest or []:
        if not isinstance(k, dict):
            continue
        k_txt = f"{k.get('headline') or ''} {k.get('takeaway') or ''}"
        if (sym_re is not None and sym_re.search(k_txt.upper())) or (cn_name and len(cn_name) >= 2 and cn_name in k_txt):
            st_k = str(k.get("stance") or "")
            if "偏多" in st_k:
                kol_bull += 1
            elif "偏空" in st_k:
                kol_bear += 1

    # Quantitative 0-100 composite score
    raw_score = 52.0
    if h_score is not None:
        raw_score += (h_score - 5.0) * 3.8
    if rev_g is not None:
        raw_score += 8.0 if rev_g >= 20.0 else (4.0 if rev_g >= 8.0 else (-7.0 if rev_g <= -10.0 else 0.0))
    if earn_g is not None:
        raw_score += 6.0 if earn_g >= 20.0 else (3.0 if earn_g >= 5.0 else (-6.0 if earn_g <= -10.0 else 0.0))
    if gm_val is not None and gm_val >= 50.0:
        raw_score += 3.0

    upside_pct: float | None = None
    if target_px is not None and price is not None and price > 0:
        upside_pct = (target_px / price - 1.0) * 100.0
        raw_score += 8.0 if upside_pct >= 15.0 else (4.0 if upside_pct >= 5.0 else (-6.0 if upside_pct <= -5.0 else 0.0))

    if pe_fwd is not None and 0 < pe_fwd <= 25.0:
        raw_score += 4.0
    elif pe_ttm is not None and pe_ttm > 65.0 and (rev_g is None or rev_g < 15.0):
        raw_score -= 5.0

    if chase_alert == "overextended_long" or (bias20 is not None and bias20 > 8.0):
        raw_score -= 6.0
    elif bias20 is not None and 0.0 <= bias20 <= 8.0 and (ma50 is None or (price is not None and price >= ma50)):
        raw_score += 5.0
    elif bias20 is not None and bias20 < -8.0:
        raw_score -= 10.0 if (ma50 is not None and price is not None and price < ma50) else 6.0
    elif ma50 is not None and price is not None and price < ma50:
        raw_score -= 4.0

    net_sig = max(-4, min(4, len(bull_sig_titles) - len(bear_sig_titles)))
    raw_score += net_sig * 2.0
    raw_score += max(-2, min(2, kol_bull - kol_bear)) * 2.0

    if gate_light == "RED":
        raw_score -= 12.0
    elif gate_light == "YELLOW":
        raw_score -= 4.0
    elif gate_light == "GREEN":
        raw_score += 3.0

    score = int(round(max(0.0, min(100.0, raw_score))))
    downtrend_stretched = (bias20 is not None and bias20 < -8.0)

    if chase_alert == "overextended_long" or (bias20 is not None and bias20 > 8.0):
        stance = "追高警示"
    elif gate_light == "RED" and (downtrend_stretched or score < 62 or (h_score is not None and h_score <= 4)):
        stance = "偏空逆风"
    elif score <= 44:
        stance = "偏空逆风"
    elif score >= 65 and gate_light != "RED" and not downtrend_stretched:
        stance = "偏多顺风"
    else:
        stance = "中性观望"

    # Assemble 2-3 factual Chinese bull points
    bull_points: list[str] = []
    if h_score is not None and h_score >= 6.0:
        lbl_part = f"（{h_label}）" if h_label else ""
        bull_points.append(f"财务健康评分 {int(round(h_score))}/10{lbl_part}，资产负债与盈利底盘清晰")
    if not declines:
        growth_points = [f"{label}同比 +{val:g}%" for label, val in growth if val > 0]
        if growth_points:
            bull_points.append("，".join(growth_points) + "，核心基本面具备支撑")
    margin_cash: list[str] = []
    if gm_val is not None and gm_val >= 50:
        margin_cash.append(f"毛利率 {gm_val:g}%")
    if fcf_sign is not None and fcf_sign > 0:
        margin_cash.append(f"近期现金流充沛（{fcf_display}）")
    if margin_cash:
        bull_points.append("，".join(margin_cash))
    if upside_pct is not None and upside_pct > 2.0:
        bull_points.append(f"机构一致目标均价 {_fmt_px(target_px, cur_sym)}，较现价存 {upside_pct:+.1f}% 修复空间")
    if bull_sig_titles:
        bull_points.append(f"站内命中 {len(bull_sig_titles)} 条偏多催化快讯：{bull_sig_titles[0][:44]}")
    if len(bull_points) < 2 and sec_filings:
        bull_points.append(f"已纳入 SEC EDGAR 持续追踪（最新 {len(sec_filings)} 份官方披露文件可一键溯源）")
    if len(bull_points) < 2:
        mcap_txt = f"市值 {mcap_fmt}、" if mcap_fmt and mcap_fmt != "—" else ""
        pos_txt = f"位于 52 周区间 {pos_52w:.1f}% 分位" if pos_52w is not None else "流动性与盘面报价保持活跃"
        px_txt = f"最新价 {_fmt_px(price, cur_sym)}，" if price is not None else ""
        bull_points.append(f"{px_txt}{mcap_txt}{pos_txt}")
    if len(bull_points) < 2:
        bull_points.append(f"{cn_name} 所属赛道关注度维持高位，具备基本面与市场催化双线观察价值")

    # Assemble 2-3 factual Chinese bear / discipline points
    bear_points: list[str] = []
    if declines:
        bear_points.append("，".join(declines) + "，同比表现承压")
    if fcf_sign is not None and fcf_sign < 0:
        bear_points.append(f"近 12 个月自由现金流为负（{fcf_display}），需跟踪现金消耗")
    if gm_val is not None and gm_val < 0:
        bear_points.append(f"毛利率 {gm_val:g}%，营业成本高于营收")
    if h_score is not None and h_score <= 4.0:
        lbl_part = f"（{h_label}）" if h_label else ""
        bear_points.append(f"财务健康评分仅 {int(round(h_score))}/10{lbl_part}，资产负债与现金流承压需防范风险")
    if gate_light in ("RED", "YELLOW"):
        bear_points.append(f"宏观风控「{gate_label}」限制方向性多头上限至 {gate_cap:.1f}x，须严格控仓")
    if bias20 is not None:
        b50_txt = f"（MA50 乖离 {bias50:+.1f}%）" if bias50 is not None else ""
        if bias20 > 8.0:
            bear_points.append(f"20 日均线正乖离达 {bias20:+.1f}%{b50_txt}，已越过 +8% 追高警示线，短线谨防回吐")
        elif bias20 < -8.0:
            bear_points.append(f"现价低于 20 日均线 {abs(bias20):.1f}%{b50_txt}，均线空头压制明显，忌盲目左侧接飞刀")
        else:
            bear_points.append(f"MA20 乖离 {bias20:+.1f}%{b50_txt}，短线仍需防范关键均线得失引发的波动")
    if pe_ttm is not None:
        fwd_part = f"，预期市盈率 {pe_fwd:.1f}x" if pe_fwd is not None else ""
        bear_points.append(f"滚动市盈率 {pe_ttm:.1f}x{fwd_part}，估值消化高度依赖后续业绩兑现")
    if bear_sig_titles:
        bear_points.append(f"近期录得 {len(bear_sig_titles)} 条偏空快讯提醒：{bear_sig_titles[0][:44]}")
    if kol_bear > kol_bull:
        bear_points.append(f"同期追踪 KOL 观点偏空占优（偏空 {kol_bear} / 偏多 {kol_bull}），注意舆论分歧压制")
    if len(bear_points) < 2:
        bear_points.append("跨资产波动与财报窗口容易放大跳空幅度，单笔仓位须预设明确失效止损点")
    if len(bear_points) < 2:
        bear_points.append("结合开仓纪律检查卡实时比对均线斜率，未确认放量企稳前避免情绪化加仓")

    if stance == "追高警示":
        b20_val = bias20 if bias20 is not None else 0.0
        action_hint = f"短线正乖离 {b20_val:+.1f}% 已超过 +8% 防追线，严禁现价追多，等待回踩 MA20 附近企稳后再评估。"
    elif stance == "偏多顺风":
        if gate_light == "GREEN":
            action_hint = "基本面与趋势信号同向偏强，可沿 20 日均线分批布局并严格设定破位止损。"
        else:
            action_hint = f"基本面支撑较强但受当前门控多头上限（{gate_cap:.1f}x）约束，宜控制单笔比例逢回踩分批执行。"
    elif stance == "偏空逆风":
        action_hint = "技术趋势与风险门控双向偏紧，多单以减仓观望或对冲保护为主，切忌在均线下方逆势重仓抄底。"
    elif downtrend_stretched:
        b20_val = bias20 if bias20 is not None else 0.0
        action_hint = f"基本面虽有支撑但现价偏离 20 日均线 {b20_val:+.1f}%（上限 {gate_cap:.1f}x），忌盲目抄底，等待均线企稳再介入。"
    else:
        action_hint = f"多空线索尚处拉锯阶段且宏观多头上限 {gate_cap:.1f}x，建议轻仓观察等待均线与财报催化明确。"

    return {
        "stance": stance,
        "score": score,
        "bull_points": [_scrub(x) for x in bull_points[:3]],
        "bear_points": [_scrub(x) for x in bear_points[:3]],
        "action_hint": _scrub(action_hint),
    }


def _assemble_profile(
    symbol: str,
    yahoo: dict[str, Any],
    gtimg: dict[str, Any],
    sec_filings: list[dict[str, str]] | None = None,
    risk_gate: dict[str, Any] | None = None,
    *,
    market: str | None = None,
    currency: str | None = None,
    currency_symbol: str | None = None,
) -> dict[str, Any] | None:
    meta_map = MULTI_MARKET_META_MAP.get(symbol)
    meta_legacy = STOCK_META_MAP.get(symbol)

    resolved_market = market or (meta_map["market"] if meta_map else "us")
    resolved_currency = currency or (
        meta_map["currency"]
        if meta_map
        else {"cn": "CNY", "hk": "HKD"}.get(resolved_market, "USD")
    )
    resolved_cur_sym = currency_symbol or (
        meta_map["currency_symbol"]
        if meta_map
        else {"cn": "¥", "hk": "HK$"}.get(resolved_market, "$")
    )

    cn_name = (
        meta_map["name_cn"]
        if meta_map
        else (
            meta_legacy[1]
            if meta_legacy
            else _scrub(gtimg.get("name_cn") or yahoo.get("name_en") or gtimg.get("name_en") or symbol)
        )
    )
    default_exch = {"cn": "A股", "hk": "港交所", "crypto": "现货 · Binance/Gate"}.get(resolved_market, "美股")
    default_sec = {"cn": "A股个股", "hk": "港股个股", "crypto": "数字资产 · 加密现货"}.get(resolved_market, "美股个股")
    default_peer = {"cn": "A股自选检索", "hk": "港股自选检索", "crypto": "加密核心资产"}.get(resolved_market, "自选检索")

    exch = (
        meta_map["exchange"]
        if meta_map
        else _scrub(gtimg.get("exchange") or yahoo.get("exchange") or default_exch)
    )
    sector_cn = (
        meta_map["sector_cn"]
        if meta_map
        else _scrub(yahoo.get("sector_en") or default_sec)
    )
    peer_group = meta_map["peer_group"] if meta_map else default_peer
    overview_cn = (
        meta_map["overview_cn"]
        if meta_map
        else _scrub(
            f"{cn_name}（{symbol}），{exch}挂牌交易标的，"
            + (f"所属行业 {yahoo['industry_en']}。" if yahoo.get("industry_en") else f"归属 {sector_cn}。")
        )
    )

    price = _finite(gtimg.get("price")) or _finite(yahoo.get("price"))
    if price is None or price <= 0:
        return None
    change_pct = _finite(gtimg.get("change_pct"))
    if change_pct is None:
        change_pct = _finite(yahoo.get("change_pct")) or 0.0
    change_amt = _finite(gtimg.get("change_amt"))
    if change_amt is None:
        change_amt = _finite(yahoo.get("change_amt")) or 0.0

    mcap_raw = _finite(gtimg.get("mcap_raw")) or _finite(yahoo.get("market_cap_raw"))
    pe_ttm = _finite(gtimg.get("pe_ttm")) or _finite(yahoo.get("pe_ttm"))
    pe_fwd = _finite(yahoo.get("pe_forward"))
    eps_ttm = _finite(gtimg.get("eps_ttm")) or _finite(yahoo.get("eps_ttm"))
    pb = _finite(gtimg.get("pb")) or _finite(yahoo.get("pb"))
    div_pct = yahoo.get("dividend_yield_pct")
    if div_pct is None:
        div_pct = gtimg.get("dividend_yield_pct")
    if _finite(div_pct) is not None and not (0 <= float(div_pct) < 25.0):
        div_pct = None
    div_fmt = f"{div_pct:.2f}%" if _finite(div_pct) is not None else "—"

    chart = yahoo.get("chart") or {}
    closes = [float(c) for c in (chart.get("closes") or []) if _finite(c) is not None]
    dates = list(chart.get("dates") or [])[: len(closes)]

    low_52w = _finite(yahoo.get("low_52w")) or _finite(gtimg.get("low_52w")) or _finite(chart.get("low_52w"))
    high_52w = _finite(yahoo.get("high_52w")) or _finite(gtimg.get("high_52w")) or _finite(chart.get("high_52w"))

    vol = _finite(gtimg.get("volume")) or _finite(yahoo.get("volume_raw"))
    vol_fmt = f"{vol / 1e6:.2f}M" if (vol is not None and vol >= 1e5) else (_fmt_num(vol, 0) if vol is not None else "—")
    turnover_fmt = _fmt_money_compact(gtimg.get("turnover"), currency=resolved_cur_sym)

    px_prec = 4 if abs(price) < 1 else (3 if resolved_market == "hk" and price < 10 else 2)
    rounded_price = round(price, px_prec)

    checklist = _build_pre_trade_checklist(
        price=rounded_price,
        closes=closes,
        low_52w=low_52w,
        high_52w=high_52w,
        risk_gate=risk_gate,
        currency_symbol=resolved_cur_sym,
    )
    now_bjt = datetime.now(BJT).strftime("%Y-%m-%d %H:%M:%S+08:00")
    as_of_str = _scrub(gtimg.get("as_of") or datetime.now(BJT).strftime("%Y-%m-%d %H:%M"))
    market_at = str(gtimg.get("market_at") or as_of_str)
    received_at = str(gtimg.get("received_at") or now_bjt)
    quote_source = str(gtimg.get("source") or ("yahoo_finance" if yahoo else "local_cache"))
    session = str(gtimg.get("session") or "closed")
    fin_cur_sym = str(yahoo.get("report_currency") or resolved_cur_sym)

    assembled: dict[str, Any] = {
        "symbol": symbol,
        "market": resolved_market,
        "asset_type": "crypto" if resolved_market == "crypto" else "equity",
        "currency": resolved_currency,
        "currency_symbol": resolved_cur_sym,
        "name_cn": _scrub(cn_name),
        "name_en": _scrub(gtimg.get("name_en") or yahoo.get("name_en") or cn_name or symbol),
        "exchange": _scrub(exch),
        "sector_cn": _scrub(sector_cn),
        "peer_group": _scrub(peer_group),
        "business_overview_cn": _scrub(overview_cn),
        "price": rounded_price,
        "change_amt": round(change_amt, px_prec),
        "change_pct": round(change_pct, 2),
        "as_of": as_of_str,
        "market_at": market_at,
        "received_at": received_at,
        "source": quote_source,
        "session": session,
        "earnings_date": str(yahoo.get("earnings_date") or "").strip(),
        "ma20": checklist["ma20"],
        "ma50": checklist["ma50"],
        "bias20_pct": checklist["bias20_pct"],
        "bias50_pct": checklist["bias50_pct"],
        "range_52w_pos": checklist["range_52w_pos"],
        "pre_trade_checklist": checklist,
        "chart": {
            "dates": dates,
            "closes": closes,
            "return_1w": _compute_return(closes, 5),
            "return_1m": _compute_return(closes, 21),
            "return_3m": _compute_return(closes, 63),
            "return_6m": _compute_return(closes, 126),
            "return_1y": _compute_return(closes, 250),
        },
        "health": _build_health(yahoo, gtimg),
        "overview": {
            "market_cap_raw": mcap_raw,
            "market_cap_fmt": _fmt_money_compact(mcap_raw, currency=resolved_cur_sym),
            "pe_ttm": round(pe_ttm, 2) if pe_ttm is not None else None,
            "pe_forward": round(pe_fwd, 2) if pe_fwd is not None else None,
            "eps_ttm": round(eps_ttm, 2) if eps_ttm is not None else None,
            "dividend_yield_pct": round(div_pct, 2) if _finite(div_pct) is not None else None,
            "dividend_yield_fmt": div_fmt,
            "volume_fmt": vol_fmt,
            "turnover_fmt": turnover_fmt,
            "low_52w": round(low_52w, px_prec) if low_52w is not None else None,
            "high_52w": round(high_52w, px_prec) if high_52w is not None else None,
            "beta": round(yahoo["beta"], 2) if _finite(yahoo.get("beta")) is not None else None,
            "short_ratio": round(yahoo["short_ratio"], 2) if _finite(yahoo.get("short_ratio")) is not None else None,
            "insider_pct": _fmt_pct_from_ratio(yahoo.get("insider_ratio")),
            "institution_pct": _fmt_pct_from_ratio(yahoo.get("institution_ratio")),
            "target_mean_price": round(yahoo["target_mean_price"], 2)
            if _finite(yahoo.get("target_mean_price")) is not None
            else None,
            "analyst_rating": _scrub(yahoo.get("analyst_rating") or "—"),
            "analyst_count": yahoo.get("analyst_count"),
        },
        "financials": {
            "rev_growth_yoy": _fmt_pct_from_ratio(yahoo.get("rev_growth"), signed=True),
            "earnings_growth_yoy": _fmt_pct_from_ratio(yahoo.get("earnings_growth"), signed=True),
            "gross_margin": _fmt_pct_from_ratio(yahoo.get("gross_margin")),
            "operating_margin": _fmt_pct_from_ratio(yahoo.get("operating_margin")),
            "net_margin": _fmt_pct_from_ratio(yahoo.get("net_margin")),
            "roe": _fmt_pct_from_ratio(yahoo.get("roe")),
            "roa": _fmt_pct_from_ratio(yahoo.get("roa")),
            "current_ratio": _fmt_num(yahoo.get("current_ratio")),
            "debt_to_equity": _fmt_num(yahoo.get("debt_to_equity")),
            "total_cash_fmt": _fmt_money_compact(yahoo.get("total_cash"), currency=fin_cur_sym),
            "total_debt_fmt": _fmt_money_compact(yahoo.get("total_debt"), currency=fin_cur_sym),
            "ttm_revenue_fmt": _fmt_money_compact(yahoo.get("ttm_revenue"), currency=fin_cur_sym),
            "ttm_ebitda_fmt": _fmt_money_compact(yahoo.get("ttm_ebitda"), currency=fin_cur_sym),
            "ttm_ocf_fmt": _fmt_money_compact(yahoo.get("ttm_ocf"), currency=fin_cur_sym),
            "ttm_fcf_fmt": _fmt_money_compact(yahoo.get("ttm_fcf"), currency=fin_cur_sym),
            "quarters": list(yahoo.get("quarters") or []),
        },
        "valuation": {
            "pe_ttm": round(pe_ttm, 2) if pe_ttm is not None else None,
            "pe_forward": round(pe_fwd, 2) if pe_fwd is not None else None,
            "ps_ttm": round(yahoo["ps_ttm"], 2) if _finite(yahoo.get("ps_ttm")) is not None else None,
            "pb": round(pb, 2) if pb is not None else None,
            "ev_ebitda": round(yahoo["ev_ebitda"], 2) if _finite(yahoo.get("ev_ebitda")) is not None else None,
            "peg": round(yahoo["peg"], 2) if _finite(yahoo.get("peg")) is not None else None,
        },
        "sec_filings": [dict(x) for x in (sec_filings or [])],
    }
    assembled["quick_thesis"] = build_quick_thesis(assembled, risk_gate=risk_gate)
    return assembled


def _fetch_crypto_profile(
    base_symbol: str,
    *,
    risk_gate: dict[str, Any] | None = None,
    timeout: int = 4,
) -> dict[str, Any] | None:
    """Build full spot research profile for any crypto asset using Binance Vision + Gate.io fallback."""
    if requests is None:
        return None
    base = re.sub(r"[^A-Z0-9]", "", base_symbol.strip().upper())
    if not base or len(base) > 12:
        return None

    ticker_price: float | None = None
    change_pct: float | None = None
    change_amt: float | None = None
    quote_vol_24h: float | None = None
    base_vol_24h: float | None = None
    high_24h: float | None = None
    low_24h: float | None = None
    src_name = ""

    dates: list[str] = []
    closes: list[float] = []
    highs: list[float] = []
    lows: list[float] = []
    quote_vols: list[float] = []

    # 1. Primary: Binance data-api.binance.vision (CN-VPS reachable)
    bn_pair = f"{base}USDT"
    try:
        rt = requests.get(
            "https://data-api.binance.vision/api/v3/ticker/24hr",
            params={"symbol": bn_pair},
            headers=_HEADERS,
            timeout=timeout,
        )
        if rt.status_code == 200:
            tk = rt.json() or {}
            px = _finite(tk.get("lastPrice"))
            if px is not None and px > 0:
                ticker_price = px
                change_pct = _finite(tk.get("priceChangePercent"))
                change_amt = _finite(tk.get("priceChange"))
                quote_vol_24h = _finite(tk.get("quoteVolume"))
                base_vol_24h = _finite(tk.get("volume"))
                high_24h = _finite(tk.get("highPrice"))
                low_24h = _finite(tk.get("lowPrice"))
                src_name = "binance_spot"
    except Exception:
        pass

    try:
        rk = requests.get(
            "https://data-api.binance.vision/api/v3/klines",
            params={"symbol": bn_pair, "interval": "1d", "limit": "365"},
            headers=_HEADERS,
            timeout=timeout,
        )
        if rk.status_code == 200:
            rows = rk.json() or []
            for row in rows:
                if not isinstance(row, list) or len(row) < 8:
                    continue
                ts_ms = _finite(row[0])
                cl = _finite(row[4])
                if ts_ms is None or cl is None or cl <= 0:
                    continue
                dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=UTC).strftime("%Y-%m-%d")
                hi = _finite(row[2]) or cl
                lo = _finite(row[3]) or cl
                qv = _finite(row[7]) or 0.0
                dates.append(dt)
                closes.append(round(cl, 6 if cl < 0.05 else (4 if cl < 1 else 2)))
                highs.append(hi)
                lows.append(lo)
                quote_vols.append(qv)
    except Exception:
        pass

    # 2. Fallback: Gate.io public spot API (also verified CN-VPS reachable)
    if ticker_price is None or not closes:
        gate_pair = f"{base}_USDT"
        if ticker_price is None:
            try:
                rgt = requests.get(
                    "https://api.gateio.ws/api/v4/spot/tickers",
                    params={"currency_pair": gate_pair},
                    headers=_HEADERS,
                    timeout=timeout,
                )
                if rgt.status_code == 200:
                    arr = rgt.json() or []
                    if arr and isinstance(arr[0], dict):
                        tk = arr[0]
                        px = _finite(tk.get("last"))
                        if px is not None and px > 0:
                            ticker_price = px
                            change_pct = _finite(tk.get("change_percentage"))
                            quote_vol_24h = _finite(tk.get("quote_volume"))
                            base_vol_24h = _finite(tk.get("base_volume"))
                            high_24h = _finite(tk.get("high_24h"))
                            low_24h = _finite(tk.get("low_24h"))
                            src_name = "gateio_spot"
            except Exception:
                pass
        if not closes:
            try:
                rgk = requests.get(
                    "https://api.gateio.ws/api/v4/spot/candlesticks",
                    params={"currency_pair": gate_pair, "interval": "1d", "limit": "365"},
                    headers=_HEADERS,
                    timeout=timeout,
                )
                if rgk.status_code == 200:
                    for row in rgk.json() or []:
                        if not isinstance(row, list) or len(row) < 6:
                            continue
                        ts_s = _finite(row[0])
                        cl = _finite(row[2])
                        if ts_s is None or cl is None or cl <= 0:
                            continue
                        dt = datetime.fromtimestamp(ts_s, tz=UTC).strftime("%Y-%m-%d")
                        hi = _finite(row[3]) or cl
                        lo = _finite(row[4]) or cl
                        qv = _finite(row[1]) or 0.0
                        dates.append(dt)
                        closes.append(round(cl, 6 if cl < 0.05 else (4 if cl < 1 else 2)))
                        highs.append(hi)
                        lows.append(lo)
                        quote_vols.append(qv)
            except Exception:
                pass

    if ticker_price is None and closes:
        ticker_price = closes[-1]
    if ticker_price is None or ticker_price <= 0:
        return None
    if change_amt is None and change_pct is not None and abs(change_pct) > 1e-9:
        prev_px = ticker_price / (1.0 + change_pct / 100.0)
        change_amt = ticker_price - prev_px

    low_52w = min(lows) if lows else low_24h
    high_52w = max(highs) if highs else high_24h
    px_prec = 6 if abs(ticker_price) < 0.05 else (4 if abs(ticker_price) < 1 else 2)
    rounded_px = round(ticker_price, px_prec)

    # Quantitative crypto risk/trend metrics
    vol_30d_ann_pct: float | None = None
    if len(closes) >= 31:
        rets: list[float] = []
        sub = closes[-31:]
        for a, b in zip(sub[:-1], sub[1:]):
            if a > 0 and b > 0:
                rets.append(math.log(b / a))
        if len(rets) >= 15:
            mean_r = sum(rets) / len(rets)
            var_r = sum((x - mean_r) ** 2 for x in rets) / max(1, len(rets) - 1)
            vol_30d_ann_pct = round(math.sqrt(var_r) * math.sqrt(365.0) * 100.0, 1)

    max_dd_90d_pct: float | None = None
    if len(closes) >= 10:
        sub90 = closes[-90:]
        peak = sub90[0]
        worst = 0.0
        for c in sub90:
            if c > peak:
                peak = c
            if peak > 0:
                dd = (c - peak) / peak * 100.0
                if dd < worst:
                    worst = dd
        max_dd_90d_pct = round(worst, 1)

    vol_ratio_30d: float | None = None
    if quote_vol_24h is not None and len(quote_vols) >= 10:
        recent_vols = [v for v in quote_vols[-31:-1] if v > 0]
        if recent_vols:
            avg_v = sum(recent_vols) / len(recent_vols)
            if avg_v > 0:
                vol_ratio_30d = round(quote_vol_24h / avg_v, 2)

    ma200 = round(sum(closes[-200:]) / 200.0, px_prec) if len(closes) >= 200 else None

    resolved_rg = risk_gate or _load_default_risk_gate()
    checklist = _build_pre_trade_checklist(
        price=rounded_px,
        closes=closes,
        low_52w=low_52w,
        high_52w=high_52w,
        risk_gate=resolved_rg,
        currency_symbol="$",
    )

    ma20 = checklist["ma20"]
    ma50 = checklist["ma50"]
    above_ma50 = ma50 is not None and rounded_px >= ma50
    ma_bull = ma20 is not None and ma50 is not None and ma20 >= ma50
    dd_ok = max_dd_90d_pct is not None and abs(max_dd_90d_pct) < 30.0
    liq_ok = quote_vol_24h is not None and quote_vol_24h >= 5e7

    c_score = 2
    if above_ma50:
        c_score += 2
    if ma_bull:
        c_score += 2
    if dd_ok:
        c_score += 2
    if liq_ok:
        c_score += 2
    c_score = max(1, min(10, c_score))

    meta_entry = MULTI_MARKET_META_MAP.get(base)
    cn_name = meta_entry["name_cn"] if meta_entry else f"{base} 现货"
    sector_cn = meta_entry["sector_cn"] if meta_entry else "数字资产 · 加密现货"
    overview_cn = (
        meta_entry["overview_cn"]
        if meta_entry
        else f"{base}/USDT 全天候现货交易标的，采用 365 日日线波动与量能结构进行趋势评估。"
    )
    now_bjt = datetime.now(BJT).strftime("%Y-%m-%d %H:%M:%S+08:00")
    vol_fmt = (
        f"{base_vol_24h / 1e6:.2f}M"
        if (base_vol_24h is not None and base_vol_24h >= 1e5)
        else (_fmt_num(base_vol_24h, 2) if base_vol_24h is not None else "—")
    )
    turnover_24h_fmt = _fmt_money_compact(quote_vol_24h, currency="$")

    prof: dict[str, Any] = {
        "symbol": base,
        "market": "crypto",
        "asset_type": "crypto",
        "currency": "USD",
        "currency_symbol": "$",
        "name_cn": cn_name,
        "name_en": f"{base}/USDT",
        "exchange": "现货 · Binance/Gate",
        "sector_cn": sector_cn,
        "peer_group": "加密核心资产",
        "business_overview_cn": overview_cn,
        "price": rounded_px,
        "change_amt": round(change_amt or 0.0, px_prec),
        "change_pct": round(change_pct or 0.0, 2),
        "as_of": now_bjt[:16],
        "market_at": now_bjt,
        "received_at": now_bjt,
        "source": src_name or "binance_spot",
        "session": "24x7",
        "earnings_date": "",
        "ma20": ma20,
        "ma50": ma50,
        "bias20_pct": checklist["bias20_pct"],
        "bias50_pct": checklist["bias50_pct"],
        "range_52w_pos": checklist["range_52w_pos"],
        "pre_trade_checklist": checklist,
        "chart": {
            "dates": dates,
            "closes": closes,
            "return_1w": _compute_return(closes, 7),
            "return_1m": _compute_return(closes, 30),
            "return_3m": _compute_return(closes, 90),
            "return_6m": _compute_return(closes, 180),
            "return_1y": _compute_return(closes, min(364, max(1, len(closes) - 1))),
        },
        "health": {
            "score": c_score,
            "checks": [
                {"label": "站上 50 日均线", "pass": bool(above_ma50), "detail": _fmt_px(ma50, "$")},
                {"label": "MA20 高于 MA50", "pass": bool(ma_bull), "detail": f"{checklist['bias50_pct']:+.1f}%" if checklist["bias50_pct"] is not None else "—"},
                {"label": "90日回撤 < 30%", "pass": bool(dd_ok), "detail": f"{max_dd_90d_pct:.1f}%" if max_dd_90d_pct is not None else "—"},
                {"label": "24h成交 > $50M", "pass": bool(liq_ok), "detail": turnover_24h_fmt},
            ],
        },
        "crypto_metrics": {
            "turnover_24h_fmt": turnover_24h_fmt,
            "base_vol_24h_fmt": f"{vol_fmt} {base}" if vol_fmt != "—" else "—",
            "high_24h": round(high_24h, px_prec) if high_24h is not None else None,
            "low_24h": round(low_24h, px_prec) if low_24h is not None else None,
            "vol_30d_ann_pct": vol_30d_ann_pct,
            "max_drawdown_90d_pct": max_dd_90d_pct,
            "vol_ratio_30d": vol_ratio_30d,
            "ma200": ma200,
        },
        "overview": {
            "market_cap_raw": None,
            "market_cap_fmt": "—",
            "pe_ttm": None,
            "pe_forward": None,
            "eps_ttm": None,
            "dividend_yield_pct": None,
            "dividend_yield_fmt": "—",
            "volume_fmt": vol_fmt,
            "turnover_fmt": turnover_24h_fmt,
            "low_52w": round(low_52w, px_prec) if low_52w is not None else None,
            "high_52w": round(high_52w, px_prec) if high_52w is not None else None,
            "beta": None,
            "short_ratio": None,
            "insider_pct": "—",
            "institution_pct": "—",
            "target_mean_price": None,
            "analyst_rating": "—",
            "analyst_count": None,
        },
        "financials": {
            "rev_growth_yoy": "—",
            "earnings_growth_yoy": "—",
            "gross_margin": "—",
            "operating_margin": "—",
            "net_margin": "—",
            "roe": "—",
            "roa": "—",
            "current_ratio": "—",
            "debt_to_equity": "—",
            "total_cash_fmt": "—",
            "total_debt_fmt": "—",
            "ttm_revenue_fmt": "—",
            "ttm_ebitda_fmt": "—",
            "ttm_ocf_fmt": "—",
            "ttm_fcf_fmt": "—",
            "quarters": [],
        },
        "valuation": {
            "pe_ttm": None,
            "pe_forward": None,
            "ps_ttm": None,
            "pb": None,
            "ev_ebitda": None,
            "peg": None,
        },
        "sec_filings": [],
    }
    prof["quick_thesis"] = _build_crypto_quick_thesis(prof, risk_gate=resolved_rg)
    return prof


def _fetch_cn_equity_profile(
    symbol: str,
    *,
    risk_gate: dict[str, Any] | None = None,
    timeout: int = 4,
) -> dict[str, Any] | None:
    quote = _fetch_cn_quote(symbol, timeout=timeout)
    if not quote:
        return None
    q_code = str(quote.get("q_code") or "")
    chart: dict[str, Any] = {}
    f10: dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_k = pool.submit(fetch_ifzq_kline, q_code, is_us=False, limit=252, timeout=timeout)
        f_f = pool.submit(_fetch_cn_f10, symbol, timeout)
        try:
            chart = f_k.result() or {}
        except Exception:
            chart = {}
        try:
            f10 = f_f.result() or {}
        except Exception:
            f10 = {}
    f10["chart"] = chart
    m_code = re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", symbol)
    exch_cn = {"SH": "上交所", "SZ": "深交所", "BJ": "北交所"}.get(m_code.group(1) if m_code else "", "A股")
    quote.setdefault("exchange", exch_cn)
    return _assemble_profile(
        symbol,
        f10,
        quote,
        sec_filings=[],
        risk_gate=risk_gate or _load_default_risk_gate(),
        market="cn",
        currency="CNY",
        currency_symbol="¥",
    )


def _fetch_hk_equity_profile(
    symbol: str,
    *,
    risk_gate: dict[str, Any] | None = None,
    timeout: int = 4,
) -> dict[str, Any] | None:
    quote = _fetch_hk_quote(symbol, timeout=timeout)
    if not quote:
        return None
    q_code = str(quote.get("q_code") or "")
    chart: dict[str, Any] = {}
    f10: dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_k = pool.submit(fetch_ifzq_kline, q_code, is_us=False, limit=252, timeout=timeout)
        f_f = pool.submit(_fetch_hk_f10, symbol, timeout)
        try:
            chart = f_k.result() or {}
        except Exception:
            chart = {}
        try:
            f10 = f_f.result() or {}
        except Exception:
            f10 = {}
    f10["chart"] = chart
    quote.setdefault("exchange", "港交所")
    return _assemble_profile(
        symbol,
        f10,
        quote,
        sec_filings=[],
        risk_gate=risk_gate or _load_default_risk_gate(),
        market="hk",
        currency="HKD",
        currency_symbol="HK$",
    )


def _merge_us_fallbacks(
    sym: str,
    yahoo: dict[str, Any],
    gtimg: dict[str, Any],
    *,
    timeout: int = 4,
) -> dict[str, Any]:
    """Backfill US chart & F10 fundamentals via CN-direct ifzq / EastMoney when Yahoo cache lacks them."""
    merged = dict(yahoo or {})
    has_chart = bool((merged.get("chart") or {}).get("closes"))
    has_fund = any(
        merged.get(k) is not None
        for k in ("rev_growth", "gross_margin", "roe", "ttm_revenue")
    )
    if has_chart and has_fund:
        return merged
    kline_sym = str(gtimg.get("gtimg_kline_sym") or "").strip()
    futures: dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        if not has_chart and kline_sym:
            futures["chart"] = pool.submit(fetch_ifzq_kline, f"us{kline_sym}", is_us=True, limit=252, timeout=timeout)
        if not has_fund:
            futures["f10"] = pool.submit(_fetch_us_f10, sym, timeout)
        for key, fut in futures.items():
            try:
                res = fut.result() or {}
            except Exception:
                res = {}
            if key == "chart" and res.get("closes"):
                merged["chart"] = res
            elif key == "f10" and res:
                for fk, fv in res.items():
                    if fk == "quarters":
                        if not merged.get("quarters") and fv:
                            merged["quarters"] = fv
                    elif merged.get(fk) is None and fv is not None:
                        merged[fk] = fv
    return merged


def build_non_us_preset_profiles(
    *,
    risk_gate: dict[str, Any] | None = None,
    timeout: int = 5,
) -> dict[str, dict[str, Any]]:
    """Build live research profiles for curated A-share, HK, and Crypto preset assets."""
    resolved_rg = risk_gate or _load_default_risk_gate()
    profiles: dict[str, dict[str, Any]] = {}

    def _run_one(row: tuple[str, ...]) -> tuple[str, dict[str, Any] | None]:
        sym, market = row[0], row[6]
        try:
            if market == "cn":
                return sym, _fetch_cn_equity_profile(sym, risk_gate=resolved_rg, timeout=timeout)
            if market == "hk":
                return sym, _fetch_hk_equity_profile(sym, risk_gate=resolved_rg, timeout=timeout)
            if market == "crypto":
                return sym, _fetch_crypto_profile(sym, risk_gate=resolved_rg, timeout=timeout)
        except Exception:
            return sym, None
        return sym, None

    with ThreadPoolExecutor(max_workers=6) as pool:
        for sym, prof in pool.map(_run_one, CORE_MULTI_MARKET_ASSETS):
            if prof is not None:
                profiles[sym] = prof
    return profiles


def _read_cache() -> dict[str, dict[str, Any]]:
    if not CACHE_PATH.exists():
        return {}
    try:
        raw = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def build_stock_profiles(
    force_refresh: bool = False,
    *,
    risk_gate: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Build and cache fundamental & chart profiles for core US, A-share, HK, and Crypto assets."""
    symbols = [row[0] for row in CORE_RESEARCH_STOCKS]
    gtimg_map = fetch_gtimg_batch(symbols)
    yahoo_cache = _read_cache()
    now = time.time()

    need_yahoo = [
        sym
        for sym in symbols
        if force_refresh
        or sym not in yahoo_cache
        or (now - float(yahoo_cache[sym].get("yahoo_fetched_at") or 0.0)) > CACHE_TTL_SECONDS
        or not (yahoo_cache[sym].get("chart") or {}).get("closes")
    ]

    if need_yahoo:
        sess, crumb = _create_yahoo_session()
        if sess is not None:
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = {pool.submit(_fetch_yahoo_one, sym, sess, crumb): sym for sym in need_yahoo}
                for fut in as_completed(futures):
                    sym = futures[fut]
                    try:
                        res = fut.result()
                        if res:
                            yahoo_cache[sym] = res
                    except Exception:
                        pass
            try:
                atomic_write_json(CACHE_PATH, yahoo_cache)
            except Exception:
                pass

    try:
        filings_map = fetch_sec_filings_map(symbols, force_refresh=force_refresh)
    except Exception:
        filings_map = {}

    resolved_rg = risk_gate or _load_default_risk_gate()
    profiles: dict[str, dict[str, Any]] = {}
    for sym in symbols:
        yahoo_item = yahoo_cache.get(sym) or {}
        gtimg_item = gtimg_map.get(sym) or {}
        if not (yahoo_item.get("chart") or {}).get("closes"):
            yahoo_item = _merge_us_fallbacks(sym, yahoo_item, gtimg_item, timeout=4)
        prof = _assemble_profile(
            sym,
            yahoo_item,
            gtimg_item,
            sec_filings=filings_map.get(sym, []),
            risk_gate=resolved_rg,
            market="us",
            currency="USD",
            currency_symbol="$",
        )
        if prof is not None:
            profiles[sym] = prof

    for sym, prof in build_non_us_preset_profiles(risk_gate=resolved_rg, timeout=5).items():
        profiles[sym] = prof
    return profiles


def fetch_single_stock_profile(
    symbol: str,
    *,
    risk_gate: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Live multi-market lookup (US / A-share / HK / Crypto) by code or Chinese name."""
    resolved = resolve_research_symbol(symbol, timeout=3)
    if resolved is None:
        return None
    canon_sym, market = resolved
    now = time.time()

    with _ON_DEMAND_LOCK:
        hit = _ON_DEMAND_CACHE.get(canon_sym)
        if hit and (now - hit[0]) < ON_DEMAND_TTL_SECONDS:
            return dict(hit[1])

    resolved_rg = risk_gate or _load_default_risk_gate()
    prof: dict[str, Any] | None = None

    if market == "cn":
        prof = _fetch_cn_equity_profile(canon_sym, risk_gate=resolved_rg, timeout=4)
    elif market == "hk":
        prof = _fetch_hk_equity_profile(canon_sym, risk_gate=resolved_rg, timeout=4)
    elif market == "crypto":
        prof = _fetch_crypto_profile(canon_sym, risk_gate=resolved_rg, timeout=4)
    else:
        gtimg = fetch_gtimg_batch([canon_sym], timeout=4).get(canon_sym) or {}
        if gtimg:
            cache = _read_cache()
            yahoo = cache.get(canon_sym) or {}
            if not yahoo or (now - float(yahoo.get("yahoo_fetched_at") or 0.0)) > CACHE_TTL_SECONDS:
                sess, crumb = _create_yahoo_session(timeout=2)
                fresh = _fetch_yahoo_one(canon_sym, sess, crumb, timeout=3) if sess is not None else None
                if fresh:
                    yahoo = fresh
                    cache[canon_sym] = fresh
                    try:
                        atomic_write_json(CACHE_PATH, cache)
                    except Exception:
                        pass
            yahoo = _merge_us_fallbacks(canon_sym, yahoo, gtimg, timeout=4)
            sec_filings: list[dict[str, str]] = []
            if canon_sym in SEC_CIK_MAP:
                try:
                    sec_filings = fetch_sec_filings_map([canon_sym], force_refresh=False, timeout=2).get(canon_sym, [])
                except Exception:
                    sec_filings = []
            prof = _assemble_profile(
                canon_sym,
                yahoo,
                gtimg,
                sec_filings=sec_filings,
                risk_gate=resolved_rg,
                market="us",
                currency="USD",
                currency_symbol="$",
            )
        elif "." not in canon_sym and re.fullmatch(r"[A-Z0-9]{2,10}", canon_sym):
            # Fallback: arbitrary unlisted crypto spot token (e.g. WIF, TRUMP, SATS)
            prof = _fetch_crypto_profile(canon_sym, risk_gate=resolved_rg, timeout=4)

    if prof is not None:
        with _ON_DEMAND_LOCK:
            if len(_ON_DEMAND_CACHE) > 256:
                oldest_key = min(_ON_DEMAND_CACHE, key=lambda k: _ON_DEMAND_CACHE[k][0])
                _ON_DEMAND_CACHE.pop(oldest_key, None)
            _ON_DEMAND_CACHE[canon_sym] = (now, dict(prof))
    return prof


if __name__ == "__main__":
    profs = build_stock_profiles()
    print(f"Built {len(profs)} research profiles; AAPL PE={profs.get('AAPL', {}).get('overview', {}).get('pe_ttm')}")
