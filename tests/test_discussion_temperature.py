from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta

from backend.discussion_temperature import (
    ASSETS,
    BJT,
    PLATFORMS,
    TREND_METHOD,
    daily_observations,
    discussion_trends,
    load_valid_trends_from_cache,
    project_discussion_trends,
    refresh_discussion_history,
)
from backend.forum_sentiment import build_sentiment_divergence
from backend.public_snapshot import project_public_state, publish_generation


TODAY = date(2026, 11, 28)
NOW = datetime(2026, 11, 28, 10, 5, tzinfo=BJT)


def _feed(platform, *headlines, now=NOW, status="success"):
    fillers = [f"{platform} 本地考古研究 {i}" for i in range(30 - len(headlines))]
    return {
        "id": platform,
        "status": status,
        "updatedTime": now.timestamp() * 1000,
        "items": [{"title": title} for title in [*headlines, *fillers]],
    }


def _history(steps, *, platforms=PLATFORMS[:2], asset="GOLD", sample_size=30, include_hashes=True):
    days = [TODAY - timedelta(days=i) for i in reversed(range(len(steps)))]
    hist = {}
    for idx, (day, count) in enumerate(zip(days, steps)):
        if count is None:
            continue
        day_row = {}
        for p_idx, platform in enumerate(platforms):
            hashes = (
                {
                    asset: [
                        hashlib.sha1(f"{platform}_{asset}_{idx}_{j}".encode("utf-8")).hexdigest()[:10]
                        for j in range(count)
                    ]
                }
                if (include_hashes and count > 0)
                else {}
            )
            day_row[platform] = {
                "sample_size": sample_size,
                "board_hits": {symbol: count if symbol == asset else 0 for symbol in ASSETS},
                "hits": {symbol: count if symbol == asset else 0 for symbol in ASSETS},
                **({"hashes": hashes} if include_hashes else {}),
            }
        hist[day.isoformat()] = day_row
    return hist


def _window(history, days=7, asset="GOLD", *, today=TODAY):
    trends = discussion_trends(history, today=today)
    row = next(item for item in trends["assets"] if item["asset"] == asset)
    return next(w for w in row["windows"] if w["days"] == days)


def test_prices_and_stock_rank_are_not_community_attitude_but_keep_popularity():
    # Old cache rows converted % price moves to extreme "retail bullishness".
    # Prices alone must leave street_score unavailable, while remaining visible in hot_discussions as 人气榜.
    quote_rows = [
        {
            "platform": "东财股吧",
            "channel": "eastmoney",
            "title": f"东财热股 #{i} +8%",
            "kind": "quote_rank",
            "sentiment": "人气榜",
            "heat": 90,
            "score": 80,
            "topics": ["A股与港股"],
            "url": f"https://guba.eastmoney.com/list,00000{i}.html",
        }
        for i in range(4)
    ]
    quote_rows.append(
        {
            "platform": "雪球",
            "channel": "xueqiu",
            "title": "雪球热股 #1 某股 +10%",
            "kind": "quote_rank",
            "sentiment": "人气榜",
            "heat": 95,
            "score": 85,
            "topics": ["A股与港股"],
            "url": "https://xueqiu.com/S/TEST",
        }
    )
    payload = build_sentiment_divergence({"items": quote_rows})
    row = next(d for d in payload["divergences"] if d["topic"] == "A股与港股")
    assert row["desk_count"] == row["street_count"] == 0
    assert row["street_score"] is row["street_heat"] is None
    assert row["divergence_tag"] == "暂无样本"
    assert any(h.get("sentiment") == "人气榜" for h in payload["hot_discussions"])
    assert "过热" not in json.dumps(payload, ensure_ascii=False)


def test_snapshot_tracks_five_public_categories_and_dedups_safely():
    feeds = {
        "weibo": _feed("weibo", "黄金大涨！", "黄金大涨", "金价创年内新高", "上证指数放量上涨", "黄金周旅游避峰"),
        "baidu": _feed("baidu", "黄金大涨", "英伟达股价走强", "美联储降息预期升温", "比特币大涨"),
        "zhihu": _feed("zhihu", "比特币", now=NOW - timedelta(days=1), status="cache"),
        "douyin": _feed("douyin", now=NOW - timedelta(hours=1)),
        "toutiao": _feed("toutiao", now=NOW - timedelta(minutes=20), status="cache"),
        "bilibili-hot-search": {"id": "bilibili-hot-search", "status": "success", "items": []},
    }
    observations = daily_observations(feeds, NOW)
    assert set(observations) == {"weibo", "baidu", "toutiao"}
    assert observations["weibo"]["sample_size"] == 29  # punctuation copy collapsed out of 30 items
    assert observations["weibo"]["board_hits"]["GOLD"] == 2  # excludes 黄金周
    assert observations["weibo"]["board_hits"]["ASHARE_HK"] == 1
    assert observations["weibo"]["hits"]["GOLD"] == 1
    assert observations["baidu"]["board_hits"]["GOLD"] == 1
    assert observations["baidu"]["hits"]["GOLD"] == 0
    assert observations["baidu"]["hits"]["US_AI"] == 1
    assert observations["baidu"]["hits"]["FED_MACRO"] == 1
    assert observations["baidu"]["hits"]["BTC"] == 1


def test_realistic_general_hotlist_breakout_and_verbatim_copy_guard():
    # A realistic breakout (0 -> 1 hotlist slot out of 30 = +3.33% across 2 boards with distinct headlines) triggers warming
    assert _window(_history([0] * 3 + [1] * 4))["state"] == "warming"
    # Single-day massive burst is still NOT sustained
    assert _window(_history([0] * 6 + [10]))["state"] == "stable"
    # One platform alone is insufficient
    assert _window(_history([0] * 3 + [2] * 4, platforms=("weibo",)))["state"] == "insufficient"

    # Verbatim copy-pasted identical headline hash across 2 boards must NOT falsely trigger warming
    verbatim = _history([0] * 3 + [1] * 4)
    same_hash = hashlib.sha1("同一条转发通稿".encode("utf-8")).hexdigest()[:10]
    for day in verbatim.values():
        for row in day.values():
            if row["board_hits"]["GOLD"] > 0:
                row["hits"]["GOLD"] = 0
                row["hashes"] = {"GOLD": [same_hash]}
    assert _window(verbatim)["state"] == "stable"


def test_persistence_high_flat_cooling_asset_isolation_and_stale():
    for period in (7, 30, 90):
        low_days = period // 2
        hot_days = period - low_days
        rising = _history([0] * low_days + [2] * hot_days)
        assert _window(rising, period)["state"] == "warming"
        assert _window(rising, period, asset="BTC")["state"] == "stable"
        assert _window(_history([3] * period), period)["state"] == "high"
        assert _window(_history([2] * low_days + [0] * hot_days), period)["state"] == "cooling"
        assert _window(rising, period, today=TODAY + timedelta(days=2))["state"] == "insufficient"
    assert _window(_history([None, None, 0, 2, 2, 2, 2]))["state"] == "insufficient"
    assert _window(_history([0, 0, 0, 2, 2, 2, 0]))["state"] == "stable"


def test_mirror_board_disappearing_or_returning_does_not_change_attention():
    weibo_titles = ("黄金市场甲方观察", "金价亚洲早盘简报")
    baidu_titles = ("黄金投资乙方访谈", "金条门店行情交流")

    def simulate(mirror_present, rising=False):
        history = {}
        for day, copies_present in enumerate(mirror_present):
            ts = NOW - timedelta(days=6 - day)
            w_t = weibo_titles if (not rising or day >= 3) else ()
            b_t = baidu_titles if (not rising or day >= 3) else ()
            feeds = {
                "weibo": _feed("weibo", *w_t, now=ts),
                "baidu": _feed("baidu", *b_t, now=ts),
            }
            if copies_present:
                feeds["zhihu"] = _feed("zhihu", *w_t, *b_t, now=ts)
            history[ts.date().isoformat()] = daily_observations(feeds, ts)
        return _window(history)

    # Flat discussion stays stable even when 3rd mirror board joins or disappears
    for observed in (simulate([True] * 3 + [False] * 4), simulate([False] * 3 + [True] * 4)):
        assert observed["state"] == "stable"
        assert observed["platforms"] == 2
        assert all(row["state"] == "stable" for row in observed["sources"])

    # Genuine rising discussion on weibo & baidu stays warming even if 3rd mirror board appears mid-week
    rising_with_mirror = simulate([False] * 3 + [True] * 4, rising=True)
    assert rising_with_mirror["state"] == "warming"


def test_24h_four_slot_sampling_and_no_raw_post_leak(tmp_path):
    calls = []

    def fetch(platform):
        calls.append(platform)
        if platform == "zhihu":
            raise TimeoutError
        return _feed(platform, f"{platform} 现货金走势")

    # Morning slot (10:05) succeeds
    morning = NOW
    first = refresh_discussion_history(tmp_path, fetch, now=morning)
    assert len(calls) == len(PLATFORMS)
    assert "slots" in first[TODAY.isoformat()]
    assert "1" in first[TODAY.isoformat()]["slots"]
    assert "zhihu" not in first[TODAY.isoformat()]["slots"]["1"]

    # Same slot within 30m is throttled
    again = refresh_discussion_history(tmp_path, fetch, now=morning + timedelta(minutes=5))
    assert again == first
    assert len(calls) == len(PLATFORMS)

    # Nighttime slot (22:15 BJT -> slot 3) captures US/crypto night session too
    night = datetime(2026, 11, 28, 22, 15, tzinfo=BJT)
    with_night = refresh_discussion_history(
        tmp_path,
        lambda p: _feed(p, f"{p} 美股黄金夜盘", now=night),
        now=night,
    )
    assert "3" in with_night[TODAY.isoformat()]["slots"]
    cache = tmp_path / "discussion_temperature_cache.json"
    raw_blob = cache.read_text(encoding="utf-8")
    assert json.loads(raw_blob)["version"] == 3
    assert "现货金走势" not in raw_blob and "美股黄金夜盘" not in raw_blob


def test_cache_v1_rejected_and_malformed_rows_blocked(tmp_path):
    history = _history([0] * 3 + [2] * 4)
    cache = tmp_path / "discussion_temperature_cache.json"
    cache.write_text(json.dumps({"version": 1, "history": history}), encoding="utf-8")
    assert load_valid_trends_from_cache(tmp_path, today=TODAY) == {}

    malformed = _history([0] * 3 + [2] * 4)
    for obs in malformed.values():
        obs["weibo"]["board_hits"]["GOLD"] = 31  # exceeds sample size
    assert _window(malformed)["state"] == "insufficient"


def test_public_projection_and_publish_generation_preserve_server_trends(tmp_path):
    today = datetime.now(BJT).date()
    raw_hist = _history([0, 0, 0, 2, 2, 2, 2])
    shifted_hist = {
        (today - timedelta(days=6 - i)).isoformat(): row
        for i, row in enumerate(raw_hist.values())
    }
    trend = discussion_trends(shifted_hist, today=today)
    trend["raw_posts"] = ["not a public aggregate"]
    trend["assets"][0]["windows"][0]["board_hits"] = {"ASHARE_HK": 7}
    full = {
        "portfolio": {"secret": "nope"},
        "journal": {"private": "nope"},
        "forum_sentiment": {
            "discussion_trends": trend,
            "hidden_history": shifted_hist,
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
    assert forum["discussion_trends"]["method"] == TREND_METHOD
    gold_tr = next(a for a in forum["discussion_trends"]["assets"] if a["asset"] == "GOLD")
    assert gold_tr["windows"][0]["state"] == "warming"
    serialized = json.dumps(forum, ensure_ascii=False)
    for token in ("raw_posts", "board_hits", "hidden_text", "见底", "过热", "立即减仓", "立即买入"):
        assert token not in serialized
    assert project_discussion_trends(trend, today=today + timedelta(days=2)) == {}
    assert not project_discussion_trends(trend, today=today - timedelta(days=1))

    # Stateless remote publish (without trends) must preserve server-side discussion_temperature_cache.json trends
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    (data_dir / "discussion_temperature_cache.json").write_text(
        json.dumps({"version": 3, "history": shifted_hist}), encoding="utf-8"
    )
    manifest = publish_generation(
        tmp_path,
        {
            "risk_gate": {"light": "GREEN", "long_risk_cap": 1.0, "label": "绿灯"},
            "signals": [],
            "forum_sentiment": {"overall_crowd_temp": 55, "divergences": [], "hot_discussions": []},
        },
        [],
        producer_id="windows-primary",
    )
    gen_state = json.loads((data_dir / f"generations/gen_{manifest['generation']}/state.json").read_text("utf-8"))
    preserved = gen_state["forum_sentiment"]["discussion_trends"]
    assert preserved["method"] == TREND_METHOD
    p_gold = next(a for a in preserved["assets"] if a["asset"] == "GOLD")
    assert p_gold["windows"][0]["state"] == "warming"


def test_120_day_four_slot_compact_cache_never_resets(tmp_path):
    """Verify compact cache & aged-hash pruning keep 120 full days (< 8MB limit) without losing history."""
    base_day = NOW - timedelta(days=119)
    for d in range(120):
        day_dt = base_day + timedelta(days=d)
        for slot in range(4):
            ts = day_dt.replace(hour=slot * 6 + 1, minute=10)
            history = refresh_discussion_history(
                tmp_path,
                lambda p, _ts=ts, _d=d: _feed(
                    p, f"黄金走势第{_d}日", f"美股英伟达动态{_d}", f"A股沪指成交{_d}", now=_ts
                ),
                now=ts,
            )
    assert len(history) == 120
    cache_file = tmp_path / "discussion_temperature_cache.json"
    assert cache_file.stat().st_size < 1_000_000
    old_row = history[base_day.date().isoformat()]["slots"]["0"]["weibo"]
    assert "hashes" not in old_row
    trends = discussion_trends(history, today=NOW.date())
    gold_90d = next(w for a in trends["assets"] if a["asset"] == "GOLD" for w in a["windows"] if w["days"] == 90)
    assert gold_90d["sample_days"] == 90


def test_v2_cache_migration_and_daily_mirrored_copy_guard(tmp_path):
    """PR #2 v2 cache migrates cleanly (including QQQ & same-day incremental merge); daily-varying copy-paste stays stable."""
    day = (NOW - timedelta(days=1)).date().isoformat()
    today_key = NOW.date().isoformat()
    v2_cache = {
        "version": 2,
        "history": {
            day: {"weibo": {"sample_size": 30, "hits": {"GOLD": 2, "FED": 1, "QQQ": 1}, "board_hits": {"GOLD": 2, "FED": 1, "QQQ": 1}}},
            today_key: {
                plat: {"sample_size": 30, "hits": {"GOLD": 1, "QQQ": 1}, "board_hits": {"GOLD": 1, "QQQ": 1}}
                for plat in PLATFORMS
            },
        },
    }
    p = tmp_path / "discussion_temperature_cache.json"
    p.write_text(json.dumps(v2_cache), encoding="utf-8")
    hist_after = refresh_discussion_history(
        tmp_path,
        lambda plat: _feed(plat, "微博单平台新增", now=NOW) if plat == "weibo" else {},
        now=NOW,
    )
    assert day in hist_after
    assert hist_after[day]["weibo"]["board_hits"]["FED_MACRO"] == 1
    assert hist_after[day]["weibo"]["hits"]["US_AI"] == 1
    # Same-day v2 platforms other than weibo must stay preserved when only weibo succeeds
    today_slots = hist_after[today_key]["slots"]
    preserved_plats = {pk for slot_row in today_slots.values() for pk in slot_row.keys()}
    assert preserved_plats == set(PLATFORMS)
    assert json.loads(p.read_text("utf-8"))["version"] == 3

    # Daily-varying copy-pasted titles identical on weibo & baidu across days 3..6 must stay stable
    copied = _history([0] * 3 + [1] * 4, platforms=("weibo", "baidu"))
    for idx, day_k in enumerate(sorted(copied.keys())):
        if idx >= 3:
            h = hashlib.sha1(f"daily-copy-{idx}".encode("utf-8")).hexdigest()[:10]
            for plat in ("weibo", "baidu"):
                copied[day_k][plat]["hits"]["GOLD"] = 0
                copied[day_k][plat]["hashes"] = {"GOLD": [h]}
    assert _window(copied)["state"] == "stable"
