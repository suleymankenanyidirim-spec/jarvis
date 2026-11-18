# JARVIS v2.9 — YAZI KUTUSU + DAHA İYİ ANLAMA + PROJE BİLGİSİ

v2.8'deki hiçbir özellik kaldırılmadı. `data/` ve `config_local.py` dokunulmadan kalır. Hepsi yerel ve ücretsiz.

## HUD yazı kutusu (interface/gpu_hud.py, interface/textbox.py — yeni)
- Sağ altta soluk **YAZ · T** düğmesi (fare üstüne gelince belirginleşir); tıklayınca ya da **T** ile açılır.
- Harfler pygame'in TEXTINPUT olayından okunur: Türkçe `ğ ş ı İ ö ü ç` ve AltGr ile yazılan `@ € { }` artık doğru gelir
  (önceden KEYDOWN'dan okunuyor, çoğu boş geliyordu). Kutuyu açan "t" harfi yazılmaz.
- İmleç: ← → (Ctrl ile kelime kelime), Home/End, Backspace/Delete (Ctrl+Backspace kelime siler).
- **Ctrl+V** yapıştırır. **↑ / ↓** önceki mesajları geri çağırır (yazdığın taslak kaybolmaz).
- ENTER gönderir, ESC kapatır, Ctrl+BOŞLUK yazarken konuşur.

## Ses tanıma (voice/listener.py)
- `WHISPER_INITIAL_PROMPT`: Türkçe bağlam ve özel adlar (Jarvis, kask, ESP32, Tailscale …) doğru yazılır.
- VAD ayarı (`WHISPER_VAD_MIN_SILENCE_MS`, `WHISPER_VAD_SPEECH_PAD_MS`): cümle ortasındaki duraklamada kesmez,
  konuşmanın başını/sonunu kırpmaz.
- Kısık mikrofonda ses en fazla `VOICE_MAX_GAIN` kat yükseltilir.
- Whisper'ın sessizlikte "duyduğu" altyazı kalıpları ("Altyazı M.K.", "İzlediğiniz için teşekkürler" …) atılır.
- `VOICE_KEEP_MIC_OPEN`: mikrofon açık kalır, BOŞLUK'a basmadan önceki 0,5 sn (`VOICE_PREROLL_SECONDS`) de kayda girer;
  `VOICE_PTT_TAIL_SECONDS`: tuş bırakılınca 0,4 sn daha kaydeder. İlk ve son hece artık kesilmez.
- Model seçimi: bkz. `config_local.py` (`WHISPER_MODEL`).

## Bilgi deposu (core/knowledge.py)
- Kod dosyaları da okunur: `.py .ino .cpp .c .h .hpp .js .gd .bat .ps1`.
- `KNOWLEDGE_EXTRA_DIRS`: proje klasörleri salt okunur taranır, sonuçlarda `@KlasörAdı/dosya` olarak görünür.
  `libraries/ data/ __pycache__ build/ …` klasörleri, `config_local.py`, `wifi_ayarlar.h` ve adında şifre/token geçen
  dosyalar atlanır; koddaki şifre değerleri (`…PASS… = "…"`) `***` olarak gizlenir. 256 KB'tan büyük kod dosyaları okunmaz.
- `D:\JARVIS_Bilgi` içine rehberler (Python, C++/Arduino/ESP32, JARVIS nasıl çalışır) ve proje açıklamaları
  (Game Boy S3, Iron Man kaskı, diğer projeler) eklendi.

## Güvenlik
- Kask sketch'indeki (`KASK_WiFi_BT_Kontrol.ino`) Wi-Fi ağ adı ve şifresi koddan çıkarıldı; artık aynı klasördeki
  `wifi_ayarlar.h` dosyasından okunur (`.gitignore`'da, GitHub'a gitmez). Örnek: `wifi_ayarlar.ornek.h`.

## Testler
- `tests/test_textbox.py` (5), `tests/test_listener_v28.py` (8), `tests/test_knowledge_v29.py` (6).
