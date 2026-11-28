"""Tests for backend/forum_sentiment.py: home fallback, 4-channel mocks, divergence tags, cache TTL, and forbidden terms."""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from backend import forum_sentiment as fs


def _assert_no_forbidden_recursive(obj: Any, path: str = "root") -> None:
    if isinstance(obj, str):
        low = obj.lower()
        for term in fs.FORBIDDEN_TERMS:
            assert term.lower() not in low, f"Forbidden term {term!r} at {path}: {obj!r}"
    elif isinstance(obj, dict):
        for k, v in obj.items():
            k_low = str(k).lower()
            for term in fs.FORBIDDEN_TERMS:
                assert term.lower() not in k_low, f"Forbidden term {term!r} in key at {path}.{k}"
            _assert_no_forbidden_recursive(v, f"{path}.{k}")
    elif isinstance(obj, (list, tuple)):
        for idx, item in enumerate(obj):
            _assert_no_forbidden_recursive(item, f"{path}[{idx}]")


def test_resolve_user_home_windows_system_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("MACROCOCKPIT_USER_HOME", raising=False)
    fake_os = Mock(wraps=fs.os)
    fake_os.name = "nt"
    monkeypatch.setattr(fs, "os", fake_os)
    monkeypatch.setattr(
        fs.Path,
        "home",
        classmethod(lambda cls: Path(r"C:\Windows\System32\config\systemprofile")),
    )
    resolved = fs._resolve_user_home()
    if Path("C:/Users").is_dir():
        assert "systemprofile" not in str(resolved).lower()
        assert (resolved / "AppData" / "Local").is_dir()

    # Explicit MACROCOCKPIT_USER_HOME override always wins
    custom = tmp_path / "custom_user"
    monkeypatch.setenv("MACROCOCKPIT_USER_HOME", str(custom))
    assert fs._resolve_user_home() == custom


def test_resolve_user_home_posix(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("MACROCOCKPIT_USER_HOME", raising=False)
    monkeypatch.setattr(fs.os, "name", "posix")
    monkeypatch.setattr(fs.Path, "home", classmethod(lambda cls: tmp_path))
    assert fs._resolve_user_home() == tmp_path


def test_collect_forum_pulse_channels_mocked_and_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    user_home = tmp_path / "home"
    (user_home / ".agent-reach").mkdir(parents=True)
    (user_home / ".agent-reach" / "config.yaml").write_text(
        'xueqiu_cookie: "xq_a_token=demo_token; u=12345"\n',
        encoding="utf-8",
    )

    hermes_home = tmp_path / "hermes"
    insights_dir = hermes_home / "scripts" / "news" / "reddit-insights"
    insights_dir.mkdir(parents=True)
    (insights_dir / "2026-11-25_reddit_insights.md").write_text(
        "# Reddit 社区洞察 — 2026-11-25\n\n"
        "- **英伟达 Blackwell 出货大超预期，散户全面追涨**\n"
        "  - EN: NVIDIA Blackwell surge bullish rally\n"
        "  - r/wallstreetbets · 1200↑ · 300💬\n",
        encoding="utf-8",
    )

    # Mock Xueqiu urllib opener
    class _FakeResp:
        def __init__(self, data: dict[str, Any], status: int = 200) -> None:
            self._data = json.dumps(data).encode("utf-8")
            self.status = status

        def read(self) -> bytes:
            return self._data

        def __enter__(self) -> "_FakeResp":
            return self

        def __exit__(self, *args: Any) -> None:
            return None

    class _FakeOpener:
        def open(self, req: Any, timeout: int = 6) -> _FakeResp:
            url = getattr(req, "full_url", str(req))
            if "hot_stock/list.json" in url:
                return _FakeResp(
                    {
                        "data": {
                            "items": [
                                {"code": "NVDA", "name": "英伟达", "current": 150.0, "percent": 5.2, "value": 9999, "increment": 320},
                                {"code": "SH600519", "name": "贵州茅台", "current": 1620.0, "percent": -1.8, "value": 4200, "increment": 110},
                            ]
                        }
                    }
                )
            if "public_timeline_by_category.json" in url:
                post_payload = json.dumps(
                    {
                        "id": 9001,
                        "title": "黄金大跌后抄底还是继续看空？",
                        "description": "<p>金价短线跳水，关注美元流动性变化。</p>",
                        "target": "/1001/9001",
                        "like_count": 88,
                        "reply_count": 21,
                    }
                )
                return _FakeResp({"list": [{"data": post_payload}]})
            return _FakeResp({})

    monkeypatch.setattr(fs.urllib.request, "build_opener", lambda *a, **kw: _FakeOpener())

    # Mock NewsNow & HN HTTP requests
    def _fake_http_get_json(url: str, *, timeout: int = 6, headers: dict[str, str] | None = None) -> dict[str, Any]:
        if "id=weibo" in url:
            return {
                "status": "success",
                "items": [
                    {"title": "某顶流明星恋情官宣吃瓜", "url": "https://weibo.com/1"},
                    {"title": "A股算力芯片与光模块板块大涨", "url": "https://weibo.com/2"},
                ],
            }
        if "id=wallstreetcn-hot" in url:
            return {
                "status": "success",
                "items": [
                    {"title": "美联储降息预期降温，美债收益率走高", "url": "https://wallstreetcn.com/1"}
                ],
            }
        if "id=cls-hot" in url:
            return {
                "status": "success",
                "items": [
                    {"title": "比特币突破关键关口，市场看涨情绪升温", "url": "https://cls.cn/1"}
                ],
            }
        if "id=36kr" in url:
            raise RuntimeError("HTTP 500")
        if "hn.algolia.com" in url:
            return {
                "hits": [
                    {"objectID": "111", "title": "Show HN: My new sourdough starter", "points": 200, "num_comments": 50},
                    {"objectID": "222", "title": "NVIDIA CUDA inference efficiency rally", "points": 420, "num_comments": 90, "url": "https://example.com/nvda"},
                ]
            }
        return {"status": "success", "items": []}

    monkeypatch.setattr(fs, "_http_get_json", _fake_http_get_json)
    monkeypatch.setattr(fs, "_http_post_text", lambda *a, **kw: (200, ""))
    monkeypatch.setattr(fs, "_http_get_text", lambda *a, **kw: (200, ""))

    # Mock rdt & xhs CLI: rdt returns 403/unauthenticated, xhs times out cleanly with stdin=DEVNULL
    monkeypatch.setattr(fs.shutil, "which", lambda name: f"/usr/bin/{name}" if name in ("rdt", "xhs") else None)

    recorded_subproc_calls: list[dict[str, Any]] = []

    def _fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        recorded_subproc_calls.append({"cmd": cmd, "stdin": kwargs.get("stdin"), "timeout": kwargs.get("timeout")})
        if "rdt" in cmd[0]:
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"data": {"authenticated": False}}), stderr="")
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=5)

    monkeypatch.setattr(fs.subprocess, "run", _fake_run)

    cache_file = tmp_path / "forum_pulse_cache.json"
    pulse = fs.collect_forum_pulse(
        cache_path=cache_file,
        force_refresh=True,
        timeout=6,
        user_home=user_home,
        hermes_home=hermes_home,
    )

    # Non-blocking subprocess guarantees
    assert recorded_subproc_calls, "Expected rdt and xhs status subprocess probes"
    for call in recorded_subproc_calls:
        assert call["stdin"] is subprocess.DEVNULL
        assert 5 <= int(call["timeout"]) <= 8

    status_by_id = {c["id"]: c for c in pulse["channels_status"]}
    assert status_by_id["xueqiu"]["status"] == "实时在线"
    assert status_by_id["cn_communities"]["status"] == "实时在线"
    assert status_by_id["reddit"]["status"] == "需登录态"
    assert "rdt login" in status_by_id["reddit"]["note"]
    assert status_by_id["xiaohongshu"]["status"] == "需登录态"
    assert status_by_id["xiaohongshu"]["count"] == 0
    assert "xhs login" in status_by_id["xiaohongshu"]["note"]

    # Entertainment gossip filtered out; finance kept; sourdough filtered out from HN
    titles = [item["title"] for item in pulse["items"]]
    assert not any("明星恋情" in t for t in titles)
    assert not any("sourdough" in t.lower() for t in titles)
    assert any("算力芯片" in t for t in titles)
    assert any("NVIDIA CUDA" in t for t in titles)

    # Disk cache hit within TTL=1800s without re-calling subprocesses
    recorded_subproc_calls.clear()
    cached_pulse = fs.collect_forum_pulse(
        cache_path=cache_file,
        force_refresh=False,
        timeout=6,
        user_home=user_home,
        hermes_home=hermes_home,
    )
    assert cached_pulse["from_cache"] is True
    assert recorded_subproc_calls == []

    # Expired TTL forces refresh
    raw_cache = json.loads(cache_file.read_text(encoding="utf-8"))
    raw_cache["cached_at"] = time.time() - 1900
    cache_file.write_text(json.dumps(raw_cache), encoding="utf-8")
    refreshed = fs.collect_forum_pulse(
        cache_path=cache_file,
        force_refresh=False,
        timeout=6,
        user_home=user_home,
        hermes_home=hermes_home,
    )
    assert refreshed["from_cache"] is False
    assert len(recorded_subproc_calls) >= 2
    _assert_no_forbidden_recursive(pulse)


def test_reddit_and_xhs_authenticated_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(fs.shutil, "which", lambda name: f"/usr/bin/{name}")

    def _fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert kwargs.get("stdin") is subprocess.DEVNULL
        assert 5 <= int(kwargs.get("timeout")) <= 8
        if "rdt" in cmd[0] and "status" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"data": {"authenticated": True}}), stderr="")
        if "rdt" in cmd[0] and "sub" in cmd:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=json.dumps(
                    {
                        "data": [
                            {"title": "Daily Discussion Thread", "selftext": "gm", "ups": 900, "num_comments": 500},
                            {"title": "$NVDA Blackwell demand rally continues", "selftext": "Strong hyperscaler capex", "ups": 800, "num_comments": 120, "url": "https://reddit.com/r/stocks/1"},
                        ]
                    }
                ),
                stderr="",
            )
        if "xhs" in cmd[0] and "status" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="ok: true\n", stderr="")
        if "xhs" in cmd[0] and "search" in cmd:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=json.dumps(
                    [
                        {"note_id": "ad1", "title": "加微信领资料稳赚不赔", "desc": "卖课推广", "liked_count": 500},
                        {"note_id": "n1", "title": "黄金大涨突破前高还能买吗", "desc": "金价走强关注回调", "liked_count": 320},
                    ]
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

    monkeypatch.setattr(fs.subprocess, "run", _fake_run)
    rdt_ch = fs._fetch_reddit_channel(user_home=tmp_path, hermes_home=tmp_path, timeout=6)
    xhs_ch = fs._fetch_xiaohongshu_channel(timeout=6)

    assert rdt_ch["status"] == "实时在线"
    assert len(rdt_ch["items"]) >= 1
    assert not any("Daily Discussion" in i["title"] for i in rdt_ch["items"])

    assert xhs_ch["status"] == "实时在线"
    assert len(xhs_ch["items"]) == 1
    assert xhs_ch["items"][0]["title"] == "黄金大涨突破前高还能买吗"


def test_build_sentiment_divergence_contract_tags_and_forbidden_terms() -> None:
    fake_pulse = {
        "updated_at_bjt": "2026-11-26 22:30",
        "channels_status": [
            {"id": "xueqiu", "name": "雪球社区", "status": "实时在线", "count": 10, "note": "测算口径 N/A 待补"}
        ],
        "items": [
            # 美股AI与科技: 散户强烈看多且高热 -> 配合 KOL 看空 => 当期看多分歧
            {"platform": "雪球", "title": "$NVDA 英伟达暴涨突破看多飙升", "sentiment": "偏多", "score": 80, "heat": 90, "topics": ["美股AI与科技"], "url": "https://xueqiu.com/1"},
            {"platform": "Hacker News", "title": "NVDA GPU boom rally surge", "sentiment": "偏多", "score": 70, "heat": 82, "topics": ["美股AI与科技"], "url": "https://news.ycombinator.com/1"},
            # 黄金与大宗: 散户恐慌大跌 + 专业看多 => 当期看空分歧
            {"platform": "雪球", "title": "黄金暴跌跳水恐慌抛售破位", "sentiment": "偏空", "score": -75, "heat": 78, "topics": ["黄金与大宗"], "url": "https://xueqiu.com/2"},
            # A股与港股: 专业看多, 散户热度低 => 专业倾向较强
            {"platform": "财联社热榜", "title": "A股两市成交额观察", "sentiment": "中性", "score": 0, "heat": 35, "topics": ["A股与港股"], "url": "https://cls.cn/2"},
            # 比特币与加密: 专业与散户共振看多
            {"platform": "微博热搜", "title": "比特币大涨突破走强", "sentiment": "偏多", "score": 55, "heat": 52, "topics": ["比特币与加密"], "url": "https://weibo.com/3"},
        ],
    }
    fake_kol = [
        {"handle": "bear_ai", "group": "AI与美股", "topic": "AI算力", "stance": "偏空", "score": -0.8, "reliability_score": 85, "verified_calls": 12, "tickers": ["NVDA"], "headline": "警惕 NVDA 估值见顶", "url": "https://x.com/1"},
        {"handle": "bull_gold", "group": "宏观与风控", "topic": "黄金", "stance": "偏多", "score": 0.8, "reliability_score": 82, "verified_calls": 10, "tickers": ["GOLD"], "headline": "黄金回踩是配置窗口", "url": "https://x.com/2"},
        {"handle": "bull_a", "group": "A股与港股", "topic": "A股", "stance": "偏多", "score": 0.7, "reliability_score": 80, "verified_calls": 9, "tickers": ["BABA"], "headline": "看多港股与A股核心资产", "url": "https://x.com/3"},
        {"handle": "bull_btc", "group": "加密与链上", "topic": "BTC", "stance": "偏多", "score": 0.6, "reliability_score": 78, "verified_calls": 8, "tickers": ["BTC"], "headline": "BTC 资金流入顺风", "url": "https://x.com/4"},
    ]
    fake_signals = [
        {"source": "大佬分享", "category": "macro", "direction": "neutral", "importance": "中", "title": "Sleeve Regime E21 待补充 测算口径", "url": "https://example.com/post"}
    ]

    div = fs.build_sentiment_divergence(fake_pulse, kol_digest=fake_kol, signals=fake_signals)

    assert set(div.keys()) == {
        "updated_at_bjt",
        "overall_crowd_temp",
        "overall_label",
        "market_pulse",
        "channels_status",
        "divergences",
        "hot_discussions",
    }
    assert 0 <= div["overall_crowd_temp"] <= 100
    assert len(div["divergences"]) == 6
    by_topic = {d["topic"]: d for d in div["divergences"]}
    assert set(by_topic.keys()) == set(fs.CORE_TOPICS)
    assert by_topic["美股AI与科技"]["divergence_tag"] == "看多分歧"
    assert by_topic["黄金与大宗"]["divergence_tag"] == "看空分歧"
    assert by_topic["A股与港股"]["divergence_tag"] == "专业偏强"
    assert by_topic["比特币与加密"]["divergence_tag"] == "多头共振"

    for d in div["divergences"]:
        assert set(d.keys()) == {
            "topic",
            "desk_score",
            "desk_bias",
            "desk_count",
            "street_score",
            "street_heat",
            "street_count",
            "divergence_tag",
            "tone",
            "action_hint",
            "desk_samples",
            "street_samples",
        }
        assert d["desk_bias"] in ("偏多", "偏空", "中性")
        assert d["tone"] in ("bull", "bear", "warn", "neutral")

    payload_bytes = len(json.dumps(div, ensure_ascii=False).encode("utf-8"))
    assert payload_bytes <= 40 * 1024
    _assert_no_forbidden_recursive(div)


def test_reddit_and_xiaohongshu_no_login_search_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Verify Exa MCP + DuckDuckGo HTML fill Reddit/Xiaohongshu as 免登直采 without login or N/A leaks."""
    monkeypatch.setattr(fs.shutil, "which", lambda _name: None)

    exa_reddit_sse = (
        "event: message\n"
        "data: "
        + json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Title: NVDA Blackwell AI demand remains strong : r/wallstreetbets\n"
                                "URL: https://www.reddit.com/r/wallstreetbets/comments/abc123/nvda_blackwell/\n"
                                "Published: N/A\n"
                                "Author: u/trader_one\n"
                                "Highlights:\n"
                                "Posted by u/trader_one in r/wallstreetbets · 850 points · 210 comments\n\n"
                                "Hyperscaler AI capex rally lifts NVDA and TSM.\n"
                                "\n---\n"
                                "Title: Daily Discussion Thread : r/wallstreetbets\n"
                                "URL: https://www.reddit.com/r/wallstreetbets/comments/daily/thread/\n"
                                "Published: N/A\n"
                                "Highlights:\nDaily chat.\n"
                            ),
                        }
                    ]
                },
            },
            ensure_ascii=False,
        )
    )
    exa_xhs_sse = (
        "event: message\n"
        "data: "
        + json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Title: 金马学堂 - 小红书\n"
                                "URL: https://www.xiaohongshu.com/user/profile/123456\n"
                                "Published: N/A\n"
                                "Highlights:\n"
                                "登录\n关注\n"
                                "美股纳指回调后还能买吗，关税与黄金逻辑分析\n"
                                "今天午餐真好吃美食探店\n"
                            ),
                        }
                    ]
                },
            },
            ensure_ascii=False,
        )
    )
    ddg_xhs_html = (
        '<html><body>'
        '<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.xiaohongshu.com%2Fexplore%2F6a603685000000000301dc8c">这只标普500定投基金居然放开限购了 - 小红书</a>'
        '<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fjob.xiaohongshu.com%2Fcampus%2F1">实习生招聘 - 小红书校园招聘</a>'
        "</body></html>"
    )

    def _fake_post(url: str, *, json_payload: dict[str, Any], headers: dict[str, str] | None = None, timeout: int = 8):
        q = str(((json_payload.get("params") or {}).get("arguments") or {}).get("query") or "")
        if "reddit.com" in q:
            return 200, exa_reddit_sse
        if "xiaohongshu.com" in q:
            return 200, exa_xhs_sse
        return 200, ""

    monkeypatch.setattr(fs, "_http_post_text", _fake_post)
    monkeypatch.setattr(fs, "_http_get_text", lambda url, **kw: (200, ddg_xhs_html))
    monkeypatch.setattr(fs, "_http_get_json", lambda url, **kw: {"hits": []})

    rdt_ch = fs._fetch_reddit_channel(user_home=tmp_path, hermes_home=tmp_path, timeout=6)
    xhs_ch = fs._fetch_xiaohongshu_channel(timeout=6)

    assert rdt_ch["status"] == "免登直采"
    assert len(rdt_ch["items"]) == 1
    assert rdt_ch["items"][0]["platform"] == "r/wallstreetbets"
    assert "NVDA" in rdt_ch["items"][0]["title"]

    assert xhs_ch["status"] == "免登直采"
    xhs_titles = [it["title"] for it in xhs_ch["items"]]
    assert any("标普500定投基金" in t for t in xhs_titles)
    assert any("纳指回调" in t for t in xhs_titles)
    assert not any("美食探店" in t or "实习生招聘" in t for t in xhs_titles)

    _assert_no_forbidden_recursive({"reddit": rdt_ch, "xiaohongshu": xhs_ch})


def test_xhs_noise_filtering_comments_and_contrarian_top_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify false-gold/lifestyle XHS noise is blocked, comments enrich score, and high XHS/CN heat stays descriptive."""
    monkeypatch.setattr(fs.shutil, "which", lambda name: "/usr/bin/xhs" if name == "xhs" else None)

    def _fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert kwargs.get("stdin") is subprocess.DEVNULL
        if "status" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="ok: true\n", stderr="")
        if "search" in cmd:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=json.dumps(
                    [
                        {"note_id": "bad1", "title": "00后学渣到学霸逆袭的黄金时机", "desc": "逛山姆养生新手投资教学", "liked_count": 900},
                        {"note_id": "good1", "title": "黄金基金继续加仓，散户实盘交流", "desc": "大家怎么看金价走势", "liked_count": 420},
                    ]
                ),
                stderr="",
            )
        if "comments" in cmd and "good1" in cmd:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=json.dumps({"comments": [{"content": "全仓梭哈看涨翻倍，闭眼冲！"}, {"content": "我也满仓冲了"}]}),
                stderr="",
            )
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

    monkeypatch.setattr(fs.subprocess, "run", _fake_run)
    xhs_ch = fs._fetch_xiaohongshu_channel(timeout=6)
    assert xhs_ch["status"] == "实时在线"
    assert len(xhs_ch["items"]) == 1
    assert "黄金基金继续加仓" in xhs_ch["items"][0]["title"]
    assert "热评：" in xhs_ch["items"][0]["summary"]
    assert len(xhs_ch["items"][0]["comments_preview"]) >= 1

    # High XHS bullish discussion heat requires strictly bullish retail sentiment + multi-item density to emit 当期关注较高
    pulse = {
        "updated_at_bjt": "2026-11-27 00:05",
        "channels_status": [],
        "items": [
            {"platform": "小红书", "channel": "xiaohongshu", "title": "黄金定投人人翻倍看涨飙升", "sentiment": "偏多", "score": 65, "heat": 78, "topics": ["黄金与大宗"], "url": "https://www.xiaohongshu.com/explore/good1"},
            {"platform": "小红书", "channel": "xiaohongshu", "title": "黄金积存金继续大涨加仓", "sentiment": "偏多", "score": 55, "heat": 78, "topics": ["黄金与大宗"], "url": "https://www.xiaohongshu.com/explore/good2"},
            {"platform": "小红书", "channel": "xiaohongshu", "title": "实物黄金热议看多突破", "sentiment": "偏多", "score": 50, "heat": 78, "topics": ["黄金与大宗"], "url": "https://www.xiaohongshu.com/explore/good3"},
            {"platform": "微博热搜", "channel": "cn_communities", "title": "金价创高大妈抢金条走强", "sentiment": "偏多", "score": 55, "heat": 76, "topics": ["黄金与大宗"], "url": "https://weibo.com/9"},
        ],
    }
    div = fs.build_sentiment_divergence(pulse)
    gold_div = {d["topic"]: d for d in div["divergences"]}["黄金与大宗"]
    assert gold_div["divergence_tag"] == "关注较高"
    assert gold_div["tone"] == "neutral"
    _assert_no_forbidden_recursive(div)

    # Simultaneous bearish sentiment between DESK and STREET must emit "同步谨慎", and avoid cross-topic duplicate samples
    bear_pulse = {
        "updated_at_bjt": "2026-11-27 12:00",
        "channels_status": [],
        "items": [
            {"platform": "Reddit", "channel": "reddit", "title": "Shared market downturn macro fears and rate shock", "sentiment": "偏空", "score": -60, "heat": 82, "topics": ["美股AI与科技", "美联储与宏观利率"], "url": "https://reddit.com/r/stocks/1"},
            {"platform": "雪球", "channel": "xueqiu", "title": "美联储鹰派表态引发流动性担忧", "sentiment": "偏空", "score": -50, "heat": 70, "topics": ["美联储与宏观利率"], "url": "https://xueqiu.com/99"},
        ],
    }
    bear_kol = [
        {"handle": "k1", "group": "加密", "topic": "美股", "headline": "NVDA valuation risk and tightening pressure", "stance": "偏空", "score": -0.7, "tickers": ["NVDA"], "url": "https://x.com/k1/1"},
        {"handle": "k2", "group": "宏观", "topic": "联储", "headline": "Fed rate cut delayed as Treasury yields spike", "stance": "偏空", "score": -0.8, "tickers": ["US10Y"], "url": "https://x.com/k2/2"},
    ]
    bear_div = fs.build_sentiment_divergence(bear_pulse, kol_digest=bear_kol)
    by_topic = {d["topic"]: d for d in bear_div["divergences"]}
    assert by_topic["美联储与宏观利率"]["divergence_tag"] == "同步谨慎"
    assert "NVDA" not in str(by_topic["比特币与加密"]["desk_samples"])
    ai_u = [s["url"] for s in by_topic["美股AI与科技"]["street_samples"]]
    fed_u = [s["url"] for s in by_topic["美联储与宏观利率"]["street_samples"]]
    if ai_u and fed_u:
        assert ai_u[0] != fed_u[0]

    # Review Blockers #1/#2/#3/#5 regression checks:
    # #2 Zero community samples must emit None / 暂无样本 instead of fake 40 / 22
    empty_div = fs.build_sentiment_divergence({"updated_at_bjt": "2026-11-27 12:00", "channels_status": [], "items": []})
    assert empty_div["overall_crowd_temp"] is None
    assert empty_div["overall_label"] == "暂无样本"
    assert all(d["street_score"] is None and d["street_heat"] is None and d["divergence_tag"] == "暂无样本" for d in empty_div["divergences"])

    # #3 Bullish Reddit heat + 1 bearish low-heat Xiaohongshu item must NOT trigger 当期关注较高
    mixed_xhs_pulse = {
        "updated_at_bjt": "2026-11-27 12:00",
        "channels_status": [],
        "items": [
            {"platform": "小红书", "channel": "xiaohongshu", "title": "黄金风险太大了先减仓避险", "sentiment": "偏空", "score": -60, "heat": 42, "topics": ["黄金与大宗"], "url": "https://www.xiaohongshu.com/explore/x1"},
            {"platform": "Reddit", "channel": "reddit", "title": "Gold GLD breakout rally surge", "sentiment": "偏多", "score": 65, "heat": 90, "topics": ["黄金与大宗"], "url": "https://reddit.com/r/stocks/g1"},
            {"platform": "Reddit", "channel": "reddit", "title": "Gold bull run continues", "sentiment": "偏多", "score": 65, "heat": 90, "topics": ["黄金与大宗"], "url": "https://reddit.com/r/stocks/g2"},
            {"platform": "Reddit", "channel": "reddit", "title": "Buying more gold miners rally", "sentiment": "偏多", "score": 65, "heat": 90, "topics": ["黄金与大宗"], "url": "https://reddit.com/r/stocks/g3"},
            {"platform": "Reddit", "channel": "reddit", "title": "Gold bull case breakout", "sentiment": "偏多", "score": 65, "heat": 90, "topics": ["黄金与大宗"], "url": "https://reddit.com/r/stocks/g4"},
        ],
    }
    mixed_div = fs.build_sentiment_divergence(mixed_xhs_pulse)
    gold_mixed = {d["topic"]: d for d in mixed_div["divergences"]}["黄金与大宗"]
    assert gold_mixed["divergence_tag"] != "关注较高"
    assert any(h.get("zh_brief") for h in mixed_div["hot_discussions"] if h.get("platform") == "Reddit")

    # Single Xiaohongshu item on an overall slightly bearish Fed crowd (street_score < 0) must NOT emit 关注较高 or 出圈见顶
    fed_neg_pulse = {
        "updated_at_bjt": "2026-11-27 23:32",
        "channels_status": [],
        "items": [
            {"platform": "小红书", "channel": "xiaohongshu", "title": "黄金和美元的爱恨情仇看多飙升", "sentiment": "偏多", "score": 35, "heat": 74, "topics": ["美联储与宏观利率"], "url": "https://www.xiaohongshu.com/explore/fed1"},
            {"platform": "Reddit", "channel": "reddit", "title": "Fed rate cut delayed as Treasury yields spike", "sentiment": "偏空", "score": -45, "heat": 82, "topics": ["美联储与宏观利率"], "url": "https://reddit.com/r/macro/f2"},
        ],
    }
    fed_neg_div = {d["topic"]: d for d in fs.build_sentiment_divergence(fed_neg_pulse)["divergences"]}["美联储与宏观利率"]
    assert fed_neg_div["divergence_tag"] not in ("关注较高", "出圈见顶")

    # #1 & #5: Private portfolio holdings leak blocked in build_sentiment_divergence & project_public_state (incl. channels_status.note), plus CJK-adjacent E21 scrub
    from backend.public_snapshot import project_public_state

    priv_pulse = {
        "updated_at_bjt": "2026-11-27 12:00",
        "channels_status": [
            {"id": "xueqiu", "name": "雪球", "status": "实时在线", "count": 1, "note": "实盘持仓：NVDA 125 shares 量化E21信号 BTC 1️⃣"},
        ],
        "items": [
            {"platform": "雪球", "title": "实盘持仓：NVDA 125 shares", "sentiment": "偏多", "score": 50, "heat": 90, "topics": ["美股AI与科技"], "url": "https://xueqiu.com/priv"},
            {"platform": "雪球", "title": "量化E21信号 BTC 1️⃣ 讨论升温", "sentiment": "中性", "score": 0, "heat": 60, "topics": ["比特币与加密"], "url": "https://xueqiu.com/pub"},
        ],
    }
    priv_div = fs.build_sentiment_divergence(priv_pulse)
    priv_blob = json.dumps(priv_div, ensure_ascii=False)
    assert "实盘持仓：NVDA 125 shares" not in priv_blob
    assert "E21" not in priv_blob
    assert "\ufe0f" not in priv_blob and "\u20e3" not in priv_blob

    # Multi-item CN hotlists (weibo/zhihu/baidu/douyin) with strictly bullish sentiment trigger 当期关注较高 without Xiaohongshu
    cn_only_pulse = {
        "updated_at_bjt": "2026-11-27 12:00",
        "channels_status": [],
        "items": [
            {"platform": "微博热搜", "channel": "cn_communities", "title": "A股全面大涨创新高看多", "sentiment": "偏多", "score": 70, "heat": 82, "topics": ["A股与港股"], "url": "https://weibo.com/10"},
            {"platform": "知乎热榜", "channel": "cn_communities", "title": "创业板飙升突破牛市看涨", "sentiment": "偏多", "score": 65, "heat": 80, "topics": ["A股与港股"], "url": "https://zhihu.com/10"},
            {"platform": "百度热搜", "channel": "cn_communities", "title": "两市放量大涨情绪高涨", "sentiment": "偏多", "score": 60, "heat": 79, "topics": ["A股与港股"], "url": "https://baidu.com/10"},
            {"platform": "抖音热点", "channel": "cn_communities", "title": "算力与券商板块持续走强", "sentiment": "偏多", "score": 55, "heat": 78, "topics": ["A股与港股"], "url": "https://douyin.com/10"},
        ],
    }
    cn_only_div = fs.build_sentiment_divergence(cn_only_pulse)
    cn_a = {d["topic"]: d for d in cn_only_div["divergences"]}["A股与港股"]
    assert cn_a["divergence_tag"] == "关注较高"
    assert "小红书" not in cn_a["action_hint"]

    proj = project_public_state(
        {
            "risk_gate": {"light": "GREEN", "long_risk_cap": 1.0, "label": "绿灯"},
            "forum_sentiment": {
                "overall_crowd_temp": 55,
                "overall_label": "情绪平稳",
                "channels_status": [{"id": "xueqiu", "name": "雪球", "count": 1, "note": "实盘持仓：NVDA 125 shares"}],
                "hot_discussions": [{"title": "实盘持仓：NVDA 125 shares", "platform": "雪球"}],
                "divergences": [
                    {
                        "topic": "美股AI与科技",
                        "desk_samples": [{"author": "@a", "summary": "实盘持仓：NVDA 125 shares", "url": "https://x.com/a"}],
                        "street_samples": [{"platform": "雪球", "title": "实盘持仓：NVDA 125 shares", "url": "https://xueqiu.com/p"}],
                    }
                ],
            },
            "kol_digest": [{"handle": "a", "headline": "实盘持仓：NVDA 125 shares"}],
        }
    )
    proj_blob = json.dumps(proj, ensure_ascii=False)
    assert "实盘持仓：NVDA 125 shares" not in proj_blob


def test_hermes_scoring_guardrails_crypto_cap_and_zh_brief(tmp_path: Path) -> None:
    """Verify personal gossip blocking, Live updates roundup isolation, cross-category separation, Top-15 crypto cap, and zh_brief."""
    from datetime import datetime, timezone
    from backend import curation, kazike_engine, public_snapshot

    now_utc = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)
    now_bjt = "2026-11-27 20:00"
    raw = [
        {
            "id": "mw-gossip",
            "source": "MarketWatch",
            "category": "equities",
            "time_bjt": now_bjt,
            "title": "My husband secretly sends his ex-wife $1,500 a month after she lost her job",
            "summary": "Personal finance advice column on divorce payments.",
            "tickers": [],
        },
        {
            "id": "cd-live",
            "source": "CoinDesk",
            "category": "crypto",
            "direction": "neutral",
            "time_bjt": now_bjt,
            "title": "Live updates: Bitcoin drops below $60,000 as Nasdaq 100 gives up gains",
            "summary": "Nasdaq 100 rose 0.8% while Nvidia fell 2% and Bitcoin slipped.",
            "tickers": ["NVDA", "QQQ", "BTC"],
        },
        {
            "id": "nvda-eq",
            "source": "财联社电报",
            "category": "equities",
            "direction": "bearish",
            "time_bjt": now_bjt,
            "title": "英伟达美股盘中下跌2%，纳斯达克100涨幅收窄至0.8%",
            "summary": "科技权重股午盘回吐涨幅。",
            "tickers": ["NVDA", "QQQ"],
        },
    ]
    visible, stats = kazike_engine.process_financial_signals(
        raw, now_utc=now_utc, receipts_path=tmp_path / "rc.json", browse_floor=0, max_visible=85
    )
    ids = {x["id"] for x in visible}
    assert "mw-gossip" not in ids
    assert stats["dropped_prefilter"] == 1
    assert "nvda-eq" in ids and "cd-live" in ids
    cd = next(x for x in visible if x["id"] == "cd-live")
    assert cd["merged_count"] == 1
    assert cd["source_tier"] == "T2"
    assert cd.get("zh_brief")

    # Top-15 curation cap keeps crypto <= 2 rows in head slots
    ws = {
        "summary": {"net_equity_usd": "15000"},
        "spot_balances": [{"instrument_id": "BTC", "symbol": "BTC", "is_cash": False, "qty": "0.11", "market_value_usd": "6800"}],
        "derivative_positions": [],
    }
    pool = [
        {
            "id": f"cr-{i}",
            "source": "CoinDesk",
            "category": "crypto",
            "direction": "bullish",
            "attention_score": 82 - i,
            "selected": True,
            "tickers": ["BTC"],
            "time_bjt": now_bjt,
            "title": f"BTC ETF flow surge item {i}",
            "summary": "Bitcoin ETF inflow accelerates.",
            "zh_brief": f"比特币ETF资金净流入升温 {i}。",
        }
        for i in range(6)
    ] + [
        {
            "id": f"eq-{i}",
            "source": "财联社电报",
            "category": "equities" if i % 2 == 0 else "ai",
            "direction": "bullish",
            "attention_score": 76 - i * 0.2,
            "selected": True,
            "tickers": ["NVDA"],
            "time_bjt": now_bjt,
            "title": f"美股科技与AI算力催化 {i}",
            "summary": "财报与算力订单跟踪。",
        }
        for i in range(16)
    ]
    ranked = curation.rank_curated_signals(pool, workspace=ws, scope="all", limit=25)
    assert sum(1 for x in ranked[:15] if x.get("category") == "crypto") <= 2
    assert len(ranked) == 22

    # Privacy check: zh_brief on forum_sentiment preserves non-private brief and strips private leaks,
    # validates asset_source_matrix categories/privacy, and strips private signals/zh_brief leaks.
    proj2 = public_snapshot.project_public_state(
        {
            "risk_gate": {"light": "GREEN", "long_risk_cap": 1.0, "label": "绿灯"},
            "signals": [
                {"id": "pub-1", "title": "Fed signals patience", "zh_brief": "美联储释放观望信号。", "category": "macro"},
                {"id": "priv-1", "title": "Fed macro brief", "zh_brief": "实盘持仓：NVDA 125 shares", "category": "macro"},
            ],
            "asset_source_matrix": {
                "categories": [
                    None,
                    {"id": "us_tech", "name": "美股科技", "primary_venues": [{"name": "CNBC", "note": "account balance: $12,345"}]},
                ]
            },
            "forum_sentiment": {
                "overall_crowd_temp": 55,
                "overall_label": "情绪平稳",
                "channels_status": [],
                "hot_discussions": [
                    {"platform": "Reddit", "title": "Fed rate cut delayed", "zh_brief": "美联储降息预期推迟。", "heat": 70}
                ],
                "divergences": [],
            },
        }
    )
    assert proj2["forum_sentiment"]["hot_discussions"][0]["zh_brief"] == "美联储降息预期推迟。"
    assert [s["id"] for s in proj2["signals"]] == ["pub-1"]
    assert len(proj2["asset_source_matrix"]["categories"]) == 1
    assert proj2["asset_source_matrix"]["categories"][0]["primary_venues"] == []

    # Blocker regressions:
    # 1. zh_brief strips negative sign preservation, post-truncate forbidden terms, and never leaks private text to network
    from backend import rss_sources, zh_brief
    neg_br = zh_brief._deterministic_zh_brief("Bitcoin funding rate hits -2.3% after drops below support", "")
    assert "-2.3%" in neg_br
    assert zh_brief._clean_brief_text("Bitcoin rally " + ("x" * 78) + " E21tail", max_len=92).find("E21") == -1
    assert zh_brief.build_zh_brief("My account balance is $99,999 in NVDA 125 shares", "", allow_network=True) == ""

    # 2. Institutional credit card debt report passes prefilter; Stock market today damped equally in curation;
    #    same-URL cross-category clusters get isolated cluster_ids; arXiv vs CNBC do not share score receipts;
    #    short crypto-heavy list is capped; CoinDesk RSS uses T2.
    assert any(f["id"] == "coindesk" and f["tier"] == "T2" for f in rss_sources.CURATED_RSS_FEEDS)
    lbl_fed, _ = kazike_engine.prefilter_financial({
        "source": "NY Fed",
        "title": "New York Fed reports US credit card debt hits $1.17T",
        "summary": "Household debt delinquency report.",
    })
    assert lbl_fed == "PASS"

    r_arxiv = kazike_engine.score_five_axes(
        {"source": "arXiv 量化金融", "title": "Treasury liquidity shock drives 10Y yield +25bps and Fed repricing", "summary": "Macro shock $2B."},
        receipts_path=tmp_path / "rc_iso.json",
    )
    r_cnbc = kazike_engine.score_five_axes(
        {"source": "CNBC", "title": "Treasury liquidity shock drives 10Y yield +25bps and Fed repricing", "summary": "Macro shock $2B."},
        receipts_path=tmp_path / "rc_iso.json",
    )
    assert r_arxiv["score"] <= 68 and r_cnbc["score"] > r_arxiv["score"]

    same_url_items = [
        {"id": "u-cr", "category": "crypto", "source": "CoinDesk", "time_bjt": now_bjt, "title": "Bitcoin rises as Fed holds", "url": "https://example.com/shared", "attention_score": 76},
        {"id": "u-eq", "category": "equities", "source": "CNBC", "time_bjt": now_bjt, "title": "Stocks rise as Fed holds", "url": "https://example.com/shared", "attention_score": 82},
    ]
    dedup_u = kazike_engine.compute_48h_heat(kazike_engine.cluster_and_dedup(same_url_items, now_utc=now_utc), now_utc=now_utc)
    assert len({x["cluster_id"] for x in dedup_u}) == 2
    assert all(x["participant_count"] == 1 for x in dedup_u)

    # Stock market today damped identically to Live updates, and Coinbase crypto news mentioning Nasdaq doesn't match QQQ
    smt_ranked = curation.rank_curated_signals(
        [
            {"id": "smt", "category": "equities", "direction": "bullish", "attention_score": 75, "tickers": ["NVDA"], "title": "Stock market today: Nvidia rises"},
            {"id": "cb", "category": "crypto", "direction": "bullish", "attention_score": 82, "tickers": ["BTC", "COIN"], "title": "What was Coinbase worth while Nasdaq was closed?"},
        ],
        workspace={**ws, "derivative_positions": [{"instrument_id": "QQQUSDT", "symbol": "QQQUSDT", "side": "long", "qty": "2", "notional_usd": "1000"}], "spot_balances": [{"instrument_id": "NVDA", "symbol": "NVDA", "is_cash": False, "qty": "10", "market_value_usd": "1400"}]},
        scope="all",
        limit=10,
    )
    by_s = {r["id"]: r for r in smt_ranked}
    assert by_s["smt"]["final_score"] < 90.0
    assert not any(t == "QQQUSDT" for t in ((by_s["cb"].get("holding_radar") or {}).get("matched_tickers") or []))

    # Single-platform 3 XHS posts alone must NOT emit 当期关注较高
    single_xhs = fs.build_sentiment_divergence(
        {
            "updated_at_bjt": now_bjt,
            "channels_status": [],
            "items": [
                {"platform": "小红书", "channel": "xiaohongshu", "title": "黄金大涨看多1", "sentiment": "偏多", "score": 60, "heat": 80, "topics": ["黄金与大宗"], "url": "https://www.xiaohongshu.com/explore/s1"},
                {"platform": "小红书", "channel": "xiaohongshu", "title": "黄金大涨看多2", "sentiment": "偏多", "score": 60, "heat": 80, "topics": ["黄金与大宗"], "url": "https://www.xiaohongshu.com/explore/s2"},
                {"platform": "小红书", "channel": "xiaohongshu", "title": "黄金大涨看多3", "sentiment": "偏多", "score": 60, "heat": 80, "topics": ["黄金与大宗"], "url": "https://www.xiaohongshu.com/explore/s3"},
            ],
        }
    )
    assert {d["topic"]: d for d in single_xhs["divergences"]}["黄金与大宗"]["divergence_tag"] != "关注较高"


def test_holding_radar_and_hybrid_perp_quote_parsing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Verify PAXG/QQQUSDT/ZHIPUUSDT curation aliases, directional radar (short=逆风 on bullish), and hybrid perp quotes."""
    from backend import curation, market_quotes

    ws = {
        "summary": {"net_equity_usd": "14900"},
        "spot_balances": [
            {"instrument_id": "PAXG", "symbol": "PAXG", "is_cash": False, "qty": "0.47", "market_value_usd": "1686"},
            {"instrument_id": "BNB", "symbol": "BNB", "is_cash": False, "qty": "1.58", "market_value_usd": "884"},
        ],
        "derivative_positions": [
            {"instrument_id": "QQQUSDT", "symbol": "QQQUSDT", "side": "long", "qty": "1.79", "notional_usd": "1134"},
            {"instrument_id": "ZHIPUUSDT", "symbol": "ZHIPUUSDT", "side": "short", "qty": "2.59", "notional_usd": "101"},
        ],
    }
    signals = [
        {"id": "s_gold", "title": "现货黄金突破 $3580 关口", "direction": "bullish", "attention_score": 80, "tickers": ["GOLD"]},
        {"id": "s_qqq", "title": "纳斯达克 100 盘前延续反弹", "direction": "bullish", "attention_score": 78, "tickers": []},
        {"id": "s_zhipu", "title": "港股智谱 (2513.HK) 大涨超 8% 创近期新高", "direction": "bullish", "attention_score": 82, "tickers": []},
    ]
    ranked = curation.rank_curated_signals(signals, workspace=ws, scope="held")
    by_id = {r["id"]: r for r in ranked}
    assert set(by_id.keys()) == {"s_gold", "s_qqq", "s_zhipu"}
    assert by_id["s_gold"]["holding_radar"]["relation"] == "顺风"
    assert by_id["s_qqq"]["holding_radar"]["relation"] == "顺风"
    # Short ZHIPUUSDT + bullish news on 智谱 => 逆风!
    assert by_id["s_zhipu"]["holding_radar"]["relation"] == "逆风"

    # Mock Bitget & Tencent quote parsing for QQQUSDT + ZHIPUUSDT fallback
    monkeypatch.setattr(market_quotes, "DATA_DIR", tmp_path)

    class _FakeResp:
        def __init__(self, *, status_code: int = 200, json_data: Any = None, content: bytes = b"") -> None:
            self.status_code = status_code
            self._json = json_data
            self.content = content

        def json(self) -> Any:
            return self._json

    class _FakeRequests:
        @staticmethod
        def get(url: str, params: dict[str, Any] | None = None, headers: Any = None, timeout: Any = None) -> _FakeResp:
            if "bitget.com" in url and (params or {}).get("symbol") == "QQQUSDT":
                return _FakeResp(json_data={"data": [{"markPr": "638.76", "fundingRate": "0.0001", "ts": "1764172000000"}]})
            if "bitget.com" in url:
                return _FakeResp(status_code=503)
            if "qt.gtimg.cn" in url:
                txt = 'v_hk02513="100~智谱~02513~293.000~271.000~";'
                return _FakeResp(content=txt.encode("gbk"))
            return _FakeResp(status_code=404)

    monkeypatch.setattr(market_quotes, "requests", _FakeRequests)
    hq = market_quotes.fetch_hybrid_perp_quotes(force_refresh=True, timeout=3)
    assert hq["QQQUSDT"]["price"] == "638.76"
    assert hq["QQQUSDT"]["source"] == "bitget"
    # 293.00 / 7.79 = 37.6123
    assert hq["ZHIPUUSDT"]["price"] == "37.6123"
    assert hq["ZHIPUUSDT"]["source"] == "tencent_hk_fx"


def test_ahk_expansion_unit_conversions_dedup_and_public_guard(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Verify HK 4/5-digit classification, exact A/HK capital-flow units, real heat parsing, desk dedup, and privacy guard."""
    from backend.public_snapshot import has_private_article_text, project_public_state

    # 1. HK 4/5-digit code & core ticker classification (incl. CJK-adjacent & SHY anti-false-positive)
    assert "0700.HK" in fs.extract_tickers("00700.HK 腾讯控股回购注销")
    assert "0388.HK" in fs.extract_tickers("港交所0388.HK涨")
    assert "002265.SZ" in fs.extract_tickers("建设工业(002265.SZ)")
    assert fs._is_ahk_ticker("SHY") is False
    assert "A股与港股" not in fs._classify_topics("US Treasury SHY yield move", ["SHY"])
    assert "A股与港股" in fs._classify_topics("港交所0388.HK涨", fs.extract_tickers("港交所0388.HK涨"))
    assert "A股与港股" in fs._classify_topics("易控智驾(07687.HK)耗资回购", fs.extract_tickers("易控智驾(07687.HK)"))
    assert "A股与港股" in fs._classify_topics("恒生科技大涨", [])

    # 2. Exact capital flow unit conversions + Eastmoney hotrank + Zhihu real heat + mass penetration
    def _mock_post(url: str, *, json_payload: Any = None, headers: Any = None, timeout: int = 6):
        if "emappdata.eastmoney.com" in url:
            return 200, json.dumps({"status": 0, "data": [{"sc": "SZ300308", "rk": 1}, {"sc": "SH600410", "rk": 2}]})
        return 200, ""

    def _mock_get_text(url: str, *, headers: Any = None, timeout: int = 6, encoding: str | None = None):
        if "qt.gtimg.cn" in url and "sh000001" in url:
            # sh amt_wan=90000000 (9000亿) + sz amt_wan=110000000 (11000亿) => 20000亿 = 2.00万亿
            # hsi amt_wan=25000000 (2500亿港元)
            pad = "~".join(["0"] * 33)
            sh_line = f'v_sh000001="1~上证指数~000001~3600.00~3500~3500~100~0~0~{pad}~0~0~0~0~90000000";'
            # wait count ~ indices explicitly (index 37)
            p_sh = ["0"] * 46
            p_sh[1], p_sh[3], p_sh[32], p_sh[37] = "上证指数", "3600.00", "2.86", "90000000"
            p_sz = ["0"] * 46
            p_sz[1], p_sz[3], p_sz[32], p_sz[37] = "深证成指", "10800.00", "4.10", "110000000"
            p_hsi = ["0"] * 46
            p_hsi[1], p_hsi[3], p_hsi[32], p_hsi[37] = "恒生指数", "22600.00", "2.10", "25000000"
            return 200, "\n".join([
                f'v_sh000001="{"~".join(p_sh)}";',
                f'v_sz399001="{"~".join(p_sz)}";',
                f'v_hkHSI="{"~".join(p_hsi)}";',
            ])
        if "qt.gtimg.cn" in url and "sz300308" in url:
            p1 = ["0"] * 46
            p1[1], p1[3], p1[32], p1[37] = "中际旭创", "160.00", "10.00", "500000"
            p2 = ["0"] * 46
            p2[1], p2[3], p2[32], p2[37] = "华胜天成", "22.50", "-3.20", "180000"
            return 200, f'v_sz300308="{"~".join(p1)}";\nv_sh600410="{"~".join(p2)}";'
        return 200, ""

    def _mock_get_json(url: str, *, timeout: int = 6, headers: Any = None):
        if "RPTA_RZRQ_LSHJ" in url:
            return {"result": {"data": [{"DIM_DATE": "2026-11-26 00:00:00", "RZYE": 2105578910371, "RZRQYE": 2134642216632, "RZMRE": 100000000000, "RZJME": 8500000000, "RZYEZB": 2.5}]}}
        if "RPT_MUTUAL_DEAL_HISTORY" in url:
            return {"result": {"data": [{"TRADE_DATE": "2026-11-27 00:00:00", "MUTUAL_TYPE": "006", "NET_DEAL_AMT": 4586.57, "DEAL_AMT": 109835.03}]}}
        if "id=gelonghui" in url:
            return {"status": "success", "items": [{"title": "腾讯控股(00700.HK)耗资回购注销股份", "extra": {"info": "港股公告摘要"}, "url": "https://gelonghui.com/1"}]}
        if "id=zhihu" in url:
            return {
                "status": "success",
                "items": [
                    {"title": "A股暴涨重回2万亿", "extra": {"info": "1600 万热度", "hover": "创业板放量涨停"}, "url": "https://zhihu.com/1"},
                    {"title": "某电影票房破十亿", "url": "https://zhihu.com/2"},
                ],
            }
        if "top.baidu.com" in url:
            return {"data": {"cards": [{"content": [{"content": [{"word": "金价银价大涨", "desc": "现货黄金突破"}, {"word": "美食外卖测评", "desc": "生活"}]}]}]}}
        if "newsnow.busiyi.world" in url:
            return {"status": "success", "items": [{"title": "普通社会新闻", "url": "https://example.com/x"}]}
        return {}

    monkeypatch.setattr(fs.shutil, "which", lambda _n: None)
    monkeypatch.setattr(
        fs,
        "_fetch_xueqiu_channel",
        lambda **kw: {"id": "xueqiu", "name": "雪球社区", "status": "需登录态", "count": 0, "note": "mock", "hot_stocks": [], "items": []},
    )
    monkeypatch.setattr(fs, "_http_post_text", _mock_post)
    monkeypatch.setattr(fs, "_http_get_text", _mock_get_text)
    monkeypatch.setattr(fs, "_http_get_json", _mock_get_json)

    pulse = fs.collect_forum_pulse(cache_path=tmp_path / "c.json", force_refresh=True, user_home=tmp_path, hermes_home=tmp_path)
    mp = pulse["market_pulse"]
    assert mp["a_turnover_yi"] == 20000.0
    assert mp["a_turnover_display"] == "2.00万亿"
    assert mp["hk_turnover_yi_hkd"] == 2500.0
    assert mp["rzrq_balance_yi"] == 21346.4
    assert mp["rzrq_balance_display"] == "2.13万亿"
    assert mp["rzrq_buy_yi"] == 1000.0
    assert mp["rzrq_net_buy_yi"] == 85.0
    assert mp["southbound_net_yi_hkd"] == 45.87
    assert mp["mass_total_slots"] >= 2
    assert mp["mass_ahk_hits"] >= 1

    # Check real heat computed from 1600 万热度 => 54 + 40 = 94
    zhihu_items = [x for x in pulse["channels"]["cn_communities"]["items"] if x["platform"] == "知乎热榜"]
    assert zhihu_items and zhihu_items[0]["heat"] == 94

    # 3. Desk dedup: same URL across kol_digest & signals counts once; pure-NVDA casual A-share mention excluded,
    #    but NVDA+HK07687 is retained via merged tickers, and long-prefix CLS wires with ?id=1 vs ?id=2 are NOT collapsed
    shared_url = "https://x.com/user1/status/12345"
    div = fs.build_sentiment_divergence(
        pulse,
        kol_digest=[
            {"handle": "user1", "stance": "偏多", "score": 0.7, "tickers": ["NVDA"], "headline": "看多 NVDA，顺便看看A股联动", "url": shared_url},
            {"handle": "hkpro", "stance": "偏多", "score": 0.8, "tickers": ["00700.HK"], "headline": "港股腾讯回购", "url": "https://x.com/hk/1"},
        ],
        signals=[
            {"source": "X深度长文", "direction": "bullish", "tickers": ["NVDA"], "title": "看多 NVDA，顺便看看A股联动", "url": shared_url},
            {"source": "财联社电报", "direction": "bullish", "tickers": ["NVDA"], "title": "NVDA versus HK07687 outlook", "url": "https://cls.cn/detail/7001"},
        ],
    )
    by_t = {d["topic"]: d for d in div["divergences"]}
    ahk = by_t["A股与港股"]
    assert ahk["desk_count"] >= 5  # hkpro + NVDA/HK07687 + gelonghui + 南向 + 两融
    assert all("user1" not in s["author"] for s in ahk["desk_samples"])
    assert ahk["street_count"] == 1  # two quote-enriched rankings excluded from directional street_score
    assert any(h.get("sentiment") == "人气榜" for h in div["hot_discussions"])

    long_prefix = "沪深两市核心芯片及算力产业链公司发布三季度业绩预告公告显示主营业务整体表现为"
    dedup_div = fs.build_sentiment_divergence(
        {"updated_at_bjt": "2026-11-27 21:00", "channels_status": [], "items": []},
        signals=[
            {"source": "财联社电报", "direction": "bullish", "tickers": ["SH600519"], "title": long_prefix + "净利增长", "url": "https://www.cls.cn/article?id=101"},
            {"source": "财联社电报", "direction": "bearish", "tickers": ["SH600519"], "title": long_prefix + "净利下滑", "url": "https://www.cls.cn/article?id=102"},
        ],
    )
    dedup_ahk = {d["topic"]: d for d in dedup_div["divergences"]}["A股与港股"]
    assert dedup_ahk["desk_count"] == 2
    assert dedup_ahk["desk_score"] == 0

    # Sign-sensitive dedup (+3.2% vs -3.2%) and malformed URL ("https://[") safety
    sign_div = fs.build_sentiment_divergence(
        {"updated_at_bjt": "2026-11-27 21:00", "channels_status": [], "items": []},
        signals=[
            {"source": "财联社电报", "direction": "bullish", "tickers": ["0700.HK"], "title": "港股腾讯盘中涨跌幅 +3.2%", "url": "https://www.cls.cn/q?id=1"},
            {"source": "财联社电报", "direction": "bearish", "tickers": ["0700.HK"], "title": "港股腾讯盘中涨跌幅 -3.2%", "url": "https://www.cls.cn/q?id=2"},
            {"source": "财联社电报", "direction": "bullish", "tickers": ["0700.HK"], "title": "港股腾讯南向净买入观测", "url": "https://["},
        ],
    )
    sign_ahk = {d["topic"]: d for d in sign_div["divergences"]}["A股与港股"]
    assert sign_ahk["desk_count"] == 3
    assert fs._safe_http_url("https://[") == ""
    assert fs._norm_desk_url("https://[") == ""

    # RZRQYE=None fallback: use RZYE+RQYE if both valid, else omit rzrq_balance_yi cleanly
    monkeypatch.setattr(
        fs,
        "_http_get_json",
        lambda u, **kw: {"result": {"data": [{"DIM_DATE": "2026-11-26", "RZYE": 2105578910371, "RQYE": 29063306261, "RZRQYE": None}]}}
        if "RPTA_RZRQ_LSHJ" in u
        else {},
    )
    mp_sum, _ = fs._fetch_cn_hk_market_pulse(timeout=3)
    assert mp_sum["rzrq_balance_display"] == "2.13万亿"
    monkeypatch.setattr(
        fs,
        "_http_get_json",
        lambda u, **kw: {"result": {"data": [{"DIM_DATE": "2026-11-26", "RZYE": 2105578910371, "RQYE": None, "RZRQYE": None}]}}
        if "RPTA_RZRQ_LSHJ" in u
        else {},
    )
    mp_partial, _ = fs._fetch_cn_hk_market_pulse(timeout=3)
    assert "rzrq_balance_yi" not in mp_partial

    # 4. Public projection strictly allowlists market_pulse & divergences[].desk_samples / street_samples scalar fields
    assert has_private_article_text("Margin balance: HK$12,500") is True
    assert has_private_article_text("Margin balance -$12,500") is True
    safe_proj = project_public_state({"risk_gate": {"light": "GREEN", "long_risk_cap": 1.0, "label": "绿灯"}, "forum_sentiment": div})
    assert safe_proj["forum_sentiment"]["market_pulse"]["southbound_net_yi_hkd"] == 45.87
    assert safe_proj["forum_sentiment"]["market_pulse"]["rzrq_balance_display"] == "2.13万亿"
    poisoned_div = json.loads(json.dumps(div, ensure_ascii=False))
    poisoned_div["divergences"][0]["portfolio"] = {"spot_balances": [{"symbol": "BTC", "qty": "0.112"}]}
    poisoned_div["divergences"][0]["desk_samples"].append(
        {"author": "a", "summary": "正常市场观察", "url": "https://x.com/a", "portfolio": {"spot_balances": [{"symbol": "BTC", "qty": "0.112"}]}}
    )
    poisoned = project_public_state(
        {
            "risk_gate": {"light": "GREEN", "long_risk_cap": 1.0, "label": "绿灯"},
            "forum_sentiment": {
                **poisoned_div,
                "portfolio": {"spot_balances": [{"symbol": "BTC", "qty": "0.112"}]},
                "market_pulse": {
                    "southbound_net_yi_hkd": 45.87,
                    "a_turnover_yi": 20000.0,
                    "a_turnover_display": "HACKED_STRING",
                    "portfolio": {"spot_balances": [{"symbol": "BTC", "qty": "0.112"}]},
                },
            },
        }
    )
    assert "portfolio" not in poisoned["forum_sentiment"]["market_pulse"]
    assert "spot_balances" not in json.dumps(poisoned["forum_sentiment"], ensure_ascii=False)
    assert poisoned["forum_sentiment"]["market_pulse"]["a_turnover_display"] == "2.00万亿"

    for bad_forum in ([{"portfolio": {"spot_balances": [{"symbol": "BTC", "qty": "0.112"}]}}], "Margin balance: HK$12,500"):
        bad_proj = project_public_state(
            {"risk_gate": {"light": "GREEN", "long_risk_cap": 1.0, "label": "绿灯"}, "forum_sentiment": bad_forum}
        )
        assert "forum_sentiment" not in bad_proj
