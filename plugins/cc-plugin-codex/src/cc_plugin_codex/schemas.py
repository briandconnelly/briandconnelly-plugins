"""Pydantic models for the normalized tool result contract."""

from __future__ import annotations

from typing import Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

# Bump this whenever the agent-visible surface changes: tool names, input or
# output schemas, the ErrorCode set, the config_mode/access/scope/detail value
# sets, or the capability guarantees in CAPABILITY_SUMMARY. Clients cache by it.
FINGERPRINT = "cc-plugin-codex/0.1/schema-6"

Severity = Literal["critical", "high", "medium", "low", "nit"]
Verdict = Literal["pass", "concerns", "fail", "unknown"]
Confidence = Literal["low", "medium", "high"]
ConfigMode = Literal["inherit", "scoped", "bare"]
Access = Literal["toolless", "readonly"]
Scope = Literal["working_tree", "staged", "branch"]
Detail = Literal["summary", "full"]
Effort = Literal["low", "medium", "high", "xhigh", "max"]
# Lifecycle states for a background job. Terminal: done|failed|cancelled|timeout.
# (TTL-expired records are deleted and reported as job_not_found, not a state.)
JobState = Literal["running", "done", "failed", "cancelled", "timeout"]

ErrorCode = Literal[
    "claude_not_found", "claude_auth_required", "api_key_required",
    "unsupported_config_mode", "unsupported_access", "invalid_scope", "invalid_base",
    "invalid_workspace_root",
    "context_too_large", "timeout", "budget_exceeded", "claude_permission_error",
    "nonzero_exit", "invalid_json", "internal_error",
    # Background-job lifecycle errors (claude_job_result for a non-done job):
    "job_not_found", "job_running", "job_cancelled", "job_timeout", "job_failed",
]


class Usage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cache_read_input_tokens: Optional[int] = None
    cache_creation_input_tokens: Optional[int] = None


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    severity: Severity
    title: str
    file: Optional[str] = None
    line: Optional[int] = None
    line_end: Optional[int] = None   # end line when the finding spans a range (line = start)
    evidence: str
    risk: str
    recommendation: str


class RawResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: Optional[str] = None
    session_id: Optional[str] = None
    model: Optional[str] = None


class ContextSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    files_changed: int = 0
    lines_added: int = 0
    lines_removed: int = 0


class Meta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cwd: str
    workspace_source: Optional[str] = None   # how cwd was resolved: param|roots|cwd
    config_mode: ConfigMode
    access: Access
    scope: Optional[str] = None
    base: Optional[str] = None
    timeout_seconds: int
    elapsed_ms: int
    truncated: bool = False
    truncation_hint: Optional[str] = None
    command_exit_code: Optional[int] = None
    permission_denials: Optional[list] = None
    cost_usd: Optional[float] = None
    usage: Optional[Usage] = None
    job_id: Optional[str] = None   # set on background-job results; None for sync calls
    request_id: str = Field(default_factory=lambda: uuid4().hex)
    fingerprint: str = FINGERPRINT


class SuccessResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: Literal[True] = True
    tool: str
    summary: str
    verdict: Verdict
    confidence: Confidence
    findings: list[Finding] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
    raw_response: RawResponse = Field(default_factory=RawResponse)
    context_summary: Optional[ContextSummary] = None
    meta: Meta


class ErrorInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: ErrorCode
    message: str
    repair: str
    offending_param: Optional[str] = None
    retryable: bool = False


class ErrorResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: Literal[False] = False
    error: ErrorInfo
    meta: Meta


class ResolvedDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")
    config_mode: ConfigMode
    access: Access
    model: Optional[str] = None
    effort: Effort
    max_budget_usd: float
    timeout_seconds: int
    budget_bounds: list[float]   # [min, max] clamp range for max_budget_usd
    timeout_bounds: list[int]    # [min, max] clamp range for timeout_seconds


class StatusResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: Literal[True] = True
    claude_found: bool
    claude_version: Optional[str] = None
    # Readiness probes (all free — no paid Claude call):
    claude_authenticated: Optional[bool] = None   # None = could not determine
    auth_detail: Optional[str] = None
    version_supported: Optional[bool] = None       # matches the supported CLI major
    ready: bool = False        # found AND a supported version AND authenticated
    config_modes_available: dict
    resolved_defaults: ResolvedDefaults
    caveat: str
    fingerprint: str = FINGERPRINT


class CapabilitiesResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: Literal[True] = True
    name: str
    version: str
    fingerprint: str = FINGERPRINT
    transport: str
    stability: str
    paid_tools: list[str]
    free_tools: list[str]
    config_modes: list[str]
    access_modes: list[str]
    scope: list[str]            # what this server is for
    negative_scope: list[str]   # what it deliberately does NOT do
    prerequisites: list[str]


class JobStarted(BaseModel):
    """Returned by the *_async tools: a handle to poll, not a result."""
    model_config = ConfigDict(extra="forbid")
    ok: Literal[True] = True
    job_id: str
    kind: str                  # the tool the job runs, e.g. claude_review_changes
    status: JobState = "running"
    started_at: str            # ISO-8601 UTC
    deadline_seconds: int      # wall-clock cap after which a poll reaps the job
    meta: Meta
    fingerprint: str = FINGERPRINT


class JobStatus(BaseModel):
    """Returned by claude_job_status: lifecycle state without the full result."""
    model_config = ConfigDict(extra="forbid")
    ok: Literal[True] = True
    job_id: str
    kind: str
    status: JobState
    started_at: str
    elapsed_ms: int
    deadline_seconds: int
    result_available: bool = False   # true once status == done
    cost_usd: Optional[float] = None  # populated for terminal jobs that spent
    detail: Optional[str] = None      # short human hint (e.g. failure reason)
    fingerprint: str = FINGERPRINT


def _object_union_schema(adapter: TypeAdapter) -> dict:
    """Wrap a model union's anyOf in a top-level object schema.

    MCP/FastMCP require an output schema whose top level is ``type: object``;
    a bare ``anyOf`` is rejected. We keep the discriminating ``ok`` key visible
    at the top and carry the full branch schemas (and their $defs) underneath.
    """
    union = adapter.json_schema()
    return {
        "type": "object",
        "properties": {
            "ok": {"type": "boolean",
                   "description": "true = success result, false = error result"},
        },
        "required": ["ok"],
        "anyOf": union["anyOf"],
        "$defs": union.get("$defs", {}),
    }


# Advertised output schemas (convention: a discriminated ok:true|false union).
RESULT_SCHEMA = _object_union_schema(TypeAdapter(SuccessResult | ErrorResult))
STATUS_SCHEMA = StatusResult.model_json_schema()
CAPABILITIES_SCHEMA = CapabilitiesResult.model_json_schema()
# A failed *_async launch (e.g. context_too_large) returns the error envelope, so
# the start tools advertise the JobStarted|ErrorResult union.
JOB_STARTED_SCHEMA = _object_union_schema(TypeAdapter(JobStarted | ErrorResult))
JOB_STATUS_SCHEMA = _object_union_schema(TypeAdapter(JobStatus | ErrorResult))
