"""Stdio MCP (2025-11-25) bridge: Bearer HTTP proxy to remote /mcp or strict --offline-public RO snapshot."""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, TextIO

_BACKEND_DIR = Path(__file__).resolve().parent
_COCKPIT_ROOT = _BACKEND_DIR.parent
if str(_COCKPIT_ROOT) not in sys.path:
    sys.path.insert(0, str(_COCKPIT_ROOT))

from backend.mcp_server import MCP_PROTOCOL_VERSION, McpDispatcher, _load_rpc_json, _rpc_error
from backend.mobile_push import load_push_env


def _log(stderr: TextIO, msg: str) -> None:
    try:
        stderr.write(f"[macrocockpit-mcp] {msg}\n")
        stderr.flush()
    except Exception:
        pass


def _write_json_line(stdout: TextIO, obj: dict[str, Any]) -> None:
    stdout.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")
    stdout.flush()


def _forward_remote_mcp(
    mcp_url: str,
    token: str,
    raw_line_bytes: bytes,
    *,
    timeout: int = 30,
) -> tuple[int, dict[str, Any] | None]:
    req = urllib.request.Request(
        mcp_url,
        data=raw_line_bytes,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
            "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        status = int(getattr(resp, "status", 200))
        body = resp.read()
        if status == 202 or not body.strip():
            return (status, None)
        parsed = json.loads(body.decode("utf-8"))
        if isinstance(parsed, dict):
            return (status, parsed)
        return (status, None)


def run_stdio_loop(
    stdin: TextIO,
    stdout: TextIO,
    stderr: TextIO,
    *,
    offline_public: bool = False,
    desktop_pro: bool = False,
    base_dir: Path | str = _COCKPIT_ROOT,
    mcp_url: str = "",
    mcp_token: str = "",
    timeout: int = 30,
) -> int:
    """Process newline-delimited UTF-8 JSON-RPC 2.0 messages cleanly via stdout, logs only to stderr."""
    if offline_public and desktop_pro:
        _log(stderr, "ERROR: --offline-public and --desktop-pro are mutually exclusive.")
        return 2

    dispatcher: McpDispatcher | None = None
    local_role = "READ_ONLY"
    local_actor = "stdio:offline_public"

    if offline_public:
        dispatcher = McpDispatcher(store=None, base_dir=Path(base_dir), desktop_pro=False)
        _log(stderr, f"started in --offline-public READ_ONLY mode (base_dir={Path(base_dir)})")
    elif desktop_pro:
        from backend.portfolio_store import PortfolioStore

        db_path = Path(base_dir) / "data" / "user_journal.db"
        store = PortfolioStore(db_path)
        dispatcher = McpDispatcher(store=store, base_dir=Path(base_dir), desktop_pro=True)
        local_role = "PORTFOLIO_RW"
        local_actor = "stdio:desktop_pro"
        _log(stderr, f"started in --desktop-pro PORTFOLIO_RW mode (12 tools, base_dir={Path(base_dir)})")
    else:
        url_clean = str(mcp_url or "").strip()
        tok_clean = str(mcp_token or "").strip()
        if not url_clean or not tok_clean:
            _log(
                stderr,
                "ERROR: Missing MACROCOCKPIT_MCP_URL / MACROCOCKPIT_MCP_TOKEN (or pass --offline-public / --desktop-pro).",
            )
            return 2
        if not url_clean.startswith(("https://", "http://")):
            _log(stderr, f"ERROR: Invalid MCP URL: {url_clean}")
            return 2
        _log(stderr, f"started in remote proxy mode -> {url_clean}")

    for raw_line in stdin:
        line = raw_line.strip()
        if not line:
            continue

        try:
            payload = _load_rpc_json(line)
        except ValueError as exc:
            _log(stderr, f"invalid JSON input: {exc}")
            _write_json_line(stdout, _rpc_error(None, -32700, "Parse error"))
            continue

        if not isinstance(payload, dict):
            _write_json_line(stdout, _rpc_error(None, -32600, "Invalid Request"))
            continue

        req_id = payload.get("id")
        is_notification = "id" not in payload

        if dispatcher is not None:
            _status, resp_obj = dispatcher.dispatch_jsonrpc(
                payload,
                role=local_role,
                actor_id=local_actor,
            )
            if resp_obj is not None and not is_notification:
                _write_json_line(stdout, resp_obj)
            continue

        # Remote Bearer proxy mode (never touches local SQLite ledger)
        wire_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            _status, resp_obj = _forward_remote_mcp(
                mcp_url.strip(),
                mcp_token.strip(),
                wire_bytes,
                timeout=timeout,
            )
            if resp_obj is not None and not is_notification:
                _write_json_line(stdout, resp_obj)
        except urllib.error.HTTPError as exc:
            err_body = ""
            try:
                err_body = exc.read().decode("utf-8", errors="replace")[:300]
            except Exception:
                pass
            _log(stderr, f"remote HTTP {exc.code}: {err_body}")
            if not is_notification:
                try:
                    parsed_err = json.loads(err_body)
                    if isinstance(parsed_err, dict) and parsed_err.get("jsonrpc") == "2.0":
                        _write_json_line(stdout, parsed_err)
                        continue
                except Exception:
                    pass
                _write_json_line(
                    stdout,
                    _rpc_error(req_id, -32000, f"Remote MCP HTTP {exc.code}", {"detail": err_body}),
                )
        except Exception as exc:
            _log(stderr, f"remote transport error: {type(exc).__name__}: {exc}")
            if not is_notification:
                _write_json_line(
                    stdout,
                    _rpc_error(req_id, -32000, f"Remote MCP error: {type(exc).__name__}"),
                )
    return 0


def main() -> None:
    load_push_env(include_sync_token=False)
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(description="MacroCockpit MCP stdio bridge (remote Bearer proxy or offline RO snapshot)")
    ap.add_argument("--url", default=os.environ.get("MACROCOCKPIT_MCP_URL", ""), help="Remote /mcp endpoint URL")
    ap.add_argument("--token", default=os.environ.get("MACROCOCKPIT_MCP_TOKEN", ""), help="Bearer token (READ_ONLY or PORTFOLIO_RW)")
    ap.add_argument(
        "--offline-public",
        action="store_true",
        help="Serve read-only tools directly from local published snapshot without touching or creating any ledger DB",
    )
    ap.add_argument(
        "--desktop-pro",
        action="store_true",
        help="Enable local Desktop Pro PORTFOLIO_RW mode (12 tools including OpenBB, deep research, and cloud sync)",
    )
    ap.add_argument("--base-dir", type=Path, default=_COCKPIT_ROOT, help="Base directory containing data/")
    ap.add_argument("--data-dir", type=Path, help="Optional data directory override")
    ap.add_argument("--timeout", type=int, default=30, help="HTTP timeout in seconds for remote proxy mode")
    args = ap.parse_args()

    base_dir = Path(args.base_dir)
    if args.data_dir is not None:
        dd = Path(args.data_dir)
        base_dir = dd.parent if dd.name == "data" else dd

    code = run_stdio_loop(
        sys.stdin if isinstance(sys.stdin, io.TextIOBase) else sys.stdin,
        sys.stdout,
        sys.stderr,
        offline_public=bool(args.offline_public),
        desktop_pro=bool(args.desktop_pro),
        base_dir=base_dir,
        mcp_url=str(args.url or ""),
        mcp_token=str(args.token or ""),
        timeout=max(3, int(args.timeout or 30)),
    )
    raise SystemExit(code)


if __name__ == "__main__":
    main()
