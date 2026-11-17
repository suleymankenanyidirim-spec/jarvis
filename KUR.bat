@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Depo duzeninde JARVIS desktop\ icinde, kurulu pakette (C:\JARVIS) bu klasorde.
set "APP=%~dp0"
if exist "%~dp0desktop\main.py" set "APP=%~dp0desktop\"
if not exist "%APP%config_local.py" copy "%APP%config_local.example.py" "%APP%config_local.py" >nul
echo JARVIS bagimliliklari kuruluyor...
py -m pip install -r "%APP%requirements.txt"
if %ERRORLEVEL% NEQ 0 (
  echo Python paket kurulumu basarisiz oldu.
  pause
  exit /b 1
)
echo Self-Heal icin istege bagli paketler (statik analiz, Claude)...
py -m pip install pyflakes anthropic
py -m playwright install chromium
if not exist selfheal_ayarlar.json copy selfheal_ayarlar.ornek.json selfheal_ayarlar.json >nul
echo Kurulum bitti.
pause
