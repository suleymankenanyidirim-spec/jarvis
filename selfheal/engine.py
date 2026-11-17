"""Kendini geliştirme döngüsü:  TARA -> SEÇ -> DÜZELTME ÜRET -> KUM HAVUZUNDA DOĞRULA -> UYGULA -> İZLE / GERİ AL."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

from . import detectors as D
from . import patcher as P
from .issues import FIXED, GONE, HUMAN, PROPOSED, IssueStore
from .llm import LLM, LLMError, extract_json
from .report import write_report

SYSTEM_PROMPT = """Sen JARVIS'in kendini onarma mühendisisin. Görevin: verilen sorunu EN KÜÇÜK ve GÜVENLİ kod değişikliğiyle düzeltmek.
Kurallar:
- Sadece JSON döndür, başka hiçbir şey yazma.
- "search" alanı dosyadaki metnin BİREBİR kopyası olmalı (boşluklar ve girintiler dahil) ve dosyada tek bir kez geçmeli. Birkaç satır bağlam ekle.
- tests/ altındaki mevcut testleri DEĞİŞTİREMEZSİN. Hatayı testi değiştirerek değil, kodu düzelterek gider.
- Yeni dosya sadece tests/auto/test_*.py olarak oluşturulabilir (unittest kullan, pytest değil).
- Ağ, dosya silme, subprocess, eval/exec, pano/klavye otomasyonu EKLEME.
- Davranışı gereksiz değiştirme, yeniden yazma yapma, yorumları ve Türkçe metinleri koru.
- Emin değilsen "edits": [] döndür ve explanation'da nedenini yaz.
Çıktı biçimi:
{"explanation": "kısa Türkçe açıklama", "edits": [{"path": "core/dosya.py", "search": "...", "replace": "..."}], "new_files": [{"path": "tests/auto/test_auto_ornek.py", "content": "..."}]}"""


def _slug(text, n=40):
    return re.sub(r"[^A-Za-z0-9_]+", "_", text)[:n].strip("_") or "sorun"


class Engine:
    def __init__(self, settings, llm=None, log=None):
        self.s = settings
        self.root = settings.target
        self.data = settings.data
        for sub in ("backups", "oneriler", "logs"):
            (self.data / sub).mkdir(parents=True, exist_ok=True)
        self.store = IssueStore(self.data / "selfheal.db")
        self.llm = llm or LLM(settings)
        self.logs = D.LogWatcher(self.data / "log_offsets.json")
        self._log = log or (lambda msg: print(msg, flush=True))
        self.lock = threading.RLock()
        self.last_apply_time = 0.0
        self.on_change_applied = None   # gözetmen bağlanırsa uygulamayı yeniden başlatır
        try:
            self.last_scan = json.loads((self.data / "last_scan.json").read_text(encoding="utf-8"))
        except Exception:
            self.last_scan = {}

    # ------------------------------------------------------------------ yardımcılar
    def log(self, msg, **event):
        stamp = datetime.now().strftime("%H:%M:%S")
        self._log(f"[{stamp}] {msg}")
        rec = {"ts": datetime.now().isoformat(timespec="seconds"), "msg": msg, **event}
        with open(self.data / "journal.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def log_paths(self):
        paths = list((self.data / "logs").glob("*.log"))
        if (self.root / "logs").is_dir():
            paths += list((self.root / "logs").glob("*.log"))
        return paths

    def make_sandbox(self):
        tmp = Path(tempfile.mkdtemp(prefix="jarvis_kumhavuzu_"))
        dst = tmp / "app"
        ignore = shutil.ignore_patterns(*self.s.ignore_dirs, "*.pyc", "*.selfheal_tmp")
        shutil.copytree(self.root, dst, ignore=ignore)
        return tmp, dst

    # ------------------------------------------------------------------ 1) tarama
    def scan(self):
        issues, sources = [], {"sozdizimi", "test", "gorev", "saglik", "pyflakes"}
        issues += D.check_syntax(self.root, self.s.ignore_dirs)
        baseline = D.run_tests(self.root, self.s.test_timeout_seconds)
        if baseline.timed_out:
            sources.discard("test")
        else:
            issues += D.test_issues(self.root, baseline)
        issues += self.logs.scan(self.log_paths(), self.root)
        issues += D.task_issues(self.root)
        if self.s.check_ollama_health and "ollama" in self.s.llm_order:
            issues += D.ollama_health(self.s.ollama_url)
        issues += D.pyflakes_issues(self.root, self.s.ignore_dirs)
        new = [i for i in issues if self.store.upsert(i)]
        gone = self.store.resolve_missing(sources, {i.fingerprint for i in issues})
        self.last_scan = {"tests_ran": baseline.ran, "tests_failing": len(baseline.failing),
                          "found": len(issues), "new": len(new), "gone": len(gone)}
        (self.data / "last_scan.json").write_text(json.dumps(self.last_scan), encoding="utf-8")
        for i in new:
            self.log(f"YENİ SORUN [{i.kind}] {i.title}", event="sorun", fp=i.fingerprint)
        for fp in gone:
            self.log(f"Sorun artık görünmüyor: {fp}", event="gitti", fp=fp)
        return issues, baseline

    # ------------------------------------------------------------------ 2) düzeltme üret
    def _file_context(self, row):
        files = []
        for f in [row["file"]] + json.loads(row["related"] or "[]"):
            if f and f not in files and (self.root / f).is_file() and f.endswith(".py"):
                files.append(f)
        files = files[:4]
        budget = self.s.max_context_chars // max(1, len(files))
        chunks = []
        for f in files:
            text = (self.root / f).read_text(encoding="utf-8", errors="replace")
            tag = " (KORUNAN — DEĞİŞTİRME, sadece oku)" if P._match(f, self.s.protected_globs) else ""
            if len(text) > budget:
                lines = text.splitlines()
                center = row["line"] if f == row["file"] and row["line"] else 1
                half = max(20, budget // 160)
                a, b = max(0, center - half), min(len(lines), center + half)
                text = f"# ... (satır {a + 1}-{b} arası gösteriliyor, dosya {len(lines)} satır)\n" + "\n".join(lines[a:b])
            chunks.append(f"===== DOSYA: {f}{tag} =====\n{text}")
        return "\n\n".join(chunks)

    def build_prompt(self, row):
        parts = [f"SORUN TÜRÜ: {row['kind']}\nBAŞLIK: {row['title']}\n\nAYRINTI:\n{row['detail'][-5000:]}"]
        if row["kind"] in ("calisma", "gorev") and self.s.require_repro_test_for_runtime:
            parts.append("ZORUNLU: Bu bir çalışma zamanı hatası. new_files ile tests/auto/test_auto_*.py altında "
                         "hatayı YENİDEN ÜRETEN bir unittest ekle: test, düzeltmeden önce başarısız olmalı, "
                         "düzeltmeden sonra geçmeli. Test ağ, GUI, ses, donanım kullanmamalı; gerekirse tempfile kullan.")
        past = self.store.past_attempts(row["fingerprint"])
        if past:
            parts.append("ÖNCEKİ BAŞARISIZ DENEMELER (aynı hatayı tekrarlama):\n" +
                         "\n".join(f"- {a['outcome']}: {a['reason'][:400]}" for a in past))
        parts.append("İLGİLİ KOD:\n" + self._file_context(row))
        return "\n\n".join(parts)

    # ------------------------------------------------------------------ 3) doğrulama
    def verify(self, ps, row, baseline):
        """Değişikliği kum havuzunda dener. (tamam, neden) döndürür."""
        tmp, box = self.make_sandbox()
        try:
            if row is not None and row["kind"] in ("calisma", "gorev") and self.s.require_repro_test_for_runtime:
                if not ps.new_files:
                    return False, "Çalışma hatası için hatayı yeniden üreten test eklenmedi"
                # Testler düzeltmeden ÖNCE başarısız olmalı (gerçekten hatayı yakalıyor mu?)
                for nf in ps.new_files:
                    P.write_new_file(box, nf)
                before = D.run_tests(box, self.s.test_timeout_seconds)
                new_ids = self._ids_from(ps.created, before)
                if not new_ids:
                    return False, "Yeni test hiç çalışmadı (unittest.TestCase sınıfı yok veya içe aktarılamadı)"
                if not (new_ids & before.failing):
                    return False, "Yeni test düzeltmeden önce de geçiyor; hatayı yakalamıyor"
                for nf in ps.new_files:
                    (box / nf.path).unlink()
            P.apply(ps, box)
            bad = D.check_syntax(box, self.s.ignore_dirs, only=ps.touched + ps.created)
            if bad:
                return False, "Sözdizimi hatası: " + bad[0].title
            after = D.run_tests(box, self.s.test_timeout_seconds)
            if after.timed_out:
                return False, "Testler zaman aşımına uğradı"
            regress = after.failing - baseline.failing
            if regress:
                first = sorted(regress)[0]
                return False, f"Yeni bozulan test(ler): {sorted(regress)[:5]}\n{after.failures[first]['trace'][-1500:]}"
            new_ids = self._ids_from(ps.created, after)
            if ps.created and not new_ids:
                return False, "Eklenen test çalışmadı"
            if row is None:
                return True, f"{len(new_ids)} yeni test geçti, {after.ran} testin hiçbiri bozulmadı"
            kind, fp = row["kind"], row["fingerprint"]
            if kind == "sozdizimi" and D.check_syntax(box, self.s.ignore_dirs, only=[row["file"]]):
                return False, "Sözdizimi hatası hâlâ var"
            if kind == "test":
                tid = fp[len("test:"):]
                if tid in after.failing:
                    return False, "Hedef test hâlâ başarısız:\n" + after.failures[tid]["trace"][-1500:]
                if tid not in after.passed and not tid.startswith("<"):
                    return False, "Hedef test çalışmadı"
            if kind == "statik" and any(i.fingerprint == fp for i in D.pyflakes_issues(box, self.s.ignore_dirs)):
                return False, "Statik analiz hatası hâlâ var"
            return True, f"Doğrulandı: {after.ran} test çalıştı, yeni bozulma yok"
        except P.PatchError as exc:
            return False, str(exc)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    @staticmethod
    def _ids_from(created_paths, run):
        mods = [p[:-3].replace("/", ".") for p in created_paths if p.endswith(".py")]
        stems = [m.rsplit(".", 1)[-1] for m in mods]
        all_ids = run.passed | run.failing
        return {t for t in all_ids
                if any(t.startswith(m + ".") for m in mods)
                or any(t.endswith("." + s) or f".{s}." in t for s in stems)}

    # ------------------------------------------------------------------ 4) uygulama / geri alma
    def apply_live(self, ps, fp, title, baseline):
        with self.lock:
            backup = self.data / "backups" / f"{datetime.now():%Y%m%d_%H%M%S}_{_slug(fp, 30)}"
            backup.mkdir(parents=True, exist_ok=True)
            P.apply(ps, self.root, backup)
            (backup / "DEGISIKLIK.diff").write_text(ps.diff, encoding="utf-8")
            change_id = self.store.add_change(fp, title, backup, ps.touched, ps.created, ps.diff)
            # Canlı kontrol: gerçek klasörde de bozulma yok mu?
            bad = D.check_syntax(self.root, self.s.ignore_dirs, only=ps.touched + ps.created)
            after = D.run_tests(self.root, self.s.test_timeout_seconds)
            regress = (after.failing - baseline.failing) if not after.timed_out else {"<zaman-asimi>"}
            if bad or regress:
                self.rollback(change_id, f"Canlı kontrolde sorun: {bad[0].title if bad else sorted(regress)[:3]}")
                return False
            self.last_apply_time = time.time()
            return True

    def rollback(self, change_id=None, reason="elle geri alındı"):
        with self.lock:
            ch = self.store.last_active_change() if change_id is None else None
            if change_id is not None:
                with self.store._db() as db:
                    ch = db.execute("SELECT * FROM changes WHERE id=?", (change_id,)).fetchone()
            if not ch:
                return None
            P.restore(self.root, Path(ch["backup_dir"]), json.loads(ch["files"]), json.loads(ch["created"]))
            self.store.set_change_status(ch["id"], "geri_alindi")
            if ch["fingerprint"] and not ch["fingerprint"].startswith("testgen:"):
                self.store.record_attempt(ch["fingerprint"], "-", "geri_alindi", reason, ch["diff"] or "",
                                          self.s.max_attempts_per_issue, self.s.retry_backoff_minutes)
            self.log(f"GERİ ALINDI: {ch['title']} — {reason}", event="geri_al", change=ch["id"])
            return ch

    def on_app_crash(self):
        """Gözetmen çağırır: düzeltmeden hemen sonra çökme varsa son değişikliği geri al."""
        if self.last_apply_time and time.time() - self.last_apply_time < self.s.probation_seconds:
            self.last_apply_time = 0.0
            return self.rollback(reason="Uygulama düzeltmeden kısa süre sonra çöktü")
        return None

    # ------------------------------------------------------------------ 5) ana adım
    def fix_issue(self, row, baseline):
        fp = row["fingerprint"]
        self.log(f"Düzeltmeye çalışılıyor: {row['title']}", event="deneme", fp=fp)
        try:
            raw, provider = self.llm.complete(self.build_prompt(row), SYSTEM_PROMPT)
        except LLMError as exc:
            self.log(f"LLM yok/cevap yok: {exc}", event="llm_hata")
            return "llm_yok"
        diff = ""
        try:
            data = extract_json(raw)
            if not (data.get("edits") or data.get("new_files")):
                raise P.PatchError("Model düzeltme önermedi: " + str(data.get("explanation", ""))[:300])
            ps = P.parse_patch(data, self.root, self.s)
            diff = ps.diff
            ok, reason = self.verify(ps, row, baseline)
        except (ValueError, P.PatchError) as exc:
            ok, reason = False, f"Geçersiz öneri: {exc}"
        if not ok:
            self.store.record_attempt(fp, provider, "reddedildi", reason, diff,
                                      self.s.max_attempts_per_issue, self.s.retry_backoff_minutes)
            st = self.store.get(fp)
            self.log(f"Öneri reddedildi ({provider}): {reason.splitlines()[0]}"
                     + (" → artık insan gerekli" if st and st["status"] == HUMAN else ""), event="red", fp=fp)
            return "reddedildi"
        if self.s.mode == "oneri":
            path = self.save_proposal(fp, row["title"], ps, data, reason)
            self.store.record_attempt(fp, provider, "oneri", reason, diff, 99, 0)
            self.store.set_status(fp, PROPOSED)
            self.log(f"ÖNERİ HAZIR (testlerden geçti): {path.name}", event="oneri", fp=fp)
            return "oneri"
        if self.apply_live(ps, fp, row["title"], baseline):
            self.store.record_attempt(fp, provider, "uygulandi", reason + "\n" + ps.explanation, diff, 99, 0)
            self.store.set_status(fp, FIXED)
            self.log(f"DÜZELTİLDİ ✓ {row['title']} — {ps.explanation[:200]}", event="duzeltildi", fp=fp)
            if self.on_change_applied:
                self.on_change_applied()
            return "duzeltildi"
        return "reddedildi"

    def save_proposal(self, fp, title, ps, data, reason):
        base = self.data / "oneriler" / f"{datetime.now():%Y%m%d_%H%M%S}_{_slug(fp)}"
        base.with_suffix(".json").write_text(json.dumps({"fingerprint": fp, "title": title, "patch": data},
                                                        ensure_ascii=False, indent=2), encoding="utf-8")
        base.with_suffix(".md").write_text(
            f"# Öneri: {title}\n\n**Neden:** {ps.explanation}\n\n**Doğrulama:** {reason}\n\n"
            f"Uygulamak için: `python run.py uygula \"{base.with_suffix('.json')}\"`\n\n```diff\n{ps.diff}\n```\n",
            encoding="utf-8")
        return base.with_suffix(".md")

    def apply_proposal(self, path):
        """Kaydedilmiş öneriyi tekrar doğrulayıp uygular (oneri modunda kullanıcı onayı için)."""
        info = json.loads(Path(path).read_text(encoding="utf-8"))
        _, baseline = self.scan()
        ps = P.parse_patch(info["patch"], self.root, self.s)
        row = self.store.get(info["fingerprint"])
        ok, reason = self.verify(ps, row, baseline)
        if not ok:
            return False, reason
        if not self.apply_live(ps, info["fingerprint"], info["title"], baseline):
            return False, "Canlı kontrolde bozulma görüldü, geri alındı"
        if row is not None:
            self.store.set_status(info["fingerprint"], FIXED)
        return True, reason

    # ------------------------------------------------------------------ 6) boşta: kendini büyüt (yeni testler)
    def _testgen_state(self):
        p = self.data / "testgen.json"
        try:
            return p, json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return p, {}

    def _importable(self, module):
        try:
            r = subprocess.run([sys.executable, "-B", "-c", f"import {module}"], cwd=str(self.root),
                               capture_output=True, timeout=60, env=D.clean_env())
            return r.returncode == 0
        except Exception:
            return False

    def grow_tests(self, baseline):
        path, state = self._testgen_state()
        existing = {p.name for p in (self.root / "tests").rglob("test_*.py")} if (self.root / "tests").is_dir() else set()
        candidates = []
        for f in sorted(D.py_files(self.root, self.s.ignore_dirs)):
            r = D.rel(self.root, f)
            if r.startswith("tests/") or f.name in ("__init__.py", "main.py", "config.py") \
                    or P._match(r, self.s.protected_globs):
                continue
            name = f.stem
            if f"test_{name}.py" in existing or f"test_auto_{name}.py" in existing:
                continue
            if state.get(r, 0) >= 2 or f.stat().st_size > 60000:
                continue
            candidates.append(r)
        for r in candidates:
            module = r[:-3].replace("/", ".")
            if not self._importable(module):
                state[r] = 99   # GUI/donanım bağımlı; test yazılamaz
                continue
            state[r] = state.get(r, 0) + 1
            path.write_text(json.dumps(state, indent=1), encoding="utf-8")
            self.log(f"Boşta: {r} için yeni test yazılıyor", event="testgen")
            code = (self.root / r).read_text(encoding="utf-8", errors="replace")[: self.s.max_context_chars]
            prompt = (f"Aşağıdaki modül için tests/auto/test_auto_{Path(r).stem}.py dosyasına unittest testleri yaz. "
                      f"Modülü `import {module}` ile içe aktar. Sadece saf mantığı test et: ağ, GUI, ses, mikrofon, "
                      "donanım, pano, gerçek tarayıcı KULLANMA. Dosya gerekiyorsa tempfile.TemporaryDirectory kullan. "
                      "Mevcut davranışı belgeleyen, ŞU AN GEÇEN testler yaz. Kodu değiştirme (edits boş olsun).\n\n"
                      f"===== DOSYA: {r} =====\n{code}")
            try:
                raw, provider = self.llm.complete(prompt, SYSTEM_PROMPT)
            except LLMError as exc:
                state[r] -= 1   # LLM yokken hak harcama
                path.write_text(json.dumps(state, indent=1), encoding="utf-8")
                self.log(f"LLM yok, test yazımı ertelendi: {str(exc)[:200]}", event="llm_hata")
                return False
            try:
                data = extract_json(raw)
                data["edits"] = []
                ps = P.parse_patch(data, self.root, self.s)
                ok, reason = self.verify(ps, None, baseline)
            except (ValueError, P.PatchError) as exc:
                ok, reason = False, str(exc)
            if not ok:
                self.log(f"Test önerisi reddedildi: {reason.splitlines()[0][:200]}", event="testgen_red")
                return True
            if self.s.mode == "oneri":
                self.save_proposal(f"testgen:{r}", f"{r} için yeni testler", ps, data, reason)
                self.log(f"Yeni test önerisi hazır: {r}", event="testgen_oneri")
            elif self.apply_live(ps, f"testgen:{r}", f"{r} için yeni testler", baseline):
                self.log(f"KENDİNİ BÜYÜTTÜ ✓ {r} için testler eklendi ({reason})", event="testgen_ok")
            return True
        path.write_text(json.dumps(state, indent=1), encoding="utf-8")
        return False

    # ------------------------------------------------------------------ döngü
    def cycle(self):
        with self.lock:
            issues, baseline = self.scan()
            outcome = "bekleme"
            if self.s.mode != "gozlem":
                row = self.store.next_fixable(self.s.max_attempts_per_issue)
                if row is not None:
                    outcome = self.fix_issue(row, baseline)
                elif self.s.idle_test_generation and not baseline.failing:
                    if self.grow_tests(baseline):
                        outcome = "testgen"
            write_report(self, baseline)
            return outcome

    def run_forever(self, stop_event, wake_event=None):
        self.log(f"JARVIS Self-Heal başladı | mod={self.s.mode} | hedef={self.root}", event="basla")
        while not stop_event.is_set():
            self._heartbeat()
            try:
                outcome = self.cycle()
            except Exception as exc:  # motor asla tamamen durmamalı
                import traceback
                self.log(f"Döngü hatası: {exc}", event="motor_hata", trace=traceback.format_exc())
                outcome = "hata"
            # İş yaptıysa hemen tekrar bak; değilse bekle (çökme olursa erken uyan).
            delay = 5 if outcome in ("duzeltildi", "reddedildi", "testgen", "oneri") else self.s.cycle_seconds
            if wake_event is not None:
                wake_event.wait(delay)
                wake_event.clear()
            else:
                stop_event.wait(delay)
        self.log("JARVIS Self-Heal durdu", event="dur")

    # ------------------------------------------------------------------ tekil çalışma kilidi
    def _lock_path(self):
        return self.data / "engine.lock"

    def acquire(self, force=False):
        p = self._lock_path()
        if p.exists() and not force:
            try:
                info = json.loads(p.read_text(encoding="utf-8"))
                if time.time() - info.get("heartbeat", 0) < max(600, 3 * self.s.cycle_seconds) and info.get("pid") != os.getpid():
                    return False
            except Exception:
                pass
        self._heartbeat()
        return True

    def _heartbeat(self):
        self._lock_path().write_text(json.dumps({"pid": os.getpid(), "heartbeat": time.time()}), encoding="utf-8")

    def release(self):
        self._lock_path().unlink(missing_ok=True)
