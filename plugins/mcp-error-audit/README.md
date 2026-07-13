# mcp-error-audit

A Claude Code slash command that audits MCP tool errors across all of your Claude Code sessions and gives you a prioritized, actionable list of what to fix.

## Usage

Run `/mcp-error-audit` with no argument for **discovery mode** — every MCP server seen in your session transcripts, ranked by error rate (servers with fewer than 5 calls are listed last).

Run `/mcp-error-audit <server>` to **audit** one server.
The command aggregates every error returned by that server's tools across all your sessions, classifies each error code, and returns a prioritized fix list.

Scope an audit to a release:

- `--server-version 0.10.0` — only calls whose result reported that release. **Observed.**
- `--fingerprint <fp>` — only calls whose result reported that contract fingerprint.
  **Observed**, and available for servers that do not (yet) stamp a version.
- `--since` / `--until` (`YYYY-MM-DD`) — only calls *started* in that window.
  **Approximate**: a date is a proxy for a release, and is wrong if you upgraded late.
- `--group-by version|fingerprint`, `--unknown include|exclude|only`

Calls whose release cannot be read from their own result are reported in an `unknown`
bucket with a reason, and per-version rates are computed over attributed calls only.

## How it works

The command runs the bundled `scripts/mcp_error_audit.py` (Python 3, standard library only) against your session transcripts under `~/.claude/projects/`.
It works only with Claude Code, since it reads Claude Code's transcript format.
No data leaves your machine.

## Development

Run the tests with `uvx pytest plugins/mcp-error-audit/tests/ -q` from the repository root.
