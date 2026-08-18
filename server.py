"""MCP server exposing tools for the ESV Bible API (https://api.esv.org/docs/).

Requires the ESV_API_KEY environment variable (see esv_client.py / .env.example).
"""

from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent
from pydantic import Field

import esv_client
from canon import CANON

mcp = FastMCP("esv-bible")


def _error_result(exc: esv_client.ESVAPIError) -> CallToolResult:
    """Build an error CallToolResult that carries a machine-readable category.

    `structuredContent.error.category` is one of "transient", "validation",
    "business_rule", or "permission" — see esv_client.py for what each means
    and how a caller should react (retry vs. fix input vs. stop and alert).
    """
    return CallToolResult(
        content=[TextContent(type="text", text=str(exc))],
        isError=True,
        structuredContent={"error": {"category": exc.category, "message": str(exc)}},
    )


@mcp.tool(
    title="Search ESV Bible Text",
    description=(
        "Full-text search across the entire ESV Bible for a word, topic, or exact phrase.\n"
        "Use when the user's request is NOT a specific reference — a topic, keyword, or "
        "phrase they want located in Scripture (e.g. \"verses about grace\", \"where does it "
        "say...\").\n"
        "Empty results are a normal, valid outcome, not an error — report them plainly rather "
        "than retrying with the same query.\n"
        "Prefer get_passage_text first if the input already looks like a real reference "
        "(e.g. \"John 3:16\"); only fall back to this tool once that returns nothing."
    ),
)
def search_passages(
    query: Annotated[
        str,
        Field(
            description=(
                'Search term or exact phrase. Wrap in double quotes for an exact-phrase '
                'match, e.g. \'"the rabble"\'; unquoted queries do a looser keyword match.'
            )
        ),
    ],
    page: Annotated[int, Field(description="Which page of results to return, starting at 1.")] = 1,
    page_size: Annotated[
        int, Field(description="Results per page, 1-100 (enforced as a business-rule check).")
    ] = 20,
) -> Any:
    try:
        return esv_client.search_passages(query, page=page, page_size=page_size)
    except esv_client.ESVAPIError as exc:
        return _error_result(exc)


@mcp.tool(
    title="Get ESV Passage Text",
    description=(
        "Retrieve the authoritative ESV wording for a specific Bible reference (e.g. "
        '"John 3:16", "Genesis 1-3", "Psalm 23").\n'
        "Use this whenever you need to quote or discuss actual Scripture text — never quote "
        "from memory, even for famous verses, since exact wording and verse boundaries must "
        "match the licensed ESV text.\n"
        "A reference the ESV API can't parse returns an empty result, not an error — treat "
        'that as "not found" and fall back to search_passages with the user\'s own words '
        "rather than retrying the same reference.\n"
        "Call this before get_passage_audio_url, and pass audio the canonical reference this "
        "tool returns rather than the user's original (possibly loose) phrasing."
    ),
)
def get_passage_text(
    reference: Annotated[
        str,
        Field(
            description=(
                'Passage reference to look up, e.g. "John 3:16" or "Genesis 1-3". Loose '
                'formats like "jn 3:16" are usually tolerated by the ESV API.'
            )
        ),
    ],
    include_footnotes: Annotated[
        bool, Field(description="Include footnote callouts and their full text below the passage.")
    ] = False,
    include_headings: Annotated[
        bool, Field(description="Include section headings (editorial subtitles).")
    ] = True,
    include_verse_numbers: Annotated[
        bool, Field(description="Prefix each verse with its verse number.")
    ] = True,
    include_passage_references: Annotated[
        bool, Field(description="Prefix the output with the resolved passage reference.")
    ] = True,
) -> Any:
    try:
        data = esv_client.get_passage_text(
            reference,
            include_footnotes=include_footnotes,
            include_headings=include_headings,
            include_verse_numbers=include_verse_numbers,
            include_passage_references=include_passage_references,
        )
    except esv_client.ESVAPIError as exc:
        return _error_result(exc)
    passages = data.get("passages", [])
    if not passages:
        return f"No passage found for '{reference}'."
    return "\n\n".join(passages)


@mcp.tool(
    title="Get ESV Passage Audio URL",
    description=(
        "Resolve a direct MP3 URL for spoken-word audio of a Bible passage.\n"
        "Only call this if the user asks to hear or listen to a passage, or clearly wants "
        "audio — don't call it by default alongside every text lookup.\n"
        "Returns a URL, not audio bytes — don't attempt to fetch or play it yourself, just "
        "surface the link to the user.\n"
        "Call get_passage_text first and pass its canonical reference here rather than the "
        "user's raw input — the audio catalog is keyed by canonical references."
    ),
)
def get_passage_audio_url(
    reference: Annotated[
        str,
        Field(
            description=(
                "Passage reference to look up, ideally the canonical form returned by "
                'get_passage_text (e.g. "John 11:35", "Psalm 23").'
            )
        ),
    ],
) -> Any:
    try:
        return esv_client.get_passage_audio_url(reference)
    except esv_client.ESVAPIError as exc:
        return _error_result(exc)


@mcp.resource(
    "esv://canon",
    name="canon",
    title="Bible canon",
    description=(
        "The 66 books of the Protestant canon in order, each with its testament, "
        "common abbreviation, and chapter count."
    ),
    mime_type="application/json",
)
def canon() -> list[dict[str, Any]]:
    return CANON


@mcp.prompt(
    name="explain-passage",
    title="Explain a passage",
    description="Explain the context and meaning of an ESV Bible passage.",
)
def explain_passage(reference: str) -> str:
    return (
        f"Look up {reference} using the get_passage_text tool (do not quote it from memory), "
        "then explain it for someone reading it for the first time:\n"
        "1. Historical and literary context (author, audience, where it falls in the book).\n"
        "2. A walkthrough of the main ideas, section by section.\n"
        "3. Any major interpretive points, cross-references, or theological themes worth noting.\n\n"
        "Quote the ESV text you retrieved to ground each point."
    )


if __name__ == "__main__":
    mcp.run()
