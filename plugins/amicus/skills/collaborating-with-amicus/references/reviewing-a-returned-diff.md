# Reviewing a returned diff

`amicus_delegate` and `amicus_delegate_async` implement a task in a throwaway git worktree seeded
from the current branch's tracked state (`HEAD` plus replayable uncommitted tracked changes;
untracked files are never copied) and return the resulting `diff`. Delegation is available for
`codex` and `kimi` in v1; Claude stays review-only.

## The diff is never applied

amicus does not touch your working tree. The `diff` field in the result is a proposal, not a
change that already happened. Never run `git apply`, patch, or otherwise merge it into your tree
before you have reviewed it.

## What to check before applying

Work through these four before you say anything about applying. Each has a fixed key, given in
bold after its number; the response contract below is written in those keys.

1. `fidelity` — **Does it do what the task asked, and nothing more?** Read the diff against
   the original task text. Delegate runs have no network egress — an install, a remote git
   operation, `gh`, or a publish step cannot have happened inside the run, so a diff that
   references one is suspicious.
2. `scope` — **Does it touch files outside the intended scope?** A delegate task should be
   self-contained; a diff that edits unrelated files is a sign the task was under-specified or
   the model over-reached.
3. `checks-run` — **Does it compile / pass checks?** The delegate result is an unverified
   claim like any other amicus result — `summary` and `findings` describe what the backend did,
   not proof that it works. Apply the diff to a disposable branch or worktree and run this
   project's actual checks before merging it into real work.
4. `consistency` — **Is the diff internally consistent?** Check `diffstat` against the diff
   itself for a sanity check on scope before reading line by line.

## Response contract

Answer in exactly this shape. Earlier guidance here asked for the checks "first" and the verdict
"after"; three graded runs showed that a directive about the ORDER of generated prose does not
move generation order. This replaces it with a required output SHAPE, so the verdict has a
labelled place to come after rather than a rule to obey.

```
Checks:
- fidelity: <what you found>
- scope: <what you found>
- checks-run: <what you found, or "not run" and why>
- consistency: <what you found>

Verdict: applied | not applied — <one sentence>
```

Fixed vocabulary, and the whole of it: `fidelity`, `scope`, `checks-run`, `consistency`, in that
order, one line each. A check you could not perform is reported under its own key as `not run`
with a reason; it is never dropped. The words "apply", "applied", "not applied", "done" and any
other statement of the outcome belong after the `Verdict:` label and nowhere before the `Checks:`
label — including in any preamble. Prose may follow the `Verdict:` line freely.

## Applying it

Once you've reviewed the diff and are satisfied it is correct and in scope, apply it yourself
(e.g. `git apply`, or hand it to your own patch-application tooling) to your actual working tree.
amicus's job is done at "returned diff" — it does not offer an apply step, by design, so that a bad
or unwanted diff never touches your tree without a human or agent decision in between.
