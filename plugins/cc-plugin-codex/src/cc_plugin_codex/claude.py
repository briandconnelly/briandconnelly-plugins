"""Build and run the `claude` CLI invocation; classify failures."""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass

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
                  max_budget_usd: float) -> list[str]:
    cmd = ["claude", "-p", "--output-format", "json"]
    cmd += config_mode_flags(config_mode)
    cmd += access_flags(access)
    cmd += ["--append-system-prompt", INDEPENDENT_CRITIC_PROMPT]
    cmd += ["--max-budget-usd", f"{max_budget_usd}"]
    if model:
        cmd += ["--model", model]
    cmd += ["--", prompt]  # end-of-options separator, then the prompt as positional
    return cmd


def run_claude(cmd: list[str], cwd: str, timeout_seconds: int) -> ClaudeRun:
    start = time.monotonic()
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                              timeout=timeout_seconds)
    except FileNotFoundError:
        elapsed = int((time.monotonic() - start) * 1000)
        return ClaudeRun("", "claude_not_found", 127, elapsed, False)
    except subprocess.TimeoutExpired:
        elapsed = int((time.monotonic() - start) * 1000)
        return ClaudeRun("", "timeout", -9, elapsed, True)
    elapsed = int((time.monotonic() - start) * 1000)
    return ClaudeRun(proc.stdout, proc.stderr, proc.returncode, elapsed, False)


def classify_failure(run: ClaudeRun) -> ErrorInfo:
    blob = f"{run.stdout}\n{run.stderr}".lower()
    if run.stderr == "claude_not_found":
        return ErrorInfo(code="claude_not_found",
                         message="The `claude` CLI was not found on PATH.",
                         repair="Install Claude Code and ensure `claude` is on PATH.")
    if run.timed_out:
        return ErrorInfo(code="timeout", message="claude exceeded the timeout.",
                         repair="Narrow the scope/focus or raise timeout_seconds.",
                         retryable=True)
    if "invalid api key" in blob:
        return ErrorInfo(code="api_key_required",
                         message="ANTHROPIC_API_KEY is invalid.",
                         repair="Set a valid ANTHROPIC_API_KEY, or use config_mode "
                                "inherit/scoped to use your existing login.")
    if "not logged in" in blob or "/login" in blob:
        return ErrorInfo(code="claude_auth_required",
                         message="claude is not authenticated.",
                         repair="Run `claude /login`.")
    if "budget" in blob:
        return ErrorInfo(code="budget_exceeded",
                         message="claude hit the max-budget cap.",
                         repair="Raise max_budget_usd or reduce context.",
                         retryable=True)
    return ErrorInfo(code="nonzero_exit",
                     message=f"claude exited {run.exit_code}: {run.stderr.strip()[:200]}",
                     repair="Inspect the error; retry with a smaller request.")
