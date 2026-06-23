#!/usr/bin/env python3
"""
Full-rebuild pipeline: export dir -> parsed/chunked Documents -> embeddings
-> chat_memory.db. Always rebuilds from scratch (see PLAN.md's "full rebuild,
not incremental upsert" model) — every run is the same whether it's the
first or the fiftieth, and the result is written atomically so a crash
mid-run never leaves a half-written DB live.

Usage:
    python3 build_db.py [export_dir] [--out chat_memory.db]
"""

import argparse
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from common import Document
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


def write_db(docs, vectors, db_path: Path) -> None:
    """Build the DB at a temp path, then atomically replace db_path — so a
    reader (or a crash) never sees a half-written file."""
    tmp_path = db_path.with_suffix(".tmp")
    tmp_path.unlink(missing_ok=True)

    conn = sqlite3.connect(tmp_path)
    conn.executescript(SCHEMA_PATH.read_text())

    conn.executemany(
        "INSERT INTO chunks (id, text, source_type, title, timestamp, embedding, source, project) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            (d.id, d.text, d.source_type, d.title, d.timestamp, vec.astype("float32").tobytes(), d.source, d.project)
            for d, vec in zip(docs, vectors)
        ),
    )
    conn.execute(
        "INSERT INTO meta (model_name, dim, built_at) VALUES (?, ?, ?)",
        (MODEL_NAME, vectors.shape[1], datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()

    os.replace(tmp_path, db_path)  # atomic on the same filesystem


def main(export_dir: Path, db_path: Path) -> None:
    print("parsing/chunking sources...")
    docs = _collect_docs(export_dir)
    print(f"  total: {len(docs)} docs")

    print(f"embedding {len(docs)} docs with {MODEL_NAME}...")
    start = time.time()
    vectors = embed_documents(docs)
    print(f"  done in {time.time() - start:.1f}s")

    print(f"writing {db_path}...")
    write_db(docs, vectors, db_path)
    print(f"  {db_path} ({db_path.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("export_dir", nargs="?", default=str(Path(__file__).parent / "data" / "inspect"))
    parser.add_argument("--out", default=os.environ.get("CONTEXT_BRIDGE_DB_PATH") or str(Path(__file__).parent / "chat_memory.db"))
    args = parser.parse_args()
    main(Path(args.export_dir), Path(args.out))
