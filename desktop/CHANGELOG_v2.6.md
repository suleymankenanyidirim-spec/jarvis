# JARVIS v2.6 — FASTER-WHISPER + "JARVIS" UYANDIRMA + TELEFON GÜVENLİĞİ

v2.5'teki hiçbir özellik kaldırılmadı. `data/` ve `config_local.py` dokunulmadan kalır. Yeni ayarların hepsi
`config.py`'de varsayılan değerleriyle duruyor. Telefon uygulaması: v1.9.0.

> **Uyumluluk:** PC v2.6 ile telefon uygulaması v1.9 birlikte güncellenmeli.
> Eski uygulamanın PC sesi (GET `/api/tts?token=`) artık 405 döner, eski uygulama cihaz sesine düşer.
> Eski `?token=` linkleri de çalışmaz. Sohbet, hafıza ve görevler etkilenmez: anahtar zaten başlıkla gidiyordu.

## Ses (voice/listener.py)
- **openai-whisper yerine faster-whisper.**
  - Ekran kartı (CUDA) varsa `float16`, yoksa CPU'da `int8` kullanılır.
  - cuDNN/cuBLAS eksikse (yükleme ya da ilk tanıma sırasında) uyarı yazılır ve kendiliğinden CPU `int8`'e geçilir.
  - Tanıma yine Türkçe; konuşma olmayan bölümler `vad_filter` ile atılır.
  - Ayarlar: `WHISPER_MODEL`, `WHISPER_DEVICE` (`auto`/`cuda`/`cpu`), `WHISPER_COMPUTE_TYPE`,
    `WHISPER_BEAM_SIZE`, `WHISPER_VAD_FILTER`.
- **Ctrl+Space bas-konuş aynen çalışıyor.** `VoiceListener.start()` / `stop()` ve HUD'daki akış değişmedi.
- **"Jarvis" uyandırma sözcüğü** (`WAKE_WORD_ENABLED`, varsayılan **kapalı**).
  - Açılınca mikrofon sürekli dinler. "Jarvis" (ya da "Hey Jarvis") deyince kayıt başlar; HUD "LISTENING..." yazar.
  - Motor openWakeWord, hazır `hey_jarvis` modeliyle (ilk açılışta bir kerelik indirilir).
    Eşik `WAKE_WORD_THRESHOLD`, kendi modelin için `WAKE_WORD_MODEL` (.onnx/.tflite yolu).
  - openWakeWord kurulamazsa ya da `WAKE_WORD_ENGINE = "whisper"` ise yedek motor çalışır: kısa konuşma
    parçaları whisper ile yazıya dökülür, "jarvis/carvis/cervis…" aranır (`WAKE_WORD_ALIASES`).
  - JARVIS konuşurken ve Ctrl+Space basılıyken uyandırma dinlenmez (kendi sesiyle tetiklenmesin).
  - Uyandırma başlatılamazsa bas-konuş yine çalışır.
- **Sessizlikte otomatik durma.**
  - Uyandırma ile başlayan kayıt, konuşma bittikten `VOICE_SILENCE_SECONDS` sonra kendiliğinden durur ve yazıya dökülür.
  - Hiç konuşulmazsa `VOICE_NO_SPEECH_TIMEOUT` sonra vazgeçilir. Tek kaydın üst sınırı `VOICE_MAX_RECORD_SECONDS`.
  - Sessizlik eşiği `VOICE_SILENCE_RMS`.
  - Bas-konuşta isteğe bağlı: `VOICE_SILENCE_AUTOSTOP_PTT = True`.
- `requirements.txt`: `openai-whisper` çıktı; `faster-whisper` ve `openwakeword` geldi. `INSTALL.bat`'ı yeniden çalıştır.
- HUD (`interface/gpu_hud.py`): yalnızca dinleyiciyi kurma ve uyandırma geri çağrıları eklendi.

## Telefon sunucusu (core/phone_server.py)
- **Anahtar yalnızca `X-Jarvis-Token` başlığında.** `?token=` ve JSON gövdedeki `token` artık yok sayılır.
  Anahtar URL'lere, tarayıcı geçmişine ve günlüklere düşmez.
- **İlk eşleşme: tek kullanımlık, kısa ömürlü kod.**
  - PC açılışta 8 karakterlik bir kod üretir (ör. `ABCD-2345`, karışan 0/O/1/I yok).
  - Kod konsola, QR'a ve `data/phone_link.txt`'ye yazılır; linkler `?pair=KOD&pcs=…` biçiminde.
  - Erişim anahtarı artık bu dosyaya ve linklere yazılmıyor; elle girmek için `data/phone_token.txt`'de duruyor.
  - `POST /api/pair {"code"}` doğru kodda anahtarı döner. Kod tek kullanımlık, `PHONE_PAIR_CODE_TTL` (300 sn) geçerli.
  - Bir koda `PHONE_PAIR_MAX_FAILURES` hatalı denemede kod iptal olur.
  - Yeni kod: `POST /api/pair/new`, yalnızca PC'nin kendisinden (127.0.0.1 / ::1). Kolay yolu
    `TELEFON_ESLESTIR.bat`; link dosyası da yeni kodla güncellenir.
- **IP başına deneme sınırı.**
  - `PHONE_AUTH_WINDOW` saniyede `PHONE_AUTH_MAX_FAILURES` hatalı anahtar ya da kod gelirse o IP
    `PHONE_AUTH_LOCKOUT` saniye kilitlenir.
  - Kilit süresince 429 ve `Retry-After` döner; doğru anahtar da geçmez.
  - Anahtar hiç gönderilmemişse deneme sayılmaz; başarılı giriş sayacı sıfırlar.
- **CORS yalnızca listedeki kaynaklara.**
  - Varsayılan liste `PHONE_CORS_ORIGINS`: `https://localhost`, `http://localhost`, `capacitor://localhost`.
  - `GITHUB_REPO` doluysa `https://<kullanıcı>.github.io` kendiliğinden eklenir (`PHONE_CORS_GITHUB_PAGES`).
  - İzinli kaynak yansıtılır ve `Vary: Origin` eklenir; listede olmayan kaynağa CORS başlığı gönderilmez.
  - PC'nin kendi sunduğu sayfa ve APK (CapacitorHttp) CORS'tan etkilenmez. `PHONE_ALLOW_CORS = False` hepsini kapatır.
- **`/api/tts` POST oldu.** Gövde `{"text"}` olursa `audio/mpeg` döner, `{"text", "format": "base64"}` olursa
  `{"audio", "mime"}` döner. Eski GET isteklerine 404 değil, açıklamalı 405 döner.

## Telefondan araç kısıtı (core/brain.py, core/tools.py)
- `PHONE_ALLOWED_TOOLS`: telefondan gelen (`source="phone"`) isteklerde kullanılabilecek araçlar.
  - Varsayılan listede `close_application`, `paste_text`, `paste_clipboard` ve `stage_self_update` yok;
    diğer tüm araçlar var.
  - Açmak için `config_local.py`'de listeyi kopyalayıp aracı ekle.
- İki katmanlı kontrol:
  - Ollama'ya telefon isteğinde yalnızca izinli araçların tanımı gönderilir.
  - Model yine de kapalı bir aracı çağırırsa çalıştırılmaz. Modele `ARAÇ_ENGELLİ` sonucu döner, konsola `[GÜVENLİK]` yazılır.
- Telefondan başlatılan arka plan görevi (`run_background_task`) aynı kısıtı devralır; kısıt bu yolla aşılamaz.
- HUD ve PC'de başlatılan görevler eskisi gibi tüm araçları kullanır.
- `core/tools.py`: `TOOL_NAMES` ve `PHONE_BLOCKED_BY_DEFAULT` eklendi.

## Telefon uygulaması (mobile/, v1.9.0)
- **AYAR → Eşleşme kodu + PC İLE EŞLEŞ.** Kod PC adreslerine sırayla gönderilir (önce son çalışan).
  Ağ hatasında sıradaki adres denenir; PC kodu reddederse durulur. Anahtar kendiliğinden kaydedilir.
- PC linki (`?pair=…&pcs=…`) açılınca uygulama kendiliğinden eşleşir.
  - Kod, adresler ve eski `token` parametresi adres çubuğundan silinir.
  - Eski `?token=` linki artık kullanılmaz; uygulama "PC'yi güncelleyin" notu düşer.
- **PC sesi:** `/api/tts` POST + base64. Sıradaki cümlenin sesi önceden istenir.
  Susturulursa (stopSpeaking) bekleyen ses çalınmaz. Hata olursa cihaz sesine düşer.
- **429:** bağlantı testi ve kendiliğinden bağlanma "Çok fazla hatalı deneme… N saniye sonra" mesajını gösterir.
- Saf mantık `www/backend.js`'te: `normalizePairCode`, `isPairCode`, `readLinkParams`, `pairWith`, `pingVerdict`.
- Sürüm: `app.js` / `sw.js` / `package.json` 1.9.0. Service worker önbelleği yenilenir.

## Testler
- `desktop/tests/test_listener.py`: cihaz seçimi, CUDA'dan CPU'ya geçiş, sessizlik algılayıcı,
  uyandırma metni ve motorları, kayıt akışı. Donanım ve paket gerekmez; numpy'li testler numpy yoksa atlanır.
- `desktop/tests/test_phone_security.py`: başlık zorunluluğu, eşleşme kodu, deneme sınırı, CORS, POST TTS.
- `desktop/tests/test_phone_tools.py`: araç listesi tutarlılığı, Ollama'ya giden araçlar, engelleme
  (normal ve akışlı), config ile açma, arka plan görevine devir.
- `desktop/tests/test_phone_server.py`: link testleri `?pair=` ve CORS kaynak listesine uyarlandı.
- `mobile/tests/pairing.test.js`: eşleşme yardımcıları, 429, app.js'in yeni uçlara uyumu.
