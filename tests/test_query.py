#!/usr/bin/env python3
"""
Unit tests for query.py — chunk sorting and conversation reconstruction.
No embedding model or real DB required: uses synthetic SQLite fixtures.
"""

import sqlite3
from pathlib import Path


import query


# --- _chunk_sort_key ---


def test_chunk_sort_key_simple() -> None:
    assert query._chunk_sort_key("uuid:0") == (0, 0)
    assert query._chunk_sort_key("uuid:3") == (3, 0)


def test_chunk_sort_key_split_piece() -> None:
    assert query._chunk_sort_key("uuid:2.1") == (2, 1)
    assert query._chunk_sort_key("uuid:2.3") == (2, 3)


def test_chunk_sort_key_ordering() -> None:
    ids = ["uuid:2.1", "uuid:0", "uuid:1", "uuid:2.0"]
    sorted_ids = sorted(ids, key=query._chunk_sort_key)
    assert sorted_ids == ["uuid:0", "uuid:1", "uuid:2.0", "uuid:2.1"]


def test_chunk_sort_key_code_session() -> None:
    # code session ids look like "code:<uuid>:<n>" — sort key uses the last segment
    assert query._chunk_sort_key("code:some-uuid:0") == (0, 0)
    assert query._chunk_sort_key("code:some-uuid:5") == (5, 0)


# --- get_conversation ---


def _make_query_db(path: Path, rows: list[tuple]) -> None:
    """Create a minimal chunks table with the columns query.py reads."""
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE chunks (
            id TEXT PRIMARY KEY,
            text TEXT,
            source_type TEXT,
            title TEXT,
            timestamp TEXT,
            embedding BLOB,
            source TEXT,
            project TEXT
        )
    """)
    conn.executemany(
        "INSERT INTO chunks (id, text, source_type, title, timestamp, embedding, source, project) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()


UUID = "aaaaaaaa-0000-0000-0000-000000000001"


def test_get_conversation_returns_empty_for_unknown(tmp_path: Path) -> None:
    db = tmp_path / "chat.db"
    _make_query_db(db, [])
    result = query.get_conversation("nonexistent-uuid", db_path=db)
    assert result == ""


def test_get_conversation_single_chunk(tmp_path: Path) -> None:
    db = tmp_path / "chat.db"
    _make_query_db(
        db,
        [
            (
                f"{UUID}:0",
                "My Title\n\nBody text here.",
                "conversation",
                "My Title",
                "2024-01-01",
                b"",
                "claude_ai",
                None,
            ),
        ],
    )
    result = query.get_conversation(UUID, db_path=db)
    assert result.startswith("My Title\n\n")
    assert "Body text here." in result


def test_get_conversation_multiple_chunks_ordered(tmp_path: Path) -> None:
    db = tmp_path / "chat.db"
    _make_query_db(
        db,
        [
            (
                f"{UUID}:1",
                "Title\n\nChunk one.",
                "conversation",
                "Title",
                "2024-01-01",
                b"",
                "claude_ai",
                None,
            ),
            (
                f"{UUID}:0",
                "Title\n\nFirst chunk.",
                "conversation",
                "Title",
                "2024-01-01",
                b"",
                "claude_ai",
                None,
            ),
        ],
    )
    result = query.get_conversation(UUID, db_path=db)
    # First chunk body must appear before second
    assert result.index("First chunk.") < result.index("Chunk one.")
    # Title appears only once at the top
    assert result.count("Title\n\n") == 1


def test_get_conversation_code_session(tmp_path: Path) -> None:
    db = tmp_path / "chat.db"
    code_uuid = "cccccccc-0000-0000-0000-000000000001"
    _make_query_db(
        db,
        [
            (
                f"code:{code_uuid}:0",
                "Session\n\nCode body.",
                "code_session",
                "Session",
                "2024-01-01",
                b"",
                "claude_code",
                "myproject",
            ),
        ],
    )
    result = query.get_conversation(code_uuid, db_path=db)
    assert "Code body." in result
