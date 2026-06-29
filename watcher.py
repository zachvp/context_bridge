#!/usr/bin/env python3
"""
Live watcher: ingests Claude Code sessions and chat export ZIPs as they appear.

Path A: ~/.claude/projects/**/*.jsonl  → ingest_code_sessions.py (5 s debounce)
Path B: <project>/data/*.zip           → build_all.sh <zip>
"""

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler, FileSystemEvent
from watchdog.observers import Observer

PROJECT_ROOT = Path(__file__).parent
PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python3"
if not PYTHON.exists():
    PYTHON = Path(sys.executable)

INGEST_SESSIONS = PROJECT_ROOT / "ingest_code_sessions.py"
BUILD_ALL = PROJECT_ROOT / "scripts" / "build_all.sh"
DATA_DIR = PROJECT_ROOT / "data"
SESSIONS_DIR = Path("~/.claude/projects").expanduser()

DEBOUNCE_SECONDS = 5


def _run(cmd: list, label: str) -> None:
    print(f"[watcher] {label}", flush=True)
    result = subprocess.run(cmd, capture_output=False)
    if result.returncode != 0:
        print(f"[watcher] ERROR: {label} exited {result.returncode}", file=sys.stderr, flush=True)


class SessionHandler(FileSystemEventHandler):
    """Debounced handler for .jsonl session files."""

    def __init__(self) -> None:
        self._timers: dict[Path, threading.Timer] = {}
        self._lock = threading.Lock()

    def on_modified(self, event: FileSystemEvent) -> None:
        self._schedule(event)

    def on_created(self, event: FileSystemEvent) -> None:
        self._schedule(event)

    def _schedule(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix != ".jsonl":
            return
        with self._lock:
            existing = self._timers.get(path)
            if existing:
                existing.cancel()
            t = threading.Timer(DEBOUNCE_SECONDS, self._ingest, args=[path])
            self._timers[path] = t
            t.start()

    def _ingest(self, path: Path) -> None:
        with self._lock:
            self._timers.pop(path, None)
        # scope ingest to the project directory that owns this file
        project_dir = path.parent
        _run(
            [str(PYTHON), str(INGEST_SESSIONS), "--sessions-dir", str(project_dir)],
            f"ingest sessions: {project_dir.name}",
        )


class ZipHandler(FileSystemEventHandler):
    """Triggers a full rebuild when a new export ZIP lands in data/."""

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix not in {".zip", ".dms"}:
            return
        _run(
            ["bash", str(BUILD_ALL), str(path)],
            f"build_all: {path.name}",
        )


def main() -> None:
    observer = Observer()
    observer.schedule(SessionHandler(), str(SESSIONS_DIR), recursive=True)
    observer.schedule(ZipHandler(), str(DATA_DIR), recursive=False)
    observer.start()
    print(f"[watcher] watching {SESSIONS_DIR} and {DATA_DIR}", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()
        observer.join()


if __name__ == "__main__":
    main()
