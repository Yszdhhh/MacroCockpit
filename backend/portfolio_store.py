"""Transactional SQLite store for portfolio holdings, auth tokens, audit logs, and workspace snapshots."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from backend.curation import _check_safe_regex, rank_curated_signals, validate_curation_rule
from backend.journal_store import JournalStore, RevisionConflictError
from backend.market_quotes import KNOWN_SWAP_MULTIPLIERS, fetch_crypto_quotes, fetch_fx_snapshot
from backend.valuation import (
    CORE_SLEEVE_IDS,
    _derivative_quote_currency,
    decimal_str,
    evaluate_portfolio,
    parse_decimal_str,
    project_valuation_currency,
    to_decimal,
)

_BJT = timezone(timedelta(hours=8))

DEFAULT_ACCOUNT_ID = "acct_default"
VALID_ROLES = frozenset({"READ_ONLY", "PORTFOLIO_RW", "SYNC_PUBLISH"})
VALID_ENTITIES = frozenset(
    {"account", "spot", "derivative", "rule", "setting", "review", "essay", "article_status"}
)
VALID_CURATION_KINDS = frozenset({"instrument", "sector", "keyword", "regex", "topic", "source"})
VALID_CURATION_ACTIONS = frozenset({"watch", "block", "mute", "adjust"})
_FORBIDDEN_AUDIT_KEYS = frozenset(
    {
        "token",
        "access_token",
        "read_token",
        "rw_token",
        "sync_token",
        "secret",
        "password",
        "authorization",
        "cookie",
        "amount",
        "qty",
        "price",
        "entry_price",
        "mark_price",
        "margin",
        "margin_usd",
        "margin_amount",
        "upl",
        "equity",
        "net_equity",
        "notional",
        "debt",
        "confirmed_debt_usd",
        "raw_text",
        "body",
        "text",
        "csv",
    }
)
_MAX_RULES = 100


class IdempotencyConflictError(RevisionConflictError):
    """Raised when the same (actor_id, request_id) is reused with a different request payload (HTTP 409)."""


class ConstraintViolationError(ValueError):
    """Raised when a delete or write violates relational integrity (e.g., ON DELETE RESTRICT)."""


class PreviewExpiredError(ValueError):
    """An import preview expired before the write transaction could commit it."""


def _now_iso() -> str:
    return datetime.now(_BJT).isoformat(timespec="seconds")


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _salted_equal(candidate: str, expected: str) -> bool:
    if not candidate or not expected:
        return False
    salt = os.urandom(16)
    d1 = hashlib.sha256(salt + candidate.encode("utf-8")).digest()
    d2 = hashlib.sha256(salt + expected.encode("utf-8")).digest()
    return hmac.compare_digest(d1, d2)


def _hash_token_pbkdf2(token: str, salt: str) -> str:
    dk = hashlib.pbkdf2_hmac("sha256", token.encode("utf-8"), salt.encode("utf-8"), 10_000)
    return f"pbkdf2_sha256$10000${dk.hex()}"


def _verify_stored_token(token: str, salt: str, stored_hash: str) -> bool:
    if stored_hash.startswith("pbkdf2_sha256$"):
        parts = stored_hash.split("$", 2)
        if len(parts) != 3:
            return False
        try:
            iters = int(parts[1])
        except ValueError:
            return False
        if iters < 1 or iters > 1_000_000:
            return False
        dk = hashlib.pbkdf2_hmac("sha256", token.encode("utf-8"), salt.encode("utf-8"), iters).hex()
        return hmac.compare_digest(dk.encode("ascii"), parts[2].encode("ascii"))

    cand1 = hashlib.sha256((salt + token).encode("utf-8")).hexdigest()
    cand2 = hashlib.sha256((f"{salt}:{token}").encode("utf-8")).hexdigest()
    cand3 = hmac.new(salt.encode("utf-8"), token.encode("utf-8"), hashlib.sha256).hexdigest()
    return any(
        hmac.compare_digest(c.encode("ascii"), stored_hash.encode("ascii"))
        for c in (cand1, cand2, cand3)
    )


def _validate_safe_regex(pattern: str) -> None:
    _check_safe_regex(pattern)


def _sanitize_audit_detail(detail: dict[str, Any] | None) -> dict[str, Any]:
    if not detail or not isinstance(detail, dict):
        return {}
    clean: dict[str, Any] = {}
    for k, v in detail.items():
        kl = str(k).strip().lower()
        if any(bad in kl for bad in _FORBIDDEN_AUDIT_KEYS):
            continue
        if isinstance(v, (int, bool)) or v is None:
            clean[str(k)] = v
        elif isinstance(v, float):
            continue
        elif isinstance(v, str):
            if "bearer " in v.lower() or len(v) > 120:
                clean[str(k)] = "[redacted]"
            else:
                clean[str(k)] = v
        elif isinstance(v, list):
            clean[str(k)] = [
                str(x)[:64] for x in v[:20] if isinstance(x, (str, int)) and "bearer " not in str(x).lower()
            ]
        elif isinstance(v, dict):
            clean[str(k)] = _sanitize_audit_detail(v)
    return clean


def _validate_rule_fields(kind: str, target: str, action: str, points: Any) -> str:
    if kind not in VALID_CURATION_KINDS:
        raise ValueError(f"invalid curation rule kind: {kind!r}")
    if action not in VALID_CURATION_ACTIONS:
        raise ValueError(f"invalid curation rule action: {action!r}")
    tgt = str(target or "").strip()
    if not tgt or len(tgt) > 120:
        raise ValueError("curation rule target must be 1..120 characters")
    pts_str = parse_decimal_str(points if points not in (None, "") else "0")
    pts_dec = Decimal(pts_str)
    if action == "adjust":
        if pts_dec < Decimal("-12") or pts_dec > Decimal("3"):
            raise ValueError("adjust rule points must be within [-12, 3]")
    elif action == "mute":
        if pts_dec < Decimal("0") or pts_dec > Decimal("15"):
            raise ValueError("mute rule points must be within [0, 15]")
    else:
        if pts_dec < Decimal("-12") or pts_dec > Decimal("15"):
            raise ValueError("rule points out of range [-12, 15]")

    validate_curation_rule(kind, tgt, action, float(pts_dec))
    return pts_str


def _extract_public_quotes(public_state: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    quotes: dict[str, dict[str, Any]] = {}
    if not isinstance(public_state, dict):
        return quotes

    for item in public_state.get("macro_fred") or []:
        if not isinstance(item, dict):
            continue
        iid = str(item.get("id") or "")
        raw_v = item.get("raw_value")
        if raw_v is None:
            continue
        try:
            px_str = parse_decimal_str(str(raw_v))
        except ValueError:
            continue
        if iid == "btc_spot":
            quotes.setdefault("BTC", {"price": px_str, "quote_currency": "USD", "source": item.get("source", "macro")})
        elif iid == "gold":
            quotes.setdefault("GOLD", {"price": px_str, "quote_currency": "USD", "source": item.get("source", "macro")})
            quotes.setdefault("XAU", {"price": px_str, "quote_currency": "USD", "source": item.get("source", "macro")})
            quotes.setdefault("XAUUSD", {"price": px_str, "quote_currency": "USD", "source": item.get("source", "macro")})

    eq = public_state.get("us_equities") or {}
    for bucket in ("indices", "mag7_and_ai", "sectors"):
        for row in eq.get(bucket) or []:
            if not isinstance(row, dict):
                continue
            sym = str(row.get("symbol") or "").upper()
            px = row.get("price")
            if sym and px is not None:
                try:
                    quotes[sym] = {
                        "price": parse_decimal_str(str(px)),
                        "quote_currency": "USD",
                        "kind": "equity_spot",
                        "source": "us_equities",
                    }
                except ValueError:
                    pass

    for sym, prof in (public_state.get("stock_profiles") or {}).items():
        if not isinstance(prof, dict):
            continue
        px = prof.get("price")
        if px is not None:
            try:
                quotes[str(sym).upper()] = {
                    "price": parse_decimal_str(str(px)),
                    "quote_currency": "USD",
                    "kind": "equity_spot",
                    "source": "stock_profiles",
                }
            except ValueError:
                pass

    cq = public_state.get("crypto_quotes") or {}
    for k, entry in (cq.get("quotes") or {}).items():
        if isinstance(entry, dict):
            quotes[str(k).upper()] = dict(entry)
    for k, entry in (cq.get("spot") or {}).items():
        if isinstance(entry, dict):
            quotes[str(k).upper()] = dict(entry)
    for k, entry in (cq.get("swap") or {}).items():
        if isinstance(entry, dict):
            quotes[str(k).upper()] = dict(entry)

    return quotes


class PortfolioStore:
    """Private portfolio, credential verification, curation, change_set, and workspace builder."""

    def __init__(self, db_path: Path | str) -> None:
        self.journal = JournalStore(db_path)
        self.db_path = self.journal.db_path
        self._lock = self.journal._lock

    def _connect(self, tx_mode: str = "read"):
        return self.journal._connect(tx_mode)

    @staticmethod
    def _find_account_in_tx(db: sqlite3.Connection, account: str) -> sqlite3.Row | None:
        if not account:
            return None
        exact = db.execute("SELECT id,name FROM account WHERE id=?", (account,)).fetchone()
        if exact is not None:
            return exact
        matches = db.execute("SELECT id,name FROM account WHERE name=? LIMIT 2", (account,)).fetchall()
        if len(matches) > 1:
            raise ValueError(f"ambiguous account name: {account!r}")
        return matches[0] if matches else None

    @staticmethod
    def _resolve_account_id_in_tx(db: sqlite3.Connection, raw_account_id: Any, *, ensure_default: bool = True) -> str:
        aid = str(raw_account_id or "").strip()
        row = PortfolioStore._find_account_in_tx(db, aid)
        if row is not None:
            return str(row["id"])
        if not aid or aid.lower() in ("main", "default") or aid in ("主账户", DEFAULT_ACCOUNT_ID):
            if ensure_default:
                PortfolioStore._ensure_default_account_in_tx(db)
            return DEFAULT_ACCOUNT_ID
        raise ValueError(f"unknown account_id: {raw_account_id!r}")

    @staticmethod
    def _ensure_default_account_in_tx(db: sqlite3.Connection) -> dict[str, Any]:
        row = db.execute("SELECT * FROM account WHERE id=?", (DEFAULT_ACCOUNT_ID,)).fetchone()
        if row is not None:
            return dict(row)
        any_row = db.execute("SELECT * FROM account ORDER BY created_at ASC LIMIT 1").fetchone()
        if any_row is not None:
            return dict(any_row)
        now = _now_iso()
        db.execute(
            """INSERT INTO account
               (id,name,kind,venue,base_currency,equity_mode,confirmed_debt_usd,notes,archived,rev,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                DEFAULT_ACCOUNT_ID,
                "主账户",
                "general",
                "Manual",
                "USD",
                "total_ex_upl",
                "0",
                "",
                0,
                1,
                now,
                now,
            ),
        )
        return dict(db.execute("SELECT * FROM account WHERE id=?", (DEFAULT_ACCOUNT_ID,)).fetchone())

    def ensure_default_account(self) -> dict[str, Any]:
        with self._lock, self._connect("immediate") as db:
            return self._ensure_default_account_in_tx(db)

    @staticmethod
    def _ensure_instrument_in_tx(
        db: sqlite3.Connection,
        instrument_id: str,
        *,
        symbol: str | None = None,
        name: str = "",
        asset_class: str = "other",
        quote_currency: str = "USD",
        base_asset: str = "",
        settlement_currency: str = "USD",
        contract_type: str = "spot",
        multiplier: str = "1",
        lot_unit: str = "unit",
        exchange: str = "",
        sector: str = "",
        sleeve: str = "",
    ) -> dict[str, Any]:
        iid = str(instrument_id or "").strip().upper()
        if not iid:
            raise ValueError("instrument_id is required")
        existing = db.execute("SELECT * FROM instrument WHERE id=?", (iid,)).fetchone()
        if existing is not None:
            return dict(existing)

        sym = (symbol or iid).strip().upper()
        spec = KNOWN_SWAP_MULTIPLIERS.get(iid)
        if spec and asset_class == "other":
            asset_class = "perp"
            contract_type = spec["ctType"]
            multiplier = spec["ctVal"]
            quote_currency = spec["quote"]
            settlement_currency = spec["settle"]
            base_asset = spec["base"]
            lot_unit = "contract"
        elif iid in ("USD", "USDT", "USDC", "CNY", "CNH") and asset_class == "other":
            asset_class = "cash"
            contract_type = "cash"
            quote_currency = iid if iid in ("USD", "CNY", "CNH") else "USD"
            settlement_currency = quote_currency

        mult_str = parse_decimal_str(multiplier or "1")
        now = _now_iso()
        db.execute(
            """INSERT INTO instrument
               (id,symbol,name,asset_class,quote_currency,base_asset,settlement_currency,
                contract_type,multiplier,lot_unit,exchange,sector,sleeve,metadata_json,rev,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                iid,
                sym,
                str(name or sym),
                asset_class,
                str(quote_currency or "USD").upper(),
                str(base_asset or sym).upper(),
                str(settlement_currency or "USD").upper(),
                contract_type,
                mult_str,
                lot_unit,
                str(exchange or ""),
                str(sector or ""),
                str(sleeve or ""),
                "{}",
                1,
                now,
                now,
            ),
        )
        return dict(db.execute("SELECT * FROM instrument WHERE id=?", (iid,)).fetchone())

    def create_credential(
        self,
        *,
        actor_id: str,
        role: str,
        token: str,
        label: str = "",
        cred_id: str | None = None,
        expires_at: str | None = None,
    ) -> dict[str, Any]:
        if role not in VALID_ROLES:
            raise ValueError(f"invalid role: {role!r}")
        tok = (token or "").strip()
        if not tok:
            raise ValueError("token cannot be empty")
        cid = cred_id or f"cred_{uuid.uuid4().hex[:12]}"
        salt = os.urandom(16).hex()
        tok_hash = _hash_token_pbkdf2(tok, salt)
        now = _now_iso()
        with self._lock, self._connect("immediate") as db:
            db.execute(
                """INSERT INTO credential
                   (id,actor_id,role,salt,token_hash,label,enabled,expires_at,last_used_at,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     actor_id=excluded.actor_id,role=excluded.role,salt=excluded.salt,
                     token_hash=excluded.token_hash,label=excluded.label,enabled=1,
                     expires_at=excluded.expires_at,updated_at=excluded.updated_at""",
                (cid, str(actor_id), role, salt, tok_hash, label, 1, expires_at, None, now, now),
            )
        return {"id": cid, "actor_id": actor_id, "role": role, "label": label, "enabled": True}

    def verify_token(self, token: str | None) -> dict[str, Any] | None:
        """Verify token against salted credential DB rows and env tokens without logging secrets."""
        cand = (token or "").strip()
        if not cand:
            return None

        now_iso = _now_iso()
        matched_cred: dict[str, Any] | None = None
        with self._lock, self._connect("read") as db:
            rows = db.execute(
                "SELECT id,actor_id,role,salt,token_hash,expires_at FROM credential WHERE enabled=1"
            ).fetchall()
            for r in rows:
                exp = r["expires_at"]
                if exp and str(exp) <= now_iso:
                    continue
                if _verify_stored_token(cand, str(r["salt"]), str(r["token_hash"])):
                    matched_cred = {
                        "valid": True,
                        "actor_id": str(r["actor_id"]),
                        "role": str(r["role"]),
                        "credential_id": str(r["id"]),
                        "source": "credential",
                    }
                    break

        if matched_cred is not None:
            try:
                with self._lock, self._connect("immediate") as db:
                    db.execute(
                        "UPDATE credential SET last_used_at=? WHERE id=?",
                        (now_iso, matched_cred["credential_id"]),
                    )
            except Exception:
                pass
            return matched_cred

        env_specs = (
            ("MACROCOCKPIT_RW_TOKEN", "PORTFOLIO_RW", "env:portfolio_rw"),
            ("MACROCOCKPIT_SYNC_TOKEN", "SYNC_PUBLISH", "env:sync_publish"),
            ("MACROCOCKPIT_READ_TOKEN", "READ_ONLY", "env:read_only"),
        )
        for env_key, role, actor in env_specs:
            expected = (os.environ.get(env_key) or "").strip()
            if expected and _salted_equal(cand, expected):
                return {
                    "valid": True,
                    "actor_id": actor,
                    "role": role,
                    "credential_id": env_key,
                    "source": "env",
                }
        return None

    def log_audit_event(
        self,
        *,
        actor_id: str,
        action: str,
        role: str = "",
        resource: str = "",
        outcome: str = "ok",
        request_id: str = "",
        detail: dict[str, Any] | None = None,
    ) -> int:
        """Write an independent short-transaction audit event; strips tokens, amounts, and raw text."""
        with self._lock, self._connect("immediate") as db:
            return self._log_audit_event_in_tx(db, actor_id, action, role, resource, outcome, request_id, detail)

    @staticmethod
    def _log_audit_event_in_tx(
        db: sqlite3.Connection,
        actor_id: str,
        action: str,
        role: str,
        resource: str,
        outcome: str,
        request_id: str,
        detail: dict[str, Any] | None,
    ) -> int:
        safe_detail = _sanitize_audit_detail(detail)
        now = _now_iso()
        cur = db.execute(
            """INSERT INTO audit_event
               (at,actor_id,role,action,resource,outcome,request_id,detail_json)
               VALUES(?,?,?,?,?,?,?,?)""",
            (
                now,
                str(actor_id or "unknown")[:128],
                str(role or "")[:64],
                str(action or "")[:64],
                str(resource or "")[:128],
                str(outcome or "ok")[:32],
                str(request_id or "")[:128],
                json.dumps(safe_detail, ensure_ascii=False),
            ),
        )
        return int(cur.lastrowid)

    def get_revision(self) -> int:
        with self._lock, self._connect("read") as db:
            row = db.execute("SELECT value FROM meta WHERE key='portfolio_revision'").fetchone()
            return int(row["value"]) if row and row["value"] is not None else 0

    def list_curation_rules(self, *, enabled_only: bool = False) -> list[dict[str, Any]]:
        with self._lock, self._connect("read") as db:
            sql = "SELECT * FROM curation_rule"
            if enabled_only:
                sql += " WHERE enabled=1"
            sql += " ORDER BY created_at ASC, id ASC"
            out: list[dict[str, Any]] = []
            for r in db.execute(sql).fetchall():
                d = dict(r)
                d["enabled"] = bool(d["enabled"])
                out.append(d)
            return out

    def get_preferences(self) -> dict[str, Any]:
        prefs: dict[str, Any] = {
            "currency": "USD",
            "hide_amounts": False,
            "watchlist": [],
            "default_account_id": DEFAULT_ACCOUNT_ID,
            "alert_prefs": {},
        }
        with self._lock, self._connect("read") as db:
            for r in db.execute("SELECT key, value_json FROM user_setting ORDER BY key").fetchall():
                try:
                    prefs[str(r["key"])] = json.loads(r["value_json"])
                except Exception:
                    prefs[str(r["key"])] = r["value_json"]
        return prefs

    def _apply_one_op(
        self,
        db: sqlite3.Connection,
        raw_op: dict[str, Any],
    ) -> tuple[str, str, str, dict[str, Any] | None, dict[str, Any] | None]:
        op_type = str(raw_op.get("op") or "").strip().lower()
        entity = str(raw_op.get("entity") or "").strip().lower()
        if op_type not in ("put", "remove"):
            raise ValueError(f"unsupported op: {op_type!r}")
        if entity not in VALID_ENTITIES:
            raise ValueError(f"unsupported entity: {entity!r}")

        payload = raw_op.get("data")
        if payload is None:
            payload = raw_op.get("payload")
        if not isinstance(payload, dict):
            payload = {k: v for k, v in raw_op.items() if k not in ("op", "entity", "data", "payload")}
        else:
            payload = dict(payload)

        explicit_id = raw_op.get("id") if raw_op.get("id") not in (None, "") else payload.get("id")
        now = _now_iso()

        if op_type == "put" and entity in ("spot", "derivative"):
            for field, alias in (
                ("instrument_id", "symbol"),
                ("avg_cost", "cost_basis"),
                ("contract_multiplier", "multiplier"),
                ("sleeve", "bucket"),
                ("sleeve", "strategy_id"),
                ("thesis", "note"),
            ):
                if field not in payload and alias in payload:
                    payload[field] = payload[alias]

        if entity == "account":
            aid = str(explicit_id or f"acct_{uuid.uuid4().hex[:12]}").strip()
            existing = db.execute("SELECT * FROM account WHERE id=?", (aid,)).fetchone()
            before = dict(existing) if existing else None
            if op_type == "remove":
                if not explicit_id:
                    raise ValueError("account remove requires id")
                try:
                    db.execute("DELETE FROM account WHERE id=?", (aid,))
                except sqlite3.IntegrityError as exc:
                    raise ConstraintViolationError(f"account {aid} is referenced and cannot be deleted") from exc
                return (op_type, entity, aid, before, None)

            name = str(payload.get("name") or (before["name"] if before else "")).strip()
            if not name:
                raise ValueError("account name is required")
            eq_mode = str(payload.get("equity_mode") or (before["equity_mode"] if before else "total_ex_upl")).strip()
            if eq_mode != "total_ex_upl":
                raise ValueError("equity_mode must be 'total_ex_upl'")
            debt_str = parse_decimal_str(
                payload.get("confirmed_debt_usd", before["confirmed_debt_usd"] if before else "0")
            )
            if Decimal(debt_str) < 0:
                raise ValueError("confirmed_debt_usd cannot be negative")
            kind = str(payload.get("kind", before["kind"] if before else "general"))
            venue = str(payload.get("venue", before["venue"] if before else ""))
            base_ccy = str(payload.get("base_currency", before["base_currency"] if before else "USD")).upper()
            notes = str(payload.get("notes", before["notes"] if before else ""))
            archived = int(bool(payload.get("archived", before["archived"] if before else 0)))
            if before is None:
                db.execute(
                    """INSERT INTO account
                       (id,name,kind,venue,base_currency,equity_mode,confirmed_debt_usd,notes,archived,rev,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (aid, name, kind, venue, base_ccy, eq_mode, debt_str, notes, archived, 1, now, now),
                )
            else:
                db.execute(
                    """UPDATE account
                       SET name=?,kind=?,venue=?,base_currency=?,equity_mode=?,confirmed_debt_usd=?,
                           notes=?,archived=?,rev=rev+1,updated_at=?
                       WHERE id=?""",
                    (name, kind, venue, base_ccy, eq_mode, debt_str, notes, archived, now, aid),
                )
            after = dict(db.execute("SELECT * FROM account WHERE id=?", (aid,)).fetchone())
            return (op_type, entity, aid, before, after)

        if entity == "spot":
            if op_type == "remove":
                if explicit_id:
                    sid = str(explicit_id)
                    existing = db.execute("SELECT * FROM spot_balance WHERE id=?", (sid,)).fetchone()
                else:
                    aid = self._resolve_account_id_in_tx(db, payload.get("account_id"))
                    iid = str(payload.get("instrument_id") or payload.get("symbol") or "").strip().upper()
                    if not iid:
                        raise ValueError("spot remove requires id or instrument_id")
                    existing = db.execute(
                        "SELECT * FROM spot_balance WHERE account_id=? AND instrument_id=?",
                        (aid, iid),
                    ).fetchone()
                    sid = str(existing["id"]) if existing else f"spot:{aid}:{iid}"
                before = dict(existing) if existing else None
                db.execute("DELETE FROM spot_balance WHERE id=?", (sid,))
                return (op_type, entity, sid, before, None)

            aid_target = self._resolve_account_id_in_tx(db, payload.get("account_id"))
            iid_target = str(payload.get("instrument_id") or payload.get("symbol") or "").strip().upper()
            existing = db.execute("SELECT * FROM spot_balance WHERE id=?", (str(explicit_id),)).fetchone() if explicit_id else None
            if (
                existing is not None
                and str(payload.get("source") or "") == "local_sync"
                and iid_target
                and (str(existing["account_id"]), str(existing["instrument_id"]).upper()) != (aid_target, iid_target)
            ):
                # Do not clobber a renamed remote row when local_sync asks to upsert a different natural key
                existing = None
                explicit_id = f"spot:{aid_target}:{iid_target}:{uuid.uuid4().hex[:8]}"
            if existing is None:
                existing = db.execute(
                    "SELECT * FROM spot_balance WHERE account_id=? AND instrument_id=?",
                    (aid_target, iid_target),
                ).fetchone()
            before = dict(existing) if existing else None
            changes = payload
            payload = {**(before or {}), **changes}
            aid = self._resolve_account_id_in_tx(db, payload.get("account_id"))
            iid = str(payload.get("instrument_id") or "").strip().upper()
            if not iid:
                raise ValueError("spot put requires instrument_id or symbol")
            is_cash = bool(payload["is_cash"]) if "is_cash" in payload else (
                iid in ("USD", "USDT", "USDC", "CNY", "CNH", "HKD") or str(payload.get("asset_class") or "").lower() == "cash"
            )
            quote_ccy = str(payload.get("quote_currency") or "USD").upper()
            asset_cls = str(payload.get("asset_class") or ("cash" if is_cash else "equity"))
            sleeve = str(payload.get("sleeve") or "").strip()
            instrument = self._ensure_instrument_in_tx(
                db,
                iid,
                symbol=str(payload.get("symbol") or iid),
                name=str(payload.get("name") or iid),
                asset_class=asset_cls,
                quote_currency=quote_ccy,
                sleeve=sleeve,
            )
            if "quote_currency" in changes and quote_ccy != instrument["quote_currency"]:
                db.execute("UPDATE instrument SET quote_currency=?,rev=rev+1,updated_at=? WHERE id=?", (quote_ccy, now, iid))
            else:
                quote_ccy = instrument["quote_currency"]
            sid = str(existing["id"]) if existing else str(explicit_id or f"spot:{aid}:{iid}")

            qty_str = parse_decimal_str(payload.get("qty"))
            if Decimal(qty_str) < 0:
                raise ValueError("spot qty cannot be negative")
            raw_cost = payload.get("avg_cost")
            avg_cost_str = parse_decimal_str(raw_cost if raw_cost not in (None, "") else "0")
            if Decimal(avg_cost_str) < 0:
                raise ValueError("avg_cost cannot be negative")
            locked_str = parse_decimal_str(
                payload.get("locked_margin_usd", "0") if payload.get("locked_margin_usd") not in (None, "") else "0"
            )
            if Decimal(locked_str) < 0:
                raise ValueError("locked_margin_usd cannot be negative")
            stop_loss = parse_decimal_str(payload["stop_loss"]) if payload.get("stop_loss") not in (None, "") else None
            take_profit = parse_decimal_str(payload["take_profit"]) if payload.get("take_profit") not in (None, "") else None
            cost_ccy = str(payload.get("cost_currency") or quote_ccy).upper()
            thesis = str(payload.get("thesis") or "")
            source = str(payload.get("source") or "manual")
            as_of = str(payload.get("as_of") or now)

            if existing is None:
                db.execute(
                    """INSERT INTO spot_balance
                       (id,account_id,instrument_id,qty,avg_cost,cost_currency,is_cash,locked_margin_usd,
                        sleeve,stop_loss,take_profit,thesis,source,as_of,rev,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        sid, aid, iid, qty_str, avg_cost_str, cost_ccy, int(is_cash), locked_str,
                        sleeve, stop_loss, take_profit, thesis, source, as_of, 1, now, now,
                    ),
                )
            else:
                db.execute(
                    """UPDATE spot_balance
                       SET account_id=?,instrument_id=?,qty=?,avg_cost=?,cost_currency=?,is_cash=?,
                           locked_margin_usd=?,sleeve=?,stop_loss=?,take_profit=?,thesis=?,source=?,
                           as_of=?,rev=rev+1,updated_at=?
                       WHERE id=?""",
                    (
                        aid, iid, qty_str, avg_cost_str, cost_ccy, int(is_cash), locked_str,
                        sleeve, stop_loss, take_profit, thesis, source, as_of, now, sid,
                    ),
                )
            after = dict(db.execute("SELECT * FROM spot_balance WHERE id=?", (sid,)).fetchone())
            return (op_type, entity, sid, before, after)

        if entity == "derivative":
            if op_type == "remove":
                if not explicit_id:
                    raise ValueError("derivative remove requires id")
                did = str(explicit_id)
                existing = db.execute("SELECT * FROM derivative_position WHERE id=?", (did,)).fetchone()
                before = dict(existing) if existing else None
                db.execute("DELETE FROM derivative_position WHERE id=?", (did,))
                return (op_type, entity, did, before, None)

            did = str(explicit_id or f"deriv_{uuid.uuid4().hex[:12]}").strip()
            existing = db.execute("SELECT * FROM derivative_position WHERE id=?", (did,)).fetchone()
            before = dict(existing) if existing else None
            changes = payload
            payload = {**(before or {}), **changes}
            aid = self._resolve_account_id_in_tx(db, payload.get("account_id"))

            iid = str(payload.get("instrument_id") or "").strip().upper()
            if not iid:
                raise ValueError("derivative put requires instrument_id or symbol")
            side = str(payload.get("side") or "").strip().lower()
            if side not in ("long", "short"):
                raise ValueError(f"invalid derivative side: {side!r}")
            margin_mode = str(payload.get("margin_mode") or "cross").strip().lower()
            if margin_mode not in ("isolated", "cross"):
                raise ValueError(f"invalid margin_mode: {margin_mode!r}")
            contract_type = str(payload.get("contract_type") or ("inverse" if iid.endswith("-USD-SWAP") else "linear")).strip().lower()
            if contract_type not in ("linear", "inverse"):
                raise ValueError(f"invalid contract_type: {contract_type!r}")
            qty_unit = str(payload.get("qty_unit") or "coin").strip().lower()
            if qty_unit not in ("coin", "contract"):
                raise ValueError(f"invalid qty_unit: {qty_unit!r}")

            qty_str = parse_decimal_str(payload.get("qty"))
            if Decimal(qty_str) <= 0:
                raise ValueError("derivative qty must be > 0")
            mult_raw = payload.get("contract_multiplier", "1")
            mult_str = parse_decimal_str(mult_raw if mult_raw not in (None, "") else "1")
            if Decimal(mult_str) <= 0:
                raise ValueError("contract_multiplier must be > 0")
            entry_str = parse_decimal_str(payload.get("entry_price"))
            if Decimal(entry_str) <= 0:
                raise ValueError("entry_price must be > 0")
            margin_usd_str = parse_decimal_str(
                payload.get("margin_usd", "0") if payload.get("margin_usd") not in (None, "") else "0"
            )
            margin_amt_str = parse_decimal_str(
                payload.get("margin_amount") if payload.get("margin_amount") not in (None, "") else margin_usd_str
            )
            if Decimal(margin_usd_str) < 0 or Decimal(margin_amt_str) < 0:
                raise ValueError("margin cannot be negative")

            leverage_str = parse_decimal_str(payload["leverage"]) if payload.get("leverage") not in (None, "") else None
            if leverage_str is not None and Decimal(leverage_str) <= 0:
                raise ValueError("leverage must be > 0")
            mmr_str = parse_decimal_str(payload["mmr"]) if payload.get("mmr") not in (None, "") else None
            if mmr_str is not None and (Decimal(mmr_str) < 0 or Decimal(mmr_str) >= 1):
                raise ValueError("mmr must be in [0, 1)")
            venue_liq_str = parse_decimal_str(payload["venue_liq"]) if payload.get("venue_liq") not in (None, "") else None
            if venue_liq_str is not None and Decimal(venue_liq_str) <= 0:
                raise ValueError("venue_liq must be > 0")
            mark_px_str = parse_decimal_str(payload["mark_price"]) if payload.get("mark_price") not in (None, "") else None
            fund_str = parse_decimal_str(payload["funding_rate"]) if payload.get("funding_rate") not in (None, "") else None
            stop_loss = parse_decimal_str(payload["stop_loss"]) if payload.get("stop_loss") not in (None, "") else None
            take_profit = parse_decimal_str(payload["take_profit"]) if payload.get("take_profit") not in (None, "") else None

            stored_instrument = db.execute("SELECT quote_currency FROM instrument WHERE id=?", (iid,)).fetchone()
            default_quote = stored_instrument["quote_currency"] if stored_instrument else _derivative_quote_currency(iid, contract_type)
            quote_ccy = str(payload.get("quote_currency") or default_quote).upper()
            base_asset = str(payload.get("base_asset") or iid.split("-")[0]).upper()
            margin_ccy = str(payload.get("margin_currency") or (base_asset if contract_type == "inverse" else quote_ccy)).upper()
            self._ensure_instrument_in_tx(
                db,
                iid,
                symbol=str(payload.get("symbol") or iid),
                name=str(payload.get("name") or iid),
                asset_class=str(payload.get("asset_class") or "perp"),
                quote_currency=quote_ccy,
                base_asset=base_asset,
                settlement_currency=margin_ccy,
                contract_type=contract_type,
                multiplier=mult_str,
                lot_unit=qty_unit,
                sleeve=str(payload.get("sleeve") or ""),
            )

            if "quote_currency" in changes and quote_ccy != default_quote and stored_instrument is not None:
                db.execute("UPDATE instrument SET quote_currency=?,rev=rev+1,updated_at=? WHERE id=?", (quote_ccy, now, iid))
            liq_asof = str(payload.get("liq_asof") or "")
            sleeve = str(payload.get("sleeve") or "").strip()
            thesis = str(payload.get("thesis") or "")
            source = str(payload.get("source") or "manual")
            as_of = str(payload.get("as_of") or now)

            if existing is None:
                db.execute(
                    """INSERT INTO derivative_position
                       (id,account_id,instrument_id,side,margin_mode,qty,qty_unit,contract_multiplier,
                        contract_type,entry_price,margin_usd,margin_amount,margin_currency,leverage,mmr,
                        venue_liq,liq_asof,mark_price,funding_rate,stop_loss,take_profit,sleeve,thesis,
                        source,as_of,rev,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        did, aid, iid, side, margin_mode, qty_str, qty_unit, mult_str,
                        contract_type, entry_str, margin_usd_str, margin_amt_str, margin_ccy,
                        leverage_str, mmr_str, venue_liq_str, liq_asof, mark_px_str, fund_str,
                        stop_loss, take_profit, sleeve, thesis, source, as_of, 1, now, now,
                    ),
                )
            else:
                db.execute(
                    """UPDATE derivative_position
                       SET account_id=?,instrument_id=?,side=?,margin_mode=?,qty=?,qty_unit=?,
                           contract_multiplier=?,contract_type=?,entry_price=?,margin_usd=?,
                           margin_amount=?,margin_currency=?,leverage=?,mmr=?,venue_liq=?,liq_asof=?,
                           mark_price=?,funding_rate=?,stop_loss=?,take_profit=?,sleeve=?,thesis=?,
                           source=?,as_of=?,rev=rev+1,updated_at=?
                       WHERE id=?""",
                    (
                        aid, iid, side, margin_mode, qty_str, qty_unit, mult_str,
                        contract_type, entry_str, margin_usd_str, margin_amt_str, margin_ccy,
                        leverage_str, mmr_str, venue_liq_str, liq_asof, mark_px_str, fund_str,
                        stop_loss, take_profit, sleeve, thesis, source, as_of, now, did,
                    ),
                )
            after = dict(db.execute("SELECT * FROM derivative_position WHERE id=?", (did,)).fetchone())
            return (op_type, entity, did, before, after)

        if entity == "rule":
            rid = str(explicit_id or f"rule_{uuid.uuid4().hex[:12]}").strip()
            existing = db.execute("SELECT * FROM curation_rule WHERE id=?", (rid,)).fetchone()
            before = dict(existing) if existing else None
            if op_type == "remove":
                if not explicit_id:
                    raise ValueError("rule remove requires id")
                db.execute("DELETE FROM curation_rule WHERE id=?", (rid,))
                return (op_type, entity, rid, before, None)

            kind = str(payload.get("kind") or "").strip().lower()
            target = str(payload.get("target") or "").strip()
            action = str(payload.get("action") or "").strip().lower()
            enabled = int(bool(payload.get("enabled", True)))
            pts_str = _validate_rule_fields(kind, target, action, payload.get("points", "0"))
            note = str(payload.get("note") or "")
            if existing is None and enabled == 1:
                cnt = int(db.execute("SELECT COUNT(*) FROM curation_rule WHERE enabled=1").fetchone()[0])
                if cnt >= _MAX_RULES:
                    raise ValueError(f"maximum {_MAX_RULES} enabled curation rules allowed")
            if existing is None:
                db.execute(
                    """INSERT INTO curation_rule
                       (id,enabled,kind,target,action,points,note,rev,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (rid, enabled, kind, target, action, pts_str, note, 1, now, now),
                )
            else:
                db.execute(
                    """UPDATE curation_rule
                       SET enabled=?,kind=?,target=?,action=?,points=?,note=?,rev=rev+1,updated_at=?
                       WHERE id=?""",
                    (enabled, kind, target, action, pts_str, note, now, rid),
                )
            after = dict(db.execute("SELECT * FROM curation_rule WHERE id=?", (rid,)).fetchone())
            return (op_type, entity, rid, before, after)

        if entity == "setting":
            skey = str(payload.get("key") or explicit_id or "").strip()
            if not skey or len(skey) > 120:
                raise ValueError("setting key is required (1..120 chars)")
            existing = db.execute("SELECT * FROM user_setting WHERE key=?", (skey,)).fetchone()
            before = dict(existing) if existing else None
            if op_type == "remove":
                db.execute("DELETE FROM user_setting WHERE key=?", (skey,))
                return (op_type, entity, skey, before, None)
            val = payload["value"] if "value" in payload else {k: v for k, v in payload.items() if k not in ("key", "id")}
            val_json = json.dumps(val, ensure_ascii=False)
            if existing is None:
                db.execute(
                    "INSERT INTO user_setting(key,value_json,rev,updated_at) VALUES(?,?,?,?)",
                    (skey, val_json, 1, now),
                )
            else:
                db.execute(
                    "UPDATE user_setting SET value_json=?,rev=rev+1,updated_at=? WHERE key=?",
                    (val_json, now, skey),
                )
            after = dict(db.execute("SELECT * FROM user_setting WHERE key=?", (skey,)).fetchone())
            return (op_type, entity, skey, before, after)

        if entity == "review":
            if op_type == "remove":
                if not explicit_id:
                    raise ValueError("review remove requires id")
                rid_int = int(explicit_id)
                existing = db.execute("SELECT * FROM trade_reviews WHERE id=?", (rid_int,)).fetchone()
                before = JournalStore._row(existing, ("tags_json",)) if existing else None
                exp_rev = payload.get("expected_rev")
                JournalStore._delete_trade_review_in_tx(
                    db, rid_int, expected_rev=int(exp_rev) if exp_rev is not None else None
                )
                return (op_type, entity, str(rid_int), before, None)

            if explicit_id and "id" not in payload:
                payload["id"] = int(explicit_id)
            before = None
            if payload.get("id"):
                ex = db.execute("SELECT * FROM trade_reviews WHERE id=?", (int(payload["id"]),)).fetchone()
                before = JournalStore._row(ex, ("tags_json",)) if ex else None
            after = JournalStore._upsert_trade_review_in_tx(db, payload)
            return (op_type, entity, str(after["id"]), before, after)

        if entity == "essay":
            if op_type == "remove":
                if not explicit_id:
                    raise ValueError("essay remove requires id")
                eid_int = int(explicit_id)
                existing = db.execute("SELECT * FROM essays WHERE id=?", (eid_int,)).fetchone()
                before = JournalStore._row(existing, ("tags_json",)) if existing else None
                exp_rev = payload.get("expected_rev")
                JournalStore._delete_essay_in_tx(
                    db, eid_int, expected_rev=int(exp_rev) if exp_rev is not None else None
                )
                return (op_type, entity, str(eid_int), before, None)

            if explicit_id and "id" not in payload:
                payload["id"] = int(explicit_id)
            before = None
            if payload.get("id"):
                ex = db.execute("SELECT * FROM essays WHERE id=?", (int(payload["id"]),)).fetchone()
                before = JournalStore._row(ex, ("tags_json",)) if ex else None
            after = JournalStore._upsert_essay_in_tx(db, payload)
            return (op_type, entity, str(after["id"]), before, after)

        # entity == "article_status"
        fname = str(payload.get("filename") or explicit_id or "").strip()
        if (
            not fname
            or len(fname) > 180
            or "\x00" in fname
            or "/" in fname
            or "\\" in fname
            or ".." in fname
            or Path(fname).name != fname
            or not fname.lower().endswith(".md")
            or "异常告警" in fname
        ):
            raise ValueError(f"invalid article filename: {fname!r}")
        existing = db.execute("SELECT * FROM article_status WHERE filename=?", (fname,)).fetchone()
        before = dict(existing) if existing else None
        if op_type == "remove":
            db.execute("DELETE FROM article_status WHERE filename=?", (fname,))
            return (op_type, entity, fname, before, None)
        exp_rev = payload.get("expected_rev")
        after = JournalStore._set_article_status_in_tx(
            db,
            fname,
            str(payload.get("status") or ""),
            payload.get("note"),
            expected_rev=int(exp_rev) if exp_rev is not None else None,
        )
        return (op_type, entity, fname, before, after)

    @staticmethod
    def _validate_ops(ops: Any) -> None:
        if not isinstance(ops, list) or not (1 <= len(ops) <= 100):
            raise ValueError("ops must be a list of 1..100 operations")

    def apply_changes(
        self,
        *,
        request_id: str,
        expected_revision: int | None,
        ops: list[dict[str, Any]],
        actor_id: str,
        role: str = "PORTFOLIO_RW",
        summary: str = "",
        _commit_preview_id: str | None = None,
        _v1_action: str = "",
    ) -> dict[str, Any]:
        """Atomically apply 1..100 put/remove ops across 8 entities with (actor_id, request_id) idempotency."""
        if role != "PORTFOLIO_RW":
            self.log_audit_event(
                actor_id=actor_id,
                role=role,
                action="apply_changes",
                resource="portfolio",
                outcome="denied",
                request_id=request_id,
                detail={"reason": "insufficient_role"},
            )
            raise PermissionError("PORTFOLIO_RW role required for apply_changes")

        req_id = str(request_id or "").strip()
        if not req_id or len(req_id) > 128:
            raise ValueError("request_id must be 1..128 characters")
        act_id = str(actor_id or "").strip()
        if not act_id:
            raise ValueError("actor_id is required")
        self._validate_ops(ops)

        exp_rev = None if _v1_action and expected_revision is None else int(expected_revision)
        req_sha256 = hashlib.sha256(
            _canonical_json({"expected_revision": exp_rev, "ops": ops}).encode("utf-8")
        ).hexdigest()

        now = _now_iso()
        applied_items: list[dict[str, Any]] = []
        before_snapshots: list[dict[str, Any] | None] = []
        entities_touched: list[str] = []

        try:
            with self._lock, self._connect("immediate") as db:
                prev_cs = db.execute(
                    "SELECT request_sha256, response_json FROM change_set WHERE actor_id=? AND request_id=?",
                    (act_id, req_id),
                ).fetchone()
                if prev_cs is not None:
                    if str(prev_cs["request_sha256"]) == req_sha256:
                        cached_resp = json.loads(prev_cs["response_json"])
                        cached_resp["idempotent_replay"] = True
                        return cached_resp
                    raise IdempotencyConflictError(
                        f"idempotency conflict for request_id={req_id!r} with differing payload",
                        expected_rev=exp_rev,
                        entity="change_set",
                        entity_id=req_id,
                    )

                rev_row = db.execute("SELECT value FROM meta WHERE key='portfolio_revision'").fetchone()
                current_rev = int(rev_row["value"]) if rev_row and rev_row["value"] is not None else 0
                if _commit_preview_id is not None:
                    preview = db.execute(
                        "SELECT expected_revision, expires_at, committed_at FROM import_preview WHERE id=?",
                        (_commit_preview_id,),
                    ).fetchone()
                    if preview is None:
                        raise KeyError(f"import_preview not found: {_commit_preview_id!r}")
                    if preview["committed_at"] is not None or exp_rev != int(preview["expected_revision"]):
                        raise RevisionConflictError(
                            f"import preview {_commit_preview_id} already committed or bound to another revision",
                            expected_rev=exp_rev,
                            actual_rev=int(preview["expected_revision"]),
                            entity="import_preview",
                            entity_id=_commit_preview_id,
                        )
                    if datetime.fromisoformat(preview["expires_at"]) <= datetime.now(_BJT):
                        raise PreviewExpiredError(f"import preview {_commit_preview_id} expired at {preview['expires_at']}")
                    db.execute(
                        "UPDATE import_preview SET committed_at=? WHERE id=?",
                        (_now_iso(), _commit_preview_id),
                    )

                if exp_rev is not None and exp_rev != current_rev:
                    raise RevisionConflictError(
                        f"portfolio revision conflict: expected {exp_rev}, actual {current_rev}",
                        expected_rev=exp_rev,
                        actual_rev=current_rev,
                        entity="portfolio",
                        entity_id="revision",
                    )

                cs_id = f"chg_{uuid.uuid4().hex[:16]}"
                for seq_idx, raw_op in enumerate(ops, start=1):
                    if not isinstance(raw_op, dict):
                        raise ValueError(f"op at index {seq_idx - 1} must be a dict")
                    op_type, entity, eid, before, after = self._apply_one_op(db, raw_op)
                    if _v1_action and op_type == "remove" and before is None:
                        return {"items": []}
                    entities_touched.append(entity)
                    before_snapshots.append(before)
                    applied_items.append(
                        {
                            "seq": seq_idx,
                            "op": op_type,
                            "entity": entity,
                            "entity_id": eid,
                            "after": after,
                        }
                    )

                new_rev = current_rev + 1
                db.execute(
                    """INSERT INTO meta(key, value) VALUES('portfolio_revision', ?)
                       ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                    (str(new_rev),),
                )

                response = {
                    "ok": True,
                    "change_set_id": cs_id,
                    "request_id": req_id,
                    "actor_id": act_id,
                    "base_revision": current_rev,
                    "revision": new_rev,
                    "applied_count": len(applied_items),
                    "items": applied_items,
                    "idempotent_replay": False,
                    "created_at": now,
                }
                db.execute(
                    """INSERT INTO change_set
                       (id,actor_id,request_id,role,request_sha256,expected_revision,base_revision,
                        new_revision,ops_count,summary,status,response_json,created_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        cs_id,
                        act_id,
                        req_id,
                        role,
                        req_sha256,
                        exp_rev if exp_rev is not None else current_rev,
                        current_rev,
                        new_rev,
                        len(applied_items),
                        summary[:200],
                        "applied",
                        json.dumps(response, ensure_ascii=False),
                        now,
                    ),
                )
                for item, before_snap in zip(applied_items, before_snapshots):
                    db.execute(
                        """INSERT INTO change_item
                           (change_set_id,seq,op,entity,entity_id,before_json,after_json,created_at)
                           VALUES(?,?,?,?,?,?,?,?)""",
                        (
                            cs_id,
                            item["seq"],
                            item["op"],
                            item["entity"],
                            item["entity_id"],
                            json.dumps(before_snap, ensure_ascii=False) if before_snap is not None else None,
                            json.dumps(item["after"], ensure_ascii=False) if item["after"] is not None else None,
                            now,
                        ),
                    )
                resource = "portfolio"
                if _v1_action:
                    item = applied_items[0]
                    entity = "article" if item["entity"] == "article_status" else item["entity"]
                    resource = f"{entity}:{item['entity_id']}"
                self._log_audit_event_in_tx(
                    db, act_id, _v1_action or "apply_changes", role, resource, "ok", req_id,
                    {"revision": new_rev, "ops_count": len(ops), "entities": sorted(set(entities_touched))},
                )
        except RevisionConflictError:
            self.log_audit_event(
                actor_id=act_id,
                role=role,
                action="apply_changes",
                resource="portfolio",
                outcome="conflict",
                request_id=req_id,
                detail={"ops_count": len(ops)},
            )
            raise
        except Exception as exc:
            self.log_audit_event(
                actor_id=act_id,
                role=role,
                action="apply_changes",
                resource="portfolio",
                outcome="error",
                request_id=req_id,
                detail={"ops_count": len(ops), "error_type": type(exc).__name__},
            )
            raise

        return response

    def build_workspace(
        self,
        public_state: dict[str, Any] | None = None,
        currency: str = "USD",
        fx_override: Any = None,
    ) -> dict[str, Any]:
        """Build complete read-only private portfolio workspace snapshot with FX projection."""
        with self._lock, self._connect("read") as db:
            rev_row = db.execute("SELECT value FROM meta WHERE key='portfolio_revision'").fetchone()
            revision = int(rev_row["value"]) if rev_row and rev_row["value"] is not None else 0

            accounts = [dict(r) for r in db.execute("SELECT * FROM account WHERE archived=0 ORDER BY created_at ASC, id ASC").fetchall()]
            instruments = {
                str(r["id"]): dict(r)
                for r in db.execute("SELECT * FROM instrument").fetchall()
            }
            spot_rows = [dict(r) for r in db.execute("SELECT * FROM spot_balance ORDER BY is_cash DESC, instrument_id ASC").fetchall()]
            deriv_rows = [dict(r) for r in db.execute("SELECT * FROM derivative_position ORDER BY created_at ASC, id ASC").fetchall()]
            rules = []
            for r in db.execute("SELECT * FROM curation_rule ORDER BY created_at ASC, id ASC").fetchall():
                d = dict(r)
                d["enabled"] = bool(d["enabled"])
                rules.append(d)

            prefs: dict[str, Any] = {
                "currency": "USD",
                "hide_amounts": False,
                "watchlist": [],
                "default_account_id": DEFAULT_ACCOUNT_ID,
                "alert_prefs": {},
            }
            stored_settings: dict[str, Any] = {}
            for r in db.execute("SELECT key, value_json FROM user_setting ORDER BY key").fetchall():
                try:
                    parsed_val = json.loads(r["value_json"])
                except Exception:
                    parsed_val = r["value_json"]
                skey = str(r["key"])
                prefs[skey] = parsed_val
                if skey != "hide_amounts":
                    stored_settings[skey] = parsed_val

            db_quotes: dict[str, dict[str, Any]] = {}
            for r in db.execute(
                "SELECT instrument_id, kind, price, quote_currency, funding_rate, market_at, source FROM quote ORDER BY market_at DESC, id DESC"
            ).fetchall():
                iid = str(r["instrument_id"]).upper()
                if iid not in db_quotes:
                    db_quotes[iid] = dict(r)

            db_fx_row = db.execute(
                "SELECT pair, rate, basis, label, source, market_at, received_at, status FROM fx_quote ORDER BY market_at DESC, id DESC LIMIT 1"
            ).fetchone()
            db_fx = dict(db_fx_row) if db_fx_row else None

            def _max_ts_from_queries(queries: tuple[str, ...]) -> str:
                candidates: list[tuple[float, str]] = []
                for sql_q in queries:
                    try:
                        row_ts = db.execute(sql_q).fetchone()
                        if row_ts and row_ts[0]:
                            raw_ts = str(row_ts[0]).strip()
                            try:
                                dt = datetime.fromisoformat(raw_ts)
                                if dt.tzinfo is None:
                                    dt = dt.replace(tzinfo=_BJT)
                                candidates.append((dt.timestamp(), raw_ts))
                            except ValueError:
                                pass
                    except sqlite3.Error:
                        pass
                return max(candidates, key=lambda pair: pair[0])[1] if candidates else ""

            holdings_updated_at = _max_ts_from_queries(
                (
                    "SELECT MAX(updated_at) FROM account WHERE archived=0",
                    "SELECT MAX(updated_at) FROM spot_balance",
                    "SELECT MAX(updated_at) FROM derivative_position",
                    "SELECT MAX(ci.created_at) FROM change_item ci WHERE ci.entity IN ('account','spot','derivative')",
                )
            )
            prefs_updated_at = _max_ts_from_queries(
                (
                    "SELECT MAX(updated_at) FROM curation_rule",
                    "SELECT MAX(updated_at) FROM user_setting WHERE key != 'hide_amounts'",
                    "SELECT MAX(ci.created_at) FROM change_item ci WHERE ci.entity IN ('rule','setting')",
                )
            )
            portfolio_updated_at = _max_ts_from_queries(
                (
                    "SELECT MAX(updated_at) FROM account WHERE archived=0",
                    "SELECT MAX(updated_at) FROM spot_balance",
                    "SELECT MAX(updated_at) FROM derivative_position",
                    "SELECT MAX(updated_at) FROM curation_rule",
                    "SELECT MAX(updated_at) FROM user_setting WHERE key != 'hide_amounts'",
                    "SELECT MAX(ci.created_at) FROM change_item ci WHERE ci.entity IN ('account','spot','derivative','rule','setting')",
                )
            )
            last_cs_row = db.execute(
                "SELECT request_id, summary, new_revision, actor_id, created_at FROM change_set ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            last_change_set = dict(last_cs_row) if last_cs_row else None

            trades = [
                JournalStore._row(r, ("tags_json",))
                for r in db.execute("SELECT * FROM trade_reviews ORDER BY trade_date DESC, id DESC").fetchall()
            ]
            essays = [
                JournalStore._row(r, ("tags_json",))
                for r in db.execute("SELECT * FROM essays ORDER BY pinned DESC, updated_at DESC, id DESC").fetchall()
            ]
            statuses = {
                r["filename"]: {
                    "status": r["status"],
                    "note": r["note"],
                    "rev": int(r["rev"]),
                    "updated_at": r["updated_at"],
                }
                for r in db.execute("SELECT * FROM article_status ORDER BY filename").fetchall()
            }

        # Resolve quotes without blocking unnecessarily
        merged_quotes = dict(db_quotes)
        merged_quotes.update(_extract_public_quotes(public_state))

        def _has_quote(inst_id: str) -> bool:
            iid = str(inst_id or "").strip().upper()
            if not iid:
                return True
            if iid in merged_quotes:
                return True
            base = iid.split("-")[0]
            if base in merged_quotes or f"{base}-USDT" in merged_quotes:
                return True
            if iid.endswith("USDT") and len(iid) > 4:
                b2 = iid[:-4]
                if f"{b2}-USDT-SWAP" in merged_quotes or f"{b2}-USDT" in merged_quotes:
                    return True
            return False

        missing_crypto = [
            str(s.get("instrument_id") or "").upper()
            for s in spot_rows
            if not bool(s.get("is_cash")) and not _has_quote(str(s.get("instrument_id") or ""))
        ] + [
            str(d.get("instrument_id") or "").upper()
            for d in deriv_rows
            if not _has_quote(str(d.get("instrument_id") or ""))
        ]
        hybrid_targets = [sym for sym in ("QQQUSDT", "ZHIPUUSDT") if sym in missing_crypto]
        std_missing = [sym for sym in missing_crypto if sym not in ("QQQUSDT", "ZHIPUUSDT")]

        if std_missing:
            try:
                cq = fetch_crypto_quotes(timeout=3)
                for k, entry in (cq.get("quotes") or {}).items():
                    merged_quotes.setdefault(str(k).upper(), entry)
                for k, entry in (cq.get("swap") or {}).items():
                    merged_quotes.setdefault(str(k).upper(), entry)
                for k, entry in (cq.get("spot") or {}).items():
                    merged_quotes.setdefault(str(k).upper(), entry)
            except Exception:
                pass

        if hybrid_targets:
            try:
                from backend.market_quotes import fetch_hybrid_perp_quotes

                hq = fetch_hybrid_perp_quotes(symbols=hybrid_targets, timeout=3)
                for k, entry in (hq or {}).items():
                    merged_quotes.setdefault(str(k).upper(), entry)
            except Exception:
                pass

        # Resolve FX snapshot (override -> public_state -> db fx_quote -> fetch_fx_snapshot)
        fx_snap: dict[str, Any] | None = None
        if fx_override not in (None, ""):
            rate_str = parse_decimal_str(fx_override)
            if Decimal(rate_str) <= 0:
                raise ValueError("fx_override must be > 0")
            fx_snap = {
                "pair": "USD/CNH",
                "rate": rate_str,
                "basis": "override",
                "label": "手动指定汇率",
                "source": "override",
                "status": "ok",
                "market_at": _now_iso(),
                "received_at": _now_iso(),
            }
        elif isinstance(public_state, dict) and isinstance(public_state.get("fx_snapshot"), dict) and public_state["fx_snapshot"].get("rate"):
            fx_snap = dict(public_state["fx_snapshot"])
        elif db_fx and db_fx.get("rate"):
            fx_snap = db_fx
        else:
            req_ccy = (currency or "USD").strip().upper()
            if req_ccy in ("CNY", "RMB"):
                fx_snap = fetch_fx_snapshot(timeout=3)
            else:
                fx_snap = fetch_fx_snapshot(timeout=2)

        # Enrich spot & derivative inputs with instrument metadata
        spot_inputs: list[dict[str, Any]] = []
        for s in spot_rows:
            inst = instruments.get(str(s["instrument_id"]).upper(), {})
            spot_inputs.append(
                {
                    **s,
                    "symbol": inst.get("symbol") or s["instrument_id"],
                    "name": inst.get("name") or s["instrument_id"],
                    "asset_class": inst.get("asset_class") or ("cash" if s["is_cash"] else "equity"),
                    "quote_currency": inst.get("quote_currency") or s.get("cost_currency") or "USD",
                }
            )

        deriv_inputs: list[dict[str, Any]] = []
        for d in deriv_rows:
            inst = instruments.get(str(d["instrument_id"]).upper(), {})
            deriv_inputs.append(
                {
                    **d,
                    "symbol": inst.get("symbol") or d["instrument_id"],
                    "quote_currency": inst.get("quote_currency") or _derivative_quote_currency(d["instrument_id"], d["contract_type"]),
                }
            )

        confirmed_debt = Decimal("0")
        for acc in accounts:
            confirmed_debt += to_decimal(acc.get("confirmed_debt_usd") or "0")

        targets = None
        if isinstance(public_state, dict):
            targets = public_state.get("strategy_targets") or public_state.get("positions")

        fx_rate_val = fx_snap.get("rate") if isinstance(fx_snap, dict) else None
        val_usd = evaluate_portfolio(
            spot_balances=spot_inputs,
            derivative_positions=deriv_inputs,
            quotes=merged_quotes,
            confirmed_debt_usd=decimal_str(confirmed_debt),
            strategy_targets=targets,
            fx_rate=fx_rate_val,
        )
        projected = project_valuation_currency(val_usd, currency=currency, fx_snapshot=fx_snap)

        accounts_by_id = {str(a["id"]): str(a["name"]) for a in accounts}
        asset_class_labels = {
            "cash": "现金",
            "equity": "美股",
            "crypto_spot": "加密现货",
            "commodity": "商品/黄金",
            "etf": "ETF",
            "perp": "永续合约",
            "futures": "期货合约",
            "other": "现货",
        }
        ne_dec = val_usd["net_equity_usd"]

        spot_enriched: list[dict[str, Any]] = []
        for s, valued_spot in zip(projected["spot_balances"], val_usd["spot_balances"]):
            inst = instruments.get(str(s["instrument_id"]).upper(), {})
            mv_usd = valued_spot["market_value_usd"]
            wt_str: str | None = None
            if ne_dec > 0 and mv_usd is not None:
                wt_str = decimal_str(mv_usd / ne_dec * Decimal("100"), 2)
            spot_enriched.append(
                {
                    **s,
                    "account_name": accounts_by_id.get(str(s["account_id"]), str(s["account_id"])),
                    "asset_class_cn": asset_class_labels.get(str(s["asset_class"]), "现货"),
                    "sector": str(inst.get("sector") or ""),
                    "cost_basis": s["avg_cost"],
                    "price_usd": s["mark_price"],
                    "unrealized_pnl_usd": s["upl_usd"],
                    "weight_pct": wt_str,
                    "pct_of_equity": wt_str,
                    "bucket": s["sleeve"],
                    "note": s["thesis"],
                }
            )

        liq_dists: list[Decimal] = []
        deriv_enriched: list[dict[str, Any]] = []
        for d, valued_derivative in zip(projected["derivative_positions"], val_usd["derivative_positions"]):
            inst = instruments.get(str(d["instrument_id"]).upper(), {})
            notional_usd = valued_derivative["notional_usd"]
            wt_str = None
            if ne_dec > 0 and notional_usd is not None:
                wt_str = decimal_str(abs(notional_usd) / ne_dec * Decimal("100"), 2)
            if valued_derivative["liq_distance_pct"] is not None:
                liq_dists.append(valued_derivative["liq_distance_pct"])
            deriv_enriched.append(
                {
                    **d,
                    "account_name": accounts_by_id.get(str(d["account_id"]), str(d["account_id"])),
                    "sector": str(inst.get("sector") or ""),
                    "unrealized_pnl_usd": d["upl_usd"],
                    "liq_buffer_pct": d["liq_distance_pct"],
                    "distance_to_liq_pct": d["liq_distance_pct"],
                    "weight_pct": wt_str,
                    "pct_of_equity": wt_str,
                    "bucket": d["sleeve"],
                    "note": d["thesis"],
                }
            )

        min_liq_dist = decimal_str(min(liq_dists), 2) if liq_dists else None
        strategy_cmp_list = [
            {
                **row,
                "bucket": row["id"],
                "strategy_id": row["id"],
            }
            for row in projected["strategy_comparison"].values()
        ]

        workspace: dict[str, Any] = {
            "revision": revision,
            "portfolio_updated_at": portfolio_updated_at,
            "generated_at": _now_iso(),
            "equity_mode": projected["equity_mode"],
            "base_currency": projected["base_currency"],
            "display_currency": projected["display_currency"],
            "fx_status": projected["fx_status"],
            "fx_label": projected["fx_label"],
            "fx_rate": projected["fx_rate"],
            "fx_snapshot": fx_snap,
            "summary": {
                "net_equity": projected["net_equity"],
                "net_equity_usd": projected["net_equity_usd"],
                "cash_and_spot": projected["cash_and_spot"],
                "cash_and_spot_usd": projected["cash_and_spot_usd"],
                "spot_and_cash_usd": projected["cash_and_spot_usd"],
                "cash": projected["cash"],
                "cash_usd": projected["cash_usd"],
                "non_cash_spot": projected["non_cash_spot"],
                "non_cash_spot_usd": projected["non_cash_spot_usd"],
                "spot_upl": projected["spot_upl"],
                "spot_upl_usd": projected["spot_upl_usd"],
                "derivatives_upl": projected["derivatives_upl"],
                "derivatives_upl_usd": projected["derivatives_upl_usd"],
                "derivative_upl_usd": projected["derivatives_upl_usd"],
                "confirmed_debt": projected["confirmed_debt"],
                "confirmed_debt_usd": projected["confirmed_debt_usd"],
                "long_risk": projected["long_risk"],
                "long_risk_usd": projected["long_risk_usd"],
                "short_risk": projected["short_risk"],
                "short_risk_usd": projected["short_risk_usd"],
                "reported_margin": projected["reported_margin"],
                "reported_margin_usd": projected["reported_margin_usd"],
                "net_direction_pct": projected["net_direction_pct"],
                "effective_leverage": projected["effective_leverage"],
                "min_liq_distance_pct": min_liq_dist,
            },
            "accounts": accounts,
            "spot_balances": spot_enriched,
            "derivative_positions": deriv_enriched,
            "strategy_comparison": strategy_cmp_list,
            "strategy_comparison_by_id": projected["strategy_comparison"],
            "curation_rules": rules,
            "preferences": prefs,
            "raw_portfolio": {
                "accounts": accounts,
                "instruments": list(instruments.values()),
                "spot_balances": spot_rows,
                "derivative_positions": deriv_rows,
                "curation_rules": rules,
                "stored_settings": stored_settings,
                "holdings_updated_at": holdings_updated_at,
                "prefs_updated_at": prefs_updated_at,
                "last_change_set": last_change_set,
            },
            "unpriced_items": projected["unpriced_items"],
            "journal": {
                "trade_reviews": trades,
                "essays": essays,
                "article_status": statuses,
            },
        }

        if isinstance(public_state, dict):
            candidates = public_state.get("signal_candidates") or public_state.get("signals")
            if isinstance(candidates, list):
                try:
                    workspace["curated_signals"] = rank_curated_signals(
                        candidates,
                        rules=rules,
                        workspace=workspace,
                    )
                except Exception:
                    pass

        return workspace
