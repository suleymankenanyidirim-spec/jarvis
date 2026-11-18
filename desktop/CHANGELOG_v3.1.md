# JARVIS v3.1 — PIPER SESİ + HAVA KARTI DÜZELTMESİ

v3.0'daki hiçbir özellik kaldırılmadı. `data/` ve `config_local.py` dokunulmadan kalır.

## Piper ses motoru (voice/tts.py)
- `TTS_ENGINE = "piper"`: ücretsiz, **internetsiz**, tamamen bilgisayarda çalışan sinir ağı sesi (Piper, onnxruntime).
  Bu PC'de 6–7 saniyelik konuşmayı işlemcide ~0,3–0,5 saniyede üretir.
- Ses dosyaları `data/piper/<PIPER_VOICE>.onnx` + `.onnx.json`. Resmi Türkçe ses `tr_TR-dfki-medium` eksikse ilk
  kullanımda Hugging Face'ten (rhasspy/piper-voices) bir kez indirilir (`PIPER_AUTO_DOWNLOAD`).
- Başka bir Piper sesi kullanmak için dosyaları `data/piper/`'a koyup `PIPER_VOICE`'a adını yaz.
- `PIPER_SPEED` (length_scale): 0.9 biraz hızlı, 1.1 biraz yavaş.
- Piper çalışmazsa (paket yok, ses dosyası yok) zincir eskisi gibi gemini → edge → pyttsx3'e düşer.
  Varsayılan motor değişmedi (`gemini`); Piper yalnızca seçilince devreye girer.
- `requirements.txt`: `piper-tts`.

## Hava kartı
- **Hata düzeltmesi:** "izmirde hava nasıl" gibi küçük harfli / ekli yazımlarda şehir bulunamıyor, kart varsayılan
  şehri (İstanbul) gösterirken cevap (modelin ayrıca sorduğu) İzmir'i söylüyordu. Artık:
  - Türkiye'nin 81 ili ve sık sorulan ilçeler her yazımda tanınır ("izmirde", "İzmir'in", "ankaranın", "Muşta").
    Ad + yalnızca hâl eki kabul edilir; "vanilya" Van, "karşıyaka" Kars sayılmaz.
  - Aynı türden ikinci kart gelirse ilkinin yerine geçer (kart her zaman son veriyi gösterir).
- Kartta **"CANLI VERİ · OPEN-METEO · SS:DD"** satırı: verinin çekildiği saat. Önbellek 10 dk → 5 dk.

## Testler
- `tests/test_v31.py` (9): Piper zinciri, WAV üretimi, sesin bir kez yüklenmesi, hız ayarı, ses/paket yoksa yedeğe
  düşme, varsayılan zincirin değişmemesi; şehir yazımları; araçla gelen hava kartının ilkinin yerine geçmesi; güncelleme saati.
