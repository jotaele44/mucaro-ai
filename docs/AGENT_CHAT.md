# Claude chat agent

Status: IMPLEMENTED against recorded/scripted API streams. **Live path unverified** until a run with a real
Anthropic API key is recorded (see "Verification" below).

When Claude is connected (Settings → Claude: an Anthropic API key plus the consent switch, or
`ANTHROPIC_API_KEY` with `MUCARO_AI_LIVE_MODE=1` for headless use), every chat turn is answered by Claude
through a streaming tool loop (`backend/app/agent.py`). Otherwise the cited-answer pipeline answers as before.

## What the user sees

- One progress line at a time while Claude works: "Thinking…", "Searching the web for …", "Found 3 results
  (docs.github.com, …)", "Reading notes.md", "Waiting for your approval". These come from `progress` run
  events: Claude's between-step notes (`thinking.display: "updates"`), narration written before a tool call,
  and Mucaro's own descriptions of each tool call and result.
- The answer streams in as Markdown (headings, lists, tables, code with a Copy button, links that open outside
  the app). `⟦n⟧` in the stored text marks a citation; it renders as a clickable `[n]` and copies as `[n]`.
- When the turn ends the steps fold into "Worked 14 s · 2 searches · 3 sources", with model, tokens and an
  approximate cost.

## What Claude may do, and what asks first

| Action | Where it runs | Mode `agent` (default, "Auto (safe actions)") | Mode `ask` / `auto` |
|---|---|---|---|
| Web search, web fetch | Anthropic (server tools) | runs | not offered |
| Code sandbox (no internet) | Anthropic (server tool) | runs | not offered |
| Search / read the document library | local, read-only | runs | `ask`: asks; `auto`: runs |
| List / read files in granted folders | local, read-only | runs | `ask`: asks; `auto`: runs |
| Repository status / diff / read | local, read-only | runs | `ask`: asks; `auto`: runs |
| Save to memory | local, changes state | **asks** | **asks** |
| Apply a patch | local, changes files | **asks** (validated first; the approval binds HEAD, patch hash and preimages) | **asks** |

Every client tool call goes through the governed tool runtime (`tools.request_tool_call` →
`approve_tool_call` → `run_tool_call`) and its hash-chained audit log; automatic approvals are recorded as
`policy:agent-safe-actions`. Server tools are executed by Anthropic and recorded as steps and citations, not
as local tool calls. `plan` mode never calls Claude. `options.engine` selects the pipeline explicitly:
`auto` (default), `agent`, or `cited`.

Files are readable only inside folders the user granted under Sources › Folder access; the app's own data
directory and `.env*` files are never readable. Web pages, documents and tool results are treated as data:
the system prompt tells Claude not to follow instructions found in them.

## Conversation history

Each conversation's Claude history is stored append-only in `agent_messages` (schema version 3) exactly as the
API returned it, and replayed byte-for-byte, so prompt caching and thinking blocks stay valid. Rules:

- Every `tool_use` gets a `tool_result` before anything else follows it. A stopped, failed, superseded or
  restarted turn commits only its complete exchanges and answers open tool calls with an error result.
- The tool list is frozen when a conversation first uses Claude; the web and sandbox toggles apply to new chats.
- Turns from before Claude was connected are carried in as plain text (mock answers and plan notes excluded).
- Deleting a conversation deletes its Claude history.

## Key and consent

The API key is stored in the macOS login Keychain (service `ai.mucaro.app`, scoped to the data directory),
or a mode-0600 file where the Keychain is unavailable (`MUCARO_AI_SECRET_STORE=keychain|file|env`). It is
never returned by the API (only its last four characters), never logged and never written to run events. It
can be set or removed only from the Mac that runs Mucaro, not from a phone on the network.

## Verification

- `backend/tests/test_agent.py` drives the real loop with scripted streams in the Messages API wire format:
  streaming, progress lines, citations, approvals, denial, stop mid-stream, restart, `pause_turn`, refusal,
  length limit, API errors, byte-identical history replay, frozen tools, granted-folder confinement, invalid
  tool input, consent and engine selection, and that the key never appears in events, history or files.
- `backend/tests/test_app_settings.py` covers key storage, consent and the Settings API.
- The live gate is one command once a key is saved in Settings → Claude:
  `.venv/bin/python scripts/claude_live_smoke.py`. It runs a real web-search question in a throwaway data
  directory, prints the progress lines and Anthropic request ids, and writes the raw response stream to
  `backend/tests/fixtures/agent_live/web_search.json` (it refuses to write if the key appears in it).
  `test_recorded_live_streams_replay_through_the_engine` then replays every recording in CI. Costs a few cents.
- Not yet done: that live run. Until then the request shape
  (beta headers `thinking-display-updates-2026-08-18`, `server-side-fallback-2026-07-01`,
  `thinking-binding-controls-2026-08-01`; `output_config.effort`; `fallbacks: "default"`; server tool versions
  `web_search_20260209`, `web_fetch_20260209`, `code_execution_20260521`) is verified only against the
  documentation and the installed SDK's request encoding (`anthropic` 0.75.0 passes the newer fields through
  `extra_body`; responses are read as raw server-sent events, so newer block types pass through untouched).

## Not covered (roadmap)

Image, audio and video generation; account connectors (Gmail, Drive, GitHub with your credentials); file
attachments in the composer and downloadable files from the sandbox (planned next); the packaged release
lanes, whose dependency locks do not include `anthropic` yet.
