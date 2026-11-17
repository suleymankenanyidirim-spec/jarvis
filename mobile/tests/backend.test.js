// JARVIS mobil v1.5 — www/backend.js birim testleri.
// Çalıştır: cd mobile && node --test tests/   (Node 18+, ek paket gerekmez)
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const BK = require("../www/backend.js");

const LAN = "http://192.168.1.20:5000";
const TS = "http://100.101.102.103:5000";

test("normalizeUrl: şema ve port eklenir, sondaki / atılır", () => {
  assert.equal(BK.normalizeUrl("192.168.1.20"), LAN);
  assert.equal(BK.normalizeUrl(" http://192.168.1.20:5000/ "), LAN);
  assert.equal(BK.normalizeUrl("http://pc.tail1234.ts.net:8080"), "http://pc.tail1234.ts.net:8080");
  assert.equal(BK.normalizeUrl(""), "");
});

test("parseUrlList: satır/virgül ayrımı, sıra korunur, tekrar atılır", () => {
  assert.deepEqual(BK.parseUrlList("192.168.1.20\n100.101.102.103, 192.168.1.20:5000"), [LAN, TS]);
  assert.deepEqual(BK.parseUrlList(["", "  "]), []);
});

test("migrateState: v1.4 pcUrl -> pcUrls", () => {
  const s = BK.migrateState({ pcUrl: "192.168.1.20", pcToken: "x" });
  assert.deepEqual(s.pcUrls, [LAN]);
  assert.equal(s.lastPcUrl, "");
  assert.equal("pcUrl" in s, false);
  assert.equal(s.pcToken, "x");
  assert.deepEqual(BK.migrateState({}).pcUrls, []);
  // listede olmayan "son çalışan" adres unutulur
  assert.equal(BK.migrateState({ pcUrls: [LAN], lastPcUrl: TS }).lastPcUrl, "");
  assert.equal(BK.migrateState({ pcUrls: [LAN, TS], lastPcUrl: TS }).lastPcUrl, TS);
});

test("mergeUrls: PC linkindeki sıra önce, eskiler korunur", () => {
  assert.deepEqual(BK.mergeUrls(`${LAN},${TS}`, ["http://10.0.0.5:5000", TS]),
    [LAN, TS, "http://10.0.0.5:5000"]);
});

test("tryOrder: son çalışan adres önce denenir", () => {
  assert.deepEqual(BK.tryOrder([LAN, TS], ""), [LAN, TS]);
  assert.deepEqual(BK.tryOrder([LAN, TS], TS), [TS, LAN]);
  assert.deepEqual(BK.tryOrder([LAN, TS], "http://1.2.3.4:5000"), [LAN, TS]);
});

test("findReachable: sırayla dener, ilk çalışanı döndürür", async () => {
  const calls = [];
  const ping = async (u) => { calls.push(u); return u === TS ? { ok: true, data: { version: "2.2.0" } } : { ok: false, why: "yok" }; };
  const r = await BK.findReachable([LAN, TS], "", ping);
  assert.equal(r.url, TS);
  assert.deepEqual(calls, [LAN, TS]);
  assert.equal(r.tried.length, 2);
});

test("findReachable: hatırlanan çalışıyorsa diğerleri denenmez", async () => {
  const calls = [];
  const r = await BK.findReachable([LAN, TS], TS, async (u) => { calls.push(u); return { ok: true, data: {} }; });
  assert.equal(r.url, TS);
  assert.deepEqual(calls, [TS]);
});

test("findReachable: hiçbiri yoksa sebep döner; ping hatası yutulur", async () => {
  const r = await BK.findReachable([LAN, TS], "", async () => { throw new Error("ağ"); });
  assert.equal(r.url, null);
  assert.match(r.why, /Hiçbir PC adresine ulaşılamadı/);
  const a = await BK.findReachable([LAN], "", async () => ({ ok: false, auth: false, why: "401" }));
  assert.equal(a.why, "Erişim anahtarı yanlış");
  const e = await BK.findReachable([], "", async () => ({ ok: true }));
  assert.equal(e.why, "PC adresi girilmedi");
});

test("decide: bulut yalnızca hiçbir PC adresine ulaşılamazsa (sınırlı mod)", () => {
  assert.deepEqual(BK.decide({ mode: "auto", pcReachable: true, hasCloud: true, hasPcUrls: true }), { backend: "pc", limited: false });
  assert.deepEqual(BK.decide({ mode: "auto", pcReachable: false, hasCloud: true, hasPcUrls: true }), { backend: "cloud", limited: true });
  assert.deepEqual(BK.decide({ mode: "auto", pcReachable: false, hasCloud: false, hasPcUrls: true }), { backend: "none", limited: true });
  // PC adresi hiç girilmemişse "ulaşılamıyor" demeyiz
  assert.deepEqual(BK.decide({ mode: "auto", pcReachable: false, hasCloud: true, hasPcUrls: false }), { backend: "cloud", limited: false });
  assert.deepEqual(BK.decide({ mode: "pc", pcReachable: false, hasCloud: true, hasPcUrls: true }), { backend: "none", limited: true });
  // Elle seçilen bulut modu eskisi gibi çalışır
  assert.deepEqual(BK.decide({ mode: "cloud", pcReachable: false, hasCloud: true, hasPcUrls: true }), { backend: "cloud", limited: false });
});

test("limitedText: ekranda açıkça 'PC'ye ulaşılamıyor, sınırlı mod' yazar", () => {
  assert.ok(BK.limitedText("cloud", "gemini").startsWith("PC'ye ulaşılamıyor, sınırlı mod"));
  assert.match(BK.limitedText("cloud", "gemini"), /GEMINI/);
  assert.ok(BK.limitedText("none").startsWith("PC'ye ulaşılamıyor, sınırlı mod"));
});

test("isTailscaleUrl / linkLabel", () => {
  assert.equal(BK.isTailscaleUrl(TS), true);
  assert.equal(BK.isTailscaleUrl("http://100.63.0.1:5000"), false);
  assert.equal(BK.isTailscaleUrl("http://100.128.0.1:5000"), false);
  assert.equal(BK.isTailscaleUrl("http://pc.tail1234.ts.net:5000"), true);
  assert.equal(BK.isTailscaleUrl(LAN), false);
  assert.equal(BK.linkLabel(TS), "PC BAĞLI · TAILSCALE");
  assert.equal(BK.linkLabel(LAN), "PC BAĞLI · EV");
});
