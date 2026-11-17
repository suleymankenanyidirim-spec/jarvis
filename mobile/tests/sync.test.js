// v1.7: hafıza / mesaj eşitleme — bekleyenler, PC önbelleği, tekrar engeli.
const test = require("node:test");
const assert = require("node:assert/strict");
const SY = require("../www/sync.js");

let n = 0;
const ids = () => "c-" + (++n);
const fresh = (extra) => SY.migrateSyncState(Object.assign({}, extra || {}), ids);

/** PC'nin /api/memories ve /api/history davranışını taklit eden sahte API (client_id ve içerik tekrarı yok). */
function fakePc(initial) {
  const pc = { mems: (initial || []).map((c, i) => ({ id: i + 1, content: c, active: true })), msgs: [], seen: new Set(), calls: [] };
  pc.api = {
    async postMemories(items) {
      pc.calls.push(["post", items.length]);
      return { results: items.map((it) => {
        if (pc.seen.has(it.client_id)) return { client_id: it.client_id, status: "synced" };
        pc.seen.add(it.client_id);
        const hit = pc.mems.find((m) => m.active && SY.normalize(m.content) === SY.normalize(it.content));
        if (hit) return { client_id: it.client_id, status: "exists", id: hit.id };
        const m = { id: pc.mems.length + 1, content: it.content, active: true };
        pc.mems.push(m);
        return { client_id: it.client_id, status: "added", id: m.id };
      }) };
    },
    async deleteMemory(id) { pc.calls.push(["del", id]); const m = pc.mems.find((x) => x.id === id); if (m) m.active = false; return { deleted: !!m }; },
    async postHistory(messages) {
      pc.calls.push(["hist", messages.length]);
      let imported = 0, skipped = 0;
      for (const m of messages) { if (pc.seen.has(m.client_id)) skipped++; else { pc.seen.add(m.client_id); pc.msgs.push(m); imported++; } }
      return { imported, skipped };
    },
    async getMemories() { pc.calls.push(["get"]); return { memories: pc.mems.filter((m) => m.active).reverse().map(({ id, content }) => ({ id, content })) }; },
  };
  return pc;
}

test("migrate: v1.6 telefon hafızası bekleyen eklemelere taşınır", () => {
  const s = fresh({ memories: [{ text: "Adım Sky", t: 1 }, { text: "adım sky." }, { text: "Renk mavi" }] });
  assert.equal(s.memories, undefined);
  assert.deepEqual(s.pending.memories.map((o) => [o.op, o.text]), [["add", "Adım Sky"], ["add", "Renk mavi"]]);
  assert.deepEqual(s.pending.messages, []);
  assert.deepEqual(s.pcMemories, []);
  assert.equal(SY.migrateSyncState(s, ids), s);   // ikinci kez zararsız
  assert.equal(s.pending.memories.length, 2);
});

test("normalize PC ile aynı: boşluk, büyük harf, ı/i, sondaki nokta", () => {
  assert.equal(SY.normalize("  Kask   IP 192.168.1.42. "), SY.normalize("kask ip 192.168.1.42"));
  assert.equal(SY.normalize("KULLANICININ adı"), "kullanicinin adi");
});

test("queueMemory: aynı içerik iki kez beklemez, PC önbelleğindekiyle de çakışmaz", () => {
  const s = fresh({ pcMemories: [{ id: 7, content: "Doğum günüm 3 Mayıs" }] });
  assert.equal(SY.queueMemory(s, "Renk mavi", ids), "queued");
  assert.equal(SY.queueMemory(s, "renk  MAVİ.", ids), "exists");
  assert.equal(SY.queueMemory(s, "doğum günüm 3 mayıs", ids), "exists");
  assert.equal(SY.queueMemory(s, "   ", ids), "empty");
  assert.equal(s.pending.memories.length, 1);
});

test("effectiveMemories: bekleyenler önce, silinmesi bekleyen PC kaydı gizli", () => {
  const s = fresh({ pcMemories: [{ id: 2, content: "B" }, { id: 1, content: "A" }] });
  SY.queueMemory(s, "Yeni 1", ids); SY.queueMemory(s, "Yeni 2", ids);
  assert.ok(SY.removeEffective(s, "pc:1", ids));
  const eff = SY.effectiveMemories(s);
  assert.deepEqual(eff.map((m) => [m.text, m.pending]), [["Yeni 2", true], ["Yeni 1", true], ["B", false]]);
  assert.equal(SY.promptMemories(s, 2), "- Yeni 2\n- Yeni 1");
  assert.equal(SY.promptMemories(fresh(), 5), "- Yok");
});

test("silinmesi bekleyen kayıt yeniden eklenirse silme iptal olur", () => {
  const s = fresh({ pcMemories: [{ id: 1, content: "Kedi Pamuk" }] });
  SY.removeEffective(s, "pc:1", ids);
  assert.equal(SY.queueMemory(s, "kedi pamuk", ids), "restored");
  assert.deepEqual(s.pending.memories, []);
});

test("queueForget: bekleyen eklemeyi atar, PC kaydı için silme bekletir", () => {
  const s = fresh({ pcMemories: [{ id: 3, content: "Geçici PC notu" }, { id: 4, content: "Kalıcı" }] });
  SY.queueMemory(s, "Geçici telefon notu", ids);
  assert.equal(SY.queueForget(s, "GEÇİCİ", ids), 2);
  assert.deepEqual(s.pending.memories.map((o) => [o.op, o.id]), [["del", 3]]);
  assert.deepEqual(SY.effectiveMemories(s).map((m) => m.text), ["Kalıcı"]);
  assert.equal(SY.queueForget(s, "yok", ids), 0);
});

test("editEffective: bekleyen yerinde değişir, PC kaydı sil+ekle olur", () => {
  const s = fresh({ pcMemories: [{ id: 5, content: "Renk mavi" }] });
  SY.queueMemory(s, "Şehir İzmir", ids);
  const pendKey = s.pending.memories[0].client_id;
  assert.equal(SY.editEffective(s, pendKey, "Şehir İstanbul", ids), "edited");
  assert.equal(SY.editEffective(s, "pc:5", "Renk yeşil", ids), "edited");
  assert.deepEqual(s.pending.memories.map((o) => [o.op, o.text]),
    [["add", "Şehir İstanbul"], ["del", "Renk mavi"], ["add", "Renk yeşil"]]);
  assert.equal(SY.editEffective(s, "pc:5", "x", ids), "missing");      // artık gizli
  assert.equal(SY.editEffective(s, pendKey, "renk yeşil", ids), "merged");
});

test("syncPending: hafızalar ve mesajlar PC'ye gider, önbellek tazelenir", async () => {
  const pc = fakePc(["PC'de zaten var", "Silinecek"]);
  const s = fresh({ pcMemories: [{ id: 2, content: "Silinecek" }, { id: 1, content: "PC'de zaten var" }] });
  SY.queueMemory(s, "Bulutta eklendi", ids);
  s.pending.memories.push({ op: "add", client_id: ids(), text: "pc'de ZATEN var" });   // eski sürümden kalmış olabilir
  SY.removeEffective(s, "pc:2", ids);
  SY.queueMessage(s, "user", "bulut sorusu", ids, 1000);
  SY.queueMessage(s, "assistant", "bulut cevabı", ids, 1001);
  assert.equal(SY.queueMessage(s, "system", "x", ids), false);
  let saves = 0;
  const r = await SY.syncPending(s, pc.api, () => saves++, 5000);
  assert.deepEqual(r, { added: 1, existed: 1, deleted: 1, messages: 2, skipped: 0 });
  assert.equal(SY.hasPending(s), false);
  assert.deepEqual(s.pcMemories.map((m) => m.content), ["Bulutta eklendi", "PC'de zaten var"]);
  assert.equal(s.pcMemoriesAt, 5000);
  assert.deepEqual(pc.msgs.map((m) => [m.role, m.text, m.t]), [["user", "bulut sorusu", 1000], ["assistant", "bulut cevabı", 1001]]);
  assert.ok(saves >= 3);
  assert.equal(SY.syncSummary(r), "PC ile eşitlendi: 1 hafıza eklendi, 1 hafıza zaten vardı, 1 hafıza silindi, 2 mesaj aktarıldı.");
  assert.equal(SY.syncSummary({ added: 0, existed: 0, deleted: 0, messages: 0 }), "");
});

test("syncPending: düzenleme sırası korunur (önce sil, sonra ekle)", async () => {
  const pc = fakePc(["Renk mavi"]);
  const s = fresh({ pcMemories: [{ id: 1, content: "Renk mavi" }] });
  SY.editEffective(s, "pc:1", "Renk yeşil", ids);
  await SY.syncPending(s, pc.api);
  assert.deepEqual(pc.calls.slice(0, 2), [["del", 1], ["post", 1]]);
  assert.deepEqual(s.pcMemories.map((m) => m.content), ["Renk yeşil"]);
});

test("syncPending: ağ hatasında gönderilmeyenler bekleyende kalır, tekrar denemede iki kez eklenmez", async () => {
  const pc = fakePc([]);
  const s = fresh();
  SY.queueMemory(s, "Bir", ids);
  SY.queueMessage(s, "user", "mesaj", ids);
  const realHist = pc.api.postHistory;
  pc.api.postHistory = async (m) => { await realHist(m); throw new Error("zaman aşımı"); };  // PC aldı ama cevap kayboldu
  await assert.rejects(SY.syncPending(s, pc.api), /zaman aşımı/);
  assert.equal(s.pending.memories.length, 0);     // hafıza adımı tamamlanmıştı
  assert.equal(s.pending.messages.length, 1);     // mesaj bekliyor
  pc.api.postHistory = realHist;
  const r = await SY.syncPending(s, pc.api);
  assert.equal(r.messages, 0);
  assert.equal(r.skipped, 1);                      // client_id sayesinde PC'de tek kopya
  assert.equal(pc.msgs.length, 1);
  assert.equal(SY.hasPending(s), false);
});

test("syncPending: çok sayıda mesaj parçalar halinde gönderilir", async () => {
  const pc = fakePc([]);
  const s = fresh();
  for (let i = 0; i < 450; i++) SY.queueMessage(s, "user", "m" + i, ids);
  await SY.syncPending(s, pc.api);
  assert.deepEqual(pc.calls.filter((c) => c[0] === "hist").map((c) => c[1]), [200, 200, 50]);
  assert.equal(pc.msgs.length, 450);
});

test("newClientId biçimi", () => {
  assert.match(SY.newClientId(), /^c-[a-z0-9]+-[a-z0-9]{12}$/);
  assert.notEqual(SY.newClientId(), SY.newClientId());
  assert.ok(SY.newClientId().length <= 80);
});
