"""v2.2: main.py --headless (HUD'sız: beyin + telefon sunucusu)."""
import io
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import main


class ParseArgsTest(unittest.TestCase):
    def test_default_is_hud(self):
        self.assertFalse(main.parse_args([]).headless)

    def test_headless_flag(self):
        self.assertTrue(main.parse_args(["--headless"]).headless)


class RunHeadlessTest(unittest.TestCase):
    def test_runs_until_stopped_without_hud(self):
        started = []
        stop = threading.Event()

        def fake_phone(brain):
            started.append(brain)
            stop.set()          # sunucu "başladı" -> döngüden hemen çık
            return object()

        out = io.StringIO()
        with redirect_stdout(out):
            code = main.run_headless("BEYIN", stop_event=stop, start_phone_fn=fake_phone)
        self.assertEqual(code, 0)
        self.assertEqual(started, ["BEYIN"])
        self.assertIn("HUD kapalı", out.getvalue())
        self.assertNotIn("interface.gpu_hud", sys.modules.keys() - _modules_before)

    def test_exits_1_when_phone_server_fails(self):
        with redirect_stdout(io.StringIO()):
            code = main.run_headless("B", stop_event=threading.Event(), start_phone_fn=lambda b: None)
        self.assertEqual(code, 1)

    def test_main_headless_never_imports_hud(self):
        fake_brain_mod = mock.MagicMock()
        with mock.patch.dict(sys.modules, {"core.brain": fake_brain_mod}), \
             mock.patch.object(main, "setup_headless_log", return_value=None), \
             mock.patch.object(main, "apply_pending_update"), \
             mock.patch.object(main, "start_update_watch"), \
             mock.patch.object(main, "run_headless", return_value=0) as rh, \
             redirect_stdout(io.StringIO()):
            sys.modules.pop("interface.gpu_hud", None)
            self.assertEqual(main.main(["--headless"]), 0)
        rh.assert_called_once()
        fake_brain_mod.JarvisBrain.assert_called_once()
        self.assertNotIn("interface.gpu_hud", sys.modules)


class HeadlessLogTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = (sys.stdout, sys.stderr)

    def tearDown(self):
        out, err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = self.old
        for s in (out, err):
            for inner in getattr(s, "streams", []):
                if inner not in self.old:
                    try:
                        inner.close()
                    except Exception:
                        pass
        self.tmp.cleanup()

    def test_tees_to_log_file(self):
        path = Path(self.tmp.name) / "logs" / "h.log"
        console = io.StringIO()
        sys.stdout = console
        result = main.setup_headless_log(str(path))
        print("merhaba")
        self.assertEqual(result, path)
        self.assertIn("merhaba", console.getvalue())
        self.assertIn("merhaba", path.read_text(encoding="utf-8"))

    def test_pythonw_without_console_does_not_crash(self):
        sys.stdout, sys.stderr = None, None
        path = Path(self.tmp.name) / "h.log"
        main.setup_headless_log(str(path))
        print("konsolsuz")
        sys.stderr.write("hata\n")
        text = path.read_text(encoding="utf-8")
        self.assertIn("konsolsuz", text)
        self.assertIn("hata", text)

    def test_no_log_and_no_console_falls_back_to_devnull(self):
        sys.stdout, sys.stderr = None, None
        self.assertIsNone(main.setup_headless_log(""))
        print("kaybolur")   # çökmemeli


_modules_before = set(sys.modules)

if __name__ == "__main__":
    unittest.main()
