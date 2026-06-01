#!/usr/bin/env bash
# Self-contained tests for hooks/voice-notify.sh — no external framework.
# Isolates PATH to a bin dir of symlinks to the real tools the script needs,
# so individual cases can omit `say` or `jq` to simulate their absence.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$HERE/../hooks/voice-notify.sh"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

pass=0
fail=0

# Real tools the script may call (besides bash builtins), plus `bash` itself —
# cases run `PATH="$bin" bash "$SCRIPT"`, so `bash` must resolve under the
# isolated PATH. `say`/`jq` are added per-case so we can simulate their absence;
# `tmux` is intentionally omitted so the session label falls back to the
# project-dir basename.
REAL_TOOLS=(bash cat jq basename tr sed cut nohup)

# make_bin <dir> <tool...> — symlink the named real tools into an isolated dir.
make_bin() {
  local dir="$1"; shift
  rm -rf "$dir"; mkdir -p "$dir"
  local t path
  for t in "$@"; do
    path="$(command -v "$t" 2>/dev/null || true)"
    [[ -n "$path" ]] && ln -sf "$path" "$dir/$t"
  done
}

# install_say_stub <dir> <log> — a `say` that records each argv on its own line.
install_say_stub() {
  local dir="$1" log="$2"
  cat >"$dir/say" <<EOF
#!/bin/bash
printf '%s\n' "\$@" >>"$log"
EOF
  chmod +x "$dir/say"
}

# wait_for_file <path> — poll briefly; the script backgrounds say via nohup &.
wait_for_file() {
  local i=0
  while [[ ! -s "$1" && $i -lt 80 ]]; do sleep 0.05; i=$((i+1)); done
}

ok()   { echo "ok   - $1"; pass=$((pass+1)); }
bad()  { echo "FAIL - $1"; fail=$((fail+1)); }

# ---- Case 1: not opted in -> silent, no say -------------------------------
case_not_opted_in() {
  local name="not opted in -> silent"
  local bin="$WORK/bin1" log="$WORK/say1.log" proj="$WORK/proj1"
  mkdir -p "$proj"; make_bin "$bin" "${REAL_TOOLS[@]}"; install_say_stub "$bin" "$log"
  local out
  out="$(unset CLAUDE_VOICE; CLAUDE_PROJECT_DIR="$proj" PATH="$bin" \
    bash "$SCRIPT" <<<'{"hook_event_name":"Stop","last_assistant_message":"Hi."}' 2>&1)"
  sleep 0.2
  [[ ! -s "$log" && -z "$out" ]] && ok "$name" || bad "$name"
}

# ---- Case 2: opted in + Stop -> "<session> done. <summary>" ----------------
case_stop() {
  local name="Stop -> session done. summary"
  local bin="$WORK/bin2" log="$WORK/say2.log" proj="$WORK/voice-notify-test-proj"
  mkdir -p "$proj"; make_bin "$bin" "${REAL_TOOLS[@]}"; install_say_stub "$bin" "$log"
  CLAUDE_VOICE=1 CLAUDE_PROJECT_DIR="$proj" PATH="$bin" \
    bash "$SCRIPT" <<<'{"hook_event_name":"Stop","last_assistant_message":"Build complete. More details follow."}'
  wait_for_file "$log"
  grep -Fxq "voice-notify-test-proj done. Build complete." "$log" && ok "$name" || bad "$name"
}

# ---- Case 3: opted in + Notification -> "<session> <message>" --------------
case_notification() {
  local name="Notification -> session message"
  local bin="$WORK/bin3" log="$WORK/say3.log" proj="$WORK/voice-notify-test-proj"
  mkdir -p "$proj"; make_bin "$bin" "${REAL_TOOLS[@]}"; install_say_stub "$bin" "$log"
  CLAUDE_VOICE=1 CLAUDE_PROJECT_DIR="$proj" PATH="$bin" \
    bash "$SCRIPT" <<<'{"hook_event_name":"Notification","message":"needs your permission"}'
  wait_for_file "$log"
  grep -Fxq "voice-notify-test-proj needs your permission" "$log" && ok "$name" || bad "$name"
}

# ---- Case 4: voice/rate env vars passed through to say ---------------------
case_voice_rate() {
  local name="CLAUDE_VOICE_NAME / _RATE passed to say"
  local bin="$WORK/bin4" log="$WORK/say4.log" proj="$WORK/voice-notify-test-proj"
  mkdir -p "$proj"; make_bin "$bin" "${REAL_TOOLS[@]}"; install_say_stub "$bin" "$log"
  CLAUDE_VOICE=1 CLAUDE_VOICE_NAME=Alex CLAUDE_VOICE_RATE=180 \
    CLAUDE_PROJECT_DIR="$proj" PATH="$bin" \
    bash "$SCRIPT" <<<'{"hook_event_name":"Stop","last_assistant_message":"Done."}'
  wait_for_file "$log"
  grep -Fxq "Alex" "$log" && grep -Fxq "180" "$log" && ok "$name" || bad "$name"
}

# ---- Case 5: malformed JSON + opted in -> stderr note, no say --------------
case_malformed() {
  local name="malformed JSON -> stderr note, no say"
  local bin="$WORK/bin5" log="$WORK/say5.log" proj="$WORK/proj5"
  mkdir -p "$proj"; make_bin "$bin" "${REAL_TOOLS[@]}"; install_say_stub "$bin" "$log"
  local err
  err="$(CLAUDE_VOICE=1 CLAUDE_PROJECT_DIR="$proj" PATH="$bin" \
    bash "$SCRIPT" <<<'{not valid json' 2>&1 >/dev/null)"
  sleep 0.2
  [[ ! -s "$log" ]] && [[ "$err" == *"could not parse"* ]] && ok "$name" || bad "$name"
}

# ---- Case 6: jq absent + opted in -> stderr note, no say -------------------
case_no_jq() {
  local name="jq absent -> stderr note, no say"
  local bin="$WORK/bin6" log="$WORK/say6.log" proj="$WORK/proj6"
  mkdir -p "$proj"
  make_bin "$bin" bash cat basename tr sed cut nohup   # deliberately no jq
  install_say_stub "$bin" "$log"
  local err
  err="$(CLAUDE_VOICE=1 CLAUDE_PROJECT_DIR="$proj" PATH="$bin" \
    bash "$SCRIPT" <<<'{"hook_event_name":"Stop","last_assistant_message":"Hi."}' 2>&1 >/dev/null)"
  sleep 0.2
  [[ ! -s "$log" ]] && [[ "$err" == *"jq not found"* ]] && ok "$name" || bad "$name"
}

# ---- Case 7: say absent (non-macOS) -> silent, no stderr -------------------
case_no_say() {
  local name="say absent -> silent exit 0"
  local bin="$WORK/bin7" proj="$WORK/proj7"
  mkdir -p "$proj"
  make_bin "$bin" "${REAL_TOOLS[@]}"   # no say stub installed
  local out
  out="$(CLAUDE_VOICE=1 CLAUDE_PROJECT_DIR="$proj" PATH="$bin" \
    bash "$SCRIPT" <<<'{"hook_event_name":"Stop","last_assistant_message":"Hi."}' 2>&1)"
  [[ -z "$out" ]] && ok "$name" || bad "$name"
}

# ---- Case 8: plugin path containing a space -> still runs ------------------
case_spaced_path() {
  local name="spaced plugin path -> still speaks"
  local bin="$WORK/bin8" log="$WORK/say8.log" proj="$WORK/voice-notify-test-proj"
  local spaced="$WORK/with space/hooks"
  mkdir -p "$proj" "$spaced"; cp "$SCRIPT" "$spaced/voice-notify.sh"
  make_bin "$bin" "${REAL_TOOLS[@]}"; install_say_stub "$bin" "$log"
  CLAUDE_VOICE=1 CLAUDE_PROJECT_DIR="$proj" PATH="$bin" \
    bash "$spaced/voice-notify.sh" <<<'{"hook_event_name":"Stop","last_assistant_message":"Done."}'
  wait_for_file "$log"
  grep -Fxq "voice-notify-test-proj done. Done." "$log" && ok "$name" || bad "$name"
}

# ---- Case 9: CLAUDE_VOICE=0 stays OFF -> silent, no say --------------------
case_voice_zero() {
  local name="CLAUDE_VOICE=0 -> silent"
  local bin="$WORK/bin9" log="$WORK/say9.log" proj="$WORK/proj9"
  mkdir -p "$proj"; make_bin "$bin" "${REAL_TOOLS[@]}"; install_say_stub "$bin" "$log"
  local out
  out="$(CLAUDE_VOICE=0 CLAUDE_PROJECT_DIR="$proj" PATH="$bin" \
    bash "$SCRIPT" <<<'{"hook_event_name":"Stop","last_assistant_message":"Hi."}' 2>&1)"
  sleep 0.2
  [[ ! -s "$log" && -z "$out" ]] && ok "$name" || bad "$name"
}

# ---- Case 10: empty Notification message -> fallback text ------------------
case_empty_message() {
  local name="empty Notification message -> fallback"
  local bin="$WORK/bin10" log="$WORK/say10.log" proj="$WORK/voice-notify-test-proj"
  mkdir -p "$proj"; make_bin "$bin" "${REAL_TOOLS[@]}"; install_say_stub "$bin" "$log"
  CLAUDE_VOICE=1 CLAUDE_PROJECT_DIR="$proj" PATH="$bin" \
    bash "$SCRIPT" <<<'{"hook_event_name":"Notification","message":""}'
  wait_for_file "$log"
  grep -Fxq "voice-notify-test-proj needs your input" "$log" && ok "$name" || bad "$name"
}

# ---- Case 11: .claude-voice file opt-in (no env var) -> speaks -------------
case_file_optin() {
  local name=".claude-voice file opt-in -> speaks"
  local bin="$WORK/bin11" log="$WORK/say11.log" proj="$WORK/voice-notify-test-proj"
  mkdir -p "$proj"; touch "$proj/.claude-voice"
  make_bin "$bin" "${REAL_TOOLS[@]}"; install_say_stub "$bin" "$log"
  ( unset CLAUDE_VOICE; CLAUDE_PROJECT_DIR="$proj" PATH="$bin" \
    bash "$SCRIPT" <<<'{"hook_event_name":"Stop","last_assistant_message":"All set."}' )
  wait_for_file "$log"
  grep -Fxq "voice-notify-test-proj done. All set." "$log" && ok "$name" || bad "$name"
}

case_not_opted_in
case_stop
case_notification
case_voice_rate
case_malformed
case_no_jq
case_no_say
case_spaced_path
case_voice_zero
case_empty_message
case_file_optin

echo "-----"
echo "passed: $pass  failed: $fail"
[[ $fail -eq 0 ]]
