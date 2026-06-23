#!/usr/bin/env python3
"""
Incremental ingest of Claude Code session transcripts into chat_memory.db.

Scans ~/.claude/projects/**/*.jsonl, extracts the canonical conversation path
from each session's message tree, chunks/embeds, and upserts into the shared
chat_memory.db alongside Claude.ai data.

Usage:
    python3 ingest_code_sessions.py [--db chat_memory.db] [--pruned-out FILE]

--pruned-out: path to write discarded branch records as JSONL (omit to discard).
"""

import argparse
import json
import os
import sqlite3
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from embed import embed_documents
from common import Document, Turn, chunk_turns, split_oversized

SESSIONS_DIR = Path.home() / ".claude" / "projects"
WINDOW_CHARS = 2_500
OVERLAP_TURNS = 1
MAX_CHUNK_CHARS = 5_000
MTIME_GRACE = 60  # skip files written within the last N seconds (may be mid-write)

MIGRATE_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    session_uuid  TEXT NOT NULL,
    source        TEXT NOT NULL DEFAULT 'claude_code',
    file_mtime    REAL NOT NULL,
    ingested_at   TEXT NOT NULL,
    PRIMARY KEY (session_uuid, source)
);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Add columns/tables absent from older chat_memory.db builds."""
    conn.executescript(MIGRATE_SQL)
    existing = {row[1] for row in conn.execute("PRAGMA table_info(chunks)")}
    if "source" not in existing:
        conn.execute("ALTER TABLE chunks ADD COLUMN source TEXT NOT NULL DEFAULT 'claude_ai'")
    if "project" not in existing:
        conn.execute("ALTER TABLE chunks ADD COLUMN project TEXT")
    # Migrate pre-source sessions tables (single-column PK → add discriminator column)
    session_cols = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
    if "source" not in session_cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN source TEXT NOT NULL DEFAULT 'claude_code'")
    conn.commit()


def load_ingested(conn: sqlite3.Connection) -> dict[str, float]:
    """Return {session_uuid: file_mtime} for every already-ingested claude_code session."""
    rows = conn.execute(
        "SELECT session_uuid, file_mtime FROM sessions WHERE source = 'claude_code'"
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def parse_session(path: Path) -> tuple[dict, dict, str | None, str, str | None]:
    """Parse a .jsonl session file into the structures needed for the tree walk.

    Returns:
        nodes      — {uuid: record} for every record that has a uuid
        adjacency  — {parent_uuid: [child_uuid, ...]}
        root_uuid  — uuid of the record whose parentUuid is null
        title      — from ai-title record, or "(untitled)"
        cwd        — working directory from first user record, or None
    """
    nodes: dict = {}
    adjacency: dict = defaultdict(list)
    root_uuid: str | None = None
    title = "(untitled)"
    cwd: str | None = None

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue

        record_type = record.get("type")

        if record_type == "ai-title":
            title = record.get("aiTitle", title)
            continue

        uuid = record.get("uuid")
        if not uuid:
            continue

        if record_type == "user" and cwd is None:
            cwd = record.get("cwd")

        nodes[uuid] = record
        parent = record.get("parentUuid")
        if parent is None:
            root_uuid = uuid
        else:
            adjacency[parent].append(uuid)

    return nodes, dict(adjacency), root_uuid, title, cwd


def _count_descendants(uuid: str, adjacency: dict, cache: dict) -> int:
    if uuid in cache:
        return cache[uuid]
    children = adjacency.get(uuid, [])
    total = len(children) + sum(_count_descendants(c, adjacency, cache) for c in children)
    cache[uuid] = total
    return total


def walk_canonical(
    nodes: dict,
    adjacency: dict,
    root_uuid: str,
) -> tuple[list[dict], list[dict]]:
    """Walk the message tree depth-first, always following the heaviest branch.

    At each fork, the child with the most descendants is the branch that was
    actually continued — the others are abandoned (edited/regenerated) turns.

    Returns:
        canonical — records in conversation order
        pruned    — [{fork_uuid, branch_uuid}, ...] for every discarded branch
    """
    canonical: list[dict] = []
    pruned: list[dict] = []
    cache: dict = {}
    current: str | None = root_uuid

    while current:
        if current not in nodes:
            break
        canonical.append(nodes[current])
        children = adjacency.get(current, [])
        if not children:
            break
        if len(children) == 1:
            current = children[0]
        else:
            best = max(children, key=lambda c: _count_descendants(c, adjacency, cache))
            for child in children:
                if child != best:
                    pruned.append({"fork_uuid": current, "branch_uuid": child})
            current = best

    return canonical, pruned


def _extract_text(record: dict) -> str:
    """Pull usable text out of a user or assistant record.

    User records: content is a plain string (typed message) or an array of
    tool_result blocks (automated feed-back — skip entirely).

    Assistant records: content is always an array; take only type=="text" blocks,
    skipping thinking and tool_use.
    """
    msg = record.get("message", {})
    content = msg.get("content", "")

    if isinstance(content, str):
        return content.strip()

    parts = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            text = block.get("text", "").strip()
            if text:
                parts.append(text)
    return "\n".join(parts)


def session_to_documents(
    session_uuid: str,
    canonical: list[dict],
    title: str,
    project: str,
) -> list[Document]:
    turns: list[Turn] = []
    for record in canonical:
        if record.get("type") not in ("user", "assistant"):
            continue
        text = _extract_text(record)
        if not text:
            continue
        sender = record.get("message", {}).get("role", record["type"])
        turns.append(Turn(sender=sender, text=text, timestamp=record.get("timestamp", "")))

    if not turns:
        return []

    title_overhead = len(title) + 2
    windows = chunk_turns(turns, WINDOW_CHARS, OVERLAP_TURNS)
    docs: list[Document] = []
    for i, window in enumerate(windows):
        body = "\n".join(f"{t.sender}: {t.text}" for t in window)
        pieces = split_oversized(body, MAX_CHUNK_CHARS - title_overhead)
        for j, piece in enumerate(pieces):
            chunk_id = (
                f"code:{session_uuid}:{i}"
                if len(pieces) == 1
                else f"code:{session_uuid}:{i}.{j}"
            )
            docs.append(Document(
                id=chunk_id,
                text=f"{title}\n\n{piece}",
                source_type="code_session",
                title=title,
                timestamp=window[-1].timestamp,
                source="claude_code",
                project=project,
            ))
    return docs


def upsert_session(
    conn: sqlite3.Connection,
    session_uuid: str,
    docs: list[Document],
    vectors: np.ndarray,
    file_mtime: float,
) -> None:
    conn.execute("DELETE FROM chunks WHERE id LIKE ?", (f"code:{session_uuid}:%",))
    conn.executemany(
        "INSERT OR REPLACE INTO chunks "
        "(id, text, source_type, title, timestamp, embedding, source, project) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            (
                d.id, d.text, d.source_type, d.title, d.timestamp,
                vec.astype("float32").tobytes(), d.source, d.project,
            )
            for d, vec in zip(docs, vectors)
        ),
    )
    conn.execute(
        "INSERT OR REPLACE INTO sessions (session_uuid, source, file_mtime, ingested_at) VALUES (?, ?, ?, ?)",
        (session_uuid, "claude_code", file_mtime, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


def main(db_path: Path, pruned_out=None) -> None:
    now = time.time()
    conn = sqlite3.connect(db_path)
    ensure_schema(conn)
    ingested = load_ingested(conn)

    session_files = list(SESSIONS_DIR.glob("*/*.jsonl"))
    print(f"found {len(session_files)} session files")

    new_count = updated_count = skipped_count = 0

    for path in session_files:
        mtime = path.stat().st_mtime
        session_uuid = path.stem

        if now - mtime < MTIME_GRACE:
            skipped_count += 1
            continue

        if session_uuid in ingested and ingested[session_uuid] >= mtime:
            skipped_count += 1
            continue

        nodes, adjacency, root_uuid, title, cwd = parse_session(path)
        if root_uuid is None or not nodes:
            skipped_count += 1
            continue

        project = Path(cwd).name if cwd else path.parent.name

        canonical, pruned = walk_canonical(nodes, adjacency, root_uuid)

        if pruned_out is not None:
            for branch in pruned:
                pruned_out.write(json.dumps({"session_uuid": session_uuid, **branch}) + "\n")

        docs = session_to_documents(session_uuid, canonical, title, project)
        if not docs:
            skipped_count += 1
            continue

        vectors = embed_documents(docs)
        upsert_session(conn, session_uuid, docs, vectors, mtime)

        is_new = session_uuid not in ingested
        new_count += is_new
        updated_count += not is_new
        print(f"  {'new' if is_new else 'updated'}: {title!r} ({project}) — {len(docs)} chunks")

    conn.close()
    print(f"\ndone: {new_count} new, {updated_count} updated, {skipped_count} skipped")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=os.environ.get("CONTEXT_BRIDGE_DB_PATH") or str(Path(__file__).parent / "chat_memory.db"))
    parser.add_argument(
        "--pruned-out",
        default=None,
        help="file to write pruned branch records as JSONL (omit to discard)",
    )
    args = parser.parse_args()

    pruned_out = open(args.pruned_out, "w") if args.pruned_out else None
    try:
        main(Path(args.db), pruned_out)
    finally:
        if pruned_out:
            pruned_out.close()
