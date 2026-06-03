"""Build per-tool prompts and normalize claude's JSON envelope into the contract."""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from cc_plugin_codex.schemas import (
    ContextSummary, ErrorInfo, ErrorResult, Finding, Meta, RawResponse, SuccessResult,
)

_SCHEMA_INSTRUCTION = (
    "Respond with ONLY a single JSON object (no prose, no code fence) with keys: "
    '"summary" (string), "verdict" (one of pass|concerns|fail|unknown), '
    '"confidence" (one of low|medium|high), "findings" (array of objects with '
    'severity[critical|high|medium|low|nit], title, file, line, evidence, risk, '
    'recommendation), "questions" (array of strings), "assumptions" (array of strings).'
)

_LEAD = {
    "claude_ask": "Give an independent second opinion on the following question.",
    "claude_review_changes": "Review the following code changes for correctness, "
        "regressions, security, and missing tests.",
    "claude_adversarial_review": "Attack the following plan/claim. Find the strongest "
        "counterarguments, failure modes, and risks.",
}

_VALID_VERDICT = {"pass", "concerns", "fail", "unknown"}
_VALID_CONFIDENCE = {"low", "medium", "high"}
_VALID_SEVERITY = {"critical", "high", "medium", "low", "nit"}


def _str_list(value: Any) -> list[str]:
    return [str(x) for x in value if x] if isinstance(value, list) else []


def build_prompt(tool: str, payload: dict[str, Any], context_text: str) -> str:
    parts = [_LEAD.get(tool, _LEAD["claude_ask"])]
    if tool == "claude_ask":
        parts.append(payload["prompt"])
        if payload.get("context"):
            parts.append(f"\nAdditional context:\n{payload['context']}")
    elif tool == "claude_review_changes":
        if payload.get("focus"):
            parts.append(f"Focus especially on: {payload['focus']}.")
        parts.append(f"\nChanges (scope={payload.get('scope')}):\n{context_text}")
    elif tool == "claude_adversarial_review":
        parts.append(f"\nTarget:\n{payload['target']}")
        if payload.get("evidence"):
            parts.append(f"\nEvidence:\n{payload['evidence']}")
        if context_text:
            parts.append(f"\nRelated changes:\n{context_text}")
    parts.append("\n" + _SCHEMA_INSTRUCTION)
    return "\n".join(parts)


def extract_json(text: str) -> Optional[dict]:
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        brace = re.search(r"\{.*\}", text, re.DOTALL)
        candidate = brace.group(0) if brace else None
    if candidate is None:
        return None
    try:
        parsed = json.loads(candidate)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def _clamp(value: Any, allowed: set[str], default: str) -> str:
    return value if value in allowed else default


def _clean_findings(raw: Any) -> list[Finding]:
    findings: list[Finding] = []
    if not isinstance(raw, list):
        return findings
    for f in raw:
        if not isinstance(f, dict):
            continue
        if not all(f.get(k) for k in ("title", "evidence", "risk", "recommendation")):
            continue  # drop incomplete findings rather than fabricate fields
        line = f.get("line")
        findings.append(Finding(
            severity=_clamp(f.get("severity"), _VALID_SEVERITY, "low"),
            title=str(f["title"]),
            file=str(f["file"]) if f.get("file") else None,
            line=line if isinstance(line, int) else None,
            evidence=str(f["evidence"]),
            risk=str(f["risk"]),
            recommendation=str(f["recommendation"]),
        ))
    return findings


def _error(info: ErrorInfo, meta: Meta) -> dict:
    return ErrorResult(error=info, meta=meta).model_dump(mode="json", exclude_none=True)


def normalize_envelope(tool: str, stdout: str, meta: Meta, detail: str,
                       context_summary: ContextSummary | None = None) -> dict:
    try:
        env = json.loads(stdout)
    except json.JSONDecodeError:
        return _error(ErrorInfo(code="invalid_json",
                      message="claude did not return valid JSON.",
                      repair="Retry; if it persists, reduce context size."), meta)

    if env.get("is_error") or env.get("subtype") not in (None, "success"):
        detail = (env.get("result") or "").strip() or (env.get("subtype") or "unknown error")
        return _error(ErrorInfo(code="nonzero_exit",
                      message=f"claude reported an error: {detail[:200]}",
                      repair="Inspect the error; retry with a smaller or corrected request."),
                      meta)

    text = env.get("result", "") or ""
    raw = RawResponse(
        text=text if detail == "full" else None,
        session_id=env.get("session_id"),
        model=next(iter(env.get("modelUsage") or {}), None),
    )
    inner = extract_json(text)

    # If Claude was blocked by denied tools AND produced nothing usable, surface it.
    denials = env.get("permission_denials") or []
    if denials and (inner is None and not text.strip()):
        return _error(ErrorInfo(code="claude_permission_error",
                      message=f"claude was denied required tools: {str(denials)[:160]}",
                      repair="Use access=toolless, or allow the needed read-only tools.",
                      ), meta)

    if inner is None:
        result = SuccessResult(tool=tool, summary=text.strip()[:500] or "(no content)",
                               verdict="unknown", confidence="low", raw_response=raw,
                               context_summary=context_summary if detail == "full" else None,
                               meta=meta)
        if denials:
            result.meta.permission_denials = denials
        return result.model_dump(mode="json", exclude_none=True)

    result = SuccessResult(
        tool=tool,
        summary=str(inner.get("summary", "")),
        verdict=_clamp(inner.get("verdict"), _VALID_VERDICT, "unknown"),
        confidence=_clamp(inner.get("confidence"), _VALID_CONFIDENCE, "low"),
        findings=_clean_findings(inner.get("findings", [])),
        questions=_str_list(inner.get("questions")),
        assumptions=_str_list(inner.get("assumptions")),
        raw_response=raw,
        context_summary=context_summary if detail == "full" else None,
        meta=meta,
    )
    if denials:
        result.meta.permission_denials = denials
    return result.model_dump(mode="json", exclude_none=True)
