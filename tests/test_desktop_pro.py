"""Contract tests for MacroCockpit Desktop Pro (OpenBB/ACP/Debate/MCP) and Wave 4 Kiro audit fixes."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import pytest

COCKPIT_ROOT = Path(__file__).resolve().parent.parent
if str(COCKPIT_ROOT) not in sys.path:
    sys.path.insert(0, str(COCKPIT_ROOT))

from backend import (  # noqa: E402
    curation,
    mcp_server,
    mobile_push,
    portfolio_store,
    public_snapshot,
    server,
)
from backend.portfolio_store import PortfolioStore  # noqa: E402
from backend.public_snapshot import publish_generation  # noqa: E402
from desktop import acp_bridge, multi_agent_debate, openbb_bridge, pro_server  # noqa: E402

STATE_PATH = COCKPIT_ROOT / "data" / "cockpit_state.json"
WEB_DIR = COCKPIT_ROOT / "web"

FORBIDDEN_JARGON = (
    r"\bSleeve\b",
    r"\bE21\b",
    r"\bE32\b",
    r"\bE33\b",
    r"\bTier-1\b",
    r"\bRegime\b",
    r"\barbitration\b",
)


def _http_req(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    json_body: dict[str, Any] | None = None,
) -> tuple[int, dict[str, str], str]:
    hdrs = dict(headers or {})
    data: bytes | None = None
    if json_body is not None:
        hdrs.setdefault("Content-Type", "application/json; charset=utf-8")
        data = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return (
                int(resp.status),
                {k.lower(): v for k, v in resp.headers.items()},
                resp.read().decode("utf-8", errors="replace"),
            )
    except urllib.error.HTTPError as exc:
        return (
            int(exc.code),
            {k.lower(): v for k, v in exc.headers.items()},
            exc.read().decode("utf-8", errors="replace"),
        )


def _seed_isolated_base(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "cockpit_state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "articles").mkdir(parents=True, exist_ok=True)
    publish_generation(tmp_path, state, [], generation=1)
    return tmp_path, state


# ---------------------------------------------------------------------------
# Part A: Desktop Pro & Dual-Layer Architecture Contracts
# ---------------------------------------------------------------------------


def test_openbb_lazy_probe_widgets_and_masked_portfolio_risk(tmp_path: Path) -> None:
    """Verify probe_openbb_status avoids top-level openbb import and /obb/portfolio_risk masks all amounts."""
    cold = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys, desktop.pro_server, desktop.openbb_bridge, backend.mcp_stdio; "
                "st = desktop.openbb_bridge.probe_openbb_status(); "
                "assert 'openbb' not in sys.modules, 'openbb eagerly imported!'; "
                "print(st.get('installed'), st.get('version'))"
            ),
        ],
        cwd=str(COCKPIT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
    )
    assert cold.returncode == 0, cold.stderr or cold.stdout
    assert "openbb" not in sys.modules

    manifest = openbb_bridge.build_openbb_widgets_manifest()
    assert set(manifest.keys()) == {"macro_signals", "portfolio_risk", "stock_thesis", "macro_board", "deep_debates"}
    apps = openbb_bridge.build_openbb_apps_manifest()
    assert isinstance(apps, list) and len(apps) >= 1

    base, _state = _seed_isolated_base(tmp_path)
    store = PortfolioStore(base / "data" / "user_journal.db")
    store.apply_changes(
        ops=[
            {"op": "put", "entity": "spot", "account_id": "main", "symbol": "USD", "asset_class": "cash", "qty": "1000", "avg_cost": "1"},
            {"op": "put", "entity": "spot", "account_id": "main", "symbol": "AAPL", "asset_class": "equity", "qty": "5", "avg_cost": "100"},
        ],
        expected_revision=0,
        actor_id="owner",
        request_id="req-obb-1",
    )
    risk_rows = openbb_bridge.get_obb_widget_data("portfolio_risk", {}, base_dir=base, store=store)
    assert isinstance(risk_rows, list) and len(risk_rows) >= 3
    serialized = json.dumps(risk_rows, ensure_ascii=False)
    for forbidden in ("1520", "1,520", "$1,520", "1000", "$1,000", "500"):
        assert forbidden not in serialized, f"Private amount {forbidden} leaked in /obb/portfolio_risk: {serialized}"
    assert "%" in serialized


def test_multi_agent_debate_markdown_sanitization_and_llm_degradation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify fast debate writes clean multiline ta_*.md without private amounts, and llm degrades smoothly."""
    base, _state = _seed_isolated_base(tmp_path)
    store = PortfolioStore(base / "data" / "user_journal.db")
    store.apply_changes(
        ops=[
            {"op": "put", "entity": "spot", "account_id": "main", "symbol": "USD", "asset_class": "cash", "qty": "1520", "avg_cost": "1"},
            {"op": "put", "entity": "spot", "account_id": "main", "symbol": "NVDA", "asset_class": "equity", "qty": "10", "avg_cost": "120"},
        ],
        expected_revision=0,
        actor_id="owner",
        request_id="req-deb-1",
    )

    doc = multi_agent_debate.run_multi_agent_debate("NVDA", mode="fast", base_dir=base, include_portfolio=True)
    for role_key in ("technical_analyst", "fundamental_analyst", "sentiment_analyst", "debate", "trader_verdict"):
        assert role_key in doc
    assert set(doc["debate"].keys()) >= {"bull_case", "bear_case", "rebuttal"}
    assert set(doc["trader_verdict"].keys()) >= {
        "stance",
        "confidence",
        "position_cap_hint",
        "invalidation_rule",
        "checklist_passed",
    }

    md_path = base / "articles" / doc["report_file"]
    assert md_path.is_file() and md_path.name.startswith("ta_NVDA_")
    md_text = md_path.read_text(encoding="utf-8")
    lines = md_text.splitlines()
    assert lines[0] == "---" and "---" in lines[1:12], "YAML frontmatter delimiters must be on standalone lines"
    assert "## 一、" in md_text and "## 二、" in md_text and "## 三、" in md_text
    assert "\n## 一、" in md_text and "\n## 二、" in md_text and "\n## 三、" in md_text
    for leak in ("1520", "1,520", "$1,520", "1200", "1,200"):
        assert leak not in md_text, f"Private portfolio amount {leak} leaked in {md_path.name}"
    for pat in FORBIDDEN_JARGON:
        assert not re.search(pat, md_text, flags=re.IGNORECASE), f"Forbidden jargon {pat} in debate markdown"
    assert "待补" not in md_text and not re.search(r"\bN/A\b", md_text)

    # llm mode graceful fallback when AgyBridge.ask_agy fails
    monkeypatch.setattr(
        acp_bridge.AgyBridge,
        "ask_agy",
        lambda self, *args, **kwargs: {"ok": False, "error": "simulated acp timeout", "reply": ""},
    )
    degraded = multi_agent_debate.run_multi_agent_debate("AAPL", mode="llm", base_dir=base)
    assert degraded["effective_mode"] == "fast"
    assert degraded["degraded_from_llm"] is True
    assert "simulated acp timeout" in degraded["degrade_reason"]


def test_desktop_pro_server_security_cors_and_mcp_12_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify non-loopback refusal, X-Desktop-Token enforcement, OpenBB CORS, and 12-tool /mcp."""
    monkeypatch.delenv("MACROCOCKPIT_ACCESS_PASSWORD", raising=False)
    monkeypatch.delenv("MACROCOCKPIT_DESKTOP_TOKEN", raising=False)
    with pytest.raises(ValueError):
        pro_server.create_desktop_server("0.0.0.0", 0, base_dir=tmp_path)

    base, _state = _seed_isolated_base(tmp_path)
    srv = pro_server.create_desktop_server(
        "127.0.0.1",
        0,
        base_dir=base,
        desktop_token="tok-desktop-test",
        db_path=base / "data" / "user_journal.db",
        state_path=base / "data" / "cockpit_state.json",
        ui_dir=COCKPIT_ROOT / "desktop" / "ui",
    )
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    base_url = f"http://127.0.0.1:{srv.server_port}"

    try:
        # Missing or wrong token -> 401
        st_no, _, _ = _http_req(f"{base_url}/api/desktop/overview")
        assert st_no == 401
        st_bad, _, _ = _http_req(f"{base_url}/api/desktop/overview", headers={"X-Desktop-Token": "wrong"})
        assert st_bad == 401

        # External evil Origin -> 403 on /api/desktop/* and /widgets.json
        st_evil_api, _, _ = _http_req(
            f"{base_url}/api/desktop/overview",
            headers={"X-Desktop-Token": "tok-desktop-test", "Origin": "https://evil.example.com"},
        )
        assert st_evil_api == 403
        st_evil_obb, _, _ = _http_req(f"{base_url}/widgets.json", headers={"Origin": "https://evil.example.com"})
        assert st_evil_obb == 403

        # OpenBB pro.openbb.co CORS allowed on /widgets.json, /apps.json, /obb/*
        st_w, hdr_w, body_w = _http_req(f"{base_url}/widgets.json", headers={"Origin": "https://pro.openbb.co"})
        assert st_w == 200 and hdr_w.get("access-control-allow-origin") == "https://pro.openbb.co"
        assert len(json.loads(body_w)) == 5

        st_obb, hdr_obb, _ = _http_req(f"{base_url}/obb/portfolio_risk", headers={"Origin": "https://pro.openbb.co"})
        assert st_obb == 200 and hdr_obb.get("access-control-allow-origin") == "https://pro.openbb.co"

        # HTML injection includes token & Cache-Control: no-store
        st_ui, hdr_ui, body_ui = _http_req(f"{base_url}/")
        assert st_ui == 200 and "no-store" in hdr_ui.get("cache-control", "")
        assert '<meta name="desktop-token" content="tok-desktop-test">' in body_ui

        # /mcp exposes all 12 tools with desktop token
        st_mcp, _, body_mcp = _http_req(
            f"{base_url}/mcp",
            method="POST",
            headers={"X-Desktop-Token": "tok-desktop-test"},
            json_body={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
        assert st_mcp == 200
        tool_names = {t["name"] for t in json.loads(body_mcp)["result"]["tools"]}
        assert len(tool_names) == 12
        assert {"desktop_status", "openbb_query", "deep_research_run", "sync_to_cloud"}.issubset(tool_names)
    finally:
        srv.shutdown()
        srv.server_close()


def test_mcp_8_vs_12_isolation_and_stdio_flags(tmp_path: Path) -> None:
    """Verify McpDispatcher 8 vs 12 gating and mcp_stdio --desktop-pro / --offline-public flags."""
    base, _state = _seed_isolated_base(tmp_path)
    store = PortfolioStore(base / "data" / "user_journal.db")

    disp_cloud = mcp_server.McpDispatcher(store=store, base_dir=base, desktop_pro=False)
    _, ro_cloud = disp_cloud.dispatch_jsonrpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, role="READ_ONLY", actor_id="ro")
    _, rw_cloud = disp_cloud.dispatch_jsonrpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, role="PORTFOLIO_RW", actor_id="rw")
    assert len(ro_cloud["result"]["tools"]) == 5
    assert len(rw_cloud["result"]["tools"]) == 8
    _, denied_cloud = disp_cloud.dispatch_jsonrpc(
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "desktop_status", "arguments": {}}},
        role="PORTFOLIO_RW",
        actor_id="rw",
    )
    assert denied_cloud["error"]["code"] == -32602

    disp_pro = mcp_server.McpDispatcher(store=store, base_dir=base, desktop_pro=True)
    _, ro_pro = disp_pro.dispatch_jsonrpc({"jsonrpc": "2.0", "id": 4, "method": "tools/list"}, role="READ_ONLY", actor_id="ro")
    _, rw_pro = disp_pro.dispatch_jsonrpc({"jsonrpc": "2.0", "id": 5, "method": "tools/list"}, role="PORTFOLIO_RW", actor_id="rw")
    assert len(ro_pro["result"]["tools"]) == 5
    assert len(rw_pro["result"]["tools"]) == 12
    _, ro_call_pro = disp_pro.dispatch_jsonrpc(
        {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "desktop_status", "arguments": {}}},
        role="READ_ONLY",
        actor_id="ro",
    )
    assert ro_call_pro["result"]["isError"] is True and ro_call_pro["result"]["structuredContent"]["code"] == 403

    # Mutually exclusive flags exit 2
    proc_conflict = subprocess.run(
        [sys.executable, "-m", "backend.mcp_stdio", "--offline-public", "--desktop-pro", "--base-dir", str(base)],
        cwd=str(COCKPIT_ROOT),
        input="",
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
    )
    assert proc_conflict.returncode == 2

    # --desktop-pro alone exposes 12 tools over stdio
    stdio_req = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n"
    proc_pro = subprocess.run(
        [sys.executable, "-m", "backend.mcp_stdio", "--desktop-pro", "--base-dir", str(base)],
        cwd=str(COCKPIT_ROOT),
        input=stdio_req,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
    )
    assert proc_pro.returncode == 0, proc_pro.stderr
    resp = json.loads(proc_pro.stdout.strip().splitlines()[0])
    assert len(resp["result"]["tools"]) == 12


# ---------------------------------------------------------------------------
# Part B: Wave 4 Kiro High/Medium Audit Regressions (#1, #3-#11, #13)
# ---------------------------------------------------------------------------


def test_kiro_wave4_high_fixes_1_3_4_11(tmp_path: Path) -> None:
    """Regress Kiro #1 (RO scope=held leak), #3 (strategy_targets), #4 (headwind alerts), #11 (MCP bands)."""
    base, state = _seed_isolated_base(tmp_path)
    pub = public_snapshot.project_public_state(state, include_candidates=False)
    store = PortfolioStore(base / "data" / "user_journal.db")
    store.apply_changes(
        ops=[{"op": "put", "entity": "spot", "account_id": "main", "symbol": "NVDA", "asset_class": "equity", "qty": "5", "avg_cost": "100"}],
        expected_revision=0,
        actor_id="owner",
        request_id="req-kiro-hi",
    )

    # #3 & #11: build_workspace(public_state=pub) preserves non-zero strategy targets + MCP bands
    ws_real = store.build_workspace(public_state=pub)
    by_id_real = {r["id"]: r for r in ws_real["strategy_comparison"]}
    assert float(by_id_real["bear_basket"]["target_pct"]) == 5.0

    synth_state = json.loads(json.dumps(state))
    synth_state.pop("strategy_targets", None)
    for p in synth_state["positions"]:
        if p["id"] == "btc_trend":
            p["baseline_pct"] = 40.0
            p["recommended_pct"] = 40.0
    synth_pub = public_snapshot.project_public_state(synth_state, include_candidates=False)
    ws_synth = store.build_workspace(public_state=synth_pub)
    by_id_synth = {r["id"]: r for r in ws_synth["strategy_comparison"]}
    assert float(by_id_synth["btc_trend"]["target_pct"]) == 40.0

    disp = mcp_server.McpDispatcher(store=store, base_dir=base, desktop_pro=False)
    _, risk_res = disp.dispatch_jsonrpc(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "portfolio_risk", "arguments": {}}},
        role="READ_ONLY",
        actor_id="ro",
    )
    bands = risk_res["result"]["structuredContent"]["strategy_bands"]
    bear_band = next(b for b in bands if b["id"] == "bear_basket")
    assert bear_band["recommended_pct_bucket"] != "无法估算"
    assert bear_band["drift_pct_bucket"] != "无法估算"

    # #1: READ_ONLY MCP signal_search(scope="held") + event_id blocked
    nvda_sig = next((s for s in pub["signals"] if "NVDA" in (s.get("tickers") or [])), pub["signals"][0])
    _, held_res = disp.dispatch_jsonrpc(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "signal_search", "arguments": {"scope": "held", "event_id": nvda_sig["id"]}},
        },
        role="READ_ONLY",
        actor_id="ro",
    )
    assert held_res["result"]["isError"] is True and held_res["result"]["structuredContent"]["code"] == 403

    # #1 HTTP /api/v2/signals?scope=held blocked 403 for READ_ONLY token
    srv = server.create_server(
        "127.0.0.1",
        0,
        web_dir=WEB_DIR,
        state_path=base / "data" / "cockpit_state.json",
        db_path=base / "data" / "user_journal.db",
        access_password="pw-k1",
        read_token="tok-ro-k1",
        rw_token="tok-rw-k1",
        sync_token="tok-sync-k1",
        role="consumer",
    )
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        u_base = f"http://127.0.0.1:{srv.server_port}"
        for qs in ("scope=held", f"scope=held&event_id={urllib.parse.quote(nvda_sig['id'])}", "scope=held&category=ai"):
            st_sig, _, _ = _http_req(f"{u_base}/api/v2/signals?{qs}", headers={"Authorization": "Bearer tok-ro-k1"})
            assert st_sig == 403, f"READ_ONLY leaked /api/v2/signals?{qs}"
    finally:
        srv.shutdown()
        srv.server_close()

    # #4: mobile_push triggers on curated_signals & lazy holding_radar without workspace["signals"]
    hw_state = json.loads(json.dumps(state))
    hw_state["signals"] = [
        {
            "id": "hw-nvda-1",
            "source": "SEC EDGAR",
            "category": "ai",
            "direction": "bearish",
            "importance": "高",
            "attention_score": 88,
            "tickers": ["NVDA"],
            "title": "NVDA 出口限制再收紧构成持仓逆风",
            "time_bjt": "2026-11-23 00:30",
        }
    ]
    ws_lazy = {
        "revision": 2,
        "summary": ws_real["summary"],
        "positions": [{"symbol": "NVDA", "direction": "long"}],
        "watchlist": [],
        "curation_rules": [],
        "strategy_comparison": ws_real["strategy_comparison"],
    }
    digest_lazy = mobile_push.build_mobile_digest_payload(hw_state, workspace=ws_lazy, mode="headwind", include_amounts=False)
    assert "NVDA" in digest_lazy["text"]
    alerts_lazy = mobile_push.evaluate_alert_triggers(hw_state, ws_lazy)
    assert any(a["rule_id"] == "holding_headwind" for a in alerts_lazy)

    ws_curated = {
        "revision": 3,
        "summary": ws_real["summary"],
        "curated_signals": [
            {
                "id": "hw-nvda-cur",
                "source": "AlphaDesk",
                "title": "NVDA 毛利率指引下修",
                "importance": "高",
                "final_score": 86,
                "holding_radar": {"relation": "逆风", "detail": "持仓多头逆风"},
            }
        ],
    }
    alerts_cur = mobile_push.evaluate_alert_triggers(state, ws_curated)
    assert any(a["rule_id"] == "holding_headwind" for a in alerts_cur)


def test_kiro_wave4_medium_fixes_5_6_7_8_9_10_13(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Regress Kiro #5 (rate limit), #6 (Host allowlist), #7 (V1 rev/audit), #8 (403 import), #9 (alias delete), #10 (ReDoS), #13 (logout)."""
    # #10: ReDoS prevention on both curation and portfolio_store
    for bad_rx in (r"(\w|\w\w)+@", r"(a|ab)*", r"((a|b)c)+"):
        with pytest.raises(ValueError):
            curation.validate_curation_rule("regex", bad_rx, "block", 0)
        with pytest.raises(ValueError):
            portfolio_store._validate_safe_regex(bad_rx)
    curation.validate_curation_rule("regex", r"(NVDA|AMD)", "adjust", 3)
    portfolio_store._validate_safe_regex(r"(NVDA|AMD)")

    # #9: spot remove with account_id="main" deletes row in acct_default
    base, _state = _seed_isolated_base(tmp_path)
    store = PortfolioStore(base / "data" / "user_journal.db")
    store.apply_changes(
        ops=[{"op": "put", "entity": "spot", "account_id": "main", "symbol": "NVDA", "asset_class": "equity", "qty": "5", "avg_cost": "100"}],
        expected_revision=0,
        actor_id="owner",
        request_id="req-9-put",
    )
    del_res = store.apply_changes(
        ops=[{"op": "remove", "entity": "spot", "account_id": "main", "symbol": "NVDA"}],
        expected_revision=1,
        actor_id="owner",
        request_id="req-9-del",
    )
    assert del_res["revision"] == 2
    assert store.build_workspace()["spot_balances"] == []

    # #6 (Host check in passwordless loopback), #7 (V1 journal revision/audit), #8 (cross-actor import commit -> 403), #13 (invalid cookie logout)
    srv_open = server.create_server(
        "127.0.0.1",
        0,
        web_dir=WEB_DIR,
        state_path=base / "data" / "cockpit_state.json",
        db_path=base / "data" / "user_journal.db",
        access_password="",
        read_token="",
        rw_token="",
        sync_token="",
        role="standalone",
    )
    th_open = threading.Thread(target=srv_open.serve_forever, daemon=True)
    th_open.start()
    try:
        u_open = f"http://127.0.0.1:{srv_open.server_port}"
        # #6: evil Host rejected 403
        st_host, _, _ = _http_req(
            f"{u_open}/api/v2/workspace",
            headers={"Host": f"evil.example:{srv_open.server_port}"},
        )
        assert st_host == 403

        # #7: V1 POST /api/journal/essay advances portfolio_revision & inserts audit_event
        rev_before = srv_open.portfolio_store.get_revision()
        with srv_open.portfolio_store._connect("read") as db:
            aud_before = int(db.execute("SELECT COUNT(*) FROM audit_event").fetchone()[0])
        st_v1, _, _ = _http_req(
            f"{u_open}/api/journal/essay",
            method="POST",
            headers={"Origin": u_open, "X-Macro-Request": "1"},
            json_body={"title": "Wave4 V1 随笔", "body_md": "正文验证版本联动"},
        )
        assert st_v1 == 200
        assert srv_open.portfolio_store.get_revision() == rev_before + 1
        with srv_open.portfolio_store._connect("read") as db:
            aud_after = int(db.execute("SELECT COUNT(*) FROM audit_event").fetchone()[0])
        assert aud_after == aud_before + 1

        # #13: forged invalid Cookie on /api/logout does NOT inflate _revoked_cookies
        assert len(srv_open._revoked_cookies) == 0
        for i in range(3):
            st_lo, _, _ = _http_req(
                f"{u_open}/api/logout",
                method="POST",
                headers={"Origin": u_open, "X-Macro-Request": "1", "Cookie": f"macro_auth=forged.{9999999999 + i}.deadbeef"},
                json_body={},
            )
            assert st_lo == 200
        assert len(srv_open._revoked_cookies) == 0
    finally:
        srv_open.shutdown()
        srv_open.server_close()

    # #8 (cross-actor import commit -> 403) + #5 (non-loopback peer spoofing CF-Connecting-IP hits 429)
    monkeypatch.setenv("MACROCOCKPIT_TRUST_PROXY", "0")
    srv_auth = server.create_server(
        "127.0.0.1",
        0,
        web_dir=WEB_DIR,
        state_path=base / "data" / "cockpit_state.json",
        db_path=base / "data" / "user_journal.db",
        access_password="pw-wave4",
        read_token="tok-ro-w4",
        rw_token="tok-rw-w4",
        sync_token="tok-sync-w4",
        role="standalone",
    )
    # Force non-loopback peer_ip so CF-Connecting-IP spoofing cannot bypass rate limit
    orig_finish = srv_auth.finish_request
    srv_auth.finish_request = lambda req, addr: orig_finish(req, ("203.0.113.50", addr[1]))  # type: ignore[method-assign]
    th_auth = threading.Thread(target=srv_auth.serve_forever, daemon=True)
    th_auth.start()
    try:
        u_auth = f"http://127.0.0.1:{srv_auth.server_port}"
        # Create preview as actor rw-A, attempt commit as different actor rw-B via cookie or rw_token mismatch
        st_prev, _, prev_body = _http_req(
            f"{u_auth}/api/v2/import/preview",
            method="POST",
            headers={"Authorization": "Bearer tok-rw-w4"},
            json_body={"account_id": "main", "format": "text", "mode": "upsert", "text": "USD 钱包 100"},
        )
        assert st_prev == 200
        preview_id = json.loads(prev_body)["preview_id"]

        # Log in via cookie (actor cookie:admin) -> different from config:portfolio_rw
        st_login, hdr_login, _ = _http_req(
            f"{u_auth}/api/login",
            method="POST",
            headers={"Origin": u_auth, "CF-Connecting-IP": "198.51.100.1"},
            json_body={"password": "pw-wave4"},
        )
        assert st_login == 200
        cookie_val = hdr_login["set-cookie"].split(";", 1)[0]
        cur_rev = srv_auth.portfolio_store.get_revision()
        st_commit, _, body_commit = _http_req(
            f"{u_auth}/api/v2/import/commit",
            method="POST",
            headers={"Origin": u_auth, "X-Macro-Request": "1", "Cookie": cookie_val},
            json_body={"preview_id": preview_id, "request_id": "req-x-actor", "expected_revision": cur_rev},
        )
        assert st_commit == 403 and "error" in json.loads(body_commit)

        # #5: rotating CF-Connecting-IP on non-loopback peer still triggers 429 on 11th bad password
        codes: list[int] = []
        for idx in range(11):
            st_bad, _, _ = _http_req(
                f"{u_auth}/api/login",
                method="POST",
                headers={"Origin": u_auth, "CF-Connecting-IP": f"198.51.100.{idx + 10}"},
                json_body={"password": "wrong-password"},
            )
            codes.append(st_bad)
        assert codes[-1] == 429
    finally:
        srv_auth.shutdown()
        srv_auth.server_close()


def test_opus_and_codex_wave5_regressions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify Kiro Opus 5.5 + Codex 6.2 Sol fixes across privacy gates, valuation math, import, alerts, and MCP."""
    from backend import curation, import_positions, mobile_push, public_snapshot, stock_research, valuation

    base, state = _seed_isolated_base(tmp_path)
    store = PortfolioStore(base / "data" / "user_journal.db")
    store.apply_changes(
        ops=[
            {"op": "put", "entity": "spot", "account_id": "main", "symbol": "USD", "asset_class": "cash", "qty": "2000", "avg_cost": "1"},
            {"op": "put", "entity": "spot", "account_id": "main", "symbol": "NVDA", "asset_class": "equity", "qty": "10", "avg_cost": "120"},
            {
                "op": "put",
                "entity": "derivative",
                "account_id": "main",
                "symbol": "BTC-USDT-SWAP",
                "side": "short",
                "margin_mode": "isolated",
                "qty": "10",
                "qty_unit": "contract",
                "contract_multiplier": "0.01",
                "entry_price": "50000",
                "margin_usd": "100",
                "mmr": "0.005",
                "venue_liq": "72000",
            },
        ],
        expected_revision=0,
        actor_id="owner",
        request_id="req-w5-seed",
    )

    # Opus P0-1: include_portfolio=True keeps exposure local ONLY, never inside public articles/ta_*.md
    doc = multi_agent_debate.run_multi_agent_debate("NVDA", mode="fast", base_dir=base, include_portfolio=True)
    md_text = (base / "articles" / doc["report_file"]).read_text(encoding="utf-8")
    assert "占净值" not in md_text and "现货多头" not in md_text and "组合关联" not in md_text
    assert "占净值" in doc["facts_summary"]["portfolio_exposure"]
    pub_proj = public_snapshot.project_public_state(
        {
            **state,
            "articles": [
                {"filename": "safe.md", "title": "公开研报", "body_md": "正常宏观分析"},
                {"filename": "leak.md", "title": "泄露文", "body_md": "现货多头占净值 17.3%"},
            ],
        }
    )
    assert [a["filename"] for a in pub_proj["articles"]] == ["safe.md"]

    # Codex H2: partial edit of derivative preserves qty_unit, contract_multiplier, mmr, and venue_liq
    ws_before = store.build_workspace(public_state=state, currency="USD")
    d_row = ws_before["derivative_positions"][0]
    assert d_row["venue_liq"] == "72000"
    store.apply_changes(
        ops=[{"op": "put", "entity": "derivative", "id": d_row["id"], "note": "仅改备注"}],
        expected_revision=1,
        actor_id="owner",
        request_id="req-w5-edit",
    )
    ws_after = store.build_workspace(public_state=state, currency="USD")
    d_after = ws_after["derivative_positions"][0]
    assert d_after["qty_unit"] == "contract"
    assert d_after["contract_multiplier"] == "0.01"
    assert d_after["mmr"] == "0.005"
    assert d_after["venue_liq"] == "72000"
    assert d_after["thesis"] == "仅改备注"

    # Opus P1-1 & Codex L5: /obb/portfolio_risk removes exact delta/target leakage & rejects bad host / bad limit
    srv_desk = pro_server.create_desktop_server(
        "127.0.0.1",
        0,
        base_dir=base,
        desktop_token="tok-w5-desk",
        db_path=base / "data" / "user_journal.db",
        state_path=base / "data" / "cockpit_state.json",
    )
    th_desk = threading.Thread(target=srv_desk.serve_forever, daemon=True)
    th_desk.start()
    try:
        u_desk = f"http://127.0.0.1:{srv_desk.server_port}"
        st_obb, _, body_obb = _http_req(f"{u_desk}/obb/portfolio_risk")
        assert st_obb == 200
        obb_rows = json.loads(body_obb)
        assert not any("偏离 " in str(r.get("detail") or "") for r in obb_rows)
        st_host, _, _ = _http_req(f"{u_desk}/obb/portfolio_risk", headers={"Host": f"attacker.example:{srv_desk.server_port}"})
        assert st_host == 403
        st_lim, _, _ = _http_req(f"{u_desk}/obb/signals?limit=abc")
        assert st_lim == 400
    finally:
        srv_desk.shutdown()
        srv_desk.server_close()

    # Codex H3 & H4: CNY cash converts by FX, negative qty rejected, BTC-USDC-SWAP keeps USDC, USDT != 1:1 USD
    prev_cny = import_positions.preview_import(store, "main", "csv", "symbol,qty\nCNY,700\n", "upsert", "owner")
    assert prev_cny["can_commit"] is True and prev_cny["ops"][0]["data"]["quote_currency"] == "CNY"
    prev_neg = import_positions.preview_import(store, "main", "text", "BTC long -2 contracts @100", "upsert", "owner")
    assert prev_neg["has_errors"] is True and prev_neg["can_commit"] is False
    prev_usdc = import_positions.preview_import(
        store, "main", "text", "BTC-USDC-SWAP long 0.01 coin @ 50000 isolated margin=100", "upsert", "owner"
    )
    assert prev_usdc["ops"][0]["data"]["quote_currency"] == "USDC"

    val_fx = valuation.evaluate_portfolio(
        spot_balances=[
            {"id": "s1", "account_id": "a", "instrument_id": "CNY", "symbol": "CNY", "quote_currency": "CNY", "is_cash": True, "qty": "700", "avg_cost": "1"},
            {"id": "s2", "account_id": "a", "instrument_id": "COINX", "symbol": "COINX", "quote_currency": "USD", "is_cash": False, "qty": "1", "avg_cost": "95"},
        ],
        derivative_positions=[
            {"id": "d1", "account_id": "a", "instrument_id": "BTC", "symbol": "BTC", "side": "long", "margin_mode": "cross", "contract_type": "linear", "qty": "1", "qty_unit": "coin", "contract_multiplier": "1", "entry_price": "100", "quote_currency": "USDT", "margin_currency": "USDT", "margin_usd": "20"}
        ],
        quotes={
            "USDT-USD": {"price": "0.95", "quote_currency": "USD"},
            "COINX": {"price": "100", "quote_currency": "USDT"},
            "BTC": {"price": "100", "quote_currency": "USDT"},
            "BTC-USDT-SWAP": {"mark_price": "110", "quote_currency": "USDT"},
        },
        fx_rate="7",
    )
    assert val_fx["cash_and_spot_usd"] == valuation.Decimal("195")
    assert val_fx["derivatives_upl_usd"] == valuation.Decimal("9.50")

    # Codex H5: two same-symbol, same-side positions emit distinct liquidation alerts
    alerts = mobile_push.evaluate_alert_triggers(
        state,
        workspace={
            "revision": 2,
            "derivative_positions": [
                {"id": "d_a", "account_id": "a1", "symbol": "BTC-USDT-SWAP", "side": "long", "margin_mode": "isolated", "liq_distance_pct": "7.0"},
                {"id": "d_b", "account_id": "a2", "symbol": "BTC-USDT-SWAP", "side": "long", "margin_mode": "isolated", "liq_distance_pct": "1.0"},
            ],
        },
    )
    liq_alerts = [a for a in alerts if a.get("rule_id") == "liq_distance_critical"]
    assert len(liq_alerts) == 2
    assert {a.get("event_id") for a in liq_alerts} == {"d_a", "d_b"}

    # Codex L4 & L3 & M12: negative growth quick_thesis, scope=rules, JSON-RPC notification & null check
    neg_prof = stock_research._assemble_profile(
        "NVDA",
        yahoo={"price": 100.0, "rev_growth": -0.30, "earnings_growth": -0.40},
        gtimg={"price": 100.0},
        risk_gate={"light": "GREEN", "long_risk_cap": 1.0},
    )
    assert neg_prof is not None
    assert not any("核心基本面具备支撑" in x for x in neg_prof["quick_thesis"]["bull_points"])
    assert any("营收" in x or "盈利" in x for x in neg_prof["quick_thesis"]["bear_points"])

    rule_hits = curation.rank_curated_signals(
        [
            {"id": "r1", "category": "ai", "title": "NVDA Rubin launch", "attention_score": 70, "time_bjt": "2026-11-23 00:00:00"},
            {"id": "r2", "category": "ai", "title": "Other stock note", "attention_score": 70, "time_bjt": "2026-11-23 00:00:00"},
        ],
        rules=[{"kind": "keyword", "target": "Rubin", "action": "watch", "points": 0, "enabled": True}],
        scope="rules",
    )
    assert [x["id"] for x in rule_hits] == ["r1"]

    disp = mcp_server.create_mcp_dispatcher(store=store, base_dir=base, desktop_pro=False)
    st_notif, body_notif = disp.dispatch_jsonrpc({"jsonrpc": "2.0", "method": "ping"}, role="READ_ONLY", actor_id="ro")
    assert st_notif == 202 and body_notif is None
    _, body_null = disp.dispatch_jsonrpc(
        {"jsonrpc": "2.0", "id": 11, "method": "tools/call", "params": {"name": "signal_search", "arguments": {"limit": None}}},
        role="READ_ONLY",
        actor_id="ro",
    )
    assert body_null is not None and body_null["result"]["isError"] is True

    # Codex M7 & M10: cookie revocation persists across server restart; /api/push rejects string "false"
    db_persist = base / "data" / "persist_auth.db"
    srv1 = server.create_server(
        "127.0.0.1",
        0,
        web_dir=WEB_DIR,
        state_path=base / "data" / "cockpit_state.json",
        db_path=db_persist,
        access_password="pw-persist",
        role="standalone",
    )
    th1 = threading.Thread(target=srv1.serve_forever, daemon=True)
    th1.start()
    try:
        u1 = f"http://127.0.0.1:{srv1.server_port}"
        st_l, hdr_l, _ = _http_req(f"{u1}/api/login", method="POST", headers={"Origin": u1}, json_body={"password": "pw-persist"})
        assert st_l == 200
        cookie = hdr_l["set-cookie"].split(";", 1)[0]
        st_p, _, _ = _http_req(
            f"{u1}/api/push",
            method="POST",
            headers={"Origin": u1, "X-Macro-Request": "1", "Cookie": cookie},
            json_body={"include_amounts": "false"},
        )
        assert st_p == 400
        st_out, _, _ = _http_req(f"{u1}/api/logout", method="POST", headers={"Origin": u1, "X-Macro-Request": "1", "Cookie": cookie}, json_body={})
        assert st_out == 200
    finally:
        srv1.shutdown()
        srv1.server_close()

    srv2 = server.create_server(
        "127.0.0.1",
        0,
        web_dir=WEB_DIR,
        state_path=base / "data" / "cockpit_state.json",
        db_path=db_persist,
        access_password="pw-persist",
        role="standalone",
    )
    th2 = threading.Thread(target=srv2.serve_forever, daemon=True)
    th2.start()
    try:
        st_revoked, _, _ = _http_req(f"http://127.0.0.1:{srv2.server_port}/api/state", headers={"Cookie": cookie})
        assert st_revoked == 401
    finally:
        srv2.shutdown()
        srv2.server_close()


def test_cloud_split_collector_and_public_repo_hygiene(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify /api/v2/ingest-intl RBAC, pure-numpy correlations without pandas, and cross-lingual dedup."""
    from datetime import datetime, timedelta, timezone
    from backend import cloud_collector, correlation_engine, kazike_engine

    # 1. Cross-lingual numeric+ticker cluster dedup merges EN & CN same event within 4h
    now_utc = datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc)
    bjt_str = (now_utc + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M")
    en_item = {
        "id": "fed-en",
        "source": "美联储官方",
        "source_tier": "T1",
        "attention_score": 82,
        "time_bjt": bjt_str,
        "title": "Fed cuts policy rate by 25 bps to 4.50% range",
        "summary": "FOMC lowers target range by 25 bps.",
        "tickers": ["US10Y"],
        "url": "https://federalreserve.gov/newsevents/pressreleases/monetary20261123a.htm",
    }
    cn_item = {
        "id": "cls-cn",
        "source": "财联社电报",
        "source_tier": "T1_5",
        "attention_score": 78,
        "time_bjt": bjt_str,
        "title": "美联储宣布降息25个基点，利率区间调整至4.50%",
        "summary": "美债10Y收益率应声回落。",
        "tickers": ["US10Y"],
        "url": "https://www.cls.cn/detail/999001",
    }
    deduped = kazike_engine.cluster_and_dedup([cn_item, en_item], now_utc=now_utc)
    assert len(deduped) == 1
    assert deduped[0]["source_tier"] == "T1"
    assert deduped[0]["merged_count"] == 2
    assert set(deduped[0]["merged_sources"]) == {"美联储官方", "财联社电报"}

    # 2. Pure-NumPy correlation matrix computes cleanly even when pandas is None and parquet is absent
    fc_path = tmp_path / "fred_cache.json"
    days = [(datetime(2026, 7, 1) + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(110)]
    series_synth = {}
    for offset, k in enumerate(("BTC", "SP500", "GOLD", "US10Y", "DOLLAR", "VIX", "WTI")):
        series_synth[k] = [[d, round(100.0 + (i * (1 + offset * 0.1)) + ((i % 5) - 2) * (offset + 1), 4)] for i, d in enumerate(days)]
    fc_path.write_text(json.dumps({"series": series_synth}), encoding="utf-8")
    monkeypatch.setattr(correlation_engine, "pd", None)
    corr = correlation_engine.compute_cross_asset_correlations(fred_cache_path=fc_path, allow_remote=False)
    assert len(corr["assets"]) == 7
    assert len(corr["matrix_30d"]) == 7 and len(corr["matrix_90d"]) == 7
    assert len(corr["pairs"]) == 6

    # 3. POST /api/v2/ingest-intl strictly requires SYNC_PUBLISH and rejects RO / RW with 403
    base = tmp_path / "vps_base"
    (base / "data").mkdir(parents=True)
    (base / "articles").mkdir(parents=True)
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    publish_generation(base, state, [], producer_id="seed")

    srv = server.create_server(
        "127.0.0.1",
        0,
        web_dir=WEB_DIR,
        state_path=base / "data" / "cockpit_state.json",
        db_path=base / "data" / "user_journal.db",
        access_password="pw",
        read_token="tok-ro",
        rw_token="tok-rw",
        sync_token="tok-sync",
        role="consumer",
    )
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        u = f"http://127.0.0.1:{srv.server_port}/api/v2/ingest-intl"
        payload = {
            "producer_id": "test-intl",
            "fetched_epoch": 1795400000.0,
            "fred_series": {"SP500": [["2026-11-22", 6100.0]]},
            "raw_signals": [{"id": "intl-1", "source": "Anthropic 官方", "title": "Claude release", "category": "ai"}],
            "raw_articles": [],
            "skip_merge": True,
        }
        st_anon, _, _ = _http_req(u, method="POST", json_body=payload)
        assert st_anon == 401
        for bad_tok in ("tok-ro", "tok-rw"):
            st_forbid, _, _ = _http_req(u, method="POST", headers={"Authorization": f"Bearer {bad_tok}"}, json_body=payload)
            assert st_forbid == 403
        st_ok, _, body_ok = _http_req(u, method="POST", headers={"Authorization": "Bearer tok-sync"}, json_body=payload)
        assert st_ok == 200
        j_ok = json.loads(body_ok)
        assert j_ok["ok"] is True and j_ok["intl_signals_count"] == 1
        assert (base / "data" / "intl_relay_bundle.json").is_file()
        assert (base / "data" / "fred_cache.json").is_file()
        assert len(cloud_collector.INTL_ONLY_RSS_IDS) == 8 and len(cloud_collector.INTL_ONLY_ARTICLE_IDS) == 1
    finally:
        srv.shutdown()
        srv.server_close()

