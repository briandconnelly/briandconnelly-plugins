import json

import pytest
from fastmcp import Client

from cc_plugin_codex.server import CAPABILITY_SUMMARY, mcp
from tests.conftest import structured

PAID_TOOLS = ("claude_ask", "claude_review_changes", "claude_adversarial_review")


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
    assert data["meta"]["fingerprint"] == "cc-plugin-codex/0.1/schema-2"


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
    assert rd["max_budget_usd"] == 5.0          # clamped to MAX_BUDGET_USD
    assert rd["timeout_seconds"] == 180
    assert rd["budget_bounds"] == [0.01, 5.0]
    assert rd["timeout_bounds"] == [10, 600]


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
