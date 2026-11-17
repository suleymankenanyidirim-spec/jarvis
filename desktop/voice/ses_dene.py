"""SES_DENE.bat: aynı Türkçe cümleyi Gemini'nin 6 erkek sesiyle sırayla okur.

Kullanım:  py -m voice.ses_dene ["denenecek cümle"]
Beğendiğin sesin adını config_local.py içine  GEMINI_TTS_VOICE = "Ad"  olarak yaz.
"""
import os
import sys
import tempfile

DEFAULT_SENTENCE = ("İyi akşamlar efendim. Sistemler hazır, hava İstanbul'da on sekiz derece. "
                    "Size nasıl yardımcı olabilirim?")


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    argv = sys.argv[1:] if argv is None else argv
    sentence = " ".join(argv).strip() or DEFAULT_SENTENCE

    from voice import tts
    if not os.environ.get("GEMINI_API_KEY", "").strip():
        print("GEMINI_API_KEY ortam değişkeni yok.")
        print('Önce şunu çalıştır, sonra YENİ bir pencere aç:  setx GEMINI_API_KEY "anahtarın"')
        return 1

    import pygame
    pygame.mixer.init()
    engine = tts.GeminiEngine()
    print("Model :", tts.cfg("GEMINI_TTS_MODEL"))
    print("Cümle :", sentence)
    print()
    worked = []
    for i, (voice, desc) in enumerate(tts.GEMINI_MALE_VOICES, 1):
        print(f"[{i}/{len(tts.GEMINI_MALE_VOICES)}] {voice}  ({desc})")
        try:
            audio, fmt = engine.synthesize(sentence, voice=voice)
        except Exception as exc:
            print("    hata:", exc)
            continue
        fd, path = tempfile.mkstemp(suffix="." + fmt)
        with os.fdopen(fd, "wb") as f:
            f.write(audio)
        try:
            pygame.mixer.music.load(path)
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy():
                pygame.time.wait(50)
            pygame.mixer.music.unload()
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
        worked.append(voice)
        pygame.time.wait(600)

    print()
    if not worked:
        print("Hiçbir ses çalınamadı. Anahtarı ve internet bağlantısını kontrol et.")
        return 1
    print("Beğendiğin sesi config_local.py dosyasına şöyle yaz (örnek):")
    print(f'    GEMINI_TTS_VOICE = "{worked[0]}"')
    print("Sonra JARVIS'i yeniden başlat.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
