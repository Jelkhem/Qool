@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONPATH=%~dp0"set "QOOL_DATA_DIR=%TEMP%qool-tests"
.venv\Scripts\python.exe -m pytest -v tests
pause
