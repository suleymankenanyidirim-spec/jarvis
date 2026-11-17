"""JARVIS hafızası (SQLite).

v2.3: konuşma oturumları. `messages` tablosunda `session_id` ve `source` (hud / phone / task) var;
yakın konuşma bağlamı yalnızca o oturumdan gelir, kalıcı hafıza (memories) herkes için ortaktır.
Aynı veritabanı dosyasını açan bütün MemoryStore nesneleri tek bir yazma kilidini paylaşır.
"""
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

from core.paths import resolve

TOKEN_RE = re.compile(r"[^\W\d_]+|[\w]+", re.UNICODE)

SCHEMA_VERSION = 2
DEFAULT_SESSION = "hud"
VALID_SOURCES = ("hud", "phone", "task")
SESSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")

_LOCKS = {}
_LOCKS_GUARD = threading.Lock()


def _shared_lock(path):
    """Aynı dosya için tek yazma kilidi ("database is locked" olmasın)."""
    key = str(path.resolve()).lower()
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = _LOCKS[key] = threading.RLock()
        return lock


def valid_session(session_id):
    return isinstance(session_id, str) and bool(SESSION_RE.match(session_id))


def _now():
    return datetime.now(timezone.utc).isoformat()


class MemoryStore:
    def __init__(self, db_path="data/jarvis_memory.db"):
        # v2.2: göreli yollar main.py'nin klasörüne göre çözülür (cwd'ye göre değil)
        self.db_path = resolve(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = _shared_lock(self.db_path)
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

    @contextmanager
    def _write(self):
        with self.lock, self._connect() as db:
            yield db

    # ------------------------------------------------------------------ şema
    @staticmethod
    def _columns(db, table):
        return {row[1] for row in db.execute(f"PRAGMA table_info({table})")}

    def _init_db(self):
        with self._write() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS messages(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    session_id TEXT NOT NULL DEFAULT 'hud',
                    source TEXT NOT NULL DEFAULT 'hud'
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
            self._migrate(db)

    def _migrate(self, db):
        """Eski veritabanını taşır. Birden çok kez çalışsa da zararsızdır; eski mesajlar HUD oturumuna geçer."""
        cols = self._columns(db, "messages")
        if "session_id" not in cols:
            db.execute(f"ALTER TABLE messages ADD COLUMN session_id TEXT NOT NULL DEFAULT '{DEFAULT_SESSION}'")
        if "source" not in cols:
            db.execute("ALTER TABLE messages ADD COLUMN source TEXT NOT NULL DEFAULT 'hud'")
        db.execute("CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id)")
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version < SCHEMA_VERSION:
            db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def schema_version(self):
        with self._connect() as db:
            return db.execute("PRAGMA user_version").fetchone()[0]

    # ------------------------------------------------------------------ mesajlar
    @staticmethod
    def _check(session_id, source):
        session_id = DEFAULT_SESSION if session_id is None else session_id
        source = "hud" if source is None else source
        if source not in VALID_SOURCES:
            raise ValueError(f"Geçersiz kaynak: {source!r}")
        if not valid_session(session_id):
            raise ValueError(f"Geçersiz oturum: {session_id!r}")
        return session_id, source

    def save_message(self, role, content, session_id=None, source=None):
        session_id, source = self._check(session_id, source)
        with self._write() as db:
            db.execute(
                "INSERT INTO messages(role, content, created_at, session_id, source) VALUES(?,?,?,?,?)",
                (role, content, _now(), session_id, source)
            )

    def recent_messages(self, limit=8, session_id=None):
        """Yalnızca verilen oturumun son mesajları (eskiden yeniye)."""
        session_id = DEFAULT_SESSION if session_id is None else session_id
        with self._connect() as db:
            rows = db.execute(
                "SELECT role, content FROM messages WHERE session_id=? ORDER BY id DESC LIMIT ?",
                (session_id, int(limit))
            ).fetchall()
        return list(reversed(rows))

    # ------------------------------------------------------------------ kalıcı hafıza
    def save_memory(self, content):
        content = str(content).strip()
        if not content:
            return False
        with self._write() as db:
            cur = db.execute(
                "INSERT OR IGNORE INTO memories(content, created_at, active) VALUES(?,?,1)",
                (content, _now())
            )
            return cur.rowcount > 0

    def forget_memory(self, phrase):
        phrase = str(phrase).strip()
        if not phrase:
            return 0
        with self._write() as db:
            cur = db.execute(
                "UPDATE memories SET active=0 WHERE active=1 AND content LIKE ?",
                ("%" + phrase + "%",)
            )
            return cur.rowcount

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

    def build_context(self, query, session_id=None, recent_limit=8):
        return self.relevant_memories(query), self.recent_messages(recent_limit, session_id=session_id)
