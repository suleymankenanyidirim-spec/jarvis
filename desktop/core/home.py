"""Akıllı ev (v3.0): odaların mini modeli ve cihaz durumları.

- Ev planı `data/ev.json` (ilk açılışta örnek plan yazılır; kendi evine göre düzenle):
    {"rooms": [{"id": "salon", "name": "Salon", "x": 0, "y": 0, "w": 5, "h": 4}, ...],
     "devices": [{"id": "salon_isik", "name": "Salon ışığı", "room": "salon", "type": "light", "x": 2.5, "y": 2}, ...]}
  x / y / w / h ızgara birimi (ör. metre); HUD planı ekrana sığdırır. type: light | klima | isitici | priz.
- Durum `data/ev_durum.json`. Gerçek bir sisteme (ör. Miola) bağlanana kadar durumu JARVIS tutar ve kartta
  "SİMÜLASYON" yazar. Bağlantı eklenince `provider` aynı arayüzle (get_states / set_state) çalışır.
"""
import json
import re
import threading

from core import turkish
from core.paths import data_path

DEFAULT_LAYOUT = {
    "rooms": [
        {"id": "salon", "name": "Salon", "x": 0, "y": 0, "w": 5, "h": 4},
        {"id": "mutfak", "name": "Mutfak", "x": 5, "y": 0, "w": 3, "h": 3},
        {"id": "banyo", "name": "Banyo", "x": 8, "y": 0, "w": 3, "h": 3},
        {"id": "koridor", "name": "Koridor", "x": 5, "y": 3, "w": 6, "h": 1},
        {"id": "yatak", "name": "Yatak Odası", "x": 0, "y": 4, "w": 5, "h": 3},
        {"id": "calisma", "name": "Çalışma Odası", "x": 5, "y": 4, "w": 6, "h": 3},
    ],
    "devices": [
        {"id": "salon_isik", "name": "Salon ışığı", "room": "salon", "type": "light", "x": 2.5, "y": 2},
        {"id": "salon_klima", "name": "Salon kliması", "room": "salon", "type": "klima", "x": 0.6, "y": 0.5},
        {"id": "mutfak_isik", "name": "Mutfak ışığı", "room": "mutfak", "type": "light", "x": 6.5, "y": 1.5},
        {"id": "banyo_isik", "name": "Banyo ışığı", "room": "banyo", "type": "light", "x": 9.5, "y": 1.5},
        {"id": "koridor_isik", "name": "Koridor ışığı", "room": "koridor", "type": "light", "x": 8, "y": 3.5},
        {"id": "yatak_isik", "name": "Yatak odası ışığı", "room": "yatak", "type": "light", "x": 2.5, "y": 5.5},
        {"id": "calisma_isik", "name": "Çalışma odası ışığı", "room": "calisma", "type": "light", "x": 8, "y": 5.5},
        {"id": "kombi", "name": "Kombi", "room": "mutfak", "type": "isitici", "x": 7.6, "y": 0.4},
    ],
}
TYPE_WORDS = {
    "light": ("ışık", "işik", "lamba", "aydınlatma", "ampul"),
    "klima": ("klima", "soğutma", "serinlet"),
    "isitici": ("kombi", "ısıtma", "ısıtıcı", "petek", "kalorifer"),
    "priz": ("priz",),
}
TYPE_LABEL = {"light": "ışık", "klima": "klima", "isitici": "ısıtma", "priz": "priz"}
ON_RE = re.compile(r"\b(aç|açar\s*mısın|yak|yakar\s*mısın|çalıştır|başlat)\b")
OFF_RE = re.compile(r"\b(kapat|kapatır\s*mısın|söndür|durdur)\b")
QUERY_RE = re.compile(r"açık\s*mı|kapalı\s*mı|yanıyor\s*mu|yanık\s*mı|hangi|durum|neler\s*açık|ne\s*açık|kontrol\s*et")
ALL_RE = re.compile(r"\b(tüm|bütün|hepsi|hepsini|evdeki|her\s*yer)\b")
HOME_HINT = re.compile(r"ışı|lamba|aydınlat|klima|kombi|ısıt|petek|kalorifer|priz|odanın|odası|salon|mutfak|banyo|koridor")


class Home:
    def __init__(self, layout_path=None, state_path=None, provider=None):
        self.layout_path = layout_path or data_path("ev.json")
        self.state_path = state_path or data_path("ev_durum.json")
        self.provider = provider
        self._lock = threading.Lock()

    # ------------------------------------------------------------ dosyalar
    def layout(self):
        try:
            data = json.loads(self.layout_path.read_text(encoding="utf-8"))
            if data.get("rooms"):
                return data
        except (OSError, ValueError):
            pass
        try:
            self.layout_path.parent.mkdir(parents=True, exist_ok=True)
            self.layout_path.write_text(json.dumps(DEFAULT_LAYOUT, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
        return json.loads(json.dumps(DEFAULT_LAYOUT))

    def states(self):
        if self.provider is not None:
            try:
                return dict(self.provider.get_states())
            except Exception as exc:
                print("[EV] Cihaz durumu okunamadı:", exc)
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self, states):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(states, ensure_ascii=False, indent=2), encoding="utf-8")

    @property
    def simulated(self):
        return self.provider is None

    # ------------------------------------------------------------ komutlar
    @staticmethod
    def is_home_command(text):
        low = turkish.tr_lower(text)
        return bool(HOME_HINT.search(low)) and bool(ON_RE.search(low) or OFF_RE.search(low) or QUERY_RE.search(low))

    def _match(self, text, layout):
        low = turkish.tr_lower(text)
        words = turkish.words(text)
        def has(tw):
            return tw in low or any(turkish.words_match(tw, w) for w in words)   # ışığını ~ ışık
        types = {t for t, ws in TYPE_WORDS.items() if any(has(tw) for tw in ws)}
        rooms = []
        for r in layout["rooms"]:
            name_words = turkish.words(r["name"])
            if name_words and all(any(turkish.words_match(nw, w) for w in words) for nw in name_words[:1]):
                rooms.append(r["id"])
        devs = layout.get("devices", [])
        hit = [d for d in devs if (not types or d.get("type") in types) and (not rooms or d.get("room") in rooms)]
        if not rooms and not ALL_RE.search(low) and types == {"light"} and not QUERY_RE.search(low):
            hit = [d for d in hit if d.get("type") == "light"]
        return hit, rooms, types

    def handle(self, text):
        """Ev komutu ise (cevap, kart) döner, değilse None."""
        if not self.is_home_command(text):
            return None
        layout = self.layout()
        low = turkish.tr_lower(text)
        devs, rooms, types = self._match(text, layout)
        if not devs:
            return ("Bu isimde bir oda ya da cihaz bulamadım. Ev planı: data/ev.json", self.visual([]))
        with self._lock:
            states = self.states()
            action = None
            if OFF_RE.search(low):
                action = False
            elif ON_RE.search(low) and not QUERY_RE.search(low):
                action = True
            if action is None:                           # durum sorusu
                on = [d["name"] for d in devs if states.get(d["id"])]
                off = [d["name"] for d in devs if not states.get(d["id"])]
                if len(devs) == 1:
                    reply = f"{devs[0]['name']} {'açık' if on else 'kapalı'}."
                else:
                    reply = ("Açık olanlar: " + ", ".join(on) + "." if on else "Hepsi kapalı.") + \
                            (f" Kapalı: {len(off)} cihaz." if on and off else "")
            else:
                for d in devs:
                    states[d["id"]] = action
                    if self.provider is not None:
                        try:
                            self.provider.set_state(d["id"], action)
                        except Exception as exc:
                            print("[EV] Cihaza gönderilemedi:", exc)
                if self.provider is None:
                    self._save(states)
                verb = "açıldı" if action else "kapatıldı"
                names = devs[0]["name"] if len(devs) == 1 else f"{len(devs)} cihaz"
                reply = f"Tamam, {verb}: {names}."
                if self.simulated:
                    reply += " (Gerçek ev sistemine henüz bağlı değilim; durumu ben tutuyorum.)"
        return reply, self.visual([d["id"] for d in devs], states)

    def visual(self, highlight, states=None):
        return {"type": "home", "side": "center", "data": {
            "layout": self.layout(), "states": states if states is not None else self.states(),
            "highlight": list(highlight), "simulated": self.simulated}}
