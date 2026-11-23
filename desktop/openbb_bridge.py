"""Lazy OpenBB Platform bridge and sanitized OpenBB Workspace widget provider."""
from __future__ import annotations

import importlib.metadata
import json
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:
    requests = None  # type: ignore[assignment]

from backend.public_snapshot import has_private_debate_text

COCKPIT_ROOT = Path(os.environ.get("MACROCOCKPIT_ROOT") or Path(__file__).resolve().parent.parent)
DATA_DIR = Path(os.environ.get("MACROCOCKPIT_DATA_DIR") or (COCKPIT_ROOT / "data"))
STATE_PATH = DATA_DIR / "cockpit_state.json"
DEBATES_PATH = DATA_DIR / "desktop_debates.json"

OPENBB_API_BASE = os.environ.get("OPENBB_API_URL", "http://127.0.0.1:6900/api/v1").rstrip("/")
OPENBB_SETTINGS_PATH = Path.home() / ".openbb_platform" / "user_settings.json"

_BJT = timezone(timedelta(hours=8))
_SDK_LOCK = threading.Lock()
_OBB_INSTANCE: Any = None


def _now_iso() -> str:
    return datetime.now(_BJT).isoformat(timespec="seconds")


def _read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _configured_credential_names() -> list[str]:
    data = _read_json(OPENBB_SETTINGS_PATH, {})
    if not isinstance(data, dict):
        return []
    creds = data.get("credentials")
    if not isinstance(creds, dict):
        return []
    return sorted(str(k) for k, v in creds.items() if k and str(v or "").strip())


def _check_openbb_api_online(timeout: float = 0.8) -> bool:
    if requests is None:
        return False
    root = OPENBB_API_BASE.removesuffix("/api/v1")
    for probe_url in (f"{root}/openapi.json", f"{OPENBB_API_BASE}"):
        try:
            resp = requests.get(probe_url, timeout=timeout)
            if 200 <= resp.status_code < 500:
                return True
        except Exception:
            continue
    return False


def probe_openbb_status() -> dict[str, Any]:
    """Probe local OpenBB version, REST API availability, and credential key names without importing openbb."""
    version: str | None = None
    installed = False
    try:
        version = importlib.metadata.version("openbb")
        installed = True
    except importlib.metadata.PackageNotFoundError:
        installed = False

    providers_count = 0
    try:
        providers_count = sum(
            1
            for d in importlib.metadata.distributions()
            if str(d.metadata.get("Name") or "").lower().startswith("openbb-")
        )
    except Exception:
        providers_count = 0

    cred_names = _configured_credential_names()
    api_online = _check_openbb_api_online()
    return {
        "installed": installed,
        "version": version,
        "providers_count": providers_count,
        "api_online": api_online,
        "api_url": OPENBB_API_BASE,
        "configured_credentials": cred_names,
        "configured_keys": cred_names,
        "has_fred_key": "fred_api_key" in cred_names or bool((os.environ.get("FRED_API_KEY") or "").strip()),
        "sdk_lazy_loaded": _OBB_INSTANCE is not None,
        "checked_at": _now_iso(),
    }


def _get_obb_sdk() -> Any:
    """Lazy-import OpenBB SDK on demand (avoids 8-10s cold import at startup)."""
    global _OBB_INSTANCE
    if _OBB_INSTANCE is not None:
        return _OBB_INSTANCE
    with _SDK_LOCK:
        if _OBB_INSTANCE is None:
            from openbb import obb  # noqa: PLC0415

            _OBB_INSTANCE = obb
    return _OBB_INSTANCE


def _sdk_rows(res: Any, limit: int = 30) -> list[dict[str, Any]]:
    if res is None:
        return []
    try:
        df = res.to_dataframe()
        if df is not None and not df.empty:
            df_tail = df.tail(limit).reset_index()
            return json.loads(df_tail.to_json(orient="records", date_format="iso"))
    except Exception:
        pass
    results = getattr(res, "results", None)
    if isinstance(results, list):
        out: list[dict[str, Any]] = []
        for item in results[:limit]:
            if hasattr(item, "model_dump"):
                out.append(item.model_dump(mode="json"))
            elif isinstance(item, dict):
                out.append(item)
        return out
    return []


def _query_openbb_http(dataset: str, symbol: str, *, limit: int = 30, timeout: float = 5.0) -> Any | None:
    if requests is None:
        return None
    endpoint_map: dict[str, tuple[str, dict[str, Any]]] = {
        "equity_quote": (f"{OPENBB_API_BASE}/equity/price/quote", {"symbol": symbol, "provider": "yfinance"}),
        "equity_fundamental": (f"{OPENBB_API_BASE}/equity/fundamental/metrics", {"symbol": symbol, "provider": "yfinance"}),
        "macro_series": (f"{OPENBB_API_BASE}/economy/fred_series", {"symbol": symbol or "DGS10", "limit": limit}),
        "crypto_quote": (f"{OPENBB_API_BASE}/crypto/price/historical", {"symbol": f"{symbol}-USD" if "-" not in symbol else symbol, "provider": "yfinance"}),
    }
    if dataset not in endpoint_map:
        return None
    url, params = endpoint_map[dataset]
    try:
        resp = requests.get(url, params=params, timeout=timeout)
        if resp.status_code != 200:
            return None
        payload = resp.json()
        results = payload.get("results") if isinstance(payload, dict) else payload
        if isinstance(results, list):
            return results[:limit]
        return results
    except Exception:
        return None


def _query_openbb_sdk(dataset: str, symbol: str, *, limit: int = 30) -> Any | None:
    try:
        obb = _get_obb_sdk()
        if dataset == "equity_quote":
            return _sdk_rows(obb.equity.price.quote(symbol=symbol, provider="yfinance"), limit=limit)
        if dataset == "equity_fundamental":
            return _sdk_rows(obb.equity.fundamental.metrics(symbol=symbol, provider="yfinance"), limit=limit)
        if dataset == "macro_series":
            return _sdk_rows(obb.economy.fred_series(symbol=symbol or "DGS10"), limit=limit)
        if dataset == "crypto_quote":
            pair = symbol if "-" in symbol else f"{symbol}-USD"
            return _sdk_rows(obb.crypto.price.historical(symbol=pair, provider="yfinance"), limit=limit)
    except Exception:
        return None
    return None


def _fallback_cockpit_dataset(dataset: str, symbol: str, *, limit: int = 30) -> tuple[str, Any]:
    state = _read_json(STATE_PATH, {}) or {}
    sym = symbol.strip().upper()

    if dataset == "equity_quote":
        profiles = state.get("stock_profiles") or {}
        if sym in profiles:
            p = profiles[sym]
            return (
                "cockpit_state",
                [
                    {
                        "symbol": sym,
                        "name": p.get("name_cn") or p.get("name_en") or sym,
                        "price": p.get("price"),
                        "change_pct": p.get("change_pct"),
                        "ma20": p.get("ma20"),
                        "ma50": p.get("ma50"),
                        "bias20_pct": p.get("bias20_pct"),
                        "as_of": p.get("as_of"),
                    }
                ],
            )
        eq = state.get("us_equities") or {}
        for bucket in ("indices", "mag7_and_ai", "sectors"):
            for row in eq.get(bucket) or []:
                if str(row.get("symbol") or "").upper() == sym:
                    return ("cockpit_state", [row])
        try:
            from backend.stock_research import fetch_single_stock_profile

            prof = fetch_single_stock_profile(sym)
            if prof:
                return (
                    "stock_research",
                    [
                        {
                            "symbol": prof["symbol"],
                            "name": prof.get("name_cn") or prof["symbol"],
                            "price": prof.get("price"),
                            "change_pct": prof.get("change_pct"),
                            "ma20": prof.get("ma20"),
                            "ma50": prof.get("ma50"),
                            "bias20_pct": prof.get("bias20_pct"),
                            "as_of": prof.get("as_of"),
                        }
                    ],
                )
        except Exception:
            pass
        return ("cockpit_state", [])

    if dataset == "equity_fundamental":
        profiles = state.get("stock_profiles") or {}
        prof = profiles.get(sym)
        if prof is None:
            try:
                from backend.stock_research import fetch_single_stock_profile

                prof = fetch_single_stock_profile(sym)
            except Exception:
                prof = None
        if not isinstance(prof, dict):
            return ("cockpit_state", {})
        return (
            "cockpit_state",
            {
                "symbol": sym,
                "name": prof.get("name_cn") or sym,
                "overview": prof.get("overview") or {},
                "valuation": prof.get("valuation") or {},
                "financials": prof.get("financials") or {},
                "health": prof.get("health") or {},
                "sec_filings": prof.get("sec_filings") or [],
            },
        )

    if dataset == "macro_series":
        items = state.get("macro_fred") or []
        if sym:
            matched = [
                m
                for m in items
                if sym in str(m.get("id") or "").upper()
                or sym in str(m.get("label") or "").upper()
                or sym in str(m.get("note") or "").upper()
            ]
            if matched:
                return ("cockpit_state", matched[:limit])
        return ("cockpit_state", items[:limit])

    if dataset == "crypto_quote":
        cq = state.get("crypto_quotes") or {}
        if not cq:
            try:
                from backend.market_quotes import fetch_crypto_quotes

                cq = fetch_crypto_quotes(symbols=[sym] if sym else None, timeout=3)
            except Exception:
                cq = {}
        spot = cq.get("spot") or {}
        swap = cq.get("swap") or {}
        rows: list[dict[str, Any]] = []
        for bucket in (spot, swap):
            for inst_id, row in bucket.items():
                if not sym or sym in inst_id.upper() or str(row.get("symbol") or "").upper() == sym:
                    rows.append(row)
        return ("market_quotes" if rows else "cockpit_state", rows[:limit])

    return ("cockpit_state", [])


def query_openbb(
    dataset: str,
    symbol: str = "",
    *,
    allow_sdk_import: bool = False,
    limit: int = 30,
) -> dict[str, Any]:
    """Query OpenBB REST API (if online), optional lazy SDK, or local MacroCockpit fallback."""
    ds = str(dataset or "").strip().lower()
    if ds not in ("equity_quote", "equity_fundamental", "macro_series", "crypto_quote"):
        raise ValueError(f"unsupported OpenBB dataset: {dataset!r}")

    sym = str(symbol or "").strip().upper()
    if ds in ("equity_quote", "equity_fundamental") and not sym:
        sym = "NVDA"
    if ds == "crypto_quote" and not sym:
        sym = "BTC"

    lim = max(1, min(200, int(limit)))

    if _check_openbb_api_online(timeout=0.6):
        http_res = _query_openbb_http(ds, sym, limit=lim)
        if http_res:
            return {
                "ok": True,
                "dataset": ds,
                "symbol": sym,
                "source": "openbb_api",
                "as_of": _now_iso(),
                "data": http_res,
            }

    if allow_sdk_import:
        sdk_res = _query_openbb_sdk(ds, sym, limit=lim)
        if sdk_res:
            return {
                "ok": True,
                "dataset": ds,
                "symbol": sym,
                "source": "openbb_sdk",
                "as_of": _now_iso(),
                "data": sdk_res,
            }

    fb_source, fb_data = _fallback_cockpit_dataset(ds, sym, limit=lim)
    return {
        "ok": True,
        "dataset": ds,
        "symbol": sym,
        "source": fb_source,
        "as_of": _now_iso(),
        "data": fb_data,
    }


def build_openbb_widgets_manifest() -> dict[str, dict[str, Any]]:
    """Return OpenBB Workspace compatible /widgets.json manifest for 5 read-only widgets."""
    return {
        "macro_signals": {
            "name": "多源精选情报",
            "description": "五维筛选、多源印证与五大板块均衡分布信号流",
            "category": "MacroCockpit",
            "type": "table",
            "endpoint": "/obb/signals",
            "gridData": {"w": 24, "h": 10},
            "params": [
                {"paramName": "category", "value": "all", "label": "板块", "type": "text"},
                {"paramName": "limit", "value": 30, "label": "条数", "type": "number"},
            ],
        },
        "portfolio_risk": {
            "name": "组合风控区间（脱敏）",
            "description": "净方向性敞口区间、有效杠杆区间与策略偏离百分点（不含绝对金额）",
            "category": "MacroCockpit",
            "type": "table",
            "endpoint": "/obb/portfolio_risk",
            "gridData": {"w": 16, "h": 8},
        },
        "stock_thesis": {
            "name": "个股多空速判",
            "description": "估值、财务健康、均线乖离纪律与多空逻辑要点",
            "category": "MacroCockpit",
            "type": "markdown",
            "endpoint": "/obb/stock_thesis",
            "gridData": {"w": 16, "h": 10},
            "params": [
                {"paramName": "symbol", "value": "NVDA", "label": "标的", "type": "text"},
            ],
        },
        "macro_board": {
            "name": "宏观与跨资产温度",
            "description": "美联储利率、美债曲线、净流动性、VIX 与加密温标",
            "category": "MacroCockpit",
            "type": "table",
            "endpoint": "/obb/macro_board",
            "gridData": {"w": 20, "h": 8},
        },
        "deep_debates": {
            "name": "多智能体深研归档",
            "description": "本地五角色多空对抗辩论与风控裁决记录",
            "category": "MacroCockpit",
            "type": "table",
            "endpoint": "/obb/deep_debates",
            "gridData": {"w": 20, "h": 8},
        },
    }


def build_openbb_apps_manifest() -> list[dict[str, Any]]:
    """Return OpenBB Workspace /apps.json dashboard layout."""
    return [
        {
            "name": "MacroCockpit 宏观与多智能体研究台",
            "img": "",
            "img_dark": "",
            "img_light": "",
            "description": "多源精选信号、脱敏组合风控、个股纪律速判与五角色深研归档",
            "allowCustomization": True,
            "tabs": {
                "overview": {
                    "id": "overview",
                    "name": "全景信号与风控",
                    "layout": [
                        {"i": "macro_board", "x": 0, "y": 0, "w": 20, "h": 8},
                        {"i": "portfolio_risk", "x": 20, "y": 0, "w": 20, "h": 8},
                        {"i": "macro_signals", "x": 0, "y": 8, "w": 40, "h": 10},
                    ],
                },
                "research": {
                    "id": "research",
                    "name": "个股速判与深研",
                    "layout": [
                        {"i": "stock_thesis", "x": 0, "y": 0, "w": 20, "h": 10},
                        {"i": "deep_debates", "x": 20, "y": 0, "w": 20, "h": 10},
                    ],
                },
            },
            "groups": [],
        }
    ]


def _band_pct_10(value: Any) -> str:
    if value is None or str(value).strip() == "":
        return "无法估算"
    try:
        val = float(value)
    except (TypeError, ValueError):
        return "无法估算"
    neg = val < 0
    mag = abs(val)
    if mag >= 100.0:
        band = ">=100%"
    else:
        lo = int(mag // 10) * 10
        band = f"{lo}%-{lo + 10}%"
    return f"-({band})" if (neg and mag > 0) else band


def _band_leverage(value: Any) -> str:
    if value is None or str(value).strip() == "":
        return "无法估算"
    try:
        val = abs(float(value))
    except (TypeError, ValueError):
        return "无法估算"
    if val < 0.5:
        return "0x-0.5x"
    if val < 1.0:
        return "0.5x-1x"
    if val < 2.0:
        return "1x-2x"
    if val < 3.0:
        return "2x-3x"
    return ">=3x"


def get_obb_widget_data(
    widget_name: str,
    params: dict[str, str] | None = None,
    *,
    base_dir: Path | None = None,
    store: Any = None,
) -> Any:
    """Return strictly sanitized widget data for /obb/* routes (never exposes private amounts)."""
    root = Path(base_dir) if base_dir else COCKPIT_ROOT
    data_dir = root / "data"
    state = _read_json(data_dir / "cockpit_state.json", {}) or {}
    q = params or {}

    if widget_name == "signals":
        cat = str(q.get("category") or "all").strip().lower()
        raw_limit = str(q.get("limit", "30")).strip()
        if not re.fullmatch(r"[+-]?[0-9]+", raw_limit):
            raise ValueError("limit must be an integer")
        try:
            lim = max(1, min(100, int(raw_limit)))
        except ValueError as exc:
            raise ValueError("limit must be an integer") from exc
        sigs = state.get("signals") or []
        if cat != "all":
            sigs = [s for s in sigs if str(s.get("category") or "").lower() == cat]
        return [
            {
                "time_bjt": s.get("time_bjt", ""),
                "category": s.get("category", ""),
                "source": s.get("source", ""),
                "direction": s.get("direction", ""),
                "importance": s.get("importance", ""),
                "score": s.get("attention_score", 0),
                "title": s.get("title", ""),
                "tickers": ", ".join(s.get("tickers") or []),
                "topics": ", ".join(s.get("topics") or []),
            }
            for s in sigs[:lim]
        ]

    if widget_name == "macro_board":
        return [
            {
                "group": m.get("group", ""),
                "label": m.get("label", ""),
                "value": m.get("value_display", ""),
                "change": m.get("change_display", ""),
                "as_of": m.get("as_of", ""),
                "source": m.get("source", ""),
            }
            for m in (state.get("macro_fred") or [])
        ]

    if widget_name == "portfolio_risk":
        rows: list[dict[str, Any]] = []
        ws: dict[str, Any] = {}
        if store is None:
            db_file = data_dir / "user_journal.db"
            if db_file.exists():
                try:
                    from backend.portfolio_store import PortfolioStore

                    store = PortfolioStore(db_file)
                except Exception:
                    store = None
        if store is not None:
            try:
                ws = store.build_workspace(state, currency="USD")
            except Exception:
                ws = {}

        summary = ws.get("summary") or {}
        rows.append(
            {
                "metric": "净方向性敞口区间",
                "band": _band_pct_10(summary.get("net_direction_pct")),
                "detail": "按 10% 脱敏区间统计",
            }
        )
        rows.append(
            {
                "metric": "有效杠杆区间",
                "band": _band_leverage(summary.get("effective_leverage")),
                "detail": "多空名义和 / 净值",
            }
        )
        rows.append(
            {
                "metric": "最近强平安全垫区间",
                "band": _band_pct_10(summary.get("min_liq_distance_pct")),
                "detail": "全仓缺强平价显示无法估算",
            }
        )
        strat_list = ws.get("strategy_comparison") or state.get("positions") or []
        for item in strat_list:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("id") or "")
            tgt = item.get("target_pct", item.get("recommended_pct", 0))
            act = item.get("actual_pct")
            rows.append(
                {
                    "metric": f"策略偏离 · {name}",
                    "band": _band_pct_10(act if act is not None else tgt),
                    "detail": "按 10% 区间脱敏（不展示精确占比与偏离）",
                }
            )
        return rows

    if widget_name == "stock_thesis":
        sym = str(q.get("symbol") or "NVDA").strip().upper()
        prof = (state.get("stock_profiles") or {}).get(sym)
        if prof is None:
            try:
                from backend.stock_research import fetch_single_stock_profile

                prof = fetch_single_stock_profile(sym)
            except Exception:
                prof = None
        if not isinstance(prof, dict):
            return f"### {sym}\n\n暂未检索到 {sym} 公开研究数据。"
        ov = prof.get("overview") or {}
        val = prof.get("valuation") or {}
        qt = prof.get("quick_thesis") or {}
        lines = [
            f"### {prof.get('name_cn', sym)} ({sym}) · ${prof.get('price', '—')} ({prof.get('change_pct', 0):+.2f}%)",
            "",
            f"- **所属板块**：{prof.get('sector_cn', '—')} · **健康分**：{(prof.get('health') or {}).get('score', '—')}/10",
            f"- **核心估值**：PE(TTM) {val.get('pe_ttm', '—')} · Forward PE {val.get('pe_forward', '—')} · 市值 {ov.get('market_cap_fmt', '—')}",
            f"- **均线乖离**：MA20 {prof.get('bias20_pct', '—')}% · MA50 {prof.get('bias50_pct', '—')}% · 52周分位 {prof.get('range_52w_pos', '—')}%",
        ]
        if qt:
            lines.append(f"- **综合速判**：{qt.get('stance', '—')}（{qt.get('score', '—')}/100）— {qt.get('action_hint', '')}")
            for bp in qt.get("bull_points") or []:
                lines.append(f"  - 利多：{bp}")
            for rp in qt.get("bear_points") or []:
                lines.append(f"  - 风险：{rp}")
        return "\n".join(lines)

    if widget_name == "deep_debates":
        debates = _read_json(data_dir / "desktop_debates.json", [])
        if not isinstance(debates, list):
            return []
        rows = []
        for d in debates[:30]:
            if not isinstance(d, dict):
                continue
            verdict = d.get("trader_verdict") or {}
            hint = str(verdict.get("position_cap_hint") or "")
            rows.append({
                "id": d.get("id", ""),
                "symbol": d.get("symbol", ""),
                "market": d.get("market", ""),
                "mode": d.get("mode", ""),
                "stance": verdict.get("stance", ""),
                "confidence": verdict.get("confidence", 0),
                "position_cap_hint": "" if has_private_debate_text(hint) else hint,
                "created_at": d.get("created_at", ""),
                "report_file": d.get("report_file", ""),
            })
        return rows

    raise KeyError(f"unknown widget: {widget_name!r}")
