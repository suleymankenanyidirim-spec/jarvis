"""JARVIS v2.1 — Kendini Geliştiren JARVIS

Kullanım:
  python run.py                      # sürekli çalış (ayarlardaki modda)
  python run.py --uygulama           # JARVIS'i de başlat, çökerse yeniden başlat ve düzelt
  python run.py --mod gozlem         # sadece sorun bul (koda dokunma)
  python run.py --mod oneri          # düzeltme üret + test et, ama uygulama; öneri klasörüne yaz
  python run.py --bir-kez            # tek tur çalış ve çık
  python run.py durum                # raporu yazdır
  python run.py geri-al              # son uygulanan değişikliği geri al
  python run.py uygula <oneri.json>  # kaydedilmiş öneriyi tekrar doğrula ve uygula
"""
import argparse
import signal
import sys
import threading

from selfheal.config import load_settings
from selfheal.engine import Engine
from selfheal.report import write_report


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Kendini geliştiren JARVIS")
    ap.add_argument("komut", nargs="?", default="calis", choices=["calis", "durum", "geri-al", "uygula"])
    ap.add_argument("dosya", nargs="?")
    ap.add_argument("--mod", choices=["gozlem", "oneri", "otomatik"])
    ap.add_argument("--hedef", help="Geliştirilecek JARVIS klasörü (varsayılan: desktop/, kurulu pakette bu klasör)")
    ap.add_argument("--uygulama", action="store_true", help="JARVIS uygulamasını da gözetim altında çalıştır")
    ap.add_argument("--bir-kez", action="store_true", help="Tek tur çalış")
    ap.add_argument("--aralik", type=int, help="Taramalar arası saniye")
    ap.add_argument("--ayarlar", help="Ayar dosyası (varsayılan: selfheal_ayarlar.json)")
    ap.add_argument("--zorla", action="store_true", help="Kilit dosyasını yok say")
    a = ap.parse_args(argv)

    s = load_settings(a.ayarlar, mode=a.mod, target_dir=a.hedef, cycle_seconds=a.aralik)
    if not s.target.is_dir():
        print(f"Hedef klasör bulunamadı: {s.target}")
        return 2
    engine = Engine(s)

    if a.komut == "durum":
        write_report(engine)
        print((s.data / "RAPOR.md").read_text(encoding="utf-8"))
        return 0
    if a.komut == "geri-al":
        ch = engine.rollback(reason="kullanıcı geri aldı")
        print("Geri alınacak aktif değişiklik yok." if not ch else f"Geri alındı: {ch['title']}")
        return 0
    if a.komut == "uygula":
        if not a.dosya:
            print("Öneri dosyası ver: python run.py uygula selfheal_data/oneriler/....json")
            return 2
        ok, reason = engine.apply_proposal(a.dosya)
        print(("UYGULANDI: " if ok else "UYGULANMADI: ") + reason)
        return 0 if ok else 1

    if not engine.acquire(force=a.zorla):
        print("Başka bir Self-Heal zaten çalışıyor gibi görünüyor (selfheal_data/engine.lock). "
              "Emin değilsen --zorla ile başlat.")
        return 1
    try:
        if a.bir_kez:
            print("Sonuç:", engine.cycle())
            print("Rapor:", s.data / "RAPOR.md")
            return 0
        stop, wake = threading.Event(), threading.Event()

        def _stop(*_):
            stop.set()
            wake.set()
        signal.signal(signal.SIGINT, _stop)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, _stop)

        sup = None
        if a.uygulama:
            from selfheal.supervisor import AppSupervisor
            sup = AppSupervisor(s, engine, wake, log=lambda m: engine.log(m, event="gozetmen"))
            sup.start()
        try:
            engine.run_forever(stop, wake)
        finally:
            if sup:
                sup.stop()
        return 0
    finally:
        engine.release()


if __name__ == "__main__":
    sys.exit(main())
