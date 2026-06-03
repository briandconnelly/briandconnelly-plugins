"""Pydantic models for the normalized tool result contract."""

from __future__ import annotations

from typing import Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

# Bump this whenever the agent-visible surface changes: tool names, input or
# output schemas, the ErrorCode set, the config_mode/access/scope/detail value
# sets, or the capability guarantees in CAPABILITY_SUMMARY. Clients cache by it.
FINGERPRINT = "cc-plugin-codex/0.1/schema-3"

Severity = Literal["critical", "high", "medium", "low", "nit"]
Verdict = Literal["pass", "concerns", "fail", "unknown"]
Confidence = Literal["low", "medium", "high"]
ConfigMode = Literal["inherit", "scoped", "bare"]
Access = Literal["toolless", "readonly"]
Scope = Literal["working_tree", "staged", "branch"]
Detail = Literal["summary", "full"]

ErrorCode = Literal[
    "claude_not_found", "claude_auth_required", "api_key_required",
    "unsupported_config_mode", "unsupported_access", "invalid_scope", "invalid_base",
    "invalid_workspace_root",
    "context_too_large", "timeout", "budget_exceeded", "claude_permission_error",
    "nonzero_exit", "invalid_json", "internal_error",
]


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    severity: Severity
    title: str
    file: Optional[str] = None
    line: Optional[int] = None
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
    max_budget_usd: float
    timeout_seconds: int
    budget_bounds: list[float]   # [min, max] clamp range for max_budget_usd
    timeout_bounds: list[int]    # [min, max] clamp range for timeout_seconds


class StatusResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: Literal[True] = True
    claude_found: bool
    claude_version: Optional[str] = None
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
