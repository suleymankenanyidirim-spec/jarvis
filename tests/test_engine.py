"""Self-Heal motorunun kendi testleri. Çalıştır: python -m unittest discover -s tests -t ."""
import json
import tempfile
import textwrap
import unittest
from pathlib import Path

from selfheal.config import Settings
from selfheal.engine import Engine
from selfheal import patcher as P
from selfheal import detectors as D

BUGGY = "def topla(a, b):\n    return a - b\n\n\ndef carp(a, b):\n    return a * b\n"
TEST = textwrap.dedent('''
    import unittest
    from mathx import topla, carp

    class T(unittest.TestCase):
        def test_topla(self):
            self.assertEqual(topla(2, 3), 5)

        def test_carp(self):
            self.assertEqual(carp(2, 3), 6)
''')


class FakeLLM:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts = []

    def complete(self, prompt, system=""):
        self.prompts.append(prompt)
        return json.dumps(self.answers.pop(0)), "sahte"


def make_project(tmp, code=BUGGY):
    root = Path(tmp) / "app"
    (root / "tests").mkdir(parents=True)
    (root / "mathx.py").write_text(code, encoding="utf-8")
    (root / "tests" / "__init__.py").write_text("", encoding="utf-8")
    (root / "tests" / "test_mathx.py").write_text(TEST, encoding="utf-8")
    return root


def settings(tmp, root, **kw):
    s = Settings(target_dir=str(root), data_dir=str(Path(tmp) / "data"), check_ollama_health=False,
                 idle_test_generation=False, retry_backoff_minutes=0, **kw)
    return s


GOOD = {"explanation": "toplama düzeltildi",
        "edits": [{"path": "mathx.py", "search": "return a - b", "replace": "return a + b"}]}


class EngineTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.root = make_project(self.tmp)

    def tearDown(self):
        self._tmp.cleanup()

    def test_detects_and_fixes_failing_test(self):
        eng = Engine(settings(self.tmp, self.root), llm=FakeLLM(GOOD), log=lambda m: None)
        self.assertEqual(eng.cycle(), "duzeltildi")
        self.assertIn("a + b", (self.root / "mathx.py").read_text())
        row = eng.store.get("test:tests.test_mathx.T.test_topla")
        self.assertEqual(row["status"], "duzeltildi")
        self.assertTrue((Path(self.tmp) / "data" / "RAPOR.md").exists())

    def test_rejects_patch_that_breaks_other_test(self):
        bad = {"explanation": "", "edits": [
            {"path": "mathx.py", "search": "return a - b", "replace": "return a + b"},
            {"path": "mathx.py", "search": "return a * b", "replace": "return a + b"}]}
        eng = Engine(settings(self.tmp, self.root), llm=FakeLLM(bad), log=lambda m: None)
        self.assertEqual(eng.cycle(), "reddedildi")
        self.assertIn("a - b", (self.root / "mathx.py").read_text())   # canlı koda dokunulmadı
        att = eng.store.past_attempts("test:tests.test_mathx.T.test_topla")
        self.assertIn("test_carp", att[0]["reason"])

    def test_cannot_cheat_by_editing_tests(self):
        cheat = {"explanation": "", "edits": [
            {"path": "tests/test_mathx.py", "search": "topla(2, 3), 5", "replace": "topla(2, 3), -1"}]}
        eng = Engine(settings(self.tmp, self.root), llm=FakeLLM(cheat), log=lambda m: None)
        self.assertEqual(eng.cycle(), "reddedildi")
        self.assertIn("topla(2, 3), 5", (self.root / "tests" / "test_mathx.py").read_text())

    def test_learns_from_failed_attempt_and_gives_up(self):
        nonsense = {"explanation": "", "edits": [{"path": "mathx.py", "search": "YOK", "replace": "x"}]}
        llm = FakeLLM(nonsense, nonsense)
        eng = Engine(settings(self.tmp, self.root, max_attempts_per_issue=2), llm=llm, log=lambda m: None)
        eng.cycle()
        eng.cycle()
        self.assertIn("ÖNCEKİ BAŞARISIZ DENEMELER", llm.prompts[1])
        self.assertEqual(eng.store.get("test:tests.test_mathx.T.test_topla")["status"], "insan_gerekli")

    def test_proposal_mode_does_not_touch_code(self):
        eng = Engine(settings(self.tmp, self.root, mode="oneri"), llm=FakeLLM(GOOD), log=lambda m: None)
        self.assertEqual(eng.cycle(), "oneri")
        self.assertIn("a - b", (self.root / "mathx.py").read_text())
        proposal = next((Path(self.tmp) / "data" / "oneriler").glob("*.json"))
        ok, _ = eng.apply_proposal(proposal)
        self.assertTrue(ok)
        self.assertIn("a + b", (self.root / "mathx.py").read_text())

    def test_observe_mode_never_calls_llm(self):
        llm = FakeLLM()
        eng = Engine(settings(self.tmp, self.root, mode="gozlem"), llm=llm, log=lambda m: None)
        self.assertEqual(eng.cycle(), "bekleme")
        self.assertEqual(llm.prompts, [])

    def test_rollback_restores_original(self):
        eng = Engine(settings(self.tmp, self.root), llm=FakeLLM(GOOD), log=lambda m: None)
        eng.cycle()
        eng.rollback(reason="test")
        self.assertIn("a - b", (self.root / "mathx.py").read_text())

    def test_crash_during_probation_rolls_back(self):
        eng = Engine(settings(self.tmp, self.root), llm=FakeLLM(GOOD), log=lambda m: None)
        eng.cycle()
        self.assertIsNotNone(eng.on_app_crash())
        self.assertIn("a - b", (self.root / "mathx.py").read_text())

    def test_syntax_error_is_found_and_fixed(self):
        (self.root / "mathx.py").write_text(BUGGY.replace("def carp(a, b):", "def carp(a, b)"), encoding="utf-8")
        fix = {"explanation": "", "edits": [
            {"path": "mathx.py", "search": "def carp(a, b)\n", "replace": "def carp(a, b):\n"},
            {"path": "mathx.py", "search": "return a - b", "replace": "return a + b"}]}
        eng = Engine(settings(self.tmp, self.root), llm=FakeLLM(fix), log=lambda m: None)
        self.assertEqual(eng.cycle(), "duzeltildi")
        self.assertEqual(eng.store.get("sozdizimi:mathx.py")["status"], "duzeltildi")

    def test_runtime_error_from_log_requires_repro_test(self):
        self.root = make_project(self.tmp + "/b", BUGGY.replace("return a - b", "return a + b") +
                                 "\n\ndef bol(a, b):\n    return a / b\n")
        s = settings(self.tmp + "/b", self.root)
        (s.data / "logs").mkdir(parents=True, exist_ok=True)
        (s.data / "logs" / "app.log").write_text(
            "Traceback (most recent call last):\n"
            f'  File "C:\\JARVIS\\mathx.py", line 10, in bol\n    return a / b\n'
            "ZeroDivisionError: division by zero\n", encoding="utf-8")
        no_test = {"explanation": "", "edits": [
            {"path": "mathx.py", "search": "return a / b", "replace": "return a / b if b else 0"}]}
        with_test = dict(no_test, new_files=[{"path": "tests/auto/test_auto_bol.py", "content":
            "import unittest\nfrom mathx import bol\n\nclass B(unittest.TestCase):\n"
            "    def test_sifir(self):\n        self.assertEqual(bol(1, 0), 0)\n"}])
        eng = Engine(s, llm=FakeLLM(no_test, with_test), log=lambda m: None)
        self.assertEqual(eng.cycle(), "reddedildi")
        self.assertEqual(eng.cycle(), "duzeltildi")
        self.assertTrue((self.root / "tests" / "auto" / "test_auto_bol.py").exists())


class PatcherTest(unittest.TestCase):
    def test_dangerous_additions(self):
        self.assertEqual(P.dangerous_additions("x = 1", "import subprocess\nsubprocess.run('x')"), ["subprocess"])
        self.assertEqual(P.dangerous_additions("import subprocess", "import subprocess\nx = 2"), [])

    def test_path_escape_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            s = Settings(target_dir=d, data_dir=d + "/data")
            for bad in ("../x.py", "/etc/x.py", "C:/x.py"):
                with self.assertRaises(P.PatchError):
                    P.parse_patch({"edits": [{"path": bad, "search": "a", "replace": "b"}]}, Path(d), s)


class DetectorTest(unittest.TestCase):
    def test_windows_traceback_mapping(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "core").mkdir()
            (root / "core" / "brain.py").write_text("x=1\n")
            block = ('Traceback (most recent call last):\n'
                     '  File "C:\\JARVIS\\core\\brain.py", line 12, in ask\n    x\n'
                     'KeyError: \'message\'\n')
            issue = D.traceback_issue(block, root, "log")
            self.assertEqual(issue.file, "core/brain.py")
            self.assertEqual(issue.fingerprint, "calisma:KeyError:core/brain.py:ask")


if __name__ == "__main__":
    unittest.main()
