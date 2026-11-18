"""JARVIS HUD v3 — sade ekran (JARVIS v2.7)

Ekranda yalnızca seçili modun görseli durur:

  ÇEKİRDEK : siyah zeminde yarı saydam, düzensiz, çok yüzlü kristal küre;
             beyaz-sıcak merkez, dışarı ışıyan flamanlar, parçacık bulutu.
  HALKA    : ince çember, bölmeli yaylar, sağda kalın parlak yay, dört yönde
             işaretler, altta kavisli taban çizgisi.

Dört durum: bekleme (yavaş, sönük) · dinleme (içe toplanır) · düşünme
(dönüş hızlanır) · konuşma (parlaklık ve kalın yay anlık ses seviyesine göre).

Altta yalnızca küçük bir durum kelimesi ve JARVIS'in son cevabı altyazı gibi.

Tuşlar: BOŞLUK basılı tut = konuş · T = yazı kutusu · M = mod · ESC = ayarlar
        F11 = tam ekran.

Arka plan işleri (beyin, araçlar, görevler, sensör okuma) aynen çalışır;
sadece ekrandan kalktılar. Eski panel: config.HUD_ESKI = True
(interface/eski/gpu_hud.py).

Çizim yapısı: geometri (üçgen batch, eklemeli karışım, MSAA) -> bloom -> ekran
-> yazılar / ayar katmanı (bloom'suz, keskin).
"""
import json
import math
import os
import re
import socket
import subprocess
import threading
import time
from collections import deque
from contextlib import contextmanager
from pathlib import Path

import moderngl
import numpy as np
import psutil
import pygame
import requests

import config

try:
    from core.paths import data_path as _data_path
except Exception:      # core paketi olmadan (ör. tek başına deneme) çalışabilsin
    def _data_path(*parts):
        return Path(__file__).resolve().parents[1].joinpath("data", *parts)

try:
    _JARVIS_VERSION = (Path(__file__).resolve().parents[1] / "VERSION").read_text(encoding="utf8").strip()
except Exception:
    _JARVIS_VERSION = "?"

TAU = math.tau
W, H = 1280, 720                       # mantıksal tasarım boyutu

SETTINGS_FILE = _data_path("hud_settings.json")
MODES = ("cekirdek", "halka")
MODE_LABEL = {"cekirdek": "ÇEKİRDEK", "halka": "HALKA"}
STATE_WORD = {"idle": "HAZIR", "listening": "DİNLİYORUM",
              "thinking": "DÜŞÜNÜYORUM", "speaking": "KONUŞUYORUM"}

# ----------------------------------------------------------------- palet
CYAN = np.array((0.04, 0.78, 1.0), "f4")
ICE = np.array((0.55, 0.93, 1.0), "f4")
HOT = np.array((1.0, 0.98, 0.93), "f4")
TXT = (0.70, 0.90, 0.96)
LIGHT_DIR = np.array((-0.45, 0.55, 0.70), "f4")
LIGHT_DIR /= np.linalg.norm(LIGHT_DIR)


def _cfg(name, default):
    return getattr(config, name, default)


# -------------------------------------------------------------- ayarlar
def load_settings():
    try:
        data = json.loads(Path(SETTINGS_FILE).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_settings(**values):
    """data/hud_settings.json'u günceller; dosyadaki diğer anahtarlar korunur."""
    try:
        path = Path(SETTINGS_FILE)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = load_settings()
        data.update(values)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except Exception as exc:
        print("[HUD] Ayar kaydedilemedi:", exc)


def initial_mode():
    mode = load_settings().get("mode")
    if mode in MODES:
        return mode
    mode = str(_cfg("HUD_MODE", "cekirdek")).lower()
    return mode if mode in MODES else "cekirdek"


# --------------------------------------------------------------- yazı temizliği
def plain_text(text):
    """Markdown/bağlantı artıklarını temizler; altyazıda yalnızca okunacak metin kalır."""
    t = str(text or "")
    t = re.sub(r"```[\s\S]*?```", " ", t)
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    t = re.sub(r"https?://\S+", "", t)
    t = re.sub(r"[*_`#>|]", "", t)
    return re.sub(r"\s+", " ", t).strip()


# ======================================================== 3B geometri (saf numpy)
def icosphere(subdiv=1):
    """Birim ikosahedron, isteğe göre bölünmüş. (köşeler, yüzler)"""
    t = (1 + 5 ** 0.5) / 2
    verts = [(-1, t, 0), (1, t, 0), (-1, -t, 0), (1, -t, 0), (0, -1, t), (0, 1, t),
             (0, -1, -t), (0, 1, -t), (t, 0, -1), (t, 0, 1), (-t, 0, -1), (-t, 0, 1)]
    verts = [np.array(v, "f8") / np.linalg.norm(v) for v in verts]
    faces = [(0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11), (1, 5, 9), (5, 11, 4),
             (11, 10, 2), (10, 7, 6), (7, 1, 8), (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8),
             (3, 8, 9), (4, 9, 5), (2, 4, 11), (6, 2, 10), (8, 6, 7), (9, 8, 1)]
    for _ in range(subdiv):
        cache, out = {}, []

        def mid(a, b):
            key = (min(a, b), max(a, b))
            if key not in cache:
                m = verts[a] + verts[b]
                verts.append(m / np.linalg.norm(m))
                cache[key] = len(verts) - 1
            return cache[key]

        for a, b, c in faces:
            ab, bc, ca = mid(a, b), mid(b, c), mid(c, a)
            out += [(a, ab, ca), (b, bc, ab), (c, ca, bc), (ab, bc, ca)]
        faces = out
    return np.array(verts, "f4"), np.array(faces, "i4")


def unique_edges(faces):
    e = np.vstack([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]])
    e.sort(axis=1)
    return np.unique(e, axis=0)


def _unit(rng, n):
    v = rng.normal(size=(n, 3))
    return (v / np.linalg.norm(v, axis=1, keepdims=True)).astype("f4")


def build_core(seed=7, particles=700, filaments=56):
    """ÇEKİRDEK modunun sabit modeli: düzensiz kristal, iç kabuk, flamanlar, parçacıklar."""
    rng = np.random.default_rng(seed)
    v, f = icosphere(1)
    v = v + rng.normal(0, 0.06, v.shape)
    v = v / np.linalg.norm(v, axis=1, keepdims=True) * rng.uniform(0.84, 1.08, (len(v), 1))
    v = v.astype("f4")
    a, b, c = v[f[:, 0]], v[f[:, 1]], v[f[:, 2]]
    n = np.cross(b - a, c - a)
    n /= np.linalg.norm(n, axis=1, keepdims=True)
    n[(n * (a + b + c)).sum(1) < 0] *= -1             # dışa bakan normaller
    edges = unique_edges(f)
    edges = edges[rng.random(len(edges)) > 0.18]      # bazı kenarlar yok: daha büyük, düzensiz yüzler

    iv, ifc = icosphere(0)
    iv = iv + rng.normal(0, 0.10, iv.shape)
    iv = (iv / np.linalg.norm(iv, axis=1, keepdims=True) * rng.uniform(0.80, 1.10, (len(iv), 1)) * 0.52)

    k = 9
    d = _unit(rng, filaments)
    length = rng.uniform(0.40, 1.38, filaments)
    s = np.linspace(0, 1, k, dtype="f4")
    steps = rng.normal(0, 0.055, (filaments, k, 3))
    steps[:, 0] = 0
    walk = np.cumsum(steps, 1)
    fil = (d[:, None, :] * (0.03 + s[None, :, None] * length[:, None, None])
           + walk * s[None, :, None])
    wig = rng.normal(0, 0.028, (filaments, k, 3)) * s[None, :, None]

    pd = _unit(rng, particles)
    pr = 1.05 + 0.95 * rng.random(particles) ** 1.8
    inside = rng.random(particles) < 0.12
    pr[inside] = rng.uniform(0.15, 0.95, inside.sum())
    pa = rng.uniform(0.15, 0.62, particles) * np.where(pr > 1.55, 0.55, 1.0)

    return {
        "v": v, "f": f, "fn": n.astype("f4"), "fvar": rng.random(len(f)).astype("f4"),
        "e": edges, "iv": iv.astype("f4"), "ie": unique_edges(ifc),
        "fil": fil.astype("f4"), "fil_w": wig.astype("f4"),
        "fil_p": rng.uniform(0, TAU, filaments).astype("f4"), "fil_s": s,
        "p_dir": pd, "p_r": pr.astype("f4"), "p_a": pa.astype("f4"),
        "p_s": rng.uniform(1.0, 2.4, particles).astype("f4"),
        "p_ph": rng.uniform(0, TAU, particles).astype("f4"),
        "p_sp": rng.uniform(0.6, 2.4, particles).astype("f4"),
        "v_ph": rng.uniform(0, TAU, len(v)).astype("f4"),
    }


def rotation(yaw, pitch):
    cy, sy = math.cos(yaw), math.sin(yaw)
    cp, sp = math.cos(pitch), math.sin(pitch)
    ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]], "f4")
    rx = np.array([[1, 0, 0], [0, cp, -sp], [0, sp, cp]], "f4")
    return rx @ ry


def project(p, cx, cy, r, cam=4.2):
    """Basit perspektif. +z izleyiciye doğru."""
    k = cam / (cam - p[..., 2])
    return cx + p[..., 0] * r * k, cy - p[..., 1] * r * k, p[..., 2]


# =================================================================== hareket
STATE_TARGETS = {
    "idle":      {"bright": 0.55, "spin": 0.10, "gather": 0.0, "think": 0.0},
    "listening": {"bright": 0.85, "spin": 0.14, "gather": 1.0, "think": 0.0},
    "thinking":  {"bright": 0.80, "spin": 0.80, "gather": 0.15, "think": 1.0},
    "speaking":  {"bright": 0.72, "spin": 0.18, "gather": 0.0, "think": 0.0},
}


class Motion:
    """Durumlar arası yumuşak geçiş. Telefon tarafındaki hud.js aynı değerleri kullanır."""

    def __init__(self):
        self.bright, self.spin, self.gather, self.think = 0.55, 0.10, 0.0, 0.0
        self.level = 0.0
        self.angle = 0.0
        self.t = 0.0

    def update(self, state, level, dt):
        tg = STATE_TARGETS.get(state, STATE_TARGETS["idle"])
        k = min(1.0, dt * 2.6)
        for name, val in tg.items():
            cur = getattr(self, name)
            setattr(self, name, cur + (val - cur) * k)
        level = max(0.0, min(1.0, float(level or 0.0)))
        kl = min(1.0, dt * (20.0 if level > self.level else 7.0))   # hızlı yükselir, yavaş söner
        self.level += (level - self.level) * kl
        self.angle += self.spin * dt
        self.t += dt


# ================================================================== shaderlar
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
void main() { gl_Position = vec4(in_pos * 2.0 - 1.0, 0.0, 1.0); uv = in_pos; }
"""
BRIGHT_FRAG = """
#version 330
uniform sampler2D src;
uniform float thr;
in vec2 uv;
out vec4 fragColor;
void main() {
    vec3 c = texture(src, uv).rgb;
    fragColor = vec4(max(c - vec3(thr), 0.0) * 1.7, 1.0);
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
# Saf siyah zemin: tarama çizgisi / ızgara yok; yalnızca sahne + parlama.
COMPOSITE_FRAG = """
#version 330
uniform sampler2D scene;
uniform sampler2D bloom1;
uniform sampler2D bloom2;
uniform float intensity;
in vec2 uv;
out vec4 fragColor;
void main() {
    vec3 c = texture(scene, uv).rgb;
    vec3 b = texture(bloom1, uv).rgb * 0.8 + texture(bloom2, uv).rgb * 1.1;
    c += b * intensity;
    c = c / (1.0 + max(c - 1.0, 0.0));          // tepe değerleri yumuşak kırp
    fragColor = vec4(c, 1.0);
}
"""

_UNIT_T = {}


def _unit_t(seg):
    a = _UNIT_T.get(seg)
    if a is None:
        a = _UNIT_T[seg] = np.linspace(0, 1, seg + 1, dtype="f4")
    return a


def _rgba(rgb, a):
    return (float(rgb[0]), float(rgb[1]), float(rgb[2]), float(a))


def _local_ip():
    """LAN IP'si (UDP soketi bağlanır ama veri gönderilmez)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


class _SilentSpeaker:
    """Ses çıkışı kurulamazsa HUD yine açılsın."""
    speaking = False
    level = 0.0
    level_live = False

    def speak(self, text):
        pass


# ================================================================== HUD
class SadeHUD:
    SAMPLE_DT = 1.0

    def __init__(self, brain, width=1280, height=720,
                 ctx=None, target=None, start_threads=True):
        self.brain = brain
        self.width, self.height = W, H
        pygame.font.init()
        self.fullscreen = False
        if ctx is None:
            pygame.init()
            self._open_window(width, height)
            self.ctx = moderngl.create_context()
            self.target = self.ctx.screen
            self.owns_window = True
        else:
            self.ctx = ctx
            self.target = target
            self.owns_window = False
        self.vp = (0, 0, W, H)
        self.scale = 1.0
        self._fonts = {}
        self._build_gl()

        # ---- durum
        self.mode = initial_mode()
        self.motion = Motion()
        self.core = build_core()
        self.running = True
        self.queue = deque()
        self.input_text = ""
        self.typing = False
        # v2.8: yazı kutusu (TEXTINPUT ile Türkçe karakterler, imleç, geçmiş, Ctrl+V) + "✎ YAZ" düğmesi
        from interface.textbox import TextBox
        self.box = TextBox()
        self._ignore_text_until = 0.0
        self.mouse = (-1.0, -1.0)
        self.write_btn = (W - 120, H - 66, 92, 32)
        # v3.0: görsel kartlar (interface/cards.py)
        from interface.cards import CardLayer
        self.cards = CardLayer(W, H, hold=_cfg("CARD_HOLD_SECONDS", 2.5))
        self._xf = None
        self.show_settings = False
        self.settings_rects = []
        self.space_held = False
        self.waiting = False
        self.transcribing = False
        self.active_queries = 0
        self.voice_ready = False
        self.voice_error = False
        self.mic_error_until = 0.0
        self.llm_online = None
        self.flash = None              # (metin, bitiş) — mod değişince kısa bilgi
        self.sub = None                # altyazı
        self.debug_state = None        # deneme/ekran görüntüsü için durum zorlama
        self.debug_level = None
        self.t0 = time.time()
        self.last_frame = self.t0
        self.fps = 0
        self._fps_n = 0
        self._fps_t = self.t0
        self.cur = {"cpu": None, "ram": None, "gpu": None, "temp": None}
        self.phone_addr = None

        self._restore_voice()
        from voice.listener import VoiceListener
        self.listener = VoiceListener()      # v2.6: model config.WHISPER_MODEL (varsayılan "small")
        try:
            from voice.speaker import VoiceSpeaker
            self.speaker = VoiceSpeaker()
        except Exception as exc:
            print("[HUD] Ses çıkışı açılamadı:", exc)
            self.speaker = _SilentSpeaker()

        if start_threads:
            for fn in (self._load_voice, self._sensor_loop, self._llm_loop, self._phone_info):
                threading.Thread(target=fn, daemon=True).start()

    # ----------------------------------------------------------- pencere
    def _open_window(self, width, height):
        pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MAJOR_VERSION, 3)
        pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MINOR_VERSION, 3)
        pygame.display.gl_set_attribute(pygame.GL_DOUBLEBUFFER, 1)
        flags = pygame.OPENGL | pygame.DOUBLEBUF | pygame.RESIZABLE
        try:
            pygame.display.set_mode((width, height), flags, vsync=1)
        except Exception:
            pygame.display.set_mode((width, height), flags)
        pygame.display.set_caption("JARVIS")
        pygame.key.set_repeat(400, 35)

    def toggle_fullscreen(self):
        if not self.owns_window:
            return
        try:
            pygame.display.toggle_fullscreen()
            self.fullscreen = not self.fullscreen
        except Exception as exc:
            print("[HUD] Tam ekran değiştirilemedi:", exc)

    # --------------------------------------------------------- ses / ayar
    def _restore_voice(self):
        try:
            import voice.speaker as sp
            saved = load_settings().get("voice") or _cfg("VOICE_PROFILE", "V3")
            if saved in getattr(sp, "VOICE_PROFILES", {}):
                sp.set_profile(saved)
            elif isinstance(saved, str) and saved:
                sp.VOICE = saved
        except Exception:
            pass

    def current_voice(self):
        return load_settings().get("voice") or _cfg("VOICE_PROFILE", "V3")

    def set_voice(self, voice_id):
        import voice.speaker as sp
        known = voice_id in getattr(sp, "VOICE_PROFILES", {})
        if known:
            sp.set_profile(voice_id)
        else:
            sp.VOICE = voice_id
        values = {"voice": voice_id}
        if known:
            values["voice_profile"] = voice_id
        save_settings(**values)

    def set_mode(self, mode):
        if mode not in MODES:
            return
        self.mode = mode
        save_settings(mode=mode)
        self.flash = (MODE_LABEL[mode], time.time() + 1.4)

    def toggle_mode(self):
        self.set_mode(MODES[(MODES.index(self.mode) + 1) % len(MODES)])

    # ------------------------------------------------------------ GL kurulum
    def _build_gl(self):
        ctx = self.ctx
        ctx.enable(moderngl.BLEND)
        self.ALPHA = (moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA)
        self.ADD = (moderngl.SRC_ALPHA, moderngl.ONE)
        ctx.blend_func = self.ALPHA
        self._add = False
        self.geo_prog = ctx.program(vertex_shader=GEO_VERT, fragment_shader=GEO_FRAG)
        self._geo = []
        self._texts = []
        self.text_cache = {}
        self._geo_size = 4 * 1024 * 1024
        self._make_geo_buffer()
        self.quad = ctx.buffer(np.array([0, 0, 1, 0, 0, 1, 1, 1], "f4").tobytes())
        self.text_prog = ctx.program(vertex_shader=RECT_VERT, fragment_shader=TEXT_FRAG)
        self.glow_prog = ctx.program(vertex_shader=RECT_VERT, fragment_shader=GLOW_FRAG)
        self.text_vao = ctx.vertex_array(self.text_prog, [(self.quad, "2f", "in_pos")])
        self.glow_vao = ctx.vertex_array(self.glow_prog, [(self.quad, "2f", "in_pos")])
        self._tsize = None
        self.ms_fbo = None
        try:
            self.samples = min(4, int(ctx.info.get("GL_MAX_SAMPLES", 0) or 0))
        except Exception:
            self.samples = 0
        self.post_ok = False
        if _cfg("ENABLE_BLOOM", True):
            try:
                def prog(frag):
                    p = ctx.program(vertex_shader=QUAD_VERT, fragment_shader=frag)
                    return p, ctx.vertex_array(p, [(self.quad, "2f", "in_pos")])
                self.p_bright = prog(BRIGHT_FRAG)
                self.p_copy = prog(COPY_FRAG)
                self.p_blur = prog(BLUR_FRAG)
                self.p_comp = prog(COMPOSITE_FRAG)
                self._ensure_targets((W, H))
                self.post_ok = True
            except Exception as exc:          # eski/zayıf GPU: bloom'suz devam
                print("BLOOM kapalı:", exc)

    def _make_geo_buffer(self):
        self.geo_buf = self.ctx.buffer(reserve=self._geo_size, dynamic=True)
        self.geo_vao = self.ctx.vertex_array(
            self.geo_prog, [(self.geo_buf, "2f 4f", "in_pos", "in_col")])

    def _release_targets(self):
        for name in ("scene_tex", "scene_fbo", "ms_rb", "ms_fbo", "h_a", "h_a_fbo",
                     "h_b", "h_b_fbo", "q_a", "q_a_fbo", "q_b", "q_b_fbo"):
            obj = getattr(self, name, None)
            if obj is not None:
                try:
                    obj.release()
                except Exception:
                    pass
                setattr(self, name, None)

    def _ensure_targets(self, size):
        """Sahne dokuları gerçek piksel boyutunda: tam ekranda da keskin kalsın."""
        size = (max(16, int(size[0])), max(16, int(size[1])))
        if self._tsize == size:
            return
        self._release_targets()
        ctx = self.ctx

        def tex(sz):
            t = ctx.texture(sz, 4)
            t.filter = (moderngl.LINEAR, moderngl.LINEAR)
            t.repeat_x = t.repeat_y = False
            return t, ctx.framebuffer([t])

        w, h = size
        self.scene_tex, self.scene_fbo = tex(size)
        self.ms_fbo = None
        if self.samples:
            try:
                self.ms_rb = ctx.renderbuffer(size, 4, samples=self.samples)
                self.ms_fbo = ctx.framebuffer([self.ms_rb])
            except Exception:
                self.samples = 0
                self.ms_fbo = None
        self.s1 = (max(1, w // 2), max(1, h // 2))
        self.s2 = (max(1, w // 4), max(1, h // 4))
        self.h_a, self.h_a_fbo = tex(self.s1)
        self.h_b, self.h_b_fbo = tex(self.s1)
        self.q_a, self.q_a_fbo = tex(self.s2)
        self.q_b, self.q_b_fbo = tex(self.s2)
        self._tsize = size

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
        xf = getattr(self, "_xf", None)
        if xf is not None:                      # v3.0: ana görselin kayma/küçülme dönüşümü
            pos = pos * xf[0] + np.asarray(xf[1:], "f4")
        arr = np.empty((len(pos), 6), "f4")
        arr[:, :2] = pos
        arr[:, 2:] = color
        self._geo.append(arr)

    def rect(self, x, y, w, h, color):
        self._emit([(x, y), (x + w, y), (x, y + h), (x + w, y), (x + w, y + h), (x, y + h)], color)

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
        if len(p) >= 2:
            self.lines(np.hstack([p[:-1], p[1:]]), width, color)

    def arcs(self, cx, cy, r0, r1, starts, span, color, seg=None):
        starts = np.atleast_1d(np.asarray(starts, "f4"))
        if seg is None:
            seg = max(2, int(abs(span) * r1 / 4) + 1)
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
                  seg=max(48, int(r * TAU / 4)))

    def tris(self, pos, cols):
        self._emit(pos, cols)

    def dots(self, x, y, size, cols):
        h = np.asarray(size, "f4") * 0.5
        x0, x1, y0, y1 = x - h, x + h, y - h, y + h
        q = [np.stack(p, 1) for p in ((x0, y0), (x1, y0), (x0, y1), (x1, y0), (x1, y1), (x0, y1))]
        self._emit(np.stack(q, 1).reshape(-1, 2), np.repeat(np.asarray(cols, "f4"), 6, 0))

    def glow(self, cx, cy, r, color, power=2.0):
        xf = getattr(self, "_xf", None)
        if xf is not None:
            cx, cy, r = cx * xf[0] + xf[1], cy * xf[0] + xf[2], r * xf[0]
        self._flush()
        self.ctx.blend_func = self.ADD
        self.glow_prog["resolution"].value = (W, H)
        self.glow_prog["rect"].value = (cx - r, cy - r, 2 * r, 2 * r)
        self.glow_prog["color"].value = tuple(float(c) for c in color)
        self.glow_prog["power"].value = power
        self.glow_vao.render(moderngl.TRIANGLE_STRIP)
        self.ctx.blend_func = self.ADD if self._add else self.ALPHA

    # ---------------------------------------------------------------- yazı
    def _font(self, size, bold=False, scale=None):
        px = max(6, int(round(size * (self.scale if scale is None else scale))))
        key = (px, bold)
        f = self._fonts.get(key)
        if f is None:
            f = self._fonts[key] = pygame.font.SysFont(
                "bahnschrift,segoeui,dejavusans,arial", px, bold=bold)
        return f

    def text(self, value, x, y, size=16, color=TXT, alpha=1.0, align="l",
             spacing=0.0, bold=False):
        if value and alpha > 0.004:
            self._texts.append((str(value), x, y, size, bold, spacing, color, alpha, align))

    def text_width(self, value, size=16, spacing=0.0, bold=False):
        f = self._font(size, bold, scale=1.0)
        return f.size(str(value))[0] + spacing * max(0, len(str(value)) - 1)

    def _render_surface(self, value, font, rgb, spacing_px):
        if spacing_px <= 0:
            return font.render(value, True, rgb)
        glyphs = [font.render(ch, True, rgb) for ch in value]
        w = sum(g.get_width() for g in glyphs) + int(spacing_px * (len(glyphs) - 1))
        # "İ" gibi harflerin yüzeyi daha uzun (nokta yukarı taşar): tabanları hizala.
        h = max(g.get_height() for g in glyphs)
        surf = pygame.Surface((max(1, w), h), pygame.SRCALPHA)
        x = 0
        for g in glyphs:
            surf.blit(g, (x, h - g.get_height()))
            x += g.get_width() + int(spacing_px)
        return surf

    def _flush_text(self):
        self._flush()
        if not self._texts:
            return
        self.ctx.blend_func = self.ALPHA
        self._add = False
        if len(self.text_cache) > 400:
            for t in self.text_cache.values():
                t.release()
            self.text_cache.clear()
        self.text_prog["resolution"].value = (W, H)
        self.text_prog["tex"].value = 0
        s = max(self.scale, 1e-6)
        for value, x, y, size, bold, spacing, color, alpha, align in self._texts:
            font = self._font(size, bold)
            rgb = tuple(int(max(0, min(1, c)) * 255) for c in color[:3])
            sp_px = int(round(spacing * s))
            key = (value, id(font), rgb, sp_px)
            tex = self.text_cache.get(key)
            if tex is None:
                surf = self._render_surface(value, font, rgb, sp_px)
                to_bytes = getattr(pygame.image, "tobytes", None) or pygame.image.tostring
                tex = self.ctx.texture(surf.get_size(), 4, to_bytes(surf, "RGBA", True))
                tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
                self.text_cache[key] = tex
            w, h = tex.size[0] / s, tex.size[1] / s
            x0 = x - w if align == "r" else x - w / 2 if align == "c" else x
            tex.use(0)
            self.text_prog["rect"].value = (x0, y, w, h)
            self.text_prog["alpha"].value = float(alpha)
            self.text_vao.render(moderngl.TRIANGLE_STRIP)
        self._texts = []

    def wrap(self, text, size, max_w):
        font = self._font(size, scale=1.0)
        out, line = [], ""
        for word in str(text).split():
            test = word if not line else line + " " + word
            if line and font.size(test)[0] > max_w:
                out.append(line)
                line = word
            else:
                line = test
        if line:
            out.append(line)
        return out

    # ------------------------------------------------------- arka plan işleri
    def _load_voice(self):
        try:
            self.listener.load()
            self.voice_ready = True
        except Exception as exc:
            self.voice_error = True
            print("VOICE:", exc)
            return
        # v2.8: mikrofon baştan açık kalır (VOICE_KEEP_MIC_OPEN): bas-konuşta ilk hece kaçmaz
        try:
            if hasattr(self.listener, "open_mic"):
                self.listener.open_mic()
        except Exception as exc:
            print("[SES] Mikrofon açılamadı:", exc)
        # v2.6: "Jarvis" uyandırma sözcüğü (config.WAKE_WORD_ENABLED). Başlatılamazsa bas-konuş yine çalışır.
        if getattr(config, "WAKE_WORD_ENABLED", False) and hasattr(self.listener, "start_wake"):
            try:
                self.listener.start_wake(on_wake=self._on_wake, on_auto_stop=self._on_auto_stop,
                                         pause_when=self._wake_paused)
            except Exception as exc:
                print("[SES] Uyandırma sözcüğü başlatılamadı (bas-konuş çalışıyor):", exc)

    def _wake_paused(self):
        # JARVIS konuşurken ve bas-konuş basılıyken uyandırma dinlenmez (kendi sesiyle tetiklenmesin).
        return bool(getattr(self.speaker, "speaking", False) or self.space_held or self.transcribing)

    def _on_wake(self):
        # Kayıt başladı; durum kelimesi listener.recording'den kendiliğinden DİNLİYORUM olur.
        print("[SES] \"Jarvis\" duyuldu, dinliyorum.")

    def _on_auto_stop(self, reason):
        # Uyandırmayla başlayan kayıt konuşma bitince kendiliğinden durdu: yazıya dök.
        if self.transcribing:
            return
        self.transcribing = True
        self._record_voice()

    def _query_gpu(self):
        try:
            flags = 0x08000000 if os.name == "nt" else 0
            out = subprocess.check_output(
                ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
                stderr=subprocess.DEVNULL, timeout=2, creationflags=flags).decode().strip()
            return float(out.splitlines()[0])
        except Exception:
            return None

    def _sensor_loop(self):
        """Sensör okuma sürüyor; değerler yalnızca ayar katmanında gösterilir."""
        psutil.cpu_percent(None)
        last_gpu = 0.0
        while self.running:
            time.sleep(self.SAMPLE_DT)
            try:
                self.cur["cpu"] = psutil.cpu_percent(None)
                self.cur["ram"] = psutil.virtual_memory().percent
            except Exception:
                pass
            now = time.time()
            if now - last_gpu >= 3.0:
                last_gpu = now
                self.cur["gpu"] = self._query_gpu()
                try:
                    self.cur["temp"] = self.brain.tools._cpu_temperature()
                except Exception:
                    self.cur["temp"] = None

    def _llm_loop(self):
        base = str(_cfg("OLLAMA_URL", "")).split("/api")[0]
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

    def _phone_info(self):
        if _cfg("PHONE_SERVER_ENABLED", True):
            self.phone_addr = f"http://{_local_ip()}:{_cfg('PHONE_SERVER_PORT', 5000)}"

    # ------------------------------------------------------------ mantık
    def send(self, text):
        text = (text or "").strip()
        if not text:
            return
        self.input_text = ""
        self._start_query(text)

    def _start_query(self, text):
        self.active_queries += 1
        self.waiting = True
        threading.Thread(target=self._ask, args=(text,), daemon=True).start()

    def _ask(self, text):
        try:
            # v3.0: cevapla birlikte görsel kartlar (hava, ev, kask, bilgi, klavye)
            ask_v = getattr(self.brain, "ask_with_visuals", None)
            if callable(ask_v) and _cfg("VISUAL_CARDS", True):
                answer, cards = ask_v(text)
                answer = (answer, cards)
            else:
                answer = self.brain.ask(text)
            self.llm_online = True
            self.queue.append(("ANSWER", answer))
        except Exception as exc:
            self.queue.append(("ERROR", str(exc)))

    def _record_voice(self):
        try:
            text = self.listener.stop()
            self.queue.append(("VOICE", text))
        except Exception as exc:
            self.queue.append(("VOICE_ERROR", str(exc)))

    def drain(self):
        while self.queue:
            kind, value = self.queue.popleft()
            if kind == "VOICE":
                self.transcribing = False
                if value:
                    self._start_query(value)
                else:
                    self.show_subtitle("Sesi anlayamadım.", speak_sync=False)
            elif kind == "VOICE_ERROR":
                self.transcribing = False
                self.show_subtitle("Mikrofon hatası: " + value, speak_sync=False)
            else:
                self.active_queries = max(0, self.active_queries - 1)
                self.waiting = self.active_queries > 0
                if kind == "ERROR":
                    self.show_subtitle("Hata: " + value, speak_sync=False)
                else:
                    cards = []
                    if isinstance(value, tuple):
                        value, cards = value
                    self.show_subtitle(value)
                    self.speaker.speak(value)
                    if cards:
                        self.cards.show(cards[0], text=value)

    def state(self):
        if self.debug_state:
            return self.debug_state
        if getattr(self.listener, "recording", False):
            return "listening"
        if getattr(self.speaker, "speaking", False):
            return "speaking"
        if self.waiting or self.transcribing:
            return "thinking"
        return "idle"

    def _level(self, state, t):
        if self.debug_level is not None:
            return self.debug_level
        if state == "listening":
            return float(getattr(self.listener, "level", 0.0) or 0.0)
        if state == "speaking":
            if getattr(self.speaker, "level_live", False):
                return float(getattr(self.speaker, "level", 0.0) or 0.0)
            return 0.30 + 0.45 * abs(math.sin(t * 7.3) * math.sin(t * 2.1 + 1.0))
        return 0.0

    # ------------------------------------------------------------- altyazı
    SUB_SIZE = 19
    SUB_WIDTH = 860

    def show_subtitle(self, text, speak_sync=True):
        clean = plain_text(text)
        if not clean:
            return
        lines = self.wrap(clean, self.SUB_SIZE, self.SUB_WIDTH)
        pages = [lines[i:i + 2] for i in range(0, len(lines), 2)]
        durs = [max(2.4, 0.064 * sum(len(x) for x in p)) for p in pages]
        now = time.time()
        self.sub = {"pages": pages, "durs": durs, "created": now,
                    "start": None if speak_sync else now, "speak_sync": speak_sync}

    def _subtitle_frame(self, now, speaking):
        """(satırlar, opaklık) ya da None."""
        s = self.sub
        if not s:
            return None
        if s["start"] is None:
            # Ses başlayınca akmaya başlasın (TTS üretimi biraz sürebilir).
            if speaking or now - s["created"] > 4.0:
                s["start"] = now
            else:
                return s["pages"][0], min(1.0, (now - s["created"]) / 0.3)
        el = now - s["start"]
        acc = 0.0
        for i, d in enumerate(s["durs"]):
            if el < acc + d:
                fade = min(1.0, (el - acc) / 0.25) if i else min(1.0, (now - s["created"]) / 0.3)
                return s["pages"][i], fade
            acc += d
        if s["speak_sync"] and speaking:
            return s["pages"][-1], 1.0
        if s.get("end") is None:
            s["end"] = now
        out = 1.0 - (now - s["end"]) / 0.9
        if out <= 0:
            self.sub = None
            return None
        return s["pages"][-1], out

    # ================================================================ çizim
    def render(self):
        now = time.time()
        dt = min(now - self.last_frame, 0.1)
        self.last_frame = now
        self._fps_n += 1
        if now - self._fps_t >= 1.0:
            self.fps, self._fps_n, self._fps_t = self._fps_n, 0, now
        st = self.state()
        m = self.motion
        m.update(st, self._level(st, m.t), dt)
        self._compute_viewport()
        ctx = self.ctx
        vw, vh = self.vp[2], self.vp[3]

        if self.post_ok:
            self._ensure_targets((vw, vh))
            (self.ms_fbo or self.scene_fbo).use()
            ctx.viewport = (0, 0, self._tsize[0], self._tsize[1])
        else:
            self.target.use()
            if self.owns_window:
                ww, wh = pygame.display.get_window_size()
                ctx.viewport = (0, 0, ww, wh)
                ctx.clear(0.0, 0.0, 0.0, 1.0)
            ctx.viewport = self.vp
        ctx.enable(moderngl.BLEND)
        ctx.clear(0.0, 0.0, 0.0, 1.0)
        self._add = False
        self._set_add(True)
        # v3.0: kart açıkken ana görsel sağa kayar ve küçülür (çizim koduna dokunmadan, dönüşümle)
        spk = self.speaker
        speaking = bool(getattr(spk, "speaking", False))
        pos = getattr(spk, "speech_pos", None) if getattr(spk, "speech_text", None) == self.cards.text else None
        self.cards.update(speaking, pos)
        s, tx, ty = self.cards.orb_transform(W * 0.5, H * (0.46 if self.mode == "halka" else 0.47))
        self._xf = (s, tx, ty) if s < 0.999 else None
        if self.mode == "halka":
            self.draw_ring(m)
        else:
            self.draw_core(m)
        self._xf = None
        self.cards.draw_shapes(self, now)
        self._flush()

        if self.post_ok:
            if self.ms_fbo is not None:
                ctx.copy_framebuffer(self.scene_fbo, self.ms_fbo)
            self._post()

        # Yazılar ve ayar katmanı bloom'suz, keskin.
        self.target.use()
        ctx.viewport = self.vp
        ctx.enable(moderngl.BLEND)
        self._add = True
        self._set_add(False)
        self.cards.draw_texts(self, now)
        self.draw_texts(st, now)
        if self.show_settings:
            self._flush_text()
            self.draw_settings()
        self._flush()
        self._flush_text()
        if self.owns_window:
            pygame.display.flip()

    def _compute_viewport(self):
        if not self.owns_window:
            self.vp, self.scale = (0, 0, W, H), 1.0
            if self.target is not None and hasattr(self.target, "size"):
                tw, th = self.target.size
                s = min(tw / W, th / H)
                vw, vh = int(W * s), int(H * s)
                self.vp, self.scale = ((tw - vw) // 2, (th - vh) // 2, vw, vh), s
            return
        ww, wh = pygame.display.get_window_size()
        s = min(ww / W, wh / H)
        vw, vh = max(1, int(W * s)), max(1, int(H * s))
        if abs(s - self.scale) > 1e-3:
            self._fonts.clear()
            for t in self.text_cache.values():
                t.release()
            self.text_cache.clear()
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
        self._pass(self.p_bright, self.h_a_fbo, src=0, thr=0.22)
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
        prog["intensity"].value = 0.95
        vao.render(moderngl.TRIANGLE_STRIP)
        ctx.enable(moderngl.BLEND)

    # ------------------------------------------------------ MOD 1: ÇEKİRDEK
    def draw_core(self, m):
        cr = self.core
        cx, cy = W * 0.5, H * 0.47
        lv = m.level
        breath = 1.0 + 0.022 * math.sin(m.t * 0.9) + 0.05 * lv
        R = 158.0 * breath
        B = m.bright + 0.5 * lv
        rot = rotation(m.angle, 0.36 + 0.07 * math.sin(m.t * 0.21))

        # büyük, sönük hale
        self.glow(cx, cy, 460, (0.0, 0.28, 0.46, 0.12 * B), 3.2)

        # --- yüzeyler: yarı saydam cam, kenara bakanlar parlak
        V = cr["v"] @ rot.T
        x, y, z = project(V, cx, cy, R)
        P = np.stack([x, y], 1)
        n = cr["fn"] @ rot.T
        nz = n[:, 2]
        rim = (1.0 - np.abs(nz)) ** 2
        spec = np.clip(n @ LIGHT_DIR, 0, 1) ** 12
        a = ((0.016 + 0.07 * rim + 0.03 * cr["fvar"]) * np.where(nz > 0, 1.0, 0.45)
             + 0.17 * spec) * B
        cols = np.empty((len(n), 4), "f4")
        sm = np.clip(spec * 0.9, 0, 1)[:, None]
        cols[:, :3] = CYAN * (1 - sm) + HOT * sm
        cols[:, 3] = a
        self.tris(P[cr["f"]].reshape(-1, 2), np.repeat(cols, 3, 0))

        # --- kenarlar: öndekiler parlak, arkadakiler soluk
        e = cr["e"]
        depth = np.clip(((z[e[:, 0]] + z[e[:, 1]]) * 0.5 + 1.1) / 2.2, 0, 1)
        ec = np.empty((len(e), 4), "f4")
        ec[:, :3] = CYAN * (1 - depth[:, None] * 0.6) + ICE * (depth[:, None] * 0.6)
        ec[:, 3] = (0.10 + 0.62 * depth ** 1.6) * B
        self.lines(np.hstack([P[e[:, 0]], P[e[:, 1]]]), 1.25, ec)

        # köşe pırıltıları (öndekiler)
        front = z > 0.25
        if front.any():
            tw = 0.5 + 0.5 * np.sin(m.t * 1.7 + cr["v_ph"][front] * 3)
            vc = np.empty((int(front.sum()), 4), "f4")
            vc[:, :3] = ICE
            vc[:, 3] = (0.15 + 0.55 * tw) * B
            self.dots(x[front], y[front], 2.2, vc)

        # --- iç kabuk: ters yönde dönen, daha küçük ve soluk
        rot2 = rotation(-m.angle * 0.7 + 1.3, -0.25)
        Vi = cr["iv"] @ rot2.T
        ix, iy, iz = project(Vi, cx, cy, R)
        ie = cr["ie"]
        idp = np.clip(((iz[ie[:, 0]] + iz[ie[:, 1]]) * 0.5 + 0.6) / 1.2, 0, 1)
        ic = np.empty((len(ie), 4), "f4")
        ic[:, :3] = ICE
        ic[:, 3] = (0.05 + 0.16 * idp) * B
        IP = np.stack([ix, iy], 1)
        self.lines(np.hstack([IP[ie[:, 0]], IP[ie[:, 1]]]), 1.0, ic)

        # --- flamanlar: merkezden dışarı ışıyan ince damarlar
        fb, k = cr["fil"], cr["fil"].shape[1]
        ph = cr["fil_p"]
        wave = np.sin(m.t * (1.1 + 2.6 * m.think) + ph[:, None] + np.arange(k)[None, :] * 0.8)
        pts = (fb + cr["fil_w"] * wave[..., None]) * (1.0 + 0.28 * lv - 0.12 * m.gather)
        fx, fy, fz = project(pts @ rot.T, cx, cy, R)
        s = cr["fil_s"]
        pa = (1.0 - s) ** 1.3
        flick = 0.62 + 0.38 * np.sin(m.t * (2.0 + 11.0 * m.think) + ph * 7.0)
        dep = np.clip((fz + 1.4) / 2.8, 0.25, 1.0)
        seg_a = ((pa[:-1] + pa[1:]) * 0.5)[None, :] * flick[:, None] * \
            ((dep[:, :-1] + dep[:, 1:]) * 0.5) * 0.95 * B
        mix = ((s[:-1] + s[1:]) * 0.5)[None, :, None]
        fc = np.empty((len(fb), k - 1, 4), "f4")
        fc[..., :3] = HOT * (1 - mix) * 0.8 + CYAN * mix + ICE * (1 - mix) * 0.2
        fc[..., 3] = seg_a
        segs = np.stack([fx[:, :-1], fy[:, :-1], fx[:, 1:], fy[:, 1:]], -1)
        self.lines(segs.reshape(-1, 4), 1.0, fc.reshape(-1, 4))

        # --- beyaz-sıcak merkez
        self.glow(cx, cy, 200 * (1 + 0.2 * lv), (0.04, 0.55, 0.95, 0.20 * B), 2.0)
        self.glow(cx, cy, 82 * (1 + 0.35 * lv), (0.55, 0.92, 1.0, 0.50 * B), 2.2)
        self.glow(cx, cy, 32 * (1 + 0.55 * lv), (1.0, 0.98, 0.92, min(1.0, 0.95 * B)), 1.5)
        self.glow(cx, cy, 11 * (1 + 0.5 * lv), (1.0, 1.0, 1.0, 1.0), 1.0)

        # --- parçacık bulutu: dinlerken içe toplanır
        g = m.gather
        rs = cr["p_r"] * (1.0 - 0.34 * g) + 0.07 * lv * np.sin(m.t * 3.0 + cr["p_ph"])
        prot = rotation(m.angle * 0.55 + g * 0.3, 0.30)
        px, py, pz = project((cr["p_dir"] * rs[:, None]) @ prot.T, cx, cy, R)
        tw = 0.5 + 0.5 * np.sin(m.t * cr["p_sp"] + cr["p_ph"])
        pc = np.empty((len(rs), 4), "f4")
        pc[:, :3] = ICE
        pc[:, 3] = (cr["p_a"] * (0.35 + 0.65 * tw) * (0.5 + 0.5 * np.clip((pz + 1.9) / 3.8, 0, 1))
                    * (0.55 + 0.6 * B) * (1.0 + 0.7 * g))
        self.dots(px, py, cr["p_s"], pc)

    # --------------------------------------------------------- MOD 2: HALKA
    # (yarıçap farkı, kalınlık, başlangıçlar, açıklıklar, dönüş hızı, opaklık)
    RING_ARCS = (
        (13, 2.4, (2.05, 3.35, 4.55), (0.80, 0.42, 1.10), 0.32, 0.62),
        (-13, 5.0, (3.55, 5.15, 0.95), (0.55, 0.30, 0.22), -0.22, 0.42),
        (22, 1.0, (1.00, 4.10), (1.40, 0.90), 0.12, 0.32),
        (-25, 1.2, (0.20, 2.80), (0.70, 1.60), -0.45, 0.26),
    )

    def draw_ring(self, m):
        cx, cy = W * 0.5, H * 0.46
        R = 150.0
        lv = m.level
        B = m.bright + 0.45 * lv
        g = m.gather

        def rr(dr):                       # dinlerken yaylar çembere doğru toplanır
            return R + dr * (1.0 - 0.6 * g)

        self.glow(cx, cy, R * 2.6, (0.0, 0.25, 0.45, 0.06 * B), 3.2)
        self.ring(cx, cy, R, 1.3, _rgba(CYAN, 0.58 * B))
        self.ring(cx, cy, rr(-36), 0.9, _rgba(CYAN, 0.12 * B))

        # ince bölme çentikleri (çemberin hemen içinde)
        n_t = 120
        ang = np.arange(n_t) * (TAU / n_t) + m.angle * 0.15
        major = (np.arange(n_t) % 10) == 0
        r0 = np.where(major, R - 10, R - 5)
        co, si = np.cos(ang), np.sin(ang)
        segs = np.stack([cx + co * r0, cy + si * r0, cx + co * (R - 2), cy + si * (R - 2)], 1)
        tc = np.empty((n_t, 4), "f4")
        tc[:, :3] = CYAN
        tc[:, 3] = np.where(major, 0.45, 0.16) * B
        self.lines(segs, 1.0, tc)

        # bölmeli yay parçaları
        for dr, w, starts, spans, spd, a in self.RING_ARCS:
            r = rr(dr)
            off = m.angle * spd * 3.0
            for st, sp in zip(starts, spans):
                self.arcs(cx, cy, r - w / 2, r + w / 2, [st + off], sp, _rgba(CYAN, a * B))

        # dış kesik halka: 5 çizgi + boşluk düzeni
        idx = np.arange(96)
        keep = (idx % 8) < 5
        starts = idx[keep] * (TAU / 96) - m.angle * 0.5
        r = rr(31)
        self.arcs(cx, cy, r - 0.6, r + 0.6, starts, 0.032, _rgba(ICE, 0.30 * B), seg=2)

        # sağdaki kalın, parlak yay: konuşurken sesle boyu ve parlaklığı atar
        span = 0.46 + 1.0 * lv + 0.10 * m.think * (0.5 + 0.5 * math.sin(m.t * 6.0))
        w = 6.0 + 4.0 * lv
        rk = R + 3.0
        hot = np.clip(0.75 + 0.25 * lv, 0, 1)
        col = _rgba(CYAN * (1 - hot * 0.5) + HOT * (hot * 0.5), min(1.0, 0.70 + 0.45 * B))
        self.arcs(cx, cy, rk - w / 2, rk + w / 2, [-span / 2], span, col)
        self.arcs(cx, cy, rk + 9 - 0.7, rk + 9 + 0.7, [-span * 0.62], span * 1.24,
                  _rgba(ICE, 0.45 * B))
        self.glow(cx + rk, cy, 70 + 50 * lv, (0.2, 0.8, 1.0, 0.16 * B + 0.12 * lv), 2.0)

        # dört ana yönde işaret ve küçük sembol grupları
        for k, a in enumerate((0.0, math.pi / 2, math.pi, 1.5 * math.pi)):
            self._cardinal(cx, cy, R, a, k, B)

        # altta kavisli ince taban çizgisi (uçlara doğru söner)
        y0 = cy + R + 96
        Rb = 1300.0
        bcy = y0 - Rb
        sN = 90
        s = np.linspace(-1, 1, sN + 1, dtype="f4")
        th = math.pi / 2 + s * 0.31
        bx, by = cx + np.cos(th) * Rb, bcy + np.sin(th) * Rb
        segs = np.stack([bx[:-1], by[:-1], bx[1:], by[1:]], 1)
        mid = np.abs((s[:-1] + s[1:]) * 0.5)
        bc = np.empty((sN, 4), "f4")
        bc[:, :3] = CYAN
        bc[:, 3] = (1 - mid) ** 1.6 * 0.55 * B
        self.lines(segs, 1.1, bc)
        self.lines([(cx, y0 + 5, cx, y0 + 13)], 1.2, _rgba(ICE, 0.5 * B))
        for sx in (-46, 46):
            self.rect(cx + sx - 1.5, y0 + 6, 3, 3, _rgba(CYAN, 0.38 * B))

    def _cardinal(self, cx, cy, R, a, k, B):
        u = np.array((math.cos(a), math.sin(a)), "f4")      # dışa
        t = np.array((-math.sin(a), math.cos(a)), "f4")     # teğet
        c = np.array((cx, cy), "f4")

        def P(r, tang=0.0):
            return c + u * r + t * tang

        main = _rgba(ICE, 0.72 * B)
        soft = _rgba(CYAN, 0.40 * B)
        p0, p1 = P(R + 42), P(R + 55)
        self.lines([(*p0, *p1)], 1.6, main)
        for side in (-1, 1):                                  # yan çentikler
            q0, q1 = P(R + 46, side * 11), P(R + 51, side * 11)
            self.lines([(*q0, *q1)], 1.0, soft)

        r0 = R + 70
        if k in (0, 3):                       # sağ ve üst: içe bakan küçük ok + noktalar
            tip, b1, b2 = P(r0), P(r0 + 8, -5), P(r0 + 8, 5)
            self.polyline([tip, b1, b2], 1.1, main, closed=True)
            for j in (-7, 0, 7):
                q = P(r0 + 18, j)
                self.rect(q[0] - 1.4, q[1] - 1.4, 2.8, 2.8, soft)
        else:                                 # alt ve sol: kademeli çizgiler
            for j, L in enumerate((14, 9, 5)):
                q0, q1 = P(r0 + j * 6, -L / 2), P(r0 + j * 6, L / 2)
                self.lines([(*q0, *q1)], 1.2, main if j == 0 else soft)
        # ana yönün iki yanında küçük ikili kare grupları
        for side in (-1, 1):
            for j in (0, 1):
                q = P(R + 30, side * (24 + j * 5))
                self.rect(q[0] - 1.1, q[1] - 1.1, 2.2, 2.2, soft)

    # --------------------------------------------------------------- yazılar
    def draw_texts(self, st, now):
        if self.typing:
            self.draw_input(now)
        elif not self.show_settings:
            self.draw_write_button()

        frame = self._subtitle_frame(now, st == "speaking")
        if frame and not self.show_settings and not self.typing:
            lines, alpha = frame
            y = H - 132 + (2 - len(lines)) * 26
            for i, line in enumerate(lines):
                self.text(line, W / 2, y + i * 28, self.SUB_SIZE, (0.80, 0.95, 1.0),
                          0.86 * alpha, align="c")

        word, alpha = STATE_WORD.get(st, ""), 0.34
        if st == "idle":
            if self.voice_error:
                word = "SES KAPALI"
            elif self.llm_online is False:
                word = "OLLAMA KAPALI"
        if st in ("listening", "speaking"):
            alpha = 0.5
        if self.flash and now < self.flash[1]:
            word, alpha = self.flash[0], 0.6
        self.text(word, W / 2, H - 52, 14, TXT, alpha, align="c", spacing=4.0)

    def draw_input(self, now):
        y = H - 96
        self.rect(W / 2 - 320, y - 46, 640, 46, _rgba(CYAN, 0.05))
        self.lines([(W / 2 - 320, y, W / 2 + 320, y)], 1.0, _rgba(CYAN, 0.55))
        caret = "|" if int(now * 2) % 2 == 0 else " "
        if self.box.text:
            shown = self.box.view(lambda s: self.text_width(s, 20) <= 600, caret)
            self.text(shown, W / 2, y - 34, 20, (0.88, 0.97, 1.0), 0.95, align="c")
        else:
            self.text(caret + "JARVIS'e yaz…", W / 2, y - 34, 20, TXT, 0.35, align="c")
        self.text("ENTER gönder  ·  ESC kapat  ·  YUKARI OK önceki mesaj  ·  CTRL+V yapıştır  ·  CTRL+BOŞLUK konuş",
                  W / 2, y + 10, 11, TXT, 0.30, align="c", spacing=0.5)

    def _in_write_btn(self, mx, my):
        bx, by, bw, bh = self.write_btn
        return bx <= mx <= bx + bw and by <= my <= by + bh

    def draw_write_button(self):
        """Ekranı bozmayan, soluk "✎ YAZ" düğmesi; fare üstüne gelince belirginleşir. T tuşuyla da açılır."""
        bx, by, bw, bh = self.write_btn
        hover = self._in_write_btn(*self.mouse)
        self.polyline([(bx, by), (bx + bw, by), (bx + bw, by + bh), (bx, by + bh)], 1.0,
                      _rgba(CYAN, 0.75 if hover else 0.18), closed=True)
        if hover:
            self.rect(bx, by, bw, bh, _rgba(CYAN, 0.10))
        self.text("YAZ  ·  T", bx + bw / 2, by + bh / 2 - 8, 12, TXT, 0.85 if hover else 0.30,
                  align="c", spacing=1.5)

    # ------------------------------------------------------------ yazı kutusu
    def open_typing(self):
        self.typing = True
        self.box.clear()
        self._ignore_text_until = time.monotonic() + 0.2   # kutuyu açan "t" harfi yazılmasın
        try:
            pygame.key.start_text_input()
        except Exception:
            pass

    def close_typing(self):
        self.typing = False

    def _paste(self):
        text = ""
        try:
            import pyperclip
            text = pyperclip.paste() or ""
        except Exception:
            try:
                raw = pygame.scrap.get(pygame.SCRAP_TEXT) or b""
                text = raw.decode("utf-8", "ignore").replace("\x00", "")
            except Exception:
                text = ""
        self.box.insert(text)

    def _text_input(self, text):
        if not self.typing or self.space_held:
            return
        if time.monotonic() < self._ignore_text_until and text in ("t", "T"):
            self._ignore_text_until = 0.0
            return
        self.box.insert(text)

    def _typing_key(self, key, ctrl):
        box = self.box
        if key == pygame.K_ESCAPE:
            self.close_typing()
        elif key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            text = box.submit()
            self.close_typing()
            self.send(text)
        elif key == pygame.K_BACKSPACE:
            box.backspace(word=ctrl)
        elif key == pygame.K_DELETE:
            box.delete()
        elif key == pygame.K_LEFT:
            box.left(word=ctrl)
        elif key == pygame.K_RIGHT:
            box.right(word=ctrl)
        elif key == pygame.K_HOME:
            box.home()
        elif key == pygame.K_END:
            box.end()
        elif key == pygame.K_UP:
            box.history_prev()
        elif key == pygame.K_DOWN:
            box.history_next()
        elif key == pygame.K_v and ctrl:
            self._paste()
        elif key == pygame.K_SPACE and ctrl:
            self._ptt_down()

    # --------------------------------------------------------- ayar katmanı
    def _button(self, x, y, w, h, label, active, kind, value):
        self.rect(x, y, w, h, _rgba(CYAN, 0.16 if active else 0.03))
        self.polyline([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], 1.0,
                      _rgba(CYAN, 0.85 if active else 0.28), closed=True)
        self.text(label, x + w / 2, y + h / 2 - 8, 13, (0.9, 0.98, 1.0) if active else TXT,
                  0.95 if active else 0.6, align="c", spacing=1.5)
        self.settings_rects.append((kind, value, x, y, w, h))

    def draw_settings(self):
        self.settings_rects = []
        self.rect(0, 0, W, H, (0.0, 0.0, 0.0, 0.80))
        x, w = W / 2 - 270, 540
        y = 92
        self.text("AYARLAR", W / 2, y, 13, TXT, 0.55, align="c", spacing=6)
        self.lines([(x, y + 30, x + w, y + 30)], 1.0, _rgba(CYAN, 0.25))

        y += 52
        self.text("MOD   ·   M", x, y, 11, TXT, 0.45, spacing=2)
        bw = (w - 12) / 2
        for i, mode in enumerate(MODES):
            self._button(x + i * (bw + 12), y + 20, bw, 38, MODE_LABEL[mode],
                         self.mode == mode, "mode", mode)

        y += 84
        engine = str(_cfg("TTS_ENGINE", "edge")).upper()
        self.text(f"SES   ·   1 / 2 / 3      (motor: {engine})", x, y, 11, TXT, 0.45, spacing=2)
        voices = [("V1", "V1 · AHMET HIZLI"), ("V2", "V2 · EMEL"), ("V3", "V3 · AHMET TOK")]
        cur = self.current_voice()
        vw = (w - 24) / 3
        for i, (vid, label) in enumerate(voices):
            self._button(x + i * (vw + 12), y + 20, vw, 38, label, cur == vid, "voice", vid)

        y += 88
        self.text("PC", x, y, 11, TXT, 0.45, spacing=2)

        def pct(v):
            return "--" if v is None else f"{v:.0f}%"

        temp = self.cur.get("temp")
        rows = [
            ("İŞLEMCİ / BELLEK", f"{pct(self.cur.get('cpu'))}  ·  {pct(self.cur.get('ram'))}"),
            ("EKRAN KARTI / SICAKLIK",
             f"{pct(self.cur.get('gpu'))}  ·  {'--' if temp is None else f'{temp:.0f}°C'}"),
            ("MODEL", f"{_cfg('MODEL', '?')}  ·  OLLAMA "
                      f"{'AÇIK' if self.llm_online else 'KAPALI' if self.llm_online is False else '…'}"),
            ("SÜRÜM", f"JARVIS {_JARVIS_VERSION}  ·  {self.fps} FPS"),
        ]
        y += 22
        for label, val in rows:
            self.text(label, x, y, 12, TXT, 0.42)
            self.text(val, x + w, y, 12, (0.85, 0.96, 1.0), 0.85, align="r")
            y += 22

        y += 14
        self.text("TELEFON", x, y, 11, TXT, 0.45, spacing=2)
        y += 22
        if _cfg("PHONE_SERVER_ENABLED", True):
            self.text("ADRES", x, y, 12, TXT, 0.42)
            self.text(self.phone_addr or "…", x + w, y, 12, (0.85, 0.96, 1.0), 0.85, align="r")
            y += 22
            self.text("BAĞLANTI LİNKİ", x, y, 12, TXT, 0.42)
            self.text("data/phone_link.txt", x + w, y, 12, (0.85, 0.96, 1.0), 0.85, align="r")
        else:
            self.text("Telefon sunucusu kapalı (PHONE_SERVER_ENABLED)", x, y, 12, TXT, 0.42)
        y += 40

        bw = (w - 12) / 2
        self._button(x, y, bw, 38, "KAPAT  ·  ESC", False, "close", None)
        self._button(x + bw + 12, y, bw, 38, "JARVIS'İ KAPAT", False, "quit", None)
        self.text("BOŞLUK basılı tut: konuş   ·   T: yaz   ·   M: mod   ·   F11: tam ekran",
                  W / 2, y + 60, 11, TXT, 0.32, align="c", spacing=0.5)

    def _settings_click(self, mx, my):
        for kind, value, bx, by, bw, bh in self.settings_rects:
            if bx <= mx <= bx + bw and by <= my <= by + bh:
                if kind == "mode":
                    self.set_mode(value)
                elif kind == "voice":
                    self.set_voice(value)
                elif kind == "close":
                    self.show_settings = False
                elif kind == "quit":
                    self.running = False
                return True
        return False

    # ------------------------------------------------------------ olaylar
    def _to_logical(self, pos):
        vx, vy, vw, vh = self.vp
        s = max(self.scale, 1e-6)
        if self.owns_window:          # pygame y ekseni yukarıdan, viewport aşağıdan ölçülür
            ww, wh = pygame.display.get_window_size()
            vy = wh - vy - vh
        return (pos[0] - vx) / s, (pos[1] - vy) / s

    def _ptt_down(self):
        if self.space_held:
            return
        self.space_held = True
        if not self.voice_ready:
            self.show_subtitle("Ses tanıma henüz hazır değil." if not self.voice_error
                               else "Ses tanıma açılamadı.", speak_sync=False)
            return
        try:
            self.listener.start()
        except Exception as exc:
            self.show_subtitle("Mikrofon hatası: " + str(exc), speak_sync=False)
            print("MIC:", exc)

    def _ptt_up(self):
        if not self.space_held:
            return
        self.space_held = False
        if getattr(self.listener, "recording", False):
            self.transcribing = True
            threading.Thread(target=self._record_voice, daemon=True).start()

    def handle_event(self, event):
        if event.type == pygame.QUIT:
            self.running = False
        elif event.type == pygame.KEYDOWN:
            self._keydown(event)
        elif event.type == pygame.TEXTINPUT:
            self._text_input(event.text)
        elif event.type == pygame.KEYUP and event.key == pygame.K_SPACE:
            self._ptt_up()
        elif event.type == pygame.MOUSEMOTION:
            self.mouse = self._to_logical(event.pos)
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mx, my = self._to_logical(event.pos)
            if self.show_settings:
                self._settings_click(mx, my)
            elif not self.typing and self._in_write_btn(mx, my):
                self.open_typing()

    def _keydown(self, event):
        key = event.key
        ctrl = bool(pygame.key.get_mods() & pygame.KMOD_CTRL)
        if key == pygame.K_F11:
            self.toggle_fullscreen()
            return
        if self.typing:
            # v2.8: harfler TEXTINPUT olayından gelir (Türkçe ve AltGr karakterleri doğru gelsin)
            self._typing_key(key, ctrl)
            return
        if self.show_settings:
            if key == pygame.K_ESCAPE:
                self.show_settings = False
            elif key == pygame.K_m:
                self.toggle_mode()
            elif key in (pygame.K_1, pygame.K_2, pygame.K_3):
                self.set_voice("V" + str(key - pygame.K_0))
            return
        if key == pygame.K_ESCAPE:
            self.show_settings = True
        elif key == pygame.K_SPACE:
            self._ptt_down()
        elif key == pygame.K_t:
            self.open_typing()
        elif key == pygame.K_m:
            self.toggle_mode()

    def run(self):
        clock = pygame.time.Clock()
        fps = int(_cfg("HUD_FPS", 60) or 60)
        while self.running:
            for event in pygame.event.get():
                self.handle_event(event)
            self.drain()
            self.render()
            clock.tick(fps)
        self.running = False
        try:
            self.listener.close()
        except Exception:
            pass
        self.ctx.release()
        pygame.quit()


def GPUHUD(brain, *args, **kwargs):
    """main.py'nin kullandığı giriş. config.HUD_ESKI = True ise eski paneli açar."""
    if _cfg("HUD_ESKI", False):
        from interface.eski.gpu_hud import GPUHUD as EskiHUD
        return EskiHUD(brain, *args, **kwargs)
    return SadeHUD(brain, *args, **kwargs)
