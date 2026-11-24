from __future__ import annotations

from unittest.mock import Mock

import pytest

from backend import cloud_collector, fred_and_market


# BLS all-items CPI-U, not seasonally adjusted: August, September and October 2026
# releases. October 2025 was not published, so the 337.511 October index has no YoY.
CPI_NSA = [
    ("2025-08-01", 323.976),
    ("2025-09-01", 324.800),
    ("2026-08-01", 334.980),
    ("2026-09-01", 336.940),
    ("2026-10-01", 337.511),
]


def test_missing_october_base_displays_dated_september_yoy():
    item = fred_and_market._build_cpi_yoy_item(list(reversed(CPI_NSA)))

    assert item is not None
    assert item["as_of"] == item["market_at"] == "2026-09-01"
    assert item["latest_observation_as_of"] == "2026-10-01"
    assert item["latest_yoy_available"] is False
    assert item["value_display"] == "3.7%（2026-09）"
    assert item["raw_value"] == pytest.approx(3.7377)
    assert item["change_num"] == pytest.approx(0.3411)
    assert item["change_display"] == "+0.34 个百分点"
    assert "2026-10 同比不可用" in item["note"]
    assert "2026-09 为未季调数据" in item["note"]
    assert "337.511" not in item["note"]


def test_yoy_recovers_without_faking_change_over_a_gap():
    series = CPI_NSA + [("2025-11-01", 325.0), ("2026-11-01", 338.0)]
    item = fred_and_market._build_cpi_yoy_item(series)

    assert item is not None
    assert item["as_of"] == item["latest_observation_as_of"] == "2026-11-01"
    assert item["latest_yoy_available"] is True
    assert item["value_display"] == "4.0%"
    assert item["change_display"] == ""
    assert item["change_num"] is None


@pytest.mark.parametrize("bad_base", [0, -42, None, float("nan"), True])
def test_only_positive_finite_exact_same_month_base_can_populate_yoy(bad_base):
    series = [
        ("2025-09-01", 324.8), ("2025-10-01", bad_base), ("2025-11-01", 325.0),
        ("2026-10-01", 337.511),
    ]

    assert fred_and_market._build_cpi_yoy_item(series) is None


def test_legacy_adjusted_cache_and_parquet_cannot_seed_nsa(monkeypatch):
    monkeypatch.setattr(fred_and_market, "_load_fred_cache", lambda: {
        "CPI": [["2025-09-01", 324.368], ["2026-09-01", 336.426]],
        **{key: [["2026-11-01", 1]] for key in ("WALCL", "RRPONTSYD", "WTREGEN")},
    })
    parquet = Mock(return_value=[])
    monkeypatch.setattr(fred_and_market, "_read_parquet_series", parquet)

    bundle = fred_and_market._load_macro_series_bundle()

    assert bundle[fred_and_market.CPI_NSA_KEY] == []
    assert "CPI" not in bundle
    assert all(call.args[0] != "CPI.parquet" for call in parquet.call_args_list)


def test_local_and_overseas_collectors_use_the_new_nsa_cache_key(monkeypatch):
    fetch = Mock(side_effect=lambda sid, **kw: CPI_NSA if sid == "CPIAUCNS" else [])
    for module in (fred_and_market, cloud_collector):
        monkeypatch.setattr(module, "_load_fred_api_key", lambda: None)
        monkeypatch.setattr(module, "fetch_fred_observations", fetch)
    monkeypatch.setattr(fred_and_market, "_load_fred_cache", lambda: {"CPI": [["2025-10-01", 333]]})
    monkeypatch.setattr(fred_and_market, "_save_fred_cache", lambda _: None)
    monkeypatch.setattr(cloud_collector, "_fetch_gold_tail", lambda: [])
    monkeypatch.setattr(cloud_collector, "fetch_btc_binance_daily", lambda _: [])

    key = fred_and_market.CPI_NSA_KEY
    assert fred_and_market.refresh_fred_cache(include_gold=False) == {key: CPI_NSA}
    assert cloud_collector._fetch_intl_fred_series() == {key: [list(p) for p in CPI_NSA]}
    assert [call.kwargs["limit"] for call in fetch.call_args_list if call.args == ("CPIAUCNS",)] == [48, 48]
    assert all(call.args != ("CPIAUCSL",) for call in fetch.call_args_list)
