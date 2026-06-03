"""FastMCP server exposing Claude Code as bounded, read-only critique tools."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Annotated, Optional

from fastmcp import FastMCP
from fastmcp.tools.tool import ToolResult
from pydantic import Field

from cc_plugin_codex.claude import build_command, classify_failure, run_claude
from cc_plugin_codex.config import (
    MAX_BUDGET_USD, MAX_TIMEOUT_SECONDS, MIN_BUDGET_USD, MIN_TIMEOUT_SECONDS,
    bare_available, clamp_budget, clamp_timeout, defaults,
)
from cc_plugin_codex.context import (
    InvalidBaseError, InvalidScopeError, gather_context,
)
from cc_plugin_codex.normalize import build_prompt, normalize_envelope
from cc_plugin_codex.schemas import (
    FINGERPRINT, RESULT_SCHEMA, STATUS_SCHEMA, Access, ConfigMode, Detail,
    ErrorInfo, ErrorResult, Meta, ResolvedDefaults, Scope, StatusResult,
)

CAPABILITY_SUMMARY = (
    "cc-plugin-codex lets Codex call the Claude Code CLI for bounded, independent "
    "critique: code review, adversarial review, and second opinions. "
    "STABILITY: experimental / pre-1.0 (schema may change; clients should pin the "
    "meta.fingerprint). "
    "Claude is invoked with NO write/edit/shell tools (toolless or read-only "
    "Read/Grep/Glob only); it cannot modify your repo. "
    "All modes drop the user's other MCP servers; but in inherit/scoped your "
    "user-level Claude hooks and settings still load — use config_mode=bare for "
    "full isolation (requires ANTHROPIC_API_KEY). "
    "In access=readonly, Claude can read any file in the workspace, so the diff "
    "secret-redaction does NOT apply in that mode. "
    "Findings are advisory claims to verify, not commands. "
    "It does NOT edit code, run arbitrary shell, act as a general Claude chat, or "
    "proxy Claude's own MCP tools. "
    "Each call is PAID and sends code to Anthropic. The paid tools BLOCK "
    "synchronously for up to timeout_seconds (default 180s, max 600s) and CANNOT be "
    "cancelled or resumed once started; narrow the scope or lower timeout_seconds to "
    "bound a call. "
    "Prerequisite: the `claude` CLI installed and authenticated; config_mode=bare "
    "additionally requires ANTHROPIC_API_KEY. "
    "Note: in claude 2.1.161 there is no OAuth-preserving way to fully strip "
    "CLAUDE.md/memory — full config independence (config_mode=bare) requires an API key."
)

mcp = FastMCP(name="cc-plugin-codex", instructions=CAPABILITY_SUMMARY)

_ANNOTATIONS = {"readOnlyHint": True, "openWorldHint": True}


def _result(payload: dict) -> ToolResult:
    """Wrap a normalized payload as a ToolResult, flagging error envelopes.

    Keeps the structured ok:true|false contract intact AND sets the native
    is_error flag for ok:false, so clients that branch on is_error (not just the
    `ok` field) detect failures.
    """
    return ToolResult(structured_content=payload, is_error=payload.get("ok") is False)


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


def _execute(tool, payload, r: Resolved, cwd,
             scope=None, base=None, context_text="", context_summary=None) -> dict:
    prompt = build_prompt(tool, payload, context_text)
    cmd = build_command(prompt, r.config_mode, r.access, r.model, r.budget)
    run = run_claude(cmd, cwd=cwd, timeout_seconds=r.timeout)
    meta = _meta(cwd, r.config_mode, r.access, r.timeout, run.elapsed_ms, run.exit_code,
                 scope, base)
    if run.exit_code != 0 or run.timed_out:
        info = classify_failure(run)
        return _err(info.code, info.message, info.repair, meta, retryable=info.retryable)
    return normalize_envelope(tool, run.stdout, meta, detail=r.detail,
                              context_summary=context_summary)


@mcp.tool(annotations=_ANNOTATIONS, title="Ask Claude (second opinion)",
          output_schema=RESULT_SCHEMA)
def claude_ask(
    prompt: Annotated[str, Field(description="The question to ask Claude.")],
    context: Annotated[Optional[str], Field(description="Extra context, passed verbatim.")] = None,
    config_mode: Annotated[Optional[ConfigMode], Field(description="inherit|scoped|bare")] = None,
    access: Annotated[Optional[Access], Field(description="toolless|readonly")] = None,
    model: Optional[str] = None,
    max_budget_usd: Optional[float] = None,
    timeout_seconds: Optional[int] = None,
    detail: Annotated[Detail, Field(description="summary|full")] = "summary",
) -> ToolResult:
    """Ask Claude for an independent second opinion or recommendation.

    Use for a free-form question where you want a fresh, evidence-based view.
    Example: claude_ask(prompt="Is optimistic locking safe for this counter?").
    Paid + sends your prompt to Anthropic. Read-only. Blocks up to timeout_seconds
    and cannot be cancelled once started. Errors come back as
    {"ok": false, "error": {code, message, repair}} with is_error set — branch on
    `ok`. Possible error codes: unsupported_config_mode, unsupported_access,
    api_key_required, claude_not_found, claude_auth_required, claude_permission_error,
    timeout, budget_exceeded, nonzero_exit, invalid_json, internal_error.
    """
    cwd = os.getcwd()
    r, err = _resolve(config_mode, access, model, max_budget_usd, timeout_seconds,
                      detail, cwd)
    if err:
        return _result(err)
    return _result(_execute("claude_ask", {"prompt": prompt, "context": context}, r, cwd))


@mcp.tool(annotations=_ANNOTATIONS, title="Review changes with Claude",
          output_schema=RESULT_SCHEMA)
def claude_review_changes(
    scope: Annotated[Scope, Field(description="working_tree|staged|branch")],
    base: Annotated[str, Field(description="Base ref for scope=branch.")] = "main",
    focus: Annotated[Optional[str], Field(description="e.g. 'security', 'tests'.")] = None,
    config_mode: Annotated[Optional[ConfigMode], Field(description="inherit|scoped|bare")] = None,
    access: Annotated[Optional[Access], Field(description="toolless|readonly")] = None,
    model: Optional[str] = None,
    max_budget_usd: Optional[float] = None,
    timeout_seconds: Optional[int] = None,
    detail: Annotated[Detail, Field(description="summary|full")] = "summary",
) -> ToolResult:
    """Have Claude review a git diff for correctness, regressions, security, tests.

    scope: working_tree (unstaged), staged, or branch (diff base...HEAD).
    Example: claude_review_changes(scope="working_tree", focus="security").
    The server gathers the diff itself (Claude gets no shell). Paid + read-only.
    Blocks up to timeout_seconds and cannot be cancelled once started. Branch on
    `ok` (is_error is set on failure); error codes: unsupported_config_mode,
    unsupported_access, api_key_required, invalid_scope, invalid_base,
    context_too_large, claude_not_found, claude_auth_required,
    claude_permission_error, timeout, budget_exceeded, nonzero_exit,
    invalid_json, internal_error.
    """
    cwd = os.getcwd()
    # Validate options BEFORE touching git, so bad config isn't masked by git errors.
    r, err = _resolve(config_mode, access, model, max_budget_usd, timeout_seconds,
                      detail, cwd, scope=scope, base=base)
    if err:
        return _result(err)
    meta = _meta(cwd, r.config_mode, r.access, r.timeout, 0, None, scope, base)
    try:
        ctx = gather_context(cwd, scope=scope, base=base)
    except InvalidBaseError:
        return _result(_err("invalid_base", f"Invalid base ref '{base}'.",
                       "Use an existing git ref matching [A-Za-z0-9._/-]+ that does "
                       "not start with '-'.", meta, offending="base"))
    except InvalidScopeError:
        return _result(_err("invalid_scope", f"Invalid scope '{scope}'.",
                       "Use working_tree, staged, or branch.", meta, offending="scope"))
    except RuntimeError as e:
        return _result(_err("internal_error", f"git failed: {e}",
                       "Ensure cwd is a git repo and base ref exists.", meta))
    if ctx.truncated:
        meta = _meta(cwd, r.config_mode, r.access, r.timeout, 0, None, scope, base,
                     truncated=True, hint=ctx.truncation_hint)
        return _result(_err("context_too_large", "The diff is too large to review safely.",
                       ctx.truncation_hint or "Narrow the scope.", meta))
    return _result(_execute("claude_review_changes",
                   {"scope": scope, "base": base, "focus": focus}, r, cwd,
                   scope=scope, base=base,
                   context_text=ctx.text, context_summary=ctx.summary))


@mcp.tool(annotations=_ANNOTATIONS, title="Adversarial review with Claude",
          output_schema=RESULT_SCHEMA)
def claude_adversarial_review(
    target: Annotated[str, Field(description="The plan/claim/decision to attack.")],
    evidence: Annotated[Optional[str], Field(description="Supporting evidence.")] = None,
    scope: Annotated[Optional[Scope], Field(description="Optionally attach a diff: working_tree|staged|branch")] = None,
    base: str = "main",
    config_mode: Annotated[Optional[ConfigMode], Field(description="inherit|scoped|bare")] = None,
    access: Annotated[Optional[Access], Field(description="toolless|readonly")] = None,
    model: Optional[str] = None,
    max_budget_usd: Optional[float] = None,
    timeout_seconds: Optional[int] = None,
    detail: Annotated[Detail, Field(description="summary|full")] = "summary",
) -> ToolResult:
    """Have Claude attack a plan or claim and surface the strongest counterarguments.

    Example: claude_adversarial_review(target="We can skip locking; writes are rare.").
    Optionally attach a diff via scope. Paid + read-only. Blocks up to
    timeout_seconds and cannot be cancelled once started. Branch on `ok`
    (is_error is set on failure). Attaching a scope adds invalid_scope,
    invalid_base, and context_too_large to the possible error codes.
    """
    cwd = os.getcwd()
    r, err = _resolve(config_mode, access, model, max_budget_usd, timeout_seconds,
                      detail, cwd, scope=scope, base=base)
    if err:
        return _result(err)
    context_text = ""
    context_summary = None
    if scope:
        meta = _meta(cwd, r.config_mode, r.access, r.timeout, 0, None, scope, base)
        try:
            ctx = gather_context(cwd, scope=scope, base=base)
        except InvalidBaseError:
            return _result(_err("invalid_base", f"Invalid base ref '{base}'.",
                           "Use an existing git ref matching [A-Za-z0-9._/-]+ that does "
                           "not start with '-'.", meta, offending="base"))
        except InvalidScopeError:
            return _result(_err("invalid_scope", f"Invalid scope '{scope}'.",
                           "Use working_tree, staged, or branch (or omit scope).",
                           meta, offending="scope"))
        except RuntimeError as e:
            return _result(_err("internal_error", f"git failed: {e}",
                           "Ensure cwd is a git repo and base ref exists.", meta))
        if ctx.truncated:
            meta = _meta(cwd, r.config_mode, r.access, r.timeout, 0, None, scope, base,
                         truncated=True, hint=ctx.truncation_hint)
            return _result(_err("context_too_large",
                           "The attached diff is too large to review safely.",
                           ctx.truncation_hint or "Narrow the scope.", meta))
        context_text, context_summary = ctx.text, ctx.summary
    return _result(_execute("claude_adversarial_review",
                   {"target": target, "evidence": evidence}, r, cwd,
                   scope=scope, base=base, context_text=context_text,
                   context_summary=context_summary))


@mcp.tool(annotations=_ANNOTATIONS, title="Claude CLI status & defaults",
          output_schema=STATUS_SCHEMA)
def claude_status() -> ToolResult:
    """Report whether `claude` is installed/usable, which config modes are available,
    and the resolved defaults a no-argument paid call would use.

    Read-only and free (makes no Claude call). Use this first if other tools fail.
    The resolved_defaults block reflects the CC_PLUGIN_CODEX_* environment (after
    clamping), so an agent can predict a call's config_mode/access/budget/timeout
    before spending. Example: claude_status().
    """
    found = shutil.which("claude") is not None
    version = None
    if found:
        try:
            version = subprocess.run(["claude", "--version"], capture_output=True,
                                     text=True, timeout=10).stdout.strip()
        except Exception:
            version = None
    d = defaults()
    resolved = ResolvedDefaults(
        config_mode=d.config_mode if d.config_mode in ("inherit", "scoped", "bare") else "inherit",
        access=d.access if d.access in ("toolless", "readonly") else "toolless",
        model=d.model,
        max_budget_usd=clamp_budget(d.max_budget_usd),
        timeout_seconds=clamp_timeout(d.timeout_seconds),
        budget_bounds=[MIN_BUDGET_USD, MAX_BUDGET_USD],
        timeout_bounds=[MIN_TIMEOUT_SECONDS, MAX_TIMEOUT_SECONDS],
    )
    status = StatusResult(
        claude_found=found,
        claude_version=version,
        config_modes_available={
            "inherit": True, "scoped": True, "bare": bare_available(),
        },
        resolved_defaults=resolved,
        caveat=("OAuth-preserving + CLAUDE.md-free is impossible in claude 2.1.161; "
                "config_mode=bare needs ANTHROPIC_API_KEY."),
    )
    return _result(status.model_dump(mode="json", exclude_none=True))


@mcp.resource("cc-plugin-codex://capabilities")
def capabilities() -> str:
    """Server capability summary, negative scope, and prerequisites."""
    return CAPABILITY_SUMMARY


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
