"""LLM'in önerdiği değişikliği doğrular ve uygular.

Format (JSON):
{"explanation": "...",
 "edits": [{"path": "core/x.py", "search": "<dosyadaki birebir metin>", "replace": "<yeni metin>"}],
 "new_files": [{"path": "tests/auto/test_x.py", "content": "..."}]}
"""
import ast
import difflib
import fnmatch
import shutil
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

# LLM'in eklediği kodda bu çağrılar/modüller yeni olarak görünürse değişiklik reddedilir.
DANGEROUS_NAMES = {
    "subprocess", "os.system", "os.popen", "os.remove", "os.unlink", "os.rmdir", "os.removedirs",
    "shutil.rmtree", "shutil.move", "eval", "exec", "__import__", "ctypes", "socket", "winreg",
    "pyautogui", "requests.post", "urllib.request", "os.kill", "Path.unlink", "rmdir", "unlink",
}


class PatchError(ValueError):
    pass


@dataclass
class Edit:
    path: str
    search: str
    replace: str


@dataclass
class NewFile:
    path: str
    content: str


@dataclass
class PatchSet:
    explanation: str
    edits: list = field(default_factory=list)
    new_files: list = field(default_factory=list)
    diff: str = ""

    @property
    def touched(self):
        return sorted({e.path for e in self.edits})

    @property
    def created(self):
        return [n.path for n in self.new_files]


def _norm_path(p: str) -> str:
    p = str(p or "").replace("\\", "/").strip()
    while p.startswith("./"):
        p = p[2:]
    pp = PurePosixPath(p)
    if not p or pp.is_absolute() or ".." in pp.parts or ":" in p:
        raise PatchError(f"Geçersiz yol: {p!r}")
    return pp.as_posix()


def _match(path, globs):
    return any(fnmatch.fnmatch(path, g) for g in globs)


def _names_in(code: str):
    """Koddaki çağrı/isim/import adlarını kaba şekilde toplar (parse edilemezse metin araması)."""
    names = set()
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return {n for n in DANGEROUS_NAMES if n in code}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{a.name}" for a in node.names)
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            parts, cur = [node.attr], node.value
            while isinstance(cur, ast.Attribute):
                parts.append(cur.attr)
                cur = cur.value
            if isinstance(cur, ast.Name):
                parts.append(cur.id)
            dotted = ".".join(reversed(parts))
            names.add(dotted)
            names.add(node.attr)
    return names


def dangerous_additions(old: str, new: str):
    def hits(code):
        found = set()
        for n in _names_in(code):
            for d in DANGEROUS_NAMES:
                if n == d or n.startswith(d + ".") or n.endswith("." + d):
                    found.add(d)
        return found
    return sorted(hits(new) - hits(old))


def parse_patch(data: dict, root: Path, settings) -> PatchSet:
    if not isinstance(data, dict):
        raise PatchError("Cevap JSON nesnesi değil")
    ps = PatchSet(str(data.get("explanation", "")).strip()[:2000])
    for e in data.get("edits") or []:
        path = _norm_path(e.get("path"))
        if _match(path, settings.protected_globs):
            raise PatchError(f"Korunan dosya değiştirilemez: {path} (testleri değiştirerek 'düzeltme' yasak)")
        if not path.endswith(".py"):
            raise PatchError(f"Sadece .py dosyaları düzenlenebilir: {path}")
        search, replace = str(e.get("search", "")), str(e.get("replace", ""))
        if not search:
            raise PatchError(f"{path}: 'search' boş olamaz")
        ps.edits.append(Edit(path, search, replace))
    for n in data.get("new_files") or []:
        path = _norm_path(n.get("path"))
        if not _match(path, settings.new_file_globs):
            raise PatchError(f"Yeni dosyaya izin yok: {path} (izinli: {settings.new_file_globs})")
        if (root / path).exists():
            raise PatchError(f"Dosya zaten var: {path}")
        ps.new_files.append(NewFile(path, str(n.get("content", ""))))
    if not ps.edits and not ps.new_files:
        raise PatchError("Değişiklik yok")
    if len(set(ps.touched) | set(ps.created)) > settings.max_files_per_fix:
        raise PatchError(f"Çok fazla dosya ({settings.max_files_per_fix} sınırı)")
    ps.diff = render(ps, root, settings)
    return ps


def _apply_edits_in_memory(ps: PatchSet, root: Path):
    """{yol: (eski, yeni)} döndürür; arama metni birebir ve tek olmalı."""
    result = {}
    for e in ps.edits:
        path = root / e.path
        if e.path not in result:
            if not path.is_file():
                raise PatchError(f"Dosya yok: {e.path}")
            text = path.read_text(encoding="utf-8")
            result[e.path] = [text, text]
        cur = result[e.path][1]
        search = e.search.replace("\r\n", "\n")
        cur_n = cur.replace("\r\n", "\n")
        count = cur_n.count(search)
        if count == 0:
            # Sık görülen LLM hatası: baştaki/sondaki boşluklar.
            stripped = search.strip("\n")
            if stripped and cur_n.count(stripped) == 1:
                search = stripped
                e.replace = e.replace.strip("\n")
                count = 1
        if count != 1:
            raise PatchError(f"{e.path}: aranan metin {'bulunamadı' if count == 0 else f'{count} kez geçiyor'}")
        result[e.path][1] = cur_n.replace(search, e.replace, 1)
    return {k: tuple(v) for k, v in result.items()}


def render(ps: PatchSet, root: Path, settings) -> str:
    changed = _apply_edits_in_memory(ps, root)
    diff_lines, changed_count = [], 0
    for path, (old, new) in changed.items():
        bad = dangerous_additions(old, new)
        if bad:
            raise PatchError(f"{path}: güvenlik nedeniyle reddedildi, yeni eklenen riskli çağrılar: {bad}")
        d = list(difflib.unified_diff(old.splitlines(), new.splitlines(), f"a/{path}", f"b/{path}", lineterm=""))
        changed_count += sum(1 for x in d if x[:1] in "+-" and not x.startswith(("+++", "---")))
        diff_lines += d
    for nf in ps.new_files:
        bad = dangerous_additions("", nf.content)
        if bad:
            raise PatchError(f"{nf.path}: güvenlik nedeniyle reddedildi, riskli çağrılar: {bad}")
        diff_lines += list(difflib.unified_diff([], nf.content.splitlines(), "/dev/null", f"b/{nf.path}", lineterm=""))
    if changed_count > settings.max_changed_lines:
        raise PatchError(f"Değişiklik çok büyük: {changed_count} satır (sınır {settings.max_changed_lines})")
    return "\n".join(diff_lines)


def apply(ps: PatchSet, root: Path, backup_dir: Path = None):
    """Değişikliği diske yazar. backup_dir verilirse orijinaller oraya kopyalanır."""
    changed = _apply_edits_in_memory(ps, root)
    if backup_dir:
        for path in changed:
            dst = backup_dir / path
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / path, dst)
    for path, (_, new) in changed.items():
        _atomic_write(root / path, new)
    for nf in ps.new_files:
        write_new_file(root, nf)


def write_new_file(root: Path, nf):
    """Yeni test dosyasını yazar; unittest bulabilsin diye eksik __init__.py dosyalarını da ekler."""
    target = root / nf.path
    target.parent.mkdir(parents=True, exist_ok=True)
    parent = target.parent
    while parent != root and root in parent.parents:
        init = parent / "__init__.py"
        if not init.exists():
            init.write_text("", encoding="utf-8")
        parent = parent.parent
    _atomic_write(target, nf.content)


def _atomic_write(path: Path, text: str):
    tmp = path.with_name(path.name + ".selfheal_tmp")
    # v2.1: Path.write_text(newline=) Python 3.10+ ister; `py` başlatıcısı 3.9 olabilir.
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    tmp.replace(path)


def restore(root: Path, backup_dir: Path, files, created):
    for path in files:
        src = backup_dir / path
        if src.exists():
            shutil.copy2(src, root / path)
    for path in created:
        (root / path).unlink(missing_ok=True)
