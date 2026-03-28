# Conventions

- When writing markdown, use one sentence per line for easy diffs
- Use kebab-case for directory and file names
- Keep plugins sorted alphabetically in `.claude-plugin/marketplace.json` and the
  `README.md` table
- Do not add `version` or `keywords` to plugin entries in `marketplace.json`---these
  fields should be tracked in each plugin's `plugin.json`.
- Commit messages follow [Conventional
  Commits](https://www.conventionalcommits.org/en/v1.0.0/) with the plugin name as scope
  (e.g., 'feature(orbnet): add cache TTL setting')
- A skill's frontmatter `name` must match its directory name within `skills/`
