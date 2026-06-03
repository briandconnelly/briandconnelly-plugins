from cc_plugin_codex.claude import build_command, classify_failure, ClaudeRun


def test_build_command_toolless_inherit():
    cmd = build_command(prompt="hi", config_mode="inherit", access="toolless",
                        model=None, max_budget_usd=1.0, resume_session=None)
    assert cmd[0] == "claude"
    assert "-p" in cmd and "--output-format" in cmd and "json" in cmd
    assert "--no-session-persistence" in cmd
    assert "--tools" in cmd
    assert "--append-system-prompt" in cmd
    assert cmd[-1] == "hi"  # prompt is the final positional arg


def test_build_command_model_and_resume():
    cmd = build_command(prompt="hi", config_mode="inherit", access="readonly",
                        model="sonnet", max_budget_usd=2.0, resume_session="sess-1")
    assert "--model" in cmd and "sonnet" in cmd
    assert "--resume" in cmd and "sess-1" in cmd
    assert "--no-session-persistence" not in cmd  # resume needs persistence


def test_classify_not_logged_in():
    run = ClaudeRun(stdout="", stderr="Not logged in · Please run /login",
                    exit_code=1, elapsed_ms=5, timed_out=False)
    info = classify_failure(run)
    assert info.code == "claude_auth_required"
    assert "/login" in info.repair


def test_classify_invalid_api_key():
    run = ClaudeRun(stdout="", stderr="Invalid API key · Fix external API key",
                    exit_code=1, elapsed_ms=5, timed_out=False)
    assert classify_failure(run).code == "api_key_required"


def test_classify_timeout():
    run = ClaudeRun(stdout="", stderr="", exit_code=-9, elapsed_ms=1, timed_out=True)
    assert classify_failure(run).code == "timeout"


def test_classify_budget():
    run = ClaudeRun(stdout="", stderr="Exceeded max budget of $1.00",
                    exit_code=1, elapsed_ms=5, timed_out=False)
    assert classify_failure(run).code == "budget_exceeded"


def test_classify_not_found():
    run = ClaudeRun(stdout="", stderr="claude_not_found", exit_code=127,
                    elapsed_ms=1, timed_out=False)
    assert classify_failure(run).code == "claude_not_found"


def test_classify_generic_nonzero():
    run = ClaudeRun(stdout="", stderr="something else", exit_code=2,
                    elapsed_ms=5, timed_out=False)
    assert classify_failure(run).code == "nonzero_exit"


def test_build_command_separates_prompt_with_double_dash():
    cmd = build_command(prompt="--model evil", config_mode="inherit", access="toolless",
                        model=None, max_budget_usd=1.0, resume_session=None)
    assert cmd[-2] == "--"
    assert cmd[-1] == "--model evil"  # flag-looking prompt stays a positional
