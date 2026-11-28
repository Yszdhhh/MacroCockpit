"""Two-phase text/CSV portfolio position importer with strict coin-vs-contract validation."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import sqlite3
import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Any

from backend.runtime import now_bjt as _now_dt
from backend.market_quotes import KNOWN_SWAP_MULTIPLIERS
from backend.portfolio_store import DEFAULT_ACCOUNT_ID, PortfolioStore, PreviewExpiredError as PreviewExpiredError
from backend.valuation import derivative_quote_currency, parse_decimal_str

DEFAULT_PREVIEW_TTL_SECONDS = 600  # 10 minutes

_CN_TICKER_ALIASES: dict[str, tuple[str, str]] = {
    "比特币": ("BTC", "crypto_spot"),
    "大饼": ("BTC", "crypto_spot"),
    "以太坊": ("ETH", "crypto_spot"),
    "以太": ("ETH", "crypto_spot"),
    "索拉纳": ("SOL", "crypto_spot"),
    "币安币": ("BNB", "crypto_spot"),
    "狗狗币": ("DOGE", "crypto_spot"),
    "黄金": ("GOLD", "commodity"),
    "英伟达": ("NVDA", "equity"),
    "苹果": ("AAPL", "equity"),
    "微软": ("MSFT", "equity"),
    "谷歌": ("GOOG", "equity"),
    "亚马逊": ("AMZN", "equity"),
    "特斯拉": ("TSLA", "equity"),
    "超威半导体": ("AMD", "equity"),
    "博通": ("AVGO", "equity"),
    "台积电": ("TSM", "equity"),
    "美光": ("MU", "equity"),
    "英特尔": ("INTC", "equity"),
    "高通": ("QCOM", "equity"),
    "甲骨文": ("ORCL", "equity"),
    "奈飞": ("NFLX", "equity"),
    "美金": ("USD", "cash"),
    "美元": ("USD", "cash"),
    "泰达币": ("USDT", "cash"),
}
_KNOWN_CRYPTO_BASES = frozenset({"BTC", "ETH", "SOL", "BNB", "DOGE", "XRP", "ADA", "AVAX", "LINK", "SUI"})
_CASH_SYMBOLS = ("USDT", "USDC", "CNY", "CNH", "HKD", "USD")
_KNOWN_EQUITIES = frozenset(
    {
        "AAPL", "NVDA", "MSFT", "GOOG", "GOOGL", "META", "AMZN", "TSLA",
        "AMD", "AVGO", "TSM", "MU", "INTC", "QCOM", "PLTR", "ORCL",
        "NFLX", "ARM", "SMCI", "COIN", "MSTR", "SPY", "QQQ", "DIA",
        "GLD", "SOXX", "XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU",
    }
)
_SLEEVE_ALIASES: dict[str, str] = {
    "btc_trend": "btc_trend",
    "核心趋势": "btc_trend",
    "趋势仓": "btc_trend",
    "gold_hedge": "gold_hedge",
    "黄金避险": "gold_hedge",
    "bear_basket": "bear_basket",
    "熊市配对": "bear_basket",
    "配对对冲": "bear_basket",
    "crash_bounce": "crash_bounce",
    "超跌反弹": "crash_bounce",
}


class PreviewValidationError(ValueError):
    """Raised when attempting to commit an import preview that has blocking validation issues."""


def _canonicalize_body(text: str) -> str:
    norm_lines = [ln.strip() for ln in str(text or "").replace("\r\n", "\n").replace("\r", "\n").splitlines()]
    return "\n".join(ln for ln in norm_lines if ln)


def _strip_thousand_commas(text: str) -> str:
    return re.sub(r"(?<=\d),(?=\d{3}(?:\D|$))", "", text)


def _issue(line_no: int, severity: str, code: str, message: str) -> dict[str, Any]:
    return {"line": line_no, "severity": severity, "code": code, "message": message}


def _detect_sleeve(text: str) -> str:
    low = text.lower()
    for alias, sid in _SLEEVE_ALIASES.items():
        if alias in low:
            return sid
    return ""


def _parse_pct_or_dec(raw: str) -> str:
    s = raw.strip()
    if s.endswith("%"):
        num = Decimal(parse_decimal_str(s[:-1])) / Decimal("100")
        return parse_decimal_str(num)
    return parse_decimal_str(s)


def _resolve_symbol_from_clause(clause: str) -> tuple[str, str]:
    """Return (symbol, inferred_asset_class) from text clause."""
    # Explicit derivative ticker like BTC-USDT-SWAP or BTC-USD-SWAP
    m_swap = re.search(r"\b([A-Z]{2,10}-(?:USDT|USDC|USD)-(?:SWAP|PERP))\b", clause, re.IGNORECASE)
    if m_swap:
        return (m_swap.group(1).upper(), "perp")

    for cn_name, (sym, cls) in _CN_TICKER_ALIASES.items():
        if cn_name in clause:
            return (sym, cls)

    # Uppercase ASCII ticker tokens (exclude common unit/field tokens)
    ignore = {*_CASH_SYMBOLS, "SWAP", "PERP", "MMR", "CSV", "LONG", "SHORT", "SL", "TP"}
    tokens = re.findall(r"\b([A-Z]{1,10}(?:\.[A-Z]{1,3})?)\b", clause)
    for tok in tokens:
        up = tok.upper()
        if up in ignore:
            continue
        if up in _KNOWN_CRYPTO_BASES:
            return (up, "crypto_spot")
        if up in _KNOWN_EQUITIES:
            return (up, "equity")
        return (up, "equity")

    for c_sym in _CASH_SYMBOLS:
        if re.search(rf"\b{c_sym}\b", clause, re.IGNORECASE):
            return (c_sym, "cash")

    return ("", "other")


def _extract_tagged_number(clause: str, pattern: str) -> str | None:
    m = re.search(pattern, clause, re.IGNORECASE)
    if not m:
        return None
    raw = _strip_thousand_commas(m.group(1)).strip()
    return parse_decimal_str(raw)


def _parse_text_clause(
    clause: str,
    line_no: int,
    account_id: str,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    issues: list[dict[str, Any]] = []
    clean = _strip_thousand_commas(clause.strip())
    if not clean:
        return (None, issues)

    is_deriv = bool(
        re.search(
            r"(做多|做空|开多|开空|多单|空单|永续|合约|逐仓|全仓|强平|SWAP|PERP|FUTURES|\blong\b|\bshort\b)",
            clean,
            re.IGNORECASE,
        )
    )
    is_cash_kw = bool(re.search(r"(钱包|现金|余额|可用资金|\bcash\b)", clean, re.IGNORECASE))
    sleeve = _detect_sleeve(clean)
    stop_loss = _extract_tagged_number(clean, r"(?:止损|sl)\s*[=:：]?\s*\$?\s*([+-]?\d+(?:\.\d+)?)")
    take_profit = _extract_tagged_number(clean, r"(?:止盈|tp)\s*[=:：]?\s*\$?\s*([+-]?\d+(?:\.\d+)?)")

    if is_cash_kw and not is_deriv:
        sym = "USD"
        for cand in _CASH_SYMBOLS:
            if re.search(rf"\b{cand}\b", clean, re.IGNORECASE):
                sym = cand
                break
        # Extract locked margin note like "含 $100 保证金" or "占用保证金 100"
        locked_margin = "0"
        m_locked = re.search(
            r"(?:含\s*\$?\s*(\d+(?:\.\d+)?)\s*(?:美元|USD|USDT)?\s*保证金|(?:占用|已用)?(?:保证金|margin)\s*[=:：]?\s*\$?\s*(-?\d+(?:\.\d+)?))",
            clean,
            re.IGNORECASE,
        )
        if m_locked:
            locked_margin = parse_decimal_str(m_locked.group(1) or m_locked.group(2))
        # Remove the "(含 ... 保证金)" or "占用保证金 ..." segment before extracting wallet balance
        no_paren = re.sub(r"[（(][^）)]*保证金[^）)]*[）)]", " ", clean)
        no_paren = re.sub(
            r"(?:占用|已用)?(?:保证金|margin)\s*[=:：]?\s*\$?\s*-?\d+(?:\.\d+)?",
            " ",
            no_paren,
            flags=re.IGNORECASE,
        )
        qty_str = _extract_tagged_number(
            no_paren,
            r"(?:钱包|现金|余额|可用资金|cash)?\s*[=:：]?\s*\$?\s*(-?(?:\d+(?:\.\d*)?|\.\d+))",
        )
        if not qty_str:
            m_any = re.search(r"\$?\s*(-?(?:\d+(?:\.\d*)?|\.\d+))", no_paren)
            qty_str = parse_decimal_str(m_any.group(1)) if m_any else None
        if not qty_str:
            issues.append(_issue(line_no, "error", "MISSING_QTY", f"现金条目缺少金额: {clause}"))
            return (None, issues)
        if Decimal(qty_str) < 0:
            issues.append(_issue(line_no, "error", "INVALID_QTY", f"现金金额不能为负: {clause}"))
            return (None, issues)

        return (
            {
                "kind": "spot",
                "key": f"spot:{account_id}:{sym}",
                "op": {
                    "op": "put",
                    "entity": "spot",
                    "data": {
                        "account_id": account_id,
                        "instrument_id": sym,
                        "symbol": sym,
                        "asset_class": "cash",
                        "quote_currency": "CNY" if sym == "CNH" else sym,
                        "is_cash": True,
                        "qty": qty_str,
                        "avg_cost": "1",
                        "locked_margin_usd": locked_margin,
                        "sleeve": sleeve,
                        "source": "import",
                    },
                },
            },
            issues,
        )

    sym, inferred_cls = _resolve_symbol_from_clause(clean)
    if not sym:
        issues.append(_issue(line_no, "error", "MISSING_SYMBOL", f"无法识别标的代码: {clause}"))
        return (None, issues)

    if is_deriv:
        # Determine side
        if re.search(r"(做空|开空|空单|空仓|\bshort\b)", clean, re.IGNORECASE):
            side = "short"
        elif re.search(r"(做多|开多|多单|多仓|\blong\b)", clean, re.IGNORECASE):
            side = "long"
        else:
            issues.append(_issue(line_no, "error", "MISSING_SIDE", f"合约仓位缺少方向(做多/做空): {clause}"))
            return (None, issues)

        margin_mode = "isolated" if re.search(r"(逐仓|\bisolated\b)", clean, re.IGNORECASE) else "cross"
        is_inverse = bool(re.search(r"(反向|币本位|\binverse\b|-USD-SWAP)", clean, re.IGNORECASE))
        contract_type = "inverse" if is_inverse else "linear"

        if sym.endswith(("-SWAP", "-PERP")):
            iid = sym
            base_sym = sym.split("-")[0]
        else:
            base_sym = sym
            iid = f"{base_sym}-USD-SWAP" if contract_type == "inverse" else f"{base_sym}-USDT-SWAP"

        # Strip labeled numeric parameters so we isolate the position quantity & unit cleanly
        masked = clean
        for tag_pat in (
            r"(?:开仓价?|成本价?|均价|入场价?|entry)\s*[=:：@]?\s*\$?\s*-?\d+(?:\.\d+)?",
            r"@\s*\$?\s*-?\d+(?:\.\d+)?",
            r"(?:保证金|margin)\s*[=:：]?\s*\$?\s*-?\d+(?:\.\d+)?",
            r"(?:强平价?|liq(?:_price)?)\s*[=:：]?\s*\$?\s*-?\d+(?:\.\d+)?",
            r"(?:mmr|维持保证金率?)\s*[=:：]?\s*[+-]?(?:\d+(?:\.\d*)?|\.\d+)%?",
            r"(?:乘数|面值|合约乘数|multiplier|m)\s*[=:：]?\s*[+-]?(?:\d+(?:\.\d*)?|\.\d+)",
            r"(?:止损|sl)\s*[=:：]?\s*\$?\s*\d+(?:\.\d+)?",
            r"(?:止盈|tp)\s*[=:：]?\s*\$?\s*\d+(?:\.\d+)?",
            r"(?:杠杆|lev)\s*[=:：]?\s*-?\d+(?:\.\d+)?[xX倍]?",
            r"\d+(?:\.\d+)?\s*[xX倍](?:\s*杠杆)?",
        ):
            masked = re.sub(tag_pat, " ", masked, flags=re.IGNORECASE)

        # Explicitly distinguish coin count vs contract count!
        qty_str: str | None = None
        qty_unit: str | None = None
        m_contract = re.search(r"(-?(?:\d+(?:\.\d*)?|\.\d+))\s*(?:张|contracts?)\b", masked, re.IGNORECASE)
        if not m_contract:
            m_contract = re.search(r"(-?(?:\d+(?:\.\d*)?|\.\d+))\s*张", masked)
        m_coin = re.search(
            rf"(-?(?:\d+(?:\.\d*)?|\.\d+))\s*(?:个|枚|币|coins?|{re.escape(base_sym)}(?!-))\b",
            masked,
            re.IGNORECASE,
        )
        if not m_coin:
            m_coin = re.search(r"(-?(?:\d+(?:\.\d*)?|\.\d+))\s*(?:个|枚|币|比特币|以太坊)", masked)

        if m_contract and m_coin:
            issues.append(_issue(line_no, "error", "AMBIGUOUS_QTY_UNIT", f"同时出现张数与币数单位，请只保留一种: {clause}"))
            return (None, issues)
        if m_contract:
            qty_str = parse_decimal_str(m_contract.group(1))
            qty_unit = "contract"
        elif m_coin:
            qty_str = parse_decimal_str(m_coin.group(1))
            qty_unit = "coin"
        else:
            issues.append(
                _issue(
                    line_no,
                    "error",
                    "AMBIGUOUS_QTY_UNIT",
                    f"合约数量必须明确标注单位（如 '0.01 {base_sym}'/'个' 或 '10 张'），严禁混用币数与张数: {clause}",
                )
            )
            return (None, issues)

        if Decimal(qty_str) <= 0:
            issues.append(_issue(line_no, "error", "INVALID_QTY", f"合约数量必须大于 0: {clause}"))
            return (None, issues)

        # Contract multiplier resolution
        explicit_mult = _extract_tagged_number(clean, r"(?:合约乘数|乘数|面值|multiplier|\bm)\s*[=:：]\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))")
        if qty_unit == "coin":
            mult_str = explicit_mult or "1"
        else:
            if explicit_mult is not None:
                mult_str = explicit_mult
            elif iid in KNOWN_SWAP_MULTIPLIERS:
                mult_str = KNOWN_SWAP_MULTIPLIERS[iid]["ctVal"]
            else:
                issues.append(
                    _issue(
                        line_no,
                        "error",
                        "UNKNOWN_CONTRACT_MULTIPLIER",
                        f"张数计价合约 {iid} 缺少已知合约乘数，请改用币数或显式指定乘数 (m=...)",
                    )
                )
                return (None, issues)

        entry_str = _extract_tagged_number(
            clean,
            r"(?:开仓价?|成本价?|均价|入场价?|entry)\s*[=:：@]?\s*\$?\s*(-?\d+(?:\.\d+)?)",
        )
        if entry_str is None:
            entry_str = _extract_tagged_number(clean, r"@\s*\$?\s*(-?\d+(?:\.\d+)?)")
        if entry_str is None or Decimal(entry_str) <= 0:
            issues.append(_issue(line_no, "error", "MISSING_ENTRY_PRICE", f"合约仓位缺少开仓价: {clause}"))
            return (None, issues)

        margin_str = _extract_tagged_number(clean, r"(?:保证金|margin)\s*[=:：]?\s*\$?\s*(-?\d+(?:\.\d+)?)") or "0"
        venue_liq = _extract_tagged_number(clean, r"(?:强平价?|liq(?:_price)?)\s*[=:：]?\s*\$?\s*(-?\d+(?:\.\d+)?)")
        lev_str = _extract_tagged_number(clean, r"(?:杠杆|lev)\s*[=:：]?\s*(-?\d+(?:\.\d+)?)[xX倍]?")
        if lev_str is None:
            lev_str = _extract_tagged_number(clean, r"(\d+(?:\.\d+)?)\s*[xX倍](?:\s*杠杆)?")

        mmr_str: str | None = None
        m_mmr = re.search(r"(?:mmr|维持保证金率?)\s*[=:：]?\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)%?)", clean, re.IGNORECASE)
        if m_mmr:
            mmr_str = _parse_pct_or_dec(m_mmr.group(1))

        quote_ccy = derivative_quote_currency(iid, contract_type)
        margin_ccy = base_sym if contract_type == "inverse" else quote_ccy
        op_data: dict[str, Any] = {
            "account_id": account_id,
            "instrument_id": iid,
            "symbol": iid,
            "base_asset": base_sym,
            "asset_class": "perp",
            "quote_currency": quote_ccy,
            "side": side,
            "margin_mode": margin_mode,
            "contract_type": contract_type,
            "qty": qty_str,
            "qty_unit": qty_unit,
            "contract_multiplier": mult_str,
            "entry_price": entry_str,
            "margin_usd": margin_str,
            "margin_amount": margin_str,
            "margin_currency": margin_ccy,
            "sleeve": sleeve,
            "source": "import",
        }
        if lev_str is not None:
            op_data["leverage"] = lev_str
        if mmr_str is not None:
            op_data["mmr"] = mmr_str
        if venue_liq is not None:
            op_data["venue_liq"] = venue_liq
        if stop_loss is not None:
            op_data["stop_loss"] = stop_loss
        if take_profit is not None:
            op_data["take_profit"] = take_profit

        return (
            {
                "kind": "derivative",
                "key": f"deriv:{account_id}:{iid}:{side}:{margin_mode}",
                "op": {"op": "put", "entity": "derivative", "data": op_data},
            },
            issues,
        )

    # Spot / Equity / Commodity
    is_cash = sym in _CASH_SYMBOLS
    masked_spot = clean
    for tag_pat in (
        r"(?:开仓价?|成本价?|均价|单价|cost)\s*[=:：@]?\s*\$?\s*-?\d+(?:\.\d+)?",
        r"@\s*\$?\s*-?\d+(?:\.\d+)?",
        r"(?:止损|sl)\s*[=:：]?\s*\$?\s*\d+(?:\.\d+)?",
        r"(?:止盈|tp)\s*[=:：]?\s*\$?\s*\d+(?:\.\d+)?",
    ):
        masked_spot = re.sub(tag_pat, " ", masked_spot, flags=re.IGNORECASE)

    qty_str = None
    m_spot_unit = re.search(
        rf"(-?(?:\d+(?:\.\d*)?|\.\d+))\s*(?:股|份|个|枚|盎司|oz|shares?|coins?|{re.escape(sym)})",
        masked_spot,
        re.IGNORECASE,
    )
    if m_spot_unit:
        qty_str = parse_decimal_str(m_spot_unit.group(1))
    else:
        m_num = re.search(r"(?<![\w$.-])(-?(?:\d+(?:\.\d*)?|\.\d+))(?![\w.])", masked_spot)
        if m_num:
            qty_str = parse_decimal_str(m_num.group(1))

    if qty_str is None:
        if re.search(r"\$\s*\d+(?:\.\d+)?", masked_spot):
            issues.append(
                _issue(
                    line_no,
                    "error",
                    "SPOT_QTY_REQUIRED",
                    f"现货/美股 {sym} 请提供股数或币数（如 '{sym} 5 股 @100'），而非仅写金额: {clause}",
                )
            )
        else:
            issues.append(_issue(line_no, "error", "MISSING_QTY", f"现货条目缺少数量: {clause}"))
        return (None, issues)

    if Decimal(qty_str) < 0:
        issues.append(_issue(line_no, "error", "INVALID_QTY", f"现货数量不能为负: {clause}"))
        return (None, issues)

    cost_str = _extract_tagged_number(
        clean,
        r"(?:成本价?|均价|单价|成本|cost)\s*[=:：@]?\s*\$?\s*(-?\d+(?:\.\d+)?)",
    )
    if cost_str is None:
        cost_str = _extract_tagged_number(clean, r"@\s*\$?\s*(-?\d+(?:\.\d+)?)")
    if cost_str is None:
        cost_str = "0"
        if not is_cash:
            issues.append(_issue(line_no, "warning", "DEFAULT_AVG_COST", f"{sym} 未提供成本价，已默认为 0"))

    op_data = {
        "account_id": account_id,
        "instrument_id": sym,
        "symbol": sym,
        "asset_class": "cash" if is_cash else inferred_cls,
        "quote_currency": ("CNY" if sym == "CNH" else sym) if is_cash else "USD",
        "is_cash": is_cash,
        "qty": qty_str,
        "avg_cost": cost_str,
        "sleeve": sleeve,
        "source": "import",
    }
    if stop_loss is not None:
        op_data["stop_loss"] = stop_loss
    if take_profit is not None:
        op_data["take_profit"] = take_profit

    return (
        {
            "kind": "spot",
            "key": f"spot:{account_id}:{sym}",
            "op": {"op": "put", "entity": "spot", "data": op_data},
        },
        issues,
    )


_CSV_HEADER_ALIASES: dict[str, str] = {
    "type": "type",
    "kind": "type",
    "类型": "type",
    "资产类型": "type",
    "symbol": "symbol",
    "instrument": "symbol",
    "instrument_id": "symbol",
    "ticker": "symbol",
    "标的": "symbol",
    "代码": "symbol",
    "side": "side",
    "方向": "side",
    "qty": "qty",
    "quantity": "qty",
    "amount": "qty",
    "数量": "qty",
    "仓位": "qty",
    "qty_unit": "qty_unit",
    "unit": "qty_unit",
    "单位": "qty_unit",
    "multiplier": "multiplier",
    "contract_multiplier": "multiplier",
    "合约乘数": "multiplier",
    "乘数": "multiplier",
    "面值": "multiplier",
    "entry_price": "entry_price",
    "avg_cost": "entry_price",
    "cost": "entry_price",
    "price": "entry_price",
    "开仓价": "entry_price",
    "成本价": "entry_price",
    "均价": "entry_price",
    "margin_mode": "margin_mode",
    "保证金模式": "margin_mode",
    "margin": "margin",
    "margin_usd": "margin",
    "margin_amount": "margin",
    "保证金": "margin",
    "locked_margin_usd": "locked_margin_usd",
    "占用保证金": "locked_margin_usd",
    "leverage": "leverage",
    "杠杆": "leverage",
    "mmr": "mmr",
    "维持保证金率": "mmr",
    "venue_liq": "venue_liq",
    "liq_price": "venue_liq",
    "强平价": "venue_liq",
    "contract_type": "contract_type",
    "合约类型": "contract_type",
    "sleeve": "sleeve",
    "策略": "sleeve",
    "stop_loss": "stop_loss",
    "止损": "stop_loss",
    "take_profit": "take_profit",
    "止盈": "take_profit",
    "is_cash": "is_cash",
    "是否现金": "is_cash",
}


def _parse_csv_rows(
    canonical_text: str,
    account_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    issues: list[dict[str, Any]] = []
    parsed_items: list[dict[str, Any]] = []

    reader = csv.reader(io.StringIO(canonical_text))
    rows = []
    line_no = 1
    for row in reader:
        if any(c.strip() for c in row):
            rows.append((line_no, row))
        line_no = reader.line_num + 1
    if not rows:
        return (parsed_items, [_issue(1, "error", "EMPTY_INPUT", "导入内容为空")])

    first_norm = [_CSV_HEADER_ALIASES.get(c.strip().lower(), "") for c in rows[0][1]]
    has_header = sum(1 for x in first_norm if x) >= 2
    if has_header:
        headers = first_norm
        data_rows = rows[1:]
    else:
        headers = ["type", "symbol", "side", "qty", "qty_unit", "entry_price", "margin_mode", "margin", "sleeve"]
        data_rows = rows

    def parse_row(line_no: int, raw_row: list[str]) -> None:
        row: dict[str, str] = {}
        for idx, val in enumerate(raw_row):
            if idx < len(headers) and headers[idx]:
                row[headers[idx]] = val.strip()

        raw_type = (row.get("type") or "").strip().lower()
        sym_raw = (row.get("symbol") or "").strip().upper()
        if not sym_raw:
            issues.append(_issue(line_no, "error", "MISSING_SYMBOL", f"第 {line_no} 行缺少标的 symbol"))
            return

        sym = _CN_TICKER_ALIASES.get(sym_raw, (sym_raw, ""))[0]
        is_deriv = raw_type in ("perp", "derivative", "futures", "swap", "合约", "永续") or sym.endswith(("-SWAP", "-PERP"))
        is_cash = (
            raw_type in ("cash", "现金")
            or (row.get("is_cash") or "").strip().lower() in ("1", "true", "yes", "是")
            or (not is_deriv and sym in _CASH_SYMBOLS and raw_type not in ("spot", "equity"))
        )

        qty_raw = _strip_thousand_commas(row.get("qty") or "")
        try:
            qty_str = parse_decimal_str(qty_raw)
        except ValueError:
            issues.append(_issue(line_no, "error", "INVALID_QTY", f"第 {line_no} 行数量格式不合法: {qty_raw!r}"))
            return

        if Decimal(qty_str) < 0 or (is_deriv and Decimal(qty_str) == 0):
            issues.append(_issue(line_no, "error", "INVALID_QTY", f"第 {line_no} 行数量超出允许范围"))
            return

        sleeve = _detect_sleeve(row.get("sleeve") or "")
        stop_loss = parse_decimal_str(_strip_thousand_commas(row["stop_loss"])) if row.get("stop_loss") else None
        take_profit = parse_decimal_str(_strip_thousand_commas(row["take_profit"])) if row.get("take_profit") else None

        if is_deriv:
            raw_side = (row.get("side") or "").strip().lower()
            if raw_side in ("long", "多", "做多"):
                side = "long"
            elif raw_side in ("short", "空", "做空"):
                side = "short"
            else:
                issues.append(_issue(line_no, "error", "MISSING_SIDE", f"第 {line_no} 行合约缺少方向(long/short)"))
                return

            raw_unit = (row.get("qty_unit") or "").strip().lower()
            if raw_unit in ("coin", "币", "个", "枚"):
                qty_unit = "coin"
            elif raw_unit in ("contract", "contracts", "张"):
                qty_unit = "contract"
            else:
                issues.append(
                    _issue(
                        line_no,
                        "error",
                        "AMBIGUOUS_QTY_UNIT",
                        f"第 {line_no} 行合约必须显式填写 qty_unit ('coin' 或 'contract')，严禁混用币数与张数",
                    )
                )
                return

            raw_ct = (row.get("contract_type") or "").strip().lower()
            if raw_ct in ("inverse", "反向", "币本位") or sym.endswith("-USD-SWAP"):
                contract_type = "inverse"
            else:
                contract_type = "linear"

            iid = sym if sym.endswith(("-SWAP", "-PERP")) else f"{sym}-{'USD' if contract_type == 'inverse' else 'USDT'}-SWAP"
            base_sym = iid.split("-")[0]

            raw_mult = _strip_thousand_commas(row.get("multiplier") or "")
            if qty_unit == "coin":
                mult_str = parse_decimal_str(raw_mult) if raw_mult else "1"
            else:
                if raw_mult:
                    mult_str = parse_decimal_str(raw_mult)
                elif iid in KNOWN_SWAP_MULTIPLIERS:
                    mult_str = KNOWN_SWAP_MULTIPLIERS[iid]["ctVal"]
                else:
                    issues.append(
                        _issue(
                            line_no,
                            "error",
                            "UNKNOWN_CONTRACT_MULTIPLIER",
                            f"第 {line_no} 行张数合约 {iid} 缺少已知合约乘数 (multiplier)",
                        )
                    )
                    return

            try:
                entry_str = parse_decimal_str(_strip_thousand_commas(row.get("entry_price") or ""))
                if Decimal(entry_str) <= 0:
                    raise ValueError("entry <= 0")
            except ValueError:
                issues.append(_issue(line_no, "error", "MISSING_ENTRY_PRICE", f"第 {line_no} 行合约缺少有效开仓价"))
                return

            raw_mm = (row.get("margin_mode") or "cross").strip().lower()
            margin_mode = "isolated" if raw_mm in ("isolated", "逐仓") else "cross"
            margin_str = parse_decimal_str(_strip_thousand_commas(row.get("margin") or "0") or "0")
            quote_ccy = derivative_quote_currency(iid, contract_type)
            margin_ccy = base_sym if contract_type == "inverse" else quote_ccy

            op_data: dict[str, Any] = {
                "account_id": account_id,
                "instrument_id": iid,
                "symbol": iid,
                "base_asset": base_sym,
                "asset_class": "perp",
                "quote_currency": quote_ccy,
                "side": side,
                "margin_mode": margin_mode,
                "contract_type": contract_type,
                "qty": qty_str,
                "qty_unit": qty_unit,
                "contract_multiplier": mult_str,
                "entry_price": entry_str,
                "margin_usd": margin_str,
                "margin_amount": margin_str,
                "margin_currency": margin_ccy,
                "sleeve": sleeve,
                "source": "import",
            }
            if row.get("leverage"):
                op_data["leverage"] = parse_decimal_str(_strip_thousand_commas(row["leverage"]))
            if row.get("mmr"):
                op_data["mmr"] = _parse_pct_or_dec(row["mmr"])
            if row.get("venue_liq"):
                op_data["venue_liq"] = parse_decimal_str(_strip_thousand_commas(row["venue_liq"]))
            if stop_loss is not None:
                op_data["stop_loss"] = stop_loss
            if take_profit is not None:
                op_data["take_profit"] = take_profit

            parsed_items.append(
                {
                    "kind": "derivative",
                    "line_no": line_no,
                    "key": f"deriv:{account_id}:{iid}:{side}:{margin_mode}",
                    "op": {"op": "put", "entity": "derivative", "data": op_data},
                }
            )
        else:
            cost_raw = _strip_thousand_commas(row.get("entry_price") or "")
            avg_cost = parse_decimal_str(cost_raw) if cost_raw else ("1" if is_cash else "0")
            locked_str = parse_decimal_str(_strip_thousand_commas(row.get("locked_margin_usd") or "0") or "0")
            asset_cls = "cash" if is_cash else ("crypto_spot" if sym in _KNOWN_CRYPTO_BASES else "equity")
            op_data = {
                "account_id": account_id,
                "instrument_id": sym,
                "symbol": sym,
                "asset_class": asset_cls,
                "quote_currency": ("CNY" if sym == "CNH" else sym) if is_cash else "USD",
                "is_cash": is_cash,
                "qty": qty_str,
                "avg_cost": avg_cost,
                "locked_margin_usd": locked_str,
                "sleeve": sleeve,
                "source": "import",
            }
            if stop_loss is not None:
                op_data["stop_loss"] = stop_loss
            if take_profit is not None:
                op_data["take_profit"] = take_profit
            parsed_items.append(
                {
                    "kind": "spot",
                    "line_no": line_no,
                    "key": f"spot:{account_id}:{sym}",
                    "op": {"op": "put", "entity": "spot", "data": op_data},
                }
            )

    for line_no, raw_row in data_rows:
        try:
            parse_row(line_no, raw_row)
        except ValueError as exc:
            issues.append(_issue(line_no, "error", "PARSE_ERROR", str(exc)))

    return (parsed_items, issues)


def _split_text_clauses(canonical_text: str) -> list[tuple[int, str]]:
    clauses: list[tuple[int, str]] = []
    for line_no, line in enumerate(canonical_text.splitlines(), start=1):
        clean_ln = _strip_thousand_commas(line)
        parts = re.split(r"[;；。\n]+", clean_ln)
        expanded: list[str] = []
        for part in parts:
            sub = [s.strip() for s in re.split(r"[，,]+", part) if s.strip()]
            expanded.extend(sub)
        for p in expanded:
            clauses.append((line_no, p))
    return clauses


def _looks_like_csv(canonical_text: str) -> bool:
    lines = canonical_text.splitlines()
    if not lines:
        return False
    first_cells = [c.strip().lower() for c in next(csv.reader([lines[0]]))]
    if sum(1 for c in first_cells if c in _CSV_HEADER_ALIASES) >= 2:
        return True
    if len(lines) >= 2 and all(len(next(csv.reader([ln]))) >= 3 for ln in lines[:2]):
        if not any(ch in lines[0] for ch in ("做多", "做空", "开仓", "钱包", "现金", "股")):
            return True
    return False


def _resolve_account(store: PortfolioStore, account_id: str | None) -> dict[str, Any]:
    raw = str(account_id or "").strip()
    with store._lock, store._connect("read") as db:
        account = store._find_account_in_tx(db, raw)
        if account is not None:
            return dict(account)
    if not raw or raw.lower() in ("main", "default") or raw in ("主账户", DEFAULT_ACCOUNT_ID):
        return store.ensure_default_account()
    raise ValueError(f"unknown account_id: {account_id!r}")


def preview_import(
    store: PortfolioStore,
    account_id: str | None,
    fmt: str,
    text: str,
    mode: str,
    actor_id: str,
    *,
    ttl_seconds: int = DEFAULT_PREVIEW_TTL_SECONDS,
) -> dict[str, Any]:
    """Parse text/CSV positions, validate coin-vs-contract & mode, compute diff, and persist 10m preview."""
    fmt_norm = str(fmt or "auto").strip().lower()
    if fmt_norm not in ("auto", "text", "csv"):
        raise ValueError(f"unsupported import format: {fmt!r}")
    mode_norm = str(mode or "upsert").strip().lower()
    if mode_norm not in ("upsert", "reconcile"):
        raise ValueError(f"unsupported import mode: {mode!r}")
    act_id = str(actor_id or "").strip()
    if not act_id:
        raise ValueError("actor_id is required")

    account = _resolve_account(store, account_id)
    aid = str(account["id"])

    source_text = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    canonical_text = _canonicalize_body(source_text)
    body_sha256 = hashlib.sha256(canonical_text.encode("utf-8")).hexdigest()

    with store._lock, store._connect("read") as db:
        rev_row = db.execute("SELECT value FROM meta WHERE key='portfolio_revision'").fetchone()
        expected_revision = int(rev_row["value"]) if rev_row and rev_row["value"] is not None else 0
        existing_spots = {
            f"spot:{aid}:{str(r['instrument_id']).upper()}": dict(r)
            for r in db.execute("SELECT * FROM spot_balance WHERE account_id=?", (aid,)).fetchall()
        }
        existing_derivs = [
            dict(r)
            for r in db.execute("SELECT * FROM derivative_position WHERE account_id=?", (aid,)).fetchall()
        ]

    resolved_fmt = fmt_norm
    if resolved_fmt == "auto":
        resolved_fmt = "csv" if _looks_like_csv(canonical_text) else "text"

    parsed_items: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []

    if not canonical_text:
        issues.append(_issue(1, "error", "EMPTY_INPUT", "导入文本为空"))
    elif resolved_fmt == "csv":
        parsed_items, issues = _parse_csv_rows(source_text, aid)
    else:
        for line_no, clause in _split_text_clauses(source_text):
            try:
                item, cl_issues = _parse_text_clause(clause, line_no, aid)
            except ValueError as exc:
                issues.append(_issue(line_no, "error", "PARSE_ERROR", str(exc)))
                continue
            issues.extend(cl_issues)
            if item is not None:
                parsed_items.append({**item, "line_no": line_no})
        if not parsed_items and not any(i["severity"] == "error" for i in issues):
            issues.append(_issue(1, "error", "NO_POSITIONS_PARSED", "未能解析出任何有效持仓"))

    # Match existing rows & build canonical_ops + diff
    existing_deriv_by_key: dict[str, list[dict[str, Any]]] = {}
    for d in existing_derivs:
        dkey = f"deriv:{aid}:{str(d['instrument_id']).upper()}:{d['side']}:{d['margin_mode']}"
        existing_deriv_by_key.setdefault(dkey, []).append(d)

    canonical_ops: list[dict[str, Any]] = []
    op_lines: list[int] = []
    added: list[dict[str, Any]] = []
    updated: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    matched_spot_ids: set[str] = set()
    matched_deriv_ids: set[str] = set()
    seen_spot_keys: set[str] = set()

    for item in parsed_items:
        op = dict(item["op"])
        data = dict(op["data"])
        if item["kind"] == "spot":
            if item["key"] in seen_spot_keys:
                issues.append(
                    _issue(
                        item["line_no"],
                        "error",
                        "DUPLICATE_SPOT_INSTRUMENT",
                        f"同一导入批次存在重复现货标的: {data['instrument_id']}",
                    )
                )
                continue
            seen_spot_keys.add(item["key"])
            ex = existing_spots.get(item["key"])
            if ex is not None:
                op["id"] = ex["id"]
                matched_spot_ids.add(str(ex["id"]))
                updated.append({"entity": "spot", "id": ex["id"], "instrument_id": data["instrument_id"], "qty": data["qty"]})
            else:
                added.append({"entity": "spot", "instrument_id": data["instrument_id"], "qty": data["qty"]})
            canonical_ops.append(op)
            op_lines.append(item["line_no"])
        else:
            candidates = [
                d for d in existing_deriv_by_key.get(item["key"], []) if str(d["id"]) not in matched_deriv_ids
            ]
            if candidates:
                ex = candidates[0]
                op["id"] = ex["id"]
                matched_deriv_ids.add(str(ex["id"]))
                updated.append(
                    {
                        "entity": "derivative",
                        "id": ex["id"],
                        "instrument_id": data["instrument_id"],
                        "side": data["side"],
                        "qty": data["qty"],
                        "qty_unit": data["qty_unit"],
                    }
                )
            else:
                added.append(
                    {
                        "entity": "derivative",
                        "instrument_id": data["instrument_id"],
                        "side": data["side"],
                        "qty": data["qty"],
                        "qty_unit": data["qty_unit"],
                    }
                )
            canonical_ops.append(op)
            op_lines.append(item["line_no"])

    has_errors = any(i["severity"] == "error" for i in issues)
    if mode_norm == "reconcile" and not has_errors and canonical_ops:
        for ex in existing_spots.values():
            if str(ex["id"]) not in matched_spot_ids:
                canonical_ops.append({"op": "remove", "entity": "spot", "id": ex["id"]})
                removed.append({"entity": "spot", "id": ex["id"], "instrument_id": ex["instrument_id"], "qty": ex["qty"]})
        for ex in existing_derivs:
            if str(ex["id"]) not in matched_deriv_ids:
                canonical_ops.append({"op": "remove", "entity": "derivative", "id": ex["id"]})
                removed.append(
                    {
                        "entity": "derivative",
                        "id": ex["id"],
                        "instrument_id": ex["instrument_id"],
                        "side": ex["side"],
                        "qty": ex["qty"],
                    }
                )

    diff = {
        "added": added,
        "updated": updated,
        "removed": removed,
        "counts": {"added": len(added), "updated": len(updated), "removed": len(removed)},
    }

    now_dt = _now_dt()
    created_at = now_dt.isoformat(timespec="seconds")
    expires_at = (now_dt + timedelta(seconds=max(1, int(ttl_seconds)))).isoformat(timespec="seconds")
    preview_id = f"prev_{uuid.uuid4().hex[:16]}"

    with store._lock, store._connect("immediate") as db:
        if canonical_ops:
            try:
                store._validate_ops(canonical_ops)
            except ValueError as exc:
                issues.append(_issue(1, "error", "INVALID_OPERATION", str(exc)))
            else:
                db.execute("SAVEPOINT validate_import")
                try:
                    for index, op in enumerate(canonical_ops):
                        db.execute("SAVEPOINT validate_import_op")
                        try:
                            store._apply_one_op(db, op)
                        except (ValueError, sqlite3.IntegrityError) as exc:
                            line_no = op_lines[index] if index < len(op_lines) else 1
                            issues.append(_issue(line_no, "error", "INVALID_OPERATION", str(exc)))
                            db.execute("ROLLBACK TO validate_import_op")
                        finally:
                            db.execute("RELEASE validate_import_op")
                finally:
                    db.execute("ROLLBACK TO validate_import")
                    db.execute("RELEASE validate_import")
        has_errors = any(i["severity"] == "error" for i in issues)
        db.execute(
            """INSERT INTO import_preview
               (id,actor_id,account_id,format,mode,body_sha256,canonical_ops_json,issues_json,
                diff_json,expected_revision,expires_at,committed_at,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                preview_id,
                act_id,
                aid,
                resolved_fmt,
                mode_norm,
                body_sha256,
                json.dumps(canonical_ops, ensure_ascii=False),
                json.dumps(issues, ensure_ascii=False),
                json.dumps(diff, ensure_ascii=False),
                expected_revision,
                expires_at,
                None,
                created_at,
            ),
        )

    store.log_audit_event(
        actor_id=act_id,
        role="PORTFOLIO_RW",
        action="import_preview",
        resource=f"account:{aid}",
        outcome="ok" if not has_errors else "validation_error",
        detail={
            "preview_id": preview_id,
            "format": resolved_fmt,
            "mode": mode_norm,
            "ops_count": len(canonical_ops),
            "error_count": sum(1 for i in issues if i["severity"] == "error"),
            "revision": expected_revision,
        },
    )

    return {
        "preview_id": preview_id,
        "account_id": aid,
        "account_name": account["name"],
        "actor_id": act_id,
        "format": resolved_fmt,
        "mode": mode_norm,
        "body_sha256": body_sha256,
        "expected_revision": expected_revision,
        "expires_at": expires_at,
        "created_at": created_at,
        "issues": issues,
        "has_errors": has_errors,
        "can_commit": (not has_errors) and len(canonical_ops) > 0,
        "ops_count": len(canonical_ops),
        "diff": diff,
        "canonical_ops": canonical_ops,
        "ops": canonical_ops,
    }


def commit_import(
    store: PortfolioStore,
    preview_id: str,
    request_id: str,
    expected_revision: int,
    actor_id: str,
    *,
    role: str = "PORTFOLIO_RW",
) -> dict[str, Any]:
    """Commit a non-expired, error-free preview atomically via PortfolioStore.apply_changes."""
    if role != "PORTFOLIO_RW":
        raise PermissionError("PORTFOLIO_RW role required to commit import")

    pid = str(preview_id or "").strip()
    req_id = str(request_id or "").strip()
    act_id = str(actor_id or "").strip()
    if not pid or not req_id or not act_id:
        raise ValueError("preview_id, request_id, and actor_id are required")
    exp_rev = int(expected_revision)

    with store._lock, store._connect("read") as db:
        row = db.execute("SELECT * FROM import_preview WHERE id=?", (pid,)).fetchone()
        if row is None:
            raise KeyError(f"import_preview not found: {pid!r}")
        preview = dict(row)

    if str(preview["actor_id"]) != act_id:
        raise PermissionError("actor_id does not own this import preview")

    ops = json.loads(preview["canonical_ops_json"] or "[]")
    issues = json.loads(preview["issues_json"] or "[]")
    if any(i.get("severity") == "error" for i in issues) or not ops:
        raise PreviewValidationError("import preview has blocking validation errors or zero ops")

    res = store.apply_changes(
        request_id=req_id,
        expected_revision=exp_rev,
        ops=ops,
        actor_id=act_id,
        role=role,
        summary=f"import:{preview['mode']}:{preview['format']}",
        _commit_preview_id=pid,
    )
    res["preview_id"] = pid
    res["body_sha256"] = preview["body_sha256"]
    return res
