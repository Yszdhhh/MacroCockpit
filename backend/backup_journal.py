"""Consistent SQLite WAL hot backup with integrity, foreign-key, and retention verification."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

BJT = timezone(timedelta(hours=8))
_SAFE_IDENT_RE_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")
_JOURNAL_TABLES = {"meta", "trade_reviews", "essays", "article_status"}
_PORTFOLIO_TABLES = {"account", "instrument", "spot_balance", "derivative_position"}


def _now_bjt() -> datetime:
    return datetime.now(BJT)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _quote_ident(name: str) -> str:
    if not name or not all(ch in _SAFE_IDENT_RE_CHARS for ch in name):
        raise ValueError(f"Unsafe table identifier: {name!r}")
    return f'"{name}"'


def _verify_backup_copy(copy_path: Path, *, expected_version: int | None = None) -> tuple[int, dict[str, int]]:
    con = sqlite3.connect(copy_path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True, timeout=15.0)
    try:
        integrity_rows = con.execute("PRAGMA integrity_check").fetchall()
        integrity_vals = [str(r[0]) for r in integrity_rows]
        if integrity_vals != ["ok"]:
            raise RuntimeError(f"Backup integrity_check failed: {integrity_vals}")

        con.execute("PRAGMA foreign_keys = ON")
        fk_rows = con.execute("PRAGMA foreign_key_check").fetchall()
        if fk_rows:
            raise RuntimeError(f"Backup foreign_key_check failed: {len(fk_rows)} violation(s)")

        user_version = int(con.execute("PRAGMA user_version").fetchone()[0])
        tables = [
            str(r[0])
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        ]
        if expected_version is not None and user_version != expected_version:
            raise RuntimeError(f"Backup user_version mismatch: expected {expected_version}, got {user_version}")
        required = set(_JOURNAL_TABLES)
        if user_version >= 2:
            required.update(_PORTFOLIO_TABLES)
        if user_version >= 3:
            required.add("import_preview")
        missing = required.difference(tables)
        if missing:
            raise RuntimeError(f"Backup missing core tables: {', '.join(sorted(missing))}")

        counts: dict[str, int] = {}
        for tbl in tables:
            counts[tbl] = int(con.execute(f"SELECT COUNT(*) FROM {_quote_ident(tbl)}").fetchone()[0])
        return user_version, counts
    finally:
        con.close()


def _prune_old_backups(backup_dir: Path, stem: str, keep: int) -> list[str]:
    keep_n = max(1, int(keep))
    candidates = [
        p
        for p in backup_dir.glob(f"{stem}_*.db")
        if p.is_file() and not p.name.endswith(".tmp")
    ]
    candidates.sort(key=lambda p: (p.stat().st_mtime_ns, p.name), reverse=True)
    pruned: list[str] = []
    for old_path in candidates[keep_n:]:
        meta_path = old_path.with_suffix(".meta.json")
        try:
            old_path.unlink(missing_ok=True)
            meta_path.unlink(missing_ok=True)
            pruned.append(old_path.name)
        except OSError:
            pass
    return pruned


def _private_temp_path(destination: Path) -> Path:
    fd, name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
    os.close(fd)
    return Path(name)


def _restrict_permissions(path: Path) -> None:
    try:
        path.chmod(0o600)
    except OSError:
        pass


def create_verified_backup(
    db_path: Path | str,
    backup_dir: Path | str,
    *,
    keep: int = 10,
) -> dict[str, Any]:
    """Snapshot a live WAL SQLite database using sqlite3.Connection.backup() and verify it."""
    src_path = Path(db_path)
    out_dir = Path(backup_dir)
    if not src_path.is_file():
        raise FileNotFoundError(f"Database file not found: {src_path}")

    out_dir.mkdir(parents=True, exist_ok=True)
    now = _now_bjt()
    stamp = now.strftime("%Y%m%d_%H%M%S_%f")
    final_path = out_dir / f"{src_path.stem}_{stamp}.db"
    suffix_idx = 1
    while final_path.exists():
        final_path = out_dir / f"{src_path.stem}_{stamp}_{suffix_idx}.db"
        suffix_idx += 1
    tmp_path = _private_temp_path(final_path)

    src_con: sqlite3.Connection | None = None
    dst_con: sqlite3.Connection | None = None
    try:
        src_con = sqlite3.connect(src_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=15.0)
        # Read the version and copy from the same SQLite snapshot, including committed WAL rows.
        src_con.execute("BEGIN")
        src_con.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()
        source_version = int(src_con.execute("PRAGMA user_version").fetchone()[0])
        dst_con = sqlite3.connect(str(tmp_path), timeout=15.0)
        src_con.backup(dst_con)
        dst_con.commit()
        dst_con.execute("PRAGMA journal_mode = DELETE")
    except Exception:
        if dst_con is not None:
            dst_con.close()
            dst_con = None
        if src_con is not None:
            src_con.close()
            src_con = None
        tmp_path.unlink(missing_ok=True)
        raise
    finally:
        if dst_con is not None:
            dst_con.close()
        if src_con is not None:
            src_con.close()

    try:
        _restrict_permissions(tmp_path)
        user_version, table_counts = _verify_backup_copy(tmp_path, expected_version=source_version)
        sha256_hex = _sha256_file(tmp_path)
        os.replace(tmp_path, final_path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    pruned = _prune_old_backups(out_dir, src_path.stem, keep=keep)
    retained = sorted(p.name for p in out_dir.glob(f"{src_path.stem}_*.db") if p.is_file())

    report = {
        "ok": True,
        "backup_path": str(final_path),
        "filename": final_path.name,
        "bytes": final_path.stat().st_size,
        "sha256": sha256_hex,
        "integrity_check": "ok",
        "foreign_key_check": [],
        "user_version": user_version,
        "table_counts": table_counts,
        "retained_count": len(retained),
        "pruned": pruned,
        "created_at": now.isoformat(timespec="seconds"),
    }
    meta_path = final_path.with_suffix(".meta.json")
    meta_tmp = _private_temp_path(meta_path)
    try:
        meta_tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        _restrict_permissions(meta_tmp)
        os.replace(meta_tmp, meta_path)
    finally:
        meta_tmp.unlink(missing_ok=True)
    return report


def restore_verified_backup(backup_path: Path | str, target_db: Path | str) -> dict[str, Any]:
    """Verify a standalone backup and replace an offline journal database atomically."""
    backup = Path(backup_path).resolve()
    target = Path(target_db).resolve()
    if not backup.is_file():
        raise FileNotFoundError(f"Backup file not found: {backup}")
    if backup == target:
        raise ValueError("Backup and target DB must be distinct")
    if any(backup.with_name(backup.name + ext).exists() for ext in ("-wal", "-journal")):
        raise RuntimeError("Backup must be a standalone SQLite file")

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = _private_temp_path(target)
    try:
        shutil.copyfile(backup, tmp_path)
        _restrict_permissions(tmp_path)
        user_version, table_counts = _verify_backup_copy(tmp_path)
        if any(target.with_name(target.name + ext).exists() for ext in ("-wal", "-shm", "-journal")):
            raise RuntimeError("Stop all journal connections and checkpoint the target before restore")
        os.replace(tmp_path, target)
    finally:
        tmp_path.unlink(missing_ok=True)
    return {
        "ok": True,
        "backup_path": str(backup),
        "restored_path": str(target),
        "integrity_check": "ok",
        "foreign_key_check": [],
        "user_version": user_version,
        "table_counts": table_counts,
    }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    root = Path(os.environ.get("MACROCOCKPIT_ROOT") or Path(__file__).resolve().parent.parent)
    data_dir = Path(os.environ.get("MACROCOCKPIT_DATA_DIR") or (root / "data"))
    parser = argparse.ArgumentParser(description="Create verified WAL backup of MacroCockpit journal SQLite DB")
    parser.add_argument("--db", type=Path, default=data_dir / "user_journal.db", help="Source DB, or target DB with --restore")
    parser.add_argument("--restore", type=Path, metavar="BACKUP", help="Verify and restore a backup into --db")
    parser.add_argument("--out-dir", type=Path, default=data_dir / "backups", help="Output backup directory")
    parser.add_argument("--keep", type=int, default=10, help="Number of recent backups to retain")
    args = parser.parse_args()
    if args.restore is not None:
        report = restore_verified_backup(args.restore, args.db)
    else:
        report = create_verified_backup(args.db, args.out_dir, keep=args.keep)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
