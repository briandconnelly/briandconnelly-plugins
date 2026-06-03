# cc-plugin-codex

Call Claude Code from Codex for bounded, independent code review and second opinions.
The mirror image of [`openai/codex-plugin-cc`](https://github.com/openai/codex-plugin-cc).

## What it does

An MCP server wraps the `claude` CLI and exposes four read-only tools to Codex:
`claude_ask`, `claude_review_changes`, `claude_adversarial_review`, `claude_status`.
Claude reviews; it never edits your code.

## Requirements

- The `claude` CLI installed and authenticated (`claude /login`), and `git`.
- `uv` for running the server.
- `config_mode=bare` additionally requires `ANTHROPIC_API_KEY`.

## Install (local dev)

This v1 runs from the local checkout (not yet published to PyPI).
Point Codex at the server via `.mcp.json` using an absolute path (see that file), or:
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
