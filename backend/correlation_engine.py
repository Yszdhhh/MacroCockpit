"""Cross-asset 30D/90D correlation analyzer & standalone Notion interactive embed HTML builder."""
from __future__ import annotations

import json
import math
import os
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import pandas as pd
except ImportError:
    pd = None  # type: ignore[assignment]
try:
    import requests
except ImportError:
    requests = None  # type: ignore[assignment]

from backend.runtime import BJT, DATA_DIR, DIST_DIR, UTC, atomic_write_json
from backend.fred_and_market import (
    FRED_CACHE_PATH,
    PARQUET_MACRO_DIR,
    fetch_gold_tail,
)

_BTC_ENV = (os.environ.get("MACROCOCKPIT_BTC_KLINES_PARQUET") or "").strip()
BTC_KLINES_CANDIDATES = (
    tuple(Path(p) for p in _BTC_ENV.split(os.pathsep) if p.strip())
    if _BTC_ENV
    else (DATA_DIR / "parquet_btc" / "BTCUSDT.parquet",)
)
NOTION_VIZ_HTML_PATH = DIST_DIR / "notion_viz_embed.html"

ASSET_ORDER: list[tuple[str, str, str]] = [
    ("BTC", "BTC", "pct"),
    ("SP500", "标普500", "pct"),
    ("GOLD", "黄金", "pct"),
    ("US10Y", "美债10Y", "diff"),
    ("DOLLAR", "美元指数", "pct"),
    ("VIX", "VIX", "diff"),
    ("WTI", "WTI原油", "pct"),
]

FLAGSHIP_PAIRS: list[tuple[str, str, str]] = [
    ("BTC", "SP500", "BTC × 标普500"),
    ("BTC", "GOLD", "BTC × 黄金"),
    ("BTC", "US10Y", "BTC × 美债10Y"),
    ("SP500", "US10Y", "标普500 × 美债10Y"),
    ("GOLD", "DOLLAR", "黄金 × 美元指数"),
    ("SP500", "VIX", "标普500 × VIX"),
]


def _read_fred_cache_bundle(cache_path: Path = FRED_CACHE_PATH) -> dict[str, list[tuple[str, float]]]:
    if not cache_path.is_file():
        return {}
    try:
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
        series = raw.get("series") if isinstance(raw, dict) else {}
        if not isinstance(series, dict):
            return {}
        out: dict[str, list[tuple[str, float]]] = {}
        for k, pts in series.items():
            if not isinstance(pts, list):
                continue
            by_day: dict[str, float] = {}
            for row in pts:
                if isinstance(row, (list, tuple)) and len(row) >= 2:
                    try:
                        val = float(row[1])
                    except (TypeError, ValueError):
                        continue
                    dt = str(row[0])[:10]
                    if dt and math.isfinite(val):
                        by_day[dt] = val
            if by_day:
                out[str(k)] = sorted(by_day.items(), key=lambda x: x[0])
        return out
    except Exception:
        return {}


def save_series_to_fred_cache(
    updates: dict[str, list[tuple[str, float]]],
    cache_path: Path = FRED_CACHE_PATH,
) -> None:
    if not updates:
        return
    try:
        raw = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.is_file() else {}
    except Exception:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    series = raw.get("series") if isinstance(raw.get("series"), dict) else {}
    for k, pts in updates.items():
        if not pts:
            continue
        merged: dict[str, float] = {}
        for row in series.get(k) or []:
            if isinstance(row, (list, tuple)) and len(row) >= 2:
                try:
                    merged[str(row[0])[:10]] = float(row[1])
                except (TypeError, ValueError):
                    pass
        for dt, val in pts:
            if math.isfinite(val):
                merged[str(dt)[:10]] = round(float(val), 6)
        series[k] = [[d, v] for d, v in sorted(merged.items(), key=lambda x: x[0])[-400:]]
    raw["updated_at_bjt"] = datetime.now(BJT).strftime("%Y-%m-%d %H:%M:%S+08:00")
    raw["series"] = series
    try:
        atomic_write_json(cache_path, raw)
    except OSError:
        pass


def fetch_btc_binance_daily(limit: int = 120, *, timeout: int = 6) -> list[tuple[str, float]]:
    """Fetch public daily BTCUSDT closes from Binance Vision (works on both CN VPS and overseas)."""
    if requests is None:
        return []
    try:
        resp = requests.get(
            "https://data-api.binance.vision/api/v3/klines",
            params={"symbol": "BTCUSDT", "interval": "1d", "limit": int(limit)},
            timeout=timeout,
        )
        if resp.status_code != 200:
            return []
        rows = resp.json()
        if not isinstance(rows, list):
            return []
        by_day: dict[str, float] = {}
        for kline in rows:
            if isinstance(kline, list) and len(kline) >= 5:
                try:
                    ts_ms = int(kline[0])
                    close_px = float(kline[4])
                except (TypeError, ValueError):
                    continue
                if math.isfinite(close_px) and close_px > 0:
                    day = datetime.fromtimestamp(ts_ms / 1000.0, tz=UTC).strftime("%Y-%m-%d")
                    by_day[day] = round(close_px, 2)
        return sorted(by_day.items(), key=lambda x: x[0])
    except Exception:
        return []


def _load_btc_daily_series(
    cache_bundle: dict[str, list[tuple[str, float]]] | None = None,
    *,
    cache_path: Path = FRED_CACHE_PATH,
    allow_remote: bool = True,
) -> list[tuple[str, float]]:
    if pd is not None:
        for path in BTC_KLINES_CANDIDATES:
            if not path.exists():
                continue
            try:
                df = pd.read_parquet(path)
                if "open_time" in df.columns and "close" in df.columns:
                    dt = pd.to_datetime(pd.to_numeric(df["open_time"], errors="coerce"), unit="ms", utc=True)
                    s = pd.Series(pd.to_numeric(df["close"], errors="coerce").to_numpy(), index=dt).dropna()
                    daily = s.groupby(s.index.tz_convert(None).normalize()).last()
                    daily.index = pd.DatetimeIndex(daily.index)
                    daily = daily[~daily.index.duplicated(keep="last")].sort_index()
                    if len(daily) >= 35:
                        return [(idx.strftime("%Y-%m-%d"), float(v)) for idx, v in daily.items() if math.isfinite(float(v))]
            except Exception:
                continue
    cached = (cache_bundle or {}).get("BTC") or []
    if len(cached) >= 35 or not allow_remote:
        return cached
    fetched = fetch_btc_binance_daily(limit=120)
    if fetched:
        save_series_to_fred_cache({"BTC": fetched}, cache_path=cache_path)
        return fetched
    return cached


def _load_macro_close_series(
    key: str,
    cache_bundle: dict[str, list[tuple[str, float]]] | None = None,
    *,
    cache_path: Path = FRED_CACHE_PATH,
    allow_remote: bool = True,
) -> list[tuple[str, float]]:
    if pd is not None:
        path = PARQUET_MACRO_DIR / f"{key}.parquet"
        if path.exists():
            try:
                df = pd.read_parquet(path)
                if "close" in df.columns:
                    s = pd.to_numeric(df["close"], errors="coerce").dropna()
                    idx = pd.DatetimeIndex(pd.to_datetime(s.index)).tz_localize(None).normalize()
                    out = pd.Series(s.to_numpy(dtype=float), index=idx)
                    out = out[~out.index.duplicated(keep="last")].sort_index()
                    if len(out) >= 35:
                        return [(d.strftime("%Y-%m-%d"), float(v)) for d, v in out.items() if math.isfinite(float(v))]
            except Exception:
                pass
    cached = list((cache_bundle or {}).get(key) or [])
    if key == "GOLD" and len(cached) < 35 and allow_remote:
        gold_tail = fetch_gold_tail()
        if gold_tail:
            merged = dict(cached)
            merged.update(gold_tail)
            cached = sorted(merged.items(), key=lambda x: x[0])
            save_series_to_fred_cache({"GOLD": cached}, cache_path=cache_path)
    return cached


def _series_changes(points: list[tuple[str, float]], mode: str) -> dict[str, float]:
    if len(points) < 2:
        return {}
    out: dict[str, float] = {}
    prev_val = points[0][1]
    for dt, val in points[1:]:
        if mode == "pct":
            if prev_val != 0.0:
                chg = (val - prev_val) / prev_val
                if math.isfinite(chg):
                    out[dt] = chg
        else:
            chg = val - prev_val
            if math.isfinite(chg):
                out[dt] = chg
        prev_val = val
    return out


def classify_divergence(r30: float | None, r90: float | None) -> str:
    """Classify correlation regime shift between 30D and 90D windows."""
    if r30 is None or r90 is None or not (math.isfinite(r30) and math.isfinite(r90)):
        return "正常联动"
    delta = abs(r30 - r90)
    sign_flip = (r30 * r90 < 0) and (max(abs(r30), abs(r90)) >= 0.25)
    if delta >= 0.35 or sign_flip:
        return "显著背离"
    if abs(r30) >= 0.80:
        return "极值警戒"
    return "正常联动"


def compute_cross_asset_correlations(
    *,
    fred_cache_path: Path = FRED_CACHE_PATH,
    allow_remote: bool = True,
) -> dict[str, Any]:
    """Compute real 30D & 90D Pearson correlation matrices using pure NumPy + fred_cache/Parquet."""
    try:
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("State refresh requires pandas and numpy") from exc

    cache_bundle = _read_fred_cache_bundle(fred_cache_path)
    raw_map: dict[str, list[tuple[str, float]]] = {
        "BTC": _load_btc_daily_series(cache_bundle, cache_path=fred_cache_path, allow_remote=allow_remote),
        "SP500": _load_macro_close_series("SP500", cache_bundle, cache_path=fred_cache_path, allow_remote=allow_remote),
        "GOLD": _load_macro_close_series("GOLD", cache_bundle, cache_path=fred_cache_path, allow_remote=allow_remote),
        "US10Y": _load_macro_close_series("US10Y", cache_bundle, cache_path=fred_cache_path, allow_remote=allow_remote),
        "DOLLAR": _load_macro_close_series("DOLLAR", cache_bundle, cache_path=fred_cache_path, allow_remote=allow_remote),
        "VIX": _load_macro_close_series("VIX", cache_bundle, cache_path=fred_cache_path, allow_remote=allow_remote),
        "WTI": _load_macro_close_series("WTI", cache_bundle, cache_path=fred_cache_path, allow_remote=allow_remote),
    }

    chg_map: dict[str, dict[str, float]] = {}
    meta_by_key = {k: (lbl, mode) for k, lbl, mode in ASSET_ORDER}
    for key, _, mode in ASSET_ORDER:
        ser = raw_map.get(key) or []
        if len(ser) < 35:
            continue
        chg = _series_changes(ser, mode)
        if len(chg) >= 30:
            chg_map[key] = chg

    active_keys = [k for k, _, _ in ASSET_ORDER if k in chg_map]
    labels = [meta_by_key[k][0] for k in active_keys]

    def _pair_corr(k1: str, k2: str, window: int) -> float | None:
        if k1 == k2:
            return 1.0
        s1 = chg_map.get(k1)
        s2 = chg_map.get(k2)
        if not s1 or not s2:
            return None
        common = sorted(d for d in s1 if d in s2)
        if len(common) < min(15, window // 2):
            return None
        sub_dates = common[-window:]
        a = np.array([s1[d] for d in sub_dates], dtype=float)
        b = np.array([s2[d] for d in sub_dates], dtype=float)
        if float(np.std(a)) == 0.0 or float(np.std(b)) == 0.0:
            return 0.0
        val = float(np.corrcoef(a, b)[0, 1])
        return round(val, 2) if math.isfinite(val) else None

    matrix_30d: list[list[float | None]] = []
    matrix_90d: list[list[float | None]] = []
    for k1 in active_keys:
        row30: list[float | None] = []
        row90: list[float | None] = []
        for k2 in active_keys:
            row30.append(_pair_corr(k1, k2, 30))
            row90.append(_pair_corr(k1, k2, 90))
        matrix_30d.append(row30)
        matrix_90d.append(row90)

    pairs: list[dict[str, Any]] = []
    latest_date = ""
    for ser in raw_map.values():
        if ser:
            dstr = ser[-1][0]
            if dstr > latest_date:
                latest_date = dstr

    for k1, k2, pair_name in FLAGSHIP_PAIRS:
        if k1 not in chg_map or k2 not in chg_map:
            continue
        r30 = _pair_corr(k1, k2, 30)
        r90 = _pair_corr(k1, k2, 90)
        if r30 is None or r90 is None:
            continue
        div = classify_divergence(r30, r90)
        delta = round(r30 - r90, 2)
        if div == "显著背离":
            note = f"30D与90D相关性差 {delta:+.2f}，近期定价逻辑出现明显切换"
        elif div == "极值警戒":
            note = f"30D相关性达 {r30:+.2f} 高耦合区间，注意同涨同跌共振波动"
        else:
            note = "30D与90D相关性结构稳定"
        pairs.append(
            {
                "id": f"corr_{k1.lower()}_{k2.lower()}",
                "pair": pair_name,
                "corr_30d": r30,
                "corr_90d": r90,
                "delta": delta,
                "divergence": div,
                "as_of": latest_date or datetime.now(BJT).strftime("%Y-%m-%d"),
                "note": note,
            }
        )

    return {
        "as_of": latest_date or datetime.now(BJT).strftime("%Y-%m-%d"),
        "assets": active_keys,
        "labels": labels,
        "matrix_30d": matrix_30d,
        "matrix_90d": matrix_90d,
        "pairs": pairs,
    }


def generate_notion_viz_html(
    state: dict[str, Any],
    corr_data: dict[str, Any],
    *,
    out_path: Path = NOTION_VIZ_HTML_PATH,
) -> Path:
    """Generate a lightweight standalone HTML embed (< 70 KiB) for Notion iframe display."""
    equities = state.get("us_equities") or {}
    macro = state.get("macro_fred") or []
    macro_by_id = {str(item.get("id", "")): item for item in macro if isinstance(item, dict)} if isinstance(macro, list) else {}
    equity_rows = []
    for key in ("indices", "mag7_and_ai"):
        rows = equities.get(key, []) if isinstance(equities, dict) else []
        if isinstance(rows, list):
            equity_rows.extend(row for row in rows if isinstance(row, dict))
    ticker_symbols = [".IXIC", "SPY", "QQQ", "NVDA", "AMD", "MU"]
    tickers = []
    for symbol in ticker_symbols:
        item = next((row for row in equity_rows if str(row.get("symbol", row.get("ticker", ""))).upper() == symbol or (symbol == ".IXIC" and str(row.get("symbol", "")).upper() in {"^IXIC", "IXIC"})), None)
        if item:
            tickers.append({"symbol": symbol, "price": item.get("price"), "change": item.get("change_pct")})
    for key in ("btc_spot", "vix", "us_10y", "fed_net_liquidity"):
        item = macro_by_id.get(key)
        if item:
            tickers.append({
                "symbol": str(item.get("label") or key),
                "price": item.get("value_display", item.get("raw_value")),
                "change": item.get("change_num"),
                "change_text": item.get("change_display"),
            })
    payload = {
        "generated_at_bjt": state.get("generated_at_bjt") or datetime.now(BJT).strftime("%Y-%m-%d %H:%M:%S+08:00"),
        "corr": corr_data,
        "tickers": tickers,
        "kol_digest": [
            {
                "handle": k.get("handle", ""),
                "group": k.get("group", ""),
                "topic": k.get("topic", "宏观与市场情绪"),
                "stance": k.get("stance", "中性"),
                "score": k.get("score", 0.0),
                "date": k.get("date", ""),
                "headline": k.get("headline", ""),
                "takeaway": k.get("takeaway", ""),
                "url": k.get("url", ""),
            }
            for k in (state.get("kol_digest") or [])
        ],
    }
    embedded_json = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")

    html_doc = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>跨资产相关性与KOL多空对抗</title>
<style>
:root {{
  --bg:#FFFFFF; --surface:#FAFAF9; --ink:#16140F; --sub:#6E675B; --rule:#E7E2DA;
  --accent:#6D28D9; --accent-soft:#F3E8FF; --bull:#15803D; --bull-bg:#DCFCE7;
  --bear:#B91C1C; --bear-bg:#FEE2E2; --neu:#57534E; --neu-bg:#F5F5F4;
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --bg:#191919; --surface:#222220; --ink:#EFECE6; --sub:#A8A29E; --rule:#33322E;
    --accent:#A78BFA; --accent-soft:#2E1F47; --bull:#4ADE80; --bull-bg:#143322;
    --bear:#F87171; --bear-bg:#3B1618; --neu:#D6D3D1; --neu-bg:#292524;
  }}
}}
* {{ box-sizing:border-box; }}
body {{
  margin:0; padding:14px 16px; background:var(--bg); color:var(--ink);
  font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
}}
.topbar {{
  display:flex; flex-wrap:wrap; align-items:center; justify-content:space-between; gap:10px;
  padding-bottom:10px; border-bottom:1px solid var(--rule); margin-bottom:12px;
}}
.tabs {{ display:flex; gap:8px; flex-wrap:wrap; }}
.btn {{
  border:1px solid var(--rule); background:var(--surface); color:var(--ink);
  padding:6px 12px; border-radius:8px; cursor:pointer; font-size:13px; font-weight:600;
}}
.btn.active {{ background:var(--accent); color:#fff; border-color:var(--accent); }}
.meta {{ font-size:12px; color:var(--sub); font-variant-numeric:tabular-nums; }}
.panel {{ display:none; }}
.panel.active {{ display:block; }}
.pairs {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:10px; margin-bottom:14px; }}
.card {{
  border:1px solid var(--rule); background:var(--surface); border-radius:10px; padding:10px 12px;
}}
.card-head {{ display:flex; justify-content:space-between; align-items:center; gap:6px; margin-bottom:4px; }}
.pair-name {{ font-weight:700; font-size:13px; }}
.badge {{ font-size:11px; padding:2px 7px; border-radius:99px; font-weight:600; }}
.badge.div {{ background:var(--bear-bg); color:var(--bear); }}
.badge.ok {{ background:var(--bull-bg); color:var(--bull); }}
.badge.warn {{ background:var(--accent-soft); color:var(--accent); }}
.nums {{ font-family:ui-monospace,Consolas,monospace; font-variant-numeric:tabular-nums; font-size:13px; }}
.note {{ font-size:12px; color:var(--sub); margin-top:4px; }}
.matrix-wrap {{ overflow-x:auto; border:1px solid var(--rule); border-radius:10px; }}
table {{ width:100%; border-collapse:collapse; font-size:12px; }}
th,td {{ padding:7px 8px; text-align:right; border-bottom:1px solid var(--rule); font-variant-numeric:tabular-nums; }}
th:first-child,td:first-child {{ text-align:left; font-weight:600; position:sticky; left:0; background:var(--surface); }}
.showdown-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:12px; margin-bottom:14px; }}
@media (max-width:720px) {{ .showdown-grid {{ grid-template-columns:1fr; }} }}
.camp {{ border:1px solid var(--rule); border-radius:10px; padding:10px 12px; background:var(--surface); }}
.camp h4 {{ margin:0 0 8px 0; font-size:13px; display:flex; justify-content:space-between; }}
.camp.bull h4 {{ color:var(--bull); }}
.camp.bear h4 {{ color:var(--bear); }}
.kol-item {{ padding:8px 0; border-top:1px solid var(--rule); }}
.kol-item:first-of-type {{ border-top:none; padding-top:0; }}
.kol-meta {{ display:flex; justify-content:space-between; font-size:12px; color:var(--sub); }}
.kol-title {{ font-weight:600; margin:2px 0; font-size:13px; }}
.timeline {{ border-left:2px solid var(--accent); padding-left:12px; margin-top:8px; }}
.tl-node {{ position:relative; margin-bottom:10px; background:var(--surface); border:1px solid var(--rule); border-radius:8px; padding:8px 10px; }}
.tl-dot {{ position:absolute; left:-18px; top:12px; width:10px; height:10px; border-radius:50%; background:var(--accent); }}
.pill {{ display:inline-block; font-size:11px; padding:1px 6px; border-radius:6px; margin-right:6px; }}
.pill.bull {{ background:var(--bull-bg); color:var(--bull); }}
.pill.bear {{ background:var(--bear-bg); color:var(--bear); }}
.pill.neu {{ background:var(--neu-bg); color:var(--neu); }}
.ticker {{ display:flex; gap:8px; overflow-x:auto; padding:8px 0 12px; border-bottom:1px solid var(--rule); margin-bottom:12px; }}
.ticker-item {{ flex:0 0 auto; min-width:112px; padding:7px 9px; border:1px solid var(--rule); border-radius:8px; background:var(--surface); }}
.ticker-symbol {{ color:var(--sub); font-size:11px; }}
.ticker-value {{ font:600 13px ui-monospace,Consolas,monospace; font-variant-numeric:tabular-nums; }}
.change {{ font-size:10px; border-radius:99px; padding:1px 5px; margin-left:4px; }}
.change.up {{ color:var(--bull); background:var(--bull-bg); }} .change.down {{ color:var(--bear); background:var(--bear-bg); }}
.kol-link {{ color:inherit; text-decoration:none; }} .kol-link:hover {{ color:var(--accent); text-decoration:underline; }}
@media (max-width:480px) {{ body {{ padding:10px; }} .btn {{ padding:6px 9px; }} th,td {{ padding:6px 5px; }} .pairs {{ grid-template-columns:1fr; }} }}
</style>
</head>
<body>
<div class="ticker" id="ticker-strip"></div>
<div class="topbar">
  <div class="tabs">
    <button class="btn active" data-tab="corr">跨资产相关性与背离</button>
    <button class="btn" data-tab="kol">KOL 多空对抗时间树</button>
  </div>
  <div class="meta" id="meta-ts"></div>
</div>

<div id="panel-corr" class="panel active">
  <div style="display:flex;gap:8px;align-items:center;margin-bottom:10px;">
    <button class="btn active" data-win="30d">近 30 个交易日</button>
    <button class="btn" data-win="90d">近 90 个交易日</button>
    <span class="meta" id="corr-asof"></span>
  </div>
  <div class="pairs" id="pairs-grid"></div>
  <div class="matrix-wrap"><table id="corr-table"></table></div>
</div>

<div id="panel-kol" class="panel">
  <div class="tabs" id="topic-filters" style="margin-bottom:10px;"></div>
  <div class="showdown-grid">
    <div class="camp bull"><h4><span>多头阵营</span><span id="cnt-bull">0</span></h4><div id="list-bull"></div></div>
    <div class="camp bear"><h4><span>空头阵营</span><span id="cnt-bear">0</span></h4><div id="list-bear"></div></div>
  </div>
  <div style="font-weight:700;font-size:13px;margin-bottom:6px;">观点演化时间树</div>
  <div class="timeline" id="kol-timeline"></div>
</div>

<script>
const DATA = {embedded_json};
let activeWin = "30d";
let activeTopic = "全部议题";

function esc(s) {{
  return String(s||"").replace(/[&<>"]/g, c => ({{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}}[c]));
}}

document.getElementById("meta-ts").textContent = "数据同步：" + (DATA.generated_at_bjt || "");
document.getElementById("corr-asof").textContent = "行情对齐至：" + ((DATA.corr && DATA.corr.as_of) || "");
document.getElementById("ticker-strip").innerHTML = (DATA.tickers || []).map(t => {{
  const n = Number(t.change); const cls = Number.isFinite(n) ? (n >= 0 ? "up" : "down") : "";
  const txt = t.change_text ? String(t.change_text) : (Number.isFinite(n) ? `${{n >= 0 ? "+" : ""}}${{n.toFixed(2)}}%` : "");
  const change = txt ? `<span class="change ${{cls}}">${{esc(txt)}}</span>` : "";
  return `<div class="ticker-item"><div class="ticker-symbol">${{esc(t.symbol)}}</div><div class="ticker-value">${{esc(t.price)}}${{change}}</div></div>`;
}}).join("");

document.querySelectorAll("[data-tab]").forEach(b => {{
  b.addEventListener("click", () => {{
    document.querySelectorAll("[data-tab]").forEach(x => x.classList.remove("active"));
    document.querySelectorAll(".panel").forEach(x => x.classList.remove("active"));
    b.classList.add("active");
    document.getElementById("panel-" + b.dataset.tab).classList.add("active");
  }});
}});

document.querySelectorAll("[data-win]").forEach(b => {{
  b.addEventListener("click", () => {{
    document.querySelectorAll("[data-win]").forEach(x => x.classList.remove("active"));
    b.classList.add("active");
    activeWin = b.dataset.win;
    renderCorr();
  }});
}});

function cellColor(v) {{
  if (v === null || v === undefined || Number.isNaN(v)) return "transparent";
  const a = Math.min(0.32, Math.abs(v) * 0.34);
  return v >= 0 ? `rgba(21,128,61,${{a.toFixed(2)}})` : `rgba(185,28,28,${{a.toFixed(2)}})`;
}}

function renderCorr() {{
  const c = DATA.corr || {{}};
  const pairs = c.pairs || [];
  document.getElementById("pairs-grid").innerHTML = pairs.map(p => {{
    const cls = p.divergence === "显著背离" ? "div" : (p.divergence === "极值警戒" ? "warn" : "ok");
    return `<div class="card">
      <div class="card-head"><span class="pair-name">${{esc(p.pair)}}</span><span class="badge ${{cls}}">${{esc(p.divergence)}}</span></div>
      <div class="nums">30D: <b>${{p.corr_30d >= 0 ? "+" : ""}}${{p.corr_30d.toFixed(2)}}</b> · 90D: ${{p.corr_90d >= 0 ? "+" : ""}}${{p.corr_90d.toFixed(2)}}</div>
      <div class="note">${{esc(p.note)}}</div>
    </div>`;
  }}).join("");

  const labels = c.labels || [];
  const mat = activeWin === "30d" ? (c.matrix_30d || []) : (c.matrix_90d || []);
  let h = "<thead><tr><th>资产</th>" + labels.map(l => `<th>${{esc(l)}}</th>`).join("") + "</tr></thead><tbody>";
  labels.forEach((lbl, i) => {{
    h += `<tr><td>${{esc(lbl)}}</td>`;
    (mat[i] || []).forEach(v => {{
      const txt = (v === null || v === undefined) ? "—" : ((v >= 0 ? "+" : "") + Number(v).toFixed(2));
      h += `<td style="background:${{cellColor(v)}}">${{txt}}</td>`;
    }});
    h += "</tr>";
  }});
  h += "</tbody>";
  document.getElementById("corr-table").innerHTML = h;
}}

function renderKol() {{
  const rows = DATA.kol_digest || [];
  const topics = ["全部议题", ...Array.from(new Set(rows.map(r => r.topic).filter(Boolean)))];
  const tf = document.getElementById("topic-filters");
  tf.innerHTML = topics.map(t => `<button class="btn ${{t === activeTopic ? "active" : ""}}" data-topic="${{esc(t)}}">${{esc(t)}}</button>`).join("");
  tf.querySelectorAll("[data-topic]").forEach(b => b.addEventListener("click", () => {{ activeTopic = b.dataset.topic; renderKol(); }}));

  const sub = activeTopic === "全部议题" ? rows : rows.filter(r => r.topic === activeTopic);
  const bulls = sub.filter(r => r.stance === "偏多");
  const bears = sub.filter(r => r.stance === "偏空");

  document.getElementById("cnt-bull").textContent = bulls.length + " 席";
  document.getElementById("cnt-bear").textContent = bears.length + " 席";

  const link = k => /^https?:\\/\\//i.test(String(k.url || "")) ? `<a class="kol-link" href="${{esc(k.url)}}" target="_blank" rel="noopener noreferrer">${{esc(k.headline)}}</a>` : esc(k.headline);
  const renderCamp = list => list.length ? list.map(k => `<div class="kol-item">
    <div class="kol-meta"><span>@${{esc(k.handle)}} · ${{esc(k.topic)}}</span><span>${{esc(k.date)}} (${{Number(k.score).toFixed(2)}})</span></div>
    <div class="kol-title">${{link(k)}}</div>
    <div class="note">${{esc(k.takeaway)}}</div>
  </div>`).join("") : `<div class="note">当前议题暂无该阵营代表</div>`;

  document.getElementById("list-bull").innerHTML = renderCamp(bulls);
  document.getElementById("list-bear").innerHTML = renderCamp(bears);

  const sorted = [...sub].sort((a, b) => String(b.date).localeCompare(String(a.date)));
  document.getElementById("kol-timeline").innerHTML = sorted.map(k => {{
    const pcls = k.stance === "偏多" ? "bull" : (k.stance === "偏空" ? "bear" : "neu");
    const plbl = k.stance === "偏多" ? "多头阵营" : (k.stance === "偏空" ? "空头阵营" : "中性观察");
    return `<div class="tl-node"><span class="tl-dot"></span>
      <div class="kol-meta"><span><span class="pill ${{pcls}}">${{plbl}}</span>@${{esc(k.handle)}} · ${{esc(k.topic)}}</span><span>${{esc(k.date)}}</span></div>
      <div class="kol-title">${{link(k)}}</div>
      <div class="note">${{esc(k.takeaway)}}</div>
    </div>`;
  }}).join("");
}}

renderCorr();
renderKol();
</script>
</body>
</html>
"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_doc, encoding="utf-8")
    return out_path
