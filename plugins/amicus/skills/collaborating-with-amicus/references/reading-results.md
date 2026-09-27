# Reading results

A result tells you three separable things: whether the call succeeded, what it covered, and what
the model claimed. Reading only the first is the common mistake — an `ok: true` result can cover
nothing at all.

## Rules

Branching on `ok`, branching on the concrete tool, reading the coverage fields, treating results
as unverified claims, and matching verification to the claim are governed by SKILL.md → Binding
rules → Results, which is their authoritative statement. This file explains what each of those
means; it adds three obligations of its own:

- **Read `questions` and `assumptions` before treating an answer as responsive.** An answer built
  on a wrong assumption is not a wrong answer to your question; it is an answer to a different
  one.
- **Run this project's full gate before calling implementation work complete**, whatever a
  returned result claims.
- **Check that a `meta` key is present before indexing it, unless the `amicus://result-meta`
  schema lists it as `required`.** `meta` is sparse on the wire: a null-valued key is dropped,
  and its absence means what null means.

`diffstat` is not an integrity check on `diff`; that rule lives in
[reviewing a returned diff](reviewing-a-returned-diff.md), and the reason is under Semantics
below.

## Three different facts about a job

| Field | Question it answers |
| --- | --- |
| `ok` (on the envelope) | Did *this MCP call* succeed? |
| `status` (on a job) | Is the job `running`, `done`, `failed`, `cancelled`, or `timeout`? |
| `result_available` | Is there a stored payload to fetch? |
| `result_ok` | Is that stored payload a success envelope, or a stored *error*? |

A job can be `done` with `result_ok: false`: the run completed and stored an error envelope. A
successful `amicus_job_result` call can therefore hand you a failed paid call. Branch on the
fetched envelope's own `ok`, not on the fetch having worked.

## Coverage: `ok: true` is not "it was reviewed"

`review_status` is `completed`, `not_run` or `unstructured`. **`not_run` means no reviewable changes were
gathered for the requested scope** — the review did not happen and no quota was spent, but the
envelope is still `ok: true` with a `summary`. Treating that as a clean review is the single
easiest way to report a passing review of nothing. When it happens, read the summary: it names
how many untracked files were detected and omitted, and the remedy.

**`unstructured` means the backend answered, but not with one JSON object amicus could read.**
The review ran and was paid for, and nothing was parsed from it: `verdict` and `confidence` are
`unknown`, `findings` and the prose lists are empty, and both diagnostics report every member
missing. The answer itself is kept, not discarded: it is `raw_response.text`, delivered at
`detail="full"` on the call and free from `amicus_job_result` for `meta.job_id` while the record
exists. Read it before deciding anything or paying for the same review again; a prose answer
often holds the review, and an identical retry can fail the same way. An answer that encloses
one object in a preamble, a fence or a sign-off is read as that object and stays `completed`;
one that holds two objects, or whose preamble has a `{` or sign-off a `}`, is `unstructured`
rather than guessed at. That reading is for reviews only: a consult answered in prose keeps its
whole answer in `summary`. Two cases are still errors, because in both amicus has no answer to
deliver: an empty one (`invalid_json`, or `empty_response` on `kimi`, which detects it before a
result is built) and one amicus refused to read (`answer_unavailable`).

`answer_unavailable` is a different fact from either: the backend **did** answer, and amicus
could not read the answer whole. `error.details.reason` says why. For an answer file amicus
refused, it is `artifact_oversize` (over the read limit, with `limit_bytes` and, when known,
`actual_bytes`), `artifact_not_regular` (a symlink, FIFO or device) or `artifact_unreadable`.
For an answer on the output stream (`claude`, and `kimi` when it wrote no answer file), it is
`stream_truncated`, the output passed `AMICUS_MAX_OUTPUT_BYTES` and the part carrying the answer
was dropped, or `stream_capture_failed`. Read `error.temporary` rather than assume: it is true
only for an unreadable file whose cause passes and for a failed stream capture, never on a keyed
call (the same key replays the error), and even then a retry is a new paid run. Otherwise never repeat the identical call: for oversize or a truncated
stream, narrow the task or ask for a shorter answer. A delegate whose summary file was refused, and which has no other whole answer
from the backend, still comes back `ok: true` when its diff was captured: with the diff,
amicus's own summary saying the backend's could not be read, a null `raw_response.text`, and a
`meta.security_warnings` entry. Where the backend's stream did carry its whole answer (`kimi`),
that answer is the summary and the raw response, beside the same warning. A `kimi` delegate whose
stream lost its summary also comes back `ok: true` with its diff and amicus's own summary saying
so, but with no warning, since nothing was refused. Read `meta.truncated` and `meta.redacted_paths` for whether that
diff is whole, as on any delegate.

When a review was not complete, the result's `coverage` object says so in fields you can branch
on: `coverage.status` is `complete` or `partial`, and `coverage.omission_reasons` names why, in a
fixed order from a fixed vocabulary. The reasons do not all mean that something was withheld:

| Reason | What it means |
| --- | --- |
| `untracked_omitted` | Untracked files in scope were not sent. `untracked_files_detected`, `untracked_files_included` and `untracked_files_omitted` count them; all three are null outside `scope="working_tree"`. |
| `tree_changed_during_gather` | The working tree changed while amicus read it, so the diff may not be one consistent snapshot. Detection is best-effort: its absence is not proof the tree held still. |
| `truncated` | The gathered diff hit the byte cap and was cut. |
| `redacted` | Secret redaction hid content. `coverage.redaction` separates files whose changes were withheld whole (`withheld_paths`) from files sent with values masked (`masked_paths`). It can be null beside `redacted` when the redaction fell only in content the byte cap cut; `meta.redacted_paths` names every redacted file either way. |
| `focused` | The call passed `focus`. Nothing was withheld, but the backend was not asked for a full review. |

`complete` means amicus detected none of these. It is not proof that nothing was missed —
`tree_changed_during_gather` cannot see a file that was already modified being edited again while
amicus read it — nor that the backend examined every line, and on a `not_run` result nothing was
reviewed at all, whatever `coverage` says.

`amicus_review_changes_dry_run` returns the `coverage` the paid review would report for the same
arguments, from the same gather and the same `focus`, so an omitted untracked file shows up
before you spend. It is a preview: the tree can change before you call.

**The degradation is one-directional, and this matters.** A model `pass` over partial coverage is
delivered as `verdict: unknown`, `confidence: low`, with the reasons also named in the summary. A
`concerns` or `fail` verdict is **passed through unchanged** — it is not degraded, because a
concrete finding stands on its own. So a `concerns` verdict over a truncated diff looks exactly
like a `concerns` verdict over a complete one. Read `coverage` yourself; the verdict will not
tell you.

## `findings_diagnostics`: what the backend said that amicus could not carry

Coverage is about how complete the review was: what amicus left out or narrowed before the backend
ran. This is the opposite axis: the backend ran over what it was given, and amicus could not relay
all of what it said. `findings_diagnostics` is `null` when nothing was
lost, and otherwise carries a `dropped` count and reasons from a fixed vocabulary:

| Reason | What it means |
| --- | --- |
| `severity_normalized` | A severity differed only in case or surrounding space. The finding is intact. |
| `extra_fields_omitted` | The backend added keys amicus's `Finding` has no home for. Every field amicus recognizes survives; whatever those extra keys said does not. |
| `backend_artifact_reference_removed` | The finding cited one of amicus's own temporary files (Kimi is handed its prompt as one). The path is replaced by `[amicus temporary file]` in the finding's text, and a `file` naming it is cleared together with its `line`. The finding is intact, and no location you could have opened was lost: treat it as a finding about what you sent, not about a file. |
| `invalid_entry` | An entry could not be represented at all and was dropped. Its content is not in the result. |
| `invalid_container` | The `findings` member was present but was not a list. Nothing could be read from it. |
| `missing_findings` | The `findings` member was absent. The output schema requires it, so this is not the backend saying "none". |

**`dropped` counts whole entries, and `dropped: null` is not `dropped: 0`.** `0` says no entry
was dropped — it is not a promise that nothing was lost, because `extra_fields_omitted` reports
content that went with keys amicus has no home for, and that is reported at `dropped: 0`, as is
`backend_artifact_reference_removed`. `null`
says amicus could not assess the list at all, so the count is unknowable. Never read `null` as
"none": read the reasons, not the count alone.

An `invalid_entry`, `invalid_container` or `missing_findings` also stops a `pass` from standing:
the verdict is
delivered as `unknown`/`low` with its own sentence in the summary, separate from any coverage
sentence. A `fail` or `concerns` keeps its verdict **and its confidence** — missing output does not
refute a negative the model did reach, so read `findings_diagnostics` on those yourself.

## `lists_diagnostics`: the prose lists amicus could not carry

The same disclosure for `questions`, `assumptions` and `next_steps` on consult, review and
adversarial results. The output schema asks for each as an array of strings. A number in one
is delivered as its string and reported; anything else amicus cannot carry is dropped and
counted, never guessed at. The field is
`null` when all three lists were carried intact. Otherwise it has one member per list, `null`
for a list that was clean and `{dropped, reasons}` for one that was not:

| Reason | What it means |
| --- | --- |
| `number_stringified` | A number was delivered as its string. Nothing was lost. |
| `invalid_entry` | An entry that was not a string or a number (an object, an array, a boolean, a null) was dropped whole. Its content is not in the result. |
| `invalid_container` | The member was present but was not a list. Nothing could be read from it. |
| `missing_member` | The member was absent. The output schema requires it, so this is not the backend saying "none". |

`dropped` counts whole entries, so it is `0` under `number_stringified` alone and `null` under
`invalid_container` or `missing_member`, where there was no list to count. **An empty list
beside a non-null member is a loss, not an answer:** `next_steps: []` with
`lists_diagnostics.next_steps: {dropped: 2, reasons: ["invalid_entry"]}` means the backend gave
two next steps amicus could not carry.

A consult answered in prose rather than the requested object parsed nothing: the answer is
`summary`, and `findings_diagnostics` reports `missing_findings` while every member here
reports `missing_member`. The empty lists on such a result are not the backend saying none.
A `review_status: unstructured` review is the same case as that prose consult, except that its
answer is `raw_response.text` rather than `summary`.
A `review_status: not_run` result is the other way round: no backend ran at all, so there is no
output to measure, both diagnostics are null, and `review_status` is the signal that says so.

Nothing here moves the verdict or the confidence. A finding is the review's correctness signal,
so losing one stops a `pass` from standing; a next step is advice, and no verdict is computed
from it. The diagnostic is the whole disclosure, which is why the rule says to read it before
treating an empty list as the backend's answer. Delegate results do not carry the field: their
`next_steps` is amicus's own literal, so there is no backend output to measure.

## `confidence`: whose rating it is

`confidence` answers how sure the review is, and two different parties can set it. Usually it is
the backend's own `low|medium|high`. amicus substitutes `low` in exactly two cases, partial
coverage and findings it could not carry, and only where it also withholds the verdict as
`unknown`. The two move together or not at all — so a `low` beside `verdict: unknown` may be
amicus's own, and a `low` beside any other verdict is the backend's word.

**A high confidence is not evidence that coverage was complete or findings intact.** A `fail` or
`concerns` verdict keeps the backend's rating whatever was lost, by design: missing output does
not refute a negative the model did reach. So `fail`/`high` is exactly what a truncated diff with
a dropped finding looks like. Read the coverage fields and `findings_diagnostics` yourself.

`unknown` is the fourth value and means something else entirely: no rating exists. Either no
backend ran (`review_status: not_run`, where there is nothing to rate), or the backend supplied
nothing amicus could read there and no such lowering applied.
**It is the absence of a rating, not a low one.** Reading it as low inverts it — amicus declined
to invent a rating precisely so that you would not infer one. The verdict beside it is untouched:
a backend that reported `fail` and said nothing readable about its certainty is delivered as
`fail`/`unknown`, neither softened nor promoted.

The dropped content is not echoed back in any form. For a job whose record still exists, a
`detail="full"` read returns `raw_response.text`, which is where the backend's own words survive.

## Meta fields that change a result's meaning

`meta` is sparse on the wire, on every tool: a key with a null value is dropped, and its absence
means exactly what null means (not applicable, not reported). The keys the `amicus://result-meta`
schema lists as `required` are always present. An empty list among them (`security_warnings`,
`compat_warnings`, `redacted_paths`) means that envelope reports none, not that a check ran: a
job handle, status or list, and a dry run, report on the call that produced them, never on the
run they name, and the run's warnings arrive on its own result.

- `meta.truncated` / `meta.truncation_hint` — the payload was cut to a byte cap. On a delegate
  result the hint names the environment variable that raises the cap.
- `meta.redacted_paths` — secret-scrubbing altered content on these paths. The model may have
  reviewed a masked version of the code you think it reviewed.
- `meta.security_warnings` — a backend-specific hazard was detected. On `claude`, this is where
  workspace hooks are reported: under `inherit` and `scoped` config modes hooks run **outside**
  the tool allowlist, so a "read-only" Claude run can still execute them.
- `meta.compat_warnings` — the backend CLI drifted from the version amicus was verified against.
  Findings are still returned; their reliability is not vouched for.
- `meta.context_summary` — files changed and lines added/removed for the gathered input.

## Semantics: why `diffstat` is not a checksum

On a delegate result, `diffstat` and `meta.context_summary` are computed from the **raw** diff the
backend produced. Redaction and truncation are applied afterwards, to the `diff` field only. So a
`diffstat` reporting more files or lines than the `diff` shows is the expected result of
redaction or a byte cap — not evidence the model contradicted itself. Read `meta.redacted_paths`
and `meta.truncated` to tell the two apart. If neither is set and the counts still disagree, then
the inconsistency is in the diff itself and is worth investigating.

## Verification: match the check to the claim

"Verify it" is not one action, and running the full project gate is not always the relevant one.
A gate that could not have failed for this change is not evidence.

| Claim | What verifying it means |
| --- | --- |
| A design critique, an architectural objection, a missing requirement | Inspect the code or document the claim is about. A test run neither confirms nor refutes it. |
| A specific bug, with a file and line | Read that location. Reproduce it if a cheap targeted reproduction exists. |
| A proposed diff | Apply it to a disposable worktree and run the checks the change actually touches — see [reviewing a returned diff](reviewing-a-returned-diff.md). |
| Implementation work you are about to call complete | This project's full gate, per `AGENTS.md`. Nothing less closes out implementation. |

Verifying a claim in the working tree and applying a change to the working tree are different
acts. Do the first freely; the second is governed by the delegate rules.
