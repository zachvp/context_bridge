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


# --- _uuid_from_chunk_id ---


def test_uuid_from_chunk_id_regular() -> None:
    uuid = "aaaaaaaa-0000-0000-0000-000000000001"
    assert query._uuid_from_chunk_id(f"{uuid}:3") == uuid


def test_uuid_from_chunk_id_code_session() -> None:
    uuid = "cccccccc-0000-0000-0000-000000000001"
    assert query._uuid_from_chunk_id(f"code:{uuid}:5") == uuid


def test_uuid_from_chunk_id_split_piece() -> None:
    uuid = "aaaaaaaa-0000-0000-0000-000000000001"
    assert query._uuid_from_chunk_id(f"{uuid}:2.1") == uuid


# --- get_nearby_context ---


def _make_conv_db(tmp_path: Path, num_chunks: int, uuid: str = UUID) -> Path:
    """Build a DB with `num_chunks` sequential chunks for a single conversation."""
    db = tmp_path / "chat.db"
    rows = [
        (
            f"{uuid}:{i}",
            f"Title\n\nBody of chunk {i}.",
            "conversation",
            "Title",
            "2024-01-01",
            b"",
            "claude_ai",
            None,
        )
        for i in range(num_chunks)
    ]
    _make_query_db(db, rows)
    return db


def test_get_nearby_context_unknown_chunk(tmp_path: Path) -> None:
    db = _make_conv_db(tmp_path, num_chunks=3)
    result = query.get_nearby_context("nonexistent-uuid:0", db_path=db)
    assert result == ""


def test_get_nearby_context_single_chunk_conversation(tmp_path: Path) -> None:
    db = _make_conv_db(tmp_path, num_chunks=1)
    result = query.get_nearby_context(f"{UUID}:0", num_chunks=2, db_path=db)
    assert "Body of chunk 0." in result
    assert "[chunks 0–0 of 1]" in result


def test_get_nearby_context_middle_of_conversation(tmp_path: Path) -> None:
    db = _make_conv_db(tmp_path, num_chunks=5)
    result = query.get_nearby_context(f"{UUID}:2", num_chunks=1, db_path=db)
    assert "Body of chunk 1." in result
    assert "Body of chunk 2." in result
    assert "Body of chunk 3." in result
    assert "Body of chunk 0." not in result
    assert "Body of chunk 4." not in result
    assert "[chunks 1–3 of 5]" in result


def test_get_nearby_context_clips_at_start(tmp_path: Path) -> None:
    db = _make_conv_db(tmp_path, num_chunks=5)
    result = query.get_nearby_context(f"{UUID}:0", num_chunks=2, db_path=db)
    assert "Body of chunk 0." in result
    assert "Body of chunk 1." in result
    assert "Body of chunk 2." in result
    assert "[chunks 0–2 of 5]" in result


def test_get_nearby_context_clips_at_end(tmp_path: Path) -> None:
    db = _make_conv_db(tmp_path, num_chunks=5)
    result = query.get_nearby_context(f"{UUID}:4", num_chunks=2, db_path=db)
    assert "Body of chunk 2." in result
    assert "Body of chunk 3." in result
    assert "Body of chunk 4." in result
    assert "[chunks 2–4 of 5]" in result


def test_get_nearby_context_title_appears_once(tmp_path: Path) -> None:
    db = _make_conv_db(tmp_path, num_chunks=5)
    result = query.get_nearby_context(f"{UUID}:2", num_chunks=2, db_path=db)
    assert result.count("Title\n\n") == 1


def test_get_nearby_context_code_session(tmp_path: Path) -> None:
    code_uuid = "cccccccc-0000-0000-0000-000000000001"
    db = tmp_path / "chat.db"
    rows = [
        (
            f"code:{code_uuid}:{i}",
            f"Session\n\nCode chunk {i}.",
            "code_session",
            "Session",
            "2024-01-01",
            b"",
            "claude_code",
            "myproject",
        )
        for i in range(3)
    ]
    _make_query_db(db, rows)
    result = query.get_nearby_context(f"code:{code_uuid}:1", num_chunks=1, db_path=db)
    assert "Code chunk 0." in result
    assert "Code chunk 1." in result
    assert "Code chunk 2." in result
