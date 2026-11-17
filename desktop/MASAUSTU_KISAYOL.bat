@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
rem JARVIS v2.8: masaüstüne "JARVIS" (START_JARVIS.bat) ve "JARVIS Bilgi Deposu" kısayolları koyar.
set "JARVIS_DIR=%~dp0"
set "JARVIS_KB=D:\JARVIS_Bilgi"
for /f "delims=" %%P in ('py -c "import config,os;print(os.path.normpath(getattr(config,'KNOWLEDGE_DIR','D:/JARVIS_Bilgi')))" 2^>nul') do set "JARVIS_KB=%%P"

powershell -NoProfile -Command ^
  "$desk = [Environment]::GetFolderPath('Desktop'); $w = New-Object -ComObject WScript.Shell;" ^
  "$s = $w.CreateShortcut((Join-Path $desk 'JARVIS.lnk'));" ^
  "$s.TargetPath = (Join-Path $env:JARVIS_DIR 'START_JARVIS.bat'); $s.WorkingDirectory = $env:JARVIS_DIR.TrimEnd('\');" ^
  "$ico = Join-Path $env:JARVIS_DIR 'jarvis.ico'; if (Test-Path $ico) { $s.IconLocation = $ico };" ^
  "$s.Description = 'JARVIS'; $s.Save();" ^
  "New-Item -ItemType Directory -Force $env:JARVIS_KB | Out-Null;" ^
  "$k = $w.CreateShortcut((Join-Path $desk 'JARVIS Bilgi Deposu.lnk')); $k.TargetPath = $env:JARVIS_KB;" ^
  "$k.Description = 'JARVIS bilgi deposu: buraya atilan dosyalari JARVIS once okur'; $k.Save();" ^
  "Write-Host ('Kisayollar: ' + $desk)"
if errorlevel 1 (
  echo [HATA] Kisayol olusturulamadi.
  pause
  exit /b 1
)
echo Tamam. Masaustunde "JARVIS" ve "JARVIS Bilgi Deposu" kisayollari var.
pause
