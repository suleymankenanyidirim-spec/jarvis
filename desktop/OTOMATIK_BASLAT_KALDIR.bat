@echo off
chcp 65001 >nul
rem JARVIS v2.2: KUR_OTOMATIK_BASLAT.bat'ın koyduğu açılış kısayolunu kaldırır.
powershell -NoProfile -Command ^
  "$dir = if ($env:JARVIS_STARTUP_DIR) { $env:JARVIS_STARTUP_DIR } else { [Environment]::GetFolderPath('Startup') };" ^
  "$lnk = Join-Path $dir 'JARVIS (arka plan).lnk';" ^
  "if (Test-Path $lnk) { Remove-Item $lnk; Write-Host 'Otomatik başlatma kaldırıldı.' } else { Write-Host 'Otomatik başlatma zaten kurulu değil.' }"
echo Çalışan arka plan JARVIS'ini kapatmak için Görev Yöneticisi'nde pythonw.exe / py.exe işlemini sonlandır.
pause
