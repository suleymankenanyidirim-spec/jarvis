"""v2.2: data/ yolları main.py'nin klasörüne göre çözülür (cwd'ye göre değil)."""
import os
import tempfile
import unittest
from pathlib import Path

from core import paths
from core.background import BackgroundTaskManager
from core.memory import MemoryStore

DESKTOP = Path(__file__).resolve().parents[1]


class _InOtherCwd(unittest.TestCase):
    """Testi main.py'den farklı bir çalışma klasöründe koşturur."""

    def setUp(self):
        self.old_cwd = os.getcwd()
        self.tmp = tempfile.TemporaryDirectory()
        os.chdir(self.tmp.name)

    def tearDown(self):
        os.chdir(self.old_cwd)
        self.tmp.cleanup()


class PathsTest(_InOtherCwd):
    def test_base_dir_is_main_py_folder(self):
        self.assertEqual(paths.BASE_DIR, DESKTOP)
        self.assertTrue((paths.BASE_DIR / "main.py").is_file())
        self.assertEqual(paths.DATA_DIR, DESKTOP / "data")

    def test_relative_resolves_against_base_not_cwd(self):
        self.assertEqual(paths.resolve("data/x.db"), DESKTOP / "data" / "x.db")
        self.assertNotEqual(paths.resolve("data/x.db"), Path(self.tmp.name) / "data" / "x.db")

    def test_absolute_kept(self):
        p = Path(self.tmp.name) / "a.db"
        self.assertEqual(paths.resolve(p), p)
        self.assertEqual(paths.resolve(str(p)), p)

    def test_custom_base(self):
        self.assertEqual(paths.resolve("data/a", base=self.tmp.name), Path(self.tmp.name) / "data" / "a")

    def test_data_path(self):
        self.assertEqual(paths.data_path("phone_token.txt"), DESKTOP / "data" / "phone_token.txt")
        self.assertEqual(paths.data_path("a", "b.json"), DESKTOP / "data" / "a" / "b.json")


class ModulesUseBaseDirTest(_InOtherCwd):
    def test_memory_default_path(self):
        # Varsayılan yol main.py'nin yanındaki data/ olmalı; cwd'de dosya oluşmamalı.
        # (Gerçek data/ klasörüne dokunmamak için yalnızca çözülen yolu kontrol ediyoruz.)
        self.assertEqual(paths.resolve("data/jarvis_memory.db"), DESKTOP / "data" / "jarvis_memory.db")
        mem = MemoryStore(Path(self.tmp.name) / "m.db")
        self.assertTrue(mem.db_path.is_absolute())

    def test_memory_relative_path_uses_base(self):
        rel = os.path.relpath(Path(self.tmp.name) / "sub" / "m.db", DESKTOP)
        mem = MemoryStore(rel)
        self.assertEqual(mem.db_path.resolve(), (Path(self.tmp.name) / "sub" / "m.db").resolve())
        self.assertTrue(mem.db_path.exists())
        self.assertFalse((Path(self.tmp.name) / rel).exists())

    def test_background_relative_path_uses_base(self):
        rel = os.path.relpath(Path(self.tmp.name) / "bg" / "tasks.json", DESKTOP)
        mgr = BackgroundTaskManager(max_workers=1, state_path=rel)
        try:
            self.assertEqual(mgr.state_path.resolve(), (Path(self.tmp.name) / "bg" / "tasks.json").resolve())
            self.assertTrue(mgr.state_path.parent.is_dir())
        finally:
            mgr.executor.shutdown(wait=False)

    def test_no_cwd_relative_data_literals_left(self):
        # Kaynakta Path("data/...") / open("data/...") gibi cwd'ye bağlı kullanım kalmamalı.
        offenders = []
        for py in list(DESKTOP.glob("core/*.py")) + list(DESKTOP.glob("interface/gpu_hud.py")) + [DESKTOP / "main.py"]:
            text = py.read_text(encoding="utf-8")
            for needle in ('Path("data/', "Path('data/", 'open("data/', "open('data/", '_SETTINGS_PATH = "data/'):
                if needle in text:
                    offenders.append(f"{py.name}: {needle}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
