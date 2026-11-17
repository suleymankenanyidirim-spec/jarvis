/* J.A.R.V.I.S. mobil — PC adres listesi / bağlantı seçimi (v1.5) + eşleşme kodu (v1.9)
 * Saf mantık: DOM'a dokunmaz, tarayıcıda window.JarvisBackend, Node'da require() ile kullanılır.
 * - Birden çok PC adresi (ev WiFi IP'si, Tailscale 100.x adresi …) sırayla denenir.
 * - Son çalışan adres hatırlanır ve bir dahaki sefere önce o denenir.
 * - Otomatik modda bulut YALNIZCA hiçbir PC adresine ulaşılamazsa devreye girer (sınırlı mod).
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.JarvisBackend = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const DEFAULT_PORT = 5000;
  const LIMITED_TEXT = "PC'ye ulaşılamıyor, sınırlı mod";

  /** "192.168.1.20" -> "http://192.168.1.20:5000" ; sondaki / atılır. */
  function normalizeUrl(value) {
    let v = String(value || "").trim();
    if (!v) return "";
    if (!/^https?:\/\//i.test(v)) v = "http://" + v;
    v = v.replace(/\/+$/, "");
    const hostPart = v.replace(/^https?:\/\//i, "");
    if (!/:\d+(\/|$)/.test(hostPart)) v += ":" + DEFAULT_PORT;
    return v;
  }

  /** Satır, virgül ya da boşlukla ayrılmış adresleri sıralı, tekrarsız listeye çevirir. */
  function parseUrlList(input) {
    const raw = Array.isArray(input) ? input : String(input || "").split(/[\s,;]+/);
    const out = [];
    for (const item of raw) {
      const u = normalizeUrl(item);
      if (u && !out.includes(u)) out.push(u);
    }
    return out;
  }

  /** Eski (v1.4) tek pcUrl ayarını yeni pcUrls listesine taşır. */
  function migrateState(state) {
    const s = state || {};
    if (!Array.isArray(s.pcUrls)) s.pcUrls = parseUrlList(s.pcUrl ? [s.pcUrl] : []);
    else s.pcUrls = parseUrlList(s.pcUrls);
    if (typeof s.lastPcUrl !== "string" || !s.pcUrls.includes(s.lastPcUrl)) s.lastPcUrl = "";
    delete s.pcUrl;
    return s;
  }

  /** PC'nin linkindeki pcs= listesini mevcut listeyle birleştirir (linkteki sıra önce). */
  function mergeUrls(fromLink, existing) {
    return parseUrlList(parseUrlList(fromLink).concat(parseUrlList(existing)));
  }

  /** Deneme sırası: önce son çalışan adres, sonra listedeki sıra. */
  function tryOrder(urls, last) {
    const list = parseUrlList(urls);
    const l = normalizeUrl(last);
    if (l && list.includes(l)) return [l].concat(list.filter((u) => u !== l));
    return list;
  }

  /** 100.64.0.0/10 ya da *.ts.net -> Tailscale. */
  function isTailscaleUrl(url) {
    let host = "";
    try { host = new URL(normalizeUrl(url)).hostname; } catch (_) { return false; }
    if (/\.ts\.net$/i.test(host)) return true;
    const m = host.match(/^(\d+)\.(\d+)\.(\d+)\.(\d+)$/);
    return !!m && Number(m[1]) === 100 && Number(m[2]) >= 64 && Number(m[2]) <= 127;
  }

  function linkLabel(url) {
    return isTailscaleUrl(url) ? "PC BAĞLI · TAILSCALE" : "PC BAĞLI · EV";
  }

  /**
   * Adresleri sırayla dener. ping(url) -> Promise<{ok, why?, auth?}>
   * Dönüş: {url, result, tried:[...]} ya da {url:null, why, tried}
   */
  async function findReachable(urls, last, ping) {
    const order = tryOrder(urls, last);
    const tried = [];
    if (!order.length) return { url: null, why: "PC adresi girilmedi", tried };
    let authFail = false, locked = "";
    for (const url of order) {
      let r;
      try { r = await ping(url); } catch (e) { r = { ok: false, why: String((e && e.message) || e) }; }
      tried.push({ url, ok: !!(r && r.ok), why: r && r.why });
      if (r && r.ok) return { url, result: r, tried };
      if (r && r.auth === false) authFail = true;
      if (r && r.locked) locked = r.why;
    }
    const why = locked ? locked : authFail ? "Erişim anahtarı yanlış"
      : (order.length > 1 ? `Hiçbir PC adresine ulaşılamadı (${order.length} adres denendi)` : "PC'ye ulaşılamadı");
    return { url: null, why, tried };
  }

  /**
   * Hangi beyin kullanılacak?
   * mode: "auto" | "pc" | "cloud"
   * Dönüş: {backend: "pc"|"cloud"|"none", limited: bool}
   *  - limited: otomatik/PC modunda PC'ye ulaşılamadı (ekranda "sınırlı mod" yazılır).
   */
  function decide({ mode, pcReachable, hasCloud, hasPcUrls }) {
    if (mode === "cloud") return { backend: hasCloud ? "cloud" : "none", limited: false };
    if (pcReachable) return { backend: "pc", limited: false };
    if (mode === "pc") return { backend: "none", limited: !!hasPcUrls };
    return { backend: hasCloud ? "cloud" : "none", limited: !!hasPcUrls };
  }

  /** Sınırlı mod şeridinde gösterilecek metin. */
  function limitedText(backend, provider) {
    if (backend === "cloud") {
      return `${LIMITED_TEXT} — bulut (${String(provider || "").toUpperCase()}) kullanılıyor; PC araçları, hafıza ve görevler yok.`;
    }
    return `${LIMITED_TEXT} — bulut anahtarı da yok. Ayarlar'dan adresleri ve anahtarı kontrol edin.`;
  }

  // ------------------------------------------------ v1.9: eşleşme (PC v2.6)
  /** " abcd-ef23 " -> "ABCDEF23" (PC'deki normalize_pair_code ile aynı). */
  function normalizePairCode(code) {
    return String(code || "").toUpperCase().replace(/[^A-Z0-9]/g, "");
  }
  function isPairCode(code) {
    const c = normalizePairCode(code);
    return c.length >= 6 && c.length <= 12;
  }

  /**
   * PC linkindeki parametreler: ?pair=KOD&pcs=… . Eski PC linklerindeki ?token= artık kullanılmaz
   * (anahtar URL'de durmasın), yalnızca adres çubuğundan silinir.
   * Dönüş: {pair, pcs, legacyToken, rest} — rest: kalan sorgu ("" ya da "?a=b").
   */
  function readLinkParams(search) {
    const params = new URLSearchParams(String(search || ""));
    const out = {
      pair: normalizePairCode(params.get("pair")),
      pcs: params.get("pcs") || "",
      legacyToken: !!params.get("token"),
    };
    ["pair", "pcs", "token"].forEach((k) => params.delete(k));
    const rest = params.toString();
    out.rest = rest ? "?" + rest : "";
    return out;
  }

  /**
   * Eşleşme kodunu PC adreslerine sırayla gönderir. post(url, code) -> Promise<{status, data}>.
   * Ağ hatasında sıradaki adres denenir; PC cevap verdiyse (403/429) durulur (kod tek kullanımlık).
   * Dönüş: {url, token} ya da {url:null, why}
   */
  async function pairWith(urls, last, code, post) {
    const c = normalizePairCode(code);
    if (!isPairCode(c)) return { url: null, why: "Eşleşme kodu 8 karakter olmalı (ör. ABCD-EFGH)" };
    const order = tryOrder(urls, last);
    if (!order.length) return { url: null, why: "PC adresi girilmedi" };
    let why = "PC'ye ulaşılamadı";
    for (const url of order) {
      let r;
      try { r = await post(url, c); } catch (e) { why = "PC'ye ulaşılamadı (" + ((e && e.message) || e) + ")"; continue; }
      const data = (r && r.data) || {};
      if (r && r.status === 200 && data.token) return { url, token: String(data.token) };
      if (r && r.status === 404) { why = "PC eşleşme kodunu tanımıyor (PC'deki JARVIS'i güncelleyin)"; continue; }
      return { url: null, why: data.error || ("Eşleşme başarısız (" + (r && r.status) + ")") };
    }
    return { url: null, why };
  }

  /** /api/status cevabından ping sonucu. 429 = çok fazla hatalı deneme (kilit). */
  function pingVerdict(status, data) {
    if (status === 401) return { ok: false, auth: false, why: "Erişim anahtarı yanlış" };
    if (status === 429) {
      return { ok: false, locked: true,
        why: (data && data.error) || "Çok fazla hatalı deneme; bir süre sonra tekrar deneyin" };
    }
    return null;
  }

  return {
    DEFAULT_PORT, LIMITED_TEXT,
    normalizeUrl, parseUrlList, migrateState, mergeUrls, tryOrder,
    isTailscaleUrl, linkLabel, findReachable, decide, limitedText,
    normalizePairCode, isPairCode, readLinkParams, pairWith, pingVerdict,
  };
});
