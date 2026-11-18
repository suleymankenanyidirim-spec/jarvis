"""v2.8: ses tanıma kalitesi — Türkçe ipucu, VAD, ses yükseltme, sahte altyazıların atılması,
tuştan önce/sonra kayıt, mikrofonu açık tutma. Donanım ve faster-whisper gerekmez."""
import sys
import time
import unittest
from unittest import mock

from voice import listener as L
from tests.test_listener import fake_faster_whisper

try:
    import numpy as np
except ImportError:
    np = None


class CleanTranscriptTest(unittest.TestCase):
    def test_hallucinations_dropped(self):
        for t in ("Altyazı M.K.", "İzlediğiniz için teşekkürler.", "  altyazı  ", "Abone olmayı unutmayın!"):
            self.assertEqual(L.clean_transcript(t), "", t)

    def test_real_speech_kept(self):
        for t in ("Teşekkürler.", "Işıkları aç.", "Altyazıları kapat ve filmi başlat"):
            self.assertEqual(L.clean_transcript(t), t, t)
        self.assertEqual(L.clean_transcript("  merhaba   jarvis "), "merhaba jarvis")


@unittest.skipIf(np is None, "numpy kurulu değil")
class PrepareAudioTest(unittest.TestCase):
    def test_quiet_audio_boosted_with_limit(self):
        quiet = np.full(100, 0.05, dtype=np.float32)
        self.assertAlmostEqual(float(np.max(L.prepare_audio(quiet))), 0.4, places=4)   # 8x üst sınır
        mid = np.full(100, 0.3, dtype=np.float32)
        self.assertAlmostEqual(float(np.max(L.prepare_audio(mid))), 0.9, places=4)
        loud = np.full(100, 0.8, dtype=np.float32)
        self.assertIs(L.prepare_audio(loud), loud)
        silent = np.zeros(10, dtype=np.float32)
        self.assertEqual(float(np.max(L.prepare_audio(silent))), 0.0)
        self.assertEqual(L.prepare_audio([0.1]), [0.1])


class TranscribeParamsTest(unittest.TestCase):
    def test_prompt_vad_and_cleanup(self):
        mod, _ = fake_faster_whisper(text=" Altyazı M.K. ")
        values = {"WHISPER_INITIAL_PROMPT": "JARVIS, kask", "WHISPER_VAD_MIN_SILENCE_MS": 700}
        with mock.patch.dict(sys.modules, {"faster_whisper": mod}), \
             mock.patch.object(L, "_cfg", side_effect=lambda n, d=None: values.get(n, d)), \
             mock.patch.object(L, "cuda_available", return_value=False), mock.patch("builtins.print"):
            v = L.VoiceListener()
            self.assertIsNone(v.transcribe([0.0]))            # sahte altyazı atıldı
        self.assertEqual(v.model.kw["initial_prompt"], "JARVIS, kask")
        self.assertEqual(v.model.kw["vad_parameters"]["min_silence_duration_ms"], 700)
        self.assertEqual(v.model.kw["vad_parameters"]["speech_pad_ms"], 400)


@unittest.skipIf(np is None, "numpy kurulu değil")
class PrerollTailTest(unittest.TestCase):
    def setUp(self):
        self.values = {"VOICE_KEEP_MIC_OPEN": True, "VOICE_PREROLL_SECONDS": 0.3, "VOICE_PTT_TAIL_SECONDS": 0.2}
        p = mock.patch.object(L, "_cfg", side_effect=lambda n, d=None: self.values.get(n, d))
        p.start()
        self.addCleanup(p.stop)
        self.v = L.VoiceListener()
        self.v.model = object()
        self.v.transcribe = mock.Mock(return_value="tamam")
        self.v._ensure_stream = mock.Mock()
        self.v._close_stream = mock.Mock()
        self.v.stream = object()

    def feed(self, value, n=1):
        for _ in range(n):
            self.v._callback(np.full((L.BLOCK_SIZE, 1), value, dtype=np.float32), L.BLOCK_SIZE, None, None)

    def test_preroll_kept_and_mic_stays_open(self):
        self.feed(0.1, 10)                          # tuşa basmadan önce: yalnızca son 0,3 sn tutulur
        self.assertEqual(len(self.v._preroll), 3)
        self.v.start()
        self.feed(0.2, 2)
        self.assertEqual(self.v.stop(), "tamam")
        self.assertEqual(len(self.v.transcribe.call_args.args[0]), 5 * L.BLOCK_SIZE)
        self.v._close_stream.assert_not_called()     # mikrofon açık kalır
        self.assertEqual(len(self.v._preroll), 0)

    def test_tail_waits_after_release(self):
        self.v.start()
        self.feed(0.2)
        t = time.monotonic()
        self.v.stop()
        self.assertGreaterEqual(time.monotonic() - t, 0.19)

    def test_close_releases_mic(self):
        self.v.close()
        self.v._close_stream.assert_called()

    def test_open_mic(self):
        self.v.open_mic()
        self.v._ensure_stream.assert_called_once()
        self.values["VOICE_KEEP_MIC_OPEN"] = False
        self.v.open_mic()
        self.v._ensure_stream.assert_called_once()


if __name__ == "__main__":
    unittest.main()
