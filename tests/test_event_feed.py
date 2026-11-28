from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend import event_feed, kazike_engine, server
from backend.public_snapshot import project_public_state, publish_generation

BJT = timezone(timedelta(hours=8))
NOW_UTC = datetime(2026, 11, 28, 4, 0, tzinfo=timezone.utc)


def _sig(
    sig_id: str,
    title: str,
    *,
    source: str = "财联社电报",
    tier: str = "T1_5",
    category: str = "equities",
    tickers: list[str] | None = None,
    url: str = "",
    summary: str = "",
    score: int = 78,
    time_bjt: str = "2026-11-28 11:30",
    selected: bool = True,
) -> dict:
    return {
        "id": sig_id,
        "source": source,
        "source_tier": tier,
        "category": category,
        "direction": "neutral",
        "importance": "高" if score >= 75 else "中",
        "time_bjt": time_bjt,
        "title": title,
        "summary": summary,
        "tickers": tickers or [],
        "topics": [],
        "url": url or f"https://example.com/{sig_id}",
        "attention_score": score,
        "selected": selected,
    }


def test_case1_wire_reprint_bilingual_translation_and_replay_idempotence(tmp_path: Path) -> None:
    """Case 1: Same wire reprint + EN/CN translation + replay -> 1 card, 1 origin family, max 1 notification."""
    items = [
        _sig(
            "s1",
            "Reuters: Nvidia CFO Colette Kress to step down next month",
            source="CNBC",
            tickers=["NVDA"],
            url="https://www.cnbc.com/2026/11/28/nvda-cfo.html",
            score=84,
            time_bjt="2026-11-28 09:00",
        ),
        _sig(
            "s2",
            "据路透报道，英伟达首席财务官将于下月离任",
            source="财联社电报",
            tickers=["NVDA"],
            url="https://www.cls.cn/detail/1002",
            score=84,
            time_bjt="2026-11-28 09:05",
        ),
        _sig(
            "s3",
            "转述路透：英伟达CFO下月卸任",
            source="华尔街见闻",
            tickers=["NVDA"],
            url="https://wallstreetcn.com/articles/1003",
            score=80,
            time_bjt="2026-11-28 09:10",
        ),
    ]
    cards1, stats1 = event_feed.update_event_feed(items, data_dir=tmp_path, now_utc=NOW_UTC)
    assert len(cards1) == 1
    card = cards1[0]
    assert card["independent_family_count"] == 1
    assert card["total_source_count"] == 3
    assert len(card["provenance_families"][0]["links"]) == 3
    assert card["verification_status"] == "unverified"
    assert card["substantive_version"] == 1

    # Replay same batch -> identical event_id, unchanged substantive_version
    cards2, stats2 = event_feed.update_event_feed(items, data_dir=tmp_path, now_utc=NOW_UTC + timedelta(minutes=10))
    assert len(cards2) == 1
    assert cards2[0]["event_id"] == card["event_id"]
    assert cards2[0]["substantive_version"] == 1
    assert stats2["reused_or_replayed"] == 3

    # Disabled by default
    push_path = tmp_path / "event_push_state.json"
    assert event_feed.evaluate_event_notifications(cards2, push_path, enabled=False) == []

    # When enabled (with allow_unverified_high), max 1 alert across retries
    alerts1 = event_feed.evaluate_event_notifications(
        cards2, push_path, enabled=True, allow_unverified_high=True, channels=("feishu_dm",)
    )
    alerts2 = event_feed.evaluate_event_notifications(
        cards2, push_path, enabled=True, allow_unverified_high=True, channels=("feishu_dm",)
    )
    assert len(alerts1) == 1
    assert alerts1[0]["title"].startswith("【未证实】")
    assert alerts2 == []


def test_case2_shared_interview_family_vs_independent_corroboration_not_confirmed(tmp_path: Path) -> None:
    """Case 2: Two URLs citing the same interview stay 1 family; 2nd independent source -> multi_source (not confirmed), same version."""
    batch1 = [
        _sig(
            "iv1",
            "TechCrunch reports Nvidia CFO will step down in an interview with CNBC",
            source="TechCrunch",
            tickers=["NVDA"],
            url="https://techcrunch.com/nvda-cfo",
            time_bjt="2026-11-28 09:00",
        ),
        _sig(
            "iv2",
            "某财经媒体：在接受 CNBC 采访时透露英伟达CFO即将离任",
            source="金融快报",
            tickers=["NVDA"],
            url="https://finance-mirror.example/nvda",
            time_bjt="2026-11-28 09:15",
        ),
    ]
    cards1, _ = event_feed.update_event_feed(batch1, data_dir=tmp_path, now_utc=NOW_UTC)
    assert len(cards1) == 1
    assert cards1[0]["independent_family_count"] == 1
    assert len(cards1[0]["provenance_families"][0]["links"]) == 2
    assert cards1[0]["verification_status"] == "unverified"

    # Independent Bloomberg reporting without official confirmation upgrades to multi_source but keeps substantive_version=1
    batch2 = [
        _sig(
            "ind1",
            "Bloomberg: Nvidia CFO expected to resign from chipmaker",
            source="彭博摘要",
            tickers=["NVDA"],
            url="https://bloomberg.example/nvda-cfo",
            time_bjt="2026-11-28 10:00",
        )
    ]
    cards2, _ = event_feed.update_event_feed(batch2, data_dir=tmp_path, now_utc=NOW_UTC + timedelta(minutes=30))
    assert len(cards2) == 1
    assert cards2[0]["independent_family_count"] == 2
    assert cards2[0]["verification_status"] == "multi_source"
    assert cards2[0]["verification_label"] == "多方待确认"
    assert cards2[0]["substantive_version"] == 1  # No new verifiable fact -> do not re-unread or notify


def test_case3_incremental_fact_denial_correction_and_targeted_correction_push(tmp_path: Path) -> None:
    """Case 3: Effective date increments timeline; minor successor stays quiet; official denial/correction notifies prior recipients."""
    push_path = tmp_path / "event_push_state.json"

    # Step 1: initial high-impact credible rumor pushed
    c1, _ = event_feed.update_event_feed(
        [
            _sig(
                "c3_1",
                "据知情人士透露，英伟达首席财务官将于近期离任",
                source="MarketWatch",
                tickers=["NVDA"],
                score=85,
                time_bjt="2026-11-28 09:00",
            )
        ],
        data_dir=tmp_path,
        now_utc=NOW_UTC,
    )
    dispatched1 = event_feed.evaluate_event_notifications(
        c1, push_path, enabled=True, allow_unverified_high=True, channels=("feishu_dm", "wecom_bot")
    )
    assert len(dispatched1) == 2

    # Step 2: substantive update adding effective date (material)
    c2, _ = event_feed.update_event_feed(
        [
            _sig(
                "c3_2",
                "英伟达发布SEC 8-K正式确认首席财务官离任，12月15日正式生效",
                source="SEC EDGAR",
                tier="T1",
                tickers=["NVDA"],
                score=90,
                url="https://www.sec.gov/Archives/edgar/data/1045810/000104581026000012/nvda8k.htm",
                time_bjt="2026-11-28 10:00",
            )
        ],
        data_dir=tmp_path,
        now_utc=NOW_UTC + timedelta(hours=1),
    )
    assert c2[0]["verification_status"] == "confirmed"
    assert c2[0]["substantive_version"] == 2
    assert "生效日 12月15日" in c2[0]["latest_change"]
    assert len(c2[0]["timeline"]) == 2
    dispatched2 = event_feed.evaluate_event_notifications(
        c2, push_path, enabled=True, channels=("feishu_dm", "wecom_bot")
    )
    assert len(dispatched2) == 2

    # Step 3: minor successor detail -> timeline step added with is_material_change=False -> no push
    c3, _ = event_feed.update_event_feed(
        [
            _sig(
                "c3_3",
                "英伟达补充披露首席财务官离任后接任者为 Jane Doe",
                source="财联社电报",
                tickers=["NVDA"],
                score=82,
                time_bjt="2026-11-28 10:30",
            )
        ],
        data_dir=tmp_path,
        now_utc=NOW_UTC + timedelta(hours=2),
    )
    assert c3[0]["substantive_version"] == 3
    assert c3[0]["timeline"][-1]["is_material_change"] is False
    assert event_feed.evaluate_event_notifications(c3, push_path, enabled=True, channels=("feishu_dm", "wecom_bot")) == []

    # Step 4: official correction preserves prior claim and notifies previously alerted channels once
    c4, _ = event_feed.update_event_feed(
        [
            _sig(
                "c3_4",
                "英伟达发布更正声明：此前首席财务官离任日期报道有误，调整为明年1月生效",
                source="SEC EDGAR",
                tier="T1",
                tickers=["NVDA"],
                score=92,
                url="https://www.sec.gov/Archives/edgar/data/1045810/000104581026000013/nvda8k-corr.htm",
                time_bjt="2026-11-28 11:00",
            )
        ],
        data_dir=tmp_path,
        now_utc=NOW_UTC + timedelta(hours=3),
    )
    assert c4[0]["verification_status"] == "corrected"
    assert c4[0]["prior_claims"] and c4[0]["prior_claims"][0]["status"] == "corrected"
    corr_alerts = event_feed.evaluate_event_notifications(
        c4, push_path, enabled=True, channels=("feishu_dm", "wecom_bot", "telegram")
    )
    # Only the channels that previously received an alert for this event get the correction alert
    assert {a["channel"] for a in corr_alerts} == {"feishu_dm", "wecom_bot"}
    assert all(a["kind"] == "correction" for a in corr_alerts)
    assert event_feed.evaluate_event_notifications(c4, push_path, enabled=True, channels=("feishu_dm", "wecom_bot")) == []


def test_case4_separate_same_ticker_events_different_months_roundups_and_split(tmp_path: Path) -> None:
    """Case 4: Same ticker diff subtype, diff macro month, roundups, distant months, KOL opinions & split_event."""
    items = [
        _sig("e1", "Nvidia CFO to step down", tickers=["NVDA"], time_bjt="2026-11-28 09:00"),
        _sig("e2", "Nvidia cuts orders after new export restrictions", tickers=["NVDA"], time_bjt="2026-11-28 09:30"),
        _sig("cpi10", "美国10月CPI同比上涨2.6%", category="macro", time_bjt="2026-11-28 09:40"),
        _sig("cpi11", "美国11月CPI同比上涨2.8%", category="macro", time_bjt="2026-11-28 09:45"),
        _sig("ru1", "周六你需要知道的隔夜全球要闻：美股收涨、黄金回调", source="华尔街见闻", time_bjt="2026-11-28 09:50"),
        _sig("kol1", "NVDA CFO change thesis breakdown", source="X核心观点", tickers=["NVDA"], time_bjt="2026-11-28 10:00"),
    ]
    cards, stats = event_feed.update_event_feed(items, data_dir=tmp_path, now_utc=NOW_UTC)
    assert stats["opinions_skipped"] == 1
    assert len(cards) == 5  # e1, e2, cpi10, cpi11, ru1 strictly separated
    by_title = {c["title"]: c for c in cards}
    assert by_title["Nvidia cuts orders after new export restrictions"]["event_id"] != by_title["Nvidia CFO to step down"]["event_id"]
    assert by_title["Nvidia CFO to step down"]["event_id"] in by_title["Nvidia cuts orders after new export restrictions"]["related_event_ids"]
    assert by_title["美国10月CPI同比上涨2.6%"]["event_id"] != by_title["美国11月CPI同比上涨2.8%"]["event_id"]
    assert by_title["周六你需要知道的隔夜全球要闻：美股收涨、黄金回调"]["verification_status"] == "roundup"

    # Deterministic split_event test on active November ledger
    ledger = json.loads((tmp_path / "event_ledger.json").read_text(encoding="utf-8"))
    eid = by_title["Nvidia CFO to step down"]["event_id"]
    ledger["events"][eid]["evidence_items"].append(
        {
            **ledger["events"][eid]["evidence_items"][0],
            "evidence_key": "u:https://example.com/extra-split",
            "norm_url": "https://example.com/extra-split",
            "url": "https://example.com/extra-split",
            "title": "拆分测试独立事件",
        }
    )
    ledger["events"][eid]["evidence_keys"].append("u:https://example.com/extra-split")
    split_ledger = event_feed.split_event(ledger, eid, ["u:https://example.com/extra-split"], now_utc=NOW_UTC)
    assert len(split_ledger["events"]) == len(ledger["events"]) + 1

    # Distant months (e.g., 16 days or 79 days later) never reuse the old card
    distant_cards, _ = event_feed.update_event_feed(
        [_sig("e_later", "Nvidia CFO to step down in reorganization", tickers=["NVDA"], time_bjt="2026-12-22 10:00")],
        data_dir=tmp_path,
        now_utc=datetime(2026, 12, 22, 2, 0, tzinfo=timezone.utc),
    )
    later_ev = next(c for c in distant_cards if "reorganization" in c["title"])
    assert later_ev["event_id"] != by_title["Nvidia CFO to step down"]["event_id"]


def test_case5_out_of_order_late_rumor_does_not_regress_confirmed_or_conflicted(tmp_path: Path) -> None:
    """Case 5: Official confirmation arrived first; later-ingested older rumor never regresses status; denial creates conflict."""
    c1, _ = event_feed.update_event_feed(
        [
            _sig(
                "auth1",
                "Federal Reserve cuts policy rate by 25 bps to 4.25%-4.50%",
                source="美联储官方",
                tier="T1",
                category="macro",
                tickers=["US10Y"],
                url="https://federalreserve.gov/newsevents/pressreleases/monetary20261128a.htm",
                score=92,
                time_bjt="2026-11-28 11:00",
            )
        ],
        data_dir=tmp_path,
        now_utc=NOW_UTC,
    )
    assert c1[0]["verification_status"] == "confirmed"
    eid = c1[0]["event_id"]

    # Out-of-order late arrival of older rumor with matching entity & action
    c2, _ = event_feed.update_event_feed(
        [
            _sig(
                "rumor_old",
                "据知情人士称美联储本周可能降息25个基点",
                source="市场快讯",
                tier="T2",
                category="macro",
                tickers=["US10Y"],
                url="https://rumor.example/fed-25bps",
                score=72,
                time_bjt="2026-11-28 08:00",
            )
        ],
        data_dir=tmp_path,
        now_utc=NOW_UTC + timedelta(minutes=15),
    )
    assert len(c2) == 1
    assert c2[0]["event_id"] == eid
    assert c2[0]["verification_status"] == "confirmed"
    assert c2[0]["substantive_version"] == 1
    assert c2[0]["total_source_count"] == 2

    # Subsequent conflicting denial produces 'conflicted' preserving both claims side-by-side
    c3, _ = event_feed.update_event_feed(
        [
            _sig(
                "deny1",
                "美联储官方发言人正式否认降息25个基点报道称尚未投票决定",
                source="美联储官方",
                tier="T1",
                category="macro",
                tickers=["US10Y"],
                url="https://federalreserve.gov/newsevents/pressreleases/monetary20261128b.htm",
                score=90,
                time_bjt="2026-11-28 11:30",
            )
        ],
        data_dir=tmp_path,
        now_utc=NOW_UTC + timedelta(minutes=30),
    )
    assert c3[0]["verification_status"] == "conflicted"
    assert len(c3[0]["conflict_claims"]) == 2


def test_pipeline_integration_api_v2_events_rbac_and_generation_preservation(tmp_path: Path) -> None:
    """Verify kazike_engine pre_cluster_items, public projection sanitization, publish preservation, and /api/v2/events RBAC."""
    # 1. kazike_engine keeps /api/v2/signals behavior while emitting pre_cluster_items
    visible, stats = kazike_engine.process_financial_signals(
        [
            _sig("k1", "Apple faces countersuit from OpenAI", tickers=["AAPL", "MSFT"], time_bjt="2026-11-28 10:00"),
            _sig("k2", "arXiv climate risk bond sensitivity model", tickers=["AAPL", "MSFT"], time_bjt="2026-11-28 10:05"),
        ],
        receipts_path=tmp_path / "receipts.json",
    )
    assert len(stats["pre_cluster_items"]) == 2
    cards, _ = event_feed.update_event_feed(stats.pop("pre_cluster_items"), data_dir=tmp_path, now_utc=NOW_UTC)
    assert len(cards) == 2  # English non-related headlines no longer collapse!

    # 2. Public projection drops private fields and blocks private text
    proj = project_public_state(
        {
            "risk_gate": {"light": "GREEN", "long_risk_cap": 1.0, "label": "绿灯"},
            "event_cards": [
                {**cards[0], "holding_radar": {"status": "顺风"}, "personal_score": 99.0},
                {"id": "bad", "event_id": "bad", "title": "My account balance is $14,900 with 2 contracts"},
            ],
            "event_stats": {"total_events": 1, "secret_token": "leak"},
        }
    )
    assert len(proj["event_cards"]) == 1
    assert "holding_radar" not in proj["event_cards"][0]
    assert "personal_score" not in proj["event_cards"][0]
    assert "secret_token" not in proj["event_stats"]

    # 3. publish_generation preserves richer server event_cards when stateless client omits them
    base = tmp_path / "vps"
    (base / "data").mkdir(parents=True)
    (base / "articles").mkdir(parents=True)
    publish_generation(
        base,
        {
            "risk_gate": {"light": "GREEN", "long_risk_cap": 1.0, "label": "绿灯"},
            "generated_at_bjt": "2026-11-28 11:00:00+08:00",
            "signals": visible,
            "event_cards": proj["event_cards"],
        },
        [],
        producer_id="cn-vps",
    )
    publish_generation(
        base,
        {
            "risk_gate": {"light": "GREEN", "long_risk_cap": 1.0, "label": "绿灯"},
            "generated_at_bjt": "2026-11-28 11:05:00+08:00",
            "signals": visible,
        },
        [],
        producer_id="windows-sync",
    )

    srv = server.create_server(
        "127.0.0.1",
        0,
        web_dir=Path("web"),
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
        port = srv.server_address[1]
        # READ_ONLY allowed on public scope=all and sees preserved event card
        req_ok = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/v2/events?scope=all",
            headers={"Authorization": "Bearer tok-ro"},
        )
        with urllib.request.urlopen(req_ok, timeout=5) as r:
            body = json.loads(r.read().decode("utf-8"))
        assert body["ok"] is True and body["count"] == 1
        assert "holding_radar" not in body["events"][0]

        # READ_ONLY blocked with 403 on scope=held
        req_forbid = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/v2/events?scope=held",
            headers={"Authorization": "Bearer tok-ro"},
        )
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req_forbid, timeout=5)
        assert exc.value.code == 403
    finally:
        srv.shutdown()
        srv.server_close()

    # 4. New generation explicit event_cards replace retired rich cards; stateless omitted event_cards fall back to ledger
    roll_base = tmp_path / "roll"
    (roll_base / "data").mkdir(parents=True)
    (roll_base / "articles").mkdir(parents=True)
    apple_cards, _ = event_feed.update_event_feed(
        [
            _sig(
                "app1",
                "Apple SEC 8-K confirms CEO succession timeline",
                source="SEC EDGAR",
                tier="T1",
                tickers=["AAPL"],
                time_bjt="2026-11-28 09:00",
            )
        ],
        data_dir=roll_base / "data",
        now_utc=NOW_UTC,
    )
    publish_generation(
        roll_base,
        {
            "risk_gate": {"light": "GREEN", "long_risk_cap": 1.0, "label": "绿灯"},
            "generated_at_bjt": "2026-11-28 11:10:00+08:00",
            "signals": [],
            "event_cards": apple_cards,
        },
        [],
        producer_id="cn-vps",
    )
    # Ledger rotates to a fresh unverified Tesla card 35 days later (Apple pruned by retention)
    later_utc = NOW_UTC + timedelta(days=35)
    tesla_cards, _ = event_feed.update_event_feed(
        [
            _sig(
                "tsla1",
                "Tesla reported planning gigafactory expansion",
                source="MarketWatch",
                tickers=["TSLA"],
                time_bjt="2027-01-02 10:00",
            )
        ],
        data_dir=roll_base / "data",
        now_utc=later_utc,
    )
    m2 = publish_generation(
        roll_base,
        {
            "risk_gate": {"light": "GREEN", "long_risk_cap": 1.0, "label": "绿灯"},
            "generated_at_bjt": "2027-01-02 10:05:00+08:00",
            "signals": [],
            "event_cards": tesla_cards,
        },
        [],
        producer_id="cn-vps",
    )
    st2 = json.loads((roll_base / "data" / f"generations/gen_{m2['generation']}/state.json").read_text("utf-8"))
    assert len(st2["event_cards"]) == 1 and "Tesla" in st2["event_cards"][0]["title"]

    # Stateless publish without event_cards falls back cleanly to server ledger (Tesla, NOT retired Apple)
    m3 = publish_generation(
        roll_base,
        {
            "risk_gate": {"light": "GREEN", "long_risk_cap": 1.0, "label": "绿灯"},
            "generated_at_bjt": "2027-01-02 10:10:00+08:00",
            "signals": [],
        },
        [],
        producer_id="windows-primary",
    )
    st3 = json.loads((roll_base / "data" / f"generations/gen_{m3['generation']}/state.json").read_text("utf-8"))
    assert len(st3["event_cards"]) == 1 and "Tesla" in st3["event_cards"][0]["title"]


def test_codex_review_regressions_same_url_ordering_stance_split_and_failure_fallback(tmp_path: Path) -> None:
    """Verify Codex blocker fixes: same-URL correction + effective_at update, monotonic timestamp, strict denial/correction wording, and category/ticker split."""
    # 1. Same-URL correction + effective_at revision (12月15日 -> 12月18日)
    d1 = tmp_path / "same_url"
    same_u = "https://example.com/nvda-cfo-post"
    c1, _ = event_feed.update_event_feed(
        [_sig("u1", "英伟达首席财务官离任，据知情人士透露 12月15日正式生效", tickers=["NVDA"], url=same_u, time_bjt="2026-11-28 09:00")],
        data_dir=d1,
        now_utc=NOW_UTC,
    )
    assert c1[0]["effective_at"] == "12月15日"
    c2, _ = event_feed.update_event_feed(
        [_sig("u1", "英伟达官方更正声明：首席财务官离任调整为12月18日正式生效", source="SEC EDGAR", tier="T1", tickers=["NVDA"], url=same_u, time_bjt="2026-11-28 10:00")],
        data_dir=d1,
        now_utc=NOW_UTC + timedelta(minutes=10),
    )
    assert c2[0]["verification_status"] == "corrected"
    assert c2[0]["substantive_version"] == 2
    assert c2[0]["effective_at"] == "12月18日"
    assert c2[0]["total_source_count"] == 1

    # 2. Out-of-order 09:00 official -> 11:00 update -> 10:00 late arrival preserves 11:00 last_substantive_at
    d2 = tmp_path / "ooo"
    event_feed.update_event_feed(
        [_sig("o1", "英伟达首席财务官离任正式确认", source="SEC EDGAR", tier="T1", tickers=["NVDA"], time_bjt="2026-11-28 09:00")],
        data_dir=d2,
        now_utc=NOW_UTC,
    )
    event_feed.update_event_feed(
        [_sig("o2", "英伟达首席财务官离任，12月18日正式生效", tickers=["NVDA"], time_bjt="2026-11-28 11:00")],
        data_dir=d2,
        now_utc=NOW_UTC + timedelta(minutes=5),
    )
    cards_ooo, _ = event_feed.update_event_feed(
        [_sig("o3", "英伟达首席财务官离任传闻：12月15日正式生效", tickers=["NVDA"], time_bjt="2026-11-28 10:00")],
        data_dir=d2,
        now_utc=NOW_UTC + timedelta(minutes=10),
    )
    assert cards_ooo[0]["last_substantive_at"].startswith("2026-11-28 11:00")
    assert "12月18日" in cards_ooo[0]["latest_change"]

    # 3. Normal business wording '调整为' and hedged '官方尚未确认，知情人士否认' stay unverified reports
    d3 = tmp_path / "stance"
    st_cards, _ = event_feed.update_event_feed(
        [
            _sig("s_adj", "英伟达将出口订单交付日期调整为12月18日", source="财联社电报", tickers=["NVDA"], time_bjt="2026-11-28 10:00"),
            _sig("s_anon_deny", "官方尚未确认，知情人士否认此前英伟达首席财务官离任传闻", source="财联社电报", tickers=["NVDA"], time_bjt="2026-11-28 10:05"),
        ],
        data_dir=d3,
        now_utc=NOW_UTC,
    )
    by_t = {c["title"]: c for c in st_cards}
    assert by_t["英伟达将出口订单交付日期调整为12月18日"]["verification_status"] == "unverified"
    assert by_t["官方尚未确认，知情人士否认此前英伟达首席财务官离任传闻"]["verification_status"] == "unverified"

    # 4. split_event preserves category, tickers and kept-side secondary topics on both sides
    d4 = tmp_path / "split_preserve"
    event_feed.update_event_feed(
        [
            _sig("sp1", "英伟达首席财务官离任，12月15日正式生效", category="equities", tickers=["NVDA"], time_bjt="2026-11-28 09:00")
            | {"topics": ["算力芯片"]},
            _sig("sp2", "英伟达首席财务官离任补充报道，12月18日正式生效", category="equities", tickers=["NVDA"], time_bjt="2026-11-28 10:00")
            | {"topics": ["七巨头·云厂"]},
            _sig("sp3", "英伟达首席财务官离任外媒报道", category="equities", tickers=["NVDA"], time_bjt="2026-11-28 10:30")
            | {"topics": ["独立拆分专题"]},
        ],
        data_dir=d4,
        now_utc=NOW_UTC,
    )
    ledger = json.loads((d4 / "event_ledger.json").read_text("utf-8"))
    eid = next(iter(ledger["events"]))
    split_out = event_feed.split_event(ledger, eid, [ledger["events"][eid]["evidence_keys"][2]], now_utc=NOW_UTC)
    assert len(split_out["events"]) == 2
    assert all(e["category"] == "equities" and "NVDA" in e["tickers"] for e in split_out["events"].values())
    assert "七巨头·云厂" in split_out["events"][eid]["topics"]

    # 5. Same-URL same-title summary effective_at update + 15->18->15 back-and-forth confirmation
    d5 = tmp_path / "eff_cycle"
    sec_u = "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000099/eff.htm"
    event_feed.update_event_feed(
        [
            _sig("ec1", "英伟达高管任命公告", source="SEC EDGAR", tier="T1", tickers=["NVDA"], url=sec_u, time_bjt="2026-11-28 09:00")
            | {"summary": "12月15日正式生效"}
        ],
        data_dir=d5,
        now_utc=NOW_UTC,
    )
    ec2, _ = event_feed.update_event_feed(
        [
            _sig("ec1", "英伟达高管任命公告", source="SEC EDGAR", tier="T1", tickers=["NVDA"], url=sec_u, time_bjt="2026-11-28 10:00")
            | {"summary": "12月18日正式生效"}
        ],
        data_dir=d5,
        now_utc=NOW_UTC + timedelta(minutes=10),
    )
    assert ec2[0]["effective_at"] == "12月18日"
    ec3, _ = event_feed.update_event_feed(
        [
            _sig("ec1", "英伟达高管任命公告", source="SEC EDGAR", tier="T1", tickers=["NVDA"], url=sec_u, time_bjt="2026-11-28 11:00")
            | {"summary": "12月15日正式生效"}
        ],
        data_dir=d5,
        now_utc=NOW_UTC + timedelta(minutes=20),
    )
    assert ec3[0]["effective_at"] == "12月15日"

    # 6. Same-URL 09:00 original -> 11:00 correction -> replay 09:00 keeps 11:00 evidence untouched
    d6 = tmp_path / "same_url_late_replay"
    shared_u = "https://example.com/fed-notice"
    orig_0900 = _sig("su1", "美联储官员暗示将维持利率不变", source="财联社电报", tickers=["US10Y"], url=shared_u, time_bjt="2026-11-28 09:00")
    corr_1100 = _sig("su1", "更正此前报道：美联储官员实为讨论降息25个基点", source="SEC EDGAR", tier="T1", tickers=["US10Y"], url=shared_u, time_bjt="2026-11-28 11:00")
    event_feed.update_event_feed([orig_0900], data_dir=d6, now_utc=NOW_UTC)
    event_feed.update_event_feed([corr_1100], data_dir=d6, now_utc=NOW_UTC + timedelta(hours=2))
    after_old, _ = event_feed.update_event_feed([orig_0900], data_dir=d6, now_utc=NOW_UTC + timedelta(hours=2, minutes=5))
    assert after_old[0]["verification_status"] == "corrected"
    l6 = json.loads((d6 / "event_ledger.json").read_text("utf-8"))
    ev6 = next(iter(l6["events"].values()))
    assert "更正" in ev6["evidence_items"][0]["title"]

    # 7. Anonymous/unconfirmed speculation never becomes confirmed/corrected; 1 correction + 16 reprints stays idempotent
    d7 = tmp_path / "anon_and_prune_replay"
    anon_cards, _ = event_feed.update_event_feed(
        [
            _sig("an1", "知情人士：英伟达拟于下周正式宣布首席财务官离任，公司尚未确认", source="财联社电报", tickers=["NVDA"], time_bjt="2026-11-28 09:00"),
            _sig("an2", "消息人士称此前英伟达裁员报道是误报，公司尚未确认", source="财联社电报", tickers=["NVDA"], time_bjt="2026-11-28 09:05"),
        ],
        data_dir=d7,
        now_utc=NOW_UTC,
    )
    assert all(c["verification_status"] == "unverified" for c in anon_cards)

    d8 = tmp_path / "trimmed_replay"
    corr_sig = _sig("tr0", "英伟达发布更正声明：此前首席财务官离任日期报道有误", source="SEC EDGAR", tier="T1", tickers=["NVDA"], url="https://sec.gov/c0", time_bjt="2026-11-28 10:00")
    reprints = [
        _sig(
            f"tr{i}",
            f"据路透报道：英伟达发布更正声明，此前首席财务官离任日期第{i}条 12月15日生效",
            source=f"快讯源{i}",
            tickers=["NVDA"],
            url=f"https://wire.example/item-{i}",
            time_bjt=f"2026-11-28 10:{10 + i:02d}",
        )
        for i in range(1, 18)
    ]
    c_trim1, _ = event_feed.update_event_feed([corr_sig, *reprints], data_dir=d8, now_utc=NOW_UTC)
    v_before = c_trim1[0]["substantive_version"]
    c_trim2, st_trim2 = event_feed.update_event_feed([corr_sig, *reprints], data_dir=d8, now_utc=NOW_UTC + timedelta(minutes=5))
    assert c_trim2[0]["substantive_version"] == v_before
    assert st_trim2["reused_or_replayed"] == 18
    l8 = json.loads((d8 / "event_ledger.json").read_text("utf-8"))
    ev8 = next(iter(l8["events"].values()))
    assert any(r.get("is_official") for r in ev8["evidence_items"])

    # 8. Unreadable/corrupt/bad-version ledger preserves prior cards on both publish_generation and state fallback
    vps_dir = tmp_path / "locked_ledger_vps"
    (vps_dir / "data").mkdir(parents=True)
    (vps_dir / "articles").mkdir(parents=True)
    (vps_dir / "data" / "event_ledger.json").write_text("{corrupt-json", encoding="utf-8")
    publish_generation(
        vps_dir,
        {
            "risk_gate": {"light": "GREEN", "long_risk_cap": 1.0, "label": "绿灯"},
            "generated_at_bjt": "2026-11-28 11:00:00+08:00",
            "signals": [],
            "event_cards": c_trim1[:1],
        },
        [],
        producer_id="cn-vps",
    )
    m_keep = publish_generation(
        vps_dir,
        {
            "risk_gate": {"light": "GREEN", "long_risk_cap": 1.0, "label": "绿灯"},
            "generated_at_bjt": "2026-11-28 11:10:00+08:00",
            "signals": [],
        },
        [],
        producer_id="stateless-sync",
    )
    st_keep = json.loads((vps_dir / "data" / f"generations/gen_{m_keep['generation']}/state.json").read_text("utf-8"))
    assert len(st_keep["event_cards"]) == 1
    # Valid JSON with corrupted event substantive_version / timeline.seq / non-finite fields must be rejected & preserve prior card
    (vps_dir / "data" / "event_ledger.json").write_text(
        json.dumps({"version": 1, "events": {"bad1": {"event_id": "bad1", "title": "test", "substantive_version": "bad-str"}}}),
        encoding="utf-8",
    )
    assert event_feed.try_load_public_event_cards_from_ledger(vps_dir / "data") is None
    m_keep2 = publish_generation(
        vps_dir,
        {"risk_gate": {"light": "GREEN", "long_risk_cap": 1.0, "label": "绿灯"}, "generated_at_bjt": "2026-11-28 11:15:00+08:00", "signals": []},
        [],
        producer_id="stateless-sync",
    )
    assert m_keep2["generation"] == m_keep["generation"] + 1
    st_keep2 = json.loads((vps_dir / "data" / f"generations/gen_{m_keep2['generation']}/state.json").read_text("utf-8"))
    assert len(st_keep2["event_cards"]) == 1
    assert st_keep2["event_cards"][0]["substantive_version"] == v_before

    # Bad timeline seq or non-finite numeric literal (1e999 -> Infinity) must fail closed as well
    (vps_dir / "data" / "event_ledger.json").write_text(
        json.dumps({"version": 1, "events": {"bad2": {"event_id": "bad2", "title": "bad tl", "timeline": [{"seq": "bad-seq", "summary": "x"}]}}}),
        encoding="utf-8",
    )
    assert event_feed.try_load_public_event_cards_from_ledger(vps_dir / "data") is None

    (vps_dir / "data" / "event_ledger.json").write_text(
        '{"version": 1, "events": {"bad3": {"event_id": "bad3", "title": "inf score", "attention_score": 1e999}}}',
        encoding="utf-8",
    )
    assert event_feed.try_load_public_event_cards_from_ledger(vps_dir / "data") is None
    assert event_feed._safe_int(float("inf"), 7) == 7
