---
name: collaborating-with-amicus
description: Use whenever this agent should call another model through amicus, or should answer a question about which models amicus can reach — a second opinion, a code review, an adversarial review, a delegated implementation, or a check of which backends are enabled, installed and authenticated. Trigger on "ask another model", "get a second opinion", "have Codex/Kimi/Claude review this", "delegate this", "which models can I use", "is Codex/Kimi/Claude available", "what does amicus support", "check backend status", on any amicus_* tool result or approval prompt you need to interpret, and at decision points: choosing a hard-to-reverse approach, after two failed fixes, before declaring risky work complete.
---

# Collaborating with amicus

amicus is one MCP server for every second-opinion model. Every paid tool takes the model as a
`backend` parameter (`codex`, `kimi`, or `claude`) instead of routing to a separate server per
model. This skill is the router: which tool, which backend, sync or async, and what a returned
result obligates you to do next.

## Rules

1. **Choose `backend` per call, from `amicus_backends`.** Call `amicus_backends` before the
   first paid call of a session, and pass only a backend it reports `enabled: true` and
   `status.authenticated: true`.
2. **Use the matching `_async` tool when the work can exceed the sync deadline.** A sync call that
   times out is terminated and its work is still spent — the quota loss is the same as if the call
   had finished, so a call you are unsure will finish in time is a call to run `_async`.
3. **Never call a paid tool to find out whether a backend is available.** `amicus_backends`,
   `amicus_dry_run`, and `amicus_delegate_dry_run` answer that for free.
4. **Review a returned diff against
   [reviewing-a-returned-diff.md](references/reviewing-a-returned-diff.md) before you apply it,
   and answer in that file's response contract.** Emit a `Checks:` block first, one line per
   fixed key (`fidelity`, `scope`, `checks-run`, `consistency`), then a separately labelled
   `Verdict:` line. Nothing about applying, not applying, or being done appears before the
   `Checks:` block.
5. **When a host asks for approval on a paid tool, name the backend whose annotation caused
   it.** The annotation tracks the most permissive *enabled* backend, not the `backend` this
   call selected (see "Annotations follow the worst enabled backend"). Report the prompt to the
   user with that attribution attached; do not retry silently, switch `backend` to dodge it, or
   ask the user to disable a backend.
6. **Never put a secret in any free-text field you supply.** On this surface those are
   `question`, `task`, `target`, `evidence`, `extra_context`, `instructions_append`, and
   `focus` — every one of them is sent to the backend's provider raw, and how each reaches the
   backend CLI is that backend's own choice: on `codex`, `instructions_append` rides argv and is
   visible in local process listings (see "Data exposure"). `target` and `evidence` are the
   primary carriers on `amicus_adversarial_review`, not optional extras.

## Route the request

| Situation | Tool | Read |
| --- | --- | --- |
| One answer, design critique, or second opinion (including on a diff you paste inline) | `amicus_consult` | [choosing a backend](references/choosing-a-backend.md) |
| Review changes already represented in git | `amicus_review_changes` | [choosing a backend](references/choosing-a-backend.md) |
| A fixed adversarial critic attacking a plan, claim, or decision before you commit to it (`claude` only in v1) | `amicus_adversarial_review` | [choosing a backend](references/choosing-a-backend.md) |
| A self-contained coding task implemented in a throwaway worktree, returned as a diff (`codex` or `kimi` only in v1) | `amicus_delegate` | [reviewing a returned diff](references/reviewing-a-returned-diff.md) |
| Any of the above when the work can exceed the sync deadline | the matching `_async` tool | [sync vs async](references/sync-vs-async.md) |
| Which backends are enabled, installed, and authenticated, and what each supports | `amicus_backends` (free) | [choosing a backend](references/choosing-a-backend.md) |
| Preview a review's or delegate's scope, size, and resolved options before spending | `amicus_dry_run` / `amicus_delegate_dry_run` (free) | [sync vs async](references/sync-vs-async.md) |
| Model slugs and reasoning-effort sets before overriding `model` or `reasoning_effort` | `amicus_models` (free) | — |
| Full tool inventory, fingerprint, and error catalog | `amicus_capabilities` (free) | — |
| Poll, fetch, list, or cancel a background job | `amicus_job_status` / `amicus_job_result` / `amicus_job_consume_result` / `amicus_job_list` / `amicus_job_cancel` (all free) | [sync vs async](references/sync-vs-async.md) |
| None of these, or a call would not change the decision | no call — proceed without amicus | — |

Discovery and job-lifecycle tools never spend quota; only `amicus_consult`, `amicus_review_changes`,
`amicus_delegate`, `amicus_adversarial_review`, and their `_async` twins do.

## Reading results

- Branch on `ok` first. On `ok: false`, read `error.code` and `error.repair`; do not guess at
  recovery from prose.
- On `ok: true`, branch on the concrete tool before reading fields. Consult, review, delegate, and
  adversarial-review results share `summary`, `findings`, and `meta`; only review and adversarial
  results carry `verdict`/`confidence`, and only delegate carries `diff`.
- Treat `verdict`, `confidence`, and any proposed diff as claims to verify, not as settled fact.
  Run this project's own checks yourself before acting on a finding.
- A `diff` in a delegate result is a proposal: amicus never applies anything to your working
  tree, so nothing has changed on disk until you apply it (rule 4 governs what you do first).

## Context

### Backend as a parameter

amicus does not have a "default" backend the way a single-model server implies one model by its
own name. `backend` names which model answers a given call, and the same 18 tools work for all
three. `AMICUS_BACKENDS` (an operator/deployment setting) controls which backends are *enabled* in
this deployment — it says nothing about which to prefer for a given task. Reading `amicus_backends`
tells you what is actually usable right now: `enabled`, `available` (the plugin loaded),
`status.installed`, and `status.authenticated`. A backend can be enabled but not ready (not
installed, not authenticated) — check `status`, not just `enabled`.

Feature support differs per backend in v1: `amicus_delegate` supports `codex` and `kimi` only
(Claude stays review-only); `amicus_adversarial_review` supports `claude` only. `amicus_consult`
and `amicus_review_changes` support all three. `amicus_backends` reports each backend's supported
features, so check it rather than assuming.

### The job lifecycle

Every paid call — sync or async — is recorded as a job (`meta.job_id`). A sync call runs to
completion and returns the result directly; an async call (`_async` suffix) returns a job handle
immediately and the result is fetched later with `amicus_job_result` or
`amicus_job_consume_result` (which also deletes the record). `amicus_job_status` polls without
fetching the result; honor the returned `poll_after_ms` rather than polling on a fixed interval.
`amicus_job_list` recovers a job by `backend`, `status`, or `task_id` when the `job_id` itself was
lost — a task-augmented call (one made under MCP's task/experimental-tasks support) maps to its job
this way. Job records expire after `AMICUS_JOB_TTL` (default 24h) and a per-workspace cap evicts
the oldest terminal records, so read results promptly rather than leaving them to expire.

### Annotations follow the worst enabled backend

Tool annotations (e.g. `destructiveHint`) are static per tool but `backend` is a per-call
parameter, so amicus annotates each paid tool for the worst-behaved backend currently enabled. If
an enabled backend can write outside a throwaway worktree (Claude, in its non-review config
modes), every paid tool — even a call routed to a read-only backend like `codex` or `kimi` on that
same call — carries that backend's approval annotations, so a host may prompt for approval before a
call that is, in fact, read-only for the backend actually used. This is a deliberate consequence of
one shared tool surface, not a bug: read `amicus_backends`' per-backend `effects` for the
call-specific truth, and expect approval friction to track the *most permissive enabled backend*,
not the one you picked for this call.

### Data exposure

- Every free-text field you supply — `question`, `task`, `target`, `evidence`, `extra_context`,
  `instructions_append`, `focus` — is sent to the selected backend's provider raw. Amicus never
  writes one to its own logs, and for an async job they reach amicus's job worker over that
  worker's stdin, never on its argv and never into the job directory.
- That is amicus's own transport only. **How a field reaches the backend CLI is the backend's
  choice, and one of them puts caller text on a command line.** Read `carriers` on
  `amicus_backends` for the authoritative per-backend statement; today:
  - `codex` — the prompt (framing, question/task/diff, `extra_context`) rides the codex process's
    stdin, but `instructions_append` rides **argv** as the `-c developer_instructions` config
    override, so its text is visible to anything that can list processes on this machine for the
    duration of the run. Do not put a secret in `instructions_append` on `codex`.
  - `claude` — the whole prompt, `instructions_append` included, rides the claude process's stdin;
    argv carries only fixed text and flags.
  - `kimi` — kimi ignores stdin and crashes on a long argv, so the whole prompt is written to a
    file in a private temp directory outside the workspace and argv carries only that file's path;
    amicus removes the directory when the run ends. Nothing you type rides argv, but the text is
    briefly on local disk.
- A backend can read files outside the workspace during an active call; the workspace is not a
  read boundary. Redaction (secret scrubbing) is best-effort and applies only to gathered diffs
  and returned output, never to what you supply.
- A dry run (`amicus_dry_run`, `amicus_delegate_dry_run`) previews the input amicus would
  assemble. It does not invoke the backend, so it neither proves a paid call is safe to make nor
  bounds what the backend itself reads once the paid call runs.
