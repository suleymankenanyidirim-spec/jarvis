// v2.5: imzalı release APK — CI yardımcısı, Android proje yaması ve iş akışı denetimi.
const test = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync, mkdtempSync, rmSync, existsSync } = require("node:fs");
const { join } = require("node:path");
const { tmpdir } = require("node:os");
const { spawnSync, execFileSync } = require("node:child_process");

const MOBILE = join(__dirname, "..");
const ROOT = join(MOBILE, "..");
const load = (name) => import("file://" + join(MOBILE, "scripts", name).replace(/\\/g, "/"));

// Capacitor 8 android şablonundaki app/build.gradle'ın kısaltılmış hali
const GRADLE = `apply plugin: 'com.android.application'

android {
    namespace = "com.jarvis.mobile"
    defaultConfig {
        applicationId "com.jarvis.mobile"
        versionCode 1
        versionName "1.0"
    }
    buildTypes {
        release {
            minifyEnabled false
        }
    }
}

apply from: 'capacitor.build.gradle'
`;

test("secret denetimi: eksikler adıyla ve README yönlendirmesiyle", async () => {
  const ci = await load("ci-android.mjs");
  const r = ci.checkSigningEnv({ ANDROID_KEYSTORE_PASSWORD: "x", ANDROID_KEY_ALIAS: " " });
  assert.equal(r.ok, false);
  assert.deepEqual(r.missing, ["ANDROID_KEYSTORE_BASE64", "ANDROID_KEY_ALIAS"]);
  assert.match(r.message, /ANDROID_KEYSTORE_BASE64, ANDROID_KEY_ALIAS/);
  assert.match(r.message, /README\.md/);
  assert.match(r.message, /Settings → Secrets and variables → Actions/);
  assert.equal(ci.checkSigningEnv({ ANDROID_KEYSTORE_BASE64: "a", ANDROID_KEYSTORE_PASSWORD: "b", ANDROID_KEY_ALIAS: "c" }).ok, true);
  assert.deepEqual(ci.OPTIONAL, ["ANDROID_KEY_PASSWORD"]);
});

test("keystore çözme: JKS / PKCS12 kabul, bozuk metin anlaşılır hata", async () => {
  const ci = await load("ci-android.mjs");
  const jks = Buffer.alloc(100); jks.writeUInt32BE(0xfeedfeed, 0);
  assert.equal(ci.decodeKeystore(jks.toString("base64")).length, 100);
  const b64 = Buffer.concat([Buffer.from([0x30, 0x82]), Buffer.alloc(98)]).toString("base64");
  assert.equal(ci.decodeKeystore(b64.replace(/(.{20})/g, "$1\n")).length, 100);   // satır sonlu base64
  assert.throws(() => ci.decodeKeystore(""), /geçerli bir base64/);
  assert.throws(() => ci.decodeKeystore("bu bir şifre değil!"), /geçerli bir base64/);
  assert.throws(() => ci.decodeKeystore(Buffer.from("x".repeat(200)).toString("base64")), /keystore .* benzemiyor/);
});

test("versionCode her derlemede artar; versionName etiketten gelir", async () => {
  const ci = await load("ci-android.mjs");
  assert.equal(ci.versionCode("41", "0"), 41);
  assert.equal(ci.versionCode(42, undefined), 42);
  assert.equal(ci.versionCode("7", "100"), 107);
  assert.ok(ci.versionCode("43", "0") > ci.versionCode("42", "0"));
  assert.throws(() => ci.versionCode("", "0"), /GITHUB_RUN_NUMBER/);
  assert.throws(() => ci.versionCode("5", "-1"), /VERSION_CODE_OFFSET/);
  assert.throws(() => ci.versionCode("5", "2100000000"), /çok büyük/);
  assert.equal(ci.versionName("refs/tags/v2.5.0", "v2.5.0", "2.5.0", 9), "2.5.0");
  assert.equal(ci.versionName("refs/heads/main", "main", "2.5.0\n", 9), "2.5.0-dev.9");
  const js = ci.buildInfoJs({ versionCode: 9, versionName: "2.5.0", repo: "o/r" });
  const sandbox = {};
  new Function("self", js)(sandbox);
  assert.deepEqual(sandbox.JARVIS_BUILD, { versionCode: 9, versionName: "2.5.0", repo: "o/r" });
});

test("signing komutu: secret yoksa açık hata ve çıkış kodu 1", () => {
  const env = Object.assign({}, process.env);
  for (const k of ["ANDROID_KEYSTORE_BASE64", "ANDROID_KEYSTORE_PASSWORD", "ANDROID_KEY_ALIAS", "GITHUB_ENV"]) delete env[k];
  const r = spawnSync(process.execPath, [join(MOBILE, "scripts", "ci-android.mjs"), "signing"], { env, encoding: "utf8" });
  assert.equal(r.status, 1);
  assert.match(r.stdout, /::error title=İmza anahtarı eksik::/);
  assert.match(r.stdout, /ANDROID_KEYSTORE_BASE64, ANDROID_KEYSTORE_PASSWORD, ANDROID_KEY_ALIAS/);
});

let keytoolOk = false;
try { execFileSync("keytool", ["-help"], { stdio: "ignore" }); keytoolOk = true; } catch (_) {}

test("signing komutu: gerçek keystore doğrulanır, yanlış şifre/alias anlaşılır hata", { skip: !keytoolOk && "keytool yok" }, () => {
  const dir = mkdtempSync(join(tmpdir(), "jarvis-ks-"));
  try {
    const ks = join(dir, "test.jks");
    execFileSync("keytool", ["-genkeypair", "-keystore", ks, "-storetype", "PKCS12", "-alias", "jarvis", "-keyalg", "RSA",
      "-keysize", "2048", "-validity", "1", "-storepass", "testsifre123", "-dname", "CN=JARVIS Test"], { stdio: "ignore" });
    const b64 = readFileSync(ks).toString("base64");
    const run = (pass, alias) => {
      const ghenv = join(dir, "env.txt");
      const env = Object.assign({}, process.env, { ANDROID_KEYSTORE_BASE64: b64, ANDROID_KEYSTORE_PASSWORD: pass,
        ANDROID_KEY_ALIAS: alias, RUNNER_TEMP: dir, GITHUB_ENV: ghenv });
      const r = spawnSync(process.execPath, [join(MOBILE, "scripts", "ci-android.mjs"), "signing"], { env, encoding: "utf8" });
      return { r, ghenv: existsSync(ghenv) ? readFileSync(ghenv, "utf8") : "" };
    };
    const good = run("testsifre123", "jarvis");
    assert.equal(good.r.status, 0, good.r.stdout + good.r.stderr);
    assert.match(good.ghenv, /^JARVIS_KEYSTORE_FILE=.*jarvis-release\.jks$/m);
    assert.doesNotMatch(good.ghenv, /testsifre123/);          // şifre GITHUB_ENV'e yazılmaz
    const badPass = run("yanlis", "jarvis");
    assert.equal(badPass.r.status, 1);
    assert.match(badPass.r.stdout, /ANDROID_KEYSTORE_PASSWORD yanlış/);
    const badAlias = run("testsifre123", "baska");
    assert.equal(badAlias.r.status, 1);
    assert.match(badAlias.r.stdout, /ANDROID_KEY_ALIAS \("baska"\) keystore'da yok/);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test("patch-android: build.gradle'a imza + sürüm bloğu eklenir, tekrar çalıştırmak güvenli", async () => {
  const pa = await load("patch-android.mjs");
  const once = pa.patchGradle(GRADLE);
  assert.ok(once.startsWith(GRADLE.trimEnd()));
  for (const v of ["JARVIS_KEYSTORE_FILE", "JARVIS_KEYSTORE_PASSWORD", "JARVIS_KEY_ALIAS", "JARVIS_KEY_PASSWORD",
    "JARVIS_VERSION_CODE", "JARVIS_VERSION_NAME"]) assert.ok(once.includes(`"${v}"`), v);
  assert.match(once, /signingConfig\(signingConfigs\.jarvisRelease\)/);
  assert.match(once, /versionCode\(jarvisEnv\("JARVIS_VERSION_CODE"\)\.toInteger\(\)\)/);
  const twice = pa.patchGradle(once);
  assert.equal(twice, once);
  assert.equal(twice.split(pa.GRADLE_BEGIN).length, 2);
  // Groovy parantez dengesi
  const count = (s, c) => s.split(c).length - 1;
  assert.equal(count(pa.GRADLE_BLOCK, "{"), count(pa.GRADLE_BLOCK, "}"));
  assert.equal(count(pa.GRADLE_BLOCK, "("), count(pa.GRADLE_BLOCK, ")"));
  assert.throws(() => pa.patchGradle("plugins { }"), /android \{ \} bloğu/);
  assert.throws(() => pa.patchGradle(GRADLE + pa.GRADLE_BEGIN + "\n"), /bitiş işareti/);
  assert.doesNotMatch(once, /(storePassword|keyPassword)\s*\(?\s*"/);   // şifre dosyaya sabit yazılmaz
});

test("patch-android: manifest izinleri (bildirim dahil), tekrar eklenmez", async () => {
  const pa = await load("patch-android.mjs");
  const xml = '<manifest xmlns:android="http://schemas.android.com/apk/res/android">\n    <application android:label="JARVIS">\n    </application>\n</manifest>\n';
  const once = pa.patchManifest(xml);
  for (const p of pa.PERMISSIONS) assert.equal(once.split(`"${p}"`).length - 1, 1, p);
  assert.ok(once.includes("android.permission.POST_NOTIFICATIONS"));
  assert.ok(once.includes('android:usesCleartextTraffic="true"'));
  assert.equal(pa.patchManifest(once), once);
});

test("build.yml: imzalı release APK, secret'lar, versionCode", () => {
  const yml = readFileSync(join(ROOT, ".github", "workflows", "build.yml"), "utf8");
  assert.ok(!yml.includes("assembleDebug"), "debug APK derlenmemeli");
  assert.ok(!yml.includes("app-debug.apk"));
  assert.match(yml, /\.\/gradlew assembleRelease/);
  assert.match(yml, /apk\/release\/app-release\.apk/);
  for (const s of ["ANDROID_KEYSTORE_BASE64", "ANDROID_KEYSTORE_PASSWORD", "ANDROID_KEY_ALIAS", "ANDROID_KEY_PASSWORD"]) {
    assert.ok(yml.includes(`\${{ secrets.${s} }}`), s);
  }
  assert.match(yml, /node scripts\/ci-android\.mjs signing/);
  assert.match(yml, /node scripts\/ci-android\.mjs version/);
  assert.match(yml, /apksigner" verify/);
  assert.match(yml, /Android versionCode: \$\{\{ needs\.android\.outputs\.version_code \}\}/);
  // İmza denetimi Android SDK kurulumundan ve npm install'dan önce: secret yoksa hızlı ve anlaşılır hata.
  assert.ok(yml.indexOf("ci-android.mjs signing") < yml.indexOf("npm install"));
  assert.ok(!/\x01|\x00/.test(yml));
});

test("README: keystore ve Secrets adımları", () => {
  const md = readFileSync(join(ROOT, "README.md"), "utf8");
  assert.match(md, /## Android imzalama \(release APK\)/);
  for (const s of ["ANDROID_KEYSTORE_BASE64", "ANDROID_KEYSTORE_PASSWORD", "ANDROID_KEY_ALIAS", "keytool -genkeypair"]) {
    assert.ok(md.includes(s), s);
  }
});

test(".gitignore keystore dosyalarını dışarıda tutar", () => {
  const gi = readFileSync(join(ROOT, ".gitignore"), "utf8");
  for (const p of ["*.jks", "*.keystore", "*.p12"]) assert.ok(gi.includes(p), p);
});
