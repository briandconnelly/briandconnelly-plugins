# Sync vs async

Every paid verb (`amicus_consult`, `amicus_review_changes`, `amicus_delegate`,
`amicus_adversarial_review`) has an `_async` twin. Both spend quota; the difference is when and how
you get the result back.

## The deadline

A synchronous call runs to a bounded deadline (`timeout_seconds`, default 300s). If the backend has
not finished by then, the call is terminated — its partial paid work is lost, but the quota is
still spent. There is no partial credit for a sync timeout.

An `_async` call returns a job handle immediately and the backend keeps running against a separate,
longer job deadline (`AMICUS_JOB_MAX_SECONDS`, default 1800s). Starting the job commits to spend
right away, even if you never poll for the result.

Prefer `_async` whenever the work is likely to exceed the sync deadline:

- a high-reasoning-effort or broad repo-grounded consult
- a multi-file or whole-branch review
- any delegate task (implementation work is usually the slowest of the four verbs)

If you are not sure whether a call will finish inside 300s, that uncertainty is itself a reason to
prefer `_async` — losing a sync call to a timeout costs the same quota as running it async and
waiting.

## What `_async` returns

The immediate return from an `_async` call is a job handle: `job_id`, `poll_after_ms`,
`expires_at`, and a `follow_up` pointer. It is not the consult/review/delegate result itself.

## Polling

1. Call the matching `_async` tool once and keep its `job_id`.
2. Wait at least the returned `poll_after_ms` before calling `amicus_job_status` with the same
   `job_id`. Do not busy-poll faster than the advised interval.
3. Repeat, honoring each new `poll_after_ms`, until `result_available` is true.
4. Fetch the result with `amicus_job_result` (record retained, re-readable) or
   `amicus_job_consume_result` (record deleted after this read — not idempotent; a repeat call
   returns `job_not_found`).
5. The fetched result has the same success shape as the tool that started the job (a consult result
   for `amicus_consult_async`, a diff for `amicus_delegate_async`, and so on).

`amicus_job_cancel` stops a running job (SIGTERM, then SIGKILL) and cleans up its worktree; calling
it on an already-terminal job is a no-op that returns the job unchanged.

## Recovering a lost job

A job started under MCP task support (task-augmented calls, where the host tracks a `task_id`
rather than the raw tool response) is recoverable even if you never captured `job_id` directly:
call `amicus_job_list(task_id=...)` to find the job that call created. `amicus_job_list` also
narrows by `backend` or `status` when you need to find a job without any of these identifiers.

Job records expire after `AMICUS_JOB_TTL` (default 24h), and a per-workspace cap evicts the oldest
terminal records first. Fetch results promptly — do not treat the job store as long-term storage.
