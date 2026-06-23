#!/usr/bin/env python3
"""
Minimal ingest pass: parse a Claude.ai export into a flat list of Documents
and report on what came out. No embeddings, no DB yet — just enough to look
at the output and confirm the parsing/chunking is sane.

Usage:
    python3 ingest.py [export_dir]

export_dir defaults to ./data/inspect (the already-unzipped export next to this
script).
"""

import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

from common import Document, Turn, chunk_turns, split_oversized

WINDOW_CHARS = 2_500
OVERLAP_TURNS = 1
MAX_CHUNK_CHARS = 5_000


def extract_text(message: dict) -> str:
    """Pull usable text out of a chat message.

    Most messages have it at the top-level `text` field. Some (tool_use,
    thinking-only, attachment-only turns) have an empty top-level `text` but
    real content in `content[]` blocks of type "text" — flatten those.
    """
    text = message.get("text") or ""
    if text.strip():
        return text

    parts = []
    for block in message.get("content") or []:
        if block.get("type") == "text" and block.get("text"):
            parts.append(block["text"])
    return "\n".join(parts)


def conversation_to_documents(conv: dict) -> list[Document]:
    turns = [
        Turn(sender=m["sender"], text=t, timestamp=m["created_at"])
        for m in conv["chat_messages"]
        if (t := extract_text(m))
    ]
    if not turns:
        return []

    title = conv["name"] or "(untitled)"
    windows = chunk_turns(turns, WINDOW_CHARS, OVERLAP_TURNS)
    title_overhead = len(title) + 2  # "{title}\n\n" prepended to every piece below

    docs = []
    for i, window in enumerate(windows):
        body = "\n".join(f"{t.sender}: {t.text}" for t in window)
        pieces = split_oversized(body, MAX_CHUNK_CHARS - title_overhead)
        for j, piece in enumerate(pieces):
            chunk_id = f"{conv['uuid']}:{i}" if len(pieces) == 1 else f"{conv['uuid']}:{i}.{j}"
            docs.append(
                Document(
                    id=chunk_id,
                    # title prefixed so topic-level queries hit chunks deep in
                    # a long thread even if the chunk text itself doesn't
                    # restate it
                    text=f"{title}\n\n{piece}",
                    source_type="conversation",
                    title=title,
                    timestamp=window[-1].timestamp,
                )
            )
    return docs


def build_documents(export_dir: Path) -> tuple[list[Document], int, int]:
    """Parse the export into Documents. Pure function, no I/O side effects
    other than reading — so a test can call this directly and assert on the
    result instead of scraping printed output."""
    docs: list[Document] = []

    # --- conversations.json: list of conversation objects ---
    conversations_path = export_dir / "conversations.json"
    conversations = json.loads(conversations_path.read_text())

    skipped = 0
    for conv in conversations:
        conv_docs = conversation_to_documents(conv)
        if not conv_docs:
            skipped += 1
        docs.extend(conv_docs)

    # --- memories.json: single-element list with one summary string ---
    memories_path = export_dir / "memories.json"
    if memories_path.exists():
        memories_list = json.loads(memories_path.read_text())
        if memories_list and memories_list[0].get("conversations_memory"):
            docs.append(
                Document(
                    id="memories",
                    text=memories_list[0]["conversations_memory"],
                    source_type="memory",
                    title="Working memory summary",
                    timestamp=datetime.now(timezone.utc).isoformat(),
                )
            )

    # --- projects/*.json: each project has docs[] (curated reference files) ---
    projects_dir = export_dir / "projects"
    for project_path in sorted(projects_dir.glob("*.json")) if projects_dir.is_dir() else []:
        project = json.loads(project_path.read_text())
        for doc in project.get("docs", []):
            if not doc.get("content"):
                continue
            docs.append(
                Document(
                    id=f"{project['uuid']}:{doc['uuid']}",
                    text=doc["content"],
                    source_type="project_doc",
                    title=f"{project['name']}/{doc['filename']}",
                    timestamp=project["updated_at"],
                )
            )

    return docs, len(conversations), skipped


def main(export_dir: Path) -> None:
    docs, conversation_count, skipped = build_documents(export_dir)
    report(docs, conversation_count, skipped)


def report(docs: list[Document], conversation_count: int, skipped: int) -> None:
    by_type: dict[str, list[Document]] = {}
    for d in docs:
        by_type.setdefault(d.source_type, []).append(d)

    total_chars = sum(len(d.text) for d in docs)
    chunk_lengths = sorted(len(d.text) for d in by_type.get("conversation", []))

    print(f"conversations in export: {conversation_count}")
    print(f"conversations skipped (no usable text): {skipped}")
    print(f"docs produced: {len(docs)}")
    for source_type, group in by_type.items():
        print(f"  {source_type}: {len(group)}")
    print(f"total chars: {total_chars:,}")
    if chunk_lengths:
        print(
            f"conversation chunk length (chars) — min/median/max: "
            f"{chunk_lengths[0]:,} / {chunk_lengths[len(chunk_lengths) // 2]:,} / {chunk_lengths[-1]:,}"
        )

    if "project_doc" in by_type:
        print("\n--- project docs ---")
        for d in by_type["project_doc"]:
            print(f"  {d.title} ({len(d.text):,} chars)")

    print("\n--- samples ---")
    for source_type, group in by_type.items():
        for d in random.sample(group, min(2, len(group))):
            preview = d.text[:200].replace("\n", " ")
            print(f"\n[{d.source_type}] {d.title} ({d.timestamp})")
            print(f"  {preview}...")


if __name__ == "__main__":
    export_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "data" / "inspect"
    main(export_dir)
