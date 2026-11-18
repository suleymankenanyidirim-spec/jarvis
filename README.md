# J.A.R.V.I.S. v3.1

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
| **PC** | Bilgisayarda JARVIS açık (ev WiFi'si ya da Tailscale ile her yerden) | PC'deki JARVIS'in beyni, hafızası, araçları, kask kontrolü, PC sesi; cevap akış halinde |
| **Bulut** | PC'ye ulaşılamıyor | Gemini (ücretsiz anahtar), OpenAI ya da Claude ile sohbet + web araması (sınırlı mod) |
| **Otomatik** | Varsayılan | PC adreslerinden birine ulaşabiliyorsa PC'yi, hiçbirine ulaşamazsa bulutu kullanır |

Sesli konuşma (mikrofon), cümle cümle sesli cevap, eller serbest mod, PC ile eşitlenen hafıza ("hatırla …", "unut …",
AYAR → HAFIZA), arka plan görevlerini izleme ve bitince bildirim, kask aç/kapat (PC yoksa doğrudan ESP32'ye).
Ana ekran sadedir: dokun = konuş, yukarı kaydır = yaz, çift dokun = mod, uzun bas = ayarlar.

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
3. Konsolda (ve `data/phone_link.txt` içinde) şu satırlar çıkar:
   ```
   [TELEFON] Ev WiFi linki       : http://192.168.1.20:5000/?pair=ABCD2345&pcs=...
   [TELEFON] Tailscale linki     : http://100.101.102.103:5000/?pair=ABCD2345&pcs=...
   [TELEFON] Eşleşme kodu        : ABCD-2345 (5 dk geçerli, tek kullanımlık; yenisi: TELEFON_ESLESTIR.bat)
   ```
   - Linki telefonda açarsan (QR kod da basılır) uygulama adresleri öğrenir ve kendiliğinden eşleşir.
   - APK'da: uzun bas → AYAR → **Eşleşme kodu** kutusuna kodu yaz → **PC İLE EŞLEŞ**. Erişim anahtarı
     kendiliğinden kaydedilir; anahtar artık linklere ve URL'lere yazılmaz (elle girmek için `data/phone_token.txt`).
   - Kodun süresi geçtiyse PC'de `TELEFON_ESLESTIR.bat` yeni kod üretir.
4. Otomatik güncelleme için `config_local.py` içine:
   ```python
   GITHUB_REPO = "kullanici-adin/jarvis"
   ```
   JARVIS 12 saatte bir GitHub'daki son Release'i kontrol eder, SHA-256 ile doğrular, bir sonraki açılışta uygular.
   `data/` ve `config_local.py` asla ezilmez.

> Windows Güvenlik Duvarı ilk açılışta Python için izin sorarsa **Özel ağlar**'a izin ver; yoksa telefon PC'ye ulaşamaz.

## Ev dışından bağlantı (Tailscale)
Telefon evden çıkınca da PC'deki JARVIS'e (aynı beyin, hafıza, araçlar) bağlanır. Tailscale ücretsizdir, port
açmak gerekmez; trafik yalnızca senin cihazların arasında şifreli gider.

1. PC'ye Tailscale'i kur (https://tailscale.com/download) ve hesabınla giriş yap.
2. Telefona Tailscale uygulamasını kur ve **aynı hesapla** giriş yap, bağlantıyı aç.
3. JARVIS'i yeniden başlat. Konsolda ve `data/phone_link.txt` içinde iki link görünür: ev WiFi (LAN) ve
   Tailscale (`http://100.x.y.z:5000/...`). Tailscale adresi bulunamazsa `config_local.py`'ye
   `TAILSCALE_IP = "100.x.y.z"` yaz (`tailscale ip -4` komutu adresi gösterir).
4. Telefonda linki bir kez aç (QR kod Tailscale linkini gösterir). Uygulama iki adresi de öğrenir:
   evde LAN'ı, dışarıda Tailscale'i kullanır; üstte `PC BAĞLI · EV` / `PC BAĞLI · TAILSCALE` yazar.
   Ya da AYAR → **PC adresleri** kutusuna her satıra bir adres yaz.
5. PC'nin hep açık kalması için **arka plan modunu** kur: `KUR_OTOMATIK_BASLAT.bat`.

PC'ye hiç ulaşılamazsa (otomatik modda) bulut devreye girer; durum kelimesinde kısa süre **SINIRLI MOD**, ayar
katmanının üstünde **"PC'ye ulaşılamıyor, sınırlı mod"** yazar. Bulutta söylenen "hatırla …" ve konuşmalar
PC'ye ilk bağlantıda eşitlenir.

### Arka plan modu (`--headless`)
- `py main.py --headless`: HUD açılmaz, pygame yüklenmez; yalnızca beyin + telefon sunucusu (+ güncelleme kontrolü)
  çalışır. Çıktı `data/jarvis_headless.log` dosyasına da yazılır.
- `KUR_OTOMATIK_BASLAT.bat`: Windows açılışında JARVIS'i pencere açmadan başlatır (yönetici izni gerekmez).
  Kaldırmak için `OTOMATIK_BASLAT_KALDIR.bat`.
- Arka planda JARVIS çalışırken HUD'u açarsan telefon sunucusu HUD'da başlamaz (port dolu); telefon arka plandakine bağlanır.
- `data/` yolları artık `main.py`'nin klasörüne göre çözülür; JARVIS hangi klasörden açılırsa açılsın aynı hafızayı kullanır.

## Bilgi deposu (v2.8)
JARVIS bir soru gelince **önce `D:\JARVIS_Bilgi` klasörüne** bakar, orada yoksa internete çıkar. Her şey PC'de kalır.
- Klasöre `.txt .md .csv .json .html` (ve `.pdf`) dosyaları at; JARVIS değişiklikleri kendiliğinden görür.
- Sohbette: **"depoya kaydet: Arabamın plakası 34 ABC 123"** → `notlar/` klasörüne yazılır.
- Telefondan: uzun bas → AYAR → **BİLGİ DEPOSU** → not ya da dosya gönder (`telefon/` klasörüne düşer).
- Masaüstü kısayolları: `MASAUSTU_KISAYOL.bat` ("JARVIS" ve "JARVIS Bilgi Deposu").
- Ayarlar `config.py` → v2.8 bölümü (`KNOWLEDGE_DIR`, `KNOWLEDGE_WEB_FALLBACK` …).

## Telefondan internet üzerinden (GitHub Pages + Tailscale https)
iPhone dahil her telefonda, evde ya da dışarıda, https ile çalışır; port açılmaz, veri yalnızca senin cihazların
arasında gider.
1. **Tailscale HTTPS'i aç (bir kez):** https://login.tailscale.com/admin/dns → *HTTPS Certificates* → **Enable**.
2. PC'de (yönetici gerekmez): `tailscale serve --bg 5000` → verdiği `https://<pc>.<tailnet>.ts.net` adresini
   `config_local.py`'ye yaz: `TAILSCALE_HTTPS_URL = "https://…ts.net"` ve `GITHUB_REPO = "kullanici/jarvis"`.
3. GitHub'da depo → Settings → Pages → Source: **GitHub Actions** (bir kez). Uygulama
   `https://kullanici.github.io/jarvis/` adresinde yayınlanır.
4. JARVIS'i yeniden başlat: QR kod ve `data/phone_link.txt` GitHub Pages linkini (eşleşme koduyla) gösterir.
   Telefonda Tailscale açıkken linki aç → kendiliğinden eşleşir. Safari'de *Paylaş → Ana Ekrana Ekle*.

## Android imzalama (release APK)
v2.5'ten itibaren APK, GitHub Secrets'taki **sabit bir keystore** ile imzalı release olarak derlenir. İmza hep
aynı olduğu için yeni APK eskisinin üzerine kurulur (veriler silinmez). Bir kerelik kurulum:

### 1. Keystore üret (PC'de, bir kez)
`keytool` Java ile gelir. Android Studio kuruluysa: `C:\Program Files\Android\Android Studio\jbr\bin\keytool.exe`.
```
keytool -genkeypair -v -keystore jarvis-release.jks -storetype PKCS12 -alias jarvis -keyalg RSA -keysize 2048 -validity 10000
```
- Sorulan şifreyi (keystore şifresi) ve alias'ı (`jarvis`) bir yere yaz. **Bu dosyayı ve şifreyi kaybetme:**
  kaybedersen sonraki APK eskisinin üzerine kurulmaz.
- `jarvis-release.jks` dosyasını depoya koyma (`.gitignore` zaten `*.jks`, `*.keystore`, `*.p12`, `*.b64`'ü dışarıda tutar).

### 2. Base64'e çevir (PowerShell)
```powershell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("jarvis-release.jks")) | Set-Content -NoNewline jarvis-release.b64
```

### 3. GitHub Secrets'a ekle
Repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Değer |
|---|---|
| `ANDROID_KEYSTORE_BASE64` | `jarvis-release.b64` dosyasının içeriği (zorunlu) |
| `ANDROID_KEYSTORE_PASSWORD` | keystore şifresi (zorunlu) |
| `ANDROID_KEY_ALIAS` | `jarvis` (zorunlu) |
| `ANDROID_KEY_PASSWORD` | anahtar şifresi (isteğe bağlı; yoksa keystore şifresi kullanılır) |

`gh` CLI ile:
```
gh secret set ANDROID_KEYSTORE_BASE64 < jarvis-release.b64
gh secret set ANDROID_KEYSTORE_PASSWORD
gh secret set ANDROID_KEY_ALIAS --body jarvis
```
Sonra `jarvis-release.b64`'ü sil (keystore'un yedeğini güvenli bir yerde tut).

### 4. Derlemeyi kontrol et
Actions → *JARVIS Derleme* → *Android APK (imzalı release)*. *İmzayı ve versionCode'u doğrula* adımı sertifikanın
SHA-256 parmak izini ve APK'nın versionCode'unu yazar. Durursa:

| Mesaj | Sebep / çözüm |
|---|---|
| `İmzalı APK için GitHub Secrets eksik: …` | Adı yazılan secret'ları ekle (adım 3). |
| `ANDROID_KEYSTORE_BASE64 geçerli bir base64 metni değil` / `… keystore dosyasına benzemiyor` | Base64'ü adım 2'deki komutla dosyanın kendisinden yeniden üret. |
| `ANDROID_KEYSTORE_PASSWORD yanlış` | Keystore şifresini kontrol et. |
| `ANDROID_KEY_ALIAS ("…") keystore'da yok` | `keytool -list -keystore jarvis-release.jks` ile alias'a bak. |
| `İmzasız APK` | `scripts/patch-android.mjs` build.gradle'a imza bloğunu ekleyemedi; derleme günlüğüne bak. |

- **versionCode** her derlemede artar (`run_number + VERSION_CODE_OFFSET`); telefon güncelleme denetimi Release
  notundaki `Android versionCode: N` satırını kullanır. `versionName` etiketten gelir (`v2.5.0` → `2.5.0`).

### 5. Telefonda bir kerelik geçiş
Eski (debug imzalı) uygulama yeni imzalı APK ile güncellenemez. Bir kez:
AYAR → **YEDEK AL** → eski uygulamayı kaldır → yeni APK'yı kur → AYAR → **YEDEK YÜKLE**. Sonraki tüm APK'lar
eskisinin üzerine kurulur.

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
| `BASLAT.bat` | JARVIS'i başlatır, çökerse yeniden başlatır, motor sürekli tarar (önerilen) |
| `ONERI_MODU.bat` | Düzeltmeleri üretir ve test eder ama **koda dokunmaz** (`selfheal_data\oneriler\`) |
| `SADECE_MOTOR.bat` | Sadece motoru çalıştırır |
| `DURUM.bat` | Raporu gösterir (`selfheal_data\RAPOR.md`) |
| `GERI_AL.bat` | Son uygulanan değişikliği geri alır |

```
py run.py --uygulama            # JARVIS + motor, selfheal_ayarlar.json'daki modla
py run.py --mod gozlem          # sadece bul ve raporla
py run.py --mod oneri           # düzelt + test et, ama uygulama
py run.py --bir-kez             # tek tur
py run.py uygula selfheal_data\oneriler\XXXX.json
py run.py geri-al
```

**Varsayılan mod `oneri`:** `KUR.bat` örnek ayarı (`selfheal_ayarlar.ornek.json`) `selfheal_ayarlar.json` olarak kopyalar;
bu dosyada `"mode": "oneri"` yazar. Motor düzeltmeleri üretip test eder ama koda kendisi uygulamaz
(`selfheal_data\oneriler\`, uygulamak için `py run.py uygula …`). Doğrulanan düzeltmelerin kendiliğinden
uygulanmasını istersen `selfheal_ayarlar.json`'da `"mode": "otomatik"` yap.

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
cd mobile && node --test tests/*.test.js                # telefon uygulaması testleri
```
GitHub'da her gönderimde Python 3.9 ve 3.12 ile çalışır (telefon sunucusu testleri için Flask kurulur);
telefon testleri Node 22 ile çalışır. Testler geçmezse APK, paket ve Release oluşmaz.

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
Yerelde imza ortam değişkenleri yoksa debug APK derlenir. GitHub Actions'taki APK sabit anahtarla imzalı release'tir
(yukarıda *Android imzalama (release APK)*).

Sürüm geçmişi: `desktop/CHANGELOG_v3.1.md`, `desktop/CHANGELOG_v3.0.md`, `desktop/CHANGELOG_v2.9.md`, `desktop/CHANGELOG_v2.8.md`, `desktop/CHANGELOG_v2.7.md` … `desktop/CHANGELOG_v2.2.md`, `desktop/CHANGELOG_v2.1.md`,
`desktop/CHANGELOG_v1.4.md`, `desktop/CHANGELOG_v1.3.md`, `desktop/CHANGELOG_v1.2.md`.
