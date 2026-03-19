import json
import sqlite3

from bems_agent.agent.sessions import SessionStore


def test_session_store_tracks_pending_threads(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions.db")

    thread_id = store.create_thread("thread-001")

    assert thread_id == "thread-001"
    assert store.thread_exists("thread-001") is True


def test_session_store_lists_checkpoint_threads(tmp_path) -> None:
    db_path = tmp_path / "sessions.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE checkpoints (
            thread_id TEXT,
            checkpoint_id TEXT,
            metadata TEXT,
            type TEXT,
            checkpoint BLOB
        )
        """
    )
    conn.execute(
        """
        INSERT INTO checkpoints(thread_id, checkpoint_id, metadata, type, checkpoint)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            "thread-001",
            "cp-001",
            json.dumps({"updated_at": "2026-03-17T00:00:00+00:00"}),
            None,
            None,
        ),
    )
    conn.commit()
    conn.close()

    store = SessionStore(db_path)
    sessions = store.list_sessions()

    assert len(sessions) == 1
    assert sessions[0].thread_id == "thread-001"
    assert sessions[0].session_id == "thread-001"
    assert sessions[0].turn_count == 0
