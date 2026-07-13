#!/usr/bin/env python3
"""Audit MCP tool errors across all Claude Code sessions.

Scans every session transcript under ~/.claude/projects/**/*.jsonl, finds tool
calls to a given MCP server, and aggregates their errors by structured error
`code`. MCP tool names look like `mcp__<server>__<tool>`, where <server> is
either the bare server name or `plugin_<plugin>_<server>` for plugin-hosted
servers; --server matches by substring, so one audit merges both naming eras.

Sidechain transcripts (<session>/subagents/agent-*.jsonl) are folded into
their parent session, so the `sessions` column counts real sessions rather
than spawned agents.

With no --server, runs discovery mode: lists every MCP server seen in the
transcripts with call/error/session counts.

Usage:
    mcp_error_audit.py [--server SUBSTRING] [--json] [--root DIR] [--samples N]

Standard library only.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import re
from dataclasses import dataclass, field
from glob import glob

VALID_SERVER = re.compile(r"^[A-Za-z0-9._-]+$")

# The label used wherever a call's release could not be attributed from its own result.
UNKNOWN = "unknown"

# Exact paths, in precedence order. NEVER a regex over key names: a server may emit an
# unrelated version (codex-in-claude emits `codex_version`, the Codex CLI's version),
# and a name-matching heuristic would report on the wrong software. Both `meta.*` and
# top-level shapes are required — in real corpora roughly half of all results carry no
# `meta` at all (job/status envelopes), and those carry their identity at top level.
VERSION_PATHS = (
    ("meta", "server_version"),
    ("server_version",),
    ("meta", "server", "version"),
    ("server", "version"),
)
FINGERPRINT_PATHS = (
    ("meta", "fingerprint"),
    ("fingerprint",),
)


def _dig(obj, path: tuple[str, ...]) -> str | None:
    """The non-empty string at `path` in `obj`, or None. Never raises on odd shapes."""
    cur = obj
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur if isinstance(cur, str) and cur else None


def _first_at_paths(obj, paths) -> str | None:
    for path in paths:
        found = _dig(obj, path)
        if found:
            return found
    return None


def extract_version(obj) -> str | None:
    """The server's own release, from an allowlisted path in its result envelope."""
    return _first_at_paths(obj, VERSION_PATHS)


def extract_fingerprint(obj) -> str | None:
    """The server's contract/surface id, from an allowlisted path."""
    return _first_at_paths(obj, FINGERPRINT_PATHS)


# Servers with fewer calls than this sort below the rest: a 1-for-1 error
# rate is noise, not signal.
MIN_CALLS_FOR_RATE = 5


def server_sort_key(kv):
    """Discovery ranking: error rate desc, low-volume servers last."""
    _, s = kv
    rate = s.errors / s.calls if s.calls else 0.0
    return (s.calls < MIN_CALLS_FOR_RATE, -rate, -s.errors, -s.calls)


def iter_records(path: str):
    """Yield parsed JSON objects from a JSONL file, skipping bad lines.

    Decoding is tolerant (`errors="replace"`) so a single corrupt byte can't
    abort a whole-tree audit; unparseable or over-nested lines are skipped.
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except (json.JSONDecodeError, ValueError, RecursionError):
                    continue
    except OSError:
        return


def message_content(record) -> list:
    """Return the message.content list from a transcript record, or []."""
    if not isinstance(record, dict):
        return []
    msg = record.get("message")
    if not isinstance(msg, dict):
        return []
    content = msg.get("content")
    return content if isinstance(content, list) else []


def record_ts(record) -> str | None:
    """The record's ISO timestamp, or None. ISO strings compare correctly."""
    ts = record.get("timestamp") if isinstance(record, dict) else None
    return ts if isinstance(ts, str) and ts else None


def result_text(item: dict) -> str:
    """Flatten a tool_result's content into plain text."""
    content = item.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            sub.get("text", "")
            for sub in content
            if isinstance(sub, dict) and sub.get("type") == "text"
        ]
        return "\n".join(parts)
    return ""


def parse_envelope(text: str):
    """Return the parsed JSON dict for a result payload, else None.

    Tolerates prose before and after the object (e.g. 'MCP error: {...} …')
    so a structured envelope isn't lost just because it's wrapped.
    """
    stripped = text.strip()
    brace = stripped.find("{")
    if brace == -1:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(stripped[brace:])
    except (json.JSONDecodeError, ValueError, RecursionError):
        return None
    return obj if isinstance(obj, dict) else None


def error_object(obj):
    """The dict that actually holds code/retryable/repair for an envelope."""
    if isinstance(obj, dict) and isinstance(obj.get("error"), dict):
        return obj["error"]
    return obj if isinstance(obj, dict) else None


def classify(text: str, obj) -> tuple[str, bool | None, str | None]:
    """Return (code, retryable, repair) for a tool_result's error.

    Reads code/retryable/repair from the SAME structured error object; falls
    back to a normalized signature for non-envelope errors (validation,
    transport drops).
    """
    err = error_object(obj)
    if err is not None:
        code = err.get("code")
        if isinstance(code, str) and code:
            retryable = err.get("retryable")
            if not isinstance(retryable, bool):
                retryable = None
            repair = err.get("repair")
            if isinstance(repair, dict):
                repair = json.dumps(repair, sort_keys=True)
            elif not isinstance(repair, str):
                repair = None
            return code, retryable, repair

    low = text.lower()
    if "validation error for call" in low:
        return "input_validation (pydantic)", False, None
    if "connection closed" in low or "mcp error -32000" in low:
        return "transport_connection_closed", None, None
    if "no such tool available" in low:
        return "tool_unavailable", None, None
    if "-32602" in text or "invalid params" in low:
        return "invalid_params (-32602)", None, None
    return "uncategorized", None, None


def is_error_result(item: dict, obj) -> bool:
    """True if a tool_result is an error, by native flag or envelope semantics.

    A bare `status: "error"` is only trusted when the payload also carries an
    `error` object/string or a string `code` — otherwise a successful result
    whose *data* describes an error (e.g. a job-status poll) would be
    miscounted as a tool failure.
    """
    if item.get("is_error") is True:
        return True
    if isinstance(obj, dict):
        if obj.get("ok") is False:
            return True
        if obj.get("status") == "error" and (
            isinstance(obj.get("error"), (dict, str))
            or isinstance(obj.get("code"), str)
        ):
            return True
    return False


def parse_tool_name(name: str) -> tuple[str, str] | None:
    """Split 'mcp__<server>__<tool>' into (server, tool), else None.

    <server> is the bare server name or 'plugin_<plugin>_<server>' for
    plugin-hosted servers (single underscores, so the first '__' splits it
    from the tool name).
    """
    if not name.startswith("mcp__"):
        return None
    server, sep, tool = name[5:].partition("__")
    if sep and server and tool:
        return server, tool
    return None


def canonical_server(srv: str) -> str:
    """Collapse 'plugin_<plugin>_<server>' to '<server>' for identity checks.

    Assumes plugin names contain no underscores (marketplace convention is
    kebab-case); a plugin name with underscores would over-strip, which only
    risks a missed warning, never wrong statistics.
    """
    if srv.startswith("plugin_"):
        rest = srv[len("plugin_") :]
        _, sep, tail = rest.partition("_")
        if sep:
            return tail
    return srv


def distinct_matches(result) -> list[str]:
    """Canonical names when the substring matched >1 genuinely different server."""
    canon = sorted({canonical_server(s) for s in result["matched_servers"]})
    return canon if len(canon) > 1 else []


def session_key(path: str, root: str) -> str:
    """Collapse sidechain transcripts into their parent session.

    <proj>/<uuid>.jsonl and <proj>/<uuid>/subagents/agent-*.jsonl are the
    same session.
    """
    rel = os.path.relpath(path, root)
    parts = rel.split(os.sep)
    if len(parts) >= 3 and parts[-2] == "subagents":
        return os.sep.join(parts[:-2])
    return rel[: -len(".jsonl")] if rel.endswith(".jsonl") else rel


@dataclass
class CodeStat:
    count: int = 0
    recovered: int = 0
    sessions: set[str] = field(default_factory=set)
    tools: collections.Counter = field(default_factory=collections.Counter)
    retryable: bool | None = None
    repair: str | None = None
    first_seen: str | None = None
    last_seen: str | None = None
    samples: list = field(default_factory=list)

    def add_sample(self, ts: str | None, input_snippet: str, text: str, limit: int):
        """Keep the `limit` most recent samples (recent evidence beats stale)."""
        if limit <= 0:
            return
        entry = {
            "ts": ts,
            "input": input_snippet,
            "text": " ".join(text.split())[:200],
        }
        if len(self.samples) < limit:
            self.samples.append(entry)
            return
        oldest = min(
            range(len(self.samples)), key=lambda i: self.samples[i]["ts"] or ""
        )
        if (ts or "") > (self.samples[oldest]["ts"] or ""):
            self.samples[oldest] = entry

    def see(self, ts: str | None) -> bool:
        """Record an occurrence timestamp; True if it is the newest so far."""
        if ts is None:
            return self.last_seen is None
        if self.first_seen is None or ts < self.first_seen:
            self.first_seen = ts
        if self.last_seen is None or ts >= self.last_seen:
            self.last_seen = ts
            return True
        return False


@dataclass
class ServerStat:
    calls: int = 0
    errors: int = 0
    sessions: set[str] = field(default_factory=set)
    last_call: str | None = None


def audit(root: str, server: str, samples: int):
    files = sorted(glob(os.path.join(root, "**", "*.jsonl"), recursive=True))
    all_sessions = {session_key(p, root) for p in files}
    needle = server.lower()

    servers: dict[str, ServerStat] = collections.defaultdict(ServerStat)
    total_calls = collections.Counter()
    total_errors = collections.Counter()
    codes: dict[str, CodeStat] = collections.defaultdict(CodeStat)
    audit_sessions: set[str] = set()

    for path in files:
        session = session_key(path, root)

        id2parsed: dict[str, tuple[str, str]] = {}
        id2input: dict[str, str] = {}
        for rec in iter_records(path):
            ts = record_ts(rec)
            for item in message_content(rec):
                if not (isinstance(item, dict) and item.get("type") == "tool_use"):
                    continue
                parsed = parse_tool_name(item.get("name") or "")
                if not parsed:
                    continue
                tid = item.get("id")
                if tid:
                    id2parsed[tid] = parsed
                srv, tool = parsed
                sstat = servers[srv]
                sstat.calls += 1
                sstat.sessions.add(session)
                if ts and (sstat.last_call is None or ts > sstat.last_call):
                    sstat.last_call = ts
                if needle and needle in srv.lower():
                    total_calls[tool] += 1
                    audit_sessions.add(session)
                    if tid:
                        raw = json.dumps(
                            item.get("input", {}), sort_keys=True, default=str
                        )
                        id2input[tid] = " ".join(raw.split())[:120]

        # An error "recovers" when a later result for the same tool in the
        # same transcript succeeds; pending holds codes awaiting that success.
        pending: dict[str, list[str]] = collections.defaultdict(list)
        for rec in iter_records(path):
            ts = record_ts(rec)
            for item in message_content(rec):
                if not (isinstance(item, dict) and item.get("type") == "tool_result"):
                    continue
                parsed = id2parsed.get(item.get("tool_use_id") or "")
                if not parsed:
                    continue
                srv, tool = parsed
                text = result_text(item)
                obj = parse_envelope(text)
                err = is_error_result(item, obj)
                if err:
                    servers[srv].errors += 1
                if not (needle and needle in srv.lower()):
                    continue
                if not err:
                    for code in pending.pop(tool, []):
                        codes[code].recovered += 1
                    continue
                total_errors[tool] += 1
                code, retryable, repair = classify(text, obj)
                stat = codes[code]
                stat.count += 1
                stat.sessions.add(session)
                stat.tools[tool] += 1
                if stat.see(ts):
                    if retryable is not None:
                        stat.retryable = retryable
                    if repair:
                        stat.repair = repair
                stat.add_sample(
                    ts, id2input.get(item.get("tool_use_id") or "", "?"), text, samples
                )
                pending[tool].append(code)

    return {
        "server": server,
        "matched_servers": sorted(s for s in servers if needle and needle in s.lower()),
        "files_scanned": len(files),
        "sessions_scanned": len(all_sessions),
        "servers": servers,
        "audit_sessions": audit_sessions,
        "total_calls": total_calls,
        "total_errors": total_errors,
        "codes": codes,
    }


def sorted_codes(result):
    return sorted(result["codes"].items(), key=lambda kv: -kv[1].count)


def matched_last_call(result) -> str | None:
    """Newest call timestamp across the matched servers, for staleness anchoring."""
    stamps = [
        result["servers"][srv].last_call
        for srv in result["matched_servers"]
        if result["servers"][srv].last_call
    ]
    return max(stamps) if stamps else None


def to_json(result) -> str:
    if not result["server"]:
        return json.dumps(
            {
                "mode": "discovery",
                "files_scanned": result["files_scanned"],
                "sessions_scanned": result["sessions_scanned"],
                "servers": {
                    srv: {
                        "calls": s.calls,
                        "errors": s.errors,
                        "error_rate": round(s.errors / s.calls, 3) if s.calls else None,
                        "sessions": len(s.sessions),
                        "last_call": s.last_call,
                    }
                    for srv, s in sorted(
                        result["servers"].items(),
                        key=server_sort_key,
                    )
                },
            },
            indent=2,
        )
    codes = {
        code: {
            "count": s.count,
            "sessions": len(s.sessions),
            "recovered": s.recovered,
            "retryable": s.retryable,
            "first_seen": s.first_seen,
            "last_seen": s.last_seen,
            "tools": dict(s.tools),
            "repair": s.repair,
            "samples": s.samples,
        }
        for code, s in sorted_codes(result)
    }
    return json.dumps(
        {
            "mode": "audit",
            "server": result["server"],
            "matched_servers": result["matched_servers"],
            **(
                {"distinct_matches": distinct_matches(result)}
                if distinct_matches(result)
                else {}
            ),
            "files_scanned": result["files_scanned"],
            "sessions_scanned": result["sessions_scanned"],
            "server_sessions": len(result["audit_sessions"]),
            "last_call": matched_last_call(result),
            "total_calls": sum(result["total_calls"].values()),
            "total_errors": sum(result["total_errors"].values()),
            "by_tool": {
                t: {
                    "calls": result["total_calls"][t],
                    "errors": result["total_errors"].get(t, 0),
                }
                for t in result["total_calls"]
            },
            "by_code": codes,
        },
        indent=2,
    )


def to_text(result) -> str:
    out = []
    if not result["server"]:
        out.append("# MCP servers seen in transcripts (discovery mode)")
        out.append(
            f"scanned {result['files_scanned']} transcripts · "
            f"{result['sessions_scanned']} sessions · ranked by error rate "
            f"(servers with <{MIN_CALLS_FOR_RATE} calls listed last)"
        )
        if not result["servers"]:
            out.append("\nNo MCP tool calls found in any transcript.")
            return "\n".join(out)
        out.append(
            f"\n{'server':<44} {'calls':>6} {'errs':>5} {'err%':>6} {'sess':>4}  last_call"
        )
        for srv, s in sorted(result["servers"].items(), key=server_sort_key):
            last = (s.last_call or "?")[:10]
            pct = f"{100 * s.errors / s.calls:.1f}" if s.calls else "n/a"
            out.append(
                f"{srv:<44} {s.calls:>6} {s.errors:>5} {pct:>6} "
                f"{len(s.sessions):>4}  {last}"
            )
        out.append("\nRe-run with --server <name-or-substring> to audit one server.")
        return "\n".join(out)

    calls = sum(result["total_calls"].values())
    errors = sum(result["total_errors"].values())
    rate = f"{100 * errors / calls:.1f}%" if calls else "n/a"
    n_sess = len(result["audit_sessions"])
    out.append(f"# MCP error audit — servers matching '{result['server']}'")
    if not calls:
        out.append(
            "\nNo MCP tool calls matched. Run without --server to list "
            "the servers seen in transcripts."
        )
        return "\n".join(out)
    out.append(f"matched: {', '.join(result['matched_servers'])}")
    distinct = distinct_matches(result)
    if distinct:
        out.append(
            f"WARNING: matched servers look distinct ({', '.join(distinct)}); "
            "the stats below blend them — narrow --server if unintended."
        )
    out.append(
        f"scanned {result['files_scanned']} transcripts "
        f"({result['sessions_scanned']} sessions) · this server: "
        f"{calls} calls · {errors} errors ({rate}) · {n_sess} sessions · "
        f"last call {(matched_last_call(result) or '?')[:10]}"
    )

    out.append("\n## Per-tool")
    out.append(f"{'tool':<28} {'calls':>6} {'errs':>6}")
    for tool, n in result["total_calls"].most_common():
        out.append(f"{tool:<28} {n:>6} {result['total_errors'].get(tool, 0):>6}")

    out.append(
        f"\n## Errors by code  (sess = distinct sessions out of the {n_sess} "
        "that called this server; recov = errors followed later in the same "
        "transcript by a success of the same tool)"
    )
    out.append(
        f"{'code':<34} {'count':>5} {'sess':>4} {'recov':>5} {'retry':>5}  "
        f"{'first_seen':<10}  {'last_seen':<10}  tools"
    )
    for code, s in sorted_codes(result):
        retry = {True: "yes", False: "no", None: "?"}[s.retryable]
        tools = ",".join(f"{t}×{c}" for t, c in s.tools.most_common())
        first = (s.first_seen or "?")[:10]
        last = (s.last_seen or "?")[:10]
        out.append(
            f"{code:<34} {s.count:>5} {len(s.sessions):>4} {s.recovered:>5} "
            f"{retry:>5}  {first:<10}  {last:<10}  {tools}"
        )
        if s.repair:
            out.append(f"    repair: {s.repair}")

    out.append("\n## Representative samples  (date · input → error)")
    for code, s in sorted_codes(result):
        if s.samples:
            out.append(f"[{code}]")
            for sample in sorted(s.samples, key=lambda e: e["ts"] or ""):
                date = (sample["ts"] or "?")[:10]
                out.append(f"  · {date} · {sample['input']} → {sample['text']}")
    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--server",
        default="",
        help="MCP server name or substring (empty/omitted → discovery mode "
        "listing all servers)",
    )
    parser.add_argument(
        "--root",
        default=os.path.expanduser("~/.claude/projects"),
        help="Directory of session transcripts",
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit JSON instead of a table"
    )
    parser.add_argument(
        "--samples", type=int, default=3, help="Sample error texts per code"
    )
    args = parser.parse_args()

    server = (args.server or "").strip()
    if server and not VALID_SERVER.match(server):
        parser.error(
            f"invalid --server value {server!r}: expected characters in [A-Za-z0-9._-]"
        )

    result = audit(args.root, server, args.samples)
    print(to_json(result) if args.json else to_text(result))


if __name__ == "__main__":
    main()
