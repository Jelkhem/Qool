@echo off
rem تشغيل قول بدون نافذة أوامر دائمة
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo شغّل setup.bat أولًا
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" -m qool --show
