"""v2.6: telefondan gelen isteklerde araç kısıtı (config.PHONE_ALLOWED_TOOLS)."""
import unittest
from unittest import mock

import config
from tests.test_brain import BrainTestBase, FakeResponse, stream_lines   # noqa: F401 (sahte requests kurar)
from core import brain as brain_mod
from core.tools import PHONE_BLOCKED_BY_DEFAULT, TOOL_NAMES

BLOCKED = ("close_application", "paste_text", "paste_clipboard", "stage_self_update")


def call(name, args="{}"):
    return [{"function": {"name": name, "arguments": args}}]


def tool_names(payload):
    return {t["function"]["name"] for t in payload["tools"]}


class ConfigListTest(unittest.TestCase):
    def test_default_blocked_set(self):
        self.assertEqual(set(PHONE_BLOCKED_BY_DEFAULT), set(BLOCKED))

    def test_config_list_covers_every_other_tool(self):
        allowed = set(config.PHONE_ALLOWED_TOOLS)
        for name in BLOCKED:
            self.assertNotIn(name, allowed, name)
        # Yeni araç eklenince config.py listesi de güncellensin (ya da bilerek kapalı kalsın)
        self.assertEqual(allowed | set(BLOCKED), set(TOOL_NAMES))

    def test_phone_allowed_tools_reads_config(self):
        with mock.patch.object(brain_mod, "_cfg", side_effect=lambda n, d=None: d):
            self.assertEqual(brain_mod.phone_allowed_tools(), frozenset(TOOL_NAMES) - set(BLOCKED))
        values = {"PHONE_ALLOWED_TOOLS": "web_search, paste_text"}
        with mock.patch.object(brain_mod, "_cfg", side_effect=lambda n, d=None: values.get(n, d)):
            self.assertEqual(brain_mod.phone_allowed_tools(), {"web_search", "paste_text"})

    def test_filter_tools(self):
        self.assertIs(brain_mod.filter_tools(None), brain_mod.TOOLS)
        self.assertEqual([t["function"]["name"] for t in brain_mod.filter_tools({"web_search"})], ["web_search"])

    def test_allowed_tools_for(self):
        self.assertIsNone(brain_mod.JarvisBrain.allowed_tools_for("hud"))
        self.assertIsNone(brain_mod.JarvisBrain.allowed_tools_for("task"))
        self.assertNotIn("paste_text", brain_mod.JarvisBrain.allowed_tools_for("phone"))
        self.assertEqual(brain_mod.JarvisBrain.allowed_tools_for("task", ["a"]), {"a"})


class PhoneToolGateTest(BrainTestBase):
    def test_phone_payload_hides_blocked_tools(self):
        with self.patch_post([FakeResponse({"content": "tamam"})]):
            self.brain.ask("selam", session_id="phone-a", source="phone")
        names = tool_names(self.posts[0]["payload"])
        for name in BLOCKED:
            self.assertNotIn(name, names)
        self.assertIn("web_search", names)

    def test_hud_payload_has_all_tools(self):
        with self.patch_post([FakeResponse({"content": "tamam"})]):
            self.brain.ask("selam")
        self.assertEqual(tool_names(self.posts[0]["payload"]), set(TOOL_NAMES))

    def test_blocked_tool_not_executed_from_phone(self):
        for name in BLOCKED:
            self.brain.tools.execute.reset_mock()
            with self.patch_post([FakeResponse({"content": "", "tool_calls": call(name, '{"text": "x"}')}),
                                  FakeResponse({"content": "PC'den yapmalısınız."})]), \
                 mock.patch("builtins.print"):
                reply = self.brain.ask("yapıştır", session_id="phone-a", source="phone")
            self.assertEqual(reply, "PC'den yapmalısınız.")
            self.brain.tools.execute.assert_not_called()
            tool_msg = [m for m in self.posts[-1]["payload"]["messages"] if m.get("role") == "tool"][0]
            self.assertIn("ARAÇ_ENGELLİ", tool_msg["content"])
            self.assertIn(name, tool_msg["content"])

    def test_blocked_tool_runs_from_hud(self):
        with self.patch_post([FakeResponse({"content": "", "tool_calls": call("paste_text", '{"text": "x"}')}),
                              FakeResponse({"content": "Yapıştırdım."})]):
            self.brain.ask("yapıştır")
        self.brain.tools.execute.assert_called_once_with("paste_text", {"text": "x"})

    def test_config_can_enable_tool_for_phone(self):
        values = {"PHONE_ALLOWED_TOOLS": ["paste_text"]}
        with mock.patch.object(brain_mod, "_cfg", side_effect=lambda n, d=None: values.get(n, d)), \
             self.patch_post([FakeResponse({"content": "", "tool_calls": call("paste_text", '{"text": "x"}')}),
                              FakeResponse({"content": "ok"})]):
            self.brain.ask("yapıştır", session_id="phone-a", source="phone")
        self.brain.tools.execute.assert_called_once_with("paste_text", {"text": "x"})

    def test_stream_also_blocked(self):
        lines = stream_lines(tool_calls=call("close_application", '{"app": "chrome"}'))
        with self.patch_post([FakeResponse(lines=lines), FakeResponse(lines=stream_lines("Olmaz."))]), \
             mock.patch("builtins.print"):
            events = list(self.brain.ask_stream("chrome'u kapat", session_id="phone-a", source="phone"))
        self.assertEqual(events[-1], {"type": "done", "reply": "Olmaz."})
        self.brain.tools.execute.assert_not_called()

    def test_background_task_from_phone_inherits_restriction(self):
        self.brain.tasks.submit.return_value = "t9"

        def execute(name, args):
            # Gerçek ToolManager gibi: run_background_task -> brain.run_background
            return self.brain.run_background(args.get("title", ""), args.get("prompt", ""))
        self.brain.tools.execute.side_effect = execute
        with self.patch_post([FakeResponse({"content": "", "tool_calls": call(
                "run_background_task", '{"title": "t", "prompt": "panoyu yapıştır"}')}),
                FakeResponse({"content": "başlattım"})]):
            self.brain.ask("arka planda yapıştır", session_id="phone-a", source="phone")
        kwargs = self.brain.tasks.submit.call_args.kwargs
        self.assertNotIn("paste_text", kwargs["allowed_tools"])
        self.assertIn("web_search", kwargs["allowed_tools"])
        # Görev çalışınca aynı kısıt uygulanır
        self.brain.tools.execute.side_effect = None
        self.brain.tools.execute.reset_mock()
        with self.patch_post([FakeResponse({"content": "", "tool_calls": call("paste_text", '{"text": "x"}')}),
                              FakeResponse({"content": "olmadı"})]), mock.patch("builtins.print"):
            self.brain._background_ask("panoyu yapıştır", task_id="t9", allowed_tools=kwargs["allowed_tools"])
        self.assertNotIn("paste_text", tool_names(self.posts[-2]["payload"]))
        self.brain.tools.execute.assert_not_called()

    def test_background_task_from_hud_unrestricted(self):
        self.brain.tasks.submit.return_value = "t1"
        self.brain.run_background("başlık", "iş")
        self.assertNotIn("allowed_tools", self.brain.tasks.submit.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
