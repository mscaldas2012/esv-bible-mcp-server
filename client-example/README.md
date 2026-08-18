# Client Example

A small Flask chat app for quickly exercising the ESV Bible MCP server (`../server/server.py`)
without wiring up a full MCP client. Type a passage reference (e.g. "Psalm 23") and it
returns the ESV text plus an audio player; if the input doesn't resolve to a reference it
falls back to a full-text search and offers clickable suggestions.

This is a demo/testing harness, not part of the released server — it isn't included in the
server's packaging and won't be tagged in GitHub Releases.

`agent.py` connects to `../server/server.py` as a real external MCP server over stdio
(spawned via `uv --directory ../server run server.py`), the same way any other MCP client
would, and drives it through the [Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk-python).
It rides your Claude.ai subscription login via the local `claude` CLI rather than spending
Developer Platform API credits — make sure `ANTHROPIC_API_KEY` is **not** set in the
environment this runs in, or it'll be used instead.

## Setup

From the repo root, install this app's extra dependencies (Flask, `claude-agent-sdk`) into
the server's project — the `client-example` dependency group defined in
`../server/pyproject.toml`:

```bash
uv sync --project server --group client-example
```

You'll also need the `claude` CLI installed and logged in, and `../server/.env` set up with
your `ESV_API_KEY` (see the [root README](../README.md)).

## Running

From the repo root:

```bash
uv run --project server --group client-example client-example/webapp.py
```

Then open http://localhost:5050.

## Running against the Docker image instead

By default `agent.py` spawns the server with `uv --directory ../server run server.py`. To
point it at a Docker image instead (e.g. to sanity-check a release build), set
`ESV_MCP_DOCKER_IMAGE` to the image tag before starting the app:

```bash
ESV_MCP_DOCKER_IMAGE=mscaldas/esv-bible-mcp-server \
  uv run --project server --group client-example client-example/webapp.py
```

or, for a local build:

```bash
ESV_MCP_DOCKER_IMAGE=esv-bible-mcp-server:local \
  uv run --project server --group client-example client-example/webapp.py
```

This still reads `../server/.env` for `ESV_API_KEY` (passed to the container via
`docker run --env-file`).
