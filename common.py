#!/usr/bin/env python3
"""Shared types and chunking utilities used by all ingest scripts."""

from dataclasses import dataclass

WINDOW_CHARS = 1_500
OVERLAP_TURNS = 1
MAX_CHUNK_CHARS = 1_800


@dataclass
class Document:
    id: str
    text: str
    source_type: str
    title: str
    timestamp: str
    source: str = "claude_ai"  # override per-source; "claude_ai" is the export default
    project: str | None = None


@dataclass
class Turn:
    sender: str
    text: str
    timestamp: str


def chunk_turns(turns: list[Turn], window_chars: int, overlap_turns: int) -> list[list[Turn]]:
    """Group turns into sliding windows sized by character count.

    A short conversation collapses to a single window naturally — no
    special-casing needed. Overlap carries the last `overlap_turns` of one
    window into the next, so a chunk boundary doesn't sever a question from
    its answer.
    """
    windows: list[list[Turn]] = []
    current: list[Turn] = []
    current_len = 0

    for turn in turns:
        current.append(turn)
        current_len += len(turn.text)
        if current_len >= window_chars:
            windows.append(current)
            current = current[-overlap_turns:] if overlap_turns else []
            current_len = sum(len(t.text) for t in current)

    if current:
        windows.append(current)
    return windows


def split_oversized(body: str, max_chars: int) -> list[str]:
    """A window can still exceed max_chars if a single turn inside it does
    (e.g. one long pasted reply) — the window-level check only fires after
    a turn is already appended. Fall back to fixed-size slicing for those,
    rather than emitting a chunk an embedding model would silently truncate."""
    if len(body) <= max_chars:
        return [body]
    return [body[i : i + max_chars] for i in range(0, len(body), max_chars)]
