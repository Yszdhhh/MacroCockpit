"""Tests for backend.deep_research_runner, server DR endpoints, CLI --run-demolab-dr, and MCP mode=demolab."""
from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend import deep_research_runner, mcp_server, periodic_report, server
from backend.kol_intel_engine import FORBIDDEN_TERMS
from backend.portfolio_store import PortfolioStore
from backend.public_snapshot import publish_generation


def _http(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
) -> tuple[int, str]:
    hdrs = dict(headers or {})
    data: bytes | None = None
    if payload is not None:
        hdrs["Content-Type"] = "application/json; charset=utf-8"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return int(r.status), r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read().decode("utf-8", errors="replace")


def test_default_skip_privacy_redline_and_straggler_guard(tmp_path: Path) -> None:
    # 1. Default execute=False never launches workers
    called = False

    def _never_run(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        nonlocal called
        called = True
        return {"ok": True, "text": "nope"}

    skipped = deep_research_runner.run_demolab_deep_research("Analyze NVDA CapEx", runner_fn=_never_run)
    assert skipped["executed"] is False
    assert skipped["status"] == "skipped_default_no_auto_call"
    assert called is False

    # 2. Private holdings / secrets are blocked before invoking any runner (prompt, title, tickers, context, subtrack outputs)
    for bad_prompt, bad_title, bad_tickers in (
        ("当前账户占总净值 45%，是否减仓 NVDA", "", None),
        ("我在英伟达的持仓金额为18万美元，如何对冲", "", None),
        ("常规课题", "我在英伟达的持仓金额为18万美元", None),
        ("调用 sk-secret1234567890abcdef 测试", "", None),
        ("常规课题", "", "AIzaSySecretTokenValue1234567890"),
    ):
        with pytest.raises(ValueError, match="敏感私人持仓或密钥"):
            deep_research_runner.run_demolab_deep_research(
                bad_prompt,
                title=bad_title,
                tickers=bad_tickers,
                execute=True,
                articles_dir=tmp_path,
                runner_fn=_never_run,
            )
    assert called is False

    # Poisoned state label & poisoned subtrack output must never reach workers or synthesis
    synth_seen: list[str] = []

    def _leak_check_runner(prompt: str, *, timeout: int = 75) -> dict[str, Any]:
        assert "AIzaSyPoisonState1234567890abcdef" not in prompt
        assert "sk-subtrackleak1234567890" not in prompt
        if prompt.startswith("你是主研究员"):
            synth_seen.append(prompt)
            return {"ok": True, "text": "# 脱敏核查\n\n> 核心结论：一切正常。\n\n## 待核实与分歧项\n\n- 已隔离。\n"}
        if "产业与跨市场定价研究员" in prompt:
            return {"ok": True, "text": "非法输出 sk-subtrackleak1234567890abcdef"}
        return {"ok": True, "text": "公开数据核查通过"}

    res_san = deep_research_runner.run_demolab_deep_research(
        "美股算力开支验证",
        execute=True,
        state={"risk_gate": {"light": "GREEN", "label": "绿灯 AIzaSyPoisonState1234567890abcdef"}},
        articles_dir=tmp_path,
        ingest=False,
        runner_fn=_leak_check_runner,
    )
    assert res_san["tracks_succeeded"] == 2 and synth_seen

    # Path & env diagnostic sanitization on errors/gaps
    res_diag = deep_research_runner.run_demolab_deep_research(
        "诊断脱敏测试",
        execute=True,
        articles_dir=tmp_path,
        ingest=False,
        runner_fn=lambda _p, **_k: {"ok": False, "error": r"failed at C:\Users\alice\secret.json GEMINI_API_KEY=secret123"},
    )
    assert "alice" not in json.dumps(res_diag, ensure_ascii=False)
    assert "secret123" not in json.dumps(res_diag, ensure_ascii=False)

    # 3. 3 parallel tracks with 1 stalled track canceled without blocking & FORBIDDEN_TERMS scrubbed
    seen_calls: list[str] = []

    def _mock_runner(prompt: str, *, timeout: int = 75) -> dict[str, Any]:
        seen_calls.append(prompt[:40])
        if prompt.startswith("你是主研究员"):
            return {
                "ok": True,
                "text": (
                    "# 2026年Q4 AI算力供应链与美债利率核查\n\n"
                    "> 核心结论：超大规模云厂 2026 年资本开支仍由真实采购承诺支撑，但 10 年期美债若突破 5.25% 将触发估值收缩。\n\n"
                    "## 1. 一手官方与产业证据\n\n"
                    "- [SEC 10-Q 披露](https://www.sec.gov) 显示 Tier-1 与 Regime 波动下的采购承诺维持高位。\n"
                    "- 台积电 CoWoS 封装排产紧凑，HBM3e 为核心刚性瓶颈。"
                ),
            }
        if "逆向风控研究员" in prompt:
            time.sleep(2.2)
            return {"ok": True, "text": "late straggler"}
        return {
            "ok": True,
            "runner": "mock_worker",
            "text": "实测 [SEC披露](https://www.sec.gov) 算力需求稳健，关注 10Y 美债 N/A 待补。",
        }

    t0 = time.monotonic()
    res = deep_research_runner.run_demolab_deep_research(
        "核查 NVDA 与美光 AI 算力资本开支周期，美元净流动性跌破 $5.6T 的触发阈值",
        execute=True,
        articles_dir=tmp_path,
        ingest=True,
        tickers="NVDA,MU",
        track_timeout=1,
        synthesis_timeout=5,
        runner_fn=_mock_runner,
    )
    elapsed = time.monotonic() - t0
    assert elapsed < 1.9, f"Straggler stalled coordinator ({elapsed:.2f}s)"
    assert res["executed"] is True and res["status"] == "completed"
    assert res["tracks_succeeded"] == 2
    assert len(res["unresolved_gaps"]) == 1
    assert "待核实与分歧项" in res["markdown"]
    for term in FORBIDDEN_TERMS:
        assert term.lower() not in res["markdown"].lower(), f"Forbidden term leaked: {term}"
    art = res["ingested_article"]
    assert art["publisher"] == "DemoLab 多智能体深研"
    assert (tmp_path / art["filename"]).is_file()


def test_http_research_run_jobs_capabilities_rbac_cli_and_mcp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    base = tmp_path / "srv"
    (base / "data").mkdir(parents=True)
    (base / "articles").mkdir(parents=True)
    state = {
        "risk_gate": {"light": "GREEN", "label": "绿灯 · 正常配置", "long_risk_cap": 1.0},
        "generated_at_bjt": "2026-11-28 12:00:00+08:00",
        "signals": [],
        "articles": [],
        "coverage": {"articles_count": 0, "periodic_reports_count": 0},
    }
    (base / "data" / "cockpit_state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    publish_generation(base, state, [], generation=1, producer_id="init")

    # 1. Consumer mode rejects execution & reports can_run_any_dr=False
    srv_cons = server.create_server(
        "127.0.0.1",
        0,
        web_dir=ROOT_DIR / "web",
        state_path=base / "data" / "cockpit_state.json",
        db_path=base / "data" / "user_journal.db",
        access_password="pw",
        read_token="tok-ro",
        rw_token="tok-rw",
        sync_token="tok-sync",
        role="consumer",
    )
    th_c = threading.Thread(target=srv_cons.serve_forever, daemon=True)
    th_c.start()
    try:
        u_c = f"http://127.0.0.1:{srv_cons.server_address[1]}"
        st_unauth, _ = _http(f"{u_c}/api/research/capabilities")
        assert st_unauth == 401
        st_ro, _ = _http(f"{u_c}/api/research/capabilities", headers={"Authorization": "Bearer tok-ro"})
        assert st_ro == 403
        st_caps, caps_body = _http(f"{u_c}/api/research/capabilities", headers={"Authorization": "Bearer tok-rw"})
        assert st_caps == 200
        assert json.loads(caps_body)["can_run_any_dr"] is False
        st_jobs_c, _ = _http(f"{u_c}/api/research/jobs", headers={"Authorization": "Bearer tok-rw"})
        assert st_jobs_c == 403
        st_run_c, _ = _http(
            f"{u_c}/api/research/run",
            method="POST",
            headers={"Authorization": "Bearer tok-rw"},
            payload={"prompt": "test consumer block"},
        )
        assert st_run_c == 403
    finally:
        srv_cons.shutdown()
        srv_cons.server_close()

    # 2. Standalone async job lifecycle, busy=409 lock, and atomic generation publish
    gate = threading.Event()

    def _fake_bg_runner(prompt: str, *, timeout: int = 75) -> dict[str, Any]:
        if "主研究员" not in prompt:
            gate.wait(timeout=3)
            return {"ok": True, "runner": "mock", "text": "一手 [美联储](https://www.federalreserve.gov) 净流动性保持充裕。"}
        return {
            "ok": True,
            "runner": "mock",
            "text": "# 美元净流动性深度核查\n\n> 核心结论：短期逆回购缓冲尚未耗尽，风险资产暂无流动性骤缩压力。关注 10Y 美债 5.25% 上沿阈值。\n\n## 待核实与分歧项\n\n- 暂无。",
        }

    srv = server.create_server(
        "127.0.0.1",
        0,
        web_dir=ROOT_DIR / "web",
        state_path=base / "data" / "cockpit_state.json",
        db_path=base / "data" / "user_journal.db",
        role="standalone",
    )
    srv.dr_manager.runner_fn = _fake_bg_runner
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        u = f"http://127.0.0.1:{srv.server_address[1]}"
        st_cap, cap_txt = _http(f"{u}/api/research/capabilities")
        assert st_cap == 200 and json.loads(cap_txt)["can_run_demolab_dr"] is True

        st_start, start_txt = _http(
            f"{u}/api/research/run",
            method="POST",
            payload={"prompt": "核查美元净流动性与美债供给节奏", "tickers": "US10Y,SPY"},
        )
        assert st_start == 202
        jid = json.loads(start_txt)["job"]["job_id"]

        # Concurrent run while first job holds semaphore returns 409
        st_busy, _ = _http(
            f"{u}/api/research/run",
            method="POST",
            payload={"prompt": "第二个并发任务"},
        )
        assert st_busy == 409

        # Simulate newer sync publish (gen=2) landing while DR job is paused in flight
        srv2_state = {
            "risk_gate": {"light": "YELLOW", "label": "黄灯 · 同步新世代", "long_risk_cap": 0.5},
            "generated_at_bjt": "2026-11-28 12:30:00+08:00",
            "signals": [{"id": "sig_gen2", "title": "Gen2 Newer Signal", "category": "macro", "source": "美联储官方", "time_bjt": "2026-11-28 12:29", "attention_score": 90}],
            "articles": [],
            "coverage": {"articles_count": 0, "periodic_reports_count": 0},
        }
        publish_generation(base, srv2_state, [], producer_id="concurrent-sync")

        gate.set()
        final_job = None
        for _ in range(50):
            _, j_txt = _http(f"{u}/api/research/jobs?job_id={jid}")
            final_job = json.loads(j_txt)["job"]
            if final_job["status"] in ("completed", "failed"):
                break
            time.sleep(0.05)
        assert final_job is not None and final_job["status"] == "completed"
        assert final_job["generation"] >= 3
        assert final_job["article_filename"].startswith("dr_")

        _, st_after_txt = _http(f"{u}/api/state")
        st_after = json.loads(st_after_txt)
        assert any(a.get("filename") == final_job["article_filename"] for a in st_after.get("articles") or [])
        assert any(s.get("id") == "sig_gen2" for s in st_after.get("signals") or [])
        assert st_after["risk_gate"]["light"] == "YELLOW"

        # Missing snapshot file without generation manifest must NOT deadlock on publish_ingested_article
        (base / "data" / "current_generation.json").unlink()
        (base / "data" / "cockpit_state.json").unlink()
        out_art, _ = srv.publish_ingested_article(
            {"filename": final_job["article_filename"], "title": "NoDeadlock", "source": "周月度深研"},
            producer_id="deadlock-check",
        )
        assert out_art["source"] == "周月度深研" and (base / "data" / "cockpit_state.json").is_file()
    finally:
        gate.set()
        srv.shutdown()
        srv.server_close()

    # ACP timeout must NOT trigger CLI fallback
    monkeypatch.setattr(deep_research_runner, "_resolve_agy_bin", lambda: Path("C:/fake/agy.exe"))
    monkeypatch.setattr(deep_research_runner, "_resolve_staff_hub_script", lambda: (None, None))
    seen_transports: list[str] = []

    def _mock_ask_agy(prompt: str, *, transport: str = "acp", **_kw: Any) -> dict[str, Any]:
        seen_transports.append(transport)
        return {"ok": False, "transport": transport, "reply": "", "error": "TimeoutError: AGY ACP timeout"}

    monkeypatch.setattr("backend.agy_runtime.ask_agy", _mock_ask_agy)
    acp_res = deep_research_runner._default_worker_runner("test acp timeout", timeout=5)
    assert acp_res["ok"] is False and seen_transports == ["acp"]

    # 3. CLI --run-demolab-dr and MCP deep_research_run(mode="demolab")
    monkeypatch.setattr(deep_research_runner, "_default_worker_runner", _fake_bg_runner)
    rc = periodic_report.main(
        [
            "--run-demolab-dr",
            "--prompt",
            "CLI 核查美股 AI 算力 CapEx",
            "--state",
            str(base / "data" / "cockpit_state.json"),
            "--articles-dir",
            str(base / "articles"),
            "--ingest-result",
        ]
    )
    assert rc == 0
    cli_out = json.loads(capsys.readouterr().out)
    assert cli_out["status"] == "completed" and cli_out["ingested_article"]["filename"].startswith("dr_")

    disp = mcp_server.McpDispatcher(store=PortfolioStore(base / "data" / "user_journal.db"), base_dir=base, desktop_pro=True)
    _, mcp_res = disp.dispatch_jsonrpc(
        {
            "jsonrpc": "2.0",
            "id": 99,
            "method": "tools/call",
            "params": {"name": "deep_research_run", "arguments": {"mode": "demolab", "symbol": "NVDA"}},
        },
        role="PORTFOLIO_RW",
        actor_id="desktop:pro",
    )
    assert mcp_res["result"].get("isError") is not True
    assert mcp_res["result"]["structuredContent"]["mode"] == "demolab"
    assert mcp_res["result"]["structuredContent"]["status"] == "completed"


def test_codex_followup_security_pipe_drain_and_first_gen_publish(tmp_path: Path, monkeypatch: Any) -> None:
    """Verify natural holding block, quoted/spaced diag disk scrub, >24KB child pipe drain, reentrant file_lock, and gen=1 init."""
    import shutil
    import sys

    from backend.public_snapshot import file_lock

    # 1. Natural first-person holding blocked; public TGA account text still allowed
    assert deep_research_runner._has_sensitive_dr_text("我持有 18 万美元的英伟达股票") is True
    assert deep_research_runner._has_sensitive_dr_text("美国财政部 TGA 账户余额变化") is False
    with pytest.raises(ValueError):
        deep_research_runner.run_demolab_deep_research("帮我分析我持有 18 万美元的英伟达股票风险", execute=True, runner_fn=lambda *_a, **_k: {"ok": True, "text": "x"})
    with pytest.raises(ValueError):
        deep_research_runner.run_demolab_deep_research("NVDA 基本面分析", title="我持有 18 万美元的英伟达股票", execute=True, runner_fn=lambda *_a, **_k: {"ok": True, "text": "x"})

    # 2. Quoted env var & forward-slash spaced Windows path scrubbed in memory AND persisted on disk
    job_dir = tmp_path / "scrub_jobs"
    job_dir.mkdir(parents=True)
    jobs_file = job_dir / deep_research_runner.JOBS_FILENAME
    jobs_file.write_text(
        json.dumps(
            [
                {
                    "job_id": "dr_dirty01",
                    "status": "failed",
                    "stage_label": "失败",
                    "prompt": "do not persist",
                    "error": 'WORKSPACE_VAR="preview FIXTURE_QUOTED_PRIVATE_VALUE" at C:/Program Files/Deep Research/FIXTURE_WINDOWS_PATH.txt',
                    "unresolved_gaps": [
                        'SECRET_MODE="leaky FIXTURE_QUOTED_PRIVATE_VALUE" C:/Program Files/Deep Research/FIXTURE_WINDOWS_PATH.txt'
                    ],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    mgr = deep_research_runner.DeepResearchJobManager(data_dir=job_dir, articles_dir=tmp_path / "arts")
    jobs = mgr.list_jobs()
    assert len(jobs) == 1
    assert "FIXTURE_QUOTED_PRIVATE_VALUE" not in jobs[0]["error"]
    assert "FIXTURE_WINDOWS_PATH" not in jobs[0]["error"]
    assert "FIXTURE_QUOTED_PRIVATE_VALUE" not in jobs[0]["unresolved_gaps"][0]
    assert "FIXTURE_WINDOWS_PATH" not in jobs[0]["unresolved_gaps"][0]
    raw_disk = jobs_file.read_text(encoding="utf-8")
    assert "FIXTURE_QUOTED_PRIVATE_VALUE" not in raw_disk
    assert "FIXTURE_WINDOWS_PATH" not in raw_disk
    assert "do not persist" not in raw_disk

    # 3. >24KB stdout pipe drain in staff_hub & acp_bridge CLI (no 8KB buffer deadlock)
    fake_hub = tmp_path / "fake_hub.py"
    fake_hub.write_text("import sys; sys.stdout.write('A' * 25000); sys.stdout.flush()\n", encoding="utf-8")
    monkeypatch.setattr(deep_research_runner, "_resolve_agy_bin", lambda: None)
    monkeypatch.setattr(deep_research_runner, "_resolve_staff_hub_script", lambda: (sys.executable, fake_hub))
    sh_res = deep_research_runner._default_worker_runner("pipe drain check", timeout=5)
    assert sh_res["ok"] is True and sh_res["runner"] == "staff_hub" and len(sh_res["text"]) == 25000

    fake_cli = tmp_path / "fake_agy.py"
    fake_cli.write_text("import sys; sys.stdout.write('B' * 25000); sys.stdout.flush()\n", encoding="utf-8")
    monkeypatch.setattr("backend.agy_runtime.resolve_agy_executable", lambda: fake_cli)
    orig_popen = deep_research_runner.subprocess.Popen
    import desktop.acp_bridge as acp_bridge

    monkeypatch.setattr(
        acp_bridge.subprocess,
        "Popen",
        lambda cmd, **kw: orig_popen([sys.executable, str(fake_cli)], **{k: v for k, v in kw.items()}),
    )
    cli_res = acp_bridge.ask_agy("pipe cli check", transport="cli", timeout=5)
    assert cli_res["ok"] is True and len(cli_res["reply"]) == 25000

    # 4. Reentrant file_lock & first-generation creation when no current_generation.json exists
    lock_target = tmp_path / "lock_dir" / "target.json"
    with file_lock(lock_target):
        with file_lock(lock_target):
            assert True

    fresh_dir = tmp_path / "fresh_first_gen"
    (fresh_dir / "data").mkdir(parents=True)
    (fresh_dir / "articles").mkdir(parents=True)
    (fresh_dir / "data" / "cockpit_state.json").write_text(
        json.dumps({"generated_at_bjt": "2026-11-28 12:00:00+08:00", "risk_gate": {"light": "GREEN", "label": "绿灯", "long_risk_cap": 1.0}, "signals": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    srv = server.create_server(
        "127.0.0.1",
        0,
        web_dir=Path("web"),
        state_path=fresh_dir / "data" / "cockpit_state.json",
        db_path=fresh_dir / "data" / "user_journal.db",
        articles_dir=fresh_dir / "articles",
        role="standalone",
        rw_token="rw-first-gen",
    )
    srv.dr_manager.runner_fn = lambda prompt, **_kw: {"ok": True, "runner": "mock", "text": "# 首代初始化报告\n\n> 核心结论：测试首代自动发布。已建立 generation=1。[来源](https://sec.gov/test)\n\n## 待核实与分歧项\n- 无"}
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        u = f"http://127.0.0.1:{srv.server_port}"
        req = urllib.request.Request(
            f"{u}/api/research/run",
            data=json.dumps({"prompt": "首代初始化深研"}).encode("utf-8"),
            method="POST",
            headers={"Authorization": "Bearer rw-first-gen", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            jid = json.loads(resp.read().decode("utf-8"))["job"]["job_id"]
        done_job = None
        for _ in range(60):
            with urllib.request.urlopen(f"{u}/api/research/jobs?job_id={jid}", timeout=5) as jr:
                done_job = json.loads(jr.read().decode("utf-8"))["job"]
            if done_job["status"] in ("completed", "failed"):
                break
            time.sleep(0.05)
        assert done_job is not None and done_job["status"] == "completed"
        assert done_job["generation"] >= 1
        assert (fresh_dir / "data" / "current_generation.json").is_file()
    finally:
        srv.shutdown()
        srv.server_close()
