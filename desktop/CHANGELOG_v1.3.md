JARVIS v1.3 — OTONOM GÖREV + GERÇEK TARAYICI + SELF-RECOVERY

- TaskPlanner: doğal dildeki uzun hedefi en fazla 7 adıma böler.
- start_planned_task: planı arka planda çalıştırır ve konuşmayı bloklamaz.
- BrowserAgent: Playwright ile gerçek Chrome/Chromium sayfalarını açar, okur ve tıklar.
- Plan adımlarında web_search, browser_visit, browser_click, council, reason ve system_info.
- Kalıcı görev planı/ilerleme/adım sonuçları saklanır.
- Her adım 3 kez denenir; başarısızsa AI Konseyi düzeltme talimatı ile son self-recovery denenir.
- Tüm görevde de BackgroundTaskManager retry mekanizması bulunur.
- INSTALL.bat Playwright Chromium kurar.

GÜVENLİK
- Browser otomasyonu araştırma/okuma/tıklama ile sınırlıdır; parola, ödeme, mesaj gönderme veya geri dönüşü zor değişiklikler planlayıcı tarafından üretilmez.
- Kendi kaynak kodunu doğrudan çalışırken değiştirme açılmadı; self-update doğrulamalı stage sistemidir.

- AUTO_PLANNER: güçlü çok-adımlı ifadelerde JARVIS otomatik olarak planlı arka plan görevine geçer; arka plan sohbeti recursive plan oluşturmaz.
- Browser planlarında FIRST_RESULT_URL ile arama sonucundan sonraki sayfaya dinamik geçiş desteklenir.


JARVIS v1.3.1 — CLIPBOARD / KOPYALA-YAPIŞTIR
----------------------------------------------
- clipboard_copy: metni Windows panosuna yazar.
- clipboard_read: mevcut panoyu okur.
- copy_selected_text: aktif pencerede Ctrl+C yapıp seçili metni okur; mümkünse önceki panoyu geri yükler.
- paste_clipboard: aktif pencereye Ctrl+V yapar.
- paste_text: metni panoya alıp aktif pencereye Ctrl+V yapar.
- paste işlemlerinde Enter opsiyonu vardır.
- pyperclip ve pyautogui requirements'a eklendi.
- JARVIS durum kontrolüne clipboard_tools eklendi.
- Güvenlik: kopyala/yapıştır araçları kullanıcı açıkça istediğinde kullanılmak üzere tanımlandı.

- get_jarvis_status clipboard_tools artık pyperclip ve pyautogui gerçekten import edilebiliyor mu kontrol eder.
