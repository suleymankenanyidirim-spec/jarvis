"""Sorun dedektörleri: sözdizimi, testler, çalışma zamanı logları, arka plan görevleri, sağlık."""
import json
import os
import re
import subprocess
import sys
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

RUNNER = Path(__file__).with_name("_testrunner.py")
FRAME_RE = re.compile(r'File "(?P<file>[^"]+)", line (?P<line>\d+), in (?P<func>[^\s]+)')
EXC_LINE_RE = re.compile(r"^(?P<exc>[A-Za-z_][\w.]*(?:Error|Exception|Exit|Interrupt|Warning)|[A-Za-z_][\w.]*Error)(?::\s?(?P<msg>.*))?$")
SECRET_ENV = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY")


@dataclass
class Issue:
    fingerprint: str
    kind: str            # sozdizimi | test | calisma | gorev | saglik | statik
    title: str
    detail: str
    file: str = ""       # hedef klasöre göre yol
    line: int = 0
    fixable: bool = True
    source: str = ""     # hangi dedektör buldu
    related_files: list = field(default_factory=list)


@dataclass
class TestRun:
    ran: int
    failures: dict       # test_id -> {"kind","trace"}
    passed: set
    output: str = ""
    timed_out: bool = False

    @property
    def failing(self):
        return set(self.failures)


def py_files(root: Path, ignore_dirs):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in ignore_dirs and not d.startswith(".")]
        for name in filenames:
            if name.endswith(".py"):
                yield Path(dirpath) / name


def rel(root: Path, path: Path) -> str:
    return Path(path).resolve().relative_to(root.resolve()).as_posix()


# --- 1) Sözdizimi ---
def check_syntax(root: Path, ignore_dirs, only=None):
    issues = []
    files = [root / f for f in only] if only else py_files(root, ignore_dirs)
    for path in files:
        if not path.exists():
            continue
        try:
            src = path.read_text(encoding="utf-8-sig")
            compile(src, str(path), "exec", dont_inherit=True)
        except UnicodeDecodeError as exc:
            r = rel(root, path)
            issues.append(Issue(f"sozdizimi:{r}", "sozdizimi", f"{r} UTF-8 okunamıyor", str(exc), r, 0, False, "sozdizimi"))
        except SyntaxError as exc:
            r = rel(root, path)
            issues.append(Issue(
                f"sozdizimi:{r}", "sozdizimi", f"{r} satır {exc.lineno}: {exc.msg}",
                f"SyntaxError: {exc.msg}\n  {r}:{exc.lineno}\n  {(exc.text or '').rstrip()}",
                r, exc.lineno or 0, True, "sozdizimi",
            ))
    return issues


# --- 2) Testler ---
def clean_env():
    env = {k: v for k, v in os.environ.items() if k not in SECRET_ENV}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["JARVIS_SELFHEAL_TEST"] = "1"
    return env


def run_tests(root: Path, timeout: int, pattern="test*.py") -> TestRun:
    cmd = [sys.executable, "-B", str(RUNNER), str(root), pattern]
    try:
        proc = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout, env=clean_env())
    except subprocess.TimeoutExpired:
        return TestRun(0, {"<zaman-asimi>": {"kind": "error", "trace": f"Testler {timeout} sn içinde bitmedi"}},
                       set(), "", True)
    out = proc.stdout or ""
    marker = out.rfind("===SELFHEAL_JSON===")
    if marker < 0:
        return TestRun(0, {"<test-calistirici>": {"kind": "error", "trace": (proc.stderr or out)[-6000:]}}, set(), out)
    data = json.loads(out[marker + len("===SELFHEAL_JSON==="):].strip().splitlines()[0])
    failures = {f["id"]: {"kind": f["kind"], "trace": f["trace"]} for f in data["failures"]}
    return TestRun(data["ran"], failures, set(data["passed"]), (proc.stderr or "")[-4000:])


def test_issues(root: Path, run: TestRun):
    issues = []
    for tid, info in run.failures.items():
        frames = parse_frames(info["trace"], root)
        related = [f for f, _, _ in frames]
        tfile = related[0] if related else ""
        last = frames[-1] if frames else ("", 0, "")
        issues.append(Issue(
            f"test:{tid}", "test", f"Test başarısız: {tid}", info["trace"],
            last[0] or tfile, last[1], True, "test", related_files=list(dict.fromkeys(related)),
        ))
    return issues


# --- 3) Çalışma zamanı logları (Traceback blokları) ---
def map_path(raw: str, root: Path):
    """Logdaki (Windows olabilir) yolu hedef klasördeki göreli yola çevirir."""
    raw = raw.replace("\\", "/")
    try:
        p = Path(raw).resolve()
        return rel(root, p)
    except Exception:
        pass
    parts = [x for x in raw.split("/") if x]
    for i in range(len(parts)):
        tail = parts[i:]
        # v2.1: "C:" gibi sürücü parçası içeren aday mutlak yola dönüşür ve hedefin
        # DIŞINDAKİ dosyayı (ör. gerçek C:\JARVIS) eşleyebilirdi; ".." da dışarı çıkar.
        if not tail or any(":" in x or x == ".." for x in tail):
            continue
        cand = "/".join(tail)
        if (root / cand).is_file():
            return cand
    return None


def parse_frames(trace: str, root: Path):
    frames = []
    for m in FRAME_RE.finditer(trace or ""):
        r = map_path(m.group("file"), root)
        if r:
            frames.append((r, int(m.group("line")), m.group("func")))
    return frames


def exception_line(block: str):
    for line in reversed(block.strip().splitlines()):
        m = EXC_LINE_RE.match(line.strip())
        if m:
            return m.group("exc"), (m.group("msg") or "").strip()
    last = block.strip().splitlines()[-1] if block.strip() else ""
    return "Hata", last.strip()


def split_tracebacks(text: str):
    blocks, cur = [], None
    for line in text.splitlines():
        if line.startswith("Traceback (most recent call last)"):
            if cur:
                blocks.append("\n".join(cur))
            cur = [line]
        elif cur is not None:
            cur.append(line)
            if line and not line.startswith((" ", "\t")) and not line.startswith("Traceback") and \
                    not line.startswith(("During handling", "The above exception")):
                blocks.append("\n".join(cur))
                cur = None
    if cur:
        blocks.append("\n".join(cur))
    return blocks


def traceback_issue(block: str, root: Path, source: str):
    frames = parse_frames(block, root)
    exc, msg = exception_line(block)
    if frames:
        f, line, func = frames[-1]
        fp = f"calisma:{exc}:{f}:{func}"
        title = f"{exc} — {f} ({func}): {msg[:120]}"
    else:
        f, line, fp = "", 0, f"calisma:{exc}:{msg[:80]}"
        title = f"{exc}: {msg[:120]}"
    return Issue(fp, "calisma", title, block[-6000:], f, line, bool(frames), source,
                 related_files=list(dict.fromkeys(x for x, _, _ in frames)))


class LogWatcher:
    """Log dosyalarını kaldığı yerden okur; yeni Traceback'leri sorun olarak döndürür."""

    def __init__(self, state_path: Path):
        self.state_path = state_path
        try:
            self.offsets = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            self.offsets = {}

    def scan(self, paths, root: Path):
        issues = []
        for path in paths:
            path = Path(path)
            if not path.is_file():
                continue
            key = str(path.resolve())
            size = path.stat().st_size
            start = self.offsets.get(key, 0)
            if start > size:  # dosya döndürülmüş/sıfırlanmış
                start = 0
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                fh.seek(start)
                text = fh.read()
                self.offsets[key] = fh.tell()
            for block in split_tracebacks(text):
                issues.append(traceback_issue(block, root, "log:" + path.name))
        self.state_path.write_text(json.dumps(self.offsets), encoding="utf-8")
        return issues


# --- 4) JARVIS arka plan görevleri ---
def task_issues(root: Path):
    path = root / "data" / "background_tasks.json"
    try:
        tasks = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    issues = []
    for t in (tasks or {}).values():
        if t.get("status") != "failed":
            continue
        tb = t.get("traceback") or ""
        if tb:
            issue = traceback_issue(tb, root, "gorev")
            issue.title = f"Görev '{t.get('title', '')}' çöktü: " + issue.title
            issues.append(issue)
        else:
            err = str(t.get("error", ""))[:200]
            issues.append(Issue(f"gorev:{err[:80]}", "gorev", f"Görev başarısız: {t.get('title', '')}",
                                err, fixable=False, source="gorev"))
    return issues


# --- 5) Sağlık ---
def ollama_health(url: str):
    base = url.split("/api/")[0]
    try:
        with urllib.request.urlopen(base + "/api/tags", timeout=5) as r:
            r.read(200)
        return []
    except Exception as exc:
        return [Issue("saglik:ollama", "saglik", "Ollama'ya ulaşılamıyor",
                      f"{base} cevap vermedi: {exc}\nÇözüm: Ollama'yı başlat (ollama serve).",
                      fixable=False, source="saglik")]


# --- 6) Statik analiz (pyflakes kuruluysa) ---
def pyflakes_issues(root: Path, ignore_dirs):
    try:
        from pyflakes import api, reporter  # type: ignore
    except ImportError:
        return []
    import io
    issues = []
    for path in py_files(root, ignore_dirs):
        out = io.StringIO()
        api.checkPath(str(path), reporter.Reporter(out, io.StringIO()))
        for line in out.getvalue().splitlines():
            if "undefined name" in line:
                r = rel(root, path)
                m = re.match(r".*?:(\d+):(?:\d+:)?\s*(.*)", line)
                ln, msg = (int(m.group(1)), m.group(2)) if m else (0, line)
                issues.append(Issue(f"statik:{r}:{msg}", "statik", f"{r}:{ln} {msg}", line, r, ln, True, "pyflakes"))
    return issues
