import json

import anyio
import pytest
from fastmcp import Client

from cc_plugin_codex.server import CAPABILITY_SUMMARY, mcp
from cc_plugin_codex.server import _first_root, _resolve_workspace
from tests.conftest import structured

PAID_TOOLS = ("claude_ask", "claude_review_changes", "claude_adversarial_review")


class _FakeRoots:
    """Minimal stand-in for a FastMCP Context exposing list_roots()."""
    def __init__(self, uris=None, raises=False):
        self._uris = uris or []
        self._raises = raises

    async def list_roots(self):
        if self._raises:
            raise RuntimeError("client does not support roots")
        return [type("R", (), {"uri": u})() for u in self._uris]


async def test_first_root_returns_path_from_file_uri():
    ctx = _FakeRoots(["file:///home/me/project"])
    assert await _first_root(ctx) == "/home/me/project"


async def test_first_root_none_when_unsupported():
    assert await _first_root(_FakeRoots(raises=True)) is None


async def test_first_root_skips_non_file_uris():
    ctx = _FakeRoots(["https://example.com/x", "file:///ok"])
    assert await _first_root(ctx) == "/ok"


async def test_resolve_workspace_param_beats_roots(tmp_path):
    ctx = _FakeRoots(["file:///should/not/win"])
    path, err, source = await _resolve_workspace(str(tmp_path), ctx)
    assert err is None
    assert path == str(tmp_path)
    assert source == "param"


async def test_resolve_workspace_uses_roots_when_no_param(tmp_path):
    ctx = _FakeRoots([f"file://{tmp_path}"])
    path, err, source = await _resolve_workspace(None, ctx)
    assert err is None
    assert path == str(tmp_path)
    assert source == "roots"


async def test_resolve_workspace_falls_back_to_cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    path, err, source = await _resolve_workspace(None, _FakeRoots(raises=True))
    assert err is None
    assert path == str(tmp_path)
    assert source == "cwd"


async def test_resolve_workspace_rejects_nonexistent_param():
    path, err, source = await _resolve_workspace("/no/such/dir/xyz", _FakeRoots())
    assert path is None
    assert err == "invalid_workspace_root"


async def test_resolve_workspace_rejects_relative_param(tmp_path, monkeypatch):
    # A relative workspace_root must be rejected — it would resolve against the
    # untrusted cwd that workspace resolution exists to bypass.
    monkeypatch.chdir(tmp_path)
    (tmp_path / "sub").mkdir()
    path, err, source = await _resolve_workspace("sub", _FakeRoots())
    assert path is None
    assert err == "invalid_workspace_root"


async def _tools_by_name():
    async with Client(mcp) as client:
        return {t.name: t for t in await client.list_tools()}


async def test_list_tools():
    names = set(await _tools_by_name())
    assert {"claude_ask", "claude_review_changes",
            "claude_adversarial_review", "claude_status"} <= names


async def test_tools_publish_real_output_schema():
    # F1: the ok-discriminated contract must be in the schema, not just prose.
    tools = await _tools_by_name()
    for name in (*PAID_TOOLS, "claude_status"):
        schema = tools[name].outputSchema
        assert schema is not None
        assert schema != {"additionalProperties": True, "type": "object"}, name
        assert schema.get("type") == "object", name
        assert '"ok"' in json.dumps(schema), name


async def test_paid_tool_output_schema_describes_both_outcomes():
    # F1: success and error shapes are both discoverable from the schema.
    schema = (await _tools_by_name())["claude_ask"].outputSchema
    blob = json.dumps(schema)
    assert "summary" in blob and "verdict" in blob   # success branch
    assert "error" in blob and "repair" in blob       # error branch


async def test_fixed_value_inputs_use_enums():
    # F2: choices are JSON Schema enums, not prose like "inherit|scoped|bare".
    props = (await _tools_by_name())["claude_review_changes"].inputSchema["properties"]
    assert props["scope"]["enum"] == ["working_tree", "staged", "branch"]
    assert props["detail"]["enum"] == ["summary", "full"]

    def _enum_in_anyof(prop):
        for branch in prop.get("anyOf", []):
            if "enum" in branch:
                return branch["enum"]
        return prop.get("enum")

    assert _enum_in_anyof(props["config_mode"]) == ["inherit", "scoped", "bare"]
    assert _enum_in_anyof(props["access"]) == ["toolless", "readonly"]


async def test_tools_have_titles():
    # F8: human-facing title for mixed human/agent pickers.
    tools = await _tools_by_name()
    for name in (*PAID_TOOLS, "claude_status"):
        assert tools[name].title, name


async def test_capability_summary_declares_tier_and_blocking():
    # F9 stability tier + F4 blocking/cancel disclosure.
    summary = CAPABILITY_SUMMARY.lower()
    assert "experimental" in summary
    assert "cancel" in summary


async def test_review_tool_documents_scope_codes_ask_does_not():
    # F6: scope/diff codes belong only on diff-bearing tools.
    tools = await _tools_by_name()
    assert "invalid_scope" in tools["claude_review_changes"].description
    assert "invalid_scope" not in tools["claude_ask"].description


async def test_status_reports_config_modes(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    async with Client(mcp) as client:
        result = await client.call_tool("claude_status", {})
    data = structured(result)
    assert "config_modes_available" in data
    assert data["config_modes_available"]["bare"] is False


async def test_claude_ask_returns_normalized(fake_claude):
    async with Client(mcp) as client:
        result = await client.call_tool("claude_ask", {"prompt": "is this safe?"})
    data = structured(result)
    assert data["ok"] is True
    assert data["verdict"] == "concerns"
    assert data["meta"]["fingerprint"] == "cc-plugin-codex/0.1/schema-6"


async def test_invalid_enum_param_rejected_by_schema(fake_claude):
    # F2: invalid enum values are rejected at the schema boundary (clients can
    # validate locally) rather than round-tripping to a structured error.
    async with Client(mcp) as client:
        with pytest.raises(Exception) as exc:
            await client.call_tool("claude_ask", {"prompt": "x", "config_mode": "bogus"})
    assert "inherit" in str(exc.value)


async def test_bogus_env_config_mode_is_structured_error(fake_claude, monkeypatch):
    # The structured unsupported_config_mode path is still reachable via a bad
    # env default (not a schema-validated parameter).
    monkeypatch.setenv("CC_PLUGIN_CODEX_CLAUDE_CONFIG", "bogus")
    async with Client(mcp) as client:
        result = await client.call_tool("claude_ask", {"prompt": "x"},
                                        raise_on_error=False)
    # F3: error envelope rides on a native is_error result, not a "success".
    assert result.is_error is True
    data = structured(result)
    assert data["ok"] is False
    assert data["error"]["code"] == "unsupported_config_mode"


async def test_bogus_env_access_is_structured_error(fake_claude, monkeypatch):
    monkeypatch.setenv("CC_PLUGIN_CODEX_ACCESS", "bogus")
    async with Client(mcp) as client:
        result = await client.call_tool("claude_ask", {"prompt": "x"},
                                        raise_on_error=False)
    data = structured(result)
    assert data["error"]["code"] == "unsupported_access"


async def test_bare_without_api_key_errors(fake_claude, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    async with Client(mcp) as client:
        result = await client.call_tool(
            "claude_ask", {"prompt": "x", "config_mode": "bare"}, raise_on_error=False)
    data = structured(result)
    assert data["error"]["code"] == "api_key_required"


async def test_success_response_carries_request_id(fake_claude):
    # F7: successful responses also carry a correlation id in meta.
    async with Client(mcp) as client:
        result = await client.call_tool("claude_ask", {"prompt": "is this safe?"})
    assert structured(result)["meta"]["request_id"]


async def test_status_reports_resolved_defaults(monkeypatch):
    # F5: agents can see the env-driven defaults a no-arg paid call would use.
    monkeypatch.setenv("CC_PLUGIN_CODEX_CLAUDE_CONFIG", "scoped")
    monkeypatch.setenv("CC_PLUGIN_CODEX_MAX_BUDGET_USD", "99")  # above clamp
    async with Client(mcp) as client:
        result = await client.call_tool("claude_status", {})
    rd = structured(result)["resolved_defaults"]
    assert rd["config_mode"] == "scoped"
    assert rd["access"] == "toolless"
    assert rd["effort"] == "xhigh"              # depth-first default effort
    assert rd["max_budget_usd"] == 5.0          # clamped to MAX_BUDGET_USD
    assert rd["timeout_seconds"] == 180
    assert rd["budget_bounds"] == [0.01, 5.0]
    assert rd["timeout_bounds"] == [10, 600]


async def test_status_reports_readiness(monkeypatch):
    # claude_status must surface auth + version-compatibility for FREE, so an
    # agent can detect a logged-out or incompatible CLI before any paid call.
    import cc_plugin_codex.server as srv

    monkeypatch.setattr(srv.shutil, "which", lambda _: "/usr/bin/claude")

    class _Ver:
        stdout = "2.1.162 (Claude Code)"

    monkeypatch.setattr(srv.subprocess, "run", lambda *a, **k: _Ver())
    monkeypatch.setattr(srv, "auth_status", lambda *a, **k: (True, "Logged in"))
    async with Client(mcp) as client:
        result = await client.call_tool("claude_status", {})
    data = structured(result)
    assert data["claude_authenticated"] is True
    assert data["version_supported"] is True
    assert data["ready"] is True


async def test_status_not_ready_when_logged_out(monkeypatch):
    import cc_plugin_codex.server as srv

    monkeypatch.setattr(srv.shutil, "which", lambda _: "/usr/bin/claude")

    class _Ver:
        stdout = "2.1.162 (Claude Code)"

    monkeypatch.setattr(srv.subprocess, "run", lambda *a, **k: _Ver())
    monkeypatch.setattr(srv, "auth_status", lambda *a, **k: (False, "Not logged in"))
    async with Client(mcp) as client:
        result = await client.call_tool("claude_status", {})
    data = structured(result)
    assert data["claude_authenticated"] is False
    assert data["ready"] is False


async def test_env_default_config_mode_used(fake_claude, monkeypatch):
    monkeypatch.setenv("CC_PLUGIN_CODEX_CLAUDE_CONFIG", "scoped")
    async with Client(mcp) as client:
        result = await client.call_tool("claude_ask", {"prompt": "x"})
    data = structured(result)
    assert data["meta"]["config_mode"] == "scoped"  # env default applied (param was None)


async def test_review_changes_validates_before_context(fake_claude, monkeypatch, tmp_path):
    # A bad env config_mode must error even though cwd is not a git repo —
    # proving option validation happens before git is touched.
    monkeypatch.setenv("CC_PLUGIN_CODEX_CLAUDE_CONFIG", "bogus")
    monkeypatch.chdir(tmp_path)
    async with Client(mcp) as client:
        result = await client.call_tool("claude_review_changes",
                                        {"scope": "working_tree"},
                                        raise_on_error=False)
    data = structured(result)
    assert data["ok"] is False
    assert data["error"]["code"] == "unsupported_config_mode"


async def test_review_changes_runs_in_git_repo(fake_claude, monkeypatch, git_repo):
    monkeypatch.chdir(git_repo)
    async with Client(mcp) as client:
        result = await client.call_tool("claude_review_changes", {"scope": "working_tree"})
    data = structured(result)
    assert data["ok"] is True
    assert data["verdict"] == "concerns"


async def test_adversarial_invalid_scope_param_rejected_by_schema(fake_claude, monkeypatch, git_repo):
    # F2: an invalid scope value is rejected by the enum schema before execution.
    monkeypatch.chdir(git_repo)
    async with Client(mcp) as client:
        with pytest.raises(Exception) as exc:
            await client.call_tool(
                "claude_adversarial_review", {"target": "skip locking", "scope": "bogus"})
    assert "working_tree" in str(exc.value)


async def test_paid_docstrings_note_schema_validation_class(fake_claude):
    # F2: docstrings must disclose that invalid enum values are rejected by the
    # framework (a validation error), separate from the ok:false envelope.
    tools = await _tools_by_name()
    for name in PAID_TOOLS:
        desc = tools[name].description.lower()
        assert "schema" in desc or "validation error" in desc, name


async def test_adversarial_bad_base_ref_is_structured_error(fake_claude, monkeypatch, git_repo):
    # A malformed base ref must report invalid_base (not invalid_scope) so the
    # agent repairs the right parameter.
    monkeypatch.chdir(git_repo)
    async with Client(mcp) as client:
        result = await client.call_tool(
            "claude_adversarial_review",
            {"target": "skip locking", "scope": "branch", "base": "-badref"},
            raise_on_error=False)
    data = structured(result)
    assert data["ok"] is False
    assert data["error"]["code"] == "invalid_base"
    assert data["error"]["offending_param"] == "base"


async def test_paid_tools_declare_cost_safety_hints():
    # F4: paid, non-idempotent calls expose machine-readable hints, not just prose.
    tools = await _tools_by_name()
    for name in PAID_TOOLS:
        ann = tools[name].annotations
        assert ann is not None, name
        assert ann.readOnlyHint is True, name
        assert ann.destructiveHint is False, name
        assert ann.idempotentHint is False, name


async def test_review_uses_workspace_root_over_cwd(fake_claude, monkeypatch, git_repo, tmp_path):
    # F1: with cwd pointed at an unrelated (non-repo) dir, an explicit
    # workspace_root makes the review target the intended repo.
    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.chdir(other)
    async with Client(mcp) as client:
        result = await client.call_tool(
            "claude_review_changes",
            {"scope": "working_tree", "workspace_root": str(git_repo)})
    data = structured(result)
    assert data["ok"] is True
    assert data["meta"]["cwd"] == str(git_repo)
    assert data["meta"]["workspace_source"] == "param"


async def test_review_invalid_workspace_root_is_structured_error(fake_claude):
    async with Client(mcp) as client:
        result = await client.call_tool(
            "claude_review_changes",
            {"scope": "working_tree", "workspace_root": "/no/such/dir/xyz"},
            raise_on_error=False)
    data = structured(result)
    assert data["ok"] is False
    assert data["error"]["code"] == "invalid_workspace_root"
    assert data["error"]["offending_param"] == "workspace_root"


async def test_review_changes_async_lifecycle(monkeypatch, git_repo, tmp_path):
    # End-to-end through the MCP surface: launch async -> poll status -> get the
    # same envelope as the sync tool. build_command is replaced with a fake that
    # writes a known claude envelope, so no real CLI runs.
    import json as _json

    import cc_plugin_codex.server as srv

    monkeypatch.setenv("CC_PLUGIN_CODEX_STATE_DIR", str(tmp_path / "state"))
    inner = {"summary": "off-by-one", "verdict": "concerns", "confidence": "high",
             "findings": [], "questions": [], "assumptions": []}
    envelope = _json.dumps({"type": "result", "subtype": "success", "is_error": False,
                            "result": _json.dumps(inner), "total_cost_usd": 0.02,
                            "usage": {"input_tokens": 5, "output_tokens": 1}})
    monkeypatch.setattr(srv, "build_command",
                        lambda *a, **k: ["sh", "-c", "printf '%s' \"$0\"", envelope])

    async with Client(mcp) as client:
        started = structured(await client.call_tool(
            "claude_review_changes_async",
            {"scope": "working_tree", "workspace_root": str(git_repo)}))
        assert started["ok"] is True
        assert started["status"] == "running"
        job_id = started["job_id"]

        import time as _time
        deadline = _time.time() + 5
        status = "running"
        while _time.time() < deadline:
            st = structured(await client.call_tool(
                "claude_job_status",
                {"job_id": job_id, "workspace_root": str(git_repo)}))
            status = st["status"]
            if status != "running":
                break
            await anyio.sleep(0.05)
        assert status == "done"

        res = structured(await client.call_tool(
            "claude_job_result", {"job_id": job_id, "workspace_root": str(git_repo)}))
    assert res["ok"] is True
    assert res["verdict"] == "concerns"
    assert res["meta"]["job_id"] == job_id


async def test_job_result_not_found_is_structured_error(tmp_path, monkeypatch, git_repo):
    monkeypatch.setenv("CC_PLUGIN_CODEX_STATE_DIR", str(tmp_path / "state"))
    async with Client(mcp) as client:
        result = await client.call_tool(
            "claude_job_result", {"job_id": "deadbeef", "workspace_root": str(git_repo)},
            raise_on_error=False)
    data = structured(result)
    assert data["ok"] is False
    assert data["error"]["code"] == "job_not_found"


async def test_capabilities_tool_returns_structured_contract():
    # F7: the capability/version contract is available as structured data, not
    # only as a prose resource.
    async with Client(mcp) as client:
        result = await client.call_tool("cc_codex_capabilities", {})
    data = structured(result)
    assert data["fingerprint"] == "cc-plugin-codex/0.1/schema-6"
    assert data["transport"] == "stdio"
    assert set(data["paid_tools"]) == {
        "claude_ask", "claude_review_changes", "claude_adversarial_review",
        "claude_review_changes_async"}
    assert "claude_status" in data["free_tools"]
    for lifecycle in ("claude_job_status", "claude_job_result", "claude_job_cancel"):
        assert lifecycle in data["free_tools"]
    assert data["negative_scope"]            # non-empty list of what it won't do
    assert data["prerequisites"]


async def test_paid_failure_reports_cost_on_error_meta(monkeypatch):
    # A non-zero claude exit that still emitted a cost-bearing JSON envelope
    # (e.g. budget_exceeded) must report cost_usd/usage on the error meta, just
    # like the is_error-envelope path does.
    import cc_plugin_codex.server as srv
    from cc_plugin_codex.claude import ClaudeRun

    envelope = json.dumps({"type": "result", "is_error": True,
                           "subtype": "error_max_budget_usd", "result": "over budget",
                           "total_cost_usd": 0.05,
                           "usage": {"input_tokens": 10, "output_tokens": 0}})

    async def fake_run(cmd, cwd, timeout_seconds):
        return ClaudeRun(stdout=envelope, stderr="", exit_code=1,
                         elapsed_ms=5, timed_out=False)

    monkeypatch.setattr(srv, "run_claude_async", fake_run)
    async with Client(mcp) as client:
        result = await client.call_tool("claude_ask", {"prompt": "x"},
                                        raise_on_error=False)
    data = structured(result)
    assert data["ok"] is False
    assert data["error"]["code"] == "budget_exceeded"
    assert data["meta"]["cost_usd"] == 0.05
    assert data["meta"]["usage"]["input_tokens"] == 10
