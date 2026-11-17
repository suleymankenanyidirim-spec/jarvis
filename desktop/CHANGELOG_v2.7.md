# JARVIS v2.7 — SADE EKRAN: "ÇEKİRDEK" VE "HALKA"

PC HUD'ı ve telefon ekranı baştan sadeleşti: ekranda yalnızca seçili modun görseli, altta küçük bir durum
kelimesi ve JARVIS'in son cevabı altyazı olarak kalır. Telefon uygulaması: v2.0.0.

**Arka plan işleri silinmedi**, yalnızca ekrandan kalktı: beyin, araçlar, arka plan görevleri, telefon
sunucusu ve sensör okuma aynen çalışır. `data/` ve `config_local.py` dokunulmadan kalır. Yeni kütüphane gerekmez.

## Kaldırılanlar (yalnızca ekrandan)
Hava durumu, harita, şehir silüeti, sensör panelleri, grafikler, üst başlık çubuğu, sohbet kaydı paneli,
görev paneli, klasik/reaktör temaları, ızgara ve tarama çizgileri.

## Eski HUD
- Eski panel `interface/eski/gpu_hud.py` altına taşındı.
- Geri açmak için `config.py` (ya da `config_local.py`): `HUD_ESKI = True`.
- `main.py` değişmedi: `interface.gpu_hud.GPUHUD` ayara göre yeni ya da eski HUD'ı açar.

## İki mod
- **ÇEKİRDEK**: siyah zeminde yarı saydam, düzensiz, çok yüzlü kristal küre.
  - Camgöbeği ince kenarlar ve yarı saydam yüzeyler; öndekiler parlak, arkadakiler soluk.
  - İçte ters dönen küçük bir kabuk, ortada beyaz-sıcak bir merkez.
  - Merkezden dışarı ışıyan ince flamanlar, çevrede hafif bir parçacık bulutu.
  - Yavaş döner ve hafifçe nefes alır.
- **HALKA**: siyah zeminde ince bir çember ve çevresinde:
  - kalınlıkları farklı, bölmeli yay parçaları;
  - sağda kalın ve parlak tek bir yay;
  - dört ana yönde küçük işaret çizgileri ve sembol grupları;
  - altta kavisli, uçlara doğru sönen ince bir taban çizgisi.
- Seçilen mod `data/hud_settings.json`'a (`"mode"`) kaydedilir. İlk açılış modu: `HUD_MODE`
  (`"cekirdek"` / `"halka"`).

## Dört durum (iki modda da)
| Durum | Görünüş |
|---|---|
| Bekleme | yavaş, sönük |
| Dinleme | parçacıklar / yaylar içe toplanır, parlaklık artar |
| Düşünme | dönüş hızlanır, flamanlar titreşir |
| Konuşma | parlaklık ve sağdaki kalın yayın boyu anlık ses seviyesine göre atar |

- Dinlerken seviye `listener.level`'dan gelir.
- Konuşurken seviye yeni TTS katmanının çalma seviyesinden gelir. `voice/speaker.py`'de `VoiceSpeaker.level`
  ve `level_live` eklendi: çalınan dosyanın 20 ms'lik RMS zarfı çıkarılır ve çalma konumuna göre okunur.
  Zarf çıkarılamazsa (ör. pyttsx3 yedeği) HUD tahmini bir dalga kullanır.

## Yazı
- Altta küçük, soluk bir durum kelimesi: HAZIR / DİNLİYORUM / DÜŞÜNÜYORUM / KONUŞUYORUM.
  Ollama kapalıysa OLLAMA KAPALI, ses tanıma açılamadıysa SES KAPALI yazar.
- JARVIS'in son cevabı altyazı gibi görünür. Uzun cevap ikişer satırlık sayfalar halinde akar.
  Konuşma sürerken son sayfa ekranda kalır, sonra kaybolur. Hatalar da altyazı olarak görünür.

## PC kontrolleri
| Tuş | İş |
|---|---|
| BOŞLUK (basılı tut) | konuş (Ctrl+Boşluk da çalışır; yazı kutusu açıkken yalnızca Ctrl+Boşluk) |
| T | yazı kutusu (Enter gönderir, Esc kapatır) |
| M | mod değiştir |
| ESC | tek, sade ayar katmanı |
| F11 | tam ekran |

- ESC artık JARVIS'i kapatmaz.
- Ayar katmanında şunlar var:
  - mod seçimi (M);
  - ses seçimi V1/V2/V3 (1/2/3) ve etkin TTS motoru;
  - PC bilgileri: işlemci, bellek, ekran kartı, sıcaklık, model, Ollama, sürüm, FPS;
  - telefon bilgileri: adres ve `data/phone_link.txt`;
  - **JARVIS'İ KAPAT** düğmesi.
- Pencereyi kapatmak da JARVIS'i kapatır.

## PC performansı
- Mevcut moderngl + bloom yapısı korundu, 60 FPS (`HUD_FPS`).
- Sahne 4× MSAA ile ve pencerenin gerçek piksel boyutunda çizilir; tam ekranda da keskin kalır.
- Yazılar bloom'dan sonra çizilir.
- Ölçüm (1280×720, ekran dışı): kare başına ~2 ms.
- `ENABLE_BLOOM = False` hâlâ çalışır.

## Telefon (mobile/www, v2.0.0)
- Ana ekranda sekme yok: tam ekran görsel, durum kelimesi ve altyazı.
- Hareketler:
  - **dokun** = konuş (dinlerken tekrar dokun = bitir; konuşurken dokun = sustur ve dinle);
  - **yukarı kaydır** = yazı kutusu (tutamaktan aşağı kaydır ya da ekrana dokun = kapat);
  - **çift dokun** = mod değiştir;
  - **uzun bas** = ayarlar.
- Eski Ayarlar, Sistem/Görevler ve Kask ekranları tek ayar katmanına taşındı.
  - Katmanda ayrıca GÖRÜNÜM (mod, pil tasarrufu) ve SON KONUŞMALAR var.
  - `?view=…` kısayolları bu katmanı açar.
- Klavyeli cihazlarda: Esc = ayarlar, T = yaz, M = mod, Boşluk = konuş.
- Çizim yeni `hud.js` ile yapılır: WebGL (eklemeli karışım), WebGL yoksa canvas 2D.
  - Normalde 60 FPS.
  - "Pil tasarrufu" açıkken, pil %20'nin altında ve şarjda değilken ya da sistemde "hareketi azalt"
    açıkken 30 FPS.
- Telefon sesi ve ses tanıma eskisi gibi çalışır (aynı `/api/tts`, cihaz TTS'i, Web Speech / Capacitor eklentisi).
  - PC sesi sayfayla aynı kaynaktan çalıyorsa (PC'nin sunduğu sayfa) seviye gerçekten ölçülür.
  - APK / GitHub Pages'te ses yönlendirilmez (CORS'ta susmasın diye); görsel tahmini dalga kullanır.
- Service worker sürümü 2.0.0. `hud.js` çevrimdışı önbelleğe eklendi; telefonda "GÜNCELLE" bildirimi çıkar.

## "Seni kim üretti?"
- PC ve telefon aynı sabit cevabı verir:
  "Slov Studio — Made in Türkiye. Süleyman Kenan Yıldırım tarafından yapılmıştır."
- Soru modele gitmeden yakalanır (`core/brain.py` → `is_creator_question`, telefonda `isCreatorQuestion`).
  Telefonda PC, bulut ve çevrimdışı modların hepsinde çalışır.
- Yakalanan örnekler: "seni kim üretti / yaptı / geliştirdi / yarattı", "yapımcın kim",
  "kim tarafından yapıldın", "seni hangi şirket geliştirdi".
- "Bu masayı kim yaptı?" ya da "Python kim tarafından geliştirildi?" gibi başka şeyleri soran sorular
  normal cevaplanır.
- Farklı sorulursa diye sistem talimatına da eklendi.
- Cevap metni: `config.CREATOR_ANSWER` (telefonda `app.js` → `CREATOR_ANSWER`).

## Notlar
- `docs/referans/mod1.jpg` ve `mod2.png` pakette yoktu. Görseller görev tarifine göre çizildi; ekran
  görüntüleri tarifle karşılaştırılıp ayarlandı.
- Değişen dosyalar:
  - `interface/gpu_hud.py` (yeni)
  - `interface/eski/` (eski HUD)
  - `voice/speaker.py` (çalma seviyesi)
  - `config.py` (`HUD_ESKI`, `HUD_MODE`, `HUD_FPS`)
  - `mobile/www/index.html`, `style.css`, `app.js`, `hud.js` (yeni), `sw.js`, `mobile/package.json`
