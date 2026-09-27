# Reviewing a returned diff

`amicus_delegate` and `amicus_delegate_async` implement a task in a throwaway git worktree seeded
from the current branch's tracked state (`HEAD` plus replayable uncommitted tracked changes;
untracked files are never copied) and return the resulting `diff`. Delegation is available for
`codex` and `kimi` in v1; Claude stays review-only.

## Rules

- **Never apply a returned diff to the user's working tree before working through the four checks
  below.**
- **Answer in the response contract**, with every fixed key present, in order.
- **Report a check you could not perform as `not run`, with a reason.** Never drop a key.
- **Read the run's containment facts for the backend that produced the diff** before concluding
  what could not have happened inside it.
- **Applying a diff in a disposable worktree is part of reviewing it, not an exception to the
  rule above.** Validate there; never validate in the user's working tree.
- **State the assessment and the action separately.** They are different facts.
- **Never use `diffstat` as the integrity check on `diff`.** They are computed at different points.
- **Never report `Action: applied` for a diff whose `Verdict:` is `reject` or `cannot-assess`.**

## What amicus guarantees, and what it does not

amicus does not touch your working tree: the `diff` is a proposal, and amicus never applies it.

That is a statement about **patch application**, not about **process containment**. It does not
follow that the run changed nothing anywhere. What the run itself could do depends entirely on the
backend, and the delegate tool description discloses that writes may land in temporary roots
outside the returned diff.

| | `codex` | `kimi` |
| --- | --- | --- |
| OS sandbox | yes — codex's `workspace-write` | **none** |
| Network egress during the run | **blocked** — amicus pins `sandbox_workspace_write.network_access=false` | **not blocked** |
| Writes bounded to the worktree | no — the sandbox also permits the OS temp roots (`/tmp`, `$TMPDIR`) | no — the worktree sets kimi's working directory, not its reach |
| Approvals | codex's own | none |

**So an inference from blocked egress holds for `codex` only, and only for operations that
actually reach a network service** — a push or fetch against a remote host, an install from a
remote index, a publish to a registry. It does not establish that nothing executed: blocked egress
bounds what a command may reach, not whether commands ran. A local install from an on-disk
artifact, a `gh --version`, and a push or fetch against a **filesystem** remote all need no
network, so none of them is excluded. On `kimi` even the egress half does not hold: a task can reach the network and
write outside the worktree with the user's own privileges. On either, the returned diff shows what
changed *in the worktree*, not everything the run did. Confirm the backend before relying on any of this, and read `effects`
and `egress` on `amicus_backends(detail="full")` rather than assuming the table above is still current.

## What to check before applying

Work through these four before you say anything about applying. Each has a fixed key, given in
bold after its number; the response contract below is written in those keys.

1. `fidelity` — **Does it do what the task asked, and nothing more?** Read the diff against the
   original task text. On `codex`, a diff that depends on having reached the network — a fetch,
   a push, a publish, a remote-index install — is suspicious, because egress was blocked; a local
   command that needed no network is not. On `kimi` neither is anomalous, which is a reason to
   scope kimi delegate tasks more tightly, not a reason to trust the diff more.
2. `scope` — **Does it touch files outside the intended scope?** A diff that edits unrelated
   files is a sign the task was under-specified or the model over-reached.
3. `checks-run` — **Does it compile / pass checks?** The delegate result is an unverified claim
   like any other: `summary` and `findings` describe what the backend says it did, not proof that
   it works. Apply the diff in a **throwaway worktree** — `git worktree add` on a scratch path,
   not a branch in the user's checkout, which shares that working tree — and run the checks the
   change actually touches.
4. `consistency` — **Is the diff internally consistent?** Read the hunk headers against the hunk
   bodies. `diffstat` is barred as the integrity check by a rule above, and this is why:
   `diffstat` is computed from the raw diff *before* redaction and truncation are applied to the
   `diff` field, so the two legitimately disagree whenever `meta.redacted_paths` is non-empty or
   `meta.truncated` is true. Check those two fields first; only a disagreement they do not explain
   is a real inconsistency.

## Response contract

Answer in exactly this shape.

```
Checks:
- fidelity: <what you found>
- scope: <what you found>
- checks-run: <what you found, or "not run" and why>
- consistency: <what you found>

Verdict: accept | reject | cannot-assess — <one sentence>
Action: applied | not applied — <one sentence>
```

Fixed vocabulary, and the whole of it: `fidelity`, `scope`, `checks-run`, `consistency`, in that
order, one line each. A check you could not perform is reported under its own key as `not run`
with a reason; it is never dropped.

`Verdict:` reports your **assessment of the proposal** — whether the diff is correct and in scope.
`Action:` reports **what you did to the working tree**. They are independent: `accept` /
`not applied` is a normal outcome when the user has not asked you to apply it yet, and
`cannot-assess` / `not applied` is the honest result when you could not obtain the evidence. The
one combination a rule above forbids is `Action: applied` under a `reject` or `cannot-assess`
verdict.

The words "apply", "applied", "not applied", "accept", "reject" and "done" belong on the labelled
`Verdict:` and `Action:` lines and nowhere before the `Checks:` label — including in any preamble.
Prose may follow the `Action:` line freely.

## Applying it

Once the diff is reviewed and you are satisfied it is correct and in scope, apply it yourself
(e.g. `git apply`, or your own patch-application tooling). amicus's job is done at "returned
diff" — it does not offer an apply step, by design, so that a bad or unwanted diff never touches
your tree without a decision in between.

## Handing a returned diff to a second backend

A diff produced by one backend can be reviewed by another. Pass it inline to `amicus_consult`, or
apply it in a throwaway worktree and run `amicus_review_changes` there against that worktree's
`workspace_root`.

Record which artifact the second backend actually saw — the raw diff text, or a worktree with the
diff applied. They support different conclusions: a backend given only the diff text cannot check
whether the result builds, and a backend given the worktree cannot see what the diff did *not*
touch unless you scope the review to it. State which one you used when you report the outcome,
and count both calls against the workflow's declared cap.
