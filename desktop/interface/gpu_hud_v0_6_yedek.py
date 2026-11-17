import math
import subprocess
import threading
import time
from collections import deque

import moderngl
import numpy as np
import pygame
import psutil

from voice.listener import VoiceListener
from voice.speaker import VoiceSpeaker

VERT = """
#version 330
in vec2 in_pos;
uniform vec2 resolution;
uniform vec4 rect;
void main() {
    vec2 p = rect.xy + in_pos * rect.zw;
    vec2 ndc = (p / resolution) * 2.0 - 1.0;
    gl_Position = vec4(ndc.x, -ndc.y, 0.0, 1.0);
}
"""

FRAG = """
#version 330
uniform vec4 color;
out vec4 fragColor;
void main() { fragColor = color; }
"""

LINE_VERT = """
#version 330
in vec2 in_pos;
uniform vec2 resolution;
void main() {
    vec2 ndc = (in_pos / resolution) * 2.0 - 1.0;
    gl_Position = vec4(ndc.x, -ndc.y, 0.0, 1.0);
}
"""

# The previous build mapped texture U directly from in_pos.x. On this setup
# the uploaded Pygame font textures appeared horizontally mirrored.
# Reversing U here fixes all HUD text while leaving geometry untouched.
TEXT_VERT = """
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
in vec2 uv;
out vec4 fragColor;
void main() {
    fragColor = texture(tex, uv);
}
"""


class GPUHUD:
    def __init__(self, brain, width=1280, height=720):
        self.brain = brain
        self.width, self.height = width, height

        pygame.init()
        pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MAJOR_VERSION, 3)
        pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MINOR_VERSION, 3)
        pygame.display.gl_set_attribute(pygame.GL_DOUBLEBUFFER, 1)

        self.screen = pygame.display.set_mode(
            (width, height), pygame.OPENGL | pygame.DOUBLEBUF
        )
        pygame.display.set_caption("JARVIS — TOOLS / MEMORY / WEB")

        self.ctx = moderngl.create_context()
        self.ctx.enable(moderngl.BLEND)
        self.ctx.blend_func = (
            moderngl.SRC_ALPHA,
            moderngl.ONE_MINUS_SRC_ALPHA,
        )

        self.program = self.ctx.program(
            vertex_shader=VERT, fragment_shader=FRAG
        )
        self.line_program = self.ctx.program(
            vertex_shader=LINE_VERT, fragment_shader=FRAG
        )
        self.text_program = self.ctx.program(
            vertex_shader=TEXT_VERT, fragment_shader=TEXT_FRAG
        )

        quad = np.array([0, 0, 1, 0, 0, 1, 1, 1], dtype="f4")
        self.quad = self.ctx.buffer(quad.tobytes())
        self.quad_vao = self.ctx.simple_vertex_array(
            self.program, self.quad, "in_pos"
        )
        self.text_vao = self.ctx.simple_vertex_array(
            self.text_program, self.quad, "in_pos"
        )

        self.font = pygame.font.SysFont("consolas", 20)
        self.small = pygame.font.SysFont("consolas", 15)
        self.big = pygame.font.SysFont("consolas", 38, bold=True)

        self.CYAN = (0.0, 0.86, 1.0, 1.0)
        self.SOFT = (0.08, 0.40, 0.52, 1.0)
        self.WHITE = (0.74, 0.92, 0.96, 1.0)
        self.GREEN = (0.15, 1.0, 0.65, 1.0)
        self.BG = (0.008, 0.022, 0.035, 1.0)

        self.input_text = ""
        self.status = "SYSTEM READY"
        self.messages = [("JARVIS", "Tool sistemi ve reactor hazır.")]
        self.waiting = False
        self.running = True
        self.space_held = False
        self.queue = deque()

        self.text_cache = {}
        self.cpu = self.ram = "0%"
        self.gpu = "--"
        self.clock_text = "--:--:--"
        self.fps = 0
        self.last_stats = 0
        self.last_gpu = 0
        self.fps_frames = 0
        self.fps_time = time.time()

        self.listener = VoiceListener(model_name="small")
        self.speaker = VoiceSpeaker()
        self.voice_ready = False
        threading.Thread(target=self._load_voice, daemon=True).start()

    def _load_voice(self):
        try:
            self.listener.load()
            self.voice_ready = True
            self.status = "VOICE READY"
        except Exception as exc:
            self.status = "VOICE ERROR"
            print("VOICE:", exc)

    def rect(self, x, y, w, h, color):
        self.program["resolution"].value = (self.width, self.height)
        self.program["rect"].value = (x, y, w, h)
        self.program["color"].value = color
        self.quad_vao.render(moderngl.TRIANGLE_STRIP)

    def line_batch(self, data, color, width=1):
        if not data:
            return
        buf = self.ctx.buffer(np.asarray(data, dtype="f4").tobytes())
        vao = self.ctx.simple_vertex_array(
            self.line_program, buf, "in_pos"
        )
        self.line_program["resolution"].value = (self.width, self.height)
        self.line_program["color"].value = color
        self.ctx.line_width = width
        vao.render(moderngl.LINES)
        vao.release()
        buf.release()

    def polyline(self, points, color, width=1):
        if len(points) < 4:
            return
        buf = self.ctx.buffer(np.asarray(points, dtype="f4").tobytes())
        vao = self.ctx.simple_vertex_array(
            self.line_program, buf, "in_pos"
        )
        self.line_program["resolution"].value = (self.width, self.height)
        self.line_program["color"].value = color
        self.ctx.line_width = width
        vao.render(moderngl.LINE_STRIP)
        vao.release()
        buf.release()

    def circle(self, cx, cy, radius, color, segments=96):
        points = []
        for i in range(segments + 1):
            a = math.tau * i / segments
            points.extend([
                cx + math.cos(a) * radius,
                cy + math.sin(a) * radius,
            ])
        self.polyline(points, color)

    def ellipse(self, cx, cy, rx, ry, rotation, color, segments=96):
        points = []
        cr, sr = math.cos(rotation), math.sin(rotation)
        for i in range(segments + 1):
            a = math.tau * i / segments
            x = math.cos(a) * rx
            y = math.sin(a) * ry
            points.extend([
                cx + x * cr - y * sr,
                cy + x * sr + y * cr,
            ])
        self.polyline(points, color)

    def text(self, value, x, y, font=None, color=None):
        font = font or self.font
        color = color or self.WHITE
        rgb = tuple(int(c * 255) for c in color[:3])
        key = (value, font.get_height(), rgb)

        tex = self.text_cache.get(key)
        if tex is None:
            surface = font.render(value, True, rgb).convert_alpha()

            # Cache static textures. Font surfaces are uploaded only once.
            tex = self.ctx.texture(
                surface.get_size(),
                4,
                pygame.image.tostring(surface, "RGBA", True),
            )
            tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
            self.text_cache[key] = tex

        tex.use(0)
        self.text_program["resolution"].value = (self.width, self.height)
        self.text_program["rect"].value = (
            x, y, tex.size[0], tex.size[1]
        )
        self.text_program["tex"].value = 0
        self.text_vao.render(moderngl.TRIANGLE_STRIP)

    def stats(self):
        now = time.time()

        if now - self.last_stats >= 1:
            self.cpu = f"{psutil.cpu_percent():.0f}%"
            self.ram = f"{psutil.virtual_memory().percent:.0f}%"
            self.clock_text = time.strftime("%H:%M:%S")
            self.last_stats = now

        if now - self.last_gpu >= 2:
            try:
                value = subprocess.check_output(
                    [
                        "nvidia-smi",
                        "--query-gpu=utilization.gpu",
                        "--format=csv,noheader,nounits",
                    ],
                    stderr=subprocess.DEVNULL,
                    timeout=1,
                ).decode().strip()
                self.gpu = value + "%"
            except Exception:
                self.gpu = "--"
            self.last_gpu = now

        self.fps_frames += 1
        if now - self.fps_time >= 1:
            self.fps = self.fps_frames
            self.fps_frames = 0
            self.fps_time = now

    def add_message(self, who, text):
        self.messages.append((who, str(text)))
        self.messages = self.messages[-8:]

    def wrap(self, text, width=76):
        result = []
        for paragraph in str(text).splitlines() or [""]:
            words = paragraph.split()
            if not words:
                result.append("")
                continue
            line = ""
            for word in words:
                test = word if not line else line + " " + word
                if len(test) > width and line:
                    result.append(line)
                    line = word
                else:
                    line = test
            if line:
                result.append(line)
        return result

    def send(self, text):
        text = text.strip()
        if not text or self.waiting:
            return
        self.add_message("USER", text)
        self.input_text = ""
        self.waiting = True
        self.status = (
            "RESEARCHING..."
            if self.brain.tools.web.should_search(text)
            else "PROCESSING..."
        )
        threading.Thread(
            target=self._ask, args=(text,), daemon=True
        ).start()

    def _ask(self, text):
        try:
            answer = self.brain.ask(text)
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
                self.waiting = True
                self.status = (
                    "RESEARCHING..."
                    if self.brain.tools.web.should_search(value)
                    else "PROCESSING..."
                )
                threading.Thread(
                    target=self._ask, args=(value,), daemon=True
                ).start()

            elif kind == "VOICE_ERROR":
                self.add_message(
                    "JARVIS", "Mikrofon hatası: " + value
                )
                self.status = "SYSTEM READY"

            elif kind == "ERROR":
                self.add_message("JARVIS", "Hata: " + value)
                self.waiting = False
                self.status = "SYSTEM READY"

            else:
                self.add_message("JARVIS", value)
                self.waiting = False
                self.status = "SYSTEM READY"
                self.speaker.speak(value)

    def render_reactor(self):
        cx, cy = self.width // 2, 292
        t = time.time()
        pulse = 9 + 8 * (0.5 + 0.5 * math.sin(t * 2.4))
        core = self.GREEN if self.listener.recording else self.CYAN

        # Soft multi-ring holographic glow.
        for r, alpha in [
            (126, 0.05),
            (112, 0.07),
            (98, 0.09),
            (86, 0.11),
            (74, 0.14),
        ]:
            self.circle(
                cx, cy, r,
                (core[0], core[1], core[2], alpha),
                72,
            )

        self.circle(cx, cy, 180, self.SOFT, 112)
        self.circle(cx, cy, 161, self.CYAN, 112)
        self.circle(cx, cy, 141, self.SOFT, 104)
        self.circle(cx, cy, 111, self.CYAN, 96)

        # Depth-like orbital rings.
        self.ellipse(
            cx, cy, 168, 40, t * 0.22, self.SOFT, 96
        )
        self.ellipse(
            cx, cy, 145, 31, -t * 0.36, self.CYAN, 96
        )
        self.ellipse(
            cx, cy, 118, 24, t * 0.50, self.SOFT, 96
        )

        # Outer ticks.
        ticks = []
        for i in range(54):
            a = t * 0.16 + i * math.tau / 54
            r1 = 166 if i % 3 else 158
            r2 = 174 if i % 3 else 181
            ticks.extend([
                cx + math.cos(a) * r1,
                cy + math.sin(a) * r1,
                cx + math.cos(a) * r2,
                cy + math.sin(a) * r2,
            ])
        self.line_batch(ticks, self.CYAN, 1)

        # Moving particles.
        particles = []
        for i in range(28):
            a = (
                t * (0.20 + (i % 5) * 0.018)
                + i * math.tau / 28
            )
            r = 122 + 9 * math.sin(t * 1.6 + i * 0.7)
            px = cx + math.cos(a) * r
            py = cy + math.sin(a) * r
            particles.extend([px - 2, py, px + 2, py])
        self.line_batch(particles, self.CYAN, 1)

        # Core rays.
        rays = []
        for i in range(18):
            a = -t * 0.75 + i * math.tau / 18
            r1 = 35
            r2 = 58 + 12 * math.sin(t * 2 + i)
            rays.extend([
                cx + math.cos(a) * r1,
                cy + math.sin(a) * r1,
                cx + math.cos(a) * r2,
                cy + math.sin(a) * r2,
            ])
        self.line_batch(rays, core, 1)

        self.circle(
            cx, cy, 51 + pulse,
            (core[0], core[1], core[2], 0.32),
            80
        )
        self.circle(
            cx, cy, 44 + pulse * 0.35,
            core,
            96
        )
        self.circle(cx, cy, 30, self.SOFT, 80)

        self.text("J A R V I S", cx - 71, cy - 10, self.font, core)
        self.text(self.status, cx - 54, cy + 66, self.small, self.SOFT)

    def render(self):
        self.stats()
        self.ctx.clear(*self.BG)

        self.text("J A R V I S", 30, 20, self.big, self.CYAN)
        self.text(
            self.clock_text,
            self.width - 115, 28,
            self.font, self.CYAN
        )
        self.text(
            f"{self.fps:02d} FPS",
            self.width - 115, 55,
            self.small, self.SOFT
        )

        panels = [
            (30, 65, "CPU", self.cpu),
            (30, 158, "RAM", self.ram),
            (self.width - 220, 65, "GPU", self.gpu),
            (
                self.width - 220, 158, "WEB",
                "READY" if self.brain.tools.web.available else "OFF",
            ),
        ]

        for x, y, title, value in panels:
            self.rect(
                x, y, 190, 78,
                (0.02, 0.15, 0.20, 0.84)
            )
            self.line_batch(
                [x, y, x + 70, y,
                 x, y + 78, x + 190, y + 78],
                self.SOFT, 1
            )
            self.text(title, x + 15, y + 10, self.small, self.CYAN)
            self.text(
                value, x + 15, y + 34, self.font,
                self.GREEN
                if title == "WEB" and value == "READY"
                else self.WHITE
            )

        self.render_reactor()

        x, y, w, h = 30, 462, 1220, 194
        self.rect(
            x, y, w, h,
            (0.02, 0.07, 0.09, 0.94)
        )
        self.line_batch(
            [x, y, x + w, y,
             x, y + h, x + w, y + h],
            self.SOFT, 1
        )
        self.text(
            "COMMUNICATION / TOOL CONSOLE",
            x + 15, y + 12, self.small, self.CYAN
        )

        all_lines = []
        for who, message in self.messages:
            for line in self.wrap(message):
                all_lines.append((who, line))

        for i, (who, line) in enumerate(all_lines[-5:]):
            yy = y + 40 + i * 20
            self.text(
                who + ":", x + 15, yy, self.small,
                self.CYAN if who == "JARVIS" else self.WHITE
            )
            self.text(
                line, x + 92, yy, self.small, self.WHITE
            )

        input_y = y + h - 33
        self.rect(
            x + 14, input_y, w - 28, 26,
            (0.01, 0.035, 0.05, 1.0)
        )
        self.text(
            "> " + self.input_text,
            x + 24, input_y + 4,
            self.small, self.CYAN
        )

        self.text(
            "[SPACE] BASILI TUT • KONUŞ • BIRAK",
            45, 678, self.small,
            self.GREEN if self.voice_ready else self.SOFT
        )

        pygame.display.flip()

    def run(self):
        clock = pygame.time.Clock()

        while self.running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.running = False

                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        self.running = False

                    elif event.key == pygame.K_SPACE and not self.space_held:
                        self.space_held = True
                        if self.voice_ready and not self.waiting:
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

                    elif event.unicode and event.unicode.isprintable():
                        self.input_text += event.unicode

                elif event.type == pygame.KEYUP and event.key == pygame.K_SPACE:
                    self.space_held = False
                    if self.listener.recording:
                        self.status = "TRANSCRIBING..."
                        threading.Thread(
                            target=self._record_voice,
                            daemon=True
                        ).start()

            self.drain()
            self.render()
            clock.tick(60)

        self.listener.close()
        self.ctx.release()
        pygame.quit()
