"""JARVIS sesi (v2.2).

Seslendirme voice/tts.py'deki motor katmanından gelir (config.TTS_ENGINE: gemini / elevenlabs / edge).
Uzun cevap cümlelere bölünür: ilk cümle hemen çalınırken sonrakiler arka planda üretilir.

Eski edge-tts profilleri (V1/V2/V3) burada durur; edge motoru bunları kullanır ve HUD'daki
ayarlar menüsü set_profile() / VOICE ile değiştirmeye devam eder.
"""
import os
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor

# Ses sürümleri geriye dönük korunur. V3 varsayılan ve ayarlar menüsünden seçilebilir.
VOICE_PROFILES = {
    "V1": {"voice": "tr-TR-AhmetNeural", "rate": "+8%", "pitch": "+0Hz", "label": "V1 — Ahmet / Hızlı"},
    "V2": {"voice": "tr-TR-EmelNeural", "rate": "+0%", "pitch": "+0Hz", "label": "V2 — Emel / Dengeli"},
    "V3": {"voice": "tr-TR-AhmetNeural", "rate": "+0%", "pitch": "-4Hz", "label": "V3 — Ahmet / Daha tok"},
}
VOICE = VOICE_PROFILES["V3"]["voice"]
RATE = VOICE_PROFILES["V3"]["rate"]
PITCH = VOICE_PROFILES["V3"]["pitch"]


def set_profile(name):
    global VOICE, RATE, PITCH
    profile = VOICE_PROFILES.get(name, VOICE_PROFILES["V3"])
    VOICE, RATE, PITCH = profile["voice"], profile["rate"], profile["pitch"]
    return profile


def sentence_offsets(text, sentences):
    """Her cümlenin metindeki başlangıç karakteri (bulunamazsa önceki cümlenin sonu)."""
    out, cur = [], 0
    for s in sentences:
        i = text.find(s.strip()[:20], cur) if s.strip() else -1
        if i < 0:
            i = cur
        out.append(i)
        cur = i + len(s.strip())
    return out


class VoiceSpeaker:
    def __init__(self, prewarm=True):
        import pygame
        self.pygame = pygame
        self.lock = threading.Lock()
        self.speaking = False
        # Anlık çalma seviyesi (0..1). HUD parlaklığı / kalın yay boyu buna göre atar.
        # level_live False ise seviye bilinmiyor demektir (ör. pyttsx3 yedeği): HUD tahmini dalga kullanır.
        self.level = 0.0
        self.level_live = False
        # v3.0: o an söylenen yer (konuşulan metindeki karakter konumu). HUD klavye kartı tuşları buna göre yakar.
        self.speech_text = ""
        self.speech_pos = 0.0
        pygame.mixer.init()
        if prewarm:
            from voice import tts
            threading.Thread(target=tts.prewarm, daemon=True).start()

    def speak(self, text):
        if text:
            threading.Thread(target=self._worker, args=(text,), daemon=True).start()

    def _worker(self, text):
        from voice import tts
        with self.lock:
            self.speaking = True
            self.speech_text, self.speech_pos = text, 0.0
            try:
                sentences = tts.split_sentences(text) or [text]
                offsets = sentence_offsets(text, sentences)
                workers = max(1, int(tts.cfg("TTS_PREFETCH") or 2))
                # Cümleler sırayla kuyruğa girer: ilki hemen üretilir, çalınırken
                # sonrakiler arka planda hazırlanır.
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    jobs = [pool.submit(tts.synthesize, s) for s in sentences]
                    for sentence, job, start in zip(sentences, jobs, offsets):
                        self.speech_pos = float(start)
                        try:
                            audio, fmt, _engine = job.result()
                        except Exception as exc:
                            print("[SES] Seslendirilemedi, doğrudan pyttsx3:", exc)
                            self._fallback(sentence)
                            continue
                        self._cur = (start, len(sentence))
                        self._play(audio, fmt)
                    self.speech_pos = float(len(text))
            finally:
                self.speaking = False
                self.level = 0.0
                self.level_live = False

    def _play(self, audio, fmt):
        start, length = getattr(self, "_cur", (0, 0))
        music = self.pygame.mixer.music
        fd, path = tempfile.mkstemp(suffix="." + fmt)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(audio)
            env = self._envelope(path)
            duration = len(env) * self.ENV_STEP if env is not None else max(0.5, length / 14.0)
            music.load(path)
            music.play()
            self.level_live = env is not None
            while music.get_busy():
                pos_s = max(0.0, music.get_pos() / 1000.0)
                if env is not None:
                    i = int(pos_s / self.ENV_STEP)
                    self.level = float(env[i]) if 0 <= i < len(env) else 0.0
                self.speech_pos = start + min(1.0, pos_s / duration) * length
                self.pygame.time.wait(20)
            self.level = 0.0
        except Exception as exc:
            print("[SES] Çalma hatası:", exc)
        finally:
            try: music.unload()
            except Exception: pass
            try: os.remove(path)
            except OSError: pass

    ENV_STEP = 0.02   # zarf çözünürlüğü (sn)

    def _envelope(self, path):
        """Ses dosyasının 20 ms'lik RMS zarfını (0..1) çıkarır. Çözülemezse None."""
        try:
            import numpy as np
            snd = self.pygame.mixer.Sound(path)
            arr = self.pygame.sndarray.array(snd).astype("f4")
            if arr.ndim > 1:
                arr = arr.mean(axis=1)
            freq = (self.pygame.mixer.get_init() or (22050,))[0]
            hop = max(1, int(freq * self.ENV_STEP))
            n = len(arr) // hop
            if n == 0:
                return None
            rms = np.sqrt(np.square(arr[:n * hop].reshape(n, hop)).mean(axis=1))
            peak = float(np.percentile(rms, 95)) or 1e-6
            return np.clip(rms / peak, 0.0, 1.0)
        except Exception:
            return None

    def _fallback(self, text):
        try:
            import pyttsx3
            engine = pyttsx3.init("sapi5")
            engine.setProperty("rate", 175); engine.setProperty("volume", 1.0)
            for voice in engine.getProperty("voices"):
                desc = f"{voice.name} {voice.id}".lower()
                if "turk" in desc or "tr-tr" in desc or "turkish" in desc:
                    engine.setProperty("voice", voice.id); break
            engine.say(text); engine.runAndWait(); engine.stop()
        except Exception as exc: print("Fallback TTS de başarısız:", exc)
