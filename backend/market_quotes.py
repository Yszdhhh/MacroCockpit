"""Live & cached FX (USD/CNH & USD/CNY) and OKX crypto spot/perp quotes."""
from __future__ import annotations

import math
import time
from datetime import datetime
from decimal import Decimal, localcontext
from typing import Any

try:
    import requests
except ImportError:
    requests = None  # type: ignore[assignment]

from backend.runtime import (
    BJT as _BJT,
    DATA_DIR,
    UTC,
    atomic_write_json as _atomic_write_json,
    now_bjt_iso as _now_iso,
    read_json_safe as _read_json,
)
from backend.valuation import decimal_str, parse_decimal_str


FX_CACHE_TTL_SECONDS = 60
CRYPTO_CACHE_TTL_SECONDS = 60

EASTMONEY_USDCNH_URL = (
    "https://push2.eastmoney.com/api/qt/stock/get"
    "?secid=133.USDCNH&fields=f43,f57,f58,f59,f86"
)
FRANKFURTER_USDCNY_URL = "https://api.frankfurter.dev/v1/latest?base=USD&symbols=CNY"

OKX_SPOT_TICKERS_URL = "https://www.okx.com/api/v5/market/tickers?instType=SPOT"
OKX_SPOT_SINGLE_URL = "https://www.okx.com/api/v5/market/ticker"
OKX_SWAP_MARK_URL = "https://www.okx.com/api/v5/public/mark-price?instType=SWAP"
OKX_SWAP_FUNDING_URL = "https://www.okx.com/api/v5/public/funding-rate"

DEFAULT_CRYPTO_SPOT_SYMBOLS: tuple[str, ...] = (
    "BTC-USDT",
    "ETH-USDT",
    "SOL-USDT",
    "BNB-USDT",
    "DOGE-USDT",
    "PAXG-USDT",
    "USDT-USD",
    "USDC-USD",
)
DEFAULT_CRYPTO_SWAP_SYMBOLS: tuple[str, ...] = (
    "BTC-USDT-SWAP",
    "ETH-USDT-SWAP",
    "SOL-USDT-SWAP",
    "BNB-USDT-SWAP",
    "DOGE-USDT-SWAP",
)

BITGET_MIX_TICKER_URL = "https://api.bitget.com/api/v2/mix/market/ticker"
TENCENT_QUOTE_URL = "https://qt.gtimg.cn/q={codes}"
HKD_PER_USDT_REF = Decimal("7.79")
HYBRID_PERP_SYMBOLS: tuple[str, ...] = ("QQQUSDT", "ZHIPUUSDT")

# OKX SWAP standard contract multipliers (ctVal)
KNOWN_SWAP_MULTIPLIERS: dict[str, dict[str, str]] = {
    "BTC-USDT-SWAP": {"ctVal": "0.01", "ctType": "linear", "quote": "USDT", "settle": "USDT", "base": "BTC"},
    "ETH-USDT-SWAP": {"ctVal": "0.1", "ctType": "linear", "quote": "USDT", "settle": "USDT", "base": "ETH"},
    "SOL-USDT-SWAP": {"ctVal": "1", "ctType": "linear", "quote": "USDT", "settle": "USDT", "base": "SOL"},
    "BNB-USDT-SWAP": {"ctVal": "0.01", "ctType": "linear", "quote": "USDT", "settle": "USDT", "base": "BNB"},
    "DOGE-USDT-SWAP": {"ctVal": "1000", "ctType": "linear", "quote": "USDT", "settle": "USDT", "base": "DOGE"},
    "BTC-USD-SWAP": {"ctVal": "100", "ctType": "inverse", "quote": "USD", "settle": "BTC", "base": "BTC"},
    "ETH-USD-SWAP": {"ctVal": "10", "ctType": "inverse", "quote": "USD", "settle": "ETH", "base": "ETH"},
    "SOL-USD-SWAP": {"ctVal": "10", "ctType": "inverse", "quote": "USD", "settle": "SOL", "base": "SOL"},
}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )
}


def _epoch_to_iso(raw_ts: Any, *, milliseconds: bool = False) -> str:
    try:
        val = float(raw_ts)
        if milliseconds:
            val /= 1000.0
        if val <= 0:
            return _now_iso()
        return datetime.fromtimestamp(val, tz=UTC).astimezone(_BJT).isoformat(timespec="seconds")
    except (TypeError, ValueError, OverflowError, OSError):
        return _now_iso()


def _cache_is_fresh(cached: dict[str, Any], now: float, ttl: int) -> bool:
    try:
        epoch = float(cached.get("fetched_epoch"))
    except (TypeError, ValueError, OverflowError):
        return False
    return math.isfinite(epoch) and 0 <= now - epoch <= ttl


def _parse_eastmoney_fx(payload: dict[str, Any], received_at: str) -> dict[str, Any] | None:
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    raw_px = data.get("f43")
    raw_dec = data.get("f59", 4)
    if raw_px is None or raw_px == "-":
        return None
    try:
        px_dec = Decimal(str(raw_px))
        scale = int(raw_dec)
        if px_dec <= 0 or scale < 0 or scale > 12:
            return None
        with localcontext() as ctx:
            ctx.prec = 38
            rate_dec = px_dec / (Decimal(10) ** scale)
        rate_str = parse_decimal_str(rate_dec)
    except Exception:
        return None
    market_at = _epoch_to_iso(data.get("f86"), milliseconds=False)
    return {
        "pair": "USD/CNH",
        "rate": rate_str,
        "hkd_per_usd": str(HKD_PER_USDT_REF),
        "basis": "offshore",
        "label": "人民币估值 · 离岸价",
        "source": "eastmoney",
        "session": "24x5",
        "market_at": market_at,
        "received_at": received_at,
        "status": "ok",
        "stale": False,
        "cached": False,
    }


def _parse_frankfurter_fx(payload: dict[str, Any], received_at: str) -> dict[str, Any] | None:
    rates = payload.get("rates")
    if not isinstance(rates, dict) or "CNY" not in rates:
        return None
    try:
        rate_str = parse_decimal_str(str(rates["CNY"]))
        if Decimal(rate_str) <= 0:
            return None
    except Exception:
        return None
    dt = str(payload.get("date") or "").strip()[:10]
    market_at = f"{dt}T00:00:00+00:00" if dt else received_at
    return {
        "pair": "USD/CNY",
        "rate": rate_str,
        "hkd_per_usd": str(HKD_PER_USDT_REF),
        "basis": "reference",
        "label": "在岸参考价",
        "source": "frankfurter",
        "session": "daily",
        "market_at": market_at,
        "received_at": received_at,
        "status": "ok",
        "stale": False,
        "cached": False,
    }


def fetch_fx_snapshot(
    timeout: int | float = 5,
    *,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Fetch USD/CNH (EastMoney) with Frankfurter USD/CNY fallback and 60s disk cache."""
    cache_path = DATA_DIR / "fx_cache.json"
    cached = _read_json(cache_path)
    if not isinstance(cached, dict):
        cached = None
    now_epoch = time.time()

    if (
        not force_refresh
        and cached
        and cached.get("rate") not in (None, "")
        and _cache_is_fresh(cached, now_epoch, FX_CACHE_TTL_SECONDS)
    ):
        snap = dict(cached.get("snapshot") or {})
        if snap.get("rate"):
            snap.setdefault("hkd_per_usd", str(HKD_PER_USDT_REF))
            snap["cached"] = True
            snap["stale"] = False
            snap["status"] = "ok"
            return snap

    received_at = _now_iso()
    snapshot: dict[str, Any] | None = None

    if requests is not None:
        try:
            resp = requests.get(EASTMONEY_USDCNH_URL, headers=_HEADERS, timeout=timeout)
            if resp.status_code == 200:
                snapshot = _parse_eastmoney_fx(resp.json(), received_at)
        except Exception:
            snapshot = None

        if snapshot is None:
            try:
                resp = requests.get(FRANKFURTER_USDCNY_URL, headers=_HEADERS, timeout=timeout)
                if resp.status_code == 200:
                    snapshot = _parse_frankfurter_fx(resp.json(), received_at)
            except Exception:
                snapshot = None

    if snapshot is not None:
        try:
            _atomic_write_json(
                cache_path,
                {
                    "fetched_epoch": now_epoch,
                    "rate": snapshot["rate"],
                    "snapshot": snapshot,
                },
            )
        except OSError:
            pass
        return snapshot

    if cached and isinstance(cached.get("snapshot"), dict) and cached["snapshot"].get("rate"):
        stale_snap = dict(cached["snapshot"])
        stale_snap.setdefault("hkd_per_usd", str(HKD_PER_USDT_REF))
        stale_snap["status"] = "stale_cache"
        stale_snap["stale"] = True
        stale_snap["cached"] = True
        return stale_snap

    return {
        "pair": "USD/CNH",
        "rate": None,
        "hkd_per_usd": str(HKD_PER_USDT_REF),
        "basis": None,
        "label": "人民币估值不可用",
        "source": None,
        "session": "",
        "market_at": "",
        "received_at": received_at,
        "status": "unavailable",
        "stale": True,
        "cached": False,
    }


def _safe_dec_str(val: Any) -> str | None:
    if val in (None, ""):
        return None
    try:
        return parse_decimal_str(str(val))
    except ValueError:
        return None


def _normalize_crypto_targets(symbols: list[str] | tuple[str, ...] | None) -> tuple[list[str], list[str]]:
    if not symbols:
        return (list(DEFAULT_CRYPTO_SPOT_SYMBOLS), list(DEFAULT_CRYPTO_SWAP_SYMBOLS))
    spots: list[str] = []
    swaps: list[str] = []
    for raw in symbols:
        s = str(raw or "").strip().upper()
        if not s:
            continue
        if s.endswith("-SWAP"):
            if s not in swaps:
                swaps.append(s)
        elif "-" in s:
            if s not in spots:
                spots.append(s)
        else:
            sp = f"{s}-USD" if s in ("USDT", "USDC") else f"{s}-USDT"
            sw = f"{s}-USDT-SWAP"
            if sp not in spots:
                spots.append(sp)
            if s not in ("USDT", "USDC") and sw not in swaps:
                swaps.append(sw)
    if not spots and not swaps:
        return (list(DEFAULT_CRYPTO_SPOT_SYMBOLS), list(DEFAULT_CRYPTO_SWAP_SYMBOLS))
    return (spots, swaps)


def fetch_crypto_quotes(
    symbols: list[str] | tuple[str, ...] | None = None,
    timeout: int | float = 5,
    *,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Fetch OKX public spot and perpetual mark-price + funding-rate quotes with graceful cache fallback."""
    cache_path = DATA_DIR / "crypto_quotes_cache.json"
    cached = _read_json(cache_path)
    if not isinstance(cached, dict):
        cached = None
    now_epoch = time.time()
    spot_targets, swap_targets = _normalize_crypto_targets(symbols)

    if (
        not force_refresh
        and symbols is None
        and cached
        and isinstance(cached.get("payload"), dict)
        and _cache_is_fresh(cached, now_epoch, CRYPTO_CACHE_TTL_SECONDS)
    ):
        payload = dict(cached["payload"])
        payload["cached"] = True
        return payload

    received_at = _now_iso()
    spot_map: dict[str, dict[str, Any]] = {}
    swap_map: dict[str, dict[str, Any]] = {}

    if requests is not None:
        raw_spot_by_id: dict[str, dict[str, Any]] = {}
        try:
            r_spot = requests.get(OKX_SPOT_TICKERS_URL, headers=_HEADERS, timeout=timeout)
            if r_spot.status_code == 200:
                for row in (r_spot.json() or {}).get("data") or []:
                    iid = str(row.get("instId") or "").upper()
                    if iid:
                        raw_spot_by_id[iid] = row
        except Exception:
            pass

        for inst_id in spot_targets:
            row = raw_spot_by_id.get(inst_id)
            if row is None and inst_id != "USDC-USD":
                try:
                    r_one = requests.get(
                        OKX_SPOT_SINGLE_URL,
                        params={"instId": inst_id},
                        headers=_HEADERS,
                        timeout=timeout,
                    )
                    if r_one.status_code == 200:
                        data = (r_one.json() or {}).get("data") or []
                        if data:
                            row = data[0]
                except Exception:
                    row = None

            if row is not None:
                px_str = _safe_dec_str(row.get("last"))
                if px_str is None or Decimal(px_str) <= 0:
                    continue
                open_str = _safe_dec_str(row.get("open24h"))
                chg_str: str | None = None
                if open_str is not None and Decimal(open_str) > 0:
                    with localcontext() as ctx:
                        ctx.prec = 38
                        chg_dec = (Decimal(px_str) - Decimal(open_str)) / Decimal(open_str) * Decimal("100")
                    chg_str = decimal_str(chg_dec, 2)
                mkt_at = _epoch_to_iso(row.get("ts"), milliseconds=True)
                base, quote_ccy = inst_id.split("-", 1)
                entry = {
                    "inst_id": inst_id,
                    "symbol": base,
                    "quote_currency": quote_ccy,
                    "kind": "spot",
                    "price": px_str,
                    "open_24h": open_str,
                    "change_24h_pct": chg_str,
                    "market_at": mkt_at,
                    "received_at": received_at,
                    "source": "okx",
                    "session": "24x7",
                }
                spot_map[inst_id] = entry

        # Derive USDC-USD via USDC-USDT * USDT-USD when direct USDC-USD ticker is unavailable on OKX
        if "USDC-USD" in spot_targets and "USDC-USD" not in spot_map:
            usdc_usdt = raw_spot_by_id.get("USDC-USDT")
            usdt_usd_entry = spot_map.get("USDT-USD")
            usdc_usdt_px = _safe_dec_str((usdc_usdt or {}).get("last"))
            if usdc_usdt_px and Decimal(usdc_usdt_px) > 0 and usdt_usd_entry:
                with localcontext() as ctx:
                    ctx.prec = 38
                    derived_px = Decimal(usdc_usdt_px) * Decimal(usdt_usd_entry["price"])
                derived_str = decimal_str(derived_px, 6)
                mkt_at = _epoch_to_iso((usdc_usdt or {}).get("ts"), milliseconds=True)
                entry = {
                    "inst_id": "USDC-USD",
                    "symbol": "USDC",
                    "quote_currency": "USD",
                    "kind": "spot",
                    "price": derived_str,
                    "open_24h": None,
                    "change_24h_pct": None,
                    "market_at": mkt_at,
                    "received_at": received_at,
                    "source": "okx_derived",
                    "session": "24x7",
                }
                spot_map["USDC-USD"] = entry

        raw_mark_by_id: dict[str, dict[str, Any]] = {}
        raw_fund_by_id: dict[str, dict[str, Any]] = {}
        try:
            r_mark = requests.get(OKX_SWAP_MARK_URL, headers=_HEADERS, timeout=timeout)
            if r_mark.status_code == 200:
                for row in (r_mark.json() or {}).get("data") or []:
                    iid = str(row.get("instId") or "").upper()
                    if iid:
                        raw_mark_by_id[iid] = row
        except Exception:
            pass

        try:
            r_fund = requests.get(
                OKX_SWAP_FUNDING_URL,
                params={"instId": "ANY"},
                headers=_HEADERS,
                timeout=timeout,
            )
            if r_fund.status_code == 200:
                for row in (r_fund.json() or {}).get("data") or []:
                    iid = str(row.get("instId") or "").upper()
                    if iid:
                        raw_fund_by_id[iid] = row
        except Exception:
            pass

        for swap_id in swap_targets:
            m_row = raw_mark_by_id.get(swap_id)
            f_row = raw_fund_by_id.get(swap_id)
            if f_row is None and m_row is not None:
                try:
                    rf = requests.get(
                        OKX_SWAP_FUNDING_URL,
                        params={"instId": swap_id},
                        headers=_HEADERS,
                        timeout=timeout,
                    )
                    if rf.status_code == 200:
                        fdata = (rf.json() or {}).get("data") or []
                        if fdata:
                            f_row = fdata[0]
                except Exception:
                    f_row = None

            mark_str = _safe_dec_str((m_row or {}).get("markPx"))
            if mark_str is not None and Decimal(mark_str) <= 0:
                mark_str = None
            fund_str = _safe_dec_str((f_row or {}).get("fundingRate"))
            if mark_str is None and fund_str is None:
                continue
            parts = swap_id.split("-")
            base = parts[0]
            quote_ccy = parts[1] if len(parts) > 1 else "USDT"
            mkt_at = _epoch_to_iso(((m_row if mark_str is not None else f_row) or {}).get("ts"), milliseconds=True)
            spec = KNOWN_SWAP_MULTIPLIERS.get(swap_id, {})
            entry = {
                "inst_id": swap_id,
                "symbol": base,
                "quote_currency": quote_ccy,
                "kind": "mark",
                "price": mark_str,
                "mark_price": mark_str,
                "funding_rate": fund_str,
                "funding_time": _epoch_to_iso((f_row or {}).get("fundingTime"), milliseconds=True)
                if (f_row or {}).get("fundingTime")
                else "",
                "contract_multiplier": spec.get("ctVal", "1"),
                "contract_type": spec.get("ctType", "linear"),
                "market_at": mkt_at,
                "received_at": received_at,
                "source": "okx",
                "session": "24x7",
            }
            swap_map[swap_id] = entry

    has_fresh = bool(spot_map or swap_map)
    old_payload = (cached or {}).get("payload")
    old_payload = old_payload if isinstance(old_payload, dict) else {}
    stale = False
    using_cache = False
    cache_maps: dict[str, dict[str, Any]] = {}
    for kind, targets, entries in (("spot", spot_targets, spot_map), ("swap", swap_targets, swap_map)):
        old_entries = old_payload.get(kind)
        old_entries = old_entries if isinstance(old_entries, dict) else {}
        for target in targets:
            old = old_entries.get(target)
            old = old if isinstance(old, dict) else {}
            entry = entries.get(target)
            if entry is None:
                stale = True
                if old:
                    entries[target] = {**old, "stale": True}
                    using_cache = True
            elif kind == "swap":
                mark_stale = entry["mark_price"] is None
                funding_stale = entry["funding_rate"] is None
                if mark_stale and (old.get("mark_price") or old.get("price")):
                    entry = {**old, "funding_rate": entry["funding_rate"], "funding_time": entry["funding_time"]}
                    using_cache = True
                elif funding_stale and old.get("funding_rate") is not None:
                    entry.update(funding_rate=old["funding_rate"], funding_time=old.get("funding_time", ""))
                    using_cache = True
                stale |= mark_stale or funding_stale
                entries[target] = {**entry, "stale": mark_stale or funding_stale,
                                   "mark_stale": mark_stale, "funding_stale": funding_stale}
        cache_maps[kind] = {**old_entries, **entries}

    def flatten(spots: dict[str, Any], swaps: dict[str, Any]) -> dict[str, Any]:
        flat = {**spots, **swaps}
        for instrument, entry in spots.items():
            if not isinstance(entry, dict):
                continue
            base, _, currency = instrument.partition("-")
            if currency == "USDT" or instrument in ("USDT-USD", "USDC-USD"):
                flat.setdefault(base, entry)
        for swap_id, entry in swaps.items():
            if not isinstance(entry, dict):
                continue
            parts = swap_id.split("-")
            if len(parts) == 3 and parts[1] == "USDT" and parts[2] in ("SWAP", "PERP"):
                flat.setdefault(f"{parts[0]}USDT", entry)
        return flat

    payload = {
        "status": "ok" if has_fresh else ("stale_cache" if using_cache else "unavailable"),
        "stale": stale,
        "cached": using_cache,
        "source": "okx",
        "session": "24x7",
        "market_at": max((str(entry.get("market_at") or "") for entry in (*spot_map.values(), *swap_map.values())), default=""),
        "received_at": received_at if has_fresh else old_payload.get("received_at", received_at),
        "spot": spot_map,
        "swap": swap_map,
        "quotes": flatten(spot_map, swap_map),
    }
    if symbols is None and has_fresh:
        try:
            cache_payload = {**payload, **cache_maps, "quotes": flatten(cache_maps["spot"], cache_maps["swap"])}
            _atomic_write_json(cache_path, {"fetched_epoch": now_epoch, "payload": cache_payload})
        except OSError:
            pass
    return payload


def _parse_tencent_spot_prices(raw_text: str) -> dict[str, Decimal]:
    """Parse Tencent qt.gtimg.cn semicolon-delimited quote payload into code -> latest price Decimal."""
    prices: dict[str, Decimal] = {}
    for stmt in str(raw_text or "").split(";"):
        s = stmt.strip()
        if not s or '="' not in s:
            continue
        head, _, body = s.partition('="')
        code = head.split("_")[-1].strip()
        fields = body.rstrip('"').split("~")
        if len(fields) > 3:
            px_str = _safe_dec_str(fields[3])
            if px_str is not None and Decimal(px_str) > 0:
                prices[code] = Decimal(px_str)
    return prices


def fetch_hybrid_perp_quotes(
    symbols: list[str] | tuple[str, ...] | None = None,
    timeout: int | float = 4,
    *,
    force_refresh: bool = False,
) -> dict[str, dict[str, Any]]:
    """Fetch USDT-perp mark prices for equity/HK-linked contracts (QQQUSDT, ZHIPUUSDT) via Bitget with Tencent fallback."""
    targets = tuple(dict.fromkeys(str(s or "").strip().upper() for s in (symbols or HYBRID_PERP_SYMBOLS) if str(s or "").strip()))
    if not targets:
        return {}

    cache_path = DATA_DIR / "hybrid_perp_cache.json"
    cached = _read_json(cache_path)
    if not isinstance(cached, dict):
        cached = None
    now_epoch = time.time()
    if (
        not force_refresh
        and cached
        and isinstance(cached.get("quotes"), dict)
        and _cache_is_fresh(cached, now_epoch, CRYPTO_CACHE_TTL_SECONDS)
    ):
        c_quotes = cached["quotes"]
        if all(t in c_quotes for t in targets):
            return {t: dict(c_quotes[t], cached=True) for t in targets}

    received_at = _now_iso()
    quotes_out: dict[str, dict[str, Any]] = {}

    if requests is not None:
        for sym in targets:
            try:
                resp = requests.get(
                    BITGET_MIX_TICKER_URL,
                    params={"productType": "USDT-FUTURES", "symbol": sym},
                    headers=_HEADERS,
                    timeout=timeout,
                )
                if resp.status_code == 200:
                    data = (resp.json() or {}).get("data") or []
                    row = data[0] if isinstance(data, list) and data else None
                    if isinstance(row, dict):
                        px_str = _safe_dec_str(row.get("markPr")) or _safe_dec_str(row.get("lastPr"))
                        if px_str is not None and Decimal(px_str) > 0:
                            fund_str = _safe_dec_str(row.get("fundingRate"))
                            mkt_at = _epoch_to_iso(row.get("ts"), milliseconds=True)
                            quotes_out[sym] = {
                                "inst_id": sym,
                                "symbol": sym,
                                "quote_currency": "USDT",
                                "kind": "mark",
                                "price": px_str,
                                "mark_price": px_str,
                                "funding_rate": fund_str,
                                "market_at": mkt_at,
                                "received_at": received_at,
                                "source": "bitget",
                                "session": "24x7",
                            }
            except Exception:
                pass

        missing = [t for t in targets if t not in quotes_out]
        if missing:
            tc_map = {"QQQUSDT": "usQQQ", "ZHIPUUSDT": "hk02513"}
            codes = [tc_map[t] for t in missing if t in tc_map]
            if codes:
                try:
                    resp = requests.get(
                        TENCENT_QUOTE_URL.format(codes=",".join(codes)),
                        headers=_HEADERS,
                        timeout=timeout,
                    )
                    if resp.status_code == 200:
                        raw_txt = resp.content.decode("gbk", errors="replace")
                        parsed = _parse_tencent_spot_prices(raw_txt)
                        if "QQQUSDT" in missing and "usQQQ" in parsed:
                            px_str = decimal_str(parsed["usQQQ"], 2)
                            quotes_out["QQQUSDT"] = {
                                "inst_id": "QQQUSDT",
                                "symbol": "QQQUSDT",
                                "quote_currency": "USDT",
                                "kind": "spot_proxy",
                                "is_proxy": True,
                                "price": px_str,
                                "market_at": received_at,
                                "received_at": received_at,
                                "source": "tencent_us",
                            }
                        if "ZHIPUUSDT" in missing and "hk02513" in parsed:
                            with localcontext() as ctx:
                                ctx.prec = 38
                                usd_px = parsed["hk02513"] / HKD_PER_USDT_REF
                            px_str = decimal_str(usd_px, 4)
                            quotes_out["ZHIPUUSDT"] = {
                                "inst_id": "ZHIPUUSDT",
                                "symbol": "ZHIPUUSDT",
                                "quote_currency": "USDT",
                                "kind": "spot_proxy",
                                "is_proxy": True,
                                "price": px_str,
                                "market_at": received_at,
                                "received_at": received_at,
                                "source": "tencent_hk_fx",
                            }
                except Exception:
                    pass

    if quotes_out:
        try:
            merged_cache = {**((cached or {}).get("quotes") or {}), **quotes_out}
            _atomic_write_json(cache_path, {"fetched_epoch": now_epoch, "quotes": merged_cache})
        except OSError:
            pass
        return quotes_out

    old_quotes = (cached or {}).get("quotes")
    if isinstance(old_quotes, dict):
        return {t: dict(old_quotes[t], stale=True, cached=True) for t in targets if isinstance(old_quotes.get(t), dict)}
    return {}
