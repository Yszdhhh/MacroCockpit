"""US equity individual stock fundamentals, valuation, quarterly financials & 1Y chart builder."""
from __future__ import annotations

import json
import math
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:
    requests = None  # type: ignore[assignment]

from backend.fred_and_market import DATA_DIR, _infer_us_session, atomic_write_json
from backend.sec_edgar import SEC_CIK_MAP, fetch_sec_filings_map

BJT = timezone(timedelta(hours=8))
CACHE_PATH = DATA_DIR / "stock_profiles_cache.json"
CACHE_TTL_SECONDS = 6 * 3600

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

_FORBIDDEN_SCRUB = (
    (r"\bE33\b|\bE32\b|\bE21\b", "波动结构"),
    (r"\bTier-1\b", "核心"),
    (r"\bTier-S\b", "高优"),
    (r"\bTier-A\b", "重点"),
    (r"\bSleeves?\b", "仓位"),
    (r"\bRegimes?\b", "市场周期"),
    (r"\barbitration\b", "风险评估"),
    (r"\bN/A\b", "—"),
)


def _scrub(text: Any) -> str:
    s = str(text or "").strip()
    if not s:
        return ""
    for pat, rep in _FORBIDDEN_SCRUB:
        s = re.sub(pat, rep, s, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", s).strip()


def _finite(val: Any) -> float | None:
    if val is None or isinstance(val, bool):
        return None
    try:
        num = float(val)
        return num if math.isfinite(num) else None
    except (TypeError, ValueError):
        return None


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


def fetch_gtimg_batch(symbols: list[str], timeout: int = 6) -> dict[str, dict[str, Any]]:
    """Fast batch quote + valuation snapshot from Tencent qt.gtimg.cn."""
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
            raw_sym = parts[2].split(".")[0].strip().upper()
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
                "price": round(price, 2),
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
                "name_en": parts[46].strip(),
                "eps_ttm": _finite(parts[47]),
                "high_52w": _finite(parts[48]),
                "low_52w": _finite(parts[49]),
                "dividend_yield_pct": div_pct,
            }
    return out


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
                        dates.append(datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d"))
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
) -> dict[str, Any]:
    ma20 = round(sum(closes[-20:]) / 20.0, 2) if len(closes) >= 20 else None
    ma50 = round(sum(closes[-50:]) / 50.0, 2) if len(closes) >= 50 else None
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
                "detail": f"现价 ${price:.2f} {'站上' if above20 else '低于'} MA20 (${ma20:.2f})，乖离 {bias20_pct:+.2f}%",
            }
        )
    if ma50 is not None and bias50_pct is not None:
        above50 = price >= ma50
        checks.append(
            {
                "key": "ma50_trend",
                "label": "50日均线趋势",
                "status": "pass" if above50 else "warn",
                "detail": f"现价 ${price:.2f} {'站上' if above50 else '低于'} MA50 (${ma50:.2f})，乖离 {bias50_pct:+.2f}%",
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


def build_quick_thesis(
    profile: dict[str, Any],
    *,
    risk_gate: dict[str, Any] | None = None,
    signals: list[dict[str, Any]] | None = None,
    kol_digest: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Synthesize deterministic fast multi-angle stock thesis (stance, 0-100 score, bull/bear points, discipline hint)."""
    sym = str(profile.get("symbol") or "").strip().upper()
    cn_name = str(profile.get("name_cn") or sym).strip()
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
    fcf_sign = _parse_pct_value(fcf_display.replace("$", "").rstrip("KMBT"))
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
        margin_cash.append(f"近 12 个月自由现金流为正（{fcf_display}）")
    if margin_cash:
        bull_points.append("，".join(margin_cash))
    if upside_pct is not None and upside_pct > 2.0:
        bull_points.append(f"华尔街一致目标均价 ${target_px:.2f}，较现价存 {upside_pct:+.1f}% 修复空间")
    if bull_sig_titles:
        bull_points.append(f"站内命中 {len(bull_sig_titles)} 条偏多催化快讯：{bull_sig_titles[0][:44]}")
    if len(bull_points) < 2 and sec_filings:
        bull_points.append(f"已纳入 SEC EDGAR 持续追踪（最新 {len(sec_filings)} 份官方披露文件可一键溯源）")
    if len(bull_points) < 2:
        mcap_txt = f"市值 {mcap_fmt}、" if mcap_fmt and mcap_fmt != "—" else ""
        pos_txt = f"位于 52 周区间 {pos_52w:.1f}% 分位" if pos_52w is not None else "流动性与盘面报价保持活跃"
        px_txt = f"最新价 ${price:.2f}，" if price is not None else ""
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
) -> dict[str, Any] | None:
    meta = STOCK_META_MAP.get(symbol)
    cn_name = meta[1] if meta else _scrub(yahoo.get("name_en") or gtimg.get("name_en") or symbol)
    exch = meta[2] if meta else _scrub(yahoo.get("exchange") or "美股")
    sector_cn = meta[3] if meta else _scrub(yahoo.get("sector_en") or "美股个股")
    peer_group = meta[4] if meta else "自选检索"
    overview_cn = meta[5] if meta else _scrub(
        f"{gtimg.get('name_en') or yahoo.get('name_en') or symbol}（{symbol}），所属行业 {yahoo.get('industry_en') or sector_cn}。"
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
    pb = _finite(yahoo.get("pb")) or _finite(gtimg.get("pb"))
    div_pct = yahoo.get("dividend_yield_pct")
    if div_pct is None:
        div_pct = gtimg.get("dividend_yield_pct")
    div_fmt = f"{div_pct:.2f}%" if _finite(div_pct) is not None else "—"

    low_52w = _finite(yahoo.get("low_52w")) or _finite(gtimg.get("low_52w"))
    high_52w = _finite(yahoo.get("high_52w")) or _finite(gtimg.get("high_52w"))

    vol = _finite(gtimg.get("volume")) or _finite(yahoo.get("volume_raw"))
    vol_fmt = f"{vol / 1e6:.2f}M" if (vol is not None and vol >= 1e5) else (_fmt_num(vol, 0) if vol is not None else "—")
    turnover_fmt = _fmt_money_compact(gtimg.get("turnover"))

    chart = yahoo.get("chart") or {}
    closes = [float(c) for c in (chart.get("closes") or []) if _finite(c) is not None]
    dates = list(chart.get("dates") or [])[: len(closes)]

    checklist = _build_pre_trade_checklist(
        price=round(price, 2),
        closes=closes,
        low_52w=low_52w,
        high_52w=high_52w,
        risk_gate=risk_gate,
    )
    now_bjt = datetime.now(BJT).strftime("%Y-%m-%d %H:%M:%S+08:00")
    as_of_str = _scrub(gtimg.get("as_of") or datetime.now(BJT).strftime("%Y-%m-%d %H:%M"))
    market_at = str(gtimg.get("market_at") or as_of_str)
    received_at = str(gtimg.get("received_at") or now_bjt)
    quote_source = str(gtimg.get("source") or ("yahoo_finance" if yahoo else "local_cache"))
    session = str(gtimg.get("session") or "closed")

    assembled: dict[str, Any] = {
        "symbol": symbol,
        "name_cn": _scrub(cn_name),
        "name_en": _scrub(gtimg.get("name_en") or yahoo.get("name_en") or symbol),
        "exchange": _scrub(exch),
        "sector_cn": _scrub(sector_cn),
        "peer_group": _scrub(peer_group),
        "business_overview_cn": _scrub(overview_cn),
        "price": round(price, 2),
        "change_amt": round(change_amt, 2),
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
            "market_cap_fmt": _fmt_money_compact(mcap_raw),
            "pe_ttm": round(pe_ttm, 2) if pe_ttm is not None else None,
            "pe_forward": round(pe_fwd, 2) if pe_fwd is not None else None,
            "eps_ttm": round(eps_ttm, 2) if eps_ttm is not None else None,
            "dividend_yield_pct": round(div_pct, 2) if _finite(div_pct) is not None else None,
            "dividend_yield_fmt": div_fmt,
            "volume_fmt": vol_fmt,
            "turnover_fmt": turnover_fmt,
            "low_52w": round(low_52w, 2) if low_52w is not None else None,
            "high_52w": round(high_52w, 2) if high_52w is not None else None,
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
            "total_cash_fmt": _fmt_money_compact(yahoo.get("total_cash")),
            "total_debt_fmt": _fmt_money_compact(yahoo.get("total_debt")),
            "ttm_revenue_fmt": _fmt_money_compact(yahoo.get("ttm_revenue")),
            "ttm_ebitda_fmt": _fmt_money_compact(yahoo.get("ttm_ebitda")),
            "ttm_ocf_fmt": _fmt_money_compact(yahoo.get("ttm_ocf")),
            "ttm_fcf_fmt": _fmt_money_compact(yahoo.get("ttm_fcf")),
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
    """Build and cache fundamental & chart profiles for 20 core US stocks."""
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
        prof = _assemble_profile(
            sym,
            yahoo_cache.get(sym) or {},
            gtimg_map.get(sym) or {},
            sec_filings=filings_map.get(sym, []),
            risk_gate=resolved_rg,
        )
        if prof is not None:
            profiles[sym] = prof
    return profiles


def fetch_single_stock_profile(
    symbol: str,
    *,
    risk_gate: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Live lookup for an arbitrary US stock ticker via gtimg + Yahoo Finance."""
    sym = symbol.strip().upper()
    if not re.fullmatch(r"[A-Z0-9.]{1,10}", sym):
        return None
    gtimg = fetch_gtimg_batch([sym]).get(sym) or {}
    cache = _read_cache()
    yahoo = cache.get(sym)
    if not yahoo or (time.time() - float(yahoo.get("yahoo_fetched_at") or 0.0)) > CACHE_TTL_SECONDS:
        sess, crumb = _create_yahoo_session(timeout=3)
        fresh = _fetch_yahoo_one(sym, sess, crumb, timeout=4) if sess is not None else None
        if fresh:
            yahoo = fresh
            cache[sym] = fresh
            try:
                atomic_write_json(CACHE_PATH, cache)
            except Exception:
                pass
    sec_filings: list[dict[str, str]] = []
    if sym in SEC_CIK_MAP:
        try:
            sec_filings = fetch_sec_filings_map([sym], force_refresh=False, timeout=2).get(sym, [])
        except Exception:
            sec_filings = []
    return _assemble_profile(
        sym,
        yahoo or {},
        gtimg,
        sec_filings=sec_filings,
        risk_gate=risk_gate or _load_default_risk_gate(),
    )


if __name__ == "__main__":
    profs = build_stock_profiles()
    print(f"Built {len(profs)} stock profiles; sample AAPL PE={profs.get('AAPL', {}).get('overview', {}).get('pe_ttm')}")
