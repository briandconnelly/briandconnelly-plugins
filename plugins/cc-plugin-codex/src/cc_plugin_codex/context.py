"""Gather git diff context for review. Claude never runs git itself."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field

from cc_plugin_codex.schemas import ContextSummary

MAX_DIFF_BYTES = 200_000

SECRET_PATH_RE = re.compile(
    r"(^|/)(\.env(\.|$)|.*\.env$|.*\.pem$|.*\.key$|id_rsa|id_ed25519|.*\.p12$)",
    re.IGNORECASE,
)


@dataclass
class ContextResult:
    text: str
    summary: ContextSummary
    truncated: bool = False
    truncation_hint: str | None = None
    redacted_paths: list[str] = field(default_factory=list)


def _git(cwd: str, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "git failed")
    return proc.stdout


def _diff_args(scope: str, base: str) -> list[str]:
    # --no-ext-diff forces unified diff format even when diff.external is configured
    # (e.g. difftastic), so our parser always sees standard `diff --git` headers.
    if scope == "working_tree":
        return ["diff", "--no-ext-diff"]
    if scope == "staged":
        return ["diff", "--cached", "--no-ext-diff"]
    if scope == "branch":
        return ["diff", f"{base}...HEAD", "--no-ext-diff"]
    raise ValueError(f"invalid scope: {scope}")


def _summary(cwd: str, diff_args: list[str]) -> ContextSummary:
    numstat = _git(cwd, *diff_args, "--numstat")
    files = added = removed = 0
    for line in numstat.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        files += 1
        if parts[0].isdigit():
            added += int(parts[0])
        if parts[1].isdigit():
            removed += int(parts[1])
    return ContextSummary(files_changed=files, lines_added=added, lines_removed=removed)


def _redact(diff: str) -> tuple[str, list[str]]:
    out_lines: list[str] = []
    redacted: list[str] = []
    skipping = False
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            path = line.split(" b/")[-1] if " b/" in line else ""
            skipping = bool(SECRET_PATH_RE.search(path))
            if skipping:
                redacted.append(path)
                out_lines.append(f"diff --git a/{path} b/{path}")
                out_lines.append(f"[redacted: {path} — secret-looking file not sent]")
                continue
        if not skipping:
            out_lines.append(line)
    return "\n".join(out_lines), redacted


def gather_context(cwd: str, scope: str, base: str) -> ContextResult:
    diff_args = _diff_args(scope, base)          # raises ValueError on bad scope
    summary = _summary(cwd, diff_args)
    raw = _git(cwd, *diff_args)
    text, redacted = _redact(raw)
    truncated = False
    hint = None
    encoded = text.encode("utf-8", "replace")
    if len(encoded) > MAX_DIFF_BYTES:
        text = encoded[:MAX_DIFF_BYTES].decode("utf-8", "ignore")
        truncated = True
        hint = (f"diff exceeded {MAX_DIFF_BYTES} bytes; narrow with a smaller "
                f"scope or review specific files")
    return ContextResult(text=text, summary=summary, truncated=truncated,
                         truncation_hint=hint, redacted_paths=redacted)
