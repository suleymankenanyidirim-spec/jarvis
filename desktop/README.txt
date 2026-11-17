JARVIS v2.1
===========
Kurulum: INSTALL.bat -> START_JARVIS.bat
Kendini onarma ile: KUR.bat -> BASLAT.bat (rapor: DURUM.bat). Ayrıntılar: CHANGELOG_v2.1.md
Kişisel ayarlar: config_local.example.py dosyasını config_local.py olarak kopyala.
Telefon: JARVIS açılınca konsolda yazan linki telefonda aç (aynı WiFi) ya da
GitHub Releases'tan APK'yı kur. Ayrıntılar: CHANGELOG_v1.4.md

------------------------------------------------------------------
ESKİ NOTLAR
------------------------------------------------------------------
JARVIS v1.0 — TEMEL SÜRÜM

Bu sürümde sadece arayüz (HUD) yenilendi. Ollama, tool calling, web araştırması,
hafıza, Whisper ve pyttsx3 tarafı v0.6 ile aynı.

YENİ (interface/gpu_hud.py — baştan yazıldı)
- Bloom/parlama efekti, tarama çizgisi, vinyet, yükselen parçacıklar, ızgara
- Reaktör: bölmeli halkalar, dönen parlak yaylar, çark kolları, altıgen, çekirdek
  parlaması. Konuşurken/dinlerken/işlerken hızlanır ve tepki verir.
- CPU / RAM / GPU / TEMP: halka gösterge + canlı akan çizgi grafik
- Storage, Network (Mbps), Power panelleri
- Konsol: gerçek mikrofon seviyesinden canlı ses dalga formu
  (JARVIS konuşurken simüle dalga), mouse tekerleğiyle kaydırma
- AI MODULES: her modülün gerçek durumu (Ollama'ya ping atılır)
- SESSION paneli: uptime, model, cevap sayısı, son cevap süresi
- LOCAL MAP: radar animasyonu + konum
- Hava durumu (Open-Meteo, ücretsiz, anahtar gerektirmez)
- Pencere yeniden boyutlandırılabilir (oran korunur)
- nvidia-smi artık ayrı thread'de: eskiden 2 sn'de bir kare takılıyordu
- Yazı doku önbelleği sınırlandı (eskiden sürekli büyüyordu)
- Metin yazarken SPACE artık boşluk yazar; giriş kutusu boşken bas-konuş çalışır

DEĞİŞEN DOSYALAR
- interface/gpu_hud.py      (yeni)
- voice/listener.py         (mikrofon seviyesi eklendi)
- voice/speaker.py          (konuşuyor bayrağı eklendi)
- config.py                 (HUD ayarları eklendi)
- interface/gpu_hud_v0_6_yedek.py  (eski HUD, geri dönmek istersen)

AYARLAR (config.py)
- LOCATION_NAME / LOCATION_LAT / LOCATION_LON : konum, hava durumu ve harita için
- ENABLE_BLOOM = False : ekran kartı zorlanırsa parlamayı kapatır

KURULUM
1. ZIP'i C:\JARVIS klasörüne çıkar (data\jarvis_memory.db varsa üzerine yazma).
2. INSTALL.bat bir kez çalıştır (yeni paket gerekmez, yine de zarar vermez).
3. START_JARVIS.bat ile başlat.

ESP32/KASK
Bu sürümde de yok. Helmet Control paneli ileride kask modülü eklenince geri gelecek.


V1.0 GİRİŞ KONTROLÜ
-------------------
- Bilgisayarda bas-konuş kısayolu: CTRL + SPACE.
- Telefon web arayüzünde dinleme için DİNLE butonu kullanılır.
- Ses motoru ve mevcut HUD animasyonları değiştirilmemiştir.


JARVIS v1.1 — ARKA PLAN + KONSEY TEMELİ
-----------------------------------------
- BackgroundTaskManager: 3 eşzamanlı arka plan görevi.
- Etkileşimli sohbet artık global lock ile bloke edilmiyor.
- AI Council: Ollama yerel beyin + API anahtarı varsa OpenAI/Anthropic/Gemini.
- Uzman yönlendirme: kod -> Claude öncelikli; araştırma/web -> Gemini öncelikli; genel -> GPT/Claude/Gemini/Ollama.
- SelfUpdater: manifest + SHA-256 doğrulamalı, çalışan dosyaları ezmeden bir sonraki açılışa güncelleme hazırlar.
- ÖNEMLİ: Kendi kendine kod değiştirme doğrudan aktif edilmedi. JARVIS'in test edilmemiş kodu canlı sisteme yazması güvenli değildir. Bunun yerine güncelleme "stage" edilir.
- API anahtarları Windows ortam değişkenlerinden alınır:
  OPENAI_API_KEY
  ANTHROPIC_API_KEY
  GEMINI_API_KEY veya GOOGLE_API_KEY


JARVIS v1.3.1
--------------
Kopyala/yapıştır araçları eklendi: clipboard_copy, clipboard_read, copy_selected_text, paste_clipboard, paste_text.
INSTALL.bat ile yeni Python paketleri kurulmalıdır.
