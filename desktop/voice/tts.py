"""
JARVIS v2.2 — Motor seçilebilen ses (TTS) katmanı
=================================================
config.TTS_ENGINE = "gemini" | "elevenlabs" | "edge" | "piper"   (varsayılan "gemini")
v3.1: "piper" — ücretsiz, çevrimdışı, bilgisayarda çalışan Türkçe ses (data/piper/*.onnx).

Seçilen motor hata verirse ya da anahtarı yoksa sırayla
gemini → edge → pyttsx3 denenir ve konsola hangisine düşüldüğü yazılır.

Her motor `synthesize(text)` ile (ses_baytları, biçim) döndürür; biçim "wav" ya da "mp3".
Masaüstü (voice/speaker.py) ve telefon (/api/tts) aynı `synthesize()` fonksiyonunu kullanır.

Gemini isteği (Gemini API belgeleri, "Speech generation", generateContent):
  POST https://generativelanguage.googleapis.com/v1beta/models/<model>:generateContent
  başlık  x-goog-api-key: <GEMINI_API_KEY>
  gövde   {"contents":[{"parts":[{"text": ...}]}],
           "generationConfig":{"responseModalities":["AUDIO"],
             "speechConfig":{"voiceConfig":{"prebuiltVoiceConfig":{"voiceName": ...}}}}}
  cevap   candidates[0].content.parts[0].inlineData.data  (base64)
  gemini-3.1-flash-tts-preview başlıksız ham PCM döndürür (16 bit, mono, 24 kHz) → WAV'a sarılır.
  gemini-3.8-* modelleri hazır WAV döndürür; RIFF başlığı görülürse olduğu gibi kullanılır.
  Ton tarifi: 3.8 modellerinde parça içindeki `speech_metadata.style` alanına, önceki
  modellerde (3.1 / 2.5 önizleme) metnin başına doğal dil yönergesi olarak eklenir.

ElevenLabs isteği:
  POST https://api.elevenlabs.io/v1/text-to-speech/<voice_id>?output_format=mp3_44100_128
  başlık  xi-api-key: <ELEVENLABS_API_KEY>
  gövde   {"text": ..., "model_id": "eleven_multilingual_v2"}
  kredi   GET https://api.elevenlabs.io/v1/user/subscription → character_count / character_limit
"""

import asyncio
import base64
import hashlib
import io
import os
import re
import tempfile
import threading
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "data" / "tts_cache"

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
ELEVEN_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
ELEVEN_SUB_URL = "https://api.elevenlabs.io/v1/user/subscription"

DEFAULT_ENGINE = "gemini"
FALLBACK_ORDER = ("gemini", "edge", "pyttsx3")

DEFAULTS = {
    "TTS_ENGINE": DEFAULT_ENGINE,
    "GEMINI_TTS_MODEL": "gemini-3.1-flash-tts-preview",
    "GEMINI_TTS_VOICE": "Charon",
    "GEMINI_TTS_STYLE": ("Sakin, kendinden emin, hafif esprili, nazik bir uşak gibi, "
                         "net Türkçe telaffuzla konuş."),
    "GEMINI_TTS_TIMEOUT": 30,
    "ELEVENLABS_VOICE_ID": "",
    "ELEVENLABS_MODEL": "eleven_multilingual_v2",
    "ELEVENLABS_OUTPUT_FORMAT": "mp3_44100_128",
    "ELEVENLABS_TIMEOUT": 30,
    "TTS_CACHE_ENABLED": True,
    "TTS_CACHE_MAX_CHARS": 40,
    "TTS_CACHE_MAX_FILES": 300,
    "TTS_CACHE_PHRASES": ["Efendim.", "Hemen bakıyorum.", "Tamamdır.", "Bir saniye efendim.",
                          "Anlaşıldı.", "Buyurun efendim."],
    "TTS_PREFETCH": 2,
    "PIPER_VOICE": "tr_TR-dfki-medium",
    "PIPER_DIR": "data/piper",
    "PIPER_SPEED": None,
    "PIPER_AUTO_DOWNLOAD": True,
}

# Gemini'nin hazır seslerinden SES_DENE.bat'ta denenen erkek sesler.
# (Google belgesi seslere cinsiyet etiketi vermiyor; liste AI Studio'daki ses
# önizlemelerine göre seçildi. Kulakla doğrulamak için SES_DENE.bat'ı çalıştır.)
GEMINI_MALE_VOICES = [
    ("Charon", "Bilgilendirici"),
    ("Orus", "Sağlam"),
    ("Iapetus", "Net"),
    ("Algieba", "Pürüzsüz"),
    ("Rasalgethi", "Bilgilendirici"),
    ("Sadaltager", "Bilgili"),
]


def cfg(name):
    try:
        import config
        return getattr(config, name, DEFAULTS.get(name))
    except Exception:
        return DEFAULTS.get(name)


class TTSUnavailable(Exception):
    """Motorun anahtarı/kütüphanesi yok — hata sayılmaz, sessizce sonrakine geçilir."""


# ------------------------------------------------------------------ yardımcılar
def pcm_to_wav(pcm, rate=24000, channels=1, sample_width=2):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(sample_width)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def _rate_from_mime(mime, default=24000):
    m = re.search(r"rate=(\d+)", mime or "")
    return int(m.group(1)) if m else default


_ABBREV = re.compile(r"\b(?:Dr|Prof|Doç|Av|Sn|vb|vs|bkz|örn|No|Yrd)\.$", re.IGNORECASE)


def split_sentences(text):
    """Uzun cevabı seslendirilecek cümlelere böler. 3.5 gibi sayıları ve 'Dr.' gibi
    kısaltmaları bölmez; satır sonları da cümle sonu sayılır."""
    text = (text or "").strip()
    if not text:
        return []
    parts, buf = [], ""
    for piece in re.split(r"(?<=[.!?…])\s+|\n+", text):
        piece = piece.strip()
        if not piece:
            continue
        buf = f"{buf} {piece}".strip() if buf else piece
        if _ABBREV.search(buf):
            continue
        parts.append(buf)
        buf = ""
    if buf:
        parts.append(buf)
    return parts


# ------------------------------------------------------------------ motorlar
class GeminiEngine:
    name = "gemini"

    def __init__(self, session=None):
        self._session = session

    def _http(self):
        if self._session is None:
            import requests
            self._session = requests
        return self._session

    def signature(self):
        return f"{cfg('GEMINI_TTS_MODEL')}|{cfg('GEMINI_TTS_VOICE')}|{cfg('GEMINI_TTS_STYLE')}"

    @staticmethod
    def uses_speech_metadata(model):
        # 3.8 ve sonrası: ton ayrı alanda. 3.1/2.5 önizleme: metne yönerge olarak.
        m = re.search(r"gemini-(\d+)\.(\d+)", model or "")
        return bool(m) and (int(m.group(1)), int(m.group(2))) >= (3, 8)

    def build_request(self, text, voice=None):
        model = cfg("GEMINI_TTS_MODEL")
        style = (cfg("GEMINI_TTS_STYLE") or "").strip()
        part = {"text": text}
        if style:
            if self.uses_speech_metadata(model):
                part["speech_metadata"] = {"style": style}
            else:
                part["text"] = f"{style.rstrip('.')}. Şu metni oku: {text}"
        body = {
            "contents": [{"parts": [part]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {
                    "voiceName": voice or cfg("GEMINI_TTS_VOICE")}}},
            },
        }
        return GEMINI_URL.format(model=model), body

    def synthesize(self, text, voice=None):
        key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not key:
            raise TTSUnavailable("GEMINI_API_KEY ortam değişkeni yok")
        url, body = self.build_request(text, voice)
        resp = self._http().post(url, json=body, timeout=cfg("GEMINI_TTS_TIMEOUT"),
                                 headers={"x-goog-api-key": key, "Content-Type": "application/json"})
        if resp.status_code != 200:
            raise RuntimeError(f"Gemini HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        try:
            inline = data["candidates"][0]["content"]["parts"][0]["inlineData"]
        except (KeyError, IndexError, TypeError):
            raise RuntimeError(f"Gemini ses döndürmedi: {str(data)[:200]}")
        audio = base64.b64decode(inline.get("data", ""))
        if not audio:
            raise RuntimeError("Gemini boş ses döndürdü")
        if audio[:4] == b"RIFF":
            return audio, "wav"
        return pcm_to_wav(audio, rate=_rate_from_mime(inline.get("mimeType"))), "wav"


class ElevenLabsEngine:
    name = "elevenlabs"

    def __init__(self, session=None):
        self._session = session

    def _http(self):
        if self._session is None:
            import requests
            self._session = requests
        return self._session

    def signature(self):
        return f"{cfg('ELEVENLABS_MODEL')}|{cfg('ELEVENLABS_VOICE_ID')}|{cfg('ELEVENLABS_OUTPUT_FORMAT')}"

    def synthesize(self, text, voice=None):
        key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
        voice_id = voice or str(cfg("ELEVENLABS_VOICE_ID") or "").strip()
        if not key:
            raise TTSUnavailable("ELEVENLABS_API_KEY ortam değişkeni yok")
        if not voice_id:
            raise TTSUnavailable("config.ELEVENLABS_VOICE_ID boş")
        fmt = cfg("ELEVENLABS_OUTPUT_FORMAT") or "mp3_44100_128"
        resp = self._http().post(
            ELEVEN_TTS_URL.format(voice_id=voice_id), params={"output_format": fmt},
            json={"text": text, "model_id": cfg("ELEVENLABS_MODEL")},
            headers={"xi-api-key": key, "Content-Type": "application/json"},
            timeout=cfg("ELEVENLABS_TIMEOUT"))
        if resp.status_code != 200:
            raise RuntimeError(f"ElevenLabs HTTP {resp.status_code}: {resp.text[:200]}")
        if not resp.content:
            raise RuntimeError("ElevenLabs boş ses döndürdü")
        if fmt.startswith("pcm_"):
            return pcm_to_wav(resp.content, rate=int(fmt.split("_")[1])), "wav"
        return resp.content, ("wav" if fmt.startswith("wav_") else "mp3")

    def credits(self):
        key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
        if not key:
            return None
        resp = self._http().get(ELEVEN_SUB_URL, headers={"xi-api-key": key}, timeout=5)
        if resp.status_code != 200:
            return {"error": f"HTTP {resp.status_code}"}
        d = resp.json()
        used, limit = int(d.get("character_count", 0)), int(d.get("character_limit", 0))
        return {"used": used, "limit": limit, "remaining": max(0, limit - used),
                "reset_unix": d.get("next_character_count_reset_unix")}


class EdgeEngine:
    """Eski edge-tts sesi. V1/V2/V3 profilleri voice/speaker.py'de durur; HUD'dan seçilir."""
    name = "edge"

    def signature(self):
        from voice import speaker
        return f"{speaker.VOICE}|{speaker.RATE}|{speaker.PITCH}"

    def synthesize(self, text, voice=None):
        try:
            import edge_tts
        except ImportError:
            raise TTSUnavailable("edge-tts kurulu değil")
        from voice import speaker

        async def run():
            comm = edge_tts.Communicate(text, voice or speaker.VOICE, rate=speaker.RATE, pitch=speaker.PITCH)
            chunks = []
            async for chunk in comm.stream():
                if chunk["type"] == "audio":
                    chunks.append(chunk["data"])
            return b"".join(chunks)

        audio = asyncio.run(run())
        if not audio:
            raise RuntimeError("edge-tts boş ses döndürdü")
        return audio, "mp3"


class Pyttsx3Engine:
    """Çevrimdışı son çare (Windows SAPI5). Sesi WAV dosyasına yazdırıp okur."""
    name = "pyttsx3"
    _lock = threading.Lock()

    def signature(self):
        return "sapi5"

    def synthesize(self, text, voice=None):
        try:
            import pyttsx3
        except ImportError:
            raise TTSUnavailable("pyttsx3 kurulu değil")
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            with self._lock:
                engine = pyttsx3.init("sapi5") if os.name == "nt" else pyttsx3.init()
                engine.setProperty("rate", 175)
                for v in engine.getProperty("voices"):
                    desc = f"{v.name} {v.id}".lower()
                    if "turk" in desc or "tr-tr" in desc:
                        engine.setProperty("voice", v.id)
                        break
                engine.save_to_file(text, path)
                engine.runAndWait()
                engine.stop()
            data = Path(path).read_bytes()
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
        if not data:
            raise RuntimeError("pyttsx3 boş ses üretti")
        return data, "wav"


PIPER_OFFICIAL = {   # rhasspy/piper-voices (Hugging Face) — eksikse ilk kullanımda indirilir
    "tr_TR-dfki-medium": "https://huggingface.co/rhasspy/piper-voices/resolve/main/tr/tr_TR/dfki/medium/tr_TR-dfki-medium",
}


class PiperEngine:
    """v3.1: Piper — ücretsiz, tamamen bilgisayarda çalışan sinir ağı sesi (internet gerekmez, anahtar yok).

    Ses dosyaları data/piper/<ad>.onnx (+ .onnx.json). PIPER_VOICE ile seçilir; PIPER_SPEED < 1 daha hızlı konuşur.
    """
    name = "piper"
    _lock = threading.Lock()
    _voices = {}

    def _voice_name(self):
        return str(cfg("PIPER_VOICE") or "tr_TR-dfki-medium").strip()

    def signature(self):
        return f"{self._voice_name()}|{cfg('PIPER_SPEED') or 1.0}"

    def _model_path(self, name):
        try:
            from core.paths import resolve
            base = resolve(cfg("PIPER_DIR") or "data/piper")
        except Exception:
            base = Path("data/piper")
        return base / f"{name}.onnx"

    def _ensure(self, name):
        path = self._model_path(name)
        if path.exists() and Path(str(path) + ".json").exists():
            return path
        url = PIPER_OFFICIAL.get(name)
        if not url or cfg("PIPER_AUTO_DOWNLOAD") is False:
            raise TTSUnavailable(f"Piper sesi yok: {path} (data/piper klasörüne .onnx ve .onnx.json koy)")
        print(f"[SES] Piper sesi indiriliyor (bir kez, ~60 MB): {name}")
        path.parent.mkdir(parents=True, exist_ok=True)
        http = _requests()
        for suffix in (".onnx.json", ".onnx"):
            r = http.get(url + suffix, timeout=120)
            r.raise_for_status()
            Path(str(path)[:-5] + suffix).write_bytes(r.content)
        return path

    def _load(self):
        name = self._voice_name()
        if name not in self._voices:
            try:
                from piper import PiperVoice
            except ImportError:
                raise TTSUnavailable("piper-tts kurulu değil (py -m pip install piper-tts)")
            self._voices[name] = PiperVoice.load(str(self._ensure(name)))
        return self._voices[name]

    def synthesize(self, text, voice=None):
        import io
        import wave
        with self._lock:
            pv = self._load()
            buf = io.BytesIO()
            with wave.open(buf, "wb") as wf:
                speed = cfg("PIPER_SPEED")
                syn = None
                if speed:
                    try:
                        from piper import SynthesisConfig
                        syn = SynthesisConfig(length_scale=float(speed))
                    except Exception:
                        syn = None
                if syn is not None:
                    pv.synthesize_wav(text, wf, syn_config=syn)
                else:
                    pv.synthesize_wav(text, wf)
        data = buf.getvalue()
        if len(data) <= 44:
            raise RuntimeError("Piper boş ses üretti")
        return data, "wav"


def _requests():
    import requests
    return requests


ENGINES = {
    "gemini": GeminiEngine(),
    "elevenlabs": ElevenLabsEngine(),
    "edge": EdgeEngine(),
    "piper": PiperEngine(),
    "pyttsx3": Pyttsx3Engine(),
}

_state = {"last_engine": None, "last_error": None}
_credit_cache = {"t": 0.0, "value": None}


def engine_chain():
    first = str(cfg("TTS_ENGINE") or DEFAULT_ENGINE).strip().lower()
    if first not in ENGINES:
        print(f"[SES] Bilinmeyen TTS_ENGINE '{first}', {DEFAULT_ENGINE} kullanılıyor.")
        first = DEFAULT_ENGINE
    chain = [first]
    for name in FALLBACK_ORDER:
        if name not in chain:
            chain.append(name)
    return chain


# ------------------------------------------------------------------ önbellek
def _cacheable(text):
    if not cfg("TTS_CACHE_ENABLED"):
        return False
    phrases = {p.strip().lower() for p in (cfg("TTS_CACHE_PHRASES") or [])}
    return text.strip().lower() in phrases or len(text.strip()) <= int(cfg("TTS_CACHE_MAX_CHARS") or 0)


def cache_path(engine, text, fmt):
    sig = f"{engine.name}|{engine.signature()}|{text.strip()}"
    digest = hashlib.sha1(sig.encode("utf8")).hexdigest()[:20]
    return CACHE_DIR / f"{engine.name}_{digest}.{fmt}"


def _cache_lookup(engine, text):
    for fmt in ("wav", "mp3"):
        p = cache_path(engine, text, fmt)
        if p.is_file() and p.stat().st_size > 0:
            try:
                os.utime(p, None)
            except OSError:
                pass
            return p.read_bytes(), fmt
    return None


def _cache_store(engine, text, audio, fmt):
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        p = cache_path(engine, text, fmt)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_bytes(audio)
        os.replace(tmp, p)
        files = sorted((f for f in CACHE_DIR.iterdir() if f.suffix in (".wav", ".mp3")),
                       key=lambda f: f.stat().st_mtime)
        for old in files[:max(0, len(files) - int(cfg("TTS_CACHE_MAX_FILES") or 300))]:
            old.unlink()
    except OSError as exc:
        print("[SES] Önbelleğe yazılamadı:", exc)


# ------------------------------------------------------------------ ana giriş
def synthesize(text):
    """Metni seslendirir: (ses_baytları, biçim, motor_adı). Hiçbiri olmazsa RuntimeError."""
    text = (text or "").strip()
    if not text:
        raise ValueError("boş metin")
    chain = engine_chain()
    errors = []
    for i, name in enumerate(chain):
        engine = ENGINES[name]
        use_cache = name != "pyttsx3" and _cacheable(text)
        if use_cache:
            hit = _cache_lookup(engine, text)
            if hit:
                _state["last_engine"] = name
                return hit[0], hit[1], name
        try:
            audio, fmt = engine.synthesize(text)
        except Exception as exc:  # TTSUnavailable dahil
            errors.append(f"{name}: {exc}")
            nxt = chain[i + 1] if i + 1 < len(chain) else None
            if nxt:
                print(f"[SES] {name} kullanılamadı ({exc}) → {nxt} motoruna düşülüyor.")
            else:
                print(f"[SES] {name} kullanılamadı ({exc}). Başka motor kalmadı.")
            continue
        if use_cache:
            _cache_store(engine, text, audio, fmt)
        _state["last_engine"] = name
        _state["last_error"] = errors[-1] if errors else None
        return audio, fmt, name
    _state["last_error"] = "; ".join(errors)
    raise RuntimeError("Hiçbir TTS motoru çalışmadı: " + "; ".join(errors))


def prewarm(phrases=None):
    """Sık cümleleri (TTS_CACHE_PHRASES) önbellekte yoksa üretir. Arka planda çağrılır."""
    for phrase in phrases or cfg("TTS_CACHE_PHRASES") or []:
        try:
            synthesize(phrase)
        except Exception:
            return


def elevenlabs_credits(max_age=300):
    now = time.time()
    if now - _credit_cache["t"] < max_age:
        return _credit_cache["value"]
    try:
        value = ENGINES["elevenlabs"].credits()
    except Exception as exc:
        value = {"error": str(exc)[:120]}
    _credit_cache.update(t=now, value=value)
    return value


def status():
    """/api/status için özet (anahtarların kendisi asla yazılmaz)."""
    info = {
        "engine": engine_chain()[0],
        "fallback": engine_chain()[1:],
        "last_engine": _state["last_engine"],
        "last_error": _state["last_error"],
        "gemini_key": bool(os.environ.get("GEMINI_API_KEY", "").strip()),
        "gemini_voice": cfg("GEMINI_TTS_VOICE"),
        "elevenlabs_key": bool(os.environ.get("ELEVENLABS_API_KEY", "").strip()),
    }
    if info["elevenlabs_key"]:
        info["elevenlabs_credits"] = elevenlabs_credits()
    return info
