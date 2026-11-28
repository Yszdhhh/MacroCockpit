"""Read, filter, balance and persist the shared Markdown article collection."""
from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.guardrails import (
    has_private_article_content,
    parse_yaml_mapping,
    safe_article_filename,
    scrub_markdown,
    scrub_quant_thesis,
)
from backend.runtime import BJT, COCKPIT_ROOT, DATA_DIR, USER_HOME, atomic_write_text, resolve_kol_dir
from backend.signal_text import clean_business_text, extract_tickers, lexical_similarity, normalize_url, yaml_quote

DEFAULT_ARTICLES_DIR = COCKPIT_ROOT / "articles"
STATE_OUTPUT_PATH = DATA_DIR / "cockpit_state.json"
KOL_ARTICLES_DIR = resolve_kol_dir() / "articles"
OBSIDIAN_INBOX_DIR = Path(
    os.environ.get("MACROCOCKPIT_NOTES_DIR")
    or (USER_HOME / "Documents" / "Obsidian Vault" / "01_Inbox")
)
PERIODIC_SOURCE_LABEL = "周月度深研"
_FRONTMATTER_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)^---[ \t]*(?:\r?\n|$)", re.MULTILINE | re.DOTALL)


def get_allowed_article_dirs() -> list[tuple[Path, str]]:
    return [
        (DEFAULT_ARTICLES_DIR, "同步深度长文"),
        (KOL_ARTICLES_DIR, "KOL深度长文"),
        (OBSIDIAN_INBOX_DIR, "本地知识库"),
    ]


def read_article_markdown(
    path: Path, source_label: str, *, max_bytes: int | None = None
) -> str | None:
    inbox = source_label == "本地知识库" or path.resolve().is_relative_to(OBSIDIAN_INBOX_DIR.resolve())
    if inbox:
        max_bytes = min(max_bytes, 256 * 1024) if max_bytes is not None else 256 * 1024
    try:
        with path.open("rb") as handle:
            raw = handle.read(max_bytes + 1) if max_bytes is not None else handle.read()
    except OSError:
        return None
    if max_bytes is not None and len(raw) > max_bytes:
        return None
    text = raw.decode("utf-8-sig", errors="replace").replace("\r\n", "\n")
    if inbox:
        frontmatter = _FRONTMATTER_RE.match(text)
        if not frontmatter or not re.search(r"^publish:[ \t]*true[ \t]*(?:#.*)?$", frontmatter[1], re.MULTILINE):
            return None
        if parse_yaml_mapping(frontmatter[1]).get("publish") is not True:
            return None
    return text if not has_private_article_content({"filename": path.name, "content": text}) else None


def parse_markdown_article(path: Path, source_label: str) -> dict[str, Any] | None:
    """Parse a markdown file into the shared article schema."""
    if not path.is_file() or not safe_article_filename(path.name):
        return None
    try:
        raw = read_article_markdown(path, source_label)
        if raw is None:
            return None
        mtime_str = datetime.fromtimestamp(path.stat().st_mtime, tz=BJT).strftime("%Y-%m-%d %H:%M")
    except OSError:
        return None

    title = ""
    excerpt = ""
    publisher = ""
    author_role = ""
    art_url = ""
    art_score: int | None = None
    art_tickers: list[str] = []
    report_kind = ""
    body_md = raw.strip()

    # Check YAML frontmatter
    match = _FRONTMATTER_RE.match(body_md)
    if match:
        fm_raw = match.group(1)
        fm_meta = parse_yaml_mapping(fm_raw)
        body_md = body_md[match.end():].strip()
        if fm_meta:
            title = str(fm_meta.get("title") or "").strip()
            excerpt = str(fm_meta.get("summary") or "").strip()
            publisher = str(fm_meta.get("publisher") or "").strip()
            author_role = str(fm_meta.get("author_role") or "").strip()
            art_url = str(fm_meta.get("url") or "").strip()
            report_kind = str(fm_meta.get("report_kind") or "").strip().lower()
            raw_score = fm_meta.get("score")
            if isinstance(raw_score, (int, float)) and not isinstance(raw_score, bool):
                art_score = int(round(float(raw_score)))
            raw_tickers = fm_meta.get("tickers")
            if isinstance(raw_tickers, list):
                art_tickers = [str(t).strip().upper() for t in raw_tickers if str(t).strip()][:8]
        else:
            m_title = re.search(r'^title:\s*["\']?(.+?)["\']?\s*$', fm_raw, re.MULTILINE)
            m_sum = re.search(r'^summary:\s*["\']?(.+?)["\']?\s*$', fm_raw, re.MULTILINE)
            m_pub = re.search(r'^publisher:\s*["\']?(.+?)["\']?\s*$', fm_raw, re.MULTILINE)
            m_role = re.search(r'^author_role:\s*["\']?(.+?)["\']?\s*$', fm_raw, re.MULTILINE)
            m_url = re.search(r'^url:\s*["\']?(.+?)["\']?\s*$', fm_raw, re.MULTILINE)
            m_rk = re.search(r'^report_kind:\s*["\']?(.+?)["\']?\s*$', fm_raw, re.MULTILINE)
            m_scr = re.search(r'^score:\s*(\d+(?:\.\d+)?)\s*$', fm_raw, re.MULTILINE)
            if m_title:
                title = m_title.group(1).strip()
            if m_sum:
                excerpt = m_sum.group(1).strip()
            if m_pub:
                publisher = m_pub.group(1).strip()
            if m_role:
                author_role = m_role.group(1).strip()
            if m_url:
                art_url = m_url.group(1).strip()
            if m_rk:
                report_kind = m_rk.group(1).strip().lower()
            if m_scr:
                art_score = int(round(float(m_scr.group(1))))

    if not title:
        for ln in body_md.splitlines():
            s = ln.strip()
            if s.startswith("# "):
                title = s[2:].strip()
                break
    if not title:
        title = path.stem

    if not excerpt:
        m_abs = re.search(r"\*\*核心摘要\*\*[：:]\s*(.+)", body_md)
        if m_abs:
            excerpt = m_abs.group(1).strip()
        else:
            for ln in body_md.splitlines():
                s = ln.strip()
                if s and not s.startswith(("#", "-", "*", ">", "---", "```")) and len(s) >= 24:
                    excerpt = s
                    break
    if len(excerpt) > 180:
        excerpt = excerpt[:177].rstrip() + "..."

    fname_lower = path.name.lower()
    if fname_lower.startswith(("weekly_", "monthly_", "dr_")) or report_kind in ("weekly", "monthly", "deep_research"):
        resolved_source = "周月度深研"
    elif fname_lower.startswith("ta_"):
        resolved_source = "多智能体深研"
    elif fname_lower.startswith("guru_"):
        resolved_source = "大佬分享"
    elif publisher:
        resolved_source = "AI与金融研报"
    else:
        resolved_source = source_label
    clean_body = scrub_quant_thesis(body_md, preserve_newlines=True)
    out: dict[str, Any] = {
        "filename": path.name,
        "title": clean_business_text(title),
        "source": resolved_source,
        "mtime": mtime_str,
        "excerpt": clean_business_text(excerpt) or clean_business_text(title),
        "body_md": clean_body,
    }
    if publisher:
        out["publisher"] = clean_business_text(publisher)
    if author_role:
        out["author_role"] = clean_business_text(author_role)
    if report_kind:
        out["report_kind"] = report_kind
    if art_tickers:
        out["tickers"] = art_tickers
    if art_url.startswith(("http://", "https://")):
        out["url"] = art_url
    if art_score is not None:
        out["score"] = art_score
    return out


def dedup_articles(
    items: list[dict[str, Any]],
    *,
    normalize_url_fn: Callable[[str | None], str] = normalize_url,
    similarity_fn: Callable[[str, str], float] = lexical_similarity,
) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    seen_titles: list[str] = []
    seen_ta_syms: set[str] = set()
    seen_periodic_fnames: set[str] = set()
    for art in items:
        fname = str(art.get("filename") or "")
        if art.get("source") == "周月度深研" or fname.lower().startswith(("weekly_", "monthly_", "dr_")):
            pf_key = fname.lower()
            if pf_key in seen_periodic_fnames:
                continue
            seen_periodic_fnames.add(pf_key)
            deduped.append(art)
            continue
        m_ta = re.match(r"^ta_([A-Za-z0-9._-]+)_", fname, re.IGNORECASE)
        if m_ta:
            sym_key = m_ta.group(1).upper()
            if sym_key in seen_ta_syms:
                continue
            seen_ta_syms.add(sym_key)
            deduped.append(art)
            continue
        norm_u = normalize_url_fn(art.get("url"))
        title = str(art.get("title") or "").strip()
        if norm_u and norm_u in seen_urls:
            continue
        if title and any(similarity_fn(title, prev) >= 0.68 for prev in seen_titles):
            continue
        if norm_u:
            seen_urls.add(norm_u)
        if title:
            seen_titles.append(title)
        deduped.append(art)
    return deduped


def interleave_article_buckets(buckets: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Round-robin interleave non-empty article source buckets so every source remains represented."""
    queues = [list(b) for b in buckets if b]
    merged: list[dict[str, Any]] = []
    while queues:
        next_queues: list[list[dict[str, Any]]] = []
        for q in queues:
            merged.append(q.pop(0))
            if q:
                next_queues.append(q)
        queues = next_queues
    return merged


def sort_and_balance_articles(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    periodic_arts = sorted(
        [a for a in articles if a.get("source") == "周月度深研"],
        key=lambda x: (int(x.get("score") or 0), str(x.get("mtime") or "")),
        reverse=True,
    )
    guru_arts = sorted(
        [a for a in articles if a.get("source") == "大佬分享"],
        key=lambda x: (int(x.get("score") or 0), str(x.get("mtime") or "")),
        reverse=True,
    )
    ai_arts = sorted(
        [a for a in articles if a.get("source") == "AI与金融研报"],
        key=lambda x: (int(x.get("score") or 0), str(x.get("mtime") or "")),
        reverse=True,
    )
    kol_arts = sorted(
        [a for a in articles if a.get("source") == "KOL深度长文"],
        key=lambda x: (int(x.get("score") or 0), str(x.get("mtime") or "")),
        reverse=True,
    )
    ta_arts = sorted(
        [a for a in articles if a.get("source") == "多智能体深研"],
        key=lambda x: str(x.get("mtime") or ""),
        reverse=True,
    )
    kb_arts = sorted(
        [
            a
            for a in articles
            if a.get("source") not in ("周月度深研", "大佬分享", "AI与金融研报", "KOL深度长文", "多智能体深研")
        ],
        key=lambda x: str(x.get("mtime") or ""),
        reverse=True,
    )
    interleaved = interleave_article_buckets([periodic_arts, guru_arts, ai_arts, kol_arts, ta_arts, kb_arts])
    return dedup_articles(interleaved)


def build_yaml_frontmatter(
    *,
    title: str,
    summary: str,
    publisher: str,
    author_role: str,
    report_kind: str,
    score: int,
    tickers: list[str],
    url: str = "",
    related_period: str = "",
) -> str:
    lines = [
        "---",
        f"title: {yaml_quote(title)}",
        f"summary: {yaml_quote(summary)}",
        f"publisher: {yaml_quote(publisher)}",
        f"author_role: {yaml_quote(author_role)}",
        f"report_kind: {yaml_quote(report_kind)}",
        f"score: {int(score)}",
    ]
    if tickers:
        tickers_yaml = ", ".join(yaml_quote(t) for t in tickers[:8])
        lines.append(f"tickers: [{tickers_yaml}]")
    else:
        lines.append("tickers: []")
    if url and url.startswith(("http://", "https://")):
        lines.append(f"url: {yaml_quote(url)}")
    if related_period:
        lines.append(f"related_period: {yaml_quote(related_period)}")
    lines.append("---")
    return "\n".join(lines)


def split_frontmatter(raw_markdown: str) -> tuple[dict[str, Any], str]:
    text = str(raw_markdown or "").replace("\r\n", "\n").lstrip("\ufeff")
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text.strip()
    fm_raw = match.group(1)
    body = text[match.end() :].strip()
    return parse_yaml_mapping(fm_raw), body


def periodic_article_dict(
    *,
    filename: str,
    title: str,
    summary: str,
    publisher: str,
    author_role: str,
    report_kind: str,
    score: int,
    tickers: list[str],
    body_md: str,
    mtime_str: str,
    url: str = "",
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "filename": filename,
        "title": scrub_markdown(title),
        "source": PERIODIC_SOURCE_LABEL,
        "mtime": mtime_str,
        "excerpt": scrub_markdown(summary or title)[:180],
        "body_md": scrub_markdown(body_md),
        "publisher": scrub_markdown(publisher),
        "author_role": scrub_markdown(author_role),
        "report_kind": report_kind,
        "score": int(score),
        "tickers": list(tickers[:8]),
    }
    if url and url.startswith(("http://", "https://")):
        out["url"] = url
    return out


def _normalize_tickers_input(tickers: list[str] | tuple[str, ...] | str | None, text_for_extract: str) -> list[str]:
    out: list[str] = []
    if isinstance(tickers, str):
        raw_items = re.split(r"[,，\s]+", tickers.strip())
    elif isinstance(tickers, (list, tuple, set)):
        raw_items = [str(x).strip() for x in tickers]
    else:
        raw_items = []
    for item in raw_items:
        sym = item.strip().lstrip("$").upper()
        if sym and sym not in out:
            out.append(sym)
    for inferred in extract_tickers(text_for_extract, source="kol"):
        if inferred not in out:
            out.append(inferred)
    return out[:8]


def _resolve_ingest_title(explicit_title: str, fm_meta: dict[str, Any], body: str) -> str:
    if explicit_title and explicit_title.strip():
        return explicit_title.strip()
    for ln in body.splitlines():
        s = ln.strip()
        if s.startswith("# "):
            heading = s[2:].strip()
            if heading:
                return heading
    fm_title = str(fm_meta.get("title") or "").strip()
    if fm_title:
        return fm_title
    plain = re.sub(r"[#>*`\[\]()\-]+", " ", body)
    plain = re.sub(r"\s+", " ", plain).strip()
    if plain:
        return plain[:20]
    return "外部深研补充"


def ingest_deep_research_markdown(
    raw_markdown: str,
    *,
    title: str = "",
    tickers: list[str] | tuple[str, ...] | str | None = None,
    related_period: str = "",
    articles_dir: Path | str | None = None,
    url: str = "",
    publisher: str = "Deep Research 深度补充",
    author_role: str = "外部深研闭环",
) -> dict[str, Any]:
    """Sanitize, privacy-check, persist, and return an external Deep Research markdown article."""
    raw_text = str(raw_markdown or "").strip()
    if not raw_text:
        raise ValueError("外部深研 Markdown 正文不能为空")

    fm_meta, stripped_body = split_frontmatter(raw_text)
    if not stripped_body:
        raise ValueError("剥离头部元信息后正文为空")

    resolved_title = _resolve_ingest_title(title, fm_meta, stripped_body)
    resolved_url = str(url or fm_meta.get("url") or "").strip()
    if not resolved_url.startswith(("http://", "https://")):
        resolved_url = ""

    # Privacy & credential redline BEFORE any persistence
    if has_private_article_content(
        {
            "filename": "dr_ingest_check.md",
            "title": resolved_title,
            "body_md": raw_text,
            "content": raw_text,
        }
    ):
        raise ValueError("内容包含敏感私人持仓或密钥信息，已拦截入库")

    clean_title = scrub_markdown(resolved_title)
    clean_body = scrub_markdown(stripped_body)
    clean_period = scrub_markdown(related_period).strip()

    if clean_period and "关联周期" not in clean_body[:160]:
        lines = clean_body.splitlines()
        quote_line = f"> 关联周期研报：{clean_period}"
        if lines and lines[0].strip().startswith("# "):
            clean_body = "\n".join([lines[0], "", quote_line, ""] + lines[1:]).strip()
        else:
            clean_body = f"# {clean_title}\n\n{quote_line}\n\n{clean_body}".strip()

    fm_tickers = fm_meta.get("tickers") if isinstance(fm_meta.get("tickers"), (list, str)) else None
    norm_tickers = _normalize_tickers_input(tickers if tickers is not None else fm_tickers, f"{clean_title}\n{clean_body}")

    # Extract concise summary
    summary = scrub_markdown(fm_meta.get("summary") or "")
    if not summary:
        for ln in clean_body.splitlines():
            s = ln.strip()
            if s and not s.startswith(("#", "-", "*", ">", "```", "|")) and len(s) >= 12:
                summary = s
                break
    if not summary:
        summary = clean_title
    if len(summary) > 180:
        summary = summary[:177].rstrip() + "..."

    # Double-check scrubbed text against privacy redline
    if has_private_article_content(
        {
            "filename": "dr_ingest_clean.md",
            "title": clean_title,
            "summary": summary,
            "body_md": clean_body,
            "content": clean_body,
        }
    ):
        raise ValueError("内容包含敏感私人持仓或密钥信息，已拦截入库")

    now_bjt = datetime.now(BJT)
    digest8 = hashlib.sha256(f"{clean_title}\n{clean_body}".encode("utf-8")).hexdigest()[:8]
    filename = f"dr_{now_bjt.strftime('%Y%m%d')}_{digest8}.md"
    if not safe_article_filename(filename):
        raise ValueError(f"非法文件名: {filename}")

    clean_pub = scrub_markdown(publisher) or "Deep Research 深度补充"
    clean_role = scrub_markdown(author_role) or "外部深研闭环"
    frontmatter = build_yaml_frontmatter(
        title=clean_title,
        summary=summary,
        publisher=clean_pub,
        author_role=clean_role,
        report_kind="deep_research",
        score=90,
        tickers=norm_tickers,
        url=resolved_url,
        related_period=clean_period,
    )
    full_doc = f"{frontmatter}\n\n{clean_body}\n"

    target_dir = Path(articles_dir) if articles_dir is not None else DEFAULT_ARTICLES_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(target_dir / filename, full_doc)

    return periodic_article_dict(
        filename=filename,
        title=clean_title,
        summary=summary,
        publisher=clean_pub,
        author_role=clean_role,
        report_kind="deep_research",
        score=90,
        tickers=norm_tickers,
        body_md=clean_body,
        mtime_str=now_bjt.strftime("%Y-%m-%d %H:%M"),
        url=resolved_url,
    )
