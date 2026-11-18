"""HUD görsel kartları (v3.0): hava durumu, klavye, kask, ev planı, bilgi penceresi.

Kart açılınca ana görsel (küre/halka) sağa kayıp küçülür; konuşma bitince kart söner ve görsel ortaya döner.
Hepsi HUD'un kendi çizim araçlarıyla (çizgi, yay, parıltı) ve aynı mavi-siyah renklerle çizilir.

Çizim iki geçişte olur: draw_shapes() bloom'lu sahneye (parlayan çizgiler), draw_texts() keskin yazı katmanına.
"""
import math
import time

import numpy as np

from core.visuals import active_combo

TAU = math.tau
CYAN = (0.04, 0.78, 1.0)
ICE = (0.55, 0.93, 1.0)
HOT = (1.0, 0.98, 0.93)
TXT = (0.70, 0.90, 0.96)
DIM = (0.0, 0.35, 0.55)
IN_S, OUT_S = 0.7, 0.55          # giriş / çıkış süreleri


def _c(rgb, a):
    return (rgb[0], rgb[1], rgb[2], max(0.0, min(1.0, a)))


def ease(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


# ---------------------------------------------------------------- yaşam döngüsü
class CardLayer:
    """Etkin kartı ve açılış/kapanış animasyonunu yönetir. HUD her karede update() ve draw_*() çağırır."""

    def __init__(self, W, H, hold=2.5, clock=time.time):
        self.W, self.H = W, H
        self.hold = float(hold)
        self.clock = clock
        self.card = None
        self.t_show = 0.0
        self.t_hide = None
        self.spoke = False
        self.quiet_since = None
        self.speech_pos = None
        self.text = ""

    def show(self, card, text=""):
        self.card, self.text = card, text or ""
        self.t_show, self.t_hide = self.clock(), None
        self.spoke, self.quiet_since, self.speech_pos = False, None, None

    def hide(self):
        if self.card is not None and self.t_hide is None:
            self.t_hide = self.clock()

    def update(self, speaking, speech_pos=None):
        """Konuşma bitince (ve en az birkaç saniye geçince) kartı kapatır."""
        if self.card is None:
            return
        now = self.clock()
        if self.t_hide is not None:
            if now - self.t_hide > OUT_S:
                self.card = None
            return
        age = now - self.t_show
        if speaking:
            self.spoke, self.quiet_since = True, None
            self.speech_pos = speech_pos
        else:
            if self.quiet_since is None:
                self.quiet_since = now
            waited_for_speech = self.spoke or age > 4.0         # ses hiç gelmediyse de kapanır
            if waited_for_speech and age > 3.0 and now - self.quiet_since >= self.hold:
                self.hide()
        if age > 90:
            self.hide()

    def visibility(self):
        """0 = kart yok (görsel ortada), 1 = kart tam açık (görsel sağda)."""
        if self.card is None:
            return 0.0
        now = self.clock()
        if self.t_hide is not None:
            return ease(1.0 - (now - self.t_hide) / OUT_S)
        return ease((now - self.t_show) / IN_S)

    def orb_transform(self, cx0, cy0):
        """(ölçek, x kayması, y kayması): görsel sağa kayar ve küçülür."""
        p = self.visibility()
        s = 1.0 - 0.48 * p
        dx = p * self.W * 0.31
        dy = -p * self.H * 0.05
        return s, cx0 * (1 - s) + dx, cy0 * (1 - s) + dy

    @property
    def age(self):
        return self.clock() - self.t_show

    # ------------------------------------------------------------ çizim
    def draw_shapes(self, hud, now):
        if self.card is None:
            return
        p = self.visibility()
        fn = SHAPES.get(self.card.get("type"))
        if fn and p > 0.01:
            fn(self, hud, self.card.get("data") or {}, p, now)

    def draw_texts(self, hud, now):
        if self.card is None:
            return
        p = self.visibility()
        fn = TEXTS.get(self.card.get("type"))
        if fn and p > 0.01:
            fn(self, hud, self.card.get("data") or {}, p, now)


# ---------------------------------------------------------------- ortak çerçeve
def frame(hud, x, y, w, h, p, label=""):
    """Köşeli ince çerçeve + yarı saydam zemin; p ile belirir (aşağıdan 18 px kayarak)."""
    y = y + (1 - p) * 18
    a = p
    hud.rect(x, y, w, h, (0.0, 0.05, 0.09, 0.10 * a))
    hud.polyline([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], 1.0, _c(CYAN, 0.22 * a), closed=True)
    k = 18
    for (cx, cy, sx, sy) in ((x, y, 1, 1), (x + w, y, -1, 1), (x, y + h, 1, -1), (x + w, y + h, -1, -1)):
        hud.polyline([(cx + sx * k, cy), (cx, cy), (cx, cy + sy * k)], 2.0, _c(CYAN, 0.9 * a))
    # tarama çizgisi açılışta yukarıdan aşağı geçer
    if p < 1.0:
        sy = y + h * p
        hud.lines([(x, sy, x + w, sy)], 1.2, _c(ICE, 0.5 * (1 - p)))
    return y


def frame_label(hud, x, y, w, p, label, right=""):
    y = y + (1 - p) * 18
    hud.text(label, x + 16, y + 10, 12, TXT, 0.75 * p, spacing=3)
    if right:
        hud.text(right, x + w - 16, y + 10, 12, TXT, 0.55 * p, align="r", spacing=2)


# ---------------------------------------------------------------- hava durumu
WX = dict(x=150, y=150, w=620, h=360)


def _cloud(hud, cx, cy, s, a, fill=False):
    parts = ((-30, 8, 20), (-4, -10, 28), (26, 6, 20))
    for ox, oy, r in parts:
        hud.arcs(cx + ox * s, cy + oy * s, r * s - 1.2, r * s + 1.2, [math.pi * 0.95], math.pi * 1.1,
                 _c(ICE, 0.85 * a))
    hud.lines([(cx - 50 * s, cy + 26 * s, cx + 46 * s, cy + 26 * s)], 2.2, _c(ICE, 0.85 * a))
    hud.glow(cx, cy, 90 * s, (0.0, 0.35, 0.55, 0.25 * a), 2.5)


def _sun(hud, cx, cy, s, a, t):
    hud.glow(cx, cy, 120 * s, (0.6, 0.85, 1.0, 0.35 * a), 2.2)
    hud.ring(cx, cy, 30 * s, 2.4, _c(HOT, 0.95 * a))
    rays = []
    for i in range(12):
        ang = t * 0.35 + i * TAU / 12
        r0, r1 = 40 * s, (52 + 5 * math.sin(t * 3 + i)) * s
        rays.append((cx + math.cos(ang) * r0, cy + math.sin(ang) * r0, cx + math.cos(ang) * r1, cy + math.sin(ang) * r1))
    hud.lines(rays, 2.0, _c(ICE, 0.9 * a))


def _moon(hud, cx, cy, s, a):
    hud.glow(cx, cy, 100 * s, (0.3, 0.6, 1.0, 0.25 * a), 2.4)
    hud.arcs(cx, cy, 30 * s - 1.4, 30 * s + 1.4, [-math.pi * 0.62], math.pi * 1.24 + math.pi, _c(ICE, 0.9 * a))
    hud.arcs(cx + 12 * s, cy - 6 * s, 24 * s - 1.2, 24 * s + 1.2, [math.pi * 0.62], math.pi * 0.76, _c(ICE, 0.6 * a))


def weather_icon(hud, icon, cx, cy, s, a, t, is_day=True):
    if icon == "sun":
        (_sun if is_day else (lambda h, x, y, s_, a_, t_: _moon(h, x, y, s_, a_)))(hud, cx, cy, s, a, t)
        return
    if icon == "partly":
        if is_day:
            _sun(hud, cx - 26 * s, cy - 24 * s, s * 0.7, a * 0.8, t)
        else:
            _moon(hud, cx - 26 * s, cy - 24 * s, s * 0.7, a * 0.8)
    _cloud(hud, cx + (8 * s if icon == "partly" else 0), cy, s, a)
    base = cy + 34 * s
    if icon in ("rain", "drizzle"):
        n, ln, sp = (10, 16, 140) if icon == "rain" else (6, 8, 90)
        drops = []
        for i in range(n):
            x = cx + (-44 + i * 88 / max(1, n - 1)) * s
            y = base + ((t * sp + i * 23) % 60) * s
            drops.append((x, y, x - 4 * s, y + ln * s))
        hud.lines(drops, 1.6, _c(CYAN, 0.9 * a))
    elif icon == "snow":
        xs, ys = [], []
        for i in range(9):
            xs.append(cx + (-44 + i * 11) * s + 4 * math.sin(t * 1.3 + i) * s)
            ys.append(base + ((t * 28 + i * 17) % 60) * s)
        hud.dots(np.array(xs, "f4"), np.array(ys, "f4"), np.full(9, 4.2 * s, "f4"),
                 np.tile(np.array(_c(HOT, 0.9 * a), "f4"), (9, 1)))
    elif icon == "storm":
        flash = 0.35 + 0.65 * (1.0 if (t % 2.2) < 0.18 or 0.3 < (t % 2.2) < 0.4 else 0.2)
        pts = [(cx + 6 * s, base - 2 * s), (cx - 8 * s, base + 22 * s), (cx + 4 * s, base + 22 * s), (cx - 10 * s, base + 50 * s)]
        hud.polyline(pts, 2.6, _c(HOT, flash * a))
        hud.glow(cx - 2 * s, base + 24 * s, 70 * s, (0.5, 0.8, 1.0, 0.35 * flash * a), 2.0)
    elif icon == "fog":
        segs = []
        for i in range(4):
            y = base + i * 10 * s
            off = 10 * math.sin(t * 0.8 + i) * s
            segs.append((cx - 48 * s + off, y, cx + 48 * s + off, y))
        hud.lines(segs, 2.0, _c(ICE, 0.6 * a))


def _wx_shapes(layer, hud, d, p, now):
    x, y, w, h = WX["x"], WX["y"], WX["w"], WX["h"]
    y = frame(hud, x, y, w, h, p)
    weather_icon(hud, d.get("icon", "cloud"), x + 150, y + h * 0.48, 1.35, p, now, d.get("is_day", True))
    hud.lines([(x + 300, y + 60, x + 300, y + h - 40)], 1.0, _c(CYAN, 0.25 * p))


def _wx_texts(layer, hud, d, p, now):
    x, y, w, h = WX["x"], WX["y"], WX["w"], WX["h"]
    frame_label(hud, x, y, w, p, "HAVA DURUMU", f"{str(d.get('place', '')).upper()}  ·  {str(d.get('day', '')).upper()}")
    y += (1 - p) * 18
    big = d.get("temp") if d.get("temp") is not None else d.get("max")
    hud.text(f"{big}°" if big is not None else "--", x + 330, y + 62, 64, (0.92, 0.98, 1.0), 0.95 * p, bold=True)
    hud.text(str(d.get("desc", "")).upper(), x + 330, y + 150, 16, TXT, 0.85 * p, spacing=3)
    rows = []
    if d.get("feels") is not None:
        rows.append(("HİSSEDİLEN", f"{d['feels']}°"))
    if d.get("max") is not None:
        rows.append(("EN YÜKSEK / DÜŞÜK", f"{d['max']}°  /  {d['min']}°"))
    if d.get("rain_chance") is not None:
        rows.append(("YAĞIŞ İHTİMALİ", f"%{d['rain_chance']}"))
    if d.get("humidity") is not None:
        rows.append(("NEM", f"%{d['humidity']}"))
    if d.get("wind") is not None:
        rows.append(("RÜZGAR", f"{d['wind']} km/sa"))
    for i, (k, v) in enumerate(rows):
        ry = y + 190 + i * 26
        k_a = p * min(1.0, max(0.0, (layer.age - 0.4 - i * 0.08) / 0.3))
        hud.text(k, x + 330, ry, 12, TXT, 0.45 * k_a, spacing=1.5)
        hud.text(v, x + w - 30, ry, 14, (0.88, 0.97, 1.0), 0.9 * k_a, align="r")


# ---------------------------------------------------------------- klavye
U = 38.0          # bir tuşun genişliği (px)
KB_ROWS = [
    [("ESC", 1.0)] + [(f"F{i}", 1.0) for i in range(1, 13)] + [("DEL", 1.0)],
    [("\"", 1.0)] + [(c, 1.0) for c in "1234567890"] + [("*", 1.0), ("-", 1.0), ("BACKSPACE", 1.9)],
    [("TAB", 1.4)] + [(c, 1.0) for c in "QWERTYUIOPĞÜ"] + [("ENTER", 1.5)],
    [("CAPS", 1.7)] + [(c, 1.0) for c in "ASDFGHJKLŞİ"] + [(",", 1.0), ("ENTER", 1.2)],
    [("SHIFT", 1.3), ("<", 1.0)] + [(c, 1.0) for c in "ZXCVBNMÖÇ"] + [(".", 1.0), ("SHIFT", 2.6)],
    [("CTRL", 1.5), ("WIN", 1.2), ("ALT", 1.2), ("SPACE", 6.6), ("ALTGR", 1.2), ("CTRL", 1.5)],
]
KB_LABEL = {"BACKSPACE": "GERİ", "SPACE": "", "WIN": "WIN", "ALTGR": "ALT GR"}


def keyboard_layout(x0, y0, u=U, gap=5.0):
    """[(ad, x, y, w, h), ...] — Türkçe Q klavye."""
    keys = []
    for r, row in enumerate(KB_ROWS):
        x = x0
        y = y0 + r * (u + gap) + (8 if r > 0 else 0)
        for name, wu in row:
            w = wu * u + (wu - 1) * gap
            keys.append((name, x, y, w, u))
            x += w + gap
    return keys


def _kb_geom(layer):
    x0, y0 = 120, 250
    return x0, y0, keyboard_layout(x0, y0)


def _kb_active(layer, d, now):
    combos = d.get("combos") or []
    if not combos:
        return None
    if layer.spoke and layer.speech_pos is not None:
        return active_combo(combos, layer.speech_pos)
    # ses yoksa (yazı modu) kısayollar sırayla gösterilir
    i = int(max(0.0, layer.age - 0.9) / 2.6) % len(combos)
    return combos[i]


def _kb_shapes(layer, hud, d, p, now):
    x0, y0, keys = _kb_geom(layer)
    w = max(k[1] + k[3] for k in keys) - x0
    h = max(k[2] + k[4] for k in keys) - y0
    frame(hud, x0 - 24, y0 - 92, w + 48, h + 116, p)
    act = _kb_active(layer, d, now)
    on = set(act["keys"]) if act else set()
    age = layer.age
    # açılış: tuşlar ana görselden çıkan küçük "atomlar" gibi yerlerine uçar
    s, tx, ty = layer.orb_transform(layer.W * 0.5, layer.H * 0.47)
    ox, oy = layer.W * 0.5 * s + tx, layer.H * 0.47 * s + ty
    fly = min(1.0, max(0.0, age / 1.0))
    if fly < 1.0:
        n = len(keys)
        xs = np.empty(n, "f4")
        ys = np.empty(n, "f4")
        for i, (_name, kx, ky, kw, kh) in enumerate(keys):
            f = ease(min(1.0, fly * 1.4 - (i % 14) * 0.02))
            xs[i] = ox + (kx + kw / 2 - ox) * f
            ys[i] = oy + (ky + kh / 2 - oy) * f + math.sin(f * math.pi) * -40
        hud.dots(xs, ys, np.full(n, 4.0, "f4"), np.tile(np.array(_c(ICE, 0.9 * (1 - fly) * p), "f4"), (n, 1)))
    ka = p * ease((age - 0.5) / 0.6)
    for name, kx, ky, kw, kh in keys:
        lit = name in on
        a = ka * (1.0 if lit else 0.32)
        hud.polyline([(kx, ky), (kx + kw, ky), (kx + kw, ky + kh), (kx, ky + kh)], 1.6 if lit else 1.0,
                     _c(ICE if lit else CYAN, a), closed=True)
        if lit:
            pulse = 0.75 + 0.25 * math.sin(now * 7)
            hud.rect(kx, ky, kw, kh, _c(CYAN, 0.22 * ka * pulse))
            hud.glow(kx + kw / 2, ky + kh / 2, max(kw, kh) * 1.3, (0.1, 0.6, 1.0, 0.55 * ka * pulse), 2.0)
            # basılı tuştan küçük atomlar yükselir
            n = 6
            ph = (now * 0.9 + np.arange(n) / n) % 1.0
            xs = kx + kw / 2 + np.sin(ph * 9 + np.arange(n)) * kw * 0.35
            ys = ky - ph * 34
            hud.dots(xs.astype("f4"), ys.astype("f4"), np.full(n, 3.2, "f4"),
                     np.array([_c(ICE, (1 - q) * ka) for q in ph], "f4"))


def _kb_texts(layer, hud, d, p, now):
    x0, y0, keys = _kb_geom(layer)
    w = max(k[1] + k[3] for k in keys) - x0
    frame_label(hud, x0 - 24, y0 - 92, w + 48, p, "KLAVYE KISAYOLU")
    act = _kb_active(layer, d, now)
    on = set(act["keys"]) if act else set()
    ka = p * ease((layer.age - 0.5) / 0.6)
    if act:
        hud.text(act["label"], x0 + w / 2, y0 - 58, 30, (0.92, 0.98, 1.0), 0.95 * ka, align="c", bold=True, spacing=2)
    for name, kx, ky, kw, kh in keys:
        label = KB_LABEL.get(name, name)
        if not label:
            continue
        lit = name in on
        size = 13 if len(label) == 1 else 9
        hud.text(label, kx + kw / 2, ky + kh / 2 - size * 0.62, size, (0.95, 1.0, 1.0) if lit else TXT,
                 (0.98 if lit else 0.5) * ka, align="c")


# ---------------------------------------------------------------- kask
HX = dict(x=250, y=110, w=440, h=420)


def _helmet_points(cx, cy, s):
    """Kaskın önden görünüşü (dış hat) ve yüz plakası."""
    shell = [(-92, 40), (-96, -20), (-80, -78), (-44, -112), (0, -122), (44, -112), (80, -78), (96, -20),
             (92, 40), (74, 92), (40, 124), (0, 134), (-40, 124), (-74, 92)]
    plate = [(-62, -58), (0, -66), (62, -58), (68, 2), (52, 70), (24, 104), (0, 110), (-24, 104), (-52, 70), (-68, 2)]
    eye_l = [(-50, -22), (-14, -16), (-18, -6), (-48, -10)]
    eye_r = [(50, -22), (14, -16), (18, -6), (48, -10)]
    mouth = [(-26, 62), (26, 62)]
    f = lambda pts: [(cx + px * s, cy + py * s) for px, py in pts]   # noqa: E731
    return f(shell), f(plate), f(eye_l), f(eye_r), f(mouth)


def _helmet_open_amount(layer, d):
    state = d.get("state")
    target = 1.0 if state == "open" else 0.0 if state == "closed" else 0.5
    start = 1.0 - target if state in ("open", "closed") else 0.5
    k = ease((layer.age - 0.5) / 1.1)
    return start + (target - start) * k


def _helmet_shapes(layer, hud, d, p, now):
    x, y, w, h = HX["x"], HX["y"], HX["w"], HX["h"]
    y = frame(hud, x, y, w, h, p)
    cx, cy, s = x + w / 2, y + h / 2 + 10, 1.25
    shell, plate, el, er, mouth = _helmet_points(cx, cy, s)
    hud.polyline(shell, 1.8, _c(CYAN, 0.85 * p), closed=True)
    o = _helmet_open_amount(layer, d)
    lift = -o * 58 * s                       # yüz plakası yukarı kalkar ve geriye yatar (basıklaşır)
    top = cy - 66 * s
    squash = 1.0 - 0.45 * o
    mv = lambda pts: [(px, top + (py - top) * squash + lift) for px, py in pts]   # noqa: E731
    hud.polyline(mv(plate), 2.2, _c(ICE, 0.95 * p), closed=True)
    eye_a = p * (1.0 - o) * (0.8 + 0.2 * math.sin(now * 5))
    for eye in (el, er):
        hud.polyline(mv(eye), 2.4, _c(HOT, max(0.15 * p, eye_a)), closed=True)
        ex = sum(q[0] for q in eye) / 4
        ey = sum(q[1] for q in eye) / 4 + lift
        hud.glow(ex, ey, 40, (0.4, 0.8, 1.0, 0.45 * eye_a), 2.0)
    hud.polyline(mv(mouth), 1.4, _c(CYAN, 0.6 * p))
    if o > 0.5:                              # açıkken yüzün yeri: sönük iç hat
        hud.polyline([(cx - 46 * s, cy - 10 * s), (cx, cy + 70 * s), (cx + 46 * s, cy - 10 * s)], 1.0,
                     _c(CYAN, 0.25 * p * o))


def _helmet_texts(layer, hud, d, p, now):
    x, y, w, h = HX["x"], HX["y"], HX["w"], HX["h"]
    frame_label(hud, x, y, w, p, "MARK KASK")
    st = d.get("state")
    word = {"open": "AÇIK", "closed": "KAPALI"}.get(st, "BİLİNMİYOR")
    hud.text(word, x + w / 2, y + (1 - p) * 18 + h + 14, 22, (0.92, 0.98, 1.0), 0.95 * p, align="c", bold=True, spacing=6)


# ---------------------------------------------------------------- ev planı
HM = dict(x=110, y=110, w=720, h=470)


def _home_geom(d):
    lay = d.get("layout") or {}
    rooms = lay.get("rooms") or []
    if not rooms:
        return [], [], (0, 0, 1)
    maxx = max(r["x"] + r["w"] for r in rooms)
    maxy = max(r["y"] + r["h"] for r in rooms)
    x, y, w, h = HM["x"], HM["y"], HM["w"], HM["h"]
    # eğik (izometrik benzeri) görünüm: y ekseni sağa kayar ve basıktır
    sk, sq = 0.45, 0.62
    span_w = maxx + maxy * sk
    unit = min((w - 80) / span_w, (h - 140) / (maxy * sq))
    ox = x + 40 + (w - 80 - span_w * unit) / 2
    oy = y + 70
    return rooms, lay.get("devices") or [], (ox, oy, unit, sk, sq)


def _proj(g, gx, gy, z=0.0):
    ox, oy, unit, sk, sq = g
    return ox + (gx + gy * sk) * unit, oy + gy * sq * unit - z


def _home_shapes(layer, hud, d, p, now):
    x, y, w, h = HM["x"], HM["y"], HM["w"], HM["h"]
    frame(hud, x, y, w, h, p)
    rooms, devices, g = _home_geom(d)
    if not rooms:
        return
    states = d.get("states") or {}
    hl = set(d.get("highlight") or [])
    build = ease((layer.age - 0.2) / 1.2)
    wall = 22.0 * build
    for i, r in enumerate(rooms):
        ra = p * ease((layer.age - 0.2 - i * 0.08) / 0.5)
        c = [(r["x"], r["y"]), (r["x"] + r["w"], r["y"]), (r["x"] + r["w"], r["y"] + r["h"]), (r["x"], r["y"] + r["h"])]
        floor = [_proj(g, *q) for q in c]
        top = [_proj(g, *q, z=wall) for q in c]
        lit = any(states.get(dv["id"]) and dv.get("type") == "light" for dv in devices if dv.get("room") == r["id"])
        if lit:
            hud.tris([floor[0], floor[1], floor[2], floor[0], floor[2], floor[3]],
                     np.tile(np.array(_c(CYAN, 0.10 * ra), "f4"), (6, 1)))
        hud.polyline(floor, 1.0, _c(CYAN, 0.35 * ra), closed=True)
        hud.polyline(top, 1.4, _c(CYAN, 0.75 * ra), closed=True)
        hud.lines([(*floor[k], *top[k]) for k in range(4)], 1.0, _c(CYAN, 0.35 * ra))
    for dv in devices:
        if "x" not in dv:
            continue
        px, py = _proj(g, dv["x"], dv["y"], z=wall * 0.5)
        on = bool(states.get(dv["id"]))
        emph = dv["id"] in hl
        pulse = 0.7 + 0.3 * math.sin(now * 6) if emph else 1.0
        kind = dv.get("type", "light")
        if kind == "light":
            if on:
                hud.glow(px, py, 70, (0.35, 0.8, 1.0, 0.55 * p * pulse), 2.0)
                hud.ring(px, py, 7, 3.0, _c(HOT, 0.95 * p))
            else:
                hud.ring(px, py, 7, 1.2, _c(CYAN, 0.45 * p * pulse))
        elif kind == "klima":
            rot = now * 2.0 if on else 0.0
            segs = []
            for k in range(3):
                a = rot + k * math.pi / 3
                segs.append((px - math.cos(a) * 9, py - math.sin(a) * 9, px + math.cos(a) * 9, py + math.sin(a) * 9))
            hud.lines(segs, 1.6, _c(ICE if on else CYAN, (0.95 if on else 0.4) * p * pulse))
        else:   # ısıtıcı / priz
            segs = []
            for k in range(3):
                xx = px - 6 + k * 6
                for j in range(4):
                    y0 = py + 8 - j * 4 - ((now * 12) % 4 if on else 0)
                    segs.append((xx + (1 if j % 2 else -1) * 1.5, y0, xx + (-1 if j % 2 else 1) * 1.5, y0 - 4))
            hud.lines(segs, 1.4, _c(HOT if on else CYAN, (0.9 if on else 0.4) * p * pulse))


def _home_texts(layer, hud, d, p, now):
    x, y, w, h = HM["x"], HM["y"], HM["w"], HM["h"]
    frame_label(hud, x, y, w, p, "EV", "SİMÜLASYON" if d.get("simulated", True) else "CANLI")
    rooms, devices, g = _home_geom(d)
    states = d.get("states") or {}
    for i, r in enumerate(rooms):
        ra = p * ease((layer.age - 0.5 - i * 0.08) / 0.5)
        cx, cy = _proj(g, r["x"] + r["w"] / 2, r["y"] + r["h"] / 2)
        hud.text(str(r["name"]).upper(), cx, cy + 4, 11, TXT, 0.75 * ra, align="c", spacing=1.5)
    on = sum(1 for dv in devices if states.get(dv["id"]))
    hud.text(f"AÇIK: {on}  ·  KAPALI: {len(devices) - on}", x + w - 16, y + (1 - p) * 18 + h - 30, 12, TXT,
             0.6 * p, align="r", spacing=1.5)


# ---------------------------------------------------------------- bilgi penceresi (solda)
IX = dict(x=40, y=70, w=560, h=560)
DRAW_BOX = (70, 120, 500, 270)        # çizim alanı (x, y, w, h)


def _info_shapes(layer, hud, d, p, now):
    x, y, w, h = IX["x"], IX["y"], IX["w"], IX["h"]
    y = frame(hud, x, y, w, h, p)
    st = d.get("strokes") or {}
    segs = st.get("segments") or []
    if not segs:
        return
    bx, by, bw, bh = DRAW_BOX
    by += (1 - p) * 18
    asp = float(st.get("aspect") or 1.0)
    dw, dh = (bw, bw / asp) if bw / asp <= bh else (bh * asp, bh)
    ox, oy = bx + (bw - dw) / 2, by + (bh - dh) / 2
    arr = np.asarray(segs, "f4")
    prog = ease((layer.age - 0.4) / 3.6)            # çizim 3,6 sn'de tamamlanır
    n = int(len(arr) * prog)
    if n <= 0:
        return
    pts = arr[:n].copy()
    pts[:, (0, 2)] = ox + pts[:, (0, 2)] * dw
    pts[:, (1, 3)] = oy + pts[:, (1, 3)] * dh
    hud.lines(pts, 1.1, _c(CYAN, 0.75 * p))
    head = pts[max(0, n - 120):n]                   # yeni çizilen uç parlak
    if len(head):
        hud.lines(head, 1.6, _c(HOT, 0.9 * p))
        hx, hy = float(head[-1][2]), float(head[-1][3])
        hud.glow(hx, hy, 40, (0.4, 0.85, 1.0, 0.6 * p * (1 - prog * 0.6)), 2.0)


def _info_texts(layer, hud, d, p, now):
    x, y, w, h = IX["x"], IX["y"], IX["w"], IX["h"]
    frame_label(hud, x, y, w, p, "BİLGİ", str(d.get("source", "")).upper())
    y += (1 - p) * 18
    has_draw = bool((d.get("strokes") or {}).get("segments"))
    ty = y + (330 if has_draw else 50)
    hud.text(str(d.get("title", "")), x + 24, ty, 22, (0.92, 0.98, 1.0), 0.95 * p, bold=True)
    lines = hud.wrap(str(d.get("text", "")), 14, w - 48)
    shown = ease((layer.age - 0.8) / 2.0)
    max_lines = 7 if has_draw else 18
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(" .,;") + "…"
    for i, line in enumerate(lines):
        la = p * min(1.0, max(0.0, shown * max_lines - i))
        hud.text(line, x + 24, ty + 38 + i * 22, 14, TXT, 0.85 * la)


SHAPES = {"weather": _wx_shapes, "keyboard": _kb_shapes, "helmet": _helmet_shapes, "home": _home_shapes,
          "info": _info_shapes}
TEXTS = {"weather": _wx_texts, "keyboard": _kb_texts, "helmet": _helmet_texts, "home": _home_texts,
         "info": _info_texts}
