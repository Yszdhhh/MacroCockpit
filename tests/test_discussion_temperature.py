from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from backend.discussion_temperature import (
    ASSETS,
    BJT,
    PLATFORMS,
    daily_observations,
    discussion_trends,
    project_discussion_trends,
    refresh_discussion_history,
)
from backend.forum_sentiment import build_sentiment_divergence
from backend.public_snapshot import project_public_state


TODAY = date(2026, 11, 28)
NOW = datetime(2026, 11, 28, 10, 5, tzinfo=BJT)


def _feed(platform, *headlines, now=NOW, status="success"):
    fillers = [f"{platform} 本地考古研究 {i}" for i in range(20 - len(headlines))]
    return {
        "id": platform, "status": status, "updatedTime": now.timestamp() * 1000,
        "items": [{"title": title} for title in [*headlines, *fillers]],
    }


def _history(steps, *, platforms=PLATFORMS[:2], asset="GOLD"):
    days = [TODAY - timedelta(days=i) for i in reversed(range(len(steps)))]
    return {
        day.isoformat(): {
            platform: {
                "sample_size": 20,
                "hits": {symbol: count if symbol == asset else 0 for symbol in ASSETS},
            } for platform in platforms
        } for day, count in zip(days, steps) if count is not None
    }


def _window(history, days=7, asset="GOLD", *, today=TODAY):
    trends = discussion_trends(history, today=today)
    row = next(item for item in trends["assets"] if item["asset"] == asset)
    return next(w for w in row["windows"] if w["days"] == days)


def test_prices_and_stock_rank_are_not_community_attitude():
    # Old cache rows converted % price moves to extreme "retail bullishness".
    # Prices alone must now leave the community measure unavailable, not neutral.
    quote_rows = [
        {"channel": "eastmoney", "title": f"东财热股 #{i} +8%", "heat": 90, "score": 80,
         "topics": ["A股与港股"]}
        for i in range(4)
    ]
    quote_rows.append(
        {"channel": "xueqiu", "title": "雪球热股 #1 某股 +10%", "heat": 95, "score": 85,
         "topics": ["A股与港股"]}
    )
    payload = build_sentiment_divergence({"items": quote_rows})
    row = next(d for d in payload["divergences"] if d["topic"] == "A股与港股")
    assert row["desk_count"] == row["street_count"] == 0
    assert row["street_score"] is row["street_heat"] is None
    assert row["divergence_tag"] == "暂无样本"
    assert "过热" not in json.dumps(payload, ensure_ascii=False)


def test_snapshot_deduplicates_and_never_assumes_missing_is_zero():
    feeds = {
        "weibo": _feed("weibo", "黄金大涨！", "黄金大涨", "金价突破后续观察", "黄金周旅游避峰"),
        "baidu": _feed("baidu", "黄金大涨", "智谱模型上线", "MiniMax 模型上新", "minimax 算法作业"),
        "zhihu": _feed("zhihu", "比特币", now=NOW - timedelta(days=1), status="cache"),
        "douyin": _feed("douyin", now=NOW - timedelta(hours=1)),
        "toutiao": _feed("toutiao", now=NOW - timedelta(minutes=20), status="cache"),
        "bilibili-hot-search": {"id": "bilibili-hot-search", "status": "success", "items": []},
    }
    observations = daily_observations(feeds, NOW)
    assert set(observations) == {"weibo", "baidu", "toutiao"}
    assert observations["weibo"]["sample_size"] == 19  # punctuation copy collapsed
    assert observations["weibo"]["hits"]["GOLD"] == 1  # copied cross-feed headline excluded
    assert observations["baidu"]["hits"]["GOLD"] == 0  # actual observed board, not an outage
    assert observations["baidu"]["hits"]["02513.HK"] == 1
    assert observations["baidu"]["hits"]["00100.HK"] == 1  # not the algorithm
    assert observations["baidu"]["hits"]["QQQ"] == 0


def test_single_day_burst_or_one_platform_is_not_sustained():
    assert _window(_history([0] * 6 + [18]))["state"] == "stable"
    assert _window(_history([0] * 3 + [4] * 4, platforms=("weibo",)))["state"] == "insufficient"
    national = _history([0] * 3 + [4] * 4)
    for platform_day in national.values():
        platform_day["baidu"]["hits"]["GOLD"] = 0
    assert _window(national)["state"] == "stable"


def test_persistence_high_flat_cooling_asset_isolation_and_stale():
    for period in (7, 30, 90):
        low_days = period // 2
        hot_days = period - low_days
        rising = _history([0] * low_days + [4] * hot_days)
        assert _window(rising, period)["state"] == "warming"
        assert _window(rising, period, asset="BTC")["state"] == "stable"
        assert _window(_history([4] * period), period)["state"] == "high"
        assert _window(_history([4] * low_days + [0] * hot_days), period)["state"] == "cooling"
        assert _window(rising, period, today=TODAY + timedelta(days=2))["state"] == "insufficient"
    assert _window(_history([None, None, 0, 4, 4, 4, 4]))["state"] == "insufficient"
    assert _window(_history([0, 0, 0, 4, 4, 4, 0]))["state"] == "stable"


def test_daily_cache_is_idempotent_bounded_and_does_not_store_raw_posts(tmp_path):
    calls = []

    def fetch(platform):
        calls.append(platform)
        if platform == "zhihu":
            raise TimeoutError
        return _feed(platform, f"{platform} 现货金走势")

    first = refresh_discussion_history(tmp_path, fetch, now=NOW)
    assert len(calls) == len(PLATFORMS)
    assert "zhihu" not in first[TODAY.isoformat()]
    again = refresh_discussion_history(tmp_path, fetch, now=NOW + timedelta(minutes=5))
    assert again == first  # same day, same bucket, no extra observations
    assert len(calls) == len(PLATFORMS)
    at_night = refresh_discussion_history(tmp_path, fetch, now=NOW + timedelta(hours=4))
    assert at_night == first
    after = refresh_discussion_history(tmp_path, fetch, now=NOW + timedelta(days=1))
    assert not (TODAY + timedelta(days=1)).isoformat() in after  # stale transport response
    assert len(calls) == 2 * len(PLATFORMS)
    cache = tmp_path / "discussion_temperature_cache.json"
    assert "现货金走势" not in cache.read_text()
    retained = refresh_discussion_history(tmp_path, fetch, now=NOW + timedelta(days=120, hours=4))
    assert retained == {}


def test_whitelisted_public_and_offline_projection_preserves_no_raw_data():
    # Use the projection clock's current date so saved fixtures do not age out.
    today = datetime.now(BJT).date()
    history = {
        (today - timedelta(days=6-i)).isoformat(): row
        for i, row in enumerate(_history([0, 0, 0, 3, 3, 3, 3]).values())
    }
    trend = discussion_trends(history, today=today)
    trend["raw_posts"] = ["not a public aggregate"]
    trend["assets"][0]["windows"][0]["sources"][0]["hidden_text"] = "not a public aggregate"
    full = {
        "portfolio": {"secret": "nope"},
        "journal": {"private": "nope"},
        "forum_sentiment": {
            "discussion_trends": trend, "hidden_history": history,
            "divergences": [
                {"topic": "宏观", "divergence_tag": "散户过热", "tone": "warn", "action_hint": "立即减仓"},
                {"topic": "黄金", "divergence_tag": "恐慌见底", "tone": "bull", "action_hint": "立即买入"},
            ],
        },
    }
    projected = project_public_state(full)
    assert "portfolio" not in projected and "journal" not in projected
    forum = projected["forum_sentiment"]
    assert "hidden_history" not in forum
    assert forum["discussion_trends"]["assets"][0]["windows"][0]["state"] == "warming"
    serialized = json.dumps(forum, ensure_ascii=False)
    for token in ("raw_posts", "hidden_text", "见底", "过热", "立即减仓", "立即买入"):
        assert token not in serialized
    assert serialized.count("当期") == 4
    assert project_discussion_trends(trend, today=today + timedelta(days=2)) == {}
    assert not project_discussion_trends(trend, today=today - timedelta(days=1))
