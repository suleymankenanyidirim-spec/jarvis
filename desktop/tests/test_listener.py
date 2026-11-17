"""v2.6: voice/listener.py — faster-whisper cihaz seçimi, sessizlikte durma, "Jarvis" uyandırma.

Ses donanımı, faster-whisper ve openWakeWord gerekmez: hepsi sahte modüllerle taklit edilir.
numpy gerektiren testler numpy yoksa (CI) atlanır.
"""
import sys
import threading
import types
import unittest
from unittest import mock

from voice import listener as L

try:
    import numpy as np
except ImportError:
    np = None


class PickDeviceTest(unittest.TestCase):
    def test_auto(self):
        self.assertEqual(L.pick_device("auto", "", has_cuda=True), ("cuda", "float16"))
        self.assertEqual(L.pick_device("auto", "", has_cuda=False), ("cpu", "int8"))

    def test_explicit_and_override(self):
        self.assertEqual(L.pick_device("cpu", "", has_cuda=True), ("cpu", "int8"))
        self.assertEqual(L.pick_device("cuda", "int8_float16"), ("cuda", "int8_float16"))
        self.assertEqual(L.pick_device("saçma", "", has_cuda=False), ("cpu", "int8"))

    def test_cuda_detection_without_ctranslate2(self):
        with mock.patch.dict(sys.modules, {"ctranslate2": None}):
            self.assertFalse(L.cuda_available())
        fake = types.SimpleNamespace(get_cuda_device_count=lambda: 1)
        with mock.patch.dict(sys.modules, {"ctranslate2": fake}):
            self.assertTrue(L.cuda_available())


class SilenceDetectorTest(unittest.TestCase):
    def make(self):
        return L.SilenceDetector(threshold=0.01, silence_seconds=1.0, no_speech_timeout=3.0, max_seconds=10.0)

    def test_stops_after_speech_then_silence(self):
        d = self.make()
        for _ in range(5):
            self.assertIsNone(d.update(0.05, 0.1))
        for _ in range(9):
            self.assertIsNone(d.update(0.001, 0.1))
        self.assertEqual(d.update(0.001, 0.1), "silence")

    def test_short_pause_does_not_stop(self):
        d = self.make()
        d.update(0.05, 0.1)
        for _ in range(5):
            d.update(0.001, 0.1)
        self.assertIsNone(d.update(0.05, 0.1))      # konuşma sürdü, sayaç sıfırlandı
        self.assertEqual(d.silent_for, 0.0)

    def test_no_speech_timeout(self):
        d = self.make()
        results = [d.update(0.0, 0.5) for _ in range(6)]
        self.assertEqual(results[-1], "no_speech")
        self.assertTrue(all(r is None for r in results[:-1]))

    def test_max_length(self):
        d = self.make()
        results = [d.update(0.5, 1.0) for _ in range(10)]
        self.assertEqual(results[-1], "max_length")

    def test_rms_without_numpy(self):
        with mock.patch.object(L, "np", None):
            self.assertAlmostEqual(L.rms_of([0.3, -0.4]), (0.125) ** 0.5)
            self.assertEqual(L.rms_of([]), 0.0)


class WakeWordTextTest(unittest.TestCase):
    def test_variants(self):
        for text in ("Jarvis", "jarvis hava nasıl", "Hey Jarvis!", "Carvis, ışıkları aç", "JARVIS'cim",
                     "Cervis.", "Jarvıs"):
            self.assertTrue(L.contains_wake_word(text), text)

    def test_negatives(self):
        for text in ("", "hava nasıl", "jar", "javascript", "servis"):
            self.assertFalse(L.contains_wake_word(text), text)

    def test_custom_words(self):
        self.assertTrue(L.contains_wake_word("Cuma geldi", ["cuma"]))
        self.assertFalse(L.contains_wake_word("jarvis", ["cuma"]))


class WhisperWakeDetectorTest(unittest.TestCase):
    def frame(self, value, n=L.WAKE_FRAME):
        return np.full(n, value, dtype=np.int16) if np is not None else [value] * n

    def test_detects_after_speech_segment(self):
        heard = []

        def transcribe(audio):
            heard.append(len(audio))
            return "Jarvis"
        d = L.WhisperWakeDetector(transcribe, threshold=0.01, max_seconds=2.5, end_silence=0.16)
        self.assertFalse(d.feed(self.frame(0)))              # sessizlik: kayıt başlamaz
        self.assertFalse(d.feed(self.frame(3000)))           # konuşma
        self.assertFalse(d.feed(self.frame(0)))              # 80 ms sessizlik
        self.assertTrue(d.feed(self.frame(0)))               # 160 ms -> parça bitti, tanındı
        self.assertEqual(heard, [4 * L.WAKE_FRAME - L.WAKE_FRAME])

    def test_other_words_ignored_and_errors_swallowed(self):
        d = L.WhisperWakeDetector(lambda a: "merhaba", threshold=0.01, end_silence=0.08)
        d.feed(self.frame(3000))
        self.assertFalse(d.feed(self.frame(0)))
        d = L.WhisperWakeDetector(mock.Mock(side_effect=RuntimeError("x")), threshold=0.01, end_silence=0.08)
        d.feed(self.frame(3000))
        with mock.patch("builtins.print"):
            self.assertFalse(d.feed(self.frame(0)))


class MakeDetectorTest(unittest.TestCase):
    def test_falls_back_to_whisper_without_openwakeword(self):
        with mock.patch.dict(sys.modules, {"openwakeword": None, "openwakeword.model": None}), \
             mock.patch("builtins.print"):
            d = L.make_wake_detector("openwakeword", lambda a: "")
        self.assertEqual(d.name, "whisper")

    def test_openwakeword(self):
        created = {}

        class Model:
            def __init__(self, wakeword_models, inference_framework):
                created.update(models=wakeword_models, fw=inference_framework)

            def predict(self, frame):
                return {"hey_jarvis": 0.9}

            def reset(self):
                created["reset"] = True
        oww = types.ModuleType("openwakeword")
        oww.utils = types.SimpleNamespace(download_models=mock.Mock())
        oww_model = types.ModuleType("openwakeword.model")
        oww_model.Model = Model
        with mock.patch.dict(sys.modules, {"openwakeword": oww, "openwakeword.model": oww_model}):
            d = L.make_wake_detector("openwakeword", lambda a: "")
        self.assertEqual(d.name, "openwakeword")
        self.assertEqual(created, {"models": ["hey_jarvis"], "fw": "onnx"})
        oww.utils.download_models.assert_called_once_with(model_names=["hey_jarvis"])
        self.assertTrue(d.feed(b""))
        d.threshold = 0.95
        self.assertFalse(d.feed(b""))
        d.reset()
        self.assertTrue(created["reset"])

    def test_whisper_engine_explicit(self):
        self.assertEqual(L.make_wake_detector("whisper", lambda a: "").name, "whisper")


class FakeSegment:
    def __init__(self, text):
        self.text = text


def fake_faster_whisper(fail_on_cuda_load=False, fail_on_cuda_transcribe=False, text=" Merhaba Jarvis "):
    created = []

    class WhisperModel:
        def __init__(self, name, device, compute_type):
            if device == "cuda" and fail_on_cuda_load:
                raise RuntimeError("CUDA yok")
            self.device = device
            created.append((name, device, compute_type))

        def transcribe(self, audio, **kw):
            if self.device == "cuda" and fail_on_cuda_transcribe:
                raise RuntimeError("cudnn_ops_infer64_8.dll bulunamadı")
            self.kw = kw
            return iter([FakeSegment(text)]), None
    mod = types.ModuleType("faster_whisper")
    mod.WhisperModel = WhisperModel
    return mod, created


class ModelTest(unittest.TestCase):
    def cfg(self, **values):
        return mock.patch.object(L, "_cfg", side_effect=lambda n, d=None: values.get(n, d))

    def test_cpu_int8_when_no_gpu(self):
        mod, created = fake_faster_whisper()
        with mock.patch.dict(sys.modules, {"faster_whisper": mod}), self.cfg(), \
             mock.patch.object(L, "cuda_available", return_value=False), mock.patch("builtins.print"):
            v = L.VoiceListener()
            self.assertEqual(v.transcribe([0.0]), "Merhaba Jarvis")
        self.assertEqual(created, [("small", "cpu", "int8")])
        self.assertEqual(v.model.kw["language"], "tr")
        self.assertTrue(v.model.kw["vad_filter"])

    def test_cuda_float16(self):
        mod, created = fake_faster_whisper()
        with mock.patch.dict(sys.modules, {"faster_whisper": mod}), self.cfg(WHISPER_MODEL="medium"), \
             mock.patch.object(L, "cuda_available", return_value=True), mock.patch("builtins.print"):
            v = L.VoiceListener()
            v.load()
        self.assertEqual(created, [("medium", "cuda", "float16")])

    def test_cuda_load_failure_falls_back(self):
        mod, created = fake_faster_whisper(fail_on_cuda_load=True)
        with mock.patch.dict(sys.modules, {"faster_whisper": mod}), self.cfg(WHISPER_DEVICE="cuda"), \
             mock.patch("builtins.print"):
            v = L.VoiceListener()
            v.load()
        self.assertEqual((v.device, v.compute_type), ("cpu", "int8"))

    def test_cuda_transcribe_failure_falls_back(self):
        mod, created = fake_faster_whisper(fail_on_cuda_transcribe=True)
        with mock.patch.dict(sys.modules, {"faster_whisper": mod}), self.cfg(WHISPER_DEVICE="cuda"), \
             mock.patch("builtins.print"):
            v = L.VoiceListener()
            self.assertEqual(v.transcribe([0.0]), "Merhaba Jarvis")
        self.assertEqual([c[1] for c in created], ["cuda", "cpu"])


@unittest.skipIf(np is None, "numpy kurulu değil")
class RecordingFlowTest(unittest.TestCase):
    def setUp(self):
        self.values = {"VOICE_SILENCE_RMS": 0.01, "VOICE_SILENCE_SECONDS": 0.3,
                       "VOICE_NO_SPEECH_TIMEOUT": 1.0, "VOICE_MAX_RECORD_SECONDS": 10.0,
                       "WAKE_WORD_COOLDOWN": 0.0}
        p = mock.patch.object(L, "_cfg", side_effect=lambda n, d=None: self.values.get(n, d))
        p.start()
        self.addCleanup(p.stop)
        self.v = L.VoiceListener()
        self.v.model = object()
        self.v.transcribe = mock.Mock(return_value="ışıkları aç")
        self.v._ensure_stream = mock.Mock()
        self.v._close_stream = mock.Mock()

    def block(self, value):
        return np.full((L.BLOCK_SIZE, 1), value, dtype=np.float32)

    def feed(self, value, n=1):
        for _ in range(n):
            self.v._callback(self.block(value), L.BLOCK_SIZE, None, None)

    def test_push_to_talk_unchanged(self):
        self.v.start()
        self.feed(0.2, 3)
        self.feed(0.0, 10)                      # bas-konuşta varsayılan: sessizlikte durmaz
        self.assertTrue(self.v.recording)
        self.assertEqual(self.v.stop(), "ışıkları aç")
        self.assertEqual(len(self.v.transcribe.call_args.args[0]), 13 * L.BLOCK_SIZE)
        self.v._close_stream.assert_called_once()
        self.assertIsNone(self.v.stop())

    def test_push_to_talk_autostop_setting(self):
        self.values["VOICE_SILENCE_AUTOSTOP_PTT"] = True
        done = threading.Event()
        self.v.on_auto_stop = lambda reason: done.set()
        self.v.start()
        self.feed(0.2, 2)
        self.feed(0.0, 3)
        self.assertTrue(done.wait(2))
        self.assertFalse(self.v.recording)
        self.assertEqual(self.v.stop(), "ışıkları aç")

    def test_wake_then_silence_autostop(self):
        reasons = []
        done = threading.Event()
        self.v.on_wake = mock.Mock()
        self.v.on_auto_stop = lambda r: (reasons.append(r), done.set())
        self.v.wake_engine = mock.Mock()
        self.v.wake_active = True
        self.v.trigger_wake()
        self.v.on_wake.assert_called_once()
        self.v.wake_engine.reset.assert_called()
        self.assertTrue(self.v.recording)
        self.feed(0.2, 4)
        self.feed(0.0, 3)
        self.assertTrue(done.wait(2))
        self.assertEqual(reasons, ["silence"])
        self.assertEqual(self.v.stop(), "ışıkları aç")
        self.v._close_stream.assert_not_called()         # uyandırma açıkken mikrofon açık kalır

    def test_wake_without_speech_gives_up(self):
        self.v.on_auto_stop = mock.Mock()
        self.v.wake_engine = mock.Mock()
        self.v.wake_active = True
        self.v.trigger_wake()
        self.feed(0.0, 10)
        self.assertFalse(self.v.recording)
        self.v.on_auto_stop.assert_not_called()
        self.assertIsNone(self.v.stop())

    def test_idle_audio_goes_to_wake_queue(self):
        self.v.wake_active = True
        self.feed(0.1, 2)
        self.assertEqual(self.v._wake_queue.qsize(), 2)
        self.v.wake_active = False
        self.feed(0.1)
        self.assertEqual(self.v._wake_queue.qsize(), 2)

    def test_wake_frames_split_and_carry(self):
        frames = self.v._wake_frames(self.block(0.5))       # 1600 örnek -> 1 kare + 320 artık
        self.assertEqual([len(f) for f in frames], [L.WAKE_FRAME])
        self.assertEqual(frames[0].dtype, np.int16)
        frames = self.v._wake_frames(self.block(0.5))       # 320 + 1600 = 1920 -> 1 kare + 640
        self.assertEqual(len(frames), 1)
        self.assertEqual(len(self.v._wake_carry), 640)

    def test_wake_loop_triggers_and_respects_pause(self):
        self.v.wake_engine = mock.Mock()
        self.v.wake_engine.feed.return_value = True
        self.v.on_wake = mock.Mock(side_effect=lambda: setattr(self.v, "wake_active", False))
        self.v.wake_active = True
        self.v.pause_when = lambda: True
        self.v._wake_queue.put(self.block(0.5))
        t = threading.Thread(target=self.v._wake_loop)
        t.start()
        t.join(0.8)
        self.v.wake_engine.feed.assert_not_called()          # JARVIS konuşurken dinlemez
        self.v.pause_when = lambda: False
        self.v._wake_queue.put(self.block(0.5))
        t.join(3)
        self.assertFalse(t.is_alive())
        self.v.on_wake.assert_called_once()
        self.assertTrue(self.v.recording)

    def test_start_wake_builds_engine(self):
        engine = mock.Mock(name_="x")
        engine.name = "whisper"
        with mock.patch.object(L, "make_wake_detector", return_value=engine) as make, \
             mock.patch.object(self.v, "load"), mock.patch("builtins.print"), \
             mock.patch.object(L.threading, "Thread") as thread:
            self.v.start_wake(on_wake=1, on_auto_stop=2, pause_when=3)
        make.assert_called_once()
        thread.return_value.start.assert_called_once()
        self.assertTrue(self.v.wake_active)
        self.assertEqual((self.v.on_wake, self.v.on_auto_stop, self.v.pause_when), (1, 2, 3))
        self.v.stop_wake()
        self.assertFalse(self.v.wake_active)
        self.v._close_stream.assert_called_once()


if __name__ == "__main__":
    unittest.main()
