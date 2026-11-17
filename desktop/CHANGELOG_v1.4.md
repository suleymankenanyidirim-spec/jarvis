# JARVIS v1.4 — TELEFON UYGULAMASI + GITHUB

## Yeni
- **Telefon uygulaması** (mobile/ klasörü): Android APK + her telefonda çalışan web uygulaması (PWA).
  - *PC modu*: bilgisayardaki JARVIS'e bağlanır (aynı beyin, hafıza, araçlar, kask, ses).
  - *Bulut modu*: PC kapalıyken Gemini / OpenAI / Claude ile çalışır.
- **Telefon API'si** (`/api/status`, `/api/ask`, `/api/tts`, `/api/tasks`, `/api/system`, `/api/helmet/...`).
- **Erişim anahtarı (token)**: aynı WiFi'deki başka biri artık JARVIS'e komut veremez.
  Anahtar açılışta konsola yazılır ve `data/phone_token.txt` içine kaydedilir. QR kod da basılır (`qrcode` paketi).
- **GitHub güncelleme**: `config_local.py` içine `GITHUB_REPO = "kullanici/jarvis"` yaz;
  JARVIS GitHub Releases'taki son sürümü SHA-256 doğrulayarak indirir, bir sonraki açılışta uygular.
  Sadece daha yeni sürüm varsa indirir.
- **config_local.py**: kişisel ayarlar artık ayrı dosyada; güncellemeler `config.py`'yi
  değiştirse bile ayarların kaybolmaz. `data/` ve `config_local.py` güncellemede asla ezilmez.

## Düzeltmeler
- `get_system_info` CPU sıcaklığı: `psutil` import edilmediği için sensör okuması hep
  başarısız oluyordu (sadece WMI deneniyordu). Düzeltildi.
- HUD'daki sürüm etiketi "v1.2" sabit yazıyordu; artık VERSION dosyasından okunuyor.
- AI Konseyi varsayılan modelleri güncellendi (Gemini: `gemini-3.6-flash`, Claude: `claude-sonnet-5-5`).
  Ortam değişkeni ile değiştirilebilir: `GEMINI_MODEL`, `ANTHROPIC_MODEL`, `OPENAI_MODEL`.
- Telefon sunucusu çok iş parçacıklı çalışır; uzun bir cevap diğer istekleri bekletmez.
