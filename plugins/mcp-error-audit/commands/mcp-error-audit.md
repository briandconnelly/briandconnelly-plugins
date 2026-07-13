---
description: Audit MCP tool errors across all your Claude Code sessions and prioritize fixes
argument-hint: "[server name or substring — letters/digits/._- only; empty lists all servers]"
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/mcp_error_audit.py:*)
---

## Error audit data

!`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/mcp_error_audit.py --server '$ARGUMENTS'`

## Your task

If the section above shows an error message instead of a report, briefly explain what failed and how to fix the invocation, then stop.

**Discovery mode** (the report is a server list because no server was given):

1. Present the servers as ranked in the report: by error rate, with low-volume servers (fewer than 5 calls) listed last.
2. Note any you judge worth auditing, and say why (weigh error rate, call volume, session spread, and recency together — sample sizes are small, so no single cutoff applies).
3. Tell me to re-run `/mcp-error-audit <server>` for the one I pick.
4. Stop there.

**Audit mode**:

About the data: the report aggregates every error returned by the matched MCP server's tools across all of my Claude Code sessions.
Subagent transcripts are folded into their parent session, and both bare and plugin-hosted tool-name forms are merged — the `matched:` line shows which.
If the report includes a `WARNING:` that the matched servers look distinct, lead with that caveat and suggest narrowing the server argument, since the blended stats may mislead.
`repair` hints come from each code's most recent occurrence that included one.
Each sample line shows the occurrence date, the tool input, and the error text; `recov` counts errors followed later in the same transcript by a success of the same tool.

Analyze the report and give me a **prioritized, actionable list of what to fix** — do not just restate the table.
Apply this lens; if an `agent-friendly-mcp` skill is available, apply its failure-recovery and tool-design guidance in addition, and where the two conflict, the numbered lens below wins.

1. **Classify each code before ranking it.** The counts alone cannot distinguish deliberate error-path probes from an agent stuck retrying — both look like "high count, few sessions".
   Read the sample dates, inputs, and the `first_seen`/`last_seen` window, then classify:
   - Varied, deliberately-wrong inputs whose sample dates cluster in a narrow window: classify as **probe testing; deprioritize**. (A clean structured envelope with an accurate `repair` hint means the error contract is *working*, not failing.)
   - Near-identical repeated inputs: classify as **retry loop; prioritize** — that is an agent failing to recover.
   - Spread across a significant fraction of the server's sessions: classify as **recurring real friction; prioritize**. State the fraction you used (code's `sess` over the server-session denominator in the report header).

2. **Rank real issues by recurring impact.** Weight by fraction of server-sessions affected, then by `recov`: an error rarely followed by a later success of the same tool ended the attempt outright and hurts more than one agents routinely recover from.

3. **For each real issue, report:** the code, sessions affected, retryable, recovery rate, likely root cause, and a concrete fix (schema/param/description/timeout/repair-hint change).

4. **Check whether `repair` hints name real recovery paths.** If a retryable error's hint omits a better escape hatch (e.g. an `_async` variant for a timeout), flag it — repair hints should point at real callable surfaces.

5. **Discount stale signatures.** Judge staleness of a code's `last_seen` relative to the server's recent activity (the `last call` date in the report header), not by a fixed window: a code absent for a month means little if the server was idle too.
   A code old relative to recent activity, or whose samples reference tools/params no longer in the server's surface, is likely already fixed — say so rather than recommending a fix for it.

End with an ordered TODO list of at most 7 items.
