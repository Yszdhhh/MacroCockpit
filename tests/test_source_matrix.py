"""Tests for data/industry_chain_map.json and backend/source_matrix.py."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.kol_intel_engine import FORBIDDEN_TERMS, MIN_VERIFIED_SAMPLE
from backend.source_matrix import DEFAULT_CHAIN_MAP_PATH, build_asset_source_matrix

EXPECTED_CATEGORY_IDS = (
    "macro_rates",
    "us_ai_semi",
    "cn_hk_equities",
    "crypto_onchain",
    "gold_commodities",
    "quant_allocation",
)


def _assert_no_forbidden_recursive(obj: Any, path: str = "root") -> None:
    if isinstance(obj, str):
        low = obj.lower()
        for term in FORBIDDEN_TERMS:
            assert term.lower() not in low, f"Forbidden term {term!r} found at {path}: {obj!r}"
    elif isinstance(obj, dict):
        for k, v in obj.items():
            k_low = str(k).lower()
            for term in FORBIDDEN_TERMS:
                assert term.lower() not in k_low, f"Forbidden term {term!r} in key at {path}.{k}"
            _assert_no_forbidden_recursive(v, f"{path}.{k}")
    elif isinstance(obj, (list, tuple)):
        for idx, item in enumerate(obj):
            _assert_no_forbidden_recursive(item, f"{path}[{idx}]")


def test_industry_chain_map_size_leaders_and_cleanliness() -> None:
    assert DEFAULT_CHAIN_MAP_PATH.exists()
    raw_bytes = DEFAULT_CHAIN_MAP_PATH.read_bytes()
    size_kb = len(raw_bytes) / 1024.0
    assert 12.0 <= size_kb <= 28.0, f"Expected 12~28KB, got {size_kb:.2f}KB ({len(raw_bytes)} bytes)"

    data = json.loads(raw_bytes.decode("utf-8"))
    _assert_no_forbidden_recursive(data, "industry_chain_map")
    assert "待评级" not in raw_bytes.decode("utf-8")

    us_syms = {item["symbol"]: item for item in data["us_leaders"]}
    for required in ("NVDA", "TSM", "ASML", "AVGO", "AMD", "MU", "MSFT", "GOOGL", "META", "AAPL", "TSLA", "VRT"):
        assert required in us_syms, f"Missing US leader {required}"
        assert len(us_syms[required]["a_share_mappings"]) >= 1, f"{required} missing A-share mappings"

    # Verify HK/CN cores & strategic resource bridges exist
    assert len(data["cn_hk_cores"]) >= 3
    assert len(data["resource_bridges"]) >= 3


def test_build_asset_source_matrix_live_cache_contract() -> None:
    matrix = build_asset_source_matrix()
    _assert_no_forbidden_recursive(matrix, "matrix")

    assert isinstance(matrix.get("updated_at_bjt"), str) and "+08:00" in matrix["updated_at_bjt"]
    summary = matrix["summary"]
    assert summary["categories_count"] == 6
    assert summary["verified_kols_linked"] >= 10
    assert summary["supply_chain_nodes"] >= 12

    cats = matrix["categories"]
    assert [c["id"] for c in cats] == list(EXPECTED_CATEGORY_IDS)

    for cat in cats:
        assert cat["name"]
        assert cat["core_question"]
        assert len(cat["primary_venues"]) >= 2
        for v in cat["primary_venues"]:
            assert v["type"] in {"领先风向标", "硬核基本面", "实时快讯"}
            assert v["url"].startswith("https://")
            assert v["focus"] and v["cadence"]

        assert 4 <= len(cat["top_kols"]) <= 6
        for kol in cat["top_kols"]:
            assert kol["handle"]
            assert kol["why_follow"]
            assert kol["url"].startswith("https://")
            vc = int(kol["verified_calls"])
            if vc < MIN_VERIFIED_SAMPLE:
                assert kol["win_rate_20d"] is None, f"Small sample KOL {kol['handle']} leaked win_rate_20d"
                assert kol["reliability_badge"] in {"样本积累", "定性观察"}
            else:
                assert kol["reliability_badge"] in {"高胜率", "稳健参考", "观点敏锐"}
                assert isinstance(kol["win_rate_20d"], float)

        assert len(cat["guru_longforms"]) >= 1
        assert cat["crowd_warning_channel"]["platform"]
        assert cat["crowd_warning_channel"]["what_to_watch"]
        assert len(cat["supply_chain_bridges"]) >= 1
        for bridge in cat["supply_chain_bridges"]:
            assert bridge["us_leader"] and bridge["us_name"] and bridge["focus"]
            assert len(bridge["a_shares"]) >= 1
            for ash in bridge["a_shares"]:
                assert ash["symbol"] and ash["name"] and ash["sector"]

    us_ai = next(c for c in cats if c["id"] == "us_ai_semi")
    nvda_bridge = next(b for b in us_ai["supply_chain_bridges"] if b["us_leader"] == "NVDA")
    assert any(a["symbol"] == "300476.SZ" and a["name"] == "胜宏科技" for a in nvda_bridge["a_shares"])


def test_dynamic_kol_sorting_and_small_sample_guard(tmp_path: Path) -> None:
    # Even if a synthetic scorecard passes a fake win_rate_20d=100.0 with verified_calls < 5,
    # build_asset_source_matrix must scrub it to None and downgrade the badge!
    synthetic_cards = [
        {
            "handle": "FakeHotHand",
            "name": "Fake Hot Hand",
            "category": "us-stocks",
            "role_label": "AI算力产业",
            "focus": "NVIDIA CoWoS Tier-1 Sleeve E21",
            "reliability_score": 95,
            "reliability_badge": "高胜率",
            "verified_calls": 3,
            "win_rate_20d": 100.0,
            "best_tickers": ["NVDA", "TSM"],
        },
        {
            "handle": "RealChipPro",
            "name": "Real Chip Pro",
            "category": "us-stocks",
            "role_label": "AI算力产业",
            "focus": "半导体存储与HBM周期",
            "reliability_score": 84,
            "reliability_badge": "高胜率",
            "verified_calls": 11,
            "win_rate_20d": 72.73,
            "best_tickers": ["NVDA", "MU"],
        },
        {
            "handle": "Benzinga",
            "name": "Benzinga Wire",
            "category": "us-stocks",
            "role_label": "媒体快讯",
            "focus": "NVDA breaking",
            "reliability_score": 99,
            "reliability_badge": "高胜率",
            "verified_calls": 50,
            "win_rate_20d": 90.0,
            "best_tickers": ["NVDA"],
        },
    ]

    matrix = build_asset_source_matrix(kol_scorecards=synthetic_cards)
    _assert_no_forbidden_recursive(matrix, "synthetic_matrix")

    us_ai = next(c for c in matrix["categories"] if c["id"] == "us_ai_semi")
    handles = [k["handle"] for k in us_ai["top_kols"]]
    assert "Benzinga" not in handles
    assert "RealChipPro" in handles
    assert "FakeHotHand" in handles
    # Verified KOL must rank ahead of unverified (<5 calls) KOL
    assert handles.index("RealChipPro") < handles.index("FakeHotHand")

    real_entry = next(k for k in us_ai["top_kols"] if k["handle"] == "RealChipPro")
    assert real_entry["win_rate_20d"] == 72.7
    assert real_entry["reliability_badge"] == "高胜率"

    fake_entry = next(k for k in us_ai["top_kols"] if k["handle"] == "FakeHotHand")
    assert fake_entry["win_rate_20d"] is None
    assert fake_entry["reliability_badge"] == "样本积累"


def test_custom_feeds_and_missing_chain_map_fallback(tmp_path: Path) -> None:
    custom_guru = [
        {
            "id": "semianalysis",
            "publisher": "Custom Semi",
            "author": "Custom Analyst",
            "background": "Track HBM supply",
            "html_url": "https://example.com/semi",
        }
    ]
    custom_rss = [
        {
            "id": "fed_press",
            "source": "Custom Fed",
            "html_url": "https://example.com/fed",
            "category": "macro",
            "tier": "T1",
            "why": "Track policy rates",
        }
    ]
    missing_chain = tmp_path / "does_not_exist.json"
    matrix = build_asset_source_matrix(
        kol_scorecards=[],
        guru_feeds=custom_guru,
        rss_feeds=custom_rss,
        chain_map_path=missing_chain,
    )
    _assert_no_forbidden_recursive(matrix, "fallback_matrix")
    assert matrix["summary"]["categories_count"] == 6
    assert matrix["summary"]["supply_chain_nodes"] == 0
    macro = next(c for c in matrix["categories"] if c["id"] == "macro_rates")
    assert any(v["name"] == "Custom Fed" for v in macro["primary_venues"])
    us_ai = next(c for c in matrix["categories"] if c["id"] == "us_ai_semi")
    assert any(g["publication"] == "Custom Semi" for g in us_ai["guru_longforms"])

