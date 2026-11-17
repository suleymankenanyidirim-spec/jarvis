/* J.A.R.V.I.S. mobil — arka plan görevi bitti bildirimi (v1.8)
 * /api/tasks düzenli yoklanır; daha önce sürmekte olan (ya da ilk yoklamadan sonra ortaya çıkan) bir görev
 * "completed" / "failed" olunca bir kez bildirim üretilir. İlk yoklamada zaten bitmiş görevler bildirilmez.
 * Saf mantık: DOM'a dokunmaz, tarayıcıda window.JarvisTaskNotify, Node'da require() ile kullanılır.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.JarvisTaskNotify = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const DONE = ["completed", "failed"];
  const MAX_SEEN = 200;

  /**
   * prev: önceki durum ({pc, seen: {id: status}}) ya da null; tasks: /api/tasks listesi; pc: bağlı PC adresi.
   * Dönüş: {notify: [görev...], next: yeni durum}
   */
  function diffTasks(prev, tasks, pc) {
    const list = Array.isArray(tasks) ? tasks.filter((t) => t && t.id) : [];
    const seen = {};
    for (const t of list.slice(0, MAX_SEEN)) seen[t.id] = String(t.status || "");
    const next = { pc: pc || "", seen };
    // İlk yoklama ya da başka bir PC: yalnızca durumu öğren, bildirme.
    if (!prev || !prev.seen || (prev.pc || "") !== (pc || "")) return { notify: [], next };
    const notify = [];
    for (const t of list) {
      const before = prev.seen[t.id];
      const now = String(t.status || "");
      if (!DONE.includes(now)) continue;
      if (before === undefined || !DONE.includes(before)) notify.push(t);
    }
    return { notify, next };
  }

  /** Görev kimliğinden (8 onaltılık karakter) bildirim için 31 bitlik tamsayı. */
  function notificationId(taskId) {
    const hex = String(taskId || "").replace(/[^0-9a-f]/gi, "").slice(0, 7);
    if (hex) return parseInt(hex, 16) || 1;
    let h = 0;
    for (const c of String(taskId || "")) h = (h * 31 + c.charCodeAt(0)) & 0x7fffffff;
    return h || 1;
  }

  function shorten(text, n) {
    const t = String(text || "").replace(/\s+/g, " ").trim();
    return t.length > n ? t.slice(0, n - 1) + "…" : t;
  }

  /** {id, title, body} — Türkçe bildirim metni. detail: /api/tasks/<id> (sonuç metni için, isteğe bağlı). */
  function notificationFor(task, detail) {
    const name = shorten(task.title || task.id, 60);
    if (task.status === "failed") {
      return { id: notificationId(task.id), title: "JARVIS görevi başarısız",
        body: name + (task.error ? " — " + shorten(task.error, 120) : "") };
    }
    const result = detail && detail.result ? " — " + shorten(detail.result, 140) : "";
    return { id: notificationId(task.id), title: "JARVIS görevi bitti", body: name + result };
  }

  return { diffTasks, notificationId, notificationFor, DONE };
});
