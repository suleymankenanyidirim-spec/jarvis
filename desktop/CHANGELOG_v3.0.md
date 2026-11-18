# JARVIS v3.0 — GÖRSEL KARTLAR

v2.9'daki hiçbir özellik kaldırılmadı. `data/` ve `config_local.py` dokunulmadan kalır. Yeni kütüphane gerekmez.

## Nasıl çalışır
Cevaba uygun bir görsel varsa ana görsel (ÇEKİRDEK / HALKA) yumuşakça **sağa kayıp küçülür**, ortada (bilgi
penceresinde solda) bir kart açılır. JARVIS konuşmayı bitirdikten `CARD_HOLD_SECONDS` (2,5 sn) sonra kart söner ve ana
görsel **sağdan ortaya geri kayar**. Kartlar HUD'un kendi çizgileriyle, aynı mavi-siyah renklerle ve bloom ile çizilir.

| Kart | Ne zaman | Ne gösterir |
|---|---|---|
| **Hava durumu** | "Ankara'da hava nasıl", "yarın yağmur yağacak mı", "kaç derece" | Hareketli simge (güneş/ay, bulut, yağmur, kar, sis, şimşek), derece, durum, hissedilen, en yüksek/düşük, yağış ihtimali, nem, rüzgar |
| **Klavye** | Cevapta kısayol geçince ("Ctrl ve C", "Ctrl + Shift + Esc", "Alt F4", "Windows tuşu ve E") | Türkçe Q klavye; tuşlar ana görselden çıkan atomlar gibi yerine uçar. **O kısayol söylendiği anda** tuşları yanar, sıradaki söylenince sönüp yenisi yanar |
| **Kask** | "kaskı aç / kapat", "kask açık mı" | Kaskın önden çizimi; yüz plakası kalkar/iner, kapalıyken gözler parlar; AÇIK / KAPALI |
| **Ev** | "salonun ışığını aç", "tüm ışıkları kapat", "evde hangi ışıklar açık", "klimayı aç", "kombi açık mı" | Odaların eğik mini modeli; yanan ışıklar parlar, klima döner, ısıtma dalgalanır; komut verilen cihaz yanıp söner |
| **Bilgi** (solda) | "… kimdir / nedir / tarihi / hakkında bilgi ver" ve bilgi deposunda yoksa | Vikipedi özeti; konunun resmi mavi çizgilerle **çizilerek** belirir |

## Ayrıntılar
- **Hava durumu**: Open-Meteo (ücretsiz, anahtarsız; yalnızca şehir/koordinat gönderilir). Gerçek veri modele verilir;
  sayılar uydurulmaz. Şehir sorudan bulunur, yoksa `LOCATION_*`. Yeni araç `get_weather`. 10 dk önbellek.
- **Bilgi**: Türkçe Vikipedi (anahtarsız). Özet modele verilir (daha doğru cevap), resim kenar çizgilerine çevrilip
  3,6 sn'de çizilir.
- **Klavye senkronu**: `voice/speaker.py` artık o an söylenen karakter konumunu (`speech_pos`) tutar; HUD bunu kısayolların
  metindeki yerleriyle eşler. Ses yoksa kısayollar sırayla gösterilir.
- **Ev** (`core/home.py`): plan `data/ev.json` (ilk açılışta örnek plan yazılır — kendi evine göre düzenle), durum
  `data/ev_durum.json`. Gerçek bir sisteme bağlanana kadar durumu JARVIS tutar ve kartta **SİMÜLASYON** yazar.
  Bağlantı için `Home(provider=…)` (get_states / set_state). Miola Mobile (EAE) bağlantısı ayrıca araştırılacak.
- **Kask ve ev komutları** modele gitmeden işlenir (küçük model araçları bazen kaçırıyordu); telefon araç kısıtı geçerli.
- Ayarlar: `VISUAL_CARDS`, `VISUAL_WEATHER`, `VISUAL_INFO`, `VISUAL_KEYBOARD`, `HOME_ENABLED`, `CARD_HOLD_SECONDS`.
- Yeni dosyalar: `core/visuals.py`, `core/weather.py`, `core/wiki.py`, `core/home.py`, `interface/cards.py`.

## Testler
- `tests/test_visuals_v30.py` (24): kısayol bulma ve konuşmaya göre seçim, hava/şehir/konu tespiti, resimden çizim,
  Open-Meteo ve Vikipedi (sahte cevaplarla), ev komutları ve sağlayıcı, beyin akışı (hava, ev, kask, klavye, bilgi),
  kart yaşam döngüsü (sağa kayma, konuşma bitince kapanma), klavye düzeni, cümle konumları.
