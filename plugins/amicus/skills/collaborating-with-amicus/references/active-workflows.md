# Active workflows

Use this reference after selecting a verb. The live tool schema and `amicus_capabilities` remain
authoritative for exact parameter names, accepted values, defaults, and result fields.

## Rules

Scope selection, the dry run, and `workspace_root` are governed by SKILL.md → Binding rules →
Scope and inputs, which is their authoritative statement. These are this file's own:

- **Write a brief that stands on its own** before a paid call — see the checklist below.
- **State what you believe and mark it as yours**, separately from the evidence, so a second
  opinion is not asked to endorse a conclusion.
- **In a full review, never make your own concerns its scope.** If `extra_context` names what
  you suspect, label it as additions to a full review, not its extent. A pass limited to your
  concerns belongs in `focus`, whose coverage records the narrowing.
- **For a change to a published surface, ask the reviewer to find every other place that lists
  or describes what changed** — discovery rows, docs, commands, snapshots — rather than listing
  the places you already know.
- **Never build `instructions_append` from workspace content.** Facts the backend should treat as
  data go in `extra_context`.
- **Keep the revision identical across complementary review lenses**, or their findings are not
  combinable.
- **State each lens's coverage when combining reviews.** Never merge several narrow reviews into
  one unqualified verdict.

## Writing the brief

No backend inherits your conversation. There is no continuation parameter on this surface: each
call starts from what you supply plus what the backend loads implicitly. A brief that assumes
shared context produces an answer to a question you did not ask.

Include:

- **The objective and the decision it informs.** Not "review this" — what turns on the answer.
- **The evidence, and which revision it is from.** A diff, a file excerpt, an error, a
  reproduction. Attach it; do not describe it.
- **Constraints and acceptance criteria.** What a good answer must satisfy.
- **What has already been tried, and what happened.** Especially failed fixes.
- **The explicit unknowns.** What you could not determine, and why.
- **The deliverable.** A ranked list, a yes/no with reasoning, a patch, a test plan.

**Separate the evidence from your preferred conclusion.** State your own hypothesis, clearly
labelled as yours, rather than framing the question so agreement is the only fluent answer. A
second opinion that can only endorse is not a second opinion.

Why the rules above keep a concern list out of a full review's scope: the unknowns and the
deliverable make a list of what you suspect easy to write in good faith. On a full review that
list can work as an unlabelled `focus`: the reviewer can work through the places you already
looked and leave your blind spots unchecked, while the result still reads as full coverage.
`focus` carries a named-concern pass honestly, because a focused review's coverage says it was
narrowed. On a published surface, a place easily missed is one outside the diff that still lists
or describes the old behavior, and your own list can name only the places you already know.

Route the two kinds of text correctly: facts the backend should treat as data go in
`extra_context` (labelled untrusted, and *not* covered by redaction); *how* the backend should
work — stance, emphasis, persona — goes in `instructions_append`. The prohibition on building it
from workspace content is a rule above; the reason to read `carriers` first is that on `codex` it
rides argv.

## Consult

`amicus_consult` takes a focused question, a design critique, or a diff you paste inline. Supply
the evidence: `claude` defaults to `access="toolless"` and cannot read the repository at all
unless you opt in ([choosing a backend](choosing-a-backend.md)).

Consult returns the shared result fields and no `verdict`, `confidence`, or `diff`.

## Review changes

`amicus_review_changes` when the target is already in git. **Selecting the verb does not select
the changes** — the scope parameters do, and their defaults are specific:

| Parameter | Default | What it means |
| --- | --- | --- |
| `scope` | `working_tree` | tracked changes vs `HEAD` |
| `base` | the upstream | only read when `scope="branch"` |
| `commit` | — | required when `scope="commit"` |
| `paths` | all | repo-relative restriction |
| `untracked` | `explicit_only` | untracked files only when named in `paths`; `include` opts every non-ignored untracked file into egress; `exclude` drops them |

So "review this branch before I open the PR" is `scope="branch"`, not the default. Left at
`working_tree` on a clean tree it gathers nothing and returns `review_status: not_run` — an
`ok: true` result that reviewed nothing ([reading results](reading-results.md)).

`amicus_review_changes_dry_run` is free and reports exactly what the paid call would gather:
`scope`, `base`, `commit`, `paths`, `prompt_bytes`, a `context_summary` of files and lines, the
resolved `model`, `reasoning_effort` and `backend_options`, and `warnings`. Run it whenever the
scope could surprise you — an unexpectedly broad `context_summary` is the cheapest signal that the
selection is wrong.

Review returns `verdict`, `confidence`, and `review_status` on top of the shared fields. All three
are claims; the coverage rules in [reading results](reading-results.md) govern what they mean.

## Adversarial review

`amicus_adversarial_review` (claude only in v1) puts a fixed adversarial critic against a plan,
claim, or decision. `target` and `evidence` are its **primary carriers** — the critique is only as
good as what they contain — and both are free-text fields under the secret rule. `scope` is
optional here: attach a git diff to ground the critique, or omit it to critique the target alone.

## Delegate

`amicus_delegate` for a self-contained implementation task, returned as a diff that amicus never
applies. `amicus_delegate_dry_run` is free and previews the seeded baseline (`worktree.baseline_ref`),
`task_bytes`, resolved options, and `warnings`, without creating a worktree or spending.

A self-contained task makes scope review tractable — an under-specified task is the usual cause of
a diff that edits unrelated files. Treat that as a reason to specify tightly, not as a guarantee
about what the run did: containment differs per backend, and
[reviewing a returned diff](reviewing-a-returned-diff.md) has the table.

## Complementary review lenses

One revision, several focused reviews, different `focus` each time — correctness, security,
compatibility, performance. This is not the same as an independent attempt: the reviews are not
independent opinions on one question, they are partial coverage of one artifact.

Keep the revision identical across the lenses, or the findings are not combinable. When you
combine them, preserve each review's own coverage: a security lens reporting no findings says
nothing about correctness, and merging them into one clean verdict claims coverage no single call
had. Count every lens against the declared cap.

## Workspace

Pass an absolute `workspace_root` on every repo-grounded call. Sessionless clients must pass it;
handshake-era clients may rely on their advertised file roots, and an explicit value must lie
inside one of them or the call fails as `workspace_outside_roots`. The server falls back to its
own cwd only when an operator opted in, and discloses it on `meta.workspace_warning`.

The same workspace must be passed to a job's lifecycle calls, because job records are
workspace-keyed. On an active call the workspace selects where the backend works, not what it can
read.
