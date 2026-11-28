"""Cross-source market synthesis engine ('市场四维定调与跨源关联')."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from backend.guardrails import sanitize_public_value, scrub_forbidden as _scrub_text
from backend.runtime import BJT, safe_float as _safe_float

_THEME_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "id": "link_nvda_ai_compute",
        "focus": "NVDA 与 AI 算力供应链",
        "tickers": ["NVDA", "TSM", "AVGO", "AMD", "MU", "SMCI", "ARM"],
        "keywords": ("nvda", "nvidia", "英伟达", "算力", "芯片", "半导体", "cowos", "hbm", "tsm", "台积电", "avgo", "amd", "semianalysis", "fabricated"),
        "categories": ("ai", "equities"),
        "default_trigger": "关注 NVDA 20日均线得失及晶圆/HBM 排产指引是否延续上修。",
    },
    {
        "id": "link_megacap_cloud",
        "focus": "美股七巨头与云厂 AI 变现",
        "tickers": ["MSFT", "GOOG", "AMZN", "META", "ORCL"],
        "keywords": ("msft", "microsoft", "azure", "goog", "google", "gemini", "amzn", "aws", "meta", "llama", "orcl", "openai", "anthropic", "云厂", "资本开支"),
        "categories": ("ai", "equities"),
        "default_trigger": "跟踪云厂商资本开支增速与大模型企业级付费转化节奏。",
    },
    {
        "id": "link_aapl_edge_ai",
        "focus": "AAPL 与端侧消费硬件",
        "tickers": ["AAPL", "QCOM"],
        "keywords": ("aapl", "apple", "苹果", "iphone", "端侧", "qcom", "高通"),
        "categories": ("equities", "ai"),
        "default_trigger": "观察 AAPL 50日均线支撑与消费电子换机周期数据兑现。",
    },
    {
        "id": "link_tsla_autonomy",
        "focus": "TSLA 与自动驾驶/具身智能",
        "tickers": ["TSLA"],
        "keywords": ("tsla", "tesla", "特斯拉", "fsd", "robotaxi", "马斯克", "自动驾驶", "具身智能", "optimus"),
        "categories": ("equities", "ai"),
        "default_trigger": "跟踪 FSD 商业化里程碑、交付增速及 20日均线偏离度。",
    },
    {
        "id": "link_fed_rates",
        "focus": "美联储利率路径与美债定价",
        "tickers": ["US10Y", "SPY", "QQQ"],
        "keywords": ("fed", "fomc", "cpi", "pce", "treasury", "rate", "美联储", "鲍威尔", "美债", "利率", "降息", "加息", "通胀", "非农", "10年期", "流动性"),
        "categories": ("macro",),
        "default_trigger": "关注美债 10Y 收益率能否有效回落至 4.75% 下方以及美联储议息表态。",
    },
    {
        "id": "link_btc_crypto",
        "focus": "BTC 与加密流动性周期",
        "tickers": ["BTC", "ETH", "COIN", "MSTR"],
        "keywords": ("btc", "bitcoin", "eth", "ethereum", "coin", "mstr", "crypto", "etf", "比特币", "以太坊", "稳定币", "链上", "加密"),
        "categories": ("crypto", "macro"),
        "default_trigger": "观察 BTC 与标普500相关性变化及现货 ETF 净流入延续性。",
    },
    {
        "id": "link_gold_energy_usd",
        "focus": "黄金、原油与美元避险结构",
        "tickers": ["GOLD", "WTI", "DOLLAR"],
        "keywords": ("gold", "xau", "wti", "brent", "oil", "dollar", "dxy", "黄金", "原油", "美元", "能源", "核电", "大宗", "关税", "避险"),
        "categories": ("macro",),
        "default_trigger": "留意美元指数强弱切换及原油价格对再通胀预期的传导。",
    },
    {
        "id": "link_ai_software",
        "focus": "AI 软件应用与软件估值重估",
        "tickers": ["PLTR", "NFLX"],
        "keywords": ("pltr", "palantir", "nflx", "agent", "saas", "软件", "应用落地", "stratechery", "dwarkesh", "not boring"),
        "categories": ("ai", "equities"),
        "default_trigger": "跟踪 AI Agent 商业合同落地节奏与软件板块相对纳指超额收益。",
    },
    {
        "id": "link_ashare_hk",
        "focus": "A股与港股科技联动主线",
        "tickers": ["BABA"],
        "keywords": ("a股", "沪指", "深成指", "创业板", "科创板", "港股", "恒指", "恒生", "北向", "南向", "baba", "阿里", "腾讯", "中芯", "寒武纪", "deepseek"),
        "categories": ("ashare",),
        "default_trigger": "跟踪两市成交额与恒生科技指数相对美股中概的估值修复持续性。",
    },
)


def _fred_map(macro_fred: list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in macro_fred or []:
        if isinstance(row, dict) and row.get("id"):
            out[str(row["id"]).strip()] = row
    return out


def _equities_map(us_equities: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if not isinstance(us_equities, dict):
        return out
    for bucket in ("indices", "mag7_and_ai", "sectors"):
        for row in us_equities.get(bucket) or []:
            if isinstance(row, dict) and row.get("symbol"):
                out[str(row["symbol"]).strip().upper()] = row
    return out


def _corr_pair_map(correlations: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if not isinstance(correlations, dict):
        return out
    for row in correlations.get("pairs") or []:
        if isinstance(row, dict) and row.get("id"):
            out[str(row["id"]).strip()] = row
    return out


def _build_four_dimensions(
    *,
    fred_by_id: dict[str, dict[str, Any]],
    eq_by_sym: dict[str, dict[str, Any]],
    us_equities: dict[str, Any],
    correlations: dict[str, Any],
    risk_gate: dict[str, Any],
    stock_profiles: dict[str, Any],
) -> list[dict[str, Any]]:
    # 1. 美元流动性
    liq_row = fred_by_id.get("fed_net_liquidity") or {}
    dxy_row = fred_by_id.get("dollar") or {}
    dff_row = fred_by_id.get("fedfunds") or {}
    liq_chg = _safe_float(liq_row.get("change_num"))
    dxy_val = _safe_float(dxy_row.get("raw_value"))
    dxy_chg = _safe_float(dxy_row.get("change_num"))
    gate_light = str((risk_gate or {}).get("light") or "YELLOW").upper()

    if gate_light == "RED" or (dxy_val is not None and dxy_val >= 106.5 and (liq_chg or 0.0) < 0):
        liq_status, liq_tone = "流动性偏紧", "bear"
        liq_imp = "强美元叠加流动性承压，高估值资产宜严格控制仓位上限。"
    elif (liq_chg is not None and liq_chg > 0) and (dxy_chg is not None and dxy_chg <= 0):
        liq_status, liq_tone = "边际充裕", "bull"
        liq_imp = "净流动性扩张且美元走弱，利好风险资产估值修复。"
    elif gate_light == "YELLOW":
        liq_status, liq_tone = "中性偏紧", "warn"
        liq_imp = "宏观风控处于谨慎区间，顺风窗口宜分批布局而非追高。"
    else:
        liq_status, liq_tone = "相对均衡", "neutral"
        liq_imp = "美元流动性维持常态波动，关注美联储资产负债表边际变化。"

    liq_parts: list[str] = []
    if liq_row.get("value_display"):
        liq_parts.append(f"净流动性 {liq_row['value_display']}")
    if dxy_row.get("value_display"):
        dxy_c = f" ({dxy_row['change_display']})" if dxy_row.get("change_display") else ""
        liq_parts.append(f"美元指数 {dxy_row['value_display']}{dxy_c}")
    if dff_row.get("value_display"):
        liq_parts.append(f"联邦利率 {dff_row['value_display']}")
    liq_metric = " · ".join(liq_parts) or "宏观流动性数据同步中"

    # 2. 利率与通胀
    y10 = fred_by_id.get("us_10y") or {}
    y2 = fred_by_id.get("us_2y") or {}
    spr = fred_by_id.get("us_10y_2y_spread") or {}
    cpi = fred_by_id.get("cpi_yoy") or {}
    y10_val = _safe_float(y10.get("raw_value"))
    y10_chg = _safe_float(y10.get("change_num"))
    cpi_val = _safe_float(cpi.get("raw_value"))

    if y10_val is not None and y10_val >= 4.75:
        if y10_chg is not None and y10_chg < 0:
            rate_status, rate_tone = "高位缓和", "warn"
            rate_imp = "10年期美债仍在高位区间但边际回落，长久期成长股呼吸空间略增。"
        else:
            rate_status, rate_tone = "利率压制", "bear"
            rate_imp = "长端利率处于高位，对科技估值折现率和信用利差形成约束。"
    elif y10_val is not None and y10_val <= 4.10 and (cpi_val is None or cpi_val <= 2.8):
        rate_status, rate_tone = "温和可控", "bull"
        rate_imp = "利率与通胀压力可控，具备估值扩张与顺周期轮动条件。"
    else:
        rate_status, rate_tone = "区间拉锯", "neutral"
        rate_imp = "利率围绕通胀预期区间波动，密切跟踪 CPI 与就业数据指引。"

    rate_parts: list[str] = []
    if y10.get("value_display"):
        c10 = f" ({y10['change_display']})" if y10.get("change_display") else ""
        rate_parts.append(f"10Y美债 {y10['value_display']}{c10}")
    if y2.get("value_display"):
        rate_parts.append(f"2Y {y2['value_display']}")
    if spr.get("value_display"):
        rate_parts.append(f"利差 {spr['value_display']}")
    if cpi.get("value_display"):
        rate_parts.append(f"CPI同比 {cpi['value_display']}")
    rate_metric = " · ".join(rate_parts) or "利率曲线观测中"

    # 3. 美股与算力
    spy = eq_by_sym.get("SPY") or {}
    qqq = eq_by_sym.get("QQQ") or {}
    nvda = eq_by_sym.get("NVDA") or {}
    spy_chg = _safe_float(spy.get("change_pct"))
    qqq_chg = _safe_float(qqq.get("change_pct"))
    nvda_chg = _safe_float(nvda.get("change_pct"))
    ai_syms = ["NVDA", "MSFT", "GOOG", "META", "AMZN", "AVGO", "AMD", "TSM"]
    ai_changes = [_safe_float((eq_by_sym.get(s) or {}).get("change_pct")) for s in ai_syms]
    valid_ai = [c for c in ai_changes if c is not None]
    avg_ai = sum(valid_ai) / len(valid_ai) if valid_ai else None

    if qqq_chg is not None and qqq_chg >= 0.8 and (avg_ai is None or avg_ai >= 0.5):
        eq_status, eq_tone = "算力领涨", "bull"
        eq_imp = "纳指与算力权重同步走强，风险偏好向成长与半导体主线集中。"
    elif (qqq_chg is not None and qqq_chg <= -1.0) or (nvda_chg is not None and nvda_chg <= -3.0):
        eq_status, eq_tone = "算力承压", "bear"
        eq_imp = "核心算力权重波动放大，短线需观察 20日/50日均线承接再介入。"
    elif spy_chg is not None and qqq_chg is not None and (spy_chg >= 0 > qqq_chg or qqq_chg >= 0 > spy_chg):
        eq_status, eq_tone = "风格分化", "warn"
        eq_imp = "指数与科技算力走势分化，资金在防御价值与成长之间高低切换。"
    else:
        eq_status, eq_tone = "结构震荡", "neutral"
        eq_imp = "美股主流指数维持震荡整理，个股依赖业绩与 AI 订单催化。"

    eq_parts: list[str] = []
    if spy.get("change_pct") is not None:
        eq_parts.append(f"标普ETF {float(spy['change_pct']):+.2f}%")
    if qqq.get("change_pct") is not None:
        eq_parts.append(f"纳指ETF {float(qqq['change_pct']):+.2f}%")
    if nvda.get("change_pct") is not None:
        eq_parts.append(f"NVDA {float(nvda['change_pct']):+.2f}%")
    elif stock_profiles and "NVDA" in stock_profiles:
        np_chg = _safe_float((stock_profiles["NVDA"] or {}).get("change_pct"))
        if np_chg is not None:
            eq_parts.append(f"NVDA {np_chg:+.2f}%")
    if not eq_parts and isinstance(us_equities, dict) and us_equities.get("summary"):
        eq_parts.append(str(us_equities["summary"])[:48])
    eq_metric = " · ".join(eq_parts) or "美股盘面观测中"

    # 4. 加密与避险
    btc_row = fred_by_id.get("btc_spot") or {}
    gold_row = fred_by_id.get("gold") or {}
    vix_row = fred_by_id.get("vix") or {}
    btc_chg = _safe_float(btc_row.get("change_num"))
    gold_chg = _safe_float(gold_row.get("change_num"))
    vix_val = _safe_float(vix_row.get("raw_value"))

    corr_pairs = _corr_pair_map(correlations)
    btc_sp = corr_pairs.get("corr_btc_sp500") or {}
    btc_sp_30 = _safe_float(btc_sp.get("corr_30d"))

    if vix_val is not None and vix_val >= 25.0:
        ch_status, ch_tone = "避险升温", "bear"
        ch_imp = "波动率抬升触发避险定价，黄金相对加密与高弹性风险资产占优。"
    elif (btc_chg is not None and btc_chg > 1.5) and (vix_val is None or vix_val < 20.0):
        ch_status, ch_tone = "风险回暖", "bull"
        ch_imp = "加密资产领涨且波动率温和，市场对流动性溢价的承受力增强。"
    elif gold_chg is not None and gold_chg > 0.5 and (btc_chg is not None and btc_chg < 0):
        ch_status, ch_tone = "避险分流", "warn"
        ch_imp = "黄金走强而加密回调，避险需求高于高弹性投机配置。"
    else:
        ch_status, ch_tone = "联动震荡", "neutral"
        ch_imp = "加密与传统避险资产维持温和轮动，关注跨资产相关性偏离。"

    ch_parts: list[str] = []
    if btc_row.get("value_display"):
        ch_parts.append(f"BTC {btc_row['value_display']}")
    if gold_row.get("value_display"):
        ch_parts.append(f"黄金 {gold_row['value_display']}")
    if vix_row.get("value_display"):
        ch_parts.append(f"VIX {vix_row['value_display']}")
    if btc_sp_30 is not None:
        ch_parts.append(f"BTC×标普30日相关 {btc_sp_30:+.2f}")
    ch_metric = " · ".join(ch_parts) or "避险与加密指标观测中"

    return [
        {
            "id": "usd_liquidity",
            "name": "美元流动性",
            "status": liq_status,
            "tone": liq_tone,
            "metric": liq_metric,
            "implication": liq_imp,
        },
        {
            "id": "rates_inflation",
            "name": "利率与通胀",
            "status": rate_status,
            "tone": rate_tone,
            "metric": rate_metric,
            "implication": rate_imp,
        },
        {
            "id": "equities_compute",
            "name": "美股与算力",
            "status": eq_status,
            "tone": eq_tone,
            "metric": eq_metric,
            "implication": eq_imp,
        },
        {
            "id": "crypto_haven",
            "name": "加密与避险",
            "status": ch_status,
            "tone": ch_tone,
            "metric": ch_metric,
            "implication": ch_imp,
        },
    ]


def _normalize_stance_dir(raw: str) -> str:
    s = str(raw or "").strip().lower()
    if s in ("bullish", "偏多", "看多", "偏多顺风"):
        return "bullish"
    if s in ("bearish", "偏空", "看空", "偏空逆风"):
        return "bearish"
    return "neutral"


def _infer_text_dir(text: str) -> str:
    low = str(text or "").lower()
    bull = len(re.findall(r"(?i)(?:\b(?:beat|surge|rally|bull|upgrade|growth|expand|strong|breakout)\b|利好|看多|偏多|走强|大涨|超预期|上修|扩产|突破|净流入)", low))
    bear = len(re.findall(r"(?i)(?:\b(?:miss|drop|plunge|bear|downgrade|warn|risk|cut|slump|pressure)\b|利空|看空|偏空|承压|大跌|下调|关税|制裁|缩水|抛压|警告)", low))
    if bull > bear:
        return "bullish"
    if bear > bull:
        return "bearish"
    return "neutral"


def _match_score(text: str, tickers: list[str], theme_tickers: set[str], keywords: tuple[str, ...], cat: str, theme_cats: tuple[str, ...]) -> int:
    score = 0
    for tk in tickers:
        if tk.upper() in theme_tickers:
            score += 4
    low = text.lower()
    for kw in keywords:
        if kw in low:
            score += 2
    if cat and cat.lower() in theme_cats and score > 0:
        score += 1
    return score


def _build_scorecard_index(kol_scorecards: list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    idx: dict[str, dict[str, Any]] = {}
    for row in kol_scorecards or []:
        if not isinstance(row, dict):
            continue
        h = str(row.get("handle") or "").strip().lower()
        if h:
            idx[h] = row
    return idx


def _format_kol_evidence(k: dict[str, Any], scorecard: dict[str, Any] | None) -> tuple[str, float, str]:
    handle = str(k.get("handle") or "").strip()
    name = str(k.get("name") or (scorecard or {}).get("name") or f"@{handle}").strip()
    stance = str(k.get("stance") or (scorecard or {}).get("latest_stance") or "中性").strip()
    badge = str(k.get("reliability_badge") or (scorecard or {}).get("reliability_badge") or "样本积累").strip()
    wr20 = _safe_float(k.get("win_rate_20d") if k.get("win_rate_20d") is not None else (scorecard or {}).get("win_rate_20d"))
    rel_score = _safe_float(k.get("reliability_score") if k.get("reliability_score") is not None else (scorecard or {}).get("reliability_score")) or 50.0
    headline = str(k.get("headline") or k.get("takeaway") or "").strip()[:84]

    if wr20 is not None:
        meta = f"{badge} · 20日胜率 {wr20:.0f}%"
    else:
        meta = badge
    label = name if handle.lower() in name.lower() else f"{name}(@{handle})"
    ev_str = f"{label} [{meta} · {stance}]：{headline}"
    weight = 1.0 + max(0.0, (rel_score - 50.0) / 50.0)
    if wr20 is not None and wr20 >= 60.0:
        weight += 0.5
    return (ev_str, weight, _normalize_stance_dir(stance))


def _market_check_for_theme(
    *,
    theme: dict[str, Any],
    eq_by_sym: dict[str, dict[str, Any]],
    fred_by_id: dict[str, dict[str, Any]],
    stock_profiles: dict[str, Any],
    correlations: dict[str, Any],
) -> tuple[str, str]:
    """Return (market_check_text, market_direction) from real quotes, MAs, or correlations."""
    tks = [str(t).upper() for t in theme["tickers"]]
    notes: list[str] = []
    bull_votes = 0
    bear_votes = 0

    for sym in tks:
        prof = stock_profiles.get(sym) if isinstance(stock_profiles, dict) else None
        eq = eq_by_sym.get(sym)
        px = _safe_float((eq or {}).get("price") or (prof or {}).get("price"))
        chg = _safe_float((eq or {}).get("change_pct") or (prof or {}).get("change_pct"))
        bias20 = _safe_float((prof or {}).get("bias20_pct"))
        bias50 = _safe_float((prof or {}).get("bias50_pct"))

        if px is not None or chg is not None or bias20 is not None:
            segs: list[str] = []
            if px is not None:
                segs.append(f"${px:.2f}" if sym != "BTC" else f"${px:,.0f}")
            if chg is not None:
                segs.append(f"日内 {chg:+.2f}%")
                if chg >= 1.2:
                    bull_votes += 1
                elif chg <= -1.2:
                    bear_votes += 1
            if bias20 is not None:
                segs.append(f"20日均线乖离 {bias20:+.1f}%")
                if bias20 >= 1.5:
                    bull_votes += 1
                elif bias20 <= -2.0:
                    bear_votes += 1
            elif bias50 is not None:
                segs.append(f"50日均线乖离 {bias50:+.1f}%")
            notes.append(f"{sym} " + " / ".join(segs))
        if len(notes) >= 2:
            break

    # Macro FRED fallback check for rate/crypto/gold themes
    if theme["id"] == "link_fed_rates":
        y10 = fred_by_id.get("us_10y") or {}
        if y10.get("value_display"):
            c10 = y10.get("change_display") or ""
            notes.append(f"10Y美债 {y10['value_display']} ({c10})".strip())
            y10_chg = _safe_float(y10.get("change_num"))
            if y10_chg is not None:
                if y10_chg <= -0.03:
                    bull_votes += 1
                elif y10_chg >= 0.04:
                    bear_votes += 1
    elif theme["id"] == "link_btc_crypto":
        btc = fred_by_id.get("btc_spot") or {}
        if btc.get("value_display"):
            notes.append(f"BTC 现货 {btc['value_display']} ({btc.get('change_display') or ''})".rstrip(" ()"))
            bchg = _safe_float(btc.get("change_num"))
            if bchg is not None:
                if bchg >= 1.0:
                    bull_votes += 1
                elif bchg <= -1.0:
                    bear_votes += 1
        btc_sp = _corr_pair_map(correlations).get("corr_btc_sp500") or {}
        if btc_sp.get("corr_30d") is not None:
            notes.append(f"BTC×标普30日相关 {float(btc_sp['corr_30d']):+.2f}")
    elif theme["id"] == "link_gold_energy_usd":
        for fid, lbl in (("gold", "黄金"), ("wti", "WTI原油"), ("dollar", "美元指数")):
            row = fred_by_id.get(fid) or {}
            if row.get("value_display"):
                notes.append(f"{lbl} {row['value_display']} ({row.get('change_display') or ''})".rstrip(" ()"))

    if not notes:
        notes.append("暂缺直接行情快照，结合板块广度与跨资产波动跟踪")

    if bull_votes > bear_votes:
        mkt_dir = "bullish"
    elif bear_votes > bull_votes:
        mkt_dir = "bearish"
    else:
        mkt_dir = "neutral"
    return ("；".join(notes[:3]), mkt_dir)


def _normalize_evidence_text(raw: str) -> str:
    """Normalize evidence title/headline by stripping bracketed source prefixes and punctuation."""
    s = _scrub_text(raw)
    s = re.sub(r"^(?:\[[^\]]+\]\s*|[^：\[]+\[[^\]]+\]\s*[：:]\s*)", "", s).strip()
    return re.sub(r"[\W_]+", "", s.lower())


def _canonical_url_key(url: Any) -> str:
    raw = str(url or "").strip()
    if not raw.startswith(("http://", "https://")):
        return ""
    base = raw.split("#", 1)[0].split("?", 1)[0].rstrip("/").lower()
    base = re.sub(r"^https?://(?:www\.)?", "", base)
    return base


def _is_duplicate_evidence(
    *,
    seen_texts: set[str],
    seen_urls: set[str],
    text: str,
    url: Any = "",
) -> bool:
    norm_t = _normalize_evidence_text(text)
    norm_u = _canonical_url_key(url)
    if norm_t and norm_t in seen_texts:
        return True
    if norm_u and norm_u in seen_urls:
        return True
    if norm_t:
        seen_texts.add(norm_t)
    if norm_u:
        seen_urls.add(norm_u)
    return False


def _build_cross_source_links(
    *,
    signals: list[dict[str, Any]],
    kol_digest: list[dict[str, Any]],
    kol_scorecards: list[dict[str, Any]],
    articles: list[dict[str, Any]],
    eq_by_sym: dict[str, dict[str, Any]],
    fred_by_id: dict[str, dict[str, Any]],
    stock_profiles: dict[str, Any],
    correlations: dict[str, Any],
    risk_gate: dict[str, Any],
) -> list[dict[str, Any]]:
    scorecard_idx = _build_scorecard_index(kol_scorecards)
    gate_light = str((risk_gate or {}).get("light") or "YELLOW").upper()

    guru_articles = [
        a for a in (articles or [])
        if isinstance(a, dict) and (str(a.get("source") or "") == "大佬分享" or str(a.get("filename") or "").startswith("guru_"))
    ]
    other_articles = [
        a for a in (articles or [])
        if isinstance(a, dict) and a not in guru_articles
    ]

    candidates: list[tuple[int, dict[str, Any]]] = []
    for theme in _THEME_CATALOG:
        theme_tks = {t.upper() for t in theme["tickers"]}
        kws = tuple(theme["keywords"])
        tcats = tuple(theme["categories"])

        # 1. Match news signals (exclude duplicate guru/KOL echoes when matching primary wires first)
        matched_news: list[tuple[int, dict[str, Any]]] = []
        for s in signals or []:
            if not isinstance(s, dict):
                continue
            src = str(s.get("source") or "")
            if src == "大佬分享":
                continue
            s_tks = [str(t).upper() for t in (s.get("tickers") or [])]
            txt = f"{s.get('title') or ''} {s.get('summary') or ''} {' '.join(s.get('topics') or [])}"
            sc = _match_score(txt, s_tks, theme_tks, kws, str(s.get("category") or ""), tcats)
            if sc > 0:
                imp_bonus = 2 if s.get("importance") == "高" else 0
                matched_news.append((sc + imp_bonus, s))
        matched_news.sort(key=lambda x: (x[0], int(x[1].get("attention_score") or 0)), reverse=True)

        # 2. Match KOL views weighted by scorecard reliability
        matched_kols: list[tuple[float, dict[str, Any], str, float, str]] = []
        for k in kol_digest or []:
            if not isinstance(k, dict):
                continue
            k_tks = [str(t).upper() for t in (k.get("tickers") or [])]
            txt = f"{k.get('headline') or ''} {k.get('takeaway') or ''} {k.get('topic') or ''}"
            sc = _match_score(txt, k_tks, theme_tks, kws, "", tcats)
            if sc > 0:
                h_low = str(k.get("handle") or "").strip().lower()
                scard = scorecard_idx.get(h_low)
                ev_str, weight, k_dir = _format_kol_evidence(k, scard)
                matched_kols.append((sc * weight, k, ev_str, weight, k_dir))
        matched_kols.sort(key=lambda x: x[0], reverse=True)

        # 3. Match Guru longform articles (fall back to deep research articles if no guru match)
        matched_gurus: list[tuple[int, dict[str, Any]]] = []
        for pool_list, bonus in ((guru_articles, 3), (other_articles, 0)):
            for a in pool_list:
                a_tks = [str(t).upper() for t in (a.get("tickers") or [])]
                txt = f"{a.get('title') or ''} {a.get('summary') or a.get('excerpt') or ''} {a.get('publisher') or ''}"
                sc = _match_score(txt, a_tks, theme_tks, kws, str(a.get("category") or ""), tcats)
                if sc > 0:
                    matched_gurus.append((sc + bonus, a))
            if matched_gurus:
                break
        matched_gurus.sort(key=lambda x: (x[0], int(x[1].get("score") or 0)), reverse=True)

        news_evidence: list[str] = []
        seen_news_texts: set[str] = set()
        seen_news_urls: set[str] = set()
        bull_weight = 0.0
        bear_weight = 0.0
        for _sc, s in matched_news:
            raw_title = str(s.get("title") or "").strip()
            if not raw_title:
                continue
            if _is_duplicate_evidence(
                seen_texts=seen_news_texts,
                seen_urls=seen_news_urls,
                text=raw_title,
                url=s.get("url"),
            ):
                continue
            s_dir = _normalize_stance_dir(str(s.get("direction") or "neutral"))
            dir_cn = {"bullish": "偏多", "bearish": "偏空", "neutral": "中性"}[s_dir]
            news_evidence.append(f"[{s.get('source') or '快讯'} · {dir_cn}] {raw_title[:76]}")
            if s_dir == "bullish":
                bull_weight += 1.0
            elif s_dir == "bearish":
                bear_weight += 1.0
            if len(news_evidence) >= 3:
                break

        kol_evidence: list[str] = []
        seen_kol_texts: set[str] = set()
        seen_kol_urls: set[str] = set()
        for _sc, k, ev_str, weight, k_dir in matched_kols:
            kol_headline = str(k.get("headline") or k.get("takeaway") or ev_str).strip()
            if _is_duplicate_evidence(
                seen_texts=seen_kol_texts,
                seen_urls=seen_kol_urls,
                text=kol_headline,
                url=k.get("url"),
            ):
                continue
            kol_evidence.append(ev_str)
            if k_dir == "bullish":
                bull_weight += weight
            elif k_dir == "bearish":
                bear_weight += weight
            if len(kol_evidence) >= 3:
                break

        guru_evidence: list[str] = []
        seen_guru_texts: set[str] = set()
        seen_guru_urls: set[str] = set()
        related_file = ""
        for _sc, a in matched_gurus:
            pub = str(a.get("publisher") or a.get("source") or "深度长文").strip()
            ttl = str(a.get("title") or "").strip()
            if not ttl:
                continue
            if _is_duplicate_evidence(
                seen_texts=seen_guru_texts,
                seen_urls=seen_guru_urls,
                text=ttl,
                url=a.get("url"),
            ):
                continue
            guru_evidence.append(f"[{pub}] {ttl[:76]}")
            if not related_file and a.get("filename"):
                related_file = str(a["filename"])
            a_dir = _normalize_stance_dir(str(a.get("direction") or ""))
            if a_dir == "neutral":
                a_dir = _infer_text_dir(f"{a.get('title') or ''} {a.get('summary') or a.get('excerpt') or ''}")
            if a_dir == "bullish":
                bull_weight += 1.1
            elif a_dir == "bearish":
                bear_weight += 1.1
            if len(guru_evidence) >= 2:
                break

        market_check, mkt_dir = _market_check_for_theme(
            theme=theme,
            eq_by_sym=eq_by_sym,
            fred_by_id=fred_by_id,
            stock_profiles=stock_profiles,
            correlations=correlations,
        )

        # Determine cross-source pattern & tone
        msg_dir = "bullish" if bull_weight >= bear_weight + 0.6 else ("bearish" if bear_weight >= bull_weight + 0.6 else "neutral")
        has_conflict = bull_weight >= 1.0 and bear_weight >= 1.0
        active_channels = int(bool(news_evidence)) + int(bool(kol_evidence)) + int(bool(guru_evidence)) + (1 if "暂缺" not in market_check else 0)

        if (msg_dir == "bullish" and (mkt_dir == "bearish" or gate_light == "RED")) or (msg_dir == "bearish" and mkt_dir == "bullish"):
            pattern = "盘面与消息背离"
            tone = "warn"
            conclusion = (
                f"消息与观点整体{ '偏多' if msg_dir == 'bullish' else '偏空' }，但盘面/风控信号尚未共振确认，谨防逆势追单。"
            )
        elif has_conflict:
            pattern = "多空分歧焦点"
            tone = "warn"
            conclusion = "一手快讯、博主与长文观点存在多空拉锯，需等待关键均线或数据落地打破僵局。"
        elif msg_dir == "bearish" or (gate_light == "RED" and mkt_dir == "bearish"):
            pattern = "空头风控共振"
            tone = "bear"
            conclusion = "消息面与盘面趋势同步承压，主基调以防守控仓、规避左侧接飞刀为主。"
        elif msg_dir == "bullish" and active_channels >= 2:
            pattern = "多源顺风共振"
            tone = "bull"
            conclusion = "快讯、高可靠博主或深度长文形成同向偏多共振，重点跟踪回踩承接机会。"
        else:
            pattern = "多空分歧焦点"
            tone = "neutral"
            conclusion = "当前主线处于消息观察与盘面消化期，等待更多高频催化验证方向。"

        # Confidence 0..100
        align_bonus = 10 if (msg_dir != "neutral" and msg_dir == mkt_dir) else 0
        conflict_penalty = 8 if has_conflict else 0
        conf = int(max(38, min(94, 42 + active_channels * 11 + align_bonus - conflict_penalty)))

        # Active tickers actually observed or theme defaults
        found_tks: list[str] = []
        for t in theme["tickers"]:
            if (isinstance(stock_profiles, dict) and t in stock_profiles) or t in eq_by_sym or t in ("US10Y", "BTC", "GOLD", "WTI", "DOLLAR", "SPY", "QQQ"):
                found_tks.append(t)
        card_tks = (found_tks or list(theme["tickers"]))[:4]

        richness_rank = active_channels * 10 + len(news_evidence) * 2 + len(kol_evidence) * 3 + len(guru_evidence) * 3
        candidates.append(
            (
                richness_rank,
                {
                    "id": theme["id"],
                    "focus": theme["focus"],
                    "tickers": card_tks,
                    "pattern": pattern,
                    "tone": tone,
                    "confidence": conf,
                    "conclusion": conclusion,
                    "news_evidence": news_evidence or ["近期主线快讯温和，暂无单边突发催化"],
                    "kol_evidence": kol_evidence or ["博主观点分散，暂以定量盘面与长文跟踪为主"],
                    "guru_evidence": guru_evidence or ["专栏长文聚焦中期基本面结构与估值安全边际"],
                    "market_check": market_check,
                    "watch_trigger": theme["default_trigger"],
                    "related_article_file": related_file,
                },
            )
        )

    candidates.sort(key=lambda x: x[0], reverse=True)
    return [card for _rank, card in candidates[:8]]


def _build_divergence_alerts(
    *,
    correlations: dict[str, Any],
    cross_links: list[dict[str, Any]],
    risk_gate: dict[str, Any],
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    seen_titles: set[str] = set()

    for p in (correlations or {}).get("pairs") or []:
        if not isinstance(p, dict):
            continue
        div_lbl = str(p.get("divergence") or "")
        r30 = _safe_float(p.get("corr_30d"))
        r90 = _safe_float(p.get("corr_90d"))
        delta = abs(r30 - r90) if (r30 is not None and r90 is not None) else 0.0
        if div_lbl in ("显著背离", "极值警戒") or delta >= 0.25:
            pair_name = str(p.get("pair") or "跨资产相关性")
            title = f"{pair_name} {div_lbl or '相关性变化'}"
            if title in seen_titles:
                continue
            seen_titles.add(title)
            tks = [t.strip().upper() for t in re.findall(r"\b(?:BTC|SP500|GOLD|US10Y|DOLLAR|VIX|WTI)\b", pair_name.upper())]
            if not tks:
                if "标普" in pair_name:
                    tks.append("SPY")
                if "黄金" in pair_name:
                    tks.append("GOLD")
                if "美债" in pair_name:
                    tks.append("US10Y")
            alerts.append(
                {
                    "title": title,
                    "detail": f"30日相关 {r30:+.2f} vs 90日相关 {r90:+.2f}（差值 {delta:.2f}），{p.get('note') or '注意跨资产联动切换'}。"
                    if (r30 is not None and r90 is not None)
                    else str(p.get("note") or "跨资产相关性结构发生变动。"),
                    "tickers": tks[:4],
                    "tone": "bear" if div_lbl == "极值警戒" else "warn",
                }
            )

    for link in cross_links:
        if link["pattern"] == "盘面与消息背离" and len(alerts) < 6:
            title = f"{link['focus']}：盘面与消息背离"
            if title in seen_titles:
                continue
            seen_titles.add(title)
            alerts.append(
                {
                    "title": title,
                    "detail": f"{link['conclusion']}（{link['market_check']}）",
                    "tickers": list(link.get("tickers") or [])[:4],
                    "tone": "warn",
                }
            )

    if not alerts:
        gate_lbl = str((risk_gate or {}).get("label") or "稳健风控").strip()
        alerts.append(
            {
                "title": "跨资产联动整体平稳",
                "detail": f"当前风险状态为「{gate_lbl}」，主要跨资产 30日与 90日相关性暂未出现极端背离。",
                "tickers": ["SPY", "BTC", "GOLD"],
                "tone": "neutral",
            }
        )
    return alerts[:6]


def build_market_synthesis(
    *,
    macro_fred: list[dict[str, Any]] | None,
    us_equities: dict[str, Any] | None,
    correlations: dict[str, Any] | None,
    signals: list[dict[str, Any]] | None,
    kol_digest: list[dict[str, Any]] | None,
    kol_scorecards: list[dict[str, Any]] | None,
    articles: list[dict[str, Any]] | None,
    risk_gate: dict[str, Any] | None,
    stock_profiles: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Synthesize macro, equities, correlations, news, KOL scorecards, and guru essays into market_analysis."""
    fred_by_id = _fred_map(macro_fred)
    eq_by_sym = _equities_map(us_equities)
    rg = risk_gate if isinstance(risk_gate, dict) else {}
    us_eq = us_equities if isinstance(us_equities, dict) else {}
    corrs = correlations if isinstance(correlations, dict) else {}
    profs = stock_profiles if isinstance(stock_profiles, dict) else {}

    dimensions = _build_four_dimensions(
        fred_by_id=fred_by_id,
        eq_by_sym=eq_by_sym,
        us_equities=us_eq,
        correlations=corrs,
        risk_gate=rg,
        stock_profiles=profs,
    )
    cross_links = _build_cross_source_links(
        signals=list(signals or []),
        kol_digest=list(kol_digest or []),
        kol_scorecards=list(kol_scorecards or []),
        articles=list(articles or []),
        eq_by_sym=eq_by_sym,
        fred_by_id=fred_by_id,
        stock_profiles=profs,
        correlations=corrs,
        risk_gate=rg,
    )
    divergence_alerts = _build_divergence_alerts(
        correlations=corrs,
        cross_links=cross_links,
        risk_gate=rg,
    )

    # Overall stance & headline_verdict
    gate_light = str(rg.get("light") or "YELLOW").upper()
    tone_scores = {"bull": 1, "neutral": 0, "warn": -0.5, "bear": -1.5}
    dim_score = sum(tone_scores.get(d["tone"], 0.0) for d in dimensions)
    bull_links = sum(1 for c in cross_links if c["pattern"] == "多源顺风共振")
    bear_links = sum(1 for c in cross_links if c["pattern"] == "空头风控共振")

    if gate_light == "RED" or dim_score <= -2.0 or bear_links >= bull_links + 2:
        stance = "偏空"
    elif gate_light == "GREEN" and dim_score >= 0.5 and bull_links >= bear_links:
        stance = "偏多"
    elif dim_score >= 1.5 and bull_links >= bear_links + 1:
        stance = "偏多"
    elif dim_score <= -1.2:
        stance = "偏空"
    else:
        stance = "中性"

    gate_label = str(rg.get("label") or {"GREEN": "绿灯顺风", "YELLOW": "黄灯控险", "RED": "红灯防守"}.get(gate_light, "稳健控险"))
    eq_dim = dimensions[2]
    rate_dim = dimensions[1]
    div_cnt = sum(1 for c in cross_links if c["pattern"] == "盘面与消息背离")
    res_cnt = bull_links + bear_links
    headline_verdict = (
        f"全局定调「{stance}」（{gate_label}）：{rate_dim['name']}「{rate_dim['status']}」，{eq_dim['name']}「{eq_dim['status']}」；"
        f"跨源跟踪发现 {res_cnt} 条多空共振主线与 {div_cnt} 处盘面消息背离，操作宜顺势控仓、等右侧确认。"
    )

    as_of = (
        str(us_eq.get("as_of") or "").strip()
        or str(corrs.get("as_of") or "").strip()
        or datetime.now(BJT).strftime("%Y-%m-%d %H:%M BJT")
    )

    result = {
        "as_of": as_of,
        "headline_verdict": headline_verdict,
        "stance": stance,
        "market_dimensions": dimensions,
        "cross_source_links": cross_links,
        "divergence_alerts": divergence_alerts,
    }
    return sanitize_public_value(result, scrub_keys=True, skip_keys=frozenset())
