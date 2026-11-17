@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Oneri modu: duzeltmeler test edilir ama koda uygulanmaz (selfheal_data\oneriler)
py run.py --uygulama --mod oneri
pause
