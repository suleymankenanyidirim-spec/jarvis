"""Bilgi deposu (v2.8): JARVIS'in önce baktığı, kullanıcının dosya ve not bırakabildiği klasör.

- Varsayılan klasör config.KNOWLEDGE_DIR ("D:/JARVIS_Bilgi"); sürücü yoksa data/bilgi kullanılır.
- Klasöre .txt / .md / .csv / .json / .html (pypdf kuruluysa .pdf) dosyaları atılır; alt klasörler de taranır.
- "depoya kaydet …" denince ya da telefondan gönderilince notlar/ ve telefon/ alt klasörlerine yazılır.
- Dizin (index) JARVIS'in kendi data/ klasöründe SQLite'ta tutulur; dosya değişince o dosya yeniden okunur.
- Arama Türkçe ekleri hesaba katar (core/turkish.py): "kaskımın adresi" -> "Kaskın IP adresi …".
  Tamamen çevrimdışıdır; hiçbir şey internete gönderilmez.
"""
import json
import os
import re
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

from core import turkish
from core.paths import data_path

DOC_EXTS = {".txt", ".md", ".markdown", ".csv", ".json", ".log", ".html", ".htm", ".ini", ".yaml", ".yml"}
# v2.9: kod dosyaları da okunur (proje klasörlerindeki Python / Arduino / C++ kodu)
CODE_EXTS = {".py", ".ino", ".cpp", ".c", ".h", ".hpp", ".js", ".gd", ".bat", ".ps1"}
TEXT_EXTS = DOC_EXTS | CODE_EXTS
PDF_EXT = ".pdf"
UPLOAD_EXTS = TEXT_EXTS | {PDF_EXT}
# Proje klasörlerinde atlanan klasörler ve hassas dosyalar
SKIP_DIRS = {"libraries", "__pycache__", ".git", "node_modules", "build", "dist", ".pio", ".vscode", "data",
             "selfheal_data", "android", "venv", ".venv", "phone_ui"}
SKIP_FILE_RE = re.compile(r"config_local\.py$|wifi_ayarlar\.h$|secret|token|password|parola|sifre|şifre|\.db$", re.I)
_SECRET_RE = re.compile(r"((?:pass(?:word|wd)?|sifre|şifre|parola|token|secret|api_?key)\w*\s*(?:=|:)\s*)"
                        r"([\"'])[^\"'\n]{3,}\2", re.I)
MAX_CODE_BYTES = 256 * 1024     # çok büyük üretilmiş kod dosyaları (ör. yazı tipi tabloları) okunmaz


def mask_secrets(text):
    """Proje kodundaki şifre/anahtar değerlerini gizler: WIFI_PASS = "…" -> WIFI_PASS = "***"."""
    return _SECRET_RE.sub(lambda m: m.group(1) + m.group(2) + "***" + m.group(2), text)


CHUNK_CHARS = 700
README_NAME = "BENI_OKU.txt"
README_TEXT = """JARVIS BİLGİ DEPOSU
===================
Bu klasöre JARVIS'in bilmesini istediğin her şeyi koyabilirsin:
.txt, .md, .csv, .json, .html dosyaları (pypdf kuruluysa .pdf de).

- JARVIS bir soru gelince ÖNCE bu klasörde arar; burada yoksa internete bakar.
- Sohbette "depoya kaydet: …" dersen not olarak notlar/ klasörüne yazılır.
- Telefondan (Ayarlar → BİLGİ DEPOSU) gönderdiğin not ve dosyalar telefon/ klasörüne düşer.
- Dosyaları istediğin gibi düzenleyip silebilirsin; JARVIS değişiklikleri kendiliğinden görür.
- Hiçbir şey internete gönderilmez; her şey bu bilgisayarda kalır.
"""
_TAG_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>|<[^>]+>", re.S | re.I)
_SAFE_NAME_RE = re.compile(r"[^\w.\- ]+", re.UNICODE)


def _read_text(path):
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1254", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "ignore")


def read_document(path):
    """Dosyanın düz metni; okunamıyorsa ''."""
    ext = path.suffix.lower()
    try:
        if ext == PDF_EXT:
            try:
                from pypdf import PdfReader
            except ImportError:
                return ""
            return "\n\n".join((page.extract_text() or "") for page in PdfReader(str(path)).pages)
        text = _read_text(path)
        if ext in (".html", ".htm"):
            text = _TAG_RE.sub(" ", text)
        elif ext == ".json":
            try:
                text = json.dumps(json.loads(text), ensure_ascii=False, indent=1)
            except ValueError:
                pass
        return text
    except OSError:
        return ""


def chunk_text(text, size=CHUNK_CHARS):
    """Paragraflara böler, kısa paragrafları size karaktere kadar birleştirir."""
    parts = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    out, cur = [], ""
    for p in parts:
        while len(p) > size * 2:                 # çok uzun paragraf: cümle sınırından böl
            cut = p.rfind(". ", 0, size * 2)
            cut = cut + 1 if cut > size // 2 else size * 2
            out.append(p[:cut].strip())
            p = p[cut:].strip()
        if cur and len(cur) + len(p) + 2 > size:
            out.append(cur)
            cur = p
        else:
            cur = (cur + "\n\n" + p) if cur else p
    if cur:
        out.append(cur)
    return out


def _forms(text):
    forms = set()
    for w in set(turkish.words(text)):
        forms |= turkish.stem_forms(w)
    return forms


def safe_filename(name):
    name = Path(str(name or "").replace("\\", "/")).name.strip()
    name = _SAFE_NAME_RE.sub("_", name).strip(" .")
    return name[:120]


class KnowledgeBase:
    def __init__(self, root, index_path=None, refresh_seconds=30, min_score=0.5, max_file_mb=20, extra_dirs=()):
        self.root = Path(root).expanduser()
        # v2.9: salt okunur proje klasörleri (C:/JARVIS, Arduino projeleri …): "@<klasör adı>/…" olarak görünür
        self.extra_dirs = [Path(d).expanduser() for d in (extra_dirs or ())]
        names = [d.name for d in self.extra_dirs]
        # Aynı adlı klasörler (ör. iki ayrı "Kod") karışmasın: üst klasörün adı da eklenir
        self._labels = {d: (f"{d.parent.name}-{d.name}" if names.count(d.name) > 1 else d.name) for d in self.extra_dirs}
        self.index_path = Path(index_path) if index_path else data_path("knowledge_index.db")
        self.refresh_seconds = float(refresh_seconds)
        self.min_score = float(min_score)
        self.max_file_bytes = int(float(max_file_mb) * 1024 * 1024)
        self._lock = threading.RLock()
        self._last_refresh = 0.0
        self._ready = False

    # ------------------------------------------------------------------ kurulum
    def ensure(self):
        """Klasörleri ve dizini ilk kullanımda oluşturur (açılışta diske dokunulmaz)."""
        if self._ready:
            return
        with self._lock:
            if self._ready:
                return
            for sub in ("", "notlar", "telefon"):
                (self.root / sub).mkdir(parents=True, exist_ok=True)
            readme = self.root / README_NAME
            if not readme.exists():
                readme.write_text(README_TEXT, encoding="utf-8")
            self.index_path.parent.mkdir(parents=True, exist_ok=True)
            with self._db() as db:
                db.execute("CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, mtime REAL, size INTEGER)")
                db.execute("CREATE TABLE IF NOT EXISTS chunks(id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT, "
                           "ord INTEGER, text TEXT, forms TEXT)")
                db.execute("CREATE INDEX IF NOT EXISTS idx_chunks_path ON chunks(path)")
            self._ready = True

    def _db(self):
        db = sqlite3.connect(self.index_path, timeout=10)
        return _Closing(db)

    def _rel(self, path):
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return path.name

    def _walk(self, base, extra):
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and (not extra or d.lower() not in SKIP_DIRS)]
            for name in filenames:
                ext = Path(name).suffix.lower()
                if ext not in TEXT_EXTS and ext != PDF_EXT:
                    continue
                if name == README_NAME or name.startswith("."):
                    continue
                if extra and (SKIP_FILE_RE.search(name) or ext in (".pdf",)):
                    continue
                yield Path(dirpath) / name

    def _documents(self):
        """(yol, depo içi ad, proje klasörü mü) üçlüleri."""
        for p in self._walk(self.root, False):
            yield p, self._rel(p), False
        for base in self.extra_dirs:
            if not base.is_dir():
                continue
            for p in self._walk(base, True):
                yield p, "@" + self._labels[base] + "/" + p.relative_to(base).as_posix(), True

    # ------------------------------------------------------------------ dizin
    def refresh(self, force=False):
        """Değişen dosyaları yeniden okur, silinenleri dizinden atar. Dönüş: güncellenen dosya sayısı."""
        self.ensure()
        now = time.monotonic()
        if not force and now - self._last_refresh < self.refresh_seconds:
            return 0
        with self._lock:
            self._last_refresh = now
            changed = 0
            with self._db() as db:
                known = {row[0]: (row[1], row[2]) for row in db.execute("SELECT path, mtime, size FROM files")}
                seen = set()
                for p, rel, extra in self._documents():
                    seen.add(rel)
                    try:
                        st = p.stat()
                    except OSError:
                        continue
                    if known.get(rel) == (st.st_mtime, st.st_size):
                        continue
                    limit = MAX_CODE_BYTES if p.suffix.lower() in CODE_EXTS else self.max_file_bytes
                    text = read_document(p) if st.st_size <= limit else ""
                    if extra:
                        text = mask_secrets(text)
                    db.execute("DELETE FROM chunks WHERE path=?", (rel,))
                    for i, chunk in enumerate(chunk_text(text)):
                        db.execute("INSERT INTO chunks(path, ord, text, forms) VALUES(?,?,?,?)",
                                   (rel, i, chunk, " ".join(sorted(_forms(chunk)))))
                    db.execute("INSERT OR REPLACE INTO files(path, mtime, size) VALUES(?,?,?)",
                               (rel, st.st_mtime, st.st_size))
                    changed += 1
                for rel in set(known) - seen:
                    db.execute("DELETE FROM chunks WHERE path=?", (rel,))
                    db.execute("DELETE FROM files WHERE path=?", (rel,))
                    changed += 1
            return changed

    # ------------------------------------------------------------------ arama
    def search(self, query, limit=4):
        """En ilgili parçalar: [{path, text, score}] (score 0..1 = sorudaki anlamlı kelimelerin eşleşen oranı)."""
        qwords = turkish.query_words(query)
        if not qwords:
            return []
        self.refresh()
        qforms = [(w, turkish.stem_forms(w)) for w in qwords]
        scored = []
        with self._lock, self._db() as db:
            for path, text, forms in db.execute("SELECT path, text, forms FROM chunks"):
                fset = set(forms.split())
                folded = turkish.fold(text)
                hits = sum(1 for w, f in qforms if (f & fset) or (len(w) >= 4 and w in folded))
                if hits:
                    scored.append((hits / len(qwords), hits, path, text))
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        out = [{"path": p, "text": t, "score": round(s, 3)} for s, _h, p, t in scored if s >= self.min_score]
        return out[:limit]

    def context(self, hits, max_chars=3000):
        lines, used = [], 0
        for h in hits:
            piece = h["text"][: max(200, max_chars - used)]
            lines.append(f"[{h['path']}]\n{piece}")
            used += len(piece)
            if used >= max_chars:
                break
        return "\n\n".join(lines)

    # ------------------------------------------------------------------ ekleme
    def add_note(self, text, title="", source="sohbet"):
        """Notu notlar/notlar_YYYY-MM.md dosyasına ekler. Dönüş: depo içindeki göreli yol."""
        text = str(text or "").strip()
        if not text:
            raise ValueError("Boş not.")
        self.ensure()
        sub = "telefon" if source == "telefon" else "notlar"
        path = self.root / sub / f"notlar_{datetime.now():%Y-%m}.md"
        header = f"## {datetime.now():%Y-%m-%d %H:%M}" + (f" — {str(title).strip()}" if str(title or "").strip() else "")
        with self._lock:
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"{header}\n\n{text}\n\n")
            self.refresh(force=True)
        return self._rel(path)

    def save_upload(self, filename, data):
        """Telefondan gelen dosyayı telefon/ klasörüne yazar (üzerine yazmaz). Dönüş: göreli yol."""
        name = safe_filename(filename)
        ext = Path(name).suffix.lower()
        if not name or ext not in UPLOAD_EXTS:
            raise ValueError("Desteklenen dosyalar: " + ", ".join(sorted(UPLOAD_EXTS)))
        if len(data) > self.max_file_bytes:
            raise OverflowError("Dosya çok büyük.")
        self.ensure()
        target = self.root / "telefon" / name
        n = 1
        while target.exists():
            target = self.root / "telefon" / f"{Path(name).stem}_{n}{ext}"
            n += 1
        with self._lock:
            target.write_bytes(data)
            self.refresh(force=True)
        return self._rel(target)

    def list_files(self, limit=200):
        self.refresh()
        with self._lock, self._db() as db:
            rows = db.execute("SELECT f.path, f.size, f.mtime, COUNT(c.id) FROM files f LEFT JOIN chunks c "
                              "ON c.path=f.path GROUP BY f.path ORDER BY f.mtime DESC LIMIT ?", (int(limit),)).fetchall()
        return [{"path": p, "size": s, "modified": datetime.fromtimestamp(m).isoformat(timespec="seconds"),
                 "chunks": c} for p, s, m, c in rows]


class _Closing:
    """sqlite3 bağlantısını commit edip kapatır (Windows'ta dosya kilitli kalmasın)."""

    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self.db

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self.db.commit()
        finally:
            self.db.close()
        return False


def from_config(cfg):
    """config'teki ayarlarla depo; KNOWLEDGE_ENABLED False ise None. Sürücü yoksa data/bilgi."""
    if not cfg("KNOWLEDGE_ENABLED", True):
        return None
    root = Path(str(cfg("KNOWLEDGE_DIR", "D:/JARVIS_Bilgi") or "")).expanduser()
    if not str(root) or not root.anchor or not Path(root.anchor).exists():
        root = data_path("bilgi")
    extra = cfg("KNOWLEDGE_EXTRA_DIRS", []) or []
    if isinstance(extra, str):
        extra = [x for x in extra.split(";") if x.strip()]
    return KnowledgeBase(root, refresh_seconds=cfg("KNOWLEDGE_REFRESH_SECONDS", 30),
                         min_score=cfg("KNOWLEDGE_MIN_SCORE", 0.5), max_file_mb=cfg("KNOWLEDGE_MAX_FILE_MB", 20),
                         extra_dirs=extra)
