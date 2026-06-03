import subprocess

import pytest

from cc_plugin_codex.context import gather_context, ContextResult


def test_working_tree_diff(git_repo):
    res = gather_context(str(git_repo), scope="working_tree", base="main")
    assert isinstance(res, ContextResult)
    assert "a - b" in res.text
    assert res.summary.files_changed == 1
    assert res.summary.lines_added >= 1
    assert res.truncated is False


def test_invalid_scope(git_repo):
    with pytest.raises(ValueError):
        gather_context(str(git_repo), scope="bogus", base="main")


def test_secret_files_redacted(git_repo):
    (git_repo / ".env").write_text("API_KEY=supersecret\n")
    # intent-to-add so the new file shows up in `git diff`
    subprocess.run(["git", "add", "-Nf", ".env"], cwd=git_repo, check=True)
    res = gather_context(str(git_repo), scope="working_tree", base="main")
    assert "supersecret" not in res.text
    assert ".env" in res.text  # path noted as redacted


def test_size_cap_truncates(git_repo, monkeypatch):
    import cc_plugin_codex.context as ctx
    monkeypatch.setattr(ctx, "MAX_DIFF_BYTES", 10)
    (git_repo / "big.py").write_text("x = 1\n" * 1000)
    subprocess.run(["git", "add", "-Nf", "big.py"], cwd=git_repo, check=True)
    res = gather_context(str(git_repo), scope="working_tree", base="main")
    assert res.truncated is True
    assert res.truncation_hint


def test_stage_env_file_redacted(git_repo):
    (git_repo / "prod.env").write_text("DB_PASSWORD=hunter2\n")
    subprocess.run(["git", "add", "-Nf", "prod.env"], cwd=git_repo, check=True)
    res = gather_context(str(git_repo), scope="working_tree", base="main")
    assert "hunter2" not in res.text
    assert "prod.env" in res.text
