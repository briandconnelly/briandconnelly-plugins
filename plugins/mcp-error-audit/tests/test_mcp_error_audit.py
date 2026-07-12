"""Tests for mcp_error_audit.py, run via: uvx pytest plugins/mcp-error-audit/tests/ -q"""

import json
import os
import sys

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
)
import mcp_error_audit as mea  # noqa: E402


def write_jsonl(path, records):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")


def tool_use(tid, name, inp, ts):
    return {
        "timestamp": ts,
        "message": {
            "content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}]
        },
    }


def tool_result(tid, text, ts, is_error=False):
    return {
        "timestamp": ts,
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tid,
                    "is_error": is_error,
                    "content": text,
                }
            ]
        },
    }


def envelope(code, retryable=False, repair=None, message="boom"):
    err = {"code": code, "message": message, "retryable": retryable}
    if repair is not None:
        err["repair"] = repair
    return json.dumps({"ok": False, "error": err})


# --- pure helpers -----------------------------------------------------------


def test_parse_tool_name():
    assert mea.parse_tool_name("mcp__srv__do_thing") == ("srv", "do_thing")
    assert mea.parse_tool_name("mcp__plugin_foo_srv__do_thing") == (
        "plugin_foo_srv",
        "do_thing",
    )
    assert mea.parse_tool_name("Bash") is None
    assert mea.parse_tool_name("mcp__noseparator") is None


def test_session_key_folds_subagents(tmp_path):
    root = str(tmp_path)
    parent = os.path.join(root, "proj", "abc.jsonl")
    child = os.path.join(root, "proj", "abc", "subagents", "agent-1.jsonl")
    assert mea.session_key(parent, root) == mea.session_key(child, root)


def test_classify_envelope_and_fallbacks():
    text = envelope("not_found", retryable=False, repair="use list_things")
    obj = mea.parse_envelope(text)
    assert mea.classify(text, obj) == ("not_found", False, "use list_things")
    assert mea.classify("1 validation error for call ...", None)[0] == (
        "input_validation (pydantic)"
    )
    assert mea.classify("MCP error -32000: Connection closed", None)[0] == (
        "transport_connection_closed"
    )
    assert mea.classify("weird unknown failure", None)[0] == "uncategorized"


def test_parse_envelope_prose_prefix():
    obj = mea.parse_envelope('MCP error: {"ok": false, "error": {"code": "x"}}')
    assert obj is not None and obj["error"]["code"] == "x"


# --- discovery ranking ------------------------------------------------------


def build_discovery_fixture(tmp_path):
    """noisy: 2/100 errors (2%). loud: 5/10 errors (50%). tiny: 1/1 (100%, <5 calls)."""
    root = str(tmp_path)
    records = []
    for i in range(100):
        records.append(tool_use(f"n{i}", "mcp__noisy__go", {}, f"2026-07-01T00:{i:02d}:00Z"))
        records.append(
            tool_result(f"n{i}", envelope("e") if i < 2 else "ok", f"2026-07-01T00:{i:02d}:01Z", is_error=i < 2)
        )
    for i in range(10):
        records.append(tool_use(f"l{i}", "mcp__loud__go", {}, f"2026-07-02T00:{i:02d}:00Z"))
        records.append(
            tool_result(f"l{i}", envelope("e") if i < 5 else "ok", f"2026-07-02T00:{i:02d}:01Z", is_error=i < 5)
        )
    records.append(tool_use("t0", "mcp__tiny__go", {}, "2026-07-03T00:00:00Z"))
    records.append(tool_result("t0", envelope("e"), "2026-07-03T00:00:01Z", is_error=True))
    write_jsonl(os.path.join(root, "proj", "s1.jsonl"), records)
    return root


def test_discovery_ranks_by_error_rate_with_floor(tmp_path):
    root = build_discovery_fixture(tmp_path)
    result = mea.audit(root, "", 3)
    text = mea.to_text(result)
    lines = [ln for ln in text.splitlines() if ln.startswith(("noisy", "loud", "tiny"))]
    order = [ln.split()[0] for ln in lines]
    # loud (50%) outranks noisy (2%); tiny (100% but <5 calls) sinks to the bottom
    assert order == ["loud", "noisy", "tiny"]
    data = json.loads(mea.to_json(result))
    assert list(data["servers"]) == ["loud", "noisy", "tiny"]
    assert data["servers"]["loud"]["error_rate"] == 0.5


# --- error detection --------------------------------------------------------


def test_is_error_result_ignores_status_error_data_payloads():
    # A successful poll whose *data* says a background job errored is not a tool error.
    data_payload = {"status": "error", "job_id": "j1", "detail": "job failed upstream"}
    assert mea.is_error_result({"is_error": False}, data_payload) is False
    # Corroborated forms still count.
    assert mea.is_error_result({}, {"status": "error", "error": {"code": "x"}}) is True
    assert mea.is_error_result({}, {"status": "error", "code": "timeout"}) is True
    assert mea.is_error_result({}, {"ok": False}) is True
    assert mea.is_error_result({"is_error": True}, None) is True
