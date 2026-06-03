"""Detached background jobs for long Claude reviews.

This server drives a one-shot ``claude -p --output-format json`` call, so a job's
terminal output is a single JSON envelope written to ``result.json`` — completion
is "the process exited and the envelope is present", with NO interactive-log or
TUI scraping. That makes background mode far simpler and more robust here than in
a harness that tails an interactive CLI.

State lives on disk (keyed by workspace), so status/result/cancel keep working
across MCP server restarts. There is no daemon: reaping (deadline-kill of
overrunning jobs, TTL cleanup, count cap) happens lazily on each lifecycle call,
and ``--max-budget-usd`` still hard-bounds spend even for a job nobody polls.
"""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import uuid4

from cc_plugin_codex.normalize import apply_cost_usage, normalize_envelope
from cc_plugin_codex.schemas import ContextSummary, Meta

STATE_ENV = "CC_PLUGIN_CODEX_STATE_DIR"
TTL_ENV = "CC_PLUGIN_CODEX_JOB_TTL"
MAX_SECONDS_ENV = "CC_PLUGIN_CODEX_JOB_MAX_SECONDS"
MAX_COUNT_ENV = "CC_PLUGIN_CODEX_JOB_MAX_COUNT"

DEFAULT_TTL = 86_400          # delete terminal job records after 24h
DEFAULT_MAX_SECONDS = 1_800   # wall-clock cap; a poll past this reaps the job
DEFAULT_MAX_COUNT = 50        # retained jobs per workspace; evict oldest terminal

_TERMINAL = {"done", "failed", "cancelled", "timeout", "expired"}


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, ""))
    except (TypeError, ValueError):
        return default


def max_seconds() -> int:
    return _int_env(MAX_SECONDS_ENV, DEFAULT_MAX_SECONDS)


def _state_root() -> Path:
    root = os.environ.get(STATE_ENV)
    if root:
        return Path(root)
    return Path(os.path.expanduser("~")) / ".cache" / "cc-plugin-codex" / "jobs"


def _ws_dir(cwd: str) -> Path:
    canonical = os.path.realpath(cwd)
    digest = hashlib.sha256(canonical.encode()).hexdigest()[:12]
    base = os.path.basename(canonical.rstrip("/")) or "workspace"
    safe = "".join(c if (c.isalnum() or c in "._-") else "-" for c in base)[:40] or "ws"
    return _state_root() / f"{safe}-{digest}"


def _job_dir(cwd: str, job_id: str) -> Path:
    return _ws_dir(cwd) / job_id


def _pid_alive(pid: Optional[int]) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _is_running(pid: Optional[int]) -> bool:
    """Whether the job process is still running.

    The job is launched detached but is still our child until it exits, so we
    must reap it with waitpid — otherwise it lingers as a zombie that kill(0)
    reports as 'alive' forever. waitpid(WNOHANG) returns (pid, _) once it exits
    (reaping it), (0, 0) while it runs, and raises ChildProcessError if it is not
    our child (e.g. after a server restart), where we fall back to a kill(0)
    liveness probe."""
    if not pid:
        return False
    try:
        reaped, _ = os.waitpid(pid, os.WNOHANG)
        if reaped == pid:
            return False
        if reaped == 0:
            return True
    except ChildProcessError:
        pass  # not our child — use the liveness probe below
    except OSError:
        return False
    return _pid_alive(pid)


def _kill_pid_tree(pid: Optional[int]) -> None:
    """Kill the detached job's process group (it is its own session leader), then
    reap it if it was our child so it does not linger as a zombie."""
    if not pid:
        return
    try:
        if hasattr(os, "killpg"):
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        else:  # pragma: no cover - non-POSIX fallback
            os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        os.waitpid(pid, 0)
    except (ChildProcessError, OSError):
        pass


def _read_meta(jd: Path) -> Optional[dict]:
    try:
        return json.loads((jd / "meta.json").read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _write_meta(jd: Path, meta: dict) -> None:
    (jd / "meta.json").write_text(json.dumps(meta))


def _read_envelope(jd: Path) -> Optional[dict]:
    """Parse the claude JSON envelope from result.json, or None if absent/partial."""
    try:
        text = (jd / "result.json").read_text()
    except OSError:
        return None
    text = text.strip()
    if not text:
        return None
    try:
        env = json.loads(text)
    except json.JSONDecodeError:
        return None
    return env if isinstance(env, dict) else None


@dataclass
class JobConfig:
    kind: str
    config_mode: str
    access: str
    scope: Optional[str]
    base: Optional[str]
    detail: str
    timeout_seconds: int
    workspace_source: Optional[str]
    context_summary: Optional[ContextSummary]


def start_job(cmd: list[str], cwd: str, cfg: JobConfig) -> tuple[str, str]:
    """Spawn the claude command detached and persist its record.

    Returns (job_id, started_at_iso)."""
    job_id = uuid4().hex
    jd = _job_dir(cwd, job_id)
    jd.mkdir(parents=True, exist_ok=True)
    # Best-effort: results contain the diff; keep the workspace tree user-only.
    try:
        os.chmod(_ws_dir(cwd), 0o700)
    except OSError:
        pass
    started = time.time()
    result_path = jd / "result.json"
    stderr_path = jd / "stderr.log"
    with open(result_path, "w") as rf, open(stderr_path, "w") as ef:
        proc = subprocess.Popen(cmd, cwd=cwd, stdout=rf, stderr=ef, text=True,
                                start_new_session=True)
    summary = cfg.context_summary.model_dump() if cfg.context_summary else None
    meta = {
        "job_id": job_id, "kind": cfg.kind, "pid": proc.pid,
        "started_epoch": started,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "deadline_epoch": started + max_seconds(),
        "completed_epoch": None,
        "terminal_status": None,   # set by cancel/deadline reap
        "config": {
            "config_mode": cfg.config_mode, "access": cfg.access,
            "scope": cfg.scope, "base": cfg.base, "detail": cfg.detail,
            "timeout_seconds": cfg.timeout_seconds,
            "workspace_source": cfg.workspace_source, "cwd": cwd,
        },
        "context_summary": summary,
    }
    _write_meta(jd, meta)
    _enforce_count_cap(cwd)
    return job_id, meta["started_at"]


def _status_of(jd: Path, meta: dict) -> str:
    """Compute the live status, killing + marking jobs that overran their deadline."""
    terminal = meta.get("terminal_status")
    if terminal:
        return terminal
    if _is_running(meta.get("pid")):
        if time.time() > meta.get("deadline_epoch", float("inf")):
            _kill_pid_tree(meta.get("pid"))
            meta["terminal_status"] = "timeout"
            meta["completed_epoch"] = time.time()
            _write_meta(jd, meta)
            return "timeout"
        return "running"
    # Process gone: done if it left a parseable envelope, else it crashed.
    if meta.get("completed_epoch") is None:
        meta["completed_epoch"] = time.time()
        _write_meta(jd, meta)
    return "done" if _read_envelope(jd) is not None else "failed"


def _elapsed_ms(meta: dict) -> int:
    end = meta.get("completed_epoch") or time.time()
    return max(0, int((end - meta.get("started_epoch", end)) * 1000))


def _reap_workspace(cwd: str) -> None:
    """Lazy maintenance: refresh statuses and delete expired terminal records."""
    ws = _ws_dir(cwd)
    if not ws.is_dir():
        return
    ttl = _int_env(TTL_ENV, DEFAULT_TTL)
    now = time.time()
    for jd in ws.iterdir():
        if not jd.is_dir():
            continue
        meta = _read_meta(jd)
        if meta is None:
            continue
        status = _status_of(jd, meta)
        if status in _TERMINAL:
            end = meta.get("completed_epoch") or meta.get("started_epoch") or now
            if now - end > ttl:
                _rmtree(jd)


def _enforce_count_cap(cwd: str) -> None:
    ws = _ws_dir(cwd)
    cap = _int_env(MAX_COUNT_ENV, DEFAULT_MAX_COUNT)
    dirs = [d for d in ws.iterdir() if d.is_dir()] if ws.is_dir() else []
    if len(dirs) <= cap:
        return
    # Evict oldest terminal jobs first; never kill a still-running one to fit.
    scored = []
    for jd in dirs:
        meta = _read_meta(jd) or {}
        status = _status_of(jd, meta)
        scored.append((status in _TERMINAL, meta.get("started_epoch", 0.0), jd))
    scored.sort(key=lambda t: (not t[0], t[1]))  # terminal first, then oldest
    for is_terminal, _epoch, jd in scored[: max(0, len(dirs) - cap)]:
        if is_terminal:
            _rmtree(jd)


def _rmtree(jd: Path) -> None:
    try:
        for child in jd.iterdir():
            child.unlink(missing_ok=True)
        jd.rmdir()
    except OSError:
        pass


def _build_meta(meta: dict, status: str) -> Meta:
    c = meta.get("config", {})
    return Meta(
        cwd=c.get("cwd", ""),
        workspace_source=c.get("workspace_source"),
        config_mode=c.get("config_mode", "inherit"),
        access=c.get("access", "toolless"),
        scope=c.get("scope"), base=c.get("base"),
        timeout_seconds=c.get("timeout_seconds", max_seconds()),
        elapsed_ms=_elapsed_ms(meta),
        job_id=meta.get("job_id"),
    )


def status(cwd: str, job_id: str) -> Optional[dict]:
    """Return a JobStatus dict, or None if the job does not exist."""
    _reap_workspace(cwd)
    jd = _job_dir(cwd, job_id)
    meta = _read_meta(jd)
    if meta is None:
        return None
    state = _status_of(jd, meta)
    cost = None
    if state == "done":
        env = _read_envelope(jd) or {}
        c = env.get("total_cost_usd")
        cost = float(c) if isinstance(c, (int, float)) else None
    detail = None
    if state == "failed":
        detail = _stderr_tail(jd)
    return {
        "ok": True, "job_id": job_id, "kind": meta.get("kind", ""),
        "status": state, "started_at": meta.get("started_at", ""),
        "elapsed_ms": _elapsed_ms(meta),
        "deadline_seconds": max_seconds(),
        "result_available": state == "done",
        "cost_usd": cost, "detail": detail,
    }


def _stderr_tail(jd: Path, limit: int = 200) -> Optional[str]:
    try:
        text = (jd / "stderr.log").read_text().strip()
    except OSError:
        return None
    return text[-limit:] or None


def result(cwd: str, job_id: str, consume: bool = False):
    """Return (payload, found). payload is the normalized SuccessResult|ErrorResult
    dict; found is False when no such job exists."""
    _reap_workspace(cwd)
    jd = _job_dir(cwd, job_id)
    meta = _read_meta(jd)
    if meta is None:
        return None, False
    state = _status_of(jd, meta)
    if state == "done":
        env_text = (jd / "result.json").read_text()
        summary = meta.get("context_summary")
        ctx_summary = ContextSummary(**summary) if summary else None
        payload = normalize_envelope(
            meta.get("kind", "claude_review_changes"), env_text,
            _build_meta(meta, state), detail=meta.get("config", {}).get("detail", "summary"),
            context_summary=ctx_summary,
        )
        if consume:
            _rmtree(jd)
        return payload, True
    # Non-done states map to an error envelope so the contract stays ok-discriminated.
    payload = _job_error(meta, state, jd)
    return payload, True


_STATE_TO_ERROR = {
    "running": ("job_running", "The job is still running.",
                "Poll claude_job_status; call claude_job_result once status=done."),
    "cancelled": ("job_cancelled", "The job was cancelled.",
                  "Start a new job; a cancelled run cannot be resumed."),
    "timeout": ("job_timeout", "The job exceeded its wall-clock deadline and was stopped.",
                "Narrow the scope or raise CC_PLUGIN_CODEX_JOB_MAX_SECONDS, then start a new job."),
    "expired": ("job_failed", "The job record expired and was cleaned up.",
                "Start a new job."),
}


def _job_error(meta: dict, state: str, jd: Path) -> dict:
    from cc_plugin_codex.schemas import ErrorInfo, ErrorResult
    if state == "failed":
        code, message, repair = (
            "job_failed",
            f"The job failed without producing a result. {_stderr_tail(jd) or ''}".strip(),
            "Run claude_status to check the CLI is installed and authenticated, then retry.",
        )
        retryable = True
    else:
        code, message, repair = _STATE_TO_ERROR.get(
            state, ("job_failed", "The job did not complete.", "Start a new job."))
        retryable = state == "running"
    bmeta = _build_meta(meta, state)
    # Surface any spend the (possibly partial) envelope recorded.
    env = _read_envelope(jd)
    if env:
        apply_cost_usage(bmeta, env)
    return ErrorResult(
        error=ErrorInfo(code=code, message=message, repair=repair, retryable=retryable),
        meta=bmeta,
    ).model_dump(mode="json", exclude_none=True)


def cancel(cwd: str, job_id: str) -> Optional[dict]:
    """Kill a running job and mark it cancelled. Returns a JobStatus dict or None."""
    _reap_workspace(cwd)
    jd = _job_dir(cwd, job_id)
    meta = _read_meta(jd)
    if meta is None:
        return None
    state = _status_of(jd, meta)
    if state not in _TERMINAL:
        _kill_pid_tree(meta.get("pid"))
        meta["terminal_status"] = "cancelled"
        meta["completed_epoch"] = time.time()
        _write_meta(jd, meta)
    return status(cwd, job_id)
