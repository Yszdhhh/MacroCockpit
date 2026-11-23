"""Contract tests for MCP 2025-11-25 HTTP endpoint, stdio bridge, role scoping, and 8 orthogonal tools."""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

COCKPIT_ROOT = Path(__file__).resolve().parent.parent
if str(COCKPIT_ROOT) not in sys.path:
    sys.path.insert(0, str(COCKPIT_ROOT))

from backend import mcp_server, server  # noqa: E402
from backend.public_snapshot import publish_generation  # noqa: E402


def _mcp_post(
    url: str,
    payload: dict[str, Any],
    *,
    token: str | None = None,
    origin: str | None = None,
) -> tuple[int, dict[str, str], dict[str, Any] | None]:
    headers: dict[str, str] = {
        "Content-Type": "application/json; charset=utf-8",
        "MCP-Protocol-Version": mcp_server.MCP_PROTOCOL_VERSION,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if origin:
        headers["Origin"] = origin
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw = resp.read()
            body = json.loads(raw.decode("utf-8")) if raw.strip() else None
            return (int(resp.status), {k.lower(): v for k, v in resp.headers.items()}, body)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        body = json.loads(raw.decode("utf-8")) if raw.strip() else None
        return (int(exc.code), {k.lower(): v for k, v in exc.headers.items()}, body)


def _seed_published_snapshot(base_dir: Path) -> None:
    src_state = json.loads((COCKPIT_ROOT / "data" / "cockpit_state.json").read_text(encoding="utf-8"))
    publish_generation(
        base_dir,
        src_state,
        [{"filename": "sample.md", "content": "# 样本深度研报\n\n正文内容"}],
        generation=1,
    )


def test_mcp_http_auth_roles_all_eight_tools_and_idempotency(tmp_path: Path) -> None:
    """Verify HTTP POST /mcp, GET 405, 2025-11-25 handshake, 202 notify, RO vs RW vs SYNC, and 8 tools."""
    _seed_published_snapshot(tmp_path)
    srv = server.create_server(
        "127.0.0.1",
        0,
        web_dir=COCKPIT_ROOT / "web",
        state_path=tmp_path / "data" / "cockpit_state.json",
        db_path=tmp_path / "data" / "user_journal.db",
        articles_dir=tmp_path / "articles",
        access_password="pw-mcp",
        read_token="tok-ro",
        rw_token="tok-rw",
        sync_token="tok-sync",
        role="consumer",
    )
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    base = f"http://127.0.0.1:{srv.server_port}"
    mcp_url = f"{base}/mcp"

    try:
        # GET /mcp -> 405 Method Not Allowed
        with pytest.raises(urllib.error.HTTPError) as exc_405:
            urllib.request.urlopen(mcp_url, timeout=5)
        assert exc_405.value.code == 405

        # ?token= query param forbidden -> 401; SYNC_PUBLISH token forbidden -> 403; bad Origin -> 403
        st_q, _, _ = _mcp_post(f"{mcp_url}?token=tok-ro", {"jsonrpc": "2.0", "id": 1, "method": "ping"})
        assert st_q == 401
        st_sync, _, _ = _mcp_post(mcp_url, {"jsonrpc": "2.0", "id": 1, "method": "ping"}, token="tok-sync")
        assert st_sync == 403
        st_orig, _, _ = _mcp_post(
            mcp_url,
            {"jsonrpc": "2.0", "id": 1, "method": "ping"},
            token="tok-ro",
            origin="https://evil.example.com",
        )
        assert st_orig == 403

        # Handshake + notifications/initialized -> 202 empty body
        st_init, _, init_res = _mcp_post(
            mcp_url,
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}},
            token="tok-ro",
        )
        assert st_init == 200
        assert init_res["result"]["protocolVersion"] == "2025-11-25"

        st_notif, _, notif_body = _mcp_post(
            mcp_url,
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            token="tok-ro",
        )
        assert st_notif == 202 and notif_body is None

        # tools/list: RO sees 5 tools; RW sees 8 tools
        _, _, ro_list = _mcp_post(mcp_url, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, token="tok-ro")
        ro_names = {t["name"] for t in ro_list["result"]["tools"]}
        assert ro_names == mcp_server.RO_TOOL_NAMES and len(ro_names) == 5

        _, _, rw_list = _mcp_post(mcp_url, {"jsonrpc": "2.0", "id": 3, "method": "tools/list"}, token="tok-rw")
        rw_names = {t["name"] for t in rw_list["result"]["tools"]}
        assert rw_names == mcp_server.ALL_TOOL_NAMES and len(rw_names) == 8

        # RO permission rejections
        _, _, ro_detail = _mcp_post(
            mcp_url,
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "portfolio_risk", "arguments": {"view": "detail"}}},
            token="tok-ro",
        )
        assert ro_detail["result"]["isError"] is True and ro_detail["result"]["structuredContent"]["code"] == 403

        _, _, ro_watch = _mcp_post(
            mcp_url,
            {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "signal_search", "arguments": {"scope": "watch"}}},
            token="tok-ro",
        )
        assert ro_watch["result"]["isError"] is True and ro_watch["result"]["structuredContent"]["code"] == 403

        _, _, ro_probe = _mcp_post(
            mcp_url,
            {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "signal_search", "arguments": {"scope": "held", "instrument_id": "BTC"}}},
            token="tok-ro",
        )
        assert ro_probe["result"]["isError"] is True and ro_probe["result"]["structuredContent"]["code"] == 403

        _, _, ro_rw_tool = _mcp_post(
            mcp_url,
            {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "preferences_get", "arguments": {}}},
            token="tok-ro",
        )
        assert ro_rw_tool["result"]["isError"] is True and ro_rw_tool["result"]["structuredContent"]["code"] == 403

        # RO public tools: market_snapshot, signal_search (ashare + sort=time), research_get, portfolio_risk summary (10% buckets), calendar_list
        _, _, snap_res = _mcp_post(
            mcp_url,
            {"jsonrpc": "2.0", "id": 8, "method": "tools/call", "params": {"name": "market_snapshot", "arguments": {"include": ["risk_gate", "positions"]}}},
            token="tok-ro",
        )
        assert snap_res["result"]["isError"] is False and "risk_gate" in snap_res["result"]["structuredContent"]

        _, _, sig_res = _mcp_post(
            mcp_url,
            {"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": "signal_search", "arguments": {"category": "ashare", "sort": "time", "limit": 5}}},
            token="tok-ro",
        )
        sig_sc = sig_res["result"]["structuredContent"]
        assert sig_res["result"]["isError"] is False and sig_sc["category"] == "ashare"
        assert all(s.get("holding_radar") is None for s in sig_sc["signals"])

        _, _, res_get = _mcp_post(
            mcp_url,
            {"jsonrpc": "2.0", "id": 10, "method": "tools/call", "params": {"name": "research_get", "arguments": {"symbol": "AAPL", "section": "all"}}},
            token="tok-ro",
        )
        res_sc = res_get["result"]["structuredContent"]
        assert res_get["result"]["isError"] is False and len(res_sc["filings"]["sec_filings"]) >= 1
        assert "portfolio_overlap" not in res_sc["checklist"]

        _, _, ro_risk = _mcp_post(
            mcp_url,
            {"jsonrpc": "2.0", "id": 11, "method": "tools/call", "params": {"name": "portfolio_risk", "arguments": {"view": "summary"}}},
            token="tok-ro",
        )
        assert ro_risk["result"]["structuredContent"]["masked"] is True
        assert ro_risk["result"]["structuredContent"]["bucket_width_pct"] == 10

        _, _, cal_res = _mcp_post(
            mcp_url,
            {"jsonrpc": "2.0", "id": 12, "method": "tools/call", "params": {"name": "calendar_list", "arguments": {"type": "macro", "limit": 5}}},
            token="tok-ro",
        )
        assert cal_res["result"]["isError"] is False and cal_res["result"]["structuredContent"]["ok"] is True

        # RW atomic commit: BTC short derivative + trade review in ONE transaction with request_id idempotency
        commit_ops = [
            {
                "op": "put",
                "entity": "derivative",
                "data": {
                    "instrument_id": "BTC-USDT-SWAP",
                    "side": "short",
                    "margin_mode": "isolated",
                    "qty": "0.01",
                    "qty_unit": "coin",
                    "entry_price": "50000",
                    "margin_amount": "100",
                    "margin_usd": "100",
                    "mmr": "0.005",
                    "sleeve": "bear_basket",
                },
            },
            {
                "op": "put",
                "entity": "review",
                "data": {
                    "trade_date": "2026-11-22",
                    "symbol": "BTC",
                    "direction": "做空",
                    "size_pct": 10.0,
                    "price_usd": 50000.0,
                    "status": "持有中",
                    "thesis": "MCP 同事务提交空单与复盘",
                },
            },
        ]
        _, _, rw_commit = _mcp_post(
            mcp_url,
            {
                "jsonrpc": "2.0",
                "id": 13,
                "method": "tools/call",
                "params": {
                    "name": "workspace_commit",
                    "arguments": {"request_id": "mcp-commit-1", "expected_revision": 0, "ops": commit_ops},
                },
            },
            token="tok-rw",
        )
        assert rw_commit["result"]["isError"] is False
        assert rw_commit["result"]["structuredContent"]["revision"] == 1
        assert rw_commit["result"]["structuredContent"]["applied_count"] == 2

        # Idempotent replay
        _, _, rw_replay = _mcp_post(
            mcp_url,
            {
                "jsonrpc": "2.0",
                "id": 14,
                "method": "tools/call",
                "params": {
                    "name": "workspace_commit",
                    "arguments": {"request_id": "mcp-commit-1", "expected_revision": 0, "ops": commit_ops},
                },
            },
            token="tok-rw",
        )
        assert rw_replay["result"]["structuredContent"]["idempotent_replay"] is True

        # import_preview + preferences_get + portfolio_risk(detail) via RW
        _, _, rw_pref = _mcp_post(
            mcp_url,
            {"jsonrpc": "2.0", "id": 15, "method": "tools/call", "params": {"name": "preferences_get", "arguments": {"include_rules": True}}},
            token="tok-rw",
        )
        assert rw_pref["result"]["isError"] is False and rw_pref["result"]["structuredContent"]["revision"] == 1

        _, _, rw_imp = _mcp_post(
            mcp_url,
            {"jsonrpc": "2.0", "id": 16, "method": "tools/call", "params": {"name": "import_preview", "arguments": {"text": "USD 钱包 1000"}}},
            token="tok-rw",
        )
        assert rw_imp["result"]["isError"] is False and rw_imp["result"]["structuredContent"]["can_commit"] is True

        _, _, rw_detail = _mcp_post(
            mcp_url,
            {"jsonrpc": "2.0", "id": 17, "method": "tools/call", "params": {"name": "portfolio_risk", "arguments": {"view": "detail"}}},
            token="tok-rw",
        )
        detail_sc = rw_detail["result"]["structuredContent"]
        assert rw_detail["result"]["isError"] is False and len(detail_sc["derivative_positions"]) == 1
        assert len(detail_sc["journal"]["trade_reviews"]) == 1
    finally:
        srv.shutdown()
        srv.server_close()
        th.join(timeout=5)


def test_mcp_stdio_offline_public_creates_no_db_and_exits_cleanly(tmp_path: Path) -> None:
    """Verify python -m backend.mcp_stdio --offline-public serves RO tools and creates zero SQLite DB."""
    _seed_published_snapshot(tmp_path)
    assert not (tmp_path / "data" / "user_journal.db").exists()

    reqs = "\n".join(
        [
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}}),
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
            json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "market_snapshot", "arguments": {"include": ["risk_gate"]}}}),
            json.dumps({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "workspace_commit", "arguments": {"request_id": "x", "expected_revision": 0, "ops": [{"op": "put", "entity": "setting", "data": {"key": "k", "value": 1}}]}}}),
        ]
    ) + "\n"

    proc = subprocess.run(
        [sys.executable, "-m", "backend.mcp_stdio", "--offline-public", "--base-dir", str(tmp_path)],
        cwd=str(COCKPIT_ROOT),
        input=reqs,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
        check=True,
    )
    lines = [json.loads(ln) for ln in proc.stdout.splitlines() if ln.strip()]
    assert [ln["id"] for ln in lines] == [1, 2, 3, 4]
    assert lines[0]["result"]["protocolVersion"] == "2025-11-25"
    assert len(lines[1]["result"]["tools"]) == 5
    assert lines[2]["result"]["isError"] is False
    assert lines[3]["result"]["isError"] is True and lines[3]["result"]["structuredContent"]["code"] == 403
    assert not (tmp_path / "data" / "user_journal.db").exists()

    # Missing URL/token in remote mode exits 2 cleanly
    bad_proc = subprocess.run(
        [sys.executable, "-m", "backend.mcp_stdio"],
        cwd=str(COCKPIT_ROOT),
        input="",
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={"PATH": "", "SystemRoot": r"C:\Windows"},
        timeout=15,
    )
    assert bad_proc.returncode == 2
