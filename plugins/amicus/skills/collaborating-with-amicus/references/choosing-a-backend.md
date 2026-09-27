# Choosing a backend

`backend` is required on every paid amicus tool and has no default — pick it deliberately for each
call. Two questions decide it: **is this backend eligible**, and **can it actually see the
evidence the task needs**.

## Rules

Eligibility, freshness and authority are governed by SKILL.md → Binding rules → Discovery; they
are not restated here. These are this file's own:

- **Decide `access` from the evidence the task needs, before the call.** Choosing `readonly` up
  front because the backend must read a named file is correct; raising it *afterwards*, because a
  result came back short of evidence, is what the root rule forbids.
- **Never spend a paid call to break a tie** between backends that are equally suitable.
- **Never claim two backends are different model families** unless you have evidence of the
  underlying models.
- **Never inspect a user's provider configuration to establish model identity.** Report the
  diversity as unverified instead.
- **Never send `kimi` text you would not leave on this machine's disk.** What is acceptable to
  hand a provider is not thereby acceptable to keep locally: the kimi CLI's own session store
  outlives the run and amicus does not delete it (see the carrier table below).

## What `amicus_backends` reports

Per backend: `enabled` (turned on for this deployment), `available` (the plugin loaded),
`status.installed`, `status.authenticated`, `status.warnings`, `features` (the gated verbs it
supports plus its extra capabilities — see below), `effects` (`paid_calls_destructive`,
`job_reads_read_only`), `options` (backend-specific knobs and their allowed values),
`egress`/`carriers` (how prompt inputs travel), `readonly_honesty` (what this backend's read-only
tier does and does not bound), and `implicit_context` (what the CLI auto-loads regardless of your
prompt). The last four are on `detail="full"` only: the default `summary` leaves those keys out of
every entry and lists them in the top-level `omitted_fields`, so an absent key means "not
requested", while a `null` on `full` means no loaded plugin declares one (the backend declares
none, or its plugin did not load). `summary` is enough for a readiness re-check once the full
report has been read.

`AMICUS_BACKENDS` decides which backends exist in this deployment; it does not decide which to
prefer.

## What `features` contains

`features` is not the list of verbs a backend answers. It names the gated verbs the backend
declares, plus capabilities that are not verbs at all:

- `delegate` and `adversarial_review` — the gated verbs. A call routing one to a backend outside
  the tool's `backend` enum, in-tree or not, is rejected pre-spend as `invalid_arguments` with
  `details.allowed_values` (the enum is the accepted set); `feature_unsupported` is only a
  defensive check, returned if the named backend's declared features lack the verb, which no
  backend a tool's enum accepts does today.
- `usage_accounting` — the backend's CLI reports token usage, carried as `meta.usage`.
- `model_validation` and `empty_response_detection` — the backend can refuse an unknown `model`
  (`invalid_model`) and report a reply with no content (`empty_response`).

`consult` and `review_changes` are baseline on every backend and never appear in `features`. A
host that looks for them there concludes, wrongly, that no backend supports them (issue #84).

The gates in v1:

- `amicus_consult` / `amicus_review_changes`: `codex`, `kimi`, `claude`; never gated.
- `amicus_delegate`: `codex`, `kimi` only. Claude stays review-only, so a delegate call routed
  to claude fails the feature gate.
- `amicus_adversarial_review`: `claude` only.

## What each backend can inspect

A backend answers from what it can read. These differ enough to change which one is right for a
task, and one default surprises people:

| | Reads the repo during a call? | Default |
| --- | --- | --- |
| `codex` | Yes — under codex's `--sandbox read-only` OS sandbox | read-only sandbox |
| `kimi` | Yes — a generated agent profile grants `Read`, `Glob`, `Grep` and no shell or write tool | read-only profile |
| `claude` | **Reviews only, by default.** `amicus_review_changes` defaults to `access="readonly"` (`Read`, `Grep`, `Glob`); consult and adversarial review default to `access="toolless"`, which grants **no tools at all** | `access="readonly"` for reviews and `"toolless"` otherwise, `config_mode="inherit"` |

**A `claude` consult told to "look at the file and tell me what's wrong" has, by default, no
tool with which to look.** Either supply the evidence inline (`question`, `extra_context`) or pass
`backend_options: {"access": "readonly"}` deliberately — which grants `Read`, `Grep`, `Glob`.

An operator who sets `AMICUS_CLAUDE_ACCESS` sets the default for every verb, reviews included,
so read the resolved value on `amicus_backends` (`default` or `default_by_verb`) rather than
assuming a review can read the repository.

`toolless` is not isolation. It removes the model's tools; it does not stop the CLI loading
context on its own. Under the default `config_mode="inherit"` (and under `scoped`) claude still
reads the workspace's `CLAUDE.md` and `.claude/settings*.json`, hooks included — and hooks run
outside the tool allowlist. What the backend receives is therefore your prompt *plus* whatever
that mode loads, which `implicit_context` states per backend.

Read-only bounds *modification*, never *reach*, on any of the three. Codex's sandbox bounds
writes, not reads; kimi's `Read` accepts absolute paths; Claude's `readonly` accepts absolute
paths too and, having read a file itself, bypasses the diff redaction entirely. The workspace
selects where a backend works, not what it can read — `readonly_honesty` on `amicus_backends(detail="full")` is
each backend's own statement of that limit.

## How your text reaches each backend

Amicus's own transport is the same for all three: free-text fields never touch its logs, and for
an async job they reach the worker over stdin. How a field reaches the *backend CLI* is the
backend's own choice, and `carriers` on `amicus_backends(detail="full")` is authoritative. Today:

| | Prompt carrier | `instructions_append` | Exposure to note |
| --- | --- | --- | --- |
| `codex` | stdin | **argv**, as the `-c developer_instructions` override | Visible to any local process listing for the run's duration — never put a secret here |
| `claude` | stdin | stdin | argv carries only fixed text and flags |
| `kimi` | a file in a private temp dir outside the workspace; argv carries only its path | same file | Nothing you type rides argv. amicus removes its temp directory when the run ends, but the kimi CLI also writes its own session store (observed under `~/.kimi-code/sessions`), which can hold the whole prompt and any answer produced; amicus does not delete it, and job expiry, the per-workspace cap and `amicus_job_consume_result` never touch it |

Each backend also loads context you did not supply — `AGENTS.md`, skills, and on `claude` under
`inherit`/`scoped`, workspace hooks that run outside the tool allowlist. `implicit_context` on
`amicus_backends(detail="full")` is the authoritative per-backend statement.

## Judgment call: which backend for a consult or review

Beyond the feature gate and the evidence question, amicus does not rank backends. Weigh:

- **Model diversity, carefully.** Different backend IDs do **not** establish different model
  families. Kimi routes to whatever OpenAI-compatible provider its `config.toml` names, and its
  `model` argument is a configuration alias — `amicus_models` exposes aliases and display labels,
  never verified provider identity. When you have reliable, non-sensitive information about the
  underlying models, weigh family diversity alongside task fit; when you do not, record the
  diversity as **unverified** rather than assuming it. Separate attempts and complementary review
  scopes are still useful without it — they just do not establish independence or correctness on
  their own.
- **Task shape.** A broad architectural question benefits from strong general reasoning; a narrow
  mechanical review benefits more from speed and cost. `amicus_models` lists each backend's
  advertised models and reasoning-effort sets.
- **What is usable right now.** A backend that is enabled but unauthenticated, rate limited, or
  reporting `status.warnings` is not a good choice however well suited it looks.
- **Effects.** Approval friction follows the worst *enabled* backend, not the one you pick for
  this call (SKILL.md → Annotations follow the worst enabled backend). Picking a different
  `backend` does not change it, and `effects` is a static per-backend declaration rather than a
  statement about your call.

When none of these distinguishes the eligible backends, pick any ready, eligible one.
