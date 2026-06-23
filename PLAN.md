# Context Bridge — Claude.ai ↔ Claude Code Working Memory

## Problem
Claude Code's session context is short, execution-oriented, and disposable. Claude.ai chat
history accumulates broad/conceptual discussion over time, but that history is locked inside
the chat app's UI — Code has no way to query it. The same core ideas keep getting re-explained
from scratch across chat sessions because there's no shared, searchable "working memory" layer
between the two surfaces.

## Goal
Turn a Claude.ai data export into a local, searchable knowledge base that Claude Code can query
via an MCP server — so a session can ask "have we covered this before?" and pull back relevant
threads instead of re-deriving them.

## Stack decisions (locked in)
- **Vector store:** SQLite + `sqlite-vec` — single file, no server process, easy to back up,
  plenty fast at personal-chat-history scale.
- **Embeddings:** local `sentence-transformers` model — free, offline, no API key, good enough
  recall for this use case.
- **Interface:** an MCP server exposing search/retrieval tools, registered in Claude Code's MCP
  config so it's available as a callable tool every session.

## Known constraint
Claude.ai's export (Settings → Account → Export Data) is **manual and pull-only** — Anthropic
emails a `.dms` file (a renamed ZIP) within a few minutes of the request; there is no API or
webhook to trigger it automatically. So this system is *fresh-as-of-last-export*, not real-time.
Mitigation: make re-ingesting a new export a single command, and make ingest idempotent (upsert
by conversation id + message hash) so re-running it after each export is cheap and safe.
Browser-automation of the export itself (e.g. scraping extensions) was considered and rejected —
fragile against UI changes and ToS-gray; not worth it for a "re-run after export" workflow.

## Export format (confirmed)
- `.dms` file = ZIP archive.
- Contains `conversations.json`: a flat JSON array, one object per conversation.
- Each conversation: `uuid`, `name`, `created_at`, `updated_at`, model, and `chat_messages[]`.
- Each message: `sender` (`human`/`assistant`), `text`, `created_at`.
- No Project boundaries preserved in the export structure.

## Architecture

```
 export.zip (.dms)
      │
      ▼
 [1] ingest.py
      - unzip, parse conversations.json
      - chunk per conversation (sliding window of N turns, not single messages,
        to keep a thread's thesis intact)
      - embed each chunk (sentence-transformers, local)
      - upsert into SQLite (rows keyed by conversation_uuid + message_hash)
      ▼
 chat_memory.db (SQLite + sqlite-vec)
      - vector table: embedding, chunk_text, conversation_uuid, title, timestamp, turn_range
      - conversations table: uuid, title, created_at, updated_at, model
      ▼
 [2] MCP server (context_bridge_server)
      - tool: search_chat_history(query, top_k) -> [{snippet, conversation_uuid, title, timestamp, score}]
      - tool: get_conversation(uuid) -> full chat_messages[] for that thread
      ▼
 [3] Claude Code MCP config
      - registers the server so any session can call these tools mid-task
```

## File layout (proposed)
```
context_bridge/
  PLAN.md                 (this file)
  ingest.py                # unzip + parse + chunk + embed + upsert
  schema.sql               # SQLite table definitions (vec table + conversations table)
  server.py                # MCP server: search_chat_history, get_conversation
  chat_memory.db            # generated, gitignored
  README.md                 # usage: how to export, how to ingest, how to register the MCP server
```

## Milestones
1. **Schema + ingest** — parse a real export, chunk, embed, write to SQLite. Verify row counts
   and spot-check a few embeddings/retrievals manually (no MCP yet).
2. **Retrieval quality pass** — tune chunk size/overlap and top-k against a handful of known
   "we've discussed this before" queries pulled from your own chat history, before building
   the server around it.
3. **MCP server** — wrap the working retrieval logic in `search_chat_history` /
   `get_conversation` tools, register it in Code's MCP config, confirm Code can call it
   mid-session.
4. **Refresh workflow** — document/script the "got a new export → run ingest → done" loop;
   confirm upsert correctly skips unchanged conversations and updates changed ones.

## Open questions for later (not blocking the plan)
- Chunking strategy: fixed-size sliding window vs. semantic/topic-based splitting — start with
  the simple sliding window, revisit only if retrieval quality is poor.
- Whether to also ingest Claude Code session logs themselves into the same store, so the
  "working memory" spans both surfaces, not just Claude.ai exports.

---

# Phase 2: Claude Code session ingest

## Goal
Extend `chat_memory.db` to also index local Claude Code session transcripts from
`~/.claude/projects/`, so the MCP server surfaces context from *both* surfaces — Claude.ai
chat history and Claude Code working sessions — in a single query.

## Source format (confirmed via inspection)
- **Location:** `~/.claude/projects/<project-slug>/<session-uuid>.jsonl`
- **Scale:** 68 session files across 36 project directories as of 2026-06-18; grows continuously.
- **Record types per file:** `user`, `assistant`, `ai-title`, `system`, `mode`,
  `permission-mode`, `file-history-snapshot`, `attachment`, `last-prompt`.
- **Relevant records:** only `user` and `assistant` where content includes `text` blocks.
  Skip `thinking`, `tool_use`, `system`, `file-history-snapshot` — execution noise.
- **Session title:** `ai-title` record in same file.
- **Timestamps:** ISO string on `user`/`assistant` records (`2026-06-12T18:44:48.327Z`).
- **Session ID:** UUID in the filename — stable deduplication key.
- **Project label:** directory slug decodes to project name
  (e.g. `-Users-zachvp-developer-sol-reason` → `sol_reason`).

## Architecture delta

```
~/.claude/projects/**/<session-uuid>.jsonl   (live, grows continuously)
      │
      ▼
 [1b] ingest_code_sessions.py
      - glob all .jsonl files under ~/.claude/projects/
      - skip any file whose mtime < 60s ago (may be mid-write by active session)
      - skip sessions already in DB whose file mtime <= last-ingested timestamp
      - per session: walk parentUuid tree to extract canonical path (see below)
      - extract user/assistant text turns from canonical path only
      - chunk with sliding window (~6 turns, 50% overlap)
      - embed + upsert into same chat_memory.db
      ▼
 chat_memory.db  (same DB, same schema + one new column)
      - add `source` column: 'claude_ai' | 'claude_code'
      - add `project` column: human-readable project name (Code sessions only)
      - all existing search/retrieval tools work unchanged; source/project are filterable metadata
```

## The canonical path problem (main implementation rock)

Session `.jsonl` files store messages as a **tree** via `parentUuid` links, not a flat list.
Branching occurs when a message is edited or Claude retries (~8 branch points per long session).
Naively reading lines in order would ingest phantom/abandoned branches as real conversation.

**Solution:** build an adjacency list from `(parentUuid → [uuid])`, then walk depth-first from
the root, always following the child with the most descendants at each fork. That longest path
is the conversation that actually happened.

## Upsert / incremental refresh key

`(session_uuid, chunk_index)` — re-run any time to pick up new sessions. Skip files whose mtime
hasn't changed since last ingest. Skip files written in the last ~60 seconds.

## Risks and mitigations

| Risk | Mitigation |
|------|-----------|
| Branching tree yields phantom turns | Walk to longest path; ignore sidechain nodes |
| Ingesting the currently-active session (partial file) | Skip files with mtime < 60s |
| `thinking` blocks inflate chunk size and hurt retrieval | Explicitly filter: only `type == "text"` content blocks |
| Scale grows unboundedly | True upsert-by-session (not full rebuild like Claude.ai side) |
| Near-duplicate content across both surfaces | Retrieval top-k clustering handles it; no ingest-time dedup needed |

## Schema changes to chat_memory.db

```sql
-- add to existing chunks table:
ALTER TABLE chunks ADD COLUMN source TEXT NOT NULL DEFAULT 'claude_ai';
ALTER TABLE chunks ADD COLUMN project TEXT;
```

No other schema changes needed.

## New file

```
context_bridge/
  ingest_code_sessions.py   # new: ingest ~/.claude/projects/**/*.jsonl into chat_memory.db
```

`build_db.py` can be extended (or a new `build_db_full.py` added) to run both ingest paths
in sequence so a single command refreshes the full unified DB.

---

# Phase 3: Project-local retrieval (cache locality)

## Goal
Bias search results toward the current working project without hard-partitioning the DB — so
local context surfaces first while cross-project insights remain reachable.

## Motivation
Once the DB has multi-project data (Phase 2), a query from a `sol_reason` session will compete
against sessions from unrelated projects. The working directory is a strong locality signal —
analogous to cache locality — that retrieval should exploit.

## Design: two-phase retrieval

```
Phase 1: vector search WHERE project = current_project  →  3 results (local hits)
Phase 2: vector search WHERE project != current_project →  2 results (global fill-in)
Merge and return combined top_k=5
```

Local results always occupy the first slots. Global results fill the remainder. This gives cache
locality without discarding cross-project context.

If sqlite-vec doesn't efficiently support `WHERE` alongside vector search, use post-filter:
retrieve `top_k * 3` candidates globally, split by project match, take the top N from each
bucket, merge. Slightly less precise on scores but always correct.

## Tool change

Add an optional `current_project` parameter to `search_chat_history`:

```python
def search_chat_history(query_text: str, top_k: int = 5, current_project: str = None) -> list[dict]:
```

Update the tool description to instruct Claude to pass the current working directory's project
name. Claude knows its CWD from the session system prompt and will populate this naturally.

## Prerequisite
Phase 2 must be complete and the DB must have multi-project Claude Code sessions ingested —
the `project` column must be populated before this retrieval signal exists.

## Not a blocker
This is a retrieval quality improvement, not a correctness fix. Phase 2 search works fine
without it; Phase 3 just makes results more contextually relevant.
