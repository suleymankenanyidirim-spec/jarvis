# JARVIS v2.4 — HAFIZA EŞİTLEME (PC TEK DOĞRU KAYNAK) + TÜRKÇE HAFIZA ARAMASI

v2.3'teki hiçbir özellik kaldırılmadı. `data/` ve `config_local.py` dokunulmadan kalır.
Telefon uygulaması: v1.7.0.

## Yeni
- **PC'deki SQLite tek doğru kaynak.** Telefonun kendi ayrı hafıza listesi kalktı. Telefon PC hafızasının son
  eşitlenen kopyasını önbellekte tutar; bulut modunda yaptığı değişiklikleri "bekleyen" olarak saklar ve PC'ye ilk
  bağlantıda gönderir.
- **Yeni uçlar** (hepsi `X-Jarvis-Token` ister):
  - `GET /api/memories` → `{memories: [{id, content, created_at}], count}` (en yeni önce)
  - `POST /api/memories`
    - `{content, client_id?}` ekler → `{status: added | reactivated | exists | synced, id}`
    - `{id, content}` düzenler → `{status: updated | merged, id}` (404: kayıt yok)
    - `{items: [{client_id, content}, …]}` toplu eşitleme → `{results: […], memories: […]}`
  - `DELETE /api/memories/<id>` (ya da `?id=`) → `{deleted, id}`. Kayıt unutulur (`active=0`, `unut` ile aynı).
  - `GET /api/history?session=...&limit=...` → `{session, messages: [{id, role, content, created_at, session_id, source}]}`
    (eskiden yeniye; `limit` varsayılanı `HISTORY_DEFAULT_LIMIT`, üst sınırı `HISTORY_MAX_LIMIT`)
  - `POST /api/history` `{session_id, messages: [{client_id, role, text, t}]}` → `{imported, skipped, invalid, session}`.
    Telefonun bulutta biriktirdiği mesajlar kendi oturumuna eklenir; `task-…` oturumlarına yazılamaz.
  - CORS artık `DELETE`'e de izin veriyor.
- **Aynı içerik iki kez eklenmez.** Kontrol normalleştirilmiş metinle yapılır: boşluk farkı, büyük/küçük harf,
  ı/i farkı ("IP" / "ip") ve sondaki `.`/`!` aynı sayılır. Her bekleyen öğenin bir `client_id`'si vardır; PC aynı
  `client_id`'yi ikinci kez işlemez. Böylece cevap ağda kaybolup istek tekrar gönderilse bile kopya oluşmaz,
  telefondan silinmiş bir kayıt da geri gelmez.
- **Hata düzeltmesi:** unutulmuş bir bilgi tekrar "hatırla" denince v2.3'te `UNIQUE` kısıtı yüzünden
  "zaten hafızada" deniyor ama kayıt geri gelmiyordu. Artık yeniden etkinleşir (`reactivated`).
- **`relevant_memories` Türkçe ekleri hesaba katıyor** (yeni `core/turkish.py`). "rengim neydi" →
  "En sevdiği renk mavi", "kaskımın adresi" → "Kaskın IP adresi…", "İstanbullu" → "İstanbul'da yaşıyor".
  Yöntem:
  - Yaygın çekim ekleri en fazla 3 kez soyulur, kök en az 3 harf kalır, b/c/d/g/ğ yumuşaması geri alınır
    (rengi→renk, kitabı→kitap, ağacı→ağaç).
  - Soyma zincirindeki tüm ara biçimler aday olarak karşılaştırılır; aşırı soyma eşleşmeyi bozmaz (adres / adresi).
  - Durak kelimeler ("benim", "için", "nedir"…) puan getirmez.
  - v2.3'teki alt-dizi eşlemesi yedek olarak durur; eskiden bulunan her şey hâlâ bulunur.
  - `MEMORY_STEMMING = False` ile v2.3 davranışına dönülür.

### Neden embedding değil, kök eşleme?
- Ollama kilidi (`OLLAMA_MAX_CONCURRENT = 1`) yüzünden her soruda bir embedding çağrısı sohbet üretimiyle aynı
  sıraya girer ve her cevabı geciktirirdi.
- Ayrı bir embedding modeli (0,3–1,2 GB) indirmek gerekirdi; Ollama kapalıyken hafıza araması da çalışmazdı.
- Hafıza birkaç düzine kısa cümle; kök eşleme anında, çevrimdışı, deterministik ve test edilebilir.
- Bilinen sınır: eşsesli kökler yanlış eşleşebilir (ör. "araba" / "arap", "kalem" / "kale"). Puan sorudaki
  kelime sayısına göre verildiği için tek kelimelik bu çakışmalar genelde sıralamayı bozmaz.

## Veritabanı (şema 3, otomatik taşıma)
- `memories.norm` sütunu (normalleştirilmiş metin, indeksli). Eski kayıtlar için açılışta doldurulur.
- Yeni `sync_log(client_id, kind, ref_id, created_at)` tablosu: telefondan gelen öğelerin tekrarını engeller.
- Yeni `MemoryStore` yöntemleri: `add_memory`, `update_memory`, `delete_memory`, `list_memories`, `history`,
  `import_messages`. `save_memory` / `forget_memory` / `relevant_memories` aynı imzayla çalışır.

## Telefon uygulaması (mobile/, v1.7.0)
- **Hafıza ekranı** (AYAR → HAFIZA):
  - PC'ye bağlıyken PC'nin hafızasını gösterir; ekle, ✎ düzenle, × sil doğrudan PC'ye yazılır.
  - PC'ye bağlı değilken son eşitlenen PC hafızasını gösterir; değişiklikler turuncu **bekleyen** etiketiyle durur.
  - Üstte bekleyen işlem/mesaj sayısı ve varsa son eşitleme hatası yazar. **EŞİTLE** düğmesi elle eşitler.
- **Bulut modunda** "hatırla …" / "unut …" ve bulutla yapılan konuşma bekleyene yazılır. PC'ye bağlanınca
  (uygulama açılışı, bağlantı kontrolü ya da PC'ye soru göndermeden hemen önce) önce hafıza işlemleri sırayla
  (düzenleme = sil + ekle), sonra mesajlar 200'lük parçalarla gönderilir; ardından PC hafızası yeniden indirilir.
  Sohbete "PC ile eşitlendi: …" notu düşer.
- **Bulut modunun sistem istemi** son eşitlenen PC hafızasını (+ bekleyen eklemeleri, − bekleyen silmeleri,
  en çok 40 kayıt) ve eşitleme zamanını içerir.
- v1.6'daki telefon hafızası ilk açılışta bekleyen eklemelere taşınır ve PC'ye eşitlenir (aynısı PC'de varsa eklenmez).
- Eski (v2.3 ve öncesi) PC'de hafıza uçları yoksa bekleyenler kaybolmaz; hafıza ekranında
  "PC'deki JARVIS eski sürüm" yazar.
- Yedek al / yükle bekleyenleri ve PC önbelleğini de içerir; v1.6 yedeği yüklenirse hafızaları bekleyene geçer.
- Saf mantık `www/sync.js` dosyasında; `index.html` ve `sw.js` önbelleğine eklendi (service worker 1.7.0).

## config.py yeni ayarlar (varsayılanlarıyla)
```python
MEMORY_STEMMING = True
HISTORY_DEFAULT_LIMIT = 50
HISTORY_MAX_LIMIT = 500
SYNC_MAX_ITEMS = 500
```

## Testler
- `desktop/tests/test_turkish.py` (yeni): küçük harf ve normalleştirme, çekimli biçimlerin eşleşmesi, yanlış
  eşleşmeme örnekleri, yumuşama, durak kelimeler.
- `desktop/tests/test_memory.py`:
  - id'li ekle / listele / düzenle / sil, normalleştirilmiş tekrar engeli.
  - Unutulan kaydın geri eklenmesi, `client_id` tekrarı.
  - `history`, `import_messages`, Türkçe ekli sorgularla `relevant_memories`, `MEMORY_STEMMING=False`.
  - Şema 2 → 3 taşıma.
- `desktop/tests/test_phone_server.py`: `/api/memories` CRUD, toplu eşitlemenin tekrar gönderimde kopya üretmemesi,
  sınırlar, `/api/history` GET/POST, `task-` engeli, token kontrolü, CORS `DELETE`, `sync.js`'in arayüzde olması.
- `desktop/tests/test_brain.py`: `MEMORY_STEMMING` ayarının hafıza deposuna iletilmesi.
- `desktop/tests/test_config.py`: yeni ayarlar.
- `mobile/tests/sync.test.js` (yeni):
  - Taşıma, bekleyen ekle / unut / düzenle / sil, önbellek + bekleyen listesi, sistem istemi satırları.
  - Eşitleme sırası, kayıp cevaptan sonra tekrar denemede kopya olmaması, mesajların parçalı gönderimi.

## Yükseltme
- Hiçbir şey yapmana gerek yok: veritabanı ilk açılışta şema 3'e taşınır. Telefondaki eski hafıza kayıtları
  PC'ye ilk bağlantıda eşitlenir.
