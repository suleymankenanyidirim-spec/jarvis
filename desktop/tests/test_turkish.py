"""v2.4: Türkçe küçük harf, normalleştirme ve ek ayıklayan kök eşleme."""
import unittest

from core import turkish as tr


class LowerNormalizeTest(unittest.TestCase):
    def test_turkish_lower(self):
        self.assertEqual(tr.tr_lower("IŞIK İzmir"), "ışık izmir")

    def test_normalize(self):
        self.assertEqual(tr.normalize("  Kullanıcının   adı  SKY. "), "kullanicinin adi sky")   # ı/i katlanır
        self.assertEqual(tr.normalize("Kask IP"), tr.normalize("kask ip"))
        self.assertEqual(tr.normalize("Doğum günüm 3 Mayıs!"), tr.normalize("doğum günüm 3 mayıs"))
        self.assertEqual(tr.normalize(""), "")
        self.assertEqual(tr.normalize(None), "")


class StemTest(unittest.TestCase):
    POSITIVE = [
        ("renk", "rengi"), ("renk", "renkli"), ("kitap", "kitabı"), ("kitap", "kitaplarımız"),
        ("ağaç", "ağacı"), ("kask", "kaskının"), ("araba", "arabam"), ("araba", "arabası"),
        ("istanbul", "istanbullu"), ("kullanıcı", "kullanıcının"), ("sevdiği", "sevdiğim"),
        ("mavi", "maviyi"), ("kedi", "kediler"), ("ışık", "ışığı"), ("köpek", "köpeğim"),
        ("adres", "adresi"), ("telefon", "telefonumun"), ("doğum", "doğumgünü"), ("Işık", "IŞIĞI"),
    ]
    NEGATIVE = [
        ("kedi", "kader"), ("mavi", "mavna"), ("renk", "rende"), ("kask", "kasa"),
        ("adres", "adalet"), ("saat", "sakat"), ("masa", "maske"), ("telefon", "televizyon"),
    ]

    def test_inflected_forms_match(self):
        for a, b in self.POSITIVE:
            self.assertTrue(tr.words_match(a, b), (a, b))
            self.assertTrue(tr.words_match(b, a), (b, a))

    def test_different_words_do_not_match(self):
        for a, b in self.NEGATIVE:
            self.assertFalse(tr.words_match(a, b), (a, b))

    def test_softening(self):
        self.assertEqual(tr.stem("rengi"), "renk")
        self.assertEqual(tr.stem("kitabı"), "kitap")
        self.assertEqual(tr.stem("ağacı"), "ağaç")

    def test_stem_keeps_minimum_length(self):
        for w in ("ev", "ad", "su", "masa", "kalem"):
            self.assertGreaterEqual(len(tr.stem(w)), min(3, len(w)))

    def test_numbers_untouched(self):
        self.assertEqual(tr.stem("2026"), "2026")
        self.assertTrue(tr.words_match("192", "192"))

    def test_query_words_drop_stopwords_and_duplicates(self):
        self.assertEqual(tr.query_words("Benim kaskın IP adresi nedir, kaskın?"), ["kaskin", "adresi"])

    def test_stems_match_prefix_rule(self):
        self.assertTrue(tr.stems_match("istanbul", "istanbullu"))
        self.assertFalse(tr.stems_match("kas", "kask"))      # 4 harften kısa önek sayılmaz


if __name__ == "__main__":
    unittest.main()
