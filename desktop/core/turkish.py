"""Türkçe metin yardımcıları (v2.4): küçük harf, normalleştirme, basit kök bulma.

Neden embedding değil de kök eşleme?
- Ollama kilidi (OLLAMA_MAX_CONCURRENT=1) yüzünden her soruda bir embedding çağrısı sohbet
  üretimiyle aynı sıraya girer ve her cevabı geciktirirdi.
- Ayrı bir embedding modeli indirmek gerekirdi; Ollama kapalıyken hafıza araması da çalışmazdı.
- Hafıza birkaç düzine kısa cümle; ek ayıklama anında, çevrimdışı, deterministik ve test edilebilir.

Kök bulucu tam bir morfolojik çözümleyici değildir: sondaki yaygın çekim eklerini (çoğul, iyelik,
hâl, bildirme, -li) en fazla 3 kez soyar, kök en az 3 harf kalır, sonra sondaki b/c/d/g/ğ
yumuşamasını geri alır (rengi -> renk, kitabı -> kitap, ağacı -> ağaç). Aynı kurallar hem soruya
hem hafızaya uygulandığı için aşırı kısaltma eşleşmeyi bozmaz.
"""
import re
from functools import lru_cache

WORD_RE = re.compile(r"[^\W\d_]+|\d+", re.UNICODE)

SUFFIXES = sorted({
    "lerimiz", "larımız", "leriniz", "larınız",
    "leri", "ları", "imiz", "ımız", "umuz", "ümüz", "iniz", "ınız", "unuz", "ünüz",
    "daki", "deki", "taki", "teki",
    "ler", "lar", "dan", "den", "tan", "ten", "nın", "nin", "nun", "nün",
    "yla", "yle", "dır", "dir", "dur", "dür", "tır", "tir", "tur", "tür",
    "yı", "yi", "yu", "yü", "ya", "ye", "sı", "si", "su", "sü",
    "da", "de", "ta", "te", "ın", "in", "un", "ün", "ım", "im", "um", "üm",
    "la", "le", "lı", "li", "lu", "lü", "ki",
    "ı", "i", "u", "ü", "a", "e", "m",
}, key=len, reverse=True)

SOFTEN = {"b": "p", "c": "ç", "d": "t", "g": "k", "ğ": "k"}

STOPWORDS = {
    "acaba", "ama", "bana", "bende", "beni", "benim", "bize", "bizim", "bunu", "bunlar", "çok",
    "daha", "değil", "gibi", "hangi", "hem", "için", "ile", "kadar", "nasıl", "neden", "nedir",
    "nerede", "neydi", "olan", "olarak", "sana", "seni", "senin", "şey", "şu", "şunu", "var",
    "veya", "yok", "zaten", "hatırla", "hatırlıyor", "musun", "misin", "mısın", "müsün",
}
STOPWORDS = {w.replace("ı", "i") for w in STOPWORDS}


def tr_lower(text):
    """Türkçe küçük harf: I -> ı, İ -> i."""
    return str(text or "").replace("I", "ı").replace("İ", "i").lower()


def fold(text):
    """Türkçe küçük harf + ı/i aynı sayılır ("IP" -> "ıp" ile "ip" eşleşsin; Türkçe klavyesiz yazım)."""
    return tr_lower(text).replace("ı", "i")


def normalize(text):
    """Tekrar kontrolü için: boşluklar sadeleşir, katlanmış küçük harf, sondaki . ! noktalaması atılır."""
    text = " ".join(str(text or "").split())
    return fold(text).rstrip(" .!").strip()


def _soften(w):
    return w[:-1] + SOFTEN[w[-1]] if len(w) >= 3 and w[-1] in SOFTEN else w


def _chain(word):
    w = fold(word)
    out = [w]
    if w.isdigit():
        return out
    for _ in range(3):
        for suffix in SUFFIXES:
            if w.endswith(suffix) and len(w) - len(suffix) >= 3:
                w = w[: -len(suffix)]
                out.append(w)
                break
        else:
            break
    return out


@lru_cache(maxsize=4096)
def stem(word):
    """En kısa (en çok soyulmuş) kök, yumuşama geri alınmış."""
    return _soften(_chain(word)[-1])


@lru_cache(maxsize=4096)
def stem_forms(word):
    """Soyma zincirindeki tüm biçimler + yumuşaması geri alınmış halleri.

    Tek kök yerine aday kümesi kullanılır; böylece "adres" ile "adresi" (-> adres -> adr)
    ortak "adres" biçiminde buluşur, aşırı soyma eşleşmeyi bozmaz.
    """
    forms = set()
    for w in _chain(word):
        forms.add(w)
        forms.add(_soften(w))
    return frozenset(forms)


def words(text, min_len=3):
    return [w for w in WORD_RE.findall(fold(text)) if len(w) >= min_len]


def stems(text, drop_stopwords=True):
    return {stem(w) for w in words(text) if not (drop_stopwords and w in STOPWORDS)}


def stems_match(a, b):
    """Kökler aynıysa ya da uzun kökün biri ötekinin başıysa (istanbul / istanbullu)."""
    if a == b:
        return True
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    return len(short) >= 4 and long_.startswith(short)


def words_match(a, b):
    """İki kelimenin aday kökleri arasında eşleşen bir çift var mı?"""
    fa, fb = stem_forms(a), stem_forms(b)
    if fa & fb:
        return True
    return any(stems_match(x, y) for x in fa for y in fb)


def query_words(text):
    """Arama için anlamlı kelimeler (durak kelimeler atılır, tekrarsız, sıra korunur)."""
    seen, out = set(), []
    for w in words(text):
        if w not in STOPWORDS and w not in seen:
            seen.add(w)
            out.append(w)
    return out
