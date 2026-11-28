"""MacroCockpit Desktop Pro local HTTP server (OpenBB bridge, AGY ACP, multi-agent debate, 12-tool MCP)."""
from __future__ import annotations

import argparse
import json
import logging
import mimetypes
import os
import secrets
import socket
import sys
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

_DESKTOP_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _DESKTOP_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend.http_security import (
    LOOPBACK_HOSTS,
    ROLE_PORTFOLIO_RW,
    const_eq,
    is_loopback_client_ip,
    is_loopback_host_header,
    is_loopback_origin,
    openbb_cors_origin,
)
from backend.runtime import COCKPIT_ROOT, resolve_data_dir
from backend.portfolio_store import PortfolioStore
from desktop.acp_bridge import ask_agy, probe_agy
from desktop.mcp_extensions import build_desktop_mcp_handlers
from desktop.multi_agent_debate import get_debate, list_debates, run_multi_agent_debate
from desktop.openbb_bridge import (
    build_openbb_apps_manifest,
    build_openbb_widgets_manifest,
    get_obb_widget_data,
    probe_openbb_status,
    query_openbb,
)

logger = logging.getLogger(__name__)

MAX_JSON_BYTES = 2_000_000


class DesktopProHandler(BaseHTTPRequestHandler):
    server_version = "MacroCockpitDesktopPro/2.0"

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
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _send_json(
        self,
        status: int,
        payload: Any,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._send_bytes(
            status,
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
            headers=headers,
        )

    def _read_json_body(self) -> tuple[int | None, dict[str, Any] | None]:
        ctype = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if ctype != "application/json":
            return (HTTPStatus.UNSUPPORTED_MEDIA_TYPE, None)
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            return (HTTPStatus.BAD_REQUEST, None)
        if length < 0:
            return (HTTPStatus.BAD_REQUEST, None)
        if length > MAX_JSON_BYTES:
            return (HTTPStatus.REQUEST_ENTITY_TOO_LARGE, None)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            obj = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return (HTTPStatus.BAD_REQUEST, None)
        if not isinstance(obj, dict):
            return (HTTPStatus.BAD_REQUEST, None)
        return (None, obj)

    def _check_loopback_request(self) -> bool:
        hosts = self.headers.get_all("Host", [])
        if len(hosts) != 1 or not is_loopback_host_header(hosts[0]):
            self._send_json(403, {"error": "forbidden Host header"})
            return False
        if not is_loopback_client_ip(self.client_address[0]):
            self._send_json(403, {"error": "forbidden TCP peer"})
            return False
        return True

    def _check_desktop_token(self) -> bool:
        origin_hdr = self.headers.get("Origin") or ""
        if origin_hdr and not is_loopback_origin(origin_hdr):
            self._send_json(403, {"error": "forbidden cross-origin request"})
            return False

        tok = (self.headers.get("X-Desktop-Token") or "").strip()
        if not tok:
            auth = (self.headers.get("Authorization") or "").strip()
            if auth.startswith("Bearer "):
                tok = auth[7:].strip()
        if not const_eq(tok, self.server.desktop_token):  # type: ignore[attr-defined]
            self._send_json(401, {"error": "invalid or missing desktop token"})
            return False
        return True

    def _obb_cors_headers(self) -> tuple[bool, dict[str, str]]:
        origin_hdr = self.headers.get("Origin") or ""
        if not origin_hdr:
            return (True, {})
        allowed = openbb_cors_origin(origin_hdr)
        if allowed is None:
            return (False, {})
        return (
            True,
            {
                "Access-Control-Allow-Origin": allowed,
                "Access-Control-Allow-Methods": "GET, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type",
                "Vary": "Origin",
            },
        )

    def _serve_ui_file(self, rel_path: str) -> None:
        ui_root: Path = self.server.ui_dir.resolve()  # type: ignore[attr-defined]
        clean = rel_path.lstrip("/") or "index.html"
        if clean == "index.html":
            index_file = ui_root / "index.html"
            if not index_file.is_file():
                fallback = (
                    "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
                    f"<meta name='desktop-token' content='{self.server.desktop_token}'>"  # type: ignore[attr-defined]
                    "<title>MacroCockpit 桌面专业终端</title></head>"
                    "<body><h1>MacroCockpit 桌面专业终端后端已就绪</h1></body></html>"
                )
                self._send_bytes(200, fallback.encode("utf-8"), "text/html; charset=utf-8")
                return
            html_txt = index_file.read_text(encoding="utf-8").replace(
                "__DESKTOP_TOKEN__",
                self.server.desktop_token,  # type: ignore[attr-defined]
            )
            self._send_bytes(200, html_txt.encode("utf-8"), "text/html; charset=utf-8")
            return

        if clean == "tokens.css":
            tokens_file = (_REPO_ROOT / "web" / "tokens.css").resolve()
            if not tokens_file.is_file():
                self._send_json(404, {"error": "not found"})
                return
            self._send_bytes(200, tokens_file.read_bytes(), "text/css; charset=utf-8")
            return

        target = (ui_root / clean).resolve()
        try:
            target.relative_to(ui_root)
        except ValueError:
            self._send_json(403, {"error": "invalid path"})
            return
        if not target.is_file():
            self._send_json(404, {"error": "not found"})
            return
        ctype, _ = mimetypes.guess_type(str(target))
        ctype = ctype or "application/octet-stream"
        if target.suffix == ".js" or ctype == "application/javascript":
            ctype = "application/javascript; charset=utf-8"
        elif ctype.startswith("text/"):
            ctype += "; charset=utf-8"
        self._send_bytes(200, target.read_bytes(), ctype)

    def do_OPTIONS(self) -> None:
        if not self._check_loopback_request():
            return
        path = urllib.parse.urlparse(self.path).path
        if path in ("/widgets.json", "/apps.json") or path.startswith("/obb/"):
            ok, cors = self._obb_cors_headers()
            if not ok:
                self._send_json(403, {"error": "Origin not allowed"})
                return
            self._send_bytes(204, b"", "text/plain; charset=utf-8", headers=cors)
            return
        self._send_bytes(204, b"", "text/plain; charset=utf-8")

    def do_GET(self) -> None:
        if not self._check_loopback_request():
            return
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query, keep_blank_values=True).items() if v}

        if path == "/health":
            self._send_json(200, {"ok": True, "service": "macrocockpit-desktop-pro", "version": "2.0.0"})
            return

        if path == "/mcp":
            self._send_json(405, {"error": "Use POST /mcp for Streamable HTTP MCP"})
            return

        # OpenBB Workspace public sanitized routes
        if path in ("/widgets.json", "/apps.json") or path.startswith("/obb/"):
            ok, cors = self._obb_cors_headers()
            if not ok:
                self._send_json(403, {"error": "Origin not allowed"})
                return
            if path == "/widgets.json":
                self._send_json(200, build_openbb_widgets_manifest(), headers=cors)
                return
            if path == "/apps.json":
                self._send_json(200, build_openbb_apps_manifest(), headers=cors)
                return
            widget_name = path[len("/obb/") :].strip("/")
            try:
                data = get_obb_widget_data(
                    widget_name,
                    qs,
                    base_dir=self.server.base_dir,  # type: ignore[attr-defined]
                    store=self.server.store,  # type: ignore[attr-defined]
                )
            except KeyError:
                self._send_json(404, {"error": "widget not found"}, headers=cors)
                return
            except ValueError as exc:
                self._send_json(400, {"error": str(exc)}, headers=cors)
                return
            if isinstance(data, str):
                self._send_bytes(200, data.encode("utf-8"), "text/markdown; charset=utf-8", headers=cors)
            else:
                self._send_json(200, data, headers=cors)
            return

        # Desktop API routes (require loopback Host + X-Desktop-Token)
        if path.startswith("/api/desktop/"):
            if not self._check_desktop_token():
                return

            if path == "/api/desktop/overview":
                base_dir: Path = self.server.base_dir  # type: ignore[attr-defined]
                state_path: Path = self.server.state_path  # type: ignore[attr-defined]
                state: dict[str, Any] = {}
                if state_path.exists():
                    try:
                        state = json.loads(state_path.read_text(encoding="utf-8"))
                    except Exception:
                        logger.warning("Desktop overview state read failed", exc_info=True)
                        state = {}
                debates = list_debates(base_dir)
                obb = probe_openbb_status()
                agy = probe_agy()
                tool_names: list[str] = []
                try:
                    from backend import mcp_server

                    tools_def = getattr(mcp_server, "DESKTOP_PRO_TOOL_DEFINITIONS", None)
                    base_defs = getattr(mcp_server, "TOOL_DEFINITIONS", [])
                    tool_names = [str(t.get("name")) for t in list(base_defs) + list(tools_def or []) if isinstance(t, dict)]
                except Exception:
                    logger.warning("Desktop overview MCP tool lookup failed", exc_info=True)
                    tool_names = []

                public_url = os.environ.get("MACROCOCKPIT_PUBLIC_URL", "https://news.shuashuabao.xyz")
                cov = dict(state.get("coverage") or {})
                snapshot_meta = {
                    "generated_at_bjt": state.get("generated_at_bjt") or "",
                    "signal_count": cov.get("signals_count", len(state.get("signals") or [])),
                    "category_breakdown": cov.get("category_breakdown") or {},
                    "stock_profile_count": cov.get("stock_profiles_count", len(state.get("stock_profiles") or {})),
                    "article_count": cov.get("articles_count", len(state.get("articles") or [])),
                    "debate_count": len(debates),
                }
                quick_syms: list[str] = []
                try:
                    ws = self.server.store.build_workspace(state, currency="USD")  # type: ignore[attr-defined]
                    for sp in ws.get("spot_balances") or []:
                        if not sp.get("is_cash") and sp.get("symbol"):
                            s = str(sp["symbol"]).upper()
                            if s not in quick_syms:
                                quick_syms.append(s)
                    for dv in ws.get("derivative_positions") or []:
                        if dv.get("symbol"):
                            s = str(dv["symbol"]).upper().split("-")[0]
                            if s not in quick_syms:
                                quick_syms.append(s)
                except Exception:
                    logger.warning("Desktop overview workspace lookup failed", exc_info=True)
                    quick_syms = []

                self._send_json(
                    200,
                    {
                        "ok": True,
                        "service": "macrocockpit-desktop-pro",
                        "openbb": obb,
                        "agy": agy,
                        "mcp": {
                            "desktop_pro": True,
                            "tools_count": len(tool_names),
                            "tool_names": tool_names,
                            "stdio_command": f"python -m backend.mcp_stdio --desktop-pro --base-dir {base_dir}",
                            "http_endpoint": f"http://127.0.0.1:{self.server.server_port}/mcp",
                        },
                        "coverage": cov,
                        "snapshot": snapshot_meta,
                        "generated_at_bjt": state.get("generated_at_bjt") or "",
                        "debates_count": len(debates),
                        "quick_symbols": quick_syms,
                        "web_url": os.environ.get("MACROCOCKPIT_LOCAL_WEB_URL", "http://127.0.0.1:8788"),
                        "public_url": public_url,
                        "sync_target_url": public_url,
                    },
                )
                return

            if path == "/api/desktop/openbb":
                dataset = qs.get("dataset") or "equity_quote"
                symbol = qs.get("symbol") or ""
                allow_sdk = str(qs.get("allow_sdk") or "0").lower() in ("1", "true", "yes")
                try:
                    res = query_openbb(dataset, symbol, allow_sdk_import=allow_sdk)
                    self._send_json(200, res)
                except ValueError as exc:
                    self._send_json(400, {"error": str(exc)})
                except Exception:
                    logger.warning("Desktop OpenBB query failed", exc_info=True)
                    self._send_json(500, {"ok": False, "error": "internal_server_error"})
                return

            if path == "/api/desktop/debates":
                did = qs.get("id")
                if did:
                    doc = get_debate(did, self.server.base_dir)  # type: ignore[attr-defined]
                    if doc is None:
                        self._send_json(404, {"error": "debate not found"})
                        return
                    self._send_json(200, {"ok": True, "debate": doc})
                    return
                debates = list_debates(self.server.base_dir)  # type: ignore[attr-defined]
                self._send_json(200, {"ok": True, "count": len(debates), "debates": debates})
                return

            self._send_json(404, {"error": "unknown desktop API endpoint"})
            return

        self._serve_ui_file(path)

    def do_POST(self) -> None:
        if not self._check_loopback_request():
            return
        path = urllib.parse.urlparse(self.path).path
        if path == "/mcp":
            if not self._check_desktop_token():
                return
            err_code, payload = self._read_json_body()
            if err_code is not None or payload is None:
                self._send_json(err_code or 400, {"error": "invalid MCP request"})
                return
            try:
                from backend import mcp_server

                version = (self.headers.get("MCP-Protocol-Version") or "").strip()
                headers = {"MCP-Protocol-Version": mcp_server.MCP_PROTOCOL_VERSION}
                if version and version not in mcp_server.SUPPORTED_MCP_VERSIONS:
                    self._send_json(400, {"error": f"Unsupported MCP-Protocol-Version: {version}"}, headers)
                    return
                dispatcher = mcp_server.create_mcp_dispatcher(
                    store=self.server.store,  # type: ignore[attr-defined]
                    base_dir=self.server.base_dir,  # type: ignore[attr-defined]
                    public_url=os.environ.get("MACROCOCKPIT_PUBLIC_URL", "https://news.shuashuabao.xyz"),
                    desktop_pro=True,
                    desktop_handlers=build_desktop_mcp_handlers(self.server.base_dir),  # type: ignore[attr-defined]
                )
                status, resp_obj = dispatcher.dispatch_jsonrpc(payload, role=ROLE_PORTFOLIO_RW, actor_id="desktop:pro")
                if status == 202 or resp_obj is None:
                    self._send_bytes(202, b"", "application/json; charset=utf-8", headers)
                else:
                    self._send_json(status, resp_obj, headers)
            except Exception as exc:
                logger.warning("Desktop MCP request failed", exc_info=True)
                self._send_json(500, {"error": f"{type(exc).__name__}: {exc}"})
            return

        if not path.startswith("/api/desktop/"):
            self._send_json(404, {"error": "unknown POST endpoint"})
            return

        if not self._check_desktop_token():
            return
        err_code, body = self._read_json_body()
        if err_code is not None or body is None:
            self._send_json(err_code or 400, {"error": "invalid JSON request"})
            return

        if path == "/api/desktop/debate":
            sym = str(body.get("symbol") or "").strip()
            if not sym:
                self._send_json(400, {"error": "symbol is required"})
                return
            try:
                doc = run_multi_agent_debate(
                    sym,
                    market=str(body.get("market") or "auto"),
                    mode=str(body.get("mode") or "fast"),
                    base_dir=self.server.base_dir,  # type: ignore[attr-defined]
                    include_portfolio=body.get("include_portfolio", True) is True,
                    sync_to_web=bool(body.get("sync_to_web", False)),
                )
                self._send_json(200, {"ok": True, "debate": doc})
            except ValueError as exc:
                self._send_json(400, {"error": str(exc)})
            except Exception:
                logger.warning("Desktop debate failed", exc_info=True)
                self._send_json(500, {"ok": False, "error": "internal_server_error"})
            return

        if path == "/api/desktop/agy/ask":
            prompt = str(body.get("prompt") or "").strip()
            if not prompt:
                self._send_json(400, {"error": "prompt is required"})
                return
            transport = str(body.get("transport") or "acp").strip().lower()
            try:
                timeout = max(3, min(120, int(body.get("timeout") or 60)))
            except (TypeError, ValueError):
                timeout = 60
            try:
                res = ask_agy(prompt, transport=transport, timeout=timeout, cwd=self.server.base_dir)  # type: ignore[attr-defined]
                self._send_json(200 if res.get("ok") else 502, res)
            except ValueError as exc:
                self._send_json(400, {"error": str(exc)})
            return

        if path == "/api/desktop/sync":
            base_dir: Path = self.server.base_dir  # type: ignore[attr-defined]
            state_path: Path = self.server.state_path  # type: ignore[attr-defined]
            refresh_state = bool(body.get("refresh_state", False))
            max_articles = max(1, min(100, int(body.get("max_articles") or 40)))
            try:
                if refresh_state:
                    from backend.signal_aggregator import generate_cockpit_state

                    generate_cockpit_state(refresh_fred=bool(body.get("refresh_fred", False)), output_path=state_path)
                from backend import vps_sync

                vps_sync.load_push_env(include_sync_token=True)
                payload, stats = vps_sync.collect_payload(state_path, max_articles)
                ok, detail = vps_sync.publish_via_http(payload)
                self._send_json(200 if ok else 502, {"ok": ok, "stats": stats, **detail})
            except Exception as exc:
                logger.warning("Desktop sync failed", exc_info=True)
                self._send_json(500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})
            return

        self._send_json(404, {"error": "unknown desktop API endpoint"})


class _IPv6ThreadingHTTPServer(ThreadingHTTPServer):
    address_family = socket.AF_INET6


def create_desktop_server(
    host: str = "127.0.0.1",
    port: int = 18790,
    *,
    base_dir: Path | str = COCKPIT_ROOT,
    desktop_token: str | None = None,
    db_path: Path | str | None = None,
    state_path: Path | str | None = None,
    ui_dir: Path | str | None = None,
) -> ThreadingHTTPServer:
    """Create a Desktop Pro ThreadingHTTPServer bound to loopback."""
    if host not in LOOPBACK_HOSTS:
        raise ValueError("Refusing non-loopback bind")
    root = Path(base_dir)
    tok = (desktop_token or os.environ.get("MACROCOCKPIT_DESKTOP_TOKEN") or "").strip()

    if not tok:
        tok = secrets.token_urlsafe(32)

    data_dir = resolve_data_dir(root)
    resolved_state = Path(state_path) if state_path else (data_dir / "cockpit_state.json")
    resolved_db = Path(db_path) if db_path else (data_dir / "user_journal.db")
    resolved_ui = Path(ui_dir) if ui_dir else (root / "desktop" / "ui")

    server_cls = _IPv6ThreadingHTTPServer if host == "::1" else ThreadingHTTPServer
    bind_host = "127.0.0.1" if host == "localhost" else host
    srv = server_cls((bind_host, port), DesktopProHandler)
    srv.base_dir = root  # type: ignore[attr-defined]
    srv.state_path = resolved_state  # type: ignore[attr-defined]
    srv.db_path = resolved_db  # type: ignore[attr-defined]
    srv.ui_dir = resolved_ui  # type: ignore[attr-defined]
    srv.desktop_token = tok  # type: ignore[attr-defined]
    srv.store = PortfolioStore(resolved_db)  # type: ignore[attr-defined]
    return srv


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="MacroCockpit Desktop Pro local terminal server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18790)
    parser.add_argument("--base-dir", type=Path, default=COCKPIT_ROOT)
    parser.add_argument("--token", default=None)
    args = parser.parse_args()

    if args.host not in LOOPBACK_HOSTS:
        print("Refusing non-loopback bind", file=sys.stderr)
        raise SystemExit(2)

    srv = create_desktop_server(args.host, args.port, base_dir=args.base_dir, desktop_token=args.token)
    display_host = "[::1]" if args.host == "::1" else args.host
    print(f"MacroCockpit Desktop Pro serving on http://{display_host}:{srv.server_port}/")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
