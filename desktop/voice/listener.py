"""Ses tanıma (v2.6): faster-whisper + "Jarvis" uyandırma sözcüğü + sessizlikte otomatik durma.

- Bas-konuş (Ctrl+Space / Boşluk) eskisi gibi: start() kaydı başlatır, stop() yazıya döker.
- faster-whisper: ekran kartı (CUDA) varsa float16, yoksa CPU int8. cuDNN/cuBLAS eksikse (yüklerken ya da
  ilk tanımada) uyarı yazılır ve CPU int8'e geçilir. Tanıma Türkçe, konuşma olmayan bölümler vad_filter ile atılır.
- Uyandırma (WAKE_WORD_ENABLED, varsayılan kapalı): mikrofon sürekli dinlenir, "Jarvis" / "Hey Jarvis" deyince
  kayıt başlar ve konuşma bitince (VOICE_SILENCE_SECONDS) kendiliğinden durur. Motor openWakeWord (hazır
  hey_jarvis modeli); kurulamazsa ya da WAKE_WORD_ENGINE = "whisper" ise kısa parçalar whisper ile yazıya
  dökülüp "jarvis/carvis/cervis…" aranır. JARVIS konuşurken ve bas-konuş basılıyken uyandırma dinlenmez.
"""
import collections
import math
import queue
import re
import threading
import time

try:
    import numpy as np
except ImportError:          # CI: numpy yok; numpy gerektiren yollar kullanılmaz
    np = None

SAMPLE_RATE = 16000
BLOCK_SIZE = 1600            # 100 ms mikrofon bloğu
WAKE_FRAME = 1280            # 80 ms — openWakeWord'ün beklediği kare boyu
DEFAULT_WAKE_WORDS = ("jarvis", "carvis", "cervis", "jarviz", "carviz", "cervız", "yarvis", "jervis")


def _cfg(name, default=None):
    try:
        import config
        return getattr(config, name, default)
    except Exception:
        return default


# ------------------------------------------------------------------ cihaz seçimi
def cuda_available():
    """ctranslate2 (faster-whisper'ın motoru) bir CUDA aygıtı görüyor mu?"""
    try:
        import ctranslate2
        return int(ctranslate2.get_cuda_device_count()) > 0
    except Exception:
        return False


def pick_device(device="auto", compute_type="", has_cuda=None):
    """(aygıt, hesap türü). auto: CUDA varsa cuda/float16, yoksa cpu/int8. compute_type doluysa o kullanılır."""
    device = str(device or "auto").strip().lower()
    if device not in ("cuda", "cpu"):
        if has_cuda is None:
            has_cuda = cuda_available()
        device = "cuda" if has_cuda else "cpu"
    default = "float16" if device == "cuda" else "int8"
    return device, (str(compute_type).strip() or default)


# ------------------------------------------------------------------ ses seviyesi / sessizlik
def rms_of(samples):
    if np is not None:
        arr = np.asarray(samples, dtype=np.float32).reshape(-1)
        return float(np.sqrt(np.mean(np.square(arr)))) if arr.size else 0.0
    values = [float(x) for x in samples]
    if not values:
        return 0.0
    return math.sqrt(sum(v * v for v in values) / len(values))


class SilenceDetector:
    """Kayıt ne zaman bitsin? update(rms, saniye) -> None | "silence" | "no_speech" | "max_length"."""
    EPS = 1e-6

    def __init__(self, threshold=0.01, silence_seconds=1.2, no_speech_timeout=6.0, max_seconds=30.0):
        self.threshold = float(threshold)
        self.silence_seconds = float(silence_seconds)
        self.no_speech_timeout = float(no_speech_timeout) if no_speech_timeout else 0.0
        self.max_seconds = float(max_seconds) if max_seconds else 0.0
        self.elapsed = 0.0
        self.silent_for = 0.0
        self.heard_speech = False

    def update(self, rms, dt):
        self.elapsed += dt
        if rms >= self.threshold:
            self.heard_speech = True
            self.silent_for = 0.0
        else:
            self.silent_for += dt
        if self.max_seconds and self.elapsed + self.EPS >= self.max_seconds:
            return "max_length"
        if not self.heard_speech:
            if self.no_speech_timeout and self.elapsed + self.EPS >= self.no_speech_timeout:
                return "no_speech"
            return None
        if self.silent_for + self.EPS >= self.silence_seconds:
            return "silence"
        return None


# ------------------------------------------------------------------ v2.8: tanıma kalitesi
# Whisper sessizlikte ya da gürültüde Türkçe altyazı kalıplarını "duyar"; bunlar atılır.
HALLUCINATIONS = (
    "altyazı m.k", "altyazı", "izlediğiniz için teşekkür", "izlediğiniz için teşekkürler",
    "abone olmayı unutmayın", "sesli betimleme",
)


def clean_transcript(text):
    t = " ".join(str(text or "").split())
    low = t.replace("I", "ı").replace("İ", "i").lower().strip(" .!…")
    if not low:
        return ""
    for h in HALLUCINATIONS:
        h = h.strip(" .!")
        if low == h or (low.startswith(h) and len(low) - len(h) < 4):
            return ""
    return t


def prepare_audio(audio):
    """Kısık mikrofonda sesi yükseltir (tepe ~0.9), çok gürültülü kırpmayı önler. numpy dizisi değilse dokunmaz."""
    if np is None or not isinstance(audio, np.ndarray) or not audio.size:
        return audio
    audio = audio.astype(np.float32, copy=False)
    peak = float(np.max(np.abs(audio)))
    if 0.0005 < peak < 0.5:
        audio = audio * min(0.9 / peak, float(_cfg("VOICE_MAX_GAIN", 8.0)))
    return audio


# ------------------------------------------------------------------ uyandırma sözcüğü
def _fold(text):
    return str(text or "").replace("I", "ı").replace("İ", "i").lower().replace("ı", "i")


_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def contains_wake_word(text, words=None):
    """Metinde "jarvis" (ya da benzeri bir yazımı) geçiyor mu? "JARVIS'cim", "Hey Jarvis!" de sayılır."""
    if words is None:
        words = _cfg("WAKE_WORD_ALIASES", None) or DEFAULT_WAKE_WORDS
    wanted = [_fold(w) for w in words if str(w).strip()]
    for token in _WORD_RE.findall(_fold(text)):
        for w in wanted:
            if token == w or (len(w) >= 4 and token.startswith(w)):
                return True
    return False


class WhisperWakeDetector:
    """Yedek uyandırma motoru: kısa konuşma parçalarını whisper ile yazıya döker, "jarvis" arar."""
    name = "whisper"

    def __init__(self, transcribe, threshold=0.02, max_seconds=2.5, end_silence=0.4,
                 sample_rate=SAMPLE_RATE, words=None):
        self.transcribe = transcribe
        self.threshold = float(threshold)
        self.max_seconds = float(max_seconds)
        self.end_silence = float(end_silence)
        self.sample_rate = sample_rate
        self.words = words
        self.reset()

    def reset(self):
        self.buffer = []
        self.collected = 0.0
        self.silence = 0.0

    def _audio(self):
        if np is not None:
            return np.concatenate([np.asarray(f, dtype=np.float32) for f in self.buffer]) / 32768.0
        out = []
        for f in self.buffer:
            out.extend(float(x) / 32768.0 for x in f)
        return out

    def feed(self, frame):
        dur = len(frame) / float(self.sample_rate)
        loud = rms_of(frame) / 32768.0 >= self.threshold
        if not self.buffer:
            if not loud:
                return False
            self.buffer.append(frame)
            self.collected = dur
            return False
        self.buffer.append(frame)
        self.collected += dur
        self.silence = 0.0 if loud else self.silence + dur
        if self.silence + 1e-6 < self.end_silence and self.collected + 1e-6 < self.max_seconds:
            return False
        audio = self._audio()
        self.reset()
        try:
            return contains_wake_word(self.transcribe(audio) or "", self.words)
        except Exception as exc:
            print("[SES] Uyandırma tanıma hatası:", exc)
            return False


class OpenWakeWordDetector:
    name = "openwakeword"

    def __init__(self, model, threshold=0.5):
        self.model = model
        self.threshold = float(threshold)

    def feed(self, frame):
        scores = self.model.predict(frame) or {}
        return bool(scores) and max(float(v) for v in scores.values()) >= self.threshold

    def reset(self):
        try:
            self.model.reset()
        except Exception:
            pass


def make_wake_detector(engine, transcribe, threshold=None, model_path=None):
    """openwakeword (hazır hey_jarvis modeli ya da kendi .onnx/.tflite modelin) ya da whisper yedeği."""
    engine = str(engine or "openwakeword").strip().lower()
    if engine != "whisper":
        try:
            import openwakeword
            from openwakeword.model import Model
            if model_path:
                models = [str(model_path)]
                framework = "tflite" if str(model_path).lower().endswith(".tflite") else "onnx"
            else:
                models, framework = ["hey_jarvis"], "onnx"
                openwakeword.utils.download_models(model_names=["hey_jarvis"])   # ilk açılışta bir kez
            model = Model(wakeword_models=models, inference_framework=framework)
            return OpenWakeWordDetector(model, 0.5 if threshold is None else threshold)
        except Exception as exc:
            print(f"[SES] openWakeWord kullanılamadı ({exc}); uyandırma için whisper yedeği çalışıyor.")
    return WhisperWakeDetector(transcribe, threshold=_cfg("VOICE_SILENCE_RMS", 0.02))


# ------------------------------------------------------------------ dinleyici
class VoiceListener:
    def __init__(self, model_name=None, sample_rate=SAMPLE_RATE):
        self.model_name = model_name or _cfg("WHISPER_MODEL", "small")
        self.sample_rate = sample_rate
        self.model = None
        self.device = None
        self.compute_type = None
        self.recording = False
        self.chunks = []
        self.stream = None
        self.level = 0.0          # anlık mikrofon seviyesi (0..1), HUD görseli için
        self._lock = threading.RLock()
        self._detector = None
        self._ready = False       # kendiliğinden durdu, stop() bekliyor
        # uyandırma
        self.wake_active = False
        self.wake_engine = None
        self.on_wake = None
        self.on_auto_stop = None
        self.pause_when = None
        self._wake_queue = queue.Queue(maxsize=200)
        self._wake_carry = None
        self._wake_thread = None
        self._last_wake = 0.0
        # v2.8: mikrofon açık kalırsa (VOICE_KEEP_MIC_OPEN) tuşa basmadan önceki ~0,5 sn de kayda girer
        self._preroll = collections.deque()

    # ---------------------------------------------------------- model
    def _create(self, device, compute_type):
        from faster_whisper import WhisperModel
        self.model = WhisperModel(self.model_name, device=device, compute_type=compute_type)
        self.device, self.compute_type = device, compute_type

    def _fallback_cpu(self, exc):
        print(f"[SES] Ekran kartıyla ses tanıma açılamadı ({exc}). cuDNN/cuBLAS eksik olabilir; CPU int8'e geçiliyor.")
        self._create("cpu", "int8")

    def load(self):
        if self.model is not None:
            return
        device, compute = pick_device(_cfg("WHISPER_DEVICE", "auto"), _cfg("WHISPER_COMPUTE_TYPE", ""))
        try:
            self._create(device, compute)
        except Exception as exc:
            if device != "cuda":
                raise
            self._fallback_cpu(exc)
        print(f"[SES] faster-whisper '{self.model_name}' hazır ({self.device}, {self.compute_type}).")

    def _run(self, audio):
        kw = dict(
            language="tr", task="transcribe",
            beam_size=int(_cfg("WHISPER_BEAM_SIZE", 5)),
            vad_filter=bool(_cfg("WHISPER_VAD_FILTER", True)),
            condition_on_previous_text=False,
        )
        # v2.8: konuşma sonunu kesmeyen VAD ayarı ve Türkçe bağlam ipucu (özel adlar doğru yazılsın)
        if kw["vad_filter"]:
            kw["vad_parameters"] = {"min_silence_duration_ms": int(_cfg("WHISPER_VAD_MIN_SILENCE_MS", 500)),
                                    "speech_pad_ms": int(_cfg("WHISPER_VAD_SPEECH_PAD_MS", 400))}
        prompt = str(_cfg("WHISPER_INITIAL_PROMPT", "") or "").strip()
        if prompt:
            kw["initial_prompt"] = prompt
        segments, _info = self.model.transcribe(prepare_audio(audio), **kw)
        return "".join(seg.text for seg in segments).strip()

    def transcribe(self, audio):
        self.load()
        try:
            text = self._run(audio)
        except Exception as exc:
            if self.device != "cuda":
                raise
            self._fallback_cpu(exc)       # ör. cudnn_ops_infer64_8.dll yok: ilk tanımada ortaya çıkar
            text = self._run(audio)
        text = clean_transcript(text)
        return text or None

    # ---------------------------------------------------------- mikrofon akışı
    def _ensure_stream(self):
        if self.stream is not None:
            return
        import sounddevice as sd
        self.stream = sd.InputStream(samplerate=self.sample_rate, channels=1, dtype="float32",
                                     blocksize=BLOCK_SIZE, callback=self._callback)
        self.stream.start()

    def _close_stream(self):
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            finally:
                self.stream = None

    def _callback(self, indata, frames, time_info, status):
        if status:
            print("Microphone:", status)
        if self.recording:
            self.chunks.append(indata.copy())
            rms = rms_of(indata)
            self.level = min(1.0, rms * 14.0)
            det = self._detector
            if det is not None:
                reason = det.update(rms, frames / float(self.sample_rate))
                if reason:
                    self._auto_stop(reason)
        else:
            if self._keep_open():
                self._preroll.append(indata.copy())
                keep = max(0, int(float(_cfg("VOICE_PREROLL_SECONDS", 0.5)) * self.sample_rate / max(1, frames)))
                while len(self._preroll) > keep:
                    self._preroll.popleft()
            if self.wake_active:
                try:
                    self._wake_queue.put_nowait(indata.copy())
                except queue.Full:
                    pass

    def _auto_stop(self, reason):
        with self._lock:
            if not self.recording:
                return
            self.recording = False
            self.level = 0.0
            self._detector = None
            if reason == "no_speech":      # hiç konuşulmadı: sessizce vazgeç
                self.chunks = []
                self._ready = False
                return
            self._ready = True
        cb = self.on_auto_stop
        if cb:
            threading.Thread(target=cb, args=(reason,), daemon=True).start()

    def _new_detector(self):
        return SilenceDetector(
            threshold=_cfg("VOICE_SILENCE_RMS", 0.01),
            silence_seconds=_cfg("VOICE_SILENCE_SECONDS", 1.2),
            no_speech_timeout=_cfg("VOICE_NO_SPEECH_TIMEOUT", 6.0),
            max_seconds=_cfg("VOICE_MAX_RECORD_SECONDS", 30.0),
        )

    @staticmethod
    def _keep_open():
        return bool(_cfg("VOICE_KEEP_MIC_OPEN", False))

    def open_mic(self):
        """VOICE_KEEP_MIC_OPEN ise mikrofonu baştan açar (bas-konuşta ilk hece kaçmasın)."""
        if self._keep_open():
            self._ensure_stream()

    def _begin(self, autostop, preroll=False):
        with self._lock:
            self.chunks = list(self._preroll) if preroll else []
            self._preroll.clear()
            self._ready = False
            self._detector = self._new_detector() if autostop else None
            self.recording = True
        self._ensure_stream()

    # ---------------------------------------------------------- bas-konuş
    def start(self):
        self.load()
        self._begin(bool(_cfg("VOICE_SILENCE_AUTOSTOP_PTT", False)), preroll=True)

    def stop(self):
        # v2.8: tuş bırakıldıktan sonra kısa bir süre daha kaydet (son hece kesilmesin)
        tail = float(_cfg("VOICE_PTT_TAIL_SECONDS", 0.0) or 0.0)
        if self.recording and tail > 0 and self.stream is not None:
            time.sleep(min(tail, 1.5))
        with self._lock:
            if not (self.recording or self._ready):
                return None
            self.recording = False
            self._ready = False
            self._detector = None
            self.level = 0.0
            chunks, self.chunks = self.chunks, []
        if not self.wake_active and not self._keep_open():
            self._close_stream()
        if not chunks:
            return None
        audio = np.concatenate(chunks, axis=0).flatten()
        return self.transcribe(audio)

    # ---------------------------------------------------------- uyandırma
    def trigger_wake(self):
        """Uyandırma sözcüğü duyuldu: kayıt başlar, sessizlikte kendiliğinden durur."""
        if self.recording:
            return
        self._last_wake = time.monotonic()
        if self.wake_engine is not None:
            self.wake_engine.reset()
        self._wake_carry = None
        if self.on_wake:
            try:
                self.on_wake()
            except Exception as exc:
                print("[SES] on_wake hatası:", exc)
        self._begin(True)

    def _wake_frames(self, block):
        pcm = (np.clip(np.asarray(block, dtype=np.float32).reshape(-1), -1.0, 1.0) * 32767).astype(np.int16)
        if self._wake_carry is not None and len(self._wake_carry):
            pcm = np.concatenate([self._wake_carry, pcm])
        n = len(pcm) // WAKE_FRAME
        frames = [pcm[i * WAKE_FRAME:(i + 1) * WAKE_FRAME] for i in range(n)]
        self._wake_carry = pcm[n * WAKE_FRAME:]
        return frames

    def _paused(self):
        try:
            return bool(self.pause_when and self.pause_when())
        except Exception:
            return False

    def _wake_loop(self):
        cooldown = float(_cfg("WAKE_WORD_COOLDOWN", 1.5))
        while self.wake_active:
            try:
                block = self._wake_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if self.recording or self._paused() or time.monotonic() - self._last_wake < cooldown:
                self._wake_carry = None           # JARVIS konuşurken / kayıt sürerken dinlenmez
                continue
            for frame in self._wake_frames(block):
                try:
                    hit = self.wake_engine.feed(frame)
                except Exception as exc:
                    print("[SES] Uyandırma motoru hatası:", exc)
                    hit = False
                if hit:
                    self.trigger_wake()
                    break

    def start_wake(self, on_wake=None, on_auto_stop=None, pause_when=None):
        """Mikrofonu sürekli dinlemeye başlar. Başlatılamazsa istisna fırlatır; bas-konuş yine çalışır."""
        self.on_wake, self.on_auto_stop, self.pause_when = on_wake, on_auto_stop, pause_when
        self.load()
        self.wake_engine = make_wake_detector(
            _cfg("WAKE_WORD_ENGINE", "openwakeword"),
            lambda audio: self.transcribe(audio) or "",
            threshold=_cfg("WAKE_WORD_THRESHOLD", 0.5),
            model_path=_cfg("WAKE_WORD_MODEL", "") or None,
        )
        self.wake_active = True
        self._ensure_stream()
        self._wake_thread = threading.Thread(target=self._wake_loop, daemon=True)
        self._wake_thread.start()
        print(f"[SES] \"Jarvis\" uyandırma sözcüğü dinleniyor ({self.wake_engine.name}).")

    def stop_wake(self):
        self.wake_active = False
        if not self.recording and not self._keep_open():
            self._close_stream()

    def close(self):
        self.stop_wake()
        if self.recording:
            try:
                self.stop()
            except Exception:
                pass
        try:
            self._close_stream()          # JARVIS kapanırken mikrofon her durumda bırakılır
        except Exception:
            pass
