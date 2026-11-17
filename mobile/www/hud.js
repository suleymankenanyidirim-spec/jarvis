/* J.A.R.V.I.S. mobil — sade ekran çizici (v2.0)
 * PC'deki interface/gpu_hud.py ile aynı iki mod ve aynı hareket değerleri:
 *   cekirdek : yarı saydam, düzensiz, çok yüzlü kristal küre + beyaz-sıcak merkez + flamanlar + parçacıklar
 *   halka    : ince çember, bölmeli yaylar, sağda kalın parlak yay, dört yönde işaretler, kavisli taban
 * Durumlar: idle (sönük, yavaş) · listening (içe toplanır) · thinking (dönüş hızlanır) · speaking (sesle atar)
 * Çizim: WebGL (eklemeli karışım); WebGL yoksa canvas 2D. Pil tasarrufunda 30 FPS.
 * Kullanım: const hud = JarvisHud.create(canvas, { mode }); hud.setState("listening"); hud.setMode("halka");
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.JarvisHud = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";
  const TAU = Math.PI * 2;
  const CYAN = [0.04, 0.78, 1.0], ICE = [0.55, 0.93, 1.0], HOT = [1.0, 0.98, 0.93];
  const MODES = ["cekirdek", "halka"];
  const TARGETS = {
    idle:      { bright: 0.55, spin: 0.10, gather: 0.0, think: 0.0 },
    listening: { bright: 0.85, spin: 0.14, gather: 1.0, think: 0.0 },
    thinking:  { bright: 0.80, spin: 0.80, gather: 0.15, think: 1.0 },
    speaking:  { bright: 0.72, spin: 0.18, gather: 0.0, think: 0.0 },
  };

  // ------------------------------------------------------------ rastgele (tohumlu)
  function rng(seed) {
    let a = seed >>> 0;
    const next = () => {
      a = (a + 0x6d2b79f5) >>> 0;
      let t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
    next.normal = () => {
      const u = Math.max(1e-9, next()), v = next();
      return Math.sqrt(-2 * Math.log(u)) * Math.cos(TAU * v);
    };
    next.unit = () => {
      const x = next.normal(), y = next.normal(), z = next.normal();
      const l = Math.hypot(x, y, z) || 1;
      return [x / l, y / l, z / l];
    };
    return next;
  }

  // ------------------------------------------------------------ 3B geometri
  function icosphere(sub) {
    const t = (1 + Math.sqrt(5)) / 2;
    const v = [[-1, t, 0], [1, t, 0], [-1, -t, 0], [1, -t, 0], [0, -1, t], [0, 1, t],
      [0, -1, -t], [0, 1, -t], [t, 0, -1], [t, 0, 1], [-t, 0, -1], [-t, 0, 1]].map(norm);
    let f = [[0, 11, 5], [0, 5, 1], [0, 1, 7], [0, 7, 10], [0, 10, 11], [1, 5, 9], [5, 11, 4], [11, 10, 2],
      [10, 7, 6], [7, 1, 8], [3, 9, 4], [3, 4, 2], [3, 2, 6], [3, 6, 8], [3, 8, 9], [4, 9, 5], [2, 4, 11],
      [6, 2, 10], [8, 6, 7], [9, 8, 1]];
    for (let s = 0; s < sub; s++) {
      const cache = new Map(), out = [];
      const mid = (a, b) => {
        const k = a < b ? a + "_" + b : b + "_" + a;
        if (!cache.has(k)) { v.push(norm([v[a][0] + v[b][0], v[a][1] + v[b][1], v[a][2] + v[b][2]])); cache.set(k, v.length - 1); }
        return cache.get(k);
      };
      for (const [a, b, c] of f) {
        const ab = mid(a, b), bc = mid(b, c), ca = mid(c, a);
        out.push([a, ab, ca], [b, bc, ab], [c, ca, bc], [ab, bc, ca]);
      }
      f = out;
    }
    return { v, f };
  }
  function norm(p) { const l = Math.hypot(p[0], p[1], p[2]) || 1; return [p[0] / l, p[1] / l, p[2] / l]; }
  function uniqueEdges(f) {
    const seen = new Set(), out = [];
    for (const [a, b, c] of f) for (const [p, q] of [[a, b], [b, c], [c, a]]) {
      const k = p < q ? p * 4096 + q : q * 4096 + p;
      if (!seen.has(k)) { seen.add(k); out.push(p < q ? [p, q] : [q, p]); }
    }
    return out;
  }

  function buildCore(seed, nPart, nFil) {
    const r = rng(seed);
    const ico = icosphere(1);
    const v = ico.v.map((p) => {
      const q = norm([p[0] + r.normal() * 0.06, p[1] + r.normal() * 0.06, p[2] + r.normal() * 0.06]);
      const s = 0.84 + r() * 0.24;
      return [q[0] * s, q[1] * s, q[2] * s];
    });
    const f = ico.f;
    const fn = f.map(([a, b, c]) => {
      const A = v[a], B = v[b], C = v[c];
      const u = [B[0] - A[0], B[1] - A[1], B[2] - A[2]], w = [C[0] - A[0], C[1] - A[1], C[2] - A[2]];
      let n = norm([u[1] * w[2] - u[2] * w[1], u[2] * w[0] - u[0] * w[2], u[0] * w[1] - u[1] * w[0]]);
      const cen = [A[0] + B[0] + C[0], A[1] + B[1] + C[1], A[2] + B[2] + C[2]];
      if (n[0] * cen[0] + n[1] * cen[1] + n[2] * cen[2] < 0) n = [-n[0], -n[1], -n[2]];
      return n;
    });
    const edges = uniqueEdges(f).filter(() => r() > 0.18);
    const inner = icosphere(0);
    const iv = inner.v.map((p) => {
      const q = norm([p[0] + r.normal() * 0.1, p[1] + r.normal() * 0.1, p[2] + r.normal() * 0.1]);
      const s = (0.8 + r() * 0.3) * 0.52;
      return [q[0] * s, q[1] * s, q[2] * s];
    });
    const K = 9, fil = [];
    for (let i = 0; i < nFil; i++) {
      const d = r.unit(), len = 0.40 + r() * 0.98;
      const walk = [0, 0, 0], pts = [], wig = [];
      for (let k = 0; k < K; k++) {
        const s = k / (K - 1);
        if (k) { walk[0] += r.normal() * 0.055; walk[1] += r.normal() * 0.055; walk[2] += r.normal() * 0.055; }
        const reach = 0.03 + s * len;
        pts.push([d[0] * reach + walk[0] * s, d[1] * reach + walk[1] * s, d[2] * reach + walk[2] * s]);
        wig.push([r.normal() * 0.028 * s, r.normal() * 0.028 * s, r.normal() * 0.028 * s]);
      }
      fil.push({ pts, wig, ph: r() * TAU });
    }
    const parts = [];
    for (let i = 0; i < nPart; i++) {
      const d = r.unit();
      let rad = 1.05 + 0.95 * Math.pow(r(), 1.8);
      if (r() < 0.12) rad = 0.15 + r() * 0.8;
      parts.push({ d, rad, a: (0.15 + r() * 0.47) * (rad > 1.55 ? 0.55 : 1), s: 1.0 + r() * 1.4,
        ph: r() * TAU, sp: 0.6 + r() * 1.8 });
    }
    const vph = v.map(() => r() * TAU), fvar = f.map(() => r());
    return { v, f, fn, fvar, edges, iv, ie: uniqueEdges(inner.f), fil, K, parts, vph };
  }

  function rotation(yaw, pitch) {
    const cy = Math.cos(yaw), sy = Math.sin(yaw), cp = Math.cos(pitch), sp = Math.sin(pitch);
    // rx @ ry
    return [cy, 0, sy, sp * sy, cp, -sp * cy, -cp * sy, sp, cp * cy];
  }
  const rot = (m, p) => [m[0] * p[0] + m[1] * p[1] + m[2] * p[2], m[3] * p[0] + m[4] * p[1] + m[5] * p[2],
    m[6] * p[0] + m[7] * p[1] + m[8] * p[2]];
  function proj(p, cx, cy, R) { const k = 4.2 / (4.2 - p[2]); return [cx + p[0] * R * k, cy - p[1] * R * k, p[2]]; }
  const clamp = (x, a, b) => (x < a ? a : x > b ? b : x);

  // ========================================================== çiziciler
  function webglPainter(canvas) {
    const gl = canvas.getContext("webgl", { antialias: true, alpha: false, premultipliedAlpha: false,
      powerPreference: "low-power" }) || canvas.getContext("experimental-webgl");
    if (!gl) return null;
    const sh = (type, src) => { const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s);
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s)); return s; };
    const prog = (vs, fs) => { const p = gl.createProgram(); gl.attachShader(p, sh(gl.VERTEX_SHADER, vs));
      gl.attachShader(p, sh(gl.FRAGMENT_SHADER, fs)); gl.linkProgram(p);
      if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p)); return p; };
    let pTri, pGlow;
    try {
      pTri = prog(`attribute vec2 p; attribute vec4 c; uniform vec2 res; varying vec4 vc;
        void main(){ vec2 n = p / res * 2.0 - 1.0; gl_Position = vec4(n.x, -n.y, 0.0, 1.0); vc = c; }`,
      `precision mediump float; varying vec4 vc; void main(){ gl_FragColor = vc; }`);
      pGlow = prog(`attribute vec2 p; attribute vec2 uv; attribute vec4 c; attribute float pw; uniform vec2 res;
        varying vec2 vuv; varying vec4 vc; varying float vpw;
        void main(){ vec2 n = p / res * 2.0 - 1.0; gl_Position = vec4(n.x, -n.y, 0.0, 1.0); vuv = uv; vc = c; vpw = pw; }`,
      `precision mediump float; varying vec2 vuv; varying vec4 vc; varying float vpw;
        void main(){ float d = length(vuv); float a = pow(clamp(1.0 - d, 0.0, 1.0), vpw); gl_FragColor = vec4(vc.rgb, vc.a * a); }`);
    } catch (e) { console.warn("WebGL shader:", e); return null; }
    const triBuf = gl.createBuffer(), glowBuf = gl.createBuffer();
    let tri = new Float32Array(6 * 30000), tn = 0, glw = new Float32Array(9 * 6 * 32), gn = 0;
    const loc = {
      tp: gl.getAttribLocation(pTri, "p"), tc: gl.getAttribLocation(pTri, "c"), tres: gl.getUniformLocation(pTri, "res"),
      gp: gl.getAttribLocation(pGlow, "p"), guv: gl.getAttribLocation(pGlow, "uv"), gc: gl.getAttribLocation(pGlow, "c"),
      gpw: gl.getAttribLocation(pGlow, "pw"), gres: gl.getUniformLocation(pGlow, "res"),
    };
    let W = 1, H = 1;
    const vtx = (x, y, r, g, b, a) => {
      if (tn + 6 > tri.length) { const n = new Float32Array(tri.length * 2); n.set(tri); tri = n; }
      tri[tn++] = x; tri[tn++] = y; tri[tn++] = r; tri[tn++] = g; tri[tn++] = b; tri[tn++] = a;
    };
    const P = {
      kind: "webgl",
      begin(w, h, dpr) {
        W = w; H = h;
        const cw = Math.round(w * dpr), ch = Math.round(h * dpr);
        if (canvas.width !== cw || canvas.height !== ch) { canvas.width = cw; canvas.height = ch; }
        gl.viewport(0, 0, cw, ch);
        gl.clearColor(0, 0, 0, 1); gl.clear(gl.COLOR_BUFFER_BIT);
        gl.enable(gl.BLEND); gl.blendFunc(gl.SRC_ALPHA, gl.ONE);
        tn = 0; gn = 0;
      },
      tri(x1, y1, x2, y2, x3, y3, r, g, b, a) { vtx(x1, y1, r, g, b, a); vtx(x2, y2, r, g, b, a); vtx(x3, y3, r, g, b, a); },
      quad(ax, ay, bx, by, cx, cy, dx, dy, r, g, b, a) { P.tri(ax, ay, bx, by, cx, cy, r, g, b, a); P.tri(bx, by, dx, dy, cx, cy, r, g, b, a); },
      line(x1, y1, x2, y2, w, r, g, b, a) {
        const dx = x2 - x1, dy = y2 - y1, l = Math.hypot(dx, dy) || 1e-6, nx = -dy / l * w / 2, ny = dx / l * w / 2;
        P.quad(x1 + nx, y1 + ny, x1 - nx, y1 - ny, x2 + nx, y2 + ny, x2 - nx, y2 - ny, r, g, b, a);
      },
      dot(x, y, s, r, g, b, a) { const h = s / 2; P.quad(x - h, y - h, x + h, y - h, x - h, y + h, x + h, y + h, r, g, b, a); },
      arc(cx, cy, rad, a0, span, w, r, g, b, a) {
        const seg = Math.max(2, Math.ceil(Math.abs(span) * rad / 4)), r0 = rad - w / 2, r1 = rad + w / 2;
        let co = Math.cos(a0), si = Math.sin(a0);
        for (let i = 1; i <= seg; i++) {
          const t = a0 + span * i / seg, c2 = Math.cos(t), s2 = Math.sin(t);
          P.quad(cx + co * r1, cy + si * r1, cx + co * r0, cy + si * r0, cx + c2 * r1, cy + s2 * r1, cx + c2 * r0, cy + s2 * r0, r, g, b, a);
          co = c2; si = s2;
        }
      },
      glow(x, y, rad, r, g, b, a, pw) {
        if (gn + 54 > glw.length) { const n = new Float32Array(glw.length * 2); n.set(glw); glw = n; }
        const c = [[-1, -1], [1, -1], [-1, 1], [1, -1], [1, 1], [-1, 1]];
        for (const [u, v] of c) { glw.set([x + u * rad, y + v * rad, u, v, r, g, b, a, pw || 2], gn); gn += 9; }
      },
      end() {
        const F = 4;
        if (gn) {
          gl.useProgram(pGlow); gl.uniform2f(loc.gres, W, H);
          gl.bindBuffer(gl.ARRAY_BUFFER, glowBuf); gl.bufferData(gl.ARRAY_BUFFER, glw.subarray(0, gn), gl.DYNAMIC_DRAW);
          const st = 9 * F;
          gl.enableVertexAttribArray(loc.gp); gl.vertexAttribPointer(loc.gp, 2, gl.FLOAT, false, st, 0);
          gl.enableVertexAttribArray(loc.guv); gl.vertexAttribPointer(loc.guv, 2, gl.FLOAT, false, st, 2 * F);
          gl.enableVertexAttribArray(loc.gc); gl.vertexAttribPointer(loc.gc, 4, gl.FLOAT, false, st, 4 * F);
          gl.enableVertexAttribArray(loc.gpw); gl.vertexAttribPointer(loc.gpw, 1, gl.FLOAT, false, st, 8 * F);
          gl.drawArrays(gl.TRIANGLES, 0, gn / 9);
          [loc.gp, loc.guv, loc.gc, loc.gpw].forEach((l) => gl.disableVertexAttribArray(l));
        }
        if (tn) {
          gl.useProgram(pTri); gl.uniform2f(loc.tres, W, H);
          gl.bindBuffer(gl.ARRAY_BUFFER, triBuf); gl.bufferData(gl.ARRAY_BUFFER, tri.subarray(0, tn), gl.DYNAMIC_DRAW);
          gl.enableVertexAttribArray(loc.tp); gl.vertexAttribPointer(loc.tp, 2, gl.FLOAT, false, 6 * F, 0);
          gl.enableVertexAttribArray(loc.tc); gl.vertexAttribPointer(loc.tc, 4, gl.FLOAT, false, 6 * F, 2 * F);
          gl.drawArrays(gl.TRIANGLES, 0, tn / 6);
          gl.disableVertexAttribArray(loc.tp); gl.disableVertexAttribArray(loc.tc);
        }
      },
      lost: () => gl.isContextLost(),
    };
    return P;
  }

  function canvasPainter(canvas) {
    const ctx = canvas.getContext("2d");
    if (!ctx) return null;
    const css = (r, g, b, a) => `rgba(${(r * 255) | 0},${(g * 255) | 0},${(b * 255) | 0},${clamp(a, 0, 1).toFixed(3)})`;
    const P = {
      kind: "canvas",
      begin(w, h, dpr) {
        const cw = Math.round(w * dpr), ch = Math.round(h * dpr);
        if (canvas.width !== cw || canvas.height !== ch) { canvas.width = cw; canvas.height = ch; }
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.globalCompositeOperation = "source-over";
        ctx.fillStyle = "#000"; ctx.fillRect(0, 0, w, h);
        ctx.globalCompositeOperation = "lighter";
        ctx.lineCap = "butt";
      },
      tri(x1, y1, x2, y2, x3, y3, r, g, b, a) {
        if (a < 0.004) return;
        ctx.fillStyle = css(r, g, b, a); ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.lineTo(x3, y3); ctx.fill();
      },
      line(x1, y1, x2, y2, w, r, g, b, a) {
        if (a < 0.004) return;
        ctx.strokeStyle = css(r, g, b, a); ctx.lineWidth = w; ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
      },
      dot(x, y, s, r, g, b, a) { if (a < 0.004) return; ctx.fillStyle = css(r, g, b, a); ctx.fillRect(x - s / 2, y - s / 2, s, s); },
      arc(cx, cy, rad, a0, span, w, r, g, b, a) {
        if (a < 0.004) return;
        ctx.strokeStyle = css(r, g, b, a); ctx.lineWidth = w; ctx.beginPath(); ctx.arc(cx, cy, rad, a0, a0 + span); ctx.stroke();
      },
      glow(x, y, rad, r, g, b, a, pw) {
        pw = pw || 2;
        const gr = ctx.createRadialGradient(x, y, 0, x, y, rad);
        for (const s of [0, 0.15, 0.35, 0.6, 0.8, 1]) gr.addColorStop(s, css(r, g, b, a * Math.pow(1 - s, pw)));
        ctx.fillStyle = gr; ctx.beginPath(); ctx.arc(x, y, rad, 0, TAU); ctx.fill();
      },
      end() {},
      lost: () => false,
    };
    return P;
  }

  // ========================================================== sahneler
  function drawCore(P, m, core, w, h) {
    const cx = w / 2, cy = h * 0.45, base = Math.min(w, h) * 0.31, u = base / 158;
    const lv = m.level, B = m.bright + 0.5 * lv;
    const R = base * (1 + 0.022 * Math.sin(m.t * 0.9) + 0.05 * lv);
    const M = rotation(m.angle, 0.36 + 0.07 * Math.sin(m.t * 0.21));
    const lw = Math.max(0.7, 1.25 * u);

    P.glow(cx, cy, 460 * u, 0.0, 0.28, 0.46, 0.12 * B, 3.2);
    const V = core.v.map((p) => proj(rot(M, p), cx, cy, R));
    const L = norm([-0.45, 0.55, 0.70]);
    core.f.forEach(([a, b, c], i) => {
      const n = rot(M, core.fn[i]), nz = n[2];
      const rim = (1 - Math.abs(nz)) ** 2;
      const spec = Math.pow(clamp(n[0] * L[0] + n[1] * L[1] + n[2] * L[2], 0, 1), 12);
      const al = ((0.016 + 0.07 * rim + 0.03 * core.fvar[i]) * (nz > 0 ? 1 : 0.45) + 0.17 * spec) * B;
      const s = clamp(spec * 0.9, 0, 1);
      P.tri(V[a][0], V[a][1], V[b][0], V[b][1], V[c][0], V[c][1],
        CYAN[0] * (1 - s) + HOT[0] * s, CYAN[1] * (1 - s) + HOT[1] * s, CYAN[2] * (1 - s) + HOT[2] * s, al);
    });
    for (const [a, b] of core.edges) {
      const d = clamp(((V[a][2] + V[b][2]) / 2 + 1.1) / 2.2, 0, 1), k = d * 0.6;
      P.line(V[a][0], V[a][1], V[b][0], V[b][1], lw,
        CYAN[0] * (1 - k) + ICE[0] * k, CYAN[1] * (1 - k) + ICE[1] * k, CYAN[2] * (1 - k) + ICE[2] * k,
        (0.10 + 0.62 * Math.pow(d, 1.6)) * B);
    }
    V.forEach((p, i) => {
      if (p[2] > 0.25) P.dot(p[0], p[1], 2.2 * u + 0.6, ICE[0], ICE[1], ICE[2],
        (0.15 + 0.55 * (0.5 + 0.5 * Math.sin(m.t * 1.7 + core.vph[i] * 3))) * B);
    });
    const M2 = rotation(-m.angle * 0.7 + 1.3, -0.25);
    const IV = core.iv.map((p) => proj(rot(M2, p), cx, cy, R));
    for (const [a, b] of core.ie) {
      const d = clamp(((IV[a][2] + IV[b][2]) / 2 + 0.6) / 1.2, 0, 1);
      P.line(IV[a][0], IV[a][1], IV[b][0], IV[b][1], Math.max(0.6, u), ICE[0], ICE[1], ICE[2], (0.05 + 0.16 * d) * B);
    }
    // flamanlar
    const K = core.K, grow = 1 + 0.28 * lv - 0.12 * m.gather, fsp = 1.1 + 2.6 * m.think;
    for (const f of core.fil) {
      const flick = 0.62 + 0.38 * Math.sin(m.t * (2 + 11 * m.think) + f.ph * 7);
      let prev = null;
      for (let k = 0; k < K; k++) {
        const wv = Math.sin(m.t * fsp + f.ph + k * 0.8), p0 = f.pts[k], wg = f.wig[k];
        const q = proj(rot(M, [(p0[0] + wg[0] * wv) * grow, (p0[1] + wg[1] * wv) * grow, (p0[2] + wg[2] * wv) * grow]), cx, cy, R);
        if (prev) {
          const s = (k - 0.5) / (K - 1), pa = Math.pow(1 - s, 1.3);
          const dep = clamp(((prev[2] + q[2]) / 2 + 1.4) / 2.8, 0.25, 1);
          P.line(prev[0], prev[1], q[0], q[1], Math.max(0.6, u),
            HOT[0] * (1 - s) * 0.8 + CYAN[0] * s + ICE[0] * (1 - s) * 0.2,
            HOT[1] * (1 - s) * 0.8 + CYAN[1] * s + ICE[1] * (1 - s) * 0.2,
            HOT[2] * (1 - s) * 0.8 + CYAN[2] * s + ICE[2] * (1 - s) * 0.2, pa * flick * dep * 0.95 * B);
        }
        prev = q;
      }
    }
    // beyaz-sıcak merkez
    P.glow(cx, cy, 200 * u * (1 + 0.2 * lv), 0.04, 0.55, 0.95, 0.20 * B, 2.0);
    P.glow(cx, cy, 82 * u * (1 + 0.35 * lv), 0.55, 0.92, 1.0, 0.50 * B, 2.2);
    P.glow(cx, cy, 32 * u * (1 + 0.55 * lv), 1.0, 0.98, 0.92, Math.min(1, 0.95 * B), 1.5);
    P.glow(cx, cy, 11 * u * (1 + 0.5 * lv), 1, 1, 1, 1, 1.0);
    // parçacıklar
    const g = m.gather, M3 = rotation(m.angle * 0.55 + g * 0.3, 0.30);
    for (const pt of core.parts) {
      const rs = pt.rad * (1 - 0.34 * g) + 0.07 * lv * Math.sin(m.t * 3 + pt.ph);
      const q = proj(rot(M3, [pt.d[0] * rs, pt.d[1] * rs, pt.d[2] * rs]), cx, cy, R);
      const tw = 0.5 + 0.5 * Math.sin(m.t * pt.sp + pt.ph);
      P.dot(q[0], q[1], pt.s * Math.max(0.8, u), ICE[0], ICE[1], ICE[2],
        pt.a * (0.35 + 0.65 * tw) * (0.5 + 0.5 * clamp((q[2] + 1.9) / 3.8, 0, 1)) * (0.55 + 0.6 * B) * (1 + 0.7 * g));
    }
  }

  const RING_ARCS = [
    [13, 2.4, [2.05, 3.35, 4.55], [0.80, 0.42, 1.10], 0.32, 0.62],
    [-13, 5.0, [3.55, 5.15, 0.95], [0.55, 0.30, 0.22], -0.22, 0.42],
    [22, 1.0, [1.00, 4.10], [1.40, 0.90], 0.12, 0.32],
    [-25, 1.2, [0.20, 2.80], [0.70, 1.60], -0.45, 0.26],
  ];

  function drawRing(P, m, w, h) {
    const cx = w / 2, cy = h * 0.44, R = Math.min(w, h) * 0.29, u = R / 150;
    const lv = m.level, B = m.bright + 0.45 * lv, g = m.gather;
    const rr = (dr) => R + dr * u * (1 - 0.6 * g);
    const C = CYAN, I = ICE;
    P.glow(cx, cy, R * 2.6, 0.0, 0.25, 0.45, 0.06 * B, 3.2);
    P.arc(cx, cy, R, 0, TAU, Math.max(0.8, 1.3 * u), C[0], C[1], C[2], 0.58 * B);
    P.arc(cx, cy, rr(-36), 0, TAU, Math.max(0.6, 0.9 * u), C[0], C[1], C[2], 0.12 * B);
    for (let i = 0; i < 120; i++) {
      const a = i * TAU / 120 + m.angle * 0.15, major = i % 10 === 0, r0 = R - (major ? 10 : 5) * u;
      const co = Math.cos(a), si = Math.sin(a);
      P.line(cx + co * r0, cy + si * r0, cx + co * (R - 2 * u), cy + si * (R - 2 * u), Math.max(0.6, u),
        C[0], C[1], C[2], (major ? 0.45 : 0.16) * B);
    }
    for (const [dr, wd, starts, spans, spd, a] of RING_ARCS) {
      const r = rr(dr), off = m.angle * spd * 3;
      starts.forEach((st, i) => P.arc(cx, cy, r, st + off, spans[i], wd * u, C[0], C[1], C[2], a * B));
    }
    const rd = rr(31);
    for (let i = 0; i < 96; i++) if (i % 8 < 5) P.arc(cx, cy, rd, i * TAU / 96 - m.angle * 0.5, 0.032, Math.max(0.7, 1.2 * u), I[0], I[1], I[2], 0.30 * B);
    // sağda kalın parlak yay
    const span = 0.46 + 1.0 * lv + 0.10 * m.think * (0.5 + 0.5 * Math.sin(m.t * 6));
    const wd = (6 + 4 * lv) * u, rk = R + 3 * u, hot = clamp(0.75 + 0.25 * lv, 0, 1) * 0.5;
    P.arc(cx, cy, rk, -span / 2, span, wd, C[0] * (1 - hot) + HOT[0] * hot, C[1] * (1 - hot) + HOT[1] * hot,
      C[2] * (1 - hot) + HOT[2] * hot, Math.min(1, 0.70 + 0.45 * B));
    P.arc(cx, cy, rk + 9 * u, -span * 0.62, span * 1.24, Math.max(0.7, 1.4 * u), I[0], I[1], I[2], 0.45 * B);
    P.glow(cx + rk, cy, (70 + 50 * lv) * u, 0.2, 0.8, 1.0, 0.16 * B + 0.12 * lv, 2.0);
    [0, Math.PI / 2, Math.PI, 1.5 * Math.PI].forEach((a, k) => cardinal(P, cx, cy, R, u, a, k, B));
    // kavisli taban
    const y0 = cy + R + 96 * u, Rb = 1300 * u, bcy = y0 - Rb, N = 90;
    let px = null, py = null;
    for (let i = 0; i <= N; i++) {
      const s = -1 + 2 * i / N, th = Math.PI / 2 + s * 0.31, x = cx + Math.cos(th) * Rb, y = bcy + Math.sin(th) * Rb;
      if (px !== null) { const mid = Math.abs(s - 1 / N); P.line(px, py, x, y, Math.max(0.7, 1.1 * u), C[0], C[1], C[2], Math.pow(1 - mid, 1.6) * 0.55 * B); }
      px = x; py = y;
    }
    P.line(cx, y0 + 5 * u, cx, y0 + 13 * u, Math.max(0.7, 1.2 * u), I[0], I[1], I[2], 0.5 * B);
    for (const sx of [-46, 46]) P.dot(cx + sx * u, y0 + 7.5 * u, 3 * u, C[0], C[1], C[2], 0.38 * B);
  }

  function cardinal(P, cx, cy, R, u, a, k, B) {
    const ux = Math.cos(a), uy = Math.sin(a), tx = -Math.sin(a), ty = Math.cos(a);
    const at = (r, t) => [cx + ux * r * u + tx * t * u, cy + uy * r * u + ty * t * u];
    const Rr = R / u, lw = Math.max(0.7, 1.1 * u);
    const main = [ICE[0], ICE[1], ICE[2], 0.72 * B], soft = [CYAN[0], CYAN[1], CYAN[2], 0.40 * B];
    const L = (p, q, wd, c) => P.line(p[0], p[1], q[0], q[1], wd, c[0], c[1], c[2], c[3]);
    L(at(Rr + 42, 0), at(Rr + 55, 0), Math.max(0.8, 1.6 * u), main);
    for (const s of [-1, 1]) L(at(Rr + 46, s * 11), at(Rr + 51, s * 11), lw, soft);
    const r0 = Rr + 70;
    if (k === 0 || k === 3) {
      const tip = at(r0, 0), b1 = at(r0 + 8, -5), b2 = at(r0 + 8, 5);
      L(tip, b1, lw, main); L(b1, b2, lw, main); L(b2, tip, lw, main);
      for (const j of [-7, 0, 7]) { const q = at(r0 + 18, j); P.dot(q[0], q[1], 2.8 * u, soft[0], soft[1], soft[2], soft[3]); }
    } else {
      [14, 9, 5].forEach((len, j) => L(at(r0 + j * 6, -len / 2), at(r0 + j * 6, len / 2), lw, j ? soft : main));
    }
    for (const s of [-1, 1]) for (const j of [0, 1]) { const q = at(Rr + 30, s * (24 + j * 5)); P.dot(q[0], q[1], 2.2 * u, soft[0], soft[1], soft[2], soft[3]); }
  }

  // ========================================================== ana nesne
  function create(canvas, opts) {
    opts = opts || {};
    let painter = null;
    if (!opts.forceCanvas) { try { painter = webglPainter(canvas); } catch (_) { painter = null; } }
    if (!painter) painter = canvasPainter(canvas);
    const small = Math.min(window.innerWidth || 400, window.innerHeight || 700) < 500;
    const core = buildCore(7, painter.kind === "webgl" ? (small ? 480 : 650) : 320, painter.kind === "webgl" ? 56 : 40);
    const m = { bright: 0.55, spin: 0.10, gather: 0, think: 0, level: 0, angle: 0, t: 0 };
    const hud = {
      mode: MODES.includes(opts.mode) ? opts.mode : "cekirdek",
      state: "idle",
      renderer: painter.kind,
      lowPower: false,
      levelSource: null,
      setState(s) { if (TARGETS[s]) hud.state = s; },
      setMode(md) { if (MODES.includes(md)) hud.mode = md; },
      nextMode() { hud.mode = MODES[(MODES.indexOf(hud.mode) + 1) % MODES.length]; return hud.mode; },
      setLowPower(on) { hud.lowPower = !!on; },
      setLevelSource(fn) { hud.levelSource = fn; },
      frame,
    };
    let last = 0, raf = 0;
    function level(t) {
      let v = null;
      if (hud.levelSource) { try { v = hud.levelSource(hud.state); } catch (_) { v = null; } }
      if (v !== null && v !== undefined) return clamp(v, 0, 1);
      if (hud.state === "speaking") return 0.30 + 0.45 * Math.abs(Math.sin(t * 7.3) * Math.sin(t * 2.1 + 1));
      if (hud.state === "listening") return 0.15 + 0.25 * Math.abs(Math.sin(t * 5.1) * Math.sin(t * 1.7 + 0.4));
      return 0;
    }
    function frame(now) {
      raf = requestAnimationFrame(frame);
      if (document.hidden) return;
      const fps = hud.lowPower ? 30 : 60;
      if (last && now - last < 1000 / fps - 2) return;
      const dt = last ? Math.min(0.1, (now - last) / 1000) : 1 / 60;
      last = now;
      const tg = TARGETS[hud.state] || TARGETS.idle, k = Math.min(1, dt * 2.6);
      for (const n in tg) m[n] += (tg[n] - m[n]) * k;
      const lv = level(m.t), kl = Math.min(1, dt * (lv > m.level ? 20 : 7));
      m.level += (lv - m.level) * kl;
      m.angle += m.spin * dt; m.t += dt;
      const w = canvas.clientWidth || window.innerWidth, h = canvas.clientHeight || window.innerHeight;
      const dpr = Math.min(window.devicePixelRatio || 1, hud.lowPower ? 1.5 : 2);
      painter.begin(w, h, dpr);
      if (hud.mode === "halka") drawRing(painter, m, w, h); else drawCore(painter, m, core, w, h);
      painter.end();
    }
    raf = requestAnimationFrame(frame);
    canvas.addEventListener("webglcontextlost", (e) => { e.preventDefault(); cancelAnimationFrame(raf); });
    canvas.addEventListener("webglcontextrestored", () => { painter = webglPainter(canvas) || painter; raf = requestAnimationFrame(frame); });
    return hud;
  }

  return { create, MODES, TARGETS, buildCore, icosphere, uniqueEdges };
});
