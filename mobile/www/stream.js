/* J.A.R.V.I.S. mobil — akışlı cevap yardımcıları (v1.6)
 * Saf mantık: DOM'a dokunmaz, tarayıcıda window.JarvisStream, Node'da require() ile kullanılır.
 * - createSseParser: /api/ask_stream'in SSE (text/event-stream) çıktısını olaylara ayırır.
 * - createSentenceSplitter: gelen metin parçalarından tamamlanan cümleleri çıkarır (seslendirme için).
 * - newSessionId: telefonun PC'deki kendi konuşma oturumu için kimlik.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.JarvisStream = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  /**
   * onEvent({type, ...}) her tam olay için çağrılır.
   * push(metin) parça parça gelen metni alır; end() kalan tamponu işler.
   */
  function createSseParser(onEvent) {
    let buf = "";
    function emit(block) {
      let name = "", data = [];
      for (const raw of block.split(/\r?\n/)) {
        if (!raw || raw.startsWith(":")) continue;           // yorum / keep-alive
        const i = raw.indexOf(":");
        const field = i < 0 ? raw : raw.slice(0, i);
        let value = i < 0 ? "" : raw.slice(i + 1);
        if (value.startsWith(" ")) value = value.slice(1);
        if (field === "event") name = value;
        else if (field === "data") data.push(value);
      }
      if (!data.length) return;
      const text = data.join("\n");
      let ev;
      try { ev = JSON.parse(text); } catch (_) { ev = { text }; }
      if (!ev || typeof ev !== "object") ev = { text: String(ev) };
      if (!ev.type) ev.type = name || "message";
      onEvent(ev);
    }
    return {
      push(chunk) {
        buf += String(chunk || "");
        let m;
        while ((m = buf.match(/\r?\n\r?\n/))) {
          const block = buf.slice(0, m.index);
          buf = buf.slice(m.index + m[0].length);
          emit(block);
        }
      },
      end() {
        if (buf.trim()) emit(buf);
        buf = "";
      },
    };
  }

  // Cümle sonu: . ! ? … (ardından boşluk/satır sonu) ya da satır sonu.
  const END_RE = /([.!?…]+["')\]]*)(\s+)|(\n+)/g;

  /**
   * push(parça) -> tamamlanan cümleler dizisi; flush() -> kalan metin (varsa) dizisi.
   * minChars'tan kısa cümleler sonrakiyle birleştirilir ("Tamam." gibi tek kelimeler için ayrı ses isteği olmasın).
   * Kod bloğu (```) kapanmadan içinden cümle çıkarılmaz.
   */
  function createSentenceSplitter(opts) {
    const minChars = (opts && opts.minChars) || 12;
    let buf = "";
    function take(final) {
      const out = [];
      let start = 0, cut = 0;
      if (!final && ((buf.match(/```/g) || []).length % 2 === 1)) return out;
      END_RE.lastIndex = 0;
      let m;
      while ((m = END_RE.exec(buf))) {
        const end = m.index + (m[1] ? m[1].length : 0);
        if (((buf.slice(0, end).match(/```/g) || []).length % 2) === 1) continue;   // kod bloğunun içi
        const piece = buf.slice(start, end).trim();
        if (piece.replace(/\s/g, "").length >= minChars) {
          out.push(piece);
          start = m.index + m[0].length;
          cut = start;
        }
      }
      buf = buf.slice(cut);
      if (final) {
        const rest = buf.trim();
        if (rest) out.push(rest);
        buf = "";
      }
      return out;
    }
    return {
      push(chunk) { buf += String(chunk || ""); return take(false); },
      flush() { return take(true); },
      reset() { buf = ""; },
      pending() { return buf; },
    };
  }

  function newSessionId(rand) {
    const r = rand || Math.random;
    const abc = "abcdefghijklmnopqrstuvwxyz0123456789";
    let s = "";
    for (let i = 0; i < 10; i++) s += abc[Math.floor(r() * abc.length) % abc.length];
    return "phone-" + s;
  }

  /** PC'nin kabul ettiği biçim: harf, rakam, _ . : - ; en çok 64 karakter; "task-" ile başlamaz. */
  function isValidSessionId(id) {
    return typeof id === "string" && /^[A-Za-z0-9_.:\-]{1,64}$/.test(id) && !id.startsWith("task-");
  }

  return { createSseParser, createSentenceSplitter, newSessionId, isValidSessionId };
});
