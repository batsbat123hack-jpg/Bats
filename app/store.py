"""SQLite persistence: conversations, messages, feedback, preferences."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT 'New chat',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    provider TEXT DEFAULT '',
    model TEXT DEFAULT '',
    pinned INTEGER DEFAULT 0,
    archived INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    attachments TEXT NOT NULL DEFAULT '[]',
    sources TEXT NOT NULL DEFAULT '[]',
    images TEXT NOT NULL DEFAULT '[]',
    meta TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_conv_updated ON conversations(updated_at DESC);
CREATE TABLE IF NOT EXISTS prefs (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def new_id(prefix: str = "") -> str:
    return prefix + uuid.uuid4().hex[:20]


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        try:
            self._conn.close()
        except sqlite3.Error:
            pass

    # ---------------------------- conversations --------------------------- #
    def create_conversation(
        self, title: str = "New chat", provider: str = "", model: str = ""
    ) -> dict[str, Any]:
        cid = new_id("c_")
        now = time.time()
        self._conn.execute(
            "INSERT INTO conversations (id,title,created_at,updated_at,provider,model) "
            "VALUES (?,?,?,?,?,?)",
            (cid, title, now, now, provider, model),
        )
        self._conn.commit()
        return self.get_conversation(cid)  # type: ignore[return-value]

    def get_conversation(self, cid: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM conversations WHERE id=?", (cid,)).fetchone()
        return dict(row) if row else None

    def touch_conversation(
        self, cid: str, title: str | None = None, provider: str = "", model: str = ""
    ) -> None:
        now = time.time()
        if title is not None:
            self._conn.execute(
                "UPDATE conversations SET updated_at=?, title=?, provider=COALESCE(NULLIF(?,''),provider), "
                "model=COALESCE(NULLIF(?,''),model) WHERE id=?",
                (now, title, provider, model, cid),
            )
        else:
            self._conn.execute(
                "UPDATE conversations SET updated_at=?, "
                "provider=COALESCE(NULLIF(?,''),provider), "
                "model=COALESCE(NULLIF(?,''),model) WHERE id=?",
                (now, provider, model, cid),
            )
        self._conn.commit()

    def rename_conversation(self, cid: str, title: str) -> None:
        self._conn.execute(
            "UPDATE conversations SET title=?, updated_at=? WHERE id=?", (title[:120], time.time(), cid)
        )
        self._conn.commit()

    def set_flags(self, cid: str, pinned: bool | None = None, archived: bool | None = None) -> None:
        if pinned is not None:
            self._conn.execute("UPDATE conversations SET pinned=? WHERE id=?", (int(pinned), cid))
        if archived is not None:
            self._conn.execute("UPDATE conversations SET archived=? WHERE id=?", (int(archived), cid))
        self._conn.commit()

    def delete_conversation(self, cid: str) -> None:
        self._conn.execute("DELETE FROM messages WHERE conversation_id=?", (cid,))
        self._conn.execute("DELETE FROM conversations WHERE id=?", (cid,))
        self._conn.commit()

    def delete_all_conversations(self) -> None:
        self._conn.execute("DELETE FROM messages")
        self._conn.execute("DELETE FROM conversations")
        self._conn.commit()

    def list_conversations(self, query: str = "", archived: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM conversations WHERE archived=?"
        args: list[Any] = [int(archived)]
        if query:
            sql += " AND (title LIKE ? OR id IN (SELECT conversation_id FROM messages WHERE content LIKE ?))"
            like = f"%{query}%"
            args.extend([like, like])
        sql += " ORDER BY pinned DESC, updated_at DESC LIMIT 300"
        rows = self._conn.execute(sql, args).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            preview = self._conn.execute(
                "SELECT content FROM messages WHERE conversation_id=? AND role='user' "
                "ORDER BY created_at LIMIT 1",
                (item["id"],),
            ).fetchone()
            item["preview"] = (preview["content"][:140] if preview else "")
            count = self._conn.execute(
                "SELECT COUNT(*) AS n FROM messages WHERE conversation_id=?", (item["id"],)
            ).fetchone()
            item["message_count"] = count["n"] if count else 0
            out.append(item)
        return out

    # ------------------------------- messages ----------------------------- #
    def add_message(
        self,
        cid: str,
        role: str,
        content: str,
        attachments: Iterable[dict[str, Any]] = (),
        sources: Iterable[dict[str, Any]] = (),
        images: Iterable[dict[str, Any]] = (),
        meta: dict[str, Any] | None = None,
        message_id: str | None = None,
    ) -> dict[str, Any]:
        mid = message_id or new_id("m_")
        now = time.time()
        self._conn.execute(
            "INSERT INTO messages (id,conversation_id,role,content,attachments,sources,images,meta,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (
                mid,
                cid,
                role,
                content,
                json.dumps(list(attachments)),
                json.dumps(list(sources)),
                json.dumps(list(images)),
                json.dumps(meta or {}),
                now,
            ),
        )
        self._conn.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now, cid))
        self._conn.commit()
        return self.get_message(mid)  # type: ignore[return-value]

    def update_message(self, mid: str, **fields: Any) -> None:
        allowed = {"content", "attachments", "sources", "images", "meta"}
        sets, args = [], []
        for key, value in fields.items():
            if key not in allowed:
                continue
            sets.append(f"{key}=?")
            args.append(value if isinstance(value, str) else json.dumps(value))
        if not sets:
            return
        args.append(mid)
        self._conn.execute(f"UPDATE messages SET {', '.join(sets)} WHERE id=?", args)
        self._conn.commit()

    def get_message(self, mid: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
        return self._row_to_message(row) if row else None

    def delete_message(self, mid: str) -> None:
        self._conn.execute("DELETE FROM messages WHERE id=?", (mid,))
        self._conn.commit()

    def get_messages(self, cid: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at", (cid,)
        ).fetchall()
        return [self._row_to_message(r) for r in rows]

    def delete_messages_after(self, cid: str, mid: str, include_self: bool = True) -> None:
        row = self._conn.execute(
            "SELECT created_at FROM messages WHERE id=?", (mid,)
        ).fetchone()
        if not row:
            return
        op = ">=" if include_self else ">"
        self._conn.execute(
            f"DELETE FROM messages WHERE conversation_id=? AND created_at {op} ?",
            (cid, row["created_at"]),
        )
        self._conn.commit()

    def truncate_messages(self, cid: str, keep: int) -> None:
        rows = self._conn.execute(
            "SELECT id FROM messages WHERE conversation_id=? ORDER BY created_at", (cid,)
        ).fetchall()
        for row in rows[keep:]:
            self._conn.execute("DELETE FROM messages WHERE id=?", (row["id"],))
        self._conn.commit()

    @staticmethod
    def _row_to_message(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        for key in ("attachments", "sources", "images", "meta"):
            try:
                item[key] = json.loads(item.get(key) or ("{}" if key == "meta" else "[]"))
            except ValueError:
                item[key] = {} if key == "meta" else []
        return item

    # -------------------------------- prefs ------------------------------- #
    def set_pref(self, key: str, value: Any) -> None:
        self._conn.execute(
            "INSERT INTO prefs (key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=?",
            (key, json.dumps(value), json.dumps(value)),
        )
        self._conn.commit()

    def get_pref(self, key: str, default: Any = None) -> Any:
        row = self._conn.execute("SELECT value FROM prefs WHERE key=?", (key,)).fetchone()
        if not row:
            return default
        try:
            return json.loads(row["value"])
        except ValueError:
            return default

    # -------------------------------- stats ------------------------------- #
    def stats(self) -> dict[str, int]:
        conv = self._conn.execute("SELECT COUNT(*) AS n FROM conversations").fetchone()["n"]
        msg = self._conn.execute("SELECT COUNT(*) AS n FROM messages").fetchone()["n"]
        return {"conversations": conv, "messages": msg}
