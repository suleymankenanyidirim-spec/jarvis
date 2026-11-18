"""Bilgi penceresi (v3.0): Türkçe Vikipedi'den özet + resim; resim HUD'da çizgi çizimine dönüşür.

Ücretsiz, anahtarsız. Yalnızca aranan konu adı gönderilir.
"""
from urllib.parse import quote

from core import visuals

API = "https://tr.wikipedia.org/w/api.php"
SUMMARY = "https://tr.wikipedia.org/api/rest_v1/page/summary/"
HEADERS = {"User-Agent": "JARVIS-kisisel-asistan/3.0 (yerel masaustu uygulamasi)"}


def lookup(topic, http=None):
    """{"title", "extract", "image", "url"} ya da None."""
    topic = str(topic or "").strip()
    if len(topic) < 2:
        return None
    if http is None:
        import requests as http
    r = http.get(API, params={"action": "query", "list": "search", "srsearch": topic, "srlimit": 1,
                              "format": "json"}, headers=HEADERS, timeout=6)
    r.raise_for_status()
    found = ((r.json() or {}).get("query") or {}).get("search") or []
    if not found:
        return None
    title = found[0]["title"]
    r = http.get(SUMMARY + quote(title.replace(" ", "_"), safe=""), headers=HEADERS, timeout=6)
    r.raise_for_status()
    d = r.json() or {}
    if d.get("type") == "disambiguation" or not d.get("extract"):
        return None
    img = (d.get("thumbnail") or {}).get("source") or (d.get("originalimage") or {}).get("source") or ""
    return {"title": d.get("title", title), "extract": d["extract"], "image": img,
            "url": ((d.get("content_urls") or {}).get("desktop") or {}).get("page", "")}


def drawing(image_url, http=None):
    """Resmi indirip çizgilere çevirir (visuals.image_to_strokes); olmazsa None."""
    if not image_url:
        return None
    if http is None:
        import requests as http
    try:
        r = http.get(image_url, headers=HEADERS, timeout=8)
        r.raise_for_status()
        if len(r.content) > 6 * 1024 * 1024:
            return None
        return visuals.image_to_strokes(r.content)
    except Exception:
        return None


def visual(info, strokes=None):
    lines = []
    for sentence in info["extract"].replace("\n", " ").split(". "):
        sentence = sentence.strip()
        if sentence:
            lines.append(sentence if sentence.endswith(".") else sentence + ".")
        if sum(len(x) for x in lines) > 420:
            break
    return {"type": "info", "side": "left", "data": {
        "title": info["title"], "text": " ".join(lines), "strokes": strokes, "source": "Vikipedi"}}
