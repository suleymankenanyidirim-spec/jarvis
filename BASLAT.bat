@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo JARVIS v2.1 - kendini gelistiren mod basliyor (durdurmak icin Ctrl+C)
py run.py --uygulama
pause
