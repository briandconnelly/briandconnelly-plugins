"""Config knobs: env defaults, clamps, config_mode/access -> claude flags, critic prompt."""

from __future__ import annotations

import os
from dataclasses import dataclass

EMPTY_MCP = '{"mcpServers":{}}'

MIN_BUDGET_USD, MAX_BUDGET_USD = 0.01, 5.00
MIN_TIMEOUT_SECONDS, MAX_TIMEOUT_SECONDS = 10, 600

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
    )


def clamp_budget(value: float) -> float:
    return max(MIN_BUDGET_USD, min(MAX_BUDGET_USD, value))


def clamp_timeout(value: int) -> int:
    return max(MIN_TIMEOUT_SECONDS, min(MAX_TIMEOUT_SECONDS, value))


def bare_available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def config_mode_flags(mode: str, resume: bool) -> list[str]:
    if mode == "inherit":
        return [] if resume else ["--no-session-persistence"]
    if mode == "scoped":
        flags = ["--setting-sources", "project",
                 "--strict-mcp-config", "--mcp-config", EMPTY_MCP]
        if not resume:
            flags.append("--no-session-persistence")
        return flags
    if mode == "bare":
        return ["--bare", "--strict-mcp-config", "--mcp-config", EMPTY_MCP]
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
