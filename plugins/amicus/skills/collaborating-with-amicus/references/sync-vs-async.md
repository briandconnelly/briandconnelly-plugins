# Sync vs async

Every paid verb (`amicus_consult`, `amicus_review_changes`, `amicus_delegate`,
`amicus_adversarial_review`) has an `_async` twin. Both spend quota; the difference is when and how
you get the result back.

## Rules

Every obligation this file depends on is stated in SKILL.md → Binding rules — the `_async`
preference and job recovery under Spend, and polling, fetching and `amicus_job_result` under Jobs.
That is their authoritative home; this file adds none of its own, and explains how to carry them
out.

## The deadline

An unkeyed synchronous call runs to a bounded deadline (`timeout_seconds`, default 300s, at most
600s). If the backend has not finished by then the call is terminated and you receive nothing —
the work it had already done is unrecoverable. An `_async` call returns a job handle immediately
and the backend keeps running against a separate, longer job deadline (`AMICUS_JOB_MAX_SECONDS`,
default 1800s). Starting the job commits the spend right away, even if you never poll.

The default leaves a review little margin. A review of a few hundred lines at medium effort has
been reported to take two to four minutes on `codex` (issue #84): inside the 300s default,
but close enough that a slower draw is lost with it. For an unkeyed sync review of more than a
small diff, raise `timeout_seconds` toward its ceiling, or use a form that keeps the result.

Prefer `_async`, or a sync call with an `idempotency_key`, for:

- a high-reasoning-effort or broad repo-grounded consult
- a multi-file or whole-branch review
- any delegate task (implementation work is usually the slowest of the four verbs)

What a terminated sync call costs at the provider is not reported to amicus, so nothing here
claims it equals a completed call's cost. The reason to prefer `_async` under uncertainty is that
an unkeyed sync timeout destroys the result you paid for.

A sync call made with an `idempotency_key` is the exception. Its run gets the job deadline
(`AMICUS_JOB_MAX_SECONDS`), as an `_async` run does, and `timeout_seconds` only bounds the wait:
at that bound the call returns `timeout` with a `poll_job_status` repair for the job, which keeps
going, and repeating the same keyed call reattaches to it without new spend. Starting the `_async`
twin or dropping the key there is a second paid run; the Spend rules in SKILL.md say what to do
with that repair. A keyed sync call waits for a long run inside a single call, as does
`amicus_job_status` with a positive `wait_seconds` (see Polling below). Sync and
`_async` are separate identities (ADR 0020), so a keyed sync call cannot wait on a job you
started with the `_async` twin.

## What `_async` returns

A job handle: `job_id`, `status`, `poll_after_ms`, `expires_at`, and a `follow_up` pointer. It is
not the consult/review/delegate result. A fresh start is always `running`. Repeating a keyed
`_async` call replays the existing job's handle instead, and that job may already be terminal:
then `poll_after_ms` is `null` and `follow_up` names `amicus_job_result` with
`next_step: fetch_job_result`, where a running handle names `amicus_job_status` with
`poll_job_status`. Both take the same `job_id` and `workspace_root`, and `status` still says
which case you are in.

## Polling

1. Call the matching `_async` tool once and keep its `job_id`.
2. Read the handle's `status`. If it is already terminal, do not poll. Call `amicus_job_result`
   with the same `job_id` and absolute `workspace_root`, which returns the stored result for
   `done` and the terminal error for any other status, then go to step 5. A handle carries no
   `result_available`, so step 4's branches are for `amicus_job_status` responses only. Otherwise
   wait at least the returned `poll_after_ms`, then call `amicus_job_status` with the same
   `job_id` and the same absolute `workspace_root`.
3. **While `status` is `running`**, honor each new `poll_after_ms` and poll again, or poll with
   a positive `wait_seconds` and call again at once.
4. **Once `status` is terminal** — `done`, `failed`, `cancelled`, or `timeout` — stop polling and
   branch:
   - `done` with `result_available: true` → fetch it.
   - any other terminal status → there is no result to wait for. `amicus_job_result` returns the
     terminal error (`job_cancelled`, `job_failed`, …); read `error.code` and `error.repair`.
5. Branch on the *fetched envelope's* own `ok`. A `done` job can hold a stored error
   (`result_ok: false`) — see [reading results](reading-results.md).

To wait without spending turns on it, pass `wait_seconds` (up to 50 s) to `amicus_job_status`.
The call holds while the job is `running` and returns as soon as it is terminal, or when the wait
runs out with the job still running.
A call with a positive `wait_seconds` is itself the wait, so call again at once if it returns
`running`; with `wait_seconds` 0 or omitted, honor `poll_after_ms` as before.
If your client's MCP request timeout is shorter than the wait, that one status call fails at the
client and the job is unaffected; pass a smaller `wait_seconds`.
Never wait by reading or watching amicus's job store from a shell: its files skip the delivery
checks `amicus_job_result` applies, and their layout is not a contract.

`poll_after_ms` grows with elapsed time — roughly "wait as long as the job has already run" —
up to `30 s`, amicus's ceiling. Once a job is older than that it is polled about every thirty
seconds: a two-to-four-minute review costs roughly 9–13 status calls, and a finished job is
noticed at most thirty seconds late. A hint that stops growing is the ceiling, not a stalled job.
If that many polls is too many, the keyed sync form above waits inside one call instead.

**`poll_after_ms` is `null` on every terminal status.** A loop written as "repeat until
`result_available` is true" therefore never terminates for a cancelled, failed, or timed-out job:
the flag stays false and there is no interval left to honor. Drive the loop from `status`.

`amicus_job_cancel` stops a running job (SIGTERM, then SIGKILL) and cleans up its worktree;
calling it on an already-terminal job is a no-op that returns the job unchanged. A cancelled job
reports `result_available: false`.

## Recovering a lost job

`amicus_job_list` narrows by `backend`, `status`, or `task_id`. A job started under MCP task
support (where the host tracks a `task_id` rather than the raw tool response) is recoverable via
`amicus_job_list(task_id=...)` even if you never captured `job_id`.

Job records expire after `AMICUS_JOB_TTL` (default 24h). A per-workspace cap
(`AMICUS_JOB_MAX_COUNT`, default 50) evicts the oldest terminal records first, but never a running
job or a result amicus has not yet returned (records written before delivery tracking are
evictable): when only those remain, a new paid call is refused
pre-spend with `job_cap_reached`, whose repair lists the workspace's jobs. Fetch or consume a
finished result, or cancel a running job, then retry. A result another amicus release wrote
reads as `job_result_incompatible` here and a consume keeps it: fetch it with that release, or
let `AMICUS_JOB_TTL` expire it. An `amicus_job_list` result carries its own `truncated` and
`truncation_hint` when the listing itself was cut.

A record keeps the backend's whole answer, whatever `detail` delivered it, which is what makes
`amicus_job_result(detail="full")` free later. That answer can quote your inputs, and only
expiry (an expired record is removed on a later job call, not by a daemon), eviction under the
per-workspace cap once amicus has returned it, or `amicus_job_consume_result` removes it.
