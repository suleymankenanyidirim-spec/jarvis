// Android projesini JARVIS için ayarlar (npx cap add android sonrası; tekrar çalıştırmak güvenlidir):
// - Mikrofon izni (ses tanıma), bildirim izni (v2.5, görev bitti bildirimi), ses tanıma / TTS servis sorguları
// - Evdeki PC'ye http (cleartext) bağlantı izni
// - v2.5: app/build.gradle sonuna ortam değişkenlerinden okuyan imza + sürüm bloğu.
//   Değişkenler yoksa (yerel derleme) blok hiçbir şey yapmaz; debug derlemesi eskisi gibi çalışır.
//   Şifreler dosyaya yazılmaz, Gradle onları derleme anında ortamdan okur.
import { readFileSync, writeFileSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";

export const MANIFEST_PATH = "android/app/src/main/AndroidManifest.xml";
export const GRADLE_PATH = "android/app/build.gradle";

export const PERMISSIONS = [
  "android.permission.INTERNET",
  "android.permission.RECORD_AUDIO",
  "android.permission.ACCESS_NETWORK_STATE",
  "android.permission.POST_NOTIFICATIONS",
];

export const GRADLE_BEGIN = "// >>> JARVIS imza + sürüm (scripts/patch-android.mjs) >>>";
export const GRADLE_END = "// <<< JARVIS imza + sürüm <<<";

export const GRADLE_BLOCK = `${GRADLE_BEGIN}
// GitHub Actions'ta JARVIS_KEYSTORE_FILE / JARVIS_KEYSTORE_PASSWORD / JARVIS_KEY_ALIAS / JARVIS_KEY_PASSWORD
// ve JARVIS_VERSION_CODE / JARVIS_VERSION_NAME ortam değişkenleri doludur. Yerelde boşsa bu blok etkisizdir.
def jarvisEnv = { String name -> (System.getenv(name) ?: "").trim() }
if (jarvisEnv("JARVIS_KEYSTORE_FILE")) {
    android {
        signingConfigs {
            jarvisRelease {
                storeFile = file(jarvisEnv("JARVIS_KEYSTORE_FILE"))
                storePassword = jarvisEnv("JARVIS_KEYSTORE_PASSWORD")
                keyAlias = jarvisEnv("JARVIS_KEY_ALIAS")
                keyPassword = jarvisEnv("JARVIS_KEY_PASSWORD") ?: jarvisEnv("JARVIS_KEYSTORE_PASSWORD")
            }
        }
        buildTypes {
            release {
                signingConfig(signingConfigs.jarvisRelease)
            }
        }
    }
}
if (jarvisEnv("JARVIS_VERSION_CODE")) {
    android {
        defaultConfig {
            versionCode(jarvisEnv("JARVIS_VERSION_CODE").toInteger())
            versionName(jarvisEnv("JARVIS_VERSION_NAME") ?: "dev")
        }
    }
}
${GRADLE_END}`;

/** build.gradle sonuna JARVIS bloğunu ekler; blok zaten varsa güncel haliyle değiştirir. */
export function patchGradle(text) {
  const src = String(text);
  const begin = src.indexOf(GRADLE_BEGIN);
  if (begin >= 0) {
    const end = src.indexOf(GRADLE_END, begin);
    if (end < 0) throw new Error("build.gradle'da JARVIS bloğunun bitiş işareti yok; bloğu elle silip tekrar çalıştırın.");
    return src.slice(0, begin) + GRADLE_BLOCK + src.slice(end + GRADLE_END.length);
  }
  if (!/(^|\n)\s*android\s*\{/.test(src)) {
    throw new Error("build.gradle'da android { } bloğu bulunamadı (Capacitor projesi mi?).");
  }
  return src.trimEnd() + "\n\n" + GRADLE_BLOCK + "\n";
}

/** AndroidManifest.xml: izinler, servis sorguları ve cleartext; her şey bir kez eklenir. */
export function patchManifest(text) {
  let xml = String(text);
  for (const p of PERMISSIONS) {
    if (!xml.includes(`"${p}"`)) xml = xml.replace("</manifest>", `    <uses-permission android:name="${p}" />\n</manifest>`);
  }
  if (!xml.includes("android.speech.RecognitionService")) {
    xml = xml.replace("</manifest>", `    <queries>
        <intent><action android:name="android.speech.RecognitionService" /></intent>
        <intent><action android:name="android.intent.action.TTS_SERVICE" /></intent>
    </queries>
</manifest>`);
  }
  if (!xml.includes("usesCleartextTraffic")) xml = xml.replace("<application", '<application\n        android:usesCleartextTraffic="true"');
  return xml;
}

function main() {
  if (!existsSync(MANIFEST_PATH)) { console.error("Önce: npx cap add android"); process.exit(1); }
  writeFileSync(MANIFEST_PATH, patchManifest(readFileSync(MANIFEST_PATH, "utf8")));
  console.log("AndroidManifest.xml güncellendi.");
  if (existsSync(GRADLE_PATH)) {
    writeFileSync(GRADLE_PATH, patchGradle(readFileSync(GRADLE_PATH, "utf8")));
    console.log("app/build.gradle: imza + sürüm bloğu hazır.");
  } else {
    console.warn("app/build.gradle bulunamadı; imza bloğu eklenmedi.");
  }
}

const isMain = process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1];
if (isMain) {
  try { main(); } catch (e) { console.error(e.message); process.exit(1); }
}
