"""Local SQLite storage and schema migrations for MacroCockpit journal & portfolio DB."""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.runtime import BJT as _BJT, require_finite_float as _finite_float

_MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
_LATEST_SCHEMA_VERSION = 3

_PATH_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS: dict[str, threading.RLock] = {}


def _get_path_lock(db_path: Path) -> threading.RLock:
    key = str(db_path.resolve())
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[key] = lock
        return lock


class RevisionConflictError(Exception):
    """Raised when optimistic concurrency revision or idempotency check fails (HTTP 409)."""

    def __init__(
        self,
        message: str,
        *,
        expected_rev: int | None = None,
        actual_rev: int | None = None,
        entity: str = "",
        entity_id: str | int | None = None,
    ) -> None:
        super().__init__(message)
        self.expected_rev = expected_rev
        self.actual_rev = actual_rev
        self.entity = entity
        self.entity_id = entity_id


def _split_sql_statements(sql_text: str) -> list[str]:
    """Split SQL text into executable statements without using autocommitting executescript()."""
    statements: list[str] = []
    buf: list[str] = []
    for raw_line in sql_text.splitlines(keepends=True):
        stripped = raw_line.strip()
        if not buf and (not stripped or stripped.startswith("--")):
            continue
        buf.append(raw_line)
        joined = "".join(buf)
        if sqlite3.complete_statement(joined):
            cleaned = joined.strip()
            if cleaned and not all(
                not ln.strip() or ln.strip().startswith("--")
                for ln in cleaned.splitlines()
            ):
                statements.append(cleaned)
            buf = []
    leftover = "".join(buf).strip()
    if leftover and not all(
        not ln.strip() or ln.strip().startswith("--")
        for ln in leftover.splitlines()
    ):
        statements.append(leftover)
    return statements


class JournalStore:
    """Thread-safe SQLite store with WAL initialization, v1->v3 migrations, and explicit transactions."""

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = _get_path_lock(self.db_path)
        with self._lock:
            self._init_wal_once()
            self._apply_migrations()
            self._init_auth_tables()

    def _init_auth_tables(self) -> None:
        with self._connect("immediate") as db:
            db.execute("CREATE TABLE IF NOT EXISTS auth_revoked_cookies (tok_hash TEXT PRIMARY KEY, exp_ts INTEGER NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS auth_runtime_kv (k TEXT PRIMARY KEY, v TEXT NOT NULL)")

    def _init_wal_once(self) -> None:
        with self._connect("none") as db:
            db.execute("PRAGMA journal_mode=WAL")

    @contextmanager
    def _connect(self, tx_mode: str = "read"):
        """Open a connection with foreign_keys=ON, busy_timeout=15000, and explicit BEGIN/BEGIN IMMEDIATE."""
        db = sqlite3.connect(self.db_path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=15000")
        db.execute("PRAGMA foreign_keys=ON")
        mode = (tx_mode or "none").lower()
        if mode in ("read", "deferred"):
            db.execute("BEGIN")
        elif mode in ("write", "immediate"):
            db.execute("BEGIN IMMEDIATE")
        elif mode != "none":
            db.close()
            raise ValueError(f"unsupported transaction mode: {tx_mode}")
        try:
            yield db
            if db.in_transaction:
                db.execute("COMMIT")
        except Exception:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    @staticmethod
    def _now() -> str:
        return datetime.now(_BJT).isoformat(timespec="seconds")

    @staticmethod
    def _exec_migration_file(db: sqlite3.Connection, filename: str) -> None:
        sql_path = _MIGRATIONS_DIR / filename
        sql_text = sql_path.read_text(encoding="utf-8")
        for stmt in _split_sql_statements(sql_text):
            db.execute(stmt)

    @staticmethod
    def _table_columns(db: sqlite3.Connection, table: str) -> set[str]:
        rows = db.execute(f'PRAGMA table_info("{table}")').fetchall()
        return {str(r["name"]) for r in rows}

    def _apply_migrations(self) -> None:
        with self._connect("read") as db:
            uv = int(db.execute("PRAGMA user_version").fetchone()[0])
        if uv >= _LATEST_SCHEMA_VERSION:
            return

        with self._connect("immediate") as db:
            uv = int(db.execute("PRAGMA user_version").fetchone()[0])
            if uv < 1:
                self._exec_migration_file(db, "001_baseline.sql")
                review_cols = self._table_columns(db, "trade_reviews")
                if "position_id" not in review_cols:
                    db.execute("ALTER TABLE trade_reviews ADD COLUMN position_id TEXT NULL")
                if "rev" not in review_cols:
                    db.execute("ALTER TABLE trade_reviews ADD COLUMN rev INTEGER NOT NULL DEFAULT 0")
                essay_cols = self._table_columns(db, "essays")
                if "rev" not in essay_cols:
                    db.execute("ALTER TABLE essays ADD COLUMN rev INTEGER NOT NULL DEFAULT 0")
                status_cols = self._table_columns(db, "article_status")
                if "rev" not in status_cols:
                    db.execute("ALTER TABLE article_status ADD COLUMN rev INTEGER NOT NULL DEFAULT 0")
                db.execute("INSERT OR IGNORE INTO meta(key, value) VALUES('portfolio_revision', '0')")
                db.execute("PRAGMA user_version = 1")
                uv = 1

            if uv < 2:
                self._exec_migration_file(db, "002_portfolio.sql")
                db.execute("PRAGMA user_version = 2")
                uv = 2

            if uv < 3:
                self._exec_migration_file(db, "003_agent_import.sql")
                db.execute("PRAGMA user_version = 3")

    def seed_if_empty(self, state: dict[str, Any] | None) -> bool:
        """Explicit one-time seed helper; never invoked automatically from read methods."""
        with self._lock, self._connect("immediate") as db:
            if db.execute("SELECT value FROM meta WHERE key=?", ("seeded_v1",)).fetchone():
                return False
            empty = all(
                db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
                for table in ("trade_reviews", "essays", "article_status")
            )
            seeded_rows = False
            if empty and isinstance(state, dict):
                gate = state.get("risk_gate") or {}
                reasons = "; ".join(str(x) for x in gate.get("reasons", []) if x)
                thesis = "宏观门控：" + str(gate.get("label") or "") + ("；" + reasons if reasons else "")
                today = str(state.get("generated_at_bjt") or self._now())[:10]
                btc = next((x for x in state.get("macro_fred", []) if x.get("id") == "btc_spot"), {})
                positions = state.get("positions") or []
                now = self._now()
                for pos in positions:
                    pos_id = str(pos.get("id") or pos.get("name") or "position")
                    spot_px = _finite_float(btc.get("raw_value"), 0.0) if "btc" in pos_id.lower() else 0.0
                    db.execute(
                        """INSERT INTO trade_reviews
                        (trade_date,symbol,direction,size_pct,price_usd,pnl_usd,status,thesis,position_id,rev,created_at,updated_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            today,
                            pos_id,
                            "跟踪",
                            _finite_float(pos.get("recommended_pct"), 0.0),
                            spot_px,
                            0.0,
                            "当前跟踪",
                            thesis,
                            pos_id,
                            1,
                            now,
                            now,
                        ),
                    )
                    seeded_rows = True
                if state.get("risk_gate"):
                    summary = "宏观门控：" + str(gate.get("label") or "")
                    if reasons:
                        summary += "\n\n" + reasons
                    db.execute(
                        "INSERT INTO essays(title,mood,tags_json,body_md,pinned,rev,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                        ("宏观观察基线", "冷静", "[]", summary, 0, 1, now, now),
                    )
                    seeded_rows = True
            db.execute("INSERT OR IGNORE INTO meta(key,value) VALUES(?,?)", ("seeded_v1", "1"))
            return seeded_rows

    @staticmethod
    def _row(row: sqlite3.Row, json_fields: tuple[str, ...] = ()) -> dict[str, Any]:
        result = dict(row)
        for field in json_fields:
            result[field.removesuffix("_json")] = json.loads(result.pop(field) or "[]")
        return result

    def get_snapshot(self, state: dict[str, Any] | None = None) -> dict[str, Any]:
        """Purely read-only consistent snapshot across journal tables (never seeds on GET)."""
        _ = state
        with self._lock, self._connect("read") as db:
            trades = [
                self._row(r, ("tags_json",))
                for r in db.execute("SELECT * FROM trade_reviews ORDER BY trade_date DESC,id DESC")
            ]
            essays = [
                self._row(r, ("tags_json",))
                for r in db.execute("SELECT * FROM essays ORDER BY pinned DESC,updated_at DESC,id DESC")
            ]
            statuses = {
                r["filename"]: {
                    "status": r["status"],
                    "note": r["note"],
                    "rev": int(r["rev"]),
                    "updated_at": r["updated_at"],
                }
                for r in db.execute("SELECT * FROM article_status ORDER BY filename")
            }
            return {"trade_reviews": trades, "essays": essays, "article_status": statuses}

    @classmethod
    def _upsert_trade_review_in_tx(
        cls,
        db: sqlite3.Connection,
        data: dict[str, Any],
        *,
        expected_rev: int | None = None,
    ) -> dict[str, Any]:
        now = cls._now()
        tags = data.get("tags") or []
        if not isinstance(tags, list):
            raise ValueError("tags must be a list")
        exp_raw = expected_rev if expected_rev is not None else data.get("expected_rev")
        exp_rev = int(exp_raw) if exp_raw is not None else None

        fields = (
            str(data.get("trade_date") or now[:10]),
            str(data.get("symbol") or ""),
            str(data.get("direction") or ""),
            _finite_float(data.get("size_pct"), 0.0),
            _finite_float(data.get("price_usd"), 0.0),
            _finite_float(data.get("pnl_usd"), 0.0),
            str(data.get("status") or ""),
            str(data.get("thesis") or ""),
            str(data.get("execution_note") or ""),
            str(data.get("lesson") or ""),
            json.dumps([str(t) for t in tags], ensure_ascii=False),
        )
        rid = data.get("id")
        if rid:
            rid_int = int(rid)
            existing = db.execute("SELECT * FROM trade_reviews WHERE id=?", (rid_int,)).fetchone()
            if existing is None:
                raise KeyError(rid_int)
            current_rev = int(existing["rev"])
            if exp_rev is not None and current_rev != exp_rev:
                raise RevisionConflictError(
                    f"trade_review {rid_int} revision conflict: expected {exp_rev}, actual {current_rev}",
                    expected_rev=exp_rev,
                    actual_rev=current_rev,
                    entity="review",
                    entity_id=rid_int,
                )
            if "position_id" in data:
                pos_raw = data.get("position_id")
                position_id = str(pos_raw).strip() if pos_raw not in (None, "") else None
            else:
                position_id = existing["position_id"]

            cur = db.execute(
                """UPDATE trade_reviews
                   SET trade_date=?,symbol=?,direction=?,size_pct=?,price_usd=?,pnl_usd=?,
                       status=?,thesis=?,execution_note=?,lesson=?,tags_json=?,position_id=?,
                       rev=rev+1,updated_at=?
                   WHERE id=? AND rev=?""",
                (*fields, position_id, now, rid_int, current_rev),
            )
            if cur.rowcount == 0:
                raise RevisionConflictError(
                    f"trade_review {rid_int} concurrent revision conflict",
                    expected_rev=exp_rev,
                    actual_rev=current_rev,
                    entity="review",
                    entity_id=rid_int,
                )
            row = db.execute("SELECT * FROM trade_reviews WHERE id=?", (rid_int,)).fetchone()
        else:
            pos_raw = data.get("position_id")
            position_id = str(pos_raw).strip() if pos_raw not in (None, "") else None
            cur = db.execute(
                """INSERT INTO trade_reviews
                   (trade_date,symbol,direction,size_pct,price_usd,pnl_usd,status,thesis,
                    execution_note,lesson,tags_json,position_id,rev,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (*fields, position_id, 1, now, now),
            )
            row = db.execute("SELECT * FROM trade_reviews WHERE id=?", (cur.lastrowid,)).fetchone()
        return cls._row(row, ("tags_json",))

    def upsert_trade_review(
        self,
        data: dict[str, Any],
        *,
        expected_rev: int | None = None,
    ) -> dict[str, Any]:
        with self._lock, self._connect("immediate") as db:
            return self._upsert_trade_review_in_tx(db, data, expected_rev=expected_rev)

    @staticmethod
    def _delete_trade_review_in_tx(
        db: sqlite3.Connection,
        review_id: int,
        *,
        expected_rev: int | None = None,
    ) -> bool:
        rid_int = int(review_id)
        if expected_rev is not None:
            existing = db.execute("SELECT rev FROM trade_reviews WHERE id=?", (rid_int,)).fetchone()
            if existing is None:
                return False
            actual = int(existing["rev"])
            if actual != int(expected_rev):
                raise RevisionConflictError(
                    f"trade_review {rid_int} revision conflict: expected {expected_rev}, actual {actual}",
                    expected_rev=int(expected_rev),
                    actual_rev=actual,
                    entity="review",
                    entity_id=rid_int,
                )
            return db.execute("DELETE FROM trade_reviews WHERE id=? AND rev=?", (rid_int, actual)).rowcount > 0
        return db.execute("DELETE FROM trade_reviews WHERE id=?", (rid_int,)).rowcount > 0

    def delete_trade_review(self, review_id: int, *, expected_rev: int | None = None) -> bool:
        with self._lock, self._connect("immediate") as db:
            return self._delete_trade_review_in_tx(db, review_id, expected_rev=expected_rev)

    @classmethod
    def _upsert_essay_in_tx(
        cls,
        db: sqlite3.Connection,
        data: dict[str, Any],
        *,
        expected_rev: int | None = None,
    ) -> dict[str, Any]:
        now = cls._now()
        tags = data.get("tags") or []
        if not isinstance(tags, list):
            raise ValueError("tags must be a list")
        exp_raw = expected_rev if expected_rev is not None else data.get("expected_rev")
        exp_rev = int(exp_raw) if exp_raw is not None else None

        fields = (
            str(data.get("title") or ""),
            str(data.get("mood") or "冷静"),
            json.dumps([str(t) for t in tags], ensure_ascii=False),
            str(data.get("body_md") or ""),
            int(bool(data.get("pinned"))),
        )
        eid = data.get("id")
        if eid:
            eid_int = int(eid)
            existing = db.execute("SELECT * FROM essays WHERE id=?", (eid_int,)).fetchone()
            if existing is None:
                raise KeyError(eid_int)
            current_rev = int(existing["rev"])
            if exp_rev is not None and current_rev != exp_rev:
                raise RevisionConflictError(
                    f"essay {eid_int} revision conflict: expected {exp_rev}, actual {current_rev}",
                    expected_rev=exp_rev,
                    actual_rev=current_rev,
                    entity="essay",
                    entity_id=eid_int,
                )
            cur = db.execute(
                "UPDATE essays SET title=?,mood=?,tags_json=?,body_md=?,pinned=?,rev=rev+1,updated_at=? WHERE id=? AND rev=?",
                (*fields, now, eid_int, current_rev),
            )
            if cur.rowcount == 0:
                raise RevisionConflictError(
                    f"essay {eid_int} concurrent revision conflict",
                    expected_rev=exp_rev,
                    actual_rev=current_rev,
                    entity="essay",
                    entity_id=eid_int,
                )
            row = db.execute("SELECT * FROM essays WHERE id=?", (eid_int,)).fetchone()
        else:
            cur = db.execute(
                "INSERT INTO essays(title,mood,tags_json,body_md,pinned,rev,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (*fields, 1, now, now),
            )
            row = db.execute("SELECT * FROM essays WHERE id=?", (cur.lastrowid,)).fetchone()
        return cls._row(row, ("tags_json",))

    def upsert_essay(
        self,
        data: dict[str, Any],
        *,
        expected_rev: int | None = None,
    ) -> dict[str, Any]:
        with self._lock, self._connect("immediate") as db:
            return self._upsert_essay_in_tx(db, data, expected_rev=expected_rev)

    @staticmethod
    def _delete_essay_in_tx(
        db: sqlite3.Connection,
        essay_id: int,
        *,
        expected_rev: int | None = None,
    ) -> bool:
        eid_int = int(essay_id)
        if expected_rev is not None:
            existing = db.execute("SELECT rev FROM essays WHERE id=?", (eid_int,)).fetchone()
            if existing is None:
                return False
            actual = int(existing["rev"])
            if actual != int(expected_rev):
                raise RevisionConflictError(
                    f"essay {eid_int} revision conflict: expected {expected_rev}, actual {actual}",
                    expected_rev=int(expected_rev),
                    actual_rev=actual,
                    entity="essay",
                    entity_id=eid_int,
                )
            return db.execute("DELETE FROM essays WHERE id=? AND rev=?", (eid_int, actual)).rowcount > 0
        return db.execute("DELETE FROM essays WHERE id=?", (eid_int,)).rowcount > 0

    def delete_essay(self, essay_id: int, *, expected_rev: int | None = None) -> bool:
        with self._lock, self._connect("immediate") as db:
            return self._delete_essay_in_tx(db, essay_id, expected_rev=expected_rev)

    @classmethod
    def _set_article_status_in_tx(
        cls,
        db: sqlite3.Connection,
        filename: str,
        status: str,
        note: str | None = None,
        *,
        expected_rev: int | None = None,
    ) -> dict[str, Any]:
        if status not in ("unread", "reading", "read", "starred"):
            raise ValueError("invalid article status")
        if note is not None and not isinstance(note, str):
            raise ValueError("note must be a string")
        existing = db.execute("SELECT * FROM article_status WHERE filename=?", (filename,)).fetchone()
        if expected_rev is not None:
            actual = int(existing["rev"]) if existing is not None else 0
            if actual != int(expected_rev):
                raise RevisionConflictError(
                    f"article_status {filename} revision conflict: expected {expected_rev}, actual {actual}",
                    expected_rev=int(expected_rev),
                    actual_rev=actual,
                    entity="article_status",
                    entity_id=filename,
                )
        now = cls._now()
        if existing is None:
            db.execute(
                "INSERT INTO article_status(filename,status,note,rev,updated_at) VALUES(?,?,?,?,?)",
                (filename, status, "" if note is None else note, 1, now),
            )
        else:
            new_note = existing["note"] if note is None else note
            db.execute(
                "UPDATE article_status SET status=?,note=?,rev=rev+1,updated_at=? WHERE filename=?",
                (status, new_note, now, filename),
            )
        row = db.execute(
            "SELECT status,note,rev,updated_at FROM article_status WHERE filename=?",
            (filename,),
        ).fetchone()
        return dict(row)

    def set_article_status(
        self,
        filename: str,
        status: str,
        note: str | None = None,
        *,
        expected_rev: int | None = None,
    ) -> dict[str, Any]:
        with self._lock, self._connect("immediate") as db:
            return self._set_article_status_in_tx(
                db, filename, status, note, expected_rev=expected_rev
            )
