"""Görsel kartlar (v3.0) — HUD'un cevaba göre açtığı pencerelerin saf (test edilebilir) mantığı.

Kart biçimi: {"type": "weather" | "keyboard" | "helmet" | "home" | "info", "side": "center" | "left", "data": {...}}
- Ana görsel (küre/halka) kart açılınca sağa kayar ve küçülür; konuşma bitince kart kaybolur, görsel ortaya döner.
- Bu modül çizim yapmaz; HUD (interface/cards.py) çizer.
"""
import re

from core import turkish

# ------------------------------------------------------------------ klavye kısayolları
KEY_ALIASES = {
    "ctrl": "CTRL", "control": "CTRL", "kontrol": "CTRL", "ktrl": "CTRL",
    "alt": "ALT", "altgr": "ALTGR", "shift": "SHIFT", "şift": "SHIFT", "üst karakter": "SHIFT",
    "win": "WIN", "windows": "WIN", "tab": "TAB", "enter": "ENTER", "giriş": "ENTER",
    "esc": "ESC", "escape": "ESC", "delete": "DEL", "del": "DEL", "sil": "DEL",
    "backspace": "BACKSPACE", "geri tuşu": "BACKSPACE", "space": "SPACE", "boşluk": "SPACE",
    "home": "HOME", "end": "END", "insert": "INS", "print screen": "PRTSC", "prtsc": "PRTSC",
    "caps lock": "CAPS", "capslock": "CAPS",
}
MODIFIERS = {"CTRL", "ALT", "ALTGR", "SHIFT", "WIN"}
_MOD_WORDS = r"ctrl|control|kontrol|ktrl|alt\s?gr|alt|shift|şift|win(?:dows)?"
_KEY_WORDS = (r"f(?:1[0-2]|[1-9])|tab|enter|esc(?:ape)?|delete|del|backspace|space|boşluk|home|end|insert|"
              r"print\s?screen|prtsc|caps\s?lock|[a-zçğıöşü0-9]")
_SEP = r"\s*(?:\+|-|ve|ile|tuşu\s+ve|tuşuyla|tuşları|tuşu)?\s*"
# Ör. "Ctrl+C", "Ctrl ve C", "CTRL + SHIFT + ESC", "Alt F4", "Windows tuşu ve E"
COMBO_RE = re.compile(
    rf"(?<![\w])((?:(?:{_MOD_WORDS})(?:\s*tuşu)?{_SEP}){{1,3}})({_KEY_WORDS})(?![\wçğıöşü])",
    re.I)


def _key_name(word):
    w = turkish.tr_lower(" ".join(str(word).split()))
    w = w.replace("altgr", "altgr").replace("alt gr", "altgr").replace("printscreen", "print screen")
    if w in KEY_ALIASES:
        return KEY_ALIASES[w]
    if re.fullmatch(r"f(1[0-2]|[1-9])", w):
        return w.upper()
    if len(w) == 1:
        return w.replace("i", "İ").upper() if w == "i" else w.upper()
    return w.upper()


def detect_key_combos(text):
    """Metindeki kısayollar: [{"keys": ["CTRL", "C"], "label": "CTRL + C", "start", "end"}] (metindeki sırayla)."""
    out = []
    for m in COMBO_RE.finditer(str(text or "")):
        mods = re.findall(_MOD_WORDS, m.group(1), re.I)
        keys = []
        for w in mods:
            k = _key_name(w)
            if k not in keys:
                keys.append(k)
        last = _key_name(m.group(2))
        if last not in keys:
            keys.append(last)
        if len(keys) < 2:
            continue
        out.append({"keys": keys, "label": " + ".join(keys), "start": m.start(), "end": m.end()})
    return out


def active_combo(combos, pos):
    """Konuşmanın şu anki karakter konumunda yanması gereken kısayol (son söylenmiş olan); yoksa None."""
    best = None
    for c in combos:
        if c["start"] <= pos + 2:
            best = c
    return best


def keyboard_visual(text):
    combos = detect_key_combos(text)
    if not combos:
        return None
    return {"type": "keyboard", "side": "center", "data": {"combos": combos}}


# ------------------------------------------------------------------ hava durumu
WEATHER_RE = re.compile(r"hava\s*(durumu|nasıl|nasil|kaç|kac|ne\s*durumda|sıcak|soğuk)|kaç\s*derece|"
                        r"yağmur\s*yağ|kar\s*yağ|yağış|şemsiye|sıcaklık|nem\b|rüzgar")
WMO = {   # Open-Meteo / WMO hava kodu -> (simge, Türkçe)
    0: ("sun", "Açık"), 1: ("sun", "Çoğunlukla açık"), 2: ("partly", "Parçalı bulutlu"), 3: ("cloud", "Kapalı"),
    45: ("fog", "Sisli"), 48: ("fog", "Kırağılı sis"),
    51: ("drizzle", "Hafif çisenti"), 53: ("drizzle", "Çisenti"), 55: ("drizzle", "Yoğun çisenti"),
    56: ("drizzle", "Dondurucu çisenti"), 57: ("drizzle", "Dondurucu çisenti"),
    61: ("rain", "Hafif yağmur"), 63: ("rain", "Yağmur"), 65: ("rain", "Kuvvetli yağmur"),
    66: ("rain", "Dondurucu yağmur"), 67: ("rain", "Dondurucu yağmur"),
    71: ("snow", "Hafif kar"), 73: ("snow", "Kar"), 75: ("snow", "Yoğun kar"), 77: ("snow", "Kar taneleri"),
    80: ("rain", "Sağanak"), 81: ("rain", "Kuvvetli sağanak"), 82: ("rain", "Şiddetli sağanak"),
    85: ("snow", "Kar sağanağı"), 86: ("snow", "Yoğun kar sağanağı"),
    95: ("storm", "Gök gürültülü fırtına"), 96: ("storm", "Dolu ile fırtına"), 99: ("storm", "Şiddetli dolu fırtınası"),
}


def is_weather_question(text):
    return bool(WEATHER_RE.search(turkish.tr_lower(text)))


def weather_icon(code):
    return WMO.get(int(code) if code is not None else -1, ("cloud", "Bilinmiyor"))


_LOC_STOP = {"hava", "durumu", "bugün", "yarın", "şu", "an", "nasıl", "kaç", "derece", "jarvis", "acaba", "orada",
             "burada", "bizim", "benim", "bu", "hafta", "akşam", "sabah"}


def extract_location(text):
    """'Ankara'da hava nasıl' -> 'Ankara'. Bulamazsa ''. (Yer adı yazımı: büyük harfle ya da 'X'te/'X'de' ekiyle.)"""
    t = str(text or "")

    def clean(name):
        words = [w for w in name.split() if turkish.tr_lower(w) not in _LOC_STOP]
        return " ".join(words)

    m = re.search(r"([A-ZÇĞİÖŞÜa-zçğıöşü][\wçğıöşü]+(?:\s[A-ZÇĞİÖŞÜ][\wçğıöşü]+)?)['’](?:da|de|ta|te|nda|nde)\b", t)
    if m and clean(m.group(1)):
        return clean(m.group(1))
    for m in re.finditer(r"\b([A-ZÇĞİÖŞÜ][a-zçğıöşü]{2,}(?:\s[A-ZÇĞİÖŞÜ][a-zçğıöşü]{2,})?)\b", t):
        name = clean(m.group(1))
        if name and name.lower() != "hava":
            return name
    return ""


# ------------------------------------------------------------------ bilgi (tarih, kişi, yer…)
INFO_RE = re.compile(r"\b(kimdir|kim\s*idi|nedir|ne\s*demek|hakkında|tarihi|tarihçesi|anlat|bilgi\s*ver|"
                     r"ne\s*zaman\s*(?:yapıldı|kuruldu|doğdu|öldü|yaşadı)|nerede\s*(?:doğdu|yaşadı))\b")
_TOPIC_DROP = re.compile(r"\b(bana|biraz|kısaca|lütfen|jarvis|bilgi\s*ver|hakkında|anlat(?:ır\s*mısın|sana)?|"
                         r"nedir|kimdir|kim\s*idi|ne\s*demek|tarihi|tarihçesi|nın|nin|nun|nün|mısın|misin)\b", re.I)


def is_info_question(text):
    low = turkish.tr_lower(text)
    return bool(INFO_RE.search(low)) and not is_weather_question(text)


def info_topic(text):
    """'Fatih Sultan Mehmet kimdir' -> 'Fatih Sultan Mehmet'."""
    t = re.sub(r"[?!.,]", " ", str(text or ""))
    t = re.sub(r"['’](?:nın|nin|nun|nün|ın|in|un|ün|ya|ye|a|e|da|de|ta|te)\b", "", t)
    t = _TOPIC_DROP.sub(" ", t)
    return " ".join(t.split()).strip()


# ------------------------------------------------------------------ kask
def helmet_state(result_text):
    """Kask aracının cevabından durum: 'open' | 'closed' | 'unknown'."""
    low = turkish.tr_lower(result_text)
    if re.search(r"kapalı|kapanıyor|kapandı|closed|kapat", low):
        return "closed"
    if re.search(r"açık|açılıyor|açıldı|open|\baç\b", low):
        return "open"
    return "unknown"


def helmet_visual(result_text):
    return {"type": "helmet", "side": "center", "data": {"state": helmet_state(result_text), "text": str(result_text)[:120]}}


# ------------------------------------------------------------------ resimden çizim (bilgi penceresi)
def image_to_strokes(image_bytes, size=200, max_segments=2600):
    """Resmi mavi-siyah temaya uyan "çizim"e çevirir: kenar piksellerini birleştiren kısa çizgiler.

    Dönüş: [(x0, y0, x1, y1), ...] 0..1 aralığında, çizim sırasına göre (yukarıdan aşağı, ortadan dışa).
    Pillow/numpy yoksa ya da resim okunamazsa None.
    """
    try:
        import io
        import numpy as np
        from PIL import Image, ImageFilter, ImageOps
    except ImportError:
        return None
    try:
        im = Image.open(io.BytesIO(image_bytes)).convert("L")
    except Exception:
        return None
    im.thumbnail((size, size))
    im = ImageOps.autocontrast(im)
    edges = np.asarray(im.filter(ImageFilter.FIND_EDGES), dtype=np.float32)
    if not edges.size:
        return None
    thr = max(40.0, float(np.percentile(edges, 88)))
    mask = edges >= thr
    mask[0, :] = mask[-1, :] = False
    mask[:, 0] = mask[:, -1] = False
    h, w = mask.shape
    segs = []
    ys, xs = np.nonzero(mask)
    for dy, dx in ((0, 1), (1, 0), (1, 1), (1, -1)):
        ny, nx = ys + dy, xs + dx
        ok = (ny < h) & (nx >= 0) & (nx < w)
        ok[ok] = mask[ny[ok], nx[ok]]
        for y, x, y2, x2 in zip(ys[ok], xs[ok], ny[ok], nx[ok]):
            segs.append((x / w, y / h, x2 / w, y2 / h))
    if len(segs) > max_segments:
        step = len(segs) / max_segments
        segs = [segs[int(i * step)] for i in range(max_segments)]
    segs.sort(key=lambda s: (round(s[1] * 24), abs(s[0] - 0.5)))
    aspect = w / float(h)
    return {"aspect": round(aspect, 3), "segments": [tuple(round(v, 4) for v in s) for s in segs]}
