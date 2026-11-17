"""JARVIS giriş noktası.

    py main.py              -> HUD + beyin + telefon sunucusu
    py main.py --headless   -> (v2.2) HUD yok, pygame yüklenmez: yalnızca beyin + telefon sunucusu
                               (+ otomatik güncelleme kontrolü). Çıktı data/jarvis_headless.log'a da yazılır.
"""
import argparse
import os
import sys
import threading
import time

import config


def _cfg(name, default=None):
    return getattr(config, name, default)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="JARVIS")
    parser.add_argument("--headless", action="store_true",
                        help="HUD'u açmadan yalnızca beyin + telefon sunucusunu çalıştır")
    return parser.parse_args(argv)


# --------------------------------------------------------------- günlük (log)
class _Tee:
    """Yazılanı birden çok akışa gönderir; biri bozulsa diğerleri çalışmaya devam eder."""

    def __init__(self, *streams):
        self.streams = [s for s in streams if s is not None]

    def write(self, text):
        for s in self.streams:
            try:
                s.write(text)
            except Exception:
                pass
        return len(text)

    def flush(self):
        for s in self.streams:
            try:
                s.flush()
            except Exception:
                pass

    def isatty(self):
        return False

    @property
    def encoding(self):
        return "utf-8"


def setup_headless_log(path):
    """stdout/stderr'i log dosyasına da yazar. pyw ile (konsolsuz) çalışırken de çökmez.

    path boşsa log tutulmaz; konsol da yoksa çıktı devnull'a gider. Log dosyasının yolu döner.
    """
    if not path:
        if sys.stdout is None or sys.stderr is None:
            devnull = open(os.devnull, "w", encoding="utf-8")
            sys.stdout = sys.stdout or devnull
            sys.stderr = sys.stderr or devnull
        return None
    from core.paths import resolve
    log_path = resolve(path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = open(log_path, "a", encoding="utf-8", buffering=1)
    sys.stdout = _Tee(sys.stdout, log)
    sys.stderr = _Tee(sys.stderr, log)
    return log_path


# --------------------------------------------------------------- başlangıç adımları
def apply_pending_update():
    """Bekleyen doğrulanmış güncellemeyi uygulama başlamadan önce uygular."""
    try:
        from core.updater import SelfUpdater
        updater = SelfUpdater()
        if _cfg("AUTO_APPLY_UPDATES_ON_START", True):
            msg = updater.apply_pending()
            if msg != "Güncelleme beklemiyor.":
                print("[UPDATE]", msg)
    except Exception as exc:
        print("[UPDATE] Uygulama başarısız:", exc)


def start_update_watch(brain):
    def auto_update_watch():
        manifest = _cfg("UPDATE_MANIFEST_URL", "") or \
            brain.updater.github_manifest_url(_cfg("GITHUB_REPO", ""))
        if not manifest or not _cfg("AUTO_UPDATE_CHECK", True):
            return
        while True:
            try:
                print("[UPDATE]", brain.updater.stage(manifest))
            except Exception as exc:
                print("[UPDATE] Kontrol başarısız:", exc)
            time.sleep(max(1, int(_cfg("AUTO_UPDATE_INTERVAL_HOURS", 12) * 3600)))
    t = threading.Thread(target=auto_update_watch, daemon=True)
    t.start()
    return t


def start_phone(brain):
    """Telefon sunucusunu başlatır; başlatılamazsa (port dolu, flask yok…) None döner."""
    if not _cfg("PHONE_SERVER_ENABLED", True):
        print("[TELEFON] Telefon sunucusu kapalı (PHONE_SERVER_ENABLED = False).")
        return None
    try:
        from core.phone_server import start_phone_server
        return start_phone_server(brain, _cfg("PHONE_SERVER_PORT", 5000))
    except Exception as exc:
        print("[TELEFON] Sunucu baslatilamadi:", exc)
        return None


def run_headless(brain, stop_event=None, start_phone_fn=None):
    """HUD'suz çalışma: telefon sunucusu ayakta kaldıkça bekler. Çıkış kodu döner."""
    print("[JARVIS] Arka plan modu (--headless): HUD kapalı; beyin + telefon sunucusu çalışıyor.")
    server = (start_phone_fn or start_phone)(brain)
    if server is None:
        print("[JARVIS] Telefon sunucusu başlatılamadı; arka plan modu kapanıyor.")
        return 1
    stop_event = stop_event or threading.Event()
    try:
        while not stop_event.wait(1.0):
            pass
    except KeyboardInterrupt:
        pass
    print("[JARVIS] Arka plan modu kapandı.")
    return 0


def main(argv=None):
    args = parse_args(argv)
    if args.headless:
        setup_headless_log(_cfg("HEADLESS_LOG_FILE", "data/jarvis_headless.log"))

    apply_pending_update()

    from core.brain import JarvisBrain
    brain = JarvisBrain()
    start_update_watch(brain)

    if args.headless:
        return run_headless(brain)

    start_phone(brain)
    from interface.gpu_hud import GPUHUD      # pygame yalnızca HUD modunda yüklenir
    GPUHUD(brain, 1280, 720).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
