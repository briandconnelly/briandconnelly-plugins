---
name: hermes-tweet
description: >-
  Use when the user asks to install, configure, test, or operate Hermes Tweet, the native Hermes Agent X/Twitter plugin for Xquik read workflows and approval-gated actions.
user-invocable: true
argument-hint: "[Hermes Tweet task or X/Twitter workflow]"
---

You help users install, configure, test, and operate Hermes Tweet.
Hermes Tweet is a native Hermes Agent plugin for X/Twitter work through Xquik.
Do not treat it as a generic API wrapper or direct HTTP fallback.

## Source Truth
- Hermes Tweet README: `https://github.com/Xquik-dev/hermes-tweet#readme`
- PyPI package: `https://pypi.org/project/hermes-tweet/`
- Hermes plugin guide: `https://github.com/NousResearch/hermes-agent/blob/main/website/docs/guides/build-a-hermes-plugin.md`
- Hermes plugins guide: `https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/plugins.md`

## Workflow
1. Confirm Hermes Tweet is installed and enabled in the Hermes runtime.
2. Use `tweet_explore` first when the user asks for a capability or route.
3. Use `tweet_read` only for catalog-listed read-only endpoints.
4. Use `tweet_action` only for write-capable or private workflows after explicit user approval.
5. Keep secrets in the Hermes runtime environment.

## Decision Rules
- If the user asks what Hermes Tweet can do, use `tweet_explore`.
- If the endpoint is read-only, use `tweet_read`.
- If the endpoint writes or changes account state, use `tweet_action` only after approval.
- If `tweet_read` is unavailable, ask the user to configure `XQUIK_API_KEY` in the Hermes runtime environment.
- If `tweet_action` is unavailable, explain that actions require `HERMES_TWEET_ENABLE_ACTIONS=true`.
- If Hermes lists the plugin as `not enabled`, tell the user to run `hermes plugins enable hermes-tweet` or reinstall with `--enable`.
- If the user uses Hermes Desktop with a remote gateway profile, install and configure Hermes Tweet on the remote Hermes host where plugin code executes.

## Guardrails
- Never ask for API keys, passwords, cookies, signing keys, or TOTP secrets.
- Never pass credentials in tool arguments.
- Never invent endpoints, fields, pricing, limits, or capabilities.
- Do not create direct HTTP fallbacks.
- Use only catalog-listed `/api/v1/...` paths.
- Summarize side effects before any account-changing action.

## Install Checks
Use these checks when the user asks for runtime troubleshooting:

```bash
hermes plugins install Xquik-dev/hermes-tweet --enable
hermes plugins list
hermes tools list
```

For PyPI installs:

```bash
uv pip install --python ~/.hermes/hermes-agent/venv/bin/python hermes-tweet
hermes plugins enable hermes-tweet
```

Expected behavior:
- `tweet_explore` can appear without `XQUIK_API_KEY`.
- `tweet_read` requires `XQUIK_API_KEY`.
- `tweet_action` requires `HERMES_TWEET_ENABLE_ACTIONS=true`.
- Third-party Hermes plugins must be enabled before tools run.
