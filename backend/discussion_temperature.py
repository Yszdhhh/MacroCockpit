"""Bounded attention trends from comparable, public hotlist observations.

A mention's share of a board is a visibility proxy, not platform-wide post
volume or a price, positioning, sentiment, or trading signal.
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any, Callable

BJT = timezone(timedelta(hours=8))
LOOKBACK_DAYS = 120
PERIODS = (7, 30, 90)
TREND_METHOD = "paired-coverage-v2"
PLATFORMS = ("weibo", "baidu", "toutiao", "douyin", "zhihu", "bilibili-hot-search")
ASSETS = {
    "GOLD": ("黄金", r"黄金(?!周|时间|档|地段|比例|年龄|搭档|时代|海岸)|金价|金条|现货金|伦敦金|(?<![a-z0-9])(?:xauusd|paxg)(?![a-z0-9])"),
    "BTC": ("比特币", r"比特币|(?<![a-z0-9])(?:bitcoin|btc(?:usd[t]?)?)(?![a-z0-9])"),
    "QQQ": ("QQQ", r"(?<![a-z0-9])qqq(?![a-z0-9])"),
    "02513.HK": ("智谱", r"智谱|zhipu|(?<![a-z0-9])(?:0?2513\.hk|hk0?2513)(?![a-z0-9])"),
    "00100.HK": ("MiniMax", r"(?<![a-z0-9])(?:0{0,2}100\.hk|hk0{0,2}100)(?![a-z0-9])|(?=.*(?:模型|港股|市值|股价|财报|上市|融资|人工智能|(?:^|\W)AI(?:$|\W)|海螺))(?=.*(?<![a-z0-9])minimax(?![a-z0-9]))"),
}
ASSET_PATTERNS = {key: re.compile(pattern, re.I) for key, (_, pattern) in ASSETS.items()}
_CACHE_NAME = "discussion_temperature_cache.json"
_CACHE_VERSION = 2
_MIN_LIST_SIZE = 12
SNAPSHOT_TAGS = {
    "热度偏高": "当期关注较高", "散户过热": "当期看多分歧", "出圈见顶": "当期关注较高",
    "恐慌见底": "当期看空分歧",
}
_LIST_SIZE = 20


def _feed_date(payload: dict[str, Any], now: datetime) -> date | None:
    """Require a current board timestamp, including recent provider cache hits."""
    raw = payload.get("updatedTime")
    status = payload.get("status")
    if status not in ("success", "cache") or type(raw) not in (int, float):
        return None
    try:
        observed = datetime.fromtimestamp(raw / 1000, tz=BJT)
    except (OverflowError, OSError, ValueError):
        return None
    age = now - observed
    if observed.date() != now.date() or not timedelta(minutes=-2) <= age <= timedelta(minutes=30):
        return None
    return observed.date()


def daily_observations(feeds: dict[str, Any], now: datetime) -> dict[str, dict[str, Any]]:
    """Extract per-platform bounded denominators and independent matching titles."""
    now = now.astimezone(BJT)
    parsed: dict[str, dict[str, set[str]]] = {}
    for platform in PLATFORMS:
        raw = feeds.get(platform)
        if not isinstance(raw, dict) or raw.get("id") != platform or _feed_date(raw, now) is None:
            continue
        items = raw.get("items")
        if not isinstance(items, list):
            continue
        titles: dict[str, set[str]] = {}
        for item in items[:_LIST_SIZE]:
            title = item.get("title") if isinstance(item, dict) else None
            if not isinstance(title, str) or not title.strip():
                continue
            # Exact-copy dedup across boards, including punctuation/spacing edits.
            key = re.sub(r"[^\w]+", "", title[:200]).casefold()
            if key:
                titles[key] = {asset for asset, pattern in ASSET_PATTERNS.items() if pattern.search(title[:200])}
        if len(titles) >= _MIN_LIST_SIZE:
            parsed[platform] = titles

    # A copied title is not independent evidence for any of its platforms.
    appearances: dict[str, int] = {}
    for titles in parsed.values():
        for title in titles:
            appearances[title] = appearances.get(title, 0) + 1
    return {
        platform: {
            "sample_size": len(titles),
            "board_hits": {asset: sum(asset in refs for refs in titles.values()) for asset in ASSETS},
            "hits": {
                asset: sum(asset in refs and appearances[title] == 1 for title, refs in titles.items())
                for asset in ASSETS
            },
        }
        for platform, titles in parsed.items()
    }


def _load_cache(path: Path) -> dict[str, Any]:
    try:
        if path.stat().st_size > 512_000:
            return {}
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) and raw.get("version") == _CACHE_VERSION else {}
    except (OSError, ValueError):
        return {}


def _parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(value) if isinstance(value, str) and len(value) == 10 else None
    except ValueError:
        return None


def _retained_days(raw: Any, today: date) -> dict[str, Any]:
    cutoff = today - timedelta(days=LOOKBACK_DAYS - 1)
    return {
        key: row for key, row in (raw.items() if isinstance(raw, dict) else ())
        if (day := _parse_date(key)) is not None and cutoff <= day <= today and isinstance(row, dict)
    }


def refresh_discussion_history(
    data_dir: Path,
    fetch: Callable[[str], dict[str, Any]],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Sample at a fixed Beijing morning window; call under producer lock.

    One comparable snapshot per day. Retry missing boards no more than twice,
    at least 30 minutes apart. Do not turn failed days into zeros.
    """
    now = (now or datetime.now(BJT)).astimezone(BJT)
    today = now.date()
    key = today.isoformat()
    path = data_dir / _CACHE_NAME
    cache = _load_cache(path)
    history = _retained_days(cache.get("history"), today)
    previous = cache.get("attempt") if isinstance(cache.get("attempt"), dict) else {}
    attempt = previous if previous.get("date") == key else {}
    last = attempt.get("epoch", 0)
    tries = attempt.get("count", 0)
    tries = tries if type(tries) is int and 0 <= tries <= 3 else 0
    if (
        not 10 <= now.hour < 12
        or len(history.get(key, {})) == len(PLATFORMS)
        or tries >= 3
        or (isinstance(last, (int, float)) and now.timestamp() - last < 1800)
    ):
        return history

    feeds: dict[str, Any] = {}
    # Retry all boards as a cohort. Combining different retries would lose
    # same-time cross-platform deduplication and create noncomparable breadth.
    from backend.fred_and_market import atomic_write_json

    with ThreadPoolExecutor(max_workers=len(PLATFORMS)) as pool:
        pending = {platform: pool.submit(fetch, platform) for platform in PLATFORMS}
        for platform, future in pending.items():
            try:
                feeds[platform] = future.result()
            except Exception:
                continue
    # The request may finish after the scheduler timestamp (or an upstream
    # worker may be a few seconds ahead), handled by the 2-minute tolerance.
    samples = daily_observations(feeds, now)
    if len(samples) > len(history.get(key, {})):
        history[key] = samples
    atomic_write_json(path, {
        "version": _CACHE_VERSION,
        "history": history,
        "attempt": {"date": key, "epoch": now.timestamp(), "count": tries + 1},
    })
    return history


def discussion_trends(history: dict[str, Any], *, today: date | None = None) -> dict[str, Any]:
    """Summarize persistent warming; no price or one-day score enters this path."""
    today = today or datetime.now(BJT).date()
    days = _retained_days(history, today)
    result: dict[str, Any] = {"method": TREND_METHOD, "as_of": today.isoformat(), "assets": []}
    for asset in ASSETS:
        windows = []
        for period in PERIODS:
            start = today - timedelta(days=period - 1)
            early_len = period // 2
            later_len = period - early_len
            min_early = (early_len * 3 + 4) // 5
            min_late = (later_len * 3 + 4) // 5
            comparable = 0
            warming = 0
            cooling = 0
            high = 0
            counts = []
            deltas = []
            sources = []
            for platform in PLATFORMS:
                groups: tuple[list[tuple[float, float]], list[tuple[float, float]]] = ([], [])
                most_recent = -1
                last_level = (0.0, 0.0)
                for day_key, entries in days.items():
                    offset = (date.fromisoformat(day_key) - start).days
                    row = entries.get(platform)
                    if offset < 0 or not isinstance(row, dict):
                        continue
                    n, hits, board_hits = row.get("sample_size"), row.get("hits"), row.get("board_hits")
                    h = hits.get(asset) if isinstance(hits, dict) else None
                    board_h = board_hits.get(asset) if isinstance(board_hits, dict) else None
                    if (
                        type(n) is not int or not _MIN_LIST_SIZE <= n <= _LIST_SIZE
                        or type(h) is not int or type(board_h) is not int or not 0 <= h <= board_h <= n
                    ):
                        continue
                    level = (board_h / n, h / n)
                    if offset > most_recent:
                        most_recent, last_level = offset, level
                    groups[int(offset >= early_len)].append(level)
                early, late = groups
                if len(early) < min_early or len(late) < min_late or most_recent < period - 2:
                    continue
                comparable += 1
                counts.append(len(early) + len(late))
                base = tuple(median(series) for series in zip(*early))
                current = tuple(median(series) for series in zip(*late))
                changes = tuple(new - old for new, old in zip(current, base))
                deltas.append(changes)
                up_bar = tuple(max(0.08, old + 0.02) for old in base)
                down_bar = tuple(old - 0.02 for old in base)
                # Both series must corroborate on the SAME days. The
                # appearance/disappearance of a mirroring third board can
                # move the filtered series without changing local attention.
                up_days = sum(all(v >= bar for v, bar in zip(pair, up_bar)) for pair in late)
                down_days = sum(all(v <= bar for v, bar in zip(pair, down_bar)) for pair in late)
                source_state = "stable"
                if min(changes) >= 0.02 and up_days * 5 >= len(late) * 3 and all(
                    v >= bar for v, bar in zip(last_level, up_bar)
                ):
                    warming += 1
                    source_state = "warming"
                elif max(changes) <= -0.02 and down_days * 5 >= len(late) * 3 and all(
                    v <= bar for v, bar in zip(last_level, down_bar)
                ):
                    cooling += 1
                    source_state = "cooling"
                elif max(map(abs, changes)) < 0.02 and min(*base, *current, *last_level) >= 0.08:
                    high += 1
                    source_state = "high"
                sources.append({"platform": platform, "state": source_state, "sample_days": counts[-1]})
            state = "insufficient"
            if comparable >= 2:
                median_changes = tuple(median(series) for series in zip(*deltas))
                if warming >= 2 and cooling == 0 and min(median_changes) >= 0.02:
                    state = "warming"
                elif cooling >= 2 and warming == 0 and max(median_changes) <= -0.02:
                    state = "cooling"
                elif high >= 2 and warming == 0 and cooling == 0:
                    state = "high"
                else:
                    state = "stable"
            windows.append({
                "days": period, "state": state, "platforms": comparable,
                "sample_days": min(counts, default=0), "sources": sources,
            })
        result["assets"].append({"asset": asset, "windows": windows})
    return result


def project_discussion_trends(value: Any, *, today: date | None = None) -> dict[str, Any]:
    """Whitelist the compact public projection; never expose the history cache."""
    if not isinstance(value, dict) or value.get("method") != TREND_METHOD:
        return {}
    today = today or datetime.now(BJT).date()
    day = _parse_date(value.get("as_of"))
    if day is None or not 0 <= (today - day).days <= 1:
        return {}
    rows = value.get("assets")
    if not isinstance(rows, list):
        return {}
    assets = []
    seen: set[str] = set()
    for row in rows[:len(ASSETS)]:
        asset = row.get("asset") if isinstance(row, dict) else None
        if not isinstance(asset, str) or asset not in ASSETS or asset in seen:
            continue
        raw = row.get("windows")
        if not isinstance(raw, list):
            continue
        windows = []
        used: set[int] = set()
        for win in raw[:len(PERIODS)]:
            if not isinstance(win, dict):
                continue
            period, state, count, days = (win.get(k) for k in ("days", "state", "platforms", "sample_days"))
            if (
                type(period) is not int or period not in PERIODS or period in used
                or state not in ("insufficient", "warming", "cooling", "stable", "high")
                or type(count) is not int or not 0 <= count <= len(PLATFORMS)
                or type(days) is not int or not 0 <= days <= period
            ):
                continue
            early = period // 2
            min_days = (early * 3 + 4) // 5 + ((period - early) * 3 + 4) // 5
            sources = []
            source_ids: set[str] = set()
            raw_sources = win.get("sources")
            for source in raw_sources[:len(PLATFORMS)] if isinstance(raw_sources, list) else []:
                if not isinstance(source, dict):
                    continue
                platform, direction, covered = (source.get(k) for k in ("platform", "state", "sample_days"))
                if (
                    isinstance(platform, str) and platform in PLATFORMS and platform not in source_ids
                    and direction in ("warming", "cooling", "stable", "high")
                    and type(covered) is int and min_days <= covered <= period
                ):
                    source_ids.add(platform)
                    sources.append({"platform": platform, "state": direction, "sample_days": covered})
            # Derive the published breadth from validated independent platform rows.
            count = len(sources)
            days = min((source["sample_days"] for source in sources), default=0)
            source_states = [source["state"] for source in sources]
            conflicts = {"warming": {"cooling"}, "cooling": {"warming"}, "high": {"warming", "cooling"}}
            if count < 2:
                state = "insufficient"
            elif state in conflicts and (source_states.count(state) < 2 or conflicts[state].intersection(source_states)):
                state = "stable"
            used.add(period)
            windows.append({"days": period, "state": state, "platforms": count, "sample_days": days, "sources": sources})
        if windows:
            seen.add(asset)
            assets.append({"asset": asset, "windows": sorted(windows, key=lambda w: w["days"])})
    return {"method": TREND_METHOD, "as_of": day.isoformat(), "assets": assets} if assets else {}
