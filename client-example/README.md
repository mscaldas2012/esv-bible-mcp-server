# Client Example

A small Flask chat app for quickly exercising the ESV Bible MCP server (`../server.py`)
without wiring up a full MCP client. Type a passage reference (e.g. "Psalm 23") and it
returns the ESV text plus an audio player; if the input doesn't resolve to a reference it
falls back to a full-text search and offers clickable suggestions.

This is a demo/testing harness, not part of the released server — it isn't included in the
server's packaging and won't be tagged in GitHub Releases.

`agent.py` connects to `../server.py` as a real external MCP server over stdio (spawned via
`uv --directory <repo root> run server.py`), the same way any other MCP client would, and
drives it through the [Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk-python).
It rides your Claude.ai subscription login via the local `claude` CLI rather than spending
Developer Platform API credits — make sure `ANTHROPIC_API_KEY` is **not** set in the
environment this runs in, or it'll be used instead.

## Setup

From the repo root, install this app's extra dependencies (Flask, `claude-agent-sdk`) on
top of the server's:

```bash
uv sync --group client-example
```

You'll also need the `claude` CLI installed and logged in, and `../.env` set up with your
`ESV_API_KEY` (see the [root README](../README.md)).

## Running

From the repo root:

```bash
uv run --group client-example client-example/webapp.py
```

Then open http://localhost:5050.
