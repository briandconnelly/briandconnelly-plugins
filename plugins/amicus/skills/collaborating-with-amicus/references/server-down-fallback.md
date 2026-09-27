# Server-down fallback

Use this only after an MCP transport error shows the amicus server is unavailable — a connection
failure, not a tool returning `ok: false`. A tool that answers is not a server that is down.

## Rules

- **Ask the user to restart or reconnect the amicus MCP server first**, and confirm recovery with
  free `amicus_backends`.
- **Prefer a sibling MCP server over a hand-rolled CLI call.**
- **Never write prompt text to a file or onto a command line in a fallback you construct.**
- **Never construct a write-capable or delegate-tier fallback.** Restore the server instead.
- **Keep every flag in a fallback command.** If the CLI rejects one, stop and report the drift —
  never drop a flag to make the command run.
- **Never use the direct CLI route when only the supplied prompt may be disclosed.** It reaches
  the filesystem; the prompt is not the limit of what it sends.
- **Point `WORKSPACE` only at a directory the user has approved for disclosure.**
- **Never carry amicus's parameter names or guarantees onto another server's surface.**
- **Never retry either route while the transport condition is unchanged.**

## Why the plugin path is preferred

Going around amicus loses workspace-aware diff gathering, the input byte cap, best-effort
redaction of gathered diffs and returned output, structured results with `error.repair`, and the
job record. Everything you gather, bound, and sanitize in a fallback, you do by hand — and the
answer comes back as prose you must treat as an unverified claim with no envelope.

## First: a sibling server

`codex-in-claude` and `moonbridge` are separate MCP servers wrapping the same two CLIs. If either
is connected while amicus is not, it is the better fallback: it keeps redaction, bounded input,
and structured results, and its own skill states its guarantees.

Use that server's own tools and its own skill. The surfaces differ, and a guarantee that holds on
one server is not evidence about another — which is what the rule above is protecting.

## Last resort: the codex CLI directly

`codex` is the only direct CLI fallback documented here. Its prompt travels on **stdin**, so a
hand-rolled call can stay inside AGENTS.md rule 18. Read-only consult only:

```sh
codex exec \
  --json \
  --sandbox read-only \
  --cd "$WORKSPACE" \
  --skip-git-repo-check \
  --ephemeral \
  --ignore-user-config \
  --ignore-rules \
  --disable remote_plugin \
  --disable sleep_tool \
  --disable goals \
  -
```

Send the prompt on stdin; the trailing `-` is what selects it.

Every flag here is one amicus supports and never gates on `--help` parsing
(`src/amicus/backends/codex/contract.py`, `ALWAYS_SEND_FLAGS` and `MODEL_RUN_DISABLED_FEATURES`),
selected here for codex's strictest config isolation: no persisted session, no
`$CODEX_HOME/config.toml`, no execpolicy rules, no remote-plugin connectors, and an explicit
working root instead of the ambient directory.

**That is not what an ordinary amicus call sends.** `ALWAYS_SEND_FLAGS` classifies flags as
guarantee-bearing rather than help-gated; it does not mean every one is emitted every time.
`--ignore-user-config` and `--ignore-rules` come from `isolation_flags()`, which returns nothing at
the default `inherit` isolation, and `--skip-git-repo-check` is emitted conditionally. So this
command is deliberately stricter than the default paid path, not a reproduction of it.

`--disable sleep_tool` is spend hygiene rather than a guarantee — it removes a native sleep whose
single call can last up to 12 hours, and no server deadline bounds this route. `--disable goals` is
the same kind of hygiene: it removes codex's goal tools, whose active goal can prompt codex to start
another turn after the answer. `--ephemeral` already makes `create_goal` fail; the disable removes
the tools rather than relying on that.

The two rules above govern `WORKSPACE` and when this route may be used at all.

**Verification status of this command.** Each flag was checked against `codex exec --help` on
codex-cli 0.153.4 and is accepted there; a negative control confirmed the check can detect an
absent flag. What that establishes is flag *acceptance* on one version, not that the combination
behaves as described, and `codex_status` reports 0.153.4 as outside amicus's tested set. The
command has not been executed end to end. Confirm each flag is still accepted rather than
assuming; a rejection is CLI drift worth reporting, never a flag to drop.

## Why there is no kimi or claude fallback here

`kimi` ignores stdin and crashes on a long argv, so any hand-rolled kimi call must put the prompt
in a file or on the command line. Rule 18 exempts *amicus's own* handshake file — a disclosed
carrier surfaced on `amicus_backends` — and that exemption does not extend to a command you
compose yourself. **There is no rule-18-compliant hand-rolled kimi fallback. Restore the server.**

`claude` does take its prompt on stdin, so rule 18 is not what stops it. Its read-only tier is a
tool allowlist assembled from several flags rather than a single sandbox switch, and no verified
invocation is published here.

Those flags are not interchangeable. `--tools` is the **primary** allowlist and
`--disallowed-tools` is defense in depth, so losing them is not the same failure: omitting
`--disallowed-tools` while `--tools Read,Grep,Glob` still stands leaves a read-only run with one
layer gone, whereas getting `--tools` wrong leaves a write-capable one. Since an improvised command
gives you no way to know which you built, do not improvise one.

## What still applies

Everything in SKILL.md → Semantics → Data exposure. A fallback changes the transport, not the
exposure: the backend still reads files outside the workspace, still auto-loads `AGENTS.md` and
discovers skills from outside it, and still sends what it reads to its provider. Redaction does
not cover what you supply, and none of it covers a route amicus is not on.
