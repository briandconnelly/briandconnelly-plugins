# presence-detector

Skill that determines whether the user is **present** or **away** from a macOS machine by combining four independent signals, so no single false reading (e.g. watching a video with no input) flips the verdict on its own:

- screen lock state (a deliberate "I'm leaving")
- screensaver activity
- input idle time (seconds since last mouse/keyboard event)
- active Focus mode (counts as away only for modes you opt in)

macOS-only.

## Requirements

- macOS (uses Quartz via PyObjC for lock and idle detection).
- [`uv`](https://docs.astral.sh/uv/) — the script carries PEP 723 inline metadata, so `uv` provisions its one dependency in an ephemeral environment.

## Usage

Ask Claude things like "am I away from my machine?" or use the skill to presence-gate an automation.

The underlying script can also be run directly:

```bash
uv run skills/presence-detector/scripts/presence.py                    # JSON verdict
uv run skills/presence-detector/scripts/presence.py --idle 180         # >=180s idle counts as away (default 120)
uv run skills/presence-detector/scripts/presence.py --away-focus Sleep,Personal
```

Exit code is `0` if present, `1` if away, and stdout carries a JSON `{status, reason, signals}` verdict — usable directly in launchd / shell guards.

## Notes

- Focus state is read from the undocumented Do Not Disturb database — best-effort, and may break on a major macOS release; every failure path safely reports no Focus mode.
