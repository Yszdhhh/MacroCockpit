"""Sync sanitized public state and selected markdown articles to VPS via v2 publish or SSH."""
from __future__ import annotations

import argparse
import base64
import gzip
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import requests

from backend.mobile_push import COCKPIT_ROOT, load_push_env
from backend.public_snapshot import (
    has_private_article_content,
    private_article_filenames,
    project_public_state,
    safe_article_filename,
)
from backend.signal_aggregator import get_allowed_article_dirs, parse_markdown_article, read_article_markdown

STATE_PATH = Path(os.environ.get("MACROCOCKPIT_DATA_DIR") or COCKPIT_ROOT / "data") / "cockpit_state.json"
MAX_ARTICLE_BYTES = 256 * 1024


def _safe_article_name(value: Any) -> str | None:
    """Preserve strict basename-only markdown article validation."""
    return safe_article_filename(value)


def collect_payload(
    state_path: Path,
    max_articles: int,
    *,
    generation: int | None = None,
    producer_id: str = "windows-primary",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load local state, strip private keys via project_public_state(include_candidates=True), and attach articles."""
    raw_state = json.loads(state_path.read_text(encoding="utf-8"))
    blocked = private_article_filenames(raw_state.get("articles") or [])
    names: list[str] = []
    for article in raw_state.get("articles") or []:
        name = _safe_article_name(article.get("filename")) if isinstance(article, dict) else None
        if name and name not in names:
            names.append(name)
    for kol in raw_state.get("kol_digest") or []:
        name = _safe_article_name(kol.get("article_file")) if isinstance(kol, dict) else None
        if name and name not in names:
            names.append(name)

    articles: list[dict[str, str]] = []
    inbox_names = {
        name
        for article in raw_state.get("articles") or []
        if isinstance(article, dict) and article.get("source") == "本地知识库"
        and (name := _safe_article_name(article.get("filename")))
    }
    allowed_dirs = [(state_path.parent.parent / "articles", "同步深度长文"), *get_allowed_article_dirs()]
    refreshed_inbox = {}
    for name in names:
        if name in blocked:
            continue
        for folder, label in allowed_dirs:
            root = folder.resolve()
            candidate = (root / name).resolve()
            if candidate.parent != root or not candidate.is_file():
                continue
            content = read_article_markdown(candidate, label, max_bytes=MAX_ARTICLE_BYTES)
            if content is not None and label == "本地知识库":
                fresh = parse_markdown_article(candidate, label)
                if fresh is None:
                    content = None
                else:
                    refreshed_inbox[name] = fresh
            if content is None:
                blocked.add(name)
            elif len(articles) < max_articles:
                articles.append({"filename": name, "content": content})
            break
        else:
            if name in inbox_names:
                blocked.add(name)

    if refreshed_inbox:
        raw_state["articles"] = [
            refreshed_inbox.get(_safe_article_name(article.get("filename")), article)
            if isinstance(article, dict) else article
            for article in raw_state.get("articles") or []
        ]
    state = project_public_state(raw_state, include_candidates=True, excluded_article_names=blocked)
    if generation is not None:
        state["generation"] = int(generation)

    payload: dict[str, Any] = {
        "state": state,
        "articles": articles,
        "producer_id": str(producer_id or "windows-primary"),
    }
    if generation is not None:
        payload["generation"] = int(generation)

    stats = {
        "articles_count": len(articles),
        "state_bytes": len(json.dumps(state, ensure_ascii=False).encode("utf-8")),
        "article_bytes": sum(len(a["content"].encode("utf-8")) for a in articles),
    }
    return payload, stats


def _resolve_publish_endpoints(explicit_url: str | None = None) -> tuple[str, str | None]:
    configured = (explicit_url or os.environ.get("MACROCOCKPIT_SYNC_URL") or "").strip()
    if configured:
        cleaned = configured.rstrip("/")
        if cleaned.endswith("/api/sync"):
            return cleaned[: -len("/api/sync")] + "/api/v2/publish", cleaned
        if cleaned.endswith("/api/v2/publish"):
            return cleaned, cleaned[: -len("/api/v2/publish")] + "/api/sync"
        return cleaned, None

    base = (os.environ.get("MACROCOCKPIT_PUBLIC_URL") or "https://news.shuashuabao.xyz").strip().rstrip("/")
    return f"{base}/api/v2/publish", f"{base}/api/sync"


def _public_payload(payload: dict[str, Any]) -> dict[str, Any]:
    state = payload.get("state") or {}
    articles = payload.get("articles") or []
    blocked = private_article_filenames(articles)
    blocked.update(private_article_filenames(state.get("articles") or []))
    return {
        **payload,
        "state": project_public_state(state, include_candidates=True, excluded_article_names=blocked),
        "articles": [
            a for a in articles if not has_private_article_content(a)
            and (not isinstance(a, dict) or _safe_article_name(a.get("filename")) not in blocked)
        ],
    }


def publish_via_http(
    payload: dict[str, Any],
    *,
    url: str | None = None,
    sync_token: str | None = None,
    timeout: tuple[int, int] = (20, 120),
) -> tuple[bool, dict[str, Any]]:
    """Publish sanitized public snapshot via POST /api/v2/publish, falling back to /api/sync ONLY on HTTP 404."""
    token = (sync_token if sync_token is not None else (os.environ.get("MACROCOCKPIT_SYNC_TOKEN") or "")).strip()
    if not token:
        return False, {"reason": "missing_sync_token"}

    sanitized_payload = _public_payload(payload)
    primary_url, legacy_url = _resolve_publish_endpoints(url)
    headers = {"Authorization": f"Bearer {token}"}

    try:
        resp = requests.post(primary_url, json=sanitized_payload, headers=headers, timeout=timeout)
        if resp.status_code == 404 and legacy_url:
            resp_legacy = requests.post(legacy_url, json=sanitized_payload, headers=headers, timeout=timeout)
            resp_legacy.raise_for_status()
            return True, {"action": "http_sync_legacy", "endpoint": legacy_url, "status_code": resp_legacy.status_code}
        resp.raise_for_status()
        return True, {"action": "http_publish", "endpoint": primary_url, "status_code": resp.status_code}
    except requests.HTTPError as exc:
        code = getattr(exc.response, "status_code", None)
        return False, {"reason": f"HTTP_{code}" if code else type(exc).__name__}
    except requests.ConnectionError as exc:
        can_fallback = not isinstance(exc, (requests.exceptions.SSLError, requests.exceptions.ProxyError))
        return False, {"reason": type(exc).__name__, "connection_failed": can_fallback}
    except requests.RequestException as exc:
        return False, {"reason": type(exc).__name__}


def _ssh_push(payload: dict[str, Any], host: str, remote_base: str = "/opt/macro-intel") -> dict[str, Any]:
    """Push sanitized snapshot over SSH and call remote publish_generation() atomically."""
    safe_payload = _public_payload(payload)
    compressed = gzip.compress(json.dumps(safe_payload, ensure_ascii=False).encode("utf-8"))
    remote_script = (
        "import gzip,json,sys\n"
        "from pathlib import Path\n"
        f"base=Path({remote_base!r})\n"
        "sys.path.insert(0, str(base))\n"
        "from backend.public_snapshot import publish_generation\n"
        "data=json.loads(gzip.decompress(sys.stdin.buffer.read()).decode('utf-8'))\n"
        "res=publish_generation(\n"
        "    base,\n"
        "    data['state'],\n"
        "    data.get('articles', []),\n"
        "    producer_id=str(data.get('producer_id') or 'windows-primary'),\n"
        "    generation=data.get('generation'),\n"
        ")\n"
        "print(json.dumps({'ok': True, 'generation': res.get('generation')}, ensure_ascii=False))\n"
    )
    b64 = base64.b64encode(remote_script.encode("utf-8")).decode("ascii")
    proc = subprocess.run(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=20",
            host,
            f"python3 -c \"import base64;exec(base64.b64decode('{b64}'))\"",
        ],
        input=compressed,
        capture_output=True,
        timeout=90,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ssh sync failed ({proc.returncode}): {proc.stderr.decode('utf-8', errors='replace')[:300]}")
    try:
        return json.loads(proc.stdout.decode("utf-8", errors="replace").strip() or "{}")
    except ValueError:
        return {"ok": True}


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Sync MacroCockpit data to VPS")
    parser.add_argument("--refresh-state", action="store_true")
    parser.add_argument("--refresh-fred", action="store_true")
    parser.add_argument("--url")
    parser.add_argument("--ssh-host", default="tencent-vps")
    parser.add_argument("--via-ssh", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-articles", type=int, default=40)
    parser.add_argument("--no-ssh-fallback", action="store_true")
    parser.add_argument("--generation", type=int, default=None)
    parser.add_argument("--producer-id", default="windows-primary")
    args = parser.parse_args()

    if not args.dry_run:
        load_push_env(include_sync_token=True)
    state_path = Path(os.environ.get("MACROCOCKPIT_DATA_DIR") or COCKPIT_ROOT / "data") / "cockpit_state.json"
    if args.refresh_state:
        from backend.signal_aggregator import generate_cockpit_state

        generate_cockpit_state(refresh_fred=args.refresh_fred, output_path=state_path)

    payload, stats = collect_payload(
        state_path,
        args.max_articles,
        generation=args.generation,
        producer_id=args.producer_id,
    )
    if args.dry_run:
        print(json.dumps({"action": "dry_run", **stats}, ensure_ascii=False))
        return

    http_error = "forced_ssh"
    can_fallback = args.via_ssh
    if not args.via_ssh:
        ok, info = publish_via_http(payload, url=args.url)
        if ok:
            print(json.dumps({**info, **stats}, ensure_ascii=False))
            return
        http_error = str(info.get("reason") or "http_failed")
        can_fallback = info.get("connection_failed") is True

    if args.no_ssh_fallback or not can_fallback:
        print(json.dumps({"action": "http_failed", "reason": http_error, **stats}, ensure_ascii=False))
        raise SystemExit(1)

    remote_res = _ssh_push(payload, args.ssh_host)
    print(
        json.dumps(
            {
                "action": "ssh_sync",
                "ssh_host": args.ssh_host,
                "http_result": http_error,
                "generation": remote_res.get("generation"),
                **stats,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
