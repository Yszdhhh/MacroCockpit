"""Pure-Decimal portfolio valuation, liquidation estimation, strategy comparison, and FX projection."""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from typing import Any

_DECIMAL_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")
_MAX_SIG_DIGITS = 38
_MAX_DECIMALS = 18
# A valuation can multiply four bounded inputs before display conversion.
_CALC_PREC = 5 * _MAX_SIG_DIGITS + _MAX_DECIMALS

CORE_SLEEVES: tuple[tuple[str, str], ...] = (
    ("btc_trend", "核心趋势仓 (BTC)"),
    ("gold_hedge", "黄金避险仓"),
    ("bear_basket", "熊市配对对冲仓"),
    ("crash_bounce", "超跌反弹短打仓"),
)
CORE_SLEEVE_IDS = frozenset(s[0] for s in CORE_SLEEVES)


def parse_decimal_str(v: Any) -> str:
    """Validate and normalize a strict decimal string (no NaN/Inf/exponent, -0 -> '0', <=38 sig / <=18 dec)."""
    if v is None or isinstance(v, (bool, float)):
        raise ValueError(f"invalid decimal input type: {type(v).__name__}")

    if isinstance(v, int):
        raw = str(v)
    elif isinstance(v, Decimal):
        if v.is_nan() or v.is_infinite():
            raise ValueError("NaN and Infinity are not allowed")
        raw = format(v, "f")
    elif isinstance(v, str):
        raw = v.strip()
    else:
        raise ValueError(f"unsupported decimal input type: {type(v).__name__}")

    if not raw or not _DECIMAL_RE.fullmatch(raw):
        raise ValueError(f"invalid decimal string: {v!r}")

    neg = raw.startswith("-")
    body = raw[1:] if raw[0] in "+-" else raw
    if "." in body:
        int_part, frac_part = body.split(".", 1)
    else:
        int_part, frac_part = body, ""

    if isinstance(v, str) and len(frac_part) > _MAX_DECIMALS:
        raise ValueError(f"decimal exceeds {_MAX_DECIMALS} fractional digits: {v!r}")

    int_stripped = int_part.lstrip("0") or "0"
    frac_stripped = frac_part.rstrip("0")

    if len(frac_stripped) > _MAX_DECIMALS:
        raise ValueError(f"decimal exceeds {_MAX_DECIMALS} fractional digits: {v!r}")

    if int_stripped == "0" and frac_stripped == "":
        return "0"

    all_digits = ("" if int_stripped == "0" else int_stripped) + frac_stripped
    sig_digits = len(all_digits.lstrip("0"))
    if sig_digits > _MAX_SIG_DIGITS:
        raise ValueError(f"decimal exceeds {_MAX_SIG_DIGITS} significant digits: {v!r}")

    # Verify with Decimal under high precision context
    try:
        with localcontext() as ctx:
            ctx.prec = _CALC_PREC
            _ = Decimal(("-" if neg else "") + int_stripped + ("." + frac_stripped if frac_stripped else ""))
    except InvalidOperation as exc:
        raise ValueError(f"invalid decimal value: {v!r}") from exc

    return ("-" if neg else "") + int_stripped + ("." + frac_stripped if frac_stripped else "")


def to_decimal(v: Any) -> Decimal:
    """Convert validated input into a Decimal without float intermediate precision loss."""
    return Decimal(parse_decimal_str(v))


def round_decimal(val: Decimal | None, places: int = 2) -> Decimal | None:
    """Round Decimal using ROUND_HALF_UP and normalize -0 to 0."""
    if val is None:
        return None
    with localcontext() as ctx:
        ctx.prec = max(_CALC_PREC, val.adjusted() + places + 2)
        ctx.rounding = ROUND_HALF_UP
        q = val.quantize(Decimal(10) ** (-places))
        if q == 0:
            return Decimal("0")
        return q


def decimal_str(val: Decimal | None, places: int | None = None) -> str | None:
    """Serialize Decimal cleanly without scientific notation or -0."""
    if val is None:
        return None
    with localcontext() as ctx:
        ctx.prec = _CALC_PREC
        ctx.rounding = ROUND_HALF_UP
        d = round_decimal(val, places) if places is not None else val
        assert d is not None
        if d == 0:
            return "0"
        s = format(d, "f")
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        return "0" if s in ("-0", "") else s


def _opt_decimal(v: Any) -> Decimal | None:
    if v in (None, ""):
        return None
    return to_decimal(v)


def _quote_price_decimal(entry: Any) -> Decimal | None:
    if entry is None or isinstance(entry, (bool, float)):
        return None
    if isinstance(entry, dict):
        for k in ("mark_price", "price", "last_price", "rate"):
            val = entry.get(k)
            if val not in (None, ""):
                try:
                    return to_decimal(val)
                except ValueError:
                    continue
        return None
    try:
        return to_decimal(entry)
    except ValueError:
        return None


def _quoted_rate(quotes: dict[str, Any], base: str, currency: str) -> Decimal | None:
    keys = (f"{base}-{currency}", base) if currency == "USD" else (f"{base}-{currency}",)
    for key in keys:
        entry = quotes.get(key)
        if isinstance(entry, dict) and str(entry.get("quote_currency") or currency).upper() != currency:
            continue
        price = _quote_price_decimal(entry)
        if price is not None and price > 0:
            return price
    return None


def _resolve_quote_currency_usd(
    quote_currency: str,
    quotes: dict[str, Any],
    fx_rate: Decimal | None,
    *,
    allow_stable_par: bool = True,
) -> Decimal | None:
    qc = (quote_currency or "USD").strip().upper()
    if qc == "USD":
        return Decimal("1")
    price = _quoted_rate(quotes, qc, "USD")
    if price is not None:
        return price
    inverse = _quoted_rate(quotes, "USD", qc)
    if inverse is None and qc in ("CNY", "CNH"):
        inverse = fx_rate
    if inverse is not None and inverse > 0:
        return Decimal("1") / inverse
    if allow_stable_par and qc in ("USDT", "USDC"):
        return Decimal("1")
    return None


def _currency_rate(
    source: str,
    target: str,
    quotes: dict[str, Any],
    fx_rate: Decimal | None,
    *,
    allow_stable_par: bool = True,
) -> Decimal | None:
    if source == target or {source, target} == {"CNY", "CNH"}:
        return Decimal("1")
    with localcontext() as ctx:
        ctx.prec = _CALC_PREC
        direct = _quoted_rate(quotes, source, target)
        if direct is not None:
            return direct
        inverse = _quoted_rate(quotes, target, source)
        if inverse is not None:
            return Decimal("1") / inverse
        source_usd = _resolve_quote_currency_usd(source, quotes, fx_rate, allow_stable_par=allow_stable_par)
        target_usd = _resolve_quote_currency_usd(target, quotes, fx_rate, allow_stable_par=allow_stable_par)
        if source_usd is not None and target_usd is not None:
            return source_usd / target_usd
    return None


def _resolve_instrument_price(
    *,
    instrument_id: str,
    symbol: str,
    is_cash: bool,
    quote_currency: str,
    fallback_price: Any,
    quotes: dict[str, Any],
    fx_rate: Decimal | None,
    prefer_mark: bool = False,
) -> tuple[Decimal | None, str]:
    """Resolve instrument price in its quote currency plus provenance tag."""
    iid = (instrument_id or "").strip().upper()
    sym = (symbol or iid).strip().upper()

    if is_cash:
        rate = _currency_rate(iid, quote_currency, quotes, fx_rate)
        if rate is not None:
            return (rate, "par" if rate == 1 else "quote")

    base = (iid or sym).split("-")[0] if prefer_mark else sym
    quote_ccys = (quote_currency, "USDT", "USD", "USDC")
    swaps = [f"{base}-{ccy}-SWAP" for ccy in quote_ccys]
    spots = [iid, sym, base, *(f"{base}-{ccy}" for ccy in quote_ccys)]
    if prefer_mark:
        exact = [iid] if iid.endswith(("-SWAP", "-PERP")) else []
        candidates = [*exact, *swaps, *spots]
    else:
        candidates = [*spots, *swaps]

    for key in dict.fromkeys(candidates):
        entry = quotes.get(key)
        price = _quote_price_decimal(entry)
        if price is None or price <= 0:
            continue
        parts = key.split("-")
        inferred_ccy = parts[1] if len(parts) > 1 else quote_currency
        entry_ccy = str((entry.get("quote_currency") if isinstance(entry, dict) else None) or inferred_ccy).upper()
        rate = _currency_rate(entry_ccy, quote_currency, quotes, fx_rate)
        if rate is not None:
            return (price * rate, "quote")

    fb = _opt_decimal(fallback_price)
    if fb is not None and fb > 0:
        return (fb, "position_fallback")

    return (None, "missing")


def _derivative_quote_currency(instrument_id: str, contract_type: str) -> str:
    parts = instrument_id.strip().upper().rsplit("-", 2)
    if len(parts) == 3 and parts[-1] in ("SWAP", "PERP"):
        return parts[-2]
    return "USD" if contract_type == "inverse" else "USDT"


def compute_liquidation_price(
    position: dict[str, Any],
    mark_price: Decimal | None,
    *,
    quotes: dict[str, Any] | None = None,
    fx_rate: Decimal | None = None,
) -> dict[str, Any]:
    """Compute liquidation price and safety cushion % following Astra strict rules."""
    side = str(position.get("side") or "").strip().lower()
    if side not in ("long", "short"):
        raise ValueError(f"invalid derivative side: {side!r}")
    s = Decimal("1") if side == "long" else Decimal("-1")

    venue_liq_raw = position.get("venue_liq")
    liq_asof = str(position.get("liq_asof") or "").strip()
    if venue_liq_raw not in (None, ""):
        venue_liq = to_decimal(venue_liq_raw)
        if venue_liq <= 0:
            raise ValueError("venue_liq must be > 0")
        dist_pct: Decimal | None = None
        if mark_price is not None and mark_price > 0:
            with localcontext() as ctx:
                ctx.prec = _CALC_PREC
                ctx.rounding = ROUND_HALF_UP
                dist_pct = s * (mark_price - venue_liq) / mark_price * Decimal("100")
        return {
            "liq_price": venue_liq,
            "liq_distance_pct": round_decimal(dist_pct, 2),
            "liq_asof": liq_asof,
            "liq_source": "venue",
            "liq_status": "交易所强平价",
        }

    margin_mode = str(position.get("margin_mode") or "cross").strip().lower()
    contract_type = str(position.get("contract_type") or "linear").strip().lower()
    instrument = str(position.get("instrument_id") or position.get("symbol") or "")
    quote_ccy = str(position.get("quote_currency") or _derivative_quote_currency(instrument, contract_type)).strip().upper()
    margin_ccy = str(position.get("margin_currency") or quote_ccy).strip().upper()

    mmr_raw = position.get("mmr")
    margin_raw = position.get("margin_amount")
    if margin_raw in (None, ""):
        margin_raw = position.get("margin_usd")

    if (
        margin_mode == "isolated"
        and contract_type == "linear"
        and mmr_raw not in (None, "")
        and margin_raw not in (None, "")
    ):
        mmr = to_decimal(mmr_raw)
        margin_amt = to_decimal(margin_raw)
        if not (Decimal("0") <= mmr < Decimal("1")):
            raise ValueError(f"mmr must be in [0, 1): {mmr}")
        margin_rate = _currency_rate(margin_ccy, quote_ccy, quotes or {}, fx_rate, allow_stable_par=False)
        if margin_amt > 0 and margin_rate is not None:
            Q = to_decimal(position["qty"])
            qty_unit = str(position.get("qty_unit") or "contract").strip().lower()
            raw_m = position.get("contract_multiplier", position.get("multiplier", "1"))
            eff_m = Decimal("1") if qty_unit == "coin" else to_decimal(raw_m)
            entry = to_decimal(position["entry_price"])
            if Q > 0 and eff_m > 0 and entry > 0:
                with localcontext() as ctx:
                    ctx.prec = _CALC_PREC
                    ctx.rounding = ROUND_HALF_UP
                    size = Q * eff_m
                    margin_amt *= margin_rate
                    if side == "long":
                        denom = size * (Decimal("1") - mmr)
                        pliq = (size * entry - margin_amt) / denom
                    else:
                        denom = size * (Decimal("1") + mmr)
                        pliq = (size * entry + margin_amt) / denom

                    if pliq <= 0:
                        return {
                            "liq_price": None,
                            "liq_distance_pct": None,
                            "liq_asof": "",
                            "liq_source": "estimated_isolated",
                            "liq_status": "全额担保无强平价",
                        }
                    dist_pct = None
                    if mark_price is not None and mark_price > 0:
                        dist_pct = s * (mark_price - pliq) / mark_price * Decimal("100")
                    return {
                        "liq_price": pliq,
                        "liq_distance_pct": round_decimal(dist_pct, 2),
                        "liq_asof": "",
                        "liq_source": "estimated_isolated",
                        "liq_status": "逐仓精确估算",
                    }

    return {
        "liq_price": None,
        "liq_distance_pct": None,
        "liq_asof": "",
        "liq_source": "unavailable",
        "liq_status": "无法估算",
    }


def _first_present(mapping: dict[str, Any], *keys: str, default: Any = "0") -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return default


def _extract_strategy_targets(strategy_targets: dict[str, Any] | list[dict[str, Any]] | None) -> dict[str, dict[str, Decimal]]:
    defaults: dict[str, dict[str, Decimal]] = {
        "btc_trend": {"target_pct": Decimal("0")},
        "gold_hedge": {"target_pct": Decimal("0")},
        "bear_basket": {"target_pct": Decimal("0"), "long_pct": Decimal("0"), "short_pct": Decimal("0"), "net_pct": Decimal("0")},
        "crash_bounce": {"target_pct": Decimal("0")},
        "other": {"target_pct": Decimal("0")},
    }
    if not strategy_targets:
        return defaults

    src: dict[str, Any]
    if isinstance(strategy_targets, list):
        src = {str(item.get("id") or ""): item for item in strategy_targets if isinstance(item, dict)}
    elif isinstance(strategy_targets, dict):
        if isinstance(strategy_targets.get("by_id"), dict) and strategy_targets["by_id"]:
            src = strategy_targets["by_id"]
        elif isinstance(strategy_targets.get("targets"), list):
            src = {str(item.get("id") or ""): item for item in strategy_targets["targets"] if isinstance(item, dict)}
        elif isinstance(strategy_targets.get("sleeves"), dict):
            src = strategy_targets["sleeves"]
        else:
            src = strategy_targets
    else:
        return defaults

    for sid in CORE_SLEEVE_IDS:
        item = src.get(sid)
        if not isinstance(item, dict):
            continue
        if sid == "bear_basket":
            t_pct = to_decimal(str(_first_present(item, "recommended_pct", "target_pct", "net_target_pct", "net_pct")))
            legs = item.get("legs")
            if "long_pct" in item or "short_pct" in item:
                l_pct = to_decimal(str(_first_present(item, "long_pct")))
                s_pct = to_decimal(str(_first_present(item, "short_pct")))
                n_pct = to_decimal(str(_first_present(item, "net_target_pct", "net_pct", default=l_pct + s_pct)))
            elif isinstance(legs, list) and legs:
                l_pct = Decimal("0")
                s_pct = Decimal("0")
                for leg in legs:
                    if not isinstance(leg, dict):
                        continue
                    side = str(leg.get("side") or "").strip().lower()
                    leg_pct = to_decimal(str(_first_present(leg, "target_pct", "recommended_pct")))
                    if side == "long":
                        l_pct += leg_pct
                    elif side == "short":
                        s_pct += leg_pct
                n_pct = to_decimal(str(_first_present(item, "net_target_pct", "net_pct", default=l_pct + s_pct)))
            elif t_pct > 0:
                with localcontext() as ctx:
                    ctx.prec = _CALC_PREC
                    l_pct = t_pct
                    s_pct = -(t_pct * Decimal("30") / Decimal("35"))
                    n_pct = l_pct + s_pct
            else:
                l_pct = s_pct = n_pct = Decimal("0")
            defaults["bear_basket"] = {
                "target_pct": t_pct,
                "long_pct": l_pct,
                "short_pct": s_pct,
                "net_pct": n_pct,
            }
        else:
            t_pct = to_decimal(str(_first_present(item, "recommended_pct", "target_pct")))
            defaults[sid] = {"target_pct": t_pct}
    return defaults


def evaluate_portfolio(
    *,
    spot_balances: list[dict[str, Any]],
    derivative_positions: list[dict[str, Any]],
    quotes: dict[str, Any] | None = None,
    confirmed_debt_usd: Any = "0",
    strategy_targets: dict[str, Any] | list[dict[str, Any]] | None = None,
    fx_rate: Any = None,
) -> dict[str, Any]:
    """Compute pure-Decimal wallet net equity ('total_ex_upl'), risk exposures, liquidation, and sleeve diffs."""
    qmap = quotes or {}
    debt_usd = to_decimal(confirmed_debt_usd)
    if debt_usd < 0:
        raise ValueError("confirmed_debt_usd cannot be negative")
    fx_dec = _opt_decimal(fx_rate)

    with localcontext() as ctx:
        ctx.prec = _CALC_PREC
        ctx.rounding = ROUND_HALF_UP

        cash_and_spot_usd = Decimal("0")
        cash_usd = Decimal("0")
        non_cash_spot_usd = Decimal("0")
        spot_upl_usd = Decimal("0")
        derivatives_upl_usd = Decimal("0")
        long_risk_usd = Decimal("0")
        short_risk_usd = Decimal("0")
        reported_locked_margin_usd = Decimal("0")
        reported_deriv_margin_usd = Decimal("0")
        unpriced_items: list[str] = []

        sleeve_actual: dict[str, dict[str, Decimal]] = {
            sid: {"long_usd": Decimal("0"), "short_usd": Decimal("0"), "net_usd": Decimal("0")}
            for sid in (*CORE_SLEEVE_IDS, "other")
        }

        valued_spots: list[dict[str, Any]] = []
        for row in spot_balances:
            qty = to_decimal(row.get("qty", "0"))
            avg_cost = to_decimal(row.get("avg_cost", "0") or "0")
            locked_margin = to_decimal(row.get("locked_margin_usd", "0") or "0")
            is_cash = bool(row.get("is_cash"))
            iid = str(row.get("instrument_id") or row.get("symbol") or "").strip()
            sym = str(row.get("symbol") or iid).strip()
            quote_ccy = str(row.get("quote_currency") or "USD").strip().upper()
            sleeve = str(row.get("sleeve") or "").strip()
            bucket = sleeve if sleeve in CORE_SLEEVE_IDS else "other"

            quote_to_usd = _resolve_quote_currency_usd(quote_ccy, qmap, fx_dec)
            price, price_source = _resolve_instrument_price(
                instrument_id=iid,
                symbol=sym,
                is_cash=is_cash,
                quote_currency=quote_ccy,
                fallback_price=row.get("mark_price", row.get("price")),
                quotes=qmap,
                fx_rate=fx_dec,
                prefer_mark=False,
            )

            mv_quote: Decimal | None = None
            mv_usd: Decimal | None = None
            upl_quote: Decimal | None = None
            upl_usd: Decimal | None = None

            if price is not None and quote_to_usd is not None:
                mv_quote = qty * price
                mv_usd = mv_quote * quote_to_usd
                cash_and_spot_usd += mv_usd
                if is_cash:
                    cash_usd += mv_usd
                    reported_locked_margin_usd += locked_margin
                    upl_quote = Decimal("0")
                    upl_usd = Decimal("0")
                else:
                    non_cash_spot_usd += mv_usd
                    long_risk_usd += mv_usd
                    upl_quote = qty * (price - avg_cost)
                    upl_usd = upl_quote * quote_to_usd
                    spot_upl_usd += upl_usd
                    sleeve_actual[bucket]["long_usd"] += mv_usd
                    sleeve_actual[bucket]["net_usd"] += mv_usd
            else:
                if qty != 0:
                    unpriced_items.append(iid or sym)

            valued_spots.append(
                {
                    "id": row.get("id"),
                    "account_id": row.get("account_id", "acct_default"),
                    "instrument_id": iid,
                    "symbol": sym,
                    "name": str(row.get("name") or sym),
                    "asset_class": str(row.get("asset_class") or ("cash" if is_cash else "equity")),
                    "is_cash": is_cash,
                    "qty": qty,
                    "avg_cost": avg_cost,
                    "quote_currency": quote_ccy,
                    "mark_price": price,
                    "price_source": price_source,
                    "market_value_usd": mv_usd,
                    "upl_usd": upl_usd,
                    "locked_margin_usd": locked_margin,
                    "sleeve": sleeve,
                    "stop_loss": _opt_decimal(row.get("stop_loss")),
                    "take_profit": _opt_decimal(row.get("take_profit")),
                    "thesis": str(row.get("thesis") or ""),
                    "rev": int(row.get("rev") or 0),
                }
            )

        valued_derivatives: list[dict[str, Any]] = []
        for row in derivative_positions:
            side = str(row.get("side") or "").strip().lower()
            if side not in ("long", "short"):
                raise ValueError(f"invalid derivative side: {side!r}")
            s = Decimal("1") if side == "long" else Decimal("-1")

            Q = to_decimal(row["qty"])
            if Q <= 0:
                raise ValueError("derivative qty must be > 0")
            qty_unit = str(row.get("qty_unit") or "contract").strip().lower()
            raw_m = row.get("contract_multiplier", row.get("multiplier", "1"))
            m_val = to_decimal(raw_m)
            eff_m = Decimal("1") if qty_unit == "coin" else m_val
            contract_type = str(row.get("contract_type") or "linear").strip().lower()
            if contract_type not in ("linear", "inverse"):
                raise ValueError(f"invalid contract_type: {contract_type!r}")
            margin_mode = str(row.get("margin_mode") or "cross").strip().lower()
            entry_price = to_decimal(row["entry_price"])
            if entry_price <= 0:
                raise ValueError("entry_price must be > 0")

            margin_usd = to_decimal(row.get("margin_usd", "0") or "0")
            margin_amt_raw = row.get("margin_amount")
            margin_amt = to_decimal(margin_amt_raw) if margin_amt_raw not in (None, "") else margin_usd
            reported_deriv_margin_usd += margin_usd

            iid = str(row.get("instrument_id") or row.get("symbol") or "").strip()
            sym = str(row.get("symbol") or iid).strip()
            quote_ccy = str(row.get("quote_currency") or _derivative_quote_currency(iid, contract_type)).strip().upper()
            margin_ccy = str(row.get("margin_currency") or quote_ccy).strip().upper()
            sleeve = str(row.get("sleeve") or "").strip()
            bucket = sleeve if sleeve in CORE_SLEEVE_IDS else "other"

            quote_to_usd = _resolve_quote_currency_usd(quote_ccy, qmap, fx_dec)
            mark_price, price_source = _resolve_instrument_price(
                instrument_id=iid,
                symbol=sym,
                is_cash=False,
                quote_currency=quote_ccy,
                fallback_price=row.get("mark_price"),
                quotes=qmap,
                fx_rate=fx_dec,
                prefer_mark=True,
            )

            notional_usd: Decimal | None = None
            upl_usd: Decimal | None = None
            upl_coin: Decimal | None = None

            if mark_price is not None and quote_to_usd is not None:
                if contract_type == "linear":
                    notional_quote = Q * eff_m * mark_price
                    notional_usd = notional_quote * quote_to_usd
                    upl_quote = s * Q * eff_m * (mark_price - entry_price)
                    upl_usd = upl_quote * quote_to_usd
                else:
                    if qty_unit == "coin":
                        notional_usd = Q * mark_price * quote_to_usd
                        usd_face = Q * entry_price
                    else:
                        usd_face = Q * m_val
                        notional_usd = usd_face
                    upl_coin = s * usd_face * (Decimal("1") / entry_price - Decimal("1") / mark_price)
                    upl_usd = upl_coin * mark_price * quote_to_usd

                derivatives_upl_usd += upl_usd
                if side == "long":
                    long_risk_usd += notional_usd
                    sleeve_actual[bucket]["long_usd"] += notional_usd
                    sleeve_actual[bucket]["net_usd"] += notional_usd
                else:
                    abs_notional = abs(notional_usd)
                    short_risk_usd += abs_notional
                    sleeve_actual[bucket]["short_usd"] -= abs_notional
                    sleeve_actual[bucket]["net_usd"] -= abs_notional
            else:
                unpriced_items.append(iid or sym)

            liq_info = compute_liquidation_price(
                {
                    **row,
                    "quote_currency": quote_ccy,
                    "margin_currency": margin_ccy,
                },
                mark_price,
                quotes=qmap,
                fx_rate=fx_dec,
            )

            funding_rate = _opt_decimal(row.get("funding_rate"))
            if funding_rate is None and isinstance(qmap.get(iid), dict):
                funding_rate = _opt_decimal(qmap[iid].get("funding_rate"))

            valued_derivatives.append(
                {
                    "id": row.get("id"),
                    "account_id": row.get("account_id", "acct_default"),
                    "instrument_id": iid,
                    "symbol": sym,
                    "side": side,
                    "margin_mode": margin_mode,
                    "contract_type": contract_type,
                    "qty": Q,
                    "qty_unit": qty_unit,
                    "contract_multiplier": m_val,
                    "entry_price": entry_price,
                    "mark_price": mark_price,
                    "price_source": price_source,
                    "quote_currency": quote_ccy,
                    "notional_usd": notional_usd,
                    "upl_usd": upl_usd,
                    "upl_coin": upl_coin,
                    "margin_usd": margin_usd,
                    "margin_amount": margin_amt,
                    "margin_currency": margin_ccy,
                    "leverage": _opt_decimal(row.get("leverage")),
                    "mmr": _opt_decimal(row.get("mmr")),
                    "funding_rate": funding_rate,
                    "venue_liq": _opt_decimal(row.get("venue_liq")),
                    "liq_price": liq_info["liq_price"],
                    "liq_distance_pct": liq_info["liq_distance_pct"],
                    "liq_asof": liq_info["liq_asof"],
                    "liq_source": liq_info["liq_source"],
                    "liq_status": liq_info["liq_status"],
                    "stop_loss": _opt_decimal(row.get("stop_loss")),
                    "take_profit": _opt_decimal(row.get("take_profit")),
                    "sleeve": sleeve,
                    "thesis": str(row.get("thesis") or ""),
                    "rev": int(row.get("rev") or 0),
                }
            )

        # Wallet net equity invariant ('total_ex_upl'):
        # Cash balance already includes locked margin; NEVER add margin again!
        net_equity_usd = cash_and_spot_usd + derivatives_upl_usd - debt_usd
        if net_equity_usd > 0:
            net_direction_pct = round_decimal(
                (long_risk_usd - short_risk_usd) / net_equity_usd * Decimal("100"),
                2,
            )
            effective_leverage = round_decimal(
                (long_risk_usd + short_risk_usd) / net_equity_usd,
                3,
            )
        else:
            net_direction_pct = None
            effective_leverage = None

        targets = _extract_strategy_targets(strategy_targets)
        strategy_comparison: dict[str, Any] = {}
        for sid, label in (*CORE_SLEEVES, ("other", "其他")):
            act = sleeve_actual[sid]
            tgt = targets[sid]
            if sid == "bear_basket":
                t_long_pct = tgt.get("long_pct", Decimal("0"))
                t_short_pct = tgt.get("short_pct", Decimal("0"))
                t_net_pct = tgt.get("net_pct", Decimal("0"))
                if net_equity_usd > 0:
                    a_long_pct = act["long_usd"] / net_equity_usd * Decimal("100")
                    a_short_pct = act["short_usd"] / net_equity_usd * Decimal("100")
                    a_net_pct = act["net_usd"] / net_equity_usd * Decimal("100")
                    t_long_usd = net_equity_usd * t_long_pct / Decimal("100")
                    t_short_usd = net_equity_usd * t_short_pct / Decimal("100")
                    t_net_usd = net_equity_usd * t_net_pct / Decimal("100")
                    d_long_pp = round_decimal(a_long_pct - t_long_pct, 2)
                    d_short_pp = round_decimal(a_short_pct - t_short_pct, 2)
                    d_net_pp = round_decimal(a_net_pct - t_net_pct, 2)
                else:
                    a_long_pct = a_short_pct = a_net_pct = None
                    t_long_usd = t_short_usd = t_net_usd = None
                    d_long_pp = d_short_pp = d_net_pp = None
                strategy_comparison[sid] = {
                    "id": sid,
                    "name": label,
                    "actual_pct": round_decimal(a_net_pct, 2),
                    "target_pct": round_decimal(t_net_pct, 2),
                    "delta_pp": d_net_pp,
                    "actual_usd": act["net_usd"],
                    "target_usd": t_net_usd,
                    "long_leg": {
                        "actual_pct": round_decimal(a_long_pct, 2),
                        "target_pct": round_decimal(t_long_pct, 2),
                        "delta_pp": d_long_pp,
                        "actual_usd": act["long_usd"],
                        "target_usd": t_long_usd,
                    },
                    "short_leg": {
                        "actual_pct": round_decimal(a_short_pct, 2),
                        "target_pct": round_decimal(t_short_pct, 2),
                        "delta_pp": d_short_pp,
                        "actual_usd": act["short_usd"],
                        "target_usd": t_short_usd,
                    },
                    "net": {
                        "actual_pct": round_decimal(a_net_pct, 2),
                        "target_pct": round_decimal(t_net_pct, 2),
                        "delta_pp": d_net_pp,
                        "actual_usd": act["net_usd"],
                        "target_usd": t_net_usd,
                    },
                }
            else:
                t_pct = tgt.get("target_pct", Decimal("0"))
                if net_equity_usd > 0:
                    a_pct = act["net_usd"] / net_equity_usd * Decimal("100")
                    t_usd = net_equity_usd * t_pct / Decimal("100")
                    d_pp = round_decimal(a_pct - t_pct, 2)
                else:
                    a_pct = None
                    t_usd = None
                    d_pp = None
                strategy_comparison[sid] = {
                    "id": sid,
                    "name": label,
                    "actual_pct": round_decimal(a_pct, 2),
                    "target_pct": round_decimal(t_pct, 2),
                    "delta_pp": d_pp,
                    "actual_usd": act["net_usd"],
                    "target_usd": t_usd,
                }

        return {
            "equity_mode": "total_ex_upl",
            "base_currency": "USD",
            "net_equity": net_equity_usd,
            "net_equity_usd": net_equity_usd,
            "cash_and_spot_usd": cash_and_spot_usd,
            "cash_usd": cash_usd,
            "non_cash_spot_usd": non_cash_spot_usd,
            "spot_upl_usd": spot_upl_usd,
            "derivatives_upl_usd": derivatives_upl_usd,
            "confirmed_debt_usd": debt_usd,
            "long_risk": long_risk_usd,
            "long_risk_usd": long_risk_usd,
            "short_risk": short_risk_usd,
            "short_risk_usd": short_risk_usd,
            "reported_margin_usd": reported_deriv_margin_usd or reported_locked_margin_usd,
            "net_direction_pct": net_direction_pct,
            "effective_leverage": effective_leverage,
            "spot_balances": valued_spots,
            "derivative_positions": valued_derivatives,
            "strategy_comparison": strategy_comparison,
            "unpriced_items": sorted(set(unpriced_items)),
        }


def project_valuation_currency(
    valuation_result: dict[str, Any],
    *,
    currency: str = "USD",
    fx_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Project portfolio equities/margins/notionals/UPL/budgets to USD or CNY without altering unit prices or %."""
    req_ccy = (currency or "USD").strip().upper()
    if req_ccy == "RMB":
        req_ccy = "CNY"

    if req_ccy == "USD":
        scale: Decimal | None = Decimal("1")
        display_ccy = "USD"
        fx_status = "ok"
        fx_label = "美元基准"
        fx_rate_dec = Decimal("1")
    elif req_ccy == "CNY":
        raw_rate = (fx_snapshot or {}).get("rate")
        status = str((fx_snapshot or {}).get("status") or "")
        rate_dec = _opt_decimal(raw_rate)
        if status == "unavailable" or rate_dec is None or rate_dec <= 0:
            scale = None
            display_ccy = "CNY"
            fx_status = "unavailable"
            fx_label = "人民币估值不可用"
            fx_rate_dec = None
        else:
            scale = rate_dec
            display_ccy = "CNY"
            fx_status = status or "ok"
            fx_label = str((fx_snapshot or {}).get("label") or "人民币估值")
            fx_rate_dec = rate_dec
    else:
        raise ValueError(f"unsupported display currency: {currency!r}")

    with localcontext() as ctx:
        ctx.prec = _CALC_PREC
        ctx.rounding = ROUND_HALF_UP

        def proj(v: Decimal | None, places: int | None = 2) -> str | None:
            if v is None or scale is None:
                return None
            return decimal_str(v * scale, places=places)

        def raw_dec(v: Decimal | None, places: int | None = None) -> str | None:
            if v is None:
                return None
            return decimal_str(v, places=places)

        spots_out: list[dict[str, Any]] = []
        for s in valuation_result["spot_balances"]:
            spots_out.append(
                {
                    "id": s["id"],
                    "account_id": s["account_id"],
                    "instrument_id": s["instrument_id"],
                    "symbol": s["symbol"],
                    "name": s["name"],
                    "asset_class": s["asset_class"],
                    "is_cash": s["is_cash"],
                    "qty": raw_dec(s["qty"]),
                    "avg_cost": raw_dec(s["avg_cost"]),
                    "quote_currency": s["quote_currency"],
                    "mark_price": raw_dec(s["mark_price"]),
                    "price_source": s["price_source"],
                    "market_value": proj(s["market_value_usd"]),
                    "market_value_usd": raw_dec(s["market_value_usd"], 2),
                    "upl": proj(s["upl_usd"]),
                    "upl_usd": raw_dec(s["upl_usd"], 2),
                    "locked_margin": proj(s["locked_margin_usd"]),
                    "locked_margin_usd": raw_dec(s["locked_margin_usd"], 2),
                    "sleeve": s["sleeve"],
                    "stop_loss": raw_dec(s["stop_loss"]),
                    "take_profit": raw_dec(s["take_profit"]),
                    "thesis": s["thesis"],
                    "rev": s["rev"],
                }
            )

        derivs_out: list[dict[str, Any]] = []
        for d in valuation_result["derivative_positions"]:
            derivs_out.append(
                {
                    "id": d["id"],
                    "account_id": d["account_id"],
                    "instrument_id": d["instrument_id"],
                    "symbol": d["symbol"],
                    "side": d["side"],
                    "margin_mode": d["margin_mode"],
                    "contract_type": d["contract_type"],
                    "qty": raw_dec(d["qty"]),
                    "qty_unit": d["qty_unit"],
                    "contract_multiplier": raw_dec(d["contract_multiplier"]),
                    "entry_price": raw_dec(d["entry_price"]),
                    "mark_price": raw_dec(d["mark_price"]),
                    "price_source": d["price_source"],
                    "quote_currency": d["quote_currency"],
                    "notional": proj(d["notional_usd"]),
                    "notional_usd": raw_dec(d["notional_usd"], 2),
                    "upl": proj(d["upl_usd"]),
                    "upl_usd": raw_dec(d["upl_usd"], 2),
                    "upl_coin": raw_dec(d["upl_coin"]),
                    "margin": proj(d["margin_usd"]),
                    "margin_usd": raw_dec(d["margin_usd"], 2),
                    "margin_amount": raw_dec(d["margin_amount"]),
                    "margin_currency": d["margin_currency"],
                    "leverage": raw_dec(d["leverage"]),
                    "mmr": raw_dec(d["mmr"]),
                    "funding_rate": raw_dec(d["funding_rate"]),
                    "venue_liq": raw_dec(d.get("venue_liq")),
                    "liq_price": raw_dec(d["liq_price"]),
                    "liq_distance_pct": raw_dec(d["liq_distance_pct"], 2),
                    "liq_asof": d["liq_asof"],
                    "liq_source": d["liq_source"],
                    "liq_status": d["liq_status"],
                    "stop_loss": raw_dec(d["stop_loss"]),
                    "take_profit": raw_dec(d["take_profit"]),
                    "sleeve": d["sleeve"],
                    "thesis": d["thesis"],
                    "rev": d["rev"],
                }
            )

        strat_out: dict[str, Any] = {}
        for sid, item in valuation_result["strategy_comparison"].items():
            entry: dict[str, Any] = {
                "id": item["id"],
                "name": item["name"],
                "actual_pct": raw_dec(item["actual_pct"], 2),
                "target_pct": raw_dec(item["target_pct"], 2),
                "delta_pp": raw_dec(item["delta_pp"], 2),
                "actual_amount": proj(item["actual_usd"]),
                "target_amount": proj(item["target_usd"]),
                "actual_usd": raw_dec(item["actual_usd"], 2),
                "target_usd": raw_dec(item["target_usd"], 2),
            }
            if "long_leg" in item:
                for leg_name in ("long_leg", "short_leg", "net"):
                    leg = item[leg_name]
                    entry[leg_name] = {
                        "actual_pct": raw_dec(leg["actual_pct"], 2),
                        "target_pct": raw_dec(leg["target_pct"], 2),
                        "delta_pp": raw_dec(leg["delta_pp"], 2),
                        "actual_amount": proj(leg["actual_usd"]),
                        "target_amount": proj(leg["target_usd"]),
                        "actual_usd": raw_dec(leg["actual_usd"], 2),
                        "target_usd": raw_dec(leg["target_usd"], 2),
                    }
            strat_out[sid] = entry

        return {
            "equity_mode": valuation_result["equity_mode"],
            "base_currency": "USD",
            "display_currency": display_ccy,
            "fx_status": fx_status,
            "fx_label": fx_label,
            "fx_rate": raw_dec(fx_rate_dec),
            "net_equity": proj(valuation_result["net_equity_usd"]),
            "net_equity_usd": raw_dec(valuation_result["net_equity_usd"], 2),
            "cash_and_spot": proj(valuation_result["cash_and_spot_usd"]),
            "cash_and_spot_usd": raw_dec(valuation_result["cash_and_spot_usd"], 2),
            "cash": proj(valuation_result["cash_usd"]),
            "cash_usd": raw_dec(valuation_result["cash_usd"], 2),
            "non_cash_spot": proj(valuation_result["non_cash_spot_usd"]),
            "non_cash_spot_usd": raw_dec(valuation_result["non_cash_spot_usd"], 2),
            "spot_upl": proj(valuation_result["spot_upl_usd"]),
            "spot_upl_usd": raw_dec(valuation_result["spot_upl_usd"], 2),
            "derivatives_upl": proj(valuation_result["derivatives_upl_usd"]),
            "derivatives_upl_usd": raw_dec(valuation_result["derivatives_upl_usd"], 2),
            "confirmed_debt": proj(valuation_result["confirmed_debt_usd"]),
            "confirmed_debt_usd": raw_dec(valuation_result["confirmed_debt_usd"], 2),
            "long_risk": proj(valuation_result["long_risk_usd"]),
            "long_risk_usd": raw_dec(valuation_result["long_risk_usd"], 2),
            "short_risk": proj(valuation_result["short_risk_usd"]),
            "short_risk_usd": raw_dec(valuation_result["short_risk_usd"], 2),
            "reported_margin": proj(valuation_result["reported_margin_usd"]),
            "reported_margin_usd": raw_dec(valuation_result["reported_margin_usd"], 2),
            "net_direction_pct": raw_dec(valuation_result["net_direction_pct"], 2),
            "effective_leverage": raw_dec(valuation_result["effective_leverage"], 3),
            "spot_balances": spots_out,
            "derivative_positions": derivs_out,
            "strategy_comparison": strat_out,
            "unpriced_items": valuation_result["unpriced_items"],
        }
