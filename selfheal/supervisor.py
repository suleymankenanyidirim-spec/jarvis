"""Uygulama gözetmeni: JARVIS'i alt süreç olarak çalıştırır, çıktısını loga yazar,
çökerse yeniden başlatır ve motoru uyandırır. Düzeltmeden hemen sonra çökme olursa
motor son değişikliği geri alır (deneme süresi / probation)."""
import subprocess
import sys
import threading
import time
from datetime import datetime


class AppSupervisor:
    def __init__(self, settings, engine, wake_event, log=print):
        self.s = settings
        self.engine = engine
        self.wake = wake_event
        self.log = log
        self.proc = None
        self.stop_event = threading.Event()
        self.restart_requested = threading.Event()
        self.log_path = settings.data / "logs" / "app.log"
        engine.on_change_applied = self.request_restart

    def command(self):
        return [sys.executable if c == "{python}" else c for c in self.s.app_command]

    def request_restart(self):
        if self.s.restart_app_after_fix:
            self.restart_requested.set()
            if self.proc and self.proc.poll() is None:
                self.proc.terminate()

    def _pump(self, proc, fh):
        for line in proc.stdout:
            fh.write(line)
            fh.flush()

    def run(self):
        quick_crashes = 0
        while not self.stop_event.is_set():
            started = time.time()
            with open(self.log_path, "a", encoding="utf-8", errors="replace") as fh:
                fh.write(f"\n=== JARVIS başlatıldı {datetime.now():%Y-%m-%d %H:%M:%S} ===\n")
                fh.flush()
                self.proc = subprocess.Popen(
                    self.command(), cwd=str(self.s.target), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace", bufsize=1,
                    env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"},
                )
                pump = threading.Thread(target=self._pump, args=(self.proc, fh), daemon=True)
                pump.start()
                code = self.proc.wait()
                pump.join(timeout=5)
                fh.write(f"=== JARVIS kapandı (kod {code}) ===\n")
            if self.stop_event.is_set():
                break
            if self.restart_requested.is_set():
                self.restart_requested.clear()
                self.log("[GÖZETMEN] Düzeltme uygulandı, JARVIS yeniden başlatılıyor")
                continue
            if code == 0:
                self.log("[GÖZETMEN] JARVIS normal kapandı; gözetmen de duruyor")
                break
            self.log(f"[GÖZETMEN] JARVIS çöktü (kod {code}); motor uyarılıyor")
            self.engine.on_app_crash()
            self.wake.set()
            quick_crashes = quick_crashes + 1 if time.time() - started < 60 else 0
            if quick_crashes >= self.s.app_max_quick_crashes:
                self.log("[GÖZETMEN] Üst üste çok hızlı çöktü; motor düzeltene kadar 10 dk bekleniyor")
                self.stop_event.wait(600)
                quick_crashes = 0
            self.stop_event.wait(self.s.app_restart_delay_seconds)

    def start(self):
        t = threading.Thread(target=self.run, name="jarvis-gozetmen", daemon=True)
        t.start()
        return t

    def stop(self):
        self.stop_event.set()
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
