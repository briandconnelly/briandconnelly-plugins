"""Tests for mcp_error_audit.py, run via: uvx pytest plugins/mcp-error-audit/tests/ -q"""

import collections
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
        minute = f"{i // 60:02d}:{i % 60:02d}"
        records.append(
            tool_use(f"n{i}", "mcp__noisy__go", {}, f"2026-07-01T{minute}:00Z")
        )
        records.append(
            tool_result(
                f"n{i}",
                envelope("e") if i < 2 else "ok",
                f"2026-07-01T{minute}:01Z",
                is_error=i < 2,
            )
        )
    for i in range(10):
        records.append(
            tool_use(f"l{i}", "mcp__loud__go", {}, f"2026-07-02T00:{i:02d}:00Z")
        )
        records.append(
            tool_result(
                f"l{i}",
                envelope("e") if i < 5 else "ok",
                f"2026-07-02T00:{i:02d}:01Z",
                is_error=i < 5,
            )
        )
    records.append(tool_use("t0", "mcp__tiny__go", {}, "2026-07-03T00:00:00Z"))
    records.append(
        tool_result("t0", envelope("e"), "2026-07-03T00:00:01Z", is_error=True)
    )
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


def test_parse_envelope_trailing_text():
    obj = mea.parse_envelope('{"ok": false, "error": {"code": "x"}} (see logs)')
    assert obj is not None and obj["error"]["code"] == "x"
    obj = mea.parse_envelope('prefix {"ok": false, "error": {"code": "y"}} suffix')
    assert obj is not None and obj["error"]["code"] == "y"
    assert mea.parse_envelope("no json here") is None
    assert mea.parse_envelope("") is None
    assert mea.parse_envelope('["not", "a", "dict"]') is None


# --- audit-mode evidence ----------------------------------------------------


def build_audit_fixture(tmp_path):
    root = str(tmp_path)
    s1 = [
        tool_use("a1", "mcp__srv__fetch", {"id": "one"}, "2026-06-01T10:00:00Z"),
        tool_result(
            "a1",
            envelope("not_found", repair="use srv_list"),
            "2026-06-01T10:00:01Z",
            is_error=True,
        ),
        tool_use("a2", "mcp__srv__fetch", {"id": "two"}, "2026-06-01T10:01:00Z"),
        tool_result("a2", "ok", "2026-06-01T10:01:01Z"),  # recovery for a1
    ]
    s2 = [
        tool_use("b1", "mcp__srv__fetch", {"id": "three"}, "2026-07-01T10:00:00Z"),
        tool_result("b1", envelope("not_found"), "2026-07-01T10:00:01Z", is_error=True),
        tool_use("b2", "mcp__srv__fetch", {"id": "four"}, "2026-07-02T10:00:00Z"),
        tool_result("b2", envelope("not_found"), "2026-07-02T10:00:01Z", is_error=True),
        tool_use("b3", "mcp__srv__fetch", {"id": "five"}, "2026-07-03T10:00:00Z"),
        tool_result("b3", envelope("not_found"), "2026-07-03T10:00:01Z", is_error=True),
        tool_use("b4", "mcp__srv__fetch", {"id": "six"}, "2026-07-04T10:00:00Z"),
        tool_result("b4", envelope("not_found"), "2026-07-04T10:00:01Z", is_error=True),
    ]
    write_jsonl(os.path.join(root, "proj", "s1.jsonl"), s1)
    write_jsonl(os.path.join(root, "proj", "s2.jsonl"), s2)
    return root


def test_samples_are_recent_dated_and_carry_inputs(tmp_path):
    root = build_audit_fixture(tmp_path)
    result = mea.audit(root, "srv", 3)
    stat = result["codes"]["not_found"]
    assert stat.count == 5
    assert stat.recovered == 1
    assert stat.first_seen.startswith("2026-06-01")
    assert stat.last_seen.startswith("2026-07-04")
    # 5 errors, limit 3 → the 3 most recent survive
    dates = sorted(s["ts"][:10] for s in stat.samples)
    assert dates == ["2026-07-02", "2026-07-03", "2026-07-04"]
    assert all('"id"' in s["input"] for s in stat.samples)
    text = mea.to_text(result)
    assert "first_seen" in text  # column present
    assert "2026-06-01" in text  # first_seen value rendered
    assert '{"id": "six"}' in text  # input snippet rendered in samples


def test_json_samples_are_structured(tmp_path):
    root = build_audit_fixture(tmp_path)
    data = json.loads(mea.to_json(mea.audit(root, "srv", 3)))
    sample = data["by_code"]["not_found"]["samples"][0]
    assert set(sample) == {"ts", "input", "text"}


def test_samples_zero_is_a_noop(tmp_path):
    # --samples 0 must disable sample collection, not crash (regression).
    root = build_audit_fixture(tmp_path)
    result = mea.audit(root, "srv", 0)
    stat = result["codes"]["not_found"]
    assert stat.count == 5
    assert stat.samples == []


# --- distinct-server merge warning -----------------------------------------


def test_canonical_server():
    assert mea.canonical_server("cwms-tools") == "cwms-tools"
    assert mea.canonical_server("plugin_cwms_cwms-tools") == "cwms-tools"
    assert mea.canonical_server("plugin_codex-in-claude_codex-in-claude") == (
        "codex-in-claude"
    )
    assert mea.canonical_server("cc-plugin-codex") == "cc-plugin-codex"


def test_distinct_match_warning(tmp_path):
    root = str(tmp_path)
    write_jsonl(
        os.path.join(root, "proj", "s1.jsonl"),
        [
            tool_use("x1", "mcp__alpha-srv__go", {}, "2026-07-01T00:00:00Z"),
            tool_result("x1", envelope("e"), "2026-07-01T00:00:01Z", is_error=True),
            tool_use("x2", "mcp__beta-srv__go", {}, "2026-07-01T00:01:00Z"),
            tool_result("x2", envelope("e"), "2026-07-01T00:01:01Z", is_error=True),
        ],
    )
    result = mea.audit(root, "srv", 3)
    text = mea.to_text(result)
    assert "WARNING" in text and "alpha-srv" in text and "beta-srv" in text
    data = json.loads(mea.to_json(result))
    assert data["distinct_matches"] == ["alpha-srv", "beta-srv"]
    # Same server across naming eras → no warning
    write_jsonl(
        os.path.join(root, "proj2", "s2.jsonl"),
        [
            tool_use(
                "y1", "mcp__plugin_alpha_alpha-srv__go", {}, "2026-07-02T00:00:00Z"
            ),
            tool_result("y1", "ok", "2026-07-02T00:00:01Z"),
        ],
    )
    result = mea.audit(root, "alpha-srv", 3)
    assert "WARNING" not in mea.to_text(result)
    assert "distinct_matches" not in json.loads(mea.to_json(result))


def test_matching_is_case_insensitive(tmp_path):
    root = build_audit_fixture(tmp_path)
    result = mea.audit(root, "SRV", 3)
    assert result["matched_servers"] == ["srv"]
    assert sum(result["total_calls"].values()) == 6


# --- version / fingerprint extraction ---------------------------------------


def test_extract_version_precedence():
    # meta.server_version wins over every other shape
    obj = {
        "meta": {"server_version": "0.10.0", "server": {"version": "9.9.9"}},
        "server_version": "8.8.8",
        "server": {"version": "7.7.7"},
    }
    assert mea.extract_version(obj) == "0.10.0"
    # top-level server_version is next (46% of real results carry no `meta`)
    assert mea.extract_version({"server_version": "0.9.0"}) == "0.9.0"
    # then the nested object forms
    assert mea.extract_version({"meta": {"server": {"version": "0.8.0"}}}) == "0.8.0"
    assert mea.extract_version({"server": {"version": "0.7.0"}}) == "0.7.0"
    assert mea.extract_version({}) is None


def test_extract_version_ignores_unrelated_version_keys():
    """Negative control: the regex trap.

    codex-in-claude emits `codex_version` — the version of the Codex CLI it shells
    out to, NOT its own. A heuristic matching version-ish key names would report on
    the wrong software. Extraction must be exact-path only.
    """
    obj = {
        "codex_version": "codex-cli 0.144.1",
        "cache_client_version": "0.144.1",
        "version_supported": True,
        "meta": {"fingerprint": "srv/0.1/schema-38"},
    }
    assert mea.extract_version(obj) is None


def test_extract_fingerprint_both_shapes():
    assert mea.extract_fingerprint({"meta": {"fingerprint": "a/1"}}) == "a/1"
    assert mea.extract_fingerprint({"fingerprint": "b/2"}) == "b/2"
    assert mea.extract_fingerprint({"meta": {"fingerprint": "a/1"}, "fingerprint": "b/2"}) == "a/1"
    assert mea.extract_fingerprint({}) is None


def test_extract_rejects_non_string_and_empty():
    assert mea.extract_version({"server_version": ""}) is None
    assert mea.extract_version({"server_version": 10}) is None
    assert mea.extract_version({"meta": "not-a-dict"}) is None


# --- call records -----------------------------------------------------------


def test_records_attribute_version_and_fingerprint(tmp_path):
    root = str(tmp_path)
    body = json.dumps(
        {"ok": True, "meta": {"server_version": "0.10.0", "fingerprint": "srv/0.1/schema-38"}}
    )
    write_jsonl(
        os.path.join(root, "proj", "s1.jsonl"),
        [
            tool_use("t1", "mcp__srv__go", {"a": 1}, "2026-07-01T00:00:00Z"),
            tool_result("t1", body, "2026-07-01T00:00:01Z"),
        ],
    )
    recs = mea.records_for_file(os.path.join(root, "proj", "s1.jsonl"), root)
    assert len(recs) == 1
    assert recs[0].tool == "go"
    assert recs[0].version == "0.10.0"
    assert recs[0].fingerprint == "srv/0.1/schema-38"
    assert recs[0].unknown_reason is None
    assert recs[0].is_error is False


def test_records_unknown_reasons(tmp_path):
    root = str(tmp_path)
    write_jsonl(
        os.path.join(root, "proj", "s1.jsonl"),
        [
            # no paired result at all (aborted / interrupted)
            tool_use("t1", "mcp__srv__go", {}, "2026-07-01T00:00:00Z"),
            # a result with no JSON envelope
            tool_use("t2", "mcp__srv__go", {}, "2026-07-01T00:01:00Z"),
            tool_result("t2", "MCP error -32000: Connection closed", "2026-07-01T00:01:01Z", True),
            # a well-formed envelope that carries no version
            tool_use("t3", "mcp__srv__go", {}, "2026-07-01T00:02:00Z"),
            tool_result("t3", json.dumps({"ok": True, "data": 1}), "2026-07-01T00:02:01Z"),
        ],
    )
    recs = {r.input_id: r for r in mea.records_for_file(os.path.join(root, "proj", "s1.jsonl"), root)}
    assert recs["t1"].unknown_reason == "no_result"
    assert recs["t2"].unknown_reason == "unparseable_result"
    assert recs["t3"].unknown_reason == "not_emitted"
    assert all(r.version is None for r in recs.values())


def test_unparseable_error_is_still_an_error(tmp_path):
    """Attribution and classification are orthogonal.

    A transport drop carries no envelope, so its release is unknown — but it is still a
    REAL error and must stay in the error totals. Sweeping it into an 'unknown' bucket
    that reads as 'not an error' would erase genuine transport failures.
    """
    root = str(tmp_path)
    write_jsonl(
        os.path.join(root, "proj", "s1.jsonl"),
        [
            tool_use("t1", "mcp__srv__go", {}, "2026-07-01T00:00:00Z"),
            tool_result("t1", "MCP error -32000: Connection closed", "2026-07-01T00:00:01Z", True),
        ],
    )
    (rec,) = mea.records_for_file(os.path.join(root, "proj", "s1.jsonl"), root)
    assert rec.is_error is True
    assert rec.code == "transport_connection_closed"
    assert rec.version is None
    assert rec.unknown_reason == "unparseable_result"


def test_records_are_in_result_order_with_unpaired_last(tmp_path):
    root = str(tmp_path)
    ok = json.dumps({"ok": True, "meta": {"server_version": "1.0.0"}})
    write_jsonl(
        os.path.join(root, "proj", "s1.jsonl"),
        [
            tool_use("t1", "mcp__srv__go", {}, "2026-07-01T00:00:00Z"),
            tool_use("t2", "mcp__srv__go", {}, "2026-07-01T00:01:00Z"),
            tool_use("t3", "mcp__srv__go", {}, "2026-07-01T00:02:00Z"),  # never answered
            tool_result("t2", ok, "2026-07-01T00:03:00Z"),
            tool_result("t1", ok, "2026-07-01T00:04:00Z"),
        ],
    )
    recs = mea.records_for_file(os.path.join(root, "proj", "s1.jsonl"), root)
    assert [r.input_id for r in recs] == ["t2", "t1", "t3"]


def test_tool_use_without_id_is_counted_as_no_result(tmp_path):
    """A tool_use with valid MCP name but no id must be counted, not dropped.

    This is a regression test for the fix: previously dropped calls are now counted
    toward discovery-mode stats, recorded with unknown_reason='no_result'.
    """
    root = str(tmp_path)
    # Construct a tool_use dict without the "id" key
    tool_use_no_id = {
        "timestamp": "2026-07-01T00:00:00Z",
        "message": {
            "content": [{"type": "tool_use", "name": "mcp__srv__do_thing", "input": {}}]
        },
    }
    write_jsonl(
        os.path.join(root, "proj", "s1.jsonl"),
        [tool_use_no_id],
    )

    # Test 1: records_for_file returns a record with unknown_reason='no_result'
    recs = mea.records_for_file(os.path.join(root, "proj", "s1.jsonl"), root)
    assert len(recs) == 1
    assert recs[0].server == "srv"
    assert recs[0].tool == "do_thing"
    assert recs[0].unknown_reason == "no_result"

    # Test 2: discovery mode counts it in servers[srv].calls
    result = mea.audit(root, "srv", 3)
    srv_stat = result["servers"]["srv"]
    assert srv_stat.calls == 1  # Must be counted in discovery mode


# --- scope-aware recovery ---------------------------------------------------


def _versioned_error(code, version):
    return json.dumps(
        {"ok": False, "error": {"code": code, "message": "boom", "retryable": False},
         "meta": {"server_version": version}}
    )


def _versioned_ok(version):
    return json.dumps({"ok": True, "meta": {"server_version": version}})


def test_recovery_requires_the_same_version(tmp_path):
    """An error on v1 followed by a success on v2 is NOT recovery for v1.

    The environment changed underneath; the honest answer is indeterminate.
    """
    root = str(tmp_path)
    write_jsonl(
        os.path.join(root, "proj", "s1.jsonl"),
        [
            tool_use("t1", "mcp__srv__go", {}, "2026-07-01T00:00:00Z"),
            tool_result("t1", _versioned_error("boom_code", "1.0.0"), "2026-07-01T00:00:01Z", True),
            tool_use("t2", "mcp__srv__go", {}, "2026-07-01T00:01:00Z"),
            tool_result("t2", _versioned_ok("2.0.0"), "2026-07-01T00:01:01Z"),
        ],
    )
    result = mea.audit(root, "srv", 3)
    stat = result["codes"]["boom_code"]
    assert stat.recovered == 0
    assert stat.cross_version_success == 1


def test_recovery_counts_within_the_same_version(tmp_path):
    root = str(tmp_path)
    write_jsonl(
        os.path.join(root, "proj", "s1.jsonl"),
        [
            tool_use("t1", "mcp__srv__go", {}, "2026-07-01T00:00:00Z"),
            tool_result("t1", _versioned_error("boom_code", "1.0.0"), "2026-07-01T00:00:01Z", True),
            tool_use("t2", "mcp__srv__go", {}, "2026-07-01T00:01:00Z"),
            tool_result("t2", _versioned_ok("1.0.0"), "2026-07-01T00:01:01Z"),
        ],
    )
    stat = mea.audit(root, "srv", 3)["codes"]["boom_code"]
    assert stat.recovered == 1
    assert stat.cross_version_success == 0


def test_multi_version_folded_session_attributes_each_call(tmp_path):
    """A session can span an upgrade: the parent transcript and its subagent sidechain
    fold into ONE session, but each call keeps its own version."""
    root = str(tmp_path)
    write_jsonl(
        os.path.join(root, "proj", "abc.jsonl"),
        [
            tool_use("t1", "mcp__srv__go", {}, "2026-07-01T00:00:00Z"),
            tool_result("t1", _versioned_error("boom_code", "1.0.0"), "2026-07-01T00:00:01Z", True),
        ],
    )
    write_jsonl(
        os.path.join(root, "proj", "abc", "subagents", "agent-1.jsonl"),
        [
            tool_use("t2", "mcp__srv__go", {}, "2026-07-02T00:00:00Z"),
            tool_result("t2", _versioned_error("boom_code", "2.0.0"), "2026-07-02T00:00:01Z", True),
        ],
    )
    result = mea.audit(root, "srv", 3)
    stat = result["codes"]["boom_code"]
    assert stat.by_scope == collections.Counter({"1.0.0": 1, "2.0.0": 1})
    # one folded session, but two scopes within it — session counts must not imply
    # a single version
    assert len(result["audit_sessions"]) == 1
    assert stat.session_count() == 1
    assert stat.sessions == {("1.0.0", "proj/abc"), ("2.0.0", "proj/abc")}


# --- filters ----------------------------------------------------------------


def _two_version_corpus(root):
    write_jsonl(
        os.path.join(root, "proj", "s1.jsonl"),
        [
            tool_use("t1", "mcp__srv__go", {}, "2026-07-01T00:00:00Z"),
            tool_result("t1", _versioned_error("old_code", "1.0.0"), "2026-07-01T00:00:01Z", True),
            tool_use("t2", "mcp__srv__go", {}, "2026-07-10T00:00:00Z"),
            tool_result("t2", _versioned_error("new_code", "2.0.0"), "2026-07-10T00:00:01Z", True),
        ],
    )


def test_filter_by_server_version(tmp_path):
    root = str(tmp_path)
    _two_version_corpus(root)
    result = mea.audit(root, "srv", 3, mea.Filters(server_version="2.0.0"))
    assert set(result["codes"]) == {"new_code"}
    # POSITIVE CONTROL: the same filter must be able to surface the other version.
    # A filter that matches nothing and a filter that is broken look identical.
    other = mea.audit(root, "srv", 3, mea.Filters(server_version="1.0.0"))
    assert set(other["codes"]) == {"old_code"}


def test_filter_by_fingerprint(tmp_path):
    root = str(tmp_path)
    body = json.dumps(
        {"ok": False, "error": {"code": "fp_code", "message": "b", "retryable": False},
         "meta": {"fingerprint": "srv/0.1/schema-38"}}
    )
    write_jsonl(
        os.path.join(root, "proj", "s1.jsonl"),
        [
            tool_use("t1", "mcp__srv__go", {}, "2026-07-01T00:00:00Z"),
            tool_result("t1", body, "2026-07-01T00:00:01Z", True),
        ],
    )
    hit = mea.audit(root, "srv", 3, mea.Filters(fingerprint="srv/0.1/schema-38"))
    assert set(hit["codes"]) == {"fp_code"}
    miss = mea.audit(root, "srv", 3, mea.Filters(fingerprint="srv/0.1/schema-1"))
    assert set(miss["codes"]) == set()


def test_date_filter_uses_the_call_timestamp_not_the_result(tmp_path):
    """A call that STARTS inside the window is counted and classified even when its
    result lands outside it. Filtering the two records independently would drop the
    tool_use while keeping its result (or vice versa) and manufacture phantom
    `no_result` calls — corrupting the denominator."""
    root = str(tmp_path)
    write_jsonl(
        os.path.join(root, "proj", "s1.jsonl"),
        [
            # starts inside the window, answers after it
            tool_use("t1", "mcp__srv__go", {}, "2026-07-05T23:59:00Z"),
            tool_result("t1", _versioned_error("inside", "1.0.0"), "2026-07-06T00:30:00Z", True),
            # starts outside the window, answers inside it
            tool_use("t2", "mcp__srv__go", {}, "2026-07-04T00:00:00Z"),
            tool_result("t2", _versioned_error("outside", "1.0.0"), "2026-07-05T00:00:00Z", True),
        ],
    )
    result = mea.audit(root, "srv", 3, mea.Filters(since="2026-07-05", until="2026-07-05"))
    assert set(result["codes"]) == {"inside"}


def test_date_filter_excludes_undated_calls_as_missing_timestamp(tmp_path):
    root = str(tmp_path)
    rec_use = tool_use("t1", "mcp__srv__go", {}, "2026-07-05T00:00:00Z")
    del rec_use["timestamp"]  # an undated call cannot be placed in a window
    write_jsonl(
        os.path.join(root, "proj", "s1.jsonl"),
        [rec_use, tool_result("t1", _versioned_error("c", "1.0.0"), "2026-07-05T00:00:01Z", True)],
    )
    result = mea.audit(root, "srv", 3, mea.Filters(since="2026-07-05"))
    assert set(result["codes"]) == set()
    assert result["coverage"].unknown["missing_timestamp"] == 1


def test_unknown_only_and_exclude(tmp_path):
    root = str(tmp_path)
    write_jsonl(
        os.path.join(root, "proj", "s1.jsonl"),
        [
            tool_use("t1", "mcp__srv__go", {}, "2026-07-01T00:00:00Z"),
            tool_result("t1", _versioned_error("known", "1.0.0"), "2026-07-01T00:00:01Z", True),
            tool_use("t2", "mcp__srv__go", {}, "2026-07-01T00:02:00Z"),
            tool_result("t2", "Connection closed", "2026-07-01T00:02:01Z", True),
        ],
    )
    only = mea.audit(root, "srv", 3, mea.Filters(unknown="only"))
    assert set(only["codes"]) == {"transport_connection_closed"}
    excl = mea.audit(root, "srv", 3, mea.Filters(unknown="exclude"))
    assert set(excl["codes"]) == {"known"}
