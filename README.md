# J.A.R.V.I.S. v2.1

Türkçe konuşan kişisel yapay zeka asistanı: Windows'ta çalışan masaüstü JARVIS (Ollama, HUD, ses, araçlar, Iron Man kaskı)
ve ona bağlanan **telefon uygulaması**.

```
desktop/   → PC sürümü (Python). desktop/tests/ = masaüstü testleri
mobile/    → Telefon uygulaması (web uygulaması + Android APK)
selfheal/  → Kendini onarma motoru (v2.1), run.py ve *.bat ile çalışır; hedefi desktop/
tests/     → Motorun kendi testleri
.github/   → GitHub otomatik derleme: testler, APK, PC paketi, GitHub Pages
```

## Telefon uygulaması nasıl çalışır?

| Mod | Ne zaman | Ne yapar |
|---|---|---|
| **PC** | Bilgisayarda JARVIS açık ve telefon aynı WiFi'de | PC'deki JARVIS'in beyni, hafızası, araçları, kask kontrolü, edge-tts sesi |
| **Bulut** | PC kapalı / dışarıdasın | Gemini (ücretsiz anahtar), OpenAI ya da Claude ile sohbet + web araması |
| **Otomatik** | Varsayılan | PC'ye ulaşabiliyorsa PC'yi, yoksa bulutu kullanır |

Sesli konuşma (mikrofon), sesli cevap, eller serbest mod, telefon hafızası ("hatırla …", "unut …"),
arka plan görevlerini ve PC sistem durumunu izleme, kask aç/kapat (PC yoksa doğrudan ESP32'ye).

## Kurulum (bir kerelik)

### 1. GitHub'a yükle
1. github.com → **New repository** → ad: `jarvis` (Private da olabilir; Pages için Public gerekir, ücretsiz hesapta).
2. Bu klasörün içeriğini yükle (*Add file → Upload files* ya da `git push`).

### 2. APK'yı al
- Yükleyince **Actions** sekmesinde "JARVIS Derleme" kendiliğinden çalışır (~5 dk).
- Biten çalışmanın altındaki **Artifacts → jarvis-apk** dosyasını indir, içindeki APK'yı telefona at ve kur.
- Kalıcı sürüm için etiket gönder: `git tag v2.1.0 && git push --tags` (ya da GitHub'da *Releases → Draft a new release → tag: v2.1.0*).
  Release sayfasında APK + PC paketi hazır olur; telefondan doğrudan oradan indirilebilir.

### 3. (İsteğe bağlı) Web sürümü — iPhone dahil her telefon
- **Settings → Pages → Source: GitHub Actions** seç.
- Adres: `https://<kullanıcı-adın>.github.io/jarvis/` → tarayıcıda aç → *Ana ekrana ekle*.
- Not: https sayfadan evdeki http PC'ye bağlanmayı tarayıcılar engelleyebilir. Web sürümünde PC modu için
  PC'nin verdiği linki kullan (aşağıda). APK'da bu sorun yok.

### 4. PC tarafı
1. Release'teki `JARVIS_desktop.zip`'i `C:\JARVIS`'e çıkar (eski `data` klasörünün üzerine yazma — hafızan orada).
2. `INSTALL.bat` → `START_JARVIS.bat` (ya da kendini onarma ile: `KUR.bat` → `BASLAT.bat`, aşağıya bak).
3. Konsolda şu satır çıkar:
   ```
   [TELEFON] Aynı WiFi'deki telefondan aç: http://192.168.1.20:5000/?token=Ab3xYz...
   [TELEFON] Uygulamada PC adresi: http://192.168.1.20:5000   Anahtar: Ab3xYz...
   ```
   - Bu linki telefonda açarsan uygulama doğrudan PC'den açılır ve kendini ayarlar (QR kod da basılır).
   - APK'da: **AYAR → PC adresi** ve **Erişim anahtarı** alanlarına bunları yaz → *Bağlantıyı test et*.
4. Otomatik güncelleme için `config_local.py` içine:
   ```python
   GITHUB_REPO = "kullanici-adin/jarvis"
   ```
   JARVIS 12 saatte bir GitHub'daki son Release'i kontrol eder, SHA-256 ile doğrular, bir sonraki açılışta uygular.
   `data/` ve `config_local.py` asla ezilmez.

> Windows Güvenlik Duvarı ilk açılışta Python için izin sorarsa **Özel ağlar**'a izin ver; yoksa telefon PC'ye ulaşamaz.

## Kendini onarma (Self-Heal, v2.1)

Motor JARVIS'in yanında sürekli çalışır:

1. **Tarar:** sözdizimi hataları, başarısız testler, çökme logları (Traceback), çöken arka plan görevleri
   (`data/background_tasks.json` içindeki hata izi), Ollama'nın açık olup olmadığı.
2. **Seçer:** en önemli açık sorunu (önce sözdizimi, sonra testler, sonra çalışma hataları).
3. **Düzeltme üretir:** sırayla Claude → GPT → Gemini → yerel Ollama'ya sorar (anahtarı olmayan atlanır).
4. **Kum havuzunda doğrular:** kodun kopyasında tüm testleri çalıştırır; hiçbir test bozulmazsa geçer.
5. **Uygular ve izler:** yedek alır, uygular, JARVIS'i yeniden başlatır; 2 dakika içinde çökerse geri alır.
6. **Deneyimden öğrenir:** başarısız denemelerin nedenini bir sonraki denemede modele verir;
   3 denemede olmazsa "insan gerekli" diye bırakır.
7. **Boşken kendini büyütür:** test yazılmamış modüller için yeni testler yazar (sadece geçenleri kabul eder).

| Dosya | Ne yapar |
|---|---|
| `KUR.bat` | Bağımlılıkları kurar (bir kez) |
| `BASLAT.bat` | JARVIS'i başlatır, çökerse yeniden başlatır, motor sürekli düzeltir (önerilen) |
| `ONERI_MODU.bat` | Düzeltmeleri üretir ve test eder ama **koda dokunmaz** (`selfheal_data\oneriler\`) |
| `SADECE_MOTOR.bat` | Sadece motoru çalıştırır |
| `DURUM.bat` | Raporu gösterir (`selfheal_data\RAPOR.md`) |
| `GERI_AL.bat` | Son uygulanan değişikliği geri alır |

```
py run.py --uygulama            # JARVIS + motor, otomatik mod
py run.py --mod gozlem          # sadece bul ve raporla
py run.py --mod oneri           # düzelt + test et, ama uygulama
py run.py --bir-kez             # tek tur
py run.py uygula selfheal_data\oneriler\XXXX.json
py run.py geri-al
```

**Hedef klasör:** depoda `desktop/`, kurulu pakette (`C:\JARVIS`) klasörün kendisi — otomatik bulunur.
Değiştirmek için `selfheal_ayarlar.json` içine `"target_dir": "D:\\Baska\\JARVIS"` yaz.
Ayarlar: `selfheal_ayarlar.ornek.json` → `selfheal_ayarlar.json` (`KUR.bat` kopyalar); tüm ayarlar `selfheal/config.py` içinde.

**Güvenlik kuralları (kodda zorunlu):**
- Testler değiştirilemez; yeni dosya sadece `tests/auto/test_*.py` olabilir.
- `data/`, `config_local.py`, `core/phone_server.py` (erişim anahtarı), `core/updater.py` (SHA-256 doğrulaması),
  `.bat`, `.ino`, `requirements.txt`, `run.py` ve motorun kendisi (`selfheal/`) asla otomatik değişmez.
- Değişiklik başına en fazla 3 dosya ve 80 satır; yeni eklenen `subprocess`, dosya silme, `eval/exec`, ağ,
  `socket`, `pyautogui` gibi çağrılar reddedilir.
- Çalışma zamanı hatası düzeltilirken önce hatayı yakalayan bir test yazılması şart.
- Testler API anahtarlarının silindiği bir ortamda çalışır. Her uygulamadan önce yedek: `selfheal_data\backups\`.

**Sınırlar:** yerel `qwen3:8b` küçük bir model; önerilerinin çoğu testlerden geçemeyip reddedilir (güvenli davranış).
Bir API anahtarı (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY` veya `GEMINI_API_KEY` ortam değişkeni) çok fark eder.
HUD, ses ve donanım kodu test edilemediği için motor oradaki hataları sadece çökme logundan görür.

## Testler
```
cd desktop && py -m unittest discover -s tests -t .     # masaüstü testleri
py -m unittest discover -s tests -t .                   # (kökte) motor + entegrasyon testleri
```
GitHub'da her gönderimde Python 3.9 ve 3.12 ile çalışır; testler geçmezse paket/Release oluşmaz.

## Bulut anahtarı
- **Gemini** (önerilen, ücretsiz kota): https://aistudio.google.com → *Get API key*
- OpenAI: https://platform.openai.com/api-keys · Claude: https://console.anthropic.com
- Anahtar sadece telefonda saklanır, hiçbir sunucuya (GitHub dahil) gönderilmez.
- Model adı değişirse AYAR → **MODELLERİ GETİR** ile güncel listeden seç.

## Geliştirici notları
```bash
cd mobile
npm install
npx cap add android && npm run android:sync
cd android && ./gradlew assembleDebug        # Android Studio / SDK gerekir
npm run serve                                # web sürümünü yerelde dene: http://localhost:8080
```
APK hata ayıklama (debug) imzasıyla derlenir; kişisel kullanım için yeterli. Play Store için imzalı sürüm gerekir.

Sürüm geçmişi: `desktop/CHANGELOG_v2.1.md`, `desktop/CHANGELOG_v1.4.md`, `desktop/CHANGELOG_v1.3.md`, `desktop/CHANGELOG_v1.2.md`.
