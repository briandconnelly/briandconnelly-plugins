# Choosing a backend

`backend` is required on every paid amicus tool and has no default — pick it deliberately for each
call. Call `amicus_backends` first (free) and pick only among backends it reports `enabled: true`
and `status.authenticated: true`. `AMICUS_BACKENDS` decides which backends exist in this
deployment; it does not decide which one to prefer.

## What `amicus_backends` reports

For each backend: `enabled` (turned on for this deployment), `available` (the plugin actually
loaded), `status.installed`, `status.authenticated`, `status.warnings`, `features` (the verbs it
supports), `effects` (`paid_calls_destructive`, `job_reads_read_only`), `options` (backend-specific
knobs and their allowed values), and `egress`/`carriers` (how prompt inputs travel to that
backend). A backend can be `enabled` but not `available`, or `available` but not `authenticated` —
check `status`, not just `enabled`, before routing a call to it.

## Feature gates in v1

- `amicus_consult` / `amicus_review_changes`: `codex`, `kimi`, `claude`.
- `amicus_delegate`: `codex`, `kimi` only. Claude stays review-only — do not route a delegate call
  to `claude`; it will fail the feature gate.
- `amicus_adversarial_review`: `claude` only.

Treat `amicus_backends`' per-entry `features` list as authoritative over this summary if they ever
disagree — the running deployment is ground truth.

## Judgment call: which backend for a consult or review

Beyond the feature gate, amicus does not rank backends — the choice among feature-eligible,
ready backends is a judgment call, not a rule this skill can make for you. Weigh what you actually
know about the situation:

- **Diversity of opinion.** If the goal is to catch what the working model (you) might be blind
  to, a genuinely different model family is worth more than the "best" model on paper.
  Consulting the same family you're already running narrows that value.
- **Task shape.** A broad architectural question benefits from a model with strong general
  reasoning; a narrow, mechanical review benefits from speed and low cost more than depth.
  `amicus_models` lists each backend's advertised models and reasoning-effort sets if you need to
  choose among a backend's own models.
- **What's actually usable right now.** A backend that is enabled but unauthenticated, rate
  limited, or reporting warnings in `status.warnings` is not a good choice regardless of how well
  suited it looks on paper — read `amicus_backends` fresh rather than reusing a stale assumption
  from earlier in the session.
- **Effects.** If a call's annotations matter to you (e.g., you want to avoid a write-capable
  backend's approval friction on an unrelated call), remember that annotations follow the worst
  *enabled* backend, not the one you pick per call — see SKILL.md → Annotations follow the worst
  enabled backend. Picking a different `backend` for this call does not change that.

When none of these considerations distinguish the eligible backends, picking any ready, eligible
backend is fine — do not spend an extra call trying to break a tie that doesn't matter to the
outcome.
