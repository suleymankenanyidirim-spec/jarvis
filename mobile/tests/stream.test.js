// v1.6: akışlı cevap yardımcıları — SSE ayrıştırma, cümle bölme, oturum kimliği.
const test = require("node:test");
const assert = require("node:assert/strict");
const ST = require("../www/stream.js");

function collect() {
  const events = [];
  const p = ST.createSseParser((e) => events.push(e));
  return { events, p };
}

test("SSE: parça parça gelen olaylar doğru birleşir", () => {
  const { events, p } = collect();
  const body = 'event: start\ndata: {"type":"start","session_id":"phone-x"}\n\n' +
    'event: tool\ndata: {"type":"tool","name":"web_search","text":"araç çalışıyor: web_search"}\n\n' +
    'event: token\ndata: {"type":"token","text":"Merhaba "}\n\n' +
    'event: done\ndata: {"type":"done","reply":"Merhaba efendim."}\n\n';
  for (let i = 0; i < body.length; i += 7) p.push(body.slice(i, i + 7));   // rastgele bölünmüş ağ parçaları
  p.end();
  assert.deepEqual(events.map((e) => e.type), ["start", "tool", "token", "done"]);
  assert.equal(events[1].text, "araç çalışıyor: web_search");
  assert.equal(events[3].reply, "Merhaba efendim.");
});

test("SSE: Türkçe karakterler, CRLF, yorum satırı ve eksik type", () => {
  const { events, p } = collect();
  p.push(': ping\r\n\r\nevent: token\r\ndata: {"text":"çğış"}\r\n\r\n');
  p.push('data: {"type":"done","reply":"ok"}');
  p.end();                                  // son blok boş satırla bitmese de işlenir
  assert.deepEqual(events, [{ type: "token", text: "çğış" }, { type: "done", reply: "ok" }]);
});

test("SSE: JSON olmayan veri metin olarak gelir", () => {
  const { events, p } = collect();
  p.push("event: wait\ndata: sırada\n\n");
  assert.deepEqual(events, [{ type: "wait", text: "sırada" }]);
});

test("Cümle bölücü: cümle tamamlandıkça verir", () => {
  const s = ST.createSentenceSplitter();
  const out = [];
  for (const piece of ["Merhaba efen", "dim, nasılsınız? Bugün hava", " güneşli görünüyor. Ya", "rın yağmur var"]) {
    out.push(...s.push(piece));
  }
  assert.deepEqual(out, ["Merhaba efendim, nasılsınız?", "Bugün hava güneşli görünüyor."]);
  assert.deepEqual(s.flush(), ["Yarın yağmur var"]);
  assert.deepEqual(s.flush(), []);
});

test("Cümle bölücü: kısa cümleler birleşir, ondalık sayı bölünmez", () => {
  const s = ST.createSentenceSplitter({ minChars: 12 });
  assert.deepEqual(s.push("Tamam. Sıcaklık 3.5 derece oldu. "), ["Tamam. Sıcaklık 3.5 derece oldu."]);
});

test("Cümle bölücü: satır sonu ve üç nokta", () => {
  const s = ST.createSentenceSplitter({ minChars: 3 });
  assert.deepEqual(s.push("Birinci madde\nİkinci madde… Son"), ["Birinci madde", "İkinci madde…"]);
  assert.deepEqual(s.flush(), ["Son"]);
});

test("Cümle bölücü: kapanmamış kod bloğu içinden cümle çıkmaz", () => {
  const s = ST.createSentenceSplitter({ minChars: 3 });
  assert.deepEqual(s.push("```\nprint('a. b')\n"), []);
  assert.deepEqual(s.push("```\nBitti efendim. "), ["```\nprint('a. b')\n```", "Bitti efendim."]);
});

test("Cümle bölücü: reset ara metni atar", () => {
  const s = ST.createSentenceSplitter();
  s.push("Araçtan önceki ara metin");
  s.reset();
  assert.equal(s.pending(), "");
  assert.deepEqual(s.flush(), []);
});

test("Oturum kimliği: biçim ve doğrulama", () => {
  const id = ST.newSessionId();
  assert.match(id, /^phone-[a-z0-9]{10}$/);
  assert.ok(ST.isValidSessionId(id));
  assert.notEqual(ST.newSessionId(), ST.newSessionId());
  assert.equal(ST.newSessionId(() => 0), "phone-aaaaaaaaaa");
  assert.ok(!ST.isValidSessionId(""));
  assert.ok(!ST.isValidSessionId("task-abc"));
  assert.ok(!ST.isValidSessionId("boşluk var"));
  assert.ok(!ST.isValidSessionId("a".repeat(65)));
  assert.ok(!ST.isValidSessionId(undefined));
});
