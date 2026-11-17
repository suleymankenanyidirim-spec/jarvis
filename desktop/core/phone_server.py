"""
Telefon Bağlantısı (v1.4)
=========================
Aynı WiFi ağındaki telefondan JARVIS'e bağlanmak için küçük bir web sunucusu.

- http://<PC_IP>:5000/?token=XXXX  -> telefon arayüzünü (PWA) doğrudan açar
- /api/*                            -> telefon uygulamasının (APK / GitHub Pages) kullandığı JSON API

Güvenlik: v1.3.1'de aynı ağdaki herkes JARVIS'e komut verebiliyordu.
v1.4'te tüm API çağrıları bir erişim anahtarı (token) ister. Anahtar
config.PHONE_TOKEN'dan alınır; boşsa data/phone_token.txt içinde üretilir.
"""

import hmac
import ipaddress
import json
import secrets
import socket
import subprocess
import threading
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
        self.app = Flask("jarvis_phone")
        self._routes()

    # ------------------------------------------------------------- helpers
    def _authorized(self):
        given = (request.headers.get("X-Jarvis-Token")
                 or request.args.get("token")
                 or (request.get_json(silent=True) or {}).get("token")
                 or "")
        return hmac.compare_digest(str(given).strip(), self.token)

    @staticmethod
    def _tagged(text):
        return "[Telefondan] " + text if _config("PHONE_TAG_MESSAGES", False) else text

    def _deny(self):
        return jsonify({"error": "Yetkisiz. Telefon uygulamasına PC'deki erişim anahtarını gir."}), 401

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
            if _config("PHONE_ALLOW_CORS", True):
                resp.headers["Access-Control-Allow-Origin"] = "*"
                resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Jarvis-Token"
                resp.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
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

        @app.route("/api/tts")
        def tts():
            if not self._authorized():
                return self._deny()
            text = request.args.get("text", "").strip()[:3000]
            if not text:
                return Response(status=204)
            try:
                audio, mimetype = self._synthesize(text)
                return Response(audio, mimetype=mimetype)
            except Exception as exc:
                print("[TELEFON] TTS hata:", exc)
                return Response(status=204)

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


def build_links(lan_ip, ts_ip, port, token):
    """v2.2: ev WiFi (LAN) ve Tailscale linkleri; ikisi de pcs= ile tüm adresleri taşır."""
    bases = [f"http://{ip}:{port}" for ip in (lan_ip, ts_ip) if ip]
    secret = "token=" + quote(str(token), safe="")
    pcs = "&pcs=" + quote(",".join(bases), safe="") if len(bases) > 1 else ""

    def link(ip):
        return f"http://{ip}:{port}/?{secret}{pcs}" if ip else ""
    return {"bases": bases, "lan": link(lan_ip), "tailscale": link(ts_ip)}


def link_report(links, token):
    lines = [
        "JARVIS telefon bağlantısı",
        f"Ev WiFi (LAN) linki : {links.get('lan') or 'bulunamadı'}",
        f"Tailscale linki     : {links.get('tailscale') or 'bulunamadı'}",
        "Uygulamada PC adresleri (her satıra bir adres):",
    ]
    lines += ["  " + b for b in links.get("bases", [])]
    lines.append(f"Erişim anahtarı     : {token}")
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
    links = build_links(lan, ts, port, server.token)
    server.links = {"lan_ip": lan, "ts_ip": ts}
    report = link_report(links, server.token)
    print("[TELEFON] Ev WiFi linki       :", links["lan"])
    print("[TELEFON] Tailscale linki     :", links["tailscale"] or "bulunamadı (Tailscale kurulu/açık değil)")
    print("[TELEFON] Uygulamada PC adresleri:", ", ".join(links["bases"]))
    write_link_file(report)
    _print_qr(links["tailscale"] or links["lan"])
    threading.Thread(target=server.run, daemon=True).start()
    return server
