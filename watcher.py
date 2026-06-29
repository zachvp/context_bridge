#!/usr/bin/env python3
"""
Live watcher: ingests Claude Code sessions and chat export ZIPs as they appear.

Path A: ~/.claude/projects/**/*.jsonl  → ingest_code_sessions.py (5 s debounce)
Path B: <project>/data/*.zip           → build_all.sh <zip>
"""

import logging
import logging.handlers
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

_LOG_PATH = Path(os.environ.get("WATCHER_LOG_PATH", PROJECT_ROOT / "logs" / "watcher.log"))
_LOG_MAX_BYTES = int(os.environ.get("WATCHER_LOG_MAX_BYTES", 2 * 1024 * 1024))
_LOG_BACKUP_COUNT = int(os.environ.get("WATCHER_LOG_BACKUP_COUNT", 1))

def _setup_logger() -> logging.Logger:
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("watcher")
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%dT%H:%M:%S")
    fh = logging.handlers.RotatingFileHandler(
        _LOG_PATH, maxBytes=_LOG_MAX_BYTES, backupCount=_LOG_BACKUP_COUNT
    )
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)
    return logger

log = _setup_logger()


def _run(cmd: list, label: str) -> None:
    log.info(label)
    result = subprocess.run(cmd, capture_output=False)
    if result.returncode != 0:
        log.error("%s exited %d", label, result.returncode)


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
    log.info("watching %s and %s", SESSIONS_DIR, DATA_DIR)
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
