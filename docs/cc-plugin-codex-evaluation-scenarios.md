# cc-plugin-codex Evaluation Scenarios

Lightweight scenarios for checking whether the installed `cc-plugin-codex` tools are discoverable, predictable, and useful from Codex.

## Scope

These scenarios are intentionally small. They should be runnable in a normal Codex session without preparing a special fixture repository, and they avoid large paid Claude calls unless explicitly noted.

Use `max_budget_usd`, `timeout_seconds`, and low reasoning effort for paid-path probes. Treat paid results as sampled behavior, not a full acceptance test.

## Scenario 1: Discover The Tool Surface

Goal: Confirm an agent can identify what the plugin does and which tool to call first.

Steps:

1. Search for a Claude review or second-opinion capability through the available tool discovery mechanism.
2. Read the installed skill, if available: `cc-plugin-codex:collaborating-with-claude`.
3. Call `cc_codex_capabilities`.

Expected:

- The skill or tool list should make it clear that this plugin is review-only.
- `cc_codex_capabilities` should identify paid and free tools, access modes, config modes, prerequisites, and negative scope.
- The first low-risk tool should be obvious: `claude_status`.

Observed locally:

- The skill clearly explained the tool choice: `claude_ask`, `claude_review_changes`, `claude_review_changes_async`, `claude_adversarial_review`, and `claude_status`.
- `cc_codex_capabilities` returned a compact contract with fingerprint `cc-plugin-codex/0.1/schema-8`.
- Deferred `tool_search` for "Claude Code review second opinion adversarial critique status async job" did not surface these MCP tools; it returned unrelated multi-agent tools. Discovery currently depends on the upfront MCP tool list or the skill list.

Improvement candidates:

- Add discovery keywords to any surface that feeds deferred tool search, if available.
- Consider naming the capability contract tool `claude_capabilities` or adding an alias. `cc_codex_capabilities` is precise, but less likely to be guessed by an agent looking for Claude tools.

## Scenario 2: Readiness Check Before Spending

Goal: Verify an agent can check whether a paid call is likely to work before invoking Claude.

Steps:

1. Call `claude_status`.
2. Inspect `ready`, `claude_found`, `claude_authenticated`, `version_supported`, available config modes, default budget, and default timeout.

Expected:

- No paid Claude call should be made.
- The result should be a structured `ok:true` response.
- If the CLI is unavailable or unauthenticated, the response should give a repair path.

Observed locally:

- `claude_status` was fast and returned `ready:true`.
- It reported Claude Code `2.1.162`, authenticated OAuth details, supported version, config mode availability, budget bounds, timeout bounds, and a clear caveat that `config_mode=bare` needs `ANTHROPIC_API_KEY`.

Improvement candidates:

- The readiness result includes account email and organization. That is useful for debugging, but it is potentially sensitive in shared logs. Consider a `detail` option or redacted summary mode.
- Surface the default workspace resolution in `claude_status`. In this install, paid calls without `workspace_root` reported `cwd` as `plugins/cc-plugin-codex`, which may surprise users working from the repository root.

## Scenario 3: Minimal Free Error Path

Goal: Confirm free job-management tools fail clearly.

Steps:

1. Call `claude_job_status` with a fake job id such as `not-a-real-job-id`.

Expected:

- The response should be `ok:false`.
- The error should identify the bad parameter, explain whether retrying helps, and provide a repair hint.

Observed locally:

- The tool returned `ok:false`, `code:"job_not_found"`, `offending_param:"job_id"`, `retryable:false`, and a useful repair message.

Improvement candidates:

- This path behaved as expected.

## Scenario 4: Small Second Opinion

Goal: Verify the cheapest common paid path: a bounded free-form opinion.

Steps:

1. Call `claude_ask` with a one-sentence prompt.
2. Use `effort:"low"`, `access:"toolless"`, `config_mode:"inherit"`, and a small budget.

Expected:

- The tool should return `ok:true` with `tool:"claude_ask"`, `summary`, `verdict`, `confidence`, `findings`, `questions`, `assumptions`, `next_steps`, and `meta`.
- `meta` should include elapsed time, cost, usage, request id, and fingerprint.

Observed locally:

- With `max_budget_usd:0.01`, the tool returned `budget_exceeded`.
- With `max_budget_usd:0.10`, the tool succeeded in about 8 seconds and returned the documented envelope.
- The successful call cost about `$0.018`.

Improvement candidates:

- Document a realistic minimum budget for even tiny calls. `$0.01` was too low for a one-sentence prompt in this environment.
- Consider exposing a preflight estimate or a cheaper "ping Claude with no model work" path if the Claude CLI supports it.

## Scenario 5: Budget Guard Behavior

Goal: Verify budget failures are understandable and bounded.

Steps:

1. Call `claude_ask` or `claude_review_changes` with an intentionally tiny `max_budget_usd`.
2. Inspect the error envelope and `meta.cost_usd`.

Expected:

- The tool should fail with `ok:false`, `code:"budget_exceeded"`, and `retryable:true`.
- The repair should suggest raising budget or reducing context.
- Actual spend should be close to or below the requested cap, subject to Claude CLI behavior.

Observed locally:

- Both `claude_ask` and `claude_review_changes` produced clear `budget_exceeded` envelopes.
- Reported cost exceeded the requested cap in both probes:
  - Ask: cap `$0.01`, reported cost about `$0.048`.
  - Working-tree review: cap `$0.05`, reported cost about `$0.077`.

Improvement candidates:

- Document that `max_budget_usd` is enforced by the Claude CLI and may not be a hard upper bound in reported accounting.
- If feasible, add server-side budget wording such as "stop threshold" rather than "cap", or apply a conservative lower value when invoking Claude.
- Include the requested budget in `meta` so an agent can compare requested versus actual without retaining call arguments.

## Scenario 6: Working-Tree Review

Goal: Verify a normal code-review call targets the intended repository and handles context size.

Steps:

1. Create or use a small tracked working-tree diff.
2. Call `claude_review_changes` with `scope:"working_tree"`.
3. Pass `workspace_root` explicitly.

Expected:

- The tool should review the diff and return structured findings.
- `meta.workspace_source` should be `param`.
- Findings should include concrete file and line references when possible.

Observed locally:

- Passing `workspace_root:"/Users/bdc/projects/briandconnelly-plugins"` correctly changed `meta.cwd` to the repository root and `workspace_source` to `param`.
- A low-budget review hit `budget_exceeded`, but the failure path was structured and useful.
- Calling paid tools without explicit `workspace_root` used the plugin directory as cwd in this install.

Improvement candidates:

- Make the "pass explicit workspace_root" guidance more prominent in the skill, not only the README.
- Consider adding a warning in paid-call metadata when the resolved workspace appears to be the plugin install directory.
- Add a cheap dry-run tool for diff size and workspace resolution, so agents can inspect what would be sent before paying.

## Scenario 7: Async Review Lifecycle

Goal: Verify the background job lifecycle is understandable for large diffs.

Steps:

1. Start `claude_review_changes_async` against a small diff.
2. Confirm the response includes `job_id`, `status`, `poll_after_ms`, and TTL metadata.
3. Poll `claude_job_status`.
4. Fetch with `claude_job_result` once `result_available:true`.
5. Optionally call `claude_job_consume_result` to delete the result, or `claude_job_cancel` while running.

Expected:

- Starting the job should return quickly.
- Status polling should not require knowing Claude internals.
- Result shape should match synchronous `claude_review_changes`.

Observed locally:

- Not run in this pass to avoid another paid review.
- The free fake-job status path behaved correctly.

Improvement candidates:

- Provide a documented "cancel immediately" smoke test for async jobs with a tiny fixture diff and low budget.
- Consider a free `claude_job_list` tool scoped to the workspace. Agents often lose job ids across context compaction or user interruption.

## Overall Assessment

The installed tools are usable and mostly behave as documented. The strongest parts are the compact capability contract, the free readiness check, the clear `ok`-discriminated result envelopes, and the explicit separation between free status/job tools and paid Claude calls.

The main rough edges are discoverability outside the skill/upfront tool list, surprising default workspace resolution in this install, budget-cap expectations, and the lack of a free dry-run that shows workspace, diff size, redaction count, and estimated context before invoking Claude.
