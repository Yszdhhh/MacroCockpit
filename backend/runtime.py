"""Canonical Level-0 runtime primitives: timezone/clock, paths, env spec, locks, atomic IO, and numeric helpers."""
from __future__ import annotations

import json
import math
import os
import re
import threading
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

UTC = timezone.utc
BJT = timezone(timedelta(hours=8))

# Authoritative inventory of runtime environment variables consumed across MacroCockpit.
ENV_SPEC: dict[str, str] = {
    "MACROCOCKPIT_ROOT": "Repository / deployment root directory override",
    "MACROCOCKPIT_DATA_DIR": "Runtime data & SQLite directory override (defaults to <root>/data)",
    "MACROCOCKPIT_DIST_DIR": "Static build artifacts directory override (defaults to <root>/dist)",
    "MACROCOCKPIT_USER_HOME": "Explicit user home override (guards against Windows SYSTEM profile)",
    "HERMES_HOME": "Hermes agent home directory override",
    "MACROCOCKPIT_KOL_DIR": "KOL notes corpus root override",
    "MACROCOCKPIT_NOTES_DIR": "Obsidian inbox markdown directory override",
    "MACROCOCKPIT_DECISION_BRIEF_PATH": "Quant decision latest_brief.json path override",
    "MACROCOCKPIT_PARQUET_MACRO_DIR": "Macro parquet directory override",
    "MACROCOCKPIT_QUANT_LATEST_PATH": "Quant latest snapshot JSON path override",
    "MACROCOCKPIT_SECRETS_PATH": "Local YAML secrets file override",
    "MACROCOCKPIT_BTC_KLINES_PARQUET": "BTC klines parquet path override",
    "MACROCOCKPIT_PUBLIC_URL": "Public MacroCockpit base URL",
    "MACROCOCKPIT_SYNC_URL": "VPS public snapshot publish URL override",
    "MACROCOCKPIT_PORTFOLIO_URL": "VPS private portfolio sync URL override",
    "MACROCOCKPIT_INGEST_URL": "CN ingest endpoint URL for overseas split collector",
    "MACROCOCKPIT_ROLE": "HTTP server role (standalone | consumer)",
    "MACROCOCKPIT_ACCESS_PASSWORD": "Web login password for consumer/non-loopback deployments",
    "MACROCOCKPIT_READ_TOKEN": "Bearer token for READ_ONLY role",
    "MACROCOCKPIT_RW_TOKEN": "Bearer token for PORTFOLIO_RW role",
    "MACROCOCKPIT_SYNC_TOKEN": "Bearer token for SYNC_PUBLISH / ingest role",
    "MACROCOCKPIT_TRUST_PROXY": "Trust X-Forwarded-* headers behind reverse proxy when set to 1",
    "MACROCOCKPIT_LOCAL_REQUIRE_PASSWORD": "Require password even on loopback standalone when set to 1",
    "MACROCOCKPIT_DESKTOP_TOKEN": "Desktop Pro local loopback session token override",
    "MACROCOCKPIT_LOCAL_WEB_URL": "Desktop Pro local web URL override",
    "MACROCOCKPIT_MCP_URL": "MCP Streamable HTTP endpoint URL for stdio bridge",
    "MACROCOCKPIT_MCP_TOKEN": "MCP bearer token for stdio bridge",
    "MACROCOCKPIT_AGY_BIN": "Explicit AGY CLI executable path override",
    "AGY_HOME": "AGY installation directory override",
    "MACROCOCKPIT_STAFF_HUB": "Explicit staff-hub agy-companion.mjs path override",
    "OPENBB_API_URL": "Local OpenBB Platform REST API URL",
    "FRED_API_KEY": "FRED API key",
    "GEMINI_API_KEY": "Gemini API key (used only when explicitly enabled)",
    "NOTION_TOKEN": "Notion integration token",
    "RSSHUB_BASE_URL": "Self-hosted RSSHub base URL override",
    "AIHOT_ACTOR_ID": "Actor identifier sent to AIHOT API",
    "KAZIKE_LLM_JUDGE": "Enable optional LLM blind judge in kazike_engine",
    "KAZIKE_LLM_MAX_DAILY": "Daily call budget for optional kazike_engine LLM judge",
}


def resolve_cockpit_root() -> Path:
    env_root = (os.environ.get("MACROCOCKPIT_ROOT") or "").strip()
    if env_root:
        return Path(env_root).expanduser()
    return Path(__file__).resolve().parent.parent


def _is_default_root(root: Path | None) -> bool:
    if root is None:
        return True
    try:
        return os.path.normcase(str(Path(root).expanduser().resolve())) == os.path.normcase(
            str(resolve_cockpit_root().expanduser().resolve())
        )
    except OSError:
        return False


def resolve_data_dir(root: Path | None = None) -> Path:
    if _is_default_root(root):
        env_data = (os.environ.get("MACROCOCKPIT_DATA_DIR") or "").strip()
        if env_data:
            return Path(env_data).expanduser()
    return (Path(root) if root is not None else resolve_cockpit_root()) / "data"


def resolve_dist_dir(root: Path | None = None) -> Path:
    if _is_default_root(root):
        env_dist = (os.environ.get("MACROCOCKPIT_DIST_DIR") or "").strip()
        if env_dist:
            return Path(env_dist).expanduser()
    return (Path(root) if root is not None else resolve_cockpit_root()) / "dist"


def resolve_user_home(
    fallback: Path | None = None,
    *,
    path_cls: Any = Path,
    os_mod: Any = os,
) -> Path:
    """Resolve user home across platforms, guarding against Windows SYSTEM service profiles."""
    env_home = getattr(os_mod, "environ", os.environ).get("MACROCOCKPIT_USER_HOME", "").strip()
    if env_home:
        return path_cls(env_home).expanduser()
    try:
        home = path_cls.home()
    except RuntimeError:
        home = fallback if fallback is not None else resolve_cockpit_root()
    if getattr(os_mod, "name", os.name) == "nt" and "systemprofile" in str(home).lower():
        users_root = path_cls("C:/Users")
        if users_root.is_dir():
            for cand in sorted(users_root.iterdir()):
                if (
                    cand.is_dir()
                    and cand.name.lower() not in ("public", "default", "default user", "all users")
                    and (cand / "AppData" / "Local").is_dir()
                ):
                    return cand
    return home


def resolve_hermes_home(
    user_home: Path | None = None,
    *,
    path_cls: Any = Path,
    os_mod: Any = os,
) -> Path:
    """Resolve Hermes home directory across Windows and POSIX platforms."""
    env_hermes = getattr(os_mod, "environ", os.environ).get("HERMES_HOME", "").strip()
    if env_hermes:
        return path_cls(env_hermes).expanduser()
    home = user_home or resolve_user_home(path_cls=path_cls, os_mod=os_mod)
    win_hermes = home / "AppData" / "Local" / "hermes"
    if win_hermes.exists() or getattr(os_mod, "name", os.name) == "nt":
        return win_hermes
    return home / ".hermes"


def resolve_kol_dir(hermes_home: Path | None = None) -> Path:
    env_kol = os.environ.get("MACROCOCKPIT_KOL_DIR", "").strip()
    if env_kol:
        return Path(env_kol).expanduser()
    hh = hermes_home or resolve_hermes_home()
    return hh / "scripts" / "news" / "x-kol-notes"


COCKPIT_ROOT: Path = resolve_cockpit_root()
DATA_DIR: Path = resolve_data_dir(COCKPIT_ROOT)
DIST_DIR: Path = resolve_dist_dir(COCKPIT_ROOT)
USER_HOME: Path = resolve_user_home(COCKPIT_ROOT)
HERMES_HOME: Path = resolve_hermes_home(USER_HOME)


def now_utc() -> datetime:
    return datetime.now(UTC)


def now_bjt() -> datetime:
    return datetime.now(BJT)


def now_bjt_iso(*, sep: str = "T", timespec: str = "seconds") -> str:
    return now_bjt().isoformat(sep=sep, timespec=timespec)


def now_bjt_minute_str() -> str:
    return datetime.now(BJT).strftime("%Y-%m-%d %H:%M")


def parse_iso_dt(raw: Any, *, default_tz: timezone = BJT) -> datetime | None:
    s = str(raw or "").strip()
    if not s:
        return None
    try:
        norm = s[:-1] + "+00:00" if s.endswith("Z") else s
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}", norm):
            norm = norm.replace(" ", "T") + ":00+08:00"
        elif re.fullmatch(r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}", norm):
            norm = norm.replace(" ", "T") + "+08:00"
        elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", norm):
            norm = norm + "T00:00:00+08:00"
        dt = datetime.fromisoformat(norm)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=default_tz)
        return dt.astimezone(BJT)
    except ValueError:
        return None


def resolve_bjt_datetime(as_of: datetime | date | str | None = None) -> datetime:
    if as_of is None:
        return now_bjt()
    if isinstance(as_of, datetime):
        return as_of.astimezone(BJT) if as_of.tzinfo is not None else as_of.replace(tzinfo=BJT)
    if isinstance(as_of, date):
        return datetime(as_of.year, as_of.month, as_of.day, 9, 0, tzinfo=BJT)
    dt = parse_iso_dt(as_of, default_tz=BJT)
    if dt is None:
        raise ValueError(f"Invalid datetime value: {as_of!r}")
    return dt


def format_bjt_iso(ts: str | None) -> str:
    if not ts:
        return ""
    dt = parse_iso_dt(ts, default_tz=BJT)
    if dt is not None:
        return dt.isoformat(timespec="seconds")
    return str(ts).strip()


_FILE_LOCK_LOCAL = threading.local()
_FILE_LOCK_GUARD = threading.Lock()
_FILE_LOCK_RLOCKS: dict[str, threading.RLock] = {}


def _get_thread_rlock(key: str) -> threading.RLock:
    with _FILE_LOCK_GUARD:
        lk = _FILE_LOCK_RLOCKS.get(key)
        if lk is None:
            lk = threading.RLock()
            _FILE_LOCK_RLOCKS[key] = lk
        return lk


@contextmanager
def file_lock(path: Path) -> Iterator[None]:
    """Serialize readers and writers across threads and processes via a stable sidecar lock (thread-reentrant)."""
    lock_path = path.with_name(f".{path.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    key = os.path.normcase(str(lock_path.resolve()))
    held = getattr(_FILE_LOCK_LOCAL, "held", None)
    if held is None:
        held = {}
        _FILE_LOCK_LOCAL.held = held

    if held.get(key, 0) > 0:
        held[key] += 1
        try:
            yield
        finally:
            held[key] -= 1
            if held[key] <= 0:
                held.pop(key, None)
        return

    t_lock = _get_thread_rlock(key)
    with t_lock:
        with lock_path.open("a+b") as lock:
            if os.name == "nt":
                import msvcrt

                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            held[key] = 1
            try:
                yield
            finally:
                held.pop(key, None)
                try:
                    if os.name == "nt":
                        lock.seek(0)
                        msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass


def atomic_write_text(dest: Path, text: str, *, newline: str = "") -> None:
    """Atomically write UTF-8 text via a pid+uuid temporary file and os.replace."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.{os.getpid()}.{uuid.uuid4().hex[:6]}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8", newline=newline)
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)


def reject_nonfinite_json(obj: Any, path: str = "root") -> Any:
    """Reject NaN/Inf floats recursively before strict RFC 8259 JSON serialization."""
    if isinstance(obj, float):
        if not math.isfinite(obj):
            raise ValueError(f"Non-finite float at {path}: {obj!r}")
        return obj
    if isinstance(obj, dict):
        return {k: reject_nonfinite_json(v, f"{path}.{k}") for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [reject_nonfinite_json(item, f"{path}[{idx}]") for idx, item in enumerate(obj)]
    return obj


def reject_json_constant(value: str) -> None:
    """Reject non-standard constants through json.loads(parse_constant=...)."""
    raise ValueError(f"Invalid JSON constant: {value}")


def atomic_write_json(
    path: Path,
    payload: Any,
    *,
    indent: int | None = 2,
    check_finite: bool = False,
    separators: tuple[str, str] | None = None,
) -> None:
    """Atomically write JSON with UTF-8 encoding and LF newlines."""
    if check_finite:
        reject_nonfinite_json(payload)
    kw: dict[str, Any] = {"ensure_ascii": False}
    if indent is not None:
        kw["indent"] = indent
    if separators is not None:
        kw["separators"] = separators
    if check_finite:
        kw["allow_nan"] = False
    raw = json.dumps(payload, **kw) + "\n"
    atomic_write_text(path, raw, newline="\n")


def read_json_safe(path: Path, default: Any = None) -> Any:
    """Read UTF-8 JSON if present and valid, returning default on missing/corrupt files."""
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def is_finite(val: Any) -> bool:
    if val is None or isinstance(val, bool):
        return False
    try:
        return math.isfinite(float(val))
    except (TypeError, ValueError):
        return False


def finite_float(val: Any, default: float | None = None) -> float | None:
    if val is None or isinstance(val, bool):
        return default
    try:
        f = float(val)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError, OverflowError):
        return default


def safe_float(val: Any) -> float | None:
    return finite_float(val, default=None)


def require_finite_float(value: Any, default: float = 0.0) -> float:
    """Coerce a journal number, rejecting invalid values before writing."""
    if isinstance(value, bool):
        raise ValueError("number cannot be boolean")
    num = finite_float(default if value in (None, "") else value)
    if num is None:
        raise ValueError("number must be finite")
    return num


def clamp(val: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, val))
