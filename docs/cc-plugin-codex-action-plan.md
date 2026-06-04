# cc-plugin-codex Improvement Action Plan

> **Status: implemented (2026-06-04).** All 8 items shipped, reviewed by Codex, with the
> `FINGERPRINT` bumped `schema-8` → `schema-9` and the plugin version `0.1.1` → `0.1.2`.
> Item 1 uses the warn-in-meta approach. 135 tests pass (12 new); `ruff check` clean.


Derived from [the evaluation scenarios](./cc-plugin-codex-evaluation-scenarios.md), cross-checked against the source, and reviewed independently by Codex.
Two issues were found that the evaluation missed (items 6 and 7 below).

All paths are under `plugins/cc-plugin-codex/`.

## Fingerprint policy

Several items change the agent-visible surface (new tools, new `meta` fields, error wording, capability guarantees), which by the policy in `schemas.py` requires bumping `FINGERPRINT`.
**Batch every accepted change into a single bump** (`schema-8` → `schema-9`) rather than incrementing per change.

## Tier 1 — Correctness / safety (do first)

### 1. Workspace silently defaults to the plugin install directory
- **Problem (confirmed).** `_resolve_workspace` (`server.py:163-185`) falls back to `os.getcwd()` when there is no `workspace_root` arg and no MCP root. Because the server process launches from its own install dir, paid reviews silently run against the wrong repo. The evaluation reproduced this (`meta.cwd` = `plugins/cc-plugin-codex`).
- **Decision point (recommended default below).**
  - *Recommended — warn, don't break:* when `workspace_source == "cwd"`, add a non-fatal `meta.workspace_warning` ("resolved from the server's own cwd; pass workspace_root to be sure"). Combined with item 2's dry-run and item 8's skill change, this fixes the footgun without breaking existing callers.
  - *Stricter — Codex's preference:* refuse paid calls with no `workspace_root` and no MCP root unless an opt-in env (`CC_PLUGIN_CODEX_ALLOW_CWD=1`) is set. Safer, but breaking.
- **Files:** `server.py` (`_resolve_workspace`, `_meta`), `schemas.py` (`Meta`).
- **Compat:** warn = additive; strict = breaking. Bumps fingerprint.

### 2. No free dry-run before paying
- **Problem (confirmed).** `gather_context` (`context.py:100-114`) already computes diff summary, byte size, truncation, and redacted paths — but nothing exposes it without a paid call. Agents can't see what they're about to send or where.
- **Fix.** Add a free, read-only `claude_review_dry_run(scope, base, focus?, workspace_root?)` returning: resolved `cwd` + `workspace_source`, `ContextSummary`, diff byte count, `truncated`/hint, and `redacted_paths` count + list. Pure reuse of existing code; no Claude call. This also subsumes the evaluation's "surface workspace in status" idea (item 9) more cleanly than touching `claude_status`.
- **Files:** `server.py` (new tool), `context.py` (expose `redacted_paths`/byte count it already gathers), `schemas.py` (new result model).
- **Compat:** additive. Bumps fingerprint.

### 3. `claude_status` leaks account email + org
- **Problem (confirmed).** `auth_status` (`claude.py:47-59`) returns `claude auth status --text` verbatim as `auth_detail`; `claude_status` passes it straight through (`server.py:652`). Sensitive in shared logs/transcripts.
- **Fix.** Keep the boolean `claude_authenticated`; redact `auth_detail` to a non-identifying summary by default (strip email/org), or drop it. Optionally gate the raw string behind `detail="full"`.
- **Files:** `claude.py` (`auth_status`), `server.py` (`claude_status`), `schemas.py` (`StatusResult`).
- **Compat:** changing/removing `auth_detail` is breaking. Bumps fingerprint.

## Tier 2 — Accuracy of the budget contract

### 4. Budget cap is not a hard cap, and `meta` doesn't echo what was requested
- **Problem (confirmed).** `--max-budget-usd` is a Claude-CLI stop threshold, not a hard ceiling — the evaluation saw `$0.01` cap → `$0.048` actual. Worse, the code itself overstates it: `jobs.py:12` ("`--max-budget-usd` still hard-bounds spend"), `server.py:438-439` ("budget cap"), `claude.py:142-145` ("max-budget cap"). `Meta` (`schemas.py:72-90`) reports `cost_usd` but never the requested budget, so an agent can't compare requested vs actual without retaining call args.
- **Fix.**
  - Add `meta.requested_max_budget_usd` (set in `_execute`/job start).
  - Replace "hard-bounds"/"cap" wording with "stop threshold (best-effort, may be exceeded)" in `jobs.py`, `server.py`, `claude.py`, README, and SKILL.
- **Files:** `schemas.py`, `server.py`, `jobs.py`, `claude.py`, `README.md`, `SKILL.md`.
- **Compat:** `meta` field is additive; wording is docs. Bumps fingerprint (meta schema change).

## Tier 3 — Ergonomics & discovery

### 5. No `claude_job_list`
- **Problem (confirmed).** Jobs persist on disk keyed by workspace (`jobs.py:70-79`), but every lifecycle tool needs a known `job_id`. Ids are lost across context compaction with no way to recover them.
- **Fix.** Add free `claude_job_list(workspace_root?)` returning recent jobs (id, kind, status, started_at, expires_at, cost when terminal). Thin wrapper over `_ws_dir` + `_read_meta` + `_status_of`.
- **Files:** `jobs.py` (new `list_jobs`), `server.py` (new tool), `schemas.py` (new model).
- **Compat:** additive. Bumps fingerprint.

### 6. SKILL.md omits the `access=readonly` redaction caveat *(missed by evaluation)*
- **Problem (confirmed).** README.md:93-96 correctly warns that with `access=readonly` Claude can `Read`/`Grep`/`Glob` any workspace file, so filename-based redaction does **not** protect it. SKILL.md:38-40 — the agent-facing surface — omits this and reads as if redaction always protects secrets. This is a safety gap, not just a doc gap.
- **Fix.** Copy the README caveat into `SKILL.md` (and consider surfacing it in `cc_codex_capabilities` negative-scope/prerequisites).
- **Files:** `SKILL.md`, optionally `server.py` (`cc_codex_capabilities`).
- **Compat:** docs; fingerprint only if capability guarantees change.

### 7. Discovery: capability tool is hard to guess / not in deferred tool search
- **Problem (partly confirmed).** The evaluation found Codex's deferred `tool_search` didn't surface these tools and `cc_codex_capabilities` is an unlikely guess. The tool-search indexing behavior is inferred (not visible in our code); the naming point is valid regardless.
- **Fix.** Add a `claude_capabilities` alias for `cc_codex_capabilities`; enrich tool titles/descriptions with discovery keywords (review, second opinion, critique, adversarial).
- **Files:** `server.py`.
- **Compat:** additive. Bumps fingerprint.

### 8. Documentation gaps in the skill
- **Problem (confirmed).** SKILL.md lacks: a realistic minimum budget (the evaluation saw `$0.01` too low; ~`$0.02` for a one-sentence ask, more for reviews), the "budget is a stop threshold, not a hard cap" note (item 4), and prominent "pass `workspace_root` on the first call" guidance (item 1).
- **Fix.** Add a short budget-expectations note, the stop-threshold clarification, and move `workspace_root` guidance to the top of the skill.
- **Files:** `SKILL.md`, `README.md`.
- **Compat:** docs only.

## Items deliberately NOT pursued

- **Workspace info inside `claude_status`** (evaluation Scenario 2/6): `claude_status` has no `ctx`, so it can't resolve roots. The dry-run tool (item 2) covers this better — skip.
- **Preflight cost estimate / "ping Claude" path** (Scenario 4): the CLI offers no cheap no-work probe; the dry-run (size/redaction) is the practical substitute. Skip the cost estimate.

## Reconciled with Codex review (locked decisions)

- **Item 1:** warn-in-meta chosen (not hard-require). Set `meta.workspace_warning` centrally in `_meta(...)` when `workspace_source == "cwd"`; name the resolved `cwd` and tell the caller to pass `workspace_root`. Preserve it through `jobs._build_meta`.
- **Item 2:** `ContextResult` already returns `redacted_paths`, but `server.py` drops it and there is no byte count anywhere — add `diff_bytes` to `ContextResult` and surface both. Dry-run uses a dedicated result model (no `verdict`, no diff text): `cwd`, `workspace_source`, `workspace_warning`, `scope`, `base`, `context_summary`, `diff_bytes`, `max_diff_bytes`, `truncated`, `truncation_hint`, `redacted_paths_count`, `redacted_paths`, `fingerprint`.
- **Item 3:** replace `auth_detail` with a non-identifying phrase (authenticated / not authenticated / probe failed). No `detail="full"` backdoor — the boolean already carries the machine-readable truth.
- **Item 4:** `meta.requested_max_budget_usd` = the **effective clamped** budget (`r.budget`), thread it everywhere `_meta` is built (incl. error/truncation metas) and persist it in `JobConfig` → `_build_meta`.
- **Item 5:** `claude_job_list` is NOT read-only — `_reap_workspace`/`_status_of` write metadata and may kill timed-out jobs. Annotate it with the local-mutation hints like `claude_job_status`.
- **Item 7:** implement `claude_capabilities` via a shared helper so it can't drift from `cc_codex_capabilities`. Frame as name/description discoverability only — deferred tool-search behavior is a client concern we can't guarantee.
- **Also (README wording):** fix "caps cost" / "budget cap bounds spend" at README.md:99,120 alongside item 4.
- **Tests that will break:** `schema-8` is hardcoded in `test_schemas.py` and `test_server.py`; `JobConfig` gains a field so `test_jobs.py::_cfg()` needs a default/update; `test_context.py` for the new byte/redaction surfacing; `test_claude.py` for budget wording. Bump `FINGERPRINT` to `schema-9` **last**, in one pass with the test updates.

## Suggested execution order

1. Items 4 + 8 (meta field + wording/doc fixes) — cheap, no risk, sets the budget story straight.
2. Items 3 + 6 (redaction: status leak + skill caveat) — safety, small.
3. Item 2 (dry-run tool) — highest ergonomic payoff, reuses existing code.
4. Item 1 (workspace warning) — fold into item 2's work; decide warn vs. strict.
5. Items 5 + 7 (job_list + capability alias) — additive niceties.
6. Single `FINGERPRINT` bump to `schema-9` covering all surface changes; update tests in `tests/`.
