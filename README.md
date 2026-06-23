# Context Bridge — operating doc

> **Status: beta.** Core search and ingestion are stable. Phase 3
> (project-local retrieval) is not yet implemented — see the retrieval
> limitation note below and `PLAN.md` for the roadmap.

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
| `CONTEXT_BRIDGE_MODEL` | `BAAI/bge-base-en-v1.5` | HuggingFace model ID (must be sentence-transformers compatible); changing after a build triggers a full rebuild |
| `CONTEXT_BRIDGE_BATCH_SIZE` | `64` | Embedding batch size; reduce to `16` or `8` if you hit OOM during build |

Edit `.env` directly to change these after initial setup. See `.env.example` for
the template.

**Changing the embedding model:** set `CONTEXT_BRIDGE_MODEL` to a different
HuggingFace sentence-transformers model ID, then run a **full rebuild** with your
complete Claude.ai export — `build_db.py` detects the model mismatch and skips
the partial-export merge to avoid mixing incompatible vectors. Partial exports
are safe again after the first full rebuild with the new model.

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
| `./install.sh` | One-time setup: venv, dependencies, MCP registration |
| `./build_all.sh` | Rebuild DB from a Claude.ai export (run after each new export) |
| `./run_server.sh` | Start the MCP server manually (smoke check outside Claude Code) |

```bash
./build_all.sh --help       # full options + steps
./install.sh --help         # prerequisites + what the wizard does
```

**Tests and standalone scripts:**
```bash
python3 tests/smoke_test.py           # validate ingest/parse stage (no DB)
python3 tests/retrieval_smoke_test.py # end-to-end retrieval quality
python3 tests/mcp_smoke_test.py       # MCP tool call smoke check
python3 tests/test_build_db.py        # build_db unit tests
bash tests/check_docs.sh              # structural lint (versions, file paths)
python3 ingest_code_sessions.py       # incremental Claude Code session ingest
python3 ingest.py                     # parse-only, no embedding (dry-run check)
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

## Troubleshooting / FAQ

**The model download hangs or fails.**
HuggingFace downloads `~440 MB` on first run. If it times out, set
`HF_HUB_OFFLINE=1` and re-run after the model is cached, or check your network.
The cache lives at `~/.cache/huggingface/`.

**`build_all.sh` says "OOM" or crashes during embedding.**
Reduce `CONTEXT_BRIDGE_BATCH_SIZE` in `.env` (try `16` or `8`) and re-run.

**The MCP server isn't appearing in Claude Code.**
Run `claude mcp list` to verify registration, then restart Claude Code — the
server list is read at session start. If it's missing, re-run `./install.sh`.

**`search_chat_history` returns nothing (or only irrelevant results).**
Run `./build_all.sh` first — the server needs a built `chat_memory.db`. If the
DB exists, try a more descriptive phrase ("what did we decide about X") rather
than a single keyword.

**Claude Code sessions aren't appearing in search.**
Run `python3 ingest_code_sessions.py` to ingest the latest sessions, then
restart the MCP server. This step is separate from the Claude.ai export build.

**I changed the embedding model and now search is broken.**
See the "Changing the embedding model" note under Configuration above. A full
rebuild with a complete Claude.ai export is required.

## Notes / known constraints
- Export is manual, pull-only (Claude.ai Settings → Account → Export Data) —
  no API/webhook trigger.
- `build_db.py` writes to a `.tmp` file and `os.replace`s it into place, so a
  crash mid-rebuild never leaves a half-written `chat_memory.db` live.
- `chat_memory.db` and `data/` are gitignored — they're local build
  artifacts and data files, not committed.
