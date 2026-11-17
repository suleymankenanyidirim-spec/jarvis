// v1.8: APK güncelleme denetimi ve görev bitti bildirimi (saf mantık).
const test = require("node:test");
const assert = require("node:assert/strict");
const UP = require("../www/updates.js");
const TN = require("../www/tasknotify.js");

const release = (over) => Object.assign({
  tag_name: "v2.5.0", draft: false, prerelease: false, html_url: "https://github.com/o/r/releases/tag/v2.5.0",
  body: "**Telefon:** …\nAndroid versionCode: 41\n",
  assets: [{ name: "JARVIS_desktop.zip", browser_download_url: "https://x/zip" },
    { name: "JARVIS-2.5.0.apk", browser_download_url: "https://x/apk", size: 7340032 }],
}, over || {});

test("sürüm karşılaştırma", () => {
  assert.equal(UP.compareVersions("v2.5.0", "2.4.9"), 1);
  assert.equal(UP.compareVersions("2.5.0", "v2.5.0"), 0);
  assert.equal(UP.compareVersions("2.10.0", "2.9.9"), 1);
  assert.equal(UP.compareVersions("2.5.0", "2.5.0-dev.12"), 1);
  assert.equal(UP.compareVersions("2.5.0-dev.12", "2.5.0"), -1);
  assert.equal(UP.compareVersions("çöp", "2.5.0"), 0);
  assert.deepEqual(UP.parseVersion("v3"), [3, 0, 0]);
});

test("release notundan versionCode", () => {
  assert.equal(UP.releaseVersionCode("Android versionCode: 41"), 41);
  assert.equal(UP.releaseVersionCode("versionCode=7"), 7);
  assert.equal(UP.releaseVersionCode("yok"), 0);
});

test("versionCode büyükse güncelleme var, küçük/eşitse yok", () => {
  const up = UP.decideUpdate({ versionCode: 40, versionName: "2.5.0" }, release());
  assert.equal(up.available, true);
  assert.equal(up.reason, "versionCode");
  assert.equal(up.apkUrl, "https://x/apk");
  assert.equal(up.apkName, "JARVIS-2.5.0.apk");
  assert.equal(up.version, "2.5.0");
  assert.equal(UP.decideUpdate({ versionCode: 41, versionName: "2.5.0" }, release()).available, false);
  // main dalından derlenmiş daha yeni APK (2.5.0-dev.45) eski release'i "yeni" sanmaz
  assert.equal(UP.decideUpdate({ versionCode: 45, versionName: "2.5.0-dev.45" }, release()).available, false);
});

test("eski release (versionCode yok): etiket sürümüyle karşılaştırılır", () => {
  const old = release({ body: "notlar" });
  assert.equal(UP.decideUpdate({ versionCode: 3, versionName: "2.4.0" }, old).available, true);
  assert.equal(UP.decideUpdate({ versionCode: 3, versionName: "2.5.0" }, old).available, false);
  const unknown = UP.decideUpdate({ versionCode: 0, versionName: "dev" }, old);
  assert.equal(unknown.available, false);
  assert.equal(unknown.reason, "bu derlemenin sürümü bilinmiyor");
});

test("APK'sız, taslak ya da ön sürüm release güncelleme sayılmaz", () => {
  const cur = { versionCode: 1, versionName: "1.0.0" };
  assert.equal(UP.decideUpdate(cur, release({ assets: [] })).reason, "APK yok");
  assert.equal(UP.decideUpdate(cur, release({ draft: true })).available, false);
  assert.equal(UP.decideUpdate(cur, release({ prerelease: true })).available, false);
  assert.equal(UP.decideUpdate(cur, {}).available, false);
});

test("otomatik denetim: yalnızca CI derlemesinde, aralık dolunca", () => {
  const build = { versionCode: 40, repo: "kullanici/jarvis" };
  assert.equal(UP.shouldAutoCheck(build, 0, 1e12), true);
  assert.equal(UP.shouldAutoCheck(build, 1e12 - 60000, 1e12), false);
  assert.equal(UP.shouldAutoCheck(build, 1e12 - UP.CHECK_INTERVAL_MS, 1e12), true);
  assert.equal(UP.shouldAutoCheck({ versionCode: 0, repo: "kullanici/jarvis" }, 0, 1e12), false);
  assert.equal(UP.shouldAutoCheck({ versionCode: 4, repo: "" }, 0, 1e12), false);
  assert.equal(UP.releasesUrl("kullanici/jarvis"), "https://api.github.com/repos/kullanici/jarvis/releases/latest");
  assert.equal(UP.releasesUrl("kötü/../depo yol"), "");
  assert.equal(UP.sizeText(7340032), "7,0 MB");
});

test("görev bildirimi: ilk yoklamada bildirilmez, sonra biten bir kez bildirilir", () => {
  const pc = "http://192.168.1.20:5000";
  let r = TN.diffTasks(null, [{ id: "aa11", status: "completed" }, { id: "bb22", status: "running" }], pc);
  assert.deepEqual(r.notify, []);
  r = TN.diffTasks(r.next, [{ id: "aa11", status: "completed" }, { id: "bb22", status: "completed", title: "Araştırma" },
    { id: "cc33", status: "failed", error: "zaman aşımı" }, { id: "dd44", status: "queued" }], pc);
  assert.deepEqual(r.notify.map((t) => t.id), ["bb22", "cc33"]);
  r = TN.diffTasks(r.next, [{ id: "bb22", status: "completed" }, { id: "cc33", status: "failed" }, { id: "dd44", status: "running" }], pc);
  assert.deepEqual(r.notify, []);
  r = TN.diffTasks(r.next, [{ id: "dd44", status: "recovering" }], pc);
  assert.deepEqual(r.notify, []);
  r = TN.diffTasks(r.next, [{ id: "dd44", status: "completed" }], pc);
  assert.deepEqual(r.notify.map((t) => t.id), ["dd44"]);
});

test("görev bildirimi: başka PC'ye geçince yeniden öğrenir, bildirim yağdırmaz", () => {
  const first = TN.diffTasks(null, [{ id: "aa11", status: "running" }], "http://a:5000").next;
  const r = TN.diffTasks(first, [{ id: "zz99", status: "completed" }], "http://b:5000");
  assert.deepEqual(r.notify, []);
  assert.equal(r.next.pc, "http://b:5000");
  assert.deepEqual(TN.diffTasks(first, "çöp", "http://a:5000").notify, []);
});

test("bildirim metni ve kimliği", () => {
  const ok = TN.notificationFor({ id: "ab12cd34", status: "completed", title: "Fiyat araştırması" },
    { result: "En uygun fiyat 1.250 TL, üç mağaza karşılaştırıldı." });
  assert.equal(ok.title, "JARVIS görevi bitti");
  assert.match(ok.body, /^Fiyat araştırması — En uygun fiyat/);
  const bad = TN.notificationFor({ id: "ab12cd34", status: "failed", title: "X", error: "Ollama kapalı" });
  assert.equal(bad.title, "JARVIS görevi başarısız");
  assert.equal(bad.body, "X — Ollama kapalı");
  assert.equal(ok.id, parseInt("ab12cd3", 16));
  assert.ok(TN.notificationId("görev-kimliği") > 0);
  assert.ok(TN.notificationId("ffffffff") <= 0x7fffffff);
  const long = TN.notificationFor({ id: "1", status: "completed", title: "a".repeat(100) }, { result: "b".repeat(500) });
  assert.ok(long.body.length < 220);
});
