"""Mobile digest builder (Feishu Card 2.0, WeCom, WeChat) and multi-channel push dispatcher."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:
    requests = None  # type: ignore[assignment]

COCKPIT_ROOT = Path(os.environ.get("MACROCOCKPIT_ROOT") or Path(__file__).resolve().parent.parent)
DATA_DIR = Path(os.environ.get("MACROCOCKPIT_DATA_DIR") or (COCKPIT_ROOT / "data"))
STATE_PATH = DATA_DIR / "cockpit_state.json"

def _safe_home(fallback: Path) -> Path:
    try:
        return Path.home()
    except RuntimeError:
        return fallback


_HERMES_ROOT = Path(os.environ.get("HERMES_HOME") or (_safe_home(COCKPIT_ROOT) / "AppData" / "Local" / "hermes"))
HERMES_ENV_PATHS = (
    _HERMES_ROOT / ".env",
    _HERMES_ROOT / "scripts" / "news" / ".env",
    COCKPIT_ROOT / ".env",
)
FEISHU_DM_NOTIFY_PATH = _HERMES_ROOT / "scripts" / "feishu_dm_notify.py"
DEFAULT_ADMIN_OPEN_ID = ""
BLOCKED_GROUP_CHAT_PREFIX = "oc_"

CHANNEL_LABELS: dict[str, str] = {
    "feishu_dm": "飞书个人私信 Card 2.0",
    "wecom_bot": "企业微信 / 微信插件",
    "wechat_push": "个人微信直推",
    "ntfy": "ntfy 手机锁屏推送",
    "bark": "Bark iOS 推送",
    "telegram": "Telegram 推送",
    "webhook": "外部 Webhook",
}

MODE_LABELS: dict[str, tuple[str, str]] = {
    "daily": ("每日决策简报", "日报"),
    "headwind": ("持仓逆风与强平预警", "逆风预警"),
    "risk_alert": ("持仓逆风与强平预警", "风控预警"),
    "drift": ("持仓偏离巡检", "偏离巡检"),
    "rebalance": ("持仓偏离巡检", "调仓巡检"),
}


def load_push_env(force: bool = False, include_sync_token: bool = False) -> None:
    """Safely load push env vars from Hermes .env without leaking auth credentials."""
    blocked = {
        "MACROCOCKPIT_ACCESS_PASSWORD",
        "MACROCOCKPIT_READ_TOKEN",
        "MACROCOCKPIT_RW_TOKEN",
    }
    if not include_sync_token:
        blocked.add("MACROCOCKPIT_SYNC_TOKEN")

    for env_path in HERMES_ENV_PATHS:
        if not env_path.exists():
            continue
        try:
            lines = env_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for raw_line in lines:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if not k or k in blocked:
                continue
            if force or k not in os.environ or not os.environ[k].strip():
                os.environ[k] = v


def _env_nonempty(key: str) -> str:
    return (os.environ.get(key) or "").strip()


def get_push_channels_status() -> dict[str, dict[str, Any]]:
    """Return push channel configuration status without exposing secrets."""
    load_push_env()
    feishu_ok = bool(_env_nonempty("FEISHU_APP_ID") and _env_nonempty("FEISHU_APP_SECRET"))
    wecom_ok = bool(_env_nonempty("WECOM_WEBHOOK_URL"))
    wechat_ok = bool(
        _env_nonempty("SERVERCHAN_SENDKEY")
        or _env_nonempty("PUSHPLUS_TOKEN")
        or _env_nonempty("WXPUSHER_TOKEN")
    )
    ntfy_ok = bool(_env_nonempty("NTFY_TOPIC_URL"))
    bark_ok = bool(_env_nonempty("BARK_URL"))
    tg_ok = bool(_env_nonempty("TG_BOT_TOKEN") and _env_nonempty("TG_CHAT_ID"))
    webhook_ok = bool(_env_nonempty("AGENT_ALERT_WEBHOOK_URL"))

    status_map = {
        "feishu_dm": feishu_ok,
        "wecom_bot": wecom_ok,
        "wechat_push": wechat_ok,
        "ntfy": ntfy_ok,
        "bark": bark_ok,
        "telegram": tg_ok,
        "webhook": webhook_ok,
    }
    return {
        ch: {"configured": configured, "label": CHANNEL_LABELS[ch]}
        for ch, configured in status_map.items()
    }


def get_public_base_url() -> str:
    return (os.environ.get("MACROCOCKPIT_PUBLIC_URL") or "https://news.shuashuabao.xyz").strip().rstrip("/")


_HYPE_PREFIX_RE = re.compile(
    r"^(?:重磅|突发|刚刚|惊爆|震惊|紧急通知|太可怕了|大消息|速看|揭秘|炸裂|独家猛料|必看)\s*[!！：:\-—\s]*"
)
_RHETORICAL_TAIL_RE = re.compile(
    r"(?:[，,：:\s]*(?:你怎么看|是真的吗|真的吗|太吓人了|慌了吗|机会还是陷阱|你敢信吗))[？?!！]*$"
)


def clean_headline_clickbait(title: str) -> str:
    """Normalize clickbait prefixes and rhetorical questions into neutral declarative headlines."""
    s = re.sub(r"\s+", " ", str(title or "")).strip()
    if not s:
        return ""

    had_hype = False
    while True:
        nxt = _HYPE_PREFIX_RE.sub("", s, count=1).strip()
        if nxt == s or not nxt:
            break
        s = nxt
        had_hype = True

    nxt_tail = _RHETORICAL_TAIL_RE.sub("", s).strip()
    if nxt_tail and nxt_tail != s:
        s = nxt_tail
        had_hype = True

    s_before = s
    s = re.sub(r"(?:究竟|到底|难道|难道说)\s*", "", s)
    s = re.sub(r"(?:要崩盘了吗|又要崩盘了吗)[？?!！]*$", "承压风险", s)
    s = re.sub(r"(?:要变天了吗|要反转了吗)[？?!！]*$", "趋势分化预期", s)
    s = re.sub(r"(?:牛市来了吗)[？?!！]*$", "走强预期", s)
    s = re.sub(r"(?:又要暴雷吗|会暴雷吗)[？?!！]*$", "信用风险", s)
    s = re.sub(r"^(?:为什么|为何)\s*([^？?!！]{2,}?)[？?!！]+$", r"\1原因", s)
    s = re.sub(r"([，,：:\s])(?:为什么|为何)\s*([^？?!！]{2,}?)[？?!！]+$", r"\1\2原因", s)
    s = re.sub(r"(?:意味着什么|说明了什么|发生了什么|释放什么信号|释放了什么信号)[？?!！]*$", "影响解析", s)
    s = re.sub(
        r"(?:还能|真能|是否还能|能否|会不会|真的会|是否会|还会)\s*([^？?!！]{2,}?)(?:吗|呢)[？?!！]*$",
        r"\1预期",
        s,
    )
    s = re.sub(r"([^？?!！]{2,}?)(?:了吗|了吗\？|吗|呢)[？?!！]+$", r"\1进展", s)
    if s != s_before:
        had_hype = True

    if re.search(r"[！!？?]{2,}$", s):
        s = re.sub(r"[！!？?]+$", "", s).strip()
    elif had_hype:
        s = re.sub(r"[！!]+$", "", s).strip()

    s = re.sub(r"^[，,：:\s]+|[，,：:\s]+$", "", s)
    return s or str(title or "").strip()


def build_muse_markdown_digest(state: dict[str, Any], topic: str = "all", public_url: str | None = None) -> str:
    """Build sanitized topic-filtered Markdown digest for reading agents."""
    if topic not in {"all", "finance", "ai", "crypto"}:
        raise ValueError("topic must be all, finance, ai, or crypto")
    base = (public_url or get_public_base_url()).rstrip("/")
    risk = state.get("risk_gate") or {}
    lines = [
        "# MacroCockpit 市场简报",
        "",
        f"更新时间：{state.get('generated_at_bjt') or '—'}",
        f"## 风险门控：{risk.get('label') or '—'}",
        str(risk.get("summary_exposure") or ""),
    ]
    if risk.get("reasons"):
        lines.extend(["", "依据：" + "；".join(str(x) for x in risk["reasons"][:3])])
    positions = state.get("positions") or []
    lines.extend(["", "## 辅助仓位"])
    lines.extend(
        [f"- {p.get('name', '')}：{p.get('state', '')}，建议 {p.get('recommended_pct', 0)}%" for p in positions[:8]]
        or ["- 当前无仓位记录"]
    )
    items = state.get("macro_fred") or []
    if topic in ("all", "finance", "crypto"):
        lines.extend(["", "## 宏观快照"])
        lines.extend(
            [f"- {m.get('label') or m.get('id')}: {m.get('value_display')}（{m.get('as_of', '')}）" for m in items[:12]]
        )
    signals = [s for s in (state.get("signals") or []) if s.get("selected") or s.get("importance") == "高"]
    if topic == "ai":
        signals = [
            s
            for s in signals
            if str(s.get("category") or "") == "ai"
            or any(
                k in (str(s.get("title", "")) + " " + str(s.get("summary", ""))).lower()
                for k in ("ai", "人工智能", "芯片", "算力", "nvda", "openai", "anthropic")
            )
        ]
    elif topic == "crypto":
        signals = [
            s
            for s in signals
            if str(s.get("category") or "") == "crypto"
            or any(
                k in (str(s.get("title", "")) + " " + str(s.get("summary", ""))).lower()
                for k in ("btc", "eth", "比特币", "以太坊", "加密", "crypto")
            )
        ]
    elif topic == "finance":
        signals = [
            s
            for s in signals
            if str(s.get("category") or "") in ("macro", "equities", "crypto")
            or any(
                k in (str(s.get("title", "")) + " " + str(s.get("summary", ""))).lower()
                for k in ("美股", "美联储", "美债", "利率", "btc", "黄金", "spx", "nasdaq")
            )
        ]
    if topic in ("all", "finance", "ai", "crypto"):
        lines.extend(["", "## 重点信号"])
        for s in signals[:8]:
            title = clean_headline_clickbait(str(s.get("title") or "市场信号"))
            url = str(s.get("url") or "")
            lines.append(f"- [{title}]({url})" if url.startswith(("https://", "http://")) else f"- {title}")
    if topic in ("all", "ai", "finance"):
        lines.extend(["", "## KOL 多空观点"])
        for k in (state.get("kol_digest") or [])[:8]:
            stance = str(k.get("stance") or "")
            lines.append(
                f"- {k.get('handle') or k.get('source') or '观点'}（{stance}）：{k.get('headline') or k.get('takeaway') or ''}"
            )
    lines.extend(["", f"[打开线上完整情报站]({base})"])
    return "\n".join(lines).strip() + "\n"


def _light_to_feishu_template(light: str) -> str:
    return {"GREEN": "green", "YELLOW": "orange", "RED": "red"}.get(light.upper(), "blue")


def _select_card_palette(light: str, mode: str) -> dict[str, str]:
    """Choose P6-compliant adjacent colors anchored to header.template."""
    mode_norm = (mode or "daily").strip().lower()
    if mode_norm in ("headwind", "risk_alert") or light == "RED":
        return {
            "template": "red",
            "accent": "red",
            "bg": "red-50",
            "border": "red-100",
            "alt_accent": "orange",
            "alt_bg": "orange-50",
            "alt_border": "orange-100",
            "tag_color": "red",
            "icon_token": "warning_outlined",
            "icon_color": "red",
        }
    if mode_norm in ("drift", "rebalance") or light == "YELLOW":
        return {
            "template": "orange",
            "accent": "orange",
            "bg": "orange-50",
            "border": "orange-100",
            "alt_accent": "yellow",
            "alt_bg": "yellow-50",
            "alt_border": "yellow-100",
            "tag_color": "orange",
            "icon_token": "bell_outlined",
            "icon_color": "orange",
        }
    if light == "GREEN":
        return {
            "template": "green",
            "accent": "green",
            "bg": "green-50",
            "border": "green-100",
            "alt_accent": "turquoise",
            "alt_bg": "turquoise-50",
            "alt_border": "turquoise-100",
            "tag_color": "green",
            "icon_token": "done_outlined",
            "icon_color": "green",
        }
    return {
        "template": "blue",
        "accent": "blue",
        "bg": "blue-50",
        "border": "blue-100",
        "alt_accent": "violet",
        "alt_bg": "violet-50",
        "alt_border": "violet-100",
        "tag_color": "blue",
        "icon_token": "info_outlined",
        "icon_color": "blue",
    }


def _to_float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_relative_portfolio_metrics(
    state: dict[str, Any],
    workspace: dict[str, Any] | None,
    *,
    include_amounts: bool,
) -> dict[str, Any]:
    """Compute strictly masked %, x, pp metrics unless include_amounts=True is explicitly requested."""
    ws = workspace if isinstance(workspace, dict) else {}
    summary = ws.get("summary") or ws.get("valuation") or ws.get("metrics") or ws
    positions = [p for p in (state.get("positions") or []) if isinstance(p, dict)]

    net_exp = _to_float_or_none(summary.get("net_direction_pct") if isinstance(summary, dict) else None)
    if isinstance(summary, dict) and "net_direction_pct" not in summary:
        net_exp = _to_float_or_none(summary.get("net_exposure_pct"))

    eff_lev = _to_float_or_none(summary.get("effective_leverage") if isinstance(summary, dict) else None)

    derivs = ws.get("derivative_positions") or ws.get("derivatives") or []
    liq_dists: list[float] = []
    has_unestimated_cross = False
    for d in derivs:
        if not isinstance(d, dict):
            continue
        dist = _to_float_or_none(d.get("distance_to_liq_pct") if "distance_to_liq_pct" in d else d.get("liq_distance_pct"))
        if dist is not None:
            liq_dists.append(dist)
        else:
            has_unestimated_cross = True

    min_liq = _to_float_or_none(summary.get("min_liq_distance_pct") if isinstance(summary, dict) else None)
    if min_liq is None and liq_dists:
        min_liq = min(liq_dists)

    # Strategy deviation rows
    strat_rows = ws.get("strategy_comparison") or ws.get("strategy_targets") or []
    if isinstance(strat_rows, dict):
        strat_rows = strat_rows.get("targets") or strat_rows.get("items") or []
    drift_lines: list[str] = []
    pos_lines: list[str] = []

    if isinstance(strat_rows, list) and strat_rows:
        for row in strat_rows:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or row.get("label") or row.get("id") or "")
            act = _to_float_or_none(row.get("actual_pct"))
            tgt = _to_float_or_none(row.get("target_pct") if "target_pct" in row else row.get("recommended_pct"))
            delta = _to_float_or_none(row.get("delta_pp") if "delta_pp" in row else row.get("deviation_pp"))
            if act is not None and tgt is not None:
                d_val = delta if delta is not None else round(act - tgt, 1)
                drift_lines.append(f"• {name}：实际 {act:.1f}% vs 目标 {tgt:.1f}%（{d_val:+.1f}pp）")
            elif tgt is not None:
                drift_lines.append(f"• {name}：目标 {tgt:.1f}%")

    for p in positions:
        name = str(p.get("name") or "")
        st_lbl = str(p.get("state") or "")
        rec = float(p.get("recommended_pct") or 0.0)
        notional = float(p.get("est_notional_usd") or 0.0)
        if include_amounts and rec > 0:
            pos_lines.append(f"• {name}：{rec:.0f}% (${notional:,.0f}) · {st_lbl}")
        else:
            pos_lines.append(f"• {name}：{rec:.0f}% · {st_lbl}")

    # Fallback net exposure / leverage from strategy state when workspace is absent
    if not ws:
        net_sum = 0.0
        gross_sum = 0.0
        for p in positions:
            rec = float(p.get("recommended_pct") or 0.0)
            if p.get("id") == "bear_basket" and rec > 0:
                net_sum += rec - 30.0
                gross_sum += rec + 30.0
            else:
                net_sum += rec
                gross_sum += abs(rec)
        net_exp = round(net_sum, 1)
        if eff_lev is None:
            eff_lev = round(gross_sum / 100.0, 2)

    if not ws and eff_lev is None:
        risk = state.get("risk_gate") or {}
        eff_lev = float(risk.get("long_risk_cap") if risk.get("long_risk_cap") is not None else 0.0)

    if min_liq is not None:
        liq_display = f"{min_liq:.1f}%"
    elif has_unestimated_cross:
        liq_display = "无法估算"
    else:
        liq_display = "无强平敞口"

    equity_note = ""
    if include_amounts and isinstance(summary, dict):
        eq_usd = _to_float_or_none(summary.get("equity_usd") if "equity_usd" in summary else summary.get("net_equity_usd"))
        if eq_usd is not None:
            equity_note = f"参考净值 ${eq_usd:,.0f}"

    return {
        "net_exp": net_exp,
        "net_exp_display": f"{net_exp:+.1f}%" if net_exp is not None else "不可计算",
        "eff_lev": eff_lev,
        "lev_display": f"{eff_lev:.2f}x" if eff_lev is not None else "不可计算",
        "min_liq": min_liq,
        "liq_display": liq_display,
        "pos_lines": pos_lines or ["• 当前无仓位配置记录"],
        "drift_lines": drift_lines or pos_lines or ["• 暂无持仓偏离项"],
        "equity_note": equity_note,
    }


def _is_headwind_radar(radar: Any) -> bool:
    """Detect headwind relation across both dict (V2 curation) and str (legacy) holding_radar values."""
    if isinstance(radar, dict):
        rel = str(radar.get("relation") or radar.get("status") or "").strip()
        if rel == "逆风" or "逆风" in rel:
            return True
        return "逆风" in str(radar.get("detail") or "")
    return "逆风" in str(radar or "")


def _resolve_signal_pool_for_alerts(state: dict[str, Any], workspace: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Resolve curated/holding-radar signal pool from workspace, computing on demand if absent."""
    ws_sigs: list[dict[str, Any]] = []
    if isinstance(workspace, dict):
        curated = workspace.get("curated_signals")
        legacy_ws_sigs = workspace.get("signals")
        if isinstance(curated, list) and curated:
            ws_sigs = [s for s in curated if isinstance(s, dict)]
        elif isinstance(legacy_ws_sigs, list) and any(isinstance(s, dict) and s.get("holding_radar") for s in legacy_ws_sigs):
            ws_sigs = [s for s in legacy_ws_sigs if isinstance(s, dict)]
        else:
            raw_pool = list(state.get("signal_candidates") or state.get("signals") or [])
            if raw_pool:
                try:
                    from backend.curation import rank_curated_signals

                    ws_sigs = rank_curated_signals(
                        raw_pool,
                        rules=workspace.get("curation_rules") if isinstance(workspace.get("curation_rules"), list) else None,
                        workspace=workspace,
                    )
                except Exception:
                    ws_sigs = [s for s in (legacy_ws_sigs or []) if isinstance(s, dict)]
            elif isinstance(legacy_ws_sigs, list):
                ws_sigs = [s for s in legacy_ws_sigs if isinstance(s, dict)]

    combined: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for s in ws_sigs + [x for x in (state.get("signals") or []) if isinstance(x, dict)]:
        sid = str(s.get("id") or s.get("title") or "")
        if sid and sid in seen_keys:
            continue
        if sid:
            seen_keys.add(sid)
        combined.append(s)
    return combined


def _collect_headwind_lines(state: dict[str, Any], workspace: dict[str, Any] | None) -> list[str]:
    pool = _resolve_signal_pool_for_alerts(state, workspace)
    lines: list[str] = []
    for s in pool:
        if _is_headwind_radar(s.get("holding_radar")):
            src = str(s.get("source") or "")
            ttl = clean_headline_clickbait(str(s.get("title") or ""))[:48]
            lines.append(f"• [逆风] [{src}] {ttl}")
        if len(lines) >= 3:
            break
    return lines


def build_mobile_digest_payload(
    state: dict[str, Any],
    *,
    workspace: dict[str, Any] | None = None,
    mode: str = "daily",
    include_amounts: bool = False,
    schedule_preset: str = "morning_evening_alert",
) -> dict[str, Any]:
    """Build masked mobile digest text, Feishu Card 1.0 + Card 2.0, WeCom card, and WeChat Markdown."""
    mode_key = (mode or "daily").strip().lower()
    if mode_key not in MODE_LABELS:
        mode_key = "daily"
    mode_label, mode_short = MODE_LABELS[mode_key]

    risk = state.get("risk_gate") or {}
    light = str(risk.get("light") or "YELLOW").upper()
    label = str(risk.get("label") or "风险门控")
    cap = float(risk.get("long_risk_cap") if risk.get("long_risk_cap") is not None else 1.0)
    summary_exp = str(risk.get("summary_exposure") or "")
    reasons = [str(r) for r in (risk.get("reasons") or []) if r][:3]

    metrics = _extract_relative_portfolio_metrics(state, workspace, include_amounts=include_amounts)
    headwind_lines = _collect_headwind_lines(state, workspace)
    drift_or_pos_lines = metrics["drift_lines"] if (workspace and mode_key in ("drift", "rebalance")) else metrics["pos_lines"]

    macro_map = {str(m.get("id")): m for m in (state.get("macro_fred") or []) if m.get("id")}
    kpi_order = [
        ("fedfunds", "联邦利率"),
        ("us_10y", "美债10Y"),
        ("fed_net_liquidity", "净流动性"),
        ("vix", "VIX"),
        ("btc_spot", "BTC"),
        ("otc_usdt_premium", "U溢价"),
    ]
    kpi_parts: list[str] = []
    for k_id, short_lbl in kpi_order:
        m = macro_map.get(k_id)
        if m and m.get("value_display"):
            kpi_parts.append(f"{short_lbl} {m['value_display']}")

    eq = state.get("us_equities") or {}
    eq_summary = str(eq.get("summary") or "").strip()

    all_signals = [s for s in (state.get("signals") or []) if isinstance(s, dict)]
    top_signals = [
        s for s in all_signals if s.get("importance") == "高" or int(s.get("merged_count") or 1) > 1
    ][:3] or all_signals[:3]

    sig_lines: list[str] = []
    sig_md_lines: list[str] = []
    for idx, s in enumerate(top_signals, 1):
        src = str(s.get("source") or "")
        ttl = clean_headline_clickbait(str(s.get("title") or ""))[:64]
        merged = int(s.get("merged_count") or 1)
        badge = f" · 多源×{merged}" if merged > 1 else ""
        url = str(s.get("url") or "")
        sig_lines.append(f"{idx}. [{src}{badge}] {ttl}")
        if url.startswith(("https://", "http://")):
            sig_md_lines.append(f"{idx}. [{src}{badge}] [{ttl}]({url})")
        else:
            sig_md_lines.append(f"{idx}. [{src}{badge}] {ttl}")

    gen_bjt = str(state.get("generated_at_bjt") or "")
    base_url = get_public_base_url()
    today_url = f"{base_url}/#today"
    portfolio_url = f"{base_url}/#portfolio"

    title = f"宏观驾驶舱 · {mode_short} | {label} (多头上限 {cap:.1f}x)"

    # Plain-text digest (keeps 【辅助仓位】 header for contract compatibility; masked unless include_amounts=True)
    text_lines: list[str] = [
        f"更新：{gen_bjt}（{mode_label}）",
        f"组合风险：净敞口 {metrics['net_exp_display']} · 有效杠杆 {metrics['lev_display']} · 强平安全垫 {metrics['liq_display']}",
        f"敞口门控：{summary_exp}" if summary_exp else "",
        ("依据：" + "；".join(reasons)) if reasons else "",
    ]
    if metrics["equity_note"]:
        text_lines.append(metrics["equity_note"])
    text_lines.extend(["", "【辅助仓位】", *metrics["pos_lines"]])
    if workspace and metrics["drift_lines"]:
        text_lines.extend(["", "【策略偏离】", *metrics["drift_lines"][:4]])
    if headwind_lines:
        text_lines.extend(["", "【持仓逆风提醒】", *headwind_lines])
    if kpi_parts:
        text_lines.extend(["", "【核心温度】", " | ".join(kpi_parts)])
    if eq_summary:
        text_lines.extend(["", f"【美股】{eq_summary}"])
    if sig_lines:
        text_lines.extend(["", "【高优快讯】", *sig_lines])

    digest_text = "\n".join(line for line in text_lines if line is not None).strip() + f"\n\n线上看板：{today_url}"

    # Card 1.0 (kept in feishu_card for backward compatibility & older client fallback)
    card_v1_elements: list[dict[str, Any]] = [
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": (
                    f"**风险灯号**：{label}（方向性多头上限 `{cap:.1f}x`）\n"
                    f"**净方向敞口**：{metrics['net_exp_display']} · **有效杠杆**：{metrics['lev_display']} · **安全垫**：{metrics['liq_display']}\n"
                    + (f"**门控依据**：{'；'.join(reasons)}" if reasons else "")
                ),
            },
        },
        {"tag": "hr"},
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": "**辅助仓位与策略偏离**\n" + "\n".join(drift_or_pos_lines[:6]),
            },
        },
    ]
    if headwind_lines:
        card_v1_elements.append(
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": "**持仓逆风预警**\n" + "\n".join(headwind_lines)},
            }
        )
    if kpi_parts:
        card_v1_elements.append(
            {"tag": "div", "text": {"tag": "lark_md", "content": "**核心指标**：" + " · ".join(kpi_parts)}}
        )
    if sig_lines:
        card_v1_elements.append({"tag": "hr"})
        card_v1_elements.append(
            {"tag": "div", "text": {"tag": "lark_md", "content": "**高优快讯**\n" + "\n".join(sig_lines)}}
        )
    card_v1_elements.append(
        {
            "tag": "actions",
            "actions": [
                {"tag": "button", "text": {"tag": "plain_text", "content": "打开今日看板"}, "type": "primary", "url": today_url},
                {"tag": "button", "text": {"tag": "plain_text", "content": "核对持仓风控"}, "type": "default", "url": portfolio_url},
            ],
        }
    )
    card_v1_elements.append(
        {
            "tag": "note",
            "elements": [{"tag": "plain_text", "content": f"MacroCockpit · {gen_bjt} · 仅个人私信推送"}],
        }
    )
    feishu_card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": _light_to_feishu_template(light),
        },
        "elements": card_v1_elements,
    }

    # Card 2.0 (schema 2.0, P0-P7 compliant: single KPI focus, 4 semantic containers, zero hr sprawl)
    palette = _select_card_palette(light, mode_key)
    block2_lines = []
    if headwind_lines:
        block2_lines.append("**逆风监测**：" + " / ".join(x.lstrip("• ").strip() for x in headwind_lines[:2]))
    else:
        block2_lines.append("**逆风监测**：当前持仓未触发高危逆风")
    block2_lines.extend(drift_or_pos_lines[:4])
    if reasons:
        block2_lines.append(f"<font color='grey'>门控依据：{'；'.join(reasons[:2])}</font>")

    block3_lines = list(sig_md_lines)
    if kpi_parts:
        block3_lines.append(f"<font color='grey'>宏观温度：{' · '.join(kpi_parts[:4])}</font>")
    if not block3_lines:
        block3_lines.append("<font color='grey'>暂无新入池高优快讯</font>")

    privacy_tag = "含金额" if include_amounts else "脱敏"
    feishu_card_v2: dict[str, Any] = {
        "schema": "2.0",
        "config": {
            "update_multi": True,
            "width_mode": "default",
            "summary": {"content": f"{mode_short} · 净敞口 {metrics['net_exp_display']} · 杠杆 {metrics['lev_display']}"},
            "style": {
                "text_size": {
                    "title": {"default": "heading-2", "pc": "heading-2", "mobile": "heading-3"},
                    "body": {"default": "normal", "pc": "normal", "mobile": "normal"},
                    "caption": {"default": "notation", "pc": "notation", "mobile": "notation"},
                }
            },
        },
        "header": {
            "title": {"tag": "plain_text", "content": f"宏观驾驶舱 · {mode_label}"},
            "subtitle": {"tag": "plain_text", "content": f"{gen_bjt} · {label}"},
            "template": palette["template"],
            "icon": {"tag": "standard_icon", "token": palette["icon_token"], "color": palette["icon_color"]},
            "text_tag_list": [
                {"tag": "text_tag", "text": {"tag": "plain_text", "content": mode_short}, "color": palette["tag_color"]},
                {"tag": "text_tag", "text": {"tag": "plain_text", "content": privacy_tag}, "color": "neutral"},
            ],
        },
        "body": {
            "direction": "vertical",
            "padding": "12px 12px 20px 12px",
            "vertical_spacing": "4px",
            "elements": [
                {
                    "tag": "column_set",
                    "flex_mode": "none",
                    "horizontal_spacing": "12px",
                    "margin": "0px 0px 12px 0px",
                    "columns": [
                        {
                            "tag": "column",
                            "width": "weighted",
                            "weight": 1,
                            "background_style": "grey-50",
                            "corner_radius": "8px",
                            "padding": "12px",
                            "vertical_spacing": "2px",
                            "elements": [
                                {
                                    "tag": "markdown",
                                    "content": f"## <font color='{palette['accent']}'>{metrics['net_exp_display']}</font>",
                                    "text_align": "center",
                                },
                                {
                                    "tag": "markdown",
                                    "content": "<font color='grey'>净方向性敞口</font>",
                                    "text_align": "center",
                                    "text_size": "notation",
                                },
                            ],
                        },
                        {
                            "tag": "column",
                            "width": "weighted",
                            "weight": 1,
                            "background_style": "grey-50",
                            "corner_radius": "8px",
                            "padding": "12px",
                            "vertical_spacing": "2px",
                            "elements": [
                                {
                                    "tag": "markdown",
                                    "content": f"**{metrics['lev_display']}**",
                                    "text_align": "center",
                                },
                                {
                                    "tag": "markdown",
                                    "content": "<font color='grey'>有效杠杆</font>",
                                    "text_align": "center",
                                    "text_size": "notation",
                                },
                            ],
                        },
                        {
                            "tag": "column",
                            "width": "weighted",
                            "weight": 1,
                            "background_style": "grey-50",
                            "corner_radius": "8px",
                            "padding": "12px",
                            "vertical_spacing": "2px",
                            "elements": [
                                {
                                    "tag": "markdown",
                                    "content": f"**{metrics['liq_display']}**",
                                    "text_align": "center",
                                },
                                {
                                    "tag": "markdown",
                                    "content": "<font color='grey'>强平安全垫</font>",
                                    "text_align": "center",
                                    "text_size": "notation",
                                },
                            ],
                        },
                    ],
                },
                {
                    "tag": "interactive_container",
                    "width": "fill",
                    "has_border": True,
                    "border_color": palette["border"],
                    "background_style": palette["bg"],
                    "corner_radius": "8px",
                    "padding": "12px 12px 12px 12px",
                    "vertical_spacing": "4px",
                    "margin": "0px 0px 12px 0px",
                    "elements": [
                        {
                            "tag": "markdown",
                            "content": f"**<font color='{palette['accent']}'>持仓逆风与策略偏离</font>**",
                        },
                        {
                            "tag": "markdown",
                            "content": "\n".join(block2_lines),
                            "text_size": "normal",
                        },
                    ],
                },
                {
                    "tag": "interactive_container",
                    "width": "fill",
                    "has_border": True,
                    "border_color": palette["alt_border"],
                    "background_style": palette["alt_bg"],
                    "corner_radius": "8px",
                    "padding": "12px 12px 12px 12px",
                    "vertical_spacing": "4px",
                    "margin": "0px 0px 12px 0px",
                    "elements": [
                        {
                            "tag": "markdown",
                            "content": f"**<font color='{palette['alt_accent']}'>高分多源快讯与宏观温度</font>**",
                        },
                        {
                            "tag": "markdown",
                            "content": "\n".join(block3_lines),
                            "text_size": "normal",
                        },
                    ],
                },
                {
                    "tag": "column_set",
                    "flex_mode": "bisect",
                    "horizontal_spacing": "8px",
                    "margin": "0px",
                    "columns": [
                        {
                            "tag": "column",
                            "width": "weighted",
                            "weight": 1,
                            "elements": [
                                {
                                    "tag": "button",
                                    "text": {"tag": "plain_text", "content": "查看今日决策"},
                                    "type": "primary_filled",
                                    "width": "fill",
                                    "behaviors": [{"type": "open_url", "default_url": today_url}],
                                }
                            ],
                        },
                        {
                            "tag": "column",
                            "width": "weighted",
                            "weight": 1,
                            "elements": [
                                {
                                    "tag": "button",
                                    "text": {"tag": "plain_text", "content": "核对持仓风控"},
                                    "type": "default",
                                    "width": "fill",
                                    "behaviors": [{"type": "open_url", "default_url": portfolio_url}],
                                }
                            ],
                        },
                    ],
                },
            ],
        },
    }

    # WeCom template_card payload
    wecom_horiz: list[dict[str, Any]] = [
        {"keyname": "风险门控", "value": f"{label} ({cap:.1f}x)"},
        {"keyname": "有效杠杆", "value": metrics["lev_display"]},
        {"keyname": "强平安全垫", "value": metrics["liq_display"]},
    ]
    if kpi_parts:
        wecom_horiz.append({"keyname": "宏观核心", "value": " · ".join(kpi_parts[:2])[:40]})
    wecom_card = {
        "card_type": "text_notice",
        "source": {"desc": f"MacroCockpit · {mode_short}", "desc_color": 0},
        "main_title": {"title": f"宏观驾驶舱 · {mode_label}", "desc": gen_bjt},
        "emphasis_content": {"title": metrics["net_exp_display"], "desc": "净方向性敞口"},
        "quote_area": {
            "type": 1,
            "url": today_url,
            "title": "持仓逆风与策略偏离",
            "quote_text": "\n".join(headwind_lines[:2] or drift_or_pos_lines[:3]),
        },
        "sub_title_text": (sig_lines[0] if sig_lines else (summary_exp or "点击直达线上看板")),
        "horizontal_content_list": wecom_horiz[:6],
        "jump_list": [
            {"type": 1, "url": today_url, "title": "今日决策"},
            {"type": 1, "url": portfolio_url, "title": "持仓风控"},
        ],
        "card_action": {"type": 1, "url": today_url},
    }

    # Personal WeChat Markdown
    wechat_parts = [
        f"### 宏观驾驶舱 · {mode_label}",
        f"> {gen_bjt} · 门控：**{label}**（多头上限 `{cap:.1f}x`）",
        "",
        f"- **净方向性敞口**：`{metrics['net_exp_display']}`",
        f"- **有效杠杆**：`{metrics['lev_display']}`",
        f"- **强平安全垫**：`{metrics['liq_display']}`",
    ]
    if metrics["equity_note"]:
        wechat_parts.append(f"- **净值参考**：{metrics['equity_note']}")
    wechat_parts.extend(["", "**持仓逆风与策略偏离**", *drift_or_pos_lines[:5]])
    if headwind_lines:
        wechat_parts.extend(["", "**逆风预警**", *headwind_lines])
    if sig_md_lines:
        wechat_parts.extend(["", "**高分多源快讯**", *sig_md_lines])
    if kpi_parts:
        wechat_parts.extend(["", f"**宏观温度**：{' · '.join(kpi_parts[:5])}"])
    wechat_parts.extend(["", f"[打开今日看板]({today_url}) | [持仓风控]({portfolio_url})"])
    wechat_markdown = "\n".join(wechat_parts).strip() + "\n"

    return {
        "title": title,
        "mode": mode_key,
        "include_amounts": bool(include_amounts),
        "schedule_preset": (schedule_preset or "morning_evening_alert").strip() or "morning_evening_alert",
        "quiet_window_bjt": "23:30-07:30",
        "text": digest_text,
        "feishu_card": feishu_card,
        "feishu_card_v2": feishu_card_v2,
        "wecom_card": wecom_card,
        "wechat_markdown": wechat_markdown,
        "generated_at_bjt": gen_bjt,
        "target_policy": "feishu_personal_dm_only",
        "channels": get_push_channels_status(),
    }


def _normalize_seen_keys(seen_keys: set[Any] | list[Any] | tuple[Any, ...] | None) -> set[tuple[str, str, int]]:
    normalized: set[tuple[str, str, int]] = set()
    for item in seen_keys or ():
        if isinstance(item, tuple) and len(item) == 3:
            normalized.add((str(item[0]), str(item[1]), int(item[2])))
        elif isinstance(item, str) and "|" in item:
            parts = item.split("|", 2)
            if len(parts) == 3:
                try:
                    normalized.add((parts[0], parts[1], int(parts[2])))
                except ValueError:
                    pass
    return normalized


def evaluate_alert_triggers(
    state: dict[str, Any],
    workspace: dict[str, Any] | None = None,
    *,
    seen_keys: set[Any] | list[Any] | tuple[Any, ...] | None = None,
) -> list[dict[str, Any]]:
    """Evaluate masked headwind & liquidation alerts deduplicated by (rule_id, event_id, portfolio_revision)."""
    ws = workspace if isinstance(workspace, dict) else {}
    rev = int(ws.get("revision") or ws.get("portfolio_revision") or 0)
    date_tag = str(state.get("generated_at_bjt") or "")[:10] or "today"
    seen = _normalize_seen_keys(seen_keys)
    alerts: list[dict[str, Any]] = []

    def _emit(rule_id: str, event_id: str, severity: str, title: str, summary: str) -> None:
        tup = (str(rule_id), str(event_id), rev)
        if tup in seen:
            return
        seen.add(tup)
        alerts.append(
            {
                "rule_id": tup[0],
                "event_id": tup[1],
                "portfolio_revision": rev,
                "dedup_tuple": tup,
                "dedup_key": f"{tup[0]}|{tup[1]}|{rev}",
                "severity": severity,
                "title": title,
                "summary": summary,
            }
        )

    # 1. Derivative liquidation proximity
    for deriv in ws.get("derivative_positions") or ws.get("derivatives") or []:
        if not isinstance(deriv, dict):
            continue
        dist = _to_float_or_none(deriv.get("distance_to_liq_pct") if "distance_to_liq_pct" in deriv else deriv.get("liq_distance_pct"))
        if dist is None:
            continue
        inst = str(deriv.get("symbol") or deriv.get("instrument_id") or "合约")
        side = str(deriv.get("side") or "")
        account_id = str(deriv.get("account_id") or "")
        margin_mode = str(deriv.get("margin_mode") or "")
        event_id = deriv.get("id") or f"{account_id}:{inst}:{side}:{margin_mode}:{date_tag}"
        if dist <= 8.0:
            _emit(
                "liq_distance_critical",
                event_id,
                "high",
                f"{inst} 强平安全垫仅 {dist:.1f}%",
                f"{inst}（{side}）距预估强平线仅剩 {dist:.1f}%，请立即核查保证金缓冲。",
            )
        elif dist <= 15.0:
            _emit(
                "liq_distance_warning",
                event_id,
                "medium",
                f"{inst} 强平安全垫收窄至 {dist:.1f}%",
                f"{inst}（{side}）距预估强平线 {dist:.1f}%，留意波动风险。",
            )

    # 2. Holding headwind signals (supports curated_signals dict radar + legacy str radar)
    signal_pool = _resolve_signal_pool_for_alerts(state, ws if isinstance(workspace, dict) else None)
    for sig in signal_pool:
        if not _is_headwind_radar(sig.get("holding_radar")):
            continue
        score = _to_float_or_none(sig.get("final_score") if "final_score" in sig else sig.get("attention_score"))
        if not (
            sig.get("importance") == "高"
            or sig.get("selected")
            or sig.get("personal_high")
            or (score is not None and score >= 70)
        ):
            continue
        ttl = clean_headline_clickbait(str(sig.get("title") or "持仓逆风信号"))
        evt_id = str(sig.get("event_id") or sig.get("id") or hashlib.sha1(ttl.encode("utf-8")).hexdigest()[:12])
        _emit(
            "holding_headwind",
            evt_id,
            "high" if (score is not None and score >= 82) else "medium",
            f"持仓逆风：{ttl[:42]}",
            f"[{sig.get('source') or '快讯'}] {ttl}",
        )

    # 3. Red macro gate
    risk = state.get("risk_gate") or {}
    if str(risk.get("light") or "").upper() == "RED":
        _emit(
            "risk_gate_red",
            date_tag,
            "high",
            f"宏观风控亮红灯：{risk.get('label') or '严格控仓'}",
            "；".join(str(x) for x in (risk.get("reasons") or [])[:3]) or "系统宏观风险门控收紧。",
        )

    # 4. Material strategy deviation (>= 10pp)
    strat_rows = ws.get("strategy_comparison") or []
    if isinstance(strat_rows, list):
        for row in strat_rows:
            if not isinstance(row, dict):
                continue
            delta = _to_float_or_none(row.get("delta_pp") if "delta_pp" in row else row.get("deviation_pp"))
            if delta is not None and abs(delta) >= 10.0:
                tid = str(row.get("id") or row.get("name") or "strategy")
                _emit(
                    "strategy_drift_alert",
                    f"{tid}:{date_tag}",
                    "medium",
                    f"{row.get('name') or tid} 偏离目标 {delta:+.1f}pp",
                    f"当前仓位偏离策略目标 {delta:+.1f} 个百分点，请评估再平衡。",
                )

    return alerts


def _load_feishu_dm_module():
    if not FEISHU_DM_NOTIFY_PATH.exists():
        raise FileNotFoundError(f"Feishu helper not found: {FEISHU_DM_NOTIFY_PATH}")
    spec = importlib.util.spec_from_file_location("feishu_dm_notify", str(FEISHU_DM_NOTIFY_PATH))
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load feishu_dm_notify module")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _resolve_personal_feishu_open_id(explicit_open_id: str | None = None) -> str:
    oid = (explicit_open_id or _env_nonempty("FEISHU_ADMIN_OPEN_ID") or DEFAULT_ADMIN_OPEN_ID).strip()
    if not oid.startswith("ou_") or oid.startswith(BLOCKED_GROUP_CHAT_PREFIX):
        raise ValueError("Refusing non-personal Feishu target: personal open_id (ou_*) required; group chats are forbidden.")
    return oid


def _try_lark_cli_send(card_v2: dict[str, Any], target_oid: str) -> tuple[bool, str]:
    cli_bin = shutil.which("lark-cli")
    if not cli_bin:
        return False, "lark_cli_not_found"
    cmd = [
        cli_bin,
        "im",
        "+messages-send",
        "--as",
        "bot",
        "--user-id",
        target_oid,
        "--msg-type",
        "interactive",
        "--content",
        json.dumps(card_v2, ensure_ascii=False),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
    except Exception as exc:
        return False, type(exc).__name__
    if proc.returncode != 0:
        return False, f"lark_cli_exit_{proc.returncode}"
    try:
        envelope = json.loads(proc.stdout.strip() or "{}")
    except ValueError:
        return False, "lark_cli_invalid_json"
    if isinstance(envelope, dict) and envelope.get("ok") is True:
        return True, "ok"
    return False, "lark_cli_ok_false"


def _send_feishu_personal_dm(payload: dict[str, Any], open_id: str | None = None) -> dict[str, Any]:
    target_oid = _resolve_personal_feishu_open_id(open_id)
    card_v2 = payload.get("feishu_card_v2") or payload["feishu_card"]

    cli_ok, _cli_reason = _try_lark_cli_send(card_v2, target_oid)
    if cli_ok:
        return {"ok": True, "transport": "lark-cli", "schema": "2.0", "target_type": "open_id", "detail": "个人私信 Card 2.0 已发送"}

    # Smooth fallback to OpenAPI helper via personal open_id (never group chat), downgrading v2 -> v1 if rejected
    mod = _load_feishu_dm_module()
    ok_v2 = bool(mod.send_card(card_v2, target_oid))
    if ok_v2:
        return {"ok": True, "transport": "openapi", "schema": "2.0", "target_type": "open_id", "detail": "个人私信卡片(v2)已发送"}

    ok_v1 = bool(mod.send_card(payload["feishu_card"], target_oid))
    return {
        "ok": ok_v1,
        "transport": "openapi",
        "schema": "1.0" if ok_v1 else "2.0",
        "target_type": "open_id",
        "detail": "个人私信卡片已降级发送" if ok_v1 else "飞书接口返回失败",
    }


def _send_wecom_bot(payload: dict[str, Any]) -> dict[str, Any]:
    if requests is None:
        return {"ok": False, "detail": "requests 未安装"}
    url = _env_nonempty("WECOM_WEBHOOK_URL")
    if not url:
        return {"ok": False, "detail": "WECOM_WEBHOOK_URL 未配置"}

    resp = requests.post(url, json={"msgtype": "template_card", "template_card": payload["wecom_card"]}, timeout=12)
    try:
        data = resp.json()
    except ValueError:
        data = {}
    if 200 <= resp.status_code < 300 and (not isinstance(data, dict) or data.get("errcode", 0) == 0):
        return {"ok": True, "status_code": resp.status_code, "detail": "企业微信模板卡片发送完成"}

    # Fallback to concise markdown if webhook restricts template_card
    resp_md = requests.post(
        url,
        json={"msgtype": "markdown", "markdown": {"content": payload["wechat_markdown"][:4000]}},
        timeout=12,
    )
    try:
        md_data = resp_md.json()
    except ValueError:
        md_data = {}
    ok = 200 <= resp_md.status_code < 300 and (not isinstance(md_data, dict) or md_data.get("errcode", 0) == 0)
    return {
        "ok": ok,
        "status_code": resp_md.status_code,
        "detail": "企业微信 Markdown 发送完成" if ok else f"HTTP {resp_md.status_code}",
    }


def _send_wechat_push(payload: dict[str, Any]) -> dict[str, Any]:
    if requests is None:
        return {"ok": False, "detail": "requests 未安装"}

    sc_key = _env_nonempty("SERVERCHAN_SENDKEY")
    if sc_key:
        resp = requests.post(
            f"https://sctapi.ftqq.com/{sc_key}.send",
            data={"title": payload["title"][:32], "desp": payload["wechat_markdown"]},
            timeout=12,
        )
        try:
            body = resp.json()
        except ValueError:
            body = {}
        ok = 200 <= resp.status_code < 300 and (not isinstance(body, dict) or body.get("code", 0) == 0)
        return {"ok": ok, "status_code": resp.status_code, "provider": "serverchan", "detail": "Server酱微信发送完成" if ok else f"HTTP {resp.status_code}"}

    pp_token = _env_nonempty("PUSHPLUS_TOKEN")
    if pp_token:
        resp = requests.post(
            "https://www.pushplus.plus/send",
            json={"token": pp_token, "title": payload["title"], "content": payload["wechat_markdown"], "template": "markdown"},
            timeout=12,
        )
        try:
            body = resp.json()
        except ValueError:
            body = {}
        ok = 200 <= resp.status_code < 300 and (not isinstance(body, dict) or body.get("code") == 200)
        return {"ok": ok, "status_code": resp.status_code, "provider": "pushplus", "detail": "PushPlus 微信发送完成" if ok else f"HTTP {resp.status_code}"}

    wxp_token = _env_nonempty("WXPUSHER_TOKEN")
    if wxp_token:
        uids = [u.strip() for u in _env_nonempty("WXPUSHER_UID").split(",") if u.strip()]
        if not uids:
            return {"ok": False, "provider": "wxpusher", "detail": "WXPUSHER_UID 未配置"}
        resp = requests.post(
            "https://wxpusher.zjiecode.com/api/send/message",
            json={
                "appToken": wxp_token,
                "content": payload["wechat_markdown"],
                "summary": payload["title"][:90],
                "contentType": 3,
                "uids": uids,
            },
            timeout=12,
        )
        try:
            body = resp.json()
        except ValueError:
            body = {}
        ok = 200 <= resp.status_code < 300 and (not isinstance(body, dict) or body.get("code") == 1000)
        return {"ok": ok, "status_code": resp.status_code, "provider": "wxpusher", "detail": "WxPusher 微信发送完成" if ok else f"HTTP {resp.status_code}"}

    return {"ok": False, "detail": "个人微信通道未配置"}


def _send_ntfy(payload: dict[str, Any]) -> dict[str, Any]:
    from urllib.parse import urlparse

    url = _env_nonempty("NTFY_TOPIC_URL")
    if not url:
        return {"ok": False, "detail": "NTFY_TOPIC_URL 未配置"}

    parsed = urlparse(url.rstrip("/"))
    topic = parsed.path.strip("/").split("/")[-1] if parsed.path.strip("/") else ""
    if topic and parsed.scheme and parsed.netloc:
        root_url = f"{parsed.scheme}://{parsed.netloc}"
        resp = requests.post(
            root_url,
            json={"topic": topic, "title": payload["title"], "message": payload["text"], "priority": 3},
            timeout=12,
        )
    else:
        resp = requests.post(
            url,
            data=payload["text"].encode("utf-8"),
            headers={"Content-Type": "text/plain; charset=utf-8"},
            timeout=12,
        )
    ok = 200 <= resp.status_code < 300
    return {"ok": ok, "status_code": resp.status_code, "detail": "ntfy 发送完成" if ok else f"HTTP {resp.status_code}"}


def _send_bark(payload: dict[str, Any]) -> dict[str, Any]:
    url = _env_nonempty("BARK_URL")
    if not url:
        return {"ok": False, "detail": "BARK_URL 未配置"}
    resp = requests.post(
        url,
        json={"title": payload["title"], "body": payload["text"], "group": "宏观驾驶舱"},
        timeout=12,
    )
    ok = 200 <= resp.status_code < 300
    return {"ok": ok, "status_code": resp.status_code, "detail": "Bark 发送完成" if ok else f"HTTP {resp.status_code}"}


def _send_telegram(payload: dict[str, Any]) -> dict[str, Any]:
    token = _env_nonempty("TG_BOT_TOKEN")
    chat_id = _env_nonempty("TG_CHAT_ID")
    if not token or not chat_id:
        return {"ok": False, "detail": "TG_BOT_TOKEN 或 TG_CHAT_ID 未配置"}
    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": f"{payload['title']}\n\n{payload['text']}"},
        timeout=12,
    )
    ok = 200 <= resp.status_code < 300
    return {"ok": ok, "status_code": resp.status_code, "detail": "Telegram 发送完成" if ok else f"HTTP {resp.status_code}"}


def _send_webhook(payload: dict[str, Any]) -> dict[str, Any]:
    url = _env_nonempty("AGENT_ALERT_WEBHOOK_URL")
    if not url:
        return {"ok": False, "detail": "AGENT_ALERT_WEBHOOK_URL 未配置"}
    resp = requests.post(
        url,
        json={
            "title": payload["title"],
            "text": payload["text"],
            "generated_at_bjt": payload["generated_at_bjt"],
        },
        timeout=12,
    )
    ok = 200 <= resp.status_code < 300
    return {"ok": ok, "status_code": resp.status_code, "detail": "Webhook 发送完成" if ok else f"HTTP {resp.status_code}"}


def dispatch_mobile_push(
    state: dict[str, Any],
    *,
    channels: list[str] | None = None,
    dry_run: bool = True,
    feishu_open_id: str | None = None,
    workspace: dict[str, Any] | None = None,
    mode: str = "daily",
    include_amounts: bool = False,
) -> dict[str, Any]:
    """Dispatch mobile push digest; strictly dry-run unless dry_run=False is explicitly passed."""
    load_push_env()
    payload = build_mobile_digest_payload(
        state,
        workspace=workspace,
        mode=mode,
        include_amounts=include_amounts,
    )
    ch_status = payload["channels"]

    if feishu_open_id is not None:
        _resolve_personal_feishu_open_id(feishu_open_id)

    selected = channels if channels is not None else [k for k, v in ch_status.items() if v["configured"]]
    selected = [c for c in selected if c in CHANNEL_LABELS]

    results: dict[str, dict[str, Any]] = {}
    if dry_run:
        for ch in selected:
            configured = bool(ch_status.get(ch, {}).get("configured"))
            results[ch] = {
                "ok": configured,
                "dry_run": True,
                "configured": configured,
                "detail": "演练预览（未触发真实发送）" if configured else "渠道未配置，已跳过",
            }
        return {
            "dry_run": True,
            "selected_channels": selected,
            "results": results,
            "payload": payload,
        }

    senders = {
        "feishu_dm": lambda: _send_feishu_personal_dm(payload, open_id=feishu_open_id),
        "wecom_bot": lambda: _send_wecom_bot(payload),
        "wechat_push": lambda: _send_wechat_push(payload),
        "ntfy": lambda: _send_ntfy(payload),
        "bark": lambda: _send_bark(payload),
        "telegram": lambda: _send_telegram(payload),
        "webhook": lambda: _send_webhook(payload),
    }

    for ch in selected:
        if not ch_status.get(ch, {}).get("configured"):
            results[ch] = {"ok": False, "dry_run": False, "configured": False, "detail": "渠道未配置"}
            continue
        try:
            res = senders[ch]()
            res["dry_run"] = False
            res["configured"] = True
            results[ch] = res
        except ValueError:
            raise
        except Exception as exc:
            results[ch] = {"ok": False, "dry_run": False, "configured": True, "detail": f"发送异常: {type(exc).__name__}"}

    return {
        "dry_run": False,
        "selected_channels": selected,
        "results": results,
        "payload": payload,
    }


def _cli() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="MacroCockpit mobile digest push tool")
    parser.add_argument("--dry-run", action="store_true", help="Preview payload JSON without sending (default)")
    parser.add_argument("--live", action="store_true", help="Actually dispatch to configured channels")
    parser.add_argument("--channels", help="Comma-separated channels, e.g. feishu_dm,wecom_bot,ntfy")
    parser.add_argument("--mode", default="daily", choices=["daily", "headwind", "drift"], help="Digest mode")
    parser.add_argument("--include-amounts", action="store_true", help="Explicitly include USD/RMB amounts")
    parser.add_argument("--state", default=str(STATE_PATH), help="Path to cockpit_state.json")
    args = parser.parse_args()

    state_file = Path(args.state)
    state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else {}
    channels = [c.strip() for c in args.channels.split(",") if c.strip()] if args.channels else None
    dry_run = not args.live or args.dry_run
    out = dispatch_mobile_push(
        state,
        channels=channels,
        dry_run=dry_run,
        mode=args.mode,
        include_amounts=args.include_amounts,
    )
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    _cli()
