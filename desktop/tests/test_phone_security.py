"""v2.6: telefon sunucusu güvenliği — yalnızca başlıkta anahtar, eşleşme kodu,
IP başına deneme sınırı, CORS kaynak listesi, POST /api/tts."""
import base64
import unittest
from unittest import mock

try:
    import flask  # noqa: F401
    HAS_FLASK = True
except ImportError:          # CI'deki test işi bağımlılık kurmaz
    HAS_FLASK = False

if HAS_FLASK:
    from core import phone_server as ps


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


class Brain:
    def ask(self, text, session_id=None, source=None, allow_planning=True):
        return "cevap"


@unittest.skipUnless(HAS_FLASK, "flask kurulu değil")
class ServerTestBase(unittest.TestCase):
    H = {"X-Jarvis-Token": "tok"}

    def setUp(self):
        self.values = {"PHONE_ALLOW_CORS": True, "PHONE_AUTH_MAX_FAILURES": 3,
                       "PHONE_AUTH_WINDOW": 60, "PHONE_AUTH_LOCKOUT": 120}
        for p in (mock.patch.object(ps, "load_token", return_value="tok"),
                  mock.patch.object(ps, "_config", side_effect=lambda n, d=None: self.values.get(n, d))):
            p.start()
            self.addCleanup(p.stop)
        self.server = ps.PhoneServer(Brain())
        self.clock = Clock()
        self.server.limiter.clock = self.clock
        self.server.pairing.clock = self.clock
        self.c = self.server.app.test_client()

    def remote(self, ip):
        return {"REMOTE_ADDR": ip}


class HeaderOnlyTokenTest(ServerTestBase):
    def test_header_accepted(self):
        self.assertEqual(self.c.get("/api/status", headers=self.H).status_code, 200)
        self.assertTrue(self.c.get("/api/ping", headers=self.H).get_json()["auth"])

    def test_query_token_rejected(self):
        self.assertEqual(self.c.get("/api/status?token=tok").status_code, 401)
        self.assertFalse(self.c.get("/api/ping?token=tok").get_json()["auth"])

    def test_body_token_rejected(self):
        r = self.c.post("/api/ask", json={"text": "selam", "token": "tok"})
        self.assertEqual(r.status_code, 401)

    def test_wrong_token(self):
        self.assertEqual(self.c.get("/api/status", headers={"X-Jarvis-Token": "yanlis"}).status_code, 401)


class RateLimitTest(ServerTestBase):
    BAD = {"X-Jarvis-Token": "yanlis"}

    def test_lockout_after_failures(self):
        env = self.remote("192.168.1.50")
        for _ in range(2):
            self.assertEqual(self.c.get("/api/status", headers=self.BAD, environ_base=env).status_code, 401)
        r = self.c.get("/api/status", headers=self.BAD, environ_base=env)
        self.assertEqual(r.status_code, 429)
        self.assertEqual(r.headers["Retry-After"], "120")
        self.assertIn("Çok fazla hatalı deneme", r.get_json()["error"])
        # Kilitliyken doğru anahtar da geçmez (kaba kuvvet devam edemesin)
        self.assertEqual(self.c.get("/api/status", headers=self.H, environ_base=env).status_code, 429)
        # Başka IP etkilenmez
        self.assertEqual(self.c.get("/api/status", headers=self.H,
                                    environ_base=self.remote("192.168.1.51")).status_code, 200)
        # Süre dolunca açılır
        self.clock.t += 121
        self.assertEqual(self.c.get("/api/status", headers=self.H, environ_base=env).status_code, 200)

    def test_missing_token_does_not_count(self):
        env = self.remote("192.168.1.60")
        for _ in range(10):
            self.assertEqual(self.c.get("/api/status", environ_base=env).status_code, 401)
            self.c.get("/api/ping", environ_base=env)
        self.assertEqual(self.c.get("/api/status", headers=self.H, environ_base=env).status_code, 200)

    def test_window_expires_and_success_resets(self):
        env = self.remote("192.168.1.70")
        self.c.get("/api/status", headers=self.BAD, environ_base=env)
        self.c.get("/api/status", headers=self.BAD, environ_base=env)
        self.c.get("/api/status", headers=self.H, environ_base=env)        # başarı sayacı sıfırlar
        self.c.get("/api/status", headers=self.BAD, environ_base=env)
        self.c.get("/api/status", headers=self.BAD, environ_base=env)
        self.assertEqual(self.c.get("/api/status", headers=self.H, environ_base=env).status_code, 200)
        self.c.get("/api/status", headers=self.BAD, environ_base=env)
        self.clock.t += 61                                                   # pencere doldu
        self.c.get("/api/status", headers=self.BAD, environ_base=env)
        self.assertEqual(self.c.get("/api/status", headers=self.H, environ_base=env).status_code, 200)

    def test_limiter_unit(self):
        clock = Clock()
        lim = ps.FailureLimiter(max_failures=2, window=10, lockout=30, clock=clock)
        self.assertEqual(lim.retry_after("a"), 0)
        lim.fail("a")
        lim.fail("a")
        self.assertEqual(lim.retry_after("a"), 30)
        clock.t += 29.5
        self.assertEqual(lim.retry_after("a"), 1)
        clock.t += 1
        self.assertEqual(lim.retry_after("a"), 0)


class PairingTest(ServerTestBase):
    def test_code_redeemed_once(self):
        code = self.server.pairing.new()
        env = self.remote("192.168.1.80")
        with mock.patch("builtins.print"):
            r = self.c.post("/api/pair", json={"code": ps.format_pair_code(code).lower()}, environ_base=env)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json(), {"token": "tok", "paired": True})
        r = self.c.post("/api/pair", json={"code": code}, environ_base=self.remote("192.168.1.81"))
        self.assertEqual(r.status_code, 403)
        self.assertIn("Eşleşme kodu geçersiz", r.get_json()["error"])

    def test_code_expires(self):
        code = self.server.pairing.new()
        self.clock.t += 301
        self.assertEqual(self.c.post("/api/pair", json={"code": code}).status_code, 403)
        self.assertIsNone(self.server.pairing.current())

    def test_wrong_codes_rate_limited(self):
        code = self.server.pairing.new()
        env = self.remote("192.168.1.90")
        self.assertEqual(self.c.post("/api/pair", json={"code": "AAAA-AAAA"}, environ_base=env).status_code, 403)
        self.assertEqual(self.c.post("/api/pair", json={"code": "BBBB"}, environ_base=env).status_code, 403)
        self.assertEqual(self.c.post("/api/pair", json={}, environ_base=env).status_code, 429)
        self.assertEqual(self.c.post("/api/pair", json={"code": code}, environ_base=env).status_code, 429)

    def test_code_burned_after_many_failures(self):
        clock = Clock()
        codes = ps.PairingCodes(ttl=300, max_failures=3, clock=clock)
        code = codes.new()
        for _ in range(3):
            self.assertFalse(codes.redeem("YANLIS00"))
        self.assertFalse(codes.redeem(code))          # kod iptal oldu
        self.assertIsNone(codes.current())

    def test_code_format(self):
        codes = ps.PairingCodes(length=8)
        code = codes.new()
        self.assertEqual(len(code), 8)
        self.assertTrue(set(code) <= set(ps.PAIR_ALPHABET))
        self.assertEqual(ps.normalize_pair_code(" ab-cd 12 "), "ABCD12")
        self.assertEqual(ps.format_pair_code("abcdefgh"), "ABCD-EFGH")

    def test_new_code_only_from_localhost(self):
        r = self.c.post("/api/pair/new", environ_base=self.remote("192.168.1.20"))
        self.assertEqual(r.status_code, 403)
        r = self.c.post("/api/pair/new", environ_base=self.remote("127.0.0.1"))
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertEqual(d["code"], self.server.pairing.current())
        self.assertEqual(d["expires_in"], 300)
        r = self.c.post("/api/pair/new?format=text", environ_base=self.remote("::1"))
        self.assertIn("Eşleşme kodu:", r.get_data(as_text=True))
        self.assertNotIn("tok", r.get_data(as_text=True))
        self.assertEqual(self.c.get("/api/pair/new").status_code, 405)

    def test_new_code_rewrites_link_file(self):
        self.server.links = {"lan_ip": "192.168.1.20", "ts_ip": ""}
        with mock.patch.object(ps, "write_link_file") as write, mock.patch("builtins.print"):
            code = self.server.new_pair_code()
        report = write.call_args.args[0]
        self.assertIn(f"?pair={code}", report)
        self.assertNotIn("tok", report)


class CorsTest(ServerTestBase):
    def origin(self, origin):
        return self.c.get("/api/ping", headers={"Origin": origin})

    def test_allowed_origin_reflected(self):
        r = self.origin("https://localhost")
        self.assertEqual(r.headers["Access-Control-Allow-Origin"], "https://localhost")
        self.assertIn("Origin", r.headers.get("Vary", ""))
        self.assertIn("X-Jarvis-Token", r.headers["Access-Control-Allow-Headers"])

    def test_unknown_origin_gets_no_cors(self):
        r = self.origin("https://kotu-site.example")
        self.assertNotIn("Access-Control-Allow-Origin", r.headers)
        r = self.c.open("/api/ask", method="OPTIONS", headers={"Origin": "https://kotu-site.example"})
        self.assertNotIn("Access-Control-Allow-Origin", r.headers)

    def test_no_origin_no_cors(self):
        self.assertNotIn("Access-Control-Allow-Origin", self.c.get("/api/ping").headers)

    def test_config_list_and_github_pages(self):
        self.values.update(PHONE_CORS_ORIGINS=["https://ornek.net/"], GITHUB_REPO="Kullanici/jarvis")
        self.assertEqual(ps.cors_origins(), ["https://ornek.net", "https://kullanici.github.io"])
        self.assertEqual(self.origin("https://kullanici.github.io").headers["Access-Control-Allow-Origin"],
                         "https://kullanici.github.io")
        self.assertNotIn("Access-Control-Allow-Origin", self.origin("https://localhost").headers)
        self.values["PHONE_CORS_GITHUB_PAGES"] = False
        self.assertEqual(ps.cors_origins(), ["https://ornek.net"])

    def test_string_setting_and_wildcard(self):
        self.values["PHONE_CORS_ORIGINS"] = "https://a.net, https://b.net"
        self.assertEqual(ps.cors_origins(), ["https://a.net", "https://b.net"])
        self.values["PHONE_CORS_ORIGINS"] = ["*"]
        self.assertEqual(self.origin("https://herhangi.net").headers["Access-Control-Allow-Origin"], "*")

    def test_cors_disabled(self):
        self.values["PHONE_ALLOW_CORS"] = False
        self.assertNotIn("Access-Control-Allow-Origin", self.origin("https://localhost").headers)


class TtsTest(ServerTestBase):
    def test_get_not_allowed(self):
        self.assertEqual(self.c.get("/api/tts?text=selam", headers=self.H).status_code, 405)

    def test_requires_token(self):
        self.assertEqual(self.c.post("/api/tts", json={"text": "selam"}).status_code, 401)

    def test_audio_and_base64(self):
        async def fake(text):
            return b"MP3:" + text.encode("utf8")
        with mock.patch.object(self.server, "_synthesize", side_effect=fake):
            r = self.c.post("/api/tts", json={"text": "merhaba"}, headers=self.H)
            self.assertEqual(r.mimetype, "audio/mpeg")
            self.assertEqual(r.data, b"MP3:merhaba")
            r = self.c.post("/api/tts", json={"text": "çğış", "format": "base64"}, headers=self.H)
            d = r.get_json()
            self.assertEqual(d["mime"], "audio/mpeg")
            self.assertEqual(base64.b64decode(d["audio"]), "MP3:çğış".encode("utf8"))

    def test_empty_text_and_error(self):
        self.assertEqual(self.c.post("/api/tts", json={"text": " "}, headers=self.H).status_code, 204)

        async def boom(text):
            raise RuntimeError("edge-tts yok")
        with mock.patch.object(self.server, "_synthesize", side_effect=boom), mock.patch("builtins.print"):
            self.assertEqual(self.c.post("/api/tts", json={"text": "a"}, headers=self.H).status_code, 204)


if __name__ == "__main__":
    unittest.main()
