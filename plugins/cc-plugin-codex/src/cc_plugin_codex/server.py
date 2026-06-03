"""FastMCP server exposing Claude Code as bounded, read-only critique tools."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Optional

from fastmcp import FastMCP
from pydantic import Field

from cc_plugin_codex.claude import build_command, classify_failure, run_claude
from cc_plugin_codex.config import (
    bare_available, clamp_budget, clamp_timeout, defaults,
)
from cc_plugin_codex.context import gather_context
from cc_plugin_codex.normalize import build_prompt, normalize_envelope
from cc_plugin_codex.schemas import FINGERPRINT, ContextSummary, ErrorInfo, ErrorResult, Meta

CAPABILITY_SUMMARY = (
    "cc-plugin-codex lets Codex call the Claude Code CLI for bounded, independent, "
    "READ-ONLY critique: code review, adversarial review, and second opinions. "
    "Findings are advisory claims to verify, not commands. "
    "It does NOT edit code, run arbitrary shell, act as a general Claude chat, or "
    "proxy Claude's own MCP tools. Each call is PAID and sends code to Anthropic. "
    "Prerequisite: the `claude` CLI installed and authenticated; config_mode=bare "
    "additionally requires ANTHROPIC_API_KEY. Note: in claude 2.1.161 there is no "
    "OAuth-preserving way to fully strip CLAUDE.md/memory — full config independence "
    "(config_mode=bare) requires an API key."
)

mcp = FastMCP(name="cc-plugin-codex", instructions=CAPABILITY_SUMMARY)

_ANNOTATIONS = {"readOnlyHint": True, "openWorldHint": True}


def _meta(cwd: str, config_mode: str, access: str, timeout: int, elapsed: int,
          exit_code: int | None, scope: str | None = None, base: str | None = None,
          truncated: bool = False, hint: str | None = None) -> Meta:
    return Meta(cwd=cwd, config_mode=config_mode, access=access, scope=scope, base=base,
                timeout_seconds=timeout, elapsed_ms=elapsed, command_exit_code=exit_code,
                truncated=truncated, truncation_hint=hint, fingerprint=FINGERPRINT)


def _err(code: str, message: str, repair: str, meta: Meta,
         offending: str | None = None, retryable: bool = False) -> dict:
    return ErrorResult(
        error=ErrorInfo(code=code, message=message, repair=repair,
                        offending_param=offending, retryable=retryable),
        meta=meta,
    ).model_dump(mode="json", exclude_none=True)


@mcp.tool(annotations=_ANNOTATIONS)
def claude_status() -> dict:
    """Report whether `claude` is installed/usable and which config modes are available.

    Read-only and free (makes no Claude call). Use this first if other tools fail.
    Example: claude_status().
    """
    found = shutil.which("claude") is not None
    version = None
    if found:
        try:
            version = subprocess.run(["claude", "--version"], capture_output=True,
                                     text=True, timeout=10).stdout.strip()
        except Exception:
            version = None
    return {
        "ok": True,
        "claude_found": found,
        "claude_version": version,
        "config_modes_available": {
            "inherit": True, "scoped": True, "bare": bare_available(),
        },
        "caveat": ("OAuth-preserving + CLAUDE.md-free is impossible in claude 2.1.161; "
                   "config_mode=bare needs ANTHROPIC_API_KEY."),
        "fingerprint": FINGERPRINT,
    }


@mcp.resource("cc-plugin-codex://capabilities")
def capabilities() -> str:
    """Server capability summary, negative scope, and prerequisites."""
    return CAPABILITY_SUMMARY


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
