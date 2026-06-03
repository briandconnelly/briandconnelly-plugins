# cc-plugin-codex

Call Claude Code from Codex for bounded, independent code review and second opinions.
The mirror image of [`openai/codex-plugin-cc`](https://github.com/openai/codex-plugin-cc)'s
review surface.
Unlike that plugin, cc-plugin-codex is review-only: it does not delegate write-capable
tasks or run background jobs.

## What it does

An MCP server wraps the `claude` CLI and exposes four read-only tools to Codex:
`claude_ask`, `claude_review_changes`, `claude_adversarial_review`, `claude_status`.
Claude reviews; it never edits your code.

Each tool publishes an output schema describing the `ok`-discriminated result
(`{"ok": true, ...}` on success, `{"ok": false, "error": {code, message, repair}, ...}`
on failure). Failures also set the MCP `isError` flag, so branch on either `ok` or
`isError`. The surface is **experimental / pre-1.0**; clients should pin
`meta.fingerprint` to detect schema changes.
Invalid values for enum-typed parameters (`config_mode`, `access`, `scope`, `detail`)
are rejected by the MCP framework as a schema validation error before the tool runs,
rather than via the `ok:false` envelope; every other failure uses the envelope.

The paid tools (`claude_ask`, `claude_review_changes`, `claude_adversarial_review`)
**block synchronously** for up to `timeout_seconds` (default 180s, max 600s).
They can be **cancelled** by the client, which terminates the underlying Claude process, but not resumed.
Call `claude_status` first — it is
free and reports the resolved defaults (`config_mode`, `access`, `model`, clamped
`max_budget_usd`/`timeout_seconds`, and the clamp bounds) a no-argument paid call
would use, so you can predict cost and behavior before spending.
Every paid result also reports `meta.cost_usd` and `meta.usage` (token counts) so an
agent can track actual spend across calls.

## Requirements

- The `claude` CLI installed and authenticated (`claude /login`), and `git`.
- `uv` for running the server.
- `config_mode=bare` additionally requires `ANTHROPIC_API_KEY`.

## Install

Install from the `briandconnelly-plugins` Codex marketplace.
The bundled MCP config runs the server with `uvx` from this repository's
`plugins/cc-plugin-codex` subdirectory.

## Local dev

This v1 runs from the local checkout (not yet published to PyPI).
`codex mcp add cc-plugin-codex -- uv run --directory "$(pwd)" cc-plugin-codex-mcp`

## Config modes (`config_mode`)

| Mode | Isolation | Auth |
| --- | --- | --- |
| `inherit` (default) | normal Claude env, no persisted session | your existing login |
| `scoped` | drops user-global settings + user MCP servers; keeps CLAUDE.md | your existing login |
| `bare` | strips CLAUDE.md/memory/hooks | requires `ANTHROPIC_API_KEY` |

Known limitation: in `claude 2.1.161` there is no OAuth-preserving way to fully strip
`CLAUDE.md`/memory — full independence (`bare`) requires an API key.

## Access modes (`access`)

`toolless` (default) sends Claude the diff as text; `readonly` lets Claude use `Read,Grep,Glob`
to pull extra context. Claude never gets write or Bash tools.

## Workspace

The diff-bearing tools operate on a workspace resolved in this order: an explicit
`workspace_root` argument, then the client's first MCP root, then the server's own
working directory.
`meta.workspace_source` reports which rule applied.
Pass `workspace_root` explicitly when launching the server from a fixed directory (e.g. a
plugin install) so reviews target your project rather than the plugin checkout.

## Safety

- Read-only: Claude is never given write or Bash tools.
- Secret redaction is filename-based (`.env`, `*.env`, `*.pem`, `*.key`, key files), not
  content-scanning — it will not catch secrets hardcoded inside ordinary source files.
- Diff redaction only applies to the context the server gathers. With `access=readonly`,
  Claude can read any file in the workspace directly (`Read`/`Grep`/`Glob`), so redaction
  does NOT protect against secrets it reads itself — use `access=toolless` (the default)
  when the workspace may contain secrets.
- All `config_mode`s drop your other MCP servers, but `inherit`/`scoped` still load your
  user-level Claude hooks and settings; use `config_mode=bare` for full isolation.
- Each call is paid and sends code to Anthropic; the server caps cost and time per call.

## Environment variables

`CC_PLUGIN_CODEX_CLAUDE_CONFIG`, `CC_PLUGIN_CODEX_ACCESS`, `CC_PLUGIN_CODEX_MODEL`,
`CC_PLUGIN_CODEX_MAX_BUDGET_USD`, `CC_PLUGIN_CODEX_TIMEOUT_SECONDS`, `ANTHROPIC_API_KEY`.
