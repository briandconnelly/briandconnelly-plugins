"""Shared test fixtures and helpers."""

import json
import subprocess

import pytest


def structured(result):
    """Extract the structured payload from a FastMCP call result across versions."""
    data = getattr(result, "structured_content", None)
    if data is not None:
        return data
    return json.loads(result.content[0].text)


@pytest.fixture
def git_repo(tmp_path):
    """A throwaway git repo with one committed file and one unstaged change."""
    def run(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True,
                       capture_output=True, text=True)
    run("init", "-q")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "Test")
    (tmp_path / "app.py").write_text("def add(a, b):\n    return a + b\n")
    run("add", "app.py")
    run("commit", "-q", "-m", "init")
    (tmp_path / "app.py").write_text("def add(a, b):\n    return a - b  # bug\n")
    return tmp_path
