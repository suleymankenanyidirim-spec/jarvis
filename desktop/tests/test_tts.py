"""v2.2: ses motoru katmanı — ağ istekleri taklit edilir (gerçek API'ye gidilmez)."""
import base64
import io
import os
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

from voice import tts

PCM = b"\x01\x00\x02\x00" * 100   # 200 örnek, 16 bit mono


class FakeResponse:
    def __init__(self, status=200, json_data=None, content=b"", text=""):
        self.status_code = status
        self._json = json_data
        self.content = content
        self.text = text or str(json_data)

    def json(self):
        return self._json


def gemini_ok(audio=PCM, mime="audio/L16;codec=pcm;rate=24000"):
    return FakeResponse(json_data={"candidates": [{"content": {"parts": [{"inlineData": {
        "mimeType": mime, "data": base64.b64encode(audio).decode()}}]}}]})


class Cfg:
    """config değerlerini test süresince değiştirir."""
    def __init__(self, **values):
        self.values = values

    def __enter__(self):
        base = dict(tts.DEFAULTS)
        base["TTS_CACHE_ENABLED"] = False
        base.update(self.values)
        self.p = mock.patch.object(tts, "cfg", side_effect=lambda n: base.get(n))
        self.p.start()
        return self

    def __exit__(self, *exc):
        self.p.stop()


def env(**values):
    return mock.patch.dict(os.environ, values, clear=False)


def no_env(*names):
    saved = {n: os.environ.pop(n) for n in names if n in os.environ}
    class _Ctx:
        def __enter__(self): return self
        def __exit__(self, *exc): os.environ.update(saved)
    return _Ctx()


class GeminiTest(unittest.TestCase):
    def setUp(self):
        self.http = mock.Mock()
        self.engine = tts.GeminiEngine(session=self.http)

    def test_request_format_and_pcm_wrapped_as_wav(self):
        self.http.post.return_value = gemini_ok()
        with Cfg(), env(GEMINI_API_KEY="k-123"):
            audio, fmt = self.engine.synthesize("Merhaba efendim.")
        url = self.http.post.call_args[0][0]
        kw = self.http.post.call_args[1]
        self.assertEqual(url, "https://generativelanguage.googleapis.com/v1beta/models/"
                              "gemini-3.1-flash-tts-preview:generateContent")
        self.assertEqual(kw["headers"]["x-goog-api-key"], "k-123")
        gc = kw["json"]["generationConfig"]
        self.assertEqual(gc["responseModalities"], ["AUDIO"])
        self.assertEqual(gc["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"], "Charon")
        text = kw["json"]["contents"][0]["parts"][0]["text"]
        self.assertTrue(text.startswith("Sakin, kendinden emin"))
        self.assertTrue(text.endswith("Merhaba efendim."))
        self.assertEqual(fmt, "wav")
        with wave.open(io.BytesIO(audio)) as w:
            self.assertEqual((w.getnchannels(), w.getsampwidth(), w.getframerate()), (1, 2, 24000))
            self.assertEqual(w.readframes(1000), PCM)

    def test_sample_rate_from_mime(self):
        self.http.post.return_value = gemini_ok(mime="audio/L16;codec=pcm;rate=16000")
        with Cfg(), env(GEMINI_API_KEY="k"):
            audio, _ = self.engine.synthesize("a")
        with wave.open(io.BytesIO(audio)) as w:
            self.assertEqual(w.getframerate(), 16000)

    def test_wav_response_passed_through(self):
        wav = tts.pcm_to_wav(PCM)
        self.http.post.return_value = gemini_ok(audio=wav, mime="audio/wav")
        with Cfg(), env(GEMINI_API_KEY="k"):
            audio, fmt = self.engine.synthesize("a")
        self.assertEqual(audio, wav)

    def test_newer_model_uses_speech_metadata(self):
        self.http.post.return_value = gemini_ok()
        with Cfg(GEMINI_TTS_MODEL="gemini-3.8-flash-tts", GEMINI_TTS_VOICE="Orus"), env(GEMINI_API_KEY="k"):
            self.engine.synthesize("Merhaba.")
        part = self.http.post.call_args[1]["json"]["contents"][0]["parts"][0]
        self.assertEqual(part["text"], "Merhaba.")
        self.assertIn("uşak", part["speech_metadata"]["style"])

    def test_voice_override(self):
        self.http.post.return_value = gemini_ok()
        with Cfg(), env(GEMINI_API_KEY="k"):
            self.engine.synthesize("a", voice="Iapetus")
        body = self.http.post.call_args[1]["json"]
        self.assertEqual(body["generationConfig"]["speechConfig"]["voiceConfig"]
                         ["prebuiltVoiceConfig"]["voiceName"], "Iapetus")

    def test_missing_key(self):
        with Cfg(), no_env("GEMINI_API_KEY"):
            with self.assertRaises(tts.TTSUnavailable):
                self.engine.synthesize("a")
        self.http.post.assert_not_called()

    def test_http_error_and_textual_reply(self):
        self.http.post.return_value = FakeResponse(status=429, text="quota")
        with Cfg(), env(GEMINI_API_KEY="k"):
            with self.assertRaisesRegex(RuntimeError, "429"):
                self.engine.synthesize("a")
        self.http.post.return_value = FakeResponse(json_data={"candidates": [{"content": {"parts": [{"text": "hi"}]}}]})
        with Cfg(), env(GEMINI_API_KEY="k"):
            with self.assertRaisesRegex(RuntimeError, "ses döndürmedi"):
                self.engine.synthesize("a")


class ElevenLabsTest(unittest.TestCase):
    def setUp(self):
        self.http = mock.Mock()
        self.engine = tts.ElevenLabsEngine(session=self.http)

    def test_request_format(self):
        self.http.post.return_value = FakeResponse(content=b"ID3mp3")
        with Cfg(ELEVENLABS_VOICE_ID="v42"), env(ELEVENLABS_API_KEY="e-1"):
            audio, fmt = self.engine.synthesize("Merhaba")
        args, kw = self.http.post.call_args
        self.assertEqual(args[0], "https://api.elevenlabs.io/v1/text-to-speech/v42")
        self.assertEqual(kw["headers"]["xi-api-key"], "e-1")
        self.assertEqual(kw["json"], {"text": "Merhaba", "model_id": "eleven_multilingual_v2"})
        self.assertEqual(kw["params"], {"output_format": "mp3_44100_128"})
        self.assertEqual((audio, fmt), (b"ID3mp3", "mp3"))

    def test_missing_voice_or_key(self):
        with Cfg(ELEVENLABS_VOICE_ID=""), env(ELEVENLABS_API_KEY="e"):
            with self.assertRaises(tts.TTSUnavailable):
                self.engine.synthesize("a")
        with Cfg(ELEVENLABS_VOICE_ID="v"), no_env("ELEVENLABS_API_KEY"):
            with self.assertRaises(tts.TTSUnavailable):
                self.engine.synthesize("a")
        self.http.post.assert_not_called()

    def test_credits(self):
        self.http.get.return_value = FakeResponse(json_data={
            "character_count": 1200, "character_limit": 10000, "next_character_count_reset_unix": 99})
        with env(ELEVENLABS_API_KEY="e"):
            info = self.engine.credits()
        self.assertEqual(self.http.get.call_args[0][0], "https://api.elevenlabs.io/v1/user/subscription")
        self.assertEqual(self.http.get.call_args[1]["headers"], {"xi-api-key": "e"})
        self.assertEqual(info, {"used": 1200, "limit": 10000, "remaining": 8800, "reset_unix": 99})


class FakeEngine:
    def __init__(self, name, result=None, error=None):
        self.name, self.result, self.error, self.calls = name, result, error, []

    def signature(self):
        return "sig"

    def synthesize(self, text, voice=None):
        self.calls.append(text)
        if self.error:
            raise self.error
        return self.result


class FallbackTest(unittest.TestCase):
    def engines(self, **specs):
        fakes = {n: FakeEngine(n, result=(n.encode(), "wav")) for n in tts.ENGINES}
        for n, err in specs.items():
            fakes[n].error = err
        return fakes

    def run_chain(self, fakes, **cfgv):
        with mock.patch.dict(tts.ENGINES, fakes), Cfg(**cfgv), mock.patch("builtins.print") as out:
            result = tts.synthesize("Merhaba.")
        return result, [" ".join(map(str, c[0])) for c in out.call_args_list]

    def test_chain_order(self):
        with Cfg(TTS_ENGINE="elevenlabs"):
            self.assertEqual(tts.engine_chain(), ["elevenlabs", "gemini", "edge", "pyttsx3"])
        with Cfg(TTS_ENGINE="edge"):
            self.assertEqual(tts.engine_chain(), ["edge", "gemini", "pyttsx3"])
        with Cfg(TTS_ENGINE="gemini"):
            self.assertEqual(tts.engine_chain(), ["gemini", "edge", "pyttsx3"])
        with Cfg(TTS_ENGINE="çöp"), mock.patch("builtins.print"):
            self.assertEqual(tts.engine_chain()[0], "gemini")

    def test_gemini_ok(self):
        (audio, fmt, name), log = self.run_chain(self.engines())
        self.assertEqual(name, "gemini")
        self.assertEqual(log, [])

    def test_gemini_no_key_falls_to_edge(self):
        (_, _, name), log = self.run_chain(self.engines(gemini=tts.TTSUnavailable("anahtar yok")))
        self.assertEqual(name, "edge")
        self.assertTrue(any("gemini" in l and "edge" in l for l in log), log)

    def test_elevenlabs_error_then_gemini(self):
        fakes = self.engines(elevenlabs=RuntimeError("HTTP 401"))
        (_, _, name), log = self.run_chain(fakes, TTS_ENGINE="elevenlabs")
        self.assertEqual(name, "gemini")
        self.assertIn("elevenlabs", log[0])
        self.assertIn("gemini", log[0])

    def test_all_down_to_pyttsx3(self):
        fakes = self.engines(gemini=RuntimeError("x"), edge=RuntimeError("y"))
        (_, _, name), log = self.run_chain(fakes)
        self.assertEqual(name, "pyttsx3")
        self.assertEqual(len(log), 2)
        self.assertIn("pyttsx3", log[1])

    def test_everything_fails(self):
        fakes = self.engines(gemini=RuntimeError("x"), edge=RuntimeError("y"), pyttsx3=RuntimeError("z"))
        with self.assertRaises(RuntimeError):
            self.run_chain(fakes)


class CacheTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = mock.patch.object(tts, "CACHE_DIR", Path(self.tmp.name) / "tts_cache")
        self.p.start()

    def tearDown(self):
        self.p.stop()
        self.tmp.cleanup()

    def test_short_phrase_cached(self):
        fake = FakeEngine("gemini", result=(b"RIFFaudio", "wav"))
        with mock.patch.dict(tts.ENGINES, {"gemini": fake}), Cfg(TTS_CACHE_ENABLED=True):
            first = tts.synthesize("Efendim.")
            second = tts.synthesize("Efendim.")
        self.assertEqual(first, second)
        self.assertEqual(fake.calls, ["Efendim."])
        self.assertEqual(len(list(tts.CACHE_DIR.glob("gemini_*.wav"))), 1)

    def test_long_text_not_cached(self):
        fake = FakeEngine("gemini", result=(b"a", "wav"))
        long_text = "Bu cümle önbellek sınırından çok daha uzun olduğu için saklanmamalı efendim."
        with mock.patch.dict(tts.ENGINES, {"gemini": fake}), Cfg(TTS_CACHE_ENABLED=True):
            tts.synthesize(long_text)
            tts.synthesize(long_text)
        self.assertEqual(len(fake.calls), 2)

    def test_listed_phrase_cached_even_if_long(self):
        phrase = "Bu uzun ama listede olan, sık kullanılan bir cevap cümlesidir efendim."
        fake = FakeEngine("gemini", result=(b"a", "wav"))
        with mock.patch.dict(tts.ENGINES, {"gemini": fake}), \
             Cfg(TTS_CACHE_ENABLED=True, TTS_CACHE_PHRASES=[phrase]):
            tts.synthesize(phrase)
            tts.synthesize(phrase)
        self.assertEqual(len(fake.calls), 1)

    def test_voice_change_invalidates(self):
        engine = tts.GeminiEngine(session=mock.Mock())
        with Cfg(GEMINI_TTS_VOICE="Charon"):
            a = tts.cache_path(engine, "Efendim.", "wav")
        with Cfg(GEMINI_TTS_VOICE="Orus"):
            b = tts.cache_path(engine, "Efendim.", "wav")
        self.assertNotEqual(a, b)

    def test_cache_pruned(self):
        fake = FakeEngine("gemini", result=(b"a", "wav"))
        with mock.patch.dict(tts.ENGINES, {"gemini": fake}), Cfg(TTS_CACHE_ENABLED=True, TTS_CACHE_MAX_FILES=3):
            for i in range(6):
                tts.synthesize(f"Kısa {i}.")
        self.assertEqual(len(list(tts.CACHE_DIR.glob("*.wav"))), 3)


class SplitTest(unittest.TestCase):
    def test_split(self):
        self.assertEqual(tts.split_sentences("Efendim. Hemen bakıyorum! Hazır mı?"),
                         ["Efendim.", "Hemen bakıyorum!", "Hazır mı?"])

    def test_numbers_and_abbreviations(self):
        self.assertEqual(tts.split_sentences("Sürüm 3.5 hazır. Dr. Ahmet aradı."),
                         ["Sürüm 3.5 hazır.", "Dr. Ahmet aradı."])

    def test_newlines_and_empty(self):
        self.assertEqual(tts.split_sentences("Birinci satır\nİkinci satır"), ["Birinci satır", "İkinci satır"])
        self.assertEqual(tts.split_sentences("   "), [])


class StatusTest(unittest.TestCase):
    def test_status_hides_keys_and_shows_credits(self):
        tts._credit_cache.update(t=0.0, value=None)
        fake = mock.Mock()
        fake.credits.return_value = {"used": 1, "limit": 10, "remaining": 9, "reset_unix": None}
        with mock.patch.dict(tts.ENGINES, {"elevenlabs": fake}), Cfg(), \
             env(GEMINI_API_KEY="gizli-g", ELEVENLABS_API_KEY="gizli-e"):
            info = tts.status()
            tts.status()   # ikinci çağrı önbellekten
        self.assertEqual(info["engine"], "gemini")
        self.assertTrue(info["gemini_key"])
        self.assertEqual(info["elevenlabs_credits"]["remaining"], 9)
        self.assertEqual(fake.credits.call_count, 1)
        self.assertNotIn("gizli", str(info))


class SpeakerPipelineTest(unittest.TestCase):
    def test_sentences_synthesized_in_order_and_played(self):
        try:
            from voice import speaker
        except ImportError as exc:
            self.skipTest(str(exc))
        spk = speaker.VoiceSpeaker.__new__(speaker.VoiceSpeaker)
        import threading
        spk.lock, spk.speaking = threading.Lock(), False
        played = []
        spk._play = lambda audio, fmt: played.append(audio)
        spk._fallback = lambda text: played.append(b"FALLBACK:" + text.encode())

        def fake_synth(text):
            if "Hata" in text:
                raise RuntimeError("yok")
            return text.encode(), "wav", "gemini"

        with mock.patch.object(tts, "synthesize", side_effect=fake_synth), Cfg():
            spk._worker("Birinci. Hata burada. Üçüncü.")
        self.assertEqual(played, [b"Birinci.", b"FALLBACK:Hata burada.", "Üçüncü.".encode()])
        self.assertFalse(spk.speaking)

    def test_edge_profiles_kept(self):
        try:
            from voice import speaker
        except ImportError as exc:
            self.skipTest(str(exc))
        self.assertEqual(set(speaker.VOICE_PROFILES), {"V1", "V2", "V3"})
        speaker.set_profile("V1")
        self.assertEqual(tts.EdgeEngine().signature(), "tr-TR-AhmetNeural|+8%|+0Hz")
        speaker.set_profile("V3")


try:
    import flask  # noqa: F401
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False


@unittest.skipUnless(HAS_FLASK, "flask kurulu değil")
class PhoneTtsTest(unittest.TestCase):
    def setUp(self):
        from core import phone_server as ps
        brain = mock.Mock()
        brain.council.available.return_value = {}
        brain.task_status.return_value = []
        with mock.patch.object(ps, "load_token", return_value="tok"):
            self.server = ps.PhoneServer(brain)
        self.client = self.server.app.test_client()

    def test_tts_uses_engine_layer(self):
        with mock.patch.object(tts, "synthesize", return_value=(b"RIFFwav", "wav", "gemini")) as syn:
            resp = self.client.get("/api/tts?text=Merhaba", headers={"X-Jarvis-Token": "tok"})
        syn.assert_called_once_with("Merhaba")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.mimetype, "audio/wav")
        self.assertEqual(resp.data, b"RIFFwav")

    def test_tts_mp3_and_failure(self):
        with mock.patch.object(tts, "synthesize", return_value=(b"ID3", "mp3", "edge")):
            resp = self.client.get("/api/tts?text=a", headers={"X-Jarvis-Token": "tok"})
        self.assertEqual(resp.mimetype, "audio/mpeg")
        with mock.patch.object(tts, "synthesize", side_effect=RuntimeError("yok")), mock.patch("builtins.print"):
            resp = self.client.get("/api/tts?text=a", headers={"X-Jarvis-Token": "tok"})
        self.assertEqual(resp.status_code, 204)

    def test_status_includes_tts(self):
        with mock.patch.object(tts, "status", return_value={"engine": "elevenlabs",
                                                             "elevenlabs_credits": {"remaining": 5}}), \
             mock.patch("requests.get", side_effect=OSError):
            data = self.client.get("/api/status", headers={"X-Jarvis-Token": "tok"}).get_json()
        self.assertEqual(data["tts"]["elevenlabs_credits"]["remaining"], 5)


if __name__ == "__main__":
    unittest.main()
