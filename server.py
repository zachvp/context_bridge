#!/usr/bin/env python3
"""
MCP server (beat 5). Exposes the already-tested retrieval logic from
query.py as two tools over stdio, so a Claude Code session can pull in
relevant chunks from your Claude.ai chat history mid-task.

This file is deliberately thin — it does protocol plumbing only. All the
retrieval logic (and its quality testing) already happened in query.py /
retrieval_smoke_test.py before this was written.

Run directly for a quick manual check, or register it with Claude Code's MCP
config to use it from a real session (beat 7).
"""

import json
import os
import threading
from pathlib import Path

from mcp.server.fastmcp import FastMCP

import query

mcp = FastMCP("context-bridge")

_PROJECT_ROOT = Path(__file__).parent
_STATS_PATH = Path(os.environ.get("MCP_STATS_PATH", _PROJECT_ROOT / "logs" / "mcp_stats.json"))
_stats_lock = threading.Lock()
_stats: dict = {"calls": 0, "bytes_out": 0, "by_tool": {}}


def _record(tool: str, result) -> None:
    size = len(json.dumps(result).encode())
    with _stats_lock:
        _stats["calls"] += 1
        _stats["bytes_out"] += size
        entry = _stats["by_tool"].setdefault(tool, {"calls": 0, "bytes_out": 0})
        entry["calls"] += 1
        entry["bytes_out"] += size
        _STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _STATS_PATH.write_text(json.dumps(_stats))


@mcp.tool()
def search_chat_history(query_text: str, top_k: int = 5) -> list[dict]:
    """Search past AI conversations and project docs for relevant context.
    Use this to check whether a topic, decision, or concept has already been
    discussed before re-deriving it from scratch.

    Args:
        query_text: what to search for, in natural language.
        top_k: how many results to return (default 5).

    Returns a list of hits, each with: id, title, source_type, timestamp,
    score (cosine similarity, higher is more relevant), and the full chunk text.
    Each chunk is ~1500 chars. For additional surrounding context, pass the
    hit's id to get_nearby_context. To retrieve the entire conversation, pass
    the hit's uuid to get_conversation:
    - conversation hit id format: <uuid>:<n>  — uuid is on left ":"
    - code_session hit id format: code:<uuid>:<n>  — uuid is in middle
    """
    result = []
    try:
        hits = query.search(query_text, top_k=top_k)
        result = [
            {
                "id": h.id,
                "title": h.title,
                "source_type": h.source_type,
                "timestamp": h.timestamp,
                "score": round(h.score, 4),
                "text": h.text,
            }
            for h in hits
        ]
        return result
    finally:
        _record("search_chat_history", result)


@mcp.tool()
def get_nearby_context(chunk_id: str, num_chunks: int = 2) -> str:
    """Retrieve a search hit's chunk plus surrounding context without loading
    the full conversation. Prefer this over get_conversation when you need
    local context around a specific match from search_chat_history.

    Args:
        chunk_id: the id field from a search_chat_history hit.
        num_chunks: how many chunks to include before and after the hit (default 2).

    Returns the surrounding window as text, with a header showing which chunk
    range was returned (e.g. "[chunks 3–7 of 22]"). Returns empty string if
    the chunk id is not found.
    """
    result = ""
    try:
        result = query.get_nearby_context(chunk_id, num_chunks=num_chunks)
        return result
    finally:
        _record("get_nearby_context", result)


@mcp.tool()
def get_conversation(conversation_uuid: str) -> str:
    """Retrieve the full reconstructed text of a conversation by its uuid
    (the part of a search_chat_history hit's id before the first ":").
    Handles both regular conversations and code sessions — for code session
    hits (id: code:<uuid>:<n>), pass the middle uuid part, not "code".
    Returns an empty string if no conversation with that uuid is found.
    """
    result = ""
    try:
        result = query.get_conversation(conversation_uuid)
        return result
    finally:
        _record("get_conversation", result)


if __name__ == "__main__":
    mcp.run()
