"""DemoLab-inspired multi-agent parallel Deep Research runner & job manager."""
from __future__ import annotations

import inspect
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from concurrent.futures import ALL_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any, Callable

_BACKEND_DIR = Path(__file__).resolve().parent
if str(_BACKEND_DIR.parent) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR.parent))

from backend import agy_runtime
from backend.guardrails import has_private_article_text as _has_sensitive_dr_text, scrub_markdown
from backend.runtime import (
    COCKPIT_ROOT,
    DATA_DIR,
    DEFAULT_GEMINI_API_EXE,
    atomic_write_json,
    now_bjt_iso as _now_bjt_iso,
    resolve_user_home,
)
from backend.article_store import DEFAULT_ARTICLES_DIR, ingest_deep_research_markdown
from backend.public_snapshot import project_public_state

JOBS_FILENAME = "deep_research_jobs.json"
MAX_JOBS_HISTORY = 20
DEMOLAB_SKILL_PATH = COCKPIT_ROOT / ".agents" / "skills" / "deep-research" / "SKILL.md"

_STAGE_LABELS: dict[str, str] = {
    "queued": "排队中",
    "orienting": "拆解课题",
    "gathering": "并行检索",
    "synthesizing": "交叉合成",
    "completed": "已完成",
    "failed": "失败",
}

_WIN_PATH_RE = re.compile(
    r'(?:"[A-Za-z]:[/\\][^"\r\n]+"|\'[A-Za-z]:[/\\][^\'\r\n]+\'|[A-Za-z]:[/\\][^\r\n"\'`,;)\]]+)'
)
_POSIX_PATH_RE = re.compile(
    r'(?:"/(?:Users|home|opt|var|tmp|etc|mnt|srv)/[^"\r\n]+"|\'/(?:Users|home|opt|var|tmp|etc|mnt|srv)/[^\'\r\n]+\'|/(?:Users|home|opt|var|tmp|etc|mnt|srv)/[^\s"\'`,;)\]]+)'
)
_ENV_ASSIGN_RE = re.compile(r'\b[A-Za-z_][A-Za-z0-9_]{2,}\s*=\s*(?:"[^"\r\n]*"|\'[^\'\r\n]*\'|\S+)')


class BusyError(RuntimeError):
    """Raised when a Deep Research run is already in progress."""


def _sanitize_diag_text(text: Any, *, max_len: int = 120) -> str:
    s = str(text or "").strip()
    if not s:
        return "执行未成功"
    s = _WIN_PATH_RE.sub("[本地路径]", s)
    s = _POSIX_PATH_RE.sub("[本地路径]", s)
    s = _ENV_ASSIGN_RE.sub("[环境配置]", s)
    if _has_sensitive_dr_text(s):
        return "[内容已脱敏]"
    s = scrub_markdown(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:max_len] or "执行未成功"


def _resolve_agy_bin() -> Path | None:
    try:
        return agy_runtime.resolve_agy_executable()
    except Exception:
        found = shutil.which("agy") or shutil.which("agy.exe")
        return Path(found) if found else None


def _resolve_staff_hub_script() -> tuple[str | None, Path | None]:
    node_exe = shutil.which("node")
    env_hub = (os.environ.get("MACROCOCKPIT_STAFF_HUB") or "").strip()
    candidates = [Path(env_hub)] if env_hub else []
    candidates.append(resolve_user_home() / ".claude" / "staff-hub" / "companion" / "agy-companion.mjs")
    script = next((p for p in candidates if p.is_file()), None)
    return node_exe, script


def probe_demolab_capabilities() -> dict[str, Any]:
    """Probe local DemoLab multi-agent Deep Research capabilities cleanly (booleans only)."""
    skill_found = DEMOLAB_SKILL_PATH.is_file()
    agy_found = _resolve_agy_bin() is not None
    node_exe, hub_script = _resolve_staff_hub_script()
    staff_hub_found = bool(node_exe and hub_script is not None)

    gemini_cli = shutil.which("gemini-api") or shutil.which("gemini-api.exe")
    if not gemini_cli and DEFAULT_GEMINI_API_EXE.is_file():
        gemini_cli = str(DEFAULT_GEMINI_API_EXE)
    has_gemini_key = bool(os.environ.get("GEMINI_API_KEY"))

    runners: list[str] = []
    if agy_found:
        runners.append("agy_acp")
    if staff_hub_found:
        runners.append("staff_hub")
    if gemini_cli and has_gemini_key:
        runners.append("gemini_api")

    return {
        "demolab_skill_found": skill_found,
        "agy_cli_found": agy_found,
        "staff_hub_found": staff_hub_found,
        "can_run_demolab_dr": bool(agy_found or staff_hub_found),
        "available_runners": runners,
    }


def _kill_proc_tree(proc: subprocess.Popen[str]) -> None:
    try:
        if os.name == "nt" and proc.poll() is None:
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True,
                    timeout=3,
                    check=False,
                )
            except OSError:
                proc.kill()
        elif proc.poll() is None:
            proc.kill()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
    finally:
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass


def _default_worker_runner(
    track_prompt: str,
    *,
    timeout: int = 75,
    cancel_event: threading.Event | None = None,
    _proc_registry: tuple[threading.Lock, set[subprocess.Popen[str]]] | None = None,
) -> dict[str, Any]:
    """Execute one sub-question via AGY ACP/CLI or staff-hub respecting cancellation and hard deadline."""
    clean = str(track_prompt or "").strip()
    if not clean:
        return {"ok": False, "runner": "none", "text": "", "error": "empty_prompt"}
    if cancel_event is not None and cancel_event.is_set():
        return {"ok": False, "runner": "none", "text": "", "error": "cancelled"}

    deadline = time.monotonic() + max(1.0, float(timeout))
    last_err = "runner_unavailable"

    agy_bin = _resolve_agy_bin()
    if agy_bin is not None:
        acp_err = ""
        try:
            rem = max(0.5, deadline - time.monotonic())
            res = agy_runtime.ask_agy(clean, transport="acp", timeout=rem, cancel_event=cancel_event)
            if res.get("ok") and str(res.get("reply") or "").strip():
                return {"ok": True, "runner": "agy_acp", "text": str(res["reply"]).strip()}
            acp_err = str(res.get("error") or "agy_empty_reply")
        except Exception as exc:
            acp_err = f"{type(exc).__name__}"

        # Never trigger CLI or fallback after timeout/cancellation or insufficient remaining budget
        low_err = acp_err.lower()
        rem_after = deadline - time.monotonic()
        if (
            (cancel_event is not None and cancel_event.is_set())
            or "timeout" in low_err
            or "cancelled" in low_err
            or rem_after <= 1.5
        ):
            return {"ok": False, "runner": "agy_acp", "text": "", "error": acp_err or "timeout"}

        try:
            res_cli = agy_runtime.ask_agy(
                clean,
                transport="cli",
                timeout=min(rem_after, 45.0),
                cancel_event=cancel_event,
            )
            if res_cli.get("ok") and str(res_cli.get("reply") or "").strip():
                return {"ok": True, "runner": "agy_cli", "text": str(res_cli["reply"]).strip()}
            last_err = str(res_cli.get("error") or acp_err or "agy_cli_failed")
        except Exception as exc:
            last_err = f"{type(exc).__name__}"

        low_cli = last_err.lower()
        if (
            (cancel_event is not None and cancel_event.is_set())
            or "timeout" in low_cli
            or "cancelled" in low_cli
            or (deadline - time.monotonic()) <= 1.5
        ):
            return {"ok": False, "runner": "agy_cli", "text": "", "error": last_err}
    else:
        last_err = "agy_not_found"

    node_exe, hub_script = _resolve_staff_hub_script()
    if node_exe and hub_script is not None and (deadline - time.monotonic()) > 1.5:
        if cancel_event is not None and cancel_event.is_set():
            return {"ok": False, "runner": "none", "text": "", "error": "cancelled"}
        proc: subprocess.Popen[str] | None = None
        try:
            with tempfile.TemporaryDirectory(prefix="mc-demolab-") as tmp_dir:
                p_file = Path(tmp_dir) / "prompt.txt"
                p_file.write_text(clean, encoding="utf-8")
                if cancel_event is not None and cancel_event.is_set():
                    return {"ok": False, "runner": "staff_hub", "text": "", "error": "cancelled"}
                proc = subprocess.Popen(
                    [
                        node_exe,
                        str(hub_script),
                        "ask",
                        "--backend",
                        "omp",
                        "--model",
                        "carbon",
                        "--prompt-file",
                        str(p_file),
                    ],
                    cwd=str(COCKPIT_ROOT),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                if _proc_registry is not None:
                    lock, proc_set = _proc_registry
                    with lock:
                        proc_set.add(proc)
                out_chunks: list[str] = []
                err_chunks: list[str] = []

                def _drain_pipe(stream: Any, sink: list[str]) -> None:
                    if stream is None:
                        return
                    try:
                        for chunk in iter(lambda: stream.read(4096), ""):
                            sink.append(chunk)
                    except (OSError, ValueError):
                        pass

                t_out = threading.Thread(target=_drain_pipe, args=(proc.stdout, out_chunks), daemon=True)
                t_err = threading.Thread(target=_drain_pipe, args=(proc.stderr, err_chunks), daemon=True)
                t_out.start()
                t_err.start()

                while proc.poll() is None:
                    if cancel_event is not None and cancel_event.is_set():
                        _kill_proc_tree(proc)
                        return {"ok": False, "runner": "staff_hub", "text": "", "error": "cancelled"}
                    if time.monotonic() >= deadline:
                        _kill_proc_tree(proc)
                        return {"ok": False, "runner": "staff_hub", "text": "", "error": "timeout"}
                    time.sleep(0.05)
                try:
                    proc.wait(timeout=max(0.2, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    _kill_proc_tree(proc)
                    return {"ok": False, "runner": "staff_hub", "text": "", "error": "timeout"}
                t_out.join(timeout=1.5)
                t_err.join(timeout=1.5)
                stdout = "".join(out_chunks)
                stderr = "".join(err_chunks)
                reply = (stdout or "").strip()
                if proc.returncode == 0 and reply:
                    return {"ok": True, "runner": "staff_hub", "text": reply}
                last_err = (stderr or f"staff_hub_rc_{proc.returncode}").strip()[:120]
        except subprocess.TimeoutExpired:
            last_err = "staff_hub_timeout"
        except Exception as exc:
            last_err = f"{type(exc).__name__}"
        finally:
            if proc is not None:
                if _proc_registry is not None:
                    lock, proc_set = _proc_registry
                    with lock:
                        proc_set.discard(proc)
                _kill_proc_tree(proc)

    return {"ok": False, "runner": "none", "text": "", "error": _sanitize_diag_text(last_err)}


def _extract_public_context(state: dict[str, Any] | None) -> str:
    """Build strictly public macro/market grounding text without any private portfolio exposure or secret leaks."""
    if not isinstance(state, dict) or not state:
        return "（当前未附带实时行情快照，请严格依据最新公开权威资料检索核实。）"
    pub = project_public_state(state, include_candidates=False)
    lines: list[str] = []
    gen_at = str(pub.get("generated_at_bjt") or "").strip()
    if gen_at and not _has_sensitive_dr_text(gen_at):
        lines.append(f"- 盘面基准时间：{gen_at}")
    rg = pub.get("risk_gate") or {}
    if isinstance(rg, dict) and rg.get("label"):
        lbl = scrub_markdown(rg.get("label"))
        if lbl and not _has_sensitive_dr_text(lbl):
            lines.append(f"- 宏观风控状态：{lbl}")

    fred_parts: list[str] = []
    wanted = {
        "us_10y": "美债10Y",
        "us_2y": "美债2Y",
        "cpi_yoy": "CPI同比",
        "fed_net_liquidity": "净流动性",
        "vix": "VIX",
        "dollar": "美元指数",
        "gold": "黄金",
    }
    for row in pub.get("macro_fred") or []:
        if isinstance(row, dict) and row.get("id") in wanted and row.get("value_display"):
            seg = f"{wanted[row['id']]} {row['value_display']}"
            if not _has_sensitive_dr_text(seg):
                fred_parts.append(seg)
    if fred_parts:
        lines.append("- 核心宏观实测：" + " · ".join(fred_parts[:6]))

    eq = pub.get("us_equities") or {}
    idx_parts: list[str] = []
    for row in (eq.get("indices") or [])[:3]:
        if isinstance(row, dict) and row.get("symbol") and row.get("price") is not None:
            seg = f"{row['symbol']} {row['price']} ({row.get('change_pct', '')}%)"
            if not _has_sensitive_dr_text(seg):
                idx_parts.append(seg)
    if idx_parts:
        lines.append("- 主要美股指数：" + " · ".join(idx_parts))

    btc = ((pub.get("crypto_quotes") or {}).get("spot") or {}).get("BTC-USDT")
    if isinstance(btc, dict) and btc.get("price"):
        seg = f"- BTC 现货：{btc.get('price')} ({btc.get('change_24h_pct', '')}%)"
        if not _has_sensitive_dr_text(seg):
            lines.append(seg)

    safe_lines = [ln for ln in lines if ln and not _has_sensitive_dr_text(ln)]
    return scrub_markdown("\n".join(safe_lines)) or "（公开市场快照背景）"


def _build_parallel_tracks(clean_prompt: str, public_context: str, *, symbol: str = "") -> list[dict[str, str]]:
    """Decompose topic into 3 non-overlapping DemoLab research sub-tracks."""
    sym_hint = f"（关联核心标的：{symbol.upper()}）" if symbol else ""
    common_rules = (
        "严格遵守：\n"
        "1. 只采用一手官方披露、权威机构或可核验原文数据，禁止把搜索摘要或自媒体臆测当结论；\n"
        "2. 关键数字必须带精确日期与单位，给出 `[出处](https://...)` 可点击链接；\n"
        "3. 不确定的信息如实标注在文末「信息缺口」，严禁编造数据或链接；\n"
        "4. 全文使用直白简体中文，禁止使用金融黑话或英文术语缩写堆砌。\n"
    )
    return [
        {
            "id": "track_primary",
            "label": "一手官方数据与披露核实",
            "prompt": (
                f"你是一手事实核查研究员{sym_hint}。仅负责「一手官方数据、监管文件与核心指标」维度，不要展开泛泛情绪或猜测。\n\n"
                f"【主研究课题】\n{clean_prompt}\n\n"
                f"【公开盘面背景】\n{public_context}\n\n"
                f"{common_rules}"
                "请列出至少 3 条可核验的一手数据、监管声明或财报原始口径对照，并指出可能存在的滞后或修订风险。"
            ),
        },
        {
            "id": "track_industry",
            "label": "产业传导与跨市场定价",
            "prompt": (
                f"你是产业与跨市场定价研究员{sym_hint}。仅负责「产业链上下游瓶颈、资本开支传导及跨资产定价差异」维度。\n\n"
                f"【主研究课题】\n{clean_prompt}\n\n"
                f"【公开盘面背景】\n{public_context}\n\n"
                f"{common_rules}"
                "请核查供需瓶颈、盈利预期变化与美股/A股/港股/大宗商品之间的定价传导，列出核心受益或承压环节。"
            ),
        },
        {
            "id": "track_counter",
            "label": "反方证伪与风险阈值",
            "prompt": (
                f"你是逆向风控研究员{sym_hint}。仅负责「反方证伪论据、历史失效案例与明确触发阈值」维度。\n\n"
                f"【主研究课题】\n{clean_prompt}\n\n"
                f"【公开盘面背景】\n{public_context}\n\n"
                f"{common_rules}"
                "请重点找出主流共识最容易翻车的反例、数据口径冲突，并给出 2~3 条可观测的证伪或风控切换阈值。"
            ),
        },
    ]


def _synthesize_fallback_markdown(
    *,
    title: str,
    clean_prompt: str,
    completed: list[dict[str, Any]],
    gaps: list[str],
) -> str:
    """Deterministic fallback report assembling verified track findings and explicit gaps."""
    resolved_title = scrub_markdown(title) or scrub_markdown(clean_prompt.splitlines()[0][:36])
    sec_lines = [
        f"# {resolved_title}",
        "",
        f"> 核心结论：已完成 {len(completed)}/3 路独立信源并行核查，围绕「{resolved_title}」整理了一手事实、产业传导与反方边界。下文逐一列出各路已核实证据，同时明确标注未能即时收回的缺口供决策交叉验证。",
        "",
        "## 1. 并行核查核心发现",
        "",
    ]
    for idx, item in enumerate(completed, 1):
        sec_lines.append(f"### 1.{idx} {item['label']}")
        sec_lines.append("")
        sec_lines.append(scrub_markdown(item["text"]))
        sec_lines.append("")
    sec_lines.append("## 2. 待核实与分歧项")
    sec_lines.append("")
    if gaps:
        for g in gaps:
            sec_lines.append(f"- {scrub_markdown(g)}")
    else:
        sec_lines.append("- 三路子课题均已收回，重点核对报告中具体触发阈值与最新一手公告日期。")
    return "\n".join(sec_lines).strip()


def _invoke_worker(
    exec_runner: Callable[..., dict[str, Any]],
    prompt: str,
    *,
    timeout: int,
    cancel_event: threading.Event,
    proc_registry: tuple[threading.Lock, set[subprocess.Popen[str]]],
) -> dict[str, Any]:
    try:
        sig = inspect.signature(exec_runner)
        kwargs: dict[str, Any] = {"timeout": timeout}
        if "cancel_event" in sig.parameters:
            kwargs["cancel_event"] = cancel_event
        if "_proc_registry" in sig.parameters:
            kwargs["_proc_registry"] = proc_registry
        return exec_runner(prompt, **kwargs)
    except TypeError:
        return exec_runner(prompt, timeout=timeout)


def run_demolab_deep_research(
    prompt: str,
    *,
    execute: bool = False,
    state: dict[str, Any] | None = None,
    articles_dir: Path | str | None = None,
    ingest: bool = True,
    title: str = "",
    tickers: list[str] | str | None = None,
    related_period: str = "",
    symbol: str = "",
    track_timeout: int = 75,
    synthesis_timeout: int = 75,
    runner_fn: Callable[..., dict[str, Any]] | None = None,
    on_progress: Callable[[str, dict[str, Any]], None] | None = None,
    defer_completed_emit: bool = False,
) -> dict[str, Any]:
    """Run DemoLab 5-stage parallel Deep Research (orient -> parallel gather -> straggler guard -> synthesize -> ingest)."""
    caps = probe_demolab_capabilities()
    if not execute:
        return {
            "executed": False,
            "status": "skipped_default_no_auto_call",
            "message": "默认不自动触发深研任务；请通过工作台「直接深研」按钮或 --run-demolab-dr 显式运行。",
            "capabilities": caps,
        }

    clean_sym = str(symbol or "").strip()
    clean_prompt = str(prompt or "").strip()
    if not clean_prompt:
        if clean_sym:
            clean_prompt = f"围绕标的 {clean_sym.upper()} 开展多维度深度研判：核实最新官方财报/监管披露、产业供需与算力/宏观传导、以及核心反方证伪阈值。"
        else:
            raise ValueError("深研课题提问词不能为空")

    clean_title = str(title or "").strip()
    tickers_txt = ",".join(str(x) for x in tickers) if isinstance(tickers, (list, tuple)) else str(tickers or "")
    for field_val in (clean_prompt, clean_title, clean_sym, tickers_txt, str(related_period or "")):
        if _has_sensitive_dr_text(field_val):
            raise ValueError("提问词、标题或参数包含敏感私人持仓或密钥信息，已拦截外发深研")

    exec_runner = runner_fn or _default_worker_runner
    if runner_fn is None and not caps["can_run_demolab_dr"]:
        return {
            "executed": False,
            "status": "no_available_runner",
            "message": "当前运行环境未检测到可用的本地多智能体执行器，请复制提问词手动深研并粘贴导入。",
            "capabilities": caps,
        }

    def _emit(stage: str, **extra: Any) -> None:
        if on_progress is not None:
            try:
                on_progress(stage, {"stage_label": _STAGE_LABELS.get(stage, stage), **extra})
            except Exception:
                pass

    # Stage 1: Orient & decompose
    _emit("orienting", tracks_total=3, tracks_succeeded=0)
    public_context = _extract_public_context(state)
    tracks = _build_parallel_tracks(clean_prompt, public_context, symbol=clean_sym)

    # Stage 2 & 3 & 4: Gather 3 tracks in parallel with hard process cancellation on stragglers
    _emit("gathering", tracks_total=len(tracks), tracks_succeeded=0)
    completed: list[dict[str, Any]] = []
    gaps: list[str] = []

    run_cancel = threading.Event()
    proc_registry: tuple[threading.Lock, set[subprocess.Popen[str]]] = (threading.Lock(), set())
    pool = ThreadPoolExecutor(max_workers=len(tracks))
    future_to_track: dict[Future[dict[str, Any]], dict[str, str]] = {
        pool.submit(
            _invoke_worker,
            exec_runner,
            tr["prompt"],
            timeout=track_timeout,
            cancel_event=run_cancel,
            proc_registry=proc_registry,
        ): tr
        for tr in tracks
    }
    try:
        done, not_done = wait(
            future_to_track.keys(),
            timeout=max(1, int(track_timeout)),
            return_when=ALL_COMPLETED,
        )
        if not_done:
            run_cancel.set()
            lock, proc_set = proc_registry
            with lock:
                procs_to_kill = list(proc_set)
            for p in procs_to_kill:
                _kill_proc_tree(p)

        for fut in done:
            tr = future_to_track[fut]
            try:
                res = fut.result()
            except Exception as exc:
                gaps.append(f"「{tr['label']}」执行异常（{_sanitize_diag_text(type(exc).__name__)}）")
                continue
            raw_txt = str((res or {}).get("text") or "").strip() if isinstance(res, dict) else ""
            if isinstance(res, dict) and res.get("ok") and raw_txt:
                if _has_sensitive_dr_text(raw_txt):
                    gaps.append(f"「{tr['label']}」返回内容触发隐私/密钥拦截，已隔离处理")
                    continue
                completed.append(
                    {
                        "id": tr["id"],
                        "label": tr["label"],
                        "runner": str(res.get("runner") or "agent"),
                        "text": raw_txt,
                    }
                )
            else:
                err_txt = _sanitize_diag_text((res or {}).get("error") or "未返回有效结论")
                gaps.append(f"「{tr['label']}」未收回有效结果（{err_txt}）")

        for fut in not_done:
            fut.cancel()
            tr = future_to_track[fut]
            gaps.append(f"「{tr['label']}」响应超时（已终止并跳过，待后续定向复核）")
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    # Order completed tracks consistently
    order_idx = {tr["id"]: idx for idx, tr in enumerate(tracks)}
    completed.sort(key=lambda r: order_idx.get(r["id"], 99))

    if not completed:
        return {
            "executed": True,
            "status": "failed",
            "tracks_total": len(tracks),
            "tracks_succeeded": 0,
            "unresolved_gaps": gaps,
            "error": _sanitize_diag_text("全部子课题均未能收回有效结果：" + "；".join(gaps[:3]), max_len=240),
        }

    # Stage 5: Synthesize grounded Markdown report
    _emit("synthesizing", tracks_total=len(tracks), tracks_succeeded=len(completed), unresolved_gaps=list(gaps))
    evidence_blocks = "\n\n".join(
        f"### 子课题：{item['label']}\n{item['text']}" for item in completed
    )
    gap_note = "\n".join(f"- {g}" for g in gaps) if gaps else "无超时缺失子课题"
    synth_prompt = (
        "你是主研究员，请交叉核验以下 3 路并行子课题的实测结果，合成为一篇可直接用于投研决策的中文 Markdown 深度报告。\n\n"
        "格式与质量要求：\n"
        "1. 第一行必须是简洁的一级标题 `# ...`（若已给定参考标题可优先采用或微调）；\n"
        "2. 标题后紧跟一行 `> 核心结论：...`（严格用两句话概括：第一句定调主判断，第二句写明核心风险与证伪阈值）；\n"
        "3. 分节展开事实依据、产业链传导与反方反例，所有引用保留可点击链接 `[出处](https://...)`；\n"
        "4. 文末必须保留 `## 待核实与分歧项` 小节，如实记录未收回子课题、数据源冲突或缺失项，禁止捏造事实；\n"
        "5. 全文只用直白中文，严禁出现英文黑话缩写或任何私人持仓金额。\n\n"
        + (f"【指定参考标题】{clean_title}\n" if clean_title else "")
        + f"【原始研究课题】\n{clean_prompt}\n\n"
        + f"【已收回子课题素材】\n{evidence_blocks}\n\n"
        + f"【超时或未完成缺口】\n{gap_note}\n"
    )

    synth_md = ""
    synth_cancel = threading.Event()
    synth_procs: tuple[threading.Lock, set[subprocess.Popen[str]]] = (threading.Lock(), set())
    try:
        synth_res = _invoke_worker(
            exec_runner,
            synth_prompt,
            timeout=synthesis_timeout,
            cancel_event=synth_cancel,
            proc_registry=synth_procs,
        )
        if isinstance(synth_res, dict) and synth_res.get("ok") and str(synth_res.get("text") or "").strip():
            cand = str(synth_res["text"]).strip()
            if not _has_sensitive_dr_text(cand):
                synth_md = cand
    except Exception:
        synth_md = ""

    if not synth_md:
        synth_md = _synthesize_fallback_markdown(
            title=clean_title,
            clean_prompt=clean_prompt,
            completed=completed,
            gaps=gaps,
        )
    elif gaps and "待核实" not in synth_md and "分歧" not in synth_md:
        synth_md = synth_md.rstrip() + "\n\n## 待核实与分歧项\n\n" + "\n".join(f"- {scrub_markdown(g)}" for g in gaps)

    clean_md = scrub_markdown(synth_md)
    res_out: dict[str, Any] = {
        "executed": True,
        "status": "completed",
        "tracks_total": len(tracks),
        "tracks_succeeded": len(completed),
        "unresolved_gaps": [_sanitize_diag_text(g, max_len=160) for g in gaps],
        "markdown": clean_md,
    }

    if ingest:
        art = ingest_deep_research_markdown(
            clean_md,
            title=clean_title,
            tickers=tickers or ([clean_sym.upper()] if clean_sym else None),
            related_period=related_period,
            articles_dir=articles_dir,
            publisher="DemoLab 多智能体深研",
            author_role="并行深研闭环",
        )
        res_out["ingested_article"] = art

    if not defer_completed_emit:
        _emit(
            "completed",
            tracks_total=len(tracks),
            tracks_succeeded=len(completed),
            unresolved_gaps=res_out["unresolved_gaps"],
        )
    return res_out


class DeepResearchJobManager:
    """In-process async coordinator for DemoLab Deep Research with single-flight concurrency lock."""

    def __init__(
        self,
        *,
        data_dir: Path | str = DATA_DIR,
        articles_dir: Path | str = DEFAULT_ARTICLES_DIR,
        state_getter: Callable[[], dict[str, Any]] | None = None,
        on_complete_article: Callable[[dict[str, Any]], int | None] | None = None,
        runner_fn: Callable[..., dict[str, Any]] | None = None,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.articles_dir = Path(articles_dir)
        self.state_getter = state_getter
        self.on_complete_article = on_complete_article
        self.runner_fn = runner_fn
        self._sem = threading.Semaphore(1)
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._order: list[str] = []
        self._load_persisted()

    @property
    def jobs_path(self) -> Path:
        return self.data_dir / JOBS_FILENAME

    def _load_persisted(self) -> None:
        if not self.jobs_path.is_file():
            return
        try:
            raw = json.loads(self.jobs_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(raw, list):
            return
        dirty = len(raw) > MAX_JOBS_HISTORY
        for row in raw[:MAX_JOBS_HISTORY]:
            if isinstance(row, dict) and row.get("job_id"):
                jid = str(row["job_id"])
                cleaned = dict(row)
                if "prompt" in cleaned:
                    cleaned.pop("prompt", None)
                    dirty = True
                if cleaned.get("status") in ("queued", "orienting", "gathering", "synthesizing"):
                    cleaned = {**cleaned, "status": "failed", "stage_label": "中断", "error": "服务重启导致后台深研中断"}
                    dirty = True
                if cleaned.get("error"):
                    sanitized_err = _sanitize_diag_text(cleaned["error"], max_len=200)
                    if sanitized_err != cleaned["error"]:
                        dirty = True
                    cleaned["error"] = sanitized_err
                if isinstance(cleaned.get("unresolved_gaps"), list):
                    sanitized_gaps = [_sanitize_diag_text(g, max_len=160) for g in cleaned["unresolved_gaps"]]
                    if sanitized_gaps != cleaned["unresolved_gaps"]:
                        dirty = True
                    cleaned["unresolved_gaps"] = sanitized_gaps
                self._jobs[jid] = cleaned
                self._order.append(jid)
        if dirty:
            self._persist_locked()

    def _persist_locked(self) -> None:
        rows = [self._jobs[jid] for jid in self._order[:MAX_JOBS_HISTORY] if jid in self._jobs]
        try:
            atomic_write_json(self.jobs_path, rows)
        except OSError:
            pass

    def _public_job(self, job: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in job.items() if k not in ("prompt",)}

    def list_jobs(self) -> list[dict[str, Any]]:
        with self._lock:
            return [self._public_job(self._jobs[jid]) for jid in self._order[:MAX_JOBS_HISTORY] if jid in self._jobs]

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(str(job_id or "").strip())
            return self._public_job(job) if job else None

    def start_job(
        self,
        prompt: str,
        *,
        title: str = "",
        tickers: list[str] | str | None = None,
        related_period: str = "",
        symbol: str = "",
        track_timeout: int = 75,
        synthesis_timeout: int = 75,
    ) -> dict[str, Any]:
        clean_prompt = str(prompt or "").strip()
        if not clean_prompt:
            raise ValueError("深研课题提问词不能为空")
        if len(clean_prompt) > 20000:
            raise ValueError("提问词过长（上限 20,000 字）")
        clean_title = str(title or "").strip()
        tickers_txt = ",".join(str(x) for x in tickers) if isinstance(tickers, (list, tuple)) else str(tickers or "")
        for val in (clean_prompt, clean_title, str(symbol or ""), tickers_txt, str(related_period or "")):
            if _has_sensitive_dr_text(val):
                raise ValueError("提问词、标题或参数包含敏感私人持仓或密钥信息，已拦截外发深研")

        if not self._sem.acquire(blocking=False):
            raise BusyError("已有深研任务正在运行，请等待当前任务完成")

        job_id = "dr_" + uuid.uuid4().hex[:10]
        now_iso = _now_bjt_iso()
        job: dict[str, Any] = {
            "job_id": job_id,
            "status": "queued",
            "stage_label": _STAGE_LABELS["queued"],
            "title": scrub_markdown(clean_title) or scrub_markdown(clean_prompt.splitlines()[0][:40]),
            "tickers": tickers if isinstance(tickers, list) else ([t.strip().upper() for t in str(tickers or "").split(",") if t.strip()] if tickers else []),
            "related_period": scrub_markdown(related_period),
            "created_at": now_iso,
            "updated_at": now_iso,
            "tracks_total": 3,
            "tracks_succeeded": 0,
            "unresolved_gaps": [],
            "article_filename": "",
            "article": None,
            "generation": None,
            "error": "",
        }
        with self._lock:
            self._jobs[job_id] = job
            self._order.insert(0, job_id)
            if len(self._order) > MAX_JOBS_HISTORY:
                for drop_id in self._order[MAX_JOBS_HISTORY:]:
                    self._jobs.pop(drop_id, None)
                self._order = self._order[:MAX_JOBS_HISTORY]
            self._persist_locked()

        def _update(patch: dict[str, Any]) -> None:
            with self._lock:
                cur = self._jobs.get(job_id)
                if cur is None:
                    return
                cur.update(patch)
                cur["updated_at"] = _now_bjt_iso()
                self._persist_locked()

        def _worker() -> None:
            try:
                state_snap: dict[str, Any] | None = None
                if self.state_getter is not None:
                    try:
                        state_snap = self.state_getter()
                    except Exception:
                        state_snap = None

                res = run_demolab_deep_research(
                    clean_prompt,
                    execute=True,
                    state=state_snap,
                    articles_dir=self.articles_dir,
                    ingest=True,
                    title=clean_title,
                    tickers=tickers,
                    related_period=related_period,
                    symbol=symbol,
                    track_timeout=track_timeout,
                    synthesis_timeout=synthesis_timeout,
                    runner_fn=self.runner_fn,
                    defer_completed_emit=True,
                    on_progress=lambda stg, info: _update(
                        {
                            "status": stg,
                            "stage_label": info.get("stage_label") or _STAGE_LABELS.get(stg, stg),
                            "tracks_total": info.get("tracks_total", 3),
                            "tracks_succeeded": info.get("tracks_succeeded", 0),
                            "unresolved_gaps": [_sanitize_diag_text(g, max_len=160) for g in (info.get("unresolved_gaps") or [])],
                        }
                    ),
                )
                if res.get("status") != "completed" or not res.get("ingested_article"):
                    _update(
                        {
                            "status": "failed",
                            "stage_label": _STAGE_LABELS["failed"],
                            "tracks_succeeded": res.get("tracks_succeeded", 0),
                            "unresolved_gaps": res.get("unresolved_gaps", []),
                            "error": _sanitize_diag_text(res.get("error") or res.get("message") or "深研未生成有效报告", max_len=200),
                        }
                    )
                    return

                art = dict(res["ingested_article"])
                out_gen: int | None = None
                if self.on_complete_article is not None:
                    out_gen = self.on_complete_article(art)
                    if out_gen is None:
                        raise RuntimeError("快照世代发布未返回有效版本号")
                _update(
                    {
                        "status": "completed",
                        "stage_label": _STAGE_LABELS["completed"],
                        "tracks_succeeded": res.get("tracks_succeeded", 0),
                        "unresolved_gaps": res.get("unresolved_gaps", []),
                        "article_filename": str(art.get("filename") or ""),
                        "article": art,
                        "generation": out_gen,
                        "error": "",
                    }
                )
            except Exception as exc:
                _update(
                    {
                        "status": "failed",
                        "stage_label": _STAGE_LABELS["failed"],
                        "error": _sanitize_diag_text(f"{type(exc).__name__}: {exc}", max_len=200),
                    }
                )
            finally:
                self._sem.release()

        th = threading.Thread(target=_worker, daemon=True, name=f"mc-dr-{job_id}")
        th.start()
        return self.get_job(job_id) or self._public_job(job)
