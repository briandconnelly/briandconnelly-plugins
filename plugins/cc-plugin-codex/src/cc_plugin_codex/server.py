"""FastMCP server exposing Claude Code as bounded, read-only critique tools."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Annotated, Optional
from urllib.parse import unquote, urlparse

import anyio
from fastmcp import Context, FastMCP
from fastmcp.tools.tool import ToolResult
from pydantic import Field

from cc_plugin_codex.claude import (
    auth_status, build_command, classify_failure, run_claude_async,
)
from cc_plugin_codex.config import (
    MAX_BUDGET_USD, MAX_TIMEOUT_SECONDS, MIN_BUDGET_USD, MIN_TIMEOUT_SECONDS,
    VALID_EFFORTS, bare_available, clamp_budget, clamp_timeout, defaults,
    sanitize_effort, version_supported,
)
from cc_plugin_codex.context import (
    InvalidBaseError, InvalidScopeError, gather_context,
)
from cc_plugin_codex import jobs
from cc_plugin_codex.jobs import JobConfig
from cc_plugin_codex.normalize import apply_cost_usage, build_prompt, normalize_envelope
from cc_plugin_codex.schemas import (
    CAPABILITIES_SCHEMA, FINGERPRINT, JOB_STARTED_SCHEMA, JOB_STATUS_SCHEMA,
    RESULT_SCHEMA, STATUS_SCHEMA,
    Access, CapabilitiesResult, ConfigMode, Detail, Effort,
    ErrorInfo, ErrorResult, JobStarted, Meta, ResolvedDefaults, Scope, StatusResult,
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
    "synchronously for up to timeout_seconds (default 180s, max 600s), but CAN be "
    "cancelled by the client (which terminates the underlying Claude process) and "
    "cannot be resumed; narrow the scope or lower timeout_seconds to bound a call. "
    "Prerequisite: the `claude` CLI installed and authenticated; config_mode=bare "
    "additionally requires ANTHROPIC_API_KEY. "
    "Note: in claude 2.1.x there is no OAuth-preserving way to fully strip "
    "CLAUDE.md/memory — full config independence (config_mode=bare) requires an API key. "
    "Invalid enum-typed arguments are rejected as schema validation errors before "
    "a tool runs (not via the ok:false envelope). "
)

mcp = FastMCP(name="cc-plugin-codex", instructions=CAPABILITY_SUMMARY)

# Paid tools read code but are NOT idempotent (each call spends money and re-invokes
# Claude) and are explicitly non-destructive (no writes/shell). openWorld: they reach
# an external service (Anthropic).
_PAID_ANNOTATIONS = {
    "readOnlyHint": True, "openWorldHint": True,
    "destructiveHint": False, "idempotentHint": False,
}
# claude_status is free, read-only, and safely repeatable.
_STATUS_ANNOTATIONS = {
    "readOnlyHint": True, "openWorldHint": False,
    "destructiveHint": False, "idempotentHint": True,
}


def _result(payload: dict) -> ToolResult:
    """Wrap a normalized payload as a ToolResult, flagging error envelopes.

    Keeps the structured ok:true|false contract intact AND sets the native
    is_error flag for ok:false, so clients that branch on is_error (not just the
    `ok` field) detect failures.
    """
    return ToolResult(structured_content=payload, is_error=payload.get("ok") is False)


def _meta(cwd: str, config_mode: str, access: str, timeout: int, elapsed: int,
          exit_code: int | None, scope: str | None = None, base: str | None = None,
          truncated: bool = False, hint: str | None = None,
          workspace_source: str | None = None) -> Meta:
    return Meta(cwd=cwd, config_mode=config_mode, access=access, scope=scope, base=base,
                timeout_seconds=timeout, elapsed_ms=elapsed, command_exit_code=exit_code,
                truncated=truncated, truncation_hint=hint, fingerprint=FINGERPRINT,
                workspace_source=workspace_source)


def _err(code: str, message: str, repair: str, meta: Meta,
         offending: str | None = None, retryable: bool = False) -> dict:
    return ErrorResult(
        error=ErrorInfo(code=code, message=message, repair=repair,
                        offending_param=offending, retryable=retryable),
        meta=meta,
    ).model_dump(mode="json", exclude_none=True)


async def _first_root(ctx) -> str | None:
    """Return the filesystem path of the client's first file:// root, or None.

    Returns None if the client provides no roots or does not support the roots
    capability (list_roots raises)."""
    if ctx is None:
        return None
    try:
        roots = await ctx.list_roots()
    except Exception:
        return None
    for root in roots or []:
        uri = str(getattr(root, "uri", ""))
        if uri.startswith("file://"):
            return unquote(urlparse(uri).path)
    return None


async def _resolve_workspace(workspace_root, ctx):
    """Resolve the workspace directory.

    Order: explicit workspace_root arg -> first file:// MCP root -> os.getcwd().
    Returns (path, error_code, source). error_code is None on success; on failure
    path is None and source is None."""
    if workspace_root:
        path, source = workspace_root, "param"
    else:
        root = await _first_root(ctx)
        if root:
            path, source = root, "roots"
        else:
            path, source = os.getcwd(), "cwd"
    # An explicit workspace_root must be absolute: a relative path would be resolved
    # against the very cwd this resolution exists to stop trusting. Roots (file:// URIs)
    # and os.getcwd() are always absolute already.
    if not os.path.isabs(path) or not os.path.isdir(path):
        return None, "invalid_workspace_root", None
    return path, None, source


@dataclass
class Resolved:
    config_mode: str
    access: str
    model: Optional[str]
    budget: float
    timeout: int
    detail: str
    effort: str


def _resolve(config_mode, access, model, max_budget_usd, timeout_seconds, detail,
             cwd, scope=None, base=None, workspace_source=None, effort=None):
    """Resolve env defaults + clamps and validate. Returns (Resolved, None) or (None, error_dict)."""
    d = defaults()
    cm = config_mode or d.config_mode
    ac = access or d.access
    mdl = model or d.model
    budget = clamp_budget(max_budget_usd if max_budget_usd is not None else d.max_budget_usd)
    timeout = clamp_timeout(timeout_seconds if timeout_seconds is not None else d.timeout_seconds)
    det = detail if detail in ("summary", "full") else "summary"
    eff = effort if effort in VALID_EFFORTS else d.effort

    # Validate before building Meta (Meta uses Literal types — invalid values
    # would raise Pydantic errors before we can return a structured response).
    if cm not in ("inherit", "scoped", "bare"):
        safe_meta = _meta(cwd, "inherit", ac if ac in ("toolless", "readonly") else "toolless",
                          timeout, 0, None, scope, base, workspace_source=workspace_source)
        return None, _err("unsupported_config_mode", f"Unknown config_mode '{cm}'.",
                          "Use one of: inherit, scoped, bare.", safe_meta,
                          offending="config_mode")
    if ac not in ("toolless", "readonly"):
        safe_meta = _meta(cwd, cm, "toolless", timeout, 0, None, scope, base,
                          workspace_source=workspace_source)
        return None, _err("unsupported_access", f"Unknown access '{ac}'.",
                          "Use one of: toolless, readonly.", safe_meta, offending="access")

    meta = _meta(cwd, cm, ac, timeout, 0, None, scope, base,
                 workspace_source=workspace_source)
    if cm == "bare" and not bare_available():
        return None, _err("api_key_required",
                          "config_mode=bare requires ANTHROPIC_API_KEY, which is unset.",
                          "Set ANTHROPIC_API_KEY, or use config_mode inherit/scoped.",
                          meta, offending="config_mode")
    return Resolved(cm, ac, mdl, budget, timeout, det, eff), None


async def _execute(tool, payload, r: Resolved, cwd,
                   scope=None, base=None, context_text="", context_summary=None,
                   workspace_source=None) -> dict:
    prompt = build_prompt(tool, payload, context_text)
    cmd = build_command(prompt, r.config_mode, r.access, r.model, r.budget, r.effort)
    run = await run_claude_async(cmd, cwd=cwd, timeout_seconds=r.timeout)
    meta = _meta(cwd, r.config_mode, r.access, r.timeout, run.elapsed_ms, run.exit_code,
                 scope, base, workspace_source=workspace_source)
    if run.exit_code != 0 or run.timed_out:
        # A non-zero exit can still carry a cost-bearing JSON envelope (e.g.
        # budget_exceeded); report what it spent when available.
        try:
            env = json.loads(run.stdout)
        except (json.JSONDecodeError, ValueError, TypeError):
            env = None
        if isinstance(env, dict):
            apply_cost_usage(meta, env)
        info = classify_failure(run)
        return _err(info.code, info.message, info.repair, meta, retryable=info.retryable)
    return normalize_envelope(tool, run.stdout, meta, detail=r.detail,
                              context_summary=context_summary)


@mcp.tool(annotations=_PAID_ANNOTATIONS, title="Ask Claude (second opinion)",
          output_schema=RESULT_SCHEMA)
async def claude_ask(
    prompt: Annotated[str, Field(description="The question to ask Claude.")],
    context: Annotated[Optional[str], Field(description="Extra context, passed verbatim.")] = None,
    workspace_root: Annotated[Optional[str], Field(
        description="Absolute path to the repo/workspace to operate in. If omitted, "
        "the server uses the client's first MCP root, else its own cwd.")] = None,
    config_mode: Annotated[Optional[ConfigMode], Field(description="inherit|scoped|bare")] = None,
    access: Annotated[Optional[Access], Field(description="toolless|readonly")] = None,
    model: Optional[str] = None,
    effort: Annotated[Optional[Effort], Field(
        description="Reasoning effort: low|medium|high|xhigh|max. "
        "Raise for high-stakes reviews; omit to use the server default.")] = None,
    max_budget_usd: Optional[float] = None,
    timeout_seconds: Optional[int] = None,
    detail: Annotated[Detail, Field(description="summary|full")] = "summary",
    ctx: Context = None,
) -> ToolResult:
    """Ask Claude for an independent second opinion or recommendation.

    Use for a free-form question where you want a fresh, evidence-based view.
    Example: claude_ask(prompt="Is optimistic locking safe for this counter?").
    Paid + sends your prompt to Anthropic. Read-only. Blocks up to timeout_seconds;
    can be cancelled by the client (terminates the Claude process), not resumed.
    Invalid values for typed enum params
    (config_mode, access, detail) are rejected by the framework as a schema
    validation error BEFORE the tool runs and do NOT use the ok:false envelope; all
    other failures return ok:false. Errors come back as
    {"ok": false, "error": {code, message, repair}} with is_error set — branch on
    `ok`. Possible error codes: unsupported_config_mode, unsupported_access,
    api_key_required, invalid_workspace_root, claude_not_found, claude_auth_required,
    claude_permission_error, timeout, budget_exceeded, nonzero_exit, invalid_json,
    internal_error.

    workspace_root: absolute path to the repo to operate in; if omitted, the
    server uses the client's first MCP root, else its own cwd (see meta.workspace_source).
    Adds error code: invalid_workspace_root (path missing or not absolute).
    """
    cwd, ws_err, ws_source = await _resolve_workspace(workspace_root, ctx)
    if ws_err:
        meta = _meta("", "inherit", "toolless", 0, 0, None)
        return _result(_err(ws_err,
                       f"workspace_root '{workspace_root}' is not an existing absolute directory.",
                       "Pass workspace_root as an absolute path to an existing directory, or "
                       "configure an MCP root.", meta, offending="workspace_root"))
    r, err = _resolve(config_mode, access, model, max_budget_usd, timeout_seconds,
                      detail, cwd, workspace_source=ws_source, effort=effort)
    if err:
        return _result(err)
    payload = {"prompt": prompt, "context": context}
    out = await _execute("claude_ask", payload, r, cwd, workspace_source=ws_source)
    return _result(out)


@mcp.tool(annotations=_PAID_ANNOTATIONS, title="Review changes with Claude",
          output_schema=RESULT_SCHEMA)
async def claude_review_changes(
    scope: Annotated[Scope, Field(description="working_tree|staged|branch")],
    base: Annotated[str, Field(description="Base ref for scope=branch.")] = "main",
    focus: Annotated[Optional[str], Field(description="e.g. 'security', 'tests'.")] = None,
    workspace_root: Annotated[Optional[str], Field(
        description="Absolute path to the repo/workspace to operate in. If omitted, "
        "the server uses the client's first MCP root, else its own cwd.")] = None,
    config_mode: Annotated[Optional[ConfigMode], Field(description="inherit|scoped|bare")] = None,
    access: Annotated[Optional[Access], Field(description="toolless|readonly")] = None,
    model: Optional[str] = None,
    effort: Annotated[Optional[Effort], Field(
        description="Reasoning effort: low|medium|high|xhigh|max. "
        "Raise for high-stakes reviews; omit to use the server default.")] = None,
    max_budget_usd: Optional[float] = None,
    timeout_seconds: Optional[int] = None,
    detail: Annotated[Detail, Field(description="summary|full")] = "summary",
    ctx: Context = None,
) -> ToolResult:
    """Have Claude review a git diff for correctness, regressions, security, tests.

    scope: working_tree (unstaged), staged, or branch (diff base...HEAD).
    Example: claude_review_changes(scope="working_tree", focus="security").
    The server gathers the diff itself (Claude gets no shell). Paid + read-only.
    Blocks up to timeout_seconds; can be cancelled by the client (terminates the
    Claude process), not resumed. Invalid values
    for typed enum params (config_mode, access, scope, detail) are rejected by the
    framework as a schema validation error BEFORE the tool runs and do NOT use the
    ok:false envelope; all other failures return ok:false. Branch on `ok` (is_error
    is set on failure); error codes: unsupported_config_mode, unsupported_access,
    api_key_required, invalid_workspace_root, invalid_scope, invalid_base,
    context_too_large, claude_not_found, claude_auth_required,
    claude_permission_error, timeout, budget_exceeded, nonzero_exit, invalid_json,
    internal_error.

    workspace_root: absolute path to the repo to operate in; if omitted, the
    server uses the client's first MCP root, else its own cwd (see meta.workspace_source).
    Adds error code: invalid_workspace_root (path missing or not absolute).
    """
    cwd, ws_err, ws_source = await _resolve_workspace(workspace_root, ctx)
    if ws_err:
        meta = _meta("", "inherit", "toolless", 0, 0, None)
        return _result(_err(ws_err,
                       f"workspace_root '{workspace_root}' is not an existing absolute directory.",
                       "Pass workspace_root as an absolute path to an existing directory, or "
                       "configure an MCP root.", meta, offending="workspace_root"))
    # Validate options BEFORE touching git, so bad config isn't masked by git errors.
    r, err = _resolve(config_mode, access, model, max_budget_usd, timeout_seconds,
                      detail, cwd, scope=scope, base=base, workspace_source=ws_source,
                      effort=effort)
    if err:
        return _result(err)
    meta = _meta(cwd, r.config_mode, r.access, r.timeout, 0, None, scope, base,
                 workspace_source=ws_source)
    try:
        ctx_data = await anyio.to_thread.run_sync(
            lambda: gather_context(cwd, scope=scope, base=base))
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
    if ctx_data.truncated:
        meta = _meta(cwd, r.config_mode, r.access, r.timeout, 0, None, scope, base,
                     truncated=True, hint=ctx_data.truncation_hint, workspace_source=ws_source)
        return _result(_err("context_too_large", "The diff is too large to review safely.",
                       ctx_data.truncation_hint or "Narrow the scope.", meta))
    out = await _execute(
        "claude_review_changes", {"scope": scope, "base": base, "focus": focus},
        r, cwd, scope=scope, base=base, context_text=ctx_data.text,
        context_summary=ctx_data.summary, workspace_source=ws_source)
    return _result(out)


@mcp.tool(annotations=_PAID_ANNOTATIONS, title="Adversarial review with Claude",
          output_schema=RESULT_SCHEMA)
async def claude_adversarial_review(
    target: Annotated[str, Field(description="The plan/claim/decision to attack.")],
    evidence: Annotated[Optional[str], Field(description="Supporting evidence.")] = None,
    scope: Annotated[Optional[Scope], Field(description="Optionally attach a diff: working_tree|staged|branch")] = None,
    base: str = "main",
    workspace_root: Annotated[Optional[str], Field(
        description="Absolute path to the repo/workspace to operate in. If omitted, "
        "the server uses the client's first MCP root, else its own cwd.")] = None,
    config_mode: Annotated[Optional[ConfigMode], Field(description="inherit|scoped|bare")] = None,
    access: Annotated[Optional[Access], Field(description="toolless|readonly")] = None,
    model: Optional[str] = None,
    effort: Annotated[Optional[Effort], Field(
        description="Reasoning effort: low|medium|high|xhigh|max. "
        "Raise for high-stakes reviews; omit to use the server default.")] = None,
    max_budget_usd: Optional[float] = None,
    timeout_seconds: Optional[int] = None,
    detail: Annotated[Detail, Field(description="summary|full")] = "summary",
    ctx: Context = None,
) -> ToolResult:
    """Have Claude attack a plan or claim and surface the strongest counterarguments.

    Example: claude_adversarial_review(target="We can skip locking; writes are rare.").
    Optionally attach a diff via scope. Paid + read-only. Blocks up to
    timeout_seconds; can be cancelled by the client (terminates the Claude process),
    not resumed. Invalid values for typed
    enum params (config_mode, access, scope, detail) are rejected by the framework
    as a schema validation error BEFORE the tool runs and do NOT use the ok:false
    envelope; all other failures return ok:false. Branch on `ok` (is_error is set
    on failure). Always possible: invalid_workspace_root. Attaching a scope adds
    invalid_scope, invalid_base, and context_too_large to the possible error codes.

    workspace_root: absolute path to the repo to operate in; if omitted, the
    server uses the client's first MCP root, else its own cwd (see meta.workspace_source).
    Adds error code: invalid_workspace_root (path missing or not absolute).
    """
    cwd, ws_err, ws_source = await _resolve_workspace(workspace_root, ctx)
    if ws_err:
        meta = _meta("", "inherit", "toolless", 0, 0, None)
        return _result(_err(ws_err,
                       f"workspace_root '{workspace_root}' is not an existing absolute directory.",
                       "Pass workspace_root as an absolute path to an existing directory, or "
                       "configure an MCP root.", meta, offending="workspace_root"))
    r, err = _resolve(config_mode, access, model, max_budget_usd, timeout_seconds,
                      detail, cwd, scope=scope, base=base, workspace_source=ws_source,
                      effort=effort)
    if err:
        return _result(err)
    context_text = ""
    context_summary = None
    if scope:
        meta = _meta(cwd, r.config_mode, r.access, r.timeout, 0, None, scope, base,
                     workspace_source=ws_source)
        try:
            ctx_data = await anyio.to_thread.run_sync(
                lambda: gather_context(cwd, scope=scope, base=base))
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
        if ctx_data.truncated:
            meta = _meta(cwd, r.config_mode, r.access, r.timeout, 0, None, scope, base,
                         truncated=True, hint=ctx_data.truncation_hint,
                         workspace_source=ws_source)
            return _result(_err("context_too_large",
                           "The attached diff is too large to review safely.",
                           ctx_data.truncation_hint or "Narrow the scope.", meta))
        context_text, context_summary = ctx_data.text, ctx_data.summary
    out = await _execute(
        "claude_adversarial_review", {"target": target, "evidence": evidence},
        r, cwd, scope=scope, base=base, context_text=context_text,
        context_summary=context_summary, workspace_source=ws_source)
    return _result(out)


# Starting a background job commits to spend (the job runs to completion or its
# budget cap even if never polled), but returns immediately without blocking.
_ASYNC_START_ANNOTATIONS = {
    "readOnlyHint": True, "openWorldHint": True,
    "destructiveHint": False, "idempotentHint": False,
}


@mcp.tool(annotations=_ASYNC_START_ANNOTATIONS, title="Review changes with Claude (background)",
          output_schema=JOB_STARTED_SCHEMA)
async def claude_review_changes_async(
    scope: Annotated[Scope, Field(description="working_tree|staged|branch")],
    base: Annotated[str, Field(description="Base ref for scope=branch.")] = "main",
    focus: Annotated[Optional[str], Field(description="e.g. 'security', 'tests'.")] = None,
    workspace_root: Annotated[Optional[str], Field(
        description="Absolute path to the repo/workspace to operate in. If omitted, "
        "the server uses the client's first MCP root, else its own cwd.")] = None,
    config_mode: Annotated[Optional[ConfigMode], Field(description="inherit|scoped|bare")] = None,
    access: Annotated[Optional[Access], Field(description="toolless|readonly")] = None,
    model: Optional[str] = None,
    effort: Annotated[Optional[Effort], Field(
        description="Reasoning effort: low|medium|high|xhigh|max.")] = None,
    max_budget_usd: Optional[float] = None,
    detail: Annotated[Detail, Field(description="summary|full")] = "summary",
    ctx: Context = None,
) -> ToolResult:
    """Launch a Claude diff review as a BACKGROUND job and return immediately.

    Unlike claude_review_changes (which blocks), this returns a job handle
    {ok, job_id, status:"running", ...} right away; the review keeps running
    detached. Poll claude_job_status(job_id), then claude_job_result(job_id) once
    status=done. Use this for large diffs or when you want to keep working while
    Claude reviews. Paid (commits to spend) + read-only. The diff is gathered now,
    with the same secret redaction and budget cap as the synchronous tool.

    The job is bounded by max_budget_usd and a wall-clock deadline
    (CC_PLUGIN_CODEX_JOB_MAX_SECONDS, default 1800s) enforced on the next status
    poll. timeout_seconds does not apply (there is no blocking call to time out).
    Error codes mirror claude_review_changes for the launch phase (e.g.
    invalid_scope, invalid_base, context_too_large, unsupported_config_mode).
    """
    cwd, ws_err, ws_source = await _resolve_workspace(workspace_root, ctx)
    if ws_err:
        meta = _meta("", "inherit", "toolless", 0, 0, None)
        return _result(_err(ws_err,
                       f"workspace_root '{workspace_root}' is not an existing absolute directory.",
                       "Pass workspace_root as an absolute path to an existing directory, or "
                       "configure an MCP root.", meta, offending="workspace_root"))
    r, err = _resolve(config_mode, access, model, max_budget_usd, None,
                      detail, cwd, scope=scope, base=base, workspace_source=ws_source,
                      effort=effort)
    if err:
        return _result(err)
    meta = _meta(cwd, r.config_mode, r.access, r.timeout, 0, None, scope, base,
                 workspace_source=ws_source)
    try:
        ctx_data = await anyio.to_thread.run_sync(
            lambda: gather_context(cwd, scope=scope, base=base))
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
    if ctx_data.truncated:
        meta = _meta(cwd, r.config_mode, r.access, r.timeout, 0, None, scope, base,
                     truncated=True, hint=ctx_data.truncation_hint, workspace_source=ws_source)
        return _result(_err("context_too_large", "The diff is too large to review safely.",
                       ctx_data.truncation_hint or "Narrow the scope.", meta))
    prompt = build_prompt("claude_review_changes",
                          {"scope": scope, "base": base, "focus": focus}, ctx_data.text)
    cmd = build_command(prompt, r.config_mode, r.access, r.model, r.budget, r.effort)
    cfg = JobConfig(kind="claude_review_changes", config_mode=r.config_mode,
                    access=r.access, scope=scope, base=base, detail=r.detail,
                    timeout_seconds=jobs.max_seconds(), workspace_source=ws_source,
                    context_summary=ctx_data.summary)
    job_id, started_at = await anyio.to_thread.run_sync(
        lambda: jobs.start_job(cmd, cwd, cfg))
    started = JobStarted(
        job_id=job_id, kind="claude_review_changes", started_at=started_at,
        deadline_seconds=jobs.max_seconds(),
        meta=_meta(cwd, r.config_mode, r.access, r.timeout, 0, None, scope, base,
                   workspace_source=ws_source),
    )
    return _result(started.model_dump(mode="json", exclude_none=True))


@mcp.tool(annotations=_STATUS_ANNOTATIONS, title="Background job status",
          output_schema=JOB_STATUS_SCHEMA)
async def claude_job_status(
    job_id: Annotated[str, Field(description="A job_id from an *_async tool.")],
    workspace_root: Annotated[Optional[str], Field(
        description="Workspace the job belongs to (defaults like the async tools).")] = None,
    ctx: Context = None,
) -> ToolResult:
    """Report a background job's lifecycle state (free; no Claude call).

    Returns {ok, job_id, status, elapsed_ms, result_available, cost_usd?} where
    status is running|done|failed|cancelled|timeout|expired. Call
    claude_job_result once result_available is true. A running job past its
    deadline is stopped and reported as timeout here. Returns job_not_found if the
    id is unknown (or its record expired).
    """
    cwd, ws_err, ws_source = await _resolve_workspace(workspace_root, ctx)
    if ws_err:
        meta = _meta("", "inherit", "toolless", 0, 0, None)
        return _result(_err(ws_err, "workspace_root is not an existing absolute directory.",
                       "Pass an absolute path or configure an MCP root.", meta,
                       offending="workspace_root"))
    data = await anyio.to_thread.run_sync(lambda: jobs.status(cwd, job_id))
    if data is None:
        meta = _meta(cwd, "inherit", "toolless", 0, 0, None, workspace_source=ws_source)
        return _result(_err("job_not_found", f"No job '{job_id}' in this workspace.",
                       "Check the job_id, or start a new job; records expire after the TTL.",
                       meta, offending="job_id"))
    return _result(data)


@mcp.tool(annotations=_STATUS_ANNOTATIONS, title="Background job result",
          output_schema=RESULT_SCHEMA)
async def claude_job_result(
    job_id: Annotated[str, Field(description="A job_id from an *_async tool.")],
    consume: Annotated[bool, Field(
        description="Delete the job record after returning the result.")] = False,
    workspace_root: Annotated[Optional[str], Field(
        description="Workspace the job belongs to (defaults like the async tools).")] = None,
    ctx: Context = None,
) -> ToolResult:
    """Fetch a finished background job's review (free; no new Claude call).

    On success returns the SAME envelope as the synchronous tools (ok, verdict,
    confidence, findings, meta.cost_usd, ...), with meta.job_id set — reuse your
    existing result parser. If the job is not yet done it returns an ok:false
    error: job_running (poll and retry), job_cancelled, job_timeout, or job_failed;
    job_not_found if the id is unknown. Pass consume=true to delete the record once
    you have the result.
    """
    cwd, ws_err, ws_source = await _resolve_workspace(workspace_root, ctx)
    if ws_err:
        meta = _meta("", "inherit", "toolless", 0, 0, None)
        return _result(_err(ws_err, "workspace_root is not an existing absolute directory.",
                       "Pass an absolute path or configure an MCP root.", meta,
                       offending="workspace_root"))
    payload, found = await anyio.to_thread.run_sync(
        lambda: jobs.result(cwd, job_id, consume))
    if not found:
        meta = _meta(cwd, "inherit", "toolless", 0, 0, None, workspace_source=ws_source)
        return _result(_err("job_not_found", f"No job '{job_id}' in this workspace.",
                       "Check the job_id, or start a new job; records expire after the TTL.",
                       meta, offending="job_id"))
    return _result(payload)


@mcp.tool(annotations=_STATUS_ANNOTATIONS, title="Cancel background job",
          output_schema=JOB_STATUS_SCHEMA)
async def claude_job_cancel(
    job_id: Annotated[str, Field(description="A job_id from an *_async tool.")],
    workspace_root: Annotated[Optional[str], Field(
        description="Workspace the job belongs to (defaults like the async tools).")] = None,
    ctx: Context = None,
) -> ToolResult:
    """Stop a running background job (free; terminates the Claude process).

    Kills the detached Claude process and marks the job cancelled; a cancelled job
    cannot be resumed. Returns the resulting JobStatus, or job_not_found. Already
    terminal jobs are returned unchanged.
    """
    cwd, ws_err, ws_source = await _resolve_workspace(workspace_root, ctx)
    if ws_err:
        meta = _meta("", "inherit", "toolless", 0, 0, None)
        return _result(_err(ws_err, "workspace_root is not an existing absolute directory.",
                       "Pass an absolute path or configure an MCP root.", meta,
                       offending="workspace_root"))
    data = await anyio.to_thread.run_sync(lambda: jobs.cancel(cwd, job_id))
    if data is None:
        meta = _meta(cwd, "inherit", "toolless", 0, 0, None, workspace_source=ws_source)
        return _result(_err("job_not_found", f"No job '{job_id}' in this workspace.",
                       "Check the job_id, or start a new job; records expire after the TTL.",
                       meta, offending="job_id"))
    return _result(data)


@mcp.tool(annotations=_STATUS_ANNOTATIONS, title="Claude CLI status & defaults",
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
    authenticated: bool | None = None
    auth_detail: str | None = None
    supported: bool | None = None
    if found:
        try:
            version = subprocess.run(["claude", "--version"], capture_output=True,
                                     text=True, timeout=10).stdout.strip()
        except Exception:
            version = None
        supported = version_supported(version)
        # Free auth probe: lets an agent discover a logged-out CLI before
        # spending money on a paid call that would only then fail auth.
        authenticated, auth_detail = auth_status()
    d = defaults()
    resolved = ResolvedDefaults(
        config_mode=d.config_mode if d.config_mode in ("inherit", "scoped", "bare") else "inherit",
        access=d.access if d.access in ("toolless", "readonly") else "toolless",
        model=d.model,
        effort=sanitize_effort(d.effort),
        max_budget_usd=clamp_budget(d.max_budget_usd),
        timeout_seconds=clamp_timeout(d.timeout_seconds),
        budget_bounds=[MIN_BUDGET_USD, MAX_BUDGET_USD],
        timeout_bounds=[MIN_TIMEOUT_SECONDS, MAX_TIMEOUT_SECONDS],
    )
    status = StatusResult(
        claude_found=found,
        claude_version=version,
        claude_authenticated=authenticated,
        auth_detail=auth_detail,
        version_supported=supported,
        ready=bool(found and supported and authenticated),
        config_modes_available={
            "inherit": True, "scoped": True, "bare": bare_available(),
        },
        resolved_defaults=resolved,
        caveat=("OAuth-preserving + CLAUDE.md-free is impossible in claude 2.1.x; "
                "config_mode=bare needs ANTHROPIC_API_KEY."),
    )
    return _result(status.model_dump(mode="json", exclude_none=True))


@mcp.tool(annotations=_STATUS_ANNOTATIONS, title="cc-plugin-codex capabilities",
          output_schema=CAPABILITIES_SCHEMA)
def cc_codex_capabilities() -> ToolResult:
    """Return this server's contract as structured data: tool inventory, modes,
    scope/negative-scope, prerequisites, and the schema fingerprint.

    Free and read-only (makes no Claude call). Clients that cannot browse MCP
    resources can read the same contract the cc-plugin-codex://capabilities
    resource carries as prose. Pin `fingerprint` to detect schema changes.
    """
    result = CapabilitiesResult(
        name="cc-plugin-codex",
        version="0.1.0",
        transport="stdio",
        stability="experimental",
        paid_tools=["claude_ask", "claude_review_changes", "claude_adversarial_review",
                    "claude_review_changes_async"],
        free_tools=["claude_status", "cc_codex_capabilities", "claude_job_status",
                    "claude_job_result", "claude_job_cancel"],
        config_modes=["inherit", "scoped", "bare"],
        access_modes=["toolless", "readonly"],
        scope=[
            "independent code review of a git diff",
            "adversarial review of a plan/claim",
            "a free-form independent second opinion",
            "background diff review with poll/result/cancel for long runs",
        ],
        negative_scope=[
            "does NOT edit code or run shell",
            "does NOT act as a general Claude chat",
            "does NOT proxy Claude's own MCP tools",
            "does NOT resume a call once it ends or is cancelled",
        ],
        prerequisites=[
            "the `claude` CLI installed and authenticated",
            "git, for the diff-bearing tools",
            "ANTHROPIC_API_KEY only for config_mode=bare",
        ],
    )
    return _result(result.model_dump(mode="json", exclude_none=True))


@mcp.resource("cc-plugin-codex://capabilities")
def capabilities() -> str:
    """Server capability summary, negative scope, and prerequisites."""
    return CAPABILITY_SUMMARY


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
