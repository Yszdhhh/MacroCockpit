"""Stateless dual-cloud split collector: overseas runner pushes blocked sources; CN VPS merges & publishes."""
from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:
    requests = None  # type: ignore[assignment]

_BACKEND_DIR = Path(__file__).resolve().parent
if str(_BACKEND_DIR.parent) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR.parent))

from backend.article_store import parse_markdown_article, read_article_markdown, sort_and_balance_articles
from backend.guardrails import (
    has_private_article_content,
    safe_article_filename,
    scrub_forbidden as _scrub_forbidden,
)
from backend.runtime import (
    BJT,
    COCKPIT_ROOT,
    atomic_write_json,
    file_lock,
    now_bjt_iso as _now_bjt_iso,
    read_json_safe as _read_json,
    resolve_data_dir,
)
from backend.calendar_events import build_calendar_events, filter_calendar_events
from backend.correlation_engine import (
    save_series_to_fred_cache,
    compute_cross_asset_correlations,
    fetch_btc_binance_daily,
)
from backend.fred_and_market import (
    FRED_SERIES_MAP,
    fetch_gold_tail,
    load_fred_api_key,
    build_macro_fred,
    fetch_fred_observations,
    fetch_us_equities,
)
from backend.kazike_engine import cluster_and_dedup, prefilter_financial, process_financial_signals, score_five_axes
from backend.event_feed import update_event_feed
from backend.market_quotes import fetch_crypto_quotes, fetch_fx_snapshot
from backend.mobile_push import get_push_channels_status, load_push_env
from backend.public_snapshot import (
    load_published_generation,
    publish_generation,
)
from backend.rss_sources import (
    CURATED_ARTICLE_FEEDS,
    CURATED_RSS_FEEDS,
    fetch_aihot_hot_articles,
    fetch_article_candidates_from_feed,
    fetch_cls_depth_candidates,
    fetch_one_feed,
    fetch_wscn_hot_articles,
    select_balanced_articles,
    write_curated_article_files,
    build_opml_export,
    collect_cn_finance_wires,
    collect_equation_news_with_meta,
)
from backend.sec_edgar import collect_recent_sec_signals, fetch_sec_filings_map
from backend.signal_aggregator import (
    KOL_ROOT,
    build_positions_and_risk_gate,
    build_strategy_targets,
    collect_aihot_signals,
    collect_equity_signals,
    ensure_signal_topics,
    sanitize_state_value,
)

from backend.guru_sources import collect_guru_articles, collect_guru_signals

from backend.kol_intel_engine import (
    build_kol_actionable_signals,
    build_kol_digest,
    build_kol_scorecards,
    select_quality_kol_articles,
)

from backend.market_synthesis import build_market_synthesis

from backend.forum_sentiment import (
    build_sentiment_divergence, collect_public_discussion_trends, trim_forum_sentiment,
)

from backend.source_matrix import build_asset_source_matrix

from backend.signal_advisor import update_signal_advisor
from backend.stock_research import (
    CACHE_TTL_SECONDS as STOCK_CACHE_TTL_SECONDS,
    CORE_RESEARCH_STOCKS,
    assemble_profile,
    create_yahoo_session,
    fetch_yahoo_one,
    merge_us_fallbacks,
    build_non_us_preset_profiles,
    build_quick_thesis,
    fetch_gtimg_batch,
)

_fetch_one_feed = fetch_one_feed
_fetch_article_candidates_from_feed = fetch_article_candidates_from_feed
_fetch_cls_depth_candidates = fetch_cls_depth_candidates
_fetch_wscn_hot_articles = fetch_wscn_hot_articles
_fetch_aihot_hot_articles = fetch_aihot_hot_articles
_collect_aihot_signals = collect_aihot_signals

# Verified live against tencent-vps: these 8 signal feeds + 1 article feed fail/timeout from CN datacenter.
INTL_ONLY_RSS_IDS: frozenset[str] = frozenset(
    {
        "deepmind_blog",
        "anthropic_news",
        "anthropic_research",
        "hf_blog",
        "the_batch",
        "ft_markets",
        "coindesk",
        "investing_macro",
    }
)
INTL_ONLY_ARTICLE_IDS: frozenset[str] = frozenset({"import_ai"})

_BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_MAX_INTL_STALE_SECONDS = 7200


def _strip_private_items(items: list[Any]) -> list[dict[str, Any]]:
    clean: list[dict[str, Any]] = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        src = str(it.get("source") or "").strip().lower()
        url = str(it.get("url") or "").strip().lower()
        iid = str(it.get("id") or "").strip().lower()
        if iid.startswith("rss-aidaily-") or src in ("a.i. 日报", "a.i.日报", "aidaily") or "aidaily.fyi" in url:
            continue
        blob = json.dumps(it, ensure_ascii=False)
        if has_private_article_content({"filename": str(it.get("filename") or it.get("title") or ""), "content": blob}):
            continue
        clean.append(it)
    return clean


def _fetch_intl_fred_series() -> dict[str, list[list[Any]]]:
    api_key = load_fred_api_key()
    out: dict[str, list[list[Any]]] = {}

    def _pull_one(key: str, sid: str) -> tuple[str, list[tuple[str, float]]]:
        lim = 48 if key == "CPI" else 120
        return key, fetch_fred_observations(sid, api_key=api_key, limit=lim, timeout=12)

    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = [pool.submit(_pull_one, k, sid) for k, sid in FRED_SERIES_MAP.items()]
        gold_fut = pool.submit(fetch_gold_tail)
        btc_fut = pool.submit(fetch_btc_binance_daily, 120)
        for fut in as_completed(futs):
            try:
                k, pts = fut.result()
                if pts:
                    out[k] = [[d, round(float(v), 6)] for d, v in pts[-400:]]
            except Exception:
                pass
        try:
            g_pts = gold_fut.result()
            if g_pts:
                out["GOLD"] = [[d, round(float(v), 6)] for d, v in g_pts[-400:]]
        except Exception:
            pass
        try:
            b_pts = btc_fut.result()
            if b_pts:
                out["BTC"] = [[d, round(float(v), 2)] for d, v in b_pts[-400:]]
        except Exception:
            pass
    return out


def _fetch_intl_yahoo_profiles(cache_path: Path, *, refresh_yahoo: bool) -> dict[str, dict[str, Any]]:
    cached = _read_json(cache_path, {}) or {}
    if not isinstance(cached, dict):
        cached = {}
    symbols = [row[0] for row in CORE_RESEARCH_STOCKS]
    now = time.time()
    need = [
        sym
        for sym in symbols
        if refresh_yahoo
        and (
            sym not in cached
            or not isinstance(cached.get(sym), dict)
            or (now - float((cached[sym] or {}).get("yahoo_fetched_at") or 0.0)) > STOCK_CACHE_TTL_SECONDS
            or not ((cached[sym] or {}).get("chart") or {}).get("closes")
        )
    ]
    if need:
        sess, crumb = create_yahoo_session(timeout=8)
        if sess is not None:
            with ThreadPoolExecutor(max_workers=4) as pool:
                futs = {pool.submit(fetch_yahoo_one, sym, sess, crumb, 10): sym for sym in need}
                for fut in as_completed(futs):
                    sym = futs[fut]
                    try:
                        res = fut.result()
                        if res:
                            cached[sym] = res
                    except Exception:
                        pass
            try:
                atomic_write_json(cache_path, cached)
            except OSError:
                pass
    return {sym: cached[sym] for sym in symbols if isinstance(cached.get(sym), dict) and cached[sym]}


def collect_intl_bundle(
    *,
    base_dir: Path | str = COCKPIT_ROOT,
    producer_id: str = "intl-collector",
    refresh_yahoo: bool = True,
    timeout: int = 10,
) -> dict[str, Any]:
    """Collect sources blocked or rate-limited in mainland China without touching private ledgers."""
    root = Path(base_dir)
    data_dir = resolve_data_dir(root)
    intl_rss_specs = [sp for sp in CURATED_RSS_FEEDS if sp["id"] in INTL_ONLY_RSS_IDS]
    intl_art_specs = [sp for sp in CURATED_ARTICLE_FEEDS if sp["id"] in INTL_ONLY_ARTICLE_IDS]

    raw_signals: list[dict[str, Any]] = []
    raw_articles: list[dict[str, Any]] = []
    fred_series: dict[str, list[list[Any]]] = {}
    crypto_quotes: dict[str, Any] = {}
    yahoo_profiles: dict[str, dict[str, Any]] = {}

    with ThreadPoolExecutor(max_workers=8) as pool:
        fred_fut = pool.submit(_fetch_intl_fred_series)
        crypto_fut = pool.submit(fetch_crypto_quotes, None, 8, force_refresh=True)
        yahoo_fut = pool.submit(
            _fetch_intl_yahoo_profiles,
            data_dir / "stock_profiles_cache.json",
            refresh_yahoo=refresh_yahoo,
        )
        rss_futs = [pool.submit(_fetch_one_feed, sp, timeout) for sp in intl_rss_specs]
        art_futs = [pool.submit(_fetch_article_candidates_from_feed, sp, timeout) for sp in intl_art_specs]

        try:
            fred_series = fred_fut.result()
        except Exception:
            fred_series = {}
        try:
            cq = crypto_fut.result()
            if isinstance(cq, dict) and ((cq.get("spot") or {}) or (cq.get("swap") or {})):
                crypto_quotes = cq
        except Exception:
            crypto_quotes = {}
        try:
            yahoo_profiles = yahoo_fut.result()
        except Exception:
            yahoo_profiles = {}
        for fut in rss_futs:
            try:
                raw_signals.extend(fut.result())
            except Exception:
                pass
        for fut in art_futs:
            try:
                raw_articles.extend(fut.result())
            except Exception:
                pass

    raw_signals = _strip_private_items(raw_signals)
    raw_articles = _strip_private_items(raw_articles)
    return {
        "schema_version": 1,
        "producer_id": str(producer_id or "intl-collector"),
        "fetched_at": _now_bjt_iso(),
        "fetched_epoch": time.time(),
        "fred_series": fred_series,
        "crypto_quotes": crypto_quotes,
        "yahoo_profiles": yahoo_profiles,
        "raw_signals": raw_signals,
        "raw_articles": raw_articles,
        "sources_status": {
            "fred_series_count": len(fred_series),
            "crypto_spot_count": len(crypto_quotes.get("spot") or {}),
            "crypto_swap_count": len(crypto_quotes.get("swap") or {}),
            "yahoo_profiles_count": len(yahoo_profiles),
            "intl_signals_count": len(raw_signals),
            "intl_articles_count": len(raw_articles),
        },
    }


def post_intl_bundle(
    payload: dict[str, Any],
    *,
    target_url: str | None = None,
    sync_token: str | None = None,
    timeout: tuple[int, int] = (15, 60),
) -> tuple[bool, dict[str, Any]]:
    """Compress and push the overseas collector bundle to POST /api/v2/ingest-intl."""
    if requests is None:
        return False, {"reason": "requests_not_installed"}
    token = (sync_token if sync_token is not None else (os.environ.get("MACROCOCKPIT_SYNC_TOKEN") or "")).strip()
    if not token:
        return False, {"reason": "missing_sync_token"}
    url = (
        target_url
        or os.environ.get("MACROCOCKPIT_INGEST_URL")
        or f"{(os.environ.get('MACROCOCKPIT_PUBLIC_URL') or 'https://news.shuashuabao.xyz').strip().rstrip('/')}/api/v2/ingest-intl"
    ).strip()

    body_bytes = gzip.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
        "Content-Encoding": "gzip",
        "User-Agent": _BROWSER_UA,
    }
    try:
        resp = requests.post(url, data=body_bytes, headers=headers, timeout=timeout)
        if not (200 <= resp.status_code < 300):
            return False, {"status_code": resp.status_code, "endpoint": url, "reason": f"HTTP_{resp.status_code}"}
        try:
            body_json = resp.json()
        except ValueError:
            body_json = {}
        return True, {"status_code": resp.status_code, "endpoint": url, "compressed_bytes": len(body_bytes), **body_json}
    except requests.RequestException as exc:
        return False, {"endpoint": url, "reason": type(exc).__name__}


def ingest_intl_bundle(base_dir: Path | str, payload: dict[str, Any]) -> dict[str, Any]:
    """Validate and merge overseas FRED/Crypto/Yahoo/RSS fragment into CN consumer/aggregator caches."""
    if not isinstance(payload, dict):
        raise ValueError("intl_payload_must_be_object")
    root = Path(base_dir)
    data_dir = resolve_data_dir(root)
    data_dir.mkdir(parents=True, exist_ok=True)

    raw_signals = _strip_private_items(list(payload.get("raw_signals") or []))
    raw_articles = _strip_private_items(list(payload.get("raw_articles") or []))
    fred_series = payload.get("fred_series") if isinstance(payload.get("fred_series"), dict) else {}
    crypto_quotes = payload.get("crypto_quotes") if isinstance(payload.get("crypto_quotes"), dict) else {}
    yahoo_profiles = payload.get("yahoo_profiles") if isinstance(payload.get("yahoo_profiles"), dict) else {}

    now_epoch = time.time()
    fetched_epoch = payload.get("fetched_epoch")
    try:
        fe = float(fetched_epoch) if fetched_epoch is not None else now_epoch
    except (TypeError, ValueError):
        fe = now_epoch

    relay_doc = {
        "schema_version": 1,
        "producer_id": str(payload.get("producer_id") or "intl-collector")[:64],
        "fetched_at": str(payload.get("fetched_at") or _now_bjt_iso())[:40],
        "fetched_epoch": fe,
        "received_at": _now_bjt_iso(),
        "received_epoch": now_epoch,
        "raw_signals": raw_signals,
        "raw_articles": raw_articles,
    }
    relay_path = data_dir / "intl_relay_bundle.json"
    with file_lock(relay_path):
        atomic_write_json(relay_path, relay_doc)

    # Merge FRED series incrementally into data/fred_cache.json
    fred_updates: dict[str, list[tuple[str, float]]] = {}
    for k, pts in fred_series.items():
        if isinstance(k, str) and isinstance(pts, list):
            parsed = []
            for row in pts:
                if isinstance(row, (list, tuple)) and len(row) >= 2:
                    try:
                        parsed.append((str(row[0])[:10], float(row[1])))
                    except (TypeError, ValueError):
                        pass
            if parsed:
                fred_updates[k] = parsed
    if fred_updates:
        fred_cache_path = data_dir / "fred_cache.json"
        with file_lock(fred_cache_path):
            save_series_to_fred_cache(fred_updates, cache_path=fred_cache_path)

    # Merge OKX crypto spot/swap quotes into data/crypto_quotes_cache.json
    if (crypto_quotes.get("spot") or {}) or (crypto_quotes.get("swap") or {}):
        cq_cache_path = data_dir / "crypto_quotes_cache.json"
        with file_lock(cq_cache_path):
            atomic_write_json(cq_cache_path, {"fetched_epoch": fe, "payload": crypto_quotes})

    # Merge Yahoo stock fundamentals/charts into data/stock_profiles_cache.json
    if yahoo_profiles:
        sp_cache_path = data_dir / "stock_profiles_cache.json"
        with file_lock(sp_cache_path):
            existing = _read_json(sp_cache_path, {}) or {}
            if not isinstance(existing, dict):
                existing = {}
            for sym, prof in yahoo_profiles.items():
                if isinstance(sym, str) and isinstance(prof, dict) and (prof.get("price") or prof.get("chart")):
                    existing[sym.upper()] = prof
            atomic_write_json(sp_cache_path, existing)

    return {
        "received_at": relay_doc["received_at"],
        "producer_id": relay_doc["producer_id"],
        "fred_series_merged": len(fred_updates),
        "yahoo_profiles_merged": len(yahoo_profiles),
        "crypto_updated": bool((crypto_quotes.get("spot") or {}) or (crypto_quotes.get("swap") or {})),
        "intl_signals_count": len(raw_signals),
        "intl_articles_count": len(raw_articles),
    }


def _load_cn_stock_profiles(
    data_dir: Path,
    *,
    filings_map: dict[str, list[dict[str, str]]],
    risk_gate: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    symbols = [row[0] for row in CORE_RESEARCH_STOCKS]
    gtimg_map = fetch_gtimg_batch(symbols)
    sp_cache = _read_json(data_dir / "stock_profiles_cache.json", {}) or {}
    if not isinstance(sp_cache, dict):
        sp_cache = {}
    profiles: dict[str, dict[str, Any]] = {}
    for sym in symbols:
        y_item = sp_cache.get(sym) or {}
        g_item = gtimg_map.get(sym) or {}
        if not (y_item.get("chart") or {}).get("closes"):
            y_item = merge_us_fallbacks(sym, y_item, g_item, timeout=4)
        prof = assemble_profile(
            sym,
            y_item,
            g_item,
            sec_filings=filings_map.get(sym, []),
            risk_gate=risk_gate,
            market="us",
            currency="USD",
            currency_symbol="$",
        )
        if prof is not None:
            profiles[sym] = prof
    for sym, prof in build_non_us_preset_profiles(risk_gate=risk_gate, timeout=5).items():
        profiles[sym] = prof
    return profiles


def _has_usable_quote_price(entry: Any) -> int:
    if not isinstance(entry, dict):
        return 0
    for key in ("mark_price", "price"):
        raw = entry.get(key)
        if raw in (None, ""):
            continue
        try:
            from decimal import Decimal, InvalidOperation

            dec = Decimal(str(raw).strip())
            if dec.is_finite() and dec > 0:
                return 1
        except (InvalidOperation, ValueError):
            continue
    return 0


def _quote_freshness_key(entry: Any) -> tuple[int, int, float]:
    if not isinstance(entry, dict):
        return (0, 0, 0.0)
    has_price = _has_usable_quote_price(entry)
    is_official = 0 if (entry.get("kind") == "spot_proxy" or entry.get("is_proxy") is True) else 1
    epoch = 0.0
    for ts_key in ("market_at", "received_at"):
        raw = str(entry.get(ts_key) or "").strip()
        if raw:
            try:
                dt = datetime.fromisoformat(raw)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=BJT)
                epoch = max(epoch, dt.timestamp())
            except ValueError:
                pass
    return (has_price, is_official, epoch)


def _merge_fresher_quotes(cur_map: dict[str, Any], prior_map: dict[str, Any]) -> dict[str, Any]:
    merged = dict(cur_map)
    for sym_k, p_val in prior_map.items():
        if sym_k not in merged:
            merged[sym_k] = p_val
            continue
        cur_val = merged.get(sym_k)
        if _quote_freshness_key(p_val) > _quote_freshness_key(cur_val):
            best = dict(p_val) if isinstance(p_val, dict) else p_val
            # If cur_val was a newer funding-only update (no price), fold funding_rate onto the usable priced record
            if isinstance(best, dict) and isinstance(cur_val, dict) and _has_usable_quote_price(cur_val) == 0:
                if cur_val.get("funding_rate") not in (None, ""):
                    best["funding_rate"] = cur_val["funding_rate"]
            merged[sym_k] = best
    return merged


def _source_matrix_richness(matrix: Any) -> tuple[int, int, int]:
    if not isinstance(matrix, dict):
        return (0, 0, 0)
    cats = matrix.get("categories") or []
    if not isinstance(cats, list):
        return (0, 0, 0)
    bridges = sum(len(c.get("supply_chain_bridges") or []) for c in cats if isinstance(c, dict))
    kols = sum(len(c.get("top_kols") or []) for c in cats if isinstance(c, dict))
    return (bridges, kols, len(cats))


def _count_signal_categories(signals: list[dict[str, Any]]) -> dict[str, int]:
    cat_counts: dict[str, int] = {}
    for sig in signals:
        c = str(sig.get("category") or "macro")
        cat_counts[c] = cat_counts.get(c, 0) + 1
    return cat_counts


def run_cn_merge_and_publish(
    base_dir: Path | str = COCKPIT_ROOT,
    *,
    max_articles: int = 40,
) -> dict[str, Any]:
    """Run domestic CN direct collection, merge intl relay bundle, dedup globally, and atomically publish generation."""
    root = Path(base_dir)
    data_dir = resolve_data_dir(root)
    articles_dir = root / "articles"
    data_dir.mkdir(parents=True, exist_ok=True)
    articles_dir.mkdir(parents=True, exist_ok=True)

    lock_path = data_dir / "cloud_merge.lock"
    with file_lock(lock_path):
        discussion_trends: dict[str, Any] = {}
        if collect_public_discussion_trends is not None:
            try:
                discussion_trends = collect_public_discussion_trends(data_dir)
            except (OSError, ValueError):
                pass

        cn_rss_specs = [sp for sp in CURATED_RSS_FEEDS if sp["id"] not in INTL_ONLY_RSS_IDS]
        rsshub_base = (os.environ.get("RSSHUB_BASE_URL") or "").strip().rstrip("/")
        if rsshub_base.startswith(("http://", "https://")):
            cn_rss_specs.append(
                {
                    "id": "rsshub_36kr",
                    "source": "36氪快讯",
                    "url": f"{rsshub_base}/36kr/newsflash",
                    "html_url": "https://36kr.com/newsflashes",
                    "category": "ai",
                    "tier": "T2",
                    "why": "国内科技与产业快讯补充，映射国产 AI 应用与硬科技情绪。",
                }
            )
        cn_art_specs = [sp for sp in CURATED_ARTICLE_FEEDS if sp["id"] not in INTL_ONLY_ARTICLE_IDS]

        cn_rss_signals: list[dict[str, Any]] = []
        cn_wires: list[dict[str, Any]] = []
        eqn_signals: list[dict[str, Any]] = []
        cn_art_cands: list[dict[str, Any]] = []
        cls_depth_cands: list[dict[str, Any]] = []
        wscn_hot_cands: list[dict[str, Any]] = []
        aihot_art_cands: list[dict[str, Any]] = []
        aihot_signals: list[dict[str, Any]] = []
        guru_articles: list[dict[str, Any]] = []
        filings_map: dict[str, list[dict[str, str]]] = {}
        us_equities: dict[str, Any] = {}
        fx_snapshot: dict[str, Any] = {"status": "unavailable", "rate": None, "basis": None, "label": "人民币估值不可用"}

        with ThreadPoolExecutor(max_workers=10) as pool:
            rss_futs = [pool.submit(_fetch_one_feed, sp, 7) for sp in cn_rss_specs]
            wires_fut = pool.submit(collect_cn_finance_wires, 7)
            eqn_fut = pool.submit(collect_equation_news_with_meta, cache_path=data_dir / "eqn_rest_cache.json")
            art_futs = [pool.submit(_fetch_article_candidates_from_feed, sp, 8) for sp in cn_art_specs]
            cls_fut = pool.submit(_fetch_cls_depth_candidates, 8)
            wscn_art_fut = pool.submit(_fetch_wscn_hot_articles, 8)
            aihot_art_fut = pool.submit(_fetch_aihot_hot_articles, 10)
            aihot_fut = pool.submit(_collect_aihot_signals)
            guru_fut = (
                pool.submit(
                    collect_guru_articles,
                    articles_dir=articles_dir,
                    cache_path=data_dir / "guru_articles_cache.json",
                )
                if collect_guru_articles is not None
                else None
            )
            sec_fut = pool.submit(fetch_sec_filings_map, None, force_refresh=False, timeout=6, cache_path=data_dir / "sec_filings_cache.json")
            eq_fut = pool.submit(fetch_us_equities)
            fx_fut = pool.submit(fetch_fx_snapshot)

            for fut in rss_futs:
                try:
                    cn_rss_signals.extend(fut.result())
                except Exception:
                    pass
            try:
                cn_wires = wires_fut.result()
            except Exception:
                cn_wires = []
            try:
                eqn_signals, _eqn_meta = eqn_fut.result()
            except Exception:
                eqn_signals = []
            for fut in art_futs:
                try:
                    cn_art_cands.extend(fut.result())
                except Exception:
                    pass
            try:
                cls_depth_cands = cls_fut.result()
            except Exception:
                cls_depth_cands = []
            try:
                wscn_hot_cands = wscn_art_fut.result()
            except Exception:
                wscn_hot_cands = []
            try:
                aihot_art_cands = aihot_art_fut.result()
            except Exception:
                aihot_art_cands = []
            try:
                aihot_signals = aihot_fut.result()
            except Exception:
                aihot_signals = []
            if guru_fut is not None:
                try:
                    guru_articles = guru_fut.result()
                except Exception:
                    guru_articles = []
            try:
                filings_map = sec_fut.result()
            except Exception:
                filings_map = {}
            try:
                us_equities = eq_fut.result()
            except Exception:
                us_equities = {"summary": "美股盘面暂不可用", "indices": [], "mag7_and_ai": [], "sectors": []}
            try:
                fx_res = fx_fut.result()
                if isinstance(fx_res, dict) and fx_res:
                    fx_snapshot = fx_res
            except Exception:
                pass

        sec_signals = collect_recent_sec_signals(filings_map)
        eq_signals = collect_equity_signals(us_equities)
        guru_signals: list[dict[str, Any]] = []
        if collect_guru_signals is not None:
            try:
                guru_signals = collect_guru_signals(
                    articles=guru_articles or None,
                    cache_path=data_dir / "guru_articles_cache.json",
                )
            except Exception:
                guru_signals = []

        # Load overseas relay fragment & prior state metadata
        relay = _read_json(data_dir / "intl_relay_bundle.json", {}) or {}
        if not isinstance(relay, dict):
            relay = {}
        relay_epoch = float(relay.get("fetched_epoch") or 0.0) if relay else 0.0
        intl_age_s = (time.time() - relay_epoch) if relay_epoch > 0 else 1e12
        intl_stale = (not relay) or (intl_age_s > _MAX_INTL_STALE_SECONDS)
        intl_signals = _strip_private_items(list(relay.get("raw_signals") or []))
        intl_articles = _strip_private_items(list(relay.get("raw_articles") or []))

        # Preserve prior kol_digest, kol_scorecards, forum_sentiment, source_matrix, crypto_quotes & curated articles
        prior_kol: list[dict[str, Any]] = []
        prior_scorecards: list[dict[str, Any]] = []
        prior_forum_sentiment: dict[str, Any] = {}
        prior_source_matrix: dict[str, Any] = {}
        prior_crypto_quotes: dict[str, Any] = {}
        prior_article_meta: dict[str, dict[str, Any]] = {}
        prior_gen = 0
        try:
            loaded_prior = load_published_generation(root)
            prior_gen = int(loaded_prior.get("generation") or 0)
            p_state = loaded_prior.get("state") or {}
            prior_kol = list(p_state.get("kol_digest") or [])
            prior_scorecards = list(p_state.get("kol_scorecards") or [])
            if isinstance(p_state.get("forum_sentiment"), dict):
                prior_forum_sentiment = dict(p_state["forum_sentiment"])
            if isinstance(p_state.get("asset_source_matrix"), dict):
                prior_source_matrix = dict(p_state["asset_source_matrix"])
            if isinstance(p_state.get("crypto_quotes"), dict):
                prior_crypto_quotes = dict(p_state["crypto_quotes"])
            for pa in p_state.get("articles") or []:
                if isinstance(pa, dict) and pa.get("filename"):
                    prior_article_meta[str(pa["filename"])] = dict(pa)
        except Exception:
            prior_state = _read_json(data_dir / "cockpit_state.json", {}) or {}
            if isinstance(prior_state, dict):
                prior_kol = list(prior_state.get("kol_digest") or [])
                prior_scorecards = list(prior_state.get("kol_scorecards") or [])
                if isinstance(prior_state.get("forum_sentiment"), dict):
                    prior_forum_sentiment = dict(prior_state["forum_sentiment"])
                if isinstance(prior_state.get("asset_source_matrix"), dict):
                    prior_source_matrix = dict(prior_state["asset_source_matrix"])
                if isinstance(prior_state.get("crypto_quotes"), dict):
                    prior_crypto_quotes = dict(prior_state["crypto_quotes"])
                for pa in prior_state.get("articles") or []:
                    if isinstance(pa, dict) and pa.get("filename"):
                        prior_article_meta[str(pa["filename"])] = dict(pa)

        # KOL intelligence engine (with graceful degradation when local KOL_ROOT is absent on VPS)
        kol_scorecards: list[dict[str, Any]] = []
        kol_digest: list[dict[str, Any]] = []
        kol_actionable_signals: list[dict[str, Any]] = []
        quality_kol_articles: list[dict[str, Any]] = []
        sc_cache_path = data_dir / "kol_scorecard_cache.json"
        if build_kol_scorecards is not None and build_kol_digest is not None and build_kol_actionable_signals is not None:
            try:
                if KOL_ROOT.exists():
                    kol_scorecards = build_kol_scorecards(
                        kol_dir=KOL_ROOT,
                        stock_profiles_path=data_dir / "stock_profiles_cache.json",
                        fred_cache_path=data_dir / "fred_cache.json",
                        cache_path=sc_cache_path,
                        force_refresh=True,
                    )
                    kol_digest = build_kol_digest(
                        kol_dir=KOL_ROOT,
                        scorecards=kol_scorecards,
                        cache_path=sc_cache_path,
                        limit=36,
                    )
                    if select_quality_kol_articles is not None and (KOL_ROOT / "articles").exists():
                        quality_kol_articles = select_quality_kol_articles(
                            KOL_ROOT / "articles",
                            kol_dir=KOL_ROOT,
                            scorecards=kol_scorecards,
                            limit=40,
                        )
                else:
                    kol_scorecards = build_kol_scorecards(
                        kol_dir=KOL_ROOT,
                        cache_path=sc_cache_path,
                        force_refresh=False,
                    )
            except Exception:
                kol_scorecards = []
                kol_digest = []
                quality_kol_articles = []

        if not kol_scorecards and prior_scorecards:
            kol_scorecards = prior_scorecards
        if not kol_digest:
            kol_digest = prior_kol[:36]
        if build_kol_actionable_signals is not None and kol_digest:
            try:
                kol_actionable_signals = build_kol_actionable_signals(
                    kol_digest_items=kol_digest,
                    scorecards=kol_scorecards,
                    min_reliability_score=68.0,
                    limit=16,
                )
            except Exception:
                kol_actionable_signals = []

        # Read macro_fred (from merged fred_cache.json) + pure-numpy correlations
        macro_fred = build_macro_fred({}, refresh_fred=False)
        correlations = compute_cross_asset_correlations(fred_cache_path=data_dir / "fred_cache.json")
        risk_gate, positions = build_positions_and_risk_gate({}, {}, macro_fred=macro_fred, us_equities=us_equities)
        strategy_targets = build_strategy_targets(positions, risk_gate, {}, {})

        # Unified Kazike pipeline: deduplicate & score CN + INTL + KOL + Guru signals together
        raw_signals = _strip_private_items(
            [
                ensure_signal_topics(s)
                for s in (
                    cn_rss_signals
                    + cn_wires
                    + eqn_signals
                    + intl_signals
                    + sec_signals
                    + aihot_signals
                    + eq_signals
                    + kol_actionable_signals
                    + guru_signals
                )
            ]
        )
        all_signals, kazike_stats = process_financial_signals(
            raw_signals,
            receipts_path=data_dir / "scoring_receipts.json",
        )
        signal_candidates = kazike_stats.pop("candidates", [])
        pre_cluster_items = kazike_stats.pop("pre_cluster_items", [])
        event_cards: list[dict[str, Any]] | None = None
        event_stats: dict[str, Any] = {}
        try:
            event_cards, event_stats = update_event_feed(pre_cluster_items, data_dir=data_dir)
            if event_stats.get("ledger_persist_skipped"):
                event_cards = None
        except Exception:
            event_cards, event_stats = None, {}

        # Deduplicate & persist curated articles (feed_*.md) while retaining guru_*.md & ta_*.md
        scored_arts: list[dict[str, Any]] = []
        for cand in (cn_art_cands + cls_depth_cands + wscn_hot_cands + aihot_art_cands + intl_articles):
            verdict, _reason = prefilter_financial(cand)
            if verdict == "BLOCK":
                continue
            sc = score_five_axes(cand, persist_receipt=False)
            c_copy = dict(cand)
            c_copy["attention_score"] = int(sc["score"])
            c_copy["score"] = int(sc["score"])
            scored_arts.append(c_copy)

        if scored_arts:
            deduped_arts = cluster_and_dedup(scored_arts)
            balanced_arts = select_balanced_articles(deduped_arts, limit=16, max_per_publisher=4)
            top_arts = [
                {
                    "filename": "",
                    "title": _scrub_forbidden(str(it.get("title") or "")),
                    "summary": _scrub_forbidden(str(it.get("summary") or "")),
                    "publisher": str(it.get("publisher") or it.get("source") or "AI与金融研报"),
                    "url": str(it.get("url") or ""),
                    "score": int(it.get("attention_score") or 0),
                    "time_bjt": str(it.get("time_bjt") or ""),
                    "body_md": _scrub_forbidden(str(it.get("body_md") or ""), preserve_newlines=True),
                }
                for it in balanced_arts
            ]
            write_curated_article_files(top_arts, articles_dir=articles_dir)

        collected_articles: list[dict[str, Any]] = []
        content_by_name: dict[str, str] = {}
        seen_names: set[str] = set()
        manifest: dict[str, Any] | None = None
        for attempt in range(5):
            if attempt > 0:
                try:
                    loaded_prior = load_published_generation(root)
                    prior_gen = int(loaded_prior.get("generation") or 0)
                    p_state = loaded_prior.get("state") or {}
                    prior_kol = list(p_state.get("kol_digest") or [])
                    prior_scorecards = list(p_state.get("kol_scorecards") or [])
                    if isinstance(p_state.get("forum_sentiment"), dict):
                        prior_forum_sentiment = dict(p_state["forum_sentiment"])
                    if isinstance(p_state.get("asset_source_matrix"), dict):
                        prior_source_matrix = dict(p_state["asset_source_matrix"])
                    if isinstance(p_state.get("crypto_quotes"), dict):
                        prior_crypto_quotes = dict(p_state["crypto_quotes"])
                    prior_article_meta = {
                        str(pa["filename"]): dict(pa)
                        for pa in (p_state.get("articles") or [])
                        if isinstance(pa, dict) and pa.get("filename")
                    }
                    if not kol_scorecards and prior_scorecards:
                        kol_scorecards = prior_scorecards
                    if not kol_digest:
                        kol_digest = prior_kol[:36]
                except Exception:
                    pass

            collected_articles = []
            content_by_name = {}
            seen_names = set()
            prior_gen_articles_dir = (data_dir / "generations" / f"gen_{prior_gen}" / "articles") if prior_gen > 0 else None
            art_scan_dirs = [articles_dir]
            if prior_gen_articles_dir is not None and prior_gen_articles_dir.is_dir():
                art_scan_dirs.append(prior_gen_articles_dir)
            for scan_dir in art_scan_dirs:
                for p in sorted(scan_dir.glob("*.md"), key=lambda fp: fp.stat().st_mtime, reverse=True):
                    if p.name in seen_names or "异常告警" in p.name or not safe_article_filename(p.name):
                        continue
                    meta_prev = prior_article_meta.get(p.name) or {}
                    src_label = str(meta_prev.get("source") or "同步深度长文")
                    art = parse_markdown_article(p, src_label)
                    content = read_article_markdown(p, src_label, max_bytes=256 * 1024)
                    if art and content:
                        if meta_prev.get("title"):
                            art["title"] = str(meta_prev["title"])
                        if meta_prev.get("excerpt"):
                            art["excerpt"] = str(meta_prev["excerpt"])
                        if has_private_article_content(art) or has_private_article_content({"filename": p.name, "body_md": content}):
                            continue
                        seen_names.add(p.name)
                        collected_articles.append(art)
                        content_by_name[p.name] = content
            for kart in quality_kol_articles:
                if not isinstance(kart, dict):
                    continue
                fname = safe_article_filename(kart.get("filename"))
                body = str(kart.get("body_md") or "")
                if not fname or fname in seen_names or "异常告警" in fname or not body:
                    continue
                if has_private_article_content(kart):
                    continue
                seen_names.add(fname)
                collected_articles.append(kart)
                content_by_name[fname] = body

            collected_articles = sort_and_balance_articles(collected_articles)[:max_articles]
            articles_payload = [
                {"filename": a["filename"], "content": content_by_name[a["filename"]]}
                for a in collected_articles
                if a.get("filename") in content_by_name
            ]

            stock_profiles = _load_cn_stock_profiles(data_dir, filings_map=filings_map, risk_gate=risk_gate)
            combined_for_thesis = signal_candidates or all_signals
            for sym, prof in stock_profiles.items():
                prof["quick_thesis"] = build_quick_thesis(
                    prof,
                    risk_gate=risk_gate,
                    signals=combined_for_thesis,
                    kol_digest=kol_digest,
                )
                sym_pat = re.compile(rf"^ta_{re.escape(str(sym).upper())}_.*\.md$", re.IGNORECASE)
                ta_matches = [a for a in collected_articles if sym_pat.match(str(a.get("filename") or ""))]
                if ta_matches:
                    ta_matches.sort(key=lambda a: (str(a.get("mtime") or ""), str(a.get("filename") or "")), reverse=True)
                    prof["deep_report_file"] = str(ta_matches[0]["filename"])

            calendar_events = filter_calendar_events(build_calendar_events(stock_profiles), days_ahead=120)

            crypto_quotes = {}
            cq_cached = _read_json(data_dir / "crypto_quotes_cache.json", {}) or {}
            if isinstance(cq_cached, dict) and isinstance(cq_cached.get("payload"), dict):
                crypto_quotes = dict(cq_cached["payload"])
                crypto_quotes["cached"] = True
                crypto_quotes["stale"] = intl_stale
            if prior_crypto_quotes:
                if not crypto_quotes:
                    crypto_quotes = dict(prior_crypto_quotes)
                    crypto_quotes["cached"] = True
                else:
                    for subk in ("quotes", "spot", "swap", "spot_proxies"):
                        p_map = prior_crypto_quotes.get(subk)
                        if isinstance(p_map, dict) and p_map:
                            cur_map = dict(crypto_quotes.get(subk) or {})
                            crypto_quotes[subk] = _merge_fresher_quotes(cur_map, p_map)

            opml_file = root / "dist" / "macro_cockpit_feeds.opml"
            if not opml_file.is_file():
                try:
                    build_opml_export(output_path=opml_file)
                except Exception:
                    pass

            market_analysis = {}
            if build_market_synthesis is not None:
                try:
                    market_analysis = build_market_synthesis(
                        macro_fred=macro_fred,
                        us_equities=us_equities,
                        correlations=correlations,
                        signals=all_signals,
                        kol_digest=kol_digest,
                        kol_scorecards=kol_scorecards,
                        articles=collected_articles,
                        risk_gate=risk_gate,
                        stock_profiles=stock_profiles,
                    )
                except Exception:
                    market_analysis = {}

            forum_sentiment = {}
            forum_pulse = _read_json(data_dir / "forum_pulse_cache.json", {}) or {}
            has_local_forum = (
                isinstance(forum_pulse, dict)
                and isinstance(forum_pulse.get("channels"), dict)
                and any(bool(v) for v in forum_pulse["channels"].values())
            )
            if has_local_forum and build_sentiment_divergence is not None:
                try:
                    forum_sentiment = build_sentiment_divergence(
                        forum_pulse,
                        kol_digest=kol_digest,
                        kol_scorecards=kol_scorecards,
                        signals=all_signals,
                    )
                except Exception:
                    forum_sentiment = {}
            if (
                not isinstance(forum_sentiment, dict)
                or (
                    not (forum_sentiment.get("channels_status") or [])
                    and not (forum_sentiment.get("hot_discussions") or [])
                )
            ) and prior_forum_sentiment:
                forum_sentiment = dict(prior_forum_sentiment)
            # Recompute against today; never carry cached warning flags forward.
            forum_sentiment.pop("discussion_trends", None)
            if discussion_trends:
                forum_sentiment["discussion_trends"] = discussion_trends
                if trim_forum_sentiment is not None:
                    forum_sentiment = trim_forum_sentiment(forum_sentiment)

            asset_source_matrix = {}
            if build_asset_source_matrix is not None:
                try:
                    asset_source_matrix = build_asset_source_matrix(
                        kol_scorecards=kol_scorecards,
                        scorecard_cache_path=sc_cache_path,
                    )
                except Exception:
                    asset_source_matrix = {}
            if _source_matrix_richness(prior_source_matrix) > _source_matrix_richness(asset_source_matrix):
                asset_source_matrix = dict(prior_source_matrix)

            # Optimistic generation CAS check before publish so concurrent Windows updates are never rolled back
            try:
                latest_pub_gen = int((load_published_generation(root) or {}).get("generation") or 0)
            except Exception:
                latest_pub_gen = 0
            if latest_pub_gen > prior_gen:
                if attempt < 4:
                    continue
                raise RuntimeError("cn_merge_publish_cas_exhausted")

            cat_counts = _count_signal_categories(all_signals)
            next_gen = prior_gen + 1
            kol_verified_authors_count = sum(
                1 for c in kol_scorecards if isinstance(c, dict) and int(c.get("verified_calls") or 0) >= 5
            )
            guru_articles_count = sum(
                1 for a in collected_articles if isinstance(a, dict) and a.get("source") == "大佬分享"
            )
            periodic_reports_count = sum(
                1 for a in collected_articles if isinstance(a, dict) and a.get("source") == "周月度深研"
            )
            forum_items_count = sum(
                int(c.get("count") or 0)
                for c in (forum_sentiment.get("channels_status") or [])
                if isinstance(c, dict)
            ) or len(forum_sentiment.get("hot_discussions") or [])
            source_matrix_count = len((asset_source_matrix or {}).get("categories") or [])
            cross_links_count = len((market_analysis or {}).get("cross_source_links") or [])

            signal_plans = None
            signal_plan_stats = None
            if update_signal_advisor is not None:
                try:
                    signal_plans, signal_plan_stats = update_signal_advisor(
                        {
                            "us_equities": us_equities,
                            "macro_fred": macro_fred,
                            "crypto_quotes": crypto_quotes,
                            "risk_gate": risk_gate,
                            "forum_sentiment": forum_sentiment,
                        },
                        data_dir=data_dir,
                    )
                    if isinstance(signal_plan_stats, dict) and signal_plan_stats.get("ledger_persist_skipped"):
                        signal_plans, signal_plan_stats = None, None
                except Exception:
                    signal_plans, signal_plan_stats = None, None

            coverage = {
                "signals_count": len(all_signals),
                "signal_candidates_count": len(signal_candidates),
                **({"event_cards_count": len(event_cards)} if event_cards is not None else {}),
                **({"signal_plans_count": len(signal_plans)} if signal_plans is not None else {}),
                "signal_sources_count": int(kazike_stats.get("source_count") or 0),
                "source_count": int(kazike_stats.get("source_count") or 0),
                "category_breakdown": cat_counts,
                "stock_profiles_count": len(stock_profiles),
                "sec_filings_count": sum(len(p.get("sec_filings") or []) for p in stock_profiles.values()),
                "articles_count": len(collected_articles),
                "calendar_events_count": len(calendar_events),
                "intl_relay_stale": bool(intl_stale),
                "intl_fetched_at": str(relay.get("fetched_at") or ""),
                "intl_signals_count": len(intl_signals),
                "eqn_signals_count": len(eqn_signals),
                "kol_digest_count": len(kol_digest),
                "kol_verified_authors_count": kol_verified_authors_count,
                "guru_articles_count": guru_articles_count,
                "cross_links_count": cross_links_count,
                "forum_items_count": forum_items_count,
                "source_matrix_count": source_matrix_count,
                "periodic_reports_count": periodic_reports_count,
            }

            state = sanitize_state_value(
                {
                    "schema_version": 2,
                    "generation": next_gen,
                    "generated_at_bjt": datetime.now(BJT).strftime("%Y-%m-%d %H:%M:%S+08:00"),
                    "risk_gate": risk_gate,
                    "positions": positions,
                    "strategy_targets": strategy_targets,
                    "macro_fred": macro_fred,
                    "us_equities": us_equities,
                    "stock_profiles": stock_profiles,
                    "signals": all_signals,
                    "signal_candidates": signal_candidates,
                    **({"event_cards": event_cards, "event_stats": event_stats} if event_cards is not None else {}),
                    **({"signal_plans": signal_plans, **({"signal_plan_stats": signal_plan_stats} if signal_plan_stats else {})} if signal_plans is not None else {}),
                    "kazike_stats": kazike_stats,
                    "correlations": correlations,
                    "kol_digest": kol_digest,
                    "kol_scorecards": kol_scorecards,
                    "market_analysis": market_analysis,
                    "forum_sentiment": forum_sentiment,
                    "asset_source_matrix": asset_source_matrix,
                    "articles": collected_articles,
                    "push_channels": get_push_channels_status(),
                    "calendar_events": calendar_events,
                    "fx_snapshot": fx_snapshot,
                    "crypto_quotes": crypto_quotes,
                    "coverage": coverage,
                }
            )

            try:
                manifest = publish_generation(
                    root,
                    state,
                    articles_payload,
                    producer_id="cloud-split-aggregator",
                    generation=next_gen,
                )
                break
            except ValueError as exc:
                if str(exc) in ("stale_generation", "generation_conflict"):
                    if attempt < 4:
                        continue
                    raise RuntimeError("cn_merge_publish_cas_exhausted") from exc
                raise
        if manifest is None:
            raise RuntimeError("cn_merge_publish_cas_exhausted")
        return {
            "ok": True,
            "generation": manifest.get("generation"),
            "published_at": manifest.get("published_at"),
            "signals_count": len(all_signals),
            "eqn_signals_count": len(eqn_signals),
            "articles_count": len(articles_payload),
            "intl_relay_stale": intl_stale,
        }


def run_eqn_fast_sync(
    base_dir: Path | str = COCKPIT_ROOT,
    *,
    channels: tuple[str, ...] = ("main", "tradfi"),
    limit: int = 20,
    force_publish: bool = False,
) -> dict[str, Any]:
    """Fast incremental EquationNews REST+ETag sync: skip publish on 304, merge & publish on 200 changes."""
    root = Path(base_dir)
    data_dir = resolve_data_dir(root)
    data_dir.mkdir(parents=True, exist_ok=True)

    eqn_signals, meta = collect_equation_news_with_meta(
        channels=channels,
        limit=limit,
        cache_path=data_dir / "eqn_rest_cache.json",
    )
    if not meta.get("changed") and not force_publish:
        return {
            "ok": True,
            "changed": False,
            "published": False,
            "eqn_signals_count": len(eqn_signals),
            "channels": meta.get("channels") or {},
        }

    lock_path = data_dir / "cloud_merge.lock"
    with file_lock(lock_path):
        manifest: dict[str, Any] | None = None
        all_signals: list[dict[str, Any]] = []
        articles_payload: list[dict[str, str]] = []
        for attempt in range(5):
            try:
                loaded = load_published_generation(root)
            except FileNotFoundError:
                cn_res = run_cn_merge_and_publish(root)
                return {
                    **cn_res,
                    "changed": True,
                    "published": True,
                    "fallback_full_merge": True,
                    "eqn_signals_count": len(eqn_signals),
                    "new_count": int(meta.get("new_count") or len(eqn_signals)),
                }

            cur_gen = int(loaded.get("generation") or 0)
            cur_manifest = loaded.get("manifest")
            cur_state = dict(loaded.get("state") or {})

            articles_payload = []
            seen_fnames: set[str] = set()
            gen_art_dir = data_dir / "generations" / f"gen_{cur_gen}" / "articles"
            legacy_art_dir = root / "articles"
            art_entries = (
                (cur_manifest.get("articles") if isinstance(cur_manifest, dict) else None)
                or cur_state.get("articles")
                or []
            )
            for entry in art_entries:
                if not isinstance(entry, dict):
                    continue
                fname = safe_article_filename(entry.get("filename"))
                if not fname or fname in seen_fnames:
                    continue
                seen_fnames.add(fname)
                art_file = gen_art_dir / fname
                if not art_file.is_file():
                    art_file = legacy_art_dir / fname
                if art_file.is_file():
                    try:
                        articles_payload.append({"filename": fname, "content": art_file.read_text(encoding="utf-8")})
                    except OSError:
                        pass

            existing_pool = list(cur_state.get("signal_candidates") or cur_state.get("signals") or [])
            fresh_ids = {str(s["id"]) for s in eqn_signals if isinstance(s, dict) and s.get("id")}
            kept_existing = [
                s for s in existing_pool if not (isinstance(s, dict) and str(s.get("id") or "") in fresh_ids)
            ]
            merged_raw = _strip_private_items(
                [ensure_signal_topics(s) for s in (eqn_signals + kept_existing)]
            )
            all_signals, kazike_stats = process_financial_signals(
                merged_raw,
                receipts_path=data_dir / "scoring_receipts.json",
            )
            signal_candidates = kazike_stats.pop("candidates", [])
            pre_cluster_items = kazike_stats.pop("pre_cluster_items", [])
            event_cards: list[dict[str, Any]] | None = None
            event_stats: dict[str, Any] = {}
            try:
                event_cards, event_stats = update_event_feed(pre_cluster_items, data_dir=data_dir)
                if event_stats.get("ledger_persist_skipped"):
                    event_cards = None
            except Exception:
                event_cards, event_stats = None, {}

            cat_counts = _count_signal_categories(all_signals)
            next_gen = cur_gen + 1

            if build_market_synthesis is not None and (cur_state.get("macro_fred") or cur_state.get("us_equities")):
                try:
                    cur_state["market_analysis"] = build_market_synthesis(
                        macro_fred=cur_state.get("macro_fred"),
                        us_equities=cur_state.get("us_equities"),
                        correlations=cur_state.get("correlations"),
                        signals=all_signals,
                        kol_digest=cur_state.get("kol_digest"),
                        kol_scorecards=cur_state.get("kol_scorecards"),
                        articles=cur_state.get("articles"),
                        risk_gate=cur_state.get("risk_gate"),
                        stock_profiles=cur_state.get("stock_profiles"),
                    )
                except Exception:
                    pass

            coverage = dict(cur_state.get("coverage") or {})
            coverage.update(
                {
                    "signals_count": len(all_signals),
                    "signal_candidates_count": len(signal_candidates),
                    **({"event_cards_count": len(event_cards)} if event_cards is not None else {}),
                    "signal_sources_count": int(kazike_stats.get("source_count") or 0),
                    "source_count": int(kazike_stats.get("source_count") or 0),
                    "category_breakdown": cat_counts,
                    "eqn_signals_count": len(eqn_signals),
                    "cross_links_count": len(((cur_state.get("market_analysis") or {}) if isinstance(cur_state.get("market_analysis"), dict) else {}).get("cross_source_links") or []),
                }
            )

            cur_state["generation"] = next_gen
            cur_state["generated_at_bjt"] = datetime.now(BJT).strftime("%Y-%m-%d %H:%M:%S+08:00")
            cur_state["signals"] = all_signals
            cur_state["signal_candidates"] = signal_candidates
            if event_cards is not None:
                cur_state["event_cards"] = event_cards
                cur_state["event_stats"] = event_stats
            else:
                cur_state.pop("event_cards", None)
                cur_state.pop("event_stats", None)
            cur_state["kazike_stats"] = kazike_stats
            cur_state["coverage"] = coverage

            try:
                manifest = publish_generation(
                    root,
                    cur_state,
                    articles_payload,
                    producer_id="eqn-fast-sync",
                    generation=next_gen,
                )
                break
            except ValueError as exc:
                if str(exc) in ("stale_generation", "generation_conflict"):
                    if attempt < 4:
                        continue
                    raise RuntimeError("eqn_fast_sync_cas_exhausted") from exc
                raise
        if manifest is None:
            raise RuntimeError("eqn_fast_sync_cas_exhausted")
        return {
            "ok": True,
            "changed": True,
            "published": True,
            "generation": manifest.get("generation"),
            "published_at": manifest.get("published_at"),
            "eqn_signals_count": len(eqn_signals),
            "new_count": int(meta.get("new_count") or 0),
            "signals_count": len(all_signals),
            "articles_preserved": len(articles_payload),
        }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="MacroCockpit split-cloud collector (overseas push & CN merge)")
    parser.add_argument("--role", choices=("intl-push", "cn-merge-publish", "eqn-fast-sync"), required=True)
    parser.add_argument("--base-dir", type=Path, default=COCKPIT_ROOT)
    parser.add_argument("--target-url", default="")
    parser.add_argument("--producer-id", default="github-actions-intl")
    parser.add_argument("--skip-yahoo", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force-publish", action="store_true")
    parser.add_argument("--max-articles", type=int, default=40)
    args = parser.parse_args()

    load_push_env(include_sync_token=True)
    if args.role == "intl-push":
        bundle = collect_intl_bundle(
            base_dir=args.base_dir,
            producer_id=args.producer_id,
            refresh_yahoo=not args.skip_yahoo,
        )
        if args.dry_run:
            print(json.dumps({"action": "intl_dry_run", **bundle["sources_status"]}, ensure_ascii=False))
            return
        ok, res = post_intl_bundle(bundle, target_url=args.target_url or None)
        print(json.dumps({"ok": ok, **bundle["sources_status"], **res}, ensure_ascii=False))
        if not ok:
            raise SystemExit(1)
        return

    if args.role == "eqn-fast-sync":
        res = run_eqn_fast_sync(args.base_dir, force_publish=args.force_publish)
        print(json.dumps(res, ensure_ascii=False))
        return

    res = run_cn_merge_and_publish(args.base_dir, max_articles=args.max_articles)
    print(json.dumps(res, ensure_ascii=False))


if __name__ == "__main__":
    main()
