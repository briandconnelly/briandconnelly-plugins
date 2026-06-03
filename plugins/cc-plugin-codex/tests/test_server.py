import pytest
from fastmcp import Client

from cc_plugin_codex.server import mcp
from tests.conftest import structured


async def test_list_tools():
    async with Client(mcp) as client:
        names = {t.name for t in await client.list_tools()}
    assert {"claude_ask", "claude_review_changes",
            "claude_adversarial_review", "claude_status"} <= names


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
    assert data["meta"]["fingerprint"] == "cc-plugin-codex/0.1/schema-1"


async def test_unsupported_config_mode_is_structured_error(fake_claude):
    async with Client(mcp) as client:
        result = await client.call_tool(
            "claude_ask", {"prompt": "x", "config_mode": "bogus"})
    data = structured(result)
    assert data["ok"] is False
    assert data["error"]["code"] == "unsupported_config_mode"


async def test_unsupported_access_is_structured_error(fake_claude):
    async with Client(mcp) as client:
        result = await client.call_tool("claude_ask", {"prompt": "x", "access": "bogus"})
    data = structured(result)
    assert data["error"]["code"] == "unsupported_access"


async def test_bare_without_api_key_errors(fake_claude, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    async with Client(mcp) as client:
        result = await client.call_tool(
            "claude_ask", {"prompt": "x", "config_mode": "bare"})
    data = structured(result)
    assert data["error"]["code"] == "api_key_required"


async def test_env_default_config_mode_used(fake_claude, monkeypatch):
    monkeypatch.setenv("CC_PLUGIN_CODEX_CLAUDE_CONFIG", "scoped")
    async with Client(mcp) as client:
        result = await client.call_tool("claude_ask", {"prompt": "x"})
    data = structured(result)
    assert data["meta"]["config_mode"] == "scoped"  # env default applied (param was None)


async def test_review_changes_validates_before_context(fake_claude):
    # Invalid config_mode must error even though cwd may not be a git repo.
    async with Client(mcp) as client:
        result = await client.call_tool("claude_review_changes",
                                        {"scope": "working_tree", "config_mode": "bogus"})
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


async def test_adversarial_invalid_scope_is_structured_error(fake_claude, monkeypatch, git_repo):
    monkeypatch.chdir(git_repo)
    async with Client(mcp) as client:
        result = await client.call_tool(
            "claude_adversarial_review", {"target": "skip locking", "scope": "bogus"})
    data = structured(result)
    assert data["ok"] is False
    assert data["error"]["code"] == "invalid_scope"
