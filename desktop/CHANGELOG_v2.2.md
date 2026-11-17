# JARVIS v2.2 — YENİ SES: GEMINI / ELEVENLABS / EDGE MOTOR SEÇİMİ

v2.1'deki hiçbir özellik kaldırılmadı. `data/` ve `config_local.py` dokunulmadan kalır.
Yeni kütüphane gerekmez (Gemini ve ElevenLabs `requests` ile çağrılır).

## Ses motoru (voice/tts.py — yeni)
- `config.TTS_ENGINE = "gemini" | "elevenlabs" | "edge"` (varsayılan `"gemini"`).
- Seçilen motor hata verirse ya da anahtarı yoksa sırayla **gemini → edge → pyttsx3** denenir.
  Konsola örn. `[SES] gemini kullanılamadı (GEMINI_API_KEY ortam değişkeni yok) → edge motoruna düşülüyor.` yazılır.
- Masaüstü ve telefon aynı `tts.synthesize()` fonksiyonunu kullanır.

### Gemini
- Model `GEMINI_TTS_MODEL` (varsayılan `gemini-3.1-flash-tts-preview`), ses `GEMINI_TTS_VOICE`
  (varsayılan `Charon`), ton `GEMINI_TTS_STYLE`. Anahtar **`GEMINI_API_KEY` ortam değişkeninden**.
- İstek: `POST v1beta/models/<model>:generateContent`, `x-goog-api-key` başlığı,
  `responseModalities: ["AUDIO"]`, `speechConfig.voiceConfig.prebuiltVoiceConfig.voiceName`.
- 3.1 önizleme modeli başlıksız ham PCM (16 bit, mono, 24 kHz) döndürür → WAV'a sarılıp pygame ile çalınır.
  Örnekleme hızı cevaptaki `mimeType`'tan (`rate=`) okunur. Cevap zaten WAV ise (`RIFF`) olduğu gibi kullanılır.
- Ton tarifi: 3.1/2.5 önizleme modellerinde metnin başına yönerge olarak eklenir; Google'ın yeni
  `gemini-3.8-*` TTS modellerine geçilirse kendiliğinden `speech_metadata.style` alanına konur.

### ElevenLabs
- `ELEVENLABS_API_KEY` ortam değişkeni + `config.ELEVENLABS_VOICE_ID`, model `ELEVENLABS_MODEL`
  (varsayılan `eleven_multilingual_v2`, Türkçe destekli). Çıktı `ELEVENLABS_OUTPUT_FORMAT` (`mp3_44100_128`).
- Kalan kredi **`/api/status` → `tts.elevenlabs_credits`** içinde: `used`, `limit`, `remaining`, `reset_unix`
  (`/v1/user/subscription`, 5 dakika önbellekli).

### Edge (eski ses)
- V1/V2/V3 edge-tts profilleri `voice/speaker.py`'de duruyor; HUD ayarlarındaki seçim edge motorunda
  aynen çalışır. `TTS_ENGINE = "edge"` yazarsan eski sese dönersin.

## Hız
- Uzun cevap cümlelere bölünür (`3.5` gibi sayılar ve `Dr.` gibi kısaltmalar bölünmez).
  İlk cümle üretilir üretilmez çalınır, sonrakiler arka planda hazırlanır (`TTS_PREFETCH = 2`).
- Kısa cümleler (`TTS_CACHE_MAX_CHARS = 40` karaktere kadar) ve `TTS_CACHE_PHRASES` listesindekiler
  ("Efendim.", "Hemen bakıyorum." …) `data/tts_cache/` içinde saklanır. Liste açılışta arka planda hazırlanır.
  Önbellek anahtarı motor + ses + model + tonu içerir; ses değişince yenisi üretilir. En fazla
  `TTS_CACHE_MAX_FILES = 300` dosya, en eskiler silinir. Kapatmak için `TTS_CACHE_ENABLED = False`.

## Telefon
- `/api/tts` artık aynı motoru kullanır. Cevap tipi motora göre `audio/wav` (Gemini) ya da `audio/mpeg`
  (edge/ElevenLabs). Telefon uygulamasında değişiklik gerekmez.
- `/api/status` cevabına `tts` bölümü eklendi (motor, yedekler, son kullanılan motor, anahtar var/yok —
  anahtarların kendisi asla gönderilmez).

## SES_DENE.bat (yeni)
- Aynı Türkçe cümleyi Gemini'nin 6 erkek sesiyle (Charon, Orus, Iapetus, Algieba, Rasalgethi, Sadaltager)
  sırayla okur, her sesin adını önce ekrana yazar. Kendi cümlen için: `SES_DENE.bat "denenecek cümle"`.
- Beğendiğini `config_local.py` içine yaz: `GEMINI_TTS_VOICE = "Orus"`.
- Not: Google belgesi seslere cinsiyet etiketi vermiyor; liste AI Studio önizlemelerine göre seçildi.

## Kurulum
```
setx GEMINI_API_KEY "anahtarın"
setx ELEVENLABS_API_KEY "anahtarın"     (isteğe bağlı)
```
`setx` sonrası yeni bir pencere açıp JARVIS'i başlat.

## Testler
- `tests/test_tts.py` (30 test): Gemini/ElevenLabs istek biçimi, PCM→WAV, yedek zinciri, önbellek,
  cümle bölme, kredi durumu, konuşma hattı, telefon `/api/tts` ve `/api/status`. Tüm ağ istekleri taklit edilir.

---

# JARVIS v2.2 — EV DIŞINDAN BAĞLANTI (TAILSCALE) + ARKA PLAN MODU

v2.1'deki hiçbir özellik kaldırılmadı. `data/` ve `config_local.py` dokunulmadan kalır.
Telefon uygulaması: v1.5.0.

## Yeni
- **Tailscale ile her yerden bağlantı.** Telefon ev dışındayken de PC'deki JARVIS'e (aynı beyin, hafıza,
  araçlar) bağlanır. Kurulum 5 adımda: kökteki `README.md` → *Ev dışından bağlantı (Tailscale)*.
- **`py main.py --headless`**: HUD açılmaz, pygame yüklenmez; yalnızca beyin + telefon sunucusu
  (+ otomatik güncelleme kontrolü) çalışır. Çıktı `data/jarvis_headless.log` dosyasına da yazılır
  (`pyw` ile konsolsuz çalışırken de). Telefon sunucusu başlatılamazsa çıkış kodu 1.
- **`KUR_OTOMATIK_BASLAT.bat`**: Windows Başlangıç klasörüne "JARVIS (arka plan)" kısayolu koyar
  (`pyw main.py --headless`, pencere açılmaz, yönetici izni gerekmez). İsterse hemen de başlatır.
  Kaldırmak için **`OTOMATIK_BASLAT_KALDIR.bat`**.
- **Açılışta iki link**: konsola ve `data/phone_link.txt` içine hem ev WiFi (LAN) hem Tailscale (100.x)
  linki, uygulamaya girilecek adres listesi ve erişim anahtarı yazılır. Linkler `pcs=` parametresiyle iki
  adresi de taşır; telefon linki bir kez açınca iki adresi de öğrenir. QR kod Tailscale linkini gösterir
  (evde de dışarıda da çalışır). Tailscale adresi sırasıyla `TAILSCALE_IP` ayarından, `tailscale ip -4`
  komutundan ya da ağ arayüzlerinden bulunur.

## Telefon uygulaması (mobile/, v1.5.0)
- Tek "PC adresi" yerine **sıralı adres listesi** (AYAR → PC adresleri, her satıra bir adres).
  Eski `pcUrl` ayarı kendiliğinden listeye taşınır.
- `resolveBackend` adresleri sırayla dener (önce son çalışan), **çalışanı hatırlar**; üstte
  `PC BAĞLI · EV` / `PC BAĞLI · TAILSCALE` yazar. *Bağlantıyı test et* her adresi ayrı ayrı gösterir.
- Otomatik modda bulut **yalnızca hiçbir PC adresine ulaşılamazsa** devreye girer; ekranın üstünde turuncu
  **"PC'ye ulaşılamıyor, sınırlı mod"** şeridi çıkar (bulut anahtarı yoksa bu da yazılır).
- Mesaj gönderirken PC cevap vermezse önce diğer adresler denenir; buluta ancak hiçbiri yoksa geçilir.
  PC'nin kendisi hata döndürdüyse (ör. `500: …`) soru ikinci kez gönderilmez.
- Bağlantı mantığı test edilebilsin diye `www/backend.js` dosyasına ayrıldı (`sw.js` önbelleğine eklendi).
  Elle seçilen PC / BULUT modları eskisi gibi çalışır.

## Değişiklikler
- **`data/` yolları artık `main.py`'nin klasörüne göre çözülür** (çalışma klasörüne göre değil). JARVIS
  Başlangıç kısayolundan ya da başka bir klasörden açılsa da aynı hafızayı kullanır. Yeni `core/paths.py`
  (`BASE_DIR`, `resolve()`, `data_path()`); `memory.py`, `background.py`, `tools.py`, `phone_server.py`,
  `interface/gpu_hud.py` bunu kullanır. Mutlak yollar olduğu gibi kalır.
- `brain.py`: `config.MEMORY_DB` ayarı artık gerçekten kullanılıyor (önceden yok sayılıyordu; varsayılan aynı).
- `phone_server.py`: port doluysa (ör. arka planda `--headless` JARVIS çalışıyorsa) sunucu başlatılmaz,
  açıkça yazılır; `start_phone_server` bu durumda `None` döner.
- `config.py` yeni ayarlar (varsayılanlarıyla): `TAILSCALE_IP = ""`, `TAILSCALE_DETECT = True`,
  `HEADLESS_LOG_FILE = "data/jarvis_headless.log"`.
- GitHub Actions: telefon uygulaması testleri (`node --test`) eklendi; APK ve PC paketi bunlar geçmeden
  oluşmaz. Telefon sunucusu testleri için CI'de Flask kurulur.

## Testler
- `desktop/tests/test_paths.py` — yol çözümü (başka cwd'de), memory/background göreli yollar.
- `desktop/tests/test_main.py` — `--headless` argümanı, HUD'un yüklenmemesi, log dosyası, konsolsuz (pyw) çalışma.
- `desktop/tests/test_phone_server.py` — Tailscale IP tespiti, LAN + Tailscale linkleri, `phone_link.txt`, port kontrolü.
- `desktop/tests/test_config.py` — yeni ayarlar.
- `mobile/tests/backend.test.js` — adres listesi, taşıma, sıralı deneme, hatırlama, sınırlı mod kararı.

## Yükseltme
- `KUR_OTOMATIK_BASLAT.bat` isteğe bağlıdır; kurmazsan JARVIS eskisi gibi `START_JARVIS.bat` ile açılır.
- Telefonda: PC'nin yeni linkini bir kez aç ya da AYAR → PC adreslerine Tailscale adresini ikinci satır olarak ekle.
