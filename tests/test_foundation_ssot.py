"""Unit tests for canonical Level-0 modules: backend/runtime.py and backend/guardrails.py."""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from backend import guardrails, runtime


def test_l0_modules_have_zero_internal_imports() -> None:
    root = Path(__file__).resolve().parent.parent
    for rel in ("backend/runtime.py", "backend/guardrails.py"):
        tree = ast.parse((root / rel).read_text(encoding="utf-8"), filename=rel)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith(("backend", "desktop")), f"{rel} imports {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                assert node.level == 0 and not mod.startswith(("backend", "desktop")), f"{rel} imports {mod}"


def test_runtime_clock_json_and_lock(tmp_path: Path) -> None:
    target = tmp_path / "sample.json"
    with runtime.file_lock(target):
        runtime.atomic_write_json(target, {"ok": True, "ts": runtime.now_bjt_iso()}, check_finite=True)
    loaded = runtime.read_json_safe(target)
    assert loaded["ok"] is True
    assert "+08:00" in loaded["ts"]

    with pytest.raises(ValueError):
        runtime.atomic_write_json(target, {"bad": float("nan")}, check_finite=True)

    assert runtime.finite_float("1.5") == 1.5
    assert runtime.finite_float(float("inf"), default=None) is None
    assert runtime.clamp(12.0, 0.0, 10.0) == 10.0
    dt = runtime.parse_iso_dt("2026-11-28T02:00:00Z")
    assert dt is not None and dt.hour == 10


def test_guardrails_scrub_privacy_regex_and_ssrf() -> None:
    assert guardrails.scrub_forbidden("量化E21信号") == "量化波动结构信号"
    assert guardrails.scrub_forbidden("Tier-1 Sleeve Regime N/A") == "核心 仓位 市场周期 —"
    assert (
        guardrails.scrub_forbidden("Amazon/AWS; Japan/Australia; https://www.bbc.com/news/en/americas-1 main/article")
        == "Amazon/AWS; Japan/Australia; https://www.bbc.com/news/en/americas-1 main/article"
    )
    assert (
        guardrails.scrub_forbidden("# Heading\n\nParagraph 1\n\n- item", preserve_newlines=True)
        == "# Heading\n\nParagraph 1\n\n- item"
    )
    assert "高流动性超跌结构" in guardrails.scrub_quant_thesis("E33+E32 液态孤立慢洗")


def test_resolve_data_dir_explicit_root_isolation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    custom_env = tmp_path / "env_data"
    explicit_root = tmp_path / "isolated_root"
    monkeypatch.setenv("MACROCOCKPIT_DATA_DIR", str(custom_env))
    assert runtime.resolve_data_dir() == custom_env
    assert runtime.resolve_data_dir(runtime.resolve_cockpit_root()) == custom_env
    assert runtime.resolve_data_dir(explicit_root) == explicit_root / "data"

    assert guardrails.has_private_article_text("当前持仓：BTC 10000 美元")
    assert not guardrails.has_private_article_text("Nvidia Blackwell demand continues rising.")
    assert guardrails.safe_article_filename("guru_test.md") == "guru_test.md"
    assert guardrails.safe_article_filename("../etc/passwd.md") is None

    rx = guardrails.check_safe_regex(r"NVDA|AMD")
    assert rx.search("nvda")
    with pytest.raises(ValueError):
        guardrails.check_safe_regex(r"(a+)+")

    assert not guardrails.is_safe_public_url("http://127.0.0.1:8788/api/state")
    assert not guardrails.is_safe_public_url("http://169.254.169.254/latest/meta-data/")
    assert not guardrails.is_safe_public_url("file:///etc/passwd")
