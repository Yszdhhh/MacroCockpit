"""MacroCockpit HTTP server, V2 portfolio/signal/sync APIs, MCP endpoint, and public-only offline HTML bundler."""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET
import zlib
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

_BACKEND_DIR = Path(__file__).resolve().parent
if str(_BACKEND_DIR.parent) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR.parent))

from backend.curation import rank_curated_signals
from backend.fred_and_market import COCKPIT_ROOT, DATA_DIR, atomic_write_json
from backend.import_positions import PreviewExpiredError, PreviewValidationError, commit_import, preview_import
from backend.journal_store import RevisionConflictError
from backend.mcp_server import (
    _filter_by_time_window,
    _origin_allowed,
    handle_mcp_http_request,
    sanitize_public_signal,
)
from backend.mobile_push import build_muse_markdown_digest, dispatch_mobile_push, load_push_env
from backend.portfolio_store import ConstraintViolationError, IdempotencyConflictError, PortfolioStore
from backend.public_snapshot import (
    file_lock,
    load_published_generation,
    lookup_published_article,
    project_public_state,
    publish_generation,
    safe_article_filename,
)

try:
    from backend.signal_aggregator import (
        STATE_OUTPUT_PATH,
        get_allowed_article_dirs,
        parse_markdown_article,
    )
except ImportError:
    STATE_OUTPUT_PATH = DATA_DIR / "cockpit_state.json"

    def get_allowed_article_dirs() -> list[tuple[Path, str]]:
        return [(COCKPIT_ROOT / "articles", "同步深度长文")]

    def parse_markdown_article(path: Path, source_label: str) -> dict[str, Any] | None:
        if not path.exists() or path.suffix.lower() != ".md" or "异常告警" in path.name:
            return None
        body_md = path.read_text(encoding="utf-8", errors="replace").strip()
        title = path.stem
        for ln in body_md.splitlines():
            s = ln.strip()
            if s.startswith("# "):
                title = s[2:].strip()
                break
        return {
            "filename": path.name,
            "title": title,
            "source": source_label,
            "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(path.stat().st_mtime)),
            "excerpt": title,
            "body_md": body_md,
        }

WEB_DIR = COCKPIT_ROOT / "web"
DIST_DIR = COCKPIT_ROOT / "dist"
OFFLINE_HTML_PATH = DIST_DIR / "macro_cockpit_offline.html"

MAX_JSON_BODY_BYTES = 2_000_000
MAX_SYNC_BODY_BYTES = 16 * 1024 * 1024
MAX_LOGIN_FORM_BYTES = 65_536

_REFRESH_LOCK = threading.Lock()
_INGEST_MERGE_LOCK = threading.Lock()


class _HttpError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _resolve_base_dir(state_path: Path) -> Path:
    sp = Path(state_path)
    if sp.parent.name == "data":
        return sp.parent.parent
    return sp.parent


def _load_or_build_state(
    state_path: Path = STATE_OUTPUT_PATH,
    *,
    role: str = "standalone",
    base_dir: Path | None = None,
) -> dict[str, Any]:
    root_dir = Path(base_dir) if base_dir is not None else _resolve_base_dir(state_path)
    # 1. Prefer published current_generation manifest when present
    manifest_path = root_dir / "data" / "current_generation.json"
    if manifest_path.is_file():
        loaded = load_published_generation(root_dir)
        return dict(loaded.get("state") or {})

    # 2. Fall back to explicit state_path if present
    if state_path.is_file():
        raw = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("snapshot must be a JSON object")
        return raw

    # 3. Secondary fallback for nested data/cockpit_state.json
    nested_state = root_dir / "data" / "cockpit_state.json"
    if nested_state != state_path and nested_state.is_file():
        raw = json.loads(nested_state.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("snapshot must be a JSON object")
        return raw

    # 4. Consumer mode NEVER invokes signal_aggregator
    if role == "consumer":
        raise FileNotFoundError("No published cockpit snapshot available in consumer mode")

    with _REFRESH_LOCK:
        from backend.signal_aggregator import generate_cockpit_state

        try:
            return generate_cockpit_state(refresh_fred=False, output_path=state_path)
        except (ImportError, RuntimeError):
            return {
                "generated_at_bjt": "",
                "risk_gate": {},
                "positions": [],
                "macro_fred": [],
                "us_equities": {},
                "signals": [],
                "kol_digest": [],
                "articles": [],
                "push_channels": {},
            }


def _read_health_manifest_only(
    state_path: Path,
    *,
    role: str,
    base_dir: Path,
    auth_enabled: bool,
) -> tuple[int, dict[str, Any]]:
    """Read only manifest/file metadata without invoking signal_aggregator."""
    manifest_path = base_dir / "data" / "current_generation.json"
    if manifest_path.is_file():
        try:
            mf = json.loads(manifest_path.read_text(encoding="utf-8"))
            gen = int(mf.get("generation") or 0)
            gen_state = base_dir / "data" / "generations" / f"gen_{gen}" / "state.json"
            if gen > 0 and gen_state.is_file():
                return (
                    200,
                    {
                        "ok": True,
                        "role": role,
                        "auth_enabled": auth_enabled,
                        "generation": gen,
                        "generated_at_bjt": str(mf.get("generated_at") or ""),
                        "published_at": str(mf.get("published_at") or ""),
                    },
                )
        except (OSError, ValueError, TypeError):
            pass

    for candidate in (state_path, base_dir / "data" / "cockpit_state.json"):
        if candidate.is_file():
            try:
                raw = json.loads(candidate.read_text(encoding="utf-8"))
                return (
                    200,
                    {
                        "ok": True,
                        "role": role,
                        "auth_enabled": auth_enabled,
                        "generation": int(raw.get("generation") or 0),
                        "generated_at_bjt": str(raw.get("generated_at_bjt") or ""),
                        "published_at": "",
                    },
                )
            except (OSError, ValueError, TypeError):
                pass

    if role == "consumer":
        return (
            503,
            {
                "ok": False,
                "role": role,
                "auth_enabled": auth_enabled,
                "error": "no published snapshot available",
            },
        )
    return (
        200,
        {
            "ok": True,
            "role": role,
            "auth_enabled": auth_enabled,
            "generation": 0,
            "generated_at_bjt": "",
            "published_at": "",
        },
    )


def _lookup_article_strict(filename: str) -> dict[str, Any] | tuple[int, str]:
    """Strict basename-only article lookup inside allowed directories."""
    if not filename or "\x00" in filename:
        return (HTTPStatus.BAD_REQUEST, "缺少有效文件名")
    if "/" in filename or "\\" in filename or ".." in filename or Path(filename).name != filename:
        return (HTTPStatus.FORBIDDEN, "非法文件路径请求")
    if not filename.lower().endswith(".md") or "异常告警" in filename:
        return (HTTPStatus.FORBIDDEN, "不支持访问该文件类型")

    for directory, src_label in get_allowed_article_dirs():
        if not directory.exists():
            continue
        dir_resolved = directory.resolve()
        candidate = (dir_resolved / filename).resolve()
        if candidate.parent != dir_resolved:
            return (HTTPStatus.FORBIDDEN, "路径越界被拒绝")
        if candidate.is_file():
            art = parse_markdown_article(candidate, src_label)
            if art:
                return art
    return (HTTPStatus.NOT_FOUND, "未找到对应深度文章")


def build_static_bundle(
    *,
    web_dir: Path = WEB_DIR,
    state_path: Path = STATE_OUTPUT_PATH,
    out_path: Path = OFFLINE_HTML_PATH,
) -> Path:
    """Bundle web/index.html, styles.css, app.js, and public-only state into single offline HTML (never includes journal)."""
    index_path = web_dir / "index.html"
    css_path = web_dir / "styles.css"
    js_path = web_dir / "app.js"
    if not index_path.exists():
        raise FileNotFoundError(f"Frontend entrypoint missing: {index_path}")

    raw_state = _load_or_build_state(state_path, role="standalone")
    state = project_public_state(raw_state, include_candidates=False)
    html_src = index_path.read_text(encoding="utf-8")
    css_src = css_path.read_text(encoding="utf-8") if css_path.exists() else ""
    js_src = js_path.read_text(encoding="utf-8") if js_path.exists() else ""

    state_json = (
        json.dumps(state, ensure_ascii=False)
        .replace("</", "<\\/")
        .replace("<!--", "<\\!--")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )
    init_script = f"<script>\nwindow.__INITIAL_COCKPIT_STATE__ = {state_json};\n</script>"

    css_block = f"<style>\n{css_src}\n</style>"
    if "<!-- INLINE_CSS -->" in html_src:
        html_src = html_src.replace("<!-- INLINE_CSS -->", css_block, 1)
    elif re.search(r'<link[^>]+href=["\'](?:\./)?styles\.css(?:\?[^"\']*)?["\'][^>]*>', html_src, flags=re.IGNORECASE):
        html_src = re.sub(
            r'<link[^>]+href=["\'](?:\./)?styles\.css(?:\?[^"\']*)?["\'][^>]*>',
            lambda _m: css_block,
            html_src,
            count=1,
            flags=re.IGNORECASE,
        )
    else:
        html_src = html_src.replace("</head>", f"{css_block}\n</head>", 1)

    html_src = re.sub(r'<link[^>]+rel=["\']manifest["\'][^>]*>\s*', "", html_src, flags=re.IGNORECASE)
    html_src = re.sub(
        r'<link[^>]+href=["\']https://fonts\.(?:googleapis|gstatic)\.com[^"\']*["\'][^>]*>\s*',
        "",
        html_src,
        flags=re.IGNORECASE,
    )

    icon_path = web_dir / "icon.svg"
    if icon_path.exists():
        svg_encoded = urllib.parse.quote(icon_path.read_text(encoding="utf-8"), safe="")
        html_src = re.sub(
            r'href=["\'](?:\./)?icon\.svg["\']',
            lambda _m: f'href="data:image/svg+xml,{svg_encoded}"',
            html_src,
            flags=re.IGNORECASE,
        )

    safe_js = js_src.replace("</script>", "<\\/script>")
    js_bundle = f"{init_script}\n<script>\n{safe_js}\n</script>"
    if "<!-- INLINE_JS -->" in html_src:
        html_src = html_src.replace("<!-- INLINE_JS -->", js_bundle, 1)
    elif re.search(
        r'<script[^>]+src=["\'](?:\./)?app\.js(?:\?[^"\']*)?["\'][^>]*>\s*</script>',
        html_src,
        flags=re.IGNORECASE,
    ):
        html_src = re.sub(
            r'<script[^>]+src=["\'](?:\./)?app\.js(?:\?[^"\']*)?["\'][^>]*>\s*</script>',
            lambda _m: js_bundle,
            html_src,
            count=1,
            flags=re.IGNORECASE,
        )
    else:
        html_src = html_src.replace("</body>", f"{js_bundle}\n</body>", 1)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".html.tmp")
    tmp.write_text(html_src, encoding="utf-8")
    os.replace(tmp, out_path)
    return out_path


def _const_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(str(a or "").encode("utf-8"), str(b or "").encode("utf-8"))


def _xml_safe(value: Any) -> str:
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(value or ""))


def _decompress_gzip_bounded(raw: bytes, max_bytes: int) -> bytes:
    dec = zlib.decompressobj(16 + zlib.MAX_WBITS)
    chunks: list[bytes] = []
    total = 0
    view = memoryview(raw)
    step = 65536
    for idx in range(0, len(view), step):
        part = dec.decompress(view[idx : idx + step], max_bytes - total + 1)
        total += len(part)
        if total > max_bytes:
            raise _HttpError(413, "Decompressed payload exceeds 16 MiB limit")
        chunks.append(part)
    tail = dec.flush(max_bytes - total + 1)
    total += len(tail)
    if total > max_bytes:
        raise _HttpError(413, "Decompressed payload exceeds 16 MiB limit")
    chunks.append(tail)
    return b"".join(chunks)


class CockpitHTTPServer(ThreadingHTTPServer):
    @property
    def _revoked_cookies(self) -> dict[str, int]:
        with self.journal_store._connect("read") as db:
            return dict(db.execute("SELECT tok_hash, exp_ts FROM auth_revoked_cookies WHERE exp_ts>?", (int(time.time()),)))


class CockpitRequestHandler(BaseHTTPRequestHandler):
    server_version = "MacroCockpitHTTP/2.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def _send_bytes(
        self,
        status: int,
        data: bytes,
        ctype: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        if ctype:
            self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if data:
            self.wfile.write(data)

    def _send_json(self, status: int, payload: Any, headers: dict[str, str] | None = None) -> None:
        self._send_bytes(
            status,
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
            headers,
        )

    def _parse_content_length(self) -> int:
        raw_cl = (self.headers.get("Content-Length") or "").strip()
        if not raw_cl:
            return 0
        try:
            n = int(raw_cl)
        except ValueError:
            raise _HttpError(400, "Invalid Content-Length header")
        if n < 0:
            raise _HttpError(400, "Invalid negative Content-Length header")
        return n

    def _read_raw_body(self, *, max_bytes: int, allow_gzip: bool = False) -> bytes:
        n = self._parse_content_length()
        if n > max_bytes:
            raise _HttpError(413, f"Payload size {n} exceeds limit of {max_bytes} bytes")
        raw = self.rfile.read(n) if n > 0 else b""
        enc = (self.headers.get("Content-Encoding") or "").strip().lower()
        if enc:
            if enc == "gzip" and allow_gzip:
                try:
                    return _decompress_gzip_bounded(raw, max_bytes)
                except _HttpError:
                    raise
                except zlib.error:
                    raise _HttpError(400, "Invalid gzip payload")
            raise _HttpError(415, f"Unsupported Content-Encoding: {enc}")
        return raw

    def _read_json_body(
        self,
        max_bytes: int = MAX_JSON_BODY_BYTES,
        *,
        allow_gzip: bool = False,
        allow_empty_no_ctype: bool = False,
    ) -> dict[str, Any]:
        ctype_raw = (self.headers.get("Content-Type") or "").strip()
        ctype = ctype_raw.split(";", 1)[0].strip().lower()
        n = self._parse_content_length()
        if n > max_bytes:
            raise _HttpError(413, f"Payload size {n} exceeds limit of {max_bytes} bytes")
        if ctype != "application/json":
            if allow_empty_no_ctype and n == 0 and not ctype:
                return {}
            raise _HttpError(415, "Content-Type must be application/json")
        raw = self._read_raw_body(max_bytes=max_bytes, allow_gzip=allow_gzip)
        if not raw:
            return {}
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise _HttpError(400, "Invalid UTF-8 JSON payload")
        if not isinstance(parsed, dict):
            raise _HttpError(400, "JSON object required")
        return parsed

    def _key(self) -> bytes:
        material = f"{self.server.access_password}|{self.server.read_token}|{self.server.rw_token}|MacroCockpit-auth-v2"
        return hmac.digest(self.server.cookie_key, material.encode("utf-8"), "sha256")

    def _client_ip(self) -> str:
        peer_ip = self.client_address[0] if self.client_address else "unknown"
        trust_proxy = os.environ.get("MACROCOCKPIT_TRUST_PROXY", "").strip() == "1"
        if trust_proxy:
            cf = (self.headers.get("CF-Connecting-IP") or "").strip()
            if cf:
                return cf[:128]
            xff = (self.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
            if xff:
                return xff[:128]
        return str(peer_ip)

    def _host_allowed(self, *, local_only: bool = False) -> bool:
        raw_host = (self.headers.get("Host") or "").strip().lower()
        if not raw_host:
            return True
        if raw_host.startswith("["):
            end = raw_host.find("]")
            if end == -1:
                return False
            host_name = raw_host[1:end]
        elif raw_host.count(":") == 1:
            host_name = raw_host.split(":", 1)[0]
        else:
            host_name = raw_host
        allowed = {"127.0.0.1", "localhost", "::1", "::ffff:127.0.0.1"}
        if local_only:
            return host_name in allowed
        allowed.add("testserver")
        try:
            pub_host = (urllib.parse.urlparse(self.server.public_url).hostname or "").strip().lower()
            if pub_host:
                allowed.add(pub_host)
        except ValueError:
            pass
        return host_name in allowed

    def _apply_v1_journal(
        self,
        op: dict[str, Any],
        auth: dict[str, Any],
        action: str,
    ) -> dict[str, Any] | None:
        result = self.server.portfolio_store.apply_changes(
            request_id=f"v1:{secrets.token_hex(16)}",
            expected_revision=None,
            ops=[op],
            actor_id=auth["actor_id"],
            role=auth["role"],
            summary=action,
            _v1_action=action,
        )
        return result["items"][0] if result["items"] else None

    def _raw_cookie(self) -> str:
        for part in (self.headers.get("Cookie") or "").split(";"):
            s = part.strip()
            if s.startswith("macro_auth="):
                return s[len("macro_auth=") :]
        return ""

    def _cookie_ok(self) -> bool:
        raw = self._raw_cookie()
        if not raw:
            return False
        parts = raw.split(".")
        if len(parts) != 3:
            return False
        exp_s, jti, sig = parts
        signed_payload = f"{exp_s}.{jti}"
        try:
            exp = int(exp_s)
        except ValueError:
            return False
        now_i = int(time.time())
        if exp <= now_i:
            return False
        expected_sig = hmac.new(self._key(), signed_payload.encode("utf-8"), hashlib.sha256).hexdigest()
        if not _const_eq(sig, expected_sig):
            return False
        tok_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        with self.server.journal_store._connect("read") as db:
            revoked = db.execute("SELECT 1 FROM auth_revoked_cookies WHERE tok_hash=?", (tok_hash,)).fetchone()
        return revoked is None

    def _resolve_bearer_token(self, token: str) -> dict[str, Any] | None:
        cand = str(token or "").strip()
        if not cand:
            return None
        info = self.server.portfolio_store.verify_token(cand)
        if info is not None:
            return {
                "kind": "bearer",
                "role": str(info.get("role") or ""),
                "actor_id": str(info.get("actor_id") or "cred:db"),
            }
        # Explicit server-configured tokens (constant-time)
        specs = (
            (self.server.rw_token, "PORTFOLIO_RW", "config:portfolio_rw"),
            (self.server.sync_token, "SYNC_PUBLISH", "config:sync_publish"),
            (self.server.read_token, "READ_ONLY", "config:read_only"),
        )
        for expected, role, actor in specs:
            exp_s = str(expected or "").strip()
            if exp_s and _const_eq(cand, exp_s):
                return {"kind": "bearer", "role": role, "actor_id": actor}
        return None

    def _authenticate(self, *, allow_query_token: bool = False) -> dict[str, Any] | None:
        auth_hdr = (self.headers.get("Authorization") or "").strip()
        if auth_hdr.startswith("Bearer "):
            resolved = self._resolve_bearer_token(auth_hdr[7:].strip())
            if resolved is not None:
                return resolved
            return None
        if allow_query_token:
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            qtok = (q.get("token") or [""])[0].strip()
            if qtok:
                resolved = self._resolve_bearer_token(qtok)
                if resolved is not None and resolved["role"] == "READ_ONLY":
                    return resolved
                return None
        if self._cookie_ok():
            return {"kind": "cookie", "role": "PORTFOLIO_RW", "actor_id": "owner:cookie"}
        if (
            not self.server.access_password
            and not auth_hdr
            and self.client_address[0] in ("127.0.0.1", "::1", "::ffff:127.0.0.1")
            and self._host_allowed(local_only=True)
            and not any(header in self.headers for header in ("CF-Connecting-IP", "X-Forwarded-For"))
        ):
            return {"kind": "local_open", "role": "PORTFOLIO_RW", "actor_id": "local:standalone"}
        return None

    def _check_same_origin_header(self) -> bool:
        host_hdr = (self.headers.get("Host") or "").strip()
        origin = (self.headers.get("Origin") or "").strip()
        if origin:
            return _origin_allowed(origin, host_header=host_hdr, public_url=self.server.public_url)
        referer = (self.headers.get("Referer") or "").strip()
        if referer:
            try:
                u = urllib.parse.urlparse(referer)
                ref_origin = f"{u.scheme}://{u.netloc}" if u.scheme and u.netloc else referer
            except ValueError:
                return False
            return _origin_allowed(ref_origin, host_header=host_hdr, public_url=self.server.public_url)
        return True

    def _enforce_write_csrf(self, auth: dict[str, Any]) -> bool:
        """Enforce Same-Origin and X-Macro-Request: 1 on Cookie-authenticated writes."""
        if auth.get("kind") == "bearer":
            return True
        if not self._check_same_origin_header():
            self._send_json(403, {"error": "CSRF 校验失败：非法跨站 Origin/Referer"})
            return False
        if auth.get("kind") == "cookie" or self.headers.get("Origin") or self.headers.get("Referer"):
            if (self.headers.get("X-Macro-Request") or "").strip() != "1":
                self._send_json(403, {"error": "CSRF 校验失败：缺少 X-Macro-Request: 1 请求头"})
                return False
        return True

    def _state(self) -> dict[str, Any]:
        try:
            return _load_or_build_state(
                self.server.state_path,
                role=self.server.role,
                base_dir=self.server.base_dir,
            )
        except ValueError as exc:
            raise _HttpError(503, "已发布快照无效") from exc

    def _snap(self) -> dict[str, Any]:
        return self.server.journal_store.get_snapshot(self._state())

    def _article(self, name: str) -> dict[str, Any] | tuple[int, str]:
        safe_name = safe_article_filename(name)
        if not safe_name:
            if not name or "\x00" in name:
                return (HTTPStatus.BAD_REQUEST, "缺少有效文件名")
            return (HTTPStatus.FORBIDDEN, "非法文件路径或文件名")

        # 1. Manifest-verified published article lookup first
        pub = lookup_published_article(self.server.base_dir, safe_name)
        if pub is not None:
            body_md = str(pub.get("content") or "")
            title = safe_name[:-3]
            for ln in body_md.splitlines():
                s = ln.strip()
                if s.startswith("# "):
                    title = s[2:].strip()
                    break
            meta_entry: dict[str, Any] = {}
            try:
                for a in self._state().get("articles") or []:
                    if isinstance(a, dict) and a.get("filename") == safe_name:
                        meta_entry = a
                        break
            except Exception:
                pass
            fallback_src = "同步深度长文"
            lower_name = safe_name.lower()
            if lower_name.startswith(("weekly_", "monthly_", "dr_")):
                fallback_src = "周月度深研"
            elif lower_name.startswith("ta_"):
                fallback_src = "多智能体深研"
            elif lower_name.startswith("guru_"):
                fallback_src = "大佬分享"
            return {
                "filename": safe_name,
                "title": str(meta_entry.get("title") or title),
                "source": str(meta_entry.get("source") or fallback_src),
                "mtime": str(meta_entry.get("mtime") or ""),
                "excerpt": str(meta_entry.get("excerpt") or title),
                "body_md": body_md,
            }

        # 2. Consumer mode strictly reads published snapshots/articles only
        if self.server.role == "consumer":
            if (self.server.base_dir / "data" / "current_generation.json").is_file():
                return (HTTPStatus.NOT_FOUND, "未找到对应深度文章")
            for folder in (self.server.articles_dir, self.server.base_dir / "articles"):
                if not folder.exists():
                    continue
                root = folder.resolve()
                f = (root / safe_name).resolve()
                if f.parent == root and f.is_file():
                    a = parse_markdown_article(f, "同步深度长文")
                    if a:
                        return a
            try:
                for a in self._state().get("articles") or []:
                    if isinstance(a, dict) and a.get("filename") == safe_name:
                        return a
            except Exception:
                pass
            return (HTTPStatus.NOT_FOUND, "未找到对应深度文章")

        # 3. Standalone fallback across local allowed article dirs
        r = _lookup_article_strict(safe_name)
        if not isinstance(r, tuple) or r[0] != 404:
            return r
        for folder, label in [(self.server.articles_dir, "同步深度长文"), *get_allowed_article_dirs()]:
            if not folder.exists():
                continue
            root = folder.resolve()
            f = (root / safe_name).resolve()
            if f.parent == root and f.is_file():
                a = parse_markdown_article(f, label)
                if a:
                    return a
        try:
            for a in self._state().get("articles") or []:
                if isinstance(a, dict) and a.get("filename") == safe_name:
                    return a
        except Exception:
            pass
        return r

    def _is_cookie_secure(self) -> bool:
        if (self.headers.get("X-Forwarded-Proto") or "").strip().lower() == "https":
            return True
        host_hdr = (self.headers.get("Host") or "").strip().lower()
        host_name = host_hdr.split(":", 1)[0]
        return bool(
            self.server.public_url.startswith("https://")
            and host_name not in ("", "127.0.0.1", "localhost", "[::1]", "::1")
        )

    def _require_role(self, auth: dict[str, Any] | None, allowed: tuple[str, ...]) -> bool:
        if auth is None:
            self._send_json(401, {"error": "需要登录或有效令牌"})
            return False
        if auth.get("role") not in allowed:
            self._send_json(403, {"error": "当前令牌角色权限不足"})
            return False
        return True

    def do_GET(self) -> None:
        try:
            self._handle_get()
        except _HttpError as exc:
            self._send_json(exc.status, {"error": exc.message})
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})

    def _handle_get(self) -> None:
        if not self._host_allowed():
            self._send_json(403, {"error": "Forbidden Host"})
            return
        u = urllib.parse.urlparse(self.path)
        path = u.path

        if path == "/mcp":
            self._send_json(405, {"error": "Method not allowed; use POST /mcp"}, {"Allow": "POST"})
            return

        if path in ("/health", "/api/health"):
            code, payload = _read_health_manifest_only(
                self.server.state_path,
                role=self.server.role,
                base_dir=self.server.base_dir,
                auth_enabled=bool(self.server.access_password),
            )
            self._send_json(code, payload)
            return

        if path == "/login":
            if not self.server.access_password and self._authenticate(allow_query_token=False) is not None:
                self.send_response(302)
                self.send_header("Location", "/")
                self.end_headers()
                return
            page = (
                '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width,initial-scale=1">'
                '<title>登录 · 宏观决策情报站</title>'
                '<style>'
                ':root{--bg:#FAFAF9;--surface:#FFFFFF;--ink:#16140F;--sub:#6E675B;--rule:#E9E5DC;--accent:#0E6E6E;--bear:#D6453D}'
                '@media (prefers-color-scheme:dark){:root{--bg:#100F09;--surface:#1A1915;--ink:#F5F4EF;--sub:#A39B8B;--rule:rgba(255,255,255,.12);--accent:#12CCD8;--bear:#F87171}}'
                'body{min-height:100vh;display:grid;place-items:center;margin:0;background:var(--bg);color:var(--ink);font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif}'
                '.card{width:min(390px,calc(100vw - 32px));background:var(--surface);border:1px solid var(--rule);border-radius:14px;padding:30px 28px;box-sizing:border-box}'
                '.eyebrow{font-size:12px;color:var(--accent);font-weight:600;letter-spacing:.06em;margin-bottom:6px}'
                'h1{margin:0 0 6px;font-size:22px;font-family:Georgia,"Noto Serif SC",serif}'
                'p{margin:0 0 20px;color:var(--sub);font-size:13.5px}'
                'label{display:block;font-size:13px;font-weight:500;margin-bottom:6px}'
                'input{width:100%;box-sizing:border-box;padding:11px 12px;border:1px solid var(--rule);border-radius:8px;background:var(--bg);color:var(--ink);font:inherit;outline:none}'
                'input:focus{border-color:var(--accent)}'
                'button{width:100%;margin-top:14px;padding:11px 14px;border:0;border-radius:8px;background:var(--accent);color:#fff;font:inherit;font-weight:600;cursor:pointer}'
                '@media (prefers-color-scheme:dark){button{color:#0b1718}}'
                '.err{margin-top:12px;color:var(--bear);font-size:13px;min-height:1.2em}'
                '</style></head><body><main class="card">'
                '<div class="eyebrow">NEWS.SHUASHUABAO.XYZ</div>'
                '<h1>宏观决策情报站</h1><p>输入访问密码以查看日报、相关性、KOL 树与私人复盘库</p>'
                '<form method="post" action="/api/login"><label for="pw">访问密码</label>'
                '<input id="pw" name="password" type="password" autocomplete="current-password" required autofocus>'
                '<button type="submit">进入情报站</button></form>'
                '<div class="err" id="err"></div></main>'
                '<script>const qs=new URLSearchParams(location.search);if(qs.get("error")==="rate")document.getElementById("err").textContent="尝试过于频繁，请 5 分钟后再试。";else if(qs.get("error")==="1")document.getElementById("err").textContent="访问密码不正确，请重试。"</script>'
                '</body></html>'
            )
            self._send_bytes(200, page.encode("utf-8"), "text/html; charset=utf-8")
            return

        if path in ("/manifest.webmanifest", "/icon.svg"):
            self._serve_web_file(path)
            return

        v1_token_paths = {
            "/api/digest.md",
            "/feed.xml",
            "/feeds.opml",
            "/api/state",
            "/api/article",
            "/api/muse-spec.md",
            "/api/caffeine-brief.md",
            "/api/stock",
            "/api/stock/search",
            "/api/v2/signals",
        }
        allow_qtok = path in v1_token_paths
        auth = self._authenticate(allow_query_token=allow_qtok)

        if auth is None:
            if not path.startswith("/api/") and path not in ("/feed.xml", "/feeds.opml"):
                self.send_response(302)
                self.send_header("Location", "/login")
                self.end_headers()
            else:
                self._send_json(401, {"error": "需要登录或有效令牌"})
            return

        if auth.get("role") == "SYNC_PUBLISH":
            self._send_json(403, {"error": "SYNC_PUBLISH 令牌仅限发布同步接口"})
            return

        try:
            if path == "/api/state":
                raw_st = self._state()
                out = project_public_state(raw_st, include_candidates=False)
                if auth.get("kind") in ("cookie", "local_open"):
                    out["journal"] = self.server.journal_store.get_snapshot(out)
                else:
                    out.pop("journal", None)
                self._send_json(200, out)
                return

            if path == "/api/v2/workspace":
                if auth.get("role") != "PORTFOLIO_RW" and auth.get("kind") not in ("cookie", "local_open"):
                    self._send_json(403, {"error": "READ_ONLY 令牌无权访问私人持仓工作区"})
                    return
                q = urllib.parse.parse_qs(u.query, keep_blank_values=True)
                cur = (q.get("currency") or ["USD"])[0].strip().upper() or "USD"
                if cur not in ("USD", "CNY", "RMB"):
                    raise ValueError(f"unsupported display currency: {cur!r}")
                st = self._state()
                ws = self.server.portfolio_store.build_workspace(public_state=st, currency=cur)
                self._send_json(200, ws)
                return

            if path == "/api/v2/preferences":
                if auth.get("role") != "PORTFOLIO_RW" and auth.get("kind") not in ("cookie", "local_open"):
                    self._send_json(403, {"error": "READ_ONLY 令牌无权读取个人偏好与规则"})
                    return
                self._send_json(
                    200,
                    {
                        "revision": self.server.portfolio_store.get_revision(),
                        "preferences": self.server.portfolio_store.get_preferences(),
                        "curation_rules": self.server.portfolio_store.list_curation_rules(),
                    },
                )
                return

            if path == "/api/v2/signals":
                q = urllib.parse.parse_qs(u.query, keep_blank_values=True)
                scope = (q.get("scope") or ["all"])[0].strip().lower() or "all"
                category = (q.get("category") or ["all"])[0].strip().lower() or "all"
                subtopic = (q.get("subtopic") or [""])[0].strip() or None
                direction = (q.get("direction") or [""])[0].strip().lower()
                if direction == "all":
                    direction = ""
                instrument_id = (q.get("instrument_id") or [""])[0].strip().upper() or None
                event_id = (q.get("event_id") or [""])[0].strip() or None
                date_from = (q.get("from") or [""])[0].strip() or None
                date_to = (q.get("to") or [""])[0].strip() or None
                sort_mode = (q.get("sort") or ["importance"])[0].strip().lower() or "importance"
                if sort_mode not in ("importance", "time"):
                    self._send_json(400, {"error": "sort must be importance or time"})
                    return
                cursor_raw = (q.get("cursor") or [""])[0].strip() or None
                try:
                    limit = max(1, min(200, int((q.get("limit") or ["85"])[0])))
                    offset = max(0, int(cursor_raw)) if cursor_raw else 0
                except ValueError:
                    self._send_json(400, {"error": "invalid limit or cursor"})
                    return

                is_ro_bearer = auth.get("role") == "READ_ONLY"
                if is_ro_bearer and scope in ("held", "watch", "rules"):
                    self._send_json(
                        403,
                        {"error": "READ_ONLY 禁止筛选持仓、自选或规则专属范围"},
                    )
                    return

                st = self._state()
                pool = list(st.get("signal_candidates") or st.get("signals") or [])
                pool = _filter_by_time_window(pool, date_from, date_to)

                rules = None
                workspace = None
                if not is_ro_bearer:
                    rules = self.server.portfolio_store.list_curation_rules(enabled_only=True)
                    workspace = self.server.portfolio_store.build_workspace(public_state=st)

                matched = rank_curated_signals(
                    pool,
                    rules=rules,
                    workspace=workspace,
                    scope=scope,
                    category=category,
                    subtopic=subtopic,
                    direction=direction or None,
                    instrument_id=instrument_id,
                    event_id=event_id,
                    sort=sort_mode,
                    cursor=None,
                    limit=max(len(pool), 500),
                )
                total = len(matched)
                sliced = matched[offset : offset + limit]
                next_cursor = str(offset + limit) if (offset + limit) < total else None
                if is_ro_bearer:
                    sliced = [sanitize_public_signal(x) for x in sliced]

                self._send_json(
                    200,
                    {
                        "ok": True,
                        "generation": int(st.get("generation") or 0),
                        "scope": scope,
                        "category": category,
                        "subtopic": subtopic or "",
                        "sort": sort_mode,
                        "total": total,
                        "count": len(sliced),
                        "next_cursor": next_cursor,
                        "signals": sliced,
                    },
                )
                return

            if path == "/api/journal":
                if auth.get("role") != "PORTFOLIO_RW" and auth.get("kind") not in ("cookie", "local_open"):
                    self._send_json(403, {"error": "需要登录或 PORTFOLIO_RW 权限"})
                    return
                self._send_json(200, self._snap())
                return

            if path == "/api/article":
                name = (urllib.parse.parse_qs(u.query, keep_blank_values=True).get("file") or [""])[0]
                r = self._article(name)
                if isinstance(r, tuple):
                    self._send_json(r[0], {"error": r[1]})
                else:
                    self._send_json(200, r)
                return

            if path == "/api/stock/search":
                q_raw = (urllib.parse.parse_qs(u.query, keep_blank_values=True).get("q") or [""])[0].strip()
                if not q_raw or len(q_raw) > 40 or any(c in q_raw for c in ("/", "\\", "..", "<", ">", "\x00", "\n", "\r")):
                    self._send_json(400, {"error": "检索关键词无效"})
                    return
                from backend.stock_research import search_research_symbols

                self._send_json(200, {"items": search_research_symbols(q_raw, limit=8)})
                return

            if path == "/api/stock":
                raw_sym = (urllib.parse.parse_qs(u.query, keep_blank_values=True).get("symbol") or [""])[0].strip()
                if not raw_sym or len(raw_sym) > 32 or any(c in raw_sym for c in ("/", "\\", "..", "<", ">", "\x00", "\n", "\r")):
                    self._send_json(400, {"error": "标的代码格式无效"})
                    return
                from backend.stock_research import fetch_single_stock_profile, resolve_research_symbol

                st = self._state()
                profiles_map = st.get("stock_profiles") or {}
                prof = profiles_map.get(raw_sym.upper())
                if prof is None:
                    resolved = resolve_research_symbol(raw_sym, timeout=3)
                    if resolved is None:
                        self._send_json(404, {"error": "暂未识别该标的（支持 AAPL / 600519 / 00700.HK / BTC / 茅台）"})
                        return
                    canon_sym, _mkt = resolved
                    prof = profiles_map.get(canon_sym)
                    if prof is None:
                        try:
                            prof = fetch_single_stock_profile(canon_sym, risk_gate=st.get("risk_gate"))
                        except Exception:
                            prof = None
                if prof is None:
                    self._send_json(404, {"error": "暂未找到该标的投研数据"})
                    return
                self._send_json(200, prof)
                return

            if path == "/api/muse-spec.md":
                candidates = (
                    self.server.base_dir / "docs" / "MUSE_HANDOFF.md",
                    self.server.base_dir / "MUSE_HANDOFF.md",
                    COCKPIT_ROOT / "docs" / "MUSE_HANDOFF.md",
                    COCKPIT_ROOT / "MUSE_HANDOFF.md",
                )
                doc = next((p for p in candidates if p.is_file()), None)
                if doc is None:
                    self._send_json(404, {"error": "MUSE_HANDOFF.md not found"})
                    return
                self._send_bytes(200, doc.read_bytes(), "text/markdown; charset=utf-8")
                return

            if path == "/api/caffeine-brief.md":
                candidates = (
                    self.server.base_dir / "docs" / "CAFFEINE_FABLE_BRIEF.md",
                    self.server.base_dir / "CAFFEINE_FABLE_BRIEF.md",
                    COCKPIT_ROOT / "docs" / "CAFFEINE_FABLE_BRIEF.md",
                    COCKPIT_ROOT / "CAFFEINE_FABLE_BRIEF.md",
                )
                doc = next((p for p in candidates if p.is_file()), None)
                if doc is None:
                    self._send_json(404, {"error": "CAFFEINE_FABLE_BRIEF.md not found"})
                    return
                self._send_bytes(200, doc.read_bytes(), "text/markdown; charset=utf-8")
                return

            if path == "/feeds.opml":
                opml_candidates = (
                    self.server.base_dir / "dist" / "macro_cockpit_feeds.opml",
                    DIST_DIR / "macro_cockpit_feeds.opml",
                )
                opml_file = next((p for p in opml_candidates if p.is_file()), None)
                if opml_file is not None:
                    self._send_bytes(200, opml_file.read_bytes(), "application/xml; charset=utf-8")
                    return
                try:
                    from backend.rss_sources import build_opml_export

                    target_opml = opml_candidates[0]
                    target_opml.parent.mkdir(parents=True, exist_ok=True)
                    res = build_opml_export(output_path=target_opml)
                    opml_file = next((p for p in opml_candidates if p.is_file()), None)
                    if opml_file is not None:
                        self._send_bytes(200, opml_file.read_bytes(), "application/xml; charset=utf-8")
                        return
                    if isinstance(res, str) and res.lstrip().startswith("<"):
                        self._send_bytes(200, res.encode("utf-8"), "application/xml; charset=utf-8")
                        return
                    if isinstance(res, str) and Path(res).is_file():
                        self._send_bytes(200, Path(res).read_bytes(), "application/xml; charset=utf-8")
                        return
                except Exception:
                    pass
                self._send_json(503, {"error": "OPML feed export unavailable"})
                return

            if path == "/api/digest.md":
                try:
                    topic = (urllib.parse.parse_qs(u.query).get("topic") or ["all"])[0]
                    out_md = build_muse_markdown_digest(self._state(), topic, self.server.public_url)
                except ValueError as e:
                    self._send_json(400, {"error": str(e)})
                    return
                self._send_bytes(200, out_md.encode("utf-8"), "text/markdown; charset=utf-8")
                return

            if path == "/feed.xml":
                st = self._state()
                gen = _xml_safe(st.get("generated_at_bjt") or "")
                root = ET.Element("rss", version="2.0")
                ch = ET.SubElement(root, "channel")
                ET.SubElement(ch, "title").text = "MacroCockpit 每日简报"
                ET.SubElement(ch, "link").text = self.server.public_url
                ET.SubElement(ch, "description").text = _xml_safe(
                    (st.get("risk_gate") or {}).get("label") or "宏观与市场简报"
                )
                rows = (
                    [
                        (
                            "每日摘要",
                            str((st.get("risk_gate") or {}).get("label") or ""),
                            self.server.public_url,
                            f"summary-{gen}",
                        )
                    ]
                    + [
                        (
                            str(a.get("title") or "市场信号"),
                            str(a.get("summary") or ""),
                            str(a.get("url") or self.server.public_url),
                            f"sig-{i}-{hashlib.sha1(str(a.get('title') or '').encode('utf-8')).hexdigest()[:10]}",
                        )
                        for i, a in enumerate(s for s in (st.get("signals") or []) if s.get("selected"))
                        if i < 10
                    ]
                    + [
                        (
                            str(a.get("title") or "深度文章"),
                            str(a.get("excerpt") or ""),
                            self.server.public_url,
                            f"art-{str(a.get('filename') or '')}",
                        )
                        for a in (st.get("articles") or [])[:10]
                    ]
                )
                for title, desc, link, gid in rows:
                    it = ET.SubElement(ch, "item")
                    ET.SubElement(it, "title").text = _xml_safe(title)
                    ET.SubElement(it, "description").text = _xml_safe(desc)
                    ET.SubElement(it, "link").text = _xml_safe(link)
                    g = ET.SubElement(it, "guid", isPermaLink="false")
                    g.text = _xml_safe(gid)
                self._send_bytes(
                    200,
                    ET.tostring(root, encoding="utf-8", xml_declaration=True),
                    "application/rss+xml; charset=utf-8",
                )
                return

            if path.startswith("/api/"):
                self._send_json(404, {"error": "未找到接口"})
                return

            self._serve_web_file("/index.html" if path == "/" else path)
        except FileNotFoundError:
            self._send_json(503, {"error": "当前暂无已发布快照"})

    def _handle_sync_publish(self) -> None:
        auth = self._authenticate(allow_query_token=False)
        if auth is None or auth.get("kind") != "bearer":
            self._send_json(401, {"error": "Authorization: Bearer sync token required"})
            return
        if auth.get("role") != "SYNC_PUBLISH":
            self.server.portfolio_store.log_audit_event(
                actor_id=str(auth.get("actor_id") or "unknown"),
                role=str(auth.get("role") or ""),
                action="sync_publish",
                resource=self.path.split("?", 1)[0],
                outcome="denied",
                detail={"reason": "requires_sync_publish_role"},
            )
            self._send_json(403, {"error": "SYNC_PUBLISH role required; read/rw tokens are forbidden"})
            return

        b = self._read_json_body(max_bytes=MAX_SYNC_BODY_BYTES, allow_gzip=True)
        st = b.get("state")
        arts = b.get("articles", [])
        if (
            not isinstance(st, dict)
            or not isinstance(st.get("risk_gate"), dict)
            or not isinstance(st.get("signals"), list)
            or not isinstance(arts, list)
        ):
            self._send_json(400, {"error": "invalid publish payload"})
            return

        gen_arg = b.get("generation")
        gen_val: int | None = None
        if gen_arg is not None:
            try:
                gen_val = int(gen_arg)
            except (TypeError, ValueError):
                self._send_json(400, {"error": "invalid generation number"})
                return

        producer_id = str(b.get("producer_id") or auth.get("actor_id") or "windows-primary")
        try:
            manifest = publish_generation(
                self.server.base_dir,
                st,
                arts,
                producer_id=producer_id,
                generation=gen_val,
            )
        except ValueError as exc:
            msg = str(exc)
            if msg in ("stale_generation", "generation_conflict"):
                self._send_json(409, {"error": msg})
                return
            if msg.startswith("invalid_article_filename"):
                self._send_json(403, {"error": msg})
                return
            self._send_json(400, {"error": msg})
            return

        # Keep compatibility copies synchronized when custom state_path / articles_dir paths are used in tests
        expected_compat_state = self.server.base_dir / "data" / "cockpit_state.json"
        if self.server.state_path != expected_compat_state and expected_compat_state.is_file():
            self.server.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_s = self.server.state_path.with_suffix(".json.tmp")
            tmp_s.write_text(expected_compat_state.read_text(encoding="utf-8"), encoding="utf-8")
            os.replace(tmp_s, self.server.state_path)

        expected_compat_articles = self.server.base_dir / "articles"
        if self.server.articles_dir != expected_compat_articles:
            self.server.articles_dir.mkdir(parents=True, exist_ok=True)
            for a in arts:
                if isinstance(a, dict) and isinstance(a.get("filename"), str) and isinstance(a.get("content"), str):
                    dest = self.server.articles_dir / a["filename"]
                    t = dest.with_suffix(".md.tmp")
                    t.write_text(a["content"], encoding="utf-8")
                    os.replace(t, dest)

        self.server.portfolio_store.log_audit_event(
            actor_id=str(auth.get("actor_id") or "sync"),
            role="SYNC_PUBLISH",
            action="sync_publish",
            resource=f"gen:{manifest.get('generation')}",
            outcome="ok",
            detail={"generation": manifest.get("generation"), "articles_count": len(manifest.get("articles") or [])},
        )
        self._send_json(
            200,
            {
                "ok": True,
                "generation": manifest.get("generation"),
                "state_sha256": manifest.get("state_sha256"),
                "published_at": manifest.get("published_at"),
                "idempotent": bool(manifest.get("idempotent", False)),
                "articles_count": len(manifest.get("articles") or []),
            },
        )

    def _handle_ingest_intl(self) -> None:
        auth = self._authenticate(allow_query_token=False)
        if auth is None or auth.get("kind") != "bearer":
            self._send_json(401, {"error": "Authorization: Bearer sync token required"})
            return
        if auth.get("role") != "SYNC_PUBLISH":
            self.server.portfolio_store.log_audit_event(
                actor_id=str(auth.get("actor_id") or "unknown"),
                role=str(auth.get("role") or ""),
                action="ingest_intl",
                resource="/api/v2/ingest-intl",
                outcome="denied",
                detail={"reason": "requires_sync_publish_role"},
            )
            self._send_json(403, {"error": "SYNC_PUBLISH role required; read/rw tokens are forbidden"})
            return

        body = self._read_json_body(max_bytes=MAX_SYNC_BODY_BYTES, allow_gzip=True)
        try:
            from backend.cloud_collector import ingest_intl_bundle, run_cn_merge_and_publish

            ingest_res = ingest_intl_bundle(self.server.base_dir, body)
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
            return

        skip_merge = bool(body.get("skip_merge"))
        if not skip_merge:
            base_dir = self.server.base_dir

            def _bg_merge() -> None:
                with _INGEST_MERGE_LOCK:
                    try:
                        run_cn_merge_and_publish(base_dir)
                    except Exception:
                        pass

            threading.Thread(target=_bg_merge, daemon=True, name="mc-intl-merge").start()

        self.server.portfolio_store.log_audit_event(
            actor_id=str(auth.get("actor_id") or "sync"),
            role="SYNC_PUBLISH",
            action="ingest_intl",
            resource=str(ingest_res.get("producer_id") or "intl-collector"),
            outcome="ok",
            detail=ingest_res,
        )
        self._send_json(200, {"ok": True, "merge_scheduled": not skip_merge, **ingest_res})

    def do_POST(self) -> None:
        if not self._host_allowed():
            self._send_json(403, {"error": "Forbidden Host"})
            return
        u = urllib.parse.urlparse(self.path)
        path = u.path

        try:
            if path == "/mcp":
                q = urllib.parse.parse_qs(u.query)
                if "token" in q:
                    self._send_json(401, {"error": "URL ?token= is forbidden on /mcp; use Authorization: Bearer"})
                    return
                raw_body = self._read_raw_body(max_bytes=MAX_JSON_BODY_BYTES, allow_gzip=False)
                status, resp_hdrs, resp_body = handle_mcp_http_request(
                    self.headers,
                    raw_body,
                    store=self.server.portfolio_store,
                    base_dir=self.server.base_dir,
                    public_url=self.server.public_url,
                    read_token=self.server.read_token,
                    rw_token=self.server.rw_token,
                    sync_token=self.server.sync_token,
                )
                ctype = resp_hdrs.pop("Content-Type", "")
                self._send_bytes(status, resp_body, ctype, resp_hdrs)
                return

            if path in ("/api/sync", "/api/v2/publish"):
                self._handle_sync_publish()
                return

            if path == "/api/v2/ingest-intl":
                self._handle_ingest_intl()
                return

            if path == "/api/login":
                if not self._check_same_origin_header():
                    self._send_json(403, {"ok": False, "error": "非法跨站登录请求"})
                    return
                ctype_raw = (self.headers.get("Content-Type") or "").strip()
                ctype = ctype_raw.split(";", 1)[0].strip().lower()
                is_json = ctype == "application/json"
                if self.server.access_password:
                    if is_json:
                        pw = str(self._read_json_body(max_bytes=MAX_LOGIN_FORM_BYTES).get("password") or "")
                    elif ctype == "application/x-www-form-urlencoded":
                        form_raw = self._read_raw_body(max_bytes=MAX_LOGIN_FORM_BYTES)
                        pw = (
                            urllib.parse.parse_qs(form_raw.decode("utf-8", errors="replace")).get("password") or [""]
                        )[0]
                    else:
                        raise _HttpError(415, "Unsupported login Content-Type")

                    ip = self._client_ip()
                    with self.server._login_lock:
                        now = time.time()
                        failures = self.server._login_fails
                        for key, attempts in list(failures.items()):
                            if now - attempts[-1] >= 300:
                                del failures[key]
                        hist = [ts for ts in failures.get(ip, []) if now - ts < 300]
                        if len(hist) >= 10:
                            error = (429, "rate", "尝试过于频繁，请稍后再试")
                        elif not _const_eq(pw, self.server.access_password):
                            if ip not in failures and len(failures) >= 2048:
                                del failures[next(iter(failures))]
                            failures[ip] = [*hist, now]
                            error = (401, "1", "访问密码不正确")
                        else:
                            failures.pop(ip, None)
                            error = None
                    if error is not None:
                        status, form_code, message = error
                        if is_json:
                            self._send_json(status, {"ok": False, "error": message})
                            return
                        self.send_response(303)
                        self.send_header("Location", f"/login?error={form_code}")
                        self.end_headers()
                        return
                    exp = str(int(now) + 2592000)
                    jti = secrets.token_hex(12)
                    signed_payload = f"{exp}.{jti}"
                    sig = hmac.new(self._key(), signed_payload.encode("utf-8"), hashlib.sha256).hexdigest()
                    secure = "; Secure" if self._is_cookie_secure() else ""
                    cookie = f"macro_auth={signed_payload}.{sig}; HttpOnly; Path=/; SameSite=Lax; Max-Age=2592000{secure}"
                    if is_json:
                        self._send_bytes(200, b'{"ok":true}', "application/json; charset=utf-8", {"Set-Cookie": cookie})
                        return
                    self.send_response(303)
                    self.send_header("Location", "/")
                    self.send_header("Set-Cookie", cookie)
                    self.end_headers()
                    return
                self._send_json(200, {"ok": True})
                return

            if path == "/api/logout":
                auth = self._authenticate(allow_query_token=False)
                if auth is not None and not self._enforce_write_csrf(auth):
                    return
                _ = self._read_json_body(max_bytes=65_536, allow_empty_no_ctype=True)
                raw = self._raw_cookie()
                if raw and self._cookie_ok():
                    now_i = int(time.time())
                    tok_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
                    exp_ts = int(raw.split(".", 1)[0])
                    st = self.server.journal_store
                    with st._lock, st._connect("immediate") as db:
                        db.execute("DELETE FROM auth_revoked_cookies WHERE exp_ts<=?", (now_i,))
                        db.execute(
                            "INSERT OR IGNORE INTO auth_revoked_cookies(tok_hash, exp_ts) VALUES(?,?)",
                            (tok_hash, exp_ts),
                        )
                secure = "; Secure" if self._is_cookie_secure() else ""
                self._send_bytes(
                    200,
                    b'{"ok":true}',
                    "application/json; charset=utf-8",
                    {"Set-Cookie": f"macro_auth=; HttpOnly; Path=/; SameSite=Lax; Max-Age=0{secure}"},
                )
                return

            auth = self._authenticate(allow_query_token=False)
            if not self._require_role(auth, ("PORTFOLIO_RW",)):
                return
            assert auth is not None
            if not self._enforce_write_csrf(auth):
                return

            b = self._read_json_body(max_bytes=MAX_JSON_BODY_BYTES)
            actor_id = str(auth.get("actor_id") or "owner")

            # V2 transactional routes
            if path == "/api/v2/changes":
                try:
                    req_id = str(b.get("request_id") or "").strip()
                    exp_rev = int(b.get("expected_revision"))
                    ops = b.get("ops")
                    res = self.server.portfolio_store.apply_changes(
                        request_id=req_id,
                        expected_revision=exp_rev,
                        ops=ops,
                        actor_id=actor_id,
                        role="PORTFOLIO_RW",
                        summary=str(b.get("summary") or ""),
                    )
                    self._send_json(200, res)
                except (RevisionConflictError, IdempotencyConflictError) as exc:
                    self._send_json(409, {"error": str(exc)})
                except (ConstraintViolationError, ValueError, TypeError, KeyError, sqlite3.Error) as exc:
                    self._send_json(400, {"error": str(exc)})
                return

            if path == "/api/v2/import/preview":
                try:
                    res = preview_import(
                        self.server.portfolio_store,
                        b.get("account_id"),
                        str(b.get("format") or "auto"),
                        str(b.get("text") or ""),
                        str(b.get("mode") or "upsert"),
                        actor_id,
                    )
                    self._send_json(200, res)
                except ValueError as exc:
                    self._send_json(400, {"error": str(exc)})
                return

            if path == "/api/v2/import/commit":
                try:
                    res = commit_import(
                        self.server.portfolio_store,
                        str(b.get("preview_id") or ""),
                        str(b.get("request_id") or ""),
                        int(b.get("expected_revision")),
                        actor_id,
                        role="PORTFOLIO_RW",
                    )
                    self._send_json(200, res)
                except PermissionError as exc:
                    self._send_json(403, {"error": str(exc)})
                except (RevisionConflictError, IdempotencyConflictError) as exc:
                    self._send_json(409, {"error": str(exc)})
                except KeyError as exc:
                    self._send_json(404, {"error": str(exc)})
                except (PreviewExpiredError, PreviewValidationError, ValueError, TypeError) as exc:
                    self._send_json(400, {"error": str(exc)})
                return

            # V1 responses retain their original shape while using the ledger transaction.
            try:
                if path in ("/api/journal/review", "/api/journal/essay"):
                    entity = path.rsplit("/", 1)[-1]
                    item = self._apply_v1_journal(
                        {"op": "put", "entity": entity, "data": b}, auth, f"v1_journal_{entity}_upsert",
                    )
                    self._send_json(200, item["after"])
                    return
                if path in ("/api/journal/review/delete", "/api/journal/essay/delete"):
                    entity = path.split("/")[-2]
                    uid = int(b.get("id") or b.get(f"{entity}_id"))
                    item = self._apply_v1_journal(
                        {"op": "remove", "entity": entity, "id": uid, "data": b},
                        auth, f"v1_journal_{entity}_delete",
                    )
                    self._send_json(200, {"deleted": item is not None})
                    return
                if path == "/api/article/status":
                    if not safe_article_filename(str(b.get("filename") or "")):
                        self._send_json(403, {"error": "invalid filename"})
                        return
                    self._apply_v1_journal(
                        {"op": "put", "entity": "article_status", "data": b}, auth, "v1_article_status_set",
                    )
                    self._send_json(200, self._snap())
                    return
            except RevisionConflictError as exc:
                self._send_json(409, {"error": str(exc)})
                return
            except (ValueError, KeyError, TypeError, sqlite3.Error) as exc:
                self._send_json(400, {"error": str(exc)})
                return

            if path == "/api/refresh":
                if self.server.role == "consumer":
                    self._send_json(403, {"error": "consumer_mode_refresh_disabled"})
                    return
                try:
                    from backend.signal_aggregator import generate_cockpit_state
                except ImportError:
                    self._send_json(503, {"error": "refresh dependencies unavailable"})
                    return
                with _REFRESH_LOCK:
                    try:
                        out = generate_cockpit_state(
                            refresh_fred=bool(b.get("refresh_fred")),
                            output_path=self.server.state_path,
                        )
                    except (ImportError, RuntimeError):
                        self._send_json(503, {"error": "refresh dependencies unavailable"})
                        return
                out_pub = project_public_state(out, include_candidates=False)
                out_pub["journal"] = self.server.journal_store.get_snapshot(out_pub)
                self._send_json(200, out_pub)
                return

            if path == "/api/research/ingest":
                if self.server.role == "consumer":
                    self._send_json(403, {"error": "consumer_mode_ingest_disabled"})
                    return
                body_md = str(b.get("body_md") or "").strip()
                if not body_md:
                    self._send_json(400, {"error": "body_md 不能为空"})
                    return
                try:
                    from backend.periodic_report import ingest_deep_research_markdown
                except ImportError:
                    self._send_json(503, {"error": "research ingest dependencies unavailable"})
                    return
                raw_tickers = b.get("tickers")
                if isinstance(raw_tickers, str) and not raw_tickers.strip():
                    raw_tickers = None
                try:
                    art_summary = ingest_deep_research_markdown(
                        body_md,
                        title=str(b.get("title") or ""),
                        tickers=raw_tickers,
                        related_period=str(b.get("related_period") or ""),
                        articles_dir=self.server.articles_dir,
                        url=str(b.get("url") or ""),
                    )
                except ValueError as exc:
                    self._send_json(400, {"error": str(exc)})
                    return

                fname = str(art_summary.get("filename") or "")
                parsed = parse_markdown_article(self.server.articles_dir / fname, "周月度深研") if fname else None
                if isinstance(parsed, dict):
                    merged_art = dict(parsed)
                    for k in ("report_kind", "publisher", "author_role", "tickers", "score", "url"):
                        if k not in merged_art and k in art_summary:
                            merged_art[k] = art_summary[k]
                    merged_art["source"] = "周月度深研"
                    art_summary = merged_art
                else:
                    art_summary["source"] = "周月度深研"

                with _REFRESH_LOCK:
                    try:
                        cur_state = dict(self._state())
                    except Exception:
                        cur_state = {}
                    prev_articles = [
                        a
                        for a in (cur_state.get("articles") or [])
                        if isinstance(a, dict) and a.get("filename") != fname
                    ]
                    cur_state["articles"] = [art_summary, *prev_articles]
                    cov = dict(cur_state.get("coverage") or {})
                    cov["articles_count"] = len(cur_state["articles"])
                    cov["periodic_reports_count"] = sum(
                        1 for a in cur_state["articles"] if isinstance(a, dict) and a.get("source") == "周月度深研"
                    )
                    cur_state["coverage"] = cov

                    manifest_path = self.server.base_dir / "data" / "current_generation.json"
                    if manifest_path.is_file():
                        loaded_pub = load_published_generation(self.server.base_dir)
                        pub_gen = int(loaded_pub.get("generation") or 0)
                        pub_manifest = loaded_pub.get("manifest")
                        gen_art_dir = self.server.base_dir / "data" / "generations" / f"gen_{pub_gen}" / "articles"
                        arts_payload: list[dict[str, str]] = []
                        seen_payload: set[str] = set()
                        new_file = self.server.articles_dir / fname
                        if fname and new_file.is_file():
                            arts_payload.append({"filename": fname, "content": new_file.read_text(encoding="utf-8")})
                            seen_payload.add(fname)
                        art_entries = (
                            (pub_manifest.get("articles") if isinstance(pub_manifest, dict) else None)
                            or cur_state.get("articles")
                            or []
                        )
                        for entry in art_entries:
                            if not isinstance(entry, dict):
                                continue
                            ef = safe_article_filename(entry.get("filename"))
                            if not ef or ef in seen_payload:
                                continue
                            seen_payload.add(ef)
                            cand = gen_art_dir / ef
                            if not cand.is_file():
                                cand = self.server.articles_dir / ef
                            if not cand.is_file():
                                cand = self.server.base_dir / "articles" / ef
                            if cand.is_file():
                                try:
                                    arts_payload.append({"filename": ef, "content": cand.read_text(encoding="utf-8")})
                                except OSError:
                                    pass
                        manifest = publish_generation(
                            self.server.base_dir,
                            cur_state,
                            arts_payload,
                            producer_id="research-ingest",
                        )
                        out_gen = int(manifest.get("generation") or 0)
                        compat_state = self.server.base_dir / "data" / "cockpit_state.json"
                        if self.server.state_path != compat_state and compat_state.is_file():
                            with file_lock(self.server.state_path):
                                atomic_write_json(self.server.state_path, json.loads(compat_state.read_text(encoding="utf-8")))
                    else:
                        with file_lock(self.server.state_path):
                            atomic_write_json(self.server.state_path, cur_state)
                        out_gen = int(cur_state.get("generation") or 0)

                self._send_json(200, {"ok": True, "article": art_summary, "generation": out_gen})
                return

            if path == "/api/push":
                include_amounts = b.get("include_amounts")
                if include_amounts is not None and not isinstance(include_amounts, bool):
                    raise _HttpError(400, "include_amounts must be a JSON boolean")
                try:
                    cur_state = self._state()
                except FileNotFoundError:
                    self._send_json(503, {"error": "当前暂无已发布快照"})
                    return
                try:
                    ws = self.server.portfolio_store.build_workspace(public_state=cur_state, currency="USD")
                except Exception:
                    ws = None
                try:
                    res = dispatch_mobile_push(
                        cur_state,
                        channels=b.get("channels"),
                        dry_run=not (b.get("live") is True and b.get("dry_run") is not True),
                        feishu_open_id=b.get("feishu_open_id"),
                        workspace=ws,
                        mode=str(b.get("mode") or "daily"),
                        include_amounts=include_amounts is True,
                    )
                    self._send_json(200, res)
                except ValueError as exc:
                    self._send_json(400, {"error": str(exc)})
                return

            self._send_json(404, {"error": "未找到接口"})
        except _HttpError as exc:
            self._send_json(exc.status, {"error": exc.message})
        except Exception:
            self._send_json(500, {"error": "internal_server_error"})

    def do_DELETE(self) -> None:
        if not self._host_allowed():
            self._send_json(403, {"error": "Forbidden Host"})
            return
        u = urllib.parse.urlparse(self.path)
        path = u.path
        if path == "/mcp":
            self._send_json(405, {"error": "Method not allowed; use POST /mcp"}, {"Allow": "POST"})
            return

        auth = self._authenticate(allow_query_token=False)
        if not self._require_role(auth, ("PORTFOLIO_RW",)):
            return
        assert auth is not None
        if not self._enforce_write_csrf(auth):
            return

        if path not in ("/api/journal/review", "/api/journal/essay"):
            self._send_json(404, {"error": "未找到接口"})
            return
        q = urllib.parse.parse_qs(u.query, keep_blank_values=True)
        entity = path.rsplit("/", 1)[-1]
        try:
            uid = int((q.get("id") or [""])[0])
            expected_rev = q.get("expected_rev", [None])[0]
            item = self._apply_v1_journal(
                {"op": "remove", "entity": entity, "id": uid, "data": {"expected_rev": expected_rev}},
                auth, f"v1_journal_{entity}_delete",
            )
            self._send_json(200, {"deleted": item is not None})
        except RevisionConflictError as exc:
            self._send_json(409, {"error": str(exc)})
        except (ValueError, TypeError):
            self._send_json(400, {"error": "invalid id"})
        except Exception:
            self._send_json(500, {"error": "internal_server_error"})

    def _serve_web_file(self, rel_path: str) -> None:
        root = self.server.web_dir.resolve()
        target = (root / (rel_path.lstrip("/") or "index.html")).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            self._send_json(403, {"error": "invalid path"})
            return
        if not target.is_file():
            self._send_json(404, {"error": "not found"})
            return
        ctype, _ = mimetypes.guess_type(str(target))
        ctype = ctype or "application/octet-stream"
        if target.suffix == ".webmanifest":
            ctype = "application/manifest+json; charset=utf-8"
        elif target.suffix == ".svg":
            ctype = "image/svg+xml; charset=utf-8"
        elif ctype.startswith("text/") or ctype == "application/javascript":
            ctype += "; charset=utf-8"
        self._send_bytes(200, target.read_bytes(), ctype, {"Cache-Control": "no-cache"})


def create_server(
    host: str = "127.0.0.1",
    port: int = 8788,
    *,
    web_dir: Path = WEB_DIR,
    state_path: Path = STATE_OUTPUT_PATH,
    db_path: Path | None = None,
    articles_dir: Path | None = None,
    access_password: str | None = None,
    read_token: str | None = None,
    rw_token: str | None = None,
    sync_token: str | None = None,
    role: str | None = None,
    public_url: str | None = None,
) -> ThreadingHTTPServer:
    resolved_role = (role if role is not None else os.environ.get("MACROCOCKPIT_ROLE", "standalone")).strip().lower()
    if resolved_role not in ("consumer", "standalone"):
        raise ValueError(f"Unsupported server role: {resolved_role!r}")

    sp = Path(state_path)
    base_dir = _resolve_base_dir(sp)

    srv = CockpitHTTPServer((host, port), CockpitRequestHandler)
    srv.web_dir = Path(web_dir)
    srv.state_path = sp
    srv.base_dir = base_dir
    srv.role = resolved_role
    srv.db_path = Path(db_path) if db_path else sp.parent / "user_journal.db"
    srv.articles_dir = Path(articles_dir) if articles_dir else base_dir / "articles"
    srv.access_password = (
        access_password if access_password is not None else os.environ.get("MACROCOCKPIT_ACCESS_PASSWORD", "")
    ).strip()
    srv.read_token = (
        read_token if read_token is not None else os.environ.get("MACROCOCKPIT_READ_TOKEN", "")
    ).strip()
    srv.rw_token = (rw_token if rw_token is not None else os.environ.get("MACROCOCKPIT_RW_TOKEN", "")).strip()
    srv.sync_token = (
        sync_token if sync_token is not None else os.environ.get("MACROCOCKPIT_SYNC_TOKEN", "")
    ).strip()
    srv.public_url = (
        public_url if public_url is not None else os.environ.get("MACROCOCKPIT_PUBLIC_URL", "https://news.shuashuabao.xyz")
    ).rstrip("/")

    srv.portfolio_store = PortfolioStore(srv.db_path)
    srv.journal_store = srv.portfolio_store.journal
    srv._login_fails = {}
    srv._login_lock = threading.Lock()
    with srv.journal_store._lock, srv.journal_store._connect("immediate") as db:
        db.execute("INSERT OR IGNORE INTO auth_runtime_kv(k,v) VALUES('cookie_key',?)", (secrets.token_hex(32),))
        srv.cookie_key = bytes.fromhex(db.execute("SELECT v FROM auth_runtime_kv WHERE k='cookie_key'").fetchone()[0])
        db.execute("DELETE FROM auth_revoked_cookies WHERE exp_ts<=?", (int(time.time()),))
    return srv


def main() -> None:
    load_push_env(include_sync_token=True)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(description="MacroCockpit HTTP server, V2 APIs, MCP endpoint, & offline bundler")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8788)
    ap.add_argument(
        "--role",
        choices=("consumer", "standalone"),
        default=os.environ.get("MACROCOCKPIT_ROLE", "standalone").strip().lower(),
    )
    ap.add_argument("--refresh-once", action="store_true")
    ap.add_argument("--refresh-fred", action="store_true")
    ap.add_argument("--build-static", action="store_true")
    ap.add_argument("--no-auth", action="store_true", help="Force passwordless mode on loopback standalone server")
    ap.add_argument("--require-password", action="store_true", help="Require access password even on loopback standalone")
    ap.add_argument("--data-dir", type=Path)
    a = ap.parse_args()

    state_path = a.data_dir / "cockpit_state.json" if a.data_dir else STATE_OUTPUT_PATH
    if a.refresh_once:
        if a.role == "consumer":
            print("Refusing --refresh-once in consumer mode", file=sys.stderr)
            raise SystemExit(2)
        from backend.signal_aggregator import generate_cockpit_state

        st = generate_cockpit_state(refresh_fred=a.refresh_fred, output_path=state_path)
        print(
            json.dumps(
                {
                    "action": "refresh_once",
                    "generated_at_bjt": st["generated_at_bjt"],
                    "positions": len(st["positions"]),
                    "macro_fred": len(st["macro_fred"]),
                    "signals": len(st["signals"]),
                    "articles": len(st["articles"]),
                },
                ensure_ascii=False,
            )
        )
        if not a.build_static:
            return
    if a.build_static:
        out = build_static_bundle(state_path=state_path)
        print(json.dumps({"action": "build_static", "output": str(out), "bytes": out.stat().st_size}, ensure_ascii=False))
        return

    access_pw = os.environ.get("MACROCOCKPIT_ACCESS_PASSWORD", "").strip()
    sync_tok = os.environ.get("MACROCOCKPIT_SYNC_TOKEN", "").strip()
    is_loopback_bind = a.host in ("127.0.0.1", "localhost", "::1")
    if a.role == "consumer" and (not access_pw or not sync_tok):
        print(
            "Refusing consumer startup without both MACROCOCKPIT_ACCESS_PASSWORD and MACROCOCKPIT_SYNC_TOKEN",
            file=sys.stderr,
        )
        raise SystemExit(2)
    if not is_loopback_bind and not access_pw:
        print("Refusing non-loopback bind without MACROCOCKPIT_ACCESS_PASSWORD", file=sys.stderr)
        raise SystemExit(2)

    local_require_pw = bool(
        a.require_password or os.environ.get("MACROCOCKPIT_LOCAL_REQUIRE_PASSWORD", "").strip() == "1"
    )
    effective_access_pw = access_pw
    if a.role == "standalone" and is_loopback_bind and (a.no_auth or not local_require_pw):
        effective_access_pw = ""

    if a.role == "standalone":
        _load_or_build_state(state_path, role="standalone")

    srv = create_server(
        a.host,
        a.port,
        state_path=state_path,
        db_path=a.data_dir / "user_journal.db" if a.data_dir else None,
        articles_dir=a.data_dir.parent / "articles" if a.data_dir else None,
        access_password=effective_access_pw,
        role=a.role,
    )
    print(f"MacroCockpit ({a.role}) serving on http://{a.host}:{a.port}/")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
