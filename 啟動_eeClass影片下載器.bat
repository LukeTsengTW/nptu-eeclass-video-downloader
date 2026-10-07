@echo off
chcp 65001 >nul
cd /d "%~dp0"
start "" "%SystemRoot%\System32\wscript.exe" "%~dp0開啟_eeClass影片下載器.vbs"
exit /b
