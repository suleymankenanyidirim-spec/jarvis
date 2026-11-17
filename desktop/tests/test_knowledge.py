"""v2.8: bilgi deposu (önce depo, yoksa internet), yerel model ayarları ve telefon uçları."""
import base64
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.test_brain import BrainTestBase, FakeResponse, stream_lines   # noqa: F401 (sahte requests kurar)
from core import brain as brain_mod
from core import knowledge as K

try:
    import flask  # noqa: F401
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False


def make_kb(tmp):
    return K.KnowledgeBase(Path(tmp) / "Bilgi", index_path=Path(tmp) / "idx.db", refresh_seconds=0)


class KnowledgeBaseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.kb = make_kb(self.tmp.name)

    def write(self, rel, text):
        p = self.kb.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def test_lazy_creation_and_readme(self):
        self.assertFalse(self.kb.root.exists())          # açılışta diske dokunulmaz
        self.assertEqual(self.kb.search("bir şey"), [])
        for sub in ("notlar", "telefon"):
            self.assertTrue((self.kb.root / sub).is_dir())
        self.assertTrue((self.kb.root / K.README_NAME).exists())
        self.assertEqual(self.kb.list_files(), [])        # BENI_OKU dizine girmez

    def test_turkish_search_finds_inflected(self):
        self.write("araba.md", "Arabamın plakası 34 ABC 123.\n\nSigorta Mart ayında bitiyor.")
        self.write("kask.txt", "Kaskın IP adresi 192.168.1.42 olarak ayarlı.")
        hits = self.kb.search("arabanın plakası nedir")
        self.assertEqual(hits[0]["path"], "araba.md")
        self.assertIn("34 ABC 123", hits[0]["text"])
        self.assertEqual(self.kb.search("kaskımın adresi")[0]["path"], "kask.txt")
        self.assertEqual(self.kb.search("bitcoin fiyatı"), [])
        self.assertEqual(self.kb.search("nedir"), [])     # yalnızca durak kelime

    def test_changes_and_deletions_are_seen(self):
        p = self.write("not.txt", "Wifi şifresi kırmızı kedi.")
        self.assertTrue(self.kb.search("wifi şifresi"))
        p.write_text("Modem kullanıcı adı admin.", encoding="utf-8")
        import os
        os.utime(p, (p.stat().st_atime, p.stat().st_mtime + 5))
        self.assertEqual(self.kb.search("wifi şifresi"), [])
        self.assertTrue(self.kb.search("modem kullanıcı"))
        p.unlink()
        self.assertEqual(self.kb.search("modem kullanıcı"), [])

    def test_add_note_and_upload(self):
        rel = self.kb.add_note("Annemin doğum günü 12 Nisan.", title="aile")
        self.assertTrue(rel.startswith("notlar/notlar_"))
        self.assertIn("12 Nisan", self.kb.search("annemin doğum günü")[0]["text"])
        rel = self.kb.add_note("Telefondan not.", source="telefon")
        self.assertTrue(rel.startswith("telefon/"))
        with self.assertRaises(ValueError):
            self.kb.add_note("  ")
        r1 = self.kb.save_upload("../../kötü/../liste.txt", "Market: süt, ekmek".encode("utf-8"))
        r2 = self.kb.save_upload("liste.txt", b"ikinci")
        self.assertEqual(r1, "telefon/liste.txt")          # yol dışına yazılamaz
        self.assertEqual(r2, "telefon/liste_1.txt")        # üzerine yazılmaz
        with self.assertRaises(ValueError):
            self.kb.save_upload("virus.exe", b"x")
        self.kb.max_file_bytes = 3
        with self.assertRaises(OverflowError):
            self.kb.save_upload("b.txt", b"12345")

    def test_html_json_and_chunks(self):
        self.write("s.html", "<html><script>gizli()</script><p>Toplantı Salı 14:00</p></html>")
        self.write("d.json", '{"doktor": "Dr. Ayşe, Perşembe randevu"}')
        self.assertNotIn("gizli", self.kb.search("toplantı ne zaman")[0]["text"])
        self.assertIn("Ayşe", self.kb.search("doktor randevu")[0]["text"])
        chunks = K.chunk_text("a" * 50 + "\n\n" + "b" * 50, size=60)
        self.assertEqual(len(chunks), 2)
        self.assertTrue(all(len(c) <= 1400 for c in K.chunk_text(("cümle. " * 600), size=700)))

    def test_from_config(self):
        cfg = {"KNOWLEDGE_ENABLED": False}
        self.assertIsNone(K.from_config(lambda n, d=None: cfg.get(n, d)))
        cfg = {"KNOWLEDGE_DIR": "Q:/olmayan_surucu/Bilgi"}
        kb = K.from_config(lambda n, d=None: cfg.get(n, d))
        if not Path("Q:/").exists():
            self.assertEqual(kb.root.name, "bilgi")      # sürücü yoksa data/bilgi


class BrainKnowledgeTest(BrainTestBase):
    def setUp(self):
        super().setUp()
        self.brain.knowledge = make_kb(self.tmp.name)
        (self.brain.knowledge.root).mkdir(parents=True)
        (self.brain.knowledge.root / "araba.md").write_text("Arabamın plakası 34 ABC 123.", encoding="utf-8")
        self.brain.tools.web.search.return_value = ([{"x": 1}], None)
        self.brain.tools.web.context.return_value = "WEB SONUCU"

    def system_text(self):
        return "\n".join(m["content"] for m in self.posts[0]["payload"]["messages"] if m.get("role") == "system")

    def test_knowledge_first_no_web(self):
        with self.patch_post([FakeResponse({"content": "34 ABC 123"})]):
            self.brain.ask("arabamın plakası nedir")
        self.assertIn("BİLGİ DEPOSU (", self.system_text())
        self.assertIn("34 ABC 123", self.system_text())
        self.brain.tools.web.search.assert_not_called()
        msgs = self.posts[0]["payload"]["messages"]
        kb_i = next(i for i, m in enumerate(msgs) if "BİLGİ DEPOSU (" in m.get("content", ""))
        user_i = next(i for i, m in enumerate(msgs) if m.get("role") == "user")
        self.assertLess(kb_i, user_i)                      # depo bilgisi kullanıcı mesajından önce

    def test_web_when_not_in_knowledge(self):
        with self.patch_post([FakeResponse({"content": "x"})]):
            self.brain.ask("Türkiye'nin başkenti nedir?")
        self.brain.tools.web.search.assert_called_once()
        self.assertIn("WEB SONUCU", self.system_text())
        self.assertNotIn("BİLGİ DEPOSU (", self.system_text())

    def test_chitchat_does_not_search(self):
        with self.patch_post([FakeResponse({"content": "x"})]):
            self.brain.ask("teşekkürler jarvis")
        self.brain.tools.web.search.assert_not_called()

    def test_fallback_can_be_disabled(self):
        with mock.patch.object(brain_mod, "_cfg", side_effect=lambda n, d=None: {"KNOWLEDGE_WEB_FALLBACK": False}.get(n, d)), \
             self.patch_post([FakeResponse({"content": "x"})]):
            self.brain.ask("Türkiye'nin başkenti nedir?")
        self.brain.tools.web.search.assert_not_called()

    def test_save_command_skips_model(self):
        reply = self.brain.ask("depoya kaydet: Wifi şifresi mavi balina")
        self.assertIn("Bilgi deposuna kaydettim", reply)
        self.assertEqual(self.posts, [])
        self.assertTrue(self.brain.knowledge.search("wifi şifresi"))
        reply = self.brain.ask("bunu depoya ekle telefondan not", source="phone", session_id="phone-a")
        self.assertIn("telefon/", reply)

    def test_stream_reports_knowledge_tool(self):
        with self.patch_post([FakeResponse(lines=stream_lines("34 ABC 123."))]):
            events = list(self.brain.ask_stream("arabamın plakası", session_id="phone-a", source="phone"))
        self.assertEqual(events[0], {"type": "tool", "name": "knowledge_search", "text": "araç çalışıyor: knowledge_search"})
        self.assertEqual(events[-1]["reply"], "34 ABC 123.")


class ModelTuningTest(BrainTestBase):
    def test_options_and_think(self):
        with self.patch_post([FakeResponse({"content": "ok"})]):
            self.brain.ask("selam")
        p = self.posts[0]["payload"]
        self.assertEqual(p["options"]["num_ctx"], 8192)
        self.assertAlmostEqual(p["options"]["temperature"], 0.4)
        self.assertFalse(p["think"])                       # kısa sohbet hızlı
        with self.patch_post([FakeResponse({"content": "ok"})]):
            self.brain.ask("gökyüzü neden mavi görünür, açıklar mısın")
        self.assertTrue(self.posts[1]["payload"]["think"])

    def test_think_setting(self):
        for value, text, expected in ((True, "selam", True), (False, "neden?", False), ("auto", "selam", False)):
            with mock.patch.object(brain_mod, "_cfg", side_effect=lambda n, d=None, v=value: v if n == "OLLAMA_THINK" else d):
                self.assertEqual(brain_mod.wants_thinking(text), expected, (value, text))
        self.assertTrue(brain_mod.wants_thinking("x", source="task"))

    def test_strip_think(self):
        self.assertEqual(brain_mod.strip_think("<think>uzun düşünce</think>\nCevap."), "Cevap.")
        self.assertEqual(brain_mod.strip_think("Cevap <think>yarım"), "Cevap")
        with self.patch_post([FakeResponse({"content": "<think>hmm</think>Merhaba."})]):
            self.assertEqual(self.brain.ask("selam"), "Merhaba.")

    def test_stream_hides_think_tags(self):
        with self.patch_post([FakeResponse(lines=stream_lines("<think>", "iç ses", "</think>Mer", "haba."))]):
            events = list(self.brain.ask_stream("selam"))
        tokens = "".join(e["text"] for e in events if e["type"] == "token")
        self.assertEqual(tokens, "Merhaba.")
        self.assertEqual(events[-1]["reply"], "Merhaba.")

    def test_tool_selection(self):
        names = lambda i: {t["function"]["name"] for t in self.posts[i]["payload"]["tools"]}   # noqa: E731
        with self.patch_post([FakeResponse({"content": "ok"}), FakeResponse({"content": "ok"})]):
            self.brain.ask("chrome'u aç")
            self.brain.ask("kaskı aç", session_id="phone-a", source="phone")
        self.assertIn("open_application", names(0))
        self.assertNotIn("helmet_control", names(0))
        self.assertNotIn("paste_text", names(0))
        self.assertIn("web_search", names(0))
        self.assertIn("helmet_control", names(1))
        self.assertNotIn("close_application", names(1))     # telefon kısıtı seçimden sonra da geçerli

    def test_tool_selection_off(self):
        from core.tools import TOOL_NAMES
        with mock.patch.object(brain_mod, "_cfg", side_effect=lambda n, d=None: False if n == "OLLAMA_TOOL_SELECTION" else d), \
             self.patch_post([FakeResponse({"content": "ok"})]):
            self.brain.ask("chrome'u aç")
        self.assertEqual({t["function"]["name"] for t in self.posts[0]["payload"]["tools"]}, set(TOOL_NAMES))


class ToolManagerKnowledgeTest(unittest.TestCase):
    def test_tools_use_knowledge(self):
        from core.tools import ToolManager
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        tm = ToolManager.__new__(ToolManager)
        tm.knowledge = None
        self.assertIn("kapalı", tm.execute("knowledge_search", {"query": "x"}))
        tm.knowledge = make_kb(tmp.name)
        self.assertIn("kaydedildi", tm.execute("knowledge_save", {"text": "Kedinin adı Pamuk"}))
        self.assertIn("Pamuk", tm.execute("knowledge_search", {"query": "kedinin adı"}))
        self.assertIn("BİLGİ_DEPOSUNDA_YOK", tm.execute("knowledge_search", {"query": "uzay mekiği"}))


@unittest.skipUnless(HAS_FLASK, "flask kurulu değil")
class KnowledgeEndpointTest(unittest.TestCase):
    H = {"X-Jarvis-Token": "tok"}

    def setUp(self):
        from core import phone_server as ps
        self.ps = ps
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.values = {"PHONE_ALLOW_CORS": True, "KNOWLEDGE_MAX_UPLOAD_MB": 1}
        for p in (mock.patch.object(ps, "load_token", return_value="tok"),
                  mock.patch.object(ps, "_config", side_effect=lambda n, d=None: self.values.get(n, d))):
            p.start()
            self.addCleanup(p.stop)
        self.brain = mock.Mock()
        self.brain.knowledge = make_kb(self.tmp.name)
        self.server = ps.PhoneServer(self.brain)
        self.c = self.server.app.test_client()

    def test_requires_token(self):
        self.assertEqual(self.c.get("/api/knowledge").status_code, 401)
        self.assertEqual(self.c.post("/api/knowledge", json={"text": "x"}).status_code, 401)

    def test_note_upload_list_search(self):
        r = self.c.post("/api/knowledge", json={"text": "Bahçe kapısının şifresi 4321"}, headers=self.H)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["path"].startswith("telefon/"))
        data = base64.b64encode("Okul servisi 07:40'ta gelir.".encode("utf-8")).decode()
        r = self.c.post("/api/knowledge", json={"filename": "okul.txt", "data": data}, headers=self.H)
        self.assertEqual(r.get_json()["path"], "telefon/okul.txt")
        d = self.c.get("/api/knowledge", headers=self.H).get_json()
        self.assertEqual(d["count"], 2)
        res = self.c.get("/api/knowledge?q=servis ne zaman gelir", headers=self.H).get_json()["results"]
        self.assertIn("07:40", res[0]["text"])

    def test_errors(self):
        self.assertEqual(self.c.post("/api/knowledge", json={"text": " "}, headers=self.H).status_code, 400)
        self.assertEqual(self.c.post("/api/knowledge", json={"filename": "a.exe", "data": "eA=="},
                                     headers=self.H).status_code, 400)
        self.assertEqual(self.c.post("/api/knowledge", json={"filename": "a.txt", "data": "%%%"},
                                     headers=self.H).status_code, 400)
        big = base64.b64encode(b"x" * (1024 * 1024 + 10)).decode()
        self.assertEqual(self.c.post("/api/knowledge", json={"filename": "a.txt", "data": big},
                                     headers=self.H).status_code, 413)
        self.brain.knowledge = None
        self.assertEqual(self.c.get("/api/knowledge", headers=self.H).status_code, 503)

    def test_tailscale_proxy_is_not_localhost(self):
        # tailscale serve arkasında istek 127.0.0.1'den gelir ama telefon "PC'nin kendisi" sayılmamalı
        env = {"REMOTE_ADDR": "127.0.0.1"}
        r = self.c.post("/api/pair/new", environ_base=env, headers={"X-Forwarded-For": "100.116.73.11"})
        self.assertEqual(r.status_code, 403)
        r = self.c.post("/api/pair/new", environ_base=env, headers={"Tailscale-User-Login": "kisi@ornek"})
        self.assertEqual(r.status_code, 403)
        with mock.patch("builtins.print"):
            self.assertEqual(self.c.post("/api/pair/new", environ_base=env).status_code, 200)

    def test_remote_report(self):
        self.assertEqual(self.ps.remote_report("ABCD2345"), "")
        self.values.update(TAILSCALE_HTTPS_URL="https://sky.tail1.ts.net/", GITHUB_REPO="Kisi/jarvis")
        rep = self.ps.remote_report("ABCD2345")
        self.assertIn("https://kisi.github.io/jarvis/?pair=ABCD2345&pcs=https%3A%2F%2Fsky.tail1.ts.net", rep)


if __name__ == "__main__":
    unittest.main()
