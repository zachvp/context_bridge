"""
Unit tests for ingest.py and common.py logic.
No embeddings, no DB, no real export data required.
"""

import pytest

from common import Turn, chunk_turns, split_oversized, WINDOW_CHARS, MAX_CHUNK_CHARS
from ingest import build_documents, conversation_to_documents, extract_text


# ---------------------------------------------------------------------------
# extract_text
# ---------------------------------------------------------------------------

def test_extract_text_top_level() -> None:
    msg = {"text": "Hello world", "content": []}
    assert extract_text(msg) == "Hello world"


def test_extract_text_from_content_blocks() -> None:
    msg = {"text": "", "content": [{"type": "text", "text": "Block text"}]}
    assert extract_text(msg) == "Block text"


def test_extract_text_skips_non_text_blocks() -> None:
    msg = {
        "text": "",
        "content": [
            {"type": "tool_use", "text": "ignored"},
            {"type": "thinking", "text": "also ignored"},
        ],
    }
    assert extract_text(msg) == ""


# ---------------------------------------------------------------------------
# conversation_to_documents
# ---------------------------------------------------------------------------

def test_conversation_to_documents_basic() -> None:
    conv = {
        "uuid": "aaaaaaaa-0000-0000-0000-000000000001",
        "name": "Test Conv",
        "chat_messages": [
            {"sender": "human", "text": "Hello", "created_at": "2024-01-01T00:00:00Z", "content": []},
            {"sender": "assistant", "text": "Hi there", "created_at": "2024-01-01T00:00:01Z", "content": []},
        ],
    }
    docs = conversation_to_documents(conv)
    assert len(docs) >= 1
    assert docs[0].source_type == "conversation"
    assert docs[0].source == "claude_ai"
    assert docs[0].id.startswith("aaaaaaaa-0000-0000-0000-000000000001:")


def test_conversation_to_documents_empty_turns() -> None:
    conv = {
        "uuid": "aaaaaaaa-0000-0000-0000-000000000002",
        "name": "Empty",
        "chat_messages": [
            {"sender": "human", "text": "", "created_at": "2024-01-01T00:00:00Z", "content": []},
        ],
    }
    assert conversation_to_documents(conv) == []


def test_conversation_to_documents_chunk_ids() -> None:
    long_text = "x" * (WINDOW_CHARS + 100)
    conv = {
        "uuid": "bbbbbbbb-0000-0000-0000-000000000001",
        "name": "Long Conv",
        "chat_messages": [
            {"sender": "human", "text": long_text, "created_at": "2024-01-01T00:00:00Z", "content": []},
            {"sender": "assistant", "text": long_text, "created_at": "2024-01-01T00:00:01Z", "content": []},
            {"sender": "human", "text": long_text, "created_at": "2024-01-01T00:00:02Z", "content": []},
        ],
    }
    docs = conversation_to_documents(conv)
    assert len(docs) > 1
    ids = [d.id for d in docs]
    assert len(ids) == len(set(ids)), "all chunk IDs must be unique"


# ---------------------------------------------------------------------------
# build_documents
# ---------------------------------------------------------------------------

def test_build_documents_missing_conversations_json(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        build_documents(tmp_path)


def test_build_documents_minimal_export(minimal_export) -> None:
    docs, conversation_count, skipped = build_documents(minimal_export)
    assert len(docs) > 0
    assert conversation_count == 1
    ids = [d.id for d in docs]
    assert len(ids) == len(set(ids)), "all doc IDs must be unique"
    assert any(d.id == "memories" for d in docs)


def test_build_documents_project_docs(tmp_path, minimal_export) -> None:
    import json

    project = {
        "uuid": "proj-0001-0000-0000-000000000001",
        "name": "TestProject",
        "updated_at": "2024-01-01T00:00:00Z",
        "docs": [
            {
                "uuid": "doc-0001-0000-0000-000000000001",
                "filename": "README.md",
                "content": "This is a project doc with some content.",
            }
        ],
    }
    (minimal_export / "projects" / "testproject.json").write_text(json.dumps(project))

    docs, _, _ = build_documents(minimal_export)
    project_docs = [d for d in docs if d.source_type == "project_doc"]
    assert len(project_docs) == 1
    assert project_docs[0].title == "TestProject/README.md"


# ---------------------------------------------------------------------------
# chunk_turns
# ---------------------------------------------------------------------------

def test_chunk_turns_single_window() -> None:
    turns = [
        Turn(sender="human", text="Hi", timestamp="2024-01-01T00:00:00Z"),
        Turn(sender="assistant", text="Hello", timestamp="2024-01-01T00:00:01Z"),
    ]
    windows = chunk_turns(turns, WINDOW_CHARS, overlap_turns=1)
    assert len(windows) == 1
    assert len(windows[0]) == 2


def test_chunk_turns_multiple_windows() -> None:
    long_text = "x" * (WINDOW_CHARS + 100)
    turns = [
        Turn(sender="human", text=long_text, timestamp="2024-01-01T00:00:00Z"),
        Turn(sender="assistant", text=long_text, timestamp="2024-01-01T00:00:01Z"),
        Turn(sender="human", text=long_text, timestamp="2024-01-01T00:00:02Z"),
    ]
    windows = chunk_turns(turns, WINDOW_CHARS, overlap_turns=1)
    assert len(windows) > 1
    # Last turn of window N appears as first turn of window N+1 (overlap)
    assert windows[0][-1] == windows[1][0]


# ---------------------------------------------------------------------------
# split_oversized
# ---------------------------------------------------------------------------

def test_split_oversized_within_limit() -> None:
    body = "short string"
    result = split_oversized(body, MAX_CHUNK_CHARS)
    assert result == [body]


def test_split_oversized_exceeds_limit() -> None:
    body = "x" * (MAX_CHUNK_CHARS * 3)
    result = split_oversized(body, MAX_CHUNK_CHARS)
    assert len(result) == 3
    assert all(len(piece) <= MAX_CHUNK_CHARS for piece in result)
    assert "".join(result) == body
