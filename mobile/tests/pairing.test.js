// JARVIS mobil v1.9 — eşleşme kodu, link parametreleri, 429 ve app.js'in PC v2.6 uyumu.
// Çalıştır: cd mobile && node --test tests/   (Node 18+, ek paket gerekmez)
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const BK = require("../www/backend.js");

const LAN = "http://192.168.1.20:5000";
const TS = "http://100.101.102.103:5000";

test("normalizePairCode / isPairCode", () => {
  assert.equal(BK.normalizePairCode(" abcd-ef23 "), "ABCDEF23");
  assert.equal(BK.normalizePairCode(null), "");
  assert.equal(BK.isPairCode("ABCD-EFGH"), true);
  assert.equal(BK.isPairCode("abc"), false);
  assert.equal(BK.isPairCode("A".repeat(13)), false);
});

test("readLinkParams: pair/pcs okunur, token yok sayılır, hepsi adresten silinir", () => {
  const r = BK.readLinkParams(`?pair=abcd-efgh&pcs=${encodeURIComponent(LAN + "," + TS)}&view=chat`);
  assert.equal(r.pair, "ABCDEFGH");
  assert.equal(r.pcs, LAN + "," + TS);
  assert.equal(r.legacyToken, false);
  assert.equal(r.rest, "?view=chat");
  const old = BK.readLinkParams("?token=gizli");
  assert.equal(old.legacyToken, true);
  assert.equal(old.pair, "");
  assert.equal(old.rest, "");
  assert.equal("token" in old, false);           // anahtarın kendisi döndürülmez
  assert.deepEqual(BK.readLinkParams(""), { pair: "", pcs: "", legacyToken: false, rest: "" });
});

test("pairWith: başarılı eşleşme anahtarı döndürür, son çalışan adres önce denenir", async () => {
  const calls = [];
  const res = await BK.pairWith([LAN, TS], TS, "abcd-efgh", async (url, code) => {
    calls.push([url, code]);
    return { status: 200, data: { token: "tok", paired: true } };
  });
  assert.deepEqual(res, { url: TS, token: "tok" });
  assert.deepEqual(calls, [[TS, "ABCDEFGH"]]);
});

test("pairWith: ağ hatasında sıradaki adres; PC reddederse durur", async () => {
  const calls = [];
  const res = await BK.pairWith([LAN, TS], "", "ABCDEFGH", async (url) => {
    calls.push(url);
    if (url === LAN) throw new Error("zaman aşımı");
    return { status: 403, data: { error: "Eşleşme kodu geçersiz ya da süresi dolmuş." } };
  });
  assert.equal(res.url, null);
  assert.match(res.why, /geçersiz/);
  assert.deepEqual(calls, [LAN, TS]);

  const calls2 = [];
  const locked = await BK.pairWith([LAN, TS], "", "ABCDEFGH", async (url) => {
    calls2.push(url);
    return { status: 429, data: { error: "Çok fazla hatalı deneme. 120 saniye sonra tekrar deneyin." } };
  });
  assert.match(locked.why, /Çok fazla/);
  assert.deepEqual(calls2, [LAN]);              // aynı PC: ikinci adres denenmez
});

test("pairWith: eski PC (404), geçersiz kod, adres yok", async () => {
  const old = await BK.pairWith([LAN], "", "ABCDEFGH", async () => ({ status: 404, data: {} }));
  assert.match(old.why, /güncelleyin/);
  assert.match((await BK.pairWith([LAN], "", "ab", async () => assert.fail())).why, /8 karakter/);
  assert.equal((await BK.pairWith([], "", "ABCDEFGH", async () => assert.fail())).why, "PC adresi girilmedi");
  const all = await BK.pairWith([LAN], "", "ABCDEFGH", async () => { throw new Error("kapalı"); });
  assert.match(all.why, /ulaşılamadı \(kapalı\)/);
});

test("pingVerdict + findReachable: 429 kilit mesajı gösterilir", async () => {
  assert.deepEqual(BK.pingVerdict(401, null), { ok: false, auth: false, why: "Erişim anahtarı yanlış" });
  const v = BK.pingVerdict(429, { error: "Çok fazla hatalı deneme. 60 saniye sonra tekrar deneyin." });
  assert.equal(v.locked, true);
  assert.match(v.why, /60 saniye/);
  assert.equal(BK.pingVerdict(200, {}), null);
  const r = await BK.findReachable([LAN, TS], "", async (u) => (u === LAN ? v : { ok: false, why: "x" }));
  assert.match(r.why, /60 saniye/);
});

test("app.js: anahtar ve TTS metni URL'ye yazılmaz, /api/tts POST, eşleşme bağlı", () => {
  const www = path.join(__dirname, "..", "www");
  const app = fs.readFileSync(path.join(www, "app.js"), "utf8");
  const html = fs.readFileSync(path.join(www, "index.html"), "utf8");
  assert.equal(/[?&]token=/.test(app), false);
  assert.equal(app.includes('params.get("token")'), false);
  assert.match(app, /"\/api\/tts",\s*\{\s*method:\s*"POST"/);
  assert.match(app, /format:\s*"base64"/);
  assert.match(app, /"\/api\/pair",\s*\{\s*method:\s*"POST"/);
  assert.match(app, /BK\.readLinkParams\(location\.search\)/);
  assert.ok(html.includes('id="pairCode"'));
  assert.ok(html.includes('id="pairBtn"'));
});
