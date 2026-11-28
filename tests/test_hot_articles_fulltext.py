from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

COCKPIT_ROOT = Path(__file__).resolve().parent.parent
if str(COCKPIT_ROOT) not in sys.path:
    sys.path.insert(0, str(COCKPIT_ROOT))

from backend import rss_sources  # noqa: E402


class _FakeResponse:
    def __init__(
        self,
        status_code: int,
        *,
        text: str = "",
        json_obj: Any = None,
        url: str = "https://example.com/article",
    ) -> None:
        self.status_code = status_code
        if json_obj is not None:
            raw = json.dumps(json_obj, ensure_ascii=False)
        else:
            raw = text
        self.content = raw.encode("utf-8")
        self.text = raw
        self.encoding = "utf-8"
        self.url = url


def test_cls_depth_candidates_fetch_v3_detail_and_graceful_fallback(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(rss_sources, "FULLTEXT_CACHE_PATH", tmp_path / "ft_cache.json")

    list_payload = {
        "data": {
            "top_article": [
                {
                    "id": "2541702",
                    "title": "美股回调何时见底？高盛给出三大信号",
                    "brief": "①标普500指数已从历史高点回调12%；②高盛关注AI资本开支回报、美联储鸽派信号与仓位出清三大催化剂。",
                    "source": "财联社编辑",
                    "ctime": 1795423000,
                    "is_ad": 0,
                },
                {
                    "id": "2541703",
                    "title": "芯片供应链最新跟踪与指引",
                    "brief": "①台积电先进封装产能持续紧张；②算力服务器与HBM高端存储需求在2026年维持高位景气度。",
                    "source": "财联社编辑",
                    "ctime": 1795422000,
                    "is_ad": 0,
                },
            ],
            "depth_list": [],
        }
    }
    full_html = (
        "<p>" + ("财联社11月23日讯，高盛最新策略报告指出，标普500盈利预期与美联储降息路径决定本轮科技股反弹高度。" * 10) + "</p>"
    )

    def _fake_get(url: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None, timeout: int = 8):
        if "v3/depth/home/assembled" in url:
            return _FakeResponse(200, json_obj=list_payload, url=url)
        if "v3/article/detail" in url:
            if (params or {}).get("id") == "2541702":
                return _FakeResponse(200, json_obj={"errno": 0, "data": {"content": full_html}}, url=url)
            return _FakeResponse(500, text="error", url=url)
        return _FakeResponse(404, url=url)

    with patch.object(rss_sources.requests, "get", side_effect=_fake_get):
        cands = rss_sources._fetch_cls_depth_candidates(timeout=5)

    assert len(cands) == 2
    by_id = {c["id"]: c for c in cands}
    assert len(by_id["art-cls-2541702"]["body_md"]) > 600
    assert "核心要点" in by_id["art-cls-2541702"]["body_md"]
    assert "台积电先进封装产能持续紧张" in by_id["art-cls-2541703"]["body_md"]


def test_wscn_hot_articles_keeps_free_and_skips_paid(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(rss_sources, "FULLTEXT_CACHE_PATH", tmp_path / "ft_cache.json")

    hot_list = {
        "data": {
            "day_items": [
                {"id": "3786536", "title": "黄仁勋重掌英伟达帅印", "pageviews": 18000, "display_time": 1795420000},
                {"id": "3786599", "title": "VIP付费研报测试", "pageviews": 12000, "display_time": 1795420000},
            ]
        }
    }
    free_detail = {
        "data": {
            "id": 3786536,
            "title": "黄仁勋重掌英伟达帅印",
            "content_short": "英伟达董事会全票恢复黄仁勋职务，盘前涨超31%。",
            "is_paid": False,
            "is_priced": False,
            "display_time": 1795420000,
            "uri": "https://wallstreetcn.com/articles/3786536",
            "author": {"display_name": "华尔街见闻"},
            "content": "<p>" + ("英伟达董事会宣布恢复黄仁勋首席执行官职务，并就供应链与AI算力需求发表声明。" * 12) + "</p>",
        }
    }
    paid_detail = {
        "data": {
            "id": 3786599,
            "title": "VIP付费研报测试",
            "is_paid": True,
            "is_priced": True,
            "content": "<p>试读内容</p>",
        }
    }

    def _fake_get(url: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None, timeout: int = 8):
        if "articles/hot" in url:
            return _FakeResponse(200, json_obj=hot_list, url=url)
        if "articles/3786536" in url:
            return _FakeResponse(200, json_obj=free_detail, url=url)
        if "articles/3786599" in url:
            return _FakeResponse(200, json_obj=paid_detail, url=url)
        return _FakeResponse(404, url=url)

    with patch.object(rss_sources.requests, "get", side_effect=_fake_get):
        cands = rss_sources._fetch_wscn_hot_articles(timeout=5)

    assert len(cands) == 1
    assert cands[0]["id"] == "art-wscn-3786536"
    assert cands[0]["publisher"] == "华尔街见闻热门"
    assert len(cands[0]["body_md"]) > 400


def test_fetch_url_fulltext_guardrails_routing_and_scrubbing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(rss_sources, "FULLTEXT_CACHE_PATH", tmp_path / "ft_cache.json")

    # 1. SSRF & paywall domain blocks without HTTP calls
    with patch.object(rss_sources.requests, "get", side_effect=AssertionError("should not fetch")):
        assert rss_sources.fetch_url_fulltext_markdown("http://127.0.0.1/secret", use_cache=False) == ("", "blocked_ssrf")
        assert rss_sources.fetch_url_fulltext_markdown("http://localhost:8080/a", use_cache=False) == ("", "blocked_ssrf")
        assert rss_sources.fetch_url_fulltext_markdown("http://192.168.1.5/a", use_cache=False) == ("", "blocked_ssrf")
        assert rss_sources.fetch_url_fulltext_markdown("https://mp.weixin.qq.com/s/abc", use_cache=False) == ("", "skipped_paywall")
        assert rss_sources.fetch_url_fulltext_markdown("https://www.ft.com/content/123", use_cache=False) == ("", "skipped_paywall")

    # 2. Bot challenge stops cleanly without hitting Jina
    called_urls: list[str] = []

    def _bot_get(url: str, headers: dict[str, str] | None = None, timeout: int = 10):
        called_urls.append(url)
        return _FakeResponse(
            200,
            text="<html><title>Just a moment...</title><body>Enable JavaScript and cookies to continue</body></html>",
            url=url,
        )

    with patch.object(rss_sources.requests, "get", side_effect=_bot_get):
        body, st = rss_sources.fetch_url_fulltext_markdown("https://example.org/protected", use_cache=False)
    assert (body, st) == ("", "failed")
    assert called_urls == ["https://example.org/protected"]

    # 2b. 302 redirect to loopback/private IP is blocked BEFORE fetching the target URL
    redirect_calls: list[str] = []

    class _RedirResp(_FakeResponse):
        def __init__(self, status_code: int, location: str, url: str) -> None:
            super().__init__(status_code, text="", url=url)
            self.headers = {"Location": location}

    def _redir_get(url: str, headers: dict[str, str] | None = None, timeout: int = 10, allow_redirects: bool = False):
        redirect_calls.append(url)
        if "public.example.org" in url:
            return _RedirResp(302, "http://127.0.0.1:8080/internal", url)
        raise AssertionError(f"Should never request internal redirect target: {url}")

    with patch.object(rss_sources.requests, "get", side_effect=_redir_get):
        body, st = rss_sources.fetch_url_fulltext_markdown("https://public.example.org/redir", use_cache=False)
    assert (body, st) == ("", "blocked_ssrf")
    assert redirect_calls == ["https://public.example.org/redir"]

    # 3. Direct <article> HTML extraction + forbidden jargon scrubbing
    long_article = (
        "<html><head><title>AI Capex Report</title></head><body>"
        "<nav>Home Subscribe</nav>"
        "<article><h1>AI Infrastructure Outlook</h1>"
        "<p>In the current market Regime, Tier-1 cloud providers report N/A for short-term payback. "
        + ("Hyperscaler GPU demand and memory bandwidth remain tight across 2026 data centers. " * 12)
        + "</p></article></body></html>"
    )

    def _html_get(url: str, headers: dict[str, str] | None = None, timeout: int = 10):
        assert headers and headers.get("Accept-Encoding") == "gzip, deflate"
        return _FakeResponse(200, text=long_article, url=url)

    with patch.object(rss_sources.requests, "get", side_effect=_html_get):
        body, st = rss_sources.fetch_url_fulltext_markdown("https://example.org/post", use_cache=False)
    assert st == "direct_html"
    assert len(body) >= 400
    assert "Regime" not in body and "Tier-1" not in body and "N/A" not in body

    # 4. X / Twitter status routes to fxtwitter
    fx_json = {
        "tweet": {
            "article": {
                "title": "Compute Bottlenecks",
                "content": {"blocks": [{"text": "Deep breakdown of CoWoS packaging and HBM supply. " * 10}]},
            }
        }
    }

    def _fx_get(url: str, headers: dict[str, str] | None = None, timeout: int = 10):
        assert url.startswith("https://api.fxtwitter.com/analyst/status/212500001")
        return _FakeResponse(200, json_obj=fx_json, url=url)

    with patch.object(rss_sources.requests, "get", side_effect=_fx_get):
        body, st = rss_sources.fetch_url_fulltext_markdown("https://x.com/analyst/status/212500001", use_cache=False)
    assert st == "fxtwitter"
    assert body.startswith("# Compute Bottlenecks")

    # 5. Jina fallback when direct HTML is too short (JS SPA), scrubbing forbidden words
    def _jina_get(url: str, headers: dict[str, str] | None = None, timeout: int = 10):
        if url.startswith("https://r.jina.ai/"):
            jina_md = (
                "Title: JS Page\nURL Source: https://spa.example.org/post\nMarkdown Content:\n"
                "Regime analysis for NVDA and TSMC. " + ("Longform inference efficiency study details. " * 15)
            )
            return _FakeResponse(200, text=jina_md, url=url)
        return _FakeResponse(200, text="<html><body><div id='root'></div></body></html>", url=url)

    with patch.object(rss_sources.requests, "get", side_effect=_jina_get):
        body, st = rss_sources.fetch_url_fulltext_markdown("https://spa.example.org/post", use_cache=False)
    assert st == "jina"
    assert "Regime" not in body
    assert len(body) >= 400

    # 6. #9: FxTwitter & Jina reject 302 redirects (no auto-follow), and real requests DNS pins IP without 2nd lookup
    def _redir_302(url: str, headers: dict[str, str] | None = None, timeout: int = 10, allow_redirects: bool = False):
        if "api.fxtwitter.com" in url or "r.jina.ai" in url:
            assert allow_redirects is False
            return _RedirResp(302, "http://127.0.0.1:8080/internal", url)
        return _FakeResponse(200, text="<html><body><div id='root'></div></body></html>", url=url)

    with patch.object(rss_sources.requests, "get", side_effect=_redir_302):
        assert rss_sources.fetch_url_fulltext_markdown("https://x.com/analyst/status/212500001", use_cache=False) == ("", "failed")
        assert rss_sources.fetch_url_fulltext_markdown("https://spa.example.org/post", use_cache=False) == ("", "failed")

    # Test real socket connection pinning via loopback-disabled real requests Session + getaddrinfo anti-rebinding check
    import http.server
    import socket
    import threading
    import urllib3.util.connection as urllib3_conn

    class _SimpleHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            html_body = (
                "<html><body><article><p>"
                + ("Pinned IP verified without second DNS lookup across redirects. " * 12)
                + "</p></article></body></html>"
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html_body)))
            self.end_headers()
            self.wfile.write(html_body)

        def log_message(self, *args):
            pass

    httpd = http.server.HTTPServer(("127.0.0.1", 0), _SimpleHandler)
    th_http = threading.Thread(target=httpd.serve_forever, daemon=True)
    th_http.start()
    try:
        # Pretend public-rebind.example resolves to fake public IP on 1st call and 127.0.0.1 on 2nd call,
        # while routing create_connection for that fake public IP to our local test server.
        dns_calls = {"n": 0}
        real_create_conn = urllib3_conn.create_connection
        real_getaddrinfo = socket.getaddrinfo

        def _rebind_getaddrinfo(host, port, *a, **kw):
            if host in ("127.0.0.1", "::1"):
                return real_getaddrinfo(host, port, *a, **kw)
            if host == "public-rebind.example":
                dns_calls["n"] += 1
                ip = "93.184.216.34" if dns_calls["n"] == 1 else "127.0.0.1"
                return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, int(port or 80)))]
            raise AssertionError(f"Unexpected getaddrinfo for {host}")

        def _intercept_create_conn(address, *a, **kw):
            host_addr, port_num = address
            assert host_addr == "93.184.216.34", f"Used non-pinned address {host_addr}"
            return real_create_conn(("127.0.0.1", httpd.server_port), *a, **kw)

        for env_p in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            monkeypatch.delenv(env_p, raising=False)
        monkeypatch.setattr(rss_sources.socket, "getaddrinfo", _rebind_getaddrinfo)
        monkeypatch.setattr(urllib3_conn, "create_connection", _intercept_create_conn)
        body_pin, st_pin = rss_sources.fetch_url_fulltext_markdown("http://public-rebind.example/post", use_cache=False, allow_jina_fallback=False)
        assert st_pin == "direct_html"
        assert "Pinned IP verified" in body_pin
        assert dns_calls["n"] == 1
    finally:
        httpd.shutdown()
        httpd.server_close()
        th_http.join(timeout=3)


def test_aihot_hot_articles_and_balanced_selection(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(rss_sources, "FULLTEXT_CACHE_PATH", tmp_path / "ft_cache.json")

    ht_payload = {
        "items": [
            {
                "rank": 1,
                "id": "ht_wechat",
                "title": "公众号热文应跳过",
                "source": {"name": "公众号"},
                "links": {"original": "https://mp.weixin.qq.com/s/skipme"},
                "latestAt": "2026-11-23T10:00:00Z",
            },
            {
                "rank": 2,
                "id": "ht_tc",
                "title": "Anthropic IPO 前瞻分析",
                "source": {"name": "TechCrunch"},
                "links": {"original": "https://techcrunch.com/2026/11/23/anthropic-test/"},
                "latestAt": "2026-11-23T11:00:00Z",
            },
        ]
    }
    sel_payload = {
        "items": [
            {
                "id": "ht_tc",
                "title": "Anthropic IPO 前瞻分析",
                "summary": "多家机构重注 Anthropic。",
                "reason": "影响大模型估值锚。",
                "links": {"original": "https://techcrunch.com/2026/11/23/anthropic-test/"},
            }
        ]
    }
    tc_html = "<html><body><article><p>" + ("TechCrunch deep dive into Anthropic revenue run-rate and enterprise adoption. " * 12) + "</p></article></body></html>"

    def _fake_get(url: str, headers: dict[str, str] | None = None, timeout: int = 10):
        if "hot-topics" in url:
            return _FakeResponse(200, json_obj=ht_payload, url=url)
        if "items?mode=selected" in url:
            return _FakeResponse(200, json_obj=sel_payload, url=url)
        if "techcrunch.com" in url:
            return _FakeResponse(200, text=tc_html, url=url)
        raise AssertionError(f"Unexpected fetch: {url}")

    with patch.object(rss_sources.requests, "get", side_effect=_fake_get):
        arts = rss_sources._fetch_aihot_hot_articles(timeout=5)

    assert len(arts) == 1
    assert arts[0]["publisher"] == "AIHOT热榜"
    assert "多家机构重注 Anthropic" in arts[0]["body_md"]
    assert len(arts[0]["body_md"]) > 500

    # Balanced publisher selection enforces max_per_publisher=4
    cands = [
        {"title": f"CLS {i}", "publisher": "财联社深度", "attention_score": 95 - i, "time_bjt": "2026-11-23 12:00"}
        for i in range(6)
    ] + [
        {"title": f"WSCN {i}", "publisher": "华尔街见闻热门", "attention_score": 88 - i, "time_bjt": "2026-11-23 11:00"}
        for i in range(5)
    ] + [
        {"title": f"AIHOT {i}", "publisher": "AIHOT热榜", "attention_score": 84 - i, "time_bjt": "2026-11-23 10:00"}
        for i in range(4)
    ] + [
        {"title": f"RSS {i}", "publisher": "Import AI", "attention_score": 80 - i, "time_bjt": "2026-11-23 09:00"}
        for i in range(4)
    ]
    picked = rss_sources._select_balanced_articles(cands, limit=14, max_per_publisher=4)
    assert len(picked) == 14
    counts: dict[str, int] = {}
    for p in picked:
        counts[p["publisher"]] = counts.get(p["publisher"], 0) + 1
    assert all(cnt <= 4 for cnt in counts.values())
