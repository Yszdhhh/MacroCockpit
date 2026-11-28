"""SEC EDGAR submissions fetcher and T1 regulatory filing signal builder."""
from __future__ import annotations

import hashlib
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:
    requests = None  # type: ignore[assignment]

_BACKEND_DIR = Path(__file__).resolve().parent
if str(_BACKEND_DIR.parent) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR.parent))

from backend.runtime import BJT, DATA_DIR, UTC, atomic_write_json


SEC_CACHE_PATH = DATA_DIR / "sec_filings_cache.json"
SEC_CACHE_TTL = 6 * 3600

SEC_HEADERS: dict[str, str] = {
    "User-Agent": "MacroCockpitResearch/1.0 (contact@shuashuabao.xyz)",
    "Accept-Encoding": "gzip, deflate",
}

SEC_CIK_MAP: dict[str, str] = {
    "AAPL": "0000320193",
    "NVDA": "0001045810",
    "MSFT": "0000789019",
    "GOOG": "0001652044",
    "META": "0001326801",
    "AMZN": "0001018724",
    "TSLA": "0001318605",
    "AMD": "0000002488",
    "AVGO": "0001730168",
    "TSM": "0001046179",
    "MU": "0000723125",
    "INTC": "0000050863",
    "QCOM": "0000804328",
    "PLTR": "0001321655",
    "ORCL": "0001341439",
    "NFLX": "0001065280",
    "ARM": "0001973239",
    "SMCI": "0001375365",
    "COIN": "0001679788",
    "MSTR": "0001050446",
}

SEC_CN_NAMES: dict[str, str] = {
    "AAPL": "苹果",
    "NVDA": "英伟达",
    "MSFT": "微软",
    "GOOG": "谷歌",
    "META": "Meta",
    "AMZN": "亚马逊",
    "TSLA": "特斯拉",
    "AMD": "超威半导体",
    "AVGO": "博通",
    "TSM": "台积电",
    "MU": "美光科技",
    "INTC": "英特尔",
    "QCOM": "高通",
    "PLTR": "Palantir",
    "ORCL": "甲骨文",
    "NFLX": "奈飞",
    "ARM": "Arm",
    "SMCI": "超微电脑",
    "COIN": "Coinbase",
    "MSTR": "MicroStrategy",
}

SEC_FORM_LABELS: dict[str, str] = {
    "10-K": "年度财报 (10-K)",
    "10-Q": "季度财报 (10-Q)",
    "8-K": "重大事件披露 (8-K)",
    "20-F": "外国发行人年报 (20-F)",
    "6-K": "外国发行人临时披露 (6-K)",
}
ALLOWED_FORMS = frozenset(SEC_FORM_LABELS.keys())


def _read_cache(cache_path: Path = SEC_CACHE_PATH) -> dict[str, dict[str, Any]]:
    if not cache_path.exists():
        return {}
    try:
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {}
        by_sym = raw.get("by_symbol")
        if isinstance(by_sym, dict):
            return {
                str(k).upper(): v
                for k, v in by_sym.items()
                if isinstance(v, dict) and isinstance(v.get("items"), list)
            }
        # Support flat legacy format if present
        ts_fallback = float(raw.get("ts") or 0.0)
        filings = raw.get("filings")
        if isinstance(filings, dict):
            return {
                str(k).upper(): {"ts": ts_fallback, "items": v}
                for k, v in filings.items()
                if isinstance(v, list)
            }
    except Exception:
        pass
    return {}


def _write_cache(by_symbol: dict[str, dict[str, Any]], cache_path: Path = SEC_CACHE_PATH) -> None:
    try:
        atomic_write_json(
            cache_path,
            {
                "ts": time.time(),
                "by_symbol": by_symbol,
            },
        )
    except Exception:
        pass


def _parse_submissions_payload(symbol: str, cik: str, payload: dict[str, Any], *, limit: int = 4) -> list[dict[str, str]]:
    recent = ((payload.get("filings") or {}).get("recent")) or {}
    forms = recent.get("form") or []
    dates = recent.get("filingDate") or []
    accessions = recent.get("accessionNumber") or []
    primary_docs = recent.get("primaryDocument") or []

    cik_int = int(cik)
    n = min(len(forms), len(dates), len(accessions), len(primary_docs))
    out: list[dict[str, str]] = []
    for i in range(n):
        form = str(forms[i] or "").strip().upper()
        if form not in ALLOWED_FORMS:
            continue
        filing_date = str(dates[i] or "").strip()
        acc = str(accessions[i] or "").strip()
        doc = str(primary_docs[i] or "").strip().lstrip("/")
        if not filing_date or not acc:
            continue
        acc_nodash = acc.replace("-", "")
        if doc:
            url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/{doc}"
        else:
            url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/{acc}-index.htm"
        out.append(
            {
                "symbol": symbol,
                "form": form,
                "label_cn": SEC_FORM_LABELS[form],
                "filing_date": filing_date,
                "url": url,
            }
        )
        if len(out) >= limit:
            break
    return out


def _fetch_one_cik(symbol: str, cik: str, *, timeout: int = 6) -> list[dict[str, str]] | None:
    if requests is None:
        return None
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    try:
        resp = requests.get(url, headers=SEC_HEADERS, timeout=timeout)
        if resp.status_code != 200:
            return None
        payload = resp.json()
        if not isinstance(payload, dict):
            return None
        return _parse_submissions_payload(symbol, cik, payload, limit=4)
    except Exception:
        return None


def fetch_sec_filings_map(
    symbols: list[str] | tuple[str, ...] | None = None,
    *,
    force_refresh: bool = False,
    timeout: int = 6,
    cache_path: Path = SEC_CACHE_PATH,
) -> dict[str, list[dict[str, str]]]:
    """Fetch and cache recent 10-K/10-Q/8-K/20-F/6-K SEC EDGAR filings for core tickers."""
    raw_syms = list(symbols) if symbols is not None else list(SEC_CIK_MAP.keys())
    target_syms: list[str] = []
    for s in raw_syms:
        sym = str(s or "").strip().upper()
        if sym in SEC_CIK_MAP and sym not in target_syms:
            target_syms.append(sym)
    if not target_syms:
        return {}

    by_symbol = _read_cache(cache_path)
    now = time.time()
    need_fetch = [
        sym
        for sym in target_syms
        if force_refresh
        or sym not in by_symbol
        or (now - float(by_symbol[sym].get("ts") or 0.0)) > SEC_CACHE_TTL
    ]

    if need_fetch and requests is not None:
        updated = False
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {
                pool.submit(_fetch_one_cik, sym, SEC_CIK_MAP[sym], timeout=timeout): sym
                for sym in need_fetch
            }
            for fut in as_completed(futures):
                sym = futures[fut]
                try:
                    items = fut.result()
                except Exception:
                    items = None
                if items is not None:
                    by_symbol[sym] = {"ts": time.time(), "items": items}
                    updated = True
        if updated:
            _write_cache(by_symbol, cache_path)

    return {
        sym: [dict(item) for item in (by_symbol.get(sym) or {}).get("items") or []]
        for sym in target_syms
    }


def collect_recent_sec_signals(
    filings_map: dict[str, list[dict[str, str]]],
    *,
    max_age_days: int = 10,
    now_utc: datetime | None = None,
) -> list[dict[str, Any]]:
    """Convert recent SEC EDGAR filings (within max_age_days) into T1 financial signals."""
    now = now_utc or datetime.now(UTC)
    cutoff_date = (now.astimezone(BJT).date() - timedelta(days=max_age_days)).isoformat()
    signals: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for sym, filings in (filings_map or {}).items():
        sym_u = str(sym or "").strip().upper()
        cn_name = SEC_CN_NAMES.get(sym_u, sym_u)
        for item in filings or []:
            filing_date = str(item.get("filing_date") or "").strip()
            form = str(item.get("form") or "").strip().upper()
            label_cn = str(item.get("label_cn") or SEC_FORM_LABELS.get(form, form)).strip()
            url = str(item.get("url") or "").strip()
            if not filing_date or filing_date < cutoff_date or not url.startswith(("http://", "https://")):
                continue
            sig_id = f"sec-{sym_u.lower()}-{form.lower()}-{filing_date}-{hashlib.sha1(url.encode('utf-8')).hexdigest()[:8]}"
            if sig_id in seen_ids:
                continue
            seen_ids.add(sig_id)
            is_core_report = form in {"10-K", "10-Q", "20-F"}
            signals.append(
                {
                    "id": sig_id,
                    "source": "SEC EDGAR",
                    "category": "equities",
                    "source_tier": "T1",
                    "direction": "neutral",
                    "importance": "高" if is_core_report else "中",
                    "time_bjt": f"{filing_date} 08:00",
                    "title": f"{cn_name} ({sym_u}) 提交 {label_cn}",
                    "summary": (
                        f"{cn_name}（{sym_u}）于 {filing_date} 向美国证监会（SEC EDGAR）正式提交 "
                        f"{form} 法定披露文件（{label_cn}），可直达原始财务与重大事项文档。"
                    ),
                    "why_it_matters": "美股上市公司一手法定监管披露，直接校准营收增速、现金流、指引与风险边界。",
                    "tickers": [sym_u],
                    "url": url,
                }
            )

    signals.sort(key=lambda x: (str(x.get("time_bjt") or ""), str(x.get("id") or "")), reverse=True)
    return signals


if __name__ == "__main__":
    fmap = fetch_sec_filings_map(["AAPL", "NVDA"])
    sigs = collect_recent_sec_signals(fmap, max_age_days=25)
    print(json.dumps({"filings_counts": {k: len(v) for k, v in fmap.items()}, "recent_signals": len(sigs)}, ensure_ascii=False))
