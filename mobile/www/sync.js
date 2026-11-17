/* J.A.R.V.I.S. mobil — hafıza / mesaj eşitleme (v1.7)
 * PC'deki SQLite tek doğru kaynaktır. Telefon:
 *  - PC hafızasının son eşitlenen kopyasını önbellekte tutar (state.pcMemories) -> bulut modunun sistem istemi,
 *  - bulut modunda eklenen/silinen hafızaları ve mesajları "bekleyen" olarak saklar (state.pending),
 *  - PC'ye ilk bağlantıda bekleyenleri gönderir. Her öğenin client_id'si vardır; PC aynı client_id'yi
 *    ikinci kez eklemez, aynı içerikli hafızayı da eklemez (cevap kaybolup tekrar gönderilse bile).
 * Saf mantık: DOM'a dokunmaz, tarayıcıda window.JarvisSync, Node'da require() ile kullanılır.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.JarvisSync = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const CHUNK = 200;

  function trLower(s) { return String(s || "").replace(/I/g, "ı").replace(/İ/g, "i").toLowerCase(); }
  /** PC'deki turkish.normalize ile aynı: boşluk sadeleşir, Türkçe küçük harf, ı/i aynı, sondaki . ! atılır. */
  function normalize(s) {
    return trLower(String(s || "").split(/\s+/).filter(Boolean).join(" ")).replace(/ı/g, "i").replace(/[ .!]+$/, "").trim();
  }
  function cleanText(s) { return String(s || "").split(/\s+/).filter(Boolean).join(" ").slice(0, 2000); }

  function newClientId(rand, now) {
    const r = rand || Math.random;
    const abc = "abcdefghijklmnopqrstuvwxyz0123456789";
    let s = "";
    for (let i = 0; i < 12; i++) s += abc[Math.floor(r() * abc.length) % abc.length];
    return "c-" + (now || Date.now()).toString(36) + "-" + s;
  }

  /** Eksik alanları tamamlar; v1.6'daki telefon hafızasını (state.memories) bekleyen eklemelere taşır. */
  function migrateSyncState(state, idgen) {
    const gen = idgen || newClientId;
    const s = state || {};
    if (!s.pending || typeof s.pending !== "object") s.pending = {};
    if (!Array.isArray(s.pending.memories)) s.pending.memories = [];
    if (!Array.isArray(s.pending.messages)) s.pending.messages = [];
    if (!Array.isArray(s.pcMemories)) s.pcMemories = [];
    if (typeof s.pcMemoriesAt !== "number") s.pcMemoriesAt = 0;
    if (Array.isArray(s.memories)) {
      for (const m of s.memories) {
        const text = m && (m.text || m.content);
        if (text) queueMemory(s, text, gen, m.t);
      }
      delete s.memories;
    }
    return s;
  }

  const deletedIds = (s) => new Set(s.pending.memories.filter((o) => o.op === "del").map((o) => o.id));

  /**
   * Ekranda / sistem isteminde kullanılacak hafıza listesi:
   * önce bekleyen eklemeler (en yeni önce), sonra silinmesi beklenmeyen PC kayıtları.
   * Öğe: {key, text, pending, id?}
   */
  function effectiveMemories(s) {
    const dels = deletedIds(s);
    const adds = s.pending.memories.filter((o) => o.op === "add").slice().reverse()
      .map((o) => ({ key: o.client_id, text: o.text, pending: true }));
    const pc = s.pcMemories.filter((m) => !dels.has(m.id))
      .map((m) => ({ key: "pc:" + m.id, id: m.id, text: m.content, pending: false }));
    return adds.concat(pc);
  }

  /** Dönüş: "queued" | "exists" | "restored" | "empty" */
  function queueMemory(s, text, idgen, now) {
    const clean = cleanText(text);
    const norm = normalize(clean);
    if (!norm) return "empty";
    const del = s.pending.memories.find((o) => o.op === "del" && normalize(o.text) === norm);
    if (del) { s.pending.memories.splice(s.pending.memories.indexOf(del), 1); return "restored"; }
    if (effectiveMemories(s).some((m) => normalize(m.text) === norm)) return "exists";
    s.pending.memories.push({ op: "add", client_id: (idgen || newClientId)(), text: clean, t: now || Date.now() });
    return "queued";
  }

  /** "unut X": içinde X geçen bekleyen eklemeler atılır, PC kayıtları için silme bekletilir. Dönüş: sayı */
  function queueForget(s, phrase, idgen) {
    const p = trLower(String(phrase || "").trim());
    if (!p) return 0;
    let n = 0;
    s.pending.memories = s.pending.memories.filter((o) => {
      const hit = o.op === "add" && trLower(o.text).includes(p);
      if (hit) n++;
      return !hit;
    });
    for (const m of effectiveMemories(s)) {
      if (!m.pending && trLower(m.text).includes(p)) { queueDelete(s, m, idgen); n++; }
    }
    return n;
  }

  function queueDelete(s, item, idgen) {
    s.pending.memories.push({ op: "del", client_id: (idgen || newClientId)(), id: item.id, text: item.text, t: Date.now() });
  }

  /** Ekrandaki öğeyi (key) siler. Dönüş: true/false */
  function removeEffective(s, key, idgen) {
    const item = effectiveMemories(s).find((m) => m.key === key);
    if (!item) return false;
    if (item.pending) s.pending.memories = s.pending.memories.filter((o) => o.client_id !== key);
    else queueDelete(s, item, idgen);
    return true;
  }

  /** Ekrandaki öğeyi düzenler (bulut modu). PC kaydı ise "sil + yeni ekle" bekletilir. */
  function editEffective(s, key, text, idgen) {
    const clean = cleanText(text);
    if (!normalize(clean)) return removeEffective(s, key, idgen) ? "removed" : "missing";
    const item = effectiveMemories(s).find((m) => m.key === key);
    if (!item) return "missing";
    if (normalize(item.text) === normalize(clean)) return "unchanged";
    if (item.pending) {
      const op = s.pending.memories.find((o) => o.client_id === key);
      if (effectiveMemories(s).some((m) => m.key !== key && normalize(m.text) === normalize(clean))) {
        s.pending.memories.splice(s.pending.memories.indexOf(op), 1);
        return "merged";
      }
      op.text = clean;
      return "edited";
    }
    queueDelete(s, item, idgen);
    const r = queueMemory(s, clean, idgen);
    return r === "exists" ? "merged" : "edited";
  }

  function queueMessage(s, role, text, idgen, now) {
    const t = String(text || "").trim();
    if (!t || (role !== "user" && role !== "assistant")) return false;
    s.pending.messages.push({ client_id: (idgen || newClientId)(), role, text: t, t: now || Date.now() });
    return true;
  }

  function pendingCount(s) {
    return { memories: s.pending.memories.length, messages: s.pending.messages.length };
  }
  function hasPending(s) { const c = pendingCount(s); return c.memories + c.messages > 0; }

  /** Bulut modunun sistem istemi için hafıza satırları. */
  function promptMemories(s, limit) {
    const list = effectiveMemories(s).slice(0, limit || 40);
    return list.length ? list.map((m) => "- " + m.text).join("\n") : "- Yok";
  }

  /**
   * Bekleyenleri PC'ye gönderir ve önbelleği tazeler.
   * api: {postMemories(items) -> {results:[{client_id,status,id}]}, deleteMemory(id), postHistory(messages) ->
   *       {imported, skipped}, getMemories() -> {memories:[...]}}
   * persist(): her adımdan sonra çağrılır (yarıda kesilirse gönderilenler tekrar gönderilmesin diye).
   * Hata olursa fırlatır; gönderilmemiş öğeler bekleyende kalır.
   */
  async function syncPending(s, api, persist, now) {
    const done = persist || (() => {});
    const out = { added: 0, existed: 0, deleted: 0, messages: 0, skipped: 0 };
    const sent = new Set();
    const ops = s.pending.memories.slice();
    // Sırayı korumak için (ör. düzenleme = sil + ekle) işlemler gruplar halinde, sırayla gönderilir.
    for (let i = 0; i < ops.length;) {
      if (ops[i].op === "del") {
        await api.deleteMemory(ops[i].id);
        out.deleted++;
        sent.add(ops[i].client_id);
        i++;
      } else {
        const group = [];
        while (i < ops.length && ops[i].op === "add" && group.length < CHUNK) group.push(ops[i++]);
        const r = await api.postMemories(group.map((o) => ({ client_id: o.client_id, content: o.text })));
        for (const res of (r && r.results) || []) {
          if (res.status === "added" || res.status === "reactivated") out.added++;
          else if (res.status === "exists" || res.status === "synced") out.existed++;
        }
        group.forEach((o) => sent.add(o.client_id));
      }
      s.pending.memories = s.pending.memories.filter((o) => !sent.has(o.client_id));
      done();
    }
    while (s.pending.messages.length) {
      const batch = s.pending.messages.slice(0, CHUNK);
      const r = await api.postHistory(batch.map((m) => ({ client_id: m.client_id, role: m.role, text: m.text, t: m.t })));
      out.messages += (r && r.imported) || 0;
      out.skipped += (r && r.skipped) || 0;
      const ids = new Set(batch.map((m) => m.client_id));
      s.pending.messages = s.pending.messages.filter((m) => !ids.has(m.client_id));
      done();
    }
    const fresh = await api.getMemories();
    if (fresh && Array.isArray(fresh.memories)) {
      s.pcMemories = fresh.memories.map((m) => ({ id: m.id, content: m.content, created_at: m.created_at }));
      s.pcMemoriesAt = now || Date.now();
      done();
    }
    return out;
  }

  /** Kullanıcıya gösterilecek kısa özet; bir şey olmadıysa "". */
  function syncSummary(r) {
    const parts = [];
    if (r.added) parts.push(`${r.added} hafıza eklendi`);
    if (r.existed) parts.push(`${r.existed} hafıza zaten vardı`);
    if (r.deleted) parts.push(`${r.deleted} hafıza silindi`);
    if (r.messages) parts.push(`${r.messages} mesaj aktarıldı`);
    return parts.length ? "PC ile eşitlendi: " + parts.join(", ") + "." : "";
  }

  return {
    normalize, trLower, newClientId, migrateSyncState, effectiveMemories, queueMemory, queueForget,
    removeEffective, editEffective, queueMessage, pendingCount, hasPending, promptMemories,
    syncPending, syncSummary,
  };
});
