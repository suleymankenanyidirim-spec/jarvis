import config
import threading
import time
from core.brain import JarvisBrain
from interface.gpu_hud import GPUHUD

def main():
    # Bekleyen doğrulanmış güncellemeyi uygulama başlamadan önce uygula.
    try:
        from core.updater import SelfUpdater
        updater = SelfUpdater()
        if getattr(config, "AUTO_APPLY_UPDATES_ON_START", True):
            msg = updater.apply_pending()
            if msg != "Güncelleme beklemiyor.": print("[UPDATE]", msg)
    except Exception as exc:
        print("[UPDATE] Uygulama başarısız:", exc)

    brain = JarvisBrain()

    def auto_update_watch():
        manifest = getattr(config, "UPDATE_MANIFEST_URL", "") or \
            brain.updater.github_manifest_url(getattr(config, "GITHUB_REPO", ""))
        if not manifest or not getattr(config, "AUTO_UPDATE_CHECK", True): return
        while True:
            try:
                result = brain.updater.stage(manifest)
                print("[UPDATE]", result)
            except Exception as exc:
                print("[UPDATE] Kontrol başarısız:", exc)
            time.sleep(max(1, int(getattr(config,"AUTO_UPDATE_INTERVAL_HOURS",12)*3600)))
    threading.Thread(target=auto_update_watch, daemon=True).start()

    if getattr(config, "PHONE_SERVER_ENABLED", True):
        try:
            from core.phone_server import start_phone_server
            start_phone_server(brain, getattr(config, "PHONE_SERVER_PORT", 5000))
        except Exception as exc:
            print("[TELEFON] Sunucu baslatilamadi:", exc)

    GPUHUD(brain, 1280, 720).run()

if __name__ == "__main__":
    main()
