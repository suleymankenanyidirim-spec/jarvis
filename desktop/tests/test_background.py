import tempfile
import time
import unittest
from pathlib import Path

from core.background import BackgroundTaskManager


def wait_done(mgr, tid, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if mgr.get(tid).get("status") in ("completed", "failed"):
            return mgr.get(tid)
        time.sleep(0.05)
    raise AssertionError("görev bitmedi")


class BackgroundTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mgr = BackgroundTaskManager(2, Path(self.tmp.name) / "t.json", max_retries=0)

    def tearDown(self):
        self.mgr.shutdown()
        self.tmp.cleanup()

    def test_success(self):
        tid = self.mgr.submit("topla", lambda a, b: a + b, 2, 3)
        task = wait_done(self.mgr, tid)
        self.assertEqual(task["status"], "completed")
        self.assertEqual(task["result"], "5")

    def test_failure_keeps_traceback(self):
        def boom():
            raise KeyError("yok")
        task = wait_done(self.mgr, self.mgr.submit("patla", boom))
        self.assertEqual(task["status"], "failed")
        self.assertIn("KeyError", task.get("traceback", ""))

    def test_traceback_points_to_failing_function(self):
        def bozuk_adim():
            return {}["message"]
        task = wait_done(self.mgr, self.mgr.submit("adim", bozuk_adim))
        self.assertIn("Traceback (most recent call last)", task["traceback"])
        self.assertIn("bozuk_adim", task["traceback"])
        self.assertEqual(task["error"], "'message'")

    def test_traceback_is_truncated_and_can_be_disabled(self):
        def uzun():
            raise ValueError("x" * 5000)
        with tempfile.TemporaryDirectory() as d:
            for limit in (100, 0):
                mgr = BackgroundTaskManager(1, Path(d) / f"t{limit}.json", max_retries=0, traceback_chars=limit)
                try:
                    task = wait_done(mgr, mgr.submit("uzun", uzun))
                finally:
                    mgr.shutdown()
                self.assertEqual(task["status"], "failed")
                self.assertLessEqual(len(task["traceback"]), limit)

    def test_default_limit_comes_from_config(self):
        import config
        expected = max(0, int(getattr(config, "BACKGROUND_TRACEBACK_CHARS", 6000)))
        self.assertEqual(self.mgr.traceback_chars, expected)

    def test_traceback_is_persisted_for_selfheal(self):
        import json

        def boom():
            raise RuntimeError("diske yazılmalı")
        tid = self.mgr.submit("kalici", boom)
        wait_done(self.mgr, tid)
        saved = json.loads((Path(self.tmp.name) / "t.json").read_text(encoding="utf-8"))
        self.assertIn("RuntimeError: diske yazılmalı", saved[tid]["traceback"])

    def test_success_has_no_traceback(self):
        task = wait_done(self.mgr, self.mgr.submit("iyi", lambda: "tamam"))
        self.assertNotIn("traceback", task)

    def test_progress_math(self):
        tid = self.mgr.submit("bekle", lambda: time.sleep(0.2))
        self.mgr.set_progress(tid, 1, 4)
        self.assertEqual(self.mgr.get(tid)["progress"], 25)
        wait_done(self.mgr, tid)


if __name__ == "__main__":
    unittest.main()
