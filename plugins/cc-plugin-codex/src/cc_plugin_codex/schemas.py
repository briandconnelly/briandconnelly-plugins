"""Pydantic models for the normalized tool result contract."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

FINGERPRINT = "cc-plugin-codex/0.1/schema-1"

Severity = Literal["critical", "high", "medium", "low", "nit"]
Verdict = Literal["pass", "concerns", "fail", "unknown"]
Confidence = Literal["low", "medium", "high"]
ConfigMode = Literal["inherit", "scoped", "bare"]
Access = Literal["toolless", "readonly"]

ErrorCode = Literal[
    "claude_not_found", "claude_auth_required", "api_key_required",
    "unsupported_config_mode", "unsupported_access", "invalid_scope",
    "context_too_large", "timeout", "budget_exceeded", "claude_permission_error",
    "nonzero_exit", "invalid_json", "internal_error",
]


class Finding(BaseModel):
    severity: Severity
    title: str
    file: Optional[str] = None
    line: Optional[int] = None
    evidence: str
    risk: str
    recommendation: str


class RawResponse(BaseModel):
    text: Optional[str] = None
    session_id: Optional[str] = None
    model: Optional[str] = None


class ContextSummary(BaseModel):
    files_changed: int = 0
    lines_added: int = 0
    lines_removed: int = 0


class Meta(BaseModel):
    cwd: str
    config_mode: ConfigMode
    access: Access
    scope: Optional[str] = None
    base: Optional[str] = None
    timeout_seconds: int
    elapsed_ms: int
    truncated: bool = False
    truncation_hint: Optional[str] = None
    command_exit_code: Optional[int] = None
    fingerprint: str = FINGERPRINT


class SuccessResult(BaseModel):
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
    code: ErrorCode
    message: str
    repair: str
    offending_param: Optional[str] = None
    retryable: bool = False
    retry_after_ms: Optional[int] = None


class ErrorResult(BaseModel):
    ok: Literal[False] = False
    error: ErrorInfo
    meta: Meta
