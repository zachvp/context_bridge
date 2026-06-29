#!/usr/bin/env python3
"""
Crawl a URL and ingest its pages as searchable chunks in chat_memory.db.

Fetches the seed URL and follows same-origin links up to --max-pages deep,
converts each page to markdown via trafilatura, chunks on heading boundaries,
embeds with the local fastembed model, and upserts into the shared DB.

Usage:
    python3 ingest_web.py https://code.claude.com/docs [--db chat_memory.db] [--max-pages 200]
"""

import argparse
import hashlib
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

from common import (
    Document,
    MAX_CHUNK_CHARS,
    SOURCE_WEB,
    chunk_markdown,
    run_migrations,
)
from embed import embed_documents

_HEADERS = {"User-Agent": "context-bridge/1.0 (personal knowledge base; not a scraper)"}
_TIMEOUT = 15


def _same_origin(base: str, url: str) -> bool:
    b, u = urlparse(base), urlparse(url)
    return b.scheme == u.scheme and b.netloc == u.netloc


def _normalize(url: str) -> str:
    """Strip fragment so #section variants collapse to the same page."""
    p = urlparse(url)
    return p._replace(fragment="").geturl()


def crawl(seed: str, max_pages: int) -> dict[str, str]:
    """BFS crawl from seed. Returns {url: html} for each successfully fetched page."""
    from html.parser import HTMLParser

    class LinkParser(HTMLParser):
        def __init__(self, base: str):
            super().__init__()
            self.base = base
            self.links: list[str] = []

        def handle_starttag(self, tag, attrs):
            if tag == "a":
                href = dict(attrs).get("href", "")
                if href and not href.startswith(("mailto:", "javascript:")):
                    self.links.append(urljoin(self.base, href))

    visited: dict[str, str] = {}
    queue = [_normalize(seed)]
    seen = {queue[0]}

    while queue and len(visited) < max_pages:
        url = queue.pop(0)
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"  skip {url}: {e}", file=sys.stderr)
            continue

        content_type = resp.headers.get("content-type", "")
        if "html" not in content_type:
            continue

        visited[url] = resp.text
        print(f"  fetched [{len(visited)}/{max_pages}] {url}")

        parser = LinkParser(url)
        parser.feed(resp.text)
        for link in parser.links:
            norm = _normalize(link)
            if norm not in seen and _same_origin(seed, norm):
                seen.add(norm)
                queue.append(norm)

    return visited


def html_to_markdown(html: str, url: str) -> tuple[str, str]:
    """Convert HTML to markdown. Returns (title, markdown_body)."""
    import trafilatura

    result = trafilatura.extract(
        html,
        url=url,
        output_format="markdown",
        include_comments=False,
        include_tables=True,
        no_fallback=False,
    )
    # extract title separately
    meta = trafilatura.extract_metadata(html, default_url=url)
    title = (meta.title if meta and meta.title else urlparse(url).path) or url
    return title, result or ""


def pages_to_documents(pages: dict[str, str]) -> list[Document]:
    now = datetime.now(timezone.utc).isoformat()
    docs: list[Document] = []

    for url, html in pages.items():
        title, md = html_to_markdown(html, url)
        if not md.strip():
            continue

        chunks = chunk_markdown(md, MAX_CHUNK_CHARS)
        for i, (heading, body) in enumerate(chunks):
            if not body.strip():
                continue
            section = f"{title} — {heading}" if heading else title
            text = f"{section}\n\n{body}"
            chunk_id = f"web:{hashlib.sha256(text.encode()).hexdigest()[:16]}"
            docs.append(
                Document(
                    id=chunk_id,
                    text=text,
                    source_type="docs",
                    title=section,
                    timestamp=now,
                    source=SOURCE_WEB,
                    project=urlparse(url).netloc,
                )
            )

    return docs


def upsert_documents(conn: sqlite3.Connection, docs: list[Document], vectors) -> None:
    if docs:
        domain = docs[0].project
        conn.execute("DELETE FROM chunks WHERE source = ? AND project = ?", (SOURCE_WEB, domain))
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
    conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Crawl a URL and ingest into chat_memory.db")
    parser.add_argument("url", help="Seed URL to crawl")
    parser.add_argument("--db", default="chat_memory.db", help="Path to SQLite DB")
    parser.add_argument("--max-pages", type=int, default=200, help="Max pages to crawl")
    args = parser.parse_args()

    db_path = Path(args.db)
    if db_path.exists():
        import shutil
        bak = db_path.with_suffix(".db.bak")
        shutil.copy2(db_path, bak)
        print(f"backup written to {bak}")

    conn = sqlite3.connect(args.db)
    run_migrations(conn)

    print(f"crawling {args.url} (max {args.max_pages} pages)...")
    pages = crawl(args.url, args.max_pages)
    print(f"fetched {len(pages)} pages")

    docs = pages_to_documents(pages)
    print(f"produced {len(docs)} chunks")

    if not docs:
        print("nothing to embed — exiting")
        return

    print("embedding...")
    vectors = embed_documents(docs)

    print("upserting into DB...")
    upsert_documents(conn, docs, vectors)
    print(f"done — {len(docs)} chunks written to {args.db}")


if __name__ == "__main__":
    main()
