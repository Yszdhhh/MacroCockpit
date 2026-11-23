"""Comprehensive verification suite for MacroCockpit docs, backend, APIs, and offline bundle."""
from __future__ import annotations

import ast
import json
import math
import re
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

from backend import mobile_push, server, signal_aggregator  # noqa: E402

DOCS_DIR = COCKPIT_ROOT / "docs"
DATA_DIR = COCKPIT_ROOT / "data"
DIST_DIR = COCKPIT_ROOT / "dist"
WEB_DIR = COCKPIT_ROOT / "web"
STATE_PATH = DATA_DIR / "cockpit_state.json"
OFFLINE_HTML_PATH = DIST_DIR / "macro_cockpit_offline.html"

FORBIDDEN_GROUP_CHAT_ID = "oc_00000000000000000000000000000000"
FORBIDDEN_JARGON = (
    r"\bSleeve\b",
    r"\bE21\b",
    r"\bE32\b",
    r"\bE33\b",
    r"\bTier-1\b",
    r"\bRegime\b",
    r"\barbitration\b",
)
FORBIDDEN_PLACEHOLDERS_AND_BANNERS = (
    "测算口径",
    "公式解释",
    "公式说明",
    "逻辑解释",
    "联动说明",
    "方法论说明",
    "待补",
    "待刷新",
    "N/A",
)


def _assert_state_contract(state: dict[str, Any]) -> None:
    """Validate cockpit_state.json structure, business invariants, and clean UTF-8 labels."""
    expected_top_keys = {
        "generated_at_bjt",
        "risk_gate",
        "positions",
        "macro_fred",
        "us_equities",
        "signals",
        "kol_digest",
        "articles",
        "push_channels",
    }
    assert expected_top_keys.issubset(state.keys())
    assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\+08:00$", state["generated_at_bjt"])

    # Risk gate contract
    rg = state["risk_gate"]
    assert rg["light"] in {"GREEN", "YELLOW", "RED"}
    assert isinstance(rg["label"], str) and rg["label"].strip()
    assert isinstance(rg["long_risk_cap"], (int, float))
    assert isinstance(rg["summary_exposure"], str) and rg["summary_exposure"].strip()
    assert isinstance(rg["reasons"], list) and len(rg["reasons"]) >= 1

    # Positions contract + invariant: baseline_pct == 0 => recommended_pct == 0
    positions = state["positions"]
    assert isinstance(positions, list) and len(positions) == 4
    expected_pos_ids = {"btc_trend", "gold_hedge", "bear_basket", "crash_bounce"}
    assert {p["id"] for p in positions} == expected_pos_ids
    for pos in positions:
        for req_k in ("id", "name", "state", "baseline_pct", "recommended_pct", "est_notional_usd", "note"):
            assert req_k in pos
        base = float(pos["baseline_pct"])
        rec = float(pos["recommended_pct"])
        notional = float(pos["est_notional_usd"])
        assert base >= 0.0 and rec >= 0.0 and notional >= 0.0
        if base == 0.0:
            assert rec == 0.0, f"Invariant violated for {pos['id']}: baseline_pct=0 but recommended_pct={rec}"
            assert notional == 0.0

    # macro_fred: zero null/empty/NaN rows
    macro_fred = state["macro_fred"]
    assert isinstance(macro_fred, list) and len(macro_fred) >= 8
    for item in macro_fred:
        for req_k in ("id", "group", "label", "value_display", "raw_value", "change_display", "change_num", "as_of", "source", "note"):
            assert req_k in item
        assert str(item["label"]).strip() != ""
        val_disp = str(item["value_display"]).strip()
        assert val_disp != "" and val_disp.upper() != "N/A" and "待补" not in val_disp
        assert math.isfinite(float(item["raw_value"]))
        assert math.isfinite(float(item["change_num"]))

    # us_equities: zero <=0 prices
    eq = state["us_equities"]
    assert isinstance(eq["summary"], str) and eq["summary"].strip()
    for bucket in ("indices", "mag7_and_ai", "sectors"):
        rows = eq[bucket]
        assert isinstance(rows, list) and len(rows) >= 1
        for row in rows:
            price = float(row["price"])
            assert math.isfinite(price) and price > 0.0, f"Invalid price in {bucket}: {row}"
            assert math.isfinite(float(row["change_pct"]))

    # signals, kol_digest, articles, push_channels
    assert isinstance(state["signals"], list) and len(state["signals"]) >= 1
    for sig in state["signals"]:
        assert sig["category"] in {"macro", "equities", "ashare", "crypto", "ai"}
        assert sig["direction"] in {"bullish", "bearish", "neutral"}
        assert sig["importance"] in {"高", "中"}
        assert str(sig["title"]).strip() != ""

    assert isinstance(state["kol_digest"], list) and len(state["kol_digest"]) >= 1
    assert isinstance(state["articles"], list)
    for art in state["articles"]:
        assert "异常告警" not in art["filename"]
        assert str(art["body_md"]).strip() != ""

    expected_channels = {
        "feishu_dm",
        "wecom_bot",
        "wechat_push",
        "ntfy",
        "bark",
        "telegram",
        "webhook",
    }
    assert expected_channels == set(state["push_channels"].keys())
    for ch_info in state["push_channels"].values():
        assert isinstance(ch_info["configured"], bool)
        assert isinstance(ch_info["label"], str) and ch_info["label"].strip()

    # Forbidden internal jargon check on serialized state text
    serialized = json.dumps(state, ensure_ascii=False)
    for pat in FORBIDDEN_JARGON:
        assert not re.search(pat, serialized, flags=re.IGNORECASE), f"Forbidden jargon matched {pat} in state"
    assert "待补" not in serialized
    assert not re.search(r"\bN/A\b", serialized)


def test_research_docs_exist_and_valid_utf8() -> None:
    """Verify both Rea research Markdown docs exist and contain rich UTF-8 Chinese content."""
    for fname in ("REA_LAYOUT_AND_CHOREOGRAPHY.md", "REA_SIGNAL_SOURCES_AND_MOBILE_PUSH.md"):
        doc_path = DOCS_DIR / fname
        assert doc_path.is_file(), f"Missing documentation file: {doc_path}"
        content = doc_path.read_text(encoding="utf-8")
        assert len(content.strip()) > 3000, f"Documentation unexpectedly short: {doc_path}"
        assert "## " in content or "# " in content


def test_persisted_cockpit_state_contract() -> None:
    """Validate data/cockpit_state.json matches schema and invariants."""
    assert STATE_PATH.is_file(), "cockpit_state.json missing"
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    _assert_state_contract(state)


def test_live_state_refresh_generation_and_zero_baseline_invariant(tmp_path: Path) -> None:
    """Run live signal_aggregator generation into tmp_path and verify baseline_pct==0 invariant."""
    out_file = tmp_path / "cockpit_state_test.json"
    state = signal_aggregator.generate_cockpit_state(refresh_fred=False, output_path=out_file)
    assert out_file.is_file()
    _assert_state_contract(state)

    # Synthetic edge-case check: even if decision_brief has non-zero recommended_weight, baseline=0 wins
    synth_quant = {
        "sleeves": {
            "S1": {"baseline_weight": 0.0, "reason": "空仓"},
            "S2": {"baseline_weight": 0.0, "reason": "空仓"},
            "S3": {"baseline_weight": 0.35, "reason": "配对运行"},
            "S4": {"baseline_weight": 0.0, "reason": "空仓"},
        }
    }
    synth_brief = {
        "risk_gate": {"light": "GREEN", "label": "绿灯", "long_risk_cap": 1.0, "reasons": ["测试"]},
        "sleeves": {
            "S1": {"baseline_weight": 0.0, "recommended_weight": 0.45, "action": "测试覆盖"},
            "S4": {"baseline_weight": 0.0, "recommended_weight": 0.25, "action": "测试覆盖"},
        },
    }
    _rg, synth_positions = signal_aggregator._build_positions_and_risk_gate(synth_quant, synth_brief)
    by_id = {p["id"]: p for p in synth_positions}
    assert by_id["btc_trend"]["baseline_pct"] == 0.0
    assert by_id["btc_trend"]["recommended_pct"] == 0.0
    assert by_id["crash_bounce"]["baseline_pct"] == 0.0
    assert by_id["crash_bounce"]["recommended_pct"] == 0.0


def test_mobile_push_dry_run_and_group_chat_safety(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify mobile digest payload, dry-run non-sending guarantee, and Feishu group chat block."""
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))

    # Ensure no network calls can happen during dry_run=True
    def _forbid_network(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("Network call attempted during dry_run=True")

    monkeypatch.setattr(mobile_push.requests, "post", _forbid_network)

    payload = mobile_push.build_mobile_digest_payload(state)
    assert payload["target_policy"] == "feishu_personal_dm_only"
    assert isinstance(payload["title"], str) and payload["title"].strip()
    assert isinstance(payload["text"], str) and "【辅助仓位】" in payload["text"]
    assert "elements" in payload["feishu_card"] and "header" in payload["feishu_card"]

    dry_res = mobile_push.dispatch_mobile_push(state, dry_run=True)
    assert dry_res["dry_run"] is True
    assert "payload" in dry_res and "results" in dry_res

    # Explicitly reject group chat ID (oc_...)
    with pytest.raises(ValueError):
        mobile_push.dispatch_mobile_push(
            state,
            dry_run=True,
            feishu_open_id=FORBIDDEN_GROUP_CHAT_ID,
        )

    # Audit mobile_push.py source: never targets the forbidden group chat ID or calls send_card_to_chat
    src = Path(mobile_push.__file__).read_text(encoding="utf-8")
    assert FORBIDDEN_GROUP_CHAT_ID not in src
    tree = ast.parse(src)
    called_attrs = {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    assert "send_card_to_chat" not in called_attrs


def test_ephemeral_http_server_endpoints_and_path_traversal() -> None:
    """Spin up an ephemeral ThreadingHTTPServer and verify /, /api/state, /api/push, /api/article."""
    srv = server.create_server("127.0.0.1", 0, web_dir=WEB_DIR, state_path=STATE_PATH)
    port = srv.server_address[1]
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"

    try:
        # 1. GET /
        with urllib.request.urlopen(f"{base}/", timeout=5) as resp:
            assert resp.status == 200
            html_body = resp.read().decode("utf-8")
            assert "<title>宏观金融决策驾驶舱</title>" in html_body

        # 2. GET /api/state
        with urllib.request.urlopen(f"{base}/api/state", timeout=5) as resp:
            assert resp.status == 200
            state_json = json.loads(resp.read().decode("utf-8"))
            assert len(state_json["positions"]) == 4

        # 3. POST /api/push (dry_run=True)
        req = urllib.request.Request(
            f"{base}/api/push",
            data=json.dumps({"dry_run": True}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 200
            push_res = json.loads(resp.read().decode("utf-8"))
            assert push_res["dry_run"] is True
            assert push_res["payload"]["target_policy"] == "feishu_personal_dm_only"

        # 4. GET /api/article rejects path traversal attempts
        for malicious in ("../etc/passwd", "..\\..\\Windows\\win.ini", "%2e%2e/etc/passwd"):
            url = f"{base}/api/article?file={urllib.parse.quote(malicious, safe='%/\\')}"
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                urllib.request.urlopen(url, timeout=5)
            assert exc_info.value.code in {400, 403, 404}

        # 5. Valid article lookup returns 200 when articles exist
        articles = state_json.get("articles") or []
        if articles:
            fname = urllib.parse.quote(articles[0]["filename"])
            with urllib.request.urlopen(f"{base}/api/article?file={fname}", timeout=5) as resp:
                assert resp.status == 200
                art = json.loads(resp.read().decode("utf-8"))
                assert art["filename"] == articles[0]["filename"]
                assert art["body_md"].strip() != ""
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=3)


def test_offline_html_bundle_invariants(tmp_path: Path) -> None:
    """Verify dist/macro_cockpit_offline.html has inline state, CSS rules, and zero clutter/placeholders."""
    target_offline = OFFLINE_HTML_PATH if OFFLINE_HTML_PATH.is_file() else server.build_static_bundle(
        state_path=STATE_PATH, out_path=tmp_path / "macro_cockpit_offline.html"
    )
    assert target_offline.is_file(), "offline HTML missing"
    bundle = target_offline.read_text(encoding="utf-8")

    # Injected initial state parses cleanly
    m = re.search(r"window\.__INITIAL_COCKPIT_STATE__\s*=\s*(\{.*?\});\s*</script>", bundle, re.DOTALL)
    assert m is not None, "window.__INITIAL_COCKPIT_STATE__ injection missing"
    embedded_state = json.loads(m.group(1).replace("<\\/", "</"))
    _assert_state_contract(embedded_state)

    # Typography, spin-button hiding, and zero external font dependencies
    assert "tabular-nums" in bundle
    assert "inner-spin-button" in bundle
    assert "appearance: textfield" in bundle
    assert "fonts.googleapis.com" not in bundle
    assert "fonts.gstatic.com" not in bundle

    # Strip <script> and <style> to check visible markup & overall bundle for forbidden strings
    visible_html = re.sub(r"<(?:script|style)\b[^>]*>.*?</(?:script|style)>", "", bundle, flags=re.DOTALL | re.IGNORECASE)
    for forbidden in FORBIDDEN_PLACEHOLDERS_AND_BANNERS:
        assert forbidden not in visible_html, f"Forbidden text {forbidden!r} found in offline HTML markup"
        assert forbidden not in bundle, f"Forbidden text {forbidden!r} found in offline bundle"
    for pat in FORBIDDEN_JARGON:
        assert not re.search(pat, visible_html, flags=re.IGNORECASE), f"Forbidden jargon {pat} in offline HTML"
        assert not re.search(pat, bundle), f"Forbidden jargon {pat} in offline bundle"


def test_kazike_engine_scoring_dedup_heat_and_backfill(tmp_path: Path) -> None:
    """Verify AIHOT prefilter, 5-axis scoring, 14d dedup, and 48h half-life heat + backfill."""
    from datetime import datetime, timedelta, timezone
    from backend import kazike_engine

    bjt = timezone(timedelta(hours=8))
    now_utc = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)
    now_bjt_str = now_utc.astimezone(bjt).strftime("%Y-%m-%d %H:%M")
    t_24h_str = (now_utc - timedelta(hours=24)).astimezone(bjt).strftime("%Y-%m-%d %H:%M")
    t_72h_str = (now_utc - timedelta(hours=72)).astimezone(bjt).strftime("%Y-%m-%d %H:%M")

    # 1. Prefilter: listing noise blocked vs Fed/NVDA signal passed
    lbl_block, _ = kazike_engine.prefilter_financial({
        "title": "Binance 新增 XYZ/USDT 交易对并上线 XYZ 永续合约",
        "summary": "交易赛与新币上币公告详情如下",
    })
    assert lbl_block == "BLOCK"
    lbl_pass, _ = kazike_engine.prefilter_financial({
        "title": "美联储理事库克警告核心 PCE 通胀黏性，12月仍存加息可能",
        "summary": "利率路径与流动性定价出现明显收紧，牵动美债与科技估值。",
    })
    assert lbl_pass == "PASS"

    # 2. Blind 5-axis scoring: Fed regulatory item outscores roundup / fragment
    receipts_file = tmp_path / "receipts.json"
    high_item = {
        "id": "fed-t1",
        "source": "美联储官方",
        "source_tier": "T1",
        "title": "美联储 FOMC 会议纪要与核心 PCE 通胀调查显示年内仍可能加息 25bps",
        "summary": "官方纪要公布失业率、PCE 预期及 $1.2B 国债流动性缩表节奏，市场降息预期大幅后移。",
        "time_bjt": now_bjt_str,
        "url": "https://federalreserve.gov/fomc/minutes-2026-11",
    }
    roundup_item = {
        "id": "roundup-1",
        "source": "T2媒体",
        "source_tier": "T2",
        "title": "每周热点汇总：早盘要闻速览与本周回顾",
        "summary": "本周盘点市场早报汇总",
        "time_bjt": now_bjt_str,
    }
    frag_item = {
        "id": "frag-1",
        "source": "T2媒体",
        "source_tier": "T2",
        "title": "短讯",
        "summary": "无数字无依据短句",
        "time_bjt": now_bjt_str,
    }
    s_high = kazike_engine.score_five_axes(high_item, receipts_path=receipts_file, persist_receipt=True)
    s_round = kazike_engine.score_five_axes(roundup_item, receipts_path=receipts_file, persist_receipt=True)
    s_frag = kazike_engine.score_five_axes(frag_item, receipts_path=receipts_file, persist_receipt=True)
    assert s_high["score"] > s_round["score"]
    assert s_frag["score"] <= 35

    # 3. 14d clustering & dedup: identical event from T1 + T2 collapses keeping T1
    dup_t1 = {**high_item, "attention_score": 88, "relation_code": "UNRELATED"}
    dup_t2 = {
        **high_item,
        "id": "media-t2",
        "source": "二线快讯",
        "source_tier": "T2",
        "attention_score": 78,
        "time_bjt": now_bjt_str,
        "url": "https://federalreserve.gov/fomc/minutes-2026-11?utm_source=x",
    }
    deduped = kazike_engine.cluster_and_dedup([dup_t2, dup_t1], now_utc=now_utc)
    assert len(deduped) == 1
    assert deduped[0]["source_tier"] == "T1"
    assert deduped[0]["group_relation"] == "同事件合并"
    assert deduped[0]["merged_count"] == 2

    # 4. 48h half-life heat & 48h backfill guard
    fresh_it = {"id": "h-now", "cluster_id": "c-now", "source": "源A", "source_tier": "T1_5", "time_bjt": now_bjt_str}
    half_it = {"id": "h-24", "cluster_id": "c-24", "source": "源A", "source_tier": "T1_5", "time_bjt": t_24h_str}
    old_it = {"id": "h-72", "cluster_id": "c-72", "source": "源A", "source_tier": "T1_5", "time_bjt": t_72h_str}
    heated = kazike_engine.compute_48h_heat([fresh_it, half_it, old_it], now_utc=now_utc)
    by_id = {x["id"]: x for x in heated}
    assert by_id["h-now"]["heat_48h"] > by_id["h-24"]["heat_48h"] > 0.0
    assert math.isclose(by_id["h-now"]["heat_48h"], by_id["h-24"]["heat_48h"] * 2.0, rel_tol=0.05)
    assert by_id["h-72"]["is_backfill"] is True
    assert by_id["h-72"]["heat_48h"] == 0.0
    assert by_id["h-72"]["heat_badge"] == "归档"


def test_correlation_engine_and_viz_embed(tmp_path: Path) -> None:
    """Verify cross-asset correlation matrices, divergence tags, and Notion embed HTML."""
    from backend import correlation_engine

    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    corr = state.get("correlations") or {}
    assert len(corr.get("assets") or []) == 7
    assert len(corr.get("matrix_30d") or []) == 7
    assert len(corr.get("matrix_90d") or []) == 7
    for row in corr["matrix_30d"] + corr["matrix_90d"]:
        assert len(row) == 7

    pairs = corr.get("pairs") or []
    assert len(pairs) == 6
    for p in pairs:
        assert p["divergence"] in {"正常联动", "显著背离", "极值警戒"}

    viz_path = DIST_DIR / "notion_viz_embed.html"
    if not viz_path.is_file():
        viz_path = correlation_engine.generate_notion_viz_html(
            state, corr, out_path=tmp_path / "notion_viz_embed.html"
        )
    assert viz_path.is_file(), "notion_viz_embed.html missing"
    viz_html = viz_path.read_text(encoding="utf-8")
    assert "BTC" in viz_html and "标普500" in viz_html and "VIX" in viz_html
    for forbidden in FORBIDDEN_PLACEHOLDERS_AND_BANNERS:
        assert forbidden not in viz_html


def test_notion_sync_payloads_and_user_field_protection() -> None:
    """Verify Notion REST builders, native date schemas, and user-owned column preservation."""
    from backend import notion_sync

    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    ids_path = COCKPIT_ROOT / "config" / "notion_ids.json"
    if not ids_path.is_file():
        ids_path = COCKPIT_ROOT / "config" / "notion_ids.example.json"
    ids_cfg = json.loads(ids_path.read_text(encoding="utf-8"))
    report_date = notion_sync.infer_report_date(state)

    # DB1 daily report native date
    db1_props = notion_sync.build_daily_report_properties(state, is_update=True)
    assert db1_props["日期"] == {"date": {"start": report_date}}

    # DB2 signal user field preservation
    sig = next(s for s in state["signals"] if s.get("selected"))
    sig_create = notion_sync.build_signal_properties(sig, report_date=report_date, is_update=False)
    sig_update = notion_sync.build_signal_properties(sig, report_date=report_date, is_update=True)
    assert sig_create["跟踪状态"] == {"status": {"name": "新入库"}}
    assert "跟踪状态" not in sig_update
    assert "date" in sig_update["发布时间"]

    # DB4 KOL stance mapping & protection
    kol = state["kol_digest"][0]
    kol_create = notion_sync.build_kol_properties(kol, fallback_date=report_date, is_update=False)
    kol_update = notion_sync.build_kol_properties(kol, fallback_date=report_date, is_update=True)
    assert kol_create["验证状态"] == {"status": {"name": "待验证"}}
    assert kol_update["多空立场"]["select"]["name"] in {"多头阵营", "空头阵营", "中性观察"}
    assert "验证状态" not in kol_update

    # DB5 longform article status schema & protection
    art = state["articles"][0]
    art_create = notion_sync.build_article_properties(art, fallback_date=report_date, is_update=False)
    art_update = notion_sync.build_article_properties(art, fallback_date=report_date, is_update=True)
    assert art_create["阅读状态"] == {"status": {"name": "待阅读"}}
    assert "阅读状态" not in art_update

    # DB6 exact category mapping ("核心趋势仓(BTC)" without space) & protection
    btc_pos = next(p for p in state["positions"] if p["id"] == "btc_trend")
    pos_create = notion_sync.build_position_properties(btc_pos, state, is_update=False)
    pos_update = notion_sync.build_position_properties(btc_pos, state, is_update=True)
    assert pos_create["仓位类别"]["select"]["name"] == "核心趋势仓(BTC)"
    assert pos_create["复盘归因"] == {"select": {"name": "待复盘"}}
    assert pos_update["日期"] == {"date": {"start": report_date}}
    for protected in ("实际仓位%", "复盘归因"):
        assert protected not in pos_update

    stripped = notion_sync.strip_user_owned_fields(
        {"标题": "ok", "跟踪状态": "x", "验证状态": "y", "阅读状态": "z", "实际仓位%": 10, "复盘归因": "k"}
    )
    assert stripped == {"标题": "ok"}

    # Dry-run sync executes cleanly without touching DB7 or making network writes
    client = notion_sync.NotionSyncClient(token="dummy-token", ids_config=ids_cfg)
    dry_res = client.sync_all(state, dry_run=True)
    assert dry_res["dry_run"] is True
    assert dry_res["planned"]["quick_notes"] == 0
    assert dry_res["failed"] == 0




def test_home_live_sections_builder_and_recursive_sync() -> None:
    from backend import notion_sync

    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    sections = notion_sync.build_home_live_sections_data(state)
    assert set(sections) == set(notion_sync.HOME_LIVE_PREFIXES)
    forbidden = ("测算口径", "公式解释", "逻辑解释", "联动说明", "待补", "待刷新", "N/A")
    serialized = json.dumps(sections, ensure_ascii=False)
    assert not any(term in serialized for term in forbidden)

    def callout(prefix: str) -> dict[str, Any]:
        return {"id": "c-" + str(notion_sync.HOME_LIVE_PREFIXES.index(prefix)), "type": "callout", "has_children": True,
                "callout": {"rich_text": [{"plain_text": prefix + "旧标题"}]}}

    protected = [
        {"id": "db-protected", "type": "child_database", "has_children": True},
        {"id": "embed-protected", "type": "embed", "has_children": False},
        {"id": "toggle-protected", "type": "toggle", "has_children": True},
    ]
    top = [{"id": "columns", "type": "column_list", "has_children": True}, *protected]
    column = [{"id": "column", "type": "column", "has_children": True}]
    nested = [callout(p) for p in notion_sync.HOME_LIVE_PREFIXES]
    requests: list[tuple[str, str, dict[str, Any] | None]] = []
    client = notion_sync.NotionSyncClient("dummy", {"home_page_id": "home"}, min_interval=0)

    def fake_request(method: str, path: str, payload: dict[str, Any] | None = None, **_kwargs: Any) -> tuple[int, dict[str, Any]]:
        requests.append((method, path, payload))
        if method == "GET" and path == "/blocks/home/children?page_size=100":
            return 200, {"results": top}
        if method == "GET" and path == "/blocks/columns/children?page_size=100":
            return 200, {"results": column}
        if method == "GET" and path == "/blocks/column/children?page_size=100":
            return 200, {"results": nested}
        # Give c-0 two old bullets + one non-bullet child, and give c-2 (7 bullets) 9 old bullets so archive runs
        if method == "GET" and path == "/blocks/c-0/children?page_size=100":
            return 200, {"results": [
                {"id": "b-0-1", "type": "bulleted_list_item"},
                {"id": "b-0-2", "type": "bulleted_list_item"},
                {"id": "para-keep", "type": "paragraph"},
            ]}
        if method == "GET" and path == "/blocks/c-2/children?page_size=100":
            return 200, {"results": [{"id": f"b-2-{i}", "type": "bulleted_list_item"} for i in range(9)]}
        if method == "GET" and path == "/blocks/toggle-protected/children?page_size=100":
            raise AssertionError("toggle children must never be traversed")
        if method == "GET" and path.endswith("/children?page_size=100"):
            return 200, {"results": []}
        return 200, {"id": "created"}

    client._request = fake_request  # type: ignore[method-assign]
    result = client.sync_home_live_sections(state)
    assert len(result["matched_sections"]) == 7
    assert result["updated_count"] == 7
    patched = [path for method, path, _ in requests if method == "PATCH"]
    assert all(not any(block_id in path for block_id in ("db-protected", "embed-protected", "toggle-protected", "para-keep")) for path in patched)
    assert "/blocks/b-0-1" in patched and "/blocks/c-0/children" in patched
    archived_paths = [path for method, path, payload in requests if method == "PATCH" and payload and payload.get("archived")]
    assert archived_paths == ["/blocks/b-2-7", "/blocks/b-2-8"]
    assert sum(1 for method, path, payload in requests if method == "PATCH" and path.startswith("/blocks/c-") and payload and "callout" in payload) == 7

    # Partial query failure must raise RuntimeError so callers never duplicate rows
    def failing_query(method: str, path: str, payload: dict[str, Any] | None = None, **_kwargs: Any) -> tuple[int, dict[str, Any]]:
        if payload and not payload.get("start_cursor"):
            return 200, {"results": [], "has_more": True, "next_cursor": "p2"}
        return 503, {"message": "upstream unavailable"}

    client._request = failing_query  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        client.query_sync_index("db-x", "同步键")


def test_upgraded_notion_viz_embed_content(tmp_path: Path) -> None:
    from backend import correlation_engine, notion_sync

    viz_path = DIST_DIR / "notion_viz_embed.html"
    if not viz_path.is_file():
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        viz_path = correlation_engine.generate_notion_viz_html(
            state, state.get("correlations") or {}, out_path=tmp_path / "notion_viz_embed.html"
        )
    assert viz_path.is_file()
    html = viz_path.read_text(encoding="utf-8")
    assert viz_path.stat().st_size < 75_000
    assert "<html" in html.lower() and "</html>" in html.lower()
    for forbidden in FORBIDDEN_PLACEHOLDERS_AND_BANNERS:
        assert forbidden not in html
    for pat in FORBIDDEN_JARGON:
        assert not re.search(pat, html, flags=re.IGNORECASE)

    # Hostile XSS / scheme inputs are safely escaped & blocked in generator and Notion builders
    assert notion_sync._url_prop("javascript:alert(1)") == {"url": None}
    assert notion_sync._url_prop("http.evil/x") == {"url": None}
    assert notion_sync._number_prop("—") == {"number": None}
    hostile_state = {
        "generated_at_bjt": "2026-11-22 02:20:00+08:00",
        "us_equities": {"indices": [], "mag7_and_ai": []},
        "macro_fred": [],
        "kol_digest": [{
            "handle": "xss\"><script>",
            "topic": "测试",
            "stance": "偏多",
            "score": 0.5,
            "date": "2026-11-22",
            "headline": "<!-- </script><img src=x onerror=alert(1)>",
            "takeaway": "safe <!-- test",
            "url": "javascript:alert(1)",
        }],
    }
    hostile_out = tmp_path / "hostile_embed.html"
    correlation_engine.generate_notion_viz_html(hostile_state, {"pairs": [], "labels": [], "matrix_30d": [], "matrix_90d": []}, out_path=hostile_out)
    hostile_html = hostile_out.read_text(encoding="utf-8")
    assert "<!-- </script>" not in hostile_html
    assert "\\u003c" in hostile_html
    assert "/^https?:\\/\\//i.test" in hostile_html


def test_journal_store_crud_persistence_and_one_time_seed(tmp_path: Path) -> None:
    from backend.journal_store import JournalStore

    db_path = tmp_path / "journal.db"
    sample_state = {
        "generated_at_bjt": "2026-11-22 14:00:00+08:00",
        "risk_gate": {"light": "YELLOW", "label": "黄灯控制风险", "reasons": ["测试依据"]},
        "macro_fred": [{"id": "btc_spot", "raw_value": 59000.0}],
        "positions": [{"id": "btc_trend", "name": "核心趋势仓", "recommended_pct": 0.0}],
    }

    store1 = JournalStore(db_path)
    # P1 fix: get_snapshot() must never auto-seed as a GET side effect
    empty_snap = store1.get_snapshot(sample_state)
    assert empty_snap == {"trade_reviews": [], "essays": [], "article_status": {}}

    assert store1.seed_if_empty(sample_state) is True
    assert store1.seed_if_empty(sample_state) is False
    snap1 = store1.get_snapshot(sample_state)
    assert len(snap1["trade_reviews"]) == 1
    assert len(snap1["essays"]) == 1

    # Delete seeded rows and verify persistence across new JournalStore instance (no resurrection)
    assert store1.delete_trade_review(snap1["trade_reviews"][0]["id"]) is True
    assert store1.delete_essay(snap1["essays"][0]["id"]) is True

    store2 = JournalStore(db_path)
    snap2 = store2.get_snapshot(sample_state)
    assert snap2["trade_reviews"] == []
    assert snap2["essays"] == []

    # Upsert review & essay & article note
    rev = store2.upsert_trade_review({
        "trade_date": "2026-11-22",
        "symbol": "QQQ",
        "direction": "做多",
        "size_pct": 12.5,
        "price_usd": 505.0,
        "pnl_usd": 320.0,
        "status": "持有中",
        "thesis": "右侧突破确认",
        "tags": ["AI", "美股"],
    })
    assert rev["id"] > 0 and rev["symbol"] == "QQQ"
    es = store2.upsert_essay({
        "title": "长端利率观察",
        "mood": "冷静",
        "tags": ["美债"],
        "body_md": "关注 **10Y** 与流动性共振。",
        "pinned": True,
    })
    assert es["id"] > 0 and es["pinned"] == 1

    st1 = store2.set_article_status("sample.md", "starred", "重点笔记")
    assert st1["status"] == "starred" and st1["note"] == "重点笔记"
    # Updating status with note=None preserves existing private note
    st2 = store2.set_article_status("sample.md", "read", None)
    assert st2["status"] == "read" and st2["note"] == "重点笔记"
    with pytest.raises(ValueError):
        store2.set_article_status("sample.md", "invalid_status")

    store3 = JournalStore(db_path)
    snap3 = store3.get_snapshot(sample_state)
    assert snap3["trade_reviews"][0]["symbol"] == "QQQ"
    assert snap3["essays"][0]["title"] == "长端利率观察"
    assert snap3["article_status"]["sample.md"]["note"] == "重点笔记"


def test_server_auth_gate_muse_digest_rss_and_sync_isolation(tmp_path: Path) -> None:
    import http.cookiejar
    import xml.etree.ElementTree as ET

    state_file = tmp_path / "data" / "cockpit_state.json"
    state_file.parent.mkdir(parents=True)
    initial_state = {
        "generated_at_bjt": "2026-11-22 14:15:00+08:00",
        "risk_gate": {"light": "YELLOW", "label": "黄灯防守", "long_risk_cap": 0.5, "summary_exposure": "低仓位", "reasons": []},
        "positions": [{"id": "btc_trend", "name": "核心趋势仓", "state": "空仓观望", "baseline_pct": 0.0, "recommended_pct": 0.0, "est_notional_usd": 0.0}],
        "macro_fred": [{"id": "fedfunds", "label": "联邦基金利率", "value_display": "3.88%", "as_of": "2026-11-19"}],
        "signals": [{"category": "ai", "title": "AI 算力动态", "summary": "算力需求观察", "selected": True, "importance": "高", "url": "https://example.com/ai"}],
        "kol_digest": [{"handle": "testkol", "stance": "偏多", "headline": "流动性改善"}],
        "articles": [{"filename": "sync_doc.md", "title": "同步文章", "source": "同步深度长文", "mtime": "2026-11-22 14:15", "excerpt": "摘要"}],
        "correlations": {"as_of": "2026-11-22", "assets": [], "labels": [], "matrix_30d": [], "matrix_90d": [], "pairs": []},
    }
    state_file.write_text(json.dumps(initial_state, ensure_ascii=False), encoding="utf-8")

    srv = server.create_server(
        "127.0.0.1",
        0,
        web_dir=WEB_DIR,
        state_path=state_file,
        db_path=tmp_path / "data" / "user_journal.db",
        articles_dir=tmp_path / "articles",
        access_password="pw-test-123",
        read_token="tok-test-456",
        rw_token="tok-rw-789",
        sync_token="tok-sync-999",
    )
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    base = f"http://127.0.0.1:{srv.server_port}"

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
            return None

    try:
        # Health is public
        with urllib.request.urlopen(f"{base}/api/health", timeout=5) as resp:
            h = json.loads(resp.read().decode("utf-8"))
            assert h["ok"] is True and h["auth_enabled"] is True

        # Unauthenticated root redirects to /login; API returns 401
        no_redir = urllib.request.build_opener(_NoRedirect())
        with pytest.raises(urllib.error.HTTPError) as exc_root:
            no_redir.open(f"{base}/", timeout=5)
        assert exc_root.value.code == 302 and exc_root.value.headers.get("Location") == "/login"

        with pytest.raises(urllib.error.HTTPError) as exc_api:
            urllib.request.urlopen(f"{base}/api/state", timeout=5)
        assert exc_api.value.code == 401

        with pytest.raises(urllib.error.HTTPError) as exc_md:
            urllib.request.urlopen(f"{base}/api/digest.md?topic=ai", timeout=5)
        assert exc_md.value.code == 401

        # Token-authenticated Muse Markdown & RSS XML endpoints
        with urllib.request.urlopen(f"{base}/api/digest.md?topic=ai&token=tok-test-456", timeout=5) as resp:
            md_txt = resp.read().decode("utf-8")
            assert resp.status == 200 and "# MacroCockpit 市场简报" in md_txt and "AI 算力动态" in md_txt

        with urllib.request.urlopen(f"{base}/feed.xml?token=tok-test-456", timeout=5) as resp:
            rss_root = ET.fromstring(resp.read().decode("utf-8"))
            assert resp.status == 200 and rss_root.tag == "rss"

        # Read-only Bearer token can inspect /api/state (without private journal) & /api/muse-spec.md, but never write journal
        ro_state_req = urllib.request.Request(f"{base}/api/state", headers={"Authorization": "Bearer tok-test-456"})
        with urllib.request.urlopen(ro_state_req, timeout=5) as resp:
            ro_st = json.loads(resp.read().decode("utf-8"))
            assert resp.status == 200 and len(ro_st.get("signals", [])) == 1 and "journal" not in ro_st

        with urllib.request.urlopen(f"{base}/api/muse-spec.md?token=tok-test-456", timeout=5) as resp:
            spec_md = resp.read().decode("utf-8")
            assert resp.status == 200 and "卡兹克" in spec_md and "/api/state" in spec_md

        ro_write_req = urllib.request.Request(
            f"{base}/api/journal/review",
            data=json.dumps({"symbol": "ETH", "direction": "做多", "status": "持有中"}).encode("utf-8"),
            headers={"Authorization": "Bearer tok-test-456", "Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc_ro_write:
            urllib.request.urlopen(ro_write_req, timeout=5)
        assert exc_ro_write.value.code == 403

        # Cookie login flow
        jar = http.cookiejar.CookieJar()
        authed = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        bad_req = urllib.request.Request(
            f"{base}/api/login",
            data=json.dumps({"password": "wrong"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc_bad:
            authed.open(bad_req, timeout=5)
        assert exc_bad.value.code == 401

        ok_req = urllib.request.Request(
            f"{base}/api/login",
            data=json.dumps({"password": "pw-test-123"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with authed.open(ok_req, timeout=5) as resp:
            assert resp.status == 200 and json.loads(resp.read().decode("utf-8"))["ok"] is True

        saved_cookie = "; ".join(f"{c.name}={c.value}" for c in jar)
        assert saved_cookie.startswith("macro_auth=")

        # Cookie write without X-Macro-Request: 1 must be rejected with 403 CSRF
        csrf_req = urllib.request.Request(
            f"{base}/api/journal/review",
            data=json.dumps({"symbol": "ETH", "direction": "做多", "status": "持有中", "size_pct": 5.0}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc_csrf:
            authed.open(csrf_req, timeout=5)
        assert exc_csrf.value.code == 403

        # Create private review with X-Macro-Request: 1, then /api/sync must preserve user_journal.db
        rv_req = urllib.request.Request(
            f"{base}/api/journal/review",
            data=json.dumps({"symbol": "ETH", "direction": "做多", "status": "持有中", "size_pct": 5.0}).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-Macro-Request": "1"},
            method="POST",
        )
        with authed.open(rv_req, timeout=5) as resp:
            assert json.loads(resp.read().decode("utf-8"))["symbol"] == "ETH"

        # RO & RW tokens MUST be forbidden (403) from /api/sync and /api/v2/publish
        for non_sync_tok in ("tok-test-456", "tok-rw-789"):
            for pub_ep in ("/api/sync", "/api/v2/publish"):
                forbid_sync = urllib.request.Request(
                    f"{base}{pub_ep}",
                    data=json.dumps({"state": initial_state, "articles": []}).encode("utf-8"),
                    headers={"Authorization": f"Bearer {non_sync_tok}", "Content-Type": "application/json"},
                    method="POST",
                )
                with pytest.raises(urllib.error.HTTPError) as exc_non_sync:
                    urllib.request.urlopen(forbid_sync, timeout=5)
                assert exc_non_sync.value.code == 403

        bad_sync = urllib.request.Request(
            f"{base}/api/sync",
            data=json.dumps({"state": initial_state, "articles": [{"filename": "../evil.md", "content": "x"}]}).encode("utf-8"),
            headers={"Authorization": "Bearer tok-sync-999", "Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc_sync:
            urllib.request.urlopen(bad_sync, timeout=5)
        assert exc_sync.value.code == 403

        good_sync = urllib.request.Request(
            f"{base}/api/sync",
            data=json.dumps({
                "state": {**initial_state, "generated_at_bjt": "2026-11-22 14:25:00+08:00"},
                "articles": [{"filename": "sync_doc.md", "content": "# 同步长文标题\n\n正文验证"}],
            }).encode("utf-8"),
            headers={"Authorization": "Bearer tok-sync-999", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(good_sync, timeout=5) as resp:
            assert json.loads(resp.read().decode("utf-8"))["articles_count"] == 1

        with authed.open(f"{base}/api/state", timeout=5) as resp:
            st = json.loads(resp.read().decode("utf-8"))
            assert st["generated_at_bjt"] == "2026-11-22 14:25:00+08:00"
            assert any(r["symbol"] == "ETH" for r in st["journal"]["trade_reviews"])

        with authed.open(f"{base}/api/article?file=sync_doc.md", timeout=5) as resp:
            art = json.loads(resp.read().decode("utf-8"))
            assert "同步长文标题" in art["body_md"]

        with urllib.request.urlopen(f"{base}/api/article?file=sync_doc.md&token=tok-test-456", timeout=5) as resp:
            art_tok = json.loads(resp.read().decode("utf-8"))
            assert "同步长文标题" in art_tok["body_md"]

        with pytest.raises(urllib.error.HTTPError) as exc_art_trav:
            urllib.request.urlopen(f"{base}/api/article?file=../evil.md&token=tok-test-456", timeout=5)
        assert exc_art_trav.value.code == 403

        # Logout revokes cookie server-side (replaying old cookie fails with 401)
        logout_req = urllib.request.Request(
            f"{base}/api/logout",
            data=b"{}",
            headers={"Content-Type": "application/json", "X-Macro-Request": "1"},
            method="POST",
        )
        with authed.open(logout_req, timeout=5) as resp:
            assert resp.status == 200
        with pytest.raises(urllib.error.HTTPError) as exc_after_logout:
            authed.open(f"{base}/api/state", timeout=5)
        assert exc_after_logout.value.code == 401

        replay_req = urllib.request.Request(f"{base}/api/state", headers={"Cookie": saved_cookie})
        with pytest.raises(urllib.error.HTTPError) as exc_replay:
            urllib.request.urlopen(replay_req, timeout=5)
        assert exc_replay.value.code == 401
    finally:
        srv.shutdown()
        srv.server_close()
        th.join(timeout=5)


def test_vps_sync_dry_run_and_safe_article_filter() -> None:
    import subprocess
    from backend import vps_sync

    assert vps_sync._safe_article_name("valid_2026.md") == "valid_2026.md"
    for bad in ("../evil.md", "sub/a.md", "a\\b.md", "note.txt", "异常告警_1.md", ""):
        assert vps_sync._safe_article_name(bad) is None

    proc = subprocess.run(
        [sys.executable, "-m", "backend.vps_sync", "--dry-run"],
        cwd=str(COCKPIT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=30,
    )
    out = json.loads(proc.stdout.strip())
    assert out["action"] == "dry_run"
    assert out["state_bytes"] > 1000
    assert "MACROCOCKPIT_READ_TOKEN" not in proc.stdout and "MACROCOCKPIT_ACCESS_PASSWORD" not in proc.stdout


def test_six_module_web_ui_and_offline_bundle_invariants() -> None:
    html_src = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    css_src = (WEB_DIR / "styles.css").read_text(encoding="utf-8")
    js_src = (WEB_DIR / "app.js").read_text(encoding="utf-8")

    assert "<title>宏观金融决策驾驶舱</title>" in html_src
    for required_id in (
        "view-today", "view-portfolio", "view-research-hub", "view-research", "view-records",
        "newsModeSwitch", "sectorRibbon", "subtopicChips", "positionDrawer", "importDrawer",
        "articleDrawer", "pushDrawer", "mobileTabbar", "museDigestUrl", "museFeedUrl",
    ):
        assert f'id="{required_id}"' in html_src
    assert 'data-sector="ashare"' in html_src

    combined = "\n".join((html_src, css_src, js_src))
    for pat in FORBIDDEN_JARGON:
        assert not re.search(pat, combined)
    for term in FORBIDDEN_PLACEHOLDERS_AND_BANNERS:
        assert term not in combined


def test_offline_bundle_strips_private_journal_and_candidates(tmp_path: Path) -> None:
    """P0 isolation: build_static_bundle must never leak user_journal.db data or signal_candidates."""
    from backend.journal_store import JournalStore

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    secret_marker = "PRIVATE_MARKER_ZZZ_9911"

    jstore = JournalStore(data_dir / "user_journal.db")
    jstore.upsert_trade_review({"symbol": "BTC", "direction": "做空", "status": "持有中", "thesis": secret_marker})
    jstore.upsert_essay({"title": secret_marker, "body_md": f"私密随笔 {secret_marker}"})
    jstore.set_article_status("secret.md", "starred", secret_marker)

    base_state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    base_state["journal"] = {"leaked": secret_marker}
    base_state["signal_candidates"] = [{"id": "cand-only", "title": secret_marker}]
    st_path = data_dir / "cockpit_state.json"
    st_path.write_text(json.dumps(base_state, ensure_ascii=False), encoding="utf-8")

    out_html = tmp_path / "offline_test.html"
    server.build_static_bundle(web_dir=WEB_DIR, state_path=st_path, out_path=out_html)
    bundle_txt = out_html.read_text(encoding="utf-8")

    assert secret_marker not in bundle_txt
    m = re.search(r"window\.__INITIAL_COCKPIT_STATE__\s*=\s*(\{.*?\});\s*</script>", bundle_txt, re.DOTALL)
    assert m is not None
    embedded = json.loads(m.group(1).replace("<\\/", "</"))
    assert "journal" not in embedded
    assert "signal_candidates" not in embedded
    assert "portfolio" not in embedded


def test_consumer_mode_startup_guard_and_health_zero_aggregation(tmp_path: Path) -> None:
    """Verify --role consumer refuses missing secrets and never invokes signal_aggregator."""
    import subprocess
    from backend.public_snapshot import publish_generation

    # Missing password or sync token exits 2
    import os
    bad_env = {k: v for k, v in os.environ.items() if not k.startswith("MACROCOCKPIT_")}
    bad_env["MACROCOCKPIT_ROLE"] = "consumer"
    bad_env["MACROCOCKPIT_ROOT"] = str(tmp_path / "no_env_root")
    proc_no_auth = subprocess.run(
        [sys.executable, "-m", "backend.server", "--role", "consumer", "--data-dir", str(tmp_path / "d1")],
        cwd=str(COCKPIT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=bad_env,
        timeout=15,
    )
    assert proc_no_auth.returncode == 2

    # Empty consumer server returns 503 on /api/health and /api/state without aggregating
    data_dir = tmp_path / "c2" / "data"
    data_dir.mkdir(parents=True)
    srv = server.create_server(
        "127.0.0.1",
        0,
        web_dir=WEB_DIR,
        state_path=data_dir / "cockpit_state.json",
        db_path=data_dir / "user_journal.db",
        access_password="pw-c",
        read_token="ro-c",
        sync_token="sync-c",
        role="consumer",
    )
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    base = f"http://127.0.0.1:{srv.server_port}"
    try:
        with pytest.raises(urllib.error.HTTPError) as exc_h:
            urllib.request.urlopen(f"{base}/api/health", timeout=5)
        assert exc_h.value.code == 503
        with pytest.raises(urllib.error.HTTPError) as exc_s:
            urllib.request.urlopen(f"{base}/api/state?token=ro-c", timeout=5)
        assert exc_s.value.code == 503

        # After publishing generation, /api/health returns 200
        src_state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        publish_generation(tmp_path / "c2", src_state, [], generation=1)
        with urllib.request.urlopen(f"{base}/api/health", timeout=5) as resp:
            assert resp.status == 200 and json.loads(resp.read().decode("utf-8"))["generation"] == 1
    finally:
        srv.shutdown()
        srv.server_close()
        th.join(timeout=5)


def test_hardening_non_ascii_rate_limit_rss_and_finite_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import http.cookiejar
    import xml.etree.ElementTree as ET
    from backend.journal_store import JournalStore

    store = JournalStore(tmp_path / "finite.db")
    with pytest.raises(ValueError):
        store.upsert_trade_review({"symbol": "BTC", "direction": "做多", "status": "持有中", "size_pct": float("nan")})
    with pytest.raises(ValueError):
        store.upsert_trade_review({"symbol": "BTC", "direction": "做多", "status": "持有中", "price_usd": float("inf")})
    with pytest.raises(ValueError):
        store.set_article_status("a.md", "read", ["non-scalar"])  # type: ignore[arg-type]

    state_file = tmp_path / "data" / "cockpit_state.json"
    state_file.parent.mkdir(parents=True)
    state_file.write_text(json.dumps({
        "generated_at_bjt": "2026-11-22 14:30:00+08:00",
        "risk_gate": {"light": "YELLOW", "label": "控制\x0b风险"},
        "positions": [],
        "macro_fred": [],
        "signals": [
            {"category": "macro", "title": "测试\x0c信号1", "summary": "摘要1", "selected": True},
            {"category": "macro", "title": "测试信号2", "summary": "摘要2", "selected": True},
        ],
        "articles": [],
    }, ensure_ascii=False), encoding="utf-8")

    srv = server.create_server(
        "127.0.0.1",
        0,
        web_dir=WEB_DIR,
        state_path=state_file,
        db_path=tmp_path / "data" / "user_journal.db",
        articles_dir=tmp_path / "articles",
        access_password="中文密码🔑",
        read_token="令牌-非ASCII",
    )
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    base = f"http://127.0.0.1:{srv.server_port}"
    try:
        # Non-ASCII Bearer header (>127 latin-1) rejects 401 cleanly; UTF-8 URL ?token= succeeds
        bad_tok_req = urllib.request.Request(f"{base}/feed.xml", headers={"Authorization": "Bearer Café-bad"})
        with pytest.raises(urllib.error.HTTPError) as exc_tok:
            urllib.request.urlopen(bad_tok_req, timeout=5)
        assert exc_tok.value.code == 401

        quoted_tok = urllib.parse.quote("令牌-非ASCII")
        with urllib.request.urlopen(f"{base}/feed.xml?token={quoted_tok}", timeout=5) as resp:
            rss_root = ET.fromstring(resp.read().decode("utf-8"))
            guids = [el.text for el in rss_root.findall("./channel/item/guid")]
            assert len(guids) == 3 and len(set(guids)) == 3
            assert resp.headers.get("X-Frame-Options") == "DENY"

        # Non-ASCII password login & per-IP rate limiting (429 after 10 failures)
        jar = http.cookiejar.CookieJar()
        op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        for _ in range(10):
            bad = urllib.request.Request(
                f"{base}/api/login",
                data=json.dumps({"password": "错误密码"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with pytest.raises(urllib.error.HTTPError) as exc:
                op.open(bad, timeout=5)
            assert exc.value.code == 401

        rate_req = urllib.request.Request(
            f"{base}/api/login",
            data=json.dumps({"password": "中文密码🔑"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc_429:
            op.open(rate_req, timeout=5)
        assert exc_429.value.code == 429

        # Without MACROCOCKPIT_TRUST_PROXY=1, spoofed CF-Connecting-IP on loopback is ignored (stays 429)
        spoof_req = urllib.request.Request(
            f"{base}/api/login",
            data=json.dumps({"password": "中文密码🔑"}).encode("utf-8"),
            headers={"Content-Type": "application/json", "CF-Connecting-IP": "198.51.100.2"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc_spoof:
            op.open(spoof_req, timeout=5)
        assert exc_spoof.value.code == 429

        # With explicit MACROCOCKPIT_TRUST_PROXY=1, different forwarded IP succeeds cleanly with non-ASCII UTF-8 password
        monkeypatch.setenv("MACROCOCKPIT_TRUST_PROXY", "1")
        ok_req = urllib.request.Request(
            f"{base}/api/login",
            data=json.dumps({"password": "中文密码🔑"}).encode("utf-8"),
            headers={"Content-Type": "application/json", "CF-Connecting-IP": "198.51.100.2"},
            method="POST",
        )
        with op.open(ok_req, timeout=5) as resp:
            assert resp.status == 200 and json.loads(resp.read().decode("utf-8"))["ok"] is True
    finally:
        srv.shutdown()
        srv.server_close()
        th.join(timeout=5)


def test_stock_research_profiles_and_api_endpoint(tmp_path: Path) -> None:
    """Verify 20+ stock research profiles in state and /api/stock auth + validation."""
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    profiles = state.get("stock_profiles") or {}
    assert isinstance(profiles, dict) and len(profiles) >= 20
    assert {"AAPL", "NVDA"}.issubset(profiles.keys())

    for sym, prof in profiles.items():
        health = prof.get("health") or {}
        assert 1 <= int(health.get("score") or 0) <= 10, sym
        div_pct = (prof.get("overview") or {}).get("dividend_yield_pct")
        if div_pct is not None:
            assert 0 <= float(div_pct) < 25.0, (sym, div_pct)
        closes = (prof.get("chart") or {}).get("closes") or []
        assert len(closes) >= 100, sym
        for q in (prof.get("financials") or {}).get("quarters") or []:
            if q.get("revenue_fmt") not in (None, "", "—"):
                assert q.get("gross_profit_fmt") != "$0.00", (sym, q)

    srv = server.create_server(
        "127.0.0.1",
        0,
        web_dir=WEB_DIR,
        state_path=STATE_PATH,
        db_path=tmp_path / "j.db",
        access_password="pw",
        read_token="ro-tok",
    )
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    base = f"http://127.0.0.1:{srv.server_port}"
    try:
        with pytest.raises(urllib.error.HTTPError) as exc_401:
            urllib.request.urlopen(f"{base}/api/stock?symbol=AAPL", timeout=5)
        assert exc_401.value.code == 401

        with urllib.request.urlopen(f"{base}/api/stock?symbol=AAPL&token=ro-tok", timeout=5) as resp:
            assert resp.status == 200
            payload = json.loads(resp.read().decode("utf-8"))
            assert payload["symbol"] == "AAPL" and float(payload["price"]) > 0

        bad_q = urllib.parse.quote("../etc")
        with pytest.raises(urllib.error.HTTPError) as exc_400:
            urllib.request.urlopen(f"{base}/api/stock?symbol={bad_q}&token=ro-tok", timeout=5)
        assert exc_400.value.code == 400
    finally:
        srv.shutdown()
        srv.server_close()
        th.join(timeout=5)


def test_curated_rss_parser_and_research_ui_bundle(tmp_path: Path) -> None:
    """Verify RSS 2.0 / Atom parsing, jargon scrubbing, and Stock Research UI bundle."""
    from datetime import datetime, timezone
    from backend import rss_sources

    now_utc = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
    rss_xml = """<?xml version="1.0" encoding="utf-8"?>
    <rss version="2.0"><channel><title>Feed</title><item>
      <title>NVDA beats Regime N/A expectations after Tier-1 surge</title>
      <description>NVDA GPU demand continues; OpenAI cluster expands.</description>
      <link>https://example.com/nvda-q4</link>
      <pubDate>Sun, 22 Nov 2026 10:00:00 GMT</pubDate>
    </item></channel></rss>"""
    atom_xml = """<?xml version="1.0" encoding="utf-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom"><entry>
      <title>OpenAI Tier-1 model update</title>
      <summary>Microsoft and OpenAI share Regime details N/A.</summary>
      <link rel="alternate" href="https://example.com/openai-update"/>
      <updated>2026-11-22T11:00:00Z</updated>
    </entry></feed>"""

    spec = {"id": "t", "source": "测试源", "category": "ai", "tier": "T1", "why": "核心行业追踪"}
    rss_items = rss_sources.parse_feed_xml(rss_xml, spec, now_utc=now_utc)
    atom_items = rss_sources.parse_feed_xml(atom_xml, spec, now_utc=now_utc)
    assert len(rss_items) == 1 and len(atom_items) == 1

    all_text = json.dumps(rss_items + atom_items, ensure_ascii=False)
    for forbidden in ("Tier-1", "Regime", "N/A"):
        assert forbidden not in all_text
    assert "NVDA" in rss_items[0]["tickers"]
    assert "MSFT" in atom_items[0]["tickers"]

    index_html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    assert 'id="view-research"' in index_html and "DM+Sans" in index_html

    offline_path = OFFLINE_HTML_PATH if OFFLINE_HTML_PATH.is_file() else server.build_static_bundle(
        state_path=STATE_PATH, out_path=tmp_path / "macro_cockpit_offline.html"
    )
    offline_html = offline_path.read_text(encoding="utf-8")
    assert 'id="view-research"' in offline_html
    assert "fonts.googleapis.com" not in offline_html
    assert "fonts.gstatic.com" not in offline_html


def test_sec_edgar_filings_in_stock_profiles_and_signals() -> None:
    """Verify stock_profiles[*].sec_filings and SEC EDGAR parser/T1 signal builder."""
    from datetime import datetime, timezone
    from backend import sec_edgar

    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    profiles = state.get("stock_profiles") or {}
    for sym in ("AAPL", "NVDA"):
        filings = (profiles.get(sym) or {}).get("sec_filings") or []
        assert isinstance(filings, list) and len(filings) >= 1, f"Missing sec_filings for {sym}"
        for f in filings:
            assert f["symbol"] == sym
            assert f["form"] in sec_edgar.ALLOWED_FORMS
            assert str(f["label_cn"]).strip() != ""
            assert re.match(r"^\d{4}-\d{2}-\d{2}$", f["filing_date"])
            assert str(f["url"]).startswith("https://www.sec.gov/")

    # Also validate _parse_submissions_payload & collect_recent_sec_signals deterministically
    sample_sub = {
        "filings": {
            "recent": {
                "form": ["10-K", "4", "8-K"],
                "filingDate": ["2026-11-20", "2026-11-19", "2026-11-01"],
                "accessionNumber": ["0000320193-26-000101", "0000320193-26-000100", "0000320193-26-000090"],
                "primaryDocument": ["aapl-2026.htm", "form4.xml", "aapl-8k.htm"],
            }
        }
    }
    parsed = sec_edgar._parse_submissions_payload("AAPL", "0000320193", sample_sub)
    assert [x["form"] for x in parsed] == ["10-K", "8-K"]
    assert parsed[0]["url"] == "https://www.sec.gov/Archives/edgar/data/320193/000032019326000101/aapl-2026.htm"

    now_utc = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
    sec_sigs = sec_edgar.collect_recent_sec_signals({"AAPL": parsed}, max_age_days=10, now_utc=now_utc)
    assert len(sec_sigs) == 1
    assert sec_sigs[0]["source"] == "SEC EDGAR"
    assert sec_sigs[0]["source_tier"] == "T1"
    assert sec_sigs[0]["tickers"] == ["AAPL"]


def test_kazike_engine_new_source_tiers_and_rate_limit(tmp_path: Path) -> None:
    """Verify T1/T1_5/T2 mapping for SEC EDGAR/arXiv/wires and max_per_source rate limiting."""
    from datetime import datetime, timezone
    from backend import kazike_engine

    assert kazike_engine.infer_source_tier({"source": "SEC EDGAR", "title": "8-K filing"}) == "T1"
    assert kazike_engine.infer_source_tier({"source": "arXiv AI前沿", "title": "LLM inference"}) == "T1"
    assert kazike_engine.infer_source_tier({"source": "arXiv 量化金融", "title": "Factor timing"}) == "T1"
    assert kazike_engine.infer_source_tier({"source": "Anthropic 官方", "title": "Claude update"}) == "T1"
    assert kazike_engine.infer_source_tier({"source": "财联社电报", "title": "盘面动态"}) == "T1_5"
    assert kazike_engine.infer_source_tier({"source": "华尔街见闻", "title": "盘中快讯"}) == "T1_5"
    assert kazike_engine.infer_source_tier({"source": "MarketWatch", "title": "Stocks move"}) == "T1_5"
    assert kazike_engine.infer_source_tier({"source": "量子位", "title": "AI 行业消息"}) == "T2"

    now_utc = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
    ts_str = "2026-11-22 19:30"
    distinct_topics = [
        ("AAPL", "苹果下季度端侧智能手机出货预测上调 12%", "消费电子供应链排产持续改善，台积电先进制程产能利用率提升。"),
        ("US10Y", "美联储官员重申关注核心服务通胀黏性与美债长端期限溢价", "十年期国债收益率维持高位，市场调整年末利率定价路径。"),
        ("BTC", "现货比特币交易所交易基金单日净流入突破 4.8 亿美元", "机构合规渠道资金持续回补，加密市场杠杆率保持温和水平。"),
        ("TSLA", "特斯拉北美储能工厂 Megapack 年度装机量创历史新高", "公用事业级电网储能需求强劲增长，驱动能源业务毛利率改善。"),
        ("MSFT", "微软发布新一代企业级云计算数据中心液冷散热方案", "云基础设施资本开支向高功率密度机柜倾斜，提升能效比。"),
        ("AMZN", "亚马逊北美物流履约中心自动化分拣效率提升 18%", "单票配送成本进一步下降，支撑零售板块营业利润率扩展。"),
    ]
    raw_items: list[dict[str, Any]] = []
    for idx, (sym, ttl, summ) in enumerate(distinct_topics):
        raw_items.append({
            "id": f"cls-{idx}",
            "source": "财联社电报",
            "category": "equities",
            "time_bjt": ts_str,
            "title": ttl,
            "summary": summ,
            "tickers": [sym],
            "url": f"https://www.cls.cn/detail/990{idx}",
        })
    raw_items.append({
        "id": "eq-wrap-keep",
        "source": "美股盘面",
        "category": "equities",
        "time_bjt": ts_str,
        "title": "美股主要指数收盘汇总",
        "summary": "标普500与纳指盘面收报。",
        "tickers": ["SPY", "QQQ"],
        "url": "",
    })

    visible, stats = kazike_engine.process_financial_signals(
        raw_items,
        now_utc=now_utc,
        receipts_path=tmp_path / "receipts.json",
        browse_floor=0,
        max_per_source=2,
        max_visible=10,
    )
    cls_kept = [x for x in visible if x["source"] == "财联社电报"]
    eq_kept = [x for x in visible if x["source"] == "美股盘面"]
    assert stats["after_dedup"] == 7
    assert len(cls_kept) == 2
    assert len(eq_kept) == 1
    assert stats["source_count"] == 2


def test_rss_content_encoded_cn_tickers_and_curated_articles(tmp_path: Path) -> None:
    """Verify <content:encoded> RSS parsing, CN ticker mapping, and AI与金融研报 article schema."""
    from datetime import datetime, timezone
    from backend import rss_sources

    now_utc = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
    rss_encoded_xml = """<?xml version="1.0" encoding="utf-8"?>
    <rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
      <channel>
        <title>Test Feed</title>
        <item>
          <title>消费电子与智能电车产业链更新</title>
          <description></description>
          <content:encoded><![CDATA[<p>苹果发布新一代端侧芯片，特斯拉加速自动驾驶落地。</p>]]></content:encoded>
          <link>https://example.com/cn-tickers</link>
          <pubDate>Sun, 22 Nov 2026 10:00:00 GMT</pubDate>
        </item>
      </channel>
    </rss>"""

    spec = {"id": "cn-test", "source": "测试订阅", "category": "equities", "tier": "T1_5", "why": "产业链跟踪"}
    items = rss_sources.parse_feed_xml(rss_encoded_xml, spec, now_utc=now_utc)
    assert len(items) == 1
    assert "AAPL" in items[0]["tickers"]
    assert "TSLA" in items[0]["tickers"]
    assert "苹果发布新一代端侧芯片" in items[0]["summary"]
    assert rss_sources.extract_tickers("苹果发布AI手机，特斯拉更新智驾") == ["AAPL", "TSLA"]

    # Frontmatter publisher -> source="AI与金融研报"
    md_path = tmp_path / "feed_test.md"
    md_path.write_text(
        "---\n"
        'title: "AI 算力资本开支跟踪"\n'
        'summary: "摘要内容"\n'
        'publisher: "Import AI"\n'
        'url: "https://importai.substack.com/p/test"\n'
        "score: 88\n"
        "---\n\n正文第一段关于英伟达与微软云资本开支的深度分析。\n",
        encoding="utf-8",
    )
    parsed_art = signal_aggregator.parse_markdown_article(md_path, "同步深度长文")
    assert parsed_art is not None
    assert parsed_art["source"] == "AI与金融研报"
    assert parsed_art["publisher"] == "Import AI"
    assert parsed_art["score"] == 88
    assert parsed_art["url"] == "https://importai.substack.com/p/test"

    # Live cockpit_state.json contains AI与金融研报 articles
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    ai_articles = [a for a in (state.get("articles") or []) if a.get("source") == "AI与金融研报"]
    assert len(ai_articles) >= 1
    for art in ai_articles:
        assert str(art.get("publisher") or "").strip() != ""
        assert isinstance(art.get("score"), int)
        assert str(art.get("url") or "").startswith(("http://", "https://"))


def test_opml_and_caffeine_brief_endpoints_and_ui_markers(tmp_path: Path) -> None:
    """Verify /feeds.opml and /api/caffeine-brief.md auth/responses and DESIGN/web markers."""
    import xml.etree.ElementTree as ET

    srv = server.create_server(
        "127.0.0.1",
        0,
        web_dir=WEB_DIR,
        state_path=STATE_PATH,
        db_path=tmp_path / "journal.db",
        access_password="pw-opml",
        read_token="tok-opml-123",
    )
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    base = f"http://127.0.0.1:{srv.server_port}"
    try:
        for ep in ("/feeds.opml", "/api/caffeine-brief.md"):
            with pytest.raises(urllib.error.HTTPError) as exc_401:
                urllib.request.urlopen(f"{base}{ep}", timeout=5)
            assert exc_401.value.code == 401

        with urllib.request.urlopen(f"{base}/feeds.opml?token=tok-opml-123", timeout=5) as resp:
            assert resp.status == 200
            opml_xml = resp.read().decode("utf-8")
            assert "<opml" in opml_xml
            root = ET.fromstring(opml_xml)
            assert root.tag == "opml"
            rss_outlines = [el for el in root.iter("outline") if el.attrib.get("type") == "rss"]
            assert len(rss_outlines) >= 20

        with urllib.request.urlopen(f"{base}/api/caffeine-brief.md?token=tok-opml-123", timeout=5) as resp:
            assert resp.status == 200
            brief_md = resp.read().decode("utf-8")
            assert "Caffeine" in brief_md and "Fable 5.1" in brief_md
    finally:
        srv.shutdown()
        srv.server_close()
        th.join(timeout=5)

    assert (COCKPIT_ROOT / "DESIGN.md").is_file()
    assert (COCKPIT_ROOT / "docs" / "CAFFEINE_FABLE_BRIEF.md").is_file()
    index_html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    assert 'data-asource="AI与金融研报"' in index_html
    assert 'id="exportOpmlBtn"' in index_html

