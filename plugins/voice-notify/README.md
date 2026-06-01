# voice-notify

Speak Claude Code `Stop` and `Notification` events aloud through macOS `say`, so
you can step away during long-running work and hear when Claude finishes or needs
input.

macOS-only.
Silent until you opt in.

## Requirements

- macOS (uses the built-in `say` command).
- [`jq`](https://jqlang.github.io/jq/) — install with `brew install jq`.

## Opt in

The plugin stays silent until you enable it, one of two ways:

- **Per project:** create an empty `.claude-voice` file in the project root.
- **Per session:** start Claude with `CLAUDE_VOICE=1 claude`.

## Tuning

| Variable | Default | Meaning |
| --- | --- | --- |
| `CLAUDE_VOICE_NAME` | `Samantha` | Any installed `say` voice (see `say -v '?'`). |
| `CLAUDE_VOICE_RATE` | `200` | Speech rate in words per minute. |

## What it says

- **Stop:** `"<session> done. <first sentence of Claude's last message>"`
- **Notification:** `"<session> <notification message>"`

The `<session>` label is your tmux session name if present, otherwise the project
directory's name.

## Behavior notes

It never blocks Claude (speech is launched detached) and never reports errors back
to the agent. If you have opted in but `jq` is missing or the hook payload cannot
be parsed, it prints a one-line note to stderr and exits cleanly.

## Tests

```bash
bash tests/run-tests.sh
```
