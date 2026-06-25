#!/usr/bin/env python3
"""
Full-rebuild pipeline: export dir -> parsed/chunked Documents -> embeddings
-> chat_memory.db. Rebuilds fresh from the export, then merges back any
claude_ai chunks from the previous DB whose owner UUID is absent from the
new export — so a partial export (e.g. 90-day) never silently drops older
history. The result is written atomically so a crash mid-run never leaves a
half-written DB live.

Usage:
    python3 build_db.py [export_dir] [--out chat_memory.db]
"""

import argparse
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from common import Document, SOURCE_CLAUDE_AI
from embed import embed_documents, MODEL_NAME
from ingest import build_documents


def _collect_docs(export_dir: Path) -> list[Document]:
    """Gather documents from all batch export sources.
    To add a new source: import its build function and extend all_docs below."""
    all_docs: list[Document] = []

    docs, count, skipped = build_documents(export_dir)
    print(f"  claude_ai: {count} conversations, {skipped} skipped → {len(docs)} docs")
    all_docs.extend(docs)

    # Add new sources here, e.g.:
    # from ingest_chatgpt import build_documents as build_chatgpt_docs
    # chatgpt_docs = build_chatgpt_docs(Path("~/.chatgpt_export").expanduser())
    # print(f"  chatgpt: {len(chatgpt_docs)} docs")
    # all_docs.extend(chatgpt_docs)

    return all_docs


SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def _merge_surviving(old_db_path: Path, new_conn: sqlite3.Connection, covered_uuids: set[str]) -> int:
    """Copy claude_ai chunks from old_db_path into new_conn if their owner UUID
    is not in covered_uuids (i.e. absent from the new export). Returns count merged.

    Skips the merge entirely if:
      - old_db_path doesn't exist (first run)
      - the old DB used a different embedding model (mixed vectors would corrupt search)
    """
    if not old_db_path.exists():
        return 0

    old_conn = sqlite3.connect(f"file:{old_db_path}?mode=ro", uri=True)
    try:
        row = old_conn.execute("SELECT model_name FROM meta LIMIT 1").fetchone()
        if row is None or row[0] != MODEL_NAME:
            print(
                f"  warning: old DB used model {row[0] if row else '(none)'!r}, "
                f"current is {MODEL_NAME!r} — skipping merge. "
                "Run with a full export to rebuild from scratch.",
                file=sys.stderr,
            )
            return 0

        rows = old_conn.execute(
            "SELECT id, text, source_type, title, timestamp, embedding, source, project "
            "FROM chunks WHERE source = ?",
            (SOURCE_CLAUDE_AI,),
        ).fetchall()
    finally:
        old_conn.close()

    merged = 0
    for row in rows:
        owner = row[0].split(":")[0]
        if owner in covered_uuids:
            continue
        new_conn.execute(
            "INSERT OR IGNORE INTO chunks "
            "(id, text, source_type, title, timestamp, embedding, source, project) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            row,
        )
        merged += 1
    return merged


def write_db(
    docs,
    vectors,
    db_path: Path,
    *,
    old_db_path: Path | None = None,
    covered_uuids: set[str] | None = None,
) -> None:
    """Build the DB at a temp path, then atomically replace db_path — so a
    reader (or a crash) never sees a half-written file.

    If old_db_path is provided, surviving claude_ai chunks (those whose owner
    UUID is absent from covered_uuids) are merged in before the atomic replace."""
    tmp_path = db_path.with_suffix(".tmp")
    tmp_path.unlink(missing_ok=True)

    conn = sqlite3.connect(tmp_path)
    conn.executescript(SCHEMA_PATH.read_text())

    conn.executemany(
        "INSERT INTO chunks (id, text, source_type, title, timestamp, embedding, source, project) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            (
                d.id,
                d.text,
                d.source_type,
                d.title,
                d.timestamp,
                vec.astype("float32").tobytes(),
                d.source,
                d.project,
            )
            for d, vec in zip(docs, vectors)
        ),
    )
    conn.execute(
        "INSERT INTO meta (model_name, dim, built_at) VALUES (?, ?, ?)",
        (MODEL_NAME, vectors.shape[1], datetime.now(timezone.utc).isoformat()),
    )

    if old_db_path is not None and covered_uuids is not None:
        merged = _merge_surviving(old_db_path, conn, covered_uuids)
        if merged:
            print(f"  merged {merged} surviving chunks from previous DB")

    conn.commit()
    conn.close()

    os.replace(tmp_path, db_path)  # atomic on the same filesystem


def main(export_dir: Path, db_path: Path) -> None:
    print(f"export_dir: {export_dir.resolve()}")
    print(f"db_path:    {db_path.resolve()}")
    print("parsing/chunking sources...")
    docs = _collect_docs(export_dir)
    print(f"  total: {len(docs)} docs")

    # Owner UUID is the first colon-delimited segment of every claude_ai chunk ID.
    # "memories" is always re-emitted by a fresh export; don't merge the old one.
    covered_uuids = {d.id.split(":")[0] for d in docs if d.source == SOURCE_CLAUDE_AI}
    covered_uuids.add("memories")

    print(f"embedding {len(docs)} docs with {MODEL_NAME}...")
    start = time.time()
    vectors = embed_documents(docs)
    print(f"  done in {time.time() - start:.1f}s")

    print(f"writing {db_path}...")
    write_db(docs, vectors, db_path, old_db_path=db_path, covered_uuids=covered_uuids)
    print(f"  {db_path} ({db_path.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("export_dir", nargs="?", default=str(Path(__file__).parent / "data" / "inspect"))
    parser.add_argument(
        "--out",
        default=os.environ.get("CONTEXT_BRIDGE_DB_PATH") or str(Path(__file__).parent / "chat_memory.db"),
    )
    args = parser.parse_args()
    main(Path(args.export_dir), Path(args.out))
