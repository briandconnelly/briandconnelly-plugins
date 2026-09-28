# Conventions

## Writing

- In markdown, keep each sentence on its own line, and never wrap a sentence across lines.
- Use kebab-case for directory and file names.
- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/), with the plugin name as scope for plugin-specific changes (e.g., `feat(orbnet): add cache TTL setting`).

## Marketplaces

- Claude Code plugins are listed in `.claude-plugin/marketplace.json`, and Codex plugins in `.agents/plugins/marketplace.json`.
- List a plugin only in the marketplaces of the platforms it supports.
- Keep plugins sorted alphabetically by name in both marketplaces and in the `README.md` table.
- Do not add `version` or `keywords` to marketplace entries; they belong in the plugin's own manifests, which live upstream for URL-sourced plugins.
- Use lowercase categories in the Claude marketplace (`utilities`) and title case in the Codex marketplace (`Utilities`).
- A plugin that lives in its own repository uses a `"source": "url"` entry, which should be pinned with `ref` (a tag, or a release branch) and `sha` unless there is a reason not to.
- When a URL-sourced plugin is listed in both marketplaces, keep its `url`, `ref`, and `sha` identical in both.

## Local plugins

- Local plugins live in `plugins/<name>/`, and the paths below are relative to that directory.
- Claude Code manifests live in `.claude-plugin/plugin.json`, with MCP servers defined inline under `mcpServers`.
- Codex manifests live in `.codex-plugin/plugin.json`, with MCP servers defined in `.mcp.json`.
- A skill's frontmatter `name` must match its directory name within `skills/`.
- `hooks/hooks.json` loads automatically, so do not add a `hooks` field to `plugin.json` that points to it.

## Verification

- Run `prek run --all-files` and `claude plugin validate .` before committing.
- Run the tests of any plugin you change, as described in its `README.md`.
- Do not modify files in `.github/workflows/` unless the task is to change a workflow, because the Claude Code Action requires them to match the default branch byte for byte.
