@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem v2.6: Telefon icin yeni tek kullanimlik eslesme kodu uretir (JARVIS acik olmali).
set PORT=5000
for /f %%p in ('py -c "import config;print(getattr(config,'PHONE_SERVER_PORT',5000))" 2^>nul') do set PORT=%%p
curl -s -f -X POST "http://127.0.0.1:%PORT%/api/pair/new?format=text"
if errorlevel 1 (
  echo JARVIS calismiyor gibi. Once START_JARVIS.bat ile baslatin.
) else (
  echo Telefonda: AYAR - Eslesme kodu kutusuna yazin ya da data\phone_link.txt icindeki linki acin.
)
pause
