// JARVIS Android CI yardımcısı (v2.5) — GitHub Actions'ta çalışır.
//
//   node scripts/ci-android.mjs signing   -> GitHub Secrets'tan gelen keystore'u denetler, dosyaya yazar,
//                                            keytool ile şifre/alias'ı doğrular, JARVIS_KEYSTORE_FILE'ı ayarlar.
//   node scripts/ci-android.mjs version   -> versionCode / versionName hesaplar, www/build-info.js yazar.
//
// Şifreler hiçbir dosyaya ya da GITHUB_ENV'e yazılmaz; Gradle adımı onları doğrudan secrets'tan alır.
import { appendFileSync, readFileSync, writeFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

export const README_HINT = "Kurulum: README.md → \"Android imzalama (release APK)\" bölümü.";
export const REQUIRED = ["ANDROID_KEYSTORE_BASE64", "ANDROID_KEYSTORE_PASSWORD", "ANDROID_KEY_ALIAS"];
export const OPTIONAL = ["ANDROID_KEY_PASSWORD"];
const MAX_VERSION_CODE = 2100000000;   // Google Play / Android üst sınırı

const filled = (v) => typeof v === "string" && v.trim() !== "";

/** Eksik secret'ları bulur. Dönüş: {ok, missing: [...], message} */
export function checkSigningEnv(env) {
  const missing = REQUIRED.filter((n) => !filled(env[n]));
  if (!missing.length) return { ok: true, missing, message: "" };
  const message =
    `İmzalı APK için GitHub Secrets eksik: ${missing.join(", ")}. ` +
    "Repo → Settings → Secrets and variables → Actions → New repository secret ile ekleyin. " + README_HINT;
  return { ok: false, missing, message };
}

/** base64 keystore'u çözer; JKS (FEEDFEED) ya da PKCS12 (ASN.1 SEQUENCE 0x30) olduğunu kaba olarak denetler. */
export function decodeKeystore(b64) {
  const clean = String(b64 || "").replace(/\s+/g, "");
  if (!clean || !/^[A-Za-z0-9+/]+={0,2}$/.test(clean)) {
    throw new Error("ANDROID_KEYSTORE_BASE64 geçerli bir base64 metni değil. " + README_HINT);
  }
  const buf = Buffer.from(clean, "base64");
  const jks = buf.length > 4 && buf.readUInt32BE(0) === 0xfeedfeed;
  const p12 = buf.length > 4 && buf[0] === 0x30;
  if (buf.length < 64 || !(jks || p12)) {
    throw new Error("ANDROID_KEYSTORE_BASE64 bir keystore (.jks / .p12) dosyasına benzemiyor. " +
      "Dosyanın kendisini (metin değil) base64'e çevirdiğinizden emin olun. " + README_HINT);
  }
  return buf;
}

/** Her derlemede artan versionCode: GitHub run_number + sabit kaydırma. */
export function versionCode(runNumber, offset) {
  const run = Number.parseInt(runNumber, 10);
  const off = offset === undefined || offset === "" ? 0 : Number.parseInt(offset, 10);
  if (!Number.isInteger(run) || run < 1) throw new Error(`GITHUB_RUN_NUMBER geçersiz: ${runNumber}`);
  if (!Number.isInteger(off) || off < 0) throw new Error(`VERSION_CODE_OFFSET geçersiz: ${offset}`);
  const code = run + off;
  if (code > MAX_VERSION_CODE) throw new Error(`versionCode çok büyük: ${code}`);
  return code;
}

/** Etiketli derleme: "v2.5.0" -> "2.5.0". Diğerleri: "<VERSION>-dev.<run>". */
export function versionName(ref, refName, desktopVersion, runNumber) {
  if (String(ref || "").startsWith("refs/tags/v")) return String(refName || "").replace(/^v/, "");
  return `${String(desktopVersion || "0.0.0").trim()}-dev.${runNumber}`;
}

export function buildInfoJs(info) {
  return "/* CI tarafından üretildi (scripts/ci-android.mjs). Elle düzenlemeyin. */\n" +
    `self.JARVIS_BUILD = ${JSON.stringify(info)};\n`;
}

function ghAppend(file, lines) {
  if (file) appendFileSync(file, lines.map((l) => l + "\n").join(""));
}

function fail(title, message) {
  console.log(`::error title=${title}::${message}`);
  console.error(message);
  process.exit(1);
}

function cmdSigning(env) {
  const check = checkSigningEnv(env);
  if (!check.ok) fail("İmza anahtarı eksik", check.message);
  let buf;
  try { buf = decodeKeystore(env.ANDROID_KEYSTORE_BASE64); } catch (e) { fail("Keystore okunamadı", e.message); }
  const file = join(env.RUNNER_TEMP || process.cwd(), "jarvis-release.jks");
  writeFileSync(file, buf, { mode: 0o600 });
  const alias = env.ANDROID_KEY_ALIAS.trim();
  try {
    execFileSync("keytool", ["-list", "-keystore", file, "-storepass:env", "ANDROID_KEYSTORE_PASSWORD", "-alias", alias],
      { stdio: "pipe", env });
  } catch (e) {
    const out = String((e.stdout || "") + (e.stderr || ""));
    const why = /password|parola|şifre|tampered|incorrect/i.test(out) ? "ANDROID_KEYSTORE_PASSWORD yanlış"
      : /alias|does not exist|bulunamad/i.test(out) ? `ANDROID_KEY_ALIAS ("${alias}") keystore'da yok`
      : "keytool keystore'u açamadı";
    fail("Keystore doğrulanamadı", `${why}. ${README_HINT}`);
  }
  ghAppend(env.GITHUB_ENV, [`JARVIS_KEYSTORE_FILE=${file}`]);
  console.log(`Keystore hazır (${buf.length} bayt), alias "${alias}" doğrulandı.`);
}

function cmdVersion(env, mobileDir) {
  const desktopVersion = readFileSync(join(mobileDir, "..", "desktop", "VERSION"), "utf8").trim();
  let code;
  try { code = versionCode(env.GITHUB_RUN_NUMBER, env.VERSION_CODE_OFFSET); } catch (e) { fail("Sürüm numarası", e.message); }
  const name = versionName(env.GITHUB_REF, env.GITHUB_REF_NAME, desktopVersion, env.GITHUB_RUN_NUMBER);
  const isTag = String(env.GITHUB_REF || "").startsWith("refs/tags/v");
  const info = { versionCode: code, versionName: name, repo: env.GITHUB_REPOSITORY || "",
    tag: isTag ? env.GITHUB_REF_NAME : "", builtAt: new Date().toISOString() };
  writeFileSync(join(mobileDir, "www", "build-info.js"), buildInfoJs(info));
  ghAppend(env.GITHUB_ENV, [`JARVIS_VERSION_CODE=${code}`, `JARVIS_VERSION_NAME=${name}`]);
  ghAppend(env.GITHUB_OUTPUT, [`code=${code}`, `name=${name}`]);
  console.log(`versionCode=${code} versionName=${name}`);
}

const isMain = process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1];
if (isMain) {
  const mobileDir = join(dirname(fileURLToPath(import.meta.url)), "..");
  const cmd = process.argv[2];
  if (cmd === "signing") cmdSigning(process.env);
  else if (cmd === "version") cmdVersion(process.env, mobileDir);
  else { console.error("Kullanım: node scripts/ci-android.mjs signing|version"); process.exit(2); }
}
