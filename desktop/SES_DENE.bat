@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo JARVIS ses denemesi - Gemini erkek sesleri sirayla okunacak.
echo.
py -m voice.ses_dene %*
echo.
pause
