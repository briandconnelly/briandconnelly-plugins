from cc_plugin_codex.schemas import (
    FINGERPRINT, RESULT_SCHEMA, Finding, Meta, RawResponse,
    SuccessResult, ErrorInfo, ErrorResult,
)


def test_fingerprint_value():
    assert FINGERPRINT == "cc-plugin-codex/0.1/schema-2"


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


def test_meta_carries_request_id():
    # F7: every Meta gets a correlation id so failures can be tied to their call.
    meta = Meta(cwd="/repo", config_mode="inherit", access="toolless",
                timeout_seconds=180, elapsed_ms=1, fingerprint=FINGERPRINT)
    dumped = meta.model_dump(mode="json", exclude_none=True)
    assert dumped.get("request_id")
    other = Meta(cwd="/repo", config_mode="inherit", access="toolless",
                 timeout_seconds=180, elapsed_ms=1, fingerprint=FINGERPRINT)
    assert other.request_id != meta.request_id  # unique per construction


def test_error_info_drops_misleading_retry_after_ms():
    # F7: retry_after_ms implied a backoff delay we never compute for budget/timeout.
    assert "retry_after_ms" not in ErrorInfo.model_fields


def test_success_result_schema_is_closed():
    assert SuccessResult.model_json_schema().get("additionalProperties") is False


def test_error_result_schema_is_closed():
    assert ErrorResult.model_json_schema().get("additionalProperties") is False


def test_result_schema_defs_are_closed():
    import json
    blob = json.dumps(RESULT_SCHEMA)
    # Nested object models (Finding, Meta, ErrorInfo, ...) carry the closed flag.
    assert '"additionalProperties": false' in blob
