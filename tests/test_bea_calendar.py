from datetime import datetime, timezone

from backend.calendar_events import build_calendar_events, filter_calendar_events
from backend.mcp_server import create_mcp_dispatcher
from backend.public_snapshot import publish_generation


NOW = datetime(2026, 11, 23, 13, 30, tzinfo=timezone.utc)
RELEASE_DAY = "2026-11-25"


def test_bea_release_pair_uses_exact_official_time_and_reference_periods():
    events = build_calendar_events(now_utc=NOW)
    events = filter_calendar_events(events, start_date=RELEASE_DAY, end_date=RELEASE_DAY, now_utc=NOW)
    bea = {ev["event_type"]: ev for ev in events if ev["source"] == "BEA"}
    assert set(bea) == {"pce", "gdp"}
    assert bea["pce"]["title"] == "美国 2026年10月 PCE 物价指数公布"
    assert bea["gdp"]["title"] == "美国 2026年第三季度 GDP 第二次估算及企业利润公布"
    for event_type, ev in bea.items():
        assert ev["id"] == f"cal-{event_type}-{RELEASE_DAY}"
        assert ev["precision"] == "exact"
        assert ev["event_utc"] == f"{RELEASE_DAY}T13:30:00Z"
        assert ev["time_local"] == ev["time_bjt"] == f"{RELEASE_DAY} 21:30"
        assert ev["days_until"] == 2 and ev["hours_until"] == 48.0
        assert ev["source_url"] == "https://www.bea.gov/news/schedule"


def test_bea_next_year_handles_daylight_saving_and_profit_release_stage():
    lookup = {ev["id"]: ev for ev in build_calendar_events(now_utc=NOW, tz_offset_hours=0)}
    assert "企业利润" not in lookup["cal-gdp-2027-02-25"]["title"]
    assert "企业利润" in lookup["cal-gdp-2027-03-31"]["title"]
    pce = lookup["cal-pce-2027-03-31"]
    assert pce["event_utc"] == "2027-03-31T12:30:00Z"
    assert pce["time_local"] == "2027-03-31 12:30"
    assert pce["time_bjt"] == "2027-03-31 20:30"


def test_bea_events_survive_publication_and_can_be_read_via_mcp(tmp_path):
    events = build_calendar_events({"AAPL": {"earnings_date": RELEASE_DAY}}, now_utc=NOW)
    publish_generation(tmp_path, {"calendar_events": events}, generation=1)
    dispatcher = create_mcp_dispatcher(store=None, base_dir=tmp_path)

    def dispatch(method, **params):
        status, response = dispatcher.dispatch_jsonrpc(
            {"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            role="READ_ONLY",
            actor_id="calendar-reader",
        )
        assert status == 200 and response is not None
        return response["result"]

    calendar_tool = next(tool for tool in dispatch("tools/list")["tools"] if tool["name"] == "calendar_list")
    assert {"pce", "gdp"} <= set(calendar_tool["inputSchema"]["properties"]["type"]["enum"])

    def calendar(event_type, **filters):
        result = dispatch("tools/call", name="calendar_list", arguments={
            "type": event_type, "from": RELEASE_DAY, "to": RELEASE_DAY, **filters,
        })
        assert not result["isError"]
        assert result["structuredContent"]["generation"] == 1
        return result["structuredContent"]["events"]

    assert {ev["event_type"] for ev in calendar("all")} == {"earnings", "pce", "gdp"}
    assert {ev["event_type"] for ev in calendar("macro")} == {"pce", "gdp"}
    assert len(calendar("pce", symbol="QQQ")) == 1
    assert calendar("gdp", symbol="AAPL") == []
    assert calendar("gdp")[0]["event_utc"] == f"{RELEASE_DAY}T13:30:00Z"
    assert calendar("earnings")[0]["precision"] == "date"
