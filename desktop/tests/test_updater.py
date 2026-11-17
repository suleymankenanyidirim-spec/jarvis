import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from core.updater import SelfUpdater


def make_pkg(path, files):
    with zipfile.ZipFile(path, "w") as z:
        for name, content in files.items():
            z.writestr(name, content)
    return path


def ready(up, pkg, version="9"):
    up.state_file.write_text(json.dumps({"status": "ready", "package": str(pkg), "version": version}),
                             encoding="utf-8")


class UpdaterTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.d = Path(self._tmp.name)
        self.root = self.d / "app"
        self.root.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def test_no_pending_update(self):
        self.assertEqual(SelfUpdater(self.root).apply_pending(), "Güncelleme beklemiyor.")

    def test_zip_path_traversal_is_blocked(self):
        pkg = make_pkg(self.d / "u.zip", {"../evil.txt": "x"})
        up = SelfUpdater(self.root)
        ready(up, pkg)
        with self.assertRaises(ValueError):
            up.apply_pending()
        self.assertFalse((self.d / "evil.txt").exists())
        self.assertEqual(json.loads(up.state_file.read_text(encoding="utf-8"))["status"], "failed_apply")

    def test_apply_makes_backup(self):
        (self.root / "a.txt").write_text("eski", encoding="utf-8")
        pkg = make_pkg(self.d / "u.zip", {"a.txt": "yeni"})
        up = SelfUpdater(self.root)
        ready(up, pkg)
        up.apply_pending()
        self.assertEqual((self.root / "a.txt").read_text(encoding="utf-8"), "yeni")
        backups = list((self.root / "data").glob("backup_*/a.txt"))
        self.assertEqual(backups[0].read_text(encoding="utf-8"), "eski")

    # --- v1.4: kullanıcı dosyaları (data/, config_local.py) güncellemede asla ezilmez
    def test_data_and_config_local_are_never_overwritten(self):
        (self.root / "config_local.py").write_text("GITHUB_REPO = 'ben/jarvis'\n", encoding="utf-8")
        up = SelfUpdater(self.root)
        (self.root / "data" / "jarvis_memory.db").write_text("hafiza", encoding="utf-8")
        pkg = make_pkg(self.d / "u.zip", {
            "config_local.py": "GITHUB_REPO = ''\n",
            "data/jarvis_memory.db": "bos",
            "data\\phone_token.txt": "saldirgan",
            "core/yeni.py": "x = 1\n",
        })
        ready(up, pkg)
        up.apply_pending()
        self.assertEqual((self.root / "config_local.py").read_text(encoding="utf-8"), "GITHUB_REPO = 'ben/jarvis'\n")
        self.assertEqual((self.root / "data" / "jarvis_memory.db").read_text(encoding="utf-8"), "hafiza")
        self.assertFalse((self.root / "data" / "phone_token.txt").exists())
        self.assertTrue((self.root / "core" / "yeni.py").exists())

    # --- v2.1: masaüstü paketi artık selfheal/ ve run.py içeriyor
    def test_selfheal_files_in_package_are_installed(self):
        pkg = make_pkg(self.d / "u.zip", {"run.py": "print(1)\n", "selfheal/__init__.py": "",
                                          "BASLAT.bat": "@echo off\n"})
        up = SelfUpdater(self.root)
        ready(up, pkg, "2.1.0")
        self.assertIn("2.1.0", up.apply_pending())
        self.assertTrue((self.root / "selfheal" / "__init__.py").exists())
        self.assertTrue((self.root / "run.py").exists())

    def test_github_manifest_url(self):
        url = "https://github.com/ben/jarvis/releases/latest/download/manifest.json"
        self.assertEqual(SelfUpdater.github_manifest_url("ben/jarvis"), url)
        self.assertEqual(SelfUpdater.github_manifest_url("https://github.com/ben/jarvis/"), url)
        for bad in ("", None, "jarvis", "a/b/c"):
            self.assertEqual(SelfUpdater.github_manifest_url(bad), "")

    def test_version_compare(self):
        (self.root / "VERSION").write_text("2.1.0\n", encoding="utf-8")
        up = SelfUpdater(self.root)
        self.assertEqual(up.current_version(), "2.1.0")
        self.assertTrue(up.is_newer("v2.1.1"))
        self.assertTrue(up.is_newer("2.10.0"))
        self.assertFalse(up.is_newer("2.1.0"))
        self.assertFalse(up.is_newer("1.4.0"))
        self.assertEqual(SelfUpdater._vtuple("2.1"), (2, 1, 0, 0))

    def test_missing_version_file(self):
        self.assertEqual(SelfUpdater(self.root).current_version(), "0")

    def test_stage_skips_when_not_newer(self):
        (self.root / "VERSION").write_text("2.1.0", encoding="utf-8")
        up = SelfUpdater(self.root)
        manifest = {"version": "2.1.0", "zip_url": "https://ornek/x.zip", "sha256": "0" * 64}
        with mock.patch.object(SelfUpdater, "check", return_value=manifest), \
                mock.patch("core.updater.urlopen") as net:
            msg = up.stage("https://ornek/manifest.json")
        self.assertIn("güncel", msg)
        net.assert_not_called()


if __name__ == "__main__":
    unittest.main()
