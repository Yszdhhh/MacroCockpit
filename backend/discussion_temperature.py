"""Bounded multi-day attention trends from comparable public hotlist observations.

A mention's share of a board is a visibility proxy, not platform-wide post
volume or a price, positioning, sentiment, or trading signal.
"""
from __future__ import annotations

import hashlib
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
TREND_METHOD = "paired-coverage-v3"
PLATFORMS = ("weibo", "baidu", "toutiao", "douyin", "zhihu", "bilibili-hot-search")
ASSETS: dict[str, tuple[str, str]] = {
    "ASHARE_HK": (
        "A股与港股",
        r"(?<![a-z0-9])(?:a股|港股|沪指|上证(?:指数)?|深成指|创业板(?:指)?|科创50|恒生(?:指数|科技)|恒指|"
        r"沪深(?:两市|300)|两市成交|北向资金|南向资金|牛市|涨停潮|券商股|中概股)(?![a-z0-9])",
    ),
    "US_AI": (
        "美股AI与科技",
        r"(?:美股|纳斯达克|纳指|标普500|道琼斯|道指|华尔街|英伟达|黄仁勋|特斯拉|苹果股价|微软股价|大模型|"
        r"人工智能|算力|芯片股|光刻机|半导体)|"
        r"(?<![a-z0-9])(?:nvda|qqq|spy|openai|chatgpt|deepseek|claude|anthropic|tsla|aapl|msft)(?![a-z0-9])",
    ),
    "GOLD": (
        "黄金与贵金属",
        r"黄金(?!周|时间|档|地段|比例|年龄|搭档|时代|海岸|联赛|左轮|矿工|分割|瞳|蟒|甲)|"
        r"金价|金条|足金|金饰|现货金|伦敦金|沪金|国际金价|(?<![a-z0-9])(?:xauusd|paxg|comex\s*gold)(?![a-z0-9])",
    ),
    "BTC": (
        "比特币与加密",
        r"比特币|以太坊|加密货币|虚拟货币|数字货币|币圈|爆仓|稳定币|"
        r"(?<![a-z0-9])(?:bitcoin|btc(?:usd[t]?)?|eth(?:ereum)?|coinbase)(?![a-z0-9])",
    ),
    "FED_MACRO": (
        "美联储与宏观",
        r"美联储|鲍威尔|降息|加息|美债|国债收益率|非农(?:就业)?|通胀|关税|贸易战|"
        r"央行(?:降准|降息|逆回购|买断)|人民币汇率|美元指数|(?<![a-z0-9])(?:fomc|cpi|gdp|pce)(?![a-z0-9])",
    ),
}
ASSET_PATTERNS = {key: re.compile(pattern, re.I) for key, (_, pattern) in ASSETS.items()}
_CACHE_NAME = "discussion_temperature_cache.json"
_CACHE_VERSION = 3
_MAX_CACHE_BYTES = 8 * 1024 * 1024
_HASH_RETENTION_DAYS = 50
_MIN_LIST_SIZE = 12
_LIST_SIZE = 30
_SLOTS_PER_DAY = 4
_MIN_ACTIVE_SHARE = 1.0 / _LIST_SIZE  # ~3.33% (>= 1 slot on a 30-item general hotlist)
_HIGH_SHARE = 3.0 / _LIST_SIZE  # 10.0% (>= 3 slots on a 30-item general hotlist)
_MIN_DELTA = 0.025  # +2.5 percentage points (~+1 slot jump vs baseline)

SNAPSHOT_TAGS = {
    "热度偏高": "关注较高",
    "当期关注较高": "关注较高",
    "散户过热": "看多分歧",
    "当期看多分歧": "看多分歧",
    "出圈见顶": "关注较高",
    "恐慌见底": "看空分歧",
    "当期看空分歧": "看空分歧",
    "机构抢跑": "专业偏强",
    "专业倾向较强": "专业偏强",
    "仅社区样本": "仅社区帖",
}


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
    """Extract per-platform bounded denominators, independent hits, and anonymized title hashes."""
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
            key = re.sub(r"[^\w]+", "", title[:200]).casefold()
            if key:
                titles[key] = {asset for asset, pattern in ASSET_PATTERNS.items() if pattern.search(title[:200])}
        if len(titles) >= _MIN_LIST_SIZE:
            parsed[platform] = titles

    appearances: dict[str, int] = {}
    for titles in parsed.values():
        for title in titles:
            appearances[title] = appearances.get(title, 0) + 1

    out: dict[str, dict[str, Any]] = {}
    for platform, titles in parsed.items():
        hashes_by_asset: dict[str, list[str]] = {}
        for asset in ASSETS:
            matched_hashes = sorted(
                {
                    hashlib.sha1(t_key.encode("utf-8")).hexdigest()[:10]
                    for t_key, refs in titles.items()
                    if asset in refs
                }
            )[:6]
            if matched_hashes:
                hashes_by_asset[asset] = matched_hashes
        row_entry: dict[str, Any] = {
            "sample_size": len(titles),
            "board_hits": {asset: sum(asset in refs for refs in titles.values()) for asset in ASSETS},
            "hits": {
                asset: sum(asset in refs and appearances[title] == 1 for title, refs in titles.items())
                for asset in ASSETS
            },
        }
        if hashes_by_asset:
            row_entry["hashes"] = hashes_by_asset
        out[platform] = row_entry
    return out


_V2_TO_V3_ASSETS = {
    "GOLD": "GOLD",
    "BTC": "BTC",
    "FED": "FED_MACRO",
    "FED_MACRO": "FED_MACRO",
    "US_TECH": "US_AI",
    "QQQ": "US_AI",
    "US_AI": "US_AI",
    "ASHARE_HK": "ASHARE_HK",
}


def _migrate_v2_row(obs: dict[str, Any]) -> dict[str, Any]:
    out = dict(obs)
    raw_cap = obs.get("sample_size")
    cap = int(raw_cap) if type(raw_cap) is int and _MIN_LIST_SIZE <= raw_cap <= _LIST_SIZE else _LIST_SIZE

    raw_hashes = obs.get("hashes")
    migrated_hashes: dict[str, list[str]] = {}
    if isinstance(raw_hashes, dict):
        merged_sets: dict[str, set[str]] = {}
        for old_k, vals in raw_hashes.items():
            new_k = _V2_TO_V3_ASSETS.get(old_k)
            if new_k and isinstance(vals, list):
                merged_sets.setdefault(new_k, set()).update(str(v) for v in vals if isinstance(v, str) and v)
        migrated_hashes = {k: sorted(v)[:6] for k, v in merged_sets.items() if v}
        if migrated_hashes:
            out["hashes"] = migrated_hashes

    src_board = obs.get("board_hits")
    migrated_board: dict[str, int] = {}
    if isinstance(src_board, dict):
        for old_k, val in src_board.items():
            new_k = _V2_TO_V3_ASSETS.get(old_k)
            if new_k and type(val) is int and val >= 0:
                migrated_board[new_k] = min(cap, migrated_board.get(new_k, 0) + val)
        out["board_hits"] = migrated_board

    src_hits = obs.get("hits")
    if isinstance(src_hits, dict):
        migrated_hits: dict[str, int] = {}
        for old_k, val in src_hits.items():
            new_k = _V2_TO_V3_ASSETS.get(old_k)
            if new_k and type(val) is int and val >= 0:
                max_k = migrated_board.get(new_k, cap)
                migrated_hits[new_k] = min(max_k, migrated_hits.get(new_k, 0) + val)
        out["hits"] = migrated_hits
    return out


def _migrate_v2_history(raw_hist: Any) -> dict[str, Any]:
    if not isinstance(raw_hist, dict):
        return {}
    out: dict[str, Any] = {}
    for day_k, day_v in raw_hist.items():
        if not isinstance(day_v, dict):
            continue
        if isinstance(day_v.get("slots"), dict):
            out[day_k] = {
                "slots": {
                    sk: {pk: _migrate_v2_row(pv) if isinstance(pv, dict) else pv for pk, pv in sv.items()}
                    if isinstance(sv, dict)
                    else sv
                    for sk, sv in day_v["slots"].items()
                }
            }
        else:
            out[day_k] = {pk: _migrate_v2_row(pv) if isinstance(pv, dict) else pv for pk, pv in day_v.items()}
    return out


def _load_cache(path: Path) -> dict[str, Any]:
    try:
        if path.stat().st_size > _MAX_CACHE_BYTES:
            return {}
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {}
        ver = raw.get("version")
        if ver == _CACHE_VERSION:
            return raw
        if ver == 2:
            return {
                "version": _CACHE_VERSION,
                "history": _migrate_v2_history(raw.get("history")),
                "attempt": raw.get("attempt") if isinstance(raw.get("attempt"), dict) else {},
            }
        return {}
    except (OSError, ValueError):
        return {}


def _parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(value) if isinstance(value, str) and len(value) == 10 else None
    except ValueError:
        return None


def _strip_old_hashes(row: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for k, v in row.items():
        if k == "slots" and isinstance(v, dict):
            cleaned["slots"] = {sk: _strip_old_hashes(sv) if isinstance(sv, dict) else sv for sk, sv in v.items()}
        elif isinstance(v, dict):
            cleaned[k] = {pk: pv for pk, pv in v.items() if pk != "hashes"}
        else:
            cleaned[k] = v
    return cleaned


def _retained_days(raw: Any, today: date) -> dict[str, Any]:
    cutoff = today - timedelta(days=LOOKBACK_DAYS - 1)
    hash_cutoff = today - timedelta(days=_HASH_RETENTION_DAYS)
    out: dict[str, Any] = {}
    for key, row in (raw.items() if isinstance(raw, dict) else ()):
        day = _parse_date(key)
        if day is not None and cutoff <= day <= today and isinstance(row, dict):
            out[key] = row if day >= hash_cutoff else _strip_old_hashes(row)
    return out


def refresh_discussion_history(
    data_dir: Path,
    fetch: Callable[[str], dict[str, Any]],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Sample across 4 six-hour Beijing time slots daily (00-06, 06-12, 12-18, 18-24); call under lock.

    Up to 3 retry attempts per (day, slot) spaced >= 30 minutes apart. Failed fetches never record zeros.
    """
    now = (now or datetime.now(BJT)).astimezone(BJT)
    today = now.date()
    key = today.isoformat()
    slot = str(max(0, min(_SLOTS_PER_DAY - 1, now.hour // (24 // _SLOTS_PER_DAY))))
    path = data_dir / _CACHE_NAME
    cache = _load_cache(path)
    history = _retained_days(cache.get("history"), today)

    day_entry = history.get(key)
    legacy_platforms = (
        {pk: pv for pk, pv in day_entry.items() if pk in PLATFORMS and isinstance(pv, dict)}
        if isinstance(day_entry, dict) and not isinstance(day_entry.get("slots"), dict)
        else {}
    )
    day_slots = dict(day_entry.get("slots")) if isinstance(day_entry, dict) and isinstance(day_entry.get("slots"), dict) else {}
    if legacy_platforms:
        prev_0 = dict(day_slots.get("0")) if isinstance(day_slots.get("0"), dict) else {}
        day_slots["0"] = {**legacy_platforms, **prev_0}
        history[key] = {"slots": day_slots}

    previous = cache.get("attempt") if isinstance(cache.get("attempt"), dict) else {}
    same_bucket = previous.get("date") == key and str(previous.get("slot") or "") == slot
    attempt = previous if same_bucket else {}
    last = attempt.get("epoch", 0)
    tries = attempt.get("count", 0)
    tries = tries if type(tries) is int and 0 <= tries <= 3 else 0
    if (
        len(day_slots.get(slot, {}) if isinstance(day_slots.get(slot), dict) else {}) == len(PLATFORMS)
        or tries >= 3
        or (isinstance(last, (int, float)) and now.timestamp() - last < 1800)
    ):
        return history

    feeds: dict[str, Any] = {}
    import os
    import uuid

    with ThreadPoolExecutor(max_workers=len(PLATFORMS)) as pool:
        pending = {platform: pool.submit(fetch, platform) for platform in PLATFORMS}
        for platform, future in pending.items():
            try:
                feeds[platform] = future.result()
            except Exception:
                continue

    samples = daily_observations(feeds, now)
    prev_slot_samples = day_slots.get(slot) if isinstance(day_slots.get(slot), dict) else {}
    merged_slot = dict(prev_slot_samples)
    merged_slot.update(samples)
    if merged_slot:
        day_slots[slot] = merged_slot
    if day_slots:
        history[key] = {"slots": day_slots}

    payload_obj: dict[str, Any] = {
        "version": _CACHE_VERSION,
        "history": history,
        "attempt": {"date": key, "slot": slot, "epoch": now.timestamp(), "count": tries + 1},
    }
    encoded = json.dumps(payload_obj, ensure_ascii=False, separators=(",", ":"))
    while len(encoded.encode("utf-8")) > _MAX_CACHE_BYTES and len(history) > 1:
        oldest_day = min(history.keys())
        history.pop(oldest_day, None)
        encoded = json.dumps(payload_obj, ensure_ascii=False, separators=(",", ":"))

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:6]}.tmp")
    try:
        tmp_path.write_text(encoded, encoding="utf-8", newline="")
        os.replace(tmp_path, path)
    finally:
        tmp_path.unlink(missing_ok=True)
    return history


def _valid_obs_row(row: Any, asset: str) -> tuple[float, float, set[str]] | None:
    if not isinstance(row, dict):
        return None
    n, hits, board_hits = row.get("sample_size"), row.get("hits"), row.get("board_hits")
    h = hits.get(asset) if isinstance(hits, dict) else None
    board_h = board_hits.get(asset) if isinstance(board_hits, dict) else None
    if (
        type(n) is not int
        or not _MIN_LIST_SIZE <= n <= _LIST_SIZE
        or type(h) is not int
        or type(board_h) is not int
        or not 0 <= h <= board_h <= n
    ):
        return None
    hashes_raw = row.get("hashes")
    asset_hashes: set[str] = set()
    if isinstance(hashes_raw, dict) and isinstance(hashes_raw.get(asset), list):
        asset_hashes = {str(x) for x in hashes_raw[asset] if isinstance(x, str) and x}
    return (board_h / n, h / n, asset_hashes)


def _extract_day_platform_obs(entries: dict[str, Any], platform: str, asset: str) -> tuple[float, float, set[str]] | None:
    """Aggregate all valid intraday slots on one calendar day into an unbiased daily platform share."""
    slots = entries.get("slots")
    if isinstance(slots, dict):
        slot_vals: list[tuple[float, float, set[str]]] = []
        for slot_key in sorted(slots.keys()):
            bucket = slots.get(slot_key)
            if isinstance(bucket, dict):
                parsed = _valid_obs_row(bucket.get(platform), asset)
                if parsed is not None:
                    slot_vals.append(parsed)
        if not slot_vals:
            return None
        b_avg = sum(x[0] for x in slot_vals) / len(slot_vals)
        i_avg = sum(x[1] for x in slot_vals) / len(slot_vals)
        merged_hashes: set[str] = set()
        for x in slot_vals:
            merged_hashes |= x[2]
        return (b_avg, i_avg, merged_hashes)
    return _valid_obs_row(entries.get(platform), asset)


def discussion_trends(history: dict[str, Any], *, today: date | None = None) -> dict[str, Any]:
    """Summarize persistent multi-day warming; unaffected by 3rd-party mirroring or single-day spikes."""
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
            warming_plats: list[str] = []
            cooling_count = 0
            high_count = 0
            counts: list[int] = []
            deltas: list[float] = []
            sources: list[dict[str, Any]] = []
            late_hashes_by_plat: dict[str, set[str]] = {}
            late_indep_by_plat: dict[str, float] = {}

            for platform in PLATFORMS:
                early_vals: list[float] = []
                late_vals: list[float] = []
                late_indep_sum = 0.0
                late_hashes: set[str] = set()
                most_recent = -1
                last_level = 0.0

                for day_key, entries in sorted(days.items()):
                    day_dt = _parse_date(day_key)
                    if day_dt is None:
                        continue
                    offset = (day_dt - start).days
                    if offset < 0:
                        continue
                    obs = _extract_day_platform_obs(entries, platform, asset)
                    if obs is None:
                        continue
                    b_share, i_share, h_set = obs
                    if offset > most_recent:
                        most_recent, last_level = offset, b_share
                    if offset < early_len:
                        early_vals.append(b_share)
                    else:
                        late_vals.append(b_share)
                        late_indep_sum += i_share
                        late_hashes |= h_set

                if len(early_vals) < min_early or len(late_vals) < min_late or most_recent < period - 2:
                    continue

                comparable += 1
                total_days = len(early_vals) + len(late_vals)
                counts.append(total_days)
                base = float(median(early_vals))
                current = float(median(late_vals))
                change = current - base
                deltas.append(change)
                late_hashes_by_plat[platform] = late_hashes
                late_indep_by_plat[platform] = late_indep_sum

                up_bar = max(_MIN_ACTIVE_SHARE, base + _MIN_DELTA)
                down_bar = base - _MIN_DELTA
                up_days = sum(v >= up_bar for v in late_vals)
                down_days = sum(v <= down_bar for v in late_vals)

                source_state = "stable"
                if change >= _MIN_DELTA and up_days * 5 >= len(late_vals) * 3 and last_level >= up_bar:
                    warming_plats.append(platform)
                    source_state = "warming"
                elif change <= -_MIN_DELTA and down_days * 5 >= len(late_vals) * 3 and last_level <= down_bar:
                    cooling_count += 1
                    source_state = "cooling"
                elif abs(change) < _MIN_DELTA and min(base, current, last_level) >= _HIGH_SHARE:
                    high_count += 1
                    source_state = "high"

                sources.append({"platform": platform, "state": source_state, "sample_days": total_days})

            state = "insufficient"
            if comparable >= 2:
                med_delta = float(median(deltas))
                if len(warming_plats) >= 2 and cooling_count == 0 and med_delta >= _MIN_DELTA:
                    # Guard against identical or daily-mirrored copy-pasted titles across warming platforms.
                    hash_sets = [late_hashes_by_plat.get(p) or set() for p in warming_plats]
                    if any(hash_sets):
                        unique_plats = sum(
                            1
                            for idx, hs in enumerate(hash_sets)
                            if hs - set().union(*(hash_sets[:idx] + hash_sets[idx + 1 :]))
                        )
                        has_diversity = unique_plats >= 2
                    else:
                        has_diversity = sum(1 for p in warming_plats if late_indep_by_plat.get(p, 0.0) > 0.0) >= 2
                    state = "warming" if has_diversity else "stable"
                elif cooling_count >= 2 and not warming_plats and med_delta <= -_MIN_DELTA:
                    state = "cooling"
                elif high_count >= 2 and not warming_plats and cooling_count == 0:
                    state = "high"
                else:
                    state = "stable"

            windows.append(
                {
                    "days": period,
                    "state": state,
                    "platforms": comparable,
                    "sample_days": min(counts, default=0),
                    "sources": sources,
                }
            )
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
    for row in rows[: len(ASSETS)]:
        asset = row.get("asset") if isinstance(row, dict) else None
        if not isinstance(asset, str) or asset not in ASSETS or asset in seen:
            continue
        raw = row.get("windows")
        if not isinstance(raw, list):
            continue
        windows = []
        used: set[int] = set()
        for win in raw[: len(PERIODS)]:
            if not isinstance(win, dict):
                continue
            period, state, count, days = (win.get(k) for k in ("days", "state", "platforms", "sample_days"))
            if (
                type(period) is not int
                or period not in PERIODS
                or period in used
                or state not in ("insufficient", "warming", "cooling", "stable", "high")
                or type(count) is not int
                or not 0 <= count <= len(PLATFORMS)
                or type(days) is not int
                or not 0 <= days <= period
            ):
                continue
            early = period // 2
            min_days = (early * 3 + 4) // 5 + ((period - early) * 3 + 4) // 5
            sources = []
            source_ids: set[str] = set()
            raw_sources = win.get("sources")
            for source in raw_sources[: len(PLATFORMS)] if isinstance(raw_sources, list) else []:
                if not isinstance(source, dict):
                    continue
                platform, direction, covered = (source.get(k) for k in ("platform", "state", "sample_days"))
                if (
                    isinstance(platform, str)
                    and platform in PLATFORMS
                    and platform not in source_ids
                    and direction in ("warming", "cooling", "stable", "high")
                    and type(covered) is int
                    and min_days <= covered <= period
                ):
                    source_ids.add(platform)
                    sources.append({"platform": platform, "state": direction, "sample_days": covered})
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


def load_valid_trends_from_cache(data_dir: Path, *, today: date | None = None) -> dict[str, Any]:
    """Compute today's whitelisted trends strictly from local persisted cache without network I/O."""
    cache = _load_cache(Path(data_dir) / _CACHE_NAME)
    history = cache.get("history")
    if not isinstance(history, dict) or not history:
        return {}
    ref_today = today or datetime.now(BJT).date()
    return project_discussion_trends(discussion_trends(history, today=ref_today), today=ref_today)
