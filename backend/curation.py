"""Personal signal curation, holding radar, and bounded safe rule validation."""
from __future__ import annotations

import math
import re
from typing import Any

from backend.guardrails import check_safe_regex as _check_safe_regex
from backend.runtime import clamp as _clamp
from backend.kazike_engine import ROUNDUP_RE

ALLOWED_RULE_KINDS: frozenset[str] = frozenset(
    {"instrument", "sector", "keyword", "regex", "topic", "source"}
)
ALLOWED_RULE_ACTIONS: frozenset[str] = frozenset(
    {"watch", "block", "mute", "adjust"}
)
MAX_RULES_COUNT = 100

def validate_curation_rule(
    kind: str,
    target: str,
    action: str,
    points: float | int | None = 0,
    *,
    existing_count: int = 0,
) -> dict[str, Any]:
    """Validate and normalize a single personal curation rule."""
    if existing_count >= MAX_RULES_COUNT:
        raise ValueError(f"个人筛选规则数量已达上限 ({MAX_RULES_COUNT})")

    k = str(kind or "").strip().lower()
    if k not in ALLOWED_RULE_KINDS:
        raise ValueError(f"不支持的规则类型: {kind}")

    a = str(action or "").strip().lower()
    if a not in ALLOWED_RULE_ACTIONS:
        raise ValueError(f"不支持的规则动作: {action}")

    tgt = str(target or "").strip()
    if not tgt or len(tgt) > 120:
        raise ValueError("规则目标不能为空且长度不得超过 120 字符")

    if k == "regex":
        _check_safe_regex(tgt)
    elif k == "instrument":
        tgt = tgt.upper()

    if points is None:
        pts = 8.0 if a == "mute" else 0.0
    else:
        if isinstance(points, bool):
            raise ValueError("分值参数不能为布尔类型")
        try:
            pts = float(points)
        except (TypeError, ValueError) as exc:
            raise ValueError("分值参数须为合法数字") from exc
        if not math.isfinite(pts):
            raise ValueError("分值参数须为有限数字")

    if a == "adjust" and not (-12.0 <= pts <= 3.0):
        raise ValueError("调分规则分值 S 须在 [-12, 3] 范围内")
    if a == "mute" and not (0.0 <= pts <= 15.0):
        raise ValueError("降权屏蔽分值 D 须在 [0, 15] 范围内")
    if a in ("watch", "block"):
        pts = 0.0

    return {
        "kind": k,
        "target": tgt,
        "action": a,
        "points": round(pts, 2),
    }


def _compile_rules(rules: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    compiled: list[dict[str, Any]] = []
    for raw in (rules or [])[:MAX_RULES_COUNT]:
        if not isinstance(raw, dict):
            continue
        if not raw.get("enabled", True):
            continue
        try:
            norm = validate_curation_rule(
                str(raw.get("kind") or ""),
                str(raw.get("target") or ""),
                str(raw.get("action") or ""),
                raw.get("points"),
            )
        except ValueError:
            continue
        if norm["kind"] == "regex":
            norm["_compiled"] = _check_safe_regex(norm["target"])
        compiled.append(norm)
    return compiled


_INSTRUMENT_ALIASES: dict[str, tuple[str, ...]] = {
    "NVDA": ("英伟达", "nvidia"),
    "AAPL": ("苹果", "apple"),
    "MSFT": ("微软", "microsoft"),
    "GOOG": ("谷歌", "google", "alphabet", "deepmind"),
    "GOOGL": ("谷歌", "google", "alphabet", "deepmind"),
    "AMZN": ("亚马逊", "amazon", "aws"),
    "META": ("meta", "扎克伯格", "llama"),
    "TSLA": ("特斯拉", "tesla", "马斯克"),
    "AMD": ("超威", "苏姿丰"),
    "TSM": ("台积电", "tsmc"),
    "AVGO": ("博通", "broadcom"),
    "INTC": ("英特尔", "intel"),
    "MU": ("美光", "micron"),
    "SMCI": ("超微电脑",),
    "MSTR": ("microstrategy",),
    "COIN": ("coinbase",),
    "PLTR": ("palantir",),
    "BABA": ("阿里巴巴", "阿里", "阿里云", "通义"),
    "9988": ("阿里巴巴", "阿里", "阿里云", "通义", "baba"),
    "09988": ("阿里巴巴", "阿里", "阿里云", "通义", "baba"),
    "TCEHY": ("腾讯", "微信"),
    "0700": ("腾讯", "微信", "tcehy"),
    "00700": ("腾讯", "微信", "tcehy"),
    "PDD": ("拼多多", "temu"),
    "JD": ("京东",),
    "9618": ("京东", "jd"),
    "09618": ("京东", "jd"),
    "BIDU": ("百度",),
    "9888": ("百度", "bidu"),
    "09888": ("百度", "bidu"),
    "BYDDY": ("比亚迪", "byd"),
    "002594": ("比亚迪", "byd"),
    "1211": ("比亚迪", "byd"),
    "01211": ("比亚迪", "byd"),
    "XIACY": ("小米",),
    "1810": ("小米",),
    "01810": ("小米",),
    "SMICY": ("中芯国际", "中芯"),
    "688981": ("中芯国际", "中芯"),
    "0981": ("中芯国际", "中芯"),
    "00981": ("中芯国际", "中芯"),
    "688256": ("寒武纪",),
    "600519": ("贵州茅台", "茅台"),
    "300750": ("宁德时代",),
    "BTC": ("比特币", "bitcoin"),
    "BTCUSDT": ("比特币", "bitcoin", "btc"),
    "BNB": ("币安", "binance"),
    "ETH": ("以太坊", "ethereum"),
    "SOL": ("solana",),
    "XAU": ("黄金", "现货黄金", "金价"),
    "PAXG": ("黄金", "现货黄金", "伦敦金", "金价", "gold", "xau", "gld"),
    "GLD": ("黄金", "现货黄金", "金价"),
    "IAU": ("黄金", "现货黄金", "金价"),
    "GC": ("黄金", "现货黄金", "金价"),
    "QQQ": ("纳指", "纳斯达克", "纳斯达克100", "nasdaq", "ndx"),
    "QQQUSDT": ("qqq", "纳指", "纳斯达克", "纳斯达克100", "nasdaq", "ndx"),
    "ZHIPU": ("智谱", "智谱ai", "2513.hk", "02513", "glm", "chatglm", "autoglm"),
    "ZHIPUUSDT": ("智谱", "智谱ai", "zhipu", "2513.hk", "02513", "glm", "chatglm", "autoglm"),
    "2513": ("智谱", "智谱ai", "zhipu", "glm", "chatglm", "autoglm"),
}

_GENERIC_ALIAS_STOPWORDS: frozenset[str] = frozenset(
    {"", "主账户", "现货", "永续", "合约", "现金", "稳定币", "股票", "美股", "港股", "a股", "商品", "黄金", "其他"}
)


def _extract_workspace_holdings(workspace: dict[str, Any] | None) -> tuple[dict[str, dict[str, Any]], set[str], bool]:
    """Extract per-instrument exposure tier, direction, aliases, sectors, and net equity validity."""
    if not isinstance(workspace, dict) or not workspace:
        return ({}, set(), False)

    # Locate net equity USD cleanly across possible workspace layouts
    equity_raw = None
    for container in (workspace, workspace.get("summary") or {}, workspace.get("valuation") or {}):
        if isinstance(container, dict):
            for key in ("net_equity_usd", "equity_usd", "net_equity"):
                if container.get(key) is not None:
                    equity_raw = container.get(key)
                    break
        if equity_raw is not None:
            break

    equity_val: float | None = None
    if equity_raw is not None:
        try:
            ef = float(equity_raw)
            if math.isfinite(ef) and ef > 0:
                equity_val = ef
        except (TypeError, ValueError):
            equity_val = None
    equity_valid = equity_val is not None and equity_val > 0

    holdings_agg: dict[str, dict[str, Any]] = {}
    held_sectors: set[str] = set()

    def _add_holding(
        sym: str,
        notional_abs: float,
        pct_of_eq: float | None,
        direction: str,
        sector: str = "",
        extra_name: str = "",
    ) -> None:
        s = sym.strip().upper().split(".")[0]
        if not s or s in ("USD", "USDT", "USDC", "CNY", "CNH", "HKD", "CASH"):
            return
        if sector:
            held_sectors.add(sector.strip().lower())
        cell = holdings_agg.setdefault(s, {"notional_abs": 0.0, "pct_of_eq": 0.0, "dirs": set(), "aliases": set()})
        cell["notional_abs"] += max(0.0, notional_abs)
        if pct_of_eq is not None and math.isfinite(pct_of_eq):
            cell["pct_of_eq"] += abs(pct_of_eq)
        if direction in ("long", "short"):
            cell["dirs"].add(direction)
        alias_clean = str(extra_name or "").strip()
        if alias_clean and alias_clean.upper() != s and alias_clean.lower() not in _GENERIC_ALIAS_STOPWORDS and len(alias_clean) >= 2:
            cell["aliases"].add(alias_clean.lower())

    # Generic positions list
    for pos in workspace.get("positions") or []:
        if not isinstance(pos, dict):
            continue
        sym = str(pos.get("symbol") or pos.get("instrument_id") or "").split("-")[0]
        is_cash = bool(pos.get("is_cash")) or sym.upper() in ("USD", "USDT", "USDC", "CASH")
        if is_cash:
            continue
        notional_raw = pos.get("market_value_usd")
        if notional_raw is None:
            notional_raw = pos.get("notional_usd")
        try:
            notional = abs(float(notional_raw or 0.0))
        except (TypeError, ValueError):
            notional = 0.0
        pct_raw = pos.get("pct_of_equity")
        if pct_raw is None:
            pct_raw = pos.get("weight_pct")
        try:
            pct_val = abs(float(pct_raw)) if pct_raw is not None else None
        except (TypeError, ValueError):
            pct_val = None
        side = str(pos.get("direction") or pos.get("side") or "long").strip().lower()
        if side in ("buy", "long", "多"):
            side = "long"
        elif side in ("sell", "short", "空"):
            side = "short"
        else:
            side = "long"
        _add_holding(sym, notional, pct_val, side, str(pos.get("sector") or ""), str(pos.get("name") or ""))

    # Spot balances
    for sp in workspace.get("spot_balances") or []:
        if not isinstance(sp, dict):
            continue
        sym = str(sp.get("symbol") or sp.get("instrument_id") or "").split("-")[0]
        if bool(sp.get("is_cash")) or sym.upper() in ("USD", "USDT", "USDC", "CASH"):
            continue
        try:
            qty = float(sp.get("qty") or sp.get("quantity") or 0.0)
            mv = float(sp.get("market_value_usd") or 0.0)
        except (TypeError, ValueError):
            qty, mv = 0.0, 0.0
        if qty == 0 and mv == 0:
            continue
        try:
            pct_val = abs(float(sp["pct_of_equity"])) if sp.get("pct_of_equity") is not None else None
        except (TypeError, ValueError):
            pct_val = None
        _add_holding(sym, abs(mv), pct_val, "long", str(sp.get("sector") or ""), str(sp.get("name") or ""))

    # Derivative positions
    for dv in workspace.get("derivative_positions") or []:
        if not isinstance(dv, dict):
            continue
        sym = str(dv.get("symbol") or dv.get("instrument_id") or "").split("-")[0]
        try:
            qty = float(dv.get("qty") or dv.get("quantity") or 0.0)
            notional = abs(float(dv.get("notional_usd") or 0.0))
        except (TypeError, ValueError):
            qty, notional = 0.0, 0.0
        if qty == 0 and notional == 0:
            continue
        side_raw = str(dv.get("side") or dv.get("direction") or ("short" if qty < 0 else "long")).lower()
        side = "short" if side_raw in ("short", "sell", "空") or qty < 0 else "long"
        try:
            pct_val = abs(float(dv["pct_of_equity"])) if dv.get("pct_of_equity") is not None else None
        except (TypeError, ValueError):
            pct_val = None
        _add_holding(sym, notional, pct_val, side, str(dv.get("sector") or ""), str(dv.get("name") or ""))

    resolved: dict[str, dict[str, Any]] = {}
    for sym, info in holdings_agg.items():
        if not equity_valid:
            tier_pts = 12.0
            exp_pct = 0.0
        else:
            exp_pct = info["pct_of_eq"] if info["pct_of_eq"] > 0 else (info["notional_abs"] / equity_val * 100.0)
            if exp_pct >= 15.0:
                tier_pts = 20.0
            elif exp_pct >= 5.0:
                tier_pts = 16.0
            else:
                tier_pts = 12.0
        dirs = info["dirs"]
        if len(dirs) > 1:
            direction = "mixed"
        elif dirs:
            direction = next(iter(dirs))
        else:
            direction = "long"
        resolved[sym] = {
            "w": tier_pts,
            "pct": round(exp_pct, 2),
            "direction": direction,
            "aliases": tuple(sorted(info["aliases"])),
        }

    return (resolved, held_sectors, equity_valid)


def _match_instrument_c(
    sym: str,
    item: dict[str, Any],
    extra_aliases: tuple[str, ...] | list[str] | None = None,
) -> float:
    """Return relevance c_i in {0.0, 0.5, 1.0} for an instrument ticker or bilingual alias against a signal."""
    s_up = sym.strip().upper().split(".")[0]
    if not s_up:
        return 0.0
    tickers = {str(t).upper() for t in (item.get("tickers") or []) if t}
    if s_up in tickers:
        return 1.0
    if s_up in ("QQQ", "QQQUSDT", "SPY", "DIA", "IWM") and str(item.get("category") or "").strip().lower() == "crypto":
        return 0.0
    title = str(item.get("title") or "")
    title_up = title.upper()
    title_low = title.lower()
    if re.search(rf"(?<![A-Z0-9]){re.escape(s_up)}(?![A-Z0-9])", title_up):
        return 1.0

    aliases: list[str] = []
    for raw_al in (*_INSTRUMENT_ALIASES.get(s_up, ()), *(extra_aliases or ())):
        al = str(raw_al or "").strip().lower()
        if al and al not in aliases:
            aliases.append(al)

    def _alias_hit(txt_low: str, txt_up: str) -> bool:
        for al in aliases:
            if al.isascii():
                if re.search(rf"(?<![A-Z0-9]){re.escape(al.upper())}(?![A-Z0-9])", txt_up):
                    return True
            elif al in txt_low:
                return True
        return False

    if aliases and _alias_hit(title_low, title_up):
        return 1.0

    body_raw = f"{item.get('summary') or ''} {item.get('why_it_matters') or ''}"
    body_up = body_raw.upper()
    if re.search(rf"(?<![A-Z0-9]){re.escape(s_up)}(?![A-Z0-9])", body_up):
        return 0.5
    if aliases and _alias_hit(body_raw.lower(), body_up):
        return 0.5
    return 0.0


_CN_TECH_ASHARE_ALIAS_GROUPS: tuple[tuple[str, ...], ...] = (
    ("deepseek", "深度求索", "deepseek-r1", "deepseek-v3"),
    ("国产大模型", "qwen", "通义千问", "minimax", "glm", "智谱", "kimi", "月之暗面", "豆包"),
    ("华为", "鸿蒙", "昇腾", "huawei", "harmonyos", "ascend"),
    ("具身智能", "宇树", "unitree", "人形机器人", "optimus"),
    ("比亚迪", "智驾", "天神之眼", "byd", "自动驾驶", "高阶智驾"),
    ("科创·半导体", "科创板", "中芯", "寒武纪", "国产算力", "光刻机"),
    ("港股科技", "恒生科技", "恒指", "港股", "腾讯", "阿里", "美团", "小米"),
)


def _expand_keyword_aliases(target_low: str) -> tuple[str, ...]:
    variants: list[str] = [target_low]
    for grp in _CN_TECH_ASHARE_ALIAS_GROUPS:
        if any(g == target_low or g in target_low or target_low in g for g in grp):
            for g in grp:
                if g not in variants:
                    variants.append(g)
    return tuple(variants)


def _match_rule_c(rule: dict[str, Any], item: dict[str, Any]) -> float:
    """Return relevance c_i in {0.0, 0.5, 1.0} for a rule against a signal."""
    kind = rule["kind"]
    target = rule["target"]
    title = str(item.get("title") or "")
    summary = f"{item.get('summary') or ''} {item.get('why_it_matters') or ''}"
    source = str(item.get("source") or "")
    cat = str(item.get("category") or "")
    sector = str(item.get("sector") or "")
    content_type = str(item.get("content_type") or "")
    topics_low = [str(tp).strip().lower() for tp in (item.get("topics") or []) if str(tp).strip()]

    if kind == "instrument":
        return _match_instrument_c(target, item)
    if kind == "source":
        t_low = target.lower()
        s_low = source.lower()
        if t_low == s_low or t_low in s_low:
            return 1.0
        merged = " ".join(str(x).lower() for x in (item.get("merged_sources") or []))
        return 0.5 if t_low in merged else 0.0
    if kind == "topic":
        t_low = target.lower()
        if t_low == cat.lower() or t_low == content_type.lower() or any(t_low == tp or t_low in tp for tp in topics_low):
            return 1.0
        aliases = _expand_keyword_aliases(t_low)
        if any(al in title.lower() for al in aliases):
            return 1.0
        if any(al in summary.lower() for al in aliases):
            return 0.5
        return 0.0
    if kind == "sector":
        t_low = target.lower()
        aliases = _expand_keyword_aliases(t_low)
        if sector and any(al in sector.lower() for al in aliases):
            return 1.0
        if any(al in title.lower() for al in aliases):
            return 1.0
        if any(al in summary.lower() for al in aliases):
            return 0.5
        return 0.0
    if kind == "keyword":
        t_low = target.lower()
        aliases = _expand_keyword_aliases(t_low)
        if any(al in title.lower() for al in aliases):
            return 1.0
        if any(al in summary.lower() for al in aliases):
            return 0.5
        return 0.0
    if kind == "regex":
        pat = rule.get("_compiled")
        if pat is None:
            return 0.0
        if pat.search(title):
            return 1.0
        if pat.search(summary):
            return 0.5
        return 0.0
    return 0.0


def _matches_subtopic(item: dict[str, Any], subtopic_norm: str) -> bool:
    topics = [str(t).strip().lower() for t in (item.get("topics") or []) if str(t).strip()]
    aliases = _expand_keyword_aliases(subtopic_norm)
    if any(subtopic_norm == tp or subtopic_norm in tp or tp in subtopic_norm for tp in topics):
        return True
    if any(al in tp for tp in topics for al in aliases):
        return True
    combined = f"{item.get('title') or ''} {item.get('summary') or ''}".lower()
    return any(al in combined for al in aliases)


def _build_holding_radar(
    matched_holdings: list[tuple[str, str, float]],
    item: dict[str, Any],
) -> dict[str, Any] | None:
    if not matched_holdings:
        return None
    symbols = [sym for sym, _d, _c in matched_holdings]
    src = str(item.get("source") or "")
    title = str(item.get("title") or "")
    # Routine 10-K / 10-Q / 20-F SEC filings default to uncertain direction
    if (src == "SEC EDGAR" or "SEC" in src) and re.search(r"\b(?:10-K|10-Q|20-F)\b", title):
        relation = "方向未明"
    else:
        ev_dir = str(item.get("direction") or "neutral").lower()
        if ev_dir not in ("bullish", "bearish"):
            relation = "方向未明"
        else:
            rels: set[str] = set()
            for _sym, h_dir, _c in matched_holdings:
                if h_dir == "mixed":
                    rels.add("方向未明")
                elif (h_dir == "long" and ev_dir == "bullish") or (h_dir == "short" and ev_dir == "bearish"):
                    rels.add("顺风")
                else:
                    rels.add("逆风")
            if len(rels) == 1:
                relation = next(iter(rels))
            else:
                relation = "方向未明"
    return {
        "matched": True,
        "symbols": symbols,
        "relation": relation,
        "detail": f"持仓关联 · {relation}",
    }


def rank_curated_signals(
    candidates: list[dict[str, Any]],
    *,
    rules: list[dict[str, Any]] | None = None,
    workspace: dict[str, Any] | None = None,
    scope: str = "all",
    category: str | None = None,
    subtopic: str | None = None,
    direction: str | None = None,
    instrument_id: str | None = None,
    event_id: str | None = None,
    sort: str = "importance",
    cursor: str | int | None = None,
    limit: int = 85,
) -> list[dict[str, Any]]:
    """Compute personal relevance score, holding radar, and filter/sort signals without mutating base scores."""
    compiled_rules = _compile_rules(rules)
    holdings, held_sectors, _equity_valid = _extract_workspace_holdings(workspace)

    block_rules = [r for r in compiled_rules if r["action"] == "block"]
    watch_rules = [r for r in compiled_rules if r["action"] == "watch"]
    adjust_rules = [r for r in compiled_rules if r["action"] == "adjust"]
    mute_rules = [r for r in compiled_rules if r["action"] == "mute"]

    scope_norm = str(scope or "all").strip().lower()
    cat_norm = str(category or "").strip().lower() or None
    sub_norm = str(subtopic or "").strip().lower() or None
    dir_norm = str(direction or "").strip().lower() or None
    inst_norm = str(instrument_id or "").strip().upper() or None
    ev_norm = str(event_id or "").strip() or None

    ranked: list[dict[str, Any]] = []
    for raw in candidates or []:
        if not isinstance(raw, dict):
            continue
        if str(raw.get("prefilter_label") or "").upper() == "BLOCK":
            continue
        if any(_match_rule_c(br, raw) > 0.0 for br in block_rules):
            continue

        item = dict(raw)
        b_score = float(item.get("attention_score") or 0.0)
        q = _clamp((b_score - 55.0) / 25.0, 0.0, 1.0)
        q_hold = 0.0 if b_score < 55.0 else _clamp((b_score - 50.0) / 20.0, 0.0, 1.0)

        # H: holding score across held positions
        h_scores: list[float] = []
        matched_holdings: list[tuple[str, str, float]] = []
        for sym, h_info in holdings.items():
            c_i = _match_instrument_c(sym, item, extra_aliases=h_info.get("aliases"))
            if c_i > 0.0:
                h_scores.append(h_info["w"] * c_i)
                matched_holdings.append((sym, h_info["direction"], c_i))
        h_raw_val = max(h_scores) if h_scores else 0.0
        item_title = str(item.get("title") or "")
        item_dir = str(item.get("direction") or "neutral").lower()
        is_roundup_or_neutral = bool(ROUNDUP_RE.search(item_title)) or item_dir == "neutral"
        h_val = round(h_raw_val * 0.45, 2) if (h_raw_val > 0.0 and is_roundup_or_neutral) else h_raw_val

        # W: watchlist (5*c_i) and sector (3*c_i) score
        w_scores: list[float] = []
        matched_watch = False
        for wr in watch_rules:
            c_i = _match_rule_c(wr, item)
            if c_i > 0.0:
                matched_watch = True
                mult = 3.0 if wr["kind"] == "sector" else 5.0
                w_scores.append(mult * c_i)
        if held_sectors:
            sec_txt = f"{item.get('sector') or ''} {item.get('title') or ''} {item.get('summary') or ''}".lower()
            for hs in held_sectors:
                if hs and hs in sec_txt:
                    w_scores.append(3.0 * (1.0 if hs in str(item.get("title") or "").lower() else 0.5))
        w_val = max(w_scores) if w_scores else 0.0

        a_val = min(24.0, max(h_val, w_val) + 0.25 * min(h_val, w_val))

        # T: independent multi-source 48h heat boost (strictly requires participant_count >= 2 and non-backfill)
        is_backfill = bool(item.get("is_backfill"))
        part_count = int(item.get("participant_count") or 1)
        heat_48h = float(item.get("heat_48h") or 0.0)
        if part_count >= 2 and not is_backfill:
            t_val = 3.0 * _clamp(heat_48h / 100.0, 0.0, 1.0)
        else:
            t_val = 0.0

        # S: explicit user adjustments in [-12, 3]
        s_raw = 0.0
        matched_rule = matched_watch
        for ar in adjust_rules:
            c_i = _match_rule_c(ar, item)
            if c_i > 0.0:
                matched_rule = True
                s_raw += float(ar["points"]) * c_i
        s_val = _clamp(s_raw, -12.0, 3.0)

        # D: mute penalty in [0, 15]
        d_raw = 0.0
        for mr in mute_rules:
            c_i = _match_rule_c(mr, item)
            if c_i > 0.0:
                matched_rule = True
                d_raw += float(mr["points"]) * c_i
        d_val = _clamp(d_raw, 0.0, 15.0)

        qa_raw = (q_hold if h_val > 0.0 else q) * a_val
        qt_raw = q * t_val
        s_eff_raw = q * max(s_val, 0.0) + min(s_val, 0.0)

        final_score = round(
            _clamp(b_score + qa_raw + qt_raw + s_eff_raw - d_val, 0.0, 100.0),
            1,
        )

        selected = bool(item.get("selected"))
        held_match = bool(matched_holdings)
        personal_high = (not is_backfill) and (
            (selected and b_score >= 70.0 and final_score >= 78.0)
            or (held_match and b_score >= 60.0 and final_score >= 74.0)
        )

        reasons: list[str] = [f"客观 {int(round(b_score))}"]
        qa_eff = round(qa_raw, 1)
        if qa_eff > 0.0:
            reasons.append(f"持仓与自选 +{qa_eff:.1f}")
        qt_eff = round(qt_raw, 1)
        if qt_eff > 0.0:
            reasons.append(f"热度 +{qt_eff:.1f}")
        s_eff = round(s_eff_raw, 1)
        if s_eff != 0.0:
            reasons.append(f"规则调分 {s_eff:+.1f}")
        if d_val > 0.0:
            reasons.append(f"降权 -{d_val:.1f}")

        radar = _build_holding_radar(matched_holdings, item)

        item["base_score"] = int(round(b_score))
        item["final_score"] = final_score
        item["personal_high"] = personal_high
        item["held_match"] = held_match
        item["is_watched"] = matched_watch
        item["reasons"] = reasons
        item["holding_radar"] = radar

        # Scope & attribute filtering
        if cat_norm and cat_norm != "all" and str(item.get("category") or "").lower() != cat_norm:
            continue
        if sub_norm and sub_norm != "all" and not _matches_subtopic(item, sub_norm):
            continue
        if dir_norm and dir_norm != "all" and str(item.get("direction") or "").lower() != dir_norm:
            continue
        if inst_norm and _match_instrument_c(inst_norm, item) <= 0.0:
            continue
        if ev_norm and str(item.get("id") or "") != ev_norm and str(item.get("cluster_id") or "") != ev_norm:
            continue
        if scope_norm == "rules" and not matched_rule:
            continue
        if scope_norm == "held" and radar is None:
            continue
        if scope_norm in ("watch", "watchlist") and (not matched_watch and radar is None):
            continue
        if scope_norm in ("high", "personal_high") and not personal_high:
            continue

        ranked.append(item)

    sort_norm = str(sort or "importance").strip().lower()
    if sort_norm == "time":
        ranked.sort(
            key=lambda x: (
                str(x.get("time_bjt") or "").replace("T", " ")[:19],
                float(x.get("final_score") or 0.0),
                float(x.get("base_score") or 0.0),
            ),
            reverse=True,
        )
    else:
        ranked.sort(
            key=lambda x: (
                0 if x.get("is_backfill") else 1,
                float(x.get("final_score") or 0.0),
                float(x.get("base_score") or 0.0),
                str(x.get("time_bjt") or "").replace("T", " ")[:19],
            ),
            reverse=True,
        )
        # Enforce Top-15 category balance on un-filtered importance feed so crypto cannot flood head rows even on shorter pools
        if (
            scope_norm == "all"
            and (not cat_norm or cat_norm == "all")
            and not sub_norm
            and not dir_norm
            and not inst_norm
            and not ev_norm
            and len(ranked) > 2
        ):
            head_cap = 2
            head_kept: list[dict[str, Any]] = []
            non_crypto_tail: list[dict[str, Any]] = []
            deferred_crypto: list[dict[str, Any]] = []
            crypto_in_head = 0
            for item in ranked:
                is_cr = str(item.get("category") or "").lower() == "crypto"
                if len(head_kept) < 15:
                    if is_cr and crypto_in_head >= head_cap:
                        deferred_crypto.append(item)
                    else:
                        head_kept.append(item)
                        if is_cr:
                            crypto_in_head += 1
                elif is_cr:
                    deferred_crypto.append(item)
                else:
                    non_crypto_tail.append(item)
            ranked = head_kept + non_crypto_tail + deferred_crypto

    start = 0
    if cursor is not None and str(cursor).strip() != "":
        try:
            start = max(0, int(cursor))
        except ValueError:
            start = 0
    lim = max(1, int(limit)) if limit and limit > 0 else len(ranked)
    return ranked[start : start + lim]


def rank_curated_events(
    event_cards: list[dict[str, Any]],
    *,
    rules: list[dict[str, Any]] | None = None,
    workspace: dict[str, Any] | None = None,
    scope: str = "all",
    category: str | None = None,
    subtopic: str | None = None,
    direction: str | None = None,
    instrument_id: str | None = None,
    event_id: str | None = None,
    sort: str = "importance",
    cursor: str | int | None = None,
    limit: int = 85,
) -> list[dict[str, Any]]:
    """Rank and filter persistent event cards reusing personal curation rules & holding radar."""
    return rank_curated_signals(
        event_cards,
        rules=rules,
        workspace=workspace,
        scope=scope,
        category=category,
        subtopic=subtopic,
        direction=direction,
        instrument_id=instrument_id,
        event_id=event_id,
        sort=sort,
        cursor=cursor,
        limit=limit,
    )
