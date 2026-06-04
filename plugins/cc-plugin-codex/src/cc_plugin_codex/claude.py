"""Build and run the `claude` CLI invocation; classify failures."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from dataclasses import dataclass

import anyio

from cc_plugin_codex.config import (
    INDEPENDENT_CRITIC_PROMPT, access_flags, config_mode_flags,
)
from cc_plugin_codex.schemas import ErrorInfo


@dataclass
class ClaudeRun:
    stdout: str
    stderr: str
    exit_code: int
    elapsed_ms: int
    timed_out: bool


def build_command(prompt: str, config_mode: str, access: str, model: str | None,
                  max_budget_usd: float, effort: str | None = None) -> list[str]:
    # --no-chrome disables the "Claude in Chrome" integration, which could
    # otherwise open an interactive picker that hangs an unattended run until the
    # timeout (burning the whole timeout and the spend) instead of answering.
    cmd = ["claude", "-p", "--output-format", "json", "--no-chrome"]
    cmd += config_mode_flags(config_mode)
    cmd += access_flags(access)
    cmd += ["--append-system-prompt", INDEPENDENT_CRITIC_PROMPT]
    cmd += ["--max-budget-usd", f"{max_budget_usd}"]
    if effort:
        cmd += ["--effort", effort]
    if model:
        cmd += ["--model", model]
    cmd += ["--", prompt]  # end-of-options separator, then the prompt as positional
    return cmd


def auth_status(timeout_seconds: int = 10) -> tuple[bool | None, str | None]:
    """Probe `claude auth status` without making a paid call.

    Returns (logged_in, detail). logged_in is None when the probe could not run
    (claude missing, timeout) so callers can report 'unknown' rather than a
    misleading False. detail is a NON-identifying phrase, never the raw CLI output:
    `claude auth status` prints the account email and organization, which would leak
    into shared logs/transcripts. The boolean already carries the machine-readable
    truth, so we deliberately drop the raw text."""
    try:
        proc = subprocess.run(["claude", "auth", "status", "--text"],
                              capture_output=True, text=True, timeout=timeout_seconds)
    except (OSError, subprocess.SubprocessError):
        return None, None
    logged_in = proc.returncode == 0
    detail = ("Claude CLI reports an authenticated session." if logged_in
              else "Claude CLI reports no authenticated session; run `claude /login`.")
    return logged_in, detail


def _kill_process_tree(proc: subprocess.Popen) -> None:
    """Best-effort terminate the process and its children. POSIX: kill the
    process group (the child is its own session leader). Falls back to killing
    just the process where process groups are unavailable (e.g. Windows)."""
    if proc.poll() is not None:
        return
    try:
        if hasattr(os, "killpg"):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        else:  # pragma: no cover - non-POSIX fallback
            proc.kill()
    except (ProcessLookupError, PermissionError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


async def run_claude_async(cmd: list[str], cwd: str, timeout_seconds: int) -> ClaudeRun:
    """Run `claude` as a subprocess, returning a ClaudeRun.

    The subprocess is started in its own session (process group) so that, on a
    timeout OR an MCP request cancellation, we can terminate the whole tree
    rather than orphaning a paid Claude run."""
    start = time.monotonic()
    try:
        proc = subprocess.Popen(
            cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, start_new_session=True,
        )
    except OSError:
        elapsed = int((time.monotonic() - start) * 1000)
        return ClaudeRun("", "claude_not_found", 127, elapsed, False)

    def _wait() -> tuple[str, str, bool]:
        try:
            out, err = proc.communicate(timeout=timeout_seconds)
            return out, err, False
        except subprocess.TimeoutExpired:
            _kill_process_tree(proc)
            out, err = proc.communicate()
            return out, err, True

    try:
        out, err, timed_out = await anyio.to_thread.run_sync(
            _wait, abandon_on_cancel=True)
    except anyio.get_cancelled_exc_class():
        _kill_process_tree(proc)
        raise
    elapsed = int((time.monotonic() - start) * 1000)
    if timed_out:
        return ClaudeRun("", "timeout", -9, elapsed, True)
    return ClaudeRun(out, err, proc.returncode, elapsed, False)


def classify_failure(run: ClaudeRun) -> ErrorInfo:
    extra = ""
    try:
        env = json.loads(run.stdout)
        extra = f"{env.get('subtype', '')} {env.get('result', '')}"
    except (json.JSONDecodeError, ValueError, TypeError):
        pass
    blob = f"{extra}\n{run.stdout}\n{run.stderr}".lower()
    if run.stderr == "claude_not_found":
        return ErrorInfo(code="claude_not_found",
                         message="The `claude` CLI was not found on PATH.",
                         repair="Install Claude Code and ensure `claude` is on PATH.")
    if run.timed_out:
        return ErrorInfo(code="timeout", message="claude exceeded the timeout.",
                         repair="Narrow the scope/focus or raise timeout_seconds.",
                         retryable=True)
    if "invalid api key" in blob:
        return ErrorInfo(code="api_key_invalid",
                         message="ANTHROPIC_API_KEY is invalid.",
                         repair="Set a valid ANTHROPIC_API_KEY, or use config_mode "
                                "inherit/scoped to use your existing login.")
    if "not logged in" in blob or "/login" in blob:
        return ErrorInfo(code="claude_auth_required",
                         message="claude is not authenticated.",
                         repair="Run `claude /login`.")
    if "budget" in blob:
        return ErrorInfo(code="budget_exceeded",
                         message="claude reached the max-budget stop threshold "
                                 "(a best-effort limit, not a hard cap).",
                         repair="Raise max_budget_usd or reduce context.",
                         retryable=True)
    return ErrorInfo(code="nonzero_exit",
                     message=f"claude exited {run.exit_code}: {run.stderr.strip()[:200]}",
                     repair="Inspect the error; retry with a smaller request.")
