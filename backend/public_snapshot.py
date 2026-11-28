"""Public state projection, atomic generation publishing, and manifest-backed reads."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.guardrails import (
    PRIVATE_TOP_LEVEL_KEYS as _PRIVATE_TOP_LEVEL_KEYS,
    _iter_strings,
    has_private_article_content,
    has_private_article_text,
    has_private_debate_text as has_private_debate_text,
    private_article_filenames,
    safe_article_filename,
)
from backend.runtime import (
    BJT,
    atomic_write_text as _atomic_write_text,
    file_lock,
    now_bjt_iso as _now_bjt_iso,
    resolve_data_dir,
)
from backend.discussion_temperature import SNAPSHOT_TAGS, load_valid_trends_from_cache, project_discussion_trends

MAX_ARTICLE_BYTES = 512 * 1024
KEEP_GENERATIONS = 3
DEFAULT_SAMPLE_CAPITAL_USD = 100000

PUBLIC_STATE_KEYS: frozenset[str] = frozenset(
    {
        "schema_version",
        "generation",
        "generated_at_bjt",
        "risk_gate",
        "positions",
        "strategy_targets",
        "macro_fred",
        "us_equities",
        "stock_profiles",
        "signals",
        "signal_candidates",
        "event_cards",
        "event_stats",
        "signal_plans",
        "signal_plan_stats",
        "kazike_stats",
        "correlations",
        "kol_digest",
        "kol_scorecards",
        "market_analysis",
        "forum_sentiment",
        "asset_source_matrix",
        "articles",
        "push_channels",
        "calendar_events",
        "fx_snapshot",
        "crypto_quotes",
        "coverage",
    }
)

_PUBLISH_LOCK = threading.RLock()


def _bear_basket_legs(rec_pct: float, sample_capital: int) -> list[dict[str, Any]]:
    active = rec_pct > 0
    long_pct = round(rec_pct if active else 35.0, 2)
    short_pct = -30.0 if active else -30.0
    long_eff = long_pct if active else 0.0
    short_eff = short_pct if active else 0.0
    return [
        {
            "instrument_id": "BTC",
            "symbol": "BTC",
            "side": "long",
            "target_pct": long_eff,
            "configured_pct": long_pct,
            "sample_notional_usd": round(sample_capital * (long_eff / 100.0), 2),
            "label": f"+{long_pct:g}% BTC",
        },
        {
            "instrument_id": "ALT_BASKET",
            "symbol": "ALT_BASKET",
            "side": "short",
            "target_pct": short_eff,
            "configured_pct": short_pct,
            "sample_notional_usd": round(sample_capital * (abs(short_eff) / 100.0), 2),
            "label": f"{short_pct:g}% ALT_BASKET",
        },
    ]


def _default_legs_for_position(pos: dict[str, Any], sample_capital: int) -> list[dict[str, Any]]:
    pid = str(pos.get("id") or "")
    rec_pct = float(pos.get("recommended_pct") or 0.0)
    if pid == "bear_basket":
        return _bear_basket_legs(rec_pct, sample_capital)
    inst_map = {"btc_trend": "BTC", "gold_hedge": "XAU", "crash_bounce": "CRYPTO_BOUNCE"}
    inst = inst_map.get(pid, pid.upper() or "ASSET")
    return [
        {
            "instrument_id": inst,
            "symbol": inst,
            "side": "long",
            "target_pct": round(rec_pct, 2),
            "sample_notional_usd": round(sample_capital * (rec_pct / 100.0), 2),
            "label": f"+{rec_pct:g}% {inst}" if rec_pct > 0 else f"0% {inst}",
        }
    ]


def _build_structured_strategy_targets(raw_state: dict[str, Any]) -> dict[str, Any]:
    """Ensure strategy_targets is structured with bear_basket dual legs and sample_capital_usd."""
    existing = raw_state.get("strategy_targets")
    sample_capital = DEFAULT_SAMPLE_CAPITAL_USD
    risk = raw_state.get("risk_gate") or {}
    long_cap = float(risk.get("long_risk_cap") if risk.get("long_risk_cap") is not None else 1.0)
    positions = [p for p in (raw_state.get("positions") or []) if isinstance(p, dict)]

    targets_list: list[dict[str, Any]] = []
    if isinstance(existing, dict) and isinstance(existing.get("targets"), list) and existing["targets"]:
        sample_capital = int(existing.get("sample_capital_usd") or DEFAULT_SAMPLE_CAPITAL_USD)
        for item in existing["targets"]:
            if not isinstance(item, dict):
                continue
            entry = dict(item)
            if str(entry.get("id") or "") == "bear_basket" and not entry.get("legs"):
                rec = float(entry.get("target_pct") or entry.get("recommended_pct") or 0.0)
                entry["legs"] = _bear_basket_legs(rec, sample_capital)
                entry.setdefault("legs_summary", "+35% BTC / -30% ALT_BASKET")
            targets_list.append(entry)
    elif isinstance(existing, list) and existing:
        for item in existing:
            if not isinstance(item, dict):
                continue
            entry = dict(item)
            if str(entry.get("id") or "") == "bear_basket" and not entry.get("legs"):
                rec = float(entry.get("target_pct") or entry.get("recommended_pct") or 0.0)
                entry["legs"] = _bear_basket_legs(rec, sample_capital)
                entry.setdefault("legs_summary", "+35% BTC / -30% ALT_BASKET")
            targets_list.append(entry)
    else:
        for pos in positions:
            pid = str(pos.get("id") or "")
            rec_pct = float(pos.get("recommended_pct") or 0.0)
            legs = _default_legs_for_position(pos, sample_capital)
            entry: dict[str, Any] = {
                "id": pid,
                "name": str(pos.get("name") or pid),
                "state": str(pos.get("state") or ""),
                "baseline_pct": float(pos.get("baseline_pct") or 0.0),
                "target_pct": rec_pct,
                "sample_notional_usd": round(float(pos.get("est_notional_usd") or sample_capital * rec_pct / 100.0), 2),
                "legs": legs,
                "note": str(pos.get("note") or ""),
            }
            if pid == "bear_basket":
                long_leg = legs[0]["target_pct"]
                short_leg = legs[1]["target_pct"]
                entry["net_target_pct"] = round(long_leg + short_leg, 2)
                entry["gross_target_pct"] = round(abs(long_leg) + abs(short_leg), 2)
                entry["legs_summary"] = "+35% BTC / -30% ALT_BASKET"
            else:
                entry["net_target_pct"] = round(rec_pct, 2)
                entry["gross_target_pct"] = round(abs(rec_pct), 2)
            targets_list.append(entry)

    by_id = {str(t.get("id")): t for t in targets_list if t.get("id")}
    return {
        "sample_capital_usd": sample_capital,
        "long_risk_cap": long_cap,
        "targets": targets_list,
        "by_id": by_id,
    }


def _build_default_coverage(projected: dict[str, Any]) -> dict[str, Any]:
    raw_cov = projected.get("coverage")
    if isinstance(raw_cov, dict) and raw_cov:
        return dict(raw_cov)
    signals = projected.get("signals") or []
    sources = {str(s.get("source") or "") for s in signals if isinstance(s, dict) and s.get("source")}
    return {
        "signals_count": len(signals),
        "signal_sources_count": len(sources),
        "articles_count": len(projected.get("articles") or []),
        "stock_profiles_count": len(projected.get("stock_profiles") or []),
        "calendar_events_count": len(projected.get("calendar_events") or []),
    }


def _json_clone(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


_MARKET_PULSE_FLOAT_KEYS: frozenset[str] = frozenset(
    {
        "a_turnover_yi",
        "sh_pct",
        "cyb_pct",
        "kc50_pct",
        "hk_turnover_yi_hkd",
        "hsi_pct",
        "hstech_pct",
        "rzrq_balance_yi",
        "rzrq_buy_yi",
        "rzrq_net_buy_yi",
        "rzrq_float_pct",
        "southbound_net_yi_hkd",
        "southbound_deal_yi_hkd",
        "southbound_5d_net_yi_hkd",
        "mass_penetration_pct",
    }
)
_MARKET_PULSE_INT_KEYS: frozenset[str] = frozenset(
    {"mass_total_slots", "mass_finance_hits", "mass_ahk_hits"}
)
_MARKET_PULSE_DATE_KEYS: frozenset[str] = frozenset({"rzrq_date", "southbound_date"})
_ALLOWED_MASS_PLATFORMS: frozenset[str] = frozenset(
    {"微博", "百度", "今日头条", "头条", "抖音", "知乎", "B站", "小红书"}
)


def _format_yi_cny_display(val: float) -> str:
    return f"{val / 10000.0:.2f}万亿" if val >= 10000.0 else f"{val:.0f}亿"


def project_market_pulse(raw_mp: Any) -> dict[str, Any]:
    """Strictly allowlist scalar market-breadth fields and recompute displays from finite numbers."""
    if not isinstance(raw_mp, dict):
        return {}
    clean: dict[str, Any] = {}
    for k in _MARKET_PULSE_FLOAT_KEYS:
        v = raw_mp.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v)):
            clean[k] = round(float(v), 2)
    for k in _MARKET_PULSE_INT_KEYS:
        v = raw_mp.get(k)
        if isinstance(v, int) and not isinstance(v, bool) and v >= 0:
            clean[k] = int(v)
    for k in _MARKET_PULSE_DATE_KEYS:
        v = raw_mp.get(k)
        if isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v.strip()):
            clean[k] = v.strip()
    if isinstance(clean.get("a_turnover_yi"), float) and clean["a_turnover_yi"] > 0:
        clean["a_turnover_display"] = _format_yi_cny_display(clean["a_turnover_yi"])
    if isinstance(clean.get("rzrq_balance_yi"), float) and clean["rzrq_balance_yi"] > 0:
        clean["rzrq_balance_display"] = _format_yi_cny_display(clean["rzrq_balance_yi"])
    plats = raw_mp.get("mass_platforms")
    if isinstance(plats, list):
        safe_plats = [str(p).strip() for p in plats if isinstance(p, str) and p.strip() in _ALLOWED_MASS_PLATFORMS]
        if safe_plats:
            clean["mass_platforms"] = safe_plats[:6]
    return clean


_FORUM_CHANNEL_KEYS = ("id", "name", "status", "count", "note")
_FORUM_HOT_SCALAR_KEYS = ("platform", "topic", "title", "zh_brief", "sentiment", "heat", "url", "channel")
_FORUM_DIV_SCALAR_KEYS = (
    "topic",
    "desk_score",
    "desk_bias",
    "desk_count",
    "street_score",
    "street_heat",
    "street_count",
    "divergence_tag",
    "tone",
    "action_hint",
    "crowd_temp",
    "divergence_label",
)


def _scalar_only_copy(obj: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k in keys:
        if k not in obj:
            continue
        v = obj[k]
        if v is None or isinstance(v, (str, bool, int, float)):
            if isinstance(v, float) and not math.isfinite(v):
                continue
            out[k] = v
    return out


def _project_forum_sample(sample: Any, keys: tuple[str, ...]) -> dict[str, Any] | None:
    if not isinstance(sample, dict):
        return None
    clean = {
        k: str(sample.get(k) or "")
        for k in keys
        if k in sample and (sample.get(k) is None or isinstance(sample.get(k), (str, int, float)))
    }
    if not clean or has_private_article_text(json.dumps(clean, ensure_ascii=False)):
        return None
    return clean


def project_public_state(
    raw_state: dict[str, Any],
    *,
    include_candidates: bool = False,
    excluded_article_names: set[str] | None = None,
) -> dict[str, Any]:
    """Project raw cockpit state strictly onto public whitelisted keys without private data."""
    if not isinstance(raw_state, dict):
        raise ValueError("raw_state must be a dictionary")

    projected: dict[str, Any] = {}
    for key in PUBLIC_STATE_KEYS:
        if key in _PRIVATE_TOP_LEVEL_KEYS:
            continue
        if key == "signal_candidates" and not include_candidates:
            continue
        if key in raw_state and raw_state[key] is not None:
            projected[key] = _json_clone(raw_state[key])

    articles = projected.get("articles")
    if isinstance(articles, list):
        projected["articles"] = [
            article for article in articles
            if not has_private_article_content(article)
            and (not isinstance(article, dict) or safe_article_filename(article.get("filename")) not in (excluded_article_names or ()))
        ]
        if len(projected["articles"]) != len(articles) and isinstance(projected.get("coverage"), dict):
            projected["coverage"]["articles_count"] = len(projected["articles"])

    for sig_key in ("signals", "signal_candidates"):
        raw_sigs = projected.get(sig_key)
        if raw_sigs is not None and not isinstance(raw_sigs, list):
            projected.pop(sig_key, None)
        elif isinstance(raw_sigs, list):
            projected[sig_key] = [
                sig for sig in raw_sigs
                if isinstance(sig, dict)
                and not any(has_private_article_text(txt) for txt in _iter_strings(sig))
            ]

    raw_matrix = projected.get("asset_source_matrix")
    if raw_matrix is not None and not isinstance(raw_matrix, dict):
        projected.pop("asset_source_matrix", None)
    elif isinstance(raw_matrix, dict):
        raw_cats = raw_matrix.get("categories")
        clean_cats: list[dict[str, Any]] = []
        if isinstance(raw_cats, list):
            for cat in raw_cats:
                if not isinstance(cat, dict):
                    continue
                cid = cat.get("id")
                if not isinstance(cid, str) or not cid.strip():
                    continue
                clean_cat = {
                    k: v
                    for k, v in cat.items()
                    if k not in ("primary_venues", "top_kols", "guru_longforms", "crowd_warning_channel", "supply_chain_bridges")
                    and not any(has_private_article_text(t) for t in _iter_strings(v))
                }
                clean_cat["id"] = cid.strip()
                for list_k in ("primary_venues", "top_kols", "guru_longforms", "supply_chain_bridges"):
                    sub_v = cat.get(list_k)
                    if isinstance(sub_v, list):
                        clean_cat[list_k] = [
                            row for row in sub_v
                            if isinstance(row, dict)
                            and not any(has_private_article_text(t) for t in _iter_strings(row))
                        ]
                warn_c = cat.get("crowd_warning_channel")
                if isinstance(warn_c, dict) and not any(has_private_article_text(t) for t in _iter_strings(warn_c)):
                    clean_cat["crowd_warning_channel"] = warn_c
                if not any(has_private_article_text(t) for t in _iter_strings(clean_cat)):
                    clean_cats.append(clean_cat)
        if not clean_cats or has_private_article_text(str(raw_matrix.get("updated_at_bjt") or "")):
            projected.pop("asset_source_matrix", None)
        else:
            clean_matrix: dict[str, Any] = {"categories": clean_cats}
            if isinstance(raw_matrix.get("updated_at_bjt"), str):
                clean_matrix["updated_at_bjt"] = raw_matrix["updated_at_bjt"]
            if isinstance(raw_matrix.get("summary"), dict) and not any(has_private_article_text(t) for t in _iter_strings(raw_matrix["summary"])):
                clean_matrix["summary"] = dict(raw_matrix["summary"])
                clean_matrix["summary"]["categories_count"] = len(clean_cats)
            projected["asset_source_matrix"] = clean_matrix

    scorecards = projected.get("kol_scorecards")
    if scorecards is not None and not isinstance(scorecards, list):
        projected.pop("kol_scorecards", None)
    elif isinstance(scorecards, list):
        projected["kol_scorecards"] = [
            card for card in scorecards
            if isinstance(card, dict)
            and not has_private_article_text(json.dumps(card, ensure_ascii=False))
        ]

    kol_digest = projected.get("kol_digest")
    if kol_digest is not None and not isinstance(kol_digest, list):
        projected.pop("kol_digest", None)
    elif isinstance(kol_digest, list):
        projected["kol_digest"] = [
            item for item in kol_digest
            if isinstance(item, dict)
            and not has_private_article_text(json.dumps(item, ensure_ascii=False))
        ]

    forum_sentiment = projected.get("forum_sentiment")
    if forum_sentiment is not None and not isinstance(forum_sentiment, dict):
        projected.pop("forum_sentiment", None)
    elif isinstance(forum_sentiment, dict):
        clean_channels = []
        for ch in (forum_sentiment.get("channels_status") or []):
            if not isinstance(ch, dict):
                continue
            ch_copy = _scalar_only_copy(ch, _FORUM_CHANNEL_KEYS)
            if has_private_article_text(str(ch_copy.get("name") or "")):
                continue
            if has_private_article_text(str(ch_copy.get("note") or "")):
                ch_copy["note"] = "渠道状态正常"
            if not ch_copy or has_private_article_text(json.dumps(ch_copy, ensure_ascii=False)):
                continue
            clean_channels.append(ch_copy)
        clean_hot = []
        for h in (forum_sentiment.get("hot_discussions") or []):
            if not isinstance(h, dict):
                continue
            h_copy = _scalar_only_copy(h, _FORUM_HOT_SCALAR_KEYS)
            raw_tks = h.get("tickers")
            if isinstance(raw_tks, list):
                h_copy["tickers"] = [str(t) for t in raw_tks if isinstance(t, (str, int, float))][:6]
            if h_copy and not has_private_article_text(json.dumps(h_copy, ensure_ascii=False)):
                clean_hot.append(h_copy)
        clean_divs = []
        for div in (forum_sentiment.get("divergences") or []):
            if not isinstance(div, dict):
                continue
            d_copy = _scalar_only_copy(div, _FORUM_DIV_SCALAR_KEYS)
            if "desk_samples" in div:
                d_copy["desk_samples"] = [
                    s for s in (_project_forum_sample(x, ("author", "summary", "zh_brief", "url")) for x in (div.get("desk_samples") or []))
                    if s is not None
                ]
            if "street_samples" in div:
                d_copy["street_samples"] = [
                    s for s in (_project_forum_sample(x, ("platform", "title", "zh_brief", "url")) for x in (div.get("street_samples") or []))
                    if s is not None
                ]
            if (tag := d_copy.get("divergence_tag")) in SNAPSHOT_TAGS:
                d_copy.update(divergence_tag=SNAPSHOT_TAGS[tag], tone="neutral", action_hint="仅为当期样本，不代表持续趋势。")
            if has_private_article_text(str(d_copy.get("action_hint") or "")):
                d_copy["action_hint"] = "按既定风控纪律跟踪市场波动。"
            clean_divs.append(d_copy)
        rebuilt_forum: dict[str, Any] = _scalar_only_copy(
            forum_sentiment, ("updated_at_bjt", "overall_crowd_temp", "overall_label")
        )
        rebuilt_forum["market_pulse"] = project_market_pulse(forum_sentiment.get("market_pulse"))
        rebuilt_forum["channels_status"] = clean_channels
        rebuilt_forum["divergences"] = clean_divs
        rebuilt_forum["hot_discussions"] = clean_hot
        trends = project_discussion_trends(forum_sentiment.get("discussion_trends"))
        if trends:
            rebuilt_forum["discussion_trends"] = trends
        if has_private_article_text(json.dumps(rebuilt_forum, ensure_ascii=False)):
            projected.pop("forum_sentiment", None)
        else:
            projected["forum_sentiment"] = rebuilt_forum
            if isinstance(projected.get("coverage"), dict) and not clean_hot:
                ch_total = sum(int(c.get("count") or 0) for c in clean_channels)
                if ch_total == 0:
                    projected["coverage"]["forum_items_count"] = 0

    market_analysis = projected.get("market_analysis")
    if market_analysis is not None and not isinstance(market_analysis, dict):
        projected.pop("market_analysis", None)
    elif isinstance(market_analysis, dict) and has_private_article_text(json.dumps(market_analysis, ensure_ascii=False)):
        projected.pop("market_analysis", None)

    raw_event_cards = projected.get("event_cards")
    if raw_event_cards is not None and not isinstance(raw_event_cards, list):
        projected.pop("event_cards", None)
    elif isinstance(raw_event_cards, list):
        from backend.event_feed import project_event_cards

        projected["event_cards"] = project_event_cards(raw_event_cards)

    raw_event_stats = projected.get("event_stats")
    if raw_event_stats is not None and not isinstance(raw_event_stats, dict):
        projected.pop("event_stats", None)
    elif isinstance(raw_event_stats, dict):
        projected["event_stats"] = _scalar_only_copy(
            raw_event_stats,
            (
                "raw_items",
                "opinions_skipped",
                "total_events",
                "new_events",
                "reused_or_replayed",
                "reprint_collapsed",
                "low_conf_linked",
                "roundups",
                "multi_source_count",
                "confirmed_count",
                "denied_or_corrected_count",
            ),
        )

    raw_signal_plans = projected.get("signal_plans")
    if raw_signal_plans is not None and not isinstance(raw_signal_plans, list):
        projected.pop("signal_plans", None)
    elif isinstance(raw_signal_plans, list):
        from backend.signal_advisor import project_signal_plans

        projected["signal_plans"] = project_signal_plans(raw_signal_plans)

    raw_plan_stats = projected.get("signal_plan_stats")
    if raw_plan_stats is not None and not isinstance(raw_plan_stats, dict):
        projected.pop("signal_plan_stats", None)
    elif isinstance(raw_plan_stats, dict):
        from backend.signal_advisor import project_signal_plan_stats

        clean_ps = project_signal_plan_stats(raw_plan_stats)
        if clean_ps is not None:
            projected["signal_plan_stats"] = clean_ps
        else:
            projected.pop("signal_plan_stats", None)

    projected["schema_version"] = int(projected.get("schema_version") or 2)
    projected["strategy_targets"] = _build_structured_strategy_targets(projected if "positions" in projected else raw_state)
    projected["coverage"] = _build_default_coverage(projected)
    if not include_candidates:
        projected.pop("signal_candidates", None)
    return projected


def _canonical_state_sha256(state: dict[str, Any]) -> str:
    canonical = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _normalize_articles(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for item in articles or []:
        if not isinstance(item, dict):
            raise ValueError("article entry must be a dict")
        raw_name = item.get("filename")
        safe_name = safe_article_filename(raw_name)
        if not safe_name:
            raise ValueError(f"invalid_article_filename: {raw_name!r}")
        if safe_name in seen_names:
            continue
        content = item.get("content")
        if not isinstance(content, str):
            raise ValueError(f"invalid_article_content: {safe_name}")
        content_bytes = content.encode("utf-8")
        if len(content_bytes) > MAX_ARTICLE_BYTES:
            raise ValueError(f"article_too_large: {safe_name}")
        seen_names.add(safe_name)
        normalized.append(
            {
                "id": Path(safe_name).stem,
                "filename": safe_name,
                "content": content,
                "bytes": len(content_bytes),
                "sha256": hashlib.sha256(content_bytes).hexdigest(),
            }
        )
    return normalized


def _prune_old_generations(gens_dir: Path, active_gen: int, keep: int = KEEP_GENERATIONS) -> list[str]:
    if not gens_dir.exists():
        return []
    found: list[tuple[int, Path]] = []
    removed: list[str] = []
    for child in gens_dir.iterdir():
        if not child.is_dir():
            continue
        m = re.fullmatch(r"gen_(\d+)", child.name)
        if m:
            found.append((int(m.group(1)), child))
        elif child.name.startswith(".staging_gen_"):
            shutil.rmtree(child, ignore_errors=True)
            removed.append(child.name)
    found.sort(key=lambda pair: pair[0], reverse=True)
    keep_ids = {active_gen}
    for gen_num, _ in found:
        if len(keep_ids) >= max(1, keep):
            break
        keep_ids.add(gen_num)
    for gen_num, folder in found:
        if gen_num not in keep_ids:
            shutil.rmtree(folder, ignore_errors=True)
            removed.append(folder.name)
    return removed


def _generation_timestamp(value: Any) -> datetime:
    stamp = datetime.fromisoformat(str(value or ""))
    return stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=BJT)


def _read_generation_state(folder: Path, generation: int) -> dict[str, Any]:
    state = json.loads((folder / "state.json").read_text(encoding="utf-8"))
    if not isinstance(state, dict) or type(state.get("generation")) is not int or state["generation"] != generation:
        raise ValueError("invalid generation state")
    return state


def _current_manifest(manifest_path: Path, *, recover_newer: bool = False) -> dict[str, Any] | None:
    gens_dir = manifest_path.parent / "generations"
    active = None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        gen = manifest["generation"]
        if (
            type(gen) is not int or gen <= 0
            or not isinstance(manifest["articles"], list)
            or any(not isinstance(a, dict) or not safe_article_filename(a.get("filename")) for a in manifest["articles"])
        ):
            raise ValueError("invalid generation manifest")
        _generation_timestamp(manifest["generated_at"])
        _generation_timestamp(manifest["published_at"])
        state = _read_generation_state(gens_dir / f"gen_{gen}", gen)
        if manifest["state_sha256"] != _canonical_state_sha256(state):
            raise ValueError("invalid generation state checksum")
        if not recover_newer:
            return manifest
        active = manifest
    except (OSError, ValueError, TypeError, KeyError):
        pass

    # Older generations have no per-generation manifest. Rebuild it from their files.
    candidates = []
    if gens_dir.is_dir():
        for folder in gens_dir.iterdir():
            match = re.fullmatch(r"gen_([1-9]\d*)", folder.name)
            if match and folder.is_dir():
                candidates.append((int(match[1]), folder))
    for gen, folder in sorted(candidates, reverse=True):
        if active is not None and gen <= active["generation"]:
            return active
        try:
            state = _read_generation_state(folder, gen)
            article_dir = folder / "articles"
            articles = _normalize_articles([
                {"filename": path.name, "content": path.read_bytes().decode("utf-8")}
                for path in sorted(article_dir.glob("*")) if path.suffix.lower() == ".md" and path.is_file()
            ])
            by_name = {
                a["filename"]: {key: a[key] for key in ("id", "filename", "bytes", "sha256")}
                for a in articles
            }
            state_sha = _canonical_state_sha256(state)
            saved = folder / "manifest.json"
            if saved.exists():
                manifest = json.loads(saved.read_text(encoding="utf-8"))
                _generation_timestamp(manifest["generated_at"])
                _generation_timestamp(manifest["published_at"])
                entries = manifest["articles"]
                if (
                    type(manifest["generation"]) is not int
                    or manifest["generation"] != gen
                    or manifest["state_sha256"] != state_sha
                    or not isinstance(entries, list)
                    or len(entries) != len(by_name)
                    or any(by_name.get(a["filename"]) != a for a in entries)
                ):
                    raise ValueError("invalid generation manifest")
                return manifest

            published_at = datetime.fromtimestamp(folder.stat().st_mtime, BJT).isoformat(timespec="seconds")
            generated_at = state.get("generated_at_bjt") or published_at
            _generation_timestamp(generated_at)
            return {
                "schema_version": 2,
                "generation": gen,
                "generated_at": generated_at,
                "published_at": published_at,
                "producer_id": "windows-primary",
                "state_sha256": state_sha,
                "articles": list(by_name.values()),
                "coverage": state.get("coverage") or {},
            }
        except (OSError, ValueError, TypeError, KeyError):
            continue
    if active is not None:
        return active
    if candidates or manifest_path.exists():
        raise ValueError("invalid_current_generation")
    return None


def publish_generation(
    base_dir: Path | str,
    state: dict[str, Any],
    articles: list[dict[str, Any]] | None = None,
    *,
    producer_id: str = "windows-primary",
    generation: int | None = None,
) -> dict[str, Any]:
    """Validate public projection + articles, enforce monotonic generations, and publish atomically."""
    base = Path(base_dir)
    data_dir = resolve_data_dir(base)
    gens_dir = data_dir / "generations"
    articles_dir = base / "articles"
    manifest_path = data_dir / "current_generation.json"

    has_candidates = isinstance(state, dict) and state.get("signal_candidates") is not None
    blocked_names = private_article_filenames(articles or [])
    if isinstance(state, dict):
        blocked_names.update(private_article_filenames(state.get("articles") or []))
    clean_state = project_public_state(
        state, include_candidates=has_candidates, excluded_article_names=blocked_names
    )
    norm_articles = _normalize_articles([
        a for a in (articles or []) if not has_private_article_content(a)
        and (not isinstance(a, dict) or safe_article_filename(a.get("filename")) not in blocked_names)
    ])
    article_meta = [
        {"id": a["id"], "filename": a["filename"], "bytes": a["bytes"], "sha256": a["sha256"]}
        for a in norm_articles
    ]

    with _PUBLISH_LOCK, file_lock(manifest_path):
        cur_manifest = _current_manifest(manifest_path, recover_newer=True)
        cur_gen = cur_manifest["generation"] if cur_manifest is not None else 0

        if generation is not None:
            target_gen = int(generation)
            if target_gen <= 0:
                raise ValueError("invalid_generation")
        else:
            req_gen = int(clean_state.get("generation") or 0)
            target_gen = max(req_gen, cur_gen + 1)
            while (gens_dir / f"gen_{target_gen}").exists():
                target_gen += 1

        generated_at = str(clean_state.get("generated_at_bjt") or _now_bjt_iso())
        generated_stamp = _generation_timestamp(generated_at)
        if cur_manifest is not None:
            cur_stamp = _generation_timestamp(cur_manifest["generated_at"])
            if generated_stamp < cur_stamp:
                if clean_state.get("generated_at_bjt"):
                    raise ValueError("stale_generation")
                generated_at = cur_manifest["generated_at"]

        # Preserve richer server-side multi-day discussion trends across stateless remote publishes (e.g. Windows vps_sync)
        fs_clean = clean_state.get("forum_sentiment")
        if isinstance(fs_clean, dict):
            def _trend_richness(tr: Any) -> tuple[int, int]:
                if not isinstance(tr, dict):
                    return (0, 0)
                max_days = 0
                non_insuf = 0
                for a in tr.get("assets") or []:
                    for w in (a.get("windows") or []) if isinstance(a, dict) else []:
                        if isinstance(w, dict):
                            max_days = max(max_days, int(w.get("sample_days") or 0))
                            if w.get("state") not in (None, "insufficient"):
                                non_insuf += 1
                return (non_insuf, max_days)

            best_trends = project_discussion_trends(fs_clean.get("discussion_trends"))
            best_score = _trend_richness(best_trends)
            cached_trends = load_valid_trends_from_cache(data_dir)
            if _trend_richness(cached_trends) > best_score:
                best_trends = cached_trends
                best_score = _trend_richness(cached_trends)
            if cur_manifest is not None and cur_gen > 0:
                try:
                    prior_state = _read_generation_state(gens_dir / f"gen_{cur_gen}", cur_gen)
                    prior_tr = project_discussion_trends((prior_state.get("forum_sentiment") or {}).get("discussion_trends"))
                    if _trend_richness(prior_tr) > best_score:
                        best_trends = prior_tr
                except (OSError, ValueError, TypeError):
                    pass
            if best_trends:
                fs_clean["discussion_trends"] = best_trends
            else:
                fs_clean.pop("discussion_trends", None)

        from backend.event_feed import (
            LEDGER_FILENAME,
            project_event_cards,
            try_load_public_event_cards_from_ledger,
        )

        explicit_events = isinstance(state, dict) and state.get("event_cards") is not None
        if explicit_events:
            chosen_events = clean_state.get("event_cards") if isinstance(clean_state.get("event_cards"), list) else []
        else:
            ledger_file = data_dir / LEDGER_FILENAME
            loaded_cards = try_load_public_event_cards_from_ledger(data_dir) if ledger_file.exists() else None
            if loaded_cards is not None:
                chosen_events = loaded_cards
            elif cur_manifest is not None and cur_gen > 0:
                chosen_events = []
                try:
                    prior_ev_state = _read_generation_state(gens_dir / f"gen_{cur_gen}", cur_gen)
                    chosen_events = project_event_cards(prior_ev_state.get("event_cards"))
                except (OSError, ValueError, TypeError):
                    chosen_events = []
            else:
                chosen_events = []
        if chosen_events:
            clean_state["event_cards"] = chosen_events
        else:
            clean_state.pop("event_cards", None)

        from backend.signal_advisor import (
            ADVISOR_LEDGER_FILENAME,
            compute_signal_plan_stats,
            merge_signal_plans,
            project_signal_plans,
            try_load_public_signal_plans_from_ledger,
        )

        adv_file = data_dir / ADVISOR_LEDGER_FILENAME
        loaded_plans, _loaded_pstats = try_load_public_signal_plans_from_ledger(data_dir) if adv_file.exists() else (None, None)
        prior_plans: list[dict[str, Any]] = []
        if cur_manifest is not None and cur_gen > 0:
            try:
                prior_pl_state = _read_generation_state(gens_dir / f"gen_{cur_gen}", cur_gen)
                prior_plans = project_signal_plans(prior_pl_state.get("signal_plans"))
            except (OSError, ValueError, TypeError):
                prior_plans = []
        base_plans = merge_signal_plans(loaded_plans or [], prior_plans)

        explicit_plans = isinstance(state, dict) and state.get("signal_plans") is not None
        incoming_plans = clean_state.get("signal_plans") if (explicit_plans and isinstance(clean_state.get("signal_plans"), list)) else []
        if incoming_plans:
            chosen_plans = merge_signal_plans(incoming_plans, base_plans)
        else:
            chosen_plans = base_plans

        if chosen_plans:
            clean_state["signal_plans"] = chosen_plans
            clean_state["signal_plan_stats"] = compute_signal_plan_stats(chosen_plans)
        else:
            clean_state.pop("signal_plans", None)
            clean_state.pop("signal_plan_stats", None)

        clean_state["generation"] = target_gen
        state_sha = _canonical_state_sha256(clean_state)

        if cur_manifest is not None:
            if target_gen < cur_gen:
                raise ValueError("stale_generation")
            if target_gen == cur_gen:
                if (
                    cur_manifest.get("state_sha256") == state_sha
                    and sorted(cur_manifest["articles"], key=lambda a: a["filename"])
                    == sorted(article_meta, key=lambda a: a["filename"])
                ):
                    _atomic_write_text(manifest_path, json.dumps(cur_manifest, ensure_ascii=False, indent=2) + "\n")
                    _prune_old_generations(gens_dir, target_gen)
                    out = dict(cur_manifest)
                    out["idempotent"] = True
                    return out
                raise ValueError("generation_conflict")

        manifest: dict[str, Any] = {
            "schema_version": 2,
            "generation": target_gen,
            "generated_at": generated_at,
            "published_at": _now_bjt_iso(),
            "producer_id": str(producer_id or "windows-primary"),
            "state_sha256": state_sha,
            "articles": article_meta,
            "coverage": clean_state.get("coverage") or {},
        }
        stage_dir = gens_dir / f".staging_gen_{target_gen}_{os.getpid()}_{time.time_ns()}"
        stage_articles = stage_dir / "articles"
        stage_articles.mkdir(parents=True)
        final_gen_dir = gens_dir / f"gen_{target_gen}"
        manifest_text = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
        state_text = json.dumps(clean_state, ensure_ascii=False, indent=2) + "\n"
        try:
            (stage_dir / "state.json").write_text(state_text, encoding="utf-8")
            (stage_dir / "manifest.json").write_text(manifest_text, encoding="utf-8")
            for art in norm_articles:
                (stage_articles / art["filename"]).write_text(art["content"], encoding="utf-8", newline="")
            if final_gen_dir.exists():
                raise ValueError("generation_conflict")
            for attempt in range(5):
                try:
                    os.replace(stage_dir, final_gen_dir)
                    break
                except PermissionError:
                    if attempt == 4:
                        raise
                    time.sleep(0.05)
            _atomic_write_text(manifest_path, manifest_text)
        finally:
            if stage_dir.exists():
                shutil.rmtree(stage_dir, ignore_errors=True)

        # Keep compatibility copies synchronized after switching the generation manifest
        compat_state = data_dir / "cockpit_state.json"
        with file_lock(compat_state):
            _atomic_write_text(compat_state, state_text)
        articles_dir.mkdir(parents=True, exist_ok=True)
        for art in norm_articles:
            _atomic_write_text(articles_dir / art["filename"], art["content"])

        _prune_old_generations(gens_dir, target_gen, keep=KEEP_GENERATIONS)
        return manifest


def load_published_generation(base_dir: Path | str) -> dict[str, Any]:
    """Load the active published state strictly via current_generation.json without aggregation."""
    base = Path(base_dir)
    data_dir = resolve_data_dir(base)
    manifest_path = data_dir / "current_generation.json"
    manifest = _current_manifest(manifest_path)
    if manifest is not None:
        gen = manifest["generation"]
        raw_state = _read_generation_state(data_dir / "generations" / f"gen_{gen}", gen)
        state = project_public_state(raw_state, include_candidates="signal_candidates" in raw_state)
        return {"generation": gen, "manifest": manifest, "state": state}

    legacy_state_path = data_dir / "cockpit_state.json"
    if not legacy_state_path.is_file():
        raise FileNotFoundError(f"No published state found in {data_dir}")
    raw_legacy = json.loads(legacy_state_path.read_text(encoding="utf-8"))
    has_candidates = isinstance(raw_legacy, dict) and raw_legacy.get("signal_candidates") is not None
    state = project_public_state(raw_legacy, include_candidates=has_candidates)
    return {"generation": int(state.get("generation") or 0), "manifest": None, "state": state}


def lookup_published_article(base_dir: Path | str, filename: str) -> dict[str, Any] | None:
    """Resolve an article markdown file strictly against current_generation manifest."""
    safe_name = safe_article_filename(filename)
    if not safe_name:
        raise ValueError(f"invalid_article_filename: {filename!r}")

    base = Path(base_dir)
    data_dir = resolve_data_dir(base)
    manifest_path = data_dir / "current_generation.json"
    manifest = _current_manifest(manifest_path)
    if manifest is not None:
        gen = manifest["generation"]
        entries = manifest["articles"]
        matched = next((e for e in entries if isinstance(e, dict) and e.get("filename") == safe_name), None)
        if matched is None:
            return None
        gen_articles = (data_dir / "generations" / f"gen_{gen}" / "articles").resolve()
        candidate = (gen_articles / safe_name).resolve()
        if candidate.parent != gen_articles or not candidate.is_file():
            return None
        content = candidate.read_text(encoding="utf-8", errors="replace")
        if has_private_article_content({"filename": safe_name, "content": content}):
            return None
        return {"filename": safe_name, "generation": gen, "meta": matched, "content": content}

    legacy_dir = (base / "articles").resolve()
    candidate = (legacy_dir / safe_name).resolve()
    if candidate.parent != legacy_dir or not candidate.is_file():
        return None
    raw_bytes = candidate.read_bytes()
    content = raw_bytes.decode("utf-8", errors="replace")
    if has_private_article_content({"filename": safe_name, "content": content}):
        return None
    return {
        "filename": safe_name,
        "generation": 0,
        "meta": {
            "id": candidate.stem,
            "filename": safe_name,
            "bytes": len(raw_bytes),
            "sha256": hashlib.sha256(raw_bytes).hexdigest(),
        },
        "content": content,
    }
