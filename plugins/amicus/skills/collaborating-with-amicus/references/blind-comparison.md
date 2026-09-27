# Blind comparison

Use this pattern when two or more finalized candidate artifacts exist, nothing cheaper can tell
them apart, and you want a backend that produced none of them to compare them. The comparison is
a critique with a preference attached. It is not a verdict, not a score, and not a vote: the
backend reads the candidates as text, and you own the decision.

The case this pattern exists for is the one [independent attempt](independent-attempt.md) leaves
open: when you wrote one of the candidates, your own synthesis is conflicted, and a reader with no
stake in either is the instrument that addresses that.

## Rules

Cap declaration, call counting and synthesis are governed by SKILL.md → Binding rules → Spend and
Composed workflows; the brief and the workspace-content prohibition on `instructions_append` by
[active workflows](active-workflows.md) → Rules; backend identity claims by
[choosing a backend](choosing-a-backend.md) → Rules. One inherited rule is overridden here: the
belief-disclosure rule in active workflows does not apply to the call fields of a blind
comparison, as the blinding rule below states. These are this file's own:

- **Compare only finalized candidates, and only when no cheaper discriminator exists.** Name the
  discriminators you considered — a test, this project's gate, a direct inspection — and why each
  cannot settle the question, before the call.
- **Exclude every backend that produced a candidate, and, when you produced one, the backend that
  runs the model you are running as, where the host tells you which that is.** A backend that
  wrote a candidate is its author whatever the label says.
- **Record whether the comparison backend's model family is verified distinct from each
  candidate's author, and say `unverified` when it is not.** Backend IDs do not establish
  families.
- **Blind the candidates in every field of the call.** Neutral labels, no authorship, no
  provenance, no marker of which one you wrote or prefer. This overrides the belief-disclosure
  rule for the call fields only: record any prior preference of your own before the call, outside
  every call field, and report it afterwards.
- **Disclose in the report which candidate is yours, if any, and every marker you could not
  remove.** The backend was not told; the reader of your report is.
- **Fix the criteria before the call and carry them in `question`; carry the candidates in
  `extra_context`.** A criterion added after reading the result is a new comparison, not a
  reading of this one.
- **Route through `amicus_consult` or `amicus_consult_async` only.** Review and adversarial
  calls accept `extra_context` too, but their results carry a `verdict` and `confidence` that
  would stand beside the preference as a rating, and the adversarial critic is instructed to
  attack, not to compare.
- **Omit `instructions_append`, or fix neutral output-shape guidance before the first call and
  hold it byte-identical across both calls.**
- **Ask for a per-criterion comparison with reasons, one stated preference, and the inspection
  or experiment that would settle the largest disagreement.** Never ask for, and never report, a
  score, a grade, a rank order beyond the preference, or a probability.
- **Treat the preference as a finding.** Verify each reason it rests on against the candidates
  before it moves the decision; a reason you cannot trace to the candidates carries no weight,
  whatever the preference says.
- **Disclose the presentation order with the result.** Report the comparison as
  position-controlled only for exactly two candidates and only after the order-swapped second
  call below; with three or more candidates, report stability under the two observed orders and
  nothing more, because one reversal leaves the middle positions where they were.
- **Take the second call only under a two-call cap declared before the first, on the same
  `backend`, `model`, `reasoning_effort`, `backend_options` and `workspace_root`, with the
  brief byte-identical except for candidate order.** Stop after it, whatever it says.
- **Report the second call as observed preference stability or instability under order, with
  both sets of reasons preserved.** Never infer from it that the criteria failed, or that the
  stable preference is correct.
- **Never run this pattern as a vote across backends, as a substitute for a test that can
  discriminate, or in a loop with regeneration.**
- **Never send a candidate you would not hand to the comparison backend's provider with its
  author's name on it.** Blinding changes the label, not the exposure.

## Order of work

Each step names the rule that governs it; the rules are the authoritative statement.

1. Declare the pattern and the cap (SKILL.md → Spend). One call is the default; two only when
   position control matters enough to pay for it, decided now.
2. Confirm every candidate is finalized and name the cheaper discriminators and why none applies
   (first rule). If one does, run it instead and stop.
3. Choose the comparison backend from `amicus_backends`, applying the exclusion rule, and record
   the family-diversity status.
4. Record your own prior preference, if any, outside every call field (blinding rule).
5. Write the brief: the decision the comparison informs, the criteria, the deliverable, and the
   presentation order you will use (criteria and deliverable rules).
6. Blind the candidates: neutral labels, authorship markers stripped where that does not change
   substance, the remainder noted for the report (blinding and disclosure rules).
7. Call the consult verb with the candidates in `extra_context` (routing rule), async when the
   candidates are large ([sync vs async](sync-vs-async.md)).
8. Read the result under SKILL.md → Binding rules → Results, including `findings_diagnostics`
   and `lists_diagnostics`.
9. Verify the load-bearing reasons against the candidates, decide, and report with the order,
   the diversity status, your own authorship, and any disagreement preserved (finding and
   disclosure rules).

## Why a preference and not a score

A comparison backend is subject to the biases a reader of two texts has: it tends to prefer the
candidate it saw first or last, the longer one, and the one written in its own style. A number
hides which of those moved it. A per-criterion reason can be traced to the candidates and checked;
a score cannot. `amicus_consult` also returns no `verdict` or `confidence` field, so a score would
arrive as prose amicus does not parse, and the Results rules give it no standing that the reasons
lack.

The same biases are why the pattern is blind. Blinding removes the backend's pointer to
authorship; it does not remove yours, which is why the disclosure rule puts your authorship in the
report the backend never sees. The belief-disclosure rule that governs an ordinary brief exists so
a second opinion is not asked to endorse a conclusion; here the same end is served by withholding
the conclusion from the call altogether, and the prior preference is kept where it can be compared
with the result rather than where it can shape it.

## What the backend does with the candidates

The deliverable is a reading of the candidates as text. Nothing in this pattern bounds what the
backend executes while it reads: `codex` runs a consult under a read-only sandbox, which bounds
writes rather than execution, and `claude` defaults to no tools at all
([choosing a backend](choosing-a-backend.md)). Whether anything ran is a fact of the result's own
account of what it did, as [review–revise](review-revise.md) → What a critique is not explains,
and not something this pattern can promise either way.

## The order-swapped second call

Position bias is the one bias a second call can measure, and only for two candidates, since
reversing three or more moves only the ends; and only when everything else is held fixed: the same backend, resolved model, options and workspace, the same criteria, the same labels
swapped with their content, the same `instructions_append` if any. `meta.instructions_append`
carries a `{sha256, bytes}` fingerprint that checks that last half, as
[review–revise](review-revise.md) describes; the rest of the brief is your own bookkeeping.

Two calls cannot separate order sensitivity from ordinary run-to-run variation, which is why the
rule limits what the pair may be said to show. The same preference under both orders is weak
evidence that the preference is stable under order; opposite preferences show that it is not, and
leave the decision resting on your verification of the two sets of reasons. Neither outcome buys
a third call.

## What this pattern is not

- **Not a vote.** Three backends preferring the same candidate are three claims from three
  readers of the same brief, each subject to the same biases. The root rule against tallying
  votes applies.
- **Not a test.** For an implementation that a test, the gate, or a reproduction can
  discriminate, the discriminator is the instrument and this pattern is the wrong spend.
  [Independent attempt](independent-attempt.md) → Independent test design is the route for
  obtaining such a test without showing the backend the implementations.
- **Not a loop.** Generate, compare, regenerate is the open-ended conversation
  [review–revise](review-revise.md) forbids. The cap ends the sequence.
- **Not independence.** The comparison backend sees every candidate. Nothing here establishes
  that the candidates were produced independently of each other; that is
  [independent attempt](independent-attempt.md)'s subject, and its reclassification triggers
  are unaffected by a comparison made afterwards.

## Data exposure

Every candidate reaches the comparison backend's provider raw, in `extra_context`, which
redaction does not cover. That is why the last rule above exists: blinding is a change to what
you send, not a protection applied to it, and stripping an author's name removes nothing from
the text that follows it.
