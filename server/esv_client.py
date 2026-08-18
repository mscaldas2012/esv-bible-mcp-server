"""Thin sync client for the ESV Bible API (https://api.esv.org/docs/).

Shared by the MCP server (server.py) and the chat web app (webapp.py) so both
speak to the API the same way.

Errors are classified into four categories so a calling agent can decide how
to react (retry with backoff, fix its input, back off permanently, or stop
and alert a human) rather than treating every failure the same:

- transient      network hiccup, ESV 5xx, or ESV 429 rate-limit — safe to retry
- validation     malformed input the caller sent — retrying as-is won't help
- business_rule  well-formed input that violates a domain constraint (e.g. page
                  size over the ESV API's cap) — same call won't succeed
- permission     missing/invalid credentials — retrying won't help without a new key
"""

import os
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

ESV_API_BASE = "https://api.esv.org/v3"

MAX_PAGE_SIZE = 100


class ESVAPIError(RuntimeError):
    """Base error for a failed ESV API request. `category` classifies the failure."""

    category = "unknown"


class TransientESVError(ESVAPIError):
    category = "transient"


class ValidationESVError(ESVAPIError):
    category = "validation"


class BusinessRuleESVError(ESVAPIError):
    category = "business_rule"


class PermissionESVError(ESVAPIError):
    category = "permission"


def _headers() -> dict[str, str]:
    api_key = os.environ.get("ESV_API_KEY")
    if not api_key:
        raise PermissionESVError(
            "ESV_API_KEY is not set. Get a key from https://api.esv.org/ and put it in "
            "esv-bible-mcp-server/.env (see .env.example)."
        )
    return {"Authorization": f"Token {api_key}"}


def _classify_status_error(exc: httpx.HTTPStatusError, action: str) -> ESVAPIError:
    status = exc.response.status_code
    if status in (401, 403):
        return PermissionESVError(f"ESV API rejected the request ({status}): check ESV_API_KEY.")
    if status == 429:
        return TransientESVError(f"ESV API rate-limited the {action} request (429).")
    if status >= 500:
        return TransientESVError(f"ESV API is having trouble ({status}) during {action}.")
    if status == 400:
        return ValidationESVError(f"ESV API rejected the {action} request as malformed (400).")
    return ESVAPIError(f"ESV API {action} request failed: {status}")


def _classify_request_error(exc: httpx.RequestError, action: str) -> TransientESVError:
    return TransientESVError(f"Network error during ESV API {action} request: {exc}")


def search_passages(query: str, page: int = 1, page_size: int = 20) -> dict[str, Any]:
    """Search the full ESV Bible text for a word or phrase.

    Wrap `query` in double quotes for an exact phrase match.
    """
    if not 1 <= page_size <= MAX_PAGE_SIZE:
        raise BusinessRuleESVError(f"page_size must be between 1 and {MAX_PAGE_SIZE} (ESV API limit).")

    with httpx.Client() as client:
        try:
            resp = client.get(
                f"{ESV_API_BASE}/passage/search/",
                headers=_headers(),
                params={"q": query, "page": page, "page-size": page_size},
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise _classify_status_error(exc, "search") from exc
        except httpx.RequestError as exc:
            raise _classify_request_error(exc, "search") from exc
        return resp.json()


def get_passage_text(
    reference: str,
    include_footnotes: bool = False,
    include_headings: bool = True,
    include_verse_numbers: bool = True,
    include_passage_references: bool = True,
) -> dict[str, Any]:
    """Retrieve raw ESV passage-text API data for a reference.

    Returns the parsed JSON body, e.g. `{"query", "canonical", "passages"}`.
    `canonical` and `passages` are empty when the reference doesn't resolve.
    """
    params = {
        "q": reference,
        "include-footnotes": include_footnotes,
        "include-footnote-body": include_footnotes,
        "include-headings": include_headings,
        "include-verse-numbers": include_verse_numbers,
        "include-passage-references": include_passage_references,
        "include-short-copyright": True,
    }
    with httpx.Client() as client:
        try:
            resp = client.get(
                f"{ESV_API_BASE}/passage/text/", headers=_headers(), params=params
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise _classify_status_error(exc, "text lookup") from exc
        except httpx.RequestError as exc:
            raise _classify_request_error(exc, "text lookup") from exc
        return resp.json()


def get_passage_audio_url(reference: str) -> str:
    """Resolve a direct MP3 URL for spoken-word audio of an ESV passage."""
    with httpx.Client(follow_redirects=False) as client:
        try:
            resp = client.get(
                f"{ESV_API_BASE}/passage/audio/", headers=_headers(), params={"q": reference}
            )
        except httpx.RequestError as exc:
            raise _classify_request_error(exc, "audio lookup") from exc

        if resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get("location")
            if not location:
                raise ESVAPIError("ESV API returned a redirect with no Location header.")
            return location

        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise _classify_status_error(exc, "audio lookup") from exc
        return str(resp.url)
