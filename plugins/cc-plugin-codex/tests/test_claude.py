import anyio

from cc_plugin_codex.claude import (
    build_command, classify_failure, run_claude_async, ClaudeRun,
)


async def test_run_claude_async_returns_output():
    run = await run_claude_async(["sh", "-c", "printf hi"], cwd=".", timeout_seconds=10)
    assert run.exit_code == 0
    assert run.stdout == "hi"
    assert run.timed_out is False


async def test_run_claude_async_times_out_and_kills(tmp_path):
    marker = tmp_path / "marker"
    cmd = ["sh", "-c", f"sleep 5; touch {marker}"]
    run = await run_claude_async(cmd, cwd=".", timeout_seconds=1)
    assert run.timed_out is True
    assert run.exit_code == -9
    await anyio.sleep(0.3)
    assert not marker.exists()  # the slept command was killed before touching marker


async def test_run_claude_async_cancellation_kills_process(tmp_path):
    marker = tmp_path / "marker"
    cmd = ["sh", "-c", f"sleep 5; touch {marker}"]
    async with anyio.create_task_group() as tg:
        async def _call():
            await run_claude_async(cmd, cwd=".", timeout_seconds=30)
        tg.start_soon(_call)
        await anyio.sleep(0.3)        # let the subprocess spawn
        tg.cancel_scope.cancel()      # simulate an MCP client cancellation
    await anyio.sleep(0.3)
    assert not marker.exists()        # cancellation terminated the process tree


def test_build_command_toolless_inherit():
    cmd = build_command(prompt="hi", config_mode="inherit", access="toolless",
                        model=None, max_budget_usd=1.0)
    assert cmd[0] == "claude"
    assert "-p" in cmd and "--output-format" in cmd and "json" in cmd
    assert "--no-chrome" in cmd  # avoid an interactive Chrome picker hanging the call
    assert "--no-session-persistence" in cmd
    assert "--tools" in cmd
    assert "--append-system-prompt" in cmd
    assert cmd[-2] == "--"
    assert cmd[-1] == "hi"  # prompt is the final positional arg


def test_build_command_effort_flag():
    cmd = build_command(prompt="hi", config_mode="inherit", access="toolless",
                        model=None, max_budget_usd=1.0, effort="xhigh")
    assert "--effort" in cmd
    assert cmd[cmd.index("--effort") + 1] == "xhigh"


def test_build_command_omits_effort_when_none():
    cmd = build_command(prompt="hi", config_mode="inherit", access="toolless",
                        model=None, max_budget_usd=1.0)
    assert "--effort" not in cmd


def test_build_command_model():
    cmd = build_command(prompt="hi", config_mode="inherit", access="readonly",
                        model="sonnet", max_budget_usd=2.0)
    assert "--model" in cmd and "sonnet" in cmd


def test_classify_not_logged_in():
    run = ClaudeRun(stdout="", stderr="Not logged in · Please run /login",
                    exit_code=1, elapsed_ms=5, timed_out=False)
    info = classify_failure(run)
    assert info.code == "claude_auth_required"
    assert "/login" in info.repair


def test_classify_invalid_api_key():
    run = ClaudeRun(stdout="", stderr="Invalid API key · Fix external API key",
                    exit_code=1, elapsed_ms=5, timed_out=False)
    assert classify_failure(run).code == "api_key_invalid"


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


def test_classify_budget_from_envelope_subtype():
    import json
    stdout = json.dumps({"type": "result", "is_error": True,
                         "subtype": "error_max_budget_usd", "result": ""})
    run = ClaudeRun(stdout=stdout, stderr="", exit_code=1, elapsed_ms=5, timed_out=False)
    assert classify_failure(run).code == "budget_exceeded"


def test_classify_auth_from_structured_envelope():
    import json
    stdout = json.dumps({"type": "result", "is_error": True,
                         "subtype": "auth_required", "result": "please login"})
    run = ClaudeRun(stdout=stdout, stderr="unrelated", exit_code=1,
                    elapsed_ms=5, timed_out=False)
    assert classify_failure(run).code == "claude_auth_required"


def test_classify_permission_from_structured_envelope():
    import json
    stdout = json.dumps({"type": "result", "is_error": True,
                         "subtype": "permission_denied", "result": "Read denied"})
    run = ClaudeRun(stdout=stdout, stderr="", exit_code=1, elapsed_ms=5,
                    timed_out=False)
    assert classify_failure(run).code == "claude_permission_error"


def test_classify_malformed_structured_error_falls_back():
    run = ClaudeRun(stdout='{"is_error": true,', stderr="something else",
                    exit_code=2, elapsed_ms=5, timed_out=False)
    assert classify_failure(run).code == "nonzero_exit"


def test_build_command_separates_prompt_with_double_dash():
    cmd = build_command(prompt="--model evil", config_mode="inherit", access="toolless",
                        model=None, max_budget_usd=1.0)
    assert cmd[-2] == "--"
    assert cmd[-1] == "--model evil"  # flag-looking prompt stays a positional
