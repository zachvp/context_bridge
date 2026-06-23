# Context Bridge — operating doc

See `PLAN.md` for the original design rationale. This file is the practical
"how do I actually run this" companion.

## Installation

```bash
bash install.sh
```

The wizard creates a `.venv`, installs dependencies, registers the MCP server
with Claude Code (global by default, so it's available in every session), and
writes a `.env` file for local config.

**Configuration** — `.env` (created by the wizard, gitignored) supports:

| Variable | Default | Purpose |
|---|---|---|
| `CONTEXT_BRIDGE_DB_PATH` | `./chat_memory.db` | Where the database lives |

Edit `.env` directly to change these after initial setup. See `.env.example` for
the template.

## Getting your Claude.ai export

There is no API for this — the export is pull-only, triggered manually:

1. Go to **Claude.ai → Settings → Account → Export Data**
2. Anthropic emails you a `.dms` file attachment (has been a few minutes in my exp)
3. Run `./build_all.sh path/to/export.dms` — it handles the rename, unpack, and rebuild

## Abstract overview

```
Claude.ai export (.zip)                ~/.claude/projects/**/*.jsonl
      │  unzip                                │  ingest_code_sessions.py
      ▼                                       │  (incremental, walk parentUuid tree)
data/inspect/                                 │
      │  ingest.py + embed.py                 │
      │  (full rebuild via build_db.py)       │
      └──────────────────┬────────────────────┘
                         ▼
               chat_memory.db   (SQLite — chunks + sessions + meta)
                         │  source: 'claude_ai' | 'claude_code'
                         │  server.py: search_chat_history, get_conversation
                         ▼
               Claude Code session, via "context-bridge" MCP
```

`build_db.py` is a **full rebuild every time** — it has no incremental/upsert
mode. Running it always parses the entire `data/inspect/` export, re-embeds
everything, and atomically replaces `chat_memory.db`. There is currently no
merge step for partial-window exports (e.g. a 90-day-only export) — always
use a full export, never a partial one, or you'll silently drop older history
from the DB on rebuild.

## Command cheat sheet

**Refresh the DB from a new export** (standard workflow — run this whenever
you pull a new Claude.ai export):
```bash
cd context_bridge
./build_all.sh data/chat-archive-<date>.dms
```
Pass the export file (`.dms` or `.zip`) and `build_all.sh` handles the unpack
step automatically. Omit the argument if `data/inspect/` is already populated.

**Run the MCP server manually** (for a quick smoke check outside Claude Code):
```bash
cd context_bridge
./run_server.sh
```
(This is also what `install.sh` registers as the `context-bridge` MCP server —
no separate setup needed once the venv/deps exist.)

**Sanity-check ingest/parsing only** (no embedding, no DB write — just see
what the export produces):
```bash
cd context_bridge
python3 ingest.py            # defaults to ./data/inspect
```

**Run the retrieval/smoke tests**:
```bash
cd context_bridge
python3 smoke_test.py
python3 retrieval_smoke_test.py
python3 mcp_smoke_test.py
```

**Refresh the DB from new Claude Code sessions** (incremental — safe to re-run
any time; skips already-ingested sessions):
```bash
cd context_bridge
python3 ingest_code_sessions.py
```

## How the MCP server is actually used

The server registers two tools with Claude at session start: `search_chat_history`
and `get_conversation`. Their schemas cost ~100–200 tokens each for the lifetime
of the session, whether or not they're ever called.

**What triggers a search:** the tool description drives autonomous behavior.
The current description is reactive — Claude calls `search_chat_history` when
it notices it's about to re-derive something it suspects has been covered before.
In a narrow coding task it may never fire; in a design or planning conversation
it may fire more.

**The most reliable pattern:** ask explicitly.
> "Search the context bridge for [topic]."

This produces a direct, well-formed tool call rather than leaving query
construction to Claude's autonomous judgment. Semantic search rewards
descriptive phrases over single keywords — "what did we decide about chunking
strategy" retrieves better than "chunking".

**Current retrieval limitation:** `search_chat_history` has no locality signal.
A query from a `sol_reason` session ranks `sol_reason` sessions no higher than
sessions from `synesthesia`, `djmgmt`, or any other project. This is the Phase 3
gap (`current_project` parameter — see `PLAN.md`). Until Phase 3 is implemented,
cross-project noise is a known retrieval quality ceiling.

## Notes / known constraints
- Export is manual, pull-only (Claude.ai Settings → Account → Export Data) —
  no API/webhook trigger.
- `build_db.py` writes to a `.tmp` file and `os.replace`s it into place, so a
  crash mid-rebuild never leaves a half-written `chat_memory.db` live.
- `chat_memory.db` and `data/` are gitignored — they're local build
  artifacts and data files, not committed.
