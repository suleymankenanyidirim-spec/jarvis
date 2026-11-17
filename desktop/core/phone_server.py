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
import secrets
import socket
import threading
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory

ROOT = Path(__file__).resolve().parents[1]
TOKEN_FILE = ROOT / "data" / "phone_token.txt"


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
        self.app = Flask("jarvis_phone")
        self._routes()

    # ------------------------------------------------------------- helpers
    def _authorized(self):
        given = (request.headers.get("X-Jarvis-Token")
                 or request.args.get("token")
                 or (request.get_json(silent=True) or {}).get("token")
                 or "")
        return hmac.compare_digest(str(given).strip(), self.token)

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
                resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
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
            text = str((request.get_json(silent=True) or {}).get("text", "")).strip()
            if not text:
                return jsonify({"reply": "Boş mesaj."})
            try:
                reply = self.brain.ask("[Telefondan] " + text if _config("PHONE_TAG_MESSAGES", False) else text)
            except Exception as exc:
                reply = f"Hata: {exc}"
            return jsonify({"reply": reply})

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
    server = PhoneServer(brain, port)
    ip = local_ip()
    link = f"http://{ip}:{port}/?token={server.token}"
    print("[TELEFON] Aynı WiFi'deki telefondan aç:", link)
    print(f"[TELEFON] Uygulamada PC adresi: http://{ip}:{port}   Anahtar: {server.token}")
    try:
        (ROOT / "data" / "phone_link.txt").write_text(link + "\n", encoding="utf8")
    except OSError:
        pass
    _print_qr(link)
    threading.Thread(target=server.run, daemon=True).start()
    return server
