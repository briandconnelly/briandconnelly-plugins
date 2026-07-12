# mcp-error-audit

A Claude Code slash command that audits MCP tool errors across all of your Claude Code sessions and gives you a prioritized, actionable list of what to fix.

## Usage

Run `/mcp-error-audit` with no argument for **discovery mode** — every MCP server seen in your session transcripts, ranked by error rate (servers with fewer than 5 calls are listed last).

Run `/mcp-error-audit <server>` to **audit** one server.
The command aggregates every error returned by that server's tools across all your sessions, classifies each error code, and returns a prioritized fix list.

## How it works

The command runs the bundled `scripts/mcp_error_audit.py` (Python 3, standard library only) against your session transcripts under `~/.claude/projects/`.
It works only with Claude Code, since it reads Claude Code's transcript format.
No data leaves your machine.

## Development

Run the tests with `uvx pytest plugins/mcp-error-audit/tests/ -q` from the repository root.
