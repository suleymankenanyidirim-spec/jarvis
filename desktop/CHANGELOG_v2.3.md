# JARVIS v2.3 — OTURUMLAR + EŞZAMANLILIK KİLİDİ + AKIŞLI CEVAP

v2.2'deki hiçbir özellik kaldırılmadı. `data/` ve `config_local.py` dokunulmadan kalır.
Telefon uygulaması: v1.6.0.

## Yeni
- **Konuşma oturumları.** `messages` tablosuna `session_id` ve `source` (`hud` / `phone` / `task`) sütunları
  eklendi. HUD, her telefon ve her arka plan görevi ayrı oturumda konuşur:
  - **Yakın konuşma** bağlamı (son `RECENT_CONTEXT_MESSAGES` mesaj) yalnızca o oturumdan gelir.
  - **Kalıcı hafıza** (hatırla / unut) herkes için ortaktır.
  - Telefon uygulaması ilk açılışta kendine bir kimlik üretir (`phone-xxxxxxxxxx`) ve her soruyla gönderir.
    Kimlik göndermeyen eski uygulamalar `PHONE_SESSION_ID` (`"phone"`) oturumunu kullanır.
    Telefon `task-…` oturumlarına yazamaz.
- **Otomatik veritabanı taşıma.** Eski `data/jarvis_memory.db` açılışta kendiliğinden taşınır
  (`PRAGMA user_version = 2`); eski mesajlar silinmez, **HUD oturumuna** geçer. Taşıma birden çok kez
  çalışsa da zararsızdır. `(session_id, id)` indeksi eklendi.
- **`brain.ask(text, session_id=None, source=None, allow_planning=True)`.** Parametresiz çağrı eskisi gibi
  çalışır (HUD oturumu, kaynak `hud`), bu yüzden `gpu_hud.py` değişmedi.
- **Arka plan görevleri kendi oturumunda.** `run_background` ve planlı görevler `task-<görev_id>` oturumuna,
  `source="task"` ile yazar; sohbet geçmişini kirletmez ve sohbetin yakın bağlamını görmez.
- **Ollama kilidi.** Flask (threaded), HUD ve arka plan görevleri aynı anda Ollama'yı çağırıyordu. Artık en
  fazla `OLLAMA_MAX_CONCURRENT` (varsayılan 1) çağrı aynı anda yapılır, diğerleri sırada bekler.
  `OLLAMA_LOCK_TIMEOUT` saniye içinde sıra gelmezse "Model meşgul…" hatası verilir. Kilit yalnızca model
  çağrısı süresince tutulur (araç çalışırken bırakılır); akış yarıda kesilse de bırakılır.
- **Hafıza yazma kilidi.** Aynı veritabanı dosyasına yapılan tüm yazmalar tek sıraya alınır
  ("database is locked" hatası olmasın). Aynı dosyayı açan bütün `MemoryStore` nesneleri aynı kilidi paylaşır.
- **`POST /api/ask_stream` (SSE).** Ollama `stream=True` ile cevap parça parça `text/event-stream` olarak döner.
  Gövde: `{"text": "...", "session_id": "phone-..."}`, token `X-Jarvis-Token` başlığıyla. Olaylar
  (`event:` satırı = `type`, `data:` = JSON):
  - `start` — `{session_id}`
  - `wait` — model başka bir istekle meşgul, sıra bekleniyor
  - `tool` — `{name, text: "araç çalışıyor: X"}` (ilk web araması da `web_search` olarak bildirilir)
  - `token` — `{text}` cevap parçası
  - `done` — `{reply}` kaydedilen son cevap
  - `error` — `{error}`
  Araç çağrısından önceki ara metin son cevaba dahil edilmez (eski `ask` ile aynı davranış).
  `PHONE_STREAM_ENABLED = False` yapılırsa uç 404 döner.
- **`/api/ask` çalışmaya devam ediyor**; isteğe bağlı `session_id` alır ve cevapta döndürür. `/ask` (v1.3.1) de duruyor.

## Telefon uygulaması (mobile/, v1.6.0)
- PC modunda cevap **akış halinde** ekrana yazılır (sonunda yanıp sönen imleç), araç çalışırken
  "araç çalışıyor: X" notu ve reaktörde `ARAÇ: X` görünür, model meşgulse "SIRADA".
- **Cümle tamamlandıkça seslendirme**: her cümle sıraya girer ve sırayla okunur; PC sesi (edge-tts) açıksa
  sıradaki cümlenin sesi önceden istenir. Kod blokları ve "Tamam." gibi çok kısa cümleler bölünmez/birleştirilir.
  Reaktöre dokunmak ya da mikrofona basmak sesi ve kuyruğu durdurur; eller serbest modu son cümleden sonra dinler.
- PC `/api/ask_stream`'i tanımıyorsa (v2.2 ve öncesi) kendiliğinden `/api/ask` kullanılır. Akış başladıktan
  sonra bağlantı koparsa soru ikinci kez gönderilmez; başlamadan koparsa eskisi gibi diğer PC adresleri denenir.
- AYAR → yeni seçenek: **"PC cevabını akış halinde göster, cümle cümle oku"** (varsayılan açık).
- Saf mantık `www/stream.js` dosyasında (SSE ayrıştırıcı, cümle bölücü, oturum kimliği); `index.html`
  ve `sw.js` önbelleğine eklendi. Service worker sürümü 1.6.0 (telefonda "GÜNCELLE" bildirimi çıkar).

## config.py yeni ayarlar (varsayılanlarıyla)
```python
HUD_SESSION_ID = "hud"
PHONE_SESSION_ID = "phone"
RECENT_CONTEXT_MESSAGES = 8
OLLAMA_MAX_CONCURRENT = 1
OLLAMA_LOCK_TIMEOUT = 300
PHONE_STREAM_ENABLED = True
```

## Testler
- `desktop/tests/test_memory.py` — oturuma göre yakın bağlam, ortak kalıcı hafıza, geçersiz kaynak/oturum,
  eski şemadan taşıma (+ tekrar çalıştırma), 8 iş parçacığıyla eşzamanlı yazma, paylaşılan kilit.
- `desktop/tests/test_brain.py` (yeni) — `ask` varsayılanları, oturum yalıtımı (HUD / telefon / görev),
  arka plan ve planlı görev oturumları, akış olayları (token / tool / wait / done / hata), Ollama kilidinin
  çakışmayı önlemesi, zaman aşımı, hata ve yarıda kesilen akıştan sonra kilidin bırakılması.
- `desktop/tests/test_phone_server.py` — `/api/ask` oturum kimliği, `task-` engeli, eski `/ask`,
  `/api/ask_stream` SSE biçimi, hata olayı, token kontrolü, kapatma ayarı, `stream.js`'in arayüzde olması.
- `desktop/tests/test_config.py` — yeni ayarlar.
- `mobile/tests/stream.test.js` (yeni) — SSE ayrıştırma (bölünmüş paketler, CRLF, Türkçe), cümle bölücü, oturum kimliği.

## Yükseltme
- Hiçbir şey yapmana gerek yok: veritabanı ilk açılışta taşınır. Eski telefon uygulaması da çalışır
  (tek ortak `phone` oturumunu kullanır); yeni uygulamayla her telefon kendi oturumunu alır.
