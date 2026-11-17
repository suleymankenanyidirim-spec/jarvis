"""JARVIS hafızası (SQLite) — PC'deki tek doğru kaynak.

v2.3: konuşma oturumları. `messages` tablosunda `session_id` ve `source` (hud / phone / task) var;
yakın konuşma bağlamı yalnızca o oturumdan gelir, kalıcı hafıza (memories) herkes için ortaktır.
Aynı veritabanı dosyasını açan bütün MemoryStore nesneleri tek bir yazma kilidini paylaşır.

v2.4 (şema 3): `memories.norm` (normalleştirilmiş metin) ile aynı içerik iki kez eklenmez;
`sync_log` telefondan gelen öğelerin (client_id) tekrar işlenmesini engeller; id'li ekle / düzenle /
sil / listele, oturum geçmişi ve telefondan mesaj aktarımı. `relevant_memories` Türkçe ekleri
hesaba katar (core/turkish.py); `stemming=False` v2.3 davranışıdır.
"""
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

from core import turkish
from core.paths import resolve

TOKEN_RE = re.compile(r"[^\W\d_]+|[\w]+", re.UNICODE)

SCHEMA_VERSION = 3
DEFAULT_SESSION = "hud"
VALID_SOURCES = ("hud", "phone", "task")
VALID_ROLES = ("user", "assistant")
SESSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
CLIENT_ID_MAX = 100

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


def _iso_from_ms(value):
    try:
        ms = float(value)
        if ms <= 0:
            return None
        return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _client_id(value):
    value = str(value or "").strip()
    return value[:CLIENT_ID_MAX] if value else ""


class MemoryStore:
    def __init__(self, db_path="data/jarvis_memory.db", stemming=True):
        # v2.2: göreli yollar main.py'nin klasörüne göre çözülür (cwd'ye göre değil)
        self.db_path = resolve(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.stemming = bool(stemming)
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
                    active INTEGER NOT NULL DEFAULT 1,
                    norm TEXT NOT NULL DEFAULT ''
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS sync_log(
                    client_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    ref_id INTEGER,
                    created_at TEXT NOT NULL
                )
            """)
            self._migrate(db)

    def _migrate(self, db):
        """Eski veritabanını taşır. Birden çok kez çalışsa da zararsızdır; hiçbir kayıt silinmez.

        şema 1 -> 2: messages.session_id / source (eski mesajlar HUD oturumuna geçer)
        şema 2 -> 3: memories.norm (eski kayıtlar için doldurulur) + sync_log
        """
        cols = self._columns(db, "messages")
        if "session_id" not in cols:
            db.execute(f"ALTER TABLE messages ADD COLUMN session_id TEXT NOT NULL DEFAULT '{DEFAULT_SESSION}'")
        if "source" not in cols:
            db.execute("ALTER TABLE messages ADD COLUMN source TEXT NOT NULL DEFAULT 'hud'")
        db.execute("CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id)")

        if "norm" not in self._columns(db, "memories"):
            db.execute("ALTER TABLE memories ADD COLUMN norm TEXT NOT NULL DEFAULT ''")
        for mid, content in db.execute("SELECT id, content FROM memories WHERE norm=''").fetchall():
            db.execute("UPDATE memories SET norm=? WHERE id=?", (turkish.normalize(content), mid))
        db.execute("CREATE INDEX IF NOT EXISTS idx_memories_norm ON memories(norm)")

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

    def history(self, session_id, limit=50):
        """v2.4: bir oturumun son `limit` mesajı, eskiden yeniye."""
        limit = max(1, int(limit))
        with self._connect() as db:
            rows = db.execute(
                "SELECT id, role, content, created_at, session_id, source FROM messages "
                "WHERE session_id=? ORDER BY id DESC LIMIT ?", (session_id, limit)
            ).fetchall()
        keys = ("id", "role", "content", "created_at", "session_id", "source")
        return [dict(zip(keys, row)) for row in reversed(rows)]

    def import_messages(self, session_id, messages, source="phone"):
        """v2.4: telefonun bulutta biriktirdiği mesajları oturumuna ekler.

        Her öğe {client_id, role, text, t}. Aynı client_id ikinci kez işlenmez.
        Dönüş: {imported, skipped, invalid}.
        """
        session_id, source = self._check(session_id, source)
        imported = skipped = invalid = 0
        with self._write() as db:
            for item in messages or []:
                if not isinstance(item, dict):
                    invalid += 1
                    continue
                role = str(item.get("role", "")).strip()
                text = str(item.get("text", item.get("content", "")) or "").strip()
                cid = _client_id(item.get("client_id"))
                if role not in VALID_ROLES or not text:
                    invalid += 1
                    continue
                if cid and self._logged(db, cid):
                    skipped += 1
                    continue
                created = _iso_from_ms(item.get("t")) or _now()
                cur = db.execute(
                    "INSERT INTO messages(role, content, created_at, session_id, source) VALUES(?,?,?,?,?)",
                    (role, text, created, session_id, source))
                if cid:
                    self._log(db, cid, "message", cur.lastrowid)
                imported += 1
        return {"imported": imported, "skipped": skipped, "invalid": invalid}

    # ------------------------------------------------------------------ eşitleme günlüğü
    @staticmethod
    def _logged(db, client_id):
        row = db.execute("SELECT ref_id FROM sync_log WHERE client_id=?", (client_id,)).fetchone()
        return row

    @staticmethod
    def _log(db, client_id, kind, ref_id):
        db.execute("INSERT OR IGNORE INTO sync_log(client_id, kind, ref_id, created_at) VALUES(?,?,?,?)",
                   (client_id, kind, ref_id, _now()))

    # ------------------------------------------------------------------ kalıcı hafıza
    def add_memory(self, content, client_id=None):
        """Ekler. Dönüş {status: added | reactivated | exists | synced, id}; boş içerikte ValueError.

        Aynılık normalleştirilmiş metinle denetlenir (boşluk, büyük/küçük harf, ı/i, sondaki . !).
        Unutulmuş bir kayıt yeniden eklenirse geri gelir (reactivated).
        """
        content = " ".join(str(content or "").split())
        if not content:
            raise ValueError("Boş hafıza kaydı.")
        cid = _client_id(client_id)
        norm = turkish.normalize(content)
        with self._write() as db:
            if cid:
                logged = self._logged(db, cid)
                if logged:
                    return {"status": "synced", "id": logged[0]}
            row = db.execute(
                "SELECT id, active FROM memories WHERE norm=? OR content=? ORDER BY active DESC, id DESC LIMIT 1",
                (norm, content)).fetchone()
            if row and row[1]:
                result = {"status": "exists", "id": row[0]}
            elif row:
                db.execute("UPDATE memories SET active=1, created_at=? WHERE id=?", (_now(), row[0]))
                result = {"status": "reactivated", "id": row[0]}
            else:
                cur = db.execute("INSERT INTO memories(content, created_at, active, norm) VALUES(?,?,1,?)",
                                 (content, _now(), norm))
                result = {"status": "added", "id": cur.lastrowid}
            if cid:
                self._log(db, cid, "memory", result["id"])
            return result

    def update_memory(self, memory_id, content):
        """Düzenler. Dönüş {status: updated | merged, id}; kayıt yoksa None.

        Yeni metin başka bir kayıtla aynıysa ikisi birleşir (merged, kalan kaydın id'si döner).
        """
        content = " ".join(str(content or "").split())
        if not content:
            raise ValueError("Boş hafıza kaydı.")
        memory_id = int(memory_id)
        norm = turkish.normalize(content)
        with self._write() as db:
            if not db.execute("SELECT 1 FROM memories WHERE id=? AND active=1", (memory_id,)).fetchone():
                return None
            other = db.execute(
                "SELECT id, active FROM memories WHERE (norm=? OR content=?) AND id<>? "
                "ORDER BY active DESC, id DESC LIMIT 1", (norm, content, memory_id)).fetchone()
            if other:
                db.execute("UPDATE memories SET active=0 WHERE id=?", (memory_id,))
                if not other[1]:
                    db.execute("UPDATE memories SET active=1, created_at=? WHERE id=?", (_now(), other[0]))
                return {"status": "merged", "id": other[0]}
            db.execute("UPDATE memories SET content=?, norm=? WHERE id=?", (content, norm, memory_id))
            return {"status": "updated", "id": memory_id}

    def delete_memory(self, memory_id):
        """Unutur (active=0, "unut" ile aynı). Etkin bir kayıt silindiyse True."""
        with self._write() as db:
            cur = db.execute("UPDATE memories SET active=0 WHERE id=? AND active=1", (int(memory_id),))
            return cur.rowcount > 0

    def list_memories(self):
        """Etkin kayıtlar, en yeni önce: [{id, content, created_at}]."""
        with self._connect() as db:
            rows = db.execute(
                "SELECT id, content, created_at FROM memories WHERE active=1 ORDER BY id DESC").fetchall()
        return [{"id": r[0], "content": r[1], "created_at": r[2]} for r in rows]

    def save_memory(self, content):
        try:
            return self.add_memory(content)["status"] in ("added", "reactivated")
        except ValueError:
            return False

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
        # v2.4: Türkçe küçük harf (İ -> i, I -> ı); str.lower() "İ"yi "i̇" yapıyordu.
        lower = turkish.tr_lower if self.stemming else str.lower
        return {lower(t) for t in TOKEN_RE.findall(text or "") if len(t) >= 3}

    def _score(self, tokens, content):
        low = content.lower()
        if not self.stemming:
            return sum(1 for token in tokens if token in low)
        folded = turkish.fold(content)
        mem_words = turkish.words(content)
        score = 0
        for token in tokens:
            if turkish.fold(token) in turkish.STOPWORDS:
                continue                        # durak kelimeler puan getirmez
            if token in low or turkish.fold(token) in folded:   # v2.3 alt-dizi eşlemesi (yedek)
                score += 1
            elif any(turkish.words_match(token, w) for w in mem_words):
                score += 1
        return score

    def relevant_memories(self, query, limit=6):
        tokens = self._tokens(query)
        with self._connect() as db:
            rows = db.execute(
                "SELECT content FROM memories WHERE active=1 ORDER BY id DESC"
            ).fetchall()

        scored = []
        for (content,) in rows:
            score = self._score(tokens, content)
            if score:
                scored.append((score, content))

        scored.sort(key=lambda x: x[0], reverse=True)
        if scored:
            return [content for _, content in scored[:limit]]
        return [content for (content,) in rows[:4]]

    def build_context(self, query, session_id=None, recent_limit=8):
        return self.relevant_memories(query), self.recent_messages(recent_limit, session_id=session_id)
