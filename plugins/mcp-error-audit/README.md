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
  Dates must be real calendar dates, and `--since` must not fall after `--until`.
- `--group-by version|fingerprint`, `--unknown include|exclude|only`

## Denominators

Never a statistic whose denominator is a guess.

- The **header** rate's denominator is whichever calls are currently in scope, and its label names that set.
  Unscoped, that is every matched call, attributed or not.
  Scoped (`--server-version`, `--fingerprint`, `--since`/`--until`, `--unknown`), it is only the calls that scope selected — never "all matched calls".
- The **matrix** prints one column per release (or fingerprint) *observed in the calls* — so a release you are running that has produced no errors yet still appears, with a zero row over a real call count.
  Each column carries its own `calls` denominator and an `err%` over it; no release's rate borrows another's calls.
  Columns sort in natural (numeric-aware) order for readability — that is not a release chronology, and it does not order prereleases correctly.
- Calls whose release cannot be read from their own result land in an `unknown` column with a reason (`no_result`, `unparseable_result`, `not_emitted`), and the report is flagged **PARTIAL**.
  They never inflate a release's denominator.
- An undated call excluded by `--since`/`--until` is a different case: it may carry a perfectly good version, so it is not filed as unattributed.
  It is excluded from the report entirely, like any other scope exclusion, and counted separately so it never vanishes silently.

A zero in the matrix means **not observed in that release over that column's calls** — not "fixed".
This tool reads transcripts; it cannot see a code change.

## Known risk: the slash command interpolates your arguments into a shell string

`commands/mcp-error-audit.md` passes `$ARGUMENTS` into a shell command as `--args '$ARGUMENTS'`.
Arguments containing a single quote can close it and run arbitrary shell commands — e.g. `/mcp-error-audit x' ; echo pwned #` executes `echo pwned`.

The script's quote check does **not** prevent this (bash tokenizes the string before Python starts, so no quote ever reaches Python), and neither does the command's `allowed-tools` entry (it is a prefix match, which the injected string still satisfies).

This is a knowingly accepted risk, not an oversight: it predates release scoping, and the only caller is your own slash command, typed by you, in your own shell.
Closing it properly means not interpolating `$ARGUMENTS` into a shell string at all.
Do not paste arguments you did not write.

## How it works

The command runs the bundled `scripts/mcp_error_audit.py` (Python 3, standard library only) against your session transcripts under `~/.claude/projects/`.
It works only with Claude Code, since it reads Claude Code's transcript format.
No data leaves your machine.

## Development

Run the tests with `uvx pytest plugins/mcp-error-audit/tests/ -q` from the repository root.
