"""Official 2026-2027 release dates for US macro events and stock earnings dates."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore[assignment]

UTC = timezone.utc
MACRO_EVENT_TYPES = ("fomc", "cpi", "nfp", "pce", "gdp")

# Verified against federalreserve.gov/monetarypolicy/fomccalendars.htm
FOMC_2026_2027: tuple[tuple[str, str, str], ...] = (
    ("2026-01-28", "exact", "1月议息会议利率决议"),
    ("2026-03-18", "exact", "3月议息会议利率决议（含经济预测）"),
    ("2026-04-29", "exact", "4月议息会议利率决议"),
    ("2026-06-17", "exact", "6月议息会议利率决议（含经济预测）"),
    ("2026-07-29", "exact", "7月议息会议利率决议"),
    ("2026-09-16", "exact", "9月议息会议利率决议（含经济预测）"),
    ("2026-10-28", "exact", "10月议息会议利率决议"),
    ("2026-12-09", "exact", "12月议息会议利率决议（含经济预测）"),
    ("2027-01-27", "exact", "1月议息会议利率决议"),
    ("2027-03-17", "exact", "3月议息会议利率决议"),
    ("2027-04-28", "exact", "4月议息会议利率决议"),
    ("2027-06-09", "exact", "6月议息会议利率决议"),
    ("2027-07-28", "exact", "7月议息会议利率决议"),
    ("2027-09-15", "exact", "9月议息会议利率决议"),
    ("2027-10-27", "exact", "10月议息会议利率决议"),
    ("2027-11-17", "date", "货币政策框架战略会议"),
)

# Verified against bls.gov/schedule/news_release/cpi.htm (08:30 AM US/Eastern)
BLS_CPI_2026_2027: tuple[tuple[str, str], ...] = (
    ("2026-01-13", "2025年12月"),
    ("2026-02-11", "2026年1月"),
    ("2026-03-11", "2026年2月"),
    ("2026-04-10", "2026年3月"),
    ("2026-05-12", "2026年4月"),
    ("2026-06-10", "2026年5月"),
    ("2026-07-14", "2026年6月"),
    ("2026-08-12", "2026年7月"),
    ("2026-09-11", "2026年8月"),
    ("2026-10-14", "2026年9月"),
    ("2026-11-10", "2026年10月"),
    ("2026-12-10", "2026年11月"),
    ("2027-01-13", "2026年12月"),
    ("2027-02-11", "2027年1月"),
    ("2027-03-10", "2027年2月"),
    ("2027-04-13", "2027年3月"),
    ("2027-05-12", "2027年4月"),
    ("2027-06-10", "2027年5月"),
    ("2027-07-14", "2027年6月"),
    ("2027-08-11", "2027年7月"),
    ("2027-09-14", "2027年8月"),
    ("2027-10-14", "2027年9月"),
    ("2027-11-10", "2027年10月"),
    ("2027-12-10", "2027年11月"),
)

# Verified against bls.gov/schedule/news_release/empsit.htm (08:30 AM US/Eastern)
BLS_NFP_2026_2027: tuple[tuple[str, str], ...] = (
    ("2026-11-06", "2026年10月"),
    ("2026-12-04", "2026年11月"),
    ("2027-01-08", "2026年12月"),
    ("2027-02-05", "2027年1月"),
    ("2027-03-05", "2027年2月"),
    ("2027-04-02", "2027年3月"),
    ("2027-05-07", "2027年4月"),
    ("2027-06-04", "2027年5月"),
    ("2027-07-02", "2027年6月"),
    ("2027-08-06", "2027年7月"),
    ("2027-09-03", "2027年8月"),
    ("2027-10-08", "2027年9月"),
    ("2027-11-05", "2027年10月"),
    ("2027-12-03", "2027年11月"),
)

# BEA's schedules, checked 2026-11-23. Update the dates from the official
# tables (not a recurrence rule); these selected releases are at 08:30 ET.
BEA_RELEASE_SCHEDULE = "https://www.bea.gov/news/schedule"
BEA_PCE_2026_2027: tuple[tuple[str, str], ...] = (
    ("2026-11-25", "2026年10月"),
    ("2026-12-23", "2026年11月"),
    ("2027-01-28", "2026年12月"),
    ("2027-02-25", "2027年1月"),
    ("2027-03-31", "2027年2月"),
    ("2027-04-29", "2027年3月"),
    ("2027-05-27", "2027年4月"),
    ("2027-06-25", "2027年5月"),
    ("2027-07-29", "2027年6月"),
    ("2027-08-25", "2027年7月"),
    ("2027-09-30", "2027年8月"),
    ("2027-10-28", "2027年9月"),
    ("2027-11-24", "2027年10月"),
    ("2027-12-22", "2027年11月"),
)

# Publication date, reference quarter, estimate stage, includes corporate profits.
BEA_GDP_2026_2027: tuple[tuple[str, str, str, bool], ...] = (
    ("2026-11-25", "2026年第三季度", "第二次估算", True),
    ("2026-12-23", "2026年第三季度", "第三次估算", True),
    ("2027-01-28", "2026年第四季度", "初步估算", False),
    ("2027-02-25", "2026年第四季度", "第二次估算", False),
    ("2027-03-31", "2026年第四季度", "第三次估算", True),
    ("2027-04-29", "2027年第一季度", "初步估算", False),
    ("2027-05-27", "2027年第一季度", "第二次估算", True),
    ("2027-06-25", "2027年第一季度", "第三次估算", True),
    ("2027-07-29", "2027年第二季度", "初步估算", False),
    ("2027-08-25", "2027年第二季度", "第二次估算", True),
    ("2027-09-30", "2027年第二季度", "第三次估算", True),
    ("2027-10-28", "2027年第三季度", "初步估算", False),
    ("2027-11-24", "2027年第三季度", "第二次估算", True),
    ("2027-12-22", "2027年第三季度", "第三次估算", True),
)


def _us_eastern_to_utc(d_str: str, hour: int, minute: int) -> datetime:
    d = date.fromisoformat(d_str)
    if ZoneInfo is not None:
        try:
            ny_tz = ZoneInfo("America/New_York")
            return datetime(d.year, d.month, d.day, hour, minute, tzinfo=ny_tz).astimezone(UTC)
        except Exception:
            pass
    # Pure-stdlib US DST rule: 2nd Sunday of March .. 1st Sunday of November
    mar1 = date(d.year, 3, 1)
    dst_start = mar1 + timedelta(days=(6 - mar1.weekday()) % 7 + 7)
    nov1 = date(d.year, 11, 1)
    dst_end = nov1 + timedelta(days=(6 - nov1.weekday()) % 7)
    is_edt = dst_start <= d < dst_end
    offset = timezone(timedelta(hours=-4 if is_edt else -5))
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=offset).astimezone(UTC)


def _make_exact_event(
    *,
    ev_id: str,
    event_type: str,
    category_cn: str,
    title: str,
    date_et: str,
    hour_et: int,
    minute_et: int,
    tickers: list[str],
    source: str,
    now_utc: datetime,
    local_tz: timezone,
    impact: str = "高",
    source_url: str | None = None,
) -> dict[str, Any]:
    dt_utc = _us_eastern_to_utc(date_et, hour_et, minute_et)
    dt_local = dt_utc.astimezone(local_tz)
    hours_until = round((dt_utc - now_utc).total_seconds() / 3600.0, 1)
    days_until = (dt_local.date() - now_utc.astimezone(local_tz).date()).days
    return {
        "id": ev_id,
        "event_type": event_type,
        "category_cn": category_cn,
        "title": title,
        "precision": "exact",
        "event_utc": dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "date_local": dt_local.strftime("%Y-%m-%d"),
        "time_local": dt_local.strftime("%Y-%m-%d %H:%M"),
        "time_bjt": dt_utc.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M"),
        "days_until": days_until,
        "hours_until": hours_until,
        "tickers": list(tickers),
        "impact": impact,
        "source": source,
        **({"source_url": source_url} if source_url else {}),
    }


def _make_date_event(
    *,
    ev_id: str,
    event_type: str,
    category_cn: str,
    title: str,
    date_str: str,
    tickers: list[str],
    source: str,
    now_utc: datetime,
    local_tz: timezone,
    impact: str = "高",
) -> dict[str, Any]:
    d_obj = date.fromisoformat(date_str[:10])
    days_until = (d_obj - now_utc.astimezone(local_tz).date()).days
    return {
        "id": ev_id,
        "event_type": event_type,
        "category_cn": category_cn,
        "title": title,
        "precision": "date",
        "event_utc": None,
        "date_local": d_obj.isoformat(),
        "time_local": d_obj.isoformat(),
        "time_bjt": d_obj.isoformat(),
        "days_until": days_until,
        "hours_until": None,
        "tickers": list(tickers),
        "impact": impact,
        "source": source,
    }


def build_calendar_events(
    stock_profiles: dict[str, dict[str, Any]] | None = None,
    *,
    now_utc: datetime | None = None,
    tz_offset_hours: int = 8,
) -> list[dict[str, Any]]:
    """Build official macro and stock earnings events with honest time precision."""
    now = now_utc or datetime.now(UTC)
    local_tz = timezone(timedelta(hours=tz_offset_hours))
    events: list[dict[str, Any]] = []

    macro_tickers = ["US10Y", "SPY", "QQQ", "BTC"]

    for d_str, prec, desc in FOMC_2026_2027:
        if prec == "exact":
            events.append(
                _make_exact_event(
                    ev_id=f"cal-fomc-{d_str}",
                    event_type="fomc",
                    category_cn="美联储议息",
                    title=f"美联储 FOMC {desc}",
                    date_et=d_str,
                    hour_et=14,
                    minute_et=0,
                    tickers=macro_tickers,
                    source="Federal Reserve",
                    now_utc=now,
                    local_tz=local_tz,
                )
            )
        else:
            events.append(
                _make_date_event(
                    ev_id=f"cal-fomc-{d_str}",
                    event_type="fomc",
                    category_cn="美联储议息",
                    title=f"美联储 FOMC {desc}",
                    date_str=d_str,
                    tickers=macro_tickers,
                    source="Federal Reserve",
                    now_utc=now,
                    local_tz=local_tz,
                    impact="中",
                )
            )

    for d_str, ref_month in BLS_CPI_2026_2027:
        events.append(
            _make_exact_event(
                ev_id=f"cal-cpi-{d_str}",
                event_type="cpi",
                category_cn="美国 CPI 通胀",
                title=f"美国 {ref_month} CPI 物价指数公布",
                date_et=d_str,
                hour_et=8,
                minute_et=30,
                tickers=macro_tickers,
                source="BLS",
                now_utc=now,
                local_tz=local_tz,
            )
        )

    for d_str, ref_month in BLS_NFP_2026_2027:
        events.append(
            _make_exact_event(
                ev_id=f"cal-nfp-{d_str}",
                event_type="nfp",
                category_cn="美国非农就业",
                title=f"美国 {ref_month} 非农就业报告 (NFP)",
                date_et=d_str,
                hour_et=8,
                minute_et=30,
                tickers=macro_tickers,
                source="BLS",
                now_utc=now,
                local_tz=local_tz,
            )
        )

    bea_releases = (
        ("pce", "美国 PCE 通胀", (
            (d_str, f"美国 {ref_month} PCE 物价指数公布")
            for d_str, ref_month in BEA_PCE_2026_2027
        )),
        ("gdp", "美国经济增长", (
            (d_str, f"美国 {ref_quarter} GDP {stage}{'及企业利润' if includes_profits else ''}公布")
            for d_str, ref_quarter, stage, includes_profits in BEA_GDP_2026_2027
        )),
    )
    for event_type, category_cn, releases in bea_releases:
        for d_str, title in releases:
            events.append(
                _make_exact_event(
                    ev_id=f"cal-{event_type}-{d_str}",
                    event_type=event_type,
                    category_cn=category_cn,
                    title=title,
                    date_et=d_str,
                    hour_et=8,
                    minute_et=30,
                    tickers=macro_tickers,
                    source="BEA",
                    source_url=BEA_RELEASE_SCHEDULE,
                    now_utc=now,
                    local_tz=local_tz,
                )
            )

    # Stock earnings dates from stock_profiles (strictly date precision; never fabricate hours)
    for sym, prof in sorted((stock_profiles or {}).items()):
        if not isinstance(prof, dict):
            continue
        ov = prof.get("overview") or {}
        raw_dt = prof.get("earnings_date") or prof.get("next_earnings_date") or ov.get("earnings_date")
        if not raw_dt:
            continue
        d_str = str(raw_dt).strip()[:10]
        try:
            date.fromisoformat(d_str)
        except ValueError:
            continue
        cn_name = str(prof.get("name_cn") or sym).strip()
        events.append(
            _make_date_event(
                ev_id=f"cal-earn-{sym.upper()}-{d_str}",
                event_type="earnings",
                category_cn="美股财报",
                title=f"{cn_name} ({sym.upper()}) 财报发布窗口",
                date_str=d_str,
                tickers=[sym.upper()],
                source="上市公司财报日程",
                now_utc=now,
                local_tz=local_tz,
            )
        )

    events.sort(key=lambda e: (str(e.get("date_local") or ""), str(e.get("time_bjt") or ""), str(e.get("id") or "")))
    return events


def filter_calendar_events(
    events: list[dict[str, Any]],
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    days_ahead: int | None = 90,
    event_type: str | None = None,
    scope: str = "market",
    held_symbols: list[str] | set[str] | tuple[str, ...] | None = None,
    now_utc: datetime | None = None,
    tz_offset_hours: int = 8,
) -> list[dict[str, Any]]:
    """Filter calendar events by date range, event_type, and market/held scope."""
    now = now_utc or datetime.now(UTC)
    local_today = now.astimezone(timezone(timedelta(hours=tz_offset_hours))).date()

    lo_date = start_date[:10] if start_date else (local_today - timedelta(days=1)).isoformat()
    if end_date:
        hi_date = end_date[:10]
    elif days_ahead is not None:
        hi_date = (local_today + timedelta(days=int(days_ahead))).isoformat()
    else:
        hi_date = "9999-12-31"

    type_norm = str(event_type or "").strip().lower() or None
    scope_norm = str(scope or "market").strip().lower()
    held_set = {str(s).strip().upper() for s in (held_symbols or []) if str(s).strip()}

    out: list[dict[str, Any]] = []
    for ev in events or []:
        d_loc = str(ev.get("date_local") or "")[:10]
        if not d_loc or d_loc < lo_date or d_loc > hi_date:
            continue
        if type_norm and type_norm != "all" and str(ev.get("event_type") or "").lower() != type_norm:
            continue
        if scope_norm == "held":
            ev_tickers = {str(t).strip().upper() for t in (ev.get("tickers") or []) if str(t).strip()}
            if not (ev_tickers & held_set):
                continue
        out.append(dict(ev))
    return out
