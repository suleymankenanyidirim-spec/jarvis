"""v3.0: görsel kartlar — kısayol bulma, hava durumu, ev, kask, bilgi penceresi, kart yaşam döngüsü.
Ağ ve donanım gerekmez (Open-Meteo / Vikipedi sahte cevaplarla)."""
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.test_brain import BrainTestBase, FakeResponse   # noqa: F401 (sahte requests kurar)
from core import visuals as V
from core import weather, wiki
from core.home import Home


class ComboTest(unittest.TestCase):
    def test_detects_turkish_phrasings(self):
        cases = {
            "Önce Ctrl ve C ile kopyala, sonra Ctrl+V ile yapıştır.": ["CTRL + C", "CTRL + V"],
            "Görev yöneticisi: CTRL + SHIFT + ESC.": ["CTRL + SHIFT + ESC"],
            "Pencereyi kapatmak için Alt F4.": ["ALT + F4"],
            "Windows tuşu ve E dosya gezginini açar.": ["WIN + E"],
            "Kontrol tuşu ve Z geri alır.": ["CTRL + Z"],
            "Alternatif olarak menüyü kullan.": [],
            "Ctrl tuşuna basılı tut.": [],
        }
        for text, want in cases.items():
            self.assertEqual([c["label"] for c in V.detect_key_combos(text)], want, text)

    def test_active_combo_follows_speech(self):
        text = "Ctrl ve C ile kopyala. Sonra hedefe git. Ctrl ve V ile yapıştır."
        combos = V.detect_key_combos(text)
        self.assertIsNone(V.active_combo(combos, -10))
        self.assertEqual(V.active_combo(combos, 3)["label"], "CTRL + C")
        self.assertEqual(V.active_combo(combos, text.index("Sonra"))["label"], "CTRL + C")
        self.assertEqual(V.active_combo(combos, text.index("Ctrl ve V") + 1)["label"], "CTRL + V")
        self.assertIsNone(V.keyboard_visual("kısayol yok"))
        self.assertEqual(V.keyboard_visual(text)["type"], "keyboard")


class DetectTest(unittest.TestCase):
    def test_weather_question_and_place(self):
        self.assertTrue(V.is_weather_question("Ankara'da hava nasıl?"))
        self.assertTrue(V.is_weather_question("yarın yağmur yağacak mı"))
        self.assertFalse(V.is_weather_question("havalı bir isim öner"))
        for text, place in (("Ankara'da hava nasıl?", "Ankara"), ("Yarın İzmir'de yağmur yağacak mı", "İzmir"),
                            ("Bursa hava durumu", "Bursa"), ("Bugün hava nasıl", ""), ("Hava durumu Jarvis", "")):
            self.assertEqual(V.extract_location(text), place, text)

    def test_info_question_and_topic(self):
        self.assertTrue(V.is_info_question("Fatih Sultan Mehmet kimdir?"))
        self.assertFalse(V.is_info_question("selam nasılsın"))
        self.assertEqual(V.info_topic("Ayasofya'nın tarihi"), "Ayasofya")
        self.assertEqual(V.info_topic("Bana İstanbul'un fethi hakkında bilgi ver"), "İstanbul fethi")

    def test_helmet_state(self):
        self.assertEqual(V.helmet_state("WiFi: Açılıyor"), "open")
        self.assertEqual(V.helmet_state("Kask kapalı"), "closed")
        self.assertEqual(V.helmet_state("yapılandırılmamış"), "unknown")

    def test_image_to_strokes(self):
        from PIL import Image, ImageDraw
        im = Image.new("RGB", (120, 80), "black")
        ImageDraw.Draw(im).rectangle((20, 20, 100, 60), outline="white", width=3)
        buf = io.BytesIO()
        im.save(buf, "PNG")
        d = V.image_to_strokes(buf.getvalue(), max_segments=500)
        self.assertAlmostEqual(d["aspect"], 1.5, places=2)
        self.assertTrue(0 < len(d["segments"]) <= 500)
        self.assertTrue(all(0 <= v <= 1 for s in d["segments"] for v in s))
        self.assertIsNone(V.image_to_strokes(b"resim degil"))


class Resp:
    def __init__(self, data=None, content=b""):
        self._d, self.content = data, content

    def raise_for_status(self):
        pass

    def json(self):
        return self._d


class FakeHttp:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, params))
        for key, resp in self.routes.items():
            if key in url:
                return resp
        raise AssertionError(url)


class WeatherTest(unittest.TestCase):
    def setUp(self):
        weather._CACHE.clear()
        self.http = FakeHttp({
            "geocoding": Resp({"results": [{"name": "Ankara", "admin1": "Ankara", "latitude": 39.9, "longitude": 32.8}]}),
            "forecast": Resp({"current": {"temperature_2m": 14.4, "apparent_temperature": 12.1, "relative_humidity_2m": 61,
                                          "weather_code": 63, "wind_speed_10m": 13.2, "is_day": 1},
                              "daily": {"temperature_2m_max": [15.2, 18], "temperature_2m_min": [9, 7],
                                        "precipitation_probability_max": [100, 10], "weather_code": [63, 0]}}),
        })

    def test_today_and_tomorrow(self):
        w = weather.get_weather("Ankara", http=self.http)
        self.assertEqual((w["place"], w["temp"], w["icon"], w["desc"]), ("Ankara", 14, "rain", "Yağmur"))
        self.assertIn("şu an 14°C", weather.summary(w))
        t = weather.get_weather("Ankara", tomorrow=True, http=self.http)
        self.assertEqual((t["icon"], t["max"], t["temp"]), ("sun", 18, None))
        self.assertEqual(weather.visual(w)["type"], "weather")

    def test_default_location_and_cache(self):
        weather.get_weather("", http=self.http)
        n = len(self.http.calls)
        weather.get_weather("", http=self.http)
        self.assertEqual(len(self.http.calls), n)            # 10 dk önbellek
        self.assertFalse(any("geocoding" in u for u, _ in self.http.calls))

    def test_unknown_place(self):
        http = FakeHttp({"geocoding": Resp({"results": []})})
        with self.assertRaises(ValueError):
            weather.get_weather("Yokistan", http=http)


class WikiTest(unittest.TestCase):
    def test_lookup_and_visual(self):
        http = FakeHttp({
            "w/api.php": Resp({"query": {"search": [{"title": "Ayasofya"}]}}),
            "page/summary": Resp({"title": "Ayasofya", "extract": "Ayasofya bir camidir. 537'de yapıldı.",
                                  "thumbnail": {"source": "https://x/y.jpg"}}),
        })
        info = wiki.lookup("Ayasofya", http=http)
        self.assertEqual((info["title"], info["image"]), ("Ayasofya", "https://x/y.jpg"))
        v = wiki.visual(info)
        self.assertEqual((v["type"], v["side"]), ("info", "left"))
        self.assertIn("537", v["data"]["text"])
        self.assertIsNone(wiki.lookup("a", http=http))


class HomeTest(unittest.TestCase):
    def setUp(self):
        t = Path(tempfile.mkdtemp())
        self.home = Home(t / "ev.json", t / "d.json")

    def test_commands(self):
        r, card = self.home.handle("salonun ışığını aç")
        self.assertIn("Salon ışığı", r)
        self.assertEqual(card["data"]["highlight"], ["salon_isik"])
        self.assertTrue(card["data"]["simulated"])
        self.assertTrue(self.home.states()["salon_isik"])
        self.assertIn("Salon ışığı", self.home.handle("evde hangi ışıklar açık")[0])
        self.assertIn("6 cihaz", self.home.handle("tüm ışıkları kapat")[0])
        self.assertFalse(any(self.home.states().values()))
        self.assertEqual(self.home.handle("kombi açık mı")[0], "Kombi kapalı.")
        self.assertIsNone(self.home.handle("chrome'u aç"))
        self.assertIsNone(self.home.handle("kaskı aç"))

    def test_layout_file_written_and_editable(self):
        self.home.layout()
        self.assertTrue(self.home.layout_path.exists())

    def test_provider(self):
        prov = mock.Mock()
        prov.get_states.return_value = {"mutfak_isik": False}
        h = Home(self.home.layout_path, self.home.state_path, provider=prov)
        r, card = h.handle("mutfak lambasını yak")
        prov.set_state.assert_called_once_with("mutfak_isik", True)
        self.assertNotIn("bağlı değilim", r)
        self.assertFalse(card["data"]["simulated"])


class BrainVisualsTest(BrainTestBase):
    def setUp(self):
        super().setUp()
        t = Path(self.tmp.name)
        self.brain.home = Home(t / "ev.json", t / "d.json")
        self.brain.weather_enabled = True
        self.brain.wiki_enabled = True
        self.brain.direct_helmet = True
        self.brain.knowledge = None

    def test_weather_card_and_data_to_model(self):
        w = {"place": "Ankara", "day": "bugün", "temp": 14, "feels": 12, "humidity": 61, "wind": 13, "is_day": True,
             "code": 63, "icon": "rain", "desc": "Yağmur", "max": 15, "min": 9, "rain_chance": 100}
        with mock.patch.object(weather, "get_weather", return_value=w) as gw, \
             self.patch_post([FakeResponse({"content": "Ankara'da yağmur var, 14 derece."})]):
            answer, cards = self.brain.ask_with_visuals("Ankara'da hava nasıl?")
        gw.assert_called_once_with("Ankara", tomorrow=False)
        self.assertEqual([c["type"] for c in cards], ["weather"])
        sys_text = " ".join(m["content"] for m in self.posts[0]["payload"]["messages"] if m.get("role") == "system")
        self.assertIn("GÜNCEL HAVA DURUMU", sys_text)
        self.assertIn("14°C", sys_text)
        self.brain.tools.web.search.assert_not_called()

    def test_home_command_skips_model(self):
        answer, cards = self.brain.ask_with_visuals("mutfak lambasını yak")
        self.assertIn("Mutfak ışığı", answer)
        self.assertEqual(cards[0]["type"], "home")
        self.assertEqual(self.posts, [])

    def test_helmet_command_direct(self):
        self.brain.tools.execute.return_value = "WiFi: Açılıyor"
        answer, cards = self.brain.ask_with_visuals("kaskı aç")
        self.brain.tools.execute.assert_called_once_with("helmet_control", {"action": "ac"})
        self.assertEqual(answer, "Kaskı açıyorum.")
        self.assertEqual(cards[0]["data"]["state"], "open")
        self.brain.tools.execute.return_value = "Kask yapılandırılmamış"
        answer, cards = self.brain.ask_with_visuals("kaskı kapat")
        self.assertIn("ulaşamadım", answer)
        self.assertEqual(cards[0]["data"]["state"], "unknown")

    def test_helmet_blocked_from_phone_when_not_allowed(self):
        with mock.patch.object(self.brain, "allowed_tools_for", return_value=frozenset({"web_search"})), \
             self.patch_post([FakeResponse({"content": "olmaz"})]):
            self.brain.ask("kaskı aç", session_id="phone-a", source="phone")
        self.brain.tools.execute.assert_not_called()

    def test_keyboard_card_from_answer(self):
        with self.patch_post([FakeResponse({"content": "Kopyalamak için Ctrl + C, yapıştırmak için Ctrl + V."})]):
            _answer, cards = self.brain.ask_with_visuals("kopyala yapıştır nasıl yapılır")
        self.assertEqual(cards[0]["type"], "keyboard")
        self.assertEqual([c["label"] for c in cards[0]["data"]["combos"]], ["CTRL + C", "CTRL + V"])

    def test_info_card_uses_wikipedia(self):
        info = {"title": "II. Mehmed", "extract": "Osmanlı padişahı. 1453'te İstanbul'u fethetti.", "image": ""}
        with mock.patch.object(wiki, "lookup", return_value=info) as lk, \
             mock.patch.object(wiki, "drawing", return_value=None), \
             self.patch_post([FakeResponse({"content": "Fatih, 1453'te İstanbul'u fetheden padişahtır."})]):
            _answer, cards = self.brain.ask_with_visuals("Fatih Sultan Mehmet kimdir?")
        lk.assert_called_once_with("Fatih Sultan Mehmet")
        self.assertEqual(cards[0]["type"], "info")
        sys_text = " ".join(m["content"] for m in self.posts[0]["payload"]["messages"] if m.get("role") == "system")
        self.assertIn("VİKİPEDİ ÖZETİ", sys_text)

    def test_plain_ask_has_no_side_effects(self):
        with self.patch_post([FakeResponse({"content": "Ctrl + C"})]):
            self.assertEqual(self.brain.ask("selam"), "Ctrl + C")


class CardLayerTest(unittest.TestCase):
    def setUp(self):
        from interface.cards import CardLayer
        self.t = [100.0]
        self.c = CardLayer(1280, 720, hold=2.0, clock=lambda: self.t[0])

    def test_slide_in_speak_and_slide_out(self):
        self.assertEqual(self.c.visibility(), 0.0)
        self.c.show({"type": "weather", "data": {}})
        self.t[0] += 0.35
        self.assertTrue(0 < self.c.visibility() < 1)
        self.t[0] += 1.0
        self.assertEqual(self.c.visibility(), 1.0)
        s, tx, _ty = self.c.orb_transform(640, 338)
        self.assertLess(s, 0.6)
        self.assertGreater(640 * s + tx, 900)               # görsel sağa kaydı
        for _ in range(10):                                  # konuşma sürüyor: kart açık kalır
            self.t[0] += 1.0
            self.c.update(True, 5)
        self.c.update(False)
        self.t[0] += 1.0
        self.c.update(False)
        self.assertIsNone(self.c.t_hide)                     # bekleme süresi dolmadı
        self.t[0] += 1.5
        self.c.update(False)
        self.assertIsNotNone(self.c.t_hide)
        self.t[0] += 1.0
        self.c.update(False)
        self.assertIsNone(self.c.card)
        self.assertEqual(self.c.orb_transform(640, 338), (1.0, 0.0, 0.0))   # görsel ortaya döndü

    def test_closes_without_speech(self):
        self.c.show({"type": "home", "data": {}})
        for _ in range(8):
            self.t[0] += 1.0
            self.c.update(False)
        self.assertIsNone(self.c.card)

    def test_keyboard_layout_has_combo_keys(self):
        from interface.cards import keyboard_layout
        names = {k[0] for k in keyboard_layout(0, 0)}
        for k in ("CTRL", "ALT", "SHIFT", "WIN", "ESC", "TAB", "ENTER", "DEL", "F4", "C", "V", "Z", "Ş", "İ"):
            self.assertIn(k, names, k)


class SpeakerOffsetsTest(unittest.TestCase):
    def test_sentence_offsets(self):
        from voice.speaker import sentence_offsets
        t = "Önce Ctrl ve C ile kopyala. Sonra gel. Ctrl ve V ile yapıştır."
        self.assertEqual(sentence_offsets(t, ["Önce Ctrl ve C ile kopyala.", "Sonra gel.", "Ctrl ve V ile yapıştır."]),
                         [0, 28, 39])


if __name__ == "__main__":
    unittest.main()
