"""Tests for mcp_error_audit.py, run via: uvx pytest plugins/mcp-error-audit/tests/ -q"""

import collections
import json
import os
import sys

import pytest

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
    # This fixture's envelopes carry NO version, so every call's scope is `unknown`.
    # This assertion previously read `stat.recovered == 1` — it encoded the bug that
    # unknown == unknown counts as a same-release recovery. Two calls that each carry
    # no version may be from different releases; the honest answer is indeterminate.
    assert stat.recovered == 0
    assert stat.cross_version_success == 1
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


def _pair(root, err_body, ok_body):
    write_jsonl(
        os.path.join(root, "proj", "s1.jsonl"),
        [
            tool_use("t1", "mcp__srv__go", {}, "2026-07-01T00:00:00Z"),
            tool_result("t1", err_body, "2026-07-01T00:00:01Z", True),
            tool_use("t2", "mcp__srv__go", {}, "2026-07-01T00:01:00Z"),
            tool_result("t2", ok_body, "2026-07-01T00:01:01Z"),
        ],
    )


def test_a_success_at_an_unknown_scope_is_never_recovery(tmp_path):
    """The spec: "A later success at a different OR UNKNOWN scope does not count as
    recovery." aggregate() compared pend_scope == scope, so two calls that BOTH carry no
    version compared equal — unknown == unknown — and the success was credited as a
    same-release recovery.

    Since nothing stamps a version yet, EVERY call today is version-unknown: scope-aware
    recovery was inert by default, `recov` silently reverted to the old unscoped
    semantics, and `cross` read a reassuring 0.
    """
    unknown_err = envelope("boom_code")  # well-formed envelope, no version
    unknown_ok = json.dumps({"ok": True})

    # unknown → unknown: NOT a recovery. Both calls could be from different releases.
    root = str(tmp_path / "uu")
    _pair(root, unknown_err, unknown_ok)
    stat = mea.audit(root, "srv", 3)["codes"]["boom_code"]
    assert stat.recovered == 0
    assert stat.cross_version_success == 1

    # known error → unknown success: the success proves nothing about 1.0.0.
    root = str(tmp_path / "ku")
    _pair(root, _versioned_error("boom_code", "1.0.0"), unknown_ok)
    stat = mea.audit(root, "srv", 3)["codes"]["boom_code"]
    assert stat.recovered == 0
    assert stat.cross_version_success == 1

    # unknown error → known success: the error is not "recovered" by anything.
    root = str(tmp_path / "uk")
    _pair(root, unknown_err, _versioned_ok("1.0.0"))
    stat = mea.audit(root, "srv", 3)["codes"]["boom_code"]
    assert stat.recovered == 0
    assert stat.cross_version_success == 1

    # POSITIVE CONTROL: recovery is still reachable — an attributed scope recovers itself.
    # (Otherwise `recovered == 0` above would be indistinguishable from a dead counter.)
    root = str(tmp_path / "kk")
    _pair(root, _versioned_error("boom_code", "1.0.0"), _versioned_ok("1.0.0"))
    stat = mea.audit(root, "srv", 3)["codes"]["boom_code"]
    assert stat.recovered == 1
    assert stat.cross_version_success == 0

    # And under --group-by fingerprint, a fingerprinted corpus recovers normally: the
    # rule is about UNATTRIBUTED scopes, not about versions specifically.
    root = str(tmp_path / "fp")
    fp_err = json.dumps(
        {"ok": False, "error": {"code": "boom_code", "message": "b", "retryable": False},
         "meta": {"fingerprint": "srv/0.1/schema-38"}}
    )
    fp_ok = json.dumps({"ok": True, "meta": {"fingerprint": "srv/0.1/schema-38"}})
    _pair(root, fp_err, fp_ok)
    by_fp = mea.audit(root, "srv", 3, mea.Filters(group_by="fingerprint"))["codes"]["boom_code"]
    assert by_fp.recovered == 1
    by_ver = mea.audit(root, "srv", 3, mea.Filters(group_by="version"))["codes"]["boom_code"]
    assert by_ver.recovered == 0  # same corpus, no version: indeterminate
    assert by_ver.cross_version_success == 1


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
    # Build a corpus with both dated and undated calls inside the window
    records = [
        # dated call inside the window
        tool_use("t1", "mcp__srv__go", {}, "2026-07-05T00:00:00Z"),
        tool_result("t1", _versioned_error("dated_error", "1.0.0"), "2026-07-05T00:00:01Z", True),
        # dated call inside the window that succeeds
        tool_use("t2", "mcp__srv__go", {}, "2026-07-05T10:00:00Z"),
        tool_result("t2", _versioned_ok("1.0.0"), "2026-07-05T10:00:01Z"),
    ]
    # undated call cannot be placed in a window
    rec_use = tool_use("t3", "mcp__srv__go", {}, "2026-07-05T12:00:00Z")
    del rec_use["timestamp"]
    records.extend([rec_use, tool_result("t3", _versioned_error("undated_error", "1.0.0"), "2026-07-05T12:00:01Z", True)])

    write_jsonl(os.path.join(root, "proj", "s1.jsonl"), records)
    result = mea.audit(root, "srv", 3, mea.Filters(since="2026-07-05"))

    # Verify the invariant: total_calls == attributed_calls + sum(unknown.values())
    cov = result["coverage"]
    assert cov.total_calls == cov.attributed_calls + sum(cov.unknown.values()), \
        f"Invariant broken: {cov.total_calls} != {cov.attributed_calls} + {sum(cov.unknown.values())}"

    # Verify the undated call is excluded but still counted in total_calls
    assert cov.unknown["missing_timestamp"] == 1
    assert cov.total_calls == 3  # 2 dated + 1 undated

    # Verify the dated calls are still counted and attributed
    assert cov.attributed_calls == 2  # only dated calls have versions

    # Verify the error from the dated call is in the codes (undated error is excluded)
    assert set(result["codes"]) == {"dated_error"}


def _fingerprint_only_corpus(root):
    """The real corpus today: every call carries a fingerprint, none carries a version."""
    fp_err = json.dumps(
        {"ok": False, "error": {"code": "fp_code", "message": "b", "retryable": False},
         "meta": {"fingerprint": "srv/0.1/schema-38"}}
    )
    fp_ok = json.dumps({"ok": True, "meta": {"fingerprint": "srv/0.1/schema-38"}})
    write_jsonl(
        os.path.join(root, "proj", "s1.jsonl"),
        [
            tool_use("t1", "mcp__srv__go", {}, "2026-07-01T00:00:00Z"),
            tool_result("t1", fp_err, "2026-07-01T00:00:01Z", True),
            tool_use("t2", "mcp__srv__go", {}, "2026-07-01T00:01:00Z"),
            tool_result("t2", fp_ok, "2026-07-01T00:01:01Z"),
        ],
    )


def test_unknown_filter_is_dimension_aware(tmp_path):
    """`--unknown` must judge unknown-ness on the ACTIVE dimension, exactly as Coverage does.

    Filters.selects() tested rec.unknown_reason ("was there any envelope?") while Coverage
    tested scope != UNKNOWN ("does this call carry a value for the dimension I am grouping
    by?"). On a fingerprint-only corpus — which is the real corpus today, since no server
    stamps a version yet — the two definitions diverge completely:

      --group-by version --unknown exclude  was a silent NO-OP (nothing has an unknown_reason)
      --group-by version --unknown only     returned ZERO calls, while coverage in the same
                                            run reported those very calls as unattributed.

    The spec says `only` exists to make the unknown bucket inspectable rather than a silent
    drain. One definition, dimension-aware, everywhere.
    """
    root = str(tmp_path)
    _fingerprint_only_corpus(root)

    # Grouping by VERSION: nothing carries a version, so every call is unknown.
    v_only = mea.audit(root, "srv", 3, mea.Filters(group_by="version", unknown="only"))
    assert v_only["coverage"].total_calls == 2
    assert set(v_only["codes"]) == {"fp_code"}  # the unknown bucket is inspectable
    assert v_only["coverage"].attributed_calls == 0

    v_excl = mea.audit(root, "srv", 3, mea.Filters(group_by="version", unknown="exclude"))
    assert v_excl["coverage"].total_calls == 0  # was 2: a silent no-op
    assert set(v_excl["codes"]) == set()

    # Grouping by FINGERPRINT: every call carries one, so the buckets invert.
    f_only = mea.audit(root, "srv", 3, mea.Filters(group_by="fingerprint", unknown="only"))
    assert f_only["coverage"].total_calls == 0
    f_excl = mea.audit(root, "srv", 3, mea.Filters(group_by="fingerprint", unknown="exclude"))
    assert f_excl["coverage"].total_calls == 2
    assert f_excl["coverage"].attributed_calls == 2

    # The invariant holds on every one of these paths.
    for res in (v_only, v_excl, f_only, f_excl):
        cov = res["coverage"]
        assert cov.total_calls == cov.attributed_calls + sum(cov.unknown.values())

    # POSITIVE CONTROL: `exclude` under group_by=version is not vacuously empty because
    # the filter is broken — the same flag surfaces calls when a version IS present.
    _two_version_corpus(str(tmp_path / "versioned"))
    ok = mea.audit(str(tmp_path / "versioned"), "srv", 3,
                   mea.Filters(group_by="version", unknown="exclude"))
    assert ok["coverage"].total_calls == 2


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


# --- coverage and the matrix ------------------------------------------------


def test_coverage_attribution_is_dimension_aware(tmp_path):
    """Coverage attribution must be relative to the ACTIVE group_by dimension.

    A call whose envelope carries only a fingerprint (no server_version) is a
    perfectly well-formed result — but it is NOT attributed under
    group_by='version', even though rec.unknown_reason is None (an envelope was
    present). Regression for: the text report claimed calls were "attributed to
    a version" while the by-version matrix showed every one of them at scope
    'unknown' — an outright contradiction. Real-corpus shape: servers that only
    ever emit a fingerprint, never a server_version.
    """
    root = str(tmp_path)
    fp_only = json.dumps(
        {
            "ok": False,
            "error": {"code": "fp_code", "message": "b", "retryable": False},
            "meta": {"fingerprint": "srv/0.1/schema-38"},
        }
    )
    write_jsonl(
        os.path.join(root, "proj", "s1.jsonl"),
        [
            tool_use("t1", "mcp__srv__go", {}, "2026-07-01T00:00:00Z"),
            tool_result("t1", fp_only, "2026-07-01T00:00:01Z", True),
            tool_use("t2", "mcp__srv__go", {}, "2026-07-01T00:01:00Z"),
            tool_result("t2", fp_only, "2026-07-01T00:01:01Z", True),
        ],
    )

    by_version = mea.audit(root, "srv", 3, mea.Filters(group_by="version"))
    cov_v = by_version["coverage"]
    assert cov_v.attributed_calls == 0
    assert cov_v.unknown == {"not_emitted": 2}
    assert cov_v.total_calls == cov_v.attributed_calls + sum(cov_v.unknown.values())
    # The matrix must AGREE with coverage: every call's version-scope is unknown.
    assert sorted({sc for s in by_version["codes"].values() for sc in s.by_scope}) == [
        "unknown"
    ]

    by_fp = mea.audit(root, "srv", 3, mea.Filters(group_by="fingerprint"))
    cov_fp = by_fp["coverage"]
    assert cov_fp.attributed_calls == 2
    assert cov_fp.unknown == {}
    assert cov_fp.total_calls == cov_fp.attributed_calls + sum(cov_fp.unknown.values())
    assert sorted(
        {sc for s in by_fp["codes"].values() for sc in s.by_scope}
    ) == ["srv/0.1/schema-38"]


def test_rate_uses_attributed_calls_and_flags_partial(tmp_path):
    root = str(tmp_path)
    write_jsonl(
        os.path.join(root, "proj", "s1.jsonl"),
        [
            tool_use("t1", "mcp__srv__go", {}, "2026-07-01T00:00:00Z"),
            tool_result("t1", _versioned_error("known", "1.0.0"), "2026-07-01T00:00:01Z", True),
            tool_use("t2", "mcp__srv__go", {}, "2026-07-01T00:01:00Z"),
            tool_result("t2", "Connection closed", "2026-07-01T00:01:01Z", True),
        ],
    )
    result = mea.audit(root, "srv", 3)
    cov = result["coverage"]
    assert cov.total_calls == 2
    assert cov.attributed_calls == 1
    assert cov.unknown["unparseable_result"] == 1
    assert cov.partial() is True

    data = json.loads(mea.to_json(result))
    assert data["coverage"]["attributed_calls"] == 1
    assert data["coverage"]["partial"] is True
    text = mea.to_text(result)
    assert "partial" in text.lower()


def _mixed_attribution_corpus(root):
    """1.0.0: 2 calls, 1 error (50%). No envelope: 3 calls, 1 error (33.3%).

    Overall: 2 errors / 5 calls = 40.0% — a figure that belongs to NO release.
    """
    records = [
        tool_use("t1", "mcp__srv__go", {}, "2026-07-01T00:00:00Z"),
        tool_result("t1", _versioned_error("known", "1.0.0"), "2026-07-01T00:00:01Z", True),
        tool_use("t2", "mcp__srv__go", {}, "2026-07-01T00:01:00Z"),
        tool_result("t2", _versioned_ok("1.0.0"), "2026-07-01T00:01:01Z"),
        tool_use("t3", "mcp__srv__go", {}, "2026-07-01T00:02:00Z"),
        tool_result("t3", "Connection closed", "2026-07-01T00:02:01Z", True),
        tool_use("t4", "mcp__srv__go", {}, "2026-07-01T00:03:00Z"),
        tool_result("t4", "plain text, no envelope", "2026-07-01T00:03:01Z"),
        tool_use("t5", "mcp__srv__go", {}, "2026-07-01T00:04:00Z"),
        tool_result("t5", "plain text, no envelope", "2026-07-01T00:04:01Z"),
    ]
    write_jsonl(os.path.join(root, "proj", "s1.jsonl"), records)


def test_header_rate_is_labeled_as_spanning_all_calls(tmp_path):
    """The ONE rate printed was errors/ALL calls, under a note claiming the opposite.

    The header rate (2/5 = 40.0%) includes unattributed calls. It is fine to print —
    but only labeled as what it is. It must NOT sit under a note asserting that "rates
    below are computed over attributed calls only", because that describes a
    computation the report never performed.
    """
    root = str(tmp_path)
    _mixed_attribution_corpus(root)
    text = mea.to_text(mea.audit(root, "srv", 3))
    header = next(ln for ln in text.splitlines() if ln.startswith("scanned "))
    assert "2 errors (40.0% of all matched calls, attributed or not)" in header
    # The old note claimed the rates were attributed-only. They were not, and there
    # were no per-scope rates at all. That exact sentence must be gone.
    assert "computed over attributed calls only" not in text


def test_per_scope_rates_use_that_scopes_own_denominator(tmp_path):
    """Every per-scope rate divides by THAT scope's calls — never the global count."""
    root = str(tmp_path)
    _mixed_attribution_corpus(root)
    result = mea.audit(root, "srv", 3)
    text = mea.to_text(result)
    lines = text.splitlines()
    header_idx = lines.index("## Errors by code × version")

    calls_row = next(ln for ln in lines[header_idx + 1 :] if ln.startswith("calls"))
    rate_row = next(ln for ln in lines[header_idx + 1 :] if ln.startswith("err%"))
    assert calls_row.split() == ["calls", "2", "3"]
    # 1/2 at 1.0.0 and 1/3 unattributed. Neither is the global 40.0%.
    assert rate_row.split() == ["err%", "50.0%", "33.3%"]

    assert mea.scope_rate(result, "1.0.0") == "50.0%"
    data = json.loads(mea.to_json(result))
    assert data["coverage"]["calls_by_scope"] == {"1.0.0": 2, "unknown": 3}
    assert data["coverage"]["errors_by_scope"] == {"1.0.0": 1, "unknown": 1}


def test_partial_note_describes_what_the_report_actually_does(tmp_path):
    """The PARTIAL note must describe the report's real computation, not a fiction."""
    root = str(tmp_path)
    _mixed_attribution_corpus(root)
    text = mea.to_text(mea.audit(root, "srv", 3))
    note = next(ln for ln in text.splitlines() if ln.startswith("NOTE: PARTIAL"))
    assert "3 calls could not be attributed to a version" in note
    # It must point at the real mechanism: per-column denominators, unknown isolated.
    assert "own denominator" in note and "unknown" in note

    # And when EVERY call is attributed, there is nothing partial to warn about.
    clean = str(tmp_path / "clean")
    _clean_release_corpus(clean)
    assert "PARTIAL" not in mea.to_text(mea.audit(clean, "srv", 3))


def test_matrix_shows_code_by_version(tmp_path):
    root = str(tmp_path)
    _two_version_corpus(root)
    result = mea.audit(root, "srv", 3)
    data = json.loads(mea.to_json(result))
    assert data["by_code"]["old_code"]["by_scope"] == {"1.0.0": 1}
    assert data["by_code"]["new_code"]["by_scope"] == {"2.0.0": 1}

    text = mea.to_text(result)
    # Structure unique to the matrix: its section header must be present.
    assert "## Errors by code × version" in text
    lines = text.splitlines()
    header_idx = lines.index("## Errors by code × version")
    header_row = lines[header_idx + 1]
    # Header row carries exactly the two scope labels, in order (tokenized by
    # whitespace: if columns ran together, this would collapse to one merged
    # token instead of two).
    assert header_row.split() == ["code", "1.0.0", "2.0.0"]

    old_code_row = next(ln for ln in lines[header_idx + 2 :] if ln.startswith("old_code"))
    # old_code was only observed at 1.0.0 — its 2.0.0 column must show the
    # NOT-OBSERVED zero, not be missing or blank.
    assert old_code_row.split() == ["old_code", "1", "0"]

    new_code_row = next(ln for ln in lines[header_idx + 2 :] if ln.startswith("new_code"))
    assert new_code_row.split() == ["new_code", "0", "1"]


def _clean_release_corpus(root):
    """1.0.0: one error, no clean calls. 2.0.0: twenty clean calls, zero errors."""
    records = [
        tool_use("t0", "mcp__srv__go", {}, "2026-07-01T00:00:00Z"),
        tool_result("t0", _versioned_error("old_code", "1.0.0"), "2026-07-01T00:00:01Z", True),
    ]
    for i in range(20):
        records.append(tool_use(f"v{i}", "mcp__srv__go", {}, f"2026-07-10T00:{i:02d}:00Z"))
        records.append(tool_result(f"v{i}", _versioned_ok("2.0.0"), f"2026-07-10T00:{i:02d}:01Z"))
    write_jsonl(os.path.join(root, "proj", "s1.jsonl"), records)


def test_calls_are_counted_by_scope_not_only_errors(tmp_path):
    """The denominator for 'not observed in 2.0.0 over N attributed calls' must EXIST.

    aggregate() counted calls only by tool, and CodeStat.by_scope counted only errors,
    so there was no per-scope call denominator anywhere. The flagship sentence in the
    spec was literally uncomputable.
    """
    root = str(tmp_path)
    _clean_release_corpus(root)
    cov = mea.audit(root, "srv", 3)["coverage"]
    assert cov.calls_by_scope == collections.Counter({"1.0.0": 1, "2.0.0": 20})
    assert cov.total_calls == 21
    assert cov.attributed_calls == 21
    assert cov.total_calls == cov.attributed_calls + sum(cov.unknown.values())

    data = json.loads(mea.to_json(mea.audit(root, "srv", 3)))
    assert data["coverage"]["calls_by_scope"] == {"1.0.0": 1, "2.0.0": 20}


def test_zero_error_release_still_gets_a_matrix_column(tmp_path):
    """A release with 20 clean calls and zero errors must APPEAR in the matrix.

    Columns were derived from ERROR scopes, so the release the user is actually
    running — clean so far — did not appear at all, and the audit said nothing about
    the one question it exists to answer.
    """
    root = str(tmp_path)
    _clean_release_corpus(root)
    text = mea.to_text(mea.audit(root, "srv", 3))
    lines = text.splitlines()
    header_idx = lines.index("## Errors by code × version")
    header_row = lines[header_idx + 1]
    assert header_row.split() == ["code", "1.0.0", "2.0.0"]

    old_row = next(ln for ln in lines[header_idx + 2 :] if ln.startswith("old_code"))
    assert old_row.split() == ["old_code", "1", "0"]

    # The per-column denominator: the N in "not observed in 2.0.0 over N calls".
    calls_row = next(ln for ln in lines[header_idx + 2 :] if ln.startswith("calls"))
    assert calls_row.split() == ["calls", "1", "20"]


def test_text_report_says_not_observed_never_fixed(tmp_path):
    root = str(tmp_path)
    _two_version_corpus(root)
    text = mea.to_text(mea.audit(root, "srv", 3, mea.Filters(server_version="2.0.0")))
    assert "fixed" not in text.lower()
    # The NOT-OBSERVED caveat itself must be present, not merely absent of "fixed".
    assert "NOT OBSERVED" in text
    assert "not a fix" in text


def test_matrix_aligns_columns_for_long_fingerprint_labels(tmp_path):
    """Finding: at fixed width-14 columns, a fingerprint label like
    'codex-in-claude/0.1/schema-38' (30 chars) overruns its column and runs
    into the next header/value, breaking alignment. The matrix must size
    columns to their content instead.
    """
    root = str(tmp_path)
    fp1 = "codex-in-claude/0.1/schema-38"
    fp2 = "codex-in-claude/2.0/schema-99"

    def fp_error(code, fp):
        return json.dumps(
            {
                "ok": False,
                "error": {"code": code, "message": "boom", "retryable": False},
                "meta": {"fingerprint": fp},
            }
        )

    write_jsonl(
        os.path.join(root, "proj", "s1.jsonl"),
        [
            tool_use("t1", "mcp__srv__go", {}, "2026-07-01T00:00:00Z"),
            tool_result("t1", fp_error("old_code", fp1), "2026-07-01T00:00:01Z", True),
            tool_use("t2", "mcp__srv__go", {}, "2026-07-02T00:00:00Z"),
            tool_result("t2", fp_error("old_code", fp2), "2026-07-02T00:00:01Z", True),
        ],
    )
    result = mea.audit(root, "srv", 3, mea.Filters(group_by="fingerprint"))
    text = mea.to_text(result)

    assert "## Errors by code × fingerprint" in text
    lines = text.splitlines()
    header_idx = lines.index("## Errors by code × fingerprint")
    header_row = lines[header_idx + 1]
    data_row = next(ln for ln in lines[header_idx + 2 :] if ln.startswith("old_code"))
    calls_row = next(ln for ln in lines[header_idx + 2 :] if ln.startswith("calls"))

    assert header_row.split() == ["code", fp1, fp2]
    assert data_row.split() == ["old_code", "1", "1"]

    # The assertions above are NOT enough on their own: .split() is invariant to column
    # width, because there is always at least one space separating the fields. They pass
    # unchanged with `col_width = 14` reintroduced. Alignment is a property of the
    # RENDERED WIDTH, so pin that: every row of the matrix must be exactly as wide as
    # every other, which is true iff each column is sized to its widest label.
    # Under the bug the 29-char labels overrun their 14-char cells and the header row
    # renders 94 chars against a 64-char data row.
    widths = {len(header_row), len(data_row), len(calls_row)}
    assert widths == {len(header_row)}, (
        f"matrix rows are ragged — columns are not sized to their labels: "
        f"header={len(header_row)} data={len(data_row)} calls={len(calls_row)}"
    )
    # And the columns land where the labels do: each count's right edge sits under the
    # right edge of its own header label.
    for i, fp in enumerate((fp1, fp2)):
        col_end = header_row.index(fp) + len(fp)
        assert data_row[:col_end].rstrip().endswith("1")
        assert len(data_row[:col_end].rstrip()) == col_end, (
            f"column {i} ({fp}) count is not right-aligned under its header label"
        )



# --- CLI / slash-command argument passing -----------------------------------


def test_args_string_round_trips_flags():
    """The slash command hands the script ONE raw string. It must reach the parser as
    flags — this is the test that catches the '--server' interpolation blocker."""
    args = mea.parse_argv(["--args", "codex --server-version 0.10.0 --group-by version"])
    assert args.server == "codex"
    assert args.server_version == "0.10.0"
    assert args.group_by == "version"


def test_args_string_bare_server_and_empty():
    assert mea.parse_argv(["--args", "codex"]).server == "codex"
    assert mea.parse_argv(["--args", ""]).server == ""  # discovery mode


def test_args_string_rejects_quotes():
    """A stray quote in --args would otherwise produce a confusing shlex parse.

    This is NOT a security control. It does not prevent shell injection and cannot:
    bash tokenizes the interpolated $ARGUMENTS in commands/mcp-error-audit.md before
    python starts, so a payload like `x' ; echo pwned #` reaches python as argv
    ['--args', 'x'] — no quote ever arrives at this check. `allowed-tools` does not
    guard it either: it is a PREFIX match, which the injected string still satisfies.
    The risk predates this branch and is knowingly accepted; see parse_argv().
    """
    with pytest.raises(SystemExit):
        mea.parse_argv(["--args", "codex --since '2026-07-12'"])


def test_args_string_trailing_backslash_is_a_clean_error():
    """A trailing backslash with no quotes passes the quote check but makes
    shlex.split raise ValueError('No escaped character'). That must surface as a
    clean parser.error (SystemExit, exit code 2), not an uncaught traceback."""
    with pytest.raises(SystemExit) as exc_info:
        mea.parse_argv(["--args", "codex --since 2026-07-12\\"])
    assert exc_info.value.code == 2


def test_unknown_only_with_server_version_is_rejected():
    """--unknown only paired with --server-version is vacuous by construction: an
    unattributed record can never equal a specific observed version, so the result
    is always empty regardless of corpus. Reject it at validation time instead of
    silently reporting zero results."""
    with pytest.raises(SystemExit):
        mea.parse_argv(["--args", "codex --unknown only --server-version 0.10.0"])


def test_unknown_only_with_fingerprint_is_rejected():
    with pytest.raises(SystemExit):
        mea.parse_argv(["--args", "codex --unknown only --fingerprint srv/0.1/schema-38"])


def test_fingerprint_validation_allows_slashes_server_does_not():
    args = mea.parse_argv(["--args", "codex --fingerprint codex-in-claude/0.1/schema-38"])
    assert args.fingerprint == "codex-in-claude/0.1/schema-38"
    with pytest.raises(SystemExit):
        mea.parse_argv(["--args", "codex/evil"])  # server token stays strict


def test_date_validation_rejects_non_iso():
    with pytest.raises(SystemExit):
        mea.parse_argv(["--args", "codex --since july"])


def test_discovery_output_is_unchanged_by_version_work(tmp_path):
    """Golden guard: discovery keeps its all-call denominator and stays unversioned."""
    root = str(tmp_path)
    _two_version_corpus(root)
    data = json.loads(mea.to_json(mea.audit(root, "", 3)))
    assert data["mode"] == "discovery"
    assert data["servers"]["srv"]["calls"] == 2
    assert data["servers"]["srv"]["errors"] == 2
    assert data["servers"]["srv"]["error_rate"] == 1.0
    assert "coverage" not in data


# --- "no calls matched" message must distinguish its two causes -------------


def test_no_such_server_message_unchanged(tmp_path):
    """Genuinely no such server: the original, unqualified message."""
    root = str(tmp_path)
    write_jsonl(
        os.path.join(root, "proj", "s1.jsonl"),
        [
            tool_use("t1", "mcp__srv__go", {}, "2026-07-01T00:00:00Z"),
            tool_result("t1", _versioned_error("boom", "1.0.0"), "2026-07-01T00:00:01Z", True),
        ],
    )
    result = mea.audit(root, "nosuchserver", 3)
    text = mea.to_text(result)
    assert (
        "No MCP tool calls matched. Run without --server to list "
        "the servers seen in transcripts." in text
    )


def test_filter_excludes_everything_message_names_the_filter(tmp_path):
    """The server matched calls; a scope filter excluded all of them. The message
    must say so and name the filter — NOT imply the server name was wrong.

    Regression for: `--server-version 0.10.0` on a corpus with no versions at all
    printed the exact same "no such server" message as a genuinely bad server name,
    sending the user to fix the wrong thing.
    """
    root = str(tmp_path)
    write_jsonl(
        os.path.join(root, "proj", "s1.jsonl"),
        [
            tool_use("t1", "mcp__srv__go", {}, "2026-07-01T00:00:00Z"),
            tool_result("t1", _versioned_error("boom", "1.0.0"), "2026-07-01T00:00:01Z", True),
            tool_use("t2", "mcp__srv__go", {}, "2026-07-01T00:01:00Z"),
            tool_result("t2", _versioned_error("boom", "1.0.0"), "2026-07-01T00:01:01Z", True),
        ],
    )
    result = mea.audit(root, "srv", 3, mea.Filters(server_version="9.9.9"))
    text = mea.to_text(result)
    assert "No MCP tool calls matched" not in text
    assert "matched 2 calls for this server, but 0 after scoping to" in text
    assert "server-version 9.9.9" in text
