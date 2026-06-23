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

from mcp.server.fastmcp import FastMCP

import query

mcp = FastMCP("context-bridge")


@mcp.tool()
def search_chat_history(query_text: str, top_k: int = 5) -> list[dict]:
    """Search past Claude.ai conversations and project docs for relevant
    context. Use this to check whether a topic, decision, or concept has
    already been discussed before re-deriving it from scratch.

    Args:
        query_text: what to search for, in natural language.
        top_k: how many results to return (default 5).

    Returns a list of hits, each with: id, title, source_type, timestamp,
    score (cosine similarity, higher is more relevant), and a text snippet.
    To retrieve the full thread, pass the hit's uuid to get_conversation:
    - conversation hit id format: <uuid>:<n>  — uuid is on left ":"
    - code_session hit id format: code:<uuid>:<n>  — uuid is in middle
    """
    hits = query.search(query_text, top_k=top_k)
    return [
        {
            "id": h.id,
            "title": h.title,
            "source_type": h.source_type,
            "timestamp": h.timestamp,
            "score": round(h.score, 4),
            "snippet": h.text[:500],
        }
        for h in hits
    ]


@mcp.tool()
def get_conversation(conversation_uuid: str) -> str:
    """Retrieve the full reconstructed text of a conversation by its uuid
    (the part of a search_chat_history hit's id before the first ":").
    Handles both regular conversations and code sessions — for code session
    hits (id: code:<uuid>:<n>), pass the middle uuid part, not "code".
    Returns an empty string if no conversation with that uuid is found.
    """
    return query.get_conversation(conversation_uuid)


if __name__ == "__main__":
    mcp.run()