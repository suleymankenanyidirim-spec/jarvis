"""v2.3: brain.ask oturumları, arka plan görevi oturumu, Ollama kilidi, akış (stream)."""
import json
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

# CI'deki test işi yalnızca Flask kurar; brain'in içe aktardığı requests / pyserial yoksa
# test için boş modül konur (gerçek ağ çağrısı yapılmaz, requests.post her testte taklit edilir).
for _name in ("requests", "serial"):
    try:
        __import__(_name)
    except ImportError:
        sys.modules[_name] = types.ModuleType(_name)
if not hasattr(sys.modules["requests"], "post"):
    sys.modules["requests"].post = None
    sys.modules["requests"].get = None

from core import brain as brain_mod          # noqa: E402
from core.brain import JarvisBrain           # noqa: E402
from core.memory import MemoryStore          # noqa: E402


class FakeResponse:
    def __init__(self, message=None, lines=None, delay=0.0):
        self._message = message or {}
        self._lines = lines or []
        self._delay = delay
        self.closed = False

    def raise_for_status(self):
        pass

    def json(self):
        if self._delay:
            time.sleep(self._delay)
        return {"message": self._message}

    def iter_lines(self):
        for line in self._lines:
            yield json.dumps(line).encode("utf8")

    def close(self):
        self.closed = True


def stream_lines(*pieces, tool_calls=None):
    lines = [{"message": {"role": "assistant", "content": p}, "done": False} for p in pieces]
    if tool_calls:
        lines.append({"message": {"role": "assistant", "content": "", "tool_calls": tool_calls}, "done": False})
    lines.append({"message": {"role": "assistant", "content": ""}, "done": True})
    return lines


def make_brain(db_path):
    b = JarvisBrain.__new__(JarvisBrain)
    b.memory = MemoryStore(db_path)
    b.tools = mock.Mock()
    b.tools.web.should_search.return_value = False
    b.tools.execute.return_value = "CPU %12"
    b.tasks = mock.Mock()
    b._init_ollama_lock()
    return b


class BrainTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.brain = make_brain(Path(self.tmp.name) / "m.db")
        self.posts = []

    def tearDown(self):
        self.tmp.cleanup()

    def patch_post(self, responses):
        queue = list(responses)

        def fake_post(url, json=None, timeout=None, stream=False):
            self.posts.append({"payload": json, "stream": stream})
            return queue.pop(0)
        return mock.patch.object(brain_mod.requests, "post", side_effect=fake_post, create=True)

    def context_of(self, call_index=0):
        return self.posts[call_index]["payload"]["messages"][1]["content"]

    def rows(self):
        import sqlite3
        db = sqlite3.connect(self.brain.memory.db_path)
        rows = db.execute("SELECT role, content, session_id, source FROM messages ORDER BY id").fetchall()
        db.close()
        return rows


class AskSessionTest(BrainTestBase):
    def test_ask_defaults_to_hud(self):
        with self.patch_post([FakeResponse({"content": "Merhaba efendim."})]):
            self.assertEqual(self.brain.ask("selam"), "Merhaba efendim.")
        self.assertEqual(self.rows(), [("user", "selam", "hud", "hud"),
                                       ("assistant", "Merhaba efendim.", "hud", "hud")])
        self.assertFalse(self.posts[0]["payload"]["stream"])

    def test_old_keyword_call_still_works(self):
        with self.patch_post([FakeResponse({"content": "ok"})]):
            self.assertEqual(self.brain.ask("selam", allow_planning=False), "ok")

    def test_recent_context_only_from_own_session(self):
        self.brain.memory.save_memory("Kullanıcının adı Sky")
        self.brain.memory.save_message("user", "HUD-GIZLI", "hud", "hud")
        self.brain.memory.save_message("user", "GOREV-GIZLI", "task-1", "task")
        self.brain.memory.save_message("user", "telefon önceki", "phone-a", "phone")
        with self.patch_post([FakeResponse({"content": "cevap"})]):
            self.brain.ask("adım ne", session_id="phone-a", source="phone")
        ctx = self.context_of()
        self.assertIn("Kullanıcının adı Sky", ctx)       # kalıcı hafıza ortak
        self.assertIn("telefon önceki", ctx)
        self.assertNotIn("HUD-GIZLI", ctx)
        self.assertNotIn("GOREV-GIZLI", ctx)
        self.assertEqual(self.rows()[-1], ("assistant", "cevap", "phone-a", "phone"))

    def test_hud_does_not_see_phone(self):
        self.brain.memory.save_message("user", "TELEFON-GIZLI", "phone-a", "phone")
        with self.patch_post([FakeResponse({"content": "x"})]):
            self.brain.ask("merhaba")
        self.assertNotIn("TELEFON-GIZLI", self.context_of())

    def test_hud_session_from_config(self):
        with mock.patch.object(brain_mod, "_cfg", side_effect=lambda n, d=None: {"HUD_SESSION_ID": "masa"}.get(n, d)), \
             self.patch_post([FakeResponse({"content": "x"})]):
            self.brain.ask("merhaba")
        self.assertEqual(self.rows()[0][2], "masa")

    def test_invalid_source(self):
        with self.assertRaises(ValueError):
            self.brain.ask("x", source="web")

    def test_tool_round(self):
        call = [{"function": {"name": "get_system_info", "arguments": "{}"}}]
        with self.patch_post([FakeResponse({"content": "", "tool_calls": call}),
                              FakeResponse({"content": "CPU yüzde 12."})]):
            self.assertEqual(self.brain.ask("cpu kaç"), "CPU yüzde 12.")
        self.brain.tools.execute.assert_called_once_with("get_system_info", {})
        self.assertIn("tool", [m.get("role") for m in self.posts[1]["payload"]["messages"]])

    def test_long_task_delegated(self):
        self.brain.start_planned_task = mock.Mock(return_value="Planlı arka plan görevi başlatıldı. Görev ID: ab")
        with mock.patch.object(brain_mod, "_cfg", side_effect=lambda n, d=None: d):
            reply = self.brain.ask("bunu detaylı araştır ve rapor hazırla", session_id="phone-a", source="phone")
        self.assertIn("Planlı", reply)
        self.assertEqual([r[2] for r in self.rows()], ["phone-a", "phone-a"])


class BackgroundSessionTest(BrainTestBase):
    def test_background_ask_writes_to_task_session(self):
        with self.patch_post([FakeResponse({"content": "görev bitti"})]):
            self.assertEqual(self.brain._background_ask("arka plan işi", task_id="ab12cd34"), "görev bitti")
        self.assertEqual({(r[2], r[3]) for r in self.rows()}, {("task-ab12cd34", "task")})
        self.assertEqual(self.brain.memory.recent_messages(8, session_id="hud"), [])

    def test_background_does_not_see_chat(self):
        self.brain.memory.save_message("user", "SOHBET", "hud", "hud")
        with self.patch_post([FakeResponse({"content": "x"})]):
            self.brain._background_ask("iş", task_id="t1")
        self.assertNotIn("SOHBET", self.context_of())

    def test_run_background_submits_task_function(self):
        self.brain.tasks.submit.return_value = "t1"
        out = self.brain.run_background("başlık", "iş")
        self.assertIn("t1", out)
        args = self.brain.tasks.submit.call_args
        self.assertEqual(args.args[1], self.brain._background_ask)
        # BackgroundTaskManager task_id'yi parametre adından tanır
        import inspect
        self.assertIn("task_id", inspect.signature(self.brain._background_ask).parameters)

    def test_planned_task_records_own_session(self):
        self.brain.planner = mock.Mock()
        self.brain.planner.create_plan.return_value = {"steps": []}
        self.brain._local_chat_text = mock.Mock(return_value="SENTEZ")
        self.assertEqual(self.brain._execute_planned("t", "hedef", task_id="p1"), "SENTEZ")
        self.assertEqual([(r[0], r[2], r[3]) for r in self.rows()],
                         [("user", "task-p1", "task"), ("assistant", "task-p1", "task")])


class StreamTest(BrainTestBase):
    def test_stream_tokens_and_done(self):
        with self.patch_post([FakeResponse(lines=stream_lines("Mer", "haba ", "efendim."))]):
            events = list(self.brain.ask_stream("selam", session_id="phone-a", source="phone"))
        self.assertEqual([e["text"] for e in events if e["type"] == "token"], ["Mer", "haba ", "efendim."])
        self.assertEqual(events[-1], {"type": "done", "reply": "Merhaba efendim."})
        self.assertTrue(self.posts[0]["stream"])
        self.assertTrue(self.posts[0]["payload"]["stream"])
        self.assertEqual(self.rows()[-1], ("assistant", "Merhaba efendim.", "phone-a", "phone"))

    def test_stream_tool_event(self):
        call = [{"function": {"name": "get_system_info", "arguments": {}}}]
        with self.patch_post([FakeResponse(lines=stream_lines(tool_calls=call)),
                              FakeResponse(lines=stream_lines("CPU yüzde 12."))]):
            events = list(self.brain.ask_stream("cpu kaç", session_id="phone-a", source="phone"))
        types_ = [e["type"] for e in events]
        self.assertEqual(types_, ["tool", "token", "done"])
        self.assertEqual(events[0]["text"], "araç çalışıyor: get_system_info")
        self.assertEqual(events[0]["name"], "get_system_info")
        self.assertEqual(events[-1]["reply"], "CPU yüzde 12.")

    def test_stream_web_search_event(self):
        self.brain.tools.web.should_search.return_value = True
        self.brain.tools.web.search.return_value = ([{"x": 1}], None)
        self.brain.tools.web.context.return_value = "KAYNAK"
        with self.patch_post([FakeResponse(lines=stream_lines("ok"))]):
            events = list(self.brain.ask_stream("güncel haber", source="phone"))
        self.assertEqual(events[0]["text"], "araç çalışıyor: web_search")

    def test_stream_error_line(self):
        with self.patch_post([FakeResponse(lines=[{"error": "model yok"}])]):
            with self.assertRaises(RuntimeError):
                list(self.brain.ask_stream("x"))
        self.assertTrue(self.brain._acquire_slot(blocking=False), "kilit bırakılmalı")

    def test_closing_stream_releases_lock(self):
        resp = FakeResponse(lines=stream_lines("a", "b", "c"))
        with self.patch_post([resp]):
            gen = self.brain._chat_stream([])
            self.assertEqual(next(gen), ("token", "a"))
            gen.close()                      # telefon bağlantıyı kopardı
        self.assertTrue(resp.closed)
        self.assertTrue(self.brain._acquire_slot(blocking=False))

    def test_wait_event_when_busy(self):
        self.brain._acquire_slot()
        with self.patch_post([FakeResponse(lines=stream_lines("tamam"))]):
            gen = self.brain.ask_stream("x")
            first = next(gen)
            self.assertEqual(first["type"], "wait")
            threading.Timer(0.05, self.brain._release_slot).start()
            rest = list(gen)
        self.assertEqual(rest[-1]["reply"], "tamam")


class BrainInitTest(unittest.TestCase):
    def test_memory_stemming_setting_is_passed(self):
        # v2.4: config.MEMORY_STEMMING hafıza deposuna iletilir.
        names = ("MemoryStore", "ToolManager", "BackgroundTaskManager", "SelfUpdater", "AICouncil", "TaskPlanner")
        patches = [mock.patch.object(brain_mod, n) for n in names]
        mocks = [p.start() for p in patches]
        self.addCleanup(lambda: [p.stop() for p in patches])
        cfg = {"MEMORY_STEMMING": False, "MEMORY_DB": "x.db"}
        with mock.patch.object(brain_mod, "_cfg", side_effect=lambda n, d=None: cfg.get(n, d)):
            JarvisBrain()
        mocks[0].assert_called_once_with("x.db", stemming=False)


class OllamaLockTest(BrainTestBase):
    def test_calls_never_overlap(self):
        active = {"now": 0, "max": 0}
        guard = threading.Lock()

        class Slow(FakeResponse):
            def json(self_inner):
                with guard:
                    active["now"] += 1
                    active["max"] = max(active["max"], active["now"])
                time.sleep(0.02)
                with guard:
                    active["now"] -= 1
                return {"message": {"content": "x"}}

        with mock.patch.object(brain_mod.requests, "post", side_effect=lambda *a, **k: Slow(), create=True):
            threads = [threading.Thread(target=self.brain._chat, args=([],)) for _ in range(6)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self.assertEqual(active["max"], 1)

    def test_concurrency_setting(self):
        with mock.patch.object(brain_mod, "_cfg", side_effect=lambda n, d=None: {"OLLAMA_MAX_CONCURRENT": 2}.get(n, d)):
            self.brain._init_ollama_lock()
        self.assertTrue(self.brain._acquire_slot(blocking=False))
        self.assertTrue(self.brain._acquire_slot(blocking=False))
        self.assertFalse(self.brain._acquire_slot(blocking=False))

    def test_lock_timeout_message(self):
        self.brain._acquire_slot()
        with mock.patch.object(brain_mod, "_cfg", side_effect=lambda n, d=None: {"OLLAMA_LOCK_TIMEOUT": 0.05}.get(n, d)):
            with self.assertRaises(RuntimeError) as ctx:
                self.brain._chat([])
        self.assertIn("Model meşgul", str(ctx.exception))

    def test_lock_released_after_error(self):
        def boom(*a, **k):
            raise ConnectionError("Ollama kapalı")
        with mock.patch.object(brain_mod.requests, "post", side_effect=boom, create=True):
            with self.assertRaises(ConnectionError):
                self.brain._chat([])
        self.assertTrue(self.brain._acquire_slot(blocking=False))


if __name__ == "__main__":
    unittest.main()
