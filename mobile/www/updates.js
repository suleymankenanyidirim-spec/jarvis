/* J.A.R.V.I.S. mobil — APK güncelleme denetimi (v1.8)
 * GitHub Releases'taki son sürüm bu derlemeden yeni mi? Saf mantık: DOM'a dokunmaz,
 * tarayıcıda window.JarvisUpdates, Node'da require() ile kullanılır.
 *
 * Karşılaştırma önceliği:
 *  1. Release notundaki "Android versionCode: N" ile bu APK'nın versionCode'u (her derlemede artar).
 *  2. Yoksa (eski release'ler) etiketin sürümü (v2.5.0) ile bu APK'nın versionName'i.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.JarvisUpdates = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const CHECK_INTERVAL_MS = 15 * 60 * 1000;   // açılışta denetlenir; hızlı yeniden açılışta GitHub tekrar sorgulanmaz

  /** "v2.5.0" / "2.5.0-dev.12" -> [2,5,0] (ön sürüm eki atılır); geçersizse null. */
  function parseVersion(v) {
    const m = String(v || "").trim().replace(/^v/i, "").match(/^(\d+)(?:\.(\d+))?(?:\.(\d+))?/);
    return m ? [Number(m[1]), Number(m[2] || 0), Number(m[3] || 0)] : null;
  }
  function isPrerelease(v) { return /^v?\d+(\.\d+){0,2}-/.test(String(v || "").trim()); }

  /** a > b: 1, eşit: 0, küçük: -1. Aynı sayılarda ön sürüm (2.5.0-dev.3) tam sürümden (2.5.0) küçüktür. */
  function compareVersions(a, b) {
    const x = parseVersion(a), y = parseVersion(b);
    if (!x || !y) return 0;
    for (let i = 0; i < 3; i++) if (x[i] !== y[i]) return x[i] > y[i] ? 1 : -1;
    const pa = isPrerelease(a), pb = isPrerelease(b);
    return pa === pb ? 0 : (pa ? -1 : 1);
  }

  function releaseVersionCode(body) {
    const m = String(body || "").match(/versionCode\D{0,5}(\d{1,10})/i);
    return m ? Number(m[1]) : 0;
  }

  function pickApk(assets) {
    const list = (assets || []).filter((a) => a && /\.apk$/i.test(a.name || "") && a.browser_download_url);
    return list[0] || null;
  }

  function releasesUrl(repo) {
    return /^[\w.-]+\/[\w.-]+$/.test(String(repo || "")) ? `https://api.github.com/repos/${repo}/releases/latest` : "";
  }

  /**
   * current: {versionCode, versionName}; release: GitHub /releases/latest cevabı.
   * Dönüş: {available, reason, version, versionCode, apkUrl, apkName, apkSize, pageUrl, notes}
   */
  function decideUpdate(current, release) {
    const cur = current || {};
    const rel = release || {};
    const base = { available: false, version: String(rel.tag_name || "").replace(/^v/i, ""), versionCode: releaseVersionCode(rel.body),
      apkUrl: "", apkName: "", apkSize: 0, pageUrl: rel.html_url || "", notes: "" };
    if (!rel.tag_name || rel.draft || rel.prerelease) return Object.assign(base, { reason: "sürüm yok" });
    const apk = pickApk(rel.assets);
    if (!apk) return Object.assign(base, { reason: "APK yok" });
    Object.assign(base, { apkUrl: apk.browser_download_url, apkName: apk.name, apkSize: apk.size || 0,
      notes: String(rel.body || "").slice(0, 600) });
    const curCode = Number(cur.versionCode) || 0;
    if (base.versionCode && curCode) {
      return Object.assign(base, { available: base.versionCode > curCode, reason: "versionCode" });
    }
    if (!parseVersion(cur.versionName)) return Object.assign(base, { reason: "bu derlemenin sürümü bilinmiyor" });
    return Object.assign(base, { available: compareVersions(rel.tag_name, cur.versionName) > 0, reason: "sürüm" });
  }

  /** Açılışta denetim: yerel derlemede (versionCode 0) ya da depo bilinmiyorsa yapılmaz; aralık dolmadıysa da. */
  function shouldAutoCheck(build, lastCheck, now) {
    if (!build || !Number(build.versionCode) || !releasesUrl(build.repo)) return false;
    return !lastCheck || (now || Date.now()) - lastCheck >= CHECK_INTERVAL_MS;
  }

  function sizeText(bytes) {
    const mb = Number(bytes) / (1024 * 1024);
    return mb >= 0.1 ? mb.toFixed(1).replace(".", ",") + " MB" : "";
  }

  return { CHECK_INTERVAL_MS, parseVersion, compareVersions, releaseVersionCode, pickApk, releasesUrl,
    decideUpdate, shouldAutoCheck, sizeText };
});
