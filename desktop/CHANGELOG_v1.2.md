# JARVIS v1.2.1

## Düzeltmeler
- v0.6'daki orijinal JARVIS reaktör animasyonu geri getirildi.
- CPU/RAM/sıcaklık isteğinde açılan LIVE TELEMETRY paneli korundu ve eksik `draw_metric_overlay()` fonksiyonu eklendi.
- CPU sıcaklığı HUD tarafında artık CPU sıcaklık sensöründen okunuyor; NVIDIA GPU sıcaklığı CPU sıcaklığı gibi gösterilmiyor.
- Ses profilinin açılışta yüklenmesindeki değişken adı hatası düzeltildi.
- HUD sürüm etiketi `v1.2` olarak düzeltildi.
- `get_jarvis_status` gerçek bağlantı/konfigürasyon durumlarını raporlayacak şekilde düzeltildi.
- Suno entegrasyonu olmadığı halde varmış gibi raporlanmıyor (`suno: false`).

## Korunan özellikler
- En fazla 3 eşzamanlı arka plan görevi.
- Kalıcı arka plan görev geçmişi.
- AI Council: Ollama / OpenAI / Anthropic / Gemini adapterları.
- Güvenli stage + SHA-256 doğrulamalı self-update altyapısı.
- V1/V2/V3 ses profilleri.
- Web araması, memory, telefon sunucusu, kask ve mevcut HUD.
