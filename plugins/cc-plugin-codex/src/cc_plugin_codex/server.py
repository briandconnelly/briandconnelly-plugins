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


@dataclass
class Resolved:
    config_mode: str
    access: str
    model: Optional[str]
    budget: float
    timeout: int
    detail: str


def _resolve(config_mode, access, model, max_budget_usd, timeout_seconds, detail,
             cwd, scope=None, base=None):
    """Resolve env defaults + clamps and validate. Returns (Resolved, None) or (None, error_dict)."""
    d = defaults()
    cm = config_mode or d.config_mode
    ac = access or d.access
    mdl = model or d.model
    budget = clamp_budget(max_budget_usd if max_budget_usd is not None else d.max_budget_usd)
    timeout = clamp_timeout(timeout_seconds if timeout_seconds is not None else d.timeout_seconds)
    det = detail if detail in ("summary", "full") else "summary"

    # Validate before building Meta (Meta uses Literal types — invalid values
    # would raise Pydantic errors before we can return a structured response).
    if cm not in ("inherit", "scoped", "bare"):
        safe_meta = _meta(cwd, "inherit", ac if ac in ("toolless", "readonly") else "toolless",
                          timeout, 0, None, scope, base)
        return None, _err("unsupported_config_mode", f"Unknown config_mode '{cm}'.",
                          "Use one of: inherit, scoped, bare.", safe_meta,
                          offending="config_mode")
    if ac not in ("toolless", "readonly"):
        safe_meta = _meta(cwd, cm, "toolless", timeout, 0, None, scope, base)
        return None, _err("unsupported_access", f"Unknown access '{ac}'.",
                          "Use one of: toolless, readonly.", safe_meta, offending="access")

    meta = _meta(cwd, cm, ac, timeout, 0, None, scope, base)
    if cm == "bare" and not bare_available():
        return None, _err("api_key_required",
                          "config_mode=bare requires ANTHROPIC_API_KEY, which is unset.",
                          "Set ANTHROPIC_API_KEY, or use config_mode inherit/scoped.",
                          meta, offending="config_mode")
    return Resolved(cm, ac, mdl, budget, timeout, det), None


def _execute(tool, payload, r: Resolved, cwd, resume_session,
             scope=None, base=None, context_text="", context_summary=None) -> dict:
    prompt = build_prompt(tool, payload, context_text)
    cmd = build_command(prompt, r.config_mode, r.access, r.model, r.budget, resume_session)
    run = run_claude(cmd, cwd=cwd, timeout_seconds=r.timeout)
    meta = _meta(cwd, r.config_mode, r.access, r.timeout, run.elapsed_ms, run.exit_code,
                 scope, base)
    if run.exit_code != 0 or run.timed_out:
        info = classify_failure(run)
        return _err(info.code, info.message, info.repair, meta, retryable=info.retryable)
    return normalize_envelope(tool, run.stdout, meta, detail=r.detail,
                              context_summary=context_summary)


@mcp.tool(annotations=_ANNOTATIONS)
def claude_ask(
    prompt: Annotated[str, Field(description="The question to ask Claude.")],
    context: Annotated[Optional[str], Field(description="Extra context, passed verbatim.")] = None,
    config_mode: Annotated[Optional[str], Field(description="inherit|scoped|bare")] = None,
    access: Annotated[Optional[str], Field(description="toolless|readonly")] = None,
    model: Optional[str] = None,
    max_budget_usd: Optional[float] = None,
    timeout_seconds: Optional[int] = None,
    detail: Annotated[str, Field(description="summary|full")] = "summary",
    resume_session: Optional[str] = None,
) -> dict:
    """Ask Claude for an independent second opinion or recommendation.

    Use for a free-form question where you want a fresh, evidence-based view.
    Example: claude_ask(prompt="Is optimistic locking safe for this counter?").
    Paid + sends your prompt to Anthropic. Read-only. Errors come back as
    {"ok": false, "error": {code, message, repair}} — branch on `ok`.
    """
    cwd = os.getcwd()
    r, err = _resolve(config_mode, access, model, max_budget_usd, timeout_seconds,
                      detail, cwd)
    if err:
        return err
    return _execute("claude_ask", {"prompt": prompt, "context": context}, r, cwd,
                    resume_session)


@mcp.tool(annotations=_ANNOTATIONS)
def claude_review_changes(
    scope: Annotated[str, Field(description="working_tree|staged|branch")],
    base: Annotated[str, Field(description="Base ref for scope=branch.")] = "main",
    focus: Annotated[Optional[str], Field(description="e.g. 'security', 'tests'.")] = None,
    config_mode: Annotated[Optional[str], Field(description="inherit|scoped|bare")] = None,
    access: Annotated[Optional[str], Field(description="toolless|readonly")] = None,
    model: Optional[str] = None,
    max_budget_usd: Optional[float] = None,
    timeout_seconds: Optional[int] = None,
    detail: Annotated[str, Field(description="summary|full")] = "summary",
    resume_session: Optional[str] = None,
) -> dict:
    """Have Claude review a git diff for correctness, regressions, security, tests.

    scope: working_tree (unstaged), staged, or branch (diff base...HEAD).
    Example: claude_review_changes(scope="working_tree", focus="security").
    The server gathers the diff itself (Claude gets no shell). Paid + read-only.
    Branch on `ok`; errors include code in {unsupported_config_mode, unsupported_access,
    api_key_required, invalid_scope, context_too_large, timeout, ...}.
    """
    cwd = os.getcwd()
    # Validate options BEFORE touching git, so bad config isn't masked by git errors.
    r, err = _resolve(config_mode, access, model, max_budget_usd, timeout_seconds,
                      detail, cwd, scope=scope, base=base)
    if err:
        return err
    meta = _meta(cwd, r.config_mode, r.access, r.timeout, 0, None, scope, base)
    try:
        ctx = gather_context(cwd, scope=scope, base=base)
    except ValueError:
        return _err("invalid_scope", f"Invalid scope '{scope}'.",
                    "Use working_tree, staged, or branch.", meta, offending="scope")
    except RuntimeError as e:
        return _err("internal_error", f"git failed: {e}",
                    "Ensure cwd is a git repo and base ref exists.", meta)
    if ctx.truncated:
        meta = _meta(cwd, r.config_mode, r.access, r.timeout, 0, None, scope, base,
                     truncated=True, hint=ctx.truncation_hint)
        return _err("context_too_large", "The diff is too large to review safely.",
                    ctx.truncation_hint or "Narrow the scope.", meta)
    return _execute("claude_review_changes",
                    {"scope": scope, "base": base, "focus": focus}, r, cwd,
                    resume_session, scope=scope, base=base,
                    context_text=ctx.text, context_summary=ctx.summary)


@mcp.tool(annotations=_ANNOTATIONS)
def claude_adversarial_review(
    target: Annotated[str, Field(description="The plan/claim/decision to attack.")],
    evidence: Annotated[Optional[str], Field(description="Supporting evidence.")] = None,
    scope: Annotated[Optional[str], Field(description="Optionally attach a diff: working_tree|staged|branch")] = None,
    base: str = "main",
    config_mode: Annotated[Optional[str], Field(description="inherit|scoped|bare")] = None,
    access: Annotated[Optional[str], Field(description="toolless|readonly")] = None,
    model: Optional[str] = None,
    max_budget_usd: Optional[float] = None,
    timeout_seconds: Optional[int] = None,
    detail: Annotated[str, Field(description="summary|full")] = "summary",
    resume_session: Optional[str] = None,
) -> dict:
    """Have Claude attack a plan or claim and surface the strongest counterarguments.

    Example: claude_adversarial_review(target="We can skip locking; writes are rare.").
    Optionally attach a diff via scope. Paid + read-only. Branch on `ok`.
    """
    cwd = os.getcwd()
    r, err = _resolve(config_mode, access, model, max_budget_usd, timeout_seconds,
                      detail, cwd, scope=scope, base=base)
    if err:
        return err
    context_text = ""
    context_summary = None
    if scope:
        meta = _meta(cwd, r.config_mode, r.access, r.timeout, 0, None, scope, base)
        try:
            ctx = gather_context(cwd, scope=scope, base=base)
        except ValueError:
            return _err("invalid_scope", f"Invalid scope '{scope}'.",
                        "Use working_tree, staged, or branch (or omit scope).",
                        meta, offending="scope")
        except RuntimeError as e:
            return _err("internal_error", f"git failed: {e}",
                        "Ensure cwd is a git repo and base ref exists.", meta)
        context_text, context_summary = ctx.text, ctx.summary
    return _execute("claude_adversarial_review",
                    {"target": target, "evidence": evidence}, r, cwd, resume_session,
                    scope=scope, base=base, context_text=context_text,
                    context_summary=context_summary)


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
