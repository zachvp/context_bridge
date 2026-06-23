"""
Unit tests for ingest_code_sessions.py pure logic functions.
Bypasses SESSIONS_DIR entirely — only functions that accept data as arguments
are tested here.
"""

import json

import pytest

from ingest_code_sessions import (
    _count_descendants,
    _extract_text,
    parse_session,
    session_to_documents,
    walk_canonical,
)


# ---------------------------------------------------------------------------
# parse_session
# ---------------------------------------------------------------------------

def test_parse_session_basic(minimal_session_jsonl) -> None:
    nodes, adjacency, root_uuid, title, cwd = parse_session(minimal_session_jsonl)
    assert root_uuid == "user-001"
    assert "user-001" in nodes
    assert "asst-001" in nodes
    assert title == "Test Session"
    assert cwd == "/home/user/myproject"


def test_parse_session_empty_file(tmp_path) -> None:
    path = tmp_path / "empty.jsonl"
    path.write_text("")
    nodes, adjacency, root_uuid, title, cwd = parse_session(path)
    assert root_uuid is None
    assert nodes == {}


def test_parse_session_malformed_json_lines(tmp_path) -> None:
    path = tmp_path / "mixed.jsonl"
    lines = [
        json.dumps({
            "type": "user",
            "uuid": "user-001",
            "parentUuid": None,
            "timestamp": "2024-01-01T00:00:00Z",
            "message": {"role": "user", "content": "Hello"},
        }),
        "this is not json {{{",
    ]
    path.write_text("\n".join(lines))
    nodes, adjacency, root_uuid, title, cwd = parse_session(path)
    assert "user-001" in nodes
    assert root_uuid == "user-001"


# ---------------------------------------------------------------------------
# _count_descendants
# ---------------------------------------------------------------------------

def test_count_descendants() -> None:
    adjacency = {"A": ["B"], "B": ["C"]}
    cache: dict = {}
    assert _count_descendants("A", adjacency, cache) == 2
    assert cache["B"] == 1
    assert cache["C"] == 0


def test_count_descendants_leaf() -> None:
    cache: dict = {}
    assert _count_descendants("X", {}, cache) == 0


# ---------------------------------------------------------------------------
# walk_canonical
# ---------------------------------------------------------------------------

def _make_node(uuid: str) -> dict:
    return {"type": "user", "uuid": uuid, "message": {"role": "user", "content": "msg"}, "timestamp": "2024-01-01T00:00:00Z"}


def test_walk_canonical_linear() -> None:
    nodes = {"A": _make_node("A"), "B": _make_node("B"), "C": _make_node("C")}
    adjacency = {"A": ["B"], "B": ["C"]}
    canonical, pruned = walk_canonical(nodes, adjacency, "A")
    assert [n["uuid"] for n in canonical] == ["A", "B", "C"]
    assert pruned == []


def test_walk_canonical_fork_picks_heavier_branch() -> None:
    nodes = {
        "A": _make_node("A"),
        "B": _make_node("B"),
        "C": _make_node("C"),
        "D": _make_node("D"),
        "E": _make_node("E"),
        "F": _make_node("F"),
    }
    # A forks to B (→D→E, 2 descendants) and C (→F, 1 descendant)
    adjacency = {"A": ["B", "C"], "B": ["D"], "D": ["E"], "C": ["F"]}
    canonical, pruned = walk_canonical(nodes, adjacency, "A")
    canonical_uuids = [n["uuid"] for n in canonical]
    assert canonical_uuids[0] == "A"
    assert "B" in canonical_uuids
    assert "C" not in canonical_uuids
    assert len(pruned) == 1
    assert pruned[0]["branch_uuid"] == "C"


# ---------------------------------------------------------------------------
# session_to_documents
# ---------------------------------------------------------------------------

def test_session_to_documents_basic() -> None:
    canonical = [
        {
            "type": "user",
            "uuid": "u1",
            "timestamp": "2024-01-01T00:00:00Z",
            "message": {"role": "user", "content": "Hello Claude"},
        },
        {
            "type": "assistant",
            "uuid": "a1",
            "timestamp": "2024-01-01T00:00:01Z",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "Hello! How can I help?"}],
            },
        },
    ]
    docs = session_to_documents("sess-001", canonical, "Test Session", "myproject")
    assert len(docs) >= 1
    assert docs[0].source == "claude_code"
    assert docs[0].source_type == "code_session"
    assert docs[0].id.startswith("code:")
    assert docs[0].project == "myproject"


def test_session_to_documents_empty_canonical() -> None:
    docs = session_to_documents("sess-002", [], "Empty Session", "myproject")
    assert docs == []


# ---------------------------------------------------------------------------
# _extract_text
# ---------------------------------------------------------------------------

def test_extract_text_string_content() -> None:
    record = {"message": {"content": "plain string"}}
    assert _extract_text(record) == "plain string"


def test_extract_text_list_content() -> None:
    record = {
        "message": {
            "content": [
                {"type": "text", "text": "useful text"},
                {"type": "tool_use", "text": "ignored"},
            ]
        }
    }
    assert _extract_text(record) == "useful text"


def test_extract_text_tool_result_skipped() -> None:
    record = {
        "message": {
            "content": [{"type": "tool_result", "content": "automated feedback"}]
        }
    }
    assert _extract_text(record) == ""
