# Independent attempt

Use this pattern to obtain your own attempt and one backend's attempt at the same problem, then
synthesize them. You are the host: you set the task, you verify, and you own the decision.

Independence must be observable in the transcript, not asserted. Neither attempt may be visible to
the other before that other attempt is finalized.

## Rules

- **Declare the pattern and its cap before the first paid call.** One worker attempt is one paid
  call.
- **Finalize your own attempt before any call that can return the backend's answer.**
- **Keep your draft out of the resolved workspace and out of every path named to any call for this
  job**; hold it in context entirely when it is small enough.
- **Omit `instructions_append`, or restrict it to neutral output-shape guidance fixed before
  either attempt begins.**
- **Check the returned output for distinctive content of your draft before comparing.** Matching
  content is evidence of possible contact, not proof of its absence when nothing matches.
- **Reclassify the operation as ordinary critique** — and stop claiming independence — when any
  reclassification trigger below holds.
- **Never alter git state — stash, commit, branch switch, new worktree — solely to manufacture
  independence.** It requires explicit user authorization plus a check that their state stays
  recoverable.
- **Disclose the basis** when the draft was on disk while the job ran.
- **Count paid calls across the whole workflow, not per backend.**
- **Never spend a further call to resolve a disagreement you can resolve from evidence.**

Synthesis itself is governed by SKILL.md → Binding rules → Composed workflows.

## Order of work

1. Declare the pattern and the cap.
2. Establish the neutral task, the shared facts, the acceptance criteria, and the workspace the
   backend may inspect. This framing reaches both attempts, so write it before either begins.
3. Start the backend's attempt with the matching `_async` tool — `amicus_consult_async` for a
   design or answer, `amicus_delegate_async` for an implementation. The start is the paid call;
   do not poll for the result yet.
4. Produce and finalize your own attempt in full, before any call that can return the backend's.
5. Only then poll and fetch, per [sync vs async](sync-vs-async.md).
6. Check the returned output for distinctive content of your draft before comparing. This is the
   one channel that can *prove* contact after the fact, and it is free.
7. Compare assumptions, evidence, trade-offs, and failure modes. Verify the load-bearing
   differences against the code or a test, and synthesize one decision.

If only the sync tool is available, finalize your attempt before making the call. The reverse
order cannot be repaired by intent: once the backend's answer is in context, everything drafted
afterwards is conditioned on it, and "I did not condition on it" is neither enforceable nor
observable.

## What placement does and does not buy

`amicus_delegate` seeds its worktree from `HEAD` plus replayable uncommitted **tracked** changes;
untracked files are never copied. So an untracked draft is not carried into a delegate worktree.

That is not a read boundary. A consult or review can read files at absolute paths outside its
resolved workspace, up to what the OS user running the backend CLI can read. Placing the draft
elsewhere removes the backend's *pointer* to it; it does not put it out of reach.

That distinction sets what you may claim. "The backend did not see the draft" is not available —
no result carries a read audit. The claim a transcript can support is that the backend had no
pointer to the draft and its output shows no sign of it.

`instructions_append` is part of the shared framing, and a stance leaks more than a question does:
"focus on whether the lock-free approach holds" tells the backend which approach you took, placed
ahead of the task itself. Omit it here, or fix neutral output-shape guidance before either attempt
starts.

## Reclassification triggers

Classify the operation as critique — do not claim independence — when any of these holds:

- the draft was supplied to the backend, or named to it;
- an `instructions_append` stance hinted at your approach;
- the draft was persisted, at any time before the job finished, inside the resolved workspace, the
  seeded baseline, or a path passed to any call for this job;
- the backend's answer entered context before your attempt was finalized;
- the returned output contains distinctive content of your draft.

Judge these from your own tool calls and the returned output; they are the only observable
evidence.

## Two workers

Running two backends against one problem is an **extension of this pattern, not a free variation
of it**. The one-call allowance here covers one worker. Two workers needs its own declared budget,
its own job tracking, and separation held for each — and remember that two backend IDs do not
establish two model families ([choosing a backend](choosing-a-backend.md)), so the diversity the
pattern trades on may be unverified.

## Independent test design

A useful variant: give the backend the requirements and acceptance criteria and ask for edge
cases, invariants, and the tests that would distinguish a correct implementation from a plausible
wrong one — **without** showing it the implementation. Then write or review the implementation
yourself and run the tests locally.

The point is that implementation and tests written together tend to share blind spots. Withholding
the implementation is what makes the exercise worth the call; supplying it turns this back into an
ordinary review.

## Synthesis

Agreement is weak evidence: both attempts may inherit the same framing from the task you wrote,
and may share blind spots regardless. Spend the synthesis on disagreements, differing assumptions,
and the experiment that would distinguish them.

What you report preserves disagreement and missing evidence, per the root's synthesis rule.
