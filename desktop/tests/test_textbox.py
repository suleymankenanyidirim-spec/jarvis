"""v2.8: HUD yazı kutusu düzenleme mantığı (pygame gerekmez)."""
import unittest

from interface.textbox import MAX_CHARS, TextBox


class TextBoxTest(unittest.TestCase):
    def test_turkish_and_altgr_chars(self):
        b = TextBox()
        for piece in ("Işık ", "ğüşöçİı ", "mail@ornek.com ", "€{}"):
            b.insert(piece)
        self.assertEqual(b.text, "Işık ğüşöçİı mail@ornek.com €{}")

    def test_cursor_editing(self):
        b = TextBox()
        b.insert("merhaba dünya")
        b.left(word=True)
        b.insert("güzel ")
        self.assertEqual(b.text, "merhaba güzel dünya")
        b.end()
        b.backspace(word=True)
        self.assertEqual(b.text, "merhaba güzel ")
        b.home()
        b.delete()
        self.assertEqual(b.text, "erhaba güzel ")
        b.right(word=True)
        self.assertEqual(b.cursor, len("erhaba"))
        b.backspace()
        self.assertEqual(b.text, "erhab güzel ")

    def test_paste_cleans_newlines_and_limits(self):
        b = TextBox()
        b.insert("satır1\r\nsatır2\tson\x07")
        self.assertEqual(b.text, "satır1 satır2 son")
        b.insert("x" * (MAX_CHARS + 50))
        self.assertEqual(len(b.text), MAX_CHARS)

    def test_submit_and_history(self):
        b = TextBox()
        b.insert("  ilk mesaj ")
        self.assertEqual(b.submit(), "ilk mesaj")
        self.assertEqual((b.text, b.cursor), ("", 0))
        b.insert("ikinci")
        b.submit()
        b.insert("taslak")
        b.history_prev()
        self.assertEqual(b.text, "ikinci")
        b.history_prev()
        b.history_prev()
        self.assertEqual(b.text, "ilk mesaj")
        b.history_next()
        b.history_next()
        self.assertEqual(b.text, "taslak")          # taslak geri gelir
        self.assertEqual(TextBox().submit(), "")

    def test_view_keeps_cursor_visible(self):
        b = TextBox()
        b.insert("a" * 50 + "SON")
        fits = lambda s: len(s) <= 10   # noqa: E731
        v = b.view(fits)
        self.assertTrue(v.endswith("SON|"))
        self.assertLessEqual(len(v), 10)
        b.home()
        self.assertTrue(b.view(fits).startswith("|a"))


if __name__ == "__main__":
    unittest.main()
