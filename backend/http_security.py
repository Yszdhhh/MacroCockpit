"""Shared HTTP authentication, origin checks, and bounded request decoding."""
from __future__ import annotations

import hmac
import os
import re
import urllib.parse
import zlib
from typing import Any

ROLE_READ_ONLY = "READ_ONLY"
ROLE_PORTFOLIO_RW = "PORTFOLIO_RW"
ROLE_SYNC_PUBLISH = "SYNC_PUBLISH"
VALID_BEARER_ROLES = frozenset({ROLE_READ_ONLY, ROLE_PORTFOLIO_RW, ROLE_SYNC_PUBLISH})
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class HttpSecurityError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def const_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(str(a or "").encode("utf-8"), str(b or "").encode("utf-8"))


def is_loopback_client_ip(addr: str) -> bool:
    return addr in ("127.0.0.1", "::1", "::ffff:127.0.0.1")


def is_loopback_host_header(host_header: str) -> bool:
    return bool(re.fullmatch(r"(?:127\.0\.0\.1|localhost|\[::1\])(?::[0-9]{1,5})?", host_header, re.IGNORECASE))


def parse_origin(url_str: str) -> tuple[str, str] | None:
    raw = str(url_str or "").strip()
    if not raw or raw.lower() == "null":
        return None
    try:
        u = urllib.parse.urlparse(raw)
    except ValueError:
        return None
    scheme = (u.scheme or "").lower()
    netloc = (u.netloc or "").lower()
    if scheme not in ("http", "https") or not netloc:
        return None
    return (scheme, netloc)


def is_loopback_origin(origin_or_scheme: str, netloc: str | None = None) -> bool:
    if netloc is None and not origin_or_scheme.strip():
        return True
    url = origin_or_scheme if netloc is None else f"{origin_or_scheme}://{netloc}"
    try:
        parsed = urllib.parse.urlparse(url.strip())
        return parsed.scheme in ("http", "https") and parsed.hostname in LOOPBACK_HOSTS
    except ValueError:
        return False


def origin_allowed(origin_header: str, *, host_header: str, public_url: str) -> bool:
    if not origin_header:
        return True
    parsed = parse_origin(origin_header)
    if parsed is None:
        return False
    scheme, netloc = parsed
    if is_loopback_origin(scheme, netloc):
        return True
    pub = parse_origin(public_url)
    if pub is not None and (scheme, netloc) == pub:
        return True
    if host_header and netloc == host_header.strip().lower():
        return True
    return False


def openbb_cors_origin(origin_header: str) -> str | None:
    raw = str(origin_header or "").strip()
    if not raw:
        return None
    try:
        parsed = urllib.parse.urlparse(raw)
    except ValueError:
        return None
    scheme = (parsed.scheme or "").lower()
    host = (parsed.hostname or "").lower()
    if scheme == "https" and host == "pro.openbb.co":
        return f"{scheme}://{parsed.netloc}"
    if scheme in ("http", "https") and host in LOOPBACK_HOSTS:
        return f"{scheme}://{parsed.netloc}"
    return None


def get_header(headers: Any, name: str) -> str:
    if not headers:
        return ""
    target = name.lower()
    if hasattr(headers, "items"):
        for k, v in headers.items():
            if str(k).lower() == target:
                return str(v or "").strip()
    elif hasattr(headers, "get"):
        val = headers.get(name) or headers.get(target) or ""
        return str(val).strip()
    return ""


def resolve_bearer_auth(
    token: str,
    *,
    store: Any,
    read_token: str = "",
    rw_token: str = "",
    sync_token: str = "",
) -> dict[str, Any] | None:
    cand = str(token or "").strip()
    if not cand:
        return None
    if store is not None:
        info = store.verify_token(cand)
        if info is not None:
            return info
    # Also support explicit in-memory server tokens & environment tokens when store is absent/test-configured
    candidates = [
        (rw_token or os.environ.get("MACROCOCKPIT_RW_TOKEN", ""), ROLE_PORTFOLIO_RW, "config:portfolio_rw"),
        (sync_token or os.environ.get("MACROCOCKPIT_SYNC_TOKEN", ""), ROLE_SYNC_PUBLISH, "config:sync_publish"),
        (read_token or os.environ.get("MACROCOCKPIT_READ_TOKEN", ""), ROLE_READ_ONLY, "config:read_only"),
    ]
    for expected, role, actor in candidates:
        exp_clean = str(expected or "").strip()
        if exp_clean and const_eq(cand, exp_clean):
            return {"valid": True, "role": role, "actor_id": actor, "source": "config"}
    return None


def decompress_gzip_bounded(raw: bytes, max_bytes: int) -> bytes:
    dec = zlib.decompressobj(16 + zlib.MAX_WBITS)
    chunks: list[bytes] = []
    total = 0
    view = memoryview(raw)
    step = 65536
    for idx in range(0, len(view), step):
        part = dec.decompress(view[idx : idx + step], max_bytes - total + 1)
        total += len(part)
        if total > max_bytes:
            raise HttpSecurityError(413, f"Decompressed payload exceeds {max_bytes} bytes")
        chunks.append(part)
    tail = dec.flush(max_bytes - total + 1)
    total += len(tail)
    if total > max_bytes:
        raise HttpSecurityError(413, f"Decompressed payload exceeds {max_bytes} bytes")
    chunks.append(tail)
    return b"".join(chunks)
