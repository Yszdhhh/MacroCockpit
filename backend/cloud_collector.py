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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:
    requests = None  # type: ignore[assignment]

_BACKEND_DIR = Path(__file__).resolve().parent
if str(_BACKEND_DIR.parent) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR.parent))

from backend.calendar_events import build_calendar_events, filter_calendar_events
from backend.correlation_engine import (
    _save_series_to_fred_cache,
    compute_cross_asset_correlations,
    fetch_btc_binance_daily,
)
from backend.fred_and_market import (
    COCKPIT_ROOT,
    FRED_SERIES_MAP,
    _fetch_gold_tail,
    _load_fred_api_key,
    atomic_write_json,
    build_macro_fred,
    fetch_fred_observations,
    fetch_us_equities,
)
from backend.kazike_engine import cluster_and_dedup, prefilter_financial, process_financial_signals, score_five_axes
from backend.market_quotes import fetch_crypto_quotes, fetch_fx_snapshot
from backend.mobile_push import get_push_channels_status, load_push_env
from backend.public_snapshot import (
    file_lock,
    has_private_article_content,
    load_published_generation,
    publish_generation,
    safe_article_filename,
)
from backend.rss_sources import (
    CURATED_ARTICLE_FEEDS,
    CURATED_RSS_FEEDS,
    _fetch_article_candidates_from_feed,
    _fetch_cls_depth_candidates,
    _fetch_one_feed,
    _write_curated_article_files,
    build_opml_export,
    collect_cn_finance_wires,
)
from backend.sec_edgar import collect_recent_sec_signals, fetch_sec_filings_map
from backend.signal_aggregator import (
    _build_positions_and_risk_gate,
    _build_strategy_targets,
    _collect_aihot_signals,
    _collect_equity_signals,
    _dedup_articles,
    _ensure_signal_topics,
    _read_json,
    parse_markdown_article,
    read_article_markdown,
)
from backend.stock_research import (
    CACHE_TTL_SECONDS as STOCK_CACHE_TTL_SECONDS,
    CORE_RESEARCH_STOCKS,
    _assemble_profile,
    _create_yahoo_session,
    _fetch_yahoo_one,
    build_quick_thesis,
    fetch_gtimg_batch,
)

BJT = timezone(timedelta(hours=8))

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


def _now_bjt_iso() -> str:
    return datetime.now(BJT).isoformat(timespec="seconds")


def _strip_private_items(items: list[Any]) -> list[dict[str, Any]]:
    clean: list[dict[str, Any]] = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        blob = json.dumps(it, ensure_ascii=False)
        if has_private_article_content({"filename": str(it.get("filename") or it.get("title") or ""), "content": blob}):
            continue
        clean.append(it)
    return clean


def _fetch_intl_fred_series() -> dict[str, list[list[Any]]]:
    api_key = _load_fred_api_key()
    out: dict[str, list[list[Any]]] = {}

    def _pull_one(key: str, sid: str) -> tuple[str, list[tuple[str, float]]]:
        lim = 48 if key == "CPI" else 120
        return key, fetch_fred_observations(sid, api_key=api_key, limit=lim, timeout=12)

    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = [pool.submit(_pull_one, k, sid) for k, sid in FRED_SERIES_MAP.items()]
        gold_fut = pool.submit(_fetch_gold_tail)
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
        sess, crumb = _create_yahoo_session(timeout=8)
        if sess is not None:
            with ThreadPoolExecutor(max_workers=4) as pool:
                futs = {pool.submit(_fetch_yahoo_one, sym, sess, crumb, 10): sym for sym in need}
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
    data_dir = root / "data"
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
    data_dir = root / "data"
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
            _save_series_to_fred_cache(fred_updates, cache_path=fred_cache_path)

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
        prof = _assemble_profile(
            sym,
            sp_cache.get(sym) or {},
            gtimg_map.get(sym) or {},
            sec_filings=filings_map.get(sym, []),
            risk_gate=risk_gate,
        )
        if prof is not None:
            profiles[sym] = prof
    return profiles


def run_cn_merge_and_publish(
    base_dir: Path | str = COCKPIT_ROOT,
    *,
    max_articles: int = 40,
) -> dict[str, Any]:
    """Run domestic CN direct collection, merge intl relay bundle, dedup globally, and atomically publish generation."""
    root = Path(base_dir)
    data_dir = root / "data"
    articles_dir = root / "articles"
    data_dir.mkdir(parents=True, exist_ok=True)
    articles_dir.mkdir(parents=True, exist_ok=True)

    lock_path = data_dir / "cloud_merge.lock"
    with file_lock(lock_path):
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
        cn_art_cands: list[dict[str, Any]] = []
        cls_depth_cands: list[dict[str, Any]] = []
        aihot_signals: list[dict[str, Any]] = []
        filings_map: dict[str, list[dict[str, str]]] = {}
        us_equities: dict[str, Any] = {}
        fx_snapshot: dict[str, Any] = {"status": "unavailable", "rate": None, "basis": None, "label": "人民币估值不可用"}

        with ThreadPoolExecutor(max_workers=10) as pool:
            rss_futs = [pool.submit(_fetch_one_feed, sp, 7) for sp in cn_rss_specs]
            wires_fut = pool.submit(collect_cn_finance_wires, 7)
            art_futs = [pool.submit(_fetch_article_candidates_from_feed, sp, 8) for sp in cn_art_specs]
            cls_fut = pool.submit(_fetch_cls_depth_candidates, 8)
            aihot_fut = pool.submit(_collect_aihot_signals)
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
                aihot_signals = aihot_fut.result()
            except Exception:
                aihot_signals = []
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
        eq_signals = _collect_equity_signals(us_equities)

        # Load overseas relay fragment & prior state metadata
        relay = _read_json(data_dir / "intl_relay_bundle.json", {}) or {}
        if not isinstance(relay, dict):
            relay = {}
        relay_epoch = float(relay.get("fetched_epoch") or 0.0) if relay else 0.0
        intl_age_s = (time.time() - relay_epoch) if relay_epoch > 0 else 1e12
        intl_stale = (not relay) or (intl_age_s > _MAX_INTL_STALE_SECONDS)
        intl_signals = _strip_private_items(list(relay.get("raw_signals") or []))
        intl_articles = _strip_private_items(list(relay.get("raw_articles") or []))

        # Preserve prior kol_digest if published state already had it
        prior_kol: list[dict[str, Any]] = []
        try:
            loaded_prior = load_published_generation(root)
            prior_kol = list((loaded_prior.get("state") or {}).get("kol_digest") or [])
        except Exception:
            prior_state = _read_json(data_dir / "cockpit_state.json", {}) or {}
            if isinstance(prior_state, dict):
                prior_kol = list(prior_state.get("kol_digest") or [])

        # Read macro_fred (from merged fred_cache.json) + pure-numpy correlations
        macro_fred = build_macro_fred({}, refresh_fred=False)
        correlations = compute_cross_asset_correlations(fred_cache_path=data_dir / "fred_cache.json")
        risk_gate, positions = _build_positions_and_risk_gate({}, {}, macro_fred=macro_fred, us_equities=us_equities)
        strategy_targets = _build_strategy_targets(positions, risk_gate, {}, {})

        # Unified Kazike pipeline: deduplicate & score CN + INTL signals together
        raw_signals = [
            _ensure_signal_topics(s)
            for s in (cn_rss_signals + cn_wires + intl_signals + sec_signals + aihot_signals + eq_signals)
        ]
        all_signals, kazike_stats = process_financial_signals(
            raw_signals,
            receipts_path=data_dir / "scoring_receipts.json",
        )
        signal_candidates = kazike_stats.pop("candidates", [])

        # Deduplicate & persist curated articles (feed_*.md) while retaining ta_*.md
        scored_arts: list[dict[str, Any]] = []
        for cand in (cn_art_cands + cls_depth_cands + intl_articles):
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
            deduped_arts.sort(
                key=lambda x: (int(x.get("attention_score") or 0), str(x.get("time_bjt") or "")),
                reverse=True,
            )
            top_arts = [
                {
                    "filename": "",
                    "title": str(it.get("title") or ""),
                    "summary": str(it.get("summary") or ""),
                    "publisher": str(it.get("publisher") or it.get("source") or "AI与金融研报"),
                    "url": str(it.get("url") or ""),
                    "score": int(it.get("attention_score") or 0),
                    "time_bjt": str(it.get("time_bjt") or ""),
                    "body_md": str(it.get("body_md") or ""),
                }
                for it in deduped_arts[:12]
            ]
            _write_curated_article_files(top_arts, articles_dir=articles_dir)

        collected_articles: list[dict[str, Any]] = []
        articles_payload: list[dict[str, str]] = []
        for p in sorted(articles_dir.glob("*.md"), key=lambda fp: fp.stat().st_mtime, reverse=True):
            if "异常告警" in p.name or not safe_article_filename(p.name):
                continue
            art = parse_markdown_article(p, "同步深度长文")
            content = read_article_markdown(p, "同步深度长文", max_bytes=256 * 1024)
            if art and content:
                collected_articles.append(art)
                articles_payload.append({"filename": p.name, "content": content})

        collected_articles = _dedup_articles(collected_articles)
        kept_names = {a["filename"] for a in collected_articles}
        articles_payload = [a for a in articles_payload if a["filename"] in kept_names][:max_articles]

        stock_profiles = _load_cn_stock_profiles(data_dir, filings_map=filings_map, risk_gate=risk_gate)
        combined_for_thesis = signal_candidates or all_signals
        for sym, prof in stock_profiles.items():
            prof["quick_thesis"] = build_quick_thesis(
                prof,
                risk_gate=risk_gate,
                signals=combined_for_thesis,
                kol_digest=prior_kol,
            )
            sym_pat = re.compile(rf"^ta_{re.escape(str(sym).upper())}_.*\.md$", re.IGNORECASE)
            ta_matches = [a for a in collected_articles if sym_pat.match(str(a.get("filename") or ""))]
            if ta_matches:
                ta_matches.sort(key=lambda a: (str(a.get("mtime") or ""), str(a.get("filename") or "")), reverse=True)
                prof["deep_report_file"] = str(ta_matches[0]["filename"])

        calendar_events = filter_calendar_events(build_calendar_events(stock_profiles), days_ahead=120)

        crypto_quotes: dict[str, Any] = {}
        cq_cached = _read_json(data_dir / "crypto_quotes_cache.json", {}) or {}
        if isinstance(cq_cached, dict) and isinstance(cq_cached.get("payload"), dict):
            crypto_quotes = dict(cq_cached["payload"])
            crypto_quotes["cached"] = True
            crypto_quotes["stale"] = intl_stale

        opml_file = root / "dist" / "macro_cockpit_feeds.opml"
        if not opml_file.is_file():
            try:
                build_opml_export(output_path=opml_file)
            except Exception:
                pass

        cat_counts: dict[str, int] = {}
        for sig in all_signals:
            c = str(sig.get("category") or "macro")
            cat_counts[c] = cat_counts.get(c, 0) + 1

        # Advance monotonic generation atomically
        counter_path = data_dir / "producer_generation.json"
        with file_lock(counter_path):
            prev_gen = 0
            if counter_path.is_file():
                try:
                    prev_gen = int((_read_json(counter_path, {}) or {}).get("generation") or 0)
                except (TypeError, ValueError):
                    prev_gen = 0
            cur_pub_gen = 0
            try:
                cur_pub_gen = int((load_published_generation(root) or {}).get("generation") or 0)
            except Exception:
                cur_pub_gen = 0
            next_gen = max(prev_gen, cur_pub_gen) + 1
            atomic_write_json(counter_path, {"generation": next_gen})

        coverage = {
            "signals_count": len(all_signals),
            "signal_candidates_count": len(signal_candidates),
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
        }

        state: dict[str, Any] = {
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
            "kazike_stats": kazike_stats,
            "correlations": correlations,
            "kol_digest": prior_kol,
            "articles": collected_articles,
            "push_channels": get_push_channels_status(),
            "calendar_events": calendar_events,
            "fx_snapshot": fx_snapshot,
            "crypto_quotes": crypto_quotes,
            "coverage": coverage,
        }

        manifest = publish_generation(
            root,
            state,
            articles_payload,
            producer_id="cloud-split-aggregator",
            generation=next_gen,
        )
        return {
            "ok": True,
            "generation": manifest.get("generation"),
            "published_at": manifest.get("published_at"),
            "signals_count": len(all_signals),
            "articles_count": len(articles_payload),
            "intl_relay_stale": intl_stale,
        }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="MacroCockpit split-cloud collector (overseas push & CN merge)")
    parser.add_argument("--role", choices=("intl-push", "cn-merge-publish"), required=True)
    parser.add_argument("--base-dir", type=Path, default=COCKPIT_ROOT)
    parser.add_argument("--target-url", default="")
    parser.add_argument("--producer-id", default="github-actions-intl")
    parser.add_argument("--skip-yahoo", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
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

    res = run_cn_merge_and_publish(args.base_dir, max_articles=args.max_articles)
    print(json.dumps(res, ensure_ascii=False))


if __name__ == "__main__":
    main()
