"""FRED macro indicators, cross-asset pricing, and live US equities fetcher."""
from __future__ import annotations

import csv
import io
import json
import math
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.runtime import (
    BJT,
    COCKPIT_ROOT,
    DATA_DIR,
    UTC,
    atomic_write_json,
    is_finite as _is_finite,
    resolve_hermes_home,
)

try:
    import pandas as pd
except ImportError:
    pd = None  # type: ignore[assignment]
try:
    import requests
except ImportError:
    requests = None  # type: ignore[assignment]
try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

FRED_CACHE_PATH = DATA_DIR / "fred_cache.json"

_HERMES_HOME = resolve_hermes_home()
PARQUET_MACRO_DIR = Path(
    os.environ.get("MACROCOCKPIT_PARQUET_MACRO_DIR") or (DATA_DIR / "parquet_macro")
)
SECRETS_PATH = Path(
    os.environ.get("MACROCOCKPIT_SECRETS_PATH") or (COCKPIT_ROOT / "config" / "local_secrets.yaml")
)
QUANT_LATEST_PATH = Path(
    os.environ.get("MACROCOCKPIT_QUANT_LATEST_PATH") or (DATA_DIR / "quant_latest.json")
)
MARKET_SNAPSHOT_FALLBACK = _HERMES_HOME / "scripts" / "news" / "inputs" / "market_snapshot.json"
MACRO_SNAPSHOT_FALLBACK = _HERMES_HOME / "scripts" / "news" / "inputs" / "macro_snapshot.json"

FRED_SERIES_MAP: dict[str, str] = {
    "FEDFUNDS": "DFF",
    "US2Y": "DGS2",
    "US5Y": "DGS5",
    "US10Y": "DGS10",
    "US30Y": "DGS30",
    "CPI": "CPIAUCSL",
    "VIX": "VIXCLS",
    "DOLLAR": "DTWEXBGS",
    "WTI": "DCOILWTICO",
    "SP500": "SP500",
    "WALCL": "WALCL",
    "RRPONTSYD": "RRPONTSYD",
    "WTREGEN": "WTREGEN",
}

US_INDICES: list[tuple[str, str]] = [
    ("SPY", "标普500 ETF"),
    ("QQQ", "纳指100 ETF"),
    ("DIA", "道琼斯 ETF"),
    (".IXIC", "纳斯达克综合"),
]

US_MAG7: list[tuple[str, str]] = [
    ("NVDA", "英伟达"),
    ("MSFT", "微软"),
    ("GOOG", "谷歌"),
    ("META", "Meta"),
    ("AMZN", "亚马逊"),
    ("AAPL", "苹果"),
    ("TSLA", "特斯拉"),
]

US_AI_COMPUTE: list[tuple[str, str]] = [
    ("AMD", "超威半导体"),
    ("AVGO", "博通"),
    ("TSM", "台积电"),
    ("MU", "美光科技"),
    ("INTC", "英特尔"),
    ("QCOM", "高通"),
    ("PLTR", "Palantir"),
    ("COIN", "Coinbase"),
    ("SOXX", "费城半导体ETF"),
]

US_SECTORS: list[tuple[str, str]] = [
    ("XLK", "科技"),
    ("XLF", "金融"),
    ("XLE", "能源"),
    ("XLV", "医疗"),
    ("XLI", "工业"),
    ("XLY", "可选消费"),
    ("XLP", "必需消费"),
    ("XLU", "公用事业"),
]

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    )
}


def _load_fred_api_key() -> str | None:
    env_key = (os.environ.get("FRED_API_KEY") or "").strip()
    if env_key:
        return env_key
    if SECRETS_PATH.exists():
        try:
            data = yaml.safe_load(SECRETS_PATH.read_text(encoding="utf-8")) or {} if yaml else {}
            key = ((data.get("fred") or {}).get("api_key") or "").strip()
            if key:
                return key
        except Exception:
            return None
    return None


def _redact(text: str, secret: str | None) -> str:
    if not secret:
        return text
    return text.replace(secret, "[REDACTED]")


def _load_fred_cache() -> dict[str, list[list[Any]]]:
    if not FRED_CACHE_PATH.exists():
        return {}
    try:
        raw = json.loads(FRED_CACHE_PATH.read_text(encoding="utf-8"))
        return raw.get("series", {}) if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _save_fred_cache(series_map: dict[str, list[tuple[str, float]]]) -> None:
    payload = {
        "updated_at_bjt": datetime.now(BJT).strftime("%Y-%m-%d %H:%M:%S+08:00"),
        "series": {
            k: [[d, round(float(v), 6)] for d, v in vals[-400:]]
            for k, vals in series_map.items()
            if vals
        },
    }
    atomic_write_json(FRED_CACHE_PATH, payload)


def fetch_fred_observations(
    series_id: str,
    api_key: str | None = None,
    limit: int = 240,
    timeout: int = 12,
) -> list[tuple[str, float]]:
    """Fetch recent FRED observations via official API with keyless fredgraph.csv fallback."""
    if api_key:
        try:
            resp = requests.get(
                "https://api.stlouisfed.org/fred/series/observations",
                params={
                    "series_id": series_id,
                    "api_key": api_key,
                    "file_type": "json",
                    "sort_order": "desc",
                    "limit": limit,
                },
                timeout=timeout,
            )
            resp.raise_for_status()
            obs = resp.json().get("observations") or []
            parsed: list[tuple[str, float]] = []
            for item in obs:
                val_raw = item.get("value")
                dt_raw = str(item.get("date") or "")[:10]
                if val_raw in (".", "", None) or not dt_raw:
                    continue
                try:
                    num = float(val_raw)
                    if math.isfinite(num):
                        parsed.append((dt_raw, num))
                except ValueError:
                    continue
            if parsed:
                parsed.sort(key=lambda x: x[0])
                return parsed
        except Exception as exc:
            _ = _redact(str(exc), api_key)

    # Keyless CSV fallback (bounded window via cosd for fast ~1s response)
    try:
        cosd = f"{datetime.now(BJT).year - 2}-01-01"
        resp = requests.get(
            f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}&cosd={cosd}",
            headers=_HEADERS,
            timeout=timeout,
        )
        resp.raise_for_status()
        reader = csv.reader(io.StringIO(resp.text))
        rows = list(reader)
        parsed = []
        for row in rows[1:]:
            if len(row) < 2:
                continue
            dt_raw, val_raw = row[0].strip()[:10], row[1].strip()
            if val_raw in (".", "", "NA", "NaN") or not dt_raw:
                continue
            try:
                num = float(val_raw)
                if math.isfinite(num):
                    parsed.append((dt_raw, num))
            except ValueError:
                continue
        parsed.sort(key=lambda x: x[0])
        return parsed[-limit:] if limit and len(parsed) > limit else parsed
    except Exception:
        return []


def _read_parquet_series(filename: str, column: str = "close") -> list[tuple[str, float]]:
    path = PARQUET_MACRO_DIR / filename
    if not path.exists() or pd is None:
        return []
    try:
        df = pd.read_parquet(path)
        if column not in df.columns:
            return []
        ser = pd.to_numeric(df[column], errors="coerce").dropna()
        out: list[tuple[str, float]] = []
        for idx, val in ser.items():
            fval = float(val)
            if not math.isfinite(fval):
                continue
            dt_str = pd.Timestamp(idx).strftime("%Y-%m-%d")
            out.append((dt_str, fval))
        out.sort(key=lambda x: x[0])
        return out
    except Exception:
        return []


def _merge_series(
    base: list[tuple[str, float]],
    overlay: list[tuple[str, float]] | list[list[Any]],
) -> list[tuple[str, float]]:
    merged: dict[str, float] = {}
    for dt, val in base:
        if _is_finite(val):
            merged[str(dt)[:10]] = float(val)
    for item in overlay:
        if len(item) >= 2 and _is_finite(item[1]):
            merged[str(item[0])[:10]] = float(item[1])
    return sorted(merged.items(), key=lambda x: x[0])


def _fetch_gold_tail() -> list[tuple[str, float]]:
    """Fetch recent daily COMEX Gold (GC=F) closes via Yahoo Finance chart API or yfinance."""
    if requests is not None:
        try:
            resp = requests.get(
                "https://query2.finance.yahoo.com/v8/finance/chart/GC%3DF",
                params={"range": "6mo", "interval": "1d"},
                headers=_HEADERS,
                timeout=8,
            )
            if resp.status_code == 200:
                res = ((resp.json() or {}).get("chart") or {}).get("result") or []
                if res:
                    ts_list = res[0].get("timestamp") or []
                    closes = (((res[0].get("indicators") or {}).get("quote") or [{}])[0]).get("close") or []
                    by_day: dict[str, float] = {}
                    for ts, val in zip(ts_list, closes):
                        if _is_finite(val) and float(val) > 0:
                            day = datetime.fromtimestamp(int(ts), tz=UTC).strftime("%Y-%m-%d")
                            by_day[day] = round(float(val), 2)
                    if len(by_day) >= 5:
                        return sorted(by_day.items(), key=lambda x: x[0])
        except Exception:
            pass
    try:
        import yfinance as yf

        df = yf.download("GC=F", period="6mo", interval="1d", auto_adjust=True, progress=False, threads=False)
        if df is None or df.empty:
            return []
        close = df["Close"]
        if pd is not None and isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]
        out: list[tuple[str, float]] = []
        for idx, val in close.dropna().items():
            fval = float(val)
            if math.isfinite(fval):
                day = idx.strftime("%Y-%m-%d") if pd is None else pd.Timestamp(idx).strftime("%Y-%m-%d")
                out.append((day, fval))
        return sorted(out, key=lambda x: x[0])
    except Exception:
        return []


def refresh_fred_cache(include_gold: bool = True) -> dict[str, list[tuple[str, float]]]:
    """Refresh FRED series + Net Liquidity components into local data/fred_cache.json."""
    api_key = _load_fred_api_key()
    cache_raw = _load_fred_cache()
    updated: dict[str, list[tuple[str, float]]] = {}

    for key, sid in FRED_SERIES_MAP.items():
        limit = 48 if key == "CPI" else 120
        obs = fetch_fred_observations(sid, api_key=api_key, limit=limit)
        existing = cache_raw.get(key, [])
        merged = _merge_series([(str(x[0]), float(x[1])) for x in existing], obs)
        if merged:
            updated[key] = merged

    if include_gold:
        gold_obs = _fetch_gold_tail()
        existing_gold = cache_raw.get("GOLD", [])
        merged_gold = _merge_series([(str(x[0]), float(x[1])) for x in existing_gold], gold_obs)
        if merged_gold:
            updated["GOLD"] = merged_gold

    if updated:
        _save_fred_cache(updated)
    return updated


def _load_macro_series_bundle(refresh_fred: bool = False) -> dict[str, list[tuple[str, float]]]:
    if refresh_fred:
        cache_data = refresh_fred_cache(include_gold=True)
    else:
        raw_cache = _load_fred_cache()
        cache_data = {
            k: [(str(x[0]), float(x[1])) for x in v if len(x) >= 2 and _is_finite(x[1])]
            for k, v in raw_cache.items()
        }

    # Always attempt best-effort Net Liquidity cache seed if absent locally
    if not all(k in cache_data and cache_data[k] for k in ("WALCL", "RRPONTSYD", "WTREGEN")):
        api_key = _load_fred_api_key()
        seeded = False
        for liq_key in ("WALCL", "RRPONTSYD", "WTREGEN"):
            if not cache_data.get(liq_key):
                obs = fetch_fred_observations(FRED_SERIES_MAP[liq_key], api_key=api_key, limit=30)
                if obs:
                    cache_data[liq_key] = obs
                    seeded = True
        if seeded:
            _save_fred_cache(cache_data)

    bundle: dict[str, list[tuple[str, float]]] = {}
    for key in ("FEDFUNDS", "CPI", "VIX", "DOLLAR", "WTI", "SP500", "GOLD"):
        base = _read_parquet_series(f"{key}.parquet", "close")
        bundle[key] = _merge_series(base, cache_data.get(key, []))

    # Treasury columns
    tr_path = PARQUET_MACRO_DIR / "TREASURY.parquet"
    tr_col_map = {
        "US2Y": "us_2y",
        "US5Y": "us_5y",
        "US10Y": "us_10y",
        "US30Y": "us_30y",
    }
    if tr_path.exists():
        for k, col in tr_col_map.items():
            base = _read_parquet_series("TREASURY.parquet", col)
            if not base:
                base = _read_parquet_series(f"{k}.parquet", "close")
            bundle[k] = _merge_series(base, cache_data.get(k, []))
    else:
        for k in tr_col_map:
            bundle[k] = _merge_series(_read_parquet_series(f"{k}.parquet", "close"), cache_data.get(k, []))

    # Compute US10Y-US2Y spread directly from merged US10Y & US2Y so --refresh-fred stays consistent
    d10 = dict(bundle.get("US10Y", []))
    d2 = dict(bundle.get("US2Y", []))
    common_dates = sorted(set(d10) & set(d2))
    if common_dates:
        bundle["US10Y_2Y_SPREAD"] = [(d, d10[d] - d2[d]) for d in common_dates]
    else:
        bundle["US10Y_2Y_SPREAD"] = _read_parquet_series("TREASURY.parquet", "us_10y_2y_spread")

    for liq_key in ("WALCL", "RRPONTSYD", "WTREGEN"):
        if cache_data.get(liq_key):
            bundle[liq_key] = cache_data[liq_key]

    return bundle


def _infer_us_session(ts_str: str) -> str:
    """Infer US equity trading session from a YYYY-MM-DD HH:MM:SS US/Eastern timestamp."""
    m = re.search(r"(\d{4}-\d{2}-\d{2})\s+(\d{2}):(\d{2})", str(ts_str or ""))
    if not m:
        return "closed"
    try:
        dt = datetime.strptime(f"{m.group(1)} {m.group(2)}:{m.group(3)}", "%Y-%m-%d %H:%M")
    except ValueError:
        return "closed"
    if dt.weekday() >= 5:
        return "closed"
    mins = dt.hour * 60 + dt.minute
    if 9 * 60 + 30 <= mins <= 16 * 60 + 5:
        return "regular"
    if 4 * 60 <= mins < 9 * 60 + 30:
        return "pre"
    if 16 * 60 + 5 < mins <= 20 * 60:
        return "after"
    return "closed"


def _make_macro_item(
    *,
    item_id: str,
    group: str,
    label: str,
    value_display: str,
    raw_value: float | None,
    change_display: str,
    change_num: float | None,
    as_of: str,
    source: str,
    note: str,
    market_at: str | None = None,
    received_at: str | None = None,
    session: str | None = None,
) -> dict[str, Any] | None:
    if not _is_finite(raw_value) or not _is_finite(change_num):
        return None
    if not value_display or not as_of:
        return None
    rec_ts = received_at or datetime.now(BJT).strftime("%Y-%m-%d %H:%M:%S+08:00")
    return {
        "id": item_id,
        "group": group,
        "label": label,
        "value_display": value_display,
        "raw_value": round(float(raw_value), 4),
        "change_display": change_display,
        "change_num": round(float(change_num), 4),
        "as_of": as_of,
        "source": source,
        "note": note,
        "market_at": market_at or as_of,
        "received_at": rec_ts,
        "session": session or ("24x7" if source == "Binance" else "daily_close"),
    }


def _compute_cpi_yoy(cpi_series: list[tuple[str, float]]) -> tuple[float, float, str, float] | None:
    """Return (latest_yoy_pct, yoy_change_pp, as_of_date, latest_raw_index) using exact 12m matches."""
    if len(cpi_series) < 13:
        return None
    by_ym: dict[tuple[int, int], tuple[str, float]] = {}
    for dt_str, val in cpi_series:
        try:
            dt = datetime.strptime(dt_str[:10], "%Y-%m-%d")
            by_ym[(dt.year, dt.month)] = (dt_str[:10], val)
        except ValueError:
            continue

    yoy_points: list[tuple[str, float, float]] = []
    for (yr, mo), (dt_str, val) in sorted(by_ym.items()):
        prev_pair = by_ym.get((yr - 1, mo))
        if prev_pair and prev_pair[1] > 0:
            yoy = (val / prev_pair[1] - 1.0) * 100.0
            yoy_points.append((dt_str, yoy, val))

    if not yoy_points:
        return None
    latest_dt, latest_yoy, latest_idx = yoy_points[-1]
    prev_yoy = yoy_points[-2][1] if len(yoy_points) >= 2 else latest_yoy
    return (latest_yoy, latest_yoy - prev_yoy, latest_dt, latest_idx)


def _fetch_btc_live(quant_snap: dict[str, Any] | None) -> tuple[float, float, str, str, str] | None:
    """Return (price, chg_pct, as_of_date, source, note) for BTC."""
    today_bjt = datetime.now(BJT).strftime("%Y-%m-%d")
    mm = (quant_snap or {}).get("market_metrics") or {}
    ma50 = mm.get("btc_ma50")
    rvol = mm.get("btc_realized_vol20")
    extra_note = ""
    if _is_finite(ma50):
        extra_note = f"50日均线 ${float(ma50):,.0f}"
    if _is_finite(rvol):
        extra_note += f" · 20日已实现波动率 {float(rvol)*100:.1f}%"

    # 1) Binance Vision public ticker (geo-friendly)
    for url in (
        "https://data-api.binance.vision/api/v3/ticker/24hr?symbol=BTCUSDT",
        "https://api.binance.com/api/v3/ticker/24hr?symbol=BTCUSDT",
    ):
        try:
            r = requests.get(url, headers=_HEADERS, timeout=8)
            if r.status_code == 200:
                d = r.json()
                price = float(d["lastPrice"])
                chg = float(d["priceChangePercent"])
                if price > 0 and math.isfinite(price) and math.isfinite(chg):
                    return (price, chg, today_bjt, "Binance", extra_note or "Binance 24h 现货实时行情")
        except Exception:
            continue

    # 2) CoinGecko fallback
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": "bitcoin", "vs_currencies": "usd", "include_24hr_change": "true"},
            headers=_HEADERS,
            timeout=8,
        )
        if r.status_code == 200:
            btc = (r.json() or {}).get("bitcoin") or {}
            price = float(btc.get("usd") or 0)
            chg = float(btc.get("usd_24h_change") or 0)
            if price > 0 and math.isfinite(price):
                return (price, chg, today_bjt, "Market", extra_note or "CoinGecko 现货行情")
    except Exception:
        pass

    # 3) Local quant snapshot fallback
    snap_price = mm.get("btc_price")
    if _is_finite(snap_price) and float(snap_price) > 0:
        as_of = str((quant_snap or {}).get("data_health", {}).get("btc_last_bar_utc", today_bjt))[:10]
        return (float(snap_price), 0.0, as_of, "Market", extra_note or "量化K线最近收盘价")

    # 4) Legacy macro_snapshot fallback
    if MACRO_SNAPSHOT_FALLBACK.exists():
        try:
            ms = json.loads(MACRO_SNAPSHOT_FALLBACK.read_text(encoding="utf-8"))
            btc = (ms.get("crypto") or {}).get("BTC") or {}
            p = float(btc.get("price") or 0)
            c = float(btc.get("change_pct") or 0)
            if p > 0:
                return (p, c, str(ms.get("timestamp") or today_bjt)[:10], "Market", "本地缓存行")
        except Exception:
            pass
    return None


def build_macro_fred(
    quant_snap: dict[str, Any] | None = None,
    *,
    refresh_fred: bool = False,
) -> list[dict[str, Any]]:
    """Build normalized macro_fred items omitting any missing/null/NaN metrics."""
    if quant_snap is None and QUANT_LATEST_PATH.exists():
        try:
            quant_snap = json.loads(QUANT_LATEST_PATH.read_text(encoding="utf-8"))
        except Exception:
            quant_snap = {}

    bundle = _load_macro_series_bundle(refresh_fred=refresh_fred)
    mm = (quant_snap or {}).get("market_metrics") or {}
    sensors = (quant_snap or {}).get("sensors") or {}
    items: list[dict[str, Any]] = []

    def add_rate_metric(key: str, item_id: str, label: str, note: str) -> None:
        ser = bundle.get(key) or []
        if not ser:
            return
        dt, val = ser[-1]
        prev = ser[-2][1] if len(ser) >= 2 else val
        diff = val - prev
        bps = diff * 100.0
        chg_disp = f"{bps:+.0f} bps" if abs(bps) >= 0.5 else "0 bps"
        item = _make_macro_item(
            item_id=item_id,
            group="美联储与利率",
            label=label,
            value_display=f"{val:.2f}%",
            raw_value=val,
            change_display=chg_disp,
            change_num=diff,
            as_of=dt,
            source="FRED",
            note=note,
        )
        if item:
            items.append(item)

    add_rate_metric("FEDFUNDS", "fedfunds", "联邦基金利率", "美联储有效联邦基金利率 (DFF)")
    add_rate_metric("US10Y", "us_10y", "美债 10Y 收益率", "10年期美国国债基准收益率")
    add_rate_metric("US2Y", "us_2y", "美债 2Y 收益率", "反映政策利率预期对短端定价")
    add_rate_metric("US5Y", "us_5y", "美债 5Y 收益率", "5年期美国国债收益率")
    add_rate_metric("US30Y", "us_30y", "美债 30Y 收益率", "超长端通胀与财政供给定价")

    spread_ser = bundle.get("US10Y_2Y_SPREAD") or []
    if spread_ser:
        dt, val = spread_ser[-1]
        prev = spread_ser[-2][1] if len(spread_ser) >= 2 else val
        diff = val - prev
        bps = diff * 100.0
        spread_note = "曲线维持正斜率（未倒挂）" if val >= 0 else "收益率曲线倒挂，提示紧缩压力"
        item = _make_macro_item(
            item_id="us_10y_2y_spread",
            group="美联储与利率",
            label="10Y-2Y 利差",
            value_display=f"{val:+.2f}%",
            raw_value=val,
            change_display=f"{bps:+.0f} bps",
            change_num=diff,
            as_of=dt,
            source="FRED",
            note=spread_note,
        )
        if item:
            items.append(item)

    cpi_info = _compute_cpi_yoy(bundle.get("CPI") or [])
    if cpi_info:
        yoy, yoy_chg, cpi_dt, _ = cpi_info
        latest_cpi_dt, latest_cpi_idx = (bundle.get("CPI") or [])[-1]
        cpi_note = f"最新物价指数 {latest_cpi_idx:.2f} ({latest_cpi_dt[:7]}) · 口径为 CPIAUCSL 同比"
        item = _make_macro_item(
            item_id="cpi_yoy",
            group="美联储与利率",
            label="美国 CPI 同比",
            value_display=f"{yoy:.2f}%",
            raw_value=yoy,
            change_display=f"{yoy_chg:+.2f} pct",
            change_num=yoy_chg,
            as_of=cpi_dt,
            source="FRED",
            note=cpi_note,
        )
        if item:
            items.append(item)

    # Fed Net Liquidity = WALCL (M$) / 1000 - WTREGEN (M$) / 1000 - RRPONTSYD (B$)
    walcl_ser = bundle.get("WALCL") or []
    rrp_ser = bundle.get("RRPONTSYD") or []
    tga_ser = bundle.get("WTREGEN") or []
    if walcl_ser and rrp_ser:
        liq_points: list[tuple[str, float, float, float, float]] = []
        for dt, w_musd in walcl_ser:
            w_b = w_musd / 1000.0
            rrp_candidates = [v for d, v in rrp_ser if d <= dt]
            if not rrp_candidates:
                continue
            rrp_b = rrp_candidates[-1]
            tga_candidates = [v / 1000.0 for d, v in tga_ser if d <= dt] if tga_ser else []
            tga_b = tga_candidates[-1] if tga_candidates else 0.0
            net_b = w_b - tga_b - rrp_b
            if math.isfinite(net_b):
                liq_points.append((dt, net_b, w_b, tga_b, rrp_b))
        if liq_points:
            dt, net_b, w_b, tga_b, rrp_b = liq_points[-1]
            prev_b = liq_points[-2][1] if len(liq_points) >= 2 else net_b
            diff_b = net_b - prev_b
            tga_part = f" - TGA ${tga_b/1000:.2f}T" if tga_ser else ""
            item = _make_macro_item(
                item_id="fed_net_liquidity",
                group="美联储与利率",
                label="美联储净流动性",
                value_display=f"${net_b/1000:.2f}T",
                raw_value=net_b,
                change_display=f"{diff_b:+.1f}B",
                change_num=diff_b,
                as_of=dt,
                source="FRED",
                note=f"总资产 ${w_b/1000:.2f}T{tga_part} - 逆回购 ${rrp_b:.1f}B",
            )
            if item:
                items.append(item)

    # Cross-asset temperatures
    vix_ser = bundle.get("VIX") or []
    if vix_ser:
        dt, val = vix_ser[-1]
        prev = vix_ser[-2][1] if len(vix_ser) >= 2 else val
        diff = val - prev
        vix_note = "高于 30 将触发趋势减半风控闸门" if val < 30 else "已突破 30 恐慌阈值，风险预算收紧"
        item = _make_macro_item(
            item_id="vix",
            group="跨资产温度",
            label="VIX 恐慌指数",
            value_display=f"{val:.2f}",
            raw_value=val,
            change_display=f"{diff:+.2f}",
            change_num=diff,
            as_of=dt,
            source="FRED",
            note=vix_note,
        )
        if item:
            items.append(item)

    sp_ser = bundle.get("SP500") or []
    if sp_ser:
        dt, val = sp_ser[-1]
        prev = sp_ser[-2][1] if len(sp_ser) >= 2 else val
        pct = ((val - prev) / prev * 100.0) if prev else 0.0
        ma50_sp = mm.get("sp500_ma50")
        sp_note = f"50日均线 {float(ma50_sp):,.2f}（{'站上' if val >= float(ma50_sp) else '低于'}均线）" if _is_finite(ma50_sp) else "标普500 指数收盘水平"
        item = _make_macro_item(
            item_id="sp500",
            group="跨资产温度",
            label="标普 500",
            value_display=f"{val:,.2f}",
            raw_value=val,
            change_display=f"{pct:+.2f}%",
            change_num=pct,
            as_of=dt,
            source="FRED",
            note=sp_note,
        )
        if item:
            items.append(item)

    gold_ser = bundle.get("GOLD") or []
    if gold_ser:
        dt, val = gold_ser[-1]
        prev = gold_ser[-2][1] if len(gold_ser) >= 2 else val
        pct = ((val - prev) / prev * 100.0) if prev else 0.0
        ma100_g = mm.get("gold_ma100")
        g_note = f"100日均线 ${float(ma100_g):,.1f}（{'站上' if val >= float(ma100_g) else '弱于'}均线）" if _is_finite(ma100_g) else "COMEX 黄金期货主力"
        item = _make_macro_item(
            item_id="gold",
            group="跨资产温度",
            label="黄金 (COMEX)",
            value_display=f"${val:,.1f}",
            raw_value=val,
            change_display=f"{pct:+.2f}%",
            change_num=pct,
            as_of=dt,
            source="Market",
            note=g_note,
        )
        if item:
            items.append(item)

    dollar_ser = bundle.get("DOLLAR") or []
    if dollar_ser:
        dt, val = dollar_ser[-1]
        prev = dollar_ser[-2][1] if len(dollar_ser) >= 2 else val
        pct = ((val - prev) / prev * 100.0) if prev else 0.0
        item = _make_macro_item(
            item_id="dollar",
            group="跨资产温度",
            label="美元广义指数",
            value_display=f"{val:.2f}",
            raw_value=val,
            change_display=f"{pct:+.2f}%",
            change_num=pct,
            as_of=dt,
            source="FRED",
            note="贸易加权广义美元指数 (DTWEXBGS)",
        )
        if item:
            items.append(item)

    wti_ser = bundle.get("WTI") or []
    if wti_ser:
        dt, val = wti_ser[-1]
        prev = wti_ser[-2][1] if len(wti_ser) >= 2 else val
        pct = ((val - prev) / prev * 100.0) if prev else 0.0
        item = _make_macro_item(
            item_id="wti",
            group="跨资产温度",
            label="WTI 原油",
            value_display=f"${val:.2f}",
            raw_value=val,
            change_display=f"{pct:+.2f}%",
            change_num=pct,
            as_of=dt,
            source="FRED",
            note="美国西德克萨斯中质原油现货",
        )
        if item:
            items.append(item)

    # Crypto liquidity
    btc_live = _fetch_btc_live(quant_snap)
    if btc_live:
        price, chg, dt, src, note = btc_live
        item = _make_macro_item(
            item_id="btc_spot",
            group="加密流动性",
            label="BTC 现价",
            value_display=f"${price:,.0f}",
            raw_value=price,
            change_display=f"{chg:+.2f}%",
            change_num=chg,
            as_of=dt,
            source=src,
            note=note,
        )
        if item:
            items.append(item)

    p7 = sensors.get("p7_otc") or {}
    buy_bps = p7.get("premium_buy_bps")
    sell_bps = p7.get("premium_sell_bps")
    usdcnh = p7.get("usdcnh")
    if _is_finite(buy_bps):
        buy_val = float(buy_bps)
        sell_val = float(sell_bps) if _is_finite(sell_bps) else 0.0
        cnh_str = f" · 离岸汇率 {float(usdcnh):.4f}" if _is_finite(usdcnh) else ""
        item = _make_macro_item(
            item_id="otc_usdt_premium",
            group="加密流动性",
            label="场外 U 溢价",
            value_display=f"{buy_val:+.1f} bps",
            raw_value=buy_val,
            change_display=f"卖盘 {sell_val:+.1f} bps" if _is_finite(sell_bps) else "买盘参考",
            change_num=sell_val,
            as_of=str(p7.get("last_date") or datetime.now(BJT).strftime("%Y-%m-%d"))[:10],
            source="Market",
            note=f"C2C 场外买盘溢价{cnh_str}；高正溢价反映逢低承接资金活跃",
        )
        if item:
            items.append(item)

    if _is_finite(usdcnh) and float(usdcnh) > 0:
        cnh_val = float(usdcnh)
        item = _make_macro_item(
            item_id="usdcnh",
            group="加密流动性",
            label="离岸人民币 USD/CNH",
            value_display=f"{cnh_val:.4f}",
            raw_value=cnh_val,
            change_display="场外折算基准",
            change_num=0.0,
            as_of=str(p7.get("last_date") or datetime.now(BJT).strftime("%Y-%m-%d"))[:10],
            source="Market",
            note="用于锚定 USDT 人民币场外资金溢价水平",
        )
        if item:
            items.append(item)

    return items


def _fetch_tencent_quotes(symbols: list[str]) -> tuple[dict[str, dict[str, Any]], str]:
    """Fetch live US quotes from qt.gtimg.cn in 20-symbol batches (GBK decoded)."""
    if not symbols:
        return ({}, "")
    parsed: dict[str, dict[str, Any]] = {}
    quote_as_of = ""
    received_at = datetime.now(BJT).strftime("%Y-%m-%d %H:%M:%S+08:00")
    line_re = re.compile(r'^v_us([^=]+)="([^"]*)";?$')

    for idx in range(0, len(symbols), 20):
        batch = symbols[idx : idx + 20]
        q_param = ",".join(f"us{s}" for s in batch)
        url = f"https://qt.gtimg.cn/q={q_param}"
        resp = requests.get(url, headers=_HEADERS, timeout=12)
        resp.raise_for_status()
        raw_text = resp.content.decode("gbk", errors="replace")

        for line in raw_text.strip().splitlines():
            m = line_re.match(line.strip())
            if not m:
                continue
            req_sym = m.group(1).strip()
            parts = m.group(2).split("~")
            if len(parts) < 33:
                continue
            try:
                price = float(parts[3])
                prev_close = float(parts[4]) if parts[4] else 0.0
                chg_pct = float(parts[32]) if parts[32] else (
                    ((price - prev_close) / prev_close * 100.0) if prev_close > 0 else 0.0
                )
            except ValueError:
                continue
            if not math.isfinite(price) or price <= 0 or not math.isfinite(chg_pct):
                continue
            ts_str = parts[30].strip() if len(parts) > 30 else ""
            market_at = f"{ts_str} 美东" if ts_str else received_at
            if ts_str and not quote_as_of:
                quote_as_of = market_at
            parsed[req_sym] = {
                "symbol": req_sym,
                "name_raw": parts[1].strip(),
                "price": round(price, 2),
                "change_pct": round(chg_pct, 2),
                "market_at": market_at,
                "received_at": received_at,
                "source": "tencent_gtimg",
                "session": _infer_us_session(ts_str),
            }
    return (parsed, quote_as_of)


def _fetch_yahoo_fallback_equities(symbols: list[str]) -> dict[str, dict[str, Any]]:
    """Secondary live fallback via Yahoo Finance v8 chart endpoint."""
    out: dict[str, dict[str, Any]] = {}
    received_at = datetime.now(BJT).strftime("%Y-%m-%d %H:%M:%S+08:00")
    for sym in symbols:
        y_sym = "^IXIC" if sym == ".IXIC" else sym
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{y_sym}?range=5d&interval=1d"
            resp = requests.get(url, headers=_HEADERS, timeout=6)
            if resp.status_code != 200:
                continue
            res = ((resp.json() or {}).get("chart") or {}).get("result") or []
            if not res:
                continue
            meta = res[0].get("meta") or {}
            closes = [c for c in (((res[0].get("indicators") or {}).get("quote") or [{}])[0].get("close") or []) if _is_finite(c)]
            price = meta.get("regularMarketPrice") or (closes[-1] if closes else 0)
            prev = meta.get("chartPreviousClose") or (closes[-2] if len(closes) >= 2 else price)
            if not _is_finite(price) or float(price) <= 0:
                continue
            p_val = float(price)
            chg_pct = meta.get("regularMarketChangePercent")
            if not _is_finite(chg_pct) and _is_finite(prev) and float(prev) > 0:
                chg_pct = (p_val - float(prev)) / float(prev) * 100.0
            mkt_ts = meta.get("regularMarketTime")
            market_at = (
                datetime.fromtimestamp(int(mkt_ts), tz=BJT).strftime("%Y-%m-%d %H:%M:%S+08:00")
                if mkt_ts
                else received_at
            )
            out[sym] = {
                "symbol": sym,
                "name_raw": sym,
                "price": round(p_val, 2),
                "change_pct": round(float(chg_pct or 0.0), 2),
                "market_at": market_at,
                "received_at": received_at,
                "source": "yahoo_chart",
                "session": "regular" if meta.get("marketState") == "REGULAR" else "closed",
            }
        except Exception:
            continue
    return out


def _load_fallback_equities() -> tuple[dict[str, dict[str, Any]], str]:
    quotes_out: dict[str, dict[str, Any]] = {}
    as_of = ""
    received_at = datetime.now(BJT).strftime("%Y-%m-%d %H:%M:%S+08:00")
    if MARKET_SNAPSHOT_FALLBACK.exists():
        try:
            snap = json.loads(MARKET_SNAPSHOT_FALLBACK.read_text(encoding="utf-8"))
            snap_ts = str(snap.get("timestamp") or "")[:19]
            as_of = (snap_ts + " (缓存快照)") if snap_ts else ""
            for sym, row in (snap.get("quotes") or {}).items():
                price = row.get("price")
                chg_pct = row.get("change_pct")
                if _is_finite(price) and float(price) > 0 and _is_finite(chg_pct):
                    quotes_out[sym] = {
                        "symbol": sym,
                        "name_raw": str(row.get("name_cn") or sym),
                        "price": round(float(price), 2),
                        "change_pct": round(float(chg_pct), 2),
                        "market_at": snap_ts or received_at,
                        "received_at": received_at,
                        "source": "local_snapshot",
                        "session": "closed",
                    }
            # Alias GOOGL -> GOOG if needed
            if "GOOG" not in quotes_out and "GOOGL" in quotes_out:
                quotes_out["GOOG"] = dict(quotes_out["GOOGL"], symbol="GOOG")
        except Exception:
            pass
    return (quotes_out, as_of)


def _summarize_us_equities(
    indices: list[dict[str, Any]],
    mag7_and_ai: list[dict[str, Any]],
    sectors: list[dict[str, Any]],
) -> str:
    idx_map = {x["symbol"]: x for x in indices}
    parts: list[str] = []
    if ".IXIC" in idx_map:
        parts.append(f"纳指 {idx_map['.IXIC']['change_pct']:+.2f}%")
    if "SPY" in idx_map:
        parts.append(f"标普500ETF {idx_map['SPY']['change_pct']:+.2f}%")
    if "DIA" in idx_map:
        parts.append(f"道指ETF {idx_map['DIA']['change_pct']:+.2f}%")

    headline = "美股最新收盘：" + "，".join(parts) if parts else "美股盘面已更新"

    if mag7_and_ai:
        sorted_tech = sorted(mag7_and_ai, key=lambda x: x["change_pct"])
        weakest = sorted_tech[0]
        strongest = sorted_tech[-1]
        if weakest["change_pct"] <= -3.0:
            headline += f"；核心科技与算力承压（{weakest['name']} {weakest['change_pct']:+.2f}%）"
        elif strongest["change_pct"] >= 3.0:
            headline += f"；AI 算力领涨（{strongest['name']} {strongest['change_pct']:+.2f}%）"

    if sectors:
        best_sec = max(sectors, key=lambda x: x["change_pct"])
        worst_sec = min(sectors, key=lambda x: x["change_pct"])
        headline += (
            f"；板块中{best_sec['name']}({best_sec['change_pct']:+.2f}%)相对抗跌，"
            f"{worst_sec['name']}({worst_sec['change_pct']:+.2f}%)领跌。"
        )
    return headline


def fetch_us_equities() -> dict[str, Any]:
    """Fetch live US indices, MAG7 + AI compute, and S&P sectors via Tencent with fallback."""
    all_symbols = [
        sym for sym, _ in (US_INDICES + US_MAG7 + US_AI_COMPUTE + US_SECTORS)
    ]
    quotes: dict[str, dict[str, Any]] = {}
    as_of = ""
    try:
        quotes, as_of = _fetch_tencent_quotes(all_symbols)
    except Exception:
        quotes = {}

    if len(quotes) < 4:
        missing = [s for s in all_symbols if s not in quotes]
        for k, v in _fetch_yahoo_fallback_equities(missing[:12]).items():
            quotes.setdefault(k, v)
    if len(quotes) < 4:
        fb_quotes, fb_as_of = _load_fallback_equities()
        for k, v in fb_quotes.items():
            quotes.setdefault(k, v)
        if not as_of:
            as_of = fb_as_of

    def collect_group(pairs: list[tuple[str, str]], tag: str | None = None) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        now_rec = datetime.now(BJT).strftime("%Y-%m-%d %H:%M:%S+08:00")
        for sym, cn_name in pairs:
            q = quotes.get(sym)
            if not q:
                continue
            price = q["price"]
            chg = q["change_pct"]
            if not _is_finite(price) or price <= 0 or not _is_finite(chg):
                continue
            row: dict[str, Any] = {
                "symbol": sym,
                "name": cn_name,
                "price": round(float(price), 2),
                "change_pct": round(float(chg), 2),
                "market_at": q.get("market_at") or as_of or now_rec,
                "received_at": q.get("received_at") or now_rec,
                "source": q.get("source") or "tencent_gtimg",
                "session": q.get("session") or "closed",
            }
            if tag is not None:
                row["tag"] = tag
            out.append(row)
        return out

    indices = collect_group(US_INDICES)
    mag7_and_ai = collect_group(US_MAG7, tag="七巨头") + collect_group(US_AI_COMPUTE, tag="AI算力")
    sectors = collect_group(US_SECTORS)

    rep_row = (indices + mag7_and_ai + sectors)[0] if (indices or mag7_and_ai or sectors) else {}
    now_rec = datetime.now(BJT).strftime("%Y-%m-%d %H:%M:%S+08:00")
    return {
        "summary": _summarize_us_equities(indices, mag7_and_ai, sectors),
        "as_of": as_of or now_rec,
        "market_at": rep_row.get("market_at") or as_of or now_rec,
        "received_at": rep_row.get("received_at") or now_rec,
        "source": rep_row.get("source") or "tencent_gtimg",
        "session": rep_row.get("session") or "closed",
        "indices": indices,
        "mag7_and_ai": mag7_and_ai,
        "sectors": sectors,
    }


if __name__ == "__main__":
    macro = build_macro_fred(refresh_fred=False)
    eq = fetch_us_equities()
    print(f"macro_fred={len(macro)} indices={len(eq['indices'])} mag7_ai={len(eq['mag7_and_ai'])} sectors={len(eq['sectors'])}")
