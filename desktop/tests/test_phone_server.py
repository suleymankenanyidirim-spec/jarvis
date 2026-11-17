"""v2.2: telefon sunucusu — Tailscale adresi, LAN + Tailscale linkleri, port kontrolü."""
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, unquote, urlparse

try:
    import flask  # noqa: F401
    HAS_FLASK = True
except ImportError:          # CI'deki test işi bağımlılık kurmaz
    HAS_FLASK = False

if HAS_FLASK:
    from core import phone_server as ps
    from core.paths import DATA_DIR


@unittest.skipUnless(HAS_FLASK, "flask kurulu değil")
class TailscaleIpTest(unittest.TestCase):
    def test_is_tailscale_ip(self):
        self.assertTrue(ps.is_tailscale_ip("100.101.102.103"))
        self.assertTrue(ps.is_tailscale_ip("100.64.0.1"))
        self.assertTrue(ps.is_tailscale_ip("100.127.255.254"))
        self.assertFalse(ps.is_tailscale_ip("100.63.255.255"))
        self.assertFalse(ps.is_tailscale_ip("100.128.0.1"))
        self.assertFalse(ps.is_tailscale_ip("192.168.1.20"))
        self.assertFalse(ps.is_tailscale_ip("çöp"))
        self.assertFalse(ps.is_tailscale_ip(""))

    def _cfg(self, **values):
        return mock.patch.object(ps, "_config", side_effect=lambda n, d=None: values.get(n, d))

    def test_manual_setting_wins(self):
        with self._cfg(TAILSCALE_IP="100.1.2.3"), mock.patch.object(ps.subprocess, "run") as run:
            self.assertEqual(ps.tailscale_ip(), "100.1.2.3")
            run.assert_not_called()

    def test_detect_disabled(self):
        with self._cfg(TAILSCALE_DETECT=False), mock.patch.object(ps.subprocess, "run") as run:
            self.assertEqual(ps.tailscale_ip(), "")
            run.assert_not_called()

    def test_from_cli(self):
        done = subprocess.CompletedProcess([], 0, stdout="100.101.102.103\n", stderr="")
        with self._cfg(), mock.patch.object(ps.subprocess, "run", return_value=done):
            self.assertEqual(ps.tailscale_ip(), "100.101.102.103")

    def test_cli_missing_falls_back_to_interfaces(self):
        infos = [(socket.AF_INET, 0, 0, "", ("192.168.1.20", 0)),
                 (socket.AF_INET, 0, 0, "", ("100.90.1.2", 0))]
        with self._cfg(), mock.patch.object(ps.subprocess, "run", side_effect=FileNotFoundError), \
             mock.patch.object(ps.socket, "getaddrinfo", return_value=infos):
            self.assertEqual(ps.tailscale_ip(), "100.90.1.2")

    def test_nothing_found(self):
        with self._cfg(), mock.patch.object(ps.subprocess, "run", side_effect=OSError), \
             mock.patch.object(ps.socket, "getaddrinfo", side_effect=OSError):
            self.assertEqual(ps.tailscale_ip(), "")


@unittest.skipUnless(HAS_FLASK, "flask kurulu değil")
class LinksTest(unittest.TestCase):
    def test_both_links_carry_all_addresses_in_order(self):
        links = ps.build_links("192.168.1.20", "100.101.102.103", 5000, "abc")
        self.assertEqual(links["bases"], ["http://192.168.1.20:5000", "http://100.101.102.103:5000"])
        for key, host in (("lan", "192.168.1.20"), ("tailscale", "100.101.102.103")):
            u = urlparse(links[key])
            self.assertEqual(u.hostname, host)
            q = parse_qs(u.query)
            self.assertEqual(q["pair"], ["abc"])        # v2.6: anahtar değil, eşleşme kodu
            self.assertNotIn("token", q)
            self.assertEqual(unquote(q["pcs"][0]).split(","), links["bases"])

    def test_without_tailscale(self):
        links = ps.build_links("192.168.1.20", "", 5000, "abc")
        self.assertEqual(links["tailscale"], "")
        self.assertEqual(links["lan"], "http://192.168.1.20:5000/?pair=abc")
        report = ps.link_report(links, "abc")
        self.assertIn("Tailscale linki     : bulunamadı", report)

    def test_report_has_both(self):
        links = ps.build_links("192.168.1.20", "100.101.102.103", 5000, "abc")
        report = ps.link_report(links, "abc")
        self.assertIn(links["lan"], report)
        self.assertIn(links["tailscale"], report)
        self.assertIn("Eşleşme kodu        : ABC", report)
        self.assertNotIn("Erişim anahtarı", report)

    def test_pair_code_is_url_encoded(self):
        links = ps.build_links("1.2.3.4", "", 5000, "a b&c")
        self.assertIn("pair=a%20b%26c", links["lan"])

    def test_paths_use_main_py_folder(self):
        self.assertEqual(ps.TOKEN_FILE, DATA_DIR / "phone_token.txt")
        self.assertEqual(ps.LINK_FILE, DATA_DIR / "phone_link.txt")


@unittest.skipUnless(HAS_FLASK, "flask kurulu değil")
class StartServerTest(unittest.TestCase):
    def test_port_in_use(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        try:
            self.assertTrue(ps.port_in_use(s.getsockname()[1]))
        finally:
            s.close()

    def test_start_writes_both_links_to_file(self):
        with tempfile.TemporaryDirectory() as d:
            link_file = Path(d) / "phone_link.txt"
            with mock.patch.object(ps, "LINK_FILE", link_file), \
                 mock.patch.object(ps, "port_in_use", return_value=False), \
                 mock.patch.object(ps, "load_token", return_value="tok"), \
                 mock.patch.object(ps, "local_ip", return_value="192.168.1.20"), \
                 mock.patch.object(ps, "tailscale_ip", return_value="100.101.102.103"), \
                 mock.patch.object(ps, "_print_qr"), \
                 mock.patch.object(ps.PhoneServer, "run"), \
                 mock.patch("builtins.print") as printed:
                server = ps.start_phone_server(object(), 5000)
            self.assertIsNotNone(server)
            text = link_file.read_text(encoding="utf8")
            code = server.pairing.current()
            self.assertTrue(code)
            self.assertIn(f"http://192.168.1.20:5000/?pair={code}", text)
            self.assertIn(f"http://100.101.102.103:5000/?pair={code}", text)
            self.assertNotIn("tok", text.replace("Tailscale", ""))   # anahtar dosyaya yazılmaz
            console = " ".join(" ".join(map(str, c.args)) for c in printed.call_args_list)
            self.assertIn("192.168.1.20", console)
            self.assertIn("100.101.102.103", console)

    def test_start_returns_none_when_port_busy(self):
        with mock.patch.object(ps, "port_in_use", return_value=True), mock.patch("builtins.print"):
            self.assertIsNone(ps.start_phone_server(object(), 5000))


@unittest.skipUnless(HAS_FLASK, "flask kurulu değil")
class ServesBackendJsTest(unittest.TestCase):
    def test_mobile_ui_ships_backend_js(self):
        server = ps.PhoneServer.__new__(ps.PhoneServer)
        ui = ps.find_ui_dir()
        if ui is None:
            self.skipTest("telefon arayüzü yok")
        self.assertTrue((ui / "backend.js").is_file())
        html = (ui / "index.html").read_text(encoding="utf8")
        self.assertLess(html.index("backend.js"), html.index("app.js"))
        del server

    def test_mobile_ui_ships_stream_js(self):
        ui = ps.find_ui_dir()
        if ui is None:
            self.skipTest("telefon arayüzü yok")
        self.assertTrue((ui / "stream.js").is_file())
        html = (ui / "index.html").read_text(encoding="utf8")
        self.assertLess(html.index("stream.js"), html.index("app.js"))
        self.assertIn("stream.js", (ui / "sw.js").read_text(encoding="utf8"))


class FakeBrain:
    """v2.3: /api/ask ve /api/ask_stream için sahte beyin."""

    def __init__(self, events=None, fail=None):
        self.calls = []
        self.events = events or []
        self.fail = fail

    def ask(self, text, session_id=None, source=None, allow_planning=True):
        self.calls.append(("ask", text, session_id, source))
        return "cevap: " + text

    def ask_stream(self, text, session_id=None, source=None, allow_planning=True):
        self.calls.append(("stream", text, session_id, source))
        for ev in self.events:
            yield ev
        if self.fail:
            raise RuntimeError(self.fail)


def parse_sse(body):
    out = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        ev = __import__("json").loads(lines["data"])
        assert lines["event"] == ev["type"]
        out.append(ev)
    return out


@unittest.skipUnless(HAS_FLASK, "flask kurulu değil")
class AskEndpointsTest(unittest.TestCase):
    def make(self, brain, **cfg):
        values = {"PHONE_ALLOW_CORS": True}
        values.update(cfg)
        patches = [mock.patch.object(ps, "load_token", return_value="tok"),
                   mock.patch.object(ps, "_config", side_effect=lambda n, d=None: values.get(n, d))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return ps.PhoneServer(brain).app.test_client()

    H = {"X-Jarvis-Token": "tok"}

    def test_ask_uses_phone_session(self):
        brain = FakeBrain()
        c = self.make(brain)
        r = c.post("/api/ask", json={"text": "selam", "session_id": "phone-abc123"}, headers=self.H)
        self.assertEqual(r.get_json(), {"reply": "cevap: selam", "session_id": "phone-abc123"})
        self.assertEqual(brain.calls, [("ask", "selam", "phone-abc123", "phone")])

    def test_ask_without_session_uses_default(self):
        brain = FakeBrain()
        c = self.make(brain, PHONE_SESSION_ID="telefon")
        c.post("/api/ask", json={"text": "selam"}, headers=self.H)
        self.assertEqual(brain.calls[0][2], "telefon")

    def test_phone_cannot_write_task_or_bad_session(self):
        brain = FakeBrain()
        c = self.make(brain)
        c.post("/api/ask", json={"text": "a", "session_id": "task-ab12"}, headers=self.H)
        c.post("/api/ask", json={"text": "b", "session_id": "kötü oturum!"}, headers=self.H)
        self.assertEqual([x[2] for x in brain.calls], ["phone", "phone"])

    def test_legacy_routes_still_work(self):
        brain = FakeBrain()
        c = self.make(brain)
        self.assertEqual(c.post("/ask", json={"text": "eski"}, headers=self.H).get_json()["reply"], "cevap: eski")
        self.assertEqual(c.post("/api/ask", json={"text": "x"}).status_code, 401)
        self.assertEqual(c.post("/api/ask", json={"text": ""}, headers=self.H).get_json()["reply"], "Boş mesaj.")

    def test_tag_messages_setting(self):
        brain = FakeBrain()
        c = self.make(brain, PHONE_TAG_MESSAGES=True)
        c.post("/api/ask", json={"text": "selam"}, headers=self.H)
        self.assertEqual(brain.calls[0][1], "[Telefondan] selam")

    def test_stream_events(self):
        brain = FakeBrain(events=[
            {"type": "tool", "name": "web_search", "text": "araç çalışıyor: web_search"},
            {"type": "token", "text": "Merhaba "},
            {"type": "token", "text": "efendim."},
            {"type": "done", "reply": "Merhaba efendim."},
        ])
        c = self.make(brain)
        r = c.post("/api/ask_stream", json={"text": "selam", "session_id": "phone-x"},
                   headers=dict(self.H, Origin="https://localhost"))
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.mimetype == "text/event-stream")
        self.assertEqual(r.headers["Cache-Control"], "no-cache")
        self.assertEqual(r.headers["Access-Control-Allow-Origin"], "https://localhost")
        events = parse_sse(r.get_data(as_text=True))
        self.assertEqual([e["type"] for e in events], ["start", "tool", "token", "token", "done"])
        self.assertEqual(events[0]["session_id"], "phone-x")
        self.assertEqual(events[1]["text"], "araç çalışıyor: web_search")
        self.assertEqual(events[-1]["reply"], "Merhaba efendim.")
        self.assertEqual(brain.calls, [("stream", "selam", "phone-x", "phone")])

    def test_stream_error_event(self):
        brain = FakeBrain(events=[{"type": "token", "text": "Yar"}], fail="Ollama kapalı")
        c = self.make(brain)
        events = parse_sse(c.post("/api/ask_stream", json={"text": "x"}, headers=self.H).get_data(as_text=True))
        self.assertEqual([e["type"] for e in events], ["start", "token", "error"])
        self.assertIn("Ollama kapalı", events[-1]["error"])

    def test_stream_requires_token(self):
        c = self.make(FakeBrain())
        self.assertEqual(c.post("/api/ask_stream", json={"text": "x"}).status_code, 401)

    def test_stream_empty_text(self):
        brain = FakeBrain()
        c = self.make(brain)
        events = parse_sse(c.post("/api/ask_stream", json={"text": " "}, headers=self.H).get_data(as_text=True))
        self.assertEqual(events, [{"type": "done", "reply": "Boş mesaj."}])
        self.assertEqual(brain.calls, [])

    def test_stream_can_be_disabled(self):
        c = self.make(FakeBrain(), PHONE_STREAM_ENABLED=False)
        self.assertEqual(c.post("/api/ask_stream", json={"text": "x"}, headers=self.H).status_code, 404)

    def test_stream_preflight(self):
        c = self.make(FakeBrain())
        r = c.open("/api/ask_stream", method="OPTIONS", headers={"Origin": "https://localhost"})
        self.assertLess(r.status_code, 300)
        self.assertIn("X-Jarvis-Token", r.headers["Access-Control-Allow-Headers"])
        self.assertIn("POST", r.headers["Access-Control-Allow-Methods"])

    def test_cors_allows_delete(self):
        c = self.make(FakeBrain())
        r = c.open("/api/memories/3", method="OPTIONS", headers={"Origin": "https://localhost"})
        self.assertIn("DELETE", r.headers["Access-Control-Allow-Methods"])

    def test_sse_format_keeps_turkish(self):
        text = ps.sse_format({"type": "token", "text": "çğış\nikinci satır"})
        self.assertTrue(text.startswith("event: token\ndata: "))
        self.assertTrue(text.endswith("\n\n"))
        self.assertEqual(text.count("\n"), 3)       # JSON içindeki satır sonu kaçışlı
        self.assertIn("çğış", text)


class MemoryBrain:
    """v2.4: gerçek MemoryStore'lu sahte beyin."""

    def __init__(self, path):
        from core.memory import MemoryStore
        self.memory = MemoryStore(path)


@unittest.skipUnless(HAS_FLASK, "flask kurulu değil")
class MemoryEndpointsTest(unittest.TestCase):
    H = {"X-Jarvis-Token": "tok"}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.brain = MemoryBrain(Path(self.tmp.name) / "m.db")
        self.values = {"PHONE_ALLOW_CORS": True}
        for p in (mock.patch.object(ps, "load_token", return_value="tok"),
                  mock.patch.object(ps, "_config", side_effect=lambda n, d=None: self.values.get(n, d))):
            p.start()
            self.addCleanup(p.stop)
        self.c = ps.PhoneServer(self.brain).app.test_client()

    def test_requires_token(self):
        for method, url in (("GET", "/api/memories"), ("POST", "/api/memories"), ("DELETE", "/api/memories/1"),
                            ("GET", "/api/history"), ("POST", "/api/history")):
            self.assertEqual(self.c.open(url, method=method, json={}).status_code, 401, (method, url))

    def test_crud(self):
        r = self.c.post("/api/memories", json={"content": "Doğum günüm 3 Mayıs"}, headers=self.H)
        self.assertEqual(r.status_code, 200)
        mid = r.get_json()["id"]
        self.assertEqual(r.get_json()["status"], "added")
        self.assertEqual(self.c.post("/api/memories", json={"content": "doğum günüm 3 mayıs."},
                                     headers=self.H).get_json()["status"], "exists")
        d = self.c.get("/api/memories", headers=self.H).get_json()
        self.assertEqual(d["count"], 1)
        self.assertEqual(d["memories"][0]["content"], "Doğum günüm 3 Mayıs")
        r = self.c.post("/api/memories", json={"id": mid, "content": "Doğum günüm 4 Mayıs"}, headers=self.H)
        self.assertEqual(r.get_json()["status"], "updated")
        self.assertEqual(self.c.delete(f"/api/memories/{mid}", headers=self.H).get_json(), {"deleted": True, "id": mid})
        self.assertEqual(self.c.get("/api/memories", headers=self.H).get_json()["count"], 0)
        # PC'deki SQLite gerçekten değişti
        self.assertEqual(self.brain.memory.list_memories(), [])

    def test_delete_variants_and_errors(self):
        mid = self.brain.memory.add_memory("a")["id"]
        self.assertTrue(self.c.delete(f"/api/memories?id={mid}", headers=self.H).get_json()["deleted"])
        self.assertFalse(self.c.delete(f"/api/memories/{mid}", headers=self.H).get_json()["deleted"])
        self.assertEqual(self.c.delete("/api/memories", headers=self.H).status_code, 400)
        self.assertEqual(self.c.post("/api/memories", json={"content": " "}, headers=self.H).status_code, 400)
        self.assertEqual(self.c.post("/api/memories", json={"id": 999, "content": "x"}, headers=self.H).status_code, 404)
        self.assertEqual(self.c.post("/api/memories", json={"id": "abc", "content": "x"}, headers=self.H).status_code, 400)

    def test_batch_sync_is_idempotent(self):
        self.brain.memory.add_memory("PC'de zaten var")
        items = [{"client_id": "c-1", "content": "Bulutta eklendi"},
                 {"client_id": "c-2", "content": "pc'de zaten var"},
                 {"client_id": "c-3", "content": "Bulutta eklendi"}]
        d = self.c.post("/api/memories", json={"items": items}, headers=self.H).get_json()
        self.assertEqual([r["status"] for r in d["results"]], ["added", "exists", "exists"])
        self.assertEqual([r["client_id"] for r in d["results"]], ["c-1", "c-2", "c-3"])
        self.assertEqual(len(d["memories"]), 2)
        # Cevap kayboldu, telefon aynı isteği tekrar gönderdi
        d = self.c.post("/api/memories", json={"items": items}, headers=self.H).get_json()
        self.assertEqual([r["status"] for r in d["results"]], ["synced", "synced", "synced"])
        self.assertEqual(len(self.brain.memory.list_memories()), 2)

    def test_batch_limits(self):
        self.values["SYNC_MAX_ITEMS"] = 2
        items = [{"client_id": f"c-{i}", "content": str(i)} for i in range(3)]
        self.assertEqual(self.c.post("/api/memories", json={"items": items}, headers=self.H).status_code, 413)
        self.assertEqual(self.c.post("/api/memories", json={"items": "x"}, headers=self.H).status_code, 400)

    def test_history_get(self):
        for i in range(5):
            self.brain.memory.save_message("user", f"m{i}", "phone-a", "phone")
        self.brain.memory.save_message("user", "hud", "hud", "hud")
        d = self.c.get("/api/history?session=phone-a&limit=2", headers=self.H).get_json()
        self.assertEqual(d["session"], "phone-a")
        self.assertEqual([m["content"] for m in d["messages"]], ["m3", "m4"])
        d = self.c.get("/api/history?session=hud", headers=self.H).get_json()
        self.assertEqual([m["content"] for m in d["messages"]], ["hud"])

    def test_history_limits_and_default_session(self):
        self.values.update(HISTORY_MAX_LIMIT=3, HISTORY_DEFAULT_LIMIT=2, PHONE_SESSION_ID="phone")
        for i in range(6):
            self.brain.memory.save_message("user", f"m{i}", "phone", "phone")
        self.assertEqual(len(self.c.get("/api/history?limit=100", headers=self.H).get_json()["messages"]), 3)
        self.assertEqual(len(self.c.get("/api/history", headers=self.H).get_json()["messages"]), 2)
        self.assertEqual(len(self.c.get("/api/history?limit=abc", headers=self.H).get_json()["messages"]), 2)
        self.assertEqual(len(self.c.get("/api/history?limit=0", headers=self.H).get_json()["messages"]), 1)
        d = self.c.get("/api/history?session=kötü oturum", headers=self.H).get_json()
        self.assertEqual(d["session"], "phone")

    def test_history_post_import(self):
        msgs = [{"client_id": "c-1", "role": "user", "text": "bulut sorusu", "t": 1794848000000},
                {"client_id": "c-2", "role": "assistant", "text": "bulut cevabı", "t": 1794848001000}]
        body = {"session_id": "phone-a", "messages": msgs}
        d = self.c.post("/api/history", json=body, headers=self.H).get_json()
        self.assertEqual((d["imported"], d["skipped"], d["session"]), (2, 0, "phone-a"))
        d = self.c.post("/api/history", json=body, headers=self.H).get_json()
        self.assertEqual((d["imported"], d["skipped"]), (0, 2))
        self.assertEqual([m["source"] for m in self.brain.memory.history("phone-a", 10)], ["phone", "phone"])

    def test_history_post_cannot_write_task_session(self):
        body = {"session_id": "task-ab", "messages": [{"client_id": "c-1", "role": "user", "text": "x"}]}
        self.assertEqual(self.c.post("/api/history", json=body, headers=self.H).get_json()["session"], "phone")
        self.assertEqual(self.c.post("/api/history", json={"messages": "x"}, headers=self.H).status_code, 400)


@unittest.skipUnless(HAS_FLASK, "flask kurulu değil")
class ServesSyncJsTest(unittest.TestCase):
    def test_mobile_ui_ships_sync_js(self):
        ui = ps.find_ui_dir()
        if ui is None:
            self.skipTest("telefon arayüzü yok")
        html = (ui / "index.html").read_text(encoding="utf8")
        self.assertTrue((ui / "sync.js").is_file())
        self.assertLess(html.index("sync.js"), html.index("app.js"))
        self.assertIn("sync.js", (ui / "sw.js").read_text(encoding="utf8"))

    def test_mobile_ui_ships_v25_scripts(self):
        # v2.5: derleme bilgisi, güncelleme denetimi ve görev bildirimi app.js'ten önce yüklenir.
        ui = ps.find_ui_dir()
        if ui is None:
            self.skipTest("telefon arayüzü yok")
        html = (ui / "index.html").read_text(encoding="utf8")
        sw = (ui / "sw.js").read_text(encoding="utf8")
        for name in ("build-info.js", "updates.js", "tasknotify.js"):
            self.assertTrue((ui / name).is_file(), name)
            self.assertLess(html.index(name), html.index("app.js"), name)
            self.assertIn(name, sw, name)
        self.assertIn('id="taskNotify"', html)
        self.assertIn('id="updateCheck"', html)


if __name__ == "__main__":
    unittest.main()
