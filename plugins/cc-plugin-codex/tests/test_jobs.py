"""Background-job lifecycle tests.

These drive jobs.start_job with a fake command (not the real `claude`) that writes
a known JSON envelope, so the full start -> status -> result/cancel/timeout flow is
exercised deterministically and for free.
"""

import json
import time

import pytest

from cc_plugin_codex import jobs
from cc_plugin_codex.jobs import JobConfig

_INNER = {
    "summary": "off-by-one bug", "verdict": "concerns", "confidence": "high",
    "findings": [{"severity": "high", "title": "subtraction", "file": "app.py",
                  "line": 2, "evidence": "a - b", "risk": "wrong", "recommendation": "use +"}],
    "questions": [], "assumptions": [],
}
_ENVELOPE = json.dumps({
    "type": "result", "subtype": "success", "is_error": False,
    "result": json.dumps(_INNER), "session_id": "sess-1",
    "total_cost_usd": 0.0123, "usage": {"input_tokens": 100, "output_tokens": 50},
})


def _cfg(**over):
    base = dict(kind="claude_review_changes", config_mode="inherit", access="toolless",
                scope="working_tree", base="main", detail="summary",
                timeout_seconds=1800, workspace_source="cwd", context_summary=None)
    base.update(over)
    return JobConfig(**base)


def _emit_cmd(envelope=_ENVELOPE):
    # `printf %s "$0"` writes the envelope (passed as $0) to stdout -> result.json.
    return ["sh", "-c", "printf '%s' \"$0\"", envelope]


def _sleep_cmd(seconds=30):
    return ["sh", "-c", f"sleep {seconds}"]


@pytest.fixture(autouse=True)
def _state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CC_PLUGIN_CODEX_STATE_DIR", str(tmp_path / "state"))


def _await_done(cwd, job_id, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = jobs.status(cwd, job_id)
        if st and st["status"] != "running":
            return st
        time.sleep(0.05)
    raise AssertionError("job did not leave running state in time")


def test_job_done_returns_normalized_result(tmp_path):
    cwd = str(tmp_path)
    job_id, started_at = jobs.start_job(_emit_cmd(), cwd, _cfg())
    assert started_at
    st = _await_done(cwd, job_id)
    assert st["status"] == "done"
    assert st["result_available"] is True
    assert st["cost_usd"] == 0.0123

    payload, found = jobs.result(cwd, job_id)
    assert found is True
    assert payload["ok"] is True
    assert payload["verdict"] == "concerns"
    assert payload["meta"]["job_id"] == job_id
    assert payload["meta"]["cost_usd"] == 0.0123


def test_job_running_then_result_says_job_running(tmp_path):
    cwd = str(tmp_path)
    job_id, _ = jobs.start_job(_sleep_cmd(), cwd, _cfg())
    st = jobs.status(cwd, job_id)
    assert st["status"] == "running"
    assert st["result_available"] is False

    payload, found = jobs.result(cwd, job_id)
    assert found is True
    assert payload["ok"] is False
    assert payload["error"]["code"] == "job_running"
    assert payload["error"]["retryable"] is True
    jobs.cancel(cwd, job_id)  # clean up the sleeper


def test_job_cancel(tmp_path):
    cwd = str(tmp_path)
    job_id, _ = jobs.start_job(_sleep_cmd(), cwd, _cfg())
    assert jobs.status(cwd, job_id)["status"] == "running"
    st = jobs.cancel(cwd, job_id)
    assert st["status"] == "cancelled"

    payload, found = jobs.result(cwd, job_id)
    assert found is True
    assert payload["error"]["code"] == "job_cancelled"


def test_job_timeout_on_deadline(tmp_path, monkeypatch):
    monkeypatch.setenv("CC_PLUGIN_CODEX_JOB_MAX_SECONDS", "0")  # deadline = start time
    cwd = str(tmp_path)
    job_id, _ = jobs.start_job(_sleep_cmd(), cwd, _cfg())
    st = jobs.status(cwd, job_id)  # first poll past deadline reaps it
    assert st["status"] == "timeout"
    payload, _ = jobs.result(cwd, job_id)
    assert payload["error"]["code"] == "job_timeout"


def test_job_not_found(tmp_path):
    cwd = str(tmp_path)
    assert jobs.status(cwd, "nope") is None
    assert jobs.cancel(cwd, "nope") is None
    payload, found = jobs.result(cwd, "nope")
    assert found is False


def test_terminal_job_reaped_after_ttl(tmp_path, monkeypatch):
    cwd = str(tmp_path)
    job_id, _ = jobs.start_job(_emit_cmd(), cwd, _cfg())
    _await_done(cwd, job_id)
    # TTL of 0 means a terminal record is eligible for cleanup on the next call.
    monkeypatch.setenv("CC_PLUGIN_CODEX_JOB_TTL", "0")
    time.sleep(0.02)
    assert jobs.status(cwd, job_id) is None  # reaped


def test_consume_deletes_record(tmp_path):
    cwd = str(tmp_path)
    job_id, _ = jobs.start_job(_emit_cmd(), cwd, _cfg())
    _await_done(cwd, job_id)
    payload, found = jobs.result(cwd, job_id, consume=True)
    assert found is True and payload["ok"] is True
    assert jobs.status(cwd, job_id) is None  # gone after consume
