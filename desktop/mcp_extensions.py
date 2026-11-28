"""Desktop-only callbacks for the shared MCP dispatcher."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from backend.mcp_server import _tool_err, _tool_ok


def build_desktop_mcp_handlers(base_dir: Path) -> dict[str, Callable[[dict[str, Any]], dict[str, Any]]]:
    def desktop_status(args: dict[str, Any]) -> dict[str, Any]:
        include_agy = bool(args.get("include_agy", True))
        out: dict[str, Any] = {"ok": True, "desktop_pro": True}
        try:
            from desktop.openbb_bridge import probe_openbb_status

            out["openbb"] = probe_openbb_status()
        except Exception as exc:
            out["openbb"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

        if include_agy:
            try:
                from desktop.acp_bridge import probe_agy

                out["agy"] = probe_agy()
            except Exception as exc:
                out["agy"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        return _tool_ok("桌面专业端状态探测完成", out)

    def openbb_query(args: dict[str, Any]) -> dict[str, Any]:
        dataset = str(args["dataset"]).strip()
        sym = str(args.get("symbol") or "").strip().upper()
        allow_sdk = bool(args.get("allow_sdk_import", False))
        try:
            from desktop.openbb_bridge import query_openbb

            res = query_openbb(dataset, symbol=sym, allow_sdk_import=allow_sdk)
        except Exception as exc:
            return _tool_err(f"OpenBB 查询失败: {type(exc).__name__}: {exc}", code=500)
        if isinstance(res, dict) and res.get("ok") is False:
            return _tool_err(str(res.get("error") or "OpenBB 查询返回错误"), code=400, detail=res)
        payload = res if isinstance(res, dict) else {"data": res}
        return _tool_ok(f"OpenBB {dataset} 查询成功 ({sym or 'macro'})", {"ok": True, "dataset": dataset, "symbol": sym, **payload})

    def deep_research_run(args: dict[str, Any]) -> dict[str, Any]:
        mode = str(args.get("mode") or "fast").strip().lower()
        sym = str(args.get("symbol") or "").strip().upper()
        if not sym:
            return _tool_err("fast/llm 模式必须提供 symbol", code=400)
        market = str(args.get("market") or "auto").strip().lower()
        inc_pf = args.get("include_portfolio", False)
        sync_web = bool(args.get("sync_to_web", False))
        try:
            from desktop.multi_agent_debate import run_multi_agent_debate

            report = run_multi_agent_debate(
                sym,
                market=market,
                mode=mode,
                base_dir=base_dir,
                include_portfolio=inc_pf,
                sync_to_web=sync_web,
            )
        except Exception as exc:
            return _tool_err(f"多智能体深研运行失败: {type(exc).__name__}: {exc}", code=500)
        verdict = report.get("trader_verdict") or {} if isinstance(report, dict) else {}
        stance = verdict.get("stance") or "研判完成"
        return _tool_ok(f"{sym} 多智能体深研完成（模式={mode} · 裁决={stance}）", {"ok": True, **(report if isinstance(report, dict) else {"report": report})})

    return {
        "desktop_status": desktop_status,
        "openbb_query": openbb_query,
        "deep_research_run": deep_research_run,
    }
