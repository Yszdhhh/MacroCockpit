"""Machine-enforced architecture governance, SSOT, hygiene, and HTTP security regression gates."""
from __future__ import annotations

import ast
import gzip
import json
import re
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterable

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PRODUCTION_PACKAGES = ("backend", "desktop")
LEVEL0_MODULES = frozenset({"backend.runtime", "backend.guardrails", "backend.http_security"})
ALLOWED_DYNAMIC_DESKTOP_LOADERS = frozenset({"backend.mcp_stdio"})

CLI_ENTRYPOINTS = (
    "backend.server",
    "desktop.pro_server",
    "backend.signal_aggregator",
    "backend.cloud_collector",
    "backend.vps_sync",
    "backend.forum_sentiment",
    "backend.notion_sync",
    "backend.periodic_report",
    "backend.mcp_stdio",
    "backend.backup_journal",
)

UI_PUBLIC_FILES = (
    ROOT / "web" / "index.html",
    ROOT / "web" / "tokens.css",
    ROOT / "web" / "styles.css",
    ROOT / "web" / "app.js",
    ROOT / "desktop" / "ui" / "index.html",
    ROOT / "desktop" / "ui" / "styles.css",
    ROOT / "desktop" / "ui" / "app.js",
)


def _iter_production_modules() -> dict[str, Path]:
    modules: dict[str, Path] = {}
    for pkg in PRODUCTION_PACKAGES:
        pkg_dir = ROOT / pkg
        for path in sorted(pkg_dir.glob("*.py")):
            mod_name = pkg if path.name == "__init__.py" else f"{pkg}.{path.stem}"
            modules[mod_name] = path
    return modules


def _resolve_import_from(current_mod: str, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    parts = current_mod.split(".")
    # If current_mod is a file module `pkg.mod`, strip module leaf; package `__init__` has 1 part `pkg`.
    pkg_parts = parts[:-1] if len(parts) > 1 else parts
    up = node.level - 1
    anchor = pkg_parts[: len(pkg_parts) - up] if up <= len(pkg_parts) else []
    base = ".".join(anchor)
    if node.module:
        return f"{base}.{node.module}" if base else node.module
    return base


def _collect_internal_edges(
    modules: dict[str, Path],
    *,
    top_level_only: bool = False,
) -> dict[str, set[str]]:
    known = set(modules)
    graph: dict[str, set[str]] = {m: set() for m in modules}

    for mod_name, path in modules.items():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        nodes: Iterable[ast.AST] = tree.body if top_level_only else ast.walk(tree)
        for node in nodes:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    for target in known:
                        if alias.name == target or alias.name.startswith(f"{target}."):
                            graph[mod_name].add(target)
            elif isinstance(node, ast.ImportFrom):
                base = _resolve_import_from(mod_name, node)
                if base in known:
                    graph[mod_name].add(base)
                    continue
                if base in PRODUCTION_PACKAGES:
                    for alias in node.names:
                        cand = f"{base}.{alias.name}"
                        if cand in known:
                            graph[mod_name].add(cand)
    return graph


def _tarjan_sccs(graph: dict[str, set[str]]) -> list[list[str]]:
    index = 0
    indices: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    cycles: list[list[str]] = []

    def strongconnect(v: str) -> None:
        nonlocal index
        indices[v] = index
        lowlink[v] = index
        index += 1
        stack.append(v)
        on_stack.add(v)

        for w in sorted(graph.get(v, ())):
            if w == v:
                continue
            if w not in indices:
                strongconnect(w)
                lowlink[v] = min(lowlink[v], lowlink[w])
            elif w in on_stack:
                lowlink[v] = min(lowlink[v], indices[w])

        if lowlink[v] == indices[v]:
            scc: list[str] = []
            while True:
                w = stack.pop()
                on_stack.remove(w)
                scc.append(w)
                if w == v:
                    break
            if len(scc) > 1:
                cycles.append(sorted(scc))

    for node in sorted(graph):
        if node not in indices:
            strongconnect(node)
    return cycles


def test_zero_import_cycles_across_backend_and_desktop() -> None:
    modules = _iter_production_modules()
    assert len(modules) >= 40
    top_cycles = _tarjan_sccs(_collect_internal_edges(modules, top_level_only=True))
    all_cycles = _tarjan_sccs(_collect_internal_edges(modules, top_level_only=False))
    assert top_cycles == [], f"Top-level import cycles detected: {top_cycles}"
    assert all_cycles == [], f"Runtime import cycles detected: {all_cycles}"


def test_strict_layering_and_zero_backend_to_desktop_imports() -> None:
    modules = _iter_production_modules()
    graph = _collect_internal_edges(modules, top_level_only=False)

    # Level-0 foundation modules must have zero internal backend/desktop imports.
    for l0 in sorted(LEVEL0_MODULES):
        assert l0 in modules, f"Missing Level-0 module: {l0}"
        deps = sorted(d for d in graph[l0] if d != l0)
        assert deps == [], f"Level-0 module {l0} imports internal modules: {deps}"

    # backend must never statically import desktop.
    for mod_name, path in modules.items():
        if not mod_name.startswith("backend"):
            continue
        desktop_deps = sorted(d for d in graph[mod_name] if d.startswith("desktop"))
        assert desktop_deps == [], f"{mod_name} imports desktop statically: {desktop_deps}"

        if mod_name not in ALLOWED_DYNAMIC_DESKTOP_LOADERS:
            src = path.read_text(encoding="utf-8")
            assert not re.search(r"""import_module\(\s*['"]desktop\b""", src), (
                f"{mod_name} dynamically imports desktop outside CLI plugin hook"
            )


def test_zero_cross_module_private_imports_in_production() -> None:
    modules = _iter_production_modules()
    violations: list[str] = []
    for mod_name, path in modules.items():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            target_mod = _resolve_import_from(mod_name, node)
            if not (
                target_mod in PRODUCTION_PACKAGES
                or target_mod.startswith("backend.")
                or target_mod.startswith("desktop.")
            ):
                continue
            for alias in node.names:
                name = alias.name
                if name.startswith("_") and not (name.startswith("__") and name.endswith("__")):
                    violations.append(f"{mod_name}:{node.lineno} from {target_mod} import {name}")
    assert violations == [], f"Cross-module private symbol imports found: {violations}"


def test_ssot_authorities_in_python_and_ui_tokens() -> None:
    modules = _iter_production_modules()

    forbidden_term_owners: list[str] = []
    bjt_owners: list[str] = []
    const_eq_owners: list[str] = []
    legacy_defs: list[str] = []

    for mod_name, path in modules.items():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                if node.name == "const_eq":
                    const_eq_owners.append(mod_name)
                elif node.name == "_safe_home":
                    legacy_defs.append(f"{mod_name}: def _safe_home")
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for tgt in targets:
                    if isinstance(tgt, ast.Name):
                        if tgt.id == "FORBIDDEN_TERMS":
                            forbidden_term_owners.append(mod_name)
                        elif tgt.id == "_FORBIDDEN_SCRUB":
                            legacy_defs.append(f"{mod_name}: _FORBIDDEN_SCRUB")
                        elif tgt.id == "BJT":
                            bjt_owners.append(mod_name)

    assert forbidden_term_owners == ["backend.guardrails"], forbidden_term_owners
    assert bjt_owners == ["backend.runtime"], bjt_owners
    assert const_eq_owners == ["backend.http_security"], const_eq_owners
    assert legacy_defs == [], legacy_defs

    tokens_css = (ROOT / "web" / "tokens.css").read_text(encoding="utf-8")
    web_css = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")
    desktop_css = (ROOT / "desktop" / "ui" / "styles.css").read_text(encoding="utf-8")
    web_html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")

    assert ":root" in tokens_css and '[data-theme="dark"]' in tokens_css
    assert "--paper-read:" in tokens_css and "--mast-bg:" in tokens_css
    assert ":root" not in web_css and "--paper-read:" not in web_css
    assert ":root" not in desktop_css and "--paper-read:" not in desktop_css
    assert len(re.findall(r"^@media\b", web_css, flags=re.MULTILINE)) <= 5
    assert 'value="100000"' not in web_html
    retired_hits = [m for m, p in modules.items() if "/api/sync" in p.read_text(encoding="utf-8")]
    assert retired_hits == [], f"Retired /api/sync still referenced in: {retired_hits}"


def test_public_ui_zero_forbidden_terms() -> None:
    from backend.guardrails import FORBIDDEN_TERMS

    patterns = [
        (term, re.compile(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])" if term.isascii() else re.escape(term)))
        for term in FORBIDDEN_TERMS
    ]
    hits: list[str] = []
    for path in UI_PUBLIC_FILES:
        text = path.read_text(encoding="utf-8")
        for term, pat in patterns:
            if pat.search(text):
                hits.append(f"{path.relative_to(ROOT).as_posix()}: {term}")
    assert hits == [], f"Forbidden jargon/placeholder terms in UI assets: {hits}"


def test_http_security_primitives_and_desktop_mcp_error_boundaries(tmp_path: Path) -> None:
    from backend import http_security
    from desktop import pro_server

    raw_ok = b"A" * 1000
    assert http_security.decompress_gzip_bounded(gzip.compress(raw_ok), 1000) == raw_ok

    with pytest.raises(http_security.HttpSecurityError) as exc_over:
        http_security.decompress_gzip_bounded(gzip.compress(b"A" * 1001), 1000)
    assert exc_over.value.status == 413

    zero_bomb = gzip.compress(b"\x00" * (5 * 1024 * 1024))
    with pytest.raises(http_security.HttpSecurityError) as exc_bomb:
        http_security.decompress_gzip_bounded(zero_bomb, 4096)
    assert exc_bomb.value.status == 413

    assert http_security.origin_allowed("HTTP://LOCALHOST:8788", host_header="127.0.0.1:8788", public_url="")
    assert http_security.origin_allowed("http://[::1]:8788", host_header="127.0.0.1:8788", public_url="")
    assert not http_security.origin_allowed("http://localhost.evil.com", host_header="127.0.0.1:8788", public_url="")
    assert http_security.openbb_cors_origin("https://pro.openbb.co") == "https://pro.openbb.co"
    assert http_security.openbb_cors_origin("https://pro.openbb.co.evil.com") is None

    # Desktop Pro /mcp version validation & JSON 500 error masking
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    srv = pro_server.create_desktop_server(
        "127.0.0.1",
        0,
        base_dir=tmp_path,
        desktop_token="tok-gov-test",
        db_path=tmp_path / "data" / "user_journal.db",
        state_path=tmp_path / "data" / "cockpit_state.json",
        ui_dir=ROOT / "desktop" / "ui",
    )
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{srv.server_port}/mcp",
            data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-Desktop-Token": "tok-gov-test",
                "MCP-Protocol-Version": "1999-01-01",
            },
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as http_err:
            urllib.request.urlopen(req, timeout=5)
        assert http_err.value.code == 400
        assert http_err.value.headers.get("MCP-Protocol-Version")
    finally:
        srv.shutdown()
        srv.server_close()


def test_ruff_static_hygiene_and_cli_entrypoints() -> None:
    ruff_check = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--no-cache", "--select", "F821,F401,F811,F541", "backend", "desktop"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=30,
    )
    if ruff_check.returncode != 0 and "No module named ruff" in (ruff_check.stderr or ""):
        pytest.skip("ruff not installed in environment")
    assert ruff_check.returncode == 0, f"Ruff check failed:\n{ruff_check.stdout}\n{ruff_check.stderr}"

    for mod in CLI_ENTRYPOINTS:
        proc = subprocess.run(
            [sys.executable, "-m", mod, "--help"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert proc.returncode == 0, f"{mod} --help failed ({proc.returncode}): {proc.stderr}"
