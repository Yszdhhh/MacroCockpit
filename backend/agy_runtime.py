"""AGY ACP (stdio JSON-RPC) and CLI bridge for local multi-agent reasoning."""
from __future__ import annotations

import json
import os
import queue
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

from backend.runtime import COCKPIT_ROOT, now_bjt_iso as _now_iso, resolve_user_home



AGY_DEFAULT_EXE = Path(os.environ.get("AGY_HOME") or (resolve_user_home() / "AppData" / "Local" / "agy")) / "bin" / "agy.exe"


def resolve_agy_executable() -> Path | None:
    env_bin = (os.environ.get("MACROCOCKPIT_AGY_BIN") or "").strip()
    if env_bin:
        p = Path(env_bin)
        if p.is_file():
            return p
    if AGY_DEFAULT_EXE.is_file():
        return AGY_DEFAULT_EXE
    found = shutil.which("agy") or shutil.which("agy.exe")
    return Path(found) if found else None


def parse_mcp_list_output(raw_text: str) -> list[dict[str, str]]:
    servers: list[dict[str, str]] = []
    for raw_ln in (raw_text or "").splitlines():
        ln = raw_ln.strip()
        if not ln:
            continue
        parts = ln.split()
        if len(parts) >= 3 and parts[0].upper() == "NAME" and parts[1].upper() == "TYPE":
            continue
        if len(parts) >= 4:
            servers.append(
                {
                    "name": parts[0],
                    "type": parts[1],
                    "status": parts[2],
                    "target": " ".join(parts[3:]),
                }
            )
        elif len(parts) == 3:
            servers.append(
                {
                    "name": parts[0],
                    "type": parts[1],
                    "status": parts[2],
                    "target": "",
                }
            )
    return servers


def probe_agy(*, timeout: int | float = 6) -> dict[str, Any]:
    """Probe local AGY binary availability, version, ACP support, and configured MCP servers."""
    exe = resolve_agy_executable()
    if exe is None:
        return {
            "available": False,
            "binary": "",
            "version": None,
            "acp_supported": False,
            "acp_ready": False,
            "mcp_servers": [],
            "checked_at": _now_iso(),
        }

    version: str | None = None
    try:
        r_ver = subprocess.run(
            [str(exe), "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        if r_ver.returncode == 0:
            lines = [ln.strip() for ln in r_ver.stdout.splitlines() if ln.strip()]
            version = lines[0] if lines else None
    except Exception:
        version = None

    mcp_servers: list[dict[str, str]] = []
    try:
        r_mcp = subprocess.run(
            [str(exe), "mcp", "list"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        if r_mcp.returncode == 0:
            mcp_servers = parse_mcp_list_output(r_mcp.stdout)
    except Exception:
        mcp_servers = []

    return {
        "available": True,
        "binary": str(exe),
        "version": version,
        "acp_supported": True,
        "acp_ready": True,
        "mcp_servers": mcp_servers,
        "checked_at": _now_iso(),
    }


def _extract_chunk_text(upd: dict[str, Any]) -> str:
    content = upd.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        if isinstance(content.get("text"), str):
            return content["text"]
        if isinstance(content.get("content"), str):
            return content["content"]
    if isinstance(content, list):
        parts: list[str] = []
        for blk in content:
            if isinstance(blk, dict) and isinstance(blk.get("text"), str):
                parts.append(blk["text"])
            elif isinstance(blk, str):
                parts.append(blk)
        return "".join(parts)
    if isinstance(upd.get("text"), str):
        return upd["text"]
    return ""


def _read_until_id(
    proc: subprocess.Popen[str],
    out_q: queue.Queue[str | None],
    target_id: int,
    deadline: float,
    send: Callable[[dict[str, Any]], None],
    chunks: list[str] | None = None,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    while True:
        if cancel_event is not None and cancel_event.is_set():
            raise TimeoutError("AGY ACP cancelled")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"AGY ACP timeout waiting for response id={target_id}")
        try:
            line = out_q.get(timeout=min(remaining, 0.25))
        except queue.Empty:
            if cancel_event is not None and cancel_event.is_set():
                raise TimeoutError("AGY ACP cancelled")
            if proc.poll() is not None and out_q.empty():
                raise RuntimeError(f"AGY ACP exited unexpectedly (code={proc.returncode})")
            continue

        if line is None:
            raise RuntimeError(f"AGY ACP stdout closed unexpectedly (code={proc.poll()})")

        raw = line.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(msg, dict):
            continue

        method = msg.get("method")
        if method == "session/update" and chunks is not None:
            params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
            upd = params.get("update") if isinstance(params.get("update"), dict) else params
            if upd.get("sessionUpdate") == "agent_message_chunk":
                txt = _extract_chunk_text(upd)
                if txt:
                    chunks.append(txt)
            continue

        # Safely decline interactive permission prompts so headless debate cannot hang
        if method == "session/request_permission" and msg.get("id") is not None:
            send({
                "jsonrpc": "2.0",
                "id": msg["id"],
                "result": {"outcome": {"outcome": "cancelled"}},
            })
            continue

        if msg.get("id") == target_id:
            if "error" in msg and msg["error"]:
                err = msg["error"]
                detail = err.get("message") if isinstance(err, dict) else str(err)
                raise RuntimeError(f"AGY ACP error for id={target_id}: {detail}")
            return msg


def _close_agy(proc: subprocess.Popen[str]) -> None:
    try:
        if os.name == "nt" and proc.poll() is None:
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True,
                    timeout=2,
                    check=True,
                )
            except (OSError, subprocess.SubprocessError):
                proc.kill()
        elif os.name != "nt":
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    finally:
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass


def ask_agy(
    prompt: str,
    *,
    transport: str = "acp",
    timeout: int | float = 60,
    cwd: Path | str = COCKPIT_ROOT,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    """Send a prompt to local AGY via ACP stdio NDJSON (default) or CLI --print fallback."""
    clean_prompt = str(prompt or "").strip()
    if not clean_prompt:
        raise ValueError("prompt cannot be empty")
    tr = str(transport or "acp").strip().lower()
    if tr not in ("acp", "cli"):
        raise ValueError(f"unsupported AGY transport: {transport!r}")
    if cancel_event is not None and cancel_event.is_set():
        return {"ok": False, "transport": tr, "reply": "", "error": "cancelled", "elapsed_ms": 0}

    exe = resolve_agy_executable()
    if exe is None:
        return {"ok": False, "transport": tr, "reply": "", "error": "agy_not_found", "elapsed_ms": 0}

    # Keep cwd in the API while isolating every model session.
    with tempfile.TemporaryDirectory(prefix="macrocockpit-agy-") as work_dir:
        return _ask_agy(exe.resolve(), clean_prompt, tr, float(timeout), work_dir, cancel_event=cancel_event)


def _ask_agy(
    exe: Path,
    clean_prompt: str,
    tr: str,
    timeout: float,
    work_dir: str,
    *,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    t0 = time.monotonic()
    deadline = t0 + max(0.0, timeout)
    proc: subprocess.Popen[str] | None = None
    if tr == "cli":
        try:
            proc = subprocess.Popen(
                [
                    str(exe),
                    "--print",
                    clean_prompt,
                    "--output-format",
                    "text",
                    "--add-dir",
                    str(work_dir),
                ],
                cwd=work_dir,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                start_new_session=os.name != "nt",
            )
            out_chunks: list[str] = []
            err_chunks: list[str] = []

            def _drain_stream(stream: Any, sink: list[str]) -> None:
                if stream is None:
                    return
                try:
                    for chunk in iter(lambda: stream.read(4096), ""):
                        sink.append(chunk)
                except (OSError, ValueError):
                    pass

            t_out = threading.Thread(target=_drain_stream, args=(proc.stdout, out_chunks), daemon=True)
            t_err = threading.Thread(target=_drain_stream, args=(proc.stderr, err_chunks), daemon=True)
            t_out.start()
            t_err.start()

            while proc.poll() is None:
                if cancel_event is not None and cancel_event.is_set():
                    _close_agy(proc)
                    return {
                        "ok": False,
                        "transport": "cli",
                        "reply": "",
                        "error": "cancelled",
                        "elapsed_ms": int((time.monotonic() - t0) * 1000),
                    }
                if time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(cmd="agy", timeout=timeout)
                time.sleep(0.05)
            try:
                proc.wait(timeout=max(0.2, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                raise subprocess.TimeoutExpired(cmd="agy", timeout=timeout)
            t_out.join(timeout=1.5)
            t_err.join(timeout=1.5)
            stdout = "".join(out_chunks)
            stderr = "".join(err_chunks)
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            if proc.returncode != 0:
                return {
                    "ok": False,
                    "transport": "cli",
                    "reply": stdout.strip(),
                    "error": (stderr or f"exit_code_{proc.returncode}").strip()[:300],
                    "elapsed_ms": elapsed_ms,
                }
            return {
                "ok": True,
                "transport": "cli",
                "reply": stdout.strip(),
                "elapsed_ms": elapsed_ms,
            }
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "transport": "cli",
                "reply": "",
                "error": "timeout",
                "elapsed_ms": int((time.monotonic() - t0) * 1000),
            }
        except Exception as exc:
            return {
                "ok": False,
                "transport": "cli",
                "reply": "",
                "error": f"{type(exc).__name__}: {exc}",
                "elapsed_ms": int((time.monotonic() - t0) * 1000),
            }

        finally:
            if proc is not None:
                _close_agy(proc)

    # ACP NDJSON stdio protocol
    stderr_tail = ""
    requests_q: queue.Queue[str | None] = queue.Queue()
    try:
        proc = subprocess.Popen(
            [str(exe), "acp", "--stdio", "--add-dir", str(work_dir)],
            cwd=str(work_dir),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            start_new_session=os.name != "nt",
        )
        stdin, stdout, stderr = proc.stdin, proc.stdout, proc.stderr
        assert stdin is not None and stdout is not None and stderr is not None
        out_q: queue.Queue[str | None] = queue.Queue()
        write_results: queue.Queue[Exception | None] = queue.Queue()

        def _reader() -> None:
            try:
                for ln in stdout:
                    out_q.put(ln)
            except (OSError, ValueError):
                pass
            finally:
                out_q.put(None)

        def _drain_stderr() -> None:
            nonlocal stderr_tail
            try:
                while chunk := stderr.read(4096):
                    stderr_tail = (stderr_tail + chunk)[-4096:]
            except (OSError, ValueError):
                pass

        def _writer() -> None:
            while (line := requests_q.get()) is not None:
                try:
                    stdin.write(line)
                    stdin.flush()
                except (OSError, ValueError) as exc:
                    write_results.put(exc)
                    return
                write_results.put(None)

        for worker in (_reader, _drain_stderr, _writer):
            threading.Thread(target=worker, daemon=True).start()

        def _send(req: dict[str, Any]) -> None:
            requests_q.put(json.dumps(req, ensure_ascii=False) + "\n")
            try:
                result = write_results.get(timeout=max(0.0, deadline - time.monotonic()))
            except queue.Empty:
                raise TimeoutError("AGY ACP timeout writing request") from None
            if result is not None:
                raise result

        _send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": 1, "clientCapabilities": {}},
            }
        )
        _read_until_id(proc, out_q, 1, deadline, _send, cancel_event=cancel_event)

        _send(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "authenticate",
                "params": {"methodId": "oauth-personal"},
            }
        )
        _read_until_id(proc, out_q, 2, deadline, _send, cancel_event=cancel_event)

        _send(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "session/new",
                "params": {"cwd": str(work_dir), "mcpServers": []},
            }
        )
        sess_resp = _read_until_id(proc, out_q, 3, deadline, _send, cancel_event=cancel_event)
        session_id = str((sess_resp.get("result") or {}).get("sessionId") or "")
        if not session_id:
            raise RuntimeError("AGY ACP session/new returned empty sessionId")

        chunks: list[str] = []
        _send(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "session/prompt",
                "params": {
                    "sessionId": session_id,
                    "prompt": [{"type": "text", "text": clean_prompt}],
                },
            }
        )
        prompt_resp = _read_until_id(proc, out_q, 4, deadline, _send, chunks=chunks, cancel_event=cancel_event)
        reply = "".join(chunks).strip()
        if not reply:
            res_obj = prompt_resp.get("result") or {}
            if isinstance(res_obj.get("text"), str):
                reply = res_obj["text"].strip()
        return {
            "ok": True,
            "transport": "acp",
            "session_id": session_id,
            "reply": reply,
            "elapsed_ms": int((time.monotonic() - t0) * 1000),
        }
    except Exception as exc:
        return {
            "ok": False,
            "transport": "acp",
            "reply": "",
            "error": f"{type(exc).__name__}: {exc}",
            "stderr_tail": stderr_tail.strip()[-300:],
            "elapsed_ms": int((time.monotonic() - t0) * 1000),
        }
    finally:
        requests_q.put(None)
        if proc is not None:
            _close_agy(proc)


class AgyBridge:
    """Convenience wrapper around local AGY probe and ACP/CLI prompt transport."""

    def __init__(self, cwd: Path | str = COCKPIT_ROOT) -> None:
        self.cwd = Path(cwd)

    def probe(self, *, timeout: int | float = 6) -> dict[str, Any]:
        return probe_agy(timeout=timeout)

    def ask_agy(
        self,
        prompt: str,
        *,
        transport: str = "acp",
        timeout: int | float = 60,
    ) -> dict[str, Any]:
        return ask_agy(prompt, transport=transport, timeout=timeout, cwd=self.cwd)
