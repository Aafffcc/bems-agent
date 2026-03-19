from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from bems_agent.agent.exceptions import SessionNotFoundError


@dataclass(slots=True)
class SessionSummary:
    thread_id: str
    updated_at: str
    turn_count: int

    @property
    def session_id(self) -> str:
        return self.thread_id


class SessionStore:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._pending_threads: set[str] = set()

    @property
    def db_path(self) -> Path:
        return self._db_path

    def create_thread(self, thread_id: str | None = None) -> str:
        resolved = thread_id or generate_thread_id()
        self._pending_threads.add(resolved)
        return resolved

    def mark_persisted(self, thread_id: str) -> None:
        self._pending_threads.discard(thread_id)

    def ensure_thread_exists(self, thread_id: str) -> None:
        if self.thread_exists(thread_id):
            return
        msg = f"Session '{thread_id}' was not found."
        raise SessionNotFoundError(msg)

    def thread_exists(self, thread_id: str) -> bool:
        if thread_id in self._pending_threads:
            return True
        with self._connect() as conn:
            if not _table_exists(conn, "checkpoints"):
                return False
            row = conn.execute(
                "SELECT 1 FROM checkpoints WHERE thread_id = ? LIMIT 1",
                (thread_id,),
            ).fetchone()
        return row is not None

    def list_sessions(self, limit: int = 20) -> list[SessionSummary]:
        with self._connect() as conn:
            if not _table_exists(conn, "checkpoints"):
                return []

            rows = conn.execute(
                """
                SELECT thread_id,
                       MAX(json_extract(metadata, '$.updated_at')) AS updated_at
                FROM checkpoints
                GROUP BY thread_id
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

            return [
                SessionSummary(
                    thread_id=str(row["thread_id"]),
                    updated_at=str(row["updated_at"] or ""),
                    turn_count=_count_human_messages(conn, str(row["thread_id"])),
                )
                for row in rows
            ]

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn


def generate_thread_id() -> str:
    from uuid_utils import uuid7

    return str(uuid7())


def patch_aiosqlite() -> None:
    import aiosqlite

    if hasattr(aiosqlite.Connection, "is_alive"):
        return

    def _is_alive(self: aiosqlite.Connection) -> bool:
        return bool(self._running and self._connection is not None)

    aiosqlite.Connection.is_alive = _is_alive  # type: ignore[attr-defined]


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _count_human_messages(conn: sqlite3.Connection, thread_id: str) -> int:
    row = conn.execute(
        """
        SELECT type, checkpoint
        FROM checkpoints
        WHERE thread_id = ?
        ORDER BY checkpoint_id DESC
        LIMIT 1
        """,
        (thread_id,),
    ).fetchone()
    if row is None or not row["type"] or not row["checkpoint"]:
        return 0

    try:
        from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

        payload = JsonPlusSerializer().loads_typed((row["type"], row["checkpoint"]))
    except Exception:
        return 0

    messages = _extract_checkpoint_messages(payload)
    return sum(1 for message in messages if _message_type(message) == "human")


def _extract_checkpoint_messages(payload: object) -> list[object]:
    if not isinstance(payload, dict):
        return []
    channel_values = payload.get("channel_values")
    if not isinstance(channel_values, dict):
        return []
    messages = channel_values.get("messages")
    if not isinstance(messages, list):
        return []
    return messages


def _message_type(message: object) -> str | None:
    if hasattr(message, "type"):
        value = message.type  # type: ignore[attr-defined]
        return value if isinstance(value, str) else None
    if isinstance(message, dict):
        msg_type = message.get("type")
        return msg_type if isinstance(msg_type, str) else None
    return None
