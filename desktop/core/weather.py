"""Hava durumu (v3.0): Open-Meteo — ücretsiz, anahtarsız. Yalnızca şehir adı / koordinat gönderilir.

get_weather("Ankara") / get_weather() (config.LOCATION_*) -> {
    "place", "temp", "feels", "humidity", "wind", "code", "icon", "desc", "is_day",
    "max", "min", "rain_chance", "day": "bugün" | "yarın"}
"""
import time

from core import visuals

GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_CACHE = {}
CACHE_SECONDS = 600


def _cfg(name, default=None):
    try:
        import config
        return getattr(config, name, default)
    except Exception:
        return default


def geocode(name, http):
    r = http.get(GEO_URL, params={"name": name, "count": 1, "language": "tr", "format": "json"}, timeout=8)
    r.raise_for_status()
    res = (r.json() or {}).get("results") or []
    if not res:
        return None
    g = res[0]
    label = g.get("name", name)
    if g.get("admin1") and g.get("admin1") != label:
        label += ", " + g["admin1"]
    return float(g["latitude"]), float(g["longitude"]), label


def get_weather(place="", tomorrow=False, http=None):
    """Hava durumu sözlüğü. Yer bulunamazsa ValueError, ağ hatasında requests istisnası."""
    if http is None:
        import requests as http
    key = (str(place or "").strip().lower(), bool(tomorrow))
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return dict(hit[1])
    if place:
        geo = geocode(place, http)
        if not geo:
            raise ValueError(f"'{place}' adlı yer bulunamadı.")
        lat, lon, label = geo
    else:
        lat, lon = float(_cfg("LOCATION_LAT", 41.0082)), float(_cfg("LOCATION_LON", 28.9784))
        label = str(_cfg("LOCATION_NAME", "İstanbul")).split(",")[0].title()
    r = http.get(FORECAST_URL, params={
        "latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": 2,
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m,is_day",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
    }, timeout=8)
    r.raise_for_status()
    d = r.json() or {}
    cur, daily = d.get("current") or {}, d.get("daily") or {}
    i = 1 if tomorrow else 0

    def day(field):
        vals = daily.get(field) or []
        return vals[i] if len(vals) > i else None

    code = day("weather_code") if tomorrow else cur.get("weather_code")
    icon, desc = visuals.weather_icon(code)
    out = {
        "place": label, "day": "yarın" if tomorrow else "bugün",
        "temp": None if tomorrow else _round(cur.get("temperature_2m")),
        "feels": None if tomorrow else _round(cur.get("apparent_temperature")),
        "humidity": None if tomorrow else _round(cur.get("relative_humidity_2m")),
        "wind": None if tomorrow else _round(cur.get("wind_speed_10m")),
        "is_day": bool(cur.get("is_day", 1)) or tomorrow,
        "code": code, "icon": icon, "desc": desc,
        "max": _round(day("temperature_2m_max")), "min": _round(day("temperature_2m_min")),
        "rain_chance": _round(day("precipitation_probability_max")),
    }
    _CACHE[key] = (time.time(), out)
    return dict(out)


def _round(v):
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return None


def summary(w):
    """Modele verilecek kısa metin."""
    parts = [f"{w['place']} ({w['day']}): {w['desc']}"]
    if w.get("temp") is not None:
        parts.append(f"şu an {w['temp']}°C (hissedilen {w['feels']}°C)")
    if w.get("max") is not None:
        parts.append(f"en yüksek {w['max']}°C, en düşük {w['min']}°C")
    if w.get("rain_chance") is not None:
        parts.append(f"yağış ihtimali %{w['rain_chance']}")
    if w.get("humidity") is not None:
        parts.append(f"nem %{w['humidity']}")
    if w.get("wind") is not None:
        parts.append(f"rüzgar {w['wind']} km/sa")
    return ", ".join(parts) + "."


def visual(w):
    return {"type": "weather", "side": "center", "data": w}
