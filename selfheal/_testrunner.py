"""Ayrı süreçte çalışan test koşucusu. Kullanım: python -B _testrunner.py <kök> [desen]
Sonucu ===SELFHEAL_JSON=== işaretinden sonra JSON olarak yazar."""
import io
import json
import os
import sys
import traceback
import unittest


def main():
    root = os.path.abspath(sys.argv[1])
    pattern = sys.argv[2] if len(sys.argv) > 2 else "test*.py"
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != here]  # selfheal/config.py karışmasın
    os.chdir(root)
    sys.path.insert(0, root)
    out = {"ran": 0, "failures": [], "passed": []}
    tests_dir = os.path.join(root, "tests")
    if not os.path.isdir(tests_dir):
        print("===SELFHEAL_JSON===" + json.dumps(out))
        return

    class Result(unittest.TextTestResult):
        def addSuccess(self, test):
            super().addSuccess(test)
            out["passed"].append(test.id())

    try:
        suite = unittest.defaultTestLoader.discover(tests_dir, pattern=pattern, top_level_dir=root)
        stream = io.StringIO()
        runner = unittest.TextTestRunner(stream=stream, verbosity=0, resultclass=Result)
        result = runner.run(suite)
        out["ran"] = result.testsRun
        for kind, items in (("failure", result.failures), ("error", result.errors)):
            for test, tb in items:
                out["failures"].append({"id": test.id(), "kind": kind, "trace": tb[-6000:]})
    except Exception:
        out["failures"].append({"id": "<test-yukleme>", "kind": "error", "trace": traceback.format_exc()[-6000:]})
    print("===SELFHEAL_JSON===" + json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
