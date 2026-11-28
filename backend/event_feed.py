"""Persistent single-card event ledger, provenance family grouping, timeline diffing, and offline replay."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from backend.guardrails import has_private_article_text, scrub_forbidden as _scrub_text
from backend.runtime import (
    BJT,
    DATA_DIR,
    UTC,
    atomic_write_json,
    parse_iso_dt as _parse_iso_dt,
    reject_json_constant,
)
from backend.kazike_engine import (
    TIER_LABELS,
    TIER_PRIORITY,
    _ROUNDUP_RE,
    _extract_isolation_keys,
    _extract_numeric_anchors,
    _isolation_conflict,
    normalize_url,
)
from backend.zh_brief import _deterministic_zh_brief, is_primarily_english


LEDGER_VERSION = 1
LEDGER_FILENAME = "event_ledger.json"
PUSH_STATE_FILENAME = "event_push_state.json"
MAX_LEDGER_EVENTS = 600
RETENTION_DAYS = 30
MAX_PUBLIC_EVENT_CARDS = 35

OPINION_SOURCES = frozenset({"X核心观点", "X深度长文", "大佬分享"})

VERIFICATION_LABELS: dict[str, tuple[str, str]] = {
    "unverified": ("未证实", "单一非官方出处，尚待当事方或官方确认"),
    "multi_source": ("多方待确认", "多方报道，待官方确认"),
    "confirmed": ("官方已确认", "已有官方公告或权威一手消息确认"),
    "denied": ("官方已否认", "当事方或官方已否认/澄清，保留旧说法对照"),
    "corrected": ("已更正", "官方或原始出处已更正先前说法"),
    "conflicted": ("说法冲突", "多方权威说法存在冲突，并列保留原文"),
    "roundup": ("盘点综述", "综合盘点与市场综述，独立成卡不合并"),
}

_OFFICIAL_SOURCE_NAMES = frozenset(
    {
        "美联储官方",
        "SEC EDGAR",
        "中国央行",
        "中国人民银行",
        "中国证监会",
        "财政部",
        "美股盘面",
        "BEA",
        "BLS",
    }
)
_OFFICIAL_HOST_HINTS = (
    "federalreserve.gov",
    "sec.gov",
    "treasury.gov",
    "bls.gov",
    "bea.gov",
    "pbc.gov.cn",
    "csrc.gov.cn",
    "gov.cn",
    "hkexnews.hk",
)
_WIRE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("wire:reuters", re.compile(r"(?i)(?:reuters|路透社?|据路透)")),
    ("wire:bloomberg", re.compile(r"(?i)(?:bloomberg|彭博社?|据彭博)")),
    ("wire:wsj", re.compile(r"(?i)(?:wsj|wall\s+street\s+journal|华尔街日报|据华尔街日报)")),
    ("wire:ft", re.compile(r"(?i)(?:financial\s+times|金融时报|据金融时报|\bft\b)")),
    ("wire:ap", re.compile(r"(?i)(?:associated\s+press|美联社|\bap\s+news\b)")),
    ("wire:afp", re.compile(r"(?i)(?:afp|法新社)")),
    ("wire:caixin", re.compile(r"(?i)(?:caixin|财新|据财新)")),
    ("wire:cls", re.compile(r"(?i)(?:财联社|据财联社|\bcls\.cn\b)")),
    ("wire:yicai", re.compile(r"(?i)(?:第一财经|据一财)")),
    ("wire:stcn", re.compile(r"(?i)(?:证券时报|据证券时报)")),
)
_INTERVIEW_CITE_RE = re.compile(
    r"(?i)(?:interview\s+with|spoke\s+to|told\s+|在接受|接受)\s*([A-Za-z0-9\u4e00-\u9fff]{2,18})"
)
_ANON_CITE_RE = re.compile(
    r"(?i)(?:知情人士|消息人士|匿名人士|不愿具名|内部人士|people\s+familiar|sources\s+say|sources\s+familiar|unnamed\s+source|reportedly)"
)
_OFFICIAL_ACTION_RE = re.compile(
    r"(?i)(?:正式确认|公告确认|官方确认|正式宣布|公告披露|发布声明称|official(?:ly)?\s+confirmed|confirmed\s+in\s+(?:a|an)\s+(?:filing|statement)|formally\s+announced|announced\s+that)"
)
_DENIAL_RE = re.compile(
    r"(?i)(?:正式否认|官方否认|发文辟谣|辟谣称|澄清传闻|否认了|否认此前|毫无根据|并无此事|denies\s+report|denied\s+(?:the\s+)?report|refuted|no\s+such\s+plan|not\s+true|false\s+rumor)"
)
_CORRECTION_RE = re.compile(
    r"(?i)(?:发布更正|更正声明|更正此前|修正此前|撤回此前|撤回报道|误报|此前报道有误|纠正此前|correction\s*:|corrects\s+earlier|retracted|erratum)"
)

_STOPWORDS = frozenset(
    {
        "the", "and", "for", "with", "from", "that", "this", "into", "over", "after", "before", "under", "about",
        "says", "said", "will", "would", "could", "should", "have", "has", "had", "been", "being", "are", "was",
        "were", "more", "than", "also", "just", "only", "new", "near", "amid", "as", "on", "in", "at", "to", "of",
        "by", "is", "it", "its", "an", "a", "or", "be", "watch", "now", "mins", "hours", "today", "report",
        "reports", "reported", "according", "market", "markets", "stock", "stocks", "shares",
    }
)

_ENTITY_ALIASES: dict[str, tuple[str, ...]] = {
    "NVDA": ("nvda", "nvidia", "英伟达", "黄仁勋"),
    "AAPL": ("aapl", "apple", "苹果"),
    "MSFT": ("msft", "microsoft", "微软"),
    "TSLA": ("tsla", "tesla", "特斯拉", "马斯克"),
    "GOOG": ("goog", "googl", "google", "alphabet", "谷歌"),
    "AMZN": ("amzn", "amazon", "亚马逊"),
    "META": ("meta", "facebook", "扎克伯格"),
    "AMD": ("amd", "超威半导体"),
    "INTC": ("intc", "intel", "英特尔"),
    "TSM": ("tsm", "tsmc", "台积电"),
    "ASML": ("asml", "阿斯麦"),
    "MU": ("mu", "micron", "美光"),
    "AVGO": ("avgo", "broadcom", "博通"),
    "BABA": ("baba", "alibaba", "阿里巴巴", "阿里"),
    "0700.HK": ("0700.hk", "00700", "tencent", "腾讯"),
    "OPENAI": ("openai", "chatgpt", "gpt-5", "gpt-6", "奥特曼"),
    "ANTHROPIC": ("anthropic", "claude"),
    "DEEPSEEK": ("deepseek", "深度求索"),
    "FED": ("fed", "fomc", "federal reserve", "powell", "美联储", "鲍威尔", "联储"),
    "PBOC": ("pboc", "中国人民银行", "中国央行", "央行", "人行"),
    "SEC": ("sec", "edgar", "美国证监会", "证监会"),
    "BLS": ("bls", "美国劳工统计局", "劳工部"),
    "BEA": ("bea", "美国商务部经济分析局"),
    "BTC": ("btc", "bitcoin", "比特币"),
    "ETH": ("eth", "ethereum", "以太坊"),
    "GOLD": ("gold", "xauusd", "黄金", "金价", "现货黄金"),
}

_CONCEPT_SLOTS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ROLE_CFO", re.compile(r"(?i)(?:(?<![a-z0-9])cfo(?![a-z0-9])|chief\s+financial\s+officer|首席财务官|财务总监)")),
    ("ROLE_CEO", re.compile(r"(?i)(?:(?<![a-z0-9])ceo(?![a-z0-9])|chief\s+executive\s+officer|首席执行官)")),
    ("ACT_STEP_DOWN", re.compile(r"(?i)(?:step(?:ping)?\s+down|resign(?:ed|ing|ation)?|depart(?:s|ed|ure)?|to\s+leave|离职|离任|辞任|卸任|退任|去职)")),
    ("ACT_APPOINT", re.compile(r"(?i)(?:appoint(?:ed|ment)?|named\s+as|succeed(?:ing)?|successor|接任|任命|出任|继任)")),
    ("ACT_EXPORT_RESTRICT", re.compile(r"(?i)(?:export\s+restriction|export\s+control|export\s+ban|出口限制|出口管制|禁售)")),
    ("ACT_ORDER_CUT", re.compile(r"(?i)(?:cut\s+orders?|order\s+cut|reduce\s+orders?|订单调整|削减订单|砍单)")),
    ("ACT_RATE_CUT", re.compile(r"(?i)(?:rate\s+cut|cut(?:s|ting)?\s+(?:policy\s+)?rates?|lower\s+target\s+range|降息)")),
    ("ACT_RATE_HIKE", re.compile(r"(?i)(?:rate\s+hike|hike(?:s|d)?\s+rates?|raise\s+rates?|加息)")),
    ("ACT_RRR_CUT", re.compile(r"(?i)(?:降准|rrr\s+cut)")),
    ("ACT_BUYBACK", re.compile(r"(?i)(?:buyback|share\s+repurchase|回购|注销股份)")),
    ("ACT_LAWSUIT", re.compile(r"(?i)(?:lawsuit|countersuit|sue[sd]?|litigation|反诉|起诉|诉讼)")),
    ("TOPIC_CPI", re.compile(r"(?i)(?:(?<![a-z0-9])cpi(?![a-z0-9])|consumer\s+price|通胀|物价指数)")),
    ("TOPIC_PCE", re.compile(r"(?i)(?:(?<![a-z0-9])pce(?![a-z0-9])|个人消费支出)")),
    ("TOPIC_NFP", re.compile(r"(?i)(?:nonfarm|payrolls?|非农)")),
    ("TOPIC_GDP", re.compile(r"(?i)(?:(?<![a-z0-9])gdp(?![a-z0-9])|国内生产总值)")),
    ("TOPIC_EARNINGS", re.compile(r"(?i)(?:earnings|revenue|(?<![a-z0-9])eps(?![a-z0-9])|guidance|财报|营收|业绩指引)")),
)

_SUBTYPE_PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    ("macro_release", "macro_cpi", re.compile(r"(?i)(?:(?<![a-z0-9])cpi(?![a-z0-9])|consumer\s+price)")),
    ("macro_release", "macro_pce", re.compile(r"(?i)(?:(?<![a-z0-9])pce(?![a-z0-9])|个人消费支出)")),
    ("macro_release", "macro_nfp", re.compile(r"(?i)(?:nonfarm|payroll|unemployment\s+rate|非农|失业率)")),
    ("macro_release", "macro_gdp", re.compile(r"(?i)(?:(?<![a-z0-9])gdp(?![a-z0-9])|国内生产总值)")),
    ("macro_release", "macro_ppi", re.compile(r"(?i)(?:(?<![a-z0-9])ppi(?![a-z0-9])|生产者物价)")),
    ("macro_release", "macro_pmi", re.compile(r"(?i)(?:(?<![a-z0-9])pmi(?![a-z0-9])|采购经理指数)")),
    ("central_bank", "cb_rate", re.compile(r"(?i)(?:fomc|federal\s+reserve|powell|fed\s+minutes|rate\s+cut|rate\s+hike|美联储|鲍威尔|降息|加息|降准|央行决议|议息)")),
    ("corp_announce", "exec_change", re.compile(r"(?i)(?:(?<![a-z0-9])(?:cfo|ceo|cto|coo)(?![a-z0-9])|chief\s+financial|chief\s+executive|首席财务官|首席执行官|step\s+down|resign|appoint|successor|离职|离任|辞任|卸任|退任|去职|接任|任命|高管)")),
    ("corp_announce", "export_order", re.compile(r"(?i)(?:export\s+restrict|export\s+control|cut\s+orders?|order\s+cut|出口限制|出口管制|订单调整|砍单|削减订单)")),
    ("corp_announce", "litigation_probe", re.compile(r"(?i)(?:lawsuit|countersuit|antitrust|subpoena|investigation|probe|反诉|起诉|诉讼|反垄断|立案调查)")),
    ("corp_announce", "capital_action", re.compile(r"(?i)(?:buyback|repurchase|dividend|merger|acquisition|secondary\s+offering|回购|分红|增发|并购|收购)")),
    ("corp_announce", "earnings", re.compile(r"(?i)(?:earnings|quarterly\s+results|guidance|(?<![a-z0-9])(?:10-q|10-k)(?![a-z0-9])|revenue\s+beat|财报|业绩指引|营收|净利润)")),
    ("corp_announce", "sec_filing", re.compile(r"(?i)(?:(?<![a-z0-9])(?:8-k|6-k|20-f)(?![a-z0-9])|sec\s+edgar|form\s+4)")),
    ("regulatory", "regulatory_rule", re.compile(r"(?i)(?:tariff|sanction|regulatory|sec\s+rule|ministry|关税|制裁|监管新规|部委批复|反倾销)")),
)

_MONTH_MAP = {
    "jan": "01", "january": "01", "feb": "02", "february": "02", "mar": "03", "march": "03",
    "apr": "04", "april": "04", "may": "05", "jun": "06", "june": "06", "jul": "07", "july": "07",
    "aug": "08", "august": "08", "sep": "09", "sept": "09", "september": "09",
    "oct": "10", "october": "10", "nov": "11", "november": "11", "dec": "12", "december": "12",
}

def _fmt_bjt(dt: datetime | None) -> str:
    if dt is None:
        return ""
    return dt.astimezone(BJT).strftime("%Y-%m-%d %H:%M+08:00")


def _extract_effective_at(text: str) -> str:
    s = str(text or "")
    m_cn = re.search(r"((?:20\d{2}年)?\d{1,2}月\d{1,2}日)\s*(?:起|正式)?(?:生效|执行|离任|上任)", s)
    if m_cn:
        return m_cn.group(1)
    m_cn2 = re.search(
        r"生效(?:日期|时间|日)?(?:恢复为|改回为|改回|调整为|改为|定为|为|是|[：:\s])*((?:20\d{2}[-/年])?\d{1,2}[-/月]\d{1,2}日?)",
        s,
    )
    if m_cn2:
        return m_cn2.group(1)
    m_en = re.search(
        r"(?i)\beffective\s+(?:on\s+|from\s+)?([A-Z][a-z]{2,8}\.?\s+\d{1,2}(?:,\s*20\d{2})?|20\d{2}-\d{2}-\d{2})",
        s,
    )
    if m_en:
        return m_en.group(1)
    return ""


def _extract_macro_periods(text: str) -> set[str]:
    periods: set[str] = set()
    low = str(text or "").lower()
    for yr, mo in re.findall(r"\b(20\d{2})[-/](0[1-9]|1[0-2])\b", low):
        periods.add(f"{yr}-{mo}")
    for mo in re.findall(r"(?<!\d)(1[0-2]|[1-9])\s*月\s*(?:份)?\s*(?:cpi|pce|ppi|非农|pmi|gdp|社融|通胀|数据)", low):
        periods.add(f"M{int(mo):02d}")
    for mo_word in re.findall(
        r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b\s+(?:cpi|pce|ppi|payrolls|jobs|gdp|pmi|inflation|data)",
        low,
    ):
        if mo_word in _MONTH_MAP:
            periods.add(f"M{_MONTH_MAP[mo_word]}")
    return periods


_OPINION_OR_DIGEST_RE = re.compile(
    r"(?i)(?:隔夜(?:全球)?要闻|全球要闻|每日要闻|需要知道的.*要闻|要闻一览|要闻汇总|盘前必读|晨报|早报|晚报|午报)"
)


def _classify_event(item: dict[str, Any]) -> dict[str, Any]:
    title = str(item.get("title") or "")
    summary = str(item.get("summary") or "")
    source = str(item.get("source") or "")
    text = f"{title} {summary}"
    low = text.lower()

    if source == "美股盘面" or _ROUNDUP_RE.search(title) or _OPINION_OR_DIGEST_RE.search(title):
        return {
            "is_roundup": True,
            "event_type": "roundup",
            "event_subtype": "roundup",
            "entities": set(),
            "concepts": set(),
            "macro_periods": set(),
            "iso_keys": {"sec_acc": set(), "sec_form_date": set(), "fiscal_period": set(), "notice_id": set()},
            "num_anchors": set(),
        }

    event_type = "market_event"
    event_subtype = "general"
    for et, est, pat in _SUBTYPE_PATTERNS:
        if pat.search(text):
            event_type, event_subtype = et, est
            break

    entities: set[str] = set()
    for t in item.get("tickers") or []:
        t_up = str(t or "").strip().upper()
        if t_up:
            entities.add(t_up)
    for canon, aliases in _ENTITY_ALIASES.items():
        for al in aliases:
            if len(al) <= 4 and al.isascii():
                if re.search(rf"(?<![a-z0-9]){re.escape(al)}(?![a-z0-9])", low):
                    entities.add(canon)
                    break
            elif al in low:
                entities.add(canon)
                break
    # Capture explicit capitalized company names in English headlines if no ticker matched
    if not entities:
        for cap in re.findall(r"\b([A-Z][a-z]{2,15})\b", title):
            if cap.lower() not in _STOPWORDS:
                entities.add(cap.upper())

    concepts = {name for name, pat in _CONCEPT_SLOTS if pat.search(text)}
    iso_keys = _extract_isolation_keys(item)
    macro_periods = _extract_macro_periods(text) if event_type == "macro_release" else set()
    num_anchors = _extract_numeric_anchors(text)

    return {
        "is_roundup": False,
        "event_type": event_type,
        "event_subtype": event_subtype,
        "entities": entities,
        "concepts": concepts,
        "macro_periods": macro_periods,
        "iso_keys": iso_keys,
        "num_anchors": num_anchors,
    }


def _tokens(text: str) -> set[str]:
    low = str(text or "").lower()
    en = {w for w in re.findall(r"[a-z][a-z0-9-]{2,}", low) if w not in _STOPWORDS}
    zh_chunks = re.findall(r"[\u4e00-\u9fff]{2,}", low)
    zh_bg = {chunk[i : i + 2] for chunk in zh_chunks for i in range(max(0, len(chunk) - 1))}
    return en | zh_bg


def _token_similarity(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return round(inter / union, 4) if union else 0.0


def _extract_origin_family(item: dict[str, Any], meta: dict[str, Any]) -> tuple[str, str]:
    """Return (origin_family_id, origin_family_label) collapsing reprints, translations & shared interviews."""
    title = str(item.get("title") or "")
    summary = str(item.get("summary") or "")
    source = str(item.get("source") or "").strip() or "市场快讯"
    url = str(item.get("url") or "")
    text = f"{title} {summary}"

    m_iv = _INTERVIEW_CITE_RE.search(text)
    if m_iv:
        outlet = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]", "", m_iv.group(1)).lower()
        if outlet:
            return f"interview:{outlet}", f"{m_iv.group(1).strip()}专访"

    for fam_id, pat in _WIRE_PATTERNS:
        if pat.search(text) or pat.search(source) or pat.search(url):
            label = fam_id.split(":", 1)[1].upper()
            return fam_id, f"{label}口径"

    if _ANON_CITE_RE.search(text):
        ent = ",".join(sorted(meta.get("entities") or []))[:24] or "mkt"
        sub = str(meta.get("event_subtype") or "general")
        return f"anon:{ent}:{sub}", "匿名/知情人士口径"

    norm_src = re.sub(r"\s+", "", source).lower() or "wire"
    return f"src:{norm_src}", source


def _is_official_source(item: dict[str, Any]) -> bool:
    src = str(item.get("source") or "").strip()
    url = str(item.get("url") or "").lower()
    if src in _OFFICIAL_SOURCE_NAMES or str(item.get("source_tier") or "") == "T1":
        return True
    return any(h in url for h in _OFFICIAL_HOST_HINTS)


_EXPLICIT_UNCONFIRMED_RE = re.compile(
    r"(?i)(?:尚未确认|未获确认|未予置评|不予置评|拒绝置评|not\s+yet\s+confirmed|unconfirmed)"
)
_ANON_HEDGE_RE = re.compile(
    r"(?i)(?:知情人士|消息人士|匿名人士|不愿具名|内部人士|传闻称?|曝料|拟于|计划于|或将|预计将|"
    r"people\s+familiar|sources\s+say|sources\s+familiar|unnamed\s+source|reportedly|declined\s+to\s+comment)"
)


def _evidence_stance_signal(item: dict[str, Any]) -> str:
    """Classify an individual piece of evidence as official confirm/deny/correct vs report."""
    title = str(item.get("title") or "")
    summary = str(item.get("summary") or "")
    text = f"{title} {summary}"
    if _EXPLICIT_UNCONFIRMED_RE.search(text):
        return "report"
    official = _is_official_source(item)
    if not official and _ANON_HEDGE_RE.search(text):
        return "report"
    if _CORRECTION_RE.search(text):
        return "correct"
    if _DENIAL_RE.search(text) and (
        official
        or bool(
            re.search(
                r"(?i)(?:官方(?:否认|辟谣|澄清|回应称|发布声明|证实)|发言人|公司声明|公告澄清|声明否认|正式否认|spokesperson)",
                text,
            )
        )
    ):
        return "deny"
    if official or _OFFICIAL_ACTION_RE.search(text):
        return "confirm"
    return "report"


def _extract_facts(item: dict[str, Any], meta: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract verifiable quantitative/effective facts preserving unit, period & scope."""
    title = str(item.get("title") or "")
    summary = str(item.get("summary") or "")
    text = f"{title} {summary}"
    facts: list[dict[str, Any]] = []
    seen_keys: set[str] = set()

    def _add(kind: str, key: str, display: str, *, material: bool = True) -> None:
        if key in seen_keys or not display:
            return
        seen_keys.add(key)
        facts.append({"kind": kind, "key": key, "display": display, "material": material})

    eff = _extract_effective_at(text)
    if eff:
        norm_eff = re.sub(r"\s+", "", eff).lower()
        _add("effective_date", f"eff:{norm_eff}", f"生效日 {eff}", material=True)

    # Percentages & bps with optional YoY/MoM context
    for m in re.finditer(
        r"(?i)((?:同比|环比|核心|yoy|mom|core)?\s*[-+]?\d+(?:\.\d+)?\s*(?:%|个百分点|bps|bp|个基点|基点)(?:\s*(?:同比|环比|yoy|mom))?)",
        text,
    ):
        raw = re.sub(r"\s+", " ", m.group(1)).strip()
        key = "num:" + re.sub(r"\s+", "", raw.lower()).replace("个基点", "bps").replace("基点", "bps").replace("bp", "bps").replace("bpss", "bps")
        _add("metric", key, raw, material=True)

    # Monetary amounts with currency/unit
    for m in re.finditer(
        r"(?i)((?:\$|USD|US\$|HK\$|RMB|CNY)?\s*\d+(?:\.\d+)?\s*(?:billion|million|trillion|B|M|万亿|亿美元|亿元|港元|万元))",
        text,
    ):
        raw = re.sub(r"\s+", " ", m.group(1)).strip()
        if any(ch.isdigit() for ch in raw):
            _add("money", "mon:" + re.sub(r"\s+", "", raw.lower()), raw, material=True)

    # Successor / appointee arrangement (marked non-material if merely naming replacement after resignation)
    m_succ_cn = re.search(r"(?:由|任命|接任者为)\s*([\u4e00-\u9fffA-Za-z·\s]{2,20})(?:出任|接任|担任)?", text)
    m_succ_en = re.search(r"(?i)\bsucceed(?:ed|ing)?\s+by\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})", text)
    if m_succ_en:
        who = m_succ_en.group(1).strip()
        is_mat = meta.get("event_subtype") != "exec_change"
        _add("person", f"succ:{who.lower()}", f"接任安排：{who}", material=is_mat)
    elif m_succ_cn and ("接任" in text or "任命" in text):
        who = m_succ_cn.group(1).strip()
        is_mat = meta.get("event_subtype") != "exec_change"
        _add("person", f"succ:{who.lower()}", f"接任安排：{who}", material=is_mat)

    return facts[:8]


def _evidence_key(item: dict[str, Any]) -> str:
    norm_u = normalize_url(item.get("url"))
    if norm_u:
        return f"u:{norm_u}"
    src = str(item.get("source") or "").strip().lower()
    title = re.sub(r"\s+", " ", str(item.get("title") or "").strip().lower())
    return "t:" + hashlib.sha1(f"{src}|{title}".encode("utf-8")).hexdigest()[:16]


def _subtype_window_days(subtype: str) -> int:
    if subtype == "exec_change":
        return 21
    if subtype.startswith("macro_"):
        return 10
    return 14


def _match_score(ev: dict[str, Any], item: dict[str, Any], meta: dict[str, Any], ev_dt: datetime) -> tuple[float, bool]:
    """Score match confidence [0, 1]; returns (score, hard_conflict)."""
    if ev.get("is_roundup") or meta["is_roundup"]:
        return 0.0, True

    norm_u = normalize_url(item.get("url"))
    if norm_u and norm_u in (ev.get("evidence_urls") or []):
        return 1.0, False

    ev_sub = str(ev.get("event_subtype") or "general")
    it_sub = str(meta.get("event_subtype") or "general")
    if ev_sub != "general" and it_sub != "general" and ev_sub != it_sub:
        return 0.0, True

    # Macro period & SEC/fiscal/notice hard isolation
    ev_macro_p = set(ev.get("macro_periods") or [])
    it_macro_p = set(meta.get("macro_periods") or [])
    if ev_macro_p and it_macro_p and not (ev_macro_p & it_macro_p):
        return 0.0, True

    ev_iso = {k: set(v) for k, v in (ev.get("iso_keys") or {}).items()}
    if _isolation_conflict(meta.get("iso_keys") or {}, ev_iso):
        return 0.0, True

    # Time separation guard
    anchor_dt = _parse_iso_dt(ev.get("last_published_at") or ev.get("first_seen_at"))
    if anchor_dt is not None:
        win = max(_subtype_window_days(ev_sub), _subtype_window_days(it_sub))
        if abs((ev_dt - anchor_dt).total_seconds()) > win * 86400:
            return 0.0, True

    # Entity overlap gate when both have explicit entities
    ev_ents = set(ev.get("entities") or [])
    it_ents = set(meta.get("entities") or [])
    if ev_ents and it_ents and not (ev_ents & it_ents):
        return 0.0, True

    shared_ents = ev_ents & it_ents
    shared_concepts = set(ev.get("concepts") or []) & set(meta.get("concepts") or [])
    shared_nums = set(ev.get("num_anchors") or []) & set(meta.get("num_anchors") or [])

    ev_text = f"{ev.get('title') or ''} {ev.get('summary') or ''}"
    it_text = f"{item.get('title') or ''} {item.get('summary') or ''}"
    tok_sim = _token_similarity(ev_text, it_text)

    if tok_sim >= 0.56:
        return min(0.95, 0.55 + tok_sim * 0.45), False

    if shared_ents:
        concept_score = 0.0
        if len(shared_concepts) >= 2:
            concept_score = 0.72
        elif len(shared_concepts) == 1 and (shared_nums or ev_sub == it_sub != "general"):
            concept_score = 0.66 if shared_nums else 0.54
        elif len(shared_nums) >= 2 or any(str(x).endswith(("%", "bps")) for x in shared_nums):
            concept_score = 0.68
        elif ev_sub == it_sub != "general" and tok_sim >= 0.28:
            concept_score = 0.64
        else:
            concept_score = max(tok_sim, 0.46 if ev_sub == it_sub != "general" else tok_sim)
        return round(max(tok_sim, concept_score), 4), False

    return round(tok_sim, 4), False


def _choose_conclusion_item(ev: dict[str, Any]) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    best_key: tuple[int, int, str, int] = (-1, -1, "", -1)
    for row in ev.get("evidence_items") or []:
        if not isinstance(row, dict):
            continue
        stance = str(row.get("stance_signal") or "report")
        stance_w = 3 if stance in ("confirmed", "confirm", "deny", "correct") else 1
        tier_w = TIER_PRIORITY.get(str(row.get("source_tier") or "T2"), 1)
        ts_str = str(row.get("published_at") or row.get("first_seen_at") or "")
        zh_bonus = 0 if is_primarily_english(str(row.get("title") or "")) else 1
        key = (stance_w, tier_w, ts_str, zh_bonus)
        if key > best_key:
            best_key = key
            best = row
    return best


def _recompute_event_status(
    ev: dict[str, Any],
    new_ev: dict[str, Any],
    new_facts: list[dict[str, Any]],
    now_str: str,
) -> None:
    """Apply monotonic verification state transitions and substantive timeline diffs."""
    if ev.get("is_roundup"):
        ev["verification_status"] = "roundup"
        label, desc = VERIFICATION_LABELS["roundup"]
        ev["verification_label"] = label
        ev["verification_desc"] = desc
        return

    cur_status = str(ev.get("verification_status") or "unverified")
    items = ev.setdefault("evidence_items", [])
    families: dict[str, dict[str, Any]] = {}
    for row in items:
        fid = str(row.get("origin_family") or "src:wire")
        fam = families.setdefault(
            fid,
            {
                "family_id": fid,
                "label": str(row.get("origin_label") or row.get("source") or fid),
                "is_official": False,
                "links": [],
            },
        )
        if row.get("is_official"):
            fam["is_official"] = True
        fam["links"].append(
            {
                "title": _scrub_text(str(row.get("title") or "")),
                "source": str(row.get("source") or ""),
                "url": str(row.get("url") or ""),
                "published_at": str(row.get("published_at") or ""),
            }
        )
    ev["provenance_families"] = list(families.values())
    ev["independent_family_count"] = len(families)
    ev["total_source_count"] = len(items)

    # Check chronological ordering against latest authoritative action and latest substantive update
    new_stance = str(new_ev.get("stance_signal") or "report")
    new_ts = str(new_ev.get("published_at") or new_ev.get("first_seen_at") or "")
    last_auth_ts = str(ev.get("last_authoritative_at") or "")
    last_sub_ts = str(ev.get("last_substantive_at") or "")
    is_late_older_arrival = bool(
        cur_status in ("confirmed", "denied", "corrected", "conflicted")
        and last_auth_ts
        and new_ts
        and new_ts < last_auth_ts
    ) or bool(
        new_stance == "report"
        and last_sub_ts
        and new_ts
        and new_ts < last_sub_ts
    )

    prev_version = int(ev.get("substantive_version") or 1)
    known_facts: dict[str, dict[str, Any]] = ev.setdefault("known_facts", {})
    timeline: list[dict[str, Any]] = ev.setdefault("timeline", [])
    prior_claims: list[dict[str, Any]] = ev.setdefault("prior_claims", [])
    conflict_claims: list[dict[str, Any]] = ev.setdefault("conflict_claims", [])

    if is_late_older_arrival:
        # Out-of-order earlier arrival only appends provenance; never regresses state or latest timestamp
        return

    added_facts: list[dict[str, Any]] = []
    has_material_fact = False
    for f in new_facts:
        fk = f["key"]
        if fk not in known_facts:
            known_facts[fk] = f
            added_facts.append(f)
            if f.get("material", True):
                has_material_fact = True

    next_status = cur_status
    transition_note = ""
    transition_material = False

    if new_stance == "correct":
        if cur_status != "corrected":
            prior_title = _scrub_text(str(ev.get("title") or ""))
            if prior_title and not any(c.get("claim") == prior_title for c in prior_claims):
                prior_claims.append(
                    {
                        "label": "已更正旧说",
                        "claim": prior_title,
                        "status": "corrected",
                        "recorded_at": new_ts or now_str,
                    }
                )
        next_status = "corrected"
        ev["last_authoritative_at"] = max(last_auth_ts, new_ts)
        transition_note = f"官方/原文更正：{ _scrub_text(str(new_ev.get('title') or '')) }"
        transition_material = True
    elif new_stance == "deny":
        if cur_status == "confirmed":
            next_status = "conflicted"
            for claim_row in (
                {"source": str(ev.get("source") or "前序消息"), "claim": _scrub_text(str(ev.get("title") or "")), "url": str(ev.get("url") or "")},
                {"source": str(new_ev.get("source") or "最新澄清"), "claim": _scrub_text(str(new_ev.get("title") or "")), "url": str(new_ev.get("url") or "")},
            ):
                if claim_row["claim"] and not any(c.get("claim") == claim_row["claim"] for c in conflict_claims):
                    conflict_claims.append(claim_row)
            transition_note = f"说法冲突（保留并列）：{ _scrub_text(str(new_ev.get('title') or '')) }"
        else:
            if cur_status != "denied":
                prior_title = _scrub_text(str(ev.get("title") or ""))
                if prior_title and not any(c.get("claim") == prior_title for c in prior_claims):
                    prior_claims.append(
                        {
                            "label": "已否认旧说",
                            "claim": prior_title,
                            "status": "denied",
                            "recorded_at": new_ts or now_str,
                        }
                    )
            next_status = "denied"
            transition_note = f"当事方/官方否认：{ _scrub_text(str(new_ev.get('title') or '')) }"
        ev["last_authoritative_at"] = max(last_auth_ts, new_ts)
        transition_material = True
    elif new_stance == "confirm":
        if cur_status == "denied":
            next_status = "conflicted"
            for claim_row in (
                {"source": str(ev.get("source") or "否认方"), "claim": _scrub_text(str(ev.get("title") or "")), "url": str(ev.get("url") or "")},
                {"source": str(new_ev.get("source") or "确认方"), "claim": _scrub_text(str(new_ev.get("title") or "")), "url": str(new_ev.get("url") or "")},
            ):
                if claim_row["claim"] and not any(c.get("claim") == claim_row["claim"] for c in conflict_claims):
                    conflict_claims.append(claim_row)
            transition_note = f"说法冲突（保留并列）：{ _scrub_text(str(new_ev.get('title') or '')) }"
            transition_material = True
        elif cur_status not in ("confirmed", "corrected", "conflicted"):
            next_status = "confirmed"
            transition_note = f"官方确认：{ _scrub_text(str(new_ev.get('title') or '')) }"
            transition_material = True
        ev["last_authoritative_at"] = max(last_auth_ts, new_ts)
    else:
        if cur_status in ("confirmed", "denied", "corrected", "conflicted"):
            next_status = cur_status
        elif len(families) >= 2:
            next_status = "multi_source"
        else:
            next_status = "unverified"

    ev["verification_status"] = next_status
    label, desc = VERIFICATION_LABELS.get(next_status, ("未证实", "单一非官方出处，尚待当事方或官方确认"))
    ev["verification_label"] = label
    ev["verification_desc"] = desc

    # Append timeline entry only on substantive transition, effective-date revision, or new verifiable facts
    eff_ts = new_ts or now_str
    cur_eff = str(ev.get("effective_at") or "")
    new_eff = str(new_ev.get("effective_at") or "")
    can_update_eff = bool(
        new_eff
        and new_eff != cur_eff
        and (
            not cur_eff
            or (
                (not last_sub_ts or eff_ts >= last_sub_ts)
                and (
                    new_stance in ("correct", "confirm", "deny")
                    or cur_status not in ("confirmed", "denied", "corrected", "conflicted")
                )
            )
        )
    )
    if can_update_eff and not any(f.get("kind") == "effective_date" for f in added_facts):
        added_facts.append(
            {
                "kind": "effective_date",
                "key": f"eff_rev:{new_eff}",
                "display": f"生效日 {new_eff}",
                "material": True,
            }
        )
        has_material_fact = True

    fact_note = "；".join(f["display"] for f in added_facts)
    if transition_note or added_facts:
        change_parts = [p for p in (transition_note, f"新增事实：{fact_note}" if fact_note else "") if p]
        change_text = " · ".join(change_parts)
        is_mat = transition_material or has_material_fact
        next_ver = prev_version + 1
        is_newest_sub = not last_sub_ts or eff_ts >= last_sub_ts or new_stance in ("deny", "correct")
        ev["substantive_version"] = next_ver
        ev["last_substantive_at"] = max(last_sub_ts, eff_ts)
        if can_update_eff:
            ev["effective_at"] = new_eff
        if is_newest_sub:
            ev["latest_change"] = _scrub_text(change_text)[:180]
        timeline.append(
            {
                "seq": next_ver,
                "version": next_ver,
                "published_at": str(new_ev.get("published_at") or ""),
                "first_seen_at": str(new_ev.get("first_seen_at") or now_str),
                "effective_at": str(new_ev.get("effective_at") or ""),
                "status": next_status,
                "status_label": label,
                "summary": _scrub_text(change_text)[:160],
                "source": str(new_ev.get("source") or ""),
                "url": str(new_ev.get("url") or ""),
                "is_material_change": is_mat,
                "is_backfill": bool(new_ev.get("is_backfill")),
            }
        )
        timeline.sort(key=lambda r: (str(r.get("published_at") or r.get("first_seen_at") or ""), int(r.get("seq") or 0)))
        if len(timeline) > 10:
            del timeline[:-10]

    rep = _choose_conclusion_item(ev)
    if rep is not None and not ev.get("is_roundup"):
        if next_status in ("denied", "corrected") and str(new_ev.get("stance_signal")) in ("deny", "correct"):
            ev["title"] = _scrub_text(str(new_ev.get("title") or ev.get("title") or ""))
            ev["summary"] = _scrub_text(str(new_ev.get("summary") or ""))
            if new_ev.get("zh_brief"):
                ev["zh_brief"] = _scrub_text(str(new_ev.get("zh_brief") or ""))
        elif next_status == "confirmed":
            ev["title"] = _scrub_text(str(rep.get("title") or ev.get("title") or ""))
            ev["summary"] = _scrub_text(str(rep.get("summary") or ev.get("summary") or ""))
            if rep.get("zh_brief"):
                ev["zh_brief"] = _scrub_text(str(rep.get("zh_brief") or ""))
        ev["source"] = str(rep.get("source") or ev.get("source") or "")
        ev["source_tier"] = str(rep.get("source_tier") or ev.get("source_tier") or "T2")
        ev["source_tier_label"] = TIER_LABELS.get(ev["source_tier"], "T2媒体KOL")
        ev["url"] = str(rep.get("url") or ev.get("url") or "")
        ev["attention_score"] = max(int(ev.get("attention_score") or 0), int(rep.get("attention_score") or 0))
        if rep.get("importance") == "高":
            ev["importance"] = "高"
        if rep.get("selected"):
            ev["selected"] = True


def _build_initial_event(item: dict[str, Any], meta: dict[str, Any], ev_record: dict[str, Any], facts: list[dict[str, Any]], now_str: str) -> dict[str, Any]:
    seed = f"{meta['event_type']}|{meta['event_subtype']}|{ev_record['evidence_key']}"
    event_id = "evt-" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]
    stance = ev_record["stance_signal"]

    if meta["is_roundup"]:
        status = "roundup"
    elif stance == "correct":
        status = "corrected"
    elif stance == "deny":
        status = "denied"
    elif stance == "confirm":
        status = "confirmed"
    else:
        status = "unverified"
    label, desc = VERIFICATION_LABELS[status]

    title_clean = _scrub_text(str(item.get("title") or ""))
    zh_brief = _scrub_text(str(item.get("zh_brief") or ""))
    if not zh_brief and is_primarily_english(title_clean):
        zh_brief = _scrub_text(_deterministic_zh_brief(title_clean, str(item.get("summary") or "")))

    first_summary = _scrub_text(title_clean)
    if facts:
        first_summary += " · " + "；".join(f["display"] for f in facts[:3])

    pub_at = ev_record["published_at"]
    last_auth_at = (pub_at or now_str) if status in ("confirmed", "denied", "corrected") else ""
    tier = str(item.get("source_tier") or "T2")
    ev = {
        "id": event_id,
        "event_id": event_id,
        "event_type": meta["event_type"],
        "event_subtype": meta["event_subtype"],
        "is_roundup": meta["is_roundup"],
        "title": title_clean,
        "summary": _scrub_text(str(item.get("summary") or "")),
        "zh_brief": zh_brief,
        "why_it_matters": _scrub_text(str(item.get("why_it_matters") or "")),
        "source": str(item.get("source") or "市场快讯"),
        "source_tier": tier,
        "source_tier_label": TIER_LABELS.get(tier, "T2媒体KOL"),
        "category": str(item.get("category") or "macro"),
        "direction": str(item.get("direction") or "neutral"),
        "importance": str(item.get("importance") or ("高" if int(item.get("attention_score") or 0) >= 75 else "中")),
        "attention_score": int(item.get("attention_score") or 0),
        "selected": bool(item.get("selected")),
        "tickers": [str(t).upper() for t in (item.get("tickers") or []) if t][:6],
        "topics": [str(t) for t in (item.get("topics") or []) if t][:6],
        "url": str(ev_record.get("url") or ""),
        "time_bjt": pub_at or ev_record["first_seen_at"],
        "published_at": pub_at,
        "first_seen_at": ev_record["first_seen_at"],
        "effective_at": ev_record["effective_at"],
        "last_published_at": pub_at,
        "last_substantive_at": pub_at or ev_record["first_seen_at"],
        "last_authoritative_at": last_auth_at,
        "is_backfill": bool(ev_record["is_backfill"]),
        "verification_status": status,
        "verification_label": label,
        "verification_desc": desc,
        "substantive_version": 1,
        "latest_change": "",
        "pending_review": False,
        "related_event_ids": [],
        "entities": sorted(meta["entities"]),
        "concepts": sorted(meta["concepts"]),
        "macro_periods": sorted(meta["macro_periods"]),
        "num_anchors": sorted(meta["num_anchors"]),
        "iso_keys": {k: sorted(v) for k, v in meta["iso_keys"].items()},
        "known_facts": {f["key"]: f for f in facts},
        "prior_claims": [],
        "conflict_claims": [],
        "provenance_families": [
            {
                "family_id": ev_record["origin_family"],
                "label": ev_record["origin_label"],
                "is_official": ev_record["is_official"],
                "links": [
                    {
                        "title": title_clean,
                        "source": str(item.get("source") or ""),
                        "url": str(ev_record.get("url") or ""),
                        "published_at": pub_at,
                    }
                ],
            }
        ],
        "independent_family_count": 1,
        "total_source_count": 1,
        "evidence_keys": [ev_record["evidence_key"]],
        "evidence_sigs": {ev_record["evidence_key"]: [ev_record["content_sig"]]},
        "evidence_urls": [ev_record["norm_url"]] if ev_record["norm_url"] else [],
        "evidence_items": [ev_record],
        "timeline": [
            {
                "seq": 1,
                "version": 1,
                "published_at": pub_at,
                "first_seen_at": ev_record["first_seen_at"],
                "effective_at": ev_record["effective_at"],
                "status": status,
                "status_label": label,
                "summary": first_summary[:220],
                "source": str(item.get("source") or ""),
                "url": str(ev_record.get("url") or ""),
                "is_material_change": True,
                "is_backfill": bool(ev_record["is_backfill"]),
            }
        ],
    }
    return ev


def _is_valid_ledger_event_schema(ev: Any) -> bool:
    if not isinstance(ev, dict):
        return False
    eid = str(ev.get("event_id") or ev.get("id") or "").strip()
    title = ev.get("title")
    if not eid or not isinstance(title, str) or not title.strip():
        return False
    for k in ("substantive_version", "independent_family_count", "total_source_count", "attention_score", "base_score"):
        if k in ev and ev[k] is not None:
            v = ev[k]
            if type(v) is not int or v < (1 if k == "substantive_version" else 0) or v > 10**9:
                return False
    if "timeline" in ev and ev["timeline"] is not None:
        tl = ev["timeline"]
        if not isinstance(tl, list):
            return False
        for step in tl:
            if not isinstance(step, dict):
                return False
            for tk in ("seq", "version"):
                if tk in step and step[tk] is not None:
                    tv = step[tk]
                    if type(tv) is not int or tv < 1 or tv > 10**9:
                        return False
    return True


def _try_load_ledger(path: Path) -> tuple[dict[str, Any], str]:
    """Load ledger and report explicit status: 'ok', 'missing', or 'error'."""
    try:
        if not path.exists():
            return {"version": LEDGER_VERSION, "events": {}}, "missing"
        if path.stat().st_size > 32 * 1024 * 1024:
            return {"version": LEDGER_VERSION, "events": {}}, "error"
        raw = json.loads(path.read_text(encoding="utf-8"), parse_constant=reject_json_constant)
        if (
            isinstance(raw, dict)
            and type(raw.get("version")) is int
            and raw.get("version") == LEDGER_VERSION
            and isinstance(raw.get("events"), dict)
            and all(isinstance(k, str) and _is_valid_ledger_event_schema(v) for k, v in raw["events"].items())
        ):
            return raw, "ok"
        return {"version": LEDGER_VERSION, "events": {}}, "error"
    except (OSError, ValueError, OverflowError):
        return {"version": LEDGER_VERSION, "events": {}}, "error"


def _load_ledger(path: Path) -> dict[str, Any]:
    ledger, _status = _try_load_ledger(path)
    return ledger


def _trim_evidence_items(ev: dict[str, Any], max_items: int = 16) -> None:
    items = ev.get("evidence_items")
    if not isinstance(items, list) or len(items) <= max_items:
        return
    official_indices = [
        idx
        for idx, row in enumerate(items)
        if isinstance(row, dict) and bool(row.get("is_official"))
    ]
    keep_idx: set[int] = set(official_indices[-max_items:])
    auth_indices = [
        idx
        for idx, row in enumerate(items)
        if idx not in keep_idx
        and isinstance(row, dict)
        and str(row.get("stance_signal") or "report") in ("confirm", "deny", "correct")
    ]
    for idx in reversed(auth_indices):
        if len(keep_idx) >= max_items:
            break
        keep_idx.add(idx)
    for idx in range(len(items) - 1, -1, -1):
        if len(keep_idx) >= max_items:
            break
        keep_idx.add(idx)
    ev["evidence_items"] = [items[idx] for idx in sorted(keep_idx)]


def _prune_ledger(events: dict[str, dict[str, Any]], now_bjt: datetime) -> dict[str, dict[str, Any]]:
    cutoff = now_bjt - timedelta(days=RETENTION_DAYS)
    kept: list[tuple[str, dict[str, Any]]] = []
    for eid, ev in events.items():
        if not isinstance(ev, dict):
            continue
        dt = _parse_iso_dt(ev.get("last_substantive_at") or ev.get("first_seen_at")) or now_bjt
        if dt >= cutoff:
            _trim_evidence_items(ev, 16)
            if isinstance(ev.get("evidence_keys"), list) and len(ev["evidence_keys"]) > 80:
                ev["evidence_keys"] = ev["evidence_keys"][-80:]
            if isinstance(ev.get("evidence_urls"), list) and len(ev["evidence_urls"]) > 80:
                ev["evidence_urls"] = ev["evidence_urls"][-80:]
            raw_sigs = ev.get("evidence_sigs")
            if isinstance(raw_sigs, dict):
                active_keys = set(ev.get("evidence_keys") or [])
                for row in ev.get("evidence_items") or []:
                    if isinstance(row, dict) and row.get("evidence_key"):
                        active_keys.add(str(row["evidence_key"]))
                ev["evidence_sigs"] = {
                    str(k): [str(s) for s in v[-8:] if s]
                    for k, v in raw_sigs.items()
                    if k in active_keys and isinstance(v, list)
                }
            if isinstance(ev.get("timeline"), list) and len(ev["timeline"]) > 10:
                ev["timeline"] = ev["timeline"][-10:]
            kept.append((eid, ev))
    kept.sort(key=lambda kv: str(kv[1].get("last_substantive_at") or ""), reverse=True)
    return dict(kept[:MAX_LEDGER_EVENTS])


def ingest_events_into_ledger(
    raw_items: list[dict[str, Any]],
    ledger: dict[str, Any],
    *,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Ingest factual signals idempotently into an in-memory event ledger."""
    now_bjt = (now_utc or datetime.now(UTC)).astimezone(BJT)
    now_str = _fmt_bjt(now_bjt)
    events: dict[str, dict[str, Any]] = dict(ledger.get("events") or {})

    new_events = 0
    reused_or_replayed = 0
    reprint_collapsed = 0
    low_conf_linked = 0
    roundups = 0
    opinions_skipped = 0

    for raw in raw_items or []:
        if not isinstance(raw, dict):
            continue
        src_name = str(raw.get("source") or "").strip()
        if src_name in OPINION_SOURCES:
            opinions_skipped += 1
            continue
        title = str(raw.get("title") or "").strip()
        if not title:
            continue

        meta = _classify_event(raw)
        if meta["is_roundup"]:
            roundups += 1

        pub_raw = raw.get("time_bjt")
        pub_dt = _parse_iso_dt(pub_raw) if str(pub_raw or "").strip() else None
        pub_str = _fmt_bjt(pub_dt) if pub_dt is not None else ""
        ev_dt = pub_dt or now_bjt
        is_backfill = bool(raw.get("is_backfill")) or (pub_dt is not None and (now_bjt - pub_dt) > timedelta(hours=48))

        fam_id, fam_label = _extract_origin_family(raw, meta)
        norm_u = normalize_url(raw.get("url"))
        ekey = _evidence_key(raw)
        stance = _evidence_stance_signal(raw)
        facts = _extract_facts(raw, meta)
        zh_brief = _scrub_text(str(raw.get("zh_brief") or ""))
        if not zh_brief and is_primarily_english(title):
            zh_brief = _scrub_text(_deterministic_zh_brief(title, str(raw.get("summary") or "")))

        summary_clean = _scrub_text(str(raw.get("summary") or ""))[:160]
        eff_val = _extract_effective_at(f"{title} {raw.get('summary') or ''}")
        fact_keys_sig = ",".join(sorted(f["key"] for f in facts))
        content_sig = hashlib.sha1(
            f"{_scrub_text(title)}|{summary_clean}|{stance}|{eff_val}|{fact_keys_sig}".encode("utf-8")
        ).hexdigest()[:12]
        ev_record = {
            "evidence_key": ekey,
            "content_sig": content_sig,
            "norm_url": norm_u,
            "url": str(raw.get("url") or norm_u or ""),
            "title": _scrub_text(title),
            "summary": summary_clean,
            "zh_brief": zh_brief,
            "why_it_matters": _scrub_text(str(raw.get("why_it_matters") or ""))[:120],
            "source": src_name or "市场快讯",
            "source_tier": str(raw.get("source_tier") or "T2"),
            "category": str(raw.get("category") or "macro"),
            "direction": str(raw.get("direction") or "neutral"),
            "tickers": [str(t).upper() for t in (raw.get("tickers") or []) if t][:6],
            "topics": [str(t) for t in (raw.get("topics") or []) if t][:6],
            "attention_score": int(raw.get("attention_score") or 0),
            "importance": str(raw.get("importance") or "中"),
            "selected": bool(raw.get("selected")),
            "published_at": pub_str,
            "first_seen_at": now_str,
            "effective_at": eff_val,
            "is_backfill": is_backfill,
            "origin_family": fam_id,
            "origin_label": fam_label,
            "is_official": _is_official_source(raw),
            "stance_signal": stance,
        }

        # Idempotent exact key check across ledger; re-evaluate only newer or authoritative updates
        existing_exact: dict[str, Any] | None = None
        for ev in events.values():
            if ekey in (ev.get("evidence_keys") or []):
                existing_exact = ev
                break
        if existing_exact is not None:
            sig_map = existing_exact.setdefault("evidence_sigs", {})
            seen_sigs = list(sig_map.get(ekey) or [])
            prior_item = next(
                (
                    r
                    for r in (existing_exact.get("evidence_items") or [])
                    if isinstance(r, dict) and r.get("evidence_key") == ekey
                ),
                None,
            )
            latest_sig = seen_sigs[-1] if seen_sigs else (
                str(prior_item.get("content_sig") or "") if isinstance(prior_item, dict) else ""
            )
            if content_sig == latest_sig:
                reused_or_replayed += 1
                continue

            prior_item_ts = str(prior_item.get("published_at") or "") if isinstance(prior_item, dict) else ""
            cand_ts = pub_str or now_str
            last_auth_ts = str(existing_exact.get("last_authoritative_at") or "")
            last_sub_ts = str(existing_exact.get("last_substantive_at") or "")
            cur_ev_status = str(existing_exact.get("verification_status") or "unverified")
            is_older_same_url = bool(
                (prior_item_ts and pub_str and pub_str < prior_item_ts)
                or (
                    cur_ev_status in ("confirmed", "denied", "corrected", "conflicted")
                    and last_auth_ts
                    and cand_ts < last_auth_ts
                )
                or (stance == "report" and last_sub_ts and cand_ts < last_sub_ts)
            )
            if is_older_same_url:
                reused_or_replayed += 1
                continue

            sig_map[ekey] = [*seen_sigs, content_sig][-8:]
            existing_exact["evidence_items"] = [
                r for r in (existing_exact.get("evidence_items") or [])
                if not (isinstance(r, dict) and r.get("evidence_key") == ekey)
            ] + [ev_record]
            if pub_str and (not existing_exact.get("last_published_at") or pub_str > str(existing_exact["last_published_at"])):
                existing_exact["last_published_at"] = pub_str
            _recompute_event_status(existing_exact, ev_record, facts, now_str)
            continue

        best_ev: dict[str, Any] | None = None
        best_score = 0.0
        review_links: list[str] = []
        same_day_links: list[str] = []

        for eid, ev in events.items():
            score, hard_conflict = _match_score(ev, raw, meta, ev_dt)
            if score >= 0.62 and score > best_score:
                best_ev = ev
                best_score = score
            elif 0.42 <= score < 0.62 and not hard_conflict:
                review_links.append(eid)
            elif (
                not meta["is_roundup"]
                and not ev.get("is_roundup")
                and (set(ev.get("entities") or []) & meta["entities"])
                and meta["event_subtype"] != ev.get("event_subtype")
            ):
                anchor_dt = _parse_iso_dt(ev.get("last_published_at") or ev.get("first_seen_at"))
                if anchor_dt is not None and abs((ev_dt - anchor_dt).total_seconds()) <= 48 * 3600:
                    same_day_links.append(eid)

        if best_ev is not None:
            prev_fams = {r.get("origin_family") for r in (best_ev.get("evidence_items") or [])}
            best_ev.setdefault("evidence_keys", []).append(ekey)
            sig_map = best_ev.setdefault("evidence_sigs", {})
            sig_map[ekey] = [*(sig_map.get(ekey) or []), content_sig][-8:]
            if norm_u and norm_u not in best_ev.setdefault("evidence_urls", []):
                best_ev["evidence_urls"].append(norm_u)
            best_ev.setdefault("evidence_items", []).append(ev_record)
            best_ev["entities"] = sorted(set(best_ev.get("entities") or []) | meta["entities"])
            best_ev["concepts"] = sorted(set(best_ev.get("concepts") or []) | meta["concepts"])
            best_ev["num_anchors"] = sorted(set(best_ev.get("num_anchors") or []) | meta["num_anchors"])
            best_ev["tickers"] = list(
                dict.fromkeys([*(best_ev.get("tickers") or []), *(str(t).upper() for t in (raw.get("tickers") or []) if t)])
            )[:6]
            best_ev["topics"] = list(
                dict.fromkeys([*(best_ev.get("topics") or []), *(str(t) for t in (raw.get("topics") or []) if t)])
            )[:6]
            if pub_str and (not best_ev.get("last_published_at") or pub_str > str(best_ev["last_published_at"])):
                best_ev["last_published_at"] = pub_str
            if fam_id in prev_fams:
                reprint_collapsed += 1
            _recompute_event_status(best_ev, ev_record, facts, now_str)
        else:
            ev_obj = _build_initial_event(raw, meta, ev_record, facts, now_str)
            links = list(dict.fromkeys([*review_links, *same_day_links]))[:6]
            if review_links:
                ev_obj["pending_review"] = True
                low_conf_linked += 1
            if links:
                ev_obj["related_event_ids"] = links
                for lid in links:
                    target = events.get(lid)
                    if isinstance(target, dict):
                        rel = list(dict.fromkeys([*(target.get("related_event_ids") or []), ev_obj["event_id"]]))[:6]
                        target["related_event_ids"] = rel
                        if lid in review_links:
                            target["pending_review"] = True
            events[ev_obj["event_id"]] = ev_obj
            new_events += 1

    pruned = _prune_ledger(events, now_bjt)
    out_ledger = {"version": LEDGER_VERSION, "updated_at_bjt": now_str, "events": pruned}
    stats = {
        "raw_items": len(raw_items or []),
        "opinions_skipped": opinions_skipped,
        "total_events": len(pruned),
        "new_events": new_events,
        "reused_or_replayed": reused_or_replayed,
        "reprint_collapsed": reprint_collapsed,
        "low_conf_linked": low_conf_linked,
        "roundups": roundups,
        "multi_source_count": sum(1 for e in pruned.values() if e.get("verification_status") == "multi_source"),
        "confirmed_count": sum(1 for e in pruned.values() if e.get("verification_status") == "confirmed"),
        "denied_or_corrected_count": sum(
            1 for e in pruned.values() if e.get("verification_status") in ("denied", "corrected", "conflicted")
        ),
    }
    return {"ledger": out_ledger, "stats": stats}


def split_event(
    ledger: dict[str, Any],
    event_id: str,
    evidence_keys: list[str],
    *,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Split mistakenly merged evidence items into a standalone event keeping full provenance."""
    events = dict((ledger or {}).get("events") or {})
    src_ev = events.get(event_id)
    if not isinstance(src_ev, dict):
        raise KeyError(f"unknown event_id: {event_id}")
    split_set = {str(k) for k in evidence_keys if k}
    if not split_set:
        raise ValueError("evidence_keys must be non-empty")

    keep_items: list[dict[str, Any]] = []
    move_items: list[dict[str, Any]] = []
    for rec in src_ev.get("evidence_items") or []:
        if isinstance(rec, dict) and rec.get("evidence_key") in split_set:
            move_items.append(dict(rec))
        elif isinstance(rec, dict):
            keep_items.append(dict(rec))
    if not keep_items or not move_items:
        raise ValueError("split requires non-empty kept and moved evidence sets")

    events.pop(event_id, None)
    now_bjt = (now_utc or datetime.now(UTC)).astimezone(BJT)

    def _rebuild_from(records: list[dict[str, Any]], forced_id: str | None = None) -> dict[str, Any]:
        first = dict(records[0])
        if not first.get("category") and src_ev.get("category"):
            first["category"] = src_ev["category"]
        if not first.get("tickers") and src_ev.get("tickers"):
            first["tickers"] = list(src_ev["tickers"])
        if not first.get("topics") and src_ev.get("topics"):
            first["topics"] = list(src_ev["topics"])
        if not first.get("direction") and src_ev.get("direction"):
            first["direction"] = src_ev["direction"]
        meta = _classify_event(first)
        facts = _extract_facts(first, meta)
        obj = _build_initial_event(first, meta, first, facts, _fmt_bjt(now_bjt))
        if forced_id:
            obj["id"] = forced_id
            obj["event_id"] = forced_id
        for extra in records[1:]:
            emeta = _classify_event(extra)
            efacts = _extract_facts(extra, emeta)
            ek = str(extra.get("evidence_key") or _evidence_key(extra))
            obj["evidence_keys"].append(ek)
            if extra.get("content_sig"):
                obj.setdefault("evidence_sigs", {})[ek] = [str(extra["content_sig"])]
            if extra.get("norm_url") and extra["norm_url"] not in obj["evidence_urls"]:
                obj["evidence_urls"].append(extra["norm_url"])
            obj["evidence_items"].append(extra)
            obj["entities"] = sorted(set(obj.get("entities") or []) | emeta["entities"])
            obj["concepts"] = sorted(set(obj.get("concepts") or []) | emeta["concepts"])
            obj["num_anchors"] = sorted(set(obj.get("num_anchors") or []) | emeta["num_anchors"])
            if extra.get("tickers"):
                obj["tickers"] = list(dict.fromkeys([*(obj.get("tickers") or []), *extra["tickers"]]))[:6]
            if extra.get("topics"):
                obj["topics"] = list(dict.fromkeys([*(obj.get("topics") or []), *extra["topics"]]))[:6]
            ex_pub = str(extra.get("published_at") or "")
            if ex_pub and (not obj.get("last_published_at") or ex_pub > str(obj["last_published_at"])):
                obj["last_published_at"] = ex_pub
            _recompute_event_status(obj, extra, efacts, _fmt_bjt(now_bjt))
        return obj

    kept_ev = _rebuild_from(keep_items, forced_id=event_id)
    moved_ev = _rebuild_from(move_items)
    if moved_ev["event_id"] == kept_ev["event_id"]:
        alt_id = "evt-" + hashlib.sha1(f"split|{event_id}|{move_items[0]['evidence_key']}".encode("utf-8")).hexdigest()[:12]
        moved_ev["id"] = alt_id
        moved_ev["event_id"] = alt_id

    kept_ev["related_event_ids"] = list(dict.fromkeys([*(kept_ev.get("related_event_ids") or []), moved_ev["event_id"]]))[:6]
    moved_ev["related_event_ids"] = [kept_ev["event_id"]]
    events[kept_ev["event_id"]] = kept_ev
    events[moved_ev["event_id"]] = moved_ev
    return {"version": LEDGER_VERSION, "updated_at_bjt": _fmt_bjt(now_bjt), "events": events}


def _rank_ledger_cards(events: dict[str, dict[str, Any]], *, limit: int = MAX_PUBLIC_EVENT_CARDS) -> list[dict[str, Any]]:
    ordered = sorted(
        events.values(),
        key=lambda e: (
            0 if e.get("is_backfill") else 1,
            1 if e.get("selected") else 0,
            1 if e.get("verification_status") in ("confirmed", "denied", "corrected", "conflicted") else 0,
            int(e.get("attention_score") or 0),
            str(e.get("last_substantive_at") or e.get("first_seen_at") or ""),
        ),
        reverse=True,
    )
    return [dict(e) for e in ordered[:limit]]


def _safe_int(val: Any, default: int = 0) -> int:
    if val is None or isinstance(val, bool):
        return default
    if isinstance(val, float) and not math.isfinite(val):
        return default
    try:
        out = int(val)
        return default if abs(out) > 10**9 else out
    except (TypeError, ValueError, OverflowError):
        return default


def project_event_card(card: Any) -> dict[str, Any] | None:
    """Strictly allowlist public-safe fields for an event card; drop private data."""
    if not isinstance(card, dict):
        return None
    eid = str(card.get("event_id") or card.get("id") or "").strip()
    title = _scrub_text(str(card.get("title") or ""))
    if not eid or not title or has_private_article_text(title):
        return None

    clean_timeline = []
    for row in (card.get("timeline") or [])[-6:]:
        if not isinstance(row, dict):
            continue
        summ = _scrub_text(str(row.get("summary") or ""))
        if not summ or has_private_article_text(summ):
            continue
        seq_val = _safe_int(row.get("seq"), 1) or 1
        clean_timeline.append(
            {
                "seq": seq_val,
                "version": _safe_int(row.get("version"), seq_val) or seq_val,
                "published_at": str(row.get("published_at") or ""),
                "first_seen_at": str(row.get("first_seen_at") or ""),
                "effective_at": str(row.get("effective_at") or ""),
                "status": str(row.get("status") or "unverified"),
                "status_label": _scrub_text(str(row.get("status_label") or "")),
                "summary": summ[:140],
                "source": str(row.get("source") or ""),
                "url": str(row.get("url") or ""),
                "is_material_change": bool(row.get("is_material_change", True)),
                "is_backfill": bool(row.get("is_backfill")),
            }
        )

    clean_families = []
    for fam in (card.get("provenance_families") or [])[:5]:
        if not isinstance(fam, dict):
            continue
        links = []
        for lk in (fam.get("links") or [])[:4]:
            if not isinstance(lk, dict):
                continue
            l_title = _scrub_text(str(lk.get("title") or ""))
            if not l_title or has_private_article_text(l_title):
                continue
            links.append(
                {
                    "title": l_title[:90],
                    "source": str(lk.get("source") or ""),
                    "url": str(lk.get("url") or ""),
                    "published_at": str(lk.get("published_at") or ""),
                }
            )
        if links:
            clean_families.append(
                {
                    "family_id": str(fam.get("family_id") or ""),
                    "label": _scrub_text(str(fam.get("label") or "")),
                    "is_official": bool(fam.get("is_official")),
                    "links": links,
                }
            )

    clean_prior = [
        {
            "label": _scrub_text(str(c.get("label") or "已更正旧说")),
            "claim": _scrub_text(str(c.get("claim") or ""))[:180],
            "status": str(c.get("status") or "corrected"),
            "recorded_at": str(c.get("recorded_at") or ""),
        }
        for c in (card.get("prior_claims") or [])[:4]
        if isinstance(c, dict) and c.get("claim") and not has_private_article_text(str(c.get("claim")))
    ]
    clean_conflict = [
        {
            "source": str(c.get("source") or ""),
            "claim": _scrub_text(str(c.get("claim") or ""))[:180],
            "url": str(c.get("url") or ""),
        }
        for c in (card.get("conflict_claims") or [])[:4]
        if isinstance(c, dict) and c.get("claim") and not has_private_article_text(str(c.get("claim")))
    ]

    summary_clean = _scrub_text(str(card.get("summary") or ""))[:100]
    if has_private_article_text(summary_clean):
        summary_clean = ""
    zh_brief = _scrub_text(str(card.get("zh_brief") or ""))[:92]
    if has_private_article_text(zh_brief):
        zh_brief = ""
    why_clean = _scrub_text(str(card.get("why_it_matters") or ""))[:80]
    if has_private_article_text(why_clean):
        why_clean = ""

    return {
        "id": eid,
        "event_id": eid,
        "event_type": str(card.get("event_type") or "market_event"),
        "event_subtype": str(card.get("event_subtype") or "general"),
        "is_roundup": bool(card.get("is_roundup")),
        "title": title,
        "summary": summary_clean,
        "zh_brief": zh_brief,
        "why_it_matters": why_clean,
        "source": str(card.get("source") or ""),
        "source_tier": str(card.get("source_tier") or "T2"),
        "source_tier_label": str(card.get("source_tier_label") or "T2媒体KOL"),
        "category": str(card.get("category") or "macro"),
        "direction": str(card.get("direction") or "neutral"),
        "importance": str(card.get("importance") or "中"),
        "attention_score": _safe_int(item_or_val := card.get("attention_score"), 0),
        "base_score": _safe_int(card.get("base_score"), _safe_int(item_or_val, 0)),
        "selected": bool(card.get("selected")),
        "tickers": [str(t).upper() for t in (card.get("tickers") or []) if isinstance(t, str) and t][:6],
        "topics": [str(t) for t in (card.get("topics") or []) if isinstance(t, str) and t][:6],
        "url": str(card.get("url") or ""),
        "time_bjt": str(card.get("last_substantive_at") or card.get("time_bjt") or ""),
        "published_at": str(card.get("published_at") or ""),
        "first_seen_at": str(card.get("first_seen_at") or ""),
        "effective_at": str(card.get("effective_at") or ""),
        "last_substantive_at": str(card.get("last_substantive_at") or ""),
        "is_backfill": bool(card.get("is_backfill")),
        "verification_status": str(card.get("verification_status") or "unverified"),
        "verification_label": _scrub_text(str(card.get("verification_label") or "未证实")),
        "verification_desc": _scrub_text(str(card.get("verification_desc") or "")),
        "substantive_version": max(1, _safe_int(card.get("substantive_version"), 1)),
        "latest_change": _scrub_text(str(card.get("latest_change") or "")),
        "pending_review": bool(card.get("pending_review")),
        "related_event_ids": [str(x) for x in (card.get("related_event_ids") or []) if isinstance(x, str)][:6],
        "independent_family_count": max(1, _safe_int(card.get("independent_family_count"), 1)),
        "total_source_count": max(1, _safe_int(card.get("total_source_count"), 1)),
        "prior_claims": clean_prior,
        "conflict_claims": clean_conflict,
        "provenance_families": clean_families,
        "timeline": clean_timeline,
    }


def project_event_cards(cards: Any) -> list[dict[str, Any]]:
    if not isinstance(cards, list):
        return []
    out: list[dict[str, Any]] = []
    for c in cards[:MAX_PUBLIC_EVENT_CARDS]:
        clean = project_event_card(c)
        if clean is not None:
            out.append(clean)
    return out


def update_event_feed(
    raw_items: list[dict[str, Any]],
    *,
    data_dir: Path | str = DATA_DIR,
    now_utc: datetime | None = None,
    persist: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Update persistent ledger with pre-dedup items and return projected public event cards."""
    root_dir = Path(data_dir)
    ledger_path = root_dir / LEDGER_FILENAME
    if persist:
        ledger, load_status = _try_load_ledger(ledger_path)
    else:
        ledger, load_status = {"version": LEDGER_VERSION, "events": {}}, "missing"

    if persist and load_status == "error":
        mem_res = ingest_events_into_ledger(raw_items, {"version": LEDGER_VERSION, "events": {}}, now_utc=now_utc)
        ranked = _rank_ledger_cards(mem_res["ledger"]["events"])
        return project_event_cards(ranked), {**mem_res["stats"], "ledger_persist_skipped": True}

    res = ingest_events_into_ledger(raw_items, ledger, now_utc=now_utc)
    persist_skipped = False
    if persist:
        import uuid

        ledger_obj = res["ledger"]
        ev_map = ledger_obj["events"]
        encoded = json.dumps(ledger_obj, ensure_ascii=False, separators=(",", ":"))
        while len(encoded.encode("utf-8")) > 16 * 1024 * 1024 and len(ev_map) > 1:
            oldest_k = min(ev_map.keys(), key=lambda k: str(ev_map[k].get("last_substantive_at") or ""))
            ev_map.pop(oldest_k, None)
            encoded = json.dumps(ledger_obj, ensure_ascii=False, separators=(",", ":"))
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = ledger_path.with_name(f".{ledger_path.name}.{os.getpid()}.{uuid.uuid4().hex[:6]}.tmp")
        try:
            tmp_path.write_text(encoded, encoding="utf-8", newline="")
            os.replace(tmp_path, ledger_path)
        except OSError:
            persist_skipped = True
        finally:
            tmp_path.unlink(missing_ok=True)
    ranked = _rank_ledger_cards(res["ledger"]["events"])
    stats = dict(res["stats"])
    if persist_skipped:
        stats["ledger_persist_skipped"] = True
    return project_event_cards(ranked), stats


def try_load_public_event_cards_from_ledger(data_dir: Path | str = DATA_DIR) -> list[dict[str, Any]] | None:
    """Return projected ledger cards when readable, or None if missing/locked/corrupt."""
    path = Path(data_dir) / LEDGER_FILENAME
    ledger, status = _try_load_ledger(path)
    if status != "ok":
        return None
    events = ledger.get("events") or {}
    if not events:
        return []
    try:
        return project_event_cards(_rank_ledger_cards(events))
    except (ValueError, TypeError, KeyError, AttributeError):
        return None


def load_public_event_cards_from_ledger(data_dir: Path | str = DATA_DIR) -> list[dict[str, Any]]:
    return try_load_public_event_cards_from_ledger(data_dir) or []


def evaluate_event_notifications(
    events: list[dict[str, Any]],
    push_state_path: Path | str,
    *,
    enabled: bool = False,
    channels: tuple[str, ...] = ("feishu_dm",),
    allow_unverified_high: bool = False,
    held_symbols: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Evaluate event notifications with disabled-by-default gate, version dedup, and prior-recipient corrections."""
    state_path = Path(push_state_path)
    try:
        push_state = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(push_state, dict):
            push_state = {}
    except (OSError, ValueError):
        push_state = {}
    sent_map: dict[str, dict[str, Any]] = dict(push_state.get("sent") or {})
    if not enabled:
        return []

    held_up = {str(s).upper() for s in (held_symbols or set()) if s}
    dispatches: list[dict[str, Any]] = []
    changed = False

    for ev in events or []:
        if not isinstance(ev, dict):
            continue
        eid = str(ev.get("event_id") or ev.get("id") or "")
        if not eid or ev.get("is_backfill") or ev.get("is_roundup"):
            continue
        ver = int(ev.get("substantive_version") or 1)
        status = str(ev.get("verification_status") or "unverified")
        timeline = ev.get("timeline") or []
        latest_step = timeline[-1] if isinstance(timeline, list) and timeline else {}
        is_material = bool(latest_step.get("is_material_change", True))

        rec = dict(sent_map.get(eid) or {})
        prior_channels = list(dict.fromkeys([str(c) for c in (rec.get("prior_channels") or []) if c]))
        delivered_versions: dict[str, list[str]] = {
            str(k): list(v) for k, v in (rec.get("versions") or {}).items() if isinstance(v, list)
        }
        already_for_ver = set(delivered_versions.get(str(ver), []))

        # Correction / denial always reaches every channel that previously received a claim for this event
        if status in ("denied", "corrected") and prior_channels:
            targets = [ch for ch in prior_channels if ch not in already_for_ver]
            if targets:
                title = f"【更正通知】{ev.get('title') or ''}" if status == "corrected" else f"【官方否认】{ev.get('title') or ''}"
                for ch in targets:
                    dispatches.append(
                        {"event_id": eid, "version": ver, "channel": ch, "kind": "correction", "title": title}
                    )
                    already_for_ver.add(ch)
                delivered_versions[str(ver)] = sorted(already_for_ver)
                sent_map[eid] = {"prior_channels": prior_channels, "versions": delivered_versions}
                changed = True
            continue

        # Normal substantive alerts require material change and sufficient authority
        if not is_material:
            continue
        if status == "multi_source" and ver == 1:
            # Mere increase in corroborating sources without substantive change does not trigger alerts
            continue
        tier = str(ev.get("source_tier") or "T2")
        score = int(ev.get("attention_score") or 0)
        is_held = bool(held_up & {str(t).upper() for t in (ev.get("tickers") or [])})
        if status == "unverified":
            if not (allow_unverified_high and score >= 82 and tier in ("T1", "T1_5")):
                continue
        elif status not in ("confirmed", "conflicted"):
            if not is_held and score < 82:
                continue

        targets = [str(ch) for ch in channels if ch and str(ch) not in already_for_ver]
        if not targets:
            continue
        prefix = "【未证实】" if status == "unverified" else (f"【{ev.get('verification_label') or '事件更新'}】")
        title = f"{prefix}{ev.get('title') or ''}"
        for ch in targets:
            dispatches.append({"event_id": eid, "version": ver, "channel": ch, "kind": "event_update", "title": title})
            already_for_ver.add(ch)
            if ch not in prior_channels:
                prior_channels.append(ch)
        delivered_versions[str(ver)] = sorted(already_for_ver)
        sent_map[eid] = {"prior_channels": prior_channels, "versions": delivered_versions}
        changed = True

    if changed:
        atomic_write_json(state_path, {"version": 1, "sent": sent_map})
    return dispatches


def replay_public_history(data_dir: Path | str = DATA_DIR) -> dict[str, Any]:
    """Replay available public snapshots strictly offline without mutating production ledgers."""
    d_dir = Path(data_dir)
    snapshots: list[tuple[str, dict[str, Any]]] = []

    gens_dir = d_dir / "generations"
    if gens_dir.is_dir():
        for gdir in sorted(gens_dir.glob("gen_*")):
            st_file = gdir / "state.json"
            if st_file.is_file():
                try:
                    snapshots.append((gdir.name, json.loads(st_file.read_text(encoding="utf-8"))))
                except (OSError, ValueError):
                    pass

    main_state = d_dir / "cockpit_state.json"
    if main_state.is_file():
        try:
            snapshots.append(("cockpit_state", json.loads(main_state.read_text(encoding="utf-8"))))
        except (OSError, ValueError):
            pass

    ledger: dict[str, Any] = {"version": LEDGER_VERSION, "events": {}}
    last_stats: dict[str, Any] = {}
    total_raw_seen = 0
    for _label, st in snapshots:
        pool = list(st.get("signal_candidates") or st.get("signals") or [])
        total_raw_seen += len(pool)
        res = ingest_events_into_ledger(pool, ledger)
        ledger = res["ledger"]
        last_stats = res["stats"]

    events = ledger.get("events") or {}
    multi_source_examples = [
        {
            "event_id": e["event_id"],
            "status": e["verification_label"],
            "title": e["title"],
            "families": e["independent_family_count"],
            "sources": e["total_source_count"],
        }
        for e in events.values()
        if e.get("independent_family_count", 1) > 1 or e.get("substantive_version", 1) > 1
    ][:5]
    return {
        "snapshots_replayed": len(snapshots),
        "total_raw_seen": total_raw_seen,
        "unique_event_cards": len(events),
        "stats": last_stats,
        "pending_review_count": sum(1 for e in events.values() if e.get("pending_review")),
        "multi_source_examples": multi_source_examples,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Event Feed ledger & offline replay utility")
    parser.add_argument("--replay", action="store_true", help="Replay local public snapshots offline")
    parser.add_argument("--data-dir", default=str(DATA_DIR), help="Data directory path")
    args = parser.parse_args()
    if args.replay:
        print(json.dumps(replay_public_history(args.data_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
