"""
Telefon Bağlantısı
==================
Telefondan (ev WiFi'si ya da Tailscale ile her yerden) JARVIS'e bağlanmak için küçük bir web sunucusu.

- http://<PC_IP>:5000/?pair=KOD&pcs=…  -> telefon arayüzünü (PWA) açar ve tek kullanımlık kodla eşleşir
- /api/*                               -> telefon uygulamasının (APK / GitHub Pages) kullandığı JSON API

Güvenlik:
- v1.4: tüm API çağrıları bir erişim anahtarı ister. Anahtar config.PHONE_TOKEN'dan alınır; boşsa
  data/phone_token.txt içinde üretilir.
- v2.6: anahtar yalnızca X-Jarvis-Token başlığında kabul edilir (?token= ve gövdedeki token yok sayılır),
  böylece URL'lere, tarayıcı geçmişine ve günlüklere düşmez. İlk eşleşme PC'nin ürettiği tek kullanımlık,
  kısa ömürlü kodla yapılır (POST /api/pair). IP başına hatalı deneme sınırı (429 + Retry-After).
  CORS yalnızca izinli kaynaklara. /api/tts POST oldu.
"""

import asyncio
import base64
import hmac
import inspect
import ipaddress
import json
import math
import secrets
import socket
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import quote

from flask import Flask, Response, jsonify, request, send_from_directory, stream_with_context

from core.paths import BASE_DIR, data_path

ROOT = BASE_DIR
TOKEN_FILE = data_path("phone_token.txt")
LINK_FILE = data_path("phone_link.txt")   # v2.2: LAN + Tailscale linkleri


def _config(name, default=None):
    import config
    return getattr(config, name, default)


def load_token():
    token = str(_config("PHONE_TOKEN", "") or "").strip()
    if token:
        return token
    try:
        token = TOKEN_FILE.read_text(encoding="utf8").strip()
    except OSError:
        token = ""
    if not token:
        token = secrets.token_urlsafe(9)
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(token, encoding="utf8")
    return token


# ------------------------------------------------------------------ v2.6: eşleşme kodu
PAIR_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"      # karışan 0/O/1/I yok
DEFAULT_CORS_ORIGINS = ("https://localhost", "http://localhost", "capacitor://localhost")
LOCAL_ADDRS = ("127.0.0.1", "::1", "::ffff:127.0.0.1")


def normalize_pair_code(code):
    return "".join(ch for ch in str(code or "").upper() if ch.isalnum())


def format_pair_code(code):
    code = normalize_pair_code(code)
    return code[:4] + "-" + code[4:] if len(code) > 4 else code


class PairingCodes:
    """Tek kullanımlık, kısa ömürlü eşleşme kodu. Bir koda max_failures hatalı denemede kod iptal olur."""

    def __init__(self, ttl=300, length=8, max_failures=5, clock=time.monotonic):
        self.ttl = float(ttl)
        self.length = int(length)
        self.max_failures = int(max_failures)
        self.clock = clock
        self._lock = threading.Lock()
        self._code = None
        self._expires = 0.0
        self._failures = 0

    def new(self):
        with self._lock:
            self._code = "".join(secrets.choice(PAIR_ALPHABET) for _ in range(self.length))
            self._expires = self.clock() + self.ttl
            self._failures = 0
            return self._code

    def _valid(self):
        if self._code and self.clock() >= self._expires:
            self._code = None
        return self._code

    def current(self):
        with self._lock:
            return self._valid()

    def expires_in(self):
        with self._lock:
            return max(0, int(math.ceil(self._expires - self.clock()))) if self._valid() else 0

    def redeem(self, code):
        with self._lock:
            active = self._valid()
            if not active:
                return False
            if hmac.compare_digest(normalize_pair_code(code), active):
                self._code = None                 # tek kullanımlık
                return True
            self._failures += 1
            if self._failures >= self.max_failures:
                self._code = None                 # çok hatalı deneme: kod iptal
            return False


class FailureLimiter:
    """IP başına hatalı anahtar/kod sınırı: window saniyede max_failures hata -> lockout saniye kilit."""

    def __init__(self, max_failures=10, window=300, lockout=600, clock=time.monotonic):
        self.max_failures = max(1, int(max_failures))
        self.window = float(window)
        self.lockout = float(lockout)
        self.clock = clock
        self._lock = threading.Lock()
        self._fails = {}
        self._locked = {}

    def retry_after(self, key):
        with self._lock:
            until = self._locked.get(key, 0.0)
            left = until - self.clock()
            if left <= 0:
                self._locked.pop(key, None)
                return 0
            return int(math.ceil(left))

    def fail(self, key):
        with self._lock:
            now = self.clock()
            recent = [t for t in self._fails.get(key, []) if now - t < self.window]
            recent.append(now)
            if len(recent) >= self.max_failures:
                self._locked[key] = now + self.lockout
                recent = []
            self._fails[key] = recent

    def success(self, key):
        with self._lock:
            self._fails.pop(key, None)


def cors_origins():
    """İzinli CORS kaynakları; GITHUB_REPO doluysa https://<kullanıcı>.github.io eklenir."""
    value = _config("PHONE_CORS_ORIGINS", list(DEFAULT_CORS_ORIGINS))
    if isinstance(value, str):
        value = value.split(",")
    out = []
    for item in value or []:
        item = str(item).strip().rstrip("/")
        if item and item not in out:
            out.append(item)
    repo = str(_config("GITHUB_REPO", "") or "").strip()
    if repo and "/" in repo and _config("PHONE_CORS_GITHUB_PAGES", True):
        pages = f"https://{repo.split('/')[0].strip().lower()}.github.io"
        if pages not in out:
            out.append(pages)
    return out


def phone_session(value):
    """v2.3: telefonun gönderdiği oturum kimliği. Geçersizse ya da task-… ise PHONE_SESSION_ID kullanılır."""
    from core.memory import valid_session
    default = str(_config("PHONE_SESSION_ID", "phone") or "phone")
    value = str(value or "").strip()
    if not value or not valid_session(value) or value.lower().startswith("task-"):
        return default
    return value


def _int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def sse_format(event):
    """Tek SSE olayı: `event: <type>` + `data: <JSON>` (Türkçe karakterler kaçışsız, satır sonu kaçışlı)."""
    return f"event: {event.get('type', 'message')}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


def find_ui_dir():
    for candidate in (ROOT / "phone_ui", ROOT.parent / "mobile" / "www"):
        if (candidate / "index.html").exists():
            return candidate
    return None


def _version():
    try:
        return (ROOT / "VERSION").read_text(encoding="utf8").strip()
    except OSError:
        return "?"


FALLBACK_PAGE = """<!DOCTYPE html><html lang="tr"><meta name="viewport" content="width=device-width,initial-scale=1">
<body style="background:#05090c;color:#bfefff;font-family:sans-serif;padding:24px">
<h2 style="color:#00dbff">J.A.R.V.I.S.</h2>
<p>Telefon arayüzü (phone_ui klasörü) bulunamadı. GitHub'daki son sürümü indir
ya da telefona JARVIS uygulamasını kur ve PC adresi olarak bu sayfanın adresini gir.</p>
</body></html>"""


class PhoneServer:
    def __init__(self, brain, port=5000):
        self.brain = brain
        self.port = port
        self.token = load_token()
        self.ui_dir = find_ui_dir()
        self.links = {}
        self.limiter = FailureLimiter(_config("PHONE_AUTH_MAX_FAILURES", 10), _config("PHONE_AUTH_WINDOW", 300),
                                      _config("PHONE_AUTH_LOCKOUT", 600))
        self.pairing = PairingCodes(ttl=_config("PHONE_PAIR_CODE_TTL", 300),
                                    max_failures=_config("PHONE_PAIR_MAX_FAILURES", 5))
        self.app = Flask("jarvis_phone")
        # v2.8: telefondan dosya gönderimi (base64 ~%35 büyütür) için üst sınır
        upload_mb = float(_config("KNOWLEDGE_MAX_UPLOAD_MB", 10) or 10)
        self.app.config["MAX_CONTENT_LENGTH"] = int((upload_mb * 1.4 + 1) * 1024 * 1024)
        self._routes()

    # ------------------------------------------------------------- helpers
    @staticmethod
    def _client():
        """İsteği gönderen IP. v2.8: `tailscale serve` (https) arkasında istekler 127.0.0.1'den gelir;
        o zaman gerçek istemci X-Forwarded-For'dan okunur. Böylece telefon "PC'nin kendisi" sayılmaz
        (yeni eşleşme kodu isteyemez) ve deneme sınırı her cihaz için ayrı işler."""
        ip = request.remote_addr or "?"
        if ip in LOCAL_ADDRS and _config("PHONE_TRUST_LOCAL_PROXY", True):
            fwd = request.headers.get("X-Forwarded-For", "")
            if fwd.strip():
                return fwd.split(",")[0].strip() or "proxy"
            if request.headers.get("Tailscale-User-Login"):
                return "tailscale-proxy"
        return ip

    def _authorized(self):
        """v2.6: anahtar yalnızca X-Jarvis-Token başlığında. Hatalı anahtar IP başına sayılır;
        kilitliyken doğru anahtar da geçmez. Hiç anahtar gönderilmemişse deneme sayılmaz."""
        ip = self._client()
        if self.limiter.retry_after(ip):
            return False
        given = str(request.headers.get("X-Jarvis-Token", "") or "").strip()
        if not given:
            return False
        if hmac.compare_digest(given.encode("utf8"), self.token.encode("utf8")):
            self.limiter.success(ip)
            return True
        self.limiter.fail(ip)
        return False

    def _too_many(self, wait):
        resp = jsonify({"error": f"Çok fazla hatalı deneme. {wait} saniye sonra tekrar deneyin.",
                        "retry_after": wait})
        resp.status_code = 429
        resp.headers["Retry-After"] = str(wait)
        return resp

    @staticmethod
    def _tagged(text):
        return "[Telefondan] " + text if _config("PHONE_TAG_MESSAGES", False) else text

    def _deny(self):
        wait = self.limiter.retry_after(self._client())
        if wait:
            return self._too_many(wait)
        return jsonify({"error": "Yetkisiz. Telefonu PC ile eşleştir (AYAR → Eşleşme kodu) "
                                 "ya da PC'deki erişim anahtarını gir."}), 401

    def new_pair_code(self):
        """Yeni eşleşme kodu üretir, link dosyasını yeni kodla günceller ve kodu döndürür."""
        code = self.pairing.new()
        links = build_links(self.links.get("lan_ip", ""), self.links.get("ts_ip", ""), self.port, code)
        write_link_file(link_report(links, code) + remote_report(code))
        print(f"[TELEFON] Yeni eşleşme kodu: {format_pair_code(code)} "
              f"({int(self.pairing.ttl // 60)} dakika geçerli, tek kullanımlık)")
        return code

    def _audio(self, text):
        """_synthesize sonucu: (ses, mime) ya da yalnızca ses; eşzamansız olabilir."""
        result = self._synthesize(text)
        if inspect.isawaitable(result):
            result = asyncio.run(result)
        if isinstance(result, tuple):
            return result[0], result[1]
        return result, "audio/mpeg"

    def _status(self):
        import config
        try:
            providers = self.brain.council.available()
        except Exception:
            providers = {}
        try:
            tasks = self.brain.task_status()
            running = sum(1 for t in tasks if t.get("status") in ("queued", "running", "recovering"))
        except Exception:
            running = 0
        ollama = False
        try:
            import requests
            base = str(getattr(config, "OLLAMA_URL", "")).split("/api")[0]
            requests.get(base, timeout=1.5).raise_for_status()
            ollama = True
        except Exception:
            pass
        return {
            "name": "JARVIS",
            "version": _version(),
            "model": getattr(config, "MODEL", ""),
            "ollama": ollama,
            "providers": providers,
            "tasks_running": running,
            "helmet": bool(getattr(config, "HELMET_WIFI_IP", "") or getattr(config, "HELMET_BT_PORT", "")),
            "tts": self._tts_status(),
        }

    @staticmethod
    def _tts_status():
        try:
            from voice import tts
            return tts.status()
        except Exception as exc:
            return {"error": str(exc)[:120]}

    def _synthesize(self, text):
        """v2.2: masaüstüyle aynı ses motoru (config.TTS_ENGINE + yedek zinciri)."""
        from voice import tts
        audio, fmt, _engine = tts.synthesize(text)
        return audio, ("audio/wav" if fmt == "wav" else "audio/mpeg")

    def _helmet(self, action):
        helmet = self.brain.tools.helmet
        if action == "ac":
            return helmet.ac()
        if action == "kapat":
            return helmet.kapat()
        if action == "durum":
            return helmet.durum()
        return "Geçersiz kask komutu."

    # -------------------------------------------------------------- routes
    def _routes(self):
        app = self.app

        @app.after_request
        def cors(resp):
            # v2.6: yalnızca listedeki kaynaklara; izinli kaynak yansıtılır (Vary: Origin).
            # PC'nin kendi sunduğu sayfa (aynı kaynak) ve APK (CapacitorHttp) CORS'tan etkilenmez.
            origin = request.headers.get("Origin", "").strip().rstrip("/")
            if not origin or not _config("PHONE_ALLOW_CORS", True):
                return resp
            allowed = cors_origins()
            if "*" in allowed:
                resp.headers["Access-Control-Allow-Origin"] = "*"
            elif origin in allowed:
                resp.headers["Access-Control-Allow-Origin"] = origin
                resp.headers.add("Vary", "Origin")
            else:
                return resp
            resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Jarvis-Token"
            resp.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
            resp.headers["Access-Control-Expose-Headers"] = "Retry-After"
            # Chrome "Private Network Access": https sayfadan yerel ağa izin
            resp.headers["Access-Control-Allow-Private-Network"] = "true"
            return resp

        @app.route("/api/<path:_any>", methods=["OPTIONS"])
        def preflight(_any):
            return Response(status=204)

        # --- arayüz (PWA) ---
        @app.route("/")
        def index():
            if self.ui_dir:
                return send_from_directory(self.ui_dir, "index.html")
            return FALLBACK_PAGE

        @app.route("/<path:filename>")
        def static_files(filename):
            if self.ui_dir and (self.ui_dir / filename).is_file():
                return send_from_directory(self.ui_dir, filename)
            return Response("Bulunamadı", status=404)

        # --- API ---
        @app.route("/api/ping")
        def ping():
            # Token gerektirmez: uygulama sunucunun JARVIS olduğunu anlasın.
            return jsonify({"jarvis": True, "version": _version(), "auth": self._authorized()})

        # --- v2.6: eşleşme kodu ---
        @app.route("/api/pair", methods=["POST"])
        def pair():
            ip = self._client()
            wait = self.limiter.retry_after(ip)
            if wait:
                return self._too_many(wait)
            code = (request.get_json(silent=True) or {}).get("code", "")
            if self.pairing.redeem(code):
                self.limiter.success(ip)
                print(f"[TELEFON] Telefon eşleşti: {ip}")
                return jsonify({"token": self.token, "paired": True})
            self.limiter.fail(ip)
            wait = self.limiter.retry_after(ip)
            if wait:
                return self._too_many(wait)
            return jsonify({"error": "Eşleşme kodu geçersiz ya da süresi dolmuş. PC'de TELEFON_ESLESTIR.bat "
                                     "ile yeni kod alın."}), 403

        @app.route("/api/pair/new", methods=["POST"])
        def pair_new():
            # Yeni kod yalnızca PC'nin kendisinden istenebilir (TELEFON_ESLESTIR.bat).
            if self._client() not in LOCAL_ADDRS:
                return jsonify({"error": "Yeni eşleşme kodu yalnızca PC'nin kendisinden istenebilir."}), 403
            code = self.new_pair_code()
            ttl = int(self.pairing.ttl)
            if request.args.get("format") == "text":
                return Response(f"Eşleşme kodu: {format_pair_code(code)}  ({ttl // 60} dakika geçerli, "
                                "tek kullanımlık)\n", mimetype="text/plain; charset=utf-8")
            return jsonify({"code": code, "formatted": format_pair_code(code), "expires_in": ttl})

        @app.route("/api/pair/new", methods=["GET"])
        def pair_new_get():
            return jsonify({"error": "POST kullanın."}), 405

        @app.route("/api/status")
        def status():
            if not self._authorized():
                return self._deny()
            return jsonify(self._status())

        @app.route("/api/ask", methods=["POST"])
        def ask():
            if not self._authorized():
                return self._deny()
            body = request.get_json(silent=True) or {}
            text = str(body.get("text", "")).strip()
            if not text:
                return jsonify({"reply": "Boş mesaj."})
            session = phone_session(body.get("session_id"))
            try:
                reply = self.brain.ask(self._tagged(text), session_id=session, source="phone")
            except Exception as exc:
                reply = f"Hata: {exc}"
            return jsonify({"reply": reply, "session_id": session})

        @app.route("/api/ask_stream", methods=["POST"])
        def ask_stream():
            # v2.3: cevap parça parça (SSE). Olaylar: start, wait, tool, token, done, error.
            if not _config("PHONE_STREAM_ENABLED", True):
                return jsonify({"error": "Akışlı cevap kapalı (PHONE_STREAM_ENABLED)."}), 404
            if not self._authorized():
                return self._deny()
            body = request.get_json(silent=True) or {}
            text = str(body.get("text", "")).strip()
            session = phone_session(body.get("session_id"))

            def generate():
                if not text:
                    yield sse_format({"type": "done", "reply": "Boş mesaj."})
                    return
                yield sse_format({"type": "start", "session_id": session})
                try:
                    for event in self.brain.ask_stream(self._tagged(text), session_id=session, source="phone"):
                        yield sse_format(event)
                except Exception as exc:
                    yield sse_format({"type": "error", "error": f"Hata: {exc}"})

            resp = Response(stream_with_context(generate()), mimetype="text/event-stream")
            resp.headers["Cache-Control"] = "no-cache"
            resp.headers["X-Accel-Buffering"] = "no"
            return resp

        @app.route("/api/tts", methods=["POST"])
        def tts():
            # v2.6: POST. {"text"} -> ses dosyası; {"text", "format": "base64"} -> {"audio", "mime"}.
            if not self._authorized():
                return self._deny()
            body = request.get_json(silent=True) or {}
            text = str(body.get("text", "") or "").strip()[:3000]
            if not text:
                return Response(status=204)
            try:
                audio, mimetype = self._audio(text)
            except Exception as exc:
                print("[TELEFON] TTS hata:", exc)
                return Response(status=204)
            if str(body.get("format", "")).lower() == "base64":
                return jsonify({"audio": base64.b64encode(audio).decode("ascii"), "mime": mimetype})
            return Response(audio, mimetype=mimetype)

        @app.route("/api/tts", methods=["GET"])
        def tts_get():
            # Eski uygulama (GET ?text=&token=) cihaz sesine düşsün diye 404 değil, açıklamalı 405.
            return jsonify({"error": "GET /api/tts kaldırıldı (v2.6). POST {\"text\"} kullanın; "
                                     "telefon uygulamasını v1.9 ya da üstüne güncelleyin."}), 405

        # --- v2.8: bilgi deposu (D:/JARVIS_Bilgi) ---
        @app.route("/api/knowledge", methods=["GET", "POST"])
        def knowledge():
            if not self._authorized():
                return self._deny()
            kb = getattr(self.brain, "knowledge", None)
            if kb is None:
                return jsonify({"error": "Bilgi deposu kapalı (KNOWLEDGE_ENABLED)."}), 503
            if request.method == "GET":
                q = str(request.args.get("q", "") or "").strip()
                if q:
                    return jsonify({"query": q, "results": kb.search(q, limit=8)})
                files = kb.list_files()
                return jsonify({"dir": str(kb.root), "files": files, "count": len(files)})
            body = request.get_json(silent=True) or {}
            try:
                if body.get("data"):
                    try:
                        data = base64.b64decode(str(body["data"]), validate=True)
                    except (ValueError, TypeError):
                        return jsonify({"error": "Dosya verisi bozuk (base64)."}), 400
                    if len(data) > float(_config("KNOWLEDGE_MAX_UPLOAD_MB", 10)) * 1024 * 1024:
                        return jsonify({"error": "Dosya çok büyük."}), 413
                    rel = kb.save_upload(body.get("filename", ""), data)
                else:
                    text = str(body.get("text", "") or "").strip()
                    if not text:
                        return jsonify({"error": "Boş not."}), 400
                    rel = kb.add_note(text, body.get("title", ""), source="telefon")
            except OverflowError:
                return jsonify({"error": "Dosya çok büyük."}), 413
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 400
            print(f"[BİLGİ] Telefondan depoya eklendi: {rel}")
            return jsonify({"saved": True, "path": rel})

        # --- v2.4: hafıza eşitleme (PC'deki SQLite tek doğru kaynak) ---
        @app.route("/api/memories", methods=["GET", "POST", "DELETE"])
        @app.route("/api/memories/<path:memory_id>", methods=["DELETE"])
        def memories(memory_id=None):
            if not self._authorized():
                return self._deny()
            mem = self.brain.memory
            if request.method == "GET":
                items = mem.list_memories()
                return jsonify({"memories": items, "count": len(items)})
            if request.method == "DELETE":
                mid = _int(memory_id if memory_id is not None else request.args.get("id"))
                if mid is None:
                    return jsonify({"error": "Silinecek kaydın id'si gerekli."}), 400
                return jsonify({"deleted": mem.delete_memory(mid), "id": mid})

            body = request.get_json(silent=True) or {}
            if "items" in body:
                items = body.get("items")
                if not isinstance(items, list):
                    return jsonify({"error": "items bir liste olmalı."}), 400
                limit = int(_config("SYNC_MAX_ITEMS", 500))
                if len(items) > limit:
                    return jsonify({"error": f"En fazla {limit} öğe gönderilebilir."}), 413
                results = []
                for item in items:
                    item = item if isinstance(item, dict) else {}
                    cid = item.get("client_id")
                    try:
                        res = mem.add_memory(item.get("content", ""), client_id=cid)
                    except ValueError:
                        res = {"status": "invalid", "id": None}
                    results.append(dict(res, client_id=cid))
                return jsonify({"results": results, "memories": mem.list_memories()})

            content = str(body.get("content", "") or "").strip()
            if "id" in body:
                mid = _int(body.get("id"))
                if mid is None:
                    return jsonify({"error": "Geçersiz id."}), 400
                if not content:
                    return jsonify({"error": "Boş hafıza kaydı."}), 400
                res = mem.update_memory(mid, content)
                if res is None:
                    return jsonify({"error": "Kayıt bulunamadı."}), 404
                return jsonify(res)
            if not content:
                return jsonify({"error": "Boş hafıza kaydı."}), 400
            return jsonify(mem.add_memory(content, client_id=body.get("client_id")))

        @app.route("/api/history", methods=["GET", "POST"])
        def history():
            if not self._authorized():
                return self._deny()
            mem = self.brain.memory
            if request.method == "GET":
                session = phone_session(request.args.get("session"))
                default = int(_config("HISTORY_DEFAULT_LIMIT", 50))
                limit = _int(request.args.get("limit"))
                limit = default if limit is None else limit
                limit = max(1, min(limit, int(_config("HISTORY_MAX_LIMIT", 500))))
                return jsonify({"session": session, "messages": mem.history(session, limit)})
            body = request.get_json(silent=True) or {}
            messages = body.get("messages")
            if not isinstance(messages, list):
                return jsonify({"error": "messages bir liste olmalı."}), 400
            if len(messages) > int(_config("SYNC_MAX_ITEMS", 500)):
                return jsonify({"error": "Çok fazla mesaj; parçalar halinde gönderin."}), 413
            session = phone_session(body.get("session_id"))
            result = mem.import_messages(session, messages, source="phone")
            return jsonify(dict(result, session=session))

        @app.route("/api/tasks")
        def tasks():
            if not self._authorized():
                return self._deny()
            items = self.brain.task_status()[:20]
            slim = [{k: t.get(k) for k in ("id", "title", "status", "progress", "progress_note",
                                            "created_at", "finished_at", "error")} for t in items]
            return jsonify({"tasks": slim})

        @app.route("/api/tasks/<task_id>")
        def task(task_id):
            if not self._authorized():
                return self._deny()
            return jsonify(self.brain.task_status(task_id) or {"error": "Görev bulunamadı."})

        @app.route("/api/system")
        def system():
            if not self._authorized():
                return self._deny()
            return jsonify({"info": self.brain.tools.execute("get_system_info", {})})

        @app.route("/api/helmet/<action>", methods=["GET", "POST"])
        def helmet(action):
            if not self._authorized():
                return self._deny()
            return jsonify({"result": self._helmet(action)})

        # --- v1.3.1 ve öncesiyle uyumluluk (artık token ister) ---
        @app.route("/ask", methods=["POST"])
        def legacy_ask():
            return ask()

        @app.route("/kask/<eylem>")
        def legacy_kask(eylem):
            if not self._authorized():
                return self._deny()
            return self._helmet(eylem)

    def run(self):
        self.app.run(host="0.0.0.0", port=self.port, debug=False,
                     use_reloader=False, threaded=True)


TAILSCALE_NET = ipaddress.ip_network("100.64.0.0/10")   # Tailscale (CGNAT) adres aralığı


def is_tailscale_ip(ip):
    try:
        addr = ipaddress.ip_address(str(ip).strip())
    except ValueError:
        return False
    return addr.version == 4 and addr in TAILSCALE_NET


def tailscale_ip():
    """v2.2: PC'nin Tailscale (100.x) adresi. Sıra: TAILSCALE_IP ayarı -> `tailscale ip -4` -> ağ arayüzleri."""
    manual = str(_config("TAILSCALE_IP", "") or "").strip()
    if manual:
        return manual
    if not _config("TAILSCALE_DETECT", True):
        return ""
    try:
        done = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=3,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        for line in (done.stdout or "").splitlines():
            if is_tailscale_ip(line):
                return line.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if is_tailscale_ip(ip):
                return ip
    except OSError:
        pass
    return ""


def port_in_use(port, host="127.0.0.1"):
    """Port başka bir süreçte (ör. arka planda --headless JARVIS) açık mı?"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        return s.connect_ex((host, int(port))) == 0
    except OSError:
        return False
    finally:
        s.close()


def build_links(lan_ip, ts_ip, port, pair_code):
    """Ev WiFi (LAN) ve Tailscale linkleri; ikisi de pcs= ile tüm adresleri taşır (v2.2).

    v2.6: linkte erişim anahtarı değil, tek kullanımlık eşleşme kodu (?pair=) bulunur.
    """
    bases = [f"http://{ip}:{port}" for ip in (lan_ip, ts_ip) if ip]
    secret = "pair=" + quote(str(pair_code), safe="")
    pcs = "&pcs=" + quote(",".join(bases), safe="") if len(bases) > 1 else ""

    def link(ip):
        return f"http://{ip}:{port}/?{secret}{pcs}" if ip else ""
    return {"bases": bases, "lan": link(lan_ip), "tailscale": link(ts_ip)}


def link_report(links, pair_code):
    """data/phone_link.txt içeriği. Erişim anahtarı bu dosyaya yazılmaz (yalnızca eşleşme kodu)."""
    lines = [
        "JARVIS telefon bağlantısı",
        f"Ev WiFi (LAN) linki : {links.get('lan') or 'bulunamadı'}",
        f"Tailscale linki     : {links.get('tailscale') or 'bulunamadı'}",
        f"Eşleşme kodu        : {format_pair_code(pair_code)}",
        "  (tek kullanımlık, birkaç dakika geçerli; yenisi için TELEFON_ESLESTIR.bat)",
        "Uygulamada PC adresleri (her satıra bir adres):",
    ]
    lines += ["  " + b for b in links.get("bases", [])]
    lines.append("Linki telefonda aç ya da AYAR → Eşleşme kodu kutusuna kodu yazıp PC İLE EŞLEŞ'e bas.")
    return "\n".join(lines) + "\n"


def remote_report(pair_code):
    """v2.8: internetten bağlantı satırları (Tailscale https + GitHub Pages). Ayarlı değilse ''."""
    https = str(_config("TAILSCALE_HTTPS_URL", "") or "").strip().rstrip("/")
    if not https:
        return ""
    lines = ["", f"İnternetten (Tailscale https) PC adresi: {https}"]
    repo = str(_config("GITHUB_REPO", "") or "").strip()
    if "/" in repo:
        user, name = repo.split("/", 1)
        pages = f"https://{user.strip().lower()}.github.io/{name.strip()}/"
        lines.append("Telefonda (GitHub Pages) aç: "
                     f"{pages}?pair={quote(str(pair_code), safe='')}&pcs={quote(https, safe='')}")
    return "\n".join(lines) + "\n"


def write_link_file(text):
    try:
        LINK_FILE.parent.mkdir(parents=True, exist_ok=True)
        LINK_FILE.write_text(text, encoding="utf8")
    except OSError:
        pass


def local_ip():
    """Bilgisayarın aynı ağdaki LAN IP'sini bulmaya çalışır (internete veri göndermez)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def _print_qr(url):
    try:
        import qrcode
        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.make(fit=True)
        qr.print_ascii(invert=True)
    except Exception:
        pass


def start_phone_server(brain, port=5000):
    """Sunucuyu arka planda başlatır. Port doluysa (ör. --headless JARVIS çalışıyor) None döner."""
    if port_in_use(port):
        print(f"[TELEFON] {port} portu kullanımda (arka planda başka bir JARVIS çalışıyor olabilir); "
              "telefon sunucusu başlatılmadı.")
        return None
    server = PhoneServer(brain, port)
    lan, ts = local_ip(), tailscale_ip()
    server.links = {"lan_ip": lan, "ts_ip": ts}
    code = server.pairing.new()
    links = build_links(lan, ts, port, code)
    report = link_report(links, code)
    print("[TELEFON] Ev WiFi linki       :", links["lan"])
    print("[TELEFON] Tailscale linki     :", links["tailscale"] or "bulunamadı (Tailscale kurulu/açık değil)")
    print("[TELEFON] Uygulamada PC adresleri:", ", ".join(links["bases"]))
    print(f"[TELEFON] Eşleşme kodu        : {format_pair_code(code)} "
          f"({int(server.pairing.ttl // 60)} dk geçerli, tek kullanımlık; yenisi: TELEFON_ESLESTIR.bat)")
    print("[TELEFON] Erişim anahtarını elle girmek için: data\\phone_token.txt")
    remote = remote_report(code)
    pages_link = next((ln.split(": ", 1)[1] for ln in remote.splitlines() if ln.startswith("Telefonda (GitHub Pages)")), "")
    if pages_link:
        print("[TELEFON] İnternetten (GitHub Pages):", pages_link)
    write_link_file(report + remote)
    # QR: internet linki varsa o (her yerden çalışır), yoksa Tailscale, yoksa ev WiFi linki
    _print_qr(pages_link or links["tailscale"] or links["lan"])
    threading.Thread(target=server.run, daemon=True).start()
    return server
