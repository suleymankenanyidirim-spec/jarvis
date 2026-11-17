# JARVIS v2.8 — BİLGİ DEPOSU + DAHA AKILLI YEREL MODEL + İNTERNETTEN TELEFON

v2.7'deki hiçbir özellik kaldırılmadı. `data/` ve `config_local.py` dokunulmadan kalır. Hepsi ücretsiz ve
yerel: hiçbir bulut anahtarı gerekmez, konuşmalar ve dosyalar bilgisayardan çıkmaz. Telefon uygulaması: v2.1.0.

## Yerel modeli daha akıllı kullanma (core/brain.py)
- **Bağlam penceresi:** Ollama'ya `num_ctx` gönderilmiyordu; küçük varsayılan pencerede sistem talimatı, araç
  tanımları, hafıza ve konuşma baştan kesiliyordu. Artık `OLLAMA_NUM_CTX = 8192` (RTX 4060 8 GB'a sığar).
- **Sıcaklık:** `OLLAMA_TEMPERATURE = 0.4` (araç kullanımı daha tutarlı).
- **Düşünme:** `OLLAMA_THINK = "auto"`: kısa sohbette kapalı (hızlı); uzun ya da "neden / nasıl / açıkla /
  hesapla / karşılaştır …" içeren sorularda ve arka plan görevlerinde açık. `True` / `False` de yazılabilir.
  Eski Ollama sürümlerinin cevaba koyduğu `<think>…</think>` metni atılır (akışta da telefona gitmez).
- **Araç seçimi:** `OLLAMA_TOOL_SELECTION = True`: mesajda bir konu geçiyorsa (uygulama aç, kask, pano,
  görev, tarayıcı …) yalnızca o konunun araçları + temel araçlar gönderilir; küçük model 26 araç arasında
  şaşırmaz. Belirli bir konu yoksa hepsi gönderilir. Telefon araç kısıtı (PHONE_ALLOWED_TOOLS) aynen geçerli.
- Sistem talimatına: önce bilgi deposu, sonra web; bilmediğini uydurma.

## Bilgi deposu (core/knowledge.py — yeni)
- Klasör: `KNOWLEDGE_DIR = "D:/JARVIS_Bilgi"` (sürücü yoksa `data/bilgi`). İlk kullanımda `notlar/`,
  `telefon/` ve `BENI_OKU.txt` oluşur.
- Klasöre `.txt .md .csv .json .html` (pypdf kuruluysa `.pdf`) dosyaları atılır; alt klasörler de taranır,
  değişen/silinen dosyalar kendiliğinden fark edilir (`KNOWLEDGE_REFRESH_SECONDS`). Dizin `data/knowledge_index.db`.
- **Önce depo, sonra internet:** her soruda depo aranır (Türkçe ekler hesaba katılır). Bulunursa modele
  "BİLGİ DEPOSU" olarak verilir ve web'e çıkılmaz. Bulunmazsa ve mesaj soru gibiyse (`… nedir`, `kaç`,
  `?` …) web araması yapılır (`KNOWLEDGE_WEB_FALLBACK`). Selamlaşma gibi mesajlarda arama yapılmaz.
- **"depoya kaydet: …"** (ya da "bunu depoya ekle …") modele gitmeden `notlar/notlar_YYYY-AA.md`'ye yazılır.
- Yeni araçlar: `knowledge_search`, `knowledge_save` (telefondan da izinli).
- Akışlı cevapta depo kullanıldıysa `araç çalışıyor: knowledge_search` olayı gider.

## Telefon sunucusu
- `GET /api/knowledge` → dosya listesi; `GET /api/knowledge?q=…` → arama.
- `POST /api/knowledge` `{text, title?}` → not (`telefon/` klasörüne); `{filename, data(base64)}` → dosya.
  Yol dışına yazılamaz, var olan dosyanın üzerine yazılmaz, `KNOWLEDGE_MAX_UPLOAD_MB` (10) sınırı.
- **Tailscale https desteği:** `tailscale serve` arkasında istekler 127.0.0.1'den gelir. Artık gerçek istemci
  `X-Forwarded-For`'dan okunur (`PHONE_TRUST_LOCAL_PROXY`): telefon "PC'nin kendisi" sayılıp yeni eşleşme kodu
  isteyemez, deneme sınırı her cihaz için ayrı işler.
- `TAILSCALE_HTTPS_URL` + `GITHUB_REPO` doluysa `phone_link.txt`'ye GitHub Pages linki (eşleşme koduyla)
  yazılır ve QR onu gösterir: telefon her yerden, https ile bağlanır.

## Telefon uygulaması (v2.1.0)
- AYAR → **BİLGİ DEPOSU**: not gönder, dosya gönder (en fazla 10 MB), depodaki dosyaların listesi.
- https PC adresleri (`https://pc.tailXXXX.ts.net`) 443'te kalır; `:5000` eklenmez.
- Service worker 2.1.0.

## Kurulum yardımcıları
- `MASAUSTU_KISAYOL.bat`: masaüstüne "JARVIS" ve "JARVIS Bilgi Deposu" kısayolları.
- `requirements.txt`: `pypdf` (depodaki PDF'ler için; yoksa PDF'ler atlanır).

## Testler
- `desktop/tests/test_knowledge.py` (yeni, 24 test): depo arama (Türkçe ekler), değişen/silinen dosyalar,
  not ve dosya ekleme güvenliği, önce depo/sonra web akışı, "depoya kaydet", model ayarları (num_ctx,
  sıcaklık, düşünme, `<think>` temizliği), araç seçimi + telefon kısıtı, `/api/knowledge` uçları,
  Tailscale proxy arkasında localhost sayılmama, GitHub Pages linki.
