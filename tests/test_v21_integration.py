"""v2.1: Self-Heal motorunun v1.4 masaüstü koduyla (desktop/) birlikte çalışması."""
import contextlib
import importlib.util
import io
import json
import tempfile
import time
import unittest
from pathlib import Path

from selfheal import detectors as D
from selfheal import patcher as P
from selfheal.config import PACKAGE_ROOT, Settings, default_target, load_settings
from selfheal.engine import Engine

DESKTOP = PACKAGE_ROOT / "desktop"


def load_background():
    spec = importlib.util.spec_from_file_location("jarvis_background_v21", DESKTOP / "core" / "background.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TargetTest(unittest.TestCase):
    def test_repo_layout_targets_desktop(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "desktop").mkdir()
            (Path(d) / "desktop" / "main.py").write_text("", encoding="utf-8")
            self.assertEqual(default_target(d), Path(d) / "desktop")

    def test_installed_package_targets_itself(self):
        # C:\JARVIS: main.py, run.py ve selfheal/ yan yana
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "main.py").write_text("", encoding="utf-8")
            self.assertEqual(default_target(d), Path(d))

    def test_default_settings_point_to_this_repos_desktop(self):
        with tempfile.TemporaryDirectory() as d:
            s = load_settings(Path(d) / "yok.json")
        self.assertEqual(s.target, DESKTOP.resolve())
        self.assertTrue((s.target / "main.py").is_file())

    def test_settings_file_overrides_target(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "ayar.json"
            p.write_text(json.dumps({"target_dir": d, "mode": "gozlem", "bilinmeyen": 1}), encoding="utf-8")
            s = load_settings(p)
            self.assertEqual(s.target, Path(d).resolve())
            self.assertEqual(s.mode, "gozlem")


class ProtectionTest(unittest.TestCase):
    def test_security_and_personal_files_are_protected(self):
        with tempfile.TemporaryDirectory() as d:
            s = Settings(target_dir=d, data_dir=d + "/sd")
            for path in ("config_local.py", "core/phone_server.py", "core/updater.py", "run.py",
                         "selfheal/engine.py", "tests/test_memory.py", "data/x.py"):
                with self.assertRaises(P.PatchError, msg=path):
                    P.parse_patch({"edits": [{"path": path, "search": "a", "replace": "b"}]}, Path(d), s)

    def test_selfheal_and_phone_ui_are_not_scanned(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for sub in ("selfheal", "phone_ui", "core"):
                (root / sub).mkdir()
                (root / sub / "x.py").write_text("def bozuk(:\n", encoding="utf-8")
            s = Settings(target_dir=d, data_dir=d + "/selfheal_data")
            found = {i.file for i in D.check_syntax(root, s.ignore_dirs)}
            self.assertEqual(found, {"core/x.py"})


class PathMappingTest(unittest.TestCase):
    def test_absolute_path_outside_target_is_not_matched(self):
        # Eskiden "C:/..." adayı hedef klasörün DIŞINDAKİ gerçek dosyaya eşleniyordu.
        outside = str(Path(__file__).resolve()).replace("/", "\\")
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(D.map_path(outside, Path(d)))

    def test_dotdot_is_not_matched(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "app"
            root.mkdir()
            (Path(d) / "gizli.py").write_text("", encoding="utf-8")
            self.assertIsNone(D.map_path("X:\\baska\\..\\gizli.py", root))

    def test_suffix_inside_target_is_matched(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "core").mkdir()
            (root / "core" / "tools.py").write_text("", encoding="utf-8")
            self.assertEqual(D.map_path("D:\\Eski\\JARVIS\\core\\tools.py", root), "core/tools.py")


class BackgroundTracebackTest(unittest.TestCase):
    def test_failed_background_task_becomes_fixable_issue(self):
        bg = load_background()
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "core").mkdir()
            (root / "core" / "hesap.py").write_text("def bol(a, b):\n    return a / b\n", encoding="utf-8")
            spec = importlib.util.spec_from_file_location("hesap_v21", root / "core" / "hesap.py")
            hesap = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(hesap)

            mgr = bg.BackgroundTaskManager(1, root / "data" / "background_tasks.json", max_retries=0,
                                           traceback_chars=6000)
            try:
                tid = mgr.submit("Bölme", hesap.bol, 1, 0)
                end = time.time() + 10
                while mgr.get(tid).get("status") != "failed" and time.time() < end:
                    time.sleep(0.05)
            finally:
                mgr.shutdown()

            issues = D.task_issues(root)
            self.assertEqual(len(issues), 1)
            issue = issues[0]
            self.assertTrue(issue.fixable)
            self.assertEqual(issue.file, "core/hesap.py")
            self.assertEqual(issue.fingerprint, "calisma:ZeroDivisionError:core/hesap.py:bol")
            self.assertIn("Bölme", issue.title)


class DesktopObserveTest(unittest.TestCase):
    def test_observe_cycle_on_real_desktop_has_no_failing_tests(self):
        with tempfile.TemporaryDirectory() as d:
            s = Settings(target_dir=str(DESKTOP), data_dir=d, mode="gozlem", check_ollama_health=False,
                         idle_test_generation=False)
            eng = Engine(s, llm=object(), log=lambda m: None)
            self.assertEqual(eng.cycle(), "bekleme")
            self.assertGreater(eng.last_scan["tests_ran"], 0)
            self.assertEqual(eng.last_scan["tests_failing"], 0)
            self.assertEqual(D.check_syntax(DESKTOP, s.ignore_dirs), [])
            self.assertTrue((Path(d) / "RAPOR.md").exists())


class RunScriptTest(unittest.TestCase):
    def test_durum_command_with_settings_file(self):
        import run
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "ayar.json"
            p.write_text(json.dumps({"data_dir": d, "mode": "gozlem"}), encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = run.main(["durum", "--ayarlar", str(p)])
            self.assertEqual(code, 0)
            self.assertIn("JARVIS Self-Heal Raporu", out.getvalue())

    def test_missing_target_returns_error(self):
        import run
        with tempfile.TemporaryDirectory() as d:
            with contextlib.redirect_stdout(io.StringIO()):
                code = run.main(["durum", "--hedef", str(Path(d) / "yok"), "--ayarlar", str(Path(d) / "a.json")])
            self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
