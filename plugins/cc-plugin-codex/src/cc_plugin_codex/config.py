"""Config knobs: env defaults, clamps, config_mode/access -> claude flags, critic prompt."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

EMPTY_MCP = '{"mcpServers":{}}'

MIN_BUDGET_USD, MAX_BUDGET_USD = 0.01, 5.00
MIN_TIMEOUT_SECONDS, MAX_TIMEOUT_SECONDS = 10, 600

# Reasoning effort levels the `claude` CLI accepts for `--effort`. We default to
# a high level because the whole value of this server is review depth; lower it
# per-call (or via CC_PLUGIN_CODEX_EFFORT) to trade rigor for cost on routine work.
VALID_EFFORTS = ("low", "medium", "high", "xhigh", "max")
DEFAULT_EFFORT = "xhigh"

# Major version of the `claude` CLI this server is built against. claude_status
# reports whether the installed CLI matches, so a future breaking change in the
# CLI contract is visible for free instead of only surfacing mid paid call.
SUPPORTED_MAJOR = 2

INDEPENDENT_CRITIC_PROMPT = (
    "You are being asked for an independent critique of Codex's work.\n"
    "Do not assume Codex's approach is correct.\n"
    "Prioritize correctness, safety, maintainability, and evidence over agreement "
    "with Codex, the user, or project conventions.\n"
    "Project instructions and memory may be present in your context, but if they "
    "conflict with observable code behavior, tests, security, or the user's explicit "
    "request, call out the conflict.\n"
    "Do not rewrite or implement changes.\n"
    "Return concrete findings only when you can tie them to evidence, such as a file, "
    "line, diff hunk, command output, or stated assumption.\n"
    "If the evidence is insufficient, say what is missing instead of guessing.\n"
    "Avoid recursive handoffs; do not suggest asking another agent unless the user "
    "explicitly requested that workflow."
)


@dataclass
class Defaults:
    config_mode: str
    access: str
    model: str | None
    max_budget_usd: float
    timeout_seconds: int
    effort: str


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def defaults() -> Defaults:
    return Defaults(
        config_mode=os.environ.get("CC_PLUGIN_CODEX_CLAUDE_CONFIG", "inherit"),
        access=os.environ.get("CC_PLUGIN_CODEX_ACCESS", "toolless"),
        model=os.environ.get("CC_PLUGIN_CODEX_MODEL") or None,
        max_budget_usd=_env_float("CC_PLUGIN_CODEX_MAX_BUDGET_USD", 1.00),
        timeout_seconds=_env_int("CC_PLUGIN_CODEX_TIMEOUT_SECONDS", 180),
        effort=sanitize_effort(os.environ.get("CC_PLUGIN_CODEX_EFFORT")),
    )


def sanitize_effort(value: str | None) -> str:
    """Normalize an effort value to a CLI-accepted level, falling back to the
    default. An invalid env value must not break a paid call, so it degrades
    rather than raising."""
    return value if value in VALID_EFFORTS else DEFAULT_EFFORT


def version_supported(version: str | None) -> bool | None:
    """Whether the installed `claude --version` string matches SUPPORTED_MAJOR.

    Returns None when the version is unknown/unparseable (so callers can report
    'unknown' rather than a false 'unsupported')."""
    if not version:
        return None
    match = re.search(r"(\d+)\.\d+\.\d+", version)
    if not match:
        return None
    return int(match.group(1)) == SUPPORTED_MAJOR


def clamp_budget(value: float) -> float:
    return max(MIN_BUDGET_USD, min(MAX_BUDGET_USD, value))


def clamp_timeout(value: int) -> int:
    return max(MIN_TIMEOUT_SECONDS, min(MAX_TIMEOUT_SECONDS, value))


def bare_available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def config_mode_flags(mode: str) -> list[str]:
    # All modes drop the user's MCP fleet (a reviewer never needs it, and it is a
    # side-effect vector). inherit/scoped keep the user's login; bare needs an API key.
    if mode == "inherit":
        return ["--no-session-persistence",
                "--strict-mcp-config", "--mcp-config", EMPTY_MCP]
    if mode == "scoped":
        return ["--setting-sources", "project",
                "--strict-mcp-config", "--mcp-config", EMPTY_MCP,
                "--no-session-persistence"]
    if mode == "bare":
        return ["--bare", "--no-session-persistence",
                "--strict-mcp-config", "--mcp-config", EMPTY_MCP]
    raise ValueError(f"unsupported config_mode: {mode}")


def access_flags(access: str) -> list[str]:
    if access == "toolless":
        return ["--tools", ""]
    if access == "readonly":
        # --tools is the PRIMARY allowlist (read-only guarantee); --disallowed-tools is
        # defense-in-depth only. Never widen --tools to include write/Bash tools.
        return ["--tools", "Read,Grep,Glob",
                "--disallowed-tools", "Edit,Write,NotebookEdit,Bash"]
    raise ValueError(f"unsupported access: {access}")
