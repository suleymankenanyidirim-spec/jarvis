# JARVIS v2.5 — İMZALI RELEASE APK + GÜNCELLEME DENETİMİ + GÖREV BİLDİRİMİ

v2.4'teki hiçbir özellik kaldırılmadı. `data/` ve `config_local.py` dokunulmadan kalır. Masaüstü Python kodu
değişmedi. Telefon uygulaması: v1.8.0.

## Android derlemesi (`.github/workflows/build.yml`)
- **Sabit anahtarla imzalı release APK.** Eskiden `assembleDebug` ile her derlemede farklı bir debug anahtarı
  kullanılıyordu. İmza değiştiği için yeni APK eskisinin üzerine kurulmuyordu. Artık `assembleRelease` ile,
  GitHub Secrets'taki keystore ile imzalanıyor:
  - `ANDROID_KEYSTORE_BASE64`: keystore dosyasının base64 hali (zorunlu)
  - `ANDROID_KEYSTORE_PASSWORD`: keystore şifresi (zorunlu)
  - `ANDROID_KEY_ALIAS`: anahtar adı (zorunlu)
  - `ANDROID_KEY_PASSWORD`: anahtar şifresi (isteğe bağlı; yoksa keystore şifresi kullanılır)
- **Secret yoksa anlaşılır hata.** İmza adımı Android SDK kurulumundan ve `npm install`'dan önce çalışır.
  Durduğu durumlar ve verdiği mesajlar:
  - Eksik secret: `İmzalı APK için GitHub Secrets eksik: <adlar>` ve README'deki bölüme yönlendirme.
  - Bozuk base64 ya da keystore olmayan dosya: ayrı, açık bir mesaj.
  - Yanlış şifre ya da alias: `keytool` ile denetlenir, ayrı mesajla durur.
  - Şifreler hiçbir dosyaya ya da `GITHUB_ENV`'e yazılmaz.
- **versionCode her derlemede artar.** `run_number + VERSION_CODE_OFFSET` (iş akışında, varsayılan 0).
  `versionName` etiketten gelir (`v2.5.0` → `2.5.0`); etiketsiz derlemede `<VERSION>-dev.<numara>`.
- **Doğrulama:** `apksigner verify` sertifikanın SHA-256 parmak izini yazdırır. `aapt2` ile APK'nın versionCode'u
  denetlenir. İmzasız APK oluşursa derleme durur.
- **Release notu** `Android versionCode: N` satırını içerir (telefon güncelleme denetimi bunu kullanır).
- **Yeni betikler:**
  - `mobile/scripts/ci-android.mjs` (`signing` / `version`): imza denetimi, sürüm hesabı ve `www/build-info.js` üretimi.
  - `mobile/scripts/patch-android.mjs` saf fonksiyonlara ayrıldı. `app/build.gradle` sonuna ortam
    değişkenlerinden okuyan imza ve sürüm bloğu ekler; tekrar çalıştırmak güvenlidir.
  - Ortam değişkeni yoksa yerel `assembleDebug` eskisi gibi çalışır. Manifest'e `POST_NOTIFICATIONS` izni eklendi.
- `.gitignore`: `*.jks`, `*.keystore`, `*.p12`, `*.b64` depoya girmez.

## Telefon uygulaması (mobile/, v1.8.0)
- **Yeni APK denetimi.** APK açılışta (en fazla 15 dakikada bir) GitHub Releases'taki son sürüme bakar.
  - Release notundaki versionCode bu APK'nınkinden büyükse üstte bir şerit çıkar:
    "Yeni JARVIS sürümü hazır: v… (… MB)" ve **İNDİR** düğmesi.
  - Eski release'lerde versionCode yoksa etiketteki sürüm karşılaştırılır.
  - Şerit kapatılırsa o sürüm bir daha gösterilmez.
  - AYAR → **GÜNCELLEME** bölümü derleme bilgisini gösterir; **GÜNCELLEMELERİ DENETLE** düğmesi indirme linkini verir.
  - Yerel derlemelerde (deposu bilinmeyen) otomatik denetim yapılmaz.
- **Görev bitince bildirim.** PC'ye bağlıyken `/api/tasks` 20 saniyede bir (ve uygulama öne gelince) yoklanır.
  - Sürmekte olan bir görev **bitince** ya da **başarısız olunca** `@capacitor/local-notifications` ile bir kez
    yerel bildirim gelir: "JARVIS görevi bitti: <başlık> — <sonucun başı>" ya da "JARVIS görevi başarısız: … — <hata>".
  - Bildirime dokununca GÖREV ekranı açılır. Aynı not sohbete de düşer.
  - İlk yoklamada zaten bitmiş görevler bildirilmez. Uygulama kapalıyken biten görev, uygulama açılınca bildirilir.
  - AYAR → *Arka plan görevi bitince bildirim gönder* (varsayılan açık); açılınca bildirim izni istenir.
  - Not: uygulama tamamen kapalıyken yoklama yapılmaz (arka plan servisi yok); Android uygulamayı canlı
    tuttuğu sürece arka planda da çalışır.
- Yeni dosyalar: `www/build-info.js` (CI üzerine yazar), `www/updates.js`, `www/tasknotify.js` (saf mantık).
  `index.html` ve `sw.js` önbelleğine eklendi (service worker 1.8.0).
- Yeni bağımlılık: `@capacitor/local-notifications` ^8.3.1 (Capacitor 8).

## README
- Yeni bölüm **"Android imzalama (release APK)"**, adım adım:
  - `keytool` ile keystore üretme (Windows / Android Studio yolu) ve base64'e çevirme (PowerShell).
  - GitHub Secrets'a ekleme (arayüz ve `gh` CLI), derlemenin kontrolü ve hata tablosu.
  - Telefonda bir kerelik geçiş (yedek al → eski uygulamayı kaldır → yeni APK → yedek yükle) ve versionCode notu.

## Testler
- `mobile/tests/android-ci.test.js` (yeni):
  - Secret denetimi ve mesajları, base64/keystore çözme, versionCode/versionName.
  - `signing` komutunun secret yokken çıkış kodu 1 ile durması.
  - Gerçek bir keystore ile `keytool` doğrulaması; yanlış şifre ve alias (keytool yoksa atlanır).
  - `build.gradle` / manifest yamaları ve tekrar çalıştırma.
  - `build.yml` denetimi: debug APK yok, release imzası, secret'lar, imza adımının sırası.
  - README bölümü ve `.gitignore`.
- `mobile/tests/updates.test.js` (yeni):
  - Sürüm karşılaştırma, release notundan versionCode, güncelleme kararı (taslak, ön sürüm ve APK'sız release dahil).
  - Otomatik denetim aralığı, görev bildirimi farkı (ilk yoklama, tek bildirim, PC değişimi), bildirim metni ve kimliği.
- `desktop/tests/test_phone_server.py`: yeni betiklerin telefon arayüzünde ve önbellekte olması.

## Yükseltme
1. README → *Android imzalama (release APK)* adımlarıyla keystore'u üret ve 3 secret'ı ekle (bir kerelik).
2. Telefonda bir kerelik: AYAR → YEDEK AL → eski uygulamayı kaldır → yeni APK'yı kur → YEDEK YÜKLE.
   Sonraki tüm APK'lar eskisinin üzerine kurulur.
