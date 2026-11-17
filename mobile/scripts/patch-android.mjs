// Android projesini JARVIS için ayarlar:
// - Mikrofon izni (ses tanıma) ve ses tanıma / TTS servis sorguları
// - Evdeki PC'ye http (cleartext) bağlantı izni
import { readFileSync, writeFileSync, existsSync } from "node:fs";

const path = "android/app/src/main/AndroidManifest.xml";
if (!existsSync(path)) { console.error("Önce: npx cap add android"); process.exit(1); }
let xml = readFileSync(path, "utf8");

const perms = [
  "android.permission.INTERNET",
  "android.permission.RECORD_AUDIO",
  "android.permission.ACCESS_NETWORK_STATE",
];
for (const p of perms) {
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
writeFileSync(path, xml);
console.log("AndroidManifest.xml güncellendi.");
