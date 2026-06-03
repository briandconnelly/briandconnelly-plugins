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
| [cc-plugin-codex](plugins/cc-plugin-codex/) | Call Claude Code from Codex for bounded, independent code review and second opinions |
| [cwms](plugins/cwms/) | MCP server for querying U.S. Army Corps of Engineers water data via the CWMS Data API |
| [ipinfo](plugins/ipinfo/) | MCP server for getting IP address details, location, and network information via ipinfo.io |
| [orb-cloud](plugins/orb-cloud/) | MCP server for managing Orb Cloud organizations and devices |
| [orbnet](plugins/orbnet/) | MCP server for monitoring internet quality via Orb Local API |
| [tempest](plugins/tempest/) | MCP server for accessing WeatherFlow Tempest personal weather station data |
| [voice-notify](plugins/voice-notify/) | Speak Claude Code Stop and Notification events aloud via macOS say |
