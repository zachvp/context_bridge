#!/usr/bin/env python3
"""
Unit tests for build_db merge logic. No real embedding model required —
vectors are synthetic float32 blobs. Tests run against temp SQLite files.
"""

import sqlite3
from pathlib import Path

import numpy as np

from build_db import write_db
from common import Document

DIM = 4
CURRENT_MODEL = "BAAI/bge-base-en-v1.5"


def _chunk_ids(db_path: Path) -> set[str]:
    conn = sqlite3.connect(db_path)
    ids = {row[0] for row in conn.execute("SELECT id FROM chunks")}
    conn.close()
    return ids


def test_partial_export_merge(tmp_path, make_db, fake_vec) -> None:
    """Chunks for UUIDs absent from the new export survive in the output DB."""
    uuid_a = "aaaaaaaa-0000-0000-0000-000000000000"
    uuid_b = "bbbbbbbb-0000-0000-0000-000000000000"

    old_rows = [
        (f"{uuid_a}:0", "text A", "conversation", "Conv A", "2024-01-01", fake_vec(), "claude_ai", None),
        (f"{uuid_b}:0", "text B", "conversation", "Conv B", "2024-01-02", fake_vec(), "claude_ai", None),
    ]

    old_db = tmp_path / "old.db"
    new_db = tmp_path / "new.db"
    make_db(old_db, CURRENT_MODEL, old_rows)

    new_docs = [Document(id=f"{uuid_b}:0", text="text B v2", source_type="conversation",
                         title="Conv B", timestamp="2024-01-02", source="claude_ai")]
    new_vecs = np.zeros((1, DIM), dtype="float32")
    covered = {uuid_b, "memories"}

    write_db(new_docs, new_vecs, new_db, old_db_path=old_db, covered_uuids=covered)

    ids = _chunk_ids(new_db)
    assert f"{uuid_a}:0" in ids, "chunk A should survive (absent from new export)"
    assert f"{uuid_b}:0" in ids, "chunk B should be present (from new export)"


def test_full_export_no_merge(tmp_path, make_db, fake_vec) -> None:
    """When all old UUIDs are covered by the new export, nothing is merged."""
    uuid_a = "aaaaaaaa-0000-0000-0000-000000000000"

    old_rows = [
        (f"{uuid_a}:0", "old text", "conversation", "Conv A", "2024-01-01", fake_vec(), "claude_ai", None),
    ]

    old_db = tmp_path / "old.db"
    new_db = tmp_path / "new.db"
    make_db(old_db, CURRENT_MODEL, old_rows)

    new_docs = [Document(id=f"{uuid_a}:0", text="new text", source_type="conversation",
                         title="Conv A", timestamp="2024-01-01", source="claude_ai")]
    new_vecs = np.zeros((1, DIM), dtype="float32")
    covered = {uuid_a, "memories"}

    write_db(new_docs, new_vecs, new_db, old_db_path=old_db, covered_uuids=covered)

    conn = sqlite3.connect(new_db)
    text = conn.execute("SELECT text FROM chunks WHERE id = ?", (f"{uuid_a}:0",)).fetchone()[0]
    conn.close()
    assert text == "new text", "new export's version should win, not the merged old one"


def test_model_mismatch_skips_merge(tmp_path, make_db, fake_vec) -> None:
    """If the old DB used a different embedding model, merge is skipped entirely."""
    uuid_a = "aaaaaaaa-0000-0000-0000-000000000000"
    uuid_b = "bbbbbbbb-0000-0000-0000-000000000000"

    old_rows = [
        (f"{uuid_a}:0", "text A", "conversation", "Conv A", "2024-01-01", fake_vec(), "claude_ai", None),
    ]

    old_db = tmp_path / "old.db"
    new_db = tmp_path / "new.db"
    make_db(old_db, "some-other-model/v1", old_rows)

    new_docs = [Document(id=f"{uuid_b}:0", text="text B", source_type="conversation",
                         title="Conv B", timestamp="2024-01-02", source="claude_ai")]
    new_vecs = np.zeros((1, DIM), dtype="float32")
    covered = {uuid_b, "memories"}

    write_db(new_docs, new_vecs, new_db, old_db_path=old_db, covered_uuids=covered)

    ids = _chunk_ids(new_db)
    assert f"{uuid_a}:0" not in ids, "old chunk should NOT be merged when model differs"
    assert f"{uuid_b}:0" in ids, "new chunk should be present regardless"


def test_first_run_no_old_db(tmp_path, fake_vec) -> None:
    """No old DB on disk → behaves identically to current behavior, no error."""
    uuid_a = "aaaaaaaa-0000-0000-0000-000000000000"

    nonexistent = tmp_path / "does_not_exist.db"
    new_db = tmp_path / "new.db"

    new_docs = [Document(id=f"{uuid_a}:0", text="text A", source_type="conversation",
                         title="Conv A", timestamp="2024-01-01", source="claude_ai")]
    new_vecs = np.zeros((1, DIM), dtype="float32")
    covered = {uuid_a, "memories"}

    write_db(new_docs, new_vecs, new_db, old_db_path=nonexistent, covered_uuids=covered)

    ids = _chunk_ids(new_db)
    assert f"{uuid_a}:0" in ids


def test_code_session_chunks_not_merged(tmp_path, make_db, fake_vec) -> None:
    """claude_code chunks in the old DB are never touched by the claude_ai merge."""
    code_uuid = "cccccccc-0000-0000-0000-000000000000"
    ai_uuid = "aaaaaaaa-0000-0000-0000-000000000000"

    old_rows = [
        (f"code:{code_uuid}:0", "code text", "code_session", "Code Session", "2024-01-01",
         fake_vec(), "claude_code", "myproject"),
        (f"{ai_uuid}:0", "ai text", "conversation", "Conv A", "2024-01-01",
         fake_vec(), "claude_ai", None),
    ]

    old_db = tmp_path / "old.db"
    new_db = tmp_path / "new.db"
    make_db(old_db, CURRENT_MODEL, old_rows)

    new_docs = [Document(id="memories", text="mem", source_type="memory",
                         title="Working memory summary", timestamp="2024-01-01", source="claude_ai")]
    new_vecs = np.zeros((1, DIM), dtype="float32")
    covered = {"memories"}

    write_db(new_docs, new_vecs, new_db, old_db_path=old_db, covered_uuids=covered)

    ids = _chunk_ids(new_db)
    assert f"{ai_uuid}:0" in ids, "orphaned claude_ai chunk should be merged"
    assert f"code:{code_uuid}:0" not in ids, "claude_code chunk must not be merged by build_db"
