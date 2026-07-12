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
