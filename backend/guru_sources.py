"""Guru blog & Substack RSS/Atom fulltext collector and signal adapter ('大佬分享')."""
from __future__ import annotations

import hashlib
import json
import re
import socket
import sys
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Sequence

try:
    import requests
except ImportError:
    requests = None  # type: ignore[assignment]

_BACKEND_DIR = Path(__file__).resolve().parent
if str(_BACKEND_DIR.parent) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR.parent))

from backend.guardrails import (
    has_private_article_content,
    is_safe_public_url,
    neutralize_private_triggers,
    safe_article_filename,
    safe_fetch_public_url,
)
from backend.runtime import BJT, DATA_DIR, UTC, atomic_write_json, file_lock
from backend.kazike_engine import cluster_and_dedup, score_five_axes
from backend.rss_sources import ARTICLES_DIR, GURU_ARTICLE_FEEDS as GURU_ARTICLE_FEEDS, _HEADERS
from backend.signal_text import (
    clean_feed_text,
    decode_http_text,
    extract_subtopics,
    extract_tickers,
    html_to_clean_markdown,
    infer_signal_direction,
    parse_rss_datetime,
    strip_xml_ns,
    yaml_quote,
)

GURU_CACHE_PATH = DATA_DIR / "guru_articles_cache.json"
GURU_CACHE_TTL = 1800  # 30 minutes
GURU_SCHEMA_TAG = "guru_v1_fulltext"

_ALLOWED_SIGNAL_CATEGORIES: frozenset[str] = frozenset({"macro", "equities", "ashare", "crypto", "ai"})

def _sanitize_guru_text(text: str) -> str:
    return neutralize_private_triggers(text)


def _escape_bare_ampersands_outside_cdata(xml_text: str) -> str:
    """Repair stray unescaped '&' outside CDATA blocks so strict XML parsing succeeds."""
    parts = re.split(r"(<!\[CDATA\[.*?\]\]>)", xml_text, flags=re.DOTALL)
    rebuilt: list[str] = []
    amp_re = re.compile(r"&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)")
    for idx, part in enumerate(parts):
        if idx % 2 == 1:
            rebuilt.append(part)
        else:
            rebuilt.append(amp_re.sub("&amp;", part))
    return "".join(rebuilt)


def _parse_guru_xml_entries(
    xml_text: str,
    *,
    now_utc: datetime | None = None,
    max_age_days: int = 45,
) -> list[dict[str, Any]]:
    """Parse complete RSS 2.0 / Atom XML string into raw entry dicts without truncating CDATA."""
    if not xml_text or not xml_text.strip():
        return []
    now = now_utc or datetime.now(UTC)
    cutoff = now - timedelta(days=max(1, int(max_age_days)))

    cleaned = xml_text.lstrip("\ufeff")
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", cleaned)

    root: ET.Element | None = None
    try:
        root = ET.fromstring(cleaned)
    except ET.ParseError:
        try:
            repaired = _escape_bare_ampersands_outside_cdata(cleaned)
            root = ET.fromstring(repaired)
        except ET.ParseError:
            return []

    entries_out: list[dict[str, Any]] = []
    nodes = [elem for elem in root.iter() if strip_xml_ns(elem.tag) in ("item", "entry")]
    for entry in nodes:
        title = ""
        link = ""
        pub_raw = ""
        author = ""
        bodies: dict[str, str] = {}
        for child in entry:
            name = strip_xml_ns(child.tag)
            if name == "title" and not title:
                title = child.text or "".join(child.itertext())
            elif name in ("encoded", "content", "description", "summary"):
                val = (child.text or "".join(child.itertext())).strip()
                if val and (name not in bodies or len(val) > len(bodies[name])):
                    bodies[name] = val
            elif name == "link":
                href = child.attrib.get("href") or (child.text or "").strip()
                if href and (not link or child.attrib.get("rel", "alternate") == "alternate"):
                    link = href.strip()
            elif name in ("pubdate", "published", "updated") and not pub_raw:
                pub_raw = (child.text or "").strip()
            elif name in ("creator", "author") and not author:
                author = (child.text or "".join(child.itertext())).strip()

        dt = parse_rss_datetime(pub_raw) or now
        if dt < cutoff:
            continue

        short_raw = (
            bodies.get("description")
            or bodies.get("summary")
            or bodies.get("encoded")
            or bodies.get("content")
            or ""
        )
        long_raw = max(
            (
                bodies.get("encoded", ""),
                bodies.get("content", ""),
                bodies.get("summary", ""),
                bodies.get("description", ""),
            ),
            key=len,
        )
        safe_link = link if is_safe_public_url(link, socket_mod=socket) else ""
        entries_out.append(
            {
                "title": title,
                "short_raw": short_raw,
                "long_raw": long_raw,
                "link": safe_link,
                "author": author,
                "dt_utc": dt,
            }
        )
    return entries_out


def _build_cn_takeaways(
    *,
    title: str,
    summary: str,
    body_md: str,
    publisher: str,
    author_role: str,
    category: str,
    direction: str,
    tickers: list[str],
    topics: list[str],
) -> list[str]:
    """Build plain-Chinese structured takeaway bullets for the article reader."""
    dir_label = {"bullish": "偏多参考", "bearish": "偏空风控", "neutral": "中性拆解"}.get(direction, "中性拆解")
    cat_label = {
        "macro": "全球宏观与流动性",
        "equities": "美股基本面与商业战略",
        "ai": "AI 算力与硬科技",
        "crypto": "加密宏观与链上周期",
        "ashare": "A股与港股联动",
    }.get(category, "市场深度观察")
    ticker_txt = "、".join(tickers[:5]) if tickers else "宏观跨资产框架"
    topic_txt = "、".join(topics[:4]) if topics else cat_label

    headings: list[str] = []
    for ln in body_md.splitlines():
        s = ln.strip()
        if s.startswith(("# ", "## ", "### ")):
            h_txt = clean_feed_text(s.lstrip("# ").strip(), max_len=90)
            if h_txt and h_txt != title and h_txt not in headings:
                headings.append(h_txt)
        if len(headings) >= 3:
            break

    bullets = [
        f"**核心议题**：{publisher}（{author_role}）围绕「{topic_txt}」展开深度剖析，文章标题为《{title}》。",
        f"**市场映射与倾向**：情绪偏向「{dir_label}」，重点映射标的/资产为 {ticker_txt}（归属赛道：{cat_label}）。",
        f"**摘要提炼**：{summary}",
    ]
    if headings:
        bullets.append(f"**行文脉络**：原文核心章节包括「{'；'.join(headings)}」。")
    return [_sanitize_guru_text(b) for b in bullets]


def _compose_guru_markdown(
    *,
    title: str,
    summary: str,
    publisher: str,
    author_role: str,
    background: str,
    time_bjt: str,
    url: str,
    tickers: list[str],
    takeaways: list[str],
    full_md: str,
) -> str:
    tickers_display = "、".join(tickers) if tickers else "宏观/跨资产"
    link_line = f"[{url}]({url})" if url else "原文链接暂未附带"
    takeaway_lines = "\n".join(f"- {b}" for b in takeaways)
    doc = (
        f"# {title}\n\n"
        f"## 大佬背景导读\n"
        f"- **专栏来源**：{publisher}（{author_role}）\n"
        f"- **作者定位**：{background}\n"
        f"- **发布时间**：{time_bjt} (BJT)\n"
        f"- **涉及标的**：{tickers_display}\n"
        f"- **原文链接**：{link_line}\n\n"
        f"## 中文核心要点\n"
        f"{takeaway_lines}\n\n"
        f"---\n\n"
        f"## 原文全文\n\n"
        f"{full_md}\n"
    )
    return _sanitize_guru_text(doc)


def parse_guru_feed_xml(
    xml_text: str,
    spec: dict[str, str],
    *,
    now_utc: datetime | None = None,
    max_items: int = 4,
    fallback_recent_if_older: bool = True,
) -> list[dict[str, Any]]:
    """Parse a guru RSS/Atom XML payload into normalized article candidates."""
    max_age_days = int(spec.get("max_age_days") or 45)
    raw_entries = _parse_guru_xml_entries(xml_text, now_utc=now_utc, max_age_days=max_age_days)
    if not raw_entries and fallback_recent_if_older:
        all_entries = _parse_guru_xml_entries(xml_text, now_utc=now_utc, max_age_days=36500)
        all_entries.sort(key=lambda e: e.get("dt_utc") or (now_utc or datetime.now(UTC)), reverse=True)
        raw_entries = all_entries[: max(1, min(2, max_items))]
    if not raw_entries:
        return []

    spec_id = str(spec.get("id") or "guru").strip()
    publisher = _sanitize_guru_text(spec.get("publisher") or spec.get("author") or "大佬专栏")
    author_role = _sanitize_guru_text(spec.get("author_role") or "资深市场观察者")
    background = _sanitize_guru_text(spec.get("background") or f"{publisher} 深度专栏文章。")
    category = str(spec.get("category") or "macro").strip().lower()
    if category not in _ALLOWED_SIGNAL_CATEGORIES:
        category = "macro"
    tier = str(spec.get("tier") or "T1_5").strip()
    if tier not in ("T1", "T1_5", "T2"):
        tier = "T1_5"

    items_out: list[dict[str, Any]] = []
    for ent in raw_entries:
        if len(items_out) >= max_items:
            break
        title = _sanitize_guru_text(clean_feed_text(ent.get("title") or "", max_len=150))
        if not title:
            continue

        full_md = html_to_clean_markdown(str(ent.get("long_raw") or ent.get("short_raw") or ""), max_chars=24000)
        full_md = _sanitize_guru_text(full_md)
        summary_clean = _sanitize_guru_text(clean_feed_text(ent.get("short_raw") or "", max_len=220))
        if not summary_clean or len(summary_clean) < 18:
            first_para = next(
                (ln.strip() for ln in full_md.splitlines() if ln.strip() and not ln.strip().startswith("#")),
                title,
            )
            summary_clean = _sanitize_guru_text(clean_feed_text(first_para, max_len=220)) or title
        if len(full_md) < 40:
            full_md = summary_clean

        url = str(ent.get("link") or spec.get("html_url") or "").strip()
        if url and not is_safe_public_url(url, socket_mod=socket):
            url = ""
        dt_utc = ent.get("dt_utc") or (now_utc or datetime.now(UTC))
        time_bjt = dt_utc.astimezone(BJT).strftime("%Y-%m-%d %H:%M")

        combined_meta = f"{title} {summary_clean} {full_md[:1800]}"
        tickers = extract_tickers(combined_meta)
        topics = extract_subtopics(combined_meta, category)
        direction = infer_signal_direction(f"{title} {summary_clean} {full_md[:1200]}")

        takeaways = _build_cn_takeaways(
            title=title,
            summary=summary_clean,
            body_md=full_md,
            publisher=publisher,
            author_role=author_role,
            category=category,
            direction=direction,
            tickers=tickers,
            topics=topics,
        )
        body_md = _compose_guru_markdown(
            title=title,
            summary=summary_clean,
            publisher=publisher,
            author_role=author_role,
            background=background,
            time_bjt=time_bjt,
            url=url,
            tickers=tickers,
            takeaways=takeaways,
            full_md=full_md,
        )

        # Deterministic score via kazike_engine (without mutating global receipt store)
        score_res = score_five_axes(
            {
                "title": title,
                "summary": summary_clean,
                "why_it_matters": background,
                "tickers": tickers,
                "direction": direction,
                "importance": "高" if tier == "T1" else "中",
                "url": url,
                "category": category,
                "source": publisher,
                "source_tier": tier,
            },
            persist_receipt=False,
        )
        bonus = 6 if tier == "T1" else 3
        score = int(max(0, min(100, int(score_res.get("score") or 50) + bonus)))

        slug_raw = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff_-]+", "_", f"{spec_id}_{title}").strip("_")
        slug = slug_raw[:32] or spec_id
        h_sfx = hashlib.sha1((spec_id + "|" + title + "|" + url).encode("utf-8")).hexdigest()[:8]
        fname = f"guru_{slug}_{h_sfx}.md"
        if not safe_article_filename(fname):
            fname = f"guru_{spec_id}_{h_sfx}.md"

        candidate = {
            "id": f"guru-{spec_id}-{h_sfx}",
            "feed_id": spec_id,
            "filename": fname,
            "source": "大佬分享",
            "publisher": publisher,
            "author_role": author_role,
            "category": category,
            "source_tier": tier,
            "direction": direction,
            "time_bjt": time_bjt,
            "title": title,
            "summary": summary_clean,
            "excerpt": summary_clean,
            "why_it_matters": f"{publisher}（{author_role}）：{background}",
            "tickers": tickers,
            "topics": topics,
            "url": url,
            "score": score,
            "attention_score": score,
            "body_md": body_md,
        }
        if has_private_article_content(candidate):
            continue
        items_out.append(candidate)
    return items_out


def _fetch_one_guru_feed(
    spec: dict[str, str],
    *,
    timeout: int = 8,
    now_utc: datetime | None = None,
) -> tuple[str, list[dict[str, Any]], str]:
    """Fetch and parse a single guru feed with full exception isolation."""
    fid = str(spec.get("id") or "guru")
    feed_url = str(spec.get("url") or "").strip()
    if requests is None:
        return (fid, [], "requests_unavailable")
    if not is_safe_public_url(feed_url, socket_mod=socket):
        return (fid, [], "blocked_url")

    headers = {
        **_HEADERS,
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.5",
    }
    try:
        resp = safe_fetch_public_url(
            feed_url, headers=headers, timeout=timeout, requests_mod=requests, socket_mod=socket,
        )
        if resp is None:
            return (fid, [], "blocked_redirect")
        final_url = str(getattr(resp, "url", "") or feed_url)
        if final_url and not is_safe_public_url(final_url, socket_mod=socket):
            return (fid, [], "blocked_redirect")
        if int(getattr(resp, "status_code", 0) or 0) != 200 or not getattr(resp, "content", b""):
            return (fid, [], f"http_{getattr(resp, 'status_code', 0)}")
        xml_text = decode_http_text(resp)
        parsed = parse_guru_feed_xml(xml_text, spec, now_utc=now_utc, max_items=4)
        return (fid, parsed, "ok" if parsed else "empty")
    except Exception as exc:
        return (fid, [], f"error:{type(exc).__name__}")


def _write_guru_article_files(articles: list[dict[str, Any]], articles_dir: Path) -> None:
    articles_dir.mkdir(parents=True, exist_ok=True)
    for old_file in articles_dir.glob("guru_*.md"):
        try:
            old_file.unlink()
        except OSError:
            pass

    for art in articles:
        fname = safe_article_filename(art.get("filename"))
        if not fname:
            continue
        if has_private_article_content(art):
            continue
        title = _sanitize_guru_text(str(art.get("title") or "大佬专栏"))
        summary = _sanitize_guru_text(str(art.get("summary") or title))
        publisher = _sanitize_guru_text(str(art.get("publisher") or "大佬分享"))
        author_role = _sanitize_guru_text(str(art.get("author_role") or "资深市场观察者"))
        url = str(art.get("url") or "").strip()
        score = int(art.get("score") or 0)
        tickers = [str(t).strip().upper() for t in (art.get("tickers") or []) if str(t).strip()]
        tickers_yaml = "[" + ", ".join(yaml_quote(t) for t in tickers) + "]"
        body_md = _sanitize_guru_text(str(art.get("body_md") or summary)).strip()

        content = (
            "---\n"
            f"title: {yaml_quote(title)}\n"
            f"summary: {yaml_quote(summary)}\n"
            f"publisher: {yaml_quote(publisher)}\n"
            f"author_role: {yaml_quote(author_role)}\n"
            f"tickers: {tickers_yaml}\n"
            f"url: {yaml_quote(url)}\n"
            f"score: {score}\n"
            "---\n\n"
            f"{body_md}\n"
        )
        if has_private_article_content({"filename": fname, "content": content}):
            continue
        (articles_dir / fname).write_text(content, encoding="utf-8")


def _select_balanced_guru_articles(
    candidates: list[dict[str, Any]],
    *,
    max_articles: int = 24,
    max_per_publisher: int = 2,
) -> list[dict[str, Any]]:
    deduped = cluster_and_dedup(candidates) if candidates else []
    ordered = sorted(
        deduped,
        key=lambda x: (str(x.get("time_bjt") or ""), int(x.get("score") or 0)),
        reverse=True,
    )
    picked: list[dict[str, Any]] = []
    picked_idx: set[int] = set()
    pub_counts: dict[str, int] = {}

    for idx, it in enumerate(ordered):
        if len(picked) >= max_articles:
            break
        pub = str(it.get("publisher") or "大佬分享").strip()
        if pub_counts.get(pub, 0) >= max_per_publisher:
            continue
        picked.append(it)
        picked_idx.add(idx)
        pub_counts[pub] = pub_counts.get(pub, 0) + 1

    if len(picked) < max_articles:
        for idx, it in enumerate(ordered):
            if idx in picked_idx:
                continue
            picked.append(it)
            if len(picked) >= max_articles:
                break
    return picked


def _load_guru_cache(cache_path: Path | None) -> tuple[list[dict[str, Any]], float]:
    if cache_path is None or not cache_path.is_file():
        return ([], 0.0)
    try:
        with file_lock(cache_path):
            raw = json.loads(cache_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("schema_tag") != GURU_SCHEMA_TAG:
            return ([], 0.0)
        ts = float(raw.get("ts") or 0.0)
        arts = [dict(x) for x in (raw.get("articles") or []) if isinstance(x, dict) and not has_private_article_content(x)]
        return (arts, ts)
    except Exception:
        return ([], 0.0)


def collect_guru_articles(
    *,
    force_refresh: bool = False,
    timeout: int = 8,
    articles_dir: Path | None = ARTICLES_DIR,
    cache_path: Path | None = GURU_CACHE_PATH,
    feeds: Sequence[dict[str, str]] | None = None,
    write_files: bool = True,
    max_articles: int = 24,
    max_per_publisher: int = 2,
    now_utc: datetime | None = None,
) -> list[dict[str, Any]]:
    """Collect, extract, cache and optionally persist guru fulltext articles as articles/guru_*.md."""
    if not force_refresh and cache_path is not None:
        cached_arts, cached_ts = _load_guru_cache(cache_path)
        if cached_arts and (time.time() - cached_ts) <= GURU_CACHE_TTL:
            if write_files and articles_dir is not None and not list(articles_dir.glob("guru_*.md")):
                _write_guru_article_files(cached_arts, articles_dir)
            return cached_arts[:max_articles]

    feed_specs = list(feeds if feeds is not None else GURU_ARTICLE_FEEDS)
    raw_candidates: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = [pool.submit(_fetch_one_guru_feed, sp, timeout=timeout, now_utc=now_utc) for sp in feed_specs]
        for fut in as_completed(futs):
            try:
                _fid, items, _status = fut.result()
                raw_candidates.extend(items)
            except Exception:
                pass

    if raw_candidates:
        selected = _select_balanced_guru_articles(
            raw_candidates,
            max_articles=max_articles,
            max_per_publisher=max_per_publisher,
        )
        # Ensure clean JSON-serializable public schema without private content
        clean_articles: list[dict[str, Any]] = []
        for art in selected:
            clean_art = {
                k: v
                for k, v in art.items()
                if not str(k).startswith("_") and not isinstance(v, datetime)
            }
            if not has_private_article_content(clean_art):
                clean_articles.append(clean_art)
        if write_files and articles_dir is not None:
            _write_guru_article_files(clean_articles, articles_dir)
        if cache_path is not None:
            try:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                with file_lock(cache_path):
                    atomic_write_json(
                        cache_path,
                        {
                            "schema_tag": GURU_SCHEMA_TAG,
                            "ts": time.time(),
                            "articles": clean_articles,
                        },
                    )
            except Exception:
                pass
        return clean_articles

    # Network failure fallback to stale cache if present
    if cache_path is not None:
        stale_arts, _ts = _load_guru_cache(cache_path)
        if stale_arts:
            if write_files and articles_dir is not None and not list(articles_dir.glob("guru_*.md")):
                _write_guru_article_files(stale_arts, articles_dir)
            return stale_arts[:max_articles]
    return []


def collect_guru_signals(
    *,
    articles: list[dict[str, Any]] | None = None,
    force_refresh: bool = False,
    timeout: int = 8,
    cache_path: Path | None = GURU_CACHE_PATH,
    articles_dir: Path | None = None,
    write_files: bool = False,
    max_signals: int = 12,
    feeds: Sequence[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """Convert flagship guru longform articles into standard cockpit signal items (source='大佬分享')."""
    src_articles = (
        list(articles)
        if articles is not None
        else collect_guru_articles(
            force_refresh=force_refresh,
            timeout=timeout,
            articles_dir=articles_dir,
            cache_path=cache_path,
            feeds=feeds,
            write_files=write_files,
        )
    )
    if not src_articles:
        return []

    ordered = sorted(
        src_articles,
        key=lambda a: (int(a.get("score") or 0), str(a.get("time_bjt") or "")),
        reverse=True,
    )
    signals_out: list[dict[str, Any]] = []
    for art in ordered:
        if len(signals_out) >= max_signals:
            break
        if has_private_article_content(art):
            continue
        title = _sanitize_guru_text(str(art.get("title") or "").strip())
        if not title:
            continue
        publisher = _sanitize_guru_text(str(art.get("publisher") or "大佬专栏").strip())
        author_role = _sanitize_guru_text(str(art.get("author_role") or "资深市场观察者").strip())
        summary = _sanitize_guru_text(str(art.get("summary") or art.get("excerpt") or title).strip())

        cat = str(art.get("category") or "macro").strip().lower()
        if cat not in _ALLOWED_SIGNAL_CATEGORIES:
            cat = "macro"

        direction = str(art.get("direction") or "").strip().lower()
        if direction not in ("bullish", "bearish", "neutral"):
            direction = infer_signal_direction(f"{title} {summary}")

        tickers = [str(t).strip().upper() for t in (art.get("tickers") or []) if str(t).strip()]
        if not tickers:
            tickers = extract_tickers(f"{title} {summary}")

        topics = [str(t).strip() for t in (art.get("topics") or []) if str(t).strip()]
        if not topics:
            topics = extract_subtopics(f"{title} {summary}", cat)

        score = int(art.get("score") or 60)
        importance = "高" if (score >= 68 or (len(tickers) >= 1 and score >= 58)) else "中"
        url = str(art.get("url") or "").strip()
        if url and not is_safe_public_url(url, socket_mod=socket):
            url = ""

        sig_id = str(art.get("id") or "")
        if not sig_id.startswith("guru-"):
            sig_id = f"guru-{hashlib.sha1((publisher + title + url).encode('utf-8')).hexdigest()[:10]}"

        why = _sanitize_guru_text(
            str(art.get("why_it_matters") or f"{publisher}（{author_role}）深度长文，辅助评估中长期主线与估值锚。")
        )
        short_pub = publisher.split("·", 1)[0].strip()
        sig_title = title if short_pub.lower() in title.lower() else f"{short_pub}：{title}"
        sig_title = _sanitize_guru_text(clean_feed_text(sig_title, max_len=140))

        sig = {
            "id": sig_id,
            "source": "大佬分享",
            "source_tier": str(art.get("source_tier") or "T1_5") if str(art.get("source_tier") or "") in ("T1", "T1_5", "T2") else "T1_5",
            "category": cat,
            "direction": direction,
            "importance": importance,
            "time_bjt": str(art.get("time_bjt") or datetime.now(BJT).strftime("%Y-%m-%d %H:%M")),
            "title": sig_title,
            "summary": clean_feed_text(summary, max_len=240),
            "why_it_matters": why,
            "tickers": tickers[:6],
            "topics": topics[:4],
            "url": url,
            "publisher": publisher,
            "author_role": author_role,
            "article_file": str(art.get("filename") or ""),
        }
        if not has_private_article_content(sig):
            signals_out.append(sig)
    return signals_out
