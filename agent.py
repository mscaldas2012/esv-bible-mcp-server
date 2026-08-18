"""Tool-calling agent for the ESV Bible chat app, built on the Claude Agent
SDK (claude_agent_sdk) instead of the raw Anthropic Messages API.

The SDK shells out to the local `claude` CLI, so it rides your Claude.ai
subscription login rather than spending Developer Platform API credits.

IMPORTANT: this only works if ANTHROPIC_API_KEY is NOT set in the process
environment — an exported key always wins over your subscription login (same
rule the repo-root webapp.py documents for the customer-support agent build).

Connects to server.py as a real external MCP server over stdio (same command
shape as .mcp.json) for the three ESV tools. MCP *prompts* aren't natively
invocable through the SDK's tool-calling loop — the CLI understands them as
slash commands for a human, but there's no `prompts/get`-style call exposed
to a script driving ClaudeSDKClient. Bridged the same way as the earlier
raw-Anthropic version: every prompt discovered via server.mcp.list_prompts()
is wrapped as one more in-process SDK tool (e.g. "use_prompt_explain_passage")
whose handler calls server.mcp.get_prompt(...) directly — in-process, no
extra IPC, since server.py is already imported as a library here — and hands
the rendered template back as the tool result for the model to follow.
"""

import dataclasses
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    ResultMessage,
    SdkMcpTool,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    create_sdk_mcp_server,
)

import esv_client
import server as esv_server

SERVER_DIR = Path(__file__).resolve().parent

AUDIO_TOOL_NAME = "mcp__esv-bible__get_passage_audio_url"


def _prompt_message_text(msg: Any) -> str:
    """Extract the text from one GetPromptResult message.

    FastMCP's get_prompt() builds GetPromptResult with `messages:
    list[PromptMessage]` typed — pydantic validates/coerces the field on
    construction, so `result.messages` holds real PromptMessage objects
    with a TextContent `.content`, not the plain dicts the intermediate
    jsonable-conversion step inside get_prompt() might suggest. Handle both
    shapes defensively rather than assuming one.
    """
    content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", None)
    if isinstance(content, dict):
        return content.get("text") or ""
    return getattr(content, "text", None) or ""

SYSTEM_PROMPT = """\
You are a Bible study assistant backed by the ESV Bible API via MCP tools.

Rules:
- Never quote Scripture from memory. Always retrieve it with
  get_passage_text so the wording is accurate and properly licensed, even
  for famous verses.
- The ESV API tolerates loose reference formats ("mt 5", "jn 3:16",
  "psalm 23") reasonably well — pass the user's phrasing through to
  get_passage_text directly rather than trying to normalize it yourself.
- If get_passage_text doesn't resolve a reference, try search_passages with
  the user's words before giving up.
- If the user asks you to "explain" a passage, call the matching
  use_prompt_explain_passage tool first to get structured guidance, then
  follow those instructions yourself (including calling get_passage_text).
- If the user might want to hear a passage, call get_passage_audio_url too.
- Keep replies conversational and concise; you don't need to dump the full
  ESV attribution boilerplate, a short "(ESV)" note is enough.
"""


async def _build_prompt_bridge_tools() -> list[SdkMcpTool]:
    """Wrap every MCP prompt on server.py as an in-process SDK tool."""
    prompts = await esv_server.mcp.list_prompts()
    tools: list[SdkMcpTool] = []

    for p in prompts:
        tool_name = f"use_prompt_{p.name.replace('-', '_')}"

        def make_handler(prompt_name: str):
            async def handler(args: dict[str, Any]) -> dict[str, Any]:
                result = await esv_server.mcp.get_prompt(prompt_name, args)
                rendered = "\n\n".join(
                    t for m in result.messages if (t := _prompt_message_text(m))
                )
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                f"[Expanded '{prompt_name}' prompt — follow these "
                                f"instructions:]\n\n{rendered}"
                            ),
                        }
                    ]
                }

            return handler

        description = f"Expand the MCP prompt template '{p.name}'"
        if p.description:
            description += f": {p.description}"
        description += ". Returns instructions for you to follow next — not a final answer."

        tools.append(
            SdkMcpTool(
                name=tool_name,
                description=description,
                input_schema={arg.name: str for arg in (p.arguments or [])},
                handler=make_handler(p.name),
            )
        )
    return tools


def _describe_block(header: str, description: str | None) -> list[str]:
    """header line, then the full description indented one line per sentence/paragraph line."""
    block = [f"  {header}"]
    for line in (description or "").strip().splitlines():
        if line.strip():
            block.append(f"      {line.strip()}")
    return block


async def describe_primitives() -> list[str]:
    """Full-description summary of every MCP primitive server.py exposes —
    tools, resources, resource templates, and prompts — queried straight off
    the FastMCP instance (server.mcp), the same registry described in the
    @mcp.tool()/@mcp.prompt() discussion: decorators register metadata there,
    they don't attach it to the functions themselves. Shows the complete
    multi-line description (what/when/edge-cases/ordering), not just a
    truncated first line, since that's the whole point of writing it that way.
    """
    tools = await esv_server.mcp.list_tools()
    resources = await esv_server.mcp.list_resources()
    templates = await esv_server.mcp.list_resource_templates()
    prompts = await esv_server.mcp.list_prompts()

    lines = ["[startup] esv-bible MCP server primitives:"]
    for t in tools:
        lines += _describe_block(f"[tool] {t.name} ({t.title or ''})", t.description)
    for r in resources:
        lines += _describe_block(f"[resource] {str(r.uri)}", r.description)
    for rt in templates:
        lines += _describe_block(f"[resource-template] {rt.uriTemplate}", rt.description)
    for p in prompts:
        args = ", ".join(a.name for a in (p.arguments or []))
        lines += _describe_block(f"[prompt] {p.name}({args})", p.description)
    return lines


# Single source of truth for slash commands — drives /help text and the
# frontend's command picker (exposed via /api/commands) so the two never
# drift out of sync with each other or with run_command()'s dispatch below.
SLASH_COMMAND_INFO: list[dict[str, str]] = [
    {
        "name": "search",
        "hint": "<word or phrase>",
        "description": "Full-text search across the ESV Bible. No LLM involved.",
    },
    {
        "name": "text",
        "hint": "<reference>",
        "description": "Raw ESV text for a reference (e.g. John 3:16). No LLM involved.",
    },
    {
        "name": "audio",
        "hint": "<reference>",
        "description": "Direct MP3 URL for a passage. No LLM involved.",
    },
    {
        "name": "explain",
        "hint": "<reference>",
        "description": "Expands the explain-passage MCP prompt, then asks the agent to follow it.",
    },
    {
        "name": "help",
        "hint": "",
        "description": "List these slash commands.",
    },
]


async def run_command(
    command: str, argument: str, resume_session_id: str | None = None
) -> dict[str, Any]:
    """Dispatch a "/<command> <argument>" chat message deterministically.

    This is the reliable way to get real slash-command behavior in this app.
    The underlying `claude` CLI *lists* MCP prompts as slash commands (e.g.
    "esv-bible:explain-passage") via get_server_info(), but sending that text
    through ClaudeSDKClient.query() does not reliably dispatch it — tested
    directly: a bare "/mcp__esv-bible__explain-passage" came back "Unknown
    command", and a function-call-style variant just got treated as an
    ordinary natural-language message instead of a real command. So instead
    of depending on that, /search, /text, and /audio call the tool directly
    here and skip the LLM turn entirely — fast, deterministic, zero tokens.
    /explain is different: a prompt's rendered text IS instructions for the
    model to follow, not a final answer, so it resolves the prompt via
    server.mcp.get_prompt() (guaranteed, not left to the model's judgment)
    and then still runs the result through the normal reply() loop.

    Returns the same shape as reply(): {"reply", "audio_url", "session_id", "log"}.
    """
    argument = argument.strip()
    log = [f"[slash_command] /{command} {argument!r}"]

    def usage(msg: str) -> dict[str, Any]:
        return {"reply": msg, "audio_url": None, "session_id": resume_session_id, "log": log}

    if command == "help" or not command:
        lines = ["Slash commands:"]
        lines += [f"/{c['name']} {c['hint']} — {c['description']}".rstrip() for c in SLASH_COMMAND_INFO]
        lines.append("Anything without a leading / is treated as natural language for the agent.")
        return usage("\n".join(lines))

    if command == "search":
        if not argument:
            return usage("Usage: /search <word or phrase>")
        try:
            data = esv_client.search_passages(argument, page_size=5)
        except esv_client.ESVAPIError as exc:
            log.append(f"[error] search_passages: {exc}")
            return usage(str(exc))
        hits = data.get("results") or []
        log.append(f"[direct_call] search_passages({{'query': {argument!r}}}) -> {len(hits)} hits")
        reply_text = (
            "\n".join(f"- {h['reference']}: {h['content'][:100]}" for h in hits)
            if hits
            else f'No matches for "{argument}".'
        )
        return {"reply": reply_text, "audio_url": None, "session_id": resume_session_id, "log": log}

    if command in ("text", "passage"):
        if not argument:
            return usage("Usage: /text <reference>")
        try:
            data = esv_client.get_passage_text(argument)
        except esv_client.ESVAPIError as exc:
            log.append(f"[error] get_passage_text: {exc}")
            return usage(str(exc))
        passages = data.get("passages") or []
        log.append(f"[direct_call] get_passage_text({{'reference': {argument!r}}})")
        reply_text = "\n\n".join(passages) if passages else f"No passage found for '{argument}'."
        return {"reply": reply_text, "audio_url": None, "session_id": resume_session_id, "log": log}

    if command == "audio":
        if not argument:
            return usage("Usage: /audio <reference>")
        try:
            url = esv_client.get_passage_audio_url(argument)
        except esv_client.ESVAPIError as exc:
            log.append(f"[error] get_passage_audio_url: {exc}")
            return usage(str(exc))
        log.append(f"[direct_call] get_passage_audio_url({{'reference': {argument!r}}})")
        return {"reply": f"Audio for {argument}:", "audio_url": url, "session_id": resume_session_id, "log": log}

    if command == "explain":
        if not argument:
            return usage("Usage: /explain <reference>")
        rendered = await esv_server.mcp.get_prompt("explain-passage", {"reference": argument})
        prompt_text = "\n\n".join(t for m in rendered.messages if (t := _prompt_message_text(m)))
        log.append(f"[prompt_expand] explain-passage({{'reference': {argument!r}}}) — handing to agent")
        result = await reply(prompt_text, resume_session_id)
        result["log"] = log + result["log"]
        return result

    return usage(f"Unknown command /{command}. Try /help.")


async def _build_options() -> ClaudeAgentOptions:
    prompt_tools = await _build_prompt_bridge_tools()
    prompt_server = create_sdk_mcp_server(name="esv-prompts", tools=prompt_tools)

    return ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        mcp_servers={
            "esv-bible": {
                "command": "uv",
                "args": ["--directory", str(SERVER_DIR), "run", "server.py"],
            },
            "esv-prompts": prompt_server,
        },
        allowed_tools=[
            "mcp__esv-bible__search_passages",
            "mcp__esv-bible__get_passage_text",
            AUDIO_TOOL_NAME,
            *[f"mcp__esv-prompts__{t.name}" for t in prompt_tools],
        ],
    )


GROUNDING_TOOL_MATCHER = "mcp__esv-bible__get_passage_text|mcp__esv-bible__search_passages"


def _build_grounding_gate_hooks(log: list[str] | None = None) -> dict[str, list[HookMatcher]]:
    """Stop-hook gate for 'never quote Scripture from memory'
    (agentic-best-practices/rules/enforcement-vs-guidance.md). Fresh
    closure per reply() call — matches reply()'s own fresh-client-per-call
    design, so no session-keyed state is needed. Any free-text message
    that reaches reply() is, by construction of this app (slash commands
    bypass reply() via run_command()'s deterministic dispatch), a
    substantive ask about Bible content, so grounding is required by
    default rather than pattern-matched from the input.

    `log`, if given, is reply()'s own log list — appending to it here (as
    opposed to returning something the caller has to notice) is what makes
    a block visible in the UI's "agent log" panel instead of silently
    steering the model via the hook's `reason` text alone."""
    grounded = False

    async def mark_grounded(input_data, tool_use_id, context) -> dict:
        nonlocal grounded
        grounded = True
        return {}

    async def require_grounding(input_data, tool_use_id, context) -> dict:
        # stop_hook_active = we already blocked once this turn; let it
        # through rather than risk an infinite loop if the model genuinely
        # can't satisfy the gate (e.g. a stray "hi").
        if input_data.get("stop_hook_active") or grounded:
            return {}
        if log is not None:
            log.append(
                "[grounding_gate] BLOCKED stop — no grounding tool called yet, "
                "forcing model to retrieve before answering"
            )
        return {
            "decision": "block",
            "reason": (
                "You have not called get_passage_text or search_passages "
                "yet. Never quote, paraphrase, or explain Bible content "
                "from memory — call one of those tools to retrieve the "
                "actual ESV wording before responding."
            ),
        }

    return {
        "PostToolUse": [
            HookMatcher(matcher=GROUNDING_TOOL_MATCHER, hooks=[mark_grounded])
        ],
        "Stop": [HookMatcher(hooks=[require_grounding])],
    }


def _result_text(content: str | list[dict[str, Any]] | None) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(part.get("text", "") for part in content if isinstance(part, dict))
    return ""


async def reply(user_message: str, resume_session_id: str | None = None) -> dict[str, Any]:
    """Run one turn against a fresh ClaudeSDKClient, resuming resume_session_id if given.

    Returns {"reply": str, "audio_url": str | None, "session_id": str, "log": list[str]}.
    `log` mirrors the format used by the repo-root main.py's process_message —
    "[tool_use] name(args)" per call, "[error] name: message" for failures,
    "stop_reason=..." at the end — so both apps' UIs can render logs the same way.
    """
    options = await _build_options()
    if resume_session_id:
        options = dataclasses.replace(options, resume=resume_session_id)

    reply_parts: list[str] = []
    log: list[str] = []
    options = dataclasses.replace(options, hooks=_build_grounding_gate_hooks(log))
    audio_url: str | None = None
    session_id = resume_session_id
    pending_tool_names: dict[str, str] = {}

    async with ClaudeSDKClient(options=options) as client:
        await client.query(user_message)
        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, ToolUseBlock):
                        pending_tool_names[block.id] = block.name
                        log.append(f"[tool_use] {block.name}({block.input})")
                    elif isinstance(block, TextBlock) and block.text.strip():
                        reply_parts.append(block.text.strip())
            elif isinstance(msg, UserMessage) and isinstance(msg.content, list):
                for block in msg.content:
                    if not isinstance(block, ToolResultBlock):
                        continue
                    tool_name = pending_tool_names.get(block.tool_use_id, "?")

                    if block.is_error:
                        log.append(f"[error] {tool_name}: {_result_text(block.content)}")
                        continue

                    if tool_name != AUDIO_TOOL_NAME:
                        continue
                    if isinstance(block.content, str):
                        audio_url = block.content
                    elif isinstance(block.content, list) and block.content:
                        audio_url = block.content[0].get("text")
            elif isinstance(msg, ResultMessage):
                session_id = msg.session_id
                log.append(f"stop_reason={msg.stop_reason}")

    return {
        "reply": "\n\n".join(reply_parts),
        "audio_url": audio_url,
        "session_id": session_id,
        "log": log,
    }
