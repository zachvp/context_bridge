"""Smoke tests for watcher MCP server lifecycle management."""

import json
import select
import subprocess
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import watcher

PROJECT_ROOT = Path(__file__).parent.parent
PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python3"
SERVER_SCRIPT = PROJECT_ROOT / "server.py"

_INIT_MSG = (
    json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
        }
    )
    + "\n"
)


def _send_initialize(proc: subprocess.Popen, timeout: float = 3.0) -> dict:
    proc.stdin.write(_INIT_MSG.encode())
    proc.stdin.flush()
    ready, _, _ = select.select([proc.stdout], [], [], timeout)
    assert ready, "server did not respond within timeout"
    return json.loads(proc.stdout.readline())


@pytest.fixture(autouse=True)
def reset_mcp_state():
    """Reset global MCP process state between tests."""
    watcher._mcp_proc = None
    yield
    watcher._mcp_proc = None


def make_mock_proc(pid: int = 1234) -> MagicMock:
    proc = MagicMock()
    proc.pid = pid
    proc.wait.return_value = 0
    return proc


def test_restart_mcp_starts_server():
    """First call spawns a server process."""
    mock_proc = make_mock_proc()
    with patch("watcher.subprocess.Popen", return_value=mock_proc) as mock_popen:
        watcher._restart_mcp()
    mock_popen.assert_called_once()
    assert watcher._mcp_proc is mock_proc


def test_restart_mcp_kills_existing_before_restarting():
    """Subsequent call terminates the old process before spawning a new one."""
    old_proc = make_mock_proc(pid=100)
    new_proc = make_mock_proc(pid=200)
    watcher._mcp_proc = old_proc

    with patch("watcher.subprocess.Popen", return_value=new_proc):
        watcher._restart_mcp()

    old_proc.terminate.assert_called_once()
    old_proc.wait.assert_called()
    assert watcher._mcp_proc is new_proc


def test_restart_mcp_force_kills_on_timeout():
    """If terminate doesn't exit in time, kill() is called."""
    import subprocess as sp

    old_proc = make_mock_proc(pid=100)
    old_proc.wait.side_effect = [sp.TimeoutExpired(cmd="python", timeout=5), 0]
    new_proc = make_mock_proc(pid=200)
    watcher._mcp_proc = old_proc

    with patch("watcher.subprocess.Popen", return_value=new_proc):
        watcher._restart_mcp()

    old_proc.kill.assert_called_once()


def test_ingest_triggers_mcp_restart(tmp_path: Path):
    """SessionHandler._ingest calls _restart_mcp after ingesting."""
    jsonl = tmp_path / "proj" / "session.jsonl"
    jsonl.parent.mkdir()
    jsonl.touch()

    handler = watcher.SessionHandler()
    mock_proc = make_mock_proc()

    with patch("watcher.subprocess.run"), patch("watcher.subprocess.Popen", return_value=mock_proc):
        handler._ingest(jsonl)

    assert watcher._mcp_proc is mock_proc


# ---------------------------------------------------------------------------
# Integration: real server.py process
# ---------------------------------------------------------------------------


def _spawn_server() -> subprocess.Popen:
    return subprocess.Popen(
        [str(PYTHON), str(SERVER_SCRIPT)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def test_server_responds_to_initialize():
    """Real server.py starts and returns a valid MCP initialize response."""
    proc = _spawn_server()
    try:
        resp = _send_initialize(proc)
        assert resp.get("id") == 1
        assert "result" in resp
        assert resp["result"]["serverInfo"]["name"] == "context-bridge"
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_server_restarts_and_responds():
    """After killing the server and spawning a new one, the new process responds."""
    proc1 = _spawn_server()
    _send_initialize(proc1)
    proc1.terminate()
    proc1.wait(timeout=5)

    proc2 = _spawn_server()
    try:
        resp = _send_initialize(proc2)
        assert "result" in resp
    finally:
        proc2.terminate()
        proc2.wait(timeout=5)


def test_tool_call_writes_stats_sidecar(tmp_path):
    """A real tools/call populates mcp_stats.json with call count and bytes."""
    stats_path = tmp_path / "mcp_stats.json"
    env = {**__import__("os").environ, "MCP_STATS_PATH": str(stats_path)}

    proc = subprocess.Popen(
        [str(PYTHON), str(SERVER_SCRIPT)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    try:
        _send_initialize(proc)

        # send initialized notification (required by MCP before tool calls)
        notif = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}) + "\n"
        proc.stdin.write(notif.encode())
        proc.stdin.flush()

        call_msg = (
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "search_chat_history",
                        "arguments": {"query_text": "test", "top_k": 1},
                    },
                }
            )
            + "\n"
        )
        proc.stdin.write(call_msg.encode())
        proc.stdin.flush()

        # read until we get the tool response (id=2)
        deadline = time.time() + 5
        resp = None
        while time.time() < deadline:
            ready, _, _ = select.select([proc.stdout], [], [], 1)
            if ready:
                line = proc.stdout.readline()
                msg = json.loads(line)
                if msg.get("id") == 2:
                    resp = msg
                    break

        assert resp is not None, "no tool response received"
        assert "result" in resp

        assert stats_path.exists(), "mcp_stats.json not written"
        stats = json.loads(stats_path.read_text())
        assert stats["calls"] == 1
        assert stats["bytes_out"] > 0
        assert "search_chat_history" in stats["by_tool"]
    finally:
        proc.terminate()
        proc.wait(timeout=5)
