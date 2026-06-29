#!/usr/bin/env python3
"""Shared types and chunking utilities used by all ingest scripts."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def run_migrations(conn: sqlite3.Connection, migrations_dir: Path = _MIGRATIONS_DIR) -> None:
    """Apply any unapplied numbered SQL migration files in migrations_dir."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    conn.commit()
    applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
    for path in sorted(migrations_dir.glob("*.sql")):
        version = int(path.stem.split("_")[0])
        if version in applied:
            continue
        conn.executescript(path.read_text())
        conn.execute(
            "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
            (version, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()


WINDOW_CHARS = 1_500
OVERLAP_TURNS = 1
MAX_CHUNK_CHARS = 1_800

# Source identifiers — add new values here when supporting additional AI tools.
SOURCE_CLAUDE_AI = "claude_ai"
SOURCE_CLAUDE_CODE = "claude_code"
SOURCE_WEB = "web"


@dataclass
class Document:
    id: str
    text: str
    source_type: str
    title: str
    timestamp: str
    source: str = SOURCE_CLAUDE_AI
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


def chunk_markdown(text: str, max_chars: int = MAX_CHUNK_CHARS) -> list[tuple[str, str]]:
    """Split markdown into (heading, body) chunks on ## / ### boundaries.

    Returns a list of (section_heading, section_text) pairs. The heading is
    the nearest ## or ### line above the content; top-of-file content before
    any heading uses an empty string heading. Each pair is then subject to
    split_oversized so no chunk exceeds max_chars.
    """
    import re

    heading_re = re.compile(r"^(#{1,3} .+)$", re.MULTILINE)

    chunks: list[tuple[str, str]] = []
    last_heading = ""
    last_end = 0

    for m in heading_re.finditer(text):
        body = text[last_end : m.start()].strip()
        if body:
            for piece in split_oversized(body, max_chars - len(last_heading) - 2):
                chunks.append((last_heading, piece))
        last_heading = m.group(1)
        last_end = m.end()

    # trailing content after the last heading
    body = text[last_end:].strip()
    if body:
        for piece in split_oversized(body, max_chars - len(last_heading) - 2):
            chunks.append((last_heading, piece))

    return chunks
