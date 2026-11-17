"""Kalıcı sorun takibi (SQLite). Her sorun, her deneme ve her uygulanan değişiklik kaydedilir;
başarısız denemelerin nedenleri bir sonraki denemede LLM'e geri verilir (deneyimden öğrenme)."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

OPEN = "acik"
FIXED = "duzeltildi"
GONE = "kendiliginden_gitti"
PROPOSED = "oneri_hazir"
HUMAN = "insan_gerekli"
OBSERVED = "izleniyor"   # kodla düzeltilemez (ör. Ollama kapalı)


def now():
    return datetime.now(timezone.utc)


def iso(dt=None):
    return (dt or now()).isoformat(timespec="seconds")


class IssueStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        with self._db() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS issues(
                fingerprint TEXT PRIMARY KEY, kind TEXT, title TEXT, detail TEXT,
                file TEXT, line INTEGER, source TEXT, related TEXT, fixable INTEGER,
                status TEXT, attempts INTEGER DEFAULT 0, seen_count INTEGER DEFAULT 0,
                first_seen TEXT, last_seen TEXT, next_try_at TEXT, last_error TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS attempts(
                id INTEGER PRIMARY KEY AUTOINCREMENT, fingerprint TEXT, ts TEXT,
                provider TEXT, outcome TEXT, reason TEXT, diff TEXT
            );
            CREATE TABLE IF NOT EXISTS changes(
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, fingerprint TEXT, title TEXT,
                backup_dir TEXT, files TEXT, created TEXT, status TEXT, diff TEXT
            );
            """)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.db_path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        finally:
            db.close()

    # --- sorunlar ---
    def upsert(self, issue):
        with self._db() as db:
            row = db.execute("SELECT status FROM issues WHERE fingerprint=?", (issue.fingerprint,)).fetchone()
            base_status = OPEN if issue.fixable else OBSERVED
            if row is None:
                db.execute(
                    "INSERT INTO issues(fingerprint,kind,title,detail,file,line,source,related,fixable,status,"
                    "seen_count,first_seen,last_seen,next_try_at) VALUES(?,?,?,?,?,?,?,?,?,?,1,?,?,?)",
                    (issue.fingerprint, issue.kind, issue.title, issue.detail, issue.file, issue.line,
                     issue.source, json.dumps(issue.related_files), int(issue.fixable), base_status,
                     iso(), iso(), iso()),
                )
                return True
            status = row["status"]
            # Düzeldi sanılan sorun geri döndüyse yeniden aç.
            if status in (FIXED, GONE):
                status = base_status
            db.execute(
                "UPDATE issues SET title=?,detail=?,file=?,line=?,related=?,seen_count=seen_count+1,"
                "last_seen=?,status=? WHERE fingerprint=?",
                (issue.title, issue.detail, issue.file, issue.line, json.dumps(issue.related_files),
                 iso(), status, issue.fingerprint),
            )
            return False

    def resolve_missing(self, sources_scanned, present_fps):
        """Bu turda taranan kaynaklarda artık görünmeyen açık sorunları kapatır.
        (Log kaynaklı sorunlar tekrar görülmeyebilir; onlar sadece düzeltmeyle kapanır.)"""
        closed = []
        with self._db() as db:
            for r in db.execute("SELECT fingerprint, source, status FROM issues WHERE status IN (?,?,?,?)",
                                (OPEN, OBSERVED, PROPOSED, HUMAN)).fetchall():
                if r["source"] in sources_scanned and r["fingerprint"] not in present_fps:
                    closed.append(r["fingerprint"])
            for fp in closed:
                db.execute("UPDATE issues SET status=? WHERE fingerprint=?", (GONE, fp))
        return closed

    def next_fixable(self, max_attempts):
        order = "CASE kind WHEN 'sozdizimi' THEN 0 WHEN 'test' THEN 1 WHEN 'statik' THEN 2 ELSE 3 END"
        with self._db() as db:
            return db.execute(
                f"SELECT * FROM issues WHERE status=? AND fixable=1 AND attempts<? AND next_try_at<=? "
                f"ORDER BY {order}, seen_count DESC LIMIT 1", (OPEN, max_attempts, iso()),
            ).fetchone()

    def get(self, fp):
        with self._db() as db:
            return db.execute("SELECT * FROM issues WHERE fingerprint=?", (fp,)).fetchone()

    def set_status(self, fp, status, error=""):
        with self._db() as db:
            db.execute("UPDATE issues SET status=?, last_error=? WHERE fingerprint=?", (status, error, fp))

    def record_attempt(self, fp, provider, outcome, reason, diff, max_attempts, backoff_minutes):
        with self._db() as db:
            db.execute("INSERT INTO attempts(fingerprint,ts,provider,outcome,reason,diff) VALUES(?,?,?,?,?,?)",
                       (fp, iso(), provider, outcome, reason[:4000], diff[:20000]))
            if outcome in ("uygulandi", "oneri"):
                return
            row = db.execute("SELECT attempts FROM issues WHERE fingerprint=?", (fp,)).fetchone()
            attempts = (row["attempts"] if row else 0) + 1
            wait = timedelta(minutes=backoff_minutes * (2 ** (attempts - 1)))
            status = HUMAN if attempts >= max_attempts else OPEN
            db.execute("UPDATE issues SET attempts=?, next_try_at=?, status=?, last_error=? WHERE fingerprint=?",
                       (attempts, iso(now() + wait), status, reason[:1000], fp))

    def past_attempts(self, fp, limit=4):
        with self._db() as db:
            return db.execute("SELECT * FROM attempts WHERE fingerprint=? AND outcome NOT IN ('uygulandi','oneri') "
                              "ORDER BY id DESC LIMIT ?", (fp, limit)).fetchall()

    def summary(self):
        with self._db() as db:
            counts = {r["status"]: r["n"] for r in db.execute("SELECT status, COUNT(*) n FROM issues GROUP BY status")}
            issues = db.execute("SELECT * FROM issues ORDER BY last_seen DESC LIMIT 40").fetchall()
            attempts = db.execute("SELECT * FROM attempts ORDER BY id DESC LIMIT 15").fetchall()
            changes = db.execute("SELECT * FROM changes ORDER BY id DESC LIMIT 15").fetchall()
        return counts, issues, attempts, changes

    # --- uygulanan değişiklikler (geri alma için) ---
    def add_change(self, fp, title, backup_dir, files, created, diff):
        with self._db() as db:
            cur = db.execute(
                "INSERT INTO changes(ts,fingerprint,title,backup_dir,files,created,status,diff) VALUES(?,?,?,?,?,?,?,?)",
                (iso(), fp, title, str(backup_dir), json.dumps(files), json.dumps(created), "aktif", diff[:20000]))
            return cur.lastrowid

    def last_active_change(self):
        with self._db() as db:
            return db.execute("SELECT * FROM changes WHERE status='aktif' ORDER BY id DESC LIMIT 1").fetchone()

    def set_change_status(self, change_id, status):
        with self._db() as db:
            db.execute("UPDATE changes SET status=? WHERE id=?", (status, change_id))
