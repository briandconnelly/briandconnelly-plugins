#!/usr/bin/env bash
# voice-notify.sh — speak Claude Code Stop / Notification events via macOS `say`.
#
# Opt-in (stays silent otherwise):
#   • per-project : create a `.claude-voice` file in the project root
#   • per-session : launch with `CLAUDE_VOICE=1 claude`
#
# Optional tuning (env vars):
#   CLAUDE_VOICE_NAME   say voice            (default: Samantha)
#   CLAUDE_VOICE_RATE   words per minute     (default: 200)
#
# Requires: jq. macOS-only (silently inert elsewhere).
#
# Error routing: this hook never exits 2 and never writes to the agent channel.
# Expected-inert conditions exit 0 silently; genuine problems for an opted-in
# user print one line to stderr and still exit 0.

set -uo pipefail

# ---- macOS guard: silently inert when `say` is unavailable -----------------
command -v say >/dev/null 2>&1 || exit 0

payload="$(cat)"

# ---- opt-in gate -----------------------------------------------------------
# Opt in with exactly CLAUDE_VOICE=1 (so CLAUDE_VOICE=0 stays OFF) or a
# `.claude-voice` file in the project root.
proj="${CLAUDE_PROJECT_DIR:-$PWD}"
if [[ "${CLAUDE_VOICE:-}" != "1" && ! -e "$proj/.claude-voice" ]]; then
  exit 0
fi

# ---- jq guard: real misconfig for an opted-in user -> stderr, exit 0 -------
command -v jq >/dev/null 2>&1 || {
  echo "voice-notify: jq not found; install with 'brew install jq'" >&2
  exit 0
}

voice="${CLAUDE_VOICE_NAME:-Samantha}"
rate="${CLAUDE_VOICE_RATE:-200}"

event="$(printf '%s' "$payload" | jq -r '.hook_event_name // empty' 2>/dev/null)"

# ---- unparseable / missing event -> diagnostic, no speech ------------------
if [[ -z "$event" ]]; then
  echo "voice-notify: could not parse hook_event_name from payload" >&2
  exit 0
fi

# ---- session label: tmux session -> project dir name -> "Claude" -----------
session=""
if [[ -n "${TMUX_PANE:-}" ]] && command -v tmux >/dev/null 2>&1; then
  session="$(tmux display-message -p -t "$TMUX_PANE" '#S' 2>/dev/null || true)"
fi
[[ -z "$session" ]] && session="$(basename "$proj")"
[[ -z "$session" ]] && session="Claude"

# ---- build the spoken line -------------------------------------------------
case "$event" in
  Stop)
    msg="$(printf '%s' "$payload" | jq -r '.last_assistant_message // empty' 2>/dev/null)"
    # collapse to one line, take the first sentence, cap length
    summary="$(printf '%s' "$msg" | tr '\n' ' ' | sed -E 's/([.!?]).*/\1/' | cut -c1-160)"
    [[ -z "$summary" ]] && summary="finished."
    spoken="$session done. $summary"
    ;;
  Notification)
    # `// empty` + bash fallback so an empty-string message also falls back
    # (jq's `//` only substitutes for null/absent, not "").
    note="$(printf '%s' "$payload" | jq -r '.message // empty' 2>/dev/null)"
    [[ -z "$note" ]] && note="needs your input"
    spoken="$session $note"
    ;;
  *)
    exit 0
    ;;
esac

# ---- speak without blocking Claude ----------------------------------------
nohup say -v "$voice" -r "$rate" "$spoken" >/dev/null 2>&1 &
exit 0
