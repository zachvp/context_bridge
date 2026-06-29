#!/usr/bin/env python3
"""
Live watcher: ingests Claude Code sessions and chat export ZIPs as they appear.

Path A: ~/.claude/projects/**/*.jsonl  → ingest_code_sessions.py (5 s debounce)
Path B: <project>/data/*.zip           → build_all.sh <zip>
"""

import json
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

SERVER_SCRIPT = PROJECT_ROOT / "server.py"
DB_PATH = Path(os.environ.get("CONTEXT_BRIDGE_DB_PATH") or PROJECT_ROOT / "chat_memory.db")

DEBOUNCE_SECONDS = 5

_mcp_proc: subprocess.Popen | None = None
_mcp_lock = threading.Lock()


def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB"):
        if n < 1024:
            return f"{n:.0f}{unit}"
        n /= 1024
    return f"{n:.1f}GB"


def _log_mcp_stats() -> None:
    try:
        data = json.loads(_STATS_PATH.read_text())
        by_tool = data.get("by_tool", {})
        tool_summary = " ".join(f"{t}={v['calls']}" for t, v in by_tool.items())
        db_size = data.get("db_bytes")
        db_delta = data.get("db_delta_bytes")
        db_part = (
            (f"  db={_fmt_bytes(db_size)}" + (f" delta=+{_fmt_bytes(db_delta)}" if db_delta else ""))
            if db_size
            else ""
        )
        log.info(
            "mcp stats: calls=%d %s bytes_out=%s%s",
            data.get("calls", 0),
            tool_summary,
            _fmt_bytes(data.get("bytes_out", 0)),
            db_part,
        )
        _STATS_PATH.unlink()
    except (FileNotFoundError, json.JSONDecodeError):
        pass


def _write_db_stats(db_bytes: int, db_delta: int) -> None:
    try:
        data = json.loads(_STATS_PATH.read_text()) if _STATS_PATH.exists() else {}
    except json.JSONDecodeError:
        data = {}
    data["db_bytes"] = db_bytes
    data["db_delta_bytes"] = db_delta
    _STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STATS_PATH.write_text(json.dumps(data))


def _restart_mcp() -> None:
    global _mcp_proc
    with _mcp_lock:
        if _mcp_proc is not None:
            _log_mcp_stats()
            log.info("mcp restart: stopping pid %d", _mcp_proc.pid)
            _mcp_proc.terminate()
            try:
                _mcp_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _mcp_proc.kill()
                _mcp_proc.wait()
        _mcp_proc = subprocess.Popen(
            [str(PYTHON), str(SERVER_SCRIPT)],
            stdout=open(_MCP_LOG_PATH, "a"),
            stderr=open(_MCP_ERR_PATH, "a"),
        )
        log.info("mcp restart: started pid %d", _mcp_proc.pid)


_LOG_PATH = Path(os.environ.get("WATCHER_LOG_PATH", PROJECT_ROOT / "logs" / "watcher.log"))
_LOG_MAX_BYTES = int(os.environ.get("WATCHER_LOG_MAX_BYTES", 2 * 1024 * 1024))
_LOG_BACKUP_COUNT = int(os.environ.get("WATCHER_LOG_BACKUP_COUNT", 1))
_MCP_LOG_PATH = _LOG_PATH.parent / "server.log"
_MCP_ERR_PATH = _LOG_PATH.parent / "server.err"
_STATS_PATH = _LOG_PATH.parent / "mcp_stats.json"


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
        db_before = DB_PATH.stat().st_size if DB_PATH.exists() else 0
        project_dir = path.parent
        _run(
            [str(PYTHON), str(INGEST_SESSIONS), "--sessions-dir", str(project_dir.parent)],
            f"ingest sessions: {project_dir.name}",
        )
        db_after = DB_PATH.stat().st_size if DB_PATH.exists() else 0
        _write_db_stats(db_after, db_after - db_before)
        _restart_mcp()


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
        _restart_mcp()


def main() -> None:
    _restart_mcp()
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
        with _mcp_lock:
            if _mcp_proc is not None:
                log.info("mcp shutdown: stopping pid %d", _mcp_proc.pid)
                _mcp_proc.terminate()
                _mcp_proc.wait()


if __name__ == "__main__":
    main()
