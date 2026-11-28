"""Sync sanitized public state to VPS (v2 publish/SSH) and optionally sync private portfolio via PORTFOLIO_RW."""
from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import requests

from backend.guardrails import has_private_article_content, private_article_filenames, safe_article_filename
from backend.runtime import BJT as _BJT, DATA_DIR, file_lock, resolve_data_dir
from backend.mobile_push import load_push_env
from backend.public_snapshot import project_public_state
from backend.article_store import get_allowed_article_dirs, parse_markdown_article, read_article_markdown

STATE_PATH = DATA_DIR / "cockpit_state.json"
DEFAULT_JOURNAL_DB_PATH = DATA_DIR / "user_journal.db"
MAX_ARTICLE_BYTES = 256 * 1024
_MANAGED_SETTING_KEYS: tuple[str, ...] = ("watchlist", "currency", "default_account_id", "alert_prefs")


_safe_article_name = safe_article_filename


def collect_payload(
    state_path: Path,
    max_articles: int,
    *,
    generation: int | None = None,
    producer_id: str = "windows-primary",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load local state, strip private keys via project_public_state(include_candidates=True), and attach articles."""
    raw_state = json.loads(state_path.read_text(encoding="utf-8"))
    blocked = private_article_filenames(raw_state.get("articles") or [])
    names: list[str] = []
    for article in raw_state.get("articles") or []:
        name = safe_article_filename(article.get("filename")) if isinstance(article, dict) else None
        if name and name not in names:
            names.append(name)
    for kol in raw_state.get("kol_digest") or []:
        name = safe_article_filename(kol.get("article_file")) if isinstance(kol, dict) else None
        if name and name not in names:
            names.append(name)

    articles: list[dict[str, str]] = []
    inbox_names = {
        name
        for article in raw_state.get("articles") or []
        if isinstance(article, dict) and article.get("source") == "本地知识库"
        and (name := safe_article_filename(article.get("filename")))
    }
    allowed_dirs = [(state_path.parent.parent / "articles", "同步深度长文"), *get_allowed_article_dirs()]
    refreshed_inbox = {}
    for name in names:
        if name in blocked:
            continue
        for folder, label in allowed_dirs:
            root = folder.resolve()
            candidate = (root / name).resolve()
            if candidate.parent != root or not candidate.is_file():
                continue
            content = read_article_markdown(candidate, label, max_bytes=MAX_ARTICLE_BYTES)
            if content is not None and label == "本地知识库":
                fresh = parse_markdown_article(candidate, label)
                if fresh is None:
                    content = None
                else:
                    refreshed_inbox[name] = fresh
            if content is None:
                blocked.add(name)
            elif len(articles) < max_articles:
                articles.append({"filename": name, "content": content})
            break
        else:
            if name in inbox_names:
                blocked.add(name)

    if refreshed_inbox:
        raw_state["articles"] = [
            refreshed_inbox.get(safe_article_filename(article.get("filename")), article)
            if isinstance(article, dict) else article
            for article in raw_state.get("articles") or []
        ]
    state = project_public_state(raw_state, include_candidates=True, excluded_article_names=blocked)
    state.pop("event_cards", None)
    state.pop("event_stats", None)
    if generation is not None:
        state["generation"] = int(generation)

    payload: dict[str, Any] = {
        "state": state,
        "articles": articles,
        "producer_id": str(producer_id or "windows-primary"),
    }
    if generation is not None:
        payload["generation"] = int(generation)

    stats = {
        "articles_count": len(articles),
        "state_bytes": len(json.dumps(state, ensure_ascii=False).encode("utf-8")),
        "article_bytes": sum(len(a["content"].encode("utf-8")) for a in articles),
    }
    return payload, stats


def _resolve_publish_endpoints(explicit_url: str | None = None) -> tuple[str, str | None]:
    configured = (explicit_url or os.environ.get("MACROCOCKPIT_SYNC_URL") or "").strip()
    if configured:
        cleaned = configured.rstrip("/")
        if cleaned.endswith("/api/sync"):
            return cleaned[: -len("/api/sync")] + "/api/v2/publish", cleaned
        if cleaned.endswith("/api/v2/publish"):
            return cleaned, cleaned[: -len("/api/v2/publish")] + "/api/sync"
        return cleaned, None

    base = (os.environ.get("MACROCOCKPIT_PUBLIC_URL") or "https://news.shuashuabao.xyz").strip().rstrip("/")
    return f"{base}/api/v2/publish", f"{base}/api/sync"


def _resolve_portfolio_base_url(explicit_url: str | None = None) -> str:
    configured = (explicit_url or os.environ.get("MACROCOCKPIT_PORTFOLIO_URL") or "").strip().rstrip("/")
    if configured:
        for suffix in ("/api/v2/publish", "/api/sync", "/api/v2/workspace", "/api/v2/changes"):
            if configured.endswith(suffix):
                return configured[: -len(suffix)]
        return configured
    return (os.environ.get("MACROCOCKPIT_PUBLIC_URL") or "https://news.shuashuabao.xyz").strip().rstrip("/")


def _validate_portfolio_url(base_url: str) -> str | None:
    import urllib.parse

    parsed = urllib.parse.urlsplit(str(base_url or "").strip())
    scheme = (parsed.scheme or "").lower()
    host = (parsed.hostname or "").strip().lower()
    if scheme not in ("https", "http") or not host:
        return "invalid_portfolio_url"
    if scheme == "http" and host not in ("127.0.0.1", "localhost", "::1"):
        return "insecure_http_forbidden"
    return None


def _norm_dec(value: Any, *, allow_none: bool = False) -> str | None:
    if value is None or str(value).strip() == "":
        return None if allow_none else "0"
    try:
        dec = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None if allow_none else "0"
    if not dec.is_finite():
        return None if allow_none else "0"
    if dec == 0:
        return "0"
    s = format(dec.normalize(), "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def _parse_ts_epoch(raw_ts: Any) -> float:
    s = str(raw_ts or "").strip()
    if not s:
        return 0.0
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_BJT)
        return dt.timestamp()
    except ValueError:
        return 0.0


def _canonical_portfolio_digest(model: dict[str, Any]) -> str:
    canonical = json.dumps(model, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _holdings_submodel(model: dict[str, Any]) -> dict[str, Any]:
    return {
        "accounts": list(model.get("accounts") or []),
        "spots": list(model.get("spots") or []),
        "derivatives": list(model.get("derivatives") or []),
    }


def _prefs_submodel(model: dict[str, Any]) -> dict[str, Any]:
    return {
        "rules": list(model.get("rules") or []),
        "settings": dict(model.get("settings") or {}),
    }


def _normalize_accounts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = [
        {
            "id": str(r.get("id") or ""),
            "name": str(r.get("name") or ""),
            "kind": str(r.get("kind") or "general"),
            "venue": str(r.get("venue") or ""),
            "base_currency": str(r.get("base_currency") or "USD").upper(),
            "equity_mode": str(r.get("equity_mode") or "total_ex_upl"),
            "confirmed_debt_usd": _norm_dec(r.get("confirmed_debt_usd")) or "0",
            "notes": str(r.get("notes") or ""),
            "archived": bool(r.get("archived", False)),
        }
        for r in rows
        if isinstance(r, dict) and not bool(r.get("archived", False))
    ]
    out.sort(key=lambda x: x["id"])
    return out


def _normalize_spots(rows: list[dict[str, Any]], instruments: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    inst_map = instruments or {}
    out: list[dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        iid = str(r.get("instrument_id") or r.get("symbol") or "").strip().upper()
        inst = inst_map.get(iid, {})
        is_cash = bool(r.get("is_cash", False))
        qccy = str(r.get("quote_currency") or inst.get("quote_currency") or r.get("cost_currency") or "USD").upper()
        cost_ccy = str(r.get("cost_currency") or qccy).upper()
        out.append(
            {
                "account_id": str(r.get("account_id") or "acct_default"),
                "instrument_id": iid,
                "symbol": str(r.get("symbol") or inst.get("symbol") or iid),
                "name": str(r.get("name") or inst.get("name") or iid),
                "asset_class": str(r.get("asset_class") or inst.get("asset_class") or ("cash" if is_cash else "equity")),
                "quote_currency": qccy,
                "cost_currency": cost_ccy,
                "is_cash": is_cash,
                "qty": _norm_dec(r.get("qty")) or "0",
                "avg_cost": _norm_dec(r.get("avg_cost")) or "0",
                "locked_margin_usd": _norm_dec(r.get("locked_margin_usd")) or "0",
                "sleeve": str(r.get("sleeve") or ""),
                "stop_loss": _norm_dec(r.get("stop_loss"), allow_none=True),
                "take_profit": _norm_dec(r.get("take_profit"), allow_none=True),
                "thesis": str(r.get("thesis") or ""),
            }
        )
    out.sort(key=lambda x: (x["account_id"], x["instrument_id"]))
    return out


def _normalize_derivatives(rows: list[dict[str, Any]], instruments: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    inst_map = instruments or {}
    out: list[dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        iid = str(r.get("instrument_id") or r.get("symbol") or "").strip().upper()
        inst = inst_map.get(iid, {})
        ctype = str(r.get("contract_type") or ("inverse" if iid.endswith("-USD-SWAP") else "linear")).lower()
        qccy = str(r.get("quote_currency") or inst.get("quote_currency") or ("USD" if ctype == "inverse" else "USDT")).upper()
        base_asset = str(inst.get("base_asset") or iid.split("-")[0]).upper()
        mccy = str(r.get("margin_currency") or inst.get("settlement_currency") or (base_asset if ctype == "inverse" else qccy)).upper()
        margin_usd = _norm_dec(r.get("margin_usd")) or "0"
        margin_amt = _norm_dec(r.get("margin_amount") if r.get("margin_amount") not in (None, "") else margin_usd) or "0"
        raw_mark = r["raw_mark_price"] if "raw_mark_price" in r else r.get("mark_price")
        out.append(
            {
                "id": str(r.get("id") or ""),
                "account_id": str(r.get("account_id") or "acct_default"),
                "instrument_id": iid,
                "symbol": str(r.get("symbol") or inst.get("symbol") or iid),
                "quote_currency": qccy,
                "side": str(r.get("side") or "").lower(),
                "margin_mode": str(r.get("margin_mode") or "cross").lower(),
                "qty": _norm_dec(r.get("qty")) or "0",
                "qty_unit": str(r.get("qty_unit") or "coin").lower(),
                "contract_multiplier": _norm_dec(r.get("contract_multiplier") if r.get("contract_multiplier") not in (None, "") else "1") or "1",
                "contract_type": ctype,
                "entry_price": _norm_dec(r.get("entry_price")) or "0",
                "margin_usd": margin_usd,
                "margin_amount": margin_amt,
                "margin_currency": mccy,
                "leverage": _norm_dec(r.get("leverage"), allow_none=True),
                "mmr": _norm_dec(r.get("mmr"), allow_none=True),
                "venue_liq": _norm_dec(r.get("venue_liq"), allow_none=True),
                "liq_asof": str(r.get("liq_asof") or ""),
                "mark_price": _norm_dec(raw_mark, allow_none=True),
                "funding_rate": _norm_dec(r.get("funding_rate"), allow_none=True),
                "sleeve": str(r.get("sleeve") or ""),
                "stop_loss": _norm_dec(r.get("stop_loss"), allow_none=True),
                "take_profit": _norm_dec(r.get("take_profit"), allow_none=True),
                "thesis": str(r.get("thesis") or ""),
            }
        )
    out.sort(key=lambda x: (x["account_id"], x["instrument_id"], x["id"]))
    return out


def _normalize_rules(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = [
        {
            "id": str(r.get("id") or ""),
            "enabled": bool(r.get("enabled", True)),
            "kind": str(r.get("kind") or "").lower(),
            "target": str(r.get("target") or ""),
            "action": str(r.get("action") or "").lower(),
            "points": _norm_dec(r.get("points")) or "0",
            "note": str(r.get("note") or ""),
        }
        for r in rows
        if isinstance(r, dict)
    ]
    out.sort(key=lambda x: x["id"])
    return out


def _normalize_settings(prefs: dict[str, Any] | None) -> dict[str, Any]:
    raw = prefs if isinstance(prefs, dict) else {}
    return {
        "watchlist": list(raw.get("watchlist") or []),
        "currency": str(raw.get("currency") or "USD").upper(),
        "default_account_id": str(raw.get("default_account_id") or "acct_default"),
        "alert_prefs": dict(raw.get("alert_prefs") or {}),
    }


def export_local_portfolio_state(db_path: Path | str) -> dict[str, Any]:
    """Read local user_journal.db portfolio tables read-only and build normalized state + split domain timestamps."""
    path = Path(db_path)
    if not path.is_file():
        model = {
            "accounts": [],
            "spots": [],
            "derivatives": [],
            "rules": [],
            "settings": _normalize_settings({}),
        }
        empty_h_dig = _canonical_portfolio_digest(_holdings_submodel(model))
        empty_p_dig = _canonical_portfolio_digest(_prefs_submodel(model))
        return {
            "model": model,
            "digest": _canonical_portfolio_digest(model),
            "holdings_digest": empty_h_dig,
            "prefs_digest": empty_p_dig,
            "holdings_updated_epoch": 0.0,
            "holdings_updated_at": "",
            "prefs_updated_epoch": 0.0,
            "prefs_updated_at": "",
            "max_updated_epoch": 0.0,
            "max_updated_at": "",
            "stored_settings_keys": [],
        }

    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=5.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        accounts_raw = [dict(r) for r in conn.execute("SELECT * FROM account WHERE archived=0 ORDER BY created_at ASC, id ASC").fetchall()]
        instruments = {str(r["id"]).upper(): dict(r) for r in conn.execute("SELECT * FROM instrument").fetchall()}
        spots_raw = [dict(r) for r in conn.execute("SELECT * FROM spot_balance ORDER BY account_id ASC, instrument_id ASC").fetchall()]
        derivs_raw = [dict(r) for r in conn.execute("SELECT * FROM derivative_position ORDER BY created_at ASC, id ASC").fetchall()]
        rules_raw = [dict(r) for r in conn.execute("SELECT * FROM curation_rule ORDER BY created_at ASC, id ASC").fetchall()]

        prefs: dict[str, Any] = {
            "currency": "USD",
            "default_account_id": "acct_default",
            "watchlist": [],
            "alert_prefs": {},
        }
        stored_keys: list[str] = []
        holdings_ts: list[tuple[float, str]] = []
        prefs_ts: list[tuple[float, str]] = []
        for r in conn.execute("SELECT key, value_json, updated_at FROM user_setting ORDER BY key").fetchall():
            k = str(r["key"])
            if k == "hide_amounts":
                continue
            stored_keys.append(k)
            try:
                prefs[k] = json.loads(r["value_json"])
            except Exception:
                prefs[k] = r["value_json"]
            ep = _parse_ts_epoch(r["updated_at"])
            if ep > 0:
                prefs_ts.append((ep, str(r["updated_at"])))

        for rows in (accounts_raw, spots_raw, derivs_raw):
            for r in rows:
                ep = _parse_ts_epoch(r.get("updated_at"))
                if ep > 0:
                    holdings_ts.append((ep, str(r["updated_at"])))
        for r in rules_raw:
            ep = _parse_ts_epoch(r.get("updated_at"))
            if ep > 0:
                prefs_ts.append((ep, str(r["updated_at"])))
        for entities, bucket in ((('account', 'spot', 'derivative'), holdings_ts), (('rule', 'setting'), prefs_ts)):
            try:
                ph = ",".join("?" for _ in entities)
                cs_row = conn.execute(
                    f"SELECT MAX(created_at) AS ts FROM change_item WHERE entity IN ({ph})",
                    entities,
                ).fetchone()
                if cs_row and cs_row["ts"]:
                    ep = _parse_ts_epoch(cs_row["ts"])
                    if ep > 0:
                        bucket.append((ep, str(cs_row["ts"])))
            except sqlite3.Error:
                pass
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
    finally:
        conn.close()

    model = {
        "accounts": _normalize_accounts(accounts_raw),
        "spots": _normalize_spots(spots_raw, instruments),
        "derivatives": _normalize_derivatives(derivs_raw, instruments),
        "rules": _normalize_rules(rules_raw),
        "settings": _normalize_settings(prefs),
    }
    h_ep, h_ts = max(holdings_ts, key=lambda x: x[0]) if holdings_ts else (0.0, "")
    p_ep, p_ts = max(prefs_ts, key=lambda x: x[0]) if prefs_ts else (0.0, "")
    all_ts = [*holdings_ts, *prefs_ts]
    max_ep, max_ts = max(all_ts, key=lambda x: x[0]) if all_ts else (0.0, "")
    return {
        "model": model,
        "digest": _canonical_portfolio_digest(model),
        "holdings_digest": _canonical_portfolio_digest(_holdings_submodel(model)),
        "prefs_digest": _canonical_portfolio_digest(_prefs_submodel(model)),
        "holdings_updated_epoch": h_ep,
        "holdings_updated_at": h_ts,
        "prefs_updated_epoch": p_ep,
        "prefs_updated_at": p_ts,
        "max_updated_epoch": max_ep,
        "max_updated_at": max_ts,
        "stored_settings_keys": [k for k in _MANAGED_SETTING_KEYS if k in stored_keys],
        "instrument_meta": {
            iid: {
                k: inst[k]
                for k in ("name", "asset_class", "base_asset")
                if inst.get(k) not in (None, "")
            }
            for iid, inst in instruments.items()
        },
    }


def _extract_remote_portfolio_state(ws: dict[str, Any], prefs_resp: dict[str, Any] | None = None) -> dict[str, Any]:
    raw_pf = ws.get("raw_portfolio") if isinstance(ws.get("raw_portfolio"), dict) else None
    if raw_pf is not None:
        accounts_raw = list(raw_pf.get("accounts") or [])
        inst_list = list(raw_pf.get("instruments") or [])
        instruments = {
            str(item.get("id") or "").upper(): dict(item)
            for item in inst_list
            if isinstance(item, dict) and item.get("id")
        }
        spots_raw = list(raw_pf.get("spot_balances") or [])
        derivs_raw = list(raw_pf.get("derivative_positions") or [])
        rules_raw = list(raw_pf.get("curation_rules") or [])
        prefs_raw = dict((prefs_resp or {}).get("preferences") or ws.get("preferences") or {})
        stored_settings = raw_pf.get("stored_settings") if isinstance(raw_pf.get("stored_settings"), dict) else {}
        for sk in _MANAGED_SETTING_KEYS:
            if sk in stored_settings:
                prefs_raw[sk] = stored_settings[sk]
        raw_h_ts = str(raw_pf.get("holdings_updated_at") or "").strip()
        raw_p_ts = str(raw_pf.get("prefs_updated_at") or "").strip()
        last_cs = raw_pf.get("last_change_set") if isinstance(raw_pf.get("last_change_set"), dict) else {}
    else:
        accounts_raw = list(ws.get("accounts") or [])
        instruments = {}
        spots_raw = list(ws.get("spot_balances") or [])
        derivs_raw = list(ws.get("derivative_positions") or [])
        rules_raw = list((prefs_resp or {}).get("curation_rules") or ws.get("curation_rules") or [])
        prefs_raw = (prefs_resp or {}).get("preferences") or ws.get("preferences") or {}
        raw_h_ts = str(ws.get("portfolio_updated_at") or "").strip()
        raw_p_ts = str(ws.get("portfolio_updated_at") or "").strip()
        last_cs = {}

    remote_spot_ids: dict[tuple[str, str], str] = {}
    used_remote_spot_ids: set[str] = set()
    for s in spots_raw:
        if isinstance(s, dict):
            key = (str(s.get("account_id") or "acct_default"), str(s.get("instrument_id") or s.get("symbol") or "").strip().upper())
            if s.get("id"):
                sid = str(s["id"])
                remote_spot_ids[key] = sid
                used_remote_spot_ids.add(sid)

    model = {
        "accounts": _normalize_accounts(accounts_raw),
        "spots": _normalize_spots(spots_raw, instruments),
        "derivatives": _normalize_derivatives(derivs_raw, instruments),
        "rules": _normalize_rules(rules_raw),
        "settings": _normalize_settings(prefs_raw),
    }
    holdings_ts: list[tuple[float, str]] = []
    prefs_ts: list[tuple[float, str]] = []
    if raw_h_ts:
        ep = _parse_ts_epoch(raw_h_ts)
        if ep > 0:
            holdings_ts.append((ep, raw_h_ts))
    if raw_p_ts:
        ep = _parse_ts_epoch(raw_p_ts)
        if ep > 0:
            prefs_ts.append((ep, raw_p_ts))
    raw_ws_ts = str(ws.get("portfolio_updated_at") or "").strip()
    if raw_ws_ts and not raw_h_ts and not raw_p_ts:
        ep = _parse_ts_epoch(raw_ws_ts)
        if ep > 0:
            holdings_ts.append((ep, raw_ws_ts))
            prefs_ts.append((ep, raw_ws_ts))
    for rows in (accounts_raw, spots_raw, derivs_raw):
        for r in rows:
            if isinstance(r, dict):
                ep = _parse_ts_epoch(r.get("updated_at"))
                if ep > 0:
                    holdings_ts.append((ep, str(r["updated_at"])))
    for r in rules_raw:
        if isinstance(r, dict):
            ep = _parse_ts_epoch(r.get("updated_at"))
            if ep > 0:
                prefs_ts.append((ep, str(r["updated_at"])))
    h_ep, h_ts = max(holdings_ts, key=lambda x: x[0]) if holdings_ts else (0.0, "")
    p_ep, p_ts = max(prefs_ts, key=lambda x: x[0]) if prefs_ts else (0.0, "")
    all_ts = [*holdings_ts, *prefs_ts]
    max_ep, max_ts = max(all_ts, key=lambda x: x[0]) if all_ts else (0.0, "")
    return {
        "revision": int(ws.get("revision") or 0),
        "model": model,
        "digest": _canonical_portfolio_digest(model),
        "holdings_digest": _canonical_portfolio_digest(_holdings_submodel(model)),
        "prefs_digest": _canonical_portfolio_digest(_prefs_submodel(model)),
        "holdings_updated_epoch": h_ep,
        "holdings_updated_at": h_ts,
        "prefs_updated_epoch": p_ep,
        "prefs_updated_at": p_ts,
        "max_updated_epoch": max_ep,
        "max_updated_at": max_ts,
        "remote_spot_ids": remote_spot_ids,
        "used_remote_spot_ids": used_remote_spot_ids,
        "last_change_set": dict(last_cs),
    }


def _build_portfolio_sync_ops(
    local_model: dict[str, Any],
    remote_model: dict[str, Any],
    *,
    setting_keys: list[str],
    instrument_meta: dict[str, dict[str, Any]] | None = None,
    remote_spot_ids: dict[tuple[str, str], str] | None = None,
    used_remote_spot_ids: set[str] | None = None,
    include_holdings: bool = True,
    include_prefs: bool = True,
) -> list[dict[str, Any]]:
    ops: list[dict[str, Any]] = []
    inst_meta = instrument_meta or {}
    rm_spot_ids = remote_spot_ids or {}
    active_spot_ids = set(used_remote_spot_ids or ())

    if include_holdings:
        local_accounts = {a["id"]: a for a in local_model["accounts"]}
        remote_accounts = {a["id"]: a for a in remote_model["accounts"]}
        local_spots = {(s["account_id"], s["instrument_id"]): s for s in local_model["spots"]}
        remote_spots = {(s["account_id"], s["instrument_id"]): s for s in remote_model["spots"]}
        local_derivs = {d["id"]: d for d in local_model["derivatives"]}
        remote_derivs = {d["id"]: d for d in remote_model["derivatives"]}

        # Emit removals first so renamed remote spot IDs or closing rows never collide with incoming puts
        for skey in remote_spots:
            if skey not in local_spots:
                target_id = rm_spot_ids.get(skey) or f"spot:{skey[0]}:{skey[1]}"
                active_spot_ids.discard(target_id)
                ops.append({"op": "remove", "entity": "spot", "id": target_id})

        for did in remote_derivs:
            if did not in local_derivs:
                ops.append({"op": "remove", "entity": "derivative", "id": did})

        for aid, acc in local_accounts.items():
            if remote_accounts.get(aid) != acc:
                ops.append({"op": "put", "entity": "account", "id": aid, "data": dict(acc)})

        for skey, sp in local_spots.items():
            if remote_spots.get(skey) != sp:
                target_id = rm_spot_ids.get(skey)
                if not target_id:
                    cand = f"spot:{skey[0]}:{skey[1]}"
                    if cand in active_spot_ids:
                        sfx = hashlib.sha1(f"{skey[0]}:{skey[1]}".encode("utf-8")).hexdigest()[:8]
                        cand = f"spot:{skey[0]}:{skey[1]}:{sfx}"
                        idx = 1
                        while cand in active_spot_ids:
                            cand = f"spot:{skey[0]}:{skey[1]}:{sfx}{idx}"
                            idx += 1
                    target_id = cand
                    active_spot_ids.add(target_id)
                ops.append({"op": "put", "entity": "spot", "id": target_id, "data": {**sp, "source": "local_sync"}})

        for did, dv in local_derivs.items():
            if remote_derivs.get(did) != dv:
                meta = inst_meta.get(dv["instrument_id"], {})
                ops.append({"op": "put", "entity": "derivative", "id": did, "data": {**meta, **dv, "source": "local_sync"}})

        for aid in remote_accounts:
            if aid not in local_accounts and aid != "acct_default":
                ops.append({"op": "remove", "entity": "account", "id": aid})

    if include_prefs:
        local_rules = {r["id"]: r for r in local_model["rules"]}
        remote_rules = {r["id"]: r for r in remote_model["rules"]}
        for rid, rule in local_rules.items():
            if remote_rules.get(rid) != rule:
                ops.append({"op": "put", "entity": "rule", "id": rid, "data": dict(rule)})

        for rid in remote_rules:
            if rid not in local_rules:
                ops.append({"op": "remove", "entity": "rule", "id": rid})

        l_set = local_model["settings"]
        r_set = remote_model["settings"]
        sync_keys = list(dict.fromkeys([*setting_keys, *[k for k in _MANAGED_SETTING_KEYS if l_set.get(k) != r_set.get(k)]]))
        for k in sync_keys:
            if l_set.get(k) != r_set.get(k):
                ops.append({"op": "put", "entity": "setting", "id": k, "data": {"key": k, "value": l_set.get(k)}})

    return ops


def _read_sync_checkpoint(state_file: Path, base_url: str) -> dict[str, Any] | None:
    if not state_file.is_file():
        return None
    try:
        raw = json.loads(state_file.read_text(encoding="utf-8"))
        entry = (raw.get("targets") or {}).get(base_url) if isinstance(raw, dict) else None
        return dict(entry) if isinstance(entry, dict) else None
    except Exception:
        return None


def _write_sync_checkpoint(state_file: Path, base_url: str, entry: dict[str, Any]) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(state_file):
        raw: dict[str, Any] = {}
        if state_file.is_file():
            try:
                loaded = json.loads(state_file.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    raw = loaded
            except Exception:
                raw = {}
        targets = dict(raw.get("targets") or {})
        targets[base_url] = entry
        raw["targets"] = targets
        tmp = state_file.with_name(f".{state_file.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, state_file)


def sync_portfolio_via_http(
    db_path: Path | str,
    *,
    url: str | None = None,
    rw_token: str | None = None,
    force: bool = False,
    timeout: tuple[int, int] = (15, 60),
    state_file: Path | str | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Sync private portfolio holdings/rules to consumer server strictly via PORTFOLIO_RW Bearer token."""
    token = (rw_token if rw_token is not None else (os.environ.get("MACROCOCKPIT_RW_TOKEN") or "")).strip()
    if not token:
        return False, {"portfolio_action": "error", "reason": "missing_rw_token"}

    base_url = _resolve_portfolio_base_url(url)
    url_err = _validate_portfolio_url(base_url)
    if url_err:
        return False, {"portfolio_action": "error", "reason": url_err}

    local_exp = export_local_portfolio_state(db_path)
    local_model = local_exp["model"]
    has_local_holdings = bool(local_model["spots"] or local_model["derivatives"])
    local_counts = {
        "accounts_count": len(local_model["accounts"]),
        "spots_count": len(local_model["spots"]),
        "derivatives_count": len(local_model["derivatives"]),
        "rules_count": len(local_model["rules"]),
    }
    ckpt_path = Path(state_file) if state_file is not None else Path(db_path).with_name("portfolio_sync_state.json")
    ckpt = _read_sync_checkpoint(ckpt_path, base_url)
    had_prev_holdings = bool(ckpt and ckpt.get("had_local_holdings"))
    if not has_local_holdings and not had_prev_holdings and not force:
        return True, {"portfolio_action": "skipped_empty_local", **local_counts}

    headers = {"Authorization": f"Bearer {token}"}
    try:
        ws_resp = requests.get(f"{base_url}/api/v2/workspace", headers=headers, timeout=timeout, allow_redirects=False)
        if 300 <= ws_resp.status_code < 400:
            return False, {"portfolio_action": "error", "reason": f"redirect_blocked_{ws_resp.status_code}"}
        ws_resp.raise_for_status()
        ws_json = ws_resp.json()

        prefs_resp = requests.get(f"{base_url}/api/v2/preferences", headers=headers, timeout=timeout, allow_redirects=False)
        if 300 <= prefs_resp.status_code < 400:
            return False, {"portfolio_action": "error", "reason": f"redirect_blocked_{prefs_resp.status_code}"}
        prefs_resp.raise_for_status()
        prefs_json = prefs_resp.json()
    except requests.HTTPError as exc:
        code = getattr(exc.response, "status_code", None)
        return False, {"portfolio_action": "error", "reason": f"HTTP_{code}" if code else type(exc).__name__}
    except requests.RequestException as exc:
        return False, {"portfolio_action": "error", "reason": type(exc).__name__}

    remote = _extract_remote_portfolio_state(ws_json, prefs_json)
    target_tag = hashlib.sha256(
        f"{local_exp['holdings_digest']}:{local_exp['prefs_digest']}".encode("utf-8")
    ).hexdigest()[:16]

    if remote["digest"] == local_exp["digest"]:
        _write_sync_checkpoint(
            ckpt_path,
            base_url,
            {
                "synced_remote_revision": remote["revision"],
                "synced_local_digest": local_exp["digest"],
                "synced_local_epoch": local_exp["max_updated_epoch"],
                "synced_holdings_digest": local_exp["holdings_digest"],
                "synced_holdings_epoch": local_exp["holdings_updated_epoch"],
                "synced_prefs_digest": local_exp["prefs_digest"],
                "synced_prefs_epoch": local_exp["prefs_updated_epoch"],
                "had_local_holdings": has_local_holdings,
                "in_progress": None,
            },
        )
        return True, {"portfolio_action": "noop_in_sync", "revision": remote["revision"], **local_counts}

    last_cs = remote.get("last_change_set") or {}
    last_req_id = str(last_cs.get("request_id") or "")
    in_prog = (ckpt or {}).get("in_progress") if isinstance((ckpt or {}).get("in_progress"), dict) else {}
    resuming_self = False
    if last_req_id.startswith(f"pf-sync-{target_tag}-"):
        resuming_self = True
    elif ckpt is not None and in_prog.get("target_tag") == target_tag:
        exp_revs = {int(in_prog.get("sent_expected_rev") or -1), int(in_prog.get("expected_new_rev") or -1)}
        if remote["revision"] in exp_revs and (last_req_id.startswith("pf-sync-") or not last_req_id):
            resuming_self = True

    def _domain_should_sync(
        *,
        local_dig: str,
        remote_dig: str,
        ckpt_dig_key: str,
        local_ep: float,
        remote_ep: float,
        check_rev_on_initial: bool,
        domain_resume_flag: str,
    ) -> tuple[bool, bool]:
        """Return (should_sync, skipped_remote_newer) independently for holdings vs preferences."""
        if local_dig == remote_dig:
            return (False, False)
        domain_resuming = resuming_self and bool(in_prog.get(domain_resume_flag, True))
        if force or domain_resuming:
            return (True, False)
        if ckpt is not None and ckpt.get(ckpt_dig_key):
            synced_dig = str(ckpt.get(ckpt_dig_key) or "")
            local_changed = local_dig != synced_dig
            remote_changed = remote_dig != synced_dig
            if remote_changed and (not local_changed or remote_ep >= local_ep):
                return (False, True)
            return (True, False)
        if ckpt is not None and ckpt.get("synced_local_digest") and not ckpt.get("synced_holdings_digest"):
            # Upgrade path from pre-split checkpoint
            remote_changed = remote["revision"] != int(ckpt.get("synced_remote_revision") or -1)
            local_changed = local_exp["digest"] != ckpt.get("synced_local_digest")
            if remote_changed and (not local_changed or remote_ep >= local_ep):
                return (False, True)
            return (True, False)
        if ((check_rev_on_initial and remote["revision"] > 0) or remote_ep > 0.0) and remote_ep >= local_ep:
            return (False, True)
        return (True, False)

    sync_holdings, holdings_remote_newer = _domain_should_sync(
        local_dig=local_exp["holdings_digest"],
        remote_dig=remote["holdings_digest"],
        ckpt_dig_key="synced_holdings_digest",
        local_ep=local_exp["holdings_updated_epoch"],
        remote_ep=remote["holdings_updated_epoch"],
        check_rev_on_initial=True,
        domain_resume_flag="sync_holdings",
    )
    sync_prefs, prefs_remote_newer = _domain_should_sync(
        local_dig=local_exp["prefs_digest"],
        remote_dig=remote["prefs_digest"],
        ckpt_dig_key="synced_prefs_digest",
        local_ep=local_exp["prefs_updated_epoch"],
        remote_ep=remote["prefs_updated_epoch"],
        check_rev_on_initial=bool(remote["model"]["rules"] or remote["prefs_updated_epoch"] > 0.0),
        domain_resume_flag="sync_prefs",
    )

    if not sync_holdings and not sync_prefs:
        if holdings_remote_newer or prefs_remote_newer:
            return True, {
                "portfolio_action": "skipped_remote_newer",
                "revision": remote["revision"],
                **local_counts,
            }

    ops = _build_portfolio_sync_ops(
        local_model,
        remote["model"],
        setting_keys=local_exp["stored_settings_keys"],
        instrument_meta=local_exp.get("instrument_meta"),
        remote_spot_ids=remote.get("remote_spot_ids"),
        used_remote_spot_ids=remote.get("used_remote_spot_ids"),
        include_holdings=sync_holdings,
        include_prefs=sync_prefs,
    )
    if not ops:
        if sync_holdings or sync_prefs:
            _write_sync_checkpoint(
                ckpt_path,
                base_url,
                {
                    "synced_remote_revision": remote["revision"],
                    "synced_local_digest": local_exp["digest"] if (sync_holdings and sync_prefs) else str((ckpt or {}).get("synced_local_digest") or ""),
                    "synced_local_epoch": local_exp["max_updated_epoch"],
                    "synced_holdings_digest": (
                        local_exp["holdings_digest"]
                        if (sync_holdings or local_exp["holdings_digest"] == remote["holdings_digest"])
                        else str((ckpt or {}).get("synced_holdings_digest") or "")
                    ),
                    "synced_holdings_epoch": (
                        local_exp["holdings_updated_epoch"]
                        if (sync_holdings or local_exp["holdings_digest"] == remote["holdings_digest"])
                        else float((ckpt or {}).get("synced_holdings_epoch") or 0.0)
                    ),
                    "synced_prefs_digest": (
                        local_exp["prefs_digest"]
                        if (sync_prefs or local_exp["prefs_digest"] == remote["prefs_digest"])
                        else str((ckpt or {}).get("synced_prefs_digest") or "")
                    ),
                    "synced_prefs_epoch": (
                        local_exp["prefs_updated_epoch"]
                        if (sync_prefs or local_exp["prefs_digest"] == remote["prefs_digest"])
                        else float((ckpt or {}).get("synced_prefs_epoch") or 0.0)
                    ),
                    "had_local_holdings": has_local_holdings if sync_holdings else had_prev_holdings,
                    "in_progress": None,
                },
            )
        return True, {"portfolio_action": "noop_in_sync", "revision": remote["revision"], **local_counts}

    cur_rev = remote["revision"]
    applied_total = 0
    prev_h_dig = str((ckpt or {}).get("synced_holdings_digest") or "")
    prev_h_ep = float((ckpt or {}).get("synced_holdings_epoch") or 0.0)
    prev_p_dig = str((ckpt or {}).get("synced_prefs_digest") or "")
    prev_p_ep = float((ckpt or {}).get("synced_prefs_epoch") or 0.0)
    for idx in range(0, len(ops), 80):
        chunk = ops[idx : idx + 80]
        req_id = f"pf-sync-{target_tag}-{cur_rev}-{idx}"
        # Record in-progress intent BEFORE HTTP POST so a dropped response still identifies self-commits on retry
        _write_sync_checkpoint(
            ckpt_path,
            base_url,
            {
                "synced_remote_revision": cur_rev,
                "synced_local_digest": str((ckpt or {}).get("synced_local_digest") or ""),
                "synced_local_epoch": float((ckpt or {}).get("synced_local_epoch") or 0.0),
                "synced_holdings_digest": prev_h_dig,
                "synced_holdings_epoch": prev_h_ep,
                "synced_prefs_digest": prev_p_dig,
                "synced_prefs_epoch": prev_p_ep,
                "had_local_holdings": had_prev_holdings or has_local_holdings,
                "in_progress": {
                    "target_tag": target_tag,
                    "sent_expected_rev": cur_rev,
                    "expected_new_rev": cur_rev + 1,
                    "request_id": req_id,
                    "sync_holdings": sync_holdings,
                    "sync_prefs": sync_prefs,
                },
            },
        )
        try:
            chg_resp = requests.post(
                f"{base_url}/api/v2/changes",
                json={
                    "request_id": req_id,
                    "expected_revision": cur_rev,
                    "summary": "local_portfolio_sync",
                    "ops": chunk,
                },
                headers=headers,
                timeout=timeout,
                allow_redirects=False,
            )
            if 300 <= chg_resp.status_code < 400:
                return False, {"portfolio_action": "error", "reason": f"redirect_blocked_{chg_resp.status_code}"}
            chg_resp.raise_for_status()
            chg_data = chg_resp.json()
            cur_rev = int(chg_data.get("revision") or cur_rev + 1)
            applied_total += int(chg_data.get("applied_count") or len(chunk))
            is_last = (idx + 80) >= len(ops)
            _write_sync_checkpoint(
                ckpt_path,
                base_url,
                {
                    "synced_remote_revision": cur_rev,
                    "synced_local_digest": (
                        local_exp["digest"]
                        if (is_last and not holdings_remote_newer and not prefs_remote_newer)
                        else str((ckpt or {}).get("synced_local_digest") or "")
                    ),
                    "synced_local_epoch": local_exp["max_updated_epoch"],
                    "synced_holdings_digest": (
                        local_exp["holdings_digest"]
                        if (is_last and (sync_holdings or local_exp["holdings_digest"] == remote["holdings_digest"]))
                        else prev_h_dig
                    ),
                    "synced_holdings_epoch": (
                        local_exp["holdings_updated_epoch"]
                        if (is_last and (sync_holdings or local_exp["holdings_digest"] == remote["holdings_digest"]))
                        else prev_h_ep
                    ),
                    "synced_prefs_digest": (
                        local_exp["prefs_digest"]
                        if (is_last and (sync_prefs or local_exp["prefs_digest"] == remote["prefs_digest"]))
                        else prev_p_dig
                    ),
                    "synced_prefs_epoch": (
                        local_exp["prefs_updated_epoch"]
                        if (is_last and (sync_prefs or local_exp["prefs_digest"] == remote["prefs_digest"]))
                        else prev_p_ep
                    ),
                    "had_local_holdings": has_local_holdings if (is_last and sync_holdings) else had_prev_holdings,
                    "in_progress": (
                        None
                        if is_last
                        else {
                            "target_tag": target_tag,
                            "sent_expected_rev": cur_rev,
                            "expected_new_rev": cur_rev + 1,
                            "request_id": req_id,
                            "sync_holdings": sync_holdings,
                            "sync_prefs": sync_prefs,
                        }
                    ),
                },
            )
        except requests.HTTPError as exc:
            code = getattr(exc.response, "status_code", None)
            return False, {"portfolio_action": "error", "reason": f"HTTP_{code}" if code else type(exc).__name__}
        except requests.RequestException as exc:
            return False, {"portfolio_action": "error", "reason": type(exc).__name__}

    return True, {
        "portfolio_action": "synced",
        "revision": cur_rev,
        "ops_applied": applied_total,
        "holdings_synced": sync_holdings,
        "prefs_synced": sync_prefs,
        **local_counts,
    }


def _public_payload(payload: dict[str, Any]) -> dict[str, Any]:
    state = payload.get("state") or {}
    articles = payload.get("articles") or []
    blocked = private_article_filenames(articles)
    blocked.update(private_article_filenames(state.get("articles") or []))
    pub_state = project_public_state(state, include_candidates=True, excluded_article_names=blocked)
    pub_state.pop("event_cards", None)
    pub_state.pop("event_stats", None)
    return {
        **payload,
        "state": pub_state,
        "articles": [
            a for a in articles if not has_private_article_content(a)
            and (not isinstance(a, dict) or safe_article_filename(a.get("filename")) not in blocked)
        ],
    }


def publish_via_http(
    payload: dict[str, Any],
    *,
    url: str | None = None,
    sync_token: str | None = None,
    timeout: tuple[int, int] = (20, 120),
) -> tuple[bool, dict[str, Any]]:
    """Publish sanitized public snapshot via POST /api/v2/publish, falling back to /api/sync ONLY on HTTP 404."""
    token = (sync_token if sync_token is not None else (os.environ.get("MACROCOCKPIT_SYNC_TOKEN") or "")).strip()
    if not token:
        return False, {"reason": "missing_sync_token"}

    sanitized_payload = _public_payload(payload)
    primary_url, legacy_url = _resolve_publish_endpoints(url)
    headers = {"Authorization": f"Bearer {token}"}

    try:
        resp = requests.post(primary_url, json=sanitized_payload, headers=headers, timeout=timeout)
        if resp.status_code == 404 and legacy_url:
            resp_legacy = requests.post(legacy_url, json=sanitized_payload, headers=headers, timeout=timeout)
            resp_legacy.raise_for_status()
            return True, {"action": "http_sync_legacy", "endpoint": legacy_url, "status_code": resp_legacy.status_code}
        resp.raise_for_status()
        return True, {"action": "http_publish", "endpoint": primary_url, "status_code": resp.status_code}
    except requests.HTTPError as exc:
        code = getattr(exc.response, "status_code", None)
        return False, {"reason": f"HTTP_{code}" if code else type(exc).__name__}
    except requests.ConnectionError as exc:
        can_fallback = not isinstance(exc, (requests.exceptions.SSLError, requests.exceptions.ProxyError))
        return False, {"reason": type(exc).__name__, "connection_failed": can_fallback}
    except requests.RequestException as exc:
        return False, {"reason": type(exc).__name__}


def _ssh_push(payload: dict[str, Any], host: str, remote_base: str = "/opt/macro-intel") -> dict[str, Any]:
    """Push sanitized snapshot over SSH and call remote publish_generation() atomically."""
    safe_payload = _public_payload(payload)
    compressed = gzip.compress(json.dumps(safe_payload, ensure_ascii=False).encode("utf-8"))
    remote_script = (
        "import gzip,json,sys\n"
        "from pathlib import Path\n"
        f"base=Path({remote_base!r})\n"
        "sys.path.insert(0, str(base))\n"
        "from backend.public_snapshot import publish_generation\n"
        "data=json.loads(gzip.decompress(sys.stdin.buffer.read()).decode('utf-8'))\n"
        "res=publish_generation(\n"
        "    base,\n"
        "    data['state'],\n"
        "    data.get('articles', []),\n"
        "    producer_id=str(data.get('producer_id') or 'windows-primary'),\n"
        "    generation=data.get('generation'),\n"
        ")\n"
        "print(json.dumps({'ok': True, 'generation': res.get('generation')}, ensure_ascii=False))\n"
    )
    b64 = base64.b64encode(remote_script.encode("utf-8")).decode("ascii")
    proc = subprocess.run(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=20",
            host,
            f"python3 -c \"import base64;exec(base64.b64decode('{b64}'))\"",
        ],
        input=compressed,
        capture_output=True,
        timeout=90,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ssh sync failed ({proc.returncode}): {proc.stderr.decode('utf-8', errors='replace')[:300]}")
    try:
        return json.loads(proc.stdout.decode("utf-8", errors="replace").strip() or "{}")
    except ValueError:
        return {"ok": True}


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Sync MacroCockpit data to VPS")
    parser.add_argument("--refresh-state", action="store_true")
    parser.add_argument("--refresh-fred", action="store_true")
    parser.add_argument("--url")
    parser.add_argument("--portfolio-url", default=None)
    parser.add_argument("--ssh-host", default="tencent-vps")
    parser.add_argument("--via-ssh", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-articles", type=int, default=40)
    parser.add_argument("--no-ssh-fallback", action="store_true")
    parser.add_argument("--generation", type=int, default=None)
    parser.add_argument("--producer-id", default="windows-primary")
    parser.add_argument("--sync-portfolio", action="store_true")
    parser.add_argument("--force-portfolio", action="store_true")
    parser.add_argument("--journal-db", type=Path, default=None)
    args = parser.parse_args()

    if not args.dry_run:
        load_push_env(include_sync_token=True, include_rw_token=bool(args.sync_portfolio))
    data_dir = resolve_data_dir()
    state_path = data_dir / "cockpit_state.json"
    journal_db = args.journal_db or (data_dir / "user_journal.db")
    if args.refresh_state:
        from backend.signal_aggregator import generate_cockpit_state

        generate_cockpit_state(refresh_fred=args.refresh_fred, output_path=state_path)

    payload, stats = collect_payload(
        state_path,
        args.max_articles,
        generation=args.generation,
        producer_id=args.producer_id,
    )
    if args.dry_run:
        out_dry = {"action": "dry_run", **stats}
        if args.sync_portfolio:
            exp = export_local_portfolio_state(journal_db)
            out_dry["portfolio_preview"] = {
                "accounts_count": len(exp["model"]["accounts"]),
                "spots_count": len(exp["model"]["spots"]),
                "derivatives_count": len(exp["model"]["derivatives"]),
                "rules_count": len(exp["model"]["rules"]),
            }
        print(json.dumps(out_dry, ensure_ascii=False))
        return

    http_error = "forced_ssh"
    can_fallback = args.via_ssh
    pub_out: dict[str, Any] | None = None
    if not args.via_ssh:
        ok, info = publish_via_http(payload, url=args.url)
        if ok:
            pub_out = {**info, **stats}
        else:
            http_error = str(info.get("reason") or "http_failed")
            can_fallback = info.get("connection_failed") is True

    if pub_out is None:
        if args.no_ssh_fallback or not can_fallback:
            print(json.dumps({"action": "http_failed", "reason": http_error, **stats}, ensure_ascii=False))
            raise SystemExit(1)

        remote_res = _ssh_push(payload, args.ssh_host)
        pub_out = {
            "action": "ssh_sync",
            "ssh_host": args.ssh_host,
            "http_result": http_error,
            "generation": remote_res.get("generation"),
            **stats,
        }

    if args.sync_portfolio:
        pf_ok, pf_info = sync_portfolio_via_http(
            journal_db,
            url=args.portfolio_url,
            force=bool(args.force_portfolio),
        )
        pub_out.update(pf_info)
        print(json.dumps(pub_out, ensure_ascii=False))
        if not pf_ok:
            raise SystemExit(1)
        return

    print(json.dumps(pub_out, ensure_ascii=False))


if __name__ == "__main__":
    main()
