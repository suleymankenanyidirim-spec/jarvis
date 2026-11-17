# JARVIS v2.1 — v1.4 + KENDİNİ ONARMA (SELF-HEAL)

v1.4 (telefon uygulaması, GitHub güncelleme, erişim anahtarı) temel alındı; v2.0'daki
kendini onarma motoru bunun üzerine taşındı. v1.4'teki hiçbir özellik kaldırılmadı.
`phone_server.py`, `updater.py`, `tools.py` v1.4 sürümünde kaldı (telefon erişim anahtarı korunuyor).

## Yeni
- **Self-Heal motoru** (depo kökünde `selfheal/`, `run.py`): JARVIS'i sürekli tarar (sözdizimi, testler,
  çökme logları, çöken arka plan görevleri, Ollama sağlığı), düzeltmeyi kodun kopyasında test eder,
  geçerse yedek alıp uygular, sorun çıkarsa geri alır. Ayrıntılar: kökteki `README.md`.
- **Başlatıcılar**: `KUR.bat`, `BASLAT.bat`, `ONERI_MODU.bat`, `SADECE_MOTOR.bat`, `DURUM.bat`, `GERI_AL.bat`.
  `py` başlatıcısını kullanır (`START_JARVIS.bat` ile aynı Python).
- **Hedef klasör kendiliğinden bulunur**: depoda `desktop/`, kurulu pakette (`C:\JARVIS`) klasörün kendisi.
  İstenirse `selfheal_ayarlar.json` içinde `target_dir` ile değiştirilebilir.
- **Testler**: `desktop/tests/` (masaüstü: arka plan görevleri, hafıza, planlayıcı, güncelleyici, ayarlar)
  ve `tests/` (motorun kendi testleri + v1.4 ile entegrasyon testleri).
- **GitHub Actions**: her gönderimde önce testler çalışır (Python 3.9 ve 3.12). Testler geçmezse
  masaüstü paketi ve Release oluşmaz. `JARVIS_desktop.zip` artık `selfheal/`, `run.py` ve `.bat`
  dosyalarını içerir; paket açılıp kurulu düzende test edilir.

## Değişiklikler
- `core/background.py`: çöken arka plan görevinin hata izi (traceback) `data/background_tasks.json`
  içine `traceback` alanı olarak yazılır. Self-Heal motoru hatanın hangi dosya/fonksiyondan geldiğini
  buradan bulur.
- `config.py`: yeni ayar `BACKGROUND_TRACEBACK_CHARS = 6000` (saklanacak traceback uzunluğu, 0 = kapalı).
- Self-Heal artık şu dosyaları **asla** otomatik değiştirmez: `config_local.py`, `config_local.example.py`,
  `core/phone_server.py`, `core/updater.py`, `run.py`, `phone_ui/` (önceki koruma listesine ek olarak:
  `tests/`, `data/`, `selfheal/`, `.bat`, `.ino`, `requirements.txt`).
- Sürüm 2.1.0 (HUD'daki etiket VERSION dosyasından okunur).

## Düzeltmeler
- `core/memory.py`: `with sqlite3.connect(...)` bağlantıyı kapatmıyordu; Windows'ta (Python 3.13+)
  `jarvis_memory.db` dosyası çöp toplayıcı çalışana kadar kilitli kalıyordu. Bağlantılar artık her
  işlemden sonra kapatılıyor.
- Self-Heal: logdaki `C:\JARVIS\...` gibi mutlak yollar, hedef klasörün dışındaki gerçek bir dosyaya
  eşlenebiliyordu (`C:\JARVIS` diskte varsa). Sürücü harfi ve `..` içeren adaylar artık reddediliyor.
- Self-Heal: dosya yazımı Python 3.9'da çöküyordu (`write_text(newline=)` 3.10+ özelliği). Düzeltildi.

## Yükseltme
- `data/` ve `config_local.py` dokunulmadan kalır (güncelleyici bunları zaten ezmez).
- Otomatik güncelleme açıksa (`GITHUB_REPO`) v2.1 paketi `selfheal/` ve `.bat` dosyalarını da kurar;
  motoru kullanmak için `KUR.bat` bir kez çalıştırılıp JARVIS `BASLAT.bat` ile açılmalı.
  `START_JARVIS.bat` eskisi gibi motorsuz çalışır.
