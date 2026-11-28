from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

COCKPIT_ROOT = Path(__file__).resolve().parent.parent
if str(COCKPIT_ROOT) not in sys.path:
    sys.path.insert(0, str(COCKPIT_ROOT))

from backend import guru_sources, market_synthesis  # noqa: E402
from backend.public_snapshot import has_private_article_content, safe_article_filename  # noqa: E402

FORBIDDEN_SUBSTRINGS: tuple[str, ...] = (
    "regime",
    "sleeve",
    "tier-1",
    "arbitration",
    "e21",
    "e32",
    "e33",
    "待补",
    "待刷新",
    "n/a",
    "测算口径",
    "公式解释",
    "公式说明",
    "逻辑解释",
    "联动说明",
    "方法论说明",
)


def _assert_no_forbidden(payload: Any) -> None:
    dumped = json.dumps(payload, ensure_ascii=False).lower()
    for bad in FORBIDDEN_SUBSTRINGS:
        assert bad not in dumped, f"Found forbidden token {bad!r} in payload"


class _FakeResponse:
    def __init__(self, status_code: int, *, text: str = "", url: str = "https://example.com/feed") -> None:
        self.status_code = status_code
        self.content = text.encode("utf-8")
        self.text = text
        self.encoding = "utf-8"
        self.url = url


def test_guru_long_substack_cdata_parsing_and_private_scrub(tmp_path: Path) -> None:
    now_utc = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
    long_paragraphs = "".join(
        f"<p>Section {i}: NVDA Blackwell &amp; TSM CoWoS demand surges as hyperscalers expand AI CapEx by 25%. "
        f"Context token: 128000 and api_key=demo_value should never trigger privacy leak.</p>"
        for i in range(420)
    )
    assert len(long_paragraphs) > 55_000

    substack_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>SemiAnalysis</title>
    <item>
      <title><![CDATA[NVDA Blackwell Supply Chain &amp; HBM4 Deep Dive]]></title>
      <link>https://www.semianalysis.com/p/nvda-blackwell-hbm4</link>
      <pubDate>Tue, 24 Nov 2026 10:00:00 GMT</pubDate>
      <description><![CDATA[Summary of NVDA and TSM CoWoS & HBM4 expansion for 2026-2027.]]></description>
      <content:encoded><![CDATA[{long_paragraphs}<p>FINAL_TAIL_MARKER_PRESERVED</p>]]></content:encoded>
    </item>
  </channel>
</rss>"""

    test_feeds = [
        {
            "id": "semianalysis",
            "publisher": "SemiAnalysis · 半导体深研",
            "author": "SemiAnalysis",
            "author_role": "AI算力与半导体供应链",
            "url": "https://www.semianalysis.com/feed",
            "html_url": "https://www.semianalysis.com/",
            "category": "ai",
            "tier": "T1",
            "max_age_days": "45",
            "background": "全球顶级半导体与 AI 算力供应链深度研究专栏。",
        }
    ]

    def _fake_get(url: str, headers: dict[str, str] | None = None, timeout: int = 8):
        assert timeout == 8
        return _FakeResponse(200, text=substack_xml, url=url)

    articles_dir = tmp_path / "articles"
    cache_path = tmp_path / "guru_cache.json"
    with patch.object(guru_sources.requests, "get", side_effect=_fake_get):
        arts = guru_sources.collect_guru_articles(
            force_refresh=True,
            timeout=8,
            articles_dir=articles_dir,
            cache_path=cache_path,
            feeds=test_feeds,
            now_utc=now_utc,
        )

    assert len(arts) == 1
    art = arts[0]
    assert art["source"] == "大佬分享"
    assert "NVDA" in art["tickers"]
    assert "TSM" in art["tickers"]
    assert len(art["body_md"]) > 15_000
    assert "大佬背景导读" in art["body_md"]
    assert "中文核心要点" in art["body_md"]
    assert "原文全文" in art["body_md"]
    assert not has_private_article_content(art)
    _assert_no_forbidden(art)

    fname = art["filename"]
    assert fname.startswith("guru_") and fname.endswith(".md")
    assert safe_article_filename(fname) == fname
    md_path = articles_dir / fname
    assert md_path.is_file()
    raw_md = md_path.read_text(encoding="utf-8")
    assert "author_role:" in raw_md
    assert "tickers:" in raw_md
    assert "publisher:" in raw_md
    assert not has_private_article_content({"filename": fname, "content": raw_md})

    sigs = guru_sources.collect_guru_signals(articles=arts)
    assert len(sigs) == 1
    sig = sigs[0]
    assert sig["source"] == "大佬分享"
    assert sig["category"] in {"macro", "equities", "ashare", "crypto", "ai"}
    assert sig["direction"] in {"bullish", "bearish", "neutral"}
    assert sig["importance"] in {"高", "中"}
    _assert_no_forbidden(sig)


def test_guru_feed_isolation_on_timeout_malformed_and_ssrf(tmp_path: Path) -> None:
    now_utc = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
    good_atom = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Arthur Hayes</title>
  <entry>
    <title>Dollar Liquidity &amp; BTC Surge Outlook</title>
    <link rel="alternate" href="https://cryptohayes.substack.com/p/dollar-liquidity"/>
    <updated>2026-11-24T08:00:00Z</updated>
    <content type="html"><![CDATA[<p>Federal Reserve liquidity expansion drives Bitcoin BTC rally higher above prior resistance.</p>]]></content>
  </entry>
</feed>"""

    mixed_feeds = [
        {
            "id": "bad_timeout",
            "publisher": "Timeout Feed",
            "author_role": "测试",
            "url": "https://timeout.example.com/feed",
            "category": "macro",
            "tier": "T1_5",
        },
        {
            "id": "bad_xml",
            "publisher": "Broken XML",
            "author_role": "测试",
            "url": "https://broken.example.com/feed",
            "category": "macro",
            "tier": "T1_5",
        },
        {
            "id": "ssrf_local",
            "publisher": "Localhost Feed",
            "author_role": "测试",
            "url": "http://127.0.0.1:8080/feed",
            "category": "macro",
            "tier": "T1_5",
        },
        {
            "id": "cryptohayes",
            "publisher": "Arthur Hayes · 加密宏观",
            "author_role": "加密宏观与美元流动性",
            "url": "https://cryptohayes.substack.com/feed",
            "category": "crypto",
            "tier": "T1",
        },
    ]

    def _fake_get(url: str, headers: dict[str, str] | None = None, timeout: int = 8):
        if "timeout.example.com" in url:
            raise TimeoutError("simulated timeout")
        if "broken.example.com" in url:
            return _FakeResponse(200, text="<rss><channel><item><title>unclosed", url=url)
        if "127.0.0.1" in url:
            raise AssertionError("SSRF URL should have been blocked before requests.get")
        return _FakeResponse(200, text=good_atom, url=url)

    with patch.object(guru_sources.requests, "get", side_effect=_fake_get):
        arts = guru_sources.collect_guru_articles(
            force_refresh=True,
            articles_dir=tmp_path / "articles",
            cache_path=tmp_path / "cache.json",
            feeds=mixed_feeds,
            now_utc=now_utc,
        )

    assert len(arts) == 1
    assert arts[0]["feed_id"] == "cryptohayes"
    assert "BTC" in arts[0]["tickers"]


def test_market_synthesis_resonance_divergence_and_forbidden_guard() -> None:
    macro_fred = [
        {"id": "fed_net_liquidity", "value_display": "$5.85T", "raw_value": 5.85, "change_display": "+$40B", "change_num": 40.0},
        {"id": "dollar", "value_display": "104.2", "raw_value": 104.2, "change_display": "-0.4%", "change_num": -0.4},
        {"id": "fedfunds", "value_display": "3.88%", "raw_value": 3.88, "change_display": "0 bps", "change_num": 0.0},
        {"id": "us_10y", "value_display": "4.92%", "raw_value": 4.92, "change_display": "-4 bps", "change_num": -0.04},
        {"id": "us_2y", "value_display": "4.60%", "raw_value": 4.60, "change_display": "-3 bps", "change_num": -0.03},
        {"id": "us_10y_2y_spread", "value_display": "+0.32%", "raw_value": 0.32},
        {"id": "cpi_yoy", "value_display": "2.6%", "raw_value": 2.6},
        {"id": "btc_spot", "value_display": "$92,400", "raw_value": 92400.0, "change_display": "+2.4%", "change_num": 2.4},
        {"id": "gold", "value_display": "$2,680", "raw_value": 2680.0, "change_display": "+0.6%", "change_num": 0.6},
        {"id": "vix", "value_display": "16.8", "raw_value": 16.8, "change_display": "-1.2", "change_num": -1.2},
        {"id": "wti", "value_display": "$69.5", "raw_value": 69.5, "change_display": "-0.8%", "change_num": -0.8},
    ]
    us_equities = {
        "as_of": "2026-11-25 16:00 美东",
        "summary": "标普500与纳指分化，半导体承压但软件活跃。",
        "indices": [
            {"symbol": "SPY", "name": "标普500 ETF", "price": 716.7, "change_pct": 0.45},
            {"symbol": "QQQ", "name": "纳指100 ETF", "price": 633.9, "change_pct": -0.35},
        ],
        "mag7_and_ai": [
            {"symbol": "NVDA", "name": "英伟达", "price": 149.3, "change_pct": -3.91},
            {"symbol": "MSFT", "name": "微软", "price": 489.9, "change_pct": 1.65},
            {"symbol": "TSLA", "name": "特斯拉", "price": 342.1, "change_pct": 2.40},
        ],
    }
    correlations = {
        "as_of": "2026-11-25",
        "pairs": [
            {"id": "corr_btc_sp500", "pair": "BTC × 标普500", "corr_30d": 0.68, "corr_90d": 0.35, "delta": 0.33, "divergence": "显著背离", "note": "BTC与美股同步弹性放大"},
            {"id": "corr_gold_dollar", "pair": "黄金 × 美元指数", "corr_30d": -0.52, "corr_90d": -0.48, "delta": -0.04, "divergence": "正常联动", "note": "负相关稳定"},
        ],
    }
    signals = [
        {
            "id": "sig-nvda-1",
            "source": "财联社电报",
            "category": "ai",
            "direction": "bullish",
            "importance": "高",
            "title": "台积电 CoWoS 先进封装明年产能预期再次上修，NVDA 需求强劲",
            "tickers": ["NVDA", "TSM"],
            "topics": ["算力芯片"],
        },
        {
            "id": "sig-msft-1",
            "source": "CNBC 美股",
            "category": "equities",
            "direction": "bullish",
            "importance": "高",
            "title": "Microsoft Azure AI revenue growth beats Wall Street expectations",
            "tickers": ["MSFT"],
            "topics": ["七巨头·云厂"],
        },
        {
            "id": "sig-btc-1",
            "source": "CoinDesk",
            "category": "crypto",
            "direction": "bullish",
            "importance": "高",
            "title": "Spot Bitcoin ETF records fifth straight day of net inflow",
            "tickers": ["BTC"],
            "topics": ["ETF·监管"],
        },
    ]
    kol_digest = [
        {
            "handle": "semiguru",
            "name": "芯片研究员",
            "stance": "偏多",
            "tickers": ["NVDA", "TSM"],
            "headline": "看好 NVDA/TSM：CoWoS 排产延续景气，短线回调属洗盘",
            "reliability_score": 84,
            "reliability_badge": "高胜率",
            "win_rate_20d": 76.5,
        },
        {
            "handle": "cloudalpha",
            "name": "云厂追踪",
            "stance": "偏多",
            "tickers": ["MSFT", "AMZN"],
            "headline": "云厂商企业级 Agent 商业化加速兑现",
            "reliability_score": 78,
            "reliability_badge": "稳健参考",
            "win_rate_20d": 68.0,
        },
        {
            "handle": "macrohawk",
            "name": "宏观鹰派",
            "stance": "偏空",
            "tickers": ["US10Y"],
            "headline": "长端美债供给压力仍在，5%附近反复拉锯",
            "reliability_score": 72,
            "reliability_badge": "稳健参考",
            "win_rate_20d": 64.0,
        },
    ]
    kol_scorecards = [
        {"handle": "semiguru", "name": "芯片研究员", "reliability_score": 84, "reliability_badge": "高胜率", "win_rate_20d": 76.5},
        {"handle": "cloudalpha", "name": "云厂追踪", "reliability_score": 78, "reliability_badge": "稳健参考", "win_rate_20d": 68.0},
    ]
    articles = [
        {
            "filename": "guru_semianalysis_abc123.md",
            "title": "Blackwell & CoWoS Ramp Analysis",
            "source": "大佬分享",
            "publisher": "SemiAnalysis · 半导体深研",
            "direction": "bullish",
            "tickers": ["NVDA", "TSM"],
            "score": 88,
        },
        {
            "filename": "guru_cryptohayes_def456.md",
            "title": "Dollar Liquidity Tailwinds for BTC",
            "source": "大佬分享",
            "publisher": "Arthur Hayes · 加密宏观",
            "direction": "bullish",
            "tickers": ["BTC"],
            "score": 82,
        },
    ]
    stock_profiles = {
        "NVDA": {"symbol": "NVDA", "price": 149.3, "change_pct": -3.91, "bias20_pct": -6.2, "bias50_pct": -8.5},
        "MSFT": {"symbol": "MSFT", "price": 489.9, "change_pct": 1.65, "bias20_pct": 2.4, "bias50_pct": 4.1},
    }
    risk_gate = {"light": "YELLOW", "label": "黄灯控险", "long_risk_cap": 0.5}

    res = market_synthesis.build_market_synthesis(
        macro_fred=macro_fred,
        us_equities=us_equities,
        correlations=correlations,
        signals=signals,
        kol_digest=kol_digest,
        kol_scorecards=kol_scorecards,
        articles=articles,
        risk_gate=risk_gate,
        stock_profiles=stock_profiles,
    )

    assert res["as_of"] == "2026-11-25 16:00 美东"
    assert res["stance"] in {"偏多", "偏空", "中性"}
    assert isinstance(res["headline_verdict"], str) and len(res["headline_verdict"]) > 12

    dims = res["market_dimensions"]
    assert [d["name"] for d in dims] == ["美元流动性", "利率与通胀", "美股与算力", "加密与避险"]
    for d in dims:
        assert set(d.keys()) >= {"id", "name", "status", "tone", "metric", "implication"}
        assert 2 <= len(d["status"]) <= 6
        assert d["tone"] in {"bull", "bear", "warn", "neutral"}

    links = res["cross_source_links"]
    assert 5 <= len(links) <= 10
    allowed_patterns = {"多源顺风共振", "空头风控共振", "盘面与消息背离", "多空分歧焦点"}
    for lk in links:
        assert set(lk.keys()) >= {
            "id", "focus", "tickers", "pattern", "tone", "confidence",
            "conclusion", "news_evidence", "kol_evidence", "guru_evidence",
            "market_check", "watch_trigger", "related_article_file",
        }
        assert lk["pattern"] in allowed_patterns
        assert 0 <= lk["confidence"] <= 100
        assert isinstance(lk["news_evidence"], list) and len(lk["news_evidence"]) >= 1
        assert isinstance(lk["kol_evidence"], list) and len(lk["kol_evidence"]) >= 1
        assert isinstance(lk["guru_evidence"], list) and len(lk["guru_evidence"]) >= 1

    by_id = {lk["id"]: lk for lk in links}
    # NVDA news/KOL/guru are bullish while price is -3.91% and bias20 -6.2% -> divergence!
    assert by_id["link_nvda_ai_compute"]["pattern"] == "盘面与消息背离"
    assert by_id["link_nvda_ai_compute"]["related_article_file"] == "guru_semianalysis_abc123.md"
    assert any("76%" in ev or "77%" in ev for ev in by_id["link_nvda_ai_compute"]["kol_evidence"])

    # MSFT news/KOL/price are all aligned bullish -> resonance!
    assert by_id["link_megacap_cloud"]["pattern"] == "多源顺风共振"

    alerts = res["divergence_alerts"]
    assert len(alerts) >= 1
    for al in alerts:
        assert set(al.keys()) >= {"title", "detail", "tickers", "tone"}

    _assert_no_forbidden(res)


def test_market_synthesis_handles_empty_inputs_gracefully() -> None:
    res = market_synthesis.build_market_synthesis(
        macro_fred=None,
        us_equities=None,
        correlations=None,
        signals=None,
        kol_digest=None,
        kol_scorecards=None,
        articles=None,
        risk_gate=None,
        stock_profiles=None,
    )
    assert res["stance"] in {"偏多", "偏空", "中性"}
    assert len(res["market_dimensions"]) == 4
    assert 5 <= len(res["cross_source_links"]) <= 10
    assert len(res["divergence_alerts"]) >= 1
    _assert_no_forbidden(res)


def test_market_synthesis_dedups_news_kol_and_guru_evidence() -> None:
    signals = [
        {
            "id": "sec-tsm-1",
            "source": "SEC EDGAR",
            "category": "equities",
            "direction": "neutral",
            "importance": "高",
            "attention_score": 88,
            "title": "台积电 (TSM) 提交 外国发行人临时披露 (6-K)",
            "tickers": ["TSM", "NVDA"],
            "url": "https://www.sec.gov/Archives/edgar/data/1046179/0001/tsm6k.htm",
        },
        {
            "id": "sec-tsm-2",
            "source": "SEC EDGAR",
            "category": "equities",
            "direction": "neutral",
            "importance": "高",
            "attention_score": 86,
            "title": "台积电 (TSM) 提交 外国发行人临时披露 (6-K)",
            "tickers": ["TSM", "NVDA"],
            "url": "https://www.sec.gov/Archives/edgar/data/1046179/0002/tsm6k.htm",
        },
        {
            "id": "cls-nvda-3",
            "source": "财联社电报",
            "category": "ai",
            "direction": "bullish",
            "importance": "高",
            "attention_score": 80,
            "title": "英伟达 Blackwell 机柜出货加速，算力链景气延续",
            "tickers": ["NVDA"],
            "url": "https://www.cls.cn/detail/999001",
        },
    ]
    kol_digest = [
        {
            "handle": "semiguru",
            "name": "芯片观察",
            "stance": "偏多",
            "tickers": ["NVDA", "TSM"],
            "headline": "CoWoS 排产延续高景气",
            "reliability_score": 82,
            "reliability_badge": "高胜率",
            "win_rate_20d": 72.0,
            "url": "https://x.com/semiguru/status/1",
        },
        {
            "handle": "semiguru_mirror",
            "name": "芯片观察备份",
            "stance": "偏多",
            "tickers": ["NVDA", "TSM"],
            "headline": "CoWoS 排产延续高景气！",
            "reliability_score": 70,
            "reliability_badge": "稳健参考",
            "url": "https://x.com/semiguru/status/1?s=20",
        },
        {
            "handle": "chip_alpha",
            "name": "半导体追踪",
            "stance": "偏多",
            "tickers": ["NVDA"],
            "headline": "HBM4 认证进度超预期",
            "reliability_score": 76,
            "reliability_badge": "稳健参考",
            "url": "https://x.com/chip_alpha/status/2",
        },
    ]
    articles = [
        {
            "filename": "guru_semianalysis_1.md",
            "title": "Blackwell Rack Scale Architecture",
            "source": "大佬分享",
            "publisher": "SemiAnalysis · 半导体深研",
            "direction": "bullish",
            "tickers": ["NVDA", "TSM"],
            "score": 90,
            "url": "https://www.semianalysis.com/p/blackwell-rack",
        },
        {
            "filename": "guru_semianalysis_2.md",
            "title": "Blackwell Rack Scale Architecture",
            "source": "大佬分享",
            "publisher": "SemiAnalysis · 半导体深研",
            "direction": "bullish",
            "tickers": ["NVDA", "TSM"],
            "score": 88,
            "url": "https://www.semianalysis.com/p/blackwell-rack?ref=feed",
        },
        {
            "filename": "guru_fabricated_3.md",
            "title": "TSMC Advanced Packaging Economics",
            "source": "大佬分享",
            "publisher": "Fabricated Knowledge · 芯片投研",
            "direction": "bullish",
            "tickers": ["TSM"],
            "score": 84,
            "url": "https://www.fabricatedknowledge.com/p/tsmc-packaging",
        },
    ]

    res = market_synthesis.build_market_synthesis(
        macro_fred=[],
        us_equities={"indices": [], "mag7_and_ai": []},
        correlations={"pairs": []},
        signals=signals,
        kol_digest=kol_digest,
        kol_scorecards=[],
        articles=articles,
        risk_gate={"light": "YELLOW", "label": "黄灯控险"},
        stock_profiles={"NVDA": {"symbol": "NVDA", "price": 150.0, "change_pct": 2.1, "bias20_pct": 2.5}},
    )

    nvda_card = next(c for c in res["cross_source_links"] if c["id"] == "link_nvda_ai_compute")
    tsm_6k_hits = [ev for ev in nvda_card["news_evidence"] if "6-K" in ev]
    assert len(tsm_6k_hits) == 1
    assert len(nvda_card["news_evidence"]) == 2
    assert len(nvda_card["kol_evidence"]) == 2
    assert len(nvda_card["guru_evidence"]) == 2
    assert len(set(nvda_card["news_evidence"])) == len(nvda_card["news_evidence"])
    assert len(set(nvda_card["kol_evidence"])) == len(nvda_card["kol_evidence"])
    assert len(set(nvda_card["guru_evidence"])) == len(nvda_card["guru_evidence"])
    _assert_no_forbidden(res)

