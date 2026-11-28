"""Concise Chinese brief builder for English financial news and forum posts (with cache & deterministic fallback)."""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from backend.guardrails import has_private_article_text, scrub_forbidden
from backend.runtime import DATA_DIR, atomic_write_json, file_lock


ZH_BRIEF_CACHE_PATH = DATA_DIR / "zh_brief_cache.json"
_MAX_CACHE_ITEMS = 2500
_CACHE_TTL_SECONDS = 14 * 86400

_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_TICKER_TOKEN_RE = re.compile(r"\$?[A-Z]{1,5}\b")
_LIVE_PREFIX_RE = re.compile(
    r"^(?:live\s+updates?|live\s+blog|watch\s+live|breaking|just\s+in|latest|update)\s*[:：\-—]\s*",
    re.IGNORECASE,
)
_ZH_PREFIX_CLEAN_RE = re.compile(r"^(?:实时更新|现场更新|直播更新|即时更新|最新消息|突发)\s*[：:]\s*")

_EN_ENTITY_ZH: tuple[tuple[str, str], ...] = (
    ("federal reserve", "美联储"),
    ("the fed", "美联储"),
    ("powell", "鲍威尔"),
    ("fomc", "美联储议息"),
    ("treasury yields", "美债收益率"),
    ("treasury yield", "美债收益率"),
    ("nasdaq 100", "纳斯达克100"),
    ("nasdaq", "纳斯达克"),
    ("s&p 500", "标普500"),
    ("dow jones", "道指"),
    ("wall street", "华尔街"),
    ("white house", "白宫"),
    ("trump", "特朗普"),
    ("nvidia", "英伟达"),
    ("openai", "OpenAI"),
    ("anthropic", "Anthropic"),
    ("microsoft", "微软"),
    ("google", "谷歌"),
    ("deepmind", "DeepMind"),
    ("apple", "苹果"),
    ("amazon", "亚马逊"),
    ("tesla", "特斯拉"),
    ("micron", "美光科技"),
    ("bitcoin", "比特币"),
    ("ethereum", "以太坊"),
    ("crypto", "加密资产"),
    ("stablecoin", "稳定币"),
    ("gold", "黄金"),
    ("crude oil", "原油"),
    ("oil", "油价"),
    ("china", "中国"),
    ("hong kong", "港股"),
    ("tariffs", "关税"),
    ("tariff", "关税"),
    ("sanctions", "制裁"),
    ("inflation", "通胀"),
    ("interest rates", "利率"),
    ("interest rate", "利率"),
    ("rate cut", "降息"),
    ("rate hike", "加息"),
    ("recession", "衰退预期"),
    ("earnings", "财报"),
    ("guidance", "业绩指引"),
    ("liquidation", "强平清算"),
    ("semiconductor", "半导体"),
    ("chip", "芯片"),
    ("sec", "SEC监管"),
    ("etf", "ETF"),
    ("fed", "美联储"),
    ("btc", "比特币"),
    ("eth", "以太坊"),
    ("nvda", "英伟达"),
    ("qqq", "纳指QQQ"),
    ("spy", "标普SPY"),
)

_EN_ACTION_ZH: tuple[tuple[str, str], ...] = (
    ("drops below", "跌破"),
    ("slipped below", "下探跌破"),
    ("gives up most of", "回吐大部分"),
    ("gives up", "回吐"),
    ("cuts rates", "降息"),
    ("hikes rates", "加息"),
    ("raises rates", "加息"),
    ("demands probe", "要求调查"),
    ("probes", "调查"),
    ("approves", "批准"),
    ("bans", "禁止"),
    ("warns", "警告"),
    ("surges", "大涨"),
    ("rallies", "反弹"),
    ("jumps", "跳涨"),
    ("plunges", "暴跌"),
    ("tumbles", "大跌"),
    ("drops", "下跌"),
    ("falls", "走低"),
    ("slips", "回落"),
    ("rises", "走强"),
    ("gains", "上涨"),
    ("launches", "发布"),
    ("releases", "发布"),
    ("acquires", "收购"),
    ("files for bankruptcy", "申请破产"),
)


def is_primarily_english(text: str) -> bool:
    """Return True when text is overwhelmingly English rather than Chinese."""
    cleaned = _URL_RE.sub(" ", str(text or "")).strip()
    if not cleaned:
        return False
    cjk_count = sum(1 for ch in cleaned if "\u4e00" <= ch <= "\u9fff")
    ascii_letters = [ch for ch in cleaned if ch.isascii() and ch.isalpha()]
    if len(ascii_letters) < 12:
        return False
    if cjk_count == 0:
        return True
    if cjk_count >= 5:
        return False
    return (cjk_count / max(1, cjk_count + len(ascii_letters))) < 0.12


def _looks_like_half_translation(text: str) -> bool:
    if not text or not re.search(r"[\u4e00-\u9fff]", text):
        return False
    words = re.findall(r"[A-Za-z]+", text)
    lower_en = [w for w in words if w.islower() and len(w) >= 3]
    return len(lower_en) >= 3


def _clean_brief_text(text: str, *, max_len: int = 92) -> str:
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    if not s:
        return ""
    s = _ZH_PREFIX_CLEAN_RE.sub("", s).strip()
    s = scrub_forbidden(s)
    s = (
        s.replace("纳斯达克 100 指数", "纳斯达克100")
        .replace("纳斯达克 100", "纳斯达克100")
        .replace("标准普尔 500 指数", "标普500")
        .replace("标准普尔 500", "标普500")
        .replace("联邦储备", "美联储")
    )
    s = re.sub(r"\s{2,}", " ", s).strip(" ，,;；:：\"'“”")
    if not s or has_private_article_text(s):
        return ""
    if len(s) > max_len:
        s = s[: max_len - 1].rstrip(" ，,;；、") + "…"
        s = scrub_forbidden(s).strip(" ，,;；:：\"'“”")
        if not s or s == "…":
            return ""
    elif s[-1] not in "。！？…":
        s += "。"
    return "" if has_private_article_text(s) else s


def deterministic_zh_brief(title: str, summary: str = "") -> str:
    """Offline bilingual entity + action + numeric digest fallback when translation API is unavailable."""
    raw_t = _LIVE_PREFIX_RE.sub("", str(title or "")).strip()
    raw_s = _LIVE_PREFIX_RE.sub("", str(summary or "")).strip()
    if has_private_article_text(raw_t) or has_private_article_text(raw_s):
        return ""
    combined = f"{raw_t} {raw_s[:220]}".strip()
    low = combined.lower()

    entities: list[str] = []
    for en_tok, zh_tok in _EN_ENTITY_ZH:
        if zh_tok in entities:
            continue
        hit = (
            re.search(rf"(?<![a-z0-9]){re.escape(en_tok)}(?![a-z0-9])", low) is not None
            if len(en_tok) <= 4
            else (en_tok in low)
        )
        if hit:
            entities.append(zh_tok)
        if len(entities) >= 3:
            break

    actions: list[str] = []
    for en_act, zh_act in _EN_ACTION_ZH:
        if zh_act in actions or any(zh_act in ex or ex in zh_act for ex in actions):
            continue
        if re.search(rf"(?<![a-z0-9]){re.escape(en_act)}(?![a-z0-9])", low):
            actions.append(zh_act)
        if len(actions) >= 2:
            break

    nums = re.findall(
        r"(?:[+-]\s*)?(?:\$?\d[\d,]*(?:\.\d+)?\s*(?:%|bps|bp|billion|million|万亿|亿)|\$[\d,]+(?:\.\d+)?)",
        combined,
        re.IGNORECASE,
    )
    uniq_nums: list[str] = []
    for n in nums:
        ns = re.sub(r"^([+-])\s+", r"\1", n.strip())
        if ns and ns not in uniq_nums:
            uniq_nums.append(ns)
        if len(uniq_nums) >= 2:
            break

    if not entities and not actions:
        return ""

    parts: list[str] = []
    if entities:
        parts.append("涉及" + "、".join(entities))
    if actions:
        parts.append("动向：" + "并".join(actions))
    if uniq_nums:
        parts.append("关键数值 " + " / ".join(uniq_nums))
    return _clean_brief_text("，".join(parts))


def _translate_gtx(text: str, *, timeout: float = 4.0) -> str:
    src = re.sub(r"\s+", " ", text).strip()[:280]
    if not src:
        return ""
    url = (
        "https://translate.googleapis.com/translate_a/single"
        "?client=gtx&sl=en&tl=zh-CN&dt=t&q="
        + urllib.parse.quote(src)
    )
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            )
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], list):
        return ""
    translated = "".join(
        str(seg[0]) for seg in payload[0] if isinstance(seg, list) and seg and seg[0]
    ).strip()
    if not translated or not re.search(r"[\u4e00-\u9fff]", translated) or _looks_like_half_translation(translated):
        return ""
    return _clean_brief_text(translated)


def _brief_cache_key(title: str, summary: str) -> str:
    norm_t = _LIVE_PREFIX_RE.sub("", str(title or "")).strip()[:180]
    norm_s = str(summary or "").strip()[:140]
    return hashlib.sha1(f"{norm_t}|{norm_s}".encode("utf-8")).hexdigest()[:20]


def load_zh_brief_cache(path: Path = ZH_BRIEF_CACHE_PATH) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {
                str(k): v
                for k, v in data.items()
                if isinstance(v, dict)
                and isinstance(v.get("brief"), str)
                and v["brief"]
                and not has_private_article_text(v["brief"])
                and (v.get("src") == "net" or not str(v["brief"]).startswith("涉及"))
            }
    except Exception:
        pass
    return {}


def save_zh_brief_cache(cache: dict[str, dict[str, Any]], path: Path = ZH_BRIEF_CACHE_PATH) -> None:
    now = time.time()
    cutoff = now - _CACHE_TTL_SECONDS
    items = [
        (k, v)
        for k, v in cache.items()
        if isinstance(v, dict)
        and isinstance(v.get("brief"), str)
        and v["brief"]
        and v.get("src") == "net"
        and not has_private_article_text(v["brief"])
        and float(v.get("ts") or now) >= cutoff
    ]
    if len(items) > _MAX_CACHE_ITEMS:
        items.sort(key=lambda kv: float(kv[1].get("ts") or 0.0), reverse=True)
        items = items[:_MAX_CACHE_ITEMS]
    with file_lock(path):
        atomic_write_json(path, dict(items))


def _first_sentence(text: str, max_len: int = 130) -> str:
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    if not clean:
        return ""
    m = re.split(r"(?<=[.!?])\s+", clean, maxsplit=1)
    first = m[0] if m else clean
    return first[:max_len]


def _build_zh_brief_ex(
    title: str,
    summary: str = "",
    *,
    allow_network: bool = True,
    cache: dict[str, dict[str, Any]] | None = None,
) -> tuple[str, bool]:
    """Return (brief, from_network); blocks private text before any external network call."""
    t_str = str(title or "").strip()
    s_str = str(summary or "").strip()
    if has_private_article_text(t_str) or has_private_article_text(s_str):
        return ("", False)
    if not is_primarily_english(t_str):
        return ("", False)

    key = _brief_cache_key(t_str, s_str)
    if cache is not None:
        cached = cache.get(key)
        if isinstance(cached, dict) and cached.get("brief"):
            return (str(cached["brief"]), cached.get("src") == "net")

    brief = ""
    from_net = False
    net_ok = allow_network and not bool(os.environ.get("PYTEST_CURRENT_TEST"))
    clean_title = _LIVE_PREFIX_RE.sub("", t_str).strip().rstrip(".")
    lead_summary = _first_sentence(s_str, max_len=120) if is_primarily_english(s_str) else ""
    src_for_net = (
        f"{clean_title}. {lead_summary}"
        if lead_summary and lead_summary.lower() not in clean_title.lower() and len(clean_title) < 95
        else clean_title
    )
    if net_ok and not has_private_article_text(src_for_net):
        try:
            brief = _translate_gtx(src_for_net, timeout=4.0)
            if brief:
                from_net = True
        except Exception:
            brief = ""

    if not brief:
        brief = deterministic_zh_brief(t_str, s_str)

    if brief and from_net and cache is not None:
        cache[key] = {"brief": brief, "ts": round(time.time(), 1), "src": "net"}
    return (brief, from_net)


def build_zh_brief(
    title: str,
    summary: str = "",
    *,
    allow_network: bool = True,
    cache: dict[str, dict[str, Any]] | None = None,
) -> str:
    """Build a concise Chinese brief for an English title/post; returns '' for Chinese or private items."""
    brief, _ = _build_zh_brief_ex(title, summary, allow_network=allow_network, cache=cache)
    return brief


def attach_zh_briefs_batch(
    items: list[dict[str, Any]],
    *,
    title_key: str = "title",
    summary_key: str = "summary",
    allow_network: bool = True,
    max_network_items: int = 60,
    cache_path: Path = ZH_BRIEF_CACHE_PATH,
    persist_cache: bool = True,
) -> None:
    """Populate `zh_brief` in-place on English items using batch concurrency and net-only persistent cache."""
    if not items:
        return
    cache = load_zh_brief_cache(cache_path) if persist_cache else {}
    dirty = False
    pending_net: list[tuple[dict[str, Any], str, str, str]] = []

    for it in items:
        if not isinstance(it, dict):
            continue
        t_str = str(it.get(title_key) or "").strip()
        s_str = str(it.get(summary_key) or "").strip()
        if has_private_article_text(t_str) or has_private_article_text(s_str):
            it.pop("zh_brief", None)
            continue
        existing = str(it.get("zh_brief") or "").strip()
        if existing:
            if has_private_article_text(existing):
                it.pop("zh_brief", None)
            continue
        if not is_primarily_english(t_str):
            continue
        key = _brief_cache_key(t_str, s_str)
        cached = cache.get(key)
        if isinstance(cached, dict) and cached.get("brief"):
            it["zh_brief"] = str(cached["brief"])
            continue
        if (
            allow_network
            and not bool(os.environ.get("PYTEST_CURRENT_TEST"))
            and len(pending_net) < max_network_items
        ):
            pending_net.append((it, t_str, s_str, key))
        else:
            fb = deterministic_zh_brief(t_str, s_str)
            if fb:
                it["zh_brief"] = fb

    if pending_net:
        def _worker(row: tuple[dict[str, Any], str, str, str]) -> tuple[dict[str, Any], str, str, bool]:
            it_obj, t_s, s_s, k_s = row
            br, is_net = _build_zh_brief_ex(t_s, s_s, allow_network=True, cache=None)
            return (it_obj, k_s, br, is_net)

        with ThreadPoolExecutor(max_workers=3) as pool:
            for it_obj, k_s, br, is_net in pool.map(_worker, pending_net):
                if br:
                    it_obj["zh_brief"] = br
                    if is_net:
                        cache[k_s] = {"brief": br, "ts": round(time.time(), 1), "src": "net"}
                        dirty = True

    if persist_cache and dirty:
        try:
            save_zh_brief_cache(cache, cache_path)
        except Exception:
            pass


_deterministic_zh_brief = deterministic_zh_brief
