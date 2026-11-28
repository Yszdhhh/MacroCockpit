"""Asset-class source navigation matrix & US-CN supply chain bridge builder."""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

_BACKEND_DIR = Path(__file__).resolve().parent
if str(_BACKEND_DIR.parent) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR.parent))

from backend.guardrails import scrub_forbidden
from backend.runtime import BJT, DATA_DIR, read_json_safe as _read_json_safe
from backend.kol_intel_engine import (
    MEDIA_WIRE_HANDLES,
    MIN_VERIFIED_SAMPLE,
    sanitize_output,
)

DEFAULT_CHAIN_MAP_PATH = DATA_DIR / "industry_chain_map.json"
DEFAULT_SCORECARD_CACHE_PATH = DATA_DIR / "kol_scorecard_cache.json"

_VALID_BADGES_VERIFIED = frozenset({"高胜率", "稳健参考", "观点敏锐"})


def _default_kol_scorecards(cache_path: Path | str | None = None) -> list[dict[str, Any]]:
    path = Path(cache_path) if cache_path is not None else DEFAULT_SCORECARD_CACHE_PATH
    payload = _read_json_safe(path)
    if isinstance(payload, dict) and isinstance(payload.get("scorecards"), list):
        return [x for x in payload["scorecards"] if isinstance(x, dict)]
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    return []


def _default_guru_feeds() -> tuple[dict[str, Any], ...]:
    try:
        from backend.guru_sources import GURU_ARTICLE_FEEDS
        return tuple(GURU_ARTICLE_FEEDS)
    except Exception:
        return ()


def _default_rss_feeds() -> tuple[dict[str, Any], ...]:
    try:
        from backend.rss_sources import CURATED_RSS_FEEDS
        return tuple(CURATED_RSS_FEEDS)
    except Exception:
        return ()


def load_industry_chain_map(chain_map_path: Path | str | None = None) -> dict[str, Any]:
    """Load the scrubbed US-CN supply chain map JSON."""
    path = Path(chain_map_path) if chain_map_path is not None else DEFAULT_CHAIN_MAP_PATH
    payload = _read_json_safe(path)
    if isinstance(payload, dict):
        return sanitize_output(payload)
    return {"us_leaders": [], "cn_hk_cores": [], "resource_bridges": [], "chains": []}


# 6 core asset categories specification
_CATEGORY_SPECS: tuple[dict[str, Any], ...] = (
    {
        "id": "macro_rates",
        "name": "宏观利率与美元流动性",
        "core_question": "紧盯美国10年期美债实际利率与美联储逆回购/财政部发债净流动性拐点。",
        "static_venues": [
            {
                "name": "FRED 美联储经济数据库",
                "type": "领先风向标",
                "url": "https://fred.stlouisfed.org/",
                "focus": "追踪 TGA 余额、逆回购 RRP 与美债期限利差变化",
                "cadence": "日度/周度",
            },
            {
                "name": "CME FedWatch 利率概率",
                "type": "实时快讯",
                "url": "https://www.cmegroup.com/markets/interest-rates/cme-fedwatch-tool.html",
                "focus": "联邦基金利率期货隐含降息/加息定价偏离",
                "cadence": "实时",
            },
            {
                "name": "美国财政部季度发债公告 (QRA)",
                "type": "硬核基本面",
                "url": "https://home.treasury.gov/policy-issues/financing-the-government/quarterly-refunding",
                "focus": "中长久期国债供给节奏与拍卖投标倍数",
                "cadence": "季度/月度",
            },
        ],
        "rss_ids": ("fed_press", "ft_markets", "cnbc_finance", "investing_macro"),
        "guru_ids": ("pragcap", "kylascanlon"),
        "extra_longforms": [
            {
                "name": "Joseph Wang · FedGuy",
                "publication": "FedGuy 货币银行专栏",
                "focus": "前纽约联储交易员拆解银行准备金、QT 边界与国债回购流动性",
                "url": "https://fedguy.com/",
            },
            {
                "name": "Alfonso Peccatiello · The Macro Compass",
                "publication": "The Macro Compass",
                "focus": "全球美元信贷脉冲与主要央行政策周期对跨资产的传导",
                "url": "https://www.themacrocompass.com/",
            },
        ],
        "crowd_warning_channel": {
            "platform": "Reddit r/Economics / 微博财经热搜",
            "what_to_watch": "当大众热议降息必定立刻暴涨或长债毫无风险时，往往已接近短期利率定价拐点。",
        },
        "kol_rules": {
            "preferred_handles": (
                "KobeissiLetter",
                "artinmemes",
                "tj_research",
                "qinbafrank",
                "josephwang",
                "NickTimiraos",
                "biancoresearch",
                "MacroAlf",
                "BobEUnlimited",
                "elerianm",
                "charliebilello",
                "jsblokland",
                "TheStalwart",
                "fejau_inc",
                "kevinmuir",
            ),
            "category_tags": ("macro",),
            "ticker_affinities": {"SP500", "DOLLAR", "US10Y", "QQQ", "VIX"},
            "keywords": ("宏观", "利率", "美联储", "通胀", "美债", "流动性", "美元", "财政", "fed", "fomc", "cpi", "降息"),
            "why_default": "用于校准美联储利率预期、美元流动性松紧与跨市场风险偏好变化。",
        },
        "bridge_leaders": ("MSFT", "GEV"),
    },
    {
        "id": "us_ai_semi",
        "name": "美股科技与AI算力半导体",
        "core_question": "重点盯紧云厂商季度资本开支指引与台积电 CoWoS / HBM 供给瓶颈松紧。",
        "static_venues": [
            {
                "name": "TrendForce 集邦半导体",
                "type": "领先风向标",
                "url": "https://www.trendforce.com/",
                "focus": "追踪 DRAM/NAND 合约价、HBM 产能分配与服务器出货增速",
                "cadence": "周度/月度",
            },
            {
                "name": "SEC EDGAR 一手财报库",
                "type": "硬核基本面",
                "url": "https://www.sec.gov/edgar/searchedgar/companysearch",
                "focus": "核对大厂 10-Q/10-K 资本开支、库存周转与大客户集中度",
                "cadence": "季度/事件驱动",
            },
        ],
        "rss_ids": ("openai_blog", "anthropic_news", "deepmind_blog", "google_ai", "hn_ai", "seekingalpha_currents"),
        "guru_ids": ("semianalysis", "fabricated", "stratechery", "dwarkesh"),
        "extra_longforms": [],
        "crowd_warning_channel": {
            "platform": "Reddit WSB / 雪球 AI 算力热股榜",
            "what_to_watch": "散户高倍看涨期权扎堆、只谈市梦率不谈折旧与现金流时，警惕财报前后高波动回撤。",
        },
        "kol_rules": {
            "preferred_handles": (
                "ShanghaoJin",
                "Venu_7_",
                "yianisz",
                "ohiain",
                "octopusycc",
                "aleabitoreddit",
                "hanking66",
                "bboczeng",
                "qinbafrank",
                "SemiAnalysis_",
                "dylan522p",
                "GavinSBaker",
                "benthompson",
                "altcap",
                "jaminball",
            ),
            "category_tags": ("us-stocks",),
            "ticker_affinities": {
                "NVDA", "TSM", "AVGO", "AMD", "MU", "MSFT", "GOOG", "GOOGL",
                "META", "AAPL", "AMZN", "ARM", "INTC", "QCOM", "PLTR", "ORCL", "SMCI",
            },
            "keywords": ("半导体", "算力", "芯片", "存储", "ai", "gpu", "晶圆", "cowos", "hbm", "dram", "云计算", "美股"),
            "why_default": "跟踪算力订单、先进封装排产与龙头估值预期差，过滤财报季噪声。",
        },
        "bridge_leaders": ("NVDA", "TSM", "ASML", "AVGO", "AMD", "MU", "COHR", "VRT", "DELL"),
    },
    {
        "id": "cn_hk_equities",
        "name": "A股与港股核心资产",
        "core_question": "核心跟踪海外产业订单外溢兑现、国内信贷脉冲与南向资金净流入连续性。",
        "static_venues": [
            {
                "name": "港交所披露易",
                "type": "硬核基本面",
                "url": "https://www.hkexnews.hk/",
                "focus": "一线核查腾讯、阿里、中芯国际回购注销与季度财报原文",
                "cadence": "日度/季度",
            },
            {
                "name": "财联社 · 盘中电报",
                "type": "实时快讯",
                "url": "https://www.cls.cn/telegraph",
                "focus": "跟踪国内产业政策、并购重组、行业排产与高频景气变化",
                "cadence": "实时",
            },
            {
                "name": "国家统计局宏观月报",
                "type": "领先风向标",
                "url": "https://www.stats.gov.cn/",
                "focus": "官方制造业 PMI 新订单、工业企业利润与出口分项",
                "cadence": "月度",
            },
        ],
        "rss_ids": ("qbitai",),
        "guru_ids": (),
        "extra_longforms": [
            {
                "name": "晚点 LatePost · 科技与产业",
                "publication": "晚点深度报道",
                "focus": "中国云厂商资本开支、国产算力集群与出海供应链一手采访",
                "url": "https://www.latepost.com/",
            },
            {
                "name": "财新 · 宏观与金融",
                "publication": "财新网深度",
                "focus": "社融信贷结构、财政化债节奏与外资配置动向解析",
                "url": "https://www.caixin.com/",
            },
        ],
        "crowd_warning_channel": {
            "platform": "雪球热股榜 / 东方财富股吧 / 微博热搜",
            "what_to_watch": "题材股连续涨停且社交讨论度飙升、人人晒收益截图时，警惕题材透支与高换手分歧。",
        },
        "kol_rules": {
            "preferred_handles": (
                "cyrilxuq",
                "ShanghaoJin",
                "SVScholar",
                "bboczeng",
                "aleabitoreddit",
                "michaeljburry",
            ),
            "category_tags": (),
            "ticker_affinities": {"BABA", "TSM", "0700.HK", "0981.HK"},
            "keywords": ("中港", "港股", "a股", "中概", "baba", "阿里", "腾讯", "国产", "供应链"),
            "why_default": "对照美股算力与海外订单传导，验证 A 股与港股核心资产的景气与估值位置。",
        },
        "bridge_leaders": ("0700.HK", "BABA", "0981.HK", "TSLA"),
    },
    {
        "id": "crypto_onchain",
        "name": "加密资产与链上衍生品",
        "core_question": "紧盯美国比特币现货 ETF 净申赎流向、稳定币总市值增速与永续资金费率。",
        "static_venues": [
            {
                "name": "Coinglass 衍生品热力图",
                "type": "实时快讯",
                "url": "https://www.coinglass.com/",
                "focus": "监控全网合约未平仓量、爆仓分布与永续合约资金费率",
                "cadence": "实时",
            },
            {
                "name": "Glassnode 链上周期",
                "type": "领先风向标",
                "url": "https://glassnode.com/",
                "focus": "观察长期持有者筹码派发、交易所净流入与已实现盈亏",
                "cadence": "日度/周度",
            },
            {
                "name": "DeFiLlama 协议与稳定币",
                "type": "硬核基本面",
                "url": "https://defillama.com/",
                "focus": "追踪 USDT/USDC 铸造增量、链上真实协议费用与锁仓量",
                "cadence": "日度",
            },
        ],
        "rss_ids": ("coindesk", "cointelegraph"),
        "guru_ids": ("cryptohayes", "vitalik"),
        "extra_longforms": [],
        "crowd_warning_channel": {
            "platform": "Reddit r/CryptoCurrency / X 山寨币狂热榜",
            "what_to_watch": "高倍杠杆资金费率持续飙高且无基本面新币刷屏时，往往临近去杠杆插针洗盘。",
        },
        "kol_rules": {
            "preferred_handles": (
                "xtony1314",
                "starzq",
                "Venu_7_",
                "embercn",
                "tiabitcoin",
                "CryptoHayes",
                "VitalikButerin",
                "nic_carter",
                "RaoulGMI",
                "HsakaTrades",
                "saylor",
                "LynAldenContact",
                "0Xsunnft",
                "Rewkang",
                "vida_bwe",
            ),
            "category_tags": ("crypto",),
            "ticker_affinities": {"BTC", "ETH", "SOL", "COIN", "MSTR", "HYPE"},
            "keywords": ("加密", "链上", "比特币", "以太坊", "鲸鱼", "crypto", "btc", "eth", "稳定币", "永续"),
            "why_default": "结合链上筹码流向与美元流动性周期，辨别趋势延续或杠杆过热风险。",
        },
        "bridge_leaders": ("COIN", "MSTR"),
    },
    {
        "id": "gold_commodities",
        "name": "黄金原油与地缘大宗",
        "core_question": "核心跟踪央行购金季度净量、美债实际利率背离度及 EIA 原油库存/核电铀供需。",
        "static_venues": [
            {
                "name": "世界黄金协会 (WGC) 需求报告",
                "type": "硬核基本面",
                "url": "https://www.gold.org/goldhub/data",
                "focus": "统计全球央行购金净买入、黄金 ETF 持仓与实物金流向",
                "cadence": "月度/季度",
            },
            {
                "name": "美国能源信息署 (EIA) 周报",
                "type": "领先风向标",
                "url": "https://www.eia.gov/petroleum/supply/weekly/",
                "focus": "追踪美国商业原油库存、产量变化与炼厂开工率",
                "cadence": "周度",
            },
            {
                "name": "CFTC 期货持仓报告 (COT)",
                "type": "硬核基本面",
                "url": "https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm",
                "focus": "监测黄金、白银、原油管理基金净多头拥挤度",
                "cadence": "周度",
            },
        ],
        "rss_ids": ("ft_markets", "investing_macro"),
        "guru_ids": ("doomberg",),
        "extra_longforms": [
            {
                "name": "Goehring & Rozencwajg · 自然资源季报",
                "publication": "G&R 资源研究",
                "focus": "长期供需资本开支周期下的原油、铜矿、铀矿与贵金属深度测算",
                "url": "https://www.gorozen.com/research/commentaries",
            },
        ],
        "crowd_warning_channel": {
            "platform": "小红书黄金抢购热帖 / Reddit r/Wallstreetsilver",
            "what_to_watch": "大众媒体集中报道排队买金条或白银逼空口号时，短期常伴随获利盘兑现与回调。",
        },
        "kol_rules": {
            "preferred_handles": (
                "WallStreet0Name",
                "KobeissiLetter",
                "qinbafrank",
                "raycat2021",
                "hanking66",
                "TaviCosta",
                "LukeGromen",
                "AndreasSteno",
            ),
            "category_tags": (),
            "ticker_affinities": {"GOLD", "WTI", "XAU", "USO", "SLV"},
            "keywords": ("黄金", "原油", "大宗", "贵金属", "资源", "能源", "gold", "oil", "wti", "铜", "铀"),
            "why_default": "把握贵金属、能源与战略资源品类的宏观通胀对冲节奏和关键技术位。",
        },
        "bridge_leaders": ("GOLD", "WTI", "GEV", "SMR", "OKLO", "UUUU"),
    },
    {
        "id": "quant_allocation",
        "name": "量化系统与长期配置",
        "core_question": "紧盯跨资产波动率结构、标普500等权市场宽度与系统化趋势/因子拥挤度。",
        "static_venues": [
            {
                "name": "Kenneth French 因子数据库",
                "type": "硬核基本面",
                "url": "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html",
                "focus": "验证价值、动量、质量与规模因子的历史长期超额收益与回撤",
                "cadence": "月度",
            },
            {
                "name": "CBOE VIX 期限结构",
                "type": "领先风向标",
                "url": "https://www.cboe.com/tradable_products/vix/",
                "focus": "观察现货波动率与期货升贴水变化，识别恐慌与尾部对冲信号",
                "cadence": "日度",
            },
        ],
        "rss_ids": ("arxiv_qfin", "marketwatch"),
        "guru_ids": ("awealthofcs", "pragcap", "stratechery"),
        "extra_longforms": [
            {
                "name": "Cliff Asness · AQR Perspectives",
                "publication": "AQR 机构研究专栏",
                "focus": "系统化因子投资、风险平价分散化与逆向估值纪律实证",
                "url": "https://www.aqr.com/Insights/Perspectives",
            },
        ],
        "crowd_warning_channel": {
            "platform": "雪球高股息跟风榜 / Reddit r/investing",
            "what_to_watch": "散户集体抛弃分散化并扎堆单一热门风格 ETF 时，往往意味着因子拥挤度接近临界点。",
        },
        "kol_rules": {
            "preferred_handles": (
                "Venu_7_",
                "yianisz",
                "ohiain",
                "raycat2021",
                "SVScholar",
                "MikeZaccardi",
                "TheMarketMemo",
                "CliffordAsness",
                "choffstein",
                "AlphaArchitect",
                "mebfaber",
                "nntaleb",
                "Quantocracy",
                "awealthofcs",
                "morganhousel",
                "macrocephalopod",
                "therobotjames",
                "patrick_oshag",
                "BillAckman",
                "markminervini",
                "unusual_whales",
            ),
            "category_tags": ("quant", "investing"),
            "ticker_affinities": {"SP500", "QQQ", "AAPL", "VIX"},
            "keywords": ("量化", "因子", "配置", "指数", "波动", "摆动", "动量", "宽度", "dca", "期权", "结构"),
            "why_default": "用于建立系统化仓位纪律、检验市场宽度并防范单边集中持仓的尾部回撤。",
        },
        "bridge_leaders": ("SP500", "AAPL"),
    },
)

# Concise curated role/focus metadata for well-known qualitative experts if missing from scorecards
_QUALITATIVE_FALLBACK_PROFILES: dict[str, dict[str, Any]] = {
    "josephwang": {"name": "Joseph Wang (FedGuy)", "role_label": "宏观策略", "focus": "美联储资产负债表与回购流动性", "best_tickers": ["US10Y", "DOLLAR"]},
    "NickTimiraos": {"name": "Nick Timiraos", "role_label": "宏观策略", "focus": "美联储FOMC利率路径前瞻", "best_tickers": ["US10Y", "SP500"]},
    "biancoresearch": {"name": "Jim Bianco", "role_label": "宏观策略", "focus": "美债收益率与通胀粘性", "best_tickers": ["US10Y", "SP500"]},
    "MacroAlf": {"name": "Alfonso Peccatiello", "role_label": "宏观策略", "focus": "全球美元流动性与信贷周期", "best_tickers": ["SP500", "DOLLAR"]},
    "CryptoHayes": {"name": "Arthur Hayes", "role_label": "加密链上", "focus": "美元流动性周期与比特币定价", "best_tickers": ["BTC", "ETH"]},
    "VitalikButerin": {"name": "Vitalik Buterin", "role_label": "加密链上", "focus": "以太坊协议扩容与底层治理", "best_tickers": ["ETH"]},
    "nic_carter": {"name": "Nic Carter", "role_label": "加密链上", "focus": "比特币宏观与稳定币监管", "best_tickers": ["BTC", "CRWV"]},
    "RaoulGMI": {"name": "Raoul Pal", "role_label": "加密链上", "focus": "全球流动性与加密周期轮动", "best_tickers": ["BTC", "SOL"]},
    "TaviCosta": {"name": "Tavi Costa", "role_label": "宏观策略", "focus": "贵金属、大宗资源与宏观周期", "best_tickers": ["GOLD", "WTI"]},
    "LukeGromen": {"name": "Luke Gromen", "role_label": "宏观策略", "focus": "美债赤字主导与黄金周期", "best_tickers": ["GOLD", "DOLLAR"]},
    "AndreasSteno": {"name": "Andreas Steno Larsen", "role_label": "宏观策略", "focus": "全球流动性、外汇与商品领先指标", "best_tickers": ["GOLD", "WTI"]},
    "CliffordAsness": {"name": "Cliff Asness", "role_label": "量化系统", "focus": "因子投资与系统化分散配置", "best_tickers": ["SP500"]},
    "choffstein": {"name": "Corey Hoffstein", "role_label": "量化系统", "focus": "系统化波动率管理与因子配置", "best_tickers": ["SP500"]},
    "AlphaArchitect": {"name": "Alpha Architect", "role_label": "量化系统", "focus": "因子投资与学术实证转化", "best_tickers": ["SP500"]},
    "mebfaber": {"name": "Meb Faber", "role_label": "量化系统", "focus": "全球资产估值与系统化趋势配置", "best_tickers": ["SP500", "AAPL"]},
    "nntaleb": {"name": "Nassim Taleb", "role_label": "量化系统", "focus": "尾部风险对冲与概率思维", "best_tickers": ["NVDA", "SP500"]},
    "GavinSBaker": {"name": "Gavin Baker", "role_label": "AI算力产业", "focus": "半导体资本开支与AI数据中心深度投资", "best_tickers": ["NVDA", "TSM"]},
    "benthompson": {"name": "Ben Thompson", "role_label": "AI算力产业", "focus": "科技平台商业壁垒与AI算力经济", "best_tickers": ["MSFT", "META", "NVDA"]},
}


def _build_why_follow(card: dict[str, Any], default_reason: str) -> str:
    focus = scrub_forbidden(str(card.get("focus") or "").strip())
    tickers = [str(t) for t in (card.get("best_tickers") or []) if str(t).strip()]
    verified = int(card.get("verified_calls") or 0)
    t_txt = f"（高频跟踪 {'/'.join(tickers[:3])}）" if tickers else ""
    if focus and len(focus) >= 4:
        if verified >= MIN_VERIFIED_SAMPLE:
            return scrub_forbidden(f"聚焦「{focus}」{t_txt}，历史核验样本达标，适合作为波段与趋势交叉印证。")
        return scrub_forbidden(f"聚焦「{focus}」{t_txt}，{default_reason}")
    return scrub_forbidden(default_reason)


def _normalize_kol_entry(card: dict[str, Any], default_reason: str) -> dict[str, Any]:
    handle = str(card.get("handle") or "").strip()
    name = scrub_forbidden(str(card.get("name") or handle))
    role_label = scrub_forbidden(str(card.get("role_label") or "行业观察"))
    verified_calls = max(0, int(card.get("verified_calls") or 0))

    if verified_calls >= MIN_VERIFIED_SAMPLE:
        raw_wr = card.get("win_rate_20d")
        win_rate_20d: float | None = round(float(raw_wr), 1) if raw_wr is not None else None
        raw_badge = str(card.get("reliability_badge") or "").strip()
        if raw_badge in _VALID_BADGES_VERIFIED:
            badge = raw_badge
        elif win_rate_20d is not None and win_rate_20d >= 60.0:
            badge = "高胜率"
        elif win_rate_20d is not None and win_rate_20d >= 52.0:
            badge = "稳健参考"
        else:
            badge = "观点敏锐"
    else:
        win_rate_20d = None
        badge = "样本积累" if verified_calls > 0 else "定性观察"

    best_tickers = [str(t).strip().upper() for t in (card.get("best_tickers") or []) if str(t).strip()][:4]
    url = str(card.get("url") or "").strip()
    if not url:
        url = f"https://x.com/{handle}"

    return {
        "handle": handle,
        "name": name,
        "role_label": role_label,
        "reliability_badge": badge,
        "win_rate_20d": win_rate_20d,
        "verified_calls": verified_calls,
        "best_tickers": best_tickers,
        "why_follow": _build_why_follow(card, default_reason),
        "url": url,
    }


def _score_kol_for_category(card: dict[str, Any], rules: dict[str, Any]) -> tuple[int, int]:
    """Return (relevance_score, preferred_rank_bonus) for a scorecard in a category."""
    handle = str(card.get("handle") or "").strip()
    h_low = handle.lower()
    if not handle or h_low in MEDIA_WIRE_HANDLES:
        return (0, 0)
    if str(card.get("reliability_badge") or "").strip() == "反向警示":
        return (0, 0)

    preferred = [h.lower() for h in rules["preferred_handles"]]
    rel = 0
    pref_bonus = 0
    if h_low in preferred:
        idx = preferred.index(h_low)
        rel += 55
        pref_bonus = max(1, len(preferred) - idx)

    cat_low = str(card.get("category") or "").strip().lower()
    if cat_low and cat_low in rules["category_tags"]:
        rel += 18

    card_tickers = {str(t).strip().upper() for t in (card.get("best_tickers") or []) if str(t).strip()}
    overlap = len(card_tickers & set(rules["ticker_affinities"]))
    if overlap > 0:
        rel += min(30, overlap * 15)

    text_blob = " ".join(
        str(card.get(k) or "") for k in ("focus", "role_label", "latest_headline", "category_label")
    ).lower()
    kw_hits = sum(1 for kw in rules["keywords"] if kw in text_blob)
    if kw_hits > 0:
        rel += min(28, kw_hits * 12)

    return (rel, pref_bonus)


def _select_top_kols(
    spec: dict[str, Any],
    scorecards: list[dict[str, Any]],
    *,
    limit: int = 5,
) -> list[dict[str, Any]]:
    rules = spec["kol_rules"]
    scored: list[tuple[tuple[int, int, int, float, int, int], dict[str, Any]]] = []

    seen_handles: set[str] = set()
    for card in scorecards:
        handle = str(card.get("handle") or "").strip()
        if not handle or handle.lower() in seen_handles:
            continue
        rel, pref_bonus = _score_kol_for_category(card, rules)
        if rel < 15:
            continue
        seen_handles.add(handle.lower())
        verified_calls = max(0, int(card.get("verified_calls") or 0))
        is_verified = 1 if verified_calls >= MIN_VERIFIED_SAMPLE else 0
        has_sample = 1 if verified_calls > 0 else 0
        rel_bucket = 2 if rel >= 40 else (1 if rel >= 25 else 0)
        rel_score = float(card.get("reliability_score") or 68.0)
        sort_key = (is_verified, rel_bucket, has_sample, rel_score, rel + pref_bonus, verified_calls)
        scored.append((sort_key, card))

    scored.sort(key=lambda item: item[0], reverse=True)
    selected: list[dict[str, Any]] = [
        _normalize_kol_entry(card, rules["why_default"]) for _, card in scored[:limit]
    ]

    # If caller provided an empty/sparse list and fewer than 4 authors matched, pad with curated qualitative experts
    if len(selected) < 4:
        for h in rules["preferred_handles"]:
            if h.lower() in seen_handles:
                continue
            fb = _QUALITATIVE_FALLBACK_PROFILES.get(h)
            if not fb:
                continue
            seen_handles.add(h.lower())
            stub = {
                "handle": h,
                "name": fb["name"],
                "role_label": fb["role_label"],
                "reliability_badge": "定性观察",
                "verified_calls": 0,
                "win_rate_20d": None,
                "best_tickers": fb["best_tickers"],
                "focus": fb["focus"],
            }
            selected.append(_normalize_kol_entry(stub, rules["why_default"]))
            if len(selected) >= 4:
                break

    return selected[:6]


def _build_primary_venues(spec: dict[str, Any], rss_by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    venues = [dict(v) for v in spec["static_venues"]]
    seen_urls = {str(v.get("url") or "").rstrip("/") for v in venues}
    for fid in spec.get("rss_ids", ()):
        feed = rss_by_id.get(fid)
        if not feed:
            continue
        url = str(feed.get("html_url") or feed.get("url") or "").strip()
        if not url or url.rstrip("/") in seen_urls:
            continue
        seen_urls.add(url.rstrip("/"))
        tier = str(feed.get("tier") or "T1_5")
        vtype = "领先风向标" if tier == "T1" else ("实时快讯" if str(feed.get("category")) in ("equities", "crypto") else "硬核基本面")
        cadence = "实时" if vtype == "实时快讯" else ("周度/专题" if str(feed.get("max_age_days") or "") in ("14", "21") else "日度/事件驱动")
        venues.append(
            {
                "name": str(feed.get("source") or fid),
                "type": vtype,
                "url": url,
                "focus": str(feed.get("why") or "跟踪该品类的核心新闻与政策催化"),
                "cadence": cadence,
            }
        )
        if len(venues) >= 5:
            break
    return venues


def _build_guru_longforms(spec: dict[str, Any], guru_by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    longforms: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for gid in spec.get("guru_ids", ()):
        gf = guru_by_id.get(gid)
        if not gf:
            continue
        url = str(gf.get("html_url") or gf.get("url") or "").strip()
        if not url or url.rstrip("/") in seen_urls:
            continue
        seen_urls.add(url.rstrip("/"))
        longforms.append(
            {
                "name": str(gf.get("author") or gf.get("publisher") or gid),
                "publication": str(gf.get("publisher") or gid),
                "focus": str(gf.get("background") or gf.get("author_role") or "行业与宏观深度长文"),
                "url": url,
            }
        )
    for extra in spec.get("extra_longforms", ()):
        url = str(extra.get("url") or "").strip()
        if url.rstrip("/") in seen_urls:
            continue
        seen_urls.add(url.rstrip("/"))
        longforms.append(dict(extra))
    return longforms


def _build_supply_chain_bridges(spec: dict[str, Any], chain_map: dict[str, Any]) -> list[dict[str, Any]]:
    node_by_sym: dict[str, dict[str, Any]] = {}
    for bucket in ("us_leaders", "cn_hk_cores", "resource_bridges"):
        for item in chain_map.get(bucket) or []:
            if not isinstance(item, dict) or not item.get("symbol"):
                continue
            sym = str(item["symbol"]).strip().upper()
            node_by_sym[sym] = item
            canon = str(item.get("canonical_symbol") or "").strip().upper()
            if canon and canon not in node_by_sym:
                node_by_sym[canon] = item

    bridges: list[dict[str, Any]] = []
    for leader_sym in spec.get("bridge_leaders", ()):
        node = node_by_sym.get(leader_sym.upper())
        if not node:
            continue
        a_shares = [
            {
                "symbol": str(a.get("symbol") or ""),
                "name": str(a.get("name") or ""),
                "sector": str(a.get("sector") or ""),
            }
            for a in (node.get("a_share_mappings") or [])
            if isinstance(a, dict) and a.get("symbol")
        ]
        if not a_shares:
            continue
        bridges.append(
            {
                "us_leader": str(node.get("symbol") or leader_sym),
                "us_name": str(node.get("name") or leader_sym),
                "focus": str(node.get("focus") or node.get("role") or ""),
                "a_shares": a_shares,
            }
        )
    return bridges


def build_asset_source_matrix(
    kol_scorecards: list[dict[str, Any]] | None = None,
    guru_feeds: Sequence[dict[str, Any]] | None = None,
    rss_feeds: Sequence[dict[str, Any]] | None = None,
    chain_map_path: Path | str | None = None,
    *,
    scorecard_cache_path: Path | str | None = None,
) -> dict[str, Any]:
    """Build the 6-category asset intelligence navigation matrix with dynamic KOL win rates and US-CN supply chain bridges."""
    cards = (
        [c for c in kol_scorecards if isinstance(c, dict)]
        if kol_scorecards is not None
        else _default_kol_scorecards(scorecard_cache_path)
    )
    g_feeds = list(guru_feeds) if guru_feeds is not None else list(_default_guru_feeds())
    r_feeds = list(rss_feeds) if rss_feeds is not None else list(_default_rss_feeds())
    chain_map = load_industry_chain_map(chain_map_path)

    guru_by_id = {str(g.get("id") or ""): g for g in g_feeds if isinstance(g, dict) and g.get("id")}
    rss_by_id = {str(r.get("id") or ""): r for r in r_feeds if isinstance(r, dict) and r.get("id")}

    categories: list[dict[str, Any]] = []
    verified_kol_handles: set[str] = set()
    bridge_nodes: set[str] = set()

    for spec in _CATEGORY_SPECS:
        top_kols = _select_top_kols(spec, cards, limit=5)
        for k in top_kols:
            if int(k.get("verified_calls") or 0) >= MIN_VERIFIED_SAMPLE:
                verified_kol_handles.add(str(k["handle"]).lower())

        bridges = _build_supply_chain_bridges(spec, chain_map)
        for b in bridges:
            bridge_nodes.add(str(b["us_leader"]).upper())

        categories.append(
            {
                "id": spec["id"],
                "name": spec["name"],
                "core_question": spec["core_question"],
                "primary_venues": _build_primary_venues(spec, rss_by_id),
                "top_kols": top_kols,
                "guru_longforms": _build_guru_longforms(spec, guru_by_id),
                "crowd_warning_channel": dict(spec["crowd_warning_channel"]),
                "supply_chain_bridges": bridges,
            }
        )

    result = {
        "updated_at_bjt": datetime.now(BJT).isoformat(timespec="seconds"),
        "summary": {
            "categories_count": len(categories),
            "verified_kols_linked": len(verified_kol_handles),
            "supply_chain_nodes": len(bridge_nodes),
        },
        "categories": categories,
    }
    return sanitize_output(result)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    matrix = build_asset_source_matrix()
    print(json.dumps(matrix["summary"], ensure_ascii=False, indent=2))
    for cat in matrix["categories"]:
        v_cnt = sum(1 for k in cat["top_kols"] if k["verified_calls"] >= MIN_VERIFIED_SAMPLE)
        print(
            f"  [{cat['id']}] {cat['name']}: venues={len(cat['primary_venues'])}, "
            f"kols={len(cat['top_kols'])}(verified={v_cnt}), "
            f"gurus={len(cat['guru_longforms'])}, bridges={len(cat['supply_chain_bridges'])}"
        )
