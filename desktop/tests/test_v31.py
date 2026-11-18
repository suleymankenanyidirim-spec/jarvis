"""v3.1: Piper ses motoru (sahte piper modülüyle) ve hava kartının doğru şehri göstermesi."""
import sys
import tempfile
import types
import unittest
import wave
from pathlib import Path
from unittest import mock

from tests.test_brain import BrainTestBase, FakeResponse   # noqa: F401
from core import visuals as V
from core import weather
from voice import tts


def fake_piper(calls):
    mod = types.ModuleType("piper")

    class PiperVoice:
        @staticmethod
        def load(path):
            calls.append(("load", path))
            return PiperVoice()

        def synthesize_wav(self, text, wf, syn_config=None):
            calls.append(("say", text, getattr(syn_config, "length_scale", None)))
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(22050)
            wf.writeframes(b"\x01\x00" * 2205)

    class SynthesisConfig:
        def __init__(self, length_scale=None):
            self.length_scale = length_scale

    mod.PiperVoice, mod.SynthesisConfig = PiperVoice, SynthesisConfig
    return mod


class PiperEngineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        d = Path(self.tmp.name)
        (d / "tr_TR-test.onnx").write_bytes(b"x")
        (d / "tr_TR-test.onnx.json").write_text("{}")
        self.values = {"TTS_ENGINE": "piper", "PIPER_VOICE": "tr_TR-test", "PIPER_DIR": str(d), "TTS_CACHE_ENABLED": False}
        p = mock.patch.object(tts, "cfg", side_effect=lambda n: self.values.get(n, tts.DEFAULTS.get(n)))
        p.start()
        self.addCleanup(p.stop)
        tts.PiperEngine._voices.clear()
        self.addCleanup(tts.PiperEngine._voices.clear)

    def test_chain_starts_with_piper_and_keeps_fallbacks(self):
        self.assertEqual(tts.engine_chain(), ["piper", "gemini", "edge", "pyttsx3"])

    def test_synthesize_wav_and_voice_cached(self):
        calls = []
        with mock.patch.dict(sys.modules, {"piper": fake_piper(calls)}):
            audio, fmt, engine = tts.synthesize("Merhaba efendim.")
            tts.synthesize("İkinci cümle.")
        self.assertEqual((fmt, engine), ("wav", "piper"))
        self.assertEqual(audio[:4], b"RIFF")
        with wave.open(__import__("io").BytesIO(audio)) as w:
            self.assertEqual(w.getframerate(), 22050)
        self.assertEqual([c[0] for c in calls], ["load", "say", "say"])      # ses bir kez yüklenir

    def test_speed_setting(self):
        calls = []
        self.values["PIPER_SPEED"] = 0.9
        with mock.patch.dict(sys.modules, {"piper": fake_piper(calls)}):
            tts.synthesize("Hızlı.")
        self.assertEqual(calls[-1][2], 0.9)

    def test_missing_voice_falls_back(self):
        self.values.update(PIPER_VOICE="yok-boyle-bir-ses", TTS_ENGINE="piper")
        with mock.patch.dict(sys.modules, {"piper": fake_piper([])}), \
             mock.patch.object(tts.ENGINES["gemini"], "synthesize", side_effect=tts.TTSUnavailable("anahtar yok")), \
             mock.patch.object(tts.ENGINES["edge"], "synthesize", return_value=(b"ID3", "mp3")), \
             mock.patch("builtins.print"):
            _audio, fmt, engine = tts.synthesize("Merhaba.")
        self.assertEqual((engine, fmt), ("edge", "mp3"))

    def test_not_installed_falls_back(self):
        with mock.patch.dict(sys.modules, {"piper": None}), \
             mock.patch.object(tts.ENGINES["gemini"], "synthesize", side_effect=tts.TTSUnavailable("anahtar yok")), \
             mock.patch.object(tts.ENGINES["edge"], "synthesize", return_value=(b"ID3", "mp3")), \
             mock.patch("builtins.print"):
            self.assertEqual(tts.synthesize("Merhaba.")[2], "edge")

    def test_default_chain_unchanged(self):
        self.values["TTS_ENGINE"] = "gemini"
        self.assertEqual(tts.engine_chain(), ["gemini", "edge", "pyttsx3"])


class PlaceTest(unittest.TestCase):
    def test_turkish_city_spellings(self):
        cases = {"izmirde hava nasıl": "İzmir", "İzmir'in hava durumu": "İzmir", "izmir hava durumu": "İzmir",
                 "ankaranın havası": "Ankara", "istanbulda yağmur": "İstanbul", "karsta kar yağıyor mu": "Kars",
                 "Paris'te hava nasıl": "Paris", "hava nasıl": "", "vanilya kokulu hava": "",
                 "karşıyakada hava": ""}
        for text, want in cases.items():
            self.assertEqual(V.extract_location(text), want, text)


class LatestWeatherCardTest(BrainTestBase):
    def test_tool_weather_replaces_first_card(self):
        """Ekran görüntüsündeki hata: kart İstanbul, cevap İzmir. Artık son veri (İzmir) kartta."""
        self.brain.weather_enabled = True
        ist = {"place": "İstanbul", "day": "bugün", "temp": 17, "icon": "cloud", "desc": "Kapalı"}
        izm = {"type": "weather", "side": "center", "data": {"place": "İzmir", "temp": 20}}
        self.brain.tools.last_visual = izm
        self.brain.tools.execute.return_value = "İzmir (bugün): Kapalı, şu an 20°C."
        call = [{"function": {"name": "get_weather", "arguments": '{"place": "İzmir"}'}}]
        with mock.patch.object(weather, "get_weather", return_value=ist), \
             self.patch_post([FakeResponse({"content": "", "tool_calls": call}),
                              FakeResponse({"content": "İzmir'de 20 derece."})]):
            _a, cards = self.brain.ask_with_visuals("hava durumu nasıl")
        self.assertEqual([c["data"]["place"] for c in cards], ["İzmir"])

    def test_weather_has_update_time(self):
        weather._CACHE.clear()

        class R:
            def __init__(self, d):
                self.d = d

            def raise_for_status(self):
                pass

            def json(self):
                return self.d

        http = mock.Mock()
        http.get.return_value = R({"current": {"temperature_2m": 1, "weather_code": 0}, "daily": {}})
        w = weather.get_weather("", http=http)
        self.assertRegex(w["updated"], r"^\d\d:\d\d$")


if __name__ == "__main__":
    unittest.main()
