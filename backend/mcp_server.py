"""MCP (2025-11-25) Streamable HTTP dispatcher and 8 orthogonal financial cockpit tools."""
from __future__ import annotations

import hmac
import json
import math
import os
import re
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from backend.guardrails import has_private_article_text
from backend.runtime import reject_json_constant, resolve_data_dir
from backend.calendar_events import filter_calendar_events
from backend.curation import rank_curated_signals
from backend.import_positions import PreviewExpiredError, PreviewValidationError, commit_import, preview_import
from backend.journal_store import RevisionConflictError
from backend.portfolio_store import IdempotencyConflictError, PortfolioStore
from backend.public_snapshot import load_published_generation, project_public_state

MCP_PROTOCOL_VERSION = "2025-11-25"
SUPPORTED_MCP_VERSIONS: frozenset[str] = frozenset({"2025-11-25", "2025-06-18", "2025-03-26"})

RO_TOOL_NAMES: frozenset[str] = frozenset(
    {"market_snapshot", "signal_search", "research_get", "portfolio_risk", "calendar_list"}
)
RW_ONLY_TOOL_NAMES: frozenset[str] = frozenset({"preferences_get", "import_preview", "workspace_commit"})
ALL_TOOL_NAMES: frozenset[str] = RO_TOOL_NAMES | RW_ONLY_TOOL_NAMES
DESKTOP_PRO_TOOL_NAMES: frozenset[str] = frozenset(
    {"desktop_status", "openbb_query", "deep_research_run", "sync_to_cloud"}
)

_VALID_CATEGORIES = ("all", "equities", "ashare", "ai", "crypto", "macro")
_VALID_SCOPES = ("all", "held", "watch", "rules", "high")
_VALID_DIRECTIONS = ("all", "bullish", "bearish", "neutral")

_PUBLIC_SIGNAL_FIELDS: tuple[str, ...] = (
    "id",
    "source",
    "source_tier",
    "source_tier_label",
    "category",
    "direction",
    "importance",
    "time_bjt",
    "title",
    "summary",
    "zh_brief",
    "why_it_matters",
    "tickers",
    "topics",
    "url",
    "attention_score",
    "base_score",
    "selected",
    "content_type",
    "axes",
    "cluster_id",
    "group_relation",
    "merged_sources",
    "merged_count",
    "heat_48h",
    "heat_badge",
    "participant_count",
    "is_backfill",
)


def _const_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(str(a or "").encode("utf-8"), str(b or "").encode("utf-8"))


def _get_header(headers: Any, name: str) -> str:
    if not headers:
        return ""
    target = name.lower()
    if hasattr(headers, "items"):
        for k, v in headers.items():
            if str(k).lower() == target:
                return str(v or "").strip()
    elif hasattr(headers, "get"):
        val = headers.get(name) or headers.get(target) or ""
        return str(val).strip()
    return ""


def _parse_origin(url_str: str) -> tuple[str, str] | None:
    raw = str(url_str or "").strip()
    if not raw or raw.lower() == "null":
        return None
    try:
        u = urllib.parse.urlparse(raw)
    except ValueError:
        return None
    scheme = (u.scheme or "").lower()
    netloc = (u.netloc or "").lower()
    if scheme not in ("http", "https") or not netloc:
        return None
    return (scheme, netloc)


def _is_loopback_origin(scheme: str, netloc: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(f"{scheme}://{netloc}")
        host = (parsed.hostname or "").lower()
    except ValueError:
        return False
    return host in ("127.0.0.1", "localhost", "::1")


def _origin_allowed(origin_header: str, *, host_header: str, public_url: str) -> bool:
    if not origin_header:
        return True
    parsed = _parse_origin(origin_header)
    if parsed is None:
        return False
    scheme, netloc = parsed
    if _is_loopback_origin(scheme, netloc):
        return True
    pub = _parse_origin(public_url)
    if pub is not None and (scheme, netloc) == pub:
        return True
    if host_header and netloc == host_header.strip().lower():
        return True
    return False


def _bucket_pct_10(value: Any) -> str:
    if value is None or str(value).strip() == "":
        return "无法估算"
    try:
        val = float(value)
    except (TypeError, ValueError):
        return "无法估算"
    neg = val < 0
    mag = abs(val)
    if mag >= 100.0:
        band = ">=100%"
    else:
        lo = int(mag // 10) * 10
        band = f"{lo}%-{lo + 10}%"
    if neg and mag > 0:
        return f"-({band})"
    return band


def _bucket_leverage(value: Any) -> str:
    if value is None or str(value).strip() == "":
        return "无法估算"
    try:
        val = abs(float(value))
    except (TypeError, ValueError):
        return "无法估算"
    if val < 0.5:
        return "0x-0.5x"
    if val < 1.0:
        return "0.5x-1x"
    if val < 2.0:
        return "1x-2x"
    if val < 3.0:
        return "2x-3x"
    return ">=3x"


def _bucket_liq_distance(value: Any) -> str:
    if value is None or str(value).strip() == "":
        return "无法估算"
    try:
        val = float(value)
    except (TypeError, ValueError):
        return "无法估算"
    if val < 10.0:
        return "<10%"
    if val < 20.0:
        return "10%-20%"
    if val < 50.0:
        return "20%-50%"
    return ">=50%"


def _parse_dt_prefix(raw: str | None) -> str:
    s = str(raw or "").strip()
    if not s:
        return ""
    # Normalize YYYY-MM-DD or YYYY-MM-DD HH:MM:SS into comparable lexical timestamp
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return s[:19]


def _filter_by_time_window(items: list[dict[str, Any]], date_from: str | None, date_to: str | None) -> list[dict[str, Any]]:
    f_str = _parse_dt_prefix(date_from)
    t_str = _parse_dt_prefix(date_to)
    if not f_str and not t_str:
        return list(items)
    t_bound = f"{t_str} 23:59:59" if len(t_str) == 10 else t_str
    f_bound = f"{f_str} 00:00:00" if len(f_str) == 10 else f_str
    out: list[dict[str, Any]] = []
    for it in items:
        tb = str(it.get("time_bjt") or "")[:19]
        if not tb:
            out.append(it)
            continue
        tb_norm = f"{tb} 00:00:00" if len(tb) == 10 else tb
        if f_bound and tb_norm < f_bound:
            continue
        if t_bound and tb_norm > t_bound:
            continue
        out.append(it)
    return out


def sanitize_public_signal(item: dict[str, Any]) -> dict[str, Any]:
    """Strip all private workspace/curation fields for READ_ONLY callers while preserving public 48h heat score."""
    clean: dict[str, Any] = {}
    for k in _PUBLIC_SIGNAL_FIELDS:
        if k in item:
            val = item[k]
            if k in ("title", "summary", "zh_brief", "why_it_matters") and isinstance(val, str) and has_private_article_text(val):
                continue
            clean[k] = val
    b_score = float(item.get("attention_score") or 0.0)
    base_i = int(round(b_score))
    q = max(0.0, min(1.0, (b_score - 55.0) / 25.0))
    is_backfill = bool(item.get("is_backfill"))
    part_count = int(item.get("participant_count") or 1)
    heat_48h = float(item.get("heat_48h") or 0.0)
    t_val = 3.0 * max(0.0, min(1.0, heat_48h / 100.0)) if (part_count >= 2 and not is_backfill) else 0.0
    qt_eff = round(q * t_val, 1)
    reasons = [f"客观 {base_i}"]
    if qt_eff > 0.0:
        reasons.append(f"热度 +{qt_eff:.1f}")
    clean["base_score"] = base_i
    clean["final_score"] = round(max(0.0, min(100.0, b_score + q * t_val)), 1)
    clean["personal_high"] = False
    clean["holding_radar"] = None
    clean["reasons"] = reasons
    return clean


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "market_snapshot",
        "description": "获取已发布的宏观指标、风控灯号、辅助仓位基准、美股盘面、汇率、加密行情、市场四维定调与高胜率KOL共识公开快照（只读）。",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "include": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": [
                            "risk_gate",
                            "positions",
                            "macro_fred",
                            "us_equities",
                            "fx_snapshot",
                            "crypto_quotes",
                            "correlations",
                            "coverage",
                            "market_analysis",
                            "kol_consensus",
                            "forum_sentiment",
                            "asset_source_matrix",
                        ],
                    },
                },
                "macro_group": {
                    "type": "string",
                    "enum": ["all", "美联储与利率", "跨资产温度", "加密流动性"],
                },
            },
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
    },
    {
        "name": "signal_search",
        "description": "按板块、子主题、方向、时间区间检索并排序财经/AI/A股/加密快讯；只读令牌禁止探测自选或指定持仓。",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "scope": {"type": "string", "enum": list(_VALID_SCOPES)},
                "category": {"type": "string", "enum": list(_VALID_CATEGORIES)},
                "subtopic": {"type": "string", "maxLength": 80},
                "instrument_id": {"type": "string", "maxLength": 24},
                "event_id": {"type": "string", "maxLength": 80},
                "direction": {"type": "string", "enum": list(_VALID_DIRECTIONS)},
                "from": {"type": "string", "maxLength": 40},
                "to": {"type": "string", "maxLength": 40},
                "sort": {"type": "string", "enum": ["importance", "time"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                "cursor": {"type": "string", "maxLength": 32},
            },
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
    },
    {
        "name": "research_get",
        "description": "读取核心美股的投研画像、1年期走势、SEC EDGAR 官方披露与开仓前纪律检查（只读仅返回公开检查）。",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["symbol"],
            "properties": {
                "symbol": {"type": "string", "minLength": 1, "maxLength": 10},
                "section": {
                    "type": "string",
                    "enum": ["all", "profile", "trend", "filings", "checklist"],
                },
            },
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
    },
    {
        "name": "portfolio_risk",
        "description": "查询组合风控汇总与策略偏离；READ_ONLY 仅可查 10% 脱敏区间 summary，detail 需要 PORTFOLIO_RW 权限。",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "view": {"type": "string", "enum": ["summary", "detail"]},
                "currency": {"type": "string", "enum": ["USD", "CNY"]},
            },
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
    },
    {
        "name": "calendar_list",
        "description": "读取美联储议息、CPI、非农就业及核心标的财报日历，严格区分精确倒计时与仅日期事件。",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "type": {"type": "string", "enum": ["all", "macro", "earnings", "fomc", "cpi", "nfp"]},
                "symbol": {"type": "string", "maxLength": 16},
                "from": {"type": "string", "maxLength": 40},
                "to": {"type": "string", "maxLength": 40},
                "sort": {"type": "string", "enum": ["importance", "time"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
    },
    {
        "name": "preferences_get",
        "description": "读取个人展示偏好、自选列表及信源筛选规则（仅 PORTFOLIO_RW）。",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "include_rules": {"type": "boolean"},
            },
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
    },
    {
        "name": "import_preview",
        "description": "解析自然文本或 CSV 持仓清单，执行币种与合约校验并生成 10 分钟有效的预览差异单（仅 PORTFOLIO_RW）。",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["text"],
            "properties": {
                "account_id": {"type": "string", "maxLength": 64},
                "format": {"type": "string", "enum": ["auto", "text", "csv"]},
                "mode": {"type": "string", "enum": ["upsert", "reconcile"]},
                "text": {"type": "string", "minLength": 1, "maxLength": 200000},
            },
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
    },
    {
        "name": "workspace_commit",
        "description": "基于 expected_revision 与 request_id 幂等提交持仓/规则/复盘变更或确认导入预览（仅 PORTFOLIO_RW）。",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["request_id", "expected_revision"],
            "properties": {
                "request_id": {"type": "string", "minLength": 1, "maxLength": 128},
                "expected_revision": {"type": "integer", "minimum": 0},
                "preview_id": {"type": "string", "minLength": 1, "maxLength": 128},
                "ops": {"type": "array", "minItems": 1, "maxItems": 100, "items": {"type": "object"}},
                "summary": {"type": "string", "maxLength": 240},
            },
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True},
    },
]

DESKTOP_PRO_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "desktop_status",
        "description": "探测本机 OpenBB Platform/Workspace 接口与 AGY ACP/MCP 通道就绪状态（仅桌面专业模式 PORTFOLIO_RW）。",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "include_agy": {"type": "boolean"},
            },
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
    },
    {
        "name": "openbb_query",
        "description": "通过桌面桥接查询 OpenBB 行情、基本面、宏观序列或加密数据（优先本地 HTTP，按需懒加载 SDK，禁止阻塞主进程）。",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["dataset"],
            "properties": {
                "dataset": {
                    "type": "string",
                    "enum": ["equity_quote", "equity_fundamental", "macro_series", "crypto_quote"],
                },
                "symbol": {"type": "string", "maxLength": 24},
                "allow_sdk_import": {"type": "boolean"},
            },
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
    },
    {
        "name": "deep_research_run",
        "description": "运行五角色多智能体深度研判（fast/llm）或 DemoLab 三路并行深研（demolab），产出研报并可选同步网页（仅桌面专业模式）。",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": [],
            "properties": {
                "symbol": {"type": "string", "minLength": 1, "maxLength": 24},
                "market": {"type": "string", "enum": ["auto", "us", "ashare", "hk", "crypto"]},
                "mode": {"type": "string", "enum": ["fast", "llm", "demolab"]},
                "prompt": {"type": "string", "minLength": 1, "maxLength": 20000},
                "title": {"type": "string", "maxLength": 160},
                "related_period": {"type": "string", "maxLength": 32},
                "include_portfolio": {"type": "boolean", "default": False},
                "sync_to_web": {"type": "boolean"},
            },
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
    },
    {
        "name": "sync_to_cloud",
        "description": "脱敏裁剪公开快照与精选长文并通过 SYNC_PUBLISH 令牌推送至云端驾驶舱；可选通过 PORTFOLIO_RW 增量同步私密持仓（仅桌面专业模式）。",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "refresh_state": {"type": "boolean"},
                "max_articles": {"type": "integer", "minimum": 0, "maximum": 100},
                "dry_run": {"type": "boolean"},
                "sync_portfolio": {"type": "boolean"},
            },
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
    },
]

_TOOLS_BY_NAME: dict[str, dict[str, Any]] = {t["name"]: t for t in TOOL_DEFINITIONS}
_DESKTOP_PRO_TOOLS_BY_NAME: dict[str, dict[str, Any]] = {t["name"]: t for t in DESKTOP_PRO_TOOL_DEFINITIONS}


def _validate_args(tool_def: dict[str, Any], raw_args: Any) -> tuple[dict[str, Any] | None, str | None]:
    schema = tool_def["inputSchema"]
    if raw_args is None:
        args: dict[str, Any] = {}
    elif isinstance(raw_args, dict):
        args = dict(raw_args)
    else:
        return (None, "arguments must be an object")

    props = schema.get("properties") or {}
    unknown = [k for k in args if k not in props]
    if unknown:
        return (None, f"未知参数（additionalProperties=false）: {', '.join(unknown)}")

    for req in schema.get("required") or []:
        if req not in args or args[req] is None or (isinstance(args[req], str) and not args[req].strip()):
            return (None, f"缺少必填参数: {req}")

    for k, val in args.items():
        spec = props[k]
        expected_type = spec.get("type")
        if expected_type == "string":
            if not isinstance(val, str):
                return (None, f"参数 {k} 须为字符串")
            if "minLength" in spec and len(val.strip()) < int(spec["minLength"]):
                return (None, f"参数 {k} 长度不足")
            if "maxLength" in spec and len(val) > int(spec["maxLength"]):
                return (None, f"参数 {k} 超出最大长度")
            if "enum" in spec and val not in spec["enum"]:
                return (None, f"参数 {k} 须为 {spec['enum']} 之一")
        elif expected_type == "integer":
            if isinstance(val, bool) or not isinstance(val, int):
                return (None, f"参数 {k} 须为整数")
            if "minimum" in spec and val < int(spec["minimum"]):
                return (None, f"参数 {k} 小于最小值 {spec['minimum']}")
            if "maximum" in spec and val > int(spec["maximum"]):
                return (None, f"参数 {k} 大于最大值 {spec['maximum']}")
        elif expected_type == "boolean":
            if not isinstance(val, bool):
                return (None, f"参数 {k} 须为布尔值")
        elif expected_type == "array":
            if not isinstance(val, list):
                return (None, f"参数 {k} 须为数组")
            if "minItems" in spec and len(val) < int(spec["minItems"]):
                return (None, f"参数 {k} 至少需要 {spec['minItems']} 项")
            if "maxItems" in spec and len(val) > int(spec["maxItems"]):
                return (None, f"参数 {k} 超过最大数量 {spec['maxItems']}")
            item_spec = spec.get("items") or {}
            for elem in val:
                if item_spec.get("type") == "string":
                    if not isinstance(elem, str):
                        return (None, f"参数 {k} 数组元素须为字符串")
                    if "enum" in item_spec and elem not in item_spec["enum"]:
                        return (None, f"参数 {k} 包含非法枚举值: {elem}")
                elif item_spec.get("type") == "object" and not isinstance(elem, dict):
                    return (None, f"参数 {k} 数组元素须为对象")
    return (args, None)


def _tool_ok(text: str, structured: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": structured,
        "isError": False,
    }


def _tool_err(text: str, *, code: int = 400, detail: dict[str, Any] | None = None) -> dict[str, Any]:
    sc = {"ok": False, "code": code, "error": text}
    if detail:
        sc.update(detail)
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": sc,
        "isError": True,
    }


def _load_rpc_json(raw: str) -> Any:
    return json.loads(raw, parse_constant=reject_json_constant)


def _rpc_result(req_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _rpc_error(req_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


class McpDispatcher:
    """Role-scoped MCP 2025-11-25 JSON-RPC dispatcher backed by published snapshots & PortfolioStore."""

    def __init__(
        self,
        *,
        store: PortfolioStore | None,
        base_dir: Path | str,
        public_url: str = "https://news.shuashuabao.xyz",
        desktop_pro: bool = False,
        include_desktop_tools: bool = False,
        desktop_handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] | None = None,
    ) -> None:
        self.store = store
        self.base_dir = Path(base_dir)
        self.public_url = str(public_url or "https://news.shuashuabao.xyz").rstrip("/")
        self.desktop_pro = bool(desktop_pro or include_desktop_tools)
        self.desktop_handlers = desktop_handlers or {}

    def _load_public_state(self) -> tuple[dict[str, Any] | None, int]:
        try:
            loaded = load_published_generation(self.base_dir)
            st = loaded.get("state") or {}
            gen = int(loaded.get("generation") or 0)
            return (st, gen)
        except FileNotFoundError:
            return (None, 0)

    def _audit(
        self,
        *,
        actor_id: str,
        role: str,
        action: str,
        resource: str = "",
        outcome: str = "ok",
        request_id: str = "",
        detail: dict[str, Any] | None = None,
    ) -> None:
        if self.store is None:
            return
        try:
            self.store.log_audit_event(
                actor_id=actor_id,
                role=role,
                action=action,
                resource=resource,
                outcome=outcome,
                request_id=request_id,
                detail=detail,
            )
        except Exception:
            pass

    def _visible_tools(self, role: str) -> list[dict[str, Any]]:
        if role == "PORTFOLIO_RW":
            tools = list(TOOL_DEFINITIONS)
            if self.desktop_pro:
                tools.extend(DESKTOP_PRO_TOOL_DEFINITIONS)
            return tools
        if role == "READ_ONLY":
            return [t for t in TOOL_DEFINITIONS if t["name"] in RO_TOOL_NAMES]
        return []

    def _call_tool(
        self,
        name: str,
        raw_args: Any,
        *,
        role: str,
        actor_id: str,
    ) -> tuple[dict[str, Any], int | None]:
        """Execute a validated tool call and return (mcp_tool_result, jsonrpc_error_code_if_protocol)."""
        is_desktop_tool = name in _DESKTOP_PRO_TOOLS_BY_NAME
        if is_desktop_tool and not self.desktop_pro:
            return ({}, -32602)
        if not is_desktop_tool and name not in _TOOLS_BY_NAME:
            return ({}, -32602)

        if (name in RW_ONLY_TOOL_NAMES or is_desktop_tool) and role != "PORTFOLIO_RW":
            self._audit(
                actor_id=actor_id,
                role=role,
                action=f"mcp:{name}",
                resource=name,
                outcome="denied",
                detail={"reason": "insufficient_role"},
            )
            return (_tool_err(f"权限不足 (403)：工具 {name} 仅限 PORTFOLIO_RW 调用", code=403), None)

        if role not in ("READ_ONLY", "PORTFOLIO_RW"):
            self._audit(
                actor_id=actor_id,
                role=role,
                action=f"mcp:{name}",
                resource=name,
                outcome="denied",
                detail={"reason": "forbidden_role"},
            )
            return (_tool_err("权限不足 (403)：当前角色禁止访问 MCP 工具", code=403), None)

        tool_def = _DESKTOP_PRO_TOOLS_BY_NAME[name] if is_desktop_tool else _TOOLS_BY_NAME[name]
        args, err_msg = _validate_args(tool_def, raw_args)
        if err_msg is not None or args is None:
            return (_tool_err(err_msg or "参数校验失败", code=400), None)

        # Dispatch each orthogonal tool
        if name == "market_snapshot":
            return (self._tool_market_snapshot(args), None)
        if name == "signal_search":
            return (self._tool_signal_search(args, role=role, actor_id=actor_id), None)
        if name == "research_get":
            return (self._tool_research_get(args, role=role), None)
        if name == "portfolio_risk":
            return (self._tool_portfolio_risk(args, role=role, actor_id=actor_id), None)
        if name == "calendar_list":
            return (self._tool_calendar_list(args), None)
        if name == "preferences_get":
            return (self._tool_preferences_get(args), None)
        if name == "import_preview":
            return (self._tool_import_preview(args, actor_id=actor_id), None)
        if name == "workspace_commit":
            return (self._tool_workspace_commit(args, role=role, actor_id=actor_id), None)
        if name == "deep_research_run" and str(args.get("mode")).strip().lower() == "demolab":
            return (self._tool_demolab_research(args), None)
        if name in ("desktop_status", "openbb_query", "deep_research_run"):
            handler = self.desktop_handlers.get(name)
            if handler is None:
                return (_tool_err(f"桌面工具 {name} 未连接，请通过桌面专业端调用", code=501), None)
            return (handler(args), None)
        if name == "sync_to_cloud":
            return (self._tool_sync_to_cloud(args, role=role), None)
        return ({}, -32601)

    @staticmethod
    def _summarize_market_analysis(raw_ma: Any) -> dict[str, Any] | None:
        if not isinstance(raw_ma, dict) or not raw_ma:
            return None
        dims = [
            {
                "id": d.get("id"),
                "name": d.get("name"),
                "status": d.get("status"),
                "tone": d.get("tone"),
                "metric": d.get("metric"),
                "implication": d.get("implication"),
            }
            for d in (raw_ma.get("market_dimensions") or [])
            if isinstance(d, dict)
        ]
        links = [
            {
                "id": lk.get("id"),
                "focus": lk.get("focus"),
                "tickers": lk.get("tickers") or [],
                "pattern": lk.get("pattern"),
                "tone": lk.get("tone"),
                "confidence": lk.get("confidence"),
                "conclusion": lk.get("conclusion"),
                "watch_trigger": lk.get("watch_trigger"),
            }
            for lk in (raw_ma.get("cross_source_links") or [])[:8]
            if isinstance(lk, dict)
        ]
        return {
            "as_of": raw_ma.get("as_of") or "",
            "stance": raw_ma.get("stance") or "中性",
            "headline_verdict": raw_ma.get("headline_verdict") or "",
            "market_dimensions": dims,
            "cross_source_links": links,
            "divergence_alerts": list(raw_ma.get("divergence_alerts") or [])[:6],
        }

    @staticmethod
    def _summarize_kol_consensus(pub: dict[str, Any]) -> dict[str, Any]:
        scorecards = [c for c in (pub.get("kol_scorecards") or []) if isinstance(c, dict)]
        digest = [d for d in (pub.get("kol_digest") or []) if isinstance(d, dict)]
        verified = [c for c in scorecards if int(c.get("verified_calls") or 0) >= 5]
        high_win = [
            c for c in verified
            if c.get("reliability_badge") == "高胜率" or (c.get("win_rate_20d") is not None and float(c["win_rate_20d"]) >= 60.0)
        ]
        top_pool = high_win or verified or scorecards
        top_kols = [
            {
                "handle": c.get("handle"),
                "name": c.get("name"),
                "role_label": c.get("role_label"),
                "reliability_score": c.get("reliability_score"),
                "reliability_badge": c.get("reliability_badge"),
                "verified_calls": c.get("verified_calls"),
                "win_rate_5d": c.get("win_rate_5d"),
                "win_rate_20d": c.get("win_rate_20d"),
                "avg_edge_20d_pct": c.get("avg_edge_20d_pct"),
                "best_tickers": c.get("best_tickers") or [],
                "latest_stance": c.get("latest_stance"),
                "latest_date": c.get("latest_date"),
                "latest_headline": c.get("latest_headline"),
            }
            for c in top_pool[:8]
        ]
        stance_src = verified if verified else digest
        stance_key = "latest_stance" if verified else "stance"
        bull_n = sum(1 for x in stance_src if x.get(stance_key) == "偏多")
        bear_n = sum(1 for x in stance_src if x.get(stance_key) == "偏空")
        neu_n = max(0, len(stance_src) - bull_n - bear_n)
        return {
            "total_scorecards": len(scorecards),
            "verified_authors_count": len(verified),
            "high_win_authors_count": len(high_win),
            "digest_count": len(digest),
            "stance_breakdown": {"偏多": bull_n, "偏空": bear_n, "中性": neu_n},
            "top_kols": top_kols,
        }

    def _tool_market_snapshot(self, args: dict[str, Any]) -> dict[str, Any]:
        raw_state, gen = self._load_public_state()
        if raw_state is None:
            return _tool_err("当前暂无已发布的公开快照 (503)", code=503)
        pub = project_public_state(raw_state, include_candidates=False)
        sections = args.get("include") or [
            "risk_gate",
            "positions",
            "macro_fred",
            "us_equities",
            "fx_snapshot",
            "crypto_quotes",
            "correlations",
            "coverage",
            "market_analysis",
            "kol_consensus",
            "forum_sentiment",
            "asset_source_matrix",
        ]
        macro_group = str(args.get("macro_group") or "all").strip()
        out: dict[str, Any] = {
            "ok": True,
            "generation": gen,
            "generated_at_bjt": pub.get("generated_at_bjt", ""),
        }
        for key in sections:
            if key == "market_analysis":
                out[key] = self._summarize_market_analysis(pub.get("market_analysis"))
                continue
            if key == "kol_consensus":
                out[key] = self._summarize_kol_consensus(pub)
                continue
            val = pub.get(key)
            if key == "macro_fred" and isinstance(val, list) and macro_group != "all":
                val = [m for m in val if isinstance(m, dict) and m.get("group") == macro_group]
            out[key] = val
        rg = pub.get("risk_gate") or {}
        ma_stance = (pub.get("market_analysis") or {}).get("stance") if isinstance(pub.get("market_analysis"), dict) else ""
        ma_suffix = f" · 定调 {ma_stance}" if ma_stance else ""
        summary_txt = f"快照世代 gen={gen} · {rg.get('label', '风险门控')}{ma_suffix} · 宏观 {len(pub.get('macro_fred') or [])} 项"
        return _tool_ok(summary_txt, out)

    def _tool_signal_search(self, args: dict[str, Any], *, role: str, actor_id: str) -> dict[str, Any]:
        scope = str(args.get("scope") or "all").strip().lower()
        category = str(args.get("category") or "all").strip().lower()
        subtopic = str(args.get("subtopic") or "").strip() or None
        instrument_id = str(args.get("instrument_id") or "").strip().upper() or None
        event_id = str(args.get("event_id") or "").strip() or None
        direction = str(args.get("direction") or "").strip().lower()
        if direction == "all":
            direction = ""
        date_from = args.get("from")
        date_to = args.get("to")
        sort_mode = str(args.get("sort") or "importance").strip().lower()
        limit = int(args.get("limit") or 25)
        cursor = args.get("cursor")

        if role == "READ_ONLY":
            if scope in ("held", "watch", "rules"):
                self._audit(
                    actor_id=actor_id,
                    role=role,
                    action="mcp:signal_search",
                    resource=f"scope:{scope}",
                    outcome="denied",
                    detail={"reason": "ro_private_scope_forbidden"},
                )
                return _tool_err(
                    "权限不足 (403)：READ_ONLY 禁止查询持仓、自选或规则专属范围",
                    code=403,
                )

        raw_state, gen = self._load_public_state()
        if raw_state is None:
            return _tool_err("当前暂无已发布的公开快照 (503)", code=503)

        pool = list(raw_state.get("signal_candidates") or raw_state.get("signals") or [])
        pool = _filter_by_time_window(pool, date_from, date_to)

        rules: list[dict[str, Any]] | None = None
        workspace: dict[str, Any] | None = None
        if role == "PORTFOLIO_RW" and self.store is not None:
            rules = self.store.list_curation_rules(enabled_only=True)
            if scope in ("held", "watch", "rules", "all"):
                workspace = self.store.build_workspace(public_state=raw_state)

        # Compute full matched length cleanly for next_cursor calculation
        offset = max(0, int(cursor)) if cursor not in (None, "") else 0
        all_matched = rank_curated_signals(
            pool,
            rules=rules,
            workspace=workspace,
            scope=scope,
            category=category,
            subtopic=subtopic,
            direction=direction or None,
            instrument_id=instrument_id,
            event_id=event_id,
            sort=sort_mode,
            cursor=None,
            limit=max(len(pool), 500),
        )
        total = len(all_matched)
        sliced = all_matched[offset : offset + limit]
        next_cursor = str(offset + limit) if (offset + limit) < total else None

        if role == "READ_ONLY":
            sliced = [sanitize_public_signal(x) for x in sliced]

        structured = {
            "ok": True,
            "generation": gen,
            "scope": scope,
            "category": category,
            "subtopic": subtopic or "",
            "sort": sort_mode,
            "total": total,
            "count": len(sliced),
            "next_cursor": next_cursor,
            "signals": sliced,
        }
        return _tool_ok(f"命中 {total} 条快讯，返回 {len(sliced)} 条（板块={category}）", structured)

    def _tool_research_get(self, args: dict[str, Any], *, role: str) -> dict[str, Any]:
        sym = str(args.get("symbol") or "").strip().upper()
        if not re.fullmatch(r"[A-Z0-9.]{1,10}", sym):
            return _tool_err("股票代码格式无效", code=400)
        section = str(args.get("section") or "all").strip().lower()

        raw_state, gen = self._load_public_state()
        if raw_state is None:
            return _tool_err("当前暂无已发布的公开快照 (503)", code=503)

        profiles = raw_state.get("stock_profiles") or {}
        prof = profiles.get(sym)
        if not isinstance(prof, dict):
            return _tool_err(f"暂未找到 {sym} 的已发布投研数据", code=404)

        out: dict[str, Any] = {
            "ok": True,
            "generation": gen,
            "symbol": sym,
            "name_cn": prof.get("name_cn", ""),
            "section": section,
        }
        if section in ("all", "profile"):
            out["profile"] = {
                k: v
                for k, v in prof.items()
                if k not in ("chart", "sec_filings", "pre_trade_checklist")
            }
        if section in ("all", "trend"):
            out["trend"] = {
                "price": prof.get("price"),
                "change_amt": prof.get("change_amt"),
                "change_pct": prof.get("change_pct"),
                "ma20": prof.get("ma20"),
                "ma50": prof.get("ma50"),
                "bias20_pct": prof.get("bias20_pct"),
                "bias50_pct": prof.get("bias50_pct"),
                "range_52w_pos": prof.get("range_52w_pos"),
                "as_of": prof.get("as_of"),
                "market_at": prof.get("market_at"),
                "received_at": prof.get("received_at"),
                "source": prof.get("source"),
                "session": prof.get("session"),
                "chart": prof.get("chart") or {},
            }
        if section in ("all", "filings"):
            out["filings"] = {
                "earnings_date": prof.get("earnings_date"),
                "sec_filings": prof.get("sec_filings") or [],
            }
        if section in ("all", "checklist"):
            chk = dict(prof.get("pre_trade_checklist") or {})
            if role == "PORTFOLIO_RW" and self.store is not None:
                ws = self.store.build_workspace(public_state=raw_state)
                spot_hit = [
                    {"account": s.get("account_name"), "sleeve": s.get("sleeve"), "weight_pct": s.get("weight_pct")}
                    for s in (ws.get("spot_balances") or [])
                    if str(s.get("instrument_id") or "").upper() == sym
                ]
                deriv_hit = [
                    {"account": d.get("account_name"), "side": d.get("side"), "leverage": d.get("leverage")}
                    for d in (ws.get("derivative_positions") or [])
                    if str(d.get("instrument_id") or "").upper() == sym
                ]
                chk["portfolio_overlap"] = {"spot": spot_hit, "derivatives": deriv_hit}
            out["checklist"] = chk

        return _tool_ok(
            f"{sym} ({prof.get('name_cn', '')}) 现价 {prof.get('price')} · {prof.get('verdict') or (prof.get('health') or {}).get('label', '')}",
            out,
        )

    def _tool_portfolio_risk(self, args: dict[str, Any], *, role: str, actor_id: str) -> dict[str, Any]:
        view = str(args.get("view") or "summary").strip().lower()
        currency = str(args.get("currency") or "USD").strip().upper()

        if role == "READ_ONLY" and view == "detail":
            self._audit(
                actor_id=actor_id,
                role=role,
                action="mcp:portfolio_risk",
                resource="view:detail",
                outcome="denied",
                detail={"reason": "ro_detail_forbidden"},
            )
            return _tool_err("权限不足 (403)：READ_ONLY 角色仅可查询 10% 区间脱敏汇总 summary，禁止查看 detail 持仓明细", code=403)

        raw_state, gen = self._load_public_state()
        rg = (raw_state or {}).get("risk_gate") or {}

        if self.store is None:
            # Pure --offline-public mode with zero local ledger created
            if role == "PORTFOLIO_RW":
                return _tool_err("离线只读模式未挂载本地账本", code=503)
            return _tool_ok(
                "只读区间摘要（未挂载私人账本）：0%-10% 区间",
                {
                    "ok": True,
                    "view": "summary",
                    "masked": True,
                    "bucket_width_pct": 10,
                    "generation": gen,
                    "risk_light": rg.get("light", "YELLOW"),
                    "long_risk_cap": rg.get("long_risk_cap", 0.5),
                    "net_direction_bucket": "0%-10%",
                    "effective_leverage_bucket": "0x-0.5x",
                    "min_liq_distance_bucket": "无法估算",
                    "strategy_bands": [],
                },
            )

        ws = self.store.build_workspace(public_state=raw_state, currency=currency)
        summary = ws.get("summary") or {}

        if role == "READ_ONLY":
            strategy_bands = []
            for row in ws.get("strategy_comparison") or []:
                rec_val = row.get("target_pct") if row.get("target_pct") is not None else row.get("recommended_pct")
                drift_val = row.get("delta_pp") if row.get("delta_pp") is not None else row.get("drift_pct")
                strategy_bands.append(
                    {
                        "id": row.get("id"),
                        "name": row.get("name"),
                        "actual_pct_bucket": _bucket_pct_10(row.get("actual_pct")),
                        "recommended_pct_bucket": _bucket_pct_10(rec_val),
                        "drift_pct_bucket": _bucket_pct_10(drift_val),
                    }
                )
            structured = {
                "ok": True,
                "view": "summary",
                "masked": True,
                "bucket_width_pct": 10,
                "generation": gen,
                "risk_light": rg.get("light", "YELLOW"),
                "long_risk_cap": rg.get("long_risk_cap", 1.0),
                "net_direction_bucket": _bucket_pct_10(summary.get("net_direction_pct")),
                "effective_leverage_bucket": _bucket_leverage(summary.get("effective_leverage")),
                "min_liq_distance_bucket": _bucket_liq_distance(summary.get("min_liq_distance_pct")),
                "strategy_bands": strategy_bands,
            }
            return _tool_ok(
                f"组合风险区间摘要（10% 脱敏）：净敞口 {structured['net_direction_bucket']} · 杠杆 {structured['effective_leverage_bucket']}",
                structured,
            )

        # PORTFOLIO_RW
        if view == "summary":
            return _tool_ok(
                f"组合风控摘要 (rev={ws.get('revision')}) · 净值 {summary.get('net_equity')} {ws.get('display_currency')}",
                {
                    "ok": True,
                    "view": "summary",
                    "masked": False,
                    "generation": gen,
                    "revision": ws.get("revision"),
                    "display_currency": ws.get("display_currency"),
                    "equity_mode": ws.get("equity_mode"),
                    "fx_status": ws.get("fx_status"),
                    "fx_rate": ws.get("fx_rate"),
                    "risk_gate": rg,
                    "summary": summary,
                    "strategy_comparison": ws.get("strategy_comparison") or [],
                    "unpriced_items": ws.get("unpriced_items") or [],
                },
            )

        return _tool_ok(
            f"组合全量持仓与风控明细 (rev={ws.get('revision')}) · 现货 {len(ws.get('spot_balances') or [])} / 合约 {len(ws.get('derivative_positions') or [])}",
            {
                "ok": True,
                "view": "detail",
                "masked": False,
                "generation": gen,
                **ws,
            },
        )

    def _tool_calendar_list(self, args: dict[str, Any]) -> dict[str, Any]:
        ev_type = str(args.get("type") or "all").strip().lower()
        sym = str(args.get("symbol") or "").strip().upper() or None
        date_from = str(args.get("from") or "").strip() or None
        date_to = str(args.get("to") or "").strip() or None
        limit = int(args.get("limit") or 30)

        raw_state, gen = self._load_public_state()
        if raw_state is None:
            return _tool_err("当前暂无已发布的公开快照 (503)", code=503)

        events = list(raw_state.get("calendar_events") or [])
        cal_event_type = None if ev_type in ("all", "macro") else ev_type
        filtered = filter_calendar_events(
            events,
            start_date=date_from,
            end_date=date_to,
            days_ahead=None if date_to else 365,
            event_type=cal_event_type,
            scope="held" if sym else "market",
            held_symbols=[sym] if sym else None,
        )
        if ev_type == "macro":
            filtered = [e for e in filtered if str(e.get("event_type") or "").lower() in ("fomc", "cpi", "nfp")]
        filtered = filtered[:limit]
        return _tool_ok(
            f"日历事件 {len(filtered)} 条（类型={ev_type}）",
            {
                "ok": True,
                "generation": gen,
                "type": ev_type,
                "symbol": sym or "",
                "count": len(filtered),
                "events": filtered,
            },
        )

    def _tool_preferences_get(self, args: dict[str, Any]) -> dict[str, Any]:
        if self.store is None:
            return _tool_err("本地账户库未初始化", code=503)
        inc_rules = args.get("include_rules", True)
        prefs = self.store.get_preferences()
        rules = self.store.list_curation_rules() if inc_rules else []
        rev = self.store.get_revision()
        return _tool_ok(
            f"个人偏好与 {len(rules)} 条筛选规则 (rev={rev})",
            {
                "ok": True,
                "revision": rev,
                "preferences": prefs,
                "curation_rules": rules,
            },
        )

    def _tool_import_preview(self, args: dict[str, Any], *, actor_id: str) -> dict[str, Any]:
        if self.store is None:
            return _tool_err("本地账户库未初始化", code=503)
        try:
            res = preview_import(
                self.store,
                args.get("account_id"),
                str(args.get("format") or "auto"),
                str(args.get("text") or ""),
                str(args.get("mode") or "upsert"),
                actor_id,
            )
        except ValueError as exc:
            return _tool_err(str(exc), code=400)
        return _tool_ok(
            f"解析完成 preview_id={res['preview_id']} · {res['ops_count']} 项操作 · errors={res['has_errors']}",
            {"ok": True, **res},
        )

    def _tool_workspace_commit(self, args: dict[str, Any], *, role: str, actor_id: str) -> dict[str, Any]:
        if self.store is None:
            return _tool_err("本地账户库未初始化", code=503)
        req_id = str(args["request_id"]).strip()
        exp_rev = int(args["expected_revision"])
        preview_id = args.get("preview_id")
        ops = args.get("ops")
        summary = str(args.get("summary") or "")

        if bool(preview_id) == bool(ops):
            return _tool_err("preview_id 与 ops 须二选一且互斥", code=400)

        try:
            if preview_id:
                res = commit_import(
                    self.store,
                    str(preview_id),
                    req_id,
                    exp_rev,
                    actor_id,
                    role=role,
                )
            else:
                res = self.store.apply_changes(
                    request_id=req_id,
                    expected_revision=exp_rev,
                    ops=list(ops or []),
                    actor_id=actor_id,
                    role=role,
                    summary=summary,
                )
        except (RevisionConflictError, IdempotencyConflictError) as exc:
            return _tool_err(str(exc), code=409)
        except (PreviewExpiredError, PreviewValidationError, ValueError) as exc:
            return _tool_err(str(exc), code=400)
        except KeyError as exc:
            return _tool_err(str(exc), code=404)
        except PermissionError as exc:
            return _tool_err(str(exc), code=403)

        new_rev = res.get("new_revision", res.get("revision"))
        return _tool_ok(
            f"提交成功 new_revision={new_rev}",
            {"ok": True, **res},
        )

    def _tool_demolab_research(self, args: dict[str, Any]) -> dict[str, Any]:
        sym = str(args.get("symbol") or "").strip().upper()
        prompt = str(args.get("prompt") or "").strip()
        if not prompt and not sym:
            return _tool_err("demolab 模式至少需要提供 prompt 或 symbol", code=400)
        try:
            from backend.deep_research_runner import run_demolab_deep_research

            state_file = resolve_data_dir(self.base_dir) / "cockpit_state.json"
            state_snap = json.loads(state_file.read_text(encoding="utf-8")) if state_file.is_file() else {}
            res = run_demolab_deep_research(
                prompt,
                execute=True,
                state=state_snap,
                articles_dir=self.base_dir / "articles",
                ingest=True,
                title=str(args.get("title") or ""),
                tickers=[sym] if sym else None,
                related_period=str(args.get("related_period") or ""),
                symbol=sym,
            )
        except ValueError as exc:
            return _tool_err(str(exc), code=400)
        except Exception as exc:
            return _tool_err(f"DemoLab 并行深研运行失败: {type(exc).__name__}: {exc}", code=500)
        if res.get("status") != "completed":
            return _tool_err(str(res.get("error") or res.get("message") or "DemoLab 并行深研未完成"), code=500, detail=res)
        art = res.get("ingested_article") or {}
        return _tool_ok(
            f"DemoLab 并行深研完成（{res.get('tracks_succeeded', 0)}/{res.get('tracks_total', 3)} 路收回 · 已入库 {art.get('filename', '')}）",
            {"ok": True, "mode": "demolab", **{k: v for k, v in res.items() if k != "markdown"}},
        )

    def _tool_sync_to_cloud(self, args: dict[str, Any], *, role: str = "PORTFOLIO_RW") -> dict[str, Any]:
        refresh_state = bool(args.get("refresh_state", False))
        max_articles = int(args.get("max_articles") if args.get("max_articles") is not None else 30)
        dry_run = bool(args.get("dry_run", False))
        sync_portfolio = bool(args.get("sync_portfolio", False))
        state_path = resolve_data_dir(self.base_dir) / "cockpit_state.json"

        if sync_portfolio and role != "PORTFOLIO_RW":
            return _tool_err("同步私密持仓需要 PORTFOLIO_RW 权限", code=403)

        try:
            from backend.mobile_push import load_push_env
            from backend.vps_sync import collect_payload, publish_via_http, sync_portfolio_via_http

            load_push_env(include_sync_token=True, include_rw_token=sync_portfolio)
            if refresh_state:
                from backend.signal_aggregator import generate_cockpit_state

                generate_cockpit_state(refresh_fred=False, output_path=state_path)
            if not state_path.is_file():
                return _tool_err(f"未找到本地状态快照: {state_path}", code=404)
            payload, stats = collect_payload(state_path, max_articles=max_articles)
            if dry_run:
                return _tool_ok(
                    f"云端同步演练完成：{stats.get('articles_count', 0)} 篇长文，载荷 {stats.get('state_bytes', 0)} 字节",
                    {"ok": True, "dry_run": True, **stats},
                )
            ok, info = publish_via_http(payload)
            if not ok:
                return _tool_err(f"云端同步失败: {info.get('reason', 'unknown')}", code=502, detail={"info": info, **stats})

            pf_info: dict[str, Any] = {}
            if sync_portfolio:
                pf_ok, pf_info = sync_portfolio_via_http(self.store.db_path)
                if not pf_ok:
                    return _tool_err(
                        f"公开快照已同步，但私密持仓同步失败: {pf_info.get('reason', 'unknown')}",
                        code=502,
                        detail={"info": info, "portfolio": pf_info, **stats},
                    )
            return _tool_ok(
                f"已同步至云端驾驶舱：{stats.get('articles_count', 0)} 篇长文"
                + (f"（持仓：{pf_info.get('portfolio_action', 'ok')}）" if sync_portfolio else ""),
                {"ok": True, "dry_run": False, "info": info, "portfolio": pf_info, **stats},
            )
        except Exception as exc:
            return _tool_err(f"云端同步异常: {type(exc).__name__}: {exc}", code=500)

    def dispatch_jsonrpc(
        self,
        payload: dict[str, Any],
        *,
        role: str,
        actor_id: str,
    ) -> tuple[int, dict[str, Any] | None]:
        """Dispatch one parsed JSON-RPC 2.0 request dict -> (http_status, response_dict_or_None)."""
        if not isinstance(payload, dict):
            return (400, _rpc_error(None, -32600, "Invalid Request"))
        if "id" not in payload:
            return (202, None)

        req_id = payload["id"]
        if req_id is not None and (
            isinstance(req_id, bool)
            or not isinstance(req_id, (str, int, float))
            or (isinstance(req_id, float) and not math.isfinite(req_id))
        ):
            return (400, _rpc_error(None, -32600, "Invalid Request id"))
        method = payload.get("method")
        if payload.get("jsonrpc") != "2.0" or not isinstance(method, str):
            return (400, _rpc_error(req_id, -32600, "Invalid Request"))

        params = payload.get("params")
        if method in ("initialize", "ping", "tools/list") and "params" in payload and not isinstance(params, dict):
            return (200, _rpc_error(req_id, -32602, "Invalid params: expected an object"))

        if method == "initialize":
            client_ver = str((params or {}).get("protocolVersion") or "").strip() if isinstance(params, dict) else ""
            negotiated = client_ver if client_ver in SUPPORTED_MCP_VERSIONS else MCP_PROTOCOL_VERSION
            return (
                200,
                _rpc_result(
                    req_id,
                    {
                        "protocolVersion": negotiated,
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": {"name": "macrocockpit-mcp", "version": "2.0.0"},
                        "instructions": "宏观决策情报站 MCP 服务：仅暴露与令牌角色匹配的只读或组合读写工具。",
                    },
                ),
            )

        if method == "ping":
            return (200, _rpc_result(req_id, {}))

        if method == "tools/list":
            return (200, _rpc_result(req_id, {"tools": self._visible_tools(role)}))

        if method == "tools/call":
            if not isinstance(params, dict) or not isinstance(params.get("name"), str):
                return (200, _rpc_error(req_id, -32602, "Invalid params: missing tool name"))
            t_name = str(params["name"]).strip()
            t_args = params.get("arguments", {})
            if not isinstance(t_args, dict):
                return (200, _rpc_error(req_id, -32602, "Invalid params: arguments must be an object"))
            result, rpc_err_code = self._call_tool(t_name, t_args, role=role, actor_id=actor_id)
            if rpc_err_code is not None:
                return (200, _rpc_error(req_id, rpc_err_code, f"Unknown tool: {t_name}"))
            return (200, _rpc_result(req_id, result))

        return (200, _rpc_error(req_id, -32601, f"Method not found: {method}"))


def create_mcp_dispatcher(
    *,
    store: PortfolioStore | None,
    base_dir: Path | str,
    public_url: str = "https://news.shuashuabao.xyz",
    desktop_pro: bool = False,
    include_desktop_tools: bool = False,
    desktop_handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] | None = None,
) -> McpDispatcher:
    """Create a configured McpDispatcher instance."""
    return McpDispatcher(
        store=store,
        base_dir=base_dir,
        public_url=public_url,
        desktop_pro=desktop_pro,
        include_desktop_tools=include_desktop_tools,
        desktop_handlers=desktop_handlers,
    )


def _resolve_bearer_auth(
    token: str,
    *,
    store: PortfolioStore | None,
    read_token: str = "",
    rw_token: str = "",
    sync_token: str = "",
) -> dict[str, Any] | None:
    cand = str(token or "").strip()
    if not cand:
        return None
    if store is not None:
        info = store.verify_token(cand)
        if info is not None:
            return info
    # Also support explicit in-memory server tokens & environment tokens when store is absent/test-configured
    candidates = [
        (rw_token or os.environ.get("MACROCOCKPIT_RW_TOKEN", ""), "PORTFOLIO_RW", "config:portfolio_rw"),
        (sync_token or os.environ.get("MACROCOCKPIT_SYNC_TOKEN", ""), "SYNC_PUBLISH", "config:sync_publish"),
        (read_token or os.environ.get("MACROCOCKPIT_READ_TOKEN", ""), "READ_ONLY", "config:read_only"),
    ]
    for expected, role, actor in candidates:
        exp_clean = str(expected or "").strip()
        if exp_clean and _const_eq(cand, exp_clean):
            return {"valid": True, "role": role, "actor_id": actor, "source": "config"}
    return None


def handle_mcp_http_request(
    headers: Any,
    body_bytes: bytes,
    *,
    store: PortfolioStore | None,
    base_dir: Path | str,
    public_url: str = "https://news.shuashuabao.xyz",
    read_token: str = "",
    rw_token: str = "",
    sync_token: str = "",
    desktop_pro: bool = False,
    include_desktop_tools: bool = False,
    desktop_handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    """Handle one HTTP POST /mcp request cleanly, enforcing Bearer auth, Origin, and protocol headers."""
    json_hdrs = {
        "Content-Type": "application/json; charset=utf-8",
        "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
    }

    origin = _get_header(headers, "Origin")
    host_hdr = _get_header(headers, "Host")
    if origin and not _origin_allowed(origin, host_header=host_hdr, public_url=public_url):
        return (403, json_hdrs, json.dumps({"error": "Origin not allowed"}, ensure_ascii=False).encode("utf-8"))

    auth_hdr = _get_header(headers, "Authorization")
    if not auth_hdr.startswith("Bearer "):
        return (401, json_hdrs, json.dumps({"error": "Authorization: Bearer token required"}, ensure_ascii=False).encode("utf-8"))
    token = auth_hdr[7:].strip()
    auth_info = _resolve_bearer_auth(
        token,
        store=store,
        read_token=read_token,
        rw_token=rw_token,
        sync_token=sync_token,
    )
    if auth_info is None:
        return (401, json_hdrs, json.dumps({"error": "Invalid or expired Bearer token"}, ensure_ascii=False).encode("utf-8"))

    role = str(auth_info.get("role") or "")
    actor_id = str(auth_info.get("actor_id") or "mcp:bearer")
    if role == "SYNC_PUBLISH" or role not in ("READ_ONLY", "PORTFOLIO_RW"):
        if store is not None:
            try:
                store.log_audit_event(
                    actor_id=actor_id,
                    role=role,
                    action="mcp:auth",
                    resource="/mcp",
                    outcome="denied",
                    detail={"reason": "sync_publish_forbidden_on_mcp"},
                )
            except Exception:
                pass
        return (403, json_hdrs, json.dumps({"error": "SYNC_PUBLISH role forbidden on MCP"}, ensure_ascii=False).encode("utf-8"))

    ctype = _get_header(headers, "Content-Type").split(";", 1)[0].strip().lower()
    if ctype != "application/json":
        return (415, json_hdrs, json.dumps({"error": "Content-Type must be application/json"}, ensure_ascii=False).encode("utf-8"))

    if len(body_bytes or b"") > 2_000_000:
        return (413, json_hdrs, json.dumps({"error": "MCP request body exceeds 2MB limit"}, ensure_ascii=False).encode("utf-8"))

    proto_ver = _get_header(headers, "MCP-Protocol-Version")
    if proto_ver and proto_ver not in SUPPORTED_MCP_VERSIONS:
        return (
            400,
            json_hdrs,
            json.dumps({"error": f"Unsupported MCP-Protocol-Version: {proto_ver}"}, ensure_ascii=False).encode("utf-8"),
        )

    try:
        payload = _load_rpc_json((body_bytes or b"").decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        err_body = _rpc_error(None, -32700, "Parse error")
        return (400, json_hdrs, json.dumps(err_body, ensure_ascii=False).encode("utf-8"))

    dispatcher = create_mcp_dispatcher(
        store=store,
        base_dir=base_dir,
        public_url=public_url,
        desktop_pro=desktop_pro,
        include_desktop_tools=include_desktop_tools,
        desktop_handlers=desktop_handlers,
    )
    status, resp_obj = dispatcher.dispatch_jsonrpc(payload, role=role, actor_id=actor_id)
    if status == 202 or resp_obj is None:
        return (202, {"MCP-Protocol-Version": MCP_PROTOCOL_VERSION}, b"")
    return (status, json_hdrs, json.dumps(resp_obj, ensure_ascii=False).encode("utf-8"))
