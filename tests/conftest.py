import json
import sqlite3
from pathlib import Path

import numpy as np
import pytest


@pytest.fixture(scope="session")
def schema_sql() -> str:
    return (Path(__file__).parent.parent / "schema.sql").read_text()


@pytest.fixture
def make_db(schema_sql):
    def _factory(path: Path, model_name: str, rows: list[tuple], dim: int = 4) -> None:
        conn = sqlite3.connect(path)
        conn.executescript(schema_sql)
        conn.executemany(
            "INSERT INTO chunks (id, text, source_type, title, timestamp, embedding, source, project) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.execute(
            "INSERT INTO meta (model_name, dim, built_at) VALUES (?, ?, '2024-01-01T00:00:00+00:00')",
            (model_name, dim),
        )
        conn.commit()
        conn.close()

    return _factory


@pytest.fixture
def fake_vec():
    def _make(dim: int = 4) -> bytes:
        return np.zeros(dim, dtype="float32").tobytes()

    return _make


@pytest.fixture
def minimal_export(tmp_path: Path) -> Path:
    export_dir = tmp_path / "export"
    export_dir.mkdir()
    (export_dir / "projects").mkdir()

    conversations = [
        {
            "uuid": "aaaaaaaa-0000-0000-0000-000000000001",
            "name": "Test Conversation",
            "chat_messages": [
                {
                    "sender": "human",
                    "text": "Hello world",
                    "created_at": "2024-01-01T00:00:00Z",
                    "content": [],
                },
                {
                    "sender": "assistant",
                    "text": "Hi there",
                    "created_at": "2024-01-01T00:00:01Z",
                    "content": [],
                },
            ],
        }
    ]
    (export_dir / "conversations.json").write_text(json.dumps(conversations))
    (export_dir / "memories.json").write_text(
        json.dumps([{"conversations_memory": "Test memory content. " * 10}])
    )
    return export_dir


@pytest.fixture
def minimal_session_jsonl(tmp_path: Path) -> Path:
    path = tmp_path / "session-abc123.jsonl"
    lines = [
        json.dumps({"type": "ai-title", "aiTitle": "Test Session"}),
        json.dumps(
            {
                "type": "user",
                "uuid": "user-001",
                "parentUuid": None,
                "timestamp": "2024-01-01T00:00:00Z",
                "message": {"role": "user", "content": "Hello Claude"},
                "cwd": "/home/user/myproject",
            }
        ),
        json.dumps(
            {
                "type": "assistant",
                "uuid": "asst-001",
                "parentUuid": "user-001",
                "timestamp": "2024-01-01T00:00:01Z",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Hello! How can I help?"}],
                },
            }
        ),
    ]
    path.write_text("\n".join(lines))
    return path
