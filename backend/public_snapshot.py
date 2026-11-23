"""Public state projection, atomic generation publishing, and manifest-backed reads."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import uuid
from contextlib import contextmanager
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

BJT = timezone(timedelta(hours=8))
MAX_ARTICLE_BYTES = 512 * 1024
KEEP_GENERATIONS = 3
DEFAULT_SAMPLE_CAPITAL_USD = 100000

PUBLIC_STATE_KEYS: frozenset[str] = frozenset(
    {
        "schema_version",
        "generation",
        "generated_at_bjt",
        "risk_gate",
        "positions",
        "strategy_targets",
        "macro_fred",
        "us_equities",
        "stock_profiles",
        "signals",
        "signal_candidates",
        "kazike_stats",
        "correlations",
        "kol_digest",
        "articles",
        "push_channels",
        "calendar_events",
        "fx_snapshot",
        "crypto_quotes",
        "coverage",
    }
)

_PRIVATE_TOP_LEVEL_KEYS = frozenset({"journal", "portfolio", "preferences", "workspace", "credentials"})
_PUBLISH_LOCK = threading.RLock()
_PRIVATE_ARTICLE_TEXT = re.compile(
    r"占\s*(?:总\s*)?净值|净值\s*占比|portfolio_exposure"
    r"|(?:现货|合约|实际|私人)持仓明细"
    r"|(?<![A-Z0-9_-])(?:sk-|Bearer\s+|gh[pousr]_[A-Z0-9]+)"
    r"|(?<![A-Z0-9])(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|token|secret)\s*[\x22\x27]?\s*[:=]",
    re.IGNORECASE,
)


_PRIVATE_DEBATE_TEXT = re.compile(
    r"[$＄¥￥€£]|\b(?:token|secret|api[_ -]?key|authorization)\b"
    r"|(?:USD|CNY|RMB)\s*[-+]?\d|\d\s*(?:美元|人民币|元(?!素)|USD|CNY|RMB|USDT)"
    r"|净值\s*[-+]?\d+(?:\.\d+)?\s*[%％]"
    r"|(?:总净值|账户净值|持仓金额|持仓数量|合约名义|现货多头|合约空头|实际持仓)",
    re.IGNORECASE,
)


def has_private_article_text(text: str) -> bool:
    return bool(_PRIVATE_ARTICLE_TEXT.search(text))


def has_private_debate_text(text: str) -> bool:
    return has_private_article_text(text) or bool(_PRIVATE_DEBATE_TEXT.search(text))


def has_private_article_content(article: Any) -> bool:
    if not isinstance(article, dict):
        return False
    filename = article.get("filename")
    is_debate = isinstance(filename, str) and filename.lower().startswith("ta_")
    check = has_private_debate_text if is_debate else has_private_article_text
    return any(
        isinstance(text, str) and check(text)
        for text in (article.get(key) for key in ("body_md", "content", "excerpt", "title", "summary"))
    )


@contextmanager
def file_lock(path: Path):
    """Serialize readers and writers across processes via a stable sidecar."""
    lock_path = path.with_name(f".{path.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock:
        if os.name == "nt":
            import msvcrt

            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        yield


def _now_bjt_iso() -> str:
    return datetime.now(BJT).isoformat(timespec="seconds")


def safe_article_filename(value: Any) -> str | None:
    """Validate strict basename-only markdown article filename."""
    if not isinstance(value, str):
        return None
    if (
        not value
        or len(value) > 180
        or "\x00" in value
        or "/" in value
        or "\\" in value
        or ".." in value
        or Path(value).name != value
        or not value.lower().endswith(".md")
        or "异常告警" in value
    ):
        return None
    return value


def _bear_basket_legs(rec_pct: float, sample_capital: int) -> list[dict[str, Any]]:
    active = rec_pct > 0
    long_pct = round(rec_pct if active else 35.0, 2)
    short_pct = -30.0 if active else -30.0
    long_eff = long_pct if active else 0.0
    short_eff = short_pct if active else 0.0
    return [
        {
            "instrument_id": "BTC",
            "symbol": "BTC",
            "side": "long",
            "target_pct": long_eff,
            "configured_pct": long_pct,
            "sample_notional_usd": round(sample_capital * (long_eff / 100.0), 2),
            "label": f"+{long_pct:g}% BTC",
        },
        {
            "instrument_id": "ALT_BASKET",
            "symbol": "ALT_BASKET",
            "side": "short",
            "target_pct": short_eff,
            "configured_pct": short_pct,
            "sample_notional_usd": round(sample_capital * (abs(short_eff) / 100.0), 2),
            "label": f"{short_pct:g}% ALT_BASKET",
        },
    ]


def _default_legs_for_position(pos: dict[str, Any], sample_capital: int) -> list[dict[str, Any]]:
    pid = str(pos.get("id") or "")
    rec_pct = float(pos.get("recommended_pct") or 0.0)
    if pid == "bear_basket":
        return _bear_basket_legs(rec_pct, sample_capital)
    inst_map = {"btc_trend": "BTC", "gold_hedge": "XAU", "crash_bounce": "CRYPTO_BOUNCE"}
    inst = inst_map.get(pid, pid.upper() or "ASSET")
    return [
        {
            "instrument_id": inst,
            "symbol": inst,
            "side": "long",
            "target_pct": round(rec_pct, 2),
            "sample_notional_usd": round(sample_capital * (rec_pct / 100.0), 2),
            "label": f"+{rec_pct:g}% {inst}" if rec_pct > 0 else f"0% {inst}",
        }
    ]


def _build_structured_strategy_targets(raw_state: dict[str, Any]) -> dict[str, Any]:
    """Ensure strategy_targets is structured with bear_basket dual legs and sample_capital_usd."""
    existing = raw_state.get("strategy_targets")
    sample_capital = DEFAULT_SAMPLE_CAPITAL_USD
    risk = raw_state.get("risk_gate") or {}
    long_cap = float(risk.get("long_risk_cap") if risk.get("long_risk_cap") is not None else 1.0)
    positions = [p for p in (raw_state.get("positions") or []) if isinstance(p, dict)]

    targets_list: list[dict[str, Any]] = []
    if isinstance(existing, dict) and isinstance(existing.get("targets"), list) and existing["targets"]:
        sample_capital = int(existing.get("sample_capital_usd") or DEFAULT_SAMPLE_CAPITAL_USD)
        for item in existing["targets"]:
            if not isinstance(item, dict):
                continue
            entry = dict(item)
            if str(entry.get("id") or "") == "bear_basket" and not entry.get("legs"):
                rec = float(entry.get("target_pct") or entry.get("recommended_pct") or 0.0)
                entry["legs"] = _bear_basket_legs(rec, sample_capital)
                entry.setdefault("legs_summary", "+35% BTC / -30% ALT_BASKET")
            targets_list.append(entry)
    elif isinstance(existing, list) and existing:
        for item in existing:
            if not isinstance(item, dict):
                continue
            entry = dict(item)
            if str(entry.get("id") or "") == "bear_basket" and not entry.get("legs"):
                rec = float(entry.get("target_pct") or entry.get("recommended_pct") or 0.0)
                entry["legs"] = _bear_basket_legs(rec, sample_capital)
                entry.setdefault("legs_summary", "+35% BTC / -30% ALT_BASKET")
            targets_list.append(entry)
    else:
        for pos in positions:
            pid = str(pos.get("id") or "")
            rec_pct = float(pos.get("recommended_pct") or 0.0)
            legs = _default_legs_for_position(pos, sample_capital)
            entry: dict[str, Any] = {
                "id": pid,
                "name": str(pos.get("name") or pid),
                "state": str(pos.get("state") or ""),
                "baseline_pct": float(pos.get("baseline_pct") or 0.0),
                "target_pct": rec_pct,
                "sample_notional_usd": round(float(pos.get("est_notional_usd") or sample_capital * rec_pct / 100.0), 2),
                "legs": legs,
                "note": str(pos.get("note") or ""),
            }
            if pid == "bear_basket":
                long_leg = legs[0]["target_pct"]
                short_leg = legs[1]["target_pct"]
                entry["net_target_pct"] = round(long_leg + short_leg, 2)
                entry["gross_target_pct"] = round(abs(long_leg) + abs(short_leg), 2)
                entry["legs_summary"] = "+35% BTC / -30% ALT_BASKET"
            else:
                entry["net_target_pct"] = round(rec_pct, 2)
                entry["gross_target_pct"] = round(abs(rec_pct), 2)
            targets_list.append(entry)

    by_id = {str(t.get("id")): t for t in targets_list if t.get("id")}
    return {
        "sample_capital_usd": sample_capital,
        "long_risk_cap": long_cap,
        "targets": targets_list,
        "by_id": by_id,
    }


def _build_default_coverage(projected: dict[str, Any]) -> dict[str, Any]:
    raw_cov = projected.get("coverage")
    if isinstance(raw_cov, dict) and raw_cov:
        return dict(raw_cov)
    signals = projected.get("signals") or []
    sources = {str(s.get("source") or "") for s in signals if isinstance(s, dict) and s.get("source")}
    return {
        "signals_count": len(signals),
        "signal_sources_count": len(sources),
        "articles_count": len(projected.get("articles") or []),
        "stock_profiles_count": len(projected.get("stock_profiles") or []),
        "calendar_events_count": len(projected.get("calendar_events") or []),
    }


def _json_clone(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def private_article_filenames(articles: list[dict[str, Any]]) -> set[str]:
    return {
        name for article in articles
        if has_private_article_content(article) and (name := safe_article_filename(article.get("filename")))
    }


def project_public_state(
    raw_state: dict[str, Any],
    *,
    include_candidates: bool = False,
    excluded_article_names: set[str] | None = None,
) -> dict[str, Any]:
    """Project raw cockpit state strictly onto public whitelisted keys without private data."""
    if not isinstance(raw_state, dict):
        raise ValueError("raw_state must be a dictionary")

    projected: dict[str, Any] = {}
    for key in PUBLIC_STATE_KEYS:
        if key in _PRIVATE_TOP_LEVEL_KEYS:
            continue
        if key == "signal_candidates" and not include_candidates:
            continue
        if key in raw_state and raw_state[key] is not None:
            projected[key] = _json_clone(raw_state[key])

    articles = projected.get("articles")
    if isinstance(articles, list):
        projected["articles"] = [
            article for article in articles
            if not has_private_article_content(article)
            and (not isinstance(article, dict) or safe_article_filename(article.get("filename")) not in (excluded_article_names or ()))
        ]
        if len(projected["articles"]) != len(articles) and isinstance(projected.get("coverage"), dict):
            projected["coverage"]["articles_count"] = len(projected["articles"])

    projected["schema_version"] = int(projected.get("schema_version") or 2)
    projected["strategy_targets"] = _build_structured_strategy_targets(projected if "positions" in projected else raw_state)
    projected["coverage"] = _build_default_coverage(projected)
    if not include_candidates:
        projected.pop("signal_candidates", None)
    return projected


def _canonical_state_sha256(state: dict[str, Any]) -> str:
    canonical = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _normalize_articles(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for item in articles or []:
        if not isinstance(item, dict):
            raise ValueError("article entry must be a dict")
        raw_name = item.get("filename")
        safe_name = safe_article_filename(raw_name)
        if not safe_name:
            raise ValueError(f"invalid_article_filename: {raw_name!r}")
        if safe_name in seen_names:
            continue
        content = item.get("content")
        if not isinstance(content, str):
            raise ValueError(f"invalid_article_content: {safe_name}")
        content_bytes = content.encode("utf-8")
        if len(content_bytes) > MAX_ARTICLE_BYTES:
            raise ValueError(f"article_too_large: {safe_name}")
        seen_names.add(safe_name)
        normalized.append(
            {
                "id": Path(safe_name).stem,
                "filename": safe_name,
                "content": content,
                "bytes": len(content_bytes),
                "sha256": hashlib.sha256(content_bytes).hexdigest(),
            }
        )
    return normalized


def _atomic_write_text(dest: Path, text: str) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.{os.getpid()}.{uuid.uuid4().hex[:6]}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8", newline="")
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)


def _prune_old_generations(gens_dir: Path, active_gen: int, keep: int = KEEP_GENERATIONS) -> list[str]:
    if not gens_dir.exists():
        return []
    found: list[tuple[int, Path]] = []
    removed: list[str] = []
    for child in gens_dir.iterdir():
        if not child.is_dir():
            continue
        m = re.fullmatch(r"gen_(\d+)", child.name)
        if m:
            found.append((int(m.group(1)), child))
        elif child.name.startswith(".staging_gen_"):
            shutil.rmtree(child, ignore_errors=True)
            removed.append(child.name)
    found.sort(key=lambda pair: pair[0], reverse=True)
    keep_ids = {active_gen}
    for gen_num, _ in found:
        if len(keep_ids) >= max(1, keep):
            break
        keep_ids.add(gen_num)
    for gen_num, folder in found:
        if gen_num not in keep_ids:
            shutil.rmtree(folder, ignore_errors=True)
            removed.append(folder.name)
    return removed


def _generation_timestamp(value: Any) -> datetime:
    stamp = datetime.fromisoformat(str(value or ""))
    return stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=BJT)


def _read_generation_state(folder: Path, generation: int) -> dict[str, Any]:
    state = json.loads((folder / "state.json").read_text(encoding="utf-8"))
    if not isinstance(state, dict) or type(state.get("generation")) is not int or state["generation"] != generation:
        raise ValueError("invalid generation state")
    return state


def _current_manifest(manifest_path: Path, *, recover_newer: bool = False) -> dict[str, Any] | None:
    gens_dir = manifest_path.parent / "generations"
    active = None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        gen = manifest["generation"]
        if (
            type(gen) is not int or gen <= 0
            or not isinstance(manifest["articles"], list)
            or any(not isinstance(a, dict) or not safe_article_filename(a.get("filename")) for a in manifest["articles"])
        ):
            raise ValueError("invalid generation manifest")
        _generation_timestamp(manifest["generated_at"])
        _generation_timestamp(manifest["published_at"])
        state = _read_generation_state(gens_dir / f"gen_{gen}", gen)
        if manifest["state_sha256"] != _canonical_state_sha256(state):
            raise ValueError("invalid generation state checksum")
        if not recover_newer:
            return manifest
        active = manifest
    except (OSError, ValueError, TypeError, KeyError):
        pass

    # Older generations have no per-generation manifest. Rebuild it from their files.
    candidates = []
    if gens_dir.is_dir():
        for folder in gens_dir.iterdir():
            match = re.fullmatch(r"gen_([1-9]\d*)", folder.name)
            if match and folder.is_dir():
                candidates.append((int(match[1]), folder))
    for gen, folder in sorted(candidates, reverse=True):
        if active is not None and gen <= active["generation"]:
            return active
        try:
            state = _read_generation_state(folder, gen)
            article_dir = folder / "articles"
            articles = _normalize_articles([
                {"filename": path.name, "content": path.read_bytes().decode("utf-8")}
                for path in sorted(article_dir.glob("*")) if path.suffix.lower() == ".md" and path.is_file()
            ])
            by_name = {
                a["filename"]: {key: a[key] for key in ("id", "filename", "bytes", "sha256")}
                for a in articles
            }
            state_sha = _canonical_state_sha256(state)
            saved = folder / "manifest.json"
            if saved.exists():
                manifest = json.loads(saved.read_text(encoding="utf-8"))
                _generation_timestamp(manifest["generated_at"])
                _generation_timestamp(manifest["published_at"])
                entries = manifest["articles"]
                if (
                    type(manifest["generation"]) is not int
                    or manifest["generation"] != gen
                    or manifest["state_sha256"] != state_sha
                    or not isinstance(entries, list)
                    or len(entries) != len(by_name)
                    or any(by_name.get(a["filename"]) != a for a in entries)
                ):
                    raise ValueError("invalid generation manifest")
                return manifest

            published_at = datetime.fromtimestamp(folder.stat().st_mtime, BJT).isoformat(timespec="seconds")
            generated_at = state.get("generated_at_bjt") or published_at
            _generation_timestamp(generated_at)
            return {
                "schema_version": 2,
                "generation": gen,
                "generated_at": generated_at,
                "published_at": published_at,
                "producer_id": "windows-primary",
                "state_sha256": state_sha,
                "articles": list(by_name.values()),
                "coverage": state.get("coverage") or {},
            }
        except (OSError, ValueError, TypeError, KeyError):
            continue
    if active is not None:
        return active
    if candidates or manifest_path.exists():
        raise ValueError("invalid_current_generation")
    return None


def publish_generation(
    base_dir: Path | str,
    state: dict[str, Any],
    articles: list[dict[str, Any]] | None = None,
    *,
    producer_id: str = "windows-primary",
    generation: int | None = None,
) -> dict[str, Any]:
    """Validate public projection + articles, enforce monotonic generations, and publish atomically."""
    base = Path(base_dir)
    data_dir = base / "data"
    gens_dir = data_dir / "generations"
    articles_dir = base / "articles"
    manifest_path = data_dir / "current_generation.json"

    has_candidates = isinstance(state, dict) and state.get("signal_candidates") is not None
    blocked_names = private_article_filenames(articles or [])
    if isinstance(state, dict):
        blocked_names.update(private_article_filenames(state.get("articles") or []))
    clean_state = project_public_state(
        state, include_candidates=has_candidates, excluded_article_names=blocked_names
    )
    norm_articles = _normalize_articles([
        a for a in (articles or []) if not has_private_article_content(a)
        and (not isinstance(a, dict) or safe_article_filename(a.get("filename")) not in blocked_names)
    ])
    article_meta = [
        {"id": a["id"], "filename": a["filename"], "bytes": a["bytes"], "sha256": a["sha256"]}
        for a in norm_articles
    ]

    with _PUBLISH_LOCK, file_lock(manifest_path):
        cur_manifest = _current_manifest(manifest_path, recover_newer=True)
        cur_gen = cur_manifest["generation"] if cur_manifest is not None else 0

        if generation is not None:
            target_gen = int(generation)
            if target_gen <= 0:
                raise ValueError("invalid_generation")
        else:
            req_gen = int(clean_state.get("generation") or 0)
            target_gen = max(req_gen, cur_gen + 1)
            while (gens_dir / f"gen_{target_gen}").exists():
                target_gen += 1

        generated_at = str(clean_state.get("generated_at_bjt") or _now_bjt_iso())
        generated_stamp = _generation_timestamp(generated_at)
        if cur_manifest is not None:
            cur_stamp = _generation_timestamp(cur_manifest["generated_at"])
            if generated_stamp < cur_stamp:
                if clean_state.get("generated_at_bjt"):
                    raise ValueError("stale_generation")
                generated_at = cur_manifest["generated_at"]

        clean_state["generation"] = target_gen
        state_sha = _canonical_state_sha256(clean_state)

        if cur_manifest is not None:
            if target_gen < cur_gen:
                raise ValueError("stale_generation")
            if target_gen == cur_gen:
                if (
                    cur_manifest.get("state_sha256") == state_sha
                    and sorted(cur_manifest["articles"], key=lambda a: a["filename"])
                    == sorted(article_meta, key=lambda a: a["filename"])
                ):
                    _atomic_write_text(manifest_path, json.dumps(cur_manifest, ensure_ascii=False, indent=2) + "\n")
                    _prune_old_generations(gens_dir, target_gen)
                    out = dict(cur_manifest)
                    out["idempotent"] = True
                    return out
                raise ValueError("generation_conflict")

        manifest: dict[str, Any] = {
            "schema_version": 2,
            "generation": target_gen,
            "generated_at": generated_at,
            "published_at": _now_bjt_iso(),
            "producer_id": str(producer_id or "windows-primary"),
            "state_sha256": state_sha,
            "articles": article_meta,
            "coverage": clean_state.get("coverage") or {},
        }
        stage_dir = gens_dir / f".staging_gen_{target_gen}_{os.getpid()}_{time.time_ns()}"
        stage_articles = stage_dir / "articles"
        stage_articles.mkdir(parents=True)
        final_gen_dir = gens_dir / f"gen_{target_gen}"
        manifest_text = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
        state_text = json.dumps(clean_state, ensure_ascii=False, indent=2) + "\n"
        try:
            (stage_dir / "state.json").write_text(state_text, encoding="utf-8")
            (stage_dir / "manifest.json").write_text(manifest_text, encoding="utf-8")
            for art in norm_articles:
                (stage_articles / art["filename"]).write_text(art["content"], encoding="utf-8", newline="")
            if final_gen_dir.exists():
                raise ValueError("generation_conflict")
            os.replace(stage_dir, final_gen_dir)
            _atomic_write_text(manifest_path, manifest_text)
        finally:
            if stage_dir.exists():
                shutil.rmtree(stage_dir, ignore_errors=True)

        # Keep compatibility copies synchronized after switching the generation manifest
        compat_state = data_dir / "cockpit_state.json"
        with file_lock(compat_state):
            _atomic_write_text(compat_state, state_text)
        articles_dir.mkdir(parents=True, exist_ok=True)
        for art in norm_articles:
            _atomic_write_text(articles_dir / art["filename"], art["content"])

        _prune_old_generations(gens_dir, target_gen, keep=KEEP_GENERATIONS)
        return manifest


def load_published_generation(base_dir: Path | str) -> dict[str, Any]:
    """Load the active published state strictly via current_generation.json without aggregation."""
    base = Path(base_dir)
    data_dir = base / "data"
    manifest_path = data_dir / "current_generation.json"
    manifest = _current_manifest(manifest_path)
    if manifest is not None:
        gen = manifest["generation"]
        raw_state = _read_generation_state(data_dir / "generations" / f"gen_{gen}", gen)
        state = project_public_state(raw_state, include_candidates="signal_candidates" in raw_state)
        return {"generation": gen, "manifest": manifest, "state": state}

    legacy_state_path = data_dir / "cockpit_state.json"
    if not legacy_state_path.is_file():
        raise FileNotFoundError(f"No published state found in {data_dir}")
    raw_legacy = json.loads(legacy_state_path.read_text(encoding="utf-8"))
    has_candidates = isinstance(raw_legacy, dict) and raw_legacy.get("signal_candidates") is not None
    state = project_public_state(raw_legacy, include_candidates=has_candidates)
    return {"generation": int(state.get("generation") or 0), "manifest": None, "state": state}


def lookup_published_article(base_dir: Path | str, filename: str) -> dict[str, Any] | None:
    """Resolve an article markdown file strictly against current_generation manifest."""
    safe_name = safe_article_filename(filename)
    if not safe_name:
        raise ValueError(f"invalid_article_filename: {filename!r}")

    base = Path(base_dir)
    data_dir = base / "data"
    manifest_path = data_dir / "current_generation.json"
    manifest = _current_manifest(manifest_path)
    if manifest is not None:
        gen = manifest["generation"]
        entries = manifest["articles"]
        matched = next((e for e in entries if isinstance(e, dict) and e.get("filename") == safe_name), None)
        if matched is None:
            return None
        gen_articles = (data_dir / "generations" / f"gen_{gen}" / "articles").resolve()
        candidate = (gen_articles / safe_name).resolve()
        if candidate.parent != gen_articles or not candidate.is_file():
            return None
        content = candidate.read_text(encoding="utf-8", errors="replace")
        if has_private_article_content({"filename": safe_name, "content": content}):
            return None
        return {"filename": safe_name, "generation": gen, "meta": matched, "content": content}

    legacy_dir = (base / "articles").resolve()
    candidate = (legacy_dir / safe_name).resolve()
    if candidate.parent != legacy_dir or not candidate.is_file():
        return None
    raw_bytes = candidate.read_bytes()
    content = raw_bytes.decode("utf-8", errors="replace")
    if has_private_article_content({"filename": safe_name, "content": content}):
        return None
    return {
        "filename": safe_name,
        "generation": 0,
        "meta": {
            "id": candidate.stem,
            "filename": safe_name,
            "bytes": len(raw_bytes),
            "sha256": hashlib.sha256(raw_bytes).hexdigest(),
        },
        "content": content,
    }
