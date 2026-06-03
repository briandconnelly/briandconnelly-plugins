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
