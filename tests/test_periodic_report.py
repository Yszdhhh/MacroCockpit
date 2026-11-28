"""Tests for backend.periodic_report (weekly/monthly generation, DR ingestion, privacy & capability checks)."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.kol_intel_engine import FORBIDDEN_TERMS
from backend.periodic_report import (
    check_deep_research_capabilities,
    generate_periodic_decision_report,
    ingest_deep_research_markdown,
    main as periodic_main,
    run_gemini_deep_research,
)
from backend.public_snapshot import has_private_article_content


def _sample_state(*, with_forum: bool = True, with_matrix: bool = True) -> dict[str, Any]:
    state: dict[str, Any] = {
        "macro_fred": [
            {"id": "fedfunds", "value_display": "3.88%", "change_display": "0 bps"},
            {"id": "us_10y", "value_display": "5.04%", "change_display": "-3 bps"},
            {"id": "us_2y", "value_display": "4.76%", "change_display": "-4 bps"},
            {"id": "us_10y_2y_spread", "value_display": "+0.28%", "change_display": "+1 bps"},
            {"id": "cpi_yoy", "value_display": "3.72%", "change_display": "+0.36 pct"},
            {"id": "fed_net_liquidity", "value_display": "$5.82T", "change_display": "-19.8B"},
            {"id": "vix", "value_display": "28.44", "change_display": "+3.88"},
            {"id": "dollar", "value_display": "122.65", "change_display": "+0.09%"},
            {"id": "gold", "value_display": "$3,577.2", "change_display": "+1.76%"},
            {"id": "wti", "value_display": "$89.53", "change_display": "+0.32%"},
            {"id": "btc_spot", "value_display": "$60,799", "change_display": "+4.20%"},
        ],
        "risk_gate": {
            "light": "YELLOW",
            "label": "黄灯 · 宏观冲击减半控险",
            "long_risk_cap": 0.5,
            "summary_exposure": "方向性多头 0% · 黄金避险 0% · 熊市配对 +35% BTC / -30% 山寨",
            "reasons": ["宏观波动抬升且 Regime 处于偏紧阶段"],
        },
        "positions": [
            {
                "id": "btc_trend",
                "name": "核心趋势仓 (BTC)",
                "state": "空仓观望",
                "baseline_pct": 0.0,
                "recommended_pct": 0.0,
                "est_notional_usd": 999999.0,  # private mock number that must NEVER leak
                "note": "趋势未确认，严禁左侧提前抄底 Sleeve 仓位",
            },
            {
                "id": "bear_basket",
                "name": "熊市配对对冲仓",
                "state": "持仓运行",
                "baseline_pct": 35.0,
                "recommended_pct": 35.0,
                "est_notional_usd": 35000.0,
                "note": "配对对冲执行 (+35% BTC / -30% 高流动性山寨)",
            },
        ],
        "us_equities": {
            "summary": "美股最新收盘：纳指 -0.62%，标普500ETF -0.29%；算力权重波动放大。",
            "indices": [
                {"symbol": "SPY", "name": "标普500 ETF", "price": 716.7, "change_pct": -0.29},
                {"symbol": "QQQ", "name": "纳指100 ETF", "price": 633.93, "change_pct": -0.65},
            ],
            "mag7_and_ai": [
                {"symbol": "NVDA", "price": 149.3, "change_pct": -3.91},
                {"symbol": "TSM", "price": 342.34, "change_pct": -1.96},
                {"symbol": "MU", "price": 601.25, "change_pct": -4.53},
                {"symbol": "META", "price": 609.89, "change_pct": 3.4},
            ],
        },
        "crypto_quotes": {
            "spot": {
                "BTC-USDT": {"symbol": "BTC", "price": "60798.7", "change_24h_pct": "4.13"},
                "ETH-USDT": {"symbol": "ETH", "price": "1702.81", "change_24h_pct": "3.92"},
            }
        },
        "correlations": {
            "pairs": [
                {"id": "btc_sp500", "label": "BTC × 标普500", "corr_30d": 0.64, "signal": "同向联动偏高"}
            ]
        },
        "kol_scorecards": [
            {
                "handle": "ShanghaoJin",
                "name": "Herman Jin",
                "role_label": "AI算力产业",
                "verified_calls": 14,
                "win_rate_5d": 64.3,
                "win_rate_20d": 71.4,
                "reliability_score": 87,
                "best_tickers": ["NVDA", "TSM", "AAPL"],
                "latest_stance": "偏空",
            },
            {
                "handle": "LowSampleKol",
                "name": "Newbie",
                "role_label": "宏观观察",
                "verified_calls": 2,
                "win_rate_5d": None,
                "win_rate_20d": None,
                "reliability_score": 50,
                "best_tickers": ["SPY"],
                "latest_stance": "偏多",
            },
        ],
        "kol_digest": [
            {
                "handle": "ShanghaoJin",
                "group": "AI与美股",
                "stance": "偏空",
                "score": -0.5,
                "verified_calls": 14,
                "win_rate_20d": 71.4,
                "reliability_score": 87,
                "takeaway": "加税对风险情绪形成半年期滞后压制",
            },
            {
                "handle": "SemiBull",
                "group": "AI与美股",
                "stance": "偏多",
                "score": 0.6,
                "verified_calls": 6,
                "win_rate_20d": 66.7,
                "reliability_score": 80,
                "takeaway": "CoWoS 先进封装排产仍维持景气，关注 N/A 待补 订单",
            },
        ],
        "market_analysis": {
            "headline_verdict": "全局定调「偏空」（黄灯控险）：美元流动性偏紧，算力短线承压。",
            "market_dimensions": [
                {
                    "id": "usd_liquidity",
                    "name": "美元流动性",
                    "status": "流动性偏紧",
                    "metric": "净流动性 $5.82T · 美元指数 122.65",
                    "implication": "强美元叠加流动性承压，高估值资产严格控制仓位。",
                }
            ],
            "cross_source_links": [
                {
                    "id": "link_nvda_ai_compute",
                    "focus": "NVDA 与 AI 算力供应链",
                    "tickers": ["NVDA", "TSM", "AVGO", "MU"],
                    "pattern": "多空分歧焦点",
                    "confidence": 78,
                    "conclusion": "快讯与长文对算力资本开支存在分歧，等待均线企稳。",
                    "action_trigger": "关注 NVDA 20日均线得失及 HBM 排产指引。",
                }
            ],
        },
        "articles": [
            {
                "filename": "guru_semianalysis_colossus.md",
                "source": "大佬分享",
                "publisher": "SemiAnalysis · 半导体深研",
                "title": "xAI Colossus 2 吉瓦级数据中心电力与算力拆解",
                "excerpt": "吉瓦级数据中心落地速度加快，电力变压器与 HBM 供给成为核心制约。",
                "tickers": ["NVDA", "MSFT", "ORCL"],
            }
        ],
        "calendar_events": [
            {"date": "2026-12-02", "title": "美国 ISM 制造业 PMI", "impact": "高优宏观"}
        ],
    }
    if with_forum:
        state["forum_sentiment"] = {
            "overall_crowd_temp": 74,
            "overall_label": "局部过热",
            "channels_status": [
                {"id": "xueqiu", "name": "雪球社区", "status": "实时在线", "count": 20, "note": "正常"},
                {"id": "reddit", "name": "Reddit", "status": "需登录态", "count": 0, "note": "待登录"},
            ],
            "divergences": [
                {
                    "topic": "美股AI与科技",
                    "desk_score": -15,
                    "desk_bias": "偏空",
                    "street_score": 68,
                    "street_heat": 82,
                    "divergence_tag": "散户过热",
                    "tone": "warn",
                    "action_hint": "散户追高热情偏高而机构谨慎，宜等待回踩确认",
                    "street_samples": [{"platform": "雪球", "title": "AI算力还能闭眼冲吗", "url": "https://xueqiu.com"}],
                }
            ],
        }
    if with_matrix:
        state["asset_source_matrix"] = {
            "categories": [
                {
                    "id": "us_ai_semi",
                    "name": "美股科技与AI算力半导体",
                    "supply_chain_bridges": [
                        {
                            "us_leader": "NVDA",
                            "us_name": "NVIDIA",
                            "focus": "Rubin 出货与高速互联需求",
                            "a_shares": [
                                {"symbol": "300476.SZ", "name": "胜宏科技", "sector": "高速PCB"},
                                {"symbol": "300308.SZ", "name": "中际旭创", "sector": "光模块"},
                            ],
                        }
                    ],
                }
            ]
        }
    return state


def _assert_no_forbidden(text: str) -> None:
    lowered = text.lower()
    for term in FORBIDDEN_TERMS:
        assert term.lower() not in lowered, f"Forbidden term found: {term!r}"


def test_generate_weekly_and_monthly_reports_and_fallbacks(tmp_path: Path) -> None:
    state = _sample_state(with_forum=True, with_matrix=True)
    weekly = generate_periodic_decision_report(
        state,
        period="weekly",
        articles_dir=tmp_path,
        as_of="2026-11-26T10:30:00+08:00",
    )
    assert weekly["filename"] == "weekly_2026-W48.md"
    assert weekly["publisher"] == "本地深研周报"
    assert weekly["author_role"] == "周期辅助决策"
    assert weekly["report_kind"] == "weekly"
    assert weekly["score"] == 92
    assert weekly["source"] == "周月度深研"
    assert not has_private_article_content(weekly)

    weekly_file = tmp_path / "weekly_2026-W48.md"
    assert weekly_file.is_file()
    raw_disk = weekly_file.read_text(encoding="utf-8")

    # Verify all 5 sections exist
    for idx in range(1, 6):
        assert f"## {idx}." in raw_disk
    assert "## 1. 本期定调与仓位风控结论" in raw_disk
    assert "## 2. 高胜率 KOL 共识 vs 全网舆论温度对照" in raw_disk
    assert "## 3. 主线资产、产业传导与关键数据复盘" in raw_disk
    assert "## 4. 逆向证伪清单与下阶段风险节点" in raw_disk
    assert "## 5. 待外部 Deep Research 深挖课题与一键提问词" in raw_disk

    # Verify 3~4 fenced code blocks for Deep Research prompts
    code_blocks = re.findall(r"```text\n(.*?)\n```", raw_disk, flags=re.DOTALL)
    assert 3 <= len(code_blocks) <= 4
    assert any("5.04%" in blk for blk in code_blocks)
    assert any("149.3" in blk or "NVDA" in blk for blk in code_blocks)

    # Verify real KOL & forum_sentiment & asset_source_matrix integration, zero private dollar amounts
    assert "@ShanghaoJin" in raw_disk
    assert "71.4%" in raw_disk
    assert "@LowSampleKol" not in raw_disk  # < 5 verified calls must not be in verified table
    assert "散户过热" in raw_disk
    assert "胜宏科技(300476.SZ" in raw_disk
    assert "999,999" not in raw_disk and "999999" not in raw_disk and "35,000" not in raw_disk
    _assert_no_forbidden(raw_disk)

    # force=False preserves existing file; force=True refreshes
    weekly_file.write_text(raw_disk.replace("本地深研周报", "本地深研周报"), encoding="utf-8")
    cached = generate_periodic_decision_report(
        state,
        period="weekly",
        articles_dir=tmp_path,
        as_of="2026-11-26T10:30:00+08:00",
        force=False,
    )
    assert cached["filename"] == "weekly_2026-W48.md"

    # Monthly report without forum_sentiment or asset_source_matrix must degrade gracefully
    minimal_state = _sample_state(with_forum=False, with_matrix=False)
    monthly = generate_periodic_decision_report(
        minimal_state,
        period="monthly",
        articles_dir=tmp_path,
        as_of="2026-11-26T10:30:00+08:00",
        force=True,
    )
    assert monthly["filename"] == "monthly_2026-11.md"
    assert monthly["publisher"] == "本地深研月报"
    assert monthly["report_kind"] == "monthly"
    monthly_disk = (tmp_path / "monthly_2026-11.md").read_text(encoding="utf-8")
    assert "未附带社区热榜温度切片" in monthly_disk
    _assert_no_forbidden(monthly_disk)


def test_ingest_deep_research_markdown_and_title_priority(tmp_path: Path) -> None:
    raw_with_fm = """---
title: 旧Frontmatter标题
author: external
---
# 2026年Q4美债供给与AI算力资本开支核查

这是一篇针对 **NVDA** 与 **TSM** 的深研补充，讨论当前 Regime 切换与待补数据 N/A 清理。
"""
    art = ingest_deep_research_markdown(
        raw_with_fm,
        tickers="nvda, tsm, $MU",
        related_period="2026-W48",
        articles_dir=tmp_path,
        url="https://gemini.google.com/app",
    )
    assert art["filename"].startswith("dr_") and art["filename"].endswith(".md")
    assert art["title"] == "2026年Q4美债供给与AI算力资本开支核查"
    assert art["publisher"] == "Deep Research 深度补充"
    assert art["author_role"] == "外部深研闭环"
    assert art["report_kind"] == "deep_research"
    assert art["score"] == 90
    assert art["source"] == "周月度深研"
    assert art["tickers"][:3] == ["NVDA", "TSM", "MU"]
    assert "关联周期研报：2026-W48" in art["body_md"]
    assert "旧Frontmatter标题" not in art["body_md"]
    _assert_no_forbidden(art["body_md"])

    # Explicit title overrides H1; fallback uses first 20 chars when no H1
    art_explicit = ingest_deep_research_markdown(
        "无标题纯文本深研结论，重点跟踪比特币与黄金流动性的季度变化趋势与风险边界。",
        title="",
        articles_dir=tmp_path,
    )
    assert art_explicit["title"] == "无标题纯文本深研结论，重点跟踪比特币与黄"


@pytest.mark.parametrize(
    "bad_md",
    [
        "当前账户占总净值 42%，需要减仓。",
        "以下是我的私人持仓明细：BTC 多单若干。",
        "调用凭证如下 sk-test1234567890abcdef",
        "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig",
        'api_key = "my-secret-value"',
        "My account balance is $14,900 and I bought 2.5 contracts of QQQ at $648 with liquidation price $91.29.",
    ],
)
def test_ingest_blocks_private_content_and_secrets(tmp_path: Path, bad_md: str) -> None:
    with pytest.raises(ValueError, match="内容包含敏感私人持仓或密钥信息，已拦截入库"):
        ingest_deep_research_markdown(bad_md, articles_dir=tmp_path)
    assert list(tmp_path.glob("*.md")) == []


def test_capabilities_and_no_auto_paid_api_call(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    secret_sentinel = "AIzaSyFakeTestSecretDoNotPrint"
    monkeypatch.setenv("GEMINI_API_KEY", secret_sentinel)
    monkeypatch.setattr("backend.periodic_report.shutil.which", lambda name: r"C:\bin\gemini-api.exe" if "gemini-api" in name else None)

    caps = check_deep_research_capabilities()
    assert caps["gemini_api_cli_found"] is True
    assert caps["has_gemini_api_key"] is True
    assert caps["can_run_gemini_dr"] is True
    assert caps["auto_paid_calls_disabled"] is True
    assert secret_sentinel not in json.dumps(caps, ensure_ascii=False)

    # Default run_gemini_deep_research NEVER triggers subprocess
    called = False

    def _fail_run(*args: Any, **kwargs: Any) -> Any:
        nonlocal called
        called = True
        raise AssertionError("Paid CLI must never be called by default")

    monkeypatch.setattr("backend.periodic_report.subprocess.run", _fail_run)
    skipped = run_gemini_deep_research("Check NVDA capex")
    assert skipped["executed"] is False
    assert skipped["status"] == "skipped_default_no_auto_paid_call"
    assert called is False

    # Missing key blocks execution even when execute=True
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    no_key = run_gemini_deep_research("Check NVDA capex", execute=True)
    assert no_key["executed"] is False
    assert no_key["status"] == "missing_gemini_api_key"
    assert called is False

    # Explicit execute=True with key invokes exact CLI and optionally ingests
    monkeypatch.setenv("GEMINI_API_KEY", secret_sentinel)
    recorded_cmds: list[list[str]] = []

    def _mock_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        recorded_cmds.append(cmd)
        payload = {
            "id": "v1_run_abc",
            "status": "completed",
            "steps": [
                {
                    "type": "model_output",
                    "content": [{"type": "text", "text": "# Gemini 深研结果\n\nNVDA 与 TSM 算力链实测分析。"}],
                }
            ],
        }
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr("backend.periodic_report.subprocess.run", _mock_run)
    res = run_gemini_deep_research(
        "Check NVDA capex",
        execute=True,
        ingest=True,
        articles_dir=tmp_path,
        related_period="2026-W48",
    )
    assert res["executed"] is True
    assert res["run_id"] == "v1_run_abc"
    assert recorded_cmds[0][1:6] == ["agent", "run", "Check NVDA capex", "--agent", "deep-research-preview-04-2026"]
    assert "--background" in recorded_cmds[0]
    assert res["ingested_article"]["report_kind"] == "deep_research"
    assert (tmp_path / res["ingested_article"]["filename"]).is_file()


def test_cli_status_weekly_and_ingest(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert periodic_main(["--status"]) == 0
    status_out = json.loads(capsys.readouterr().out)
    assert "has_gemini_api_key" in status_out
    assert isinstance(status_out["has_gemini_api_key"], bool)

    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps(_sample_state(), ensure_ascii=False), encoding="utf-8")
    arts_dir = tmp_path / "articles"

    assert periodic_main(
        [
            "--period",
            "weekly",
            "--state",
            str(state_file),
            "--articles-dir",
            str(arts_dir),
            "--as-of",
            "2026-11-26T12:00:00+08:00",
        ]
    ) == 0
    weekly_out = json.loads(capsys.readouterr().out)
    assert weekly_out["filename"] == "weekly_2026-W48.md"
    assert (arts_dir / "weekly_2026-W48.md").is_file()

    dr_src = tmp_path / "ext_dr.md"
    dr_src.write_text("# 外部深研测试\n\n重点核查 BTC 现货 ETF 与黄金避险需求。", encoding="utf-8")
    assert periodic_main(
        [
            "--ingest",
            str(dr_src),
            "--articles-dir",
            str(arts_dir),
            "--tickers",
            "BTC,GOLD",
            "--related-period",
            "2026-W48",
        ]
    ) == 0
    ingest_out = json.loads(capsys.readouterr().out)
    assert ingest_out["report_kind"] == "deep_research"
    assert (arts_dir / ingest_out["filename"]).is_file()
