# ESV Bible MCP Server

An MCP server that wraps the [ESV Bible API](https://api.esv.org/docs/), exposing three tools:

- `search_passages(query, page, page_size)` — full-text search of the ESV Bible.
- `get_passage_text(reference, ...)` — retrieve plain-text Scripture for a reference (e.g. `"John 3:16"`, `"Genesis 1-3"`).
- `get_passage_audio_url(reference)` — resolve a direct MP3 URL for spoken-word audio of a passage.

Also includes `webapp.py`, a small Flask chat UI that sits on top of the same ESV client
code (`esv_client.py`) — type a passage reference, get the text back plus an audio player
you can hit play on whenever you want.

## Setup

1. Get a free API key at https://api.esv.org/ (requires a registered application).
2. Copy `.env.example` to `.env` in this directory and fill in your key:

   ```bash
   cp .env.example .env
   # then edit .env and set ESV_API_KEY=your-key-here
   ```

   The server loads `.env` itself at startup (via `python-dotenv`), so this works no matter
   how it's launched — directly, through the MCP Inspector, or via Claude Code's `.mcp.json`
   — without depending on each launcher's environment-forwarding behavior. `.env` is
   git-ignored; no key is stored in the repo.

3. This server is also registered in the repo root's `.mcp.json` under `esv-bible`.

## Running standalone

```bash
cd esv-bible-mcp-server
uv sync
uv run server.py
```

## Running with the MCP Inspector

```bash
cd esv-bible-mcp-server
uv run mcp dev server.py
```

## Running the chat web app

```bash
cd esv-bible-mcp-server
uv run webapp.py
```

Then open http://localhost:5050. Type a passage reference (e.g. "Psalm 23") and the app
returns the ESV text plus an audio player; if the input doesn't resolve to a reference it
falls back to a full-text search and offers clickable suggestions.

## Notes

- The ESV API's audio endpoint returns an HTTP redirect to an MP3 file rather than the
  audio bytes themselves; `get_passage_audio_url` follows that redirect one hop and
  returns the resulting URL instead of downloading the file.
- Per ESV API terms, avoid requesting more than 500 verses of text or displaying passages
  without the "(ESV)" attribution (`include_short_copyright` is on by default via the API).
