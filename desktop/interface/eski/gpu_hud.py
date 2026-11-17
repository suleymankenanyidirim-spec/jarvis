"""JARVIS HUD v0.7

Tüm arayüz 1280x720 tasarım koordinatlarında çizilir; pencere büyütülürse
oran korunarak ölçeklenir. Çizim yapısı:

  geometri (üçgen batch) -> yazılar -> bloom (parlama) -> ekran

Sensör okumaları (CPU/RAM/GPU/ağ/disk) ve hava durumu ayrı thread'lerde
çalışır, böylece nvidia-smi gibi yavaş çağrılar animasyonu takıltmaz.
"""
import math
import os
import subprocess
import threading

try:
    _JARVIS_VERSION = (__import__('pathlib').Path(__file__).resolve().parents[1] / 'VERSION').read_text(encoding='utf8').strip()
except Exception:
    _JARVIS_VERSION = '1.4'
import time
from collections import deque
from contextlib import contextmanager

import moderngl
import numpy as np
import psutil
import pygame
import requests

import config
from voice.listener import VoiceListener
from voice.speaker import VoiceSpeaker

TAU = math.tau
W, H = 1280, 720

# ---------------------------------------------------------------- palet
CYAN = (0.0, 0.86, 1.0)
SOFT = (0.08, 0.40, 0.52)
WHITE = (0.74, 0.92, 0.96)
BRIGHT = (0.86, 0.98, 1.0)
GREEN = (0.15, 1.0, 0.65)
WARN = (1.0, 0.72, 0.20)
RED = (1.0, 0.36, 0.30)
BG = (0.006, 0.018, 0.030)
PANEL_T = (0.02, 0.11, 0.15)
PANEL_B = (0.01, 0.06, 0.09)


def C(col, a=1.0):
    return (col[0], col[1], col[2], a)


# ------------------------------------------------------------- temalar
# "klasik": orijinal mavi-camgöbeği JARVIS paleti
# "reaktor": daha koyu/siyah zemin, elektrik mavisi halkalar,
#            yeşil-camgöbeği çekirdek (arc-reactor referans görseline yakın)
THEMES = {
    "klasik": {
        "CYAN": (0.0, 0.86, 1.0), "SOFT": (0.08, 0.40, 0.52),
        "WHITE": (0.74, 0.92, 0.96), "BRIGHT": (0.86, 0.98, 1.0),
        "GREEN": (0.15, 1.0, 0.65), "WARN": (1.0, 0.72, 0.20),
        "RED": (1.0, 0.36, 0.30), "BG": (0.006, 0.018, 0.030),
        "PANEL_T": (0.02, 0.11, 0.15), "PANEL_B": (0.01, 0.06, 0.09),
    },
    "reaktor": {
        "CYAN": (0.15, 0.62, 1.0), "SOFT": (0.04, 0.16, 0.24),
        "WHITE": (0.72, 0.94, 0.97), "BRIGHT": (0.55, 1.0, 0.93),
        "GREEN": (0.20, 0.95, 0.55), "WARN": (1.0, 0.72, 0.20),
        "RED": (1.0, 0.36, 0.30), "BG": (0.0, 0.0, 0.0),
        "PANEL_T": (0.0, 0.05, 0.06), "PANEL_B": (0.0, 0.01, 0.015),
    },
}
THEME_NAMES = list(THEMES.keys())

_SETTINGS_PATH = "data/hud_settings.json"


def _load_saved_theme(default_name):
    try:
        import json
        with open(_SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        name = data.get("theme")
        if name in THEMES:
            return name
    except Exception:
        pass
    return default_name if default_name in THEMES else "klasik"


def _save_theme_setting(name):
    try:
        import json
        from pathlib import Path
        Path(_SETTINGS_PATH).parent.mkdir(parents=True, exist_ok=True)
        data = {}
        if Path(_SETTINGS_PATH).exists():
            try:
                data = json.loads(Path(_SETTINGS_PATH).read_text(encoding="utf-8"))
            except Exception:
                data = {}
        data["theme"] = name
        Path(_SETTINGS_PATH).write_text(json.dumps(data), encoding="utf-8")
    except Exception as exc:
        print("Ayar kaydedilemedi:", exc)


def _load_saved_voice(default_voice):
    try:
        import json
        from pathlib import Path
        data = json.loads(Path(_SETTINGS_PATH).read_text(encoding="utf-8"))
        return data.get("voice", default_voice)
    except Exception:
        return default_voice


# -------------------------------------------------------------- shaderlar
GEO_VERT = """
#version 330
in vec2 in_pos;
in vec4 in_col;
uniform vec2 resolution;
out vec4 v_col;
void main() {
    vec2 ndc = (in_pos / resolution) * 2.0 - 1.0;
    gl_Position = vec4(ndc.x, -ndc.y, 0.0, 1.0);
    v_col = in_col;
}
"""
GEO_FRAG = """
#version 330
in vec4 v_col;
out vec4 fragColor;
void main() { fragColor = v_col; }
"""

RECT_VERT = """
#version 330
in vec2 in_pos;
uniform vec2 resolution;
uniform vec4 rect;
out vec2 uv;
void main() {
    vec2 p = rect.xy + in_pos * rect.zw;
    vec2 ndc = (p / resolution) * 2.0 - 1.0;
    gl_Position = vec4(ndc.x, -ndc.y, 0.0, 1.0);
    uv = vec2(in_pos.x, 1.0 - in_pos.y);
}
"""

# Yazı dokuları: eski sürümdeki "ayna" düzeltmesi (V ters) aynen korundu.
TEXT_FRAG = """
#version 330
uniform sampler2D tex;
uniform float alpha;
in vec2 uv;
out vec4 fragColor;
void main() { fragColor = texture(tex, uv) * vec4(1.0, 1.0, 1.0, alpha); }
"""

GLOW_FRAG = """
#version 330
uniform vec4 color;
uniform float power;
in vec2 uv;
out vec4 fragColor;
void main() {
    float d = length(uv - 0.5) * 2.0;
    float a = pow(clamp(1.0 - d, 0.0, 1.0), power);
    fragColor = vec4(color.rgb, color.a * a);
}
"""

QUAD_VERT = """
#version 330
in vec2 in_pos;
out vec2 uv;
void main() {
    gl_Position = vec4(in_pos * 2.0 - 1.0, 0.0, 1.0);
    uv = in_pos;
}
"""
BRIGHT_FRAG = """
#version 330
uniform sampler2D src;
uniform float thr;
in vec2 uv;
out vec4 fragColor;
void main() {
    vec3 c = texture(src, uv).rgb;
    fragColor = vec4(max(c - vec3(thr), 0.0) * 1.6, 1.0);
}
"""
COPY_FRAG = """
#version 330
uniform sampler2D src;
in vec2 uv;
out vec4 fragColor;
void main() { fragColor = vec4(texture(src, uv).rgb, 1.0); }
"""
BLUR_FRAG = """
#version 330
uniform sampler2D src;
uniform vec2 dir;
in vec2 uv;
out vec4 fragColor;
void main() {
    vec3 c = texture(src, uv).rgb * 0.227027;
    c += texture(src, uv + dir * 1.3846).rgb * 0.3162162;
    c += texture(src, uv - dir * 1.3846).rgb * 0.3162162;
    c += texture(src, uv + dir * 3.2308).rgb * 0.0702703;
    c += texture(src, uv - dir * 3.2308).rgb * 0.0702703;
    fragColor = vec4(c, 1.0);
}
"""
COMPOSITE_FRAG = """
#version 330
uniform sampler2D scene;
uniform sampler2D bloom1;
uniform sampler2D bloom2;
uniform float time;
uniform float intensity;
in vec2 uv;
out vec4 fragColor;
void main() {
    vec3 c = texture(scene, uv).rgb;
    vec3 b = texture(bloom1, uv).rgb * 0.75 + texture(bloom2, uv).rgb * 1.0;
    c += b * intensity;

    vec2 q = uv - 0.5;
    c *= clamp(1.0 - dot(q, q) * 0.95, 0.0, 1.0);

    c *= 0.965 + 0.035 * sin(uv.y * 720.0 * 3.14159);

    float y = 1.0 - fract(time * 0.06);
    float band = exp(-pow((uv.y - y) * 26.0, 2.0));
    c += vec3(0.0, 0.10, 0.14) * band * 0.45;

    fragColor = vec4(c, 1.0);
}
"""

WEATHER_TEXT = {
    "clear": "AÇIK", "partly": "PARÇALI BULUTLU", "cloud": "BULUTLU",
    "fog": "SİSLİ", "rain": "YAĞMURLU", "snow": "KARLI", "storm": "FIRTINA",
}


def weather_kind(code):
    if code == 0:
        return "clear"
    if code in (1, 2):
        return "partly"
    if code == 3:
        return "cloud"
    if code in (45, 48):
        return "fog"
    if code in (71, 73, 75, 77, 85, 86):
        return "snow"
    if code in (95, 96, 99):
        return "storm"
    if code in (51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82):
        return "rain"
    return "cloud"


def _cfg(name, default):
    return getattr(config, name, default)


_UNIT_T = {}
_UNIT_CIRCLE = {}


def _unit_t(seg):
    a = _UNIT_T.get(seg)
    if a is None:
        a = _UNIT_T[seg] = np.linspace(0, 1, seg + 1, dtype="f4")
    return a


def _unit_circle(seg):
    c = _UNIT_CIRCLE.get(seg)
    if c is None:
        a = np.linspace(0, TAU, seg + 1, dtype="f4")
        c = _UNIT_CIRCLE[seg] = np.stack([np.cos(a), np.sin(a)], 1)
    return c


class GPUHUD:
    HIST = 48          # grafik başına örnek sayısı
    SAMPLE_DT = 0.5    # örnekleme aralığı (sn)

    def __init__(self, brain, width=1280, height=720,
                 ctx=None, target=None, start_threads=True):
        self.brain = brain
        self.width, self.height = W, H   # mantıksal tasarım boyutu

        pygame.font.init()
        if ctx is None:
            pygame.init()
            pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MAJOR_VERSION, 3)
            pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MINOR_VERSION, 3)
            pygame.display.gl_set_attribute(pygame.GL_DOUBLEBUFFER, 1)
            pygame.display.set_mode(
                (width, height),
                pygame.OPENGL | pygame.DOUBLEBUF | pygame.RESIZABLE,
            )
            pygame.display.set_caption("JARVIS")
            pygame.key.set_repeat(400, 35)
            self.ctx = moderngl.create_context()
            self.target = self.ctx.screen
            self.owns_window = True
        else:
            self.ctx = ctx
            self.target = target
            self.owns_window = False
        self.vp = (0, 0, W, H)
        self.scale = 1.0

        self._build_gl()
        self._build_fonts()

        # ---- durum
        self.input_text = ""
        self.status = "SYSTEM READY"
        self.messages = [("JARVIS", "Sistem hazır."),
                         ("JARVIS", "Komutunuzu bekliyorum...")]
        self.scroll = 0
        self.waiting = False
        self.active_queries = 0
        self.metric_focus = None
        self.metric_focus_until = 0.0
        self.metric_focus_since = 0.0
        self.running = True
        self.space_held = False
        self.queue = deque()
        self.text_cache = {}
        self._texts = []
        self._wrap_cache = {}
        self.fps = 0
        self.fps_frames = 0
        self.fps_time = time.time()
        self.t0 = time.time()
        self.last_frame = self.t0
        self.t = 0.0
        self.phase = 0.0
        self.activity = 0.0
        self.level_s = 0.0
        self.wave = deque([0.0] * 44, maxlen=44)
        self.last_wave = 0.0
        self.queries = 0
        self.last_reply = None
        self.llm_online = None      # None=bilinmiyor
        self.voice_error = False
        self.exit_rect = None

        # ---- ayarlar / tema
        self.show_settings = False
        self.tab_rects = []          # [(name, x, y, w, h), ...] draw_header doldurur
        self.settings_rects = []     # [(kind, value, x, y, w, h), ...] draw_settings_panel doldurur
        self.theme_name = _load_saved_theme(_cfg("HUD_THEME", "klasik"))
        self.apply_theme(self.theme_name, persist=False)
        try:
            import voice.speaker as _sp
            _saved_voice = _load_saved_voice("V3")
            if _saved_voice in getattr(_sp, "VOICE_PROFILES", {}):
                _sp.set_profile(_saved_voice)
            elif isinstance(_saved_voice, str) and _saved_voice:
                _sp.VOICE = _saved_voice
        except Exception:
            pass

        rng = np.random.default_rng(7)
        self.motes = np.stack([
            rng.uniform(0, W, 70), rng.uniform(0, H, 70),
            rng.uniform(4, 18, 70), rng.uniform(0, TAU, 70)], 1).astype("f4")

        # ---- sensör verisi
        n = self.HIST
        self.hist = {k: deque([0.0] * n, maxlen=n)
                     for k in ("cpu", "ram", "gpu", "temp")}
        self.cur = {"cpu": 0.0, "ram": 0.0, "gpu": None, "temp": None,
                    "down": 0.0, "up": 0.0, "disk_used": 0, "disk_total": 1,
                    "batt": None, "plugged": True}
        self.disp = {"cpu": 0.0, "ram": 0.0, "gpu": 0.0, "temp": 0.0}
        self.net_peak = 10.0
        self.last_sample_t = time.time()
        self.weather = None         # (sıcaklık, tür)
        self.drive_label = os.path.splitdrive(os.getcwd())[0] or "/"
        if self.drive_label.endswith(":"):
            self.drive_label += "\\"

        self.listener = VoiceListener(model_name="small")
        self.speaker = VoiceSpeaker()
        self.voice_ready = False

        if start_threads:
            for target_fn in (self._load_voice, self._sensor_loop,
                              self._weather_loop, self._llm_loop):
                threading.Thread(target=target_fn, daemon=True).start()

    # -------------------------------------------------------- ayarlar/tema
    def apply_theme(self, name, persist=True):
        """Renk paletini (modül seviyesi sabitler) canlı olarak değiştirir.
        Önbelleğe alınmış (statik) geometriler eski renkle çizilmiş olabileceği
        için önbelleği de temizleriz, bir sonraki karede yeni renkle yeniden
        üretilirler."""
        theme = THEMES.get(name, THEMES["klasik"])
        g = globals()
        for key, val in theme.items():
            g[key] = val
        self.theme_name = name
        if hasattr(self, "_static"):
            self._static.clear()
        if persist:
            _save_theme_setting(name)

    def set_voice(self, voice_id):
        import voice.speaker as sp
        profile = None
        if voice_id in getattr(sp, "VOICE_PROFILES", {}):
            profile = sp.set_profile(voice_id)
            selected = voice_id
        else:
            sp.VOICE = voice_id
            selected = voice_id
        try:
            import json
            from pathlib import Path
            Path(_SETTINGS_PATH).parent.mkdir(parents=True, exist_ok=True)
            data = {}
            if Path(_SETTINGS_PATH).exists():
                try: data = json.loads(Path(_SETTINGS_PATH).read_text(encoding="utf-8"))
                except Exception: data = {}
            data["voice"] = selected
            data["voice_profile"] = selected if profile else data.get("voice_profile")
            data["theme"] = self.theme_name
            Path(_SETTINGS_PATH).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            print("Ses ayarı kaydedilemedi:", exc)

    # ------------------------------------------------------------ GL kurulum
    def _build_gl(self):
        ctx = self.ctx
        ctx.enable(moderngl.BLEND)
        self.ALPHA = (moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA)
        self.ADD = (moderngl.SRC_ALPHA, moderngl.ONE)
        ctx.blend_func = self.ALPHA
        self._add = False

        self.geo_prog = ctx.program(vertex_shader=GEO_VERT,
                                    fragment_shader=GEO_FRAG)
        self._geo = []
        self._static = {}
        self._geo_size = 4 * 1024 * 1024
        self._make_geo_buffer()

        quad = ctx.buffer(np.array([0, 0, 1, 0, 0, 1, 1, 1], "f4").tobytes())
        self.quad = quad
        self.text_prog = ctx.program(vertex_shader=RECT_VERT,
                                     fragment_shader=TEXT_FRAG)
        self.glow_prog = ctx.program(vertex_shader=RECT_VERT,
                                     fragment_shader=GLOW_FRAG)
        self.text_vao = ctx.vertex_array(self.text_prog, [(quad, "2f", "in_pos")])
        self.glow_vao = ctx.vertex_array(self.glow_prog, [(quad, "2f", "in_pos")])

        self.post_ok = False
        if _cfg("ENABLE_BLOOM", True):
            try:
                self._build_post()
                self.post_ok = True
            except Exception as exc:       # eski/zayıf GPU: bloom'suz devam
                print("BLOOM kapalı:", exc)

    def _make_geo_buffer(self):
        self.geo_buf = self.ctx.buffer(reserve=self._geo_size, dynamic=True)
        self.geo_vao = self.ctx.vertex_array(
            self.geo_prog, [(self.geo_buf, "2f 4f", "in_pos", "in_col")])

    def _build_post(self):
        ctx = self.ctx

        def tex(size):
            t = ctx.texture(size, 4)
            t.filter = (moderngl.LINEAR, moderngl.LINEAR)
            t.repeat_x = False
            t.repeat_y = False
            return t, ctx.framebuffer([t])

        self.s1 = (W // 2, H // 2)
        self.s2 = (W // 4, H // 4)
        self.scene_tex, self.scene_fbo = tex((W, H))
        self.h_a, self.h_a_fbo = tex(self.s1)
        self.h_b, self.h_b_fbo = tex(self.s1)
        self.q_a, self.q_a_fbo = tex(self.s2)
        self.q_b, self.q_b_fbo = tex(self.s2)

        def prog(frag):
            p = ctx.program(vertex_shader=QUAD_VERT, fragment_shader=frag)
            return p, ctx.vertex_array(p, [(self.quad, "2f", "in_pos")])

        self.p_bright = prog(BRIGHT_FRAG)
        self.p_copy = prog(COPY_FRAG)
        self.p_blur = prog(BLUR_FRAG)
        self.p_comp = prog(COMPOSITE_FRAG)

    def _build_fonts(self):
        names = "bahnschrift,segoeui,consolas,dejavusansmono"

        def mk(size, bold=False):
            return pygame.font.SysFont(names, size, bold=bold)

        self.f_tiny = mk(12)
        self.f_small = mk(14)
        self.f_norm = mk(18)
        self.f_mid = mk(24)
        self.f_time = mk(40)
        self.f_big = mk(36, True)

    # --------------------------------------------------------- batch çizim
    def _flush(self):
        if not self._geo:
            return
        data = np.ascontiguousarray(np.concatenate(self._geo), dtype="f4")
        self._geo = []
        if data.nbytes > self._geo_size:
            self.geo_vao.release()
            self.geo_buf.release()
            self._geo_size = int(data.nbytes * 1.5)
            self._make_geo_buffer()
        self.geo_buf.write(data.tobytes())
        self.geo_prog["resolution"].value = (W, H)
        self.geo_vao.render(moderngl.TRIANGLES, vertices=len(data))

    def _set_add(self, on):
        if on != self._add:
            self._flush()
            self.ctx.blend_func = self.ADD if on else self.ALPHA
            self._add = on

    @contextmanager
    def additive(self):
        prev = self._add
        self._set_add(True)
        try:
            yield
        finally:
            self._set_add(prev)

    def _emit(self, pos, color):
        pos = np.asarray(pos, "f4").reshape(-1, 2)
        arr = np.empty((len(pos), 6), "f4")
        arr[:, :2] = pos
        arr[:, 2:] = color
        self._geo.append(arr)

    def _cached(self, key, build):
        """Değişmeyen geometriyi bir kez üretip her karede yeniden kullanır."""
        hit = self._static.get(key)
        if hit is None:
            start = len(self._geo)
            build()
            self._static[key] = list(self._geo[start:])
        else:
            self._geo.extend(hit)

    def rect(self, x, y, w, h, color):
        self._emit([(x, y), (x + w, y), (x, y + h),
                    (x + w, y), (x + w, y + h), (x, y + h)], color)

    def grad_rect(self, x, y, w, h, ctop, cbot):
        pos = np.array([(x, y), (x + w, y), (x, y + h),
                        (x + w, y), (x + w, y + h), (x, y + h)], "f4")
        cols = np.array([ctop, ctop, cbot, ctop, cbot, cbot], "f4")
        arr = np.empty((6, 6), "f4")
        arr[:, :2] = pos
        arr[:, 2:] = cols
        self._geo.append(arr)

    def lines(self, segs, width, color):
        s = np.asarray(segs, "f4").reshape(-1, 4)
        if not len(s):
            return
        p1, p2 = s[:, :2], s[:, 2:]
        d = p2 - p1
        length = np.maximum(np.hypot(d[:, 0], d[:, 1]), 1e-6)[:, None]
        n = np.stack([-d[:, 1], d[:, 0]], 1) / length * (width * 0.5)
        a, b, c, e = p1 + n, p1 - n, p2 + n, p2 - n
        pos = np.stack([a, b, c, b, e, c], 1).reshape(-1, 2)
        col = np.asarray(color, "f4")
        if col.ndim == 2:
            col = np.repeat(col, 6, 0)
        self._emit(pos, col)

    def polyline(self, pts, width, color, closed=False):
        p = np.asarray(pts, "f4").reshape(-1, 2)
        if closed:
            p = np.vstack([p, p[:1]])
        if len(p) < 2:
            return
        self.lines(np.hstack([p[:-1], p[1:]]), width, color)

    def arcs(self, cx, cy, r0, r1, starts, span, color, seg=None):
        starts = np.atleast_1d(np.asarray(starts, "f4"))
        if seg is None:
            seg = max(2, int(abs(span) * r1 / 5) + 1)
        t = _unit_t(seg)
        ang = starts[:, None] + span * t[None, :]
        co, si = np.cos(ang), np.sin(ang)
        o = np.stack([cx + co * r1, cy + si * r1], -1)
        i = np.stack([cx + co * r0, cy + si * r0], -1)
        o0, o1, i0, i1 = o[:, :-1], o[:, 1:], i[:, :-1], i[:, 1:]
        pos = np.stack([o0, i0, o1, i0, i1, o1], 2)
        self._emit(pos.reshape(-1, 2), color)

    def ring(self, cx, cy, r, width, color):
        self.arcs(cx, cy, r - width / 2, r + width / 2, [0.0], TAU, color,
                  seg=max(24, int(r * TAU / 6)))

    def disc(self, cx, cy, r, color, seg=32):
        p = _unit_circle(seg) * r + np.array([cx, cy], "f4")
        c = np.tile(np.array([[cx, cy]], "f4"), (seg, 1))
        pos = np.stack([c, p[:-1], p[1:]], 1)
        self._emit(pos.reshape(-1, 2), color)

    def area(self, pts, ybase, ctop, cbot):
        p = np.asarray(pts, "f4")
        if len(p) < 2:
            return
        a, b = p[:-1], p[1:]
        a0 = np.stack([a[:, 0], np.full(len(a), ybase, "f4")], 1)
        b0 = np.stack([b[:, 0], np.full(len(b), ybase, "f4")], 1)
        pos = np.stack([a, b, a0, b, b0, a0], 1).reshape(-1, 2)
        cols = np.tile(np.array([ctop, ctop, cbot, ctop, cbot, cbot], "f4"),
                       (len(a), 1))
        arr = np.empty((len(pos), 6), "f4")
        arr[:, :2] = pos
        arr[:, 2:] = cols
        self._geo.append(arr)

    def glow(self, cx, cy, r, color, power=2.0):
        self._flush()
        if not self._add:
            self.ctx.blend_func = self.ADD
        self.glow_prog["resolution"].value = (W, H)
        self.glow_prog["rect"].value = (cx - r, cy - r, 2 * r, 2 * r)
        self.glow_prog["color"].value = color
        self.glow_prog["power"].value = power
        self.glow_vao.render(moderngl.TRIANGLE_STRIP)
        if not self._add:
            self.ctx.blend_func = self.ALPHA

    # ---------------------------------------------------------------- yazı
    def text(self, value, x, y, font=None, color=WHITE, alpha=1.0, align="l"):
        self._texts.append((str(value), x, y, font or self.f_norm,
                            color, alpha, align))

    def _flush_text(self):
        self._flush()
        if not self._texts:
            return
        if self._add:
            self.ctx.blend_func = self.ALPHA
            self._add = False
        if len(self.text_cache) > 700:
            for t in self.text_cache.values():
                t.release()
            self.text_cache.clear()
        self.text_prog["resolution"].value = (W, H)
        self.text_prog["tex"].value = 0
        for value, x, y, font, color, alpha, align in self._texts:
            if not value:
                continue
            rgb = tuple(int(c * 255) for c in color[:3])
            key = (value, id(font), rgb)
            tex = self.text_cache.get(key)
            if tex is None:
                surf = font.render(value, True, rgb)
                to_bytes = getattr(pygame.image, "tobytes", None) or \
                    pygame.image.tostring
                tex = self.ctx.texture(
                    surf.get_size(), 4, to_bytes(surf, "RGBA", True))
                tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
                self.text_cache[key] = tex
            w, h = tex.size
            if align == "r":
                x0 = x - w
            elif align == "c":
                x0 = x - w / 2
            else:
                x0 = x
            tex.use(0)
            self.text_prog["rect"].value = (round(x0), round(y), w, h)
            self.text_prog["alpha"].value = alpha
            self.text_vao.render(moderngl.TRIANGLE_STRIP)
        self._texts = []

    def wrap_px(self, text, font, max_w):
        key = (text, id(font), max_w)
        hit = self._wrap_cache.get(key)
        if hit is not None:
            return hit
        out = []
        for para in str(text).splitlines() or [""]:
            words = para.split()
            if not words:
                out.append("")
                continue
            line = ""
            for w in words:
                while font.size(w)[0] > max_w and len(w) > 1:
                    k = len(w)
                    while k > 1 and font.size(w[:k])[0] > max_w:
                        k -= 1
                    if line:
                        out.append(line)
                        line = ""
                    out.append(w[:k])
                    w = w[k:]
                test = w if not line else line + " " + w
                if line and font.size(test)[0] > max_w:
                    out.append(line)
                    line = w
                else:
                    line = test
            if line:
                out.append(line)
        if len(self._wrap_cache) > 300:
            self._wrap_cache.clear()
        self._wrap_cache[key] = out
        return out

    # ------------------------------------------------------- arka plan işleri
    def _load_voice(self):
        try:
            self.listener.load()
            self.voice_ready = True
            self.status = "VOICE READY"
        except Exception as exc:
            self.voice_error = True
            self.status = "VOICE ERROR"
            print("VOICE:", exc)

    def _query_gpu(self):
        try:
            flags = 0x08000000 if os.name == "nt" else 0   # konsol penceresi açma
            out = subprocess.check_output(
                ["nvidia-smi",
                 "--query-gpu=utilization.gpu,temperature.gpu",
                 "--format=csv,noheader,nounits"],
                stderr=subprocess.DEVNULL, timeout=2,
                creationflags=flags).decode().strip().splitlines()[0]
            util, temp = [float(v) for v in out.split(",")[:2]]
            return util, temp
        except Exception:
            return None, None

    def _sensor_loop(self):
        psutil.cpu_percent(None)
        last_net = psutil.net_io_counters()
        last_t = time.time()
        last_slow = 0.0
        last_gpu = 0.0
        root = os.path.abspath(os.sep)
        while self.running:
            time.sleep(self.SAMPLE_DT)
            now = time.time()
            dt = max(now - last_t, 1e-3)
            cur = self.cur
            cur["cpu"] = psutil.cpu_percent(None)
            cur["ram"] = psutil.virtual_memory().percent
            net = psutil.net_io_counters()
            cur["down"] = (net.bytes_recv - last_net.bytes_recv) * 8 / 1e6 / dt
            cur["up"] = (net.bytes_sent - last_net.bytes_sent) * 8 / 1e6 / dt
            last_net, last_t = net, now
            self.net_peak = max(10.0, self.net_peak * 0.995,
                                cur["down"], cur["up"])
            if now - last_gpu >= 2.0:
                gpu_util, _gpu_temp = self._query_gpu()
                cur["gpu"] = gpu_util
                # TEMP kartı ve metric overlay CPU sıcaklığını göstermeli.
                try:
                    cur["temp"] = self.brain.tools._cpu_temperature()
                except Exception:
                    cur["temp"] = None
                last_gpu = now
            if now - last_slow >= 5.0:
                try:
                    du = psutil.disk_usage(root)
                    cur["disk_used"], cur["disk_total"] = du.used, du.total
                except Exception:
                    pass
                try:
                    b = psutil.sensors_battery()
                    cur["batt"] = None if b is None else b.percent
                    cur["plugged"] = True if b is None else bool(b.power_plugged)
                except Exception:
                    pass
                last_slow = now
            self.hist["cpu"].append(cur["cpu"])
            self.hist["ram"].append(cur["ram"])
            self.hist["gpu"].append(cur["gpu"] or 0.0)
            self.hist["temp"].append(cur["temp"] or 0.0)
            self.last_sample_t = now

    def _weather_loop(self):
        while self.running:
            try:
                r = requests.get(
                    "https://api.open-meteo.com/v1/forecast",
                    params={"latitude": _cfg("LOCATION_LAT", 41.0082),
                            "longitude": _cfg("LOCATION_LON", 28.9784),
                            "current": "temperature_2m,weather_code"},
                    timeout=6)
                cur = r.json()["current"]
                self.weather = (float(cur["temperature_2m"]),
                                weather_kind(int(cur["weather_code"])))
            except Exception:
                pass
            for _ in range(900):        # 15 dk
                if not self.running:
                    return
                time.sleep(1)

    def _llm_loop(self):
        base = str(getattr(config, "OLLAMA_URL", "")).split("/api")[0]
        while self.running:
            try:
                requests.get(base, timeout=1.5)
                self.llm_online = True
            except Exception:
                self.llm_online = False
            for _ in range(8):
                if not self.running:
                    return
                time.sleep(1)

    # ------------------------------------------------------------ mantık
    def add_message(self, who, text):
        self.messages.append((who, str(text)))
        self.messages = self.messages[-40:]
        self.scroll = 0

    def send(self, text):
        text = text.strip()
        if not text:
            return
        self.add_message("USER", text)
        self.input_text = ""
        self._start_query(text)

    def _start_query(self, text):
        self.active_queries += 1
        self.waiting = self.active_queries > 0
        low = text.lower()
        if any(k in low for k in ("işlemci sıcak", "cpu sıcak", "cpu sıcaklığı", "ram", "işlemci kullan", "cpu kullan")):
            if "ram" in low and "cpu" not in low and "işlemci" not in low:
                self.metric_focus = "ram"
            elif "sıcak" in low:
                self.metric_focus = "temp"
            else:
                self.metric_focus = "cpu"
            self.metric_focus_until = time.time() + 8.0
            self.metric_focus_since = time.time()
        self.status = ("RESEARCHING..." if self.brain.tools.web.should_search(text) else "PROCESSING...")
        threading.Thread(target=self._ask, args=(text,), daemon=True).start()

    def _ask(self, text):
        t0 = time.time()
        try:
            answer = self.brain.ask(text)
            self.last_reply = time.time() - t0
            self.llm_online = True
            self.queue.append(("ANSWER", answer))
        except Exception as exc:
            self.queue.append(("ERROR", str(exc)))

    def _record_voice(self):
        try:
            text = self.listener.stop()
            self.queue.append(("VOICE", text or "Sesi anlayamadım."))
        except Exception as exc:
            self.queue.append(("VOICE_ERROR", str(exc)))

    def drain(self):
        while self.queue:
            kind, value = self.queue.popleft()
            if kind == "VOICE":
                self.add_message("USER", value)
                self._start_query(value)
            elif kind == "VOICE_ERROR":
                self.add_message("JARVIS", "Mikrofon hatası: " + value)
                self.status = "SYSTEM READY"
            elif kind == "ERROR":
                self.add_message("JARVIS", "Hata: " + value)
                self.active_queries = max(0, self.active_queries - 1)
                self.waiting = self.active_queries > 0
                self.status = "PROCESSING..." if self.waiting else "SYSTEM READY"
            else:
                self.queries += 1
                self.add_message("JARVIS", value)
                self.active_queries = max(0, self.active_queries - 1)
                self.waiting = self.active_queries > 0
                self.status = "PROCESSING..." if self.waiting else "SYSTEM READY"
                self.speaker.speak(value)

    def _update(self, dt):
        self.t = time.time() - self.t0
        recording = self.listener.recording
        speaking = getattr(self.speaker, "speaking", False)
        target_act = 1.0 if (recording or self.waiting or speaking) else 0.0
        self.activity += (target_act - self.activity) * min(1.0, dt * 3.0)
        self.phase += dt * (1.0 + 3.2 * self.activity)

        if recording:
            tgt = float(getattr(self.listener, "level", 0.0))
        elif speaking:
            tgt = 0.30 + 0.45 * abs(math.sin(self.t * 7.3) *
                                    math.sin(self.t * 2.1 + 1.0))
        elif self.waiting:
            tgt = 0.12 + 0.10 * math.sin(self.t * 5.0)
        else:
            tgt = 0.0
        self.level_s += (tgt - self.level_s) * min(1.0, dt * 14.0)

        if self.t - self.last_wave > 1 / 42:
            self.last_wave = self.t
            jitter = 0.55 + 0.45 * np.random.random()
            self.wave.append(0.04 * np.random.random()
                             + min(1.0, self.level_s * 1.4) * jitter)

        for k in self.disp:
            v = self.cur.get(k)
            v = 0.0 if v is None else v
            self.disp[k] += (v - self.disp[k]) * min(1.0, dt * 6.0)

    # ================================================================ çizim
    def render(self):
        now = time.time()
        dt = min(now - self.last_frame, 0.1)
        self.last_frame = now
        self.fps_frames += 1
        if now - self.fps_time >= 1:
            self.fps = self.fps_frames
            self.fps_frames = 0
            self.fps_time = now
        self._update(dt)
        self._compute_viewport()

        if self.post_ok:
            self.scene_fbo.use()
        else:
            self.target.use()
            self.ctx.viewport = self.vp
        self.ctx.enable(moderngl.BLEND)
        self.ctx.blend_func = self.ALPHA
        self._add = False
        self.ctx.clear(*BG)

        self.draw_background()
        self.draw_reactor()
        self.draw_header()
        self.draw_left()
        self.draw_right()
        self.draw_console()
        self.draw_bottom()
        if self.metric_focus and time.time() < self.metric_focus_until:
            self.draw_metric_overlay()
        elif self.metric_focus:
            self.metric_focus = None
        if self.show_settings:
            self.draw_settings_panel()

        self._flush()
        self._flush_text()
        if self.post_ok:
            self._post()
        if self.owns_window:
            pygame.display.flip()

    def _compute_viewport(self):
        if not self.owns_window:
            self.vp, self.scale = (0, 0, W, H), 1.0
            return
        ww, wh = pygame.display.get_window_size()
        s = min(ww / W, wh / H)
        vw, vh = int(W * s), int(H * s)
        self.scale = s
        self.vp = ((ww - vw) // 2, (wh - vh) // 2, vw, vh)

    def _pass(self, pair, fbo, **uniforms):
        prog, vao = pair
        fbo.use()
        for k, v in uniforms.items():
            prog[k].value = v
        vao.render(moderngl.TRIANGLE_STRIP)

    def _post(self):
        ctx = self.ctx
        ctx.disable(moderngl.BLEND)
        w1, h1 = self.s1
        w2, h2 = self.s2

        self.scene_tex.use(0)
        self._pass(self.p_bright, self.h_a_fbo, src=0, thr=0.30)
        for _ in range(2):
            self.h_a.use(0)
            self._pass(self.p_blur, self.h_b_fbo, src=0, dir=(1.0 / w1, 0.0))
            self.h_b.use(0)
            self._pass(self.p_blur, self.h_a_fbo, src=0, dir=(0.0, 1.0 / h1))
        self.h_a.use(0)
        self._pass(self.p_copy, self.q_a_fbo, src=0)
        for _ in range(3):
            self.q_a.use(0)
            self._pass(self.p_blur, self.q_b_fbo, src=0, dir=(1.0 / w2, 0.0))
            self.q_b.use(0)
            self._pass(self.p_blur, self.q_a_fbo, src=0, dir=(0.0, 1.0 / h2))

        self.target.use()
        if self.owns_window:
            ww, wh = pygame.display.get_window_size()
            ctx.viewport = (0, 0, ww, wh)
            ctx.clear(0.0, 0.0, 0.0, 1.0)
        ctx.viewport = self.vp
        self.scene_tex.use(0)
        self.h_a.use(1)
        self.q_a.use(2)
        prog, vao = self.p_comp
        prog["scene"].value = 0
        prog["bloom1"].value = 1
        prog["bloom2"].value = 2
        prog["time"].value = self.t
        prog["intensity"].value = 0.85
        vao.render(moderngl.TRIANGLE_STRIP)
        ctx.enable(moderngl.BLEND)

    # ---------------------------------------------------------- arka plan
    def draw_background(self):
        cx, cy = 634, 272
        self.glow(cx, cy, 560, (0.0, 0.30, 0.42, 0.16), 1.6)

        grid = []
        for x in range(0, W + 1, 64):
            grid += [x, 0, x, H]
        for y in range(0, H + 1, 64):
            grid += [0, y, W, y]
        self.lines(grid, 1.0, C(SOFT, 0.10))

        # yavaşça yükselen parçacıklar
        m = self.motes
        m[:, 1] -= m[:, 2] * 0.016
        m[:, 0] += np.sin(self.t * 0.4 + m[:, 3]) * 0.12
        m[m[:, 1] < -4, 1] = H + 4
        tw = 0.25 + 0.30 * (0.5 + 0.5 * np.sin(self.t * 1.7 + m[:, 3] * 3))
        segs = np.stack([m[:, 0], m[:, 1], m[:, 0] + 1.8, m[:, 1]], 1)
        cols = np.zeros((len(m), 4), "f4")
        cols[:, :3] = CYAN
        cols[:, 3] = tw
        with self.additive():
            self.lines(segs, 1.8, cols)

    # ---------------------------------------------------------------- panel
    def panel(self, x, y, w, h, title=None, cut=10):
        self._cached(("panel", x, y, w, h, cut, bool(title)),
                     lambda: self._panel_geo(x, y, w, h, title, cut))
        if title:
            self.text(title, x + 14, y + 9, self.f_small, CYAN)

    def _panel_geo(self, x, y, w, h, title, cut):
        self.grad_rect(x, y, w, h, C(PANEL_T, 0.80), C(PANEL_B, 0.72))
        pts = [(x + cut, y), (x + w, y), (x + w, y + h - cut),
               (x + w - cut, y + h), (x, y + h), (x, y + cut)]
        self.polyline(pts, 1.0, C(SOFT, 0.85), closed=True)
        br = C(CYAN, 0.95)
        self.polyline([(x, y + cut + 14), (x, y + cut), (x + cut, y),
                       (x + cut + 26, y)], 1.8, br)
        self.polyline([(x + w, y + h - cut - 14), (x + w, y + h - cut),
                       (x + w - cut, y + h), (x + w - cut - 26, y + h)],
                      1.8, br)
        self.polyline([(x + w - 16, y), (x + w, y), (x + w, y + 12)],
                      1.4, C(CYAN, 0.6))
        self.polyline([(x, y + h - 12), (x, y + h), (x + 16, y + h)],
                      1.4, C(CYAN, 0.6))
        if title:
            self.lines([(x + 12, y + 30, x + w - 12, y + 30)], 1.0,
                       C(SOFT, 0.30))

    # ---------------------------------------------------------------- header
    def draw_header(self):
        self.text("J.A.R.V.I.S.", 30, 12, self.f_big, CYAN)
        self.text("v" + _JARVIS_VERSION, 30 + self.f_big.size("J.A.R.V.I.S.")[0] + 10, 26,
                  self.f_tiny, SOFT)

        x = 380
        self.tab_rects = []
        for i, name in enumerate(["SYSTEM", "TOOLS", "RESEARCH",
                                  "MEMORY", "CONTROL", "SETTINGS"]):
            wd = self.f_small.size(name)[0]
            active = (name == "SETTINGS") if self.show_settings else (i == 0)
            self.text(name, x, 26, self.f_small, BRIGHT if active else WHITE,
                      1.0 if active else 0.75)
            if active:
                self.lines([(x, 46, x + wd, 46)], 2.0, C(CYAN, 0.95))
            self.tab_rects.append((name, x - 6, 16, wd + 12, 32))
            x += wd + 34

        self.text(time.strftime("%a %d %b %Y").upper(), 1122, 16,
                  self.f_small, SOFT, align="r")
        self.text(time.strftime("%H:%M:%S"), 1122, 30, self.f_time, BRIGHT,
                  align="r")
        self.lines([(1140, 18, 1140, 62)], 1.0, C(SOFT, 0.6))

        name = str(_cfg("LOCATION_NAME", "İSTANBUL, TR"))
        self.text(name, 1150, 14, self.f_tiny, SOFT)
        if self.weather:
            temp, kind = self.weather
            self.weather_icon(kind, 1168, 46)
            self.text(f"{temp:.0f}°C", 1192, 32, self.f_mid, BRIGHT)
            self.text(WEATHER_TEXT.get(kind, ""), 1150, 62, self.f_tiny, SOFT)
        else:
            self.weather_icon("cloud", 1168, 46, 0.5)
            self.text("--°C", 1192, 32, self.f_mid, SOFT)
            self.text("BAĞLANTI YOK", 1150, 62, self.f_tiny, SOFT)

    def weather_icon(self, kind, cx, cy, alpha=1.0):
        col = C(BRIGHT, 0.9 * alpha)
        sun = C(WARN, 0.9 * alpha) if kind in ("clear", "partly") else None

        def cloud(dx, dy):
            self.disc(cx + dx - 7, cy + dy + 3, 5, col)
            self.disc(cx + dx, cy + dy - 2, 7, col)
            self.disc(cx + dx + 8, cy + dy + 2, 5, col)
            self.rect(cx + dx - 8, cy + dy + 2, 17, 6, col)

        if kind == "clear":
            self.disc(cx, cy, 7, sun)
            a = np.arange(8) * TAU / 8
            self.lines(np.stack([cx + np.cos(a) * 10, cy + np.sin(a) * 10,
                                 cx + np.cos(a) * 14, cy + np.sin(a) * 14], 1),
                       1.6, sun)
        elif kind == "partly":
            self.disc(cx + 6, cy - 6, 6, sun)
            cloud(-1, 2)
        elif kind == "fog":
            for i in range(3):
                self.lines([(cx - 12, cy - 6 + i * 6, cx + 12, cy - 6 + i * 6)],
                           2.0, col)
        else:
            cloud(0, -4)
            if kind == "rain":
                self.lines([(cx - 7 + i * 7, cy + 8, cx - 9 + i * 7, cy + 14)
                            for i in range(3)], 1.6, C(CYAN, alpha))
            elif kind == "snow":
                for i in range(3):
                    self.disc(cx - 7 + i * 7, cy + 12, 1.6, col, 8)
            elif kind == "storm":
                self.polyline([(cx + 1, cy + 6), (cx - 3, cy + 12),
                               (cx + 2, cy + 12), (cx - 2, cy + 18)],
                              1.8, C(WARN, alpha))

    # ------------------------------------------------------ sol sütun
    def sparkline(self, key, x, y, w, h, color, vmax=100.0):
        hist = self.hist[key]
        self.lines([(x, y + h, x + w, y + h)], 1.0, C(SOFT, 0.5))
        self.lines([(x, y + h / 2, x + w, y + h / 2)], 1.0, C(SOFT, 0.16))
        n = len(hist)
        dx = w / (self.HIST - 1)
        frac = min(1.0, max(0.0, (time.time() - self.last_sample_t)
                            / self.SAMPLE_DT))
        vals = np.array(list(hist), "f4")   # thread-safe kopya
        xs = x + w - (n - 1 - np.arange(n)) * dx - frac * dx
        ys = y + h - np.clip(vals / vmax, 0, 1) * (h - 2)
        pts = np.stack([xs, ys], 1)
        pts = np.vstack([pts, [x + w, ys[-1]]])
        keep = pts[:, 0] >= x
        idx = int(np.argmax(keep))
        if idx > 0:
            p0, p1 = pts[idx - 1], pts[idx]
            tt = (x - p0[0]) / (p1[0] - p0[0])
            edge = [x, p0[1] + (p1[1] - p0[1]) * tt]
            pts = np.vstack([edge, pts[idx:]])
        self.area(pts, y + h, C(color, 0.30), C(color, 0.0))
        self.polyline(pts, 1.6, C(color, 0.95))
        self.disc(pts[-1][0], pts[-1][1], 2.2, C(BRIGHT, 1.0), 10)

    def gauge(self, cx, cy, r, value, color):
        self.ring(cx, cy, r, 4, C(SOFT, 0.35))
        value = max(0.0, min(1.0, value))
        if value > 0.004:
            col = RED if value > 0.9 else color
            self.arcs(cx, cy, r - 2, r + 2, [-math.pi / 2], TAU * value,
                      C(col, 1.0))

    def icon(self, kind, cx, cy, col):
        c = C(col, 0.95)
        if kind == "cpu":
            self.polyline([(cx - 6, cy - 6), (cx + 6, cy - 6), (cx + 6, cy + 6),
                           (cx - 6, cy + 6)], 1.4, c, closed=True)
            self.polyline([(cx - 2.5, cy - 2.5), (cx + 2.5, cy - 2.5),
                           (cx + 2.5, cy + 2.5), (cx - 2.5, cy + 2.5)],
                          1.2, c, closed=True)
            pins = []
            for o in (-3, 0, 3):
                pins += [cx + o, cy - 6, cx + o, cy - 9,
                         cx + o, cy + 6, cx + o, cy + 9,
                         cx - 6, cy + o, cx - 9, cy + o,
                         cx + 6, cy + o, cx + 9, cy + o]
            self.lines(pins, 1.0, c)
        elif kind == "ram":
            self.polyline([(cx - 9, cy - 5), (cx + 9, cy - 5), (cx + 9, cy + 4),
                           (cx - 9, cy + 4)], 1.4, c, closed=True)
            self.lines([(cx + o, cy - 2, cx + o, cy + 2)
                        for o in (-5, -1.5, 2, 5.5)], 1.6, c)
            self.lines([(cx + o, cy + 4, cx + o, cy + 8)
                        for o in (-6, -3, 0, 3, 6)], 1.0, c)
        elif kind == "gpu":
            self.polyline([(cx - 10, cy - 6), (cx + 10, cy - 6), (cx + 10, cy + 5),
                           (cx - 10, cy + 5)], 1.4, c, closed=True)
            self.ring(cx + 2, cy - 0.5, 3.6, 1.4, c)
            self.lines([(cx - 10, cy + 8, cx + 6, cy + 8)], 1.2, c)
        else:  # temp
            self.polyline([(cx - 2.5, cy + 3), (cx - 2.5, cy - 8), (cx + 2.5, cy - 8),
                           (cx + 2.5, cy + 3)], 1.4, c)
            self.ring(cx, cy + 6, 4.5, 1.6, c)
            self.disc(cx, cy + 6, 2.4, c, 12)
            self.lines([(cx, cy + 3, cx, cy - 4)], 1.6, c)

    def draw_left(self):
        x, w = 16, 242
        self.panel(x, 74, w, 248, "SYSTEM STATUS")
        rows = [
            ("cpu", "CPU", self.disp["cpu"], self.cur["cpu"], "%", CYAN, 100),
            ("ram", "RAM", self.disp["ram"], self.cur["ram"], "%", CYAN, 100),
            ("gpu", "GPU", self.disp["gpu"], self.cur["gpu"], "%", CYAN, 100),
            ("temp", "CPU TEMP", self.disp["temp"], self.cur["temp"], "°C", WARN, 110),
        ]
        for i, (key, label, dv, raw, unit, col, vmax) in enumerate(rows):
            ry = 74 + 40 + i * 51
            cx, cy = x + 36, ry + 22
            self.gauge(cx, cy, 19, dv / vmax if raw is not None else 0, col)
            self.icon(key, cx, cy, col)
            self.text(label, x + 66, ry + 1, self.f_tiny, SOFT)
            val = "--" if raw is None else f"{dv:.0f}{unit}"
            self.text(val, x + 66, ry + 15, self.f_mid, BRIGHT)
            self.sparkline(key, x + 134, ry + 4, 94, 38, col, vmax)

        # --- STORAGE
        self.panel(x, 332, w, 62, "STORAGE")
        used, total = self.cur["disk_used"], max(1, self.cur["disk_total"])
        pct = used / total
        self.text(self.drive_label, x + 14, 358, self.f_small, WHITE)
        self.bar(x + 62, 363, 116, 7, pct, CYAN)
        self.text(f"{pct * 100:.0f}%", x + w - 14, 354, self.f_norm, BRIGHT,
                  align="r")
        self.text(f"{used / 1e9:.0f} GB / {total / 1e9:.0f} GB",
                  x + 62, 374, self.f_tiny, SOFT)

        # --- NETWORK
        self.panel(x, 402, w, 62, "NETWORK")
        d, u = self.cur["down"], self.cur["up"]
        self.text("NET", x + 14, 436, self.f_small, WHITE)
        self.bar(x + 52, 441, 84, 6, min(1.0, max(d, u) / self.net_peak), CYAN)
        for j, (val, up) in enumerate(((d, False), (u, True))):
            yy = 424 + j * 16
            self.arrow(x + w - 92, yy + 8, up, GREEN if up else CYAN)
            self.text(f"{val:.1f} Mbps", x + w - 14, yy, self.f_tiny, WHITE,
                      align="r")

        # --- POWER
        self.panel(x, 472, w, 48, "POWER")
        batt = self.cur["batt"]
        pct = 1.0 if batt is None else batt / 100.0
        segs = 10
        for i in range(segs):
            on = (i + 0.5) / segs <= pct
            col = GREEN if pct > 0.2 else RED
            self.rect(x + 14 + i * 8, 500, 6, 10,
                      C(col, 0.95) if on else C(SOFT, 0.35))
        self.rect(x + 14 + segs * 8, 503, 2, 4, C(SOFT, 0.8))
        label = ("AC CONNECTED" if self.cur["plugged"] else "BATTERY") \
            if batt is not None else "AC POWER"
        self.text(label, x + 108, 498, self.f_tiny, WHITE)
        self.text(f"{pct * 100:.0f}%", x + w - 14, 496, self.f_norm, BRIGHT,
                  align="r")

        self.draw_stark(x, 534, w, 150)

    def bar(self, x, y, w, h, value, color):
        self.rect(x, y, w, h, C(SOFT, 0.30))
        wf = max(0.0, min(1.0, value)) * w
        if wf > 0.5:
            self.rect(x, y, wf, h, C(color, 0.95))
            self.rect(x, y, wf, h * 0.4, C(BRIGHT, 0.25))
        self.polyline([(x, y), (x + w, y), (x + w, y + h), (x, y + h)],
                      1.0, C(SOFT, 0.7), closed=True)

    def arrow(self, cx, cy, up, color):
        s = 4
        if up:
            pts = [(cx, cy - s), (cx + s, cy + s * 0.6), (cx - s, cy + s * 0.6)]
        else:
            pts = [(cx, cy + s), (cx + s, cy - s * 0.6), (cx - s, cy - s * 0.6)]
        self._emit(np.array(pts, "f4"), C(color, 0.95))

    def draw_stark(self, x, y, w, h):
        self.panel(x, y, w, h)
        self._cached(("skyline", x, y), lambda: self._skyline(x, y))
        self._stark_text(x, y)

    def _skyline(self, x, y):
        # tel kafes şehir silueti
        bx, by = x + 12, y + 108
        blds = [(0, 16, 40), (18, 14, 62), (34, 20, 84), (56, 14, 50),
                (72, 18, 96), (92, 14, 56), (108, 16, 36)]
        for off, bw, bh in blds:
            l, r, top = bx + off, bx + off + bw, by - bh
            self.polyline([(l, by), (l, top), (r, top), (r, by)], 1.0,
                          C(CYAN, 0.85))
            self.lines([(l + bw / 2, top, l + bw / 2, by)], 1.0, C(SOFT, 0.6))
            fl = [(l, yy, r, yy) for yy in np.arange(by - 7, top + 2, -7)]
            self.lines(fl, 1.0, C(SOFT, 0.55))
        self.lines([(bx - 4, by, bx + 128, by)], 1.2, C(CYAN, 0.9))
        self.lines([(bx + 81, by - 96, bx + 81, by - 108)], 1.2, C(CYAN, 0.9))

    def _stark_text(self, x, y):
        self.text("STARK INDUSTRIES", x + 14, y + 118, self.f_small, BRIGHT)
        self.text("A BRIGHTER TOMORROW", x + 14, y + 134, self.f_tiny, SOFT)

        items = ["HOME", "LAB", "HANGAR", "WORKSHOP", "SECURITY", "EXIT"]
        ix = x + 152
        for i, name in enumerate(items):
            iy = y + 16 + i * 17
            on = i == 0
            self.ring(ix, iy + 7, 3.4, 1.4, C(CYAN if on else SOFT, 0.95))
            if on:
                self.disc(ix, iy + 7, 1.8, C(CYAN, 1.0), 8)
            self.text(name, ix + 12, iy, self.f_tiny,
                      BRIGHT if on else WHITE, 1.0 if on else 0.7)
            if name == "EXIT":
                self.exit_rect = (ix - 8, iy - 3, 80, 17)

    # ------------------------------------------------------ sağ sütun
    def status_dot(self, x, y, state):
        col = {True: GREEN, False: RED, None: WARN}[state]
        self.ring(x, y, 5.2, 1.4, C(col, 0.9))
        self.disc(x, y, 2.4, C(col, 1.0), 10)

    def draw_right(self):
        x, w = 1010, 254
        self.panel(x, 76, w, 172, "AI MODULES")
        if self.voice_ready:
            voice = True
        elif self.voice_error:
            voice = False
        else:
            voice = None
        mods = [
            ("VOICE RECOGNITION", voice),
            ("LANGUAGE MODEL (QWEN)", self.llm_online),
            ("WEB SEARCH", bool(self.brain.tools.web.available)),
            ("MEMORY SYSTEM", getattr(self.brain, "memory", None) is not None),
            ("WINDOWS CONTROL", os.name == "nt"),
        ]
        for i, (name, ok) in enumerate(mods):
            yy = 76 + 40 + i * 25
            self.text(name, x + 14, yy, self.f_tiny, WHITE)
            word = {True: "ONLINE", False: "OFFLINE", None: "LOADING"}[ok]
            col = {True: GREEN, False: RED, None: WARN}[ok]
            self.text(word, x + w - 30, yy, self.f_tiny, col, align="r")
            self.status_dot(x + w - 16, yy + 7, ok)

        self.panel(x, 258, w, 118, "SESSION")
        up = int(time.time() - self.t0)
        rows = [
            ("UPTIME", f"{up // 3600:02d}:{up // 60 % 60:02d}:{up % 60:02d}"),
            ("MODEL", str(getattr(config, "MODEL", "?")).upper()),
            ("REPLIES", str(self.queries)),
            ("LAST REPLY", "--" if self.last_reply is None
             else f"{self.last_reply:.1f} s"),
        ]
        for i, (k, v) in enumerate(rows):
            yy = 258 + 40 + i * 18
            self.text(k, x + 14, yy, self.f_tiny, SOFT)
            self.text(v, x + w - 14, yy, self.f_tiny, WHITE, align="r")

        self.draw_map(x, 388, w, 296)

    def draw_map(self, x, y, w, h):
        self.panel(x, y, w, h, "LOCAL MAP")
        cx, cy, R = x + w / 2, y + 122, 84
        t = self.t
        self.glow(cx, cy, R + 30, C(CYAN, 0.10), 2.0)
        for r in (R, R * 0.72, R * 0.44):
            self.ring(cx, cy, r, 1.0, C(SOFT, 0.9))
        self.ring(cx, cy, R + 8, 1.2, C(CYAN, 0.55))
        # harita ızgarası
        g = []
        for k in np.linspace(-R * 0.85, R * 0.85, 7):
            hh = math.sqrt(max(R * R - k * k, 0)) * 0.98
            g += [cx + k, cy - hh, cx + k, cy + hh, cx - hh, cy + k, cx + hh, cy + k]
        self.lines(g, 1.0, C(SOFT, 0.22))
        self.lines([(cx - R - 6, cy, cx + R + 6, cy),
                    (cx, cy - R - 6, cx, cy + R + 6)], 1.0, C(CYAN, 0.35))
        # tarama süpürmesi
        ang = t * 1.1
        with self.additive():
            for i in range(14):
                a1 = ang - i * 0.06
                self.arcs(cx, cy, 0, R, [a1], -0.06, C(CYAN, 0.20 * (1 - i / 14)), seg=3)
            self.lines([(cx, cy, cx + math.cos(ang) * R, cy + math.sin(ang) * R)],
                       1.6, C(CYAN, 0.9))
        # mavi noktalar
        rng = np.random.default_rng(3)
        for i in range(9):
            a, rr = rng.uniform(0, TAU), rng.uniform(0.2, 0.9) * R
            self.disc(cx + math.cos(a) * rr, cy + math.sin(a) * rr, 1.8,
                      C(CYAN, 0.55), 8)
        # konum iğnesi
        pulse = 5 + 7 * (0.5 + 0.5 * math.sin(t * 2.5))
        self.ring(cx, cy, 6 + pulse, 1.2, C(GREEN, 0.6 * (1 - pulse / 14)))
        self.ring(cx, cy, 6, 1.6, C(GREEN, 0.95))
        self.disc(cx, cy, 2.6, C(GREEN, 1.0), 10)

        name = str(_cfg("LOCATION_NAME", "İSTANBUL, TR"))
        lat, lon = _cfg("LOCATION_LAT", 41.0082), _cfg("LOCATION_LON", 28.9784)
        self.text(name, cx, y + 216, self.f_small, BRIGHT, align="c")
        self.text(f"{abs(lat):.4f}° {'N' if lat >= 0 else 'S'}   "
                  f"{abs(lon):.4f}° {'E' if lon >= 0 else 'W'}",
                  cx, y + 234, self.f_tiny, SOFT, align="c")
        self.text("“SOMETIMES YOU GOTTA RUN", x + w - 14, y + 258,
                  self.f_tiny, SOFT, align="r")
        self.text("BEFORE YOU CAN WALK.”", x + w - 14, y + 271,
                  self.f_tiny, SOFT, align="r")

    # ------------------------------------------------------------ eski reaktör animasyonu
    def circle(self, cx, cy, radius, color, segments=96):
        a = np.linspace(0.0, TAU, segments + 1, dtype="f4")
        pts = np.stack([cx + np.cos(a) * radius, cy + np.sin(a) * radius], 1)
        self.polyline(pts, 1.0, color, closed=False)

    def ellipse(self, cx, cy, rx, ry, rotation, color, segments=96):
        a = np.linspace(0.0, TAU, segments + 1, dtype="f4")
        cr, sr = math.cos(rotation), math.sin(rotation)
        x, y = np.cos(a) * rx, np.sin(a) * ry
        pts = np.stack([cx + x * cr - y * sr, cy + x * sr + y * cr], 1)
        self.polyline(pts, 1.0, color, closed=False)

    def draw_reactor(self):
        # v0.6'nın orijinal reaktör animasyonu geri getirildi.
        cx, cy = self.width // 2, 292
        t = time.time()
        pulse = 9 + 8 * (0.5 + 0.5 * math.sin(t * 2.4))
        core = GREEN if self.listener.recording else CYAN

        # Soft multi-ring holographic glow.
        for r, alpha in [
            (126, 0.05), (112, 0.07), (98, 0.09), (86, 0.11), (74, 0.14),
        ]:
            self.circle(cx, cy, r, C(core, alpha), 72)

        self.circle(cx, cy, 180, C(SOFT, 0.95), 112)
        self.circle(cx, cy, 161, C(CYAN, 0.95), 112)
        self.circle(cx, cy, 141, C(SOFT, 0.95), 104)
        self.circle(cx, cy, 111, C(CYAN, 0.95), 96)

        # Depth-like orbital rings.
        self.ellipse(cx, cy, 168, 40, t * 0.22, C(SOFT, 0.95), 96)
        self.ellipse(cx, cy, 145, 31, -t * 0.36, C(CYAN, 0.95), 96)
        self.ellipse(cx, cy, 118, 24, t * 0.50, C(SOFT, 0.95), 96)

        # Outer ticks.
        ticks = []
        for i in range(54):
            a = t * 0.16 + i * TAU / 54
            r1 = 166 if i % 3 else 158
            r2 = 174 if i % 3 else 181
            ticks.extend([
                cx + math.cos(a) * r1, cy + math.sin(a) * r1,
                cx + math.cos(a) * r2, cy + math.sin(a) * r2,
            ])
        self.lines(np.asarray(ticks, "f4").reshape(-1, 4), 1.0, C(CYAN, 0.95))

        # Moving particles (v0.6).
        particles = []
        for i in range(28):
            a = t * (0.20 + (i % 5) * 0.018) + i * TAU / 28
            r = 122 + 9 * math.sin(t * 1.6 + i * 0.7)
            px = cx + math.cos(a) * r
            py = cy + math.sin(a) * r
            particles.extend([px - 2, py, px + 2, py])
        self.lines(np.asarray(particles, "f4").reshape(-1, 4), 1.0, C(CYAN, 0.95))

        # Core rays.
        rays = []
        for i in range(18):
            a = -t * 0.75 + i * TAU / 18
            r1 = 35
            r2 = 58 + 12 * math.sin(t * 2 + i)
            rays.extend([
                cx + math.cos(a) * r1, cy + math.sin(a) * r1,
                cx + math.cos(a) * r2, cy + math.sin(a) * r2,
            ])
        self.lines(np.asarray(rays, "f4").reshape(-1, 4), 1.0, C(core, 0.95))

        self.circle(cx, cy, 51 + pulse, C(core, 0.32), 80)
        self.circle(cx, cy, 44 + pulse * 0.35, C(core, 0.95), 96)
        self.circle(cx, cy, 30, C(SOFT, 0.95), 80)

        self.text("J A R V I S", cx, cy - 10, self.f_norm, core, align="c")
        self.text(self.status, cx, cy + 66, self.f_tiny, SOFT, align="c")

    # ------------------------------------------------------------ konsol
    def draw_console(self):
        x, y, w, h = 288, 503, 692, 124
        self.panel(x, y, w, h)
        self.text("COMMUNICATION CONSOLE", x + 16, y + 9, self.f_small, CYAN)

        st = self.status
        scol = RED if "ERROR" in st else GREEN if "LISTENING" in st else CYAN
        self.status_dot(x + w - 20, y + 16, None if self.waiting else True)
        self.text(st, x + w - 34, y + 9, self.f_small, scol, align="r")
        self.lines([(x + 12, y + 30, x + w - 12, y + 30)], 1.0, C(SOFT, 0.30))

        # mesajlar
        wave_w = 150
        text_w = w - 14 - 74 - wave_w - 26
        lines = []
        for who, msg in self.messages:
            wrapped = self.wrap_px(msg, self.f_small, text_w)
            for j, ln in enumerate(wrapped):
                lines.append((who if j == 0 else None, ln))
        visible = 4
        self.scroll = max(0, min(self.scroll, max(0, len(lines) - visible)))
        end = len(lines) - self.scroll
        shown = lines[max(0, end - visible):end]
        for i, (who, ln) in enumerate(shown):
            yy = y + 38 + i * 19
            if who:
                self.text(who + ":", x + 16, yy, self.f_small,
                          CYAN if who == "JARVIS" else WHITE)
            self.text(ln, x + 16 + 74, yy, self.f_small, WHITE)
        if self.scroll > 0:
            self.arrow(x + 16 + 74 + text_w + 6, y + 40, True, SOFT)
        if end > visible:
            self.arrow(x + 16 + 74 + text_w + 6, y + 104, False, SOFT)

        self.draw_wave(x + w - wave_w - 14, y + 78, wave_w, 40)

        # giriş kutusu
        iy = y + h + 9
        self.grad_rect(x, iy, w, 32, C(PANEL_T, 0.85), C(PANEL_B, 0.85))
        self.polyline([(x + 8, iy), (x + w, iy), (x + w, iy + 32 - 8),
                       (x + w - 8, iy + 32), (x, iy + 32), (x, iy + 8)],
                      1.0, C(SOFT, 0.9), closed=True)
        self.text("›", x + 14, iy + 5, self.f_norm, CYAN)
        if self.input_text:
            self.text(self.input_text[-70:], x + 32, iy + 8, self.f_small, BRIGHT)
            tw = self.f_small.size(self.input_text[-70:])[0]
        else:
            self.text("Komutunuzu söyleyin veya yazın...", x + 32, iy + 8,
                      self.f_small, SOFT)
            tw = 0
        if int(self.t * 2) % 2 == 0:
            self.rect(x + 34 + tw, iy + 7, 2, 18, C(CYAN, 0.95))
        self.draw_mic(x + w - 24, iy + 16)

    def draw_mic(self, cx, cy):
        rec = self.listener.recording
        col = C(GREEN if rec else CYAN, 0.95 if rec else 0.7)
        self.rect(cx - 3, cy - 8, 6, 9, col)
        self.disc(cx, cy - 8, 3, col, 10)
        self.disc(cx, cy + 1, 3, col, 10)
        self.arcs(cx, cy - 1, 5, 6.4, [0.05], math.pi - 0.1, col, seg=10)
        self.lines([(cx, cy + 6, cx, cy + 9), (cx - 4, cy + 9, cx + 4, cy + 9)],
                   1.4, col)
        if rec:
            self.ring(cx, cy - 2, 14 + 3 * math.sin(self.t * 8), 1.2,
                      C(GREEN, 0.6))

    def draw_wave(self, x, y, w, h):
        rec = self.listener.recording
        col = GREEN if rec else CYAN
        n = len(self.wave)
        step = w / n
        vals = np.array(self.wave, "f4")
        xs = x + step * (np.arange(n) + 0.5)
        env = np.sin(np.pi * (np.arange(n) + 0.5) / n) ** 0.6
        amp = (1.5 + vals * h * 0.5) * (0.35 + 0.65 * env)
        cols = np.zeros((n, 4), "f4")
        cols[:, :3] = col
        cols[:, 3] = 0.35 + 0.65 * env
        segs = np.stack([xs, y - amp, xs, y + amp], 1)
        with self.additive():
            self.lines(segs, max(1.5, step * 0.55), cols)
        self.lines([(x, y, x + w, y)], 1.0, C(SOFT, 0.35))

    # ------------------------------------------------------------ alt bar
    # --------------------------------------------------------------- ayarlar
    def _settings_button(self, x, y, w, h, label, active, kind, value):
        fill = C(CYAN, 0.28) if active else C(PANEL_T, 0.85)
        self.rect(x, y, w, h, fill)
        self.polyline([(x, y), (x + w, y), (x + w, y + h), (x, y + h)],
                      1.4, C(CYAN if active else SOFT, 0.9), closed=True)
        self.text(label, x + w / 2, y + h / 2 - 7, self.f_small,
                  BRIGHT if active else WHITE, align="c")
        self.settings_rects.append((kind, value, x, y, w, h))

    def draw_settings_panel(self):
        x, y, w, h = 320, 130, 640, 400
        # arkayı hafif karart, panel dikkat çeksin
        self.rect(0, 0, W, H, (0.0, 0.0, 0.0, 0.55))
        self.panel(x, y, w, h, title="AYARLAR")
        self.settings_rects = []

        self.text("HUD TEMASI", x + 28, y + 56, self.f_small, SOFT)
        bx = x + 28
        by = y + 82
        labels = {"klasik": "KLASIK (Mavi/Camgöbeği)",
                  "reaktor": "REAKTÖR (Siyah/Elektrik Mavisi)"}
        for name in THEME_NAMES:
            self._settings_button(bx, by, w - 56, 44, labels.get(name, name),
                                  self.theme_name == name, "theme", name)
            by += 54

        by += 14
        self.text("JARVIS SESI", x + 28, by, self.f_small, SOFT)
        by += 26
        import voice.speaker as _sp
        current_voice = _load_saved_voice("V3")
        voices = [("V1", "V1 — Ahmet/Hızlı"), ("V2", "V2 — Emel/Dengeli"), ("V3", "V3 — Ahmet/Tok")]
        vx = x + 28
        vw = (w - 56 - 12) / 2
        for i, (vid, label) in enumerate(voices):
            self._settings_button(vx + i * (vw + 12), by, vw, 44, label,
                                  current_voice == vid, "voice", vid)

        self._settings_button(x + w - 148, y + h - 56, 120, 36, "KAPAT",
                              False, "close", None)

    def draw_metric_overlay(self):
        """Sorulan sistem metriğini kısa ve animasyonlu bir panel olarak göster."""
        metric = self.metric_focus
        if metric not in ("cpu", "ram", "temp"):
            return

        now = time.time()
        age = max(0.0, now - self.metric_focus_since)
        enter = min(1.0, age / 0.28)
        # Smoothstep easing: panel aşağıdan yumuşakça yerine otursun.
        enter = enter * enter * (3.0 - 2.0 * enter)
        x, target_y, w, h = 318, 352, 644, 128
        y = target_y + (1.0 - enter) * 24.0

        self.panel(x, y, w, h, "LIVE TELEMETRY")
        labels = {"cpu": "CPU KULLANIMI", "ram": "RAM KULLANIMI",
                  "temp": "CPU SICAKLIĞI"}
        units = {"cpu": "%", "ram": "%", "temp": "°C"}
        vmax = {"cpu": 100.0, "ram": 100.0, "temp": 110.0}[metric]
        col = WARN if metric == "temp" else CYAN

        raw = self.cur.get(metric)
        display = "--" if raw is None else f"{raw:.0f}{units[metric]}"
        value = 0.0 if raw is None else float(raw)

        self.text(labels[metric], x + 20, y + 44, self.f_small, SOFT)
        self.text(display, x + 20, y + 61, self.f_mid, BRIGHT)
        self.gauge(x + 122, y + 75, 34, value / vmax if raw is not None else 0.0, col)
        self.sparkline(metric, x + 184, y + 42, 430, 62, col, vmax)

        # Son noktayı belirginleştir; grafik gerçekten canlı veri akışını göstersin.
        self.text("CANLI", x + w - 18, y + 10, self.f_tiny, GREEN, align="r")

    def draw_bottom(self):
        self.lines([(16, 690, 1264, 690)], 1.0, C(SOFT, 0.45))
        ok = self.llm_online
        col = GREEN if ok else WARN if ok is None else RED
        self.rect(24, 699, 9, 9, C(col, 0.95))
        msg = ("ALL SYSTEMS OPERATIONAL" if ok
               else "CHECKING SYSTEMS..." if ok is None
               else "OLLAMA OFFLINE — MODEL BAĞLANTISI YOK")
        self.text(msg, 42, 697, self.f_small, col)
        self.text("[SPACE] BASILI TUT • KONUŞ • BIRAK", 634, 697, self.f_small,
                  GREEN if self.voice_ready else SOFT, align="c")
        self.text(f"{self.fps:02d} FPS", 1264, 697, self.f_small, SOFT, align="r")
        for i in range(5):
            on = int(self.t * 3) % 5 == i
            self.rect(1090 + i * 10, 702, 7, 3, C(CYAN if on else SOFT, 0.9 if on else 0.4))

    # ------------------------------------------------------------ olaylar
    def _to_logical(self, pos):
        vx, vy, vw, vh = self.vp
        return (pos[0] - vx) / max(self.scale, 1e-6), \
               (pos[1] - vy) / max(self.scale, 1e-6)

    def handle_event(self, event):
        if event.type == pygame.QUIT:
            self.running = False

        elif event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                self.running = False
            elif (event.key == pygame.K_SPACE
                  and (pygame.key.get_mods() & pygame.KMOD_CTRL)
                  and not self.space_held):
                # CTRL+SPACE = bas-konuş (bilgisayar)
                self.space_held = True
                if self.voice_ready:
                    try:
                        self.listener.start()
                        self.status = "LISTENING..."
                    except Exception as exc:
                        self.status = "MIC ERROR"
                        print("MIC:", exc)
            elif event.key == pygame.K_BACKSPACE:
                self.input_text = self.input_text[:-1]
            elif event.key == pygame.K_RETURN:
                self.send(self.input_text)
            elif event.unicode and event.unicode.isprintable() \
                    and not (event.key == pygame.K_SPACE and self.space_held):
                self.input_text += event.unicode

        elif event.type == pygame.KEYUP and event.key == pygame.K_SPACE:
            if self.space_held:
                self.space_held = False
                if self.listener.recording:
                    self.status = "TRANSCRIBING..."
                    threading.Thread(target=self._record_voice, daemon=True).start()

        elif event.type == pygame.MOUSEWHEEL:
            self.scroll += event.y

        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mx, my = self._to_logical(event.pos)
            r = self.exit_rect
            if r and r[0] <= mx <= r[0] + r[2] and r[1] <= my <= r[1] + r[3]:
                self.running = False
                return

            if self.show_settings:
                for kind, value, bx, by, bw, bh in self.settings_rects:
                    if bx <= mx <= bx + bw and by <= my <= by + bh:
                        if kind == "theme":
                            self.apply_theme(value)
                        elif kind == "voice":
                            self.set_voice(value)
                        elif kind == "close":
                            self.show_settings = False
                        return
                # panel dışına tıklanınca da kapansın
                self.show_settings = False
                return

            for name, tx, ty, tw, th in self.tab_rects:
                if tx <= mx <= tx + tw and ty <= my <= ty + th:
                    if name == "SETTINGS":
                        self.show_settings = True
                    return

    def run(self):
        clock = pygame.time.Clock()
        while self.running:
            for event in pygame.event.get():
                self.handle_event(event)
            self.drain()
            self.render()
            clock.tick(60)

        self.running = False
        self.listener.close()
        self.ctx.release()
        pygame.quit()
