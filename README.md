# briandconnelly's Plugin Marketplaces

Skills, MCP servers, and more as Claude Code and Codex plugins.

## Setup

Add this marketplace to Claude Code:

```
/plugin marketplace add briandconnelly/briandconnelly-plugins
```

Add this marketplace to Codex:

```
codex plugin marketplace add briandconnelly/briandconnelly-plugins
```

## Available Plugins

In Claude Code, install a plugin with `/plugin install <plugin>@briandconnelly-plugins`.
In Codex, install plugins from the `briandconnelly-plugins` marketplace after adding it.

| **Plugin** | **Description** |
| --- | --- |
| [amicus](https://github.com/briandconnelly/amicus) | One MCP server for every second-opinion model: consult, review, and delegate with the backend as a parameter |
| [cwms](plugins/cwms/) | MCP server for querying U.S. Army Corps of Engineers water data via the CWMS Data API |
| [data-reasoning](https://github.com/briandconnelly/data-reasoning) | Skills for reasoning from data: exploratory analysis, hypothesis-driven investigation, causal identification review, and decision analysis |
| [ipinfo](plugins/ipinfo/) | MCP server for getting IP address details, location, and network information via ipinfo.io |
| [mcp-error-audit](plugins/mcp-error-audit/) | Slash command that audits MCP tool errors across your Claude Code sessions and prioritizes fixes |
| [orb-cloud](plugins/orb-cloud/) | MCP server for managing Orb Cloud organizations and devices |
| [orbnet](plugins/orbnet/) | MCP server for monitoring internet quality via Orb Local API |
| [presence-detector](plugins/presence-detector/) | Skill for detecting whether the user is present at or away from their macOS machine |
| [tempest](https://github.com/briandconnelly/mcp-server-tempest) | MCP server for accessing WeatherFlow Tempest personal weather station data |
| [voice-notify](plugins/voice-notify/) | Speak Claude Code Stop and Notification events aloud via macOS say |
