@echo off
cd /d "%~dp0"
if not exist config_local.py copy config_local.example.py config_local.py >nul
echo JARVIS bagimliliklari kuruluyor...
py -m pip install -r requirements.txt
if %ERRORLEVEL% NEQ 0 (
  echo Python paket kurulumu basarisiz oldu.
  pause
  exit /b 1
)
echo Playwright Chromium tarayicisi kuruluyor...
py -m playwright install chromium
if %ERRORLEVEL% NEQ 0 echo Playwright tarayici kurulumu basarisiz. Chrome kuruluysa yine BROWSER_CHANNEL=chrome kullanılabilir.
pause
