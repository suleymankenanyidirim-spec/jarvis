@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title JARVIS - Otomatik başlatma kurulumu

rem JARVIS v2.2: Windows açılışında JARVIS'i HUD'sız (--headless) başlatır.
rem Başlangıç klasörüne "JARVIS (arka plan)" kısayolu koyar; yönetici izni gerekmez.
rem Kaldırmak için: OTOMATIK_BASLAT_KALDIR.bat

set "JARVIS_DIR=%~dp0"
set "JARVIS_PY="
for /f "delims=" %%P in ('where pyw 2^>nul') do if not defined JARVIS_PY set "JARVIS_PY=%%P"
set "JARVIS_STYLE=1"
if not defined JARVIS_PY (
  for /f "delims=" %%P in ('where py 2^>nul') do if not defined JARVIS_PY set "JARVIS_PY=%%P"
  set "JARVIS_STYLE=7"
)
if not defined JARVIS_PY (
  echo [HATA] Python bulunamadı. Önce INSTALL.bat çalıştır.
  pause
  exit /b 1
)

powershell -NoProfile -Command ^
  "$dir = if ($env:JARVIS_STARTUP_DIR) { $env:JARVIS_STARTUP_DIR } else { [Environment]::GetFolderPath('Startup') };" ^
  "$lnk = Join-Path $dir 'JARVIS (arka plan).lnk';" ^
  "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk);" ^
  "$s.TargetPath = $env:JARVIS_PY;" ^
  "$q = [char]34; $s.Arguments = $q + (Join-Path $env:JARVIS_DIR 'main.py') + $q + ' --headless';" ^
  "$s.WorkingDirectory = $env:JARVIS_DIR.TrimEnd('\');" ^
  "$s.WindowStyle = [int]$env:JARVIS_STYLE;" ^
  "$s.Description = 'JARVIS - HUD olmadan beyin + telefon sunucusu';" ^
  "$s.Save(); Write-Host ('Kısayol: ' + $lnk)"
if errorlevel 1 (
  echo [HATA] Kısayol oluşturulamadı.
  pause
  exit /b 1
)

echo.
echo Tamam. Windows her açıldığında JARVIS arka planda (HUD'sız) başlayacak.
echo Telefon linkleri: data\phone_link.txt   Log: data\jarvis_headless.log
echo Not: Arka plan JARVIS'i çalışırken START_JARVIS.bat ile HUD'u açarsan
echo telefon sunucusu HUD'da başlamaz; telefon arka plandakine bağlanır.
echo.
choice /c EH /n /m "Şimdi de arka planda başlatılsın mı? (E/H) "
if errorlevel 2 goto :son
start "" "%JARVIS_PY%" "%JARVIS_DIR%main.py" --headless
echo Başlatıldı. Birkaç saniye sonra data\phone_link.txt dosyasına bak.
:son
pause
