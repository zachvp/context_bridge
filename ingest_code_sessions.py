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
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from embed import embed_documents
from common import (
    Document,
    Turn,
    chunk_turns,
    split_oversized,
    WINDOW_CHARS,
    OVERLAP_TURNS,
    MAX_CHUNK_CHARS,
    SOURCE_CLAUDE_CODE,
    run_migrations,
)

DEFAULT_SESSIONS_DIR = Path.home() / ".claude" / "projects"
MTIME_GRACE = 60  # skip files written within the last N seconds (may be mid-write)


def load_ingested(conn: sqlite3.Connection) -> dict[str, float]:
    """Return {session_uuid: file_mtime} for every already-ingested claude_code session."""
    rows = conn.execute(
        "SELECT session_uuid, file_mtime FROM sessions WHERE source = ?", (SOURCE_CLAUDE_CODE,)
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


def _count_descendants(uuid: str, adjacency: dict, cache: dict, _visiting: None = None) -> int:
    # Iterative post-order DFS — avoids recursion limit on deep trees and cycles.
    stack = [uuid]
    order = []
    visited = set()
    while stack:
        node = stack.pop()
        if node in cache or node in visited:
            continue
        visited.add(node)
        order.append(node)
        for child in adjacency.get(node, []):
            if child not in cache and child not in visited:
                stack.append(child)
    for node in reversed(order):
        children = adjacency.get(node, [])
        cache[node] = len(children) + sum(cache.get(c, 0) for c in children)
    return cache.get(uuid, 0)


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
            chunk_id = f"code:{session_uuid}:{i}" if len(pieces) == 1 else f"code:{session_uuid}:{i}.{j}"
            docs.append(
                Document(
                    id=chunk_id,
                    text=f"{title}\n\n{piece}",
                    source_type="code_session",
                    title=title,
                    timestamp=window[-1].timestamp,
                    source=SOURCE_CLAUDE_CODE,
                    project=project,
                )
            )
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
        "INSERT OR REPLACE INTO sessions (session_uuid, source, file_mtime, ingested_at) VALUES (?, ?, ?, ?)",
        (
            session_uuid,
            SOURCE_CLAUDE_CODE,
            file_mtime,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()


def main(db_path: Path, sessions_dir: Path, pruned_out=None) -> None:
    now = time.time()
    conn = sqlite3.connect(db_path)
    run_migrations(conn)
    ingested = load_ingested(conn)

    session_files = list(sessions_dir.glob("*/*.jsonl"))
    print(f"found {len(session_files)} session files")

    new_count = updated_count = 0
    skip_grace = skip_current = skip_problem = skip_innocuous = 0

    for path in session_files:
        mtime = path.stat().st_mtime
        session_uuid = path.stem

        if now - mtime < MTIME_GRACE:
            skip_grace += 1
            continue

        if session_uuid in ingested and ingested[session_uuid] >= mtime:
            skip_current += 1
            continue

        try:
            nodes, adjacency, root_uuid, title, cwd = parse_session(path)
        except OSError as e:
            print(f"  warning: skipping {path.name} — {e}", file=sys.stderr)
            skip_problem += 1
            continue

        if not nodes:
            skip_innocuous += 1
            continue
        if root_uuid is None:
            print(
                f"  warning: skipping {path.name} — no root message found (malformed session)",
                file=sys.stderr,
            )
            skip_problem += 1
            continue

        project = Path(cwd).name if cwd else path.parent.name

        canonical, pruned = walk_canonical(nodes, adjacency, root_uuid)

        if pruned_out is not None:
            for branch in pruned:
                pruned_out.write(json.dumps({"session_uuid": session_uuid, **branch}) + "\n")

        docs = session_to_documents(session_uuid, canonical, title, project)
        if not docs:
            skip_innocuous += 1
            continue

        vectors = embed_documents(docs)
        upsert_session(conn, session_uuid, docs, vectors, mtime)

        is_new = session_uuid not in ingested
        new_count += is_new
        updated_count += not is_new
        print(f"  {'new' if is_new else 'updated'}: {title!r} ({project}) — {len(docs)} chunks")

    conn.close()

    skipped_total = skip_grace + skip_current + skip_problem + skip_innocuous
    skip_parts = [f"{skip_current} already up-to-date"]
    if skip_grace:
        skip_parts.append(f"{skip_grace} too recent (grace period)")
    if skip_innocuous:
        skip_parts.append(f"{skip_innocuous} empty")
    if skip_problem:
        skip_parts.append(f"{skip_problem} malformed/unreadable (see warnings above)")
    print(
        f"\ndone: {new_count} new, {updated_count} updated, {skipped_total} skipped ({'; '.join(skip_parts)})"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--db",
        default=os.environ.get("CONTEXT_BRIDGE_DB_PATH") or str(Path(__file__).parent / "chat_memory.db"),
    )
    parser.add_argument(
        "--sessions-dir",
        default=str(DEFAULT_SESSIONS_DIR),
        help="directory containing session JSONL files (default: ~/.claude/projects)",
    )
    parser.add_argument(
        "--pruned-out",
        default=None,
        help="file to write pruned branch records as JSONL (omit to discard)",
    )
    args = parser.parse_args()

    pruned_out = None
    if args.pruned_out:
        try:
            pruned_out = open(args.pruned_out, "w")
        except OSError as e:
            print(
                f"Error: cannot open --pruned-out {args.pruned_out!r}: {e}",
                file=sys.stderr,
            )
            sys.exit(1)
    try:
        main(Path(args.db), Path(args.sessions_dir).expanduser(), pruned_out)
    finally:
        if pruned_out:
            pruned_out.close()
