import tempfile
import unittest
from pathlib import Path

from core.memory import MemoryStore


class MemoryStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mem = MemoryStore(Path(self.tmp.name) / "m.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_save_and_dedupe(self):
        self.assertTrue(self.mem.save_memory("Kullanıcının adı Sky"))
        self.assertFalse(self.mem.save_memory("Kullanıcının adı Sky"))
        self.assertFalse(self.mem.save_memory("   "))

    def test_relevant_memories_scoring(self):
        self.mem.save_memory("En sevdiği renk mavi")
        self.mem.save_memory("Kaskın IP adresi 192.168.1.42")
        self.assertEqual(self.mem.relevant_memories("renk nedir")[0], "En sevdiği renk mavi")

    def test_forget(self):
        self.mem.save_memory("Geçici not")
        self.assertEqual(self.mem.forget_memory("Geçici"), 1)
        self.assertNotIn("Geçici not", self.mem.relevant_memories("Geçici"))

    def test_connections_are_closed(self):
        # v2.1: açık kalan bağlantı Windows'ta veritabanı dosyasını kilitliyordu.
        self.mem.save_memory("not")
        self.mem.forget_memory("not")
        self.mem.save_message("user", "selam")
        self.mem.relevant_memories("not")
        self.mem.recent_messages()
        moved = Path(self.tmp.name) / "tasindi.db"
        self.mem.db_path.replace(moved)   # dosya kilitliyse Windows'ta PermissionError
        moved.unlink()

    def test_data_survives_new_instance(self):
        self.mem.save_memory("Kalıcı bilgi")
        again = MemoryStore(self.mem.db_path)
        self.assertIn("Kalıcı bilgi", again.relevant_memories("Kalıcı"))

    def test_recent_messages_order(self):
        for i in range(3):
            self.mem.save_message("user", f"mesaj {i}")
        self.assertEqual([c for _, c in self.mem.recent_messages(2)], ["mesaj 1", "mesaj 2"])


if __name__ == "__main__":
    unittest.main()
