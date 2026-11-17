import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from datetime import datetime
from urllib.request import Request, urlopen

class SelfUpdater:
    """
    Safe/staged updater.
    Manifest format:
    {"version":"1.1.0","zip_url":"https://.../JARVIS.zip","sha256":"..."}
    It downloads and verifies an update, then writes UPDATE_READY.json.
    The running process is never overwritten.
    """
    def __init__(self, root=None):
        self.root = Path(root or Path(__file__).resolve().parents[1])
        self.state_file = self.root / "data" / "UPDATE_READY.json"
        self.state_file.parent.mkdir(parents=True, exist_ok=True)

    # Güncelleme paketinde olsa bile asla ezilmeyecek kullanıcı dosyaları
    PROTECTED = ("data/", "config_local.py")

    @staticmethod
    def github_manifest_url(repo):
        repo = (repo or "").strip().strip("/")
        if repo.startswith("https://github.com/"):
            repo = repo[len("https://github.com/"):]
        if repo.count("/") != 1:
            return ""
        return f"https://github.com/{repo}/releases/latest/download/manifest.json"

    def current_version(self):
        try:
            return (self.root / "VERSION").read_text(encoding="utf8").strip()
        except OSError:
            return "0"

    @staticmethod
    def _vtuple(v):
        out = []
        for part in str(v).lstrip("vV").split("."):
            num = "".join(ch for ch in part if ch.isdigit())
            out.append(int(num) if num else 0)
        return tuple(out + [0] * (4 - len(out)))

    def is_newer(self, version):
        return self._vtuple(version) > self._vtuple(self.current_version())

    def check(self, manifest_url):
        req = Request(manifest_url, headers={"User-Agent": "JARVIS-Updater/1.0"})
        with urlopen(req, timeout=15) as r:
            manifest = json.loads(r.read().decode("utf-8"))
        for key in ("version", "zip_url", "sha256"):
            if key not in manifest:
                raise ValueError(f"Manifest eksik: {key}")
        return manifest

    def apply_pending(self):
        """Apply a previously verified package at process start, with a rollback backup."""
        if not self.state_file.exists(): return "Güncelleme beklemiyor."
        state=json.loads(self.state_file.read_text(encoding="utf8"))
        if state.get("status") != "ready": return "Güncelleme hazır değil."
        package=Path(state.get("package",""))
        if not package.exists():
            self.state_file.unlink(missing_ok=True); return "Güncelleme paketi bulunamadı."
        backup=self.root / "data" / ("backup_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
        backup.mkdir(parents=True,exist_ok=True)
        try:
            with zipfile.ZipFile(package) as z:
                names=[n for n in z.namelist() if n and not n.endswith("/")
                       and not n.replace("\\", "/").startswith(self.PROTECTED)]
                # Zip path traversal koruması
                for n in names:
                    target=(self.root / n).resolve()
                    if self.root.resolve() not in target.parents and target != self.root.resolve():
                        raise ValueError("Güncelleme paketi güvenli olmayan yol içeriyor.")
                for n in names:
                    target=self.root / n
                    if target.exists():
                        b=backup / n; b.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(target,b)
                for n in names:
                    z.extract(n, self.root)
            state.update(status="applied", applied_at=datetime.now().isoformat(), backup=str(backup))
            self.state_file.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding="utf8")
            return f"JARVIS {state.get('version','')} güncellemesi uygulandı. Yedek: {backup.name}"
        except Exception:
            # Uygulama başarısızsa bir sonraki açılışta tekrar denemek yerine güvenli şekilde bırak.
            state["status"]="failed_apply"
            self.state_file.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding="utf8")
            raise

    def stage(self, manifest_url):
        manifest = self.check(manifest_url)
        if not self.is_newer(manifest["version"]):
            return f"JARVIS güncel (yüklü: {self.current_version()}, GitHub: {manifest['version']})."
        fd, tmp = tempfile.mkstemp(suffix=".zip")
        os.close(fd)
        try:
            req = Request(manifest["zip_url"], headers={"User-Agent": "JARVIS-Updater/1.0"})
            with urlopen(req, timeout=60) as src, open(tmp, "wb") as dst:
                shutil.copyfileobj(src, dst)
            digest = hashlib.sha256(Path(tmp).read_bytes()).hexdigest().lower()
            if digest != manifest["sha256"].lower():
                raise ValueError("Güncelleme SHA-256 doğrulamasından geçmedi.")
            # Basic zip integrity check.
            with zipfile.ZipFile(tmp) as z:
                bad = z.testzip()
                if bad:
                    raise ValueError(f"Bozuk güncelleme dosyası: {bad}")
            staged = self.root / "data" / f"JARVIS_update_{manifest['version']}.zip"
            shutil.move(tmp, staged)
            state = {"version": manifest["version"], "package": str(staged), "status": "ready"}
            self.state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            return f"{manifest['version']} güncellemesi indirildi ve doğrulandı. Bir sonraki başlatmada uygulanabilir."
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
