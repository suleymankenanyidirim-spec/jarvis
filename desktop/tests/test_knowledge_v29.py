"""v2.9: bilgi deposu proje klasörlerini salt okunur tarar; kod dosyaları, hassas dosyaların atlanması,
şifre değerlerinin gizlenmesi."""
import tempfile
import unittest
from pathlib import Path

from core import knowledge as K


class ExtraDirsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name)
        self.proj = t / "GameBoyS3"
        files = {
            "pins.h": "// Tuşlar\n#define PIN_UP 32   // YUKARI tuşu\n#define PIN_A 13   // ONAY",
            "GameBoyS3.ino": "void setup() {\n  // ekran ILI9341 başlatılır\n}\n",
            "kask.ino": 'const char* WIFI_PASS = "GizliSifre99";\nconst char* ssid = "EvAgi";\n// kask servo GPIO18',
            "wifi_ayarlar.h": '#define WIFI_SIFRE "CokGizli"',
            "config_local.py": 'ANTHROPIC_API_KEY = "sk-gizli"',
            "libraries/Adafruit/lib.h": "// kütüphane tuşlar",
            "data/jarvis_memory.db": "x",
            "eski/notlar.md": "Ekran parlaklığı GPIO26 ile ayarlanır.",
            "buyuk.h": "// tablo\n" + "0x00, " * 60000,
        }
        for rel, text in files.items():
            p = self.proj / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        self.kb = K.KnowledgeBase(t / "Bilgi", index_path=t / "i.db", refresh_seconds=0, extra_dirs=[self.proj])

    def test_code_and_docs_indexed_with_prefix(self):
        paths = {f["path"] for f in self.kb.list_files()}
        self.assertIn("@GameBoyS3/pins.h", paths)
        self.assertIn("@GameBoyS3/GameBoyS3.ino", paths)
        self.assertIn("@GameBoyS3/eski/notlar.md", paths)
        hit = self.kb.search("yukarı tuşu hangi pin")[0]
        self.assertEqual(hit["path"], "@GameBoyS3/pins.h")
        self.assertIn("PIN_UP 32", hit["text"])

    def test_sensitive_files_and_dirs_skipped(self):
        paths = {f["path"] for f in self.kb.list_files()}
        for bad in ("wifi_ayarlar.h", "config_local.py", "libraries/Adafruit/lib.h", "data/jarvis_memory.db"):
            self.assertNotIn("@GameBoyS3/" + bad, paths, bad)

    def test_secret_values_masked(self):
        hit = self.kb.search("kask servo wifi")[0]
        self.assertIn('WIFI_PASS = "***"', hit["text"])
        self.assertNotIn("GizliSifre99", hit["text"])
        self.assertIn('ssid = "EvAgi"', hit["text"])          # yalnızca şifre gizlenir

    def test_huge_generated_code_not_read(self):
        f = next(f for f in self.kb.list_files() if f["path"] == "@GameBoyS3/buyuk.h")
        self.assertEqual(f["chunks"], 0)

    def test_own_notes_not_masked(self):
        self.kb.add_note('Modem şifresi = "kirmizi-kedi"')
        self.assertIn("kirmizi-kedi", self.kb.search("modem şifresi")[0]["text"])

    def test_from_config_extra_dirs(self):
        cfg = {"KNOWLEDGE_DIR": self.tmp.name + "/Bilgi2", "KNOWLEDGE_EXTRA_DIRS": f"{self.proj};"}
        kb = K.from_config(lambda n, d=None: cfg.get(n, d))
        self.assertEqual(kb.extra_dirs, [self.proj])


if __name__ == "__main__":
    unittest.main()
