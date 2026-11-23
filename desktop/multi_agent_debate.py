"""Five-role grounded multi-agent deep research & bull/bear debate engine (zero MongoDB/Redis)."""
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:
    requests = None  # type: ignore[assignment]

from backend.portfolio_store import PortfolioStore
from backend.public_snapshot import _atomic_write_text, file_lock, has_private_debate_text
from desktop.acp_bridge import AgyBridge
from desktop.openbb_bridge import query_openbb

COCKPIT_ROOT = Path(os.environ.get("MACROCOCKPIT_ROOT") or Path(__file__).resolve().parent.parent)
_BJT = timezone(timedelta(hours=8))
_MAX_DEBATES_HISTORY = 50
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


def _now_dt() -> datetime:
    return datetime.now(_BJT)


def _scrub(text: Any) -> str:
    s = str(text or "").strip()
    if not s:
        return ""
    for pat, rep in _FORBIDDEN_SCRUB:
        s = re.sub(pat, rep, s, flags=re.IGNORECASE)
    return re.sub(r"[ \t]{2,}", " ", s).strip()


def _read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _atomic_write_json(path: Path, payload: Any) -> None:
    _atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _normalize_symbol_and_market(symbol: str, market: str = "auto") -> tuple[str, str]:
    raw = str(symbol or "").strip().upper()
    if not raw or not re.fullmatch(r"[A-Z0-9.\-]{1,20}", raw) or ".." in raw:
        raise ValueError(f"标的代码格式无效: {symbol!r}")

    mkt = str(market or "auto").strip().lower()
    if mkt not in ("auto", "us", "crypto", "ashare", "hk"):
        raise ValueError(f"不支持的市场类型: {market!r}")

    if mkt == "auto":
        if raw in ("BTC", "ETH", "SOL", "BNB", "DOGE", "XRP", "ADA") or raw.endswith(("-USDT", "-USD", "-SWAP")):
            mkt = "crypto"
        elif re.fullmatch(r"\d{6}(?:\.(?:SH|SZ|SS))?", raw):
            mkt = "ashare"
        elif re.fullmatch(r"\d{4,5}(?:\.HK)?", raw) or raw.endswith(".HK"):
            mkt = "hk"
        else:
            mkt = "us"

    clean_sym = raw.split(".")[0] if mkt in ("ashare", "hk") else raw
    if mkt == "crypto" and "-" in clean_sym:
        clean_sym = clean_sym.split("-")[0]
    return (clean_sym, mkt)


def _fetch_tencent_cn_hk_quote(sym: str, market: str) -> dict[str, Any] | None:
    if requests is None:
        return None
    if market == "ashare":
        prefix = "sh" if sym.startswith(("5", "6", "9")) else "sz"
        q_code = f"{prefix}{sym}"
        ccy = "CNY"
    elif market == "hk":
        q_code = f"hk{sym.zfill(5)}"
        ccy = "HKD"
    else:
        return None
    try:
        resp = requests.get(f"https://qt.gtimg.cn/q={q_code}", timeout=4)
        if resp.status_code != 200:
            return None
        txt = resp.content.decode("gbk", errors="replace")
        m = re.search(r'="([^"]+)"', txt)
        if not m:
            return None
        parts = m.group(1).split("~")
        if len(parts) < 35:
            return None
        px = float(parts[3])
        if px <= 0:
            return None
        chg_pct = float(parts[32]) if parts[32] else 0.0
        pe = float(parts[39]) if len(parts) > 39 and parts[39] else None
        pb = float(parts[46]) if len(parts) > 46 and parts[46] else None
        hi_52 = float(parts[47]) if len(parts) > 47 and parts[47] else None
        lo_52 = float(parts[48]) if len(parts) > 48 and parts[48] else None
        return {
            "symbol": sym,
            "name_cn": parts[1].strip() or sym,
            "price": round(px, 3),
            "change_pct": round(chg_pct, 2),
            "currency": ccy,
            "pe_ttm": round(pe, 2) if pe and pe > 0 else None,
            "pb": round(pb, 2) if pb and pb > 0 else None,
            "high_52w": hi_52,
            "low_52w": lo_52,
            "source": "qt.gtimg.cn",
        }
    except Exception:
        return None


def _gather_grounded_facts(
    sym: str,
    market: str,
    *,
    root: Path,
    include_portfolio: bool,
) -> dict[str, Any]:
    state_path = root / "data" / "cockpit_state.json"
    with file_lock(state_path):
        state = _read_json(state_path, {}) or {}
    risk_gate = state.get("risk_gate") or {"light": "YELLOW", "label": "风控观察", "long_risk_cap": 0.5}

    # 1. Primary market profile/quote
    profile: dict[str, Any] | None = None
    cn_hk_quote: dict[str, Any] | None = None
    crypto_info: dict[str, Any] | None = None

    if market == "us":
        profile = (state.get("stock_profiles") or {}).get(sym)
        if profile is None:
            try:
                from backend.stock_research import fetch_single_stock_profile

                profile = fetch_single_stock_profile(sym)
            except Exception:
                profile = None
    elif market == "crypto":
        cq = state.get("crypto_quotes") or {}
        if not cq:
            try:
                from backend.market_quotes import fetch_crypto_quotes

                cq = fetch_crypto_quotes(symbols=[sym], timeout=3)
            except Exception:
                cq = {}
        spot_q = (cq.get("spot") or {}).get(f"{sym}-USDT") or (cq.get("quotes") or {}).get(sym) or {}
        swap_q = (cq.get("swap") or {}).get(f"{sym}-USDT-SWAP") or {}
        macro_btc = next((m for m in (state.get("macro_fred") or []) if m.get("id") == "btc_spot"), {})
        price_val = spot_q.get("price") or (macro_btc.get("raw_value") if sym == "BTC" else None)
        chg_val = spot_q.get("change_24h_pct") or (macro_btc.get("change_num") if sym == "BTC" else None)
        crypto_info = {
            "symbol": sym,
            "price": float(price_val) if price_val not in (None, "") else None,
            "change_pct": float(chg_val) if chg_val not in (None, "") else 0.0,
            "mark_price": swap_q.get("mark_price"),
            "funding_rate": swap_q.get("funding_rate"),
            "note": macro_btc.get("note", "") if sym == "BTC" else "",
        }
    else:
        cn_hk_quote = _fetch_tencent_cn_hk_quote(sym, market)

    # 2. OpenBB supplementary check (lazy/non-blocking)
    obb_ds = "crypto_quote" if market == "crypto" else "equity_quote"
    obb_probe = query_openbb(obb_ds, sym, allow_sdk_import=False, limit=10)

    # 3. Signals, KOLs, Calendar
    sig_pool = state.get("signal_candidates") or state.get("signals") or []
    matched_signals: list[dict[str, Any]] = []
    for s in sig_pool:
        if not isinstance(s, dict):
            continue
        tickers = [str(t).upper() for t in (s.get("tickers") or [])]
        txt = f"{s.get('title', '')} {s.get('summary', '')}".upper()
        if sym in tickers or sym in txt or (cn_hk_quote and cn_hk_quote.get("name_cn") and cn_hk_quote["name_cn"] in txt):
            matched_signals.append(s)
    matched_signals = matched_signals[:12]

    matched_kols: list[dict[str, Any]] = []
    for k in state.get("kol_digest") or []:
        if not isinstance(k, dict):
            continue
        txt = f"{k.get('headline', '')} {k.get('takeaway', '')}".upper()
        if sym in txt or (market == "crypto" and sym == "BTC" and ("比特币" in txt or "BTC" in txt)):
            matched_kols.append(k)

    matched_cal = [
        c
        for c in (state.get("calendar_events") or [])
        if isinstance(c, dict) and (sym in [str(t).upper() for t in (c.get("tickers") or [])] or sym in str(c.get("title") or "").upper())
    ][:5]

    # 4. Sanitized portfolio exposure % ONLY (STRICTLY ZERO absolute USD/CNY amounts!)
    portfolio_context = {
        "has_position": False,
        "exposure_summary": "未接入私人持仓或当前无该标的持仓",
        "weight_pct": None,
        "side": None,
    }
    if include_portfolio:
        db_file = root / "data" / "user_journal.db"
        if db_file.exists():
            try:
                store = PortfolioStore(db_file)
                ws = store.build_workspace(state, currency="USD")
                parts: list[str] = []
                total_w = 0.0
                side_found: str | None = None
                for sp in ws.get("spot_balances") or []:
                    if bool(sp.get("is_cash")):
                        continue
                    s_sym = str(sp.get("symbol") or sp.get("instrument_id") or "").upper()
                    if s_sym == sym:
                        wt = float(sp.get("weight_pct") or 0.0)
                        total_w += wt
                        side_found = "long"
                        parts.append(f"现货多头占净值 {wt:.1f}%")
                for dv in ws.get("derivative_positions") or []:
                    d_base = str(dv.get("symbol") or dv.get("instrument_id") or "").upper().split("-")[0]
                    if d_base == sym:
                        wt = float(dv.get("weight_pct") or 0.0)
                        d_side = str(dv.get("side") or "long").lower()
                        side_found = d_side
                        total_w += wt
                        lbl = "空头" if d_side == "short" else "多头"
                        parts.append(f"合约{lbl}名义占净值 {wt:.1f}%")
                if parts:
                    portfolio_context = {
                        "has_position": True,
                        "exposure_summary": "；".join(parts),
                        "weight_pct": round(total_w, 2),
                        "side": side_found,
                    }
            except Exception:
                pass

    return {
        "symbol": sym,
        "market": market,
        "risk_gate": risk_gate,
        "profile": profile,
        "cn_hk_quote": cn_hk_quote,
        "crypto_info": crypto_info,
        "obb_source": obb_probe.get("source"),
        "signals": matched_signals,
        "kol_digest": matched_kols,
        "calendar_events": matched_cal,
        "portfolio": portfolio_context,
    }


def _build_fast_five_roles(facts: dict[str, Any]) -> dict[str, Any]:
    sym = facts["symbol"]
    mkt = facts["market"]
    rg = facts["risk_gate"]
    rg_light = str(rg.get("light") or "YELLOW").upper()
    rg_cap = float(rg.get("long_risk_cap") if rg.get("long_risk_cap") is not None else 0.5)
    rg_label = _scrub(rg.get("label") or "风控谨慎")

    prof = facts.get("profile") or {}
    cn_hk = facts.get("cn_hk_quote") or {}
    cry = facts.get("crypto_info") or {}

    name = str(prof.get("name_cn") or cn_hk.get("name_cn") or sym)
    price = prof.get("price") or cn_hk.get("price") or cry.get("price")
    chg_pct = prof.get("change_pct") if prof.get("change_pct") is not None else (cn_hk.get("change_pct") if cn_hk.get("change_pct") is not None else cry.get("change_pct"))
    ma20 = prof.get("ma20")
    ma50 = prof.get("ma50")
    bias20 = prof.get("bias20_pct")
    bias50 = prof.get("bias50_pct")
    pos_52w = prof.get("range_52w_pos")
    checklist = prof.get("pre_trade_checklist") or {}
    chase_alert = str(checklist.get("chase_alert") or "normal")

    # 1. Technical Analyst
    tech_points: list[str] = []
    if price is not None:
        tech_points.append(f"最新价 {price}（日内 {float(chg_pct or 0.0):+.2f}%）")
    if bias20 is not None and ma20 is not None:
        tech_points.append(f"相对 20 日均线 ({ma20}) 乖离 {float(bias20):+.2f}%")
    if bias50 is not None and ma50 is not None:
        tech_points.append(f"相对 50 日均线 ({ma50}) 乖离 {float(bias50):+.2f}%")
    if pos_52w is not None:
        tech_points.append(f"52 周区间分位 {float(pos_52w):.1f}%")
    if mkt == "crypto" and cry.get("funding_rate") not in (None, ""):
        fr_pct = float(cry["funding_rate"]) * 100.0
        tech_points.append(f"永续资金费率 {fr_pct:+.4f}%")
    if not tech_points:
        tech_points.append("公开均线数据有限，需结合日线支撑位确认突破有效性")

    tech_bias_label = "温和偏强"
    if bias20 is not None and float(bias20) > 8.0:
        tech_bias_label = "短线过热（追高警戒）"
    elif bias20 is not None and float(bias20) < -8.0:
        tech_bias_label = "短线深幅回撤（防接飞刀）"
    elif bias50 is not None and float(bias50) < 0:
        tech_bias_label = "50日均线下方偏弱"

    technical_analyst = {
        "role": "技术与乖离分析师",
        "headline": f"{name} ({sym}) · {tech_bias_label}",
        "stance": tech_bias_label,
        "price": price,
        "change_pct": chg_pct,
        "ma20": ma20,
        "ma50": ma50,
        "bias20_pct": bias20,
        "bias50_pct": bias50,
        "range_52w_pos": pos_52w,
        "chase_alert": chase_alert,
        "points": tech_points,
        "summary": "；".join(tech_points[:3]),
    }

    # 2. Fundamental Analyst
    ov = prof.get("overview") or {}
    val = prof.get("valuation") or {}
    fin = prof.get("financials") or {}
    health = prof.get("health") or {}
    filings = prof.get("sec_filings") or []
    fund_points: list[str] = []
    pe_ttm = val.get("pe_ttm") or cn_hk.get("pe_ttm")
    pe_fwd = val.get("pe_forward")
    pb_val = val.get("pb") or cn_hk.get("pb")
    if pe_ttm is not None:
        fund_points.append(f"市盈率 PE(TTM) {pe_ttm}" + (f" / 前瞻 PE {pe_fwd}" if pe_fwd is not None else ""))
    if pb_val is not None:
        fund_points.append(f"市净率 PB {pb_val}")
    if fin.get("rev_growth_yoy") not in (None, "", "—"):
        fund_points.append(f"营收同比 {fin['rev_growth_yoy']} · 毛利率 {fin.get('gross_margin', '—')} · ROE {fin.get('roe', '—')}")
    if health.get("score") is not None:
        fund_points.append(f"财务健康评分 {health['score']}/10")
    if filings:
        latest_f = filings[0]
        fund_points.append(f"最新 SEC 披露：{latest_f.get('form')}（{latest_f.get('filing_date')}）")
    if not fund_points:
        fund_points.append("宏观流动性与板块相对强弱为核心定价锚，持续跟踪主流资金费率与成交量变化")

    fund_stance = "稳健" if (health.get("score") or 0) >= 7 else ("谨慎" if pe_ttm and float(pe_ttm) > 45 else "中性")
    fundamental_analyst = {
        "role": "基本面与披露分析师",
        "headline": f"{name} 基本面与监管披露研判",
        "stance": fund_stance,
        "health_score": health.get("score"),
        "pe_ttm": pe_ttm,
        "pe_forward": pe_fwd,
        "sec_filings_count": len(filings),
        "points": fund_points,
        "summary": "；".join(fund_points[:3]),
    }

    # 3. Sentiment Analyst
    sigs = facts["signals"]
    kols = facts["kol_digest"]
    cals = facts["calendar_events"]
    bull_sig = sum(1 for s in sigs if str(s.get("direction") or "").lower() == "bullish")
    bear_sig = sum(1 for s in sigs if str(s.get("direction") or "").lower() == "bearish")
    sent_points = [f"近期站内关联快讯 {len(sigs)} 条（偏多 {bull_sig} / 偏空 {bear_sig}）"]
    for s in sigs[:2]:
        sent_points.append(f"[{s.get('source', '快讯')}] {_scrub(s.get('title', ''))}")
    if kols:
        sent_points.append(f"KOL 提及 {len(kols)} 条（首条：{kols[0].get('handle')} {kols[0].get('stance')}）")
    if cals:
        sent_points.append(f"临近事件：{cals[0].get('title')}（{cals[0].get('time_bjt') or cals[0].get('date_local')}）")

    sent_stance = "偏多" if bull_sig > bear_sig else ("偏空" if bear_sig > bull_sig else "中性")
    sentiment_analyst = {
        "role": "快讯与情绪分析师",
        "headline": f"情绪面汇总（多 {bull_sig} vs 空 {bear_sig}）",
        "stance": sent_stance,
        "signals_count": len(sigs),
        "bull_signals": bull_sig,
        "bear_signals": bear_sig,
        "kol_count": len(kols),
        "calendar_count": len(cals),
        "points": sent_points,
        "summary": "；".join(sent_points[:3]),
    }

    # 4. Bull vs Bear Debate + Rebuttal
    bull_case: list[str] = []
    bear_case: list[str] = []

    if health.get("score") is not None and int(health["score"]) >= 7:
        bull_case.append(f"财务基本面稳健（健康评分 {health['score']}/10），抗回撤能力较强")
    if ov.get("target_mean_price") and price and float(ov["target_mean_price"]) > float(price):
        upside = (float(ov["target_mean_price"]) - float(price)) / float(price) * 100.0
        bull_case.append(f"华尔街一致目标价较现价有约 +{upside:.1f}% 的潜在空间")
    if bias50 is not None and float(bias50) > 0:
        bull_case.append(f"价格站稳 50 日均线上方（+{float(bias50):.1f}%），中期结构维持多头趋势")
    if bull_sig > bear_sig:
        bull_case.append(f"近期核心快讯情绪偏多（{bull_sig} 多 vs {bear_sig} 空），消息面形成正反馈")
    if not bull_case:
        bull_case.append(f"若放量收复关键均线并配合宏观门控改善，{sym} 具备波段修复弹性")

    if rg_light != "GREEN":
        bear_case.append(f"当前宏观风控门控为「{rg_label}」（多头上限 {rg_cap:.1f}x），限制单边加仓空间")
    if bias20 is not None and float(bias20) > 8.0:
        bear_case.append(f"20 日均线乖离率达 {float(bias20):+.1f}%（高于 +8% 警示线），短期追高回撤赔率不佳")
    elif bias20 is not None and float(bias20) < -5.0:
        bear_case.append(f"20 日均线乖离 {float(bias20):+.1f}% 处于弱势区，未现右侧止跌前严禁左侧接飞刀")
    if pe_ttm is not None and float(pe_ttm) > 40:
        bear_case.append(f"PE(TTM) 约 {pe_ttm} 倍处于较高估值水位，对利率与业绩增速高度敏感")
    if bear_sig > bull_sig:
        bear_case.append(f"近期负面/偏空消息占优（{bear_sig} 空 vs {bull_sig} 多），短期抛压仍待消化")
    if not bear_case:
        bear_case.append("若宏观流动性收紧或跌破 50 日均线支撑，需按纪律执行止损减仓")

    rebuttal = f"综合质询：{bull_case[0]}；但同时必须正视「{bear_case[0]}」。"
    debate = {
        "bull_case": bull_case[:3],
        "bear_case": bear_case[:3],
        "rebuttal": rebuttal,
    }

    # 5. Trader & Risk Verdict (strictly % hints, NO absolute amounts)
    score = 52
    if health.get("score") is not None:
        score += (int(health["score"]) - 5) * 4
    if bias50 is not None:
        score += 8 if float(bias50) > 0 else -8
    if bull_sig > bear_sig:
        score += 6
    elif bear_sig > bull_sig:
        score -= 6
    if rg_light == "RED":
        score -= 15
    elif rg_light == "YELLOW":
        score -= 5
    if bias20 is not None and float(bias20) > 8.0:
        score -= 8
    confidence = max(15, min(95, int(round(score))))

    checklist_passed = True
    if rg_light == "RED" or (bias20 is not None and (float(bias20) > 8.0 or float(bias20) < -8.0)):
        checklist_passed = False

    if bias20 is not None and float(bias20) > 8.0:
        stance = "追高警示"
        cap_hint = f"禁止追高，仅保留存量观察或将仓位控制在 {min(5, int(round(rg_cap * 10)))}% 以内"
    elif rg_light == "RED" or (bear_sig > bull_sig and (bias50 is None or float(bias50) < 0)):
        stance = "偏空逆风"
        cap_hint = "防御观望为主，多头新开仓上限 0%，防左侧接飞刀"
    elif rg_light == "GREEN" and confidence >= 65 and (bias50 is None or float(bias50) >= 0):
        stance = "偏多顺风"
        cap_hint = f"可按计划右侧跟进，单标的仓位上限 {int(round(rg_cap * 15))}%"
    else:
        stance = "中性观望"
        cap_hint = f"等待均线与情绪共振，单标的试探仓位上限 {max(3, int(round(rg_cap * 10)))}%"

    invalidation_rule = (
        f"日线收盘跌破 50 日均线 ({ma50}) 或宏观风控门控转红灯时立即减仓/止损"
        if ma50 is not None
        else "突破前低支撑失效或宏观风险门控收紧时触发离场纪律"
    )

    trader_verdict = {
        "role": "交易员与风控裁决",
        "stance": stance,
        "confidence": confidence,
        "position_cap_hint": cap_hint,
        "invalidation_rule": invalidation_rule,
        "checklist_passed": checklist_passed,
        "summary": f"最终裁决：{stance}（置信度 {confidence}/100）。{cap_hint}；失效条件：{invalidation_rule}。",
    }

    return {
        "technical_analyst": technical_analyst,
        "fundamental_analyst": fundamental_analyst,
        "sentiment_analyst": sentiment_analyst,
        "debate": debate,
        "trader_verdict": trader_verdict,
    }


def _extract_json_block(raw_text: str) -> dict[str, Any] | None:
    txt = str(raw_text or "").strip()
    if not txt:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", txt, re.DOTALL | re.IGNORECASE)
    cand = fenced.group(1) if fenced else txt
    if not fenced:
        start = cand.find("{")
        end = cand.rfind("}")
        if start != -1 and end > start:
            cand = cand[start : end + 1]
    try:
        obj = json.loads(cand)
        return obj if isinstance(obj, dict) else None
    except ValueError:
        return None


def _public_report_text(value: Any) -> str:
    text = _scrub(value)
    return "参考公开数据与风险纪律。" if has_private_debate_text(text) else text


def _render_debate_markdown(debate_doc: dict[str, Any]) -> str:
    sym = _public_report_text(debate_doc["symbol"])
    tv = debate_doc["trader_verdict"]
    stance = _public_report_text(tv["stance"])
    tech = debate_doc["technical_analyst"]
    fund = debate_doc["fundamental_analyst"]
    sent = debate_doc["sentiment_analyst"]
    deb = debate_doc["debate"]

    title = f"{sym} 多智能体多空深研：{stance}（置信度 {tv['confidence']}）"
    title = _public_report_text(title)
    summary = _public_report_text(tv["summary"])
    fm = [
        "---",
        f"title: {json.dumps(title, ensure_ascii=False)}",
        f"summary: {json.dumps(summary, ensure_ascii=False)}",
        'publisher: "多智能体深研"',
        f'symbol: "{sym}"',
        f'stance: "{stance}"',
        f"score: {int(tv['confidence'])}",
        "---",
        "",
    ]

    lines = [
        f"# {title}",
        "",
        f"- **生成时间**：{debate_doc['created_at']}",
        f"- **推理模式**：{debate_doc['effective_mode']}"
        + ("（大模型模式已降级）" if debate_doc.get("degraded_from_llm") else ""),
        f"- **风险门控**：{_public_report_text(debate_doc['facts_summary']['risk_gate_label'])}",
        "",
        "## 一、三路专家研判",
        "",
        f"### 1. {tech['role']}",
        *[f"- {_public_report_text(p)}" for p in tech.get("points") or []],
        "",
        f"### 2. {fund['role']}",
        *[f"- {_public_report_text(p)}" for p in fund.get("points") or []],
        "",
        f"### 3. {sent['role']}",
        *[f"- {_public_report_text(p)}" for p in sent.get("points") or []],
        "",
        "## 二、多空对抗辩论",
        "",
        "**看多论据**：",
        *[f"- {_public_report_text(x)}" for x in deb.get("bull_case") or []],
        "",
        "**看空与纪律反驳**：",
        *[f"- {_public_report_text(x)}" for x in deb.get("bear_case") or []],
        "",
        f"**交叉质询**：{_public_report_text(deb.get('rebuttal', ''))}",
        "",
        "## 三、交易员与风控裁决",
        "",
        f"- **姿态结论**：{stance}（置信度 {tv['confidence']}/100）",
        f"- **仓位上限建议**：{_public_report_text(tv['position_cap_hint'])}",
        f"- **失效止损纪律**：{_public_report_text(tv['invalidation_rule'])}",
        f"- **纪律检查**：{'通过' if tv.get('checklist_passed') else '触发风控提示'}",
        "",
    ]
    cleaned_lines = [_scrub(line) if line else "" for line in (fm + lines)]
    return "\n".join(cleaned_lines) + "\n"


def list_debates(base_dir: Path | str = COCKPIT_ROOT) -> list[dict[str, Any]]:
    root = Path(base_dir)
    history_path = root / "data" / "desktop_debates.json"
    if not history_path.exists():
        return []
    with file_lock(history_path):
        rows = _read_json(history_path, [])
    return rows if isinstance(rows, list) else []


def get_debate(debate_id: str, base_dir: Path | str = COCKPIT_ROOT) -> dict[str, Any] | None:
    for row in list_debates(base_dir):
        if isinstance(row, dict) and str(row.get("id") or "") == str(debate_id):
            return row
    return None


def _sync_debate_to_web(
    *,
    root: Path,
    sym: str,
    report_filename: str,
    report_md: str,
    debate_doc: dict[str, Any],
) -> dict[str, Any]:
    state_path = root / "data" / "cockpit_state.json"
    with file_lock(state_path):
        raw_state = _read_json(state_path, {}) or {}
        if isinstance(raw_state, dict) and raw_state:
            articles = [
                a for a in (raw_state.get("articles") or []) if isinstance(a, dict) and a.get("filename") != report_filename
            ]
            tv = debate_doc["trader_verdict"]
            art_entry = {
                "filename": report_filename,
                "title": _public_report_text(f"{sym} 多智能体多空深研：{tv['stance']}（置信度 {tv['confidence']}）"),
                "source": "多智能体深研",
                "publisher": "多智能体深研",
                "mtime": _now_dt().strftime("%Y-%m-%d %H:%M"),
                "excerpt": _public_report_text(tv["summary"])[:180],
                "body_md": report_md,
                "score": int(tv["confidence"]),
            }
            raw_state["articles"] = [art_entry, *articles]
            profiles = raw_state.get("stock_profiles")
            if isinstance(profiles, dict) and sym in profiles and isinstance(profiles[sym], dict):
                profiles[sym]["deep_report_file"] = report_filename
            _atomic_write_json(state_path, raw_state)

    try:
        from backend import vps_sync

        vps_sync.load_push_env(include_sync_token=True)
        payload, stats = vps_sync.collect_payload(state_path, max_articles=40)
        if not any(a.get("filename") == report_filename for a in payload.get("articles") or []):
            payload.setdefault("articles", []).insert(0, {"filename": report_filename, "content": report_md})
        ok, detail = vps_sync.publish_via_http(payload)
        return {"ok": ok, "local_state_updated": True, "stats": stats, **detail}
    except Exception as exc:
        return {"ok": False, "local_state_updated": True, "reason": f"{type(exc).__name__}: {exc}"}


def run_multi_agent_debate(
    symbol: str,
    *,
    market: str = "auto",
    mode: str = "fast",
    base_dir: Path | str = COCKPIT_ROOT,
    include_portfolio: bool = False,
    sync_to_web: bool = False,
) -> dict[str, Any]:
    """Run 5-role grounded debate, persist JSON + public markdown report, and optionally sync to VPS."""
    root = Path(base_dir)
    sym, resolved_mkt = _normalize_symbol_and_market(symbol, market)
    req_mode = str(mode or "fast").strip().lower()
    if req_mode not in ("fast", "llm"):
        raise ValueError(f"unsupported debate mode: {mode!r}")

    facts = _gather_grounded_facts(sym, resolved_mkt, root=root, include_portfolio=include_portfolio)
    roles_out = _build_fast_five_roles(facts)
    effective_mode = "fast"
    degraded_from_llm = False
    degrade_reason = ""

    if req_mode == "llm":
        grounded_brief = {
            "symbol": sym,
            "market": resolved_mkt,
            "risk_gate": {
                key: facts["risk_gate"].get(key) for key in ("light", "label", "long_risk_cap")
            },
            "technical": roles_out["technical_analyst"],
            "fundamental": roles_out["fundamental_analyst"],
            "sentiment": roles_out["sentiment_analyst"],
        }
        brief_json = json.dumps(grounded_brief, ensure_ascii=False)
        prompt = (
            "你是专业投研多智能体辩论裁决组。请严格仅基于以下实测事实 JSON（严禁捏造价格、财报数字或绝对美元/人民币仓位金额），"
            "以纯中文输出一个 JSON 对象，包含 bull_case（数组2-3条）、bear_case（数组2-3条）、rebuttal（字符串）、"
            "stance（仅限 偏多顺风 / 中性观望 / 偏空逆风 / 追高警示）、confidence（0-100整数）、"
            "position_cap_hint（仅百分比建议）、invalidation_rule（字符串）、summary（一句话结论）：\n"
            + brief_json
        )
        if has_private_debate_text(brief_json):
            agy_res = {"ok": False, "error": "输入含敏感内容，已自动降级极速量化推演"}
        else:
            agy_res = AgyBridge(cwd=root).ask_agy(prompt, transport="acp", timeout=75)
        parsed = _extract_json_block(agy_res.get("reply") or "") if agy_res.get("ok") else None
        text_fields = ("rebuttal", "position_cap_hint", "invalidation_rule", "summary", "stance")
        if parsed:
            cases = [parsed.get("bull_case"), parsed.get("bear_case")]
            texts = [parsed.get(key, "") for key in text_fields]
            if not all(isinstance(case, list) for case in cases):
                parsed = None
            else:
                texts.extend([*cases[0], *cases[1]])
                if not all(isinstance(text, str) for text in texts):
                    parsed = None
                elif any(has_private_debate_text(text) for text in texts) or has_private_debate_text(
                    json.dumps(parsed, ensure_ascii=False)
                ):
                    parsed = None
                    agy_res = {"error": "大模型输出含敏感内容，已自动降级极速量化推演"}
        if parsed:
            effective_mode = "llm"
            roles_out["debate"] = {
                "bull_case": [_scrub(x) for x in parsed["bull_case"][:3] if x],
                "bear_case": [_scrub(x) for x in parsed["bear_case"][:3] if x],
                "rebuttal": _scrub(parsed.get("rebuttal") or roles_out["debate"]["rebuttal"]),
            }
            stance_cand = _scrub(parsed.get("stance") or "")
            if stance_cand in ("偏多顺风", "中性观望", "偏空逆风", "追高警示"):
                roles_out["trader_verdict"]["stance"] = stance_cand
            try:
                conf = int(parsed.get("confidence", roles_out["trader_verdict"]["confidence"]))
                roles_out["trader_verdict"]["confidence"] = max(0, min(100, conf))
            except (TypeError, ValueError, OverflowError):
                pass
            if parsed.get("position_cap_hint"):
                roles_out["trader_verdict"]["position_cap_hint"] = _scrub(parsed["position_cap_hint"])
            if parsed.get("invalidation_rule"):
                roles_out["trader_verdict"]["invalidation_rule"] = _scrub(parsed["invalidation_rule"])
            if parsed.get("summary"):
                roles_out["trader_verdict"]["summary"] = _scrub(parsed["summary"])
        else:
            degraded_from_llm = True
            degrade_reason = str(agy_res.get("error") or "大模型输出未返回有效 JSON，已自动降级极速量化推演")

    now_dt = _now_dt()
    safe_sym = re.sub(r"[^A-Z0-9]", "", sym) or "ASSET"
    report_key = f"{now_dt:%Y%m%d_%H%M%S}_{uuid.uuid4().hex}"
    report_filename = f"ta_{safe_sym}_{report_key}.md"
    debate_id = f"deb_{report_key}"

    debate_doc: dict[str, Any] = {
        "id": debate_id,
        "symbol": sym,
        "name": str((facts.get("profile") or {}).get("name_cn") or (facts.get("cn_hk_quote") or {}).get("name_cn") or sym),
        "market": resolved_mkt,
        "mode": req_mode,
        "effective_mode": effective_mode,
        "degraded_from_llm": degraded_from_llm,
        "degrade_reason": degrade_reason,
        "fallback_note": degrade_reason,
        "created_at": now_dt.isoformat(timespec="seconds"),
        "report_file": report_filename,
        "facts_summary": {
            "obb_source": facts.get("obb_source"),
            "signals_matched": len(facts["signals"]),
            "kol_matched": len(facts["kol_digest"]),
            "risk_gate_light": facts["risk_gate"].get("light"),
            "risk_gate_label": _scrub(facts["risk_gate"].get("label") or ""),
            "portfolio_exposure": facts["portfolio"]["exposure_summary"],
        },
        **roles_out,
    }

    report_md = _render_debate_markdown(debate_doc)
    history_path = root / "data" / "desktop_debates.json"
    with file_lock(history_path):
        _atomic_write_text(root / "articles" / report_filename, report_md)
        rows = _read_json(history_path, [])
        history = [d for d in rows if isinstance(d, dict)] if isinstance(rows, list) else []
        _atomic_write_json(history_path, [debate_doc, *history][:_MAX_DEBATES_HISTORY])

    if sync_to_web:
        debate_doc["sync_result"] = _sync_debate_to_web(
            root=root,
            sym=sym,
            report_filename=report_filename,
            report_md=report_md,
            debate_doc=debate_doc,
        )
    else:
        debate_doc["sync_result"] = {"ok": False, "skipped": True}

    return debate_doc
