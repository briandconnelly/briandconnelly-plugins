from cc_plugin_codex.schemas import (
    FINGERPRINT, Finding, Meta, RawResponse, ContextSummary,
    SuccessResult, ErrorInfo, ErrorResult,
)


def test_fingerprint_value():
    assert FINGERPRINT == "cc-plugin-codex/0.1/schema-1"


def test_success_result_dump_omits_none():
    meta = Meta(cwd="/repo", config_mode="inherit", access="toolless",
                timeout_seconds=180, elapsed_ms=10, fingerprint=FINGERPRINT)
    res = SuccessResult(
        tool="claude_ask", summary="s", verdict="pass", confidence="high",
        findings=[Finding(severity="low", title="t", evidence="e", risk="r",
                          recommendation="rec")],
        raw_response=RawResponse(), meta=meta,
    )
    dumped = res.model_dump(mode="json", exclude_none=True)
    assert dumped["ok"] is True
    assert "text" not in dumped["raw_response"]      # None text dropped
    assert "file" not in dumped["findings"][0]       # None file dropped


def test_error_result_shape():
    err = ErrorResult(
        error=ErrorInfo(code="timeout", message="m", repair="r"),
        meta=Meta(cwd="/repo", config_mode="inherit", access="toolless",
                  timeout_seconds=180, elapsed_ms=1, fingerprint=FINGERPRINT),
    )
    dumped = err.model_dump(mode="json", exclude_none=True)
    assert dumped["ok"] is False
    assert dumped["error"]["code"] == "timeout"
