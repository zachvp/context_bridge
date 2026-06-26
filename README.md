# Context Bridge
This file is the practical "how do I actually run this" companion.
See `PLAN.md` for the original design rationale and vague roadmap. 

## Installation
```bash
bash scripts/wizard.sh
```

The wizard creates a `.venv`, installs dependencies, registers the MCP server
with Claude Code (global by default, so it's available in every session), and
writes a `.env` file for local config.

**Configuration** — `.env` (created by the wizard, gitignored) supports:

| Variable | Default | Purpose |
|---|---|---|
| `CONTEXT_BRIDGE_DB_PATH` | `./chat_memory.db` | Where the database lives |
| `CONTEXT_BRIDGE_MODEL` | `BAAI/bge-base-en-v1.5` | fastembed model ID; changing after a build triggers a full rebuild |
| `CONTEXT_BRIDGE_BATCH_SIZE` | `64` | Embedding batch size; reduce to `16` or `8` if you hit OOM during build |

Edit `.env` directly to change these after initial setup. See `.env.example` for
the template.

**Changing the embedding model:** set `CONTEXT_BRIDGE_MODEL` to a different
fastembed-compatible model ID, then run a **full rebuild** with your
complete Claude.ai export — `build_db.py` detects the model mismatch and skips
the partial-export merge to avoid mixing incompatible vectors. Partial exports
are safe again after the first full rebuild with the new model.

## Getting your Claude.ai export

There is no API for this — the export is pull-only, triggered manually:

1. Go to **Claude.ai → Settings → Account → Export Data**
2. Anthropic emails you a `.dms` file attachment (has been a few minutes in my exp)
3. Run `./scripts/build_all.sh path/to/export.dms` (or `.zip`) — it handles the rename, unpack, and rebuild

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

`build_db.py` always parses the entire `data/inspect/` export and re-embeds
everything, but before the atomic replace it merges back any `claude_ai`
chunks from the previous DB whose conversation/project UUID is absent from the
new export. This means a partial export (e.g. 90-day-only) is safe — older
history that isn't in the new export is preserved from the old DB.

**One exception:** if the embedding model changes between builds, the merge is
skipped (mixing vectors from two models would corrupt search). In that case run
`build_db.py` with a full export to get a clean rebuild.

## Commands

Each shell script accepts `--help` for full usage and options. Quick reference:

| Script | Purpose |
|---|---|
| `./scripts/wizard.sh` | One-time setup: venv, dependencies, MCP registration |
| `./scripts/build_all.sh` | Rebuild DB from a Claude.ai export (run after each new export) |
| `./scripts/run_server.sh` | Start the MCP server manually (smoke check outside Claude Code) |

```bash
./scripts/build_all.sh --help      # full options + steps
./scripts/wizard.sh --help         # prerequisites + what the wizard does
```

**Tests and standalone scripts:**
```bash
pytest                           # unit tests (ingest, query, build_db, code sessions)
bash tests/check_docs.sh         # structural lint (versions, file paths)
python3 ingest_code_sessions.py  # incremental Claude Code session ingest
python3 ingest.py                # parse-only, no embedding (dry-run check)
python3 query.py "your query"    # ad-hoc CLI search (--top-k N, --db PATH)
```

## How the MCP server is actually used

The server registers two tools with Claude at session start: `search_chat_history`
and `get_conversation`. Their schemas cost ~100–200 tokens each for the lifetime
of the session, whether or not they're ever called.

**What triggers a search:**
Note that the tool description drives autonomous behavior.
The current description is reactive: Claude calls `search_chat_history` when
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
A query from a `foo` session ranks `foo` sessions no higher than
sessions from `bar`, `baz`, or any other project. This is the Phase 3
gap (`current_project` parameter — see `PLAN.md`). Until Phase 3 is implemented,
cross-project noise is a known retrieval quality ceiling.

## Troubleshooting / FAQ

**The model download hangs or fails.**
fastembed downloads `~130 MB` on first run. If it times out, check your network
and retry. The cache lives at `~/.cache/fastembed/`.

**`build_all.sh` says "OOM" or crashes during embedding.**
Reduce `CONTEXT_BRIDGE_BATCH_SIZE` in `.env` (try `16` or `8`) and re-run.

**The MCP server isn't appearing in Claude Code.**
Run `claude mcp list` to verify registration, then restart Claude Code (exit & resume session).
The server list is read at session start. If it's missing, re-run `./scripts/wizard.sh`.

**`search_chat_history` returns nothing (or only irrelevant results).**
Run `./scripts/build_all.sh` first — the server needs a built `chat_memory.db`. If the
DB exists, try a more descriptive phrase ("what did we decide about X") rather
than a single keyword.

**Claude Code sessions aren't appearing in search.**
Run `python3 ingest_code_sessions.py` to ingest the latest sessions, then
restart the MCP server. This step is separate from the Claude.ai export build.

**I changed the embedding model and now search is broken.**
See "Changing the embedding model" under Configuration above.

## Notes / known constraints
- `chat_memory.db` and `data/` are gitignored — local build artifacts, not committed.
