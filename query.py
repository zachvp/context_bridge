#!/usr/bin/env python3
"""
Retrieval, standalone — no MCP involved. This is the layer that gets
quality-tested (beat 4) before any protocol plumbing wraps it (beat 5+).

Usage:
    python3 query.py "some search text" [--top-k 5] [--db chat_memory.db]
"""

import argparse
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from embed import embed_query

DB_PATH = Path(os.environ.get("CONTEXT_BRIDGE_DB_PATH") or (Path(__file__).parent / "chat_memory.db"))


@dataclass
class Hit:
    id: str
    title: str
    text: str
    source_type: str
    timestamp: str
    score: float


def _load(db_path: Path):
    """Load every chunk + its embedding into memory. At this corpus size
    (~5k rows, 768-dim) that's a few tens of MB — cheap enough to just do a
    brute-force scan at query time rather than maintain an index."""
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT id, text, source_type, title, timestamp, embedding FROM chunks").fetchall()
    conn.close()

    ids = [r[0] for r in rows]
    texts = [r[1] for r in rows]
    source_types = [r[2] for r in rows]
    titles = [r[3] for r in rows]
    timestamps = [r[4] for r in rows]
    vectors = (
        np.stack([np.frombuffer(r[5], dtype="float32") for r in rows])
        if rows
        else np.empty((0, 0), dtype="float32")
    )
    return ids, texts, source_types, titles, timestamps, vectors


def search(query_text: str, top_k: int = 5, db_path: Path = DB_PATH) -> list[Hit]:
    ids, texts, source_types, titles, timestamps, vectors = _load(db_path)

    if not ids:
        return []

    query_vec = embed_query(query_text)
    # vectors and query_vec are both unit-normalized -> dot product == cosine similarity
    scores = vectors @ query_vec

    top_idx = np.argsort(-scores)[:top_k]
    return [
        Hit(
            id=ids[i],
            title=titles[i],
            text=texts[i],
            source_type=source_types[i],
            timestamp=timestamps[i],
            score=float(scores[i]),
        )
        for i in top_idx
    ]


def _chunk_sort_key(chunk_id: str) -> tuple[int, int]:
    # ids look like "<uuid>:12" or "<uuid>:12.3" (split piece from an
    # oversized turn) — sort by (window_index, split_index).
    idx_part = chunk_id.rsplit(":", 1)[1]
    major, _, minor = idx_part.partition(".")
    return (int(major), int(minor) if minor else 0)


def _uuid_from_chunk_id(chunk_id: str) -> str:
    """Extract conversation uuid from any chunk id format.
    - Regular:      <uuid>:<n>           → uuid is left of first ":"
    - Code session: code:<uuid>:<n>      → uuid is middle segment
    """
    parts = chunk_id.split(":")
    return parts[1] if parts[0] == "code" else parts[0]


def get_nearby_context(chunk_id: str, num_chunks: int = 2, db_path: Path = DB_PATH) -> str:
    """Return the chunk matching chunk_id plus num_chunks before and after it.
    Use this instead of get_conversation when you only need local context around
    a search hit — avoids loading the full (potentially very large) conversation."""
    conv_uuid = _uuid_from_chunk_id(chunk_id)

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT id, text FROM chunks WHERE id LIKE ? OR id LIKE ?",
        (f"{conv_uuid}:%", f"%:{conv_uuid}:%"),
    ).fetchall()
    conn.close()

    if not rows:
        return ""

    rows.sort(key=lambda r: _chunk_sort_key(r[0]))

    target_idx = next((i for i, (rid, _) in enumerate(rows) if rid == chunk_id), 0)
    start = max(0, target_idx - num_chunks)
    end = min(len(rows), target_idx + num_chunks + 1)
    window = rows[start:end]

    title, _, _ = rows[0][1].partition("\n\n")
    bodies = [text.partition("\n\n")[2] for _, text in window]
    header = f"{title}\n\n[chunks {start}–{end - 1} of {len(rows)}]\n\n"
    return header + "\n".join(bodies)


def get_conversation(conversation_uuid: str, db_path: Path = DB_PATH) -> str:
    """Reconstruct a conversation's full text by concatenating its chunks in
    order. Pulled from the DB rather than the raw archive, so this works even
    if the original export has since been deleted.

    Handles both regular conversations (id: <uuid>:<n>) and code sessions
    (id: code:<uuid>:<n>) — pass just the uuid in both cases."""
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT id, text FROM chunks WHERE id LIKE ? OR id LIKE ?",
        (f"{conversation_uuid}:%", f"%:{conversation_uuid}:%"),
    ).fetchall()
    conn.close()

    if not rows:
        return ""
    rows.sort(key=lambda r: _chunk_sort_key(r[0]))
    # each chunk's text is "{title}\n\n{body}" — drop the repeated title after the first
    title, _, first_body = rows[0][1].partition("\n\n")
    bodies = [first_body] + [text.partition("\n\n")[2] for _, text in rows[1:]]
    return f"{title}\n\n" + "\n".join(bodies)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("query")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--db", default=str(DB_PATH))
    args = parser.parse_args()

    hits = search(args.query, top_k=args.top_k, db_path=Path(args.db))
    for h in hits:
        preview = h.text[:200].replace("\n", " ")
        print(f"\n[{h.score:.3f}] [{h.source_type}] {h.title} ({h.timestamp})")
        print(f"  {preview}...")


if __name__ == "__main__":
    main()
