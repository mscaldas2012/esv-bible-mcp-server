# ESV Bible MCP Server

An MCP server that wraps the [ESV Bible API](https://api.esv.org/docs/), exposing three tools:

- `search_passages(query, page, page_size)` — full-text search of the ESV Bible.
- `get_passage_text(reference, ...)` — retrieve plain-text Scripture for a reference (e.g. `"John 3:16"`, `"Genesis 1-3"`).
- `get_passage_audio_url(reference)` — resolve a direct MP3 URL for spoken-word audio of a passage.

The server itself lives in [`server/`](server/) (`server.py`, `esv_client.py`, `canon.py`) —
that's what GitHub Releases track. A small Flask chat app that exercises the server
end-to-end lives separately in [`client-example/`](client-example/); it's a demo/testing
harness, not part of the server.

## Setup

1. Get a free API key at https://api.esv.org/ (requires a registered application).
2. Copy `server/.env.example` to `server/.env` and fill in your key:

   ```bash
   cp server/.env.example server/.env
   # then edit server/.env and set ESV_API_KEY=your-key-here
   ```

   The server loads `.env` itself at startup (via `python-dotenv`), so this works no matter
   how it's launched — directly, through the MCP Inspector, or via a client's `.mcp.json`
   — without depending on each launcher's environment-forwarding behavior. `.env` is
   git-ignored; no key is stored in the repo.

3. To use this server from an MCP client (Claude Code, Claude Desktop, etc.), point its
   `.mcp.json`/config at the `server/` directory, e.g.:

   ```json
   {
     "mcpServers": {
       "esv-bible": {
         "command": "uv",
         "args": ["--directory", "/path/to/esv-bible-mcp-server/server", "run", "server.py"]
       }
     }
   }
   ```

## Running standalone

```bash
cd server
uv sync
uv run server.py
```

## Running with the MCP Inspector

```bash
cd server
uv run mcp dev server.py
```

## Running via Docker

A prebuilt image is published to Docker Hub as
[`mscaldas/esv-bible-mcp-server`](https://hub.docker.com/r/mscaldas/esv-bible-mcp-server) on
every GitHub Release (see [`.github/workflows/docker-release.yml`](.github/workflows/docker-release.yml)):

```bash
docker run --rm -i -e ESV_API_KEY=your-key-here mscaldas/esv-bible-mcp-server
```

The server communicates over stdio, so an MCP client would run it the same way, e.g.:

```json
{
  "mcpServers": {
    "esv-bible": {
      "command": "docker",
      "args": ["run", "--rm", "-i", "-e", "ESV_API_KEY", "mscaldas/esv-bible-mcp-server"],
      "env": { "ESV_API_KEY": "your-key-here" }
    }
  }
}
```

## Trying it out: the client example

See [`client-example/README.md`](client-example/README.md) for a Flask chat UI that drives
this server through the Claude Agent SDK — useful for quickly poking at the tools without
wiring up a full MCP client.

## Notes

- The ESV API's audio endpoint returns an HTTP redirect to an MP3 file rather than the
  audio bytes themselves; `get_passage_audio_url` follows that redirect one hop and
  returns the resulting URL instead of downloading the file.
- Per ESV API terms, avoid requesting more than 500 verses of text or displaying passages
  without the "(ESV)" attribution (`include_short_copyright` is on by default via the API).
