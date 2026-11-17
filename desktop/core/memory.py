import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

TOKEN_RE = re.compile(r"[^\W\d_]+|[\w]+", re.UNICODE)

class MemoryStore:
    def __init__(self, db_path="data/jarvis_memory.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _connect(self):
        # v2.1: "with sqlite3.connect()" bağlantıyı kapatmaz; Windows'ta (Python 3.13+)
        # dosya kilitli kalıyordu. Artık işlem bitince commit edilip kapatılır.
        db = sqlite3.connect(self.db_path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def _init_db(self):
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS messages(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS memories(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1
                )
            """)
            db.commit()

    def save_message(self, role, content):
        with self._connect() as db:
            db.execute(
                "INSERT INTO messages(role, content, created_at) VALUES(?,?,?)",
                (role, content, datetime.now(timezone.utc).isoformat())
            )
            db.commit()

    def save_memory(self, content):
        content = str(content).strip()
        if not content:
            return False
        with self._connect() as db:
            cur = db.execute(
                "INSERT OR IGNORE INTO memories(content, created_at, active) VALUES(?,?,1)",
                (content, datetime.now(timezone.utc).isoformat())
            )
            db.commit()
            return cur.rowcount > 0

    def forget_memory(self, phrase):
        phrase = str(phrase).strip()
        if not phrase:
            return 0
        with self._connect() as db:
            cur = db.execute(
                "UPDATE memories SET active=0 WHERE active=1 AND content LIKE ?",
                ("%" + phrase + "%",)
            )
            db.commit()
            return cur.rowcount

    def recent_messages(self, limit=8):
        with self._connect() as db:
            rows = db.execute(
                "SELECT role, content FROM messages ORDER BY id DESC LIMIT ?",
                (limit,)
            ).fetchall()
        return list(reversed(rows))

    def _tokens(self, text):
        return {t.lower() for t in TOKEN_RE.findall(text or "") if len(t) >= 3}

    def relevant_memories(self, query, limit=6):
        tokens = self._tokens(query)
        with self._connect() as db:
            rows = db.execute(
                "SELECT content FROM memories WHERE active=1 ORDER BY id DESC"
            ).fetchall()

        scored = []
        for (content,) in rows:
            low = content.lower()
            score = sum(1 for token in tokens if token in low)
            if score:
                scored.append((score, content))

        scored.sort(key=lambda x: x[0], reverse=True)
        if scored:
            return [content for _, content in scored[:limit]]
        return [content for (content,) in rows[:4]]

    def build_context(self, query):
        return self.relevant_memories(query), self.recent_messages(8)
