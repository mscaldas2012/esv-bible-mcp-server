# Scratchpad — esv-bible-mcp-server review + finding #1 resolution

**Purpose of this file:** a self-contained handoff for a new session picking
up implementation work. It carries the curated facts from a prior session's
review + design discussion — not a transcript. Read this file, you should
not need the original conversation.

**Status when this was written:**
- The `agentic-best-practices/` toolkit (CLAUDE.md/rules/skills/agent) is
  built and in [PR #13](https://github.com/mscaldas2012/ccaf-study/pull/13)
  (open, mergeable, not yet merged as of this writing).
- `esv-bible-mcp-server/` was reviewed against that toolkit's 16 rules.
  **No code in `esv-bible-mcp-server/` has been changed yet** — this file
  covers review findings + a fully-designed (but unimplemented) fix for
  finding #1 only. Findings #2-6 are documented but not designed/fixed.
- The fix for finding #1 should land as its **own PR**, separate from #13,
  once implemented.

---

## 1. How the review was done

Applied `agentic-best-practices/agents/agentic-architecture-reviewer.md`'s
checklist manually against `esv-bible-mcp-server/server.py`,
`esv_client.py`, `agent.py`, `webapp.py`, `canon.py`, plus `.mcp.json` and
`.env.example`. 6 of the toolkit's 16 rules had nothing to check here (no
subagents, no batch processing, no human escalation/review workflow, no
dedicated CI for this subproject) — skipped rather than forced.

## 2. What's already solid (don't break these while fixing anything below)

- Error categorization exists and is well-documented — `esv_client.py`'s
  four `ESVAPIError` subclasses (`TransientESVError`, `ValidationESVError`,
  `BusinessRuleESVError`, `PermissionESVError`) map cleanly onto
  `rules/error-contract.md`'s four categories.
- Empty-result-vs-error is correctly distinguished in both
  `search_passages` and `get_passage_text` (zero matches = success with
  empty payload, not an error).
- Tool descriptions in `server.py` are a genuine model example of
  `rules/tool-interface-design.md` — explicit boundaries, edge cases,
  call-ordering guidance baked into every description.
- `canon` is correctly modeled as an MCP **resource**, not a tool
  (`rules/mcp-integration.md`).
- Secrets are never hardcoded — `.mcp.json` uses `${ESV_API_KEY}`
  expansion, `.env.example` is a template.
- Tool set is small and role-scoped — no over-provisioning.

## 3. All findings from the review (most severe first)

**#1 — [enforcement-vs-guidance.md] "Never quote Scripture from memory" is
compliance-critical but only prompt-enforced, in three places, with no
gate.** See §4-6 below for the full design. This is the one being resolved.

**#2 — [error-contract.md] An unmapped HTTP status falls through to a live
`"unknown"` category.**
Location: `esv_client.py` `_classify_status_error` (~L62-72); base class
default `category = "unknown"` at ~L33.
Only 401/403/429/5xx/400 are explicitly classified; anything else (a 404,
or a new status the ESV API starts returning) raises the base
`ESVAPIError`, whose category is `"unknown"` — a fifth value outside the
four the contract defines, forwarded verbatim into
`structuredContent.error.category` by `server.py`'s `_error_result`.
Fix direction: decide explicitly where unmapped statuses land (most
plausibly `validation`) instead of leaving `"unknown"` reachable. Not
designed further than this.

**#3 — [error-contract.md] No explicit `is_retryable` field; retryability
is inferred from category name by convention.**
Location: the four `ESVAPIError` subclasses in `esv_client.py`, and
`server.py`'s `_error_result` (~L18-29).
Fix direction: add a `retryable: bool` key alongside `category` in
`_error_result`'s `structuredContent.error` payload, derived once from
category. Not designed further.

**#4 — [error-contract.md] No local recovery for transient failures —
every network blip or 429 propagates immediately.**
Location: `search_passages`/`get_passage_text`/`get_passage_audio_url` in
`esv_client.py` — no retry around any `httpx` call.
Possibly an intentional simplicity tradeoff for a small demo server, not
necessarily a bug — flagged as a decision point, not designed further.

**#5 — [session-lifecycle.md + error-contract.md] A failed session resume
surfaces as a generic exception string with no fresh-session fallback.**
Location: `webapp.py` `/api/message` (~L52-53) — `except Exception` returns
`f"Agent error: {exc}"` uniformly for every failure mode.
Fix direction: catch resume-specific failure distinctly, clear the stale
`claude_session_id` from the Flask session, either auto-retry as a fresh
session or point the user at `/api/reset` explicitly. Not designed further.

**#6 — [agentic-loop-control.md] `stop_reason` is captured and logged but
never shapes the user-facing response.**
Location: `agent.py` `reply()` (~L357-359) — `stop_reason` is appended to
`log` but never branched on when building the returned `reply` text.
Fix direction: branch on `msg.stop_reason` before building the final
`reply` string; at minimum flag the response distinctly when it isn't
`end_turn`. Not designed further.

---

## 4. Finding #1 — design history (why two earlier ideas were rejected)

**Rejected idea A — trigger the gate off a regex built from `canon.py`'s
book names/abbreviations, matching reference-shaped input
(`John 3:16`, `Genesis 1-3`).**
Broken by a real counterexample: named/thematic requests like *"show me
the Shema"* or *"the story of man's creation"* share zero tokens with any
book name — no finite pattern list generalizes to arbitrary named or
paraphrased asks ("that thing Paul said about love," etc). These are
arguably the highest-risk case for this finding, not an edge case: a named
ask forces the model to resolve "what passage is this" from its own
knowledge first, which is exactly the moment it's most likely to just keep
answering from memory instead of stopping to retrieve.

**Rejected idea B — use `tool_choice: "any"` to force a tool call.**
Two independent reasons this doesn't work, confirmed by inspecting the
installed SDK directly (not assumed):
1. **`ClaudeAgentOptions` has no `tool_choice` field at all** — grepped
   `claude_agent_sdk/types.py` in this subproject's own `.venv`
   (`grep -n "tool_choice" .venv/lib/python3.11/site-packages/claude_agent_sdk/types.py`
   → zero matches). This app talks to Claude through `ClaudeSDKClient`,
   which shells out to the `claude` CLI and runs its own internal
   multi-turn loop; the SDK options object doesn't expose per-call
   `tool_choice` override. There is nothing to set.
2. **Even ignoring (1), `"any"` forces *some* tool call, not the *right*
   one**, and has no per-turn memory of "already grounded, stop forcing
   more calls" — applied globally it would also force a tool call on the
   *final* synthesis turn after `get_passage_text` already ran, breaking
   the normal "retrieve, then answer" shape of every successful turn.

## 5. Finding #1 — the actual design (ready to implement)

**Mechanism: an SDK-native `Stop` hook, paired with a `PostToolUse` hook.**
Confirmed by inspecting the installed SDK (same `.venv`, version pinned
`claude-agent-sdk>=0.2.132` in `pyproject.toml`) that these primitives
exist and behave as follows — grounded, not assumed:

- `HookEvent` literal (in `claude_agent_sdk/types.py`) includes `"Stop"`
  alongside `"PreToolUse"`/`"PostToolUse"`/etc.
- `StopHookInput` has a `stop_hook_active: bool` field — the SDK's own
  loop-safety valve, true when a `Stop` hook already blocked once this
  turn. **Must check this to avoid an infinite block loop.**
- `HookCallback` signature: `async def hook(input_data: dict, tool_use_id: str | None, context: HookContext) -> dict`
  (runtime shape is a plain dict, per the existing pattern in the
  repo-root `hooks.py`, even though the types are `TypedDict`).
- `HookMatcher(matcher: str | None, hooks: list[HookCallback], timeout: float | None)`
  — `matcher` supports pipe-combined tool names, e.g. the SDK's own
  docstring example `"Write|MultiEdit|Edit"`.
- A blocking `Stop`-hook return is `{"decision": "block", "reason": "<feedback to Claude>"}`
  — `reason` becomes feedback the model sees and can act on within the
  same turn, so the model can self-correct (call the tool, then answer)
  rather than producing two disjointed replies.
- `ClaudeAgentOptions.hooks: dict[HookEvent, list[HookMatcher]] | None` —
  confirmed field, matches the wiring pattern already used at the repo
  root (`main.py` builds `ClaudeAgentOptions(hooks={"PreToolUse": [...], "PostToolUse": [...]})`
  from lists assembled in `hooks.py`).
- Dispatch note from the SDK's own docstring: multiple matchers on the
  *same* event fire **concurrently**, not sequentially — not directly
  relevant here (only one hook per event is being registered), but keep in
  mind if more hooks get added to this gate later.

**Trigger condition: default-require grounding on every free-text turn
that reaches `reply()`, not pattern-matching.**
This works specifically because of how this app is already shaped:
`/search`, `/text`, `/audio`, `/help` already bypass `reply()` entirely via
`run_command()`'s deterministic dispatch (`agent.py` ~L196-280). Anything
that reaches `reply()` as free text is, by construction of this
single-purpose app, a substantive ask about biblical content — no other
conversational surface exists to false-positive against. `/explain` also
routes through `reply()` (it renders the MCP prompt then calls
`reply(prompt_text, ...)` at ~L276), so it's covered too.

**Grounding is satisfied by *either* `get_passage_text` OR
`search_passages`** — not just the first one. Search results carry real
ESV content snippets too (see the `/search` handler's `h['content'][:100]`
at ~L240), so a response built from search results is just as grounded as
one from `get_passage_text`. This also correctly handles the "Shema" case:
the model can call `get_passage_text` directly if confident of the
reference, or `search_passages` first if not — either satisfies the gate.

**State scope: pure closure, no session-keyed external storage needed.**
`reply()` already builds a fresh `ClaudeSDKClient` on every call (see its
own docstring: "Run one turn against a fresh ClaudeSDKClient..."). So "was
a grounding tool called this turn" only needs to live as long as one
`reply()` call — a plain closure variable, no `session_id`-keyed dict, no
reliance on `transcript_path` (which the SDK/docs warn is written
asynchronously and can lag the live turn — the same reason
`rules/enforcement-vs-guidance.md` says not to trust it for a real-time
gate).

### Concrete tool names (already established in this codebase)

`agent.py` already has the naming convention nailed down via its own
constant: `AUDIO_TOOL_NAME = "mcp__esv-bible__get_passage_audio_url"`
(~L45), i.e. `mcp__<server-name>__<tool-name>`, server name `"esv-bible"`
per `FastMCP("esv-bible")` in `server.py` L15. So the two grounding tools
are:
- `mcp__esv-bible__get_passage_text`
- `mcp__esv-bible__search_passages`

### Sketch (not yet written into the file — starting point only)

```python
# agent.py — add HookMatcher to the existing claude_agent_sdk import block

GROUNDING_TOOL_MATCHER = "mcp__esv-bible__get_passage_text|mcp__esv-bible__search_passages"


def _build_grounding_gate_hooks() -> dict[str, list[HookMatcher]]:
    """Stop-hook gate for 'never quote Scripture from memory'
    (agentic-best-practices/rules/enforcement-vs-guidance.md). Fresh
    closure per reply() call — matches reply()'s own fresh-client-per-call
    design, so no session-keyed state is needed. Any free-text message
    that reaches reply() is, by construction of this app (slash commands
    bypass reply() via run_command()'s deterministic dispatch), a
    substantive ask about Bible content, so grounding is required by
    default rather than pattern-matched from the input."""
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
```

Wire into `reply()` (~L321-323), alongside the existing `resume` replace:

```python
options = await _build_options()
if resume_session_id:
    options = dataclasses.replace(options, resume=resume_session_id)
options = dataclasses.replace(options, hooks=_build_grounding_gate_hooks())
```

## 6. Remaining implementation steps (checklist for the new session)

1. Add `HookMatcher` to `agent.py`'s existing `claude_agent_sdk` import
   (currently ~L27-38).
2. Add `_build_grounding_gate_hooks()` (sketch above) to `agent.py`.
3. Wire it into `reply()` via `dataclasses.replace`, as sketched.
4. Manually test with `uv run webapp.py`:
   - A named/thematic ask ("tell me about the Shema") — confirm the
     `[tool_use]` log line shows `get_passage_text` or `search_passages`
     being called before the final answer (reply()'s own `log` list
     already surfaces this per-turn, no new instrumentation needed).
   - A literal reference ask ("John 3:16") — same check.
   - A non-content message ("hi") — confirm it does **not** hang; expect
     one blocked attempt (visible as a forced tool call in the log) then
     a normal reply on the next turn via `stop_hook_active`.
5. Once verified working, this becomes its own PR — **do not stack it on
   #13**, which is toolkit-only. Consider using
   `agentic-best-practices/skills/scaffold-enforcement-gate/SKILL.md` as
   the reference workflow while implementing, and running
   `agentic-best-practices/agents/agentic-architecture-reviewer.md`
   against the finished diff afterward to confirm it now passes
   `rules/enforcement-vs-guidance.md` and doesn't regress anything else.
6. Findings #2-6 (§3 above) are still open and undesigned — separate work,
   not blocking #1.

## 7. Links

- Toolkit PR: https://github.com/mscaldas2012/ccaf-study/pull/13
- Rule driving this work: `agentic-best-practices/rules/enforcement-vs-guidance.md`
- Scaffolding skill: `agentic-best-practices/skills/scaffold-enforcement-gate/SKILL.md`
- Reviewer agent (re-check after implementing): `agentic-best-practices/agents/agentic-architecture-reviewer.md`
- Reference pattern already in this repo (same hook style, different gate):
  repo-root `hooks.py` (the `get_customer`→`process_refund` gate) + `main.py`
  (where `POST_TOOL_USE_HOOKS`/`PRE_TOOL_USE_HOOKS` get wired into
  `ClaudeAgentOptions(hooks=...)`).
