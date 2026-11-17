/* Derleme bilgisi. GitHub Actions APK derlerken bu dosyanın yerine gerçek değerleri yazar
 * (scripts/ci-android.mjs version). versionCode 0 = yerel/geliştirme derlemesi: güncelleme denetimi
 * yalnızca elle yapılır ve GitHub deposu bilinmiyorsa atlanır. */
self.JARVIS_BUILD = { versionCode: 0, versionName: "dev", repo: "", tag: "", builtAt: "" };
